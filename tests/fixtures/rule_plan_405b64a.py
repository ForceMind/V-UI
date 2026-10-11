# Frozen planner from 405b64a34c0ca691f14afe39eddd60bb1885b300.
# Differential oracle only; unchanged helpers are supplied by the test namespace.
def build_rule_plan(routing: dict[str, Any] | None = None) -> dict[str, Any]:
    routing = normalize_routing(routing or default_routing())
    direct_mode = routing["mode"] == "direct"
    direct_resolvers = SYSTEM_DNS if direct_mode else DIRECT_DNS

    direct_targets = [
        parse_routing_target(value) for value in routing["direct_domains"]
    ]
    proxy_targets = [
        parse_routing_target(value) for value in routing["proxy_domains"]
    ]
    direct_targets = [target for target in direct_targets if target]
    proxy_targets = [target for target in proxy_targets if target]

    zones = routing["intranet"]
    intranet_matches = [
        {"type": "DOMAIN-SUFFIX", "value": zone["suffix"]}
        for zone in zones
    ]
    direct_matches = [_target_match(target) for target in direct_targets]

    warning_sets: dict[str, set[str]] = {}
    sections = [{
        "id": "local",
        "comment": "本机 / 局域网优先直连",
        "rules": [
            *[_rule(match, "DIRECT") for match in LOCAL_DOMAINS],
            *LOCAL_IP_RULES,
            *([CGNAT_RULE] if routing["bypass_cgnat"] else []),
        ],
    }]
    planned: list[dict[str, Any]] = []
    policy: dict[str, list[str]] = {}

    for local in LOCAL_DOMAINS:
        policy[_dns_key(local)] = list(SYSTEM_DNS if direct_mode else LOCAL_DNS)

    for zone in zones:
        policy[f"+.{zone['suffix']}"] = list(zone["nameservers"])

    for local in LOCAL_DOMAINS:
        matching_zone = next(
            (
                zone for zone in zones
                if _covers(
                    {"type": "DOMAIN-SUFFIX", "value": zone["suffix"]},
                    local,
                )
            ),
            None,
        )
        if matching_zone:
            policy[_dns_key(local)] = list(matching_zone["nameservers"])

    bootstrap_policy = {
        key: list(servers)
        for key, servers in policy.items()
    }

    def warn(code: str, match: dict[str, str]) -> None:
        warning_sets.setdefault(code, set()).add(
            f"{match['type']}:{match['value']}"
        )

    def add_section(
        section_id: str,
        comment: str,
        matches: list[dict[str, str]],
        destination: str,
        resolvers: list[str],
    ) -> None:
        rules: list[str] = []
        for match in matches:
            if any(_covers(entry["match"], match) for entry in planned):
                continue
            planned.append({
                "match": match,
                "policy": destination,
                "dns": list(resolvers),
            })
            rules.append(_rule(match, destination))
            if destination == "FORCE_PROXY":
                rules.append(_rule(match, "REJECT"))
        if rules:
            sections.append({
                "id": section_id,
                "comment": comment,
                "rules": rules,
            })

    for zone in zones:
        add_section(
            f"intranet:{zone['suffix']}",
            "内网域名直连并使用指定 DNS",
            [{"type": "DOMAIN-SUFFIX", "value": zone["suffix"]}],
            "DIRECT",
            list(zone["nameservers"]),
        )

    effective_direct: list[dict[str, str]] = []
    for target in direct_targets:
        match = _target_match(target)
        if _is_local_target(target, routing["bypass_cgnat"]):
            warn("LOCAL_OVERRIDE", match)
            continue
        if any(_overlaps(zone, match) for zone in intranet_matches):
            warn("INTRANET_OVERRIDE", match)
        effective_direct.append(match)

    add_section(
        "custom-direct",
        "用户始终直连",
        effective_direct,
        "DIRECT",
        list(direct_resolvers),
    )

    def effective_proxy(match: dict[str, str], target: dict[str, str] | None = None) -> bool:
        if (
            target and _is_local_target(target, routing["bypass_cgnat"])
        ) or any(_covers(local, match) for local in LOCAL_DOMAINS):
            warn("LOCAL_OVERRIDE", match)
            return False

        if any(_overlaps(local, match) for local in LOCAL_DOMAINS):
            warn("LOCAL_OVERRIDE", match)
        if any(_overlaps(zone, match) for zone in intranet_matches):
            warn("INTRANET_OVERRIDE", match)
        if any(_overlaps(direct_match, match) for direct_match in direct_matches):
            warn("DIRECT_OVERRIDE", match)

        return not (
            any(_covers(zone, match) for zone in intranet_matches)
            or any(_covers(direct_match, match) for direct_match in direct_matches)
        )

    proxy_matches = []
    for target in proxy_targets:
        match = _target_match(target)
        if effective_proxy(match, target):
            proxy_matches.append(match)

    add_section(
        "custom-proxy",
        "用户必须代理；不支持的流量拒绝，不降级直连",
        proxy_matches,
        "FORCE_PROXY",
        proxy_dns("FORCE_PROXY"),
    )

    enabled = routing["presets"]
    for preset in RULE_PRESETS:
        if not enabled.get(preset["id"], preset["default_enabled"]):
            continue

        strict = (
            direct_mode
            or preset["id"] not in {"developer", "google"}
        )
        destination = "FORCE_PROXY" if strict else "PROXY"
        preset_rules = list(preset["rules"])
        if direct_mode:
            preset_rules.extend(DIRECT_MODE_PRESET_ADDITIONS.get(preset["id"], []))
        matches = [
            deepcopy(match)
            for match in preset_rules
            if effective_proxy(match)
        ]
        add_section(
            preset["id"],
            (
                f"{preset['name_zh']}：必须代理；失败不降级直连"
                if strict
                else f"{preset['name_zh']}：使用 PROXY 组"
            ),
            matches,
            destination,
            proxy_dns(destination),
        )

    for entry in planned:
        match = entry["match"]
        if not _is_domain(match):
            continue
        local = any(_covers(local_match, match) for local_match in LOCAL_DOMAINS)
        intranet = any(_covers(zone, match) for zone in intranet_matches)
        if local and not intranet:
            continue
        key = _dns_key(match)
        if key not in policy:
            policy[key] = list(entry["dns"])

    if direct_mode:
        sections.append({
            "id": "fallback",
            "comment": "其余流量使用当前网络直连",
            "rules": ["MATCH,DIRECT"],
        })
    else:
        policy["geosite:cn"] = list(DIRECT_DNS)
        policy["geosite:geolocation-!cn"] = proxy_dns("PROXY")
        sections.append({
            "id": "mainland",
            "comment": "中国大陆域名 / IP 直连",
            "rules": ["GEOSITE,CN,DIRECT", "GEOIP,CN,DIRECT"],
        })
        sections.append({
            "id": "fallback",
            "comment": "其余流量交给 PROXY",
            "rules": ["MATCH,PROXY"],
        })

    seen_rules: set[str] = set()
    for section in sections:
        deduped = []
        for value in section["rules"]:
            if value not in seen_rules:
                seen_rules.add(value)
                deduped.append(value)
        section["rules"] = deduped
    sections = [section for section in sections if section["rules"]]

    return {
        "sections": sections,
        "dns": {
            "enable": True,
            "ipv6": False,
            "enhanced-mode": "fake-ip",
            "fake-ip-range": "198.18.0.1/16",
            "fake-ip-filter": list(dict.fromkeys([
                "+.localhost",
                "+.lan",
                "+.local",
                "+.home.arpa",
                "localhost.ptlogin2.qq.com",
                "+.stun.*.*",
                "+.stun.*.*.*",
                *[f"+.{zone['suffix']}" for zone in zones],
            ])),
            "use-hosts": True,
            "use-system-hosts": True,
            "default-nameserver": ["223.5.5.5", "119.29.29.29"],
            "nameserver": list(direct_resolvers),
            "proxy-server-nameserver": list(direct_resolvers),
            "proxy-server-nameserver-policy": bootstrap_policy,
            "direct-nameserver": list(direct_resolvers),
            "direct-nameserver-follow-policy": True,
            "nameserver-policy": policy,
        },
        "warnings": [
            {"code": code, "count": len(entries)}
            for code, entries in warning_sets.items()
        ],
    }
