"""Root-metadata logic in private temp directories; all host observations fake.

No runtime import, process execution, network, production path or service action.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from app.core_updates import selection as s, selection_files as m
from test_core_selection import APP, TARGET, bundle, initial, material


class SelectionFilesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='vui-selection-files-')
        self.addCleanup(temp.cleanup)
        self.anchor = Path(temp.name).resolve()
        self.layout = m.Layout(self.anchor / 'metadata', os.geteuid(), self.anchor)
        self.files = m.SelectionFiles(self.layout)
        self.context = dict(app_source=APP, target=TARGET)
        self.before = initial()
        self.pre = self.snapshot(self.before)
        self.scan = {core: dict(schema=None, revision=None, enabled=False) for core in s.CORES}
        self.preserved = self.anchor / 'unrelated-user-data'
        self.preserved.write_bytes(b'preserve')
        self.addCleanup(lambda: self.assertEqual(self.preserved.read_bytes(), b'preserve'))

    def snapshot(self, state, *, panel=True, enabled=(True, False)):
        return dict(panel_running=panel, cores={core: dict(
            artifact_id=s.reference_identity(state.slot(core).current),
            revision=hashlib.sha256(core.encode()).hexdigest() if enabled[index] else None,
            enabled=enabled[index]) for index, core in enumerate(s.CORES)})

    def validation(self, state, snapshot=None):
        snapshot = self.pre if snapshot is None else snapshot
        return dict(selection_revision=state.revision, panel_running=snapshot['panel_running'],
            panel_health_verified=snapshot['panel_running'], cores={core: dict(
                artifact_id=s.reference_identity(state.slot(core).current),
                binary_sha256=state.slot(core).current.binary_sha256,
                revision=snapshot['cores'][core]['revision'], enabled=snapshot['cores'][core]['enabled'],
                owned_listeners_verified=snapshot['panel_running'] and snapshot['cores'][core]['enabled']) for core in s.CORES})

    def bootstrap(self):
        stopped = self.snapshot(self.before, panel=False, enabled=(False, False))
        self.files.bootstrap(self.before, writer_observation=self.scan, service_snapshot=stopped)
        self.assertEqual(self.files.read_stable(**self.context), self.before)

    def begin(self):
        self.bootstrap()
        plan = s.plan_component(self.before, core='sing-box', identity=material(),
                                expected_revision=self.before.revision)
        tx = self.files.begin(plan, expected_revision=self.before.revision, service_snapshot=self.pre)
        return tx, plan.after

    def quiesce(self, tx, snapshot=None):
        snapshot = self.pre if snapshot is None else snapshot
        self.files.record_quiesced(tx, dict(panel_stopped=True, writers_exclusive=True,
                                  cores=snapshot['cores']), **self.context)

    def backup(self, tx):
        saved = self.files.backup_snapshot(tx, **self.context)
        receipt = dict(kind='backup-components-v1', backup_id='d' * 64, sha256='e' * 64,
                       selection_revision=saved['selection'].revision,
                       intents_sha256=m._digest(m._raw(saved['intents'])))
        self.files.record_backup(tx, receipt, **self.context)
        return saved

    def validated(self):
        tx, after = self.begin()
        self.quiesce(tx)
        self.backup(tx)
        self.files.provision(tx, **self.context)
        self.files.record_validation(tx, self.validation(after), **self.context)
        return tx, after

    def initial_switch(self):
        plan=s.plan_component(self.before,core='sing-box',identity=material(),expected_revision=self.before.revision)
        tx=self.files.begin(plan,expected_revision=self.before.revision,service_snapshot=self.pre,
                            initial_schemas={'sing-box':1,'xray':None})
        return tx,plan.after

    def test_first_component_switch_binds_absent_public_metadata_until_validated(self):
        self.assertIsNone(self.files.read_boot(**self.context))
        tx,after=self.initial_switch();root=self.layout.root
        self.assertFalse((root/m.SELECTION).exists());self.assertFalse((root/m.FENCE).exists())
        with self.assertRaises(m.CoreSelectionFilesError):self.files.read_boot(**self.context)
        self.quiesce(tx);self.assertEqual(self.backup(tx)['selection'],self.before)
        self.assertEqual(self.files.provision(tx,**self.context),after)
        self.assertFalse((root/m.SELECTION).exists())
        with self.assertRaises(m.CoreSelectionFilesError):self.files.confirm(tx,**self.context)
        self.files.record_validation(tx,self.validation(after),**self.context)
        self.assertEqual(self.files.confirm(tx,**self.context),after)
        self.assertEqual(self.files.read_stable(**self.context),after)

    def test_failed_first_component_trial_recovers_the_logical_old_bundle_at_new_generation(self):
        tx,after=self.initial_switch();self.quiesce(tx);self.backup(tx);self.files.provision(tx,**self.context)
        self.files.mark_failed(tx,**self.context)
        old=s._build(APP,TARGET,after.generation+1,self.before.slots)
        self.assertEqual(self.files.rollback_metadata(tx,self.validation(old),**self.context),old)
        self.assertEqual(self.files.read_stable(**self.context),old)
        self.assertEqual(self.files.recovery_view(**self.context)['before'],m._selection_map(self.before))

    def test_initial_switch_does_not_adopt_missing_schema_or_existing_lock_trace(self):
        plan=s.plan_component(self.before,core='sing-box',identity=material(),expected_revision=self.before.revision)
        for schemas in ({'sing-box':2,'xray':None},{'sing-box':True,'xray':None},
                        {'sing-box':None,'xray':None},{'sing-box':1,'xray':1},{'sing-box':1}):
            with self.subTest(schemas=schemas),self.assertRaises(m.CoreSelectionFilesError):
                self.files.begin(plan,expected_revision=self.before.revision,service_snapshot=self.pre,initial_schemas=schemas)
            self.assertFalse(self.layout.root.exists())
        self.layout.root.mkdir(mode=0o755);self.layout.root.chmod(0o755)
        lock=self.layout.root/m.LOCK;lock.touch(mode=0o600)
        with self.assertRaisesRegex(m.CoreSelectionFilesError,'RECOVERY_REQUIRED'):self.initial_switch()
        self.assertTrue(lock.exists());self.assertFalse((self.layout.root/m.PENDING).exists())

    def test_initial_switch_preserves_foreign_selection_inserted_during_admission(self):
        original=m._new
        def insert(scope,root,name,raw,**kwargs):
            result=original(scope,root,name,raw,**kwargs)
            if name==m.PENDING:original(scope,root,m.SELECTION,b'foreign initial selection',mode=0o644)
            return result
        with patch.object(m,'_new',side_effect=insert),self.assertRaisesRegex(m.CoreSelectionFilesError,'PUBLIC_CHANGED'):
            self.initial_switch()
        self.assertEqual((self.layout.root/m.SELECTION).read_bytes(),b'foreign initial selection')
        self.assertTrue((self.layout.root/m.PENDING).exists())
        with self.assertRaises(m.CoreSelectionFilesError):self.files.read_boot(**self.context)

    def durable_switch(self):
        self.bootstrap()
        plan=s.plan_component(self.before,core='sing-box',identity=material(),expected_revision=self.before.revision)
        context=dict(application=dict(**self.context, release_id='fixture',
            current_sha256='1'*64,manifest_sha256='2'*64,ready_sha256='3'*64),
            data_generation=dict(dev=0,ino=19),
            services={'v-ui.service':'active','v-ui-http01.service':'inactive','v-ui-http01.socket':'active'},
            config_sha256='4'*64,certificate_mode='provided',trust_sha256='5'*64)
        tx=self.files.begin(plan,expected_revision=self.before.revision,service_snapshot=self.pre,switch_context=context)
        return tx,plan.after,context

    def test_switch_recovery_reads_immutable_context_and_refuses_foreign_selector(self):
        tx,after,context=self.durable_switch()
        recovered=self.files.read_switch_recovery()
        self.assertEqual(recovered['switch_context'],context)
        self.assertEqual(recovered['transaction'],tx)
        self.assertEqual(recovered['recovery_selection'],m._selection_map(
            s._build(APP,TARGET,after.generation+1,self.before.slots)))
        wrong=deepcopy(recovered);wrong['switch_context']['trust_sha256']='6'*64
        with self.assertRaisesRegex(m.CoreSelectionFilesError,'JOURNAL_REWRITTEN'):
            m._follows(recovered,wrong)
        path=self.layout.root/m.SELECTION;path.write_bytes(s.encode_selection(after))
        with self.assertRaisesRegex(m.CoreSelectionFilesError,'PUBLIC_CHANGED'):
            self.files.read_switch_recovery()

    def test_recovery_rechecks_the_public_records_before_writing_rollback(self):
        tx,after,context=self.durable_switch()
        restored=s.decode_selection(m._raw(self.files.read_switch_recovery()['recovery_selection']),**self.context)
        (self.layout.root/m.FENCE).unlink()
        with self.assertRaisesRegex(m.CoreSelectionFilesError,'PUBLIC_CHANGED'):
            self.files.rollback_metadata(tx,self.validation(restored),final_intents=self.pre['cores'],
                                         **self.context)
        self.assertTrue((self.layout.root/m.PENDING).exists())
        self.assertFalse((self.layout.root/m.FENCE).exists())

    def test_consumed_staging_and_old_park_do_not_explain_a_missing_public_fence(self):
        tx,after,_=self.durable_switch()
        self.quiesce(tx)
        name='core-maintenance-'+'a'*32+'.zip';digest='b'*64
        self.files.record_backup(tx,dict(kind='backup-components-v1',name=name,sha256=digest,
            backup_id=m._digest((name+':'+digest).encode()), selection_revision=self.before.revision,
            intents_sha256=m._digest(m._raw(self.pre['cores']))),**self.context)
        self.files.provision(tx,**self.context)
        self.files.record_validation(tx,self.validation(after),**self.context)
        rename=m.fs._rename_new
        def lose_ack(parent,name,destination,target):
            rename(parent,name,destination,target)
            if name.startswith('confirm-fence.new-'):
                raise OSError('synthetic fence publish acknowledgement loss')
        with patch.object(m.fs,'_rename_new',side_effect=lose_ack),self.assertRaises(m.CoreSelectionFilesError):
            self.files.confirm(tx,**self.context)
        root=self.layout.root
        self.assertTrue((root/m.ACTIVE/'confirm-fence.old').exists())
        (root/m.FENCE).unlink()
        with self.assertRaisesRegex(m.CoreSelectionFilesError,'PUBLIC_CHANGED'):
            self.files.read_switch_recovery()
        self.assertTrue((root/m.PENDING).exists())

    def test_switch_cleanup_keeps_pending_through_fence_rename_and_rejects_missing_archive(self):
        tx,after,context=self.durable_switch()
        self.quiesce(tx)
        name='core-maintenance-'+'a'*32+'.zip';digest='b'*64
        self.files.record_backup(tx,dict(kind='backup-components-v1',name=name,sha256=digest,
            backup_id=m._digest((name+':'+digest).encode()), selection_revision=self.before.revision,
            intents_sha256=m._digest(m._raw(self.pre['cores']))),**self.context)
        self.files.provision(tx,**self.context)
        self.files.record_validation(tx,self.validation(after),**self.context)
        rename=m.fs._rename_new
        fired=False
        def interrupt(parent,name,destination,identity):
            nonlocal fired
            rename(parent,name,destination,identity)
            if name==m.FENCE and identity=='settle-fence.old' and not fired:
                fired=True
                raise OSError('synthetic fence park failure')
        with patch.object(m.fs,'_rename_new',side_effect=interrupt),self.assertRaises(m.CoreSelectionFilesError):
            self.files.confirm(tx,**self.context)
        self.assertTrue(fired)
        root=self.layout.root
        self.assertTrue((root/m.PENDING).exists())
        self.assertFalse((root/m.FENCE).exists())
        self.assertEqual(self.files.read_switch_recovery()['transaction'],tx)
        # An absent public marker cannot justify finalizing when its owned
        # archived inode is also missing.
        pending=root/m.PENDING;saved=self.anchor/'detached-pending'
        pending.rename(saved)
        with self.assertRaises(m.CoreSelectionFilesError):
            self.files.finish_cleanup(tx,**self.context)
        self.assertFalse((root/m.FENCE).exists())
        saved.rename(pending)
        self.assertEqual(self.files.finish_cleanup(tx,**self.context),after)
        with self.assertRaisesRegex(m.CoreSelectionFilesError,'NO_SWITCH_RECOVERY'):
            self.files.read_switch_recovery()

    def test_explicit_bootstrap_closed_layout_and_nonroot_public_read(self):
        self.bootstrap()
        root = self.layout.root
        for name in (m.SELECTION, m.FENCE):
            self.assertEqual(stat.S_IMODE((root / name).stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE((root / m.LOCK).stat().st_mode), 0o600)
        self.assertEqual((root / m.LOCK).read_bytes(), b'')
        self.assertEqual(stat.S_IMODE((root / m.HISTORY).stat().st_mode), 0o700)
        archive = next((root / m.HISTORY).iterdir())
        self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == (0o600 if path.name.startswith('journal-') else 0o644)
                            for path in archive.iterdir()))
        with patch.object(m.os, 'geteuid', return_value=os.geteuid() + 1), patch.object(m.os, 'write') as write:
            self.assertEqual(self.files.read_stable(**self.context), self.before)
        write.assert_not_called()

    def test_trial_is_never_public_confirmed_and_pending_is_static(self):
        tx, after = self.begin()
        root = self.layout.root
        pending = (root / m.PENDING).read_bytes()
        original = (root / m.SELECTION).read_bytes()
        with self.assertRaises(m.CoreSelectionFilesError):
            self.files.read_stable(**self.context)
        with self.assertRaises(m.CoreSelectionFilesError):
            self.files.backup_stable(**self.context)
        self.quiesce(tx)
        saved = self.backup(tx)
        self.assertEqual(saved['selection'], self.before)
        self.assertEqual(saved['intents'], self.pre['cores'])
        self.assertEqual(self.files.provision(tx, **self.context), after)
        self.assertEqual(self.files.trial_view(tx, **self.context), after)
        self.assertEqual((root / m.SELECTION).read_bytes(), original)
        self.files.record_validation(tx, self.validation(after), **self.context)
        self.assertEqual((root / m.PENDING).read_bytes(), pending)
        self.assertEqual(self.files.confirm(tx, **self.context), after)
        self.assertFalse((root / m.PENDING).exists())
        self.assertEqual(self.files.read_stable(**self.context), after)
        self.assertEqual(self.files.recovery_view(**self.context)['phase'], 'cleanup')

    def test_same_id_noop_still_checks_cas_without_creating_pending(self):
        self.bootstrap()
        plan = s.plan_bundle(self.before, core='sing-box', bundle=bundle('sing-box'),
                             expected_revision=self.before.revision)
        self.assertIsNone(self.files.begin(plan, expected_revision=self.before.revision, service_snapshot=self.pre))
        with self.assertRaises(m.CoreSelectionFilesError):
            self.files.begin(plan, expected_revision='f' * 64, service_snapshot=self.pre)
        self.assertFalse((self.layout.root / m.PENDING).exists())

    def test_wrong_phase_missing_backup_wrong_validation_do_not_confirm(self):
        tx, after = self.begin()
        for action in (lambda: self.files.provision(tx, **self.context),
                       lambda: self.files.confirm(tx, **self.context),
                       lambda: self.files.record_validation(tx, self.validation(after), **self.context)):
            with self.assertRaises(m.CoreSelectionFilesError):
                action()
        self.quiesce(tx)
        with self.assertRaises(m.CoreSelectionFilesError):
            self.files.record_backup(tx, dict(kind='backup-components-v1', backup_id='d' * 64,
                sha256='e' * 64, selection_revision=after.revision, intents_sha256='a' * 64), **self.context)
        self.assertEqual(self.files.recovery_view(**self.context)['phase'], 'quiesced')
        self.backup(tx)
        self.files.provision(tx, **self.context)
        bad = self.validation(after)
        bad['cores']['sing-box']['artifact_id'] = 'f' * 64
        with self.assertRaises(m.CoreSelectionFilesError):
            self.files.record_validation(tx, bad, **self.context)

    def test_final_quiesced_intent_includes_last_stop_not_pre_stop_enabled(self):
        tx, after = self.begin()
        final = self.snapshot(self.before, panel=True, enabled=(False, False))
        self.quiesce(tx, final)
        saved = self.backup(tx)
        self.assertFalse(saved['intents']['sing-box']['enabled'])
        self.files.provision(tx, **self.context)
        with self.assertRaises(m.CoreSelectionFilesError):
            self.files.record_validation(tx, self.validation(after), **self.context)
        self.files.record_validation(tx, self.validation(after, final), **self.context)
        self.files.confirm(tx, **self.context)

    def test_rollback_metadata_has_fresh_highwater_and_preserves_before_previous(self):
        tx, after = self.begin()
        self.quiesce(tx)
        self.files.mark_failed(tx, **self.context)
        restored = s._build(APP, TARGET, 21, self.before.slots)
        result = self.files.rollback_metadata(tx, self.validation(restored), known_generations=(20,), **self.context)
        self.assertEqual(result, restored)
        self.assertEqual(result.slots, self.before.slots)
        self.assertIsNone(result.slot('sing-box').previous)
        self.assertNotEqual(result.revision, self.before.revision)
        self.assertEqual(self.files.read_stable(**self.context), restored)

    def test_restore_never_copies_input_generation_or_revision(self):
        self.bootstrap()
        snapshot = s._build(APP, TARGET, 70, self.before.slots)
        tx = self.files.begin_restore(snapshot, expected_revision=self.before.revision,
            service_snapshot=self.pre, source_backup_sha256='a'*64,
            restored_intents=self.pre['cores'], known_generations=(90,), **self.context)
        view = self.files.recovery_view(**self.context)
        self.assertEqual(view['after']['generation'], 91)
        self.assertNotEqual(view['after']['revision'], snapshot.revision)
        self.assertNotEqual(tx, self.files.recovery_view(**self.context)['before']['revision'][:32])

    def restore_trial(self):
        self.bootstrap()
        incoming=self.snapshot(self.before,enabled=(False,True))
        incoming['cores']['sing-box']['revision']='d'*64
        incoming['cores']['xray']['revision']='e'*64
        tx=self.files.begin_restore(self.before,expected_revision=self.before.revision,
            service_snapshot=self.pre,source_backup_sha256='a'*64,
            restored_intents=incoming['cores'],**self.context)
        self.quiesce(tx)
        saved=self.backup(tx)
        trial=self.files.provision(tx,**self.context)
        return tx,trial,incoming,saved

    def test_restore_validates_incoming_configuration_and_stop_intents_without_changing_backup_before(self):
        tx,trial,incoming,saved=self.restore_trial()
        self.assertEqual(saved['selection'],self.before)
        self.assertEqual(saved['intents'],self.pre['cores'])
        with self.assertRaisesRegex(m.CoreSelectionFilesError,'VALIDATION'):
            self.files.record_validation(tx,self.validation(trial,self.pre),**self.context)
        self.files.record_validation(tx,self.validation(trial,incoming),**self.context)
        after=self.files.confirm(tx,**self.context)
        view=self.files.recovery_view(**self.context)
        self.assertEqual(after.slots,self.before.slots)
        self.assertGreater(after.generation,self.before.generation)
        self.assertEqual(view['quiesced']['cores'],self.pre['cores'])
        self.assertEqual(view['restore_target'],dict(kind='core-restore-target-v1',
            source_backup_sha256='a'*64,intents=incoming['cores']))

    def test_failed_restore_rolls_back_using_live_final_intents_rather_than_incoming_intents(self):
        tx,trial,incoming,_=self.restore_trial()
        self.files.mark_failed(tx,**self.context)
        restored=s._build(APP,TARGET,trial.generation+1,self.before.slots)
        with self.assertRaisesRegex(m.CoreSelectionFilesError,'VALIDATION'):
            self.files.rollback_metadata(tx,self.validation(restored,incoming),**self.context)
        actual=self.files.rollback_metadata(tx,self.validation(restored,self.pre),**self.context)
        self.assertEqual(actual,restored)
        self.assertEqual(self.files.read_stable(**self.context),restored)

    def test_restore_target_missing_wrong_artifact_or_bad_source_sha_refused_before_new_pending(self):
        self.bootstrap()
        bad=deepcopy(self.pre['cores']);bad['sing-box']['artifact_id']='0'*64
        for checksum,intents in (('not-a-sha',self.pre['cores']),('a'*64,bad),('a'*64,{})):
            with self.subTest(checksum=checksum,intents=intents),self.assertRaises(m.CoreSelectionFilesError):
                self.files.begin_restore(self.before,expected_revision=self.before.revision,
                    service_snapshot=self.pre,source_backup_sha256=checksum,restored_intents=intents,**self.context)
            self.assertFalse((self.layout.root/m.PENDING).exists())
            self.assertFalse((self.layout.root/m.ACTIVE).exists())

    def dual_restore_inputs(self):
        before, incoming = {'dev': 3, 'ino': 7}, {'dev': 3, 'ino': 11}
        return dict(service_snapshot=self.pre, source_backup_sha256='a'*64,
            restored_intents=self.pre['cores'], expected_revision=self.before.revision,
            switch_context=dict(application=dict(**self.context, release_id='fixture-release',
                current_sha256='1'*64, manifest_sha256='2'*64, ready_sha256='3'*64),
                data_generation=before, config_sha256='4'*64, certificate_mode='managed', trust_sha256=None,
                services={'v-ui.service':'active', 'v-ui-http01.service':'inactive',
                          'v-ui-http01.socket':'active'}),
            data_plan=dict(slot='b'*32, incoming_generation=incoming, preflight_tree_sha256='c'*64,
                           incoming_trust_sha256=None))

    def dual_restore(self):
        self.bootstrap()
        inputs = self.dual_restore_inputs()
        tx = self.files.begin_restore(self.before, **inputs, **self.context)
        before = inputs['switch_context']['data_generation']
        incoming = inputs['data_plan']['incoming_generation']
        positions = dict(
            staged=dict(live=before, original=None, incoming=incoming, rejected=None),
            parked=dict(live=None, original=before, incoming=incoming, rejected=None),
            trial=dict(live=incoming, original=before, incoming=None, rejected=None),
            rollback_gap=dict(live=None, original=before, incoming=None, rejected=incoming),
            restored=dict(live=before, original=None, incoming=None, rejected=incoming))
        return tx, inputs, positions

    def dual_quiesce_and_backup(self, tx, staged):
        self.files.record_quiesced(tx, dict(panel_stopped=True, writers_exclusive=True,
            cores=self.pre['cores']), restore_data=staged, original_tree_sha256='7'*64,
            prepared_tree_sha256='6'*64, **self.context)
        name = 'core-maintenance-'+'a'*32+'.zip'
        digest = 'e'*64
        receipt = dict(kind='backup-components-v1', name=name, sha256=digest,
            backup_id=m._digest((name+':'+digest).encode()), selection_revision=self.before.revision,
            intents_sha256=m._digest(m._raw(self.pre['cores'])))
        self.files.record_backup(tx, receipt, **self.context)

    def test_dual_restore_refuses_cross_device_same_inode_and_unsafe_slot_before_barrier(self):
        self.bootstrap()
        for change in ({'slot':'../data'}, {'incoming_generation':{'dev':4,'ino':11}},
                       {'incoming_generation':{'dev':3,'ino':7}}, {'preflight_tree_sha256':None}):
            inputs = self.dual_restore_inputs()
            inputs['data_plan'].update(change)
            with self.subTest(change=change), self.assertRaises(m.CoreSelectionFilesError):
                self.files.begin_restore(self.before, **inputs, **self.context)
            self.assertFalse((self.layout.root/m.PENDING).exists())
            self.assertFalse((self.layout.root/m.ACTIVE).exists())

    def test_dual_restore_reserves_generation_for_original_recovery(self):
        self.bootstrap()
        inputs = self.dual_restore_inputs()
        with self.assertRaisesRegex(m.CoreSelectionFilesError, 'GENERATION_EXHAUSTED'):
            self.files.begin_restore(self.before, known_generations=(s.MAX_GENERATION-1,),
                                     **inputs, **self.context)
        self.assertFalse((self.layout.root/m.PENDING).exists())
        self.files.begin_restore(self.before, known_generations=(s.MAX_GENERATION-2,),
                                 **inputs, **self.context)
        journal = self.files.read_restore_recovery()
        self.assertEqual(journal['after']['generation'], s.MAX_GENERATION-1)
        self.assertEqual(journal['recovery_selection']['generation'], s.MAX_GENERATION)

    def test_dual_restore_binds_independent_old_and_incoming_public_chain(self):
        self.bootstrap()
        inputs = self.dual_restore_inputs()
        inputs['switch_context'].update(certificate_mode='provided', trust_sha256='8'*64)
        with self.assertRaisesRegex(m.CoreSelectionFilesError, 'RESTORE_TRUST'):
            self.files.begin_restore(self.before, **inputs, **self.context)
        self.assertFalse((self.layout.root/m.PENDING).exists())
        inputs['data_plan']['incoming_trust_sha256'] = '9'*64
        self.files.begin_restore(self.before, **inputs, **self.context)
        journal = self.files.read_restore_recovery()
        self.assertEqual(journal['switch_context']['trust_sha256'], '8'*64)
        self.assertEqual(journal['restore_target']['data']['incoming_trust_sha256'], '9'*64)

    def test_dual_restore_recognizes_only_exact_data_locations_and_separates_switch_recovery(self):
        tx, inputs, positions = self.dual_restore()
        journal = self.files.read_restore_recovery()
        self.assertEqual(journal['transaction'], tx)
        self.assertEqual(journal['restore_target']['data'], inputs['data_plan'])
        self.assertEqual(journal['data_disposition'], 'restore_original')
        with self.assertRaisesRegex(m.CoreSelectionFilesError, 'RECOVERY_OPERATION'):
            self.files.read_switch_recovery()
        for position, observed in positions.items():
            self.assertEqual(m.restore_data_position(journal, observed), position)
        for invalid in (dict(positions['staged'], live={'dev':3,'ino':99}),
                        dict(positions['staged'], original=positions['staged']['live']),
                        dict(positions['trial'], original=None), dict(positions['parked'], incoming=None),
                        dict(positions['staged'], extra=None)):
            with self.subTest(invalid=invalid), self.assertRaises(m.CoreSelectionFilesError):
                m.restore_data_position(journal, invalid)

    def test_dual_restore_requires_matching_data_and_digest_before_trial_and_commit(self):
        tx, inputs, positions = self.dual_restore()
        with self.assertRaises(m.CoreSelectionFilesError):
            self.dual_quiesce_and_backup(tx, positions['parked'])
        self.dual_quiesce_and_backup(tx, positions['staged'])
        digest = '6'*64
        for observed, content in ((None, None), (positions['staged'], digest),
                                  (positions['trial'], inputs['data_plan']['preflight_tree_sha256'])):
            with self.subTest(observed=observed), self.assertRaises(m.CoreSelectionFilesError):
                self.files.provision(tx, restore_data=observed, restore_tree_sha256=content, **self.context)
        trial = self.files.provision(tx, restore_data=positions['trial'],
                                     restore_tree_sha256=digest, **self.context)
        with self.assertRaises(m.CoreSelectionFilesError):
            self.files.record_validation(tx, self.validation(trial), **self.context)
        self.files.record_validation(tx, self.validation(trial), restore_data=positions['trial'], **self.context)
        with self.assertRaises(m.CoreSelectionFilesError):
            self.files.confirm(tx, restore_data=positions['restored'], **self.context)
        with patch.object(self.files, '_archive_and_clear', side_effect=OSError('synthetic interruption')):
            with self.assertRaises(m.CoreSelectionFilesError):
                self.files.confirm(tx, restore_data=positions['trial'], **self.context)
        journal = self.files.read_restore_recovery()
        self.assertEqual(journal['data_disposition'], 'retain_incoming')
        self.assertEqual(journal['confirmed_selection'], m._selection_map(trial))
        with self.assertRaises(m.CoreSelectionFilesError):
            self.files.finish_cleanup(tx, restore_data=positions['restored'], **self.context)
        self.assertTrue((self.layout.root/m.PENDING).exists())
        self.assertEqual(self.files.finish_cleanup(tx, restore_data=positions['trial'], **self.context), trial)

    def test_dual_restore_unconfirmed_keeps_original_and_rejects_rollback_gap(self):
        tx, _, positions = self.dual_restore()
        early = self.files.read_restore_recovery()
        early_selection = s.decode_selection(m._raw(early['recovery_selection']), **self.context)
        with self.assertRaisesRegex(m.CoreSelectionFilesError, 'RESTORE_DATA_POSITION'):
            self.files.rollback_metadata(tx, self.validation(early_selection), restore_data=positions['restored'],
                                         final_intents=self.pre['cores'], **self.context)
        self.dual_quiesce_and_backup(tx, positions['staged'])
        self.files.provision(tx, restore_data=positions['trial'],
            restore_tree_sha256='6'*64, **self.context)
        journal = self.files.read_restore_recovery()
        self.assertEqual(journal['data_disposition'], 'restore_original')
        self.assertEqual(journal['quiesced']['original_tree_sha256'], '7'*64)
        restored = s.decode_selection(m._raw(journal['recovery_selection']), **self.context)
        for observed in (positions['trial'], positions['rollback_gap']):
            with self.assertRaises(m.CoreSelectionFilesError):
                self.files.rollback_metadata(tx, self.validation(restored), restore_data=observed,
                                             original_tree_sha256='7'*64, **self.context)
        with self.assertRaisesRegex(m.CoreSelectionFilesError, 'RESTORE_DATA_DIGEST'):
            self.files.rollback_metadata(tx, self.validation(restored), restore_data=positions['restored'],
                                         original_tree_sha256='8'*64, **self.context)
        with patch.object(self.files, '_archive_and_clear', side_effect=OSError('synthetic interruption')):
            with self.assertRaises(m.CoreSelectionFilesError):
                self.files.rollback_metadata(tx, self.validation(restored),
                    restore_data=positions['restored'], original_tree_sha256='7'*64, **self.context)
        journal = self.files.read_restore_recovery()
        self.assertEqual(journal['data_disposition'], 'restore_original')
        self.assertEqual(journal['confirmed_selection'], m._selection_map(restored))
        self.assertEqual(self.files.finish_cleanup(tx, restore_data=positions['restored'], **self.context), restored)

    def test_bootstrap_refuses_runtime2_bad_unknown_scan_and_first_loss(self):
        stopped = self.snapshot(self.before, panel=False, enabled=(False, False))
        for fault in ('runtime2', 'unknown', 'bad', 'legacy-missing-revision'):
            scan = deepcopy(self.scan)
            if fault == 'runtime2': scan['sing-box']['schema'] = 2
            elif fault == 'unknown': scan['sing-box']['extra'] = True
            elif fault == 'bad': scan['sing-box']['enabled'] = 1
            else: scan['sing-box']['schema'] = 1
            with self.subTest(fault=fault), self.assertRaises(m.CoreSelectionFilesError):
                self.files.bootstrap(self.before, writer_observation=scan, service_snapshot=stopped)
        self.bootstrap()
        for name in (m.SELECTION, m.FENCE):
            path = self.layout.root / name
            raw = path.read_bytes()
            path.unlink()
            with self.assertRaises(m.CoreSelectionFilesError):
                self.files.bootstrap(self.before, writer_observation=self.scan, service_snapshot=stopped)
            path.write_bytes(raw)
            path.chmod(0o644)

    def test_public_symlink_hardlink_wrong_mode_owner_and_duplicate_json_refused(self):
        self.bootstrap()
        path = self.layout.root / m.SELECTION
        original = path.read_bytes()
        path.chmod(0o666)
        with self.assertRaises(m.CoreSelectionFilesError): self.files.read_stable(**self.context)
        path.chmod(0o644)
        link = self.anchor / 'hardlink'
        os.link(path, link)
        with self.assertRaises(m.CoreSelectionFilesError): self.files.read_stable(**self.context)
        link.unlink()
        path.unlink()
        source = self.anchor / 'source'
        source.write_bytes(original)
        path.symlink_to(source)
        with self.assertRaises(m.CoreSelectionFilesError): self.files.read_stable(**self.context)
        path.unlink()
        path.write_bytes(original.replace(b'"schema":1', b'"schema":1,"schema":1'))
        path.chmod(0o644)
        with self.assertRaises(m.CoreSelectionFilesError): self.files.read_stable(**self.context)
        path.write_bytes(original)
        with patch.object(m.fs, '_regular', side_effect=m.fs.CoreStoreError('FILE_UNTRUSTED')):
            with self.assertRaises(m.CoreSelectionFilesError): self.files.read_stable(**self.context)

    def test_foreign_public_replacement_is_preserved_before_confirm(self):
        tx, after = self.validated()
        path = self.layout.root / m.SELECTION
        foreign = self.layout.root / 'foreign'
        foreign.write_bytes(path.read_bytes())
        foreign.chmod(0o644)
        os.replace(foreign, path)
        inode = path.stat().st_ino
        with self.assertRaisesRegex(m.CoreSelectionFilesError, 'PUBLIC_CHANGED'):
            self.files.confirm(tx, **self.context)
        self.assertEqual(path.stat().st_ino, inode)
        self.assertTrue((self.layout.root / m.PENDING).exists())
        with self.assertRaises(m.CoreSelectionFilesError): self.files.read_stable(**self.context)

    def test_rename_lost_ack_retains_original_and_explicit_rollback_uses_fresh_generation(self):
        tx, after = self.validated()
        rename = m.fs._rename_new
        fired = False
        def lose_ack(parent, name, destination, identity):
            nonlocal fired
            rename(parent, name, destination, identity)
            if name == m.SELECTION and identity == 'confirm-selection.old' and not fired:
                fired = True
                raise m.fs.CoreStoreError('PUBLISH_FAILED')
        with patch.object(m.fs, '_rename_new', side_effect=lose_ack):
            with self.assertRaises(m.CoreSelectionFilesError):
                self.files.confirm(tx, **self.context)
        self.assertTrue(fired)
        self.assertFalse((self.layout.root / m.SELECTION).exists())
        parked = self.layout.root / m.ACTIVE / 'confirm-selection.old'
        self.assertEqual(parked.read_bytes(), s.encode_selection(self.before))
        view = self.files.recovery_view(**self.context)
        self.assertEqual(view['phase'], 'confirming')
        self.assertIsNone(view['actual_files']['selection'])
        with self.assertRaises(m.CoreSelectionFilesError): self.files.confirm(tx, **self.context)
        restored = s._build(APP, TARGET, after.generation + 1, self.before.slots)
        self.files.rollback_metadata(tx, self.validation(restored), **self.context)
        self.assertEqual(self.files.read_stable(**self.context), restored)
        archive = self.layout.root / m.HISTORY / tx
        self.assertEqual((archive / 'confirm-selection.old').read_bytes(), s.encode_selection(self.before))

    def test_pending_park_fsync_failure_stays_unsettled_until_explicit_cleanup(self):
        tx, after = self.validated()
        rename, fsync = m.fs._rename_new, os.fsync
        pending_parked = False
        fired = False
        def observe_park(parent, name, destination, identity):
            nonlocal pending_parked
            rename(parent, name, destination, identity)
            if name == m.PENDING:
                pending_parked = True
        def fail_fsync(fd):
            nonlocal fired
            if pending_parked and not fired:
                fired = True
                raise OSError('synthetic pending durability failure')
            return fsync(fd)
        with patch.object(m.fs, '_rename_new', side_effect=observe_park), patch.object(m.os, 'fsync', side_effect=fail_fsync):
            with self.assertRaises(m.CoreSelectionFilesError): self.files.confirm(tx, **self.context)
        self.assertTrue(fired)
        self.assertFalse((self.layout.root / m.PENDING).exists())
        self.assertFalse(json.loads((self.layout.root / m.FENCE).read_bytes())['settled'])
        with self.assertRaises(m.CoreSelectionFilesError): self.files.read_stable(**self.context)
        view = self.files.recovery_view(**self.context)
        self.assertEqual(view['phase'], 'confirmed')
        self.assertEqual(view['location'], 'archive')
        self.assertTrue(view['requires_recovery'])
        self.assertEqual(self.files.finish_cleanup(tx, **self.context), after)
        self.assertEqual(self.files.read_stable(**self.context), after)
        self.assertFalse(self.files.recovery_view(**self.context)['requires_recovery'])

    def test_publish_after_rename_lost_ack_never_adopts_foreign_or_retries_trial(self):
        tx, after = self.validated()
        rename = m.fs._rename_new
        fired = False
        def lose_ack(parent, name, destination, identity):
            nonlocal fired
            rename(parent, name, destination, identity)
            if name.startswith('confirm-selection.new-') and not fired:
                fired = True
                raise m.fs.CoreStoreError('PUBLISH_FAILED')
        with patch.object(m.fs, '_rename_new', side_effect=lose_ack):
            with self.assertRaises(m.CoreSelectionFilesError): self.files.confirm(tx, **self.context)
        self.assertEqual((self.layout.root / m.SELECTION).read_bytes(), s.encode_selection(after))
        self.assertEqual(self.files.recovery_view(**self.context)['phase'], 'confirming')
        with self.assertRaises(m.CoreSelectionFilesError): self.files.finish_cleanup(tx, **self.context)
        restored = s._build(APP, TARGET, after.generation + 1, self.before.slots)
        self.files.rollback_metadata(tx, self.validation(restored), **self.context)
        self.assertEqual(self.files.read_stable(**self.context), restored)

    def test_foreign_name_inserted_after_park_is_not_overwritten_or_deleted(self):
        tx, after = self.validated()
        rename = m.fs._rename_new
        foreign_raw = b'foreign preserve exactly'
        def insert_foreign(parent, name, destination, identity):
            rename(parent, name, destination, identity)
            if name == m.SELECTION and identity == 'confirm-selection.old':
                path = self.layout.root / m.SELECTION
                path.write_bytes(foreign_raw)
                path.chmod(0o644)
        with patch.object(m.fs, '_rename_new', side_effect=insert_foreign):
            with self.assertRaises(m.CoreSelectionFilesError): self.files.confirm(tx, **self.context)
        self.assertEqual((self.layout.root / m.SELECTION).read_bytes(), foreign_raw)
        self.assertEqual((self.layout.root / m.ACTIVE / 'confirm-selection.old').read_bytes(),
                         s.encode_selection(self.before))
        self.assertTrue((self.layout.root / m.PENDING).exists())

    def test_foreign_source_replaced_at_native_park_is_returned_without_overwrite(self):
        tx, after = self.validated()
        rename = m.fs._rename_new
        foreign_raw = b'foreign source preserve'
        fired = False
        def race(parent, name, destination, identity):
            nonlocal fired
            if name == m.SELECTION and identity == 'confirm-selection.old' and not fired:
                fired = True
                foreign = self.layout.root / 'foreign-before-syscall'
                foreign.write_bytes(foreign_raw)
                foreign.chmod(0o644)
                os.replace(foreign, self.layout.root / m.SELECTION)
            rename(parent, name, destination, identity)
        with patch.object(m.fs, '_rename_new', side_effect=race):
            with self.assertRaisesRegex(m.CoreSelectionFilesError, 'PUBLIC_CHANGED'):
                self.files.confirm(tx, **self.context)
        self.assertEqual((self.layout.root / m.SELECTION).read_bytes(), foreign_raw)
        self.assertTrue((self.layout.root / m.PENDING).exists())

    def test_close_failure_attempts_all_owned_fds_and_leaves_recoverable_barrier(self):
        self.bootstrap()
        plan = s.plan_component(self.before, core='sing-box', identity=material(),
                                expected_revision=self.before.revision)
        opened, closed = [], []
        open_fd, close = os.open, os.close
        def observe_open(*args, **kwargs):
            fd = open_fd(*args, **kwargs)
            opened.append(fd)
            return fd
        def lose_close_ack(fd):
            close(fd)
            closed.append(fd)
            if len(closed) == 1:
                raise OSError('synthetic close acknowledgement lost')
        with patch.object(m.os, 'open', side_effect=observe_open), patch.object(m.os, 'close', side_effect=lose_close_ack):
            with self.assertRaisesRegex(m.CoreSelectionFilesError, 'CLOSE_FAILED'):
                self.files.begin(plan, expected_revision=self.before.revision, service_snapshot=self.pre)
        self.assertEqual(sorted(opened), sorted(closed))
        for fd in opened:
            with self.assertRaises(OSError): os.fstat(fd)
        self.assertTrue((self.layout.root / m.PENDING).exists())
        self.assertEqual(self.files.recovery_view(**self.context)['phase'], 'prepared')

    def test_mutation_of_already_read_selection_or_new_barrier_fails_public_read(self):
        self.bootstrap()
        read = os.pread
        fired = False
        def inject_pending(fd, size, offset):
            nonlocal fired
            value = read(fd, size, offset)
            if b'"highest_generation"' in value and not fired:
                fired = True
                path = self.layout.root / m.PENDING
                path.write_bytes(b'{}')
                path.chmod(0o644)
            return value
        with patch.object(m.os, 'pread', side_effect=inject_pending):
            with self.assertRaisesRegex(m.CoreSelectionFilesError, 'RECOVERY_REQUIRED'):
                self.files.read_stable(**self.context)
        self.assertTrue((self.layout.root / m.PENDING).exists())

    def test_initial_barrier_fsync_failure_cannot_rebootstrap_or_read_initial_bundle(self):
        stopped = self.snapshot(self.before, panel=False, enabled=(False, False))
        fsync = os.fsync
        def fail_after_pending(fd):
            if (self.layout.root / m.PENDING).exists():
                raise OSError('synthetic initial pending fsync failure')
            return fsync(fd)
        with patch.object(m.os, 'fsync', side_effect=fail_after_pending):
            with self.assertRaises(m.CoreSelectionFilesError):
                self.files.bootstrap(self.before, writer_observation=self.scan, service_snapshot=stopped)
        self.assertTrue((self.layout.root / m.PENDING).exists())
        with self.assertRaises(m.CoreSelectionFilesError): self.files.read_stable(**self.context)
        with self.assertRaises(m.CoreSelectionFilesError):
            self.files.bootstrap(self.before, writer_observation=self.scan, service_snapshot=stopped)

    def test_restore_panel_stopped_keeps_all_four_enabled_intents_without_listener_claim(self):
        self.bootstrap()
        state = self.before
        for enabled in ((False, False), (True, False), (False, True), (True, True)):
            snapshot = self.snapshot(state, panel=False, enabled=enabled)
            other_core = 'sing-box'
            plan = s.plan_component(state, core=other_core,
                identity=material(seed=str(enabled)), expected_revision=state.revision)
            tx = self.files.begin(plan, expected_revision=state.revision, service_snapshot=snapshot)
            self.quiesce(tx, snapshot)
            self.backup(tx)
            self.files.provision(tx, **self.context)
            observation = self.validation(plan.after, snapshot)
            self.assertTrue(all(not row['owned_listeners_verified'] for row in observation['cores'].values()))
            self.files.record_validation(tx, observation, **self.context)
            state = self.files.confirm(tx, **self.context)
            self.assertEqual(self.files.read_stable(**self.context), state)

    def test_live_flock_blocks_second_writer_without_touching_selection(self):
        import fcntl
        self.bootstrap()
        lock = os.open(self.layout.root / m.LOCK, os.O_RDWR)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            plan = s.plan_component(self.before, core='sing-box', identity=material(),
                                    expected_revision=self.before.revision)
            with self.assertRaisesRegex(m.CoreSelectionFilesError, 'SELECTION_BUSY'):
                self.files.begin(plan, expected_revision=self.before.revision, service_snapshot=self.pre)
            self.assertFalse((self.layout.root / m.PENDING).exists())
        finally:
            os.close(lock)

    def test_parent_replaced_while_reading_is_detected_by_held_directory_binding(self):
        self.bootstrap()
        read = os.pread
        fired = False
        def replace_parent(fd, size, offset):
            nonlocal fired
            value = read(fd, size, offset)
            if b'"highest_generation"' in value and not fired:
                fired = True
                self.layout.root.rename(self.anchor / 'preserved-parent')
                self.layout.root.mkdir(mode=0o755)
            return value
        with patch.object(m.os, 'pread', side_effect=replace_parent):
            with self.assertRaisesRegex(m.CoreSelectionFilesError, 'DIRECTORY_CHANGED'):
                self.files.read_stable(**self.context)
        self.assertTrue((self.anchor / 'preserved-parent' / m.SELECTION).exists())

    def test_before_snapshot_race_is_not_promoted_to_original_owned_file(self):
        self.bootstrap()
        plan = s.plan_component(self.before, core='sing-box', identity=material(),
                                expected_revision=self.before.revision)
        new = m._new
        foreign_inode = []
        def replace_before_capture(scope, parent, name, raw, **kwargs):
            result = new(scope, parent, name, raw, **kwargs)
            if name == m.PENDING:
                foreign = self.layout.root / 'racing-foreign'
                foreign.write_bytes((self.layout.root / m.SELECTION).read_bytes())
                foreign.chmod(0o644)
                os.replace(foreign, self.layout.root / m.SELECTION)
                foreign_inode.append((self.layout.root / m.SELECTION).stat().st_ino)
            return result
        with patch.object(m, '_new', side_effect=replace_before_capture):
            with self.assertRaisesRegex(m.CoreSelectionFilesError, 'PUBLIC_CHANGED'):
                self.files.begin(plan, expected_revision=self.before.revision, service_snapshot=self.pre)
        self.assertEqual((self.layout.root / m.SELECTION).stat().st_ino, foreign_inode[0])
        self.assertTrue((self.layout.root / m.PENDING).exists())
        self.assertFalse(any((self.layout.root / m.ACTIVE).glob('journal-*')))

    def test_incomplete_and_rewritten_journal_cannot_claim_validated(self):
        tx, after = self.begin()
        directory = self.layout.root / m.ACTIVE
        latest = sorted(directory.glob('journal-*'))[-1]
        value = json.loads(latest.read_bytes())
        value['phase'] = 'validated'
        latest.write_bytes(m._raw(value))
        with self.assertRaisesRegex(m.CoreSelectionFilesError, 'PHASE_EVIDENCE'):
            self.files.confirm(tx, **self.context)
        latest.write_bytes(m._raw(dict(value, phase='prepared')))
        value = json.loads(latest.read_bytes())
        value['pre_stop']['panel_running'] = False
        latest.write_bytes(m._raw(value))
        with self.assertRaisesRegex(m.CoreSelectionFilesError, 'JOURNAL_REWRITTEN'):
            self.files.recovery_view(**self.context)

    def test_rollback_lost_ack_is_reentrant_but_rejects_new_higher_known_generation(self):
        tx, after = self.begin()
        self.quiesce(tx)
        restored = s._build(APP, TARGET, 2, self.before.slots)
        observation = self.validation(restored)
        rename = m.fs._rename_new
        fired = False
        def lose_ack(parent, name, destination, identity):
            nonlocal fired
            rename(parent, name, destination, identity)
            if name.startswith('rollback-selection.new-') and not fired:
                fired = True
                raise m.fs.CoreStoreError('PUBLISH_FAILED')
        with patch.object(m.fs, '_rename_new', side_effect=lose_ack):
            with self.assertRaises(m.CoreSelectionFilesError):
                self.files.rollback_metadata(tx, observation, **self.context)
        with self.assertRaisesRegex(m.CoreSelectionFilesError, 'RECOVERY_CONFLICT'):
            self.files.rollback_metadata(tx, observation, known_generations=(100,), **self.context)
        self.assertEqual(self.files.rollback_metadata(tx, observation, **self.context), restored)
        self.assertEqual(self.files.read_stable(**self.context), restored)

    def test_no_replace_unavailable_never_uses_overwrite_fallback(self):
        tx, after = self.validated()
        original = (self.layout.root / m.SELECTION).read_bytes()
        with patch.object(m.fs, '_rename_new', side_effect=m.fs.CoreStoreError('UNSUPPORTED')), \
                patch.object(m.os, 'replace') as replace:
            with self.assertRaisesRegex(m.CoreSelectionFilesError, 'UNSUPPORTED'):
                self.files.confirm(tx, **self.context)
        replace.assert_not_called()
        self.assertEqual((self.layout.root / m.SELECTION).read_bytes(), original)
        self.assertTrue((self.layout.root / m.PENDING).exists())

    def test_private_or_public_parent_group_write_refused_without_repair(self):
        self.bootstrap()
        root = self.layout.root
        root.chmod(0o775)
        with self.assertRaises(m.CoreSelectionFilesError): self.files.read_stable(**self.context)
        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o775)
        root.chmod(0o755)
        plan = s.plan_component(self.before, core='sing-box', identity=material(),
                                expected_revision=self.before.revision)
        tx = self.files.begin(plan, expected_revision=self.before.revision, service_snapshot=self.pre)
        (root / m.ACTIVE).chmod(0o755)
        with self.assertRaises(m.CoreSelectionFilesError): self.files.recovery_view(**self.context)
        self.assertEqual(stat.S_IMODE((root / m.ACTIVE).stat().st_mode), 0o755)

    def test_cleanup_is_idempotent_only_for_already_confirmed_journal(self):
        self.bootstrap()
        tx = json.loads((self.layout.root / m.FENCE).read_bytes())['transaction']
        self.assertEqual(self.files.finish_cleanup(tx, **self.context), self.before)
        self.assertEqual(self.files.finish_cleanup(tx, **self.context), self.before)
        self.assertEqual(self.files.read_stable(**self.context), self.before)

    def test_short_journal_write_keeps_valid_prefix_and_requires_fresh_final_intent_for_rollback(self):
        tx, after = self.begin()
        final = self.snapshot(self.before, panel=True, enabled=(False, False))
        write = os.write
        partial = False
        def short_then_fail(fd, raw):
            nonlocal partial
            if not partial:
                partial = True
                return write(fd, raw[:15])
            raise OSError('synthetic journal short write failure')
        with patch.object(m.os, 'write', side_effect=short_then_fail):
            with self.assertRaises(m.CoreSelectionFilesError): self.quiesce(tx, final)
        self.assertTrue(partial)
        self.assertEqual(self.files.recovery_view(**self.context)['phase'], 'prepared')
        stages = list((self.layout.root / m.ACTIVE).glob('journal-stage-*'))
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0].stat().st_size, 15)
        preserved_stage = stages[0].read_bytes()
        restored = s._build(APP, TARGET, after.generation + 1, self.before.slots)
        with self.assertRaisesRegex(m.CoreSelectionFilesError, 'FINAL_INTENT_REQUIRED'):
            self.files.rollback_metadata(tx, self.validation(restored, final), **self.context)
        self.files.rollback_metadata(tx, self.validation(restored, final),
                                     final_intents=final['cores'], **self.context)
        self.assertEqual(self.files.read_stable(**self.context), restored)
        archived = self.layout.root / m.HISTORY / tx
        self.assertEqual((archived / stages[0].name).read_bytes(), preserved_stage)
        self.assertFalse(self.files.recovery_view(**self.context)['rollback']['intents']['sing-box']['enabled'])

    def test_short_rollback_material_write_can_retry_without_adopting_or_deleting_orphan(self):
        tx, after = self.begin()
        self.quiesce(tx)
        restored = s._build(APP, TARGET, 2, self.before.slots)
        write = os.write
        failing_fd = None
        def short_material(fd, raw):
            nonlocal failing_fd
            value = bytes(raw)
            if failing_fd is None and b'"cores"' in value and b'"kind":"core-selection-journal-v1"' not in value:
                failing_fd = fd
                return write(fd, raw[:20])
            if fd == failing_fd:
                raise OSError('synthetic new selector write failure')
            return write(fd, raw)
        with patch.object(m.os, 'write', side_effect=short_material):
            with self.assertRaises(m.CoreSelectionFilesError):
                self.files.rollback_metadata(tx, self.validation(restored), **self.context)
        orphans = list((self.layout.root / m.ACTIVE).glob('rollback-selection.new-*'))
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0].stat().st_size, 20)
        self.assertEqual(self.files.recovery_view(**self.context)['phase'], 'recovery')
        self.files.rollback_metadata(tx, self.validation(restored), **self.context)
        archived = self.layout.root / m.HISTORY / tx
        self.assertTrue((archived / orphans[0].name).exists())
        self.assertEqual(self.files.read_stable(**self.context), restored)

    def test_foreign_pending_raced_at_final_park_is_preserved_at_its_live_name(self):
        tx, after = self.validated()
        rename = m.fs._rename_new
        foreign_raw = b'foreign pending preserve'
        fired = False
        def race(parent, name, destination, identity):
            nonlocal fired
            if name == m.PENDING and not fired:
                fired = True
                foreign = self.layout.root / 'foreign-pending'
                foreign.write_bytes(foreign_raw)
                foreign.chmod(0o644)
                os.replace(foreign, self.layout.root / m.PENDING)
            rename(parent, name, destination, identity)
        with patch.object(m.fs, '_rename_new', side_effect=race):
            with self.assertRaisesRegex(m.CoreSelectionFilesError, 'PENDING_CHANGED'):
                self.files.confirm(tx, **self.context)
        self.assertEqual((self.layout.root / m.PENDING).read_bytes(), foreign_raw)
        self.assertFalse(json.loads((self.layout.root / m.FENCE).read_bytes())['settled'])
        with self.assertRaises(m.CoreSelectionFilesError): self.files.read_stable(**self.context)

    def test_foreign_active_directory_raced_at_archive_is_returned_without_deletion(self):
        tx, after = self.validated()
        rename = m.fs._rename_new
        fired = False
        def race(parent, name, destination, identity):
            nonlocal fired
            if name == m.ACTIVE and not fired:
                fired = True
                active = self.layout.root / m.ACTIVE
                active.rename(self.anchor / 'preserved-owned-active')
                active.mkdir(mode=0o700)
                (active / 'foreign').write_bytes(b'foreign private preserve')
            rename(parent, name, destination, identity)
        with patch.object(m.fs, '_rename_new', side_effect=race):
            with self.assertRaisesRegex(m.CoreSelectionFilesError, 'PRIVATE_CHANGED'):
                self.files.confirm(tx, **self.context)
        self.assertEqual((self.layout.root / m.ACTIVE / 'foreign').read_bytes(), b'foreign private preserve')
        self.assertTrue((self.anchor / 'preserved-owned-active' / 'confirm-selection.old').exists())
        self.assertTrue((self.layout.root / m.PENDING).exists())

    def test_durable_confirmed_cannot_rollback_same_transaction_before_cleanup(self):
        tx, after = self.validated()
        with patch.object(self.files, '_archive_and_clear', side_effect=OSError('synthetic archive failure')):
            with self.assertRaises(m.CoreSelectionFilesError): self.files.confirm(tx, **self.context)
        view = self.files.recovery_view(**self.context)
        self.assertEqual(view['phase'], 'confirmed')
        self.assertEqual(view['location'], 'active')
        self.assertTrue(view['pending'])
        self.assertEqual(view['before'], m._selection_map(self.before))
        self.assertEqual(view['after'], m._selection_map(after))
        public_before = {name: (self.layout.root / name).read_bytes()
                         for name in (m.SELECTION, m.FENCE, m.PENDING)}
        journals_before = {path.name: path.read_bytes()
                           for path in (self.layout.root / m.ACTIVE).glob('journal-*')}
        restored = s._build(APP, TARGET, after.generation + 1, self.before.slots)
        with self.assertRaisesRegex(m.CoreSelectionFilesError, 'PHASE'):
            self.files.rollback_metadata(tx, self.validation(restored), **self.context)
        self.assertEqual({name: (self.layout.root / name).read_bytes() for name in public_before}, public_before)
        self.assertEqual({path.name: path.read_bytes()
                          for path in (self.layout.root / m.ACTIVE).glob('journal-*')}, journals_before)
        self.assertEqual(self.files.recovery_view(**self.context)['phase'], 'confirmed')
        self.assertEqual(self.files.finish_cleanup(tx, **self.context), after)
        self.assertEqual(self.files.read_stable(**self.context), after)
        self.assertEqual(after.slot('sing-box').previous, self.before.slot('sing-box').current)
        self.assertIsNone(self.before.slot('sing-box').previous)

    def test_append_checks_durable_previous_phase_before_publishing_any_journal(self):
        tx, after = self.validated()
        with patch.object(self.files, '_archive_and_clear', side_effect=OSError('synthetic archive failure')):
            with self.assertRaises(m.CoreSelectionFilesError): self.files.confirm(tx, **self.context)
        with self.files._io(write=True) as (scope, root):
            directory, journal, _ = self.files._load(scope, root, tx, APP, TARGET)
            restored = s._build(APP, TARGET, after.generation + 1, self.before.slots)
            journal['rollback'] = dict(selection=m._selection_map(restored),
                validation=self.validation(restored), intents=deepcopy(self.pre['cores']))
            with patch.object(self.files, '_publish_journal') as publish:
                with self.assertRaisesRegex(m.CoreSelectionFilesError, 'JOURNAL_PHASE'):
                    self.files._append(scope, directory, journal, phase='recovery')
            publish.assert_not_called()
        self.assertEqual(self.files.recovery_view(**self.context)['phase'], 'confirmed')

    def test_durable_admitting_can_explicitly_recover_only_with_fresh_final_intents(self):
        self.bootstrap()
        plan = s.plan_component(self.before, core='sing-box', identity=material(),
                                expected_revision=self.before.revision)
        publish = self.files._publish_journal
        def fail_prepared(scope, directory, name, raw):
            if name == 'journal-001.json':
                raise OSError('synthetic prepared journal failure')
            return publish(scope, directory, name, raw)
        with patch.object(self.files, '_publish_journal', side_effect=fail_prepared):
            with self.assertRaises(m.CoreSelectionFilesError):
                self.files.begin(plan, expected_revision=self.before.revision, service_snapshot=self.pre)
        view = self.files.recovery_view(**self.context)
        self.assertEqual(view['phase'], 'admitting')
        self.assertEqual(view['before'], m._selection_map(self.before))
        self.assertEqual(view['pre_stop'], self.pre)
        tx = view['transaction']
        final = self.snapshot(self.before, panel=True, enabled=(False, False))
        restored = s._build(APP, TARGET, plan.after.generation + 1, self.before.slots)
        with self.assertRaisesRegex(m.CoreSelectionFilesError, 'FINAL_INTENT_REQUIRED'):
            self.files.rollback_metadata(tx, self.validation(restored, final), **self.context)
        self.assertEqual(self.files.rollback_metadata(tx, self.validation(restored, final),
                         final_intents=final['cores'], **self.context), restored)
        self.assertEqual(self.files.read_stable(**self.context), restored)
        finished = self.files.recovery_view(**self.context)
        self.assertEqual(finished['phase'], 'cleanup')
        self.assertEqual(finished['rollback']['intents'], final['cores'])
        self.assertEqual(restored.slots, self.before.slots)
        self.assertIsNone(restored.slot('sing-box').previous)

    def test_production_writer_requires_uid_zero_before_any_path_access(self):
        with patch.object(m.os, 'geteuid', return_value=12345), patch.object(m, '_root') as root:
            with self.assertRaisesRegex(m.CoreSelectionFilesError, 'ROOT_REQUIRED'):
                m.SelectionFiles().bootstrap(self.before, writer_observation=self.scan,
                    service_snapshot=self.snapshot(self.before, panel=False, enabled=(False, False)))
        root.assert_not_called()


if __name__ == '__main__':
    unittest.main()
