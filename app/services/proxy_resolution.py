"""Bounded system bootstrap for explicit proxy checks/applications only.

This is not a business-DNS resolver. Only an already validated upstream host
or the configured panel-origin host reaches the child. No profile, credentials,
resolver override, DNS setting, or environment bypass is sent to it. Runtime
configuration consumes the checked addresses without another name lookup.
"""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

# Portable bundles retain ordinary CPython and app/ sources. An absolute script
# path works with -I regardless of the service cwd, PYTHONPATH, or shell setup.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.upstream_profile import UpstreamProfile, UpstreamValidationError, server_host

RESOLUTION_TIMEOUT_SECONDS = 3
RESOLVER_MEMORY_BYTES = 128 * 1024 * 1024
MAX_ADDRESSES = 32
MAX_WORKER_INPUT_BYTES = 512
MAX_WORKER_OUTPUT_BYTES = 4096
MAX_CONCURRENT_RESOLVERS = 2
_RESOLVER_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_RESOLVERS)
_MESSAGES = {
    "RESOLVER_BUSY": "Two address resolvers are already running; retry shortly",
    "RESOLUTION_TIMEOUT": "Address resolution exceeded its execution deadline",
    "RESOLVER_UNAVAILABLE": "The isolated address resolver could not start or communicate",
    "RESOLVER_LIMIT_UNAVAILABLE": "Required address-resolver resource limits are unavailable",
    "RESOLVER_RESOURCE_LIMIT": "The isolated address resolver exceeded its resource limits",
    "RESOLUTION_FAILED": "The system resolver could not resolve the configured host",
    "INVALID_RESOLUTION": "The isolated address resolver returned an invalid result",
    "TOO_MANY_ADDRESSES": "Address resolution exceeded the 32-address limit",
    "SELF_ADDRESS_UNAVAILABLE": "A complete local-address snapshot is required",
    "INVALID_PANEL_ORIGIN": "The configured panel origin is invalid",
    "INVALID_RESOLUTION_CONTEXT": "A saved upstream and its exact revision are required",
}
_WORKER_ERROR_CODES = frozenset({"RESOLVER_LIMIT_UNAVAILABLE", "RESOLVER_RESOURCE_LIMIT",
                               "RESOLUTION_FAILED", "INVALID_RESOLUTION", "TOO_MANY_ADDRESSES"})


class ResolutionError(ValueError):
    """Controlled, credential-free errors; never stringify resolver exceptions."""

    def __init__(self, code):
        self.code = code
        super().__init__(_MESSAGES[code])


def _fail(code):
    raise ResolutionError(code) from None


def _address(value):
    if type(value) is not str or len(value) > 45 or "%" in value:
        _fail("INVALID_RESOLUTION")
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        _fail("INVALID_RESOLUTION")


def _ordered(addresses):
    return tuple(str(address) for address in sorted(set(addresses), key=lambda ip: (ip.version, int(ip))))


def _bounded_worker_io(command, raw):
    """Drain bounded IPC under one wall deadline, including startup and exit.

    Cancellation (including BaseException) kills and reaps the child before the
    caller releases its global slot. The helper starts its own process group;
    no preexec_fn is used in the potentially multithreaded panel process.
    """
    if type(raw) is not bytes or len(raw) > MAX_WORKER_INPUT_BYTES:
        _fail("INVALID_RESOLUTION")
    process = None
    completed = False
    deadline = time.monotonic() + RESOLUTION_TIMEOUT_SECONDS
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        output = bytearray()
        remaining = memoryview(raw)
        with selectors.DefaultSelector() as selector:
            for pipe, events in ((process.stdin, selectors.EVENT_WRITE), (process.stdout, selectors.EVENT_READ)):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, events)
            while selector.get_map():
                wait = deadline - time.monotonic()
                if wait <= 0:
                    _fail("RESOLUTION_TIMEOUT")
                for key, events in selector.select(wait):
                    pipe = key.fileobj
                    if events & selectors.EVENT_WRITE:
                        try:
                            count = os.write(pipe.fileno(), remaining) if remaining else 0
                        except BrokenPipeError:
                            _fail("INVALID_RESOLUTION")
                        remaining = remaining[count:]
                        if not remaining:
                            selector.unregister(pipe)
                            pipe.close()
                    else:
                        chunk = os.read(pipe.fileno(), MAX_WORKER_OUTPUT_BYTES + 1 - len(output))
                        if not chunk:
                            selector.unregister(pipe)
                            pipe.close()
                        else:
                            output.extend(chunk)
                            if len(output) > MAX_WORKER_OUTPUT_BYTES:
                                _fail("RESOLVER_RESOURCE_LIMIT")
            wait = deadline - time.monotonic()
            if wait <= 0:
                _fail("RESOLUTION_TIMEOUT")
            try:
                returncode = process.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                _fail("RESOLUTION_TIMEOUT")
        if returncode:
            _fail("RESOLVER_RESOURCE_LIMIT")
        completed = True
        return bytes(output)
    except OSError:
        _fail("RESOLVER_UNAVAILABLE")
    finally:
        if process is not None:
            # Kill the group even when its leader exited with a pipe still open.
            try:
                if not completed or process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            finally:
                process.wait()
                for pipe in (process.stdin, process.stdout):
                    if pipe is not None:
                        pipe.close()


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _decode_worker_output(output):
    if type(output) is not bytes or not output or len(output) > MAX_WORKER_OUTPUT_BYTES:
        _fail("INVALID_RESOLUTION")
    try:
        result = json.loads(output, object_pairs_hook=_strict_object)
        if type(result) is not dict or type(result.get("ok")) is not bool:
            raise ValueError()
        if not result["ok"]:
            if set(result) != {"ok", "code"} or type(result["code"]) is not str or result["code"] not in _WORKER_ERROR_CODES:
                raise ValueError()
            _fail(result["code"])
        if set(result) != {"ok", "addresses"} or type(result["addresses"]) is not list:
            raise ValueError()
        values = result["addresses"]
        if not 1 <= len(values) <= MAX_ADDRESSES:
            _fail("TOO_MANY_ADDRESSES" if len(values) > MAX_ADDRESSES else "INVALID_RESOLUTION")
        addresses = [_address(value) for value in values]
        if any(str(address) != value for address, value in zip(addresses, values)) or len(set(addresses)) != len(addresses):
            raise ValueError()
        return _ordered(addresses)
    except ResolutionError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("INVALID_RESOLUTION")


