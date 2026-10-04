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
    # Editing must not leave stale transport blocks behind.
    for key in ("rawSettings", "wsSettings", "grpcSettings", "xhttpSettings",
                "httpupgradeSettings", "tcpSettings"):
        stream.pop(key, None)
    stream.pop("network", None)
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
        stream.pop("tlsSettings", None)
        stream.pop("realitySettings", None)
        stream.pop("_vui", None)
        return

    if security == "tls":
        certificate_path, key_path = _certificate_paths(profile)
        stream["security"] = "tls"
        stream.pop("realitySettings", None)
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

        existing_reality = stream.get("realitySettings") or {}
        existing_meta = stream.get("_vui") or {}
        private_key = str(
            profile.get("reality_private_key")
            or existing_reality.get("privateKey")
            or ""
        ).strip()
        public_key = str(
            profile.get("reality_public_key")
            or existing_meta.get("reality_public_key")
            or ""
        ).strip()
        if private_key and not public_key:
            try:
                padded = private_key + "=" * (-len(private_key) % 4)
                raw = base64.urlsafe_b64decode(padded.encode())
                public_key = _b64url(
                    X25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(
                        encoding=serialization.Encoding.Raw,
                        format=serialization.PublicFormat.Raw,
                    )
                )
            except Exception as exc:
                raise HTTPException(status_code=422, detail="Existing Reality private key is invalid") from exc
        elif public_key and not private_key:
            raise HTTPException(status_code=422, detail="Reality private key is missing; regenerate the key pair explicitly")
        elif not private_key:
            private_key, public_key = generate_reality_keypair()

        stream["security"] = "reality"
        stream.pop("tlsSettings", None)
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
        stream.pop("_vui", None)
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

        existing_reality = (stream.get("tls") or {}).get("reality") or {}
        existing_meta = stream.get("_vui") or {}
        private_key = str(
            profile.get("reality_private_key")
            or existing_reality.get("private_key")
            or ""
        ).strip()
        public_key = str(
            profile.get("reality_public_key")
            or existing_meta.get("reality_public_key")
            or ""
        ).strip()
        if private_key and not public_key:
            try:
                padded = private_key + "=" * (-len(private_key) % 4)
                raw = base64.urlsafe_b64decode(padded.encode())
                public_key = _b64url(
                    X25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(
                        encoding=serialization.Encoding.Raw,
                        format=serialization.PublicFormat.Raw,
                    )
                )
            except Exception as exc:
                raise HTTPException(status_code=422, detail="Existing Reality private key is invalid") from exc
        elif public_key and not private_key:
            raise HTTPException(status_code=422, detail="Reality private key is missing; regenerate the key pair explicitly")
        elif not private_key:
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
                previous = settings.get("obfs") or {}
                password = str(profile.get("obfs_password") or "").strip()
                if not password and previous.get("type") == obfs_type:
                    password = str(previous.get("password") or "")
                if not password:
                    raise HTTPException(
                        status_code=422,
                        detail="Hysteria2 obfs password is required when enabling obfs",
                    )
                settings["obfs"] = {"type": obfs_type, "password": password}
            else:
                settings.pop("obfs", None)
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


def _first_user(settings: dict) -> dict:
    users = settings.get("users") or settings.get("clients") or []
    return dict(users[0]) if users else {}


