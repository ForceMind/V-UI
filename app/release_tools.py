"""Offline, per-user release control. No root, daemon installation, or network calls."""
from __future__ import annotations
from contextlib import contextmanager, ExitStack, nullcontext
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import sqlite3
import stat
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import tarfile
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener
import zipfile

PLATFORM = 'linux-multi-cpython312'
ARCHES={'x86_64':'x86_64','amd64':'x86_64','aarch64':'aarch64','arm64':'aarch64'}

def target_key() -> str:
    if platform.system()!='Linux': raise ReleaseError('V-UI server packages support Linux only')
    arch=ARCHES.get(platform.machine().lower())
    if not arch: raise ReleaseError('Unsupported CPU architecture: '+platform.machine())
    musl=bool(list(Path('/lib').glob('ld-musl-*.so.1')) or list(Path('/usr/lib').glob('ld-musl-*.so.1')))
    if not musl:
        name,_=platform.libc_ver(); musl=name.lower()=='musl'
    return arch+('-musl' if musl else '-gnu')

def target_arch() -> str:
    return target_key().split('-',1)[0]

def runtime_python(release: Path) -> Path:
    value=release/'runtime'/'python'/'bin'/'python3'
    if value.is_symlink():
        resolved=value.resolve()
        if release.resolve() not in resolved.parents: raise ReleaseError('Runtime Python symlink escapes release')
    if not value.exists(): raise ReleaseError('Portable Python runtime is missing')
    return value

def runtime_tree_digest(root: Path) -> str:
    digestor=hashlib.sha256()
    for path in sorted(root.rglob('*')):
        rel=path.relative_to(root).as_posix().encode()
        digestor.update(rel+b'\0')
        if path.is_symlink():
            digestor.update(b'L'+os.readlink(path).encode()+b'\0')
        elif path.is_file():
            digestor.update(b'F'+str(path.stat().st_mode & 0o777).encode()+b'\0')
            with path.open('rb') as handle:
                # This tree belongs to the prepared release, never user data.
                # Hash every byte without retaining the entire installed Python
                # tree in cache after each integrity scan.
                wrapped = _PrivateArchiveFile(handle)
                if wrapped.advice is not None and wrapped.dontneed is not None:
                    os.fsync(handle.fileno())
                for chunk in iter(lambda:wrapped.read(STREAM_CHUNK),b''):
                    digestor.update(chunk)
        elif path.is_dir(): digestor.update(b'D\0')
        else: raise ReleaseError('Unsupported runtime file type')
    return digestor.hexdigest()

def _normalized_archive_path(value: PurePosixPath) -> tuple[str, ...]:
    if value.is_absolute():
        raise ReleaseError('Unsafe portable Python link')
    stack=[]
    for part in value.parts:
        if part in ('','.'): continue
        if part=='..':
            if not stack: raise ReleaseError('Unsafe portable Python link')
            stack.pop()
        else:
            if '\\' in part or any(ord(ch)<32 or ord(ch)==127 for ch in part):
                raise ReleaseError('Unsafe portable Python link')
            stack.append(part)
    if not stack or stack[0]!='python':
        raise ReleaseError('Portable Python link escapes runtime root')
    return tuple(stack)


