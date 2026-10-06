"""Bare pinned TUIC compatibility, before admitting public exports.

Only HTTP/TCP payloads are tested. QUIC itself requires a UDP listener; that
socket is not evidence of forwarded-UDP support. No obfs, hop, bandwidth, ALPN
or fingerprint tuning is supplied. ALPN h3 is explicit and zero-RTT is off.
TUIC v5 is selected by UUID/password. Mihomo advertises UDP capability even
with udp:false; this fixture tests HTTP/TCP only and makes no UDP claim.
"""
from contextlib import ExitStack
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest

import yaml

from loopback_helpers import certificate_files, CoreProcess, http_through, start_http_target, unused_port, require_tls_rejection, require_rejection_evidence

PASSWORD = "tuic-test:p@ss/word?#%雪 with space"
SNI = "vpn.example.test"
TARGET_BODY = b"VUI-LOOPBACK-TARGET"
CLIENTS = ("mihomo", "singbox")
UUID = "11111111-1111-4111-8111-111111111111"
WRONG_UUID = "22222222-2222-4222-8222-222222222222"
FAILURES = ("uuid", "password", "ca", "sni")


@unittest.skipUnless(os.getenv("VUI_TEST_CORES") and os.getenv("VUI_TEST_MIHOMO"),
                     "pinned real sing-box and Mihomo binaries required")
