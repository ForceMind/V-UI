"""Private worker protocol, fake sessions and temporary real session flocks.

No daemon, system service, core executable, product archive/build or public
maintenance action is created. Root/bootstrap application checks are fake.
"""
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import queue
import signal
import threading
import time
import types
import unittest
from unittest.mock import patch

from deploy import ops_worker as w
from app import release_tools as tools
import test_core_maintenance_cycle as cycle_fixture


def command(name,**extra):return dict(command=name,**extra)


class FakeSession:
    def __init__(self,root,trace,*,proof_result=None,proof_marker='callable',close_error=False,guard_error=False,slow=False):
        self.trace=trace;self.phase='new';self.closed=0;self.close_error=close_error;self.guard_error=guard_error
        def proof():
            self.trace.append('proof')
            if self.phase not in ('trial_writers_released','recovery_writers_released'):raise RuntimeError('wrong proof phase')
            if slow:time.sleep(0.03)
            return proof_result
        if proof_marker=='callable':self._operation_proof=proof
        elif proof_marker!='missing':self._operation_proof=proof_marker
    def prepare(self,action,**kwargs):self.trace.append('prepare:'+action);self.phase='prepared'
    def execute(self):self.trace.append('execute');self.phase='done';return {}
    def release_writers(self):self.trace.append('ordinary_release');self.phase='writers_released'
    def release_writers_for_trial(self):self.trace.append('trial_release');self.phase='trial_writers_released'
    def reacquire_writers(self):self.trace.append('reacquire');self.phase='writers_reacquired'
    def _check_reacquired_writers(self):
        self.trace.append('writer_guard')
        if self.phase!='writers_reacquired' or self.guard_error:raise RuntimeError('invalid writer guard')
    def release_reacquired_writers(self):self.trace.append('recovery_release');self.phase='recovery_writers_released'
    def close(self):
        self.closed+=1;self.trace.append('close');self.phase='closed'
        if self.close_error:raise RuntimeError('private cleanup diagnostic')


class ProtocolOutput(io.StringIO):
    def __init__(self,trace):super().__init__();self.trace=trace
    def write(self,raw):
        self.trace.append('event:'+json.loads(raw)['event']);return super().write(raw)


