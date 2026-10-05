from __future__ import annotations

import json
from copy import deepcopy
import re
import secrets
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.database import Inbound
from app.services.protocol_profiles import compile_profile, decompile_profile
from app.services.grpc_profile import is_grpc_transport
from app.services.hysteria2_profile import password_value

SUPPORTED_CORES = {"xray", "sing-box"}
XRAY_PROTOCOLS = {"vless", "vmess", "trojan", "shadowsocks"}
SING_BOX_PROTOCOLS = {
    "vless",
    "vmess",
    "trojan",
    "shadowsocks",
    "hysteria2",
    "tuic",
}


def normalize_mapping(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return {}
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid JSON: {exc.msg}") from exc
        if not isinstance(decoded, dict):
            raise HTTPException(status_code=422, detail="Expected a JSON object")
        return decoded
    raise HTTPException(status_code=422, detail="Expected an object")


def validate_core_protocol(core: str, protocol: str) -> None:
    core = core.lower()
    protocol = protocol.lower()
    if core not in SUPPORTED_CORES:
        raise HTTPException(status_code=422, detail=f"Unsupported core: {core}")
    supported = XRAY_PROTOCOLS if core == "xray" else SING_BOX_PROTOCOLS
    if protocol not in supported:
        raise HTTPException(
            status_code=422,
            detail=f"{protocol} is not supported by {core}",
        )


def ensure_credentials(core: str, protocol: str, settings: dict) -> dict:
    settings = deepcopy(settings)

    if core == "xray":
        if protocol in {"vless", "vmess"}:
            users = list(settings.get("users") or settings.pop("clients", []) or [])
            if not users:
                users = [{"id": str(uuid.uuid4())}]
            elif not users[0].get("id"):
                users[0]["id"] = str(uuid.uuid4())
            settings["users"] = users
            if protocol == "vless":
                settings.setdefault("decryption", "none")
        elif protocol == "trojan":
            users = list(settings.get("users") or settings.pop("clients", []) or [])
            if not users:
                users = [{"password": secrets.token_urlsafe(18)}]
            elif not users[0].get("password"):
                users[0]["password"] = secrets.token_urlsafe(18)
            settings["users"] = users
        elif protocol == "shadowsocks":
            settings.setdefault("network", "tcp,udp")
            settings.setdefault("method", "aes-256-gcm")
            settings.setdefault("password", secrets.token_urlsafe(24))
    else:
        if protocol in {"vless", "vmess"}:
            users = list(settings.get("users") or [])
            if not users:
                users = [{"uuid": str(uuid.uuid4())}]
            elif not users[0].get("uuid"):
                users[0]["uuid"] = str(uuid.uuid4())
            settings["users"] = users
        elif protocol == "trojan":
            users = list(settings.get("users") or [])
            if not users:
                users = [{"password": secrets.token_urlsafe(18)}]
            elif not users[0].get("password"):
                users[0]["password"] = secrets.token_urlsafe(18)
            settings["users"] = users
        elif protocol == "shadowsocks":
            settings.setdefault("method", "aes-128-gcm")
            settings.setdefault("password", secrets.token_urlsafe(24))
        elif protocol == "hysteria2":
            users = list(settings.get("users") or [])
            if not users:
                users = [{"password": secrets.token_urlsafe(18)}]
            elif not users[0].get("password"):
                users[0]["password"] = secrets.token_urlsafe(18)
            settings["users"] = users
        elif protocol == "tuic":
            users = list(settings.get("users") or [])
            if not users:
                users = [{
                    "uuid": str(uuid.uuid4()),
                    "password": secrets.token_urlsafe(18),
                }]
            else:
                users[0].setdefault("uuid", str(uuid.uuid4()))
                users[0].setdefault("password", secrets.token_urlsafe(18))
            settings["users"] = users
    return settings


def to_dict(item: Inbound) -> dict:
    return {
        "id": item.id,
        "user_id": item.user_id,
        "core": item.core or "xray",
        "up": item.up or 0,
        "down": item.down or 0,
        "total": item.total or 0,
        "remark": item.remark or "",
        "enable": bool(item.enable),
        "expiry_time": item.expiry_time or 0,
        "port": item.port,
        "protocol": item.protocol,
        "settings": item.settings or {},
        "stream_settings": item.stream_settings or {},
        "tag": item.tag,
    }


def editor_dict(item: Inbound) -> dict:
    settings=item.settings or {}
    if item.core == "sing-box" and item.protocol == "hysteria2":
        users = settings.get("users") if isinstance(settings, dict) else None
        if not isinstance(users, list) or len(users) != 1 or not isinstance(users[0], dict):
            raise HTTPException(status_code=422, detail="Existing Hysteria2 credentials cannot be represented by this editor")
    else:
        users=settings.get("users") or settings.get("clients") or []
    first=users[0] if users else {}
    result={
        "id":item.id,
        "core":item.core or "xray",
        "protocol":item.protocol,
        "remark":item.remark or "",
        "port":item.port,
        "enable":bool(item.enable),
        "expiry_time":item.expiry_time or 0,
        "tag":item.tag,
        "profile":decompile_profile(item.core or "xray",item.protocol,settings,item.stream_settings if item.core == "sing-box" and item.protocol == "hysteria2" else (item.stream_settings or {})),
        "credentials":{
            "user_count":len(users),
            "has_uuid":bool(first.get("uuid") or first.get("id")),
            "has_password":bool(first.get("password") or settings.get("password")),
        },
    }
    return result


def list_inbounds(db: Session, core: str | None = None) -> list[Inbound]:
    query = db.query(Inbound)
    if core:
        query = query.filter(Inbound.core == core)
    return query.order_by(Inbound.id.asc()).all()


def get_inbound(db: Session, inbound_id: int) -> Inbound:
    item = db.query(Inbound).filter(Inbound.id == inbound_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Inbound not found")
    return item


def _prepared_payload(payload: dict, existing: Inbound | None = None) -> tuple[str, str, int, dict, dict]:
    core = str(
        payload.get("core")
        or (existing.core if existing else "xray")
        or "xray"
    ).lower()
    protocol = str(
        payload.get("protocol")
        or (existing.protocol if existing else "")
    ).lower()
    validate_core_protocol(core, protocol)

    port = int(payload.get("port") or (existing.port if existing else 0) or 0)
    if not 1 <= port <= 65535:
        raise HTTPException(status_code=422, detail="Port must be between 1 and 65535")

    settings = normalize_mapping(payload.get("settings", existing.settings if existing else None))
    stream_settings = normalize_mapping(
        payload.get(
            "stream_settings",
            existing.stream_settings if existing else None,
        )
    )
    profile = normalize_mapping(payload.get("profile"))
    if existing is None and core == "sing-box" and protocol == "hysteria2":
        users = settings.get("users", [])
        if (not isinstance(users, list) or len(users) > 1
                or any(not isinstance(user, dict) for user in users)):
            raise HTTPException(status_code=422, detail="New Hysteria2 nodes require one password user")
        if users and "password" in users[0] and users[0]["password"] != "":
            try:
                password_value(users[0]["password"])
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
    old_stream = existing.stream_settings if existing else {}
    old_transport = old_stream.get("transport") if isinstance(old_stream, dict) else None
    transport = stream_settings.get("transport")
    grpc_edit = existing is not None and core == "sing-box" and protocol == "vless" and (
        is_grpc_transport(old_transport)
        or is_grpc_transport(transport)
        or str(profile.get("transport") or "").lower() == "grpc"
    )
    if existing is not None and core == "sing-box" and protocol == "hysteria2":
        users = settings.get("users")
        try:
            if not isinstance(users, list) or len(users) != 1 or not isinstance(users[0], dict):
                raise ValueError("Existing Hysteria2 editing requires one explicit password user")
            password_value(users[0].get("password"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc) + "; no credential was generated") from exc
        if profile and "security" not in profile:
            previous_tls = stream_settings.get("tls")
            if not isinstance(previous_tls, dict) or previous_tls.get("enabled") is not True:
                raise HTTPException(status_code=422, detail="Explicit TLS selection is required to change an existing Hysteria2 security state")
        settings = deepcopy(settings)
    elif grpc_edit:
        # Creating a node may generate credentials; an ordinary edit must not
        # repair an imported missing/invalid secret and silently enable export.
        users = settings.get("users")
        uid = users[0].get("uuid") if isinstance(users, list) and len(users) == 1 and isinstance(users[0], dict) else None
        if not isinstance(uid, str) or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", uid):
            raise HTTPException(status_code=422, detail="Existing VLESS gRPC editing requires one explicit valid UUID; no credential was generated")
        settings = deepcopy(settings)
    else:
        settings = ensure_credentials(core, protocol, settings)
    settings, stream_settings = compile_profile(
        core,
        protocol,
        profile,
        settings,
        stream_settings,
    )
    return core, protocol, port, settings, stream_settings


def create_inbound(db: Session, payload: dict) -> Inbound:
    core, protocol, port, settings, stream_settings = _prepared_payload(payload)

    duplicate = (
        db.query(Inbound)
        .filter(Inbound.port == port, Inbound.enable.is_(True))
        .first()
    )
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail=f"Port {port} is already used by inbound {duplicate.id}",
        )

    item = Inbound(
        user_id=payload.get("user_id"),
        core=core,
        remark=payload.get("remark") or "",
        enable=bool(payload.get("enable", True)),
        expiry_time=int(payload.get("expiry_time") or 0),
        port=port,
        protocol=protocol,
        settings=settings,
        stream_settings=stream_settings,
        tag=payload.get("tag") or f"{core}-{protocol}-{port}",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def update_inbound(db: Session, inbound_id: int, payload: dict) -> Inbound:
    item = get_inbound(db, inbound_id)
    core, protocol, port, settings, stream_settings = _prepared_payload(
        payload,
        existing=item,
    )

    duplicate = (
        db.query(Inbound)
        .filter(
            Inbound.id != inbound_id,
            Inbound.port == port,
            Inbound.enable.is_(True),
        )
        .first()
    )
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail=f"Port {port} is already used by inbound {duplicate.id}",
        )

    item.core = core
    item.protocol = protocol
    item.port = port
    item.remark = payload.get("remark", item.remark) or ""
    item.enable = bool(payload.get("enable", item.enable))
    item.expiry_time = int(payload.get("expiry_time", item.expiry_time) or 0)
    item.settings = settings
    item.stream_settings = stream_settings
    item.tag = payload.get("tag", item.tag) or f"{core}-{protocol}-{port}"

    db.commit()
    db.refresh(item)
    return item


def delete_inbound(db: Session, inbound_id: int) -> str:
    item = get_inbound(db, inbound_id)
    core = item.core or "xray"
    db.delete(item)
    db.commit()
    return core
