"""Bounded double-hop fixtures. Fake credentials, loopback, temporary trust only.

These helpers never install a CA, change DNS/routes/firewalls, or contact a public
service. Source-bound egress lets the application and DoH record which hop really
made each connection rather than inferring routing from a successful response.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from collections import Counter
from http import client as http_client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import socket
import socketserver
import ssl
import struct
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit

from loopback_helpers import (
    CoreProcess, _recv_exact, _socks_address_from_stream, _socks_udp_target,
    question,
)

INGRESS = "127.0.0.1"
UPSTREAM = "127.0.0.2"
TARGET = "127.0.0.3"
UPSTREAM_EGRESS = "127.0.0.4"
DIRECT_EGRESS = "127.0.0.5"
DOH = "127.0.0.6"
DENIED = "127.0.0.7"
SNI = "vpn.example.test"
BODY = b"VUI-VERIFIED-DOUBLE-HOP"


class ProxyRejected(OSError):
    """A wire-level proxy refusal, not an application response or timeout."""


def listen_port(host=INGRESS):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def serve(stack, server):
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": .02}, daemon=True)
    thread.start()
    def close():
        server.shutdown()
        try:
            if hasattr(server, "close_tracked_connections"):
                server.close_tracked_connections()
        finally:
            server.server_close()
            thread.join(timeout=2)
            if thread.is_alive():
                raise AssertionError("Fixture server thread did not exit at close")
    stack.callback(close)
    return server.server_address[1]


class ConnectionAccounting:
    """Register accepted sockets before TLS or HTTP parsing/thread dispatch."""
    def opened(self, stream):
        with self.lock:
            token = id(stream)
            self.connections[token] = stream
            return token

    def start_tls(self, token, context):
        # Swap under the same lock used by close_connections. A raw socket
        # cannot be detached into an untracked SSL socket during cleanup.
        with self.lock:
            stream = context.wrap_socket(self.connections[token], server_side=True,
                                         do_handshake_on_connect=False)
            self.connections[token] = stream
            return stream

    def closed(self, token):
        with self.lock:
            self.connections.pop(token, None)

    def close_connections(self):
        with self.lock:
            for stream in self.connections.values():
                try:
                    stream.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                stream.close()

    def connection_count(self):
        with self.lock:
            return len(self.connections)


def tracked_https_server(stack, host, handler, cert, key, accounting):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    context.set_alpn_protocols(["http/1.1"])

    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET

        def get_request(self):
            stream, address = super().get_request()
            stream.settimeout(3)
            accounting.opened(stream)  # Before process_request starts a thread.
            return stream, address

        def process_request_thread(self, request, address):
            token = id(request)
            stream = request
            try:
                stream = accounting.start_tls(token, context)
                stream.do_handshake()
                self.finish_request(stream, address)
            except (OSError, ValueError):
                # Closed/truncated/deadline fixture peers are expected. The
                # evidence audit still checks every parsed application/DNS item.
                pass
            finally:
                try:
                    self.shutdown_request(stream)
                finally:
                    accounting.closed(token)

        def close_tracked_connections(self):
            deadline = time.monotonic() + 3
            while accounting.connection_count():
                accounting.close_connections()
                if time.monotonic() >= deadline:
                    raise AssertionError("Accepted fixture connections did not drain at close")
                time.sleep(.025)

    return serve(stack, Server((host, 0), handler))


class Traffic(ConnectionAccounting):
    def __init__(self):
        self.lock = threading.Lock()
        self.connections = {}
        self.deliveries = []
        self.hold_started = threading.Event()
        self.release_hold = threading.Event()
        self.active = 0

    @contextmanager
    def processing(self):
        with self.lock:
            self.active += 1
        try:
            yield
        finally:
            with self.lock:
                self.active -= 1

    def pending(self):
        with self.lock:
            return self.active + len(self.connections)

    def append(self, peer, host, path):
        with self.lock:
            self.deliveries.append((peer, host, path))

    def snapshot(self):
        with self.lock:
            return list(self.deliveries)


def https_target(stack, host, cert, key):
    traffic = Traffic()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            with traffic.processing():
                traffic.append(self.client_address[0], self.headers.get("Host"), self.path)
                self.send_response(200)
                self.send_header("Content-Length", str(len(BODY) * (2 if self.path.startswith("/hold-") else 1)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(BODY)
                self.wfile.flush()
                if self.path.startswith("/hold-"):
                    traffic.hold_started.set()
                    traffic.release_hold.wait(timeout=15)
                    try:
                        self.wfile.write(BODY)
                    except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                        pass
    return tracked_https_server(stack, host, Handler, cert, key, traffic), traffic

class BusinessAnswers(ConnectionAccounting):
    """Wire DNS with multiple answers, deliberate failure and rebinding fixtures."""
    def __init__(self):
        self.lock = threading.Lock()
        self.connections = {}
        self.queries = []
        self.records = {}
        self.sequences = {}
        self.counts = {}
        self.failed = set()
        self.delays = {}
        self.active = 0

    def pending(self):
        with self.lock:
            return self.active + len(self.connections)

    def answer(self, packet, peer):
        with self.lock:
            self.active += 1
        try:
            name, kind, end = question(packet)
            with self.lock:
                self.queries.append((peer, name, kind))
                key = name, kind
                count = self.counts.get(key, 0)
                self.counts[key] = count + 1
                sequence = self.sequences.get(name)
                values = sequence[min(count, len(sequence)-1)] if sequence else self.records.get(name, (TARGET,))
                failed = name in self.failed or (name, kind) in self.failed
                delay = self.delays.get((name, kind), 0)
            if delay:
                time.sleep(min(delay, 12))  # Deliberate DNS deadline fixture, bounded.
            records = []
            if not failed:
                for value in values:
                    address = ipaddress.ip_address(value)
                    if kind == (1 if address.version == 4 else 28):
                        raw = address.packed
                        records.append(b"\xc0\x0c" + struct.pack("!HHIH", kind, 1, 0, len(raw)) + raw)
            return (packet[:2] + struct.pack("!HHHHH", 0x8182 if failed else 0x8180,
                                            1, len(records), 0, 0) + packet[12:end] + b"".join(records))
        finally:
            with self.lock:
                self.active -= 1

    def snapshot(self):
        with self.lock:
            return list(self.queries)


def doh_target(stack, answers, cert, key):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args):
            pass
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65535:
                self.send_error(400)
                return
            self.respond(self.rfile.read(length))
        def do_GET(self):
            encoded = parse_qs(urlsplit(self.path).query).get("dns", [""])[0]
            try:
                self.respond(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            except (ValueError, IndexError):
                self.send_error(400)
        def respond(self, packet):
            if urlsplit(self.path).path != "/dns-query":
                self.send_error(404)
                return
            data = answers.answer(packet, self.client_address[0])
            self.send_response(200)
            self.send_header("Content-Type", "application/dns-message")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                # Intentional deadline fixtures close their HTTP stream first.
                pass
    return tracked_https_server(stack, DOH, Handler, cert, key, answers)

def socks_auth(stream, auth):
    stream.sendall(b"\x05\x01" + (b"\x02" if auth else b"\x00"))
    if _recv_exact(stream, 2) != (b"\x05\x02" if auth else b"\x05\x00"):
        raise ProxyRejected("SOCKS authentication method rejected")
    if auth:
        user, password = (part.encode("ascii") for part in auth)
        stream.sendall(b"\x01" + bytes([len(user)]) + user + bytes([len(password)]) + password)
        if _recv_exact(stream, 2) != b"\x01\x00":
            raise ProxyRejected("SOCKS username/password rejected")


@contextmanager
def tunnel(port, host, target_port, *, protocol="connect", auth=None, timeout=3):
    stream = socket.create_connection((INGRESS, port), timeout=timeout)
    try:
        stream.settimeout(timeout)
        if protocol == "socks5h":
            socks_auth(stream, auth)
            stream.sendall(b"\x05\x01\x00" + _socks_udp_target(host, target_port))
            reply = _recv_exact(stream, 4)
            if reply[:3] != b"\x05\x00\x00":
                raise ProxyRejected("SOCKS CONNECT rejected with status " + str(reply[1]))
            _socks_address_from_stream(stream, reply[3])
        else:
            authority = f"[{host}]:{target_port}" if ":" in host else f"{host}:{target_port}"
            credentials = ""
            if auth:
                encoded = base64.b64encode(":".join(auth).encode("ascii")).decode("ascii")
                credentials = f"Proxy-Authorization: Basic {encoded}\r\n"
            stream.sendall((f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n" + credentials + "\r\n").encode("ascii"))
            response = b""
            while not response.endswith(b"\r\n\r\n"):
                if len(response) > 16384:
                    raise ProxyRejected("Oversized CONNECT response")
                response += _recv_exact(stream, 1)
            first_line = response.partition(b"\r\n")[0]
            if first_line.split()[1] != b"200":
                raise ProxyRejected("HTTP CONNECT rejected: " + first_line.decode("ascii", "replace"))
        yield stream
    finally:
        stream.close()


def https_through(port, host, target_port, ca, marker, *, protocol="connect", auth=None, timeout=3):
    with tunnel(port, host, target_port, protocol=protocol, auth=auth, timeout=timeout) as stream:
        # SNI is deliberately independent of the SOCKS/CONNECT destination. The
        # same temporary certificate is verified, never insecure=True.
        context = ssl.create_default_context(cafile=str(ca))
        with context.wrap_socket(stream, server_hostname=SNI) as tls:
            tls.sendall(f"GET /{marker} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode("ascii"))
            response = http_client.HTTPResponse(tls)
            response.begin()
            return response.status, response.read()


def https_control(host, port, ca, marker):
    with socket.create_connection((host, port), timeout=3) as stream:
        context = ssl.create_default_context(cafile=str(ca))
        with context.wrap_socket(stream, server_hostname=SNI) as tls:
            tls.sendall(f"GET /{marker} HTTP/1.1\r\nHost: {SNI}\r\nConnection: close\r\n\r\n".encode("ascii"))
            response = http_client.HTTPResponse(tls)
            response.begin()
            return response.status, response.read()


def udp_through(port, host, target_port, payload, *, auth=None):
    with socket.create_connection((INGRESS, port), timeout=3) as control:
        control.settimeout(3)
        socks_auth(control, auth)
        control.sendall(b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00")
        reply = _recv_exact(control, 4)
        if reply[:3] != b"\x05\x00\x00":
            raise ProxyRejected("SOCKS UDP associate rejected")
        relay_host, relay_port = _socks_address_from_stream(control, reply[3])
        if relay_host in ("0.0.0.0", "::"):
            relay_host = INGRESS
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
            stream.settimeout(2)
            stream.sendto(b"\x00\x00\x00" + _socks_udp_target(host, target_port) + payload,
                          (relay_host, relay_port))
            return stream.recv(65535)


def udp_target(stack):
    packets = []
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            payload, stream = self.request
            packets.append((self.client_address[0], payload))
            stream.sendto(b"VUI-UDP-CONTROL:" + payload, self.client_address)
    # A synchronous bounded handler cannot outlive serve_forever/shutdown and
    # append a datagram after the final evidence audit.
    server = socketserver.UDPServer((TARGET, 0), Handler)
    server.socket.settimeout(1)
    return serve(stack, server), packets


def ordinary_dns_target(stack, answers):
    """Joined synchronous trap; no daemon DNS worker can append after close."""
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            packet, transport = self.request
            try:
                transport.sendto(answers.answer(packet), self.client_address)
            except (OSError, ValueError, IndexError, UnicodeError):
                pass
    server = socketserver.UDPServer((INGRESS, 0), Handler)
    server.socket.settimeout(1)
    return serve(stack, server)


class DeliveryEvidence:
    """Lifetime authorized deliveries; a late negative can never become baseline."""
    def __init__(self, answers, ordinary):
        self.targets = {}
        self.dns = Counter()
        self.answers, self.ordinary = answers, ordinary
        self.udp = []

    def expect(self, traffic, peer, host, path):
        self.targets.setdefault(traffic, Counter())[(peer, host, path)] += 1

    def expect_dns(self, host):
        self.dns.update(((UPSTREAM_EGRESS, host, 1), (UPSTREAM_EGRESS, host, 28)))

    def target_baseline(self):
        return {traffic: expected.copy() for traffic, expected in self.targets.items()}

    def assert_targets(self, case, baseline=None):
        expected_targets = self.targets if baseline is None else baseline
        for traffic, expected in expected_targets.items():
            case.assertEqual(Counter(traffic.snapshot()), expected,
                             "Unexpected, duplicate, late, or DIRECT application delivery")
        for packets, expected in self.udp:
            case.assertEqual(Counter(packets), expected, "Unexpected or late application UDP delivery")

    def assert_exact(self, case):
        self.assert_targets(case)
        case.assertEqual(Counter(self.answers.snapshot()), self.dns,
                         "Business DNS count/source/name differs from explicitly requested lookups")
        case.assertEqual(self.ordinary.queries, [], "Protected traffic escaped to ordinary DNS")

    def pending(self):
        return self.answers.pending() + sum(traffic.pending() for traffic in self.targets)


class BoundCoreProcess(CoreProcess):
    """The existing core lifecycle, but probes the explicitly selected address."""
    def start(self, port, host=INGRESS):
        if self.process is not None and self.process.poll() is None:
            raise AssertionError("Refusing to overlap two processes for one fixture")
        self.process = subprocess.Popen(self.command, stdout=self.log,
                                        stderr=subprocess.STDOUT, env=self.env)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError(self.log_path.read_text(errors="replace"))
            try:
                with socket.create_connection((host, port), timeout=.1):
                    return
            except OSError:
                time.sleep(.03)
        raise AssertionError("Core listener not ready: " + self.log_path.read_text(errors="replace"))
