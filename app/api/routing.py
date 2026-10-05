from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.mihomo_routing import (
    build_rule_plan,
    catalog,
    load_routing,
    save_routing,
)

router = APIRouter()


class IntranetZone(BaseModel):
    suffix: str
    nameservers: list[str] = Field(default_factory=list)


class MihomoRoutingPayload(BaseModel):
    mode: str = "standard"
    direct_domains: list[str] = Field(default_factory=list)
    proxy_domains: list[str] = Field(default_factory=list)
    presets: dict[str, bool] = Field(default_factory=dict)
    bypass_cgnat: bool = False
    intranet: list[IntranetZone] = Field(default_factory=list)


@router.get("/mihomo")
async def get_mihomo_routing():
    return load_routing()


@router.put("/mihomo")
async def update_mihomo_routing(payload: MihomoRoutingPayload):
    try:
        return save_routing(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/mihomo/catalog")
async def get_mihomo_catalog():
    return catalog()


@router.get("/mihomo/preview")
async def preview_mihomo_routing():
    try:
        return build_rule_plan(load_routing())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
