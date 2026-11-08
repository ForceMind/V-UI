"""Immutable local preparation/readback of M0-verified core packs, stdlib only.

No active selection, installer, native/configuration probe, network or garbage
collection. Layout injection is a trusted Python test seam, never a CLI/env or
user-data option. Production writes require UID0; readers can be non-root.
Caller authority/tag-ref/app-source remain independent inputs on every call:
TRUST.json records them but cannot authorize its own bytes or a restored backup.
"""
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import zipfile
import zlib

from . import artifacts as a


@dataclass(frozen=True)
class Layout:
    root: Path = Path('/var/lib/v-ui-core')
    owner: int = 0
    # Production walks from /. Tests explicitly trust their own 0700 anchor.
    anchor: Path = Path('/')


PRODUCTION_LAYOUT = Layout()


class CoreStoreError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(condition, code):
    if not condition:
        raise CoreStoreError(code)


def _directory(info, owner, mode=None):
    _need(stat.S_ISDIR(info.st_mode) and info.st_uid == owner and not info.st_mode & 0o022,
          'DIRECTORY_UNTRUSTED')
    if mode is not None:
        _need(stat.S_IMODE(info.st_mode) == mode, 'DIRECTORY_MODE')


def _regular(info, owner, mode, limit):
    _need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == owner
          and stat.S_IMODE(info.st_mode) == mode and 0 <= info.st_size <= limit, 'FILE_UNTRUSTED')


def _dir_identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def _authority(value):
    _need(type(value) is a.Authority, 'AUTHORITY_REQUIRED')
    raw = dict(schema=1, repository=a.REPOSITORY, repository_id=a.REPOSITORY_ID,
        release_id=value.release_id, tag=value.tag, published_at=value.published_at,
        draft=False, prerelease=False, assets=[dict(id=item.id, name=item.name,
            size=item.size, digest='sha256:'+item.sha256) for item in sorted(value.assets, key=lambda item: item.name)])
    _need(a.decode_authority(_canonical(raw)) == value, 'AUTHORITY_REQUIRED')
    return raw


def _cache(authority, source):
    return dict(schema=1, kind='core-trust-cache-v1', authority=_authority(authority), tag_source=source)


def _cache_matches(raw, authority, source):
    value = a._json(raw)
    _need(set(value) == {'schema', 'kind', 'authority', 'tag_source'} and type(value['schema']) is int
          and value['schema'] == 1 and value['kind'] == 'core-trust-cache-v1'
          and value['tag_source'] == source
          and a.decode_authority(_canonical(value['authority'])) == authority, 'TRUST_MISMATCH')


class _Scope:
    """FD ownership and stable bindings for this one store operation only."""
    def __init__(self, owner):
        self.owner = owner
        self.fds = []
        self.directories = []
        self.files = []

    def own(self, fd):
        self.fds.append(fd)
        return fd

    def directory(self, parent, name, *, mode=0o755, create=False, owner=None):
        created = False
        if create:
            try:
                os.mkdir(name, mode, dir_fd=parent); created = True
            except FileExistsError:
                pass
        fd = self.own(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent))
        if created:
            os.fchmod(fd, mode); os.fsync(fd); os.fsync(parent)
        uid = self.owner if owner is None else owner
        info = os.fstat(fd); _directory(info, uid, mode)
        self.directories.append((parent, name, fd, _dir_identity(info)))
        return fd

    def file(self, parent, name, *, mode=0o644, limit=a.MAX_JSON):
        fd = self.own(os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent))
        info = os.fstat(fd); _regular(info, self.owner, mode, limit)
        self.files.append((parent, name, fd, a._file_identity(info)))
        return fd, info.st_size

    def bytes(self, parent, name, *, limit=a.MAX_JSON):
        fd, size = self.file(parent, name, limit=limit)
        value = os.pread(fd, size+1, 0)
        _need(len(value) == size, 'FILE_CHANGED')
        return value

    def stable(self):
        for parent, name, fd, expected in self.directories:
            opened = os.fstat(fd)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            _need(_dir_identity(opened) == expected and _dir_identity(current) == expected,
                  'DIRECTORY_CHANGED')
        for parent, name, fd, expected in self.files:
            _need(a._file_identity(os.fstat(fd)) == expected
                  and a._file_identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == expected,
                  'FILE_CHANGED')

    def close(self):
        failed = False
        for fd in reversed(self.fds):
            try: os.close(fd)
            except OSError: failed = True
        self.fds.clear()
        if failed:
            raise CoreStoreError('CLOSE_FAILED') from None


