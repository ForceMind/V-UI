"""Pure descriptor/state checks plus Linux-only fake executable runtime checks.

The pure state loader removes only psutil's import; it never executes a process
or substitutes Linux runtime acceptance. Linux setUp imports the real module.
"""
import ast
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from app.core_updates.binary import (RuntimeArtifact, RuntimeArtifactError, open_runtime_artifact,
                                     verify_runtime_artifact_fd)
from app.core_updates.selection import ComponentReference, bundle_reference, reference_identity


SOURCE = Path(__file__).resolve().parents[1] / 'app/services/core_runtime.py'


def artifact_for(path, anchor, *, version='1.14.3', **overrides):
    raw = path.read_bytes()
    reference = bundle_reference(core='sing-box', version=version, target='x86_64-gnu',
        app_source='a' * 40, payload_manifest_sha256='b' * 64,
        binary_sha256=hashlib.sha256(raw).hexdigest(), binary_size=len(raw))
    return RuntimeArtifact(reference, path, os.geteuid(), anchor, **overrides)


def pure_runtime():
    tree = ast.parse(SOURCE.read_text(), filename=str(SOURCE))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.Import) and any(alias.name == 'psutil' for alias in node.names))]
    module = types.ModuleType('_pure_artifact_runtime')
    module.__file__ = str(SOURCE)
    exec(compile(tree, str(SOURCE), 'exec'), module.__dict__)
    return module


class ArtifactFixture:
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='vui-artifact-')
        self.addCleanup(temp.cleanup)
        self.anchor = Path(temp.name).resolve()
        self.path = self.anchor / 'bin' / 'sing-box'
        self.path.parent.mkdir(mode=0o755)
        self.path.write_bytes(b'fake-independent-core')
        self.path.chmod(0o555)
        self.artifact = artifact_for(self.path, self.anchor)



