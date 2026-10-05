from __future__ import annotations
import yaml
from app.services.mihomo_routing import build_policy_groups
from app.services.routing_store import load_routing
from app.services.routing_validation import build_rule_plan, normalize_routing
from app.services.validated_export import validated_nodes


def mihomo_config(inbounds, host: str, routing: dict | None = None) -> str:
    proxies = validated_nodes(inbounds, host)
    settings = normalize_routing(load_routing() if routing is None else routing)
    plan = build_rule_plan(settings)
    config = {'mixed-port':7890, 'allow-lan':False, 'mode':'rule', 'log-level':'info'}
    if settings['mode'] == 'direct':
        config['ipv6'] = False
    config.update({'proxies':proxies,
        'proxy-groups':build_policy_groups([n['name'] for n in proxies],settings),
        'dns':plan['dns'], 'rules':[r for section in plan['sections'] for r in section['rules']]})
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
