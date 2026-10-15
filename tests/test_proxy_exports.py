"""Strict-export state/auth tests with fake runtimes, not network evidence."""
import copy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import yaml

from app.api import proxy_exports as api
from app.middleware.auth import AdminAuthMiddleware
from app.services.core_manager import SingBoxAdapter
from app.services.proxy_apply import ProxyApplyCoordinator
from app.services.proxy_export import DNS_INBOUND, DNS_TAG, RELAY_TAG, RelayExport, StrictExportError, StrictRelayExporter
from app.services.proxy_policy import VerifiedEndpoint
from app.services.proxy_store import ProxyConflict, ProxyStore
from tests.test_proxy_apply import FakeRuntime, row
from tests.test_proxy_store import FAKE_PASSWORD, FAKE_UUID, FAKE_UUID_2, profile, writable_settings


class ExportFixture:
    def setup_export(self):
        directory = tempfile.TemporaryDirectory(prefix="vui-strict-export-")
        self.addCleanup(directory.cleanup)
        self.store = ProxyStore(Path(directory.name))
        self.runtime, self.xray = FakeRuntime(), FakeRuntime()
        self.runtime.active_config = lambda: copy.deepcopy(self.runtime.saved[self.runtime.record["revision"]])
        self.lock = threading.RLock()
        self.rows = [row()]
        self.rows[0].remark = "domestic ingress"
        self.manager = SimpleNamespace(lock=self.lock, _all_rows=lambda: self.rows,
            get=lambda name: SimpleNamespace(runtime=self.runtime if name == "sing-box" else self.xray))
        self.coordinator = ProxyApplyCoordinator(self.store, self.runtime, SingBoxAdapter().build_config, self.lock,
            rows_provider=lambda: self.rows, preflight=lambda *_: None,
            address_provider=lambda _: ("127.0.0.1",),
            resolver=lambda pid, rev, value, **kw: VerifiedEndpoint(pid, rev, value["server"], ("8.8.4.4",), 1))
        self.manager.proxy_coordinator = self.coordinator
        self.exporter = StrictRelayExporter(self.manager)
        overseas = profile()
        overseas["uuid"] = FAKE_UUID_2
        added = self.store.save_upstream(overseas, self.store.read_snapshot()["revision"])
        settings = writable_settings(added["snapshot"])
        settings.update(selected_upstream_id=added["upstream_id"], relay_enabled=True,
                        local_username="test-local")
        self.saved = self.store.save_draft(settings, added["snapshot"]["revision"], local_password=FAKE_PASSWORD)
        self.rev = self.saved["revision"]

    def commit(self):
        self.coordinator.apply(self.rev, "test-export-commit")

    def exported(self, kind="sing-box", **kwargs):
        return self.exporter.export(kind, kwargs.get("rev", self.rev), kwargs.get("inbound", 1),
                                    kwargs.get("server", "8.8.8.8"))


