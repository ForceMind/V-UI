"""Managed VLESS/WS editing rejects malformed client metadata without mutation."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from app.api import inbounds as inbound_api
from app.models import database
import test_auth as auth_tests
from test_inbound_security_binding import BindingManager


class VlessWsManagedCertificateTests(unittest.TestCase):
    def setUp(self):
        auth_tests.AuthenticationTests.setUp(self)
        self.assertEqual(auth_tests.AuthenticationTests.login(self).status_code, 200)
        self.manager = BindingManager()
        patch('app.certificates.manager.get_manager', return_value=self.manager).start()
        patch.object(inbound_api, 'apply_checked', return_value={'applied': True}).start()

    def test_invalid_ws_edit_is_atomic_then_valid_edit_preserves_secret_and_binding(self):
        result = self.client.post('/api/inbounds', headers=auth_tests.HEADERS, json={
            'core': 'sing-box', 'protocol': 'vless', 'remark': 'managed-vless-ws', 'port': 10449,
            'profile': {'security': 'tls', 'transport': 'ws', 'path': '/before', 'host': 'cdn.example.test'},
            'certificate_id': 'a' * 32})
        self.assertEqual(result.status_code, 200, result.text)
        identity = result.json()['inbound']['id']
        with database.SessionLocal() as db:
            original = deepcopy(db.get(database.Inbound, identity).stream_settings)
            settings = deepcopy(db.get(database.Inbound, identity).settings)
        state = self.client.get(f'/api/inbounds/{identity}/editor').json()
        self.assertEqual(state['certificate_id'], 'a' * 32)
        self.assertEqual(state['profile']['path'], '/before')
        self.assertEqual(state['profile']['host'], 'cdn.example.test')
        self.assertNotIn(settings['users'][0]['uuid'], json.dumps(state))
        for change in ({'path': '/bad?ed=2048'}, {'host': 'bad.example.test:443'}):
            result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json={
                'profile': {**state['profile'], **change}, 'certificate_id': 'a' * 32})
            self.assertEqual(result.status_code, 422, result.text)
            with database.SessionLocal() as db:
                row = db.get(database.Inbound, identity)
                self.assertEqual(row.stream_settings, original)
                self.assertEqual(row.settings, settings)
            self.assertEqual(self.manager.binding(f'inbound:{identity}'), 'a' * 32)
        result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json={
            'profile': {**state['profile'], 'path': '/after', 'host': 'other.example.test'},
            'certificate_id': 'a' * 32})
        self.assertEqual(result.status_code, 200, result.text)
        refreshed = self.client.get(f'/api/inbounds/{identity}/editor').json()
        self.assertEqual(refreshed['profile']['path'], '/after')
        self.assertEqual(refreshed['profile']['host'], 'other.example.test')
        self.assertEqual(refreshed['certificate_id'], 'a' * 32)
        self.assertNotIn(settings['users'][0]['uuid'], json.dumps(refreshed))
        with database.SessionLocal() as db:
            self.assertEqual(db.get(database.Inbound, identity).settings, settings)


if __name__ == '__main__':
    unittest.main()
