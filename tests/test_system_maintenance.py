"""Root control boundary tests: temporary files and mocks only, never host changes."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location('_test_' + name, SOURCE / 'deploy' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


daemon = module('maintenance_daemon')
upgrade = module('maintenance_upgrade')
cli = module('maintenance_cli_worker')
ops = module('system_ops')


@contextmanager
def no_lock(*args, **kwargs):
    yield


class ProtocolTests(unittest.TestCase):
    def test_failed_service_diagnostics_only_disclose_fixed_classifications(self):
        from scripts.system_maintenance_smoke import safe_maintenance_journal, maintenance_diagnostics
        raw = ('\n'.join(json.dumps({'MESSAGE': text}) for text in (
            'V-UI maintenance startup refused: STARTUP_SOCKET_DIRECTORY private-token-secret',
            'ModuleNotFoundError private-grant-secret', 'arbitrary-url-password-secret'))).encode()
        self.assertEqual(safe_maintenance_journal(raw), ['STARTUP_SOCKET_DIRECTORY', 'MODULE_MISSING', 'UNCLASSIFIED'])
        with patch('scripts.system_maintenance_smoke.bounded_diagnostic', side_effect=[
                b'ActiveState=failed\nExecMainStatus=1\nSecret=private-grant-secret\n', raw]) as run:
            result = maintenance_diagnostics()
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(result['ExecMainStatus'], '1')
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            self.assertIn('v-ui-maintenance.service', ' '.join(call.args[0]))

    def test_private_umask_creates_exact_socket_directory_mode_via_new_fd_only(self):
        @contextmanager
        def directory(path):
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try: yield fd
            finally: os.close(fd)
        real_fstat = os.fstat
        def root_info(fd):
            info = real_fstat(fd)
            return types.SimpleNamespace(st_uid=0, st_gid=info.st_gid, st_mode=info.st_mode,
                st_dev=info.st_dev, st_ino=info.st_ino)
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'socket-parent'
            previous = os.umask(0o077)
            try:
                with patch.object(daemon.os, 'fstat', side_effect=root_info), patch.object(daemon.os, 'fchown'):
                    daemon.directory(types.SimpleNamespace(trusted_directory=directory), target, 0o750, os.getgid())
                self.assertEqual(target.stat().st_mode & 0o777, 0o750)
                # Existing metadata is not silently repaired or re-endorsed.
                target.chmod(0o700)
                with patch.object(daemon.os, 'fstat', side_effect=root_info), patch.object(daemon.os, 'fchmod') as chmod:
                    with self.assertRaises(ValueError):
                        daemon.directory(types.SimpleNamespace(trusted_directory=directory), target, 0o750, os.getgid())
                    chmod.assert_not_called()
            finally:
                os.umask(previous)

    def test_installed_summary_exposes_only_fixed_safe_version_target_readiness(self):
        config = {'release_id': '0.4.17-' + 'a' * 12, 'target': 'x86_64-gnu', 'ready': True,
                  'domain': 'private.example.test', 'email': 'private@example.test', 'admin': 'private', 'bootstrap_python': '/private/path'}
        fake_ops = types.SimpleNamespace(trusted_bytes=lambda path, limit: json.dumps(config).encode(),
            decode_json=ops.decode_json, CONFIG=Path('/etc/v-ui/service.json'), RELEASE=ops.RELEASE)
        result = daemon.install_summary(fake_ops)
        self.assertEqual(set(result), {'release_id', 'target', 'ready'})
        self.assertNotIn('private', json.dumps(result))
        config['release_id'] = '/private/path'
        with self.assertRaises(ValueError): daemon.install_summary(fake_ops)

    def test_capacity_prunes_only_ended_nonbackup_records_and_reserves_operating_slots(self):
        @contextmanager
        def directory(path):
            fd = os.open(path, os.O_RDONLY)
            try: yield fd
            finally: os.close(fd)
        fake_ops = types.SimpleNamespace(trusted_bytes=lambda path, limit: path.read_bytes(),
            decode_json=ops.decode_json, trusted_directory=directory)
        with tempfile.TemporaryDirectory() as temporary, patch.object(daemon, 'TASKS', Path(temporary)), patch.object(daemon, 'MAX_TASKS', 4), patch.object(daemon, 'MAX_BACKUPS', 1):
            store = daemon.Store(fake_ops)
            backup = {'id': 'a' * 32, 'action': 'backup', 'state': 'succeeded', 'created_at': 1,
                      '_backup': {'name': 'kept.zip', 'sha256': 'f' * 64}}
            active = {'id': 'b' * 32, 'action': 'restart', 'state': 'running', 'created_at': 2}
            store.write(backup); store.write(active)
            for number in (3, 4):
                store.write({'id': str(number) * 32, 'action': 'status', 'state': 'succeeded', 'created_at': number})
            with self.assertRaises(ValueError): store.create('backup')
            status = store.create('status')
            self.assertEqual(status['state'], 'queued')
            status['state'] = 'succeeded'; store.write(status)
            start = store.create('start')
            self.assertEqual(start['state'], 'queued')
            self.assertEqual(store.read(active['id'])['state'], 'running')
            self.assertEqual(store.read(backup['id'])['_backup'], backup['_backup'])
            self.assertEqual(len(store.all()), 4)

    def test_journal_only_returns_fixed_classifications_and_discards_all_raw_text(self):
        records = [
            {'_SYSTEMD_UNIT': 'v-ui.service', '__REALTIME_TIMESTAMP': '1700000000000000', 'MESSAGE': 'PermissionError https://secret/?token=PRIVATE'},
            {'_SYSTEMD_UNIT': 'v-ui-http01.service', 'MESSAGE': 'Bearer PRIVATE arbitrary private key text'},
            {'_SYSTEMD_UNIT': 'sshd.service', 'MESSAGE': 'PermissionError SSH_PRIVATE'},
        ]
        result = daemon.safe_logs(('\n'.join(json.dumps(row) for row in records)).encode())
        self.assertEqual([row['code'] for row in result], ['PERMISSION_DENIED', 'UNCLASSIFIED'])
        self.assertEqual(result[0]['timestamp'], 1700000000)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('MESSAGE', json.dumps(result))

    def test_protocol_closed_actions_fields_and_fixed_backup_identity(self):
        for action in daemon.ACTIONS:
            value = {'protocol': 1, 'operation': 'submit', 'action': action}
            if action == 'restore': value['backup_id'] = 'a' * 32
            self.assertEqual(daemon.validate(value), 'submit')
        for change in ({'url': 'https://evil.test'}, {'unit': 'sshd.service'}, {'command': 'id'},
                       {'path': '/etc/shadow'}, {'action': 'stage'}, {'protocol': True},
                       {'action': 'restore', 'backup_id': '../escape'}, {'action': 'restore'},
                       {'action': 'status', 'backup_id': 'a' * 32}):
            value = {'protocol': 1, 'operation': 'submit', 'action': 'upgrade', **change}
            with self.subTest(change=change), self.assertRaises((ValueError, KeyError)):
                daemon.validate(value)

    def test_receive_checks_kernel_peer_and_bounded_json_duplicate_keys(self):
        connection = Mock()
        connection.getsockopt.return_value = struct.pack('3i', 9, 991, 991)
        connection.recv.side_effect = [b'{"protocol":1,"operation":"status"}\n', b'']
        with patch.object(socket, 'SO_PEERCRED', 17, create=True):
            self.assertEqual(daemon.receive(connection, ops, 991)['operation'], 'status')
            connection.getsockopt.return_value = struct.pack('3i', 9, 992, 991)
            with self.assertRaises(ValueError): daemon.receive(connection, ops, 991)
            connection.getsockopt.return_value = struct.pack('3i', 9, 991, 991)
            for raw in (b'x' * 4097, b'{"protocol":1,"protocol":1,"operation":"status"}\n',
                        b'{"protocol":1,"operation":"status"}\ntrailing', b'{}'):
                connection.recv.side_effect = [raw, b'']
                with self.subTest(raw=raw[:20]), self.assertRaises((ValueError, ops.Refused)):
                    daemon.receive(connection, ops, 991)

    def test_terminal_grants_allow_only_fixed_nonroot_subcommands(self):
        self.assertEqual(cli.arguments(['list']), ['list'])
        self.assertEqual(cli.arguments(['revoke', '1']), ['revoke', '--id', '1'])
        self.assertEqual(cli.arguments(['create', 'diagnose,nodes', '1800']), ['create', '--scopes', 'diagnose,nodes', '--seconds', '1800'])
        for value in (['-c', 'id'], ['revoke', '../1'], ['create', 'admin', '1800'],
                      ['create', 'diagnose', '7201'], ['create', 'diagnose,diagnose', '1800']):
            with self.subTest(value=value), self.assertRaises(ValueError): cli.arguments(value)

    def test_store_persists_interruption_and_hides_internal_backup_data(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(daemon, 'TASKS', Path(temporary)):
            fake_ops = types.SimpleNamespace(trusted_bytes=lambda path, limit: path.read_bytes(),
                decode_json=ops.decode_json, trusted_directory=no_lock)
            # fsync requires an actual directory fd, without a fake root trust.
            @contextmanager
            def directory(path):
                fd = os.open(path, os.O_RDONLY)
                try: yield fd
                finally: os.close(fd)
            fake_ops.trusted_directory = directory
            store = daemon.Store(fake_ops)
            row = {'id': 'a' * 32, 'action': 'backup', 'state': 'running', 'created_at': 1,
                   '_backup': {'name': 'private', 'sha256': 'secret'}, 'backup_id': 'internal'}
            store.write(row)
            self.assertEqual((Path(temporary) / (row['id'] + '.json')).stat().st_mode & 0o777, 0o600)
            store.interrupt()
            restored = store.read(row['id'])
            self.assertEqual(restored['state'], 'failed')
            self.assertEqual(restored['error'], 'MAINTENANCE_INTERRUPTED')
            self.assertNotIn('_backup', daemon.public(restored))
            self.assertNotIn('backup_id', daemon.public(restored))

    def test_root_menu_never_launches_application_as_root(self):
        account = types.SimpleNamespace(pw_uid=101, pw_gid=102)
        with patch.object(ops, 'read_input', side_effect=['2']), patch.object(ops, 'verify_installation', return_value=('systemd', account)), patch.object(ops.subprocess, 'run') as run:
            ops.terminal_grants()
        self.assertEqual(run.call_args.kwargs['user'], 101)
        self.assertEqual(run.call_args.kwargs['group'], 102)
        self.assertEqual(run.call_args.kwargs['extra_groups'], [])
        self.assertEqual(run.call_args.args[0][-1], 'list')
        self.assertNotIn('PYTHONPATH', run.call_args.kwargs['env'])

    def test_terminal_grants_use_chinese_presets_and_default_readonly(self):
        account = types.SimpleNamespace(pw_uid=101, pw_gid=102)
        for answers, scopes, seconds in ((['1', '', ''], 'diagnose', '1800'),
                (['1', '3', '60'], 'diagnose,nodes,share,relay,certificates,operate,upgrade,destructive', '3600')):
            with self.subTest(answers=answers), patch.object(ops, 'read_input', side_effect=answers), patch.object(ops, 'verify_installation', return_value=('systemd', account)), patch.object(ops.subprocess, 'run') as run:
                ops.terminal_grants()
                self.assertEqual(run.call_args.args[0][-3:], ['create', scopes, seconds])


class UpgradeTests(unittest.TestCase):
    def test_unattended_upgrade_cannot_create_or_request_initial_credentials(self):
        from scripts import install_system as installer
        with patch.object(installer, 'installer_account', return_value=False) as account, patch.object(installer, 'controlling_terminal', side_effect=AssertionError('no terminal')):
            with self.assertRaises(installer.InstallError):
                with installer.provision_initial_account('/runtime', '/payload', {'admin': 'existing'}, require_existing=True):
                    self.fail('missing account was accepted')
        self.assertEqual(account.call_args.args[2], 'installer-status')
        self.assertEqual(account.call_count, 1)

    def manifest(self):
        raw = ''.join('a' * 64 + '  ' + name + '\n' for name in sorted(upgrade.ASSETS | upgrade.BUNDLES)).encode()
        return raw, hashlib.sha256(raw).hexdigest()

    def test_manifest_is_complete_pinned_closed_and_duplicate_free(self):
        raw, pin = self.manifest()
        self.assertEqual(set(upgrade.checksums(raw, pin)), upgrade.ASSETS | upgrade.BUNDLES)
        for changed in (raw + b'b' * 64 + b'  arbitrary.sh\n', raw + raw.splitlines(keepends=True)[0],
                        raw.replace(b'  install.sh\n', b'  ../install.sh\n')):
            with self.subTest(changed=changed[-80:]), self.assertRaises(upgrade.UpgradeRefused):
                upgrade.checksums(changed, hashlib.sha256(changed).hexdigest())
        with self.assertRaises(upgrade.UpgradeRefused): upgrade.checksums(raw, '0' * 64)

    def test_fixed_https_host_boundary_has_no_proxy_or_unverified_tls(self):
        for value in (upgrade.SETUP, upgrade.LATEST, upgrade.BASE + 'v0.4.17/SHA256SUMS'):
            upgrade.url_allowed(value)
        for value in ('http://github.com/x', 'https://github.com.evil.test/x', 'https://evil.test/x',
                      'https://u@github.com/x', 'https://github.com:4430/x', 'file:///etc/passwd'):
            with self.subTest(value=value), self.assertRaises(upgrade.UpgradeRefused): upgrade.url_allowed(value)

    def test_canonical_pin_and_official_stable_release_must_agree(self):
        setup = b'  local version=v0.4.17 version_seen=0 argument domain=\n  local kit_sha=' + b'a' * 64 + b'\n'
        release = {'tag_name': 'v0.4.17', 'draft': False, 'prerelease': False, 'published_at': '2026-11-04'}
        with patch.object(upgrade, 'download', side_effect=[setup, json.dumps(release).encode()]):
            self.assertEqual(upgrade.accepted_release(ops), ('v0.4.17', 'a' * 64))
        for change in ({'tag_name': 'v0.4.18'}, {'draft': True}, {'prerelease': True}, {'published_at': None}):
            with self.subTest(change=change), patch.object(upgrade, 'download', side_effect=[setup, json.dumps({**release, **change}).encode()]):
                with self.assertRaises(upgrade.UpgradeRefused): upgrade.accepted_release(ops)

    def fake_ops(self, config):
        return types.SimpleNamespace(installation_lock=no_lock, verify_installation=lambda: ('systemd', object()),
            decode_json=ops.decode_json, trusted_bytes=lambda path, limit: json.dumps(config).encode(),
            CONFIG=Path('/etc/v-ui/service.json'), PYTHON=Path('/usr/bin/python3'), CONTROL=Path('/usr/local/lib/v-ui'), ENV=ops.ENV,
            trusted_directory=no_lock)

    def config(self):
        return {'installer': 1, 'ready': True, 'release_id': '0.4.17-aaaaaaaaaaaa', 'certificate_mode': 'managed',
                'domain': 'panel.example.test', 'target': 'x86_64-gnu',
                'ca_authorizations': [{'domain': 'panel.example.test', 'environment': 'production'}]}

    def test_downgrade_and_same_version_do_not_download_or_mutate(self):
        for version in ('v0.4.16', 'v0.4.17'):
            with patch.object(upgrade, 'accepted_release', return_value=(version, 'a' * 64)), patch.object(upgrade, 'download') as download, patch.object(upgrade, 'run') as run:
                result = upgrade.upgrade(self.fake_ops(self.config()))
            self.assertFalse(result['upgraded']); download.assert_not_called(); run.assert_not_called()

    def test_missing_ca_authorization_refuses_before_any_network(self):
        config = self.config(); config['ca_authorizations'] = []
        with patch.object(upgrade, 'accepted_release') as accepted:
            with self.assertRaises(upgrade.UpgradeRefused): upgrade.upgrade(self.fake_ops(config))
        accepted.assert_not_called()

    def test_full_download_chain_uses_manifest_before_verifier_and_fixed_unattended_flags(self):
        config = self.config(); config['release_id'] = '0.4.16-aaaaaaaaaaaa'
        contents = {name: b'accepted fake bytes' for name in upgrade.ASSETS | upgrade.BUNDLES}
        contents['RELEASE.json'] = json.dumps({'version': '0.4.17', 'release_id': '0.4.17-' + 'a' * 12, 'source_commit': 'a' * 40}).encode()
        raw = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n' for name, data in sorted(contents.items())).encode()
        pin = hashlib.sha256(raw).hexdigest()
        downloaded = []
        def download(url, path=None, **kwargs):
            if path is None: return raw
            downloaded.append(path.name)
            data = contents[path.name]; path.write_bytes(data)
            return hashlib.sha256(data).hexdigest()
        temporary = tempfile.TemporaryDirectory()
        with temporary as folder, patch.object(upgrade, 'accepted_release', return_value=('v0.4.17', pin)), patch.object(upgrade.tempfile, 'TemporaryDirectory', return_value=no_lock()), patch.object(upgrade, 'download', side_effect=download), patch.object(upgrade, 'run') as run:
            # A root-only staging location is simulated, never installed locally.
            @contextmanager
            def work(**kwargs): yield folder
            with patch.object(upgrade.tempfile, 'TemporaryDirectory', side_effect=work):
                result = upgrade.upgrade(self.fake_ops(config))
        self.assertTrue(result['upgraded'])
        self.assertEqual(set(downloaded) & upgrade.BUNDLES, {'vui-linux-x86_64-gnu.zip'})
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 3)
        self.assertIn('--preflight', commands[0]); self.assertIn('--unattended-upgrade', commands[1]); self.assertIn('--refresh', commands[2])
        for command in commands:
            self.assertNotIn('--interactive', command); self.assertNotIn('--accept-terms', command)


if __name__ == '__main__': unittest.main()
