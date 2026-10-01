from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from app.services.mihomo_routing import catalog
from app.services.routing_validation import build_rule_plan
from app.services.routing_store import read_snapshot, save_routing, RoutingConflict, RoutingStorageError

router = APIRouter()
Target = Annotated[str, StringConstraints(max_length=4096)]

class IntranetZone(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    suffix: Target
    nameservers: list[Target] = Field(min_length=1, max_length=16)

class MihomoRoutingPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    mode: str = Field(default='standard', max_length=16)
    direct_domains: list[Target] = Field(default_factory=list, max_length=2048)
    proxy_domains: list[Target] = Field(default_factory=list, max_length=2048)
    presets: dict[str, bool] = Field(default_factory=dict)
    bypass_cgnat: bool = False
    intranet: list[IntranetZone] = Field(default_factory=list, max_length=256)

def snapshot():
    try:
        return read_snapshot()
    except RoutingStorageError as exc:
        raise HTTPException(503, str(exc)) from None

@router.get('/mihomo')
def get_settings(response: Response):
    value = snapshot()
    response.headers['ETag'] = '"' + value['revision'] + '"'
    return value['settings']

@router.get('/mihomo/snapshot')
def get_snapshot():
    return snapshot()

@router.put('/mihomo')
def update_settings(payload: MihomoRoutingPayload, response: Response,
                    if_match: str | None = Header(default=None)):
    if if_match is None:
        raise HTTPException(428, 'Read the saved revision and send If-Match before writing')
    try:
        value = save_routing(payload.model_dump(), if_match.strip('"'))
    except RoutingConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except RoutingStorageError as exc:
        raise HTTPException(503, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    response.headers['ETag'] = '"' + value['revision'] + '"'
    return value['settings']

@router.get('/mihomo/catalog')
def get_catalog():
    return catalog()

@router.get('/mihomo/preview')
def saved_preview():
    value = snapshot()
    return {**build_rule_plan(value['settings']), 'source': value['source'], 'revision': value['revision']}

@router.post('/mihomo/preview')
def draft_preview(payload: MihomoRoutingPayload):
    try:
        return {**build_rule_plan(payload.model_dump()), 'source': 'draft'}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
