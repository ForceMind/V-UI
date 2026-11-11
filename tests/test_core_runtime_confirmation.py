"""Observe inert fake executable bytes; never launch a core or service."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.services.core_runtime import CoreError, CoreRuntime


class RuntimeConfirmationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='vui-confirmation-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.binary = self.root/'inert-core'
        self.binary.write_bytes(b'inert-test-data-not-an-executable')
        self.binary.chmod(0o700)
        self.runtime = CoreRuntime('sing-box', self.binary, self.root/'runtime')

    def test_observed_bytes_are_stable_private_and_not_a_trust_claim(self):
        first = self.runtime.confirmation_identity()
        self.assertEqual(first, self.runtime.confirmation_identity())
        self.assertEqual(first['binary_sha256'], hashlib.sha256(self.binary.read_bytes()).hexdigest())
        self.assertIsNone(first['artifact_id'])
        self.assertIsNone(first['execution']['selected'])
        self.assertNotIn(str(self.root), repr(first))
        self.binary.write_bytes(b'different-inert-test-data')
        self.assertNotEqual(first, self.runtime.confirmation_identity())

    def test_non_executable_writable_and_linked_files_refused(self):
        for mode in (0o600, 0o722):
            self.binary.chmod(mode)
            with self.subTest(mode=mode), self.assertRaises(CoreError):
                self.runtime.confirmation_identity()
        self.binary.chmod(0o700)
        other = self.root/'other'
        self.binary.rename(other)
        self.binary.symlink_to(other)
        with self.assertRaises(CoreError):
            self.runtime.confirmation_identity()

    def test_process_generation_and_committed_state_are_bound(self):
        process = Mock(pid=12345)
        process.poll.return_value = None
        self.runtime.process = process
        observed = Mock()
        observed.create_time.return_value = 100.5
        with patch('app.services.core_runtime.psutil.Process', return_value=observed), \
                patch.object(self.runtime, 'state', return_value={'revision': None, 'enabled': False}):
            before = self.runtime.confirmation_identity()
            observed.create_time.return_value = 101.5
            after = self.runtime.confirmation_identity()
        self.assertNotEqual(before, after)
        self.runtime.process = None
        with patch.object(self.runtime, 'state', return_value={'revision': 'a'*64, 'enabled': True}):
            self.assertNotEqual(before, self.runtime.confirmation_identity())

    def test_path_replacement_during_read_refuses_observation(self):
        import os
        original = os.read
        changed = False
        def read(fd, size):
            nonlocal changed
            data = original(fd, size)
            if not changed:
                changed = True
                self.binary.rename(self.root/'previous')
                self.binary.write_bytes(b'replacement')
                self.binary.chmod(0o700)
            return data
        with patch('app.services.core_runtime.os.read', side_effect=read):
            with self.assertRaises(CoreError):
                self.runtime.confirmation_identity()

    def test_replaced_ancestor_is_not_followed_even_to_same_binary(self):
        original = self.root/'child'
        original.mkdir(mode=0o700)
        binary = original/'inert-core'
        binary.write_bytes(b'same-inert-bytes')
        binary.chmod(0o700)
        runtime = CoreRuntime('sing-box', binary, self.root/'other-runtime')
        moved = self.root/'moved'
        original.rename(moved)
        original.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(CoreError):
            runtime.confirmation_identity()

    def test_selected_descriptor_is_reverified_before_observation(self):
        from test_core_runtime_artifact import artifact_for
        descriptor = artifact_for(self.binary, self.root, mode=0o700)
        runtime = CoreRuntime('sing-box', self.binary, self.root/'selected-runtime', artifact=descriptor)
        identity = runtime.confirmation_identity()
        self.assertEqual(identity['artifact_id'], descriptor.artifact_id)
        self.binary.write_bytes(b'changed-inert-test-data')
        with self.assertRaises(CoreError):
            runtime.confirmation_identity()

    def test_writable_ancestor_and_security_metadata_change_are_refused(self):
        self.root.chmod(0o777)
        try:
            with self.assertRaises(CoreError):
                self.runtime.confirmation_identity()
        finally:
            self.root.chmod(0o700)
        import os
        read = os.read
        changed = False
        def change_mode(fd, size):
            nonlocal changed
            data = read(fd, size)
            if not changed:
                changed = True
                self.root.chmod(0o755)
            return data
        try:
            with patch('app.services.core_runtime.os.read', side_effect=change_mode):
                with self.assertRaises(CoreError):
                    self.runtime.confirmation_identity()
        finally:
            self.root.chmod(0o700)