class WorkerCycleProtocolTests(unittest.TestCase):
    def run_worker(self,commands,*,action='start',raw=None,hold_open=False,timeout=1,uid=501,**options):
        trace=[];sessions=[];read,write=os.pipe();out=ProtocolOutput(trace)
        def factory(root):
            session=FakeSession(root,trace,**options);sessions.append(session);return session
        @contextmanager
        def quiet():
            trace.append('quiet_enter')
            try:yield
            finally:trace.append('quiet_exit')
        request=dict(protocol=1,action=action)
        if action=='stage':request.update(path='/private-fixture.zip',sha256='a'*64)
        data=(json.dumps(request)+'\n'+''.join(json.dumps(item)+'\n' for item in commands)).encode() if raw is None else raw
        os.write(write,data)
        if not hold_open:os.close(write);write=None
        previous={value:signal.getsignal(value) for value in (signal.SIGALRM,signal.SIGTERM,signal.SIGINT)}
        try:
            with patch.object(w.os,'geteuid',return_value=uid),patch.object(w.os,'umask'), \
                    patch.object(w,'_quiet_candidate_io',new=quiet):
                status=w._run(Path('/fake-private-root'),types.SimpleNamespace(_MaintenanceSession=factory),read,out,timeout=timeout)
        finally:
            os.close(read)
            if write is not None:os.close(write)
        self.assertEqual({value:signal.getsignal(value) for value in previous},previous)
        events=[json.loads(line) for line in out.getvalue().splitlines()]
        return status,events,trace,sessions
    def test_legacy_roundtrip_and_stage_order_unchanged(self):
        status,events,trace,sessions=self.run_worker([command('proceed'),command('release_writers'),command('finish')])
        self.assertEqual(status,0);self.assertEqual([item['event'] for item in events],['prepared','done','writers_released'])
        self.assertEqual(trace,['prepare:start','event:prepared','quiet_enter','execute','quiet_exit','event:done',
                                'ordinary_release','event:writers_released','close'])
        self.assertEqual(sessions[0].closed,1)
        status,events,trace,_=self.run_worker([command('proceed'),command('finish')],action='stage')
        self.assertEqual(status,0);self.assertEqual([item['event'] for item in events],['prepared','done'])
        self.assertNotIn('trial_release',trace)
    def test_opt_in_cycle_closed_state_table_and_actual_proof_calls(self):
        commands=[command(name) for name in ('proceed','release_writers_for_trial','prove_operation','prove_operation',
                    'reacquire_writers','release_reacquired_writers','prove_operation','finish')]
        status,events,trace,sessions=self.run_worker(commands)
        self.assertEqual(status,0)
        self.assertEqual([item['event'] for item in events],['prepared','done','trial_writers_released',
            'operation_held','operation_held','writers_reacquired','recovery_writers_released','operation_held'])
        self.assertEqual(trace.count('execute'),1);self.assertEqual(trace.count('proof'),3)
        self.assertLess(trace.index('reacquire'),trace.index('writer_guard'))
        self.assertLess(trace.index('writer_guard'),trace.index('event:writers_reacquired'))
        self.assertEqual(sessions[0].closed,1)
    def test_success_trial_can_finish_without_reacquiring_or_reexecuting(self):
        status,events,trace,_=self.run_worker([command(name) for name in ('proceed','release_writers_for_trial','prove_operation','finish')])
        self.assertEqual(status,0);self.assertNotIn('reacquire',trace);self.assertEqual(trace.count('execute'),1)
    def test_wrong_phase_unknown_fields_commands_and_extra_root_objects_are_refused(self):
        variants=[['prove_operation'],['reacquire_writers'],['finish'],
            ['release_writers','prove_operation'],
            ['release_writers_for_trial','release_writers'],
            ['release_writers_for_trial','reacquire_writers','reacquire_writers'],
            ['release_writers_for_trial','reacquire_writers','prove_operation'],
            ['release_writers_for_trial','reacquire_writers','finish'],
            ['release_writers_for_trial','reacquire_writers','release_reacquired_writers','reacquire_writers'],
            ['release_writers_for_trial','restore'],['release_writers_for_trial','execute']]
        for names in variants:
            with self.subTest(names=names):
                status,events,trace,sessions=self.run_worker([command('proceed')]+[command(name) for name in names])
                self.assertEqual(status,1);self.assertEqual(events[-1],dict(event='error',code='MAINTENANCE_REFUSED'))
                self.assertEqual(trace.count('execute'),1);self.assertEqual(sessions[0].closed,1)
        for extra in (dict(pid=123),dict(path='/private'),dict(approved=True),dict(callback='exec')):
            status,events,trace,_=self.run_worker([command('proceed'),command('release_writers_for_trial',**extra)])
            self.assertEqual(status,1);self.assertNotIn('trial_release',trace)
    def test_proof_must_be_callable_and_return_none_not_flags_or_marker(self):
        for options in (dict(proof_marker=True),dict(proof_marker=False),dict(proof_marker=None),dict(proof_marker='missing'),
                        dict(proof_result=True),dict(proof_result=False),dict(proof_result=object())):
            with self.subTest(options=options):
                status,events,trace,sessions=self.run_worker([command(name) for name in ('proceed','release_writers_for_trial','prove_operation','finish')],**options)
                self.assertEqual(status,1);self.assertNotIn('operation_held',[item['event'] for item in events])
                self.assertEqual(sessions[0].closed,1)
    def test_reacquired_event_requires_dedicated_writer_guard(self):
        status,events,trace,_=self.run_worker([command(name) for name in ('proceed','release_writers_for_trial',
            'reacquire_writers','release_reacquired_writers','finish')],guard_error=True)
        self.assertEqual(status,1);self.assertIn('writer_guard',trace)
        self.assertNotIn('writers_reacquired',[item['event'] for item in events])
    def test_single_deadline_eof_and_close_error_are_nonzero_fixed_refusals(self):
        for kwargs in (dict(hold_open=False),dict(hold_open=True,timeout=0.05)):
            status,events,trace,sessions=self.run_worker([command('proceed'),command('release_writers_for_trial')],**kwargs)
            self.assertEqual(status,1);self.assertEqual(events[-1]['code'],'MAINTENANCE_REFUSED');self.assertEqual(sessions[0].closed,1)
        status,events,trace,sessions=self.run_worker([command('proceed'),command('release_writers'),command('finish')],close_error=True)
        self.assertEqual(status,1);self.assertEqual(events[-1]['event'],'error');self.assertEqual(sessions[0].closed,1)
        status,events,trace,_=self.run_worker([command('proceed'),command('release_writers_for_trial')]+
            [command('prove_operation')]*8+[command('finish')],slow=True,timeout=0.07)
        self.assertEqual(status,1);self.assertLess(trace.count('proof'),8)
    def test_late_alarm_during_fixed_error_does_not_escape_cleanup(self):
        original=w._Channel.send
        def late(channel,event,**values):
            if event=='error':signal.getsignal(signal.SIGALRM)(signal.SIGALRM,None)
            return original(channel,event,**values)
        with patch.object(w._Channel,'send',new=late):
            status,events,trace,sessions=self.run_worker(
                [command('proceed'),command('release_writers_for_trial')])
        self.assertEqual(status,1)
        self.assertEqual(events[-1],dict(event='error',code='MAINTENANCE_REFUSED'))
        self.assertEqual(sessions[0].closed,1)

    def test_root_new_action_stage_cycle_and_duplicate_fields_do_not_extend_public_actions(self):
        status,events,trace,sessions=self.run_worker([],uid=0)
        self.assertEqual(status,1);self.assertEqual(sessions,[])
        for raw in (b'{"protocol":1,"action":"core_update"}\n',
                    b'{"protocol":1,"action":"start","action":"restart"}\n',
                    b'{"protocol":1,"action":"start"}\n{"command":"proceed"}\n{"command":"release_writers_for_trial","command":"finish"}\n'):
            status,events,trace,sessions=self.run_worker([],raw=raw)
            self.assertEqual(status,1);self.assertEqual(events[-1]['code'],'MAINTENANCE_REFUSED')
        status,events,trace,_=self.run_worker([command('proceed'),command('release_writers_for_trial')],action='stage')
        self.assertEqual(status,1);self.assertNotIn('trial_release',trace)


