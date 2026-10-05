from fastapi import APIRouter, HTTPException
from app.services.core_manager import CoreError, core_manager

router = APIRouter()

@router.get("/status")
def status():
    return core_manager.status()


def apply_checked(core: str, activate=True):
    try:
        return core_manager.apply_database(core, activate=activate)
    except CoreError as exc:
        raise HTTPException(409, {"saved": True, "applied": False, "message": str(exc)}) from exc


@router.post("/{core}/apply")
def validate(core: str):
    return apply_checked(core, activate=False)


@router.post("/{core}/restart")
def restart(core: str):
    return apply_checked(core)


@router.post("/{core}/stop")
def stop(core: str):
    try:
        core_manager.get(core).stop()
        return core_manager.get(core).status()
    except CoreError as exc:
        raise HTTPException(409, str(exc)) from exc
