"""Pure safety checks for the Actions-only installed-task fixture.

These fakes validate fixture failure/ordering only. They do not execute Root,
systemd, actual product cores, an installed daemon or an HTTP identity.
"""
from contextlib import ExitStack
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_installed_remote_fixture', ROOT/'tests/core_remote_tasks_installed.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class InstalledRemoteFixtureTests(unittest.TestCase):
    def test_root_entry_refuses_local_execution_before_loading_product_modules(self):
        with patch.object(fixture.os, 'geteuid', return_value=501), patch.object(fixture, 'installed') as load:
            with self.assertRaises(ValueError): fixture.main()
        load.assert_not_called()

    def test_service_uid_client_keeps_private_request_off_argv_environment_and_logs(self):
        secret = 'private-root-bearer'
        child = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO('{"accepted":true,"value":{"id":"a"}}\n'),
                                poll=lambda: 0)
        ops = SimpleNamespace(PYTHON=Path('/fixed/python'), ENV={'LANG':'C'})
        account = SimpleNamespace(pw_uid=1001, pw_gid=1002)
        with patch.object(fixture.subprocess, 'Popen', return_value=child) as spawn, ExitStack() as stack:
            peer = fixture.Peer(ops, account, stack)
            with patch('select.select', return_value=([child.stdout], [], [])):
                self.assertEqual(peer.request({'token':secret}), {'id':'a'})
            self.assertIn(secret, child.stdin.getvalue())
        args, options = spawn.call_args
        self.assertNotIn(secret, repr(args)+repr(options))
        self.assertEqual((options['user'], options['group'], options['extra_groups']), (1001,1002,[]))
        self.assertIs(options['stderr'], fixture.subprocess.DEVNULL)

    def test_unknown_submit_ack_is_rejected_without_retry(self):
        child = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO('{"accepted":false}\n'), poll=lambda: 0)
        with patch.object(fixture.subprocess, 'Popen', return_value=child), ExitStack() as stack:
            peer = fixture.Peer(SimpleNamespace(PYTHON='/python', ENV={}), SimpleNamespace(pw_uid=1,pw_gid=2), stack)
            with patch('select.select', return_value=([child.stdout], [], [])), self.assertRaises(ValueError):
                peer.request({'operation':'core_submit', 'request_id':'nonce'})
            self.assertEqual(len(child.stdin.getvalue().splitlines()), 1)

    def test_failed_task_cannot_be_reported_as_success_or_resubmitted(self):
        peer = SimpleNamespace(request=Mock(return_value={'id':'a'*32,'state':'failed','phase':'finished'}))
        with patch.object(fixture.time, 'sleep'), self.assertRaises(ValueError):
            fixture.wait_task(peer, {'id':'a'*32,'state':'queued'})
        self.assertEqual(peer.request.call_args.args[0], {'protocol':1,'operation':'core_get','task_id':'a'*32})
        self.assertEqual(peer.request.call_count, 1)

    def test_mutation_transport_failure_is_never_retried(self):
        child = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO('{"accepted":false,"transport":true}\n'), poll=lambda: 0)
        with patch.object(fixture.subprocess, 'Popen', return_value=child), ExitStack() as stack:
            peer = fixture.Peer(SimpleNamespace(PYTHON='/python', ENV={}), SimpleNamespace(pw_uid=1,pw_gid=2), stack)
            with patch('select.select', return_value=([child.stdout], [], [])), self.assertRaises(fixture.ReadTransportUnavailable):
                peer.request({'operation':'core_submit'})
            self.assertEqual(len(child.stdin.getvalue().splitlines()), 1)

    def test_restart_does_not_retry_daemon_state_refusal(self):
        peer = SimpleNamespace(request=Mock(side_effect=ValueError('fixed refusal')))
        with patch.object(fixture, 'property_value', side_effect=['1','2']), \
                patch.object(fixture.subprocess, 'run'), self.assertRaises(ValueError):
            fixture.restart(peer, [{'id':'a'*32}])
        self.assertEqual(peer.request.call_count, 1)

    def test_writer_conflict_recovery_releases_actual_test_lease_before_dispatch(self):
        identity, revision = 'a'*32, 'b'*64
        failed = {'id':identity,'state':'failed','phase':'finished','error':'CORE_TASK_FAILED'}
        journal = {'transaction':identity,'sequence':1,'before':{'revision':revision}}
        pending = dict(recovery_kind='backup',transaction=identity,sequence=1,selection_revision=revision)
        recovered = {'id':'c'*32,'state':'succeeded','phase':'finished',
                     'result':dict(outcome='backup_recovered',selection_revision=revision,panel_running=True)}
        authority = SimpleNamespace(backup_recovery=Mock(side_effect=[journal,journal,journal,None,None]),
                                    admission=Mock(return_value={'phase':'mutating'}))
        ops = SimpleNamespace(trusted_bytes=lambda *args:b'{}', decode_json=lambda raw:{})
        released = []
        dispatched = []
        def request(value, *, accepted=True):
            dispatched.append((value,accepted))
            if value.get('action')=='recover' and accepted:
                self.assertTrue(released, 'Recover dispatched before writer lease release')
                return recovered
            if value.get('operation')=='core_observe': return {'id':'d'*32,'state':'queued'}
            if value.get('operation')=='core_get': return failed
            return failed
        peer = SimpleNamespace(request=Mock(side_effect=request))
        def complete(peer, row, *, state='succeeded'):
            if row.get('id')=='d'*32: return {'result':{'pending':pending}}
            return failed if state=='failed' else recovered
        submission = dict(protocol=1,operation='core_submit',token='secret',main_id=7,main_digest='e'*64,
                          request_id='f'*32,action='backup',parameters={'selection_revision':revision})
        with ExitStack() as stack, patch.object(fixture,'writer_peer',return_value=lambda:released.append(True)), \
                patch.object(fixture,'wait_task',side_effect=complete), patch.object(fixture,'restart') as restart, \
                patch.object(fixture,'property_value',side_effect=['inactive','inactive','inactive','active','active']):
            self.assertEqual(fixture.interrupted_backup(ops,None,authority,peer,submission,stack), recovered)
        self.assertEqual(restart.call_count,2)
        self.assertTrue(any(not accepted and row.get('parameters',{}).get('pending_sequence')==2
                            for row,accepted in dispatched))
        self.assertEqual(authority.admission.call_args.kwargs, {'task_id':identity,'live':False})


    def test_stopped_panel_fixture_leaves_pending_for_real_root_menu_without_socket_recovery(self):
        identity,revision='a'*32,'b'*64
        failed=dict(id=identity,state='failed',phase='finished',error='CORE_TASK_FAILED')
        journal=dict(transaction=identity,sequence=1,before={'revision':revision})
        pending=dict(recovery_kind='backup',transaction=identity,sequence=1,selection_revision=revision)
        authority=SimpleNamespace(backup_recovery=Mock(return_value=journal),
            admission=Mock(return_value={'phase':'mutating'}))
        ops=SimpleNamespace(trusted_bytes=lambda *args:b'{}',decode_json=lambda _: {})
        peer=SimpleNamespace(request=Mock(side_effect=[{'id':identity},failed,{'id':'d'*32}]))
        request=dict(action='backup',parameters={'selection_revision':revision})
        release=Mock()
        with ExitStack() as stack,patch.object(fixture,'writer_peer',return_value=release), \
                patch.object(fixture,'wait_task',side_effect=[failed,{'result':{'pending':pending}}]), \
                patch.object(fixture,'restart'),patch.object(fixture,'property_value',return_value='inactive'):
            self.assertEqual(fixture.interrupted_backup(ops,None,authority,peer,request,stack,leave_pending=True),failed)
        release.assert_called_once_with()
        self.assertFalse(any(row.args[0].get('action')=='recover' for row in peer.request.call_args_list))
        self.assertEqual(authority.backup_recovery.call_count,2)


if __name__ == '__main__':
    unittest.main()
