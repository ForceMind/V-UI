"""Bare pinned Hysteria2 compatibility, before admitting public exports.

Only HTTP/TCP payloads are tested. QUIC itself requires a UDP listener; that
socket is not evidence of forwarded-UDP support. No obfs, hop, bandwidth, ALPN
or fingerprint overrides are supplied: the existing pinned defaults are used.
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

from loopback_helpers import certificate_files, CoreProcess, http_through, start_http_target, unused_port

PASSWORD = "hy2-test:p@ss/word?#%"
SNI = "vpn.example.test"
TARGET_BODY = b"VUI-LOOPBACK-TARGET"
CLIENTS = ("mihomo", "singbox")
FAILURES = ("password", "ca", "sni")


@unittest.skipUnless(os.getenv("VUI_TEST_CORES") and os.getenv("VUI_TEST_MIHOMO"),
                     "pinned real sing-box and Mihomo binaries required")
class Hysteria2PreflightLoopbackTests(unittest.TestCase):
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
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="vui-hy2-preflight-")))
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
            "inbounds": [{"type": "hysteria2", "tag": "hy2-in", "listen": "127.0.0.1",
                          "listen_port": port, "users": [{"password": PASSWORD}],
                          "tls": {"enabled": True, "server_name": SNI,
                                  "certificate_path": str(self.cert), "key_path": str(self.key)}}],
            "outbounds": [{"type": "direct", "tag": "target"}],
            "route": {"final": "target"},
        }

    def client_config(self, client, port, server_port, *, failure=None):
        password = "wrong-hy2-password" if failure == "password" else PASSWORD
        sni = "wrong.example.test" if failure == "sni" else SNI
        if client == "mihomo":
            config = {
                "mixed-port": port, "bind-address": "127.0.0.1", "allow-lan": False,
                "mode": "rule", "log-level": "debug", "ipv6": False,
                "proxies": [{"name": "HY2_ONLY", "type": "hysteria2", "server": "127.0.0.1",
                             "port": server_port, "password": password, "udp": False,
                             "sni": sni, "skip-cert-verify": False}],
                "rules": ["MATCH,HY2_ONLY"],
            }
            self.assertNotIn("DIRECT", json.dumps(config))
            return config
        self.assertEqual(client, "singbox")
        config = {
            "log": {"level": "debug", "timestamp": False},
            "inbounds": [{"type": "mixed", "listen": "127.0.0.1", "listen_port": port}],
            "outbounds": [{"type": "hysteria2", "tag": "HY2_ONLY", "server": "127.0.0.1",
                           "server_port": server_port, "password": password, "network": "tcp",
                           "tls": {"enabled": True, "server_name": sni, "insecure": False}}],
            "route": {"final": "HY2_ONLY"},
        }
        self.assertEqual([node["type"] for node in config["outbounds"]], ["hysteria2"])
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
        if client == "mihomo":
            path = self.unique_path(client, "yaml")
            path.write_text(yaml.safe_dump(config, sort_keys=False))
            home = str(self.unique_path("mihomo-home", "data"))
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
        self.fail(f"Hysteria2/TLS did not reach the HTTP target: {last}\n{self.logs()}")

    def assert_failure_without_direct(self, port):
        self.requests.clear()
        started = time.monotonic()
        try:
            status, _ = http_through(port, "127.0.0.1", self.target_port)
            outcome = f"HTTP {status}"
            self.assertGreaterEqual(status, 400, self.logs())
        except (OSError, http.client.HTTPException) as exc:
            outcome = f"{type(exc).__name__}: {exc}"
        elapsed = time.monotonic() - started
        time.sleep(.1)
        self.assertEqual(self.requests, [], "Rejected HY2 request reached directly reachable target\n" + self.logs())
        return f"{outcome} after {elapsed:.2f}s"

    def failure_evidence(self, process, failure):
        deadline = time.monotonic() + 3
        evidence = "x509" if failure in {"ca", "sni"} else "auth"
        log = ""
        while time.monotonic() < deadline:
            log = process.log_path.read_text(errors="replace").lower()
            if evidence in log:
                break
            time.sleep(.05)
        self.assertIn(evidence, log, self.logs())
        if failure in {"ca", "sni"}:
            self.assertIn("unknown authority" if failure == "ca" else "wrong.example.test", log)
        print(f"Hysteria2 preflight {failure}: " + next(line for line in log.splitlines() if evidence in line))

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
            for client in CLIENTS:
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
                                outcome = self.assert_failure_without_direct(port)
                                self.failure_evidence(process, mutation)
                                print(f"Hysteria2 preflight {client} {mutation}: {outcome}; target requests=0")
                            self.assertIsNone(process.process.poll(), process.log_path.read_text())
                            self.assertIsNone(server.process.poll(), server.log_path.read_text())
                        finally:
                            process.stop()
                    print(f"Hysteria2 preflight {client}: {failure or 'HTTP success'}; native QUIC defaults; no DIRECT")

    def test_00_bare_server_and_client_config_checks(self):
        # Parsing has no local listener dependency, so environment restrictions
        # cannot hide whether the pinned binaries implement this profile.
        self.prepare_server(19443)
        for client in CLIENTS:
            for failure in (None, *FAILURES):
                with self.subTest(client=client, failure=failure):
                    self.prepare_client(client, 19444, 19443, failure=failure)
        print("Hysteria2 bare server and both real clients accept the bounded profile")

    def test_verified_http_forwarding_over_quic(self):
        self.verify_both_clients()

    def test_wrong_password_is_rejected_without_direct(self):
        self.verify_both_clients(failure="password")

    def test_wrong_ca_is_rejected_without_direct(self):
        self.verify_both_clients(failure="ca")

    def test_wrong_sni_is_rejected_without_direct(self):
        self.verify_both_clients(failure="sni")


if __name__ == "__main__":
    unittest.main()