class RuntimeArtifactDescriptorTests(ArtifactFixture, unittest.TestCase):
    def test_frozen_descriptor_binds_full_reference(self):
        self.assertEqual(self.artifact.artifact_id, reference_identity(self.artifact.reference))
        changed = replace(self.artifact, reference=replace(self.artifact.reference,
            payload_manifest_sha256='c' * 64))
        self.assertNotEqual(changed.artifact_id, self.artifact.artifact_id)
        self.assertEqual((self.artifact.core, self.artifact.version), ('sing-box', '1.14.3'))
        with self.assertRaises(FrozenInstanceError):
            self.artifact.path = self.anchor / 'other'

    def test_fd_keeps_verified_inode_after_path_replacement_and_closes(self):
        with open_runtime_artifact(self.artifact) as fd:
            original = os.fstat(fd).st_ino
            replacement = self.anchor / 'replacement'
            replacement.write_bytes(b'untrusted replacement')
            replacement.chmod(0o555)
            os.replace(replacement, self.path)
            self.assertEqual(os.fstat(fd).st_ino, original)
            self.assertEqual(os.read(fd, 100), b'fake-independent-core')
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_hash_and_size_rejected(self):
        for reference in (replace(self.artifact.reference, binary_sha256='0' * 64),
                          replace(self.artifact.reference, binary_size=1)):
            with self.subTest(reference=reference), self.assertRaises(RuntimeArtifactError):
                with open_runtime_artifact(replace(self.artifact, reference=reference)):
                    self.fail('unverified descriptor opened')

    def test_changed_during_hash_rejected(self):
        real_read = os.pread
        def mutate(fd, size, offset):
            value = real_read(fd, size, offset)
            if value:
                self.path.chmod(0o755)
            return value
        with patch('app.core_updates.binary.os.pread', side_effect=mutate):
            with self.assertRaises(RuntimeArtifactError):
                with open_runtime_artifact(self.artifact):
                    self.fail('changed inode accepted')

    def test_modes_owner_hardlink_symlinks_rejected(self):
        for mode in (0o755, 0o555 | 0o020, 0o444):
            with self.subTest(mode=mode):
                self.path.chmod(mode)
                with self.assertRaises(RuntimeArtifactError):
                    with open_runtime_artifact(self.artifact):
                        self.fail('unsafe mode accepted')
        self.path.chmod(0o555)
        with self.assertRaises(RuntimeArtifactError):
            with open_runtime_artifact(replace(self.artifact, owner=os.geteuid() + 1)):
                self.fail('wrong owner accepted')
        link = self.anchor / 'hardlink'
        os.link(self.path, link)
        with self.assertRaises(RuntimeArtifactError):
            with open_runtime_artifact(self.artifact):
                self.fail('hardlink accepted')
        link.unlink()
        old = self.path.with_name('old')
        self.path.rename(old)
        self.path.symlink_to(old)
        with self.assertRaises(RuntimeArtifactError):
            with open_runtime_artifact(self.artifact):
                self.fail('symlink accepted')

    def test_writable_and_symlink_ancestor_rejected(self):
        self.path.parent.chmod(0o777)
        with self.assertRaises(RuntimeArtifactError):
            with open_runtime_artifact(self.artifact):
                self.fail('writable ancestor accepted')
        self.path.parent.chmod(0o755)
        self.path.parent.rename(self.anchor / 'original')
        self.path.parent.symlink_to(self.anchor / 'original', target_is_directory=True)
        with self.assertRaises(RuntimeArtifactError):
            with open_runtime_artifact(self.artifact):
                self.fail('linked ancestor accepted')

    def test_bundle_verified_owner_and_manifest_mode_accept_700_555_755(self):
        for mode in (0o700, 0o555, 0o755):
            self.path.chmod(mode)
            descriptor = replace(self.artifact, mode=mode)
            with self.subTest(mode=mode), open_runtime_artifact(descriptor) as fd:
                verify_runtime_artifact_fd(fd, descriptor)
                self.assertEqual(os.fstat(fd).st_uid, descriptor.owner)
        with self.assertRaises(RuntimeArtifactError):
            replace(self.artifact, mode=0o775)

    def test_component_700_or_nonroot_production_owner_rejected(self):
        bundle = self.artifact.reference
        component = ComponentReference(repository='ForceMind/V-UI', release_id=1,
            tag='core-sing-box-v1.14.3-r1', core=bundle.core, version=bundle.version,
            target=bundle.target, builder_source='a' * 40,
            release_manifest_sha256='c' * 64, checksums_sha256='d' * 64,
            pack_sha256='e' * 64, pack_manifest_sha256='f' * 64,
            binary_sha256=bundle.binary_sha256, binary_size=bundle.binary_size)
        with self.assertRaises(RuntimeArtifactError):
            replace(self.artifact, reference=component, mode=0o700)
        with self.assertRaises(RuntimeArtifactError):
            replace(self.artifact, reference=component, owner=1, anchor=Path('/'))
        descriptor = replace(self.artifact, reference=component)
        with open_runtime_artifact(descriptor) as fd:
            self.assertEqual(os.fstat(fd).st_size, bundle.binary_size)
        self.path.chmod(0o700)
        with self.assertRaises(RuntimeArtifactError):
            with open_runtime_artifact(descriptor):
                self.fail('component inherited bundle executable mode')

    def test_directory_allows_root_or_verified_owner_but_not_other_owner(self):
        from app.core_updates import binary
        for owner in (0, self.artifact.owner):
            info = types.SimpleNamespace(st_mode=0o40755, st_uid=owner)
            with patch.object(binary.os, 'fstat', return_value=info):
                self.assertIs(binary._directory(123, self.artifact.owner), info)
        info = types.SimpleNamespace(st_mode=0o40755, st_uid=max(1, self.artifact.owner + 1))
        with patch.object(binary.os, 'fstat', return_value=info):
            with self.assertRaises(RuntimeArtifactError):
                binary._directory(123, self.artifact.owner)

    def test_held_bundle_inplace_change_rejected_even_with_same_size_and_restored_mtime(self):
        self.path.chmod(0o700)
        descriptor = replace(self.artifact, mode=0o700)
        with open_runtime_artifact(descriptor) as fd:
            before = os.fstat(fd)
            self.path.write_bytes(b'x' * before.st_size)
            os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns))
            with self.assertRaises(RuntimeArtifactError):
                verify_runtime_artifact_fd(fd, descriptor)

    def test_ancestor_replaced_during_digest_rejected(self):
        read = os.pread
        moved = False
        def replace_parent(fd, size, offset):
            nonlocal moved
            value = read(fd, size, offset)
            if value and not moved:
                moved = True
                self.path.parent.rename(self.anchor / 'moved')
                self.path.parent.mkdir(mode=0o755)
                self.path.write_bytes(b'fake-independent-core')
                self.path.chmod(0o555)
            return value
        with patch('app.core_updates.binary.os.pread', side_effect=replace_parent):
            with self.assertRaises(RuntimeArtifactError):
                with open_runtime_artifact(self.artifact):
                    self.fail('replaced ancestor accepted')

    def test_close_error_attempts_all_descriptors_and_reports_fixed_error(self):
        close = os.close
        closed = []
        def fail_once(fd):
            close(fd)
            closed.append(fd)
            if len(closed) == 1:
                raise OSError('synthetic close failure after release')
        with patch('app.core_updates.binary.os.close', side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeArtifactError, '^Runtime core artifact descriptor cleanup failed$'):
                with open_runtime_artifact(self.artifact):
                    pass
        self.assertEqual(len(closed), 3)
        for fd in closed:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_descriptor_rejects_relative_escape_and_boolean_owner(self):
        for kwargs in ({'path': Path('relative')}, {'path': self.anchor / '..' / 'outside'},
                       {'anchor': self.anchor / 'elsewhere'}, {'owner': True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(RuntimeArtifactError):
                replace(self.artifact, **kwargs)


class RuntimeArtifactStateTests(ArtifactFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.module = pure_runtime()
        self.runtime = self.module.CoreRuntime('sing-box', self.path,
            self.anchor / 'runtime', artifact=self.artifact)
        self.addCleanup(self.runtime.close)
        self.runtime._claim()
        self.config = {'inbounds': []}
        self.rev = self.module.revision(self.config)
        self.runtime._write(self.runtime.root / (self.rev + '.json'), self.module.encode(self.config))

    def record(self, value):
        self.runtime._write(self.runtime.state_file, self.module.encode(value))

    def test_schema2_normalized_view_and_stop_keep_binding(self):
        state = {'revision': self.rev, 'enabled': True}
        self.record(self.runtime._persistent_state(state))
        self.assertEqual(self.runtime.state(), state)
        self.runtime.stop()
        saved = json.loads(self.runtime.state_file.read_bytes())
        self.assertEqual(saved, {'schema': 2, 'artifact_id': self.artifact.artifact_id,
                                'revision': self.rev, 'enabled': False})
        self.runtime.close()
        self.assertEqual(json.loads(self.runtime.state_file.read_bytes()), saved)

    def test_legacy_other_artifact_unknown_fields_duplicate_or_boolean_schema_rejected(self):
        valid = self.runtime._persistent_state({'revision': self.rev, 'enabled': True})
        for invalid in ({'revision': self.rev, 'enabled': True},
                        {**valid, 'artifact_id': '0' * 64}, {**valid, 'schema': True},
                        {**valid, 'extra': 1}, {**valid, 'enabled': 1}):
            with self.subTest(invalid=invalid):
                self.record(invalid)
                with self.assertRaises(self.module.CoreError):
                    self.runtime.state()
        raw = self.module.encode(valid).replace(b'"schema":2', b'"schema":2,"schema":2')
        self.runtime._write(self.runtime.state_file, raw)
        with self.assertRaises(self.module.CoreError):
            self.runtime.state()

    def test_legacy_runtime_keeps_state1_and_rejects_state2(self):
        old = self.module.CoreRuntime('sing-box', self.path, self.runtime.root)
        self.addCleanup(old.close)
        value = {'revision': self.rev, 'enabled': False}
        self.assertEqual(old._persistent_state(value), value)
        self.record(value)
        self.assertEqual(old.state(), value)
        self.record(self.runtime._persistent_state(value))
        with self.assertRaises(self.module.CoreError):
            old.state()

    def test_missing_state_stays_stopped_without_opening_missing_binary(self):
        self.path.unlink()
        self.runtime.recover()
        self.assertEqual(self.runtime.state(), {'revision': None, 'enabled': False})
        self.assertIsNone(self.runtime.process)

    def test_check_error_and_validate_only_release_operation_fd(self):
        captured = []
        def reject(command, **kwargs):
            captured.append(int(command[0].rsplit('/', 1)[1]))
            return types.SimpleNamespace(returncode=1, stdout=b'')
        with patch.object(self.module.subprocess, 'run', side_effect=reject):
            with self.assertRaises(self.module.CoreError):
                self.runtime.check(self.runtime.root / (self.rev + '.json'))
        self.assertIsNone(self.runtime._operation_binary_fd)
        for fd in captured:
            with self.assertRaises(OSError):
                os.fstat(fd)
        with patch.object(self.runtime, 'check'):
            self.runtime.apply(self.config, activate=False)
        self.assertIsNone(self.runtime._operation_binary_fd)
        self.assertIsNone(self.runtime._binary_fd)
        self.assertFalse(self.runtime.state_file.exists())

    def test_launch_failure_releases_generation_and_operation_fds(self):
        captured = []
        def fail(path, binary):
            self.assertEqual(binary, f'/proc/{os.getpid()}/fd/{self.runtime._binary_fd}')
            captured.extend((self.runtime._binary_fd, self.runtime._operation_binary_fd))
            raise OSError('synthetic failure')
        with patch.object(self.runtime, '_launch_binary', side_effect=fail):
            with self.assertRaises(self.module.CoreError):
                self.runtime._launch(self.runtime.root / (self.rev + '.json'))
        self.assertIsNone(self.runtime._operation_binary_fd)
        self.assertIsNone(self.runtime._binary_fd)
        for fd in captured:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_generation_owns_dup_after_operation_closes_until_close(self):
        captured = []
        def launch(path, binary):
            captured.extend((self.runtime._binary_fd, self.runtime._operation_binary_fd))
            self.assertEqual(os.fstat(captured[0]).st_ino, os.fstat(captured[1]).st_ino)
        with patch.object(self.runtime, '_launch_binary', side_effect=launch):
            self.runtime._launch(self.runtime.root / (self.rev + '.json'))
        self.assertEqual(os.fstat(captured[0]).st_ino, self.path.stat().st_ino)
        with self.assertRaises(OSError):
            os.fstat(captured[1])
        self.runtime.close()
        with self.assertRaises(OSError):
            os.fstat(captured[0])

    def test_execution_identity_separates_selection_generation_and_exit(self):
        selected = {'artifact_id': self.artifact.artifact_id,
                    'binary_sha256': self.artifact.reference.binary_sha256}
        self.assertEqual(self.runtime.execution_identity(), {'selected': selected, 'running': None})
        process = types.SimpleNamespace(poll=lambda: None)
        def launch(path, binary):
            self.runtime.process = process
        with patch.object(self.runtime, '_launch_binary', side_effect=launch):
            self.runtime._launch(self.runtime.root / (self.rev + '.json'))
        self.assertEqual(self.runtime.execution_identity(), {'selected': selected, 'running': selected})
        self.path.chmod(0o755)
        self.assertIsNone(self.runtime.execution_identity()['running'])
        self.path.chmod(0o555)
        self.runtime.process = types.SimpleNamespace(poll=lambda: 0)
        self.assertEqual(self.runtime.execution_identity(), {'selected': selected, 'running': None})
        self.assertIsNone(self.runtime._binary_fd)
        self.assertIsNone(self.runtime._generation_artifact)

    def test_check_boundary_rejects_bundle_inplace_mutation_before_second_execution(self):
        self.path.chmod(0o700)
        self.runtime.close()
        self.runtime = self.module.CoreRuntime('sing-box', self.path, self.anchor / 'runtime',
            artifact=replace(self.artifact, mode=0o700))
        self.addCleanup(self.runtime.close)
        calls = []
        def mutate(command, **kwargs):
            calls.append(command)
            self.path.write_bytes(b'x' * self.path.stat().st_size)
            return types.SimpleNamespace(returncode=0, stdout=b'sing-box version 1.14.3')
        with patch.object(self.module.subprocess, 'run', side_effect=mutate):
            with self.assertRaises(self.module.CoreError):
                self.runtime.check(self.runtime.root / (self.rev + '.json'))
        self.assertEqual(len(calls), 1)
        self.assertIsNone(self.runtime._operation_binary_fd)

    def test_popen_failure_lost_config_close_ack_never_closes_reused_foreign_fd(self):
        foreign_path = self.anchor / 'foreign-descriptor'
        foreign_path.write_bytes(b'foreign owner')
        foreign_source = os.open(foreign_path, os.O_RDONLY)
        close = os.close
        target = []
        attempts = []
        lost_ack = False
        def fail_popen(*args, **kwargs):
            target.append(self.runtime._config_fd)
            raise OSError('synthetic Popen failure')
        def lose_close_ack(fd):
            nonlocal lost_ack
            if target and fd == target[0]:
                attempts.append(fd)
                close(fd)
                if not lost_ack:
                    lost_ack = True
                    os.dup2(foreign_source, fd)
                    raise OSError('synthetic close acknowledgment lost')
                return
            close(fd)
        try:
            with patch.object(self.module.subprocess, 'Popen', side_effect=fail_popen):
                with patch.object(self.module.os, 'close', side_effect=lose_close_ack):
                    with self.assertRaises(self.module.CoreError):
                        self.runtime._launch(self.runtime.root / (self.rev + '.json'))
            self.assertEqual(attempts, target, 'released config descriptor must never be closed twice')
            self.assertEqual(os.fstat(target[0]).st_ino, os.fstat(foreign_source).st_ino,
                             'foreign descriptor reused after close must remain open')
            self.assertIsNone(self.runtime._config_fd)
            self.assertIsNone(self.runtime._binary_fd)
            self.assertIsNone(self.runtime._operation_binary_fd)
        finally:
            if target:
                try:
                    close(target[0])
                except OSError:
                    pass
            close(foreign_source)

    def test_close_error_releases_other_generation_fds_lease_and_root(self):
        # No child or execution: manually owned fake descriptors exercise cleanup.
        self.runtime._config_fd = os.open(self.path, os.O_RDONLY)
        self.runtime._binary_fd = os.open(self.path, os.O_RDONLY)
        config_fd, binary_fd = self.runtime._config_fd, self.runtime._binary_fd
        lease_fd, root_fd = self.runtime.lease.fileno(), self.runtime._root_fd
        close = os.close
        def fail_config(fd):
            close(fd)
            if fd == config_fd:
                raise OSError('synthetic close failure after release')
        with patch.object(self.module.os, 'close', side_effect=fail_config):
            with self.assertRaisesRegex(self.module.CoreError, '^Runtime descriptor cleanup failed; core stopped$'):
                self.runtime.close()
        for fd in (config_fd, binary_fd, lease_fd, root_fd):
            with self.assertRaises(OSError):
                os.fstat(fd)
        self.assertIsNone(self.runtime._binary_fd)
        self.assertIsNone(self.runtime.lease)
        self.assertIsNone(self.runtime._root_fd)

    def test_runtime_rejects_mismatch_and_exposes_readonly_artifact(self):
        for name, path in (('xray', self.path), ('sing-box', self.anchor / 'elsewhere')):
            with self.assertRaises(self.module.CoreError):
                self.module.CoreRuntime(name, path, self.anchor / 'other', artifact=self.artifact)
        with self.assertRaises(AttributeError):
            self.runtime.artifact = None
        self.runtime.binary = self.anchor / 'elsewhere'
        with self.assertRaises(self.module.CoreError):
            with self.runtime._binary_operation():
                self.fail('changed instance binary accepted')


FAKE = '''#!{python}
import json,os,socket,sys,time
with open({log!r}, 'a') as log:
    log.write(sys.argv[1] + ':' + str(os.stat(sys.argv[0]).st_ino) + '\\n')
if sys.argv[1] == 'version':
    print('sing-box version 1.14.3'); sys.exit(0)
c = json.load(open(sys.argv[-1]))
if sys.argv[1] == 'check': sys.exit(3 if c.get('invalid') else 0)
if c.get('crash'): sys.exit(4)
s=socket.socket();s.bind(('127.0.0.1', c['inbounds'][0]['listen_port']));s.listen()
time.sleep(90)
'''


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux procfd/watchdog fake runtime acceptance')
class RuntimeArtifactLinuxTests(unittest.TestCase):
    def setUp(self):
        # Delayed real import: macOS pure tests do not require runtime packages.
        from app.services import core_runtime
        self.module = core_runtime
        temp = tempfile.TemporaryDirectory(prefix='vui-artifact-runtime-')
        self.addCleanup(temp.cleanup)
        self.anchor = Path(temp.name).resolve()
        self.path = self.anchor / 'sing-box'
        self.log = self.anchor / 'executions.log'
        self.path.write_text(FAKE.format(python=sys.executable, log=str(self.log)))
        self.path.chmod(0o555)
        self.artifact = artifact_for(self.path, self.anchor)
        self.runtime = core_runtime.CoreRuntime('sing-box', self.path,
            self.anchor / 'runtime', artifact=self.artifact)
        self.addCleanup(self.runtime.close)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.config = {'inbounds': [{'type': 'fake', 'listen_port': port}], 'value': 1}

    def assert_released(self):
        self.assertIsNone(self.runtime._binary_fd)
        self.assertIsNone(self.runtime._operation_binary_fd)
        self.assertIsNone(self.runtime._config_fd)

    def test_check_launch_uses_same_verified_inode_after_path_replaced(self):
        run = self.module.subprocess.run
        original = self.path.stat().st_ino
        def replace_after_version(command, **kwargs):
            result = run(command, **kwargs)
            if command[1] == 'version':
                replacement = self.anchor / 'replacement'
                replacement.write_text('#!/bin/sh\nexit 99\n')
                replacement.chmod(0o555)
                os.replace(replacement, self.path)
            return result
        with patch.object(self.module.subprocess, 'run', side_effect=replace_after_version):
            self.runtime.apply(self.config)
        rows = self.log.read_text().splitlines()
        self.assertEqual(rows, ['version:' + str(original), 'check:' + str(original), 'run:' + str(original)])
        fd = self.runtime._binary_fd
        self.assertEqual(os.fstat(fd).st_ino, original)
        self.assertEqual(self.runtime.execution_identity()['running'], self.runtime.execution_identity()['selected'])
        self.runtime.stop()
        self.assert_released()
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_validate_only_and_check_failure_close_all_fds(self):
        self.runtime.apply(self.config, activate=False)
        self.assertFalse(self.runtime.state_file.exists())
        self.assert_released()
        with self.assertRaises(self.module.CoreError):
            self.runtime.apply({**self.config, 'invalid': True})
        self.assert_released()

    def test_version_mismatch_rejected_without_commit(self):
        self.runtime.close()
        artifact = replace(self.artifact, reference=replace(self.artifact.reference, version='1.14.4'))
        self.runtime = self.module.CoreRuntime('sing-box', self.path, self.anchor / 'runtime', artifact=artifact)
        self.addCleanup(self.runtime.close)
        with self.assertRaisesRegex(self.module.CoreError, 'version'):
            self.runtime.apply(self.config)
        self.assertFalse(self.runtime.state_file.exists())
        self.assert_released()

    def test_launch_failure_and_same_artifact_rollback_preserve_schema2(self):
        self.runtime.apply(self.config)
        prior = self.runtime.state_file.read_bytes()
        old_fd = self.runtime._binary_fd
        with self.assertRaises(self.module.CoreError):
            self.runtime.apply({**self.config, 'crash': True})
        self.assertEqual(self.runtime.state_file.read_bytes(), prior)
        self.assertTrue(self.runtime.status()['running'])
        self.assertEqual(json.loads(prior)['artifact_id'], self.artifact.artifact_id)
        self.runtime.close()
        self.assert_released()
        with self.assertRaises(OSError):
            os.fstat(old_fd)

    def test_coordinated_failure_stops_without_relaunching(self):
        self.runtime.apply(self.config)
        prior = self.runtime.state_file.read_bytes()
        with self.assertRaisesRegex(self.module.CoreError, 'coordinated'):
            self.runtime.apply({**self.config, 'crash': True}, rollback_on_failure=False)
        self.assertEqual(self.runtime.state_file.read_bytes(), prior)
        self.assertFalse(self.runtime.status()['running'])
        self.assert_released()
        self.runtime.restore(self.module.revision(self.config), enabled=True)
        self.assertTrue(self.runtime.status()['running'])

    def test_popen_failure_first_launch_does_not_leak_or_commit(self):
        with patch.object(self.module.subprocess, 'Popen', side_effect=OSError('fixture')):
            # Popen is also used by subprocess.run; replace check to isolate launch.
            with patch.object(self.runtime, 'check'):
                with self.assertRaises(self.module.CoreError):
                    self.runtime.apply(self.config)
        self.assertFalse(self.runtime.state_file.exists())
        self.assert_released()

    def test_exited_generation_recover_reclaims_fd_and_manual_stop_survives(self):
        self.runtime.apply(self.config)
        self.runtime.process.terminate()
        self.runtime.process.wait(timeout=5)
        self.runtime.recover()
        self.assertTrue(self.runtime.status()['running'])
        # FD numbers may be reused, so compare current ownership, not the number.
        self.assertIsNotNone(self.runtime._binary_fd)
        self.runtime.stop()
        self.runtime.close()
        self.runtime.recover()
        self.assertFalse(self.runtime.status()['running'])
        self.assert_released()
        self.assertFalse(json.loads(self.runtime.state_file.read_bytes())['enabled'])


if __name__ == '__main__':
    unittest.main()
