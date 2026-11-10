"""Temporary recovery journals and real writer locks, with fake core material."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from app import data_owner
from app.core_updates import maintenance_recovery as recovery, selection as s, selection_files as sf
import test_core_maintenance_session as fixture


class CoreRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.CoreMaintenanceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.files = self.fixture.files

    def context(self, observed, *, config=None, trust=None):
        return dict(application=observed['application'], data_generation=observed['data_generation'],
                    services={'v-ui.service':'active', 'v-ui-http01.service':'inactive',
                              'v-ui-http01.socket':'active'},
                    config_sha256='e'*64 if config is None else sf._digest(sf._raw(config)),
                    certificate_mode='managed' if config is None else config['certificate_mode'],
                    trust_sha256=trust)

    def open_recovery(self, transaction, context, before, after, *, final=None, backup=None,
                      allow_after=False, confirmed=None):
        module = self.fixture
        session = recovery.CoreRecoverySession(module.root, transaction=transaction, context=context,
            before=sf._selection_map(before), after=sf._selection_map(after), final_intents=final,
            backup=backup, allow_after_ids=allow_after,
            confirmed_selection=None if confirmed is None else sf._selection_map(confirmed),
            _selection_files=self.files, _catalog=module.material.catalog, _store=module.material.store,
            _executor=module.executor, _bundle_anchor=module.cycle.base)
        self.addCleanup(session.close)
        return session

    def initialized(self, *, config=None, trust=None, missing_xray=False):
        module = self.fixture
        if missing_xray:
            (module.data/'runtime/xray/state.json').unlink()
        original = module.session()
        observed = original.prepare()
        original.close()
        before = s.decode_selection(sf._raw(observed['selection']),
            app_source=observed['application']['app_source'], target=observed['application']['target'])
        after = s._next(before, 'sing-box', module.candidate, before.slot('sing-box').current, 'component')
        scan = {core:dict(schema=1 if row['revision'] is not None else None,
                          revision=row['revision'], enabled=row['enabled'])
                for core, row in observed['intents'].items()}
        service = dict(panel_running=True, cores=observed['intents'])
        self.files.bootstrap(before, writer_observation=scan, service_snapshot=service)
        for core, row in observed['intents'].items():
            if row['revision'] is not None:
                module.write_state(core, dict(schema=2, **row))
        context = self.context(observed, config=config, trust=trust)
        transaction = self.files.begin(after, expected_revision=before.revision, service_snapshot=service,
                                       switch_context=context)
        return transaction, context, before, after.after

    def confirmed_snapshot(self, *, rollback=False, missing_xray=False, provided=False):
        config, chain = self.fixture.tls_fixture(mode='provided' if provided else 'managed')
        trust = sf._digest(chain.read_bytes()) if provided else None
        transaction, context, before, after = self.initialized(
            config=config, trust=trust, missing_xray=missing_xray)
        source = dict(app_source=before.app_source, target=before.target)
        final = self.files.recovery_view(**source)['pre_stop']['cores']
        self.files.record_quiesced(transaction, dict(panel_stopped=True, writers_exclusive=True, cores=final), **source)
        name, digest = 'core-maintenance-'+'8'*32+'.zip', '9'*64
        self.files.record_backup(transaction, dict(kind='backup-components-v1', name=name, sha256=digest,
            backup_id=sf._digest((name+':'+digest).encode()), selection_revision=before.revision,
            intents_sha256=sf._digest(sf._raw(final))), **source)
        self.files.provision(transaction, **source)
        target = s._build(before.app_source, before.target, after.generation+3, before.slots) if rollback else after
        bound = sf._rebind_intents(final, target)
        for core, row in bound.items():
            if row['revision'] is not None:
                self.fixture.write_state(core, dict(schema=2, **row))
        observation = dict(selection_revision=target.revision, panel_running=True, panel_health_verified=True,
            cores={core: dict(row, binary_sha256=target.slot(core).current.binary_sha256,
                              owned_listeners_verified=row['enabled']) for core, row in bound.items()})
        # The real journal and public files reach durable confirmation. Fail
        # at the next cleanup boundary so the original pending stays public.
        with patch.object(self.files, '_archive_and_clear', side_effect=OSError('fixture interrupted cleanup')):
            with self.assertRaisesRegex(sf.CoreSelectionFilesError, '^METADATA_IO$'):
                if rollback:
                    self.files.mark_failed(transaction, **source)
                    self.files.rollback_metadata(transaction, observation,
                        known_generations=(target.generation-1,), **source)
                else:
                    self.files.record_validation(transaction, observation, **source)
                    self.files.confirm(transaction, **source)
        self.assertEqual(self.files.recovery_view(**source)['phase'], 'confirmed')
        return transaction, context, before, after, final, target, bound, config, chain

    def assert_readonly_confirmed(self, snapshot):
        transaction, context, before, after, final, target, bound, _, _ = snapshot
        runtime = self.fixture.data/'runtime'
        state_paths = [runtime/core/'state.json' for core in s.CORES]
        before_bytes = {path:path.read_bytes() if path.exists() else None for path in state_paths}
        pending = self.files.layout.root/sf.PENDING
        pending_bytes = pending.read_bytes()
        with patch.object(data_owner, '_CORE_UPDATE_PENDING', pending):
            session = self.open_recovery(transaction, context, before, after, final=final, confirmed=target)
            self.assertEqual(session.prepare()['transaction'], transaction)
            self.assertTrue(self.fixture.cycle.busy(self.fixture.root/'.operation.lock'))
            with patch.object(recovery.tools, 'atomic_json', side_effect=AssertionError('unexpected state write')), \
                    patch.object(session, '_bind', side_effect=AssertionError('unexpected recovery binding')):
                result = session.execute()
            self.assertEqual(result, dict(selection=sf._selection_map(target), final_intents=bound, changed_cores=[]))
            self.fixture.cycle.assert_writers(True)
            session.release_recovery_writers()
            self.fixture.cycle.assert_writers(False)
            session._operation_proof()
        self.assertEqual({path:path.read_bytes() if path.exists() else None for path in state_paths}, before_bytes)
        self.assertEqual(pending.read_bytes(), pending_bytes)
        for core, (raw, revision) in self.fixture.saved.items():
            self.assertEqual((runtime/core/(revision+'.json')).read_bytes(), raw)

    def test_confirmed_candidate_verifies_under_real_writers_and_keeps_legitimate_missing_state(self):
        snapshot = self.confirmed_snapshot(missing_xray=True)
        self.assert_readonly_confirmed(snapshot)
        self.assertFalse((self.fixture.data/'runtime/xray/state.json').exists())

    def test_confirmed_recovery_generation_verifies_before_slots_without_writing(self):
        snapshot = self.confirmed_snapshot(rollback=True)
        self.assertGreater(snapshot[5].generation, snapshot[3].generation)
        self.assert_readonly_confirmed(snapshot)

    def test_confirmed_path_rejects_unpublished_target_and_wrong_pending_transaction(self):
        config, _ = self.fixture.tls_fixture(mode='managed')
        transaction, context, before, after = self.initialized(config=config)
        final = self.files.recovery_view(app_source=before.app_source, target=before.target)['pre_stop']['cores']
        with self.assertRaisesRegex(ValueError, 'RECOVERY_CONFIRMED_TARGET'):
            self.open_recovery(transaction, context, before, after, final=final, confirmed=before)
        unrelated = s._build(before.app_source, before.target, after.generation+1, after.slots)
        with self.assertRaisesRegex(ValueError, 'RECOVERY_CONFIRMED_TARGET'):
            self.open_recovery(transaction, context, before, after, final=final, confirmed=unrelated)
        with self.assertRaisesRegex(ValueError, 'RECOVERY_PENDING_CHANGED'):
            self.open_recovery('f'*32, context, before, after, final=final, confirmed=after).prepare()
        with self.assertRaisesRegex(ValueError, 'RECOVERY_CONFIRMED_CHANGED'):
            self.open_recovery(transaction, context, before, after, final=final, confirmed=after).prepare()
        self.assertFalse(self.fixture.cycle.busy(self.fixture.root/'.operation.lock'))

    def test_confirmed_path_refuses_wrong_schema_id_intent_or_config_bytes_without_repair(self):
        transaction, context, before, after, final, target, bound, _, _ = self.confirmed_snapshot()
        runtime = self.fixture.data/'runtime/sing-box'
        state_path = runtime/'state.json'
        config_path = runtime/(bound['sing-box']['revision']+'.json')
        state_raw, config_raw = state_path.read_bytes(), config_path.read_bytes()
        for fault in ('legacy', 'wrong-id', 'stop-intent', 'missing-state', 'config-bytes'):
            with self.subTest(fault=fault):
                self.fixture.cycle.write(state_path, state_raw)
                self.fixture.cycle.write(config_path, config_raw)
                current = dict(schema=2, **bound['sing-box'])
                if fault == 'legacy':
                    self.fixture.write_state('sing-box', dict(revision=current['revision'], enabled=current['enabled']))
                elif fault == 'wrong-id':
                    self.fixture.write_state('sing-box', dict(current, artifact_id=final['sing-box']['artifact_id']))
                elif fault == 'stop-intent':
                    self.fixture.write_state('sing-box', dict(current, enabled=False))
                elif fault == 'missing-state':
                    state_path.unlink()
                else:
                    self.fixture.cycle.write(config_path, config_raw+b'\n')
                observed = state_path.read_bytes() if state_path.exists() else None
                session = self.open_recovery(transaction, context, before, after, final=final, confirmed=target)
                session.prepare()
                with patch.object(recovery.tools, 'atomic_json', side_effect=AssertionError('unexpected state write')):
                    with self.assertRaises(ValueError):
                        session.execute()
                self.assertEqual(state_path.read_bytes() if state_path.exists() else None, observed)
                session.close()

    def test_confirmed_path_refuses_changed_root_config_or_saved_public_chain(self):
        transaction, context, before, after, final, target, _, config, chain = self.confirmed_snapshot(provided=True)
        config['port'] += 1
        session = self.open_recovery(transaction, context, before, after, final=final, confirmed=target)
        with self.assertRaisesRegex(ValueError, 'RECOVERY_CONFIG_CHANGED'):
            session.prepare()
        config['port'] -= 1
        session = self.open_recovery(transaction, context, before, after, final=final, confirmed=target)
        session.prepare()
        self.fixture.cycle.write(chain, chain.read_bytes()+b'\n')
        with patch.object(recovery.tools, 'atomic_json', side_effect=AssertionError('unexpected state write')):
            with self.assertRaisesRegex(ValueError, 'RECOVERY_TRUST_CHANGED'):
                session.execute()

    def test_confirmed_path_never_reports_verified_after_exclusive_writers_close(self):
        transaction, context, before, after, final, target, _, _, _ = self.confirmed_snapshot()
        session = self.open_recovery(transaction, context, before, after, final=final, confirmed=target)
        session.prepare()
        original = session._check_runtime_policy

        def lose_writer(artifact_ids):
            original(artifact_ids)
            self.fixture.cycle.assert_writers(True)
            session._writers.close()

        with patch.object(session, '_check_runtime_policy', side_effect=lose_writer), \
                patch.object(recovery.tools, 'atomic_json', side_effect=AssertionError('unexpected state write')):
            with self.assertRaisesRegex(ValueError, 'WRITERS_REQUIRED'):
                session.execute()
        self.fixture.cycle.assert_writers(False)
        self.assertTrue(self.fixture.cycle.busy(self.fixture.root/'.operation.lock'))

    def test_early_recovery_preserves_final_stop_and_needs_no_backup_or_state_write(self):
        transaction, context, before, after = self.initialized()
        module = self.fixture
        old = module.read_state('sing-box')
        old['enabled'] = False
        module.write_state('sing-box', old)
        baseline = {core:module.read_state(core) for core in s.CORES}
        with patch.object(data_owner, '_CORE_UPDATE_PENDING', self.files.layout.root/sf.PENDING):
            session = self.open_recovery(transaction, context, before, after)
            self.assertEqual(session.prepare()['transaction'], transaction)
            self.assertTrue(module.cycle.busy(module.root/'.operation.lock'))
            with patch.object(recovery.tools, 'atomic_json', side_effect=AssertionError('unexpected state write')):
                result = session.execute()
            self.assertEqual(result['changed_cores'], [])
            self.assertFalse(result['final_intents']['sing-box']['enabled'])
            self.assertEqual({core:module.read_state(core) for core in s.CORES}, baseline)
            module.cycle.assert_writers(True)
            session.release_recovery_writers()
            module.cycle.assert_writers(False)
            session._operation_proof()
        self.assertEqual(self.files.read_switch_recovery()['phase'], 'prepared')

    def test_generation_or_unknown_runtime_id_refuses_before_writing(self):
        transaction, context, before, after = self.initialized()
        modified = deepcopy(context)
        modified['data_generation']['ino'] += 1
        with self.assertRaisesRegex(ValueError, 'RECOVERY_SOURCE_CHANGED'):
            self.open_recovery(transaction, modified, before, after).prepare()
        old = self.fixture.read_state('sing-box')
        self.fixture.write_state('sing-box', dict(old, artifact_id='9'*64))
        session = self.open_recovery(transaction, context, before, after)
        session.prepare()
        with patch.object(recovery.tools, 'atomic_json', side_effect=AssertionError('unexpected state write')):
            with self.assertRaisesRegex(ValueError, 'RECOVERY_RUNTIME_ID'):
                session.execute()
        self.assertTrue((self.files.layout.root/sf.PENDING).exists())

    def test_partial_first_switch_repairs_only_ids_with_the_verified_backup(self):
        module = self.fixture
        original, observed, _ = module.checked()
        before, after = original._before, original._after
        plan = s.SelectionPlan('component', 'sing-box', before, after, True)
        context = self.context(observed)
        transaction = self.files.begin(plan, expected_revision=before.revision,
            service_snapshot=dict(panel_running=True, cores=observed['intents']),
            initial_schemas=observed['runtime_schemas'], switch_context=context)
        source = dict(app_source=before.app_source, target=before.target)
        final = original.execute()['final_intents']
        self.files.record_quiesced(transaction, dict(panel_stopped=True, writers_exclusive=True, cores=final),
                                   **source)
        made = original.backup_core(dict(schema=1, kind='core-backup-closure-v1',
            selection=sf._selection_map(before), intents=final, panel_was_running=True))
        name, digest = made['backup_name'], made['backup_sha256']
        backup = dict(kind='backup-components-v1', name=name, sha256=digest,
            backup_id=sf._digest((name+':'+digest).encode()), selection_revision=before.revision,
            intents_sha256=sf._digest(sf._raw(final)))
        self.files.record_backup(transaction, backup, **source)
        self.files.provision(transaction, **source)
        legacy_xray = module.read_state('xray')
        original.bind_core_ids(digest)
        module.write_state('xray', legacy_xray)
        original.close()
        archive = module.root/'backups'/name
        original_bytes = archive.read_bytes()
        archive.write_bytes(original_bytes+b'corrupt')
        bad = self.open_recovery(transaction, context, before, after, final=final, backup=backup,
                                 allow_after=True)
        bad.prepare()
        with patch.object(recovery.tools, 'atomic_json', side_effect=AssertionError('unexpected state write')):
            with self.assertRaisesRegex(ValueError, 'RECOVERY_BACKUP_CHANGED'):
                bad.execute()
        bad.close()
        archive.write_bytes(original_bytes)
        session = self.open_recovery(transaction, context, before, after, final=final, backup=backup,
                                     allow_after=True)
        session.prepare()
        result = session.execute()
        self.assertEqual(set(result['changed_cores']), set(s.CORES))
        self.assertEqual(result['final_intents'], final)
        for core in s.CORES:
            raw, revision = module.saved[core]
            self.assertEqual((module.data/'runtime'/core/(revision+'.json')).read_bytes(), raw)
            self.assertEqual(module.read_state(core), dict(schema=2, **final[core]))
        self.assertTrue((self.files.layout.root/sf.PENDING).exists())


if __name__ == '__main__':
    unittest.main()
