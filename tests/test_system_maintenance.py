"""Root control boundary tests: temporary files and mocks only, never host changes."""
from contextlib import contextmanager
import hashlib
import errno
import io
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location('_test_' + name, SOURCE / 'deploy' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


daemon = module('maintenance_daemon')
upgrade = module('maintenance_upgrade')
cli = module('maintenance_cli_worker')
ops = module('system_ops')


@contextmanager
def no_lock(*args, **kwargs):
    yield


class InstalledNetlinkFixtureTests(unittest.TestCase):
    def test_real_installed_gate_requires_both_explicit_flag_and_actual_020(self):
        from scripts import system_maintenance_smoke as smoke
        for flag in ('', '0', '1'):
            for version in ('0.4.14', '0.4.18', '0.4.19', '0.4.20', '0.4.21'):
                with self.subTest(flag=flag,version=version),patch.dict(os.environ,{'VUI_NETLINK_SYSTEMD_TEST':flag}):
                    self.assertEqual(smoke.netlink_systemd_enabled(version+'-'+'a'*12),
                                     flag=='1' and version in ('0.4.20','0.4.21'))

    def test_019_and_020_keep_actual_180_day_cli_while_old_releases_keep_default(self):
        from scripts import system_maintenance_smoke as smoke
        for version,expected in (('0.4.14',False),('0.4.15',False),('0.4.16',False),
                                 ('0.4.17',False),('0.4.18',False),('0.4.19',True),('0.4.20',True),('0.4.21',True)):
            with self.subTest(version=version):
                self.assertIs(smoke.maintenance_long_duration(version+'-'+'a'*12),expected)

    def test_only_exact_previously_verified_fixture_rows_can_be_cleaned(self):
        from scripts import system_maintenance_smoke as smoke
        original={'id':7,'tag':'sing-box-vless-19371','remark':'published-upgrade-synthetic-draft',
                  'core':'sing-box','protocol':'vless','port':19371,'mutation_revision':'a'*64}
        self.assertEqual(smoke.verified_upgrade_fixture_rows([dict(original)],[original]),[original])
        self.assertEqual(smoke.verified_upgrade_fixture_rows([],[]),[])
        for change in ({'id':8},{'tag':'unrelated'},{'remark':'unknown'},{'core':'xray'},
                       {'port':19372},{'mutation_revision':'b'*64},{'mutation_revision':None}):
            with self.subTest(change=change),self.assertRaises((AssertionError,TypeError)):
                smoke.verified_upgrade_fixture_rows([{**original,**change}],[original])
        with self.assertRaises(AssertionError):smoke.verified_upgrade_fixture_rows([original],[])
        with self.assertRaises(AssertionError):smoke.verified_upgrade_fixture_rows([], [original])

    def test_post_menu_identity_wait_retries_only_refused_get_with_shared_budget(self):
        from scripts import system_maintenance_smoke as smoke
        identity={'id':1,'username':'owner_changed','role':'admin'}
        api=Mock(side_effect=[ConnectionRefusedError(errno.ECONNREFUSED,'synthetic not bound'),
                              (200,json.dumps(identity).encode())])
        with patch.object(smoke.time,'monotonic',return_value=100),patch.object(smoke.time,'sleep') as sleep:
            self.assertEqual(smoke.wait_installed_admin_identity(api,'owner_changed'),identity)
        self.assertEqual(api.call_count,2)
        for call in api.call_args_list:
            self.assertEqual(call.args,('/api/auth/me',))
            self.assertEqual(call.kwargs,{'method':'GET','timeout':5,'readiness':True})
        sleep.assert_called_once_with(.1)

    def test_post_menu_identity_wait_never_retries_tls_timeout_http_or_content_errors(self):
        from scripts import system_maintenance_smoke as smoke
        import ssl
        identity={'id':1,'username':'owner_changed','role':'admin'}
        cases=[(ssl.SSLCertVerificationError('synthetic TLS verification'),ssl.SSLCertVerificationError),
               (TimeoutError('synthetic request timeout'),TimeoutError),
               ((401,json.dumps(identity).encode()),AssertionError),
               ((500,json.dumps(identity).encode()),AssertionError),
               ((200,b'not-json'),ValueError),((200,b'[]'),AssertionError),
               ((200,b'{"id":1,"username":"owner_changed"}'),AssertionError),
               ((200,json.dumps({**identity,'id':True}).encode()),AssertionError),
               ((200,json.dumps({**identity,'username':'different_owner'}).encode()),AssertionError),
               ((200,json.dumps({**identity,'secret':'unexpected'}).encode()),AssertionError)]
        for value,error in cases:
            api=Mock(side_effect=value) if isinstance(value,Exception) else Mock(return_value=value)
            with self.subTest(error=type(value).__name__),patch.object(smoke.time,'monotonic',return_value=100), \
                 patch.object(smoke.time,'sleep') as sleep,self.assertRaises(error):
                smoke.wait_installed_admin_identity(api,'owner_changed')
            api.assert_called_once();sleep.assert_not_called()

    def test_post_menu_identity_wait_cannot_extend_deadline_or_accept_late_success(self):
        from scripts import system_maintenance_smoke as smoke
        with patch.object(smoke.time,'monotonic',side_effect=[100,120]),patch.object(smoke.time,'sleep') as sleep:
            api=Mock()
            with self.assertRaisesRegex(AssertionError,'original deadline'):
                smoke.wait_installed_admin_identity(api,'owner_changed')
            api.assert_not_called();sleep.assert_not_called()
        api=Mock(side_effect=ConnectionRefusedError(errno.ECONNREFUSED,'synthetic refused'))
        with patch.object(smoke.time,'monotonic',side_effect=[100,100,119.95,120.01]),patch.object(smoke.time,'sleep') as sleep:
            with self.assertRaisesRegex(AssertionError,'original deadline'):
                smoke.wait_installed_admin_identity(api,'owner_changed')
            api.assert_called_once()
            self.assertAlmostEqual(sleep.call_args.args[0],.05)
        api=Mock(return_value=(200,b'{"id":1,"username":"owner_changed","role":"admin"}'))
        with patch.object(smoke.time,'monotonic',side_effect=[100,100,120.01]),patch.object(smoke.time,'sleep') as sleep:
            with self.assertRaisesRegex(AssertionError,'after its original deadline'):
                smoke.wait_installed_admin_identity(api,'owner_changed')
            api.assert_called_once();sleep.assert_not_called()
        for budget in (0,20.01,True,float('nan')):
            with self.subTest(budget=budget),self.assertRaisesRegex(AssertionError,'budget'):
                smoke.wait_installed_admin_identity(Mock(),'owner_changed',timeout=budget)

    def test_actual_tcp_upstream_yaml_is_wrapped_as_one_unchanged_client_node(self):
        # Use the real exporter and smoke's real YAML branch. No binary, socket,
        # service, database, trust store or external endpoint is involved.
        import ast
        from copy import deepcopy
        import inspect
        import yaml
        from app.services.tcp_upstream_export import export_tcp_upstream
        from inbound_routing_real_helpers import yaml_client
        from scripts import system_maintenance_smoke as smoke
        item=types.SimpleNamespace(id=1,core='sing-box',protocol='vless',port=10443,
            remark='installed-node',tag='installed-node',enable=True,expiry_time=0,
            settings={'users':[{'uuid':'11111111-1111-4111-8111-111111111111'}]},
            stream_settings={'tls':{'enabled':True,'server_name':'vpn.example.test',
                'certificate_path':'/fake/cert.pem','key_path':'/fake/key.pem','alpn':['h2','http/1.1']},
                '_vui':{'security':'tls','server_name':'vpn.example.test',
                        'client_fingerprint':'chrome','skip_cert_verify':False}})
        original=deepcopy(vars(item))
        artifact=export_tcp_upstream(item,'127.0.0.1','clash-yaml')
        node=yaml.safe_load(artifact.content)
        self.assertNotIn('proxies',node)
        self.assertIs(node['udp'],False)
        tree=ast.parse(inspect.getsource(smoke.installed_netlink_smoke))
        branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If)
                    and isinstance(n.test,ast.Compare) and isinstance(n.test.left,ast.Name)
                    and n.test.left.id=='source_format')
        program=compile(ast.Module(body=branch.orelse,type_ignores=[]),'<actual-smoke-upstream-yaml>','exec')
        namespace={'yaml':yaml,'raw':artifact.content.encode(),'check':self,'yaml_client':yaml_client,
                   'client_port':1234,'controller_port':1235}
        exec(program,namespace)
        self.assertEqual(namespace['node'],node)
        self.assertEqual(namespace['client_config']['proxies'],[node])
        self.assertEqual(vars(item),original)
        self.assertNotIn('DIRECT',json.dumps(namespace['client_config']))
        namespace['client_config']['proxies'][0]['alpn'].append('copy-only')
        self.assertEqual(namespace['node'],node,'Client shell must not mutate exported fields')
        for change in ({'udp':True},{'udp':0},{'tls':False},{'skip-cert-verify':True},{'network':'udp'}):
            with self.subTest(change=change),patch.object(yaml,'safe_load',return_value={**node,**change}),self.assertRaises(AssertionError):
                exec(program,namespace)

    def test_installed_identity_uses_live_session_not_historical_install_metadata(self):
        from scripts import system_maintenance_smoke as smoke
        import inspect
        source=inspect.getsource(smoke.installed_netlink_smoke)
        capture="admin_identity_before=wait_installed_admin_identity(api,expected_username)"
        compare="check.assertEqual(request('/api/auth/me'),admin_identity_before)"
        self.assertEqual(source.count(capture),1)
        self.assertEqual(source.count(compare),1)
        self.assertLess(source.index(capture),source.index('for row in verified_upgrade_fixture_rows'))
        self.assertGreater(source.index(compare),source.index('service_python(user_program),users_before'))
        self.assertNotIn("config['admin']",source)
        self.assertIn('expected_username=admin_username',(SOURCE/'tests/test_oneclick_system.py').read_text())

    def test_installed_success_marker_is_unique_and_emitted_only_after_finally_cleanup(self):
        from scripts import system_maintenance_smoke as smoke
        import inspect
        source=inspect.getsource(smoke.installed_netlink_smoke)
        self.assertEqual(source.count('print(NETLINK_SYSTEMD_MARKER)'),1)
        self.assertGreater(source.index('print(NETLINK_SYSTEMD_MARKER)'),source.index('finally:'))
        self.assertIn('installed20 panel-child TLS TCP URI/YAML passed',smoke.NETLINK_SYSTEMD_MARKER)


