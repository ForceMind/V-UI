"""Durable DNS-01 intents and cleanup, never an order/credential manager.

The caller holds the surrounding data_owner lease. All state transitions use
one nonblocking cross-process lock. Remote writes are never retried blindly;
unknown creation without a known ID cannot be cleared by one empty listing.
"""
from __future__ import annotations
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import math
import os
from pathlib import Path
import re
import secrets
import stat
import time
import uuid

from app.certificates.cloudflare import (CloudflareClient, CertificateError, parse_ownership,
                                          parse_txt_content, verify_ownership)
from app.certificates.dns_store import (DNSCredentialStore, DNSStoreError, normalize_zone,
                                        private_directory, _read, _write)

MAX_JOBS = 256
MAX_INTENTS = 2
ISSUE_BUDGET = 900
RECOVERY_BUDGET = 300
CLOSED = frozenset({'cleaned', 'manual_confirmed'})
_JOB_KEYS = frozenset({'schema', 'job_id', 'credential_id', 'credential_revision', 'profile_revision',
                      'provider', 'zone_id', 'zone_name', 'domain', 'names', 'owner', 'created_at', 'deadline',
                      'revision', 'updated_at', 'phase', 'error', 'entries', 'recovery_credential_revision',
                      'recovery_profile_revision', 'recovery_authorized_at', 'manual_confirmed_at'})
_ENTRY_KEYS = frozenset({'identifier', 'value', 'ownership', 'record_id', 'state', 'error'})
_SAFE_CODES = frozenset({
    'DNS_INVALID_IDENTIFIER', 'DNS_INVALID_NAME', 'DNS_INVALID_OWNER', 'DNS_INVALID_TXT_VALUE',
    'DNS_INVALID_OWNERSHIP', 'DNS_INVALID_TOKEN', 'DNS_INVALID_DEADLINE', 'DNS_INVALID_TRANSPORT',
    'DNS_PROVIDER_INVALID_RESPONSE', 'DNS_PROVIDER_RESPONSE_TOO_LARGE', 'DNS_PROVIDER_TIMEOUT',
    'DNS_PROVIDER_TLS_FAILED', 'DNS_PROVIDER_UNAVAILABLE', 'DNS_PROVIDER_WRITE_UNKNOWN',
    'DNS_PROVIDER_REDIRECT_REFUSED', 'DNS_PROVIDER_FORBIDDEN', 'DNS_PROVIDER_RATE_LIMITED',
    'DNS_PROVIDER_REJECTED', 'DNS_PROVIDER_RESULT_LIMIT', 'DNS_PROVIDER_PAGINATION_CHANGED',
    'DNS_PROVIDER_DUPLICATE_RECORD', 'DNS_RECORD_IDENTITY_MISMATCH', 'DNS_RECORD_OWNERSHIP_MISMATCH',
    'DNS_OWNER_OUTSIDE_ZONE', 'DNS_DELEGATION_UNSUPPORTED', 'DNS_ZONE_NOT_FOUND', 'DNS_ZONE_NOT_UNIQUE',
    'DNS_ZONE_IDENTITY_MISMATCH', 'DNS_CREDENTIAL_RESTORE_PENDING', 'DNS_CREDENTIAL_DISABLED',
    'DNS_CREDENTIAL_NOT_FOUND', 'DNS_CREDENTIALS_UNSAFE', 'DNS_CREDENTIALS_INVALID',
    'DNS_CREDENTIAL_REVISION_CONFLICT', 'DNS_CHALLENGE_CREATE_UNKNOWN', 'DNS_CHALLENGE_AMBIGUOUS',
    'DNS_CHALLENGE_CANCELLED', 'DNS_CHALLENGE_BUDGET_EXHAUSTED', 'DNS_CHALLENGE_OPERATION_FAILED',
    'DNS_CHALLENGE_RECORD_STILL_PRESENT', 'DNS_CHALLENGE_MANUAL_CONFIRMATION',
    'DNS_CHALLENGE_LIMIT_REACHED', 'DNS_CHALLENGE_CREDENTIAL_MISMATCH',
})


