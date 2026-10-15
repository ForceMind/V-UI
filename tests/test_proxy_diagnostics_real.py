"""Opt-in, exact-core M3 worker acceptance against isolated fake services.

VUI_PROXY_LOOPBACK=1 enables runtime tests; missing pins, EPERM, startup errors,
timeouts without refusal evidence, and incomplete cleanup FAIL rather than skip.
No public service is contacted. A test-only child harness replaces only checked
loopback endpoints, temporary trust, and the fixed catalog's privileged TCP port.
The real worker main, subprocess supervisor, core, TLS, CONNECT and HTTP parser
run normally. Neither production API input nor production environment can enable
these substitutions. Fake-service observations do not establish an overseas IP.
"""
from __future__ import annotations

from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import functools
from http.server import BaseHTTPRequestHandler
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services import proxy_diagnostics as service
from app.services import proxy_diagnostic_worker as worker
from app.services.proxy_policy import AddressPolicy, VerifiedEndpoint, compile_proxy_policy
from app.services.upstream_profile import UpstreamProfile
from proxy_loopback_support import (
    BoundCoreProcess, BusinessAnswers, DOH, INGRESS, SNI, TARGET, UPSTREAM,
    UPSTREAM_EGRESS, Traffic, doh_target, listen_port, tracked_https_server,
)
from reality_helpers import SNI as REALITY_SNI, reference_certificates, start_reference_target
from test_reality_preflight_loopback import keypair, safe_log

PROFILE_ID = "up_" + "4" * 32
REVISION = "a" * 64
UUID = "22222222-2222-4222-8222-222222222222"
WRONG_UUID = "33333333-3333-4333-8333-333333333333"
SHORT_ID = "0123456789abcdef"
FLOW = "xtls-rprx-vision"
DENIED = "127.0.0.7"
_CATALOG = "api64.ipify.org"


def fixture_certificates(root):
    """One ephemeral CA, exact fake upstream/DoH/catalog SANs; no installed trust."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "VUI diagnostic fixture CA")])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
            encipher_only=False, decipher_only=False), True).sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, _CATALOG)]))
        .issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(hours=12))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(value) for value in
            (SNI, _CATALOG, "cloudflare-dns.com")]), False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
        .sign(ca_key, hashes.SHA256()))
    paths = root / "ca.pem", root / "cert.pem", root / "key.pem"
    paths[0].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths[1].write_bytes(cert.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM))
    paths[2].write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    for path in paths:
        path.chmod(0o600)
    return paths


class _CertificateTraffic(Traffic):
    """Test accessor for the tracked server's otherwise private TLS context."""
    def __init__(self):
        super().__init__()
        self._certificate_override = None

    def load_cert_chain(self, cert, key):
        # Wrong-certificate tests set this before opening a client connection.
        with self.lock:
            self._certificate_override = (cert, key)

    def start_tls(self, token, context):
        with self.lock:
            if self._certificate_override is not None:
                context.load_cert_chain(*self._certificate_override)
                self._certificate_override = None
        return super().start_tls(token, context)


