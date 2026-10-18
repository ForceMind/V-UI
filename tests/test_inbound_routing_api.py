"""Real HTTP/SQLite routing metadata; runtime calls here are deliberate stubs."""
from contextlib import ExitStack
from copy import deepcopy
import unittest
from unittest.mock import patch

from app.api import inbounds, singbox, xray
from app.models import database
from app.services import core_manager as managers
from app.services.inbound_service import routing_dicts
import test_auth as auth

UUID = '11111111-1111-4111-8111-111111111111'


class InboundRoutingApiTests(unittest.TestCase):
    def setUp(self):
        auth.AuthenticationTests.setUp(self)
        self.assertEqual(auth.AuthenticationTests.login(self).status_code, 200)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module in (inbounds, singbox, xray):
            self.stack.enter_context(patch.object(module, 'apply_checked',
                return_value={'applied': True, 'valid': True}))
        self.validate = self.stack.enter_context(patch.object(managers.core_manager,
            'validate_inbound_change', return_value=None))
        self.stack.enter_context(patch.object(managers.core_manager,
            'inbound_routing_status', return_value={}))
        self.port = 31000

    def create(self, *, mode='inherit', legacy=None, omit=False):
        self.port += 1
        core = 'xray' if legacy == 'xray' else 'sing-box'
        payload = {'core': core, 'protocol': 'vless', 'port': self.port, 'enable': False,
            'settings': {'users': [{('id' if core == 'xray' else 'uuid'): UUID}]},
            'stream_settings': {}, 'remark': 'routing-fixture'}
        if not omit:
            payload['routing_mode'] = mode
        path = '/api/inbounds' if legacy is None else '/api/' + legacy + '/inbounds'
        if legacy:
            payload.pop('core')
        response = self.client.post(path, headers=auth.HEADERS, json=payload)
        return path, payload, response

    def test_create_default_and_all_modes_persist_without_credentials_in_response(self):
        for legacy in (None, 'singbox', 'xray'):
            for mode in ('inherit', 'direct', 'relay'):
                with self.subTest(legacy=legacy, mode=mode):
                    _, _, response = self.create(mode=mode, legacy=legacy, omit=mode == 'inherit')
                    self.assertEqual(response.status_code, 200, response.text)
                    value = response.json()['inbound']
                    self.assertEqual(value['routing_mode'], mode)
                    self.assertNotIn(UUID, response.text)
                    self.assertNotIn('settings', value)
                    with database.SessionLocal() as db:
                        self.assertEqual(db.get(database.Inbound, value['id']).routing_mode, mode)

    def test_legacy_and_modern_update_omission_preserves_existing_mode(self):
        for legacy in (None, 'singbox', 'xray'):
            path, payload, created = self.create(mode='relay', legacy=legacy)
            self.assertEqual(created.status_code, 200, created.text)
            identity = created.json()['inbound']['id']
            changed = {'remark': 'still-relay'} if legacy is None else {**payload, 'remark': 'still-relay'}
            changed.pop('routing_mode', None)
            response = self.client.put(f'{path}/{identity}', headers=auth.HEADERS, json=changed)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['inbound']['routing_mode'], 'relay')
            with database.SessionLocal() as db:
                row = db.get(database.Inbound, identity)
                self.assertEqual(row.routing_mode, 'relay')
                self.assertIn(UUID, str(row.settings))

    def test_explicit_null_and_invalid_values_reject_without_rows_or_runtime_calls(self):
        for legacy in (None, 'singbox', 'xray'):
            for mode in (None, '', 'Relay', True, 1, [], {}, 'invalid'):
                with self.subTest(legacy=legacy, mode=mode):
                    with database.SessionLocal() as db:
                        before = db.query(database.Inbound).count()
                    calls = self.validate.call_count
                    _, _, response = self.create(mode=mode, legacy=legacy)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(self.validate.call_count, calls)
                    with database.SessionLocal() as db:
                        self.assertEqual(db.query(database.Inbound).count(), before)

    def test_update_null_rejects_and_editor_returns_only_saved_mode(self):
        path, payload, created = self.create(mode='direct')
        identity = created.json()['inbound']['id']
        response = self.client.put(f'{path}/{identity}', headers=auth.HEADERS,
                                  json={'routing_mode': None})
        self.assertEqual(response.status_code, 422)
        editor = self.client.get(f'/api/inbounds/{identity}/editor')
        self.assertEqual(editor.status_code, 200, editor.text)
        self.assertEqual(editor.json()['routing_mode'], 'direct')
        self.assertNotIn(UUID, editor.text)

    def test_runtime_read_failure_is_unknown_not_desired_applied_claim(self):
        _, _, created = self.create(mode='relay')
        identity = created.json()['inbound']['id']
        with patch.object(managers.core_manager, 'inbound_routing_status',
                          side_effect=RuntimeError('private-not-for-response')):
            for path in ('/api/inbounds', '/api/singbox/inbounds'):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn('private-not-for-response', response.text)
                value = next(v for v in response.json() if v['id'] == identity)
                self.assertEqual(value['routing_status'], {'desired_effective_mode': None,
                    'applied_effective_mode': None, 'state': 'unknown', 'policy_identity': None})

    def test_batch_refreshes_orm_under_same_lock_as_routing_observation(self):
        _, _, created = self.create(mode='direct')
        identity = created.json()['inbound']['id']
        with database.SessionLocal() as stale:
            old = stale.get(database.Inbound, identity)
            with database.SessionLocal() as writer:
                writer.get(database.Inbound, identity).routing_mode = 'relay'
                writer.commit()
            self.assertEqual(old.routing_mode, 'direct')
            seen = []
            def observe(rows):
                self.assertTrue(managers.core_manager.lock._is_owned())
                selected = next(row for row in rows if row.id == identity)
                seen.append(selected.routing_mode)
                return {identity: {'desired_effective_mode': 'relay',
                    'applied_effective_mode': None, 'state': 'pending', 'policy_identity': None}}
            with patch.object(managers.core_manager, 'inbound_routing_status', side_effect=observe):
                values = routing_dicts(stale)
            self.assertEqual(seen, ['relay'])
            self.assertEqual(values[0]['routing_mode'], 'relay')
            self.assertEqual(values[0]['routing_status']['state'], 'pending')


if __name__ == '__main__':
    unittest.main()
