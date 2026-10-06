"""Managed TUIC TLS preserves secrets and separates issuance from application."""
from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from cryptography.hazmat.primitives import serialization

from app.api import inbounds as inbound_api
from app.certificates.material import CertificateError
from app.certificates.models import CertificateBinding
from app.models import database
from app.services.core_manager import core_manager
from app.services import core_manager as core_module
from app.services.core_runtime import CoreRuntime
from app.services.inbound_service import create_inbound
import test_auth as auth_tests
import test_certificates as certificate_tests
import test_tuic_preflight_loopback as tuic_preflight
from test_inbound_security_binding import BindingManager
from loopback_helpers import CoreProcess, start_http_target, unused_port


PASSWORD = 'Synthetic-TUIC:p@ss/word?+#%'
UUID = '11111111-1111-4111-8111-111111111111'


class TUICManagedEditorTests(unittest.TestCase):
    def setUp(self):
        auth_tests.AuthenticationTests.setUp(self)
        self.assertEqual(auth_tests.AuthenticationTests.login(self).status_code, 200)
        self.manager = BindingManager()
        patch('app.certificates.manager.get_manager', return_value=self.manager).start()
        self.apply = patch.object(inbound_api, 'apply_checked', return_value={'applied': True}).start()

    def create(self):
        result = self.client.post('/api/inbounds', headers=auth_tests.HEADERS, json={
            'core': 'sing-box', 'protocol': 'tuic', 'remark': 'managed-tuic', 'port': 10451,
            'profile': {'security': 'tls', 'transport': 'quic', 'tuic_uuid': UUID, 'tuic_password': PASSWORD,
                        'client_fingerprint': '', 'up_mbps': None, 'down_mbps': None},
            'certificate_id': 'a' * 32})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()['inbound']['id']

    def snapshot(self, identity):
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, identity)
            return deepcopy(row.settings), deepcopy(row.stream_settings)

    def test_create_edit_refresh_keep_binding_and_never_return_auth_password(self):
        identity = self.create()
        original = self.snapshot(identity)
        self.assertEqual(original[0]['users'], [{'uuid': UUID, 'password': PASSWORD}])
        self.assertNotIn('transport', original[1])
        for field in ('up_mbps', 'down_mbps', 'obfs'):
            self.assertNotIn(field, original[0])
        for index in range(2):
            state = self.client.get(f'/api/inbounds/{identity}/editor')
            self.assertEqual(state.status_code, 200, state.text)
            state = state.json()
            self.assertEqual(state['certificate_id'], 'a' * 32)
            self.assertEqual(state['profile']['transport'], 'quic')
            self.assertEqual(state['profile']['security'], 'tls')
            self.assertEqual(state['profile']['server_name'], 'vpn.example.test')
            self.assertTrue(state['profile']['tuic_password_set'])
            self.assertTrue(state['profile']['tuic_uuid_set'])
            self.assertFalse(state['profile'].get('tuic_uuid'))
            self.assertNotIn(UUID, json.dumps(state))
            self.assertFalse(state['profile'].get('tuic_password'))
            self.assertNotIn(PASSWORD, json.dumps(state))
            result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json={
                'remark': f'managed-tuic-edited-{index}',
                'profile': {**state['profile'], 'tuic_password': ''},
                'certificate_id': state['certificate_id']})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(self.snapshot(identity), original)

    def test_explicit_password_replacement_preserves_tls_and_binding(self):
        identity = self.create()
        _, stream = self.snapshot(identity)
        replacement = 'Synthetic-new-TUIC:p@ss/word?#%'
        state = self.client.get(f'/api/inbounds/{identity}/editor').json()
        result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json={
            'profile': {**state['profile'], 'tuic_password': replacement},
            'certificate_id': 'a' * 32})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.snapshot(identity), ({'users': [{'uuid': UUID, 'password': replacement}]}, stream))
        refreshed = self.client.get(f'/api/inbounds/{identity}/editor').json()
        self.assertEqual(refreshed['certificate_id'], 'a' * 32)
        self.assertTrue(refreshed['profile']['tuic_password_set'])
        for secret in (PASSWORD, replacement):
            self.assertNotIn(secret, json.dumps(refreshed))

    def test_non_tls_and_unbind_attempts_fail_without_mutating_saved_node(self):
        identity = self.create()
        original = self.snapshot(identity)
        state = self.client.get(f'/api/inbounds/{identity}/editor').json()
        self.apply.reset_mock()
        for security in ('none', 'reality'):
            with self.subTest(security=security):
                result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json={
                    'profile': {**state['profile'], 'security': security}, 'certificate_id': 'a' * 32})
                self.assertEqual(result.status_code, 409, result.text)
                self.assertEqual(self.snapshot(identity), original)
                self.assertEqual(self.manager.bound, 'a' * 32)
        result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json={
            'profile': state['profile'], 'certificate_id': None})
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(self.snapshot(identity), original)
        self.assertEqual(self.manager.unbound, [])
        self.apply.assert_not_called()

    def test_other_targets_remain_rejected_before_material_lookup(self):
        for core, protocol, security in (
                ('xray', 'tuic', 'tls'), ('sing-box', 'unsupported', 'tls'),
                ('sing-box', 'shadowsocks', 'tls'), ('sing-box', 'tuic', 'none'),
                ('sing-box', 'tuic', 'reality')):
            with self.subTest(core=core, protocol=protocol, security=security):
                with self.assertRaises(HTTPException) as rejected:
                    inbound_api._managed_certificate({'security': security}, 'a' * 32, core, protocol)
                self.assertEqual(rejected.exception.status_code, 409)
        self.assertEqual(self.manager.material_calls, 0)


