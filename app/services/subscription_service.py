from __future__ import annotations

import base64
import json
from typing import Iterable
from urllib.parse import quote, urlencode

from app.models.database import Inbound

try:
    import yaml
except ImportError:
    yaml = None


def _first(mapping: dict, key: str) -> dict:
    values = mapping.get(key) or []
    return values[0] if values else {}


def _stream(item: Inbound) -> dict:
    return item.stream_settings or {}


def _security_params(item: Inbound) -> dict:
    stream = _stream(item)
    params: dict[str, str] = {}
    security = stream.get("security")
    if security:
        params["security"] = security
    network = stream.get("network")
    if network:
        params["type"] = network

    reality = stream.get("realitySettings") or stream.get("reality") or {}
    if reality:
        params["security"] = "reality"
        if reality.get("serverName"):
            params["sni"] = reality["serverName"]
        if reality.get("publicKey"):
            params["pbk"] = reality["publicKey"]
        if reality.get("shortId"):
            params["sid"] = reality["shortId"]
        if reality.get("fingerprint"):
            params["fp"] = reality["fingerprint"]

    tls = stream.get("tls") or {}
    if tls.get("enabled"):
        params["security"] = "tls"
        if tls.get("server_name"):
            params["sni"] = tls["server_name"]

    return params


def share_link(item: Inbound, host: str) -> str | None:
    settings = item.settings or {}
    name = quote(item.remark or f"{item.protocol}-{item.port}")
    protocol = item.protocol

    if protocol == "vless":
        user = _first(settings, "clients") if item.core == "xray" else _first(settings, "users")
        user_id = user.get("id") or user.get("uuid")
        if not user_id:
            return None
        params = _security_params(item)
        flow = user.get("flow")
        if flow:
            params["flow"] = flow
        query = urlencode(params)
        suffix = f"?{query}" if query else ""
        return f"vless://{user_id}@{host}:{item.port}{suffix}#{name}"

    if protocol == "vmess":
        user = _first(settings, "clients") if item.core == "xray" else _first(settings, "users")
        user_id = user.get("id") or user.get("uuid")
        if not user_id:
            return None
        stream = _stream(item)
        payload = {
            "v": "2",
            "ps": item.remark or f"vmess-{item.port}",
            "add": host,
            "port": str(item.port),
            "id": user_id,
            "aid": str(user.get("alterId", 0)),
            "scy": user.get("security", "auto"),
            "net": stream.get("network", "tcp"),
            "type": "none",
            "host": "",
            "path": "",
            "tls": stream.get("security", ""),
            "sni": "",
        }
        encoded = base64.b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode()
        return f"vmess://{encoded}"

    if protocol == "trojan":
        user = _first(settings, "clients") if item.core == "xray" else _first(settings, "users")
        password = user.get("password")
        if not password:
            return None
        params = _security_params(item)
        query = urlencode(params)
        suffix = f"?{query}" if query else ""
        return f"trojan://{quote(password)}@{host}:{item.port}{suffix}#{name}"

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
        user = _first(settings, "users")
        password = user.get("password")
        if not password:
            return None
        params = {}
        tls = _stream(item).get("tls") or {}
        if tls.get("server_name"):
            params["sni"] = tls["server_name"]
        query = urlencode(params)
        suffix = f"?{query}" if query else ""
        return f"hysteria2://{quote(password)}@{host}:{item.port}{suffix}#{name}"

    if protocol == "tuic":
        user = _first(settings, "users")
        user_id = user.get("uuid")
        password = user.get("password")
        if not user_id or not password:
            return None
        params = {}
        tls = _stream(item).get("tls") or {}
        if tls.get("server_name"):
            params["sni"] = tls["server_name"]
        query = urlencode(params)
        suffix = f"?{query}" if query else ""
        return f"tuic://{user_id}:{quote(password)}@{host}:{item.port}{suffix}#{name}"

    return None


def raw_links(inbounds: Iterable[Inbound], host: str) -> list[str]:
    result = []
    for item in inbounds:
        if not item.enable:
            continue
        link = share_link(item, host)
        if link:
            result.append(link)
    return result


def base64_subscription(inbounds: Iterable[Inbound], host: str) -> str:
    payload = "\n".join(raw_links(inbounds, host))
    return base64.b64encode(payload.encode()).decode()


