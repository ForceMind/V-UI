"""Origin canonicalization must not downgrade HTTPS session cookies."""
import os
import unittest
from unittest.mock import patch

from fastapi import Response
from starlette.requests import Request

from app.api.auth import clear_cookie
from app.middleware.auth import configured_origin, cookie_name, expected_origin


class OriginNormalizationTests(unittest.TestCase):
    def test_mixed_case_https_keeps_secure_host_cookie(self):
        with patch.dict(os.environ, {"VUI_PUBLIC_ORIGIN": "HTTPS://PANEL.EXAMPLE.TEST:443/"}):
            self.assertEqual(configured_origin(), "https://panel.example.test")
            self.assertEqual(cookie_name(), "__Host-vui_session")
            response = Response()
            clear_cookie(response)
            self.assertIn("Secure", response.headers["set-cookie"])
            request = Request({"type": "http", "scheme": "https", "path": "/api/auth/me",
                               "query_string": b"", "headers": [(b"host", b"panel.example.test")],
                               "client": ("127.0.0.1", 50000)})
            self.assertEqual(expected_origin(request), "https://panel.example.test")

    def test_nondefault_port_and_ipv6_are_preserved(self):
        with patch.dict(os.environ, {"VUI_PUBLIC_ORIGIN": "https://[::1]:8443"}):
            self.assertEqual(configured_origin(), "https://[::1]:8443")
            self.assertEqual(cookie_name(), "__Host-vui_session")

    def test_local_http_remains_local_cookie_only(self):
        with patch.dict(os.environ, {"VUI_PUBLIC_ORIGIN": "HTTP://LOCALHOST:80/"}):
            self.assertEqual(configured_origin(), "http://localhost")
            self.assertEqual(cookie_name(), "vui_local_session")


if __name__ == "__main__":
    unittest.main()
