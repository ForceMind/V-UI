"""Execute the real workflow gate against fake Git trees; never build or run root."""
import contextlib
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import textwrap
import unittest


SOURCE = Path(__file__).resolve().parents[1]
WORKFLOW = SOURCE / '.github/workflows/low-resource-root-install.yml'
BASELINE = '5f44a2fb4a9ac0c32bc09b9a7859fc8ea8629154'
REVIEWED_BLOB = 'd3cb78d1045dc70ac070e126de27b73eda612e52'
RELEASE_PATH = 'app/release_tools.py'
EXCLUSION = ':(top,exclude,literal)' + RELEASE_PATH
EVIDENCE_PATH = '/tmp/vui-root-evidence'
PROTECTED_PATHS = (
    'app', 'web', 'deploy', 'third_party', 'main.py', 'VERSION', 'install.sh',
    'requirements-runtime.txt', 'scripts/deploy.py', 'scripts/install_system.py',
    'scripts/build_bundle.py', 'scripts/platform_support.py',
    'scripts/fetch_portable_runtimes.py', 'scripts/fetch_test_cores.py',
    'scripts/prepare_frontend.py', 'scripts/vendor_frontend.py',
)
PROTECTED_FILES = tuple(
    path + '/nested/fixture.txt' if path in PROTECTED_PATHS[:4] else path
    for path in PROTECTED_PATHS
)
OLD_RELEASE = b'# fake original release tools\n'
REVIEWED_RELEASE = b'# fake reviewed release tools\n'


def workflow_scripts():
    """Extract indented YAML run blocks without adding a YAML dependency."""
    scripts = []
    lines = WORKFLOW.read_text().splitlines()
    for index, line in enumerate(lines):
        if line != '        run: |':
            continue
        block = []
        for following in lines[index + 1:]:
            if following and not following.startswith('          '):
                break
            block.append(following[10:])
        scripts.append('\n'.join(block) + '\n')
    guard, = (script for script in scripts if 'python scripts/build_bundle.py ' in script)
    root, = (script for script in scripts if 'python -B scripts/low_resource_root.py ' in script)
    return guard, root


