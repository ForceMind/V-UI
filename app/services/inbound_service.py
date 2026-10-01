from __future__ import annotations

import json
import secrets
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.database import Inbound

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
    settings = dict(settings)
    if core == "xray":
        if protocol in {"vless", "vmess"}:
            clients = list(settings.get("clients") or [])
            if not clients:
                clients = [{"id": str(uuid.uuid4())}]
            elif not clients[0].get("id"):
                clients[0]["id"] = str(uuid.uuid4())
            if protocol == "vmess":
                clients[0].setdefault("alterId", 0)
            settings["clients"] = clients
            if protocol == "vless":
                settings.setdefault("decryption", "none")
        elif protocol == "trojan":
            clients = list(settings.get("clients") or [])
            if not clients:
                clients = [{"password": secrets.token_urlsafe(18)}]
            settings["clients"] = clients
        elif protocol == "shadowsocks":
            settings.setdefault("method", "aes-128-gcm")
            settings.setdefault("password", secrets.token_urlsafe(18))
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
            settings["users"] = users
        elif protocol == "shadowsocks":
            settings.setdefault("method", "aes-128-gcm")
            settings.setdefault("password", secrets.token_urlsafe(24))
        elif protocol == "hysteria2":
            users = list(settings.get("users") or [])
            if not users:
                users = [{"password": secrets.token_urlsafe(18)}]
            settings["users"] = users
        elif protocol == "tuic":
            users = list(settings.get("users") or [])
            if not users:
                users = [{
                    "uuid": str(uuid.uuid4()),
                    "password": secrets.token_urlsafe(18),
                }]
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


def create_inbound(db: Session, payload: dict) -> Inbound:
    core = str(payload.get("core") or "xray").lower()
    protocol = str(payload.get("protocol") or "").lower()
    validate_core_protocol(core, protocol)

    port = int(payload.get("port") or 0)
    if not 1 <= port <= 65535:
        raise HTTPException(status_code=422, detail="Port must be between 1 and 65535")

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

    settings = ensure_credentials(
        core,
        protocol,
        normalize_mapping(payload.get("settings")),
    )
    stream_settings = normalize_mapping(payload.get("stream_settings"))

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
    core = str(payload.get("core", item.core or "xray")).lower()
    protocol = str(payload.get("protocol", item.protocol)).lower()
    validate_core_protocol(core, protocol)

    port = int(payload.get("port", item.port))
    if not 1 <= port <= 65535:
        raise HTTPException(status_code=422, detail="Port must be between 1 and 65535")

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
    item.settings = ensure_credentials(
        core,
        protocol,
        normalize_mapping(payload.get("settings", item.settings)),
    )
    item.stream_settings = normalize_mapping(
        payload.get("stream_settings", item.stream_settings)
    )
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
