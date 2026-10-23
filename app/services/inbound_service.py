from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
import hmac
import re
import secrets
import uuid
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.models.database import Inbound
from app.services.protocol_profiles import compile_profile, decompile_profile
from app.services.grpc_profile import is_grpc_transport
from app.services.hysteria2_profile import password_value
from app.services import tuic_profile, reality_profile
from app.services.node_selection_revision import node_selection_revision
from app.services.inbound_instance_revision import INSTANCE_CHANGED, REVISION_RE, mutation_revision

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


def _routing_mode(value: Any) -> str:
    from app.services.inbound_policy import normalize_mode
    try:
        return normalize_mode(value)
    except ValueError:
        raise HTTPException(422, "Invalid inbound routing mode") from None


def routing_dicts(db: Session, core: str | None = None) -> list[dict]:
    """Keep existing callers' array-only contract."""
    return routing_snapshot(db, core=core)[0]


def routing_snapshot(db: Session, core: str | None = None) -> tuple[list[dict], str]:
    """Batch runtime read for API summaries, never used by pure exporters.

    A failed or unavailable runtime observation cannot turn desired database
    values into an applied-route claim. The manager returns an allowlisted
    summary while holding the same operation lock as reconciliation. The node
    revision uses these same model rows, never a second query. A core filter
    limits only the body; the revision always describes the full inventory.
    """
    from app.services.core_manager import core_manager
    with core_manager.lock:
        items = db.query(Inbound).populate_existing().order_by(Inbound.id.asc()).all()
        revision = node_selection_revision(items)
        values = [to_dict(item) for item in items if core is None or item.core == core]
        try:
            statuses = core_manager.inbound_routing_status(items)
        except Exception:
            statuses = {}
        if type(statuses) is not dict:
            statuses = {}
        for value in values:
            status = statuses.get(value["id"], {})
            if type(status) is not dict:
                status = {}
            value["routing_status"] = {
                "desired_effective_mode": status.get("desired_effective_mode") if type(status.get("desired_effective_mode")) is str and status["desired_effective_mode"] in {"direct", "relay"} else None,
                "applied_effective_mode": status.get("applied_effective_mode") if type(status.get("applied_effective_mode")) is str and status["applied_effective_mode"] in {"direct", "relay"} else None,
                "state": status.get("state") if type(status.get("state")) is str and status["state"] in {"applied", "pending", "stopped", "unknown"} else "unknown",
                "policy_identity": status.get("policy_identity") if type(status.get("policy_identity")) is str and re.fullmatch(r"[0-9a-f]{64}", status["policy_identity"]) else None,
            }
        return values, revision


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
                if not users[0].get("uuid"): users[0]["uuid"] = str(uuid.uuid4())
                if not users[0].get("password"): users[0]["password"] = secrets.token_urlsafe(18)
            settings["users"] = users
    return settings


def to_dict(item: Inbound) -> dict:
    """Allowlisted administration summary, never a raw core/export document.

    Imported settings may contain unknown credential types or private TLS
    material. Internal apply and intentional exports read the model directly.
    """
    settings = item.settings if isinstance(item.settings, dict) else {}
    stream = item.stream_settings if isinstance(item.stream_settings, dict) else {}
    users = settings.get("users") or settings.get("clients") or []
    users = users if isinstance(users, list) else []
    user_maps = [user for user in users if isinstance(user, dict)]
    obfs = settings.get("obfs")
    tls = stream.get("tls")
    return {
        "id": item.id,
        "mutation_revision": mutation_revision(item),
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
        "routing_mode": _routing_mode(item.routing_mode),
        "credentials": {
            "user_count": len(users),
            "has_uuid": any(bool(user.get("uuid") or user.get("id")) for user in user_maps),
            "has_password": bool(settings.get("password")) or any(bool(user.get("password")) for user in user_maps),
            "has_obfs_password": isinstance(obfs, dict) and bool(obfs.get("password")),
        },
        "managed_certificate_eligible": (
            item.core == "sing-box" and item.protocol in {"vless", "trojan", "vmess", "hysteria2", "tuic"}
            and isinstance(tls, dict) and tls.get("enabled") is True and not reality_profile.has_reality(stream)
        ),
        "tag": item.tag,
    }