@contextmanager
def _private_runtime_tar(payload: Path, key: str, expected_sha: str, payload_identity):
    """Read only the new stage-owned runtime archive through a pinned descriptor.

    Generic extraction and caller-owned archives never opt in. The payload's
    identity is captured before stage renames its freshly unpacked directory.
    Keep the full runtime pin check on the same fd subsequently used by tar.
    """
    if (not isinstance(expected_sha, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_sha)
            or key not in ('x86_64-gnu', 'aarch64-gnu', 'x86_64-musl', 'aarch64-musl')):
        raise ReleaseError('Portable Python runtime pin mismatch')
    path = payload / 'runtimes' / (key + '.tar.gz')
    if any(getattr(os, name, None) is None for name in ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK')):
        # Without safe traversal, never advise a source whose private identity
        # cannot be established. Still hash every byte on the same plain fd
        # used by tar, with the original content and extraction checks intact.
        if not path.is_file():
            raise ReleaseError('Portable Python runtime pin mismatch')
        with path.open('rb') as source:
            if stream_digest(source, limit=MAX_ARCHIVE)[0] != expected_sha:
                raise ReleaseError('Portable Python runtime pin mismatch')
            source.seek(0)
            with tarfile.open(fileobj=source, mode='r:gz') as archive:
                yield archive
        return

    def directory_identity(entry):
        return entry.st_dev, entry.st_ino, entry.st_mode, entry.st_uid, entry.st_gid

    def file_identity(entry):
        return (*directory_identity(entry), entry.st_size, entry.st_nlink,
                entry.st_mtime_ns, entry.st_ctime_ns)

    descriptors = []; bindings = []; raw = None
    try:
        parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        descriptors.append(parent)
        for name in payload.absolute().parts[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            descriptors.append(child)
            entry = os.fstat(child)
            bindings.append((parent, name, child, directory_identity(entry)))
            parent = child
        entry = os.fstat(parent)
        if ((entry.st_dev, entry.st_ino) != payload_identity or entry.st_uid != os.geteuid()
                or not stat.S_ISDIR(entry.st_mode) or stat.S_IMODE(entry.st_mode) != 0o700):
            raise ReleaseError('Runtime archive payload identity changed')
        directory = os.open('runtimes', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        descriptors.append(directory)
        bindings.append((parent, 'runtimes', directory, directory_identity(os.fstat(directory))))
        name = key + '.tar.gz'
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        descriptors.append(fd)
        entry = os.fstat(fd)
        identity = file_identity(entry)
        if (not stat.S_ISREG(entry.st_mode) or entry.st_uid != os.geteuid()
                or stat.S_IMODE(entry.st_mode) != 0o600 or entry.st_nlink != 1
                or not 0 < entry.st_size <= MAX_ARCHIVE):
            raise ReleaseError('Runtime archive must be a new private regular file')
        if file_identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) != identity:
            raise ReleaseError('Runtime archive identity changed before reading')
        raw = os.fdopen(fd, 'rb', closefd=False)
        reader = _PrivateArchiveFile(raw)
        if reader.advice is not None and reader.dontneed is not None:
            os.fsync(fd)
        if stream_digest(reader, limit=MAX_ARCHIVE)[0] != expected_sha:
            raise ReleaseError('Portable Python runtime pin mismatch')
        reader.seek(0)
        with tarfile.open(fileobj=reader, mode='r:gz') as archive:
            yield archive
        for parent, component, child, before in bindings:
            if (directory_identity(os.fstat(child)) != before
                    or directory_identity(os.stat(component, dir_fd=parent, follow_symlinks=False)) != before):
                raise ReleaseError('Runtime archive parent identity changed during extraction')
        if (file_identity(os.fstat(fd)) != identity
                or file_identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) != identity):
            raise ReleaseError('Runtime archive identity changed during extraction')
        # gzip may stop before reading raw EOF. Advise this same completed fd,
        # never reopen the pathname or follow a replacement for final cleanup.
        reader._discard(0, 0)
    finally:
        active_error = sys.exc_info()[0] is not None
        close_error = None
        if raw is not None:
            try: raw.close()
            except OSError as error: close_error = error
        for descriptor in reversed(descriptors):
            try: os.close(descriptor)
            except OSError as error:
                if close_error is None: close_error = error
        if close_error is not None and not active_error:
            raise close_error


def extract_runtime(archive_path: Path, destination: Path, *, _private_archive=None) -> None:
    destination.mkdir(parents=True,mode=0o700)
    try:
        with (_private_archive if _private_archive is not None else tarfile.open(archive_path,'r:gz')) as archive:
            members=archive.getmembers()
            if len(members)>50000 or sum(max(0,m.size) for m in members)>650_000_000:
                raise ReleaseError('Portable Python archive exceeds limits')
            for member in members:
                name=PurePosixPath(member.name)
                parts=name.parts
                if (not parts or parts[0]!='python' or member.name.startswith('/')
                        or any(part in ('','..') for part in parts)):
                    raise ReleaseError('Unsafe portable Python archive path')
                if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                    raise ReleaseError('Portable Python archive contains unsupported entry')
                if member.issym():
                    _normalized_archive_path(name.parent / PurePosixPath(member.linkname))
                elif member.islnk():
                    _normalized_archive_path(PurePosixPath(member.linkname))
            archive.extractall(destination,filter='data')
        # destination was just created by this extraction. Persist and advise
        # only its regular files before ensurepip adds a second working set.
        # Never follow runtime links, inspect old releases or cool user data.
        if (getattr(os, 'posix_fadvise', None) is not None
                and getattr(os, 'POSIX_FADV_DONTNEED', None) is not None
                and all(getattr(os, name, None) is not None for name in ('O_NOFOLLOW', 'O_NONBLOCK', 'fwalk'))):
            def walk_error(error):
                raise error
            for _, _, names, directory in os.fwalk(destination, follow_symlinks=False, onerror=walk_error):
                for name in names:
                    entry = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    if stat.S_ISLNK(entry.st_mode):
                        continue
                    if not stat.S_ISREG(entry.st_mode):
                        raise ReleaseError('Unsupported extracted runtime file type')
                    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                    with os.fdopen(descriptor, 'rb') as handle:
                        opened = os.fstat(handle.fileno())
                        if (not stat.S_ISREG(opened.st_mode)
                                or (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino)):
                            raise ReleaseError('Extracted runtime file changed before writeback')
                        os.fsync(handle.fileno())
                        _PrivateArchiveFile(handle)._discard(0, 0)
    except (ReleaseError,tarfile.TarError,OSError,ValueError,KeyError) as exc:
        shutil.rmtree(destination,ignore_errors=True)
        if isinstance(exc,ReleaseError): raise
        raise ReleaseError('Portable Python extraction failed') from None
MAX_ARCHIVE = 850_000_000
STREAM_CHUNK = 1024 * 1024
MAX_EXPANDED = 1_100_000_000
MANIFEST = 'MANIFEST.json'

class ReleaseError(RuntimeError):
    pass

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def stream_digest(handle, output=None, limit=None):
    """Hash/copy with bounded reads; optional limit also bounds snapshot disk use."""
    checksum = hashlib.sha256(); size = 0
    while True:
        chunk = handle.read(STREAM_CHUNK if limit is None else min(STREAM_CHUNK, limit + 1 - size))
        if not chunk: break
        size += len(chunk)
        if limit is not None and size > limit:
            raise ReleaseError('Archive exceeds size limit')
        checksum.update(chunk)
        if output is not None: output.write(chunk)
    return checksum.hexdigest(), size


def file_digest(path: Path, *, cold=False) -> str:
    with path.open('rb') as handle:
        if cold:
            # Only caller-selected immutable operation-owned files opt in;
            # never live data or caller-owned inputs. Preserve every digest.
            wrapped = _PrivateArchiveFile(handle)
            if wrapped.advice is not None and wrapped.dontneed is not None:
                os.fsync(handle.fileno())
            return stream_digest(wrapped)[0]
        return stream_digest(handle)[0]


class _PrivateArchiveFile:
    """Bound cache for operation-owned immutable files, never live user data.

    Cache advice is optional. Writeback errors still fail the operation; an
    unsupported advisory syscall only loses the optimization. Keep this helper
    identical in the standalone installer and release controller.
    """
    def __init__(self, handle):
        self.handle = handle
        self.pending = 0
        self.read_pending = 0
        self.advice = getattr(os, 'posix_fadvise', None)
        self.dontneed = getattr(os, 'POSIX_FADV_DONTNEED', None)
        self.page_size = os.sysconf('SC_PAGESIZE')

    def __getattr__(self, name):
        return getattr(self.handle, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.handle.__exit__(*args)

    def _discard(self, offset, size):
        if self.advice is not None and self.dontneed is not None:
            try:
                self.advice(self.handle.fileno(), offset, size, self.dontneed)
            except OSError:
                # Some filesystems/kernels do not implement advisory eviction.
                pass

    def finish_writes(self):
        self.handle.flush()
        if self.pending and self.advice is not None and self.dontneed is not None:
            os.fsync(self.handle.fileno())  # Dirty pages cannot be discarded.
            self._discard(0, 0)
        self.pending = 0

    def write(self, data):
        count = self.handle.write(data)
        self.pending += count
        if self.pending >= 8 * 1024 * 1024:
            self.finish_writes()
        return count

    def read(self, size=-1):
        if size == 0:
            return self.handle.read(0)  # A zero-byte request is not EOF.
        offset = self.handle.tell()
        data = self.handle.read(size)
        # Revisit the consumed prefix in batches: a previous hint can race
        # kernel readahead/LRU insertion. Partial trailing pages are retained.
        self.read_pending += len(data)
        end = ((offset + len(data)) // self.page_size) * self.page_size
        if not data:
            self._discard(0, 0)
        elif self.read_pending >= 8 * 1024 * 1024 and end:
            self._discard(0, end)
            self.read_pending = 0
        return data


@contextmanager
def verified_snapshot(path: Path, expected_sha: str, *, directory=None):
    # Hash exactly the bytes subsequently opened by ZipFile, without retaining
    # the archive in RAM or reopening a mutable caller-owned path after hashing.
    with _PrivateArchiveFile(tempfile.TemporaryFile(prefix='vui-verified-', dir=directory or '/var/tmp')) as snapshot:
        with path.open('rb') as source:
            actual, _ = stream_digest(source, snapshot, MAX_ARCHIVE)
        if actual != expected_sha: raise ReleaseError('Archive checksum mismatch')
        snapshot.finish_writes()
        snapshot.seek(0)
        yield snapshot

def safe_name(value: str) -> bool:
    return (isinstance(value, str) and bool(value) and len(value) < 512
        and not any(ord(c) < 32 or ord(c) == 127 or c == '\\' for c in value)
        and not value.startswith('/') and all(p not in ('', '.', '..') for p in value.split('/'))
        and str(PurePosixPath(value)) == value)

def private_root(root: Path) -> Path:
    root = Path(root).absolute()
    if root.is_symlink(): raise ReleaseError('Root must not be a symbolic link')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o077:
        raise ReleaseError('Root must be owned by the current user with mode 0700')
    return root.resolve()

def sync_directory(path: Path):
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)

def atomic_json(path: Path, value: dict):
    raw = json.dumps(value, sort_keys=True, indent=2).encode()
    fd, name = tempfile.mkstemp(prefix='.commit-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(name): os.unlink(name)

@contextmanager
def lease(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink(): raise ReleaseError('Refusing symbolic lock file')
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a+b') as handle:
        try: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ReleaseError('Service or another operation is active; stop it first') from None
        try: yield
        finally: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

@contextmanager
def _offline_writers(data: Path):
    """Stable whole-data exclusion plus leases used by older source workers."""
    from app.data_owner import data_owner, DataOwnerError
    try:
        with ExitStack() as stack:
            stack.enter_context(data_owner(data, exclusive=True))
            if os.path.lexists(data):
                with _maintenance_directory(data): pass
                for relative, name in (('runtime/xray', 'owner.lock'),
                                       ('runtime/sing-box', 'owner.lock'),
                                       ('certificates', '.worker.lock')):
                    directory = data / relative
                    if os.path.lexists(directory):
                        stack.enter_context(_maintenance_lease(directory, name))
            yield
    except DataOwnerError:
        raise ReleaseError('A data writer is active or its ownership is unsafe; stop it first') from None


@contextmanager
def writer_operation(root: Path):
    """Stopped in-process writer; let it claim its own component worker locks."""
    from app.data_owner import data_owner, DataOwnerError, core_update_pending
    try:
        with lease(root / '.operation.lock'), lease(root / '.panel.lease'), data_owner(root / 'data', exclusive=True):
            if core_update_pending():
                raise ReleaseError('核心维护尚未完成，请先恢复核心事务')
            if os.path.lexists(root / 'RESTORE_PENDING.json'):
                raise ReleaseError('An interrupted restore requires recover-restore before writes')
            yield
    except DataOwnerError:
        raise ReleaseError('A data writer is active or its ownership is unsafe; stop it first') from None


def _require_legacy_core_selection():
    from app.data_owner import core_selection_present
    if core_selection_present():
        raise ReleaseError('检测到独立核心选择或未完成事务，当前操作需要支持核心选择的恢复流程')


@contextmanager
def _legacy_stopped(root: Path):
    _require_legacy_core_selection()
    with stopped(root) as guard:
        _require_legacy_core_selection()
        _require_legacy_runtime_states(root / 'data')
        yield guard


@contextmanager
def stopped(root: Path):
    with lease(root / '.operation.lock'), lease(root / '.panel.lease'), _offline_writers(root / 'data'):
        if os.path.lexists(root / 'RESTORE_PENDING.json'):
            raise ReleaseError('An interrupted restore requires recover-restore before other operations')
        guard = _StoppedGuard(root)
        try: yield guard
        finally: guard._valid = False


class _OperationGuard:
    """In-process proof of an operation scope, never a CLI bypass."""
    def __init__(self, root: Path):
        self.root = root
        self._pid = os.getpid()
        self._valid = True

    def _check_operation(self):
        if not self._valid or self._pid != os.getpid():
            raise ReleaseError('Operation scope is not active')


class _StoppedGuard(_OperationGuard):
    """In-process proof of the existing stopped() scope."""
    def _check(self):
        self._check_operation()
        if os.path.lexists(self.root / 'RESTORE_PENDING.json'):
            raise ReleaseError('An interrupted restore requires recover-restore before other operations')

    def _prepare_replacement(self, work: Path):
        self._check()

    def _check_recovery(self):
        self._check_operation()

    def _recovery_interrupted_path(self):
        return self.root / 'recovery' / ('interrupted-' + uuid.uuid4().hex)

def supported_environment():
    key=target_key()
    if os.geteuid()==0: raise ReleaseError('Run release operations as the unprivileged service user, not root')
    if not hasattr(os,'pidfd_open'): raise ReleaseError('Linux pidfd support is required')
    fd=os.pidfd_open(os.getpid());os.close(fd)
    return key

def _packaging_artifact(name: str) -> bool:
    return ((name.startswith('wheels/') and name.endswith('.whl'))
            or (name.startswith('runtimes/') and name.endswith('.tar.gz')))


def _cold_digest_artifact(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return (_packaging_artifact(name) or (len(parts) == 3 and parts[0] == 'cores'
            and parts[1] in ('x86_64', 'aarch64') and parts[2] in ('sing-box', 'xray')))


def files_in(root: Path, *, cold_install_artifacts=False, private_files=(), excluded=()) -> dict:
    result = {}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        # Only the stopped backup path excludes its separately validated proxy
        # snapshot. Standalone payload/archive verification excludes nothing.
        if any(name == prefix or name.startswith(prefix + '/') for prefix in excluded): continue
        if path.is_symlink(): raise ReleaseError('Symbolic links are not permitted in payloads or backups')
        if path.is_dir(): continue
        if not path.is_file(): raise ReleaseError('Only regular files are permitted')
        if name == MANIFEST: continue
        if not safe_name(name): raise ReleaseError('Unsafe payload path')
        cold = name in private_files or (cold_install_artifacts and _cold_digest_artifact(name))
        result[name] = {'sha256':file_digest(path, cold=cold), 'size':path.stat().st_size,
                       'mode':0o700 if name.startswith('cores/') and path.name in ('xray','sing-box') else 0o600}
    return result

def create_archive(payload: Path, destination: Path, metadata: dict, *, private_files=None) -> str:
    if destination.exists(): raise ReleaseError('Destination already exists')
    # Only backup() supplies its own completed private copies. Neither metadata
    # nor a filename makes a caller-owned payload eligible for cache advice.
    private_names = frozenset(private_files or ())
    manifest = {**metadata, 'schema':1, 'files':files_in(payload, private_files=private_names)}
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # The backup target remains absent until a complete, synced ZIP and digest
    # exist. Never conditionally unlink a public pathname after an inode check:
    # another writer can replace it between that check and unlink().
    temporary = (tempfile.TemporaryDirectory(prefix='.backup-archive-', dir=destination.parent)
                 if private_files is not None else nullcontext(None))
    with temporary as directory:
        archive_path = Path(directory) / 'archive.zip' if directory is not None else destination
        fd = os.open(archive_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as raw:
            output = _PrivateArchiveFile(raw) if private_files is not None else raw
            with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(MANIFEST, json.dumps(manifest, sort_keys=True, indent=2))
                for name, info in manifest['files'].items():
                    entry = zipfile.ZipInfo(name); entry.compress_type = zipfile.ZIP_DEFLATED
                    entry.external_attr = (stat.S_IFREG | info['mode']) << 16
                    entry.file_size = info['size']
                    with (payload / name).open('rb') as source, archive.open(entry, 'w') as target:
                        reader = _PrivateArchiveFile(source) if name in private_names else source
                        shutil.copyfileobj(reader, target, STREAM_CHUNK)
            # ZipFile.close() rewrites headers and writes the central directory.
            if isinstance(output, _PrivateArchiveFile): output.finish_writes()
            raw.flush(); os.fsync(raw.fileno())
        with archive_path.open('rb') as handle:
            reader = _PrivateArchiveFile(handle) if private_files is not None else handle
            checksum = stream_digest(reader)[0]
        if directory is not None:
            # Same-filesystem hard-link publication is atomic and never replaces
            # an existing destination. Failure only removes our private directory.
            os.link(archive_path, destination, follow_symlinks=False)
        # If this fails after publication, leave the complete file in place and
        # report the durability failure; deleting destination would race writers.
        sync_directory(destination.parent)
        return checksum

def unpack_verified(archive_path: Path, expected_sha: str, destination: Path) -> dict:
    if not re.fullmatch(r'[a-f0-9]{64}', expected_sha): raise ReleaseError('Expected archive SHA-256 is required')
    if destination.exists(): raise ReleaseError('Extraction requires a new directory')
    try:
        with verified_snapshot(archive_path, expected_sha,
                directory=destination.parent if destination.parent.is_dir() else None) as snapshot, zipfile.ZipFile(snapshot) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(names) > 10000 or len(set(names)) != len(names) or not all(safe_name(n) for n in names):
                raise ReleaseError('Duplicate or unsafe archive paths')
            if MANIFEST not in names or sum(e.file_size for e in entries) > MAX_EXPANDED:
                raise ReleaseError('Missing manifest or archive exceeds expanded size limit')
            for entry in entries:
                kind = stat.S_IFMT(entry.external_attr >> 16)
                if entry.is_dir() or kind not in (0, stat.S_IFREG) or entry.flag_bits & 1:
                    raise ReleaseError('Archive contains a non-regular or encrypted entry')
            if archive.getinfo(MANIFEST).file_size > 4_000_000: raise ReleaseError('Manifest exceeds size limit')
            manifest = json.loads(archive.read(MANIFEST))
            if manifest.get('schema') != 1 or not isinstance(manifest.get('files'),dict): raise ReleaseError('Invalid manifest')
            if set(names) != set(manifest['files']) | {MANIFEST}: raise ReleaseError('Manifest file set mismatch')
            destination.mkdir(mode=0o700, parents=True)
            for name, info in manifest['files'].items():
                if info['mode'] not in (0o600, 0o700) or not re.fullmatch(r'[a-f0-9]{64}', info['sha256']):
                    raise ReleaseError('Invalid manifest entry')
                if type(info['size']) is not int or info['size'] != archive.getinfo(name).file_size:
                    raise ReleaseError('Payload size mismatch')
                target = destination / name; target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with archive.open(name) as source, target.open('xb') as output:
                    # Exclusive creation cannot follow an existing file/link.
                    # Only this new private release's exact core files opt in;
                    # backup paths and old/running core files never do.
                    cold_core = (manifest.get('kind') == 'release'
                                 and _cold_digest_artifact(name) and not _packaging_artifact(name))
                    created = os.fstat(output.fileno()) if cold_core else None
                    if created is not None and (not stat.S_ISREG(created.st_mode)
                            or created.st_uid != os.geteuid() or created.st_nlink != 1):
                        raise ReleaseError('Core output must be a newly owned regular file')
                    writer = (_PrivateArchiveFile(output) if manifest.get('kind') == 'release'
                              and _cold_digest_artifact(name) else output)
                    checksum, size = stream_digest(source, writer, info['size'])
                    if isinstance(writer, _PrivateArchiveFile): writer.finish_writes()
                    if created is not None:
                        current, opened = target.lstat(), os.fstat(output.fileno())
                        identity = (created.st_dev, created.st_ino)
                        if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                                or current.st_uid != created.st_uid or opened.st_uid != created.st_uid
                                or opened.st_nlink != 1 or opened.st_size != size
                                or (current.st_dev, current.st_ino) != identity
                                or (opened.st_dev, opened.st_ino) != identity):
                            raise ReleaseError('New core output identity changed during extraction')
                if size != info['size'] or checksum != info['sha256']: raise ReleaseError('Payload checksum mismatch')
                target.chmod(info['mode'])
            (destination / MANIFEST).write_text(json.dumps(manifest,sort_keys=True,indent=2))
            (destination / MANIFEST).chmod(0o600)
            return manifest
    except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile, RuntimeError) as exc:
        if destination.exists(): shutil.rmtree(destination)
        if isinstance(exc, ReleaseError): raise
        raise ReleaseError('Archive validation or extraction failed') from None

def verify_payload(payload: Path) -> dict:
    try:
        manifest = json.loads((payload / MANIFEST).read_text())
        if manifest.get('schema') != 1 or files_in(payload, cold_install_artifacts=manifest.get('kind') == 'release') != manifest['files']:
            raise ReleaseError('Installed payload differs from its manifest')
        for name, info in manifest['files'].items():
            path = payload / name
            if stat.S_IMODE(path.stat().st_mode) != info['mode'] or path.stat().st_uid != os.geteuid():
                raise ReleaseError('Payload file ownership or mode changed')
        if (payload / MANIFEST).stat().st_mode & 0o077:
            raise ReleaseError('Manifest must be private')
        return manifest
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ReleaseError('Installed payload cannot be verified') from None

def child_env(payload: Path, data: Path) -> dict:
    env = {k:v for k,v in os.environ.items() if not k.startswith(('PIP_', 'PYTHON'))}
    env.update(PYTHONPATH=str(payload), PYTHONDONTWRITEBYTECODE='1', VUI_DATA_DIR=str(data),
               VUI_BIN_DIR=str(payload/'cores'/target_arch()), VUI_PUBLIC_ORIGIN='', VUI_RELEASE_ROOT='')
    return env

def health_check(release: Path):
    payload = release / 'payload'; verify_payload(payload)
    verify_runtime(release)
    python = runtime_python(release)
    with tempfile.TemporaryDirectory(prefix='vui-candidate-') as directory:
        data = Path(directory)
        env = child_env(payload, data/'data'); env['VUI_BIN_DIR']=str(data/'no-cores')
        log_path = data/'health.log'
        with log_path.open('w') as log:
            process = subprocess.Popen([str(python),'-B','-m','uvicorn','main:app','--host','127.0.0.1','--port','0',
                '--no-proxy-headers','--no-use-colors'],cwd=payload,env=env,stdout=log,stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic()+20; match = None
                while time.monotonic() < deadline:
                    if process.poll() is not None: raise ReleaseError('Candidate failed startup health check')
                    match = re.search(r'Uvicorn running on http://127\.0\.0\.1:(\d+)', log_path.read_text())
                    if match: break
                    time.sleep(.05)
                if not match: raise ReleaseError('Candidate startup health check timed out')
                base='http://127.0.0.1:'+match.group(1); opener=build_opener(ProxyHandler({}))
                with opener.open(base+'/login',timeout=3) as response:
                    if response.status != 200: raise ReleaseError('Candidate login page unavailable')
                try: opener.open(base+'/api/auth/me',timeout=3)
                except HTTPError as exc:
                    if exc.code != 401: raise ReleaseError('Candidate authentication boundary failed') from None
                else: raise ReleaseError('Candidate allowed an anonymous administrator request')
            finally:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)

def release_id(value: str) -> str:
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}',value):
        raise ReleaseError('Invalid release identifier')
    return value

def verify_runtime(release: Path) -> dict:
    ready=json.loads((release/'READY.json').read_text())
    if ready.get('runtime_key')!=target_key(): raise ReleaseError('Prepared release runtime does not match this host')
    if runtime_tree_digest(release/'runtime')!=ready.get('runtime_tree_sha256'):
        raise ReleaseError('Portable runtime changed after preparation')
    return ready

def stage(archive: Path, expected_sha: str, root: Path) -> str:
    supported_environment(); root=private_root(root)
    with lease(root/'.operation.lock'):
        guard = _OperationGuard(root)
        try: return _stage_locked(guard, archive, expected_sha)
        finally: guard._valid = False


def _stage_locked(guard: _OperationGuard, archive: Path, expected_sha: str) -> str:
    guard._check_operation(); root=guard.root; key=target_key()
    from app.data_owner import core_update_pending
    if core_update_pending():
        raise ReleaseError('核心维护尚未完成，请先恢复核心事务')
    with tempfile.TemporaryDirectory(prefix='.stage-',dir=root) as temporary:
        payload=Path(temporary)/'payload'; meta=unpack_verified(archive,expected_sha,payload)
        if meta.get('kind')!='release' or meta.get('platform')!=PLATFORM or key not in meta.get('targets',[]):
            raise ReleaseError('Release archive does not support this Linux target: '+key)
        identity=release_id(meta.get('release_id'))
        releases=root/'releases'
        if releases.is_symlink(): raise ReleaseError('Release parent cannot be a symbolic link')
        releases.mkdir(mode=0o700,exist_ok=True)
        final=releases/identity
        if final.exists() or final.is_symlink(): raise ReleaseError('Release already exists; never overwrite a prepared version')
        final.mkdir(parents=True,mode=0o700)
        try:
            created_payload = payload.lstat()
            os.replace(payload,final/'payload')
            runtime_archive=final/'payload'/'runtimes'/(key+'.tar.gz')
            pin=(meta.get('portable_runtime_pins') or {}).get(key,{})
            private_archive = _private_runtime_tar(final/'payload', key, pin.get('sha256'),
                (created_payload.st_dev, created_payload.st_ino))
            extract_runtime(runtime_archive,final/'runtime',_private_archive=private_archive)
            python=runtime_python(final)
            env=child_env(final/'payload',root/'data')
            subprocess.run([str(python),'-m','ensurepip','--upgrade'],env=env,check=True,timeout=60,stdout=subprocess.DEVNULL)
            lock=final/'payload'/('requirements.'+key+'.lock')
            wheels=final/'payload'/'wheels'/key
            subprocess.run([str(python),'-m','pip','--isolated','--disable-pip-version-check','install',
                '--no-index','--only-binary=:all:','--require-hashes','--find-links',str(wheels),'-r',str(lock)],
                env=env,check=True,timeout=180)
            subprocess.run([str(python),'-m','pip','--isolated','check'],env=env,check=True,timeout=20)
            ready={'archive_sha256':expected_sha,'manifest_sha256':digest((final/'payload'/MANIFEST).read_bytes()),
                   'runtime_key':key,'runtime_archive_sha256':pin['sha256'],
                   'runtime_tree_sha256':runtime_tree_digest(final/'runtime')}
            atomic_json(final/'READY.json',ready)
            health_check(final)
            return identity
        except Exception:
            shutil.rmtree(final); raise

def active_verified(root: Path) -> tuple[Path,dict,dict,dict]:
    """Reuse the full active verification result inside trusted boot code.

    The returned dictionaries are observations of this verification, not a
    transferable authority token or a seal against subsequent owner writes.
    """
    try:
        current=json.loads((root/'CURRENT.json').read_text()); identity=release_id(current['release_id'])
        release=root/'releases'/identity
        if release.is_symlink(): raise ReleaseError('Release must not be a symbolic link')
        meta=verify_payload(release/'payload'); ready=verify_runtime(release)
        if digest((release/'payload'/MANIFEST).read_bytes()) != ready['manifest_sha256']:
            raise ReleaseError('Release manifest changed after health check')
        if meta['release_id'] != identity: raise ReleaseError('Release identity mismatch')
        return release,current,meta,ready
    except (OSError,ValueError,KeyError,TypeError): raise ReleaseError('No verified active release') from None

def active(root: Path) -> tuple[Path,dict]:
    release,current,_,_=active_verified(root)
    return release,current

def activate(root: Path, identity: str):
    root=private_root(root); identity=release_id(identity)
    with _legacy_stopped(root) as guard:
        _activate_locked(guard, identity)


def _readonly_proxy_store_type():
    """Private offline checker reader, retaining normal ownership and flock."""
    from app.services.proxy_store import ProxyStore
    class ExistingProxyStore(ProxyStore):
        def _locked(self, allow_uncertain=False, initialize=True):
            return super()._locked(allow_uncertain=allow_uncertain, initialize=False)
    return ExistingProxyStore


def _policy_store(data, existing_type=None):
    from app.services.proxy_store import ProxyStore
    if existing_type is not None and (not isinstance(existing_type, type) or not issubclass(existing_type, ProxyStore)):
        raise ReleaseError('Invalid private policy reader')
    return (ProxyStore if existing_type is None else existing_type)(data)


def _database_mode_fence(data: Path, *, _existing_store_type=None):
    """Minimal stdlib controller gate; never invent a missing proxy store."""
    from app.services.proxy_store import ProxyStore
    from app.services.inbound_policy import MODES
    data = Path(data)
    names = ('proxy-state', '.proxy-state-initialized', '.proxy-state.lock')
    schema = (_policy_store(data, _existing_store_type).policy_schema_version()
              if any(os.path.lexists(data / name) for name in names) else 1)
    with sqlite3.connect((data / 'v-ui.db').as_uri() + '?mode=ro', uri=True) as db:
        columns = {row[1] for row in db.execute('PRAGMA table_info(inbounds)')}
        if 'routing_mode' not in columns:
            if schema != 1:
                raise ReleaseError('Per-inbound database column is missing; recovery is required')
            return False
        invalid, explicit = db.execute("SELECT count(CASE WHEN routing_mode IS NULL OR "
            "typeof(routing_mode) != 'text' OR routing_mode NOT IN (?,?,?) THEN 1 END), "
            "count(CASE WHEN routing_mode != 'inherit' THEN 1 END) FROM inbounds", sorted(MODES)).fetchone()
        if invalid or explicit and schema != 2:
            raise ReleaseError('Inbound routing and its capability fence disagree; recovery is required')
        return bool(explicit)


def _migrate_legacy_modes(data: Path):
    """Upgrade only a verified legacy column absence in private staging."""
    _database_mode_fence(data)
    with sqlite3.connect(data / 'v-ui.db') as db:
        columns = {row[1] for row in db.execute('PRAGMA table_info(inbounds)')}
        if 'routing_mode' not in columns:
            db.execute("ALTER TABLE inbounds ADD COLUMN routing_mode VARCHAR NOT NULL DEFAULT 'inherit'")
            db.commit()
    with (data / 'v-ui.db').open('rb') as handle: os.fsync(handle.fileno())



def _check_policy_artifact_ids(value):
    if value is not None and (type(value) is not dict or set(value) != {'sing-box', 'xray'}
            or any(type(identity) is not str or not re.fullmatch('[0-9a-f]{64}', identity)
                   for identity in value.values())):
        raise ReleaseError('Runtime artifact bindings are invalid')


_RUNTIME_STATE_MEMBERS = ('runtime/sing-box/state.json', 'runtime/xray/state.json')


def _legacy_runtime_state(state):
    return (type(state) is dict and set(state) == {'revision', 'enabled'}
            and type(state['revision']) is str and re.fullmatch('[0-9a-f]{64}', state['revision'])
            and type(state['enabled']) is bool)


def _unique_runtime_fields(pairs):
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError('Duplicate core runtime field')
    return fields


def _require_legacy_runtime_states(data: Path, *, check_configs=False):
    for core in ('sing-box', 'xray'):
        try:
            _policy_runtime(data, core, _state_only=not check_configs)
        except (OSError, ValueError, RecursionError, ReleaseError):
            raise ReleaseError('核心运行状态需要独立核心恢复，已停止旧格式操作') from None


def _policy_runtime(data: Path, core: str, *, _expected_artifact_id=None, _state_only=False):
    """Strict saved runtime bytes for an offline controller, without launch."""
    if _expected_artifact_id is not None and (type(_expected_artifact_id) is not str
            or not re.fullmatch('[0-9a-f]{64}', _expected_artifact_id)):
        raise ReleaseError('Runtime artifact binding is invalid')
    directory = data / 'runtime' / core
    with ExitStack() as scope:
        try:
            root = scope.enter_context(_maintenance_directory(directory))
            os.stat('state.json', dir_fd=root, follow_symlinks=False)
        except FileNotFoundError:
            return {'revision': None, 'enabled': False}, None

        def read(name, limit):
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
            try:
                info = os.fstat(descriptor)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > limit):
                    raise ReleaseError('Committed proxy runtime file is unsafe')
                with os.fdopen(os.dup(descriptor), 'rb') as handle: return handle.read(limit + 1)
            finally: os.close(descriptor)
        state = json.loads(read('state.json', 4096), object_pairs_hook=_unique_runtime_fields)
        legacy = _legacy_runtime_state(state)
        modern = (type(_expected_artifact_id) is str and re.fullmatch('[0-9a-f]{64}', _expected_artifact_id)
                  and type(state) is dict and set(state) == {'schema', 'revision', 'enabled', 'artifact_id'}
                  and type(state['schema']) is int and state['schema'] == 2
                  and state['artifact_id'] == _expected_artifact_id)
        if (not (legacy or modern) or type(state.get('revision')) is not str
                or not re.fullmatch('[0-9a-f]{64}', state['revision']) or type(state.get('enabled')) is not bool):
            raise ReleaseError('Committed proxy runtime state is invalid')
        if _state_only:
            return state, None
        raw = read(state['revision'] + '.json', 16 * 1024 * 1024)
    if digest(raw) != state['revision']:
        raise ReleaseError('Committed proxy runtime files disagree')
    config = json.loads(raw, object_pairs_hook=_unique_runtime_fields)
    if type(config) is not dict or type(config.get('inbounds', [])) is not list:
        raise ReleaseError('Committed proxy runtime configuration is invalid')
    return state, config


def _committed_policy_protection(data: Path, *, _artifact_ids=None, _existing_store_type=None):
    """Protected IDs and exact inbound bindings from immutable runtime bytes.

    The controller deliberately does not import the ORM or compiler. Reading
    current SQL here would let an unapplied edit erase committed protection.
    """
    from app.services.proxy_store import ProxyStore
    _check_policy_artifact_ids(_artifact_ids)
    store = _policy_store(data, _existing_store_type)
    active = store.load_active(reject_pending=True)
    if active is None:
        return {'local': False, 'global': False, 'bindings': []}
    flags = active['settings']
    context = active.get('runtime_context')
    if context is None:
        if flags['local_proxy_enabled'] or flags['relay_enabled']:
            raise ReleaseError('Committed proxy provenance is missing; dedicated recovery is required')
        return {'local': False, 'global': False, 'bindings': []}
    revision = active['core_revision']
    if context['candidate_core_revision'] != revision or not re.fullmatch('[0-9a-f]{64}', revision):
        raise ReleaseError('Committed proxy runtime identity is invalid')
    state, config = _policy_runtime(data, 'sing-box',
        _expected_artifact_id=_artifact_ids['sing-box'] if _artifact_ids is not None else None)
    if state['revision'] != revision or config is None:
        raise ReleaseError('Committed proxy runtime files disagree')
    plan = store.context_plan(context, flags)
    selected = ({entry['id']: entry for entry in plan.manifest
                 if entry['enable'] and entry['effective'] == 'relay'} if plan else None)
    bindings, seen = [], set()
    for inbound in config.get('inbounds', []):
        if type(inbound) is not dict: raise ReleaseError('Invalid committed inbound binding')
        tag = inbound.get('tag')
        # Frozen compiler namespace; no protocol/compiler import in controller.
        prefix = 'vui-relay-ingress-'
        if type(tag) is not str or not tag.startswith(prefix): continue
        suffix = tag[len(prefix):]
        if not suffix.isdigit() or str(int(suffix)) != suffix:
            raise ReleaseError('Invalid committed inbound identity')
        identifier = int(suffix)
        if identifier < 1 or identifier in seen:
            raise ReleaseError('Duplicate committed inbound identity')
        seen.add(identifier)
        if selected is not None:
            entry = selected.get(identifier)
            if (entry is None or entry['core'] != 'sing-box' or inbound.get('type') != entry['protocol']
                    or inbound.get('listen_port') != entry['port']):
                raise ReleaseError('Committed protected binding differs from its manifest')
        elif not flags['relay_enabled']:
            raise ReleaseError('Unexpected protected inbound in committed runtime')
        bindings.append({'id': identifier, 'binding_sha256': digest(json.dumps(inbound,
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())})
    if selected is not None and seen != set(selected):
        raise ReleaseError('Committed protected inbound set is incomplete')
    return {'local': flags['local_proxy_enabled'], 'global': flags['relay_enabled'],
            'bindings': sorted(bindings, key=lambda item: item['id'])}


def _validate_restore_policy(data: Path, *, _artifact_ids=None, _existing_store_type=None):
    """Exact v2 source check without starting cores or importing runtime deps."""
    from app.services.proxy_store import ProxyStore
    _check_policy_artifact_ids(_artifact_ids)
    explicit = _database_mode_fence(data, _existing_store_type=_existing_store_type)
    store = _policy_store(data, _existing_store_type)
    active = store.load_active(reject_pending=True)
    if active is None:
        if explicit:
            from app.services.inbound_policy import policy_plan
            from app.services.proxy_store import _defaults
            if policy_plan(store.policy_rows(), _defaults()).relay_ids:
                raise ReleaseError('Enabled relay has no committed upstream selection')
        return
    context = active.get('runtime_context')
    if context is not None and context['schema_version'] == 2:
        from app.services.inbound_policy import policy_plan
        plan = policy_plan(store.policy_rows(), active['settings'])
        if (plan.manifest != context['plan_manifest']
                or plan.source_fingerprint != context['source_fingerprint']):
            raise ReleaseError('Backup rows differ from their committed inbound policy')
    elif explicit:
        raise ReleaseError('Backup contains unapplied inbound modes; dedicated recovery is required')
    protection = _committed_policy_protection(data, _artifact_ids=_artifact_ids,
        _existing_store_type=_existing_store_type)
    if protection['global'] or protection['bindings']:
        xray, _ = _policy_runtime(data, 'xray',
            _expected_artifact_id=_artifact_ids['xray'] if _artifact_ids is not None else None)
        if xray['enabled']:
            raise ReleaseError('Backup has incompatible active Xray and relay runtimes')


def _policy_release_compatible(data: Path, payload: Path):
    """Refuse a release lacking the explicit per-inbound reader capability.

    Read a literal from already verified package source; do not execute an old
    release against live data merely to discover that it ignores new metadata.
    """
    from app.certificates.dns_backup import DNSBackupError, require_dns_release
    try:
        require_dns_release(data, payload)
    except DNSBackupError:
        raise ReleaseError('Selected release cannot safely read DNS certificate state; downgrade refused') from None
    if (data / 'v-ui.db').exists():
        _database_mode_fence(data)
    if not (data / '.proxy-state-initialized').exists():
        return
    from app.services.proxy_store import ProxyStore, ProxyStorageError
    try:
        required = ProxyStore(data).policy_schema_version()
    except ProxyStorageError:
        raise ReleaseError('Proxy compatibility cannot be established; dedicated recovery is required') from None
    if required == 1:
        return
    import ast
    source = payload / 'app' / 'services' / 'proxy_store.py'
    try:
        raw = source.read_bytes()
        if len(raw) > 512 * 1024: raise ValueError()
        module = ast.parse(raw)
        values = [node.value.value for node in module.body if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == 'PROXY_STATE_SCHEMA_MAX'
                          for target in node.targets) and isinstance(node.value, ast.Constant)]
        if len(values) != 1 or type(values[0]) is not int or values[0] < required:
            raise ValueError()
    except (OSError, ValueError, SyntaxError):
        raise ReleaseError('Selected release cannot read per-inbound routing; downgrade refused') from None


def _activate_locked(guard: _StoppedGuard, identity: str):
    guard._check(); root=guard.root; identity=release_id(identity)
    release=root/'releases'/identity
    if release.is_symlink() or (root/'releases').is_symlink(): raise ReleaseError('Symbolic release is not permitted')
    meta=verify_payload(release/'payload')
    ready=verify_runtime(release)
    if digest((release/'payload'/MANIFEST).read_bytes()) != ready['manifest_sha256'] or meta['release_id'] != identity:
        raise ReleaseError('Release not prepared')
    _policy_release_compatible(root / 'data', release / 'payload')
    health_check(release)
    previous=active(root)[1]['release_id'] if (root/'CURRENT.json').exists() else None
    (root/'data').mkdir(mode=0o700,exist_ok=True)
    atomic_json(root/'CURRENT.json',{'release_id':identity,'previous_id':previous})

def backup(root: Path, destination: Path) -> str:
    root=private_root(root)
    with _legacy_stopped(root) as guard:
        return _backup_locked(guard, destination)


def _copy_backup_data(guard: _StoppedGuard, work: Path):
    """Copy a stopped private data snapshot; old/new archive layouts share it."""
    guard._check(); data=guard.root/'data'
    private_files = set()
    if not data.is_dir(): raise ReleaseError('No data directory to back up')
    from app.certificates.dns_backup import DNSBackupError, validate_dns_snapshot, prepare_dns_staging
    try:
        source_dns = validate_dns_snapshot(data)
    except DNSBackupError:
        raise ReleaseError('DNS state is unsafe or incomplete; no backup was created') from None
    # The initialized marker is required to distinguish saved state from a
    # never-applied installation. Copy it only as part of a validated,
    # locked proxy snapshot; do not broaden the general hidden-file rule.
    proxy_names = ('proxy-state', '.proxy-state-initialized', '.proxy-state.lock')
    if any(os.path.lexists(data / name) for name in proxy_names):
        from app.services.proxy_store import ProxyStore, ProxyStorageError
        try:
            with ProxyStore(data).backup_snapshot() as entries:
                for name, content in entries:
                    target = work / name
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with target.open('xb') as output:
                        output.write(content); output.flush(); os.fsync(output.fileno())
                    target.chmod(0o600); private_files.add(name)
        except ProxyStorageError:
            raise ReleaseError('Proxy state is unsafe or incomplete; no backup was created') from None
    for name in files_in(data, excluded=proxy_names):
        if name.endswith(('owner.lock','.db-wal','.db-shm')) or name.startswith('.'): continue
        if name=='v-ui.db': continue
        target=work/name; target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (data/name).open('rb') as source, target.open('xb') as output:
            writer = _PrivateArchiveFile(output)
            shutil.copyfileobj(source, writer, STREAM_CHUNK)
            writer.finish_writes()
        target.chmod(0o600); private_files.add(name)
    if (data/'v-ui.db').is_file():
        with sqlite3.connect(f'file:{data / "v-ui.db"}?mode=ro',uri=True) as src, sqlite3.connect(work/'v-ui.db') as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': raise ReleaseError('Database integrity check failed')
    # SQLite retains its existing backup/transaction path and is not cooled.
    try:
        prepare_dns_staging(work)
        copied_dns = validate_dns_snapshot(work)
        if copied_dns != source_dns:
            raise DNSBackupError()
    except DNSBackupError:
        raise ReleaseError('DNS backup reference closure is incomplete; no backup was created') from None
    kind = 'backup-dns01-v1' if copied_dns else 'backup'
    return private_files, kind


def _backup_locked(guard: _StoppedGuard, destination: Path) -> str:
    guard._check(); root=guard.root
    with tempfile.TemporaryDirectory(prefix='.backup-',dir=root) as temporary:
        data=root/'data'; work=Path(temporary)/'data'; work.mkdir(mode=0o700)
        private_files,kind=_copy_backup_data(guard,work)
        return create_archive(work,destination,{'kind':kind,'data_path':str(data),'platform':PLATFORM},
                              private_files=private_files)

def recover_restore(root: Path):
    _require_legacy_core_selection()
    root=private_root(root)
    with lease(root/'.operation.lock'), lease(root/'.panel.lease'), _offline_writers(root / 'data'):
        guard = _StoppedGuard(root)
        try:
            with ExitStack() as writers:
                if os.path.lexists(root / 'RESTORE_PENDING.json'):
                    _, _, _, old = _maintenance_recovery_snapshot(root, legacy=True)
                    if (root / old).exists():
                        writers.enter_context(_offline_writers(root / old))
                _recover_restore_locked(guard)
        finally: guard._valid = False


def _recover_restore_locked(guard: _StoppedGuard):
    guard._check_recovery(); root=guard.root
    _require_legacy_core_selection()
    journal=root/'RESTORE_PENDING.json'
    if not os.path.lexists(journal): return
    _, _, raw, old = _maintenance_recovery_snapshot(root, legacy=type(guard) is _StoppedGuard)
    value=json.loads(raw)
    if not safe_name(old) or not old.startswith('recovery/before-'):
        raise ReleaseError('Invalid recovery journal; manual inspection required')
    previous=root/old; data=root/'data'
    _require_legacy_runtime_states(previous if previous.is_dir() else data)
    expected = value.get('source_sha256')
    if expected is not None:
        source = previous if previous.is_dir() else data
        if recovery_tree_digest(source) != expected:
            raise ReleaseError('Recovery source changed; journal and data retained')
    if previous.is_dir():
        if data.exists(): os.replace(data,guard._recovery_interrupted_path())
        os.replace(previous,data)
    elif not data.exists(): raise ReleaseError('Original data is missing; restore from an external backup')
    sync_directory(root/'recovery');sync_directory(root)
    if expected is not None and recovery_tree_digest(data) != expected:
        raise ReleaseError('Restored source validation failed; journal retained')
    journal.unlink();sync_directory(root)

def _check_legacy_backup_format(meta, data):
    if (type(meta) is not dict or meta.get('kind') not in ('backup', 'backup-dns01-v1')
            or meta.get('data_path') != str(data)):
        raise ReleaseError('Backup belongs to a different data path or unsupported format; automatic path rewriting is not supported')


def _bound_backup_zip_directory(stream):
    # ZipFile allocates the directory and trusts its byte count, but not the
    # declared entry count. Walk fixed headers before creating any ZipInfos.
    end = zipfile._EndRecData(stream)
    if end is None:
        raise ReleaseError('Invalid backup ZIP directory')
    count, remaining, offset = (end[zipfile._ECD_ENTRIES_TOTAL], end[zipfile._ECD_SIZE],
                                end[zipfile._ECD_OFFSET])
    zip64 = end[zipfile._ECD_SIGNATURE] == zipfile.stringEndArchive64
    extension = zipfile.sizeEndCentDir64 + zipfile.sizeEndCentDir64Locator if zip64 else 0
    if (not 0 < count <= 10_000 or not 46 * count <= remaining <= 16_000_000
            or offset < 0 or offset + remaining + extension != end[zipfile._ECD_LOCATION]):
        raise ReleaseError('Invalid backup ZIP directory')
    stream.seek(offset)
    for _ in range(count):
        header = stream.read(46)
        if len(header) != 46 or header[:4] != b'PK\x01\x02':
            raise ReleaseError('Invalid backup ZIP directory')
        fields = sum(struct.unpack_from('<HHH', header, 28))
        remaining -= 46 + fields
        if remaining < 0:
            raise ReleaseError('Invalid backup ZIP directory')
        stream.seek(fields, os.SEEK_CUR)
    if remaining:
        raise ReleaseError('Invalid backup ZIP directory')


def _preflight_legacy_backup(stream, data):
    try:
        _bound_backup_zip_directory(stream)
        with zipfile.ZipFile(stream) as archive:
            names = archive.namelist()
            for name, limit in ((MANIFEST, 4_000_000), *((name, 4096) for name in _RUNTIME_STATE_MEMBERS)):
                count = names.count(name)
                if count == 0 and name != MANIFEST:
                    continue
                if count != 1 or archive.getinfo(name).file_size > limit:
                    raise ReleaseError('Invalid backup manifest or runtime state')
                value = json.loads(archive.read(name), object_pairs_hook=_unique_runtime_fields)
                if name == MANIFEST:
                    _check_legacy_backup_format(value, data)
                elif not _legacy_runtime_state(value):
                    raise ReleaseError('备份包含独立核心状态，需要使用支持核心选择的恢复流程')
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, ReleaseError):
            raise
        raise ReleaseError('Invalid backup manifest or runtime state') from None


def restore(root: Path, archive: Path, expected_sha: str):
    root=private_root(root)
    with _legacy_stopped(root) as guard:
        _restore_locked(guard, archive, expected_sha)


def _restore_locked(guard: _StoppedGuard, archive: Path, expected_sha: str):
    guard._check(); root=guard.root
    with tempfile.TemporaryDirectory(prefix='.restore-',dir=root) as temporary:
        work=Path(temporary)/'data'; meta=unpack_verified(archive,expected_sha,work)
        _check_legacy_backup_format(meta, root / 'data')
        (work/MANIFEST).unlink()
        _require_legacy_runtime_states(work)
        from app.certificates.dns_backup import (DNSBackupError, validate_dns_snapshot,
                                                prepare_dns_staging, suspend_restored_dns)
        try:
            prepare_dns_staging(work)
            incoming_dns = validate_dns_snapshot(work)
            if incoming_dns != (meta['kind'] == 'backup-dns01-v1'):
                raise DNSBackupError()
        except DNSBackupError:
            raise ReleaseError('Backup DNS format or reference closure is invalid; existing data was not replaced') from None
        proxy_names = ('proxy-state', '.proxy-state-initialized', '.proxy-state.lock')
        incoming_proxy = None
        current_schema = 1
        if any(os.path.lexists(work / name) for name in proxy_names):
            from app.services.proxy_store import ProxyStore, ProxyStorageError
            try:
                if os.path.lexists(work / '.proxy-state.lock'):
                    raise ReleaseError('Backup contains a transient proxy lock')
                # ZIP stores regular files only. A genuinely empty secrets
                # directory has no member; rebuild that exact structural path
                # in private staging, then validate every reference and object.
                for name in ('proxy-state', 'proxy-state/revisions', 'proxy-state/secrets',
                             'runtime', 'runtime/sing-box', 'runtime/xray'):
                    directory = work / name
                    if name.startswith('runtime') and not directory.exists():
                        continue
                    if name == 'proxy-state/secrets' and not directory.exists():
                        directory.mkdir(mode=0o700)
                    # Recursive extraction mkdir may use the caller's umask for
                    # intermediate directories. These exact directories belong
                    # to this new private extraction, never to live user data.
                    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        if os.fstat(fd).st_uid != os.geteuid(): raise ReleaseError('Unsafe extracted proxy directory')
                        os.fchmod(fd, 0o700); os.fsync(fd)
                    finally:
                        os.close(fd)
                with ProxyStore(work).backup_snapshot() as entries:
                    for _ in entries: pass
                incoming_proxy = ProxyStore(work).read_snapshot(initialize=False)
                if incoming_proxy['pending'] is not None:
                    raise ReleaseError('Backup contains an unresolved proxy apply; dedicated recovery is required')
            except (ProxyStorageError, OSError):
                raise ReleaseError('Backup proxy state is unsafe or incomplete; existing data was not replaced') from None
        if any(os.path.lexists(root / 'data' / name) for name in proxy_names):
            # Restore is not a mode-decrease confirmation. Neither legacy nor
            # proxy-bearing backups may silently replace an ON policy with OFF.
            from app.services.proxy_store import ProxyStore, ProxyStorageError
            try:
                current_store = ProxyStore(root / 'data')
                current = current_store.read_snapshot(initialize=False)
                current_schema = current_store.policy_schema_version()
            except ProxyStorageError:
                raise ReleaseError('Current proxy policy is unknown; dedicated recovery is required') from None
            if current['pending'] is not None:
                raise ReleaseError('Current proxy apply is unresolved; dedicated recovery is required')
            current_active = current['active']
            incoming_active = incoming_proxy['active'] if incoming_proxy is not None else None
            if current_active and any(current_active['settings'][key] and (
                    incoming_active is None or not incoming_active['settings'][key])
                    for key in ('local_proxy_enabled', 'relay_enabled')):
                raise ReleaseError('Backup would turn off an active proxy; explicitly disable and commit those settings before restoring it')
            protected = _committed_policy_protection(root / 'data')
            incoming_protected = (_committed_policy_protection(work) if incoming_proxy is not None
                                  else {'local': False, 'global': False, 'bindings': []})
            if protected['bindings'] and protected['bindings'] != incoming_protected['bindings']:
                raise ReleaseError('Backup changes committed protected inbound bindings; dedicated recovery is required')
        _database_mode_fence(work)
        if incoming_proxy is not None:
            _validate_restore_policy(work)
        if current_schema == 2:
            # Ordinary restore cannot undo an already durable capability fence.
            _migrate_legacy_modes(work)
            ProxyStore(work).ensure_policy_v2()
        if (root / 'CURRENT.json').exists():
            _policy_release_compatible(work, active(root)[0] / 'payload')
        if incoming_dns:
            try:
                suspend_restored_dns(work)
            except DNSBackupError:
                raise ReleaseError('Restored DNS authority could not be suspended; existing data was not replaced') from None
        db_path=work/'v-ui.db'
        if not db_path.is_file(): raise ReleaseError('Backup has no database')
        with sqlite3.connect(db_path) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': raise ReleaseError('Backup database is damaged')
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'users','inbounds','admin_sessions','subscription_grants'} <= tables:
                raise ReleaseError('Unsupported backup database schema')
            # Restoring must never resurrect previously revoked access tokens.
            db.execute('DELETE FROM admin_sessions')
            # Restoring an old backup must not resurrect a revoked temporary
            # maintenance link/session. Legacy archives may lack these tables.
            if 'maintenance_sessions' in tables:
                db.execute('DELETE FROM maintenance_sessions')
            if 'maintenance_grants' in tables:
                db.execute('DELETE FROM maintenance_grants')
            db.execute('UPDATE subscription_grants SET revoked=1')
            db.commit()
        with db_path.open('rb') as handle: os.fsync(handle.fileno())
        if not (root/'data').is_dir() or (root/'data').is_symlink():
            raise ReleaseError('Existing data directory is required for a reversible restore')
        # A managed transaction locks both inode generations before either rename.
        # The ordinary stopped() guard preserves its existing per-user semantics.
        guard._prepare_replacement(work)
        old='recovery/before-'+uuid.uuid4().hex; (root/'recovery').mkdir(exist_ok=True,mode=0o700)
        atomic_json(root/'RESTORE_PENDING.json',{'old':old})
        try:
            os.replace(root/'data',root/old)
            sync_directory(root/'recovery');sync_directory(root)
            os.replace(work,root/'data')
            sync_directory(root)
            (root/'RESTORE_PENDING.json').unlink();sync_directory(root)
        except OSError:
            if (root/old).exists() and not (root/'data').exists():
                os.replace(root/old,root/'data');sync_directory(root)
                (root/'RESTORE_PENDING.json').unlink();sync_directory(root)
            raise ReleaseError('Restore interrupted; original data retained. Run recover-restore before service startup.') from None


