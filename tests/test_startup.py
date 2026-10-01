"""Start the real ASGI app with isolated data and no proxy core binaries."""

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
from urllib.request import ProxyHandler, build_opener


class ApplicationStartupTests(unittest.TestCase):
    def test_clean_install_starts_serves_ui_and_initializes_database(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="vui-startup-") as directory:
            work = Path(directory)
            binaries = work / "bin"
            binaries.mkdir()
            env = dict(os.environ)
            env.update({
                "PYTHONPATH": str(root),
                "VUI_DATA_DIR": str(work / "data"),
                "VUI_BIN_DIR": str(binaries),
                "PYTHONUNBUFFERED": "1",
            })
            log_path = work / "server.log"
            # Bypass environment proxy settings for these loopback-only checks.
            opener = build_opener(ProxyHandler({}))
            with log_path.open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "main:app",
                     "--host", "127.0.0.1", "--port", "0",
                     "--lifespan", "on", "--no-use-colors"],
                    cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT,
                )
                try:
                    deadline = time.monotonic() + 20
                    match = None
                    while time.monotonic() < deadline:
                        output = log_path.read_text(encoding="utf-8")
                        if process.poll() is not None:
                            self.fail("Application exited during startup:\n" + output[-8000:])
                        match = re.search(r"Uvicorn running on http://127\.0\.0\.1:(\d+)", output)
                        if match:
                            break
                        time.sleep(0.05)
                    self.assertIsNotNone(match, "Startup timed out:\n" + log_path.read_text(encoding="utf-8")[-8000:])
                    base_url = "http://127.0.0.1:" + match.group(1)

                    with opener.open(base_url + "/openapi.json", timeout=3) as response:
                        schema = json.load(response)
                    for path in ("/api/inbounds", "/api/cores/status",
                                 "/api/files/upload_site", "/api/subscription/mihomo.yaml"):
                        self.assertIn(path, schema["paths"])

                    with opener.open(base_url + "/ui/", timeout=3) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn("V-UI", response.read().decode("utf-8"))
                    with opener.open(base_url + "/ui/js/app.js", timeout=3) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn("app.mount", response.read().decode("utf-8"))

                    database = work / "data" / "v-ui.db"
                    self.assertTrue(database.is_file(), "Startup must create SQLite in the isolated data directory")
                    with sqlite3.connect(database) as connection:
                        columns = {row[1] for row in connection.execute("PRAGMA table_info(inbounds)")}
                        self.assertIn("core", columns)
                        self.assertEqual(connection.execute("SELECT COUNT(*) FROM inbounds").fetchone()[0], 0)
                    self.assertEqual(list(binaries.iterdir()), [])
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
            if os.name == "posix":
                self.assertIn(process.returncode, (0, -signal.SIGTERM))
                self.assertIn("Application shutdown complete", log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
