"""Authenticated M3 API boundary, not live-network qualification."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import proxy_diagnostics as api
from app.middleware.auth import AdminAuthMiddleware
from app.services.proxy_diagnostics import DiagnosticsError, ProxyDiagnostics, _Admission
from app.services.proxy_store import ProxyConflict, ProxyStorageError
from test_proxy_diagnostics import ID, REVISION, UUID, Store, successful

ORIGIN = "https://panel.example.test"
HEADERS = {"Origin": ORIGIN, "X-VUI-Request": "1"}
JOB = "diag_" + "1" * 32


class DiagnosticApiTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.add_middleware(AdminAuthMiddleware)
        self.app.include_router(api.router, prefix="/api/proxy")
        self.addCleanup(patch.stopall)
        patch("app.middleware.auth.configured_origin", return_value=ORIGIN).start()
        self.auth = patch("app.middleware.auth.authenticate", return_value={"id": 1}).start()
        self.service = MagicMock()
        self.service.start.return_value = {"job_id": JOB, "state": "running", "revision": REVISION}
        self.service.get.return_value = {"job_id": JOB, "state": "completed", "revision": REVISION}
        self.service.cancel.return_value = {"job_id": JOB, "state": "cancelled", "revision": REVISION}
        patch.object(api, "get_diagnostics", return_value=self.service).start()
        self.client = TestClient(self.app, base_url=ORIGIN)

    def post(self, payload=None, headers=None, path="/api/proxy/diagnostics"):
        return self.client.post(path, json={"upstream_id": ID, "base_revision": REVISION} if payload is None else payload,
                                headers=HEADERS if headers is None else headers)

    def test_start_get_and_cancel_use_authenticated_id_and_direct_job_response(self):
        started = self.post()
        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(started.json()["job_id"], JOB)
        self.service.start.assert_called_once_with(1, ID, REVISION)
        result = self.client.get("/api/proxy/diagnostics/" + JOB)
        self.assertEqual(result.status_code, 200, result.text)
        self.service.get.assert_called_once_with(1, JOB)
        cancelled = self.client.delete("/api/proxy/diagnostics/" + JOB, headers=HEADERS)
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.service.cancel.assert_called_once_with(1, JOB)
        for response in (started, result, cancelled):
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_authentication_and_same_origin_precede_service_calls(self):
        self.auth.return_value = None
        for method, path in (("post", ""), ("get", "/" + JOB), ("delete", "/" + JOB)):
            response = self.client.request(method, "/api/proxy/diagnostics" + path, headers=HEADERS)
            self.assertEqual(response.status_code, 401, response.text)
        self.auth.return_value = {"id": 1}
        for method, path in (("post", ""), ("delete", "/" + JOB)):
            for headers in ({}, {"Origin": ORIGIN}, {**HEADERS, "Origin": "https://evil.example.test"}):
                response = self.client.request(method, "/api/proxy/diagnostics" + path, headers=headers)
                self.assertEqual(response.status_code, 403, response.text)
        self.service.start.assert_not_called()
        self.service.get.assert_not_called()
        self.service.cancel.assert_not_called()

    def test_missing_or_malformed_admin_state_fails_even_without_guard(self):
        bare = FastAPI()
        bare.include_router(api.router, prefix="/api/proxy")
        client = TestClient(bare)
        self.assertEqual(client.post("/api/proxy/diagnostics", json={"upstream_id": ID, "base_revision": REVISION}).status_code, 401)
        for user in ({"id": "1"}, {"id": True}, {"id": -1}, {}, []):
            self.auth.return_value = user
            self.assertEqual(self.post().status_code, 401)
        self.service.start.assert_not_called()

    def test_strict_shape_fields_identifiers_query_and_body(self):
        payload = {"upstream_id": ID, "base_revision": REVISION}
        for key, value in (("upstream_id", True), ("upstream_id", "up_" + "A" * 32),
                           ("upstream_id", ID + "\n"), ("base_revision", 123),
                           ("base_revision", REVISION + "\n"), ("base_revision", "*"),
                           ("url", "https://evil.example/?secret=" + UUID), ("headers", {"Authorization": UUID}),
                           ("admin_id", 2), ("command", UUID), ("provider", UUID)):
            response = self.post({**payload, key: value})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn(UUID, response.text)
            self.assertEqual(response.json()["detail"], "INVALID_DIAGNOSTIC_REQUEST")
        for value in ({}, [], "secret " + UUID):
            response = self.post(value)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(UUID, response.text)
        self.assertEqual(self.post(path="/api/proxy/diagnostics?url=private").status_code, 422)
        for method in ("GET", "DELETE"):
            for path, body in ((JOB + "?url=private", None), (JOB, b"secret"), ("bad-id", None)):
                response = self.client.request(method, "/api/proxy/diagnostics/" + path, headers=HEADERS, content=body)
                self.assertEqual(response.status_code, 422, response.text)
        self.service.start.assert_not_called()
        self.service.get.assert_not_called()
        self.service.cancel.assert_not_called()

    def test_request_validation_and_oversized_body_never_echo_input(self):
        response = self.client.post("/api/proxy/diagnostics", content=b'{"secret":"' + UUID.encode(),
                                    headers={**HEADERS, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(UUID, response.text)
        response = self.client.post("/api/proxy/diagnostics", content=b"x" * 2_097_153,
                                    headers={**HEADERS, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)
        self.service.start.assert_not_called()

    def test_duplicate_json_members_are_not_silently_replaced(self):
        body = '{"upstream_id":"' + ID + '","base_revision":"' + REVISION + '","base_revision":"' + REVISION + '"}'
        response = self.client.post("/api/proxy/diagnostics", content=body,
                                    headers={**HEADERS, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 422, response.text)
        self.service.start.assert_not_called()

    def test_static_error_translation_and_retry_after(self):
        for error, expected, code in ((ProxyConflict(UUID), 409, "PROXY_REVISION_CONFLICT"),
                (ProxyStorageError(UUID), 503, "PROXY_STORAGE_UNAVAILABLE"),
                (DiagnosticsError("DIAGNOSTIC_BUSY"), 429, "DIAGNOSTIC_BUSY"),
                (DiagnosticsError("DIAGNOSTIC_RATE_LIMITED"), 429, "DIAGNOSTIC_RATE_LIMITED"),
                (DiagnosticsError("INVALID_DIAGNOSTIC_REQUEST"), 422, "INVALID_DIAGNOSTIC_REQUEST"),
                (DiagnosticsError("DIAGNOSTIC_NOT_FOUND"), 404, "DIAGNOSTIC_NOT_FOUND"),
                (ValueError(UUID), 503, "DIAGNOSTIC_UNAVAILABLE")):
            self.service.start.side_effect = error
            response = self.post()
            self.assertEqual(response.status_code, expected, response.text)
            self.assertEqual(response.json()["detail"], code)
            self.assertNotIn(UUID, response.text)
            if expected == 429:
                self.assertEqual(response.headers["retry-after"], "30")

    def test_real_service_ownership_and_conflicts_without_external_network(self):
        service = ProxyDiagnostics(Store(), _runner=successful, _admission=_Admission())
        self.addCleanup(service.close)
        with patch.object(api, "get_diagnostics", return_value=service):
            response = self.post()
            self.assertEqual(response.status_code, 202, response.text)
            job = response.json()["job_id"]
            self.assertTrue(service._jobs[job].done.wait(2))
            self.auth.return_value = {"id": 2}
            for method in ("get", "delete"):
                result = self.client.request(method, "/api/proxy/diagnostics/" + job, headers=HEADERS)
                self.assertEqual(result.status_code, 404, result.text)
            self.auth.return_value = {"id": 1}
            result = self.client.get("/api/proxy/diagnostics/" + job)
            self.assertEqual(result.json()["state"], "completed")
            self.assertNotIn(UUID, result.text)
            self.assertEqual(self.post().status_code, 429)


class DiagnosticSingletonTests(unittest.TestCase):
    def test_concurrent_access_constructs_one_and_shutdown_does_not_create(self):
        with patch.object(api, "_INSTANCE", None), patch.object(api, "ProxyDiagnostics") as constructor, \
                patch.object(api, "ProxyStore") as store, patch.object(api, "configured_origin", return_value=ORIGIN):
            api.close_diagnostics_if_created()
            constructor.assert_not_called()
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: api.get_diagnostics(), range(16)))
            self.assertTrue(all(value is results[0] for value in results))
            constructor.assert_called_once_with(store.return_value, panel_origin=ORIGIN)
            api.close_diagnostics_if_created()
            constructor.return_value.close.assert_called_once_with()
            self.assertIs(api.get_diagnostics(), results[0])
            self.assertEqual(constructor.call_count, 1)


if __name__ == "__main__":
    unittest.main()