class StrictExportTests(ExportFixture, unittest.TestCase):
    def setUp(self):
        self.setup_export()

    def test_only_committed_relay_and_never_saved_draft_is_exportable(self):
        with self.assertRaisesRegex(StrictExportError, "RELAY_NOT_ACTIVE"):
            self.exported()
        self.commit()
        before = self.exported()
        draft = writable_settings(self.store.read_snapshot())
        draft.update(relay_enabled=False)
        newer = self.store.save_draft(draft, self.rev)
        self.assertNotEqual(newer["revision"], self.rev)
        self.assertEqual(before, self.exported())
        with self.assertRaises(ProxyConflict):
            self.exported(rev=newer["revision"])
        self.coordinator.apply(newer["revision"], "relay-off")
        with self.assertRaisesRegex(StrictExportError, "RELAY_NOT_ACTIVE"):
            self.exported(rev=newer["revision"])

    def test_complete_configs_only_include_existing_domestic_credential_and_verified_dns(self):
        self.commit()
        for kind in ("mihomo", "sing-box"):
            artifact = self.exported(kind)
            self.assertEqual(artifact.revision, self.rev)
            self.assertIn(FAKE_UUID, artifact.content)
            for secret in (FAKE_UUID_2, FAKE_PASSWORD, "upstream.example", "test-only-fake-cert", "test-only-fake-key"):
                self.assertNotIn(secret, artifact.content)
            self.assertNotIn(FAKE_UUID, repr(artifact))
            self.assertNotIn("DIRECT", artifact.content)
            self.assertNotIn("tun", artifact.content)
            config = yaml.safe_load(artifact.content)
            if kind == "sing-box":
                self.assertEqual(config["inbounds"][0]["listen"], "127.0.0.1")
                self.assertEqual(len(config["outbounds"]), 1)
                node = config["outbounds"][0]
                self.assertEqual(node["tls"], {"enabled": True, "server_name": "node.example"})
                self.assertEqual(node["network"], "tcp")
                self.assertNotIn("packet_encoding", node)
                self.assertEqual(config["route"]["rules"], [
                    {"inbound": [DNS_INBOUND], "action": "hijack-dns"}, {"network": "udp", "action": "reject"}])
                self.assertFalse(any(value.get("action") == "resolve" for value in config["route"]["rules"]))
                self.assertEqual(config["inbounds"][1], {"type": "direct", "tag": DNS_INBOUND,
                                                       "listen": "127.0.0.1", "listen_port": 1053})
                self.assertFalse(any(value["type"] == "direct" for value in config["outbounds"]))
                self.assertEqual(config["route"]["final"], RELAY_TAG)
                self.assertEqual(config["dns"]["final"], DNS_TAG)
                self.assertEqual(config["dns"]["servers"][0]["detour"], RELAY_TAG)
                self.assertEqual(config["dns"]["servers"][0]["tls"]["server_name"], "cloudflare-dns.com")
            else:
                self.assertEqual(config["bind-address"], "127.0.0.1")
                self.assertFalse(config["allow-lan"])
                self.assertEqual(config["proxy-groups"], [{"name": "GLOBAL", "type": "select", "proxies": [RELAY_TAG]}])
                self.assertEqual(config["rules"], ["NETWORK,UDP,REJECT", "MATCH," + RELAY_TAG])
                self.assertEqual(len(config["proxies"]), 1)
                node = config["proxies"][0]
                self.assertFalse(node["udp"])
                self.assertNotIn("packet-encoding", node)
                self.assertFalse(node["skip-cert-verify"])
                self.assertEqual(node["servername"], "node.example")
                dns = config["dns"]
                self.assertTrue(dns["enable"])
                self.assertFalse(dns["use-system-hosts"])
                self.assertFalse(dns["prefer-h3"])
                self.assertEqual(dns["fallback"], [])
                for name in ("nameserver", "default-nameserver", "proxy-server-nameserver"):
                    self.assertEqual(dns[name], ["https://1.1.1.1/dns-query#" + RELAY_TAG +
                                                  "&name-cert-verify=cloudflare-dns.com"])
            self.assertEqual(node["server"], "8.8.8.8")

    def test_public_literal_only_no_implicit_bootstrap_or_user_defined_dns(self):
        self.commit()
        for server in ("example.test", "https://8.8.8.8", "8.8.8.8:443", "8.8.8.8\n", "127.0.0.1",
                       "10.1.2.3", "100.100.100.200", "169.254.169.254", "192.0.2.1", "::1",
                       "::ffff:8.8.8.8", "fe80::1%eth0", "[2606:4700:4700::1111]", None, True):
            with self.subTest(server=server), self.assertRaisesRegex(StrictExportError, "PUBLIC_IP_REQUIRED"):
                self.exported(server=server)
        self.assertIn("2606:4700:4700::1111", self.exported(server="2606:4700:4700::1111").content)

    def test_changed_all_rows_and_unqualified_ingress_cannot_export_stale_state(self):
        self.commit()
        self.rows[0].port += 1
        with self.assertRaisesRegex(StrictExportError, "STATE_MISMATCH"):
            self.exported()
        self.rows[0].port -= 1
        self.rows.append(row(2, 24444))
        self.rows[-1].core = "xray"
        with self.assertRaisesRegex(StrictExportError, "UNQUALIFIED_INGRESS"):
            self.exported()
        self.rows[-1].enable = False
        self.assertEqual(self.exported().revision, self.rev)
        for identifier in (2, 99):
            with self.assertRaisesRegex(StrictExportError, "INGRESS_NOT_FOUND"):
                self.exported(inbound=identifier)

    def test_pending_stopped_corrupt_mismatched_and_residual_core_are_blocked(self):
        self.commit()
        self.runtime.running = False
        with self.assertRaisesRegex(StrictExportError, "RUNTIME_UNAVAILABLE"):
            self.exported()
        self.runtime.running = True
        self.runtime.saved[self.runtime.record["revision"]]["route"]["final"] = "tampered"
        with self.assertRaisesRegex(StrictExportError, "RUNTIME_UNAVAILABLE"):
            self.exported()
        self.runtime.saved[self.runtime.record["revision"]] = self.coordinator.compile_active(self.rows).config
        self.xray.running = True
        with self.assertRaisesRegex(StrictExportError, "STATE_MISMATCH"):
            self.exported()
        self.xray.running = False
        self.store.begin_apply(self.rev, "pending-export")
        with self.assertRaises(ProxyConflict):
            self.exported()

    def test_exact_strict_inputs_and_operation_lock_cover_all_reads(self):
        self.commit()
        for rev, identifier in (("a"*63, 1), (self.rev + "\n", 1), (self.rev, True), (self.rev, "1")):
            with self.assertRaisesRegex(StrictExportError, "INVALID_STRICT_EXPORT_REQUEST"):
                self.exported(rev=rev, inbound=identifier)
        original = self.manager._all_rows
        def rows():
            self.assertTrue(self.lock._is_owned())
            return original()
        self.manager._all_rows = rows
        active = self.runtime.active_config
        def read_config():
            self.assertTrue(self.lock._is_owned())
            return active()
        self.runtime.active_config = read_config
        calls = list(self.runtime.calls)
        self.exported()
        self.assertEqual(self.runtime.calls, calls, "Export must not check/apply/start/stop a core")


