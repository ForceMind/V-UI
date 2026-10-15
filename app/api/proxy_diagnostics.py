"""Authenticated M3 diagnostic jobs; mount under the existing /api/proxy guard."""
from __future__ import annotations

import json
import threading
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app.middleware.auth import configured_origin
from app.services.proxy_diagnostics import DiagnosticsError, ProxyDiagnostics, _strict_object
from app.services.proxy_store import ProxyConflict, ProxyStorageError, ProxyStore


class _SafeRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def safe(request):
            try:
                if request.method == "POST":
                    raw = await request.body()
                    if len(raw) > 2048:
                        raise HTTPException(413, "DIAGNOSTIC_REQUEST_TOO_LARGE")
                    try:
                        json.loads(raw, object_pairs_hook=_strict_object)
                    except (ValueError, UnicodeError, RecursionError):
                        raise HTTPException(422, "INVALID_DIAGNOSTIC_REQUEST") from None
                return await original(request)
            except RequestValidationError:
                # FastAPI's default validation includes the submitted value.
                raise HTTPException(422, "INVALID_DIAGNOSTIC_REQUEST") from None
        return safe


router = APIRouter(route_class=_SafeRoute)
_INSTANCE = None
_INSTANCE_LOCK = threading.Lock()


class DiagnosticPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    upstream_id: str = Field(pattern=r"^up_[0-9a-f]{32}$", min_length=35, max_length=35)
    base_revision: str = Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)


def get_diagnostics():
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = ProxyDiagnostics(ProxyStore(), panel_origin=configured_origin())
        return _INSTANCE


def close_diagnostics_if_created():
    # Do not instantiate a service merely to shut down. Keep the closed instance
    # so a late request cannot recreate background work during application exit.
    with _INSTANCE_LOCK:
        instance = _INSTANCE
    if instance is not None:
        instance.close()


def _owner(request):
    admin = getattr(request.state, "admin", None)
    if type(admin) is not dict or type(admin.get("id")) is not int or admin["id"] <= 0:
        raise HTTPException(401, "Authentication required")
    if request.query_params:
        raise HTTPException(422, "INVALID_DIAGNOSTIC_REQUEST")
    if request.method in {"GET", "DELETE"} and (
            request.headers.get("content-length", "0") != "0" or "transfer-encoding" in request.headers):
        raise HTTPException(422, "INVALID_DIAGNOSTIC_REQUEST")
    return admin["id"]


def _call(method, *args):
    try:
        return getattr(get_diagnostics(), method)(*args)
    except ProxyConflict:
        raise HTTPException(409, "PROXY_REVISION_CONFLICT") from None
    except ProxyStorageError:
        raise HTTPException(503, "PROXY_STORAGE_UNAVAILABLE") from None
    except DiagnosticsError as error:
        mapping = {"DIAGNOSTIC_BUSY": 429, "DIAGNOSTIC_RATE_LIMITED": 429,
                   "DIAGNOSTIC_NOT_FOUND": 404, "INVALID_DIAGNOSTIC_REQUEST": 422,
                   "DIAGNOSTIC_UNAVAILABLE": 503}
        code = error.code if error.code in mapping else "DIAGNOSTIC_UNAVAILABLE"
        headers = {"Retry-After": "30"} if mapping[code] == 429 else None
        raise HTTPException(mapping[code], code, headers=headers) from None
    except Exception:
        raise HTTPException(503, "DIAGNOSTIC_UNAVAILABLE") from None


@router.post("/diagnostics", status_code=202)
def start_diagnostic(payload: DiagnosticPayload, request: Request):
    return _call("start", _owner(request), payload.upstream_id, payload.base_revision)


JobId = Annotated[str, Path(pattern=r"^diag_[0-9a-f]{32}$", min_length=37, max_length=37)]


@router.get("/diagnostics/{job_id}")
def read_diagnostic(job_id: JobId, request: Request):
    return _call("get", _owner(request), job_id)


@router.delete("/diagnostics/{job_id}")
def cancel_diagnostic(job_id: JobId, request: Request):
    return _call("cancel", _owner(request), job_id)
