"""The existing fixed root lease is modeled with real non-root temporary files."""
from contextlib import ExitStack
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from scripts import install_system as installer


class InstallerLockTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Keep all ancestors non-writable; /tmp may be shared without sticky
        # semantics on unusual executors and is not the production /run parent.
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory(
            prefix='.installer-lock-test-', dir=Path(__file__).resolve().parents[1])))
        self.path = self.directory / 'v-ui-install.lock'
        self.owner = os.geteuid()

    def hold(self, path=None):
        return installer._installation_lock(path or self.path, owner=self.owner)

    def test_real_competition_and_reuse_preserve_inode(self):
        with self.hold():
            inode = self.path.stat().st_ino
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
            with self.assertRaises(installer.InstallError):
                with self.hold():
                    self.fail('concurrent lease entered')
        with self.hold():
            self.assertEqual(self.path.stat().st_ino, inode)
        self.assertTrue(self.path.exists())

    def test_symlink_fifo_hardlink_and_public_leaf_refused(self):
        target = self.directory / 'target'
        target.write_text('unchanged')
        for kind in ('symlink', 'fifo', 'hardlink', 'public'):
            with self.subTest(kind=kind):
                if kind == 'symlink': self.path.symlink_to(target)
                elif kind == 'fifo': os.mkfifo(self.path, 0o600)
                elif kind == 'hardlink': os.link(target, self.path)
                else:
                    self.path.write_text('not private')
                    self.path.chmod(0o644)
                with self.assertRaises((installer.InstallError, OSError)):
                    with self.hold():
                        self.fail('unsafe leaf entered')
                self.path.unlink()
                self.assertEqual(target.read_text(), 'unchanged')

    def test_parent_link_and_unprotected_writable_parent_refused(self):
        real = self.directory / 'real'; real.mkdir(mode=0o700)
        linked = self.directory / 'linked'; linked.symlink_to(real, target_is_directory=True)
        with self.assertRaises(OSError):
            with self.hold(linked / 'lock'): pass
        real.chmod(0o777)
        with self.assertRaises(installer.InstallError):
            with self.hold(real / 'lock'): pass
        self.assertFalse((real / 'lock').exists())

    def test_sticky_final_parent_is_supported_without_repair(self):
        self.directory.chmod(0o1777)
        with self.hold():
            self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o1777)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_name_replacement_after_flock_is_not_accepted(self):
        original = installer.fcntl.flock
        def replace_then_hold(fd, flags):
            original(fd, flags)
            self.path.unlink()
            self.path.touch(mode=0o600)
        with patch.object(installer.fcntl, 'flock', side_effect=replace_then_hold):
            with self.assertRaisesRegex(installer.InstallError, '安装锁文件已发生变化'):
                with self.hold(): pass
        # The abandoned original fd was closed; no automatic deletion of the
        # unknown replacement or claim that the old lock still serializes it.
        self.assertTrue(self.path.exists())

    def test_wrong_owner_and_nonabsolute_paths_are_rejected(self):
        with self.assertRaises(installer.InstallError):
            with installer._installation_lock(Path('relative')): pass
        self.path.touch(mode=0o600)
        with self.assertRaises(installer.InstallError):
            with installer._installation_lock(self.path, owner=self.owner + 999): pass


if __name__ == '__main__': unittest.main()
