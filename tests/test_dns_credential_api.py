"""Credential endpoints must never expand a maintenance grant or echo tokens."""
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_maintenance_auth as maintenance_tests
from app.api import certificates as api
from app.certificates.dns_credentials import DNSCredentials
from app.certificates.models import Certificate
from app.models import database


class DNSCredentialAPITests(unittest.TestCase):
    issue = maintenance_tests.MaintenanceAuthorizationTests.issue
    bearer = maintenance_tests.MaintenanceAuthorizationTests.bearer

    def setUp(self):
        maintenance_tests.MaintenanceAuthorizationTests.setUp(self)
        self.calls = []
        self.fake_token = 'only_fake_DNS_test_' + 'x' * 32
        calls = self.calls
        class ReadOnlyCloudflare:
            def __init__(self, token):
                self.token = token
            def discover_zone(self, name):
                calls.append(('GET', name))
                return {'zone_name': name, 'zone_id': '1' * 32}
        self.service = DNSCredentials(Path(self.temp.name).resolve() / 'certificates', client_factory=ReadOnlyCloudflare)
        patch.object(api, '_dns_credentials', return_value=self.service).start()
        self.headers = maintenance_tests.BROWSER

    def create(self):
        response = self.client.post('/api/certificates/dns-credentials', headers=self.headers,
            json={'label': '测试域名', 'zone_name': 'example.test', 'api_token': self.fake_token})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertNotIn(self.fake_token, response.text)
        return response.json()

    def test_admin_saves_once_returns_only_metadata_and_performs_no_dns_write(self):
        profile = self.create()
        listed = self.client.get('/api/certificates/dns-credentials')
        self.assertEqual(listed.status_code, 200, listed.text)
        value = listed.json()[0]
        self.assertEqual(value['id'], profile['id'])
        self.assertEqual(value['references'], 0)
        self.assertNotIn(self.fake_token, listed.text)
        self.assertNotIn('api_token', listed.text)
        self.assertNotIn(str(self.service.store.root), listed.text)
        self.assertEqual(self.calls, [('GET', 'example.test')])

    def test_bearer_plus_admin_cookie_cannot_manage_or_list_credentials(self):
        profile = self.create()
        _, headers = self.bearer(['certificates', 'destructive', 'diagnose', 'operate', 'nodes'])
        before = list(self.calls)
        operations = [('GET', '', None), ('POST', '', {'label': '另一个', 'zone_name': 'example.test', 'api_token': self.fake_token}),
                      ('PUT', '/' + profile['id'], {'expected_revision': profile['revision'], 'api_token': self.fake_token}),
                      ('POST', '/' + profile['id'] + '/disable', {'expected_revision': profile['revision']}),
                      ('POST', '/' + profile['id'] + '/enable', {'expected_revision': profile['revision'], 'confirmed': True})]
        for method, suffix, payload in operations:
            kwargs = {'headers': headers}
            if payload is not None:
                kwargs['json'] = payload
            response = self.client.request(method, '/api/certificates/dns-credentials' + suffix, **kwargs)
            self.assertEqual(response.status_code, 403, response.text)
            self.assertNotIn(self.fake_token, response.text)
        for method, path in (('GET', '/api/certificates/dns-challenges'),
                             ('POST', '/api/certificates/dns-challenges/' + 'a' * 32 + '/cleanup'),
                             ('POST', '/api/certificates/dns-challenges/' + 'a' * 32 + '/confirm-absence')):
            response = self.client.request(method, path, headers=headers,
                **({'json': {'expected_revision': 'b' * 32, 'expected_profile_revision': profile['revision'], 'confirmed': True}} if method == 'POST' else {}))
            self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.calls, before)
        self.assertTrue(self.service.list()[0]['enabled'])

    def test_old_certificate_scope_can_still_renew_http_but_does_not_gain_dns_writes(self):
        profile = self.create()
        with database.SessionLocal() as db:
            for identity, method in (('d' * 32, 'dns-01'), ('e' * 32, 'http-01')):
                db.add(Certificate(id=identity, domain=('dns' if method == 'dns-01' else 'http') + '.example.test',
                    email='admin@example.test', environment='production', method=method, wildcard=False,
                    credential_id=profile['id'] if method == 'dns-01' else None, created_at=1, auto_renew=False))
            db.commit()
        manager = SimpleNamespace(renew=Mock(return_value={'id': 'synthetic-http-job'}), set_auto_renew=Mock())
        _, headers = self.bearer(['certificates'])
        with patch.object(api, 'get_manager', return_value=manager):
            denied = self.client.post('/api/certificates/' + 'd' * 32 + '/renew', headers=headers)
            self.assertEqual(denied.status_code, 403, denied.text)
            denied = self.client.put('/api/certificates/' + 'd' * 32 + '/auto-renew', headers=headers, json={'enabled': True})
            self.assertEqual(denied.status_code, 403, denied.text)
            manager.renew.assert_not_called(); manager.set_auto_renew.assert_not_called()
            allowed = self.client.post('/api/certificates/' + 'e' * 32 + '/renew', headers=headers)
            self.assertEqual(allowed.status_code, 202, allowed.text)
            manager.renew.assert_called_once_with('e' * 32)

    def test_invalid_schema_duplicate_keys_and_oversize_never_echo_secret(self):
        base = {'label': '测试', 'zone_name': 'example.test', 'api_token': self.fake_token}
        for payload in ({**base, 'api_token': {'value': self.fake_token}}, {**base, 'unknown': self.fake_token}):
            response = self.client.post('/api/certificates/dns-credentials', headers=self.headers, json=payload)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn(self.fake_token, response.text)
        duplicate = json.dumps(base)[:-1] + ',"api_token":"' + self.fake_token + '"}'
        for raw, expected in ((duplicate, 422), ('{"api_token":"' + self.fake_token + 'x' * 9000 + '"}', 413)):
            response = self.client.post('/api/certificates/dns-credentials',
                headers={**self.headers, 'Content-Type': 'application/json'}, content=raw)
            self.assertEqual(response.status_code, expected, response.text)
            self.assertNotIn(self.fake_token, response.text)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.service.store.root.exists())

    def test_rotation_preserves_disabled_state_and_enable_is_explicit_read_only_check(self):
        profile = self.create()
        path = '/api/certificates/dns-credentials/' + profile['id']
        disabled = self.client.post(path + '/disable', headers=self.headers,
            json={'expected_revision': profile['revision']})
        self.assertEqual(disabled.status_code, 200, disabled.text)
        replacement = self.client.put(path, headers=self.headers,
            json={'expected_revision': disabled.json()['revision'], 'api_token': 'new_fake_token_' + 'y' * 32})
        self.assertEqual(replacement.status_code, 200, replacement.text)
        self.assertFalse(replacement.json()['enabled'])
        stale = self.client.put(path, headers=self.headers,
            json={'expected_revision': profile['revision'], 'api_token': self.fake_token})
        self.assertEqual(stale.status_code, 409, stale.text)
        missing = self.client.post(path + '/enable', headers=self.headers,
            json={'expected_revision': replacement.json()['revision']})
        self.assertEqual(missing.status_code, 422, missing.text)
        enabled = self.client.post(path + '/enable', headers=self.headers,
            json={'expected_revision': replacement.json()['revision'], 'confirmed': True})
        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.assertTrue(enabled.json()['enabled'])
        self.assertTrue(all(method == 'GET' for method, _ in self.calls))

    def test_cross_origin_never_reaches_credential_service(self):
        response = self.client.post('/api/certificates/dns-credentials',
            headers={**self.headers, 'Origin': 'https://unrelated.example.test'},
            json={'label': '测试', 'zone_name': 'example.test', 'api_token': self.fake_token})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
