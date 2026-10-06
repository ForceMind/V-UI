from __future__ import annotations

import base64
from copy import deepcopy
import secrets
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from fastapi import HTTPException

from app.services.websocket_profile import websocket_host, websocket_path
from app.services.grpc_profile import grpc_service_name, is_grpc_transport
from app.services import hysteria2_profile, tuic_profile


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


def _grpc_editor_guard(profile: dict, stream: dict, *, entering: bool) -> list[str] | None:
    """Keep bounded gRPC edits literal and refuse unrepresented imported options."""
    previous = stream.get("transport", {})
    existing = is_grpc_transport(previous)
    desired_security = str(profile.get("security") or "none").lower()
    try:
        if existing and previous["type"] != "grpc":
            raise ValueError("Existing gRPC transport type is malformed; no fields were dropped")
        if entering:
            profile["service_name"] = grpc_service_name(profile.get("service_name"))
            if {"authority", "headers", "user_agent", "idle_timeout", "ping_timeout",
                    "permit_without_stream", "multi_mode", "max_early_data",
                    "early_data_header_name"} & profile.keys():
                raise ValueError("The visual VLESS gRPC profile does not accept authority, headers or additional transport options")
        if entering and ("Host" in profile or (profile.get("host", "") != "" and
                (existing or not isinstance(previous, dict) or previous.get("type") != "ws"))):
            raise ValueError("The verified VLESS gRPC profile does not accept a custom Host")
        if existing and set(previous) - {"type", "service_name"}:
            raise ValueError("Existing gRPC transport options cannot be represented by this editor; no fields were dropped")
        # Do not normalize an imported invalid flow into an exportable empty flow.
        if not isinstance(profile.get("flow", ""), str):
            raise ValueError("VLESS flow must be a string")
        if entering and profile.get("flow", "") != "":
            raise ValueError("The verified VLESS gRPC profile requires an empty flow")
        for field in ("server_name", "client_fingerprint", "certificate_path", "key_path"):
            if field in profile and (not isinstance(profile[field], str) or profile[field] != profile[field].strip()):
                raise ValueError(f"gRPC {field} must be a literal string without surrounding whitespace")
        if "skip_cert_verify" in profile and type(profile["skip_cert_verify"]) is not bool:
            raise ValueError("gRPC skip_cert_verify must be a boolean")
        previous_tls = stream.get("tls", {})
        metadata = stream.get("_vui", {})
        if existing or (entering and desired_security == "tls"):
            if not isinstance(previous_tls, dict) or not isinstance(metadata, dict):
                raise ValueError("Existing gRPC TLS/client metadata cannot be represented by this editor")
            reality = previous_tls.get("reality")
            is_reality = isinstance(reality, dict) and reality.get("enabled") is True
            tls_fields = {"enabled", "server_name", "certificate_path", "key_path", "alpn"}
            meta_fields = {"security", "server_name", "client_fingerprint", "skip_cert_verify"}
            if is_reality:
                tls_fields.add("reality")
                meta_fields |= {"reality_public_key", "reality_short_id", "mihomo_compatibility"}
                handshake = reality.get("handshake", {})
                shorts = reality.get("short_id", [])
                if (set(reality) - {"enabled", "handshake", "private_key", "short_id"}
                        or not isinstance(handshake, dict) or set(handshake) - {"server", "server_port"}
                        or not isinstance(shorts, list) or len(shorts) > 1):
                    raise ValueError("Existing gRPC REALITY options cannot be represented by this editor; no fields were dropped")
            if set(previous_tls) - tls_fields or set(metadata) - meta_fields:
                raise ValueError("Existing gRPC TLS/client metadata options cannot be represented by this editor; no fields were dropped")
            if existing and previous_tls.get("enabled") is True and not is_reality:
                if metadata.get("security", "tls") != "tls":
                    raise ValueError("Existing gRPC client/server TLS security conflicts")
                if metadata.get("server_name") and metadata["server_name"] != previous_tls.get("server_name"):
                    raise ValueError("Existing gRPC client/server TLS names conflict")
            if "enabled" in previous_tls and type(previous_tls["enabled"]) is not bool:
                raise ValueError("Existing gRPC TLS enabled must be a boolean")
            for mapping, fields in ((previous_tls, ("server_name", "certificate_path", "key_path")),
                                    (metadata, ("security", "server_name", "client_fingerprint"))):
                for field in fields:
                    if field in mapping and (not isinstance(mapping[field], str) or mapping[field] != mapping[field].strip()):
                        raise ValueError(f"Existing gRPC {field} must be a literal string without surrounding whitespace")
            if "skip_cert_verify" in metadata and type(metadata["skip_cert_verify"]) is not bool:
                raise ValueError("Existing gRPC skip_cert_verify must be a boolean")
        alpn = previous_tls.get("alpn") if isinstance(previous_tls, dict) else None
        if entering and desired_security == "tls" and "alpn" in previous_tls and alpn != ["h2"]:
            raise ValueError("The validated gRPC profile only accepts HTTP/2 h2 ALPN")
        if existing and "alpn" in previous_tls and previous_tls["alpn"] != ["h2"]:
            raise ValueError("Existing gRPC ALPN cannot be represented by this editor")
        return deepcopy(alpn)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
    settings = deepcopy(settings)
    stream = deepcopy(stream_settings)

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
        tuic_alpn = None
        if protocol == "tuic":
            try:
                tuic_alpn = tuic_profile.prepare_profile(profile, settings, stream)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        hy2_alpn = None
        if protocol == "hysteria2":
            try:
                hy2_alpn = hysteria2_profile.prepare_profile(profile, settings, stream)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        previous_transport = stream.get("transport", {})
        previous_grpc = protocol == "vless" and is_grpc_transport(previous_transport)
        vless_grpc = protocol == "vless" and str(profile.get("transport") or "").lower() == "grpc"
        if previous_grpc:
            # An omitted edit field is not an explicit repair of an imported
            # value. Preserve literals until the caller supplies a replacement.
            prior_users = settings.get("users")
            prior_user = prior_users[0] if isinstance(prior_users, list) and prior_users and isinstance(prior_users[0], dict) else {}
            profile.setdefault("flow", deepcopy(prior_user.get("flow", "")))
            prior_meta = stream.get("_vui", {})
            if isinstance(prior_meta, dict):
                profile.setdefault("skip_cert_verify", deepcopy(prior_meta.get("skip_cert_verify", False)))
                profile.setdefault("client_fingerprint", deepcopy(prior_meta.get("client_fingerprint", "")))
        preserved_grpc_alpn = _grpc_editor_guard(profile, stream, entering=vless_grpc) if vless_grpc or previous_grpc else None
        vless_ws = protocol == "vless" and str(profile.get("transport") or "").lower() == "ws"
        preserved_ws_alpn = None
        if vless_ws:
            try:
                profile["path"] = websocket_path(profile.get("path", "/"))
                host = profile.get("host", "")
                if host != "":
                    profile["host"] = websocket_host(host)
                if not isinstance(profile.get("flow", ""), str):
                    raise ValueError("VLESS flow must be a string; the verified WebSocket profile uses an empty flow")
                if {"headers", "max_early_data", "early_data_header_name"} & profile.keys():
                    raise ValueError("The visual VLESS WebSocket profile does not accept arbitrary headers or early data")
                previous = stream.get("transport") or {}
                if isinstance(previous, dict) and previous.get("type") == "ws":
                    headers = previous.get("headers", {})
                    if (set(previous) - {"type", "path", "headers"}
                            or not isinstance(headers, dict) or set(headers) - {"Host"}):
                        raise ValueError("Existing WebSocket options cannot be represented by this editor; no fields were dropped")
                if str(profile.get("security") or "none").lower() == "tls":
                    previous_tls = stream.get("tls") or {}
                    if not isinstance(previous_tls, dict):
                        raise ValueError("Existing WebSocket TLS options cannot be represented by this editor")
                    previous_reality = previous_tls.get("reality")
                    leaving_reality = isinstance(previous_reality, dict) and previous_reality.get("enabled") is True
                    if previous_tls.get("enabled") and not leaving_reality:
                        metadata = stream.get("_vui") or {}
                        if (set(previous_tls) - {"enabled", "server_name", "certificate_path", "key_path", "alpn"}
                                or not isinstance(metadata, dict)
                                or set(metadata) - {"security", "server_name", "client_fingerprint", "skip_cert_verify"}):
                            raise ValueError("Existing WebSocket TLS options cannot be represented by this editor; no fields were dropped")
                    preserved_ws_alpn = previous_tls.get("alpn")
                    if preserved_ws_alpn not in (None, ["http/1.1"]):
                        raise ValueError("The validated WebSocket profile only accepts HTTP/1.1 ALPN")
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        if protocol not in {"hysteria2", "tuic"}:
            _apply_singbox_transport(stream, profile)
        _apply_singbox_tls(protocol, stream, profile)
        if protocol == "hysteria2":
            stream["_vui"]["client_fingerprint"] = profile["client_fingerprint"]
            if hy2_alpn is not None:
                stream["tls"]["alpn"] = hy2_alpn
        if protocol == "tuic":
            stream["_vui"]["client_fingerprint"] = profile["client_fingerprint"]
            stream["tls"]["alpn"] = tuic_alpn
        if vless_ws and preserved_ws_alpn:
            stream["tls"]["alpn"] = list(preserved_ws_alpn)
        if vless_grpc and str(profile.get("security") or "none").lower() == "tls":
            if preserved_grpc_alpn is not None:
                stream["tls"]["alpn"] = preserved_grpc_alpn
            # Empty optional fingerprint stays empty instead of acquiring the
            # generic form's Chrome default during an unrelated imported edit.
            stream["_vui"]["client_fingerprint"] = profile.get("client_fingerprint", "chrome")

        users = list(settings.get("users") or [])
        if protocol == "shadowsocks":
            method=str(profile.get("shadowsocks_method") or settings.get("method") or "aes-128-gcm").strip()
            if method not in {"aes-128-gcm","aes-256-gcm","chacha20-ietf-poly1305"}:
                raise HTTPException(status_code=422,detail="Unsupported Shadowsocks method")
            settings["method"]=method

        if protocol == "vless" and users:
            flow = profile.get("flow", "") if vless_grpc or previous_grpc else str(profile.get("flow") or "").strip()
            if flow:
                users[0]["flow"] = flow
            else:
                users[0].pop("flow", None)
            settings["users"] = users
        elif protocol == "hysteria2":
            if profile.get("hysteria2_password", "") != "":
                if len(users) != 1 or not isinstance(users[0], dict):
                    raise HTTPException(status_code=422, detail="Hysteria2 requires one explicit password user")
                users[0]["password"] = profile["hysteria2_password"]
                settings["users"] = users
            for field in ("up_mbps", "down_mbps"):
                if field in profile:
                    if profile[field] in (None, ""):
                        settings.pop(field, None)
                    else:
                        settings[field] = profile[field]
            # Omission is not a request to disable an existing obfuscator.
            if "obfs_type" in profile:
                obfs_type = profile["obfs_type"]
                if obfs_type:
                    previous = settings.get("obfs") or {}
                    password = profile.get("obfs_password", "")
                    if not password and previous.get("type") == obfs_type:
                        password = previous.get("password", "")
                    if not password:
                        raise HTTPException(status_code=422, detail="Hysteria2 obfs password is required when enabling obfs")
                    settings["obfs"] = {"type": obfs_type, "password": password}
                else:
                    settings.pop("obfs", None)
        elif protocol == "tuic":
            for field in ("uuid", "password"):
                value = profile.get("tuic_" + field, "")
                if value != "": users[0][field] = value
            settings["users"] = users
            # Advanced draft values remain explicit; strict export is narrower.
            for field, default in (("congestion_control", "cubic"), ("zero_rtt_handshake", False)):
                if field in settings or profile[field] != default: settings[field] = profile[field]
            if "udp_relay_mode" in stream_settings.get("_vui", {}) or profile["udp_relay_mode"] != "native":
                stream["_vui"]["udp_relay_mode"] = profile["udp_relay_mode"]

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
    if core == "sing-box" and protocol in {"hysteria2", "tuic"}:
        try:
            module = tuic_profile if protocol == "tuic" else hysteria2_profile
            return module.editor_profile(settings, stream_settings)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    settings = dict(settings or {})
    stream = dict(stream_settings or {})
    grpc = core == "sing-box" and protocol == "vless" and is_grpc_transport(stream.get("transport"))
    if grpc and (not isinstance(stream.get("_vui", {}), dict) or not isinstance(stream.get("tls", {}), dict)):
        raise HTTPException(status_code=422, detail="Existing gRPC TLS/client metadata cannot be represented by this editor")
    if grpc and "reality" in stream.get("tls", {}) and not isinstance(stream["tls"]["reality"], dict):
        raise HTTPException(status_code=422, detail="Existing gRPC REALITY options cannot be represented by this editor")
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
    profile["shadowsocks_method"] = str(settings.get("method") or "aes-128-gcm")
    profile["shadowsocks_password_set"] = bool(settings.get("password"))

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
        if protocol == "vless" and profile["transport"] == "ws":
            # An imported invalid path must require a deliberate correction,
            # not become an exportable root path on an unrelated visual edit.
            profile["path"] = transport.get("path", "")
            profile["flow"] = user.get("flow", "")
        headers = transport.get("headers") or {}
        if not isinstance(headers, dict):
            headers = {}  # compile_profile refuses unrepresentable WS imports.
        profile["host"] = str(headers.get("Host") or transport.get("host") or "")
        if protocol == "vless" and profile["transport"] == "ws":
            profile["host"] = headers.get("Host", "")
            if "Host" in headers and headers["Host"] == "":
                # Explicit empty imported headers are rejected by strict export.
                # Null keeps that invalid draft invalid on an unrelated save;
                # typing/clearing the input explicitly yields the optional "".
                profile["host"] = None
    elif grpc or profile["transport"] == "grpc":
        profile["service_name"] = transport.get("service_name", "") if grpc else str(transport.get("service_name") or "")
        if grpc:
            profile["flow"] = user.get("flow", "")
            profile["client_fingerprint"] = meta.get("client_fingerprint", "")
            profile["skip_cert_verify"] = meta.get("skip_cert_verify", False)

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

    if grpc and profile["security"] == "tls":
        profile["server_name"] = meta.get("server_name", tls.get("server_name", ""))
        profile["certificate_path"] = tls.get("certificate_path", "")
        profile["key_path"] = tls.get("key_path", "")

    if protocol == "hysteria2":
        obfs = settings.get("obfs") or {}
        profile["obfs_type"] = str(obfs.get("type") or "")
        profile["obfs_password"] = ""
        profile["obfs_password_set"] = bool(obfs.get("password"))
    return profile