class FakeRepository:
    """Only synthetic files, local Git objects and disposable sentinel output."""

    def __init__(self, directory, omitted=None):
        self.directory = Path(directory)
        self.repo = self.directory / 'repo'
        self.repo.mkdir()
        self.evidence = self.directory / 'evidence'
        self.calls = self.directory / 'calls'
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith('GIT_')}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                        LC_ALL='C')
        template = self.directory / 'empty-template'
        template.mkdir()
        self.git('init', '--quiet', '--object-format=sha1', '--template=' + str(template))
        self.git('config', 'user.name', 'Synthetic provenance test')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        self.git('config', 'core.filemode', 'true')
        self.git('config', 'core.autocrlf', 'false')
        for path in PROTECTED_FILES:
            if path != omitted:
                self.write(path, b'# unchanged synthetic fixture\n')
        self.write(RELEASE_PATH, OLD_RELEASE)
        self.write('docs/fixture.md', b'Baseline documentation.\n')
        self.baseline = self.commit()
        self.write(RELEASE_PATH, REVIEWED_RELEASE)
        self.head = self.commit()
        self.blob = self.git('rev-parse', 'HEAD:' + RELEASE_PATH).stdout.strip()

    def git(self, *args, check=True):
        return subprocess.run(['git', *args], cwd=self.repo, env=self.env,
                              text=True, capture_output=True, check=check, timeout=10)

    def write(self, path, data):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def commit(self, stage=True):
        if stage:
            self.git('add', '-A')
        self.git('commit', '--quiet', '-m', 'Synthetic fixture')
        self.head = self.git('rev-parse', 'HEAD').stdout.strip()
        return self.head

    def run(self, *, source='current', git_failure='', builder_status=0):
        guard, root = workflow_scripts()
        # These are the only changes to the actual checked-in workflow scripts.
        script = (guard + root).replace(BASELINE, self.baseline)
        script = script.replace(REVIEWED_BLOB, self.blob)
        script = script.replace(EVIDENCE_PATH, shlex.quote(str(self.evidence)))
        self.calls.write_text('')
        environment = dict(self.env, FIXTURE_CALLS=str(self.calls),
                           FIXTURE_GIT_FAILURE=git_failure,
                           FIXTURE_BUILDER_STATUS=str(builder_status))
        environment.pop('VUI_SOURCE_COMMIT', None)
        if source is not None:
            environment['VUI_SOURCE_COMMIT'] = self.head if source == 'current' else source
        # Functions affect only this child Bash. Real Git still performs every
        # read unless an individual Git command failure is explicitly requested.
        stubs = r'''
        git() {
          { printf 'git'; printf '\t%s' "$@"; printf '\n'; } >> "$FIXTURE_CALLS"
          if [[ "$*" = "$FIXTURE_GIT_FAILURE" ]]; then
            printf 'fixture Git failure: %s\n' "$*" >&2
            return 73
          fi
          command git "$@"
        }
        python() {
          { printf 'python'; printf '\t%s' "$@"; printf '\n'; } >> "$FIXTURE_CALLS"
          if [[ "$1" = scripts/build_bundle.py ]]; then
            printf 'fixture builder stdout\n'
            printf 'fixture builder stderr\n' >&2
            return "$FIXTURE_BUILDER_STATUS"
          elif [[ "$1" = -B && "$2" = scripts/low_resource_root.py ]]; then
            printf 'fixture root sentinel\n'
            return 0
          fi
          printf 'Unexpected Python invocation\n' >&2
          return 91
        }
        '''
        return subprocess.run(['bash', '-c', textwrap.dedent(stubs) + script],
                              cwd=self.repo, env=environment, text=True,
                              capture_output=True, timeout=15)

    def call_rows(self):
        return [line.split('\t') for line in self.calls.read_text().splitlines()]

    def python_calls(self):
        return [row[1:] for row in self.call_rows() if row[0] == 'python']

    def log(self):
        return (self.evidence / 'build.log').read_text()


@contextlib.contextmanager
def fake_repository(*, omitted=None):
    with tempfile.TemporaryDirectory(prefix='vui-provenance-') as directory:
        yield FakeRepository(directory, omitted=omitted)


