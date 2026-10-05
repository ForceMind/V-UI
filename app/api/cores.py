from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.services.core_manager import CoreError, core_manager
from app.services.inbound_service import list_inbounds

router = APIRouter()


@router.get("/status")
async def get_core_status():
    return core_manager.status()


@router.post("/{core}/apply")
async def apply_core(core: str, db: Session = Depends(get_db)):
    try:
        return core_manager.apply(
            core,
            list_inbounds(db, core=core),
            restart=False,
        )
    except CoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{core}/restart")
async def restart_core(core: str, db: Session = Depends(get_db)):
    try:
        return core_manager.apply(
            core,
            list_inbounds(db, core=core),
            restart=True,
        )
    except CoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{core}/stop")
async def stop_core(core: str):
    try:
        adapter = core_manager.get(core)
        adapter.stop()
        return adapter.status()
    except CoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
