"""Real HTTP/SQLite and durable store; fake runtime/DNS are explicit seams."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app.api import proxy as proxy_api
from app.models import database
from app.services.core_manager import core_manager
from app.services.proxy_apply import ProxyApplyCoordinator
from app.services.proxy_policy import VerifiedEndpoint
from app.services.proxy_store import ProxyStorageError, ProxyStore
from tests.test_proxy_apply import FakeRuntime
from tests.test_proxy_store import profile, writable_settings
import test_auth as auth

UUID = '11111111-1111-4111-8111-111111111111'


class InboundRoutingTransactionApiTests(unittest.TestCase):
    def setUp(self):
        auth.AuthenticationTests.setUp(self)
        self.assertEqual(auth.AuthenticationTests.login(self).status_code, 200)
        self.store = ProxyStore(Path(self.temp.name) / 'private')
        self.runtime, self.xray = FakeRuntime(), FakeRuntime()
        self.runtime.active_config = lambda: copy.deepcopy(self.runtime.saved.get(self.runtime.record['revision']))
        self.resolutions = []
        def resolve(pid, rev, value, **kwargs):
            self.resolutions.append((pid, rev))
            return VerifiedEndpoint(pid, rev, value['server'], ('8.8.4.4',), 1)
        self.coordinator = ProxyApplyCoordinator(self.store, self.runtime,
            core_manager.get('sing-box').build_config, core_manager.lock,
            rows_provider=core_manager._all_rows, preflight=lambda *_: None,
            resolver=resolve, address_provider=lambda _: ('127.0.0.1',))
        patch.object(core_manager, '_proxy_coordinator', self.coordinator).start()
        patch.object(core_manager.get('sing-box'), '_runtime', self.runtime).start()
        patch.object(core_manager.get('xray'), '_runtime', self.xray).start()
        patch.object(proxy_api, 'get_store', return_value=self.store).start()
        self.sequence = 0
        # Restore adapters before TestClient lifespan shutdown closes runtimes.
        self.addCleanup(patch.stopall)

    def payload(self, mode='direct', enabled=True):
        self.sequence += 1
        return {'core': 'sing-box', 'protocol': 'vless', 'port': 32000 + self.sequence,
            'routing_mode': mode, 'enable': enabled, 'remark': 'actual API mode',
            'settings': {'users': [{'uuid': UUID}]},
            'stream_settings': {'tls': {'enabled': True, 'server_name': 'node.example',
                'certificate_path': '/synthetic/cert.pem', 'key_path': '/synthetic/key.pem'}}}

    def post(self, path, body, **headers):
        if "Idempotency-Key" in headers:
            headers["Idempotency-Key"] = hashlib.sha256(headers["Idempotency-Key"].encode()).hexdigest()
        return self.client.post(path, headers={**auth.HEADERS, **headers}, json=body)

    def applied(self, key='apply'):
        revision = self.store.read_snapshot()['revision']
        check = self.post('/api/proxy/check', {'base_revision': revision})
        self.assertEqual(check.status_code, 200, check.text)
        body = {'base_revision': revision, 'source_fingerprint': check.json()['source_fingerprint']}
        result = self.post('/api/proxy/apply', body, **{'Idempotency-Key': key})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['applied'])
        return body

    def rows(self):
        with database.SessionLocal() as db:
            return [(v.id, v.routing_mode, v.enable) for v in db.query(database.Inbound).all()]

    def test_first_direct_without_any_upstream_is_saved_then_explicitly_applied(self):
        created = self.post('/api/inbounds', self.payload())
        self.assertEqual(created.status_code, 409, created.text)
        self.assertTrue(created.json()['detail']['saved'])
        self.assertFalse(created.json()['detail']['applied'])
        self.assertEqual(len(self.rows()), 1)
        self.assertFalse(self.runtime.running)
        self.assertIsNone(self.store.load_active())
        self.assertEqual(self.store.read_snapshot()['upstreams'], [])
        self.applied('first-direct')
        value = self.client.get('/api/inbounds').json()[0]
        self.assertEqual(value['routing_status']['state'], 'applied')
        self.assertEqual(value['routing_status']['applied_effective_mode'], 'direct')
        self.assertEqual(self.resolutions, [])
        self.assertNotIn(UUID, json.dumps(value))

    def test_first_enabled_relay_rejects_before_commit_then_disabled_draft_saves(self):
        result = self.post('/api/inbounds', self.payload('relay'))
        self.assertEqual(result.status_code, 409, result.text)
        self.assertIs(result.json()['detail']['saved'], False)
        self.assertEqual(self.rows(), [])
        draft = self.post('/api/inbounds', self.payload('relay', False))
        self.assertEqual(draft.status_code, 409, draft.text)
        self.assertIs(draft.json()['detail']['saved'], True)
        self.assertEqual(len(self.rows()), 1)
        self.assertIs(self.rows()[0][2], False)
        self.assertEqual(self.resolutions, [])
        self.assertEqual(self.runtime.calls, [])

    def test_first_relay_enable_uses_applied_selected_upstream_at_both_flags_off(self):
        added = self.store.save_upstream(profile(), self.store.read_snapshot()['revision'])
        settings = writable_settings(added['snapshot'])
        settings['selected_upstream_id'] = added['upstream_id']
        saved = self.store.save_draft(settings, added['snapshot']['revision'])
        self.applied('selected-only')
        self.assertIsNone(self.store.load_active()['runtime_context']['endpoint'])
        created = self.post('/api/inbounds', self.payload('relay'))
        self.assertEqual(created.status_code, 200, created.text)
        active = self.store.load_active()
        self.assertFalse(active['settings']['local_proxy_enabled'])
        self.assertFalse(active['settings']['relay_enabled'])
        self.assertEqual(active['revision'], saved['revision'])
        self.assertEqual(self.resolutions, [(added['upstream_id'], saved['revision'])])
        self.assertEqual(self.client.get('/api/inbounds').json()[0]['routing_status']['applied_effective_mode'], 'relay')

    def test_fence_write_failure_rolls_back_real_sql_insert(self):
        with patch.object(self.store, 'ensure_policy_v2', side_effect=ProxyStorageError('synthetic')):
            result = self.post('/api/inbounds', self.payload())
        self.assertEqual(result.status_code, 409, result.text)
        self.assertFalse(result.json()['detail']['saved'])
        self.assertEqual(self.rows(), [])
        self.assertFalse(self.runtime.running)

    def test_real_sql_commit_failure_keeps_fence_and_no_inbound(self):
        original = database.SessionLocal.class_.commit
        def fail_write(session):
            if session.info.get('fail_after_fence'):
                raise RuntimeError('synthetic SQL commit failure')
            return original(session)
        actual_validate = core_manager.validate_inbound_change
        def validated(rows):
            actual_validate(rows)
            # ORM rows belong to the request's real Session; SQL flush has
            # happened, but its transaction must still be rolled back.
            from sqlalchemy.orm import object_session
            object_session(rows[0]).info['fail_after_fence'] = True
        with patch.object(core_manager, 'validate_inbound_change', side_effect=validated), \
                patch.object(database.SessionLocal.class_, 'commit', fail_write):
            with self.assertRaisesRegex(RuntimeError, 'synthetic SQL commit failure'):
                self.post('/api/inbounds', self.payload())
        self.assertEqual(self.rows(), [])
        state = json.loads((self.store.path / 'state.json').read_text())
        self.assertEqual(state['schema_version'], 2)
        self.assertIsNone(self.store.load_active())
        self.assertFalse(self.runtime.running)

    def test_delete_commits_sql_but_runtime_failure_is_saved_not_applied(self):
        self.post('/api/inbounds', self.payload())
        self.applied('before-delete')
        identity = self.rows()[0][0]
        previous_state = copy.deepcopy(self.runtime.state())
        previous_config = self.runtime.active_config()
        previous_active = self.store.load_active()
        self.runtime.fail_apply = True
        result = self.client.delete(f'/api/inbounds/{identity}', headers=auth.HEADERS)
        self.assertEqual(result.status_code, 409, result.text)
        self.assertIs(result.json()['detail']['saved'], True)
        self.assertIs(result.json()['detail']['applied'], False)
        self.assertEqual(self.rows(), [])
        # A verified complete rollback is allowed: saved deletion must never
        # be presented as applied while the old immutable ingress still runs.
        self.assertTrue(self.runtime.running)
        self.assertEqual(self.runtime.state(), previous_state)
        self.assertEqual(self.runtime.active_config(), previous_config)
        self.assertEqual(self.store.load_active(), previous_active)
        self.assertIn(('restore', previous_state['revision']), self.runtime.calls)
        self.assertTrue(core_manager.status()['sing-box']['dirty'])
        self.assertEqual(self.client.get('/api/inbounds').json(), [])
        # A subsequent startup may not silently launch this mismatched source.
        from app.services.proxy_apply import ProxyApplyError
        self.runtime.fail_apply = False
        with self.assertRaises(ProxyApplyError):
            self.coordinator.recover()
        self.assertFalse(self.runtime.running)

    def test_saved_edit_after_manual_stop_never_restarts_until_explicit_restart(self):
        self.post('/api/inbounds', self.payload())
        self.applied('before-stop')
        identity = self.rows()[0][0]
        stopped = self.post('/api/cores/sing-box/stop', {})
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertFalse(self.runtime.running)
        changed = self.client.put(f'/api/inbounds/{identity}', headers=auth.HEADERS,
            json={'remark': 'saved while stopped'})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertFalse(self.runtime.running)
        status = self.client.get('/api/inbounds').json()[0]['routing_status']
        self.assertIsNone(status['applied_effective_mode'])
        self.assertNotEqual(status['state'], 'applied')
        restarted = self.post('/api/cores/sing-box/restart', {})
        self.assertEqual(restarted.status_code, 200, restarted.text)
        self.assertTrue(self.runtime.running)
        self.assertEqual(self.client.get('/api/inbounds').json()[0]['routing_status']['state'], 'applied')

    def test_changed_inbound_source_invalidates_checked_and_completed_apply_tokens(self):
        self.post('/api/inbounds', self.payload())
        body = self.applied('original-token')
        identity = self.rows()[0][0]
        changed = self.client.put(f'/api/inbounds/{identity}', headers=auth.HEADERS,
            json={'routing_mode': 'inherit'})
        self.assertEqual(changed.status_code, 200, changed.text)
        for token in ('original-token', 'new-stale-token'):
            response = self.post('/api/proxy/apply', body, **{'Idempotency-Key': token})
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()['detail'], 'PROXY_REVISION_CONFLICT')


if __name__ == '__main__':
    unittest.main()
