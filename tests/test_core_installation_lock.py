"""Temporary real flocks; UID0 metadata is an explicit fixture seam only.

No installer, service, Root system path, product build or network operation.
"""
from contextlib import ExitStack
import os
from pathlib import Path
import stat
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from deploy import system_ops as ops


class InstallationLockProofTests(unittest.TestCase):
    def setUp(self):
        stack=ExitStack();self.addCleanup(stack.close)
        self.root=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-install-proof-'))).resolve()
        self.root.chmod(0o700);self.parent=self.root/'locks';self.parent.mkdir(mode=0o700)
        self.path=self.parent/'install.lock';self.path.write_bytes(b'unchanged original lock');self.path.chmod(0o600)
        self.bad_owner=set();self.fstat=os.fstat;self.stat=os.stat
        def fake_root(info):
            fields=('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
            values={name:getattr(info,name) for name in fields}
            values['st_uid']=99 if (info.st_dev,info.st_ino) in self.bad_owner else 0
            return types.SimpleNamespace(**values)
        # This is only metadata simulation; all open/noFollow/flock/close calls
        # remain actual kernel operations on these private unprivileged files.
        stack.enter_context(patch.object(ops,'INSTALL_LOCK',self.path))
        stack.enter_context(patch.object(ops.os,'fstat',side_effect=lambda fd:fake_root(self.fstat(fd))))
        stack.enter_context(patch.object(ops.os,'stat',side_effect=lambda *args,**kwargs:fake_root(self.stat(*args,**kwargs))))
    def test_default_none_existing_bytes_and_competing_flock_remain_unchanged(self):
        before=self.path.read_bytes()
        with ops.installation_lock() as value:
            self.assertIsNone(value)
            with self.assertRaises(ops.Refused):
                with ops.installation_lock():pass
        self.assertEqual(self.path.read_bytes(),before)
    def test_proof_callable_thread_safe_pid_bound_and_never_relocks(self):
        flock=ops.fcntl.flock;calls=[]
        def tracked(*args):calls.append(args);return flock(*args)
        with patch.object(ops.fcntl,'flock',side_effect=tracked):
            with ops.installation_lock(_proof=True) as proof:
                self.assertIsNone(proof());self.assertIsNone(proof());errors=[]
                def thread_proof():
                    try:self.assertIsNone(proof())
                    except BaseException as error:errors.append(error)
                thread=threading.Thread(target=thread_proof);thread.start();thread.join()
                self.assertEqual(errors,[]);self.assertEqual(len(calls),1)
                with patch.object(ops.os,'getpid',return_value=os.getpid()+1):
                    with self.assertRaises(ops.Refused):proof()
                self.assertIsNone(proof())
            with self.assertRaises(ops.Refused):proof()
    def test_proof_branch_competing_lock_and_exact_mode_link_owner_checks(self):
        with ops.installation_lock(_proof=True) as proof:
            with self.assertRaises(ops.Refused):
                with ops.installation_lock(_proof=True):pass
            proof()
        for fault in ('mode','link','owner'):
            with self.subTest(fault=fault):
                link=self.root/'hardlink'
                if fault=='mode':self.path.chmod(0o644)
                elif fault=='link':os.link(self.path,link)
                else:
                    observer=os.open(self.path,os.O_RDONLY)
                    try:info=self.fstat(observer)
                    finally:os.close(observer)
                    self.bad_owner.add((info.st_dev,info.st_ino))
                with self.assertRaises(ops.Refused):
                    with ops.installation_lock(_proof=True):pass
                if fault=='mode':self.path.chmod(0o600)
                elif fault=='link':link.unlink()
                else:self.bad_owner.clear()
    def test_replaced_lock_original_inode_and_symlink_are_never_adopted(self):
        with self.assertRaises(ops.Refused):
            with ops.installation_lock(_proof=True) as proof:
                self.path.rename(self.parent/'old')
                self.path.write_bytes(b'foreign replacement');self.path.chmod(0o600)
                with self.assertRaises(ops.Refused):proof()
        self.assertEqual(self.path.read_bytes(),b'foreign replacement')
        self.path.unlink();self.path.symlink_to(self.parent/'old')
        with self.assertRaises(OSError):
            with ops.installation_lock(_proof=True):pass
        self.assertTrue(self.path.is_symlink())
    def test_proof_rewalk_rejects_replaced_parent_even_if_bytes_are_identical(self):
        with self.assertRaises(ops.Refused):
            with ops.installation_lock(_proof=True) as proof:
                self.parent.rename(self.root/'preserved-parent');self.parent.mkdir(mode=0o700)
                self.path.write_bytes(b'unchanged original lock');self.path.chmod(0o600)
                proof()
        self.assertEqual(self.path.read_bytes(),b'unchanged original lock')
    def test_parent_permissions_and_ancestor_symlinks_fail_without_repair(self):
        self.parent.chmod(0o777)
        with self.assertRaises(ops.Refused):
            with ops.installation_lock(_proof=True):pass
        self.assertEqual(stat.S_IMODE(self.parent.lstat().st_mode),0o777)
        self.parent.chmod(0o700);self.parent.rename(self.root/'preserved-parent')
        self.parent.symlink_to(self.root/'preserved-parent',target_is_directory=True)
        with self.assertRaises(OSError):
            with ops.installation_lock(_proof=True):pass
    def test_exception_exit_revokes_before_owned_fd_close(self):
        original_close=os.close;observed=[];proof=None
        def close(fd):
            if proof is not None:
                with self.assertRaises(ops.Refused):proof()
                observed.append(fd)
            return original_close(fd)
        with patch.object(ops.os,'close',side_effect=close):
            with self.assertRaisesRegex(RuntimeError,'body failure'):
                with ops.installation_lock(_proof=True) as proof:raise RuntimeError('body failure')
        self.assertTrue(observed)
        with self.assertRaises(ops.Refused):proof()
    def test_close_lost_ack_attempts_all_owned_fds_and_cannot_revive_proof_by_fd_reuse(self):
        opened=[];closed=[];captured={};proof=None
        open_fd=os.open;close=os.close
        def tracked(*args,**kwargs):
            fd=open_fd(*args,**kwargs);opened.append(fd)
            if args[0]==self.path.name:captured.update(file=fd,parent=kwargs['dir_fd'])
            return fd
        def lost(fd):
            close(fd);closed.append(fd)
            if len(closed)==1:raise OSError('close acknowledgement lost')
        # Inject only at context exit; successful proof rewalks are not targets.
        context=ops.installation_lock(_proof=True)
        with patch.object(ops.os,'open',side_effect=tracked):proof=context.__enter__()
        with patch.object(ops.os,'close',side_effect=lost):
            with self.assertRaises(OSError):context.__exit__(None,None,None)
        for fd in set(opened):
            with self.assertRaises(OSError):self.fstat(fd)
        owned=[]
        try:
            directory=open_fd(self.parent,os.O_RDONLY|os.O_DIRECTORY);owned.append(directory)
            os.dup2(directory,captured['parent']);owned.append(captured['parent'])
            source=open_fd(self.path,os.O_RDWR);owned.append(source)
            os.dup2(source,captured['file']);owned.append(captured['file'])
            with self.assertRaises(ops.Refused):proof()
            self.assertEqual(self.fstat(captured['file']).st_ino,self.path.lstat().st_ino)
        finally:
            for fd in set(owned):close(fd)
    def test_inflight_proof_cannot_report_success_after_its_scope_released_the_flock(self):
        entered=threading.Event();resume=threading.Event();outcomes=[]
        original_close=os.close;first=[]
        def close(fd):
            if threading.current_thread().name=='installation-proof-race' and not first:
                first.append(fd);entered.set()
                if not resume.wait(timeout=3):raise AssertionError('test resume deadline')
            return original_close(fd)
        context=ops.installation_lock(_proof=True);proof=context.__enter__()
        def attempt():
            try:proof();outcomes.append('returned')
            except BaseException as error:outcomes.append(error)
        thread=threading.Thread(target=attempt,name='installation-proof-race')
        try:
            with patch.object(ops.os,'close',side_effect=close):
                thread.start();self.assertTrue(entered.wait(timeout=2))
                context.__exit__(None,None,None)
                # An independent open-file description really obtains EX now.
                fd=os.open(self.path,os.O_RDWR|os.O_NOFOLLOW)
                try:ops.fcntl.flock(fd,ops.fcntl.LOCK_EX|ops.fcntl.LOCK_NB)
                finally:original_close(fd)
                resume.set();thread.join(timeout=2)
            self.assertFalse(thread.is_alive());self.assertEqual(len(outcomes),1)
            self.assertIsInstance(outcomes[0],ops.Refused)
        finally:
            resume.set();thread.join(timeout=3)

    def test_invalid_proof_option_is_not_a_public_flag_or_boolean_authority(self):
        for value in (1,'approved',object(),None):
            with self.assertRaises(ops.Refused):
                with ops.installation_lock(_proof=value):pass


if __name__=='__main__':unittest.main()
