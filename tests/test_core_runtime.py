import json
import os
from pathlib import Path
import subprocess
import sys
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app.services.core_runtime import CoreRuntime, CoreError, encode, atomic_write, revision

FAKE = '''#!/usr/bin/python3
import json,sys,time,socket
if sys.argv[1] == 'version': print('sing-box version 1.14.2'); sys.exit(0)
c = json.load(open(sys.argv[-1]))
if sys.argv[1] == 'check': sys.exit(3 if c.get('invalid') else 0)
if c.get('crash'): sys.exit(4)
s=socket.socket();s.bind(('127.0.0.1', c['inbounds'][0]['listen_port']));s.listen()
time.sleep(90)
'''


class SafeCoreRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='vui-runtime-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.binary = self.root / 'sing-box'
        self.binary.write_text(FAKE)
        self.binary.chmod(0o700)
        self.runtime = CoreRuntime('sing-box', self.binary, self.root / 'runtime')
        self.addCleanup(self.runtime.close)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        self.config = {'inbounds': [{'type': 'fake', 'listen_port': port}], 'value': 1}

    def test_invalid_candidate_preserves_state_and_pid(self):
        self.runtime.apply(self.config)
        old = self.runtime.state_file.read_bytes()
        pid = self.runtime.process.pid
        with self.assertRaises(CoreError): self.runtime.apply({**self.config, 'invalid': True})
        self.assertEqual(self.runtime.state_file.read_bytes(), old)
        self.assertEqual(self.runtime.process.pid, pid)
        self.assertIsNone(self.runtime.process.poll())

    def test_failed_start_rolls_back_to_last_known_good(self):
        self.runtime.apply(self.config)
        old = self.runtime.state_file.read_bytes()
        with self.assertRaises(CoreError): self.runtime.apply({**self.config, 'crash': True})
        self.assertEqual(self.runtime.state_file.read_bytes(), old)
        self.assertTrue(self.runtime.status()['running'])

    def test_validate_only_does_not_replace_runtime(self):
        self.runtime.apply(self.config)
        pid = self.runtime.process.pid
        value = self.runtime.apply({**self.config, 'value': 2}, activate=False)
        self.assertFalse(value['applied'])
        self.assertEqual(pid, self.runtime.process.pid)
        self.assertEqual(self.runtime.state()['revision'], revision(self.config))

    def test_committed_state_recovers_but_staging_does_not(self):
        self.runtime.apply(self.config)
        self.runtime.close()
        atomic_write(self.runtime.root / (revision({'invalid': True}) + '.json'), encode({'invalid': True}))
        other = CoreRuntime('sing-box', self.binary, self.runtime.root)
        self.addCleanup(other.close)
        other.recover()
        self.assertTrue(other.status()['running'])
        self.assertEqual(other.state()['revision'], revision(self.config))

    def test_manual_stop_survives_restart(self):
        self.runtime.apply(self.config)
        self.runtime.stop()
        self.runtime.close()
        self.runtime.recover()
        self.assertFalse(self.runtime.status()['running'])
        self.assertFalse(self.runtime.state()['enabled'])

    def test_mismatched_version_cannot_apply(self):
        self.binary.write_text(FAKE.replace('1.14.2', '1.99.0'))
        with self.assertRaisesRegex(CoreError, 'version'): self.runtime.apply(self.config)
        self.assertFalse(self.runtime.state_file.exists())

    def test_second_panel_cannot_take_runtime_ownership(self):
        self.runtime.apply(self.config)
        other = CoreRuntime('sing-box', self.binary, self.runtime.root)
        self.addCleanup(other.close)
        with self.assertRaisesRegex(CoreError, 'owns'): other.apply(self.config)

    def test_tampered_revision_is_rejected_on_recovery(self):
        self.runtime.apply(self.config)
        path = self.runtime.config_path()
        self.runtime.close()
        path.write_text('{}')
        with self.assertRaises(CoreError): self.runtime.recover()
        self.assertFalse(self.runtime.status()['running'])

    def test_parallel_apply_is_serialized(self):
        errors = []
        def apply(i):
            try: self.runtime.apply({**self.config, 'value': i})
            except Exception as exc: errors.append(exc)
        threads = [threading.Thread(target=apply, args=(i,)) for i in range(3)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errors, [])
        state = self.runtime.state()
        self.assertEqual(state['revision'], revision(json.loads(self.runtime.config_path().read_text())))
        self.assertTrue(self.runtime.status()['running'])

    def test_commit_failure_restores_state_even_after_pointer_replace(self):
        self.runtime.apply(self.config)
        old = self.runtime.state_file.read_bytes()
        real_write = self.runtime._write
        def fail_once(path, content):
            real_write(path, content)
            if path == self.runtime.state_file and content != old:
                raise OSError('simulated directory fsync error')
        with patch.object(self.runtime, '_write', side_effect=fail_once):
            with self.assertRaises(CoreError): self.runtime.apply({**self.config, 'value': 2})
        self.assertEqual(old, self.runtime.state_file.read_bytes())
        self.assertTrue(self.runtime.status()['running'])

    def test_panel_process_death_terminates_watchdog_child(self):
        import psutil
        script = """import json,sys,time
from pathlib import Path
from app.services.core_runtime import CoreRuntime
r=CoreRuntime('sing-box',Path(sys.argv[1]),Path(sys.argv[2]))
r.apply(json.loads(sys.argv[3]))
Path(sys.argv[4]).write_text(str(r.process.pid))
time.sleep(60)
"""
        pidfile = self.root / 'pid'
        parent = subprocess.Popen([sys.executable, '-c', script, str(self.binary),
            str(self.root / 'orphan'), json.dumps(self.config), str(pidfile)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 10
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(pidfile.exists())
            watchdog = psutil.Process(int(pidfile.read_text()))
            children = watchdog.children()
            self.assertTrue(children)
            parent.kill(); parent.wait()
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                if all(not c.is_running() or c.status() == psutil.STATUS_ZOMBIE for c in children):
                    break
                time.sleep(0.05)
            self.assertTrue(all(not c.is_running() or c.status() == psutil.STATUS_ZOMBIE for c in children))
        finally:
            if parent.poll() is None: parent.kill(); parent.wait()

    def test_verified_fd_survives_watchdog_spawn_and_entire_core_lifetime(self):
        self.binary.write_text(FAKE.replace('time.sleep(90)', """if c.get('reread'):
 conn,_=s.accept()
 conn.sendall(open(sys.argv[-1], 'rb').read())
 conn.close()
time.sleep(90)"""))
        config = {**self.config, 'reread': True}
        self.runtime.apply(config)
        fd = self.runtime._config_fd
        self.assertIsNotNone(fd)
        self.assertEqual(Path(f'/proc/{os.getpid()}/fd/{fd}').read_bytes(), encode(config))
        with socket.create_connection(('127.0.0.1', config['inbounds'][0]['listen_port']), timeout=3) as conn:
            self.assertEqual(conn.recv(65536), encode(config))
        self.runtime.stop()
        self.assertIsNone(self.runtime._config_fd)
        with self.assertRaises(OSError): os.fstat(fd)

    def test_check_uses_verified_fd_and_discards_all_config_output(self):
        self.runtime._claim()
        path = self.runtime.root / (revision(self.config) + '.json')
        atomic_write(path, encode(self.config))
        original = subprocess.run
        checks = []
        def run(command, **kwargs):
            if command[1] == 'check':
                checks.append(command[-1])
                self.assertTrue(command[-1].startswith(f'/proc/{os.getpid()}/fd/'))
                self.assertEqual(Path(command[-1]).read_bytes(), encode(self.config))
                self.assertEqual(kwargs['stdout'], subprocess.DEVNULL)
                self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
            return original(command, **kwargs)
        with patch('app.services.core_runtime.subprocess.run', side_effect=run):
            self.runtime.check(path)
        self.assertEqual(len(checks), 1)
        self.assertFalse(Path(checks[0]).exists())

    def test_recovery_after_child_exit_releases_the_old_config_descriptor(self):
        self.runtime.apply(self.config)
        old_pid = self.runtime.process.pid
        self.runtime.process.terminate()
        self.runtime.process.wait(timeout=3)
        self.runtime.recover()
        self.assertNotEqual(self.runtime.process.pid, old_pid)
        self.assertTrue(self.runtime.status()['running'])
        self.assertIsNotNone(self.runtime._config_fd)

    def test_unsafe_state_cannot_prevent_stop_or_leave_recovery_running(self):
        for operation in ('stop', 'recover'):
            with self.subTest(operation=operation):
                self.runtime.apply(self.config)
                self.runtime.state_file.chmod(0o644)
                with self.assertRaises(CoreError): getattr(self.runtime, operation)()
                self.assertIsNone(self.runtime.process)
                self.assertIsNone(self.runtime._config_fd)
                # Test cleanup is offline; production never repairs permissions.
                self.runtime.state_file.chmod(0o600)

    def test_xray_fd_check_and_launch_explicitly_select_json_format(self):
        self.runtime.name = 'xray'
        fake = FAKE.replace('sing-box version 1.14.2', 'Xray 26.3.27')
        fake = fake.replace("c = json.load(open(sys.argv[-1]))", """if '-format=json' not in sys.argv: sys.exit(9)
c = json.load(open(sys.argv[-1]))""")
        fake = fake.replace("if sys.argv[1] == 'check':", "if '-test' in sys.argv:")
        self.binary.write_text(fake)
        self.runtime.apply(self.config)
        self.assertTrue(self.runtime.status()['running'])
        self.assertIsNotNone(self.runtime._config_fd)

    def test_new_files_are_owner_only(self):
        self.runtime.apply(self.config)
        for path in (self.runtime.state_file, self.runtime.config_path()):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__': unittest.main()
