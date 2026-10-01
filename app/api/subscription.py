from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.services.inbound_service import get_inbound, list_inbounds
from app.services.mihomo_routing import load_routing
from app.services.mihomo_subscription import mihomo_config
from app.services.subscription_service import (
    base64_subscription,
    share_link,
    singbox_client_config,
)

router = APIRouter()


def _host(request: Request, host: str | None) -> str:
    return host or request.url.hostname or "127.0.0.1"


@router.get("/raw", response_class=PlainTextResponse)
async def raw_subscription(
    request: Request,
    host: str | None = Query(default=None),
    core: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return base64_subscription(list_inbounds(db, core=core), _host(request, host))


@router.get("/mihomo.yaml")
async def mihomo_subscription(
    request: Request,
    host: str | None = Query(default=None),
    core: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    content = mihomo_config(
        list_inbounds(db, core=core),
        _host(request, host),
        load_routing(),
    )
    return Response(content=content, media_type="text/yaml; charset=utf-8")


@router.get("/sing-box.json")
async def singbox_subscription(
    request: Request,
    host: str | None = Query(default=None),
    core: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    content = singbox_client_config(list_inbounds(db, core=core), _host(request, host))
    return Response(
        content=json.dumps(content, ensure_ascii=False, indent=2),
        media_type="application/json",
    )


@router.get("/link/{inbound_id}", response_class=PlainTextResponse)
async def inbound_share_link(
    inbound_id: int,
    request: Request,
    host: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    item = get_inbound(db, inbound_id)
    return share_link(item, _host(request, host)) or ""
