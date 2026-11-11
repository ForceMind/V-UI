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
    source_rules: tuple[tuple[str, str, str], ...] = field(repr=False)
    group_members: tuple[tuple[str, str], ...] = field(repr=False)

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


def _static_document(baseline, candidate, choices):
    merged = merge_mihomo(baseline, candidate, choices=choices)
    if set(merged) - {'mode', 'proxies', 'proxy-groups', 'rules'}:
        raise MihomoMergeError('PLAN_UNSUPPORTED_SECTION')
    if merged.get('mode') != 'rule':
        raise MihomoMergeError('PLAN_RULE_MODE_REQUIRED')
    try:
        profiles = tuple(_clash_profile(node) for node in merged.get('proxies', ()))
    except UpstreamValidationError:
        raise MihomoMergeError('PLAN_UNSUPPORTED_NODE') from None
    nodes = {profile.name for profile in profiles}
    available = nodes | _BUILTINS
    groups = {}
    for group in merged.get('proxy-groups', ()):
        if (set(group) != {'name', 'type', 'proxies'} or group['type'] != 'select'
                or not group['proxies'] or not set(group['proxies']) <= available):
            raise MihomoMergeError('PLAN_UNSUPPORTED_GROUP')
        groups[group['name']] = group['proxies']
    rules = []
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
        if parsed[2] not in available | groups.keys():
            raise MihomoMergeError('PLAN_INVALID_TARGET')
        rules.append(parsed)
    if not rules or rules[-1][0] != 'MATCH' or any(rule[0] == 'MATCH' for rule in rules[:-1]):
        raise MihomoMergeError('PLAN_FINAL_REQUIRED')
    return profiles, groups, tuple(rules)


def mihomo_plan_options(baseline, candidate, *, choices=None):
    """Private selector labels only; no credentials, hosts or implicit selections."""
    profiles, groups, rules = _static_document(baseline, candidate, choices)
    referenced = {target for _, _, target in rules if target in groups}
    return {'upstreams': [profile.name for profile in profiles],
            'groups': [{'name': name, 'members': list(members)}
                       for name, members in groups.items() if name in referenced]}


def plan_mihomo(baseline, candidate, *, choices=None, group_members=None) -> StaticMihomoPlan:
    """Resolve explicit selections and produce ordered rules without writes."""
    profiles, groups, source_rules = _static_document(baseline, candidate, choices)
    if group_members is None:
        group_members = {}
    if (type(group_members) is not dict or len(group_members) > len(groups)
            or any(type(name) is not str or name not in groups
                   or type(member) is not str or member not in groups[name]
                   for name, member in group_members.items())):
        raise MihomoMergeError('PLAN_INVALID_GROUP_SELECTION')
    rules, referenced_groups = [], set()
    for kind, operand, target in source_rules:
        if target in groups:
            referenced_groups.add(target)
            if target not in group_members:
                raise MihomoMergeError('PLAN_GROUP_SELECTION_REQUIRED')
            target = group_members[target]
        rules.append((kind, operand, target))
    if referenced_groups != group_members.keys():
        raise MihomoMergeError('PLAN_UNUSED_GROUP_SELECTION')
    return StaticMihomoPlan(profiles, tuple(rules), source_rules, tuple(group_members.items()))


def render_sing_box_rules(plan: StaticMihomoPlan, *, inbound: str, outbound_tags: dict[str, str]) -> list[dict]:
    """Render rules for one isolated inbound; the caller owns verified outbounds."""
    expected = {profile.name for profile in plan.upstreams} | {'DIRECT'}
    if (type(inbound) is not str or not inbound or type(outbound_tags) is not dict
            or outbound_tags.keys() != expected
            or any(type(tag) is not str or not tag for tag in outbound_tags.values())
            or len(set(outbound_tags.values())) != len(outbound_tags)):
        raise MihomoMergeError('PLAN_INVALID_RUNTIME_TAGS')
    fields = {'DOMAIN': 'domain', 'DOMAIN-SUFFIX': 'domain_suffix',
              'IP-CIDR': 'ip_cidr', 'IP-CIDR6': 'ip_cidr'}
    rendered = []
    for kind, operand, target in plan.rules:
        rule = {'inbound': [inbound]}
        if kind != 'MATCH':
            rule[fields[kind]] = [operand]
        if target == 'REJECT':
            rule['action'] = 'reject'
        else:
            rule.update(action='route', outbound=outbound_tags[target])
        rendered.append(rule)
    return rendered
