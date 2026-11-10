"""Private Root-issued core authority and durable single-consumption ledger.

No socket, environment, application import, network, service or core execution.
Production callers use the fixed default layout; private constructor injection
exists only for isolated owner-controlled tests. This module is not wired into
remote writes until the complete Root controller and scope contracts pass CI.
"""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
import uuid

ROOT = Path('/var/lib/v-ui-maintenance/core-authority')
MAX_BYTES = 2_000_000
MAX_GRANTS = 256
MAX_ADMISSIONS = 1024
MAX_BINDINGS = 1024
MAX_APPLICATIONS = 1024
SCOPES = frozenset(('core_operate', 'core_backup', 'core_restore'))
ID = re.compile(r'[a-f0-9]{32}\Z')
HEX = re.compile(r'[a-f0-9]{64}\Z')
RELEASE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}\Z')
TARGETS = frozenset(('x86_64-gnu','x86_64-musl','aarch64-gnu','aarch64-musl'))
TOKEN = re.compile(r'vui_cg_([a-f0-9]{32})_([A-Za-z0-9_-]{43})\Z')
MAX_INT = 2**63-1


class CoreAuthorityError(ValueError):
    """Fixed diagnostic only; never include bearer, record or private path."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(value, code):
    if not value:
        raise CoreAuthorityError(code)


def _hex(value, pattern=HEX):
    return type(value) is str and pattern.fullmatch(value) is not None


def _integer(value, *, zero=False):
    return type(value) is int and (0 if zero else 1) <= value <= MAX_INT


def _keys(value, names):
    _need(type(value) is dict and set(value) == set(names), 'SCHEMA')


def _raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def _digest(value):
    return hashlib.sha256(_raw(value)).hexdigest()


def _pairs(items):
    value = {}
    for key, item in items:
        _need(key not in value, 'JSON')
        value[key] = item
    return value


def _number(value):
    raise CoreAuthorityError('JSON')


def _json_integer(raw):
    # Bound decimal conversion before int(), including Python 3.9.
    _need(len(raw) <= 20, 'JSON_INTEGER')
    value = int(raw)
    _need(-2**63 <= value <= MAX_INT, 'JSON_INTEGER')
    return value


def _decode(raw):
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, 'SIZE')
    try:
        value = json.loads(raw.decode('ascii'), object_pairs_hook=_pairs,
                           parse_float=_number, parse_constant=_number, parse_int=_json_integer)
        _need(_raw(value) == raw, 'JSON')
        if type(value) is dict and type(value.get('schema')) is int and value['schema'] == 1:
            _keys(value, ('schema','kind','installation_id','epoch','grants','admissions','revoked_bindings'))
            _need(value['kind'] == 'core-authority-v1', 'SCHEMA')
            # Preserve all existing authority and consumption state. No service
            # metadata is consulted or given origin authority by this migration.
            value = {**value, 'schema':2, 'kind':'core-authority-v2', 'applications':{}}
        if type(value) is dict and 'backup_recovery' not in value:
            value = {**value, 'backup_recovery':None}
        return _validate(value)
    except CoreAuthorityError:
        raise
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise CoreAuthorityError('JSON') from None


def normalized(action, parameters):
    """Closed intent only. Actual CAS, artifact and backup facts are rechecked
    by the Root controller under its installation lock before mutation.
    """
    names = {
        'update': ('core', 'release_id', 'selection_revision'),
        'rollback': ('core', 'selection_revision'),
        'recover': ('recovery_kind', 'pending_transaction', 'pending_sequence', 'selection_revision'),
        'backup': ('selection_revision',),
        'restore': ('backup_task_id', 'selection_revision'),
    }
    _need(type(action) is str and action in names, 'ACTION')
    _keys(parameters, names[action])
    revision = parameters['selection_revision']
    _need(revision is None or _hex(revision), 'REVISION')
    if action in ('rollback', 'backup', 'restore'):
        _need(revision is not None, 'REVISION')
    if action in ('update', 'rollback'):
        _need(type(parameters['core']) is str and parameters['core'] in ('sing-box', 'xray'), 'CORE')
    if action == 'update':
        _need(_integer(parameters['release_id']), 'RELEASE')
    if action == 'recover':
        _need(type(parameters['recovery_kind']) is str and parameters['recovery_kind'] in ('switch', 'restore', 'backup')
              and _hex(parameters['pending_transaction'], ID)
              and _integer(parameters['pending_sequence'], zero=True), 'PENDING')
    if action == 'restore':
        _need(_hex(parameters['backup_task_id'], ID), 'BACKUP')
    return deepcopy(parameters)


def _binding(main_id, main_digest):
    return _digest(dict(main_id=main_id, main_digest=main_digest))


def _scope(action, parameters):
    if action == 'restore' or action == 'recover' and parameters['recovery_kind'] == 'restore':
        return 'core_restore'
    return 'core_backup' if action == 'backup' or action == 'recover' and parameters['recovery_kind'] == 'backup' else 'core_operate'


def _grant(value, key):
    _keys(value, ('id', 'installation_id', 'epoch', 'main_id', 'main_digest', 'scopes',
                  'secret_sha256', 'created_at', 'expires_at', 'revoked'))
    _need(value['id'] == key and _hex(key, ID)
          and all(_hex(value[name], ID) for name in ('installation_id', 'epoch'))
          and _integer(value['main_id']) and _hex(value['main_digest']) and _hex(value['secret_sha256'])
          and type(value['scopes']) is list and value['scopes']
          and all(type(item) is str and item in SCOPES for item in value['scopes'])
          and value['scopes'] == sorted(set(value['scopes']))
          and _integer(value['created_at']) and _integer(value['expires_at'])
          and 1 <= value['expires_at']-value['created_at'] <= 15552000
          and type(value['revoked']) is bool, 'GRANT')


def _application(value):
    _keys(value, ('release_id','target','app_source','manifest_sha256','archive_sha256'))
    _need(_hex(value['release_id'], RELEASE) and type(value['target']) is str
          and value['target'] in TARGETS and type(value['app_source']) is str
          and re.fullmatch(r'[a-f0-9]{40}', value['app_source']) is not None
          and _hex(value['manifest_sha256']) and _hex(value['archive_sha256']), 'APPLICATION')
    return deepcopy(value)


def _application_key(release_id, target):
    return _digest(dict(release_id=release_id, target=target))


def _known_bindings(value):
    return set(value['revoked_bindings']) | {
        _binding(row['main_id'], row['main_digest']) for row in value['grants'].values()}


def _validate(value):
    _keys(value, ('schema', 'kind', 'installation_id', 'epoch', 'grants', 'admissions', 'revoked_bindings', 'applications', 'backup_recovery'))
    _need(type(value['schema']) is int and value['schema'] == 2 and value['kind'] == 'core-authority-v2'
          and _hex(value['installation_id'], ID) and _hex(value['epoch'], ID)
          and type(value['grants']) is dict and len(value['grants']) <= MAX_GRANTS
          and type(value['admissions']) is dict and len(value['admissions']) <= MAX_ADMISSIONS
          and type(value['revoked_bindings']) is dict and len(value['revoked_bindings']) <= MAX_BINDINGS
          and type(value['applications']) is dict and len(value['applications']) <= MAX_APPLICATIONS, 'SCHEMA')
    for key, row in value['applications'].items():
        _application(row)
        _need(_hex(key) and key == _application_key(row['release_id'], row['target']), 'APPLICATION')
    for key, row in value['revoked_bindings'].items():
        _keys(row, ('main_id', 'main_digest', 'revoked_at'))
        _need(_integer(row['main_id']) and _hex(row['main_digest'])
              and _integer(row['revoked_at']) and _hex(key)
              and key == _binding(row['main_id'], row['main_digest']), 'BINDING')
    for key, row in value['grants'].items():
        _grant(row, key)
        _need(row['installation_id'] == value['installation_id'], 'INSTALLATION')
        _need(row['epoch'] == value['epoch'] or row['revoked'], 'EPOCH')
        _need(_binding(row['main_id'], row['main_digest']) not in value['revoked_bindings']
              or row['revoked'], 'REVOKED_BINDING')
    _need(len(_known_bindings(value)) <= MAX_BINDINGS, 'CAPACITY')
    unique = set()
    for key, row in value['admissions'].items():
        _keys(row, ('id', 'grant_id', 'request_id', 'installation_id', 'epoch', 'action', 'parameters',
                    'parameters_sha256', 'created_at', 'phase', 'plan_sha256', 'mutation_at'))
        _need(_hex(key, ID) and row['id'] == key and _hex(row['grant_id'], ID)
              and row['grant_id'] in value['grants'] and _hex(row['request_id'], ID)
              and row['installation_id'] == value['installation_id']
              and row['epoch'] == value['grants'][row['grant_id']]['epoch']
              and _integer(row['created_at'])
              and row['created_at'] >= value['grants'][row['grant_id']]['created_at'], 'ADMISSION')
        normalized(row['action'], row['parameters'])
        _need(row['parameters_sha256'] == _digest(dict(action=row['action'], parameters=row['parameters']))
              and row['phase'] in ('preflight', 'mutating')
              and (row['phase'] == 'preflight' and row['plan_sha256'] is None and row['mutation_at'] is None
                   or row['phase'] == 'mutating' and _hex(row['plan_sha256'])
                   and _integer(row['mutation_at']) and row['mutation_at'] >= row['created_at']), 'ADMISSION')
        pair = (row['grant_id'], row['request_id'])
        _need(pair not in unique, 'IDEMPOTENCY')
        unique.add(pair)
    pending = value['backup_recovery']
    if pending is not None:
        _backup_recovery(pending)
        task = value['admissions'].get(pending['transaction'])
        _need(task is not None and task['action'] == 'backup' and task['phase'] == 'mutating'
              and pending['installation_id'] == value['installation_id']
              and pending['before']['revision'] == task['parameters']['selection_revision'], 'BACKUP_RECOVERY')
    return value


def _backup_recovery(value):
    _keys(value, ('kind','transaction','sequence','installation_id','before','context'))
    _need(value['kind'] == 'core-backup-recovery-v1' and _hex(value['transaction'],ID)
          and value['sequence'] == 1 and type(value['sequence']) is int
          and _hex(value['installation_id'],ID) and type(value['before']) is dict
          and _hex(value['before'].get('revision')), 'BACKUP_RECOVERY')
    context = value['context']
    _keys(context, ('facts','config_sha256','services','trust_sha256'))
    _keys(context['facts'], ('application','data_generation','selection','selection_initialized','bundles','intents','runtime_schemas'))
    _need(context['facts']['selection'] == value['before'] and context['facts']['selection_initialized'] is True
          and _hex(context['config_sha256']) and (context['trust_sha256'] is None or _hex(context['trust_sha256'])), 'BACKUP_RECOVERY')
    _keys(context['services'], ('v-ui.service','v-ui-http01.socket','v-ui-http01.service'))
    _need(all(row in ('active','inactive') for row in context['services'].values()), 'BACKUP_RECOVERY')
    return deepcopy(value)


def _file(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _directory(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)


class CoreAuthority:
    def __init__(self, *, _root=ROOT, _anchor=Path('/'), _owner=0):
        _need(isinstance(_root, Path) and isinstance(_anchor, Path) and _root.is_absolute()
              and _anchor.is_absolute() and '..' not in _root.parts and '..' not in _anchor.parts
              and _root != _anchor and type(_owner) is int and _owner >= 0, 'LAYOUT')
        try:
            relative = _root.relative_to(_anchor)
        except ValueError:
            raise CoreAuthorityError('LAYOUT') from None
        self.root, self.anchor, self.owner, self.parts = _root, _anchor, _owner, relative.parts

    def _private_file(self, info, *, empty=False):
        _need(stat.S_ISREG(info.st_mode) and info.st_uid == self.owner and info.st_nlink == 1
              and stat.S_IMODE(info.st_mode) == 0o600
              and (info.st_size == 0 if empty else 0 < info.st_size <= MAX_BYTES), 'FILE')

    @contextmanager
    def _locked(self, *, create=False):
        try:
            with self._directory_lock(create=create) as held:
                yield held
        except OSError:
            raise CoreAuthorityError('IO') from None

    @contextmanager
    def _directory_lock(self, *, create=False):
        _need(os.geteuid() == self.owner, 'OWNER')
        with ExitStack() as owned:
            bindings = []
            root = os.open(self.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            owned.callback(os.close, root)
            initial = _directory(os.fstat(root))
            _need(initial[3] == (0 if self.anchor == Path('/') else self.owner)
                  and not initial[2] & 0o022, 'DIRECTORY')
            for index, name in enumerate(self.parts):
                last = index == len(self.parts)-1
                created = False
                make = create and index >= len(self.parts)-2
                if make:
                    try:
                        os.mkdir(name, mode=0o700, dir_fd=root)
                        created = True
                    except FileExistsError:
                        pass
                fresh = None
                if created:
                    # The caller exclusively created this entry under an owned
                    # parent. Repair only this new inode after restrictive umask;
                    # no existing directory is adopted or chmod-repaired.
                    fresh = os.stat(name, dir_fd=root, follow_symlinks=False)
                    _need(stat.S_ISDIR(fresh.st_mode) and fresh.st_uid == self.owner, 'DIRECTORY')
                    os.chmod(name, 0o700, dir_fd=root, follow_symlinks=False)
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                owned.callback(os.close, child)
                if fresh is not None:
                    info = os.fstat(child)
                    _need((fresh.st_dev, fresh.st_ino) == (info.st_dev, info.st_ino), 'DIRECTORY_CHANGED')
                if created and self.owner == 0:
                    os.fchown(child, 0, 0)
                info = os.fstat(child)
                _need(info.st_uid == self.owner and not info.st_mode & 0o022
                      and (not last or stat.S_IMODE(info.st_mode) == 0o700), 'DIRECTORY')
                bindings.append((root, name, child, _directory(info)))
                if make:
                    # Persist the owned namespace, including retry after an
                    # earlier interrupted bootstrap. fsync(leaf) alone cannot
                    # durably publish its name in the parent.
                    _need(_directory(os.stat(name, dir_fd=root, follow_symlinks=False)) == _directory(info),
                          'DIRECTORY_CHANGED')
                    os.fsync(root)
                    _need(_directory(os.stat(name, dir_fd=root, follow_symlinks=False)) == _directory(info),
                          'DIRECTORY_CHANGED')
                root = child
            lock_flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            lock_created = False
            if create:
                try:
                    lock = os.open('.lock', lock_flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=root)
                    lock_created = True
                except FileExistsError:
                    lock = os.open('.lock', lock_flags, dir_fd=root)
            else:
                lock = os.open('.lock', lock_flags, dir_fd=root)
            owned.callback(os.close, lock)
            if lock_created:
                os.fchmod(lock, 0o600)
            before = os.fstat(lock);self._private_file(before, empty=True)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CoreAuthorityError('BUSY') from None
            def proof():
                _need(_directory(os.stat(self.anchor, follow_symlinks=False)) == initial, 'DIRECTORY_CHANGED')
                for parent, name, child, expected in bindings:
                    _need(_directory(os.fstat(child)) == expected
                          and _directory(os.stat(name, dir_fd=parent, follow_symlinks=False)) == expected,
                          'DIRECTORY_CHANGED')
                _need(_file(os.fstat(lock)) == _file(before)
                      and _file(os.stat('.lock', dir_fd=root, follow_symlinks=False)) == _file(before), 'LOCK_CHANGED')
            proof()
            yield root, proof
            proof()

    def _load(self, directory, *, missing=False):
        try:
            fd = os.open('authority.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            if not missing:
                raise CoreAuthorityError('NOT_INITIALIZED') from None
            _need(os.listdir(directory) == ['.lock'], 'INCOMPLETE')
            return dict(schema=2, kind='core-authority-v2', installation_id=uuid.uuid4().hex,
                        epoch=uuid.uuid4().hex, grants={}, admissions={}, revoked_bindings={}, applications={}, backup_recovery=None), None
        try:
            before = os.fstat(fd);self._private_file(before)
            raw = bytearray()
            while len(raw) <= MAX_BYTES:
                chunk = os.read(fd, min(65536, MAX_BYTES+1-len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            after = os.fstat(fd)
            named = os.stat('authority.json', dir_fd=directory, follow_symlinks=False)
            _need(_file(before) == _file(after) == _file(named) and len(raw) == before.st_size, 'FILE_CHANGED')
            return _decode(bytes(raw)), _file(before)
        finally:
            os.close(fd)

    def _save(self, directory, proof, value, expected):
        raw = _raw(_validate(value));_need(len(raw) <= MAX_BYTES, 'SIZE')
        name = '.stage-'+uuid.uuid4().hex
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        identity = None
        try:
            created = os.fstat(fd)
            identity = (created.st_dev, created.st_ino)
            os.fchmod(fd, 0o600)
            self._private_file(os.fstat(fd), empty=True)
            remaining = memoryview(raw)
            while remaining:
                written = os.write(fd, remaining)
                _need(written > 0, 'WRITE')
                remaining = remaining[written:]
            os.fsync(fd)
            current = os.fstat(fd)
            self._private_file(current)
            _need(current.st_size == len(raw), 'WRITE')
            proof()
            try:
                actual = _file(os.stat('authority.json', dir_fd=directory, follow_symlinks=False))
            except FileNotFoundError:
                actual = None
            _need(actual == expected, 'FILE_CHANGED')
            _need(_file(os.stat(name, dir_fd=directory, follow_symlinks=False)) == _file(current), 'FILE_CHANGED')
            os.replace(name, 'authority.json', src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
            proof()
        finally:
            try:
                try:
                    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    if identity is not None and (info.st_dev, info.st_ino) == identity:
                        os.unlink(name, dir_fd=directory)
                except FileNotFoundError:
                    pass
            finally:
                os.close(fd)

    def _ack(self, directory, proof, expected):
        """Reconfirm durability on replay after an unknown prior fsync result."""
        fd = os.open('authority.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(fd)
            self._private_file(before)
            _need(_file(before) == expected, 'FILE_CHANGED')
            proof()
            os.fsync(fd)
            os.fsync(directory)
            _need(_file(os.fstat(fd)) == expected
                  and _file(os.stat('authority.json', dir_fd=directory, follow_symlinks=False)) == expected,
                  'FILE_CHANGED')
            proof()
        finally:
            os.close(fd)

    def _now(self):
        now = int(time.time())
        _need(_integer(now), 'CLOCK')
        return now

    def _live(self, state, grant, now, directory, proof, expected):
        # Persist observed expiration as revocation; rolling the wall clock back
        # cannot revive an authority already rejected for expiration.
        saved = False
        if now >= grant['expires_at'] and not grant['revoked']:
            grant['revoked'] = True
            self._save(directory, proof, state, expected)
            saved = True
        if grant['revoked']:
            if not saved:
                self._ack(directory, proof, expected)
            raise CoreAuthorityError('AUTHORITY')
        _need(grant['epoch'] == state['epoch']
              and grant['created_at'] <= now < grant['expires_at'], 'AUTHORITY')

    def issue(self, *, main_id, main_digest, scopes, seconds=1800, not_after=None):
        _need(_integer(main_id) and _hex(main_digest) and type(scopes) in (list, tuple)
              and scopes and all(type(item) is str and item in SCOPES for item in scopes)
              and len(set(scopes)) == len(scopes) and type(seconds) is int
              and 60 <= seconds <= 15552000
              and (not_after is None or _integer(not_after)), 'ISSUE')
        with self._locked(create=True) as (directory, proof):
            state, before = self._load(directory, missing=True)
            binding = _binding(main_id, main_digest)
            _need(binding not in state['revoked_bindings'], 'REVOKED_BINDING')
            known = _known_bindings(state)
            _need(binding in known or len(known) < MAX_BINDINGS, 'CAPACITY')
            _need(len(state['grants']) < MAX_GRANTS, 'CAPACITY')
            identity = uuid.uuid4().hex
            _need(identity not in state['grants'], 'COLLISION')
            token = 'vui_cg_'+identity+'_'+secrets.token_urlsafe(32)
            now = self._now()
            expires_at=min(now+seconds,not_after) if not_after is not None else now+seconds
            _need(expires_at>now,'EXPIRED')
            row = dict(id=identity, installation_id=state['installation_id'], epoch=state['epoch'],
                       main_id=main_id, main_digest=main_digest, scopes=sorted(scopes),
                       secret_sha256=hashlib.sha256(token.encode('ascii')).hexdigest(),
                       created_at=now, expires_at=expires_at, revoked=False)
            state['grants'][identity] = row
            self._save(directory, proof, state, before)
            return self._public_grant(row), token

    @staticmethod
    def _public_grant(row):
        return {key: deepcopy(row[key]) for key in ('id', 'main_id', 'scopes', 'created_at', 'expires_at', 'revoked')}

    def grants(self):
        with self._locked() as (directory, proof):
            state, _ = self._load(directory)
            proof()
            return [self._public_grant(row) for row in state['grants'].values()]

    def revoke_main(self, *, main_id, main_digest):
        _need(_integer(main_id) and _hex(main_digest), 'MAIN_IDENTITY')
        with self._locked(create=True) as (directory, proof):
            state, before = self._load(directory, missing=True)
            key = _binding(main_id, main_digest)
            if key in state['revoked_bindings']:
                self._ack(directory, proof, before)
                return {'revoked': True}
            known = _known_bindings(state)
            # Every issued binding reserves its eventual tombstone capacity;
            # unrelated reductions cannot exhaust revocation of issued grants.
            _need(key in known or len(known) < MAX_BINDINGS, 'CAPACITY')
            # Retain the binding tombstone even if no Root grant exists yet.
            # A late issue based on an earlier DB observation must not reopen
            # the Root-disable -> ordinary-DB-revoke commit window.
            state['revoked_bindings'][key] = dict(main_id=main_id, main_digest=main_digest,
                                                 revoked_at=self._now())
            for row in state['grants'].values():
                if row['main_id'] == main_id and row['main_digest'] == main_digest:
                    row['revoked'] = True
            self._save(directory, proof, state, before)
            return {'revoked': True}

    def advance_epoch(self):
        """Root restore calls this before any data mutation; never restore it."""
        with self._locked(create=True) as (directory, proof):
            state, before = self._load(directory, missing=True)
            next_epoch = uuid.uuid4().hex
            _need(next_epoch != state['epoch'], 'COLLISION')
            state['epoch'] = next_epoch
            for row in state['grants'].values():
                row['revoked'] = True
            self._save(directory, proof, state, before)
            return {'advanced': True}

    def status(self, *, token, main_id, main_digest):
        """Authenticated Root capability metadata; no admission or mutation."""
        _need(type(token) is str and TOKEN.fullmatch(token) is not None, 'AUTHORITY')
        _need(_integer(main_id) and _hex(main_digest), 'REQUEST')
        with self._locked() as (directory, proof):
            state, before = self._load(directory)
            grant = state['grants'].get(TOKEN.fullmatch(token).group(1))
            _need(grant is not None and hmac.compare_digest(grant['secret_sha256'],
                  hashlib.sha256(token.encode('ascii')).hexdigest()) and grant['main_id'] == main_id
                  and hmac.compare_digest(grant['main_digest'], main_digest), 'AUTHORITY')
            self._live(state, grant, self._now(), directory, proof, before)
            return self._public_grant(grant)

    def reserve(self, *, token, main_id, main_digest, request_id, action, parameters):
        _need(type(token) is str and TOKEN.fullmatch(token) is not None, 'AUTHORITY')
        _need(_integer(main_id) and _hex(main_digest) and _hex(request_id, ID), 'REQUEST')
        parameters = normalized(action, parameters)
        digest = _digest(dict(action=action, parameters=parameters))
        with self._locked() as (directory, proof):
            state, before = self._load(directory)
            grant = state['grants'].get(TOKEN.fullmatch(token).group(1))
            _need(grant is not None and hmac.compare_digest(grant['secret_sha256'],
                  hashlib.sha256(token.encode('ascii')).hexdigest()) and grant['main_id'] == main_id
                  and hmac.compare_digest(grant['main_digest'], main_digest), 'AUTHORITY')
            now = self._now();self._live(state, grant, now, directory, proof, before)
            _need(_scope(action, parameters) in grant['scopes'], 'SCOPE')
            for row in state['admissions'].values():
                if row['grant_id'] == grant['id'] and row['request_id'] == request_id:
                    _need(hmac.compare_digest(row['parameters_sha256'], digest), 'CONFLICT')
                    self._ack(directory, proof, before)
                    return deepcopy(row), False
            pending=state['backup_recovery']
            if pending is not None:
                _need(action=='recover' and parameters['recovery_kind']=='backup'
                      and parameters['pending_transaction']==pending['transaction']
                      and parameters['pending_sequence']==pending['sequence']
                      and parameters['selection_revision']==pending['before']['revision'], 'BACKUP_PENDING')
            _need(len(state['admissions']) < MAX_ADMISSIONS, 'CAPACITY')
            identity = uuid.uuid4().hex
            _need(identity not in state['admissions'], 'COLLISION')
            row = dict(id=identity, grant_id=grant['id'], request_id=request_id,
                       installation_id=state['installation_id'], epoch=state['epoch'], action=action,
                       parameters=parameters, parameters_sha256=digest, created_at=now, phase='preflight',
                       plan_sha256=None, mutation_at=None)
            state['admissions'][identity] = row
            self._save(directory, proof, state, before)
            return deepcopy(row), True

    def lookup(self, *, token, main_id, main_digest, request_id):
        _need(_hex(request_id,ID),'REQUEST')
        grant=self.status(token=token,main_id=main_id,main_digest=main_digest)
        with self._locked() as (directory,proof):
            state,_=self._load(directory)
            for row in state['admissions'].values():
                if row['grant_id']==grant['id'] and row['request_id']==request_id:return deepcopy(row)
            return None

    def admission(self, *, task_id, live=True):
        """Read a Root-reserved immutable intent, never caller parameters.
        Live preflight checks do not consume it or authorize a first mutation.
        """
        _need(_hex(task_id,ID) and type(live) is bool,'REQUEST')
        with self._locked() as (directory,proof):
            state,before=self._load(directory)
            row=state['admissions'].get(task_id)
            _need(row is not None,'ADMISSION')
            if live:
                _need(row['phase']=='preflight','CONSUMED')
                grant=state['grants'][row['grant_id']]
                self._live(state,grant,self._now(),directory,proof,before)
                _need(_scope(row['action'],row['parameters']) in grant['scopes'],'SCOPE')
            return deepcopy(row)

    def enter_mutation(self, *, task_id, plan_sha256, backup_recovery=None):
        """Root controller calls while retaining the installation/CAS lock.
        This persistent one-shot mark is not an executable or replay ticket.
        """
        _need(_hex(task_id, ID) and _hex(plan_sha256), 'REQUEST')
        with self._locked() as (directory, proof):
            state, before = self._load(directory)
            row = state['admissions'].get(task_id)
            _need(row is not None and row['phase'] == 'preflight', 'CONSUMED')
            grant = state['grants'][row['grant_id']]
            now = self._now();self._live(state, grant, now, directory, proof, before)
            _need(_scope(row['action'], row['parameters']) in grant['scopes'], 'SCOPE')
            _need(row['action']!='backup' or backup_recovery is not None,'BACKUP_RECOVERY')
            pending=state['backup_recovery']
            if pending is not None:
                parameters=row['parameters']
                _need(row['action']=='recover' and parameters['recovery_kind']=='backup'
                      and parameters['pending_transaction']==pending['transaction']
                      and parameters['pending_sequence']==pending['sequence']
                      and parameters['selection_revision']==pending['before']['revision'], 'BACKUP_PENDING')
            if backup_recovery is not None:
                pending=_backup_recovery(backup_recovery)
                _need(row['action']=='backup' and state['backup_recovery'] is None
                      and pending['transaction']==task_id and pending['installation_id']==state['installation_id']
                      and pending['before']['revision']==row['parameters']['selection_revision'], 'BACKUP_RECOVERY')
                state['backup_recovery']=pending
            row.update(phase='mutating', plan_sha256=plan_sha256, mutation_at=now)
            self._save(directory, proof, state, before)
            return deepcopy(row)


    def backup_recovery(self):
        """Root facts only; a retained record never schedules execution."""
        try:
            with self._locked() as (directory,proof):
                state,before=self._load(directory)
                self._ack(directory,proof,before)
                return deepcopy(state['backup_recovery'])
        except CoreAuthorityError as error:
            if error.code=='NOT_INITIALIZED':return None
            raise

    def finish_backup_recovery(self, expected):
        expected=_backup_recovery(expected)
        with self._locked() as (directory,proof):
            state,before=self._load(directory)
            _need(state['backup_recovery']==expected,'BACKUP_RECOVERY_CHANGED')
            state['backup_recovery']=None
            self._save(directory,proof,state,before)


    def record_application(self, record):
        """Root producer only: facts must come from its accepted original ZIP
        snapshot. This method does not authenticate arbitrary dictionary origin.
        No public socket/API or service-UID caller may invoke this producer.
        """
        record = _application(record)
        key = _application_key(record['release_id'], record['target'])
        with self._locked(create=True) as (directory, proof):
            state, before = self._load(directory, missing=True)
            previous = state['applications'].get(key)
            if previous is not None:
                _need(previous == record, 'APPLICATION_CONFLICT')
                self._ack(directory, proof, before)
                return deepcopy(previous)
            _need(len(state['applications']) < MAX_APPLICATIONS, 'CAPACITY')
            state['applications'][key] = record
            self._save(directory, proof, state, before)
            return deepcopy(record)

    def application(self, *, release_id, target):
        """Root observation only. Missing authority state remains an error;
        a missing accepted origin returns None, never a service-metadata fill.
        """
        _need(_hex(release_id, RELEASE) and type(target) is str and target in TARGETS, 'APPLICATION')
        with self._locked() as (directory, proof):
            state, before = self._load(directory)
            row = state['applications'].get(_application_key(release_id, target))
            if row is None:
                return None
            self._ack(directory, proof, before)
            return deepcopy(row)
