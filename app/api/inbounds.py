from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.api.cores import apply_checked
from app.services.inbound_service import create_inbound, delete_inbound, list_inbounds, to_dict, update_inbound
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


@router.get("/profiles")
def get_protocol_profiles():
    return profile_catalog()


@router.get("")
@router.get("/")
def get_all_inbounds(core: str | None = None, db: Session = Depends(get_db)):
    return [to_dict(item) for item in list_inbounds(db, core=core)]


@router.post("")
@router.post("/")
def add_inbound(payload: InboundPayload, db: Session = Depends(get_db)):
    item = create_inbound(db, payload.model_dump())
    core_status = apply_checked(item.core)
    return {"message": "Inbound added", "inbound": to_dict(item), "core": core_status}


@router.put("/{inbound_id}")
def edit_inbound(inbound_id: int, payload: InboundPayload, db: Session = Depends(get_db)):
    previous = next((item for item in list_inbounds(db) if item.id == inbound_id), None)
    if previous and payload.core != previous.core:
        raise HTTPException(409, "Cross-core migration requires a separate inbound")
    item = update_inbound(db, inbound_id, payload.model_dump())
    return {"message": "Inbound updated", "inbound": to_dict(item), "cores": {item.core: apply_checked(item.core)}}


@router.delete("/{inbound_id}")
def remove_inbound(inbound_id: int, db: Session = Depends(get_db)):
    core = delete_inbound(db, inbound_id)
    return {"message": "Inbound deleted", "core": apply_checked(core)}
