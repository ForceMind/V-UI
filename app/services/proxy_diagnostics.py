"""Bounded, administrator-owned diagnostics. No runtime or store writes.

The service retains only allowlisted observations. Credentials cross a private
pipe to one throwaway worker and are never retained in a job or returned by API.
Production uses one panel process, as required by the runtime ownership model.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from app.services.proxy_store import ProxyConflict

TIMEOUT_SECONDS = 30
MAX_OUTPUT_BYTES = 65536
MAX_INPUT_BYTES = 16384
MAX_ACTIVE = 2
START_INTERVAL_SECONDS = 30
RESULT_TTL_SECONDS = 300
MAX_RETAINED_JOBS = 128
CATALOG_VERSION = 1
TARGET_HOST = "api64.ipify.org"
TARGET_URL = "https://api64.ipify.org?format=json"
DNS_ENDPOINT = "https://cloudflare-dns.com/dns-query (1.1.1.1:443 via upstream)"
STAGES = ("configuration", "tcp", "tls_or_reality", "proxy_forwarding", "https_exit", "dns", "external_client")
STATUSES = frozenset({"passed", "failed", "not_tested", "insufficient_evidence", "expired"})
ERROR_CODES = frozenset({
    "DIAGNOSTIC_BUSY", "DIAGNOSTIC_RATE_LIMITED", "DIAGNOSTIC_NOT_FOUND",
    "INVALID_DIAGNOSTIC_REQUEST", "DIAGNOSTIC_UNAVAILABLE", "DIAGNOSTIC_TIMEOUT",
    "DIAGNOSTIC_OUTPUT_LIMIT", "DIAGNOSTIC_INVALID_OUTPUT", "DIAGNOSTIC_CANCELLED",
    "DIAGNOSTIC_WORKER_FAILED", "DIAGNOSTIC_RESOURCE_LIMIT", "ADDRESS_CHECK_FAILED",
    "RESOLUTION_FAILED", "RESOLUTION_TIMEOUT", "SELF_ADDRESS_UNAVAILABLE",
    "CORE_UNAVAILABLE", "CORE_VERSION_MISMATCH", "CORE_CONFIG_REJECTED", "CORE_START_FAILED",
    "TCP_FAILED", "TLS_FAILED", "PROXY_REQUEST_FAILED", "HTTPS_FAILED",
    "HTTP_STATUS_REJECTED", "HTTP_RESPONSE_INVALID", "HTTP_RESPONSE_LIMIT",
    "EXIT_IP_INVALID", "DIAGNOSTIC_COMPLETE",
})
STAGE_CODES = frozenset({
    "NOT_TESTED", "CORE_CONFIG_VALID", "TCP_CONNECTED", "TLS_CERTIFICATE_VERIFIED",
    "REALITY_AUTHENTICATED_BY_HTTPS", "REALITY_REQUIRES_PROXY_EVIDENCE",
    "PROXY_FORWARDING_PROVEN_BY_HTTPS", "HTTPS_EXIT_OBSERVED", "DNS_PATH_BOUND_TO_HTTPS",
    "DNS_PATH_UNPROVEN", "EXTERNAL_CLIENT_NOT_TESTED", *ERROR_CODES,
})
_JOB_ID = re.compile(r"diag_[0-9a-f]{32}\Z")
_PROFILE_ID = re.compile(r"up_[0-9a-f]{32}\Z")
_REVISION = re.compile(r"[0-9a-f]{64}\Z")


class DiagnosticsError(ValueError):
    """Controlled codes only; no exception text, profile or child output."""
    def __init__(self, code):
        self.code = code if code in ERROR_CODES else "DIAGNOSTIC_UNAVAILABLE"
        super().__init__(self.code)


def _strict_object(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            raise ValueError()
        value[key] = child
    return value


def _admin_key(admin_id):
    if type(admin_id) is int and admin_id > 0:
        return ("id", admin_id)
    if type(admin_id) is str and 1 <= len(admin_id) <= 128 and not any(ord(c) < 32 for c in admin_id):
        return ("name", admin_id)
    raise DiagnosticsError("INVALID_DIAGNOSTIC_REQUEST")


class _Admission:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = set()
        self.last_start = {}

    def acquire(self, owner, now):
        with self.lock:
            self.last_start = {key: value for key, value in self.last_start.items()
                               if now - value < START_INTERVAL_SECONDS or key in self.active}
            if owner in self.active or len(self.active) >= MAX_ACTIVE:
                raise DiagnosticsError("DIAGNOSTIC_BUSY")
            if owner in self.last_start and now - self.last_start[owner] < START_INTERVAL_SECONDS:
                raise DiagnosticsError("DIAGNOSTIC_RATE_LIMITED")
            # Bound authenticated-account churn without discarding rate limits.
            if len(self.last_start) >= 256:
                raise DiagnosticsError("DIAGNOSTIC_BUSY")
            self.active.add(owner)
            self.last_start[owner] = now

    def release(self, owner):
        with self.lock:
            self.active.discard(owner)


_ADMISSION = _Admission()


def _initial_stages():
    return [{"stage": stage, "status": "not_tested",
             "code": "EXTERNAL_CLIENT_NOT_TESTED" if stage == "external_client" else "NOT_TESTED",
             "observed_at": None,
             "endpoint": TARGET_URL if stage == "https_exit" else DNS_ENDPOINT if stage == "dns"
             else "external client" if stage == "external_client" else "saved upstream"}
            for stage in STAGES]


@dataclass
class _Job:
    owner: tuple = field(repr=False)
    public: dict
    started: float
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    done: threading.Event = field(default_factory=threading.Event, repr=False)
    thread: threading.Thread | None = field(default=None, repr=False)


class ProxyDiagnostics:
    """Only ``start(owner, saved_id, exact_revision)`` can initiate network work.

    Underscored dependencies are in-process unit-test seams, never deserialized
    from API input, environment variables or saved user configuration.
    """
    def __init__(self, store, *, panel_origin=None, _runner=None, _admission=None,
                 _clock=None, _wall_clock=None):
        self.store, self.panel_origin = store, panel_origin
        self._runner = _runner or _run_worker
        self._admission = _admission or _ADMISSION
        self._clock, self._wall = _clock or time.monotonic, _wall_clock or time.time
        self._jobs, self._lock, self._closed = {}, threading.RLock(), False

    def _prune(self):
        now = self._wall()
        for identifier, job in list(self._jobs.items()):
            if job.done.is_set() and now >= job.public["expires_at"] + 60:
                del self._jobs[identifier]
        terminal = sorted((job for job in self._jobs.values() if job.done.is_set()),
                          key=lambda job: job.public["created_at"])
        for job in terminal[:max(0, len(self._jobs) - MAX_RETAINED_JOBS + 1)]:
            del self._jobs[job.public["job_id"]]

    def start(self, admin_id, upstream_id, base_revision):
        owner = _admin_key(admin_id)
        if (type(upstream_id) is not str or not _PROFILE_ID.fullmatch(upstream_id)
                or type(base_revision) is not str or not _REVISION.fullmatch(base_revision)):
            raise DiagnosticsError("INVALID_DIAGNOSTIC_REQUEST")
        started = self._clock()
        # This is the only privileged store read. Never keep this snapshot in a
        # cached job, and do not modify selected upstream or either saved switch.
        snapshot = self.store.load_draft()
        if snapshot["revision"] != base_revision:
            raise ProxyConflict("Proxy draft changed before diagnostics")
        if upstream_id not in snapshot["upstreams"]:
            raise DiagnosticsError("INVALID_DIAGNOSTIC_REQUEST")
        profile = copy.deepcopy(snapshot["upstreams"][upstream_id])
        del snapshot
        with self._lock:
            if self._closed:
                raise DiagnosticsError("DIAGNOSTIC_UNAVAILABLE")
            self._admission.acquire(owner, started)
            self._prune()
            now = self._wall()
            public = {"job_id": "diag_" + uuid.uuid4().hex, "upstream_id": upstream_id,
                      "revision": base_revision, "catalog_version": CATALOG_VERSION,
                      "state": "running", "created_at": now, "finished_at": None,
                      "expires_at": now + TIMEOUT_SECONDS + RESULT_TTL_SECONDS,
                      "code": None, "stages": _initial_stages(), "pinned_address": None,
                      "resolved_at": None, "exit_ip": None, "stale": False}
            job = _Job(owner, public, started)
            self._jobs[public["job_id"]] = job
            payload = {"profile": profile, "profile_id": upstream_id, "revision": base_revision,
                       "panel_origin": self.panel_origin}
            job.thread = threading.Thread(target=self._execute, args=(job, payload),
                                          name="proxy-diagnostic", daemon=True)
            try:
                job.thread.start()
            except Exception:
                del self._jobs[public["job_id"]]
                self._admission.release(owner)
                raise DiagnosticsError("DIAGNOSTIC_UNAVAILABLE") from None
            return copy.deepcopy(public)

    def _emit(self, job, event):
        """Strict worker-output allowlist; arbitrary text can never reach a job."""
        if type(event) is not dict or type(event.get("event")) is not str:
            raise DiagnosticsError("DIAGNOSTIC_INVALID_OUTPUT")
        with self._lock:
            if event["event"] == "endpoint" and set(event) == {"event", "address", "resolved_at"}:
                from app.services.proxy_policy import AddressPolicy, PolicyError
                try:
                    address = event["address"]
                    if type(address) is not str or str(ipaddress.ip_address(address)) != address:
                        raise ValueError()
                    AddressPolicy().check_addresses((address,))
                    if type(event["resolved_at"]) is not int or event["resolved_at"] < 0:
                        raise ValueError()
                except (ValueError, PolicyError):
                    raise DiagnosticsError("DIAGNOSTIC_INVALID_OUTPUT") from None
                job.public.update(pinned_address=address, resolved_at=event["resolved_at"])
                return
            if (event["event"] != "stage" or set(event) != {"event", "stage", "status", "code"}
                    or type(event["stage"]) is not str or event["stage"] not in STAGES
                    or type(event["status"]) is not str or event["status"] not in STATUSES - {"expired"}
                    or type(event["code"]) is not str or event["code"] not in STAGE_CODES
                    or event["stage"] == "external_client"):
                raise DiagnosticsError("DIAGNOSTIC_INVALID_OUTPUT")
            permitted_passes = {
                "configuration": {"CORE_CONFIG_VALID"}, "tcp": {"TCP_CONNECTED"},
                "tls_or_reality": {"TLS_CERTIFICATE_VERIFIED", "REALITY_AUTHENTICATED_BY_HTTPS"},
                "proxy_forwarding": {"PROXY_FORWARDING_PROVEN_BY_HTTPS"},
                "https_exit": {"HTTPS_EXIT_OBSERVED"},
            }
            if (event["status"] == "passed" and event["code"] not in permitted_passes.get(event["stage"], set())
                    or event["stage"] == "dns" and event["status"] not in {"insufficient_evidence", "not_tested"}):
                raise DiagnosticsError("DIAGNOSTIC_INVALID_OUTPUT")
            stage = job.public["stages"][STAGES.index(event["stage"])]
            stage.update(status=event["status"], code=event["code"], observed_at=self._wall())

    def _execute(self, job, payload):
        state, code, exit_ip = "failed", "DIAGNOSTIC_WORKER_FAILED", None
        try:
            with tempfile.TemporaryDirectory(prefix="vui-diagnostic-") as directory:
                # TemporaryDirectory is private (0700); the worker writes its
                # one 0600 config inside. Parent cleanup also covers forced kill.
                result = self._runner(payload, job.started + TIMEOUT_SECONDS,
                                      job.cancel, lambda event: self._emit(job, event), directory)
                if (type(result) is not dict or set(result) != {"code", "exit_ip"}
                        or type(result["code"]) is not str or result["code"] not in ERROR_CODES):
                    raise DiagnosticsError("DIAGNOSTIC_INVALID_OUTPUT")
                code = result["code"]
                if code == "DIAGNOSTIC_COMPLETE":
                    value = result["exit_ip"]
                    try:
                        if type(value) is not str or "%" in value or str(ipaddress.ip_address(value)) != value:
                            raise ValueError()
                    except ValueError:
                        raise DiagnosticsError("DIAGNOSTIC_INVALID_OUTPUT") from None
                    required = {row["stage"]: row for row in job.public["stages"]}
                    if (any(required[name]["status"] != "passed" for name in
                            ("configuration", "tcp", "tls_or_reality", "proxy_forwarding", "https_exit"))
                            or required["dns"]["status"] != "insufficient_evidence"
                            or required["dns"]["code"] != "DNS_PATH_BOUND_TO_HTTPS"):
                        raise DiagnosticsError("DIAGNOSTIC_INVALID_OUTPUT")
                    exit_ip, state = value, "completed"
                elif result["exit_ip"] is not None:
                    raise DiagnosticsError("DIAGNOSTIC_INVALID_OUTPUT")
        except DiagnosticsError as error:
            code = error.code
        except BaseException:
            # Raw OS, TLS, core and fixture failures can contain private data.
            code = "DIAGNOSTIC_WORKER_FAILED"
        finally:
            payload.clear()
            with self._lock:
                if job.cancel.is_set():
                    state, code, exit_ip = "cancelled", "DIAGNOSTIC_CANCELLED", None
                elif self._clock() >= job.started + TIMEOUT_SECONDS:
                    state, code, exit_ip = "failed", "DIAGNOSTIC_TIMEOUT", None
                now = self._wall()
                job.public.update(state=state, code=code, exit_ip=exit_ip, finished_at=now,
                                  expires_at=now + RESULT_TTL_SECONDS)
                job.done.set()
                self._admission.release(job.owner)

    def _owned(self, admin_id, job_id):
        owner = _admin_key(admin_id)
        if type(job_id) is not str or not _JOB_ID.fullmatch(job_id):
            raise DiagnosticsError("DIAGNOSTIC_NOT_FOUND")
        job = self._jobs.get(job_id)
        if job is None or job.owner != owner:
            raise DiagnosticsError("DIAGNOSTIC_NOT_FOUND")
        return job

    def get(self, admin_id, job_id):
        with self._lock:
            self._prune()
            job = self._owned(admin_id, job_id)
            value = copy.deepcopy(job.public)
        # A saved edit invalidates evidence even when the original worker is
        # still running. Return no green observations under a different draft.
        current = self.store.read_snapshot()["revision"]
        value["stale"] = current != value["revision"]
        expired = self._wall() >= value["expires_at"]
        if expired:
            value["state"] = "expired"
        if value["stale"] or expired:
            for stage in value["stages"]:
                if stage["status"] != "not_tested":
                    stage["status"] = "expired"
        return value

    def cancel(self, admin_id, job_id):
        with self._lock:
            job = self._owned(admin_id, job_id)
            if not job.done.is_set():
                job.cancel.set()
        # Only report terminal cancellation after the worker and its children
        # have been cleaned up. While cleanup runs, get() honestly says running.
        job.done.wait(timeout=2)
        return self.get(admin_id, job_id)

    def close(self):
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
            for job in jobs:
                if not job.done.is_set():
                    job.cancel.set()
        for job in jobs:
            job.done.wait(timeout=2)


def _stop_process(process, deadline=None):
    """Graceful worker cleanup first; group kill/reap on timeout and all exits."""
    if process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            wait = 0.75 if deadline is None else min(0.75, max(0, deadline - time.monotonic()))
            process.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()
    for pipe in (process.stdin, process.stdout):
        if pipe is not None:
            pipe.close()


def _run_worker(payload, deadline, cancelled, emit, directory):
    from app.services.core_manager import BIN_DIR
    request = {**payload, "binary": str((BIN_DIR / "sing-box").resolve())}
    raw = json.dumps(request, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(raw) > MAX_INPUT_BYTES:
        raise DiagnosticsError("INVALID_DIAGNOSTIC_REQUEST")
    command = [sys.executable, "-I", "-B", str(Path(__file__).with_name("proxy_diagnostic_worker.py")),
               str(os.getpid()), directory]
    process, result = None, None
    execution_deadline = deadline - 0.8  # Reserve termination/reaping inside the total 30 seconds.
    try:
        if cancelled.is_set():
            raise DiagnosticsError("DIAGNOSTIC_CANCELLED")
        if time.monotonic() >= execution_deadline:
            raise DiagnosticsError("DIAGNOSTIC_TIMEOUT")
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        pending, buffer, total = memoryview(raw), bytearray(), 0
        with selectors.DefaultSelector() as selector:
            for pipe, event in ((process.stdin, selectors.EVENT_WRITE), (process.stdout, selectors.EVENT_READ)):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, event)
            while selector.get_map():
                if cancelled.is_set():
                    raise DiagnosticsError("DIAGNOSTIC_CANCELLED")
                remaining = execution_deadline - time.monotonic()
                if remaining <= 0:
                    raise DiagnosticsError("DIAGNOSTIC_TIMEOUT")
                for key, events in selector.select(min(remaining, 0.05)):
                    pipe = key.fileobj
                    if events & selectors.EVENT_WRITE:
                        count = os.write(pipe.fileno(), pending)
                        pending = pending[count:]
                        if not pending:
                            selector.unregister(pipe)
                            pipe.close()
                    else:
                        chunk = os.read(pipe.fileno(), min(4096, MAX_OUTPUT_BYTES + 1 - total))
                        if not chunk:
                            selector.unregister(pipe)
                            pipe.close()
                            continue
                        total += len(chunk)
                        if total > MAX_OUTPUT_BYTES:
                            raise DiagnosticsError("DIAGNOSTIC_OUTPUT_LIMIT")
                        buffer.extend(chunk)
                        while b"\n" in buffer:
                            line, _, buffer = buffer.partition(b"\n")
                            try:
                                event = json.loads(line, object_pairs_hook=_strict_object)
                            except (ValueError, UnicodeError, RecursionError):
                                raise DiagnosticsError("DIAGNOSTIC_INVALID_OUTPUT") from None
                            if result is not None:
                                raise DiagnosticsError("DIAGNOSTIC_INVALID_OUTPUT")
                            if type(event) is dict and event.get("event") == "result":
                                if set(event) != {"event", "code", "exit_ip"}:
                                    raise DiagnosticsError("DIAGNOSTIC_INVALID_OUTPUT")
                                result = {key: event[key] for key in ("code", "exit_ip")}
                            else:
                                emit(event)
            if buffer or result is None:
                raise DiagnosticsError("DIAGNOSTIC_INVALID_OUTPUT")
        # Startup, I/O and process exit all share the original task deadline.
        while process.poll() is None:
            if cancelled.is_set():
                raise DiagnosticsError("DIAGNOSTIC_CANCELLED")
            if time.monotonic() >= execution_deadline:
                raise DiagnosticsError("DIAGNOSTIC_TIMEOUT")
            cancelled.wait(0.02)
        if process.returncode:
            raise DiagnosticsError("DIAGNOSTIC_WORKER_FAILED")
        return result
    except OSError:
        raise DiagnosticsError("DIAGNOSTIC_UNAVAILABLE") from None
    finally:
        if process is not None:
            _stop_process(process, deadline)
