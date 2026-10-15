"""Active-only manager/ingress integration and fail-closed runtime boundaries.

These use temporary SQLite and a fake process runtime. They establish commit,
qualification and recovery semantics, not real-core or double-hop acceptance.
"""
from copy import deepcopy
import json
from pathlib import Path
import socket
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import database
from app.services import core_manager as manager_module
from app.services.core_manager import CoreManager, SingBoxAdapter
from app.services.core_runtime import CoreError, CoreRuntime, atomic_write, encode, revision
from app.services.inbound_service import create_inbound, update_inbound, delete_inbound
from app.services.proxy_apply import ProxyApplyCoordinator
from app.services.proxy_policy import LOCAL_TAG, UPSTREAM_TAG, VerifiedEndpoint
from app.services.proxy_store import ProxyStore, ProxyStorageError

UUID = '11111111-1111-4111-8111-111111111111'
PASSWORD = 'fixture-proxy-password-only'


def profile():
    return {'schema_version': 1, 'protocol': 'vless', 'name': 'test upstream',
            'server': 'upstream.example.test', 'server_port': 443, 'uuid': UUID,
            'transport': 'tcp', 'encryption': 'none', 'flow': '',
            'tls': {'mode': 'tls', 'server_name': 'upstream.example.test', 'alpn': [],
                    'fingerprint': '', 'public_key': '', 'short_id': ''}}


def payload(port=18443, **changes):
    return {'core': 'sing-box', 'protocol': 'vless', 'port': port, 'enable': True,
            'settings': {'users': [{'uuid': UUID}]}, 'stream_settings': {'tls': {
                'enabled': True, 'server_name': 'ingress.example.test',
                'certificate_path': '/fixture/certificate.pem', 'key_path': '/fixture/key.pem'}},
            **changes}


class MemoryRuntime:
    def __init__(self, name='sing-box'):
        self.name = name
        self.committed = {'revision': None, 'enabled': False}
        self.configs = {}
        self.running = False
        self.calls = []
        self.fail_next = False
        self.last_error = None

    def state(self):
        return deepcopy(self.committed)

    def status(self):
        return {'name': self.name, 'applied_revision': self.committed['revision'],
                'enabled': self.committed['enabled'], 'running': self.running,
                'config_exists': self.committed['revision'] is not None, 'last_error': self.last_error}

    def apply(self, config, *, activate=True, rollback_on_failure=True):
        self.calls.append(('apply', activate))
        rev = revision(config)
        self.configs[rev] = deepcopy(config)
        if activate:
            self.committed = {'revision': rev, 'enabled': bool(config.get('inbounds'))}
            self.running = self.committed['enabled']
            if self.fail_next:
                self.fail_next = False
                raise CoreError('Synthetic runtime failure')
        return {**self.status(), 'valid': True, 'applied': activate, 'candidate_revision': rev}

    def stop(self, *, persist=True):
        self.calls.append(('stop', persist))
        self.running = False
        if persist:
            self.committed['enabled'] = False

    def recover(self):
        self.calls.append(('recover',))
        self.running = self.committed['enabled']

    def restore(self, rev, *, enabled):
        self.calls.append(('restore', rev, enabled))
        if rev is not None and rev not in self.configs:
            raise CoreError('Synthetic missing recovery config')
        self.committed = {'revision': rev, 'enabled': enabled}
        self.running = enabled

    def close(self):
        self.stop(persist=False)

    @property
    def config(self):
        return self.configs[self.committed['revision']]


class ProxyManagerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vui-manager-proxy-')
        self.addCleanup(self.temp.cleanup)
        self.engine = create_engine('sqlite:///' + str(Path(self.temp.name) / 'db.sqlite'))
        self.addCleanup(self.engine.dispose)
        database.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.Session()
        self.addCleanup(self.db.close)
        self.manager = CoreManager()
        self.runtime = MemoryRuntime()
        self.xray = MemoryRuntime('xray')
        self.manager.get('sing-box')._runtime = self.runtime
        self.manager.get('xray')._runtime = self.xray
        self.store = ProxyStore(Path(self.temp.name) / 'private')
        self.resolutions = []

        def resolve(identifier, identity, value, *, self_addresses):
            self.resolutions.append((identifier, identity))
            return VerifiedEndpoint(identifier, identity, value['server'], ('8.8.8.8',), 1792080000)

        self.coordinator = ProxyApplyCoordinator(self.store, self.runtime,
            lambda rows: SingBoxAdapter().build_config(row for row in rows if row.core == 'sing-box'),
            self.manager.lock, rows_provider=self.manager._all_rows,
            resolver=resolve, address_provider=lambda origin: ('127.0.0.1', '9.9.9.9'),
            preflight=lambda config, runtime: None)
        self.manager._proxy_coordinator = self.coordinator
        self.patches = [patch.object(database, 'SessionLocal', self.Session),
                        patch.object(manager_module, 'core_manager', self.manager)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def draft(self, *, local=False, relay=False):
        snapshot = self.store.read_snapshot()
        if not snapshot['upstreams']:
            saved = self.store.save_upstream(profile(), snapshot['revision'])
            snapshot, upstream = saved['snapshot'], saved['upstream_id']
        else:
            upstream = snapshot['upstreams'][0]['id']
        settings = {key: value for key, value in snapshot['settings'].items() if key != 'local_password_set'}
        settings.update(local_proxy_enabled=local, relay_enabled=relay,
                        selected_upstream_id=upstream, local_port=17891, local_username='fixture')
        return self.store.save_draft(settings, snapshot['revision'], local_password=PASSWORD)

    def activate(self, *, local=False, relay=False):
        value = self.draft(local=local, relay=relay)
        return self.manager.apply_proxy(value['revision'], 'apply-' + value['revision'])

    def rows(self):
        self.db.expire_all()
        return self.db.query(database.Inbound).order_by(database.Inbound.id).all()

    def test_never_applied_ordinary_mode_keeps_original_configs(self):
        a = create_inbound(self.db, payload())
        b = create_inbound(self.db, {'core': 'xray', 'protocol': 'vmess', 'port': 18444})
        self.manager.apply_database('sing-box')
        self.manager.apply_database('xray')
        self.assertEqual(self.runtime.config, self.manager.get('sing-box').build_config([a]))
        self.assertEqual(self.xray.config, self.manager.get('xray').build_config([b]))
        self.assertIsNone(self.store.load_active())
        self.assertEqual(self.resolutions, [])

    def test_active_relay_guards_create_enable_update_and_source_binding_before_commit(self):
        a = create_inbound(self.db, payload())
        self.activate(relay=True)
        for values in ({'core': 'xray', 'protocol': 'vless', 'port': 18444},
                       payload(18445, protocol='vmess'), payload(18446, tag='vui-relay-collision')):
            with self.subTest(values=values), self.assertRaises(HTTPException) as caught:
                create_inbound(self.db, values)
            self.assertFalse(caught.exception.detail['saved'])
            self.assertEqual(len(self.rows()), 1)
        disabled = create_inbound(self.db, {'core': 'xray', 'protocol': 'vless', 'port': 18444, 'enable': False})
        with self.assertRaises(HTTPException):
            update_inbound(self.db, disabled.id, {'enable': True})
        self.assertFalse(self.db.get(database.Inbound, disabled.id).enable)
        with self.assertRaises(HTTPException):
            update_inbound(self.db, a.id, {'stream_settings': {}})
        self.assertTrue(self.db.get(database.Inbound, a.id).stream_settings['tls']['enabled'])
        delete_inbound(self.db, disabled.id)
        self.assertEqual(len(self.rows()), 1)

    def test_qualified_new_row_gets_real_id_and_active_routing_without_rebootstrap(self):
        create_inbound(self.db, payload())
        self.activate(relay=True)
        before = self.store.load_active()['revision']
        saved = create_inbound(self.db, payload(18444))
        self.manager.apply_database('sing-box')
        self.assertIn('vui-relay-ingress-' + str(saved.id), [row['tag'] for row in self.runtime.config['inbounds']])
        self.assertEqual(self.store.load_active()['revision'], before)
        self.assertEqual(len(self.resolutions), 1)

    def test_newer_draft_does_not_change_apply_or_dirty_identity(self):
        item = create_inbound(self.db, payload())
        self.activate(local=True, relay=True)
        old_active = self.store.load_active()['revision']
        self.draft(local=False, relay=False)
        self.assertFalse(self.manager.status()['sing-box']['dirty'])
        update_inbound(self.db, item.id, {'port': 18555})
        self.assertTrue(self.manager.status()['sing-box']['dirty'])
        result = self.manager.apply_database('sing-box')
        self.assertTrue(result['applied'])
        self.assertEqual(self.store.load_active()['revision'], old_active)
        self.assertTrue(self.store.load_active()['settings']['relay_enabled'])
        self.assertIn(LOCAL_TAG, [row['tag'] for row in self.runtime.config['inbounds']])
        self.assertIn(UPSTREAM_TAG, [row['tag'] for row in self.runtime.config['outbounds']])
        self.assertFalse(self.manager.status()['sing-box']['dirty'])
        self.assertEqual(len(self.resolutions), 1)

    def test_local_only_xray_mutation_keeps_singbox_runtime_unchanged(self):
        self.activate(local=True)
        old = self.runtime.state()
        calls = len(self.runtime.calls)
        create_inbound(self.db, {'core': 'xray', 'protocol': 'vmess', 'port': 18444})
        self.manager.apply_database('xray')
        self.assertEqual(self.runtime.state(), old)
        self.assertEqual(len(self.runtime.calls), calls)
        self.assertTrue(self.store.load_active()['settings']['local_proxy_enabled'])

    def test_core_specific_apply_cannot_hide_other_core_ingress(self):
        item = create_inbound(self.db, payload())
        self.activate(relay=True)
        self.db.add(database.Inbound(core='xray', protocol='vless', port=18444,
                                     enable=True, settings={}, stream_settings={}, tag='external'))
        self.db.commit()  # Model an out-of-band DB edit, never a supported API.
        old = self.runtime.state()
        with self.assertRaises(CoreError):
            self.manager.apply('sing-box', [item])
        self.assertEqual(self.runtime.state(), old)
        self.assertTrue(self.manager.status()['sing-box']['dirty'])

    def test_database_failure_rolls_back_and_never_touches_runtime(self):
        create_inbound(self.db, payload())
        self.activate(relay=True)
        calls = len(self.runtime.calls)
        with patch.object(self.db, 'commit', side_effect=RuntimeError('fixture DB failure')):
            with self.assertRaises(RuntimeError):
                create_inbound(self.db, payload(18444))
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(len(self.runtime.calls), calls)

    def test_saved_runtime_failure_restores_active_policy_and_reports_saved_only(self):
        from app.api import cores
        item = create_inbound(self.db, payload())
        self.activate(relay=True)
        old = self.runtime.state()
        update_inbound(self.db, item.id, {'port': 18555})
        self.runtime.fail_next = True
        with patch.object(cores, 'core_manager', self.manager):
            with self.assertRaises(HTTPException) as caught:
                cores.apply_checked('sing-box')
        self.assertTrue(caught.exception.detail['saved'])
        self.assertFalse(caught.exception.detail['applied'])
        self.assertEqual(self.runtime.state(), old)
        self.assertEqual(self.rows()[0].port, 18555)
        self.assertIsNone(self.store.recoverable_pending())
        self.assertTrue(self.store.load_active()['settings']['relay_enabled'])

    def test_saved_node_capacity_exhaustion_is_conflict_and_keeps_old_runtime(self):
        from app.api import cores
        from test_proxy_store import near_full_history
        item = create_inbound(self.db, payload())
        self.activate(relay=True)
        old = self.runtime.state()
        update_inbound(self.db, item.id, {'port': 18555})
        before = near_full_history(self.store)
        with patch.object(cores, 'core_manager', self.manager):
            with self.assertRaises(HTTPException) as caught:
                cores.apply_checked('sing-box')
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail['code'], 'PROXY_CAPACITY_EXHAUSTED')
        self.assertTrue(caught.exception.detail['saved'])
        self.assertFalse(caught.exception.detail['applied'])
        self.assertEqual(self.runtime.state(), old)
        self.assertTrue(self.runtime.running)
        self.assertEqual(self.rows()[0].port, 18555)
        self.assertEqual((self.store.path / 'state.json').read_bytes(), before)
        self.assertIsNone(self.store.recoverable_pending())

    def test_validate_only_and_manual_stop_preserve_committed_proxy_strategy(self):
        create_inbound(self.db, payload())
        self.activate(local=True, relay=True)
        old = self.store.load_active()
        self.manager.stop('sing-box')
        self.assertFalse(self.runtime.running)
        checked = self.manager.apply_database('sing-box', activate=False)
        self.assertFalse(checked['applied'])
        self.assertFalse(self.runtime.running)
        self.assertEqual(self.store.load_active(), old)
        self.manager.apply_database('sing-box')
        self.assertTrue(self.runtime.running)
        self.assertTrue(self.store.load_active()['settings']['relay_enabled'])

    def test_certificate_reload_uses_committed_relay_and_keeps_credentials(self):
        from app.certificates.manager import CertificateManager
        from app.certificates.models import CertificateBinding
        database.Base.metadata.create_all(self.engine)
        item = create_inbound(self.db, payload())
        self.activate(relay=True)
        old_active = self.store.load_active()['revision']
        self.draft(relay=False)
        target = 'inbound:' + str(item.id)
        self.db.add(CertificateBinding(target=target, certificate_id='a' * 32))
        self.db.commit()
        manager = CertificateManager(Path(self.temp.name) / 'certificates')
        paths = (Path('/fixture/renewed-certificate.pem'), Path('/fixture/renewed-key.pem'))
        with patch.object(manager, 'material', return_value=(paths, 'ingress.example.test', 'b' * 64)):
            manager._apply_target(target, explicit=True)
        server = self.runtime.config['inbounds'][0]
        self.assertEqual(server['tag'], 'vui-relay-ingress-' + str(item.id))
        self.assertEqual(server['tls']['certificate_path'], str(paths[0]))
        self.assertEqual(server['users'], [{'uuid': UUID}])
        self.assertEqual(self.store.load_active()['revision'], old_active)
        self.assertTrue(self.store.load_active()['settings']['relay_enabled'])
        self.assertEqual(len(self.resolutions), 1)

    def test_certificate_prospective_guard_rejects_other_core_before_db_commit(self):
        from app.certificates.manager import CertificateManager
        from app.certificates.material import CertificateError
        from app.certificates.models import CertificateBinding
        database.Base.metadata.create_all(self.engine)
        item = create_inbound(self.db, payload())
        self.activate(relay=True)
        target = 'inbound:' + str(item.id)
        self.db.add(CertificateBinding(target=target, certificate_id='a' * 32))
        self.db.add(database.Inbound(core='xray', protocol='vless', port=18444,
                                     enable=True, settings={}, stream_settings={}, tag='external'))
        self.db.commit()
        old = self.runtime.state()
        manager = CertificateManager(Path(self.temp.name) / 'certificates')
        paths = (Path('/fixture/rejected-certificate.pem'), Path('/fixture/rejected-key.pem'))
        with patch.object(manager, 'material', return_value=(paths, 'ingress.example.test', 'b' * 64)):
            with self.assertRaises(CertificateError):
                manager._apply_target(target, explicit=True)
        self.db.expire_all()
        self.assertEqual(self.db.get(database.Inbound, item.id).stream_settings['tls']['certificate_path'],
                         '/fixture/certificate.pem')
        self.assertEqual(self.runtime.state(), old)

    def test_corrupt_proxy_state_prevents_both_startups(self):
        self.store.read_snapshot()
        (self.store.path / 'state.json').write_text('{}')
        self.manager.recover_all()
        self.assertNotIn(('recover',), self.runtime.calls)
        self.assertNotIn(('recover',), self.xray.calls)
        self.assertIn(('stop', False), self.xray.calls)
        self.assertIn('startup blocked', self.xray.last_error)

    def test_corrupt_storage_in_check_or_apply_gate_stops_both_without_replacing_evidence(self):
        initial = self.store.read_snapshot()
        state_file = self.store.path / 'state.json'
        state_file.write_text('{}')
        for operation in (lambda: self.manager.check_proxy(initial['revision']),
                          lambda: self.manager.apply_proxy(initial['revision'], 'corrupt-gate')):
            self.runtime.running = self.xray.running = True
            with self.assertRaises(ProxyStorageError):
                operation()
            self.assertFalse(self.runtime.running)
            self.assertFalse(self.xray.running)
            self.assertEqual(state_file.read_text(), '{}')

    def test_first_apply_pending_blocks_ordinary_core_apply_and_mutation(self):
        draft = self.draft(relay=True)
        self.store.begin_apply(draft['revision'], 'pending-first')
        with self.assertRaises(CoreError):
            self.manager.apply_database('sing-box')
        with self.assertRaises(HTTPException):
            create_inbound(self.db, payload())
        self.assertFalse(self.runtime.calls)
        self.assertEqual(self.rows(), [])

    def test_stale_enabled_xray_runtime_blocks_relay_and_never_auto_recovers(self):
        self.xray.apply({'inbounds': [{'port': 18444}]})
        draft = self.draft(relay=True)
        with self.assertRaisesRegex(CoreError, 'remaining Xray'):
            self.manager.apply_proxy(draft['revision'], 'blocked-xray')
        self.assertIsNone(self.store.load_active())
        self.manager.stop('xray')
        self.manager.apply_proxy(draft['revision'], 'relay-after-stop')
        self.xray.committed['enabled'] = True  # Damaged/out-of-date runtime evidence.
        self.xray.calls.clear()
        self.manager.recover_all()
        self.assertNotIn(('recover',), self.xray.calls)
        self.assertFalse(self.xray.running)

    def test_modern_and_legacy_http_mutations_share_relay_gate(self):
        from fastapi.testclient import TestClient
        from app.api import cores
        import main
        create_inbound(self.db, payload())
        self.activate(relay=True)
        headers = {'Origin': 'https://panel.example.test', 'X-VUI-Request': '1'}
        with patch('app.middleware.auth.configured_origin', return_value=headers['Origin']), \
             patch('app.middleware.auth.authenticate', return_value={'id': 1}), \
             patch.object(cores, 'core_manager', self.manager):
            client = TestClient(main.app, base_url=headers['Origin'])
            for path, data in (
                ('/api/inbounds', {'core': 'xray', 'protocol': 'vless', 'port': 18444}),
                ('/api/xray/inbounds', {'protocol': 'vmess', 'port': 18445}),
                ('/api/singbox/inbounds', {'protocol': 'vmess', 'port': 18446}),
            ):
                with self.subTest(path=path):
                    response = client.post(path, json=data, headers=headers)
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertFalse(response.json()['detail']['saved'])
                    self.assertNotIn(UUID, response.text)
            self.assertEqual(len(self.rows()), 1)

    def test_check_shares_runtime_gate_but_completed_apply_replay_ignores_newer_draft(self):
        self.xray.apply({'inbounds': [{'port': 18444}]})
        applied = self.activate(local=True)
        old_revision = self.store.load_active()['revision']
        newer = self.draft(relay=True)
        with self.assertRaisesRegex(CoreError, 'remaining Xray'):
            self.manager.check_proxy(newer['revision'])
        calls = len(self.runtime.calls)
        replayed = self.manager.apply_proxy(old_revision, applied['transaction_id'])
        self.assertTrue(replayed['already_completed'])
        self.assertTrue(replayed['applied'])
        self.assertEqual(len(self.runtime.calls), calls)
        self.assertEqual(self.store.load_active()['revision'], old_revision)

    def test_waiting_edit_refreshes_preloaded_orm_identity_under_lock(self):
        item = create_inbound(self.db, payload())
        original_port = item.port
        with self.Session() as other:
            row = other.get(database.Inbound, item.id)
            row.port = original_port + 10
            other.commit()
        self.assertEqual(item.port, original_port)
        updated = update_inbound(self.db, item.id, {'remark': 'new remark'})
        self.assertEqual(updated.port, original_port + 10)

    def test_proxy_apply_and_inbound_commit_share_one_operation_lock(self):
        draft = self.draft(relay=True)
        entered, release, attempted, finished = (threading.Event() for _ in range(4))
        errors = []
        original = self.coordinator.apply
        def held_apply(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError('fixture release was not signalled')
            return original(*args, **kwargs)
        def activate():
            try:
                self.manager.apply_proxy(draft['revision'], 'concurrent-apply')
            except Exception as exc:
                errors.append(exc)
        def mutate():
            attempted.set()
            try:
                with self.Session() as db:
                    create_inbound(db, {'core': 'xray', 'protocol': 'vless', 'port': 18444})
            except HTTPException as exc:
                if exc.status_code != 409 or exc.detail['saved']:
                    errors.append(exc)
            except Exception as exc:
                errors.append(exc)
            else:
                errors.append(AssertionError('unqualified row was saved'))
            finally:
                finished.set()
        with patch.object(self.coordinator, 'apply', side_effect=held_apply):
            application = threading.Thread(target=activate)
            mutation = threading.Thread(target=mutate)
            application.start()
            self.assertTrue(entered.wait(3))
            mutation.start()
            try:
                self.assertTrue(attempted.wait(3))
                self.assertFalse(finished.wait(0.05))
            finally:
                release.set()
                application.join(3)
                mutation.join(3)
        self.assertFalse(application.is_alive())
        self.assertFalse(mutation.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.rows(), [])
        self.assertTrue(self.store.load_active()['settings']['relay_enabled'])

    def test_prospective_guard_and_commit_share_manager_lock(self):
        original = self.manager.validate_inbound_change
        checked = []
        def guard(rows):
            self.assertTrue(self.manager.lock._is_owned())
            checked.append([row.id for row in rows])
            return original(rows)
        with patch.object(self.manager, 'validate_inbound_change', side_effect=guard), \
             patch.object(self.db, 'commit', wraps=self.db.commit) as commit:
            item = create_inbound(self.db, payload())
            update_inbound(self.db, item.id, {'remark': 'changed'})
            delete_inbound(self.db, item.id)
        self.assertEqual(checked, [[item.id], [item.id], []])
        self.assertEqual(commit.call_count, 3)


class RuntimeRelayBoundaryTests(unittest.TestCase):
    @staticmethod
    def connection(ip, port, kind=socket.SOCK_STREAM):
        return SimpleNamespace(laddr=SimpleNamespace(ip=ip, port=port), type=kind,
                               status='LISTEN' if kind == socket.SOCK_STREAM else 'NONE')

    def test_loopback_readiness_rejects_wildcards_wrong_address_and_udp_only(self):
        config = {'inbounds': [{'type': 'mixed', 'listen': '127.0.0.1', 'listen_port': 17891}]}
        exact = self.connection('127.0.0.1', 17891)
        self.assertTrue(CoreRuntime._listeners_ready(config, [exact]))
        for wrong in (self.connection('0.0.0.0', 17891), self.connection('::', 17891),
                      self.connection('127.0.0.2', 17891), self.connection('127.0.0.1', 17891, socket.SOCK_DGRAM)):
            with self.subTest(wrong=wrong):
                self.assertFalse(CoreRuntime._listeners_ready(config, [wrong]))
        self.assertFalse(CoreRuntime._listeners_ready(config, [exact, self.connection('0.0.0.0', 17891)]))

    def test_explicit_ingress_address_and_quic_transport_are_checked(self):
        config = {'inbounds': [{'type': 'tuic', 'listen': '::', 'listen_port': 18443}]}
        self.assertTrue(CoreRuntime._listeners_ready(config, [self.connection('::', 18443, socket.SOCK_DGRAM)]))
        self.assertFalse(CoreRuntime._listeners_ready(config, [self.connection('::', 18443)]))
        self.assertFalse(CoreRuntime._listeners_ready(config, [self.connection('127.0.0.1', 18443, socket.SOCK_DGRAM)]))

    def test_restore_checks_hash_and_never_launches_candidate_on_failure(self):
        with tempfile.TemporaryDirectory(prefix='vui-restore-') as temp:
            runtime = CoreRuntime('sing-box', Path(temp) / 'unused', Path(temp) / 'runtime')
            self.addCleanup(runtime.close)
            runtime._claim()
            old, candidate = {'inbounds': [{'listen_port': 18000}]}, {'inbounds': [{'listen_port': 18001}]}
            old_revision, candidate_revision = revision(old), revision(candidate)
            atomic_write(runtime.root / (old_revision + '.json'), encode(old))
            atomic_write(runtime.root / (candidate_revision + '.json'), encode(candidate))
            atomic_write(runtime.state_file, encode({'revision': candidate_revision, 'enabled': True}))
            with patch.object(runtime, 'check') as check, patch.object(runtime, '_launch', side_effect=CoreError('failure')) as launch, \
                 patch.object(runtime, '_terminate') as terminate:
                with self.assertRaises(CoreError):
                    runtime.restore(old_revision, enabled=True)
                check.assert_called_once_with(runtime.root / (old_revision + '.json'))
                launch.assert_called_once_with(runtime.root / (old_revision + '.json'))
                self.assertGreaterEqual(terminate.call_count, 1)
            self.assertEqual(runtime.state()['revision'], candidate_revision)
            self.assertEqual((runtime.root / (old_revision + '.json')).read_bytes(), encode(old))
            with patch.object(runtime, 'check'), patch.object(runtime, '_launch') as launch:
                runtime.restore(old_revision, enabled=False)
                launch.assert_not_called()
            self.assertEqual(runtime.state(), {'revision': old_revision, 'enabled': False})
            (runtime.root / (old_revision + '.json')).write_text('{}')
            with patch.object(runtime, 'check') as check, patch.object(runtime, '_launch') as launch:
                with self.assertRaises(CoreError):
                    runtime.restore(old_revision, enabled=True)
                check.assert_not_called()
                launch.assert_not_called()

    def test_cancelled_restore_also_terminates_and_retains_evidence(self):
        with tempfile.TemporaryDirectory(prefix='vui-restore-cancel-') as temp:
            runtime = CoreRuntime('sing-box', Path(temp) / 'unused', Path(temp) / 'runtime')
            self.addCleanup(runtime.close)
            runtime._claim()
            config = {'inbounds': [{'listen_port': 18000}]}
            saved = revision(config)
            atomic_write(runtime.root / (saved + '.json'), encode(config))
            atomic_write(runtime.state_file, encode({'revision': saved, 'enabled': True}))
            with patch.object(runtime, 'check'), patch.object(runtime, '_launch', side_effect=KeyboardInterrupt), \
                 patch.object(runtime, '_terminate') as terminate:
                with self.assertRaises(KeyboardInterrupt):
                    runtime.restore(saved, enabled=True)
                self.assertEqual(terminate.call_count, 2)
            self.assertEqual(runtime.state()['revision'], saved)

    def test_restore_no_prior_revision_stops_before_removing_state(self):
        with tempfile.TemporaryDirectory(prefix='vui-restore-empty-') as temp:
            runtime = CoreRuntime('sing-box', Path(temp) / 'unused', Path(temp) / 'runtime')
            self.addCleanup(runtime.close)
            runtime._claim()
            config = {'inbounds': []}
            atomic_write(runtime.root / (revision(config) + '.json'), encode(config))
            atomic_write(runtime.state_file, encode({'revision': revision(config), 'enabled': False}))
            with patch.object(runtime, '_terminate') as terminate:
                runtime.restore(None, enabled=False)
                terminate.assert_called_once()
            self.assertEqual(runtime.state(), {'revision': None, 'enabled': False})
            self.assertTrue((runtime.root / (revision(config) + '.json')).is_file())


if __name__ == '__main__':
    unittest.main()
