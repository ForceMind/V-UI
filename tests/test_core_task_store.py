"""Private core task schema/namespace using fake owner verification only."""
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]

class CoreTaskStoreTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('_core_store_daemon',ROOT/'deploy/maintenance_daemon.py')
        self.d=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.d)
        temporary=tempfile.TemporaryDirectory(prefix='vui-core-tasks-');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.d.TASKS=self.root/'ordinary';self.d.CORE_TASKS=self.root/'core'
        self.d.TASKS.mkdir();self.d.CORE_TASKS.mkdir()
        @contextmanager
        def directory(path):
            fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
            try:yield fd
            finally:os.close(fd)
        self.ops=SimpleNamespace(trusted_bytes=lambda path,limit:path.read_bytes(),decode_json=json.loads,
            trusted_directory=directory,Refused=RuntimeError)
        self.store=self.d.CoreStore(self.ops)
    def test_core_and_ordinary_ids_are_separate_and_replay_does_not_create_new_record(self):
        identity='a'*32
        row,created=self.store.create_core(identity,'update',{'core':'sing-box'})
        self.assertTrue(created)
        replay,created=self.store.create_core(identity,'update',{'core':'sing-box'})
        self.assertFalse(created);self.assertEqual(replay,row)
        self.assertFalse((self.d.TASKS/(identity+'.json')).exists())
        with self.assertRaises(FileNotFoundError):self.d.Store(self.ops).read(identity)
        with self.assertRaises(ValueError):self.store.create_core(identity,'rollback',{})
    def test_public_projection_removes_private_intent_mapping_and_root_secret(self):
        row,_=self.store.create_core('a'*32,'backup',{})
        row.update(_backup={'name':'private.zip','sha256':'b'*64},parameters={'token':'private'},
                   result={'outcome':'backed_up','backup_id':row['id']})
        public=self.d.public_core_task(row)
        self.assertNotIn('_backup',public);self.assertNotIn('parameters',public)
        self.assertNotIn('private',json.dumps(public))
        row['result']['path']='/private/file'
        with self.assertRaises(ValueError):self.d.public_core_task(row)
    def test_interrupted_queued_or_running_jobs_fail_without_reexecution(self):
        row,_=self.store.create_core('a'*32,'observe',{})
        self.store.interrupt();result=self.store.read(row['id'])
        self.assertEqual(result['state'],'failed');self.assertEqual(result['error'],'CORE_TASK_INTERRUPTED')
        self.assertEqual(len(self.store.all()),1)

    def test_daemon_restart_marks_backup_failed_and_keeps_root_recovery_without_dispatch(self):
        from deploy.core_authority import CoreAuthority
        authority=CoreAuthority(_root=self.root/'authority',_anchor=self.root,_owner=os.geteuid())
        _,token=authority.issue(main_id=7,main_digest='a'*64,scopes=['core_backup'])
        intent,_=authority.reserve(token=token,main_id=7,main_digest='a'*64,request_id='b'*32,
            action='backup',parameters={'selection_revision':'c'*64})
        row,_=self.store.create_core(intent['id'],'backup',intent['parameters'])
        row.update(state='running',phase='changing');self.store.write(row)
        before={'revision':'c'*64}
        facts=dict(application={},data_generation={},selection=before,selection_initialized=True,
                   bundles={},intents={},runtime_schemas={})
        pending=dict(kind='core-backup-recovery-v1',transaction=row['id'],sequence=1,
            installation_id=intent['installation_id'],before=before,context=dict(facts=facts,
            config_sha256='d'*64,services={'v-ui.service':'active','v-ui-http01.socket':'inactive',
            'v-ui-http01.service':'inactive'},trust_sha256=None))
        authority.enter_mutation(task_id=row['id'],plan_sha256='e'*64,backup_recovery=pending)
        original=(authority.root/'authority.json').read_bytes()
        self.store.interrupt()
        result=self.store.read(row['id'])
        self.assertEqual(result['state'],'failed')
        self.assertEqual(result['error'],'CORE_TASK_INTERRUPTED')
        self.assertNotIn('_backup',result)
        self.assertEqual(authority.backup_recovery(),pending)
        self.assertEqual((authority.root/'authority.json').read_bytes(),original)
        self.assertEqual(len(self.store.all()),1)

    def test_observation_budget_and_list_summary_preserve_backup_mapping_capacity(self):
        from unittest.mock import patch
        row,_=self.store.create_core('a'*32,'observe',{})
        row['result']={'outcome':'observed','backups':[{'id':str(i).zfill(32),'created_at':1} for i in range(256)]}
        row.update(state='succeeded',phase='finished');self.store.write(row)
        self.assertEqual(len(self.store.read(row['id'])['result']['backups']),256)
        self.assertNotIn('result',self.d.public_core_task(row,summary=True))
        with patch.object(self.d,'MAX_BACKUPS',1):
            backup,_=self.store.create_core('b'*32,'backup',{})
            backup.update(state='succeeded',phase='finished');self.store.write(backup)
            with self.assertRaises(ValueError):self.store.create_core('c'*32,'backup',{})
            self.assertEqual(self.store.read(backup['id'])['state'],'succeeded')

    def test_visible_queued_row_without_dispatched_child_is_failed_not_replayed(self):
        row,_=self.store.create_core('a'*32,'update',{})
        raw=(self.d.CORE_TASKS/(row['id']+'.json')).read_bytes()
        result=self.d.visible_core_task(row,None)
        self.assertEqual(result['state'],'failed');self.assertEqual(result['error'],'CORE_DISPATCH_UNCONFIRMED')
        self.assertEqual(self.d.visible_core_task(row,(42,row['id'],'core'))['state'],'queued')
        self.assertEqual((self.d.CORE_TASKS/(row['id']+'.json')).read_bytes(),raw)

    def test_directory_sync_lost_ack_leaves_queue_visible_but_never_claims_dispatched(self):
        from unittest.mock import patch
        original=os.fsync
        def failure(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):raise OSError('inert directory ack loss')
            original(fd)
        with patch.object(self.d.os,'fsync',side_effect=failure):
            with self.assertRaises(OSError):self.store.create_core('a'*32,'update',{})
        row=self.store.read('a'*32)
        self.assertEqual(self.d.visible_core_task(row,None)['error'],'CORE_DISPATCH_UNCONFIRMED')
        replay,created=self.store.create_core('a'*32,'update',{})
        self.assertFalse(created);self.assertEqual(replay['id'],row['id'])

    def test_completed_observations_recycle_without_consuming_write_capacity(self):
        with patch.object(self.d,'MAX_TASKS',3),patch.object(self.d,'MAX_CORE_OBSERVATIONS',2):
            write,_=self.store.create_core('a'*32,'update',{})
            for i in range(8):
                row,_=self.store.create_core(format(i,'032x'),'observe',{})
                row.update(state='succeeded',phase='finished');self.store.write(row)
                self.assertLessEqual(len(self.store.all()),3)
            backup,_=self.store.create_core('b'*32,'backup',{})
            backup.update(state='succeeded',phase='finished',_backup={'name':'private.zip'})
            self.store.write(backup)
            self.store.create_core('c'*32,'recover',{})
            self.assertEqual({row['action'] for row in self.store.all()},{'update','backup','recover'})
            self.assertEqual(self.store.read(write['id'])['state'],'queued')
            self.assertEqual(self.store.read(backup['id'])['_backup'],{'name':'private.zip'})

    def test_observation_capacity_never_reclaims_live_or_write_records(self):
        with patch.object(self.d,'MAX_TASKS',2),patch.object(self.d,'MAX_CORE_OBSERVATIONS',1):
            self.store.create_core('a'*32,'observe',{})
            with self.assertRaises(ValueError):self.store.create_core('b'*32,'observe',{})
            write,_=self.store.create_core('c'*32,'update',{})
            write.update(state='failed',phase='finished');self.store.write(write)
            with self.assertRaises(ValueError):self.store.create_core('d'*32,'recover',{})
            self.assertEqual(len(self.store.all()),2)