class RootProvenanceTests(unittest.TestCase):
    def assert_blocked(self, fixture, **kwargs):
        result = fixture.run(**kwargs)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(fixture.python_calls(), [], fixture.calls.read_text())
        self.assertTrue((fixture.evidence / 'build.log').exists())
        return result

    def assert_accepted(self, fixture):
        result = fixture.run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr + fixture.log())
        self.assertEqual(fixture.python_calls(), [
            ['scripts/build_bundle.py', str(fixture.evidence / 'vui-linux.zip'),
             '--source-commit', fixture.head],
            ['-B', 'scripts/low_resource_root.py', '--bundle',
             str(fixture.evidence / 'vui-linux.zip'), '--source-commit', fixture.head,
             '--output', str(fixture.evidence / 'root-result.json')],
        ])
        return result

    def test_exact_reviewed_blob_and_regular_nonexecutable_mode_are_accepted(self):
        with fake_repository() as fixture:
            self.assertEqual(fixture.git('ls-tree', 'HEAD', '--', RELEASE_PATH).stdout,
                             '100644 blob ' + fixture.blob + '\t' + RELEASE_PATH + '\n')
            self.assert_accepted(fixture)

    def test_documentation_and_test_only_commit_is_accepted(self):
        with fake_repository() as fixture:
            fixture.write('docs/fixture.md', b'Updated synthetic documentation.\n')
            fixture.write('README.md', b'Synthetic readme.\n')
            fixture.write('tests/test_synthetic.py', b'# inert test fixture\n')
            fixture.commit()
            self.assert_accepted(fixture)

    def test_dirty_unprotected_documentation_does_not_expand_product_boundary(self):
        with fake_repository() as fixture:
            fixture.write('docs/fixture.md', b'Uncommitted documentation.\n')
            self.assert_accepted(fixture)

    def test_release_entry_rejects_other_old_executable_symlink_gitlink_missing_and_tree(self):
        for mutation in ('other-blob', 'old-blob', 'executable', 'symlink',
                         'gitlink', 'missing', 'directory'):
            with self.subTest(mutation=mutation), fake_repository() as fixture:
                target = fixture.repo / RELEASE_PATH
                if mutation in ('other-blob', 'old-blob'):
                    target.write_bytes(OLD_RELEASE if mutation == 'old-blob' else b'not reviewed\n')
                elif mutation == 'executable':
                    target.chmod(0o755)
                else:
                    target.unlink()
                    if mutation == 'symlink':
                        target.symlink_to('untrusted-target')
                    elif mutation == 'directory':
                        target.mkdir()
                        (target / 'nested.py').write_bytes(REVIEWED_RELEASE)
                    elif mutation == 'gitlink':
                        fixture.git('update-index', '--add', '--cacheinfo',
                                    '160000,' + fixture.head + ',' + RELEASE_PATH)
                fixture.commit(stage=mutation != 'gitlink')
                self.assert_blocked(fixture)
                self.assertIn('actual_entry=', fixture.log())
                self.assertNotIn('baseline_protected_tree:', fixture.log())

    def test_all_protected_prefixes_and_files_reject_each_committed_change_kind(self):
        for path in PROTECTED_FILES:
            for mutation in ('add', 'delete', 'rename', 'content', 'mode'):
                with self.subTest(path=path, mutation=mutation), fake_repository(
                        omitted=path if mutation == 'add' else None) as fixture:
                    target = fixture.repo / path
                    if mutation in ('add', 'content'):
                        fixture.write(path, b'Unauthorized product change.\n')
                    elif mutation == 'delete':
                        target.unlink()
                    elif mutation == 'rename':
                        target.rename(target.with_name(target.name + '.renamed'))
                    else:
                        target.chmod(0o755)
                    fixture.commit()
                    self.assert_blocked(fixture)
                    self.assertIn('actual_product_delta:', fixture.log())
                    self.assertIn(path, fixture.log())

    def test_dirty_worktree_and_index_are_checked_for_every_protected_selector(self):
        for path in (*PROTECTED_FILES, RELEASE_PATH):
            for staged in (False, True):
                with self.subTest(path=path, staged=staged), fake_repository() as fixture:
                    fixture.write(path, b'Uncommitted product mutation.\n')
                    if staged:
                        fixture.git('add', '--', path)
                    self.assert_blocked(fixture)
                    self.assertNotIn('baseline_protected_tree:', fixture.log())

    def test_dirty_index_cannot_hide_behind_restored_worktree(self):
        for path in ('app/nested/fixture.txt', RELEASE_PATH, 'scripts/build_bundle.py'):
            for mutation in ('content', 'mode'):
                with self.subTest(path=path, mutation=mutation), fake_repository() as fixture:
                    target = fixture.repo / path
                    original = target.read_bytes()
                    if mutation == 'mode':
                        fixture.git('update-index', '--chmod=+x', '--', path)
                    else:
                        target.write_bytes(b'Staged unauthorized product.\n')
                        fixture.git('add', '--', path)
                        target.write_bytes(original)
                    # This is the bypass missed by a HEAD-to-worktree check alone.
                    self.assertEqual(fixture.git('diff', '--exit-code', fixture.head,
                                                 '--', path, check=False).returncode, 0)
                    self.assertEqual(fixture.git('diff', '--cached', '--exit-code',
                                                 fixture.head, '--', path,
                                                 check=False).returncode, 1)
                    self.assert_blocked(fixture)
                    self.assertNotIn('baseline_protected_tree:', fixture.log())

    def test_staged_deletion_is_rejected_even_when_original_file_is_restored(self):
        for path in ('app/nested/fixture.txt', RELEASE_PATH, 'scripts/build_bundle.py'):
            with self.subTest(path=path), fake_repository() as fixture:
                original = (fixture.repo / path).read_bytes()
                fixture.git('rm', '--', path)
                fixture.write(path, original)
                self.assertEqual((fixture.repo / path).read_bytes(), original)
                self.assertEqual(fixture.git('diff', '--cached', '--exit-code',
                                             fixture.head, '--', path,
                                             check=False).returncode, 1)
                self.assert_blocked(fixture)
                self.assertNotIn('baseline_protected_tree:', fixture.log())

    def test_mode_check_cannot_be_disabled_by_repository_config(self):
        for path in (RELEASE_PATH, 'scripts/build_bundle.py', 'web/nested/fixture.txt'):
            with self.subTest(path=path), fake_repository() as fixture:
                fixture.git('config', 'core.filemode', 'false')
                (fixture.repo / path).chmod(0o755)
                self.assertEqual(fixture.git('diff', '--exit-code', fixture.head,
                                             '--', path, check=False).returncode, 0)
                self.assert_blocked(fixture)

    def test_staged_product_addition_is_rejected_before_builder(self):
        with fake_repository() as fixture:
            fixture.write('app/new_module.py', b'# staged synthetic addition\n')
            fixture.git('add', '--', 'app/new_module.py')
            self.assert_blocked(fixture)

    def test_source_requires_the_exact_nonempty_head_sha(self):
        with fake_repository() as fixture:
            for source in (None, '', 'HEAD', fixture.head[:12], fixture.baseline,
                           '0' * 40, fixture.head + '\n', '$(echo untrusted)'):
                with self.subTest(source=source):
                    self.assert_blocked(fixture, source=source)
                    self.assertIn('expected_source=', fixture.log())
                    self.assertNotIn('actual_entry=', fixture.log())

    def test_every_failed_git_command_fails_closed_and_preserves_diagnostics(self):
        with fake_repository() as fixture:
            self.assert_accepted(fixture)
            commands = [row[1:] for row in fixture.call_rows() if row[0] == 'git']
            self.assertEqual(len(commands), 8)
            for command in commands:
                with self.subTest(command=command):
                    result = self.assert_blocked(fixture, git_failure=' '.join(command))
                    self.assertEqual(result.returncode, 73)
                    self.assertIn('fixture Git failure: ' + ' '.join(command), fixture.log())

    def test_exact_path_pin_and_both_clean_checks_precede_the_only_exclusion(self):
        with fake_repository() as fixture:
            self.assert_accepted(fixture)
            calls = fixture.call_rows()
            git_calls = [row[1:] for row in calls if row[0] == 'git']
            self.assertEqual(git_calls[0], ['rev-parse', 'HEAD'])
            self.assertEqual(git_calls[1], ['ls-tree', '--full-tree', fixture.head,
                                           '--', RELEASE_PATH])
            diff_calls = [args for args in git_calls if 'diff' in args]
            self.assertEqual(len(diff_calls), 4)
            for args in diff_calls:
                paths = args[args.index('--') + 1:]
                self.assertEqual(paths, list(PROTECTED_PATHS) +
                                 ([EXCLUSION] if args is diff_calls[-1] else []))
                self.assertIn('--no-ext-diff', args)
                self.assertIn('--no-textconv', args)
            self.assertNotIn('--cached', diff_calls[0])
            self.assertIn('--cached', diff_calls[1])
            for args in diff_calls[:2]:
                self.assertIn('core.filemode=true', args)
                self.assertIn('--exit-code', args)
                self.assertIn(fixture.head, args)
                self.assertNotIn(fixture.baseline, args)
            self.assertIn('--exit-code', diff_calls[-1])
            self.assertIn(fixture.baseline, diff_calls[-1])
            self.assertIn(fixture.head, diff_calls[-1])
            self.assertTrue(all(row[0] == 'git' for row in calls[:-2]))
            self.assertEqual(sum(EXCLUSION in row for row in git_calls), 1)

    def test_build_log_appends_output_without_losing_provenance(self):
        with fake_repository() as fixture:
            self.assert_accepted(fixture)
            log = fixture.log()
            for expected in ('baseline=' + fixture.baseline,
                             'expected_source=' + fixture.head,
                             'actual_head=' + fixture.head,
                             'expected_entry=100644 blob ' + fixture.blob + '\t' + RELEASE_PATH,
                             'actual_entry=100644 blob ' + fixture.blob + '\t' + RELEASE_PATH,
                             'baseline_protected_tree:', 'candidate_protected_tree:',
                             'actual_product_delta:', 'fixture builder stdout',
                             'fixture builder stderr'):
                self.assertIn(expected, log)
            self.assertLess(log.index('actual_product_delta:'), log.index('fixture builder stdout'))

    def test_builder_failure_preserves_log_and_never_reaches_root(self):
        with fake_repository() as fixture:
            result = fixture.run(builder_status=29)
            self.assertEqual(result.returncode, 29)
            self.assertEqual(len(fixture.python_calls()), 1)
            self.assertEqual(fixture.python_calls()[0][0], 'scripts/build_bundle.py')
            self.assertIn('actual_product_delta:', fixture.log())
            self.assertIn('fixture builder stderr', fixture.log())


