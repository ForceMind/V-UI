"""Authenticated, bounded import and draft management for server egress.

Draft changes stay separate from explicit checked, commit-last application.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.services.proxy_store import (
    ProxyCapacityError, ProxyConflict, ProxyStorageError, ProxyStore, ProxyValidationError,
)
from app.services.upstream_import import preview_upstreams, select_upstream
from app.services.upstream_profile import UpstreamValidationError

router = APIRouter()
FORMATS = Literal['uri', 'v2ray-json', 'clash-yaml']


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ImportPayload(StrictPayload):
    format: FORMATS
    content: str = Field(min_length=1, max_length=1_048_576)


class ProfilePayload(ImportPayload):
    candidate_index: int = Field(ge=0, le=127)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    acknowledged_ignored_sections: list[str] = Field(default_factory=list, max_length=128)


class ApplyPayload(StrictPayload):
    base_revision: str = Field(pattern=r'^[0-9a-f]{64}$', min_length=64, max_length=64)


class SettingsPayload(StrictPayload):
    schema_version: int = Field(default=1, ge=1, le=1, strict=True)
    local_proxy_enabled: bool
    relay_enabled: bool
    selected_upstream_id: str | None
    local_port: int = Field(ge=1024, le=65535)
    local_username: str = Field(max_length=64)
    local_password: str | None = Field(default=None, max_length=255)
    dns_policy_version: int = Field(default=1, ge=1, le=1, strict=True)
    deny_policy_version: int = Field(default=1, ge=1, le=1, strict=True)


def get_store() -> ProxyStore:
    return ProxyStore()


def single_header(request: Request, name: str, value: str | None) -> str | None:
    # HTTP clients/proxies may send separate physical fields instead of one
    # comma-joined value. Starlette's scalar Header otherwise selects the first.
    if len(request.headers.getlist(name)) > 1:
        raise HTTPException(422, 'INVALID_REVISION' if name == 'if-match' else 'INVALID_IDEMPOTENCY_KEY')
    return value


def expected_revision(value: str | None) -> str:
    if value is None:
        raise HTTPException(428, 'REVISION_REQUIRED')
    # Only one strong entity tag; never accept wildcard or multiple revisions.
    if len(value) == 66 and value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    if len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise HTTPException(422, 'INVALID_REVISION')
    return value


def safe_error(exc: Exception) -> HTTPException:
    # Never stringify parser/storage exceptions: input may contain credentials.
    if isinstance(exc, ProxyConflict):
        return HTTPException(409, 'PROXY_REVISION_CONFLICT')
    if isinstance(exc, ProxyCapacityError):
        return HTTPException(409, 'PROXY_CAPACITY_EXHAUSTED')
    if isinstance(exc, ProxyStorageError):
        return HTTPException(503, 'PROXY_STORAGE_UNAVAILABLE')
    return HTTPException(422, 'UPSTREAM_OR_SETTINGS_INVALID')


def snapshot_response(value: dict, response: Response) -> dict:
    response.headers['ETag'] = '"' + value['revision'] + '"'
    return value


@router.get('/capabilities')
def capabilities():
    return {
        'schema_version': 1,
        'implementation_stage': 'panel-diagnostics-exports',
        'apply_available': True,
        'diagnostics_available': True,
        'strict_exports_available': True,
        'full_system_proxy': False,
        'local_listen': '127.0.0.1',
        'qualified_ingress': ['sing-box-vless-tcp-tls'],
        'formats': ['uri', 'v2ray-json', 'clash-yaml'],
        'upstream_profiles': ['vless-tcp-tls', 'vless-tcp-reality-vision'],
        'core_version': '1.14.2',
        'application_networks': ['tcp'],
        'remote_providers': False,
        'system_proxy': False,
        'limits': {'input_bytes': 1_048_576, 'request_bytes': 2_097_152,
                   'uri_bytes': 8192, 'candidates': 128, 'depth': 16},
    }


@router.post('/import/preview')
def preview(payload: ImportPayload):
    try:
        return preview_upstreams(payload.content, source_format=payload.format).to_dict()
    except (ValueError, UnicodeError) as exc:
        raise safe_error(exc) from None


@router.get('/settings')
def read_settings(response: Response):
    try:
        return snapshot_response(get_store().read_snapshot(), response)
    except (ProxyStorageError, ProxyValidationError) as exc:
        raise safe_error(exc) from None


@router.put('/settings')
def save_settings(payload: SettingsPayload, response: Response, request: Request,
                  if_match: str | None = Header(default=None)):
    revision = expected_revision(single_header(request, 'if-match', if_match))
    values = payload.model_dump(exclude={'local_password'})
    try:
        result = get_store().save_draft(values, revision, local_password=payload.local_password)
        return snapshot_response(result, response)
    except (ProxyCapacityError, ProxyStorageError, ProxyConflict, ProxyValidationError, UpstreamValidationError) as exc:
        raise safe_error(exc) from None


@router.post('/profiles')
def create_profile(payload: ProfilePayload, response: Response, request: Request,
                   if_match: str | None = Header(default=None)):
    revision = expected_revision(single_header(request, 'if-match', if_match))
    try:
        inspection = preview_upstreams(payload.content, source_format=payload.format).to_dict()
        ignored = inspection['ignored_sections']
        acknowledged = payload.acknowledged_ignored_sections
        if (len(acknowledged) != len(set(acknowledged))
                or set(acknowledged) != set(ignored)):
            raise HTTPException(422, 'IGNORED_SECTIONS_ACKNOWLEDGEMENT_REQUIRED')
        profile = select_upstream(payload.content, selected_index=payload.candidate_index,
                                  source_format=payload.format).to_dict()
        if payload.name is not None:
            profile['name'] = payload.name
        result = get_store().save_upstream(
            profile, revision, source_format=inspection['source_format'],
            source_fingerprint=inspection['source_fingerprint'])
        response.status_code = 201
        snapshot_response(result['snapshot'], response)
        return result
    except (ValueError, UnicodeError) as exc:
        raise safe_error(exc) from None


def get_core_manager():
    from app.services.core_manager import core_manager
    return core_manager


def runtime_error(exc: Exception) -> HTTPException:
    from app.services.proxy_apply import ProxyApplyError
    from app.services.proxy_policy import PolicyError
    from app.services.proxy_resolution import ResolutionError
    if isinstance(exc, ProxyApplyError):
        return HTTPException(503 if exc.code in {'RECOVERY_REQUIRED', 'RUNTIME_MISMATCH',
                             'MISSING_ACTIVE_CONTEXT'} else 409, 'PROXY_' + exc.code)
    if isinstance(exc, PolicyError):
        return HTTPException(409, {'code': 'PROXY_POLICY_REJECTED', 'reason': exc.code,
                                   'coverage': exc.coverage})
    if isinstance(exc, ResolutionError):
        return HTTPException(422, 'PROXY_' + exc.code)
    if isinstance(exc, (ProxyCapacityError, ProxyConflict, ProxyStorageError, ProxyValidationError)):
        return safe_error(exc)
    # Do not stringify core failures, OS details, paths, or rejected input.
    return HTTPException(409, 'PROXY_RUNTIME_UNAVAILABLE')


@router.post('/check')
def check_candidate(payload: ApplyPayload, response: Response):
    from app.services.core_runtime import CoreError
    from app.services.proxy_policy import PolicyError
    from app.services.proxy_resolution import ResolutionError
    manager = get_core_manager()
    try:
        # A configuration check can bootstrap the upstream address. It is not
        # a successful TLS/authentication/HTTPS diagnostic or a live listener.
        result = manager.check_proxy(payload.base_revision)
        snapshot_response(result['snapshot'], response)
        return result
    except (CoreError, PolicyError, ResolutionError, ProxyConflict,
            ProxyCapacityError, ProxyStorageError, ProxyValidationError) as exc:
        raise runtime_error(exc) from None


@router.post('/apply')
def apply_candidate(payload: ApplyPayload, response: Response, request: Request,
                    idempotency_key: str | None = Header(default=None)):
    from app.services.core_runtime import CoreError
    from app.services.proxy_policy import PolicyError
    from app.services.proxy_resolution import ResolutionError
    idempotency_key = single_header(request, 'idempotency-key', idempotency_key)
    if idempotency_key is None:
        raise HTTPException(428, 'IDEMPOTENCY_KEY_REQUIRED')
    if (len(idempotency_key) != 64 or
            any(character not in '0123456789abcdef' for character in idempotency_key)):
        raise HTTPException(422, 'INVALID_IDEMPOTENCY_KEY')
    try:
        # Manager additionally gates stale enabled runtimes from other cores;
        # directly calling the coordinator would omit that global ingress gate.
        result = get_core_manager().apply_proxy(payload.base_revision, idempotency_key)
        if result.get('applied') is not True or result.get('phase') != 'committed':
            raise HTTPException(409, 'PROXY_TRANSACTION_NOT_ACTIVE')
        snapshot_response(result['snapshot'], response)
        return result
    except (CoreError, PolicyError, ResolutionError, ProxyConflict,
            ProxyCapacityError, ProxyStorageError, ProxyValidationError) as exc:
        raise runtime_error(exc) from None
