from __future__ import annotations

import base64
import json
from typing import Iterable
from urllib.parse import quote, urlencode

from app.models.database import Inbound


def _first_user(item: Inbound) -> dict:
    settings = item.settings or {}
    users = settings.get("users") or settings.get("clients") or []
    return users[0] if users else {}


def _stream(item: Inbound) -> dict:
    return dict(item.stream_settings or {})


def _meta(item: Inbound) -> dict:
    return dict(_stream(item).get("_vui") or {})


def _xray_transport(item: Inbound) -> tuple[str, dict]:
    stream = _stream(item)
    method = str(
        stream.get("method")
        or stream.get("network")
        or "raw"
    ).lower()
    network = {
        "raw": "tcp",
        "tcp": "tcp",
        "websocket": "ws",
        "ws": "ws",
        "grpc": "grpc",
        "xhttp": "xhttp",
        "httpupgrade": "httpupgrade",
    }.get(method, method)
    return network, stream


def _singbox_transport(item: Inbound) -> tuple[str, dict]:
    stream = _stream(item)
    transport = dict(stream.get("transport") or {})
    transport_type = str(transport.get("type") or "direct").lower()
    network = {
        "direct": "tcp",
        "ws": "ws",
        "grpc": "grpc",
        "httpupgrade": "httpupgrade",
        "http": "h2",
    }.get(transport_type, transport_type)
    return network, transport


def _security(item: Inbound) -> tuple[str, dict]:
    stream = _stream(item)
    meta = _meta(item)
    if meta.get("security"):
        return str(meta["security"]), meta

    if item.core == "xray":
        value = str(stream.get("security") or "none")
        if value == "reality":
            reality = stream.get("realitySettings") or {}
            return value, {
                "server_name": (
                    (reality.get("serverNames") or [""])[0]
                ),
                "reality_short_id": (
                    (reality.get("shortIds") or [""])[0]
                ),
            }
        if value == "tls":
            tls = stream.get("tlsSettings") or {}
            return value, {"server_name": tls.get("serverName", "")}
        return value, {}

    tls = stream.get("tls") or {}
    if not tls.get("enabled"):
        return "none", {}
    reality = tls.get("reality") or {}
    if reality.get("enabled"):
        return "reality", {
            "server_name": tls.get("server_name", ""),
            "reality_short_id": (
                (reality.get("short_id") or [""])[0]
                if isinstance(reality.get("short_id"), list)
                else reality.get("short_id", "")
            ),
        }
    return "tls", {"server_name": tls.get("server_name", "")}


