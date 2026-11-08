"""Authenticated certificate lifecycle API. No PEM private-key export endpoint."""
from typing import Literal
import time
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from app.certificates.manager import get_manager
from app.certificates.material import CertificateError
from app.certificates.dns_store import DNSStoreError

router = APIRouter()


class RequestCertificate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    domain: str = Field(min_length=3, max_length=253)
    email: str = Field(min_length=3, max_length=254)
    environment: Literal['staging', 'production'] = 'staging'
    auto_renew: bool = True
    accept_terms: Literal[True]
    method: Literal['http-01', 'dns-01'] = 'http-01'
    wildcard: bool = False
    credential_id: str | None = Field(default=None, pattern='^[a-f0-9]{32}$')


class RenewalSetting(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool


class BindInbound(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    inbound_id: int = Field(ge=1)
    server_name: str | None = Field(default=None, min_length=3, max_length=253)


class DNSCredentialCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    label: str = Field(min_length=1, max_length=64)
    zone_name: str = Field(min_length=3, max_length=253)
    api_token: str = Field(min_length=20, max_length=256, repr=False)


class DNSCredentialReplace(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: str = Field(pattern='^[a-f0-9]{32}$')
    api_token: str = Field(min_length=20, max_length=256, repr=False)


class DNSCredentialRevision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: str = Field(pattern='^[a-f0-9]{32}$')


class DNSCredentialEnable(DNSCredentialRevision):
    confirmed: Literal[True]


class DNSChallengeCleanup(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: str = Field(pattern='^[a-f0-9]{32}$')
    expected_profile_revision: str = Field(pattern='^[a-f0-9]{32}$')
    confirmed: Literal[True]


def _dns_admin(request):
    if getattr(request.state, 'maintenance', None) is not None:
        raise HTTPException(403, '维护授权不能管理DNS凭据；请使用管理员账户')
    user = getattr(request.state, 'admin', None)
    if type(user) is not dict or type(user.get('id')) is not int:
        raise HTTPException(401, '需要管理员登录')


def _dns_credentials():
    from app.certificates.dns_credentials import DNSCredentials
    return DNSCredentials(get_manager().root)


def _certificate_dns_write(request, certificate_id):
    if getattr(request.state, 'maintenance', None) is None:
        return
    from app.certificates.manager import get_record
    from app.models import database
    with database.SessionLocal() as db:
        row = perform(lambda: get_record(db, certificate_id))
        if row.method == 'dns-01':
            raise HTTPException(403, 'DNS证书签发和续期设置仅由管理员操作；既有维护授权未包含DNS写入')


def perform(call):
    try: return call()
    except (CertificateError, DNSStoreError) as exc:
        code = exc.code
        status = 404 if code.endswith('NOT_FOUND') else 422 if code.startswith('INVALID_') else 409
        raise HTTPException(status, code) from None


@router.get('/dns-credentials')
def dns_credentials(request: Request):
    _dns_admin(request)
    values = perform(lambda: _dns_credentials().list())
    from app.models import database
    from app.certificates.models import Certificate
    with database.SessionLocal() as db:
        for value in values:
            value['references'] = db.query(Certificate).filter_by(credential_id=value['id']).count()
    return values


@router.post('/dns-credentials', status_code=201)
def create_dns_credential(request: Request, payload: DNSCredentialCreate):
    _dns_admin(request)
    return perform(lambda: _dns_credentials().create(**payload.model_dump()))


@router.put('/dns-credentials/{credential_id}')
def replace_dns_credential(credential_id: str, request: Request, payload: DNSCredentialReplace):
    _dns_admin(request)
    return perform(lambda: _dns_credentials().replace(credential_id, **payload.model_dump()))


@router.post('/dns-credentials/{credential_id}/disable')
def disable_dns_credential(credential_id: str, request: Request, payload: DNSCredentialRevision):
    _dns_admin(request)
    return perform(lambda: _dns_credentials().set_enabled(credential_id, enabled=False, **payload.model_dump()))


@router.post('/dns-credentials/{credential_id}/enable')
def enable_dns_credential(credential_id: str, request: Request, payload: DNSCredentialEnable):
    _dns_admin(request)
    return perform(lambda: _dns_credentials().set_enabled(credential_id, enabled=True, **payload.model_dump()))


def _dns_journal():
    from app.certificates.dns_journal import DNSChallengeJournal
    return DNSChallengeJournal(get_manager().root)


@router.get('/dns-challenges')
def dns_challenges(request: Request):
    _dns_admin(request)
    return perform(lambda: _dns_journal().pending())


@router.post('/dns-challenges/{job_id}/cleanup', status_code=202)
def cleanup_dns_challenge(job_id: str, request: Request, payload: DNSChallengeCleanup):
    _dns_admin(request)
    from app.data_owner import data_owner
    manager = get_manager()
    with data_owner(manager.root.parent):
        value = perform(lambda: _dns_journal().authorize_recovery(job_id, payload.expected_revision,
            payload.expected_profile_revision, confirmed=payload.confirmed))
        manager.request_dns_cleanup()
    return {**value, 'queued': True}


@router.post('/dns-challenges/{job_id}/confirm-absence')
def confirm_dns_absence(job_id: str, request: Request, payload: DNSChallengeCleanup):
    _dns_admin(request)
    from app.data_owner import data_owner
    manager = get_manager()
    with data_owner(manager.root.parent):
        journal = _dns_journal()
        value = perform(lambda: journal.authorize_recovery(job_id, payload.expected_revision,
            payload.expected_profile_revision, confirmed=payload.confirmed))
        return perform(lambda: journal.confirm_absent(job_id, value['revision'], confirmed=True,
                                                       deadline=time.monotonic() + 60))


@router.get('')
def all_certificates():
    return get_manager().list_certificates()


@router.get('/capabilities')
def capabilities(request: Request):
    value = get_manager().capabilities()
    value['dns_credentials_manage'] = getattr(request.state, 'maintenance', None) is None
    return value


@router.post('', status_code=202)
def request_certificate(payload: RequestCertificate):
    return perform(lambda: get_manager().create(**payload.model_dump()))


@router.get('/jobs/{job_id}')
def job(job_id: str):
    return perform(lambda: get_manager().job(job_id))


@router.post('/{certificate_id}/renew', status_code=202)
def renew(certificate_id: str, request: Request):
    _certificate_dns_write(request, certificate_id)
    return perform(lambda: get_manager().renew(certificate_id))


@router.put('/{certificate_id}/auto-renew')
def auto_renew(certificate_id: str, payload: RenewalSetting, request: Request):
    _certificate_dns_write(request, certificate_id)
    return perform(lambda: get_manager().set_auto_renew(certificate_id, payload.enabled))


@router.post('/{certificate_id}/bind-panel')
def panel(certificate_id: str):
    return perform(lambda: get_manager().bind(certificate_id, 'panel'))


@router.post('/{certificate_id}/bind-inbound')
def inbound(certificate_id: str, payload: BindInbound):
    return perform(lambda: get_manager().bind(certificate_id, 'inbound:' + str(payload.inbound_id), server_name=payload.server_name))


@router.post('/{certificate_id}/apply')
def retry_apply(certificate_id: str):
    return perform(lambda: get_manager().apply_existing(certificate_id))


@router.get('/{certificate_id}/fullchain.pem')
def download_certificate(certificate_id: str):
    paths, _, _ = perform(lambda: get_manager().material(certificate_id, production=False))
    return Response(paths[0].read_bytes(), media_type='application/x-pem-file',
                    headers={'Content-Disposition': 'attachment; filename="fullchain.pem"', 'Cache-Control': 'no-store'})
