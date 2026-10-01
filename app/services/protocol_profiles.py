from __future__ import annotations

import base64
import secrets
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from fastapi import HTTPException


def profile_catalog() -> dict[str, Any]:
    return {
        "xray": {
            "vless": {
                "security": ["none", "tls", "reality"],
                "transport": ["raw", "websocket", "grpc", "xhttp"],
                "flow": ["", "xtls-rprx-vision"],
                "mihomo_reality_warning": True,
            },
            "vmess": {
                "security": ["none", "tls"],
                "transport": ["raw", "websocket", "grpc"],
            },
            "trojan": {
                "security": ["tls"],
                "transport": ["raw", "websocket", "grpc"],
            },
            "shadowsocks": {
                "security": ["none"],
                "transport": ["raw"],
            },
        },
        "sing-box": {
            "vless": {
                "security": ["none", "tls", "reality"],
                "transport": ["direct", "ws", "grpc", "httpupgrade"],
                "flow": ["", "xtls-rprx-vision"],
            },
            "vmess": {
                "security": ["none", "tls"],
                "transport": ["direct", "ws", "grpc", "httpupgrade"],
            },
            "trojan": {
                "security": ["tls"],
                "transport": ["direct", "ws", "grpc", "httpupgrade"],
            },
            "shadowsocks": {
                "security": ["none"],
                "transport": ["direct"],
            },
            "hysteria2": {
                "security": ["tls"],
                "transport": ["quic"],
                "obfs": ["", "salamander", "gecko"],
            },
            "tuic": {
                "security": ["tls"],
                "transport": ["quic"],
                "congestion_control": ["cubic", "new_reno", "bbr"],
                "udp_relay_mode": ["native", "quic"],
            },
        },
    }


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def generate_reality_keypair() -> tuple[str, str]:
    private = X25519PrivateKey.generate()
    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _b64url(private_raw), _b64url(public_raw)


def _required(profile: dict, key: str, label: str | None = None) -> str:
    value = str(profile.get(key) or "").strip()
    if not value:
        raise HTTPException(
            status_code=422,
            detail=f"{label or key} is required",
        )
    return value


def _target_parts(target: str) -> tuple[str, int]:
    target = target.strip()
    if target.startswith("[") and "]:" in target:
        host, port_text = target[1:].rsplit("]:", 1)
    elif ":" in target:
        host, port_text = target.rsplit(":", 1)
    else:
        host, port_text = target, "443"
    try:
        port = int(port_text)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Reality target port must be numeric",
        ) from exc
    if not host or not 1 <= port <= 65535:
        raise HTTPException(status_code=422, detail="Invalid Reality target")
    return host, port


def _certificate_paths(profile: dict) -> tuple[str, str]:
    certificate_path = _required(
        profile,
        "certificate_path",
        "TLS certificate path",
    )
    key_path = _required(profile, "key_path", "TLS private key path")
    return certificate_path, key_path


def _common_client_metadata(profile: dict) -> dict:
    result = {
        "server_name": str(profile.get("server_name") or "").strip(),
        "client_fingerprint": str(
            profile.get("client_fingerprint") or "chrome"
        ).strip(),
        "skip_cert_verify": bool(profile.get("skip_cert_verify", False)),
    }
    return {key: value for key, value in result.items() if value != ""}


def _apply_xray_transport(stream: dict, profile: dict) -> None:
    transport = str(profile.get("transport") or "raw").lower()
    if transport not in {"raw", "websocket", "grpc", "xhttp"}:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported Xray transport: {transport}",
        )
    stream["method"] = transport

    if transport == "websocket":
        ws = {"path": str(profile.get("path") or "/")}
        host = str(profile.get("host") or "").strip()
        if host:
            ws["host"] = host
        stream["wsSettings"] = ws
    elif transport == "grpc":
        stream["grpcSettings"] = {
            "serviceName": str(profile.get("service_name") or "")
        }
    elif transport == "xhttp":
        xhttp = {
            "path": str(profile.get("path") or "/"),
            "mode": str(profile.get("xhttp_mode") or "auto"),
        }
        host = str(profile.get("host") or "").strip()
        if host:
            xhttp["host"] = host
        stream["xhttpSettings"] = xhttp
    else:
        stream["rawSettings"] = {"header": {"type": "none"}}


