"""M1 egress API: real parser/store, fake admin, no networking side effects."""
import json
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import proxy
from app.services.proxy_store import ProxyStore
import main

ORIGIN = 'https://panel.example.test'
HEADERS = {'Origin': ORIGIN, 'X-VUI-Request': '1'}
UUID = '11111111-1111-1111-1111-111111111111'
URI = f'vless://{UUID}@upstream.example.test:443?type=tcp&security=tls&sni=upstream.example.test&encryption=none#Fake'


class ProxyApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vui-proxy-api-')
        self.addCleanup(self.temp.cleanup)
        self.store = ProxyStore(self.temp.name)
        self.addCleanup(patch.stopall)
        patch('app.middleware.auth.configured_origin', return_value=ORIGIN).start()
        self.auth = patch('app.middleware.auth.authenticate', return_value={'id': 1}).start()
        patch.object(proxy, 'get_store', return_value=self.store).start()
        self.apply = patch.object(main.core_manager, 'apply_database').start()
        self.client = TestClient(main.app, base_url=ORIGIN)

    def snapshot(self):
        result = self.client.get('/api/proxy/settings')
        self.assertEqual(result.status_code, 200, result.text)
        return result

    def add_profile(self, **extra):
        revision = self.snapshot().headers['etag']
        payload = {'format': 'uri', 'content': URI, 'candidate_index': 0, **extra}
        return self.client.post('/api/proxy/profiles', json=payload,
                                headers={**HEADERS, 'If-Match': revision})

    def test_defaults_are_off_and_runtime_not_touched(self):
        result = self.snapshot()
        self.assertFalse(result.json()['settings']['local_proxy_enabled'])
        self.assertFalse(result.json()['settings']['relay_enabled'])
        self.assertIsNone(result.json()['active'])
        self.apply.assert_not_called()

    def test_preview_is_secret_free_and_does_not_save(self):
        before = self.snapshot().json()
        result = self.client.post('/api/proxy/import/preview',
                                  json={'format': 'uri', 'content': URI}, headers=HEADERS)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['candidates'][0]['supported'])
        self.assertNotIn(UUID, result.text)
        self.assertNotIn(URI, result.text)
        self.assertEqual(self.snapshot().json(), before)
        self.apply.assert_not_called()

    def test_save_profile_requires_explicit_selection_and_returns_no_secret(self):
        revision = self.snapshot().headers['etag']
        result = self.client.post('/api/proxy/profiles', json={'format': 'uri', 'content': URI},
                                  headers={**HEADERS, 'If-Match': revision})
        self.assertEqual(result.status_code, 422)
        self.assertNotIn(UUID, result.text)
        result = self.add_profile()
        self.assertEqual(result.status_code, 201, result.text)
        self.assertNotIn(UUID, result.text)
        self.assertNotIn('secret_ref', result.text)
        self.assertEqual(len(result.json()['snapshot']['upstreams']), 1)
        self.assertTrue(result.headers['etag'])
        self.apply.assert_not_called()

    def test_import_acknowledges_only_recognized_ignored_sections(self):
        outbound = {'protocol': 'vless', 'settings': {'vnext': [{
            'address': 'upstream.example.test', 'port': 443,
            'users': [{'id': UUID, 'encryption': 'none'}]}]},
            'streamSettings': {'network': 'tcp', 'security': 'tls',
                               'tlsSettings': {'serverName': 'upstream.example.test'}}}
        document = json.dumps({'outbounds': [outbound], 'routing': {'rules': []}})
        revision = self.snapshot().headers['etag']
        payload = {'format': 'v2ray-json', 'content': document, 'candidate_index': 0}
        result = self.client.post('/api/proxy/profiles', json=payload,
                                  headers={**HEADERS, 'If-Match': revision})
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.json()['detail'], 'IGNORED_SECTIONS_ACKNOWLEDGEMENT_REQUIRED')
        payload['acknowledged_ignored_sections'] = ['routing']
        result = self.client.post('/api/proxy/profiles', json=payload,
                                  headers={**HEADERS, 'If-Match': revision})
        self.assertEqual(result.status_code, 201, result.text)
        self.assertNotIn(UUID, result.text)

    def test_profile_writes_require_strong_exact_revision(self):
        for tag, expected in ((None, 428), ('*', 422), ('W/"' + '0' * 64 + '"', 422),
                              ('0' * 64, 409), ('"' + '0' * 64 + '","' + '1' * 64 + '"', 422)):
            headers = dict(HEADERS)
            if tag is not None:
                headers['If-Match'] = tag
            with self.subTest(tag=tag):
                result = self.client.post('/api/proxy/profiles',
                    json={'format': 'uri', 'content': URI, 'candidate_index': 0}, headers=headers)
                self.assertEqual(result.status_code, expected, result.text)
        self.assertEqual(self.snapshot().json()['upstreams'], [])

    def test_four_valid_draft_combinations_do_not_apply(self):
        result = self.add_profile()
        self.assertEqual(result.status_code, 201, result.text)
        upstream_id = result.json()['upstream_id']
        for local, relay in ((False, False), (False, True), (True, False), (True, True)):
            old = self.snapshot()
            settings = old.json()['settings']
            settings.pop('local_password_set', None)
            settings.update(local_proxy_enabled=local, relay_enabled=relay,
                            selected_upstream_id=upstream_id, local_username='fake-user',
                            local_password='fake-local-password')
            with self.subTest(local=local, relay=relay):
                changed = self.client.put('/api/proxy/settings', json=settings,
                    headers={**HEADERS, 'If-Match': old.headers['etag']})
                self.assertEqual(changed.status_code, 200, changed.text)
                self.assertEqual(changed.json()['settings']['local_proxy_enabled'], local)
                self.assertEqual(changed.json()['settings']['relay_enabled'], relay)
                self.assertIsNone(changed.json()['active'])
                self.assertNotIn('fake-local-password', changed.text)
                self.assertNotIn('password_ref', changed.text)
        self.apply.assert_not_called()

    def test_auth_and_csrf_are_required_before_import(self):
        self.auth.return_value = None
        result = self.client.post('/api/proxy/import/preview',
            json={'format': 'uri', 'content': URI}, headers=HEADERS)
        self.assertEqual(result.status_code, 401)
        self.auth.return_value = {'id': 1}
        for headers in ({}, {'Origin': ORIGIN}, {**HEADERS, 'Origin': 'https://other.example.test'}):
            result = self.client.post('/api/proxy/import/preview',
                json={'format': 'uri', 'content': URI}, headers=headers)
            self.assertEqual(result.status_code, 403)

    def test_validation_errors_never_echo_input_credentials(self):
        for payload in ({'format': 'wrong', 'content': URI},
                        {'format': 'uri', 'content': URI, 'unknown': UUID},
                        {'format': 'uri', 'content': [URI]},
                        {'format': 'uri', 'content': URI.replace('security=tls', 'security=none')}):
            result = self.client.post('/api/proxy/import/preview', json=payload, headers=HEADERS)
            # An unsupported candidate is a preview result, not a successful conversion.
            self.assertIn(result.status_code, (200, 422))
            if result.status_code == 200:
                self.assertFalse(result.json()['candidates'][0]['supported'])
            self.assertNotIn(UUID, result.text)
            self.assertNotIn(URI, result.text)

    def test_chunked_actual_byte_cap_precedes_json_parsing(self):
        def chunks():
            yield b'{"format":"uri","content":"'
            for _ in range(33):
                yield b'x' * 65536
            yield b'"}'
        result = self.client.post('/api/proxy/import/preview', content=chunks(),
                                  headers={**HEADERS, 'Content-Type': 'application/json'})
        self.assertEqual(result.status_code, 413, result.text)
        self.apply.assert_not_called()

    def test_stage_capability_is_honest_and_apply_not_exposed(self):
        capabilities = self.client.get('/api/proxy/capabilities').json()
        self.assertFalse(capabilities['apply_available'])
        self.assertEqual(capabilities['implementation_stage'], 'draft-only')
        self.assertEqual(self.client.post('/api/proxy/apply', json={}, headers=HEADERS).status_code, 404)
        self.apply.assert_not_called()

    def test_version_fields_reject_boolean_float_string_and_unknown_versions(self):
        original = self.snapshot()
        settings = original.json()['settings']
        settings.pop('local_password_set')
        for field in ('schema_version', 'dns_policy_version', 'deny_policy_version'):
            for value in (True, False, 1.0, '1', None, 0, 2):
                with self.subTest(field=field, value=value):
                    result = self.client.put('/api/proxy/settings',
                        json={**settings, field: value},
                        headers={**HEADERS, 'If-Match': original.headers['etag']})
                    self.assertEqual(result.status_code, 422, result.text)
        self.assertEqual(self.snapshot().json(), original.json())
        self.apply.assert_not_called()


if __name__ == '__main__':
    unittest.main()