def editor_dict(item: Inbound) -> dict:
    strict_reality = item.core == "sing-box" and item.protocol == "vless" and reality_profile.is_direct_candidate(item.stream_settings)
    if strict_reality:
        try:
            reality_profile.validate_stored(item.settings, item.stream_settings)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    settings=item.settings or {}
    if item.core == "sing-box" and item.protocol in {"hysteria2", "tuic"}:
        users = settings.get("users") if isinstance(settings, dict) else None
        if not isinstance(users, list) or len(users) != 1 or not isinstance(users[0], dict):
            raise HTTPException(status_code=422, detail=f"Existing {item.protocol} credentials cannot be represented by this editor")
    else:
        users=settings.get("users") or settings.get("clients") or []
    first=users[0] if users else {}
    result={
        "id":item.id,
        "mutation_revision":mutation_revision(item),
        "core":item.core or "xray",
        "protocol":item.protocol,
        "routing_mode":_routing_mode(item.routing_mode),
        "remark":item.remark or "",
        "port":item.port,
        "enable":bool(item.enable),
        "expiry_time":item.expiry_time or 0,
        "tag":item.tag,
        "profile":decompile_profile(item.core or "xray",item.protocol,settings,item.stream_settings if item.core == "sing-box" and item.protocol in {"hysteria2", "tuic"} else (item.stream_settings or {})),
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
    if "instance_id" in payload or "inbound_bindings" in payload:
        raise HTTPException(422, "Inbound instance identity is server-managed")
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
    old_stream = existing.stream_settings if existing else {}
    if (existing is not None and existing.core == "sing-box" and existing.protocol == "vless"
            and reality_profile.has_reality(old_stream)):
        try:
            # Inspect original persisted credentials, before a generic helper
            # can regenerate a missing UUID in an unqualified transport draft.
            reality_profile.validate_existing_for_edit(existing.settings, old_stream)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    strict_reality = core == "sing-box" and protocol == "vless" and (
        reality_profile.is_direct_candidate(old_stream)
        or reality_profile.is_direct_candidate(stream_settings)
        or (not profile and "stream_settings" in payload and reality_profile.has_reality(stream_settings))
        or reality_profile.wants_direct(profile))
    if strict_reality:
        try:
            if existing is not None and reality_profile.is_direct_candidate(old_stream):
                reality_profile.validate_stored(existing.settings, old_stream)
            if not profile and reality_profile.has_reality(stream_settings):
                reality_profile.validate_stored(settings, stream_settings)
            elif existing is not None:
                reality_profile.credential_user(settings)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if core == "sing-box" and protocol == "tuic":
        try:
            if existing is not None:
                tuic_profile.credential_user(settings)
            else:
                users = settings.get("users", [])
                if not isinstance(users, list) or len(users) > 1 or any(not isinstance(user, dict) for user in users):
                    raise ValueError("New TUIC nodes require one UUID/password pair")
                if users:
                    for field, validator in (("uuid", tuic_profile.uuid_value), ("password", tuic_profile.password_value)):
                        if field in users[0] and users[0][field] != "": validator(users[0][field])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    old_transport = old_stream.get("transport") if isinstance(old_stream, dict) else None
    transport = stream_settings.get("transport")
    grpc_edit = existing is not None and core == "sing-box" and protocol == "vless" and (
        is_grpc_transport(old_transport)
        or is_grpc_transport(transport)
        or str(profile.get("transport") or "").lower() == "grpc"
    )
    if strict_reality:
        # The REALITY compiler alone generates new credentials. Existing
        # malformed imports must not be healed by the generic ensure helper.
        settings = deepcopy(settings)
    elif existing is not None and core == "sing-box" and protocol == "hysteria2":
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
    elif existing is not None and core == "sing-box" and protocol == "tuic":
        if profile and "security" not in profile:
            previous_tls = stream_settings.get("tls")
            if not isinstance(previous_tls, dict) or previous_tls.get("enabled") is not True:
                raise HTTPException(status_code=422, detail="Explicit TLS selection is required to change an existing TUIC security state")
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
        creating=existing is None,
    )
    return core, protocol, port, settings, stream_settings


def _create_inbound(db: Session, payload: dict) -> Inbound:
    routing_mode = _routing_mode(payload.get("routing_mode", "inherit"))
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
        routing_mode=routing_mode,
        settings=settings,
        stream_settings=stream_settings,
        tag=payload.get("tag") or f"{core}-{protocol}-{port}",
    )
    db.add(item)
    _commit_inbound_change(db)
    db.refresh(item)
    return item


def _update_inbound(db: Session, inbound_id: int, payload: dict) -> Inbound:
    item = get_inbound(db, inbound_id)
    # API handlers may have loaded this identity before waiting for our lock.
    # Refresh so omitted fields do not overwrite a concurrent committed edit.
    db.refresh(item)
    _stage_inbound_update(db, item, payload)
    _commit_inbound_change(db)
    db.refresh(item)
    return item