def _apply_singbox_transport(stream: dict, profile: dict) -> None:
    transport = str(profile.get("transport") or "direct").lower()
    if transport == "direct":
        stream.pop("transport", None)
        return
    if transport == "ws":
        value: dict[str, Any] = {
            "type": "ws",
            "path": str(profile.get("path") or "/"),
        }
        host = str(profile.get("host") or "").strip()
        if host:
            value["headers"] = {"Host": host}
        stream["transport"] = value
        return
    if transport == "grpc":
        stream["transport"] = {
            "type": "grpc",
            "service_name": str(profile.get("service_name") or ""),
        }
        return
    if transport == "httpupgrade":
        stream["transport"] = {
            "type": "httpupgrade",
            "host": str(profile.get("host") or ""),
            "path": str(profile.get("path") or "/"),
        }
        return
    raise HTTPException(
        status_code=422,
        detail=f"Unsupported sing-box transport: {transport}",
    )


def _apply_xray_security(
    protocol: str,
    settings: dict,
    stream: dict,
    profile: dict,
) -> None:
    security = str(profile.get("security") or "none").lower()
    if protocol == "trojan" and security != "tls":
        raise HTTPException(
            status_code=422,
            detail="Public Trojan should use TLS",
        )

    if security == "none":
        stream["security"] = "none"
        return

    if security == "tls":
        certificate_path, key_path = _certificate_paths(profile)
        stream["security"] = "tls"
        stream["tlsSettings"] = {
            "serverName": str(profile.get("server_name") or ""),
            "certificates": [{
                "certificateFile": certificate_path,
                "keyFile": key_path,
            }],
        }
        stream["_vui"] = {
            **_common_client_metadata(profile),
            "security": "tls",
        }
        return

    if security == "reality":
        if protocol != "vless":
            raise HTTPException(
                status_code=422,
                detail="V-UI visual REALITY profile currently targets VLESS",
            )
        method = stream.get("method", "raw")
        if method not in {"raw", "xhttp", "grpc"}:
            raise HTTPException(
                status_code=422,
                detail="Xray REALITY only supports RAW, XHTTP or gRPC here",
            )
        target = _required(profile, "reality_target", "Reality target")
        server_name = (
            str(profile.get("reality_server_name") or "").strip()
            or _target_parts(target)[0]
        )
        short_id = (
            str(profile.get("reality_short_id") or "").strip()
            or secrets.token_hex(8)
        )
        if len(short_id) > 16 or len(short_id) % 2 != 0:
            raise HTTPException(
                status_code=422,
                detail="Reality short ID must be even-length hex up to 16 characters",
            )
        try:
            int(short_id or "0", 16)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Reality short ID must be hexadecimal",
            ) from exc

        private_key = str(profile.get("reality_private_key") or "").strip()
        public_key = str(profile.get("reality_public_key") or "").strip()
        if not private_key or not public_key:
            private_key, public_key = generate_reality_keypair()

        stream["security"] = "reality"
        stream["realitySettings"] = {
            "show": False,
            "target": target,
            "serverNames": [server_name],
            "privateKey": private_key,
            "shortIds": [short_id],
        }
        stream["_vui"] = {
            "security": "reality",
            "server_name": server_name,
            "client_fingerprint": str(
                profile.get("client_fingerprint") or "chrome"
            ),
            "reality_public_key": public_key,
            "reality_short_id": short_id,
            "mihomo_compatibility": "xray-v26.7.11-plus-risk",
        }
        return

    raise HTTPException(
        status_code=422,
        detail=f"Unsupported Xray security: {security}",
    )