def _resolve_host(host):
    """Resolve only a validated configured host, never a user business target."""
    try:
        server_host(host)
    except UpstreamValidationError:
        _fail("INVALID_RESOLUTION_CONTEXT")
    try:
        return (str(ipaddress.ip_address(host)),)
    except ValueError:
        pass
    if not _RESOLVER_SLOTS.acquire(blocking=False):
        _fail("RESOLVER_BUSY")
    try:
        command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--resolve-worker"]
        # Host goes over bounded stdin, never argv; no other profile fields leave
        # this process. Child errors contain only controlled codes.
        raw = json.dumps({"host": host}, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        return _decode_worker_output(_bounded_worker_io(command, raw))
    finally:
        _RESOLVER_SLOTS.release()


def _self_addresses(values):
    if type(values) not in (tuple, list, set, frozenset) or not values or len(values) > 4096:
        _fail("SELF_ADDRESS_UNAVAILABLE")
    try:
        addresses = [_address(value) for value in values]
    except ResolutionError:
        _fail("SELF_ADDRESS_UNAVAILABLE")
    # A wildcard alone is not evidence that actual interfaces were enumerated.
    addresses = [address for address in addresses if not address.is_unspecified]
    if not addresses:
        _fail("SELF_ADDRESS_UNAVAILABLE")
    return _ordered(addresses)


def resolve_upstream(profile_id, revision, profile, *, self_addresses):
    """Produce one immutable, revision-bound set of checked upstream addresses.

    Check *all* answers, including literal IPs. A forbidden or self answer
    rejects the entire endpoint; callers must pin addresses[0] and preserve
    profile.tls.server_name. This function never mutates or returns credentials.
    """
    from app.services.proxy_policy import AddressPolicy, VerifiedEndpoint

    if (type(profile_id) is not str or not re.fullmatch(r"up_[0-9a-f]{32}", profile_id)
            or type(revision) is not str or not re.fullmatch(r"[0-9a-f]{64}", revision)):
        _fail("INVALID_RESOLUTION_CONTEXT")
    try:
        if type(profile) is dict:
            profile = UpstreamProfile.from_dict(profile)
        if type(profile) is not UpstreamProfile:
            _fail("INVALID_RESOLUTION_CONTEXT")
    except UpstreamValidationError:
        _fail("INVALID_RESOLUTION_CONTEXT")
    own_addresses = _self_addresses(self_addresses)
    addresses = AddressPolicy().check_addresses(_resolve_host(profile.server), self_addresses=own_addresses)
    return VerifiedEndpoint(profile_id=profile_id, revision=revision, host=profile.server,
                            addresses=addresses, resolved_at=int(time.time()))


def _panel_host(panel_origin):
    if panel_origin is None or panel_origin == "":
        return None
    if type(panel_origin) is not str or len(panel_origin) > 2048 or any(ord(char) < 33 or ord(char) > 126 for char in panel_origin):
        _fail("INVALID_PANEL_ORIGIN")
    try:
        parsed = urlsplit(panel_origin)
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username is not None
                or parsed.password is not None or parsed.path or "?" in panel_origin or "#" in panel_origin
                or parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError()
        host = server_host(parsed.hostname)
        # urlsplit tolerates empty ports and text after a bracketed IP. Require
        # the complete authority to be exactly the host and optional port.
        authority = f"[{host}]" if ":" in host else host
        if parsed.port is not None:
            authority += f":{parsed.port}"
        if parsed.netloc.lower() != authority.lower():
            raise ValueError()
        return host
    except (ValueError, UpstreamValidationError):
        _fail("INVALID_PANEL_ORIGIN")


def self_address_snapshot(panel_origin=None):
    """Enumerate actual local IPs plus every configured panel-endpoint answer.

    Private, loopback, link-local and public interface IPs all belong in this
    snapshot. An interface zone is local metadata and is removed from IPv6;
    resolver-supplied scope IDs remain forbidden. Enumeration or panel DNS
    failure is fatal, rather than silently omitting a self-protection layer.
    """
    host = _panel_host(panel_origin)
    try:
        import psutil
        interfaces = psutil.net_if_addrs()
        if type(interfaces) is not dict:
            _fail("SELF_ADDRESS_UNAVAILABLE")
        addresses = []
        for entries in interfaces.values():
            for entry in entries:
                if entry.family not in (socket.AF_INET, socket.AF_INET6):
                    continue
                value = entry.address
                if type(value) is not str:
                    _fail("SELF_ADDRESS_UNAVAILABLE")
                if "%" in value:
                    if entry.family != socket.AF_INET6 or value.count("%") != 1 or not value.split("%", 1)[1]:
                        _fail("SELF_ADDRESS_UNAVAILABLE")
                    value = value.split("%", 1)[0]
                address = _address(value)
                if address.version != (4 if entry.family == socket.AF_INET else 6):
                    _fail("SELF_ADDRESS_UNAVAILABLE")
                if not address.is_unspecified:
                    addresses.append(str(address))
        snapshot = _self_addresses(addresses)
    except Exception:
        # OS, psutil and interface errors can contain host information. Preserve
        # cancellation, but never return a partial snapshot or raw error text.
        raise ResolutionError("SELF_ADDRESS_UNAVAILABLE") from None
    if host is not None:
        snapshot = _ordered([_address(value) for value in (*snapshot, *_resolve_host(host))])
    return snapshot


def _set_worker_limits():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (RESOLVER_MEMORY_BYTES, RESOLVER_MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (RESOLUTION_TIMEOUT_SECONDS, RESOLUTION_TIMEOUT_SECONDS))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, OSError, ValueError):
        _fail("RESOLVER_LIMIT_UNAVAILABLE")


