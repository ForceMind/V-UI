"""Bounded UDP fixtures: loopback only, exact wire/peer accounting, no host changes.

These characterize small datagrams at unprivileged ports. They do not implement
an Internet DNS resolver, fragmentation, TUN, UDP/53 or UDP/443 qualification.
"""
from collections import Counter
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import ipaddress
import socket
import socketserver
import ssl
import struct
import threading
import time

from loopback_helpers import _recv_exact, _socks_address_from_stream, _socks_udp_target
from proxy_loopback_support import (
    serve, ReadyMihomoProcess, await_mihomo_ready, fresh_lines,
)

CLIENT = "127.0.0.1"
SERVER = "127.0.0.2"
TARGET = "127.0.0.3"
EGRESS = "127.0.0.4"


def attach_failure_context(error, label, collect):
    """Diagnostics must never replace the failure being diagnosed."""
    try:
        error.add_note(label + collect())
    except Exception as diagnostic_error:
        try:
            error.add_note(label + "collection failed: " + type(diagnostic_error).__name__)
        except Exception:
            pass  # Even a custom exception's broken add_note keeps its identity.


class LifecycleTrace:
    """In-memory diagnostics, emitted only if the enclosing fixture fails."""
    def __init__(self):
        self.lock = threading.Lock()
        self.events = []

    def record(self, event, **fields):
        with self.lock:
            self.events.append({"monotonic_ns": time.monotonic_ns(), "event": event, **fields})

    def snapshot(self):
        with self.lock:
            return list(self.events)