class TUICManagedLifecycleTests(unittest.TestCase):
    # Reuse the isolated real certificate/SQLite fixture without inheriting its
    # unrelated tests. SigningProvider is a temporary CA, never a public account.
    setUp = certificate_tests.CertificateTests.setUp
    login = certificate_tests.CertificateTests.login
    issue = certificate_tests.CertificateTests.issue
    due = certificate_tests.CertificateTests.due

    def node(self, *, protocol='tuic', core='sing-box', tls=None):
        stream = {'tls': tls if tls is not None else {'enabled': True, 'server_name': 'vpn.example.test'},
                  '_vui': {'security': 'tls', 'server_name': 'vpn.example.test',
                           'client_fingerprint': '', 'skip_cert_verify': False}}
        with database.SessionLocal() as db:
            row = database.Inbound(core=core, protocol=protocol, port=10451, enable=True,
                settings={'users': [{'uuid': UUID, 'password': PASSWORD}]}, stream_settings=stream)
            db.add(row); db.commit(); db.refresh(row)
            return row.id

    def snapshot(self, identity):
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, identity)
            return deepcopy(row.settings), deepcopy(row.stream_settings)

    def test_bind_renew_failure_and_stopped_pending_apply_preserve_password_and_material(self):
        certificate_id = self.issue(domain='vpn.example.test')
        identity = self.node()
        target = f'inbound:{identity}'
        with patch.object(core_manager.get('sing-box'), 'status', return_value={'running': True}), \
             patch.object(core_manager, 'apply_database', return_value={'applied': True}) as apply:
            bound = self.client.post(f'/api/certificates/{certificate_id}/bind-inbound',
                headers=auth_tests.HEADERS, json={'inbound_id': identity})
            self.assertEqual(bound.status_code, 200, bound.text)
            apply.assert_called_with('sing-box', activate=True)
            first_paths, _, first_revision = self.manager.material(certificate_id)
            old_bytes = tuple(path.read_bytes() for path in first_paths)
            self.due(certificate_id)
            job = self.manager.renew(certificate_id)
            self.assertTrue(self.manager.process_once())
            self.assertEqual(self.manager.job(job['id'])['state'], 'succeeded')
        paths, domain, revision = self.manager.material(certificate_id)
        self.assertEqual(domain, 'vpn.example.test')
        self.assertNotEqual(revision, first_revision)
        self.assertEqual(tuple(path.read_bytes() for path in first_paths), old_bytes)
        settings, stream = self.snapshot(identity)
        self.assertEqual(settings, {'users': [{'uuid': UUID, 'password': PASSWORD}]})
        self.assertNotIn('transport', stream)
        self.assertEqual(stream['tls']['certificate_path'], str(paths[0]))
        self.assertEqual(stream['tls']['key_path'], str(paths[1]))
        self.assertEqual(stream['_vui']['server_name'], domain)
        with database.SessionLocal() as db:
            binding = db.get(CertificateBinding, target)
            self.assertEqual(binding.applied_revision, revision)
            self.assertEqual(binding.applied_certificate_id, certificate_id)
            self.assertIsNone(binding.error)

        # CA failure must keep the complete desired/applied node and active PEM.
        self.due(certificate_id)
        self.provider.failure = True
        with patch.object(core_manager, 'apply_database') as apply:
            failed = self.manager.renew(certificate_id)
            self.assertTrue(self.manager.process_once())
            self.assertEqual(self.manager.job(failed['id'])['state'], 'failed')
            self.assertEqual(self.manager.job(failed['id'])['error'], 'ACME_VALIDATION_FAILED')
            apply.assert_not_called()
        self.assertEqual(self.manager.material(certificate_id)[2], revision)
        self.assertEqual(self.snapshot(identity), (settings, stream))
        self.assertEqual(tuple(path.read_bytes() for path in first_paths), old_bytes)

        # Successful issuance on a stopped core saves desired material only.
        self.provider.failure = False
        self.due(certificate_id)
        with patch.object(core_manager.get('sing-box'), 'status', return_value={'running': False}), \
             patch.object(core_manager, 'apply_database', return_value={'applied': False}) as apply:
            renewed = self.manager.renew(certificate_id)
            self.assertTrue(self.manager.process_once())
            self.assertEqual(self.manager.job(renewed['id'])['state'], 'succeeded')
            apply.assert_called_once_with('sing-box', activate=False)
        next_paths, _, next_revision = self.manager.material(certificate_id)
        self.assertNotEqual(next_revision, revision)
        pending_settings, pending_stream = self.snapshot(identity)
        self.assertEqual(pending_settings, settings)
        self.assertEqual(pending_stream, {**stream, 'tls': {
            **stream['tls'], 'certificate_path': str(next_paths[0]), 'key_path': str(next_paths[1])}})
        with database.SessionLocal() as db:
            binding = db.get(CertificateBinding, target)
            self.assertEqual(binding.applied_revision, revision)
            self.assertEqual(binding.error, 'CORE_STOPPED_PENDING_APPLY')
        listed = self.client.get('/api/certificates').json()[0]
        self.assertTrue(listed['bindings'][0]['pending'])
        self.assertEqual(listed['bindings'][0]['error'], 'CORE_STOPPED_PENDING_APPLY')
        self.assertNotIn(PASSWORD, json.dumps(listed))
        self.assertNotIn(next_paths[1].read_text(), json.dumps(listed))
        with patch.object(core_manager.get('sing-box'), 'status', return_value={'running': True}), \
             patch.object(core_manager, 'apply_database', return_value={'applied': True}) as apply:
            result = self.manager.apply_existing(certificate_id)
            self.assertEqual(result, [{'target': target, 'applied': True}])
            apply.assert_called_once_with('sing-box', activate=True)
        with database.SessionLocal() as db:
            binding = db.get(CertificateBinding, target)
            self.assertEqual(binding.applied_revision, next_revision)
            self.assertIsNone(binding.error)

    def test_apply_failure_keeps_old_applied_revision_distinct_from_issued_material(self):
        certificate_id = self.issue(domain='vpn.example.test')
        identity = self.node()
        target = f'inbound:{identity}'
        with patch.object(core_manager.get('sing-box'), 'status', return_value={'running': True}), \
             patch.object(core_manager, 'apply_database', return_value={'applied': True}):
            self.manager.bind(certificate_id, target)
        paths, _, previous = self.manager.material(certificate_id)
        material = tuple(path.read_bytes() for path in paths)
        self.due(certificate_id)
        with patch.object(core_manager.get('sing-box'), 'status', return_value={'running': True}), \
             patch.object(core_manager, 'apply_database', side_effect=RuntimeError('synthetic apply failure')):
            job = self.manager.renew(certificate_id)
            self.assertTrue(self.manager.process_once())
        self.assertEqual(self.manager.job(job['id'])['state'], 'succeeded')
        self.assertNotEqual(self.manager.material(certificate_id)[2], previous)
        self.assertEqual(tuple(path.read_bytes() for path in paths), material)
        self.assertEqual(self.snapshot(identity)[0], {'users': [{'uuid': UUID, 'password': PASSWORD}]})
        with database.SessionLocal() as db:
            binding = db.get(CertificateBinding, target)
            self.assertEqual(binding.applied_revision, previous)
            self.assertEqual(binding.error, 'CERTIFICATE_APPLY_FAILED')

    def test_manager_retains_target_and_tls_guards(self):
        certificate_id = self.issue(domain='vpn.example.test')
        for core, protocol, tls, error in (
                ('xray', 'tuic', None, 'UNSUPPORTED_CERTIFICATE_TARGET'),
                ('sing-box', 'unsupported', None, 'UNSUPPORTED_CERTIFICATE_TARGET'),
                ('sing-box', 'tuic', {'enabled': False}, 'TLS_NODE_REQUIRED'),
                ('sing-box', 'tuic', {'enabled': True, 'reality': {'enabled': True}}, 'TLS_NODE_REQUIRED')):
            with self.subTest(core=core, protocol=protocol, tls=tls):
                identity = self.node(core=core, protocol=protocol, tls=tls)
                before = self.snapshot(identity)
                with patch.object(core_manager, 'apply_database') as apply:
                    with self.assertRaisesRegex(CertificateError, error):
                        self.manager.bind(certificate_id, f'inbound:{identity}')
                    apply.assert_not_called()
                self.assertEqual(self.snapshot(identity), before)


