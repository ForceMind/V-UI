from __future__ import annotations

import json
from typing import Any, Literal
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.api.cores import apply_checked
from app.services.inbound_service import (
    create_inbound,
    delete_inbound,
    delete_inbound_if_instance,
    editor_dict,
    get_inbound,
    list_inbounds,
    normalize_mapping,
    routing_snapshot,
    to_dict,
    update_inbound,
    update_inbound_if_instance,
)
from app.services.protocol_profiles import profile_catalog
from app.services.reality_profile import has_reality
from app.services.node_selection_revision import NODE_REVISION_HEADER

router = APIRouter()


class InboundPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    core: str = "xray"
    remark: str = ""
    port: int = Field(ge=1, le=65535)
    protocol: str
    routing_mode: Literal["inherit", "direct", "relay"] = "inherit"
    settings: dict[str, Any] | str | None = None
    stream_settings: dict[str, Any] | str | None = None
    profile: dict[str, Any] | None = None
    enable: bool = True
    expiry_time: int = 0
    tag: str | None = None
    user_id: int | None = None
    certificate_id: str | None = None


class InboundUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    remark: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    profile: dict[str, Any] | None = None
    enable: bool | None = None
    expiry_time: int | None = None
    tag: str | None = None
    certificate_id: str | None = None
    routing_mode: Literal["inherit", "direct", "relay"] | None = None
    # Only omission selects legacy current-ID behavior. An explicit null is
    # invalid, and old servers' extra=forbid rejects this new JSON condition.
    expected_instance_revision: str = Field(default=None, strict=True, min_length=64,
        max_length=64, pattern=r"^[0-9a-f]{64}$",
        description="Optional displayed node-instance revision; explicit null is invalid")

    @field_validator("routing_mode", mode="before")
    @classmethod
    def routing_mode_not_null(cls, value):
        if value is None:
            raise ValueError("routing_mode cannot be null")
        return value


class InboundDeletePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_instance_revision: str = Field(strict=True, min_length=64, max_length=64,
        pattern=r"^[0-9a-f]{64}$")


def _managed_certificate(profile: dict | None, certificate_id: str, core: str, protocol: str):
    from app.certificates.manager import get_manager
    from app.api.certificates import perform
    if core != "sing-box" or protocol not in {"vless", "trojan", "vmess", "hysteria2", "tuic"}:
        raise HTTPException(409, "Managed certificates require sing-box VLESS/TLS, Trojan/TLS, VMess/TLS or Hysteria2/TLS or TUIC/TLS")
    if profile and "security" in profile and str(profile["security"]).lower() != "tls":
        raise HTTPException(409, "Managed certificates require TLS; clear certificate_id when leaving TLS")
    paths, domain, _ = perform(lambda: get_manager().material(certificate_id))
    return {
        **(profile or {}),
        "security": "tls",
        "server_name": domain,
        "certificate_path": str(paths[0]),
        "key_path": str(paths[1]),
    }


@router.get("/profiles")
def get_protocol_profiles():
    return profile_catalog()


@router.get("")
@router.get("/")
def get_all_inbounds(response: Response, core: str | None = None, db: Session = Depends(get_db)):
    values, revision = routing_snapshot(db, core=core)
    response.headers[NODE_REVISION_HEADER] = revision
    return values


@router.get("/{inbound_id}/editor")
def get_inbound_editor(inbound_id: int, db: Session = Depends(get_db)):
    item = get_inbound(db, inbound_id)
    value = editor_dict(item)
    try:
        from app.certificates.manager import get_manager
        value["certificate_id"] = get_manager().binding("inbound:" + str(item.id))
    except Exception:
        # Certificate storage availability must not hide a node from the editor.
        value["certificate_id"] = None
        value["certificate_binding_unavailable"] = True
    return value


@router.post("")
@router.post("/")
def add_inbound(payload: InboundPayload, db: Session = Depends(get_db)):
    data = payload.model_dump()
    if payload.certificate_id:
        if has_reality(normalize_mapping(payload.stream_settings)):
            raise HTTPException(409, "REALITY cannot bind a managed certificate")
        data["profile"] = _managed_certificate(
            payload.profile, payload.certificate_id, payload.core, payload.protocol
        )
    item = create_inbound(db, data)
    core_status = apply_checked(item.core)
    if payload.certificate_id:
        from app.certificates.manager import get_manager
        from app.api.certificates import perform
        perform(lambda: get_manager().bind(payload.certificate_id, "inbound:" + str(item.id)))
    return {"message": "Inbound added", "inbound": to_dict(item), "core": core_status}


