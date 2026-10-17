"""Bounded real encrypted VMess/XUDP and shared-mux TCP compatibility probes.

These are new experiments, not replacements for any original assertion.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib
import socket
import struct
import threading
import time

import common as c

VMESS = {"compat_vmess_aes128gcm": "aes-128-gcm", "compat_vmess_chacha20": "chacha20-poly1305"}
TCP = "compat_shared_tcp_multiframe"
CASES = (*VMESS, TCP)
PAYLOAD_SIZES = (1, 8192, 32768, 60000)
TCP_BYTES = 3 * 65535 + 137


def payload(size, seed):
    block = hashlib.sha256(seed.encode()).digest()
    return (block * ((size + len(block) - 1) // len(block)))[:size]


def vmess_configs(f, security):
    c.need(security in VMESS.values(), "Unexpected VMess cipher")
    domestic = f.guarded_config()
    domestic["inbounds"][0]["type"] = "vmess"
    domestic["inbounds"][0]["users"][0].pop("flow")
    client = f.client_config("singbox")
    node = client["outbounds"][0]
    node["type"] = "vmess"
    node.pop("flow")
    # Explicit security is essential: sing-box auto + TLS selects zero.
    # packet_encoding=xudp selects VMess CommandMux; multiplex=true does not.
    node.update(security=security, alter_id=0, global_padding=True, authenticated_length=True)
    c.need(node["packet_encoding"] == "xudp" and "multiplex" not in node,
           "Compatibility probe no longer targets VMess CommandMux")
    return domestic, client


class LargeDatagramTarget:
    """Independent 65,535-byte recorder; frozen UDPServer caps reads at 8,192.

    Match the lifetime ledger interface without altering the original helper.
    The old empty target remains in the fixture and is still audited/closed.
    """
    def __init__(self, host):
        self.host, self.closed = host, False
        self.received, self.expected, self.errors = [], Counter(), []
        self.stream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.stream.bind((host, 0))
        self.port = self.stream.getsockname()[1]
        self.stream.settimeout(.1)
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.thread = threading.Thread(target=self.run, name="mux-runtime-large-udp", daemon=False)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try:
                body, peer = self.stream.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as exc:
                if not self.stop.is_set():
                    self.errors.append(str(exc))
                return
            with self.lock:
                self.received.append((peer[0], body))
            try:
                self.stream.sendto(body, peer)
            except OSError as exc:
                self.errors.append(str(exc))

    def expect(self, peer, body):
        self.expected[(peer, body)] += 1

    def audit(self):
        with self.lock:
            c.need(not self.errors and Counter(self.received) == self.expected,
                   "Large UDP target lifetime ledger mismatch")

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop.set()
        self.stream.close()
        self.thread.join(timeout=2)
        c.need(not self.thread.is_alive(), "Large UDP recorder survived cleanup")


def vmess_case(f, module, events, security):
    helper = importlib.import_module("udp_dns_capability_helpers")
    target = LargeDatagramTarget(module.TARGET)
    f.targets.append(target)
    c.need(target.port != f.upstream_port, "Large UDP target collided with upstream TCP port")
    f.target = target
    domestic, client = vmess_configs(f, security)
    f.start("singbox", domestic=domestic, client=client)
    events.event("encrypted_mux_configuration", security=security, alter_id=0,
                 global_padding=True, authenticated_length=True, packet_encoding="xudp",
                 verified_tls=True, official_device="sing-box", official_overseas="sing-box",
                 source_path="VMess ListenPacket -> DialEarlyXUDPPacketConn -> CommandMux -> HandleMuxConnection")
    with helper.SocksDatagrams(f.client_port) as association:
        for index, size in enumerate(PAYLOAD_SIZES):
            body = payload(size, security + str(index))
            f.target.expect(module.UPSTREAM_EGRESS, body)
            events.event("encrypted_mux_send", index=index, requested_host=f.target.host,
                         requested_port=f.target.port, payload=c.observer.payload_identity(body))
            association.send(f.target.host, f.target.port, body)
            association.stream.settimeout(4)
            response, relay = association.stream.recvfrom(65535)
            c.need(relay == association.relay, "VMess reply came from the wrong SOCKS relay")
            host, port, received = helper.decode_socks_datagram(response)
            c.need((host, port, received) == (f.target.host, f.target.port, body),
                   "Encrypted/chunked VMess changed reply address, port or payload")
            f.audit()
            events.event("encrypted_mux_reply", index=index, host=host, port=port,
                         payload=c.observer.payload_identity(received))
    c.need(len(f.target.received) == len(PAYLOAD_SIZES), "Wrong encrypted-mux delivery cardinality")
    f.observations.append({"kind": "encrypted-vmess-xudp", "security": security,
                           "payload_sizes": list(PAYLOAD_SIZES), "verified_tls": True})


def exact(stream, size):
    result = bytearray()
    while len(result) < size:
        part = stream.recv(size - len(result))
        c.need(part, "Truncated TCP application stream")
        result.extend(part)
    return bytes(result)


class TcpTarget:
    """At most two application connections, with joined workers and exact ledgers."""
    def __init__(self, host, peer, bodies):
        self.peer, self.bodies = peer, bodies
        self.received, self.errors, self.workers, self.streams = [], [], [], set()
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.listener = socket.socket()
        self.listener.bind((host, 0))
        self.listener.listen(2)
        self.listener.settimeout(.1)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.accept, name="mux-runtime-tcp-target", daemon=False)
        self.thread.start()

    def accept(self):
        while not self.stop.is_set():
            try:
                stream, address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError as exc:
                if not self.stop.is_set():
                    self.errors.append(str(exc))
                return
            with self.lock:
                if len(self.workers) >= 2:
                    self.errors.append("Unexpected third application connection")
                    stream.close()
                    continue
                self.streams.add(stream)
                worker = threading.Thread(target=self.handle, args=(stream, address), daemon=False)
                self.workers.append(worker)
            worker.start()

    def handle(self, stream, address):
        try:
            with stream:
                stream.settimeout(5)
                size = struct.unpack("!I", exact(stream, 4))[0]
                c.need(size == TCP_BYTES, "Unexpected TCP application length")
                body = exact(stream, size)
                with self.lock:
                    self.received.append((address[0], body))
                c.need(address[0] == self.peer and body in self.bodies, "TCP direct bypass or altered payload")
                stream.sendall(body)
                # EOF creates the actual mux End; no synthetic client End is used.
        except Exception as exc:
            with self.lock:
                self.errors.append(str(exc))
        finally:
            with self.lock:
                self.streams.discard(stream)

    def audit(self):
        c.need(not self.errors, "TCP target errors: " + "; ".join(self.errors))
        c.need(Counter(self.received) == Counter((self.peer, body) for body in self.bodies),
               "TCP lifetime ledger mismatch, duplicate or missing application delivery")

    def close(self):
        self.stop.set()
        self.listener.close()
        self.thread.join(timeout=2)
        with self.lock:
            for stream in self.streams:
                try:
                    stream.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                stream.close()
        for worker in self.workers:
            worker.join(timeout=2)
        c.need(not self.thread.is_alive() and all(not worker.is_alive() for worker in self.workers),
               "TCP application worker survived cleanup")
        c.need(not self.streams, "TCP application socket survived cleanup")


class FrameReader:
    def __init__(self, decode_address):
        self.decode = decode_address
        self.buffer = bytearray()
        self.response_read = False
        self.total_frames = 0

    def feed(self, data):
        self.buffer.extend(data)
        c.need(len(self.buffer) <= 2 * 65535 + 2048, "Unbounded TCP mux response buffer")
        frames = []
        if not self.response_read:
            if len(self.buffer) < 2:
                return frames
            c.need(self.buffer[:2] == b"\0\0", "Wrong VLESS response header")
            del self.buffer[:2]
            self.response_read = True
        while len(self.buffer) >= 2:
            size = int.from_bytes(self.buffer[:2], "big")
            c.need(4 <= size <= 1024, "Invalid shared-mux response header length")
            if len(self.buffer) < 2 + size:
                break
            header = bytes(self.buffer[2:2 + size])
            sid, status, flags = int.from_bytes(header[:2], "big"), header[2], header[3]
            c.need(sid in (0, 1, 2) and status in (2, 3) and flags in (0, 1), "Unknown/error mux response")
            address = None
            if size > 4:
                c.need(sid == 2 and header[4] == 2, "Unexpected TCP response address")
                address = self.decode(header[5:])
            position, body = 2 + size, None
            if status == 3:
                c.need(size == 4 and flags == 0, "Malformed End")
            elif flags:
                if len(self.buffer) < position + 2:
                    break
                length = int.from_bytes(self.buffer[position:position + 2], "big")
                position += 2
                if len(self.buffer) < position + length:
                    break
                body = bytes(self.buffer[position:position + length])
                position += length
            del self.buffer[:position]
            frames.append((sid, status, address, body))
            self.total_frames += 1
            c.need(self.total_frames <= 256, "Too many TCP mux frames")
        return frames


def tcp_frame(module, sid, host, port, body, first):
    c.need(0 < len(body) <= 65535, "Invalid TCP frame payload size")
    header = struct.pack("!HBB", sid, 1 if first else 2, 1)
    if first:
        header += b"\x01" + module.xudp_address(host, port)
    return struct.pack("!H", len(header)) + header + struct.pack("!H", len(body)) + body


def tcp_case(f, module, events):
    bodies = [payload(TCP_BYTES, "tcp-session-" + str(sid)) for sid in (0, 1)]
    target = TcpTarget(module.TARGET, module.UPSTREAM_EGRESS, bodies)
    # Final ledger is checked after the fixture closes all cores, sockets and threads.
    def final_audit():
        target.audit()
        c.need(not target.thread.is_alive() and all(not worker.is_alive() for worker in target.workers)
               and not target.streams, "TCP lifetime audit lacks joined workers/sockets")
        events.event("tcp_lifetime_audit", connections=len(target.received), workers_joined=True,
                     deliveries=[{"peer": peer, "payload": c.observer.payload_identity(body)}
                                 for peer, body in target.received])
    f.stack.callback(final_audit)
    f.stack.callback(target.close)
    f.start("singbox", domestic=f.guarded_config())
    reader = FrameReader(module.decode_xudp_address)
    received, counts, ends = {0: bytearray(), 1: bytearray()}, Counter(), set()
    with module.RawXudpAssociation(f, module.TARGET, f.target.port) as raw:
        messages = [struct.pack("!I", len(body)) + body for body in bodies]
        for offset in range(0, len(messages[0]), 65535):
            for sid in (0, 1):
                chunk = messages[sid][offset:offset + 65535]
                raw.stream.sendall(tcp_frame(module, sid, module.TARGET, target.port, chunk, offset == 0))
        events.event("shared_tcp_requests_sent", sessions=2, payload_bytes=TCP_BYTES,
                     request_frames_per_session=4, verified_tls=True)
        deadline = time.monotonic() + 8
        while ends != {0, 1}:
            c.need(time.monotonic() < deadline, "TCP mux replies/Ends did not complete")
            raw.stream.settimeout(min(1, max(.01, deadline - time.monotonic())))
            try:
                data = raw.stream.recv(65536)
            except socket.timeout:
                continue
            c.need(data, "Shared TLS parent closed during TCP streams")
            for sid, status, address, body in reader.feed(data):
                c.need(sid in (0, 1), "Reply for unopened UDP sibling")
                c.need(sid not in ends, "Duplicate End or data after TCP End")
                if status == 3:
                    c.need(bytes(received[sid]) == bodies[sid], "TCP End preceded complete payload")
                    ends.add(sid)
                elif body is not None:
                    received[sid].extend(body)
                    counts[sid] += 1
                    c.need(len(received[sid]) <= TCP_BYTES, "Extra TCP reply bytes")
        target.audit()
        c.need(not reader.buffer and all(counts[sid] >= 4 for sid in (0, 1)),
               "TCP response did not demonstrate complete multi-frame delivery")
        for sid in (0, 1):
            events.event("tcp_stream_complete", session=sid, reply_frames=counts[sid],
                         exactly_one_end=True, payload=c.observer.payload_identity(bytes(received[sid])))
        body = b"shared-parent-udp-after-two-tcp-ends"
        f.target.expect(module.UPSTREAM_EGRESS, body)
        frame = module.xudp_frame(module.TARGET, f.target.port, body, first=True)
        raw.stream.sendall(frame[:2] + b"\0\x02" + frame[4:])
        deadline, replied = time.monotonic() + 4, False
        while not replied:
            c.need(time.monotonic() < deadline, "UDP sibling did not recover on the same TLS parent")
            raw.stream.settimeout(max(.01, deadline - time.monotonic()))
            data = raw.stream.recv(65536)
            c.need(data, "TLS parent closed after TCP Ends")
            for sid, status, address, response in reader.feed(data):
                c.need(sid == 2 and status == 2 and not replied, "Late TCP or extra UDP response")
                if response is not None:
                    c.need(address == (module.TARGET, f.target.port) and response == body,
                           "Same-parent UDP sibling address, port or payload changed")
                    replied = True
        c.need(not reader.buffer, "Partial trailing mux frame")
        f.audit()
        events.event("same_parent_udp_recovered", session=2, host=module.TARGET, port=f.target.port,
                     payload=c.observer.payload_identity(body))
    f.observations.append({"kind": "shared-mux-tcp-multiframe", "sessions": 2,
                           "payload_bytes_per_stream": TCP_BYTES, "udp_sibling_recovered": True})


def run_case(case, fixture, module, psutil_module, output):
    events = c.life.Events(output / "observations" / (case + ".json"), case)
    def exercise(f):
        try:
            if case in VMESS:
                vmess_case(f, module, events, VMESS[case])
            else:
                c.need(case == TCP, "Unknown compatibility case")
                tcp_case(f, module, events)
        finally:
            events.event("fixture_cleanup_begin")
    c.observer.run_case(case, fixture, psutil_module, events, exercise)
    events.data["compatibility_passed"] = True
    events.flush()
    return {key: value for key, value in events.data.items() if key != "events"}
