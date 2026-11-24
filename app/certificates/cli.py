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


def ensure(manager, domain, email, *, environment='production', accept_terms=False,
           method='http-01', credential_id=None):
    from app.models import database
    from app.certificates.models import Certificate
    domain=domain_name(domain)
    if not accept_terms: raise CertificateError('TERMS_NOT_ACCEPTED')
    if method not in ('http-01', 'dns-01'):
        raise CertificateError('INVALID_CERTIFICATE_METHOD')
    with database.SessionLocal() as db:
        record=db.query(Certificate).filter_by(domain=domain,environment=environment,method=method,wildcard=False).first()
        identity=record.id if record else None
        if record is not None and credential_id is not None and record.credential_id != credential_id:
            raise CertificateError('DNS_CREDENTIAL_REVISION_CONFLICT')
    if identity is None:
        job=manager.create(domain,email,environment,True,True,method=method,credential_id=credential_id)
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
        if not manager.process_once():
            state = manager.job(job['id'])
            if state['state'] == 'failed':
                raise CertificateError(state['error'] or 'CERTIFICATE_OPERATION_FAILED')
            raise CertificateError('CERTIFICATE_WORKER_BUSY')
    raise CertificateError('CERTIFICATE_QUEUE_NOT_DRAINED')


def applied_dns_binding(manager, domain):
    from app.models import database
    from app.certificates.models import Certificate, CertificateBinding
    domain = domain_name(domain)
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
    return None


def dns_bootstrap_status(manager, domain):
    """Private local protocol: decide whether a first install needs a token."""
    from app.models import database
    from app.certificates.models import Certificate, CertificateBinding
    domain = domain_name(domain)
    if applied_dns_binding(manager, domain) is not None:
        return {'version': 1, 'needs_token': False}
    with database.SessionLocal() as db:
        if db.get(CertificateBinding, 'panel') is not None:
            # The applied binding is authoritative after a deliberate panel
            # change; a saved first-install DNS choice does not replace it.
            return {'version': 1, 'needs_token': False}
        record = db.query(Certificate).filter_by(domain=domain, environment='production',
                                                method='dns-01', wildcard=False).first()
        return {'version': 1, 'needs_token': record is None}


def private_dns_request(stream):
    """Read one bounded request; no token argument/environment is accepted."""
    try:
        raw = stream.read(4097)
        if type(raw) is not str or len(raw.encode('utf-8')) > 4096:
            raise ValueError()
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result: raise ValueError()
                result[key] = value
            return result
        request = json.loads(raw, object_pairs_hook=unique)
        shapes = ({'version'}, {'version', 'zone_name', 'api_token'},
                  {'version', 'action', 'credential_id', 'expected_profile_revision', 'api_token'},
                  {'version', 'action', 'credential_id', 'expected_profile_revision'},
                  {'version', 'action', 'credential_id', 'expected_profile_revision', 'job_id', 'expected_revision'})
        if (type(request) is not dict or type(request.get('version')) is not int
                or request['version'] != 1 or set(request) not in shapes):
            raise ValueError()
        if any(type(value) is not str for key, value in request.items() if key != 'version'):
            raise ValueError()
        return request
    except (ValueError, UnicodeError):
        raise CertificateError('DNS_BOOTSTRAP_REQUEST_INVALID') from None


def dns_bootstrap_recovery_status(manager, domain):
    """Only an unbound first-install identity can use local recovery authority."""
    from app.models import database
    from app.certificates.models import Certificate, CertificateBinding
    from app.certificates.dns_credentials import DNSCredentials
    domain = domain_name(domain)
    with database.SessionLocal() as db:
        if db.get(CertificateBinding, 'panel') is not None:
            raise CertificateError('DNS_BOOTSTRAP_RECOVERY_UNAVAILABLE')
        record = db.query(Certificate).filter_by(domain=domain, environment='production',
                                                method='dns-01', wildcard=False).first()
        if record is None:
            raise CertificateError('DNS_BOOTSTRAP_RECOVERY_UNAVAILABLE')
        credential_id = record.credential_id
    profile = next((item for item in DNSCredentials(manager.root).list() if item['id'] == credential_id), None)
    if profile is None:
        raise CertificateError('DNS_CREDENTIAL_NOT_FOUND')
    pending = [item for item in manager.provider.dns_journal.pending()
               if item['credential_id'] == credential_id and item['domain'] == domain]
    return {'version': 1,
            'credential': {key: profile[key] for key in ('id', 'revision', 'zone_name', 'status')},
            'pending': [{key: item[key] for key in ('job_id', 'revision', 'phase', 'error')} for item in pending]}