class RootWorkflowSafetyTests(unittest.TestCase):
    def test_production_constants_and_complete_protected_paths_are_fixed(self):
        guard, _ = workflow_scripts()
        self.assertEqual(guard.count(BASELINE), 1)
        self.assertEqual(guard.count(REVIEWED_BLOB), 1)
        self.assertIn('BASELINE=' + BASELINE, guard)
        self.assertIn("REVIEWED_ENTRY=$'100644 blob " + REVIEWED_BLOB +
                      "\\tapp/release_tools.py'", guard)
        paths, = re.findall(r'^\s*PRODUCT_PATHS=\(([^)]*)\)$', guard, re.MULTILINE)
        self.assertEqual(shlex.split(paths), list(PROTECTED_PATHS))
        self.assertEqual(guard.count(EXCLUSION), 1)
        self.assertIn("'" + EXCLUSION + "'", guard)
        self.assertEqual(guard.count('python '), 1)
        self.assertIn('2>&1 | tee -a ' + EVIDENCE_PATH + '/build.log', guard)

    def test_opt_in_checkout_pins_limits_and_failure_artifact_remain_unchanged(self):
        workflow = WORKFLOW.read_text()
        for fragment in (
            'on:\n  pull_request:\n    types: [labeled]\n',
            'permissions:\n  contents: read\n',
            "if: github.event.label.name == 'run-low-resource-root-install'",
            'runs-on: ubuntu-24.04\n    timeout-minutes: 40',
            'persist-credentials: false\n          fetch-depth: 0',
            'ref: ${{ github.event.pull_request.head.sha }}',
            "python-version: '3.12'", 'run: pip install -r requirements-test.txt',
            'name: Preserve diagnostics including failures\n        if: always()',
            'name: vui-root-resource-evidence-${{ github.event.pull_request.head.sha }}',
            '            /tmp/vui-root-evidence/*.json\n            /tmp/vui-root-evidence/*.log',
            'retention-days: 7',
        ):
            self.assertIn(fragment, workflow)
        self.assertEqual(re.findall(r'uses: (\S+)', workflow), [
            'actions/checkout@11d5960a326750d5838078e36cf38b85af677262',
            'actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065',
            'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02',
        ])
        self.assertEqual(workflow.count('VUI_SOURCE_COMMIT: ${{ github.event.pull_request.head.sha }}'), 2)
        self.assertNotIn('continue-on-error', workflow)
        self.assertNotIn('pull_request_target', workflow)
        _, root = workflow_scripts()
        self.assertEqual(root, textwrap.dedent('''\
            set -euo pipefail
            python -B scripts/low_resource_root.py \\
              --bundle /tmp/vui-root-evidence/vui-linux.zip \\
              --source-commit "$VUI_SOURCE_COMMIT" \\
              --output /tmp/vui-root-evidence/root-result.json
            '''))
        runner = (SOURCE / 'scripts/low_resource_root.py').read_text()
        quota = r'MemoryMax=536870912\nMemorySwapMax=0\nCPUQuota=100%\nCPUQuotaPeriodSec=100ms\n'
        self.assertEqual(runner.count(quota), 2)


if __name__ == '__main__':
    unittest.main()
