"""Explicit, authenticated read-only YAML inspection; no store or core access.

Mounted under /api/proxy/ to inherit the existing auth, exact-origin, bounded
body, fixed validation errors and no-store contract. Original text is transient.
"""
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.services.mihomo_merge import MihomoMergeError
from app.services.mihomo_merge_download import (
    download_mihomo_merge, review_mihomo_download, reviewed_mihomo_download,
)
from app.services.mihomo_preview import MihomoPreviewError, preview_mihomo
from app.services.mihomo_preview_report import render_preview_text

router = APIRouter()


class PreviewPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    candidate: str = Field(min_length=1, max_length=1_048_576)
    baseline: str | None = Field(default=None, min_length=1, max_length=1_048_576)


@router.post('/yaml/preview')
def inspect_yaml(payload: PreviewPayload):
    try:
        report = preview_mihomo(payload.candidate, payload.baseline)
        text = render_preview_text(report)
    except MihomoPreviewError as error:
        raise _parser_error(error) from None
    return {'schema_version': 1, 'can_apply': False, 'text': text}


def _parser_error(error):
    unavailable = {'PARSE_TIMEOUT', 'PARSER_UNAVAILABLE', 'PARSER_FAILED',
                       'PARSER_RESOURCE_LIMIT', 'PARSER_LIMIT_UNAVAILABLE'}
    status = 429 if error.code == 'PARSER_BUSY' else 503 if error.code in unavailable else 422
    return HTTPException(status, {'code': error.code, 'source': error.source})


class MergeChoice(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    path: list[str] = Field(min_length=1, max_length=16)
    side: Literal['baseline', 'candidate']


class MergeReviewPayload(PreviewPayload):
    baseline: str = Field(min_length=1, max_length=1_048_576)


class MergeDownloadPayload(MergeReviewPayload):
    fingerprint: str | None = Field(default=None, pattern='^[a-f0-9]{64}$')
    selections: list[Literal['baseline', 'candidate']] | None = Field(default=None, max_length=256)
    choices: list[MergeChoice] = Field(default_factory=list, max_length=256)


@router.post('/yaml/merge/download')
def download_yaml(payload: MergeDownloadPayload, request: Request):
    if getattr(request.state, 'admin', None) is None or getattr(request.state, 'maintenance', None) is not None:
        raise HTTPException(403, {'code': 'ADMIN_REQUIRED'})
    choices = {tuple(choice.path): choice.side for choice in payload.choices}
    if len(choices) != len(payload.choices):
        raise HTTPException(422, {'code': 'MERGE_INVALID_CHOICES'})
    try:
        if payload.fingerprint is not None or payload.selections is not None:
            if payload.fingerprint is None or payload.selections is None or choices:
                raise MihomoMergeError('MERGE_INVALID_CHOICES')
            raw = reviewed_mihomo_download(payload.baseline, payload.candidate,
                                           payload.fingerprint, payload.selections)
        else:
            raw = download_mihomo_merge(payload.baseline, payload.candidate, choices)
    except MihomoPreviewError as error:
        raise _parser_error(error) from None
    except MihomoMergeError as error:
        raise HTTPException(422, {'code': error.code}) from None
    return Response(raw, media_type='application/yaml', headers={
        'Content-Disposition': 'attachment; filename="vui-merged.yaml"',
    })


@router.post('/yaml/merge/review')
def review_yaml_merge(payload: MergeReviewPayload, request: Request):
    if getattr(request.state, 'admin', None) is None or getattr(request.state, 'maintenance', None) is not None:
        raise HTTPException(403, {'code': 'ADMIN_REQUIRED'})
    try:
        return review_mihomo_download(payload.baseline, payload.candidate)
    except MihomoPreviewError as error:
        raise _parser_error(error) from None
    except MihomoMergeError as error:
        raise HTTPException(422, {'code': error.code}) from None
