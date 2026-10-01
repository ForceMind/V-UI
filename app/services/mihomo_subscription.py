from __future__ import annotations

import json
from typing import Any, Iterable

from app.models.database import Inbound
from app.services.mihomo_routing import (
    build_policy_groups,
    build_rule_plan,
    default_routing,
    load_routing,
    normalize_routing,
    unique_proxy_names,
)
from app.services.subscription_service import _mihomo_proxy

try:
    import yaml
except ImportError:
    yaml = None


def mihomo_config(
    inbounds: Iterable[Inbound],
    host: str,
    routing: dict[str, Any] | None = None,
) -> str:
    proxies = []
    for item in inbounds:
        if not item.enable:
            continue
        proxy = _mihomo_proxy(item, host)
        if proxy:
            proxies.append(proxy)

    proxies = unique_proxy_names(proxies)

    if proxies:
        active_routing = normalize_routing(routing or load_routing())
    else:
        # Keep an empty installation importable instead of emitting groups that
        # reference missing nodes.
        empty_routing = default_routing()
        empty_routing["mode"] = "direct"
        empty_routing["presets"] = {
            preset_id: False
            for preset_id in empty_routing["presets"]
        }
        active_routing = normalize_routing(empty_routing)

    names = [proxy["name"] for proxy in proxies]
    groups = build_policy_groups(names, active_routing)
    plan = build_rule_plan(active_routing)

    config: dict[str, Any] = {
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "info",
    }
    if active_routing["mode"] == "direct":
        config["ipv6"] = False

    config["proxies"] = proxies
    config["proxy-groups"] = groups
    config["dns"] = plan["dns"]
    config["rules"] = [
        rule
        for section in plan["sections"]
        for rule in section["rules"]
    ]

    if yaml is not None:
        return yaml.safe_dump(
            config,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    return json.dumps(config, ensure_ascii=False, indent=2)