@unittest.skipUnless(os.getenv('VUI_TEST_CORES') and os.getenv('VUI_TEST_MIHOMO'),
                     'pinned real sing-box and Mihomo binaries required')
class TUICManagedRealCoreTests(unittest.TestCase):
    """Real managed QUIC reloads use the same operator runtime as the panel.

    Client construction and HTTP assertions reuse the pinned bare preflight;
    no client TLS bypass or global trust-store changes are made here.
    """
    login = certificate_tests.CertificateTests.login
    issue = certificate_tests.CertificateTests.issue
    due = certificate_tests.CertificateTests.due
    unique_path = tuic_preflight.TUICPreflightLoopbackTests.unique_path
    client_config = tuic_preflight.TUICPreflightLoopbackTests.client_config
    check_command = tuic_preflight.TUICPreflightLoopbackTests.check_command
    prepare_client = tuic_preflight.TUICPreflightLoopbackTests.prepare_client
    logs = tuic_preflight.TUICPreflightLoopbackTests.logs
    assert_success = tuic_preflight.TUICPreflightLoopbackTests.assert_success

    @classmethod
    def setUpClass(cls):
        tuic_preflight.TUICPreflightLoopbackTests.setUpClass.__func__(cls)

    def setUp(self):
        certificate_tests.CertificateTests.setUp(self)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.temp.name)
        self.ca = self.root / 'managed-test-ca.pem'
        self.ca.write_bytes(self.provider.ca.public_bytes(serialization.Encoding.PEM))
        (self.root / 'empty-roots').mkdir()
        self.env = {**os.environ, 'SSL_CERT_FILE': str(self.ca),
                    'SSL_CERT_DIR': str(self.root / 'empty-roots')}
        self.sequence = 0
        self.real_cores = core_module.CoreManager()
        self.runtime = CoreRuntime('sing-box', Path(self.singbox), self.root / 'managed-runtime')
        self.real_cores.get('sing-box')._runtime = self.runtime
        patch.object(core_module, 'core_manager', self.real_cores).start()
        self.stack.callback(self.real_cores.stop_all)

    def forward_both(self, server_port, phase):
        # Each phase launches fresh clients and new QUIC sessions, so an old
        # connection cannot conceal failed server restarts or TLS reloads.
        for client in tuic_preflight.CLIENTS:
            with self.subTest(client=client, phase=phase), ExitStack() as processes:
                port = unused_port()
                command, env = self.prepare_client(client, port, server_port)
                process = CoreProcess(processes, command, self.unique_path(client, 'log'), env)
                process.start(port)
                self.assert_success(port)
                self.assertIsNone(process.process.poll(), self.logs())
                self.assertTrue(self.runtime.status()['running'])
                print(f'TUIC managed TLS {phase}: {client} HTTP/TCP over QUIC; fresh verified session')

    def test_managed_bind_renew_failed_issue_stopped_apply_and_restart_forward_both_clients(self):
        certificate_id = self.issue(domain=tuic_preflight.SNI)
        paths, _, first_revision = self.manager.material(certificate_id)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.bind(('127.0.0.1', 0))
            server_port = udp.getsockname()[1]
        with database.SessionLocal() as db:
            row = create_inbound(db, {
                'core': 'sing-box', 'protocol': 'tuic', 'remark': 'managed-real-tuic',
                'port': server_port, 'profile': {
                    'security': 'tls', 'transport': 'quic', 'server_name': tuic_preflight.SNI,
                    'certificate_path': str(paths[0]), 'key_path': str(paths[1]),
                    'tuic_uuid': tuic_preflight.UUID, 'tuic_password': tuic_preflight.PASSWORD,
                    'client_fingerprint': '', 'up_mbps': None, 'down_mbps': None,
                    'skip_cert_verify': False}})
            identity = row.id
        target = f'inbound:{identity}'
        applied = self.real_cores.apply_database('sing-box')
        self.assertTrue(applied['applied'])
        self.assertTrue(applied['running'])
        self.manager.bind(certificate_id, target)
        config = json.loads(self.runtime.config_path().read_text())
        server = config['inbounds'][0]
        self.assertEqual(server['type'], 'tuic')
        self.assertEqual(server['users'], [{'uuid': tuic_preflight.UUID, 'password': tuic_preflight.PASSWORD}])
        for field in ('transport', 'up_mbps', 'down_mbps', 'obfs'):
            self.assertNotIn(field, server)
        self.target_port, self.requests = start_http_target(self.stack)
        self.forward_both(server_port, 'bound')

        self.due(certificate_id)
        job = self.manager.renew(certificate_id)
        self.assertTrue(self.manager.process_once())
        self.assertEqual(self.manager.job(job['id'])['state'], 'succeeded')
        next_paths, _, next_revision = self.manager.material(certificate_id)
        self.assertNotEqual(next_revision, first_revision)
        actual = json.loads(self.runtime.config_path().read_text())['inbounds'][0]
        self.assertEqual(actual['tls']['certificate_path'], str(next_paths[0]))
        self.assertEqual(actual['tls']['key_path'], str(next_paths[1]))
        self.assertEqual(actual['users'], server['users'])
        with database.SessionLocal() as db:
            self.assertEqual(db.get(CertificateBinding, target).applied_revision, next_revision)
        self.forward_both(server_port, 'renewed')

        before_state = self.runtime.state_file.read_bytes()
        before_pid = self.runtime.process.pid
        before_material = tuple(path.read_bytes() for path in next_paths)
        self.due(certificate_id)
        self.provider.failure = True
        failed = self.manager.renew(certificate_id)
        self.assertTrue(self.manager.process_once())
        self.assertEqual(self.manager.job(failed['id'])['error'], 'ACME_VALIDATION_FAILED')
        self.assertEqual(self.manager.material(certificate_id)[2], next_revision)
        self.assertEqual(tuple(path.read_bytes() for path in next_paths), before_material)
        self.assertEqual(self.runtime.state_file.read_bytes(), before_state)
        self.assertEqual(self.runtime.process.pid, before_pid)
        self.forward_both(server_port, 'failed issuance retains old material')

        self.runtime.stop()
        stopped_state = self.runtime.state_file.read_bytes()
        self.assertFalse(self.runtime.status()['running'])
        self.provider.failure = False
        self.due(certificate_id)
        pending = self.manager.renew(certificate_id)
        self.assertTrue(self.manager.process_once())
        self.assertEqual(self.manager.job(pending['id'])['state'], 'succeeded')
        newest_paths, _, newest_revision = self.manager.material(certificate_id)
        self.assertNotEqual(newest_revision, next_revision)
        self.assertEqual(self.runtime.state_file.read_bytes(), stopped_state)
        self.assertFalse(self.runtime.status()['running'])
        with database.SessionLocal() as db:
            binding = db.get(CertificateBinding, target)
            self.assertEqual(binding.applied_revision, next_revision)
            self.assertEqual(binding.error, 'CORE_STOPPED_PENDING_APPLY')
            desired = db.get(database.Inbound, identity)
            self.assertEqual(desired.settings['users'], server['users'])
            self.assertEqual(desired.stream_settings['tls']['certificate_path'], str(newest_paths[0]))
        restarted = self.real_cores.apply_database('sing-box', activate=True)
        self.assertTrue(restarted['applied'])
        self.assertEqual(self.manager.apply_existing(certificate_id), [{'target': target, 'applied': True}])
        with database.SessionLocal() as db:
            binding = db.get(CertificateBinding, target)
            self.assertEqual(binding.applied_revision, newest_revision)
            self.assertIsNone(binding.error)
        self.forward_both(server_port, 'operator restart applies pending certificate')


if __name__ == '__main__':
    unittest.main()
