"""Backup cache hints may touch only owned copies, never live data or inputs."""
import hashlib
import os
import random
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app import release_tools as tools


class BackupPrivateCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vui-backup-cache-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.data.mkdir(mode=0o700)
        with sqlite3.connect(self.data / "v-ui.db") as db:
            db.executescript("CREATE TABLE users(id INTEGER); CREATE TABLE inbounds(remark TEXT);"
                "CREATE TABLE admin_sessions(token_hash TEXT); CREATE TABLE subscription_grants(revoked INTEGER);"
                "INSERT INTO inbounds VALUES('original'); INSERT INTO admin_sessions VALUES('fake');"
                "INSERT INTO subscription_grants VALUES(0);")
        self.source = self.data / "large.log"
        self.content = b"synthetic log\n" * 700000 + b"tail"
        self.source.write_bytes(self.content)
        self.archive = self.root / "backup.zip"

    def assert_intact(self):
        self.assertEqual(self.source.read_bytes(), self.content)
        self.assertFalse(list(self.root.glob(".backup-*")))
        with tools.stopped(self.root):
            pass

    def test_hints_bind_only_owned_copy_and_zip_not_database_or_source(self):
        observed = []
        def hint(fd, offset, size, advice):
            observed.append((Path(os.readlink(f"/proc/self/fd/{fd}")), os.fstat(fd).st_ino))
        with patch.object(os, "posix_fadvise", side_effect=hint):
            sha = tools.backup(self.root, self.archive)
        self.assertEqual(sha, hashlib.sha256(self.archive.read_bytes()).hexdigest())
        self.assertTrue(observed)
        self.assertNotIn(self.source.stat().st_ino, [inode for _, inode in observed])
        copies = {path for path, _ in observed if path.name == "large.log"}
        self.assertTrue(copies)
        self.assertTrue(all(path.name == "large.log" and path.is_relative_to(self.root)
                            and path.relative_to(self.root).parts[0].startswith(".backup-") for path in copies))
        archives = [(path, inode) for path, inode in observed if path.name == "archive.zip"]
        self.assertTrue(archives)
        self.assertTrue(all(path.parent.name.startswith(".backup-archive-") and inode == self.archive.stat().st_ino
                            for path, inode in archives))
        self.assertEqual(len(observed), sum(path in copies or path.name == 'archive.zip' for path, _ in observed))
        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual(archive.read("large.log"), self.content)
            self.assertIsNone(archive.testzip())
        self.assert_intact()

    def test_generic_backup_metadata_does_not_enable_hints(self):
        with patch.object(os, "posix_fadvise") as hint:
            tools.create_archive(self.data, self.archive, {"kind": "backup"})
        hint.assert_not_called()

    def test_zip_close_precedes_final_writeback_and_digest(self):
        events = []
        original_close = zipfile.ZipFile.close
        original_finish = tools._PrivateArchiveFile.finish_writes
        def close(archive):
            writing = archive.fp is not None and archive.mode == "w"
            result = original_close(archive)
            if writing: events.append("zip_closed")
            return result
        def finish(handle):
            if Path(os.readlink(f"/proc/self/fd/{handle.fileno()}")).name == "archive.zip":
                events.append("zip_finished")
            return original_finish(handle)
        with patch.object(zipfile.ZipFile, "close", close), patch.object(tools._PrivateArchiveFile, "finish_writes", finish):
            sha = tools.backup(self.root, self.archive)
        self.assertLess(events.index("zip_closed"), len(events) - 1)
        self.assertEqual(events[-1], "zip_finished")
        self.assertEqual(sha, hashlib.sha256(self.archive.read_bytes()).hexdigest())

    def test_unavailable_or_rejected_advice_keeps_backup_restore_contract(self):
        for advice in (None, lambda *args: (_ for _ in ()).throw(OSError("unsupported"))):
            with self.subTest(advice=advice), patch.object(os, "posix_fadvise", advice):
                sha = tools.backup(self.root, self.archive)
                with self.assertRaises(tools.ReleaseError): tools.restore(self.root, self.archive, "0" * 64)
                self.assert_intact()
                tools.restore(self.root, self.archive, sha)
                with sqlite3.connect(self.data / "v-ui.db") as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM admin_sessions").fetchone()[0], 0)
                    self.assertEqual(db.execute("SELECT revoked FROM subscription_grants").fetchone()[0], 1)
                self.assertEqual(self.source.read_bytes(), self.content)
                self.archive.unlink()

    def test_copy_writeback_failure_preserves_original_and_releases_locks(self):
        original = os.fsync
        def sync(fd):
            path = os.readlink(f"/proc/self/fd/{fd}")
            if "/.backup-" in path and path.endswith("large.log"):
                raise OSError("copy writeback failed")
            return original(fd)
        with patch.object(os, "fsync", side_effect=sync):
            with self.assertRaisesRegex(OSError, "copy writeback failed"):
                tools.backup(self.root, self.archive)
        self.assertFalse(self.archive.exists())
        self.assert_intact()

    def test_private_digest_failure_preserves_original(self):
        original = tools.file_digest
        def digest(path, **options):
            if "/.backup-" in str(path) and options.get("cold"):
                raise OSError("snapshot digest read failed")
            return original(path, **options)
        with patch.object(tools, "file_digest", side_effect=digest):
            with self.assertRaisesRegex(OSError, "snapshot digest read failed"):
                tools.backup(self.root, self.archive)
        self.assertFalse(self.archive.exists())
        self.assert_intact()

    def test_compression_failure_removes_only_new_partial_zip(self):
        original = tools.shutil.copyfileobj
        def copy(source, target, length=0):
            if isinstance(source, tools._PrivateArchiveFile):
                raise OSError("snapshot compression read failed")
            return original(source, target, length)
        with patch.object(tools.shutil, "copyfileobj", side_effect=copy):
            with self.assertRaisesRegex(OSError, "snapshot compression read failed"):
                tools.backup(self.root, self.archive)
        self.assertFalse(self.archive.exists())
        self.assert_intact()

    def test_zip_sync_failure_cleans_private_partial_and_preserves_public_writer(self):
        original = os.fsync
        for replace in (False, True):
            with self.subTest(replace=replace):
                def sync(fd):
                    if Path(os.readlink(f"/proc/self/fd/{fd}")).name == "archive.zip":
                        if replace:
                            self.archive.write_bytes(b"external replacement")
                        raise OSError("archive writeback failed")
                    return original(fd)
                with patch.object(os, "fsync", side_effect=sync):
                    with self.assertRaisesRegex(OSError, "archive writeback failed"):
                        tools.backup(self.root, self.archive)
                if replace:
                    self.assertEqual(self.archive.read_bytes(), b"external replacement")
                    self.archive.unlink()
                else:
                    self.assertFalse(self.archive.exists())
                self.assert_intact()

    def test_concurrent_public_destination_is_never_overwritten_or_unlinked(self):
        original = os.link
        def publish(source, destination, **options):
            self.assertEqual(Path(destination), self.archive)
            self.archive.write_bytes(b"concurrent writer")
            return original(source, destination, **options)
        with patch.object(os, "link", side_effect=publish):
            with self.assertRaises(FileExistsError): tools.backup(self.root, self.archive)
        self.assertEqual(self.archive.read_bytes(), b"concurrent writer")
        self.assert_intact()

    def test_published_complete_zip_survives_directory_sync_failure(self):
        with patch.object(tools, "sync_directory", side_effect=OSError("directory sync failed")):
            with self.assertRaisesRegex(OSError, "directory sync failed"):
                tools.backup(self.root, self.archive)
        with zipfile.ZipFile(self.archive) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read("large.log"), self.content)
        self.assert_intact()

    def test_final_digest_failure_never_publishes_partial_archive(self):
        original = tools.stream_digest
        def digest(source, *args, **options):
            if Path(os.readlink(f"/proc/self/fd/{source.fileno()}")).name == "archive.zip":
                raise OSError("final archive digest read failed")
            return original(source, *args, **options)
        with patch.object(tools, "stream_digest", side_effect=digest):
            with self.assertRaisesRegex(OSError, "final archive digest read failed"):
                tools.backup(self.root, self.archive)
        self.assertFalse(self.archive.exists())
        self.assert_intact()

    def test_large_compressed_zip_writeback_occurs_before_close_and_at_tail(self):
        self.content = random.Random(711).randbytes(10 * 1024 * 1024 + 17)
        self.source.write_bytes(self.content)
        events = []
        original_sync, original_close = os.fsync, zipfile.ZipFile.close
        def sync(fd):
            if Path(os.readlink(f"/proc/self/fd/{fd}")).name == "archive.zip":
                events.append("sync")
            return original_sync(fd)
        def close(archive):
            writing = archive.fp is not None and archive.mode == "w"
            result = original_close(archive)
            if writing: events.append("closed")
            return result
        with patch.object(os, "fsync", side_effect=sync), patch.object(zipfile.ZipFile, "close", close):
            checksum = tools.backup(self.root, self.archive)
        self.assertGreater(self.archive.stat().st_size, 8 * 1024 * 1024)
        self.assertIn("sync", events[:events.index("closed")])
        self.assertIn("sync", events[events.index("closed") + 1:])
        self.assertEqual(checksum, hashlib.sha256(self.archive.read_bytes()).hexdigest())
        self.assert_intact()

    def test_existing_destination_is_never_removed(self):
        self.archive.write_bytes(b"existing backup")
        with self.assertRaises(tools.ReleaseError): tools.backup(self.root, self.archive)
        self.assertEqual(self.archive.read_bytes(), b"existing backup")
        self.assert_intact()

    def test_source_replaced_after_copy_does_not_change_snapshot_manifest(self):
        original = tools.create_archive
        def create(payload, destination, metadata, **options):
            self.source.unlink(); self.source.write_bytes(b"new live source")
            return original(payload, destination, metadata, **options)
        with patch.object(tools, "create_archive", side_effect=create):
            sha = tools.backup(self.root, self.archive)
        extracted = self.root / "extracted"
        manifest = tools.unpack_verified(self.archive, sha, extracted)
        self.assertEqual((extracted / "large.log").read_bytes(), self.content)
        self.assertEqual(manifest['files']['large.log']['sha256'], hashlib.sha256(self.content).hexdigest())
        self.assertEqual(self.source.read_bytes(), b"new live source")


if __name__ == "__main__":
    unittest.main()
