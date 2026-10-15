"""M2 exact-core double-hop acceptance, not a parser or simulated network test.

Opt in with VUI_PROXY_LOOPBACK=1, VUI_TEST_CORES and VUI_TEST_MIHOMO. An enabled
run fails on missing binaries, wrong pins, netlink/host restrictions, startup
failure, or missing rejection evidence; those conditions are never skips.

The production compiler supplies routes, authentication and address guards. Only
fixture endpoints/trust, ordinary egress source binding and the ingress listener
address are substituted. The latter is narrowed to loopback after exact source
binding has been checked. These process restarts prove routing transitions; the
separate transaction suite owns journal/active-revision and rollback assertions.
"""
from contextlib import ExitStack, contextmanager
from collections import Counter
from copy import deepcopy
import hashlib
import http.client
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

import yaml
import psutil

from app.services.core_manager import SingBoxAdapter
from app.services.proxy_policy import (
    AddressPolicy, DNS_TAG, LOCAL_TAG, NAMESPACE, UPSTREAM_TAG,
    VerifiedEndpoint, compile_proxy_policy,
)
from loopback_helpers import DnsAnswers, _socks_udp_target, certificate_files, dns_query
from reality_helpers import (
    SNI as REALITY_SNI, WRONG_SNI, reference_certificates, start_reference_target,
)
from test_reality_preflight_loopback import keypair, safe_log
from proxy_loopback_support import (
    BODY, DENIED, DIRECT_EGRESS, DOH, INGRESS, SNI, TARGET, UPSTREAM,
    UPSTREAM_EGRESS, BoundCoreProcess, BusinessAnswers, DeliveryEvidence, Traffic, ProxyRejected,
    doh_target, https_control, https_target, https_through, listen_port,
    ordinary_dns_target, tunnel, udp_target, udp_through,
)

INGRESS_UUID = "11111111-1111-4111-8111-111111111111"
UPSTREAM_UUID = "22222222-2222-4222-8222-222222222222"
WRONG_UUID = "33333333-3333-4333-8333-333333333333"
PROFILE_ID = "up_" + "4" * 32
USERNAME = "isolated-local-user"
PASSWORD = "isolated-local-password-only"
FLOW = "xtls-rprx-vision"
SHORT_ID = "0123456789abcdef"
AUTH = USERNAME, PASSWORD
# An Euler circuit visits every non-self directed edge once, keeping startup
# cost bounded while proving all twelve switch transitions for each upstream.
STATE_WALK = ("00", "10", "00", "01", "00", "11", "10", "01", "10", "11", "01", "11", "00")