def share_link(item: Inbound, host: str) -> str | None:
    settings = item.settings or {}
    user = _first_user(item)
    meta = _meta(item)
    name = quote(item.remark or f"{item.protocol}-{item.port}")
    protocol = item.protocol

    if protocol == "vless":
        user_id = user.get("id") or user.get("uuid")
        if not user_id:
            return None
        params: dict[str, str] = {}
        security, security_meta = _security(item)
        params["security"] = security

        if item.core == "xray":
            network, stream = _xray_transport(item)
            params["type"] = network
            if network == "ws":
                ws = stream.get("wsSettings") or {}
                if ws.get("path"):
                    params["path"] = ws["path"]
                if ws.get("host"):
                    params["host"] = ws["host"]
            elif network == "grpc":
                grpc = stream.get("grpcSettings") or {}
                if grpc.get("serviceName"):
                    params["serviceName"] = grpc["serviceName"]
            elif network == "xhttp":
                xhttp = stream.get("xhttpSettings") or {}
                for source, target in (
                    ("path", "path"),
                    ("host", "host"),
                    ("mode", "mode"),
                ):
                    if xhttp.get(source):
                        params[target] = xhttp[source]
        else:
            network, transport = _singbox_transport(item)
            params["type"] = network
            if network == "ws":
                if transport.get("path"):
                    params["path"] = transport["path"]
                host_header = (transport.get("headers") or {}).get("Host")
                if host_header:
                    params["host"] = host_header
            elif network == "grpc" and transport.get("service_name"):
                params["serviceName"] = transport["service_name"]

        server_name = (
            meta.get("server_name")
            or security_meta.get("server_name")
        )
        if server_name:
            params["sni"] = server_name

        if security == "reality":
            public_key = meta.get("reality_public_key")
            short_id = (
                meta.get("reality_short_id")
                or security_meta.get("reality_short_id")
            )
            if public_key:
                params["pbk"] = public_key
            if short_id:
                params["sid"] = short_id
            params["fp"] = meta.get("client_fingerprint", "chrome")

        flow = user.get("flow")
        if flow:
            params["flow"] = flow

        return (
            f"vless://{user_id}@{host}:{item.port}"
            f"?{urlencode(params)}#{name}"
        )

    if protocol == "vmess":
        user_id = user.get("id") or user.get("uuid")
        if not user_id:
            return None
        network, stream = (
            _xray_transport(item)
            if item.core == "xray"
            else _singbox_transport(item)
        )
        security, security_meta = _security(item)
        payload = {
            "v": "2",
            "ps": item.remark or f"vmess-{item.port}",
            "add": host,
            "port": str(item.port),
            "id": user_id,
            "aid": "0",
            "scy": user.get("security", "auto"),
            "net": network,
            "type": "none",
            "host": "",
            "path": "",
            "tls": "tls" if security in {"tls", "reality"} else "",
            "sni": (
                meta.get("server_name")
                or security_meta.get("server_name")
                or ""
            ),
        }
        if item.core == "xray" and isinstance(stream, dict):
            if network == "ws":
                ws = stream.get("wsSettings") or {}
                payload["path"] = ws.get("path", "")
                payload["host"] = ws.get("host", "")
            elif network == "grpc":
                payload["path"] = (
                    (stream.get("grpcSettings") or {}).get("serviceName", "")
                )
        encoded = base64.b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode()
        return f"vmess://{encoded}"

    if protocol == "trojan":
        password = user.get("password")
        if not password:
            return None
        security, security_meta = _security(item)
        params = {"security": security}
        server_name = (
            meta.get("server_name")
            or security_meta.get("server_name")
        )
        if server_name:
            params["sni"] = server_name
        return (
            f"trojan://{quote(password)}@{host}:{item.port}"
            f"?{urlencode(params)}#{name}"
        )

    if protocol == "shadowsocks":
        method = settings.get("method")
        password = settings.get("password")
        if not method or not password:
            return None
        userinfo = base64.urlsafe_b64encode(
            f"{method}:{password}".encode()
        ).decode().rstrip("=")
        return f"ss://{userinfo}@{host}:{item.port}#{name}"

    if protocol == "hysteria2":
        password = user.get("password")
        if not password:
            return None
        params: dict[str, str] = {}
        if meta.get("server_name"):
            params["sni"] = meta["server_name"]
        if meta.get("skip_cert_verify"):
            params["insecure"] = "1"
        obfs = settings.get("obfs") or {}
        if obfs.get("type"):
            params["obfs"] = obfs["type"]
        if obfs.get("password"):
            params["obfs-password"] = obfs["password"]
        return (
            f"hysteria2://{quote(password)}@{host}:{item.port}"
            f"?{urlencode(params)}#{name}"
        )

    if protocol == "tuic":
        user_id = user.get("uuid")
        password = user.get("password")
        if not user_id or not password:
            return None
        params: dict[str, str] = {}
        if meta.get("server_name"):
            params["sni"] = meta["server_name"]
        if meta.get("udp_relay_mode"):
            params["udp_relay_mode"] = meta["udp_relay_mode"]
        if settings.get("congestion_control"):
            params["congestion_control"] = settings["congestion_control"]
        return (
            f"tuic://{user_id}:{quote(password)}@{host}:{item.port}"
            f"?{urlencode(params)}#{name}"
        )

    return None


