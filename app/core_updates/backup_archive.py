"""Private complete backup container: original data ZIP plus component closure.

Writer requires the caller's actual stopped guard. Preflight and restore
preparation never modify live data or Root state. Authorization clearing and
DNS suspension affect only the private incoming tree. The exchange helper
handles fixed data slots separately from preparation and selector publication.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import uuid
import zipfile

from app import release_tools as tools
from . import artifacts as a, selection as s
from .backup import CoreBackup, _intents
from .boot import _data_root, _runtime_states
from .selection_files import _new, restore_data_position
from .store import _rename_new

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
    _proof: object = field(default=None, repr=False, compare=False)

    @property
    def data_generation(self):
        return dict(dev=self._proof.generation[0], ino=self._proof.generation[1])

    @property
    def data_sha256(self):
        return self._proof.digest


class _RestoreProof:
    def __init__(self, core_backup, live, generation, digest):
        self.core_backup = core_backup
        self.live = live
        self.generation = generation
        self.digest = digest
        self.pid = os.getpid()
        self.checked = self.prepared = None
        self.used = False
        self.current = None


def _generation(data):
    with tools._maintenance_directory(data) as descriptor:
        info = os.fstat(descriptor)
        return info.st_dev, info.st_ino


def _private_extraction_dirs(data):
    # Recursive mkdir can apply a caller's umask to intermediate directories.
    # Normalize only the newly extracted tree, anchored to its private FD.
    def fail(error):
        raise error

    with tools._maintenance_directory(data) as root:
        for _, _, _, directory in os.fwalk('.', dir_fd=root, follow_symlinks=False, onerror=fail):
            _need(os.fstat(directory).st_uid == os.geteuid())
            os.fchmod(directory, 0o700)


@contextmanager
def preflight(archive_path, expected_sha, workspace, *, core_backup, expected_data_path):
    """Check untrusted input; never swap live data or prepare the Root store."""
    _need(type(core_backup) is CoreBackup)
    workspace, live = Path(workspace), Path(expected_data_path)
    _need(workspace.is_absolute() and live.is_absolute() and '..' not in workspace.parts
          and '..' not in live.parts and workspace != live and live not in workspace.parents)
    try:
        with tools._maintenance_directory(workspace):
            pass
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
            _private_extraction_dirs(data)
            from app.certificates.dns_backup import prepare_dns_staging
            prepare_dns_staging(data)
            migration=_validate_data(data,meta['data_kind'],closure,anchor=work)
            from app.data_owner import data_owner
            with data_owner(data, exclusive=True):
                generation = _generation(data)
                digest = tools.recovery_tree_digest(data)
                _need(_generation(data) == generation)
            proof = _RestoreProof(core_backup, live, generation, digest)
            checked = ComponentsBackupInput(data, closure, expected_sha, meta['data_kind'], migration, proof)
            proof.checked = checked
            try:
                yield checked
            finally:
                proof.checked = proof.prepared = None
    except tools.ReleaseError:
        raise
    except Exception:
        raise tools.ReleaseError('Component backup preflight failed; original data was not changed') from None


@dataclass(frozen=True)
class PreparedComponentsRestore:
    data_path: Path
    core_closure: object
    archive_sha256: str
    data_kind: str
    migrated_cores: tuple
    data_sha256: str
    _generation: tuple = field(repr=False)
    _proof: object = field(repr=False, compare=False)

    @property
    def data_generation(self):
        return dict(dev=self._generation[0], ino=self._generation[1])


_PROXY_NAMES = ('proxy-state', '.proxy-state-initialized', '.proxy-state.lock')


def _has_proxy(data):
    return any(os.path.lexists(data/name) for name in _PROXY_NAMES)


def _check_restore_selection(current, core_backup):
    _need(type(current) is s.SelectionState and current.app_source == core_backup.app_source
          and current.target == core_backup.target, 'Current core selection does not match the restore context')
    return s.encode_selection(current)


def _remove_stage_proxy_lock(data):
    path = data/'.proxy-state.lock'
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    _need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1
          and info.st_size == 0 and stat.S_IMODE(info.st_mode) == 0o600)
    path.unlink()


@contextmanager
def _live_restore_scope(guard, live):
    from app.data_owner import _EXCLUSIVE, data_owner
    _need(isinstance(guard, tools._StoppedGuard) and guard.root/'data' == live,
          'Current restore policy requires the stopped guard')
    guard._check()
    held = getattr(_EXCLUSIVE, 'held', {})
    owner = held.get(str(live))
    _need(owner is not None and owner[0] == os.getpid(), 'Current restore policy requires exclusive data ownership')
    with data_owner(live):
        def verify():
            guard._check()
            _need(held.get(str(live)) == owner, 'Restore data ownership changed')
        verify()
        yield verify
        verify()


def place_restore_candidate(guard, journal, prepared, *, core_backup, current_selection):
    """Place a validated candidate in its durable slot before quiescence is recorded."""
    _need(journal['phase'] == 'prepared' and journal['quiesced'] is None)
    target = journal['restore_target']
    _need(target is not None and target['kind'] == 'core-restore-target-v2')
    plan = target['data']
    _need(a._hex(plan['slot'], 32) and type(prepared) is PreparedComponentsRestore
          and type(prepared._proof) is _RestoreProof)
    _need(target['source_backup_sha256'] == prepared.archive_sha256
          and plan['incoming_generation'] == prepared.data_generation
          and plan['preflight_tree_sha256'] == prepared._proof.digest)
    live = guard.root / 'data'
    recovery = guard.root / 'recovery'
    stage = prepared.data_path
    name = 'core-restore-' + plan['slot'] + '-incoming'
    with _live_restore_scope(guard, live) as verify:
        original = validate_restore_against_current(
            guard, prepared, core_backup=core_backup, current_selection=current_selection)
        _need(original['data_generation'] == journal['switch_context']['data_generation'])
        with tools._maintenance_directory(stage.parent) as source, \
                tools._maintenance_directory(recovery, create=True) as destination:
            verify()
            info = os.stat(stage.name, dir_fd=source, follow_symlinks=False)
            _need(stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == prepared._generation)
            _need(_generation(stage) == prepared._generation
                  and tools.recovery_tree_digest(stage) == prepared.data_sha256
                  and _generation(recovery) == (os.fstat(destination).st_dev, os.fstat(destination).st_ino))
            for key in ('original', 'rejected'):
                try:
                    os.stat('core-restore-' + plan['slot'] + '-' + key,
                            dir_fd=destination, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise tools.ReleaseError('Restore data slot already exists')
            tools.sync_directory(guard.root)
            _rename_new(source, stage.name, destination, name)
            _need(_generation(recovery / name) == prepared._generation)
            os.fsync(destination)
            os.fsync(source)
        return original


def exchange_restore_data(guard, journal, *, rollback=False):
    """Move the journal's fixed slots under the stable exclusive data lease."""
    live = guard.root / 'data'
    recovery = guard.root / 'recovery'
    target = journal['restore_target']
    _need(target is not None and target['kind'] == 'core-restore-target-v2')
    slot = target['data']['slot']
    _need(a._hex(slot, 32) and type(rollback) is bool)
    phase = journal['phase']
    _need(phase not in ('confirmed', 'cleanup') if rollback else phase == 'backed_up')
    if rollback and journal['quiesced'] is None:
        try:
            recovery.lstat()
        except FileNotFoundError:
            with _live_restore_scope(guard, live):
                dev, ino = _generation(live)
                observed = dict(live=dict(dev=dev, ino=ino), original=None, incoming=None, rejected=None)
                _need(restore_data_position(journal, observed) == 'unplaced')
                return observed
    names = {key: 'core-restore-' + slot + '-' + key for key in ('original', 'incoming', 'rejected')}
    names['live'] = 'data'

    with _live_restore_scope(guard, live) as verify, \
            tools._maintenance_directory(guard.root) as root_fd, \
            tools._maintenance_directory(recovery) as recovery_fd:
        parents = {key: recovery_fd for key in names}
        parents['live'] = root_fd

        def inspect():
            verify()
            for path, fd in ((guard.root, root_fd), (recovery, recovery_fd)):
                info = path.lstat()
                opened = os.fstat(fd)
                _need(stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) ==
                      (opened.st_dev, opened.st_ino), 'Restore directory changed')
            observed = {}
            for key, name in names.items():
                try:
                    info = os.stat(name, dir_fd=parents[key], follow_symlinks=False)
                except FileNotFoundError:
                    observed[key] = None
                    continue
                _need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                      and stat.S_IMODE(info.st_mode) == 0o700, 'Unsafe restore data slot')
                observed[key] = dict(dev=info.st_dev, ino=info.st_ino)
            return restore_data_position(journal, observed), observed

        position, observed = inspect()
        quiesced = journal['quiesced']
        if quiesced is None:
            _need(rollback and position in ('unplaced', 'staged'))
            return observed
        if not rollback:
            _need(position in ('staged', 'parked', 'trial'))
            original = live if position == 'staged' else recovery / names['original']
            _need(tools.recovery_tree_digest(original) == quiesced['original_tree_sha256'],
                  'Original restore data changed before exchange')
        protected = ('live' if position in ('staged', 'restored') else 'original') if rollback else (
            'live' if position == 'trial' else 'incoming')
        digest = quiesced['original_tree_sha256' if rollback else 'prepared_tree_sha256']
        path = live if protected == 'live' else recovery / names[protected]
        _need(tools.recovery_tree_digest(path) == digest and inspect() == (position, observed),
              'Restore data changed before exchange')
        transitions = ({'trial': ('live', 'rejected', 'rollback_gap'),
                        'rollback_gap': ('original', 'live', 'restored'),
                        'parked': ('original', 'live', 'staged')} if rollback else
                       {'staged': ('live', 'original', 'parked'),
                        'parked': ('incoming', 'live', 'trial')})
        # On retry the preceding rename may have lost its acknowledgement.
        # Persist the observed layout before issuing a second rename.
        os.fsync(root_fd)
        os.fsync(recovery_fd)
        while position in transitions:
            source, destination, expected = transitions[position]
            _rename_new(parents[source], names[source], parents[destination], names[destination])
            position, observed = inspect()
            _need(position == expected, 'Restore data changed during exchange')
            os.fsync(root_fd)
            os.fsync(recovery_fd)
        _need(position in ('staged', 'restored') if rollback else position == 'trial')
        return observed