def recording_https_server(stack, host, handler, cert, key, accounting, *, trace):
    """The existing tracked TLS fixture semantics plus handshake/close reasons.

    Used only while constructing this independent UDP fixture's HTTPS/DoH
    servers. Keep pre-handshake accounting, bounded sockets and final drain.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    context.set_alpn_protocols(["http/1.1"])

    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET

        def get_request(self):
            stream, address = super().get_request()
            stream.settimeout(3)
            token = accounting.opened(stream)
            trace.record("tls.accepted", endpoint=self.server_address, peer=address, token=token)
            return stream, address

        def process_request_thread(self, request, address):
            token, stream, stage = id(request), request, "wrap"
            try:
                stream = accounting.start_tls(token, context)
                stage = "handshake"
                stream.do_handshake()
                trace.record("tls.established", endpoint=self.server_address, peer=address, token=token)
                stage = "http"
                self.finish_request(stream, address)
            except (OSError, ValueError) as exc:
                trace.record("tls.error", endpoint=self.server_address, peer=address, token=token,
                             stage=stage, error_type=type(exc).__name__, reason=str(exc))
            finally:
                try:
                    self.shutdown_request(stream)
                finally:
                    accounting.closed(token)
                    trace.record("tls.closed", endpoint=self.server_address, peer=address, token=token, stage=stage)

        def close_tracked_connections(self):
            deadline = time.monotonic() + 3
            while accounting.connection_count():
                accounting.close_connections()
                if time.monotonic() >= deadline:
                    raise AssertionError("Accepted fixture connections did not drain at close")
                time.sleep(.025)

    return serve(stack, Server((host, 0), handler))


def paired_port(host):
    """Find one currently free TCP/UDP port without modifying host networking."""
    for _ in range(20):
        with socket.socket() as tcp, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            tcp.bind((host, 0))
            port = tcp.getsockname()[1]
            try:
                udp.bind((host, port))
            except OSError:
                continue
            return port
    raise AssertionError("Could not reserve a paired fixture TCP/UDP port")


def dns_response(packet):
    """Strict single-question fixture, deterministic A/AAAA/TXT wire response."""
    if len(packet) < 17 or struct.unpack("!HHHHH", packet[2:12]) != (0x100, 1, 0, 0, 0):
        raise ValueError("Expected one uncompressed fixture DNS question")
    pos = 12
    while True:
        if pos >= len(packet):
            raise ValueError("Truncated DNS name")
        length = packet[pos]
        pos += 1
        if not length:
            break
        if length > 63 or pos + length >= len(packet):
            raise ValueError("Invalid DNS label")
        packet[pos:pos + length].decode("ascii")
        pos += length
    if pos + 4 != len(packet):
        raise ValueError("Unexpected DNS question length")
    kind, klass = struct.unpack("!HH", packet[pos:])
    if klass != 1 or kind not in (1, 28, 16):
        raise ValueError("Unsupported fixture DNS question")
    value = {1: socket.inet_aton("192.0.2.17"),
             28: socket.inet_pton(socket.AF_INET6, "2001:db8::17"),
             16: b"\x0bVUI-UDP-DNS"}[kind]
    return (packet[:2] + struct.pack("!HHHHH", 0x8180, 1, 1, 0, 0) + packet[12:]
            + b"\xc0\x0c" + struct.pack("!HHIH", kind, 1, 0, len(value)) + value)


class DatagramTarget:
    """Synchronous handler plus lifetime allowlist, audited after shutdown.

    Authorize expected data before sending it. Never absorb observed packets as
    a new baseline: even a delayed negative after a later positive must fail.
    """
    def __init__(self, responder=lambda packet: packet, host=TARGET):
        self.responder = responder
        self.lock = threading.Lock()
        self.received = []
        self.expected = Counter()
        self.errors = []
        self.closed = False
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                packet, stream = self.request
                with owner.lock:
                    owner.received.append((self.client_address[0], packet))
                try:
                    response = owner.responder(packet)
                    stream.sendto(response, self.client_address)
                except Exception as exc:
                    with owner.lock:
                        owner.errors.append(repr(exc))

        self.server = socketserver.UDPServer((host, 0), Handler)
        self.server.socket.settimeout(.25)
        self.host, self.port = self.server.server_address
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()

    def expect(self, peer, packet):
        self.expected[(peer, packet)] += 1

    def audit(self, *, complete=True):
        with self.lock:
            received, errors = Counter(self.received), list(self.errors)
        if errors:
            raise AssertionError("UDP fixture errors: " + repr(errors))
        if received - self.expected:
            raise AssertionError("Unexpected or late UDP delivery: " + repr(received - self.expected))
        if complete and received != self.expected:
            raise AssertionError("Expected UDP delivery is missing: " + repr(self.expected - received))

    def drain(self, timeout=2, quiet=.2):
        deadline = time.monotonic() + timeout
        last, quiet_since = None, None
        while True:
            self.audit()
            with self.lock:
                snapshot = tuple(self.received)
            now = time.monotonic()
            if snapshot != last:
                last, quiet_since = snapshot, now
            if now - quiet_since >= quiet:
                return
            if now >= deadline:
                raise AssertionError("UDP evidence did not drain within its deadline")
            time.sleep(.01)

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.server.shutdown()
        finally:
            self.server.server_close()
            self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise AssertionError("Synchronous UDP fixture failed to join")
        self.audit()

    def direct(self, payload):
        self.expect(CLIENT, payload)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
            stream.bind((CLIENT, 0))
            stream.settimeout(2)
            stream.sendto(payload, (self.host, self.port))
            reply, peer = stream.recvfrom(65535)
        if peer != (self.host, self.port) or reply != self.responder(payload):
            raise AssertionError("Direct control wire or peer mismatch")
        self.audit()


def decode_socks_datagram(packet):
    if len(packet) < 4 or packet[:3] != b"\0\0\0":
        raise AssertionError("Invalid or fragmented SOCKS UDP response")
    kind, pos = packet[3], 4
    if kind in (1, 4):
        size = 4 if kind == 1 else 16
        if len(packet) < pos + size + 2:
            raise AssertionError("Truncated SOCKS UDP address")
        host = str(ipaddress.ip_address(packet[pos:pos + size]))
        pos += size
    elif kind == 3:
        if len(packet) <= pos:
            raise AssertionError("Truncated SOCKS UDP domain")
        size, pos = packet[pos], pos + 1
        if not size or len(packet) < pos + size + 2:
            raise AssertionError("Truncated SOCKS UDP domain")
        host = packet[pos:pos + size].decode("ascii")
        pos += size
    else:
        raise AssertionError("Unsupported SOCKS UDP address")
    port = struct.unpack("!H", packet[pos:pos + 2])[0]
    return host, port, packet[pos + 2:]


class SocksDatagrams:
    """One live SOCKS association; every response includes verified identity."""
    def __init__(self, port, *, auth=None):
        self.stack = ExitStack()
        try:
            control = self.stack.enter_context(socket.create_connection((CLIENT, port), timeout=3))
            control.settimeout(3)
            method = b"\x02" if auth else b"\x00"
            control.sendall(b"\x05\x01" + method)
            if _recv_exact(control, 2) != b"\x05" + method:
                raise AssertionError("SOCKS authentication negotiation failed")
            if auth:
                user, password = (part.encode("ascii") for part in auth)
                control.sendall(b"\x01" + bytes([len(user)]) + user + bytes([len(password)]) + password)
                if _recv_exact(control, 2) != b"\x01\x00":
                    raise AssertionError("SOCKS username/password rejected")
            control.sendall(b"\x05\x03\x00\x01" + b"\x00" * 6)
            reply = _recv_exact(control, 4)
            if reply[:3] != b"\x05\x00\x00":
                raise AssertionError("SOCKS UDP association failed")
            host, port = _socks_address_from_stream(control, reply[3])
            if host in ("0.0.0.0", "::"):
                host = CLIENT
            if host != CLIENT:
                raise AssertionError("SOCKS relay escaped the loopback fixture")
            self.relay = host, port
            self.stream = self.stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
            self.stream.bind((CLIENT, 0))
            self.sent = []
        except BaseException:
            self.stack.close()
            raise

    def close(self):
        self.stack.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def send(self, host, port, payload):
        packet = b"\x00\x00\x00" + _socks_udp_target(host, port) + payload
        if self.stream.sendto(packet, self.relay) != len(packet):
            raise AssertionError("Incomplete UDP fixture send")
        # A failed negotiation or send never creates rejection evidence.
        self.sent.append((host, port, payload))

    def receive(self, host, port, timeout=3):
        self.stream.settimeout(timeout)
        response, relay = self.stream.recvfrom(65535)
        if relay != self.relay:
            raise AssertionError("UDP response came from a different SOCKS relay")
        target_host, target_port, payload = decode_socks_datagram(response)
        if (target_host, target_port) != (host, port):
            raise AssertionError("SOCKS response destination identity mismatch")
        return payload


def isolated_rejection_offset(path, reasons):
    """Take a checkpoint only after association setup, before its one send.

    D06/D07 start new cores and a new log for every datagram. Refuse accidental
    reuse of a log that already has an earlier UDP rejection. A prior partial
    line is still discarded by fresh_lines if completed after this checkpoint.
    """
    text = path.read_text(errors="replace")
    if any(all(word in line for word in reasons) for line in text.lower().splitlines()):
        raise AssertionError("Isolated core log already contains another request's rejection")
    return len(text)


def observe_rejection(association, target, log_path, offset, reasons, *, window=1.0):
    """Observe the entire window after one send, with fresh reasons and no data.

    A caller timeout is merely absence of a response. When reasons are supplied,
    a matching new core line is independently mandatory. Empty reasons are only
    used with an independently proven stopped server process/socket.
    """
    sent = getattr(association, "sent", [])
    if len(sent) != 1 or sent[0][:2] != (target.host, target.port):
        raise AssertionError("Rejection observation requires exactly one sent datagram for this target")
    deadline = time.monotonic() + window
    matched = False
    while time.monotonic() < deadline:
        target.audit()
        if reasons:
            matched |= any(all(word in line for word in reasons) for line in fresh_lines(log_path, offset))
        association.stream.settimeout(min(.025, max(.001, deadline - time.monotonic())))
        try:
            response = association.stream.recv(65535)
        except (socket.timeout, ConnectionRefusedError):
            pass
        else:
            raise AssertionError("Rejected UDP returned data: " + repr(response))
    target.audit()
    if reasons and not matched:
        raise AssertionError("No fresh actual core rejection: " + repr(reasons)
                             + "\n" + "\n".join(fresh_lines(log_path, offset)))
