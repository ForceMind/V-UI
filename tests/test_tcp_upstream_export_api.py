"""D1 real HTTP application/auth/SQLite/import/store regression, no core runtime."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import proxy, subscription
from app.middleware.auth import cookie_name
from app.models import database
from app.services import auth_service
from app.services.core_manager import SingBoxAdapter
from app.services.proxy_policy import UPSTREAM_TAG, VerifiedEndpoint, compile_proxy_policy
from app.services.proxy_store import ProxyStore
from app.services.tcp_upstream_export import INELIGIBLE, INVALID_REQUEST
from tests.test_tcp_upstream_export import (
    PRIVATE, PUBLIC, SECRET, SERVER, SHORT_ID, UUID, expected_profile, inbound, node_state,
)
import main

ORIGIN = "https://panel.example.test"
HEADERS = {"Origin": ORIGIN, "X-VUI-Request": "1"}
PASSWORD = "Synthetic-D1-admin-password!"


class TCPUpstreamExportApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="vui-tcp-upstream-api-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        engine = create_engine(f"sqlite:///{self.root / 'source.db'}",
                               connect_args={"check_same_thread": False})
        self.addCleanup(engine.dispose)
        self.addCleanup(patch.stopall)
        patch.multiple(database, engine=engine, SessionLocal=sessionmaker(bind=engine, autoflush=False)).start()
        patch.dict(os.environ, {"VUI_PUBLIC_ORIGIN": ORIGIN}).start()
        self.store = ProxyStore(self.root / "target")
        patch.object(proxy, "get_store", return_value=self.store).start()
        self.apply = patch.object(main.core_manager, "apply_database").start()
        self.apply_proxy = patch.object(main.core_manager, "apply_proxy").start()
        self.check_proxy = patch.object(main.core_manager, "check_proxy").start()
        auth_service.provision_admin("tester", PASSWORD)
        self.client = TestClient(main.app, base_url=ORIGIN, client=("127.0.0.1", 55001))
        self.addCleanup(self.client.close)
        result = self.client.post("/api/auth/login",
            json={"username": "tester", "password": PASSWORD}, headers=HEADERS)
        self.assertEqual(result.status_code, 200, result.text)
        self.items = []
        with database.SessionLocal() as db:
            for reality, identity in ((False, 1), (True, 2)):
                item = inbound(reality, id=identity, port=10442 + identity)
                db.add(item)
                self.items.append(node_state(item))
            db.commit()
        self.snapshot()

    def snapshot(self):
        response = self.client.get("/api/proxy/settings")
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def payload(self, **changes):
        return {"format": "uri", "server": SERVER, "acknowledge_tcp_only": True, **changes}

    def export(self, identity=1, **changes):
        return self.client.post(f"/api/subscription/upstream/{identity}",
                                json=self.payload(**changes), headers=HEADERS)

    def all_files(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def assert_no_core(self):
        self.apply.assert_not_called()
        self.apply_proxy.assert_not_called()
        self.check_proxy.assert_not_called()

    def assert_fixed_error(self, response, status, detail):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json(), {"detail": detail})
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertNotIn("content-disposition", response.headers)
        self.assertNotIn("x-vui-export-profile", response.headers)
        for secret in (UUID, PRIVATE, PUBLIC, SHORT_ID, SECRET, "/private/", SERVER):
            self.assertNotIn(secret, response.text)

    def test_four_api_exports_preview_explicit_selection_and_save_are_lossless(self):
        for identity, reality in ((1, False), (2, True)):
            for fmt in ("uri", "clash-yaml"):
                with self.subTest(reality=reality, format=fmt):
                    before = self.snapshot()
                    files = self.all_files()
                    exported = self.export(identity, format=fmt)
                    self.assertEqual(exported.status_code, 200, exported.text)
                    self.assertIn("no-store", exported.headers["cache-control"])
                    self.assertEqual(exported.headers["x-vui-export-profile"], "tcp-upstream-v1")
                    extension = "txt" if fmt == "uri" else "yaml"
                    self.assertEqual(exported.headers["content-disposition"],
                                     f'attachment; filename="upstream-tcp.{extension}"')
                    self.assertEqual(exported.headers["content-type"].split(";", 1)[0],
                                     "text/plain" if fmt == "uri" else "application/yaml")
                    self.assertEqual(self.all_files(), files)
                    payload = {"format": fmt, "content": exported.text}
                    preview = self.client.post("/api/proxy/import/preview", json=payload, headers=HEADERS)
                    self.assertEqual(preview.status_code, 200, preview.text)
                    self.assertEqual(len(preview.json()["candidates"]), 1)
                    self.assertTrue(preview.json()["candidates"][0]["supported"])
                    self.assertEqual(preview.json()["ignored_sections"], [])
                    missing = self.client.post("/api/proxy/profiles", json=payload,
                        headers={**HEADERS, "If-Match": before.headers["etag"]})
                    self.assertEqual(missing.status_code, 422, missing.text)
                    self.assertEqual(self.snapshot().json(), before.json())
                    saved = self.client.post("/api/proxy/profiles", json={**payload, "candidate_index": 0},
                        headers={**HEADERS, "If-Match": before.headers["etag"]})
                    self.assertEqual(saved.status_code, 201, saved.text)
                    private = self.store.load_draft()["upstreams"][saved.json()["upstream_id"]]
                    self.assertEqual(private, expected_profile(inbound(reality, id=identity, port=10442 + identity)))
                    self.assertFalse(saved.json()["snapshot"]["settings"]["local_proxy_enabled"])
                    self.assertFalse(saved.json()["snapshot"]["settings"]["relay_enabled"])
                    self.assertIsNone(saved.json()["snapshot"]["active"])
                    # Pure policy construction is separate from applying a core:
                    # an accepted import cannot grant application UDP capability.
                    draft = self.store.load_draft()
                    selected = saved.json()["upstream_id"]
                    draft["settings"].update(relay_enabled=True, selected_upstream_id=selected)
                    rows = [inbound()]
                    policy = compile_proxy_policy(SingBoxAdapter().build_config(rows), rows, draft,
                        endpoint=VerifiedEndpoint(selected, draft["revision"], SERVER, ("8.8.8.8",), 1),
                        self_addresses=("127.0.0.1",))
                    upstream = next(row for row in policy.config["outbounds"] if row["tag"] == UPSTREAM_TAG)
                    self.assertEqual(upstream["network"], "tcp")
                    self.assertNotIn("packet_encoding", upstream)
                    self.assertEqual(policy.config["route"]["rules"][0]["network"], "udp")
                    self.assertEqual(policy.config["route"]["rules"][0]["action"], "reject")
                    for response in (preview, missing, saved, self.client.get("/api/inbounds")):
                        for secret in (UUID, PRIVATE, PUBLIC, SHORT_ID, "/private/", exported.text):
                            self.assertNotIn(secret, response.text)
        self.assert_no_core()

    def test_existing_ordinary_api_exports_stay_rejected_by_the_import_gate(self):
        for path, fmt in (("/api/subscription/link/1?host=" + SERVER, "uri"),
                          ("/api/subscription/mihomo.yaml?host=" + SERVER, "clash-yaml")):
            legacy = self.client.get(path)
            self.assertEqual(legacy.status_code, 200, legacy.text)
            marker = "packetEncoding=xudp" if fmt == "uri" else "packet-encoding: xudp"
            self.assertIn(marker, legacy.text)
            preview = self.client.post("/api/proxy/import/preview",
                json={"format": fmt, "content": legacy.text}, headers=HEADERS)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()["candidates"][0]["reason_code"], "UDP_CAPABILITY_NOT_QUALIFIED")
            self.export()
            self.assertEqual(self.client.get(path).text, legacy.text)
        self.assert_no_core()

    def test_acknowledgement_types_exact_shape_host_and_format_are_strict(self):
        original = self.snapshot().json()
        files = self.all_files()
        payloads = [None, [], True, SECRET, {}, self.payload(extra=SECRET),
            self.payload(format=SECRET), self.payload(format=None), self.payload(server=None),
            self.payload(server=123), self.payload(server=[]), self.payload(server=SECRET + "\r\nX: 1"),
            self.payload(server=" " + SERVER), self.payload(server=SERVER + "."),
            self.payload(server="https://" + SERVER), self.payload(server=SERVER + ":443"),
            self.payload(server=SERVER + "/"), self.payload(server=SERVER + "#" + SECRET)]
        for field in ("format", "server", "acknowledge_tcp_only"):
            payload = self.payload(); payload.pop(field); payloads.append(payload)
        payloads.extend(self.payload(acknowledge_tcp_only=value)
                        for value in (False, 0, 1, 1.0, "true", "false", None, [], {}))
        for index, payload in enumerate(payloads):
            with self.subTest(payload=index):
                response = self.client.post("/api/subscription/upstream/1", json=payload, headers=HEADERS)
                self.assert_fixed_error(response, 422, INVALID_REQUEST)
        self.assertEqual(self.snapshot().json(), original)
        self.assertEqual(self.all_files(), files)
        self.assert_no_core()

    def test_malformed_duplicate_nonfinite_and_wrong_content_type_are_fixed_errors(self):
        raw_bodies = ['{"server":"' + SECRET + '",',
            '{"format":"uri","server":"' + SERVER + '","acknowledge_tcp_only":false,"acknowledge_tcp_only":true}',
            '{"format":"uri","server":"' + SECRET + '","server":"' + SERVER + '","acknowledge_tcp_only":true}',
            '{"format":"uri","server":"' + SERVER + '","acknowledge_tcp_only":NaN}',
            '{"format":"uri","server":"' + SERVER + '","acknowledge_tcp_only":Infinity}',
            b'{"server":"\xff"}', '[' * 1000]
        for body in raw_bodies:
            with self.subTest(body=str(body)[:20]):
                result = self.client.post("/api/subscription/upstream/1", content=body,
                    headers={**HEADERS, "Content-Type": "application/json"})
                self.assert_fixed_error(result, 422, INVALID_REQUEST)
        for content_type in ("text/plain", "application/x-www-form-urlencoded"):
            result = self.client.post("/api/subscription/upstream/1", content=json.dumps(self.payload()),
                headers={**HEADERS, "Content-Type": content_type})
            self.assert_fixed_error(result, 422, INVALID_REQUEST)
        self.assert_no_core()

    def test_invalid_path_query_and_missing_node_never_echo_input(self):
        for suffix in (SECRET, "0", "-1", "true", "9223372036854775808", "1?server=" + SECRET):
            result = self.client.post("/api/subscription/upstream/" + suffix,
                json=self.payload(), headers=HEADERS)
            self.assert_fixed_error(result, 422, INVALID_REQUEST)
        self.assert_fixed_error(self.export(99999), 404, "TCP_UPSTREAM_INBOUND_NOT_FOUND")
        self.assert_no_core()

    def test_disabled_expiring_multiuser_and_unknown_fields_are_fixed_conflicts(self):
        changes = [lambda n: setattr(n, "enable", False), lambda n: setattr(n, "expiry_time", 1),
            lambda n: setattr(n, "expiry_time", 9999999999999), lambda n: setattr(n, "core", "xray"),
            lambda n: n.settings["users"].append(deepcopy(n.settings["users"][0])),
            lambda n: n.settings["users"][0].update(secret=SECRET),
            lambda n: n.stream_settings["tls"].update(secret=SECRET),
            lambda n: n.stream_settings.update(transport={"type": "ws", "path": "/"}),
            lambda n: n.stream_settings["_vui"].update(server_name=0)]
        for index, mutate in enumerate(changes):
            item = inbound(id=10 + index, port=11000 + index)
            mutate(item)
            with database.SessionLocal() as db:
                db.add(item); db.commit()
            files = self.all_files()
            for fmt in ("uri", "clash-yaml"):
                with self.subTest(change=index, format=fmt):
                    self.assert_fixed_error(self.export(10 + index, format=fmt), 409, INELIGIBLE)
                    self.assertEqual(self.all_files(), files)
        self.assert_no_core()

    def test_source_lookup_or_export_exception_is_fixed_and_never_a_fallback(self):
        for target in ("get_inbound", "export_tcp_upstream"):
            with patch.object(subscription, target, side_effect=RuntimeError(SECRET + UUID)):
                self.assert_fixed_error(self.export(), 503, "TCP_UPSTREAM_EXPORT_UNAVAILABLE")
        with patch.object(subscription, "get_inbound", side_effect=HTTPException(422, SECRET)):
            self.assert_fixed_error(self.export(), 503, "TCP_UPSTREAM_EXPORT_UNAVAILABLE")
        self.assert_no_core()

    def test_authenticated_same_origin_is_required_and_expired_session_is_denied(self):
        for headers in ({}, {"Origin": ORIGIN}, {"X-VUI-Request": "1"},
                        {**HEADERS, "Origin": "https://evil.example.test"}, {**HEADERS, "Origin": "null"}):
            result = self.client.post("/api/subscription/upstream/1", json=self.payload(), headers=headers)
            self.assert_fixed_error(result, 403, "Same-origin request header required")
        token = self.client.cookies.get(cookie_name())
        self.client.cookies.clear()
        self.assert_fixed_error(self.export(), 401, "Authentication required")
        self.client.cookies.set(cookie_name(), token)
        with database.SessionLocal() as db:
            db.query(auth_service.AdminSession).update({"expires_at": 1}); db.commit()
        self.assert_fixed_error(self.export(), 401, "Authentication required")
        self.assert_no_core()

    def test_real_subscription_grant_cannot_authorize_the_new_export(self):
        issued = self.client.post("/api/subscriptions", headers=HEADERS, json={
            "label": "D1 grant fixture", "server": SERVER, "inbound_ids": [1],
            "formats": ["raw"], "expires_days": 1})
        self.assertEqual(issued.status_code, 201, issued.text)
        token = issued.json()["token"]
        self.client.cookies.clear()
        self.assertEqual(self.client.get(issued.json()["paths"]["raw"]).status_code, 200)
        for headers, suffix in ((HEADERS, ""), ({**HEADERS, "Authorization": "Bearer " + token}, ""),
                                (HEADERS, "?token=" + token)):
            result = self.client.post("/api/subscription/upstream/1" + suffix,
                json=self.payload(), headers=headers)
            self.assert_fixed_error(result, 401, "Authentication required")
            self.assertNotIn(token, result.text)
        self.client.cookies.set(cookie_name(), token)
        self.assert_fixed_error(self.export(), 401, "Authentication required")
        self.assert_no_core()

    def asgi_stream(self, messages, *, delayed=False):
        consumed, sent = [], []
        token = self.client.cookies.get(cookie_name())
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "https", "path": "/api/subscription/upstream/1",
            "raw_path": b"/api/subscription/upstream/1", "query_string": b"",
            "headers": [(b"host", b"panel.example.test"), (b"origin", ORIGIN.encode()),
                (b"x-vui-request", b"1"), (b"content-type", b"application/json"),
                (b"content-length", b"1"), (b"cookie", (cookie_name() + "=" + token).encode())],
            "client": ("127.0.0.1", 55001), "server": ("panel.example.test", 443)}

        async def receive():
            if delayed:
                await asyncio.sleep(1)
            index = len(consumed)
            consumed.append(index)
            return messages[index] if index < len(messages) else {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        asyncio.run(main.app(scope, receive, send))
        start = next(message for message in sent if message["type"] == "http.response.start")
        body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
        return start["status"], json.loads(body), len(consumed)

    def test_actual_stream_size_limit_stops_reading_without_trusting_content_length(self):
        files = self.all_files()
        status, result, consumed = self.asgi_stream([
            {"type": "http.request", "body": b"x" * 1024, "more_body": True},
            {"type": "http.request", "body": b"x" * 1025, "more_body": True},
            {"type": "http.request", "body": b"never-read", "more_body": False}])
        self.assertEqual((status, result, consumed),
                         (413, {"detail": "TCP_UPSTREAM_EXPORT_REQUEST_TOO_LARGE"}, 2))
        self.assertEqual(self.all_files(), files)
        self.assert_no_core()

    def test_disconnected_and_slow_streams_never_generate_a_partial_export(self):
        files = self.all_files()
        status, result, _ = self.asgi_stream([
            {"type": "http.request", "body": b'{"server":"' + SECRET.encode(), "more_body": True},
            {"type": "http.disconnect"}])
        self.assertEqual((status, result),
                         (400, {"detail": "TCP_UPSTREAM_EXPORT_REQUEST_DISCONNECTED"}))
        actual_wait_for = asyncio.wait_for

        async def short_deadline(awaitable, timeout):
            self.assertEqual(timeout, 10)
            return await actual_wait_for(awaitable, timeout=0.01)

        with patch.object(subscription.asyncio, "wait_for", side_effect=short_deadline):
            status, result, consumed = self.asgi_stream([], delayed=True)
        self.assertEqual((status, result, consumed),
                         (408, {"detail": "TCP_UPSTREAM_EXPORT_REQUEST_TIMED_OUT"}, 0))
        self.assertEqual(self.all_files(), files)
        self.assert_no_core()


if __name__ == "__main__":
    unittest.main()
