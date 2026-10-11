"""Writeback is restricted to the just-extracted runtime and preserves safety."""
import hashlib
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from app import release_tools as tools


class RuntimeWritebackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / 'runtime.tar.gz'
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name, data, mode in [('python/bin/python3.12', b'executable', 0o755),
                                     ('python/lib/data', b'payload' * 4096, 0o644)]:
                entry = tarfile.TarInfo(name); entry.size = len(data); entry.mode = mode
                archive.addfile(entry, io.BytesIO(data))
            for name, target, kind in [('python/bin/python3', 'python3.12', tarfile.SYMTYPE),
                                       ('python/lib/alias', 'python/lib/data', tarfile.LNKTYPE),
                                       ('python/lib-link', 'lib', tarfile.SYMTYPE)]:
                entry = tarfile.TarInfo(name); entry.type = kind; entry.linkname = target
                archive.addfile(entry)
        self.destination = self.root / 'new-runtime'
        self.old = self.root / 'old-runtime'; self.old.mkdir()
        (self.old / 'sentinel').write_bytes(b'old running runtime stays intact')
        self.input_hash = hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def assert_inputs_unchanged(self):
        self.assertEqual(hashlib.sha256(self.archive.read_bytes()).hexdigest(), self.input_hash)
        self.assertEqual((self.old / 'sentinel').read_bytes(), b'old running runtime stays intact')

    def test_only_new_regular_inodes_are_synced_then_advised_links_and_bytes_preserved(self):
        events = []
        def sync(fd): events.append(('sync', os.fstat(fd).st_ino))
        def advise(fd, offset, size, advice):
            inode = os.fstat(fd).st_ino
            self.assertEqual(events[-1], ('sync', inode))
            self.assertEqual((offset, size, advice), (0, 0, os.POSIX_FADV_DONTNEED))
            events.append(('advice', inode))
        with patch.object(tools.os, 'fsync', side_effect=sync), patch.object(tools.os, 'posix_fadvise', side_effect=advise):
            tools.extract_runtime(self.archive, self.destination)
        expected = [(self.destination / name).stat().st_ino for name in
                    ('python/bin/python3.12', 'python/lib/data', 'python/lib/alias')]
        self.assertCountEqual([inode for kind, inode in events if kind == 'advice'], expected)
        self.assertEqual((self.destination / 'python/bin/python3.12').stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.destination / 'python/lib/data').read_bytes(), b'payload' * 4096)
        self.assertTrue((self.destination / 'python/bin/python3').is_symlink())
        self.assertTrue((self.destination / 'python/lib-link').is_symlink())
        self.assertEqual(expected[1], expected[2])
        self.assert_inputs_unchanged()

    def test_optional_advice_failure_and_missing_platform_interfaces_preserve_extraction(self):
        for missing in ('posix_fadvise', 'POSIX_FADV_DONTNEED'):
            with self.subTest(missing=missing), patch.object(tools.os, missing, None), patch.object(tools.os, 'fsync') as sync:
                tools.extract_runtime(self.archive, self.destination)
                sync.assert_not_called()
            tools.shutil.rmtree(self.destination)
        with patch.object(tools.os, 'posix_fadvise', side_effect=OSError('unsupported')):
            tools.extract_runtime(self.archive, self.destination)
        self.assertEqual((self.destination / 'python/lib/data').read_bytes(), b'payload' * 4096)
        self.assert_inputs_unchanged()

    def test_sync_failure_removes_new_tree_but_keeps_archive_and_old_runtime(self):
        with patch.object(tools.os, 'fsync', side_effect=OSError('disk writeback failed')):
            with self.assertRaisesRegex(tools.ReleaseError, 'extraction failed'):
                tools.extract_runtime(self.archive, self.destination)
        self.assertFalse(self.destination.exists()); self.assert_inputs_unchanged()

    def test_existing_destination_is_never_advised_or_removed(self):
        self.destination.mkdir(); (self.destination / 'old').write_bytes(b'keep')
        with patch.object(tools.os, 'posix_fadvise') as advise:
            with self.assertRaises(FileExistsError): tools.extract_runtime(self.archive, self.destination)
            advise.assert_not_called()
        self.assertEqual((self.destination / 'old').read_bytes(), b'keep')
        self.assert_inputs_unchanged()

    def test_file_replacement_before_open_fails_without_following_external_link(self):
        original_open = os.open
        replaced = False
        def open_file(name, flags, *args, **kwargs):
            nonlocal replaced
            if kwargs.get('dir_fd') is not None and flags & os.O_NOFOLLOW and not replaced:
                replaced = True
                os.unlink(name, dir_fd=kwargs['dir_fd'])
                os.symlink(str(self.old / 'sentinel'), name, dir_fd=kwargs['dir_fd'])
            return original_open(name, flags, *args, **kwargs)
        with patch.object(tools.os, 'open', side_effect=open_file), patch.object(tools.os, 'posix_fadvise') as advise:
            with self.assertRaises(tools.ReleaseError): tools.extract_runtime(self.archive, self.destination)
            advise.assert_not_called()
        self.assertTrue(replaced); self.assertFalse(self.destination.exists()); self.assert_inputs_unchanged()


if __name__ == '__main__': unittest.main()