def raw_links(inbounds: Iterable[Inbound], host: str) -> list[str]:
    return [
        link
        for item in inbounds
        if item.enable
        for link in [share_link(item, host)]
        if link
    ]


def base64_subscription(inbounds: Iterable[Inbound], host: str) -> str:
    payload = "\n".join(raw_links(inbounds, host))
    return base64.b64encode(payload.encode()).decode()


def mihomo_proxy(item: Inbound, host: str) -> dict | None:
    settings = item.settings or {}
    user = _first_user(item)
    meta = _meta(item)
    name = item.remark or f"{item.protocol}-{item.port}"
    common = {"name": name, "server": host, "port": item.port}
    protocol = item.protocol

    if protocol == "vless":
        user_id = user.get("id") or user.get("uuid")
        if not user_id:
            return None
        proxy = {
            **common,
            "type": "vless",
            "uuid": user_id,
            "udp": True,
            "encryption": "",
        }
        if user.get("flow"):
            proxy["flow"] = user["flow"]

        if item.core == "xray":
            network, stream = _xray_transport(item)
        else:
            network, stream = _singbox_transport(item)
        proxy["network"] = network

        if network == "ws":
            if item.core == "xray":
                value = stream.get("wsSettings") or {}
                headers = {}
                if value.get("host"):
                    headers["Host"] = value["host"]
                proxy["ws-opts"] = {
                    "path": value.get("path", "/"),
                    "headers": headers,
                }
            else:
                proxy["ws-opts"] = {
                    "path": stream.get("path", "/"),
                    "headers": stream.get("headers") or {},
                }
        elif network == "grpc":
            service_name = (
                (stream.get("grpcSettings") or {}).get("serviceName", "")
                if item.core == "xray"
                else stream.get("service_name", "")
            )
            proxy["grpc-opts"] = {"grpc-service-name": service_name}
        elif network == "xhttp":
            value = stream.get("xhttpSettings") or {}
            proxy["xhttp-opts"] = {
                key: value[source]
                for source, key in (
                    ("path", "path"),
                    ("host", "host"),
                    ("mode", "mode"),
                )
                if value.get(source)
            }

        security, security_meta = _security(item)
        if security in {"tls", "reality"}:
            proxy["tls"] = True
            proxy["servername"] = (
                meta.get("server_name")
                or security_meta.get("server_name")
                or host
            )
        if security == "reality":
            public_key = meta.get("reality_public_key")
            short_id = (
                meta.get("reality_short_id")
                or security_meta.get("reality_short_id")
            )
            if public_key:
                proxy["reality-opts"] = {
                    "public-key": public_key,
                    "short-id": short_id or "",
                }
            proxy["client-fingerprint"] = meta.get(
                "client_fingerprint",
                "chrome",
            )
        if meta.get("skip_cert_verify"):
            proxy["skip-cert-verify"] = True
        return proxy

    if protocol == "vmess":
        user_id = user.get("id") or user.get("uuid")
        if not user_id:
            return None
        network, stream = (
            _xray_transport(item)
            if item.core == "xray"
            else _singbox_transport(item)
        )
        proxy = {
            **common,
            "type": "vmess",
            "uuid": user_id,
            "alterId": 0,
            "cipher": user.get("security", "auto"),
            "udp": True,
            "network": network,
        }
        security, security_meta = _security(item)
        if security == "tls":
            proxy["tls"] = True
            proxy["servername"] = (
                meta.get("server_name")
                or security_meta.get("server_name")
                or host
            )
        if network == "ws":
            if item.core == "xray":
                value = stream.get("wsSettings") or {}
                proxy["ws-opts"] = {
                    "path": value.get("path", "/"),
                    "headers": (
                        {"Host": value["host"]}
                        if value.get("host")
                        else {}
                    ),
                }
            else:
                proxy["ws-opts"] = {
                    "path": stream.get("path", "/"),
                    "headers": stream.get("headers") or {},
                }
        elif network == "grpc":
            service_name = (
                (stream.get("grpcSettings") or {}).get("serviceName", "")
                if item.core == "xray"
                else stream.get("service_name", "")
            )
            proxy["grpc-opts"] = {"grpc-service-name": service_name}
        return proxy

    if protocol == "trojan":
        password = user.get("password")
        if not password:
            return None
        proxy = {
            **common,
            "type": "trojan",
            "password": password,
            "udp": True,
        }
        security, security_meta = _security(item)
        server_name = (
            meta.get("server_name")
            or security_meta.get("server_name")
        )
        if server_name:
            proxy["sni"] = server_name
        if meta.get("skip_cert_verify"):
            proxy["skip-cert-verify"] = True
        return proxy

    if protocol == "shadowsocks":
        if not settings.get("method") or not settings.get("password"):
            return None
        return {
            **common,
            "type": "ss",
            "cipher": settings["method"],
            "password": settings["password"],
            "udp": True,
        }

    if protocol == "hysteria2":
        password = user.get("password")
        if not password:
            return None
        proxy = {
            **common,
            "type": "hysteria2",
            "password": password,
        }
        if settings.get("up_mbps") is not None:
            proxy["up"] = f"{settings['up_mbps']} Mbps"
        if settings.get("down_mbps") is not None:
            proxy["down"] = f"{settings['down_mbps']} Mbps"
        obfs = settings.get("obfs") or {}
        if obfs.get("type"):
            proxy["obfs"] = obfs["type"]
        if obfs.get("password"):
            proxy["obfs-password"] = obfs["password"]
        if meta.get("server_name"):
            proxy["sni"] = meta["server_name"]
        if meta.get("skip_cert_verify"):
            proxy["skip-cert-verify"] = True
        return proxy

    if protocol == "tuic":
        user_id = user.get("uuid")
        password = user.get("password")
        if not user_id or not password:
            return None
        proxy = {
            **common,
            "type": "tuic",
            "uuid": user_id,
            "password": password,
            "udp-relay-mode": meta.get("udp_relay_mode", "native"),
            "congestion-controller": settings.get(
                "congestion_control",
                "bbr",
            ),
            "reduce-rtt": bool(settings.get("zero_rtt_handshake", False)),
        }
        if meta.get("server_name"):
            proxy["sni"] = meta["server_name"]
        if meta.get("skip_cert_verify"):
            proxy["skip-cert-verify"] = True
        return proxy

    return None


