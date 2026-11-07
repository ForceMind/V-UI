from __future__ import annotations

import json
from typing import Any, Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.api.cores import apply_checked, maintenance_result
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
from app.services.protocol_profiles import decompile_profile, profile_catalog
from app.services.reality_profile import has_reality
from app.services.node_selection_revision import NODE_REVISION_HEADER
from app.services.managed_tls import supports_profile

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
    expected_proxy_revision: str = Field(default=None, strict=True, min_length=64,
        max_length=64, pattern=r'^[0-9a-f]{64}$')


class InboundUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    remark: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    profile: dict[str, Any] | None = None
    enable: bool | None = None
    expiry_time: int | None = None
    tag: str | None = None
    certificate_id: str | None = None
    expected_proxy_revision: str = Field(default=None, strict=True, min_length=64,
        max_length=64, pattern=r'^[0-9a-f]{64}$')
    expected_content_revision: str = Field(default=None, strict=True, min_length=64,
        max_length=64, pattern=r'^[0-9a-f]{64}$')
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
    from app.certificates.material import CertificateError, domain_name
    from app.api.certificates import perform
    if not supports_profile(core, protocol, profile):
        raise HTTPException(409, "此协议或传输暂不支持托管证书；Xray 仅支持 VLESS RAW/TCP TLS")
    if profile and "security" in profile and str(profile["security"]).lower() != "tls":
        raise HTTPException(409, "托管证书只能用于 TLS；切换安全方式时请取消证书绑定")
    server_name = (profile or {}).get("server_name", "")
    if not isinstance(server_name, str):
        raise HTTPException(422, "SNI 必须是域名字符串")
    paths, domain, _ = perform(lambda: get_manager().material(certificate_id))
    if server_name:
        try:
            matches = domain_name(server_name) == domain_name(domain)
        except CertificateError:
            raise HTTPException(422, "SNI 格式无效，请填写有效域名") from None
        if not matches:
            raise HTTPException(409, "SNI 与所选证书域名不匹配，请核对后保存")
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
def get_inbound_editor(inbound_id: int, request: Request = None, db: Session = Depends(get_db)):
    item = get_inbound(db, inbound_id)
    value = editor_dict(item)
    try:
        from app.certificates.manager import get_manager
        value["certificate_id"] = get_manager().binding("inbound:" + str(item.id))
    except Exception:
        # Certificate storage availability must not hide a node from the editor.
        value["certificate_id"] = None
        value["certificate_binding_unavailable"] = True
    if getattr(getattr(request, 'state', None), 'maintenance', None) is not None:
        # Remote editing uses certificate_id and preserves existing manual
        # bindings; the filesystem layout is not part of delegated authority.
        value.get('profile', {}).pop('certificate_path', None)
        value.get('profile', {}).pop('key_path', None)
    return value


@router.post("")
@router.post("/")
def add_inbound(payload: InboundPayload, request: Request = None, db: Session = Depends(get_db)):
    expected_proxy = payload.expected_proxy_revision
    conditions = {} if expected_proxy is None else {'expected_proxy_revision': expected_proxy}
    data = payload.model_dump(exclude={'expected_proxy_revision'})
    if payload.certificate_id:
        if has_reality(normalize_mapping(payload.stream_settings)):
            raise HTTPException(409, "REALITY 不能绑定托管证书")
        data["profile"] = _managed_certificate(
            payload.profile, payload.certificate_id, payload.core, payload.protocol
        )
    item = create_inbound(db, data, **conditions)
    core_status = apply_checked(item.core, **conditions)
    if payload.certificate_id:
        from app.certificates.manager import get_manager
        from app.api.certificates import perform
        perform(lambda: get_manager().bind(payload.certificate_id, "inbound:" + str(item.id)))
    return {"message": "Inbound added", "inbound": to_dict(item), "core": maintenance_result(request, core_status)}