ORIGIN = "https://panel.example.test"
HEADERS = {"Origin": ORIGIN, "X-VUI-Request": "1"}
REVISION = "a" * 64


class StrictExportApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.add_middleware(AdminAuthMiddleware)
        app.include_router(api.router, prefix="/api/proxy")
        self.addCleanup(patch.stopall)
        patch("app.middleware.auth.configured_origin", return_value=ORIGIN).start()
        self.auth = patch("app.middleware.auth.authenticate", return_value={"id": 1}).start()
        self.service = MagicMock()
        self.service.export.return_value = RelayExport("test-only-credential", "application/yaml", "vui-strict-relay.yaml", REVISION)
        patch.object(api, "get_exporter", return_value=self.service).start()
        self.client = TestClient(app, base_url=ORIGIN)
        self.payload = {"base_revision": REVISION, "inbound_id": 1, "server": "8.8.8.8"}

    def post(self, payload=None, headers=None, suffix="mihomo.yaml"):
        return self.client.post("/api/proxy/exports/" + suffix, json=self.payload if payload is None else payload,
                                headers=HEADERS if headers is None else headers)

    def test_post_attachment_exact_revision_and_never_cache(self):
        for kind, suffix in (("mihomo", "mihomo.yaml"), ("sing-box", "sing-box.json")):
            result = self.post(suffix=suffix)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.text, "test-only-credential")
            self.assertEqual(result.headers["etag"], '"' + REVISION + '"')
            self.assertIn("no-store", result.headers["cache-control"])
            self.assertIn("attachment;", result.headers["content-disposition"])
            self.service.export.assert_called_with(kind, REVISION, 1, "8.8.8.8")
            self.assertEqual(self.client.get("/api/proxy/exports/" + suffix).status_code, 405)

    def test_admin_origin_csrf_and_bare_router_are_required(self):
        self.auth.return_value = None
        self.assertEqual(self.post().status_code, 401)
        self.auth.return_value = {"id": 1}
        for headers in ({}, {"Origin": ORIGIN}, {**HEADERS, "Origin": "https://evil.test"}):
            self.assertEqual(self.post(headers=headers).status_code, 403)
        for admin in ({"id": True}, {"id": "1"}, {"id": -1}, {}):
            self.auth.return_value = admin
            self.assertEqual(self.post().status_code, 401)
        bare = FastAPI()
        bare.include_router(api.router, prefix="/api/proxy")
        self.assertEqual(TestClient(bare).post("/api/proxy/exports/mihomo.yaml", json=self.payload).status_code, 401)
        self.service.export.assert_not_called()

    def test_strict_body_query_duplicates_and_safe_errors(self):
        for key, value in (("inbound_id", True), ("inbound_id", "1"), ("inbound_id", 0),
                           ("base_revision", REVISION + "\n"), ("server", 7), ("format", FAKE_PASSWORD),
                           ("dns", FAKE_PASSWORD), ("url", FAKE_PASSWORD)):
            result = self.post({**self.payload, key: value})
            self.assertEqual(result.status_code, 422, result.text)
            self.assertNotIn(FAKE_PASSWORD, result.text)
        self.assertEqual(self.post(suffix="mihomo.yaml?server=secret").status_code, 422)
        for raw in ('{"server":"one","server":"two"}', '{"secret":NaN}', '{"secret":"' + FAKE_PASSWORD):
            result = self.client.post("/api/proxy/exports/mihomo.yaml", content=raw,
                                      headers={**HEADERS, "Content-Type": "application/json"})
            self.assertEqual(result.status_code, 422, result.text)
            self.assertNotIn(FAKE_PASSWORD, result.text)
        self.assertEqual(self.client.post("/api/proxy/exports/mihomo.yaml", content=b"x"*2049,
                                         headers=HEADERS).status_code, 413)
        self.service.export.assert_not_called()
        for error, status, code in ((ProxyConflict(FAKE_PASSWORD), 409, "PROXY_REVISION_CONFLICT"),
                (StrictExportError("STRICT_EXPORT_PUBLIC_IP_REQUIRED"), 422, "STRICT_EXPORT_PUBLIC_IP_REQUIRED"),
                (RuntimeError(FAKE_PASSWORD), 503, "STRICT_EXPORT_RUNTIME_UNAVAILABLE")):
            self.service.export.side_effect = error
            result = self.post()
            self.assertEqual(result.status_code, status)
            self.assertEqual(result.json(), {"detail": code})
            self.assertNotIn(FAKE_PASSWORD, result.text)


if __name__ == "__main__":
    unittest.main()
