"""Pure role/environment/log/fake-kit checks; no fetch, core or install executes."""
from __future__ import annotations
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import acceptance_job as job
from scripts import release_acceptance as core
import test_release_acceptance as fixtures

SHA = fixtures.SHA
VERSION = fixtures.VERSION
ACTIONS = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': core.REPOSITORY,
           'GITHUB_RUN_ID': '42', 'GITHUB_RUN_ATTEMPT': '2', 'GITHUB_SHA': 'b' * 40}
SUCCESS = b'test_actual_offline_installer_permissions_socket_and_autostart (test_oneclick_system.OneClickSystemTests) ... ok\n\n----------------------------------------------------------------------\nRan 1 test in 12.345s\n\nOK\n'


class RecipeTests(unittest.TestCase):
    def test_legacy_matrices_preserved_and_25_includes_exact_public24(self):
        current = job.matrix_for_version('0.4.22')['include']
        self.assertEqual([row['version'] for row in current], ['0.4.14', '0.4.15', '0.4.16', '0.4.17', '0.4.19', '0.4.20', '0.4.21'])
        future = job.matrix_for_version('0.4.23')['include']
        self.assertEqual(future[:-1], current)
        self.assertEqual(future[-1], {'version': '0.4.22', 'source': '558687a76cd29f4e4735284926eb05bf6664472e',
                                     'path': '.published-v0422', 'netlink': True})
        candidate = job.matrix_for_version('0.4.24')['include']
        self.assertEqual(candidate[:-1], future)
        self.assertEqual(candidate[-1], {'version': '0.4.23', 'source': 'be5d8459cdd3eeedce73f2ff6e4663e5eb781e42',
                                        'path': '.published-v0423', 'netlink': True})
        next_candidate = job.matrix_for_version('0.4.25')['include']
        self.assertEqual(next_candidate[:-1], candidate)
        self.assertEqual(next_candidate[-1], {'version': '0.4.24',
            'source': '8b8d7cf82e55609a205455b351f659eb9de91974', 'path': '.published-v0424', 'netlink': True})
        for row in next_candidate:
            self.assertEqual(row['source'], core.BASELINES[row['version']])
            self.assertIs(type(row['netlink']), bool)
        for value in ('0.4.21', '00.4.22', 'v0.4.22', '0.4.22-rc1', True):
            with self.subTest(value=value), self.assertRaises(core.ReleaseAcceptanceError): job.matrix_for_version(value)
        with self.assertRaises(core.ReleaseAcceptanceError): job.role_recipe('0.4.22', 'upgrade-0.4.22', old_source='/old')
        with self.assertRaises(core.ReleaseAcceptanceError): job.role_recipe('0.4.23', 'upgrade-0.4.23', old_source='/old')

    def test_fresh_recipe_preserves_all_original_flags_and_fixed_commands(self):
        recipe = job.role_recipe(VERSION, 'fresh')
        self.assertEqual(recipe['env'], {
            'VUI_SYSTEM_INSTALL_TEST': '1', 'VUI_REMOTE_MAINTENANCE_TEST': '1',
            'VUI_RELEASE_BUNDLE': '/tmp/vui-oneclick/vui-linux.zip', 'VUI_RELEASE_KIT': '/tmp/vui-oneclick/kit',
            'VUI_DOMAIN_RETRY_TEST': '1', 'VUI_INSTALLER_YN_TEST': '1', 'VUI_EXISTING_INSTALL_TEST': '1',
            'VUI_NETLINK_SYSTEMD_TEST': '1', 'VUI_TEST_MIHOMO': '/tmp/vui-oneclick/client/mihomo'})
        self.assertEqual(recipe['commands'], [job.FETCH_COMMAND, job.TEST_COMMAND])
        self.assertEqual(job.TEST_COMMAND[1:], ['-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_oneclick_system.py', '-v'])

    def test_each_upgrade_has_exact_original_environment_without_truthy_zero_flags(self):
        for row in job.matrix_for_version('0.4.24')['include']:
            role = 'upgrade-' + row['version']; old = '/workspace/' + row['path']
            recipe = job.role_recipe('0.4.24', role, old_source=old)
            env = recipe['env']
            self.assertEqual(env['VUI_PUBLISHED_INSTALL_VERSION'], row['version'])
            self.assertEqual(env['VUI_RELEASE_COMMIT'], row['source'])
            self.assertEqual(env['VUI_PUBLISHED_UPGRADE_SOURCE'], old)
            self.assertEqual(env['VUI_REMOTE_MAINTENANCE_TEST'], '1')
            self.assertNotIn('VUI_EXISTING_INSTALL_TEST', env); self.assertNotIn('VUI_SETUP_INSTALL', env)
            self.assertNotIn('VUI_DOMAIN_RETRY_TEST', env); self.assertNotIn('VUI_INSTALLER_YN_TEST', env)
            if row['netlink']:
                self.assertEqual(env['VUI_NETLINK_SYSTEMD_TEST'], '1')
                self.assertEqual(env['VUI_TEST_MIHOMO'], '/tmp/vui-oneclick/client/mihomo')
                self.assertEqual(recipe['commands'], [job.FETCH_COMMAND, job.TEST_COMMAND])
            else:
                self.assertNotIn('VUI_NETLINK_SYSTEMD_TEST', env); self.assertNotIn('VUI_TEST_MIHOMO', env)
                self.assertEqual(recipe['commands'], [job.TEST_COMMAND])
            inherited = {**ACTIONS, 'PATH': '/usr/bin', **{key: '0' for key in job.role_recipe(VERSION, 'fresh')['env']},
                         'VUI_SETUP_INSTALL': '1', 'VUI_DATA_DIR': '/not-user-data', 'VUI_UNRELATED_GATE': '1'}
            clean = job.role_environment(inherited, '0.4.24', role, old_source=old)
            self.assertEqual({key: value for key, value in clean.items() if key.startswith('VUI_')}, env)
            self.assertEqual(clean['PATH'], '/usr/bin')
        for role in ('build', 'upgrade-0.4.18', 'other', True):
            with self.assertRaises(core.ReleaseAcceptanceError): job.role_recipe(VERSION, role)

    def test_names_match_locked_repository_run_attempt_role_convention(self):
        common = dict(source=SHA, version=VERSION, run_id=42, run_attempt=2)
        self.assertEqual(job.artifact_name('kit', role='build', **common), f'vui-release-kit-{SHA}-42-2')
        self.assertEqual(job.artifact_name('receipt', role='fresh', **common), f'vui-oneclick-receipt-fresh-{SHA}-42-2')
        self.assertEqual(job.artifact_name('log', role='upgrade-0.4.21', **common), f'vui-oneclick-evidence-upgrade-0.4.21-{SHA}-42-2')
        for bad in ({'kind': 'kit', 'role': 'fresh'}, {'kind': 'other', 'role': 'build'},
                    {'kind': 'log', 'role': 'unknown'}, {'kind': 'log', 'role': 'upgrade-0.4.22'}):
            with self.assertRaises(core.ReleaseAcceptanceError): job.artifact_name(**bad, **common)
        for value in (True, '42', 0, -1):
            with self.assertRaises(core.ReleaseAcceptanceError):
                job.artifact_name('kit', role='build', **{**common, 'run_id': value})


class JobTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.KitTests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root; self.kit = self.fixture.kit
        self.checkout = self.root / 'checkout'; self.checkout.mkdir(); (self.checkout / 'VERSION').write_text(VERSION)
        self.bundle = self.root / 'vui-linux.zip'; self.identity_path = self.root / 'identity.json'
        self.log = self.root / 'acceptance.log'; self.output = self.root / 'acceptance-receipt.json'
        self.patchers = [patch.object(job, 'KIT', self.kit), patch.object(job, 'BUNDLE', self.bundle), patch.object(job, 'ROOT', self.checkout)]
        for patcher in self.patchers: patcher.start(); self.addCleanup(patcher.stop)
        self.identity = job.prepare(self.kit, SHA, VERSION, self.fixture.anchor,
                                    fixtures.digest((self.kit / core.BUNDLE).read_bytes()), self.identity_path, bundle_output=self.bundle)

    def fake_git(self, command, *, cwd, text):
        self.assertEqual(command, ['git', 'rev-parse', 'HEAD']); self.assertTrue(text)
        if Path(cwd) == self.checkout: return SHA + '\n'
        return core.BASELINES[Path(cwd).name.removeprefix('old-')] + '\n'

    def run_role(self, role='fresh', *, old_source=None, result=0, data=SUCCESS, fetch_result=0, core_switch=False):
        calls = []
        def execute(command, **kwargs):
            calls.append((command, kwargs))
            self.assertEqual(kwargs['cwd'], self.checkout)
            if command == job.FETCH_COMMAND:
                self.assertTrue(kwargs['check'])
                if fetch_result: raise subprocess.CalledProcessError(fetch_result, command)
            else:
                self.assertEqual(command, job.TEST_COMMAND); self.assertFalse(kwargs['check'])
                kwargs['stdout'].write(data); kwargs['stdout'].flush()
            return subprocess.CompletedProcess(command, result if command == job.TEST_COMMAND else 0)
        with patch.object(job.subprocess, 'check_output', side_effect=self.fake_git), patch.object(job.subprocess, 'run', side_effect=execute):
            code = job.run_test(SHA, VERSION, role, self.identity_path, self.log, old_source=old_source,
                                environ=ACTIONS, core_switch=core_switch)
        return code, calls

    def make_receipt(self, role='fresh', **changes):
        values = {'identity_path': self.identity_path, 'source': SHA, 'version': VERSION, 'role': role,
                  'artifact_id': 123, 'artifact_digest': 'sha256:' + 'c' * 64,
                  'table': self.identity['checksums_sha256'], 'bundle': self.identity['bundle_sha256'],
                  'output': self.output, 'test_log': self.log, 'environ': ACTIONS}
        values.update(changes); return job.receipt(**values)

    def test_prepare_real_fake_kit_producer_inspection_and_new_600_outputs(self):
        self.assertEqual(self.bundle.read_bytes(), (self.kit / core.BUNDLE).read_bytes())
        self.assertEqual(stat.S_IMODE(self.identity_path.stat().st_mode), 0o600)
        producer_path = self.root / 'producer.json'
        identity = job.producer(self.kit, SHA, VERSION, producer_path)
        self.assertEqual(identity['validation_scope'], core.PRODUCER_SCOPE)
        with self.assertRaises(FileExistsError): job.producer(self.kit, SHA, VERSION, producer_path)
        with self.assertRaises(core.ReleaseAcceptanceError): job.producer(self.kit, SHA, VERSION, self.kit / 'extra.json')
        with self.assertRaises(core.ReleaseAcceptanceError):
            job.prepare(self.kit, SHA, VERSION, self.fixture.anchor, '0' * 64, self.root / 'invalid.json', bundle_output=self.root / 'invalid.zip')
        self.assertFalse((self.root / 'invalid.zip').exists())

    def test_fixed_fresh_test_and_receipt_accept_complete_real_log_shape(self):
        code, calls = self.run_role(); self.assertEqual(code, 0); self.assertEqual(len(calls), 2)
        self.assertEqual(calls[-1][1]['env']['VUI_EXISTING_INSTALL_TEST'], '1')
        result = self.make_receipt()
        self.assertEqual(result['run_id'], 42); self.assertEqual(result['run_attempt'], 2)
        self.assertEqual(result['role'], 'fresh'); self.assertNotIn('success', result)
        self.assertEqual(stat.S_IMODE(self.log.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        with self.assertRaises(FileExistsError): self.make_receipt()

    def test_old14_does_not_fetch_and_checks_immutable_old_git_head(self):
        old = self.root / 'old-0.4.14'; old.mkdir()
        code, calls = self.run_role('upgrade-0.4.14', old_source=old)
        self.assertEqual(code, 0); self.assertEqual(len(calls), 1)
        self.assertNotIn('VUI_NETLINK_SYSTEMD_TEST', calls[0][1]['env'])
        self.assertNotIn('VUI_TEST_MIHOMO', calls[0][1]['env'])
        result = self.make_receipt('upgrade-0.4.14')
        self.assertEqual(result['old_source_sha'], core.BASELINES['0.4.14'])

    def test_wrong_candidate_or_old_checkout_never_runs_a_test(self):
        old = self.root / 'old-0.4.14'; old.mkdir()
        with patch.object(job.subprocess, 'check_output', side_effect=[SHA + '\n', 'f' * 40 + '\n']), \
                patch.object(job.subprocess, 'run') as execute, self.assertRaisesRegex(core.ReleaseAcceptanceError, 'CHECKOUT_SOURCE'):
            job.run_test(SHA, VERSION, 'upgrade-0.4.14', self.identity_path, self.log, old_source=old, environ=ACTIONS)
        execute.assert_not_called(); self.assertFalse(self.log.exists())
        with patch.object(job.subprocess, 'check_output', return_value='f' * 40 + '\n'), \
                patch.object(job.subprocess, 'run') as execute, self.assertRaises(core.ReleaseAcceptanceError):
            job.run_test(SHA, VERSION, 'fresh', self.identity_path, self.log, environ=ACTIONS)
        execute.assert_not_called()

    def test_nonzero_exit_and_fixed_fetch_failure_propagate_once_without_receipt(self):
        code, calls = self.run_role(result=7)
        self.assertEqual(code, 7); self.assertEqual(len(calls), 2)
        with self.assertRaises(core.ReleaseAcceptanceError): self.make_receipt()
        self.assertFalse(self.output.exists()); self.log.unlink()
        code, calls = self.run_role(fetch_result=23)
        self.assertEqual(code, 23); self.assertEqual(len(calls), 1)
        with self.assertRaises(core.ReleaseAcceptanceError): self.make_receipt()

    def test_zero_exit_but_skip_missing_or_failure_summary_is_rejected(self):
        for data in (SUCCESS.replace(b'OK\n', b'OK (skipped=1)\n'), SUCCESS.replace(b'Ran 1 test', b'Ran 0 tests'),
                     SUCCESS.replace(b'OK\n', b'FAILED (errors=1)\n'), b'no unittest summary\n', SUCCESS + SUCCESS):
            with self.subTest(data=data[-50:]), self.assertRaises(core.ReleaseAcceptanceError): self.run_role(data=data)
            with self.assertRaises(core.ReleaseAcceptanceError): self.make_receipt()
            self.assertFalse(self.output.exists()); self.log.unlink()

    def test_receipt_rejects_wrong_context_source_role_scope_ids_hashes_and_unknown_fields(self):
        self.run_role()
        for changes in ({'source': 'f' * 40}, {'version': '0.4.23'}, {'role': 'upgrade-0.4.21'},
                        {'role': 'unknown'}, {'artifact_id': True}, {'artifact_digest': 'c' * 64},
                        {'table': '0' * 64}, {'bundle': '0' * 64},
                        {'environ': {**ACTIONS, 'GITHUB_RUN_ATTEMPT': '3'}},
                        {'environ': {**ACTIONS, 'GITHUB_REPOSITORY': 'other/repo'}}):
            with self.subTest(changes=changes), self.assertRaises(core.ReleaseAcceptanceError): self.make_receipt(**changes)
            self.assertFalse(self.output.exists())
        raw = self.identity_path.read_bytes()
        self.identity_path.write_bytes(raw[:-2] + b',"schema":1}\n')
        with self.assertRaisesRegex(core.ReleaseAcceptanceError, 'DUPLICATE_JSON_KEY'): self.make_receipt()

    def test_raw_test_log_context_strict_json_and_nested_identity_types(self):
        self.run_role(); original = self.log.read_bytes()
        first, body = original.split(b'\n', 1)
        context = json.loads(first[len(job.HEADER):])
        for key, value in (('run_id', True), ('run_attempt', 3), ('role', 'upgrade-0.4.21'), ('unknown', True)):
            bad = {**context, key: value}
            self.log.write_bytes(job.HEADER.encode() + json.dumps(bad).encode() + b'\n' + body)
            with self.subTest(key=key), self.assertRaises(core.ReleaseAcceptanceError): self.make_receipt()
        context['identity']['schema'] = True
        self.log.write_bytes(job.HEADER.encode() + json.dumps(context).encode() + b'\n' + body)
        with self.assertRaises(core.ReleaseAcceptanceError): self.make_receipt()
        self.log.write_bytes(first[:-1] + b',"run_attempt":2}\n' + body)
        with self.assertRaisesRegex(core.ReleaseAcceptanceError, 'DUPLICATE_JSON_KEY'): self.make_receipt()

    def test_latest_public_upgrade_requires_one_exact_preservation_marker(self):
        pairs = [('0.4.24', '0.4.23', b'VUI_23_TO_24_UPGRADE_ACCEPTANCE:'),
                 ('0.4.25', '0.4.24', b'VUI_24_TO_25_UPGRADE_ACCEPTANCE:')]
        for candidate, old, marker in pairs:
            identity = {**self.identity, 'version': candidate, 'release_id': candidate + '-' + SHA[:12]}
            context = job._log_context(identity, 'upgrade-' + old, ACTIONS)
            header = (job.HEADER + json.dumps(context) + '\n').encode()
            footer = (job.FOOTER + '0\n').encode()
            for bad in (b'', marker + b' \n', b'prefix ' + marker + b' evidence\n',
                        (marker + b' evidence\n') * 2):
                with self.subTest(candidate=candidate, bad=bad), self.assertRaisesRegex(
                        core.ReleaseAcceptanceError, f'UPGRADE_{old[-2:]}_EVIDENCE_REQUIRED'):
                    job.validate_test_log(header + bad + SUCCESS + footer, context)
            job.validate_test_log(header + marker + b' exact preservation evidence\n' + SUCCESS + footer, context)

    def test_run_test_rejects_false_identity_or_changed_copy_before_any_install_command(self):
        original = self.identity_path.read_bytes()
        value = json.loads(original); value['validation_scope'] = core.PRODUCER_SCOPE
        self.identity_path.write_text(json.dumps(value))
        with patch.object(job.subprocess, 'run') as execute, self.assertRaises(core.ReleaseAcceptanceError):
            job.run_test(SHA, VERSION, 'fresh', self.identity_path, self.log, environ=ACTIONS)
        execute.assert_not_called(); self.identity_path.write_bytes(original)
        self.bundle.write_bytes(b'wrong bundle bytes')
        with patch.object(job.subprocess, 'run') as execute, self.assertRaises(core.ReleaseAcceptanceError):
            job.run_test(SHA, VERSION, 'fresh', self.identity_path, self.log, environ=ACTIONS)
        execute.assert_not_called()

    def test_native_core_switch_requires_fresh_fixture_and_both_evidence_lines(self):
        fixture = self.root / 'fixture'
        sing_box = fixture / 'sing-box' / 'candidate'
        xray = fixture / 'xray' / 'candidate'
        sing_box.mkdir(mode=0o700, parents=True)
        sb_marker = b'VUI_INSTALLED_CORE_SWITCH_ACCEPTANCE: sing-box synthetic evidence\n'
        xray_marker = b'VUI_INSTALLED_CORE_SWITCH_ACCEPTANCE: xray synthetic evidence\n'
        interrupt_marker = b'VUI_INSTALLED_CORE_INTERRUPT_ACCEPTANCE: synthetic evidence\n'
        restore_marker = b'VUI_INSTALLED_CORE_RESTORE_ACCEPTANCE: synthetic evidence\n'
        with patch.object(job, 'CORE_SWITCH_FIXTURE', fixture):
            with self.assertRaisesRegex(core.ReleaseAcceptanceError, 'CORE_SWITCH_FRESH_FIXTURE_REQUIRED'):
                self.run_role(core_switch=True)
            self.assertFalse(self.log.exists())
            xray.parent.symlink_to(sing_box.parent, target_is_directory=True)
            with self.assertRaisesRegex(core.ReleaseAcceptanceError, 'CORE_SWITCH_FRESH_FIXTURE_REQUIRED'):
                self.run_role(core_switch=True)
            self.assertFalse(self.log.exists())
            xray.parent.unlink()
            xray.mkdir(mode=0o700, parents=True)
            for evidence in (b'', sb_marker, sb_marker + sb_marker, xray_marker + sb_marker,
                             sb_marker + xray_marker + xray_marker,
                             sb_marker.rstrip() + xray_marker + xray_marker,
                             sb_marker + b'VUI_INSTALLED_CORE_SWITCH_ACCEPTANCE: xray \n'):
                with self.subTest(evidence=evidence):
                    with self.assertRaisesRegex(core.ReleaseAcceptanceError, 'CORE_SWITCH_EVIDENCE_REQUIRED'):
                        self.run_role(core_switch=True, data=SUCCESS + evidence)
                    self.log.unlink()
            switches = sb_marker + xray_marker
            for evidence in (switches, interrupt_marker+switches, switches+interrupt_marker+interrupt_marker):
                with self.assertRaisesRegex(core.ReleaseAcceptanceError, 'CORE_INTERRUPT_EVIDENCE_REQUIRED'):
                    self.run_role(core_switch=True, data=SUCCESS+evidence)
                self.log.unlink()
            for evidence in (switches+interrupt_marker, switches+restore_marker+interrupt_marker,
                             switches+interrupt_marker+restore_marker+restore_marker):
                with self.assertRaisesRegex(core.ReleaseAcceptanceError, 'CORE_RESTORE_EVIDENCE_REQUIRED'):
                    self.run_role(core_switch=True, data=SUCCESS+evidence)
                self.log.unlink()
            evidence=switches+interrupt_marker+restore_marker
            for marker,error in job.CORE_REMOTE_MARKERS:
                for suffix in (b'',marker+b' \n',marker+b' evidence\n'+marker+b' duplicate\n'):
                    with self.assertRaisesRegex(core.ReleaseAcceptanceError,error):
                        self.run_role(core_switch=True,data=SUCCESS+evidence+suffix)
                    self.log.unlink()
                evidence+=marker+b' synthetic evidence\n'
            code, calls = self.run_role(core_switch=True, data=SUCCESS+evidence)
        self.assertEqual(code, 0)
        self.assertEqual(calls[-1][1]['env']['VUI_CORE_SWITCH_FIXTURE'], str(fixture))

    def test_build_receipt_requires_producer_scope_and_no_log_claim(self):
        producer_path = self.root / 'producer.json'; job.producer(self.kit, SHA, VERSION, producer_path)
        with self.assertRaises(core.ReleaseAcceptanceError): self.make_receipt('build', test_log=None)
        result = self.make_receipt('build', identity_path=producer_path, test_log=None)
        self.assertEqual(result['validation_scope'], core.PRODUCER_SCOPE)
        self.assertEqual(result['role'], 'build'); self.assertNotIn('old_version', result)

    def test_actions_context_must_be_explicit_canonical_and_not_pr_merge_sha(self):
        self.assertEqual(job._context(ACTIONS)['run_id'], 42)  # GITHUB_SHA may be PR merge ref.
        for changes in ({'GITHUB_ACTIONS': 'false'}, {'GITHUB_RUN_ID': '0'}, {'GITHUB_RUN_ID': '01'},
                        {'GITHUB_RUN_ATTEMPT': True}, {'GITHUB_RUN_ATTEMPT': '0'}, {'GITHUB_RUN_ATTEMPT': '-1'}):
            with self.subTest(changes=changes), self.assertRaises(core.ReleaseAcceptanceError): job._context({**ACTIONS, **changes})

    def test_cli_matrix_name_and_run_test_exit_code(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output): job.main(['matrix', '--version', VERSION])
        self.assertEqual(len(json.loads(output.getvalue())['include']), 7)
        output = io.StringIO()
        with patch.dict(os.environ, ACTIONS, clear=True), contextlib.redirect_stdout(output):
            job.main(['name', '--kind', 'kit', '--role', 'build', '--source', SHA, '--version', VERSION])
        self.assertEqual(output.getvalue().strip(), f'vui-release-kit-{SHA}-42-2')
        with patch.object(job, 'run_test', return_value=7), self.assertRaises(SystemExit) as result:
            job.main(['run-test', '--source', SHA, '--version', VERSION, '--role', 'fresh',
                      '--identity', str(self.identity_path), '--log', str(self.log)])
        self.assertEqual(result.exception.code, 7)


if __name__ == '__main__': unittest.main()