def fake_ipify(stack, cert, key):
    traffic = _CertificateTraffic()
    state = {"response": "normal", "headers": [], "tls_context": traffic}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            with traffic.processing():
                traffic.append(self.client_address[0], self.headers.get("Host"), self.path)
                state["headers"].append(dict(self.headers.items()))
                kind = state["response"]
                body = json.dumps({"ip": self.client_address[0]}, separators=(",", ":")).encode()
                if kind == "malformed":
                    body = b'{"ip":"9.9.9.9","ip":"8.8.8.8"}'
                elif kind == "oversize":
                    body = b"x" * 65537
                elif kind == "hold":
                    body = b'{"ip":"'
                self.send_response(302 if kind == "redirect" else 200)
                if kind == "redirect":
                    self.send_header("Location", "https://127.0.0.7/must-not-follow")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(100 if kind == "hold" else len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self.wfile.write(body)
                    self.wfile.flush()
                    if kind == "hold":
                        traffic.hold_started.set()
                        traffic.release_hold.wait(timeout=15)
                except (OSError, ssl.SSLError):
                    pass
    port = tracked_https_server(stack, TARGET, Handler, cert, key, traffic)
    return port, traffic, state


def fixture_policy(manifest):
    return AddressPolicy._for_tests(allowed_addresses=(UPSTREAM, TARGET, DOH),
                                   doh=(DOH, manifest["doh_port"], "cloudflare-dns.com"))


def install_fixture(manifest):
    """Called only by this test file's explicit child entrypoint, never app code."""
    expected = {"target_port", "upstream_port", "doh_port", "core_log", "children_file"}
    if (type(manifest) is not dict or set(manifest) != expected
            or any(type(manifest[key]) is not int or not 1024 <= manifest[key] <= 65535
                   for key in ("target_port", "upstream_port", "doh_port"))):
        raise AssertionError("Invalid bounded fixture manifest")
    policy = fixture_policy(manifest)
    original_compile = worker.compile_proxy_policy
    original_config = worker._diagnostic_config
    original_spawn = worker._spawn
    def context(profile, profile_id, revision, panel_origin):
        if (profile.server != UPSTREAM or profile.server_port != manifest["upstream_port"]
                or profile_id != PROFILE_ID or revision != REVISION or panel_origin is not None):
            raise AssertionError("Fixture attempted an undeclared endpoint")
        addresses = policy.check_addresses((UPSTREAM,), self_addresses=(INGRESS,))
        return (INGRESS,), VerifiedEndpoint(profile_id, revision, profile.server, addresses, int(time.time()))
    def compile_fixture(*args, **kwargs):
        return original_compile(*args, **kwargs, policy=policy)
    def config_fixture(*args, **kwargs):
        config = original_config(*args, **kwargs)
        # Preserve the fixed host, SNI, path, method and response parser. Only
        # the fixture service's unprivileged TCP port differs from catalog v1.
        assert config["route"]["rules"][0] == {"domain": [_CATALOG], "invert": True, "action": "reject"}
        assert config["route"]["rules"][1] == {"port": [443], "invert": True, "action": "reject"}
        config["route"]["rules"][1]["port"] = [manifest["target_port"]]
        config["log"] = {"level": "debug", "timestamp": False, "output": manifest["core_log"]}
        return config
    def spawn_fixture(*args, **kwargs):
        child = original_spawn(*args, **kwargs)
        with open(manifest["children_file"], "a", encoding="ascii") as handle:
            handle.write(str(child.pid) + "\n")
        return child
    worker._address_context = context
    worker.compile_proxy_policy = compile_fixture
    worker._diagnostic_config = config_fixture
    worker._connect_proxy = functools.partial(worker._connect_proxy, _target_port=manifest["target_port"])
    worker._spawn = spawn_fixture
    # This is the worker's single-threaded child process, retaining the actual
    # production descendant parent-death setup rather than disabling cleanup.
    worker.__name__ = "__main__"


def fixture_child():
    if os.environ.get("VUI_PROXY_LOOPBACK") != "1" or len(sys.argv) != 5:
        return 64
    _, _, parent, directory, manifest_path = sys.argv
    raw = Path(manifest_path).read_bytes()
    if len(raw) > 4096:
        return 64
    install_fixture(json.loads(raw))
    sys.argv = [str(Path(worker.__file__)), parent, directory]
    return worker.main()


class DiagnosticFixtureHarnessTests(unittest.TestCase):
    def test_fixture_manifest_cannot_add_arbitrary_host_provider_or_policy(self):
        value = {"target_port": 18000, "upstream_port": 18001, "doh_port": 18002,
                 "core_log": "/unused", "children_file": "/unused"}
        for key in ("target_host", "provider", "url", "policy", "disable_checks"):
            with self.assertRaises(AssertionError):
                install_fixture({**value, key: "not allowed"})
        for port in (0, 443, 65536, True, "18000"):
            with self.assertRaises(AssertionError):
                install_fixture({**value, "target_port": port})
        policy = fixture_policy(value)
        self.assertEqual(policy.check_addresses((TARGET,)), (TARGET,))
        from app.services.proxy_policy import PolicyError
        for target in ("127.0.0.8", "10.0.0.1", "169.254.169.254"):
            with self.assertRaises(PolicyError):
                policy.check_addresses((target,))

    def check_accepted_connection_lifecycle(self, complete_tls):
        with tempfile.TemporaryDirectory() as directory:
            ca, cert, key = fixture_certificates(Path(directory))
            stack = ExitStack()
            port, traffic, _ = fake_ipify(stack, cert, key)
            peer = socket.create_connection((TARGET, port), timeout=2)
            closing, outcome = None, {}
            try:
                if complete_tls:
                    context = ssl.create_default_context(cafile=str(ca))
                    peer = context.wrap_socket(peer, server_hostname=_CATALOG)
                    peer.sendall(b"GET /?format=json HTTP/1.1\r\nHost: api64.ipify.org\r\n")
                deadline = time.monotonic() + 1
                while not traffic.pending() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertGreater(traffic.pending(), 0,
                                   "Accepted TLS or incomplete HTTP must not look idle")
                self.assertEqual(traffic.snapshot(), [])
                def close():
                    try:
                        stack.close()
                    except BaseException as error:
                        outcome["error"] = error
                closing = threading.Thread(target=close)
                closing.start()
                closing.join(timeout=2)
                completed_while_peer_open = not closing.is_alive()
            finally:
                peer.close()
                if closing is None:
                    stack.close()
                else:
                    closing.join(timeout=4)
            self.assertTrue(completed_while_peer_open, "Fixture close waited for an untracked peer to finish")
            self.assertNotIn("error", outcome)
            self.assertFalse(closing.is_alive())
            self.assertEqual(traffic.pending(), 0)
            self.assertEqual(traffic.snapshot(), [], "Closing incomplete HTTP created a late application delivery")

    def test_accepted_pre_tls_socket_is_pending_and_drained_at_close(self):
        self.check_accepted_connection_lifecycle(False)

    def test_accepted_partial_http_is_pending_and_drained_at_close(self):
        self.check_accepted_connection_lifecycle(True)

    def test_tracked_certificate_accessor_preserves_hostname_rejection(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            ca, cert, key = fixture_certificates(root)
            reference_ca, reference_cert, reference_key = reference_certificates(root)
            port, traffic, state = fake_ipify(stack, cert, key)
            state["tls_context"].load_cert_chain(reference_cert, reference_key)
            trust = root / "combined-ca.pem"
            trust.write_bytes(ca.read_bytes() + reference_ca.read_bytes())
            context = ssl.create_default_context(cafile=str(trust))
            with socket.create_connection((TARGET, port), timeout=2) as peer:
                with self.assertRaises(ssl.SSLCertVerificationError):
                    context.wrap_socket(peer, server_hostname=_CATALOG)
            stack.close()
            self.assertEqual(traffic.pending(), 0)
            self.assertEqual(traffic.snapshot(), [])

    def test_real_child_harness_strict_request_handshake_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {"target_port": 18000, "upstream_port": 18001, "doh_port": 18002,
                        "core_log": str(root / "core.log"), "children_file": str(root / "children")}
            path = root / "fixture.json"
            path.write_text(json.dumps(manifest))
            command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--fixture-worker",
                       str(os.getpid()), directory, str(path)]
            # Malformed shape is rejected before context, sockets or core spawn.
            result = subprocess.run(command, input=b'{"url":"must-not-connect"}', capture_output=True,
                                    timeout=5, env={**os.environ, "VUI_PROXY_LOOPBACK": "1"})
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            self.assertEqual(json.loads(result.stdout), {"event": "result", "code": "INVALID_DIAGNOSTIC_REQUEST", "exit_ip": None})
            self.assertFalse((root / "children").exists())
            self.assertFalse((root / "core.log").exists())


@unittest.skipUnless(os.getenv("VUI_PROXY_LOOPBACK") == "1", "real diagnostics require explicit VUI_PROXY_LOOPBACK=1")
class DiagnosticRealWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get("VUI_TEST_CORES")
        if not configured:
            raise AssertionError("Enabled real diagnostics require VUI_TEST_CORES")
        cls.cores = Path(configured).resolve()
        cls.binary = cls.cores / "sing-box"
        result = subprocess.run([str(cls.binary), "version"], capture_output=True, text=True, timeout=5)
        if result.returncode or "sing-box version 1.14.2" not in result.stdout or "with_utls" not in result.stdout:
            raise AssertionError("Required official sing-box 1.14.2/with_utls is unavailable")
        print("diagnostic pin: " + result.stdout.splitlines()[0])

    def setUp(self):
        self.traffic = self.answers = None
        self.expected_deliveries, self.expected_dns = Counter(), Counter()
        # LIFO cleanup: finish producers/drain, close and join all fixture
        # connections/listeners, then audit immutable lifetime expectations.
        self.addCleanup(self.final_evidence_audit)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="vui-real-diagnostic-")))
        self.ca, self.cert, self.key = fixture_certificates(self.root)
        ref_ca, ref_cert, ref_key = reference_certificates(self.root)
        self.reference_cert, self.reference_key = ref_cert, ref_key
        trust = self.root / "trust.pem"
        trust.write_bytes(self.ca.read_bytes() + ref_ca.read_bytes())
        trust.chmod(0o600)
        empty = self.root / "empty-roots"
        empty.mkdir()
        self.env = {**os.environ, "SSL_CERT_FILE": str(trust), "SSL_CERT_DIR": str(empty)}
        self.private, self.public = keypair()
        _, self.wrong_public = keypair()
        self.reference_port, self.reference = start_reference_target(self.stack, ref_cert, ref_key)
        self.target_port, self.traffic, self.response = fake_ipify(self.stack, self.cert, self.key)
        self.answers = BusinessAnswers()
        self.answers.records[_CATALOG] = (TARGET,)
        self.doh_port = doh_target(self.stack, self.answers, self.cert, self.key)
        self.upstream_port = listen_port(UPSTREAM)
        self.manifest = {"target_port": self.target_port, "upstream_port": self.upstream_port,
                         "doh_port": self.doh_port, "core_log": str(self.root / "diagnostic.log"),
                         "children_file": str(self.root / "children.txt")}
        self.manifest_path = self.root / "fixture.json"
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.manifest_path.chmod(0o600)
        self.expected_deliveries = Counter()
        self.worker_processes = []
        self.upstream = None
        self.addCleanup(self.finish_evidence)

    def sanitized(self, value):
        return safe_log(value, (self.private, self.public, self.wrong_public, UUID, WRONG_UUID))

    def start_upstream(self, mode):
        tls = {"enabled": True, "server_name": SNI, "certificate_path": str(self.cert), "key_path": str(self.key)}
        user = {"uuid": UUID}
        if mode == "reality":
            user["flow"] = FLOW
            tls = {"enabled": True, "server_name": REALITY_SNI, "reality": {
                "enabled": True, "handshake": {"server": INGRESS, "server_port": self.reference_port},
                "private_key": self.private, "short_id": [SHORT_ID]}}
        config = {"log": {"level": "debug", "timestamp": False}, "inbounds": [{"type": "vless",
            "tag": "fixture-upstream", "listen": UPSTREAM, "listen_port": self.upstream_port,
            "users": [user], "tls": tls}], "outbounds": [{"type": "direct", "tag": "fixture-exit",
            "inet4_bind_address": UPSTREAM_EGRESS}], "route": {"final": "fixture-exit"}}
        path, log = self.root / "upstream.json", self.root / "upstream.log"
        path.write_text(json.dumps(config)); path.chmod(0o600)
        result = subprocess.run([str(self.binary), "check", "-c", str(path)], env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, self.sanitized(result.stdout + result.stderr))
        self.upstream = BoundCoreProcess(self.stack, [str(self.binary), "run", "-c", str(path)], log, self.env)
        try:
            self.upstream.start(self.upstream_port, UPSTREAM)
        except AssertionError as error:
            raise AssertionError(self.sanitized(str(error))) from None
        return self.profile(mode)

    def profile(self, mode):
        tls = {"mode": "tls", "server_name": SNI, "alpn": [], "fingerprint": "", "public_key": "", "short_id": ""}
        if mode == "reality":
            tls.update(mode="reality", server_name=REALITY_SNI, fingerprint="chrome", public_key=self.public, short_id=SHORT_ID)
        return {"schema_version": 1, "protocol": "vless", "name": "bounded fixture", "server": UPSTREAM,
                "server_port": self.upstream_port, "uuid": UUID, "transport": "tcp", "encryption": "none",
                "flow": FLOW if mode == "reality" else "", "tls": tls}

    def run_worker(self, profile, cancel=None):
        from app.services import core_manager
        original_popen = subprocess.Popen
        def launch(command, **kwargs):
            self.assertEqual(Path(command[3]).name, "proxy_diagnostic_worker.py")
            command = [*command[:3], str(Path(__file__).resolve()), "--fixture-worker", *command[4:], str(self.manifest_path)]
            kwargs["env"] = self.env
            child = original_popen(command, **kwargs)
            self.worker_processes.append(child)
            return child
        events = []
        payload = {"profile": profile, "profile_id": PROFILE_ID, "revision": REVISION, "panel_origin": None}
        with tempfile.TemporaryDirectory(dir=self.root, prefix="private-job-") as directory, \
                patch.object(core_manager, "BIN_DIR", self.cores), patch.object(service.subprocess, "Popen", launch):
            result = service._run_worker(payload, time.monotonic() + 30, cancel or threading.Event(), events.append, directory)
            self.assertTrue((Path(directory) / "diagnostic.json").is_file())
        self.assertFalse(Path(directory).exists())
        return result, events

    def expect_dns(self):
        self.expected_dns.update(((UPSTREAM_EGRESS, _CATALOG, 1), (UPSTREAM_EGRESS, _CATALOG, 28)))

    def expect_request(self):
        self.expected_deliveries[(UPSTREAM_EGRESS, _CATALOG, "/?format=json")] += 1
        self.expect_dns()

    def assert_dns_path(self):
        self.assertEqual(Counter(self.answers.snapshot()), self.expected_dns,
                         "Unexpected, duplicate, late, wrong-source or wrong-name diagnostic DNS")

    def final_evidence_audit(self):
        # This runs after ExitStack.close, including listener and tracked TLS/
        # HTTP worker closure. An earlier quiet interval cannot hide late work.
        if self.traffic is not None:
            self.assertEqual(self.traffic.pending(), 0, "Accepted target work survived fixture close")
            self.assertEqual(Counter(self.traffic.snapshot()), self.expected_deliveries,
                             "Late or unauthorized delivery appeared during fixture shutdown")
        if self.answers is not None:
            self.assertEqual(self.answers.pending(), 0, "Accepted DoH work survived fixture close")
            self.assert_dns_path()

    def finish_evidence(self):
        self.traffic.release_hold.set()
        if self.upstream is not None:
            self.upstream.stop()
        self.assertTrue(all(child.poll() is not None for child in self.worker_processes), "Diagnostic worker still running")
        children = Path(self.manifest["children_file"])
        if children.exists():
            import psutil
            for line in children.read_text().splitlines():
                self.assertFalse(psutil.pid_exists(int(line)), "Diagnostic descendant was not reaped")
        deadline, quiet = time.monotonic() + 3, None
        while True:
            self.assertEqual(Counter(self.traffic.snapshot()), self.expected_deliveries,
                             "Unexpected, duplicate, late or DIRECT diagnostic target delivery")
            self.assert_dns_path()
            if self.traffic.pending() == 0 and self.answers.pending() == 0:
                quiet = quiet or time.monotonic()
                if time.monotonic() - quiet >= .2:
                    break
            else:
                quiet = None
            if time.monotonic() >= deadline:
                self.fail("Diagnostic fixture callbacks did not drain")
            time.sleep(.02)
        for headers in self.response["headers"]:
            lowered = {key.lower(): value for key, value in headers.items()}
            self.assertNotIn("proxy-authorization", lowered)
            self.assertNotIn("authorization", lowered)
            self.assertNotIn("cookie", lowered)
            self.assertNotIn(UUID, json.dumps(headers))

    def refusal_reason(self, path, *reasons):
        text = Path(path).read_text(errors="replace").lower() if Path(path).exists() else ""
        found = [line for line in text.splitlines() if all(reason.lower() in line for reason in reasons)]
        self.assertTrue(found, "Missing actual refusal reason: " + self.sanitized(text))
        print("diagnostic refusal: " + self.sanitized(found[-1]))

    def positive(self, mode):
        profile = self.start_upstream(mode)
        self.expect_request()
        result, events = self.run_worker(profile)
        self.assertEqual(result, {"code": "DIAGNOSTIC_COMPLETE", "exit_ip": UPSTREAM_EGRESS})
        by_stage = {event["stage"]: event for event in events if event["event"] == "stage"}
        for stage in ("configuration", "tcp", "tls_or_reality", "proxy_forwarding", "https_exit"):
            self.assertEqual(by_stage[stage]["status"], "passed")
        self.assertEqual(by_stage["dns"]["status"], "insufficient_evidence")
        self.assertEqual(by_stage["dns"]["code"], "DNS_PATH_BOUND_TO_HTTPS")
        self.assertNotIn("external_client", by_stage)
        self.assert_dns_path()
        if mode == "reality":
            self.assertEqual(by_stage["tls_or_reality"]["code"], "REALITY_AUTHENTICATED_BY_HTTPS")
            self.assertGreater(self.reference.snapshot()["accepts"], 0)
        self.assertNotIn(UUID, json.dumps(events))
        print("diagnostic " + mode + ": real isolated worker, proxy HTTPS, observed fixture exit, limited DNS and cleanup")

    def test_tls_worker_positive(self):
        self.positive("tls")

    def test_reality_worker_positive(self):
        self.positive("reality")

    def test_tls_wrong_uuid_rejected_with_real_reason_and_zero_delivery(self):
        profile = self.start_upstream("tls")
        profile["uuid"] = WRONG_UUID
        result, events = self.run_worker(profile)
        self.assertNotEqual(result["code"], "DIAGNOSTIC_COMPLETE")
        self.assertIsNone(result["exit_ip"])
        self.assertFalse(any(event.get("stage") == "proxy_forwarding" and event.get("status") == "passed" for event in events))
        self.assertEqual(self.answers.snapshot(), [])
        self.refusal_reason(self.root / "upstream.log", "unknown uuid")

    def test_reality_bad_key_rejected_with_real_reason_and_zero_delivery(self):
        profile = self.start_upstream("reality")
        profile["tls"]["public_key"] = self.wrong_public
        result, events = self.run_worker(profile)
        self.assertNotEqual(result["code"], "DIAGNOSTIC_COMPLETE")
        self.assertFalse(any(event.get("stage") == "tls_or_reality" and event.get("status") == "passed" for event in events))
        self.assertEqual(self.answers.snapshot(), [])
        self.refusal_reason(self.manifest["core_log"], "reality verification failed")

    def test_tls_wrong_sni_fails_before_any_business_request(self):
        profile = self.start_upstream("tls")
        profile["tls"]["server_name"] = "untrusted.example.test"
        result, events = self.run_worker(profile)
        self.assertEqual(result, {"code": "TLS_FAILED", "exit_ip": None})
        self.assertTrue(any(event.get("stage") == "tls_or_reality" and event.get("status") == "failed" for event in events))
        self.assertEqual(self.answers.snapshot(), [])

    def test_business_https_certificate_name_is_verified(self):
        profile = self.start_upstream("tls")
        # The reference CA is trusted, but its SANs deliberately do not include
        # api64.ipify.org. Rejection therefore proves target hostname checking.
        self.response["tls_context"].load_cert_chain(self.reference_cert, self.reference_key)
        self.expect_dns()
        result, events = self.run_worker(profile)
        self.assertEqual(result, {"code": "HTTPS_FAILED", "exit_ip": None})
        self.assertFalse(any(event.get("stage") == "https_exit" and event.get("status") == "passed" for event in events))
        self.assert_dns_path()

    def test_business_doh_failure_has_no_direct_target_fallback(self):
        profile = self.start_upstream("tls")
        self.answers.failed.add(_CATALOG)
        self.expect_dns()
        result, _ = self.run_worker(profile)
        self.assertNotEqual(result["code"], "DIAGNOSTIC_COMPLETE")
        self.assert_dns_path()
        self.refusal_reason(self.manifest["core_log"], "server failure")

    def test_redirect_is_not_followed(self):
        profile = self.start_upstream("tls")
        self.response["response"] = "redirect"
        self.expect_request()
        result, _ = self.run_worker(profile)
        self.assertEqual(result, {"code": "HTTP_STATUS_REJECTED", "exit_ip": None})
        self.assert_dns_path()

    def test_duplicate_json_response_is_rejected(self):
        profile = self.start_upstream("tls")
        self.response["response"] = "malformed"
        self.expect_request()
        result, _ = self.run_worker(profile)
        self.assertEqual(result, {"code": "EXIT_IP_INVALID", "exit_ip": None})
        self.assert_dns_path()

    def test_oversized_response_is_rejected(self):
        profile = self.start_upstream("tls")
        self.response["response"] = "oversize"
        self.expect_request()
        result, _ = self.run_worker(profile)
        self.assertEqual(result, {"code": "HTTP_RESPONSE_LIMIT", "exit_ip": None})
        self.assert_dns_path()

    def test_mixed_allowed_and_denied_business_dns_rejects_entire_target(self):
        profile = self.start_upstream("tls")
        self.answers.records[_CATALOG] = (TARGET, DENIED)
        self.expect_dns()
        result, _ = self.run_worker(profile)
        self.assertNotEqual(result["code"], "DIAGNOSTIC_COMPLETE")
        self.assert_dns_path()
        self.refusal_reason(self.manifest["core_log"], "reject")

    def test_cancellation_during_real_https_body_reaps_worker_and_core(self):
        profile = self.start_upstream("tls")
        self.response["response"] = "hold"
        self.expect_request()
        cancel, outcome = threading.Event(), {}
        def run():
            try:
                outcome["result"] = self.run_worker(profile, cancel)
            except BaseException as error:
                outcome["error"] = error
        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(self.traffic.hold_started.wait(15), "Actual HTTPS body was not reached")
            cancel.set()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive(), "Cancellation did not finish cleanup")
            self.assertIsInstance(outcome.get("error"), service.DiagnosticsError)
            self.assertEqual(outcome["error"].code, "DIAGNOSTIC_CANCELLED")
            self.assert_dns_path()
        finally:
            cancel.set()
            self.traffic.release_hold.set()
            thread.join(timeout=3)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--fixture-worker":
        raise SystemExit(fixture_child())
    unittest.main()
