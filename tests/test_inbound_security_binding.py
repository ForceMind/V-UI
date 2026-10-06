"""Managed TLS transitions must keep desired security and renewal consistent."""
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from app.models import database
from app.api import inbounds as inbound_api
import test_auth as auth_tests


class BindingManager:
    def __init__(self):
        self.bound = 'a' * 32
        self.unbound = []
        self.material_calls = 0

    def binding(self, target):
        return self.bound

    def material(self, identity):
        self.material_calls += 1
        return (Path('/managed/cert.pem'), Path('/managed/key.pem')), 'vpn.example.test', 'b' * 64

    def unbind(self, target):
        self.unbound.append(target)
        self.bound = None

    def bind(self, identity, target):
        self.bound = identity


class SecurityBindingTests(unittest.TestCase):
    def setUp(self):
        auth_tests.AuthenticationTests.setUp(self)
        self.assertEqual(auth_tests.AuthenticationTests.login(self).status_code, 200)
        self.manager = BindingManager()
        patch('app.certificates.manager.get_manager', return_value=self.manager).start()
        self.apply = patch.object(inbound_api, 'apply_checked', return_value={'applied': True}).start()
        self.uuid = '11111111-1111-1111-1111-111111111111'
        with database.SessionLocal() as db:
            item = database.Inbound(core='sing-box', protocol='vless', remark='managed', port=10445,
                enable=True, settings={'users': [{'uuid': self.uuid}]}, stream_settings={
                    'tls': {'enabled': True, 'server_name': 'vpn.example.test',
                            'certificate_path': '/managed/cert.pem', 'key_path': '/managed/key.pem'},
                    '_vui': {'security': 'tls', 'server_name': 'vpn.example.test'}})
            db.add(item); db.commit(); db.refresh(item)
            self.identity = item.id

    def update(self, body):
        return self.client.put(f'/api/inbounds/{self.identity}', headers=auth_tests.HEADERS, json=body)

    def profile(self, security):
        return {'security': security, 'transport': 'direct',
                'flow': 'xtls-rprx-vision' if security == 'reality' else '', 'client_fingerprint': 'chrome',
                'reality_target': 'target.example.test:443',
                'reality_server_name': 'target.example.test',
                'reality_short_id': '0102030405060708'}

    def assert_transition(self, security):
        state = self.client.get(f'/api/inbounds/{self.identity}/editor').json()
        self.assertEqual(state['profile']['security'], security)
        self.assertIsNone(state['certificate_id'])
        self.assertNotIn(self.uuid, str(state))
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, self.identity)
            self.assertEqual(row.settings['users'][0]['uuid'], self.uuid)
            self.assertNotIn('certificate_path', row.stream_settings.get('tls', {}))
            self.assertNotIn('key_path', row.stream_settings.get('tls', {}))
        self.assertEqual(self.manager.unbound, [f'inbound:{self.identity}'])
        self.assertEqual(self.manager.material_calls, 0)

    def test_explicit_none_unbind_needs_no_certificate_material(self):
        result = self.update({'profile': self.profile('none'), 'certificate_id': None})
        self.assertEqual(result.status_code, 200, result.text)
        self.assert_transition('none')

    def test_explicit_reality_unbind_needs_no_certificate_material(self):
        result = self.update({'profile': self.profile('reality'), 'certificate_id': None})
        self.assertEqual(result.status_code, 200, result.text)
        self.assert_transition('reality')

    def test_security_transition_without_certificate_field_clears_binding(self):
        result = self.update({'profile': self.profile('none')})
        self.assertEqual(result.status_code, 200, result.text)
        self.assert_transition('none')

    def test_stale_managed_id_cannot_override_explicit_non_tls(self):
        for security in ('none', 'reality'):
            with self.subTest(security=security):
                result = self.update({'profile': self.profile(security), 'certificate_id': 'a' * 32})
                self.assertEqual(result.status_code, 409, result.text)
                self.assertEqual(self.manager.bound, 'a' * 32)
        self.apply.assert_not_called()

    def test_invalid_reality_does_not_unbind_or_save(self):
        result = self.update({'profile': {'security': 'reality'}, 'certificate_id': None})
        self.assertEqual(result.status_code, 422, result.text)
        self.assertEqual(self.manager.unbound, [])
        with database.SessionLocal() as db:
            self.assertEqual(db.get(database.Inbound, self.identity).stream_settings['_vui']['security'], 'tls')
        self.apply.assert_not_called()

    def test_saved_transition_unbinds_even_when_core_apply_fails(self):
        self.apply.side_effect = HTTPException(409, {'saved': True, 'applied': False, 'message': 'synthetic failure'})
        result = self.update({'profile': self.profile('none'), 'certificate_id': None})
        self.assertEqual(result.status_code, 409, result.text)
        self.assertTrue(result.json()['detail']['saved'])
        self.assert_transition('none')

    def test_empty_profile_does_not_drop_managed_tls_binding(self):
        result = self.update({'profile': {}})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.manager.bound, 'a' * 32)
        self.assertEqual(self.manager.unbound, [])
        with database.SessionLocal() as db:
            self.assertEqual(db.get(database.Inbound, self.identity).stream_settings['tls']['certificate_path'], '/managed/cert.pem')

    def test_empty_profile_cannot_explicitly_unbind_managed_tls(self):
        result = self.update({'profile': {}, 'certificate_id': None})
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(self.manager.unbound, [])
        self.apply.assert_not_called()

    def test_nonempty_profile_with_default_security_clears_binding(self):
        result = self.update({'profile': {'transport': 'direct'}})
        self.assertEqual(result.status_code, 200, result.text)
        self.assert_transition('none')

    def test_empty_security_uses_compiler_default_and_clears_binding(self):
        result = self.update({'profile': {'security': ''}})
        self.assertEqual(result.status_code, 200, result.text)
        self.assert_transition('none')

    def test_tls_unbind_still_requires_both_new_paths(self):
        for profile in (None, {'security': 'tls'},
                        {'security': 'tls', 'certificate_path': '/managed/cert.pem', 'key_path': '/new/key.pem'},
                        {'security': 'tls', 'certificate_path': '/new/cert.pem', 'key_path': '/managed/key.pem'}):
            with self.subTest(profile=profile):
                result = self.update({'profile': profile, 'certificate_id': None})
                self.assertEqual(result.status_code, 409, result.text)
                self.assertEqual(self.manager.unbound, [])
        self.apply.assert_not_called()


if __name__ == '__main__':
    unittest.main()
