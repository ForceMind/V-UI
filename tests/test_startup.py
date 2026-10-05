"""Real Uvicorn startup/auth on loopback with temporary data and no proxy cores."""
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

PASSWORD = "Synthetic-startup-password!"


def check_browser(base_url):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            # The account pages use only local assets. Other panel CDN widgets
            # are outside this auth smoke test; never depend on them for login.
            page.route("https://unpkg.com/**", lambda route: route.abort())
            page.route("https://cdnjs.cloudflare.com/**", lambda route: route.abort())
            page.goto(base_url + "/login")
            expect(page.locator("#login-form")).to_be_visible()
            page.locator("#login-name").fill("tester")
            page.locator("#login-password").fill("wrong-password")
            page.get_by_role("button", name="登录面板").click()
            expect(page.locator("#message")).to_contain_text("不正确")
            page.locator("#login-password").fill(PASSWORD)
            page.get_by_role("button", name="登录面板").click()
            page.wait_for_url("**/ui/", wait_until="domcontentloaded")
            assert context.request.get(base_url + "/api/auth/me").status == 200
            page.goto(base_url + "/account")
            expect(page.locator("#current-name")).to_have_text("tester")
            page.reload()
            expect(page.locator("#account-panel")).to_be_visible()
            assert page.evaluate("Object.keys(localStorage).length + Object.keys(sessionStorage).length") == 0
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.locator("#profile-name").fill("tester-new")
            page.locator("#current-password").fill(PASSWORD)
            page.get_by_role("button", name="保存并退出全部会话").click()
            expect(page.locator("#login-form")).to_be_visible()
            assert context.request.get(base_url + "/api/auth/me").status == 401
            page.locator("#login-name").fill("tester-new")
            page.locator("#login-password").fill(PASSWORD)
            page.get_by_role("button", name="登录面板").click()
            page.wait_for_url("**/ui/", wait_until="domcontentloaded")
            page.goto(base_url + "/account")
            expect(page.locator("#current-name")).to_have_text("tester-new")
            page.get_by_role("button", name="退出登录", exact=True).click()
            expect(page.locator("#login-form")).to_be_visible()
            assert context.request.get(base_url + "/api/subscription/mihomo.yaml").status == 401
            print("Browser auth smoke: login / reload / rename / re-login / logout / mobile layout OK")
        finally:
            browser.close()


class ApplicationStartupTests(unittest.TestCase):
    def test_clean_install_starts_serves_ui_and_initializes_database(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="vui-startup-") as directory:
            work = Path(directory)
            binaries = work / "bin"
            binaries.mkdir()
            env = dict(os.environ)
            env.update({"PYTHONPATH": str(root), "VUI_DATA_DIR": str(work / "data"),
                        "VUI_BIN_DIR": str(binaries), "VUI_PUBLIC_ORIGIN": "", "PYTHONUNBUFFERED": "1"})
            log_path = work / "server.log"
            opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
            with log_path.open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "0",
                     "--lifespan", "on", "--no-use-colors", "--no-proxy-headers"],
                    cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
                try:
                    deadline, match = time.monotonic() + 20, None
                    while time.monotonic() < deadline:
                        output = log_path.read_text(encoding="utf-8")
                        if process.poll() is not None:
                            self.fail("Application exited during startup:\n" + output[-8000:])
                        match = re.search(r"Uvicorn running on http://127\.0\.0\.1:(\d+)", output)
                        if match: break
                        time.sleep(0.05)
                    self.assertIsNotNone(match, "Startup timed out:\n" + log_path.read_text(encoding="utf-8")[-8000:])
                    base_url = "http://127.0.0.1:" + match.group(1)
                    with self.assertRaises(HTTPError) as denied:
                        opener.open(base_url + "/openapi.json", timeout=3)
                    self.assertEqual(denied.exception.code, 401)
                    # Provision synthetic credentials locally; no public signup,
                    # no password in process arguments, no real deployment data.
                    setup = subprocess.run([sys.executable, "-c",
                        "import sys; from app.services.auth_service import provision_admin; provision_admin('tester', sys.stdin.read())"],
                        input=PASSWORD, text=True, cwd=work, env=env, capture_output=True, timeout=10)
                    self.assertEqual(setup.returncode, 0, setup.stderr)
                    login = Request(base_url + "/api/auth/login",
                        data=json.dumps({"username": "tester", "password": PASSWORD}).encode(),
                        headers={"Content-Type": "application/json", "Origin": base_url, "X-VUI-Request": "1"})
                    with opener.open(login, timeout=3) as response:
                        self.assertEqual(response.status, 200)
                    with opener.open(base_url + "/openapi.json", timeout=3) as response:
                        schema = json.load(response)
                    for path in ("/api/inbounds", "/api/cores/status", "/api/files/upload_site", "/api/subscription/mihomo.yaml"):
                        self.assertIn(path, schema["paths"])
                    for path, marker in (("/ui/", "V-UI"), ("/ui/js/app.js", "app.mount"), ("/account", "current-password")):
                        with opener.open(base_url + path, timeout=3) as response:
                            self.assertEqual(response.status, 200)
                            self.assertIn(marker, response.read().decode("utf-8"))
                    database_path = work / "data" / "v-ui.db"
                    self.assertTrue(database_path.is_file())
                    with sqlite3.connect(database_path) as connection:
                        self.assertIn("core", {row[1] for row in connection.execute("PRAGMA table_info(inbounds)")})
                        self.assertEqual(connection.execute("SELECT COUNT(*) FROM inbounds").fetchone()[0], 0)
                        self.assertEqual(connection.execute("SELECT COUNT(*) FROM admin_sessions").fetchone()[0], 1)
                    self.assertEqual(list(binaries.iterdir()), [])
                    if os.getenv("VUI_BROWSER_CHECK") == "1":
                        check_browser(base_url)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try: process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
            if os.name == "posix":
                self.assertIn(process.returncode, (0, -signal.SIGTERM))
                self.assertIn("Application shutdown complete", log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