def recover_dns_bootstrap(manager, domain, request):
    """Explicit local operations reuse the same revision/auth rules as the API."""
    from app.certificates.dns_credentials import DNSCredentials
    state = dns_bootstrap_recovery_status(manager, domain)
    profile = state['credential']
    required = {'version', 'action', 'credential_id', 'expected_profile_revision'}
    action = request.get('action') if type(request) is dict else None
    extra = ({'api_token'} if action == 'replace-token' else set() if action == 'enable'
             else {'job_id', 'expected_revision'} if action in ('cleanup', 'confirm-absent') else None)
    if (extra is None or set(request) != required | extra or type(request.get('version')) is not int or request.get('version') != 1
            or request.get('credential_id') != profile['id']):
        raise CertificateError('DNS_BOOTSTRAP_REQUEST_INVALID')
    if request['expected_profile_revision'] != profile['revision']:
        raise CertificateError('DNS_CREDENTIAL_REVISION_CONFLICT')
    credentials = DNSCredentials(manager.root)
    if action == 'replace-token':
        credentials.replace(profile['id'], expected_revision=profile['revision'], api_token=request['api_token'])
        return {'version': 1, 'action': action, 'phase': 'credential-replaced'}
    if action == 'enable':
        credentials.set_enabled(profile['id'], expected_revision=profile['revision'], enabled=True, confirmed=True)
        return {'version': 1, 'action': action, 'phase': 'credential-enabled'}
    job = next((item for item in state['pending'] if item['job_id'] == request['job_id']), None)
    if job is None or job['revision'] != request['expected_revision']:
        raise CertificateError('DNS_CHALLENGE_REVISION_CONFLICT')
    journal = manager.provider.dns_journal
    authorized = journal.authorize_recovery(job['job_id'], job['revision'], profile['revision'], confirmed=True)
    if action == 'cleanup':
        journal.cleanup(job['job_id'], stop=manager.stop_event)
        value = journal.view(job['job_id'])
    else:
        value = journal.confirm_absent(job['job_id'], authorized['revision'], confirmed=True, stop=manager.stop_event)
    return {'version': 1, 'action': action, 'phase': value['phase']}


def bootstrap_panel(manager, domain, email, *, accept_terms=False,
                    method='http-01', dns_request=None):
    from app.models import database
    from app.certificates.models import Certificate, CertificateBinding
    domain = domain_name(domain)
    if not accept_terms: raise CertificateError('TERMS_NOT_ACCEPTED')
    existing = applied_dns_binding(manager, domain)
    if existing is not None:
        return existing
    if method == 'dns-01':
        with database.SessionLocal() as db:
            binding = db.get(CertificateBinding, 'panel')
            if binding is not None:
                applied = db.get(Certificate, binding.applied_certificate_id)
                if applied is not None and applied.method == 'http-01':
                    method = 'http-01'
    credential_id = None
    if method == 'dns-01':
        status = dns_bootstrap_status(manager, domain)
        if status['needs_token']:
            if not dns_request or set(dns_request) != {'version', 'zone_name', 'api_token'}:
                raise CertificateError('DNS_BOOTSTRAP_CREDENTIAL_REQUIRED')
            from app.certificates.dns_credentials import DNSCredentials
            from app.certificates.dns_store import normalize_zone
            zone = normalize_zone(dns_request['zone_name'])
            if domain != zone and not domain.endswith('.' + zone):
                raise CertificateError('DNS_OWNER_OUTSIDE_ZONE')
            profile = DNSCredentials(manager.root).create(label='面板首次安装',
                zone_name=zone, api_token=dns_request['api_token'])
            credential_id = profile['id']
        # Read-only zone discovery is not proof of TXT write permission.
        # Keep every credential revision and intent if issuance later fails.
        if hasattr(manager.provider, 'recover_dns') and not manager.provider.recover_dns(manager.stop_event):
            raise CertificateError('DNS_CLEANUP_PENDING')
    identity = ensure(manager, domain, email, accept_terms=accept_terms,
                      method=method, credential_id=credential_id)
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
    p.add_argument('command',choices=['bootstrap','test-issuance','dns-bootstrap-status',
                                     'dns-bootstrap-recovery-status','dns-bootstrap-recover'])
    p.add_argument('--root',required=True,type=Path)
    p.add_argument('--domain',required=True);p.add_argument('--email',required=True)
    p.add_argument('--accept-terms',action='store_true',required=True)
    p.add_argument('--method', choices=['http-01', 'dns-01'], default='http-01')
    p.add_argument('--private-dns-stdin', action='store_true')
    args=p.parse_args()
    from app.release_tools import private_root,writer_operation,supported_environment
    supported_environment();os.umask(0o077);root=private_root(args.root)
    os.environ['VUI_DATA_DIR']=str(root/'data')
    # The manager/database imports happen after locating the actual data directory.
    from app.certificates.manager import CertificateManager
    from app.models.database import init_db
    with writer_operation(root):
        init_db();manager=CertificateManager(root/'data'/'certificates')
        if args.command == 'dns-bootstrap-recovery-status':
            result = dns_bootstrap_recovery_status(manager, args.domain)
        elif args.command == 'dns-bootstrap-recover':
            if not args.private_dns_stdin:
                raise CertificateError('DNS_BOOTSTRAP_REQUEST_INVALID')
            result = recover_dns_bootstrap(manager, args.domain, private_dns_request(sys.stdin))
        elif args.command == 'dns-bootstrap-status':
            result = dns_bootstrap_status(manager, args.domain)
        elif args.command=='bootstrap':
            request = private_dns_request(sys.stdin) if args.private_dns_stdin else None
            if args.private_dns_stdin and args.method != 'dns-01':
                raise CertificateError('DNS_BOOTSTRAP_REQUEST_INVALID')
            result=bootstrap_panel(manager,args.domain,args.email,accept_terms=args.accept_terms,
                                   method=args.method,dns_request=request)
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