def _mihomo_proxy(item: Inbound, host: str) -> dict | None:
    settings = item.settings or {}
    stream = _stream(item)
    name = item.remark or f"{item.protocol}-{item.port}"
    common = {"name": name, "server": host, "port": item.port}
    protocol = item.protocol

    if protocol == "vless":
        user = _first(settings, "clients") if item.core == "xray" else _first(settings, "users")
        user_id = user.get("id") or user.get("uuid")
        if not user_id:
            return None
        proxy = {**common, "type": "vless", "uuid": user_id, "udp": True}
        if user.get("flow"):
            proxy["flow"] = user["flow"]

        proxy["network"] = stream.get("network") or "tcp"
        security = stream.get("security")
        reality = stream.get("realitySettings") or stream.get("reality") or {}
        tls = stream.get("tls") or {}
        if security in {"tls", "reality"} or tls.get("enabled") or reality:
            proxy["tls"] = True
        if reality:
            proxy["servername"] = reality.get("serverName") or host
            proxy["client-fingerprint"] = reality.get("fingerprint", "chrome")
            proxy["reality-opts"] = {
                key: value
                for key, value in {
                    "public-key": reality.get("publicKey"),
                    "short-id": reality.get("shortId"),
                }.items()
                if value
            }
        elif tls.get("enabled"):
            proxy["servername"] = tls.get("server_name") or host
        return proxy

    if protocol == "vmess":
        user = _first(settings, "clients") if item.core == "xray" else _first(settings, "users")
        user_id = user.get("id") or user.get("uuid")
        if not user_id:
            return None
        proxy = {
            **common,
            "type": "vmess",
            "uuid": user_id,
            "alterId": int(user.get("alterId", 0)),
            "cipher": user.get("security", "auto"),
            "udp": True,
            "network": stream.get("network", "tcp"),
        }
        if stream.get("security") == "tls":
            proxy["tls"] = True
        return proxy

    if protocol == "trojan":
        user = _first(settings, "clients") if item.core == "xray" else _first(settings, "users")
        if not user.get("password"):
            return None
        proxy = {**common, "type": "trojan", "password": user["password"], "udp": True}
        params = _security_params(item)
        if params.get("sni"):
            proxy["sni"] = params["sni"]
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
        user = _first(settings, "users")
        if not user.get("password"):
            return None
        proxy = {**common, "type": "hysteria2", "password": user["password"]}
        tls = stream.get("tls") or {}
        if tls.get("server_name"):
            proxy["sni"] = tls["server_name"]
        if tls.get("insecure"):
            proxy["skip-cert-verify"] = True
        return proxy

    if protocol == "tuic":
        user = _first(settings, "users")
        if not user.get("uuid") or not user.get("password"):
            return None
        proxy = {
            **common,
            "type": "tuic",
            "uuid": user["uuid"],
            "password": user["password"],
            "udp-relay-mode": settings.get("udp_relay_mode", "native"),
            "congestion-controller": settings.get("congestion_control", "bbr"),
        }
        tls = stream.get("tls") or {}
        if tls.get("server_name"):
            proxy["sni"] = tls["server_name"]
        if tls.get("insecure"):
            proxy["skip-cert-verify"] = True
        return proxy

    return None


def mihomo_config(inbounds: Iterable[Inbound], host: str) -> str:
    proxies = []
    for item in inbounds:
        if not item.enable:
            continue
        proxy = _mihomo_proxy(item, host)
        if proxy:
            proxies.append(proxy)

    names = [proxy["name"] for proxy in proxies]
    config = {
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "info",
        "proxies": proxies,
        "proxy-groups": [
            {
                "name": "PROXY",
                "type": "select",
                "proxies": names or ["DIRECT"],
            }
        ],
        "rules": ["MATCH,PROXY"],
    }

    if yaml is not None:
        return yaml.safe_dump(
            config,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    return json.dumps(config, ensure_ascii=False, indent=2)


def singbox_client_config(inbounds: Iterable[Inbound], host: str) -> dict:
    outbounds = []
    tags = []

    for item in inbounds:
        if not item.enable:
            continue
        settings = item.settings or {}
        stream = _stream(item)
        tag = item.remark or f"{item.protocol}-{item.port}"
        outbound: dict | None = None

        if item.protocol == "vless":
            user = _first(settings, "clients") if item.core == "xray" else _first(settings, "users")
            user_id = user.get("id") or user.get("uuid")
            if user_id:
                outbound = {
                    "type": "vless",
                    "tag": tag,
                    "server": host,
                    "server_port": item.port,
                    "uuid": user_id,
                }
                if user.get("flow"):
                    outbound["flow"] = user["flow"]

        elif item.protocol == "vmess":
            user = _first(settings, "clients") if item.core == "xray" else _first(settings, "users")
            user_id = user.get("id") or user.get("uuid")
            if user_id:
                outbound = {
                    "type": "vmess",
                    "tag": tag,
                    "server": host,
                    "server_port": item.port,
                    "uuid": user_id,
                    "security": user.get("security", "auto"),
                    "alter_id": int(user.get("alterId", 0)),
                }

        elif item.protocol == "trojan":
            user = _first(settings, "clients") if item.core == "xray" else _first(settings, "users")
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
            user = _first(settings, "users")
            if user.get("password"):
                outbound = {
                    "type": "hysteria2",
                    "tag": tag,
                    "server": host,
                    "server_port": item.port,
                    "password": user["password"],
                }

        elif item.protocol == "tuic":
            user = _first(settings, "users")
            if user.get("uuid") and user.get("password"):
                outbound = {
                    "type": "tuic",
                    "tag": tag,
                    "server": host,
                    "server_port": item.port,
                    "uuid": user["uuid"],
                    "password": user["password"],
                }

        if outbound:
            tls = stream.get("tls") or {}
            reality = stream.get("realitySettings") or stream.get("reality") or {}
            if tls.get("enabled") or reality:
                outbound["tls"] = {
                    "enabled": True,
                    "server_name": (
                        tls.get("server_name")
                        or reality.get("serverName")
                        or host
                    ),
                }
                if tls.get("insecure"):
                    outbound["tls"]["insecure"] = True
                if reality:
                    outbound["tls"]["utls"] = {
                        "enabled": True,
                        "fingerprint": reality.get("fingerprint", "chrome"),
                    }
                    outbound["tls"]["reality"] = {
                        "enabled": True,
                        "public_key": reality.get("publicKey", ""),
                        "short_id": reality.get("shortId", ""),
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
