"""Single-owner Linux core runtime with immutable revisions and commit-last state."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import json
import ipaddress
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid

import psutil

from app.data_owner import DataOwnerError, data_owner

VERSIONS = {"xray": "26.3.27", "sing-box": "1.14.2"}


class CoreError(RuntimeError):
    """Only fixed, non-secret messages from this exception reach the API."""


def encode(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def revision(value: dict) -> str:
    return hashlib.sha256(encode(value)).hexdigest()


MAX_RUNTIME_BYTES = 16 * 1024 * 1024
_STORAGE_ERROR = "Runtime storage is unsafe or unavailable; offline recovery is required"


def _state_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate state key")
        result[key] = value
    return result


def _require_private(condition: bool):
    if not condition:
        raise CoreError(_STORAGE_ERROR)


def _identity(info):
    return info.st_dev, info.st_ino


def _check_directory(fd: int, *, private: bool):
    info = os.fstat(fd)
    _require_private(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                     and (stat.S_IMODE(info.st_mode) == 0o700 if private else not info.st_mode & 0o022))


def _check_file(fd: int):
    info = os.fstat(fd)
    _require_private(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                     and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)
    return info


def _check_lease(fd: int):
    """Accept legacy read-only bits on an empty non-secret lease, never repair.

    Older releases created owner.lock with open('a+'), usually mode 0644.
    Only this empty lock may keep those bits; secret-bearing files stay 0600.
    """
    info = os.fstat(fd)
    _require_private(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                     and info.st_nlink == 1 and info.st_size == 0
                     and stat.S_IMODE(info.st_mode) & ~0o644 == 0
                     and stat.S_IMODE(info.st_mode) & 0o600 == 0o600)
    return info


def _verify_lease(directory: int):
    try:
        fd = os.open("owner.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return
    try:
        info = _check_lease(fd)
        _require_private(_identity(info) == _identity(
            os.stat("owner.lock", dir_fd=directory, follow_symlinks=False)))
    finally:
        os.close(fd)


def _private_directory(path: Path, *, create: bool = False) -> int:
    """Open every component without following links; never repair live modes."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = path.parts[1:]
        _require_private(bool(parts))
        for part in parts:
            ancestor = os.fstat(fd)
            # Match ProxyStore's root/service-owned sticky temporary parent
            # exception. The selected runtime root itself is always private.
            _require_private(ancestor.st_uid in (0, os.geteuid()) and (
                not ancestor.st_mode & 0o022 or bool(ancestor.st_mode & stat.S_ISVTX)))
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        _check_directory(fd, private=True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_file(directory: int, name: str, *, optional: bool = False) -> int | None:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        if optional:
            return None
        raise
    try:
        info = _check_file(fd)
        _require_private(_identity(info) == _identity(os.stat(name, dir_fd=directory, follow_symlinks=False)))
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_fd(fd: int, *, limit: int = MAX_RUNTIME_BYTES) -> bytes:
    before = _check_file(fd)
    _require_private(before.st_size <= limit)
    os.lseek(fd, 0, os.SEEK_SET)
    pieces, total = [], 0
    while True:
        part = os.read(fd, min(65536, limit + 1 - total))
        if not part:
            break
        pieces.append(part)
        total += len(part)
        _require_private(total <= limit)
    after = _check_file(fd)
    _require_private((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                     == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns))
    os.lseek(fd, 0, os.SEEK_SET)
    return b"".join(pieces)


def _read_file(directory: int, name: str, *, optional: bool = False, limit: int = MAX_RUNTIME_BYTES):
    fd = _open_file(directory, name, optional=optional)
    if fd is None:
        return None
    try:
        return _read_fd(fd, limit=limit)
    finally:
        os.close(fd)


def _atomic_write(directory: int, name: str, content: bytes):
    _check_directory(directory, private=True)
    _require_private(len(content) <= MAX_RUNTIME_BYTES)
    existing = _read_file(directory, name, optional=True)
    if re.fullmatch(r"[a-f0-9]{64}\.json", name):
        _require_private(hashlib.sha256(content).hexdigest() == name[:-5])
        if existing is not None:
            _require_private(existing == content)
            return
    staged = ".stage-" + uuid.uuid4().hex
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=directory)
    try:
        _check_file(fd)
        remaining = memoryview(content)
        while remaining:
            count = os.write(fd, remaining)
            if count <= 0:
                raise OSError("short write")
            remaining = remaining[count:]
        os.fsync(fd)
        # Recheck the target before replacing it, including non-regular files.
        current = _read_file(directory, name, optional=True)
        if re.fullmatch(r"[a-f0-9]{64}\.json", name) and current is not None:
            _require_private(current == content)
            return
        _check_directory(directory, private=True)
        os.replace(staged, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        os.close(fd)
        try:
            os.unlink(staged, dir_fd=directory)
        except FileNotFoundError:
            pass


def atomic_write(path: Path, content: bytes) -> None:
    """Atomically write into an owned 0700 directory, rejecting unsafe targets."""
    directory = None
    try:
        path = Path(os.path.abspath(path))
        directory = _private_directory(path.parent, create=True)
        _atomic_write(directory, path.name, content)
    except OSError:
        raise CoreError(_STORAGE_ERROR) from None
    finally:
        if directory is not None:
            os.close(directory)


class CoreRuntime:
    def __init__(self, name: str, binary: Path, root: Path):
        # abspath is lexical: resolving would hide an unsafe symlink ancestor.
        self.name, self.binary, self.root = name, binary.resolve(), Path(os.path.abspath(root))
        self.state_file = self.root / "state.json"
        self.process: subprocess.Popen | None = None
        self.lock = threading.RLock()
        self.lease = None
        self._data_owner = None
        self._root_fd = None
        self._config_fd = None
        self.last_error = None

    @contextmanager
    def _directory(self, *, create: bool = False):
        directory = None
        try:
            try:
                directory = _private_directory(self.root, create=create)
            except FileNotFoundError:
                if create or self._root_fd is not None:
                    raise
            if directory is not None:
                if self._root_fd is not None:
                    _check_directory(self._root_fd, private=True)
                    _require_private(_identity(os.fstat(directory)) == _identity(os.fstat(self._root_fd)))
                if self.lease is not None:
                    info = _check_lease(self.lease.fileno())
                    linked = os.stat("owner.lock", dir_fd=directory, follow_symlinks=False)
                    _require_private(_identity(info) == _identity(linked))
                else:
                    _verify_lease(directory)
            yield directory
        except OSError:
            raise CoreError(_STORAGE_ERROR) from None
        finally:
            if directory is not None:
                os.close(directory)

    def _claim(self):
        owner = None
        try:
            if (self._data_owner is None and self.root.parent.name == "runtime"
                    and self.root.name == self.name):
                candidate = data_owner(self.root.parent.parent)
                candidate.__enter__()
                owner = candidate
            self._claim_core()
        except BaseException as exc:
            if owner is not None:
                owner.__exit__(None, None, None)
            if isinstance(exc, DataOwnerError):
                raise CoreError("Data is in use or unsafe; core ownership refused") from None
            raise
        else:
            if owner is not None:
                self._data_owner = owner

    def _claim_core(self):
        with self._directory(create=True) as directory:
            if self.lease is not None:
                return
            fd = os.open("owner.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=directory)
            try:
                _check_lease(fd)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise CoreError("Another panel process owns this core; use one worker") from None
                info = _check_lease(fd)
                _require_private(_identity(info) == _identity(
                    os.stat("owner.lock", dir_fd=directory, follow_symlinks=False)))
                self._root_fd = os.dup(directory)
                self.lease = os.fdopen(fd, "r+b")
            except BaseException:
                os.close(fd)
                raise

    def _write(self, path: Path, content: bytes):
        _require_private(path.parent == self.root)
        with self._directory() as directory:
            _require_private(directory is not None)
            _atomic_write(directory, path.name, content)

    def _remove_state(self):
        with self._directory() as directory:
            _require_private(directory is not None)
            if _read_file(directory, "state.json", optional=True, limit=4096) is not None:
                os.unlink("state.json", dir_fd=directory)
                os.fsync(directory)

    def _read_config(self, directory: int, rev: str):
        raw = _read_file(directory, rev + ".json")
        _require_private(hashlib.sha256(raw).hexdigest() == rev)
        return raw

    @contextmanager
    def _config(self, path: Path, *, allow_external: bool = False):
        # Core processes receive this verified inode, never a re-opened pathname.
        path = Path(os.path.abspath(path))
        try:
            with ExitStack() as stack:
                if path.parent == self.root:
                    directory = stack.enter_context(self._directory())
                else:
                    # Public check(path) also supports standalone validation,
                    # but its directory and file must meet the same privacy rules.
                    _require_private(allow_external)
                    directory = _private_directory(path.parent)
                    stack.callback(os.close, directory)
                _require_private(directory is not None)
                fd = _open_file(directory, path.name)
                stack.callback(os.close, fd)
                raw = _read_fd(fd)
                hashed = re.fullmatch(r"[a-f0-9]{64}\.json", path.name) is not None
                _require_private(allow_external or hashed)
                if hashed:
                    _require_private(hashlib.sha256(raw).hexdigest() == path.stem)
                yield fd, raw
        except OSError:
            raise CoreError(_STORAGE_ERROR) from None

    def state(self) -> dict:
        with self.lock:
            try:
                with self._directory() as directory:
                    raw = _read_file(directory, "state.json", optional=True, limit=4096) if directory is not None else None
                    if raw is None:
                        return {"revision": None, "enabled": False}
                    state = json.loads(raw, object_pairs_hook=_state_object,
                                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    if (not isinstance(state, dict) or set(state) != {"revision", "enabled"}
                            or type(state.get("enabled")) is not bool
                            or not re.fullmatch(r"[a-f0-9]{64}", state.get("revision", ""))):
                        raise ValueError()
                    self._read_config(directory, state["revision"])
                    return state
            except (OSError, ValueError, TypeError, RecursionError):
                raise CoreError("Applied state is damaged; restore a verified backup") from None

    def config_path(self) -> Path | None:
        state = self.state()
        return self.root / (state["revision"] + ".json") if state["revision"] else None

    def active_config(self) -> dict | None:
        """Read the committed config privately; never expose this in API DTOs.

        Callers must not reopen config_path() themselves: a separately returned
        pathname loses the verified descriptor and root/lease binding.
        """
        with self.lock:
            state = self.state()
            if state["revision"] is None:
                return None
            with self._config(self.root / (state["revision"] + ".json")) as (_, raw):
                try:
                    value = json.loads(raw)
                    _require_private(isinstance(value, dict))
                    return value
                except (ValueError, TypeError, RecursionError):
                    raise CoreError("Applied state is damaged; restore a verified backup") from None

    def check(self, path: Path):
        with self.lock, self._config(path, allow_external=True) as (fd, _):
            if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
                raise CoreError("Pinned core binary is missing or not executable")
            try:
                result = subprocess.run([str(self.binary), "version"], capture_output=True, timeout=5)
                expected = VERSIONS[self.name].encode()
                first = result.stdout.splitlines()[0] if result.stdout else b""
                if result.returncode or expected not in first.split():
                    raise CoreError("Core version does not match the tested version lock")
                config = f"/proc/{os.getpid()}/fd/{fd}"
                command = ([str(self.binary), "run", "-test", "-format=json", "-c", config] if self.name == "xray"
                           else [str(self.binary), "check", "-c", config])
                # Malformed configs may echo secrets: discard output, including
                # from successful checks, rather than writing it to /tmp.
                result = subprocess.run(command, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, timeout=15)
                if result.returncode:
                    raise CoreError("Candidate configuration was rejected by the pinned core")
            except (OSError, subprocess.TimeoutExpired):
                raise CoreError("Core validation failed or timed out") from None

    def _terminate(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=3)
            self.process = None
        if self._config_fd is not None:
            os.close(self._config_fd)
            self._config_fd = None

    def _launch(self, path: Path):
        """Readiness means the child owns its listeners, not end-to-end reachability."""
        with self._config(path) as (fd, raw):
            value = json.loads(raw)
            _require_private(self._config_fd is None)
            # Keep this descriptor for the entire core lifetime. The watchdog
            # closes inherited fds, so use the panel's proc entry explicitly.
            self._config_fd = os.dup(fd)
        launcher = Path(__file__).with_name("core_child.py")
        command = [str(self.binary), "run"]
        if self.name == "xray":
            command.append("-format=json")
        command += ["-c", f"/proc/{os.getpid()}/fd/{self._config_fd}"]
        try:
            self.process = subprocess.Popen([sys.executable, str(launcher), str(os.getpid()), *command],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except BaseException:
            os.close(self._config_fd)
            self._config_fd = None
            raise
        deadline = time.monotonic() + 5
        ready_since = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise CoreError("Candidate core exited before its listeners became ready")
            try:
                children = psutil.Process(self.process.pid).children()
                sockets = [s for child in children for s in child.net_connections(kind="inet")]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                children, sockets = [], []
            if children and self._listeners_ready(value, sockets):
                ready_since = ready_since or time.monotonic()
                if time.monotonic() - ready_since >= 0.3:
                    return
            else:
                ready_since = None
            time.sleep(0.05)
        raise CoreError("Candidate core did not establish the expected listeners")

    @staticmethod
    def _listeners_ready(config: dict, sockets) -> bool:
        """Check owned transport/address/port, including exclusive loopback binds.

        A wildcard on the requested port is not evidence that a loopback-only
        proxy is private. Legacy configs without an explicit address retain
        their old port readiness behavior; compiled proxy entries always name
        their exact address and are checked strictly.
        """
        listeners = [item for item in sockets if item.laddr and (
            item.status == psutil.CONN_LISTEN or item.type == socket.SOCK_DGRAM)]
        for inbound in config.get("inbounds", []):
            port = int(inbound.get("listen_port", inbound.get("port", 0)))
            if not port:
                continue
            protocol = inbound.get("type", inbound.get("protocol"))
            transport = inbound.get("streamSettings", {}).get("network")
            kind = (socket.SOCK_DGRAM if protocol in {"hysteria2", "tuic"} or transport in {"kcp", "quic"}
                    else socket.SOCK_STREAM if protocol in {"vless", "vmess", "trojan", "mixed", "http", "socks"}
                    else None)
            matches = [item for item in listeners if item.laddr.port == port]
            requested = inbound.get("listen")
            if requested:
                try:
                    address = ipaddress.ip_address(requested)
                    exact = [item for item in matches if ipaddress.ip_address(item.laddr.ip) == address]
                    if address.is_loopback and len(exact) != len(matches):
                        return False
                    matches = exact
                except (TypeError, ValueError):
                    return False
            if not any(kind is None or item.type == kind for item in matches):
                return False
        return True

    def restore(self, revision: str | None, *, enabled: bool) -> None:
        """Restore only a verified committed revision chosen by the journal.

        Never use ``apply`` here: its generic rollback could re-launch the
        uncommitted candidate. A failed restoration leaves the process stopped
        and retains immutable configurations and the last state evidence.
        """
        with self.lock:
            try:
                self._claim()
                # Reject an unsafe existing state target before starting a core.
                with self._directory() as directory:
                    _read_file(directory, "state.json", optional=True, limit=4096)
                if (type(enabled) is not bool or revision is None and enabled
                        or revision is not None and (
                            type(revision) is not str or not re.fullmatch(r"[a-f0-9]{64}", revision))):
                    raise CoreError("Saved runtime revision is invalid")
                path = None
                if revision is not None:
                    path = self.root / (revision + ".json")
                    with self._directory() as directory:
                        self._read_config(directory, revision)
                    self.check(path)
                self._terminate()
                if enabled:
                    self._launch(path)
                if revision is None:
                    self._remove_state()
                else:
                    self._write(self.state_file, encode({"revision": revision, "enabled": enabled}))
                self.last_error = None
            except BaseException as exc:
                self._terminate()
                self.last_error = "Runtime recovery failed; core stopped and committed evidence retained"
                if not isinstance(exc, Exception):
                    raise
                raise CoreError(self.last_error) from None

    def apply(self, config: dict, *, activate: bool = True, rollback_on_failure: bool = True) -> dict:
        with self.lock:
            self._claim()
            previous = self.state()
            rev = revision(config)
            path = self.root / (rev + ".json")
            self._write(path, encode(config))
            try:
                self.check(path)
            except CoreError as exc:
                self.last_error = str(exc)
                raise
            if not activate:
                return {**self.status(), "valid": True, "applied": False,
                        "candidate_revision": rev, "message": "Validated only; runtime unchanged"}
            was_running = self.process is not None and self.process.poll() is None
            enabled = bool(config.get("inbounds"))
            try:
                self._terminate()
                if enabled:
                    self._launch(path)
                # Sole commit record. Earlier crashes recover the prior revision.
                self._write(self.state_file, encode({"revision": rev, "enabled": enabled}))
                self.last_error = None
            except (CoreError, OSError, ValueError):
                self._terminate()
                rollback_ok = True
                try:
                    if previous["revision"]:
                        self._write(self.state_file, encode(previous))
                    else:
                        self._remove_state()
                except (CoreError, OSError):
                    rollback_ok = False
                if rollback_on_failure and was_running and previous["revision"]:
                    try:
                        self._launch(self.root / (previous["revision"] + ".json"))
                    except (CoreError, OSError):
                        self._terminate()
                        rollback_ok = False
                if not rollback_on_failure:
                    # A higher-level proxy journal owns rollback. It must check
                    # the current self-address safety context BEFORE an older
                    # core can listen again. Never briefly relaunch it here.
                    self.last_error = "Apply failed; core stopped for coordinated recovery"
                else:
                    self.last_error = ("Apply failed; previous runtime restored" if rollback_ok
                                       else "Apply and runtime recovery failed; previous committed config retained")
                raise CoreError(self.last_error) from None
            return {**self.status(), "valid": True, "applied": True, "candidate_revision": rev}

    def recover(self):
        with self.lock:
            try:
                state = self.state()
                if self.process and self.process.poll() is None:
                    return
                # A previously exited watchdog may still have a held config fd.
                self._terminate()
                if not state["revision"]:
                    return
                self._claim()
                state = self.state()
                if state["enabled"]:
                    path = self.root / (state["revision"] + ".json")
                    self.check(path)
                    self._launch(path)
            except BaseException:
                self._terminate()
                raise

    def stop(self, *, persist: bool = True):
        with self.lock:
            try:
                if persist:
                    self._claim()
                    state = self.state()
                    if state["revision"]:
                        self._write(self.state_file, encode({**state, "enabled": False}))
            finally:
                # A damaged state path must not prevent a requested stop.
                self._terminate()

    def close(self):
        with self.lock:
            self.stop(persist=False)
            if self.lease is not None:
                self.lease.close()
                self.lease = None
            if self._root_fd is not None:
                os.close(self._root_fd)
                self._root_fd = None
            owner, self._data_owner = self._data_owner, None
            if owner is not None:
                owner.__exit__(None, None, None)

    def status(self) -> dict:
        try:
            state = self.state()
        except CoreError as exc:
            self.last_error = str(exc)
            state = {"revision": None, "enabled": False}
        running = self.process is not None and self.process.poll() is None
        return {"name": self.name, "version_required": VERSIONS[self.name],
            "binary_exists": self.binary.is_file(), "running": running,
            "pid": self.process.pid if running else None,
            "applied_revision": state["revision"], "enabled": state["enabled"],
            "config_exists": state["revision"] is not None,
            "last_error": self.last_error}