def _apply_singbox_tls(
    protocol: str,
    stream: dict,
    profile: dict,
) -> None:
    security = str(
        profile.get("security")
        or ("tls" if protocol in {"hysteria2", "tuic", "trojan"} else "none")
    ).lower()

    if security == "none":
        if protocol in {"hysteria2", "tuic", "trojan"}:
            raise HTTPException(
                status_code=422,
                detail=f"{protocol} requires TLS in the visual profile",
            )
        stream.pop("tls", None)
        return

    if security == "tls":
        certificate_path, key_path = _certificate_paths(profile)
        server_name = str(profile.get("server_name") or "").strip()
        tls = {
            "enabled": True,
            "certificate_path": certificate_path,
            "key_path": key_path,
        }
        if server_name:
            tls["server_name"] = server_name
        stream["tls"] = tls
        stream["_vui"] = {
            **_common_client_metadata(profile),
            "security": "tls",
        }
        return

    if security == "reality":
        if protocol != "vless":
            raise HTTPException(
                status_code=422,
                detail="V-UI visual sing-box REALITY profile currently targets VLESS",
            )
        target = _required(profile, "reality_target", "Reality handshake target")
        server, server_port = _target_parts(target)
        server_name = (
            str(profile.get("reality_server_name") or "").strip()
            or server
        )
        short_id = (
            str(profile.get("reality_short_id") or "").strip()
            or secrets.token_hex(8)
        )
        if len(short_id) > 16:
            raise HTTPException(
                status_code=422,
                detail="Reality short ID must be at most 16 hexadecimal characters",
            )
        try:
            int(short_id or "0", 16)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Reality short ID must be hexadecimal",
            ) from exc

        private_key = str(profile.get("reality_private_key") or "").strip()
        public_key = str(profile.get("reality_public_key") or "").strip()
        if not private_key or not public_key:
            private_key, public_key = generate_reality_keypair()

        stream["tls"] = {
            "enabled": True,
            "server_name": server_name,
            "reality": {
                "enabled": True,
                "handshake": {
                    "server": server,
                    "server_port": server_port,
                },
                "private_key": private_key,
                "short_id": [short_id],
            },
        }
        stream["_vui"] = {
            "security": "reality",
            "server_name": server_name,
            "client_fingerprint": str(
                profile.get("client_fingerprint") or "chrome"
            ),
            "reality_public_key": public_key,
            "reality_short_id": short_id,
            "mihomo_compatibility": "supported",
        }
        return

    raise HTTPException(
        status_code=422,
        detail=f"Unsupported sing-box security: {security}",
    )


def compile_profile(
    core: str,
    protocol: str,
    profile: dict[str, Any] | None,
    settings: dict,
    stream_settings: dict,
) -> tuple[dict, dict]:
    if not profile:
        return settings, stream_settings

    profile = dict(profile)
    settings = dict(settings)
    stream = dict(stream_settings)

    if core == "xray":
        _apply_xray_transport(stream, profile)
        _apply_xray_security(protocol, settings, stream, profile)
        if protocol == "vless":
            users = list(settings.get("users") or [])
            if users:
                flow = str(profile.get("flow") or "").strip()
                if flow:
                    users[0]["flow"] = flow
                else:
                    users[0].pop("flow", None)
                settings["users"] = users
        return settings, stream

    if core == "sing-box":
        if protocol not in {"hysteria2", "tuic"}:
            _apply_singbox_transport(stream, profile)
        _apply_singbox_tls(protocol, stream, profile)

        users = list(settings.get("users") or [])
        if protocol == "vless" and users:
            flow = str(profile.get("flow") or "").strip()
            if flow:
                users[0]["flow"] = flow
            else:
                users[0].pop("flow", None)
            settings["users"] = users
        elif protocol == "hysteria2":
            up = profile.get("up_mbps")
            down = profile.get("down_mbps")
            if up not in (None, ""):
                settings["up_mbps"] = int(up)
            if down not in (None, ""):
                settings["down_mbps"] = int(down)
            obfs_type = str(profile.get("obfs_type") or "").strip()
            if obfs_type:
                settings["obfs"] = {
                    "type": obfs_type,
                    "password": _required(
                        profile,
                        "obfs_password",
                        "Hysteria2 obfs password",
                    ),
                }
        elif protocol == "tuic":
            congestion = str(
                profile.get("congestion_control") or "bbr"
            ).strip()
            if congestion not in {"cubic", "new_reno", "bbr"}:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid TUIC congestion control",
                )
            settings["congestion_control"] = congestion
            settings["zero_rtt_handshake"] = bool(
                profile.get("zero_rtt_handshake", False)
            )
            metadata = dict(stream.get("_vui") or {})
            metadata["udp_relay_mode"] = str(
                profile.get("udp_relay_mode") or "native"
            )
            stream["_vui"] = metadata

        return settings, stream

    raise HTTPException(status_code=422, detail=f"Unsupported core: {core}")
