"""Legacy sing-box routes are scoped to sing-box rows."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from app.api.cores import apply_checked
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.services.inbound_service import create_inbound, delete_inbound, get_inbound, list_inbounds, to_dict, update_inbound

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


def _check_scope(db, inbound_id):
    if get_inbound(db, inbound_id).core != "sing-box":
        raise HTTPException(404, "Inbound not found for this core")

@router.get("/inbounds")
def get_inbounds(db: Session = Depends(get_db)):
    return [to_dict(item) for item in list_inbounds(db, core="sing-box")]

@router.post("/inbounds")
def add_inbound(payload: SingBoxInboundPayload, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"id"})
    data["core"] = "sing-box"
    item = create_inbound(db, data)
    return {"message": "Inbound added", "inbound": to_dict(item), "core": apply_checked("sing-box")}

@router.put("/inbounds/{inbound_id}")
def edit_inbound(inbound_id: int, payload: SingBoxInboundPayload, db: Session = Depends(get_db)):
    _check_scope(db, inbound_id)
    data = payload.model_dump(exclude={"id"})
    data["core"] = "sing-box"
    item = update_inbound(db, inbound_id, data)
    return {"message": "Inbound updated", "inbound": to_dict(item), "core": apply_checked("sing-box")}

@router.delete("/inbounds/{inbound_id}")
def remove_inbound(inbound_id: int, db: Session = Depends(get_db)):
    _check_scope(db, inbound_id)
    delete_inbound(db, inbound_id)
    return {"message": "Inbound deleted", "core": apply_checked("sing-box")}

@router.post("/restart")
def restart_singbox():
    return apply_checked("sing-box")
