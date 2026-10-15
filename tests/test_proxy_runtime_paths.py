"""Runtime secrets stay inside a verified private, single-owner file tree."""
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services import core_runtime as module
from app.services.core_runtime import CoreError, CoreRuntime, atomic_write, encode, revision


class PrivateRuntimePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vui-private-runtime-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'runtime'
        self.runtime = self.make_runtime(self.root)
        self.config = {'inbounds': [], 'password': 'fake-test-secret'}
        self.path = self.root / (revision(self.config) + '.json')

    def make_runtime(self, root):
        runtime = CoreRuntime('sing-box', self.base / 'unused', root)
        self.addCleanup(runtime.close)
        return runtime

    def private_file(self, path, raw=b''):
        path.write_bytes(raw)
        path.chmod(0o600)
        return path

    def seed(self):
        self.runtime._claim()
        atomic_write(self.path, encode(self.config))
        atomic_write(self.runtime.state_file, encode({'revision': revision(self.config), 'enabled': False}))

    def assert_no_apply(self, runtime=None):
        runtime = runtime or self.runtime
        with patch.object(runtime, 'check') as check, patch.object(runtime, '_launch') as launch:
            with self.assertRaises(CoreError):
                runtime.apply(self.config, activate=False)
            check.assert_not_called()
            launch.assert_not_called()

    def fake_owner(self, target):
        identity = (target.stat().st_dev, target.stat().st_ino)
        original = os.fstat
        def fstat(fd):
            info = original(fd)
            if (info.st_dev, info.st_ino) == identity:
                fields = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
                return SimpleNamespace(**{**fields, 'st_uid': os.geteuid() + 1001})
            return info
        return patch.object(module.os, 'fstat', side_effect=fstat)

    def test_root_symlink_never_writes_secrets_or_lease(self):
        exposed = self.base / 'exposed'
        exposed.mkdir(mode=0o777)
        exposed.chmod(0o777)
        self.root.symlink_to(exposed, target_is_directory=True)
        self.assert_no_apply()
        self.assertEqual(list(exposed.iterdir()), [])
        self.assertEqual(stat.S_IMODE(exposed.stat().st_mode), 0o777)

    def test_ancestor_symlink_is_not_hidden_by_constructor(self):
        actual = self.base / 'actual'
        actual.mkdir(mode=0o700)
        linked = self.base / 'linked'
        linked.symlink_to(actual, target_is_directory=True)
        runtime = self.make_runtime(linked / 'runtime')
        self.assertEqual(runtime.root, linked / 'runtime')
        self.assert_no_apply(runtime)
        self.assertEqual(list(actual.iterdir()), [])

    def test_insecure_runtime_modes_fail_without_repair(self):
        self.root.mkdir(mode=0o700)
        for mode in (0o755, 0o777, 0o750, 0o770, 0o1700):
            with self.subTest(mode=oct(mode)):
                self.root.chmod(mode)
                self.assert_no_apply()
                self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), mode)
                self.assertEqual(list(self.root.iterdir()), [])

    def test_group_or_world_writable_ancestor_is_rejected(self):
        parent = self.base / 'data'
        parent.mkdir(mode=0o700)
        runtime = self.make_runtime(parent / 'runtime')
        for mode in (0o775, 0o777):
            with self.subTest(mode=oct(mode)):
                parent.chmod(mode)
                self.assert_no_apply(runtime)
                self.assertFalse(runtime.root.exists())

    def test_owned_0755_parent_and_sticky_temporary_parent_are_supported(self):
        for mode in (0o755, 0o1777):
            parent = self.base / ('data-' + oct(mode))
            parent.mkdir(mode=0o700)
            parent.chmod(mode)
            runtime = self.make_runtime(parent / 'nested' / 'runtime')
            with patch.object(runtime, 'check'):
                self.assertFalse(runtime.apply(self.config, activate=False)['applied'])
            self.assertEqual(stat.S_IMODE(runtime.root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(runtime.root.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((runtime.root / 'owner.lock').stat().st_mode), 0o600)

    def test_foreign_runtime_and_ancestor_owners_fail_closed(self):
        self.root.mkdir(mode=0o700)
        for target in (self.base, self.root):
            with self.subTest(target=target.name), self.fake_owner(target):
                self.assert_no_apply()
                self.assertEqual(list(self.root.iterdir()), [])

    def test_only_empty_read_only_legacy_lease_bits_are_allowed(self):
        self.root.mkdir(mode=0o700)
        lease = self.private_file(self.root / 'owner.lock')
        for mode in (0o600, 0o640, 0o644):
            lease.chmod(mode)
            with patch.object(self.runtime, 'check'):
                self.runtime.apply(self.config, activate=False)
            self.runtime.close()
            self.assertEqual(stat.S_IMODE(lease.stat().st_mode), mode)
        for mode, raw in ((0o666, b''), (0o620, b''), (0o700, b''), (0o644, b'x'), (0o600, b'x')):
            with self.subTest(mode=oct(mode), nonempty=bool(raw)):
                lease.chmod(0o600)
                lease.write_bytes(raw)
                lease.chmod(mode)
                self.assert_no_apply()

    def test_unsafe_lease_links_and_nonregular_objects_are_rejected(self):
        self.root.mkdir(mode=0o700)
        target = self.private_file(self.base / 'target')
        lease = self.root / 'owner.lock'
        for kind in ('symlink', 'hardlink', 'directory', 'fifo'):
            with self.subTest(kind=kind):
                if kind == 'symlink': lease.symlink_to(target)
                elif kind == 'hardlink': os.link(target, lease)
                elif kind == 'directory': lease.mkdir(mode=0o700)
                else: os.mkfifo(lease, mode=0o600)
                self.assert_no_apply()
                if kind == 'directory': lease.rmdir()
                else: lease.unlink()
        self.assertEqual(target.read_bytes(), b'')

    def test_foreign_lease_owner_is_rejected(self):
        self.root.mkdir(mode=0o700)
        lease = self.private_file(self.root / 'owner.lock')
        with self.fake_owner(lease): self.assert_no_apply()

    def test_unsafe_state_rejected_before_candidate_write_or_restore_start(self):
        self.seed()
        state = self.runtime.state_file
        state.unlink()
        target = self.private_file(self.base / 'outside', b'outside evidence')
        for kind in ('symlink', 'hardlink', 'directory', 'fifo', 'mode'):
            with self.subTest(kind=kind):
                if kind == 'symlink': state.symlink_to(target)
                elif kind == 'hardlink': os.link(target, state)
                elif kind == 'directory': state.mkdir(mode=0o700)
                elif kind == 'fifo': os.mkfifo(state, mode=0o600)
                else: self.private_file(state, b'{}').chmod(0o644)
                self.assert_no_apply()
                with patch.object(self.runtime, 'check') as check, patch.object(self.runtime, '_launch') as launch:
                    with self.assertRaises(CoreError): self.runtime.restore(revision(self.config), enabled=True)
                    with self.assertRaises(CoreError): self.runtime.restore(None, enabled=False)
                    check.assert_not_called()
                    launch.assert_not_called()
                if kind == 'directory': state.rmdir()
                else: state.unlink()
        self.assertEqual(target.read_bytes(), b'outside evidence')

    def test_config_links_and_modes_fail_check_recover_and_restore(self):
        self.seed()
        self.path.unlink()
        target = self.private_file(self.base / 'outside', encode(self.config))
        for kind in ('symlink', 'hardlink', 'directory', 'fifo', 'mode'):
            with self.subTest(kind=kind):
                if kind == 'symlink': self.path.symlink_to(target)
                elif kind == 'hardlink': os.link(target, self.path)
                elif kind == 'directory': self.path.mkdir(mode=0o700)
                elif kind == 'fifo': os.mkfifo(self.path, mode=0o600)
                else: self.private_file(self.path, encode(self.config)).chmod(0o644)
                with patch.object(module.subprocess, 'run') as run, patch.object(module.subprocess, 'Popen') as popen:
                    for action in (self.runtime.state, self.runtime.recover,
                                   lambda: self.runtime.check(self.path),
                                   lambda: self.runtime._launch(self.path),
                                   lambda: self.runtime.restore(revision(self.config), enabled=True)):
                        with self.assertRaises(CoreError): action()
                    run.assert_not_called()
                    popen.assert_not_called()
                if kind == 'directory': self.path.rmdir()
                else: self.path.unlink()
        self.assertEqual(target.read_bytes(), encode(self.config))

    def test_foreign_state_and_config_owners_are_rejected(self):
        self.seed()
        for target in (self.path, self.runtime.state_file):
            with self.subTest(target=target.name), self.fake_owner(target):
                with self.assertRaises(CoreError): self.runtime.state()
                self.assert_no_apply()

    def test_candidate_link_target_never_receives_secret(self):
        self.runtime._claim()
        target = self.private_file(self.base / 'outside', b'unchanged')
        for kind in ('symlink', 'hardlink'):
            with self.subTest(kind=kind):
                if kind == 'symlink': self.path.symlink_to(target)
                else: os.link(target, self.path)
                self.assert_no_apply()
                self.assertEqual(target.read_bytes(), b'unchanged')
                self.path.unlink()
        self.assertEqual(list(self.root.glob('.stage-*')), [])

    def test_immutable_revision_is_not_silently_repaired_or_replaced(self):
        self.seed()
        original = self.path.stat().st_ino
        with patch.object(self.runtime, 'check'): self.runtime.apply(self.config, activate=False)
        self.assertEqual(self.path.stat().st_ino, original)
        self.path.write_bytes(b'{}')
        self.assert_no_apply()
        with self.assertRaises(CoreError): atomic_write(self.path, encode(self.config))
        self.assertEqual(self.path.read_bytes(), b'{}')

    def test_replaced_claimed_root_is_not_reclaimed_or_written(self):
        self.runtime._claim()
        self.root.rename(self.base / 'original-runtime')
        self.root.mkdir(mode=0o700)
        self.assert_no_apply()
        with self.assertRaises(CoreError): self.runtime.state()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_replaced_claimed_lease_is_not_reclaimed_or_written(self):
        self.runtime._claim()
        lease = self.root / 'owner.lock'
        lease.rename(self.root / 'old-owner.lock')
        self.private_file(lease)
        self.assert_no_apply()
        with self.assertRaises(CoreError): self.runtime.state()
        self.assertFalse(self.path.exists())

    def test_lease_name_must_still_match_after_flock(self):
        original = module.fcntl.flock
        def swap(fd, flags):
            original(fd, flags)
            lease = self.root / 'owner.lock'
            lease.rename(self.root / 'old-owner.lock')
            self.private_file(lease)
        with patch.object(module.fcntl, 'flock', side_effect=swap):
            self.assert_no_apply()
        self.assertIsNone(self.runtime.lease)
        self.assertFalse(self.path.exists())

    def test_standalone_atomic_writer_rejects_nonprivate_parent_and_links(self):
        self.root.mkdir(mode=0o755)
        self.root.chmod(0o755)
        with self.assertRaises(CoreError): atomic_write(self.path, encode(self.config))
        self.root.chmod(0o700)
        target = self.private_file(self.base / 'target', b'unchanged')
        for kind in ('symlink', 'hardlink'):
            if kind == 'symlink': self.path.symlink_to(target)
            else: os.link(target, self.path)
            with self.assertRaises(CoreError): atomic_write(self.path, encode(self.config))
            self.path.unlink()
        self.assertEqual(target.read_bytes(), b'unchanged')

    def test_state_rejects_duplicate_unknown_and_nonfinite_fields(self):
        self.seed()
        rev = revision(self.config)
        for text in ('{"revision":"%s","enabled":false,"enabled":true}' % rev,
                     '{"revision":"%s","enabled":false,"extra":null}' % rev,
                     '{"revision":"%s","enabled":NaN}' % rev,
                     '{"revision":"%s","enabled":0}' % rev):
            with self.subTest(text=text):
                atomic_write(self.runtime.state_file, text.encode())
                with self.assertRaises(CoreError): self.runtime.state()
                self.assert_no_apply()

    def test_active_config_is_private_descriptor_backed_and_independent(self):
        self.assertIsNone(self.runtime.active_config())
        self.seed()
        value = self.runtime.active_config()
        self.assertEqual(value, self.config)
        value['password'] = 'mutated fake value'
        self.assertEqual(self.runtime.active_config(), self.config)

    def test_active_config_rejects_path_swap_after_state_verification(self):
        self.seed()
        external = self.private_file(self.base / 'external', b'{"password":"outside-fake-secret"}')
        external_identity = (external.stat().st_dev, external.stat().st_ino)
        real_state, real_read = self.runtime.state, os.read
        def swap():
            state = real_state()
            self.path.unlink()
            self.path.symlink_to(external)
            return state
        def read(fd, count):
            info = os.fstat(fd)
            self.assertNotEqual((info.st_dev, info.st_ino), external_identity,
                                'External bytes must never be read')
            return real_read(fd, count)
        with patch.object(self.runtime, 'state', side_effect=swap), patch.object(module.os, 'read', side_effect=read):
            with self.assertRaises(CoreError): self.runtime.active_config()
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(external.read_bytes(), b'{"password":"outside-fake-secret"}')

    def test_reads_are_bounded_before_any_subprocess(self):
        self.seed()
        with self.runtime.state_file.open('r+b') as handle: handle.truncate(4097)
        with self.assertRaises(CoreError): self.runtime.state()
        atomic_write(self.runtime.state_file, encode({'revision': revision(self.config), 'enabled': False}))
        with self.path.open('r+b') as handle: handle.truncate(module.MAX_RUNTIME_BYTES + 1)
        with patch.object(module.subprocess, 'run') as run:
            with self.assertRaises(CoreError): self.runtime.check(self.path)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