class PipeOutput:
    def __init__(self):self.events=queue.Queue()
    def write(self,raw):self.events.put(json.loads(raw));return len(raw)
    def flush(self):pass


@unittest.skipIf(os.geteuid()==0,'Real service-user fixture requires a non-root test runner')
class ActualSessionWorkerCycleTests(unittest.TestCase):
    def setUp(self):
        self.fixture=cycle_fixture.MaintenanceCycleTests(methodName='runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
    def test_actual_pipeline_proves_operation_and_reacquires_all_real_flocks(self):
        fixture=self.fixture;read,write=os.pipe();out=PipeOutput();errors=[];trace=[]
        def send(name):os.write(write,(json.dumps(command(name))+'\n').encode())
        def event(name):
            value=out.events.get(timeout=2);self.assertEqual(value['event'],name);trace.append(name)
        def controller():
            try:
                event('prepared');self.assertTrue(fixture.busy(fixture.root/'.operation.lock'));send('proceed')
                event('done');fixture.assert_writers(True);send('release_writers_for_trial')
                event('trial_writers_released');fixture.assert_writers(False);self.assertTrue(fixture.busy(fixture.root/'.operation.lock'))
                send('prove_operation');event('operation_held');send('reacquire_writers')
                event('writers_reacquired');fixture.assert_writers(True);send('release_reacquired_writers')
                event('recovery_writers_released');fixture.assert_writers(False)
                send('prove_operation');event('operation_held');self.assertTrue(fixture.busy(fixture.root/'.operation.lock'))
                send('finish')
            except BaseException as error:errors.append(error)
            finally:os.close(write)
        os.write(write,b'{"protocol":1,"action":"start"}\n')
        thread=threading.Thread(target=controller);thread.start()
        @contextmanager
        def quiet():yield
        try:
            with patch.object(w,'_quiet_candidate_io',new=quiet),patch.object(w.os,'umask'):
                status=w._run(fixture.root,tools,read,out,timeout=3)
        finally:os.close(read);thread.join(timeout=2)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(status,0)
        self.assertEqual(trace,['prepared','done','trial_writers_released','operation_held',
            'writers_reacquired','recovery_writers_released','operation_held'])
        fixture.assert_writers(False);self.assertFalse(fixture.busy(fixture.root/'.operation.lock'));fixture.unchanged()


if __name__=='__main__':unittest.main()
