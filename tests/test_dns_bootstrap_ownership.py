"""Stopped DNS bootstrap keeps real flock exclusion across child processes."""
from contextlib import contextmanager
import os
from pathlib import Path
import select
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.data_owner import (DataOwnerError, data_owner, exclusive_owner_descriptor,
                            inherited_exclusive_owner)
from app.certificates.dns_certbot import fixed_bootstrap_hooks
from app.certificates.provider import CertbotProvider


class DNSBootstrapOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.data = self.root / 'data'
        self.data.mkdir(mode=0o700)

    def test_live_shared_owner_cannot_export_or_enter_stopped_bootstrap(self):
        from app.release_tools import writer_operation, ReleaseError
        with data_owner(self.data), exclusive_owner_descriptor(self.data) as descriptor:
            self.assertIsNone(descriptor)
            with self.assertRaises((ReleaseError, DataOwnerError)):
                with writer_operation(self.root):
                    self.fail('source panel must exclude stopped bootstrap')

    def test_child_keeps_exclusive_flock_after_parent_scope_closes(self):
        code = '''
import sys
from pathlib import Path
from app.data_owner import inherited_exclusive_owner, data_owner
with inherited_exclusive_owner(Path(sys.argv[1]), int(sys.argv[2])):
    with data_owner(Path(sys.argv[1])):
        print('ready', flush=True)
        sys.stdin.readline()
'''
        with data_owner(self.data, exclusive=True):
            with exclusive_owner_descriptor(self.data) as descriptor:
                child = subprocess.Popen([sys.executable, '-c', code, str(self.data), str(descriptor)],
                    pass_fds=(descriptor,), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True)
                try:
                    expected = b'ready\n'; ready = bytearray()
                    deadline = time.monotonic() + 5
                    while not ready.endswith(b'\n') and len(ready) <= len(expected):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not select.select([child.stdout], [], [], remaining)[0]:
                            self.fail('synthetic lease child readiness deadline exceeded')
                        chunk = os.read(child.stdout.fileno(), len(expected) + 1 - len(ready))
                        if not chunk: break
                        ready.extend(chunk)
                    self.assertEqual(bytes(ready), expected)
                except BaseException:
                    child.kill(); child.communicate(timeout=5); raise
        try:
            for exclusive in (False, True):
                with self.subTest(exclusive=exclusive), self.assertRaises(DataOwnerError):
                    with data_owner(self.data, exclusive=exclusive):
                        self.fail('child must still retain the stopped writer lease')
            output, errors = child.communicate('\n', timeout=5)
            self.assertEqual(child.returncode, 0, errors)
        finally:
            if child.poll() is None:
                child.kill(); child.communicate(timeout=5)
        with data_owner(self.data):
            pass

    def test_handoff_rejects_unrelated_file_and_live_shared_writer(self):
        wrong = self.root / 'wrong'
        wrong.touch(mode=0o600)
        with wrong.open('r+b') as stream:
            with self.assertRaises(DataOwnerError):
                with inherited_exclusive_owner(self.data, stream.fileno()):
                    self.fail('wrong lease inode')
        with data_owner(self.data):
            descriptor = os.open(self.root / '.data.owner.lease', os.O_RDWR)
            try:
                with self.assertRaises(DataOwnerError):
                    with inherited_exclusive_owner(self.data, descriptor):
                        self.fail('an independent shared writer remains active')
            finally:
                os.close(descriptor)

    def test_fixed_hooks_never_execute_shell_or_unmatched_command(self):
        from certbot.compat import misc
        certs = self.data / 'certificates'
        provider = CertbotProvider(certs)
        record = dict(method='dns-01', environment='production', email='test@example.test',
                      job_id='a' * 32, dns_deadline=123456, _owner_fd=19)
        args = provider.command(record, self.root / 'work')
        arguments = args[7:]
        original = misc.execute_command_status
        auth = arguments[arguments.index('--manual-auth-hook') + 1]
        result = SimpleNamespace(returncode=0, stderr='', stdout='')
        with fixed_bootstrap_hooks(arguments, certs, 19):
            with patch('app.certificates.dns_certbot.subprocess.run', return_value=result) as run:
                self.assertEqual(misc.execute_command_status('manual-auth-hook', auth, env={'PATH': '/usr/bin'}), (0, '', ''))
                invoked = run.call_args
                self.assertEqual(invoked.args[0], shlex.split(auth))
                self.assertEqual(invoked.kwargs['pass_fds'], (19,))
                self.assertNotIn('shell', invoked.kwargs)
                for name, command in [('manual-auth-hook', auth + '; anything'), ('deploy-hook', auth)]:
                    with self.assertRaisesRegex(ValueError, 'HOOK_REFUSED'):
                        misc.execute_command_status(name, command)
                self.assertEqual(run.call_count, 1)
        self.assertIs(misc.execute_command_status, original)
        malformed = list(arguments)
        malformed[malformed.index('--manual-auth-hook') + 1] = auth.replace(str(certs), str(self.root / 'other'))
        with self.assertRaisesRegex(ValueError, 'HOOK_INVALID'):
            with fixed_bootstrap_hooks(malformed, certs, 19):
                self.fail('mismatched data root')

    def test_unpinned_certbot_cannot_inherit_bootstrap_authority(self):
        with patch('importlib.metadata.version', return_value='5.9.0'):
            with self.assertRaisesRegex(ValueError, 'VERSION_UNSUPPORTED'):
                with fixed_bootstrap_hooks([], self.data / 'certificates', 19):
                    self.fail('a new Certbot hook contract requires review')

    def test_real_certbot_wrapper_runs_real_cleanup_hook_with_exclusive_lease(self):
        from app.certificates.dns_journal import DNSChallengeJournal
        from app.certificates.dns_store import DNSCredentialStore
        certs = self.data / 'certificates'
        store = DNSCredentialStore(certs)
        profile = store.create(label='fake', zone_name='example.test', zone_id='c' * 32,
                               api_token='fake_token_' + 'a' * 32)
        journal = DNSChallengeJournal(certs)
        job = 'a' * 32
        context = journal.begin(job, profile['id'], store.snapshot(profile['id'])['revision'],
                                'example.test', ['example.test'])
        code = '''
import os, sys
from pathlib import Path
import certbot.main
from certbot.compat import misc
from app.certificates.dns_certbot import main
from app.certificates.provider import CertbotProvider
root, fd, deadline = Path(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
record = dict(method='dns-01', environment='production', email='a@example.test',
              job_id='a' * 32, dns_deadline=deadline, _owner_fd=fd)
def fake_ca(args):
    hook = args[args.index('--manual-cleanup-hook') + 1]
    status, errors, output = misc.execute_command_status('manual-cleanup-hook', hook, env=dict(os.environ))
    assert status == 0, errors
    return 0
certbot.main.main = fake_ca
raise SystemExit(main(CertbotProvider(root).command(record, root / 'work')[4:]))
'''
        with data_owner(self.data, exclusive=True), exclusive_owner_descriptor(self.data) as descriptor:
            result = subprocess.run([sys.executable, '-c', code, str(certs), str(descriptor), str(context['deadline'])],
                pass_fds=(descriptor,), capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(journal.view(job)['phase'], 'cleaned')
            # Hook cleanup must close its borrowed description, not unlock ours.
            competitor = subprocess.run([sys.executable, '-c',
                'from app.data_owner import data_owner; import sys;\nwith data_owner(sys.argv[1]): pass',
                str(self.data)], capture_output=True, text=True, timeout=5)
            self.assertNotEqual(competitor.returncode, 0)

    def wait_file(self, path):
        deadline = time.monotonic() + 8
        while not path.exists():
            if time.monotonic() >= deadline:
                self.fail('synthetic child did not reach its ready boundary')
            time.sleep(.02)
        return path.read_text()

    def assert_excluded(self):
        for exclusive in (False, True):
            with self.subTest(exclusive=exclusive), self.assertRaises(DataOwnerError):
                with data_owner(self.data, exclusive=exclusive):
                    self.fail('a surviving inherited holder must exclude all writers')

    def test_killed_parent_then_killed_wrapper_leave_live_hook_exclusive(self):
        # The fixed adapter executes an actual child. This synthetic hook only
        # replaces remote DNS work with a deterministic wait, and uses the real
        # inherited lease context; no subprocess.Popen/run is mocked.
        fixture = self.root / 'hooks'
        fixture.mkdir(mode=0o700)
        project = Path(__file__).resolve().parents[1]
        hook = fixture / 'dns_hook.py'
        hook.write_text('''
import sys, time
from pathlib import Path
sys.path.insert(0, PROJECT)
from app.data_owner import inherited_exclusive_owner
root = Path(sys.argv[2]); fd = int(sys.argv[-1])
with inherited_exclusive_owner(root.parent, fd):
    (root.parent / 'hook-ready').write_text(str(__import__('os').getpid()))
    while not (root.parent / 'hook-release').exists(): time.sleep(.02)
'''.replace('PROJECT', repr(str(project))))
        wrapper = '''
import sys
from pathlib import Path
import certbot.main
from certbot.compat import misc
import app.certificates.dns_certbot as wrapper
import app.certificates.provider as provider
fixture, data, fd = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
wrapper.__file__ = str(fixture / 'dns_certbot.py')
provider.__file__ = str(fixture / 'provider.py')
def fake_ca(args):
    command = args[args.index('--manual-cleanup-hook') + 1]
    return misc.execute_command_status('manual-cleanup-hook', command)[0]
certbot.main.main = fake_ca
record = dict(method='dns-01', environment='production', email='a@example.test',
              job_id='a' * 32, dns_deadline=__import__('time').time() + 60, _owner_fd=fd)
raise SystemExit(wrapper.main(provider.CertbotProvider(data / 'certificates').command(record, data / 'work')[4:]))
'''
        launcher = '''
import subprocess, sys, time
from pathlib import Path
from app.data_owner import data_owner, exclusive_owner_descriptor
data, fixture = Path(sys.argv[1]), Path(sys.argv[2])
with data_owner(data, exclusive=True), exclusive_owner_descriptor(data) as fd:
    child = subprocess.Popen([sys.executable, '-c', WRAPPER, str(fixture), str(data), str(fd)],
                             pass_fds=(fd,), stderr=subprocess.DEVNULL)
    pending = data / 'wrapper-pid.pending'
    pending.write_text(str(child.pid)); pending.replace(data / 'wrapper-pid')
    while True: time.sleep(.02)
'''.replace('WRAPPER', repr(wrapper))
        parent = subprocess.Popen([sys.executable, '-c', launcher, str(self.data), str(fixture)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                  start_new_session=True)
        wrapper_pid = None
        try:
            wrapper_pid = int(self.wait_file(self.data / 'wrapper-pid'))
            self.wait_file(self.data / 'hook-ready')
            parent.kill(); parent.communicate(timeout=5)
            self.assert_excluded()
            os.kill(wrapper_pid, signal.SIGKILL)
            self.assert_excluded()
            (self.data / 'hook-release').touch(mode=0o600)
            deadline = time.monotonic() + 5
            while True:
                try:
                    with data_owner(self.data, exclusive=True):
                        break
                except DataOwnerError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.02)
        finally:
            # This known session also contains wrapper/hooks if PID publication
            # or the first readiness wait failed. Never rely on that file for
            # the authority to clean up the fixture's process group.
            try:
                try: os.killpg(parent.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            finally:
                if parent.poll() is None: parent.kill()
                parent.communicate(timeout=5)

    def test_provider_timeout_or_dead_wrapper_reaps_real_hook_group_and_retains_journal(self):
        from app.certificates.dns_journal import DNSChallengeJournal
        from app.certificates.dns_store import DNSCredentialStore
        from app.certificates.errors import CertificateError
        from test_dns_journal import Server, TOKEN, VALUE, ZONE
        project = str(Path(__file__).resolve().parents[1])
        child_code = '''
import os, signal, sys, time
from pathlib import Path
sys.path.insert(0, PROJECT)
from app.data_owner import inherited_exclusive_owner
data, fd = Path(sys.argv[1]), int(sys.argv[2])
with inherited_exclusive_owner(data, fd):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    (data / 'hook-ready').write_text(str(os.getpid()))
    while True: time.sleep(.02)
'''.replace('PROJECT', repr(project))
        wrapper_code = '''
import os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, PROJECT)
from app.data_owner import inherited_exclusive_owner
data, fd, mode = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
with inherited_exclusive_owner(data, fd):
    subprocess.Popen([sys.executable, '-c', CHILD, str(data), str(fd)], pass_fds=(fd,))
    while not (data / 'hook-ready').exists(): time.sleep(.02)
    if mode == 'exit': os._exit(7)
    while True: time.sleep(.02)
'''.replace('PROJECT', repr(project)).replace('CHILD', repr(child_code))
        for mode, expected in [('exit', 'ACME_VALIDATION_FAILED'),
                               ('timeout', 'ISSUANCE_INTERRUPTED_OR_TIMED_OUT')]:
            with self.subTest(mode=mode):
                data = self.root / mode; data.mkdir(mode=0o700)
                certs = data / 'certificates'
                store = DNSCredentialStore(certs)
                profile = store.create(label='fake', zone_name='example.test', zone_id=ZONE, api_token=TOKEN)
                server = Server(); journal = DNSChallengeJournal(certs, client_factory=server.factory)
                provider = CertbotProvider(certs, dns_journal=journal)
                job = 'a' * 32
                journal.begin(job, profile['id'], store.snapshot(profile['id'])['revision'], 'example.test', ['example.test'])
                journal.present(job, 'example.test', VALUE)
                durable = {path: path.read_bytes() for path in certs.rglob('*.json')}
                work = data / 'work'; work.mkdir(mode=0o700)
                def command(record, work):
                    self.assertIn('_owner_fd', record)
                    return [sys.executable, '-c', wrapper_code, str(data), str(record['_owner_fd']), mode]
                with data_owner(data, exclusive=True), patch.object(provider, 'command', side_effect=command):
                    with self.assertRaisesRegex(CertificateError, '^' + expected + '$'):
                        provider._issue_process({'method': 'dns-01'}, work, threading.Event(),
                                                deadline=time.monotonic() + (2 if mode == 'timeout' else 8))
                self.assertTrue((data / 'hook-ready').exists())
                deadline = time.monotonic() + 5
                while True:
                    try:
                        with data_owner(data, exclusive=True): break
                    except DataOwnerError:
                        if time.monotonic() >= deadline: raise
                        time.sleep(.02)
                with data_owner(data): pass
                for path, raw in durable.items(): self.assertEqual(path.read_bytes(), raw)
                self.assertEqual(len(server.records), 1)
                self.assertTrue(journal.cleanup(job))
                self.assertEqual(server.records, {})


if __name__ == '__main__':
    unittest.main()