def decompile_profile(core: str, protocol: str, settings: dict | None,
                      stream_settings: dict | None) -> dict[str, Any]:
    """Convert persisted core fields back to the visual editor shape.

    Private Reality keys are intentionally not returned. compile_profile()
    preserves them from the persisted stream when the editor saves.
    """
    settings = dict(settings or {})
    stream = dict(stream_settings or {})
    meta = dict(stream.get("_vui") or {})
    profile: dict[str, Any] = {
        "security": "none",
        "transport": "raw" if core == "xray" else "direct",
        "flow": "",
        "server_name": str(meta.get("server_name") or ""),
        "certificate_path": "",
        "key_path": "",
        "path": "/",
        "host": "",
        "service_name": "",
        "xhttp_mode": "auto",
        "reality_target": "",
        "reality_server_name": "",
        "reality_short_id": str(meta.get("reality_short_id") or ""),
        "client_fingerprint": str(meta.get("client_fingerprint") or "chrome"),
        "skip_cert_verify": bool(meta.get("skip_cert_verify", False)),
        "up_mbps": settings.get("up_mbps", 100),
        "down_mbps": settings.get("down_mbps", 100),
        "obfs_type": "",
        "obfs_password": "",
        "congestion_control": str(settings.get("congestion_control") or "bbr"),
        "udp_relay_mode": str(meta.get("udp_relay_mode") or "native"),
        "zero_rtt_handshake": bool(settings.get("zero_rtt_handshake", False)),
    }
    user = _first_user(settings)
    profile["flow"] = str(user.get("flow") or "")

    if core == "xray":
        method = str(stream.get("method") or stream.get("network") or "raw").lower()
        method = {"tcp": "raw", "ws": "websocket"}.get(method, method)
        profile["transport"] = method
        if method == "websocket":
            value = stream.get("wsSettings") or {}
            profile["path"] = str(value.get("path") or "/")
            profile["host"] = str(value.get("host") or "")
        elif method == "grpc":
            value = stream.get("grpcSettings") or {}
            profile["service_name"] = str(value.get("serviceName") or "")
        elif method == "xhttp":
            value = stream.get("xhttpSettings") or {}
            profile["path"] = str(value.get("path") or "/")
            profile["host"] = str(value.get("host") or "")
            profile["xhttp_mode"] = str(value.get("mode") or "auto")

        security = str(meta.get("security") or stream.get("security") or "none").lower()
        profile["security"] = security
        if security == "tls":
            tls = stream.get("tlsSettings") or {}
            certs = tls.get("certificates") or []
            cert = certs[0] if certs else {}
            profile["server_name"] = str(meta.get("server_name") or tls.get("serverName") or "")
            profile["certificate_path"] = str(cert.get("certificateFile") or "")
            profile["key_path"] = str(cert.get("keyFile") or "")
        elif security == "reality":
            reality = stream.get("realitySettings") or {}
            profile["reality_target"] = str(reality.get("target") or "")
            names = reality.get("serverNames") or []
            shorts = reality.get("shortIds") or []
            profile["reality_server_name"] = str(meta.get("server_name") or (names[0] if names else ""))
            profile["reality_short_id"] = str(meta.get("reality_short_id") or (shorts[0] if shorts else ""))
        return profile

    transport = stream.get("transport") or {}
    profile["transport"] = str(transport.get("type") or ("quic" if protocol in {"hysteria2", "tuic"} else "direct"))
    if profile["transport"] in {"ws", "httpupgrade"}:
        profile["path"] = str(transport.get("path") or "/")
        profile["host"] = str((transport.get("headers") or {}).get("Host") or transport.get("host") or "")
    elif profile["transport"] == "grpc":
        profile["service_name"] = str(transport.get("service_name") or "")

    tls = stream.get("tls") or {}
    reality = tls.get("reality") or {}
    if reality.get("enabled"):
        profile["security"] = "reality"
        handshake = reality.get("handshake") or {}
        server = str(handshake.get("server") or "")
        port = handshake.get("server_port")
        profile["reality_target"] = server + ((":" + str(port)) if server and port else "")
        profile["reality_server_name"] = str(meta.get("server_name") or tls.get("server_name") or server)
        short = reality.get("short_id") or []
        if isinstance(short, list):
            short = short[0] if short else ""
        profile["reality_short_id"] = str(meta.get("reality_short_id") or short or "")
    elif tls.get("enabled"):
        profile["security"] = "tls"
        profile["server_name"] = str(meta.get("server_name") or tls.get("server_name") or "")
        profile["certificate_path"] = str(tls.get("certificate_path") or "")
        profile["key_path"] = str(tls.get("key_path") or "")
    else:
        profile["security"] = "none"

    if protocol == "hysteria2":
        obfs = settings.get("obfs") or {}
        profile["obfs_type"] = str(obfs.get("type") or "")
        profile["obfs_password"] = ""
        profile["obfs_password_set"] = bool(obfs.get("password"))
    return profile
