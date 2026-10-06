"""Isolated SQLite regressions: REALITY exclusion never rewrites TLS state."""
from copy import deepcopy
import json
from unittest.mock import patch
import unittest

from fastapi import HTTPException
from app.api import singbox as legacy_api
from app.certificates.models import CertificateBinding
from app.models import database
from app.services.core_manager import core_manager
from app.services.reality_profile import FLOW, generate_keypair
import test_auth as auth_tests
import test_certificates as certificate_tests


class RealityCertificateBoundaryTests(unittest.TestCase):
    setUp = certificate_tests.CertificateTests.setUp
    issue = certificate_tests.CertificateTests.issue
    login = auth_tests.AuthenticationTests.login

    def state(self):
        private, public = generate_keypair()
        return ({'users': [{'uuid': '11111111-1111-4111-8111-111111111111', 'flow': FLOW}]},
            {'tls': {'enabled': True, 'server_name': 'reference.example.test', 'reality': {
                'enabled': True, 'handshake': {'server': '127.0.0.1', 'server_port': 19445},
                'private_key': private, 'short_id': ['0123456789abcdef']}},
             '_vui': {'security': 'reality', 'server_name': 'reference.example.test',
                'client_fingerprint': 'chrome', 'reality_public_key': public,
                'reality_short_id': '0123456789abcdef'}})

    def add_node(self, *, reality):
        settings, stream = self.state()
        if not reality:
            settings['users'][0].pop('flow')
            stream = {'tls': {'enabled': True, 'server_name': 'vpn.example.test'},
                      '_vui': {'security': 'tls', 'server_name': 'vpn.example.test'}}
        with database.SessionLocal() as db:
            row = database.Inbound(core='sing-box', protocol='vless', port=10453,
                enable=True, settings=settings, stream_settings=stream)
            db.add(row); db.commit()
            return row.id

    def snapshot(self, identity):
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, identity)
            binding = db.get(CertificateBinding, 'inbound:' + str(identity))
            return ((deepcopy(row.settings), deepcopy(row.stream_settings)), None if binding is None else {
                key: getattr(binding, key) for key in ('certificate_id', 'applied_certificate_id',
                    'applied_revision', 'error')})

    def bind(self, cert, identity):
        return self.client.post(f'/api/certificates/{cert}/bind-inbound',
            headers=auth_tests.HEADERS, json={'inbound_id': identity})

    def test_rejected_reality_bind_creates_no_binding_and_never_applies(self):
        cert = self.issue(domain='vpn.example.test')
        identity = self.add_node(reality=True)
        before = self.snapshot(identity)
        with patch.object(core_manager, 'apply_database') as apply:
            response = self.bind(cert, identity)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail'], 'TLS_NODE_REQUIRED')
        self.assertEqual(self.snapshot(identity), before)
        apply.assert_not_called()

    def test_raw_reality_with_managed_certificate_rejects_before_material_or_create(self):
        from app.api import inbounds as inbound_api
        settings, stream = self.state()
        streams = [stream, {'tls': {'reality': {}}}, {'_vui': {'security': 'reality'}},
                   {'_vui': {'reality_public_key': 'invalid'}}, {'realitySettings': {}}]
        for raw_stream in streams:
            for representation in (raw_stream, json.dumps(raw_stream)):
                with self.subTest(stream=representation), \
                     patch.object(self.manager, 'material', side_effect=AssertionError('material lookup must not run')) as material, \
                     patch.object(inbound_api, 'apply_checked') as apply:
                    response = self.client.post('/api/inbounds', headers=auth_tests.HEADERS, json={
                        'core': 'sing-box', 'protocol': 'vless', 'port': 10455,
                        'settings': settings, 'stream_settings': representation, 'certificate_id': 'a' * 32})
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertIn('REALITY', response.json()['detail'])
                    material.assert_not_called(); apply.assert_not_called()
                    with database.SessionLocal() as db:
                        self.assertEqual(db.query(database.Inbound).count(), 0)
                        self.assertEqual(db.query(CertificateBinding).count(), 0)

    def test_invalid_raw_json_with_certificate_id_never_reads_material_or_writes(self):
        from app.api import inbounds as inbound_api
        for raw in ('not-json', '[]', 'null', '"reality"'):
            with self.subTest(raw=raw), patch.object(self.manager, 'material') as material, \
                 patch.object(inbound_api, 'apply_checked') as apply:
                response = self.client.post('/api/inbounds', headers=auth_tests.HEADERS, json={
                    'core': 'sing-box', 'protocol': 'vless', 'port': 10455,
                    'stream_settings': raw, 'certificate_id': 'a' * 32})
                self.assertEqual(response.status_code, 422, response.text)
                material.assert_not_called(); apply.assert_not_called()
                with database.SessionLocal() as db:
                    self.assertEqual(db.query(database.Inbound).count(), 0)
                    self.assertEqual(db.query(CertificateBinding).count(), 0)

    def test_modern_binding_requires_explicit_tls_transition_before_material_lookup(self):
        from app.api import inbounds as inbound_api
        cert = self.issue(domain='vpn.example.test')
        for index, changes in enumerate(({}, {'profile': {}}, {'profile': {'transport': 'direct'}})):
            identity = self.add_node(reality=True)
            with database.SessionLocal() as db:
                db.get(database.Inbound, identity).port = 10500 + index
                if index == 2:
                    db.add(CertificateBinding(target='inbound:' + str(identity), certificate_id=cert,
                        applied_certificate_id=cert, applied_revision='b' * 64, error='CORE_STOPPED_PENDING_APPLY'))
                db.commit()
            before = self.snapshot(identity)
            with self.subTest(changes=changes), \
                 patch.object(self.manager, 'material', wraps=self.manager.material) as material, \
                 patch.object(inbound_api, 'apply_checked', return_value={'applied': True}), \
                 patch.object(core_manager.get('sing-box'), 'status', return_value={'running': True}), \
                 patch.object(core_manager, 'apply_database', return_value={'applied': True}):
                response = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS,
                    json={**changes, 'certificate_id': cert})
                after = self.snapshot(identity)
                self.assertEqual((response.status_code, material.call_count,
                    after[0] == before[0], after[1] == before[1]), (409, 0, True, True))
                self.assertIn('explicit TLS', response.json()['detail'])

    def test_modern_explicit_tls_transition_still_binds_and_preserves_uuid(self):
        from app.api import inbounds as inbound_api
        cert = self.issue(domain='vpn.example.test')
        identity = self.add_node(reality=True)
        before, _ = self.snapshot(identity)
        with patch.object(inbound_api, 'apply_checked', return_value={'applied': True}), \
             patch.object(core_manager.get('sing-box'), 'status', return_value={'running': True}), \
             patch.object(core_manager, 'apply_database', return_value={'applied': True}):
            response = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json={
                'profile': {'security': 'tls', 'transport': 'direct', 'flow': ''}, 'certificate_id': cert})
        self.assertEqual(response.status_code, 200, response.text)
        after, binding = self.snapshot(identity)
        self.assertEqual(after[0]['users'], [{'uuid': before[0]['users'][0]['uuid']}])
        self.assertNotIn('reality', after[1]['tls'])
        self.assertEqual(after[1]['_vui']['security'], 'tls')
        self.assertEqual(binding['certificate_id'], cert)
        self.assertEqual(binding['applied_certificate_id'], cert)
        self.assertTrue(binding['applied_revision'])
        self.assertIsNone(binding['error'])
        for secret in (before[0]['users'][0]['uuid'], before[1]['tls']['reality']['private_key']):
            self.assertNotIn(secret, response.text)

    def test_malformed_reality_indicators_also_refuse_binding_before_mutation(self):
        from app.services.inbound_service import to_dict
        cert = self.issue(domain='vpn.example.test')
        identity = self.add_node(reality=True)
        streams = [{'tls': {'enabled': True, 'reality': value}} for value in ({}, None, False, [], 'invalid')]
        streams += [{'tls': {'enabled': True}, '_vui': {'security': 'reality'}},
                    {'tls': {'enabled': True}, '_vui': {'reality_public_key': 'invalid'}}]
        for stream in streams:
            with database.SessionLocal() as db:
                row = db.get(database.Inbound, identity); row.stream_settings = stream; db.commit()
                self.assertFalse(to_dict(row)['managed_certificate_eligible'])
            before = self.snapshot(identity)
            with self.subTest(stream=stream), patch.object(core_manager, 'apply_database') as apply:
                result = self.bind(cert, identity)
                self.assertEqual(result.status_code, 409, result.text)
                self.assertEqual(result.json()['detail'], 'TLS_NODE_REQUIRED')
                self.assertEqual(self.snapshot(identity), before)
                apply.assert_not_called()

    def test_rejected_reality_rebind_keeps_existing_desired_applied_state(self):
        cert = self.issue(domain='vpn.example.test')
        other = self.issue(domain='other.example.test')
        identity = self.add_node(reality=True)
        with database.SessionLocal() as db:
            db.add(CertificateBinding(target='inbound:' + str(identity), certificate_id=cert,
                applied_certificate_id=cert, applied_revision='b' * 64, error='CORE_STOPPED_PENDING_APPLY'))
            db.commit()
        before = self.snapshot(identity)
        with patch.object(core_manager, 'apply_database') as apply:
            response = self.bind(other, identity)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.snapshot(identity), before)
        apply.assert_not_called()

    def test_valid_legacy_transition_clears_binding_after_save_even_if_apply_fails(self):
        cert = self.issue(domain='vpn.example.test')
        identity = self.add_node(reality=False)
        with patch.object(core_manager.get('sing-box'), 'status', return_value={'running': True}), \
             patch.object(core_manager, 'apply_database', return_value={'applied': True}):
            self.assertEqual(self.bind(cert, identity).status_code, 200)
        settings, stream = self.state()
        def failed_apply(core):
            self.assertEqual(self.snapshot(identity), ((settings, stream), None))
            raise HTTPException(409, {'saved': True, 'applied': False})
        with patch.object(legacy_api, 'apply_checked', side_effect=failed_apply):
            response = self.client.put(f'/api/singbox/inbounds/{identity}', headers=auth_tests.HEADERS,
                json={'port': 10454, 'protocol': 'vless', 'settings': settings, 'stream_settings': stream})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.snapshot(identity), ((settings, stream), None))
        with patch.object(core_manager, 'apply_database') as apply:
            self.assertEqual(self.manager.apply_existing(cert), [])
        apply.assert_not_called()

    def test_invalid_legacy_transition_keeps_node_and_binding(self):
        cert = self.issue(domain='vpn.example.test')
        identity = self.add_node(reality=False)
        with patch.object(core_manager.get('sing-box'), 'status', return_value={'running': True}), \
             patch.object(core_manager, 'apply_database', return_value={'applied': True}):
            self.assertEqual(self.bind(cert, identity).status_code, 200)
        before = self.snapshot(identity)
        for invalid in ('alpn', 'ws', 'grpc', 'bad-public', 'disabled', 'multiple-shorts'):
            settings, stream = self.state()
            if invalid == 'alpn': stream['tls']['alpn'] = ['h2']
            elif invalid in ('ws', 'grpc'): stream['transport'] = {'type': invalid}
            elif invalid == 'bad-public': stream['_vui']['reality_public_key'] = 'invalid'
            elif invalid == 'disabled': stream['tls']['reality']['enabled'] = False
            else: stream['tls']['reality']['short_id'] *= 2
            with self.subTest(invalid=invalid), patch.object(legacy_api, 'apply_checked') as apply:
                response = self.client.put(f'/api/singbox/inbounds/{identity}', headers=auth_tests.HEADERS,
                    json={'port': 10454, 'protocol': 'vless', 'settings': settings, 'stream_settings': stream})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.snapshot(identity), before)
                apply.assert_not_called()


if __name__ == '__main__': unittest.main()
