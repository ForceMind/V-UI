"""Offline guard regressions with temporary metadata and no service operations."""
from contextlib import contextmanager
import io
import json
import os
import struct
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from app import data_owner, release_tools as tools
from app.core_updates import selection_files
from deploy import system_ops
from scripts import install_system

TRACES = (selection_files.SELECTION, selection_files.FENCE, selection_files.PENDING,
          selection_files.ACTIVE, selection_files.HISTORY, selection_files.LOCK)
LEGACY_STATE = {'revision': 'a' * 64, 'enabled': False}
MODERN_STATE = dict(LEGACY_STATE, schema=2, artifact_id='b' * 64)


class CoreOfflineGuardTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='vui-offline-guards-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.config = self.root / 'etc-v-ui'
        self.config.mkdir(mode=0o700)
        self.install = self.root / 'install'
        self.install.mkdir(mode=0o700)
        self.data = self.install / 'data'
        self.data.mkdir(mode=0o700)
        patcher = patch.object(data_owner, '_CORE_UPDATE_PENDING', self.config / selection_files.PENDING)
        patcher.start()
        self.addCleanup(patcher.stop)

    def state(self, value):
        directory = self.data / 'runtime' / 'xray'
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / 'state.json'
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        return path

    def test_every_metadata_trace_blocks_legacy_data_operations(self):
        for name in TRACES:
            with self.subTest(name=name):
                path = self.config / name
                path.symlink_to(self.root / 'absent')
                try:
                    self.assertTrue(data_owner.core_selection_present())
                    with self.assertRaisesRegex(tools.ReleaseError, '独立核心'):
                        with tools._legacy_stopped(self.install):
                            self.fail('legacy offline operation was admitted')
                    self.assertFalse((self.install / '.operation.lock').exists())
                finally:
                    path.unlink()
        self.assertFalse(data_owner.core_selection_present())

    def test_absent_selector_does_not_allow_orphan_modern_state(self):
        with tools._legacy_stopped(self.install):
            pass
        path = self.state(LEGACY_STATE)
        with tools._legacy_stopped(self.install):
            pass
        self.state(MODERN_STATE)
        before = path.read_bytes()
        with self.assertRaisesRegex(tools.ReleaseError, '核心运行状态'):
            tools.backup(self.install, self.root / 'invalid.zip')
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.root / 'invalid.zip').exists())

    def test_absent_runtime_under_symlink_is_not_a_legacy_missing_state(self):
        (self.data / 'runtime').symlink_to(self.root / 'missing', target_is_directory=True)
        with self.assertRaisesRegex(tools.ReleaseError, '核心运行状态'):
            tools._require_legacy_runtime_states(self.data)

    def test_legacy_start_refuses_missing_config_and_duplicate_state_keys(self):
        path = self.state(LEGACY_STATE)
        session = tools._MaintenanceSession(self.install)
        with self.assertRaisesRegex(tools.ReleaseError, '启动或重启'):
            session._verify_start_cores()
        path.write_text('{"revision":"' + 'a' * 64 + '","enabled":false,"enabled":true}')
        with self.assertRaisesRegex(tools.ReleaseError, '核心运行状态'):
            tools._require_legacy_runtime_states(self.data)

    def test_managed_prepare_refuses_incompatible_actions_before_verifying_app(self):
        (self.config / selection_files.FENCE).write_text('incomplete')
        for action, arguments in (('activate', {'identity': 'candidate'}), ('backup', {}),
                                  ('recover-restore', {}), ('restore', {'archive': self.root / 'fake.zip',
                                                                        'sha256': 'a' * 64})):
            with self.subTest(action=action), patch.object(tools, 'supported_environment'), \
                    patch.object(tools, 'active_verified') as verify:
                session = tools._MaintenanceSession(self.install)
                try:
                    with self.assertRaisesRegex(tools.ReleaseError, '独立核心'):
                        session.prepare(action, **arguments)
                    verify.assert_not_called()
                finally:
                    session.close()

    def test_start_reuses_resolver_for_selected_components(self):
        (self.config / selection_files.SELECTION).write_text('candidate')
        from app.core_updates import boot, maintenance
        application = (self.install, {}, {'source_commit': 'a' * 40}, {'runtime_key': 'x86_64-gnu'})
        session = tools._MaintenanceSession(self.install)
        session._active_application = application
        versions = {'sing-box': '1.14.2', 'xray': '26.3.27'}
        with patch.object(maintenance, '_versions', return_value=versions), \
                patch.object(boot, 'resolve_boot', return_value={}) as resolve:
            session._verify_start_cores()
            resolve.assert_called_once_with(payload=self.install / 'payload', manifest=application[2],
                                            ready=application[3], data=self.data, versions=versions)
            resolve.side_effect = boot.CoreBootError('CORE_SELECTION_UNAVAILABLE')
            with self.assertRaisesRegex(tools.ReleaseError, '启动或重启'):
                session._verify_start_cores()

    def test_legacy_backup_preflight_refuses_new_kind_and_disguised_state(self):
        for kind, state, expected in (('backup', LEGACY_STATE, None),
                                      ('backup-dns01-v1', LEGACY_STATE, None),
                                      ('backup', MODERN_STATE, '独立核心'),
                                      ('backup-components-v1', LEGACY_STATE, 'unsupported format')):
            with self.subTest(kind=kind, state=state):
                stream = io.BytesIO()
                with zipfile.ZipFile(stream, 'w') as archive:
                    archive.writestr(tools.MANIFEST, json.dumps({'kind': kind, 'data_path': str(self.data)}))
                    archive.writestr('runtime/xray/state.json', json.dumps(state))
                if expected is None:
                    tools._preflight_legacy_backup(stream, self.data)
                else:
                    with self.assertRaisesRegex(tools.ReleaseError, expected):
                        tools._preflight_legacy_backup(stream, self.data)

    def test_large_central_directory_rejected_before_zipfile_allocation(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr(tools.MANIFEST, '{}')
        end = zipfile._EndRecData(stream)
        end[zipfile._ECD_SIZE] = 16_000_001
        with patch.object(zipfile, '_EndRecData', return_value=end), \
                patch.object(zipfile, 'ZipFile') as open_archive:
            with self.assertRaisesRegex(tools.ReleaseError, 'ZIP directory'):
                tools._preflight_legacy_backup(stream, self.data)
            open_archive.assert_not_called()

    def test_lying_central_directory_count_refuses_before_zipfile_allocation(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr(tools.MANIFEST, '{}')
            archive.writestr('data', 'fake')
        raw = bytearray(stream.getvalue())
        eocd = raw.rindex(b'PK\x05\x06')
        struct.pack_into('<HH', raw, eocd + 8, 1, 1)
        with patch.object(zipfile, 'ZipFile') as open_archive:
            with self.assertRaisesRegex(tools.ReleaseError, 'ZIP directory'):
                tools._preflight_legacy_backup(io.BytesIO(raw), self.data)
            open_archive.assert_not_called()

    def test_root_checks_pending_before_start_fast_path_and_allows_stop(self):
        @contextmanager
        def directory(path):
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                yield fd
            finally:
                os.close(fd)

        with patch.object(system_ops, 'CONFIG', self.config / 'service.json'), \
                patch.object(system_ops, 'trusted_directory', directory):
            system_ops.require_core_compatibility('backup')
            (self.config / selection_files.SELECTION).write_text('selected')
            system_ops.require_core_compatibility('start')
            system_ops.require_core_compatibility('stage')
            with self.assertRaises(system_ops.Refused):
                system_ops.require_core_compatibility('upgrade')
            (self.config / selection_files.PENDING).mkdir()
            system_ops.require_core_compatibility('stop')
            services = Mock()
            with self.assertRaises(system_ops.Refused):
                system_ops.operate({'protocol': 1, 'action': 'start'}, services, Mock(), [])
            self.assertEqual(services.mock_calls, [])

    def test_installer_blocks_old_transaction_recovery_before_any_service_change(self):
        with patch.object(install_system, 'CONFIG_DIR', self.config), \
                patch.object(install_system.service_support, 'stop') as stop:
            install_system.require_legacy_core_installation()
            transaction = self.config / install_system.TRANSACTION_NAME
            transaction.write_text('{}')
            (self.config / selection_files.ACTIVE).mkdir()
            with self.assertRaisesRegex(install_system.InstallError, '独立核心'):
                install_system.recover_transaction(self.root)
            stop.assert_not_called()
            self.assertTrue(transaction.exists())


if __name__ == '__main__':
    unittest.main()
