"""Temporary data only: offline ownership versus supported source writers."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import os
import sqlite3
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.data_owner import DataOwnerError, data_owner


PROBE = '''
import sys
from pathlib import Path
from app.data_owner import DataOwnerError, data_owner
try:
    with data_owner(Path(sys.argv[1]), exclusive=sys.argv[2] == 'exclusive'):
        pass
except DataOwnerError:
    raise SystemExit(23)
'''


class DataOwnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='vui-data-owner-')
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.data = self.parent / 'data'
        self.lease = self.parent / '.data.owner.lease'

    def probe(self, *, exclusive=True):
        return subprocess.run([sys.executable, '-c', PROBE, str(self.data),
                               'exclusive' if exclusive else 'shared'],
                              capture_output=True, text=True, timeout=10).returncode

    def other_thread(self, *, exclusive=False):
        def attempt():
            with data_owner(self.data, exclusive=exclusive):
                return True
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(attempt).result(timeout=5)

    def test_lease_is_private_sibling_and_never_creates_data(self):
        with data_owner(self.data):
            self.assertFalse(self.data.exists())
            self.assertEqual(stat.S_IMODE(self.lease.stat().st_mode), 0o600)
            self.assertEqual(self.lease.stat().st_nlink, 1)
        inode = self.lease.stat().st_ino
        with data_owner(self.data, exclusive=True):
            pass
        self.assertEqual(self.lease.stat().st_ino, inode)

    def test_existing_data_tree_is_unchanged(self):
        self.data.mkdir(mode=0o700)
        (self.data / 'sentinel').write_bytes(b'unchanged')
        before = self.data.stat().st_mtime_ns
        with data_owner(self.data, exclusive=True):
            self.assertEqual(list(self.data.iterdir()), [self.data / 'sentinel'])
        self.assertEqual(self.data.stat().st_mtime_ns, before)
        self.assertEqual((self.data / 'sentinel').read_bytes(), b'unchanged')

    def test_shared_writers_coexist_but_exclusive_refuses(self):
        with data_owner(self.data):
            with data_owner(self.data):
                self.assertEqual(self.probe(exclusive=False), 0)
                self.assertEqual(self.probe(), 23)
                self.assertTrue(self.other_thread())
                with self.assertRaises(DataOwnerError):
                    self.other_thread(exclusive=True)
        self.assertEqual(self.probe(), 0)

    def test_exclusive_only_allows_same_thread_shared_validation(self):
        with data_owner(self.data, exclusive=True):
            with data_owner(self.data):
                with data_owner(self.data):
                    self.assertEqual(self.probe(exclusive=False), 23)
                    self.assertEqual(self.probe(), 23)
            with self.assertRaises(DataOwnerError):
                self.other_thread()
            with self.assertRaises(DataOwnerError):
                with data_owner(self.data, exclusive=True):
                    pass
        self.assertTrue(self.other_thread())

    @unittest.skipUnless(hasattr(os, 'fork'), 'Linux ownership uses flock')
    def test_forked_child_cannot_borrow_parent_exclusive(self):
        with data_owner(self.data, exclusive=True):
            pid = os.fork()
            if pid == 0:
                try:
                    with data_owner(self.data):
                        pass
                except DataOwnerError:
                    os._exit(23)
                except BaseException:
                    os._exit(24)
                os._exit(0)
            _, status = os.waitpid(pid, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 23)
            self.assertEqual(self.probe(), 23)

    def test_directory_swap_keeps_stable_ownership(self):
        self.data.mkdir(mode=0o700)
        with data_owner(self.data, exclusive=True):
            self.data.rename(self.parent / 'old-data')
            self.data.mkdir(mode=0o700)
            with data_owner(self.data):
                pass
            self.assertEqual(self.probe(exclusive=False), 23)
        self.assertEqual(self.probe(), 0)

    def test_body_error_propagates_and_releases_lease(self):
        error = OSError('original writer failure')
        with self.assertRaises(OSError) as result:
            with data_owner(self.data):
                raise error
        self.assertIs(result.exception, error)
        self.assertEqual(self.probe(), 0)

    def test_shared_scope_can_close_on_shutdown_thread(self):
        owner = data_owner(self.data)
        owner.__enter__()
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(owner.__exit__, None, None, None).result(timeout=5)
        self.assertEqual(self.probe(), 0)

    def test_borrowed_scope_retains_exclusive_lock_after_outer_scope_exits(self):
        with data_owner(self.data, exclusive=True):
            borrower = data_owner(self.data)
            borrower.__enter__()
        try:
            with self.assertRaises(DataOwnerError):
                self.other_thread(exclusive=True)
            self.assertEqual(self.probe(), 23)
            self.assertEqual(self.probe(exclusive=False), 23)
        finally:
            # Shutdown may close a live shared writer on a different thread.
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(borrower.__exit__, None, None, None).result(timeout=5)
        self.assertTrue(self.other_thread(exclusive=True))
        self.assertEqual(self.probe(), 0)

    def test_sqlite_borrow_retains_lock_until_connection_closes(self):
        from app.models import database
        with patch.object(database, 'DATA_DIR', self.data), \
             patch.object(database, 'DB_PATH', self.data / 'v-ui.db'):
            with data_owner(self.data, exclusive=True):
                connection = database._connect()
            try:
                with self.assertRaises(DataOwnerError):
                    self.other_thread(exclusive=True)
                self.assertEqual(self.probe(), 23)
                connection.execute('CREATE TABLE retained_writer (value INTEGER)')
                connection.execute('INSERT INTO retained_writer VALUES (1)')
                connection.commit()
                with self.assertRaises(DataOwnerError):
                    self.other_thread(exclusive=True)
            finally:
                connection.close()
        self.assertTrue(self.other_thread(exclusive=True))
        self.assertEqual(self.probe(), 0)

    def test_unsafe_lease_modes_are_not_repaired(self):
        self.lease.touch(mode=0o600)
        for mode in (0o644, 0o660, 0o400, 0o700):
            with self.subTest(mode=oct(mode)):
                self.lease.chmod(mode)
                with self.assertRaises(DataOwnerError):
                    with data_owner(self.data):
                        pass
                self.assertEqual(stat.S_IMODE(self.lease.stat().st_mode), mode)

    def test_nonempty_lease_is_refused_without_truncation(self):
        self.lease.write_bytes(b'not a lease')
        self.lease.chmod(0o600)
        with self.assertRaises(DataOwnerError):
            with data_owner(self.data):
                pass
        self.assertEqual(self.lease.read_bytes(), b'not a lease')

    def test_symlink_and_hardlink_lease_are_refused_without_writes(self):
        target = self.parent / 'target'
        target.write_bytes(b'unchanged')
        target.chmod(0o600)
        self.lease.symlink_to(target)
        with self.assertRaises(DataOwnerError):
            with data_owner(self.data):
                pass
        self.lease.unlink()
        os.link(target, self.lease)
        with self.assertRaises(DataOwnerError):
            with data_owner(self.data):
                pass
        self.assertEqual(target.read_bytes(), b'unchanged')

    def test_fifo_and_directory_lease_are_refused(self):
        os.mkfifo(self.lease, mode=0o600)
        with self.assertRaises(DataOwnerError):
            with data_owner(self.data):
                pass
        self.lease.unlink()
        self.lease.mkdir(mode=0o700)
        with self.assertRaises(DataOwnerError):
            with data_owner(self.data):
                pass

    def test_foreign_lease_owner_is_refused(self):
        self.lease.touch(mode=0o600)
        real_geteuid = os.geteuid()
        with patch('app.data_owner.os.geteuid', return_value=real_geteuid + 1001):
            with self.assertRaises(DataOwnerError):
                with data_owner(self.data):
                    pass

    def test_symlink_data_or_ancestor_and_writable_parent_are_refused(self):
        actual = self.parent / 'actual'
        actual.mkdir(mode=0o700)
        self.data.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(DataOwnerError):
            with data_owner(self.data):
                pass
        with self.assertRaises(DataOwnerError):
            with data_owner(self.data / 'child'):
                pass
        self.data.unlink()
        self.parent.chmod(0o777)
        try:
            with self.assertRaises(DataOwnerError):
                with data_owner(self.data):
                    pass
        finally:
            self.parent.chmod(0o700)
        self.assertEqual(list(actual.iterdir()), [])
        self.assertFalse(self.lease.exists())

    def test_missing_parent_is_not_bootstrapped(self):
        with self.assertRaises(DataOwnerError):
            with data_owner(self.parent / 'missing' / 'data'):
                pass
        self.assertFalse((self.parent / 'missing').exists())

    def test_lease_replacement_is_detected_on_exit_and_nested_entry(self):
        with self.assertRaises(DataOwnerError):
            with data_owner(self.data, exclusive=True):
                self.lease.rename(self.parent / 'old-lease')
                self.lease.touch(mode=0o600)
                with self.assertRaises(DataOwnerError):
                    with data_owner(self.data):
                        pass
        self.assertEqual(self.probe(), 0)

    def test_parent_replacement_is_detected_on_exit(self):
        inner = self.parent / 'private'
        inner.mkdir(mode=0o700)
        with self.assertRaises(DataOwnerError):
            with data_owner(inner / 'data'):
                inner.rename(self.parent / 'old-private')
                inner.mkdir(mode=0o700)

    def test_proxy_store_blocks_before_initialization(self):
        from app.services.proxy_store import ProxyStore, ProxyStorageError
        with data_owner(self.data, exclusive=True):
            with ThreadPoolExecutor(max_workers=1) as executor:
                with self.assertRaises(ProxyStorageError):
                    executor.submit(ProxyStore(self.data).read_snapshot).result(timeout=5)
            self.assertFalse(self.data.exists())
            # Dedicated offline validation may use the original store inline.
            ProxyStore(self.data).read_snapshot()
        self.assertTrue((self.data / 'proxy-state' / 'state.json').is_file())

    def test_proxy_read_without_initialization_does_not_create_data(self):
        from app.services.proxy_store import ProxyStore, ProxyStorageError
        with data_owner(self.data, exclusive=True):
            with self.assertRaises(ProxyStorageError):
                ProxyStore(self.data).read_snapshot(initialize=False)
            self.assertFalse(self.data.exists())

    def test_borrowed_lease_rejects_replaced_data_symlink(self):
        self.data.mkdir(mode=0o700)
        target = self.parent / 'other'
        target.mkdir(mode=0o700)
        with data_owner(self.data, exclusive=True):
            self.data.rmdir()
            self.data.symlink_to(target, target_is_directory=True)
            with self.assertRaises(DataOwnerError):
                with data_owner(self.data):
                    pass

    def test_core_lifetime_blocks_offline_until_close(self):
        from app.services.core_runtime import CoreRuntime
        runtime = CoreRuntime('sing-box', self.parent / 'unused', self.data / 'runtime' / 'sing-box')
        self.addCleanup(runtime.close)
        runtime._claim()
        self.assertEqual(self.probe(), 23)
        runtime.close()
        self.assertEqual(self.probe(), 0)

    def test_core_claim_refuses_before_creating_runtime(self):
        from app.services.core_runtime import CoreError, CoreRuntime
        runtime = CoreRuntime('sing-box', self.parent / 'unused', self.data / 'runtime' / 'sing-box')
        self.addCleanup(runtime.close)
        with data_owner(self.data, exclusive=True):
            with ThreadPoolExecutor(max_workers=1) as executor:
                with self.assertRaises(CoreError):
                    executor.submit(runtime._claim).result(timeout=5)
        self.assertFalse(self.data.exists())

    def test_core_claim_failure_releases_data_lease(self):
        from app.services.core_runtime import CoreError, CoreRuntime
        runtime = CoreRuntime('sing-box', self.parent / 'unused', self.data / 'runtime' / 'sing-box')
        with patch.object(runtime, '_claim_core', side_effect=CoreError('fake failure')):
            with self.assertRaises(CoreError):
                runtime._claim()
        self.assertEqual(self.probe(), 0)

    def script(self, source):
        return subprocess.run([sys.executable, '-c', source],
                              env={**os.environ, 'VUI_DATA_DIR': str(self.data)},
                              text=True, capture_output=True, timeout=20)

    def test_source_imports_do_not_create_data(self):
        result = self.script('import main; import app.services.core_manager; import app.services.mihomo_routing')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.data.exists())
        self.assertFalse(self.lease.exists())

    def test_database_connection_blocks_offline_until_closed(self):
        script = '''
from pathlib import Path
from app.models import database
from sqlalchemy import text
database.init_db()
connection = database.engine.connect()
connection.execute(text('CREATE TABLE owner_test (id INTEGER)'))
print('ready', flush=True)
input()
connection.close()
print('closed', flush=True)
input()
with database.engine.connect() as reopened:
    assert reopened.execute(text('SELECT value FROM restored_marker')).scalar() == 'new-tree'
'''
        child = subprocess.Popen([sys.executable, '-c', script],
                                 env={**os.environ, 'VUI_DATA_DIR': str(self.data)},
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            self.assertEqual(self.probe(), 23)
            child.stdin.write('\n'); child.stdin.flush()
            self.assertEqual(child.stdout.readline().strip(), 'closed')
            self.assertEqual(self.probe(), 0)
            with data_owner(self.data, exclusive=True):
                self.data.rename(self.parent / 'previous-data')
                self.data.mkdir(mode=0o700)
                fresh = sqlite3.connect(self.data / 'v-ui.db')
                try:
                    fresh.execute('CREATE TABLE restored_marker (value TEXT)')
                    fresh.execute("INSERT INTO restored_marker VALUES ('new-tree')")
                    fresh.commit()
                finally:
                    fresh.close()
            child.stdin.write('\n'); child.stdin.flush()
            self.assertEqual(child.wait(timeout=10), 0, child.stderr.read())
        finally:
            if child.poll() is None:
                child.kill(); child.wait(timeout=10)
            child.stdin.close(); child.stdout.close(); child.stderr.close()

    def test_database_standalone_write_refuses_before_creating_data(self):
        with data_owner(self.data, exclusive=True):
            result = self.script('from app.models.database import init_db; init_db()')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('DataOwnerError', result.stderr)
            self.assertFalse(self.data.exists())

    def test_admin_provision_refuses_offline_lease_before_writes(self):
        with data_owner(self.data, exclusive=True):
            result = self.script("from app.services.auth_service import provision_admin; "
                                 "provision_admin('owner-test', 'A-fake-offline-test-password!')")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('DataOwnerError', result.stderr)
            self.assertFalse(self.data.exists())

    def test_source_startup_holds_owner_without_any_core_state(self):
        script = '''
import asyncio
from pathlib import Path
import main
asyncio.run(main.startup_event())
assert not (main.DB_PATH.parent / 'runtime').exists()
print('ready', flush=True)
input()
asyncio.run(main.shutdown_event())
'''
        child = subprocess.Popen([sys.executable, '-c', script],
                                 env={**os.environ, 'VUI_DATA_DIR': str(self.data)},
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            self.assertEqual(self.probe(), 23)
            child.stdin.write('\n'); child.stdin.flush()
            self.assertEqual(child.wait(timeout=10), 0, child.stderr.read())
            self.assertEqual(self.probe(), 0)
        finally:
            if child.poll() is None:
                child.kill(); child.wait(timeout=10)
            child.stdin.close(); child.stdout.close(); child.stderr.close()

    def test_source_startup_failure_releases_owner_and_closes_writers(self):
        import main
        manager = Mock()
        with patch.object(main, 'DB_PATH', self.data / 'v-ui.db'), \
             patch.object(main, 'init_db', side_effect=RuntimeError('fake startup failure')), \
             patch.object(main, 'core_manager', manager), \
             patch.object(main.certificates, 'get_manager', return_value=Mock()):
            with self.assertRaisesRegex(RuntimeError, 'fake startup failure'):
                asyncio.run(main.startup_event())
        manager.stop_all.assert_called_once()
        self.assertEqual(self.probe(), 0)

    def test_source_startup_under_offline_lease_never_initializes(self):
        with data_owner(self.data, exclusive=True):
            result = self.script('import asyncio, main; asyncio.run(main.startup_event())')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('DataOwnerError', result.stderr)
            self.assertFalse(self.data.exists())

    def test_serve_preinitialization_refuses_offline_lease(self):
        self.data.mkdir(mode=0o700)
        with data_owner(self.data, exclusive=True):
            result = self.script(f'''import sys
from app.serve import run
sys.argv = ['serve', '--root', {str(self.parent)!r}, '--origin', 'https://panel.example.test:8443']
run()
''')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('DataOwnerError', result.stderr)
            self.assertEqual(list(self.data.iterdir()), [])

    def test_routing_and_certificate_writers_refuse_before_mkdir(self):
        from app.services import routing_store, mihomo_routing
        from app.certificates.manager import CertificateManager
        certificate = CertificateManager(self.data / 'certificates')
        with data_owner(self.data, exclusive=True), \
             patch.object(routing_store, 'ROUTING_FILE', self.data / 'mihomo-routing.json'), \
             patch.object(mihomo_routing, 'ROUTING_FILE', self.data / 'mihomo-routing.json'):
            with ThreadPoolExecutor(max_workers=1) as executor:
                with self.assertRaises(routing_store.RoutingStorageError):
                    executor.submit(routing_store.read_snapshot).result(timeout=5)
                with self.assertRaises(DataOwnerError):
                    executor.submit(certificate.process_once).result(timeout=5)
                with self.assertRaises(DataOwnerError):
                    executor.submit(mihomo_routing.save_routing, {}).result(timeout=5)
            self.assertFalse(self.data.exists())
    def test_parent_traversal_is_rejected_before_path_normalization(self):
        for value in (self.parent / 'child' / '..' / 'data', Path('..') / 'data'):
            with self.subTest(path=str(value)):
                with self.assertRaises(DataOwnerError):
                    with data_owner(value):
                        pass
        self.assertFalse(self.data.exists())
        self.assertFalse(self.lease.exists())

    def test_sqlite_symlink_parent_traversal_cannot_lease_different_tree(self):
        alias_parent = self.parent / 'alias'
        actual_parent = self.parent / 'actual'
        alias_parent.mkdir(mode=0o700)
        actual_parent.mkdir(mode=0o700)
        (actual_parent / 'nested').mkdir(mode=0o700)
        (alias_parent / 'link').symlink_to(actual_parent / 'nested', target_is_directory=True)
        target = actual_parent / 'data'
        alias = alias_parent / 'link' / '..' / 'data'
        script = ("from app.models.database import _connect; connection = _connect(); "
                  "connection.execute('CREATE TABLE escaped (value INTEGER)'); "
                  "connection.commit(); connection.close()")
        with data_owner(target, exclusive=True):
            result = subprocess.run([sys.executable, '-c', script],
                                    env={**os.environ, 'VUI_DATA_DIR': str(alias)},
                                    capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('DataOwnerError', result.stderr)
        self.assertFalse(target.exists())
        self.assertFalse((alias_parent / 'data').exists())
        self.assertFalse((alias_parent / '.data.owner.lease').exists())

    def test_pending_journal_blocks_shared_but_allows_exclusive_validation(self):
        journal = self.parent / 'RESTORE_PENDING.json'
        journal.write_bytes(b'{invalid journal')
        with self.assertRaises(DataOwnerError):
            with data_owner(self.data):
                self.fail('shared writer passed pending journal')
        with data_owner(self.data, exclusive=True):
            with data_owner(self.data):
                pass
            with self.assertRaises(DataOwnerError):
                self.other_thread()
        self.assertEqual(self.probe(), 0)
        self.assertEqual(self.probe(exclusive=False), 23)
        self.assertFalse(self.data.exists())
        self.assertEqual(journal.read_bytes(), b'{invalid journal')

    def test_any_pending_journal_path_blocks_without_following_or_reading(self):
        journal = self.parent / 'RESTORE_PENDING.json'
        cases = (
            lambda: journal.symlink_to(self.parent / 'absent-target'),
            lambda: journal.mkdir(mode=0o700),
            lambda: os.mkfifo(journal, mode=0o600),
            lambda: journal.write_bytes(b''),
        )
        for create in cases:
            create()
            try:
                with self.assertRaises(DataOwnerError):
                    with data_owner(self.data):
                        pass
                self.assertFalse(self.data.exists())
            finally:
                if journal.is_dir() and not journal.is_symlink():
                    journal.rmdir()
                else:
                    journal.unlink()

    def test_pending_journal_is_checked_after_acquiring_shared_flock(self):
        journal = self.parent / 'RESTORE_PENDING.json'
        original = fcntl.flock
        def lock_and_leave_journal(fd, operation):
            original(fd, operation)
            if operation & fcntl.LOCK_SH:
                journal.write_bytes(b'pending after preliminary checks')
        with patch('app.data_owner.fcntl.flock', side_effect=lock_and_leave_journal):
            with self.assertRaises(DataOwnerError):
                with data_owner(self.data):
                    pass
        self.assertFalse(self.data.exists())
        self.assertEqual(self.probe(), 0)

    def assert_pending_writers_blocked(self, *, new_tree):
        from app.services.proxy_store import ProxyStore, ProxyStorageError
        from app.services.core_runtime import CoreRuntime, CoreError
        from app.certificates.manager import CertificateManager
        from app.services import routing_store, mihomo_routing
        self.data.mkdir(mode=0o700)
        (self.data / 'original').write_bytes(b'preserve original tree')
        self.data.rename(self.parent / 'previous-data')
        if new_tree:
            self.data.mkdir(mode=0o700)
            (self.data / 'candidate').write_bytes(b'preserve selected tree')
        journal = self.parent / 'RESTORE_PENDING.json'
        journal.write_bytes(b'{"interrupted": "new-renamed"}' if new_tree else b'{"interrupted": "old-renamed"}')
        before = {str(path.relative_to(self.parent)): path.read_bytes()
                  for path in self.parent.rglob('*') if path.is_file()}
        scripts = (
            'import asyncio, main; asyncio.run(main.startup_event())',
            'from app.models.database import init_db; init_db()',
            'from app.models.database import engine; engine.connect()',
            "from app.services.auth_service import provision_admin; "
            "provision_admin('owner-test', 'A-fake-offline-test-password!')",
        )
        for source in scripts:
            with self.subTest(writer=source, new_tree=new_tree):
                result = self.script(source)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('DataOwnerError', result.stderr)
                self.assertIn('Interrupted restore', result.stderr)
        with self.assertRaises(ProxyStorageError):
            ProxyStore(self.data).read_snapshot()
        runtime = CoreRuntime('sing-box', self.parent / 'unused', self.data / 'runtime' / 'sing-box')
        try:
            with self.assertRaises(CoreError):
                runtime._claim()
        finally:
            runtime.close()
        with self.assertRaises(DataOwnerError):
            CertificateManager(self.data / 'certificates').process_once()
        with patch.object(routing_store, 'ROUTING_FILE', self.data / 'mihomo-routing.json'), \
             patch.object(mihomo_routing, 'ROUTING_FILE', self.data / 'mihomo-routing.json'):
            with self.assertRaises(routing_store.RoutingStorageError):
                routing_store.read_snapshot()
            with self.assertRaises(DataOwnerError):
                mihomo_routing.save_routing({})
        after = {str(path.relative_to(self.parent)): path.read_bytes()
                 for path in self.parent.rglob('*') if path.is_file() and path != self.lease}
        self.assertEqual(before, after)
        if not new_tree:
            self.assertFalse(self.data.exists())
        else:
            self.assertEqual(list(self.data.iterdir()), [self.data / 'candidate'])
        self.assertFalse((self.data / 'proxy-state').exists())
        self.assertFalse((self.data / 'v-ui.db').exists())
        self.assertEqual(self.probe(), 0)  # No process is holding a live lease.
        self.assertEqual(self.probe(exclusive=False), 23)

    def test_all_source_writers_refuse_durable_old_rename_journal(self):
        self.assert_pending_writers_blocked(new_tree=False)

    def test_all_source_writers_refuse_durable_new_rename_journal(self):
        self.assert_pending_writers_blocked(new_tree=True)

    def test_certificate_writer_operation_owns_its_existing_worker_lease(self):
        from app import release_tools
        from app.models import database
        from app.certificates.manager import CertificateManager
        self.data.mkdir(mode=0o700)
        certificates = self.data / 'certificates'
        certificates.mkdir(mode=0o700)
        worker = certificates / '.worker.lock'
        worker.touch(mode=0o600)
        manager = CertificateManager(certificates)
        database_session = Mock()
        database_session.query.return_value.filter_by.return_value.all.return_value = []
        database_session.query.return_value.filter_by.return_value.order_by.return_value.first.return_value = None
        entered = []
        @contextmanager
        def session():
            # Mirror the shared ownership of a real standalone DB connection.
            with data_owner(self.data):
                entered.append(True)
                yield database_session
        with patch.object(database, 'SessionLocal', side_effect=session), \
             patch.object(manager, '_schedule_due'):
            with release_tools.writer_operation(self.parent):
                self.assertFalse(manager.process_once())  # No synthetic jobs.
            self.assertEqual(entered, [True])
            self.assertEqual(str(database_session.execute.call_args.args[0]), 'BEGIN IMMEDIATE')
            database_session.commit.assert_called_once()
            self.assertTrue(worker.is_file())
            # A running source panel keeps its shared data lease even when it
            # has no core state. The installed panel also has its panel lease.
            with data_owner(self.data):
                with self.assertRaises((release_tools.ReleaseError, DataOwnerError)):
                    with release_tools.writer_operation(self.parent):
                        manager.process_once()
            with release_tools.lease(self.parent / '.panel.lease'):
                with self.assertRaises((release_tools.ReleaseError, DataOwnerError)):
                    with release_tools.writer_operation(self.parent):
                        manager.process_once()
            (self.parent / 'RESTORE_PENDING.json').write_bytes(b'interrupted restore')
            with self.assertRaises((release_tools.ReleaseError, DataOwnerError)):
                with release_tools.writer_operation(self.parent):
                    manager.process_once()
            self.assertEqual(entered, [True])
        self.assertFalse((self.data / 'v-ui.db').exists())

    def test_serve_rechecks_journal_created_after_early_precheck(self):
        from app import serve
        self.data.mkdir(mode=0o700)
        real_lease = serve.lease
        @contextmanager
        def crash_after_precheck(path):
            with real_lease(path):
                (self.parent / 'RESTORE_PENDING.json').write_bytes(b'interrupted restore')
                yield
        argv = ['serve', '--root', str(self.parent), '--origin', 'https://panel.example.test:8443']
        with patch.object(sys, 'argv', argv), patch.dict(os.environ), \
             patch.object(serve, 'lease', side_effect=crash_after_precheck), \
             patch.object(serve, 'active') as active:
            with self.assertRaises(DataOwnerError):
                serve.run()
            active.assert_not_called()
        self.assertEqual(list(self.data.iterdir()), [])



if __name__ == '__main__':
    unittest.main()
