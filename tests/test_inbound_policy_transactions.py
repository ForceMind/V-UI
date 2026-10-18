"""Per-inbound commit domains: no real core, DNS, credentials or network."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services.core_manager import CoreManager
from app.services.core_runtime import CoreError
from app.services.inbound_policy import policy_plan
from app.services.proxy_apply import ProxyApplyError
from app.services.proxy_store import ProxyConflict, ProxyStorageError
from tests import test_proxy_apply as apply_fixtures
from tests.test_proxy_apply import FakeRuntime, SimulatedCrash, row
from tests.test_proxy_store import writable_settings


class InboundTransactionTests(unittest.TestCase):
    setUp = apply_fixtures.ProxyApplyTests.setUp
    resolver = apply_fixtures.ProxyApplyTests.resolver
    make_coordinator = apply_fixtures.ProxyApplyTests.make_coordinator
    draft = apply_fixtures.ProxyApplyTests.draft
    apply = apply_fixtures.ProxyApplyTests.apply

    def apply_modes(self, transaction='mode', local=False, relay=False):
        # Product precommit requires an active selected upstream before the
        # first enabled relay save; establish that old all-inherit OFF state.
        if self.store.load_active() is None and any(
                row.enable and row.routing_mode == 'relay' for row in self.rows):
            modes = [row.routing_mode for row in self.rows]
            try:
                for row in self.rows: row.routing_mode = 'inherit'
                self.apply(False, False, 'selected-before-' + transaction)
            finally:
                for row, mode in zip(self.rows, modes): row.routing_mode = mode
        # Simulate the product's validated SQL save and its prior fence.
        self.store.ensure_policy_v2()
        draft = self.draft(local, relay)
        checked = self.coordinator.check(draft['revision'])
        return self.coordinator.apply(draft['revision'], transaction,
            expected_source_fingerprint=checked['source_fingerprint'])

    def manager(self):
        manager = CoreManager()
        manager._proxy_coordinator = self.coordinator
        manager.lock = self.lock
        manager._all_rows = lambda: self.rows
        manager.get('sing-box')._runtime = self.runtime
        manager.get('xray')._runtime = FakeRuntime()
        self.runtime.active_config = lambda: copy.deepcopy(self.runtime.saved.get(self.runtime.record['revision']))
        return manager

    def state(self):
        return json.loads((self.store.path / 'state.json').read_text())

    def test_actual_a98_reader_rejects_durable_fence_before_any_row_commit(self):
        path = Path(__file__).parent / 'fixtures' / 'proxy_store_a98_reader.py'
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         '4d82965839f8621d9c20a6accbf1610b3b2d2fc478d94eaafdcd4316b9c72bf9')
        spec = importlib.util.spec_from_file_location('vui_a98_store_reader', path)
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        self.assertIsNone(old.ProxyStore(self.data).load_active())
        before = {p: p.read_bytes() for name in ('revisions', 'secrets')
                  for p in (self.store.path / name).glob('*.json')}
        self.rows[0].routing_mode = 'direct'
        self.manager().validate_inbound_change(self.rows)
        self.assertEqual(self.state()['schema_version'], 2)
        with self.assertRaises(old.ProxyStorageError):
            old.ProxyStore(self.data).load_active()
        # Emulate the independently failing SQL transaction: fence stays newer.
        self.rows[0].routing_mode = 'inherit'
        self.manager().validate_inbound_change(self.rows)
        self.assertEqual(self.state()['schema_version'], 2)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertIsNone(self.store.load_active())

    def test_fence_failure_refuses_precommit_without_applying_any_runtime(self):
        self.rows[0].routing_mode = 'direct'
        with patch.object(self.store, 'ensure_policy_v2', side_effect=ProxyStorageError('fixture')):
            with self.assertRaises(CoreError): self.manager().validate_inbound_change(self.rows)
        self.assertEqual(self.state()['schema_version'], 1)
        self.assertEqual(self.runtime.calls, [])

    def test_runtime_check_and_apply_reject_unfenced_noninherit_sql_source(self):
        self.rows[0].routing_mode = 'direct'
        draft = self.store.load_draft()
        fingerprint = policy_plan(self.rows, draft['settings']).source_fingerprint
        with self.assertRaises(ProxyApplyError): self.coordinator.check(draft['revision'])
        with self.assertRaises(ProxyApplyError):
            self.coordinator.apply(draft['revision'], 'unfenced', expected_source_fingerprint=fingerprint)
        self.assertEqual(self.state()['schema_version'], 1)
        self.assertFalse(self.runtime.running)

    def test_corrupt_saved_relay_stops_before_missing_active_or_idempotent_early_return(self):
        for fenced in (False, True):
            for operation in ('manager', 'reconcile', 'check', 'apply_existing',
                              'manager_stale_check', 'manager_stale_apply'):
                with self.subTest(fenced=fenced, operation=operation):
                    self.rows[0].routing_mode = 'inherit'
                    self.runtime.apply(self.coordinator.build_config(self.rows))
                    if fenced: self.store.ensure_policy_v2()
                    self.rows[0].routing_mode = 'relay'
                    snapshot = self.store.load_draft()
                    fingerprint = policy_plan(self.rows, snapshot['settings']).source_fingerprint
                    with patch.object(self.coordinator, '_existing', side_effect=AssertionError('early return')):
                        with self.assertRaises((CoreError, ProxyApplyError)):
                            if operation == 'manager': self.manager().apply_database('sing-box')
                            elif operation == 'reconcile': self.coordinator.reconcile()
                            elif operation == 'check': self.coordinator.check(snapshot['revision'])
                            elif operation == 'manager_stale_check': self.manager().check_proxy('0'*64)
                            elif operation == 'manager_stale_apply': self.manager().apply_proxy('0'*64, 'stale')
                            else: self.coordinator.apply(snapshot['revision'], 'old-aborted',
                                expected_source_fingerprint=fingerprint)
                    self.assertFalse(self.runtime.running)
                    self.assertEqual(self.state()['schema_version'], 2 if fenced else 1)
                    self.assertIsNone(self.store.load_active())
        self.assertEqual(self.resolutions, [])

    def test_no_active_enabled_relay_rejected_disabled_draft_allowed_without_dns(self):
        self.rows[0].routing_mode = 'relay'
        with self.assertRaises(ProxyApplyError): self.manager().validate_inbound_change(self.rows)
        self.assertEqual(self.state()['schema_version'], 1)
        self.rows[0].enable = False
        self.manager().validate_inbound_change(self.rows)
        self.assertEqual(self.state()['schema_version'], 2)
        self.assertEqual(self.resolutions, [])
        self.assertIsNone(self.store.load_active())

    def test_active_none_direct_save_is_unapplied_until_explicit_proxy_apply(self):
        self.rows[0].routing_mode = 'direct'
        manager = self.manager()
        manager.validate_inbound_change(self.rows)
        with self.assertRaises(ProxyApplyError): manager.apply_database('sing-box')
        self.assertFalse(self.runtime.running)
        self.assertEqual(manager.inbound_routing_status()[1]['state'], 'unknown')
        self.apply_modes()
        self.assertEqual(manager.inbound_routing_status()[1]['applied_effective_mode'], 'direct')
        self.assertEqual(self.resolutions, [])

    def test_first_relay_enable_precommit_is_pure_reconcile_pins_active_not_draft(self):
        self.apply(False, False, 'selected-only')
        active = self.store.load_active()
        self.assertIsNone(active['runtime_context']['endpoint'])
        self.rows[0].routing_mode = 'relay'
        self.manager().validate_inbound_change(self.rows)
        self.assertEqual(self.resolutions, [])
        # A newer draft credential/profile must not enter the active transaction.
        draft = self.draft(True, True)
        self.coordinator.reconcile(transaction_id='first-needed')
        result = self.store.load_active()
        self.assertEqual(len(self.resolutions), 1)
        self.assertEqual(self.resolutions[0][1], active['revision'])
        self.assertEqual(result['revision'], active['revision'])
        self.assertEqual(self.store.load_draft()['revision'], draft['revision'])
        self.assertFalse(result['settings']['relay_enabled'])
        self.assertTrue(result['runtime_context']['requires_upstream'])
        self.assertEqual(result['runtime_context']['schema_version'], 2)

    def test_status_and_recovery_never_resolve_first_pin(self):
        self.apply(False, False, 'selected-only')
        self.rows[0].routing_mode = 'relay'
        self.manager().validate_inbound_change(self.rows)
        self.assertTrue(self.manager().status()['sing-box']['dirty'])
        self.assertEqual(self.resolutions, [])
        with self.assertRaises(ProxyApplyError): self.coordinator.recover()
        self.assertEqual(self.resolutions, [])
        self.assertFalse(self.runtime.running)

    def test_missing_committed_relay_endpoint_is_corruption_not_fresh_pin(self):
        self.rows[0].routing_mode = 'relay'
        self.apply_modes()
        state = self.state()
        state['transactions'][state['active_transaction_id']]['runtime_context']['endpoint'] = None
        (self.store.path / 'state.json').write_text(json.dumps(state))
        before = list(self.resolutions)
        with self.assertRaises(ProxyApplyError): self.coordinator.reconcile()
        self.assertEqual(self.resolutions, before)
        self.assertFalse(self.runtime.running)

    def test_check_fingerprint_required_and_invalidated_by_every_mode_change(self):
        self.apply(False, False, 'selected-for-fingerprint')
        active = self.store.load_active()
        self.rows[0].routing_mode = 'direct'
        self.store.ensure_policy_v2()
        draft = self.draft(False, True)
        checked = self.coordinator.check(draft['revision'])
        with self.assertRaises(ProxyConflict): self.coordinator.apply(draft['revision'], 'missing')
        self.rows[0].routing_mode = 'relay'
        with self.assertRaises(ProxyConflict):
            self.coordinator.apply(draft['revision'], 'stale',
                expected_source_fingerprint=checked['source_fingerprint'])
        self.assertEqual(self.store.load_active(), active)
        self.assertTrue(self.runtime.running)

    def test_same_config_different_mode_changes_identity_and_dirty_status(self):
        self.apply(False, True, 'old-inherit')
        old = self.store.load_active()
        self.rows[0].routing_mode = 'relay'
        self.manager().validate_inbound_change(self.rows)
        self.assertTrue(self.manager().status()['sing-box']['dirty'])
        result = self.coordinator.reconcile(transaction_id='explicit')
        new = self.store.load_active()
        self.assertEqual(old['core_revision'], new['core_revision'])
        self.assertNotEqual(old['runtime_context']['policy_identity'], new['runtime_context']['policy_identity'])
        self.assertEqual(new['runtime_context']['plan_manifest'][0]['mode'], 'relay')
        self.assertFalse(self.manager().status()['sing-box']['dirty'])
        self.assertTrue(result['applied'])

    def test_same_bytes_mode_change_rejects_old_idempotency_key(self):
        self.rows[0].routing_mode = 'relay'
        first = self.apply_modes(relay=True)
        self.rows[0].routing_mode = 'inherit'
        with self.assertRaises(ProxyConflict):
            self.coordinator.apply(first['revision'], 'mode',
                expected_source_fingerprint=first['source_fingerprint'])
        with self.assertRaises(ProxyConflict): self.coordinator.reconcile(transaction_id='mode')

    def test_v2_idempotency_survives_newer_draft_without_using_it(self):
        self.rows[0].routing_mode = 'relay'
        first = self.apply_modes()
        self.draft(True, True)
        before = list(self.resolutions)
        result = self.coordinator.apply(first['revision'], 'mode',
            expected_source_fingerprint=first['source_fingerprint'])
        self.assertTrue(result['already_completed'])
        self.assertTrue(result['applied'])
        self.assertEqual(self.resolutions, before)

    def test_mode_only_dirty_restart_fails_closed_without_resolution(self):
        self.rows[0].routing_mode = 'relay'
        self.apply_modes(relay=True)
        self.rows[0].routing_mode = 'inherit'
        before = list(self.resolutions)
        with self.assertRaises(ProxyApplyError): self.coordinator.recover()
        self.assertFalse(self.runtime.running)
        self.assertEqual(self.resolutions, before)

    def test_failed_reconcile_preserves_exact_old_committed_policy_not_new_rows(self):
        self.rows[0].routing_mode = 'relay'
        self.apply_modes()
        old = self.store.load_active()
        self.rows[0].routing_mode = 'direct'
        self.runtime.fail_after_switch = True
        with self.assertRaisesRegex(ProxyApplyError, 'complete previous'):
            self.coordinator.reconcile(transaction_id='failed')
        self.assertEqual(self.store.load_active(), old)
        self.assertTrue(self.runtime.running)
        self.assertEqual(self.rows[0].routing_mode, 'direct')
        self.assertEqual(self.manager().inbound_routing_status()[1]['state'], 'pending')

    def test_crashed_mode_change_retains_journal_and_stops_when_old_rows_unprovable(self):
        self.rows[0].routing_mode = 'relay'
        self.apply_modes()
        self.rows[0].routing_mode = 'direct'
        with patch.object(self.store, 'commit_apply', side_effect=SimulatedCrash()):
            with self.assertRaises(SimulatedCrash): self.coordinator.reconcile(transaction_id='crashed')
        with self.assertRaises(ProxyApplyError): self.coordinator.recover()
        self.assertIsNotNone(self.store.recoverable_pending())
        self.assertFalse(self.runtime.running)

    def test_active_relay_protects_selected_upstream_with_both_flags_off(self):
        self.rows[0].routing_mode = 'relay'
        self.apply_modes()
        active = self.store.load_active()
        with self.assertRaises(ProxyConflict):
            self.store.delete_upstream(active['settings']['selected_upstream_id'], self.store.read_snapshot()['revision'])
        self.runtime.stop()
        with self.assertRaises(ProxyConflict):
            self.store.delete_upstream(active['settings']['selected_upstream_id'], self.store.read_snapshot()['revision'])

    def test_disabled_relay_draft_requires_explicit_upstream_unselection_before_delete(self):
        self.rows[0].routing_mode, self.rows[0].enable = 'relay', False
        draft = self.store.load_draft()
        with self.assertRaises(ProxyConflict):
            self.manager().delete_proxy_upstream(draft['settings']['selected_upstream_id'], draft['revision'])
        settings = writable_settings(self.store.read_snapshot())
        settings['selected_upstream_id'] = None
        saved = self.store.save_draft(settings, draft['revision'])
        self.manager().delete_proxy_upstream(draft['settings']['selected_upstream_id'], saved['revision'])

    def test_relay_only_never_protects_unneeded_local_password(self):
        self.rows[0].routing_mode = 'relay'
        self.apply_modes()
        snapshot = self.store.clear_local_password(self.store.read_snapshot()['revision'])
        self.assertFalse(snapshot['settings']['local_password_set'])
        self.assertTrue(self.store.load_active()['runtime_context']['requires_upstream'])

    def test_manifest_need_hash_and_effective_claims_are_checked(self):
        self.rows[0].routing_mode = 'relay'
        self.apply_modes()
        original = self.state()
        for mutate in (
            lambda c: c.update(requires_upstream=False),
            lambda c: c.update(source_fingerprint='0' * 64),
            lambda c: c['plan_manifest'][0].update(effective='direct'),
            lambda c: c.update(policy_version=1),
        ):
            state = copy.deepcopy(original)
            mutate(state['transactions'][state['active_transaction_id']]['runtime_context'])
            (self.store.path / 'state.json').write_text(json.dumps(state))
            with self.assertRaises(ProxyStorageError): self.store.load_active()
        (self.store.path / 'state.json').write_text(json.dumps(original))

    def test_manual_stop_stays_stopped_on_mode_save_but_explicit_restart_applies(self):
        self.rows[0].routing_mode = 'relay'
        self.apply_modes()
        self.runtime.stop()
        self.rows[0].routing_mode = 'direct'
        manager = self.manager()
        manager.validate_inbound_change(self.rows)
        result = manager.apply_database('sing-box')
        self.assertFalse(result['applied'])
        self.assertFalse(self.runtime.running)
        self.assertEqual(manager.inbound_routing_status()[1]['state'], 'pending')
        manager.apply_database('sing-box', preserve_stopped=False)
        self.assertTrue(self.runtime.running)
        self.assertEqual(manager.inbound_routing_status()[1]['applied_effective_mode'], 'direct')

    def test_runtime_crash_disabled_rows_and_xray_cannot_claim_applied_route(self):
        self.rows[0].routing_mode = 'direct'
        disabled = row(2, 24444)
        disabled.routing_mode, disabled.enable = 'relay', False
        xray = row(3, 24445)
        xray.core = 'xray'
        self.rows.extend((disabled, xray))
        self.apply_modes()
        manager = self.manager()
        status = manager.inbound_routing_status()
        self.assertEqual(status[2]['state'], 'stopped')
        self.assertIsNone(status[2]['applied_effective_mode'])
        self.assertEqual(status[3]['state'], 'unknown')
        self.runtime.running = False
        status = manager.inbound_routing_status()
        self.assertEqual(status[1]['state'], 'unknown')
        self.assertIsNone(status[1]['applied_effective_mode'])

    def test_xray_runtime_gate_rejects_without_stopping_unrelated_runtime(self):
        self.apply(False, False, 'selected')
        self.rows[0].routing_mode = 'relay'
        manager = self.manager()
        xray = manager.get('xray').runtime
        xray.record = {'revision': 'x' * 64, 'enabled': True}
        xray.running = True
        with self.assertRaises(CoreError): manager.validate_inbound_change(self.rows)
        self.assertTrue(xray.running)
        self.assertFalse(any(call[0] == 'stop' for call in xray.calls))

    def test_public_coverage_has_modes_without_private_manifest_or_source_material(self):
        self.rows[0].routing_mode = 'relay'
        result = self.apply_modes()
        self.assertEqual(result['coverage'][0]['routing_mode'], 'relay')
        self.assertEqual(result['coverage'][0]['effective_mode'], 'relay')
        raw = json.dumps(result)
        for private in ('plan_manifest', 'source_sha256', 'runtime_context', 'test-only-fake'):
            self.assertNotIn(private, raw)


if __name__ == '__main__':
    unittest.main()