def _fail(code):
    raise CertificateError(code)


def _need(condition, code='DNS_JOURNAL_INVALID'):
    if not condition: _fail(code)


def _id(value):
    _need(type(value) is str and len(value) == 32 and re.fullmatch('[a-f0-9]{32}', value), 'DNS_CHALLENGE_INVALID_ID')
    return value


def _domain(value):
    valid = False
    try:
        valid = type(value) is str and normalize_zone(value) == value
    except DNSStoreError:
        pass
    _need(valid, 'DNS_CHALLENGE_INVALID_DOMAIN')
    return value


def _names(domain, names):
    _need(type(names) is list and 1 <= len(names) <= 2 and all(type(name) is str for name in names))
    _need(len(set(names)) == len(names) and set(names) in ({domain}, {domain, '*.' + domain}))
    return [domain] if len(names) == 1 else [domain, '*.' + domain]


def _value(value):
    _need(type(value) is str and parse_txt_content(value) == value, 'DNS_INVALID_TXT_VALUE')
    return value


def _timestamp(value):
    return type(value) is int and 0 < value < 2**63


def _error(error):
    if type(error) in (CertificateError, DNSStoreError):
        code = error.code
        if type(code) is str and code in _SAFE_CODES:
            return code
    return 'DNS_CHALLENGE_OPERATION_FAILED'


def _deadline(value, budget):
    now = time.monotonic()
    if value is not None:
        valid = False
        if type(value) in (int, float):
            try: valid = math.isfinite(value)
            except OverflowError: pass
        _need(valid, 'DNS_INVALID_DEADLINE')
    return now + budget if value is None else min(value, now + budget)


def _check(deadline, stop):
    if stop is not None:
        _need(callable(getattr(stop, 'is_set', None)), 'DNS_JOURNAL_INVALID_STOP')
        stopped = stop.is_set()
        _need(type(stopped) is bool, 'DNS_JOURNAL_INVALID_STOP')
        _need(not stopped, 'DNS_CHALLENGE_CANCELLED')
    _need(time.monotonic() < deadline, 'DNS_CHALLENGE_BUDGET_EXHAUSTED')


