from __future__ import annotations

import ipaddress
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DATA_DIR = Path(os.getenv("VUI_DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
ROUTING_FILE = DATA_DIR / "mihomo-routing.json"

DIRECT_DNS = ["https://dns.alidns.com/dns-query", "https://doh.pub/dns-query"]
SYSTEM_DNS = ["system"]
LOCAL_DNS = ["rcode://refused"]
LOCAL_DOMAINS = [
    {"type": "DOMAIN-SUFFIX", "value": "localhost"},
    {"type": "DOMAIN-SUFFIX", "value": "local"},
    {"type": "DOMAIN-SUFFIX", "value": "lan"},
    {"type": "DOMAIN-SUFFIX", "value": "home.arpa"},
]
LOCAL_IP_RULES = [
    "IP-CIDR,127.0.0.0/8,DIRECT,no-resolve",
    "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve",
    "IP-CIDR,172.16.0.0/12,DIRECT,no-resolve",
    "IP-CIDR,192.168.0.0/16,DIRECT,no-resolve",
    "IP-CIDR,169.254.0.0/16,DIRECT,no-resolve",
    "IP-CIDR6,::1/128,DIRECT,no-resolve",
    "IP-CIDR6,fc00::/7,DIRECT,no-resolve",
    "IP-CIDR6,fe80::/10,DIRECT,no-resolve",
]
CGNAT_RULE = "IP-CIDR,100.64.0.0/10,DIRECT,no-resolve"

PRESET_CATEGORIES = [
    {"id": "ai", "name_zh": "AI 服务", "name_en": "AI services"},
    {"id": "social", "name_zh": "社交与通讯", "name_en": "Social & messaging"},
    {"id": "media", "name_zh": "媒体与流媒体", "name_en": "Media & streaming"},
    {"id": "work", "name_zh": "工作与协作", "name_en": "Work & collaboration"},
    {"id": "developer", "name_zh": "开发者服务", "name_en": "Developer services"},
    {"id": "commerce", "name_zh": "购物与支付", "name_en": "Shopping & payments"},
    {"id": "gaming", "name_zh": "游戏", "name_en": "Gaming"},
]


def _suffixes(*values: str) -> list[dict[str, str]]:
    return [{"type": "DOMAIN-SUFFIX", "value": value} for value in values]


# Synchronized from ForceMind/ToClash v0.3.8 service catalogue.
RULE_PRESETS = [
    {
        "id": "openai", "category": "ai", "name_zh": "Codex / OpenAI",
        "name_en": "Codex / OpenAI", "default_enabled": True,
        "rules": [
            *_suffixes(
                "openai.com", "chatgpt.com", "oaistatic.com", "oaiusercontent.com",
                "oaistatsig.com", "workos.com", "workoscdn.com",
            ),
            {"type": "DOMAIN", "value": "workos.imgix.net"},
            {"type": "DOMAIN", "value": "challenges.cloudflare.com"},
        ],
    },
    {
        "id": "claude", "category": "ai", "name_zh": "Claude / Anthropic",
        "name_en": "Claude / Anthropic", "default_enabled": True,
        "rules": [
            *_suffixes("anthropic.com", "claude.ai", "claude.com", "anthropic-static.com"),
            {"type": "DOMAIN", "value": "bridge.claudeusercontent.com"},
            {"type": "DOMAIN", "value": "storage.googleapis.com"},
            {"type": "DOMAIN", "value": "raw.githubusercontent.com"},
        ],
    },
    {
        "id": "developer", "category": "developer", "name_zh": "开发者服务 / GitHub",
        "name_en": "Developer services / GitHub", "default_enabled": True,
        "rules": _suffixes("github.com", "githubusercontent.com", "githubassets.com", "github.io"),
    },
    {
        "id": "google", "category": "media", "name_zh": "Google / YouTube",
        "name_en": "Google / YouTube", "default_enabled": True,
        "rules": _suffixes(
            "google.com", "googleapis.com", "gstatic.com", "youtube.com", "ytimg.com",
            "googlevideo.com", "ggpht.com", "gvt1.com", "youtu.be",
        ),
    },
    {"id": "x", "category": "social", "name_zh": "X / Twitter", "name_en": "X / Twitter", "default_enabled": False, "rules": _suffixes("x.com", "twitter.com", "twimg.com", "t.co")},
    {"id": "tiktok", "category": "social", "name_zh": "TikTok", "name_en": "TikTok", "default_enabled": False, "rules": _suffixes("tiktok.com", "tiktokv.com", "tiktokcdn.com", "tiktokcdn-us.com", "byteoversea.com", "musical.ly", "muscdn.com")},
    {"id": "meta", "category": "social", "name_zh": "Meta / Meta AI / Quest", "name_en": "Meta / Meta AI / Quest", "default_enabled": False, "rules": _suffixes("meta.com", "meta.ai", "oculus.com")},
    {"id": "facebook", "category": "social", "name_zh": "Facebook", "name_en": "Facebook", "default_enabled": False, "rules": _suffixes("facebook.com", "fb.com", "fb.me", "fbcdn.net", "fbsbx.com")},
    {"id": "instagram", "category": "social", "name_zh": "Instagram", "name_en": "Instagram", "default_enabled": False, "rules": _suffixes("instagram.com", "cdninstagram.com")},
    {"id": "threads", "category": "social", "name_zh": "Threads", "name_en": "Threads", "default_enabled": False, "rules": _suffixes("threads.com", "threads.net")},
    {"id": "reddit", "category": "social", "name_zh": "Reddit", "name_en": "Reddit", "default_enabled": False, "rules": _suffixes("reddit.com", "redd.it", "redditstatic.com", "redditmedia.com")},
    {"id": "telegram", "category": "social", "name_zh": "Telegram", "name_en": "Telegram", "default_enabled": False, "rules": _suffixes("telegram.org", "telegram.me", "t.me", "tdlib.org", "telegram.dog")},
    {"id": "whatsapp", "category": "social", "name_zh": "WhatsApp", "name_en": "WhatsApp", "default_enabled": False, "rules": _suffixes("whatsapp.com", "whatsapp.net", "wa.me")},
    {"id": "discord", "category": "social", "name_zh": "Discord", "name_en": "Discord", "default_enabled": False, "rules": _suffixes("discord.com", "discordapp.com", "discordapp.net", "discord.gg", "discord.media", "discordcdn.com")},
    {"id": "linkedin", "category": "social", "name_zh": "LinkedIn", "name_en": "LinkedIn", "default_enabled": False, "rules": _suffixes("linkedin.com", "licdn.com", "lnkd.in")},
    {"id": "pinterest", "category": "social", "name_zh": "Pinterest", "name_en": "Pinterest", "default_enabled": False, "rules": _suffixes("pinterest.com", "pinimg.com")},
    {"id": "snapchat", "category": "social", "name_zh": "Snapchat", "name_en": "Snapchat", "default_enabled": False, "rules": _suffixes("snapchat.com", "snap.com", "sc-cdn.net")},
    {"id": "netflix", "category": "media", "name_zh": "Netflix", "name_en": "Netflix", "default_enabled": False, "rules": _suffixes("netflix.com", "netflix.net", "nflxext.com", "nflximg.com", "nflximg.net", "nflxvideo.net", "fast.com")},
    {"id": "disney", "category": "media", "name_zh": "Disney+", "name_en": "Disney+", "default_enabled": False, "rules": _suffixes("disneyplus.com", "disney-plus.net", "disneyplus.net", "bamgrid.com")},
    {"id": "primevideo", "category": "media", "name_zh": "Prime Video", "name_en": "Prime Video", "default_enabled": False, "rules": _suffixes("primevideo.com", "amazonvideo.com", "aiv-cdn.net")},
    {"id": "spotify", "category": "media", "name_zh": "Spotify", "name_en": "Spotify", "default_enabled": False, "rules": _suffixes("spotify.com", "spoti.fi", "scdn.co", "spotifycdn.com", "spotifycdn.net")},
    {"id": "twitch", "category": "media", "name_zh": "Twitch", "name_en": "Twitch", "default_enabled": False, "rules": _suffixes("twitch.tv", "ttvnw.net", "jtvnw.net", "twitchcdn.net")},
    {"id": "perplexity", "category": "ai", "name_zh": "Perplexity", "name_en": "Perplexity", "default_enabled": False, "rules": _suffixes("perplexity.ai", "pplx.ai", "perplexityusercontent.com")},
    {"id": "grok", "category": "ai", "name_zh": "Grok", "name_en": "Grok", "default_enabled": False, "rules": _suffixes("grok.com", "x.ai")},
    {"id": "microsoft", "category": "work", "name_zh": "Microsoft", "name_en": "Microsoft", "default_enabled": False, "rules": _suffixes("microsoft.com", "microsoftonline.com", "office.com", "office365.com", "outlook.com", "live.com", "msauth.net", "msftauth.net", "msftauthimages.net")},
    {"id": "apple", "category": "work", "name_zh": "Apple / iCloud", "name_en": "Apple / iCloud", "default_enabled": False, "rules": _suffixes("apple.com", "apple.co", "icloud.com", "icloud-content.com", "apple-cloudkit.com", "apple-mapkit.com", "cdn-apple.com")},
    {"id": "notion", "category": "work", "name_zh": "Notion", "name_en": "Notion", "default_enabled": False, "rules": _suffixes("notion.so", "notion.site", "notion-static.com", "notionusercontent.com", "notion.com")},
    {"id": "slack", "category": "work", "name_zh": "Slack", "name_en": "Slack", "default_enabled": False, "rules": _suffixes("slack.com", "slack-edge.com", "slack-files.com", "slackb.com")},
    {"id": "zoom", "category": "work", "name_zh": "Zoom", "name_en": "Zoom", "default_enabled": False, "rules": _suffixes("zoom.us", "zoom.com", "zoomgov.com", "zoomcdn.com")},
    {"id": "dropbox", "category": "work", "name_zh": "Dropbox", "name_en": "Dropbox", "default_enabled": False, "rules": _suffixes("dropbox.com", "dropboxapi.com", "dropboxstatic.com", "dropboxusercontent.com", "getdropbox.com")},
    {"id": "figma", "category": "work", "name_zh": "Figma", "name_en": "Figma", "default_enabled": False, "rules": _suffixes("figma.com", "figmaweave.com")},
    {"id": "adobe", "category": "work", "name_zh": "Adobe", "name_en": "Adobe", "default_enabled": False, "rules": _suffixes("adobe.com", "adobe.io", "adobelogin.com", "adobe-identity.com", "adobejanus.com", "adobecc.com", "adobeccstatic.com")},
    {"id": "aws", "category": "developer", "name_zh": "AWS 控制台", "name_en": "AWS Console", "default_enabled": False, "rules": _suffixes("aws.amazon.com", "signin.aws", "awsapps.com")},
    {"id": "cloudflare", "category": "developer", "name_zh": "Cloudflare", "name_en": "Cloudflare", "default_enabled": False, "rules": _suffixes("cloudflare.com")},
    {"id": "amazon", "category": "commerce", "name_zh": "Amazon 购物", "name_en": "Amazon Shopping", "default_enabled": False, "rules": _suffixes("amazon.com")},
    {"id": "paypal", "category": "commerce", "name_zh": "PayPal", "name_en": "PayPal", "default_enabled": False, "rules": _suffixes("paypal.com", "paypalobjects.com")},
    {"id": "stripe", "category": "commerce", "name_zh": "Stripe", "name_en": "Stripe", "default_enabled": False, "rules": _suffixes("stripe.com")},
    {"id": "steam", "category": "gaming", "name_zh": "Steam", "name_en": "Steam", "default_enabled": False, "rules": _suffixes("steampowered.com", "steamcommunity.com", "steamstatic.com", "steamcontent.com", "steam-chat.com", "steamgames.com")},
    {"id": "epic", "category": "gaming", "name_zh": "Epic Games", "name_en": "Epic Games", "default_enabled": False, "rules": _suffixes("epicgames.com", "epicgames.dev", "unrealengine.com", "fortnite.com")},
    {"id": "roblox", "category": "gaming", "name_zh": "Roblox", "name_en": "Roblox", "default_enabled": False, "rules": _suffixes("roblox.com", "rbxcdn.com")},
]

DIRECT_MODE_PRESET_ADDITIONS = {
    "openai": [{"type": "DOMAIN", "value": "cdn.openaimerge.com"}],
    "claude": _suffixes("claudeusercontent.com"),
    "google": _suffixes(
        "google.com.hk", "google.com.tw", "google.com.sg", "google.co.jp",
        "google.co.uk", "googleusercontent.com", "gvt2.com", "gmail.com",
        "googlemail.com", "recaptcha.net", "g.co", "goo.gl",
        "youtube-nocookie.com",
    ),
}

RESERVED_PROXY_NAMES = {
    "DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL", "DNS",
    "PROXY", "AUTO", "FORCE_PROXY",
}


def proxy_dns(group: str) -> list[str]:
    return [
        f"https://1.1.1.1/dns-query#{group}",
        f"https://8.8.8.8/dns-query#{group}",
    ]


def default_presets() -> dict[str, bool]:
    return {
        preset["id"]: bool(preset["default_enabled"])
        for preset in RULE_PRESETS
    }


def default_routing() -> dict[str, Any]:
    return {
        "mode": "standard",
        "direct_domains": [],
        "proxy_domains": [],
        "presets": default_presets(),
        "bypass_cgnat": False,
        "intranet": [],
    }


def catalog() -> dict[str, Any]:
    return {
        "categories": deepcopy(PRESET_CATEGORIES),
        "presets": [
            {
                key: deepcopy(preset[key])
                for key in ("id", "category", "name_zh", "name_en", "default_enabled")
            }
            for preset in RULE_PRESETS
        ],
    }


def _unsafe_text(value: str) -> bool:
    return (
        any(character.isspace() for character in value)
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def parse_routing_target(value: str) -> dict[str, str] | None:
    entry = str(value or "").strip()
    if not entry or _unsafe_text(entry):
        return None

    wildcard = entry.startswith("*.") or entry.startswith("+.")
    if wildcard:
        entry = entry[2:]

    try:
        ip = ipaddress.ip_address(entry.strip("[]"))
        if wildcard:
            return None
        return {
            "kind": "ipv4" if ip.version == 4 else "ipv6",
            "value": ip.compressed.lower(),
        }
    except ValueError:
        pass

    try:
        if "://" in entry:
            parsed = urlsplit(entry)
            if parsed.scheme.lower() not in {"http", "https", "ws", "wss"}:
                return None
            if parsed.username or parsed.password:
                return None
            host = parsed.hostname
        else:
            if any(token in entry for token in ("/", "?", "#", "@")):
                return None
            host = entry

        if not host:
            return None
        host = host.lower().rstrip(".")
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
        if len(host) > 253:
            return None
        labels = host.split(".")
        pattern = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
        if not labels or not all(pattern.fullmatch(label) for label in labels):
            return None
        return {"kind": "domain", "value": host}
    except (ValueError, UnicodeError):
        return None


def normalize_dns_server(value: str) -> str | None:
    entry = str(value or "").strip()
    if entry == "system":
        return "system"
    if entry.lower().startswith("udp://"):
        entry = entry[6:]
    if not entry or _unsafe_text(entry) or any(ch in entry for ch in "/@?#"):
        return None

    host = entry
    port = 53
    if entry.startswith("["):
        match = re.fullmatch(r"\[([^\]]+)\](?::(\d+))?", entry)
        if not match:
            return None
        host = match.group(1)
        if match.group(2):
            port = int(match.group(2))
    elif entry.count(":") == 1:
        host, port_text = entry.rsplit(":", 1)
        if not port_text.isdigit():
            return None
        port = int(port_text)
    elif entry.count(":") > 1:
        host = entry

    if not 1 <= port <= 65535:
        return None

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    if ip.is_unspecified:
        return None
    rendered = f"[{ip.compressed}]" if ip.version == 6 else ip.compressed
    return f"udp://{rendered}:{port}"


def normalize_routing(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload or {}
    mode = payload.get("mode", "standard")
    if mode not in {"standard", "direct"}:
        raise ValueError("mode must be 'standard' or 'direct'")

    def normalize_targets(values: Any) -> list[str]:
        if values is None:
            return []
        if not isinstance(values, list):
            raise ValueError("routing domains must be arrays")
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            target = parse_routing_target(str(raw))
            if not target:
                raise ValueError(f"invalid routing target: {raw}")
            value = target["value"]
            if value not in seen:
                seen.add(value)
                result.append(value)
        return result

    presets = default_presets()
    supplied_presets = payload.get("presets") or {}
    if not isinstance(supplied_presets, dict):
        raise ValueError("presets must be an object")
    for preset_id, enabled in supplied_presets.items():
        if preset_id in presets:
            presets[preset_id] = bool(enabled)

    intranet: list[dict[str, Any]] = []
    seen_zones: dict[str, list[str]] = {}
    for zone in payload.get("intranet") or []:
        if not isinstance(zone, dict):
            raise ValueError("intranet zones must be objects")
        target = parse_routing_target(str(zone.get("suffix") or ""))
        if not target or target["kind"] != "domain":
            raise ValueError("invalid intranet suffix")
        servers: list[str] = []
        for raw_server in zone.get("nameservers") or []:
            server = normalize_dns_server(str(raw_server))
            if not server:
                raise ValueError("invalid intranet DNS server")
            if server not in servers:
                servers.append(server)
        if not servers:
            raise ValueError("intranet zone requires at least one DNS server")
        previous = seen_zones.get(target["value"])
        if previous is not None and previous != servers:
            raise ValueError("same intranet suffix has conflicting DNS servers")
        seen_zones[target["value"]] = servers

    for suffix, nameservers in sorted(
        seen_zones.items(),
        key=lambda item: (-len(item[0].split(".")), item[0]),
    ):
        intranet.append({"suffix": suffix, "nameservers": nameservers})

    return {
        "mode": mode,
        "direct_domains": normalize_targets(payload.get("direct_domains")),
        "proxy_domains": normalize_targets(payload.get("proxy_domains")),
        "presets": presets,
        "bypass_cgnat": bool(payload.get("bypass_cgnat", False)),
        "intranet": intranet,
    }


def load_routing() -> dict[str, Any]:
    if not ROUTING_FILE.exists():
        return default_routing()
    try:
        with ROUTING_FILE.open("r", encoding="utf-8") as handle:
            return normalize_routing(json.load(handle))
    except (OSError, json.JSONDecodeError, ValueError):
        return default_routing()


def save_routing(payload: dict[str, Any]) -> dict[str, Any]:
    routing = normalize_routing(payload)
    temp = ROUTING_FILE.with_suffix(".json.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(routing, handle, ensure_ascii=False, indent=2)
    temp.replace(ROUTING_FILE)
    return routing


def _target_match(target: dict[str, str]) -> dict[str, str]:
    if target["kind"] == "domain":
        return {"type": "DOMAIN-SUFFIX", "value": target["value"]}
    return {
        "type": "IP-CIDR" if target["kind"] == "ipv4" else "IP-CIDR6",
        "value": f"{target['value']}/{'32' if target['kind'] == 'ipv4' else '128'}",
    }


def _is_domain(match: dict[str, str]) -> bool:
    return match["type"] in {"DOMAIN", "DOMAIN-SUFFIX"}


def _covers(parent: dict[str, str], child: dict[str, str]) -> bool:
    if not _is_domain(parent) or not _is_domain(child):
        return parent["type"] == child["type"] and parent["value"] == child["value"]
    if parent["type"] == "DOMAIN":
        return child["type"] == "DOMAIN" and parent["value"] == child["value"]
    return (
        child["value"] == parent["value"]
        or child["value"].endswith(f".{parent['value']}")
    )


def _overlaps(left: dict[str, str], right: dict[str, str]) -> bool:
    return _covers(left, right) or _covers(right, left)


class _MatchIndex:
    """Per-plan lookup with the exact type and domain-boundary rules of _covers."""

    def __init__(self, matches=()) -> None:
        self.exact: set[tuple[str, str]] = set()
        self.suffixes: set[str] = set()
        self.domain_ancestors: set[str] = set()
        for match in matches:
            self.add(match)

    @staticmethod
    def _ancestors(value: str):
        yield value
        for offset, char in enumerate(value):
            if char == ".":
                yield value[offset + 1:]

    def add(self, match: dict[str, str]) -> None:
        kind, value = match["type"], match["value"]
        self.exact.add((kind, value))
        if kind in {"DOMAIN", "DOMAIN-SUFFIX"}:
            self.domain_ancestors.update(self._ancestors(value))
        if kind == "DOMAIN-SUFFIX":
            self.suffixes.add(value)

    def covers(self, match: dict[str, str]) -> bool:
        kind, value = match["type"], match["value"]
        if (kind, value) in self.exact:
            return True
        return kind in {"DOMAIN", "DOMAIN-SUFFIX"} and any(
            suffix in self.suffixes for suffix in self._ancestors(value)
        )

    def overlaps(self, match: dict[str, str]) -> bool:
        return self.covers(match) or (
            match["type"] == "DOMAIN-SUFFIX"
            and match["value"] in self.domain_ancestors
        )


def _rule(match: dict[str, str], policy: str) -> str:
    suffix = "" if _is_domain(match) else ",no-resolve"
    return f"{match['type']},{match['value']},{policy}{suffix}"


def _dns_key(match: dict[str, str]) -> str:
    return match["value"] if match["type"] == "DOMAIN" else f"+.{match['value']}"


def _is_local_target(target: dict[str, str], bypass_cgnat: bool) -> bool:
    if target["kind"] == "domain":
        match = _target_match(target)
        return (
            target["value"] == "localhost"
            or any(_covers(local, match) for local in LOCAL_DOMAINS)
        )

    ip = ipaddress.ip_address(target["value"])
    networks = [
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("169.254.0.0/16"),
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("fc00::/7"),
        ipaddress.ip_network("fe80::/10"),
    ]
    if bypass_cgnat:
        networks.append(ipaddress.ip_network("100.64.0.0/10"))
    return any(ip in network for network in networks if ip.version == network.version)


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
    intranet_index = _MatchIndex(intranet_matches)
    direct_index = _MatchIndex(direct_matches)

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
    planned_index = _MatchIndex()
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
            if planned_index.covers(match):
                continue
            planned_index.add(match)
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
        if intranet_index.overlaps(match):
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
        if intranet_index.overlaps(match):
            warn("INTRANET_OVERRIDE", match)
        if direct_index.overlaps(match):
            warn("DIRECT_OVERRIDE", match)

        return not (
            intranet_index.covers(match)
            or direct_index.covers(match)
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
        intranet = intranet_index.covers(match)
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


def unique_proxy_names(proxies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    used = set(RESERVED_PROXY_NAMES)
    result: list[dict[str, Any]] = []
    for proxy in proxies:
        item = deepcopy(proxy)
        original = str(item.get("name") or "node")
        name = original
        suffix = 2
        while name in used:
            name = f"{original} {suffix}"
            suffix += 1
        item["name"] = name
        used.add(name)
        result.append(item)
    return result


def build_policy_groups(node_names: list[str], routing: dict[str, Any]) -> list[dict[str, Any]]:
    if not node_names:
        return []
    if routing["mode"] == "direct":
        return [{
            "name": "FORCE_PROXY",
            "type": "select",
            "proxies": list(node_names),
        }]
    return [
        {
            "name": "PROXY",
            "type": "select",
            "proxies": ["AUTO", "DIRECT", *node_names],
        },
        {
            "name": "AUTO",
            "type": "url-test",
            "proxies": list(node_names),
            "url": "https://www.gstatic.com/generate_204",
            "interval": 300,
        },
        {
            "name": "FORCE_PROXY",
            "type": "select",
            "proxies": ["AUTO", *node_names],
        },
    ]
