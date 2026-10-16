"""D0: existing Shadowsocks UDP qualification, not new protocol support.

Runtime opt-in: VUI_UDP_DNS_LOOPBACK=1 plus the existing VUI_TEST_CORES and
VUI_TEST_MIHOMO pins. Parser-only opt-in: VUI_UDP_DNS_PARSER=1. An opted-in
startup/permission/missing-reason failure is an error, never a skip.

Only IPv4 loopback, high target ports, three existing AEAD methods, and 1/1200
byte application datagrams or A/AAAA/TXT DNS wire payloads are characterized.
No UDP/53, UDP/443, Vision, MTU/fragmentation, public DNS, system networking,
new client export support, or new double-hop UDP forwarding is implied.
"""
from collections import Counter
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import psutil
import yaml

from app.models import database
from app.services import routing_store
from app.services.core_manager import SingBoxAdapter
from app.services.mihomo_routing import default_routing
from app.services.routing_store import read_snapshot, save_routing
import test_subscription_tokens as grants
import test_proxy_double_hop as double_hop
import proxy_loopback_support as proxy_support
from loopback_helpers import certificate_files, dns_query
from proxy_loopback_support import BoundCoreProcess, udp_target
from udp_dns_capability_helpers import (
    CLIENT, SERVER, TARGET, EGRESS, DatagramTarget, SocksDatagrams,
    decode_socks_datagram, dns_response, fresh_lines, isolated_rejection_offset,
    observe_rejection, paired_port,
    await_mihomo_ready, ReadyMihomoProcess, LifecycleTrace, recording_https_server,
    attach_failure_context,
)

METHODS = ("aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305")
KINDS = ("mihomo", "sing-box")
PASSWORD = "shadowsocks-correct-password"
RUNTIME_ENABLED = os.getenv("VUI_UDP_DNS_LOOPBACK") == "1"
PARSER_ENABLED = os.getenv("VUI_UDP_DNS_PARSER") == "1"