@contextmanager
def _maintenance_directory(path: Path, *, create=False):
    """Open a private service-owned directory without following any path links.

    Retain ancestor FDs until exit so a close failure cannot leak a just-opened
    child or cause retry of an already released/reused descriptor number.
    """
    path = Path(path).absolute()
    if '..' in path.parts:
        raise ReleaseError('Unsafe maintenance directory')
    with ExitStack() as stack:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, fd)
        for index, part in enumerate(path.parts[1:]):
            if create and index == len(path.parts) - 2:
                try: os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError: pass
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, fd)
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ReleaseError('Maintenance directory must be private and owned by the service user')
        yield fd


@contextmanager
def _maintenance_lease(directory: Path, name: str, *, _proof=False):
    with _maintenance_directory(directory) as parent:
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=parent)
        try:
            def verify():
                info = os.fstat(fd)
                mode = stat.S_IMODE(info.st_mode)
                legacy_core = (name == 'owner.lock' and info.st_size == 0
                               and mode & ~0o644 == 0 and mode & 0o600 == 0o600)
                linked = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.geteuid() or not (mode == 0o600 or legacy_core)
                        or (info.st_dev, info.st_ino) != (linked.st_dev, linked.st_ino)):
                    raise ReleaseError('Unsafe maintenance lease')
            verify()
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ReleaseError('Service or another operation is active; stop it first') from None
            verify()
            proof_active = True
            proof_pid = os.getpid() if _proof else None
            def proof():
                if not proof_active or proof_pid != os.getpid():
                    raise ReleaseError('Maintenance lease proof is no longer active')
                verify()
            try: yield proof if _proof else None
            finally:
                # Revoke before closing: descriptor numbers may immediately be
                # reused even when close later reports failure.
                proof_active = False
                verify()
        finally:
            os.close(fd)


