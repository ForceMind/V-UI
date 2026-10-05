"""Authenticated certificate lifecycle API. No PEM private-key export endpoint."""
from typing import Literal
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from app.certificates.manager import get_manager
from app.certificates.material import CertificateError

router = APIRouter()


class RequestCertificate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    domain: str = Field(min_length=3, max_length=253)
    email: str = Field(min_length=3, max_length=254)
    environment: Literal['staging', 'production'] = 'staging'
    auto_renew: bool = True
    accept_terms: Literal[True]


class RenewalSetting(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool


class BindInbound(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    inbound_id: int = Field(ge=1)


def perform(call):
    try: return call()
    except CertificateError as exc:
        code = exc.code
        status = 404 if code.endswith('NOT_FOUND') else 422 if code.startswith('INVALID_') else 409
        raise HTTPException(status, code) from None


@router.get('')
def all_certificates():
    return get_manager().list_certificates()


@router.get('/capabilities')
def capabilities():
    return get_manager().capabilities()


@router.post('', status_code=202)
def request_certificate(payload: RequestCertificate):
    return perform(lambda: get_manager().create(**payload.model_dump()))


@router.get('/jobs/{job_id}')
def job(job_id: str):
    return perform(lambda: get_manager().job(job_id))


@router.post('/{certificate_id}/renew', status_code=202)
def renew(certificate_id: str):
    return perform(lambda: get_manager().renew(certificate_id))


@router.put('/{certificate_id}/auto-renew')
def auto_renew(certificate_id: str, payload: RenewalSetting):
    return perform(lambda: get_manager().set_auto_renew(certificate_id, payload.enabled))


@router.post('/{certificate_id}/bind-panel')
def panel(certificate_id: str):
    return perform(lambda: get_manager().bind(certificate_id, 'panel'))


@router.post('/{certificate_id}/bind-inbound')
def inbound(certificate_id: str, payload: BindInbound):
    return perform(lambda: get_manager().bind(certificate_id, 'inbound:' + str(payload.inbound_id)))


@router.post('/{certificate_id}/apply')
def retry_apply(certificate_id: str):
    return perform(lambda: get_manager().apply_existing(certificate_id))


@router.get('/{certificate_id}/fullchain.pem')
def download_certificate(certificate_id: str):
    paths, _, _ = perform(lambda: get_manager().material(certificate_id, production=False))
    return Response(paths[0].read_bytes(), media_type='application/x-pem-file',
                    headers={'Content-Disposition': 'attachment; filename="fullchain.pem"', 'Cache-Control': 'no-store'})
