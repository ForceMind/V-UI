"""Managed VLESS/gRPC editing stays atomic and preserves TLS/UUID boundaries."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from app.api import inbounds as inbound_api
from app.models import database
import test_auth as auth_tests
from test_inbound_security_binding import BindingManager


class VlessGrpcManagedCertificateTests(unittest.TestCase):
    def setUp(self):
        auth_tests.AuthenticationTests.setUp(self)
        self.assertEqual(auth_tests.AuthenticationTests.login(self).status_code, 200)
        self.manager = BindingManager()
        patch('app.certificates.manager.get_manager', return_value=self.manager).start()
        self.apply = patch.object(inbound_api, 'apply_checked', return_value={'applied': True}).start()

    def create(self):
        result = self.client.post('/api/inbounds', headers=auth_tests.HEADERS, json={
            'core': 'sing-box', 'protocol': 'vless', 'remark': 'managed-vless-grpc', 'port': 10450,
            'profile': {'security': 'tls', 'transport': 'grpc', 'service_name': 'VUI.Before_1'},
            'certificate_id': 'a' * 32})
        self.assertEqual(result.status_code, 200, result.text)
        identity = result.json()['inbound']['id']
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, identity)
            stream = deepcopy(row.stream_settings)
            stream['tls']['alpn'] = ['h2']
            row.stream_settings = stream
            db.commit()
        return identity

    def snapshot(self, identity):
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, identity)
            return deepcopy(row.settings), deepcopy(row.stream_settings)

    def test_invalid_edit_is_atomic_then_valid_edit_preserves_uuid_alpn_binding(self):
        identity = self.create()
        settings, stream = self.snapshot(identity)
        state = self.client.get(f'/api/inbounds/{identity}/editor').json()
        self.assertEqual(state['certificate_id'], 'a' * 32)
        self.assertEqual(state['profile']['service_name'], 'VUI.Before_1')
        self.assertNotIn(settings['users'][0]['uuid'], json.dumps(state))
        invalid = [{'service_name': value} for value in (
            '', 'with space', ' leading', 'trailing ', '/service', 'svc/name', 'svc%2Fname',
            'service?x=1', 'service#fragment', '中文', 'a\r\nb', 'x' * 129, None, 7, False, [], {})]
        invalid += [{'flow': value} for value in (' ', 'xtls-rprx-vision', None, False, 0, [], {})]
        invalid += [{field: value} for field, value in (
            ('host', 'custom.example.test'), ('Host', 'custom.example.test'),
            ('authority', 'custom.example.test'), ('headers', {'Host': 'custom.example.test'}),
            ('user_agent', 'agent'), ('idle_timeout', '5s'), ('ping_timeout', '5s'),
            ('permit_without_stream', False), ('multi_mode', False), ('skip_cert_verify', 0))]
        self.apply.reset_mock()
        for change in invalid:
            with self.subTest(change=change):
                result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json={
                    'profile': {**state['profile'], **change}, 'certificate_id': 'a' * 32})
                self.assertEqual(result.status_code, 422, result.text)
                self.assertEqual(self.snapshot(identity), (settings, stream))
                self.assertEqual(self.manager.binding(f'inbound:{identity}'), 'a' * 32)
        self.apply.assert_not_called()
        result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json={
            'profile': {**state['profile'], 'service_name': 'VUI.After-2', 'security': 'TLS', 'transport': 'GRPC'},
            'certificate_id': 'a' * 32})
        self.assertEqual(result.status_code, 200, result.text)
        refreshed = self.client.get(f'/api/inbounds/{identity}/editor').json()
        self.assertEqual(refreshed['profile']['service_name'], 'VUI.After-2')
        self.assertEqual(refreshed['certificate_id'], 'a' * 32)
        self.assertNotIn(settings['users'][0]['uuid'], json.dumps(refreshed))
        saved_settings, saved_stream = self.snapshot(identity)
        self.assertEqual(saved_settings, settings)
        self.assertEqual(saved_stream['tls']['alpn'], ['h2'])
        self.assertEqual(saved_stream['transport'], {'type': 'grpc', 'service_name': 'VUI.After-2'})

    def test_unrepresented_imports_cannot_be_erased_by_transport_or_security_change(self):
        identity = self.create()
        original_settings, original_stream = self.snapshot(identity)
        changes = [('transport', {'authority': 'must-not-disappear'}),
                   ('transport', {'idle_timeout': '5s'}), ('tls', {'min_version': '1.3'}),
                   ('tls', {'reality': {'enabled': False, 'unknown': 'must-not-disappear'}}),
                   ('_vui', {'unknown': 'must-not-disappear'}),
                   ('_vui', {'security': 'none'}), ('_vui', {'security': 'reality'}), ('_vui', {'server_name': 'conflicting.example.test'}),
                   ('tls', {'enabled': 1}), ('tls', {'alpn': []}), ('tls', {'alpn': None}),
                   ('_vui', {'skip_cert_verify': 0})]
        for target, change in changes:
            for transport, security in (('grpc', 'tls'), ('direct', 'tls'), ('direct', 'none')):
                with self.subTest(target=target, change=change, transport=transport, security=security):
                    imported = deepcopy(original_stream)
                    imported[target].update(change)
                    with database.SessionLocal() as db:
                        db.get(database.Inbound, identity).stream_settings = imported
                        db.commit()
                    state = self.client.get(f'/api/inbounds/{identity}/editor')
                    self.assertEqual(state.status_code, 200, state.text)
                    profile = {**state.json()['profile'], 'transport': transport, 'security': security}
                    result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS,
                        json={'profile': profile})
                    self.assertEqual(result.status_code, 422, result.text)
                    self.assertEqual(self.snapshot(identity), (original_settings, imported))
                    self.assertEqual(self.manager.binding(f'inbound:{identity}'), 'a' * 32)

    def test_invalid_imported_service_and_flow_remain_literal_until_corrected(self):
        identity = self.create()
        settings, stream = self.snapshot(identity)
        for field in ('service_name', 'flow'):
            for value in (None, False, 0, [], {}, ' '):
                with self.subTest(field=field, value=value):
                    imported_settings, imported_stream = deepcopy(settings), deepcopy(stream)
                    if field == 'service_name': imported_stream['transport'][field] = value
                    else: imported_settings['users'][0][field] = value
                    with database.SessionLocal() as db:
                        row = db.get(database.Inbound, identity)
                        row.settings, row.stream_settings = imported_settings, imported_stream
                        db.commit()
                    state = self.client.get(f'/api/inbounds/{identity}/editor').json()
                    self.assertEqual(state['profile'][field], value)
                    self.assertIs(type(state['profile'][field]), type(value))
                    result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS,
                        json={'remark': 'unrelated edit', 'profile': state['profile']})
                    self.assertEqual(result.status_code, 422, result.text)
                    self.assertEqual(self.snapshot(identity), (imported_settings, imported_stream))
                    if field == 'flow':
                        omitted_flow = dict(state['profile'])
                        omitted_flow.pop('flow')
                        result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS,
                            json={'profile': omitted_flow})
                        self.assertEqual(result.status_code, 422, result.text)
                        self.assertEqual(self.snapshot(identity), (imported_settings, imported_stream))
                    corrected = {**state['profile'], field: 'Corrected.Service' if field == 'service_name' else ''}
                    result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS,
                        json={'profile': corrected})
                    self.assertEqual(result.status_code, 200, result.text)
                    self.assertEqual(self.snapshot(identity)[0]['users'][0]['uuid'], settings['users'][0]['uuid'])

    def test_malformed_imported_tls_and_metadata_fail_with_controlled_422(self):
        identity = self.create()
        settings, stream = self.snapshot(identity)
        original = self.client.get(f'/api/inbounds/{identity}/editor').json()['profile']
        for target, value in [(field, value) for field in ('tls', '_vui')
                for value in (None, False, 0, [], 'invalid')] + [
                    ('reality', value) for value in (None, False, 0, [], 'invalid')]:
            with self.subTest(target=target, value=value):
                imported = deepcopy(stream)
                if target == 'reality': imported['tls'][target] = value
                else: imported[target] = value
                with database.SessionLocal() as db:
                    db.get(database.Inbound, identity).stream_settings = imported
                    db.commit()
                result = self.client.get(f'/api/inbounds/{identity}/editor')
                self.assertEqual(result.status_code, 422, result.text)
                result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS,
                    json={'profile': original})
                self.assertEqual(result.status_code, 422, result.text)
                self.assertEqual(self.snapshot(identity), (settings, imported))

    def test_grpc_security_migrations_keep_existing_draft_boundary(self):
        identity = self.create()
        settings, _ = self.snapshot(identity)
        state = self.client.get(f'/api/inbounds/{identity}/editor').json()
        result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS,
            json={'profile': {**state['profile'], 'security': 'none'}, 'certificate_id': None})
        self.assertEqual(result.status_code, 200, result.text)
        state = self.client.get(f'/api/inbounds/{identity}/editor').json()
        self.assertEqual(state['profile']['security'], 'none')
        self.assertIsNone(state['certificate_id'])
        result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json={
            'profile': {**state['profile'], 'security': 'reality',
                'reality_target': 'target.example.test:443', 'reality_server_name': 'target.example.test',
                'reality_short_id': '0102030405060708'}})
        self.assertEqual(result.status_code, 200, result.text)
        state = self.client.get(f'/api/inbounds/{identity}/editor').json()
        self.assertEqual(state['profile']['security'], 'reality')
        private = self.snapshot(identity)[1]['tls']['reality']['private_key']
        self.assertNotIn(private, json.dumps(state))
        result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json={
            'profile': state['profile'], 'remark': 'reality-draft-edited'})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.snapshot(identity)[1]['tls']['reality']['private_key'], private)
        result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json={
            'profile': {**state['profile'], 'security': 'tls'}, 'certificate_id': 'a' * 32})
        self.assertEqual(result.status_code, 200, result.text)
        restored = self.client.get(f'/api/inbounds/{identity}/editor').json()
        self.assertEqual(restored['profile']['security'], 'tls')
        self.assertEqual(restored['certificate_id'], 'a' * 32)
        self.assertEqual(restored['profile']['service_name'], 'VUI.Before_1')
        self.assertEqual(self.snapshot(identity)[0], settings)


if __name__ == '__main__':
    unittest.main()
