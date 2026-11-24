"""Read-only diagnostic observations using fake processes and private fixtures."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, PropertyMock, patch

from app.core_updates.binary import RuntimeArtifact, open_runtime_artifact
from app.core_updates.selection import bundle_reference
from app.services.core_manager import CoreManager
from app.services.core_runtime import CoreRuntime, VERSIONS, revision


class CoreCapabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='vui-capabilities-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.artifacts = {}
        for core in VERSIONS:
            binary = self.root / core
            content = ('fake diagnostic fixture ' + core).encode()
            binary.write_bytes(content)
            binary.chmod(0o555)
            reference = bundle_reference(core=core, version=VERSIONS[core],
                target='x86_64-gnu', app_source='a'*40,
                payload_manifest_sha256='b'*64,
                binary_sha256=hashlib.sha256(content).hexdigest(), binary_size=len(content))
            self.artifacts[core] = RuntimeArtifact(reference, binary, os.geteuid(), self.root)
        self.artifact = self.artifacts['sing-box']
        self.runtime = CoreRuntime('sing-box', self.artifact.path,
                                   self.root / 'unused-runtime', artifact=self.artifact)

    def live_generation(self):
        # No core is executed. Use the real descriptor verifier and held FD.
        fd = self.enterContext(open_runtime_artifact(self.artifact))
        self.runtime._binary_fd = fd
        self.runtime._generation_artifact = self.artifact
        self.runtime._generation_binary_identity = self.runtime._execution_inode(fd)
        self.runtime.process = SimpleNamespace(poll=lambda: None)
        return fd

    def test_certificate_config_check_does_not_stop_or_inspect_another_core(self):
        from app.services.proxy_apply import ProxyApplyError

        manager = CoreManager(artifacts=self.artifacts)
        manager._proxy_coordinator = Mock()
        manager._proxy_coordinator.compile_active.side_effect = ProxyApplyError("RECOVERY_REQUIRED")
        active = manager.get('xray').build_config([])
        with patch.object(manager, '_all_rows', return_value=[]), \
             patch.object(manager, '_stop_untrusted', side_effect=AssertionError('unrelated stop')), \
             patch.object(manager.get('xray').runtime, 'state', return_value={'revision': revision(active)}):
            self.assertFalse(manager.has_unapplied_config('xray'))
            manager._proxy_coordinator.compile_active.assert_not_called()
            self.assertTrue(manager.has_unapplied_config('sing-box'))
            manager._proxy_coordinator.compile_active.assert_called_once_with([])

    def test_uninitialized_manager_does_not_create_runtime_or_read_configuration(self):
        manager = CoreManager(artifacts=self.artifacts)
        with patch('app.services.core_manager.CoreRuntime', side_effect=AssertionError('created')), \
             patch.object(manager, 'status', side_effect=AssertionError('status')), \
             patch.object(manager, '_compiled_active', side_effect=AssertionError('compiled')), \
             patch.object(manager, '_stop_untrusted', side_effect=AssertionError('stopped')):
            result = manager.capability_snapshot()
        for name, row in result.items():
            self.assertEqual(row['selected_version'], VERSIONS[name])
            self.assertEqual(row['identity_state'], 'not_initialized')
            self.assertIsNone(row['running'])
            self.assertIsNone(row['running_version'])
            self.assertIsNone(manager.adapters[name]._runtime)
            self.assertFalse(manager.adapters[name]._runtime_created)
        self.assertIsNone(manager._proxy_coordinator)
        self.assertFalse(self.runtime.root.exists())

    def test_stopped_and_exited_observations_never_cleanup_held_generation(self):
        self.assertEqual(self.runtime.capability_snapshot()['identity_state'], 'stopped')
        fd = self.live_generation()
        self.runtime.process = SimpleNamespace(poll=lambda: 0)
        previous = (self.runtime.process, self.runtime._binary_fd,
                    self.runtime._generation_artifact, self.runtime._generation_binary_identity)
        with patch.object(self.runtime, '_terminate', side_effect=AssertionError('cleanup')), \
             patch.object(self.runtime, 'state', side_effect=AssertionError('state read')):
            result = self.runtime.capability_snapshot()
        self.assertEqual(result['identity_state'], 'stopped')
        self.assertFalse(result['running'])
        self.assertIsNone(result['running_version'])
        self.assertEqual(previous, (self.runtime.process, self.runtime._binary_fd,
                                   self.runtime._generation_artifact, self.runtime._generation_binary_identity))
        self.assertGreater(os.fstat(fd).st_size, 0)

    def test_legacy_running_pin_is_not_claimed_as_verified_running_version(self):
        legacy = CoreRuntime('sing-box', self.artifact.path, self.root / 'legacy')
        legacy.process = SimpleNamespace(poll=lambda: None)
        result = legacy.capability_snapshot()
        self.assertEqual(result['selected_version'], VERSIONS['sing-box'])
        self.assertEqual(result['selection_source'], 'bundled')
        self.assertTrue(result['running'])
        self.assertIsNone(result['running_version'])
        self.assertIsNone(result['matches_selected'])
        self.assertEqual(result['identity_state'], 'unverified')

    def test_verified_generation_and_selection_mismatch_are_distinct(self):
        self.live_generation()
        result = self.runtime.capability_snapshot()
        self.assertEqual(result['identity_state'], 'verified')
        self.assertEqual(result['running_version'], self.artifact.version)
        self.assertTrue(result['matches_selected'])
        # Identical version and bytes do not prove the same trusted selection.
        self.runtime._artifact = replace(self.artifact, reference=replace(
            self.artifact.reference, payload_manifest_sha256='c'*64))
        result = self.runtime.capability_snapshot()
        self.assertEqual(result['identity_state'], 'mismatch')
        self.assertFalse(result['matches_selected'])

    def test_new_selected_version_does_not_replace_the_qualification_baseline(self):
        updated = replace(self.artifact, reference=replace(self.artifact.reference, version='1.14.3'))
        runtime = CoreRuntime('sing-box', updated.path, self.root / 'unused-new', artifact=updated)
        result = runtime.capability_snapshot()
        self.assertEqual(result['qualification_baseline'], '1.14.2')
        self.assertEqual(result['selected_version'], '1.14.3')
        self.assertIsNone(result['running_version'])

    def test_process_exit_during_identity_read_is_reported_without_cleanup(self):
        fd = self.live_generation()
        self.runtime.process = SimpleNamespace(poll=Mock(side_effect=[None, 0]))
        with patch.object(self.runtime, '_terminate', side_effect=AssertionError('cleanup')):
            result = self.runtime.capability_snapshot()
        self.assertEqual(result['identity_state'], 'stopped')
        self.assertFalse(result['running'])
        self.assertIsNone(result['running_version'])
        self.assertGreater(os.fstat(fd).st_size, 0)

    def test_held_binary_mutation_cannot_keep_a_verified_label(self):
        self.live_generation()
        self.artifact.path.chmod(0o755)
        result = self.runtime.capability_snapshot()
        self.assertTrue(result['running'])
        self.assertEqual(result['identity_state'], 'unverified')
        self.assertIsNone(result['running_version'])
        self.assertIsNone(result['matches_selected'])

    def test_poll_error_has_no_private_exception_or_configuration_details(self):
        self.runtime.process = SimpleNamespace(poll=Mock(side_effect=OSError('/private/token-secret')))
        self.runtime.last_error = '/private/stored-secret'
        result = self.runtime.capability_snapshot()
        self.assertEqual(result['identity_state'], 'unavailable')
        self.assertIsNone(result['running'])
        serialized = json.dumps(result)
        for private in ('private', 'secret', str(self.root), 'artifact_id', 'binary_sha256',
                        'payload_manifest_sha256', 'pid', 'last_error'):
            self.assertNotIn(private, serialized)

    def test_busy_runtime_and_manager_do_not_wait_or_infer_stopped(self):
        def observe_while_locked(target):
            acquired, release = threading.Event(), threading.Event()
            def hold():
                with target.lock:
                    acquired.set()
                    release.wait(2)
            thread = threading.Thread(target=hold)
            thread.start()
            try:
                self.assertTrue(acquired.wait(1))
                return target.capability_snapshot()
            finally:
                release.set()
                thread.join(1)
                self.assertFalse(thread.is_alive())
        result = observe_while_locked(self.runtime)
        self.assertEqual(result['identity_state'], 'busy')
        self.assertIsNone(result['running'])
        manager = CoreManager(artifacts=self.artifacts)
        result = observe_while_locked(manager)
        self.assertTrue(all(row['identity_state'] == 'busy' for row in result.values()))
        self.assertTrue(all(row['running'] is None for row in result.values()))

    def test_manager_uses_existing_instance_without_adapter_property_or_status(self):
        self.live_generation()
        manager = CoreManager(artifacts=self.artifacts)
        adapter = manager.adapters['sing-box']
        adapter._runtime = self.runtime
        with patch.object(type(adapter), 'runtime', new_callable=PropertyMock,
                          side_effect=AssertionError('runtime factory')), \
             patch.object(self.runtime, 'status', side_effect=AssertionError('status')), \
             patch.object(self.runtime, 'execution_identity', side_effect=AssertionError('cleanup path')):
            result = manager.capability_snapshot()
        self.assertEqual(result['sing-box']['identity_state'], 'verified')
        self.assertEqual(result['xray']['identity_state'], 'not_initialized')
        expected = {'qualification_baseline', 'selected_version', 'selection_source',
                    'running', 'running_version', 'identity_state', 'matches_selected'}
        self.assertTrue(all(set(row) == expected for row in result.values()))


if __name__ == '__main__':
    unittest.main()
