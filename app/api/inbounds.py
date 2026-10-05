from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.api.cores import apply_checked
from app.services.inbound_service import (
    create_inbound,
    delete_inbound,
    editor_dict,
    get_inbound,
    list_inbounds,
    to_dict,
    update_inbound,
)
from app.services.protocol_profiles import profile_catalog

router = APIRouter()


class InboundPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    core: str = "xray"
    remark: str = ""
    port: int = Field(ge=1, le=65535)
    protocol: str
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


def _managed_certificate(profile: dict | None, certificate_id: str, core: str, protocol: str):
    from app.certificates.manager import get_manager
    from app.api.certificates import perform
    if core != "sing-box" or protocol != "vless":
        raise HTTPException(409, "Managed certificates currently target sing-box VLESS/TLS")
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
def get_all_inbounds(core: str | None = None, db: Session = Depends(get_db)):
    return [to_dict(item) for item in list_inbounds(db, core=core)]


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


@router.put("/{inbound_id}")
def edit_inbound(
    inbound_id: int,
    payload: InboundUpdatePayload,
    db: Session = Depends(get_db),
):
    item = get_inbound(db, inbound_id)
    data = payload.model_dump(exclude_unset=True)
    profile = data.get("profile")
    # Match compile_profile: an empty profile is a no-op; a nonempty VLESS
    # profile defaults a missing/empty security value to none.
    leaving_tls = bool(profile) and str(profile.get("security") or "none").lower() in {"none", "reality"}
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
        target = "inbound:" + str(inbound_id)
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

    updated = update_inbound(db, inbound_id, data)
    # The desired profile is now saved. Remove renewal before applying so a
    # failed core apply cannot leave this saved non-TLS/manual node bound.
    if certificate_changed and not certificate_id and previous_certificate:
        from app.api.certificates import perform
        perform(lambda: certificate_manager.unbind("inbound:" + str(inbound_id)))
    result = apply_checked(updated.core)

    if certificate_changed:
        from app.api.certificates import perform
        target = "inbound:" + str(inbound_id)
        if certificate_id:
            perform(lambda: certificate_manager.bind(certificate_id, target))

    return {
        "message": "Inbound updated",
        "inbound": to_dict(updated),
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
