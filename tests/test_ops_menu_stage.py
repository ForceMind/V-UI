"""Candidate-only menu: actual authored archives, installed PTYs and process lifetime.

Synthetic runtimes below are locally authored health fixtures, NOT release artifacts.
Real current-built runtime/wheels are exercised separately by the package gates.
"""
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import time
import unittest
from unittest.mock import patch

from app import ops_menu as menu, release_tools as tools
from scripts.ops_menu_smoke import MenuSession, tree_snapshot
import test_ops_menu as readonly


class StageMenuTests(unittest.TestCase):
    write = readonly.MenuTests.write
    run_menu = readonly.MenuTests.run_menu

    def setUp(self):
        readonly.MenuTests.setUp(self)
        self.stack.enter_context(patch.object(menu, 'STAGE_TERM_GRACE', .05))
        self.stack.enter_context(patch.object(menu, 'STAGE_REAP_TIMEOUT', 1))
        self.stack.enter_context(patch.object(menu, 'STAGE_TIMEOUT', 5))
        self.data = self.root / 'data'
        self.data.mkdir(mode=0o700)
        (self.data / 'fake-record').write_bytes(b'synthetic existing data\n')

    def candidate(self, identity='menu-candidate', *, behavior='healthy', target=None):
        source = self.root / ('source-' + identity)
        source.mkdir(mode=0o700)
        runtime = source / 'runtimes'
        runtime.mkdir(mode=0o700)
        key = tools.target_key()
        # A tiny Python executable accepts only stage's known runtime commands.
        # Its health endpoint is real loopback HTTP, with the required 401.
        program = (f'#!{sys.executable}\n' + '''import http.server, os, sys, time
if sys.argv[1:3] in (['-m','ensurepip'], ['-m','pip']):
    raise SystemExit(0)
if sys.argv[1:3] != ['-B','-m'] or sys.argv[3] != 'uvicorn':
    raise SystemExit(19)
BEHAVIOR
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == '/login' else 401)
        self.end_headers()
        self.wfile.write(b'auth-fixture')
    def log_message(self, *args): pass
server = http.server.HTTPServer(('127.0.0.1',0), Handler)
print('Uvicorn running on http://127.0.0.1:' + str(server.server_port), flush=True)
server.serve_forever()
'''.replace('BEHAVIOR', {
            'healthy': 'time.sleep(.2)',
            'broken': 'print("CANDIDATE-SECRET\\x1b[2J",flush=True);raise SystemExit(23)',
            'hang': 'time.sleep(60)',
        }[behavior]))
        blob = program.encode()
        archive = runtime / (key + '.tar.gz')
        with tarfile.open(archive, 'w:gz') as tar:
            info = tarfile.TarInfo('python/bin/python3')
            info.mode = 0o700
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
        (source / ('requirements.' + key + '.lock')).write_text('')
        metadata = {'kind': 'release', 'platform': tools.PLATFORM, 'release_id': identity,
                    'targets': [target or key], 'portable_runtime_pins': {
                        key: {'sha256': tools.digest(archive.read_bytes())}}}
        bundle = self.root / (identity + '.zip')
        checksum = tools.create_archive(source, bundle, metadata)
        return bundle, checksum

    def answers(self, bundle, checksum, end='0\n'):
        return f'4\n{bundle}\n{checksum}\nyes\n' + end

    def protected(self):
        return ((self.root / 'CURRENT.json').read_bytes(), tree_snapshot(self.release),
                tree_snapshot(self.data), (self.data / 'fake-record').read_bytes())

    def test_actual_stage_success_bad_hash_duplicate_and_operation_lease(self):
        bundle, checksum = self.candidate()
        frozen = self.protected()
        # A running panel owns .panel.lease, while stage only needs .operation.lock.
        with tools.lease(self.root / '.panel.lease'):
            code, text = self.run_menu(self.answers(bundle, '0' * 64))
            self.assertEqual(code, 0)
            self.assertIn('准备失败或结果未确认', text)
            self.assertTrue((self.root / '.operation.lock').exists())
            self.assertEqual(self.protected(), frozen)
            with tools.lease(self.root / '.operation.lock'):
                code, text = self.run_menu(self.answers(bundle, checksum))
                self.assertIn('准备失败或结果未确认', text)
                self.assertFalse((self.root / 'releases/menu-candidate').exists())
            code, text = self.run_menu(self.answers(bundle, checksum))
            self.assertEqual(code, 0, text)
            self.assertIn('候选已准备，尚未升级', text)
            ready = self.root / 'releases/menu-candidate/READY.json'
            self.assertTrue(ready.exists())
            candidate_before = tree_snapshot(ready.parent)
            code, text = self.run_menu(self.answers(bundle, checksum))
            self.assertIn('准备失败或结果未确认', text)
            self.assertEqual(tree_snapshot(ready.parent), candidate_before)
        self.assertEqual(self.protected(), frozen)
        self.assertNotIn(str(bundle), text)

    def test_actual_cross_target_and_broken_health_preserve_old_install(self):
        other = next(target for target in menu.TARGETS if target != tools.target_key())
        for identity, options in (('wrong-target', {'target': other}), ('broken', {'behavior': 'broken'})):
            with self.subTest(identity=identity):
                bundle, checksum = self.candidate(identity, **options)
                frozen = self.protected()
                code, text = self.run_menu(self.answers(bundle, checksum))
                self.assertEqual(code, 0)
                self.assertIn('准备失败或结果未确认', text)
                self.assertNotIn('候选已准备', text)
                self.assertNotIn('CANDIDATE-SECRET', text)
                self.assertEqual(self.protected(), frozen)
                self.assertFalse((self.root / 'releases' / identity).exists())

    def test_timeout_ready_marker_is_not_success_and_partial_is_not_deleted(self):
        bundle, checksum = self.candidate('hanging', behavior='hang')
        frozen = self.protected()
        with patch.object(menu, 'STAGE_TIMEOUT', .8):
            code, text = self.run_menu(self.answers(bundle, checksum))
        self.assertEqual(code, 0)
        self.assertIn('准备失败或结果未确认', text)
        self.assertNotIn('候选已准备', text)
        self.assertIn('总处理时限已到', text)
        self.assertTrue((self.root / 'releases/hanging/READY.json').exists())
        self.assertEqual(self.protected(), frozen)

    def test_blank_decline_eof_and_overlong_prompts_never_start_or_write(self):
        bundle, checksum = self.candidate()
        for answers in ('4\n\n0\n', f'4\n{bundle}\n\n0\n',
                        f'4\n{bundle}\n{checksum}\nno\n0\n', '4\n',
                        f'4\n{bundle}\n', f'4\n{bundle}\n{checksum}\n',
                        '4\n' + 'x' * 4097 + 'yes\n',
                        f'4\n{bundle}\n' + 'a' * 66 + 'yes\n',
                        f'4\n{bundle}\n{checksum}\n' + 'yes' * 6 + '\n'):
            with self.subTest(answers=answers[:20]):
                frozen = tree_snapshot(self.root)
                with patch.object(menu, '_stage_worker') as worker:
                    code, text = self.run_menu(answers)
                worker.assert_not_called()
                self.assertIn(code, (0, 2))
                self.assertEqual(tree_snapshot(self.root), frozen)
                self.assertNotIn(str(bundle), text)

    def test_revalidation_symlink_fifo_control_path_and_root_mode_refused(self):
        bundle, checksum = self.candidate()
        link = self.root / 'linked.zip'
        link.symlink_to(bundle)
        fifo = self.root / 'fifo.zip'
        os.mkfifo(fifo, 0o600)
        for path in (link, fifo, self.root / '$NOT_EXPANDED', self.root / 'no\x1bfile'):
            with self.subTest(path=path), patch.object(menu, '_stage_worker') as worker:
                frozen = tree_snapshot(self.root)
                code, text = self.run_menu(self.answers(path, checksum))
                self.assertIn('准备拒绝', text)
                worker.assert_not_called()
                self.assertEqual(tree_snapshot(self.root), frozen)
        boundary = menu._stage_boundary(self.root)
        with patch.object(menu, '_stage_boundary', side_effect=[boundary, (0, 0, b'changed')]), \
                patch.object(menu, '_stage_worker') as worker:
            self.assertIn('准备拒绝', self.run_menu(self.answers(bundle, checksum))[1])
        worker.assert_not_called()

    def test_current_bytes_change_and_later_ctrl_c_do_not_claim_preservation(self):
        bundle, checksum = self.candidate()
        def changed(*args):
            path = self.root / 'CURRENT.json'
            path.write_bytes(path.read_bytes() + b'\n')
            return 'prepared'
        with patch.object(menu, '_stage_worker', side_effect=changed):
            code, text = self.run_menu(self.answers(bundle, checksum))
        self.assertIn('候选已准备，但活动选择保留未确认', text)
        self.assertNotIn('活动选择未变', text)
        with patch.object(menu, '_stage_worker', return_value='prepared'), \
                patch.object(menu, 'show_metadata', side_effect=KeyboardInterrupt):
            code, text = self.run_menu(self.answers(bundle, checksum, '2\n'))
        self.assertEqual(code, 130)
        self.assertNotIn('未修改安装', text)

    def pty(self, *, deadline=5, launch_hook=''):
        # In-process test instrumentation only; installed source imports no helper.
        hook = ('import runpy,sys;from app import ops_menu as m;'
                f'm.STAGE_TIMEOUT={deadline!r};m.STAGE_TERM_GRACE=.1;m.STAGE_REAP_TIMEOUT=1;'
                + launch_hook + 'sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name="__main__")')
        command = [sys.executable, '-B', '-c', hook, str(self.payload / 'scripts/deploy.py'),
                   '--root', str(self.root), 'menu']
        return MenuSession(command, cwd=self.payload, env={
            'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(self.payload), 'PYTHONDONTWRITEBYTECODE': '1'})

    def pty_confirm(self, session, bundle, checksum):
        session.expect()
        session.send('4\n')
        session.expect('本地升级包路径（留空取消）: ')
        session.send(str(bundle) + '\n')
        session.expect('可信 SHA-256（留空取消）: ')
        session.send(checksum + '\n')
        session.expect('确认仅准备候选 [yes/N]: ')
        session.send('yes\n')
        session.expect('正在准备候选；')

    def test_actual_pty_eof_and_ctrl_c_at_each_preconfirmation_prompt(self):
        bundle, checksum = self.candidate()
        for index in range(3):
            for key, code in ((b'\x04', 0), (b'\x03', 130)):
                with self.subTest(index=index, key=key):
                    frozen = tree_snapshot(self.root)
                    with self.pty() as session:
                        session.expect()
                        session.send('4\n')
                        session.expect('本地升级包路径（留空取消）: ')
                        if index >= 1:
                            session.send(str(bundle) + '\n')
                            session.expect('可信 SHA-256（留空取消）: ')
                        if index >= 2:
                            session.send(checksum + '\n')
                            session.expect('确认仅准备候选 [yes/N]: ')
                        session.send(key)
                        text = session.finish(code)
                    self.assertNotIn(str(bundle), text)
                    self.assertEqual(tree_snapshot(self.root), frozen)

    def test_actual_pty_postconfirmation_eof_repeated_ctrl_c_hup_term(self):
        for mode in ('eof', 'interrupt', 'hup', 'term'):
            with self.subTest(mode=mode):
                bundle, checksum = self.candidate('post-' + mode)
                frozen = self.protected()
                with self.pty() as session:
                    self.pty_confirm(session, bundle, checksum)
                    if mode == 'eof':
                        session.send(b'\x04')
                        code = 0
                    elif mode == 'interrupt':
                        session.send(b'\x03\x03')
                        time.sleep(.05)
                        session.send(b'\x03')
                        code = 130
                    else:
                        number = signal.SIGHUP if mode == 'hup' else signal.SIGTERM
                        os.kill(session.process.pid, number)
                        os.kill(session.process.pid, number)
                        code = 128 + number
                    text = session.finish(code)
                self.assertIn('候选已准备，尚未升级', text)
                self.assertNotIn('未修改安装', text)
                self.assertNotIn(str(bundle), text)
                self.assertEqual(self.protected(), frozen)

    def test_worker_argv_devnull_deadline_abnormal_and_retained_identity(self):
        processes = []
        popen = subprocess.Popen
        signals = []
        real_killpg = os.killpg
        def launch(argv, **kwargs):
            self.assertEqual(argv[:2], [sys.executable, '-B'])
            self.assertEqual(argv[3:], ['--root', str(self.root), 'stage', '/literal;$path.zip',
                                        '--sha256', 'a' * 64])
            self.assertTrue(kwargs['start_new_session'])
            for stream in ('stdin', 'stdout', 'stderr'):
                self.assertEqual(kwargs[stream], subprocess.DEVNULL)
            child = popen([sys.executable, '-c', 'raise SystemExit(17)'], **kwargs)
            processes.append(child)
            return child
        def kill(pid, number):
            child = processes[-1]
            self.assertIsNone(child.returncode, 'Leader reaped before final group signal')
            # WNOWAIT still sees the identical exited direct child.
            self.assertEqual(os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT).si_pid, pid)
            signals.append(number)
            return real_killpg(pid, number)
        with patch.object(menu.subprocess, 'Popen', side_effect=launch), patch.object(menu.os, 'killpg', side_effect=kill):
            self.assertEqual(menu._stage_worker(self.root, Path('/literal;$path.zip'), 'a' * 64), 'unconfirmed')
        self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertEqual(processes[0].returncode, 17)
        with self.assertRaises(ChildProcessError):
            os.waitpid(processes[0].pid, os.WNOHANG)

    def test_descendant_holding_stdout_does_not_hold_menu_and_is_terminated(self):
        pidfile = self.root / 'descendant.pid'
        code = ('import os,signal,time;from pathlib import Path;pid=os.fork();'
                f'Path({str(pidfile)!r}).write_text(str(pid)) if pid else None;'
                'os._exit(0) if pid else None;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)')
        popen = subprocess.Popen
        children = []
        def launch(argv, **kwargs):
            child = popen([sys.executable, '-c', code], **kwargs)
            children.append(child)
            return child
        started = time.monotonic()
        with patch.object(menu.subprocess, 'Popen', side_effect=launch):
            self.assertEqual(menu._stage_worker(self.root, Path('/not-opened-by-stub'), 'a' * 64), 'prepared')
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(children[0].returncode, 0)
        descendant = int(pidfile.read_text())
        # We reap only our direct child; an orphan may remain an init-owned zombie.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                raw = Path(f'/proc/{descendant}/stat').read_text()
                if raw.rsplit(')', 1)[1].split()[0] == 'Z':
                    break
            except (FileNotFoundError, ProcessLookupError):
                break
            time.sleep(.02)
        else:
            self.fail('Cooperative descendant remained running after group cleanup')

    def test_spawn_masks_interrupts_restores_exact_parent_mask_and_keeps_term(self):
        marker = self.root / 'child-mask.json'
        popen = subprocess.Popen
        initial = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
        expected = initial | {signal.SIGUSR1}
        try:
            def launch(argv, **kwargs):
                code = ('import json,signal;from pathlib import Path;'
                        f'Path({str(marker)!r}).write_text(json.dumps(sorted(int(x) for x in '
                        'signal.pthread_sigmask(signal.SIG_BLOCK,set()))))')
                return popen([sys.executable, '-c', code], **kwargs)
            with patch.object(menu.subprocess, 'Popen', side_effect=launch):
                self.assertEqual(menu._stage_worker(self.root, Path('/fixture'), 'a' * 64), 'prepared')
            mask = set(json.loads(marker.read_text()))
            self.assertTrue({signal.SIGINT, signal.SIGHUP, signal.SIGUSR1} <= mask)
            self.assertNotIn(signal.SIGTERM, mask)
            self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), expected)
            with patch.object(menu.subprocess, 'Popen', side_effect=OSError('synthetic launch refusal')):
                self.assertEqual(menu._stage_worker(self.root, Path('/fixture'), 'a' * 64), 'unconfirmed')
            self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), expected)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, initial)

    def test_deadline_term_reaches_child_despite_interrupt_mask(self):
        marker = self.root / 'term-received'
        popen = subprocess.Popen
        children = []
        code = ('import signal,time;from pathlib import Path;'
                f'signal.signal(signal.SIGTERM,lambda *args:(Path({str(marker)!r}).write_text("TERM"),exit(0)));'
                'time.sleep(60)')
        def launch(argv, **kwargs):
            child = popen([sys.executable, '-c', code], **kwargs)
            children.append(child)
            return child
        with patch.object(menu.subprocess, 'Popen', side_effect=launch), \
                patch.object(menu, 'STAGE_TIMEOUT', .3), patch.object(menu, 'STAGE_TERM_GRACE', .2):
            self.assertEqual(menu._stage_worker(self.root, Path('/fixture'), 'a' * 64), 'timeout')
        self.assertEqual(marker.read_text(), 'TERM')
        self.assertEqual(children[0].returncode, 0)

    def test_nondefault_sigchld_and_reaped_leader_never_signal_reused_group(self):
        for disposition in (signal.SIG_IGN, lambda *args: None):
            with patch.object(menu.signal, 'getsignal', return_value=disposition), \
                    patch.object(menu.subprocess, 'Popen') as launch:
                self.assertEqual(menu._stage_worker(self.root, Path('/fixture'), 'a' * 64), 'unconfirmed')
                launch.assert_not_called()
        popen = subprocess.Popen
        children = []
        def externally_reaped(argv, **kwargs):
            child = popen([sys.executable, '-c', 'pass'], **kwargs)
            os.waitpid(child.pid, 0)
            children.append(child)
            return child
        with patch.object(menu.subprocess, 'Popen', side_effect=externally_reaped), \
                patch.object(menu.os, 'killpg') as kill:
            self.assertEqual(menu._stage_worker(self.root, Path('/fixture'), 'a' * 64), 'unconfirmed')
        kill.assert_not_called()
        self.assertEqual(len(children), 1)
        # Test harness records that its deliberate external reap is complete.
        children[0].wait(timeout=0)

    def test_echo_restored_when_interrupted_during_disable(self):
        stdin = readonly.Terminal('unused')
        original = [0, 0, 0, menu.termios.ECHO, 0, 0, []]
        with patch.object(stdin, 'fileno', return_value=17), \
                patch.object(menu.sys, 'stdin', stdin), \
                patch.object(menu.termios, 'tcgetattr', return_value=original), \
                patch.object(menu.termios, 'tcsetattr', side_effect=[KeyboardInterrupt, None]) as change, \
                patch.object(menu, 'print', create=True):
            with self.assertRaises(KeyboardInterrupt):
                menu._stage_input('prompt', 20, hidden=True)
        self.assertEqual(change.call_count, 2)
        self.assertEqual(change.call_args.args[2], original)

    def test_signals_at_launch_and_result_report_are_deferred(self):
        import builtins
        popen = subprocess.Popen
        for edge in ('launch', 'report'):
            with self.subTest(edge=edge):
                bundle, checksum = self.candidate('edge-' + edge)
                previous = {number: signal.getsignal(number)
                            for number in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM)}
                def launch(*args, **kwargs):
                    child = popen(*args, **kwargs)
                    if edge == 'launch':
                        os.kill(os.getpid(), signal.SIGINT)
                    return child
                def report(*args, **kwargs):
                    if edge == 'report' and args and '候选已准备，尚未升级' in str(args[0]):
                        os.kill(os.getpid(), signal.SIGTERM)
                    return builtins.print(*args, **kwargs)
                with patch.object(menu.subprocess, 'Popen', side_effect=launch), \
                        patch.object(menu, 'print', side_effect=report, create=True):
                    code, text = self.run_menu(self.answers(bundle, checksum))
                self.assertEqual(code, 130 if edge == 'launch' else 143)
                self.assertIn('候选已准备，尚未升级', text)
                self.assertNotIn('未修改安装', text)
                self.assertEqual(previous, {number: signal.getsignal(number) for number in previous})

    def test_launch_and_cleanup_failure_are_unconfirmed_without_raw_errors(self):
        bundle, checksum = self.candidate()
        with patch.object(menu.subprocess, 'Popen', side_effect=OSError('RAW-CANDIDATE-PATH')):
            code, text = self.run_menu(self.answers(bundle, checksum))
        self.assertIn('准备失败或结果未确认', text)
        self.assertNotIn('RAW-CANDIDATE-PATH', text)
        real_killpg = os.killpg
        def failed_signal(pid, number):
            real_killpg(pid, number)
            raise PermissionError('secret')
        with patch.object(menu.os, 'killpg', side_effect=failed_signal):
            self.assertIn('准备失败或结果未确认', self.run_menu(self.answers(bundle, checksum))[1])


if __name__ == '__main__':
    unittest.main()