_MAINTENANCE_CONFIG = Path('/etc/v-ui/service.json')


def _maintenance_read_config():
    path = _MAINTENANCE_CONFIG
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = next_fd
            info = os.fstat(fd)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise ReleaseError('Service configuration directory is not controlled')
        config_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    finally:
        os.close(fd)
    try:
        info = os.fstat(config_fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != 0 or info.st_mode & 0o022
                or info.st_size > 16384):
            raise ReleaseError('Service configuration is not controlled')
        with os.fdopen(config_fd, 'rb', closefd=False) as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ReleaseError('Service configuration exceeds limit')
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result: raise ReleaseError('Duplicate service configuration field')
                result[key] = value
            return result
        config = json.loads(raw, object_pairs_hook=unique)
    finally:
        os.close(config_fd)
    return config


def _maintenance_anchor(root: Path):
    """Verify the retained bootstrap interpreter selected by root-owned config.

    This is intentionally independent of CURRENT: old service units execute the
    retained interpreter, whose launcher then selects CURRENT's new runtime.
    """
    config = _maintenance_read_config()
    value = config.get('bootstrap_python') if isinstance(config, dict) else None
    prefix = str(root / 'releases') + '/'
    suffix = '/runtime/python/bin/python3'
    if (not isinstance(value, str) or not value.startswith(prefix) or not value.endswith(suffix)
            or config.get('target') != target_key()):
        raise ReleaseError('Unsupported bootstrap runtime path or target')
    identity = release_id(value[len(prefix):-len(suffix)])
    release = root / 'releases' / identity
    if any(parent.is_symlink() for parent in (root / 'releases', release,
            release / 'runtime', release / 'runtime/python', release / 'runtime/python/bin')):
        raise ReleaseError('Symbolic bootstrap runtime directory')
    ready = verify_runtime(release)
    executable = runtime_python(release)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ReleaseError('Bootstrap runtime is not executable')
    return identity, ready['runtime_tree_sha256']


