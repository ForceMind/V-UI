"""Pure, fail-closed sing-box policy compiler for the frozen relay contract.

Inputs are privileged, consistent snapshots captured under the operation lock.
This module performs no resolution, I/O, or process/network changes. Its complete
config contains credentials and must never be returned by an ordinary API.
Neither syntactic compilation nor a core config check qualifies a double hop.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
import re

from app.services.upstream_profile import UpstreamProfile, UpstreamValidationError, server_host

NAMESPACE = "vui-relay-"
UPSTREAM_TAG = NAMESPACE + "upstream"
LOCAL_TAG = NAMESPACE + "local"
DNS_TAG = NAMESPACE + "business-dns"
NORMAL_DNS_TAG = NAMESPACE + "ordinary-dns"
DENY_CIDRS = (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
    "224.0.0.0/4", "240.0.0.0/4", "::/128", "::1/128", "::ffff:0:0/96",
    "64:ff9b::/96", "64:ff9b:1::/48", "100::/64", "2001::/23", "2001:db8::/32",
    "2002::/16", "fc00::/7", "fe80::/10", "ff00::/8",
)
# The exact complement of 2000::/3: positive membership tests on a mixed DNS
# result would otherwise accept a public answer alongside a forbidden one.
NON_GLOBAL_IPV6_CIDRS = ("::/3", "4000::/2", "8000::/1")
_ID = re.compile(r"up_[0-9a-f]{32}\Z")
_REVISION = re.compile(r"[0-9a-f]{64}\Z")
_SETTINGS = {"schema_version", "local_proxy_enabled", "relay_enabled", "selected_upstream_id",
             "local_port", "local_username", "local_password_ref", "dns_policy_version", "deny_policy_version"}


class PolicyError(ValueError):
    """Stable codes and allowlisted coverage only; never include rejected data."""
    def __init__(self, code: str, coverage=()):
        self.code = code
        self.coverage = copy.deepcopy(list(coverage))
        super().__init__("Proxy policy rejected: " + code)


def _ip(value):
    if type(value) is not str or "%" in value:
        raise PolicyError("INVALID_ADDRESS")
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        raise PolicyError("INVALID_ADDRESS") from None


def _addresses(values):
    if type(values) not in (tuple, list) or len(values) > 4096:
        raise PolicyError("INVALID_ADDRESS_SNAPSHOT")
    return tuple(sorted(set(_ip(value) for value in values), key=lambda value: (value.version, int(value))))


@dataclass(frozen=True)
class VerifiedEndpoint:
    profile_id: str
    revision: str
    host: str
    addresses: tuple[str, ...]
    resolved_at: int


@dataclass(frozen=True)
class AddressPolicy:
    """Version-one public-address policy. Exceptions exist only in Python tests.

    There is deliberately no from_dict, environment switch, or API field for
    fixture exceptions. Production callers construct AddressPolicy() only.
    """
    _fixture_addresses: tuple[str, ...] = field(default=(), repr=False)
    _fixture_doh: tuple[str, int, str] | None = field(default=None, repr=False)

    @classmethod
    def _for_tests(cls, *, allowed_addresses=(), doh=None):
        allowed = tuple(str(value) for value in _addresses(tuple(allowed_addresses)))
        if any(not _ip(value).is_loopback for value in allowed):
            raise PolicyError("TEST_POLICY_REQUIRES_LOOPBACK")
        if doh is not None:
            if (type(doh) is not tuple or len(doh) != 3 or doh[0] not in allowed
                    or type(doh[1]) is not int or not 1024 <= doh[1] <= 65535):
                raise PolicyError("INVALID_TEST_DNS")
            # Names still undergo the same strict profile SNI validation.
            from app.services.upstream_profile import dns_name
            dns_name(doh[2])
        return cls(allowed, doh)

    def check_addresses(self, addresses, *, self_addresses=(), excluded_addresses=()):
        """Reject the entire set on one bad answer, then stable-sort and dedupe."""
        values = _addresses(addresses)
        if not values:
            raise PolicyError("EMPTY_ADDRESS_RESULT")
        blocked = set(_addresses(self_addresses)) | set(_addresses(excluded_addresses))
        denied = tuple(ipaddress.ip_network(cidr) for cidr in DENY_CIDRS)
        allowed = set(_addresses(self._fixture_addresses))
        for value in values:
            if value in blocked:
                raise PolicyError("SELF_OR_UPSTREAM_ADDRESS")
            if value in allowed:
                continue
            if (not value.is_global or value.is_multicast
                    or value.version == 6 and value not in ipaddress.ip_network("2000::/3")
                    or any(value.version == network.version and value in network for network in denied)):
                raise PolicyError("PROHIBITED_ADDRESS")
        return tuple(str(value) for value in values)

    def denied_cidrs(self, self_addresses=(), excluded_addresses=()):
        # Test exceptions subtract exact loopback hosts; no broad private-network
        # bypass and dynamic self/upstream targets remain unconditionally denied.
        networks = [ipaddress.ip_network(cidr) for cidr in DENY_CIDRS + NON_GLOBAL_IPV6_CIDRS]
        for address in _addresses(self._fixture_addresses):
            host = ipaddress.ip_network(f"{address}/{address.max_prefixlen}")
            next_networks = []
            for network in networks:
                if network.version == address.version and address in network:
                    next_networks.extend(network.address_exclude(host))
                else:
                    next_networks.append(network)
            networks = next_networks
        dynamic = set(_addresses(self_addresses)) | set(_addresses(excluded_addresses))
        static = ([str(network) for network in networks] if self._fixture_addresses
                  else list(DENY_CIDRS + NON_GLOBAL_IPV6_CIDRS))
        return static + [
            f"{value}/{value.max_prefixlen}" for value in sorted(dynamic, key=lambda x: (x.version, int(x)))
        ]

    def dns_server(self):
        host, port, sni = self._fixture_doh or ("1.1.1.1", 443, "cloudflare-dns.com")
        return {"type": "https", "tag": DNS_TAG, "server": host, "server_port": port,
                "path": "/dns-query", "tls": {"enabled": True, "server_name": sni},
                "detour": UPSTREAM_TAG}


@dataclass(frozen=True)
class CompiledPolicy:
    config: dict = field(repr=False)
    coverage: list[dict]
    identity: str
    pinned_address: str | None = None
    resolved_at: int | None = None


def _digest(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise PolicyError("INVALID_CONFIG_SNAPSHOT") from None


def _qualification(item):
    core = getattr(item, "core", None)
    protocol = getattr(item, "protocol", None)
    stream = getattr(item, "stream_settings", None)
    transport = stream.get("transport") if type(stream) is dict else None
    transport_name = "tcp" if transport in (None, {}) else (
        transport.get("type") if type(transport) is dict else "unsupported")
    summary = {"inbound_id": item.id, "core": core if core in ("sing-box", "xray") else "unsupported",
               "protocol": protocol if protocol in ("vless", "vmess", "trojan", "shadowsocks", "hysteria2", "tuic") else "unsupported",
               "transport": transport_name if transport_name in ("tcp", "ws", "grpc") else "unsupported",
               "qualified": False, "reason": "UNQUALIFIED_INGRESS"}
    try:
        if core != "sing-box" or protocol != "vless" or transport_name != "tcp" or transport not in (None, {}):
            return summary
        # Same bounded VLESS/TCP/TLS surface as validated_export, kept local to
        # avoid its database/routing imports (which create data directories).
        if (getattr(item, "expiry_time", 0) or type(item.port) is not int
                or not 1 <= item.port <= 65535 or type(stream) is not dict
                or set(stream) - {"tls", "transport", "_vui"}):
            return summary
        settings = item.settings
        if type(settings) is not dict or set(settings) != {"users"}:
            return summary
        users = settings["users"]
        if type(users) is not list or len(users) != 1 or type(users[0]) is not dict:
            return summary
        user = users[0]
        if (set(user) - {"uuid", "name", "flow"} or type(user.get("uuid")) is not str
                or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", user["uuid"])
                or user.get("flow", "") != "" or "name" in user and type(user["name"]) is not str):
            return summary
        tls = stream.get("tls")
        if (type(tls) is not dict or set(tls) - {"enabled", "server_name", "certificate_path", "key_path", "alpn"}
                or tls.get("enabled") is not True):
            return summary
        server_host(tls.get("server_name"))
        meta = stream.get("_vui", {})
        if (type(meta) is not dict or set(meta) - {"security", "server_name", "client_fingerprint", "skip_cert_verify"}
                or meta.get("security", "tls") != "tls" or meta.get("skip_cert_verify", False) is not False
                or meta.get("client_fingerprint", "") not in ("", "chrome")
                or meta.get("server_name") and meta["server_name"] != tls["server_name"]):
            return summary
        alpn = tls.get("alpn")
        if alpn is not None and (type(alpn) is not list or not alpn
                or any(type(value) is not str or not re.fullmatch(r"[A-Za-z0-9./_-]{1,64}", value) for value in alpn)):
            return summary
        if any(type(tls.get(key)) is not str or not tls[key] for key in ("certificate_path", "key_path")):
            summary["reason"] = "MISSING_INGRESS_TLS_MATERIAL"
            return summary
        summary.update(qualified=True, reason="QUALIFIED_VLESS_TCP_TLS")
    except (ValueError, TypeError, AttributeError, KeyError):
        pass
    return summary


def ingress_coverage(inbounds):
    """Snapshot all enabled cores, without exposing tags/names/config/credentials."""
    result, ids = [], set()
    for item in inbounds:
        if type(getattr(item, "enable", None)) is not bool:
            raise PolicyError("INVALID_INGRESS_SNAPSHOT")
        if not item.enable:
            continue
        identifier = getattr(item, "id", None)
        if type(identifier) is not int or identifier <= 0 or identifier in ids:
            raise PolicyError("INVALID_INGRESS_ID")
        ids.add(identifier)
        result.append(_qualification(item))
    return sorted(result, key=lambda row: row["inbound_id"])


def _check_tags(config):
    tags = []
    for key in ("inbounds", "outbounds", "endpoints"):
        values = config.get(key, [])
        if type(values) is not list or any(type(value) is not dict for value in values):
            raise PolicyError("INVALID_BASE_CONFIG")
        tags.extend(value.get("tag") for value in values)
    dns = config.get("dns", {})
    if type(dns) is not dict or type(dns.get("servers", [])) is not list:
        raise PolicyError("INVALID_BASE_CONFIG")
    tags.extend(value.get("tag") if type(value) is dict else None for value in dns.get("servers", []))
    if any(type(tag) is not str or not tag or tag.startswith(NAMESPACE) for tag in tags):
        raise PolicyError("RESERVED_OR_MISSING_TAG")
    if len(set(tags)) != len(tags):
        raise PolicyError("DUPLICATE_TAG")
    # Reject dangling references to our namespace too: introducing an internal
    # tag must not accidentally activate a preexisting detour or route.
    def visit(value):
        if type(value) is dict:
            for key, child in value.items():
                if key in {"tag", "inbound", "outbound", "detour", "final", "server", "default_domain_resolver"}:
                    references = child if type(child) is list else [child]
                    if any(type(ref) is str and ref.startswith(NAMESPACE) for ref in references):
                        raise PolicyError("RESERVED_TAG_REFERENCE")
                visit(child)
        elif type(value) is list:
            for child in value:
                visit(child)
    visit(config)


def _bind_ingress(config, inbounds):
    existing = config.get("inbounds", [])
    rows = [item for item in inbounds if item.enable and item.core == "sing-box"]
    if len(existing) != len(rows):
        raise PolicyError("INGRESS_SNAPSHOT_MISMATCH")
    by_tag = {value["tag"]: value for value in existing}
    used = set()
    for item in rows:
        tag = getattr(item, "tag", None) or f"sing-box-{item.protocol}-{item.port}"
        if type(tag) is not str or tag in used:
            raise PolicyError("INGRESS_TAG_COLLISION")
        used.add(tag)
        stream = item.stream_settings or {}
        expected = {"type": item.protocol, "tag": tag, "listen": "::", "listen_port": item.port,
                    **copy.deepcopy(item.settings)}
        for key in ("tls", "transport", "multiplex"):
            if key in stream:
                expected[key] = copy.deepcopy(stream[key])
        if by_tag.get(tag) != expected:
            raise PolicyError("INGRESS_SNAPSHOT_MISMATCH")
        by_tag[tag]["tag"] = f"{NAMESPACE}ingress-{item.id}"
    return [f"{NAMESPACE}ingress-{item.id}" for item in sorted(rows, key=lambda row: row.id)]


def _settings(snapshot):
    if type(snapshot) is not dict or type(snapshot.get("settings")) is not dict:
        raise PolicyError("INVALID_PROXY_SNAPSHOT")
    settings = snapshot["settings"]
    if set(settings) != _SETTINGS:
        raise PolicyError("INVALID_PROXY_SETTINGS")
    if any(type(settings[key]) is not bool for key in ("local_proxy_enabled", "relay_enabled")):
        raise PolicyError("INVALID_PROXY_SETTINGS")
    if any(type(settings[key]) is not int or settings[key] != 1 for key in ("schema_version", "dns_policy_version", "deny_policy_version")):
        raise PolicyError("UNSUPPORTED_POLICY_VERSION")
    return settings


def _upstream(profile, pinned):
    tls = {"enabled": True, "server_name": profile.tls.server_name}
    if profile.tls.alpn:
        tls["alpn"] = list(profile.tls.alpn)
    if profile.tls.fingerprint:
        tls["utls"] = {"enabled": True, "fingerprint": profile.tls.fingerprint}
    if profile.tls.mode == "reality":
        tls["reality"] = {"enabled": True, "public_key": profile.tls.public_key,
                          "short_id": profile.tls.short_id}
    value = {"type": "vless", "tag": UPSTREAM_TAG, "server": pinned,
             "server_port": profile.server_port, "uuid": profile.uuid, "network": "tcp", "tls": tls}
    if profile.flow:
        value["flow"] = profile.flow
    return value


def compile_proxy_policy(base_config, inbounds, proxy_snapshot, *, endpoint=None,
                         self_addresses=None, policy=None):
    """Compile a full config from one locked snapshot; do not execute or resolve.

    ``endpoint`` must originate from the explicit bounded bootstrap check and
    be bound to this profile ID, revision and original host. ``self_addresses``
    must explicitly contain the checked interface/panel/listener snapshot.
    ``policy`` is a Python-only fixture seam, never deserialized from an API.
    """
    settings = _settings(proxy_snapshot)
    if type(base_config) is not dict:
        raise PolicyError("INVALID_BASE_CONFIG")
    config = copy.deepcopy(base_config)
    rows = list(inbounds)
    coverage = ingress_coverage(rows)
    local, relay = settings["local_proxy_enabled"], settings["relay_enabled"]
    if not local and not relay:
        return CompiledPolicy(config, coverage, _digest(config))
    if relay and any(not row["qualified"] for row in coverage):
        raise PolicyError("UNQUALIFIED_INGRESS", coverage)
    policy = AddressPolicy() if policy is None else policy
    if type(policy) is not AddressPolicy or self_addresses is None:
        raise PolicyError("MISSING_ADDRESS_SNAPSHOT")
    self_addresses = tuple(str(value) for value in _addresses(self_addresses))
    if not self_addresses:
        raise PolicyError("MISSING_ADDRESS_SNAPSHOT")
    profile_id, revision = settings["selected_upstream_id"], proxy_snapshot.get("revision")
    if (type(profile_id) is not str or not _ID.fullmatch(profile_id)
            or type(revision) is not str or not _REVISION.fullmatch(revision)):
        raise PolicyError("INVALID_PROXY_SNAPSHOT")
    try:
        profile = UpstreamProfile.from_dict(proxy_snapshot["upstreams"][profile_id])
    except (UpstreamValidationError, KeyError, TypeError):
        raise PolicyError("INVALID_SELECTED_UPSTREAM") from None
    if (type(endpoint) is not VerifiedEndpoint or endpoint.profile_id != profile_id
            or endpoint.revision != revision or endpoint.host != profile.server
            or type(endpoint.addresses) is not tuple
            or type(endpoint.resolved_at) is not int or endpoint.resolved_at < 0):
        raise PolicyError("ENDPOINT_SNAPSHOT_MISMATCH")
    checked = policy.check_addresses(endpoint.addresses, self_addresses=self_addresses)
    try:
        literal = _ip(profile.server)
    except PolicyError:
        literal = None
    if literal is not None and checked != (str(literal),):
        raise PolicyError("ENDPOINT_LITERAL_MISMATCH")
    pinned = checked[0]
    _check_tags(config)
    selected_tags = _bind_ingress(config, rows) if relay else []
    if local:
        port, username, password = settings["local_port"], settings["local_username"], proxy_snapshot.get("local_password")
        if (type(port) is not int or not 1024 <= port <= 65535
                or type(username) is not str or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", username)
                or type(password) is not str or not 12 <= len(password) <= 255
                or any(not 33 <= ord(char) <= 126 for char in password)
                or not settings["local_password_ref"]):
            raise PolicyError("INVALID_LOCAL_AUTHENTICATION")
        # This is a snapshot collision guard, not the separate live TCP/UDP,
        # IPv4/IPv6 ownership probes required before runtime activation.
        if any(item.enable and item.port == port for item in rows) or any(
                value.get("listen_port") == port for value in config.get("inbounds", [])):
            raise PolicyError("LOCAL_PORT_COLLISION")
        config.setdefault("inbounds", []).append({"type": "mixed", "tag": LOCAL_TAG,
            "listen": "127.0.0.1", "listen_port": port, "set_system_proxy": False,
            "users": [{"username": username, "password": password}]})
        selected_tags.append(LOCAL_TAG)
    config.setdefault("outbounds", []).append(_upstream(profile, pinned))
    dns = config.setdefault("dns", {})
    servers = dns.setdefault("servers", [])
    if not servers and not relay:
        # Existing direct-only configs implicitly use the local resolver. Keep
        # ordinary ingress behavior in state 10. Protected sources always use
        # the explicit business server below; this is never their fallback.
        servers.append({"type": "local", "tag": NORMAL_DNS_TAG})
        dns.setdefault("final", NORMAL_DNS_TAG)
    servers.append(policy.dns_server())
    route = config.setdefault("route", {})
    if type(route) is not dict or type(route.get("rules", [])) is not list:
        raise PolicyError("INVALID_BASE_ROUTE")
    # With multiple DNS transports, 1.14.2 requires an explicit dialer
    # resolver. Keep the preexisting normal default; protected requests have
    # already resolved through DNS_TAG and cannot reach this fallback.
    if len(servers) > 1:
        route.setdefault("default_domain_resolver", dns.get("final") or servers[0]["tag"])
    denied = policy.denied_cidrs(self_addresses, (pinned,))
    guards = [{"inbound": selected_tags, "ip_cidr": denied, "action": "reject"}]
    if not policy._fixture_addresses:
        guards.append({"inbound": selected_tags, "ip_is_private": True, "action": "reject"})
    # Pinned v1.14.2: route/rule/rule_item_cidr.go matches any resolved
    # DestinationAddresses; route/route.go resolve errors abort routing;
    # route/conn.go dials the same DestinationAddresses via DialSerialNetwork.
    # https://github.com/SagerNet/sing-box/tree/v1.14.2/route
    rules = [{"inbound": selected_tags, "network": "udp", "action": "reject"},
             *copy.deepcopy(guards),
             {"inbound": selected_tags, "action": "resolve", "server": DNS_TAG,
              "strategy": "prefer_ipv4", "disable_cache": True, "disable_optimistic_cache": True},
             *copy.deepcopy(guards),
             {"inbound": selected_tags, "network": "tcp", "ip_cidr": ["0.0.0.0/0", "::/0"],
              "action": "route", "outbound": UPSTREAM_TAG},
             {"inbound": selected_tags, "action": "reject"}]
    # An empty selected source list would match every source; state 01 with no
    # enabled nodes creates no rules until an ingress snapshot exists.
    if selected_tags:
        route["rules"] = rules + route.get("rules", [])
    identity = _digest({"config": config, "revision": revision, "coverage": coverage,
                        "self_addresses": self_addresses, "endpoint_addresses": checked,
                        "resolved_at": endpoint.resolved_at, "deny_policy_version": 1, "dns_policy_version": 1})
    return CompiledPolicy(config, coverage, identity, pinned, endpoint.resolved_at)
