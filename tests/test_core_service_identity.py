"""Fake systemd/proc identity observations only; never invoke a service command."""
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'deploy/system_ops.py'
spec=importlib.util.spec_from_file_location('core_service_identity_ops',SOURCE)
ops=importlib.util.module_from_spec(spec);spec.loader.exec_module(ops)
PID=4321;UID=65534


def fields(**changes):
    value=dict(LoadState='loaded',ActiveState='active',SubState='running',MainPID=str(PID),ControlPID='0',
               ControlGroup='/system.slice/v-ui.service',User='v-ui',Group='v-ui',KillMode='control-group',
               NeedDaemonReload='no',FragmentPath='/etc/systemd/system/v-ui.service',DropInPaths='')
    value.update(changes)
    return value


def encoded(value):return ''.join(key+'='+item+'\n'for key,item in value.items()).encode('ascii')


def proc_stat(ticks=100,comm='v-ui (with spaces)'):
    return (str(PID)+' ('+comm+') '+' '.join(['S']+['0']*18+[str(ticks)]+['0']*4)+'\n').encode()


class ServiceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-proc-identity-'))).resolve()
        self.directory=root/str(PID);self.directory.mkdir(mode=0o700)
        (self.directory/'stat').write_bytes(proc_stat())
        (self.directory/'status').write_bytes(('Name:\tfake\nUid:\t'+str(UID)+'\t'+str(UID)+'\t'+str(UID)+'\t'+str(UID)+'\n').encode())
        (self.directory/'cgroup').write_bytes(b'0::/system.slice/v-ui.service\n')
        # These files simulate proc, not a real process/uid. Preserve all real
        # FD/type/inode behavior; only ownership of the fake proc root/leaf is
        # supplied explicitly so tests never chown or create an OS account.
        self.stack.enter_context(patch.object(ops,'PROC_ROOT',root))
        self.stack.enter_context(patch.object(ops,'_root_owned'))
        real_fstat=os.fstat;identity=self.directory.stat().st_dev,self.directory.stat().st_ino
        def fstat(fd):
            info=real_fstat(fd)
            if (info.st_dev,info.st_ino)==identity:
                return SimpleNamespace(st_mode=info.st_mode,st_uid=UID,st_dev=info.st_dev,st_ino=info.st_ino)
            return info
        self.stack.enter_context(patch.object(ops.os,'fstat',side_effect=fstat))
        with patch.object(ops,'trusted_executable'):
            self.services=ops.Services('systemd')
        self.query=self.stack.enter_context(patch.object(self.services,'_run',return_value=(0,encoded(fields()))))

    def test_fixed_service_real_fields_and_complex_comm_produce_stable_identity(self):
        result=self.services.managed_identity(UID)
        self.assertEqual(result,dict(pid=PID,uid=UID,start_identity='100',cgroup='/system.slice/v-ui.service'))
        self.assertEqual(self.query.call_count,2)
        self.assertTrue(all(call.args[0][-1]=='v-ui.service'for call in self.query.call_args_list))

    def test_uid_must_be_independent_positive_nonroot_integer(self):
        for value in (0,True,-1,2**32,'65534'):
            with self.subTest(uid=value),self.assertRaises(ops.Refused):self.services.managed_identity(value)
        self.query.assert_not_called()

    def test_wrong_real_effective_saved_or_fs_uid_and_duplicate_line_are_rejected(self):
        path=self.directory/'status'
        for position in range(4):
            values=[str(UID)]*4;values[position]='0'
            path.write_text('Uid:\t'+'\t'.join(values)+'\n')
            with self.subTest(position=position),self.assertRaises(ops.Refused):self.services.managed_identity(UID)
        path.write_text(('Uid:\t'+'\t'.join([str(UID)]*4)+'\n')*2)
        with self.assertRaises(ops.Refused):self.services.managed_identity(UID)

    def test_cgroup_mismatch_hybrid_or_missing_records_are_rejected(self):
        path=self.directory/'cgroup'
        for raw in (b'0::/system.slice/other.service\n',b'1:cpu:/system.slice/v-ui.service\n',
                    b'0::/system.slice/v-ui.service\n1:cpu:/other\n',b''):
            path.write_bytes(raw)
            with self.subTest(raw=raw),self.assertRaises(ops.Refused):self.services.managed_identity(UID)

    def test_short_oversized_invalid_pid_or_start_identity_stat_is_rejected(self):
        path=self.directory/'stat'
        for raw in (b'4321 (short) S 0\n',b'x'*8193,proc_stat().replace(b'4321 (',b'4322 (',1),
                    proc_stat(0),proc_stat(-1),proc_stat('not-ticks')):
            path.write_bytes(raw)
            with self.subTest(size=len(raw)),self.assertRaises(ops.Refused):self.services.managed_identity(UID)

    def test_symlink_leaf_and_nonregular_proc_record_refuse(self):
        path=self.directory/'stat';target=self.directory/'foreign'
        path.rename(target);path.symlink_to(target)
        with self.assertRaises((ops.Refused,OSError)):self.services.managed_identity(UID)
        path.unlink();path.mkdir(mode=0o700)
        with self.assertRaises(ops.Refused):self.services.managed_identity(UID)

    def test_zero_or_unknown_pid_and_transitional_state_do_not_become_normal_active(self):
        for update in (dict(MainPID='0'),dict(MainPID='-1'),dict(MainPID='9999999999'),
                       dict(ActiveState='activating',SubState='start'),dict(ActiveState='inactive',SubState='dead',MainPID='0')):
            self.query.return_value=(0,encoded(fields(**update)))
            with self.subTest(update=update),self.assertRaises(ops.Refused):self.services.managed_identity(UID)

    def test_configuration_drift_duplicate_missing_and_unexpected_properties_still_refuse(self):
        variants=[fields(User='root'),fields(Group='root'),fields(DropInPaths='/bad'),
                  fields(FragmentPath='/other'),fields(KillMode='process'),fields(NeedDaemonReload='yes')]
        missing=fields();del missing['ControlPID'];variants.append(missing)
        for value in variants:
            self.query.return_value=(0,encoded(value))
            with self.subTest(value=value),self.assertRaises(ops.Refused):self.services.managed_identity(UID)
        for raw in (encoded(fields())+b'MainPID=4321\n',encoded(fields())+b'Unexpected=0\n'):
            self.query.return_value=(0,raw)
            with self.assertRaises(ops.Refused):self.services.managed_identity(UID)

    def test_same_pid_reused_with_new_start_identity_during_query_is_rejected(self):
        count=[0]
        def query(_):
            count[0]+=1
            if count[0]==2:(self.directory/'stat').write_bytes(proc_stat(200))
            return 0,encoded(fields())
        self.query.side_effect=query
        with self.assertRaises(ops.Refused):self.services.managed_identity(UID)

    def test_systemd_identity_or_configuration_change_between_queries_is_rejected(self):
        for update in (dict(MainPID='4322'),dict(ControlGroup='/other'),dict(NeedDaemonReload='yes')):
            self.query.side_effect=[(0,encoded(fields())),(0,encoded(fields(**update)))]
            with self.subTest(update=update),self.assertRaises(ops.Refused):self.services.managed_identity(UID)

    def test_existing_state_active_inactive_and_transitional_semantics_remain(self):
        self.assertEqual(self.services.state('v-ui.service'),'active')
        self.query.return_value=(0,encoded(fields(ActiveState='inactive',SubState='dead',MainPID='0',ControlGroup='')))
        self.assertEqual(self.services.state('v-ui.service'),'inactive')
        self.query.return_value=(0,encoded(fields(ActiveState='activating',SubState='start')))
        with self.assertRaises(ops.Refused):self.services.state('v-ui.service')


if __name__=='__main__':unittest.main()
