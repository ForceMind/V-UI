from __future__ import annotations
import json
from typing import Annotated, Literal
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from app.middleware.auth import expected_origin
from app.models import database
from app.services.subscription_tokens import authorized_nodes, change_grant, create_grant, list_grants
from app.services.validated_export import base64_subscription, singbox_client_config
from app.services.mihomo_subscription import mihomo_config
from app.services.mihomo_routing import load_routing

router = APIRouter()
public_router = APIRouter()
OutputFormat = Literal['mihomo.yaml','raw','sing-box.json']
NodeId = Annotated[int, Field(strict=True, gt=0)]

class GrantPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1,max_length=128)
    server: str = Field(min_length=1,max_length=253)
    inbound_ids: list[NodeId] = Field(min_length=1,max_length=256)
    formats: list[OutputFormat] = Field(default_factory=lambda:['mihomo.yaml'],min_length=1,max_length=3)
    expires_days: int = Field(default=90,ge=1,le=3650,strict=True)

class RotatePayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expires_days: int = Field(default=90,ge=1,le=3650,strict=True)


def issued_response(record, token):
    return {**record,'token':token,'paths':{fmt:f'/sub/{token}/{fmt}' for fmt in record['formats']}}

@router.get('')
def list_subscriptions(request: Request):
    return list_grants(request.state.admin['id'])

@router.post('',status_code=201)
def create_subscription(payload: GrantPayload, request: Request):
    record,token=create_grant(request.state.admin['id'],payload.label,payload.server,payload.inbound_ids,payload.formats,payload.expires_days)
    return issued_response(record,token)

@router.post('/{grant_id}/rotate')
def rotate_subscription(grant_id: int,payload: RotatePayload,request: Request):
    record,token=change_grant(request.state.admin['id'],grant_id,rotate_days=payload.expires_days)
    return issued_response(record,token)

@router.delete('/{grant_id}')
def revoke_subscription(grant_id: int,request: Request):
    return change_grant(request.state.admin['id'],grant_id)[0]

@public_router.api_route('/sub/{token}/{output_format}',methods=['GET','HEAD'],include_in_schema=False)
def read_subscription(token: str,output_format: str,request: Request):
    if expected_origin(request) is None or request.url.query:
        raise HTTPException(404,'Subscription not found')
    with database.SessionLocal() as db:
        items,server=authorized_nodes(db,token,output_format)
        try:
            if output_format=='mihomo.yaml':
                content,media=mihomo_config(items,server,load_routing()),'application/yaml'
            elif output_format=='raw':
                content,media=base64_subscription(items,server),'text/plain'
            else:
                content,media=json.dumps(singbox_client_config(items,server),ensure_ascii=False),'application/json'
        except (ValueError,OSError,TypeError,KeyError):
            raise HTTPException(409,'Subscription configuration is not ready') from None
    return Response(content='' if request.method=='HEAD' else content,media_type=media,
        headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
