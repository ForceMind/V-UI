"""Pure synthetic installer failure boundaries; no installation, network or root."""
import argparse
from contextlib import contextmanager
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import install_system as installer
import online_install_helpers as online


PASSWORD = 'Synthetic_only_random_password_' + 'X' * 16


@contextmanager
def terminal_scope(terminal, close_error=False):
    yield terminal
    if isinstance(close_error,BaseException):
        raise close_error
    if close_error:
        raise OSError('synthetic terminal close failure')


class InstallerInteractionTests(unittest.TestCase):
    config = {'admin': 'vui_012345abcdef', 'origin': 'https://panel.example.test:8443'}

    def test_credentials_are_fully_delivered_before_create_and_summarized_after_commit(self):
        terminal=io.StringIO();events=[]
        def account(python,payload,action,**values):
            events.append(action)
            if action=='installer-status':return False
            self.assertIn(PASSWORD,terminal.getvalue())
            self.assertIn('账户尚未创建，以最终安装成功为准',terminal.getvalue())
            self.assertEqual(values,{'username':self.config['admin'],'password':PASSWORD})
            return True
        with patch.object(installer,'installer_account',side_effect=account), \
             patch.object(installer,'controlling_terminal',side_effect=lambda:terminal_scope(terminal)), \
             patch.object(installer.secrets,'token_urlsafe',return_value=PASSWORD), \
             patch('sys.stdout',new_callable=io.StringIO) as output:
            with installer.provision_initial_account('/portable','/payload',self.config):
                events.append('commit-root-config')
                self.assertNotIn('安装完成。',terminal.getvalue())
        self.assertEqual(events,['installer-status','installer-create','commit-root-config'])
        self.assertEqual(terminal.getvalue().count(PASSWORD),2)
        self.assertIn('安装完成。',terminal.getvalue())
        self.assertNotIn(PASSWORD,output.getvalue())

    def test_existing_account_never_opens_terminal_generates_or_resets_credentials(self):
        with patch.object(installer,'installer_account',return_value=True) as account, \
             patch.object(installer,'controlling_terminal') as terminal, \
             patch.object(installer.secrets,'token_urlsafe') as random:
            with installer.provision_initial_account('/portable','/payload',self.config):pass
        account.assert_called_once_with('/portable','/payload','installer-status')
        terminal.assert_not_called();random.assert_not_called()

    def test_short_or_failed_private_delivery_creates_no_account(self):
        for kind in ('short','write','flush'):
            terminal=Mock()
            terminal.write.return_value=1
            if kind=='write':terminal.write.side_effect=OSError(PASSWORD)
            if kind=='flush':
                terminal.write.side_effect=lambda value:len(value)
                terminal.flush.side_effect=OSError(PASSWORD)
            with self.subTest(kind=kind),patch.object(installer,'installer_account',return_value=False) as account, \
                 patch.object(installer,'controlling_terminal',side_effect=lambda:terminal_scope(terminal)), \
                 patch.object(installer.secrets,'token_urlsafe',return_value=PASSWORD):
                with self.assertRaises(installer.InstallError) as failed:
                    with installer.provision_initial_account('/portable','/payload',self.config):
                        self.fail('Root completion must not be reached')
                self.assertNotIn(PASSWORD,str(failed.exception))
                account.assert_called_once_with('/portable','/payload','installer-status')

    def test_root_finalization_failure_keeps_pre_delivered_password_and_no_success_summary(self):
        terminal=io.StringIO()
        with patch.object(installer,'installer_account',side_effect=[False,True]), \
             patch.object(installer,'controlling_terminal',side_effect=lambda:terminal_scope(terminal)), \
             patch.object(installer.secrets,'token_urlsafe',return_value=PASSWORD):
            with self.assertRaisesRegex(OSError,'root finalization'):
                with installer.provision_initial_account('/portable','/payload',self.config):
                    raise OSError('synthetic root finalization failure')
        self.assertEqual(terminal.getvalue().count(PASSWORD),1)
        self.assertNotIn('安装完成。',terminal.getvalue())

    def test_postcommit_summary_write_flush_and_terminal_close_are_best_effort(self):
        for kind in ('write','flush','close','interrupt','handler-signal','handler-close'):
            terminal=io.StringIO();writes=0;flushes=0
            def write(value):
                nonlocal writes
                writes+=1
                if writes==2 and kind in ('write','interrupt','handler-signal'):
                    if kind=='interrupt':raise KeyboardInterrupt()
                    if kind=='handler-signal':raise installer.InstallError('安装被信号中断')
                    raise OSError(PASSWORD)
                return io.StringIO.write(terminal,value)
            def flush():
                nonlocal flushes
                flushes+=1
                if flushes==2 and kind=='flush':raise OSError(PASSWORD)
            with self.subTest(kind=kind),patch.object(installer,'installer_account',side_effect=[False,True]), \
                 patch.object(installer,'controlling_terminal',side_effect=lambda:terminal_scope(terminal,
                     installer.InstallError('安装被信号中断') if kind=='handler-close' else kind=='close')), \
                 patch.object(installer.secrets,'token_urlsafe',return_value=PASSWORD), \
                 patch.object(terminal,'write',side_effect=write),patch.object(terminal,'flush',side_effect=flush), \
                 patch('sys.stderr',new_callable=io.StringIO) as errors:
                committed=False
                with installer.provision_initial_account('/portable','/payload',self.config):
                    committed=True
                self.assertTrue(committed)
                self.assertIn('安装已完成',errors.getvalue())
                self.assertNotIn(PASSWORD,errors.getvalue())

    def test_private_child_protocol_keeps_password_out_of_command_and_environment(self):
        account=SimpleNamespace(pw_uid=1234,pw_gid=1234)
        with patch.object(installer.pwd,'getpwnam',return_value=account), \
             patch.object(installer,'command',return_value=SimpleNamespace(stdout='{"version":1,"created":true}')) as command:
            self.assertTrue(installer.installer_account('/portable','/payload','installer-create',
                username=self.config['admin'],password=PASSWORD))
        arguments,options=command.call_args
        self.assertNotIn(PASSWORD,str(arguments))
        self.assertNotIn(PASSWORD,str(options['env']))
        self.assertEqual(json.loads(options['input'])['password'],PASSWORD)
        self.assertTrue(options['capture_output'])
        self.assertNotIn('stdin',options)

    def test_private_pipe_failures_or_invalid_responses_never_leak_request_or_output(self):
        for failure in (subprocess.CalledProcessError(7,['app.admin'],output=PASSWORD,stderr=PASSWORD),
                        subprocess.TimeoutExpired(['app.admin'],1,output=PASSWORD),
                        SimpleNamespace(stdout='{"version":true,"created":true}'),
                        SimpleNamespace(stdout=PASSWORD)):
            with self.subTest(kind=type(failure).__name__):
                response={'side_effect':failure} if isinstance(failure,BaseException) else {'return_value':failure}
                with patch.object(installer,'as_service',**response):
                    with self.assertRaises(installer.InstallError) as error:
                        installer.installer_account('/portable','/payload','installer-create',
                            username=self.config['admin'],password=PASSWORD)
                self.assertNotIn(PASSWORD,str(error.exception))
                self.assertIsNone(error.exception.__cause__)

    def test_known_blocked_port_stops_before_root_configuration_or_account_changes(self):
        args=argparse.Namespace(domain='panel.example.test',email='owner@example.test',admin='synthetic-owner',
            port=8443,node_port=10443,node_udp_port=[],bind='0.0.0.0',cert=None,key=None,
            accept_terms=True,upgrade=False,dry_run=False)
        with tempfile.TemporaryDirectory() as folder, \
             patch.multiple(installer,ROOT=Path(folder)/'root',CONFIG=Path(folder)/'service.json'), \
             patch.object(installer,'check_platform',return_value={'init':'systemd','name':'fixture','target':'x86_64-gnu'}), \
             patch.object(installer,'check_reserved'),patch.object(installer,'probe_ipv6',return_value=False), \
             patch.object(installer,'check_port'), \
             patch.object(installer,'firewall_preflight',side_effect=installer.InstallError('尚未放行：80/tcp')), \
             patch.object(installer,'write_root_file') as writes,patch.object(installer,'create_service_user') as user, \
             patch.object(installer,'begin_transaction') as transaction,patch.object(installer,'as_service') as service:
            with self.assertRaisesRegex(installer.InstallError,'80/tcp'):
                installer.install_verified(args,None,None,{'release_id':'fixture'})
            writes.assert_not_called();user.assert_not_called();transaction.assert_not_called();service.assert_not_called()

    def test_http01_failure_keeps_exit_status_and_gives_bounded_chinese_guidance(self):
        with patch.object(installer,'as_service',side_effect=subprocess.CalledProcessError(23,['bootstrap'])):
            with self.assertRaises(installer.InstallError) as error:
                installer.bootstrap_managed_certificate('/portable','/payload',
                    SimpleNamespace(domain='panel.example.test',email='owner@example.test'))
        for phrase in ('退出码 23','原始错误码','DNS','公网 TCP 80','本机防火墙','云安全组','不代表已确定根因'):
            self.assertIn(phrase,str(error.exception))

    def test_private_frames_and_partial_timeout_lines_are_redacted_everywhere(self):
        username=self.config['admin'];frame='初始用户名：'+username+'\r\n初始密码：'+PASSWORD+'\r\n'
        self.assertEqual(online.private_installer_credentials(frame+frame),(username,PASSWORD))
        for text in (frame,'原管理员：'+username+'\n'+frame,'初始密码：'+PASSWORD[:10]):
            redacted=online.redact_installer_output(text,username,PASSWORD)
            self.assertNotIn(PASSWORD,redacted)
            self.assertNotIn(PASSWORD[:10],redacted)
            self.assertNotIn(username,redacted)
        for text in ('missing',frame+'初始用户名：other\n初始密码：'+'Y'*44+'\n'):
            with self.assertRaises(AssertionError) as invalid:online.private_installer_credentials(text)
            self.assertNotIn(PASSWORD,str(invalid.exception))

    def test_menu_assertion_and_timeout_diagnostics_cannot_echo_automatic_credentials(self):
        frame='初始用户名：'+self.config['admin']+'\n初始密码：'+PASSWORD+'\n'
        session=object.__new__(online.PrivateMenuSession)
        session.private_values=[self.config['admin'],PASSWORD]
        session.output=bytearray(frame.encode())
        self.assertNotIn(PASSWORD,session.text())
        with patch.object(online.MenuSession,'expect',side_effect=AssertionError(frame)):
            with self.assertRaises(AssertionError) as error:session.expect('missing')
        self.assertNotIn(PASSWORD,str(error.exception))
        fake=Mock();fake.__enter__=Mock(return_value=fake);fake.__exit__=Mock(return_value=False)
        fake.process.poll.return_value=None;fake.text.return_value=frame
        with patch.object(online,'MenuSession',return_value=fake), \
             patch.object(online.time,'monotonic',side_effect=[0,2]):
            with self.assertRaises(subprocess.TimeoutExpired) as timeout:
                online.configured_setup_with_tty(['synthetic-menu'],timeout=1,
                    private_values=[self.config['admin'],PASSWORD])
        self.assertNotIn(PASSWORD,timeout.exception.output)


if __name__=='__main__':unittest.main()
