"""Durable certificate jobs and consumer state, with a single bounded worker."""
from copy import deepcopy
import hashlib
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid

from sqlalchemy import text, func
from app.models import database
from app.certificates.models import Certificate, CertificateJob, CertificateBinding
from app.certificates.material import (CertificateError, domain_name, email_address,
    make_csr, private_write, validate_material)
from app.certificates.provider import CertbotProvider
from app.release_tools import lease, ReleaseError, sync_directory

ACTIVE_JOBS = ('queued', 'running')
COOLDOWN = 3600


def now() -> int:
    return int(time.time())


def get_record(db, certificate_id):
    if not isinstance(certificate_id, str) or not re.fullmatch(r'[a-f0-9]{32}', certificate_id):
        raise CertificateError('CERTIFICATE_NOT_FOUND')
    record = db.get(Certificate, certificate_id)
    if record is None:
        raise CertificateError('CERTIFICATE_NOT_FOUND')
    return record


def job_view(job):
    return {key: getattr(job, key) for key in ('id', 'certificate_id', 'state', 'error',
            'created_at', 'started_at', 'finished_at')}


class CertificateManager:
    def __init__(self, root: Path, provider=None, *, trusted_roots: bytes | None = None):
        self.root = Path(root).absolute()
        self.provider = provider or CertbotProvider(self.root)
        self.trusted_roots = trusted_roots
        self.stop_event = threading.Event()
        self.wakeup = threading.Event()
        self.thread = None
        self._process_lock = threading.Lock()
        self._binding_lock = threading.RLock()
        self.panel_reloader = None
        self.worker_error = None

    def capabilities(self):
        return {'method': 'http-01', 'public_challenge_port': 80,
                'production_requires_public_dns': True, 'wildcards': False,
                'worker_running': bool(self.thread and self.thread.is_alive()),
                'worker_error': self.worker_error,
                'panel_hot_reload': self.panel_reloader is not None,
                'terms_url': 'https://letsencrypt.org/repository/'}

    def list_certificates(self):
        with database.SessionLocal() as db:
            rows = db.query(Certificate).order_by(Certificate.created_at.desc()).all()
            result = []
            for row in rows:
                job = db.query(CertificateJob).filter_by(certificate_id=row.id).order_by(CertificateJob.sequence.desc()).first()
                bindings = db.query(CertificateBinding).filter_by(certificate_id=row.id).all()
                item = {key: getattr(row, key) for key in ('id', 'domain', 'email', 'environment',
                    'auto_renew', 'revision', 'not_before', 'not_after', 'renew_at', 'retry_at',
                    'last_attempt', 'failures', 'error', 'created_at')}
                item['days_remaining'] = (row.not_after - now()) // 86400 if row.not_after else None
                item['status'] = ('pending' if job and job.state in ACTIVE_JOBS else
                    'expired' if row.not_after and row.not_after <= now() else
                    'renewal_failed' if row.error and row.revision else 'failed' if row.error else
                    'staging_only' if row.revision and row.environment == 'staging' else
                    'valid' if row.revision else 'not_issued')
                item['latest_job'] = job_view(job) if job else None
                item['bindings'] = [{'target': b.target, 'applied_revision': b.applied_revision,
                    'pending': b.applied_revision != row.revision or b.applied_certificate_id != row.id,
                    'error': b.error} for b in bindings]
                result.append(item)
            return result

    def create(self, domain: str, email: str, environment: str, auto_renew: bool, accept_terms: bool):
        if accept_terms is not True:
            raise CertificateError('TERMS_NOT_ACCEPTED')
        domain, email = domain_name(domain), email_address(email)
        if environment not in ('production', 'staging') or type(auto_renew) is not bool:
            raise CertificateError('INVALID_ENVIRONMENT')
        with database.SessionLocal() as db:
            db.execute(text('BEGIN IMMEDIATE'))
            record = db.query(Certificate).filter_by(domain=domain, environment=environment).first()
            if record:
                raise CertificateError('CERTIFICATE_ALREADY_EXISTS')
            if db.query(Certificate).count() >= 32:
                raise CertificateError('CERTIFICATE_LIMIT_REACHED')
            record = Certificate(id=uuid.uuid4().hex, domain=domain, email=email, environment=environment,
                auto_renew=auto_renew, created_at=now(), failures=0, renew_at=0, retry_at=0, last_attempt=0)
            db.add(record); db.flush()
            job = self._queue(db, record)
            value = job_view(job)
            db.commit()
            self.wakeup.set()
            return value

    def _queue(self, db, record):
        pending = db.query(CertificateJob).filter(CertificateJob.certificate_id == record.id,
                    CertificateJob.state.in_(ACTIVE_JOBS)).first()
        if pending:
            return pending
        if record.revision and record.renew_at > now():
            raise CertificateError('RENEWAL_NOT_DUE')
        if record.retry_at > now() or record.last_attempt and record.last_attempt + COOLDOWN > now():
            raise CertificateError('RETRY_COOLDOWN')
        if db.query(CertificateJob).filter(CertificateJob.state.in_(ACTIVE_JOBS)).count() >= 32:
            raise CertificateError('QUEUE_FULL')
        sequence = (db.query(func.max(CertificateJob.sequence)).scalar() or 0) + 1
        job = CertificateJob(id=uuid.uuid4().hex, certificate_id=record.id, sequence=sequence, state='queued', created_at=now())
        db.add(job); db.flush()
        return job

    def renew(self, certificate_id):
        with database.SessionLocal() as db:
            db.execute(text('BEGIN IMMEDIATE'))
            job = self._queue(db, get_record(db, certificate_id))
            result = job_view(job); db.commit(); self.wakeup.set(); return result

    def set_auto_renew(self, certificate_id, enabled):
        if type(enabled) is not bool: raise CertificateError('INVALID_RENEWAL_SETTING')
        with database.SessionLocal() as db:
            get_record(db, certificate_id).auto_renew = enabled
            db.commit()
        return {'auto_renew': enabled}

    def job(self, job_id):
        with database.SessionLocal() as db:
            row = db.get(CertificateJob, job_id)
            if not row: raise CertificateError('JOB_NOT_FOUND')
            return job_view(row)

    def _schedule_due(self, db):
        for record in db.query(Certificate).filter(Certificate.auto_renew.is_(True),
                Certificate.renew_at <= now(), Certificate.retry_at <= now()).all():
            try: self._queue(db, record)
            except CertificateError: pass
        # Retain bounded metadata; private materials remain immutable for rollback.
        db.query(CertificateJob).filter(CertificateJob.state.notin_(ACTIVE_JOBS),
                    CertificateJob.created_at < now() - 30 * 86400).delete(synchronize_session=False)

    def process_once(self):
        if not self._process_lock.acquire(blocking=False): return False
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            with lease(self.root / '.worker.lock'):
                with database.SessionLocal() as db:
                    db.execute(text('BEGIN IMMEDIATE'))
                    # Exclusive process lease means a running row belongs to an interrupted worker.
                    for old in db.query(CertificateJob).filter_by(state='running').all():
                        old.state, old.error, old.finished_at = 'failed', 'WORKER_INTERRUPTED', now()
                        cert = get_record(db, old.certificate_id)
                        cert.error, cert.retry_at = 'WORKER_INTERRUPTED', now() + COOLDOWN
                    self._schedule_due(db)
                    job = db.query(CertificateJob).filter_by(state='queued').order_by(CertificateJob.sequence).first()
                    if not job: db.commit(); return False
                    record = get_record(db, job.certificate_id)
                    job.state, job.started_at = 'running', now()
                    record.last_attempt = now()
                    values = {'id': record.id, 'domain': record.domain, 'email': record.email, 'environment': record.environment}
                    job_id = job.id; db.commit()
                try:
                    with tempfile.TemporaryDirectory(prefix='.issue-', dir=self.root) as directory:
                        work = Path(directory)
                        key, csr = make_csr(values['domain'])
                        private_write(work / 'private.pem', key); private_write(work / 'request.pem', csr)
                        chain = self.provider.issue(values, work, self.stop_event)
                        meta = validate_material(chain, key, values['domain'], trusted_roots=self.trusted_roots,
                                                 verify_chain=values['environment'] == 'production')
                        target = self.root / 'revisions' / values['id'] / meta['revision']
                        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        if not target.exists():
                            prepared = work / 'material'; prepared.mkdir(mode=0o700)
                            private_write(prepared / 'fullchain.pem', chain)
                            private_write(prepared / 'privkey.pem', key)
                            sync_directory(prepared)
                            os.replace(prepared, target); sync_directory(target.parent)
                        with database.SessionLocal() as db:
                            record = get_record(db, values['id'])
                            for name in ('revision', 'not_before', 'not_after', 'renew_at'):
                                setattr(record, name, meta[name])
                            record.failures, record.error, record.retry_at = 0, None, 0
                            job = db.get(CertificateJob, job_id)
                            job.state, job.finished_at = 'succeeded', now()
                            db.commit()
                    # Issued and applied are separate states; a consumer failure must stay visible.
                    self.apply_existing(values['id'])
                except Exception as exc:
                    code = exc.code if isinstance(exc, CertificateError) else 'CERTIFICATE_OPERATION_FAILED'
                    with database.SessionLocal() as db:
                        record = get_record(db, values['id'])
                        record.failures += 1; record.error = code
                        record.retry_at = now() + min(86400, COOLDOWN * 2 ** min(record.failures - 1, 4))
                        job = db.get(CertificateJob, job_id)
                        job.state, job.error, job.finished_at = 'failed', code, now()
                        db.commit()
                return True
        except ReleaseError:
            return False  # Another explicit bootstrap process holds the worker lease.
        finally:
            self._process_lock.release()

    def material(self, certificate_id, revision=None, *, production=True):
        with database.SessionLocal() as db:
            record = get_record(db, certificate_id)
            if production and record.environment != 'production': raise CertificateError('STAGING_CANNOT_BE_DEPLOYED')
            selected = revision or record.revision
            if not selected or not re.fullmatch(r'[a-f0-9]{64}', selected): raise CertificateError('CERTIFICATE_NOT_READY')
            folder = self.root / 'revisions' / record.id / selected
            paths = folder / 'fullchain.pem', folder / 'privkey.pem'
            if any(p.is_symlink() or not p.is_file() or p.stat().st_mode & 0o077 for p in paths):
                raise CertificateError('CERTIFICATE_FILES_INVALID')
            chain, key = (p.read_bytes() for p in paths)
            if hashlib.sha256(chain).hexdigest() != selected: raise CertificateError('CERTIFICATE_FILES_CHANGED')
            validate_material(chain, key, record.domain, trusted_roots=self.trusted_roots,
                              verify_chain=record.environment == 'production')
            return paths, record.domain, selected

    def binding(self, target):
        with database.SessionLocal() as db:
            row=db.get(CertificateBinding,target)
            return row.certificate_id if row else None

    def unbind(self, target):
        with self._binding_lock, database.SessionLocal() as db:
            db.execute(text('BEGIN IMMEDIATE'))
            row=db.get(CertificateBinding,target)
            if row:
                db.delete(row)
            db.commit()
        return {'target':target,'configured':False}

    def bind(self, certificate_id, target):
        self.material(certificate_id)
        if target != 'panel' and not re.fullmatch(r'inbound:[1-9][0-9]*', target): raise CertificateError('INVALID_TARGET')
        with self._binding_lock:
            with database.SessionLocal() as db:
                db.execute(text('BEGIN IMMEDIATE'))
                binding = db.get(CertificateBinding, target)
                if binding is None:
                    binding = CertificateBinding(target=target); db.add(binding)
                binding.certificate_id = certificate_id
                db.commit()
            self._apply_target(target, explicit=True)
        return {'target': target, 'configured': True}

    def _apply_target(self, target, *, explicit=False):
        from app.services.core_manager import core_manager
        try:
            with self._binding_lock, core_manager.lock, database.SessionLocal() as db:
                binding = db.get(CertificateBinding, target)
                paths, domain, rev = self.material(binding.certificate_id)
                if target == 'panel':
                    from app.middleware.auth import configured_origin
                    if urlsplit(configured_origin()).hostname != domain:
                        raise CertificateError('PANEL_DOMAIN_MISMATCH')
                    if self.panel_reloader is None: raise CertificateError('PANEL_HOT_RELOAD_UNAVAILABLE')
                    self.panel_reloader(*paths)
                else:
                    row = db.get(database.Inbound, int(target.split(':')[1]))
                    if not row: raise CertificateError('INBOUND_NOT_FOUND')
                    if row.core != 'sing-box' or row.protocol not in {'vless','trojan'}: raise CertificateError('UNSUPPORTED_CERTIFICATE_TARGET')
                    stream = deepcopy(row.stream_settings or {})
                    tls = stream.get('tls') or {}
                    if tls.get('reality') or not tls.get('enabled'):
                        raise CertificateError('TLS_NODE_REQUIRED')
                    if not explicit and binding.applied_revision:
                        oldroot = self.root / 'revisions' / binding.applied_certificate_id / binding.applied_revision
                        allowed = {(str(oldroot / 'fullchain.pem'), str(oldroot / 'privkey.pem')),
                                   (str(paths[0]), str(paths[1]))}
                        if (tls.get('certificate_path'), tls.get('key_path')) not in allowed:
                            raise CertificateError('BINDING_CHANGED_MANUALLY')
                    tls.update(certificate_path=str(paths[0]), key_path=str(paths[1]), server_name=domain)
                    stream['tls'] = tls
                    if '_vui' in stream:
                        stream['_vui']['server_name'] = domain
                    row.stream_settings = stream
                    db.commit()
                    # Do not resurrect a manually stopped core during renewal.
                    runtime = core_manager.get(row.core).status()
                    result = core_manager.apply_database(row.core, activate=bool(runtime['running']))
                    if not result.get('applied'):
                        raise CertificateError('CORE_STOPPED_PENDING_APPLY')
                binding.applied_certificate_id = binding.certificate_id
                binding.applied_revision, binding.error = rev, None
                db.commit()
        except Exception as exc:
            code = exc.code if isinstance(exc, CertificateError) else 'CERTIFICATE_APPLY_FAILED'
            with database.SessionLocal() as db:
                binding = db.get(CertificateBinding, target)
                if binding: binding.error = code; db.commit()
            raise CertificateError(code) from None

    def apply_existing(self, certificate_id):
        with database.SessionLocal() as db:
            targets = [b.target for b in db.query(CertificateBinding).filter_by(certificate_id=certificate_id).all()]
        result = []
        for target in targets:
            try:
                self._apply_target(target)
                result.append({'target': target, 'applied': True})
            except CertificateError as exc:
                result.append({'target': target, 'applied': False, 'error': exc.code})
        return result

    def panel_material(self):
        with database.SessionLocal() as db:
            binding = db.get(CertificateBinding, 'panel')
            if not binding or not binding.applied_certificate_id or not binding.applied_revision: return None
            return self.material(binding.applied_certificate_id, binding.applied_revision)[0]

    def start(self):
        if self.thread and self.thread.is_alive(): return
        self.stop_event.clear()
        def loop():
            while not self.stop_event.is_set():
                try:
                    worked = self.process_once(); self.worker_error = None
                except Exception:
                    worked = False; self.worker_error = 'WORKER_STORAGE_UNAVAILABLE'
                    logging.getLogger('vui.certificates').warning('Certificate worker storage unavailable')
                self.wakeup.wait(.2 if worked else 60)
                self.wakeup.clear()
        self.thread = threading.Thread(target=loop, name='vui-certificate-worker', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wakeup.set()
        if self.thread:
            self.thread.join(timeout=12)
            if self.thread.is_alive():
                self.worker_error = 'WORKER_SHUTDOWN_PENDING'
                return
        self.thread = None


_instance = None

def get_manager():
    global _instance
    if _instance is None:
        _instance = CertificateManager(database.DATA_DIR / 'certificates')
    return _instance
