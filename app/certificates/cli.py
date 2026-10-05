"""Local bootstrap/recovery. No private key is returned; no root CA account runs as root."""
from __future__ import annotations
import argparse
import json
import os
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
        record=db.query(Certificate).filter_by(domain=domain,environment=environment).first()
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
    from app.certificates.models import CertificateBinding
    identity=ensure(manager,domain,email,accept_terms=accept_terms)
    paths,_,revision=manager.material(identity)
    # Only called while the panel lease is held by this stopped CLI operation.
    # On startup the HTTPS entrypoint loads and validates these same files.
    with database.SessionLocal() as db:
        binding=db.get(CertificateBinding,'panel')
        if binding is None:
            binding=CertificateBinding(target='panel');db.add(binding)
        binding.certificate_id=binding.applied_certificate_id=identity
        binding.applied_revision=revision;binding.error=None;db.commit()
    return {'certificate_id':identity,'domain':domain,'revision':revision}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['bootstrap','test-issuance'])
    p.add_argument('--root',required=True,type=Path)
    p.add_argument('--domain',required=True);p.add_argument('--email',required=True)
    p.add_argument('--accept-terms',action='store_true',required=True)
    args=p.parse_args()
    from app.release_tools import private_root,stopped,supported_environment
    supported_environment();os.umask(0o077);root=private_root(args.root)
    os.environ['VUI_DATA_DIR']=str(root/'data')
    # The manager/database imports happen after locating the actual data directory.
    from app.certificates.manager import CertificateManager
    from app.models.database import init_db
    with stopped(root):
        init_db();manager=CertificateManager(root/'data'/'certificates')
        if args.command=='bootstrap':
            result=bootstrap_panel(manager,args.domain,args.email,accept_terms=args.accept_terms)
        else:
            result={'certificate_id':ensure(manager,args.domain,args.email,environment='staging',accept_terms=args.accept_terms),'environment':'staging','deployable':False}
        print(json.dumps(result))

if __name__=='__main__':
    try:main()
    except (CertificateError,ReleaseError,ValueError,OSError) as exc:
        print('Certificate operation failed: '+str(exc),file=sys.stderr);raise SystemExit(1)
