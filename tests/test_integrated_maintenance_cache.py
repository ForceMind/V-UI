"""Managed transactions consume the private streaming/cache paths correctly.

All inputs are known synthetic non-root fixtures. Assertions concern real file
descriptors, SQLite, archives and flocks, not kernel residency or a memory bound.
No service manager, privileged installer, real core or external source is used.
"""
from contextlib import ExitStack
import hashlib
import importlib.util
import os
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch
import zipfile

from app import release_tools as tools


REPO = Path(__file__).resolve().parents[1]


def _load_fixture(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_fixtures = _load_fixture('_integrated_maintenance_fixtures', REPO / 'tests/test_maintenance_worker.py')
_worker = _load_fixture('_integrated_maintenance_worker', REPO / 'deploy/ops_worker.py')


@unittest.skipUnless(sys.platform.startswith('linux') and os.geteuid() != 0
    and hasattr(os, 'posix_fadvise') and hasattr(os, 'POSIX_FADV_DONTNEED')
    and Path('/proc/self/fd').is_dir(), 'Requires non-root Linux fd/advice fixtures')
class IntegratedMaintenanceCacheTests(unittest.TestCase):
    def fixture(self):
        # Reuse the established fake SQLite/runtime/config boundary without
        # inheriting its TestCase (which would rerun its whole suite here).
        fixture = _fixtures.MaintenanceWorkerTests()
        try:
            fixture.setUp()
        except BaseException:
            fixture.doCleanups()
            raise
        self.addCleanup(fixture.doCleanups)
        return fixture

    @staticmethod
    def inode(path):
        info = path.stat()
        return info.st_dev, info.st_ino

    @staticmethod
    def capture_advice():
        events = []
        original = os.posix_fadvise
        def record(fd, offset, size, advice):
            info = os.fstat(fd)
            events.append((Path(os.readlink(f'/proc/self/fd/{fd}')),
                           (info.st_dev, info.st_ino), offset, size, advice))
            # Preserve the real syscall; the product owns unsupported-advice
            # fallback. Observing a call does not claim that pages were evicted.
            return original(fd, offset, size, advice)
        return events, patch.object(os, 'posix_fadvise', side_effect=record)

    def add_log(self, fixture):
        content = b'known synthetic maintenance log\n' * 350000 + b'tail'
        self.assertGreater(len(content), 8 * 1024 * 1024)
        path = fixture.data / 'large.log'
        path.write_bytes(content)
        path.chmod(0o600)
        return path, content

    def test_managed_stage_cold_reads_new_private_tar_with_live_writer_leases(self):
        fixture = self.fixture()
        archive, checksum = fixture.stage_archive(require_eof=True)
        source_inode = self.inode(archive)
        current = (fixture.root / 'CURRENT.json').read_bytes()
        old_runtime = tools.runtime_tree_digest(fixture.root / 'releases/old/runtime')
        events, capture = self.capture_advice()
        with ExitStack() as held:
            for path in (fixture.root / '.panel.lease',
                         *(fixture.data / name for name in _fixtures.LOCKS)):
                held.enter_context(tools.lease(path))
            with capture:
                session = fixture.session('stage', archive=archive, sha256=checksum)
                with _worker._quiet_candidate_io():
                    result = session.execute()
            self.assertEqual(result, {'prepared_release': 'staged'})
            prepared = fixture.root / 'releases/staged'
            private_tar = prepared / 'payload/runtimes' / (tools.target_key() + '.tar.gz')
            private_inode = self.inode(private_tar)
            self.assertTrue(any(inode == private_inode for _, inode, *_ in events))
            self.assertNotIn(source_inode, [inode for _, inode, *_ in events])
            self.assertFalse(any(path.is_relative_to(fixture.data) for path, *_ in events))
            self.assertEqual((fixture.root / 'CURRENT.json').read_bytes(), current)
            self.assertEqual(tools.runtime_tree_digest(fixture.root / 'releases/old/runtime'), old_runtime)
            self.assertEqual(tools.verify_runtime(prepared)['archive_sha256'], checksum)
            tools.verify_payload(prepared / 'payload')
            self.assertTrue(fixture.busy(fixture.root / '.operation.lock'))
            self.assertTrue(fixture.busy(fixture.root / '.panel.lease'))
            fixture.assert_writers(fixture.data, True)
            session.close()
            self.assertFalse(fixture.busy(fixture.root / '.operation.lock'))
            fixture.assert_writers(fixture.data, True)

    def test_managed_backup_advises_only_private_copies_and_completed_archive(self):
        fixture = self.fixture()
        log, content = self.add_log(fixture)
        source_inode = self.inode(log)
        database = fixture.data / 'v-ui.db'
        database_inode, original_database = self.inode(database), database.read_bytes()
        current = (fixture.root / 'CURRENT.json').read_bytes()
        events, capture = self.capture_advice()
        with capture:
            session = fixture.session('backup')
            result = session.execute()
        archive = fixture.root / 'backups' / result['backup_name']
        self.assertEqual(archive.stat().st_nlink, 1)
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), result['backup_sha256'])
        self.assertNotIn(source_inode, [inode for _, inode, *_ in events])
        self.assertNotIn(database_inode, [inode for _, inode, *_ in events])
        self.assertFalse(any(path.name == 'v-ui.db' for path, *_ in events))
        copy_events = [path for path, *_ in events if path.name == 'large.log']
        self.assertTrue(copy_events)
        self.assertTrue(all(path.is_relative_to(fixture.root)
            and path.relative_to(fixture.root).parts[0].startswith('.backup-') for path in copy_events))
        archive_events = [(path, inode) for path, inode, *_ in events if path.name == 'archive.zip']
        self.assertTrue(archive_events)
        self.assertTrue(all(path.parent.name.startswith('.backup-archive-')
            and inode == self.inode(archive) for path, inode in archive_events))
        with zipfile.ZipFile(archive) as saved:
            self.assertIsNone(saved.testzip())
            self.assertEqual(saved.read('large.log'), content)
        self.assertEqual(log.read_bytes(), content)
        self.assertEqual(database.read_bytes(), original_database)
        self.assertEqual((fixture.root / 'CURRENT.json').read_bytes(), current)
        fixture.assert_writers(fixture.data, True)
        self.assertTrue(fixture.busy(fixture.root / '.operation.lock'))
        session.release_writers()
        self.assertTrue(fixture.busy(fixture.root / '.operation.lock'))
        session.close()

    def test_managed_restore_cools_snapshot_not_source_or_either_data_generation(self):
        fixture = self.fixture()
        log, content = self.add_log(fixture)
        backup = fixture.session('backup')
        result = backup.execute()
        backup.release_writers(); backup.close()
        archive = fixture.root / 'backups' / result['backup_name']
        source_inode = self.inode(archive)
        current = (fixture.root / 'CURRENT.json').read_bytes()
        log.write_bytes(b'changed after the known backup')
        events, capture = self.capture_advice()
        with capture:
            session = fixture.session('restore', archive=archive, sha256=result['backup_sha256'])
            self.assertEqual(session.execute(), {})
        previous = list((fixture.root / 'recovery').glob('before-*'))
        self.assertEqual(len(previous), 1)
        fixture.assert_writers(previous[0], True)
        fixture.assert_writers(fixture.data, True)
        self.assertNotIn(source_inode, [inode for _, inode, *_ in events])
        self.assertFalse(any(path.is_relative_to(fixture.data)
            or path.is_relative_to(previous[0]) for path, *_ in events))
        self.assertFalse(any(path.name == 'v-ui.db' for path, *_ in events))
        self.assertTrue(any(path.is_relative_to(fixture.root)
            and path.relative_to(fixture.root).parts[0].startswith('.restore-') for path, *_ in events))
        self.assertEqual(log.read_bytes(), content)
        self.assertEqual((previous[0] / 'large.log').read_bytes(), b'changed after the known backup')
        with sqlite3.connect(fixture.data / 'v-ui.db') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM admin_sessions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT revoked FROM subscription_grants').fetchone()[0], 1)
        self.assertFalse((fixture.root / 'RESTORE_PENDING.json').exists())
        self.assertEqual((fixture.root / 'CURRENT.json').read_bytes(), current)
        session.release_writers()
        fixture.assert_writers(previous[0], False)
        fixture.assert_writers(fixture.data, False)
        self.assertTrue(fixture.busy(fixture.root / '.operation.lock'))
        session.close()

    def test_managed_activate_preserves_current_on_backup_writeback_or_publish_sync_failure(self):
        for failure in ('copy-fsync', 'published-directory-fsync'):
            with self.subTest(failure=failure):
                fixture = self.fixture()
                fixture.prepared('new')
                log, content = self.add_log(fixture)
                current = (fixture.root / 'CURRENT.json').read_bytes()
                session = fixture.session('activate', identity='new')
                original_fsync, original_directory_sync = os.fsync, tools.sync_directory
                def fsync(fd):
                    path = os.readlink(f'/proc/self/fd/{fd}')
                    if '/.backup-' in path and path.endswith('/large.log'):
                        raise OSError('injected private-copy writeback')
                    return original_fsync(fd)
                def sync_directory(path):
                    if Path(path) == fixture.root / 'backups':
                        raise OSError('injected published directory sync')
                    return original_directory_sync(path)
                injected = (patch.object(os, 'fsync', side_effect=fsync) if failure == 'copy-fsync'
                            else patch.object(tools, 'sync_directory', side_effect=sync_directory))
                with injected, self.assertRaisesRegex(OSError, 'injected'):
                    session.execute()
                self.assertEqual((fixture.root / 'CURRENT.json').read_bytes(), current)
                self.assertEqual(log.read_bytes(), content)
                self.assertTrue((fixture.root / 'releases/new').is_dir())
                fixture.assert_writers(fixture.data, True)
                self.assertTrue(fixture.busy(fixture.root / '.operation.lock'))
                with self.assertRaises(tools.ReleaseError):
                    session.release_writers()
                archives = list((fixture.root / 'backups').glob('maintenance-*.zip'))
                if failure == 'copy-fsync':
                    self.assertEqual(archives, [])
                else:
                    self.assertEqual(len(archives), 1)
                    self.assertEqual(archives[0].stat().st_nlink, 1)
                    with zipfile.ZipFile(archives[0]) as saved:
                        self.assertIsNone(saved.testzip())
                        self.assertEqual(saved.read('large.log'), content)
                session.close()
                self.assertFalse(fixture.busy(fixture.root / '.operation.lock'))


if __name__ == '__main__':
    unittest.main()
