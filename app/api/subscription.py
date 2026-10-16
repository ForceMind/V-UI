"""Authenticated legacy export endpoints. Public clients use scoped /sub/ grants."""
from __future__ import annotations
import asyncio
import json
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import ClientDisconnect
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.services.validated_export import base64_subscription, export_warnings, share_link, singbox_client_config
from app.services.inbound_service import get_inbound, list_inbounds
from app.services.routing_store import load_routing
from app.services.mihomo_subscription import mihomo_config
from app.services.tcp_upstream_export import (
    INVALID_REQUEST, PROFILE, TCPUpstreamExportError, export_tcp_upstream,
)

router = APIRouter()


def _host(request: Request, host: str | None) -> str:
    # Legacy administrator-only preview. Client subscriptions always use the
    # grant's explicitly configured server address, not this request Host.
    return host or request.url.hostname or '127.0.0.1'


@router.get('/raw', response_class=PlainTextResponse)
def raw_subscription(request: Request, host: str | None = Query(default=None), core: str | None = Query(default=None), db: Session = Depends(get_db)):
    return base64_subscription(list_inbounds(db, core=core), _host(request, host))


@router.get('/mihomo.yaml')
def mihomo_subscription(request: Request, host: str | None = Query(default=None), core: str | None = Query(default=None), db: Session = Depends(get_db)):
    return Response(content=mihomo_config(list_inbounds(db, core=core),_host(request,host),load_routing()),media_type='application/yaml')


@router.get('/mihomo-warnings')
def mihomo_warnings(core: str | None = Query(default=None), db: Session = Depends(get_db)):
    return export_warnings(list_inbounds(db, core=core))


@router.get('/sing-box.json')
def singbox_subscription(request: Request, host: str | None = Query(default=None), core: str | None = Query(default=None), db: Session = Depends(get_db)):
    return Response(content=json.dumps(singbox_client_config(list_inbounds(db,core=core),_host(request,host)),ensure_ascii=False),media_type='application/json')


@router.get('/link/{inbound_id}', response_class=PlainTextResponse)
def inbound_share_link(inbound_id: int, request: Request, host: str | None = Query(default=None), db: Session = Depends(get_db)):
    return share_link(get_inbound(db,inbound_id),_host(request,host))


def _unique_json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


class _TCPUpstreamRoute(APIRoute):
    """Bound and sanitize only the new explicit-purpose endpoint."""
    def get_route_handler(self):
        original = super().get_route_handler()

        async def safe(request):
            async def read_body():
                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 2048:
                        raise HTTPException(413, "TCP_UPSTREAM_EXPORT_REQUEST_TOO_LARGE")
                    body.extend(chunk)
                return bytes(body)

            try:
                if (request.query_params or request.headers.get("content-type", "")
                        .split(";", 1)[0].strip().lower() != "application/json"):
                    raise HTTPException(422, INVALID_REQUEST)
                raw = await asyncio.wait_for(read_body(), timeout=10)
                json.loads(raw, object_pairs_hook=_unique_json_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                # Preserve Starlette's normal cached-body API for FastAPI parsing.
                request._body = raw
                return await original(request)
            except asyncio.TimeoutError:
                raise HTTPException(408, "TCP_UPSTREAM_EXPORT_REQUEST_TIMED_OUT") from None
            except ClientDisconnect:
                raise HTTPException(400, "TCP_UPSTREAM_EXPORT_REQUEST_DISCONNECTED") from None
            except (RequestValidationError, ValueError, UnicodeError, RecursionError):
                raise HTTPException(422, INVALID_REQUEST) from None
        return safe


upstream_router = APIRouter(route_class=_TCPUpstreamRoute)


class TCPUpstreamExportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    format: Literal["uri", "clash-yaml"]
    server: str = Field(min_length=1, max_length=253)
    acknowledge_tcp_only: bool


@upstream_router.post("/upstream/{inbound_id}")
def inbound_tcp_upstream_export(
    payload: TCPUpstreamExportPayload, request: Request,
    inbound_id: int = Path(gt=0, le=9223372036854775807), db: Session = Depends(get_db),
):
    # In addition to the existing admin-session and same-origin POST middleware,
    # keep this purpose explicitly unavailable to subscription-grant principals.
    admin = getattr(request.state, "admin", None)
    if type(admin) is not dict or type(admin.get("id")) is not int or admin["id"] <= 0:
        raise HTTPException(401, "Authentication required")
    if payload.acknowledge_tcp_only is not True:
        raise HTTPException(422, INVALID_REQUEST)
    try:
        item = get_inbound(db, inbound_id)
    except HTTPException as error:
        if error.status_code == 404:
            raise HTTPException(404, "TCP_UPSTREAM_INBOUND_NOT_FOUND") from None
        raise HTTPException(503, "TCP_UPSTREAM_EXPORT_UNAVAILABLE") from None
    except Exception:
        raise HTTPException(503, "TCP_UPSTREAM_EXPORT_UNAVAILABLE") from None
    try:
        result = export_tcp_upstream(item, payload.server, payload.format)
    except TCPUpstreamExportError as error:
        raise HTTPException(422 if error.code == INVALID_REQUEST else 409, error.code) from None
    except Exception:
        raise HTTPException(503, "TCP_UPSTREAM_EXPORT_UNAVAILABLE") from None
    return Response(result.content, media_type=result.media_type, headers={
        "Cache-Control": "no-store", "X-VUI-Export-Profile": PROFILE,
        "Content-Disposition": 'attachment; filename="' + result.filename + '"',
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
    })


router.include_router(upstream_router)
