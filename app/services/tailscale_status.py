"""Opt-in local read-only observation. No network/core/configuration actions."""
from __future__ import annotations

import json
import os
import re
import selectors
import signal
import subprocess
import sys
import threading
import time

COMMAND = ("/usr/bin/tailscale", "--socket=/var/run/tailscale/tailscaled.sock",
           "status", "--json", "--peers=false")
ENVIRONMENT = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
EXECUTION_SECONDS = 2.0
REAP_SECONDS = 0.5
MAX_BYTES = 65_536
MAX_NODES = 1024
MAX_DEPTH = 16
CACHE_SECONDS = 30.0
VERSION = re.compile(r"1\.104\.2(?:-t[0-9a-f]{6,40}(?:-g[0-9a-f]{6,40})?)?\Z")


class ObservationError(Exception):
    """Only fixed, local reason codes leave the collector."""


class CleanupError(ObservationError):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ObservationError("invalid_data")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ObservationError("invalid_data")


def project(raw: bytes) -> tuple[str, str]:
    """Discard all identity/address/raw fields, including fields not yet known."""
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ObservationError("output_limit")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=_invalid_constant)
        pending = [(data, 0)]
        count = 0
        while pending:
            value, depth = pending.pop()
            count += 1
            if count > MAX_NODES or depth > MAX_DEPTH:
                raise ObservationError("output_limit")
            if isinstance(value, dict):
                pending.extend((item, depth + 1) for item in value.values())
            elif isinstance(value, list):
                pending.extend((item, depth + 1) for item in value)
        if not isinstance(data, dict):
            raise ObservationError("invalid_data")
        version = data.get("Version")
        if not isinstance(version, str) or VERSION.fullmatch(version) is None:
            raise ObservationError("unsupported_version")
        state = data.get("BackendState")
        if state == "Stopped":
            return "offline", "stopped"
        if state in ("NoState", "NeedsLogin", "NeedsMachineAuth", "Starting"):
            return "unknown", "not_ready"
        if state != "Running":
            raise ObservationError("invalid_data")
        self_status = data.get("Self")
        if not isinstance(self_status, dict) or type(self_status.get("Online")) is not bool:
            raise ObservationError("invalid_data")
        return ("online", "control_connected") if self_status["Online"] else ("offline", "control_disconnected")
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise ObservationError("invalid_data") from None


def _reap(process):
    # Retain ownership immediately before signaling; an external/auto reaper
    # invalidates the PID/PGID guard. Never signal an already reaped identity.
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise CleanupError("cleanup_failed")
    try:
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except OSError:
        raise CleanupError("cleanup_failed") from None
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        raise CleanupError("cleanup_failed") from None
    try:
        process.wait(timeout=REAP_SECONDS)
    except (subprocess.TimeoutExpired, OSError):
        raise CleanupError("cleanup_failed") from None


def collect() -> bytes:
    """Linux-only, fixed executable/socket/arguments/environment, streaming cap."""
    if sys.platform != "linux" or not hasattr(os, "WNOWAIT"):
        raise ObservationError("unavailable")
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise ObservationError("unavailable")
    deadline = time.monotonic() + EXECUTION_SECONDS
    try:
        process = subprocess.Popen(COMMAND, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=ENVIRONMENT, cwd="/",
                                   shell=False, close_fds=True, start_new_session=True)
    except FileNotFoundError:
        raise ObservationError("unavailable") from None
    except PermissionError:
        raise ObservationError("permission_denied") from None
    except OSError:
        raise ObservationError("unavailable") from None
    owned = True
    try:
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ObservationError("timeout")
                if not selector.select(remaining):
                    raise ObservationError("timeout")
                chunk = os.read(process.stdout.fileno(), min(4096, MAX_BYTES + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > MAX_BYTES:
                    raise ObservationError("output_limit")
            # Observe completion without reaping: keeping the leader as a zombie
            # reserves its PID/PGID until group cleanup has signalled descendants.
            # Popen.poll()/wait() here would permit signaling a recycled group.
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ObservationError("timeout")
                exited = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if exited is not None:
                    if exited.si_code != os.CLD_EXITED or exited.si_status != 0:
                        raise ObservationError("unavailable")
                    return bytes(output)
                time.sleep(min(0.01, remaining))
    except ChildProcessError:
        owned = False
        raise CleanupError("cleanup_failed") from None
    except OSError:
        raise ObservationError("unavailable") from None
    finally:
        try:
            if owned:
                _reap(process)
        finally:
            # Closing a local read pipe is not child cleanup. Do not let a close
            # failure hide the cleanup fuse or expose an underlying exception.
            try:
                process.stdout.close()
            except OSError:
                pass


def _response(state="unknown", reason="busy", observed_at=None, stale=True, remaining=0):
    return {"enabled": True, "state": state, "reason": reason,
            "observed_at": observed_at, "stale": stale,
            "valid_for_seconds": max(0, int(remaining))}


class TailscaleStatus:
    def __init__(self, *, _collector=collect, _clock=time.monotonic, _wall=time.time):
        self._collector = _collector
        self._clock = _clock
        self._wall = _wall
        self._lock = threading.Lock()
        self._started = None
        self._cached = None
        self._blocked = False

    def read(self):
        if os.getenv("VUI_TAILSCALE_STATUS_ENABLED") != "1":
            return {"enabled": False}
        if not self._lock.acquire(blocking=False):
            return _response()
        try:
            if self._blocked:
                return _response(reason="cleanup_failed")
            now = self._clock()
            if self._started is not None and now - self._started < CACHE_SECONDS:
                if self._cached is None:
                    return _response()
                return _response(**self._cached, remaining=CACHE_SECONDS - (now - self._started))
            self._started = now
            self._cached = None
            try:
                state, reason = project(self._collector())
            except CleanupError:
                self._blocked = True
                return _response(reason="cleanup_failed")
            except ObservationError as error:
                state = "unknown"
                reason = str(error) if str(error) in {
                    "invalid_data", "unsupported_version", "output_limit", "unavailable",
                    "permission_denied", "timeout"} else "unavailable"
            except Exception:
                # Never leak raw exception text or log daemon output.
                state, reason = "unknown", "unavailable"
            remaining = CACHE_SECONDS - (self._clock() - now)
            if remaining <= 0:
                return _response(reason="expired")
            self._cached = {"state": state, "reason": reason,
                            "observed_at": int(self._wall()), "stale": False}
            return _response(**self._cached, remaining=remaining)
        finally:
            self._lock.release()


status = TailscaleStatus()
