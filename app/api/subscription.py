"""Authenticated legacy export endpoints. Public clients use scoped /sub/ grants."""
from __future__ import annotations
import json
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.services.validated_export import base64_subscription, export_warnings, share_link, singbox_client_config
from app.services.inbound_service import get_inbound, list_inbounds
from app.services.routing_store import load_routing
from app.services.mihomo_subscription import mihomo_config

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
