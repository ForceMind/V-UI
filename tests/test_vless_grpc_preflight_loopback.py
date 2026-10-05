"""Characterize the pinned gRPC Lite binaries before enabling public exports.

These are deliberately bare real-core fixtures, independent of the application
compiler and exporters. Only HTTP/TCP forwarding is exercised. The verified
TLS probe proves that the real server negotiates h2; successful requests then
exercise each real client's gRPC transport without an intermediary or DIRECT
fallback. No custom HTTP authority behavior is claimed.
"""
from contextlib import ExitStack
import http.client
from itertools import product
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import time
import unittest

import yaml

from loopback_helpers import (
    certificate_files, CoreProcess, http_through, start_http_target, unused_port,
)


UUID = "44444444-4444-4444-4444-444444444444"
WRONG_UUID = "55555555-5555-5555-5555-555555555555"
SERVICE_NAME = "vless.grpc_0-4.4"
SNI = "vpn.example.test"
TARGET_BODY = b"VUI-LOOPBACK-TARGET"
CLIENTS = ("mihomo", "singbox")
VARIANTS = tuple(product((False, True), (False, True)))
BOUNDARY_SERVICES = (".", "..", "A" + ("a_.-" * 32)[:127])
FAILURES = ("uuid", "ca", "sni", "service_name", "service_case")