class ProxyFixtureTests(unittest.TestCase):
    """Wire/coverage fixture self-tests; not fixed-core acceptance evidence."""
    def test_transition_walk_covers_each_directed_pair_once(self):
        states = ("00", "10", "01", "11")
        pairs = list(zip(STATE_WALK, STATE_WALK[1:]))
        self.assertEqual(len(pairs), 12)
        self.assertEqual(set(pairs), {(a, b) for a in states for b in states if a != b})

    def test_dns_fixture_multi_answer_family_failure_and_rebinding(self):
        answers = BusinessAnswers()
        name = "multi.example.test"
        answers.records[name] = (TARGET, DENIED)
        response = answers.answer(dns_query(name, 1), UPSTREAM_EGRESS)
        self.assertEqual(struct.unpack("!H", response[6:8])[0], 2)
        answers.failed.add((name, 28))
        self.assertEqual(answers.answer(dns_query(name, 28), UPSTREAM_EGRESS)[3] & 15, 2)
        self.assertEqual(answers.answer(dns_query(name, 1), UPSTREAM_EGRESS)[3] & 15, 0)
        name = "rebind.example.test"
        answers.sequences[name] = ((TARGET,), (DENIED,))
        self.assertEqual(answers.answer(dns_query(name, 1), UPSTREAM_EGRESS)[-4:], socket.inet_aton(TARGET))
        self.assertEqual(answers.answer(dns_query(name, 1), UPSTREAM_EGRESS)[-4:], socket.inet_aton(DENIED))

    def test_mapped_target_uses_real_ipv6_socks_wire_type(self):
        address = "::ffff:" + DENIED
        encoded = _socks_udp_target(address, 443)
        self.assertEqual(encoded, b"\x04" + socket.inet_pton(socket.AF_INET6, address) + struct.pack("!H", 443))


    def evidence_fixture(self):
        answers, ordinary, target = BusinessAnswers(), DnsAnswers("ordinary"), Traffic()
        evidence = DeliveryEvidence(answers, ordinary)
        evidence.expect(target, UPSTREAM_EGRESS, "good.example.test", "/good")
        target.append(UPSTREAM_EGRESS, "good.example.test", "/good")
        return evidence, answers, target

    def test_fixed_negative_baseline_rejects_delivery_between_related_requests(self):
        evidence, _, target = self.evidence_fixture()
        baseline = evidence.target_baseline()
        evidence.assert_targets(self, baseline)  # First negative returned its reason.
        target.append(DIRECT_EGRESS, "bad.example.test", "/must-not-arrive-late")
        with self.assertRaisesRegex(AssertionError, "late"):
            evidence.assert_targets(self, baseline)  # Next request cannot absorb it.

    def test_lifetime_ledger_catches_late_negative_after_authorized_recovery(self):
        evidence, _, target = self.evidence_fixture()
        evidence.assert_exact(self)
        evidence.expect(target, UPSTREAM_EGRESS, "recovery.example.test", "/recovery")
        target.append(UPSTREAM_EGRESS, "recovery.example.test", "/recovery")
        target.append(UPSTREAM_EGRESS, "bad.example.test", "/must-not-arrive-delayed")
        with self.assertRaisesRegex(AssertionError, "late"):
            evidence.assert_exact(self)

    def test_phase_cleanup_keeps_primary_reason_and_reports_failed_drain(self):
        evidence, _, target = self.evidence_fixture()
        case = DoubleHopFixture()
        case.evidence, case._negative_phase = evidence, None
        stopped = []
        case.stop_evidence_processes = lambda: stopped.append(True) or []
        with self.assertRaisesRegex(ValueError, "primary authentication reason") as caught:
            with case.negative_phase():
                target.append(UPSTREAM_EGRESS, "bad.example.test", "/must-not-arrive-late")
                raise ValueError("primary authentication reason")
        self.assertEqual(stopped, [True])
        self.assertIsNone(case._negative_phase)
        self.assertTrue(any("cleanup also failed" in note for note in caught.exception.__notes__))

    def rejection_fixture(self, path, *, dns=False):
        evidence, answers, _ = self.evidence_fixture()
        case = DoubleHopFixture()
        case.evidence = evidence
        case.ordinary_answers = evidence.ordinary
        case.ingress = SimpleNamespace(log_path=path)
        case.sanitized = lambda text: text
        case._negative_phase = {"baseline": evidence.target_baseline(), "dns": dns}
        return case, answers

    def test_rejection_sends_once_then_polls_an_async_fresh_dns_reason(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"ingress.log"
            name = "servfail.example.test"
            line = f"dns: lookup failed for {name}: (exchange6: servfail | exchange4: servfail)\n"
            path.write_text(line)  # Old matching evidence cannot satisfy this request.
            case, answers = self.rejection_fixture(path, dns=True)
            answers.failed.add(name)
            calls, written = [], threading.Event()
            def write_reason():
                with path.open("a") as log:
                    log.write(line)
                written.set()
            timer = threading.Timer(.1, write_reason)
            def request():
                calls.append(True)
                for family in (1, 28):
                    answers.answer(dns_query(name, family), UPSTREAM_EGRESS)
                timer.start()
                raise ProxyRejected("Caller received proxy refusal before the core log flushed")
            request.dns_name = name
            try:
                case.rejection(request, case.ingress,
                               (f"dns: lookup failed for {name}:", "exchange4: servfail", "exchange6: servfail"),
                               timeout=1)
                self.assertTrue(written.is_set(), "Returned before the fresh reason was written")
            finally:
                timer.join(timeout=2)
            self.assertEqual(calls, [True])
            self.assertEqual(case.evidence.dns, Counter({(UPSTREAM_EGRESS, name, 1): 1,
                                                       (UPSTREAM_EGRESS, name, 28): 1}))
            case.evidence.assert_exact(self)

    def test_rejection_never_reissues_when_only_stale_or_timeout_evidence_exists(self):
        for fresh in ("", "connection: context deadline exceeded\n"):
            with self.subTest(fresh=fresh), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary)/"ingress.log"
                path.write_text("connection: unknown uuid\n")
                case, _ = self.rejection_fixture(path)
                calls = []
                def request():
                    calls.append(True)
                    with path.open("a") as log:
                        log.write(fresh)
                    raise TimeoutError("A caller timeout is not an authentication reason")
                with self.assertRaisesRegex(AssertionError, "No fresh actual rejection reason"):
                    case.rejection(request, case.ingress, ("unknown uuid",), timeout=.1)
                self.assertEqual(calls, [True])
                case.evidence.assert_exact(self)

    def test_dns_deadline_requires_the_failed_lookup_and_requested_name(self):
        for fresh in ("connection: context deadline exceeded\n",
                      "dns: lookup failed for unrelated.example.test: context deadline exceeded\n"):
            with self.subTest(fresh=fresh), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary)/"ingress.log"
                path.write_text("")
                case, _ = self.rejection_fixture(path)
                calls = []
                def request():
                    calls.append(True)
                    path.write_text(fresh)
                    raise ProxyRejected("DNS route refused")
                with self.assertRaisesRegex(AssertionError, "No fresh actual rejection reason"):
                    case.rejection(request, case.ingress,
                                   ("dns: lookup failed for timeout.example.test:", "context deadline exceeded"),
                                   timeout=.1)
                self.assertEqual(calls, [True])

    def test_unfinished_dns_drain_fails_explicitly_at_its_bound(self):
        evidence, answers, _ = self.evidence_fixture()
        case = DoubleHopFixture()
        case.evidence = evidence
        answers.active = 1
        with self.assertRaisesRegex(AssertionError, "drain exceeded"):
            case.drain_evidence(timeout=.001)
        answers.active = 0

    def wait_for_connections(self, accounting, count):
        deadline = time.monotonic() + 2
        while accounting.connection_count() != count and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(accounting.connection_count(), count)

    def test_unfinished_tls_is_counted_before_parsing_and_closed_at_cleanup(self):
        # Both HTTPS sinks register the raw accepted socket before launching the
        # TLS worker. Neither a handshake nor a parsed request is necessary for
        # the bounded drain to notice unfinished work.
        for role in ("application", "doh"):
            with self.subTest(role=role), ExitStack() as stack:
                root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
                _, cert, key = certificate_files(root)
                answers = BusinessAnswers()
                evidence = DeliveryEvidence(answers, DnsAnswers("ordinary"))
                servers = stack.enter_context(ExitStack())
                if role == "application":
                    port, accounting = https_target(servers, INGRESS, cert, key)
                    evidence.targets[accounting] = Counter()
                    host = INGRESS
                else:
                    port, accounting, host = doh_target(servers, answers, cert, key), answers, DOH
                peer = stack.enter_context(socket.create_connection((host, port), timeout=2))
                self.wait_for_connections(accounting, 1)
                self.assertEqual(accounting.active, 0)
                self.assertGreater(evidence.pending(), 0)
                case = DoubleHopFixture()
                case.evidence = evidence
                with self.assertRaisesRegex(AssertionError, "drain exceeded"):
                    case.drain_evidence(timeout=.05)
                servers.close()  # The client has deliberately not closed yet.
                self.wait_for_connections(accounting, 0)
                self.assertEqual(peer.recv(1), b"")
                case.drain_evidence()

    def test_partial_headers_cannot_hide_a_late_application_delivery(self):
        with ExitStack() as stack:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            ca, cert, key = certificate_files(root)
            port, target = https_target(stack, INGRESS, cert, key)
            evidence = DeliveryEvidence(BusinessAnswers(), DnsAnswers("ordinary"))
            evidence.targets[target] = Counter()
            context = ssl.create_default_context(cafile=str(ca))
            raw = stack.enter_context(socket.create_connection((INGRESS, port), timeout=2))
            peer = stack.enter_context(context.wrap_socket(raw, server_hostname=SNI))
            peer.sendall(b"GET /must-not-arrive-late-header HTTP/1.1\r\nHost: vpn.example.test\r\n")
            self.wait_for_connections(target, 1)
            self.assertEqual(target.active, 0)
            self.assertEqual(target.snapshot(), [])
            case = DoubleHopFixture()
            case.evidence = evidence
            with self.assertRaisesRegex(AssertionError, "drain exceeded"):
                case.drain_evidence(timeout=.05)
            # Complete the previously accepted request only after the first
            # drain attempt. The lifetime ledger must preserve and reject it.
            peer.sendall(b"\r\n")
            response = http.client.HTTPResponse(peer)
            response.begin()
            self.assertEqual(response.read(), BODY)
            response.close()
            self.wait_for_connections(target, 0)
            with self.assertRaisesRegex(AssertionError, "late"):
                case.drain_evidence()

    def test_synchronous_dns_close_joins_an_already_accepted_datagram(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        answers, errors = DnsAnswers("ordinary"), []
        answer = answers.answer
        def delayed(packet):
            entered.set()
            if not release.wait(timeout=2):
                raise AssertionError("Delayed UDP fixture was not released")
            return answer(packet)
        answers.answer = delayed
        with ExitStack() as stack:
            servers = stack.enter_context(ExitStack())
            port = ordinary_dns_target(servers, answers)
            peer = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
            peer.sendto(dns_query("late.example.test", 1), (INGRESS, port))
            self.assertTrue(entered.wait(timeout=1))
            def close():
                try:
                    servers.close()
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    closed.set()
            closer = threading.Thread(target=close, daemon=True)
            closer.start()
            try:
                self.assertFalse(closed.wait(timeout=.05), "Close abandoned its accepted UDP handler")
            finally:
                release.set()
                closer.join(timeout=2)
            self.assertTrue(closed.is_set())
            self.assertEqual(errors, [])
            self.assertEqual(answers.queries, [("late.example.test", 1)])


class DoubleHopFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cores, mihomo = os.getenv("VUI_TEST_CORES"), os.getenv("VUI_TEST_MIHOMO")
        if not cores or not mihomo:
            raise AssertionError("Enabled double-hop test requires both pinned binary paths")
        cls.singbox, cls.mihomo = str(Path(cores)/"sing-box"), mihomo
        for command, prefix in (([cls.singbox, "version"], "sing-box version 1.14.2\n"),
                                ([cls.mihomo, "-v"], "Mihomo Meta v1.19.32 ")):
            result = subprocess.run(command, text=True, capture_output=True, timeout=15)
            output = result.stdout + result.stderr
            if result.returncode or not output.startswith(prefix):
                raise AssertionError("Fixed-core pin mismatch: " + output)
            if command[0] == cls.singbox and "with_utls" not in output:
                raise AssertionError("Fixed sing-box lacks required with_utls")
            print("double-hop pin: " + output.splitlines()[0])

    def setUp(self):
        self.evidence = None
        self.addCleanup(self.final_evidence_audit)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="vui-double-hop-")))
        self.ca, self.cert, self.key = certificate_files(self.root)
        self.reference_ca, reference_cert, reference_key = reference_certificates(self.root)
        self.trust = self.root/"temporary-trust.pem"
        self.trust.write_bytes(self.ca.read_bytes() + self.reference_ca.read_bytes())
        self.trust.chmod(0o600)
        (self.root/"empty-roots").mkdir()
        self.env = {**os.environ, "SSL_CERT_FILE": str(self.trust),
                    "SSL_CERT_DIR": str(self.root/"empty-roots")}
        self.private_key, self.public_key = keypair()
        _, self.wrong_public_key = keypair()
        self.reference_port, self.reference = start_reference_target(self.stack, reference_cert, reference_key)
        self.target_port, self.target = https_target(self.stack, TARGET, self.cert, self.key)
        self.denied_port, self.denied = https_target(self.stack, DENIED, self.cert, self.key)
        self.answers = BusinessAnswers()
        self.doh_port = doh_target(self.stack, self.answers, self.cert, self.key)
        self.ordinary_answers = DnsAnswers("ORDINARY_FALLBACK", {"servfail.example.test": TARGET})
        self.ordinary_dns_port = ordinary_dns_target(self.stack, self.ordinary_answers)
        self.upstream_port = listen_port(UPSTREAM)
        self.ingress_port, self.local_port = listen_port(), listen_port()
        self.policy = AddressPolicy._for_tests(allowed_addresses=(TARGET, UPSTREAM, DOH),
                                                doh=(DOH, self.doh_port, SNI))
        self.sequence = 0
        self.bootstrap_queries = []
        self.ingress = self.upstream = None
        self.clients = {}
        self.mode = "tls"
        self.core_processes = []
        self._negative_phase = None
        self.evidence = DeliveryEvidence(self.answers, self.ordinary_answers)
        self.addCleanup(self.finish_evidence)
        for host, port, traffic in ((TARGET, self.target_port, self.target),
                                    (DENIED, self.denied_port, self.denied)):
            self.evidence.expect(traffic, INGRESS, SNI, "/reachable-control")
            self.assertEqual(https_control(host, port, self.ca, "reachable-control"), (200, BODY))
            self.assertEqual(len(traffic.snapshot()), 1)
        # Preserve controls and all subsequent deliveries; never clear counters
        # to hide a delayed rejected request.

    def stop_evidence_processes(self):
        running = []
        for process in self.core_processes:
            if process.process is not None and process.process.poll() is None:
                running.append(process)
            process.stop()
        runtime = getattr(self, "runtime", None)
        if runtime is not None:
            runtime.stop(persist=False)
        return running

    def drain_evidence(self, baseline=None, *, timeout=5):
        # Wait for every accepted connection and application/DoH callback,
        # then require a continuous quiet window. This has a hard bound; an
        # unfinished callback fails instead of being silently abandoned.
        deadline, quiet_since = time.monotonic() + timeout, None
        while True:
            if baseline is not None:
                self.evidence.assert_targets(self, baseline)
            now = time.monotonic()
            if self.evidence.pending() == 0:
                quiet_since = quiet_since or now
                if now - quiet_since >= .2:
                    self.evidence.assert_exact(self)
                    return
            else:
                quiet_since = None
            if now >= deadline:
                self.fail("Application/DNS evidence drain exceeded its bounded deadline")
            time.sleep(.025)

    def finish_evidence(self):
        if self.evidence is not None:
            self.stop_evidence_processes()
            self.drain_evidence()

    def final_evidence_audit(self):
        # Registered before stack.close: this last audit runs AFTER processes,
        # fixture servers and their sockets have been closed, even on failures.
        if self.evidence is not None:
            self.drain_evidence()

    @contextmanager
    def negative_phase(self, *, dns=False):
        self.assertIsNone(self._negative_phase, "Negative phases must not overlap")
        self.evidence.assert_exact(self)
        baseline = self.evidence.target_baseline()
        self._negative_phase = {"baseline": baseline, "dns": dns}
        ingress_log = None
        if getattr(self, "ingress", None) is not None:
            ingress_log = (self.ingress.log_path, len(self.ingress.log_path.read_text(errors="replace")))
        primary = None
        try:
            yield
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                running = self.stop_evidence_processes()
                self.drain_evidence(baseline)
                if ingress_log:
                    path, offset = ingress_log
                    self.assertNotIn("outbound/direct[direct]", path.read_text(errors="replace")[offset:].lower(),
                                     "Negative-phase drain revealed a late DIRECT attempt")
                # No new baseline is taken from observed traffic. The lifetime
                # authorized-path ledger remains unchanged across recovery.
                if primary is None:
                    for process in running:
                        host, port = process.fixture_endpoint
                        self.start_process(process, port, host)
            except Exception as cleanup_error:
                if primary is None:
                    raise
                primary.add_note("Negative-phase cleanup also failed: " + str(cleanup_error))
            finally:
                self._negative_phase = None

    def path(self, name, suffix):
        self.sequence += 1
        return self.root/f"{self.sequence:04d}-{name}.{suffix}"

    def sanitized(self, text):
        return safe_log(text, (self.private_key, self.public_key, self.wrong_public_key))

    def logs(self):
        return self.sanitized("\n".join(path.name + ":\n" + path.read_text(errors="replace")
                                        for path in sorted(self.root.glob("*.log"))))

    def process(self, label, config, *, client="singbox", env=None):
        path = self.path(label, "yaml" if client == "mihomo" else "json")
        path.write_text(yaml.safe_dump(config, sort_keys=False) if client == "mihomo" else json.dumps(config))
        path.chmod(0o600)
        if client == "mihomo":
            home = self.path(label, "home")
            home.mkdir()
            check = [self.mihomo, "-t", "-d", str(home), "-f", str(path)]
            command = [self.mihomo, "-d", str(home), "-f", str(path)]
        else:
            check = [self.singbox, "check", "-c", str(path)]
            command = [self.singbox, "run", "-c", str(path)]
        result = subprocess.run(check, text=True, capture_output=True, timeout=15, env=env or self.env)
        self.assertEqual(result.returncode, 0, self.sanitized(result.stdout + result.stderr))
        return BoundCoreProcess(self.stack, command, self.path(label, "log"), env or self.env)

    def start_process(self, process, port, host=INGRESS):
        process.fixture_endpoint = (host, port)
        if process not in self.core_processes:
            self.core_processes.append(process)
        try:
            process.start(port, host)
        except AssertionError as exc:
            # Host-level EPERM remains a hard, explicit blocked result. Do not
            # retry with privileges, change the host, or turn it into a skip.
            raise AssertionError(self.sanitized(str(exc))) from None

    def upstream_config(self, mode):
        tls = {"enabled": True, "server_name": SNI,
               "certificate_path": str(self.cert), "key_path": str(self.key)}
        user = {"uuid": UPSTREAM_UUID}
        if mode == "reality":
            tls = {"enabled": True, "server_name": REALITY_SNI, "reality": {
                "enabled": True, "handshake": {"server": INGRESS, "server_port": self.reference_port},
                "private_key": self.private_key, "short_id": [SHORT_ID]}}
            user["flow"] = FLOW
        return {"log": {"level": "debug", "timestamp": False}, "inbounds": [{
            "type": "vless", "tag": "fixture-upstream-ingress", "listen": UPSTREAM,
            "listen_port": self.upstream_port, "users": [user], "tls": tls}],
            "outbounds": [{"type": "direct", "tag": "fixture-upstream-egress", "inet4_bind_address": UPSTREAM_EGRESS}],
            "route": {"final": "fixture-upstream-egress"}}

    def start_upstream(self, mode):
        self.mode = mode
        self.upstream = self.process("upstream-" + mode, self.upstream_config(mode))
        self.start_process(self.upstream, self.upstream_port, UPSTREAM)

    def compile_state(self, state, *, failure=None):
        row = SimpleNamespace(id=1, core="sing-box", protocol="vless", port=self.ingress_port,
            remark="isolated-ingress", tag="fixture-ingress", enable=True, expiry_time=0,
            settings={"users": [{"uuid": INGRESS_UUID}]}, stream_settings={"tls": {
                "enabled": True, "server_name": SNI,
                "certificate_path": str(self.cert), "key_path": str(self.key)}})
        base = SingBoxAdapter().build_config([row])
        base["log"] = {"level": "debug", "timestamp": False}
        base["outbounds"][0]["inet4_bind_address"] = DIRECT_EGRESS
        base["dns"] = {"servers": [{"type": "udp", "tag": "fixture-ordinary-dns",
            "server": INGRESS, "server_port": self.ordinary_dns_port}], "final": "fixture-ordinary-dns"}
        base["route"]["default_domain_resolver"] = "fixture-ordinary-dns"
        tls = {"mode": "tls", "server_name": SNI, "alpn": [], "fingerprint": "",
               "public_key": "", "short_id": ""}
        if self.mode == "reality":
            tls.update(mode="reality", server_name=REALITY_SNI, fingerprint="chrome",
                       public_key=self.public_key, short_id=SHORT_ID)
        if failure == "sni":
            tls["server_name"] = WRONG_SNI
        if failure == "public_key":
            tls["public_key"] = self.wrong_public_key
        if failure == "short_id":
            tls["short_id"] = "fedcba9876543210"
        profile = {"schema_version": 1, "protocol": "vless", "name": "isolated upstream",
            "server": "upstream.fixture.test", "server_port": self.upstream_port,
            "uuid": WRONG_UUID if failure == "uuid" else UPSTREAM_UUID, "transport": "tcp",
            "encryption": "none", "flow": FLOW if self.mode == "reality" else "", "tls": tls}
        snapshot = {"revision": hashlib.sha256((self.mode + state + str(failure)).encode()).hexdigest(),
            "settings": {"schema_version": 1, "local_proxy_enabled": state[0] == "1",
                "relay_enabled": state[1] == "1", "selected_upstream_id": PROFILE_ID,
                "local_port": self.local_port, "local_username": USERNAME,
                "local_password_ref": "sec_" + "5"*32, "dns_policy_version": 1, "deny_policy_version": 1},
            "upstreams": {PROFILE_ID: profile}, "local_password": PASSWORD}
        # A bootstrap fixture only: resolution is outside this pure compiler.
        # The original name must never be looked up by business DoH/runtime.
        if state != "00":
            self.bootstrap_queries.append(profile["server"])
        endpoint = VerifiedEndpoint(PROFILE_ID, snapshot["revision"], profile["server"], (UPSTREAM,), 1792080000)
        compiled = compile_proxy_policy(base, [row], snapshot, endpoint=endpoint,
                                         self_addresses=(INGRESS,), policy=self.policy)
        config = deepcopy(compiled.config)
        for inbound in config["inbounds"]:
            if inbound["type"] == "vless":
                self.assertEqual(inbound["listen"], "::")
                inbound["listen"] = INGRESS  # Narrow a test listener, never relax a guard.
        if failure == "flow":
            # The strict profile correctly rejects missing REALITY flow. This
            # deliberate negative mutation tests the fixed core's runtime
            # authentication reason, not a supported compiler output.
            next(item for item in config["outbounds"] if item["tag"] == UPSTREAM_TAG).pop("flow")
        return compiled, config, snapshot

    def activate(self, state, *, failure=None, env=None):
        if self.ingress:
            self.ingress.stop()
        compiled, config, snapshot = self.compile_state(state, failure=failure)
        self.ingress = self.process("ingress-" + state + "-" + str(failure), config, env=env)
        self.start_process(self.ingress, self.ingress_port)
        self.current_state, self.compiled, self.snapshot = state, compiled, snapshot
        if state[0] == "1":
            with socket.create_connection((INGRESS, self.local_port), timeout=1):
                pass
        else:
            with self.assertRaises(ConnectionRefusedError):
                socket.create_connection((INGRESS, self.local_port), timeout=.5)
        return compiled

    def start_client(self, kind, *, wrong_uuid=False, udp=False):
        port = listen_port()
        uid = WRONG_UUID if wrong_uuid else INGRESS_UUID
        if kind == "singbox":
            node = {"type": "vless", "tag": "INGRESS_ONLY", "server": INGRESS,
                "server_port": self.ingress_port, "uuid": uid,
                "tls": {"enabled": True, "server_name": SNI}}
            if not udp:
                node["network"] = "tcp"
            config = {"log": {"level": "debug", "timestamp": False},
                "inbounds": [{"type": "mixed", "listen": INGRESS, "listen_port": port}],
                "outbounds": [node], "route": {"final": "INGRESS_ONLY"}}
        else:
            config = {"mixed-port": port, "bind-address": INGRESS, "allow-lan": False,
                "mode": "rule", "log-level": "debug", "ipv6": False,
                "proxies": [{"name": "INGRESS_ONLY", "type": "vless", "server": INGRESS,
                    "port": self.ingress_port, "uuid": uid, "network": "tcp", "udp": udp,
                    "tls": True, "servername": SNI, "skip-cert-verify": False}],
                "rules": ["MATCH,INGRESS_ONLY"]}
        self.assertNotIn("DIRECT", json.dumps(config))
        process = self.process(kind + ("-bad-auth" if wrong_uuid else ""), config, client=kind)
        self.start_process(process, port)
        return port, process

    def start_clients(self):
        self.clients = {kind: self.start_client(kind) for kind in ("singbox", "mihomo")}

    def success(self, port, *, proxied=True, protocol="connect", auth=None, host=None, target_port=None, timeout=3):
        self.assertIsNone(self._negative_phase, "Positive delivery cannot reset an active negative phase")
        self.evidence.assert_exact(self)
        marker = f"probe-{self.sequence}-{time.monotonic_ns()}"
        host = host or (marker + ".example.test" if proxied else TARGET)
        before, dns_before = self.target.snapshot(), self.answers.snapshot()
        upstream_offset = len(self.upstream.log_path.read_text(errors="replace")) if self.upstream else 0
        bootstrap_before = list(self.bootstrap_queries)
        self.evidence.expect(self.target, UPSTREAM_EGRESS if proxied else DIRECT_EGRESS, host, "/" + marker)
        if proxied and not self.is_ip(host):
            self.evidence.expect_dns(host)
        result = https_through(port, host, target_port or self.target_port, self.ca, marker,
                               protocol=protocol, auth=auth, timeout=timeout)
        self.assertEqual(result, (200, BODY), self.logs())
        delivered = self.target.snapshot()[len(before):]
        self.assertEqual(len(delivered), 1, self.logs())
        self.assertEqual(delivered[0], (UPSTREAM_EGRESS if proxied else DIRECT_EGRESS, host, "/" + marker), self.logs())
        self.assertEqual(self.bootstrap_queries, bootstrap_before, "Runtime re-resolved the upstream fixture")
        fresh_dns = self.answers.snapshot()[len(dns_before):]
        if proxied:
            self.assertEqual(self.ordinary_answers.queries, [], "Protected traffic used ordinary DNS")
            if not self.is_ip(host):
                self.assertIn((UPSTREAM_EGRESS, host, 1), fresh_dns, self.logs())
                self.assertTrue(all(peer == UPSTREAM_EGRESS for peer, _, _ in fresh_dns), fresh_dns)
            fresh = self.upstream.log_path.read_text(errors="replace")[upstream_offset:]
            self.assertIn(TARGET + ":" + str(target_port or self.target_port), fresh, self.logs())
        else:
            self.assertEqual(fresh_dns, [], "Ordinary literal-IP traffic used protected business DNS")
        self.assertNotIn("upstream.fixture.test", [name for _, name, _ in self.answers.snapshot()])
        return marker

    @staticmethod
    def is_ip(host):
        import ipaddress
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            return False

    def assert_state(self, state):
        self.assertEqual(self.current_state, state)
        self.assertEqual((self.snapshot["settings"]["local_proxy_enabled"], self.snapshot["settings"]["relay_enabled"]),
                         (state[0] == "1", state[1] == "1"))
        for kind, (port, _) in self.clients.items():
            with self.subTest(state=state, client=kind):
                self.success(port, proxied=state[1] == "1", protocol="socks5h")
        if state[0] == "1":
            for protocol in ("connect", "socks5h"):
                with self.subTest(state=state, local=protocol):
                    self.success(self.local_port, auth=AUTH, protocol=protocol)

    def rejection(self, request, process, reasons, *, timeout=12):
        """Send once, then poll fresh specific core evidence with zero delivery."""
        self.assertIsNotNone(self._negative_phase, "Every rejection belongs to an explicit fixed-baseline phase")
        baseline = self._negative_phase["baseline"]
        self.evidence.assert_targets(self, baseline)
        offset = len(process.log_path.read_text(errors="replace"))
        ingress_offset = len(self.ingress.log_path.read_text(errors="replace")) if self.ingress else 0
        deadline = time.monotonic() + timeout
        if self._negative_phase["dns"] and getattr(request, "dns_name", None):
            self.evidence.expect_dns(request.dns_name)
        # A late log flush must never create another network request or another
        # expected DNS pair. Missing/wrong evidence is a failure of this request.
        try:
            result = request()
        except (OSError, http.client.HTTPException):
            pass
        else:
            self.fail("Rejected operation unexpectedly succeeded: " + repr(result))
        while True:
            self.evidence.assert_targets(self, baseline)
            fresh = process.log_path.read_text(errors="replace")[offset:].lower()
            lines = [line for line in fresh.splitlines() if all(reason in line for reason in reasons)]
            if lines:
                self.evidence.assert_targets(self, baseline)
                self.assertEqual(self.ordinary_answers.queries, [], "Rejected traffic used ordinary DNS fallback")
                if self.ingress:
                    ingress_log = self.ingress.log_path.read_text(errors="replace")[ingress_offset:].lower()
                    self.assertNotIn("outbound/direct[direct]", ingress_log, "Rejected ingress attempted DIRECT")
                print("double-hop refusal: " + self.sanitized(lines[-1]))
                return fresh
            if time.monotonic() >= deadline:
                self.fail("No fresh actual rejection reason " + repr(reasons) + ":\n" + self.sanitized(fresh))
            time.sleep(.05)

    def negative_request(self, port=None, *, auth=AUTH, protocol="socks5h", host=TARGET, target_port=None, timeout=3):
        def request():
            return https_through(port or self.local_port, host, target_port or self.target_port,
                self.ca, "must-not-arrive-" + str(time.monotonic_ns()), protocol=protocol, auth=auth, timeout=timeout)
        request.dns_name = None if self.is_ip(host) else host
        return request

    def transitions(self, mode):
        self.start_upstream(mode)
        self.activate(STATE_WALK[0])
        self.start_clients()
        self.assert_state(STATE_WALK[0])
        seen = set()
        for old, new in zip(STATE_WALK, STATE_WALK[1:]):
            with self.subTest(mode=mode, transition=old + "->" + new):
                before_identity = self.compiled.identity
                self.activate(new)
                self.assertNotEqual(before_identity, self.compiled.identity)
                self.assert_state(new)
                seen.add((old, new))
                print(f"double-hop {mode} transition {old}->{new}: real local/ingress paths and listeners verified")
        states = ("00", "10", "01", "11")
        self.assertEqual(seen, {(left, right) for left in states for right in states if left != right})
        if mode == "reality":
            self.assertGreater(self.reference.snapshot()["accepts"], 0)
            print("double-hop REALITY reference/camouflage counts: " + repr(self.reference.snapshot()))


