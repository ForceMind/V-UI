"""Authenticated, bounded import and draft management for server egress.

M1 deliberately has no apply endpoint: saving a draft never changes networking.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.services.proxy_store import (
    ProxyConflict, ProxyStorageError, ProxyStore, ProxyValidationError,
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
        'implementation_stage': 'draft-only',
        'apply_available': False,
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
def save_settings(payload: SettingsPayload, response: Response,
                  if_match: str | None = Header(default=None)):
    revision = expected_revision(if_match)
    values = payload.model_dump(exclude={'local_password'})
    try:
        result = get_store().save_draft(values, revision, local_password=payload.local_password)
        return snapshot_response(result, response)
    except (ProxyStorageError, ProxyConflict, ProxyValidationError, UpstreamValidationError) as exc:
        raise safe_error(exc) from None


@router.post('/profiles')
def create_profile(payload: ProfilePayload, response: Response,
                   if_match: str | None = Header(default=None)):
    revision = expected_revision(if_match)
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