@unittest.skipUnless(
    os.getenv("VUI_TEST_CORES") and os.getenv("VUI_TEST_MIHOMO"),
    "pinned real sing-box and Mihomo binaries required",
)
class VLESSGRPCPreflightLoopbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singbox = str(Path(os.environ["VUI_TEST_CORES"]) / "sing-box")
        cls.mihomo = os.environ["VUI_TEST_MIHOMO"]
        versions = []
        for command, expected in (
            ([cls.singbox, "version"], "sing-box version 1.14.2"),
            ([cls.mihomo, "-v"], "Mihomo Meta v1.19.32 "),
        ):
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            output = result.stdout + result.stderr
            if result.returncode or not output.startswith(expected + ("\n" if command[-1] == "version" else "")):
                raise AssertionError("Unexpected characterization binary: " + output)
            versions.append(output)
        tags = next((line for line in versions[0].splitlines()
                     if line.startswith("Tags:")), "")
        if not tags or "with_grpc" in tags.removeprefix("Tags:").strip().split(","):
            raise AssertionError("This preflight requires the pinned gRPC Lite build: " + versions[0])
        print("gRPC preflight: sing-box 1.14.2 without with_grpc (gRPC Lite); Mihomo 1.19.32")

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(
            tempfile.TemporaryDirectory(prefix="vui-vless-grpc-preflight-")
        ))
        self.ca, self.cert, self.key = certificate_files(self.root)
        self.wrong_ca, _, _ = certificate_files(self.root, "wrong")
        (self.root / "empty-roots").mkdir()
        self.env = {
            **os.environ,
            "SSL_CERT_FILE": str(self.ca),
            "SSL_CERT_DIR": str(self.root / "empty-roots"),
        }
        self.target_port, self.requests = start_http_target(self.stack)
        # A DIRECT escape must genuinely work, so a failed proxy cannot pass by
        # depending on an unresolvable hostname or an unreachable destination.
        connection = http.client.HTTPConnection("127.0.0.1", self.target_port, timeout=3)
        try:
            connection.request("GET", "/reachable-control")
            response = connection.getresponse()
            self.assertEqual((response.status, response.read()), (200, TARGET_BODY))
        finally:
            connection.close()
        self.assertEqual(len(self.requests), 1)
        self.requests.clear()
        self.sequence = 0

    def unique_path(self, label, suffix):
        self.sequence += 1
        return self.root / f"{self.sequence}-{label}.{suffix}"

    def server_config(self, port, *, alpn=False, service_name=SERVICE_NAME):
        tls = {
            "enabled": True, "server_name": SNI,
            "certificate_path": str(self.cert), "key_path": str(self.key),
        }
        if alpn:
            tls["alpn"] = ["h2"]
        return {
            "log": {"level": "debug", "timestamp": False},
            "inbounds": [{
                "type": "vless", "tag": "grpc-in", "listen": "127.0.0.1",
                "listen_port": port, "users": [{"uuid": UUID, "flow": ""}],
                "tls": tls, "transport": {"type": "grpc", "service_name": service_name},
            }],
            # Only the server delivers accepted traffic to the HTTP target.
            "outbounds": [{"type": "direct", "tag": "target"}],
            "route": {"final": "target"},
        }

    def client_config(self, client, port, server_port, *, fingerprint=False,
                      alpn=False, failure=None, service_name=SERVICE_NAME):
        uuid = WRONG_UUID if failure == "uuid" else UUID
        sni = "wrong.example.test" if failure == "sni" else SNI
        service = "different.grpc_0-4.4" if failure == "service_name" else service_name
        if failure == "service_case":
            service = service_name[0].swapcase() + service_name[1:]
            self.assertNotEqual(service, service_name)
            self.assertEqual(service.lower(), service_name.lower())
        self.assertRegex(service, r"\A[A-Za-z0-9_.-]{1,128}\Z")
        if client == "mihomo":
            proxy = {
                "name": "GRPC_ONLY", "type": "vless", "server": "127.0.0.1",
                "port": server_port, "uuid": uuid, "udp": False, "tls": True,
                "servername": sni, "skip-cert-verify": False, "network": "grpc",
                "grpc-opts": {"grpc-service-name": service},
            }
            if fingerprint:
                proxy["client-fingerprint"] = "chrome"
            if alpn:
                proxy["alpn"] = ["h2"]
            config = {
                "mixed-port": port, "bind-address": "127.0.0.1", "allow-lan": False,
                "mode": "rule", "log-level": "debug", "ipv6": False,
                "proxies": [proxy], "rules": ["MATCH,GRPC_ONLY"],
            }
            self.assertEqual(config["rules"], ["MATCH," + proxy["name"]])
            self.assertEqual(len(config["proxies"]), 1)
            self.assertNotIn("DIRECT", json.dumps(config))
            return config
        self.assertEqual(client, "singbox")
        tls = {"enabled": True, "server_name": sni, "insecure": False}
        if fingerprint:
            tls["utls"] = {"enabled": True, "fingerprint": "chrome"}
        if alpn:
            tls["alpn"] = ["h2"]
        config = {
            "log": {"level": "debug", "timestamp": False},
            "inbounds": [{"type": "mixed", "listen": "127.0.0.1", "listen_port": port}],
            "outbounds": [{
                "type": "vless", "tag": "GRPC_ONLY", "server": "127.0.0.1",
                "server_port": server_port, "uuid": uuid, "flow": "", "network": "tcp",
                "tls": tls, "transport": {"type": "grpc", "service_name": service},
            }],
            "route": {"final": "GRPC_ONLY"},
        }
        self.assertEqual([node["type"] for node in config["outbounds"]], ["vless"])
        self.assertEqual(config["route"], {"final": config["outbounds"][0]["tag"]})
        return config

    def check_command(self, command, env):
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def prepare_server(self, port, *, alpn=False, service_name=SERVICE_NAME):
        path = self.unique_path("server", "json")
        path.write_text(json.dumps(self.server_config(port, alpn=alpn, service_name=service_name)))
        self.check_command([self.singbox, "check", "-c", str(path)], self.env)
        return [self.singbox, "run", "-c", str(path)]

    def prepare_client(self, client, port, server_port, **options):
        config = self.client_config(client, port, server_port, **options)
        env = self.env.copy()
        if options.get("failure") == "ca":
            env["SSL_CERT_FILE"] = str(self.wrong_ca)
        if client == "singbox" and options.get("failure") in {"ca", "sni"}:
            # v1.14.2 gRPC Lite stores RoundTrip errors in GunConn.setup, but
            # an initial pipe Write can block before Read reports that error.
            # Its pinned x/net v0.57.0 http2 transport_common.go logs the actual
            # TLS dial error with http2debug=1. Enable only diagnostic logging
            # for these subprocesses; keep the unchanged binary, protocol,
            # certificate verification, and required x509/reason assertions.
            debug = [flag for flag in env.get("GODEBUG", "").split(",")
                     if flag and not flag.startswith("http2debug=")]
            env["GODEBUG"] = ",".join([*debug, "http2debug=1"])
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

    def verified_h2_probe(self, server_port):
        # Observe ALPN on a real, authenticated server TLS connection; do not
        # infer h2 solely from the JSON field or replace the server with a proxy.
        context = ssl.create_default_context(cafile=str(self.ca))
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        context.set_alpn_protocols(["h2"])
        with socket.create_connection(("127.0.0.1", server_port), timeout=3) as raw:
            with context.wrap_socket(raw, server_hostname=SNI) as tls:
                self.assertEqual(tls.selected_alpn_protocol(), "h2")
                self.assertTrue(tls.getpeercert())
        print("gRPC preflight: real server TLS verified against temporary CA/SNI; negotiated ALPN h2")

    def logs(self):
        return "\n".join(path.name + ":\n" + path.read_text(errors="replace")
                         for path in sorted(self.root.glob("*.log")))

    def assert_success(self, port):
        before = len(self.requests)
        last = None
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            try:
                last = http_through(port, "127.0.0.1", self.target_port)
                if last == (200, TARGET_BODY):
                    self.assertGreater(len(self.requests), before)
                    return
            except (OSError, http.client.HTTPException) as exc:
                last = exc
            time.sleep(.08)
        self.fail(f"VLESS/gRPC/TLS did not reach the HTTP target: {last}\n{self.logs()}")

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
        self.assertEqual(self.requests, [],
                         "Rejected gRPC request reached the directly reachable target\n" + self.logs())
        # A caller timeout is only its observed outcome, not TLS-error proof.
        # CA/SNI cases independently require the real process's x509 evidence.
        return f"{outcome} after {elapsed:.2f}s"

    def tls_error_evidence(self, process, failure):
        deadline = time.monotonic() + 2
        log = ""
        while time.monotonic() < deadline:
            log = process.log_path.read_text(errors="replace").lower()
            if "x509" in log:
                break
            time.sleep(.05)
        self.assertIn("x509", log, self.logs())
        self.assertIn("unknown authority" if failure == "ca" else "wrong.example.test", log)
        print(f"gRPC preflight {failure}: " + next(line for line in log.splitlines() if "x509" in line))

    def verify_both_clients(self, *, fingerprint=False, alpn=False, failure=None,
                            service_name=SERVICE_NAME):
        with ExitStack() as processes:
            server_port = unused_port()
            command = self.prepare_server(server_port, alpn=alpn, service_name=service_name)
            server = CoreProcess(processes, command, self.unique_path("server", "log"), self.env)
            # A sandbox startup failure is a failure with its exact core log,
            # never a skip, an alternate core, or a permission workaround.
            server.start(server_port)
            self.verified_h2_probe(server_port)
            for client in CLIENTS:
                with self.subTest(client=client, failure=failure):
                    # Each negative proves the same client/server/target works
                    # first, then changes exactly one property in a new process.
                    for mutation in ((None, failure) if failure else (None,)):
                        port = unused_port()
                        command, env = self.prepare_client(
                            client, port, server_port, fingerprint=fingerprint,
                            alpn=alpn, failure=mutation, service_name=service_name,
                        )
                        process = CoreProcess(processes, command, self.unique_path(client, "log"), env)
                        try:
                            process.start(port)
                            if mutation is None:
                                self.assert_success(port)
                            else:
                                outcome = self.assert_failure_without_direct(port)
                                print(f"gRPC preflight {client} {mutation}: caller observed "
                                      f"{outcome}; target requests=0")
                                if mutation in {"ca", "sni"}:
                                    self.tls_error_evidence(process, mutation)
                            self.assertIsNone(process.process.poll(), process.log_path.read_text())
                            self.assertIsNone(server.process.poll(), server.log_path.read_text())
                        finally:
                            process.stop()
                    print(f"gRPC preflight {client}: {failure or 'HTTP success'}; "
                          f"Chrome={fingerprint}, explicit h2={alpn}, service={service_name!r}; "
                          "no DIRECT fallback")

    def test_00_bare_server_and_client_config_checks(self):
        # Keep this independent of process startup, so a sandbox network-monitor
        # restriction cannot hide whether the unchanged real binaries parse it.
        for fingerprint, alpn in VARIANTS:
            with self.subTest(fingerprint=fingerprint, alpn=alpn):
                server_port = unused_port()
                self.prepare_server(server_port, alpn=alpn)
                for client in CLIENTS:
                    self.prepare_client(client, unused_port(), server_port,
                                        fingerprint=fingerprint, alpn=alpn)
                print(f"gRPC preflight config checks: server and both clients accept "
                      f"Chrome={fingerprint}, explicit h2={alpn}")
        for failure, client in product(FAILURES, CLIENTS):
            with self.subTest(failure=failure, client=client):
                self.prepare_client(client, unused_port(), unused_port(), failure=failure)
                print(f"gRPC preflight config check: {client} accepts syntactically valid {failure} mismatch")
        for service_name in BOUNDARY_SERVICES:
            with self.subTest(service_name=service_name):
                server_port = unused_port()
                self.prepare_server(server_port, service_name=service_name)
                for client in CLIENTS:
                    self.prepare_client(client, unused_port(), server_port, service_name=service_name)
                print(f"gRPC preflight config checks: server and both clients accept literal {service_name!r}")

    def test_verified_http_forwarding_all_chrome_h2_variants(self):
        for fingerprint, alpn in VARIANTS:
            with self.subTest(fingerprint=fingerprint, alpn=alpn):
                self.verify_both_clients(fingerprint=fingerprint, alpn=alpn)

    def test_literal_service_name_boundaries_in_both_clients(self):
        for service_name in BOUNDARY_SERVICES:
            with self.subTest(service_name=service_name):
                self.verify_both_clients(service_name=service_name)

    def test_wrong_uuid_is_rejected_without_direct(self):
        self.verify_both_clients(failure="uuid")

    def test_wrong_ca_is_rejected_without_direct(self):
        self.verify_both_clients(failure="ca")

    def test_wrong_sni_is_rejected_without_direct(self):
        self.verify_both_clients(failure="sni")

    def test_wrong_service_name_is_rejected_without_direct(self):
        self.verify_both_clients(failure="service_name")

    def test_service_name_is_case_sensitive_without_direct(self):
        self.verify_both_clients(failure="service_case")


if __name__ == "__main__":
    unittest.main()
