"""Private, versioned DNS credentials. No network, ORM, or runtime initialization.

Callers hold data_owner for the surrounding data tree. Per-profile locks protect
CAS state changes; immutable credential revisions keep active jobs independent
of rotation. Public methods never return token suffixes or token hashes.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import uuid


class DNSStoreError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


MAX_JSON = 16_384
MAX_PROFILES = 32
MAX_REVISIONS = 256
_STATE_KEYS = {'schema', 'id', 'provider', 'label', 'zone_name', 'zone_id',
               'revision', 'credential_revision', 'enabled', 'restore_pending'}
_SECRET_KEYS = {'schema', 'id', 'provider', 'zone_name', 'zone_id', 'revision', 'api_token'}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value):
        raise DNSStoreError('INVALID_DNS_CREDENTIAL_ID')
    return value


def normalize_zone(value):
    try:
        if not isinstance(value, str) or value != value.strip():
            raise ValueError()
        result = value.rstrip('.').encode('idna').decode('ascii').lower()
        if (not 3 <= len(result) <= 253 or '.' not in result or result.split('.')[-1].isdigit()
                or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                           for label in result.split('.'))):
            raise ValueError()
        return result
    except (ValueError, UnicodeError):
        raise DNSStoreError('INVALID_DNS_ZONE') from None


def _token(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,256}', value):
        raise DNSStoreError('INVALID_DNS_TOKEN')
    return value


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    try:
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_JSON:
            raise ValueError()
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if type(value) is not dict:
            raise ValueError()
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise DNSStoreError('DNS_CREDENTIALS_INVALID') from None


def _check_file(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
            or not 0 < info.st_size <= MAX_JSON):
        raise DNSStoreError('DNS_CREDENTIALS_UNSAFE')


def _read(fd, name):
    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        _check_file(os.fstat(child))
        raw = os.read(child, MAX_JSON + 1)
        return _json(raw)
    finally:
        os.close(child)


def _write(fd, name, value, *, replace=False):
    raw = (json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    if len(raw) > MAX_JSON:
        raise DNSStoreError('DNS_CREDENTIALS_INVALID')
    temporary = '.write-' + uuid.uuid4().hex
    child = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600, dir_fd=fd)
    try:
        with os.fdopen(child, 'wb', closefd=False) as stream:
            stream.write(raw); stream.flush(); os.fsync(child)
        if replace:
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        else:
            # No-overwrite publication, even if a supposedly new identity exists.
            os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            os.unlink(temporary, dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            info = os.stat(temporary, dir_fd=fd, follow_symlinks=False)
            if (info.st_dev, info.st_ino) == (os.fstat(child).st_dev, os.fstat(child).st_ino):
                os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass
        finally:
            os.close(child)


def _state(value, selected):
    try:
        if (set(value) != _STATE_KEYS or type(value['schema']) is not int or value['schema'] != 1
                or value['id'] != selected or value['provider'] != 'cloudflare'
                or type(value['enabled']) is not bool or type(value['restore_pending']) is not bool
                or not isinstance(value['label'], str) or not 1 <= len(value['label']) <= 64
                or value['label'] != value['label'].strip()
                or any(ord(c) < 32 or ord(c) == 127 for c in value['label'])):
            raise ValueError()
        for key in ('id', 'zone_id', 'revision', 'credential_revision'):
            identifier(value[key])
        if normalize_zone(value['zone_name']) != value['zone_name']:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise DNSStoreError('DNS_CREDENTIALS_INVALID') from None
    return value


def _secret(value, state, revision):
    try:
        if (set(value) != _SECRET_KEYS or type(value['schema']) is not int or value['schema'] != 1
                or value['revision'] != revision
                or any(value[key] != state[key] for key in ('id', 'provider', 'zone_name', 'zone_id'))):
            raise ValueError()
        _token(value['api_token'])
    except (ValueError, TypeError, KeyError):
        raise DNSStoreError('DNS_CREDENTIALS_INVALID') from None
    return value


@contextmanager
def private_directory(path, *, create=False):
    """Walk trusted ancestors; the last two directories are private and owned."""
    selected = Path(path)
    if not selected.is_absolute() or ".." in selected.parts:
        raise DNSStoreError("DNS_CREDENTIALS_UNSAFE")
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, part in enumerate(selected.parts[1:], start=1):
            managed = index >= len(selected.parts) - 2
            if create and managed:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd); os.fsync(fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
            info = os.fstat(fd)
            trusted = info.st_uid in (0, os.geteuid())
            writable = bool(info.st_mode & 0o022)
            if (not trusted or (writable and not info.st_mode & stat.S_ISVTX)
                    or (managed and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700))):
                raise DNSStoreError('DNS_CREDENTIALS_UNSAFE')
        yield fd
    except OSError:
        raise DNSStoreError('DNS_CREDENTIALS_UNSAFE') from None
    finally:
        os.close(fd)


class DNSCredentialStore:
    def __init__(self, certificates_root):
        selected = Path(certificates_root)
        if '..' in selected.parts:
            raise DNSStoreError('DNS_CREDENTIALS_UNSAFE')
        self.root = Path(os.path.abspath(selected)) / 'dns-credentials'

    def _root(self, *, create=False):
        return private_directory(self.root, create=create)

    @contextmanager
    def _profile(self, root, selected, *, create=False, pending=False):
        identifier(selected)
        name = 'pending-' + selected if pending else selected
        if create:
            os.mkdir(name, 0o700, dir_fd=root); os.fsync(root)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        except FileNotFoundError:
            raise DNSStoreError('DNS_CREDENTIAL_NOT_FOUND') from None
        try:
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise DNSStoreError('DNS_CREDENTIALS_UNSAFE')
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def _lock(self, fd):
        lock = os.open('owner.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                       0o600, dir_fd=fd)
        try:
            info = os.fstat(lock)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or info.st_size or stat.S_IMODE(info.st_mode) != 0o600):
                raise DNSStoreError('DNS_CREDENTIALS_UNSAFE')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DNSStoreError('DNS_CREDENTIAL_BUSY') from None
            yield
        finally:
            os.close(lock)

    @staticmethod
    def public(state):
        value = {key: state[key] for key in ('id', 'provider', 'label', 'zone_name', 'revision',
                                            'enabled', 'restore_pending')}
        value['status'] = 'restore_pending' if state['restore_pending'] else 'configured' if state['enabled'] else 'disabled'
        return value

    def list(self):
        # Absence is a normal legacy installation. Any existing unsafe object fails.
        if not os.path.lexists(self.root):
            return []
        with self._root() as root, self._lock(root):
            names = [name for name in os.listdir(root) if name != 'owner.lock']
            if len(names) > MAX_PROFILES:
                raise DNSStoreError('DNS_CREDENTIAL_LIMIT_REACHED')
            result = []
            for selected in sorted(names):
                if re.fullmatch('pending-[a-f0-9]{32}', selected):
                    # An interrupted pre-publication write is never a usable
                    # profile. Keep its private bytes for explicit recovery.
                    with self._profile(root, selected[8:], pending=True):
                        pass
                    continue
                identifier(selected)
                with self._profile(root, selected) as fd:
                    state = _state(_read(fd, 'state.json'), selected)
                    _secret(_read(fd, state['credential_revision'] + '.json'), state, state['credential_revision'])
                    result.append(self.public(state))
            return result

    def validate_snapshot(self):
        """Read-only full private-tree validation; no token/hash escapes this scan.

        Caller holds the surrounding data-owner exclusion. Unpublished pending
        profiles and bounded atomic-write remnants are retained, never promoted.
        Missing transient owner.lock files are normal in an extracted backup.
        """
        if not os.path.lexists(self.root):
            return {}
        def signature(info):
            return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
                    info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        def read(directory, name, *, lock=False, fragment=False):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                        or not 0 <= info.st_size <= MAX_JSON or lock and info.st_size):
                    raise DNSStoreError('DNS_CREDENTIALS_UNSAFE')
                raw = os.read(fd, MAX_JSON + 1)
                if (len(raw) != info.st_size or signature(os.fstat(fd)) != signature(info)
                        or signature(os.stat(name, dir_fd=directory, follow_symlinks=False)) != signature(info)):
                    raise DNSStoreError('DNS_CREDENTIALS_UNSAFE')
                if lock or fragment:
                    return None
                value = _json(raw)
                # Reject escaped lone surrogates/nonfinite numeric JSON too.
                json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
                return value
            finally:
                os.close(fd)
        try:
            with self._root() as root:
                root_info = os.fstat(root)
                root_names = sorted(os.listdir(root))
                if len(root_names) > MAX_PROFILES + 1:
                    raise DNSStoreError('DNS_CREDENTIAL_LIMIT_REACHED')
                result = {}
                for name in root_names:
                    if name == 'owner.lock':
                        read(root, name, lock=True)
                        continue
                    pending = re.fullmatch(r'pending-([a-f0-9]{32})', name)
                    selected = pending.group(1) if pending else identifier(name)
                    with self._profile(root, selected, pending=bool(pending)) as directory:
                        directory_info = os.fstat(directory)
                        entries = sorted(os.listdir(directory))
                        if len(entries) > MAX_REVISIONS + 3:
                            raise DNSStoreError('DNS_CREDENTIAL_REVISION_LIMIT_REACHED')
                        state = None; revisions = {}; provisional = None
                        for child in entries:
                            if child == 'owner.lock':
                                read(directory, child, lock=True)
                            elif child == 'state.json':
                                state = _state(read(directory, child), selected)
                            elif re.fullmatch(r'[a-f0-9]{32}\.json', child):
                                revisions[child[:-5]] = read(directory, child)
                            elif re.fullmatch(r'\.write-[a-f0-9]{32}', child):
                                read(directory, child, fragment=True)
                            else:
                                raise DNSStoreError('DNS_CREDENTIALS_INVALID')
                        if len(revisions) > MAX_REVISIONS:
                            raise DNSStoreError('DNS_CREDENTIAL_REVISION_LIMIT_REACHED')
                        for revision, value in sorted(revisions.items()):
                            if provisional is None:
                                provisional = _state(dict(schema=1, id=selected, provider=value.get('provider'),
                                    label='unpublished', zone_name=value.get('zone_name'), zone_id=value.get('zone_id'),
                                    revision=revision, credential_revision=revision, enabled=False, restore_pending=True), selected)
                            _secret(value, state or provisional, revision)
                        if state is not None:
                            if state['credential_revision'] not in revisions:
                                raise DNSStoreError('DNS_CREDENTIALS_INVALID')
                        elif not pending:
                            raise DNSStoreError('DNS_CREDENTIALS_INVALID')
                        if (entries != sorted(os.listdir(directory))
                                or signature(directory_info) != signature(os.fstat(directory))
                                or signature(directory_info) != signature(os.stat(name, dir_fd=root, follow_symlinks=False))):
                            raise DNSStoreError('DNS_CREDENTIALS_UNSAFE')
                        if not pending:
                            result[selected] = {key: state[key] for key in ('id', 'provider', 'zone_name', 'zone_id',
                                'revision', 'credential_revision', 'enabled', 'restore_pending')}
                            result[selected]['revisions'] = sorted(revisions)
                if (root_names != sorted(os.listdir(root)) or signature(root_info) != signature(os.fstat(root))
                        or signature(root_info) != signature(os.stat(self.root, follow_symlinks=False))):
                    raise DNSStoreError('DNS_CREDENTIALS_UNSAFE')
                return result
        except DNSStoreError:
            raise
        except (OSError, ValueError, TypeError, UnicodeError):
            raise DNSStoreError('DNS_CREDENTIALS_INVALID') from None

    def create(self, *, label, zone_name: str, zone_id: str, api_token: str):
        selected, revision = uuid.uuid4().hex, uuid.uuid4().hex
        state = _state(dict(schema=1, id=selected, provider='cloudflare', label=label,
                            zone_name=normalize_zone(zone_name), zone_id=identifier(zone_id),
                            revision=uuid.uuid4().hex, credential_revision=revision,
                            enabled=True, restore_pending=False), selected)
        secret = {key: state[key] for key in ('schema', 'id', 'provider', 'zone_name', 'zone_id')}
        secret.update(revision=revision, api_token=_token(api_token))
        with self._root(create=True) as root, self._lock(root):
            if len([name for name in os.listdir(root) if name != 'owner.lock']) >= MAX_PROFILES:
                raise DNSStoreError('DNS_CREDENTIAL_LIMIT_REACHED')
            with self._profile(root, selected, create=True, pending=True) as fd, self._lock(fd):
                _write(fd, revision + '.json', secret)
                _write(fd, 'state.json', state)
            # Publish the complete private directory under the store-wide
            # writer lock. The name is a freshly generated opaque UUID.
            try:
                os.stat(selected, dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise DNSStoreError('DNS_CREDENTIAL_REVISION_CONFLICT')
            os.rename('pending-' + selected, selected, src_dir_fd=root, dst_dir_fd=root)
            os.fsync(root)
        return self.public(state)

    def snapshot(self, selected, *, revision=None, for_cleanup=False):
        """Internal secret-bearing value; never send this object to an API/log."""
        with self._root() as root, self._profile(root, selected) as fd:
            state = _state(_read(fd, 'state.json'), selected)
            if state['restore_pending']:
                raise DNSStoreError('DNS_CREDENTIAL_RESTORE_PENDING')
            if not state['enabled'] and not for_cleanup:
                raise DNSStoreError('DNS_CREDENTIAL_DISABLED')
            selected_revision = identifier(revision) if revision is not None else state['credential_revision']
            value = _secret(_read(fd, selected_revision + '.json'), state, selected_revision)
            return {**value, 'profile_revision': state['revision']}

    def verification_snapshot(self, selected, *, expected_revision):
        """Admin-only caller's read-only upstream check before explicit enable.

        This bypasses local disable/restore flags solely to verify the current
        token/Zone, never for issuing, creating TXT, or automatic cleanup.
        """
        identifier(expected_revision)
        with self._root() as root, self._profile(root, selected) as fd:
            state = _state(_read(fd, 'state.json'), selected)
            if state['revision'] != expected_revision:
                raise DNSStoreError('DNS_CREDENTIAL_REVISION_CONFLICT')
            value = _secret(_read(fd, state['credential_revision'] + '.json'), state, state['credential_revision'])
            return {**value, 'profile_revision': state['revision']}

    def _change(self, selected, expected_revision, operation):
        identifier(expected_revision)
        with self._root() as root, self._profile(root, selected) as fd, self._lock(fd):
            state = _state(_read(fd, 'state.json'), selected)
            if state['revision'] != expected_revision:
                raise DNSStoreError('DNS_CREDENTIAL_REVISION_CONFLICT')
            operation(fd, state)
            state['revision'] = uuid.uuid4().hex
            _write(fd, 'state.json', _state(state, selected), replace=True)
            return self.public(state)

    def replace(self, selected, *, expected_revision, api_token):
        token = _token(api_token)
        def change(fd, state):
            if len(os.listdir(fd)) >= MAX_REVISIONS + 2:
                raise DNSStoreError('DNS_CREDENTIAL_REVISION_LIMIT_REACHED')
            revision = uuid.uuid4().hex
            secret = {key: state[key] for key in ('schema', 'id', 'provider', 'zone_name', 'zone_id')}
            secret.update(revision=revision, api_token=token)
            _write(fd, revision + '.json', secret)
            state['credential_revision'] = revision
        return self._change(selected, expected_revision, change)

    def set_enabled(self, selected, *, expected_revision, enabled, confirmed=False):
        if type(enabled) is not bool or (enabled and confirmed is not True):
            raise DNSStoreError('DNS_CREDENTIAL_CONFIRMATION_REQUIRED')
        def change(fd, state):
            # The caller first revalidates the current token/zone for an enable.
            _secret(_read(fd, state['credential_revision'] + '.json'), state, state['credential_revision'])
            state['enabled'] = enabled
            if enabled:
                state['restore_pending'] = False
        return self._change(selected, expected_revision, change)

    def suspend_restored(self):
        """Private extraction only: restoring cannot reactivate old DNS authority."""
        for profile in self.list():
            def suspend(fd, state):
                state['restore_pending'], state['enabled'] = True, False
            self._change(profile['id'], profile['revision'], suspend)
