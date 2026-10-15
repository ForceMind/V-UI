"""Stable data-tree ownership shared by live writers and offline maintenance.

The sibling lease is never renamed with the data directory or removed. This
stdlib-only module deliberately does not initialize anything inside that tree.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
import threading


class DataOwnerError(ValueError):
    """Data is busy or its ownership cannot be established safely."""


_EXCLUSIVE = threading.local()
_UNSAFE = "Data ownership is unsafe or unavailable"
_BUSY = "Data is in use by a service or maintenance operation; stop it first"


def _require(condition: bool) -> None:
    if not condition:
        raise DataOwnerError(_UNSAFE)


def _identity(info):
    return info.st_dev, info.st_ino


def _trusted_directory(fd: int) -> None:
    info = os.fstat(fd)
    # Root/service-owned sticky scratch directories protect an owned child
    # against replacement by other users, just like a non-writable ancestor.
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.geteuid())
             and (not info.st_mode & 0o022 or bool(info.st_mode & stat.S_ISVTX)))


def _parent(path: Path) -> int:
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _trusted_directory(fd)
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            _trusted_directory(fd)
        info = os.fstat(fd)
        _require(info.st_uid == os.geteuid() or bool(info.st_mode & stat.S_ISVTX))
        return fd
    except BaseException:
        os.close(fd)
        raise


def _verify(parent: int, fd: int, path: Path, name: str) -> None:
    info = os.fstat(fd)
    _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
             and info.st_nlink == 1 and info.st_size == 0
             and stat.S_IMODE(info.st_mode) == 0o600)
    linked = os.stat(name, dir_fd=parent, follow_symlinks=False)
    _require(_identity(info) == _identity(linked))
    current = _parent(path)
    try:
        _require(_identity(os.fstat(parent)) == _identity(os.fstat(current)))
    finally:
        os.close(current)


def _check_data(parent: int, name: str) -> None:
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
             and not info.st_mode & 0o022)


def _check_pending_restore(parent: int) -> None:
    # A crash releases flock but not the durable recovery obligation. Do not
    # parse or follow this path: even an invalid or dangling journal blocks
    # ordinary writers until an exclusive recovery operation resolves it.
    try:
        os.stat("RESTORE_PENDING.json", dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise DataOwnerError("Interrupted restore requires offline recovery before data can be used")


def _safe_verify(parent: int, fd: int, path: Path, name: str) -> None:
    try:
        _verify(parent, fd, path, name)
    except OSError:
        raise DataOwnerError(_UNSAFE) from None


@contextmanager
def data_owner(data_dir: Path, *, exclusive: bool = False):
    """Take nonblocking shared writer or exclusive offline ownership.

    A shared scope may borrow this thread's exclusive lease to validate the
    original store offline, including during a pending restore. Ordinary
    shared acquisition refuses a durable restore journal while holding flock.
    Other threads and forked children must take their
    own flock and cannot borrow that proof. Shared scopes are otherwise normal
    independent leases, and may be released by a shutdown thread.
    """
    # Collapsing '..' before rejecting it would hide a symlink ancestor and
    # lease a different tree than a writer using its original SQLite path.
    selected = Path(data_dir)
    _require(".." not in selected.parts)
    path = Path(os.path.abspath(selected))
    _require(bool(path.name))
    name = "." + path.name + ".owner.lease"
    key = str(path)
    held = getattr(_EXCLUSIVE, "held", {})
    owner = held.get(key)
    if not exclusive and owner is not None and owner[0] == os.getpid():
        parent = fd = None
        try:
            try:
                # A supported writer can retain this scope beyond the outer
                # offline context (for example a SQLite connection or runtime).
                # Dup retains the same exclusive flock open-file description;
                # closing the outer scope cannot release a live borrower's lock.
                parent = os.dup(owner[1])
                fd = os.dup(owner[2])
                _verify(parent, fd, path.parent, name)
                _check_data(parent, path.name)
            except OSError:
                raise DataOwnerError(_UNSAFE) from None
            try:
                yield
            finally:
                _safe_verify(parent, fd, path.parent, name)
        finally:
            if fd is not None:
                os.close(fd)
            if parent is not None:
                os.close(parent)
        return

    parent = fd = None
    registered = False
    try:
        try:
            parent = _parent(path.parent)
            # Do not resolve symlinks, create directories, or touch missing state.
            _check_data(parent, path.name)
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
            try:
                fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                os.fsync(parent)
            except FileExistsError:
                fd = os.open(name, flags, dir_fd=parent)
            _verify(parent, fd, path.parent, name)
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DataOwnerError(_BUSY) from None
            _verify(parent, fd, path.parent, name)
            if not exclusive:
                _check_pending_restore(parent)
        except OSError:
            raise DataOwnerError(_UNSAFE) from None
        if exclusive:
            _EXCLUSIVE.held = held
            held[key] = (os.getpid(), parent, fd)
            registered = True
        try:
            yield
        finally:
            _safe_verify(parent, fd, path.parent, name)
    finally:
        if registered:
            held.pop(key, None)
        # Closing rather than LOCK_UN avoids unlocking a parent's inherited
        # open-file description when a forked child drops its own descriptor.
        if fd is not None:
            os.close(fd)
        if parent is not None:
            os.close(parent)
