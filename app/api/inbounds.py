from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.services.core_manager import core_manager
from app.services.inbound_service import (
    create_inbound,
    delete_inbound,
    list_inbounds,
    to_dict,
    update_inbound,
)
from app.services.protocol_profiles import profile_catalog

router = APIRouter()


class InboundPayload(BaseModel):
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


def _apply_core(db: Session, core: str) -> dict:
    inbounds = list_inbounds(db, core=core)
    return core_manager.apply(core, inbounds, restart=True)


@router.get("/profiles")
async def get_protocol_profiles():
    return profile_catalog()


@router.get("")
@router.get("/")
async def get_all_inbounds(
    core: str | None = None,
    db: Session = Depends(get_db),
):
    return [to_dict(item) for item in list_inbounds(db, core=core)]


@router.post("")
@router.post("/")
async def add_inbound(payload: InboundPayload, db: Session = Depends(get_db)):
    item = create_inbound(db, payload.model_dump())
    core_status = _apply_core(db, item.core)
    return {
        "message": "Inbound added",
        "inbound": to_dict(item),
        "core": core_status,
    }


@router.put("/{inbound_id}")
async def edit_inbound(
    inbound_id: int,
    payload: InboundPayload,
    db: Session = Depends(get_db),
):
    previous = next(
        (item for item in list_inbounds(db) if item.id == inbound_id),
        None,
    )
    old_core = previous.core if previous else None
    item = update_inbound(db, inbound_id, payload.model_dump())

    statuses = {item.core: _apply_core(db, item.core)}
    if old_core and old_core != item.core:
        statuses[old_core] = _apply_core(db, old_core)

    return {
        "message": "Inbound updated",
        "inbound": to_dict(item),
        "cores": statuses,
    }


@router.delete("/{inbound_id}")
async def remove_inbound(inbound_id: int, db: Session = Depends(get_db)):
    core = delete_inbound(db, inbound_id)
    core_status = _apply_core(db, core)
    return {"message": "Inbound deleted", "core": core_status}
