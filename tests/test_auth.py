"""Auth against the real application, temporary SQLite, and synthetic credentials."""
import os
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import database
from app.services import auth_service
from app.middleware.auth import configured_origin, cookie_name
import main

ORIGIN = "https://panel.example.test"
HEADERS = {"Origin": ORIGIN, "X-VUI-Request": "1"}
PASSWORD = "A-synthetic-test-password!"
NEW_PASSWORD = "A-different-test-password!"


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vui-auth-")
        self.addCleanup(self.temp.cleanup)
        db_path = Path(self.temp.name) / "auth.db"
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        self.addCleanup(engine.dispose)
        sessions = sessionmaker(bind=engine, autoflush=False)
        self.addCleanup(patch.stopall)
        patch.multiple(database, engine=engine, SessionLocal=sessions).start()
        patch.object(main, "DB_PATH", db_path).start()
        patch.dict(os.environ, {"VUI_PUBLIC_ORIGIN": ORIGIN}).start()
        self.client = TestClient(main.app, base_url=ORIGIN, client=("127.0.0.1", 55001), follow_redirects=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        auth_service.provision_admin("tester", PASSWORD)

    def login(self, password=PASSWORD, username="tester"):
        return self.client.post("/api/auth/login", json={"username": username, "password": password}, headers=HEADERS)

    def test_every_registered_api_method_is_anonymous_denied(self):
        checked = 0
        for path, methods in main.app.openapi()["paths"].items():
            if not path.startswith("/api/"): continue
            for method in methods:
                if method not in {"get", "post", "put", "patch", "delete", "head", "options"}: continue
                if path == "/api/auth/login" and method == "post": continue
                concrete = re.sub(r"\{[^}]+\}", "1", path)
                with self.subTest(method=method, path=path):
                    response = self.client.request(method, concrete)
                    self.assertEqual(response.status_code, 401)
                    self.assertIn("no-store", response.headers["cache-control"])
                checked += 1
        self.assertGreater(checked, 25)

    def test_legacy_mock_and_query_token_are_not_credentials(self):
        for path in ("/api/xray/inbounds", "/api/singbox/inbounds", "/api/subscription/raw",
                     "/api/subscription/mihomo.yaml", "/api/subscription/sing-box.json",
                     "/api/subscription/link/1", "/api/routing/mihomo", "/api/unknown"):
            with self.subTest(path=path):
                response = self.client.get(path + "?token=mock-jwt-token-xyz", headers={"Authorization": "Bearer mock-jwt-token-xyz"})
                self.assertEqual(response.status_code, 401)

    def test_no_default_admin_password(self):
        self.assertEqual(self.login("admin", "admin").status_code, 401)

    def test_cookie_flags_password_hash_and_token_hash(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["set-cookie"]
        for part in ("__Host-vui_session=", "HttpOnly", "Secure", "SameSite=strict", "Path=/"):
            self.assertIn(part, cookie)
        token = self.client.cookies.get(cookie_name())
        self.assertNotIn(token, response.text)
        with database.SessionLocal() as db:
            user = db.query(database.User).one()
            session = db.query(auth_service.AdminSession).one()
            self.assertTrue(user.password_hash.startswith("$argon2id$"))
            self.assertNotIn(PASSWORD, user.password_hash)
            self.assertEqual(session.token_hash, auth_service.digest(token))
            self.assertNotEqual(session.token_hash, token)
        self.assertEqual(self.client.get("/api/auth/me").json()["username"], "tester")
        self.assertEqual(self.client.get("/api/cores/status").status_code, 200)

    def test_login_rejects_cross_origin_missing_header_and_missing_origin(self):
        for headers in ({}, {"Origin": ORIGIN}, {"X-VUI-Request": "1"},
                        {**HEADERS, "Origin": ORIGIN + ".evil.test"},
                        {**HEADERS, "Origin": "https://sibling.example.test"},
                        {**HEADERS, "Origin": "null"}):
            with self.subTest(headers=headers):
                response = self.client.post("/api/auth/login", json={"username": "tester", "password": PASSWORD}, headers=headers)
                self.assertEqual(response.status_code, 403)

    def test_session_writes_require_origin_and_custom_header(self):
        self.login()
        for headers in ({}, {"Origin": ORIGIN}, {**HEADERS, "Origin": "https://other.example.test"}):
            self.assertEqual(self.client.post("/api/auth/logout", json={}, headers=headers).status_code, 403)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)

    def test_logout_invalidates_replayed_cookie(self):
        self.login()
        token = self.client.cookies.get(cookie_name())
        self.assertEqual(self.client.post("/api/auth/logout", json={}, headers=HEADERS).status_code, 200)
        self.client.cookies.set(cookie_name(), token)
        self.assertEqual(self.client.get("/api/inbounds").status_code, 401)

    def test_new_login_rotates_previous_browser_session(self):
        self.login()
        first = self.client.cookies.get(cookie_name())
        self.login()
        second = self.client.cookies.get(cookie_name())
        self.assertNotEqual(first, second)
        self.assertIsNone(auth_service.authenticate(first))
        self.assertIsNotNone(auth_service.authenticate(second))

    def test_sustained_boundaries_refresh_without_extending_session_lifetime(self):
        self.assertEqual(auth_service.SESSION_SECONDS, 3600)
        start = int(time.time())
        with patch.object(auth_service.time, "time", return_value=start) as clock:
            self.assertEqual(self.login().status_code, 200)
            original = self.client.cookies.get("__Host-vui_session")
            clock.return_value = start + 3601
            self.assertEqual(self.client.get("/api/auth/me", headers={"Cookie": "__Host-vui_session=" + original}).status_code, 401)
            # Reproduce the sustained sequence with explicit refresh after the
            # first idle and before each load; never extend production TTL.
            self.assertEqual(self.login().status_code, 200)
            base = clock.return_value
            clock.return_value = base + 1800
            self.assertEqual(self.client.get("/api/auth/me").status_code, 200)
            self.assertEqual(self.login().status_code, 200)
            clock.return_value = base + 3601
            self.assertEqual(self.client.get("/api/auth/me").status_code, 200)
            for elapsed in (3601, 4202, 4803):
                clock.return_value = base + elapsed
                self.assertEqual(self.login().status_code, 200)
                clock.return_value += 601
                self.assertEqual(self.client.get("/api/auth/me").status_code, 200)
            self.assertEqual(self.client.get("/api/auth/me", headers={"Cookie": "__Host-vui_session=" + original}).status_code, 401)

    def test_expired_session_is_denied(self):
        self.login()
        with database.SessionLocal() as db:
            db.query(auth_service.AdminSession).update({"expires_at": int(time.time()) - 1})
            db.commit()
        self.assertEqual(self.client.get("/api/subscription/mihomo.yaml").status_code, 401)

    def test_disabled_user_is_denied(self):
        self.login()
        with database.SessionLocal() as db:
            db.query(database.User).update({"is_active": False})
            db.commit()
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.assertEqual(self.login().status_code, 401)

    def test_password_change_revokes_all_sessions_and_preserves_nodes(self):
        self.login()
        first = self.client.cookies.get(cookie_name())
        _, second = auth_service.login("tester", PASSWORD, "127.0.0.2")
        with database.SessionLocal() as db:
            db.add(database.Inbound(port=10443, core="xray", protocol="vless", remark="keep-me"))
            db.commit()
        response = self.client.post("/api/auth/update_profile", headers=HEADERS,
            json={"username": "tester-new", "current_password": PASSWORD, "password": NEW_PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(auth_service.authenticate(first))
        self.assertIsNone(auth_service.authenticate(second))
        self.assertEqual(self.login(PASSWORD, "tester-new").status_code, 401)
        self.assertEqual(self.login(NEW_PASSWORD, "tester-new").status_code, 200)
        with database.SessionLocal() as db:
            self.assertEqual(db.query(database.Inbound).one().remark, "keep-me")

    def test_profile_update_requires_current_password(self):
        self.login()
        response = self.client.post("/api/auth/update", json={"username": "tester", "current_password": "incorrect", "password": NEW_PASSWORD}, headers=HEADERS)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)

    def test_reset_from_local_admin_service_revokes_session(self):
        self.login()
        auth_service.provision_admin("tester", NEW_PASSWORD, reset=True)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.assertEqual(self.login(NEW_PASSWORD).status_code, 200)

    def test_credentials_and_session_survive_a_new_app_client(self):
        self.login()
        token = self.client.cookies.get(cookie_name())
        with TestClient(main.app, base_url=ORIGIN, client=("127.0.0.1", 55002)) as other:
            other.cookies.set(cookie_name(), token)
            self.assertEqual(other.get("/api/auth/me").status_code, 200)

    def test_limits_survive_client_restart_and_ignore_forwarded_ip(self):
        for n in range(8):
            response = self.client.post("/api/auth/login", headers={**HEADERS, "X-Forwarded-For": f"192.0.2.{n}"},
                json={"username": "tester", "password": "incorrect"})
            self.assertEqual(response.status_code, 401)
        with TestClient(main.app, base_url=ORIGIN, client=("127.0.0.1", 55002)) as other:
            response = other.post("/api/auth/login", headers=HEADERS, json={"username": "tester", "password": PASSWORD})
            self.assertEqual(response.status_code, 429)
            self.assertGreater(int(response.headers["retry-after"]), 0)
        with database.SessionLocal() as db:
            db.query(auth_service.LoginBucket).update({"resets_at": int(time.time()) - 1})
            db.commit()
        self.assertEqual(self.login().status_code, 200)

    def test_validation_errors_do_not_echo_secret_input(self):
        marker = "NEVER_ECHO_PASSWORD_PAYLOAD"
        response = self.client.post("/api/auth/login", headers=HEADERS,
            json={"username": "tester", "password": {"secret": marker}})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(marker, response.text)
        response = self.client.post("/api/auth/login", headers=HEADERS, content=b'{"password":')
        self.assertEqual(response.status_code, 422)

    def test_actual_auth_body_size_is_bounded(self):
        response = self.client.post("/api/auth/login", headers={**HEADERS, "Content-Type": "application/json"}, content=b'x' * 8193)
        self.assertEqual(response.status_code, 413)

    def test_openapi_docs_and_ui_entry_are_guarded(self):
        for path in ("/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"):
            self.assertEqual(self.client.get(path).status_code, 401)
        for path in ("/ui/", "/ui/index.html"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/login")
        self.login()
        self.assertEqual(self.client.get("/openapi.json").status_code, 200)
        self.assertEqual(self.client.get("/ui/").status_code, 200)

    def test_site_hosting_is_quarantined_and_cors_not_permissive(self):
        self.login()
        self.assertEqual(self.client.get("/api/files/list_files").status_code, 503)
        self.assertEqual(self.client.post("/api/files/upload_site", headers=HEADERS, content=b"not-a-zip").status_code, 503)
        self.assertEqual(self.client.get("/not-a-panel-file.html").status_code, 404)
        response = self.client.options("/api/auth/login", headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "POST"})
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_unsafe_public_origin_configuration_is_rejected(self):
        for value in ("http://panel.example.test", "https://u:p@panel.example.test", "https://panel.example.test/path", "https://panel.example.test:bad"):
            with self.subTest(value=value), patch.dict(os.environ, {"VUI_PUBLIC_ORIGIN": value}):
                with self.assertRaises(ValueError): configured_origin()

    def test_unknown_host_and_unconfigured_remote_peer_are_rejected(self):
        self.assertEqual(self.client.get("/api/auth/me", headers={"Host": "attacker.test"}).status_code, 403)
        with patch.dict(os.environ, {"VUI_PUBLIC_ORIGIN": ""}):
            with TestClient(main.app, base_url="http://127.0.0.1:2053", client=("192.0.2.10", 55001)) as other:
                self.assertEqual(other.get("/api/auth/me").status_code, 403)

    def test_weak_password_and_implicit_second_admin_are_rejected(self):
        with self.assertRaises(ValueError): auth_service.provision_admin("tester", "short", reset=True)
        with self.assertRaises(ValueError): auth_service.provision_admin("second-user", PASSWORD)

    def test_login_page_is_local_and_does_not_store_credentials(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn("script-src 'self'", response.headers["content-security-policy"])
        script = self.client.get("/ui/js/account.js").text
        for storage in ("localStorage", "sessionStorage"):
            self.assertNotIn(storage, script)
        self.assertIn("textContent", script)


if __name__ == "__main__":
    unittest.main()
