"""Closed private worker protocol with fake session and temporary OS pipes.

The isolated loader test only imports copied stdlib Python sources. No core,
service, product install, network, Root metadata or candidate execution occurs.
"""
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from deploy import ops_worker as w


def command(name,**fields):return dict(command=name,**fields)


class FakeCoreSession:
    def __init__(self,root,trace,**kwargs):
        self.trace=trace;self.closed=0;self._operation_proof=lambda:self.trace.append('proof')
    def prepare(self):self.trace.append('context');return dict(application=dict(app_source='a'*40,target='x86_64-gnu'))
    def check_core(self,value):self.trace.append('coldcheck');return dict(candidate=value)
    def execute(self):self.trace.append('execute');return dict(final_intents={})
    def backup_core(self,value):self.trace.append('backup');return dict(backup_name='private.zip',backup_sha256='a'*64)
    def bind_core_ids(self,value):self.trace.append('bind');return dict(selection={})
    def release_writers_for_trial(self):self.trace.append('trial_release')
    def reacquire_writers(self):self.trace.append('reacquire')
    def _check_reacquired_writers(self):self.trace.append('writer_guard')
    def restore_core_ids(self):self.trace.append('repair');return dict(selection={})
    def release_reacquired_writers(self):self.trace.append('recovery_release')
    def close(self):self.trace.append('close');self.closed+=1


