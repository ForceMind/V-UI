"""Fixed-core strict export parsing and separately opt-in controlled real paths.

VUI_PROXY_EXPORT_REAL=1 enables parser checks. VUI_PROXY_LOOPBACK=1 enables real
committed runtime + exported clients + TLS/REALITY upstream + observed HTTPS/DoH.
Enabled tests fail, never skip, on missing pins or host restrictions. Temporary
CA trust and loopback fixture endpoints do not alter system/network settings.
"""
import copy
from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import threading
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml
import psutil

from app.services.core_manager import SingBoxAdapter
from app.services.core_runtime import CoreRuntime
from app.services.proxy_apply import ProxyApplyCoordinator
from app.services.proxy_export import StrictExportError, StrictRelayExporter
from app.services.proxy_policy import VerifiedEndpoint, compile_proxy_policy
from app.services.proxy_store import ProxyStore
from loopback_helpers import _recv_exact, certificate_files, dns_query
from proxy_loopback_support import (DENIED, DOH, INGRESS, SNI, TARGET, UPSTREAM, UPSTREAM_EGRESS,
                                    BusinessAnswers, doh_target, listen_port, udp_target, udp_through)
from test_proxy_double_hop import DoubleHopFixture, INGRESS_UUID, UPSTREAM_UUID, FLOW, SHORT_ID, REALITY_SNI
from test_proxy_exports import ExportFixture
from tests.test_proxy_store import profile, writable_settings


WRONG_DNS_IDENTITY = "wrong.example.test"


def dns_rejection_environment(environment, kind, wrong_dns_identity):
    env = environment.copy()
    if kind == "sing-box" and wrong_dns_identity:
        # Pinned 1.14.2 dns/router.go finishExchangeAsync forwards errors without
        # logging them; protocol/dns/handle.go cancels the UDP request. Its DoH
        # transport uses x/net v0.57.0 HTTP/2, whose transport_common.go logs the
        # actual TLS dial failure with http2debug=1. Enable that only for this
        # fake-credential negative child, not the runtime or positive clients.
        # https://github.com/SagerNet/sing-box/blob/v1.14.2/dns/transport/https_transport.go
        # https://github.com/golang/net/blob/v0.57.0/http2/transport_common.go
        debug = [flag for flag in env.get("GODEBUG", "").split(",")
                 if flag and not flag.startswith("http2debug=")]
        env["GODEBUG"] = ",".join([*debug, "http2debug=1"])
    return env


class StrictExportEvidenceFixtureTests(unittest.TestCase):
    """Evidence-helper controls; these do not replace fixed-client acceptance."""
    def test_http2_diagnostics_are_scoped_to_wrong_identity_singbox_child(self):
        original = {"GODEBUG": "gctrace=0,http2debug=2,http2debug=0",
                    "SSL_CERT_FILE": "/fixture/trust.pem", "SSL_CERT_DIR": "/fixture/empty"}
        before = original.copy()
        for kind in ("sing-box", "mihomo"):
            for wrong in (False, True):
                with self.subTest(kind=kind, wrong=wrong):
                    result = dns_rejection_environment(original, kind, wrong)
                    expected = {**original, "GODEBUG": "gctrace=0,http2debug=1"} if kind == "sing-box" and wrong else original
                    self.assertEqual(result, expected)
                    self.assertIsNot(result, original)
                    self.assertEqual(original, before)
        self.assertEqual(dns_rejection_environment({}, "sing-box", True), {"GODEBUG": "http2debug=1"})

    def test_repeated_client_labels_have_distinct_configuration_and_log_paths(self):
        fixture = DoubleHopFixture()
        fixture.root, fixture.sequence = Path("/fixture"), 0
        paths = [fixture.path("strict-sing-box", suffix) for suffix in ("json", "log", "json", "log")]
        self.assertEqual(len(set(paths)), 4)
        self.assertNotEqual(paths[1], paths[3])

    def test_controlled_doh_certificate_accepts_expected_name_and_rejects_wrong_name(self):
        with ExitStack() as stack:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="vui-strict-dns-identity-")))
            ca, cert, key = certificate_files(root)
            answers = BusinessAnswers()
            port = doh_target(stack, answers, cert, key)
            context = ssl.create_default_context(cafile=str(ca))
            with socket.create_connection((DOH, port), timeout=2) as raw:
                with context.wrap_socket(raw, server_hostname=SNI) as peer:
                    self.assertTrue(peer.getpeercert())
            with socket.create_connection((DOH, port), timeout=2) as raw:
                with self.assertRaises(ssl.SSLCertVerificationError) as caught:
                    context.wrap_socket(raw, server_hostname=WRONG_DNS_IDENTITY)
            self.assertIn("Hostname mismatch", caught.exception.verify_message)
            self.assertIn(WRONG_DNS_IDENTITY, caught.exception.verify_message)
            self.assertEqual(answers.snapshot(), [], "A rejected TLS identity sent a DoH application query")


