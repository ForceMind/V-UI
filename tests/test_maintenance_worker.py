"""Non-root synthetic fixtures: real SQLite, archive operations and OS flocks.

No system service, account, production directory, CA or real core is touched.
The synthetic runtime implements only the isolated health-check HTTP contract.
"""
from contextlib import ExitStack
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import tarfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import release_tools as tools


REPO = Path(__file__).resolve().parents[1]
LOCKS = ('runtime/xray/owner.lock', 'runtime/sing-box/owner.lock', 'certificates/.worker.lock')


@unittest.skipIf(os.geteuid() == 0, 'Real maintenance fixtures require a non-root test user')
class MaintenanceWorkerTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.base = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-maintenance-')))
        self.root = self.base / 'instance'
        self.root.mkdir(mode=0o700)
        self.data = self.root / 'data'
        self.data.mkdir(mode=0o700)
        with sqlite3.connect(self.data / 'v-ui.db') as db:
            db.executescript('CREATE TABLE users(id INTEGER, username TEXT);'
                'CREATE TABLE inbounds(id INTEGER, remark TEXT);'
                'CREATE TABLE admin_sessions(token_hash TEXT);'
                'CREATE TABLE subscription_grants(revoked INTEGER);'
                "INSERT INTO users VALUES(1,'fixture-only');"
                "INSERT INTO inbounds VALUES(1,'original-node');"
                "INSERT INTO admin_sessions VALUES('fake-digest');"
                'INSERT INTO subscription_grants VALUES(0);')
        (self.data / 'v-ui.db').chmod(0o600)
        (self.data / 'routing.json').write_text('{"fixture":true}')
        (self.data / 'routing.json').chmod(0o600)
        for part in ('runtime', 'runtime/xray', 'runtime/sing-box', 'certificates'):
            (self.data / part).mkdir(mode=0o700)
        self.prepared('old')
        tools.atomic_json(self.root / 'CURRENT.json', {'release_id': 'old', 'previous_id': None})
        # Root-controlled metadata is an explicit fixture boundary: these tests
        # run as uid != 0 and cannot create a real root-owned installation.
        # All selected runtime, SQLite, archive, lease and worker work is real.
        self.config = self.base / 'service.json'
        self.config.write_text(json.dumps({'bootstrap_python': str(self.root /
            'releases/old/runtime/python/bin/python3'), 'target': tools.target_key(),
            'release_id': 'old'}))
        self.stack.enter_context(patch.object(tools, '_maintenance_read_config',
            side_effect=lambda: json.loads(self.config.read_text())))
        self.control = self.base / 'control'
        self.control.mkdir(mode=0o700)
        shutil.copyfile(REPO / 'deploy/ops_worker.py', self.control / 'ops_worker.py')
        shutil.copyfile(REPO / 'app/release_tools.py', self.control / 'release_tools.py')
        shutil.copyfile(REPO / 'app/data_owner.py', self.control / 'data_owner.py')
        for name in ('proxy_store.py', 'upstream_profile.py'):
            shutil.copyfile(REPO / 'app/services' / name, self.control / name)

    def prepared(self, identity, *, healthy=True):
        source = self.base / ('source-' + identity)
        source.mkdir(mode=0o700)
        (source / 'main.py').write_text('# Known synthetic fixture, never production code.\n')
        archive = self.base / (identity + '.zip')
        checksum = tools.create_archive(source, archive, {'kind': 'release', 'platform': tools.PLATFORM,
            'release_id': identity, 'targets': [tools.target_key()]})
        release = self.root / 'releases' / identity
        release.mkdir(parents=True, mode=0o700)
        tools.unpack_verified(archive, checksum, release / 'payload')
        runtime = release / 'runtime/python/bin'
        runtime.mkdir(parents=True, mode=0o700)
        executable = runtime / 'python3'
        if healthy:
            executable.write_text('#!' + sys.executable + '\n'
                'from http.server import BaseHTTPRequestHandler,HTTPServer\n'
                'class Handler(BaseHTTPRequestHandler):\n'
                ' def do_GET(self):\n'
                '  self.send_response(200 if self.path=="/login" else 401);self.end_headers()\n'
                ' def log_message(self,*args): pass\n'
                'server=HTTPServer(("127.0.0.1",0),Handler)\n'
                'print("Uvicorn running on http://127.0.0.1:"+str(server.server_port),flush=True)\n'
                'server.serve_forever()\n')
        else:
            executable.write_text('#!' + sys.executable + '\nraise SystemExit(4)\n')
        executable.chmod(0o700)
        tools.atomic_json(release / 'READY.json', {
            'manifest_sha256': tools.digest((release / 'payload' / tools.MANIFEST).read_bytes()),
            'runtime_key': tools.target_key(),
            'runtime_tree_sha256': tools.runtime_tree_digest(release / 'runtime')})
        return release

    def session(self, action, **values):
        session = tools._MaintenanceSession(self.root)
        self.stack.callback(session.close)
        session.prepare(action, **values)
        return session

    def stage_archive(self, identity='staged', *, healthy=True, change_current=False,
                      health_delay=0, require_eof=False):
        source = self.base / ('stage-source-' + identity)
        source.mkdir(mode=0o700)
        (source / 'runtimes').mkdir(mode=0o700)
        (source / 'main.py').write_text('# Known synthetic stage fixture.\n')
        runtime_script = (self.root / 'releases/old/runtime/python/bin/python3').read_text()
        header, body = runtime_script.split('\n', 1)
        setup = 'import sys,os\nfrom pathlib import Path\n'
        if require_eof:
            setup += 'if os.read(0,1) != b"": raise SystemExit(19)\n'
        setup += (
            'if "ensurepip" in sys.argv or "pip" in sys.argv:\n'
            ' print(\'{"event":"done","ok":true,"prepared_release":"forged"}\')\n')
        if change_current:
            setup += (' if "ensurepip" in sys.argv:\n'
                '  p=Path(os.environ["VUI_DATA_DIR"]).parent/"CURRENT.json"\n'
                '  p.write_bytes(p.read_bytes()+b" ")\n')
        setup += ' raise SystemExit(0)\n'
        if health_delay:
            setup += 'import time\ntime.sleep(' + repr(health_delay) + ')\n'
        if not healthy: body = 'raise SystemExit(7)\n'
        executable = (header + '\n' + setup + body).encode()
        runtime_archive = source / 'runtimes' / (tools.target_key() + '.tar.gz')
        with tarfile.open(runtime_archive, 'w:gz') as archive:
            entry = tarfile.TarInfo('python/bin/python3')
            entry.size = len(executable); entry.mode = 0o700
            archive.addfile(entry, io.BytesIO(executable))
        pin = hashlib.sha256(runtime_archive.read_bytes()).hexdigest()
        (source / ('requirements.' + tools.target_key() + '.lock')).write_text('')
        archive = self.base / ('stage-' + identity + '.zip')
        checksum = tools.create_archive(source, archive, {
            'kind': 'release', 'platform': tools.PLATFORM, 'release_id': identity,
            'targets': [tools.target_key()], 'portable_runtime_pins': {tools.target_key(): {'sha256': pin}}})
        return archive, checksum

    def busy(self, path):
        try:
            with tools.lease(path): pass
        except tools.ReleaseError:
            return True
        return False

    def assert_writers(self, data, busy):
        for name in LOCKS:
            self.assertEqual(self.busy(data / name), busy, name)

    def start_worker(self, *, timeout=10, recovery_interrupt=None):
        code = ('import runpy,sys,json;from pathlib import Path;'
                'm=runpy.run_path(sys.argv[1]);'
                'api=m["_load_release_tools"]();'
                'api._maintenance_read_config=lambda:json.loads(Path(sys.argv[4]).read_text());')
        if recovery_interrupt is not None:
            self.assertIn(recovery_interrupt, ('before_current', 'after_current', 'before_old', 'after_old'))
            code += '\n' + (
                'import os,signal\n'
                'original_replace=api.os.replace\n'
                'def interrupt_replace(source,destination):\n'
                ' point=("current" if Path(source)==Path(sys.argv[2])/"data" else\n'
                '        "old" if Path(source).name=="before-"+"1"*32 else "other")\n'
                ' if "before_"+point==sys.argv[5]: signal.raise_signal(signal.SIGTERM)\n'
                ' result=original_replace(source,destination)\n'
                ' if "after_"+point==sys.argv[5]: signal.raise_signal(signal.SIGTERM)\n'
                ' return result\n'
                'api.os.replace=interrupt_replace\n')
        code += (
                'raise SystemExit(m["_run"](Path(sys.argv[2]),api,'
                'sys.stdin.fileno(),sys.stdout,timeout=float(sys.argv[3])))')
        process = subprocess.Popen([sys.executable, '-I', '-S', '-c', code,
            str(self.control / 'ops_worker.py'), str(self.root), str(timeout), str(self.config),
            recovery_interrupt or ''],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True)

        def cleanup():
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        self.stack.callback(cleanup)
        return process

    def send(self, process, value):
        process.stdin.write(json.dumps(value) + '\n')
        process.stdin.flush()

    def event(self, process):
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            self.assertTrue(selector.select(5), 'Worker did not emit a bounded event')
        line = process.stdout.readline()
        self.assertTrue(line, 'Worker exited before its expected event')
        self.assertLessEqual(len(line), 8192)
        return json.loads(line)

    def request(self, process, action, **values):
        self.send(process, {'protocol': 1, 'action': action, **values})
        self.assertEqual(self.event(process), {'event': 'prepared'})

    def finish(self, process):
        self.send(process, {'command': 'release_writers'})
        self.assertEqual(self.event(process), {'event': 'writers_released'})
        self.assertTrue(self.busy(self.root / '.operation.lock'))
        self.assertFalse(self.busy(self.root / '.panel.lease'))
        self.assert_writers(self.data, False)
        self.send(process, {'command': 'finish'})
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertFalse(self.busy(self.root / '.operation.lock'))

    def test_prepared_holds_operation_but_does_not_claim_quiescence(self):
        session = self.session('backup')
        self.assertTrue(self.busy(self.root / '.operation.lock'))
        self.assertFalse(self.busy(self.root / '.panel.lease'))
        with self.assertRaises(tools.ReleaseError):
            tools.backup(self.root, self.base / 'competing.zip')
        with self.assertRaises(tools.ReleaseError):
            self.session('start')
        session.close()
        self.assertFalse(self.busy(self.root / '.operation.lock'))

    def test_backup_real_sqlite_and_operation_retained_through_release(self):
        current = (self.root / 'CURRENT.json').read_bytes()
        session = self.session('backup')
        result = session.execute()
        self.assertRegex(result['backup_name'], r'^maintenance-[a-f0-9]{32}\.zip$')
        archive = self.root / 'backups' / result['backup_name']
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), result['backup_sha256'])
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        self.assert_writers(self.data, True)
        self.assertTrue(self.busy(self.root / '.panel.lease'))
        self.assertEqual((self.root / 'CURRENT.json').read_bytes(), current)
        with self.assertRaises(tools.ReleaseError): session.execute()
        session.release_writers()
        self.assert_writers(self.data, False)
        self.assertTrue(self.busy(self.root / '.operation.lock'))
        with self.assertRaises(tools.ReleaseError): session.release_writers()
        session.close()

    def test_each_existing_writer_lease_blocks_without_mutation(self):
        for name in ('.panel.lease', *('data/' + value for value in LOCKS)):
            with self.subTest(name=name):
                session = self.session('backup')
                with tools.lease(self.root / name):
                    with self.assertRaises(tools.ReleaseError): session.execute()
                self.assertFalse((self.root / 'backups').exists())
                with self.assertRaises(tools.ReleaseError): session.release_writers()
                session.close()

    def test_restore_preserves_original_and_locks_both_inode_generations(self):
        archive = self.base / 'restore.zip'
        checksum = tools.backup(self.root, archive)
        with sqlite3.connect(self.data / 'v-ui.db') as db:
            db.execute("UPDATE inbounds SET remark='changed-node'")
        session = self.session('restore', archive=archive, sha256=checksum)
        replace = tools.os.replace
        observed = []

        def inspect_replace(source, destination):
            if Path(source) == self.data:
                staged = list(self.root.glob('.restore-*/data'))
                self.assertEqual(len(staged), 1)
                self.assert_writers(self.data, True)
                self.assert_writers(staged[0], True)
                observed.append('both-before-rename')
            value = replace(source, destination)
            if Path(destination) == self.data:
                self.assert_writers(self.data, True)
                observed.append('new-after-rename')
            return value

        with patch.object(tools.os, 'replace', side_effect=inspect_replace):
            self.assertEqual(session.execute(), {})
        self.assertEqual(observed, ['both-before-rename', 'new-after-rename'])
        original = list((self.root / 'recovery').glob('before-*'))
        self.assertEqual(len(original), 1)
        self.assert_writers(original[0], True)
        self.assert_writers(self.data, True)
        with sqlite3.connect(self.data / 'v-ui.db') as db:
            self.assertEqual(db.execute('SELECT remark FROM inbounds').fetchone()[0], 'original-node')
            self.assertEqual(db.execute('SELECT count(*) FROM admin_sessions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT revoked FROM subscription_grants').fetchone()[0], 1)
        with sqlite3.connect(original[0] / 'v-ui.db') as db:
            self.assertEqual(db.execute('SELECT remark FROM inbounds').fetchone()[0], 'changed-node')
        self.assertFalse((self.root / 'RESTORE_PENDING.json').exists())
        session.release_writers()
        self.assert_writers(original[0], False)
        self.assert_writers(self.data, False)
        self.assertTrue(self.busy(self.root / '.operation.lock'))

    def recovery_fixture(self, *, old_present=True, current_present=True):
        recovery = self.root / 'recovery'
        recovery.mkdir(mode=0o700, exist_ok=True)
        old = recovery / ('before-' + '1' * 32)
        if old_present:
            shutil.copytree(self.data, old)
        if current_present:
            with sqlite3.connect(self.data / 'v-ui.db') as db:
                db.execute("UPDATE inbounds SET remark='displaced-current'")
        else:
            shutil.rmtree(self.data)
        tools.atomic_json(self.root / 'RESTORE_PENDING.json', {'old': old.relative_to(self.root).as_posix()})
        return old

    def test_recover_restore_four_directory_combinations(self):
        pristine = self.base / 'pristine'
        shutil.copytree(self.data, pristine)
        for old_present, current_present in ((True, True), (True, False), (False, True), (False, False)):
            with self.subTest(old=old_present, data=current_present):
                shutil.rmtree(self.data, ignore_errors=True)
                shutil.rmtree(self.root / 'recovery', ignore_errors=True)
                shutil.copytree(pristine, self.data)
                old = self.recovery_fixture(old_present=old_present, current_present=current_present)
                if not old_present and not current_present:
                    with self.assertRaises(tools.ReleaseError): self.session('recover-restore')
                    self.assertFalse(self.data.exists())
                    self.assertTrue((self.root / 'RESTORE_PENDING.json').exists())
                    continue
                session = self.session('recover-restore')
                self.assertEqual(self.data.exists(), current_present)
                self.assertEqual(session.execute(), {})
                self.assertFalse((self.root / 'RESTORE_PENDING.json').exists())
                self.assertFalse(old.exists())
                with sqlite3.connect(self.data / 'v-ui.db') as db:
                    self.assertEqual(db.execute('SELECT remark FROM inbounds').fetchone()[0],
                                     'original-node' if old_present else 'displaced-current')
                    # Recover moves the original data, unlike restore; it must
                    # not invent a second token-revocation/database algorithm.
                    self.assertEqual(db.execute('SELECT count(*) FROM admin_sessions').fetchone()[0], 1)
                self.assert_writers(self.data, True)
                displaced = list((self.root / 'recovery').glob('interrupted-*'))
                self.assertEqual(len(displaced), int(old_present and current_present))
                if displaced:
                    self.assert_writers(displaced[0], True)
                    with sqlite3.connect(displaced[0] / 'v-ui.db') as db:
                        self.assertEqual(db.execute('SELECT remark FROM inbounds').fetchone()[0], 'displaced-current')
                session.release_writers()
                self.assertTrue(self.busy(self.root / '.operation.lock'))
                self.assert_writers(self.data, False)
                if displaced: self.assert_writers(displaced[0], False)
                session.close()

    def test_installed_smoke_audits_real_recovery_siblings_after_worker_release(self):
        from scripts.managed_ops_smoke import _audit_released_writer_leases, _write_drivers
        from scripts.ops_menu_smoke import _lease_busy
        archive = self.root / 'original-backup.zip'
        checksum = tools.backup(self.root, archive)
        restore = self.session('restore', archive=archive, sha256=checksum)
        restore.execute(); restore.release_writers(); restore.close()
        retained = list((self.root / 'recovery').glob('before-*'))
        self.assertEqual(len(retained), 1)
        pending_old = self.recovery_fixture()
        driver = _write_drivers(self.control, self.root, Path(sys.executable), self.config)
        functions = [node for node in ast.parse(driver.read_text()).body
                     if isinstance(node, ast.FunctionDef) and node.name in ('busy', 'stable_busy', 'locks')]
        self.assertEqual(len(functions), 3)
        namespace = {'ROOT': self.root, 'Path': Path, 'os': os,
                     'fcntl': __import__('fcntl'), 'stat': __import__('stat')}
        exec(compile(ast.Module(body=functions, type_ignores=[]),
                     '<actual installed fixture lease assertions>', 'exec'), namespace)
        process = self.start_worker()
        self.request(process, 'recover-restore')
        self.send(process, {'command': 'proceed'})
        self.assertEqual(self.event(process), {'event': 'done', 'ok': True})
        namespace['locks'](True, True)
        displaced = list((self.root / 'recovery').glob('interrupted-*'))
        self.assertEqual(len(displaced), 1)
        stable = (self.root / '.data.owner.lease',
                  pending_old.with_name('.' + pending_old.name + '.owner.lease'))
        for path in stable:
            self.assertTrue(_lease_busy(path), 'Actual worker must retain both stable locks through done')
            self.assertTrue(namespace['stable_busy'](path))
        with self.assertRaises(AssertionError):
            _audit_released_writer_leases(self.root, retained[0], displaced[0], pending_old)
        self.send(process, {'command': 'release_writers'})
        self.assertEqual(self.event(process), {'event': 'writers_released'})
        namespace['locks'](True, False)
        for path in stable:
            self.assertFalse(_lease_busy(path))
            self.assertFalse(namespace['stable_busy'](path))
        self.assertEqual(_audit_released_writer_leases(
            self.root, retained[0], displaced[0], pending_old),
            {'generations': 3, 'component_locks': 9, 'stable_siblings': 2})
        # Retain the old audit's exact RED against real recovery output. Merely
        # filtering non-directories would hide unknown entries or leaked leases.
        with self.assertRaisesRegex(AssertionError, 'Expected real instance lease'):
            for generation in [self.data, *sorted((self.root / 'recovery').iterdir())]:
                for name in LOCKS:
                    _lease_busy(generation / name)
        self.send(process, {'command': 'finish'})
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertFalse(_lease_busy(self.root / '.operation.lock'))
        self.assertFalse(_lease_busy(self.root / '.panel.lease'))
        self.assertEqual(_audit_released_writer_leases(
            self.root, retained[0], displaced[0], pending_old),
            {'generations': 3, 'component_locks': 9, 'stable_siblings': 2})

    def test_recover_worker_missing_data_then_holds_operation_through_finish(self):
        self.recovery_fixture(current_present=False)
        process = self.start_worker()
        self.request(process, 'recover-restore')
        self.assertFalse(self.data.exists(), 'Preparation must never mkdir missing data')
        self.send(process, {'command': 'proceed'})
        self.assertEqual(self.event(process), {'event': 'done', 'ok': True})
        self.assert_writers(self.data, True)
        self.finish(process)

    def test_recovery_bad_journals_never_create_missing_data(self):
        old = self.recovery_fixture(current_present=False)
        journal = self.root / 'RESTORE_PENDING.json'
        valid = {'old': old.relative_to(self.root).as_posix()}
        invalid = [b'{}', json.dumps({'old': 'recovery/before-fixture'}).encode(),
            json.dumps({'old': '../data'}).encode(), json.dumps({'old': '/tmp/data'}).encode(),
            json.dumps({**valid, 'extra': True}).encode(),
            ('{"old":"' + valid['old'] + '","old":"' + valid['old'] + '"}').encode(),
            b' ' * 16385]
        for raw in invalid:
            journal.write_bytes(raw); journal.chmod(0o600)
            with self.subTest(raw=raw[:80]), self.assertRaises((tools.ReleaseError, ValueError)):
                self.session('recover-restore')
            self.assertFalse(self.data.exists())
            self.assertTrue(old.exists())
        journal.unlink()
        journal.symlink_to(old / 'v-ui.db')
        with self.assertRaises((tools.ReleaseError, OSError)): self.session('recover-restore')
        journal.unlink(); tools.atomic_json(journal, valid)
        os.link(journal, self.base / 'journal-hardlink')
        with self.assertRaises(tools.ReleaseError): self.session('recover-restore')

    def test_recovery_public_or_fifo_journal_is_rejected(self):
        self.recovery_fixture(current_present=False)
        journal = self.root / 'RESTORE_PENDING.json'
        journal.chmod(0o644)
        with self.assertRaises(tools.ReleaseError): self.session('recover-restore')
        journal.unlink(); os.mkfifo(journal, 0o600)
        with self.assertRaises(tools.ReleaseError): self.session('recover-restore')
        self.assertFalse(self.data.exists())

    def test_recovery_displaced_destination_collision_does_not_overwrite(self):
        old = self.recovery_fixture()
        occupied = self.root / 'recovery' / ('interrupted-' + '2' * 32)
        occupied.mkdir(mode=0o700)
        (occupied / 'preserved').write_text('prior recovery evidence')
        session = self.session('recover-restore')
        with patch.object(tools.uuid, 'uuid4', return_value=SimpleNamespace(hex='2' * 32)):
            with self.assertRaises(tools.ReleaseError): session.execute()
        self.assertEqual((occupied / 'preserved').read_text(), 'prior recovery evidence')
        self.assertTrue(old.exists()); self.assertTrue(self.data.exists())
        self.assertTrue((self.root / 'RESTORE_PENDING.json').exists())

    def test_recovery_no_journal_is_noop_with_real_post_validation(self):
        before = (self.data / 'v-ui.db').read_bytes()
        session = self.session('recover-restore')
        self.assertEqual(session.execute(), {})
        self.assertEqual((self.data / 'v-ui.db').read_bytes(), before)
        self.assertFalse((self.root / 'recovery').exists())
        self.assert_writers(self.data, True)
        session.release_writers()
        self.assertTrue(self.busy(self.root / '.operation.lock'))

    def test_recovery_private_directories_and_generation_leases_are_required(self):
        old = self.recovery_fixture()
        for data in (old, self.data):
            for name in LOCKS:
                with self.subTest(data=data.name, lock=name):
                    session = self.session('recover-restore')
                    with tools.lease(data / name):
                        with self.assertRaises(tools.ReleaseError): session.execute()
                    self.assertTrue(old.exists())
                    self.assertTrue(self.data.exists())
                    self.assertTrue((self.root / 'RESTORE_PENDING.json').exists())
                    session.close()
        (self.root / 'recovery').chmod(0o755)
        with self.assertRaises(tools.ReleaseError): self.session('recover-restore')
        (self.root / 'recovery').chmod(0o700)
        shutil.rmtree(old)
        old.symlink_to(self.data, target_is_directory=True)
        with self.assertRaises((tools.ReleaseError, OSError)): self.session('recover-restore')

    def test_recovery_journal_raw_bytes_revalidated_after_prepared(self):
        old = self.recovery_fixture()
        session = self.session('recover-restore')
        journal = self.root / 'RESTORE_PENDING.json'
        journal.write_bytes(journal.read_bytes() + b' ')
        with self.assertRaises(tools.ReleaseError): session.execute()
        self.assertTrue(old.exists())
        self.assertTrue(self.data.exists())
        with self.assertRaises(tools.ReleaseError): session.release_writers()

    def test_recovery_rejects_replaced_root_inode_even_with_identical_journal(self):
        old = self.recovery_fixture()
        session = self.session('recover-restore')
        retained = self.base / 'retained-instance'
        os.replace(self.root, retained)
        shutil.copytree(retained, self.root)
        with self.assertRaises(tools.ReleaseError): session.execute()
        self.assertTrue(old.exists()); self.assertTrue(self.data.exists())
        self.assertEqual(list((self.root / 'recovery').glob('interrupted-*')), [])
        self.assertTrue((self.root / 'RESTORE_PENDING.json').exists())

    def test_recovery_retains_both_locks_before_and_after_renames(self):
        old = self.recovery_fixture()
        session = self.session('recover-restore')
        replace = tools.os.replace
        observations = []
        def inspect(source, destination):
            source, destination = Path(source), Path(destination)
            if source in (old, self.data):
                self.assert_writers(source, True)
                self.assertTrue(self.busy(self.root / '.panel.lease'))
                self.assertTrue(self.busy(self.root / '.operation.lock'))
            result = replace(source, destination)
            if source in (old, self.data):
                self.assert_writers(destination, True)
                observations.append(source.name)
            return result
        with patch.object(tools.os, 'replace', side_effect=inspect): session.execute()
        self.assertEqual(observations, ['data', old.name])

    def test_recovery_signal_at_each_rename_boundary_preserves_both_data_sets(self):
        pristine = self.base / 'pristine'
        shutil.copytree(self.data, pristine)
        for point in ('before_current', 'after_current', 'before_old', 'after_old'):
            with self.subTest(point=point):
                shutil.rmtree(self.data, ignore_errors=True)
                shutil.rmtree(self.root / 'recovery', ignore_errors=True)
                shutil.copytree(pristine, self.data)
                old = self.recovery_fixture()
                process = self.start_worker(recovery_interrupt=point)
                self.request(process, 'recover-restore')
                self.send(process, {'command': 'proceed'})
                self.assertEqual(self.event(process), {'event': 'error', 'code': 'MAINTENANCE_REFUSED'})
                self.assertEqual(process.wait(timeout=5), 1)
                self.assertTrue((self.root / 'RESTORE_PENDING.json').exists())
                places = [path for path in (self.data, old, *list((self.root / 'recovery').glob('interrupted-*')))
                          if path.exists()]
                remarks = []
                for data in places:
                    self.assert_writers(data, False)
                    with sqlite3.connect(data / 'v-ui.db') as db:
                        remarks.append(db.execute('SELECT remark FROM inbounds').fetchone()[0])
                self.assertCountEqual(remarks, ['original-node', 'displaced-current'])
                self.assertFalse(self.busy(self.root / '.operation.lock'))
                # A second explicit recovery must finish whichever original
                # rename had completed, preserving the displaced data evidence.
                retry = self.session('recover-restore')
                self.assertEqual(retry.execute(), {})
                with sqlite3.connect(self.data / 'v-ui.db') as db:
                    self.assertEqual(db.execute('SELECT remark FROM inbounds').fetchone()[0], 'original-node')
                displaced = list((self.root / 'recovery').glob('interrupted-*'))
                self.assertEqual(len(displaced), 1)
                with sqlite3.connect(displaced[0] / 'v-ui.db') as db:
                    self.assertEqual(db.execute('SELECT remark FROM inbounds').fetchone()[0], 'displaced-current')
                retry.release_writers(); retry.close()

    def test_recovery_invalid_active_or_anchor_never_releases_writers(self):
        self.recovery_fixture()
        session = self.session('recover-restore')
        (self.root / 'releases/old/runtime/python/bin/python3').write_text('damaged fixture')
        with self.assertRaises(tools.ReleaseError): session.execute()
        self.assertTrue((self.data / 'v-ui.db').exists())
        self.assertEqual(len(list((self.root / 'recovery').glob('interrupted-*'))), 1)
        self.assert_writers(self.data, True)
        with self.assertRaises(tools.ReleaseError): session.release_writers()

    def test_activate_backs_up_before_current_changes_with_real_health_flow(self):
        self.prepared('new')
        session = self.session('activate', identity='new')
        result = session.execute()
        self.assertEqual(result['selected_release'], 'new')
        self.assertTrue((self.root / 'backups' / result['backup_name']).is_file())
        self.assertEqual(json.loads((self.root / 'CURRENT.json').read_text()),
                         {'release_id': 'new', 'previous_id': 'old'})
        self.assert_writers(self.data, True)

    def test_bad_candidate_health_preserves_current_and_completed_backup(self):
        self.prepared('unhealthy', healthy=False)
        before = (self.root / 'CURRENT.json').read_bytes()
        session = self.session('activate', identity='unhealthy')
        with self.assertRaises(tools.ReleaseError): session.execute()
        self.assertEqual((self.root / 'CURRENT.json').read_bytes(), before)
        self.assertEqual(len(list((self.root / 'backups').glob('maintenance-*.zip'))), 1)
        self.assertTrue((self.root / 'releases/unhealthy').is_dir())
        with self.assertRaises(tools.ReleaseError): session.release_writers()

    def test_old_bootstrap_anchor_remains_required_after_activation(self):
        self.prepared('new')
        session = self.session('activate', identity='new')
        session.execute(); session.release_writers(); session.close()
        # The old root configuration is intentionally not rewritten by worker.
        self.assertEqual(json.loads(self.config.read_text())['release_id'], 'old')
        self.assertEqual(tools._maintenance_anchor(self.root)[0], 'old')
        session = self.session('start')
        self.assertEqual(session.execute(), {})
        session.close()
        (self.root / 'releases/old/runtime/python/bin/python3').unlink()
        with self.assertRaises((tools.ReleaseError, OSError)):
            self.session('start')
        self.assertEqual(json.loads((self.root / 'CURRENT.json').read_text())['release_id'], 'new')

    def test_anchor_cannot_escape_or_use_wrong_target_or_nonexecutable_runtime(self):
        original = json.loads(self.config.read_text())
        for changes in ({'bootstrap_python': '/usr/bin/python3'},
                        {'bootstrap_python': str(self.root / 'releases/../old/runtime/python/bin/python3')},
                        {'target': 'unsupported-target'}):
            self.config.write_text(json.dumps({**original, **changes}))
            with self.assertRaises(tools.ReleaseError): self.session('start')
        self.config.write_text(json.dumps(original))
        (self.root / 'releases/old/runtime/python/bin/python3').chmod(0o600)
        with self.assertRaises(tools.ReleaseError): self.session('start')

    def test_pending_restore_refuses_start_and_data_actions_but_allows_stop(self):
        tools.atomic_json(self.root / 'RESTORE_PENDING.json', {'old': 'recovery/before-fixture'})
        for action in ('start', 'restart', 'backup', 'activate'):
            with self.subTest(action=action), self.assertRaises(tools.ReleaseError):
                self.session(action, **({'identity': 'old'} if action == 'activate' else {}))
        session = self.session('stop')
        self.assertEqual(session.execute(), {})
        session.release_writers()
        self.assertTrue((self.root / 'RESTORE_PENDING.json').exists())

    def test_new_journal_after_preflight_is_not_ignored(self):
        session = self.session('start')
        tools.atomic_json(self.root / 'RESTORE_PENDING.json', {'old': 'recovery/before-fixture'})
        with self.assertRaises(tools.ReleaseError): session.execute()
        with self.assertRaises(tools.ReleaseError): session.release_writers()

    def test_stop_with_pending_restore_and_missing_data_stays_failed_closed(self):
        recovery = self.root / 'recovery'
        recovery.mkdir(mode=0o700)
        os.replace(self.data, recovery / 'before-fixture')
        tools.atomic_json(self.root / 'RESTORE_PENDING.json', {'old': 'recovery/before-fixture'})
        process = self.start_worker()
        self.request(process, 'stop')
        self.send(process, {'command': 'proceed'})
        self.assertEqual(self.event(process), {'event': 'error', 'code': 'MAINTENANCE_REFUSED'})
        self.assertEqual(process.wait(timeout=5), 1)
        self.assertFalse(self.data.exists())
        self.assertTrue((recovery / 'before-fixture/v-ui.db').is_file())
        self.assertTrue((self.root / 'RESTORE_PENDING.json').exists())
        self.assertFalse(self.busy(self.root / '.operation.lock'))

    def test_wrong_hash_and_invalid_candidate_fail_before_prepared(self):
        archive = self.base / 'restore.zip'
        tools.backup(self.root, archive)
        with self.assertRaises(tools.ReleaseError):
            self.session('restore', archive=archive, sha256='0'*64)
        self.assertFalse(self.busy(self.root / '.operation.lock'))
        with self.assertRaises((tools.ReleaseError, OSError)):
            self.session('activate', identity='missing')
        self.assertFalse((self.root / 'backups').exists())

    def test_symlink_writer_parent_and_hardlinked_lock_are_refused(self):
        external = self.base / 'external'
        external.mkdir(mode=0o700)
        (self.data / 'runtime/xray').rmdir()
        (self.data / 'runtime/xray').symlink_to(external, target_is_directory=True)
        session = self.session('backup')
        with self.assertRaises((tools.ReleaseError, OSError)): session.execute()
        self.assertFalse((external / 'owner.lock').exists())
        session.close()
        (self.data / 'runtime/xray').unlink()
        (self.data / 'runtime/xray').mkdir(mode=0o700)
        lock = self.data / LOCKS[0]
        lock.write_bytes(b''); lock.chmod(0o600)
        os.link(lock, self.base / 'linked-lock')
        session = self.session('backup')
        with self.assertRaises(tools.ReleaseError): session.execute()

    def test_closed_guard_cannot_bypass_public_stopped_scope(self):
        with tools.stopped(self.root) as guard: pass
        with self.assertRaises(tools.ReleaseError):
            tools._backup_locked(guard, self.base / 'bypass.zip')
        self.assertFalse((self.base / 'bypass.zip').exists())

    def test_real_worker_protocol_backup_and_reentry(self):
        process = self.start_worker()
        self.request(process, 'backup')
        self.assertTrue(self.busy(self.root / '.operation.lock'))
        competing = self.start_worker()
        self.send(competing, {'protocol': 1, 'action': 'start'})
        self.assertEqual(self.event(competing), {'event': 'error', 'code': 'MAINTENANCE_REFUSED'})
        self.assertEqual(competing.wait(timeout=5), 1)
        self.send(process, {'command': 'proceed'})
        done = self.event(process)
        self.assertEqual(set(done), {'event', 'ok', 'backup_name', 'backup_sha256'})
        self.assertEqual(done['event'], 'done'); self.assertIs(done['ok'], True)
        self.assert_writers(self.data, True)
        self.finish(process)

    def test_real_worker_protocol_proxy_backup_restore_preserves_all_four_states_and_secrets(self):
        from tests.test_proxy_controller_dependencies import seed_proxy
        store = seed_proxy(self.data)
        for local, relay in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(local=local, relay=relay):
                current = store.read_snapshot()
                settings = {key: value for key, value in current['settings'].items()
                            if key != 'local_password_set'}
                settings.update(local_proxy_enabled=local, relay_enabled=relay)
                draft = store.save_draft(settings, current['revision'])
                transaction = 'fixture-state-' + str(int(local)) + str(int(relay))
                store.begin_apply(draft['revision'], transaction)
                store.commit_apply(transaction, transaction + '-core')
                before = store.load_active()
                snapshot = store.read_snapshot()
                process = self.start_worker()
                self.request(process, 'backup')
                self.send(process, {'command': 'proceed'})
                result = self.event(process)
                self.assertIs(result['ok'], True)
                self.assert_writers(self.data, True)
                self.assertTrue(self.busy(self.root / '.panel.lease'))
                self.assertTrue(self.busy(self.root / '.operation.lock'))
                self.finish(process)
                archive = self.root / 'backups' / result['backup_name']
                process = self.start_worker()
                self.request(process, 'restore', path=str(archive), sha256=result['backup_sha256'])
                self.send(process, {'command': 'proceed'})
                self.assertEqual(self.event(process), {'event': 'done', 'ok': True})
                self.assert_writers(self.data, True)
                self.assertTrue(self.busy(self.root / '.panel.lease'))
                self.assertTrue(self.busy(self.root / '.operation.lock'))
                self.finish(process)
                self.assertEqual(store.load_active(), before)
                self.assertEqual(store.read_snapshot(), snapshot)
                with sqlite3.connect(self.data / 'v-ui.db') as db:
                    self.assertEqual(db.execute('SELECT remark FROM inbounds').fetchall(), [('original-node',)])
                    self.assertEqual(db.execute('SELECT count(*) FROM admin_sessions').fetchone()[0], 0)
                    self.assertEqual(db.execute('SELECT revoked FROM subscription_grants').fetchone()[0], 1)

    def test_real_worker_start_verifies_then_releases_writers(self):
        process = self.start_worker()
        self.request(process, 'start')
        self.send(process, {'command': 'proceed'})
        self.assertEqual(self.event(process), {'event': 'done', 'ok': True})
        self.assert_writers(self.data, True)
        self.finish(process)

    def test_worker_stage_reuses_real_primitive_without_taking_live_writer_leases(self):
        archive, checksum = self.stage_archive(require_eof=True)
        before = (self.root / 'CURRENT.json').read_bytes()
        leases = ExitStack()
        self.stack.callback(leases.close)
        for path in (self.root / '.panel.lease', *(self.data / name for name in LOCKS)):
            leases.enter_context(tools.lease(path))
        process = self.start_worker()
        self.request(process, 'stage', path=str(archive), sha256=checksum)
        self.send(process, {'command': 'proceed'})
        # Synthetic pip deliberately prints a forged event. It is discarded,
        # and only stage's health-checked return can generate this real event.
        self.assertEqual(self.event(process), {'event': 'done', 'ok': True, 'prepared_release': 'staged'})
        self.assertEqual((self.root / 'CURRENT.json').read_bytes(), before)
        self.assertTrue(self.busy(self.root / '.operation.lock'))
        self.assertTrue(self.busy(self.root / '.panel.lease'))
        self.assert_writers(self.data, True)
        tools.verify_runtime(self.root / 'releases/staged')
        tools.verify_payload(self.root / 'releases/staged/payload')
        self.send(process, {'command': 'finish'})
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertFalse(self.busy(self.root / '.operation.lock'))
        self.assert_writers(self.data, True)

    def test_stage_rejects_release_writers_and_duplicate_keeps_existing_candidate(self):
        archive, checksum = self.stage_archive()
        process = self.start_worker()
        self.request(process, 'stage', path=str(archive), sha256=checksum)
        self.send(process, {'command': 'proceed'})
        self.assertEqual(self.event(process)['prepared_release'], 'staged')
        self.send(process, {'command': 'release_writers'})
        self.assertEqual(self.event(process)['event'], 'error')
        self.assertEqual(process.wait(timeout=5), 1)
        self.assertTrue((self.root / 'releases/staged/READY.json').exists())
        process = self.start_worker()
        self.request(process, 'stage', path=str(archive), sha256=checksum)
        self.send(process, {'command': 'proceed'})
        self.assertEqual(self.event(process)['event'], 'error')
        self.assertEqual(process.wait(timeout=5), 1)
        self.assertTrue((self.root / 'releases/staged/READY.json').exists())

    def test_stage_health_failure_and_raw_current_change_are_not_success(self):
        for identity, changes in (('unhealthy-stage', {'healthy': False}),
                                  ('changed-current', {'change_current': True})):
            with self.subTest(identity=identity):
                archive, checksum = self.stage_archive(identity, **changes)
                process = self.start_worker()
                self.request(process, 'stage', path=str(archive), sha256=checksum)
                self.send(process, {'command': 'proceed'})
                self.assertEqual(self.event(process), {'event': 'error', 'code': 'MAINTENANCE_REFUSED'})
                self.assertEqual(process.wait(timeout=5), 1)
                self.assertEqual(json.loads((self.root / 'CURRENT.json').read_text())['release_id'], 'old')
                self.assertFalse(self.busy(self.root / '.operation.lock'))
        self.assertFalse((self.root / 'releases/unhealthy-stage').exists())
        # The caller does not remove a known prepared candidate when a later
        # boundary check fails. It remains explicitly unconfirmed, never active.
        self.assertTrue((self.root / 'releases/changed-current/READY.json').exists())

    def test_archive_ancestor_symlink_is_refused_before_prepared(self):
        archive, checksum = self.stage_archive()
        linked = self.base / 'linked-source'
        linked.symlink_to(self.base, target_is_directory=True)
        for action in ('stage', 'restore'):
            with self.subTest(action=action), self.assertRaises((tools.ReleaseError, OSError)):
                self.session(action, archive=linked / archive.name, sha256=checksum)
        self.assertFalse((self.root / 'releases/staged').exists())

    def test_ordinary_stage_still_works_while_panel_lease_is_held(self):
        archive, checksum = self.stage_archive()
        before = (self.root / 'CURRENT.json').read_bytes()
        with tools.lease(self.root / '.panel.lease'):
            result = subprocess.run([sys.executable, '-B', str(REPO / 'scripts/deploy.py'),
                '--root', str(self.root), 'stage', str(archive), '--sha256', checksum],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.splitlines()[-1], 'staged')
            self.assertTrue(self.busy(self.root / '.panel.lease'))
        self.assertEqual((self.root / 'CURRENT.json').read_bytes(), before)
        self.assertFalse(self.busy(self.root / '.operation.lock'))

    def test_worker_rejects_unknown_fields_duplicate_fields_and_out_of_order(self):
        invalid = ({'protocol': 1, 'action': 'backup', 'path': '/tmp/arbitrary'},
                   {'protocol': True, 'action': 'start'},
                   {'protocol': 1, 'action': 'restore', 'path': '/tmp/a', 'sha256': 'bad'})
        for value in invalid:
            process = self.start_worker()
            self.send(process, value)
            self.assertEqual(self.event(process), {'event': 'error', 'code': 'MAINTENANCE_REFUSED'})
            self.assertEqual(process.wait(timeout=5), 1)
        process = self.start_worker()
        process.stdin.write('{"protocol":1,"protocol":1,"action":"start"}\n');process.stdin.flush()
        self.assertEqual(self.event(process)['event'], 'error')
        self.assertEqual(process.wait(timeout=5), 1)
        process = self.start_worker()
        self.request(process, 'start')
        self.send(process, {'command': 'release_writers'})
        self.assertEqual(self.event(process)['event'], 'error')
        self.assertEqual(process.wait(timeout=5), 1)
        self.assertFalse(self.busy(self.root / '.operation.lock'))

    def test_worker_eof_timeout_and_signal_release_only_owned_locks(self):
        for failure in ('eof', 'timeout', 'signal'):
            with self.subTest(failure=failure):
                process = self.start_worker(timeout=.6 if failure == 'timeout' else 10)
                self.request(process, 'start')
                self.send(process, {'command': 'proceed'})
                self.assertEqual(self.event(process), {'event': 'done', 'ok': True})
                if failure == 'eof': process.stdin.close()
                elif failure == 'signal': process.send_signal(signal.SIGTERM)
                self.assertEqual(self.event(process), {'event': 'error', 'code': 'MAINTENANCE_REFUSED'})
                self.assertEqual(process.wait(timeout=5), 1)
                self.assertFalse(self.busy(self.root / '.operation.lock'))
                self.assert_writers(self.data, False)
                self.assertEqual(json.loads((self.root / 'CURRENT.json').read_text())['release_id'], 'old')

    def test_worker_total_timeout_includes_waiting_for_proceed(self):
        process = self.start_worker(timeout=.6)
        self.request(process, 'backup')
        # The entire transaction shares one deadline; the command cannot reset it.
        self.assertEqual(self.event(process), {'event': 'error', 'code': 'MAINTENANCE_REFUSED'})
        self.assertEqual(process.wait(timeout=5), 1)
        self.assertFalse((self.root / 'backups').exists())

    def test_worker_total_timeout_interrupts_actual_stage_health(self):
        archive, checksum = self.stage_archive(health_delay=3)
        before = (self.root / 'CURRENT.json').read_bytes()
        process = self.start_worker(timeout=.6)
        self.request(process, 'stage', path=str(archive), sha256=checksum)
        started = time.monotonic()
        self.send(process, {'command': 'proceed'})
        self.assertEqual(self.event(process), {'event': 'error', 'code': 'MAINTENANCE_REFUSED'})
        self.assertEqual(process.wait(timeout=5), 1)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual((self.root / 'CURRENT.json').read_bytes(), before)
        self.assertFalse(self.busy(self.root / '.operation.lock'))
        # READY can predate a timed-out health probe; it is never a success event.
        self.assertTrue((self.root / 'releases/staged/READY.json').exists())

    def test_production_entry_rejects_arguments_without_a_bypass(self):
        result = subprocess.run([sys.executable, '-I', '-S', str(self.control / 'ops_worker.py'),
            '--root', str(self.root)], input='', capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.root / '.operation.lock').exists())


if __name__ == '__main__':
    unittest.main()
