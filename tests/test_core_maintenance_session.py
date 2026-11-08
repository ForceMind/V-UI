"""Private temp data, fake M0/M2 materials and fake native executor only.

The operation/writer leases and M13 data/component backup are real. Application
and bootstrap validation is an explicit fixture seam, never Root qualification.
"""
from copy import deepcopy
import hashlib
import io
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

from app import release_tools as tools
from app.core_updates import maintenance as m, selection as s, selection_files as sf
import test_core_maintenance_cycle as cycle
import test_core_backup as materials


class CoreMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.cycle=cycle.MaintenanceCycleTests();self.cycle.setUp();self.addCleanup(self.cycle.doCleanups)
        self.material=materials.CoreBackupTests();self.material.setUp();self.addCleanup(self.material.doCleanups)
        self.root=self.cycle.root;self.data=self.root/'data';self.payload=self.cycle.payload
        self.calls=[]
        manifest=self.cycle.manifest
        manifest.update(kind='release',platform='linux-multi-cpython312',source_commit=materials.NEW_APP,
                        targets=[materials.TARGET])
        source=b"VERSIONS = {'sing-box':'1.14.2','xray':'26.3.27'}\n"
        for name,raw,mode in [('app/services/core_runtime.py',source,0o600)]+[
                ('cores/x86_64/'+core,('fake bundle '+core).encode(),0o700) for core in s.CORES]:
            path=self.payload/name;path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            path.write_bytes(raw);path.chmod(mode)
            manifest['files'][name]=dict(sha256=tools.digest(raw),size=len(raw),mode=mode)
        self.cycle.write(self.payload/tools.MANIFEST,json.dumps(manifest).encode())
        ready=json.loads(self.cycle.ready.read_bytes());ready['manifest_sha256']=tools.digest((self.payload/tools.MANIFEST).read_bytes())
        self.cycle.write(self.cycle.ready,json.dumps(ready).encode())
        with sqlite3.connect(self.data/'v-ui.db') as db:
            db.executescript('CREATE TABLE users(id INTEGER,username TEXT,password_hash TEXT);'
                'CREATE TABLE inbounds(id INTEGER,raw_config TEXT);CREATE TABLE admin_sessions(token_hash TEXT);'
                'CREATE TABLE subscription_grants(id INTEGER,revoked INTEGER);')
        (self.data/'v-ui.db').chmod(0o600)
        self.saved={}
        for core in s.CORES:
            raw=json.dumps(dict(inbounds=[],fake=core),separators=(',',':')).encode();revision=tools.digest(raw)
            self.saved[core]=(raw,revision)
            self.cycle.write(self.data/'runtime'/core/(revision+'.json'),raw)
            self.write_state(core,dict(revision=revision,enabled=core=='sing-box'))
        self.files=sf.SelectionFiles(sf.Layout(self.cycle.base/'root-metadata',os.geteuid(),self.cycle.base))
        self.candidate=self.material.add('sing-box','1.14.3')
    def write_state(self,core,value):self.cycle.write(self.data/'runtime'/core/'state.json',json.dumps(value).encode())
    def read_state(self,core):return json.loads((self.data/'runtime'/core/'state.json').read_bytes())
    def executor(self,argv,**kwargs):
        binary=int(argv[0].rsplit('/',1)[1]);self.assertEqual(os.pread(binary,1024,0),b'NOT AN ELF: synthetic binary x86_64-gnu')
        self.calls.append((list(argv),kwargs,os.fstat(binary).st_ino))
        if '-c' in argv:
            fd=int(argv[-1].rsplit('/',1)[1]);self.assertEqual(os.pread(fd,1024,0),self.saved['sing-box'][0])
    def session(self,**kwargs):
        item=m.CoreMaintenanceSession(self.root,core=kwargs.pop('core','sing-box'),
            expected_selection_revision=kwargs.pop('expected_selection_revision',None),
            _selection_files=kwargs.pop('selection_files',self.files),_catalog=self.material.catalog,_store=self.material.store,
            _executor=kwargs.pop('executor',self.executor),_bundle_anchor=self.cycle.base,**kwargs)
        self.addCleanup(item.close);return item
    def checked(self):
        session=self.session();context=session.prepare();prepared=session.check_core(s._reference_map(self.candidate))
        return session,context,prepared
    def backed(self):
        session,context,prepared=self.checked();final=session.execute()
        snapshot=dict(schema=1,kind='core-backup-closure-v1',selection=context['selection'],
                      intents=final['final_intents'],panel_was_running=True)
        result=session.backup_core(snapshot);return session,context,result
    def test_real_leases_cold_fd_backup_bind_and_one_recovery_cycle_preserve_config_and_stop(self):
        session,context,prepared=self.checked()
        self.assertFalse(context['selection_initialized']);self.assertEqual(len(self.calls),2)
        self.assertEqual(self.calls[0][2],self.calls[1][2]);self.assertTrue(self.cycle.busy(self.root/'.operation.lock'))
        # The last stop before real writer acquisition is the final user intent.
        value=self.read_state('sing-box');value['enabled']=False;self.write_state('sing-box',value)
        final=session.execute();self.cycle.assert_writers(True)
        snapshot=dict(schema=1,kind='core-backup-closure-v1',selection=context['selection'],
                      intents=final['final_intents'],panel_was_running=True)
        result=session.backup_core(snapshot)
        self.assertEqual(tools.file_digest(self.root/'backups'/result['backup_name']),result['backup_sha256'])
        with zipfile.ZipFile(self.root/'backups'/result['backup_name']) as outer:
            with zipfile.ZipFile(io.BytesIO(outer.read('data/snapshot.zip'))) as inner:
                self.assertEqual(set(json.loads(inner.read('runtime/sing-box/state.json'))),{'revision','enabled'})
        bound=session.bind_core_ids(result['backup_sha256'])
        self.assertEqual(bound['intents']['sing-box']['artifact_id'],s.reference_identity(self.candidate))
        for core,(raw,revision) in self.saved.items():
            self.assertEqual((self.data/'runtime'/core/(revision+'.json')).read_bytes(),raw)
            self.assertFalse(self.read_state(core)['enabled']);self.assertEqual(self.read_state(core)['schema'],2)
        session.release_writers_for_trial();self.cycle.assert_writers(False);session._operation_proof()
        session.reacquire_writers();self.cycle.assert_writers(True)
        restored=session.restore_core_ids();self.assertEqual(restored['selection'],context['selection'])
        self.assertEqual(self.read_state('sing-box')['artifact_id'],context['intents']['sing-box']['artifact_id'])
        session.release_reacquired_writers();self.cycle.assert_writers(False)
        self.assertTrue(self.cycle.busy(self.root/'.operation.lock'));session.close()
        self.assertFalse(self.cycle.busy(self.root/'.operation.lock'));self.assertFalse(self.files.layout.root.exists())
    def test_unknown_state2_without_selection_and_cas_fail_without_writer_or_native(self):
        self.write_state('xray',dict(schema=2,artifact_id='b'*64,revision=self.saved['xray'][1],enabled=False))
        session=self.session()
        with self.assertRaises(m.CoreMaintenanceError):session.prepare()
        self.assertEqual(self.calls,[]);self.assertFalse(self.cycle.busy(self.root/'.operation.lock'))
    def test_application_manifest_can_exceed_component_metadata_budget(self):
        bulk=self.payload/'bulk-source';bulk.mkdir()
        for i in range(470):
            self.cycle.write(bulk/(f'{i:04d}'+'x'*48),b'fixture')
        manifest=self.cycle.manifest;manifest['files']=tools.files_in(self.payload)
        raw=json.dumps(manifest).encode();self.assertGreater(len(raw),m.a.MAX_JSON)
        self.cycle.write(self.payload/tools.MANIFEST,raw)
        ready=json.loads(self.cycle.ready.read_bytes());ready['manifest_sha256']=tools.digest(raw)
        self.cycle.write(self.cycle.ready,json.dumps(ready).encode())
        self.assertEqual(tools.verify_payload(self.payload),manifest)
        context=self.session().prepare()
        self.assertEqual(context['application']['manifest_sha256'],tools.digest(raw))

    def test_state2_appearing_before_first_cold_check_is_not_legacy_observation(self):
        session=self.session();context=session.prepare()
        value=self.read_state('sing-box')
        value.update(schema=2,artifact_id=context['intents']['sing-box']['artifact_id'])
        self.write_state('sing-box',value)
        with self.assertRaisesRegex(m.CoreMaintenanceError,'SELECTION_REQUIRED'):
            session.check_core(s._reference_map(self.candidate))
        self.assertEqual(self.calls,[])

    def test_policy_fence_rejects_unapplied_modes_before_any_native_or_stop(self):
        with sqlite3.connect(self.data/'v-ui.db') as db:
            db.execute("ALTER TABLE inbounds ADD COLUMN routing_mode TEXT")
            db.execute("INSERT INTO inbounds VALUES(1,'fake','relay')")
        session=self.session();session.prepare()
        with self.assertRaises(tools.ReleaseError):session.check_core(s._reference_map(self.candidate))
        self.assertEqual(self.calls,[]);self.assertFalse((self.root/'.panel.lease').exists())
        self.assertFalse((self.data/'proxy-state').exists())
    def test_source_changes_during_coldcheck_or_before_final_writer_are_rejected(self):
        def mutate(argv,**kwargs):
            self.executor(argv,**kwargs)
            if '-c' in argv:
                value=self.read_state('sing-box');value['enabled']=False;self.write_state('sing-box',value)
        session=self.session(executor=mutate);session.prepare()
        with self.assertRaises(m.CoreMaintenanceError):session.check_core(s._reference_map(self.candidate))
        self.assertFalse((self.root/'.panel.lease').exists());session.close()
        session,_,_=self.checked()
        self.cycle.write(self.cycle.config,b'changed bootstrap')
        # fake anchor is explicitly replaced to represent independently observed drift.
        self.cycle.anchor=('changed','c'*64)
        with self.assertRaises(tools.ReleaseError):session.execute()
    def test_unused_core_missing_state_remains_missing_and_stopped(self):
        (self.data/'runtime/xray/state.json').unlink()
        session,context,result=self.backed()
        self.assertEqual(context['intents']['xray']['revision'],None)
        session.bind_core_ids(result['backup_sha256'])
        self.assertFalse((self.data/'runtime/xray/state.json').exists())
    def test_backup_before_snapshot_sha_tamper_and_phase_checks_do_not_bind(self):
        session,context,_=self.checked()
        with self.assertRaises(m.CoreMaintenanceError):session.bind_core_ids('a'*64)
        final=session.execute()
        bad=dict(schema=1,kind='core-backup-closure-v1',selection=context['selection'],
                 intents=deepcopy(final['final_intents']),panel_was_running=False)
        bad['intents']['sing-box']['enabled']=False
        with self.assertRaises(m.CoreMaintenanceError):session.backup_core(bad)
        good=deepcopy(bad);good['intents']=final['final_intents'];result=session.backup_core(good)
        with self.assertRaises(m.CoreMaintenanceError):session.bind_core_ids('b'*64)
        archive=self.root/'backups'/result['backup_name'];archive.write_bytes(archive.read_bytes()+b'x')
        with self.assertRaises(m.CoreMaintenanceError):session.bind_core_ids(result['backup_sha256'])
        self.assertNotIn('schema',self.read_state('sing-box'))
    def test_repair_refuses_unknown_identity_and_does_not_overwrite_state(self):
        session,_,result=self.backed();session.bind_core_ids(result['backup_sha256'])
        session.release_writers_for_trial();session.reacquire_writers()
        value=self.read_state('sing-box');value['artifact_id']='c'*64;self.write_state('sing-box',value)
        with self.assertRaises(m.CoreMaintenanceError):session.restore_core_ids()
        self.assertEqual(self.read_state('sing-box'),value)
    def test_closed_or_forged_writer_guard_cannot_create_backup(self):
        session,context,_=self.checked();final=session.execute();session._writers.close()
        snapshot=dict(schema=1,kind='core-backup-closure-v1',selection=context['selection'],
                      intents=final['final_intents'],panel_was_running=False)
        with self.assertRaises(m.CoreMaintenanceError):session.backup_core(snapshot)
        self.assertFalse((self.root/'backups').exists())
    def test_existing_proxy_disappears_after_preread_does_not_initialize_defaults(self):
        from app.services.proxy_store import ProxyStore
        ProxyStore(self.data).read_snapshot()
        original=tools._validate_restore_policy
        def disappear(data,**kwargs):
            (data/'proxy-state').rename(data/'retained-proxy-state')
            (data/'.proxy-state-initialized').rename(data/'retained-proxy-marker')
            return original(data,**kwargs)
        session=self.session();session.prepare()
        with patch.object(tools,'_validate_restore_policy',side_effect=disappear):
            with self.assertRaises(Exception):session.check_core(s._reference_map(self.candidate))
        self.assertEqual(self.calls,[]);self.assertFalse((self.data/'proxy-state').exists())
        self.assertFalse((self.data/'.proxy-state-initialized').exists())
        self.assertTrue((self.data/'retained-proxy-state/state.json').is_file())
    def test_cold_check_native_failure_never_acquires_writers_or_changes_state(self):
        states={core:self.read_state(core) for core in s.CORES}
        def failed(argv,**kwargs):raise RuntimeError('fake binary rejected configuration')
        session=self.session(executor=failed);session.prepare()
        with self.assertRaises(RuntimeError):session.check_core(s._reference_map(self.candidate))
        self.assertFalse((self.root/'.panel.lease').exists())
        self.assertEqual({core:self.read_state(core) for core in s.CORES},states)
        with self.assertRaises(m.CoreMaintenanceError):session.execute()
    def test_policy_is_rechecked_after_native_observation_and_under_final_writers(self):
        session,_,_=self.checked()
        with sqlite3.connect(self.data/'v-ui.db') as db:
            db.execute('ALTER TABLE inbounds ADD COLUMN routing_mode TEXT')
            db.execute("INSERT INTO inbounds VALUES(1,'fake','relay')")
        with self.assertRaises(tools.ReleaseError):session.execute()
        self.assertNotIn('schema',self.read_state('sing-box'))
        with self.assertRaises(m.CoreMaintenanceError):session.release_writers_for_trial()
    def test_xray_coldcheck_uses_fixed_test_command_and_same_verified_fd(self):
        candidate=self.material.add('xray','26.10.21');observed=[]
        def executor(argv,**kwargs):
            fd=int(argv[0].rsplit('/',1)[1]);observed.append((argv,os.fstat(fd).st_ino))
            if argv[1]!='version':
                self.assertEqual(argv[1:-1],['run','-test','-format=json','-c'])
                config=int(argv[-1].rsplit('/',1)[1]);self.assertEqual(os.pread(config,1024,0),self.saved['xray'][0])
        session=self.session(core='xray',executor=executor);session.prepare();session.check_core(s._reference_map(candidate))
        self.assertEqual(len(observed),2);self.assertEqual(observed[0][1],observed[1][1])
        self.assertFalse((self.root/'.panel.lease').exists())
    def test_confirmed_context_cas_and_before_material_qualification_are_independent(self):
        # This seam supplies a fake authenticated M5 selection. Material still
        # uses the real independent fixture catalog/store, never this object.
        probe=self.session();context=probe.prepare();before=probe._before;probe.close()
        slots=tuple(s.CoreSlot(slot.core,self.candidate,slot.current) if slot.core=='sing-box' else slot for slot in before.slots)
        current=s._build(before.app_source,before.target,8,slots)
        self.write_state('sing-box',dict(schema=2,artifact_id=s.reference_identity(self.candidate),
            revision=self.saved['sing-box'][1],enabled=True))
        files=types.SimpleNamespace(read_boot=lambda **kwargs:current)
        wrong=self.session(selection_files=files)
        with self.assertRaises(m.CoreMaintenanceError):wrong.prepare()
        session=self.session(selection_files=files,expected_selection_revision=current.revision)
        facts=session.prepare();self.assertTrue(facts['selection_initialized'])
        self.assertEqual(facts['selection']['revision'],current.revision);session.close()
        directory=self.material.store.layout.root
        # Remove only the fixture's independently verified current material.
        directory.rename(directory.with_name('retained-material'))
        session=self.session(selection_files=files,expected_selection_revision=current.revision)
        with self.assertRaises(Exception):session.prepare()
        self.assertEqual(self.calls,[])
    def test_root_pending_does_not_bypass_shared_admission_but_real_writer_can_backup(self):
        from app.data_owner import DataOwnerError, data_owner
        owner=importlib.import_module('app.data_owner')
        session,context,_=self.checked()
        pending=self.cycle.base/'fake-root-pending';self.cycle.write(pending,b'not an authorization')
        with patch.object(owner,'_CORE_UPDATE_PENDING',pending):
            with self.assertRaises(DataOwnerError):
                with data_owner(self.data):pass
            final=session.execute()
            snapshot=dict(schema=1,kind='core-backup-closure-v1',selection=context['selection'],
                          intents=final['final_intents'],panel_was_running=False)
            result=session.backup_core(snapshot)
            self.assertEqual(tools.file_digest(self.root/'backups'/result['backup_name']),result['backup_sha256'])
            session.bind_core_ids(result['backup_sha256'])
        self.assertEqual(pending.read_bytes(),b'not an authorization')
    @patch.object(m, '_command_group', return_value=501)
    @patch.object(m, '_require_clean_subtree')
    def test_native_executor_bounds_version_and_cleans_failed_fake_child(self, clean, group):
        class Child:
            def __init__(self,raw):
                read,write=os.pipe();os.write(write,raw);os.close(write)
                self.stdout=os.fdopen(read,'rb');self.done=False;self.killed=False
            def wait(self,**kwargs):self.done=True;return 0
            def poll(self):return 0 if self.done else None
            def kill(self):self.killed=True;self.done=True
        child=Child(b'sing-box version 1.14.3\n')
        with patch.object(m.subprocess,'Popen',return_value=child) as popen:
            m._command(['/fake-verified-fd','version'],timeout=1,expected_version='1.14.3')
        self.assertTrue(child.stdout.closed);self.assertFalse(child.killed)
        self.assertEqual(popen.call_args.kwargs['stdin'],m.subprocess.DEVNULL)
        self.assertEqual(popen.call_args.kwargs['stderr'],m.subprocess.DEVNULL)
        self.assertFalse(popen.call_args.kwargs.get('start_new_session',False))
        child=Child(b'sing-box version 1.14.2\n')
        with patch.object(m.subprocess,'Popen',return_value=child):
            with self.assertRaises(m.CoreMaintenanceError):
                m._command(['/fake-verified-fd','version'],timeout=1,expected_version='1.14.3')
        self.assertTrue(child.stdout.closed);self.assertTrue(child.killed)
        self.assertEqual(clean.call_count,2)

    @patch.object(m, '_command_group', return_value=501)
    def test_native_executor_refuses_success_with_live_descendant(self, group):
        class Fatal(BaseException):
            pass
        child=types.SimpleNamespace(wait=lambda **kw:0,poll=lambda:0,stdout=None)
        def abort(pid):
            self.assertEqual(pid,501)
            raise Fatal
        with patch.object(m.subprocess,'Popen',return_value=child), \
                patch.object(m.os,'waitid',return_value=None,create=True), \
                patch.object(m,'_abort_command_group',side_effect=abort):
            with self.assertRaises(Fatal):
                m._command(['/fake-verified-fd','check'],timeout=1)

    def test_writer_closed_during_application_read_cannot_bind_either_state(self):
        session,_,result=self.backed()
        original=session._unchanged_application
        def close_during_read():
            original()
            session._writers.close()
        with patch.object(session,'_unchanged_application',side_effect=close_during_read):
            with self.assertRaises(m.CoreMaintenanceError):
                session.bind_core_ids(result['backup_sha256'])
        for core in s.CORES:
            self.assertNotIn('schema',self.read_state(core))
        self.cycle.assert_writers(False)

    def test_writer_closed_after_first_state_write_cannot_bind_second(self):
        session,_,result=self.backed();original=tools.atomic_json;writes=[]
        def close_after_write(path,value):
            original(path,value)
            writes.append(path)
            session._writers.close()
        with patch.object(tools,'atomic_json',side_effect=close_after_write):
            with self.assertRaises(m.CoreMaintenanceError):
                session.bind_core_ids(result['backup_sha256'])
        self.assertEqual(len(writes),1)
        self.assertEqual(sum(self.read_state(core).get('schema')==2 for core in s.CORES),1)

    def test_reacquired_writer_closed_during_state_read_cannot_repair(self):
        session,_,result=self.backed()
        session.bind_core_ids(result['backup_sha256'])
        session.release_writers_for_trial();session.reacquire_writers()
        original=session._observe_states
        def close_during_read():
            states=original()
            session._writers.close()
            return states
        with patch.object(session,'_observe_states',side_effect=close_during_read):
            with self.assertRaises(m.CoreMaintenanceError):
                session.restore_core_ids()
        self.assertEqual(self.read_state('sing-box')['artifact_id'],s.reference_identity(self.candidate))




if __name__=='__main__':unittest.main()