def export_warnings(inbounds: Iterable[Inbound]) -> list[dict]:
    warnings = []
    for item in inbounds:
        meta = _meta(item)
        if (
            item.core == "xray"
            and item.protocol == "vless"
            and meta.get("security") == "reality"
        ):
            warnings.append({
                "inbound_id": item.id,
                "remark": item.remark,
                "code": "XRAY_REALITY_MIHOMO_COMPATIBILITY",
                "message": (
                    "Current Mihomo documentation warns that Xray-core "
                    "v26.7.11+ REALITY is intentionally incompatible. "
                    "Prefer sing-box REALITY for a Mihomo-facing node."
                ),
            })
    return warnings


def _client_tls(item: Inbound, host: str) -> dict | None:
    security, security_meta = _security(item)
    meta = _meta(item)
    if security == "none":
        return None
    tls = {
        "enabled": True,
        "server_name": (
            meta.get("server_name")
            or security_meta.get("server_name")
            or host
        ),
    }
    if meta.get("skip_cert_verify"):
        tls["insecure"] = True
    if security == "reality" and meta.get("reality_public_key"):
        tls["utls"] = {
            "enabled": True,
            "fingerprint": meta.get("client_fingerprint", "chrome"),
        }
        tls["reality"] = {
            "enabled": True,
            "public_key": meta["reality_public_key"],
            "short_id": meta.get("reality_short_id", ""),
        }
    return tls