class DatagramFixtureTests(unittest.TestCase):
    """Fixture self-tests, deliberately separate from real-core evidence."""
    def test_mihomo_ready_waits_after_tcp_listener_without_request_retry(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            process = ReadyMihomoProcess(stack, ["fake-mihomo"], Path(temporary)/"core.log", {})
            fake = SimpleNamespace(pid=4321, poll=lambda: None)
            listener_ready, returned = threading.Event(), threading.Event()
            errors = []
            def tcp_only_start(owner, port, host):
                owner.process = fake
                listener_ready.set()
            def start():
                try:
                    process.start(18080)
                    returned.set()
                except Exception as exc:
                    errors.append(exc)
            with patch.object(BoundCoreProcess, "start", tcp_only_start), patch.object(process, "stop"):
                thread = threading.Thread(target=start)
                thread.start()
                self.assertTrue(listener_ready.wait(1))
                self.assertFalse(returned.wait(.03), "TCP listening alone released application traffic")
                process.ready_marker.write_text("4321\n")
                thread.join(1)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertTrue(returned.is_set())
                self.assertEqual(process.generation, 1)
                self.assertEqual(process.command.count("-post-up"), 1)
            process.process = None

    def test_mihomo_ready_fails_for_missing_exited_or_foreign_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary)/"ready"
            live = SimpleNamespace(pid=4321, poll=lambda: None)
            with self.assertRaisesRegex(AssertionError, "deadline exceeded"):
                await_mihomo_ready(live, marker, timeout=.02)
            with self.assertRaisesRegex(AssertionError, "exited before"):
                await_mihomo_ready(SimpleNamespace(pid=4321, poll=lambda: 7), marker)
            marker.write_text("4320\n")
            with self.assertRaisesRegex(AssertionError, "another process"):
                await_mihomo_ready(live, marker)

    def test_mihomo_ready_partial_and_previous_generation_never_qualify(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary)
            marker = root/"partial"
            marker.write_text("4321")
            with self.assertRaisesRegex(AssertionError, "deadline exceeded"):
                await_mihomo_ready(SimpleNamespace(pid=4321, poll=lambda: None), marker, timeout=.02)
            process = ReadyMihomoProcess(stack, ["fake-mihomo"], root/"core.log", {})
            (root/"core.log.ready-1").write_text("4321\n")
            with patch.object(BoundCoreProcess, "start") as start:
                with self.assertRaisesRegex(AssertionError, "pre-existing"):
                    process.start(18080)
                start.assert_not_called()
            # A second generation cannot accept the old generation's valid PID.
            fake = SimpleNamespace(pid=4321, poll=lambda: None)
            def tcp_only_start(owner, port, host):
                owner.process = fake
            real_wait = await_mihomo_ready
            with patch.object(BoundCoreProcess, "start", tcp_only_start), \
                 patch("proxy_loopback_support.await_mihomo_ready",
                       side_effect=lambda proc, path: real_wait(proc, path, timeout=.02)):
                with self.assertRaisesRegex(AssertionError, "deadline exceeded"):
                    process.start(18080)
                self.assertEqual(process.ready_marker.name, "core.log.ready-2")
            process.process = None

    def test_tls_diagnostics_keep_handshake_failure_and_final_close(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            _, cert, key = certificate_files(Path(temporary))
            trace = LifecycleTrace()
            with patch.object(proxy_support, "tracked_https_server",
                              lambda *args: recording_https_server(*args, trace=trace)):
                port, traffic = proxy_support.https_target(stack, TARGET, cert, key)
            with socket.create_connection((TARGET, port), timeout=1) as stream:
                stream.sendall(b"not a TLS handshake\r\n")
            deadline = time.monotonic() + 1
            while not any(event["event"] == "tls.closed" for event in trace.snapshot()):
                self.assertLess(time.monotonic(), deadline, "TLS diagnostic thread did not finish")
                time.sleep(.01)
            errors = [event for event in trace.snapshot() if event["event"] == "tls.error"]
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0]["stage"], "handshake")
            self.assertEqual(errors[0]["error_type"], "SSLError")
            self.assertTrue(errors[0]["reason"])
            self.assertEqual(traffic.snapshot(), [])
            self.assertEqual(traffic.pending(), 0)

    def test_failure_context_preserves_original_exception_and_missing_positive(self):
        case = DualSwitchDatagramRejectionTests()
        case.lifecycle = LifecycleTrace()
        case.failure_details = lambda label: "sanitized core context; missing positive; UDP not sent"
        expected = Counter({("target", "positive-control"): 1})
        baseline = expected.copy()
        case.evidence = SimpleNamespace(targets=expected)
        original = ssl.SSLEOFError("original positive TLS EOF")
        with self.assertRaises(ssl.SSLEOFError) as caught:
            with case.failure_context("positive-control"):
                raise original
        self.assertIs(caught.exception, original)
        self.assertIn("missing positive; UDP not sent", original.__notes__[0])
        self.assertEqual(case.evidence.targets, baseline)
        self.assertEqual(case.lifecycle.snapshot()[-1]["error_type"], "SSLEOFError")
        for problem in (OSError("missing log file"), ValueError("redaction failed")):
            with self.subTest(diagnostic=type(problem).__name__):
                def collect():
                    raise problem
                same_error = ssl.SSLEOFError("original core failure")
                with self.assertRaises(ssl.SSLEOFError) as preserved:
                    try:
                        raise same_error
                    except BaseException as exc:
                        attach_failure_context(exc, "fixture context: ", collect)
                        raise
                self.assertIs(preserved.exception, same_error)
                self.assertIn("collection failed", same_error.__notes__[0])
        class BrokenNotes(ssl.SSLEOFError):
            def add_note(self, value):
                raise RuntimeError("note attachment failed")
        unnotable = BrokenNotes("original error without working notes")
        with self.assertRaises(BrokenNotes) as preserved:
            try:
                raise unnotable
            except BaseException as exc:
                attach_failure_context(exc, "fixture context: ", lambda: "diagnostic")
                raise
        self.assertIs(preserved.exception, unnotable)

    def test_dns_answers_preserve_transaction_question_and_exact_rdata(self):
        for kind, value in ((1, socket.inet_aton("192.0.2.17")),
                            (28, socket.inet_pton(socket.AF_INET6, "2001:db8::17")),
                            (16, b"\x0bVUI-UDP-DNS")):
            packet = dns_query("fixture.example.test", kind)
            response = dns_response(packet)
            self.assertEqual(response[:2], packet[:2])
            self.assertEqual(response[12:len(packet)], packet[12:])
            self.assertEqual(struct.unpack("!HHHHH", response[2:12]), (0x8180, 1, 1, 0, 0))
            self.assertEqual(response[len(packet):], b"\xc0\x0c"
                             + struct.pack("!HHIH", kind, 1, 0, len(value)) + value)
        for malformed in (b"", b"GET / HTTP/1.0\r\n", dns_query("x.test", 15),
                          dns_query("x.test", 1) + b"trailing"):
            with self.assertRaises(ValueError):
                dns_response(malformed)

    def test_socks_reply_identity_and_fragmentation_are_not_discarded(self):
        packet = b"\0\0\0\x01" + socket.inet_aton(TARGET) + struct.pack("!H", 18053) + b"\0payload"
        self.assertEqual(decode_socks_datagram(packet), (TARGET, 18053, b"\0payload"))
        for invalid in (packet[:8], b"\0\0\x01" + packet[3:], b"\0\0\0\x03\x05x"):
            with self.assertRaises(AssertionError):
                decode_socks_datagram(invalid)

    def test_lifetime_ledger_rejects_late_delivery_after_authorized_recovery(self):
        target = DatagramTarget()
        try:
            target.direct(b"control")
            target.direct(b"recovery")
            with target.lock:
                target.received.append((EGRESS, b"late-negative"))
            with self.assertRaisesRegex(AssertionError, "late"):
                target.audit()
            with self.assertRaisesRegex(AssertionError, "late"):
                target.close()
        finally:
            target.close()

    def test_stale_split_and_generic_timeout_logs_never_supply_crypto_reason(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"core.log"
            reason = "inbound/shadowsocks[test]: process packet from 127.0.0.1:1: cipher: message authentication failed\n"
            path.write_text(reason + "partial")
            offset = len(path.read_text())
            with path.open("a") as log:
                log.write(reason + "context deadline exceeded\n")
            self.assertEqual(fresh_lines(path, offset), ["context deadline exceeded"])
            with path.open("a") as log:
                log.write(reason)
            self.assertEqual(fresh_lines(path, offset)[-1], reason.strip())
            target = DatagramTarget()
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
                    stream.bind((CLIENT, 0))
                    association = SimpleNamespace(stream=stream, sent=[(target.host, target.port, b"fixture")])
                    with self.assertRaisesRegex(AssertionError, "No fresh actual core rejection"):
                        observe_rejection(association, target, path, len(path.read_text()),
                                          ("process packet from", "message authentication failed"), window=.05)
            finally:
                target.close()

    def test_fresh_reason_does_not_end_observation_before_a_late_packet(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"core.log"
            path.write_text("process packet from 127.0.0.1:1: message authentication failed\n")
            target = DatagramTarget()
            def late_packet():
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    sender.bind((CLIENT, 0))
                    sender.sendto(b"late-negative", (target.host, target.port))
            timer = threading.Timer(.04, late_packet)
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
                    stream.bind((CLIENT, 0))
                    timer.start()
                    with self.assertRaisesRegex(AssertionError, "late"):
                        observe_rejection(SimpleNamespace(stream=stream, sent=[(target.host, target.port, b"fixture")]),
                                          target, path, 0,
                                          ("process packet from", "message authentication failed"), window=.3)
            finally:
                timer.join(timeout=1)
                with self.assertRaisesRegex(AssertionError, "late"):
                    target.close()

    def test_no_send_failed_association_and_other_target_cannot_use_a_real_reason(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"core.log"
            path.write_text("router: network=udp => reject\n")
            target = DatagramTarget()
            try:
                for sent in ([], [(target.host, target.port + 1, b"other-request")],
                             [(target.host, target.port, b"one"), (target.host, target.port, b"two")]):
                    with self.subTest(sent=sent), self.assertRaisesRegex(AssertionError, "exactly one sent datagram"):
                        observe_rejection(SimpleNamespace(sent=sent), target, path, 0, ("network=udp", "reject"))
                # Failed SOCKS setup must escape before checkpoint or send. It
                # cannot be caught and reinterpreted as application UDP refusal.
                with patch("udp_dns_capability_helpers.socket.create_connection", side_effect=OSError("associate failed")), \
                     patch("udp_dns_capability_helpers.SocksDatagrams.send") as send:
                    with self.assertRaisesRegex(OSError, "associate failed"):
                        with SocksDatagrams(18080):
                            self.fail("Failed association entered the request body")
                    send.assert_not_called()
            finally:
                target.close()

    def test_old_partial_and_unrelated_request_reason_cannot_qualify_new_udp(self):
        reasons = ("network=udp", "reject")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"core.log"
            path.write_text("router: network=udp => reject\n")
            with self.assertRaisesRegex(AssertionError, "another request"):
                isolated_rejection_offset(path, reasons)
            for prefix, suffix in (("OLD REQUEST partial prefix: ", "network=udp => reject\n"),
                                   ("", "router: network=tcp => reject\n")):
                with self.subTest(prefix=prefix, suffix=suffix):
                    path.write_text(prefix)
                    offset = isolated_rejection_offset(path, reasons)
                    with path.open("a") as log:
                        log.write(suffix)
                    target = DatagramTarget()
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
                            stream.bind((CLIENT, 0))
                            association = SimpleNamespace(stream=stream, sent=[(target.host, target.port, b"current")])
                            with self.assertRaisesRegex(AssertionError, "No fresh actual core rejection"):
                                observe_rejection(association, target, path, offset, reasons, window=.05)
                    finally:
                        target.close()

    def test_synchronous_target_close_waits_for_accepted_packet(self):
        accepted, release, closed = threading.Event(), threading.Event(), threading.Event()
        def respond(packet):
            accepted.set()
            if not release.wait(2):
                raise AssertionError("Test handler was not released")
            return packet
        target = DatagramTarget(respond)
        target.expect(CLIENT, b"in-flight")
        errors = []
        def close():
            try:
                target.close()
            except BaseException as exc:
                errors.append(exc)
            finally:
                closed.set()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
            stream.bind((CLIENT, 0))
            stream.sendto(b"in-flight", (target.host, target.port))
            self.assertTrue(accepted.wait(1))
            closer = threading.Thread(target=close)
            closer.start()
            try:
                self.assertFalse(closed.wait(.05), "Close abandoned an accepted datagram")
            finally:
                release.set()
                closer.join(timeout=3)
        self.assertTrue(closed.is_set())
        self.assertEqual(errors, [])
        target.audit()


class ShadowsocksFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cores, mihomo = os.getenv("VUI_TEST_CORES"), os.getenv("VUI_TEST_MIHOMO")
        if not cores or not mihomo:
            raise AssertionError("Enabled UDP/DNS characterization requires both fixed binary paths")
        cls.singbox, cls.mihomo = str(Path(cores)/"sing-box"), mihomo
        for command, prefix in (([cls.singbox, "version"], "sing-box version 1.14.2\n"),
                                ([cls.mihomo, "-v"], "Mihomo Meta v1.19.32 ")):
            result = subprocess.run(command, text=True, capture_output=True, timeout=15)
            output = result.stdout + result.stderr
            if result.returncode or not output.startswith(prefix):
                raise AssertionError("Fixed UDP/DNS core pin mismatch: " + output)

    def setUp(self):
        grants.SubscriptionTokenTests.setUp(self)
        temporary = tempfile.TemporaryDirectory(prefix="vui-udp-routing-")
        self.addCleanup(temporary.cleanup)
        self.suite_root = Path(temporary.name)
        patch.object(routing_store, "ROUTING_FILE", self.suite_root/"routing.json").start()
        save_routing({**default_routing(), "mode": "direct"}, read_snapshot()["revision"])

    def export(self, kind):
        response = self.client.post("/api/subscriptions", headers=grants.HEADERS, json={
            "label": "isolated-udp-characterization", "server": SERVER, "inbound_ids": [1],
            "formats": ["mihomo.yaml", "sing-box.json"]})
        self.assertEqual(response.status_code, 201, response.text)
        grant = response.json()
        with self.client.__class__(self.client.app, base_url=grants.ORIGIN,
                                   client=(CLIENT, 54112)) as public:
            response = public.get(grant["paths"]["mihomo.yaml" if kind == "mihomo" else "sing-box.json"])
        self.assertEqual(response.status_code, 200, response.text)
        return yaml.safe_load(response.text)

    def core(self, stack, root, label, config, kind):
        path = root/(label + (".yaml" if kind == "mihomo" else ".json"))
        path.write_text(yaml.safe_dump(config, sort_keys=False) if kind == "mihomo" else json.dumps(config))
        path.chmod(0o600)
        if kind == "mihomo":
            home = root/(label + "-home")
            home.mkdir()
            command = [self.mihomo, "-d", str(home), "-f", str(path)]
            check = [self.mihomo, "-t", "-d", str(home), "-f", str(path)]
        else:
            command = [self.singbox, "run", "-c", str(path)]
            check = [self.singbox, "check", "-c", str(path)]
        result = subprocess.run(check, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        process_class = ReadyMihomoProcess if kind == "mihomo" else BoundCoreProcess
        return process_class(stack, command, root/(label + ".log"), dict(os.environ))

    @contextmanager
    def scenario(self, method, kind, *, failure=None, start=True):
        with ExitStack() as stack:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="vui-udp-dns-")))
            targets, processes = [], []
            server_port, client_port = paired_port(SERVER), paired_port(CLIENT)
            with database.SessionLocal() as db:
                row = db.get(database.Inbound, 1)
                row.core, row.protocol, row.port, row.remark = "sing-box", "shadowsocks", server_port, "ss-udp-only"
                row.settings, row.stream_settings = {"method": method, "password": PASSWORD}, {}
                db.commit()
                server_config = SingBoxAdapter().build_config([row])
                if failure:
                    row.settings = {"method": ("aes-128-gcm" if method == "aes-256-gcm" else "aes-256-gcm")
                                    if failure == "cipher" else method,
                                    "password": "wrong-password" if failure == "password" else PASSWORD}
                    db.commit()
            # Only isolated listener/egress endpoints and verbosity change.
            # Source-bound egress distinguishes true server delivery from DIRECT.
            server_config["inbounds"][0]["listen"] = SERVER
            server_config["outbounds"][0]["inet4_bind_address"] = EGRESS
            server_config["log"] = {"level": "debug", "timestamp": False}
            config = self.export(kind)
            if kind == "mihomo":
                self.assertTrue(config["proxies"][0]["udp"])
                self.assertEqual(config["proxy-groups"], [{"name": "FORCE_PROXY", "type": "select",
                                                          "proxies": ["ss-udp-only"]}])
                config.update({"mixed-port": client_port, "bind-address": CLIENT, "allow-lan": False,
                               "log-level": "debug", "find-process-mode": "off", "geo-auto-update": False})
                # Pin this protocol experiment to its generated proxy. Payload
                # DNS is not client resolver traffic and requires no live resolver.
                config["rules"] = ["MATCH,FORCE_PROXY"]
                config["dns"] = {"enable": False}
            else:
                config["inbounds"][0]["listen_port"] = client_port
                config["log"] = {"level": "debug", "timestamp": False}
                self.assertEqual([value["type"] for value in config["outbounds"]], ["shadowsocks"])
            server = self.core(stack, root, "server", server_config, "sing-box")
            client = self.core(stack, root, "client", config, kind)
            processes.extend((server, client))
            value = SimpleNamespace(server=server, client=client, server_port=server_port,
                                    port=client_port, targets=targets, stack=stack)
            primary = None
            try:
                if start:
                    server.start(server_port, SERVER)
                    listeners = psutil.Process(server.process.pid).net_connections(kind="inet")
                    self.assertIn((socket.SOCK_DGRAM, SERVER, server_port),
                                  [(item.type, item.laddr.ip, item.laddr.port) for item in listeners if item.laddr])
                    client.start(client_port)
                yield value
            except BaseException as exc:
                primary = exc
                attach_failure_context(exc, "UDP fixture core context: ", lambda: double_hop.safe_log("\n".join(
                    f"{process.log_path.name} pid={getattr(process.process, 'pid', None)} "
                    f"status={process.process.poll() if process.process else None}\n"
                    + process.log_path.read_text(errors="replace") for process in processes),
                    (PASSWORD, "wrong-password")))
                raise
            finally:
                # No producer survives the final quiet-window audit. Target
                # callbacks run synchronously, then close performs a second audit.
                errors = []
                for action in ([process.stop for process in reversed(processes)]
                               + [target.drain for target in targets]
                               + [target.close for target in targets]):
                    try:
                        action()
                    except Exception as exc:
                        errors.append(exc)
                try:
                    text = client.log_path.read_text(errors="replace").lower()
                    self.assertNotIn("outbound/direct", text)
                    self.assertNotIn("using direct", text)
                except Exception as exc:
                    errors.append(exc)
                if errors:
                    if primary is not None:
                        for error in errors:
                            primary.add_note("UDP final audit also failed: " + str(error))
                    else:
                        for error in errors[1:]:
                            errors[0].add_note("Additional UDP final audit failure: " + str(error))
                        raise errors[0]

    def target(self, case, responder=lambda packet: packet):
        target = DatagramTarget(responder)
        case.targets.append(target)
        # Even a failing drain/primary assertion must join every fixture.
        case.stack.callback(target.close)
        return target

    def exchange(self, association, target, payload):
        target.audit()
        target.expect(EGRESS, payload)
        association.send(target.host, target.port, payload)
        self.assertEqual(association.receive(target.host, target.port), target.responder(payload))
        target.audit()


@unittest.skipUnless(PARSER_ENABLED, "Set VUI_UDP_DNS_PARSER=1 for separate fixed parser evidence")
class ShadowsocksDatagramParserTests(ShadowsocksFixture):
    def test_public_subscription_configs_parse_for_three_aead_and_both_clients(self):
        for method in METHODS:
            for kind in KINDS:
                with self.subTest(method=method, client=kind), self.scenario(method, kind, start=False):
                    pass


@unittest.skipUnless(RUNTIME_ENABLED, "Set VUI_UDP_DNS_LOOPBACK=1 for bounded real UDP/DNS evidence")
class ShadowsocksDatagramLoopbackTests(ShadowsocksFixture):
    def test_binary_1_and_1200_bytes_have_exact_response_and_server_source(self):
        for method in METHODS:
            for kind in KINDS:
                with self.subTest(method=method, client=kind), self.scenario(method, kind) as case:
                    target = self.target(case)
                    target.direct(b"reachable-binary-control")
                    with SocksDatagrams(case.port) as association:
                        for payload in (b"\0", bytes(range(256)) * 4 + bytes(range(176))):
                            with self.subTest(size=len(payload)):
                                self.exchange(association, target, payload)

    def test_dns_a_aaaa_txt_are_wire_payloads_over_existing_shadowsocks_udp(self):
        for method in METHODS:
            for kind in KINDS:
                with self.subTest(method=method, client=kind), self.scenario(method, kind) as case:
                    target = self.target(case, dns_response)
                    target.direct(dns_query("reachable-control.example.test", 1))
                    with SocksDatagrams(case.port) as association:
                        for query_type in (1, 28, 16):
                            with self.subTest(qtype=query_type):
                                payload = dns_query(f"udp-{time.monotonic_ns()}.example.test", query_type)
                                self.exchange(association, target, payload)

    def credential_rejections(self, failure):
        for method in METHODS:
            for kind in KINDS:
                with self.subTest(method=method, client=kind), self.scenario(method, kind, failure=failure) as case:
                    target = self.target(case)
                    target.direct(b"reachable-auth-control")
                    # Fixed inbound.go logs NewPacket failures from the pinned
                    # sing-shadowsocks v0.2.8 AEAD Open, on the same fresh line.
                    reasons = ("inbound/shadowsocks[", "process packet from", "message authentication failed")
                    with SocksDatagrams(case.port) as association:
                        offset = isolated_rejection_offset(case.server.log_path, reasons)
                        payload = (f"must-not-arrive-{failure}-{time.monotonic_ns()}".encode() + b"\0" * 1200)[:1200]
                        association.send(target.host, target.port, payload)
                        observe_rejection(association, target, case.server.log_path, offset, reasons)
                    self.assertNotIn("outbound/direct", "\n".join(fresh_lines(case.server.log_path, offset)))

    def test_wrong_password_has_fresh_crypto_reason_and_lifetime_zero_delivery(self):
        self.credential_rejections("password")

    def test_wrong_cipher_has_fresh_crypto_reason_and_lifetime_zero_delivery(self):
        self.credential_rejections("cipher")

    def test_stopped_server_has_no_direct_escape_then_new_datagram_recovers(self):
        for method in METHODS:
            for kind in KINDS:
                with self.subTest(method=method, client=kind), self.scenario(method, kind) as case:
                    target = self.target(case)
                    with SocksDatagrams(case.port) as association:
                        self.exchange(association, target, f"before-{time.monotonic_ns()}".encode())
                    case.server.stop()
                    self.assertIsNotNone(case.server.process.poll(), "Stop must finish before the negative request")
                    # A real bind proves the old TCP and UDP sockets are gone.
                    with socket.socket() as tcp, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                        tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        tcp.bind((SERVER, case.server_port))
                        udp.bind((SERVER, case.server_port))
                    target.direct(f"reachable-while-stopped-{time.monotonic_ns()}".encode())
                    with SocksDatagrams(case.port) as association:
                        association.send(target.host, target.port, f"stopped-must-not-arrive-{time.monotonic_ns()}".encode())
                        # No authentication rejection is claimed here. Process
                        # exit/socket closure independently establish the cause.
                        observe_rejection(association, target, None, 0, ())
                    target.drain()
                    case.server.start(case.server_port, SERVER)
                    with SocksDatagrams(case.port) as association:
                        self.exchange(association, target, f"recovered-{time.monotonic_ns()}".encode())


@unittest.skipUnless(RUNTIME_ENABLED, "Set VUI_UDP_DNS_LOOPBACK=1 for fixed Mihomo startup evidence")
class MihomoStartupLoopbackTests(unittest.TestCase):
    """Deterministic startup-mechanism reproduction, not UDP qualification."""
    @classmethod
    def setUpClass(cls):
        cls.mihomo = os.getenv("VUI_TEST_MIHOMO")
        if not cls.mihomo:
            raise AssertionError("Enabled startup characterization requires the fixed Mihomo path")
        result = subprocess.run([cls.mihomo, "-v"], capture_output=True, text=True, timeout=15)
        if result.returncode or not result.stdout.startswith("Mihomo Meta v1.19.32 "):
            raise AssertionError("Fixed startup core pin mismatch: " + result.stdout + result.stderr)

    def test_listener_and_socks_reply_precede_running_but_fresh_marker_gates_request(self):
        with tempfile.TemporaryDirectory(prefix="vui-mihomo-startup-") as temporary, ExitStack() as stack:
            root, trace = Path(temporary), LifecycleTrace()
            ca, cert, key = certificate_files(root)
            with patch.object(proxy_support, "tracked_https_server",
                              lambda *args: recording_https_server(*args, trace=trace)):
                target_port, traffic = proxy_support.https_target(stack, TARGET, cert, key)
            requested, release = threading.Event(), threading.Event()
            requests = []
            class Provider(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass
                def do_GET(self):
                    requests.append(self.path)
                    requested.set()
                    if not release.wait(4):
                        self.send_error(504)
                        return
                    body = b"payload:\n  - DOMAIN,unused.example.test\n"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
            provider_port = proxy_support.serve(stack, ThreadingHTTPServer((CLIENT, 0), Provider))
            port = paired_port(CLIENT)
            # A static local data-only provider holds loadProvider before
            # OnRunning. DIRECT here is solely the explicit loopback reference
            # path; it is not a proxy-export or negative-fallback experiment.
            config = {"mixed-port": port, "bind-address": CLIENT, "allow-lan": False,
                      "mode": "rule", "log-level": "debug", "ipv6": False,
                      "find-process-mode": "off", "geo-auto-update": False,
                      "dns": {"enable": False}, "rules": ["MATCH,DIRECT"],
                      "rule-providers": {"startup-barrier": {
                          "type": "http", "behavior": "classical", "format": "yaml",
                          "url": f"http://{CLIENT}:{provider_port}/rules.yaml", "interval": 0}}}
            path = root/"client.yaml"
            path.write_text(yaml.safe_dump(config))
            process = ReadyMihomoProcess(stack, [self.mihomo, "-d", str(root), "-f", str(path)],
                                         root/"client.log", dict(os.environ))
            started, errors = threading.Event(), []
            def start():
                try:
                    process.start(port)
                    started.set()
                except Exception as exc:
                    errors.append(exc)
            thread = threading.Thread(target=start)
            thread.start()
            try:
                self.assertTrue(requested.wait(3), "Local provider never entered the bounded startup hold")
                self.assertFalse(started.is_set(), "Readiness gate returned while provider loading was held")
                self.assertFalse(process.ready_marker.exists())
                # The old listener-only gate is satisfied, and SOCKS CONNECT
                # replies success, yet routing deliberately closes before TLS.
                with proxy_support.tunnel(port, TARGET, target_port, protocol="socks5h", timeout=1) as stream:
                    context = ssl.create_default_context(cafile=str(ca))
                    with self.assertRaises((ssl.SSLEOFError, ConnectionResetError)) as closed:
                        with context.wrap_socket(stream, server_hostname=double_hop.SNI):
                            self.fail("TLS unexpectedly reached a target before Running")
                self.assertEqual(traffic.snapshot(), [])
                self.assertFalse(any(event["event"] == "tls.accepted" for event in trace.snapshot()))
                release.set()
                thread.join(6)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertTrue(started.is_set())
                self.assertEqual(process.ready_marker.read_text(), f"{process.process.pid}\n")
                self.assertEqual(proxy_support.https_through(port, TARGET, target_port, ca, "after-running",
                                                             protocol="socks5h"), (200, proxy_support.BODY))
                self.assertEqual(traffic.snapshot(), [(CLIENT, TARGET, "/after-running")])
                self.assertEqual(requests, ["/rules.yaml"])
                print("Mihomo startup mechanism: SOCKS success before Running yielded "
                      + type(closed.exception).__name__ + "; fresh post-up PID marker then one HTTPS request succeeded")
            except BaseException as exc:
                attach_failure_context(exc, "Mihomo startup context: ", lambda:
                                       repr(trace.snapshot()) + "\n" + process.log_path.read_text(errors="replace"))
                raise
            finally:
                release.set()
                thread.join(6)
                process.stop()


@unittest.skipUnless(RUNTIME_ENABLED, "Set VUI_UDP_DNS_LOOPBACK=1 for additional dual-switch UDP rejection")
class DualSwitchDatagramRejectionTests(double_hop.DoubleHopFixture):
    def setUp(self):
        self.lifecycle = LifecycleTrace()
        with patch.object(proxy_support, "tracked_https_server",
                          lambda *args: recording_https_server(*args, trace=self.lifecycle)):
            super().setUp()

    def start_process(self, process, port, host=CLIENT):
        self.lifecycle.record("core.start", log=process.log_path.name, endpoint=(host, port))
        super().start_process(process, port, host)
        self.lifecycle.record("core.ready", log=process.log_path.name, endpoint=(host, port),
                              pid=process.process.pid,
                              marker=str(getattr(process, "ready_marker", None)))

    def stop_evidence_processes(self):
        self.lifecycle.record("cores.stop.begin")
        running = super().stop_evidence_processes()
        self.lifecycle.record("cores.stop.end", stopped=[{
            "log": process.log_path.name, "pid": process.process.pid,
            "status": process.process.poll()} for process in running])
        return running

    def failure_details(self, label):
        targets = []
        for traffic, expected in self.evidence.targets.items():
            actual = Counter(traffic.snapshot())
            targets.append({"name": "target" if traffic is self.target else "denied",
                            "missing": repr(expected - actual), "unexpected": repr(actual - expected),
                            "pending": traffic.pending()})
        actual_dns = Counter(self.answers.snapshot())
        data = {"phase": label, "current_state": getattr(self, "current_state", None),
                "target_port": self.target_port, "doh_port": self.doh_port,
                "ingress_port": self.ingress_port, "upstream_port": self.upstream_port,
                "local_port": self.local_port, "targets": targets,
                "dns_missing": repr(self.evidence.dns - actual_dns),
                "dns_unexpected": repr(actual_dns - self.evidence.dns),
                "ordinary_dns": self.ordinary_answers.queries,
                "udp": [{"missing": repr(expected - Counter(packets)),
                         "unexpected": repr(Counter(packets) - expected)}
                        for packets, expected in self.evidence.udp],
                "cores": [{"log": process.log_path.name,
                           "endpoint": getattr(process, "fixture_endpoint", None),
                           "pid": getattr(process.process, "pid", None),
                           "status": process.process.poll() if process.process else None,
                           "ready_marker": str(getattr(process, "ready_marker", None))}
                          for process in self.core_processes],
                "timeline": self.lifecycle.snapshot()}
        return self.sanitized(json.dumps(data, sort_keys=True) + "\n" + self.logs())

    @contextmanager
    def failure_context(self, label):
        self.lifecycle.record("phase.begin", phase=label)
        try:
            yield
        except BaseException as exc:
            self.lifecycle.record("phase.error", phase=label, error_type=type(exc).__name__, reason=str(exc))
            attach_failure_context(exc, "UDP/DNS failure context: ", lambda: self.failure_details(label))
            raise  # Preserve the original exception and all lifetime expectations.

    def finish_evidence(self):
        with self.failure_context("final-stop-and-drain"):
            super().finish_evidence()

    def final_evidence_audit(self):
        with self.failure_context("after-final-close"):
            super().final_evidence_audit()

    def udp_rejections(self, state):
        routes = ("local",) if state == "10" else ("singbox", "mihomo")
        for route in routes:
            for payload_label in ("binary", "dns"):
                with self.subTest(state=state, route=route, payload=payload_label), \
                     self.failure_context(f"state={state} route={route} payload={payload_label}"):
                    # Fixed reject lines identify the UDP rule, not the target.
                    # Each one-send case therefore gets entirely fresh cores and
                    # logs, plus a distinct live target port. Never reuse a late
                    # previous request's generic reason for the next datagram.
                    self.stop_evidence_processes()
                    self.drain_evidence()
                    self.start_upstream("tls")
                    self.activate(state)
                    auth = double_hop.AUTH if route == "local" else None
                    proxy_port = self.local_port if route == "local" else self.start_client(route, udp=True)[0]
                    self.lifecycle.record("positive-control.begin", proxy_port=proxy_port)
                    self.success(proxy_port, auth=auth, protocol="socks5h")
                    self.lifecycle.record("positive-control.end", proxy_port=proxy_port)
                    port, packets = udp_target(self.stack)
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
                        control.bind((CLIENT, 0))
                        control.settimeout(2)
                        control.sendto(b"reachable-control", (TARGET, port))
                        self.assertEqual(control.recv(65535), b"VUI-UDP-CONTROL:reachable-control")
                    expected = [(CLIENT, b"reachable-control")]
                    self.assertEqual(packets, expected)
                    self.evidence.udp.append((packets, Counter(expected)))
                    target = SimpleNamespace(host=TARGET, port=port, audit=lambda: self.evidence.assert_exact(self))
                    before_dns = self.answers.snapshot()
                    payload = (b"\0arbitrary-must-not-arrive" if payload_label == "binary"
                               else dns_query(f"blocked-udp-{time.monotonic_ns()}.example.test", 1))
                    with self.negative_phase():
                        with SocksDatagrams(proxy_port, auth=auth) as association:
                            reasons = ("network=udp", "reject")
                            offset = isolated_rejection_offset(self.ingress.log_path, reasons)
                            association.send(TARGET, port, payload)
                            self.lifecycle.record("udp.sent", target=(TARGET, port), payload_label=payload_label)
                            observe_rejection(association, target, self.ingress.log_path, offset, reasons)
                    self.assertEqual(self.answers.snapshot(), before_dns,
                                     "DNS-looking application UDP triggered resolution")
                    self.assertEqual(self.current_state, state)

    def test_state10_local_rejects_arbitrary_and_dns_wire_udp(self):
        self.udp_rejections("10")

    def test_state01_both_node_clients_reject_arbitrary_and_dns_wire_udp(self):
        self.udp_rejections("01")


if __name__ == "__main__":
    unittest.main()
