"""Explicit administrator-only downloads; mount under the /api/proxy guard."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app.services.core_manager import core_manager
from app.services.proxy_export import StrictExportError, StrictRelayExporter
from app.services.proxy_store import ProxyConflict, ProxyStorageError


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


class _SafeRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def safe(request):
            try:
                raw = await request.body()
                if len(raw) > 2048:
                    raise HTTPException(413, "STRICT_EXPORT_REQUEST_TOO_LARGE")
                try:
                    json.loads(raw, object_pairs_hook=_object,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except (ValueError, UnicodeError, RecursionError):
                    raise HTTPException(422, "INVALID_STRICT_EXPORT_REQUEST") from None
                return await original(request)
            except RequestValidationError:
                raise HTTPException(422, "INVALID_STRICT_EXPORT_REQUEST") from None
        return safe


router = APIRouter(route_class=_SafeRoute)


class ExportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_revision: str = Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)
    inbound_id: int = Field(gt=0, le=9223372036854775807)
    server: str = Field(min_length=1, max_length=253)


def get_exporter():
    return StrictRelayExporter(core_manager)


def _export(kind, payload, request):
    admin = getattr(request.state, "admin", None)
    if type(admin) is not dict or type(admin.get("id")) is not int or admin["id"] <= 0:
        raise HTTPException(401, "Authentication required")
    if request.query_params:
        raise HTTPException(422, "INVALID_STRICT_EXPORT_REQUEST")
    try:
        result = get_exporter().export(kind, payload.base_revision, payload.inbound_id, payload.server)
    except ProxyConflict:
        raise HTTPException(409, "PROXY_REVISION_CONFLICT") from None
    except ProxyStorageError:
        raise HTTPException(503, "PROXY_STORAGE_UNAVAILABLE") from None
    except StrictExportError as error:
        codes = {"INVALID_STRICT_EXPORT_REQUEST": 422, "STRICT_EXPORT_PUBLIC_IP_REQUIRED": 422,
                 "STRICT_EXPORT_RELAY_NOT_ACTIVE": 409, "STRICT_EXPORT_UNQUALIFIED_INGRESS": 409,
                 "STRICT_EXPORT_INGRESS_NOT_FOUND": 409, "STRICT_EXPORT_STATE_MISMATCH": 409,
                 "STRICT_EXPORT_RUNTIME_UNAVAILABLE": 503}
        code = error.code if error.code in codes else "STRICT_EXPORT_RUNTIME_UNAVAILABLE"
        raise HTTPException(codes[code], code) from None
    except Exception:
        raise HTTPException(503, "STRICT_EXPORT_RUNTIME_UNAVAILABLE") from None
    return Response(result.content, media_type=result.media_type, headers={
        "Content-Disposition": 'attachment; filename="' + result.filename + '"',
        "ETag": '"' + result.revision + '"', "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})


@router.post("/exports/mihomo.yaml")
def export_mihomo(payload: ExportPayload, request: Request):
    return _export("mihomo", payload, request)


@router.post("/exports/sing-box.json")
def export_singbox(payload: ExportPayload, request: Request):
    return _export("sing-box", payload, request)
