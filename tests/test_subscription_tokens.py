import base64
import logging
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import yaml

from app.models import database
from app.services import auth_service
from app.services.subscription_tokens import SubscriptionGrant, digest, normalize_server
from app.services.log_redaction import SubscriptionURLFilter
import main

ORIGIN = "https://panel.example.test"
HEADERS = {"Origin": ORIGIN, "X-VUI-Request": "1"}
PASSWORD = "Synthetic-grant-test-password!"


class SubscriptionTokenTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vui-grant-")
        self.addCleanup(temp.cleanup)
        engine = create_engine(f"sqlite:///{temp.name}/data.db", connect_args={"check_same_thread": False})
        self.addCleanup(engine.dispose)
        self.addCleanup(patch.stopall)
        patch.multiple(database, engine=engine, SessionLocal=sessionmaker(bind=engine, autoflush=False)).start()
        patch.object(main, "DB_PATH", Path(temp.name) / "data.db").start()
        patch.dict(os.environ, {"VUI_PUBLIC_ORIGIN": ORIGIN}).start()
        self.client = TestClient(main.app, base_url=ORIGIN, client=("127.0.0.1", 55000), follow_redirects=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        auth_service.provision_admin("tester", PASSWORD)
        self.assertEqual(self.client.post("/api/auth/login", headers=HEADERS,
            json={"username": "tester", "password": PASSWORD}).status_code, 200)
        with database.SessionLocal() as db:
            for n in (1, 2):
                db.add(database.Inbound(id=n, core="sing-box", protocol="vless", remark=f"node-{n}",
                    port=10000+n, enable=True, settings={"users": [{"uuid": f"11111111-1111-1111-1111-11111111111{n}"}]},
                    stream_settings={"tls": {"enabled": True, "server_name": "vpn.example.test"},
                        "_vui": {"security": "tls", "server_name": "vpn.example.test"}}))
            db.commit()

    def issue(self, **kwargs):
        response = self.client.post("/api/subscriptions", headers=HEADERS, json={
            "label": "my laptop", "server": "vpn.example.test", "inbound_ids": [1],
            "formats": ["mihomo.yaml", "raw"], **kwargs})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def public_get(self, path, **kwargs):
        with TestClient(main.app, base_url=ORIGIN, client=("127.0.0.1", 55001)) as public:
            return public.get(path, **kwargs)

    def test_grant_exports_only_selected_node_without_admin_cookie(self):
        grant = self.issue()
        response = self.public_get(grant["paths"]["mihomo.yaml"])
        self.assertEqual(response.status_code, 200)
        data = yaml.safe_load(response.text)
        self.assertEqual([n["name"] for n in data["proxies"]], ["node-1"])
        self.assertEqual(data["proxies"][0]["server"], "vpn.example.test")
        self.assertNotIn("11111111-1111-1111-1111-111111111112", response.text)
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertNotIn("set-cookie", response.headers)

    def test_database_and_listing_never_return_cleartext_token(self):
        grant = self.issue()
        with database.SessionLocal() as db:
            row = db.get(SubscriptionGrant, grant["id"])
            self.assertEqual(row.token_hash, digest(grant["token"]))
            self.assertNotIn(grant["token"], repr(row.__dict__))
        listed = self.client.get("/api/subscriptions")
        self.assertNotIn(grant["token"], listed.text)
        self.assertNotIn("token_hash", listed.text)

    def test_scope_format_and_query_cannot_be_overridden(self):
        grant = self.issue(formats=["raw"])
        url = grant["paths"]["raw"]
        self.assertEqual(self.public_get(url.replace('/raw', '/sing-box.json')).status_code, 404)
        self.assertEqual(self.public_get(url + "?host=attacker.test&core=xray").status_code, 404)
        decoded = base64.b64decode(self.public_get(url).text).decode()
        self.assertIn("vpn.example.test:10001", decoded)
        self.assertNotIn("10002", decoded)

    def test_subscription_token_has_no_management_authority(self):
        grant = self.issue()
        with TestClient(main.app, base_url=ORIGIN, client=("127.0.0.1", 55001)) as public:
            public.cookies.set("__Host-vui_session", grant["token"])
            for path in ("/api/inbounds", "/api/subscriptions", "/api/auth/me", "/api/subscription/raw"):
                self.assertEqual(public.get(path, headers={"Authorization": "Bearer " + grant["token"]}).status_code, 401)

    def test_rotate_revokes_old_link_and_creates_new_link(self):
        old = self.issue()
        response = self.client.post(f"/api/subscriptions/{old['id']}/rotate", headers=HEADERS, json={"expires_days": 10})
        self.assertEqual(response.status_code, 200)
        new = response.json()
        self.assertNotEqual(old["token"], new["token"])
        self.assertEqual(self.public_get(old["paths"]["raw"]).status_code, 404)
        self.assertEqual(self.public_get(new["paths"]["raw"]).status_code, 200)

    def test_revoke_and_expiry_are_persistent(self):
        grant = self.issue()
        self.assertEqual(self.client.delete(f"/api/subscriptions/{grant['id']}", headers=HEADERS).status_code, 200)
        self.assertEqual(self.public_get(grant["paths"]["raw"]).status_code, 404)
        grant = self.issue()
        with database.SessionLocal() as db:
            db.get(SubscriptionGrant, grant["id"]).expires_at = int(time.time()) - 1
            db.commit()
        self.assertEqual(self.public_get(grant["paths"]["raw"]).status_code, 404)

    def test_disabled_account_and_password_reset_revoke_grants(self):
        grant = self.issue()
        auth_service.provision_admin("tester", "Another-synthetic-test-password!", reset=True)
        self.assertEqual(self.public_get(grant["paths"]["raw"]).status_code, 404)
        with database.SessionLocal() as db:
            db.query(database.User).update({"is_active": False})
            db.commit()
        self.assertEqual(self.public_get(grant["paths"]["raw"]).status_code, 404)

    def test_disabled_or_deleted_node_never_becomes_direct_fallback(self):
        grant = self.issue()
        with database.SessionLocal() as db:
            db.get(database.Inbound, 1).enable = False
            db.commit()
        response = self.public_get(grant["paths"]["mihomo.yaml"])
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("DIRECT", response.text)

    def test_bad_token_and_wrong_host_return_no_private_data(self):
        grant = self.issue()
        self.assertEqual(self.public_get('/sub/not-a-token/raw').status_code, 404)
        self.assertEqual(self.public_get(grant["paths"]["raw"], headers={"Host": "attacker.test"}).status_code, 404)

    def test_create_requires_known_enabled_nodes_and_strict_schema(self):
        for payload in ({"inbound_ids": []}, {"inbound_ids": [99]}, {"inbound_ids": [True]},
                        {"formats": ["unknown"]}, {"server": "https://vpn.example.test"},
                        {"owner_id": 999}, {"expires_days": 0}):
            response = self.client.post("/api/subscriptions", headers=HEADERS, json={
                "label": "test", "server": "vpn.example.test", "inbound_ids": [1], **payload})
            self.assertEqual(response.status_code, 422)

    def test_grant_writes_require_csrf_header(self):
        self.assertEqual(self.client.post("/api/subscriptions", json={}).status_code, 403)
        self.assertEqual(self.client.post("/api/subscriptions/1/rotate", json={}).status_code, 403)

    def test_access_logs_redact_token_format_and_query(self):
        token = "vui_s_" + "A" * 43
        record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0,
            '%s - "%s %s HTTP/%s" %s', ("127.0.0.1", "GET", f"/sub/{token}/raw?x={token}", "1.1", 200), None)
        SubscriptionURLFilter().filter(record)
        self.assertNotIn(token, record.getMessage())
        self.assertIn("[redacted]", record.getMessage())

    def test_connection_address_normalization(self):
        self.assertEqual(normalize_server("VPN.Example.Test."), "vpn.example.test")
        self.assertEqual(normalize_server("[2001:db8::1]"), "2001:db8::1")
        for value in ("bad host", "0.0.0.0", "::", "999.1.2.3", "example.test:443", "name@host", "example.test/path"):
            with self.subTest(value=value), self.assertRaises(ValueError): normalize_server(value)


if __name__ == "__main__":
    unittest.main()