@contextmanager
def _root(layout, *, create=False):
    scope = _Scope(layout.owner)
    try:
        root, anchor = layout.root, layout.anchor
        _need(type(layout.owner) is int and layout.owner >= 0 and isinstance(root, Path) and isinstance(anchor, Path)
              and root.is_absolute() and anchor.is_absolute() and '..' not in root.parts
              and '..' not in anchor.parts and root != anchor, 'LAYOUT')
        try: relative = root.relative_to(anchor)
        except ValueError: raise CoreStoreError('LAYOUT') from None
        fd = scope.own(os.open(anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        anchor_info = os.fstat(fd)
        _directory(anchor_info, 0 if anchor == Path('/') else layout.owner,
                   None if anchor == Path('/') else 0o700)
        anchor_identity = _dir_identity(anchor_info)
        for index, name in enumerate(relative.parts):
            final = index == len(relative.parts)-1
            # Existing system ancestors must be root-owned and non-writable.
            if final:
                fd = scope.directory(fd, name, create=create)
            else:
                child = scope.own(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd))
                info = os.fstat(child)
                _directory(info, 0 if anchor == Path('/') else layout.owner)
                scope.directories.append((fd, name, child, _dir_identity(info))); fd = child
        artifacts = scope.directory(fd, 'artifacts', create=create)
        yield scope, fd, artifacts
        _need(_dir_identity(os.fstat(scope.fds[0])) == anchor_identity
              and _dir_identity(os.stat(anchor, follow_symlinks=False)) == anchor_identity, 'ANCHOR_CHANGED')
        scope.stable()
    finally:
        scope.close()


def _lock(scope, root):
    created = False
    try:
        fd = scope.own(os.open('prepare.lock', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root))
        created = True
    except FileExistsError:
        fd = scope.own(os.open('prepare.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root))
    if created:
        os.fchmod(fd, 0o600); os.fsync(fd); os.fsync(root)
    info = os.fstat(fd); _regular(info, scope.owner, 0o600, 0)
    scope.files.append((root, 'prepare.lock', fd, a._file_identity(info)))
    try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: raise CoreStoreError('STORE_BUSY') from None


def _rename_new(parent, name, destination, identity):
    """Native atomic no-replace rename only; never an overwrite-style fallback."""
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith('linux'):
        function = getattr(library, 'renameat2', None); flag = 1  # RENAME_NOREPLACE
    elif sys.platform == 'darwin':
        function = getattr(library, 'renameatx_np', None); flag = 4  # RENAME_EXCL
    else:
        function = None; flag = 0
    _need(function is not None, 'UNSUPPORTED')
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(parent, os.fsencode(name), destination, os.fsencode(identity), flag) != 0:
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise CoreStoreError('DESTINATION_EXISTS')
        if code in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
            raise CoreStoreError('UNSUPPORTED')
        raise CoreStoreError('PUBLISH_FAILED')


class _Stage:
    def __init__(self, scope, parent):
        self.scope, self.parent = scope, parent
        self.name = 'prepare-'+secrets.token_hex(16)
        self.created = False
        self.fd = self.identity = None
        self.files = []
        self.bin = None
        self.bin_created = False

    def create(self):
        parent = self.parent
        os.mkdir(self.name, 0o700, dir_fd=parent)
        self.created = True
        self.identity = _dir_identity(os.stat(self.name, dir_fd=parent, follow_symlinks=False))[:2]
        self.fd = self.scope.own(os.open(self.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent))
        os.fchmod(self.fd, 0o700)
        _need(_dir_identity(os.fstat(self.fd))[:2] == self.identity, 'STAGING_CHANGED')

    def bound(self):
        info = os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)
        _need(stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == self.identity, 'STAGING_CHANGED')

    def new_file(self, parent, name):
        fd = self.scope.own(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent))
        self.files.append((parent, name, fd))
        return fd

    def write(self, parent, name, chunks, *, mode, size, checksum):
        fd = self.new_file(parent, name); digest = hashlib.sha256(); count = 0
        for raw in chunks:
            count += len(raw); _need(count <= size, 'COPY_SIZE')
            view = memoryview(raw)
            while view:
                written = os.write(fd, view); _need(written > 0, 'COPY_FAILED'); view = view[written:]
            digest.update(raw)
        _need(count == size and digest.hexdigest() == checksum, 'COPY_DIGEST')
        os.fchmod(fd, mode); os.fsync(fd)
        _regular(os.fstat(fd), self.scope.owner, mode, size)

    def small(self, name, raw):
        _need(type(raw) is bytes and len(raw) <= a.MAX_JSON, 'METADATA_LIMIT')
        self.write(self.fd, name, (raw,), mode=0o644, size=len(raw), checksum=hashlib.sha256(raw).hexdigest())

    def cleanup(self):
        # Only the exclusive .staging namespace. If publication moved this name,
        # its FD now refers to public content: NEVER unlink through it.
        if not self.created:
            return
        try: self.bound()
        except FileNotFoundError: return
        failed = False
        for parent, name, fd in reversed(self.files):
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                owned = os.fstat(fd)
                _need((info.st_dev, info.st_ino) == (owned.st_dev, owned.st_ino), 'STAGING_CHANGED')
                os.unlink(name, dir_fd=parent)
            except (OSError, CoreStoreError): failed = True
        if self.bin_created:
            try: os.rmdir('bin', dir_fd=self.fd)
            except OSError: failed = True
        try: os.rmdir(self.name, dir_fd=self.parent)
        except OSError: failed = True
        if failed:
            raise CoreStoreError('CLEANUP_FAILED')


def _chunks(fd, size):
    position = 0
    while position < size:
        raw = os.pread(fd, min(a.CHUNK, size-position), position)
        _need(bool(raw), 'COPY_SIZE')
        position += len(raw); yield raw


def _extract(stage, fd, size, identity):
    reader = a._Reader(fd, size); a._zip_metadata(reader)
    with zipfile.ZipFile(reader) as archive:
        names = ('LICENSE', 'SOURCE.tar.gz', 'PROVENANCE.json', 'CORE-PACK.json', 'bin/'+identity.core)
        os.mkdir('bin', 0o755, dir_fd=stage.fd)
        stage.bin_created = True
        stage.bin = stage.scope.own(os.open('bin', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=stage.fd))
        os.fchmod(stage.bin, 0o755)
        record = a._json(a._read_member(archive, 'CORE-PACK.json', limit=a.MAX_JSON, retain=True))
        for name in names:
            entry = archive.getinfo(name)
            checksum = identity.pack_manifest_sha256 if name == 'CORE-PACK.json' else record['files'][name]['sha256']
            with archive.open(entry) as stream:
                def chunks():
                    while True:
                        raw = stream.read(a.CHUNK)
                        if not raw: break
                        yield raw
                stage.write(stage.bin if name.startswith('bin/') else stage.fd,
                    identity.core if name.startswith('bin/') else name, chunks(),
                    mode=0o555 if name.startswith('bin/') else 0o644, size=entry.file_size, checksum=checksum)
        os.fsync(stage.bin)


@dataclass(frozen=True)
class StoredArtifact:
    identity: a.PackIdentity
    binary_path: Path
    reused: bool


class CoreArtifactStore:
    def __init__(self, layout=PRODUCTION_LAYOUT):
        _need(type(layout) is Layout, 'LAYOUT')
        self.layout = layout

    def _read(self, scope, directory, artifact_id, manifest, checksums, release, target, *, reused):
        expected = next(item for item in release.packs if item.target == target)
        core, _, _ = a._tag(release.authority.tag)
        names = {'bin', expected.name, 'CORE-RELEASE.json', 'SHA256SUMS', 'TRUST.json',
                 'CORE-PACK.json', 'LICENSE', 'SOURCE.tar.gz', 'PROVENANCE.json'}
        _directory(os.fstat(directory), self.layout.owner, 0o755)
        _need(set(os.listdir(directory)) == names, 'ARTIFACT_MEMBERS')
        _need(scope.bytes(directory, 'CORE-RELEASE.json') == manifest
              and scope.bytes(directory, 'SHA256SUMS') == checksums, 'METADATA_CHANGED')
        _cache_matches(scope.bytes(directory, 'TRUST.json'), release.authority, release.builder_source)
        zip_fd, size = scope.file(directory, expected.name, limit=a.MAX_PACK)
        identity = a.verify_pack(zip_fd, release, target=target)
        raw = scope.bytes(directory, 'CORE-PACK.json')
        _need(hashlib.sha256(raw).hexdigest() == expected.manifest_sha256, 'EXTRACTED_CHANGED')
        record = a._json(raw)
        binary_dir = scope.directory(directory, 'bin')
        _need(set(os.listdir(binary_dir)) == {core}, 'ARTIFACT_MEMBERS')
        for name, info in record['files'].items():
            parent, leaf = (binary_dir, core) if name == 'bin/'+core else (directory, name)
            fd, length = scope.file(parent, leaf, mode=info['mode'], limit=info['size'])
            _need(length == info['size'] and a._digest_fd(fd, length) == info['sha256'], 'EXTRACTED_CHANGED')
        _need(set(os.listdir(directory)) == names and set(os.listdir(binary_dir)) == {core}, 'ARTIFACT_MEMBERS')
        _need(artifact_id == identity.release_manifest_sha256, 'ARTIFACT_ID')
        scope.stable()
        return StoredArtifact(identity, self.layout.root/'artifacts'/artifact_id/'bin'/core, reused)

    def read(self, artifact_id, *, authority, expected_tag, expected_builder_source, app_source, target):
        """Read current-host artifact with an independent trusted context."""
        _need(a._hex(artifact_id) and type(target) is str and target in a.TARGETS, 'IDENTITY')
        try:
            with _root(self.layout) as (scope, _, artifacts):
                directory = scope.directory(artifacts, artifact_id)
                manifest = scope.bytes(directory, 'CORE-RELEASE.json')
                checksums = scope.bytes(directory, 'SHA256SUMS')
                release = a.verify_release(manifest, checksums, authority=authority, expected_tag=expected_tag,
                    expected_builder_source=expected_builder_source, app_source=app_source)
                return self._read(scope, directory, artifact_id, manifest, checksums, release, target, reused=True)
        except CoreStoreError:
            raise
        except a.CoreArtifactError:
            raise CoreStoreError('ARTIFACT_REJECTED') from None
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, EOFError, zlib.error):
            raise CoreStoreError('STORE_INVALID') from None

    def prepare(self, pack, manifest, checksums, *, authority, expected_tag, expected_builder_source, app_source, target):
        """Validate, stage, publish-no-replace, then verify all stored bytes.

        Public artifacts are never automatically removed, including after a
        post-publication fsync/validation failure. A retry must verify them.
        """
        _need(os.geteuid() == self.layout.owner, 'ROOT_REQUIRED')
        _need(type(target) is str and target in a.TARGETS, 'TARGET')
        stage = None
        try:
            release = a.verify_release(manifest, checksums, authority=authority, expected_tag=expected_tag,
                expected_builder_source=expected_builder_source, app_source=app_source)
            artifact_id = release.manifest_sha256
            with a._opened(pack) as (source, size, source_stable):
                identity = a.verify_pack(source, release, target=target)
                with _root(self.layout, create=True) as (scope, root, artifacts):
                    _lock(scope, root)
                    try:
                        existing = os.stat(artifact_id, dir_fd=artifacts, follow_symlinks=False)
                    except FileNotFoundError:
                        existing = None
                    if existing is not None:
                        directory = scope.directory(artifacts, artifact_id)
                        result = self._read(scope, directory, artifact_id, manifest, checksums, release, target, reused=True)
                        os.fsync(directory); os.fsync(artifacts)
                        source_stable()
                        return result
                    staging = scope.directory(root, '.staging', mode=0o700, create=True)
                    try:
                        stage = _Stage(scope, staging)
                        stage.create()
                        selected = next(item for item in release.packs if item.target == target)
                        stage.write(stage.fd, selected.name, _chunks(source, size), mode=0o644,
                                    size=selected.size, checksum=selected.sha256)
                        copied, copied_size = scope.file(stage.fd, selected.name, limit=a.MAX_PACK)
                        _need(a.verify_pack(copied, release, target=target) == identity, 'COPY_CHANGED')
                        _extract(stage, copied, copied_size, identity)
                        stage.small('CORE-RELEASE.json', manifest); stage.small('SHA256SUMS', checksums)
                        stage.small('TRUST.json', _canonical(_cache(authority, expected_builder_source)))
                        os.fchmod(stage.fd, 0o755); os.fsync(stage.fd)
                        self._read(scope, stage.fd, artifact_id, manifest, checksums, release, target, reused=False)
                        source_stable(); stage.bound(); scope.stable()
                        _rename_new(staging, stage.name, artifacts, artifact_id)
                        os.fsync(artifacts); os.fsync(staging)
                        directory = scope.directory(artifacts, artifact_id)
                        _need(_dir_identity(os.fstat(directory))[:2] == stage.identity, 'PUBLISH_CHANGED')
                        result = self._read(scope, directory, artifact_id, manifest, checksums, release, target, reused=False)
                        source_stable()
                        return result
                    finally:
                        if stage is not None:
                            stage.cleanup()
        except CoreStoreError:
            raise
        except a.CoreArtifactError:
            raise CoreStoreError('ARTIFACT_REJECTED') from None
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, EOFError, zipfile.BadZipFile, zlib.error):
            raise CoreStoreError('STORE_INVALID') from None
