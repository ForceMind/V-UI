"""Private complete backup container: original data ZIP plus component closure.

Writer requires the caller's actual stopped guard. Preflight is read-only with
respect to live data/Root state and does not authorize restore or execution.
No Root store/selection write, DNS suspension, revocation or data swap occurs.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import zipfile

from app import release_tools as tools
from . import artifacts as a, selection as s
from .backup import CoreBackup
from .boot import _runtime_states

KIND = 'backup-components-v1'
MAX_MEMBERS = 15
MAX_CORE_METADATA = 9 * a.MAX_JSON
MAX_CONTENT = tools.MAX_ARCHIVE + 4*a.MAX_PACK + MAX_CORE_METADATA
MAX_EXPANDED = MAX_CONTENT + 4_000_000
MAX_ARCHIVE = MAX_EXPANDED + 4_000_000


def _need(value, message='Component backup is unsafe or incomplete'):
    if not value:
        raise tools.ReleaseError(message)


def _data_bindings(data, closure, *, anchor):
    """Preserve original runtime bytes; report bundle-only legacy migration."""
    observed = _runtime_states(data, owner=os.geteuid(), anchor=anchor)
    migration = []
    for core in s.CORES:
        expected = closure.intents[core]
        actual = observed.get(core)
        if actual is None:
            _need(expected['revision'] is None and expected['enabled'] is False)
            continue
        _need(actual['revision'] == expected['revision'] and actual['enabled'] == expected['enabled'])
        if actual['_schema'] == 2:
            _need(actual['artifact_id'] == expected['artifact_id'])
        else:
            _need(type(closure.selection.slot(core).current) is s.BundleReference)
            migration.append(core)
    return tuple(migration)


def _data_format(data, kind):
    _need(kind in ('backup', 'backup-dns01-v1'))
    from app.certificates.dns_backup import validate_dns_snapshot
    _need(bool(validate_dns_snapshot(data)) == (kind == 'backup-dns01-v1'))
    database = data/'v-ui.db'
    _need(database.is_file() and not database.is_symlink())
    with sqlite3.connect('file:'+str(database)+'?mode=ro', uri=True) as connection:
        _need(connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok')
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        _need({'users', 'inbounds', 'admin_sessions', 'subscription_grants'} <= tables)
    for name in ('runtime', 'runtime/sing-box', 'runtime/xray'):
        path = data/name
        if not os.path.lexists(path):
            continue
        _need(path.is_dir() and not path.is_symlink() and path.stat().st_uid == os.geteuid())
        path.chmod(0o700)  # Only exact directories in our new private staging tree.
    proxy_names = ('proxy-state', '.proxy-state-initialized', '.proxy-state.lock')
    if any(os.path.lexists(data/name) for name in proxy_names):
        from app.services.proxy_store import ProxyStore
        _need(not os.path.lexists(data/'.proxy-state.lock'))
        secrets = data/'proxy-state/secrets'
        if not secrets.exists():
            secrets.mkdir(mode=0o700)
        for name in ('proxy-state', 'proxy-state/revisions', 'proxy-state/secrets',
                     'runtime', 'runtime/sing-box', 'runtime/xray'):
            path = data/name
            if name.startswith('runtime') and not path.exists():
                continue
            _need(path.is_dir() and not path.is_symlink() and path.stat().st_uid == os.geteuid())
            path.chmod(0o700)  # Only exact new private extraction directories.
        with ProxyStore(data).backup_snapshot() as entries:
            for _ in entries:
                pass
        _need(ProxyStore(data).read_snapshot(initialize=False)['pending'] is None)
        tools._database_mode_fence(data, _existing_store_type=tools._readonly_proxy_store_type())
    else:
        tools._database_mode_fence(data, _existing_store_type=tools._readonly_proxy_store_type())



def _validate_data(data, kind, closure, *, anchor):
    from app.data_owner import data_owner
    # Only this newly created private staging tree is leased here. Shared DNS/
    # proxy reads borrow its actual EX open-file description in this thread;
    # ordinary live SH callers stay blocked by CORE/RESTORE pending. The sibling
    # lease is outside data/ and therefore never part of the data snapshot ZIP.
    with data_owner(data, exclusive=True):
        _data_format(data, kind)
        migration = _data_bindings(data, closure, anchor=anchor)
        if (data/'.proxy-state-initialized').exists():
            tools._validate_restore_policy(data, _artifact_ids={core:closure.intents[core]['artifact_id'] for core in s.CORES},
                                           _existing_store_type=tools._readonly_proxy_store_type())
            # The shared old checker owns this newly created private staging lock.
            (data/'.proxy-state.lock').unlink()
        return migration


def create(guard, destination, *, core_backup, selection, intents, panel_was_running):
    """Create the full new archive under the caller's actual stopped guard."""
    guard._check()
    _need(type(core_backup) is CoreBackup)
    destination = Path(destination)
    _need(destination.is_absolute() and '..' not in destination.parts and destination.name not in ('', '.', '..'))
    try:
        with tempfile.TemporaryDirectory(prefix='.backup-components-', dir=guard.root) as temporary:
            work = Path(temporary); work.chmod(0o700)
            data = work/'data'; data.mkdir(mode=0o700)
            private_files, data_kind = tools._copy_backup_data(guard, data)
            closure = core_backup.stage(work, 'core', selection=selection, intents=intents,
                                        panel_was_running=panel_was_running)
            _validate_data(data, data_kind, closure, anchor=work)
            table = tools.files_in(data)
            _need(sum(info['size'] for info in table.values()) <= tools.MAX_EXPANDED,
                  'Data snapshot exceeds its original expanded budget')
            _need(len(table)+1 <= 10000)
            container = work/'container'; container.mkdir(mode=0o700)
            (container/'data').mkdir(mode=0o700)
            data_zip = container/'data/snapshot.zip'
            tools.create_archive(data, data_zip,
                dict(kind=data_kind, data_path=str(guard.root/'data'), platform=tools.PLATFORM),
                private_files=private_files)
            _need(data_zip.stat().st_size <= tools.MAX_ARCHIVE, 'Data snapshot exceeds its original archive budget')
            with zipfile.ZipFile(data_zip) as inner:
                _need(len(inner.infolist()) <= 10000
                      and sum(item.file_size for item in inner.infolist()) <= tools.MAX_EXPANDED
                      and inner.getinfo(tools.MANIFEST).file_size <= 4_000_000,
                      'Data snapshot exceeds its original reader budget')
            os.rename(work/'core', container/'core')  # New private owned names.
            outer_files = tools.files_in(container)
            _need(len(outer_files)+1 <= MAX_MEMBERS and sum(item['size'] for item in outer_files.values()) <= MAX_CONTENT)
            prepared = work/'archive.zip'
            checksum = tools.create_archive(container, prepared,
                dict(kind=KIND, data_path=str(guard.root/'data'), platform=tools.PLATFORM, data_kind=data_kind),
                private_files=set(outer_files))
            _need(prepared.stat().st_size <= MAX_ARCHIVE)
            guard._check()
            with tools._maintenance_directory(destination.parent) as parent:
                os.link(prepared, destination.name, dst_dir_fd=parent, follow_symlinks=False)
                os.fsync(parent)
            return checksum
    except tools.ReleaseError:
        raise
    except Exception:
        raise tools.ReleaseError('Component backup creation failed; original data was not changed') from None


