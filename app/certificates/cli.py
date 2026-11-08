"""Local bootstrap/recovery. No private key is returned; no root CA account runs as root."""
from __future__ import annotations
import argparse
import json
import os
import re
from pathlib import Path
import sys
from app.certificates.material import CertificateError, domain_name
from app.release_tools import ReleaseError


def ensure(manager, domain, email, *, environment='production', accept_terms=False):
    from app.models import database
    from app.certificates.models import Certificate
    domain=domain_name(domain)
    if not accept_terms: raise CertificateError('TERMS_NOT_ACCEPTED')
    with database.SessionLocal() as db:
        record=db.query(Certificate).filter_by(domain=domain,environment=environment,method='http-01',wildcard=False).first()
        identity=record.id if record else None
    if identity is None:
        job=manager.create(domain,email,environment,True,True)
        identity=job['certificate_id']
    else:
        try: job=manager.renew(identity)
        except CertificateError as exc:
            if exc.code!='RENEWAL_NOT_DUE':raise
            manager.material(identity,production=environment=='production')
            return identity
    for _ in range(33):
        state=manager.job(job['id'])
        if state['state']=='succeeded':
            manager.material(identity,production=environment=='production')
            return identity
        if state['state']=='failed':raise CertificateError(state['error'] or 'CERTIFICATE_OPERATION_FAILED')
        if not manager.process_once():raise CertificateError('CERTIFICATE_WORKER_BUSY')
    raise CertificateError('CERTIFICATE_QUEUE_NOT_DRAINED')


def bootstrap_panel(manager, domain, email, *, accept_terms=False):
    from app.models import database
    from app.certificates.models import Certificate, CertificateBinding
    domain = domain_name(domain)
    if not accept_terms: raise CertificateError('TERMS_NOT_ACCEPTED')
    existing = None
    with database.SessionLocal() as db:
        binding = db.get(CertificateBinding, 'panel')
        if binding is not None:
            if binding.certificate_id != binding.applied_certificate_id:
                raise CertificateError('PANEL_CERTIFICATE_BINDING_CONFLICT')
            applied = db.get(Certificate, binding.applied_certificate_id)
            if applied is None:
                raise CertificateError('PANEL_CERTIFICATE_BINDING_INVALID')
            if applied.method == 'dns-01':
                if (applied.environment != 'production' or binding.server_name != domain
                        or type(binding.applied_revision) is not str
                        or not re.fullmatch('[a-f0-9]{64}', binding.applied_revision)):
                    raise CertificateError('PANEL_DOMAIN_MISMATCH')
                existing = (applied.id, binding.applied_revision,
                            applied.revision != binding.applied_revision or binding.error is not None)
    if existing is not None:
        identity, revision, pending = existing
        # Material validation is independent of credential enable/restore flags.
        # Reuse the actual applied revision; never promote a pending renewal,
        # clear its error, call renew, write DNS or replace the saved binding.
        if manager.connection_domain(identity, domain) != domain:
            raise CertificateError('PANEL_DOMAIN_MISMATCH')
        manager.material(identity, revision, production=True)
        return {'certificate_id': identity, 'domain': domain, 'revision': revision,
                'used_existing': True, 'pending_apply': pending}
    identity = ensure(manager, domain, email, accept_terms=accept_terms)
    paths, _, revision = manager.material(identity)
    # Only called while the panel lease is held by this stopped CLI operation.
    # On startup the HTTPS entrypoint loads and validates these same files.
    with database.SessionLocal() as db:
        binding = db.get(CertificateBinding, 'panel')
        if binding is None:
            binding = CertificateBinding(target='panel'); db.add(binding)
        binding.certificate_id = binding.applied_certificate_id = identity
        binding.applied_revision = revision; binding.server_name = domain; binding.error = None; db.commit()
    return {'certificate_id': identity, 'domain': domain, 'revision': revision}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['bootstrap','test-issuance'])
    p.add_argument('--root',required=True,type=Path)
    p.add_argument('--domain',required=True);p.add_argument('--email',required=True)
    p.add_argument('--accept-terms',action='store_true',required=True)
    args=p.parse_args()
    from app.release_tools import private_root,writer_operation,supported_environment
    supported_environment();os.umask(0o077);root=private_root(args.root)
    os.environ['VUI_DATA_DIR']=str(root/'data')
    # The manager/database imports happen after locating the actual data directory.
    from app.certificates.manager import CertificateManager
    from app.models.database import init_db
    with writer_operation(root):
        init_db();manager=CertificateManager(root/'data'/'certificates')
        if args.command=='bootstrap':
            result=bootstrap_panel(manager,args.domain,args.email,accept_terms=args.accept_terms)
        else:
            result={'certificate_id':ensure(manager,args.domain,args.email,environment='staging',accept_terms=args.accept_terms),'environment':'staging','deployable':False}
        print(json.dumps(result))

if __name__=='__main__':
    try:main()
    except (CertificateError,ReleaseError,ValueError,OSError) as exc:
        explanations = {
            'PANEL_CERTIFICATE_BINDING_CONFLICT': '面板仍有待处理的证书切换，请先处理原绑定；未改用其他证书',
            'PANEL_CERTIFICATE_BINDING_INVALID': '面板原证书绑定不完整，已保留原状态，请核对或恢复绑定',
            'PANEL_DOMAIN_MISMATCH': '当前面板域名不在原证书覆盖范围内，已保留原绑定',
            'TERMS_NOT_ACCEPTED': '请先确认域名控制权和证书机构条款',
        }
        code = str(exc)
        detail = explanations.get(code)
        print('证书操作失败：'+(detail+'（'+code+'）' if detail else code),file=sys.stderr);raise SystemExit(1)
