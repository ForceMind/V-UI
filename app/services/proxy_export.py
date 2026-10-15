"""Explicit, credential-bearing strict relay exports, separate from subscriptions.

Only a committed, running, all-ingress-consistent relay can be exported. The
administrator supplies one public IP literal for the domestic ingress; domains
are rejected rather than resolved implicitly. TLS retains the ingress's stored
identity. These files configure explicit loopback clients, not TUN/system DNS.
They intentionally contain that ingress's existing UUID, never overseas secrets.

Mihomo 1.19.32 uses an IP DoH URL plus name-cert-verify: certificate identity is
verified, but that option does not change SNI. sing-box 1.14.2 supports explicit
DoH SNI. Both connect through the sole ingress, with no direct DNS fallback.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import ipaddress
import json
import re

import yaml

from app.services.core_runtime import revision
from app.services.proxy_policy import AddressPolicy, PolicyError, ingress_coverage
from app.services.proxy_store import ProxyConflict, ProxyStorageError
from app.services.validated_export import ExportError, _client_tls, validated_node

RELAY_TAG = "VUI-STRICT-RELAY"
DNS_TAG = "vui-strict-dns"
DNS_INBOUND = "vui-strict-dns-in"
_REVISION = re.compile(r"[0-9a-f]{64}\Z")


class StrictExportError(ValueError):
    """Only a fixed, non-sensitive code may leave this boundary."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class RelayExport:
    content: str = field(repr=False)
    media_type: str
    filename: str
    revision: str


def _public_server(server):
    if type(server) is not str or not server or len(server) > 45 or "%" in server:
        raise StrictExportError("STRICT_EXPORT_PUBLIC_IP_REQUIRED")
    try:
        address = ipaddress.ip_address(server)
    except ValueError:
        raise StrictExportError("STRICT_EXPORT_PUBLIC_IP_REQUIRED") from None
    try:
        return AddressPolicy().check_addresses((str(address),))[0]
    except PolicyError:
        raise StrictExportError("STRICT_EXPORT_PUBLIC_IP_REQUIRED") from None


def _client_config(node, kind):
    """Pure mapping after the service's active-state and ingress checks.

    No caller-supplied config, DNS, routing, paths, tags or providers are merged.
    Tests may narrowly substitute loopback fixture endpoints in the result.
    """
    if kind == "sing-box":
        return {
            "log": {"level": "warn"},
            "inbounds": [{"type": "mixed", "tag": "vui-strict-local", "listen": "127.0.0.1",
                          "listen_port": 7890},
                         {"type": "direct", "tag": DNS_INBOUND, "listen": "127.0.0.1", "listen_port": 1053}],
            "outbounds": [{"type": "vless", "tag": RELAY_TAG, "server": node["server"],
                           "server_port": node["port"], "uuid": node["uuid"], "network": "tcp",
                           "tls": _client_tls(node)}],
            "dns": {"servers": [{"type": "https", "tag": DNS_TAG, "server": "1.1.1.1",
                                 "server_port": 443, "path": "/dns-query", "detour": RELAY_TAG,
                                 "tls": {"enabled": True, "server_name": "cloudflare-dns.com"}}],
                    "final": DNS_TAG, "strategy": "prefer_ipv4"},
            # Preserve the requested hostname in VLESS. Resolving it here
            # would send only one chosen IP and hide forbidden sibling answers
            # from the domestic relay's complete-answer guard. The DNS listener
            # is an explicit, separate facility; no application name pre-resolve.
            "route": {"rules": [{"inbound": [DNS_INBOUND], "action": "hijack-dns"},
                                {"network": "udp", "action": "reject"}], "final": RELAY_TAG},
        }
    if kind != "mihomo":
        raise StrictExportError("INVALID_STRICT_EXPORT_REQUEST")
    proxy = copy.deepcopy(node)
    proxy.update(name=RELAY_TAG, udp=False)
    proxy.pop("packet-encoding", None)
    # Fixed source: dns/doh.go and tunnel/dns_dialer.go at v1.19.32. An existing
    # named adapter is used directly; its dial error is returned, never retried
    # through DIRECT. Explicit default/proxy resolvers override core defaults.
    doh = "https://1.1.1.1/dns-query#" + RELAY_TAG + "&name-cert-verify=cloudflare-dns.com"
    return {
        "mixed-port": 7890, "allow-lan": False, "bind-address": "127.0.0.1",
        "mode": "rule", "log-level": "warning", "ipv6": True,
        "find-process-mode": "off", "geo-auto-update": False,
        "profile": {"store-selected": False, "store-fake-ip": False},
        # Override Mihomo's synthesized GLOBAL selector, which otherwise adds
        # built-in DIRECT/REJECT candidates even when no group was supplied.
        "proxies": [proxy], "proxy-groups": [{"name": "GLOBAL", "type": "select", "proxies": [RELAY_TAG]}],
        "rules": ["NETWORK,UDP,REJECT", "MATCH," + RELAY_TAG],
        "dns": {"enable": True, "listen": "127.0.0.1:1053", "ipv6": True,
                "enhanced-mode": "redir-host", "prefer-h3": False,
                "use-hosts": False, "use-system-hosts": False,
                "default-nameserver": [doh], "nameserver": [doh],
                "proxy-server-nameserver": [doh], "fallback": [],
                "nameserver-policy": {}, "proxy-server-nameserver-policy": {},
                "direct-nameserver": []},
    }


