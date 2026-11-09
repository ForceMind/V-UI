"""Private static routing plan for the supported offline Mihomo merge subset."""
from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress

from app.services.mihomo_merge import MihomoMergeError, merge_mihomo
from app.services.mihomo_preview import _rule_shape
from app.services.routing_validation import parse_routing_target
from app.services.upstream_import import _clash_profile
from app.services.upstream_profile import UpstreamProfile, UpstreamValidationError


_BUILTINS = frozenset({'DIRECT', 'REJECT'})


@dataclass(frozen=True)
class StaticMihomoPlan:
    # This is private material, including upstream credentials and destinations.
    upstreams: tuple[UpstreamProfile, ...] = field(repr=False)
    rules: tuple[tuple[str, str, str], ...] = field(repr=False)

    def route(self, destination: str) -> str:
        """Return the first matching explicit target without resolving DNS."""
        if type(destination) is not str or len(destination) > 253 or '%' in destination:
            raise MihomoMergeError('PLAN_INVALID_DESTINATION')
        parsed = parse_routing_target(destination)
        if not parsed or parsed['value'] != destination:
            raise MihomoMergeError('PLAN_INVALID_DESTINATION')
        kind, value = parsed['kind'], parsed['value']
        address = ipaddress.ip_address(value) if kind in ('ipv4', 'ipv6') else None
        for rule, operand, target in self.rules:
            if rule == 'MATCH':
                return target
            if kind == 'domain':
                if rule == 'DOMAIN' and value == operand:
                    return target
                if rule == 'DOMAIN-SUFFIX' and (value == operand or value.endswith('.' + operand)):
                    return target
            elif address is not None and rule in ('IP-CIDR', 'IP-CIDR6'):
                if address in ipaddress.ip_network(operand):
                    return target
        raise MihomoMergeError('PLAN_FINAL_REQUIRED')


def plan_mihomo(baseline, candidate, *, choices=None, group_members=None) -> StaticMihomoPlan:
    """Resolve explicit selections and produce ordered rules without writes."""
    merged = merge_mihomo(baseline, candidate, choices=choices)
    if set(merged) - {'mode', 'proxies', 'proxy-groups', 'rules'}:
        raise MihomoMergeError('PLAN_UNSUPPORTED_SECTION')
    if merged.get('mode') != 'rule':
        raise MihomoMergeError('PLAN_RULE_MODE_REQUIRED')
    try:
        profiles = tuple(_clash_profile(node) for node in merged.get('proxies', ()))
    except UpstreamValidationError:
        raise MihomoMergeError('PLAN_UNSUPPORTED_NODE') from None
    nodes = {node['name'] for node in merged.get('proxies', ())}
    available = nodes | _BUILTINS
    groups = {}
    for group in merged.get('proxy-groups', ()):
        if (set(group) != {'name', 'type', 'proxies'} or group['type'] != 'select'
                or not group['proxies'] or not set(group['proxies']) <= available):
            raise MihomoMergeError('PLAN_UNSUPPORTED_GROUP')
        groups[group['name']] = group['proxies']
    if group_members is None:
        group_members = {}
    if (type(group_members) is not dict or len(group_members) > len(groups)
            or any(type(name) is not str or name not in groups
                   or type(member) is not str or member not in groups[name]
                   for name, member in group_members.items())):
        raise MihomoMergeError('PLAN_INVALID_GROUP_SELECTION')

    rules, referenced_groups = [], set()
    for rule in merged.get('rules', ()):
        if type(rule) is not str:
            raise MihomoMergeError('PLAN_UNSUPPORTED_RULE')
        if rule.startswith(('IP-CIDR,', 'IP-CIDR6,')):
            if not rule.endswith(',no-resolve'):
                raise MihomoMergeError('PLAN_UNSUPPORTED_RULE')
            rule = rule.removesuffix(',no-resolve')
        parsed = _rule_shape(rule)
        if parsed is None:
            raise MihomoMergeError('PLAN_UNSUPPORTED_RULE')
        kind, operand, target = parsed
        if target in groups:
            referenced_groups.add(target)
            if target not in group_members:
                raise MihomoMergeError('PLAN_GROUP_SELECTION_REQUIRED')
            target = group_members[target]
        if target not in available:
            raise MihomoMergeError('PLAN_INVALID_TARGET')
        rules.append((kind, operand, target))
    if referenced_groups != group_members.keys():
        raise MihomoMergeError('PLAN_UNUSED_GROUP_SELECTION')
    if not rules or rules[-1][0] != 'MATCH' or any(rule[0] == 'MATCH' for rule in rules[:-1]):
        raise MihomoMergeError('PLAN_FINAL_REQUIRED')
    return StaticMihomoPlan(profiles, tuple(rules))