class DNSChallengeJournal:
    def __init__(self, certificates_root, *, store=None, client_factory=CloudflareClient):
        selected = Path(certificates_root)
        _need(selected.is_absolute() and '..' not in selected.parts, 'DNS_JOURNAL_UNSAFE')
        self.root = selected / 'dns-challenges'
        self.store = DNSCredentialStore(selected) if store is None else store
        self.client_factory = client_factory

    @contextmanager
    def _guard(self, *, create=False, read_only=False, offline=False):
        if not os.path.lexists(self.root) and not create:
            yield None
            return
        failure = None
        try:
            with private_directory(self.root, create=create) as directory:
                if read_only and 'owner.lock' not in os.listdir(directory):
                    _need(offline or not os.listdir(directory), 'DNS_JOURNAL_UNSAFE')
                    yield directory
                    return
                flags = os.O_RDONLY if read_only else os.O_RDWR | os.O_CREAT
                lock = os.open('owner.lock', flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
                try:
                    info = os.fstat(lock)
                    _need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1
                          and info.st_size == 0 and stat.S_IMODE(info.st_mode) == 0o600, 'DNS_JOURNAL_UNSAFE')
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        _fail('DNS_JOURNAL_BUSY')
                    yield directory
                finally:
                    os.close(lock)
        except DNSStoreError as error:
            failure = _error(error)
        except OSError:
            failure = 'DNS_JOURNAL_UNSAFE'
        if failure: _fail(failure)

    @staticmethod
    def _validate(value, selected):
        invalid = False
        try:
            _need(type(value) is dict and set(value) == _JOB_KEYS)
            _need(type(value['schema']) is int and value['schema'] == 1 and value['job_id'] == selected
                  and value['provider'] == 'cloudflare')
            for key in ('job_id', 'credential_id', 'credential_revision', 'profile_revision', 'zone_id', 'revision'):
                _id(value[key])
            _domain(value['domain']); _domain(value['zone_name'])
            _need(value['names'] == _names(value['domain'], value['names']))
            _need(value['owner'] == '_acme-challenge.' + value['domain'] and len(value['owner']) <= 253)
            owner, zone = value['owner'].split('.'), value['zone_name'].split('.')
            _need(len(owner) > len(zone) and owner[-len(zone):] == zone)
            _need(all(_timestamp(value[key]) for key in ('created_at', 'deadline', 'updated_at'))
                  and value['deadline'] == value['created_at'] + ISSUE_BUDGET
                  and value['updated_at'] >= value['created_at'])
            _need(value['phase'] in ('active', 'cleanup_pending', 'cleaned', 'manual_confirmed'))
            _need(value['error'] is None or value['error'] in _SAFE_CODES)
            _need(type(value['entries']) is list and len(value['entries']) <= MAX_INTENTS)
            values, ownerships, record_ids = set(), set(), set()
            for entry in value['entries']:
                _need(type(entry) is dict and set(entry) == _ENTRY_KEYS)
                _need(entry['identifier'] in value['names'])
                _value(entry['value']); parse_ownership(entry['ownership'])
                _need(entry['value'] not in values and entry['ownership'] not in ownerships)
                values.add(entry['value']); ownerships.add(entry['ownership'])
                _need(entry['state'] in ('intent', 'created', 'unknown', 'deleted', 'rejected'))
                if entry['record_id'] is not None:
                    _id(entry['record_id']); _need(entry['record_id'] not in record_ids)
                    record_ids.add(entry['record_id'])
                _need(entry['state'] != 'intent' or entry['record_id'] is None)
                _need(entry['state'] != 'rejected' or entry['record_id'] is None
                      and entry['error'] in ('DNS_PROVIDER_FORBIDDEN', 'DNS_PROVIDER_RATE_LIMITED'))
                _need(entry['state'] not in ('created', 'deleted') or entry['record_id'] is not None)
                _need(entry['error'] is None or entry['error'] in _SAFE_CODES)
            if value['recovery_credential_revision'] is None:
                _need(value['recovery_profile_revision'] is None and value['recovery_authorized_at'] is None)
            else:
                _id(value['recovery_credential_revision']); _id(value['recovery_profile_revision'])
                _need(_timestamp(value['recovery_authorized_at']) and value['phase'] != 'active')
            if value['phase'] == 'manual_confirmed':
                _need(_timestamp(value['manual_confirmed_at']))
            else:
                _need(value['manual_confirmed_at'] is None)
            if value['phase'] == 'cleaned': _need(all(entry['state'] in ('deleted', 'rejected') for entry in value['entries']))
        except (ValueError, TypeError, KeyError, OverflowError):
            invalid = True
        if invalid: _fail('DNS_JOURNAL_INVALID')
        return value

    def _load(self, directory, selected):
        _id(selected)
        try:
            value = _read(directory, selected + '.json')
        except FileNotFoundError:
            _fail('DNS_CHALLENGE_NOT_FOUND')
        except (DNSStoreError, OSError):
            _fail('DNS_JOURNAL_UNSAFE')
        return self._validate(value, selected)

    def _scan(self, directory):
        if directory is None: return []
        names = [name for name in os.listdir(directory) if name != 'owner.lock']
        _need(len(names) <= MAX_JOBS, 'DNS_JOURNAL_LIMIT_REACHED')
        _need(all(re.fullmatch('[a-f0-9]{32}\\.json', name) for name in names), 'DNS_JOURNAL_UNSAFE')
        return [self._load(directory, name[:-5]) for name in sorted(names)]

    def _save(self, directory, value, *, new=False):
        value['revision'] = uuid.uuid4().hex
        value['updated_at'] = max(value['created_at'], int(time.time()))
        self._validate(value, value['job_id'])
        failed = False
        try:
            _write(directory, value['job_id'] + '.json', value, replace=not new)
        except (DNSStoreError, OSError):
            failed = True
        if failed: _fail('DNS_JOURNAL_STORAGE_FAILED')

    @staticmethod
    def _metadata(value):
        result = {key: value[key] for key in ('job_id', 'credential_id', 'zone_name', 'domain', 'phase',
                                              'error', 'revision', 'created_at', 'updated_at', 'deadline')}
        result['count'] = len(value['entries'])
        if value['phase'] not in CLOSED and any(entry['state'] == 'intent' for entry in value['entries']):
            result.update(phase='cleanup_pending', error='DNS_CHALLENGE_CREATE_UNKNOWN')
        return result

    def _recover_intents(self, directory, value):
        if value['phase'] in CLOSED: return
        changed = False
        for entry in value['entries']:
            if entry['state'] == 'intent':
                entry.update(state='unknown', error='DNS_CHALLENGE_CREATE_UNKNOWN'); changed = True
        if changed:
            value.update(phase='cleanup_pending', error='DNS_CHALLENGE_CREATE_UNKNOWN')
            self._save(directory, value)

    @staticmethod
    def _snapshot_identity(snapshot, selected, revision=None):
        _need(type(snapshot) is dict and type(snapshot.get('schema')) is int and snapshot['schema'] == 1
              and snapshot.get('id') == selected and snapshot.get('provider') == 'cloudflare',
              'DNS_CHALLENGE_CREDENTIAL_MISMATCH')
        for key in ('id', 'revision', 'profile_revision', 'zone_id'): _id(snapshot.get(key))
        _need(revision is None or snapshot['revision'] == revision, 'DNS_CHALLENGE_CREDENTIAL_MISMATCH')
        _domain(snapshot.get('zone_name'))
        _need(type(snapshot.get('api_token')) is str and 1 <= len(snapshot['api_token']) <= 2048,
              'DNS_CHALLENGE_CREDENTIAL_MISMATCH')
        return snapshot

    def _snapshot(self, value, *, cleanup):
        revision = value['recovery_credential_revision'] if cleanup and value['recovery_credential_revision'] else value['credential_revision']
        snapshot = self.store.snapshot(value['credential_id'], revision=revision, for_cleanup=cleanup)
        self._snapshot_identity(snapshot, value['credential_id'], revision)
        _need(all(snapshot[key] == value[key] for key in ('provider', 'zone_id', 'zone_name')), 'DNS_CHALLENGE_CREDENTIAL_MISMATCH')
        return snapshot

    @staticmethod
    def _call(client, method, *args, deadline, stop):
        _check(deadline, stop)
        result = getattr(client, method)(*args, deadline=deadline)
        _check(deadline, stop)
        return result

    def begin(self, job_id, credential_id, credential_revision, domain, names):
        _id(job_id); _id(credential_id); _id(credential_revision); _domain(domain)
        names = _names(domain, names)
        with self._guard(create=True) as directory:
            jobs = self._scan(directory)
            existing = next((row for row in jobs if row['job_id'] == job_id), None)
            if existing:
                _need(all(existing[key] == expected for key, expected in (
                    ('credential_id', credential_id), ('credential_revision', credential_revision), ('domain', domain), ('names', names))),
                    'DNS_CHALLENGE_IDENTITY_CONFLICT')
                self._recover_intents(directory, existing)
                _need(existing['phase'] == 'active', 'DNS_CHALLENGE_ALREADY_CLOSED')
                return deepcopy(existing)
            _need(len(jobs) < MAX_JOBS, 'DNS_JOURNAL_LIMIT_REACHED')
            snapshot = self.store.snapshot(credential_id, revision=credential_revision)
            self._snapshot_identity(snapshot, credential_id, credential_revision)
            owner = '_acme-challenge.' + domain
            labels, zone = owner.split('.'), snapshot['zone_name'].split('.')
            _need(len(owner) <= 253 and len(labels) > len(zone) and labels[-len(zone):] == zone, 'DNS_OWNER_OUTSIDE_ZONE')
            _need(not any(row['zone_id'] == snapshot['zone_id'] and row['owner'] == owner and row['phase'] not in CLOSED for row in jobs),
                  'DNS_CHALLENGE_OWNER_BUSY')
            now = int(time.time())
            value = dict(schema=1, job_id=job_id, credential_id=credential_id, credential_revision=credential_revision,
                         profile_revision=snapshot['profile_revision'], provider='cloudflare', zone_id=snapshot['zone_id'],
                         zone_name=snapshot['zone_name'], domain=domain, names=names, owner=owner, created_at=now,
                         deadline=now + ISSUE_BUDGET, revision=uuid.uuid4().hex, updated_at=now, phase='active', error=None,
                         entries=[], recovery_credential_revision=None, recovery_profile_revision=None,
                         recovery_authorized_at=None, manual_confirmed_at=None)
            self._save(directory, value, new=True)
            return deepcopy(value)

    def present(self, job_id, identifier, value, *, deadline=None, stop=None):
        _id(job_id); value = _value(value)
        with self._guard() as directory:
            _need(directory is not None, 'DNS_CHALLENGE_NOT_FOUND')
            job = self._load(directory, job_id); self._recover_intents(directory, job)
            _need(job['phase'] == 'active' and job['recovery_credential_revision'] is None, 'DNS_CHALLENGE_NOT_ISSUING')
            _need(type(identifier) is str and identifier in job['names']
                  and (identifier[2:] if identifier.startswith('*.') else identifier) == job['domain'], 'DNS_CHALLENGE_IDENTIFIER_MISMATCH')
            budget = min(ISSUE_BUDGET, max(0, job['deadline'] - time.time()))
            deadline = _deadline(deadline, budget)
            existing = next((entry for entry in job['entries'] if entry['value'] == value), None)
            failure = None
            try:
                _check(deadline, stop)
                snapshot = self._snapshot(job, cleanup=False)
                if existing:
                    _need(existing['state'] == 'created', 'DNS_CHALLENGE_CREATE_UNKNOWN')
                    return existing['ownership']
                _need(len(job['entries']) < MAX_INTENTS, 'DNS_CHALLENGE_LIMIT_REACHED')
                client = self.client_factory(snapshot['api_token'])
                # Pure preparation first: delegation refusal must not manufacture
                # an ambiguous sent-write intent or require manual absence proof.
                self._call(client, 'assert_authoritative_owner', job['zone_id'], job['zone_name'], job['owner'], deadline=deadline, stop=stop)
            except Exception as error:
                failure = _error(error)
            if failure:
                job.update(phase='cleanup_pending', error=failure); self._save(directory, job); _fail(failure)
            entry = dict(identifier=identifier, value=value, ownership='v-ui:' + secrets.token_hex(32),
                         record_id=None, state='intent', error=None)
            job['entries'].append(entry)
            self._save(directory, job)  # fsync intent immediately before the one POST.
            failure = None
            try:
                result = self._call(client, 'create_txt', job['zone_id'], job['owner'], value, entry['ownership'], deadline=deadline, stop=stop)
                result = verify_ownership(result, zone_id=job['zone_id'], name=job['owner'], value=value, ownership=entry['ownership'])
            except Exception as error:
                failure = _error(error)
            if failure:
                state = 'rejected' if failure in ('DNS_PROVIDER_FORBIDDEN', 'DNS_PROVIDER_RATE_LIMITED') else 'unknown'
                entry.update(state=state, error=failure); job.update(phase='cleanup_pending', error=failure)
                self._save(directory, job); _fail(failure)
            entry.update(record_id=result['id'], state='created')
            self._save(directory, job)
            return entry['ownership']

    def _cleanup(self, directory, job, *, deadline, stop):
        self._recover_intents(directory, job)
        if job['phase'] == 'cleaned': return True
        if job['phase'] == 'manual_confirmed': return False
        job.update(phase='cleanup_pending'); self._save(directory, job)
        if all(entry['state'] in ('deleted', 'rejected') for entry in job['entries']):
            job.update(phase='cleaned'); self._save(directory, job)
            return True
        failure = None
        try:
            _check(deadline, stop)
            snapshot = self._snapshot(job, cleanup=True)
            client = self.client_factory(snapshot['api_token'])
        except Exception as error:
            failure = _error(error)
        if failure:
            job['error'] = failure; self._save(directory, job); return False
        for entry in job['entries']:
            if entry['state'] in ('deleted', 'rejected'): continue
            failure = None
            try:
                _check(deadline, stop)
                if entry['record_id'] is None:
                    matches = self._call(client, 'find_txt', job['zone_id'], job['owner'], entry['ownership'], deadline=deadline, stop=stop)
                    _need(type(matches) is list and len(matches) <= 100, 'DNS_CHALLENGE_AMBIGUOUS')
                    _need(len(matches) != 0, 'DNS_CHALLENGE_CREATE_UNKNOWN')
                    _need(len(matches) == 1, 'DNS_CHALLENGE_AMBIGUOUS')
                    found = verify_ownership(matches[0], zone_id=job['zone_id'], name=job['owner'], value=entry['value'], ownership=entry['ownership'])
                    entry.update(record_id=found['id'], state='created'); self._save(directory, job)
                observed = self._call(client, 'get_record', job['zone_id'], entry['record_id'], deadline=deadline, stop=stop)
                if observed is not None:
                    verify_ownership(observed, zone_id=job['zone_id'], record_id=entry['record_id'], name=job['owner'], value=entry['value'], ownership=entry['ownership'])
                    self._call(client, 'assert_authoritative_owner', job['zone_id'], job['zone_name'], job['owner'], deadline=deadline, stop=stop)
                    # Re-observe after authority reads; the remaining API gap is
                    # still explicitly non-atomic, not a conditional deletion.
                    observed = self._call(client, 'get_record', job['zone_id'], entry['record_id'], deadline=deadline, stop=stop)
                    if observed is not None:
                        verify_ownership(observed, zone_id=job['zone_id'], record_id=entry['record_id'], name=job['owner'], value=entry['value'], ownership=entry['ownership'])
                        self._call(client, 'delete_txt', job['zone_id'], entry['record_id'], deadline=deadline, stop=stop)
            except Exception as error:
                failure = _error(error)
            if failure:
                entry.update(state='unknown', error=failure); job['error'] = failure
            else:
                entry.update(state='deleted', error=None)
            self._save(directory, job)
            if failure in ('DNS_CHALLENGE_CANCELLED', 'DNS_CHALLENGE_BUDGET_EXHAUSTED'): break
        done = all(entry['state'] in ('deleted', 'rejected') for entry in job['entries'])
        if done: job.update(phase='cleaned')
        self._save(directory, job)
        return done

    def cleanup(self, job_id, *, deadline=None, stop=None):
        _id(job_id); deadline = _deadline(deadline, RECOVERY_BUDGET)
        with self._guard() as directory:
            _need(directory is not None, 'DNS_CHALLENGE_NOT_FOUND')
            return self._cleanup(directory, self._load(directory, job_id), deadline=deadline, stop=stop)

    def pending(self):
        with self._guard() as directory:
            return [self._metadata(value) for value in self._scan(directory) if value['phase'] not in CLOSED]

    def view(self, job_id):
        _id(job_id)
        with self._guard(read_only=True) as directory:
            _need(directory is not None, 'DNS_CHALLENGE_NOT_FOUND')
            return self._metadata(self._load(directory, job_id))

    def recover_all(self, stop, *, deadline=None):
        deadline = _deadline(deadline, RECOVERY_BUDGET)
        with self._guard() as directory:
            jobs = [job for job in self._scan(directory) if job['phase'] not in CLOSED]
            done = True
            for job in jobs:
                done = self._cleanup(directory, job, deadline=deadline, stop=stop) and done
                if job['phase'] not in CLOSED and job['error'] in ('DNS_CHALLENGE_CANCELLED', 'DNS_CHALLENGE_BUDGET_EXHAUSTED'): break
            return done and all(job['phase'] in CLOSED for job in jobs)

    @staticmethod
    def _expected(job, revision):
        _id(revision); _need(job['revision'] == revision, 'DNS_CHALLENGE_REVISION_CONFLICT')
        _need(job['phase'] not in CLOSED, 'DNS_CHALLENGE_ALREADY_CLOSED')

    def authorize_recovery(self, job_id, expected_revision, expected_profile_revision, confirmed=True):
        _id(job_id); _id(expected_profile_revision); _need(confirmed is True, 'DNS_CHALLENGE_CONFIRMATION_REQUIRED')
        with self._guard() as directory:
            _need(directory is not None, 'DNS_CHALLENGE_NOT_FOUND')
            job = self._load(directory, job_id); self._expected(job, expected_revision)
            snapshot = self.store.snapshot(job['credential_id'], for_cleanup=True)
            self._snapshot_identity(snapshot, job['credential_id'])
            _need(snapshot['profile_revision'] == expected_profile_revision, 'DNS_CREDENTIAL_REVISION_CONFLICT')
            _need(all(snapshot[key] == job[key] for key in ('provider', 'zone_id', 'zone_name')), 'DNS_CHALLENGE_CREDENTIAL_MISMATCH')
            job.update(phase='cleanup_pending', recovery_credential_revision=snapshot['revision'],
                       recovery_profile_revision=snapshot['profile_revision'], recovery_authorized_at=int(time.time()))
            self._save(directory, job)
            return self._metadata(job)

    def confirm_absent(self, job_id, expected_revision, confirmed=True, *, deadline=None, stop=None):
        _id(job_id); _need(confirmed is True, 'DNS_CHALLENGE_CONFIRMATION_REQUIRED')
        deadline = _deadline(deadline, RECOVERY_BUDGET)
        with self._guard() as directory:
            _need(directory is not None, 'DNS_CHALLENGE_NOT_FOUND')
            job = self._load(directory, job_id); self._expected(job, expected_revision)
            snapshot = self._snapshot(job, cleanup=True); client = self.client_factory(snapshot['api_token'])
            for entry in job['entries']:
                if entry['record_id'] is not None:
                    observed = self._call(client, 'get_record', job['zone_id'], entry['record_id'], deadline=deadline, stop=stop)
                    if observed is not None:
                        verify_ownership(observed, zone_id=job['zone_id'], record_id=entry['record_id'], name=job['owner'], value=entry['value'], ownership=entry['ownership'])
                        _fail('DNS_CHALLENGE_RECORD_STILL_PRESENT')
                matches = self._call(client, 'find_txt', job['zone_id'], job['owner'], entry['ownership'], deadline=deadline, stop=stop)
                _need(type(matches) is list and len(matches) <= 100, 'DNS_CHALLENGE_AMBIGUOUS')
                for match in matches:
                    verify_ownership(match, zone_id=job['zone_id'], name=job['owner'], value=entry['value'], ownership=entry['ownership'])
                _need(not matches, 'DNS_CHALLENGE_RECORD_STILL_PRESENT')
            for entry in job['entries']:
                if entry['state'] == 'intent': entry.update(state='unknown', error='DNS_CHALLENGE_CREATE_UNKNOWN')
            job.update(phase='manual_confirmed', error='DNS_CHALLENGE_MANUAL_CONFIRMATION', manual_confirmed_at=int(time.time()))
            self._save(directory, job)
            return self._metadata(job)

    def validate_for_backup(self):
        """Local references only, under caller's offline-exclusive data_owner lease.

        Restored private staging legitimately excludes owner.lock. Its absence
        permits offline reading, never runtime write-lock qualification. This
        path creates no lock, repairs no intent and never instantiates a client.
        """
        with self._guard(read_only=True, offline=True) as directory:
            return [{key: value[key] for key in ('job_id', 'credential_id', 'credential_revision', 'recovery_credential_revision',
                                                'provider', 'zone_id', 'zone_name')}
                    for value in self._scan(directory)]
