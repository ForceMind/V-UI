"""Fixed fixture failure evidence with inert coordinator methods; no service or core."""
from contextlib import redirect_stderr
from io import StringIO
from types import SimpleNamespace
import json
import threading
import unittest

import core_switch_fixture_runner as runner


class CoreSwitchFixtureTraceTests(unittest.TestCase):
    def fixture(self, *, trial_error=None, recovery_error=None, login_error=None, prechange=False,
                recover_success=False, exchange_error=None):
        class ControlError(RuntimeError):pass
        class OpsRefused(RuntimeError):pass
        class BrokerError(ValueError):
            def __init__(self,code):self.code=code
        class Worker:
            def exchange(self,request,event,fields=()):
                if exchange_error is not None:raise exchange_error
                return {'status':'ready'}
        class Switch:
            def __init__(self):
                self.transaction=None;self.stop_attempted=False;self.released=False;self.confirming=False
                self.broker=None;self.worker=Worker()
                self.wire=SimpleNamespace(CoreBootBrokerError=BrokerError)
                self.in_recovery=False
            def _tls(self,identity,*,login=False):
                self.worker.exchange({},'panel_tls')
                error=(recovery_error if self.in_recovery else login_error if login else trial_error)
                if error is not None:
                    raise ControlError(error) if type(error) is str else error
            def _activate(self,state,intents,*,recovery,cleanup=False):
                self.in_recovery=recovery
                self.broker=SimpleNamespace(_state='probed')
                self._tls(None)
                self.confirming=True;self.broker._state='released'
                self._tls(None,login=True)
            def run(self):
                if prechange:return self._tls(None)
                self.transaction='private-transaction-must-not-escape'
                self.stop_attempted=True;self.released=True
                try:self._activate(None,None,recovery=False)
                except BaseException:
                    try:
                        self._activate(None,None,recovery=True)
                        if recover_success:return {'outcome':'restored_previous'}
                    except BaseException:pass
                    raise ControlError('CORE_RECOVERY_REQUIRED') from None
                return {'outcome':'switched'}
        control=SimpleNamespace(_Worker=Worker,_Switch=Switch,CoreControlError=ControlError)
        ops=SimpleNamespace(Refused=OpsRefused)
        trace=runner.trace_switch(control,ops)
        return Switch(),trace,control,ops,BrokerError

    def test_first_trial_tls_failure_survives_later_recovery_failure_and_final_fold(self):
        task,trace,control,ops,_=self.fixture(trial_error='CORE_PANEL_TRUST_CHANGED',
                                           recovery_error='CORE_PANEL_TLS_TIMEOUT')
        with self.assertRaisesRegex(control.CoreControlError,'^CORE_RECOVERY_REQUIRED$') as caught:task.run()
        first=trace.first_failure
        self.assertEqual(first['code'],'CORE_PANEL_TRUST_CHANGED')
        self.assertEqual((first['coordinator'],first['activation'],first['worker_event']),('TLS','trial','PANEL_TLS'))
        self.assertTrue(first['transaction_present']);self.assertTrue(first['released'])
        self.assertTrue(first['stop_attempted']);self.assertFalse(first['confirming'])
        self.assertEqual(first['broker_state'],'probed')
        self.assertTrue(runner.switch_error_code(caught.exception,ops,control,trace[0]).startswith('PANEL_TLS_CORE_RECOVERY_REQUIRED'))
        output=StringIO()
        with redirect_stderr(output):trace.emit()
        text=output.getvalue()
        self.assertEqual(runner.first_failure_diagnostic(text),' '+text.rstrip('\n'))
        self.assertNotIn(task.transaction,text)

    def test_prechange_tls_snapshot_reports_actual_absent_transaction_and_no_stop(self):
        task,trace,control,_,_=self.fixture(trial_error='CORE_PANEL_TLS_REJECTED',prechange=True)
        with self.assertRaises(control.CoreControlError):task.run()
        first=trace.first_failure
        self.assertEqual(first['activation'],'none')
        self.assertFalse(first['transaction_present']);self.assertFalse(first['stop_attempted'])
        self.assertFalse(first['released']);self.assertFalse(first['confirming'])
        self.assertEqual(first['broker_state'],'none')

    def test_postconfirmation_login_snapshot_preserves_released_broker_fact(self):
        task,trace,control,_,_=self.fixture(login_error='CORE_PANEL_TLS_TIMEOUT',recovery_error='CORE_PANEL_TRUST_CHANGED')
        with self.assertRaises(control.CoreControlError):task.run()
        first=trace.first_failure
        self.assertEqual(first['activation'],'trial')
        self.assertTrue(first['confirming']);self.assertEqual(first['broker_state'],'released')
        self.assertEqual(first['code'],'CORE_PANEL_TLS_TIMEOUT')

    def test_unknown_exception_never_exposes_type_message_or_paths(self):
        class SecretFailure(RuntimeError):
            def __str__(self):raise AssertionError('unknown exception string must not be read')
        task,trace,control,_,_=self.fixture(trial_error=SecretFailure('/secret/private user token'))
        with self.assertRaises(control.CoreControlError):task.run()
        output=StringIO()
        with redirect_stderr(output):trace.emit()
        self.assertEqual(trace.first_failure['code'],'UNKNOWN')
        for value in ('SecretFailure','secret','token','private'):
            self.assertNotIn(value,output.getvalue())
        self.assertLess(len(output.getvalue()),1024)

    def test_fixed_broker_code_and_unknown_code_do_not_expand_allowlist(self):
        for code,expected in (('DEADLINE','BROKER_DEADLINE'),('PRIVATE_SECRET','UNKNOWN')):
            with self.subTest(code=code):
                task,trace,control,ops,error_type=self.fixture()
                task.transaction='opaque';task.stop_attempted=True
                trace.task=task;trace.coordinator='ACTIVATE';trace.activation='recovery'
                trace.remember(error_type(code),ops,control)
                self.assertEqual(trace.first_failure['code'],expected)
                trace.remember(control.CoreControlError('CORE_PANEL_TLS_TIMEOUT'),ops,control)
                self.assertEqual(trace.first_failure['code'],expected)

    def test_successful_switch_or_safety_recovery_emits_no_failure_evidence(self):
        for options,expected in (({},'switched'),
                ({'trial_error':'CORE_PANEL_TLS_REJECTED','recover_success':True},'restored_previous')):
            with self.subTest(options=options):
                task,trace,_,_,_=self.fixture(**options)
                output=StringIO()
                with redirect_stderr(output):self.assertEqual(task.run()['outcome'],expected)
                self.assertEqual(output.getvalue(),'')
                if not options:self.assertIsNone(trace.first_failure)

    def test_exchange_error_preserves_actual_tls_scope_and_fixed_worker_event(self):
        task,trace,control,_,_=self.fixture(exchange_error=RuntimeError('private raw worker'))
        with self.assertRaises(control.CoreControlError):task.run()
        self.assertEqual(trace.first_failure['coordinator'],'TLS')
        self.assertEqual(trace.first_failure['activation'],'trial')
        self.assertEqual(trace.first_failure['worker_event'],'PANEL_TLS')
        self.assertEqual(trace.first_failure['code'],'UNKNOWN')

    def test_concurrent_failure_cannot_overwrite_claimed_code_or_scope_during_mapping(self):
        task,trace,control,ops,_=self.fixture()
        first_at_mapping=threading.Event();release_first=threading.Event();second_done=threading.Event()
        mapped=[];errors=[]
        original_str=control.CoreControlError.__str__
        def blocked_str(error):
            mapped.append(threading.current_thread().name)
            first_at_mapping.set()
            if not release_first.wait(3):raise RuntimeError('inert formatting wait')
            return original_str(error)
        control.CoreControlError.__str__=blocked_str
        class Broker:
            _state='unknown'
            def __init__(self):self._state='probed'
            @property
            def state(self):raise AssertionError('diagnostics must never acquire broker state lock')
        task.broker=Broker();task.transaction='private';task.stop_attempted=True;task.released=True
        trace.task=task;trace.coordinator='TLS';trace.activation='trial';trace.worker_event='PANEL_TLS'
        def remember(code,done=None):
            try:trace.remember(control.CoreControlError(code),ops,control)
            except BaseException as error:errors.append(error)
            finally:
                if done is not None:done.set()
        first=threading.Thread(target=remember,args=('CORE_PANEL_TRUST_CHANGED',),name='first-failure')
        second=threading.Thread(target=remember,args=('CORE_PANEL_TLS_TIMEOUT',second_done),name='later-failure')
        first.start()
        try:
            self.assertTrue(first_at_mapping.wait(3))
            trace.coordinator='ACTIVATE';trace.activation='recovery';trace.worker_event='OPERATION_HELD'
            task.confirming=True
            second.start()
            self.assertTrue(second_done.wait(1),'later failure must return without formatting or waiting')
            self.assertEqual(trace.first_failure['code'],'UNKNOWN')
            output=StringIO()
            with redirect_stderr(output):trace.emit()
            self.assertTrue(runner.first_failure_diagnostic(output.getvalue()))
            self.assertEqual(trace.first_failure['broker_state'],'unknown')
        finally:
            release_first.set();first.join(3)
            if second.ident is not None:second.join(3)
        self.assertFalse(first.is_alive());self.assertFalse(second.is_alive());self.assertFalse(errors)
        self.assertEqual(mapped,['first-failure'])
        self.assertEqual(trace.first_failure['code'],'CORE_PANEL_TRUST_CHANGED')
        self.assertEqual((trace.first_failure['coordinator'],trace.first_failure['activation'],
                          trace.first_failure['worker_event']),('TLS','trial','PANEL_TLS'))
        self.assertFalse(trace.first_failure['confirming'])
        self.assertEqual(trace.first_failure['broker_state'],'probed')

    def test_concurrent_queued_proof_does_not_relabel_failing_prepared_exchange(self):
        _,_,control,ops,_=self.fixture()
        entered=threading.Event();release=threading.Event();proof_entered=threading.Event();errors=[]
        class Worker:
            def __init__(self):self.mutex=threading.Lock()
            def exchange(self,request,event,fields=()):
                if event=='operation_held':proof_entered.set()
                with self.mutex:
                    if event=='prepared':
                        entered.set()
                        if not release.wait(3):raise RuntimeError('inert protocol wait')
                        raise control.CoreControlError('CORE_WORKER_PROTOCOL')
                    return {}
        control._Worker=Worker
        trace=runner.trace_switch(control,ops)
        worker=Worker()
        def exchange(event):
            try:worker.exchange({},event)
            except control.CoreControlError:pass
            except BaseException as error:errors.append(error)
        first=threading.Thread(target=exchange,args=('prepared',))
        later=threading.Thread(target=exchange,args=('operation_held',))
        first.start()
        try:
            self.assertTrue(entered.wait(3))
            later.start()
            self.assertTrue(proof_entered.wait(3))
        finally:
            release.set();first.join(3)
            if later.ident is not None:later.join(3)
        self.assertFalse(first.is_alive());self.assertFalse(later.is_alive());self.assertFalse(errors)
        self.assertEqual(trace.first_failure['code'],'CORE_WORKER_PROTOCOL')
        self.assertEqual(trace.first_failure['worker_event'],'PREPARED')

    def test_caller_accepts_only_one_exact_closed_bounded_diagnostic(self):
        task,trace,control,_,_=self.fixture(trial_error='CORE_PANEL_TLS_TIMEOUT')
        with self.assertRaises(control.CoreControlError):task.run()
        value=trace.first_failure
        valid=runner.FIRST_FAILURE_MARKER+runner.encoded(value).decode('ascii')+'\n'
        self.assertTrue(runner.first_failure_diagnostic(valid))
        for raw in (valid+valid,'private raw worker\n'+valid,valid+'extra\n','x'*1025,
                    runner.FIRST_FAILURE_MARKER+'{}',
                    runner.FIRST_FAILURE_MARKER+json.dumps(value,indent=2),
                    runner.FIRST_FAILURE_MARKER+runner.encoded({**value,'code':'PRIVATE_SECRET'}).decode(),
                    runner.FIRST_FAILURE_MARKER+runner.encoded({**value,'private':'sensitive'}).decode(),
                    runner.FIRST_FAILURE_MARKER+runner.encoded({**value,'confirming':1}).decode(),
                    runner.FIRST_FAILURE_MARKER+'{"code":"UNKNOWN","code":"UNKNOWN"}'):
            with self.subTest(raw=raw[:40]):self.assertEqual(runner.first_failure_diagnostic(raw),'')
