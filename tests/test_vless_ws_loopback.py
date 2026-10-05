"""v0.4.3: pinned VLESS/WebSocket/TLS server and two public client paths.

Only temporary CAs, databases, listeners and client homes are used. Host is
client metadata: sing-box's WS listener enforces path, not the Host header.
"""
import base64
from contextlib import ExitStack
from copy import deepcopy
import http.client
from itertools import product
import json
import os
from pathlib import Path
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
    certificate_files, start_http_target, unused_port, http_through, CoreProcess,
)


UUID = "44444444-4444-4444-4444-444444444444"
WS_PATH = "/vless/ws-0_4.3"
WS_HOST = "ws.example.test"
TARGET_BODY = b"VUI-LOOPBACK-TARGET"


@unittest.skipUnless(
    os.getenv("VUI_TEST_MIHOMO") and os.getenv("VUI_TEST_CORES"),
    "pinned real binaries required",
)
class VLESSWebSocketLoopbackTests(unittest.TestCase):
    def setUp(self):
        grant_tests.SubscriptionTokenTests.setUp(self)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(
            tempfile.TemporaryDirectory(prefix="vui-vless-ws-chain-")
        ))
        patch.object(routing_store, "ROUTING_FILE", self.root / "routing.json").start()
        self.ca, self.cert, self.key = certificate_files(self.root)
        self.server_port = unused_port()
        self.target_port, self.requests = start_http_target(self.stack)
        (self.root / "empty-roots").mkdir()
        self.env = {
            **os.environ,
            "SSL_CERT_FILE": str(self.ca),
            "SSL_CERT_DIR": str(self.root / "empty-roots"),
        }
        self.singbox = str(Path(os.environ["VUI_TEST_CORES"]) / "sing-box")
        self.server = None
        self.configure_profile()
        save_routing({**default_routing(), "mode": "direct"}, read_snapshot()["revision"])
        # A DIRECT escape would genuinely work, not fail because of fixture DNS.
        connection = http.client.HTTPConnection("127.0.0.1", self.target_port, timeout=3)
        try:
            connection.request("GET", "/reachable-control")
            response = connection.getresponse()
            self.assertEqual((response.status, response.read()), (200, TARGET_BODY))
        finally:
            connection.close()
        self.requests.clear()

    def configure_profile(self, *, fingerprint=False, alpn=False, host=WS_HOST):
        self.assertIsNone(self.server, "Do not rewrite a running server fixture")
        transport = {"type": "ws", "path": WS_PATH}
        if host:
            transport["headers"] = {"Host": host}
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, 1)
            row.protocol = "vless"
            row.port = self.server_port
            row.remark = "vless-ws-real"
            row.settings = {"users": [{"uuid": UUID, "flow": ""}]}
            stream = {
                "transport": transport,
                "tls": {
                    "enabled": True, "server_name": "vpn.example.test",
                    "certificate_path": str(self.cert), "key_path": str(self.key),
                },
                "_vui": {
                    "security": "tls", "server_name": "vpn.example.test",
                    "skip_cert_verify": False,
                },
            }
            if alpn:
                stream["tls"]["alpn"] = ["http/1.1"]
            if fingerprint:
                stream["_vui"]["client_fingerprint"] = "chrome"
            row.stream_settings = stream
            db.commit()
            self.server_config = SingBoxAdapter().build_config([row])
        self.server_config["inbounds"][0]["listen"] = "127.0.0.1"

    def update_export(self, *, uuid=None, sni=None, path=None, host=None):
        # The real server configuration was captured above. These changes affect
        # public client exports only, so the negative tests have real mismatches.
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, 1)
            if uuid is not None:
                row.settings = {"users": [{"uuid": uuid, "flow": ""}]}
            stream = deepcopy(row.stream_settings)
            if sni is not None:
                stream["tls"]["server_name"] = sni
                stream["_vui"]["server_name"] = sni
            if path is not None:
                stream["transport"]["path"] = path
            if host is not None:
                stream["transport"]["headers"] = {"Host": host}
            row.stream_settings = stream
            db.commit()

    def check_command(self, command, *, env=None):
        result = subprocess.run(command, capture_output=True, timeout=15, env=env or self.env)
        self.assertEqual(result.returncode, 0,
                         result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace"))

    def start_server(self):
        if self.server is not None:
            self.assertIsNone(self.server.process.poll(), self.server.log_path.read_text())
            return
        path = self.root / "server.json"
        path.write_text(json.dumps(self.server_config))
        self.check_command([self.singbox, "check", "-c", str(path)])
        self.server = CoreProcess(self.stack, [self.singbox, "run", "-c", str(path)],
                                  self.root / "server.log", self.env)
        self.server.start(self.server_port)

    def issue_grant(self):
        response = self.client.post("/api/subscriptions", headers=grant_tests.HEADERS, json={
            "label": "vless-ws-real", "server": "127.0.0.1", "inbound_ids": [1],
            "formats": ["mihomo.yaml", "raw", "sing-box.json"],
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def public_get(self, path):
        with self.client.__class__(self.client.app, base_url=grant_tests.ORIGIN,
                                   client=("127.0.0.1", 54212)) as public:
            response = public.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        for forbidden in (str(self.key), str(self.cert), "certificate_path", "key_path", "PRIVATE KEY"):
            self.assertNotIn(forbidden, response.text)
        return response

    def export_profile(self):
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, 1)
            return deepcopy(row.settings), deepcopy(row.stream_settings)

    def assert_uri(self, grant):
        raw = base64.b64decode(self.public_get(grant["paths"]["raw"]).text).decode()
        self.assertEqual(len(raw.splitlines()), 1)
        for forbidden in (str(self.key), str(self.cert), "certificate_path", "key_path", "PRIVATE KEY"):
            self.assertNotIn(forbidden, raw)
        uri = urlsplit(raw)
        settings, stream = self.export_profile()
        self.assertEqual(uri.scheme, "vless")
        self.assertEqual(uri.username, settings["users"][0]["uuid"])
        self.assertEqual((uri.hostname, uri.port), ("127.0.0.1", self.server_port))
        self.assertEqual(unquote(uri.fragment), "vless-ws-real")
        expected = {
            "security": ["tls"], "type": ["ws"], "sni": [stream["tls"]["server_name"]],
            "encryption": ["none"],
            "path": [stream["transport"]["path"]],
        }
        host = stream["transport"].get("headers", {}).get("Host")
        if host:
            expected["host"] = [host]
        if stream["_vui"].get("client_fingerprint"):
            expected["fp"] = ["chrome"]
        if stream["tls"].get("alpn"):
            expected["alpn"] = ["http/1.1"]
        self.assertEqual(parse_qs(uri.query, keep_blank_values=True), expected)

    def exported_mihomo(self, grant, port):
        config = yaml.safe_load(self.public_get(grant["paths"]["mihomo.yaml"]).text)
        settings, stream = self.export_profile()
        node = config["proxies"][0]
        self.assertEqual(node["type"], "vless")
        self.assertEqual(node["network"], "ws")
        self.assertEqual(node["uuid"], settings["users"][0]["uuid"])
        self.assertTrue(node["tls"])
        self.assertFalse(node["skip-cert-verify"])
        self.assertFalse(node["udp"])
        self.assertNotIn("packet-encoding", node)
        self.assertEqual(node["servername"], stream["tls"]["server_name"])
        expected = {"path": stream["transport"]["path"]}
        if stream["transport"].get("headers"):
            expected["headers"] = stream["transport"]["headers"]
        self.assertEqual(node["ws-opts"], expected)
        self.assertEqual(node.get("alpn"), stream["tls"].get("alpn"))
        self.assertEqual(node.get("client-fingerprint", ""), stream["_vui"].get("client_fingerprint", ""))
        groups = {group["name"]: group for group in config["proxy-groups"]}
        self.assertEqual(groups["FORCE_PROXY"]["proxies"], [node["name"]])
        # The exported policy intentionally bypasses LAN addresses. Isolate the
        # protocol path using the exported no-DIRECT group and a reachable IP.
        config["rules"] = ["MATCH,FORCE_PROXY"]
        config["mixed-port"] = port
        config["bind-address"] = "127.0.0.1"
        path = self.root / f"mihomo-client-{port}.yaml"
        path.write_text(yaml.safe_dump(config, sort_keys=False))
        return path

    def exported_singbox(self, grant, port):
        config = self.public_get(grant["paths"]["sing-box.json"]).json()
        settings, stream = self.export_profile()
        self.assertEqual(len(config["outbounds"]), 1)
        node = config["outbounds"][0]
        self.assertEqual(node["type"], "vless")
        self.assertEqual(node["uuid"], settings["users"][0]["uuid"])
        self.assertEqual(node["transport"], stream["transport"])
        self.assertEqual(node["network"], "tcp")
        self.assertNotIn("packet_encoding", node)
        self.assertTrue(node["tls"]["enabled"])
        self.assertFalse(node["tls"].get("insecure", False))
        self.assertEqual(node["tls"]["server_name"], stream["tls"]["server_name"])
        self.assertEqual(node["tls"].get("alpn"), stream["tls"].get("alpn"))
        self.assertEqual(node["tls"].get("utls", {}).get("fingerprint", ""),
                         stream["_vui"].get("client_fingerprint", ""))
        self.assertEqual(config["route"]["final"], node["tag"])
        self.assertNotIn("direct", [outbound["type"] for outbound in config["outbounds"]])
        config["inbounds"][0]["listen_port"] = port
        path = self.root / f"singbox-client-{port}.json"
        path.write_text(json.dumps(config))
        return path

    def start_client(self, client, grant, *, trust=True):
        self.assert_uri(grant)
        self.start_server()
        port = unused_port()
        env = self.env.copy()
        if not trust:
            untrusted, _, _ = certificate_files(self.root, "untrusted-" + client)
            env["SSL_CERT_FILE"] = str(untrusted)
        if client == "mihomo":
            path = self.exported_mihomo(grant, port)
            home = str(self.root / f"mihomo-data-{port}")
            self.check_command([os.environ["VUI_TEST_MIHOMO"], "-t", "-d", home, "-f", str(path)], env=env)
            command = [os.environ["VUI_TEST_MIHOMO"], "-d", home, "-f", str(path)]
        else:
            path = self.exported_singbox(grant, port)
            self.check_command([self.singbox, "check", "-c", str(path)], env=env)
            command = [self.singbox, "run", "-c", str(path)]
        process = CoreProcess(self.stack, command, self.root / f"{client}-client-{port}.log", env)
        process.start(port)
        return process, port

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
            except (OSError, TimeoutError) as exc:
                last = exc
            time.sleep(.08)
        logs = "\n".join(path.read_text(errors="replace") for path in self.root.glob("*.log"))
        self.fail(f"VLESS/WS/TLS did not reach the HTTP target: {last}\n{logs}")

    def assert_failure_without_direct(self, port):
        before = len(self.requests)
        try:
            status, _ = http_through(port, "127.0.0.1", self.target_port)
            self.assertGreaterEqual(status, 400)
        except (OSError, TimeoutError):
            pass
        # Allow any incorrectly asynchronous fallback to expose itself too.
        time.sleep(.1)
        self.assertEqual(len(self.requests), before,
                         "Failed WS proxy must not reach the reachable IP target through DIRECT")

    def evidence(self, process, marker):
        deadline = time.monotonic() + 2
        log = ""
        while time.monotonic() < deadline:
            log = process.log_path.read_text(errors="replace").lower()
            if marker in log:
                return log
            time.sleep(.05)
        return log

    def verify_both_clients(self, *, failure=None):
        grant = self.issue_grant()
        passed = 0
        for client in ("mihomo", "singbox"):
            with self.subTest(client=client, failure=failure):
                process, port = self.start_client(client, grant, trust=failure != "ca")
                try:
                    if failure is None:
                        self.assert_success(port)
                    else:
                        self.assert_failure_without_direct(port)
                        self.assertIsNone(process.process.poll(), process.log_path.read_text())
                        self.assertIsNone(self.server.process.poll(), self.server.log_path.read_text())
                        if failure in {"ca", "sni"}:
                            log = self.evidence(process, "x509")
                            self.assertIn("x509", log)
                            self.assertIn("unknown authority" if failure == "ca" else "wrong.example.test", log)
                            print(f"VLESS/WS/TLS {client} {failure}: " + next(line for line in log.splitlines() if "x509" in line))
                finally:
                    process.stop()
                passed += 1
        if passed == 2:
            print(f"VLESS/WS/TLS both clients: {failure or 'HTTP success'}; reachable IP target, no DIRECT fallback")
        return passed == 2

    def test_public_uri_and_client_exports_preserve_ws_profile(self):
        for fingerprint, alpn, host in product((False, True), (False, True), ("", WS_HOST)):
            with self.subTest(fingerprint=fingerprint, alpn=alpn, host=host):
                self.configure_profile(fingerprint=fingerprint, alpn=alpn, host=host)
                grant = self.issue_grant()
                self.assert_uri(grant)
                self.exported_mihomo(grant, unused_port())
                self.exported_singbox(grant, unused_port())

    def test_default_ws_tls_with_host_in_both_real_clients(self):
        self.verify_both_clients()

    def test_default_ws_tls_without_host_in_both_real_clients(self):
        self.configure_profile(host="")
        self.verify_both_clients()

    def test_optional_chrome_http11_with_host_in_both_real_clients(self):
        self.configure_profile(fingerprint=True, alpn=True)
        self.verify_both_clients()

    def test_optional_chrome_http11_without_host_in_both_real_clients(self):
        self.configure_profile(fingerprint=True, alpn=True, host="")
        self.verify_both_clients()

    def test_chrome_only_with_default_alpn_in_both_real_clients(self):
        self.configure_profile(fingerprint=True)
        self.verify_both_clients()

    def test_http11_only_without_fingerprint_in_both_real_clients(self):
        self.configure_profile(alpn=True)
        self.verify_both_clients()

    def test_different_valid_ws_host_is_accepted_as_client_metadata(self):
        self.update_export(host="another.example.test")
        if self.verify_both_clients():
            print("sing-box WS server accepts different Host: metadata is not a server authorization boundary")

    def test_wrong_uuid_is_rejected_by_both_clients_without_direct(self):
        self.update_export(uuid="55555555-5555-5555-5555-555555555555")
        self.verify_both_clients(failure="uuid")

    def test_untrusted_ca_is_rejected_by_both_clients_without_direct(self):
        self.verify_both_clients(failure="ca")

    def test_wrong_sni_is_rejected_by_both_clients_without_direct(self):
        self.update_export(sni="wrong.example.test")
        self.verify_both_clients(failure="sni")

    def test_wrong_ws_path_is_rejected_by_both_clients_without_direct(self):
        self.update_export(path="/different-path")
        self.verify_both_clients(failure="path")


if __name__ == "__main__":
    unittest.main()
