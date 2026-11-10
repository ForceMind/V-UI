"""Pure fixed-worker recovery protocol; fake session and no service execution."""
from contextlib import contextmanager, nullcontext
from copy import deepcopy
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import release_tools
from app.core_updates import maintenance
from deploy import core_maintenance_control as control, ops_worker


class CoreBackupRecoveryProtocolTests(unittest.TestCase):
    def run_protocol(self, commands, *, fail_release=False):
        events=[];trace=[];requests=iter(commands)
        session=SimpleNamespace(prepare=lambda: {'fixture':'context'},
            execute=lambda: {'final_intents':{'fixture':'current'}},
            release_backup_recovery_writers=Mock(side_effect=RuntimeError('inert release') if fail_release else None),
            release_backup_writers=Mock(side_effect=AssertionError('archive release forbidden')),
            backup_core=Mock(side_effect=AssertionError('new archive forbidden')),
            _operation_proof=lambda: trace.append('proof'))
        channel=SimpleNamespace(read=lambda:next(requests),
            send=lambda name,**data:events.append((name,data)))
        def expect(name):
            if channel.read()!={'command':name}:raise ops_worker.WorkerError('inert invalid command')
        channel.expect=expect
        with patch.object(ops_worker,'_quiet_candidate_io',side_effect=lambda:nullcontext()):
            ops_worker._complete_core_backup(channel,session,recovery=True)
        return events,trace,session

    def test_recovery_prepares_quiesces_and_releases_without_creating_archive(self):
        events,trace,session=self.run_protocol([{'command':name} for name in
            ('proceed','release_backup_writers','prove_operation','finish')])
        self.assertEqual([name for name,_ in events],
            ['core_context','core_quiesced','backup_writers_released','operation_held'])
        self.assertEqual(trace,['proof'])
        session.release_backup_recovery_writers.assert_called_once_with()
        session.backup_core.assert_not_called();session.release_backup_writers.assert_not_called()

    def test_recovery_rejects_archive_command_and_never_claims_release_after_failure(self):
        for command in ({'command':'backup_core','snapshot':{}},
                        {'command':'release_backup_writers','path':'private'}, {'command':'finish'}):
            with self.subTest(command=command),self.assertRaises(ops_worker.WorkerError):
                self.run_protocol([{'command':'proceed'},command])
        with self.assertRaises(RuntimeError):
            self.run_protocol([{'command':'proceed'},{'command':'release_backup_writers'}],fail_release=True)

    def test_session_recovery_release_requires_quiesced_and_uses_real_lease_release(self):
        session=object.__new__(maintenance.CoreBackupSession)
        for phase in ('new','backup_checked','backed_up','closed'):
            session._core_phase=phase
            with self.assertRaises(maintenance.CoreMaintenanceError):session.release_backup_recovery_writers()
        session._core_phase='quiesced'
        with patch.object(release_tools._MaintenanceSession,'release_writers_for_trial') as release:
            session.release_backup_recovery_writers()
        release.assert_called_once_with(session)

    def test_explicit_local_root_entry_dispatches_backup_recovery_under_installation_lock(self):
        trace=[];pending=dict(transaction='a'*32,sequence=1,before={'revision':'b'*64})
        authority=SimpleNamespace(backup_recovery=lambda:trace.append('pending') or deepcopy(pending))
        @contextmanager
        def installation(**kwargs):
            trace.append('lock')
            try:yield lambda:None
            finally:trace.append('unlock')
        def loader(receipt):
            self.assertEqual(receipt,hashlib.sha256(b'receipt').hexdigest())
            self.assertEqual(trace,['lock'])
            return SimpleNamespace(CoreAuthority=lambda:authority)
        worker=SimpleNamespace(close=lambda:trace.append('worker-close'))
        task=SimpleNamespace(worker=worker,_close_broker=lambda:None)
        def recover(journal):
            self.assertEqual(journal,pending)
            self.assertIsNone(task.admission_guard)
            self.assertIs(task.backup_authority,authority)
            trace.append('recover')
            return {'outcome':'backup_recovered','panel_running':True}
        task.run_backup_recovery=recover
        ops=SimpleNamespace(TIMEOUT=10,RECEIPT='receipt',CONFIG='config',MAX_JSON=1000,
            trusted_bytes=lambda *args:b'receipt',decode_json=lambda _: {'target':'x86_64-gnu'},
            installation_lock=installation,verify_installation=lambda:('systemd',None),
            Services=lambda *_:SimpleNamespace(deadline=None),_load_core_authority=loader)
        with patch.object(control.os,'geteuid',return_value=0),                 patch.object(control.os,'getpid',return_value=11),                 patch.object(control.os,'getpgrp',return_value=11),                 patch.object(control.os,'getsid',return_value=11),                 patch.object(control,'_modules',side_effect=lambda _:nullcontext(None)),                 patch.object(control,'_start_worker',return_value=worker),                 patch.object(control,'_Switch',return_value=task):
            result=control.recover(ops,_receipt_sha256=hashlib.sha256(b'receipt').hexdigest())
        self.assertEqual(result['outcome'],'backup_recovered')
        self.assertEqual(trace,['lock','pending','recover','worker-close','unlock'])

    def test_local_recovery_entry_refuses_nonroot_unisolated_or_remote_guard_bypass(self):
        with patch.object(control,'_run_task') as run:
            with patch.object(control.os,'geteuid',return_value=501):
                with self.assertRaisesRegex(control.CoreControlError,'ROOT_REQUIRED'):control.recover(None)
            with patch.object(control.os,'geteuid',return_value=0),                     patch.object(control.os,'getpid',return_value=11),                     patch.object(control.os,'getpgrp',return_value=12):
                with self.assertRaisesRegex(control.CoreControlError,'CORE_TASK_ISOLATION'):control.recover(None)
            run.assert_not_called()
        with patch.object(control.os,'geteuid',return_value=0),                 patch.object(control.os,'getpid',return_value=11),                 patch.object(control.os,'getpgrp',return_value=11),                 patch.object(control.os,'getsid',return_value=11):
            with self.assertRaisesRegex(control.CoreControlError,'CORE_LOCAL_RECOVERY_REQUEST'):
                control._run_task(None,None,None,local_recovery=True,admission_guard=object())

    def test_local_core_status_projects_retained_backup_as_pending(self):
        authority=SimpleNamespace(backup_recovery=lambda:{'transaction':'a'*32})
        ops=SimpleNamespace(RECEIPT='receipt',MAX_JSON=1000,
            trusted_bytes=lambda *_:b'receipt',verify_installation=lambda:('systemd',None),
            installation_lock=lambda **_:nullcontext(),
            _load_core_authority=lambda _:SimpleNamespace(CoreAuthority=lambda:authority))
        with patch.object(control.os,'geteuid',return_value=0),                 patch.object(control,'_modules',side_effect=AssertionError('pending uses no selector or worker')):
            self.assertEqual(control.inspect_core_selection(ops,
                _receipt_sha256=hashlib.sha256(b'receipt').hexdigest()),{'outcome':'core_pending'})

    def test_pending_observation_never_starts_worker_or_replays_backup(self):
        pending=dict(transaction='a'*32,sequence=1,before={'revision':'b'*64})
        authority=SimpleNamespace(backup_recovery=lambda:deepcopy(pending))
        files=SimpleNamespace(read_status=Mock(side_effect=AssertionError('must return retained pending first')))
        modules=SimpleNamespace(selection_files=SimpleNamespace(SelectionFiles=lambda:files))
        ops=SimpleNamespace(TIMEOUT=1,MAX_JSON=1000,CONFIG='config',RECEIPT='receipt',
            installation_lock=lambda **_:nullcontext(),verify_installation=lambda:('systemd',None),
            trusted_bytes=lambda *_:b'{}',decode_json=lambda _: {'target':'x86_64-gnu'},
            _load_core_authority=lambda _:SimpleNamespace(CoreAuthority=lambda:authority))
        with patch.object(control.os,'geteuid',return_value=0), \
                patch.object(control,'_modules',side_effect=lambda _:nullcontext(modules)), \
                patch.object(control,'_start_worker',side_effect=AssertionError('no replay')):
            observed=control.observe(ops)
        self.assertEqual(observed['pending'],dict(recovery_kind='backup',transaction='a'*32,
            sequence=1,selection_revision='b'*64))
        self.assertIsNone(observed['app_source'])
        files.read_status.assert_not_called()
