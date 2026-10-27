"""Explicit, authenticated read-only YAML inspection; no store or core access.

Mounted under /api/proxy/ to inherit the existing auth, exact-origin, bounded
body, fixed validation errors and no-store contract. Original text is transient.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

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
        unavailable = {'PARSE_TIMEOUT', 'PARSER_UNAVAILABLE', 'PARSER_FAILED',
                       'PARSER_RESOURCE_LIMIT', 'PARSER_LIMIT_UNAVAILABLE'}
        status = 429 if error.code == 'PARSER_BUSY' else 503 if error.code in unavailable else 422
        raise HTTPException(status, {'code': error.code, 'source': error.source}) from None
    return {'schema_version': 1, 'can_apply': False, 'text': text}