def _migrate_stage_runtime(data, closure, migration):
    if not migration:
        return
    with _data_root(data, os.geteuid(), data.parent) as (scope, directory):
        runtime = scope.directory(directory, 'runtime', mode=0o700)
        for core in migration:
            target = scope.directory(runtime, core, mode=0o700)
            old_fd, size = scope.file(target, 'state.json', mode=0o600, limit=4096)
            expected = closure.intents[core]
            _need(a._json(os.pread(old_fd, size+1, 0)) ==
                  dict(revision=expected['revision'], enabled=expected['enabled']))
            name = '.restore-state-' + uuid.uuid4().hex
            raw = json.dumps(dict(schema=2, **expected), sort_keys=True, indent=2).encode()
            _new(scope, target, name, raw)
            scope.stable()
            os.replace(name, 'state.json', src_dir_fd=target, dst_dir_fd=target)
            scope.files[:] = [row for row in scope.files if row[:2] not in
                              ((target, name), (target, 'state.json'))]
            fd, size = scope.file(target, 'state.json', mode=0o600, limit=4096)
            _need(os.pread(fd, size+1, 0) == raw)
            os.fsync(target)


@contextmanager
def prepare_restore(guard, checked, *, core_backup, current_selection):
    """Modify only the private data after the existing live stopped EX is held."""
    from app.data_owner import data_owner
    _need(type(checked) is ComponentsBackupInput and type(checked._proof) is _RestoreProof)
    proof = checked._proof
    _need(proof.checked is checked and proof.pid == os.getpid() and not proof.used
          and proof.core_backup is core_backup)
    data = checked.data_path
    with _live_restore_scope(guard, proof.live) as verify_live, data_owner(data, exclusive=True):
        proof.current = _check_restore_selection(current_selection, core_backup)
        proof.used = True
        try:
            _need(_generation(data) == proof.generation and tools.recovery_tree_digest(data) == proof.digest,
                  'Component restore staging changed after preflight')
            closure = checked.core_closure
            _intents(closure.intents, closure.selection)
            migration = _data_bindings(data, closure, anchor=data.parent)
            _need(migration == checked.requires_legacy_migration)
            verify_live()
            _migrate_stage_runtime(data, closure, migration)
            if checked.data_kind == 'backup-dns01-v1':
                tools._suspend_restore_dns(data)
            tools._revoke_restore_access(data, _strict_staging=True)
            _need(not _data_bindings(data, closure, anchor=data.parent))
            if _has_proxy(data):
                reader = tools._readonly_proxy_store_type()
                tools._validate_restore_policy(data, _artifact_ids={
                    core:closure.intents[core]['artifact_id'] for core in s.CORES}, _existing_store_type=reader)
                _remove_stage_proxy_lock(data)
            digest = tools.recovery_tree_digest(data, _sync_source=True)
            _need(_generation(data) == proof.generation)
            verify_live()
            prepared = PreparedComponentsRestore(data, closure, checked.archive_sha256, checked.data_kind,
                                                  migration, digest, proof.generation, proof)
        except tools.ReleaseError:
            raise
        except Exception:
            raise tools.ReleaseError('Component restore preparation failed; original data was not changed') from None
        proof.prepared = prepared
        try:
            yield prepared
        finally:
            proof.prepared = None


