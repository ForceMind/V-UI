"""Only newly created release core outputs receive writeback/cache advice."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app import release_tools as tools


class CoreUnpackWritebackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root / 'source'; self.source.mkdir()
        self.core_name = 'cores/x86_64/sing-box'
        self.core = self.source / self.core_name; self.core.parent.mkdir(parents=True)
        self.data = b'fixed fake core\n' * 600000 + b'last partial page'
        self.core.write_bytes(self.data)
        (self.core.parent / 'provenance.json').write_bytes(b'{}')
        (self.source / 'user.log').write_bytes(b'not a core')
        self.bundle = self.root / 'bundle.zip'
        self.sha = tools.create_archive(self.source, self.bundle, {'kind': 'release'})
        self.output = self.root / 'new-private-payload'
        self.old = self.root / 'old-running-core'; self.old.write_bytes(b'keep old')

    def intact_inputs(self):
        self.assertEqual(self.core.read_bytes(), self.data)
        self.assertEqual(self.old.read_bytes(), b'keep old')
        self.assertEqual(hashlib.sha256(self.bundle.read_bytes()).hexdigest(), self.sha)

    def test_advice_and_sync_only_new_core_inode_keep_every_byte_and_mode(self):
        events = []; original_sync = os.fsync
        def sync(fd):
            events.append(('sync', os.fstat(fd).st_ino)); original_sync(fd)
        def advise(fd, offset, length, advice):
            path = Path(os.readlink(f'/proc/self/fd/{fd}'))
            if path == self.output / self.core_name:
                info = os.fstat(fd)
                self.assertEqual(info.st_ino, path.stat().st_ino)
                self.assertEqual(info.st_uid, os.geteuid()); self.assertEqual(info.st_nlink, 1)
                self.assertIn(('sync', info.st_ino), events)
                events.append(('core_advice', info.st_ino))
            else:
                self.assertNotIn(os.fstat(fd).st_ino, [self.core.stat().st_ino, self.old.stat().st_ino])
                self.assertFalse(path.is_relative_to(self.output))
        with patch.object(os, 'fsync', side_effect=sync), patch.object(os, 'posix_fadvise', side_effect=advise):
            manifest = tools.unpack_verified(self.bundle, self.sha, self.output)
        self.assertGreaterEqual(sum(kind == 'core_advice' for kind, _ in events), 2)
        self.assertEqual((self.output / self.core_name).read_bytes(), self.data)
        self.assertEqual((self.output / self.core_name).stat().st_mode & 0o777, 0o700)
        self.assertEqual(tools.verify_payload(self.output), manifest); self.intact_inputs()

    def test_missing_or_unsupported_advice_preserves_manifest_and_output(self):
        for hint in (None, lambda *args: (_ for _ in ()).throw(OSError('not supported'))):
            with patch.object(os, 'posix_fadvise', hint):
                manifest = tools.unpack_verified(self.bundle, self.sha, self.output)
            self.assertEqual(tools.verify_payload(self.output), manifest)
            tools.shutil.rmtree(self.output)
        self.intact_inputs()

    def test_core_writeback_failure_removes_only_new_payload(self):
        original = os.fsync
        def sync(fd):
            if Path(os.readlink(f'/proc/self/fd/{fd}')) == self.output / self.core_name:
                raise OSError('core writeback failed')
            return original(fd)
        with patch.object(os, 'fsync', side_effect=sync), self.assertRaises(tools.ReleaseError):
            tools.unpack_verified(self.bundle, self.sha, self.output)
        self.assertFalse(self.output.exists()); self.intact_inputs()

    def test_exclusive_creation_refuses_preexisting_symlink(self):
        original = Path.open
        def opening(path, mode='r', *args, **kwargs):
            if path == self.output / self.core_name and mode == 'xb': path.symlink_to(self.old)
            return original(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', opening), self.assertRaises(tools.ReleaseError):
            tools.unpack_verified(self.bundle, self.sha, self.output)
        self.assertFalse(self.output.exists()); self.intact_inputs()

    def test_changed_output_identity_after_copy_is_rejected_and_old_file_retained(self):
        original = tools._PrivateArchiveFile.finish_writes
        swapped = False
        def finish(handle):
            nonlocal swapped
            result = original(handle)
            if not swapped and str(handle.handle.name) == str(self.output / self.core_name):
                swapped = True; path = self.output / self.core_name
                path.unlink(); path.symlink_to(self.old)
            return result
        with patch.object(tools._PrivateArchiveFile, 'finish_writes', finish), self.assertRaises(tools.ReleaseError):
            tools.unpack_verified(self.bundle, self.sha, self.output)
        self.assertTrue(swapped); self.assertFalse(self.output.exists()); self.intact_inputs()

    def test_corrupt_core_digest_still_rejects_after_stream_and_cleans_up(self):
        with zipfile.ZipFile(self.bundle) as archive:
            rows = [(info, archive.read(info.filename)) for info in archive.infolist()]
        with zipfile.ZipFile(self.bundle, 'w') as archive:
            for info, data in rows:
                archive.writestr(info, b'x' * len(data) if info.filename == self.core_name else data)
        altered_sha = hashlib.sha256(self.bundle.read_bytes()).hexdigest()
        with self.assertRaisesRegex(tools.ReleaseError, 'checksum mismatch'):
            tools.unpack_verified(self.bundle, altered_sha, self.output)
        self.assertFalse(self.output.exists()); self.assertEqual(self.old.read_bytes(), b'keep old')
        self.assertEqual(self.core.read_bytes(), self.data)


if __name__ == '__main__': unittest.main()
