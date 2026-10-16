"""D2 preflight only: fixed-core, verified TLS/XUDP, IPv4 loopback fixtures.

No production compiler changes or qualification are implied by these tests.
"""
from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
import json
import ipaddress
import os
from pathlib import Path
import socket
import ssl
import struct
import uuid
import subprocess
import tempfile
import time

import yaml

from loopback_helpers import certificate_files, DnsAnswers, _recv_exact
from proxy_loopback_support import (
    INGRESS, UPSTREAM, TARGET, UPSTREAM_EGRESS, DIRECT_EGRESS, DOH, DENIED,
    SNI, BoundCoreProcess, ReadyMihomoProcess, listen_port, BusinessAnswers,
    doh_target, ordinary_dns_target, fresh_lines,
)
from udp_dns_capability_helpers import DatagramTarget, SocksDatagrams, paired_port, decode_socks_datagram
from app.services.proxy_policy import AddressPolicy

INGRESS_UUID = "11111111-1111-4111-8111-111111111111"
UPSTREAM_UUID = "22222222-2222-4222-8222-222222222222"
WRONG_UUID = "33333333-3333-4333-8333-333333333333"


class RestrictedUdpFixture:
    """Process lifecycle and exact target ledger, with no host-level changes."""
    def __init__(self, case, label):
        self.case, self.label = case, label
        self.stack = ExitStack()
        self.processes = []
        self.targets = []
        self.answers = self.ordinary_answers = None
        self.expected_dns = Counter()
        self.observations = []
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="vui-d2-")))
        self.ca, self.cert, self.key = certificate_files(self.root)
        self.wrong_ca, _, _ = certificate_files(self.root, "wrong")
        (self.root / "empty-roots").mkdir()
        self.env = {**os.environ, "SSL_CERT_FILE": str(self.ca),
                    "SSL_CERT_DIR": str(self.root / "empty-roots")}
        self.singbox = str(Path(os.environ["VUI_TEST_CORES"]) / "sing-box")
        self.mihomo = os.environ["VUI_TEST_MIHOMO"]
        self.upstream_port = listen_port(UPSTREAM)
        self.ingress_port = listen_port(INGRESS)
        self.client_port = paired_port(INGRESS)
        self.sequence = 0
        self.target = self.target_at(TARGET)
        self.upstream = self.domestic = self.client = None

    def target_at(self, host):
        for _ in range(20):
            target = DatagramTarget(host=host)
            if target.port != self.upstream_port:
                self.targets.append(target)
                return target
            # Before any request/process starts: accidental cross-host port
            # equality must not hide the fixed-core reply-port defect.
            target.close()
        raise AssertionError("Could not reserve target port distinct from upstream VLESS")

    def __enter__(self):
        return self

    def logs(self):
        return "\n".join(path.name + ":\n" + path.read_text(errors="replace")
                         for path in sorted(self.root.glob("*.log")))

    def safe(self, text):
        for value in (INGRESS_UUID, UPSTREAM_UUID, WRONG_UUID, str(self.root)):
            text = text.replace(value, "<fixture>")
        return text

    def audit(self):
        for target in self.targets:
            target.audit()
        if self.answers is not None:
            self.case.assertEqual(Counter(self.answers.snapshot()), self.expected_dns,
                                  "Unexpected, duplicate, direct, or late business DNS query")
            self.case.assertEqual(self.ordinary_answers.queries, [], "Protected DNS used ordinary fallback")
        for process in self.processes:
            if "domestic" in process.log_path.name:
                self.case.assertNotIn("outbound/direct[fixture-domestic-direct]",
                                      process.log_path.read_text(errors="replace").lower(),
                                      "Protected packet attempted DIRECT")

    def drain(self, timeout=5):
        deadline, quiet_since = time.monotonic() + timeout, None
        while True:
            self.audit()
            now = time.monotonic()
            pending = self.answers.pending() if self.answers is not None else 0
            if not pending:
                quiet_since = quiet_since or now
                if now - quiet_since >= .2:
                    return
            else:
                quiet_since = None
            if now >= deadline:
                raise AssertionError("D2 application/DNS drain exceeded its bound")
            time.sleep(.01)

    def __exit__(self, kind, error, traceback):
        cleanup_errors = []
        for process in reversed(self.processes):
            try:
                process.stop()
            except BaseException as problem:
                cleanup_errors.append(problem)
        try:
            self.drain()
        except BaseException as problem:
            cleanup_errors.append(problem)
        for target in self.targets:
            try:
                target.close()
            except BaseException as problem:
                cleanup_errors.append(problem)
        logs = self.safe(self.logs())
        try:
            self.stack.close()
            for target in self.targets:
                target.audit()  # Final audit after joined servers and closed sockets.
            if self.answers is not None:
                self.case.assertEqual(Counter(self.answers.snapshot()), self.expected_dns)
                self.case.assertEqual(self.ordinary_answers.queries, [])
        except BaseException as problem:
            cleanup_errors.append(problem)
        if cleanup_errors:
            if error is None:
                error = cleanup_errors.pop(0)
            for problem in cleanup_errors:
                error.add_note("D2 lifetime cleanup failed: " + str(problem))
        if error is not None:
            error.add_note(logs)
        directory = os.getenv("VUI_RESTRICTED_UDP_EVIDENCE")
        if directory:
            destination = Path(directory)
            destination.mkdir(parents=True, exist_ok=True)
            (destination / (self.label + ".log")).write_text(logs)
            (destination / (self.label + ".json")).write_text(json.dumps({
                "status": "passed" if error is None else "failed",
                "observations": self.observations,
                "deliveries": [{"host": target.host, "count": len(target.received)}
                               for target in self.targets]}, indent=2))
        if kind is None and error is not None:
            raise error
        return False

    def process(self, label, config, *, mihomo=False, env=None):
        self.sequence += 1
        prefix = self.root / f"{self.sequence:03d}-{label}"
        path = prefix.with_suffix(".yaml" if mihomo else ".json")
        path.write_text(yaml.safe_dump(config, sort_keys=False) if mihomo else json.dumps(config))
        path.chmod(0o600)
        if mihomo:
            home = prefix.with_suffix(".home")
            home.mkdir()
            command = [self.mihomo, "-d", str(home), "-f", str(path)]
            check = [self.mihomo, "-t", "-d", str(home), "-f", str(path)]
        else:
            command = [self.singbox, "run", "-c", str(path)]
            check = [self.singbox, "check", "-c", str(path)]
        result = subprocess.run(check, text=True, capture_output=True, timeout=15, env=env or self.env)
        self.case.assertEqual(result.returncode, 0, self.safe(result.stdout + result.stderr))
        process_class = ReadyMihomoProcess if mihomo else BoundCoreProcess
        process = process_class(self.stack, command, prefix.with_suffix(".log"), env or self.env)
        self.processes.append(process)
        return process

    def inbound(self, tag, host, port, uuid):
        return {"type": "vless", "tag": tag, "listen": host, "listen_port": port,
                "users": [{"uuid": uuid, "flow": ""}],
                "tls": {"enabled": True, "server_name": SNI,
                        "certificate_path": str(self.cert), "key_path": str(self.key)}}

    def upstream_config(self):
        return {"log": {"level": "debug", "timestamp": False},
                "inbounds": [self.inbound("fixture-overseas", UPSTREAM, self.upstream_port, UPSTREAM_UUID)],
                "outbounds": [{"type": "direct", "tag": "fixture-overseas-egress",
                               "inet4_bind_address": UPSTREAM_EGRESS}],
                "route": {"final": "fixture-overseas-egress"}}

    def domestic_config(self):
        return {"log": {"level": "debug", "timestamp": False},
                "inbounds": [self.inbound("fixture-domestic", INGRESS, self.ingress_port, INGRESS_UUID)],
                "outbounds": [{"type": "vless", "tag": "fixture-upstream", "server": UPSTREAM,
                               "server_port": self.upstream_port, "uuid": UPSTREAM_UUID,
                               "flow": "", "network": ["tcp", "udp"], "packet_encoding": "xudp",
                               "tls": {"enabled": True, "server_name": SNI}}],
                "route": {"final": "fixture-upstream"}}

    def client_config(self, kind):
        if kind == "mihomo":
            return {"mixed-port": self.client_port, "bind-address": INGRESS, "allow-lan": False,
                    "mode": "rule", "log-level": "debug", "ipv6": False,
                    "proxies": [{"name": "DOMESTIC_ONLY", "type": "vless", "server": INGRESS,
                                 "port": self.ingress_port, "uuid": INGRESS_UUID, "network": "tcp",
                                 "udp": True, "packet-encoding": "xudp", "flow": "",
                                 "tls": True, "servername": SNI, "skip-cert-verify": False}],
                    "rules": ["MATCH,DOMESTIC_ONLY"]}
        return {"log": {"level": "debug", "timestamp": False},
                "inbounds": [{"type": "mixed", "listen": INGRESS, "listen_port": self.client_port}],
                "outbounds": [{"type": "vless", "tag": "DOMESTIC_ONLY", "server": INGRESS,
                               "server_port": self.ingress_port, "uuid": INGRESS_UUID,
                               "flow": "", "network": ["tcp", "udp"], "packet_encoding": "xudp",
                               "tls": {"enabled": True, "server_name": SNI}}],
                "route": {"final": "DOMESTIC_ONLY"}}

    def start(self, kind="mihomo", *, domestic=None, client=None, domestic_env=None):
        self.upstream = self.process("overseas", self.upstream_config())
        self.upstream.start(self.upstream_port, UPSTREAM)
        self.domestic = self.process("domestic", domestic or self.domestic_config(), env=domestic_env)
        self.domestic.start(self.ingress_port, INGRESS)
        self.client = self.process("device-" + kind, client or self.client_config(kind), mihomo=kind == "mihomo")
        self.client.start(self.client_port, INGRESS)

    def echo(self, payload, *, host=None, target=None):
        target = target or self.target
        host = host or target.host
        self.case.assertEqual(ipaddress.ip_address(host).version, 4,
                              "Device UDP probes require literal IPv4; use raw XUDP for domains")
        target.expect(UPSTREAM_EGRESS, payload)
        with SocksDatagrams(self.client_port) as association:
            association.send(host, target.port, payload)
            association.stream.settimeout(4)
            response, relay = association.stream.recvfrom(65535)
            self.case.assertEqual(relay, association.relay)
            actual_host, actual_port, actual_payload = decode_socks_datagram(response)
            self.case.assertIn(actual_host, {host, target.host})
            self.case.assertEqual(actual_port, target.port)
            self.case.assertEqual(actual_payload, payload)
        target.audit()
        self.observations.append({"kind": "echo", "bytes": len(payload), "host": host, "peer": UPSTREAM_EGRESS})

    def guarded_config(self, *, udp_connect=True, udp_timeout="30s"):
        """Candidate shape only; this deliberately does not mutate production.

        Ordinary direct fallback is an observable trap. Protected UDP gets the
        same pre/post all-address guards, a pinned upstream and terminal reject.
        The UDP connected-socket option must separately pass retarget tests.
        """
        if self.answers is None:
            self.answers = BusinessAnswers()
            self.doh_port = doh_target(self.stack, self.answers, self.cert, self.key)
            self.ordinary_answers = DnsAnswers("D2_ORDINARY_DNS_TRAP")
            self.ordinary_port = ordinary_dns_target(self.stack, self.ordinary_answers)
        policy = AddressPolicy._for_tests(allowed_addresses=(TARGET, UPSTREAM, DOH),
                                         doh=(DOH, self.doh_port, SNI))
        denied = policy.denied_cidrs((INGRESS,), (UPSTREAM,))
        # IPv4 only: reject even public IPv6 in a mixed answer set.
        denied.append("::/0")
        guard = {"inbound": ["fixture-domestic"], "ip_cidr": denied, "action": "reject"}
        config = self.domestic_config()
        config["outbounds"].append({"type": "direct", "tag": "fixture-domestic-direct",
                                    "inet4_bind_address": DIRECT_EGRESS})
        dns = policy.dns_server()
        dns["tag"], dns["detour"] = "fixture-business-dns", "fixture-upstream"
        config["dns"] = {"servers": [
            {"type": "udp", "tag": "fixture-ordinary-dns", "server": INGRESS,
             "server_port": self.ordinary_port}, dns], "final": "fixture-ordinary-dns"}
        config["route"] = {"final": "fixture-domestic-direct",
                            "default_domain_resolver": "fixture-ordinary-dns",
                            "rules": [deepcopy(guard),
            {"inbound": ["fixture-domestic"], "action": "resolve", "server": "fixture-business-dns",
             "strategy": "prefer_ipv4", "disable_cache": True, "disable_optimistic_cache": True},
            deepcopy(guard),
            {"inbound": ["fixture-domestic"], "network": "udp", "ip_cidr": ["0.0.0.0/0"],
             "action": "route", "outbound": "fixture-upstream", "port_range": ["1024:65535"],
             "udp_connect": udp_connect,
             "udp_timeout": udp_timeout},
            {"inbound": ["fixture-domestic"], "network": "tcp", "ip_cidr": ["0.0.0.0/0"],
             "action": "route", "outbound": "fixture-upstream"},
            {"inbound": ["fixture-domestic"], "action": "reject"}]}
        return config

    def expect_dns(self, host):
        self.expected_dns.update(((UPSTREAM_EGRESS, host, 1), (UPSTREAM_EGRESS, host, 28)))

    def restart_domestic_and_device(self, kind, config, *, domestic_env=None, client=None, client_env=None):
        self.client.stop()
        self.domestic.stop()
        self.domestic = self.process("domestic-reconfigured", config, env=domestic_env)
        self.domestic.start(self.ingress_port, INGRESS)
        self.client = self.process("device-reconfigured-" + kind, client or self.client_config(kind),
                                   mihomo=kind == "mihomo", env=client_env)
        self.client.start(self.client_port, INGRESS)

    def reject(self, payload, process, reasons, *, host=None, target=None, port=None, window=2):
        target = target or self.target
        host = host or target.host
        self.case.assertEqual(ipaddress.ip_address(host).version, 4,
                              "Device UDP probes require literal IPv4; use raw XUDP for domains")
        with SocksDatagrams(self.client_port) as association:
            offset = len(process.log_path.read_text(errors="replace"))
            domestic_offset = len(self.domestic.log_path.read_text(errors="replace"))
            association.send(host, target.port if port is None else port, payload)
            deadline, matched = time.monotonic() + window, []
            while time.monotonic() < deadline:
                for item in self.targets:
                    item.audit()
                fresh = fresh_lines(process.log_path, offset)
                matched += [line for line in fresh if all(reason in line for reason in reasons)]
                association.stream.settimeout(.025)
                try:
                    packet = association.stream.recv(65535)
                except (socket.timeout, ConnectionRefusedError):
                    pass
                else:
                    raise AssertionError("Rejected UDP returned data: " + repr(packet))
            self.case.assertTrue(matched, "No fresh actual rejection reason " + repr(reasons) + "\n" + self.safe("\n".join(fresh)))
            self.case.assertNotIn("outbound/direct[fixture-domestic-direct]",
                                  self.domestic.log_path.read_text(errors="replace")[domestic_offset:].lower())
        self.observations.append({"kind": "rejection", "reason": list(reasons), "actual": self.safe(matched[0])})
        self.audit()

    def raw_reject(self, payload, process, reasons, *, host, target=None, port=None, window=2):
        """One authenticated XUDP send with no device-side DNS resolution.

        Retain response bytes for structural checking; polling must never resend
        a rejected datagram or lose a partial frame to per-read timeouts.
        """
        target = target or self.target
        port = target.port if port is None else port
        with RawXudpAssociation(self, host, port) as association:
            offset = len(process.log_path.read_text(errors="replace"))
            association.send(host, port, payload)
            deadline, matched, response = time.monotonic() + window, [], bytearray()
            closed = False
            while time.monotonic() < deadline:
                for item in self.targets:
                    item.audit()
                fresh = fresh_lines(process.log_path, offset)
                matched += [line for line in fresh if all(reason in line for reason in reasons)]
                if closed:
                    time.sleep(.01)
                    continue
                association.stream.settimeout(.025)
                try:
                    data = association.stream.recv(4096)
                except socket.timeout:
                    continue
                except (ConnectionResetError, ssl.SSLEOFError):
                    closed = True
                    continue
                if not data:
                    closed = True
                else:
                    response.extend(data)
                    self.case.assertLessEqual(len(response), 8192, "Oversized rejected XUDP response")
            assert_xudp_rejection_response(bytes(response))
            self.case.assertTrue(matched, "No fresh actual raw XUDP rejection " + repr(reasons)
                                 + "\n" + self.safe("\n".join(fresh)))
        self.observations.append({"kind": "raw-rejection", "reason": list(reasons),
                                  "actual": self.safe(matched[0])})
        self.audit()