@contextmanager
def _snapshot(path, expected_sha, workspace):
    _need(type(expected_sha) is str and re.fullmatch('[a-f0-9]{64}', expected_sha))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        initial = os.fstat(source.fileno())
        _need(stat.S_ISREG(initial.st_mode) and initial.st_nlink == 1 and initial.st_size <= MAX_ARCHIVE)
        with tools._PrivateArchiveFile(tempfile.TemporaryFile(prefix='core-verified-', dir=workspace)) as copied:
            digest, size = tools.stream_digest(source, copied, MAX_ARCHIVE)
            _need(digest == expected_sha and size == initial.st_size, 'Archive checksum mismatch')
            signature = a._file_identity
            _need(signature(initial) == signature(os.fstat(source.fileno())) == signature(os.stat(path, follow_symlinks=False)))
            copied.finish_writes(); copied.seek(0)
            yield copied


def _outer_extract(archive, destination, expected_data_path):
    entries = archive.infolist(); names = [entry.filename for entry in entries]
    _need(0 < len(entries) <= MAX_MEMBERS and len(set(names)) == len(names)
          and tools.MANIFEST in names and sum(entry.file_size for entry in entries) <= MAX_EXPANDED)
    for entry in entries:
        _need(tools.safe_name(entry.filename) and not entry.is_dir() and not entry.flag_bits & 1
              and stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG))
        _need(entry.filename == tools.MANIFEST or entry.filename == 'data/snapshot.zip'
              or entry.filename.startswith('core/') and len(entry.filename.split('/')) == 2)
    _need(archive.getinfo(tools.MANIFEST).file_size <= 4_000_000)
    meta = json.loads(archive.read(tools.MANIFEST), object_pairs_hook=a._pairs,
                      parse_constant=a._number, parse_float=a._number)
    _need(type(meta) is dict and set(meta) == {'schema','kind','data_path','platform','data_kind','files'})
    _need(type(meta['schema']) is int and meta['schema'] == 1 and meta['kind'] == KIND
          and meta['data_path'] == str(expected_data_path) and meta['platform'] == tools.PLATFORM
          and meta['data_kind'] in ('backup','backup-dns01-v1') and type(meta['files']) is dict)
    _need(set(names) == set(meta['files']) | {tools.MANIFEST}
          and 'data/snapshot.zip' in meta['files'] and 'core/SNAPSHOT.json' in meta['files'])
    for name, info in meta['files'].items():
        _need(type(info) is dict and set(info) == {'size','sha256','mode'}
              and type(info['size']) is int and info['size'] > 0 and info['size'] == archive.getinfo(name).file_size
              and type(info['mode']) is int and info['mode'] == 0o600 and a._hex(info['sha256']))
    _need(meta['files']['data/snapshot.zip']['size'] <= tools.MAX_ARCHIVE)
    core_entries = [value for key,value in meta['files'].items() if key.startswith('core/')]
    _need(sum(item['size'] for item in core_entries) <= 4*a.MAX_PACK + MAX_CORE_METADATA)
    destination.mkdir(mode=0o700)
    for name, info in meta['files'].items():
        target=destination/name;target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with archive.open(name) as source, target.open('xb') as output:
            writer=tools._PrivateArchiveFile(output)
            digest,size=tools.stream_digest(source,writer,info['size'])
            _need(size == info['size'] and digest == info['sha256'])
            writer.finish_writes();output.flush();os.fsync(output.fileno())
        target.chmod(0o600)
    return meta


