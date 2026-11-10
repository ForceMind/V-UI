"""Private core-material backup closure staging/inspection, never data restore.

The caller supplies an independently confirmed M5 stable or before/quiesced
snapshot and verified active application facts. Objects/flags do not prove that
origin. Backup declarations never seed catalog trust or grant execution. No core,
network, root-store write, data/config/state mutation or service action occurs.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import os
import hashlib
from pathlib import Path
import re
import stat

from . import artifacts as a, selection as s, store as fs
from .binary import RuntimeArtifactError
from .catalog import CoreCatalog, ArchiveCatalogEntry, CoreCatalogError, _archive_release
from .resolver import CoreResolver, CoreResolutionError

MAX_COMPONENTS = 4
MAX_ZIP_BYTES = 4 * a.MAX_PACK


class CoreBackupError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(value, code):
    if not value:
        raise CoreBackupError(code)


def _keys(value, fields):
    _need(type(value) is dict and set(value) == set(fields), 'BACKUP_FIELDS')


def _raw(value):
    raw = fs._canonical(value)
    _need(0 < len(raw) <= a.MAX_JSON, 'BACKUP_METADATA_LIMIT')
    return raw


def _intents(value, state):
    _keys(value, s.CORES)
    for core in s.CORES:
        row = value[core]
        _keys(row, ('artifact_id', 'revision', 'enabled'))
        _need(row['artifact_id'] == s.reference_identity(state.slot(core).current)
              and type(row['enabled']) is bool
              and (row['revision'] is None or a._hex(row['revision']))
              and (row['revision'] is not None or not row['enabled']), 'BACKUP_INTENT')
    return deepcopy(value)


def _snapshot(value, app_source, target):
    _keys(value, ('schema', 'kind', 'selection', 'intents', 'panel_was_running'))
    _need(type(value['schema']) is int and value['schema'] == 1
          and value['kind'] == 'core-backup-closure-v1'
          and type(value['panel_was_running']) is bool, 'BACKUP_SNAPSHOT')
    state = s.decode_selection(_raw(value['selection']), app_source=app_source, target=target)
    _intents(value['intents'], state)
    return state


def _references(state):
    references, current, bundles = {}, set(), []
    for slot in state.slots:
        current.add(s.reference_identity(slot.current))
        for reference in (slot.current, slot.previous):
            if reference is None:
                continue
            if type(reference) is s.BundleReference:
                if reference not in bundles:
                    bundles.append(reference)
                continue
            _need(type(reference) is s.ComponentReference, 'BACKUP_REFERENCE')
            identity = reference.release_manifest_sha256
            _need(identity not in references or references[identity] == reference, 'BACKUP_REFERENCE_CONFLICT')
            references[identity] = reference
    _need(len(references) <= MAX_COMPONENTS, 'BACKUP_COMPONENT_LIMIT')
    return tuple(references[key] for key in sorted(references)), current, tuple(bundles)


def _names(reference):
    prefix = reference.release_manifest_sha256 + '-'
    return (prefix + a._pack_name(reference.core, reference.version, reference.target),
            prefix + 'CORE-RELEASE.json', prefix + 'SHA256SUMS')


@dataclass(frozen=True)
class ArchivePackFact:
    """Verified archival input for Root prepare_archive, not execution identity."""
    reference: s.ComponentReference
    pack_path: Path
    size: int
    required_as_current: bool


@dataclass(frozen=True)
class CoreClosure:
    selection: s.SelectionState
    intents: dict
    panel_was_running: bool
    components: tuple
    external_bundles: tuple


@contextmanager
def _directory(path, *, owner, anchor):
    scope = fs._Scope(owner)
    try:
        _need(type(owner) is int and owner >= 0 and isinstance(path, Path) and isinstance(anchor, Path)
              and path.is_absolute() and anchor.is_absolute() and '..' not in path.parts
              and '..' not in anchor.parts and path != anchor, 'BACKUP_PATH')
        try:
            parts = path.relative_to(anchor).parts
        except ValueError:
            raise CoreBackupError('BACKUP_PATH') from None
        fd = scope.own(os.open(anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        info = os.fstat(fd)
        fs._directory(info, 0 if anchor == Path('/') else owner, None if anchor == Path('/') else 0o700)
        anchor_identity = fs._dir_identity(info)
        for index, name in enumerate(parts):
            child = scope.own(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd))
            info = os.fstat(child)
            if index == len(parts) - 1:
                fs._directory(info, owner, 0o700)
            else:
                _need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, owner)
                      and (not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX), 'BACKUP_DIRECTORY')
            scope.directories.append((fd, name, child, fs._dir_identity(info)))
            fd = child
        yield scope, fd
        _need(fs._dir_identity(os.fstat(scope.fds[0])) == anchor_identity
              and fs._dir_identity(os.stat(anchor, follow_symlinks=False)) == anchor_identity, 'BACKUP_CHANGED')
        scope.stable()
    finally:
        scope.close()


def _read(scope, directory, name, *, limit=a.MAX_JSON):
    fd, size = scope.file(directory, name, mode=0o600, limit=limit)
    raw = os.pread(fd, size + 1, 0)
    _need(len(raw) == size, 'BACKUP_CHANGED')
    scope.stable()
    return raw


def _small(stage, name, raw):
    _need(type(raw) is bytes and 0 < len(raw) <= a.MAX_JSON, 'BACKUP_METADATA_LIMIT')
    stage.write(stage.fd, name, (raw,), mode=0o600, size=len(raw), checksum=hashlib.sha256(raw).hexdigest())


@contextmanager
def _errors():
    try:
        yield
    except CoreBackupError:
        raise
    except (fs.CoreStoreError, a.CoreArtifactError, s.CoreSelectionError, CoreCatalogError,
            CoreResolutionError, RuntimeArtifactError) as error:
        raise CoreBackupError(getattr(error, 'code', 'BACKUP_MATERIAL_UNAVAILABLE')) from None
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, EOFError):
        raise CoreBackupError('BACKUP_IO_OR_INPUT') from None


class CoreBackup:
    def __init__(self, *, app_source, target, bundles, catalog=None, store=None, owner=None, anchor=Path('/')):
        with _errors():
            self.catalog = CoreCatalog() if catalog is None else catalog
            self.store = fs.CoreArtifactStore() if store is None else store
            _need(type(self.catalog) is CoreCatalog and type(self.store) is fs.CoreArtifactStore, 'BACKUP_CONTEXT')
            self._resolver = CoreResolver(app_source=app_source, target=target, bundles=bundles,
                                          catalog=self.catalog, store=self.store)
            self.app_source, self.target = app_source, target
            self.owner = os.geteuid() if owner is None else owner
            self.anchor = anchor

    def _archive(self, reference):
        entry = self.catalog.read_archive(reference.release_manifest_sha256)
        _need(type(entry) is ArchiveCatalogEntry and entry.authority.release_id == reference.release_id
              and entry.authority.tag == reference.tag and entry.tag_source == reference.builder_source
              and entry.manifest_sha256 == reference.release_manifest_sha256
              and entry.checksums_sha256 == reference.checksums_sha256, 'BACKUP_ARCHIVE_CONTEXT')
        release = _archive_release(entry.authority, entry.tag_source, entry.manifest, entry.checksums)
        _need(release.app_sources == entry.declared_app_sources, 'BACKUP_ARCHIVE_CONTEXT')
        return entry, release

    def _inspect_at(self, scope, directory, path):
        names_before = set(os.listdir(directory))
        raw = _read(scope, directory, 'SNAPSHOT.json')
        value = a._json(raw)
        state = _snapshot(value, self.app_source, self.target)
        references, current, bundles = _references(state)
        expected = {'SNAPSHOT.json'}
        for reference in references:
            expected.update(_names(reference))
        _need(names_before == expected, 'BACKUP_MEMBERS')
        for slot in state.slots:
            if type(slot.current) is s.BundleReference:
                self._resolver.resolve(slot.current)
        facts, total = [], 0
        for reference in references:
            entry, release = self._archive(reference)
            zip_name, manifest_name, table_name = _names(reference)
            _need(_read(scope, directory, manifest_name) == entry.manifest
                  and _read(scope, directory, table_name) == entry.checksums, 'BACKUP_METADATA_CHANGED')
            fd, size = scope.file(directory, zip_name, mode=0o600, limit=a.MAX_PACK)
            total += size
            _need(total <= MAX_ZIP_BYTES, 'BACKUP_TOTAL_LIMIT')
            identity = a.verify_pack(fd, release, target=self.target)
            _need(s.component_reference(identity) == reference, 'BACKUP_REFERENCE')
            selected = s.reference_identity(reference) in current
            if selected:
                live = self.catalog.read(reference.release_manifest_sha256, app_source=self.app_source)
                identity = a.verify_pack(fd, live.verified_release, target=self.target)
                _need(s.component_reference(identity) == reference, 'BACKUP_CURRENT_CONTEXT')
            _need(self.catalog.read_archive(reference.release_manifest_sha256) == entry, 'BACKUP_CATALOG_CHANGED')
            facts.append(ArchivePackFact(reference, path / zip_name, size, selected))
        _need(set(os.listdir(directory)) == expected, 'BACKUP_MEMBERS')
        scope.stable()
        return CoreClosure(state, deepcopy(value['intents']), value['panel_was_running'], tuple(facts), bundles)

    def inspect(self, path):
        """Untrusted closure read; never needs components already in Root store."""
        with _errors(), _directory(path, owner=self.owner, anchor=self.anchor) as (scope, directory):
            return self._inspect_at(scope, directory, path)

    def stage(self, workspace, name, *, selection, intents, panel_was_running):
        """Stage confirmed/before facts from the caller, not a confirmation API."""
        with _errors():
            _need(type(name) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', name), 'BACKUP_NAME')
            value = dict(schema=1, kind='core-backup-closure-v1', selection=a._json(s.encode_selection(selection)),
                         intents=deepcopy(intents), panel_was_running=panel_was_running)
            state = _snapshot(value, self.app_source, self.target)
            references, _, _ = _references(state)
            # Normal resolver eligibility, including immutable/current store
            # checks, is mandatory at stage time for both current cores.
            for core in s.CORES:
                self._resolver.current(state, core)
            with _directory(workspace, owner=self.owner, anchor=self.anchor) as (scope, parent):
                _need(not os.path.lexists(workspace / name), 'BACKUP_EXISTS')
                stage = fs._Stage(scope, parent)
                try:
                    stage.create()
                    os.fsync(stage.fd)
                    os.fsync(parent)
                    _small(stage, 'SNAPSHOT.json', _raw(value))
                    total = 0
                    for reference in references:
                        entry, release = self._archive(reference)
                        expected = next(item for item in release.packs if item.target == self.target)
                        zip_name, manifest_name, table_name = _names(reference)
                        _small(stage, manifest_name, entry.manifest)
                        _small(stage, table_name, entry.checksums)
                        total += expected.size
                        _need(total <= MAX_ZIP_BYTES, 'BACKUP_TOTAL_LIMIT')
                        with self.store.open_archive(reference, target=self.target, catalog=self.catalog) as stream:
                            _need(stream.reference == reference and stream.size == expected.size, 'BACKUP_ARCHIVE_CONTEXT')
                            stage.write(stage.fd, zip_name, stream.chunks(), mode=0o600,
                                        size=expected.size, checksum=reference.pack_sha256)
                        # No success identity/fact is recorded before the stream
                        # exits and all source store/catalog FD checks succeed.
                        _need(self.catalog.read_archive(reference.release_manifest_sha256) == entry, 'BACKUP_CATALOG_CHANGED')
                    result = self._inspect_at(scope, stage.fd, workspace / name)
                    os.fsync(stage.fd)
                    scope.stable()
                    stage.bound()
                    fs._rename_new(parent, stage.name, parent, name)
                    _need(fs._dir_identity(os.stat(name, dir_fd=parent, follow_symlinks=False))[:2] == stage.identity,
                          'BACKUP_CHANGED')
                    os.fsync(parent)
                    return result
                finally:
                    stage.cleanup()
