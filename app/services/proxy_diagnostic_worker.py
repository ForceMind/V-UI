"""One private diagnostic worker, fixed catalog, no application runtime changes.

All descendants inherit its process group. SIGTERM (including panel death)
cleans and reaps them; the panel enforces the startup-inclusive 30 s deadline,
then kills the group if graceful cleanup is unavailable. No core output escapes.
"""
from __future__ import annotations

import base64
import ctypes
import http.client
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import socket
import ssl
import subprocess
import sys
import time

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.proxy_diagnostics import (
    MAX_INPUT_BYTES, MAX_OUTPUT_BYTES, TARGET_HOST, DiagnosticsError, _strict_object,
)
from app.services.proxy_policy import (
    AddressPolicy, DNS_TAG, LOCAL_TAG, UPSTREAM_TAG, VerifiedEndpoint, compile_proxy_policy,
)
from app.services.proxy_resolution import _decode_worker_output, _panel_host, self_address_snapshot
from app.services.upstream_profile import UpstreamProfile

_CHILDREN = []


class _Cancelled(BaseException):
    pass


def _stage(stage, status, code):
    _emit({"event": "stage", "stage": stage, "status": status, "code": code})


def _emit(event):
    print(json.dumps(event, ensure_ascii=True, separators=(",", ":"), allow_nan=False), flush=True)


def _spawn(command, **kwargs):
    # No shell, no new session: the panel can kill this entire tree atomically.
    # This helper runs in the single-threaded isolated worker. PDEATHSIG also
    # covers an abrupt worker death between Popen and registering the child.
    # Never install a preexec callback in the multithreaded panel process.
    parent = os.getpid()
    def child_parent_death():
        if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
            os._exit(70)
    if __name__ == "__main__":
        kwargs["preexec_fn"] = child_parent_death
    child = subprocess.Popen(command, stderr=subprocess.DEVNULL, **kwargs)
    _CHILDREN.append(child)
    return child


