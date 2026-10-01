"""Sing-box compatibility API.

The unified /api/inbounds endpoint is preferred, but these endpoints make the
second core easy to script and mirror the existing Xray API.
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


class SingBoxInboundPayload(BaseModel):
    id: int | None = None
    remark: str = ""
    port: int = Field(ge=1, le=65535)
    protocol: str
    settings: dict[str, Any] | str | None = None
    stream_settings: dict[str, Any] | str | None = None
    enable: bool = True
    expiry_time: int = 0
    tag: str | None = None


def _apply(db: Session):
    return core_manager.apply(
        "sing-box",
        list_inbounds(db, core="sing-box"),
        restart=True,
    )


@router.get("/inbounds")
async def get_inbounds(db: Session = Depends(get_db)):
    return [to_dict(item) for item in list_inbounds(db, core="sing-box")]


@router.post("/inbounds")
async def add_inbound(payload: SingBoxInboundPayload, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"id"})
    data["core"] = "sing-box"
    item = create_inbound(db, data)
    return {"message": "Inbound added", "inbound": to_dict(item), "core": _apply(db)}


@router.put("/inbounds/{inbound_id}")
async def edit_inbound(
    inbound_id: int,
    payload: SingBoxInboundPayload,
    db: Session = Depends(get_db),
):
    data = payload.model_dump(exclude={"id"})
    data["core"] = "sing-box"
    item = update_inbound(db, inbound_id, data)
    return {"message": "Inbound updated", "inbound": to_dict(item), "core": _apply(db)}


@router.delete("/inbounds/{inbound_id}")
async def remove_inbound(inbound_id: int, db: Session = Depends(get_db)):
    delete_inbound(db, inbound_id)
    return {"message": "Inbound deleted", "core": _apply(db)}


@router.post("/restart")
async def restart_singbox(db: Session = Depends(get_db)):
    return _apply(db)