class StrictRelayExporter:
    def __init__(self, manager):
        self.manager = manager

    def export(self, kind, base_revision, inbound_id, server):
        if (kind not in {"mihomo", "sing-box"} or type(base_revision) is not str
                or not _REVISION.fullmatch(base_revision) or type(inbound_id) is not int or inbound_id <= 0):
            raise StrictExportError("INVALID_STRICT_EXPORT_REQUEST")
        server = _public_server(server)
        # Same lock as row mutations, apply/recovery, certificates and stop.
        # A newer draft is allowed but never used to construct this export.
        with self.manager.lock:
            coordinator = self.manager.proxy_coordinator
            active = coordinator.store.load_active(reject_pending=True)
            if active is None or active["settings"]["relay_enabled"] is not True:
                raise StrictExportError("STRICT_EXPORT_RELAY_NOT_ACTIVE")
            if active["revision"] != base_revision:
                raise ProxyConflict("Active revision differs")
            rows = self.manager._all_rows()
            try:
                coverage = ingress_coverage(rows)
                if not coverage or any(not item["qualified"] for item in coverage):
                    raise StrictExportError("STRICT_EXPORT_UNQUALIFIED_INGRESS")
                selected = [item for item in rows if item.id == inbound_id and item.enable is True]
                if len(selected) != 1:
                    raise StrictExportError("STRICT_EXPORT_INGRESS_NOT_FOUND")
                compiled = coordinator.compile_active(rows)
                context = active.get("runtime_context")
                if (compiled is None or revision(compiled.config) != active["core_revision"]
                        or context is None or compiled.identity != context["policy_identity"]):
                    raise StrictExportError("STRICT_EXPORT_STATE_MISMATCH")
                runtime = self.manager.get("sing-box").runtime
                state, status = runtime.state(), runtime.status()
                if (state != {"revision": active["core_revision"], "enabled": True}
                        or status.get("running") is not True or status.get("enabled") is not True
                        or status.get("binary_exists") is not True or status.get("config_exists") is not True
                        or status.get("version_required") != "1.14.2"
                        or status.get("applied_revision") != active["core_revision"]
                        or revision(runtime.active_config()) != active["core_revision"]):
                    raise StrictExportError("STRICT_EXPORT_RUNTIME_UNAVAILABLE")
                xray = self.manager.get("xray").runtime
                if xray.state().get("enabled") is not False or xray.status().get("running") is not False:
                    raise StrictExportError("STRICT_EXPORT_STATE_MISMATCH")
                node = validated_node(selected[0], server)
                config = _client_config(node, kind)
                # Recheck persisted active identity after private reads. Writers
                # outside the manager lock may save drafts, never substitute one.
                latest = coordinator.store.load_active(reject_pending=True)
                if (latest is None or latest["revision"] != base_revision
                        or latest["core_revision"] != active["core_revision"]):
                    raise ProxyConflict("Active revision changed")
            except (StrictExportError, ProxyConflict, ProxyStorageError):
                raise
            except (PolicyError, ExportError):
                raise StrictExportError("STRICT_EXPORT_UNQUALIFIED_INGRESS") from None
            except Exception:
                raise StrictExportError("STRICT_EXPORT_RUNTIME_UNAVAILABLE") from None
            if kind == "mihomo":
                return RelayExport(yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
                                   "application/yaml", "vui-strict-relay.yaml", base_revision)
            return RelayExport(json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                               "application/json", "vui-strict-relay.json", base_revision)