class TUICPreflightLoopbackTests(unittest.TestCase):
    clients = CLIENTS
    @classmethod
    def setUpClass(cls):
        cls.singbox = str(Path(os.environ["VUI_TEST_CORES"]) / "sing-box")
        cls.mihomo = os.environ["VUI_TEST_MIHOMO"]
        for command, expected in (([cls.singbox, "version"], "sing-box version 1.14.2\n"),
                                  ([cls.mihomo, "-v"], "Mihomo Meta v1.19.32 ")):
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            output = result.stdout + result.stderr
            if result.returncode or not output.startswith(expected):
                raise AssertionError("Unexpected characterization binary: " + output)
            print(output.splitlines()[0])

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="vui-tuic-preflight-")))
        self.ca, self.cert, self.key = certificate_files(self.root)
        self.wrong_ca, _, _ = certificate_files(self.root, "wrong")
        (self.root / "empty-roots").mkdir()
        self.env = {**os.environ, "SSL_CERT_FILE": str(self.ca),
                    "SSL_CERT_DIR": str(self.root / "empty-roots")}
        self.sequence = 0

    def unique_path(self, label, suffix):
        self.sequence += 1
        return self.root / f"{self.sequence}-{label}.{suffix}"

    def server_config(self, port):
        return {
            "log": {"level": "debug", "timestamp": False},
            "inbounds": [{"type": "tuic", "tag": "tuic-in", "listen": "127.0.0.1",
                          "listen_port": port, "users": [{"uuid": UUID, "password": PASSWORD}],
                          "zero_rtt_handshake": False,
                          "tls": {"enabled": True, "server_name": SNI, "alpn": ["h3"],
                                  "certificate_path": str(self.cert), "key_path": str(self.key)}}],
            "outbounds": [{"type": "direct", "tag": "target"}],
            "route": {"final": "target"},
        }

    def client_config(self, client, port, server_port, *, failure=None):
        uuid = WRONG_UUID if failure == "uuid" else UUID
        password = "wrong-tuic-password" if failure == "password" else PASSWORD
        sni = "wrong.example.test" if failure == "sni" else SNI
        if client == "mihomo":
            config = {
                "mixed-port": port, "bind-address": "127.0.0.1", "allow-lan": False,
                "mode": "rule", "log-level": "debug", "ipv6": False,
                "proxies": [{"name": "TUIC_ONLY", "type": "tuic", "server": "127.0.0.1",
                             "port": server_port, "uuid": uuid, "password": password, "udp": False,
                             "alpn": ["h3"], "reduce-rtt": False,
                             "sni": sni, "skip-cert-verify": False}],
                "rules": ["MATCH,TUIC_ONLY"],
            }
            self.assertNotIn("DIRECT", json.dumps(config))
            return config
        self.assertEqual(client, "singbox")
        config = {
            "log": {"level": "debug", "timestamp": False},
            "inbounds": [{"type": "mixed", "listen": "127.0.0.1", "listen_port": port}],
            "outbounds": [{"type": "tuic", "tag": "TUIC_ONLY", "server": "127.0.0.1",
                           "server_port": server_port, "uuid": uuid, "password": password, "network": "tcp",
                           "zero_rtt_handshake": False,
                           "tls": {"enabled": True, "server_name": sni, "insecure": False, "alpn": ["h3"]}}],
            "route": {"final": "TUIC_ONLY"},
        }
        self.assertEqual([node["type"] for node in config["outbounds"]], ["tuic"])
        return config

    def check_command(self, command, env):
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def prepare_server(self, port):
        path = self.unique_path("server", "json")
        path.write_text(json.dumps(self.server_config(port)))
        self.check_command([self.singbox, "check", "-c", str(path)], self.env)
        return [self.singbox, "run", "-c", str(path)]

    def prepare_client(self, client, port, server_port, **options):
        config = self.client_config(client, port, server_port, **options)
        env = self.env.copy()
        if options.get("failure") == "ca":
            env["SSL_CERT_FILE"] = str(self.wrong_ca)
        if client.startswith("mihomo"):
            path = self.unique_path(client, "yaml")
            home = str(self.unique_path("mihomo-home", "data"))
            Path(home).mkdir()
            for name, provider in config.get("proxy-providers", {}).items():
                # Honor the real client's existing safe-path boundary.
                destination = Path(home) / (name + ".txt")
                destination.write_bytes(Path(provider["path"]).read_bytes())
                provider["path"] = str(destination)
            path.write_text(yaml.safe_dump(config, sort_keys=False))
            command = [self.mihomo, "-d", home, "-f", str(path)]
            self.check_command([self.mihomo, "-t", "-d", home, "-f", str(path)], env)
        else:
            path = self.unique_path(client, "json")
            path.write_text(json.dumps(config))
            command = [self.singbox, "run", "-c", str(path)]
            self.check_command([self.singbox, "check", "-c", str(path)], env)
        return command, env

    def start_udp_server(self, process, port):
        # CoreProcess.start waits for TCP and cannot prove a QUIC listener.
        # Wait for the real core's UDP bind log, then the actual HTTP exchange
        # proves readiness. No host firewall or network settings are changed.
        process.process = subprocess.Popen(process.command, stdout=process.log,
                                           stderr=subprocess.STDOUT, env=process.env)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            log = process.log_path.read_text(errors="replace")
            if process.process.poll() is not None:
                self.fail(log)
            if "udp server started at 127.0.0.1:" + str(port) in log:
                return
            time.sleep(.03)
        self.fail("QUIC UDP listener did not start: " + log)

    def logs(self):
        return "\n".join(path.name + ":\n" + path.read_text(errors="replace")
                         for path in sorted(self.root.glob("*.log")))

    def assert_success(self, port):
        before = len(self.requests)
        last = None
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                last = http_through(port, "127.0.0.1", self.target_port)
                if last == (200, TARGET_BODY):
                    self.assertGreater(len(self.requests), before)
                    return
            except (OSError, http.client.HTTPException) as exc:
                last = exc
            time.sleep(.08)
        self.fail(f"TUIC/TLS did not reach the HTTP target: {last}\n{self.logs()}")

    def assert_failure_without_direct(self, port, *, clear=True):
        if clear:
            self.requests.clear()
        else:
            self.assertEqual(self.requests, [], "A previous rejected request arrived late at the target")
        started = time.monotonic()
        try:
            status, _ = http_through(port, "127.0.0.1", self.target_port)
            outcome = f"HTTP {status}"
            self.assertGreaterEqual(status, 400, self.logs())
        except (OSError, http.client.HTTPException) as exc:
            outcome = f"{type(exc).__name__}: {exc}"
        elapsed = time.monotonic() - started
        time.sleep(.1)
        self.assertEqual(self.requests, [], "Rejected TUIC request reached directly reachable target\n" + self.logs())
        return f"{outcome} after {elapsed:.2f}s"

    def failure_evidence(self, process, failure, port, server, offset):
        if failure in {"ca", "sni"}:
            reason = "unknown authority" if failure == "ca" else "wrong.example.test"
            log = require_tls_rejection(self, process.log_path,
                lambda: self.assert_failure_without_direct(port, clear=False), self.requests, reason)
            evidence = next(line for line in log.splitlines() if "x509" in line and reason in line)
        else:
            reason = "authentication: unknown user " + WRONG_UUID if failure == "uuid" else "authentication: token mismatch"
            log = require_rejection_evidence(self, server.log_path,
                lambda: self.assert_failure_without_direct(port, clear=False), self.requests,
                (reason,), log_offset=offset)
            evidence = next(line for line in log.splitlines() if reason in line)
        print(f"TUIC v5 preflight {failure}: " + evidence)

    def verify_both_clients(self, *, failure=None):
        self.target_port, self.requests = start_http_target(self.stack)
        control = http.client.HTTPConnection("127.0.0.1", self.target_port, timeout=3)
        try:
            control.request("GET", "/reachable-control")
            response = control.getresponse()
            self.assertEqual((response.status, response.read()), (200, TARGET_BODY))
        finally:
            control.close()
        self.assertEqual(len(self.requests), 1)
        self.requests.clear()
        with ExitStack() as processes:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                udp.bind(("127.0.0.1", 0))
                server_port = udp.getsockname()[1]
            server = CoreProcess(processes, self.prepare_server(server_port), self.unique_path("server", "log"), self.env)
            self.start_udp_server(server, server_port)
            for client in self.clients:
                with self.subTest(client=client, failure=failure):
                    for mutation in ((None, failure) if failure else (None,)):
                        port = unused_port()
                        command, env = self.prepare_client(client, port, server_port, failure=mutation)
                        process = CoreProcess(processes, command, self.unique_path(client, "log"), env)
                        try:
                            process.start(port)
                            if mutation is None:
                                self.assert_success(port)
                            else:
                                offset = len(server.log_path.read_text(errors="replace"))
                                outcome = self.assert_failure_without_direct(port)
                                self.failure_evidence(process, mutation, port, server, offset)
                                print(f"TUIC preflight {client} {mutation}: {outcome}; target requests=0")
                            self.assertIsNone(process.process.poll(), process.log_path.read_text())
                            self.assertIsNone(server.process.poll(), server.log_path.read_text())
                        finally:
                            process.stop()
                    print(f"TUIC preflight {client}: {failure or 'HTTP success'}; native QUIC defaults; no DIRECT")

    def test_00_bare_server_and_client_config_checks(self):
        # Parsing has no local listener dependency, so environment restrictions
        # cannot hide whether the pinned binaries implement this profile.
        self.prepare_server(19443)
        for client in self.clients:
            for failure in (None, *FAILURES):
                with self.subTest(client=client, failure=failure):
                    self.prepare_client(client, 19444, 19443, failure=failure)
        print("TUIC bare server and both real clients accept the bounded profile")

    def test_verified_http_forwarding_over_quic(self):
        self.verify_both_clients()

    def test_wrong_uuid_is_rejected_without_direct(self):
        self.verify_both_clients(failure="uuid")

    def test_wrong_password_is_rejected_without_direct(self):
        self.verify_both_clients(failure="password")

    def test_wrong_ca_is_rejected_without_direct(self):
        self.verify_both_clients(failure="ca")

    def test_wrong_sni_is_rejected_without_direct(self):
        self.verify_both_clients(failure="sni")


if __name__ == "__main__":
    unittest.main()
