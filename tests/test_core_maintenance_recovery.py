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

    def context(self, observed):
        return dict(application=observed['application'], data_generation=observed['data_generation'],
                    services={'v-ui.service':'active', 'v-ui-http01.service':'inactive',
                              'v-ui-http01.socket':'active'},
                    config_sha256='e'*64, certificate_mode='managed', trust_sha256=None)

    def open_recovery(self, transaction, context, before, after, *, final=None, backup=None,
                      allow_after=False):
        module = self.fixture
        session = recovery.CoreRecoverySession(module.root, transaction=transaction, context=context,
            before=sf._selection_map(before), after=sf._selection_map(after), final_intents=final,
            backup=backup, allow_after_ids=allow_after,
            _selection_files=self.files, _catalog=module.material.catalog, _store=module.material.store,
            _executor=module.executor, _bundle_anchor=module.cycle.base)
        self.addCleanup(session.close)
        return session

    def initialized(self):
        module = self.fixture
        original = module.session()
        observed = original.prepare()
        original.close()
        before = s.decode_selection(sf._raw(observed['selection']),
            app_source=observed['application']['app_source'], target=observed['application']['target'])
        after = s._next(before, 'sing-box', module.candidate, before.slot('sing-box').current, 'component')
        scan = {core:dict(schema=1, revision=row['revision'], enabled=row['enabled'])
                for core, row in observed['intents'].items()}
        service = dict(panel_running=True, cores=observed['intents'])
        self.files.bootstrap(before, writer_observation=scan, service_snapshot=service)
        for core, row in observed['intents'].items():
            module.write_state(core, dict(schema=2, **row))
        context = self.context(observed)
        transaction = self.files.begin(after, expected_revision=before.revision, service_snapshot=service,
                                       switch_context=context)
        return transaction, context, before, after.after

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