def _prepare_inbound_edit(item, data: dict) -> tuple[dict, tuple[bool, str | None, str | None]]:
    """Read-only preparation; guarded callers already checked this model."""
    data = dict(data)
    profile = data.get("profile")
    if (data.get("certificate_id") and has_reality(item.stream_settings)
            and (not isinstance(profile, dict) or profile.get("security") != "tls")):
        raise HTTPException(409, "Choose an explicit TLS transition before binding a managed certificate to REALITY")
    # Match compile_profile: {} is a no-op; implicit security is protocol-aware.
    # In particular, Trojan remains TLS when security is missing/empty/null.
    default_security = "tls" if item.core == "sing-box" and item.protocol in {"hysteria2", "tuic", "trojan"} else "none"
    leaving_tls = bool(profile) and str(profile.get("security") or default_security).lower() in {"none", "reality"}
    certificate_changed = "certificate_id" in data or leaving_tls
    certificate_id = data.pop("certificate_id", None)
    data["core"] = item.core or "xray"
    data["protocol"] = item.protocol

    previous_certificate = None
    certificate_manager = None
    if certificate_changed:
        from app.certificates.manager import get_manager
        from app.api.certificates import perform
        certificate_manager = get_manager()
        target = "inbound:" + str(item.id)
        previous_certificate = certificate_manager.binding(target)
        if certificate_id:
            data["profile"] = _managed_certificate(
                data.get("profile"), certificate_id, data["core"], data["protocol"]
            )
        elif previous_certificate and not leaving_tls:
            profile = data.get("profile")
            if not isinstance(profile, dict):
                raise HTTPException(
                    409,
                    "Provide explicit manual certificate and key paths before removing managed renewal",
                )
            old_paths, _, _ = perform(
                lambda: certificate_manager.material(previous_certificate)
            )
            certificate_path = str(profile.get("certificate_path") or "").strip()
            key_path = str(profile.get("key_path") or "").strip()
            if (
                not certificate_path
                or not key_path
                or certificate_path == str(old_paths[0])
                or key_path == str(old_paths[1])
            ):
                raise HTTPException(
                    409,
                    "Replace the managed certificate/key paths before unbinding; "
                    "otherwise the node would lose automatic renewal while still using old material",
                )

    return data, (certificate_changed, certificate_id, previous_certificate)


def _finish_inbound_edit(inbound_id: int, core: str, certificate_plan: tuple):
    # Keep the established post-commit boundary. Certificate methods have
    # their own write transactions and acquire binding_lock before core.lock;
    # neither the guarded SQLite transaction nor core.lock may span this work.
    certificate_changed, certificate_id, previous_certificate = certificate_plan
    # The desired profile is now saved. Remove renewal before applying so a
    # failed core apply cannot leave this saved non-TLS/manual node bound.
    if certificate_changed and not certificate_id and previous_certificate:
        from app.certificates.manager import get_manager
        from app.api.certificates import perform
        perform(lambda: get_manager().unbind("inbound:" + str(inbound_id)))
    result = apply_checked(core)

    if certificate_changed and certificate_id:
        from app.certificates.manager import get_manager
        from app.api.certificates import perform
        target = "inbound:" + str(inbound_id)
        perform(lambda: get_manager().bind(certificate_id, target))
    return result


@router.put("/{inbound_id}")
def edit_inbound(
    inbound_id: int,
    payload: InboundUpdatePayload,
    db: Session = Depends(get_db),
):
    data = payload.model_dump(exclude_unset=True)
    guarded = "expected_instance_revision" in data
    expected = data.pop("expected_instance_revision", None)
    if guarded:
        updated = update_inbound_if_instance(db, inbound_id, expected,
            lambda item: _prepare_inbound_edit(item, data))
        result = _finish_inbound_edit(inbound_id, updated.core, updated.certificate_plan)
        value = json.loads(updated.inbound_json)
    else:
        # Preserve legacy callers' current-ID behavior and transaction shape.
        item = get_inbound(db, inbound_id)
        data, certificate_plan = _prepare_inbound_edit(item, data)
        updated = update_inbound(db, inbound_id, data)
        result = _finish_inbound_edit(inbound_id, updated.core, certificate_plan)
        value = to_dict(updated)

    return {
        "message": "Inbound updated",
        "inbound": value,
        "core": result,
    }


@router.delete("/{inbound_id}")
def remove_inbound(inbound_id: int, db: Session = Depends(get_db)):
    core = delete_inbound(db, inbound_id)
    try:
        from app.certificates.manager import get_manager
        get_manager().unbind("inbound:" + str(inbound_id))
    except Exception:
        pass
    return {"message": "Inbound deleted", "core": apply_checked(core)}


@router.post("/{inbound_id}/delete")
def remove_inbound_guarded(inbound_id: int, payload: InboundDeletePayload,
                           db: Session = Depends(get_db)):
    # A dedicated route fails closed on older servers; an optional DELETE
    # header/body could be silently ignored there. Never fall back to DELETE.
    core = delete_inbound_if_instance(db, inbound_id, payload.expected_instance_revision)
    try:
        from app.certificates.manager import get_manager
        get_manager().unbind("inbound:" + str(inbound_id))
    except Exception:
        pass
    return {"message": "Inbound deleted", "core": apply_checked(core)}