def validate_restore_against_current(guard, prepared, *, core_backup, current_selection):
    """Check the old domain under its existing real EX; return old tree facts.

    A bounded private copy allows SQLite and proxy readers to create their
    temporary sidecars/locks without touching the live data tree. This copies
    at most the existing recovery tree budget once, alongside incoming staging.
    """
    from app.data_owner import data_owner
    _need(type(prepared) is PreparedComponentsRestore and type(prepared._proof) is _RestoreProof)
    proof = prepared._proof
    live, stage = guard.root/'data', prepared.data_path
    _need(proof.prepared is prepared and proof.checked is not None and proof.pid == os.getpid()
          and proof.live == live and proof.core_backup is core_backup
          and proof.current == _check_restore_selection(current_selection, core_backup))
    try:
        with _live_restore_scope(guard, live) as verify_live, data_owner(stage):
            _need(_generation(stage) == prepared._generation
                  and tools.recovery_tree_digest(stage) == prepared.data_sha256,
                  'Prepared component restore data changed')
            _intents(prepared.core_closure.intents, prepared.core_closure.selection)
            _need(not _data_bindings(stage, prepared.core_closure, anchor=stage.parent))
            generation = _generation(live)
            _need(generation[0] == prepared._generation[0], 'Prepared data is on a different filesystem')
            with tempfile.TemporaryDirectory(prefix='.restore-policy-', dir=stage.parent) as temporary:
                workspace = Path(temporary)
                old = workspace/'data'
                has_proxy = _has_proxy(live)
                checksum = tools.recovery_tree_digest(live, copy_to=old)
                # The copy omits transient locks. A live lock with no store
                # remains unknown and cannot silently become a clean OFF.
                _need(has_proxy == _has_proxy(old), 'Current proxy policy is unknown')
                old_ids = {core:s.reference_identity(current_selection.slot(core).current) for core in s.CORES}
                for core, observed in _runtime_states(old, owner=os.geteuid(), anchor=workspace).items():
                    _need(observed['_schema'] == 2 and observed['artifact_id'] == old_ids[core]
                          or observed['_schema'] == 1
                          and type(current_selection.slot(core).current) is s.BundleReference,
                          'Current runtime artifact binding differs from restore selection')
                new_ids = {core:prepared.core_closure.intents[core]['artifact_id'] for core in s.CORES}
                reader = tools._readonly_proxy_store_type()
                with data_owner(old, exclusive=True):
                    if has_proxy:
                        tools._validate_restore_policy(old, _artifact_ids=old_ids, _existing_store_type=reader)
                    else:
                        tools._database_mode_fence(old, _existing_store_type=reader)
                    try:
                        incoming = reader(stage).read_snapshot(initialize=False) if _has_proxy(stage) else None
                        incoming_schema = reader(stage).policy_schema_version() if incoming is not None else 1
                        current_schema = tools._restore_policy_floor(old, stage, incoming,
                            _current_artifact_ids=old_ids, _incoming_artifact_ids=new_ids, _existing_store_type=reader)
                        _need(incoming_schema >= current_schema,
                              'Backup would downgrade the current proxy capability fence')
                    finally:
                        _remove_stage_proxy_lock(stage)
            verify_live()
            _need(_generation(live) == generation
                  and _generation(stage) == prepared._generation, 'Restore data ownership changed')
        return dict(data_generation=dict(dev=generation[0], ino=generation[1]), data_sha256=checksum)
    except tools.ReleaseError:
        raise
    except Exception:
        raise tools.ReleaseError('Current restore policy could not be verified; original data was not changed') from None