@unittest.skipUnless(os.getenv("VUI_PROXY_LOOPBACK") == "1",
                     "real double hop requires explicit VUI_PROXY_LOOPBACK=1")
class ProxyDoubleHopTests(DoubleHopFixture):
    def test_tls_four_states_and_all_twelve_transitions(self):
        self.transitions("tls")

    def test_reality_four_states_and_all_twelve_transitions(self):
        self.transitions("reality")

    def test_wrong_domestic_uuid_both_clients_never_reaches_upstream_or_target(self):
        self.start_upstream("tls")
        self.activate("11")
        self.start_clients()
        bad_clients = []
        for kind in ("singbox", "mihomo"):
            self.success(self.clients[kind][0], auth=None)
            port, process = self.start_client(kind, wrong_uuid=True)
            bad_clients.append((kind, port, process))
        with self.negative_phase():
            for kind, port, process in bad_clients:
                with self.subTest(client=kind):
                    self.rejection(self.negative_request(port, auth=None), self.ingress, ("unknown uuid:", WRONG_UUID))
                    process.stop()

    def upstream_failures(self, mode, failures):
        self.start_upstream(mode)
        self.activate("11")
        self.start_clients()
        for failure in failures:
            with self.subTest(upstream=mode, failure=failure):
                self.activate("11")
                self.success(self.local_port, auth=AUTH)
                env = None
                if failure == "ca":
                    other, _, _ = certificate_files(self.root, "untrusted")
                    env = {**self.env, "SSL_CERT_FILE": str(other)}
                self.activate("11", failure=failure, env=env)
                if failure == "uuid":
                    evidence, reasons = self.upstream, ("unknown uuid:", WRONG_UUID)
                elif failure == "flow":
                    evidence, reasons = self.upstream, ("flow mismatch:", "expected " + FLOW, "got none")
                elif failure in ("public_key", "short_id") or failure == "sni" and mode == "reality":
                    evidence, reasons = self.ingress, ("reality verification failed",)
                else:
                    evidence, reasons = self.ingress, ("x509", "unknown authority" if failure == "ca" else WRONG_SNI)
                with self.negative_phase():
                    for route, port, auth in (("local", self.local_port, AUTH),
                                             *( (kind, value[0], None) for kind, value in self.clients.items())):
                        with self.subTest(route=route):
                            self.rejection(self.negative_request(port, auth=auth), evidence, reasons)
                if mode == "reality":
                    self.assertGreater(self.reference.snapshot()["accepts"], 0)
                self.assertTrue(all(peer == UPSTREAM_EGRESS for peer, _, _ in self.answers.snapshot()))

    def test_tls_upstream_wrong_uuid_ca_and_sni_fail_closed(self):
        self.upstream_failures("tls", ("uuid", "ca", "sni"))

    def test_reality_upstream_wrong_uuid_public_key_short_id_sni_and_flow_fail_closed(self):
        self.upstream_failures("reality", ("uuid", "public_key", "short_id", "sni", "flow"))

    def test_local_authentication_and_exact_ipv4_loopback_binding(self):
        self.start_upstream("tls")
        self.activate("11")
        self.success(self.local_port, auth=AUTH)
        with self.negative_phase():
            for protocol in ("connect", "socks5h"):
                for auth in (None, (USERNAME, "wrong-password")):
                    with self.subTest(protocol=protocol, auth="missing" if auth is None else "wrong"):
                        before, dns = self.target.snapshot(), self.answers.snapshot()
                        with self.assertRaises(ProxyRejected) as caught:
                            self.negative_request(auth=auth, protocol=protocol)()
                        if protocol == "connect":
                            self.assertIn("407", str(caught.exception))
                        else:
                            self.assertIn("authentication" if auth is None else "username/password", str(caught.exception))
                        self.assertEqual(self.target.snapshot(), before)
                        self.assertEqual(self.answers.snapshot(), dns)
        # 127.0.0.2 is another local interface address: successful connection here
        # would expose a wildcard bind. IPv6 must remain closed in this phase.
        addresses = [UPSTREAM, "::1"]
        addresses += sorted({value.address for values in psutil.net_if_addrs().values() for value in values
                             if value.family == socket.AF_INET and not value.address.startswith("127.")})
        for address in addresses:
            with self.subTest(bind_address=address), self.assertRaises(ConnectionRefusedError):
                socket.create_connection((address, self.local_port), timeout=.5)
        local = next(item for item in self.compiled.config["inbounds"] if item["tag"] == LOCAL_TAG)
        self.assertEqual((local["listen"], local["set_system_proxy"]), (INGRESS, False))

    def test_stopped_killed_upstream_refuses_without_dns_or_direct_fallback_then_recovers(self):
        self.start_upstream("tls")
        self.activate("11")
        self.start_clients()
        for abrupt in (False, True):
            with self.subTest(abrupt=abrupt):
                self.success(self.local_port, auth=AUTH)
                if abrupt:
                    self.upstream.process.kill()
                    self.upstream.process.wait(timeout=5)
                    self.assertLess(self.upstream.process.returncode, 0)
                else:
                    self.upstream.stop()
                before_dns = self.answers.snapshot()
                with self.negative_phase():
                    for route, port, auth in (("local", self.local_port, AUTH),
                                             *( (kind, value[0], None) for kind, value in self.clients.items())):
                        with self.subTest(route=route):
                            host = "stopped-" + str(time.monotonic_ns()) + ".example.test"
                            self.rejection(self.negative_request(port, auth=auth, host=host), self.ingress,
                                           ("connect: connection refused",))
                self.assertEqual(self.answers.snapshot(), before_dns, "Business DNS escaped a stopped upstream")
                self.assertEqual(self.current_state, "11", "Failure changed the selected routing policy")
                self.start_process(self.upstream, self.upstream_port, UPSTREAM)
                self.success(self.local_port, auth=AUTH)
                self.success(self.clients["singbox"][0], auth=None)

    def test_full_local_accept_queue_proves_upstream_dial_timeout_without_fallback(self):
        self.start_upstream("tls")
        self.activate("11")
        self.success(self.local_port, auth=AUTH)
        self.upstream.stop()
        before_dns = self.answers.snapshot()
        # A deliberately unaccepted bounded loopback queue drops further SYNs
        # on the CI Linux host. This needs no firewall/network-setting changes
        # and proves a genuine upstream dial timeout rather than a caller wait.
        with ExitStack() as stall:
            listener = stall.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((UPSTREAM, self.upstream_port))
            listener.listen(1)
            filled, timed_out = 0, False
            for _ in range(4):
                pending = stall.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
                pending.settimeout(.25)
                try:
                    pending.connect((UPSTREAM, self.upstream_port))
                    filled += 1
                except socket.timeout:
                    timed_out = True
                    break
            self.assertGreater(filled, 0, "Controlled upstream queue did not accept its initial connections")
            self.assertTrue(timed_out, "Controlled upstream queue did not create an actual dial deadline")
            with self.negative_phase():
                self.rejection(self.negative_request(timeout=15), self.ingress,
                               ("dial tcp", "i/o timeout"), timeout=20)
            self.assertEqual(self.answers.snapshot(), before_dns)
            self.assertEqual(self.current_state, "11")
        self.start_process(self.upstream, self.upstream_port, UPSTREAM)
        self.success(self.local_port, auth=AUTH)

    def test_upstream_exit_interrupts_inflight_https_without_new_direct_delivery(self):
        self.start_upstream("tls")
        self.activate("11")
        self.success(self.local_port, auth=AUTH)
        before = len(self.target.snapshot())
        self.evidence.expect(self.target, UPSTREAM_EGRESS, SNI, "/hold-interruption")
        try:
            with tunnel(self.local_port, TARGET, self.target_port, auth=AUTH) as stream:
                context = ssl.create_default_context(cafile=str(self.ca))
                with context.wrap_socket(stream, server_hostname=SNI) as tls:
                    tls.sendall(b"GET /hold-interruption HTTP/1.1\r\nHost: vpn.example.test\r\nConnection: close\r\n\r\n")
                    response = http.client.HTTPResponse(tls)
                    response.begin()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(len(BODY)), BODY)
                    self.assertTrue(self.target.hold_started.wait(timeout=1))
                    self.assertEqual(len(self.target.snapshot()), before + 1)
                    self.assertEqual(self.target.snapshot()[-1][0], UPSTREAM_EGRESS)
                    self.upstream.process.kill()
                    self.upstream.process.wait(timeout=5)
                    self.assertLess(self.upstream.process.returncode, 0)
                    # An established, verified HTTPS body is truncated by the
                    # real process exit. A caller timeout alone cannot pass.
                    with self.assertRaises(http.client.IncompleteRead):
                        response.read()
        finally:
            self.target.release_hold.set()
        with self.negative_phase():
            self.rejection(self.negative_request(), self.ingress, ("connect: connection refused",))
        self.assertEqual(len(self.target.snapshot()), before + 1)
        self.assertEqual(self.current_state, "11")
        self.start_process(self.upstream, self.upstream_port, UPSTREAM)
        self.success(self.local_port, auth=AUTH)

    def test_business_dns_failure_has_no_direct_fallback_and_can_recover(self):
        self.start_upstream("tls")
        self.activate("11")
        self.success(self.local_port, auth=AUTH)
        for fault in ("servfail", "timeout"):
            # These faults share live processes. A failed phase stops them, so
            # abort this sequence rather than continuing against dead listeners.
            name = fault + ".example.test"
            self.ordinary_answers.hosts[name] = TARGET
            if fault == "servfail":
                self.answers.failed.add(name)
                reasons = (f"dns: lookup failed for {name}:", "exchange4: servfail", "exchange6: servfail")
            else:
                self.answers.delays.update({(name, 1): 12, (name, 28): 12})
                reasons = (f"dns: lookup failed for {name}:", "context deadline exceeded")
            with self.negative_phase(dns=True):
                self.rejection(self.negative_request(host=name, timeout=15), self.ingress, reasons, timeout=20)
            self.assertIn((UPSTREAM_EGRESS, name, 1), self.answers.snapshot())
            self.assertTrue(all(peer == UPSTREAM_EGRESS for peer, _, _ in self.answers.snapshot()))
            self.answers.failed.discard(name)
            self.answers.delays.pop((name, 1), None)
            self.answers.delays.pop((name, 28), None)
            self.success(self.local_port, auth=AUTH, host=name)

    def test_partial_dns_family_failure_uses_checked_successful_answers(self):
        self.start_upstream("tls")
        self.activate("11")
        for failure in ("servfail", "timeout"):
            with self.subTest(family="AAAA", failure=failure):
                name = "partial-aaaa-" + failure + ".example.test"
                if failure == "servfail":
                    self.answers.failed.add((name, 28))
                else:
                    self.answers.delays[(name, 28)] = 12
                self.success(self.local_port, auth=AUTH, host=name, timeout=15)
                self.assertIn((UPSTREAM_EGRESS, name, 28), self.answers.snapshot())
                self.assertEqual(self.answers.counts[(name, 1)], 1)
                self.assertEqual(self.ordinary_answers.queries, [])
        # Reverse direction uses a separate IPv6 loopback target. There is only
        # one bindable IPv6 loopback source on an unmodified host; actual second-
        # hop logs and DoH source counters prove that route independently.
        target_port, traffic = https_target(self.stack, "::1", self.cert, self.key)
        self.evidence.expect(traffic, "::1", SNI, "/ipv6-control")
        self.assertEqual(https_control("::1", target_port, self.ca, "ipv6-control"), (200, BODY))
        self.policy = AddressPolicy._for_tests(allowed_addresses=(TARGET, UPSTREAM, DOH, "::1"),
                                                doh=(DOH, self.doh_port, SNI))
        self.activate("11")
        for failure in ("servfail", "timeout"):
            with self.subTest(family="A", failure=failure):
                name = "partial-a-" + failure + ".example.test"
                self.answers.records[name] = ("::1",)
                if failure == "servfail":
                    self.answers.failed.add((name, 1))
                else:
                    self.answers.delays[(name, 1)] = 12
                before = len(traffic.snapshot())
                offset = len(self.upstream.log_path.read_text(errors="replace"))
                self.evidence.expect(traffic, "::1", name, "/" + name)
                self.evidence.expect_dns(name)
                self.assertEqual(https_through(self.local_port, name, target_port, self.ca, name,
                                 auth=AUTH, timeout=15), (200, BODY))
                self.assertEqual(traffic.snapshot()[before:], [("::1", name, "/" + name)])
                self.assertIn("[::1]:" + str(target_port),
                              self.upstream.log_path.read_text(errors="replace")[offset:])
                self.assertEqual(self.answers.counts[(name, 28)], 1)
                self.assertIn((UPSTREAM_EGRESS, name, 1), self.answers.snapshot())
                self.assertEqual(self.ordinary_answers.queries, [])

    def test_literal_domains_all_answers_and_rebinding_are_denied_before_dial(self):
        self.start_upstream("tls")
        self.activate("11")
        self.success(self.local_port, auth=AUTH)
        forbidden = (DENIED, INGRESS, UPSTREAM, "100.100.100.200", "169.254.169.254", "10.0.0.1",
                     "::1", "::ffff:" + DENIED, "::ffff:" + INGRESS, "::ffff:169.254.169.254",
                     "::ffff:100.100.100.200", "64:ff9b::7f00:3", "2001:db8::1")
        with self.negative_phase():
            for address in forbidden:
                with self.subTest(literal=address):
                    dns_before = self.answers.snapshot()
                    self.rejection(self.negative_request(host=address, target_port=self.denied_port),
                                   self.ingress, ("reject",))
                    self.assertEqual(self.answers.snapshot(), dns_before)
        with self.negative_phase(dns=True):
            for index, addresses in enumerate(((DENIED,), (TARGET, DENIED), (DENIED, TARGET),
                                               (TARGET, "::ffff:" + DENIED), ("100.100.100.200",),
                                               (INGRESS,), (UPSTREAM,), ("2001:db8::1",))):
                name = f"denied-{index}.example.test"
                self.answers.records[name] = addresses
                with self.subTest(resolved=addresses):
                    self.rejection(self.negative_request(host=name, target_port=self.denied_port),
                                   self.ingress, ("reject",))
                    self.assertTrue(any(peer == UPSTREAM_EGRESS and query == name for peer, query, _ in self.answers.snapshot()))
        # First answer is safe. A second lookup would return a forbidden host;
        # the first request must use exactly its checked result. The next
        # request sees the changed answer and must fail, with cache disabled.
        name = "rebind.example.test"
        self.answers.sequences[name] = ((TARGET,), (DENIED,))
        self.success(self.local_port, auth=AUTH, host=name)
        self.assertEqual(self.answers.counts[(name, 1)], 1, "Checked answer was resolved again before dialing")
        with self.negative_phase(dns=True):
            self.rejection(self.negative_request(host=name), self.ingress, ("reject",))
        self.assertGreaterEqual(self.answers.counts[(name, 1)], 2)

    def test_local_and_node_application_udp_are_explicitly_rejected(self):
        self.start_upstream("tls")
        self.activate("11")
        self.success(self.local_port, auth=AUTH)
        target_port, packets = udp_target(self.stack)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
            control.settimeout(2)
            control.sendto(b"reachable-control", (TARGET, target_port))
            self.assertEqual(control.recv(65535), b"VUI-UDP-CONTROL:reachable-control")
        before = [(INGRESS, b"reachable-control")]
        self.assertEqual(packets, before)
        self.evidence.udp.append((packets, Counter(before)))
        routes = [("local", self.local_port, AUTH)]
        for kind in ("singbox", "mihomo"):
            port, _ = self.start_client(kind, udp=True)
            routes.append((kind, port, None))
        with self.negative_phase():
            for route, port, auth in routes:
                with self.subTest(route=route):
                    self.rejection(lambda: udp_through(port, TARGET, target_port, b"must-not-arrive", auth=auth),
                                   self.ingress, ("network=udp", "reject"))
                    self.assertEqual(packets, before, "Application UDP reached the controlled target")
        self.assertEqual(self.current_state, "11")


if __name__ == "__main__":
    unittest.main()