def singbox_client_config(inbounds: Iterable[Inbound], host: str) -> dict:
    outbounds = []
    tags = []

    for item in inbounds:
        if not item.enable:
            continue
        settings = item.settings or {}
        user = _first_user(item)
        meta = _meta(item)
        tag = item.remark or f"{item.protocol}-{item.port}"
        outbound: dict | None = None

        if item.protocol == "vless":
            user_id = user.get("id") or user.get("uuid")
            if user_id:
                outbound = {
                    "type": "vless",
                    "tag": tag,
                    "server": host,
                    "server_port": item.port,
                    "uuid": user_id,
                    "packet_encoding": "xudp",
                }
                if user.get("flow"):
                    outbound["flow"] = user["flow"]

        elif item.protocol == "vmess":
            user_id = user.get("id") or user.get("uuid")
            if user_id:
                outbound = {
                    "type": "vmess",
                    "tag": tag,
                    "server": host,
                    "server_port": item.port,
                    "uuid": user_id,
                    "security": user.get("security", "auto"),
                    "alter_id": 0,
                }

        elif item.protocol == "trojan":
            if user.get("password"):
                outbound = {
                    "type": "trojan",
                    "tag": tag,
                    "server": host,
                    "server_port": item.port,
                    "password": user["password"],
                }

        elif item.protocol == "shadowsocks":
            if settings.get("method") and settings.get("password"):
                outbound = {
                    "type": "shadowsocks",
                    "tag": tag,
                    "server": host,
                    "server_port": item.port,
                    "method": settings["method"],
                    "password": settings["password"],
                }

        elif item.protocol == "hysteria2":
            if user.get("password"):
                outbound = {
                    "type": "hysteria2",
                    "tag": tag,
                    "server": host,
                    "server_port": item.port,
                    "password": user["password"],
                }
                if settings.get("up_mbps") is not None:
                    outbound["up_mbps"] = settings["up_mbps"]
                if settings.get("down_mbps") is not None:
                    outbound["down_mbps"] = settings["down_mbps"]
                if settings.get("obfs"):
                    outbound["obfs"] = settings["obfs"]

        elif item.protocol == "tuic":
            if user.get("uuid") and user.get("password"):
                outbound = {
                    "type": "tuic",
                    "tag": tag,
                    "server": host,
                    "server_port": item.port,
                    "uuid": user["uuid"],
                    "password": user["password"],
                    "congestion_control": settings.get(
                        "congestion_control",
                        "bbr",
                    ),
                    "udp_relay_mode": meta.get(
                        "udp_relay_mode",
                        "native",
                    ),
                    "zero_rtt_handshake": bool(
                        settings.get("zero_rtt_handshake", False)
                    ),
                }

        if not outbound:
            continue

        tls = _client_tls(item, host)
        if tls:
            outbound["tls"] = tls

        stream = _stream(item)
        if item.core == "sing-box" and stream.get("transport"):
            outbound["transport"] = stream["transport"]
        elif item.core == "xray":
            network, xray_stream = _xray_transport(item)
            if network == "ws":
                ws = xray_stream.get("wsSettings") or {}
                transport = {
                    "type": "ws",
                    "path": ws.get("path", "/"),
                }
                if ws.get("host"):
                    transport["headers"] = {"Host": ws["host"]}
                outbound["transport"] = transport
            elif network == "grpc":
                outbound["transport"] = {
                    "type": "grpc",
                    "service_name": (
                        xray_stream.get("grpcSettings") or {}
                    ).get("serviceName", ""),
                }

        outbounds.append(outbound)
        tags.append(tag)

    if not tags:
        outbounds.append({"type": "direct", "tag": "direct"})

    return {
        "log": {"level": "info"},
        "outbounds": outbounds,
        "route": {"final": tags[0] if tags else "direct"},
    }
