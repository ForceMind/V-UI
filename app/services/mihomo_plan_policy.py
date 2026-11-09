"""Protected, scoped route recipe for a previously validated static YAML plan."""
from __future__ import annotations

from copy import deepcopy
import json

from app.services.mihomo_merge import MihomoMergeError
from app.services.mihomo_merge_plan import render_sing_box_rules
from app.services.proxy_policy import AddressPolicy


DENIED_SET_TAG = 'vui-yaml-denied'
MAX_RECIPE_BYTES = 16 * 1024 * 1024  # Existing CoreRuntime config-byte limit.


def protected_yaml_route(plan, *, inbounds, outbound_tags, dns_tag, self_addresses, upstream_addresses,
                         policy=None, dns_upstream=None):
    """Resolve only a chosen domain rule, then check every resulting address.

    The caller supplies independently verified endpoint and local-address
    snapshots. This function performs no resolution, persistence or activation.
    The inline denied set keeps large address snapshots from being copied into
    every user rule.
    """
    if (type(inbounds) is not tuple or any(type(tag) is not str or not tag for tag in inbounds)
            or len(inbounds) != len(set(inbounds))):
        raise MihomoMergeError('PLAN_INVALID_RUNTIME_TAGS')
    if not inbounds:
        return {'rules': [], 'rule_set': []}
    if type(dns_tag) is not str or not dns_tag or not self_addresses:
        raise MihomoMergeError('PLAN_MISSING_ADDRESS_CONTEXT')
    policy = AddressPolicy() if policy is None else policy
    if type(policy) is not AddressPolicy:
        raise MihomoMergeError('PLAN_INVALID_ADDRESS_POLICY')
    names = {profile.name for profile in plan.upstreams}
    referenced = {target for _, _, target in plan.rules if target not in {'DIRECT', 'REJECT'}}
    if dns_upstream is not None:
        if type(dns_upstream) is not str or dns_upstream not in names:
            raise MihomoMergeError('PLAN_MISSING_ADDRESS_CONTEXT')
        referenced.add(dns_upstream)
    if (type(upstream_addresses) is not dict or upstream_addresses.keys() != referenced
            or any(type(values) not in (tuple, list) or not 1 <= len(values) <= 32
                   for values in upstream_addresses.values())):
        raise MihomoMergeError('PLAN_MISSING_ADDRESS_CONTEXT')
    pins = []
    for addresses in upstream_addresses.values():
        pins.extend(policy.check_addresses(addresses, self_addresses=self_addresses))
    denied = policy.denied_cidrs(self_addresses, tuple(pins))
    guards = [{'rule_set': [DENIED_SET_TAG], 'action': 'reject'}]
    if not policy._fixture_addresses:
        guards.append({'ip_is_private': True, 'action': 'reject'})
    user_rules = render_sing_box_rules(plan, inbound=inbounds[0], outbound_tags=outbound_tags)
    sets = [{'type': 'inline', 'tag': DENIED_SET_TAG, 'rules': [{'ip_cidr': denied}]}]

    def templates():
        yield {'network': 'udp', 'action': 'reject'}
        yield from guards
        for original, user_rule in zip(plan.rules, user_rules):
            row = {key: value for key, value in user_rule.items() if key != 'inbound'}
            row['network'] = 'tcp'
            if row['action'] == 'reject' or original[0] in ('IP-CIDR', 'IP-CIDR6'):
                yield row
                continue
            # Select before DNS so earlier no-resolve rules cannot acquire a
            # different meaning through addresses populated by protection.
            resolve = {key: value for key, value in row.items() if key not in ('action', 'outbound')}
            resolve.update(action='resolve', server=dns_tag, strategy='prefer_ipv4',
                           disable_cache=True, disable_optimistic_cache=True)
            yield resolve
            yield from guards
            yield row
        yield {'action': 'reject'}

    def encoded_size(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))

    scope = list(inbounds)
    scope_size = encoded_size(scope)
    size = encoded_size({'rules': [], 'rule_set': sets})
    rules = []
    for template in templates():
        size += encoded_size({**template, 'inbound': []}) + scope_size - 2 + bool(rules)
        if size > MAX_RECIPE_BYTES:
            raise MihomoMergeError('PLAN_RECIPE_LIMIT')
        rules.append({**deepcopy(template), 'inbound': list(inbounds)})
    return {'rules': rules, 'rule_set': sets}
