"""Single-owner Linux core runtime with immutable revisions and commit-last state."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time

import psutil

VERSIONS = {"xray": "26.3.27", "sing-box": "1.14.2"}


class CoreError(RuntimeError):
    """Only fixed, non-secret messages from this exception reach the API."""


def encode(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def revision(value: dict) -> str:
    return hashlib.sha256(encode(value)).hexdigest()


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".stage-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class CoreRuntime:
    def __init__(self, name: str, binary: Path, root: Path):
        self.name, self.binary, self.root = name, binary.resolve(), root.resolve()
        self.state_file = self.root / "state.json"
        self.process: subprocess.Popen | None = None
        self.lock = threading.RLock()
        self.lease = None
        self.last_error = None

    def _claim(self):
        if self.lease is not None:
            return
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lease = (self.root / "owner.lock").open("a+")
        try:
            fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lease.close()
            raise CoreError("Another panel process owns this core; use one worker") from None
        self.lease = lease

    def state(self) -> dict:
        if not self.state_file.exists():
            return {"revision": None, "enabled": False}
        try:
            state = json.loads(self.state_file.read_text())
            if (not isinstance(state, dict) or not isinstance(state.get("enabled"), bool)
                    or not re.fullmatch(r"[a-f0-9]{64}", state.get("revision", ""))):
                raise ValueError()
            path = self.root / (state["revision"] + ".json")
            if hashlib.sha256(path.read_bytes()).hexdigest() != state["revision"]:
                raise ValueError()
            return state
        except (OSError, ValueError, TypeError):
            raise CoreError("Applied state is damaged; restore a verified backup") from None

    def config_path(self) -> Path | None:
        state = self.state()
        return self.root / (state["revision"] + ".json") if state["revision"] else None

    def check(self, path: Path):
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            raise CoreError("Pinned core binary is missing or not executable")
        try:
            result = subprocess.run([str(self.binary), "version"], capture_output=True, timeout=5)
            expected = VERSIONS[self.name].encode()
            first = result.stdout.splitlines()[0] if result.stdout else b""
            if result.returncode or expected not in first.split():
                raise CoreError("Core version does not match the tested version lock")
            command = ([str(self.binary), "run", "-test", "-c", str(path)] if self.name == "xray"
                       else [str(self.binary), "check", "-c", str(path)])
            # Never return core stdout/stderr: malformed configs may echo secrets.
            with tempfile.TemporaryFile() as sink:
                result = subprocess.run(command, stdout=sink, stderr=sink, timeout=15)
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

    def _launch(self, path: Path):
        """Readiness means the child owns its listeners, not end-to-end reachability."""
        value = json.loads(path.read_text())
        expected = {int(i.get("listen_port", i.get("port", 0))) for i in value.get("inbounds", [])}
        expected.discard(0)
        launcher = Path(__file__).with_name("core_child.py")
        self.process = subprocess.Popen([sys.executable, str(launcher), str(os.getpid()),
            str(self.binary), "run", "-c", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        deadline = time.monotonic() + 5
        ready_since = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise CoreError("Candidate core exited before its listeners became ready")
            try:
                children = psutil.Process(self.process.pid).children()
                sockets = [s for child in children for s in child.net_connections(kind="inet")]
                bound = {s.laddr.port for s in sockets if s.laddr and (s.status == psutil.CONN_LISTEN or s.type == 2)}
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                children, bound = [], set()
            if children and expected <= bound:
                ready_since = ready_since or time.monotonic()
                if time.monotonic() - ready_since >= 0.3:
                    return
            else:
                ready_since = None
            time.sleep(0.05)
        raise CoreError("Candidate core did not establish the expected listeners")

    def apply(self, config: dict, *, activate: bool = True) -> dict:
        with self.lock:
            self._claim()
            previous = self.state()
            rev = revision(config)
            path = self.root / (rev + ".json")
            atomic_write(path, encode(config))
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
                atomic_write(self.state_file, encode({"revision": rev, "enabled": enabled}))
                self.last_error = None
            except (CoreError, OSError, ValueError):
                self._terminate()
                rollback_ok = True
                try:
                    if previous["revision"]:
                        atomic_write(self.state_file, encode(previous))
                    else:
                        self.state_file.unlink(missing_ok=True)
                except OSError:
                    rollback_ok = False
                if was_running and previous["revision"]:
                    try:
                        self._launch(self.root / (previous["revision"] + ".json"))
                    except (CoreError, OSError):
                        self._terminate()
                        rollback_ok = False
                self.last_error = ("Apply failed; previous runtime restored" if rollback_ok
                                   else "Apply and runtime recovery failed; previous committed config retained")
                raise CoreError(self.last_error) from None
            return {**self.status(), "valid": True, "applied": True, "candidate_revision": rev}

    def recover(self):
        with self.lock:
            if not self.state_file.exists() or (self.process and self.process.poll() is None):
                return
            self._claim()
            state = self.state()
            if state["enabled"]:
                path = self.root / (state["revision"] + ".json")
                self.check(path)
                self._launch(path)

    def stop(self, *, persist: bool = True):
        with self.lock:
            if persist:
                self._claim()
                state = self.state()
                if state["revision"]:
                    atomic_write(self.state_file, encode({**state, "enabled": False}))
            self._terminate()

    def close(self):
        with self.lock:
            self.stop(persist=False)
            if self.lease is not None:
                self.lease.close()
                self.lease = None

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