def _reap(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            child.kill()
    child.wait()
    for pipe in (child.stdin, child.stdout):
        if pipe is not None:
            pipe.close()
    if child in _CHILDREN:
        _CHILDREN.remove(child)


def _capture(command, raw=b"", *, timeout=3, maximum=4096):
    """Bound subprocess startup, pipe input/output and exit with one deadline."""
    deadline = time.monotonic() + timeout
    child = _spawn(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    pending, output = memoryview(raw), bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            for pipe, mode in ((child.stdin, selectors.EVENT_WRITE), (child.stdout, selectors.EVENT_READ)):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, mode)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DiagnosticsError("RESOLUTION_TIMEOUT")
                for key, events in selector.select(remaining):
                    pipe = key.fileobj
                    if events & selectors.EVENT_WRITE:
                        size = os.write(pipe.fileno(), pending) if pending else 0
                        pending = pending[size:]
                        if not pending:
                            selector.unregister(pipe)
                            pipe.close()
                    else:
                        chunk = os.read(pipe.fileno(), maximum + 1 - len(output))
                        if not chunk:
                            selector.unregister(pipe)
                            pipe.close()
                        else:
                            output.extend(chunk)
                            if len(output) > maximum:
                                raise DiagnosticsError("DIAGNOSTIC_OUTPUT_LIMIT")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DiagnosticsError("RESOLUTION_TIMEOUT")
            try:
                status = child.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise DiagnosticsError("RESOLUTION_TIMEOUT") from None
        return status, bytes(output)
    finally:
        _reap(child)


def _resolve(host):
    try:
        return (str(ipaddress.ip_address(host)),)
    except ValueError:
        pass
    # Reuse the M2 isolated resolver and decoder, but deliberately inherit this
    # worker's process group so cancellation cannot orphan a resolver session.
    command = [sys.executable, "-I", "-B", str(Path(__file__).with_name("proxy_resolution.py")), "--resolve-worker"]
    raw = json.dumps({"host": host}, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    status, output = _capture(command, raw, timeout=3)
    if status:
        raise DiagnosticsError("RESOLUTION_FAILED")
    try:
        return _decode_worker_output(output)
    except ValueError:
        raise DiagnosticsError("RESOLUTION_FAILED") from None


def _address_context(profile, profile_id, revision, panel_origin):
    try:
        own = self_address_snapshot(None)
        panel_host = _panel_host(panel_origin)
        if panel_host is not None:
            own = tuple(sorted(set((*own, *_resolve(panel_host)))))
    except DiagnosticsError:
        raise
    except Exception:
        raise DiagnosticsError("SELF_ADDRESS_UNAVAILABLE") from None
    try:
        addresses = AddressPolicy().check_addresses(_resolve(profile.server), self_addresses=own)
    except DiagnosticsError:
        raise
    except Exception:
        raise DiagnosticsError("ADDRESS_CHECK_FAILED") from None
    return own, VerifiedEndpoint(profile_id, revision, profile.server, addresses, int(time.time()))


def _diagnostic_config(profile, profile_id, revision, endpoint, own, port, username, password):
    snapshot = {"revision": revision, "upstreams": {profile_id: profile.to_dict()}, "local_password": password,
                "settings": {"schema_version": 1, "local_proxy_enabled": True, "relay_enabled": True,
                    "selected_upstream_id": profile_id, "local_port": port, "local_username": username,
                    "local_password_ref": "ephemeral-only", "dns_policy_version": 1, "deny_policy_version": 1}}
    # No ordinary ingress, ordinary resolver or direct outbound exists here.
    base = {"log": {"disabled": True}, "inbounds": [], "outbounds": [],
            "dns": {"servers": []}, "route": {}}
    config = compile_proxy_policy(base, [], snapshot, endpoint=endpoint, self_addresses=own).config
    config["dns"]["final"] = DNS_TAG
    config["route"]["final"] = UPSTREAM_TAG
    # Restrict the listener itself to the fixed domain/port *before* DNS. The
    # inherited M2 resolve/any-address guard then checks and dials the same set.
    # Literal targets, alternate hosts and caller-selected probes are forbidden.
    config["route"]["rules"][:0] = [
        {"domain": [TARGET_HOST], "invert": True, "action": "reject"},
        {"port": [443], "invert": True, "action": "reject"},
    ]
    return config


def _write_config(directory, config):
    path = Path(directory) / "diagnostic.json"
    raw = json.dumps(config, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(raw) > MAX_OUTPUT_BYTES:
        raise DiagnosticsError("DIAGNOSTIC_RESOURCE_LIMIT")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    return path


def _validate_core(binary, config_path):
    path = Path(binary)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise DiagnosticsError("CORE_UNAVAILABLE")
    try:
        status, output = _capture([binary, "version"], timeout=3)
    except DiagnosticsError:
        raise DiagnosticsError("CORE_UNAVAILABLE") from None
    first = output.splitlines()[0] if output else b""
    if status or first.split()[:3] != [b"sing-box", b"version", b"1.14.2"]:
        raise DiagnosticsError("CORE_VERSION_MISMATCH")
    child = _spawn([binary, "check", "-c", str(config_path)], stdout=subprocess.DEVNULL)
    try:
        if child.wait(timeout=4):
            raise DiagnosticsError("CORE_CONFIG_REJECTED")
    except subprocess.TimeoutExpired:
        raise DiagnosticsError("CORE_CONFIG_REJECTED") from None
    finally:
        _reap(child)


def _tcp_tls(profile, address):
    try:
        with socket.create_connection((address, profile.server_port), timeout=3) as connection:
            _stage("tcp", "passed", "TCP_CONNECTED")
            if profile.tls.mode == "reality":
                # A vanilla TLS response from the camouflage site is expressly
                # not evidence of REALITY authentication.
                _stage("tls_or_reality", "insufficient_evidence", "REALITY_REQUIRES_PROXY_EVIDENCE")
                return
            try:
                context = ssl.create_default_context()
                if profile.tls.alpn:
                    context.set_alpn_protocols(list(profile.tls.alpn))
                with context.wrap_socket(connection, server_hostname=profile.tls.server_name):
                    _stage("tls_or_reality", "passed", "TLS_CERTIFICATE_VERIFIED")
            except (OSError, ssl.SSLError):
                _stage("tls_or_reality", "failed", "TLS_FAILED")
                raise DiagnosticsError("TLS_FAILED") from None
    except DiagnosticsError:
        raise
    except OSError:
        _stage("tcp", "failed", "TCP_FAILED")
        raise DiagnosticsError("TCP_FAILED") from None


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _owns_listener(child, port):
    try:
        import psutil
        if child.poll() is not None:
            return False
        return any(connection.family == socket.AF_INET and connection.type == socket.SOCK_STREAM
                   and connection.status == psutil.CONN_LISTEN and connection.laddr
                   and connection.laddr.ip == "127.0.0.1" and connection.laddr.port == port
                   for connection in psutil.Process(child.pid).net_connections(kind="inet"))
    except Exception:
        return False


def _start_core(binary, config_path, port):
    child = _spawn([binary, "run", "-c", str(config_path)], stdout=subprocess.DEVNULL)
    deadline = time.monotonic() + 4
    # A competing process can accept arbitrary proxy credentials. Readiness
    # therefore proves the *core PID* owns this exact loopback TCP listener.
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise DiagnosticsError("CORE_START_FAILED")
        if _owns_listener(child, port):
            return child
        time.sleep(0.03)
    raise DiagnosticsError("CORE_START_FAILED")


def _connect_proxy(port, username, password, *, _target_port=443):
    # The keyword is an internal loopback-test seam for unprivileged fixture
    # ports. No request, environment or profile field is forwarded to it.
    connection = socket.create_connection(("127.0.0.1", port), timeout=7)
    try:
        auth = base64.b64encode((username + ":" + password).encode("ascii")).decode("ascii")
        request = (f"CONNECT {TARGET_HOST}:{_target_port} HTTP/1.1\r\nHost: {TARGET_HOST}:{_target_port}\r\n"
                   f"Proxy-Authorization: Basic {auth}\r\n\r\n").encode("ascii")
        connection.sendall(request)
        header = bytearray()
        while not header.endswith(b"\r\n\r\n"):
            value = connection.recv(1)
            if not value or len(header) >= 4096:
                raise DiagnosticsError("PROXY_REQUEST_FAILED")
            header.extend(value)
        first = header.split(b"\r\n", 1)[0].split(b" ")
        if len(first) < 2 or first[0] not in (b"HTTP/1.0", b"HTTP/1.1") or first[1] != b"200":
            raise DiagnosticsError("PROXY_REQUEST_FAILED")
        return connection
    except BaseException:
        connection.close()
        raise


class _CappedReader(io.RawIOBase):
    def __init__(self, raw):
        self.raw, self.count = raw, 0

    def readable(self):
        return True

    def readinto(self, buffer):
        limit = min(len(buffer), MAX_OUTPUT_BYTES + 1 - self.count)
        count = self.raw.readinto(memoryview(buffer)[:limit])
        if count is not None:
            self.count += count
            if self.count > MAX_OUTPUT_BYTES:
                raise DiagnosticsError("HTTP_RESPONSE_LIMIT")
        return count

    def close(self):
        try:
            self.raw.close()
        finally:
            super().close()


class _HTTPView:
    def __init__(self, connection):
        self.connection = connection

    def makefile(self, _mode):
        return io.BufferedReader(_CappedReader(self.connection.makefile("rb", buffering=0)), buffer_size=4096)


def _read_exit(connection):
    try:
        with http.client.HTTPResponse(_HTTPView(connection)) as response:
            response.begin()
            if response.status != 200:
                raise DiagnosticsError("HTTP_STATUS_REJECTED")
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise DiagnosticsError("HTTP_RESPONSE_INVALID")
            lengths = response.headers.get_all("Content-Length", [])
            transfers = response.headers.get_all("Transfer-Encoding", [])
            if (len(lengths) > 1 or len(transfers) > 1 or lengths and transfers
                    or lengths and not re.fullmatch(r"[0-9]+", lengths[0])
                    or transfers and transfers[0].lower() != "chunked"):
                raise DiagnosticsError("HTTP_RESPONSE_INVALID")
            if lengths and int(lengths[0]) > MAX_OUTPUT_BYTES:
                raise DiagnosticsError("HTTP_RESPONSE_LIMIT")
            body = response.read(MAX_OUTPUT_BYTES + 1)
            if len(body) > MAX_OUTPUT_BYTES:
                raise DiagnosticsError("HTTP_RESPONSE_LIMIT")
            # read(amt) does not raise for a short declared Content-Length.
            if lengths and len(body) != int(lengths[0]):
                raise DiagnosticsError("HTTP_RESPONSE_INVALID")
        value = json.loads(body, object_pairs_hook=_strict_object)
        if type(value) is not dict or set(value) != {"ip"} or type(value["ip"]) is not str or "%" in value["ip"]:
            raise ValueError()
        return str(ipaddress.ip_address(value["ip"]))
    except DiagnosticsError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise DiagnosticsError("EXIT_IP_INVALID") from None
    except (http.client.HTTPException, OSError):
        raise DiagnosticsError("HTTP_RESPONSE_INVALID") from None


def _https_exit(port, username, password):
    try:
        connection = _connect_proxy(port, username, password)
        try:
            context = ssl.create_default_context()
            context.set_alpn_protocols(["http/1.1"])
            with context.wrap_socket(connection, server_hostname=TARGET_HOST) as secure:
                secure.sendall((f"GET /?format=json HTTP/1.1\r\nHost: {TARGET_HOST}\r\n"
                                "Accept: application/json\r\nConnection: close\r\n\r\n").encode("ascii"))
                return _read_exit(secure)
        finally:
            connection.close()
    except DiagnosticsError:
        raise
    except (OSError, ssl.SSLError):
        raise DiagnosticsError("HTTPS_FAILED") from None


def _run(request, directory):
    if (type(request) is not dict or set(request) != {"profile", "profile_id", "revision", "panel_origin", "binary"}
            or type(request["profile_id"]) is not str or not re.fullmatch(r"up_[0-9a-f]{32}", request["profile_id"])
            or type(request["revision"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", request["revision"])
            or type(request["binary"]) is not str):
        raise DiagnosticsError("INVALID_DIAGNOSTIC_REQUEST")
    profile = UpstreamProfile.from_dict(request["profile"])
    own, endpoint = _address_context(profile, request["profile_id"], request["revision"], request["panel_origin"])
    _emit({"event": "endpoint", "address": endpoint.addresses[0], "resolved_at": endpoint.resolved_at})
    username, password, port = "diagnostic", secrets.token_urlsafe(32), _free_port()
    config = _diagnostic_config(profile, request["profile_id"], request["revision"], endpoint, own, port, username, password)
    path = _write_config(directory, config)
    try:
        _validate_core(request["binary"], path)
    except DiagnosticsError as error:
        _stage("configuration", "failed", error.code)
        raise
    _stage("configuration", "passed", "CORE_CONFIG_VALID")
    _tcp_tls(profile, endpoint.addresses[0])
    core = _start_core(request["binary"], path, port)
    try:
        exit_ip = _https_exit(port, username, password)
        if not _owns_listener(core, port):
            raise DiagnosticsError("CORE_START_FAILED")
    except DiagnosticsError as error:
        _stage("https_exit", "failed", error.code)
        _stage("proxy_forwarding", "insufficient_evidence", "PROXY_REQUEST_FAILED")
        _stage("dns", "insufficient_evidence", "DNS_PATH_UNPROVEN")
        raise
    _stage("proxy_forwarding", "passed", "PROXY_FORWARDING_PROVEN_BY_HTTPS")
    if profile.tls.mode == "reality":
        _stage("tls_or_reality", "passed", "REALITY_AUTHENTICATED_BY_HTTPS")
    _stage("https_exit", "passed", "HTTPS_EXIT_OBSERVED")
    # This observes the business request, not independent complete A/AAAA query
    # results. Do not turn fixed-policy provenance into a green DNS measurement.
    _stage("dns", "insufficient_evidence", "DNS_PATH_BOUND_TO_HTTPS")
    return exit_ip


def _install_limits(parent):
    import resource
    # No AS cap: the pinned Go core reserves large virtual address ranges.
    # Bound CPU, files/descriptors and crash output; wall time is parent-owned.
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    if not sys.platform.startswith("linux") or ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise DiagnosticsError("DIAGNOSTIC_RESOURCE_LIMIT")
    if os.getppid() != parent:
        raise _Cancelled()


def main():
    if len(sys.argv) != 3:
        return 64
    code, exit_ip = "DIAGNOSTIC_WORKER_FAILED", None
    def stop(*_):
        raise _Cancelled()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        _install_limits(int(sys.argv[1]))
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise DiagnosticsError("INVALID_DIAGNOSTIC_REQUEST")
        request = json.loads(raw, object_pairs_hook=_strict_object)
        exit_ip = _run(request, sys.argv[2])
        code = "DIAGNOSTIC_COMPLETE"
    except _Cancelled:
        code = "DIAGNOSTIC_CANCELLED"
    except DiagnosticsError as error:
        code = error.code
    except BaseException:
        # Do not stringify untrusted profile, resolver, TLS or core exceptions.
        code = "DIAGNOSTIC_WORKER_FAILED"
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        for child in list(reversed(_CHILDREN)):
            _reap(child)
    _emit({"event": "result", "code": code, "exit_ip": exit_ip})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