@unittest.skipUnless(os.getenv("VUI_PROXY_EXPORT_REAL") == "1" or os.getenv("VUI_PROXY_LOOPBACK") == "1",
                     "explicit strict-export parser or loopback opt-in required")
class StrictExportParserTests(ExportFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Pin checks require both binaries when enabled; no capability skips.
        DoubleHopFixture.setUpClass.__func__(cls)

    def setUp(self):
        self.setup_export()

    def test_unmodified_exports_parse_on_both_pinned_clients(self):
        self.commit()
        for optional in (False, True):
            if optional:
                self.rows[0].stream_settings["tls"]["alpn"] = ["h2", "http/1.1"]
                self.rows[0].stream_settings["_vui"] = {"security": "tls", "server_name": "node.example",
                    "client_fingerprint": "chrome", "skip_cert_verify": False}
                self.coordinator.reconcile(self.rows)
            for kind in ("sing-box", "mihomo"):
                with self.subTest(optional=optional, kind=kind):
                    artifact = self.exported(kind)
                    root = self.store.path.parent
                    path = root / artifact.filename
                    path.write_text(artifact.content)
                    path.chmod(0o600)
                    command = ([self.singbox, "check", "-c", str(path)] if kind == "sing-box" else
                               [self.mihomo, "-t", "-d", str(root), "-f", str(path)])
                    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    print("strict export fixed parser accepted:", kind, "optional TLS:", optional)


@unittest.skipUnless(os.getenv("VUI_PROXY_LOOPBACK") == "1", "explicit strict-export loopback opt-in required")
class StrictExportLoopbackTests(DoubleHopFixture):
    def committed_relay(self, mode="tls"):
        self.start_upstream(mode)
        self.rows = [SimpleNamespace(id=1, core="sing-box", protocol="vless", port=self.ingress_port,
            remark="strict fixture ingress", tag="fixture-ingress", enable=True, expiry_time=0,
            settings={"users": [{"uuid": INGRESS_UUID}]}, stream_settings={"tls": {
                "enabled": True, "server_name": SNI,
                "certificate_path": str(self.cert), "key_path": str(self.key)}})]
        self.store = ProxyStore(self.root / "store")
        self.runtime = CoreRuntime("sing-box", Path(self.singbox), self.root / "runtime")
        self.addCleanup(self.runtime.close)
        # CoreRuntime launches the unchanged binary with its ordinary lifecycle.
        # Trust is scoped to these test subprocesses, never installed globally.
        trust = patch.dict(os.environ, {key: self.env[key] for key in ("SSL_CERT_FILE", "SSL_CERT_DIR")})
        trust.start()
        self.addCleanup(trust.stop)
        self.ingress = SimpleNamespace(log_path=self.root / "committed-ingress.log")
        lock = threading.RLock()
        def build(rows):
            config = SingBoxAdapter().build_config(rows)
            config["log"] = {"level": "debug", "output": str(self.ingress.log_path)}
            return config
        def compile_fixture(*args, **kwargs):
            compiled = compile_proxy_policy(*args, **kwargs, policy=self.policy)
            config = copy.deepcopy(compiled.config)
            for inbound in config["inbounds"]:
                self.assertEqual(inbound["listen"], "::")
                inbound["listen"] = INGRESS  # Narrow after exact source binding.
            return replace(compiled, config=config)
        self.coordinator = ProxyApplyCoordinator(self.store, self.runtime, build, lock,
            rows_provider=lambda: self.rows, address_provider=lambda _: (INGRESS,),
            compiler=compile_fixture,
            resolver=lambda pid, rev, value, **kw: VerifiedEndpoint(pid, rev, value["server"], (UPSTREAM,), 1))
        stopped_xray = SimpleNamespace(state=lambda: {"revision": None, "enabled": False},
                                       status=lambda: {"running": False})
        manager = SimpleNamespace(lock=lock, proxy_coordinator=self.coordinator, _all_rows=lambda: self.rows,
            get=lambda name: SimpleNamespace(runtime=self.runtime if name == "sing-box" else stopped_xray))
        self.exporter = StrictRelayExporter(manager)
        upstream = profile()
        upstream.update(server="upstream.fixture.test", server_port=self.upstream_port, uuid=UPSTREAM_UUID)
        upstream["tls"]["server_name"] = SNI
        if mode == "reality":
            upstream["flow"] = FLOW
            upstream["tls"].update(mode="reality", server_name=REALITY_SNI, fingerprint="chrome",
                                   public_key=self.public_key, short_id=SHORT_ID)
        added = self.store.save_upstream(upstream, self.store.read_snapshot()["revision"])
        settings = writable_settings(added["snapshot"])
        settings.update(relay_enabled=True, selected_upstream_id=added["upstream_id"])
        saved = self.store.save_draft(settings, added["snapshot"]["revision"])
        self.rev = saved["revision"]
        result = self.coordinator.apply(self.rev, "strict-runtime-" + mode)
        self.assertTrue(result["applied"])
        self.assertEqual(result["phase"], "committed")
        self.assertEqual(self.store.load_active()["core_revision"], self.runtime.state()["revision"])

    def exported_client(self, kind, *, wrong_dns_identity=False):
        # Production insists on a public ingress IP. Only this isolated fixture
        # replaces it with loopback, and replaces fixed DoH IP/port/TLS identity
        # with the controlled equivalent. Routing, detours and TLS remain intact.
        artifact = self.exporter.export(kind, self.rev, 1, "8.8.8.8")
        self.assertNotIn(UPSTREAM_UUID, artifact.content)
        config = yaml.safe_load(artifact.content)
        port = listen_port()
        dns_port = listen_port()
        if kind == "sing-box":
            config["inbounds"][0]["listen_port"] = port
            config["inbounds"][1]["listen_port"] = dns_port
            config["outbounds"][0]["server"] = INGRESS
            server = config["dns"]["servers"][0]
            server.update(server=DOH, server_port=self.doh_port)
            server["tls"]["server_name"] = WRONG_DNS_IDENTITY if wrong_dns_identity else SNI
            config["log"] = {"level": "debug"}
        else:
            config["mixed-port"] = port
            config["proxies"][0]["server"] = INGRESS
            config["dns"]["listen"] = f"{INGRESS}:{dns_port}"
            for name in ("nameserver", "default-nameserver", "proxy-server-nameserver"):
                config["dns"][name] = [value.replace("1.1.1.1", DOH + ":" + str(self.doh_port))
                    .replace("cloudflare-dns.com", WRONG_DNS_IDENTITY if wrong_dns_identity else SNI)
                    for value in config["dns"][name]]
            config["log-level"] = "debug"
        self.assertNotIn("DIRECT", json.dumps(config))
        process = self.process("strict-" + kind, config, client="mihomo" if kind == "mihomo" else "singbox",
                               env=dns_rejection_environment(self.env, kind, wrong_dns_identity))
        self.start_process(process, port)
        # DNS is an explicit local facility, not a wildcard/system listener.
        with socket.create_connection((INGRESS, dns_port), timeout=2):
            pass
        sockets = psutil.Process(process.process.pid).net_connections(kind="inet")
        listeners = [item for item in sockets if item.laddr and item.laddr.port == dns_port]
        self.assertEqual({item.laddr.ip for item in listeners}, {INGRESS})
        self.assertEqual({item.type for item in listeners}, {socket.SOCK_STREAM, socket.SOCK_DGRAM})
        return port, process, dns_port

    def dns_request(self, port, name, *, success):
        packet = dns_query(name, 1)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(3)
            client.sendto(packet, (INGRESS, port))
            response, _ = client.recvfrom(65535)
        self.assertEqual(response[:2], packet[:2])
        rcode, answers = response[3] & 15, struct.unpack("!H", response[6:8])[0]
        if rcode or answers == 0:
            raise OSError("Explicit DNS query refused")
        if success:
            self.assertIn(socket.inet_aton(TARGET), response)
        return response

    def client_success(self, kind, client):
        port, _, dns_port = client
        if dns_port is not None:
            host = "client-dns-" + str(time.monotonic_ns()) + ".example.test"
            self.evidence.dns[(UPSTREAM_EGRESS, host, 1)] += 1
            self.dns_request(dns_port, host, success=True)
            self.evidence.assert_exact(self)
            host = "client-tcp-dns-" + str(time.monotonic_ns()) + ".example.test"
            self.evidence.dns[(UPSTREAM_EGRESS, host, 1)] += 1
            packet = dns_query(host, 1)
            with socket.create_connection((INGRESS, dns_port), timeout=3) as stream:
                stream.sendall(struct.pack("!H", len(packet)) + packet)
                response = _recv_exact(stream, struct.unpack("!H", _recv_exact(stream, 2))[0])
            self.assertEqual(response[:2], packet[:2])
            self.assertEqual(response[3] & 15, 0)
            self.assertIn(socket.inet_aton(TARGET), response)
            self.evidence.assert_exact(self)
        self.success(port, auth=None, protocol="socks5h")
        self.assertEqual(self.ordinary_answers.queries, [])
        print("strict export real DNS and HTTPS observed via both hops:", kind)

    def test_tls_exports_use_committed_runtime_and_verified_dns_https(self):
        self.committed_relay()
        for kind in ("sing-box", "mihomo"):
            self.client_success(kind, self.exported_client(kind))

    def test_reality_exports_never_share_overseas_secrets_and_forward_both_clients(self):
        self.committed_relay("reality")
        for kind in ("sing-box", "mihomo"):
            self.client_success(kind, self.exported_client(kind))

    def test_upstream_failure_keeps_exported_clients_without_dns_or_direct_fallback(self):
        self.committed_relay()
        clients = {kind: self.exported_client(kind) for kind in ("sing-box", "mihomo")}
        for kind, client in clients.items():
            self.client_success(kind, client)
        self.upstream.stop()
        before_dns = self.answers.snapshot()
        with self.negative_phase():
            for kind, (port, process, dns_port) in clients.items():
                self.rejection(self.negative_request(port, auth=None, host="offline-" + kind + ".example.test"),
                               self.ingress, ("connect: connection refused",))
                if dns_port is not None:
                    self.rejection(lambda: self.dns_request(dns_port, "offline-dns.example.test", success=False),
                                   self.ingress, ("connect: connection refused",))
        self.assertEqual(self.answers.snapshot(), before_dns)
        # The negative-phase drain deliberately stops the runtime; export must
        # reject the now-stopped committed state, rather than serving old green.
        with self.assertRaisesRegex(StrictExportError, "RUNTIME_UNAVAILABLE"):
            self.exporter.export("mihomo", self.rev, 1, "8.8.8.8")

    def test_exported_hostname_requests_preserve_mixed_answers_and_rebinding_guards(self):
        self.committed_relay()
        clients = {kind: self.exported_client(kind) for kind in ("sing-box", "mihomo")}
        for kind, client in clients.items():
            self.client_success(kind, client)
        with self.negative_phase(dns=True):
            for kind, (port, _, _) in clients.items():
                for index, answers in enumerate(((TARGET, DENIED), (DENIED, TARGET),
                                                (TARGET, INGRESS), (TARGET, "169.254.169.254"))):
                    name = f"export-mixed-{kind}-{index}.example.test"
                    self.answers.records[name] = answers
                    self.rejection(self.negative_request(port, auth=None, host=name), self.ingress, ("reject",))
                    self.assertIn((UPSTREAM_EGRESS, name, 1), self.answers.snapshot())
        self.runtime.recover()  # Explicitly restart the unchanged committed state after the bounded drain.
        for kind, (port, _, _) in clients.items():
            name = "export-rebind-" + kind + ".example.test"
            self.answers.sequences[name] = ((TARGET,), (DENIED,))
            self.success(port, auth=None, host=name, protocol="socks5h")
            self.assertEqual(self.answers.counts[(name, 1)], 1, "Client or relay resolved the target twice")
            with self.negative_phase(dns=True):
                self.rejection(self.negative_request(port, auth=None, host=name), self.ingress, ("reject",))
            self.assertGreaterEqual(self.answers.counts[(name, 1)], 2)
            self.runtime.recover()

    def test_explicit_dns_tls_identity_failure_never_falls_back(self):
        self.committed_relay()
        for kind in ("sing-box", "mihomo"):
            self.client_success(kind, self.exported_client(kind))
        before_dns = self.answers.snapshot()
        with self.negative_phase():
            for kind in ("sing-box", "mihomo"):
                _, process, dns_port = self.exported_client(kind, wrong_dns_identity=True)
                self.rejection(lambda: self.dns_request(dns_port, "bad-dns-" + kind + ".example.test", success=False),
                               process, ("x509", "certificate", WRONG_DNS_IDENTITY, SNI))
        self.assertEqual(self.answers.snapshot(), before_dns, "Unverified TLS sent a DNS query or used fallback")

    def test_dns_inbound_cannot_forward_malformed_http_and_mixed_udp_is_rejected(self):
        self.committed_relay()
        clients = {kind: self.exported_client(kind) for kind in ("sing-box", "mihomo")}
        for kind, client in clients.items():
            self.client_success(kind, client)  # HTTPS at the non-53 target stays ordinary TCP.
        udp_port, packets = udp_target(self.stack)
        self.evidence.udp.append((packets, {}))
        before_dns = self.answers.snapshot()
        with self.negative_phase():
            for kind, (port, process, dns_port) in clients.items():
                self.rejection(lambda: udp_through(port, TARGET, udp_port, b"must-not-forward"),
                               process, ("reject",))
                # Deliberately incomplete/non-DNS input must not open a direct
                # connection. Closing the sender and draining all fixture work
                # makes this a bounded no-delivery assertion, not a timeout pass.
                with socket.create_connection((INGRESS, dns_port), timeout=2) as stream:
                    stream.sendall(b"\x00\x10GET / HTTP/1.0\r\n\r\n")
                    stream.shutdown(socket.SHUT_WR)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
                    stream.sendto(b"GET / HTTP/1.0\r\n\r\n", (INGRESS, dns_port))
        self.assertEqual(self.answers.snapshot(), before_dns)


if __name__ == "__main__":
    unittest.main()
