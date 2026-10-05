"""Pinned VLESS/gRPC/TLS forwarding through the public token export surface.

The application adapter builds the actual server; public URI/YAML/JSON must
preserve the same profile. The clients use unchanged checksum-pinned binaries,
with temporary CA trust only. No custom authority or UDP behavior is claimed.
"""
import base64
from contextlib import ExitStack
from copy import deepcopy
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
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

import yaml

from app.models import database
from app.services import routing_store
from app.services.core_manager import SingBoxAdapter
from app.services.mihomo_routing import default_routing
from app.services.routing_store import read_snapshot, save_routing
import test_subscription_tokens as grant_tests
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
    os.getenv("VUI_TEST_MIHOMO") and os.getenv("VUI_TEST_CORES"),
    "pinned real sing-box and Mihomo binaries required",
)
class VLESSGRPCLoopbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singbox = str(Path(os.environ["VUI_TEST_CORES"]) / "sing-box")
        cls.mihomo = os.environ["VUI_TEST_MIHOMO"]
        versions = []
        for command, expected in (
            ([cls.singbox, "version"], "sing-box version 1.14.2\n"),
            ([cls.mihomo, "-v"], "Mihomo Meta v1.19.32 "),
        ):
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            output = result.stdout + result.stderr
            if result.returncode or not output.startswith(expected):
                raise AssertionError("Unexpected public-export test binary: " + output)
            versions.append(output)
        tags = next((line for line in versions[0].splitlines()
                     if line.startswith("Tags:")), "")
        if not tags or "with_grpc" in tags.removeprefix("Tags:").strip().split(","):
            raise AssertionError("Public exports must use the pinned gRPC Lite build: " + versions[0])
        print("gRPC public exports: sing-box 1.14.2 gRPC Lite; Mihomo 1.19.32")

    def setUp(self):
        grant_tests.SubscriptionTokenTests.setUp(self)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(
            tempfile.TemporaryDirectory(prefix="vui-vless-grpc-chain-")
        ))
        patch.object(routing_store, "ROUTING_FILE", self.root / "routing.json").start()
        self.ca, self.cert, self.key = certificate_files(self.root)
        self.wrong_ca, _, _ = certificate_files(self.root, "wrong")
        (self.root / "empty-roots").mkdir()
        self.env = {
            **os.environ,
            "SSL_CERT_FILE": str(self.ca),
            "SSL_CERT_DIR": str(self.root / "empty-roots"),
        }
        self.sequence = 0
        self.target_port, self.requests = start_http_target(self.stack)
        save_routing({**default_routing(), "mode": "direct"}, read_snapshot()["revision"])
        # A DIRECT escape would work. The negative cannot pass by relying on
        # unresolvable fixture DNS or an unreachable destination.
        connection = http.client.HTTPConnection("127.0.0.1", self.target_port, timeout=3)
        try:
            connection.request("GET", "/reachable-control")
            response = connection.getresponse()
            self.assertEqual((response.status, response.read()), (200, TARGET_BODY))
        finally:
            connection.close()
        self.assertEqual(len(self.requests), 1)
        self.requests.clear()

    def unique_path(self, label, suffix):
        self.sequence += 1
        return self.root / f"{self.sequence}-{label}.{suffix}"

    def configure_profile(self, *, fingerprint=False, alpn=False, service_name=SERVICE_NAME):
        self.server_port = unused_port()
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, 1)
            row.protocol = "vless"
            row.port = self.server_port
            row.remark = "vless-grpc-real"
            row.settings = {"users": [{"uuid": UUID, "flow": ""}]}
            stream = {
                "transport": {"type": "grpc", "service_name": service_name},
                "tls": {
                    "enabled": True, "server_name": SNI,
                    "certificate_path": str(self.cert), "key_path": str(self.key),
                },
                "_vui": {
                    "security": "tls", "server_name": SNI,
                    "skip_cert_verify": False,
                },
            }
            if alpn:
                stream["tls"]["alpn"] = ["h2"]
            if fingerprint:
                stream["_vui"]["client_fingerprint"] = "chrome"
            row.stream_settings = stream
            db.commit()
            # Freeze server before changing the public profile for negatives.
            self.server_config = SingBoxAdapter().build_config([row])
            self.good_settings = deepcopy(row.settings)
            self.good_stream = deepcopy(row.stream_settings)
        inbound = self.server_config["inbounds"][0]
        inbound["listen"] = "127.0.0.1"
        self.assertEqual(inbound["transport"], {"type": "grpc", "service_name": service_name})

    def update_export(self, failure=None):
        # Start from the verified good profile each time. Exactly one client
        # property differs; neither the frozen server nor target is changed.
        settings = deepcopy(self.good_settings)
        stream = deepcopy(self.good_stream)
        if failure == "uuid":
            settings["users"][0]["uuid"] = WRONG_UUID
        elif failure == "sni":
            stream["tls"]["server_name"] = "wrong.example.test"
            stream["_vui"]["server_name"] = "wrong.example.test"
        elif failure == "service_name":
            stream["transport"]["service_name"] = "different.grpc_0-4.4"
        elif failure == "service_case":
            service = stream["transport"]["service_name"]
            different = service[0].swapcase() + service[1:]
            self.assertNotEqual(service, different)
            self.assertEqual(service.lower(), different.lower())
            stream["transport"]["service_name"] = different
        else:
            self.assertIn(failure, (None, "ca"))
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, 1)
            row.settings = settings
            row.stream_settings = stream
            db.commit()

    def issue_grant(self):
        response = self.client.post("/api/subscriptions", headers=grant_tests.HEADERS, json={
            "label": "vless-grpc-real", "server": "127.0.0.1", "inbound_ids": [1],
            "formats": ["mihomo.yaml", "raw", "sing-box.json"],
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def assert_no_server_material(self, text):
        for forbidden in (str(self.key), str(self.cert), str(self.ca),
                          "certificate_path", "key_path", "PRIVATE KEY"):
            self.assertNotIn(forbidden, text)

    def public_get(self, path):
        # Deliberately create a separate cookie-free public client.
        with self.client.__class__(self.client.app, base_url=grant_tests.ORIGIN,
                                   client=("127.0.0.1", 54212)) as public:
            response = public.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertNotIn("set-cookie", response.headers)
        self.assert_no_server_material(response.text)
        return response

    def export_profile(self):
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, 1)
            return deepcopy(row.settings), deepcopy(row.stream_settings)

    def assert_uri(self, grant):
        raw = base64.b64decode(self.public_get(grant["paths"]["raw"]).text, validate=True).decode()
        self.assertEqual(len(raw.splitlines()), 1)
        self.assert_no_server_material(raw)
        uri = urlsplit(raw)
        settings, stream = self.export_profile()
        self.assertEqual(uri.scheme, "vless")
        self.assertEqual(uri.username, settings["users"][0]["uuid"])
        self.assertIsNone(uri.password)
        self.assertEqual((uri.hostname, uri.port), ("127.0.0.1", self.server_port))
        self.assertEqual(unquote(uri.fragment), "vless-grpc-real")
        expected = {
            "security": ["tls"], "type": ["grpc"], "sni": [stream["tls"]["server_name"]],
            "encryption": ["none"], "serviceName": [stream["transport"]["service_name"]],
        }
        if stream["_vui"].get("client_fingerprint"):
            expected["fp"] = ["chrome"]
        if stream["tls"].get("alpn"):
            expected["alpn"] = ["h2"]
        self.assertEqual(parse_qs(uri.query, keep_blank_values=True), expected)

    def exported_mihomo(self, grant, port):
        config = yaml.safe_load(self.public_get(grant["paths"]["mihomo.yaml"]).text)
        settings, stream = self.export_profile()
        self.assertEqual(len(config["proxies"]), 1)
        node = config["proxies"][0]
        expected = {
            "name": "vless-grpc-real", "type": "vless", "server": "127.0.0.1",
            "port": self.server_port, "uuid": settings["users"][0]["uuid"],
            "network": "grpc", "tls": True, "skip-cert-verify": False, "udp": False,
            "servername": stream["tls"]["server_name"],
            "grpc-opts": {"grpc-service-name": stream["transport"]["service_name"]},
        }
        if stream["tls"].get("alpn"):
            expected["alpn"] = ["h2"]
        if stream["_vui"].get("client_fingerprint"):
            expected["client-fingerprint"] = "chrome"
        self.assertEqual(node, expected)
        groups = {group["name"]: group for group in config["proxy-groups"]}
        self.assertEqual(groups["FORCE_PROXY"]["proxies"], [node["name"]])
        self.assertEqual(groups["FORCE_PROXY"]["type"], "select")
        # The production policy bypasses LAN IPs. Force this reachable target
        # through the exported one-node group, which has no DIRECT fallback.
        # UUID/TLS/transport fields remain exactly as publicly exported.
        config["rules"] = ["MATCH,FORCE_PROXY"]
        config["mixed-port"] = port
        config["bind-address"] = "127.0.0.1"
        path = self.unique_path("mihomo-client", "yaml")
        path.write_text(yaml.safe_dump(config, sort_keys=False))
        return path

    def exported_singbox(self, grant, port):
        config = self.public_get(grant["paths"]["sing-box.json"]).json()
        settings, stream = self.export_profile()
        self.assertEqual(len(config["outbounds"]), 1)
        tls = {"enabled": True, "server_name": stream["tls"]["server_name"]}
        if stream["tls"].get("alpn"):
            tls["alpn"] = ["h2"]
        if stream["_vui"].get("client_fingerprint"):
            tls["utls"] = {"enabled": True, "fingerprint": "chrome"}
        expected = {
            "type": "vless", "tag": "vless-grpc-real", "server": "127.0.0.1",
            "server_port": self.server_port, "uuid": settings["users"][0]["uuid"],
            "network": "tcp", "tls": tls,
            "transport": {"type": "grpc", "service_name": stream["transport"]["service_name"]},
        }
        self.assertEqual(config["outbounds"][0], expected)
        self.assertFalse(config["outbounds"][0]["tls"].get("insecure", False))
        self.assertEqual(config["route"]["final"], expected["tag"])
        self.assertNotIn("direct", [node["type"] for node in config["outbounds"]])
        config["inbounds"][0]["listen_port"] = port
        path = self.unique_path("singbox-client", "json")
        path.write_text(json.dumps(config))
        return path

    def check_command(self, command, env):
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def prepare_server(self):
        path = self.unique_path("server", "json")
        path.write_text(json.dumps(self.server_config))
        self.check_command([self.singbox, "check", "-c", str(path)], self.env)
        return [self.singbox, "run", "-c", str(path)]

    def prepare_client(self, client, grant, port, *, failure=None):
        self.assert_uri(grant)
        env = self.env.copy()
        if failure == "ca":
            env["SSL_CERT_FILE"] = str(self.wrong_ca)
        if client == "singbox" and failure in {"ca", "sni"}:
            # Pinned gRPC Lite stores asynchronous RoundTrip errors while its
            # initial Write can wait in a pipe. HTTP/2 diagnostics expose the
            # true TLS reason without changing trust, transport or the binary.
            debug = [flag for flag in env.get("GODEBUG", "").split(",")
                     if flag and not flag.startswith("http2debug=")]
            env["GODEBUG"] = ",".join([*debug, "http2debug=1"])
        if client == "mihomo":
            path = self.exported_mihomo(grant, port)
            home = str(self.unique_path("mihomo-home", "data"))
            self.check_command([self.mihomo, "-t", "-d", home, "-f", str(path)], env)
            command = [self.mihomo, "-d", home, "-f", str(path)]
        else:
            self.assertEqual(client, "singbox")
            path = self.exported_singbox(grant, port)
            self.check_command([self.singbox, "check", "-c", str(path)], env)
            command = [self.singbox, "run", "-c", str(path)]
        return command, env

    def verified_h2_probe(self):
        context = ssl.create_default_context(cafile=str(self.ca))
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        context.set_alpn_protocols(["h2"])
        with socket.create_connection(("127.0.0.1", self.server_port), timeout=3) as raw:
            with context.wrap_socket(raw, server_hostname=SNI) as tls:
                self.assertEqual(tls.selected_alpn_protocol(), "h2")
                self.assertTrue(tls.getpeercert())
        print("gRPC public exports: real adapter server TLS verified against temporary CA/SNI; negotiated ALPN h2")

    def logs(self):
        return "\n".join(path.name + ":\n" + path.read_text(errors="replace")
                         for path in sorted(self.root.glob("*.log")))

    def assert_success(self, port):
        before = len(self.requests)
        deadline = time.monotonic() + 4
        last = None
        while time.monotonic() < deadline:
            try:
                last = http_through(port, "127.0.0.1", self.target_port)
                if last == (200, TARGET_BODY):
                    self.assertGreater(len(self.requests), before)
                    return
            except (OSError, http.client.HTTPException) as exc:
                last = exc
            time.sleep(.08)
        self.fail(f"Public VLESS/gRPC/TLS export did not reach target: {last}\n{self.logs()}")

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
                         "Rejected public gRPC request reached the reachable target\n" + self.logs())
        # A caller timeout describes the caller only. Separate assertions below
        # require the true certificate failure from the real client's log.
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
        print(f"gRPC public exports {failure}: " + next(line for line in log.splitlines() if "x509" in line))

    def verify_both_clients(self, *, fingerprint=False, alpn=False, failure=None,
                            service_name=SERVICE_NAME):
        self.configure_profile(fingerprint=fingerprint, alpn=alpn, service_name=service_name)
        grant = self.issue_grant()
        with ExitStack() as processes:
            server = CoreProcess(processes, self.prepare_server(),
                                 self.unique_path("server", "log"), self.env)
            # A sandbox startup failure is reported with the actual core log,
            # never converted to a skip or worked around with another binary.
            server.start(self.server_port)
            self.verified_h2_probe()
            for client in CLIENTS:
                with self.subTest(client=client, failure=failure):
                    for mutation in ((None, failure) if failure else (None,)):
                        self.update_export(mutation)
                        port = unused_port()
                        command, env = self.prepare_client(client, grant, port, failure=mutation)
                        process = CoreProcess(processes, command, self.unique_path(client, "log"), env)
                        try:
                            process.start(port)
                            if mutation is None:
                                self.assert_success(port)
                            else:
                                outcome = self.assert_failure_without_direct(port)
                                print(f"gRPC public exports {client} {mutation}: caller observed "
                                      f"{outcome}; target requests=0")
                                if mutation in {"ca", "sni"}:
                                    self.tls_error_evidence(process, mutation)
                            self.assertIsNone(process.process.poll(), process.log_path.read_text())
                            self.assertIsNone(server.process.poll(), server.log_path.read_text())
                        finally:
                            process.stop()
                    print(f"gRPC public exports {client}: {failure or 'HTTP success'}; "
                          f"Chrome={fingerprint}, explicit h2={alpn}, service={service_name!r}; "
                          "no DIRECT fallback")

    def test_00_public_uri_and_real_config_checks(self):
        # Intentionally separable from core startup, so restricted local
        # network-monitor permissions do not hide parser/export regressions.
        passed = 0
        for fingerprint, alpn, service_name in product(
                (False, True), (False, True), (SERVICE_NAME, *BOUNDARY_SERVICES)):
            with self.subTest(fingerprint=fingerprint, alpn=alpn, service_name=service_name):
                self.configure_profile(fingerprint=fingerprint, alpn=alpn, service_name=service_name)
                self.prepare_server()
                grant = self.issue_grant()
                for client in CLIENTS:
                    self.prepare_client(client, grant, unused_port())
                passed += 1
        for failure in FAILURES:
            variants = VARIANTS if failure in {"ca", "sni"} else ((False, False),)
            for fingerprint, alpn in variants:
                with self.subTest(failure=failure, fingerprint=fingerprint, alpn=alpn):
                    self.configure_profile(fingerprint=fingerprint, alpn=alpn)
                    grant = self.issue_grant()
                    self.update_export(failure)
                    for client in CLIENTS:
                        self.prepare_client(client, grant, unused_port(), failure=failure)
                    passed += 1
        if passed == 27:
            print("gRPC public URI/YAML/JSON preserve literal services and independent Chrome/h2; "
                  "real server/client configuration checks accept positive and negative profiles")

    def test_verified_public_forwarding_all_chrome_h2_variants(self):
        for fingerprint, alpn in VARIANTS:
            with self.subTest(fingerprint=fingerprint, alpn=alpn):
                self.verify_both_clients(fingerprint=fingerprint, alpn=alpn)

    def test_public_literal_service_boundaries_in_both_clients(self):
        for service_name in BOUNDARY_SERVICES:
            with self.subTest(service_name=service_name):
                self.verify_both_clients(service_name=service_name)

    def test_wrong_uuid_is_rejected_without_direct(self):
        self.verify_both_clients(failure="uuid")

    def test_wrong_ca_is_rejected_all_chrome_h2_variants(self):
        for fingerprint, alpn in VARIANTS:
            with self.subTest(fingerprint=fingerprint, alpn=alpn):
                self.verify_both_clients(failure="ca", fingerprint=fingerprint, alpn=alpn)

    def test_wrong_sni_is_rejected_all_chrome_h2_variants(self):
        for fingerprint, alpn in VARIANTS:
            with self.subTest(fingerprint=fingerprint, alpn=alpn):
                self.verify_both_clients(failure="sni", fingerprint=fingerprint, alpn=alpn)

    def test_wrong_service_name_is_rejected_without_direct(self):
        self.verify_both_clients(failure="service_name")

    def test_service_name_is_case_sensitive_without_direct(self):
        self.verify_both_clients(failure="service_case")


if __name__ == "__main__":
    unittest.main()
