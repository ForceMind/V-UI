"""Exact committed protection and offline recovery, fake private fixtures only."""
import copy
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import release_tools as release
from app.services import proxy_recovery as recovery
from app.services.core_manager import SingBoxAdapter
from app.services.core_runtime import revision
from app.services.inbound_policy import policy_plan
from app.services.proxy_policy import VerifiedEndpoint, compile_proxy_policy
from app.services.proxy_store import ProxyStore
from tests import test_proxy_recovery as fixtures


class InboundRecoveryTests(unittest.TestCase):
    setUp = fixtures.ProxyRecoveryTests.setUp
    fixture = fixtures.ProxyRecoveryTests.fixture
    settings = staticmethod(fixtures.ProxyRecoveryTests.settings)
    draft = fixtures.ProxyRecoveryTests.draft
    runtime = fixtures.ProxyRecoveryTests.runtime
    commit = fixtures.ProxyRecoveryTests.commit
    backup = fixtures.ProxyRecoveryTests.backup
    files = staticmethod(fixtures.ProxyRecoveryTests.files)
    preview_restore = fixtures.ProxyRecoveryTests.preview_restore
    execute_restore = fixtures.ProxyRecoveryTests.execute_restore
    assert_no_change = fixtures.ProxyRecoveryTests.assert_no_change

    def mixed(self):
        recovery.migrate_legacy_modes(self.data)
        self.row.routing_mode = 'relay'
        other = SimpleNamespace(**copy.deepcopy(vars(self.row)))
        other.id, other.port, other.tag, other.routing_mode = 2, 24444, 'direct-inbound', 'direct'
        self.rows = [self.row, other]
        self.write_rows()

    def write_rows(self):
        with sqlite3.connect(self.data / 'v-ui.db') as db:
            db.execute('DELETE FROM inbounds')
            for row in self.rows:
                db.execute('INSERT INTO inbounds VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (row.id, row.user_id, row.core, row.up, row.down, row.total, row.remark,
                     int(row.enable), row.expiry_time, row.port, row.protocol,
                     json.dumps(row.settings), json.dumps(row.stream_settings), row.tag, row.routing_mode))

    def compiled(self, snapshot, previous=None, previous_enabled=False, *, kind='apply'):
        rows = getattr(self, 'rows', [self.row])
        plan = policy_plan(rows, snapshot['settings'])
        endpoint = (VerifiedEndpoint(self.upstream_id, snapshot['revision'], 'upstream.example',
                                     ('8.8.4.4',), 1) if plan.requires_upstream else None)
        addresses = ['127.0.0.1', '192.168.8.2'] if endpoint else []
        compiled = compile_proxy_policy(SingBoxAdapter().build_config(rows), rows, snapshot,
                                        endpoint=endpoint, self_addresses=addresses)
        context = {'schema_version': 1, 'kind': kind, 'candidate_core_revision': revision(compiled.config),
            'previous_core_revision': previous, 'previous_enabled': previous_enabled,
            'policy_identity': compiled.identity, 'self_addresses': addresses,
            'endpoint': None if endpoint is None else {**vars(endpoint), 'addresses': list(endpoint.addresses)}}
        if not plan.legacy:
            self.store.ensure_policy_v2()
            context.update(schema_version=2, policy_version=2, plan_manifest=plan.manifest,
                           source_fingerprint=plan.source_fingerprint, requires_upstream=plan.requires_upstream)
        return compiled.config, context

    def test_mixed_both_flags_off_roundtrip_preserves_exact_applied_manifest(self):
        self.mixed()
        active = self.commit(False, False)
        checksum = self.backup()
        plan = self.preview_restore(checksum)
        self.execute_restore(checksum, plan)
        self.assertEqual(self.store.load_active(), active)
        self.assertEqual([row.routing_mode for row in recovery._rows(self.data)], ['relay', 'direct'])
        self.assertTrue(self.store.load_active()['runtime_context']['requires_upstream'])
        self.assertEqual(recovery._validate(self.data)['active_switches'],
                         {'local_proxy_enabled': False, 'relay_enabled': False})

    def test_ordinary_restore_rejects_same_count_different_protected_ids(self):
        self.mixed()
        self.rows[0].routing_mode, self.rows[1].routing_mode = 'direct', 'relay'
        self.write_rows()
        self.commit(False, False, transaction='protect-b')
        checksum = self.backup()
        self.rows[0].routing_mode, self.rows[1].routing_mode = 'relay', 'direct'
        self.write_rows()
        self.commit(False, False, transaction='protect-a')
        self.assert_no_change(lambda: release.restore(self.root, self.archive, checksum))

    def test_ordinary_restore_protection_ignores_dirty_desired_database(self):
        self.mixed()
        active = self.commit(False, False)
        checksum = self.backup()
        before = recovery.committed_protection(self.data)
        self.rows[0].routing_mode, self.rows[1].routing_mode = 'direct', 'relay'
        self.write_rows()
        self.assertEqual(recovery.committed_protection(self.data), before)
        release.restore(self.root, self.archive, checksum)
        self.assertEqual(self.store.load_active(), active)
        self.assertEqual([row.routing_mode for row in recovery._rows(self.data)], ['relay', 'direct'])

    def test_manual_stop_cannot_clear_restore_protection_intent(self):
        self.mixed()
        self.rows[0].routing_mode, self.rows[1].routing_mode = 'direct', 'relay'
        self.write_rows()
        self.commit(False, False, transaction='protect-b')
        checksum = self.backup()
        self.rows[0].routing_mode, self.rows[1].routing_mode = 'relay', 'direct'
        self.write_rows()
        active = self.commit(False, False, transaction='protect-a')
        release.atomic_json(self.data / 'runtime/sing-box/state.json',
                            {'revision': active['core_revision'], 'enabled': False})
        self.assert_no_change(lambda: release.restore(self.root, self.archive, checksum))

    def test_dirty_backup_cannot_claim_new_rows_match_committed_old_policy(self):
        self.mixed()
        self.commit(False, False)
        self.rows[0].routing_mode, self.rows[1].routing_mode = 'direct', 'relay'
        self.write_rows()
        checksum = self.backup()
        self.assert_no_change(lambda: release.restore(self.root, self.archive, checksum))
        self.assert_no_change(lambda: self.preview_restore(checksum))

    def test_v2_missing_column_is_corruption_even_after_returning_to_inherit(self):
        self.store.ensure_policy_v2()
        with self.assertRaises(recovery.ProxyRecoveryError): recovery._rows(self.data)
        with self.assertRaises(recovery.ProxyRecoveryError): recovery.migrate_legacy_modes(self.data)

    def test_existing_null_or_unknown_column_is_never_migrated(self):
        with sqlite3.connect(self.data / 'v-ui.db') as db:
            db.execute('ALTER TABLE inbounds ADD COLUMN routing_mode TEXT')
        for value in (None, '', 'Relay', 'unknown'):
            with sqlite3.connect(self.data / 'v-ui.db') as db:
                db.execute('UPDATE inbounds SET routing_mode=?', (value,))
            with self.assertRaises(ValueError): recovery._rows(self.data)
        with sqlite3.connect(self.data / 'v-ui.db') as db:
            self.assertEqual(db.execute('SELECT routing_mode FROM inbounds').fetchone()[0], 'unknown')

    def test_legacy_backup_restore_keeps_monotonic_fence_and_explicit_column_migration(self):
        self.commit(False, False, transaction='legacy')
        checksum = self.backup()
        recovery.migrate_legacy_modes(self.data)
        self.store.ensure_policy_v2()
        release.restore(self.root, self.archive, checksum)
        self.assertEqual(self.store.policy_schema_version(), 2)
        self.assertEqual(recovery._rows(self.data)[0].routing_mode, 'inherit')
        self.assertEqual(self.store.load_active()['runtime_context']['schema_version'], 1)

    def test_dedicated_legacy_restore_also_preserves_monotonic_fence(self):
        self.commit(False, False, transaction='legacy')
        checksum = self.backup()
        recovery.migrate_legacy_modes(self.data)
        self.store.ensure_policy_v2()
        plan = self.preview_restore(checksum)
        self.execute_restore(checksum, plan)
        self.assertEqual(self.store.policy_schema_version(), 2)
        self.assertEqual(recovery._rows(self.data)[0].routing_mode, 'inherit')

    def test_unapplied_direct_and_disabled_relay_backups_retain_no_applied_claim(self):
        self.mixed()
        self.rows[0].enable = False
        self.write_rows()
        self.store.ensure_policy_v2()
        checksum = self.backup()
        plan = self.preview_restore(checksum)
        self.assertIsNone(plan['policy']['active_revision'])
        self.assertFalse(plan['starts_core'])
        self.execute_restore(checksum, plan)
        self.assertIsNone(self.store.load_active())
        self.assertEqual([row.routing_mode for row in recovery._rows(self.data)], ['relay', 'direct'])

    def test_noninherit_database_requires_v2_fence_with_or_without_store(self):
        self.mixed()
        self.rows[0].enable = False
        self.write_rows()
        # state1 + legal saved modes is still inconsistent, not legacy data.
        with self.assertRaises(recovery.ProxyRecoveryError): recovery._validate(self.data)
        with self.assertRaises(recovery.ProxyRecoveryError): recovery.validate_mode_fence(self.data)
        # Strip all proxy roots from a separately constructed private candidate.
        candidate = self.top / 'missing-store'
        candidate.mkdir(mode=0o700)
        (candidate / 'v-ui.db').write_bytes((self.data / 'v-ui.db').read_bytes())
        with self.assertRaises(recovery.ProxyRecoveryError): recovery.validate_mode_fence(candidate)
        self.assertFalse((candidate / 'proxy-state').exists())
        with self.assertRaises(release.ReleaseError):
            release._policy_release_compatible(candidate, self.top / 'irrelevant-payload')

    def test_recovery_plan_shows_exact_saved_and_applied_routes_without_source_material(self):
        self.mixed()
        self.commit(False, False)
        checksum = self.backup()
        policy = self.preview_restore(checksum)['policy']
        self.assertEqual(policy['protected_inbound_ids'], [1])
        self.assertTrue(policy['requires_upstream'])
        self.assertEqual(policy['applied_inbound_policy'], [
            {'id': 1, 'routing_mode': 'relay', 'effective_mode': 'relay', 'enabled': True},
            {'id': 2, 'routing_mode': 'direct', 'effective_mode': 'direct', 'enabled': True}])
        self.assertNotIn('tag', json.dumps(policy))
        self.assertNotIn('source_sha256', json.dumps(policy))

    def test_release_gate_rejects_real_old_reader_and_accepts_new_capability(self):
        self.mixed()
        self.commit(False, False)
        payload = self.top / 'candidate'
        directory = payload / 'app/services'
        directory.mkdir(parents=True)
        old_reader = Path(__file__).parent / 'fixtures/proxy_store_a98_reader.py'
        (directory / 'proxy_store.py').write_bytes(old_reader.read_bytes())
        with self.assertRaisesRegex(release.ReleaseError, 'downgrade refused'):
            release._policy_release_compatible(self.data, payload)
        current_reader = Path(__file__).resolve().parents[1] / 'app/services/proxy_store.py'
        (directory / 'proxy_store.py').write_bytes(current_reader.read_bytes())
        release._policy_release_compatible(self.data, payload)


if __name__ == '__main__': unittest.main()
