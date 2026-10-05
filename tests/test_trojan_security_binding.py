"""Trojan's implicit TLS must not be mistaken for a managed TLS exit."""
import unittest
from app.models import database
import test_inbound_security_binding as binding_tests


class TrojanDefaultSecurityBindingTests(unittest.TestCase):
    def setUp(self):
        binding_tests.SecurityBindingTests.setUp(self)
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, self.identity)
            row.protocol = 'trojan'
            row.settings = {'users': [{'password': 'synthetic-trojan-password'}]}
            db.commit()

    update = binding_tests.SecurityBindingTests.update

    def profile(self, fields=None):
        return {'transport': 'direct', 'server_name': 'vpn.example.test',
                'certificate_path': '/managed/cert.pem', 'key_path': '/managed/key.pem',
                **(fields or {})}

    def test_implicit_or_explicit_tls_cannot_unbind_with_old_material(self):
        for fields in ({}, {'security': ''}, {'security': None}, {'security': 'tls'}, {'security': 'TLS'}):
            with self.subTest(fields=fields):
                response = self.update({'profile': self.profile(fields), 'certificate_id': None})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(self.manager.bound, 'a' * 32)
                self.assertEqual(self.manager.unbound, [])
                with database.SessionLocal() as db:
                    row = db.get(database.Inbound, self.identity)
                    self.assertEqual(row.stream_settings['tls']['certificate_path'], '/managed/cert.pem')
                    self.assertEqual(row.stream_settings['tls']['key_path'], '/managed/key.pem')
        self.apply.assert_not_called()

    def test_default_tls_edits_keep_renewal_when_certificate_field_is_omitted(self):
        for fields in ({}, {'security': ''}, {'security': None}):
            with self.subTest(fields=fields):
                response = self.update({'profile': self.profile(fields)})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.manager.bound, 'a' * 32)
                self.assertEqual(self.manager.unbound, [])
                with database.SessionLocal() as db:
                    row = db.get(database.Inbound, self.identity)
                    self.assertTrue(row.stream_settings['tls']['enabled'])
                    self.assertEqual(row.settings['users'][0]['password'], 'synthetic-trojan-password')

    def test_default_tls_explicit_unbind_requires_both_manual_replacements(self):
        for fields in ({'certificate_path': '/manual/cert.pem'}, {'key_path': '/manual/key.pem'}):
            with self.subTest(fields=fields):
                response = self.update({'profile': self.profile(fields), 'certificate_id': None})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(self.manager.unbound, [])
        response = self.update({'profile': self.profile({'certificate_path': '/manual/cert.pem',
            'key_path': '/manual/key.pem'}), 'certificate_id': None})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self.manager.bound)
        self.assertEqual(self.manager.unbound, [f'inbound:{self.identity}'])

    def test_empty_profile_is_noop_and_invalid_none_does_not_unbind(self):
        response = self.update({'profile': {}})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.update({'profile': {}, 'certificate_id': None})
        self.assertEqual(response.status_code, 409, response.text)
        response = self.update({'profile': {'security': 'none'}, 'certificate_id': None})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.manager.bound, 'a' * 32)
        self.assertEqual(self.manager.unbound, [])
