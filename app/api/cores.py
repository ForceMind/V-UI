from fastapi import APIRouter, HTTPException
from app.services.core_manager import CoreError, core_manager
from app.services.proxy_store import ProxyCapacityError

router = APIRouter()

@router.get("/status")
def status():
    return core_manager.status()


def apply_checked(core: str, activate=True):
    try:
        return core_manager.apply_database(core, activate=activate)
    except ProxyCapacityError:
        raise HTTPException(409, {"saved": True, "applied": False,
            "code": "PROXY_CAPACITY_EXHAUSTED",
            "message": "Proxy history capacity is full; the existing runtime is unchanged"}) from None
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
        return core_manager.stop(core)
    except CoreError as exc:
        raise HTTPException(409, str(exc)) from exc