class CorePrepareWorkerTests(unittest.TestCase):
    def request(self):return dict(protocol=1,action='core_prepare',core='sing-box',expected_selection_revision=None)
    def prefix(self):return [command('check_core',candidate={'kind':'fake-reference'}),command('proceed'),
        command('backup_core',snapshot={'kind':'fake-snapshot'}),command('bind_core_ids',backup_sha256='a'*64),
        command('release_writers_for_trial')]
    def run_worker(self,commands,*,request=None,close_error=False,guard_error=False,proof_result=None,uid=501,
                   timeout=1,hold=False):
        trace=[];sessions=[];read,write=os.pipe();output=io.StringIO()
        def factory(root,**kwargs):
            instance=FakeCoreSession(root,trace,**kwargs)
            if close_error:
                def close():instance.closed+=1;trace.append('close');raise RuntimeError('close lost ack')
                instance.close=close
            if guard_error:
                def guard():trace.append('writer_guard');raise RuntimeError('revoked guard')
                instance._check_reacquired_writers=guard
            if proof_result is not None:instance._operation_proof=lambda:proof_result
            sessions.append(instance);return instance
        @contextmanager
        def quiet():yield
        os.write(write,(''.join(json.dumps(item)+'\n' for item in [request or self.request()]+commands)).encode())
        if not hold:os.close(write);write=None
        try:
            with patch.object(w,'_load_core_session',return_value=factory),patch.object(w,'_quiet_candidate_io',new=quiet), \
                    patch.object(w.os,'geteuid',return_value=uid),patch.object(w.os,'umask'):
                status=w._run(Path('/explicit-fake-root'),types.SimpleNamespace(),read,output,timeout=timeout)
        finally:
            os.close(read)
            if write is not None:os.close(write)
        return status,[json.loads(line) for line in output.getvalue().splitlines()],trace,sessions
    def test_two_step_source_context_precedes_candidate_and_success_finish(self):
        status,events,trace,sessions=self.run_worker(self.prefix()+[command('prove_operation'),command('finish')])
        self.assertEqual(status,0)
        self.assertEqual([v['event'] for v in events],['core_context','prepared','core_quiesced','core_backed_up',
            'core_ids_bound','trial_writers_released','operation_held'])
        self.assertEqual(trace,['context','coldcheck','execute','backup','bind','trial_release','proof','close'])
        self.assertEqual(sessions[0].closed,1)
    def test_abort_while_root_prepares_material_never_coldchecks_or_stops(self):
        status,events,trace,_=self.run_worker([command('finish')])
        self.assertEqual(status,0);self.assertEqual([v['event'] for v in events],['core_context'])
        self.assertEqual(trace,['context','close'])
    def test_recovery_guard_repair_terminal_release_and_real_proof_order(self):
        status,events,trace,_=self.run_worker(self.prefix()+[command(name) for name in (
            'prove_operation','reacquire_writers','restore_core_ids','release_reacquired_writers','prove_operation','finish')])
        self.assertEqual(status,0)
        self.assertEqual([v['event'] for v in events][-5:],['operation_held','writers_reacquired','core_ids_restored',
                                                           'recovery_writers_released','operation_held'])
        self.assertEqual(trace.count('execute'),1)
        self.assertLess(trace.index('writer_guard'),trace.index('repair'))
        self.assertLess(trace.index('repair'),trace.index('recovery_release'))
    def test_request_is_pathless_closed_and_not_a_public_action(self):
        from app import release_tools
        self.assertNotIn('core_prepare',release_tools._MaintenanceSession.ACTIONS)
        variants=[dict(self.request(),candidate={}),dict(self.request(),path='/tmp/fake'),
                  dict(self.request(),protocol=True),dict(self.request(),expected_selection_revision='BAD'),
                  dict(self.request(),core='other')]
        for value in variants:
            with self.subTest(value=value):
                status,events,trace,sessions=self.run_worker([],request=value)
                self.assertEqual(status,1);self.assertEqual(trace,[]);self.assertEqual(sessions,[])
                self.assertEqual(events,[dict(event='error',code='MAINTENANCE_REFUSED')])
    def test_every_phase_rejects_arbitrary_paths_flags_and_command_reordering(self):
        cases=[[],[command('proceed')],self.prefix()[:2]+[command('bind_core_ids',backup_sha256='a'*64)],
            self.prefix()+[command('release_writers')],self.prefix()+[command('restore_core_ids')],
            self.prefix()+[command('reacquire_writers'),command('finish')],
            self.prefix()+[command('reacquire_writers'),command('prove_operation')],
            self.prefix()+[command('reacquire_writers'),command('restore_core_ids'),command('execute')],
            self.prefix()+[command('reacquire_writers'),command('restore_core_ids'),command('release_reacquired_writers'),command('reacquire_writers')]]
        for values in cases:
            with self.subTest(values=values):
                status,events,trace,sessions=self.run_worker(values)
                self.assertEqual(status,1);self.assertEqual(events[-1]['event'],'error')
                self.assertLessEqual(trace.count('execute'),1);self.assertEqual(sessions[0].closed,1)
        for index in range(len(self.prefix())):
            values=self.prefix();values[index]=dict(values[index],approved=True)
            status,events,trace,_=self.run_worker(values+[command('finish')])
            self.assertEqual(status,1);self.assertEqual(events[-1]['event'],'error')
    def test_guard_proof_deadline_eof_and_close_failure_never_report_success(self):
        for options,values in [(dict(guard_error=True),self.prefix()+[command('reacquire_writers')]),
            (dict(proof_result=True),self.prefix()+[command('prove_operation')]),
            (dict(close_error=True),[command('finish')]),
            (dict(hold=True,timeout=.03),[])]:
            status,events,trace,sessions=self.run_worker(values,**options)
            self.assertEqual(status,1);self.assertEqual(events[-1],dict(event='error',code='MAINTENANCE_REFUSED'))
            self.assertEqual(sessions[0].closed,1)
        status,events,trace,sessions=self.run_worker([],uid=0)
        self.assertEqual(status,1);self.assertEqual(trace,[])
    def test_fixed_control_closure_imports_in_isolated_python_without_payload_path(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='vui-core-fixed-closure-') as temporary:
            directory=Path(temporary)
            source={'ops_worker.py':root/'deploy/ops_worker.py','release_tools.py':root/'app/release_tools.py',
                'data_owner.py':root/'app/data_owner.py','upstream_profile.py':root/'app/services/upstream_profile.py',
                'inbound_policy.py':root/'app/services/inbound_policy.py','proxy_store.py':root/'app/services/proxy_store.py',
                'certificate_errors.py':root/'app/certificates/errors.py'}
            for name in ('dns_store','cloudflare','dns_journal','dns_backup'):
                source[name+'.py']=root/'app/certificates'/(name+'.py')
            for module,name in w.CORE_IMPLEMENTATIONS:source[name]=root/Path(*module.split('.')).with_suffix('.py')
            for name,path in source.items():shutil.copyfile(path,directory/name)
            script="""import importlib.util,sys
spec=importlib.util.spec_from_file_location('fixed_worker',sys.argv[1]);w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
t=w._load_release_tools();cls=w._load_core_session(t)
assert cls.__module__=='app.core_updates.maintenance'
assert all(sys.modules[n].__path__==[] for n in ('app','app.services','app.certificates','app.core_updates'))
assert not any(n in sys.modules for n in ('psutil','main','app.services.core_runtime'))
try:w._load_core_session(t)
except w.WorkerError:pass
else:raise AssertionError('namespace reuse accepted')
"""
            result=subprocess.run([sys.executable,'-I','-S','-c',script,str(directory/'ops_worker.py')],
                                  stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr.decode())
    def test_actual_session_end_to_end_pipe_backup_binding_and_repair(self):
        # App/bootstrap/native execution remain explicit fake boundaries; all
        # leases, source observations, M13 archive and state-ID writes are real.
        import test_core_maintenance_session as fixture
        from app.core_updates import selection as selection
        case=fixture.CoreMaintenanceTests();case.setUp();self.addCleanup(case.doCleanups)
        read,write=os.pipe();events=[];context={}
        class RootReplies(io.StringIO):
            def write(self,raw):
                event=json.loads(raw);events.append(event);name=event['event'];reply=None
                if name=='core_context':
                    context.update(event);reply=command('check_core',candidate=selection._reference_map(case.candidate))
                elif name=='prepared':reply=command('proceed')
                elif name=='core_quiesced':reply=command('backup_core',snapshot=dict(schema=1,kind='core-backup-closure-v1',
                    selection=context['selection'],intents=event['final_intents'],panel_was_running=True))
                elif name=='core_backed_up':reply=command('bind_core_ids',backup_sha256=event['backup_sha256'])
                else:
                    steps={'core_ids_bound':'release_writers_for_trial','trial_writers_released':'reacquire_writers',
                        'writers_reacquired':'restore_core_ids','core_ids_restored':'release_reacquired_writers',
                        'recovery_writers_released':'prove_operation','operation_held':'finish'}
                    if name in steps:reply=command(steps[name])
                if reply is not None:os.write(write,(json.dumps(reply)+'\n').encode())
                return super().write(raw)
        @contextmanager
        def quiet():yield
        output=RootReplies();os.write(write,(json.dumps(self.request())+'\n').encode())
        try:
            with patch.object(w,'_load_core_session',return_value=lambda root,**kwargs:case.session()), \
                    patch.object(w,'_quiet_candidate_io',new=quiet):
                status=w._run(case.root,types.SimpleNamespace(),read,output,timeout=10)
        finally:os.close(read);os.close(write)
        self.assertEqual(status,0,[event['event'] for event in events])
        self.assertEqual([event['event'] for event in events],['core_context','prepared','core_quiesced','core_backed_up',
            'core_ids_bound','trial_writers_released','writers_reacquired','core_ids_restored',
            'recovery_writers_released','operation_held'])
        for core,(raw,revision) in case.saved.items():
            self.assertEqual((case.data/'runtime'/core/(revision+'.json')).read_bytes(),raw)
            self.assertEqual(case.read_state(core)['artifact_id'],context['intents'][core]['artifact_id'])
        self.assertEqual(len(list((case.root/'backups').glob('*.zip'))),1)
        self.assertFalse(case.cycle.busy(case.root/'.operation.lock'))


if __name__=='__main__':unittest.main()
