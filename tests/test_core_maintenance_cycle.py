"""Private temp/flock writer cycles; application/bootstrap verification is fake.

No core, product install, service command, browser, network or production path.
"""
from contextlib import ExitStack
import copy
import fcntl
import json
import os
from pathlib import Path
import pickle
import shutil
import tempfile
import unittest
from unittest.mock import patch

from app import release_tools as t

LOCKS=('.panel.lease','.data.owner.lease','data/runtime/xray/owner.lock',
       'data/runtime/sing-box/owner.lock','data/certificates/.worker.lock')


class MaintenanceCycleTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.base=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-writer-cycle-'))).resolve()
        self.root=self.base/'instance';self.root.mkdir(mode=0o700)
        for name in ('data','data/runtime','data/runtime/xray','data/runtime/sing-box','data/certificates',
                     'releases','releases/old','releases/old/payload'):
            (self.root/name).mkdir(mode=0o700)
        self.payload=self.root/'releases/old/payload'
        self.manifest=dict(schema=1,release_id='old',source_commit='a'*40,files={})
        self.write(self.payload/t.MANIFEST,json.dumps(self.manifest).encode())
        self.ready=self.root/'releases/old/READY.json'
        self.write(self.ready,json.dumps(dict(manifest_sha256=t.digest((self.payload/t.MANIFEST).read_bytes()),
            runtime_key='x86_64-gnu',runtime_tree_sha256='b'*64)).encode())
        self.write(self.root/'CURRENT.json',b'{"release_id":"old","previous_id":null}')
        self.anchor=('old','b'*64)
        self.config=self.base/'fake-root-config.json';self.write(self.config,b'{"anchor":"old"}')
        self.write(self.root/'data/runtime/xray/state2-sentinel',b'state2 unchanged')
        self.write(self.root/'data/runtime/sing-box/config-sentinel',b'config unchanged')
        self.data_bytes={path:path.read_bytes() for path in (self.root/'data').rglob('*') if path.is_file()}
        self.stack.enter_context(patch.object(t,'supported_environment'))
        self.stack.enter_context(patch.object(t,'active_verified',side_effect=self.active))
        self.stack.enter_context(patch.object(t,'_maintenance_anchor',side_effect=lambda root:self.anchor))
        # Explicit fake root-owned bootstrap boundary; tests cannot claim a real
        # privileged installation. Everything in the writer leasing is real.
        self.stack.enter_context(patch.object(t,'_maintenance_cycle_bootstrap',side_effect=self.bootstrap))
    def write(self,path,raw):path.write_bytes(raw);path.chmod(0o600)
    def active(self,root):
        current=json.loads((root/'CURRENT.json').read_bytes());release=root/'releases'/current['release_id']
        return release,current,json.loads((release/'payload'/t.MANIFEST).read_bytes()),json.loads((release/'READY.json').read_bytes())
    def bootstrap(self,root):
        return t._maintenance_cycle_file(self.config,limit=16384,owner=os.geteuid()),self.anchor
    def session(self,action='start'):
        session=t._MaintenanceSession(self.root);self.addCleanup(session.close)
        session.prepare(action);session.execute();return session
    def busy(self,path):
        fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return True
            return False
        finally:os.close(fd)
    def assert_writers(self,locked):
        for name in LOCKS:self.assertEqual(self.busy(self.root/name),locked,name)
    def unchanged(self):self.assertEqual({path:path.read_bytes() for path in self.data_bytes},self.data_bytes)
    def test_ancestor_close_lost_ack_does_not_leak_child_or_reclose_foreign_reuse(self):
        foreign=os.open(self.config,os.O_RDONLY);opened=[];closed=[];reused=[]
        open_fd,close=os.open,os.close
        def observed(*args,**kwargs):
            fd=open_fd(*args,**kwargs);opened.append(fd);return fd
        def lost(fd):
            close(fd);closed.append(fd)
            if not reused:
                reused.append(fd);os.dup2(foreign,fd);raise OSError('close acknowledgement lost')
        try:
            with patch.object(t.os,'open',side_effect=observed),patch.object(t.os,'close',side_effect=lost):
                with self.assertRaises(OSError):
                    with t._maintenance_directory(self.root):pass
            self.assertEqual(closed.count(reused[0]),1,'released number must not be closed again')
            self.assertEqual(os.fstat(reused[0]).st_ino,os.fstat(foreign).st_ino)
            for fd in set(opened)-set(reused):
                with self.assertRaises(OSError):os.fstat(fd)
        finally:
            for fd in set(opened+reused+[foreign]):
                try:close(fd)
                except OSError:pass
    def test_writer_cycle_retains_operation_and_reacquires_in_separate_guard_state(self):
        session=self.session();self.assert_writers(True)
        session.release_writers_for_trial();self.assertEqual(session._state,'trial_writers_released')
        self.assert_writers(False);self.assertTrue(self.busy(self.root/'.operation.lock'))
        other=t._MaintenanceSession(self.root)
        with self.assertRaises(t.ReleaseError):other.prepare('start')
        session.reacquire_writers();session._check_reacquired_writers();self.assert_writers(True)
        self.assertEqual(session._state,'writers_reacquired')
        for callback in (session._check,session.execute,session.release_writers,session.release_writers_for_trial,session.reacquire_writers):
            with self.assertRaises(t.ReleaseError):callback()
        self.unchanged();session.close();self.assert_writers(False)
        self.assertFalse(self.busy(self.root/'.operation.lock'))
    def test_ordinary_release_remains_cheap_and_cannot_reacquire(self):
        session=self.session()
        with patch.object(t,'_maintenance_cycle_snapshot',side_effect=AssertionError('ordinary path must not hash')):
            session.release_writers()
        self.assertEqual(session._state,'writers_released')
        with self.assertRaises(t.ReleaseError):session.reacquire_writers()
        self.assert_writers(False);self.assertTrue(self.busy(self.root/'.operation.lock'));self.unchanged()
    def test_stop_stage_or_missing_data_cannot_establish_checkpoint(self):
        session=self.session('stop')
        with self.assertRaises(t.ReleaseError):session.release_writers_for_trial()
        session.close()
        session=self.session();session._action='stage'
        with self.assertRaises(t.ReleaseError):session.release_writers_for_trial()
        session.close()
        session=self.session();(self.root/'data').rename(self.root/'retained-data')
        with self.assertRaises((t.ReleaseError,OSError)):session.release_writers_for_trial()
        self.assertEqual(session._state,'failed');self.assertFalse(session._valid)
        self.assertTrue(self.busy(self.root/'.operation.lock'))
    def test_checkpoint_not_copyable_serializable_cross_session_or_cross_pid(self):
        session=self.session();session.release_writers_for_trial();checkpoint=session._writer_checkpoint
        for value in (session,checkpoint):
            for function in (copy.copy,copy.deepcopy,pickle.dumps):
                with self.assertRaises(t.ReleaseError):function(value)
        with patch.object(t.os,'getpid',return_value=session._pid+1):
            with self.assertRaises(t.ReleaseError):session.reacquire_writers()
            with self.assertRaises(t.ReleaseError):session.close()
        clone=t._MaintenanceSession(self.root);clone._state='trial_writers_released'
        clone._writer_checkpoint=checkpoint;clone._operation_active=True;clone._operation_proof=session._operation_proof
        with self.assertRaises(t.ReleaseError):clone.reacquire_writers()
        clone.close();self.assertTrue(self.busy(self.root/'.operation.lock'))
        session.reacquire_writers();self.assert_writers(True)
    def test_current_bytes_identity_bootstrap_and_source_drift_fail_before_reacquisition(self):
        for fault in ('current-bytes','current-inode','bootstrap','source'):
            with self.subTest(fault=fault):
                session=self.session();session.release_writers_for_trial()
                current=self.root/'CURRENT.json';original=current.read_bytes()
                if fault=='current-bytes':self.write(current,original+b' ')
                elif fault=='current-inode':
                    self.write(self.root/'replacement',original);os.replace(self.root/'replacement',current)
                elif fault=='bootstrap':self.write(self.config,b'{"anchor":"changed"}')
                else:
                    value=json.loads((self.payload/t.MANIFEST).read_bytes());value['source_commit']='c'*40
                    self.write(self.payload/t.MANIFEST,json.dumps(value).encode())
                with self.assertRaises((t.ReleaseError,OSError)):session.reacquire_writers()
                self.assertEqual(session._state,'failed');self.assertFalse(session._valid);self.assert_writers(False)
                self.assertTrue(self.busy(self.root/'.operation.lock'));session.close()
                self.write(current,original);self.write(self.config,b'{"anchor":"old"}')
                self.write(self.payload/t.MANIFEST,json.dumps(self.manifest).encode())
    def test_data_generation_replacement_rejected_without_adopting_new_tree(self):
        session=self.session();session.release_writers_for_trial()
        (self.root/'data').rename(self.root/'retained-data');(self.root/'data').mkdir(mode=0o700)
        with self.assertRaises((t.ReleaseError,OSError)):session.reacquire_writers()
        self.assertEqual(session._state,'failed');self.assertFalse(session._valid)
        self.assertEqual(list((self.root/'data').iterdir()),[])
        self.assertTrue(self.busy(self.root/'.operation.lock'))
    def test_each_busy_writer_cleans_partial_acquisition_and_never_drops_operation(self):
        for name in LOCKS:
            with self.subTest(name=name):
                session=self.session();session.release_writers_for_trial()
                fd=os.open(self.root/name,os.O_RDWR);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                try:
                    with self.assertRaises(Exception):session.reacquire_writers()
                    self.assertEqual(session._state,'failed');self.assertFalse(session._valid)
                    self.assertTrue(self.busy(self.root/'.operation.lock'))
                finally:os.close(fd)
                self.assert_writers(False);session.close();self.unchanged()
    def test_post_lock_checkpoint_failure_releases_every_writer(self):
        session=self.session();session.release_writers_for_trial();original=t._maintenance_cycle_snapshot;calls=0
        def drift(root):
            nonlocal calls
            calls+=1
            if calls==2:self.write(self.config,b'{"anchor":"post-lock-changed"}')
            return original(root)
        with patch.object(t,'_maintenance_cycle_snapshot',side_effect=drift):
            with self.assertRaises(t.ReleaseError):session.reacquire_writers()
        self.assertEqual(session._state,'failed');self.assert_writers(False)
        self.assertTrue(self.busy(self.root/'.operation.lock'));self.unchanged()
    def test_directory_helper_keeps_create_yield_permissions_and_nofollow_semantics(self):
        path=self.root/'new-private'
        with t._maintenance_directory(path,create=True) as fd:
            self.assertEqual(os.fstat(fd).st_ino,path.stat().st_ino)
            self.assertEqual(path.stat().st_mode&0o777,0o700)
        bad=self.root/'unsafe';bad.mkdir(mode=0o755)
        with self.assertRaises(t.ReleaseError):
            with t._maintenance_directory(bad):pass
        link=self.root/'linked';link.symlink_to(path,target_is_directory=True)
        with self.assertRaises(OSError):
            with t._maintenance_directory(link):pass
        self.assertTrue(link.is_symlink());self.assertEqual(bad.stat().st_mode&0o777,0o755)
    def test_identical_root_replacement_cannot_establish_trial_checkpoint(self):
        session=self.session();retained=self.base/'retained-root'
        self.root.rename(retained);shutil.copytree(retained,self.root,copy_function=shutil.copy2)
        # Byte-identical data/current/app files are not the root inode whose
        # operation is actually locked by this session.
        with self.assertRaises(Exception):session.release_writers_for_trial()
        self.assertEqual(session._state,'failed');self.assertFalse(session._valid)
        self.assertTrue(self.busy(retained/'.operation.lock'))
        self.assertFalse(self.busy(self.root/'.operation.lock'))
        session.close();self.assertFalse(self.busy(retained/'.operation.lock'));self.unchanged()

    def test_operation_closed_behind_flag_cannot_establish_trial_checkpoint(self):
        session=self.session();session._operation.close()
        self.assertTrue(session._operation_active)
        with self.assertRaises((t.ReleaseError,OSError)):session.release_writers_for_trial()
        self.assertEqual(session._state,'failed');self.assertFalse(session._valid)
        self.assert_writers(False);self.unchanged()
    def test_close_attempts_both_stacks_even_when_both_report_errors(self):
        session=self.session();events=[]
        def writer():events.append('writer');raise OSError('writer acknowledgement lost')
        def operation():events.append('operation');raise OSError('operation acknowledgement lost')
        session._writers.callback(writer);session._operation.callback(operation)
        with self.assertRaises(OSError):session.close()
        self.assertEqual(events,['writer','operation']);self.assertEqual(session._state,'closed')
        self.assertFalse(session._operation_active);self.assert_writers(False)
        self.assertFalse(self.busy(self.root/'.operation.lock'))
        session.close();self.assertEqual(events,['writer','operation'])

    def test_closed_writer_stack_revokes_reacquired_guard_immediately(self):
        session=self.session();session.release_writers_for_trial();session.reacquire_writers()
        session._check_reacquired_writers();session._writers.close();self.assert_writers(False)
        with self.assertRaises(t.ReleaseError):session._check_reacquired_writers()
        self.assertFalse(session._valid)
        self.assertTrue(self.busy(self.root/'.operation.lock'));self.unchanged()
    def test_closed_lease_proof_rejects_reused_parent_and_file_descriptor_numbers(self):
        original_open=os.open;ids={};owned=[]
        def observe(*args,**kwargs):
            fd=original_open(*args,**kwargs)
            if args[0]=='.proof.lock':ids.update(file=fd,parent=kwargs['dir_fd'])
            return fd
        with patch.object(t.os,'open',side_effect=observe):
            with t._maintenance_lease(self.root,'.proof.lock',_proof=True) as proof:proof()
        try:
            parent=original_open(self.root,os.O_RDONLY|os.O_DIRECTORY);owned.append(parent)
            os.dup2(parent,ids['parent']);owned.append(ids['parent'])
            source=original_open(self.root/'.proof.lock',os.O_RDWR);owned.append(source)
            os.dup2(source,ids['file']);owned.append(ids['file'])
            self.assertFalse(self.busy(self.root/'.proof.lock'))
            with self.assertRaises(t.ReleaseError):proof()
            self.assertEqual(os.fstat(ids['file']).st_ino,(self.root/'.proof.lock').stat().st_ino)
            self.assertEqual(os.fstat(ids['parent']).st_ino,self.root.stat().st_ino)
        finally:
            for fd in set(owned):os.close(fd)

    def test_terminal_release_after_reacquire_frees_writers_but_retains_operation(self):
        session=self.session();session.release_writers_for_trial();session.reacquire_writers()
        session.release_reacquired_writers();self.assert_writers(False)
        self.assertTrue(self.busy(self.root/'.operation.lock'))
        self.assertEqual(session._state,'recovery_writers_released');self.assertFalse(session._valid)
        self.assertIsNone(session._writer_generation);self.assertIsNone(session._writer_checkpoint)
        session._operation_proof()
        for function in (session._check_reacquired_writers,session.reacquire_writers,session.execute,
                         session.release_writers,session.release_writers_for_trial,session.release_reacquired_writers):
            with self.assertRaises(t.ReleaseError):function()
        self.unchanged();session.close();self.assertFalse(self.busy(self.root/'.operation.lock'))
    def test_terminal_release_guard_or_close_failure_is_failed_and_operation_stays_live(self):
        for failure in ('guard','close'):
            with self.subTest(failure=failure):
                session=self.session();session.release_writers_for_trial();session.reacquire_writers()
                if failure=='guard':
                    self.write(self.config,b'{"anchor":"changed-before-exit"}')
                else:
                    def lost():raise OSError('writer close acknowledgement lost')
                    session._writers.callback(lost)
                with self.assertRaises((t.ReleaseError,OSError)):session.release_reacquired_writers()
                self.assertEqual(session._state,'failed');self.assertFalse(session._valid)
                self.assertIsNone(session._writer_generation);self.assertIsNone(session._writer_checkpoint)
                self.assert_writers(False);self.assertTrue(self.busy(self.root/'.operation.lock'))
                session._operation_proof();self.unchanged();session.close()
                self.assertFalse(self.busy(self.root/'.operation.lock'))
                self.write(self.config,b'{"anchor":"old"}')

    def test_close_attempts_operation_and_finishes_even_when_writer_cleanup_raises(self):
        session=self.session();closed=[]
        def fail():closed.append('writer-close');raise OSError('writer close acknowledgement lost')
        session._writers.callback(fail)
        with self.assertRaises(OSError):session.close()
        self.assertEqual(closed,['writer-close']);self.assertEqual(session._state,'closed')
        self.assertFalse(session._operation_active);self.assertFalse(self.busy(self.root/'.operation.lock'))
        self.assert_writers(False);self.unchanged()


if __name__=='__main__':unittest.main()
