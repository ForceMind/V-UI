"""Read-only confirmed core boot selection, before any runtime is constructed.

The caller holds the data-owner lease and supplies active_verified observations
and verified code version constants. This module creates no root metadata,
state, process, trial permission or fallback. Layout/owner/anchor injection is a
trusted Python fixture seam only; production selection is fixed /etc/v-ui.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat

from . import artifacts as a, selection as s, store as fs
from .application import ApplicationCoreError, bundle_artifacts
from .binary import RuntimeArtifactError
from .resolver import CoreResolver, CoreResolutionError
from .selection_files import CoreSelectionFilesError, SelectionFiles

MAX_STATE = 4096
MAX_CONFIG = 16 * 1024 * 1024


class CoreBootError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(value, code):
    if not value:
        raise CoreBootError(code)


def _ancestor(info, owner):
    _need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, owner)
          and (not info.st_mode & 0o022 or bool(info.st_mode & stat.S_ISVTX)), 'RUNTIME_STORAGE_UNSAFE')


def _exists(parent, name):
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


@contextmanager
def _data_root(data, owner, anchor):
    scope = fs._Scope(owner)
    try:
        _need(type(owner) is int and owner >= 0 and isinstance(data, Path) and isinstance(anchor, Path)
              and data.is_absolute() and anchor.is_absolute() and '..' not in data.parts
              and '..' not in anchor.parts and data != anchor, 'RUNTIME_CONTEXT')
        try:
            parts = data.relative_to(anchor).parts
        except ValueError:
            raise CoreBootError('RUNTIME_CONTEXT') from None
        fd = scope.own(os.open(anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        info = os.fstat(fd)
        if anchor == Path('/'):
            fs._directory(info, 0)
        else:
            fs._directory(info, owner, 0o700)
        anchor_identity = fs._dir_identity(info)
        for index, name in enumerate(parts):
            child = scope.own(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd))
            info = os.fstat(child)
            if index == len(parts) - 1:
                fs._directory(info, owner, 0o700)
            else:
                _ancestor(info, owner)
            scope.directories.append((fd, name, child, fs._dir_identity(info)))
            fd = child
        yield scope, fd
        _need(fs._dir_identity(os.fstat(scope.fds[0])) == anchor_identity
              and fs._dir_identity(os.stat(anchor, follow_symlinks=False)) == anchor_identity,
              'RUNTIME_STORAGE_CHANGED')
        scope.stable()
    finally:
        scope.close()


def _private_bytes(scope, directory, name, limit):
    fd, size = scope.file(directory, name, mode=0o600, limit=limit)
    raw = os.pread(fd, size + 1, 0)
    _need(len(raw) == size, 'RUNTIME_STORAGE_CHANGED')
    scope.stable()
    return raw


def _config(scope, directory, revision):
    fd, size = scope.file(directory, revision + '.json', mode=0o600, limit=MAX_CONFIG)
    digest, offset = hashlib.sha256(), 0
    while offset < size:
        block = os.pread(fd, min(65536, size - offset), offset)
        _need(bool(block), 'RUNTIME_STORAGE_CHANGED')
        digest.update(block)
        offset += len(block)
    _need(digest.hexdigest() == revision, 'RUNTIME_STATE_DAMAGED')
    scope.stable()


def _runtime_states(data, *, owner, anchor):
    missing, result = [], {}
    with _data_root(data, owner, anchor) as (scope, directory):
        if not _exists(directory, 'runtime'):
            missing.append((directory, 'runtime'))
        else:
            runtimes = scope.directory(directory, 'runtime', mode=0o700)
            for core in s.CORES:
                if not _exists(runtimes, core):
                    missing.append((runtimes, core))
                    continue
                runtime = scope.directory(runtimes, core, mode=0o700)
                if not _exists(runtime, 'state.json'):
                    missing.append((runtime, 'state.json'))
                    continue
                value = a._json(_private_bytes(scope, runtime, 'state.json', MAX_STATE))
                legacy = set(value) == {'revision', 'enabled'}
                modern = (set(value) == {'schema', 'revision', 'enabled', 'artifact_id'}
                          and type(value['schema']) is int and value['schema'] == 2
                          and a._hex(value['artifact_id']))
                _need((legacy or modern) and a._hex(value.get('revision'))
                      and type(value.get('enabled')) is bool, 'RUNTIME_STATE_DAMAGED')
                _config(scope, runtime, value['revision'])
                result[core] = dict(value, _schema=1 if legacy else 2)
        _need(not any(_exists(parent, name) for parent, name in missing), 'RUNTIME_STORAGE_CHANGED')
    return result


def resolve_boot(*, payload, manifest, ready, data, versions,
                   selection_files=None, catalog=None, store=None,
                   owner=None, data_anchor=Path('/'), bundle_anchor=Path('/')):
    """Verify the whole confirmed snapshot without changing the runtime.

    None means verified absence plus only missing/valid schema1 runtime states;
    the manager is untouched, retaining exact legacy construction. A schema2
    state without a root choice, or any malformed/partial metadata, never means
    initial bundle. A committed legacy state under a new selection requires the
    separate explicit migration transaction; it is not adopted here.
    """
    _need(type(manifest) is dict and a._hex(manifest.get('source_commit'), 40)
          and type(ready) is dict and type(ready.get('runtime_key')) is str
          and ready['runtime_key'] in a.TARGETS, 'APPLICATION_CONTEXT')
    files = SelectionFiles() if selection_files is None else selection_files
    _need(type(files) is SelectionFiles, 'SELECTION_CONTEXT')
    owner = os.geteuid() if owner is None else owner
    context = dict(app_source=manifest['source_commit'], target=ready['runtime_key'])
    try:
        state = files.read_boot(**context)
        committed = _runtime_states(data, owner=owner, anchor=data_anchor)
        if state is None:
            _need(all(value['_schema'] == 1 for value in committed.values()), 'CORE_SELECTION_REQUIRED')
            # Never turn a new barrier appearing during the scan into legacy.
            _need(files.read_boot(**context) is None, 'CORE_SELECTION_CHANGED')
            return None
        for core, value in committed.items():
            _need(value['_schema'] == 2 and value['artifact_id'] == s.reference_identity(state.slot(core).current),
                  'RUNTIME_SELECTION_MISMATCH')
        bundles = bundle_artifacts(payload, manifest, ready, versions=versions,
                                   owner=owner, anchor=bundle_anchor)
        resolver = CoreResolver(app_source=context['app_source'], target=context['target'],
                                bundles=bundles, catalog=catalog, store=store)
        snapshot = {core: resolver.current(state, core) for core in s.CORES}
        _need(files.read_boot(**context) == state, 'CORE_SELECTION_CHANGED')
        return snapshot
    except CoreBootError:
        raise
    except CoreSelectionFilesError:
        raise CoreBootError('CORE_SELECTION_UNAVAILABLE') from None
    except (CoreResolutionError, ApplicationCoreError, RuntimeArtifactError, s.CoreSelectionError):
        raise CoreBootError('CORE_MATERIAL_UNAVAILABLE') from None
    except (fs.CoreStoreError, a.CoreArtifactError, OSError):
        raise CoreBootError('RUNTIME_STATE_UNAVAILABLE') from None


def configure_boot(manager, **context):
    snapshot = resolve_boot(**context)
    if snapshot is not None:
        try:
            manager.configure_artifacts(snapshot)
        except RuntimeError:
            raise CoreBootError('CORE_MANAGER_CONFIGURATION_REFUSED') from None
    return snapshot


def configure_managed(manager, *, session, payload, manifest, ready, data, versions):
    """Only an authenticated production startup scope may select a trial map.

    The supplied state2/config must already match Root's intent. This never
    migrates or rewrites state, changes stop intent, or accepts a test locator.
    """
    from app.data_owner import data_owner
    from . import managed_startup, managed_boot
    _need(type(session) is managed_startup.ManagedStartup
          and getattr(managed_startup._LOCAL, 'session', None) is session, 'MANAGED_SCOPE_REQUIRED')
    managed_boot.require_native_connection(session.connection)
    session._check_initial()
    _need(data == session.root / 'data' and payload == session.release / 'payload', 'MANAGED_CONTEXT_CHANGED')
    state, context = session.connection.selection, session.connection.context
    _need(state.app_source == manifest.get('source_commit') and state.target == ready.get('runtime_key'),
          'MANAGED_CONTEXT_CHANGED')
    with data_owner(data):
        committed = _runtime_states(data, owner=os.geteuid(), anchor=Path('/'))
        for core in s.CORES:
            actual, intent = committed.get(core), context['intents'][core]
            if actual is None:
                _need(intent['revision'] is None and not intent['enabled'], 'MANAGED_STATE_REQUIRED')
            else:
                _need(actual['_schema'] == 2 and actual['artifact_id'] == intent['artifact_id']
                      and actual['revision'] == intent['revision'] and actual['enabled'] == intent['enabled'],
                      'MANAGED_STATE_CHANGED')
        bundles = bundle_artifacts(payload, manifest, ready, versions=versions, owner=os.geteuid())
        resolver = CoreResolver(app_source=state.app_source, target=state.target, bundles=bundles)
        snapshot = {core: resolver.current(state, core) for core in s.CORES}
        session._check_initial()
        manager.configure_artifacts(snapshot)
        return snapshot
