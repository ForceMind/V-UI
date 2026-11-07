"""No-network backend contracts for the simplified operations (run in Actions)."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.local_connection import LocalConnectionExporter, LocalConnectionError
from app.services.inbound_instance_revision import content_revision, mutation_revision
from app.services.proxy_store import ProxyConflict
from app.services.core_manager import CoreManager
from tests.test_proxy_exports import ExportFixture
from tests.test_proxy_store import FAKE_PASSWORD, FAKE_UUID_2, writable_settings


class SimpleOperationStateTests(ExportFixture, unittest.TestCase):
    def setUp(self):
        self.setup_export()
        values = writable_settings(self.saved)
        values['local_proxy_enabled'] = True
        self.saved = self.store.save_draft(values, self.rev)
        self.rev = self.saved['revision']
        self.commit()

    def test_discard_restores_exact_active_secret_without_erasing_draft_history(self):
        active = self.store.load_active()
        changed = self.store.save_draft(writable_settings(self.saved), self.rev,
                                        local_password='new-draft-secret-only-12345')
        self.assertNotEqual(changed['revision'], self.rev)
        calls = list(self.runtime.calls)
        restored = self.store.discard_draft(changed['revision'], self.rev)
        self.assertEqual(restored['revision'], self.rev)
        self.assertEqual(self.store.load_draft()['local_password'], FAKE_PASSWORD)
        self.assertEqual(self.store.load_active(), active)
        self.assertEqual(self.runtime.calls, calls)
        with self.assertRaises(ProxyConflict):
            self.store.discard_draft(changed['revision'], self.rev)

    def test_discard_conflict_and_pending_do_not_change_state(self):
        before = self.store.read_snapshot()
        for draft, active in [('f'*64, self.rev), (self.rev, 'f'*64)]:
            with self.assertRaises(ProxyConflict):
                self.store.discard_draft(draft, active)
            self.assertEqual(self.store.read_snapshot(), before)
        self.store.begin_apply(self.rev, 'simple-pending')
        with self.assertRaises(ProxyConflict):
            self.store.discard_draft(self.rev, self.rev)

    def test_local_delivery_is_active_only_redacted_elsewhere_and_loopback_only(self):
        exporter = LocalConnectionExporter(self.manager)
        artifact = exporter.connection(self.rev)
        value = artifact.delivery()
        self.assertEqual(value['host'], '127.0.0.1')
        self.assertFalse(value['system_proxy'])
        self.assertEqual(value['password'], FAKE_PASSWORD)
        self.assertNotIn(FAKE_PASSWORD, repr(artifact))
        self.assertNotIn(FAKE_UUID_2, str(value))
        self.assertNotIn(FAKE_PASSWORD, str(self.store.read_snapshot()))
        draft = self.store.save_draft(writable_settings(self.saved), self.rev,
                                     local_password='different-draft-secret-123')
        self.assertEqual(exporter.connection(self.rev).delivery()['password'], FAKE_PASSWORD)
        with self.assertRaises(ProxyConflict):
            exporter.connection(draft['revision'])
        self.runtime.stop()
        with self.assertRaises(LocalConnectionError):
            exporter.connection(self.rev)

    def test_local_delivery_blocks_uncommitted_tampered_and_pending_runtime(self):
        exporter = LocalConnectionExporter(self.manager)
        config = self.runtime.saved[self.runtime.record['revision']]
        config['log'] = {'level':'trace'}
        with self.assertRaises(LocalConnectionError):
            exporter.connection(self.rev)
        self.runtime.saved[self.runtime.record['revision']] = copy.deepcopy(
            self.coordinator.compile_active(self.rows).config)
        self.store.begin_apply(self.rev, 'simple-pending')
        with self.assertRaises(ProxyConflict):
            exporter.connection(self.rev)

    def manager_for_check(self):
        manager = CoreManager.__new__(CoreManager)
        manager.lock = self.lock
        manager.adapters = {'sing-box': SimpleNamespace(runtime=self.runtime),
                            'xray': SimpleNamespace(runtime=self.xray)}
        manager._proxy_coordinator = self.coordinator
        manager._all_rows = lambda: self.rows
        return manager

    def test_manual_stop_between_check_and_apply_is_not_silently_undone(self):
        manager = self.manager_for_check()
        with patch.object(manager, '_check_proxy_runtime_gate'):
            checked = manager.check_proxy(self.rev)
            self.runtime.stop()
            calls = list(self.runtime.calls)
            with self.assertRaises(ProxyConflict):
                manager.apply_proxy(self.rev, 'simple-new-attempt',
                    expected_runtime_fingerprint=checked['runtime_fingerprint'])
            self.assertEqual(self.runtime.calls, calls)
            self.assertFalse(self.runtime.running)
            # Replay of a committed ID only reads its result, never starts it.
            manager.apply_proxy(self.rev, 'test-export-commit',
                expected_runtime_fingerprint=checked['runtime_fingerprint'])
            self.assertEqual(self.runtime.calls, calls)
            self.assertFalse(self.runtime.running)


class ContentConditionTests(unittest.TestCase):
    def test_content_condition_detects_same_instance_edits_but_ignores_traffic(self):
        node = SimpleNamespace(id=1, instance_id='a'*32, settings={'uuid':'fake-uuid'},
                               stream_settings={'key_path':'/fake/key'}, port=10443,
                               remark='example', enable=True)
        original, identity = content_revision(node), mutation_revision(node)
        self.assertEqual(len(original), 64)
        for field, value in [('port',11443), ('remark','renamed'), ('enable',False),
                             ('settings',{'uuid':'changed-fake'}), ('stream_settings',{'key_path':'/other'})]:
            changed = copy.deepcopy(node)
            setattr(changed, field, value)
            self.assertNotEqual(content_revision(changed), original)
            self.assertEqual(mutation_revision(changed), identity)
        node.up, node.down = 123, 456
        self.assertEqual(content_revision(node), original)
        node.instance_id = None
        self.assertIsNone(content_revision(node))


class LocalConnectionApiTests(unittest.TestCase):
    def setUp(self):
        from tests.test_proxy_exports import StrictExportApiTests
        StrictExportApiTests.setUp(self)
        from app.api import proxy_exports
        from app.services.local_connection import LocalConnection
        self.local = self.enterContext(patch.object(proxy_exports, 'get_local_exporter')).return_value
        self.local.connection.return_value = LocalConnection(7891, 'test-local', FAKE_PASSWORD, 'a'*64)

    def post(self, body=None, headers=None, query=''):
        from tests.test_proxy_exports import HEADERS
        return self.client.post('/api/proxy/local-connection'+query,
            json={'base_revision':'a'*64} if body is None else body,
            headers=HEADERS if headers is None else headers)

    def test_explicit_authorized_post_only_and_never_cache(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['password'], FAKE_PASSWORD)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['referrer-policy'], 'no-referrer')
        self.assertEqual(self.client.get('/api/proxy/local-connection').status_code, 405)
        self.local.reset_mock()
        for body in ({'base_revision':None}, {'base_revision':'a'*64,'password':'injected'}, {'base_revision':True}):
            self.assertEqual(self.post(body).status_code, 422)
        self.assertEqual(self.post(query='?raw=1').status_code, 422)
        self.local.connection.assert_not_called()
        self.assertEqual(self.post(headers={}).status_code, 403)
        self.auth.return_value = None
        self.assertEqual(self.post().status_code, 401)


class NodeContentGuardTests(unittest.TestCase):
    # Reuse the isolated disk fixture only, not its unrelated test methods.
    from tests.test_inbound_instance_preconditions import InboundInstancePreconditionTests as _Fixture
    setUp = _Fixture.setUp
    request = _Fixture.request
    create = _Fixture.create
    row = _Fixture.row
    editor = _Fixture.editor

    def test_same_instance_concurrent_credentials_edit_blocks_stale_basic_save(self):
        from app.models import database
        node, _ = self.create()
        shown = self.editor(node)
        with database.SessionLocal() as db:
            current = db.get(database.Inbound, node)
            current.settings = {'users':[{'uuid':'22222222-2222-4222-8222-222222222222'}]}
            db.commit()
        before = self.row(node)
        self.apply.reset_mock()
        status, response = self.request('PUT', node, {
            'remark':'stale rename', 'expected_instance_revision':shown['mutation_revision'],
            'expected_content_revision':shown['content_revision']})
        self.assertEqual(status, 409, response)
        self.assertEqual(self.row(node), before)
        self.apply.assert_not_called()
        updated = self.editor(node)
        status, response = self.request('PUT', node, {
            'remark':'new rename', 'expected_instance_revision':updated['mutation_revision'],
            'expected_content_revision':updated['content_revision']})
        self.assertEqual(status, 200, response)
        self.assertEqual(self.row(node)['settings'], before['settings'])