def _system_addresses(host):
    answers = socket.getaddrinfo(host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM,
                                 proto=socket.IPPROTO_TCP, flags=0)
    addresses = set()
    for answer in answers:
        if type(answer) is not tuple or len(answer) != 5:
            _fail("INVALID_RESOLUTION")
        family, socktype, proto, _, sockaddr = answer
        if (family not in (socket.AF_INET, socket.AF_INET6) or socktype != socket.SOCK_STREAM
                or proto != socket.IPPROTO_TCP or type(sockaddr) is not tuple
                or len(sockaddr) != (2 if family == socket.AF_INET else 4)):
            _fail("INVALID_RESOLUTION")
        if family == socket.AF_INET6 and sockaddr[3] != 0:
            _fail("INVALID_RESOLUTION")
        address = _address(sockaddr[0])
        if address.version != (4 if family == socket.AF_INET else 6):
            _fail("INVALID_RESOLUTION")
        addresses.add(address)
        if len(addresses) > MAX_ADDRESSES:
            _fail("TOO_MANY_ADDRESSES")
    if not addresses:
        _fail("RESOLUTION_FAILED")
    return _ordered(addresses)


def _worker_main():
    try:
        _set_worker_limits()
        raw = sys.stdin.buffer.read(MAX_WORKER_INPUT_BYTES + 1)
        if len(raw) > MAX_WORKER_INPUT_BYTES:
            _fail("INVALID_RESOLUTION")
        value = json.loads(raw, object_pairs_hook=_strict_object)
        if type(value) is not dict or set(value) != {"host"}:
            _fail("INVALID_RESOLUTION")
        host = server_host(value["host"])
        result = {"ok": True, "addresses": _system_addresses(host)}
    except ResolutionError as error:
        result = {"ok": False, "code": error.code}
    except (MemoryError, RecursionError):
        result = {"ok": False, "code": "RESOLVER_RESOURCE_LIMIT"}
    except Exception:
        result = {"ok": False, "code": "RESOLUTION_FAILED"}
    sys.stdout.write(json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "--resolve-worker":
        raise SystemExit(64)
    raise SystemExit(_worker_main())