class ProtocolTests(unittest.TestCase):
    def test_real_client_retries_only_observed_listener_syscalls(self):
        from scripts import system_maintenance_smoke as smoke
        cases = {
            'missing_socket': True, 'missing_parent': True, 'refused': True, 'reset': True,
            'missing_run': False, 'untrusted_parent': False, 'untrusted_socket': False,
            'permission': False, 'malformed': False, 'empty': False, 'oversized': False,
            'unsafe_socket_mode': False, 'unexpected_os': False,
        }
        for mode, retryable in cases.items():
            with self.subTest(mode=mode):
                client = module('maintenance_client')
                def lstat(path, *args, **kwargs):
                    name = str(path)
                    missing = {'missing_socket': str(client.SOCKET), 'missing_parent': str(client.SOCKET.parent), 'missing_run': '/run'}
                    if mode in missing and name == missing[mode]:raise FileNotFoundError(errno.ENOENT, 'synthetic missing')
                    if mode == 'permission' and name == str(client.SOCKET):raise PermissionError(errno.EACCES, 'synthetic denied')
                    socket_path = name == str(client.SOCKET)
                    untrusted = (mode == 'untrusted_parent' and name == str(client.SOCKET.parent)
                                 or mode == 'untrusted_socket' and socket_path)
                    permissions = 0o667 if mode == 'unsafe_socket_mode' else 0o660
                    return types.SimpleNamespace(st_uid=1000 if untrusted else 0,
                        st_mode=(stat.S_IFSOCK | permissions) if socket_path else (stat.S_IFDIR | 0o755))
                class FakeSocket:
                    def __init__(self, *args, **kwargs):self.reads = 0
                    def __enter__(self):return self
                    def __exit__(self, *args):pass
                    def settimeout(self, value):pass
                    def connect(self, address):
                        if mode == 'refused':raise ConnectionRefusedError(errno.ECONNREFUSED, 'synthetic refused')
                        if mode == 'unexpected_os':raise OSError(errno.EINVAL, 'synthetic unknown')
                    def sendall(self, value):pass
                    def shutdown(self, value):pass
                    def recv(self, amount):
                        if mode == 'reset':raise ConnectionResetError(errno.ECONNRESET, 'synthetic reset')
                        self.reads += 1
                        if mode == 'empty':return b''
                        if self.reads > 1:return b''
                        if mode == 'malformed':return b'not-json'
                        if mode == 'oversized':return b'x' * 65537
                        return b'{"ok":true,"result":{"busy":false}}'
                stderr = io.StringIO()
                with patch.object(Path, 'lstat', side_effect=lstat), patch.object(socket, 'socket', FakeSocket), \
                     patch('runpy.run_path', return_value=client.__dict__), \
                     patch.object(sys, 'stdin', io.StringIO('{"protocol":1,"operation":"status"}')), \
                     patch.object(sys, 'stderr', stderr), patch.object(sys, 'stdout', io.StringIO()):
                    try:exec(smoke.CLIENT, {})
                    except client.MaintenanceUnavailable as error:
                        stderr.write('MaintenanceUnavailable: ' + str(error) + '\n')
                    else:self.fail('Negative real-client fixture unexpectedly succeeded')
                replies = [subprocess.CompletedProcess([], 1, '', stderr.getvalue()),
                           subprocess.CompletedProcess([], 0, '{"busy":false}', '')]
                with patch.object(smoke.subprocess, 'run', side_effect=replies) as run, \
                     patch.object(smoke, 'maintenance_diagnostics', return_value={}), \
                     patch.object(smoke.time, 'monotonic', return_value=100), patch.object(smoke.time, 'sleep'):
                    if retryable:
                        self.assertEqual(smoke.read_when_available({'operation': 'status'}, 101), {'busy': False})
                        self.assertEqual(run.call_count, 2)
                    else:
                        with self.assertRaises(AssertionError) as raised:
                            smoke.read_when_available({'operation': 'status'}, 101)
                        self.assertIs(type(raised.exception), AssertionError)
                        run.assert_called_once()

    def test_retry_reason_must_be_a_single_exact_fixed_observation(self):
        from scripts import system_maintenance_smoke as smoke
        final = 'MaintenanceUnavailable: 系统维护组件不可用，请通过本机终端检查\n'
        for marker in ('', 'VUI_CI_LISTENER=UNKNOWN\n',
                       'VUI_CI_LISTENER=LISTENER_MISSING\nVUI_CI_LISTENER=LISTENER_MISSING\n'):
            with self.subTest(marker=marker), patch.object(smoke.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', marker+final)), \
                 patch.object(smoke, 'maintenance_diagnostics', return_value={}):
                with self.assertRaises(AssertionError) as raised:smoke.invoke({'operation': 'status'})
                self.assertIs(type(raised.exception), AssertionError)

    def test_request_budget_cannot_extend_the_original_fifteen_second_limit(self):
        from scripts import system_maintenance_smoke as smoke
        for invalid in (0, -1, 15.01, True, float('inf'), float('nan'), '15'):
            with self.subTest(invalid=invalid), patch.object(smoke.subprocess, 'run') as run:
                with self.assertRaisesRegex(AssertionError, 'request budget'):
                    smoke.invoke({'operation': 'status'}, timeout=invalid)
                run.assert_not_called()

    def test_readiness_waits_only_for_transport_and_never_retries_a_mutation(self):
        from scripts import system_maintenance_smoke as smoke
        with patch.object(smoke, 'invoke', side_effect=[smoke.BridgeTransportUnavailable('safe diagnostic'), {'busy': False}]) as invoke, \
             patch.object(smoke.time, 'monotonic', return_value=100), patch.object(smoke.time, 'sleep') as sleep:
            self.assertEqual(smoke.read_when_available({'operation': 'status'}, 101), {'busy': False})
            self.assertEqual(invoke.call_count, 2)
            self.assertTrue(all(c.args[0]['operation'] == 'status' for c in invoke.call_args_list))
            self.assertTrue(all(c.kwargs['timeout'] == 1 for c in invoke.call_args_list))
            sleep.assert_called_once_with(.2)
        for operation in ('submit', 'restore', 'upgrade'):
            with self.subTest(operation=operation), patch.object(smoke, 'invoke') as invoke:
                with self.assertRaisesRegex(AssertionError, 'Only fixed maintenance reads'):
                    smoke.read_when_available({'operation': operation}, 101)
                invoke.assert_not_called()

    def test_readiness_preserves_refusals_unknown_errors_and_original_deadline(self):
        from scripts import system_maintenance_smoke as smoke
        for error in (AssertionError('DAEMON_REFUSED'), ValueError('UNKNOWN')):
            with self.subTest(error=type(error)), patch.object(smoke, 'invoke', side_effect=error) as invoke, \
                 patch.object(smoke.time, 'monotonic', return_value=100), patch.object(smoke.time, 'sleep') as sleep:
                with self.assertRaises(type(error)) as raised:
                    smoke.read_when_available({'operation': 'get'}, 101)
                self.assertIs(raised.exception, error)
                invoke.assert_called_once(); sleep.assert_not_called()
        error = smoke.BridgeTransportUnavailable('safe original diagnostic')
        with patch.object(smoke, 'invoke', side_effect=error) as invoke, \
             patch.object(smoke.time, 'monotonic', side_effect=[100, 101]), patch.object(smoke.time, 'sleep') as sleep:
            with self.assertRaises(smoke.BridgeTransportUnavailable) as raised:
                smoke.read_when_available({'operation': 'status'}, 101)
            self.assertIs(raised.exception, error)
            invoke.assert_called_once(); sleep.assert_not_called()
        with patch.object(smoke, 'invoke') as invoke, patch.object(smoke.time, 'monotonic', return_value=101):
            with self.assertRaisesRegex(AssertionError, 'original deadline'):
                smoke.read_when_available({'operation': 'status'}, 101)
            invoke.assert_not_called()
        with patch.object(smoke, 'invoke', return_value={'busy': False}), \
             patch.object(smoke.time, 'monotonic', side_effect=[100, 102]):
            with self.assertRaisesRegex(AssertionError, 'after its original deadline'):
                smoke.read_when_available({'operation': 'status'}, 101)

    def test_wait_idle_requires_explicit_false_and_respects_quiescence_deadline(self):
        from scripts import system_maintenance_smoke as smoke
        with patch.object(smoke, 'read_when_available', side_effect=[{'busy': True}, {'busy': False}]) as read, \
             patch.object(smoke.time, 'monotonic', return_value=100), patch.object(smoke.time, 'sleep') as sleep:
            self.assertEqual(smoke.wait_idle(101), {'busy': False})
            self.assertEqual(read.call_count, 2); sleep.assert_called_once_with(.2)
        for invalid in ({}, {'busy': 0}, {'busy': None}, {'busy': 'false'}):
            with self.subTest(invalid=invalid), patch.object(smoke, 'read_when_available', return_value=invalid):
                with self.assertRaisesRegex(AssertionError, 'busy state'):smoke.wait_idle(101)
        with patch.object(smoke, 'read_when_available', return_value={'busy': True}), \
             patch.object(smoke.time, 'monotonic', return_value=101):
            with self.assertRaisesRegex(AssertionError, 'original deadline'):smoke.wait_idle(101)

    def test_task_waits_for_idle_after_success_and_submits_exactly_once(self):
        from scripts import system_maintenance_smoke as smoke
        queued = {'id': 'synthetic-task', 'state': 'queued'}
        succeeded = {**queued, 'state': 'succeeded'}
        with patch.object(smoke.time, 'monotonic', return_value=100), patch.object(smoke.time, 'sleep'), \
             patch.object(smoke, 'wait_idle') as idle, \
             patch.object(smoke, 'invoke', return_value=queued) as invoke, \
             patch.object(smoke, 'read_when_available', return_value=succeeded) as read:
            self.assertEqual(smoke.task('backup'), succeeded)
            invoke.assert_called_once_with({'protocol': 1, 'operation': 'submit', 'action': 'backup'}, timeout=15)
            self.assertEqual(idle.call_args_list, [unittest.mock.call(250), unittest.mock.call(250)])
            read.assert_called_once_with({'protocol': 1, 'operation': 'get', 'task_id': 'synthetic-task'}, 250)
        with patch.object(smoke.time, 'monotonic', return_value=100), patch.object(smoke, 'wait_idle') as idle, \
             patch.object(smoke, 'invoke', side_effect=AssertionError('DAEMON_REFUSED')) as invoke:
            with self.assertRaisesRegex(AssertionError, 'DAEMON_REFUSED'):smoke.task('backup')
            invoke.assert_called_once(); idle.assert_called_once_with(250)

    def test_failed_task_and_nonquiescent_worker_cannot_become_success(self):
        from scripts import system_maintenance_smoke as smoke
        queued = {'id': 'synthetic-task', 'state': 'queued'}
        with patch.object(smoke.time, 'monotonic', return_value=100), patch.object(smoke.time, 'sleep'), \
             patch.object(smoke, 'wait_idle') as idle, patch.object(smoke, 'invoke', return_value=queued) as invoke, \
             patch.object(smoke, 'read_when_available', return_value={**queued, 'state': 'failed'}):
            with self.assertRaisesRegex(AssertionError, 'did not confirm success'):smoke.task('backup')
            invoke.assert_called_once(); idle.assert_called_once_with(250)
        with patch.object(smoke.time, 'monotonic', return_value=100), \
             patch.object(smoke, 'wait_idle', side_effect=AssertionError('worker still busy')), patch.object(smoke, 'invoke') as invoke:
            with self.assertRaisesRegex(AssertionError, 'worker still busy'):smoke.task('backup')
            invoke.assert_not_called()
        with patch.object(smoke.time, 'monotonic', return_value=100), patch.object(smoke.time, 'sleep'), \
             patch.object(smoke, 'wait_idle', side_effect=[None, AssertionError('completed child not reaped')]), \
             patch.object(smoke, 'invoke', return_value=queued) as invoke, \
             patch.object(smoke, 'read_when_available', return_value={**queued, 'state': 'succeeded'}):
            with self.assertRaisesRegex(AssertionError, 'child not reaped'):smoke.task('backup')
            invoke.assert_called_once()

    def test_failed_invoke_reports_only_fixed_classification_code_and_time_without_retry(self):
        from scripts import system_maintenance_smoke as smoke
        secret = 'synthetic-private-token-and-configuration'
        cases = (
            ('<run_path>.MaintenanceUnavailable: 系统维护未接受请求，请检查任务与本机受管组件', 'DAEMON_REFUSED'),
            ('<run_path>.MaintenanceUnavailable: 系统维护组件不可用，请通过本机终端检查', 'SOCKET_OR_TRANSPORT_UNAVAILABLE'),
            ('RuntimeError: ' + secret, 'UNKNOWN'),
            ('<run_path>.MaintenanceUnavailable: 系统维护组件不可用，请通过本机终端检查 ' + secret, 'UNKNOWN'),
        )
        for final, expected in cases:
            with self.subTest(expected=expected), \
                 patch.object(smoke.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1,
                     stdout=secret, stderr='Traceback ' + secret + '\n' + final + '\n')) as run, \
                 patch.object(smoke.time, 'monotonic', side_effect=[100, 100.125]), \
                 patch.object(smoke, 'maintenance_diagnostics', return_value={'unit': smoke.MAINTENANCE_UNIT,
                     'ActiveState': 'active', 'journal_codes': ['UNCLASSIFIED']}) as maintenance, \
                 patch.object(smoke, 'service_status_diagnostics', return_value={}) as services:
                with self.assertRaises(AssertionError) as error:
                    smoke.invoke({'protocol': 1, 'operation': 'status', 'synthetic_secret': secret})
                message = str(error.exception)
                self.assertTrue(message.startswith('Installed maintenance bridge unavailable: '))
                record = json.loads(message.split(': ', 1)[1])
                self.assertEqual(record['classification'], expected)
                self.assertEqual(record['returncode'], 1)
                self.assertEqual(record['elapsed_ms'], 125)
                self.assertNotIn(secret, message)
                self.assertNotIn('Traceback', message)
                self.assertNotIn('stderr', record)
                self.assertNotIn('stdout', record)
                run.assert_called_once()
                self.assertEqual(run.call_args.kwargs['timeout'], 15)
                maintenance.assert_called_once()
                self.assertEqual(services.call_count, int(expected == 'DAEMON_REFUSED'))

    def test_invoke_success_expected_denial_and_diagnostic_failure_keep_original_outcomes(self):
        from scripts import system_maintenance_smoke as smoke
        for code, expected_success in ((0, True), (1, False), (0, False)):
            with self.subTest(code=code, expected_success=expected_success), \
                 patch.object(smoke.subprocess, 'run', return_value=subprocess.CompletedProcess([], code,
                     stdout='{"ready": true}', stderr='synthetic-private-token')) as run, \
                 patch.object(smoke, 'maintenance_diagnostics') as diagnostic:
                if expected_success:
                    self.assertEqual(smoke.invoke({}, expect_success=True), {'ready': True})
                elif code:
                    self.assertIsNone(smoke.invoke({}, expect_success=False))
                else:
                    with self.assertRaisesRegex(AssertionError, 'Unexpected maintenance peer accepted'):
                        smoke.invoke({}, expect_success=False)
                run.assert_called_once()
                diagnostic.assert_not_called()
        with patch.object(smoke.subprocess, 'run', return_value=subprocess.CompletedProcess([], 2,
                stdout='synthetic-private-token', stderr='synthetic-private-token')) as run, \
             patch.object(smoke, 'maintenance_diagnostics', side_effect=RuntimeError('synthetic-private-token')):
            with self.assertRaises(AssertionError) as error:
                smoke.invoke({})
            record = json.loads(str(error.exception).split(': ', 1)[1])
            self.assertEqual(record['returncode'], 2)
            self.assertEqual(record['classification'], 'UNKNOWN')
            self.assertEqual(record['diagnostic'], 'DIAGNOSTIC_UNAVAILABLE')
            self.assertNotIn('synthetic-private-token', str(error.exception))
            run.assert_called_once()

    def test_fixed_service_status_projection_hides_unknown_values_and_material_paths(self):
        from scripts import system_maintenance_smoke as smoke
        secret = 'synthetic-private-token'
        raw = ('LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=123\nControlPID=0\n'
               'ControlGroup=/system.slice/v-ui.service\nUser=v-ui\nGroup=v-ui\nKillMode=control-group\n'
               'NeedDaemonReload=no\nFragmentPath=/etc/systemd/system/v-ui.service\nDropInPaths=\n'
               'Unknown=' + secret + '\n').encode()
        record = smoke.safe_service_status(raw, 'v-ui.service')
        self.assertEqual(record['MainPID'], '123')
        self.assertTrue(record['fragment_path_matches'])
        self.assertTrue(record['control_group_matches'])
        self.assertFalse(record['has_drop_ins'])
        self.assertNotIn(secret, json.dumps(record))
        corrupt = ('LoadState=' + secret + '\nActiveState=' + secret + '\nSubState=' + secret +
                   '\nMainPID=' + secret + '\nControlPID=-1\nUser=' + secret + '\nGroup=' + secret +
                   '\nKillMode=' + secret + '\nNeedDaemonReload=' + secret + '\nFragmentPath=/' + secret +
                   '\nControlGroup=/' + secret + '\nDropInPaths=/' + secret + '\n').encode()
        record = smoke.safe_service_status(corrupt, 'v-ui.service')
        self.assertEqual(record['MainPID'], 'INVALID')
        self.assertEqual(record['ControlPID'], 'INVALID')
        self.assertEqual(record['ActiveState'], 'OTHER')
        self.assertFalse(record['fragment_path_matches'])
        self.assertFalse(record['control_group_matches'])
        self.assertTrue(record['has_drop_ins'])
        self.assertNotIn(secret, json.dumps(record))
        self.assertNotIn('/', json.dumps(record))
        for invalid in (b'ActiveState=active\nActiveState=inactive\n', b'malformed\n'):
            self.assertEqual(smoke.safe_service_status(invalid, 'v-ui.service'), {'diagnostic': 'INVALID_STATUS'})
        with self.assertRaises(ValueError): smoke.safe_service_status(raw, 'sshd.service')

    def test_service_status_diagnostics_only_reads_three_fixed_units_with_bounded_commands(self):
        from scripts import system_maintenance_smoke as smoke
        with patch.object(smoke, 'bounded_diagnostic', side_effect=[b'ActiveState=active\n', None,
                OSError('synthetic-private-token')]) as query:
            records = smoke.service_status_diagnostics()
        self.assertEqual(tuple(records), smoke.SERVICE_UNITS)
        self.assertEqual(records['v-ui.service'], {'ActiveState': 'active'})
        for unit in smoke.SERVICE_UNITS[1:]:
            self.assertEqual(records[unit], {'diagnostic': 'DIAGNOSTIC_UNAVAILABLE'})
        self.assertNotIn('synthetic-private-token', json.dumps(records))
        for call, unit in zip(query.call_args_list, smoke.SERVICE_UNITS):
            self.assertEqual(call.args[0], ['/usr/bin/timeout', '3s', '/usr/bin/systemctl', 'show',
                '--all', '--no-pager', '--property=' + ','.join(smoke.SERVICE_FIELDS), unit])

    def test_failed_service_diagnostics_only_disclose_fixed_classifications(self):
        from scripts.system_maintenance_smoke import safe_maintenance_journal, maintenance_diagnostics
        raw = ('\n'.join(json.dumps({'MESSAGE': text}) for text in (
            'V-UI maintenance startup refused: STARTUP_SOCKET_DIRECTORY private-token-secret',
            'ModuleNotFoundError private-grant-secret', 'arbitrary-url-password-secret'))).encode()
        self.assertEqual(safe_maintenance_journal(raw), ['STARTUP_SOCKET_DIRECTORY', 'MODULE_MISSING', 'UNCLASSIFIED'])
        with patch('scripts.system_maintenance_smoke.bounded_diagnostic', side_effect=[
                b'ActiveState=failed\nExecMainStatus=1\nSecret=private-grant-secret\n', raw]) as run:
            result = maintenance_diagnostics()
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(result['ExecMainStatus'], '1')
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            self.assertIn('v-ui-maintenance.service', ' '.join(call.args[0]))

    def test_private_umask_creates_exact_socket_directory_mode_via_new_fd_only(self):
        @contextmanager
        def directory(path):
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try: yield fd
            finally: os.close(fd)
        real_fstat = os.fstat
        def root_info(fd):
            info = real_fstat(fd)
            return types.SimpleNamespace(st_uid=0, st_gid=info.st_gid, st_mode=info.st_mode,
                st_dev=info.st_dev, st_ino=info.st_ino)
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'socket-parent'
            previous = os.umask(0o077)
            try:
                with patch.object(daemon.os, 'fstat', side_effect=root_info), patch.object(daemon.os, 'fchown'):
                    daemon.directory(types.SimpleNamespace(trusted_directory=directory), target, 0o750, os.getgid())
                self.assertEqual(target.stat().st_mode & 0o777, 0o750)
                # Existing metadata is not silently repaired or re-endorsed.
                target.chmod(0o700)
                with patch.object(daemon.os, 'fstat', side_effect=root_info), patch.object(daemon.os, 'fchmod') as chmod:
                    with self.assertRaises(ValueError):
                        daemon.directory(types.SimpleNamespace(trusted_directory=directory), target, 0o750, os.getgid())
                    chmod.assert_not_called()
            finally:
                os.umask(previous)

    def test_installed_summary_exposes_only_fixed_safe_version_target_readiness(self):
        config = {'release_id': '0.4.17-' + 'a' * 12, 'target': 'x86_64-gnu', 'ready': True,
                  'domain': 'private.example.test', 'email': 'private@example.test', 'admin': 'private', 'bootstrap_python': '/private/path'}
        fake_ops = types.SimpleNamespace(trusted_bytes=lambda path, limit: json.dumps(config).encode(),
            decode_json=ops.decode_json, CONFIG=Path('/etc/v-ui/service.json'), RELEASE=ops.RELEASE)
        result = daemon.install_summary(fake_ops)
        self.assertEqual(set(result), {'release_id', 'target', 'ready'})
        self.assertNotIn('private', json.dumps(result))
        config['release_id'] = '/private/path'
        with self.assertRaises(ValueError): daemon.install_summary(fake_ops)

    def test_capacity_prunes_only_ended_nonbackup_records_and_reserves_operating_slots(self):
        @contextmanager
        def directory(path):
            fd = os.open(path, os.O_RDONLY)
            try: yield fd
            finally: os.close(fd)
        fake_ops = types.SimpleNamespace(trusted_bytes=lambda path, limit: path.read_bytes(),
            decode_json=ops.decode_json, trusted_directory=directory)
        with tempfile.TemporaryDirectory() as temporary, patch.object(daemon, 'TASKS', Path(temporary)), patch.object(daemon, 'MAX_TASKS', 4), patch.object(daemon, 'MAX_BACKUPS', 1):
            store = daemon.Store(fake_ops)
            backup = {'id': 'a' * 32, 'action': 'backup', 'state': 'succeeded', 'created_at': 1,
                      '_backup': {'name': 'kept.zip', 'sha256': 'f' * 64}}
            active = {'id': 'b' * 32, 'action': 'restart', 'state': 'running', 'created_at': 2}
            store.write(backup); store.write(active)
            for number in (3, 4):
                store.write({'id': str(number) * 32, 'action': 'status', 'state': 'succeeded', 'created_at': number})
            with self.assertRaises(ValueError): store.create('backup')
            status = store.create('status')
            self.assertEqual(status['state'], 'queued')
            status['state'] = 'succeeded'; store.write(status)
            start = store.create('start')
            self.assertEqual(start['state'], 'queued')
            self.assertEqual(store.read(active['id'])['state'], 'running')
            self.assertEqual(store.read(backup['id'])['_backup'], backup['_backup'])
            self.assertEqual(len(store.all()), 4)

    def test_journal_only_returns_fixed_classifications_and_discards_all_raw_text(self):
        records = [
            {'_SYSTEMD_UNIT': 'v-ui.service', '__REALTIME_TIMESTAMP': '1700000000000000', 'MESSAGE': 'PermissionError https://secret/?token=PRIVATE'},
            {'_SYSTEMD_UNIT': 'v-ui-http01.service', 'MESSAGE': 'Bearer PRIVATE arbitrary private key text'},
            {'_SYSTEMD_UNIT': 'sshd.service', 'MESSAGE': 'PermissionError SSH_PRIVATE'},
        ]
        result = daemon.safe_logs(('\n'.join(json.dumps(row) for row in records)).encode())
        self.assertEqual([row['code'] for row in result], ['PERMISSION_DENIED', 'UNCLASSIFIED'])
        self.assertEqual(result[0]['timestamp'], 1700000000)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('MESSAGE', json.dumps(result))

    def test_protocol_closed_actions_fields_and_fixed_backup_identity(self):
        for action in daemon.ACTIONS:
            value = {'protocol': 1, 'operation': 'submit', 'action': action}
            if action == 'restore': value['backup_id'] = 'a' * 32
            self.assertEqual(daemon.validate(value), 'submit')
        for change in ({'url': 'https://evil.test'}, {'unit': 'sshd.service'}, {'command': 'id'},
                       {'path': '/etc/shadow'}, {'action': 'stage'}, {'protocol': True},
                       {'action': 'restore', 'backup_id': '../escape'}, {'action': 'restore'},
                       {'action': 'status', 'backup_id': 'a' * 32}):
            value = {'protocol': 1, 'operation': 'submit', 'action': 'upgrade', **change}
            with self.subTest(change=change), self.assertRaises((ValueError, KeyError)):
                daemon.validate(value)

    def test_receive_checks_kernel_peer_and_bounded_json_duplicate_keys(self):
        connection = Mock()
        connection.getsockopt.return_value = struct.pack('3i', 9, 991, 991)
        connection.recv.side_effect = [b'{"protocol":1,"operation":"status"}\n', b'']
        with patch.object(socket, 'SO_PEERCRED', 17, create=True):
            self.assertEqual(daemon.receive(connection, ops, 991)['operation'], 'status')
            connection.getsockopt.return_value = struct.pack('3i', 9, 992, 991)
            with self.assertRaises(ValueError): daemon.receive(connection, ops, 991)
            connection.getsockopt.return_value = struct.pack('3i', 9, 991, 991)
            for raw in (b'x' * 4097, b'{"protocol":1,"protocol":1,"operation":"status"}\n',
                        b'{"protocol":1,"operation":"status"}\ntrailing', b'{}'):
                connection.recv.side_effect = [raw, b'']
                with self.subTest(raw=raw[:20]), self.assertRaises((ValueError, ops.Refused)):
                    daemon.receive(connection, ops, 991)

    def test_terminal_grants_allow_only_fixed_nonroot_subcommands(self):
        self.assertEqual(cli.arguments(['list']), ['list'])
        self.assertEqual(cli.arguments(['revoke', '1']), ['revoke', '--id', '1'])
        self.assertEqual(cli.arguments(['create', 'diagnose,nodes', '1800']), ['create', '--scopes', 'diagnose,nodes', '--seconds', '1800'])
        self.assertEqual(cli.arguments(['create', 'diagnose', '15552000']), ['create', '--scopes', 'diagnose', '--seconds', '15552000'])
        for value in (['-c', 'id'], ['revoke', '../1'], ['create', 'admin', '1800'],
                      ['create', 'diagnose', '15552001'], ['create', 'diagnose', '999999999'],
                      ['create', 'diagnose', '-1'], ['create', 'diagnose', '15552000.0'],
                      ['create', 'diagnose,diagnose', '1800']):
            with self.subTest(value=value), self.assertRaises(ValueError): cli.arguments(value)

    def test_store_persists_interruption_and_hides_internal_backup_data(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(daemon, 'TASKS', Path(temporary)):
            fake_ops = types.SimpleNamespace(trusted_bytes=lambda path, limit: path.read_bytes(),
                decode_json=ops.decode_json, trusted_directory=no_lock)
            # fsync requires an actual directory fd, without a fake root trust.
            @contextmanager
            def directory(path):
                fd = os.open(path, os.O_RDONLY)
                try: yield fd
                finally: os.close(fd)
            fake_ops.trusted_directory = directory
            store = daemon.Store(fake_ops)
            row = {'id': 'a' * 32, 'action': 'backup', 'state': 'running', 'created_at': 1,
                   '_backup': {'name': 'private', 'sha256': 'secret'}, 'backup_id': 'internal'}
            store.write(row)
            self.assertEqual((Path(temporary) / (row['id'] + '.json')).stat().st_mode & 0o777, 0o600)
            store.interrupt()
            restored = store.read(row['id'])
            self.assertEqual(restored['state'], 'failed')
            self.assertEqual(restored['error'], 'MAINTENANCE_INTERRUPTED')
            self.assertNotIn('_backup', daemon.public(restored))
            self.assertNotIn('backup_id', daemon.public(restored))

    def test_root_menu_never_launches_application_as_root(self):
        account = types.SimpleNamespace(pw_uid=101, pw_gid=102)
        with patch.object(ops, 'read_input', side_effect=['2']), patch.object(ops, 'verify_installation', return_value=('systemd', account)), patch.object(ops.subprocess, 'run') as run:
            ops.terminal_grants()
        self.assertEqual(run.call_args.kwargs['user'], 101)
        self.assertEqual(run.call_args.kwargs['group'], 102)
        self.assertEqual(run.call_args.kwargs['extra_groups'], [])
        self.assertEqual(run.call_args.args[0][-1], 'list')
        self.assertNotIn('PYTHONPATH', run.call_args.kwargs['env'])

    def test_terminal_grants_use_chinese_presets_and_default_readonly(self):
        account = types.SimpleNamespace(pw_uid=101, pw_gid=102)
        for answers, scopes, seconds in ((['1', '', ''], 'diagnose', '1800'),
                (['1', '3', '60'], 'diagnose,nodes,share,relay,certificates,operate,upgrade,destructive', '3600'),
                (['1', '2', '180天'], 'diagnose,nodes,share,relay,certificates,operate,upgrade', '15552000'),
                (['1', '1', '180d'], 'diagnose', '15552000'),
                (['1', '1', '259200'], 'diagnose', '15552000')):
            with self.subTest(answers=answers), patch.object(ops, 'read_input', side_effect=answers), patch.object(ops, 'verify_installation', return_value=('systemd', account)), patch.object(ops.subprocess, 'run') as run:
                ops.terminal_grants()
                self.assertEqual(run.call_args.args[0][-3:], ['create', scopes, seconds])

    def test_terminal_grants_reject_over_max_without_launch(self):
        account = types.SimpleNamespace(pw_uid=101, pw_gid=102)
        for duration in ('259201', '181d', '180.0d', '０', '-1'):
            with self.subTest(duration=duration), patch.object(ops, 'read_input', side_effect=['1', '1', duration]), patch.object(ops, 'verify_installation', return_value=('systemd', account)), patch.object(ops.subprocess, 'run') as run:
                with self.assertRaises(ops.Refused):ops.terminal_grants()
                run.assert_not_called()


class UpgradeTests(unittest.TestCase):
    def test_unattended_upgrade_cannot_create_or_request_initial_credentials(self):
        from scripts import install_system as installer
        with patch.object(installer, 'installer_account', return_value=False) as account, patch.object(installer, 'controlling_terminal', side_effect=AssertionError('no terminal')):
            with self.assertRaises(installer.InstallError):
                with installer.provision_initial_account('/runtime', '/payload', {'admin': 'existing'}, require_existing=True):
                    self.fail('missing account was accepted')
        self.assertEqual(account.call_args.args[2], 'installer-status')
        self.assertEqual(account.call_count, 1)

    def manifest(self):
        raw = ''.join('a' * 64 + '  ' + name + '\n' for name in sorted(upgrade.ASSETS | upgrade.BUNDLES)).encode()
        return raw, hashlib.sha256(raw).hexdigest()

    def test_manifest_is_complete_pinned_closed_and_duplicate_free(self):
        raw, pin = self.manifest()
        self.assertEqual(set(upgrade.checksums(raw, pin)), upgrade.ASSETS | upgrade.BUNDLES)
        for changed in (raw + b'b' * 64 + b'  arbitrary.sh\n', raw + raw.splitlines(keepends=True)[0],
                        raw.replace(b'  install.sh\n', b'  ../install.sh\n')):
            with self.subTest(changed=changed[-80:]), self.assertRaises(upgrade.UpgradeRefused):
                upgrade.checksums(changed, hashlib.sha256(changed).hexdigest())
        with self.assertRaises(upgrade.UpgradeRefused): upgrade.checksums(raw, '0' * 64)

    def test_fixed_https_host_boundary_has_no_proxy_or_unverified_tls(self):
        for value in (upgrade.SETUP, upgrade.LATEST, upgrade.BASE + 'v0.4.17/SHA256SUMS'):
            upgrade.url_allowed(value)
        for value in ('http://github.com/x', 'https://github.com.evil.test/x', 'https://evil.test/x',
                      'https://u@github.com/x', 'https://github.com:4430/x', 'file:///etc/passwd'):
            with self.subTest(value=value), self.assertRaises(upgrade.UpgradeRefused): upgrade.url_allowed(value)

    def test_canonical_pin_and_official_stable_release_must_agree(self):
        setup = b'  local version=v0.4.17 version_seen=0 argument domain=\n  local kit_sha=' + b'a' * 64 + b'\n'
        release = {'tag_name': 'v0.4.17', 'draft': False, 'prerelease': False, 'published_at': '2026-11-04'}
        with patch.object(upgrade, 'download', side_effect=[setup, json.dumps(release).encode()]):
            self.assertEqual(upgrade.accepted_release(ops), ('v0.4.17', 'a' * 64))
        for change in ({'tag_name': 'v0.4.18'}, {'draft': True}, {'prerelease': True}, {'published_at': None}):
            with self.subTest(change=change), patch.object(upgrade, 'download', side_effect=[setup, json.dumps({**release, **change}).encode()]):
                with self.assertRaises(upgrade.UpgradeRefused): upgrade.accepted_release(ops)

    def fake_ops(self, config):
        return types.SimpleNamespace(installation_lock=no_lock, verify_installation=lambda: ('systemd', object()),
            decode_json=ops.decode_json, trusted_bytes=lambda path, limit: json.dumps(config).encode(),
            CONFIG=Path('/etc/v-ui/service.json'), PYTHON=Path('/usr/bin/python3'), CONTROL=Path('/usr/local/lib/v-ui'), ENV=ops.ENV,
            trusted_directory=no_lock)

    def config(self):
        return {'installer': 1, 'ready': True, 'release_id': '0.4.17-aaaaaaaaaaaa', 'certificate_mode': 'managed',
                'domain': 'panel.example.test', 'target': 'x86_64-gnu',
                'ca_authorizations': [{'domain': 'panel.example.test', 'environment': 'production'}]}

    def test_downgrade_and_same_version_do_not_download_or_mutate(self):
        for version in ('v0.4.16', 'v0.4.17'):
            with patch.object(upgrade, 'accepted_release', return_value=(version, 'a' * 64)), patch.object(upgrade, 'download') as download, patch.object(upgrade, 'run') as run:
                result = upgrade.upgrade(self.fake_ops(self.config()))
            self.assertFalse(result['upgraded']); download.assert_not_called(); run.assert_not_called()

    def test_missing_ca_authorization_refuses_before_any_network(self):
        config = self.config(); config['ca_authorizations'] = []
        with patch.object(upgrade, 'accepted_release') as accepted:
            with self.assertRaises(upgrade.UpgradeRefused): upgrade.upgrade(self.fake_ops(config))
        accepted.assert_not_called()

    def test_full_download_chain_uses_manifest_before_verifier_and_fixed_unattended_flags(self):
        config = self.config(); config['release_id'] = '0.4.16-aaaaaaaaaaaa'
        contents = {name: b'accepted fake bytes' for name in upgrade.ASSETS | upgrade.BUNDLES}
        contents['RELEASE.json'] = json.dumps({'version': '0.4.17', 'release_id': '0.4.17-' + 'a' * 12, 'source_commit': 'a' * 40}).encode()
        raw = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n' for name, data in sorted(contents.items())).encode()
        pin = hashlib.sha256(raw).hexdigest()
        downloaded = []
        def download(url, path=None, **kwargs):
            if path is None: return raw
            downloaded.append(path.name)
            data = contents[path.name]; path.write_bytes(data)
            return hashlib.sha256(data).hexdigest()
        temporary = tempfile.TemporaryDirectory()
        with temporary as folder, patch.object(upgrade, 'accepted_release', return_value=('v0.4.17', pin)), patch.object(upgrade.tempfile, 'TemporaryDirectory', return_value=no_lock()), patch.object(upgrade, 'download', side_effect=download), patch.object(upgrade, 'run') as run:
            # A root-only staging location is simulated, never installed locally.
            @contextmanager
            def work(**kwargs): yield folder
            with patch.object(upgrade.tempfile, 'TemporaryDirectory', side_effect=work):
                result = upgrade.upgrade(self.fake_ops(config))
        self.assertTrue(result['upgraded'])
        self.assertEqual(set(downloaded) & upgrade.BUNDLES, {'vui-linux-x86_64-gnu.zip'})
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 3)
        self.assertIn('--preflight', commands[0]); self.assertIn('--unattended-upgrade', commands[1]); self.assertIn('--refresh', commands[2])
        for command in commands:
            self.assertNotIn('--interactive', command); self.assertNotIn('--accept-terms', command)


if __name__ == '__main__': unittest.main()
