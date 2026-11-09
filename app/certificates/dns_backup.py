"""Stdlib-only offline DNS reference closure and release-reader capability fence.

No network, ORM, credential repair, or candidate code execution. Callers hold the
whole-data exclusion. Permission normalization/suspension is limited to a new
private .backup-*/data or .restore-*/data staging tree, never live data.
"""
import ast
import os
from pathlib import Path
import re
import sqlite3
import stat

from app.certificates.dns_store import DNSCredentialStore, DNSStoreError, normalize_zone, private_directory

MAX_CERTIFICATES = 4096
MAX_SOURCE_BYTES = 512 * 1024
_REQUIRED = {'id', 'domain', 'environment', 'method', 'wildcard', 'credential_id'}


class DNSBackupError(ValueError):
    def __init__(self, code='DNS_BACKUP_INVALID'):
        self.code = code
        super().__init__(code)


def _need(value):
    if not value:
        raise DNSBackupError()


def _id(value):
    _need(type(value) is str and re.fullmatch(r'[a-f0-9]{32}', value))
    return value


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _database_references(data, *, private_dns):
    path = data / 'v-ui.db'
    if not os.path.lexists(path):
        _need(not private_dns)
        return []
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        _need(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and before.st_nlink == 1
              and not before.st_mode & 0o022 and before.st_size > 0)
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        try:
            db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            objects = dict(db.execute("SELECT name,type FROM sqlite_master WHERE name IN "
                "('managed_certificates','certificate_bindings','certificate_jobs','certificate_identity_schema')"))
            _need(all(kind == 'table' for kind in objects.values()))
            columns = {row[1] for row in db.execute('PRAGMA table_info(managed_certificates)')}
            marked = 'certificate_identity_schema' in objects
            modern = _REQUIRED <= columns
            if marked:
                marker = list(db.execute('PRAGMA table_info(certificate_identity_schema)'))
                _need(len(marker) == 1 and marker[0][1] == 'version' and marker[0][2].upper() == 'INTEGER'
                      and marker[0][3] == 1 and list(db.execute('SELECT typeof(version),version FROM '
                      'certificate_identity_schema')) == [('integer', 1)])
                _need(modern and {'managed_certificates', 'certificate_bindings', 'certificate_jobs'} <= set(objects)
                      and 'server_name' in {row[1] for row in db.execute('PRAGMA table_info(certificate_bindings)')})
            _need(not private_dns or marked)
            if not modern:
                _need(not marked and not ({'method', 'wildcard', 'credential_id'} & columns))
                return []
            rows = list(db.execute('SELECT id,domain,environment,method,wildcard,credential_id '
                                   'FROM managed_certificates LIMIT ?', (MAX_CERTIFICATES + 1,)))
            _need(len(rows) <= MAX_CERTIFICATES and (marked or not rows))
            refs = []; ids = set(); identities = set()
            for identifier, domain, environment, method, wildcard, credential in rows:
                _id(identifier)
                _need(identifier not in ids and type(domain) is str and normalize_zone(domain) == domain
                      and environment in ('production', 'staging') and method in ('http-01', 'dns-01')
                      and type(wildcard) is int and wildcard in (0, 1))
                identity = (domain, environment, method, wildcard)
                _need(identity not in identities)
                ids.add(identifier); identities.add(identity)
                if method == 'http-01':
                    _need(wildcard == 0 and credential is None)
                else:
                    refs.append((_id(credential), domain))
            return refs
        finally:
            db.close()
    finally:
        try:
            _need(_signature(before) == _signature(os.fstat(fd))
                  == _signature(path.lstat()))
        finally:
            os.close(fd)


def _credential_payload_present(store):
    if not os.path.lexists(store.root):
        return False
    with store._root() as root:
        for name in os.listdir(root):
            if name == 'owner.lock': continue
            pending = name.startswith('pending-')
            with store._profile(root, name[8:] if pending else name, pending=pending) as profile:
                if any(child != 'owner.lock' for child in os.listdir(profile)):
                    return True
    return False