def _maintenance_open_archive(path: Path):
    """Return a no-follow fd, including each ancestor of a user-selected path."""
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = next_fd
        return os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    finally:
        os.close(fd)


def _maintenance_snapshot(root: Path):
    with _maintenance_directory(root) as parent:
        root_info = os.fstat(parent)
        fd = os.open('CURRENT.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > 16384):
            raise ReleaseError('Unsafe current release pointer')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            current = stream.read(16385)
        if len(current) > 16384:
            raise ReleaseError('Current release pointer exceeds limit')
        return root_info.st_dev, root_info.st_ino, current
    finally:
        os.close(fd)


def _maintenance_recovery_snapshot(root: Path, *, legacy=False):
    """Read one bounded private journal; never follow its paths or create data."""
    with _maintenance_directory(root) as parent:
        root_info = os.fstat(parent)
        try:
            fd = os.open('RESTORE_PENDING.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=parent)
        except FileNotFoundError:
            return root_info.st_dev, root_info.st_ino, None, None
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_uid != os.geteuid() or before.st_mode & 0o077
                    or before.st_size > 16384):
                raise ReleaseError('Unsafe restore recovery journal')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(16385)
            after = os.fstat(fd)
            named = os.stat('RESTORE_PENDING.json', dir_fd=parent, follow_symlinks=False)
            signature = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_uid,
                value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            if (len(raw) > 16384 or signature(before) != signature(after)
                    or signature(after) != signature(named)):
                raise ReleaseError('Restore recovery journal changed')
            def unique(pairs):
                result = {}
                for name, value in pairs:
                    if name in result: raise ReleaseError('Duplicate restore recovery field')
                    result[name] = value
                return result
            value = json.loads(raw, object_pairs_hook=unique)
            extended = {'old', 'schema_version', 'kind', 'source_sha256', 'target_sha256', 'plan_sha256'}
            if (not isinstance(value, dict) or set(value) not in ({'old'}, extended)
                    or not isinstance(value['old'], str)
                    or not re.fullmatch(r'recovery/before-[A-Za-z0-9_-]{1,128}' if legacy and set(value) == {'old'}
                                        else r'recovery/before-[a-f0-9]{32}', value['old'])):
                raise ReleaseError('Invalid restore recovery journal')
            if set(value) == extended and (type(value['schema_version']) is not int
                    or value['schema_version'] != 2 or value['kind'] != 'proxy-recovery'
                    or any(not isinstance(value[key], str) or not re.fullmatch(r'[a-f0-9]{64}', value[key])
                           for key in ('source_sha256', 'target_sha256', 'plan_sha256'))):
                raise ReleaseError('Invalid restore recovery journal')
        finally:
            os.close(fd)
    if not legacy or set(value) != {'old'}:
        with _maintenance_directory(root / 'recovery'): pass
    return root_info.st_dev, root_info.st_ino, raw, value['old']


def _maintenance_existing_data(path: Path):
    try: path.lstat()
    except FileNotFoundError: return False
    with _maintenance_directory(path): pass
    return True


@contextmanager
def _maintenance_cycle_directory(path):
    """Checkpoint-only walk; every owned FD is closed once, even on failure."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ReleaseError('Unsafe maintenance cycle directory')
    with ExitStack() as stack:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, fd)
        bindings = []
        for part in path.parts[1:]:
            parent = os.fstat(fd)
            if (parent.st_uid not in (0, os.geteuid()) or not stat.S_ISDIR(parent.st_mode)
                    or parent.st_mode & 0o022 and not parent.st_mode & stat.S_ISVTX):
                raise ReleaseError('Unsafe maintenance cycle ancestor')
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, child)
            info = os.fstat(child)
            identity = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
            bindings.append((fd, part, child, identity))
            fd = child
        info = os.fstat(fd)
        if info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
            raise ReleaseError('Unsafe maintenance cycle directory')
        def verify():
            for parent, name, child, expected in bindings:
                signature = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid)
                if (signature(os.fstat(child)) != expected
                        or signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != expected):
                    raise ReleaseError('Maintenance cycle directory changed')
        verify()
        yield fd, verify
        verify()


def _maintenance_cycle_file(path, *, limit, owner, private=True):
    """Bounded no-follow FD/path identity and original-byte observation."""
    with _maintenance_cycle_directory(path.parent) as (parent, verify), ExitStack() as stack:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        stack.callback(os.close, fd)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != owner
                or before.st_mode & (0o077 if private else 0o022) or before.st_size > limit):
            raise ReleaseError('Unsafe maintenance cycle material')
        raw = os.pread(fd, before.st_size + 1, 0)
        signature = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        identity = signature(before)
        if (len(raw) != before.st_size or signature(os.fstat(fd)) != identity
                or signature(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) != identity):
            raise ReleaseError('Maintenance cycle material changed')
        verify()
        return identity, raw


def _maintenance_cycle_bootstrap(root):
    before = _maintenance_cycle_file(_MAINTENANCE_CONFIG, limit=16384, owner=0, private=False)
    anchor = _maintenance_anchor(root)
    if _maintenance_cycle_file(_MAINTENANCE_CONFIG, limit=16384, owner=0, private=False) != before:
        raise ReleaseError('Bootstrap changed during maintenance cycle')
    return before, anchor


def _maintenance_cycle_snapshot(root):
    """Existing app/data/lease facts only, never root-core transaction proof."""
    def directory(path):
        with _maintenance_cycle_directory(path) as (fd, _):
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ReleaseError('Unsafe maintenance cycle root or data')
            return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid
    root_identity = directory(root)
    current = _maintenance_cycle_file(root / 'CURRENT.json', limit=16384, owner=os.geteuid())
    release, pointer, manifest, ready = active_verified(root)
    material_paths = ((release / 'payload' / MANIFEST, 4_000_000), (release / 'READY.json', 64000))
    materials = tuple(_maintenance_cycle_file(path, limit=limit, owner=os.geteuid())
                      for path, limit in material_paths)
    def parsed(raw):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result: raise ReleaseError('Duplicate maintenance cycle material field')
                result[key] = value
            return result
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    canonical = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=True,
                                         separators=(',', ':'), allow_nan=False).encode()
    if (canonical(parsed(current[1])) != canonical(pointer)
            or canonical(parsed(materials[0][1])) != canonical(manifest)
            or canonical(parsed(materials[1][1])) != canonical(ready)
            or digest(materials[0][1]) != ready.get('manifest_sha256')):
        raise ReleaseError('Active application changed during maintenance cycle')
    bootstrap = _maintenance_cycle_bootstrap(root)
    data_identity = directory(root / 'data')
    leases = tuple(_maintenance_cycle_file(root / name, limit=16384, owner=os.geteuid(), private=False)
        for name in ('.panel.lease', '.data.owner.lease', 'data/runtime/xray/owner.lock',
                     'data/runtime/sing-box/owner.lock', 'data/certificates/.worker.lock'))
    if (directory(root) != root_identity or directory(root / 'data') != data_identity
            or _maintenance_cycle_file(root / 'CURRENT.json', limit=16384, owner=os.geteuid()) != current
            or tuple(_maintenance_cycle_file(path, limit=limit, owner=os.geteuid())
                     for path, limit in material_paths) != materials):
        raise ReleaseError('Installation changed during maintenance cycle')
    return root_identity, current, str(release), materials, bootstrap, data_identity, leases


class _MaintenanceWriterCheckpoint:
    __slots__ = ('owner', 'pid', 'facts')
    def __init__(self, owner, facts):
        self.owner, self.pid, self.facts = owner, os.getpid(), facts
    def __reduce__(self):
        raise ReleaseError('Maintenance cycle checkpoint cannot be transferred')
    def __copy__(self):
        raise ReleaseError('Maintenance cycle checkpoint cannot be transferred')
    def __deepcopy__(self, memo):
        raise ReleaseError('Maintenance cycle checkpoint cannot be transferred')


class _MaintenanceSession(_StoppedGuard):
    """Private managed-worker transaction; root never imports or calls this API.

    prepare retains the operation lease, execute takes all writer leases, and
    release_writers permits service recovery while the operation lease remains
    held. Only close releases the operation lease. There is no lock-free path.
    """
    ACTIONS = frozenset(('start', 'stop', 'restart', 'backup', 'restore', 'activate', 'stage',
                         'recover-restore'))

    def __init__(self, root: Path):
        self.root = Path(root)
        self._pid = os.getpid()
        self._valid = False
        self._state = 'new'
        self._operation = ExitStack()
        self._writers = ExitStack()
        self._action = None
        self._selection = None
        self._operation_active = False
        self._operation_proof = None
        self._writer_checkpoint = None
        self._writer_cycle_used = False
        self._writer_generation = None

    def __copy__(self):
        raise ReleaseError('Maintenance session cannot be transferred')

    def __deepcopy__(self, memo):
        raise ReleaseError('Maintenance session cannot be transferred')

    def __reduce__(self):
        raise ReleaseError('Maintenance session cannot be transferred')

    def _mark_writers_live(self):
        token = object()
        self._writer_generation = token
        # Registered after acquisitions, so raw ExitStack.close revokes first.
        self._writers.callback(self._revoke_writer_guard, token)

    def _revoke_writer_guard(self, token):
        if self._writer_generation is token:
            self._writer_generation = None
            self._valid = False

    def _state_is(self, expected):
        if self._pid != os.getpid() or self._state != expected:
            raise ReleaseError('Invalid maintenance transaction state')

    def _journal_clear(self):
        if self._action in ('backup', 'activate', 'restore', 'recover-restore'):
            _require_legacy_core_selection()
        elif self._action in ('stage', 'start', 'restart'):
            from app.data_owner import core_update_pending
            if core_update_pending():
                raise ReleaseError('核心维护尚未完成，请先恢复核心事务')
        journal = self.root / 'RESTORE_PENDING.json'
        if self._action not in ('stop', 'recover-restore') and (journal.exists() or journal.is_symlink()):
            raise ReleaseError('An interrupted restore requires recover-restore before other operations')

    def prepare(self, action: str, *, archive=None, sha256=None, identity=None):
        self._state_is('new')
        if action not in self.ACTIONS:
            raise ReleaseError('Unknown maintenance action')
        if ((action in ('restore', 'stage')) != (archive is not None and sha256 is not None)
                or (action not in ('restore', 'stage') and (archive is not None or sha256 is not None))
                or (action == 'activate') != (identity is not None)):
            raise ReleaseError('Invalid maintenance action arguments')
        try:
            supported_environment()
            # Unlike ordinary per-user operations, a managed operation never
            # creates or adopts a missing installation root.
            with _maintenance_directory(self.root): pass
            self.root = private_root(self.root)
            self._action = action
            self._operation_proof = self._operation.enter_context(
                _maintenance_lease(self.root, '.operation.lock', _proof=True))
            self._operation_active = True
            self._journal_clear()
            if action in ('backup', 'activate', 'restore'):
                _require_legacy_runtime_states(self.root / 'data')
            if action not in ('stop', 'recover-restore'):
                with _maintenance_directory(self.root / 'data'): pass
            if action not in ('stop', 'recover-restore'):
                application = active_verified(self.root)
                self._selection = application[1]
                self._active_application = application
                self._anchor = _maintenance_anchor(self.root)
                if action in ('start', 'restart'):
                    self._verify_start_cores()
            if action == 'recover-restore':
                self._recovery_snapshot = _maintenance_recovery_snapshot(self.root)
                _require_legacy_runtime_states(self._recovery_directories()[0])
            self._identity = release_id(identity) if action == 'activate' else None
            if action == 'activate':
                self._candidate()
            self._archive = None
            self._sha256 = None
            if action in ('restore', 'stage'):
                self._archive = Path(archive)
                if (not self._archive.is_absolute() or '..' in self._archive.parts
                        or not isinstance(sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', sha256)):
                    raise ReleaseError('Invalid restore input')
                fd = _maintenance_open_archive(self._archive)
                try:
                    info = os.fstat(fd)
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                            or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > MAX_ARCHIVE):
                        raise ReleaseError('Unsafe restore archive')
                    signature = lambda item: (item.st_dev, item.st_ino, item.st_size,
                                              item.st_mtime_ns, item.st_ctime_ns)
                    checksum = hashlib.sha256()
                    total = 0
                    with os.fdopen(fd, 'rb', closefd=False) as stream:
                        while True:
                            chunk = stream.read(1024 * 1024)
                            if not chunk: break
                            total += len(chunk)
                            if total > MAX_ARCHIVE:
                                raise ReleaseError('Restore archive exceeds limit')
                            checksum.update(chunk)
                        if action == 'restore' and checksum.hexdigest() == sha256:
                            _preflight_legacy_backup(stream, self.root / 'data')
                    after = os.fstat(fd)
                    named = self._archive.lstat()
                    if (signature(info) != signature(after) or signature(after) != signature(named)
                            or not stat.S_ISREG(named.st_mode)):
                        raise ReleaseError('Restore archive changed during preflight')
                    if checksum.hexdigest() != sha256:
                        raise ReleaseError('Archive checksum mismatch')
                finally:
                    os.close(fd)
                self._sha256 = sha256
            self._snapshot = _maintenance_snapshot(self.root) if action == 'stage' else None
            self._state = 'prepared'
        except BaseException:
            self.close()
            raise

    def _verify_start_cores(self):
        from app.data_owner import core_selection_present, data_owner
        try:
            with data_owner(self.root / 'data'):
                if not core_selection_present():
                    _require_legacy_runtime_states(self.root / 'data', check_configs=True)
                    return
                from app.core_updates.boot import resolve_boot
                from app.core_updates.maintenance import _versions
                release, _, manifest, ready = self._active_application
                payload = release / 'payload'
                resolve_boot(payload=payload, manifest=manifest, ready=ready, data=self.root / 'data',
                             versions=_versions(payload, manifest))
        except (ImportError, OSError, ValueError, RuntimeError, KeyError):
            raise ReleaseError('当前应用无法验证已选核心，已停止启动或重启') from None

    def _candidate(self):
        release = self.root / 'releases' / self._identity
        if release.is_symlink() or (self.root / 'releases').is_symlink():
            raise ReleaseError('Symbolic release is not permitted')
        meta = verify_payload(release / 'payload')
        ready = verify_runtime(release)
        if (digest((release / 'payload' / MANIFEST).read_bytes()) != ready['manifest_sha256']
                or meta['release_id'] != self._identity):
            raise ReleaseError('Release not prepared')

    def _lock_data(self, data: Path, *, stable=True, create=True):
        if stable:
            from app.data_owner import data_owner
            self._writers.enter_context(data_owner(data, exclusive=True))
        with _maintenance_directory(data): pass
        if create:
            for parent in (data / 'runtime', data / 'runtime' / 'xray',
                           data / 'runtime' / 'sing-box', data / 'certificates'):
                with _maintenance_directory(parent, create=True): pass
        for directory, name in ((data / 'runtime' / 'xray', 'owner.lock'),
                                (data / 'runtime' / 'sing-box', 'owner.lock'),
                                (data / 'certificates', '.worker.lock')):
            if create or os.path.lexists(directory):
                self._writers.enter_context(_maintenance_lease(directory, name))

    def _check(self):
        self._state_is('executing')
        if not self._valid:
            raise ReleaseError('Maintenance writers are not locked')
        self._journal_clear()

    def _check_operation(self):
        self._state_is('executing')
        if not self._operation_active:
            raise ReleaseError('Maintenance operation is not locked')

    def _prepare_replacement(self, work: Path):
        self._check()
        self._lock_data(work, stable=False)

    def _recovery_directories(self):
        old = self._recovery_snapshot[3]
        paths = ([self.root / old] if old is not None else []) + [self.root / 'data']
        present = [path for path in paths if _maintenance_existing_data(path)]
        if not present:
            raise ReleaseError('Original and current data are missing; external recovery required')
        return present

    def _check_recovery(self):
        self._check()
        if self._action != 'recover-restore':
            raise ReleaseError('Recovery requires its own maintenance action')
        if _maintenance_recovery_snapshot(self.root) != self._recovery_snapshot:
            raise ReleaseError('Restore recovery journal changed during maintenance')

    def _recovery_interrupted_path(self):
        target = super()._recovery_interrupted_path()
        if target.exists() or target.is_symlink():
            raise ReleaseError('Refusing to overwrite existing interrupted data')
        return target

    def _automatic_backup(self):
        parent = self.root / 'backups'
        with _maintenance_directory(parent, create=True): pass
        name = 'maintenance-' + uuid.uuid4().hex + '.zip'
        checksum = _backup_locked(self, parent / name)
        return {'backup_name': name, 'backup_sha256': checksum}

    def execute(self) -> dict:
        self._state_is('prepared')
        try:
            if self._action == 'stage':
                self._state = 'executing'
                self._check_operation()
                self._journal_clear()
                if _maintenance_snapshot(self.root) != self._snapshot:
                    raise ReleaseError('Installation changed during candidate preparation')
                identity = _stage_locked(self, self._archive, self._sha256)
                if _maintenance_snapshot(self.root) != self._snapshot:
                    raise ReleaseError('Installation changed during candidate preparation')
                self._state = 'done'
                return {'prepared_release': identity}
            self._writers.enter_context(_maintenance_lease(self.root, '.panel.lease'))
            if self._action == 'recover-restore':
                if _maintenance_recovery_snapshot(self.root) != self._recovery_snapshot:
                    raise ReleaseError('Restore recovery journal changed during maintenance')
                # The canonical name may be absent after the first rename.
                # Its stable lease must survive rollback and journal clearing,
                # until release_writers explicitly permits a service restart.
                from app.data_owner import data_owner
                self._writers.enter_context(data_owner(self.root / 'data', exclusive=True))
                raw = self._recovery_snapshot[2]
                fingerprinted = raw is not None and json.loads(raw).get('kind') == 'proxy-recovery'
                for data in self._recovery_directories():
                    self._lock_data(data, stable=data != self.root / 'data', create=not fingerprinted)
            else:
                self._lock_data(self.root / 'data')
            self._mark_writers_live()
            self._valid = True
            self._state = 'executing'
            self._check()
            if self._action not in ('stop', 'recover-restore') and active(self.root)[1] != self._selection:
                raise ReleaseError('Active release changed during maintenance')
            if self._action not in ('stop', 'recover-restore') and _maintenance_anchor(self.root) != self._anchor:
                raise ReleaseError('Bootstrap runtime changed during maintenance')
            result = {}
            if self._action in ('start', 'restart'):
                self._verify_start_cores()
            if self._action in ('backup', 'activate', 'restore'):
                _require_legacy_runtime_states(self.root / 'data')
            if self._action == 'backup':
                result = self._automatic_backup()
            elif self._action == 'activate':
                self._candidate()
                result = self._automatic_backup()
                _activate_locked(self, self._identity)
                result['selected_release'] = self._identity
            elif self._action == 'restore':
                _restore_locked(self, self._archive, self._sha256)
            elif self._action == 'recover-restore':
                _recover_restore_locked(self)
                with _maintenance_directory(self.root / 'data'): pass
                active(self.root)
                _maintenance_anchor(self.root)
                if (self.root / 'RESTORE_PENDING.json').exists() or (self.root / 'RESTORE_PENDING.json').is_symlink():
                    raise ReleaseError('Restore recovery remains pending')
            self._journal_clear()
            self._state = 'done'
            return result
        except BaseException:
            self._state = 'failed'
            raise

    def release_writers_for_trial(self):
        """One private writer cycle; not a new action or root-core trial grant."""
        self._state_is('done')
        if (self._action in ('stop', 'stage') or not self._valid or not self._operation_active
                or self._operation_proof is None or self._writer_generation is None or self._writer_cycle_used):
            raise ReleaseError('Maintenance writer cycle is unavailable')
        try:
            self._journal_clear()
            self._operation_proof()
            checkpoint = _MaintenanceWriterCheckpoint(self, _maintenance_cycle_snapshot(self.root))
            self._operation_proof()
            self._writer_cycle_used = True
            self._writer_checkpoint = checkpoint
            self._valid = False
            self._writers.close()
            self._state = 'trial_writers_released'
        except BaseException:
            self._valid = False
            self._state = 'failed'
            try: self._writers.close()
            finally: self._writer_checkpoint = None
            raise

    def _check_cycle_checkpoint(self):
        checkpoint = self._writer_checkpoint
        if (checkpoint is None or checkpoint.owner is not self or checkpoint.pid != os.getpid()
                or not self._operation_active or self._operation_proof is None):
            raise ReleaseError('Maintenance writer checkpoint is unavailable')
        self._operation_proof()
        self._journal_clear()
        if _maintenance_cycle_snapshot(self.root) != checkpoint.facts:
            raise ReleaseError('Installation changed during maintenance writer cycle')
        self._operation_proof()

    def reacquire_writers(self):
        """Reacquire existing writers only; never execute the previous action."""
        self._state_is('trial_writers_released')
        self._valid = False
        try:
            self._check_cycle_checkpoint()
            self._writers.enter_context(_maintenance_lease(self.root, '.panel.lease'))
            self._lock_data(self.root / 'data', create=False)
            self._check_cycle_checkpoint()
            self._mark_writers_live()
            self._state = 'writers_reacquired'
            self._valid = True
        except BaseException:
            self._valid = False
            self._state = 'failed'
            try: self._writers.close()
            finally: self._writer_checkpoint = None
            raise

    def _check_reacquired_writers(self):
        """Narrow internal guard; ordinary executing/done checks do not apply."""
        self._state_is('writers_reacquired')
        token = self._writer_generation
        if not self._valid or token is None:
            raise ReleaseError('Maintenance writers are not locked')
        self._check_cycle_checkpoint()
        if not self._valid or self._writer_generation is not token:
            raise ReleaseError('Maintenance writers are not locked')

    def release_reacquired_writers(self):
        """Terminal private exit before external old-topology recovery.

        Releases only these writer leases; operation remains held until close.
        No service/core action or second writer cycle is granted here.
        """
        self._state_is('writers_reacquired')
        try:
            self._check_reacquired_writers()
        except BaseException:
            self._valid = False
            self._writer_generation = None
            self._writer_checkpoint = None
            self._state = 'failed'
            self._writers.close()
            raise
        # Revoke before any close callback, including a lost acknowledgement.
        self._valid = False
        self._writer_generation = None
        self._writer_checkpoint = None
        try:
            self._writers.close()
        except BaseException:
            self._state = 'failed'
            raise
        self._state = 'recovery_writers_released'

    def release_writers(self):
        self._state_is('done')
        if self._action == 'stage':
            raise ReleaseError('Candidate preparation owns no writer leases')
        if self._action == 'recover-restore' and ((self.root / 'RESTORE_PENDING.json').exists()
                or (self.root / 'RESTORE_PENDING.json').is_symlink()):
            raise ReleaseError('Restore recovery remains pending')
        self._journal_clear()
        self._writers.close()
        self._valid = False
        self._state = 'writers_released'

    def close(self):
        if self._pid != os.getpid():
            raise ReleaseError('Maintenance session belongs to another process')
        self._valid = False
        self._writer_checkpoint = None
        self._writer_generation = None
        try:
            self._writers.close()
        finally:
            try:
                self._operation.close()
            finally:
                self._operation_active = False
                self._operation_proof = None
                self._state = 'closed'


_RECOVERY_LOCKS = {'.proxy-state.lock', 'runtime/xray/owner.lock',
                   'runtime/sing-box/owner.lock', 'certificates/.worker.lock'}


def recovery_tree_digest(data: Path, *, copy_to: Path | None = None) -> str:
    """Bounded descriptor-based fingerprint/copy; excludes only transient leases.

    This intentionally accepts corrupt bytes, never unsafe file ownership or
    links. It can preserve an unknown policy without interpreting it as OFF.
    Caller holds the stable exclusive data-owner lease throughout.
    """
    records = []; total = 0; count = 0
    with _maintenance_directory(data) as root:
        identity = os.fstat(root)
        if copy_to is not None:
            copy_to.mkdir(mode=0o700)
        def failure(error):
            raise error
        for current, directories, files, directory in os.fwalk(data, follow_symlinks=False, onerror=failure):
            directories.sort(); files.sort()
            relative = Path(current).relative_to(data)
            for name in directories + files:
                count += 1
                if count > 32768: raise ReleaseError('Recovery source contains too many entries')
                entry = os.stat(name, dir_fd=directory, follow_symlinks=False)
                path = (relative / name).as_posix()
                if not safe_name(path) or entry.st_uid != os.geteuid():
                    raise ReleaseError('Unsafe recovery source entry')
                if stat.S_ISDIR(entry.st_mode):
                    if entry.st_mode & 0o077: raise ReleaseError('Recovery directories must be private')
                    records.append([path, 'directory'])
                    if copy_to is not None: (copy_to / path).mkdir(mode=0o700)
                    continue
                if not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1:
                    raise ReleaseError('Recovery requires owned regular files without links')
                if path in _RECOVERY_LOCKS:
                    if entry.st_size or entry.st_mode & 0o022:
                        raise ReleaseError('Unsafe transient recovery lease')
                    continue
                if entry.st_mode & 0o077: raise ReleaseError('Recovery files must be private')
                total += entry.st_size
                if total > MAX_EXPANDED: raise ReleaseError('Recovery source exceeds size limit')
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                try:
                    signature = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
                        info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                    if signature(entry) != signature(os.fstat(fd)):
                        raise ReleaseError('Recovery source changed before reading')
                    with os.fdopen(fd, 'rb', closefd=False) as source:
                        target = (copy_to / path).open('xb') if copy_to is not None else nullcontext()
                        with target as output:
                            checksum, size = stream_digest(source, output, MAX_EXPANDED)
                            if output is not None:
                                os.fchmod(output.fileno(), stat.S_IMODE(entry.st_mode))
                                output.flush(); os.fsync(output.fileno())
                    if (size != entry.st_size or signature(entry) != signature(os.fstat(fd))
                            or signature(entry) != signature(os.stat(name, dir_fd=directory, follow_symlinks=False))):
                        raise ReleaseError('Recovery source changed while reading')
                    records.append([path, stat.S_IMODE(entry.st_mode), size, checksum])
                finally:
                    os.close(fd)
        named = data.lstat()
        if (identity.st_dev, identity.st_ino) != (named.st_dev, named.st_ino):
            raise ReleaseError('Recovery data directory changed')
    if copy_to is not None:
        for current, _, _ in os.walk(copy_to, topdown=False): sync_directory(Path(current))
    return digest(json.dumps(sorted(records), sort_keys=True, separators=(',', ':')).encode())
