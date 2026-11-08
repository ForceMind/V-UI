"""Runtime binding for independently trusted material; not a trust resolver.

Only trusted Python integration may construct these descriptors after checking
selection, authority and compatibility. A reference/path/hash does not grant
execution permission. No CLI, user path, URL or environment entry point lives
here. Bundle owner/mode must come from a separately verified whole payload and
manifest. An FD pins an inode, not immutable contents: a service-owned 0700
bundle remains writable by that owner. Boundary rechecks detect observed
in-place changes; they do not grant protection against a malicious owner racing
execution. The explicit anchor/owner seam permits isolated, unprivileged tests.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat

from .selection import ComponentReference, Reference, reference_identity


class RuntimeArtifactError(ValueError):
    """Fixed non-secret failure; never include a path or binary output."""


def _need(value):
    if not value:
        raise RuntimeArtifactError('Runtime core artifact is unsafe or changed')


def _absolute(value):
    _need(isinstance(value, Path) and value.is_absolute()
          and str(value) == os.path.abspath(value) and '..' not in value.parts)
    return value


@dataclass(frozen=True)
class RuntimeArtifact:
    reference: Reference
    path: Path
    owner: int = 0
    anchor: Path = Path('/')
    mode: int = 0o555

    def __post_init__(self):
        # Structural validation only; callers still supply independent trust.
        reference_identity(self.reference)
        _absolute(self.path)
        _absolute(self.anchor)
        _need(type(self.owner) is int and self.owner >= 0
              and self.path != self.anchor and self.anchor in self.path.parents)
        _need(type(self.mode) is int and self.mode in (0o555, 0o700, 0o755))
        if type(self.reference) is ComponentReference:
            _need(self.mode == 0o555 and (self.anchor != Path('/') or self.owner == 0))

    @property
    def artifact_id(self):
        return reference_identity(self.reference)

    @property
    def core(self):
        return self.reference.core

    @property
    def version(self):
        return self.reference.version


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _directory(fd, owner):
    info = os.fstat(fd)
    _need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, owner) and not info.st_mode & 0o022)
    return info


def _file(fd, artifact, *, held=False):
    info = os.fstat(fd)
    _need(stat.S_ISREG(info.st_mode) and info.st_uid == artifact.owner
          and info.st_nlink in ((0, 1) if held else (1,)) and stat.S_IMODE(info.st_mode) == artifact.mode
          and info.st_size == artifact.reference.binary_size)
    return info


def verify_runtime_artifact_fd(fd, artifact):
    """Rehash the held inode without reopening the selected pathname.

    Metadata must stay stable during this read. A later unlink is permitted;
    byte/size/owner/mode changes are not. This is an execution-boundary check,
    not a kernel write seal for a service-owned executable.
    """
    try:
        before = _file(fd, artifact, held=True)
        digest, count = hashlib.sha256(), 0
        while True:
            block = os.pread(fd, min(65536, artifact.reference.binary_size + 1 - count), count)
            if not block:
                break
            digest.update(block)
            count += len(block)
            _need(count <= artifact.reference.binary_size)
        after = _file(fd, artifact, held=True)
        _need(_identity(before) == _identity(after)
              and count == artifact.reference.binary_size
              and digest.hexdigest() == artifact.reference.binary_sha256)
    except OSError:
        raise RuntimeArtifactError('Runtime core artifact is unsafe or unavailable') from None


@contextmanager
def open_runtime_artifact(artifact):
    """Hold one verified inode, independent of later pathname replacement.

    Production walks from /. Test anchors are explicitly trusted roots, as in
    the artifact store; there is no corresponding public configuration option.
    All opened ancestors retain their identity through validation. The returned
    FD is non-inheritable: runtime uses the panel's proc entry for its lifetime.
    """
    _need(type(artifact) is RuntimeArtifact)
    fds, bindings = [], []
    try:
        directory = os.open(artifact.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fds.append(directory)
        _directory(directory, artifact.owner)
        parts = artifact.path.relative_to(artifact.anchor).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            fds.append(child)
            info = _directory(child, artifact.owner)
            bindings.append((directory, part, _identity(info)))
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        fds.append(fd)
        before = _file(fd, artifact)
        verify_runtime_artifact_fd(fd, artifact)
        after = _file(fd, artifact)
        _need(_identity(before) == _identity(after))
        _need(_identity(after) == _identity(os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)))
        for parent, name, expected in bindings:
            _need(expected == _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)))
        os.lseek(fd, 0, os.SEEK_SET)
        yield fd
    except OSError:
        raise RuntimeArtifactError('Runtime core artifact is unsafe or unavailable') from None
    finally:
        close_failed = False
        for fd in reversed(fds):
            try:
                os.close(fd)
            except OSError:
                # Do not retry close: a failed close may already release the
                # descriptor, whose number could be reused by another thread.
                close_failed = True
        if close_failed:
            raise RuntimeArtifactError('Runtime core artifact descriptor cleanup failed') from None