def _stage_inbound_update(db: Session, item: Inbound, payload: dict) -> None:
    """Validate and stage only; callers own the read/commit/refresh boundary."""
    routing_mode = _routing_mode(payload["routing_mode"] if "routing_mode" in payload else item.routing_mode)
    core, protocol, port, settings, stream_settings = _prepared_payload(
        payload,
        existing=item,
    )

    duplicate = (
        db.query(Inbound)
        .filter(
            Inbound.id != item.id,
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
    item.routing_mode = routing_mode
    item.port = port
    item.remark = payload.get("remark", item.remark) or ""
    item.enable = bool(payload.get("enable", item.enable))
    item.expiry_time = int(payload.get("expiry_time", item.expiry_time) or 0)
    item.settings = settings
    item.stream_settings = stream_settings
    item.tag = payload.get("tag", item.tag) or f"{core}-{protocol}-{port}"


def _delete_inbound(db: Session, inbound_id: int) -> str:
    item = get_inbound(db, inbound_id)
    db.refresh(item)
    core = item.core or "xray"
    db.delete(item)
    _commit_inbound_change(db)
    return core


def _commit_inbound_change(db: Session) -> None:
    from app.services.core_manager import CoreError, core_manager
    # A new row needs its actual primary key for source-binding compilation.
    # Flush remains inside the SQL transaction and the shared operation lock;
    # rejection rolls it back, rather than persisting an unsafe enabled node.
    db.flush()
    try:
        core_manager.validate_inbound_change(list_inbounds(db))
    except CoreError as exc:
        raise HTTPException(409, {"saved": False, "applied": False, "message": str(exc)}) from None
    db.commit()


def _mutate_inbound(db: Session, operation, *args):
    from app.services.core_manager import core_manager
    with core_manager.lock:
        try:
            return operation(db, *args)
        except Exception:
            # Roll back only the unfinished SQL transaction. This cannot
            # undo a commit that succeeded before a later refresh failed.
            # Runtime apply has its separate saved/not-applied result.
            db.rollback()
            raise


def create_inbound(db: Session, payload: dict) -> Inbound:
    return _mutate_inbound(db, _create_inbound, payload)


def update_inbound(db: Session, inbound_id: int, payload: dict) -> Inbound:
    return _mutate_inbound(db, _update_inbound, inbound_id, payload)


def delete_inbound(db: Session, inbound_id: int) -> str:
    return _mutate_inbound(db, _delete_inbound, inbound_id)


@dataclass(frozen=True)
class GuardedInboundUpdate:
    """Detached values captured before commit, never a post-commit ORM read."""
    core: str
    inbound_json: str
    certificate_plan: tuple[bool, str | None, str | None]


def _guarded_inbound_change(db: Session, inbound_id: int, expected: str, operation):
    from app.services.core_manager import core_manager
    if type(expected) is not str or not REVISION_RE.fullmatch(expected):
        raise HTTPException(422, "Invalid inbound instance revision")
    with core_manager.lock:
        # This path must own its transaction. In particular, do not roll back
        # pending caller work or a transaction owned by a bound Connection.
        bind = db.get_bind()
        if (db.in_transaction() or db.new or db.dirty or db.deleted
                or isinstance(bind, Connection) and bind.in_transaction()):
            raise RuntimeError("Guarded inbound mutation requires a pristine database session")
        try:
            db.execute(text("BEGIN IMMEDIATE"))
            item = db.query(Inbound).populate_existing().filter(Inbound.id == inbound_id).first()
            current = mutation_revision(item)
            if current is None or not hmac.compare_digest(expected, current):
                raise HTTPException(409, INSTANCE_CHANGED)
            result = operation(item)
            _commit_inbound_change(db)
            # No refresh or expired ORM field access after commit: another
            # process may already have replaced the row using the same ID.
            return result
        except Exception:
            db.rollback()
            raise


def update_inbound_if_instance(db: Session, inbound_id: int, expected: str,
                              prepare: Callable[[Inbound], tuple[dict, tuple]]) -> GuardedInboundUpdate:
    def stage(item):
        # Certificate/profile preparation is read-only and uses this checked
        # model. Certificate writes and runtime apply belong after we return.
        payload, certificate_plan = prepare(item)
        _stage_inbound_update(db, item, payload)
        return GuardedInboundUpdate(item.core or "xray", json.dumps(to_dict(item)), certificate_plan)
    return _guarded_inbound_change(db, inbound_id, expected, stage)


def delete_inbound_if_instance(db: Session, inbound_id: int, expected: str) -> str:
    def stage(item):
        core = item.core or "xray"
        db.delete(item)
        return core
    return _guarded_inbound_change(db, inbound_id, expected, stage)
