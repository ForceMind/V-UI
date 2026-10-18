"""M2 HTTP boundary tests; fake coordinator results are not runtime evidence."""
import json
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import main
from app.api import proxy
from app.services.core_runtime import CoreError
from app.services.proxy_apply import ProxyApplyError
from app.services.proxy_policy import PolicyError
from app.services.proxy_resolution import ResolutionError
from app.services.proxy_store import ProxyCapacityError, ProxyConflict, ProxyStorageError, ProxyStore

ORIGIN = 'https://panel.example.test'
HEADERS = {'Origin': ORIGIN, 'X-VUI-Request': '1'}
KEY = '1' * 64


class ProxyRuntimeApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='vui-proxy-runtime-api-')
        self.addCleanup(temp.cleanup)
        self.store = ProxyStore(temp.name)
        self.snapshot = self.store.read_snapshot()
        self.manager = MagicMock()
        self.manager.lock = threading.RLock()
        self.addCleanup(patch.stopall)
        patch('app.middleware.auth.configured_origin', return_value=ORIGIN).start()
        self.auth = patch('app.middleware.auth.authenticate', return_value={'id': 1}).start()
        patch.object(proxy, 'get_store', return_value=self.store).start()
        patch.object(proxy, 'get_core_manager', return_value=self.manager).start()
        self.client = TestClient(main.app, base_url=ORIGIN)

    def post(self, path='/apply', *, payload=None, headers=None):
        return self.client.post('/api/proxy' + path,
            json={'base_revision': self.snapshot['revision']} if payload is None else payload,
            headers={**HEADERS, 'Idempotency-Key': KEY} if headers is None else headers)

    def test_apply_requires_exact_revision_and_idempotency_key(self):
        result = self.post(headers=HEADERS)
        self.assertEqual(result.status_code, 428)
        for key in ('', 'short', 'A' * 64, '1' * 65, '"' + KEY + '"', KEY + ',' + KEY):
            with self.subTest(key=key):
                result = self.post(headers={**HEADERS, 'Idempotency-Key': key})
                self.assertEqual(result.status_code, 422)
        for revision in (None, True, 1, 'A' * 64, '1' * 63, self.snapshot['revision'] + '\n'):
            result = self.post(payload={'base_revision': revision})
            self.assertEqual(result.status_code, 422)
        for payload in ({}, {'base_revision': self.snapshot['revision'], 'config': {'password': 'secret'}}):
            result = self.post(payload=payload)
            self.assertEqual(result.status_code, 422)
            self.assertNotIn('secret', result.text)
        self.manager.apply_proxy.assert_not_called()

    def test_source_fingerprint_is_strict_and_forwarded_to_the_manager(self):
        fingerprint = 'a' * 64
        self.manager.apply_proxy.return_value = {'snapshot': self.snapshot,
            'phase': 'committed', 'applied': True}
        response = self.post(payload={'base_revision': self.snapshot['revision'],
                                      'source_fingerprint': fingerprint})
        self.assertEqual(response.status_code, 200, response.text)
        self.manager.apply_proxy.assert_called_once_with(self.snapshot['revision'], KEY,
                                                        expected_source_fingerprint=fingerprint)
        self.manager.check_proxy.return_value = {'snapshot': self.snapshot,
            'phase': 'checked', 'applied': False, 'source_fingerprint': fingerprint}
        response = self.post('/check', payload={'base_revision': self.snapshot['revision']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['source_fingerprint'], fingerprint)
        for value in ('', 'a' * 63, 'A' * 64, True, 1, {}, fingerprint + '\n'):
            before = self.manager.apply_proxy.call_count
            response = self.post(payload={'base_revision': self.snapshot['revision'],
                                          'source_fingerprint': value})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(self.manager.apply_proxy.call_count, before)

    def test_duplicate_physical_idempotency_keys_are_rejected(self):
        for second in (KEY, '2' * 64):
            headers = [*HEADERS.items(), ('Idempotency-Key', KEY), ('idempotency-key', second)]
            result = self.post(headers=headers)
            self.assertEqual(result.status_code, 422, result.text)
            self.assertEqual(result.json()['detail'], 'INVALID_IDEMPOTENCY_KEY')
        self.manager.apply_proxy.assert_not_called()

    def test_apply_uses_global_manager_gate_and_committed_snapshot(self):
        result_value = {'snapshot': self.snapshot, 'phase': 'committed', 'applied': True,
                        'transaction_id': KEY, 'revision': self.snapshot['revision']}
        self.manager.apply_proxy.return_value = result_value
        response = self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), result_value)
        self.assertEqual(response.headers['etag'], '"' + self.snapshot['revision'] + '"')
        self.manager.apply_proxy.assert_called_once_with(self.snapshot['revision'], KEY)
        self.manager.proxy_coordinator.apply.assert_not_called()

    def test_old_or_aborted_idempotent_result_is_not_current_success(self):
        for phase, applied in (('aborted', False), ('committed', False), ('prepared', True)):
            self.manager.apply_proxy.return_value = {'snapshot': self.snapshot, 'phase': phase,
                'applied': applied, 'already_completed': True}
            response = self.post()
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()['detail'], 'PROXY_TRANSACTION_NOT_ACTIVE')

    def test_check_is_explicit_configuration_only(self):
        self.manager.check_proxy.return_value = {
            'snapshot': self.snapshot, 'phase': 'checked', 'applied': False,
            'coverage': [], 'valid': True}
        response = self.post('/check', headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['applied'])
        self.manager.check_proxy.assert_called_once_with(self.snapshot['revision'])
        self.manager.apply_proxy.assert_not_called()
        self.manager.proxy_coordinator.apply.assert_not_called()

    def test_errors_are_static_and_corruption_is_unavailable(self):
        for error, status, code in (
            (CoreError('password leaked-material-path'), 409, 'PROXY_RUNTIME_UNAVAILABLE'),
            (ProxyConflict('password leaked-material-path'), 409, 'PROXY_REVISION_CONFLICT'),
            (ProxyCapacityError('password leaked-material-path'), 409, 'PROXY_CAPACITY_EXHAUSTED'),
            (ProxyStorageError('password leaked-material-path'), 503, 'PROXY_STORAGE_UNAVAILABLE'),
            (ProxyApplyError('RECOVERY_REQUIRED'), 503, 'PROXY_RECOVERY_REQUIRED'),
            (ProxyApplyError('APPLY_FAILED'), 409, 'PROXY_APPLY_FAILED'),
            (ResolutionError('RESOLUTION_TIMEOUT'), 422, 'PROXY_RESOLUTION_TIMEOUT')):
            with self.subTest(error=type(error).__name__):
                self.manager.apply_proxy.side_effect = error
                response = self.post()
                self.assertEqual(response.status_code, status, response.text)
                self.assertEqual(response.json()['detail'], code)
                self.assertNotIn('leaked-material-path', response.text)
                self.assertNotIn('password', response.text)
        self.manager.check_proxy.side_effect = ProxyStorageError('leaked-material-path')
        response = self.post('/check')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('leaked-material-path', response.text)

    def test_qualification_failure_retains_safe_coverage(self):
        coverage = [{'inbound_id': 3, 'core': 'xray', 'protocol': 'vless',
                     'transport': 'tcp', 'qualified': False, 'reason': 'UNQUALIFIED_INGRESS'}]
        self.manager.apply_proxy.side_effect = PolicyError('UNQUALIFIED_INGRESS', coverage)
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail'], {'code': 'PROXY_POLICY_REJECTED',
                         'reason': 'UNQUALIFIED_INGRESS', 'coverage': coverage})

    def test_authentication_and_same_origin_precede_all_runtime_work(self):
        self.auth.return_value = None
        for path in ('/apply', '/check'):
            self.assertEqual(self.post(path).status_code, 401)
        self.auth.return_value = {'id': 1}
        for path in ('/apply', '/check'):
            for headers in ({}, {'Origin': ORIGIN}, {**HEADERS, 'Origin': 'https://other.example.test'}):
                self.assertEqual(self.post(path, headers=headers).status_code, 403)
        self.manager.apply_proxy.assert_not_called()
        self.manager.check_proxy.assert_not_called()


if __name__ == '__main__':
    unittest.main()
