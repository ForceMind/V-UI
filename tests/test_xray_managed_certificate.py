"""Bounded Xray TLS bindings, using synthetic CA material and mocked runtimes."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from app.api import inbounds as inbound_api
from app.certificates.material import CertificateError
from app.certificates.models import CertificateBinding
from app.models import database
from app.services.core_manager import core_manager, XrayAdapter
from app.services.inbound_service import to_dict
from app.services.managed_tls import target_error
import test_auth as auth_tests
import test_certificates as certificate_fixtures


class XrayManagedCertificateTests(unittest.TestCase):
    setUp = certificate_fixtures.CertificateTests.setUp
    login = auth_tests.AuthenticationTests.login
    issue = certificate_fixtures.CertificateTests.issue
    due = certificate_fixtures.CertificateTests.due

    def create(self, certificate):
        response = self.client.post('/api/inbounds', headers=auth_tests.HEADERS, json={
            'core': 'xray', 'protocol': 'vless', 'port': 10463, 'remark': 'synthetic-xray',
            'profile': {'security': 'tls', 'transport': 'raw', 'flow': ''},
            'certificate_id': certificate})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['inbound']['id']

    def test_api_create_edit_renew_stop_retry_and_summary_preserve_contract(self):
        certificate = self.issue()
        with patch.object(inbound_api, 'apply_checked', return_value={'applied': True}), \
             patch.object(core_manager, 'validate_inbound_change'), \
             patch.object(core_manager, 'apply_database', return_value={'applied': True}) as apply, \
             patch.object(core_manager.get('xray'), 'status', return_value={'running': True}):
            identity = self.create(certificate)
            target = f'inbound:{identity}'
            with database.SessionLocal() as db:
                row = db.get(database.Inbound, identity)
                settings = deepcopy(row.settings)
                first = deepcopy(row.stream_settings)
                summary = to_dict(row)
                self.assertTrue(summary['managed_certificate_eligible'])
                self.assertNotIn(settings['users'][0]['id'], json.dumps(summary))
                self.assertNotIn('certificateFile', json.dumps(summary))
                self.assertNotIn(str(self.manager.root), json.dumps(summary))
                runtime = XrayAdapter().build_config([row])
                self.assertEqual(runtime['inbounds'][0]['streamSettings']['tlsSettings'], first['tlsSettings'])
                self.assertNotIn('_vui', runtime['inbounds'][0]['streamSettings'])
            state = self.client.get(f'/api/inbounds/{identity}/editor').json()
            self.assertEqual(state['certificate_id'], certificate)
            self.assertNotIn(settings['users'][0]['id'], json.dumps(state))
            response = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json={
                'remark': 'edited', 'profile': state['profile'], 'certificate_id': certificate,
                'expected_instance_revision': state['mutation_revision']})
            self.assertEqual(response.status_code, 200, response.text)
            self.due(certificate)
            self.manager.process_once()
            with database.SessionLocal() as db:
                row = db.get(database.Inbound, identity)
                self.assertEqual(row.settings, settings)
                self.assertNotEqual(row.stream_settings['tlsSettings'], first['tlsSettings'])
                revision = db.get(CertificateBinding, target).applied_revision
                self.assertEqual(revision, self.manager.material(certificate)[2])
                saved = deepcopy(row.stream_settings)
            # Instance preconditions identify the node, not a configuration
            # revision. Re-read managed material instead of saving stale paths.
            stale = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json={
                'profile': state['profile'], 'certificate_id': certificate,
                'expected_instance_revision': state['mutation_revision']})
            self.assertEqual(stale.status_code, 200, stale.text)
            with database.SessionLocal() as db:
                self.assertEqual(db.get(database.Inbound, identity).stream_settings, saved)
            self.provider.failure = True
            self.due(certificate)
            job = self.manager.renew(certificate)
            self.manager.process_once()
            self.assertEqual(self.manager.job(job['id'])['state'], 'failed')
            self.assertEqual(self.manager.material(certificate)[2], revision)
            with database.SessionLocal() as db:
                self.assertEqual(db.get(database.Inbound, identity).stream_settings, saved)
            self.provider.failure = False
            self.due(certificate)
            with patch.object(core_manager.get('xray'), 'status', return_value={'running': False}), \
                 patch.object(core_manager, 'apply_database', return_value={'applied': False}) as stopped:
                self.manager.process_once()
                self.assertFalse(stopped.call_args.kwargs['activate'])
            with database.SessionLocal() as db:
                binding = db.get(CertificateBinding, target)
                self.assertEqual(binding.error, 'CORE_STOPPED_PENDING_APPLY')
                self.assertEqual(binding.applied_revision, revision)
            self.assertEqual(self.manager.apply_existing(certificate), [{'target': target, 'applied': True}])
            self.assertEqual(apply.call_args.args, ('xray',))

    def test_unqualified_targets_reject_before_material_read(self):
        for protocol, transport, security, flow in (
                ('vmess', 'raw', 'tls', ''), ('trojan', 'raw', 'tls', ''),
                ('vless', 'websocket', 'tls', ''), ('vless', 'grpc', 'tls', ''),
                ('vless', 'xhttp', 'tls', ''), ('vless', 'raw', 'reality', ''),
                ('vless', 'raw', 'tls', 'xtls-rprx-vision')):
            with self.subTest(protocol=protocol, transport=transport, security=security, flow=flow):
                with patch.object(self.manager, 'material') as material:
                    response = self.client.post('/api/inbounds', headers=auth_tests.HEADERS, json={
                        'core': 'xray', 'protocol': protocol, 'port': 10464,
                        'profile': {'transport': transport, 'security': security, 'flow': flow},
                        'certificate_id': 'a' * 32})
                self.assertEqual(response.status_code, 409, response.text)
                material.assert_not_called()

    def test_missing_manual_tls_paths_are_chinese_and_do_not_save(self):
        for profile, message in (
                ({'security': 'tls', 'transport': 'raw'}, '请选择可用的正式证书，或填写 TLS 证书路径'),
                ({'security': 'tls', 'transport': 'raw', 'certificate_path': '/manual/cert.pem'}, '请填写 TLS 私钥路径')):
            response = self.client.post('/api/inbounds', headers=auth_tests.HEADERS,
                json={'core': 'xray', 'protocol': 'vless', 'port': 10464, 'profile': profile})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.json()['detail'], message)
        with database.SessionLocal() as db:
            self.assertEqual(db.query(database.Inbound).count(), 0)

    def test_explicit_sni_conflict_or_invalid_type_never_creates_or_edits_binding(self):
        certificate = self.issue()
        invalid = [('other.example.test', 409), (None, 422), (123, 422),
                   ({}, 422), ([], 422), ('not a domain', 422)]
        with patch.object(inbound_api, 'apply_checked', return_value={'applied': True}) as apply, \
             patch.object(core_manager, 'validate_inbound_change'), \
             patch.object(core_manager, 'apply_database', return_value={'applied': True}), \
             patch.object(core_manager.get('xray'), 'status', return_value={'running': True}):
            for server_name, status in invalid:
                with self.subTest(operation='create', server_name=server_name):
                    response = self.client.post('/api/inbounds', headers=auth_tests.HEADERS, json={
                        'core': 'xray', 'protocol': 'vless', 'port': 10465,
                        'profile': {'security': 'tls', 'transport': 'raw', 'server_name': server_name},
                        'certificate_id': certificate})
                    self.assertEqual(response.status_code, status, response.text)
                    self.assertIn('SNI', response.json()['detail'])
            apply.assert_not_called()
            with database.SessionLocal() as db:
                self.assertEqual(db.query(database.Inbound).count(), 0)
                self.assertEqual(db.query(CertificateBinding).count(), 0)
            identity = self.create(certificate)
            other = self.issue(domain='second.example.test')
            target = f'inbound:{identity}'
            state = self.client.get(f'/api/inbounds/{identity}/editor').json()
            with database.SessionLocal() as db:
                row = db.get(database.Inbound, identity)
                before = (deepcopy(row.settings), deepcopy(row.stream_settings), row.remark)
                binding = db.get(CertificateBinding, target)
                fields = ('certificate_id', 'applied_certificate_id', 'applied_revision', 'error')
                before_binding = {field: getattr(binding, field) for field in fields}
            apply.reset_mock()
            for server_name, status in invalid + [('panel.example.test', 409)]:
                with self.subTest(operation='edit', server_name=server_name):
                    response = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json={
                        'remark': 'must-not-save', 'profile': {**state['profile'], 'server_name': server_name},
                        'certificate_id': other if server_name == 'panel.example.test' else certificate,
                        'expected_instance_revision': state['mutation_revision']})
                    self.assertEqual(response.status_code, status, response.text)
                    with database.SessionLocal() as db:
                        row = db.get(database.Inbound, identity)
                        self.assertEqual((row.settings, row.stream_settings, row.remark), before)
                        binding = db.get(CertificateBinding, target)
                        self.assertEqual({field: getattr(binding, field) for field in fields}, before_binding)
            apply.assert_not_called()

    def test_empty_and_domain_equivalent_sni_create_edit_use_canonical_certificate_domain(self):
        certificate = self.issue()
        with patch.object(inbound_api, 'apply_checked', return_value={'applied': True}), \
             patch.object(core_manager, 'validate_inbound_change'), \
             patch.object(core_manager, 'apply_database', return_value={'applied': True}), \
             patch.object(core_manager.get('xray'), 'status', return_value={'running': True}):
            for index, server_name in enumerate(('', 'PANEL.EXAMPLE.TEST', 'panel.example.test.')):
                with self.subTest(server_name=server_name):
                    response = self.client.post('/api/inbounds', headers=auth_tests.HEADERS, json={
                        'core': 'xray', 'protocol': 'vless', 'port': 10500 + index,
                        'profile': {'security': 'tls', 'transport': 'raw', 'server_name': server_name},
                        'certificate_id': certificate})
                    self.assertEqual(response.status_code, 200, response.text)
                    identity = response.json()['inbound']['id']
                    state = self.client.get(f'/api/inbounds/{identity}/editor').json()
                    self.assertEqual(state['profile']['server_name'], 'panel.example.test')
                    edited = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json={
                        'profile': {**state['profile'], 'server_name': server_name}, 'certificate_id': certificate,
                        'expected_instance_revision': state['mutation_revision']})
                    self.assertEqual(edited.status_code, 200, edited.text)
                    with database.SessionLocal() as db:
                        self.assertEqual(db.get(database.Inbound, identity).stream_settings['tlsSettings']['serverName'],
                                         'panel.example.test')

    def test_renewal_refuses_manual_paths_and_explicit_unsupported_rebind_preserves_binding(self):
        certificate = self.issue()
        with patch.object(inbound_api, 'apply_checked', return_value={'applied': True}), \
             patch.object(core_manager, 'validate_inbound_change'), \
             patch.object(core_manager, 'apply_database', return_value={'applied': True}), \
             patch.object(core_manager.get('xray'), 'status', return_value={'running': True}):
            identity = self.create(certificate)
            target = f'inbound:{identity}'
            with database.SessionLocal() as db:
                row = db.get(database.Inbound, identity)
                stream = deepcopy(row.stream_settings)
                stream['tlsSettings']['certificates'][0]['keyFile'] = '/manual/key.pem'
                row.stream_settings = stream
                db.commit()
            self.assertEqual(self.manager.apply_existing(certificate)[0]['error'], 'BINDING_CHANGED_MANUALLY')
            with database.SessionLocal() as db:
                row = db.get(database.Inbound, identity)
                stream = deepcopy(row.stream_settings)
                stream['method'] = 'grpc'
                row.stream_settings = stream
                db.commit()
                before = deepcopy(db.get(CertificateBinding, target).__dict__)
            with self.assertRaisesRegex(CertificateError, 'UNSUPPORTED_CERTIFICATE_TARGET'):
                self.manager.bind(certificate, target)
            with database.SessionLocal() as db:
                after = db.get(CertificateBinding, target)
                for field in ('certificate_id', 'applied_certificate_id', 'applied_revision', 'error'):
                    self.assertEqual(getattr(after, field), before[field])

    def test_target_machine_codes_and_rejected_rebind_preserve_existing_state(self):
        certificate = self.issue()
        cases = [
            ('sing-box', 'hysteria2', {}, {}, 'TLS_NODE_REQUIRED'),
            ('sing-box', 'hysteria2', {}, {'tls': {'enabled': False}}, 'TLS_NODE_REQUIRED'),
            ('sing-box', 'tuic', {}, {}, 'TLS_NODE_REQUIRED'),
            ('sing-box', 'tuic', {}, {'tls': {'enabled': False}}, 'TLS_NODE_REQUIRED'),
            ('sing-box', 'tuic', {}, {'tls': {'enabled': True, 'reality': {'enabled': True}}}, 'TLS_NODE_REQUIRED'),
            ('sing-box', 'unsupported', {}, {}, 'UNSUPPORTED_CERTIFICATE_TARGET'),
            ('xray', 'hysteria2', {}, {}, 'UNSUPPORTED_CERTIFICATE_TARGET'),
            ('xray', 'tuic', {}, {}, 'UNSUPPORTED_CERTIFICATE_TARGET'),
            ('xray', 'vless', {}, {'method': 'raw'}, 'TLS_NODE_REQUIRED'),
            ('xray', 'vless', {}, {'method': 'grpc'}, 'UNSUPPORTED_CERTIFICATE_TARGET'),
            ('xray', 'vless', {'users': [{'flow': 'xtls-rprx-vision'}]}, {'method': 'raw'}, 'UNSUPPORTED_CERTIFICATE_TARGET'),
            ('xray', 'vless', {}, {'method': 'raw', 'security': 'tls', 'tlsSettings': {'certificates': [{'certificate': ['inline']}]}}, 'UNSUPPORTED_CERTIFICATE_TARGET'),
            ('xray', 'vless', {}, {'method': 'raw', 'security': 'tls', 'tlsSettings': {'certificates': [{}, {}]}}, 'UNSUPPORTED_CERTIFICATE_TARGET'),
            # REALITY remains the first TLS exclusion even for an unsupported target.
            ('xray', 'unsupported', {}, {'security': 'reality'}, 'TLS_NODE_REQUIRED'),
        ]
        fields = ('certificate_id', 'applied_certificate_id', 'applied_revision', 'error')
        for index, (core, protocol, settings, stream, expected) in enumerate(cases):
            with self.subTest(core=core, protocol=protocol, stream=stream):
                with database.SessionLocal() as db:
                    row = database.Inbound(core=core, protocol=protocol, port=12000 + index,
                        enable=True, settings=settings, stream_settings=stream)
                    db.add(row)
                    db.flush()
                    target = f'inbound:{row.id}'
                    self.assertEqual(target_error(row), expected)
                    self.assertFalse(to_dict(row)['managed_certificate_eligible'])
                    binding = CertificateBinding(target=target, certificate_id='a' * 32,
                        applied_certificate_id='b' * 32, applied_revision='c' * 64,
                        error='CORE_STOPPED_PENDING_APPLY')
                    db.add(binding)
                    db.commit()
                    before = {field: getattr(binding, field) for field in fields}
                with patch.object(core_manager, 'apply_database') as apply:
                    with self.assertRaisesRegex(CertificateError, expected):
                        self.manager.bind(certificate, target)
                    apply.assert_not_called()
                with database.SessionLocal() as db:
                    binding = db.get(CertificateBinding, target)
                    self.assertEqual({field: getattr(binding, field) for field in fields}, before)


if __name__ == '__main__':
    unittest.main()
