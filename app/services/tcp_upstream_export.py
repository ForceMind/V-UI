"""Explicit TCP-only upstream artifacts, separate from ordinary client exports.

The administrator authorizes this purpose at the API boundary. We validate the
stored inbound using the existing qualified exporter, then construct a fresh
UpstreamProfile. This is never a converter that strips UDP options from an
ordinary share, subscription, or user-supplied document. No DNS or runtime work
is performed here; the importing instance still enforces its application policy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

import yaml

from app.services.upstream_profile import (
    TLSProfile, UpstreamProfile, literal_string, server_host,
)
from app.services.validated_export import node_host, validated_node

PROFILE = "tcp-upstream-v1"
INVALID_REQUEST = "INVALID_TCP_UPSTREAM_EXPORT_REQUEST"
INELIGIBLE = "TCP_UPSTREAM_EXPORT_INELIGIBLE"


class TCPUpstreamExportError(ValueError):
    """Only fixed, non-secret codes may cross the service boundary."""
    def __init__(self, code=INELIGIBLE):
        self.code = INVALID_REQUEST if code == INVALID_REQUEST else INELIGIBLE
        super().__init__(self.code)


@dataclass(frozen=True)
class TCPUpstreamExport:
    content: str = field(repr=False)
    media_type: str
    filename: str
    profile: str = PROFILE


def tcp_upstream_profile(item, server: str) -> UpstreamProfile:
    """Qualify one stored inbound and preserve every supported client field."""
    try:
        # Validate the literal first: normalize_server alone accepts trimming,
        # bracketed hosts, and IDNA, which this explicit request does not allow.
        server = server_host(server)
        # Reuse the established rejection of unspecified connection addresses,
        # but deliberately do not substitute its normalized return value.
        node_host(server)
        # URI hostname parsing lowercases ASCII DNS/IPv6 hosts. Apply precisely
        # that host-only representation rule to both formats, without trimming,
        # IDNA conversion, IPv6 compression, or any change to the TLS SNI.
        server = server.lower()
    except (ValueError, TypeError, UnicodeError):
        raise TCPUpstreamExportError(INVALID_REQUEST) from None
    try:
        if (item.enable is not True or type(item.expiry_time) is not int
                or item.expiry_time != 0 or item.core != "sing-box"
                or item.protocol != "vless"):
            raise TCPUpstreamExportError()
        stream = item.stream_settings
        if type(stream) is not dict:
            raise TCPUpstreamExportError()
        # Even separately qualified WS/gRPC client profiles are not upstreams.
        if "transport" in stream and (type(stream["transport"]) is not dict
                                      or stream["transport"] != {}):
            raise TCPUpstreamExportError()
        node = validated_node(item, server)
        tls, meta = stream["tls"], stream.get("_vui", {})
        user = item.settings["users"][0]
        if "name" in user:
            literal_string(user["name"])
        # The legacy validator intentionally has looser optional-field handling.
        # Reject ambiguous null/coerced metadata rather than erasing it here.
        if "server_name" in meta and meta["server_name"] != tls["server_name"]:
            raise TCPUpstreamExportError()
        if "server_name" in meta:
            literal_string(meta["server_name"], allow_empty=False)
        if "alpn" in tls and type(tls["alpn"]) is not list:
            raise TCPUpstreamExportError()
        for path in ("certificate_path", "key_path"):
            if path in tls:
                literal_string(tls[path], allow_empty=False)
        if item.remark is not None and type(item.remark) is not str:
            raise TCPUpstreamExportError()
        reality = node.get("reality-opts")
        security = TLSProfile(
            mode="reality" if reality is not None else "tls",
            server_name=tls["server_name"],
            alpn=tuple(tls.get("alpn", [])),
            fingerprint=meta.get("client_fingerprint", ""),
            public_key=reality["public-key"] if reality is not None else "",
            short_id=reality["short-id"] if reality is not None else "",
        )
        return UpstreamProfile(
            name=node["name"], server=server, server_port=item.port,
            uuid=user["uuid"], tls=security, flow=user.get("flow", ""),
        )
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        # Neither invalid persisted values nor validator exception text is public.
        raise TCPUpstreamExportError() from None


def export_tcp_upstream(item, server: str, output_format: str) -> TCPUpstreamExport:
    if type(output_format) is not str or output_format not in {"uri", "clash-yaml"}:
        raise TCPUpstreamExportError(INVALID_REQUEST)
    value = tcp_upstream_profile(item, server)
    if output_format == "uri":
        host = f"[{value.server}]" if ":" in value.server else value.server
        params = {"security": value.tls.mode, "type": value.transport,
                  "sni": value.tls.server_name, "encryption": value.encryption}
        if value.flow:
            params["flow"] = value.flow
        if value.tls.fingerprint:
            params["fp"] = value.tls.fingerprint
        if value.tls.alpn:
            params["alpn"] = ",".join(value.tls.alpn)
        if value.tls.mode == "reality":
            params.update(pbk=value.tls.public_key, sid=value.tls.short_id)
        # URI omission does not itself disable UDP in arbitrary clients. The
        # receiving V-UI profile and its network:tcp/reject policy enforce scope.
        content = (f"vless://{value.uuid}@{host}:{value.server_port}?"
                   f"{urlencode(params)}#{quote(value.name, safe='')}")
        return TCPUpstreamExport(content, "text/plain", "upstream-tcp.txt")
    node = {"name": value.name, "type": value.protocol, "server": value.server,
            "port": value.server_port, "uuid": value.uuid, "network": value.transport,
            "tls": True, "servername": value.tls.server_name, "udp": False,
            "skip-cert-verify": False}
    if value.flow:
        node["flow"] = value.flow
    if value.tls.fingerprint:
        node["client-fingerprint"] = value.tls.fingerprint
    if value.tls.alpn:
        node["alpn"] = list(value.tls.alpn)
    if value.tls.mode == "reality":
        node["reality-opts"] = {"public-key": value.tls.public_key, "short-id": value.tls.short_id}
    return TCPUpstreamExport(yaml.safe_dump(node, allow_unicode=True, sort_keys=False),
                             "application/yaml", "upstream-tcp.yaml")