def _prepare_inbound_edit(item, data: dict, *, allow_certificate_changes=True, preserve_material_paths=False) -> tuple[dict, tuple[bool, str | None, str | None]]:
    """Read-only preparation; guarded callers already checked this model."""
    data = dict(data)
    profile = data.get("profile")
    if (data.get("certificate_id") and has_reality(item.stream_settings)
            and (not isinstance(profile, dict) or profile.get("security") != "tls")):
        raise HTTPException(409, "请先明确切换为 TLS，再绑定托管证书")
    # Match compile_profile: {} is a no-op; implicit security is protocol-aware.
    # In particular, Trojan remains TLS when security is missing/empty/null.
    default_security = "tls" if item.core == "sing-box" and item.protocol in {"hysteria2", "tuic", "trojan"} else "none"
    if (preserve_material_paths and isinstance(profile, dict)
            and str(profile.get('security') or default_security).lower() == 'tls'):
        # Delegated clients cannot read/supply material paths. Reuse only this
        # guarded instance's saved TLS material inside the write transaction.
        previous = decompile_profile(item.core or 'xray', item.protocol,
            item.settings, item.stream_settings)
        if previous.get('security') == 'tls':
            profile = {**profile, **{key: previous[key] for key in ('certificate_path','key_path')
                if key in previous}}
            data['profile'] = profile
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
        if previous_certificate and not allow_certificate_changes:
            # Guarded edits hold BEGIN IMMEDIATE here: check the actual binding
            # with the same write authority as the node instance precondition.
            raise HTTPException(403, '修改已有托管证书绑定需要证书管理权限')
        if certificate_id:
            data["profile"] = _managed_certificate(
                data.get("profile"), certificate_id, data["core"], data["protocol"]
            )
        elif previous_certificate and not leaving_tls:
            profile = data.get("profile")
            if not isinstance(profile, dict):
                raise HTTPException(
                    409,
                    "取消自动续期前，请填写新的手工证书和私钥路径",
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
                    "取消绑定前，请替换托管证书和私钥路径，避免继续使用旧材料却失去自动续期",
                )

    return data, (certificate_changed, certificate_id, previous_certificate)


def _finish_inbound_edit(inbound_id: int, core: str, certificate_plan: tuple, *, expected_proxy_revision=None):
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
    conditions = {} if expected_proxy_revision is None else {'expected_proxy_revision': expected_proxy_revision}
    result = apply_checked(core, **conditions)

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
    request: Request = None,
    db: Session = Depends(get_db),
):
    data = payload.model_dump(exclude_unset=True)
    expected_proxy = data.pop('expected_proxy_revision', None)
    conditions = {} if expected_proxy is None else {'expected_proxy_revision': expected_proxy}
    guarded = "expected_instance_revision" in data
    expected = data.pop("expected_instance_revision", None)
    content_condition = data.pop('expected_content_revision', None)
    if content_condition is not None and not guarded:
        raise HTTPException(422, 'Content condition requires an instance condition')
    guard_conditions = {**conditions, **({} if content_condition is None else {'expected_content_revision': content_condition})}
    principal = getattr(getattr(request, 'state', None), 'maintenance', None)
    allow_certificates = principal is None or 'certificates' in principal['scopes']
    if guarded:
        updated = update_inbound_if_instance(db, inbound_id, expected,
            lambda item: _prepare_inbound_edit(item, data, allow_certificate_changes=allow_certificates,
                preserve_material_paths=principal is not None), **guard_conditions)
        result = _finish_inbound_edit(inbound_id, updated.core, updated.certificate_plan, **conditions)
        value = json.loads(updated.inbound_json)
    else:
        # Preserve legacy callers' current-ID behavior and transaction shape.
        item = get_inbound(db, inbound_id)
        data, certificate_plan = _prepare_inbound_edit(item, data, allow_certificate_changes=allow_certificates,
            preserve_material_paths=principal is not None)
        updated = update_inbound(db, inbound_id, data, **conditions)
        result = _finish_inbound_edit(inbound_id, updated.core, certificate_plan, **conditions)
        value = to_dict(updated)

    return {
        "message": "Inbound updated",
        "inbound": value,
        "core": maintenance_result(request, result),
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
