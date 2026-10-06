"""VMess uses the existing managed TLS API without weakening target guards."""
import json
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from app.api import inbounds as inbound_api
from app.models import database
import test_auth as auth_tests
from test_inbound_security_binding import BindingManager


class VMessManagedCertificateTests(unittest.TestCase):
    def setUp(self):
        auth_tests.AuthenticationTests.setUp(self)
        self.assertEqual(auth_tests.AuthenticationTests.login(self).status_code, 200)
        self.manager = BindingManager()
        patch('app.certificates.manager.get_manager', return_value=self.manager).start()
        patch.object(inbound_api, 'apply_checked', return_value={'applied': True}).start()

    def test_create_edit_and_refresh_keep_binding_and_hide_uuid(self):
        result = self.client.post('/api/inbounds', headers=auth_tests.HEADERS, json={
            'core': 'sing-box', 'protocol': 'vmess', 'remark': 'managed-vmess', 'port': 10446,
            'profile': {'security': 'tls', 'transport': 'direct'}, 'certificate_id': 'a' * 32})
        self.assertEqual(result.status_code, 200, result.text)
        identity = result.json()['inbound']['id']
        with database.SessionLocal() as db:
            uuid = db.get(database.Inbound, identity).settings['users'][0]['uuid']
        state = self.client.get(f'/api/inbounds/{identity}/editor').json()
        self.assertEqual(state['certificate_id'], 'a' * 32)
        self.assertEqual(state['profile']['security'], 'tls')
        self.assertNotIn(uuid, json.dumps(state))
        result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json={
            'remark': 'managed-vmess-edited', 'profile': state['profile'], 'certificate_id': state['certificate_id']})
        self.assertEqual(result.status_code, 200, result.text)
        refreshed = self.client.get(f'/api/inbounds/{identity}/editor').json()
        self.assertEqual(refreshed['certificate_id'], 'a' * 32)
        self.assertNotIn(uuid, json.dumps(refreshed))
        with database.SessionLocal() as db:
            self.assertEqual(db.get(database.Inbound, identity).settings['users'][0]['uuid'], uuid)

    def test_incompatible_targets_and_security_reject_before_material_lookup(self):
        for core, protocol, security in (('xray', 'vmess', 'tls'),
                ('sing-box', 'shadowsocks', 'tls'), ('sing-box', 'unsupported', 'tls'),
                ('sing-box', 'vmess', 'none'), ('sing-box', 'vmess', 'reality')):
            with self.subTest(core=core, protocol=protocol, security=security):
                with self.assertRaises(HTTPException) as rejected:
                    inbound_api._managed_certificate({'security': security}, 'a' * 32, core, protocol)
                self.assertEqual(rejected.exception.status_code, 409)
        self.assertEqual(self.manager.material_calls, 0)