def validate_dns_snapshot(data) -> bool:
    """Validate all private files and references without writing or exposing secrets."""
    _need('..' not in Path(data).parts)
    data = Path(os.path.abspath(data))
    try:
        if not os.path.lexists(data):
            return False
        with private_directory(data):
            pass
        certificates = data / 'certificates'
        store = DNSCredentialStore(certificates)
        journal_path = certificates / 'dns-challenges'
        # Inspect the parent without following a link even on a legacy tree.
        if os.path.lexists(certificates):
            fd = os.open(certificates, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            os.close(fd)
        present = os.path.lexists(store.root) or os.path.lexists(journal_path)
        profiles = store.validate_snapshot()
        refs = []
        if os.path.lexists(journal_path):
            from app.certificates.dns_journal import DNSChallengeJournal
            refs = DNSChallengeJournal(certificates).validate_for_backup()
            _need(type(refs) is list and len(refs) <= 256)
        required_profiles = _database_references(data, private_dns=present)
        for identifier, domain in required_profiles:
            _need(identifier in profiles)
            zone = profiles[identifier]['zone_name']
            _need(domain == zone or domain.endswith('.' + zone))
        seen = set()
        for ref in refs:
            required = {'job_id', 'credential_id', 'credential_revision', 'provider', 'zone_id', 'zone_name'}
            _need(type(ref) is dict and required <= set(ref)
                  and set(ref) <= required | {'recovery_credential_revision'})
            job = _id(ref['job_id']); selected = _id(ref['credential_id']); revision = _id(ref['credential_revision'])
            _need(job not in seen and selected in profiles and revision in profiles[selected]['revisions'])
            _need(all(ref[key] == profiles[selected][key] for key in ('provider', 'zone_id', 'zone_name')))
            seen.add(job)
            recovery = ref.get('recovery_credential_revision')
            if recovery is not None:
                _need(_id(recovery) in profiles[selected]['revisions'])
        journal_payload = False
        if os.path.lexists(journal_path):
            with private_directory(journal_path) as directory:
                journal_payload = any(name != 'owner.lock' for name in os.listdir(directory))
        return bool(_credential_payload_present(store) or journal_payload or required_profiles or refs)
    except DNSBackupError:
        raise
    except Exception:
        # Upstream journal/store diagnostics may contain private data in a future
        # implementation. This offline boundary reports only one fixed code.
        raise DNSBackupError() from None


def require_dns_release(data, payload):
    """Read one literal capability from verified source; never import the candidate."""
    if not validate_dns_snapshot(data):
        return
    _need('..' not in Path(payload).parts)
    source = Path(payload) / 'app' / 'certificates' / 'schema.py'
    fd = None; parents = []
    try:
        absolute = Path(os.path.abspath(source))
        directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW); parents.append(directory)
        for part in absolute.parts[1:-1]:
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            parents.append(directory)
        fd = os.open(absolute.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(fd)
        _need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
              and not info.st_mode & 0o022 and 0 < info.st_size <= MAX_SOURCE_BYTES)
        with os.fdopen(os.dup(fd), 'rb') as stream:
            raw = stream.read(MAX_SOURCE_BYTES + 1)
        _need(len(raw) == info.st_size and _signature(info) == _signature(os.fstat(fd))
              == _signature(os.stat(absolute.name, dir_fd=directory, follow_symlinks=False)))
        module = ast.parse(raw)
        assignments = [node for node in module.body if isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(isinstance(target, ast.Name) and target.id == 'DNS_IDENTITY_SCHEMA_MAX'
                    for target in (node.targets if isinstance(node, ast.Assign) else [node.target]))]
        _need(len(assignments) == 1 and isinstance(assignments[0], ast.Assign)
              and len(assignments[0].targets) == 1 and isinstance(assignments[0].value, ast.Constant)
              and type(assignments[0].value.value) is int and assignments[0].value.value >= 1)
    except Exception:
        raise DNSBackupError('DNS_RELEASE_READER_REQUIRED') from None
    finally:
        if fd is not None: os.close(fd)
        for directory in reversed(parents): os.close(directory)


def prepare_dns_staging(data):
    """Normalize only exact DNS directories in a newly extracted private staging tree."""
    _need('..' not in Path(data).parts)
    data = Path(os.path.abspath(data))
    _need(data.name == 'data' and data.parent.name.startswith(('.restore-', '.backup-')))
    descriptors = []
    def directory(parent, name):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        descriptors.append(fd); info = os.fstat(fd)
        _need(info.st_uid == os.geteuid() and stat.S_ISDIR(info.st_mode))
        return fd
    try:
        with private_directory(data) as root:
            if 'certificates' not in os.listdir(root): return
            cert = directory(root, 'certificates')
            namespaces = set(os.listdir(cert)) & {'dns-credentials', 'dns-challenges'}
            if not namespaces: return
            for name in sorted(namespaces):
                parent = directory(cert, name); entries = os.listdir(parent)
                _need(len(entries) <= (33 if name == 'dns-credentials' else 1024))
                for entry in entries:
                    if name == 'dns-credentials' and entry != 'owner.lock':
                        _need(re.fullmatch(r'(?:pending-)?[a-f0-9]{32}', entry))
                        profile = directory(parent, entry); children = os.listdir(profile)
                        _need(len(children) <= 259 and all(child in ('owner.lock', 'state.json')
                            or re.fullmatch(r'[a-f0-9]{32}\.json|\.write-[a-f0-9]{32}', child) for child in children))
                        _need(all(not stat.S_ISDIR(os.stat(child, dir_fd=profile, follow_symlinks=False).st_mode)
                                  for child in children))
                    else:
                        _need(entry == 'owner.lock' or name == 'dns-challenges'
                              and re.fullmatch(r'[a-f0-9]{32}\.json|\.write-[a-f0-9]{32}', entry))
                        _need(not stat.S_ISDIR(os.stat(entry, dir_fd=parent, follow_symlinks=False).st_mode))
            # No change occurs until every directory's exact namespace/ownership
            # has been checked. Files are subsequently checked strictly at 0600.
            for fd in descriptors:
                os.fchmod(fd, 0o700); os.fsync(fd)
    except DNSBackupError:
        raise
    except Exception:
        raise DNSBackupError() from None
    finally:
        for fd in reversed(descriptors): os.close(fd)


def suspend_restored_dns(data):
    """Only private incoming staging may revoke restored DNS authority."""
    prepare_dns_staging(data)
    present = validate_dns_snapshot(data)
    store = DNSCredentialStore(Path(data) / 'certificates')
    try:
        if os.path.lexists(store.root):
            store.suspend_restored()
        _need(validate_dns_snapshot(data) == present)
    except DNSBackupError:
        raise
    except Exception:
        raise DNSBackupError() from None
    return present