@dataclass(frozen=True)
class ComponentsBackupInput:
    data_path: Path
    core_closure: object
    archive_sha256: str
    data_kind: str
    requires_legacy_migration: tuple


@contextmanager
def preflight(archive_path, expected_sha, workspace, *, core_backup, expected_data_path):
    """Check untrusted input; never swap live data or prepare the Root store."""
    _need(type(core_backup) is CoreBackup)
    try:
        with tempfile.TemporaryDirectory(prefix='.restore-components-', dir=workspace) as temporary:
            work=Path(temporary);work.chmod(0o700);container=work/'container'
            with _snapshot(Path(archive_path),expected_sha,work) as snapshot, zipfile.ZipFile(snapshot) as archive:
                meta=_outer_extract(archive,container,expected_data_path)
            closure=core_backup.inspect(container/'core')
            inner=container/'data/snapshot.zip';data=work/'data'
            inner_meta=tools.unpack_verified(inner,tools.file_digest(inner),data)
            _need(inner_meta.get('kind') == meta['data_kind']
                  and inner_meta.get('data_path') == str(expected_data_path) and inner_meta.get('platform') == tools.PLATFORM)
            (data/tools.MANIFEST).unlink()
            from app.certificates.dns_backup import prepare_dns_staging
            prepare_dns_staging(data)
            migration=_validate_data(data,meta['data_kind'],closure,anchor=work)
            yield ComponentsBackupInput(data,closure,expected_sha,meta['data_kind'],migration)
    except tools.ReleaseError:
        raise
    except Exception:
        raise tools.ReleaseError('Component backup preflight failed; original data was not changed') from None