def assert_xudp_rejection_response(response):
    """Accept only absent replies or complete empty end/control frames."""
    if not response:
        return
    if len(response) < 2 or response[0] != 0:
        raise AssertionError("Malformed rejected VLESS response")
    pos = 2 + response[1]
    if pos > len(response):
        raise AssertionError("Incomplete rejected VLESS response")
    while pos < len(response):
        if pos + 2 > len(response):
            raise AssertionError("Incomplete rejected XUDP frame length")
        length = struct.unpack("!H", response[pos:pos + 2])[0]
        pos += 2
        if length < 4 or pos + length > len(response):
            raise AssertionError("Incomplete rejected XUDP frame")
        header = response[pos:pos + length]
        pos += length
        if header[:2] != b"\0\0" or header[2] not in (3, 4) or header[3] & 1:
            raise AssertionError("Rejected XUDP returned a data-capable frame")


def xudp_address(host, port):
    """sing-vmess v0.2.8 PortThenAddress wire format, not SOCKS ATYP."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raw = host.encode("ascii")
        if not 0 < len(raw) < 256:
            raise ValueError("Invalid fixture domain")
        encoded = b"\x02" + bytes([len(raw)]) + raw
    else:
        encoded = (b"\x01" if address.version == 4 else b"\x03") + address.packed
    return struct.pack("!H", port) + encoded


def xudp_frame(host, port, payload, *, first):
    address = xudp_address(host, port)
    header = b"\0\0" + bytes([1 if first else 2, 1, 2]) + address
    if len(payload) > 65535:
        raise ValueError("Oversized fixture datagram")
    return struct.pack("!H", len(header)) + header + struct.pack("!H", len(payload)) + payload


def decode_xudp_address(data):
    if len(data) < 4:
        raise AssertionError("Truncated XUDP address")
    port, kind, rest = struct.unpack("!H", data[:2])[0], data[2], data[3:]
    if kind in (1, 3):
        size = 4 if kind == 1 else 16
        if len(rest) != size:
            raise AssertionError("Wrong XUDP IP length")
        host = str(ipaddress.ip_address(rest))
    elif kind == 2:
        if len(rest) != 1 + rest[0] or not rest[0]:
            raise AssertionError("Wrong XUDP domain length")
        host = rest[1:].decode("ascii")
    else:
        raise AssertionError("Unexpected XUDP address kind")
    return host, port


class RawXudpAssociation:
    """Authenticated adversarial destination changes within one mux session.

    Source: github.com/SagerNet/sing-vmess at v0.2.8, xudp.go/protocol.go and
    vless/protocol.go. Test CA only; every TLS connection verifies SNI/chain.
    """
    def __init__(self, fixture, host, port):
        self.stack = ExitStack()
        self.initial = host, port
        self.first = self.response_read = False
        try:
            plain = self.stack.enter_context(socket.create_connection((INGRESS, fixture.ingress_port), timeout=3))
            context = ssl.create_default_context(cafile=str(fixture.ca))
            self.stream = self.stack.enter_context(context.wrap_socket(plain, server_hostname=SNI))
            self.stream.settimeout(4)
            # Empty flow/addons; CommandMux has no VLESS destination field.
            self.stream.sendall(b"\0" + uuid.UUID(INGRESS_UUID).bytes + b"\0\x03")
        except BaseException:
            self.stack.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stack.close()

    def send(self, host, port, payload):
        self.stream.sendall(xudp_frame(host, port, payload, first=not self.first))
        self.first = True

    def receive(self):
        if not self.response_read:
            version, addon_length = _recv_exact(self.stream, 2)
            if version != 0:
                raise AssertionError("Wrong VLESS response version")
            _recv_exact(self.stream, addon_length)
            self.response_read = True
        for _ in range(8):
            length = struct.unpack("!H", _recv_exact(self.stream, 2))[0]
            if not 4 <= length <= 1024:
                raise AssertionError("Invalid XUDP reply frame length")
            header = _recv_exact(self.stream, length)
            if header[:2] != b"\0\0" or header[2] != 2 or header[3] & 2:
                raise AssertionError("XUDP session ended or returned unexpected frame: " + repr(header))
            destination = self.initial
            if len(header) > 4:
                if header[4] != 2:
                    raise AssertionError("XUDP reply is not UDP")
                destination = decode_xudp_address(header[5:])
            if header[3] & 1:
                size = struct.unpack("!H", _recv_exact(self.stream, 2))[0]
                return *destination, _recv_exact(self.stream, size)
        raise AssertionError("XUDP reply had no payload within bounded control frames")
