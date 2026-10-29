"""Explicit control installation in fake temporary roots only, never host sudo."""
import argparse
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import build_release_kit, install_ops_control as installer, service_support

REPO = Path(__file__).resolve().parents[1]
REAL_RUN = subprocess.run


class ControlInstallTests(unittest.TestCase):
    def setUp(self):
        previous_umask = os.umask(0o022)
        self.addCleanup(os.umask, previous_umask)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Keep all paths under the checkout; never relax /tmp sticky handling in
        # the production root-path checker just to make a fixture work.
        self.base = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=REPO)))
        self.kit = self.base / 'kit'
        self.kit.mkdir()
        self.control = self.base / 'usr/local/lib/v-ui'
        self.entry = self.base / 'usr/local/sbin/v-ui'
        self.command_alias = self.base / 'usr/local/bin/v-ui'
        self.config = self.base / 'etc/v-ui/service.json'
        self.units = self.base / 'etc/systemd/system'
        self.openrc = self.base / 'etc/init.d'
        self.root = self.base / 'var/lib/v-ui'
        self.python = self.base / 'usr/bin/python3'
        self.real_python = self.python.with_name('python3.12')
        self.lock = self.base / 'run/lock/v-ui-install.lock'
        for path in (self.control, self.entry.parent, self.command_alias.parent, self.config.parent, self.units,
                     self.openrc, self.root, self.python.parent, self.lock.parent):
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(0o755)
        self.root.chmod(0o700)
        self.real_python.write_bytes(b'fake OS executable, never executed')
        self.real_python.chmod(0o755)
        self.python.symlink_to(self.real_python.name)
        self.account = argparse.Namespace(pw_uid=os.getuid(), pw_gid=os.getgid())
        if not self.account.pw_uid or not self.account.pw_gid:
            self.skipTest('Temporary non-root fixture required; never exercise root installation')
        self.stack.enter_context(patch.multiple(installer, ROOT=self.root, CONFIG=self.config,
            CONTROL=self.control, ENTRY=self.entry, COMMAND_ALIAS=self.command_alias, RECEIPT=self.control / 'ops-control.json',
            LAUNCHER=self.control / 'launcher.py', SYSTEM_PYTHON=self.python,
            SYSTEMD_DIR=self.units, OPENRC_DIR=self.openrc, LOCK=self.lock))
        self.stack.enter_context(patch.object(installer, 'owned_by_root', return_value=True))
        self.stack.enter_context(patch.object(installer.os, 'geteuid', return_value=0))
        self.stack.enter_context(patch.object(installer.pwd, 'getpwnam', return_value=self.account))
        self.stack.enter_context(patch.object(installer.platform, 'system', return_value='Linux'))
        self.stack.enter_context(patch.object(installer, 'local_target', return_value='x86_64-gnu'))
        self.run = self.stack.enter_context(patch.object(installer.subprocess, 'run',
            return_value=subprocess.CompletedProcess([], 0, self.probe(), '')))
        self.config_value = {'installer': 1, 'ready': True, 'service_manager': 'systemd',
            'ipv6': False, 'target': 'x86_64-gnu',
            'bootstrap_python': '/var/lib/v-ui/releases/fixture/runtime/python/bin/python3'}
        self.write_instance()
        self.write_kit()

    def probe(self, *, version=None, capabilities=None):
        return json.dumps({'version': version or [3, 12], 'capabilities': capabilities if capabilities is not None
                           else {name: True for name in installer.PROBE_CAPABILITIES}}) + '\n'

    def write_instance(self, manager='systemd'):
        self.config_value['service_manager'] = manager
        self.config.write_text(json.dumps(self.config_value))
        self.config.chmod(0o644)
        (self.control / 'launcher.py').write_bytes((REPO / 'deploy/system_launcher.py').read_bytes())
        (self.control / 'launcher.py').chmod(0o644)
        with patch.multiple(service_support, SYSTEMD_DIR=self.units, OPENRC_DIR=self.openrc):
            for path, (value, mode) in service_support.service_files(self.config_value).items():
                path.write_text(value)
                path.chmod(mode)

    def write_kit(self):
        for name in installer.COMMON_ASSETS:
            (self.kit / name).write_bytes(b'accepted non-code fixture')
        for source, name in build_release_kit.SOURCE_ASSETS:
            (self.kit / name).write_bytes((REPO / source).read_bytes())
        (self.kit / 'vui-linux-x86_64-gnu.zip').write_bytes(b'archive never executed or extracted')
        (self.kit / 'RELEASE.json').write_text(json.dumps({'schema': 1, 'platform': 'linux-multi-cpython312',
            'targets': ['x86_64-gnu'], 'source_commit': 'a' * 40}))
        with zipfile.ZipFile(self.kit / 'vui-source.zip', 'w') as source:
            source.comment = b'a' * 40
            for name, member in installer.SOURCE_MEMBERS.items():
                source.writestr(member, (self.kit / name).read_bytes())
            for member in installer.SOURCE_DEPENDENCIES.values():
                source.writestr(member, (REPO / member).read_bytes())
        self.rehash()

    def rehash(self):
        raw = ''.join(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n'
                      for path in sorted(self.kit.iterdir()) if path.name != 'SHA256SUMS').encode()
        (self.kit / 'SHA256SUMS').write_bytes(raw)
        self.sha = hashlib.sha256(raw).hexdigest()

    def install(self, **kwargs):
        return installer.install(self.kit, self.sha, **kwargs)

    def test_preflight_authenticates_without_publishing_or_requiring_a_new_instance(self):
        with patch.object(installer, 'inspect_instance', side_effect=AssertionError('no instance access')), \
                patch.object(installer, 'publish', side_effect=AssertionError('no publication')):
            verified, record, interpreter = installer.preflight(self.kit, self.sha)
        self.assertEqual(record['source_commit'], 'a' * 40)
        self.assertIn('v-ui', verified)
        self.assertEqual(interpreter['system_python'], str(self.python))
        self.assert_not_published()
        self.assertFalse(os.path.lexists(self.command_alias))

    def test_incomplete_instance_may_repair_missing_units_but_not_changed_control(self):
        self.install()
        self.config_value['ready'] = False
        self.config.write_text(json.dumps(self.config_value))
        for path in self.units.iterdir(): path.unlink()
        installer.preflight(self.kit, self.sha)
        # Publishing a global menu still requires the completed canonical instance.
        with self.assertRaises(installer.InstallError): self.install(refresh=True)
        helper = self.control/'system_ops.py'
        helper.write_bytes(helper.read_bytes()+b'\n# untrusted drift\n')
        with self.assertRaisesRegex(installer.InstallError,'control code drift'):
            installer.preflight(self.kit,self.sha)

    def test_complete_instance_cannot_hide_missing_units_as_incomplete_repair(self):
        self.install()
        next(self.units.iterdir()).unlink()
        with self.assertRaises((installer.InstallError,OSError)):
            installer.preflight(self.kit,self.sha)

    def test_preflight_refuses_unknown_command_or_control_bytes_before_installation(self):
        for path in (self.command_alias, self.entry, self.control / 'system_ops.py'):
            with self.subTest(path=path):
                path.write_bytes(b'unrelated command, must remain unchanged')
                path.chmod(0o755 if path in (self.command_alias, self.entry) else 0o644)
                before = path.read_bytes()
                with self.assertRaises(installer.InstallError):
                    installer.preflight(self.kit, self.sha)
                self.assertEqual(path.read_bytes(), before)
                path.unlink()

    def test_absent_command_directory_is_searchable_despite_private_umask(self):
        self.command_alias.parent.rmdir()
        previous = os.umask(0o077)
        try:
            self.install()
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(self.command_alias.parent.stat().st_mode), 0o755)
        self.assertEqual(os.readlink(self.command_alias), str(self.entry))

    def test_unknown_entry_is_explained_even_before_control_directory_exists(self):
        self.entry.write_bytes(b'foreign program')
        self.entry.chmod(0o755)
        missing=self.base/'uncreated-control'
        with patch.multiple(installer,CONTROL=missing,RECEIPT=missing/'ops-control.json'):
            with self.assertRaisesRegex(installer.InstallError,'differs from this trusted kit'):
                installer.preflight(self.kit,self.sha)
        self.assertFalse(missing.exists())
        self.assertEqual(self.entry.read_bytes(),b'foreign program')

    def test_alias_publication_failure_disables_the_new_controller(self):
        real_replace = os.replace
        def replace(source, destination):
            if Path(destination) == self.command_alias:
                raise OSError('simulated alias publication failure')
            return real_replace(source, destination)
        with patch.object(installer.os, 'replace', side_effect=replace):
            with self.assertRaises(OSError):
                self.install()
        self.assert_not_published()
        self.assertFalse(os.path.lexists(self.command_alias))
        self.assertEqual(list(self.command_alias.parent.glob('.vui-command-*')), [])

    def assert_not_published(self):
        self.assertFalse(self.entry.exists())
        self.assertFalse(installer.RECEIPT.exists())

    def test_installs_exact_receipt_and_real_code_without_service_or_user_commands(self):
        receipt = self.install()
        self.assertEqual(set(receipt), installer.RECEIPT_KEYS)
        self.assertEqual(receipt['schema'], 1)
        self.assertEqual(receipt['interpreter_resolved'], str(self.real_python))
        self.assertEqual(receipt, json.loads(installer.RECEIPT.read_bytes()))
        expected = {self.config, self.control / 'launcher.py', self.entry,
                    *(self.control / name for name in ('system_ops.py', 'ops_worker.py', 'release_tools.py', *installer.SOURCE_DEPENDENCIES)),
                    *(self.units / name for name in ('v-ui.service', 'v-ui-http01.service', 'v-ui-http01.socket'))}
        self.assertEqual(set(receipt['files']), {str(path) for path in expected})
        for path in expected:
            self.assertEqual(receipt['files'][str(path)], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755 if path == self.entry else 0o644)
            self.assertFalse(path.stat().st_mode & (stat.S_ISUID | stat.S_ISGID))
        for path, (name, _) in installer.destinations().items():
            expected_bytes = ((REPO / installer.SOURCE_DEPENDENCIES[name]).read_bytes()
                              if name in installer.SOURCE_DEPENDENCIES else (self.kit / name).read_bytes())
            self.assertEqual(path.read_bytes(), expected_bytes)
        self.assertIn(b'/usr/bin/python3 -B -I -S /usr/local/lib/v-ui/system_ops.py', self.entry.read_bytes())
        self.assertTrue(self.command_alias.is_symlink())
        self.assertEqual(os.readlink(self.command_alias), str(self.entry))
        self.assertIn(b'def operate(', (self.control / 'system_ops.py').read_bytes())
        self.assertIn(b'class _MaintenanceSession(', (self.control / 'release_tools.py').read_bytes())
        self.assertEqual(len(self.run.call_args_list), 1)
        self.assertEqual(self.run.call_args.args[0][:4], [str(self.python), '-B', '-I', '-S'])
        self.assertEqual(stat.S_IMODE(self.lock.stat().st_mode), 0o600)

    def test_bad_manifest_digest_and_bad_code_hash_fail_before_any_host_write(self):
        for sha in ('0' * 64, '', 'A' * 64):
            with self.subTest(sha=sha), self.assertRaises(installer.InstallError):
                installer.install(self.kit, sha)
        (self.kit / 'system_ops.py').write_bytes(b'bad code')
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assert_not_published()
        self.run.assert_not_called()
        self.assertFalse(self.lock.exists())

    def test_all_assets_verified_before_executing_even_trusted_template(self):
        marker = self.base / 'must-not-exist'
        (self.kit / 'service_support.py').write_text('raise AssertionError("template ran before validation")')
        self.rehash()
        (self.kit / 'vui-source.zip').write_bytes(b'corrupt final large asset')
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertFalse(marker.exists())
        self.run.assert_not_called()

    def test_rehashed_control_from_another_source_or_commit_is_rejected(self):
        (self.kit / 'system_ops.py').write_bytes(b'new working-tree root code')
        self.rehash()
        with self.assertRaisesRegex(installer.InstallError, 'exact-commit source'):
            self.install()
        self.write_kit()
        with zipfile.ZipFile(self.kit / 'vui-source.zip', 'a') as source:
            source.comment = b'b' * 40
        self.rehash()
        with self.assertRaisesRegex(installer.InstallError, 'Source archive commit'):
            self.install()
        self.run.assert_not_called()
        self.assert_not_published()

    def test_missing_extra_duplicate_or_traversal_members_rejected(self):
        original = (self.kit / 'system_ops.py').read_bytes()
        (self.kit / 'system_ops.py').unlink()
        with self.assertRaises(installer.InstallError):
            self.install()
        (self.kit / 'system_ops.py').write_bytes(original)
        (self.kit / 'unrelated').write_bytes(b'no')
        with self.assertRaises(installer.InstallError):
            self.install()
        (self.kit / 'unrelated').unlink()
        original = (self.kit / 'SHA256SUMS').read_bytes()
        for extra in (original.splitlines()[0], b'0' * 64 + b'  ../escape'):
            raw = original + extra + b'\n'
            (self.kit / 'SHA256SUMS').write_bytes(raw)
            with self.assertRaises(installer.InstallError):
                installer.install(self.kit, hashlib.sha256(raw).hexdigest())
        self.assert_not_published()

    def test_source_dependency_missing_link_oversize_or_duplicate_refuses_before_writes(self):
        for dependency in installer.SOURCE_DEPENDENCIES:
            for failure in ('missing', 'link', 'oversize', 'duplicate'):
                with self.subTest(dependency=dependency, failure=failure):
                    self.write_kit()
                    path = self.kit / 'vui-source.zip'
                    with zipfile.ZipFile(path) as source:
                        members = {name: source.read(name) for name in source.namelist()}
                    selected = installer.SOURCE_DEPENDENCIES[dependency]
                    raw = members.pop(selected)
                    with zipfile.ZipFile(path, 'w') as source:
                        source.comment = b'a' * 40
                        for name, content in members.items():
                            source.writestr(name, content)
                        if failure == 'link':
                            entry = zipfile.ZipInfo(selected)
                            entry.create_system = 3
                            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                            source.writestr(entry, b'/mutable/payload')
                        elif failure == 'oversize':
                            source.writestr(selected, b'#' * (installer.MAX_CODE + 1))
                        elif failure == 'duplicate':
                            import warnings
                            with warnings.catch_warnings():
                                warnings.simplefilter('ignore', UserWarning)
                                source.writestr(selected, raw)
                                source.writestr(selected, raw)
                    self.rehash()
                    with self.assertRaises(installer.InstallError):
                        self.install()
                    self.assert_not_published()
                    self.assertFalse(self.lock.exists())
                    self.run.assert_not_called()

    def test_internal_source_dependencies_do_not_expand_public_kit_assets(self):
        verified, _ = installer.verify_kit(self.kit, self.sha)
        self.assertEqual(set(verified), installer.CODE_ASSETS | installer.SOURCE_DEPENDENCIES.keys())
        self.assertTrue(installer.SOURCE_DEPENDENCIES.keys().isdisjoint(installer.COMMON_ASSETS))
        for name, member in installer.SOURCE_DEPENDENCIES.items():
            self.assertEqual(verified[name], (REPO / member).read_bytes())
            self.assertFalse((self.kit / name).exists())

    def test_kit_symlink_hardlink_and_linked_parent_are_rejected(self):
        path = self.kit / 'system_ops.py'
        raw = path.read_bytes()
        other = self.base / 'source'
        other.write_bytes(raw)
        for link in ('symlink', 'hardlink'):
            path.unlink()
            path.symlink_to(other) if link == 'symlink' else os.link(other, path)
            with self.subTest(link=link), self.assertRaises((installer.InstallError, OSError)):
                self.install()
            path.unlink()
            path.write_bytes(raw)
        linked = self.base / 'kit-link'
        linked.symlink_to(self.kit, target_is_directory=True)
        with self.assertRaises(installer.InstallError):
            installer.install(linked, self.sha)
        self.assert_not_published()

    def test_wrong_platform_target_or_incomplete_instance_refuses(self):
        with patch.object(installer.platform, 'system', return_value='Darwin'):
            with self.assertRaises(installer.InstallError):
                self.install()
        for key, value in (('target', 'aarch64-musl'), ('ready', False), ('installer', True),
                           ('bootstrap_python', '/tmp/python'), ('ipv6', 'false')):
            config = {**self.config_value, key: value}
            self.config.write_text(json.dumps(config))
            with self.subTest(key=key), self.assertRaises(installer.InstallError):
                self.install()
        self.assert_not_published()

    def test_no_root_account_or_unmanaged_data_root(self):
        with patch.object(installer.pwd, 'getpwnam', return_value=argparse.Namespace(pw_uid=0, pw_gid=0)):
            with self.assertRaises(installer.InstallError):
                self.install()
        self.root.chmod(0o755)
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assert_not_published()

    def test_root_config_launcher_units_must_be_exact_safe_and_unlinked(self):
        for path in (self.config, self.control / 'launcher.py', self.units / 'v-ui.service'):
            raw = path.read_bytes()
            for change in ('writable', 'hardlink', 'symlink'):
                with self.subTest(path=path.name, change=change):
                    other = self.base / 'other'
                    if change == 'writable':
                        path.chmod(0o666)
                    else:
                        other.write_bytes(raw)
                        path.unlink()
                        path.symlink_to(other) if change == 'symlink' else os.link(other, path)
                    with self.assertRaises((installer.InstallError, OSError)):
                        self.install()
                    path.unlink()
                    other.unlink(missing_ok=True)
                    path.write_bytes(raw)
                    path.chmod(0o644)
        for path in (self.control / 'launcher.py', self.units / 'v-ui.service'):
            raw = path.read_bytes()
            path.write_bytes(raw + b'\n# unmanaged change\n')
            with self.assertRaises(installer.InstallError):
                self.install()
            path.write_bytes(raw)
        self.assert_not_published()

    def test_non_root_owned_files_and_writable_or_symbolic_ancestors_refuse(self):
        with patch.object(installer, 'owned_by_root', return_value=False):
            with self.assertRaises(installer.InstallError):
                self.install()
        self.control.chmod(0o777)
        with self.assertRaises(installer.InstallError):
            self.install()
        self.control.chmod(0o755)
        actual = self.control.with_name('real-control')
        self.control.rename(actual)
        self.control.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assert_not_published()

    def test_os_python_must_be_root_owned_safe_supported_and_never_portable(self):
        for output in (self.probe(version=[3, 8]), self.probe(version=[2, 7]), '3.12\ninjected', 'garbage'):
            self.run.return_value.stdout = output
            with self.subTest(output=output), self.assertRaises(installer.InstallError):
                self.install()
        self.run.return_value.stdout = self.probe()
        self.real_python.chmod(0o777)
        with self.assertRaises(installer.InstallError):
            self.install()
        self.real_python.chmod(0o755)
        self.python.unlink()
        with self.assertRaises(OSError):
            self.install()
        self.assert_not_published()

    def test_os_probe_rejects_missing_capabilities_or_stdlib(self):
        for name in installer.PROBE_CAPABILITIES:
            for change in ('missing', 'false'):
                capabilities = {name: True for name in installer.PROBE_CAPABILITIES}
                if change == 'missing':
                    del capabilities[name]
                else:
                    capabilities[name] = False
                self.run.return_value.stdout = self.probe(capabilities=capabilities)
                with self.subTest(name=name, change=change), self.assertRaises(installer.InstallError):
                    self.install()
        self.run.side_effect = subprocess.CalledProcessError(1, 'fixed OS probe')
        with self.assertRaisesRegex(installer.InstallError, 'standard-library/runtime'):
            self.install()
        self.assert_not_published()
        self.assertFalse(self.lock.exists())

    def test_cli_requires_bytecode_suppression_alongside_isolation(self):
        with patch.object(installer.sys, 'flags', argparse.Namespace(isolated=True, no_site=True)), \
                patch.object(installer.sys, 'executable', str(self.python)), \
                patch.object(installer.sys, 'dont_write_bytecode', False), \
                patch.object(installer, 'install') as install:
            with self.assertRaisesRegex(installer.InstallError, '-B -I -S'):
                installer.main(['--kit', str(self.kit), '--sha256', self.sha])
            install.assert_not_called()

    def test_unrelated_or_partial_existing_files_never_overwritten(self):
        for path in (*installer.destinations(), installer.RECEIPT):
            path.write_bytes(b'unrelated')
            path.chmod(0o755 if path == self.entry else 0o644)
            with self.subTest(path=path), self.assertRaises(installer.InstallError):
                self.install()
            self.assertEqual(path.read_bytes(), b'unrelated')
            path.unlink()

    def test_refresh_requires_intact_exact_old_receipt(self):
        with self.assertRaises(installer.InstallError):
            self.install(refresh=True)
        first = self.install()
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertEqual(self.install(refresh=True), first)
        for change in ('file', 'extra', 'schema', 'interpreter'):
            value = json.loads(installer.RECEIPT.read_bytes())
            original = dict(value)
            if change == 'file':
                value['files'] = {**value['files'], str(self.entry): '0' * 64}
            elif change == 'extra':
                value['files'] = {**value['files'], '/unrelated': '0' * 64}
            elif change == 'schema':
                value['schema'] = True
            else:
                value['interpreter_sha256'] = 'not-a-sha256'
            installer.RECEIPT.write_text(json.dumps(value))
            with self.subTest(change=change), self.assertRaises(installer.InstallError):
                self.install(refresh=True)
            installer.RECEIPT.write_text(json.dumps(original))

    def test_config_drift_requires_explicit_refresh_and_current_canonical_units(self):
        self.install()
        self.config_value['bootstrap_python'] = '/var/lib/v-ui/releases/upgraded/runtime/python/bin/python3'
        self.write_instance()
        with self.assertRaisesRegex(installer.InstallError, 'receipt drift'):
            self.install()
        self.assertEqual(self.install(refresh=True)['files'][str(self.config)],
                         hashlib.sha256(self.config.read_bytes()).hexdigest())
        unit = self.units / 'v-ui.service'
        unit.write_bytes(unit.read_bytes() + b'\n# drift\n')
        with self.assertRaisesRegex(installer.InstallError, 'canonical template'):
            self.install(refresh=True)

    def test_os_python_upgrade_refresh_rebinds_only_safe_current_interpreter(self):
        old = self.install()
        self.real_python.write_bytes(b'upgraded trusted OS Python, never executed')
        with self.assertRaisesRegex(installer.InstallError, 'receipt drift'):
            self.install()
        new = self.install(refresh=True)
        self.assertNotEqual(old['interpreter_sha256'], new['interpreter_sha256'])
        self.assertEqual(new['interpreter_sha256'], hashlib.sha256(self.real_python.read_bytes()).hexdigest())
        self.real_python.chmod(0o777)
        with self.assertRaises(installer.InstallError):
            self.install(refresh=True)

    def test_refresh_never_readopts_changed_control_code(self):
        self.install()
        for path in installer.destinations():
            original = path.read_bytes()
            path.write_bytes(original + b'\n# unrelated change\n')
            with self.subTest(path=path), self.assertRaisesRegex(installer.InstallError, 'control code drift'):
                self.install(refresh=True)
            path.write_bytes(original)

    def test_explicit_refresh_migrates_only_an_intact_legacy_receipt(self):
        old = self.install()
        for name in installer.SOURCE_DEPENDENCIES:
            path = self.control / name
            old['files'].pop(str(path))
            path.unlink()
        installer.RECEIPT.write_text(json.dumps(old))
        with self.assertRaises(installer.InstallError):
            self.install()
        # Unreceipted pre-existing dependency bytes may not be adopted.
        extra = self.control / 'proxy_store.py'
        extra.write_bytes(b'untrusted dependency')
        extra.chmod(0o644)
        with self.assertRaisesRegex(installer.InstallError, 'Unreceipted dependency'):
            self.install(refresh=True)
        extra.unlink()
        helper = self.control / 'ops_worker.py'
        raw = helper.read_bytes()
        helper.write_bytes(raw + b'\n# drift')
        with self.assertRaisesRegex(installer.InstallError, 'control code drift'):
            self.install(refresh=True)
        helper.write_bytes(raw)
        upgraded = self.install(refresh=True)
        self.assertEqual(set(upgraded['files']) - set(old['files']),
                         {str(self.control / name) for name in installer.SOURCE_DEPENDENCIES})

    def test_explicit_refresh_migrates_intact_m1_receipt_with_only_owner_helper_missing(self):
        old = self.install()
        owner = self.control / 'data_owner.py'
        old['files'].pop(str(owner))
        owner.unlink()
        installer.RECEIPT.write_text(json.dumps(old))
        with self.assertRaises(installer.InstallError): self.install()
        owner.write_bytes(b'unreceipted helper')
        owner.chmod(0o644)
        with self.assertRaisesRegex(installer.InstallError, 'Unreceipted dependency'):
            self.install(refresh=True)
        owner.unlink()
        dependency = self.control / 'proxy_store.py'
        raw = dependency.read_bytes()
        dependency.write_bytes(raw + b'\n# changed M1 dependency')
        with self.assertRaisesRegex(installer.InstallError, 'control code drift'):
            self.install(refresh=True)
        dependency.write_bytes(raw)
        upgraded = self.install(refresh=True)
        self.assertEqual(set(upgraded['files']) - set(old['files']), {str(owner)})
        self.assertEqual(owner.read_bytes(), (REPO / 'app/data_owner.py').read_bytes())

    def test_partial_publish_fails_closed_without_usable_wrapper_or_receipt(self):
        original_replace = os.replace
        self.install()
        for fail_at in range(1, len(installer.destinations()) + 2):
            # Reconstruct an intact fake installation for each write boundary.
            for path in (*installer.destinations(), installer.RECEIPT):
                path.unlink(missing_ok=True)
            self.install()
            count = 0
            def failing_replace(source, destination):
                nonlocal count
                count += 1
                if count == fail_at:
                    raise OSError('simulated disk failure')
                return original_replace(source, destination)
            with self.subTest(fail_at=fail_at), patch.object(installer.os, 'replace', side_effect=failing_replace):
                with self.assertRaises(OSError):
                    self.install(refresh=True)
            self.assert_not_published()
            self.assertFalse(list(self.control.glob('.vui-control-*')))
            self.assertFalse(list(self.entry.parent.glob('.vui-control-*')))
            with self.assertRaisesRegex(installer.InstallError, 'explicit --refresh'):
                self.install()
            self.install(refresh=True)
            self.assertTrue(self.entry.exists())
            self.assertTrue(installer.RECEIPT.exists())

    def test_partial_refresh_only_repairs_matching_accepted_kit_bytes(self):
        source = self.control / 'ops_worker.py'
        original = (self.kit / 'ops_worker.py').read_bytes()
        other = self.base / 'partial-source'
        for change in ('unknown', 'mixed-version', 'symlink', 'hardlink', 'writable'):
            source.write_bytes(original)
            source.chmod(0o644)
            if change == 'unknown':
                source.write_bytes(b'unrelated root helper')
            elif change == 'mixed-version':
                source.write_bytes(original + b'\n# another accepted version\n')
            elif change in ('symlink', 'hardlink'):
                other.write_bytes(original)
                source.unlink()
                source.symlink_to(other) if change == 'symlink' else os.link(other, source)
            else:
                source.chmod(0o666)
            with self.subTest(change=change), self.assertRaises((installer.InstallError, OSError)):
                self.install(refresh=True)
            self.assert_not_published()
            source.unlink()
            other.unlink(missing_ok=True)
        source.write_bytes(original)
        source.chmod(0o644)
        installer.RECEIPT.symlink_to(self.base / 'missing-receipt')
        with self.assertRaises(OSError):
            self.install(refresh=True)
        installer.RECEIPT.unlink()
        self.install(refresh=True)
        self.assertTrue(self.entry.exists())

    def test_staging_failure_leaves_intact_old_installation(self):
        receipt = self.install()
        with patch.object(installer, 'stage_file', side_effect=OSError('full disk')):
            with self.assertRaises(OSError):
                self.install(refresh=True)
        self.assertEqual(json.loads(installer.RECEIPT.read_bytes()), receipt)
        self.assertTrue(self.entry.exists())

    def test_openrc_uses_exact_scripts_without_starting_or_reloading(self):
        self.write_instance('openrc')
        receipt = self.install()
        self.assertEqual(receipt['service_manager'], 'openrc')
        self.assertTrue({str(self.openrc / name) for name in ('v-ui', 'v-ui-http01')} <= receipt['files'].keys())
        self.assertFalse(any('/systemd/' in name for name in receipt['files']))
        self.assertEqual(len(self.run.call_args_list), 1)

    def test_lock_symlink_hardlink_wrong_mode_and_contention_refuse(self):
        other = self.base / 'other-lock'
        other.touch(mode=0o600)
        self.lock.symlink_to(other)
        with self.assertRaises(OSError):
            self.install()
        self.lock.unlink()
        os.link(other, self.lock)
        with self.assertRaises(installer.InstallError):
            self.install()
        self.lock.unlink()
        self.lock.touch(mode=0o644)
        self.lock.chmod(0o644)
        with self.assertRaises(installer.InstallError):
            self.install()
        self.lock.chmod(0o600)
        with installer.installation_lock():
            with self.assertRaisesRegex(installer.InstallError, 'Another installer'):
                self.install()
        self.assertTrue(self.lock.exists())
        self.assert_not_published()

    def test_lock_replacement_is_detected_after_flock(self):
        def replace_lock(fd, operation):
            self.lock.unlink()
            self.lock.touch(mode=0o600)
        with patch.object(installer.fcntl, 'flock', side_effect=replace_lock):
            with self.assertRaisesRegex(installer.InstallError, 'pathname changed'):
                self.install()
        self.assert_not_published()

    def test_actual_installed_worker_loads_its_packaged_maintenance_implementation(self):
        self.install()
        path = self.control / 'ops_worker.py'
        # Load in the real isolated worker context, never sharing app modules
        # with this test process. The latter would hide a missing dependency.
        code = ('import runpy,sys;from pathlib import Path;'
                'm=runpy.run_path(sys.argv[1]);api=m["_load_release_tools"]();'
                'assert Path(api.__file__)==Path(sys.argv[1]).with_name("release_tools.py");'
                'assert callable(api._MaintenanceSession.execute);'
                'from app.services.proxy_store import ProxyStore;'
                'assert Path(sys.modules[ProxyStore.__module__].__file__).parent==Path(sys.argv[1]).parent')
        # setUp mocks the OS capability subprocess only; use the saved function
        # for this genuine interpreter process.
        result = REAL_RUN([sys.executable, '-B', '-I', '-S', '-c', code, str(path)],
                          cwd='/', capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_controller_consumes_installer_receipt_and_rejects_drift(self):
        receipt = self.install()
        path = self.control / 'system_ops.py'
        spec = importlib.util.spec_from_file_location('_test_installed_controller', path)
        controller = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(controller)
        with patch.multiple(controller, CONFIG=self.config, CONTROL=self.control,
                RECEIPT=installer.RECEIPT, WRAPPER=self.entry, PYTHON=self.python,
                WORKER=self.control / 'ops_worker.py'), \
                patch.object(controller, '_root_owned'), \
                patch.object(controller, 'trusted_executable', return_value=self.real_python), \
                patch.object(controller, 'fixed_files', return_value={Path(name) for name in receipt['files']}):
            manager, account = controller.verify_installation()
            self.assertEqual(manager, 'systemd')
            self.assertEqual(account, self.account)
            (self.control / 'release_tools.py').write_bytes(b'changed')
            with self.assertRaises(controller.Refused):
                controller.verify_installation()

    def test_legacy_panel_installer_keeps_control_installation_explicit(self):
        self.assertNotIn('install_ops_control', (REPO / 'scripts/install_system.py').read_text())
        # The online shell now has an explicit authenticated managed mode.
        # Legacy default dispatch is covered by test_installer_shell's actual
        # subprocess controls; the panel installer never installs root helpers.
        self.assertIn('--with-global-cli', (REPO / 'install.sh').read_text())
        self.assertNotIn('install_ops_control', (REPO / '.github/workflows/oneclick.yml').read_text())


if __name__ == '__main__':
    unittest.main()
