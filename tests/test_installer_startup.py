"""Pure startup clocks/transports; these tests never start a real service."""
from contextlib import ExitStack
import io
import json
import ssl
import subprocess
import unittest
from unittest.mock import MagicMock, Mock, patch

from scripts import install_system as installer
from scripts import service_support


class Clock:
    def __init__(self):self.now=0.;self.sleeps=[]
    def monotonic(self):return self.now
    def sleep(self, seconds):
        self.sleeps.append(seconds);self.now+=seconds


class InstallerStartupTests(unittest.TestCase):
    config={'bind':'127.0.0.1','domain':'panel.example.test','port':3215,'service_manager':'systemd'}

    def harness(self, *, connect=None, state=None, status=401):
        stack=ExitStack();self.addCleanup(stack.close)
        clock=Clock();context=MagicMock();tls=MagicMock();sock=MagicMock()
        context.wrap_socket.return_value.__enter__.return_value=tls
        sock.__enter__.return_value=sock
        stack.enter_context(patch.object(installer.time,'monotonic',side_effect=clock.monotonic))
        stack.enter_context(patch.object(installer.time,'sleep',side_effect=clock.sleep))
        stack.enter_context(patch.object(installer.ssl,'create_default_context',return_value=context))
        connection=stack.enter_context(patch.object(installer.socket,'create_connection',side_effect=connect,return_value=sock))
        state_probe=stack.enter_context(patch.object(service_support,'startup_state',side_effect=state,
            return_value={'ActiveState':'active','SubState':'running','Job':'none'}))
        response=Mock(status=status)
        stack.enter_context(patch.object(installer.http.client,'HTTPResponse',return_value=response))
        stack.enter_context(patch('sys.stdout',new=io.StringIO()))
        return clock,connection,state_probe,response,sock,tls

    def test_ready_after_more_than_ten_seconds_retains_tls_and_http401(self):
        attempts=[]
        def connect(*args,**kwargs):
            attempts.append(kwargs['timeout'])
            if len(attempts)<=48:raise ConnectionRefusedError('synthetic')
            return MagicMock()
        clock,connection,state,response,sock,tls=self.harness(connect=connect)
        installer.https_health(self.config)
        self.assertEqual(clock.now,12)
        self.assertEqual(connection.call_count,49)
        self.assertTrue(all(0<value<=2 for value in attempts))
        self.assertTrue(all(0<call.kwargs['timeout']<=2 for call in state.call_args_list))
        response.close.assert_called_once()

    def test_refusals_stop_at_exact_120_second_deadline(self):
        clock,connection,state,*_=self.harness(connect=ConnectionRefusedError('synthetic'))
        with self.assertRaisesRegex(installer.InstallError,'120 秒启动预算'):
            installer.https_health(self.config)
        self.assertEqual(clock.now,120)
        self.assertEqual(connection.call_count,480)
        self.assertTrue(all(0<value<=.25 for value in clock.sleeps))

    def test_state_time_consumes_deadline_and_no_io_starts_after_expiry(self):
        clock,connection,state,*_=self.harness()
        def delayed_state(*args,**kwargs):
            self.assertEqual(kwargs['timeout'],.125)
            clock.now+=.125
            return {}
        state.side_effect=delayed_state
        with self.assertRaisesRegex(installer.InstallError,'启动预算'):
            installer.https_health(self.config,deadline=.125)
        connection.assert_not_called();self.assertEqual(clock.now,.125)

    def test_terminal_exit_is_fast_but_queued_and_restart_states_are_not_terminal(self):
        for active,sub,job,terminal in [('failed','failed','none',True),('inactive','dead','none',True),
                ('activating','start-pre','pending',False),('activating','auto-restart','none',False),
                ('inactive','dead','pending',False),('failed','failed','pending',False)]:
            with self.subTest(active=active,sub=sub,job=job):
                clock,connection,*_=self.harness(state=lambda *a,**k:dict(ActiveState=active,SubState=sub,Job=job))
                if terminal:
                    with self.assertRaisesRegex(installer.InstallError,'服务已终止'):
                        installer.https_health(self.config)
                    connection.assert_not_called();self.assertEqual(clock.now,0)
                else:
                    installer.https_health(self.config);connection.assert_called_once()

    def test_tls_and_wrong_http_status_fail_without_retry_or_secret_text(self):
        for error in (ssl.SSLCertVerificationError('private-password'),ssl.SSLError('private-token')):
            with self.subTest(error=type(error).__name__):
                clock,connection,*_=self.harness(connect=error)
                with self.assertRaisesRegex(installer.InstallError,'TLS') as caught:
                    installer.https_health(self.config)
                self.assertNotIn('private',str(caught.exception));self.assertTrue(caught.exception.__suppress_context__)
                connection.assert_called_once();self.assertEqual(clock.now,0)
        for status in (200,302,403,404,500):
            with self.subTest(status=status):
                clock,connection,*_=self.harness(status=status)
                with self.assertRaisesRegex(installer.InstallError,'必须为 HTTP 401'):
                    installer.https_health(self.config)
                connection.assert_called_once();self.assertEqual(clock.now,0)

    def test_late_401_is_rejected_and_each_transport_stage_gets_remaining_budget(self):
        clock,connection,state,response,sock,tls=self.harness()
        def connect(*args,**kwargs):
            self.assertEqual(kwargs['timeout'],.8);clock.now+=.2
            return sock
        connection.side_effect=connect
        def send(*args):clock.now+=.2
        tls.sendall.side_effect=send
        def begin():clock.now=1
        response.begin.side_effect=begin
        with self.assertRaisesRegex(installer.InstallError,'启动预算'):
            installer.https_health(self.config,deadline=.8)
        self.assertAlmostEqual(sock.settimeout.call_args.args[0],.6)
        self.assertAlmostEqual(tls.settimeout.call_args.args[0],.6)
        response.close.assert_called_once()

    def test_dribbling_http_reads_refresh_budget_and_stop_at_deadline(self):
        clock=Clock();tls=Mock()
        def recv(buffer):clock.now+=.3;return 1
        tls.recv_into.side_effect=recv
        reader=installer._DeadlineReader(tls,1)
        with patch.object(installer.time,'monotonic',side_effect=clock.monotonic):
            for _ in range(4):reader.readinto(bytearray(1))
            with self.assertRaises(TimeoutError):reader.readinto(bytearray(1))
        self.assertEqual(tls.recv_into.call_count,4)
        for call,expected in zip(tls.settimeout.call_args_list,(1,.7,.4,.1)):
            self.assertAlmostEqual(call.args[0],expected)

    def test_real_http_parser_uses_deadline_reader_for_every_header_fragment(self):
        clock=Clock();tls=Mock();data=io.BytesIO(b'HTTP/1.0 401 Unauthorized\r\nContent-Length: 0\r\n\r\n')
        def recv(buffer):
            chunk=data.read(1);buffer[:len(chunk)]=chunk;clock.now+=.3
            return len(chunk)
        tls.recv_into.side_effect=recv
        with patch.object(installer.time,'monotonic',side_effect=clock.monotonic):
            response=installer.http.client.HTTPResponse(installer._DeadlineHTTPTransport(tls,1))
            try:
                with self.assertRaises(TimeoutError):response.begin()
            finally:response.close()
        self.assertEqual(tls.recv_into.call_count,4)

    def test_diagnostics_hide_unknown_values_before_output_truncation(self):
        secret='secret-password-and-subscription-token'
        messages=['A'*4090+secret, 'ModuleNotFoundError: '+secret,
                  'V-UI launch refused: Launch code failed integrity check '+secret,
                  'Control process exited, code=exited, status=23/n/a '+secret]
        raw='\n'.join(json.dumps({'MESSAGE':message}) for message in messages)
        output=io.StringIO()
        with patch.object(service_support,'startup_state',return_value={'ExecMainStatus':'17'}), \
             patch.object(installer,'_startup_journal',return_value=raw) as journal,patch('sys.stdout',output):
            installer.startup_diagnostics('systemd','v-ui.service',123.5)
        journal.assert_called_once_with('v-ui.service',123.5)
        text=output.getvalue()
        self.assertIn('ModuleNotFoundError',text);self.assertIn('17',text)
        self.assertIn('Launch code failed integrity check',text)
        self.assertIn('Control process exited code=exited status=23',text)
        self.assertNotIn(secret,text);self.assertNotIn(secret[:8],text)
        self.assertNotIn('A'*100,text)

    def test_diagnostics_failure_is_swallowed_including_output_interrupt(self):
        for error in (OSError('private-secret'),KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), \
                 patch.object(service_support,'startup_state',side_effect=error), \
                 patch('sys.stdout',new=io.StringIO()) as output:
                installer.startup_diagnostics('systemd','v-ui.service',1)
                self.assertNotIn('private-secret',output.getvalue())

    def test_journal_byte_limit_kills_collector_without_retaining_partial_message(self):
        process=Mock();process.stdout=Mock();process.stdout.fileno.return_value=8
        process.poll.return_value=None
        with patch.object(installer.subprocess,'Popen',return_value=process) as popen, \
             patch.object(installer.time,'monotonic',return_value=0), \
             patch.object(installer.select,'select',return_value=([process.stdout],[],[])), \
             patch.object(installer.os,'read',side_effect=[b'x'*8192]*8+[b'x']) as read:
            with self.assertRaisesRegex(OSError,'byte limit'):
                installer._startup_journal('v-ui.service',123.5)
        process.kill.assert_called_once();process.stdout.close.assert_called_once()
        self.assertEqual(read.call_args.args[1],1)
        self.assertIn('--since=@123.500000',popen.call_args.args[0])
        process.wait.assert_called_once_with(timeout=1)

    def test_journal_timeout_kills_collector_with_bounded_cleanup(self):
        process=Mock();process.poll.return_value=None
        with patch.object(installer.subprocess,'Popen',return_value=process), \
             patch.object(installer.time,'monotonic',return_value=0), \
             patch.object(installer.select,'select',return_value=([],[],[])) as select_ready:
            with self.assertRaises(TimeoutError):installer._startup_journal('v-ui.service',1)
        self.assertEqual(select_ready.call_args.args[-1],3)
        process.kill.assert_called_once();process.wait.assert_called_once_with(timeout=1)

    def test_start_commands_only_request_no_block_for_explicit_systemd_call(self):
        with patch.object(service_support.subprocess,'run') as run:
            service_support.enable_start('systemd',['v-ui.service'],no_wait=True,timeout=120)
            self.assertIn('--no-block',run.call_args.args[0])
            self.assertLessEqual(run.call_args.kwargs['timeout'],120)
            service_support.enable_start('systemd',['v-ui-http01.socket'])
            self.assertNotIn('--no-block',run.call_args.args[0])
            service_support.enable_start('openrc',['v-ui'],no_wait=True,timeout=120)
            self.assertEqual(run.call_args.args[0],['rc-service','v-ui','start'])
            self.assertTrue(all('--no-block' not in call.args[0] for call in run.call_args_list[-2:]))

    def test_openrc_update_and_start_share_one_aggregate_budget(self):
        clock=Clock()
        def run(*args,**kwargs):
            clock.now+=min(90,kwargs['timeout'])
            if kwargs['timeout']<90:raise subprocess.TimeoutExpired(args[0],kwargs['timeout'])
        with patch.object(service_support.time,'monotonic',side_effect=clock.monotonic), \
             patch.object(service_support.subprocess,'run',side_effect=run) as command:
            with self.assertRaises(subprocess.TimeoutExpired):
                service_support.enable_start('openrc',['v-ui'],no_wait=True,timeout=120)
        self.assertEqual([call.kwargs['timeout'] for call in command.call_args_list],[120,30])
        self.assertEqual(clock.now,120)

    def test_state_probe_has_explicit_manager_name_and_allowlists_values(self):
        raw='ActiveState=activating\nSubState=auto-restart\nResult=exit-code\nExecMainStatus=17\nJob=9 /secret-token\nUnknown=private-password\n'
        result=subprocess.CompletedProcess([],0,raw,'private-password')
        with patch.object(service_support.subprocess,'run',return_value=result) as run:
            state=service_support.startup_state('systemd','v-ui.service',timeout=.25)
        self.assertEqual(state['Job'],'pending');self.assertEqual(state['ExecMainStatus'],'17')
        self.assertNotIn('private',str(state));self.assertNotIn('secret',str(state))
        self.assertEqual(run.call_args.kwargs['timeout'],.25)
        self.assertEqual(run.call_args.args[0][:3],['systemctl','show','v-ui.service'])


if __name__=='__main__':unittest.main()
