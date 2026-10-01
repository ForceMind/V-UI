"""Backward-compatible Xray API.

New code should use /api/inbounds and /api/cores. The old paths stay available
so existing V-UI frontends and scripts do not break during the migration.
"""

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

router = APIRouter()


class LegacyInboundPayload(BaseModel):
    id: int | None = None
    remark: str = ""
    port: int = Field(ge=1, le=65535)
    protocol: str
    settings: dict[str, Any] | str | None = None
    stream_settings: dict[str, Any] | str | None = None
    enable: bool = True
    expiry_time: int = 0
    total_traffic: int = 0
    tag: str | None = None


def _apply(db: Session):
    return core_manager.apply(
        "xray",
        list_inbounds(db, core="xray"),
        restart=True,
    )


@router.get("/inbounds")
async def get_inbounds(db: Session = Depends(get_db)):
    return [to_dict(item) for item in list_inbounds(db, core="xray")]


@router.post("/inbounds")
async def add_inbound(payload: LegacyInboundPayload, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"id", "total_traffic"})
    data["core"] = "xray"
    item = create_inbound(db, data)
    return {"message": "Inbound added", "inbound": to_dict(item), "core": _apply(db)}


@router.put("/inbounds/{inbound_id}")
async def edit_inbound(
    inbound_id: int,
    payload: LegacyInboundPayload,
    db: Session = Depends(get_db),
):
    data = payload.model_dump(exclude={"id", "total_traffic"})
    data["core"] = "xray"
    item = update_inbound(db, inbound_id, data)
    return {"message": "Inbound updated", "inbound": to_dict(item), "core": _apply(db)}


@router.delete("/inbounds/{inbound_id}")
async def remove_inbound(inbound_id: int, db: Session = Depends(get_db)):
    delete_inbound(db, inbound_id)
    return {"message": "Inbound deleted", "core": _apply(db)}


@router.post("/restart")
async def restart_xray(db: Session = Depends(get_db)):
    return _apply(db)
