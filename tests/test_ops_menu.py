"""Read-only menu boundaries: fake metadata/services and real isolated PTYs."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from app import ops_menu as menu, release_tools
from scripts.ops_menu_smoke import MenuSession, installed_menu_smoke, tree_snapshot


SOURCE = Path(__file__).resolve().parents[1]


class Terminal(io.StringIO):
    def isatty(self):
        return True


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-menu-')))
        self.release = self.root / 'releases' / 'fixture-release'
        self.payload = self.release / 'payload'
        self.runtime = self.release / 'runtime'
        for directory in (self.payload / 'app', self.payload / 'scripts', self.runtime):
            directory.mkdir(parents=True, mode=0o700)
        for directory in self.root.rglob('*'):
            if directory.is_dir():
                directory.chmod(0o700)
        for name in ('app/ops_menu.py', 'app/release_tools.py', 'scripts/deploy.py'):
            shutil.copyfile(SOURCE / name, self.payload / name)
            (self.payload / name).chmod(0o600)
        (self.runtime / 'fixture').write_bytes(b'not an executable\n')
        (self.runtime / 'fixture').chmod(0o600)
        self.write(self.payload / 'MANIFEST.json', {'schema': 1, 'release_id': 'fixture-release',
            'files': release_tools.files_in(self.payload)})
        self.write(self.release / 'READY.json', {'runtime_key': release_tools.target_key(),
            'manifest_sha256': release_tools.digest((self.payload / 'MANIFEST.json').read_bytes()),
            'runtime_tree_sha256': release_tools.runtime_tree_digest(self.runtime)})
        self.write(self.root / 'CURRENT.json', {'release_id': 'fixture-release', 'previous_id': None})

    def write(self, path, value):
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def run_menu(self, choices, *, input_tty=True, output_tty=True, root=None):
        stdin = Terminal(choices) if input_tty else io.StringIO(choices)
        stdout = Terminal() if output_tty else io.StringIO()
        stderr = io.StringIO()
        with patch.object(menu.sys, 'stdin', stdin), patch.object(menu.sys, 'stdout', stdout), \
                patch.object(menu.sys, 'stderr', stderr):
            code = menu.run_menu(root or self.root)
        return code, stdout.getvalue() + stderr.getvalue()

    def test_first_screen_exit_never_reads_metadata_hashes_or_creates_files(self):
        before = tree_snapshot(self.root)
        with patch.object(menu, '_json_file', side_effect=AssertionError('metadata read')), \
                patch.object(menu, '_query', side_effect=AssertionError('service query')), \
                patch.object(release_tools, 'active', side_effect=AssertionError('full hash')), \
                patch.object(release_tools, 'private_root', side_effect=AssertionError('mkdir')), \
                patch.object(release_tools, 'supported_environment', side_effect=AssertionError('pidfd')):
            code, text = self.run_menu('0\n')
        self.assertEqual(code, 0, text)
        self.assertIn('运维菜单', text)
        self.assertEqual(tree_snapshot(self.root), before)

    def test_all_readonly_paths_do_not_call_mutating_tools(self):
        with ExitStack() as stack:
            for name in ('activate', 'stage', 'backup', 'restore', 'recover_restore', 'health_check',
                         'private_root', 'stopped', 'lease', 'supported_environment'):
                stack.enter_context(patch.object(release_tools, name, side_effect=AssertionError(name)))
            stack.enter_context(patch.object(menu, '_query', side_effect=AssertionError('host query')))
            code, text = self.run_menu('nonsense\nh\n1\n2\n3\n0\n')
        self.assertEqual(code, 0, text)
        self.assertIn('无效选择', text)
        self.assertIn('服务状态: unavailable', text)
        self.assertIn('完整校验通过', text)

    def test_non_tty_refuses_before_input_or_metadata(self):
        for input_tty, output_tty in ((False, True), (True, False), (False, False)):
            with self.subTest(input_tty=input_tty, output_tty=output_tty), \
                    patch.object(menu, '_directory', side_effect=AssertionError('directory read')):
                code, text = self.run_menu('3\n', input_tty=input_tty, output_tty=output_tty)
                self.assertEqual(code, 2, text)
                self.assertNotIn('V-UI 运维菜单', text)

    def test_root_wrong_owner_missing_and_unsafe_permissions_refused(self):
        for identity in ('geteuid', 'getuid'):
            with patch.object(menu.os, identity, return_value=0):
                self.assertEqual(self.run_menu('0\n')[0], 1)
        with patch.object(menu.os, 'geteuid', return_value=os.geteuid() + 1):
            self.assertEqual(self.run_menu('0\n')[0], 1)
        missing = self.root / 'missing'
        self.assertEqual(self.run_menu('0\n', root=missing)[0], 1)
        self.assertFalse(missing.exists())
        self.root.chmod(0o750)
        self.assertEqual(self.run_menu('0\n')[0], 1)

    def test_symlink_root_and_ancestor_refused(self):
        link = self.root / 'link'
        link.symlink_to(self.release, target_is_directory=True)
        self.assertEqual(self.run_menu('0\n', root=link)[0], 1)
        self.assertEqual(self.run_menu('0\n', root=link / 'payload')[0], 1)

    def test_eof_and_keyboard_interrupt_clean(self):
        self.assertEqual(self.run_menu('')[0], 0)
        with patch.object(menu, 'show_metadata', side_effect=KeyboardInterrupt):
            code, text = self.run_menu('2\n')
        self.assertEqual(code, 130)
        self.assertNotIn('Traceback', text)
        with patch.object(menu, '_directory', side_effect=KeyboardInterrupt):
            code, text = self.run_menu('0\n')
        self.assertEqual(code, 130)
        self.assertNotIn('Traceback', text)

    def test_overlong_input_suffix_cannot_become_a_selection(self):
        with patch.object(release_tools, 'active') as active, patch.object(menu, '_query') as query:
            code, text = self.run_menu('x' * 65 + '3\n')
        self.assertEqual(code, 2)
        self.assertIn('输入过长', text)
        active.assert_not_called()
        query.assert_not_called()

    def test_canonical_root_wrong_dedicated_owner_rejected_at_entry(self):
        with patch.object(menu, 'MANAGED_ROOT', self.root), \
                patch.object(menu.pwd, 'getpwnam') as account, patch.object(release_tools, 'active') as active:
            account.return_value.pw_uid = os.geteuid() + 1
            code, text = self.run_menu('3\n')
        self.assertEqual(code, 1)
        self.assertNotIn('V-UI 运维菜单', text)
        active.assert_not_called()

    def test_metadata_only_does_not_read_manifest_or_full_tree(self):
        original = menu._json_file
        def read(directory, name, *args):
            self.assertNotEqual(name, 'MANIFEST.json')
            return original(directory, name, *args)
        with patch.object(menu, '_json_file', side_effect=read), \
                patch.object(release_tools, 'active', side_effect=AssertionError('hash')):
            code, text = self.run_menu('2\n0\n')
        self.assertEqual(code, 0)
        self.assertIn('活动版本: fixture-release', text)
        self.assertIn('尚未执行完整性校验', text)

    def test_only_explicit_three_verifies(self):
        with patch.object(release_tools, 'active', wraps=release_tools.active) as active:
            self.assertEqual(self.run_menu('1\n2\nh\n0\n')[0], 0)
            active.assert_not_called()
            self.assertIn('完整校验通过', self.run_menu('3\n0\n')[1])
            active.assert_called_once_with(self.root)

    def test_invalid_current_never_runs_verifier_or_echoes_secrets(self):
        current = self.root / 'CURRENT.json'
        for raw in (b'not-json secret', b'[]', b'{"release_id":"../escape"}',
                    b'{"release_id":"one","release_id":"two"}',
                    json.dumps({'release_id': 'secret\x1b[2J\n'}).encode(),
                    b'{' * 16000, b' ' * (menu.METADATA_LIMIT + 1)):
            with self.subTest(raw=raw[:30]):
                current.write_bytes(raw)
                with patch.object(release_tools, 'active') as active:
                    code, text = self.run_menu('2\n3\n0\n')
                self.assertEqual(code, 0)
                self.assertIn('完整校验拒绝', text)
                self.assertNotIn('secret', text)
                active.assert_not_called()

    def test_symlink_fifo_public_and_oversized_metadata_rejected(self):
        for path in (self.root / 'CURRENT.json', self.release / 'READY.json', self.payload / 'MANIFEST.json'):
            original = path.read_bytes()
            for kind in ('symlink', 'fifo', 'public', 'oversized'):
                with self.subTest(path=path.name, kind=kind):
                    path.unlink()
                    if kind == 'symlink':
                        path.symlink_to(self.root / 'not-present')
                    elif kind == 'fifo':
                        os.mkfifo(path, 0o600)
                    else:
                        path.write_bytes(b' ' * (menu.MANIFEST_LIMIT + 1) if kind == 'oversized' else original)
                        path.chmod(0o644 if kind == 'public' else 0o600)
                    with patch.object(release_tools, 'active') as active:
                        code, text = self.run_menu('3\n0\n')
                    self.assertEqual(code, 0)
                    self.assertIn('完整校验拒绝', text)
                    active.assert_not_called()
                    path.unlink()
                    path.write_bytes(original)
                    path.chmod(0o600)

    def test_symlink_directory_boundaries_rejected(self):
        for path in (self.root / 'releases', self.release, self.payload, self.runtime):
            with self.subTest(path=path):
                target = path.with_name(path.name + '-moved')
                path.rename(target)
                path.symlink_to(target, target_is_directory=True)
                try:
                    with patch.object(release_tools, 'active') as active:
                        code, text = self.run_menu('3\n0\n')
                    self.assertEqual(code, 0)
                    self.assertIn('完整校验拒绝', text)
                    active.assert_not_called()
                finally:
                    path.unlink()
                    target.rename(path)

    def test_selection_change_cannot_report_verification_success(self):
        first = menu.installation_metadata(self.root, full=True)
        with patch.object(menu, 'installation_metadata', side_effect=[first, {**first, 'previous_id': 'changed'}]), \
                patch.object(release_tools, 'active', return_value=(self.release, {'release_id': 'fixture-release'})):
            text = io.StringIO()
            menu.verify_active(self.root, text)
        self.assertIn('选择发生变化', text.getvalue())
        self.assertNotIn('完整校验通过', text.getvalue())
        with patch.object(release_tools, 'active', return_value=(self.release, {'release_id': 'other'})):
            self.assertNotIn('完整校验通过', self.run_menu('3\n0\n')[1])

    def test_hash_failure_does_not_echo_raw_exception(self):
        with patch.object(release_tools, 'active', side_effect=RuntimeError('SECRET\x1b[2J')):
            code, text = self.run_menu('3\n0\n')
        self.assertEqual(code, 0)
        self.assertIn('完整校验失败', text)
        self.assertNotIn('SECRET', text)

    def test_per_user_root_never_reads_global_service_config(self):
        with patch.object(menu.pwd, 'getpwnam', side_effect=AssertionError('global owner lookup')), \
                patch.object(menu, '_query', side_effect=AssertionError('host query')):
            self.assertEqual(self.run_menu('1\n2\n0\n')[0], 0)

    def test_managed_config_requires_dedicated_owner(self):
        with patch.object(menu, 'MANAGED_ROOT', self.root), \
                patch.object(menu.pwd, 'getpwnam') as account:
            account.return_value.pw_uid = os.geteuid() + 1
            with self.assertRaises(menu.MetadataError):
                menu.managed_metadata(self.root)

    def managed(self, config):
        stack = ExitStack()
        stack.enter_context(patch.object(menu, 'MANAGED_ROOT', self.root))
        account = stack.enter_context(patch.object(menu.pwd, 'getpwnam'))
        account.return_value.pw_uid = os.geteuid()
        # Read a fake config through a real private directory; no host file touched.
        stack.enter_context(patch.object(menu, 'SERVICE_CONFIG', self.root / 'service.json'))
        original_directory = menu._directory
        stack.enter_context(patch.object(menu, '_directory', side_effect=
            lambda path, **unused: original_directory(path, owner=os.geteuid())))
        original_json = menu._json_file
        stack.enter_context(patch.object(menu, '_json_file', side_effect=
            lambda directory, name, owner, limit=menu.METADATA_LIMIT:
                original_json(directory, name, os.geteuid(), limit)))
        self.write(self.root / 'service.json', config)
        return stack

    def config(self):
        return {'installer': 1, 'service_manager': 'systemd', 'ready': True,
                'origin': 'https://panel.example.test:8443', 'target': 'x86_64-gnu',
                'email': 'SECRET-email', 'admin': 'SECRET-admin',
                'bootstrap_python': '/SECRET/never-execute', 'private_key': 'SECRET-key'}

    def test_managed_metadata_is_strict_allowlist(self):
        with self.managed(self.config()):
            info = menu.managed_metadata(self.root)
            self.assertEqual(set(info), {'origin', 'ready', 'service_manager', 'target'})
            text = io.StringIO()
            menu.show_metadata(self.root, text)
        self.assertIn('https://panel.example.test:8443', text.getvalue())
        self.assertNotIn('SECRET', text.getvalue())
        self.assertNotIn('bootstrap_python', text.getvalue())

    def test_bad_service_metadata_no_query_no_leak(self):
        for key, value in (('installer', True), ('ready', 1), ('service_manager', 'evil'),
                           ('target', '../bad'), ('origin', 'https://user:SECRET@host.test'),
                           ('origin', 'https://host.test/?SECRET'), ('origin', 'http://host.test'),
                           ('origin', 'https://host.test\x1b[2J'), ('origin', 'https://host.test:99999')):
            with self.subTest(key=key, value=value), self.managed({**self.config(), key: value}), \
                    patch.object(menu, '_query') as query:
                text = io.StringIO()
                menu.show_services(self.root, text)
                self.assertIn('unavailable', text.getvalue())
                self.assertNotIn('SECRET', text.getvalue())
                query.assert_not_called()

    def test_system_config_owner_and_writability_checked(self):
        self.write(self.root / 'service.json', self.config())
        with menu._directory(self.root, owner=os.geteuid()) as directory:
            with self.assertRaises(menu.MetadataError):
                menu._json_file(directory, 'service.json', 0)
        with self.assertRaises(menu.MetadataError):
            with menu._directory(self.root, owner=0):
                pass

    def test_systemd_states_fixed_argv_and_socket_activation(self):
        replies = [(0, 'LoadState=loaded\nActiveState=active\nSubState=running\n', None),
                   (0, 'LoadState=loaded\nActiveState=active\nSubState=listening\n', None),
                   (0, 'LoadState=loaded\nActiveState=inactive\nSubState=dead\n', None)]
        with self.managed(self.config()), patch.object(menu, '_trusted_binary', return_value='/usr/bin/systemctl'), \
                patch.object(menu, '_query', side_effect=replies) as query:
            text = io.StringIO()
            menu.show_services(self.root, text)
        self.assertIn('HTTP-01: 按需响应已就绪', text.getvalue())
        self.assertIn('v-ui-http01.service: inactive (dead)', text.getvalue())
        self.assertEqual([call.args[0] for call in query.call_args_list], [
            ['/usr/bin/systemctl', '--no-pager', '--no-ask-password', 'show',
             '--property=LoadState', '--property=ActiveState', '--property=SubState', unit]
            for unit in menu.SYSTEMD_UNITS])

    def test_query_errors_are_never_inactive(self):
        cases = [((None, '', 'timeout'), 'unknown'), ((None, '', 'oversized'), 'unknown'),
                 ((None, '', 'inaccessible'), 'inaccessible'), ((None, '', 'unavailable'), 'unavailable'),
                 ((1, 'Failed to connect to bus', None), 'unknown'),
                 ((1, 'Access denied SECRET', None), 'inaccessible'),
                 ((1, 'Operation not permitted', None), 'inaccessible'),
                 ((0, 'LoadState=not-found\nActiveState=inactive\nSubState=dead\n', None), 'unavailable'),
                 ((0, 'LoadState=loaded\nActiveState=inactive\n', None), 'unknown'),
                 ((0, 'LoadState=loaded\nActiveState=SECRET\nSubState=dead\n', None), 'unknown'),
                 ((0, 'LoadState=loaded\nActiveState=failed\nSubState=failed\n', None), 'failed'),
                 ((0, 'LoadState=loaded\nActiveState=activating\nSubState=start\n', None), 'transitional')]
        for result, expected in cases:
            with self.subTest(result=result), patch.object(menu, '_query', return_value=result):
                self.assertEqual(menu.service_status('systemd', 'v-ui.service', '/usr/bin/systemctl')[0], expected)

    def test_openrc_exact_status_and_returncode_pairs(self):
        cases = [(0, ' * status: started\n', 'active'), (3, ' * status: stopped\n', 'inactive'),
                 (32, ' * status: crashed\n', 'failed'), (3, ' * status: crashed\n', 'unknown'),
                 (0, ' * status: crashed\n', 'unknown'),
                 (8, ' * status: starting\n', 'transitional'), (4, ' * status: stopping\n', 'transitional'),
                 (16, ' * status: inactive\n', 'inactive'), (0, ' * status: stopped\n', 'unknown'),
                 (1, ' * status: stopped\n', 'unknown'), (1, 'service not found', 'unknown'),
                 (1, 'permission denied', 'inaccessible')]
        for code, output, expected in cases:
            with self.subTest(code=code, output=output), patch.object(menu, '_query', return_value=(code, output, None)) as query:
                self.assertEqual(menu.service_status('openrc', 'v-ui', '/sbin/rc-service')[0], expected)
                query.assert_called_once_with(['/sbin/rc-service', '--nocolor', 'v-ui', 'status'])

    def test_invalid_units_and_missing_binary_do_not_execute(self):
        with patch.object(menu, '_query') as query:
            self.assertEqual(menu.service_status('systemd', 'ssh.service', '/bin/systemctl')[0], 'unknown')
            self.assertEqual(menu.service_status('bad', 'v-ui', '/bad')[0], 'unknown')
            query.assert_not_called()
        with self.managed(self.config()), patch.object(menu, '_trusted_binary', return_value=None), \
                patch.object(menu, '_query') as query:
            text = io.StringIO()
            menu.show_services(self.root, text)
            self.assertIn('unavailable', text.getvalue())
            query.assert_not_called()

    def test_systemd_contradictory_states_are_unknown(self):
        for unit, state, detail in (('v-ui.service', 'inactive', 'running'),
                                     ('v-ui.service', 'active', 'dead'),
                                     ('v-ui.service', 'active', 'listening'),
                                     ('v-ui-http01.socket', 'active', 'exited'),
                                     ('v-ui.service', 'failed', 'running'),
                                     ('v-ui.service', 'activating', 'dead')):
            response = f'LoadState=loaded\nActiveState={state}\nSubState={detail}\n'
            with self.subTest(unit=unit, state=state, detail=detail), \
                    patch.object(menu, '_query', return_value=(0, response, None)):
                self.assertEqual(menu.service_status('systemd', unit, '/usr/bin/systemctl'), ('unknown', ''))

    def test_trusted_binary_symlink_loop_is_unavailable(self):
        with patch.object(menu.Path, 'resolve', side_effect=RuntimeError('Symlink loop')):
            self.assertIsNone(menu._trusted_binary('systemd'))

    def test_query_real_fake_child_has_bounded_output_time_and_clean_env(self):
        children = []
        popen = subprocess.Popen
        def capture(*args, **kwargs):
            child = popen(*args, **kwargs)
            children.append(child)
            return child
        with patch.object(menu.subprocess, 'Popen', side_effect=capture), \
                patch.dict(os.environ, {'SYSTEMD_HOST': 'remote-secret', 'LD_PRELOAD': '/not-real'}):
            result = menu._query([sys.executable, '-B', '-c',
                'import os,json;print(json.dumps(dict(os.environ)))'])
            self.assertEqual(result[0], 0)
            env = json.loads(result[1])
            self.assertNotIn('SYSTEMD_HOST', env)
            self.assertNotIn('LD_PRELOAD', env)
            self.assertEqual(env['LC_ALL'], 'C')
            result = menu._query([sys.executable, '-B', '-c', 'print("x"*20000)'])
            self.assertEqual(result[2], 'oversized')
            with patch.object(menu, 'QUERY_TIMEOUT', .1):
                result = menu._query([sys.executable, '-B', '-c', 'import time;time.sleep(10)'])
            self.assertEqual(result[2], 'timeout')
        self.assertTrue(all(child.poll() is not None and child.stdout.closed for child in children))

    def test_successful_query_does_not_signal_reaped_process_group(self):
        with patch.object(menu.os, 'killpg') as kill:
            result = menu._query([sys.executable, '-B', '-c', 'print("fixture")'])
        self.assertEqual(result[0], 0)
        kill.assert_not_called()

    def test_actual_installed_entrypoint_pty_smoke(self):
        result = installed_menu_smoke(Path(sys.executable), self.payload, self.root)
        self.assertIn('installed-PTY', result['menu'])

    def test_ctrl_c_during_fake_hanging_query_kills_and_reaps_child(self):
        pidfile = self.root / 'fake-query.pid'
        child = ('import os,time;from pathlib import Path;'
                 f'Path({str(pidfile)!r}).write_text(str(os.getpid()));time.sleep(30)')
        hook = ('import runpy,sys;from app import ops_menu as m;'
                'm.QUERY_TIMEOUT=30;m.managed_metadata=lambda root:{"service_manager":"systemd"};'
                'm._trusted_binary=lambda manager:sys.executable;'
                f'm.service_status=lambda *args:m._query([sys.executable,"-B","-c",{child!r}]);'
                'sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name="__main__")')
        command = [sys.executable, '-B', '-c', hook, str(self.payload / 'scripts/deploy.py'),
                   '--root', str(self.root), 'menu']
        env = {'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(self.payload), 'PYTHONDONTWRITEBYTECODE': '1'}
        with MenuSession(command, cwd=self.payload, env=env) as session:
            session.expect()
            session.send('1\n')
            deadline = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(pidfile.exists(), session.text())
            pid = int(pidfile.read_text())
            session.send(b'\x03')
            text = session.finish(130)
        self.assertIn('已取消', text)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_installer_minimal_controller_does_not_import_menu(self):
        controller = self.root / 'installer-controller'
        for name in ('scripts/deploy.py', 'app/release_tools.py'):
            path = controller / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE / name, path)
        self.assertFalse((controller / 'app/ops_menu.py').exists())
        result = subprocess.run([sys.executable, '-B', str(controller / 'scripts/deploy.py'),
            '--root', str(self.root / 'absent'), 'stage', '--help'], cwd=controller,
            env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'},
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'absent').exists())


if __name__ == '__main__':
    unittest.main()
