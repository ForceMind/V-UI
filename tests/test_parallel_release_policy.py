"""Pure fake API/ZIP and isolated Git qualification; no installer, runtime or network."""
from contextlib import contextmanager
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
import warnings
import zipfile
from unittest.mock import patch

from scripts import parallel_release_policy as policy
from scripts import release_acceptance as core
from scripts.publish_release import BROWSER_SKIP_STEPS, validate_kit, validate_target_artifact

SHA = 'a' * 40
VERSION = '0.4.22'
BASELINES = ['0.4.14', '0.4.15', '0.4.16', '0.4.17', '0.4.19', '0.4.20', '0.4.21']
REQUIRED = {'test.yml': {'Run tests'}, 'loopback.yml': {'Actual TCP transfer'},
            'release.yml': {'Actual cgroup install'}, 'acme.yml': {'Actual Certbot issuance'},
            'short-install.yml': {'Actual public installation'}}


def archive(entries):
    output = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(output, 'w') as writer:
        warnings.simplefilter('ignore', UserWarning)
        for name, raw in entries:
            writer.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), raw)
    return output.getvalue()


def bundle(target, version):
    return archive([('MANIFEST.json', json.dumps({'source_commit': SHA, 'version': version,
        'platform': 'linux-multi-cpython312', 'targets': [target]}).encode())])


class Materials:
    """Independent role and tiny manifest fixtures matching the legacy promotion format."""
    def __init__(self, version=VERSION):
        self.version = version
        self.roles = ['build', 'fresh', *('upgrade-' + value for value in BASELINES)]
        if version in ('0.4.23','0.4.24'): self.roles.append('upgrade-0.4.22')
        if version == '0.4.24': self.roles.append('upgrade-0.4.23')
        self.checks = {}; self.jobs = {}; self.artifacts = {}; self.bytes = {}; self.downloads = []
        self.reread = {}; self.pages = {}
        for index, path in enumerate(sorted(policy.WORKFLOWS), 1):
            run = dict(id=index, run_attempt=2, path='.github/workflows/' + path, head_sha=SHA,
                       status='completed', conclusion='success', run_started_at='2026-11-08T00:00:00Z',
                       updated_at='2026-11-08T02:00:00Z')
            self.checks[path] = run
            if path == 'oneclick.yml':
                specifications = [(self.job_name(role), self.role_steps(role)) for role in self.roles]
            elif path == 'portable.yml':
                specifications = [
                    ('native-arm64-glibc', ['Build exact aarch64 glibc target']),
                    ('musl (ubuntu-24.04, x86_64)', ['Build and stage native Alpine/musl target']),
                    ('musl (ubuntu-24.04-arm, aarch64)', ['Build and stage native Alpine/musl target']),
                    ('distro-probe', ['Probe representative distro families without a whitelist'])]
            else:
                specifications = [(path, ['Nonbrowser work', *sorted(REQUIRED.get(path, ())),
                                                *sorted(BROWSER_SKIP_STEPS.get(path, ()))])]
            self.jobs[index] = [dict(id=index * 100 + number, name=name, run_id=index, head_sha=SHA,
                status='completed', conclusion='success', steps=[dict(name=step, status='completed',
                conclusion='skipped' if step in BROWSER_SKIP_STEPS.get(path, ()) else 'success') for step in steps])
                for number, (name, steps) in enumerate(specifications, 1)]
            self.artifacts[index] = []
        files = {name: b'fixture bytes' for name in core.KIT_FILES - {'SHA256SUMS'}}
        files[core.BUNDLE] = bundle('x86_64-gnu', version)
        files['RELEASE.json'] = json.dumps({'version': version, 'source_commit': SHA}).encode()
        files['SHA256SUMS'] = b''.join(hashlib.sha256(raw).hexdigest().encode() + b'  ' + name.encode() + b'\n'
                                     for name, raw in sorted(files.items()))
        self.kit_files = files
        run = self.checks['oneclick.yml']
        self.producer = self.add(run, f"vui-release-kit-{SHA}-{run['id']}-2", archive(sorted(files.items())))
        self.receipts = {}; self.logs = {}
        for role in self.roles:
            identity = {'schema': 1, 'repository': core.REPOSITORY, 'source_sha': SHA, 'version': version,
                'target': 'x86_64-gnu', 'release_id': version + '-' + SHA[:12],
                'checksums_sha256': hashlib.sha256(files['SHA256SUMS']).hexdigest(),
                'bundle_sha256': hashlib.sha256(files[core.BUNDLE]).hexdigest(),
                'validation_scope': 'producer-inspection' if role == 'build' else 'consumer-files'}
            receipt = core.create_receipt(identity, run_id=run['id'], run_attempt=2, role=role,
                producer_artifact_id=self.producer['id'], producer_artifact_digest=self.producer['digest'])
            self.receipts[role] = self.add(run, f"vui-oneclick-receipt-{role}-{SHA}-{run['id']}-2",
                archive([('acceptance-receipt.json', json.dumps(receipt).encode())]))
            self.logs[role] = self.add(run, f"vui-oneclick-evidence-{role}-{SHA}-{run['id']}-2", b'log artifact')
        self.portable = {}
        run = self.checks['portable.yml']
        for target in ('aarch64-gnu', 'x86_64-musl', 'aarch64-musl'):
            raw = bundle(target, version); name = 'vui-linux-' + target + '.zip'
            self.portable[target] = self.add(run, f"vui-linux-{target}-{SHA}-{run['id']}-2", archive([
                (name, raw), (name + '.sha256', hashlib.sha256(raw).hexdigest().encode() + b'  ' + name.encode() + b'\n')]))

    @staticmethod
    def job_name(role):
        return 'build-kit' if role == 'build' else 'system-install' if role == 'fresh' else 'published-' + role

    @staticmethod
    def role_steps(role):
        if role == 'build':
            return ['Build exact candidate for installer test', 'Assemble exact candidate kit for combined installation acceptance',
                'Inspect producer candidate kit', 'Upload release kit', 'Record producer receipt',
                'Upload producer receipt', 'Upload acceptance logs']
        old = role[len('upgrade-'):]
        return (['Check out immutable published v' + old + ' source for real upgrade acceptance'] if role != 'fresh' else []) + [
            'Download exact candidate kit', 'Prepare verified candidate kit',
            'Test interactive installer and global v-ui on ephemeral systemd runner' if role == 'fresh' else
                f'Test published v{old} to exact candidate upgrade on ephemeral systemd runner',
            'Record acceptance receipt', 'Upload acceptance receipt', 'Upload acceptance logs']

    def add(self, run, name, raw):
        identity = 10000 + len(self.bytes)
        item = dict(id=identity, name=name, expired=False, size_in_bytes=len(raw),
            digest='sha256:' + hashlib.sha256(raw).hexdigest(), created_at='2026-11-08T01:00:00Z',
            workflow_run=dict(id=run['id'], head_sha=SHA, repository_id=404))
        self.artifacts[run['id']].append(item); self.bytes[identity] = raw
        return item

    def replace(self, item, raw):
        self.bytes[item['id']] = raw; item['size_in_bytes'] = len(raw)
        item['digest'] = 'sha256:' + hashlib.sha256(raw).hexdigest()

    def change_receipt(self, role, mutate=None, raw=None, entries=None):
        item = self.receipts[role]
        if raw is None and entries is None:
            with zipfile.ZipFile(io.BytesIO(self.bytes[item['id']])) as z:
                value = json.loads(z.read('acceptance-receipt.json'))
            mutate(value); raw = json.dumps(value).encode()
        self.replace(item, archive(entries or [('acceptance-receipt.json', raw)]))

    def api(self, path):
        if path in self.pages: return copy.deepcopy(self.pages[path])
        if path == '': return {'id': 404}
        parts = path.split('/')
        identity = int(parts[2])
        if '/jobs?' in path:
            return {'jobs': copy.deepcopy(self.jobs[identity])}
        if '/artifacts?' in path:
            return {'artifacts': copy.deepcopy(self.artifacts[identity])}
        if path == 'actions/runs/' + str(identity):
            return copy.deepcopy(self.reread.get(identity, next(run for run in self.checks.values() if run['id'] == identity)))
        raise AssertionError(path)

    def download(self, item, *, max_bytes):
        self.downloads.append((item['id'], max_bytes))
        return self.bytes[item['id']]

    def qualify(self, **overrides):
        args = dict(api=self.api, download=self.download, validate_kit=validate_kit,
            validate_target_artifact=validate_target_artifact, browser_skip_steps=BROWSER_SKIP_STEPS,
            required_other_steps=REQUIRED)
        args.update(overrides)
        return policy.validate_parallel_runs(self.checks, SHA, self.version, **args)

    def job(self, role):
        return next(value for value in self.jobs[self.checks['oneclick.yml']['id']] if value['name'] == self.job_name(role))


class ParallelMaterialsTests(unittest.TestCase):
    def test_fake_archives_keep_identical_bytes_across_clock_boundaries(self):
        with patch('zipfile.time.localtime', side_effect=[
                (2026,11,10,0,0,0,1,314,0),(2026,11,10,0,0,2,1,314,0)]):
            first=bundle('x86_64-gnu','0.4.23')
            second=bundle('x86_64-gnu','0.4.23')
        self.assertEqual(first,second)
        with zipfile.ZipFile(io.BytesIO(first)) as reader:
            self.assertEqual(reader.getinfo('MANIFEST.json').date_time,(1980,1,1,0,0,0))

    def test_future_publisher_requires_real_dns_ca_and_both_core_steps_without_skip(self):
        from scripts.publish_release import _parallel_other_steps
        fixture = Materials('0.4.23')
        required = _parallel_other_steps()
        for path, names in required.items():
            steps = fixture.jobs[fixture.checks[path]['id']][0]['steps']
            present = {step['name'] for step in steps}
            steps.extend(dict(name=name, status='completed', conclusion='success') for name in sorted(names - present))
        policy._jobs(fixture.checks, SHA, '0.4.23', fixture.api, BROWSER_SKIP_STEPS, required)
        for path, name in (
                ('acme.yml', 'Test actual Certbot DNS-01 wildcard renewal and legacy-reader refusal'),
                ('loopback.yml', 'Exercise DNS wildcard material through actual sing-box and Xray TLS')):
            steps = fixture.jobs[fixture.checks[path]['id']][0]['steps']
            step = next(value for value in steps if value['name'] == name)
            with self.subTest(path=path, fault='missing'):
                steps.remove(step)
                with self.assertRaises(policy.ParallelPolicyError):
                    policy._jobs(fixture.checks, SHA, '0.4.23', fixture.api, BROWSER_SKIP_STEPS, required)
                steps.append(step)
            with self.subTest(path=path, fault='skipped'):
                step['conclusion'] = 'skipped'
                with self.assertRaises(policy.ParallelPolicyError):
                    policy._jobs(fixture.checks, SHA, '0.4.23', fixture.api, BROWSER_SKIP_STEPS, required)
                step['conclusion'] = 'success'
        self.assertEqual(fixture.downloads, [])

    def test_current_and_future_matrix_require_every_role_and_return_original_bytes(self):
        for version, count in [('0.4.22', 9), ('0.4.23', 10), ('0.4.24', 11)]:
            with self.subTest(version=version):
                fixture = Materials(version); result = fixture.qualify()
                self.assertEqual(len(fixture.jobs[fixture.checks['oneclick.yml']['id']]), count)
                self.assertEqual(result['kit_files'], fixture.kit_files)
                self.assertEqual(result['evidence']['producer']['artifact_id'], fixture.producer['id'])
                self.assertEqual(set(result['evidence']['receipts']), set(fixture.roles))
                self.assertFalse(result['evidence']['browser_verified'])
                for target, raw in result['portable_bundles'].items(): self.assertEqual(raw, bundle(target, version))
                downloaded = dict(fixture.downloads)
                for item in fixture.receipts.values(): self.assertEqual(downloaded[item['id']], 65536)
                self.assertTrue(set(item['id'] for item in fixture.logs.values()).isdisjoint(downloaded))

    def test_missing_wrong_or_duplicate_workflow_and_job_identity(self):
        for mutation in ('missing-workflow', 'extra-workflow', 'run-source', 'run-id-bool', 'attempt-bool', 'run-state',
                         'workflow-path', 'missing-job', 'duplicate-job', 'wrong-role', 'job-source', 'job-run', 'job-state', 'job-id-bool'):
            with self.subTest(mutation=mutation), self.assertRaises(policy.ParallelPolicyError):
                f = Materials(); run = f.checks['oneclick.yml']; jobs = f.jobs[run['id']]
                if mutation == 'missing-workflow': f.checks.pop('docs.yml')
                elif mutation == 'extra-workflow': f.checks['unexpected.yml'] = dict(run)
                elif mutation == 'run-source': run['head_sha'] = 'b' * 40
                elif mutation == 'run-id-bool': run['id'] = True
                elif mutation == 'attempt-bool': run['run_attempt'] = True
                elif mutation == 'run-state': run['status'] = 'queued'
                elif mutation == 'workflow-path': run['path'] = '.github/workflows/test.yml'
                elif mutation == 'missing-job': jobs.pop()
                elif mutation == 'duplicate-job': jobs[-1] = copy.deepcopy(jobs[0])
                elif mutation == 'wrong-role': jobs[-1]['name'] = 'published-upgrade-0.4.22'
                elif mutation == 'job-source': jobs[0]['head_sha'] = 'b' * 40
                elif mutation == 'job-run': jobs[0]['run_id'] = 999
                elif mutation == 'job-state': jobs[0]['conclusion'] = 'failure'
                else: jobs[0]['id'] = True
                f.qualify()

    def test_required_steps_cannot_be_failed_skipped_relocated_or_reordered(self):
        for mutation in ('missing', 'failed', 'skipped', 'empty', 'relocated', 'reordered', 'checkout-late'):
            with self.subTest(mutation=mutation), self.assertRaises(policy.ParallelPolicyError):
                f = Materials(); steps = f.job('fresh')['steps']
                if mutation == 'missing': steps.pop(1)
                elif mutation == 'failed': steps[1]['conclusion'] = 'failure'
                elif mutation == 'skipped': steps[1]['conclusion'] = 'skipped'
                elif mutation == 'empty': steps[:] = []
                elif mutation == 'relocated': f.job('build')['steps'].append(steps.pop(1))
                elif mutation == 'reordered': steps[2], steps[3] = steps[3], steps[2]
                else:
                    steps = f.job('upgrade-0.4.14')['steps']; steps.append(steps.pop(0))
                f.qualify()
        f = Materials(); f.job('fresh')['steps'][0] = {}
        with self.assertRaises(policy.ParallelPolicyError): f.qualify()

    def test_portable_build_and_probe_are_required_in_their_own_four_jobs(self):
        for name in ('native-arm64-glibc', 'musl (ubuntu-24.04, x86_64)', 'musl (ubuntu-24.04-arm, aarch64)', 'distro-probe'):
            f = Materials(); jobs = f.jobs[f.checks['portable.yml']['id']]
            job = next(value for value in jobs if value['name'] == name)
            other = next(value for value in jobs if value is not job)
            other['steps'].extend(job['steps']); job['steps'] = [dict(name='Unrelated work', status='completed', conclusion='success')]
            with self.subTest(name=name), self.assertRaises(policy.ParallelPolicyError): f.qualify()

    def test_browser_skip_contract_and_required_workflow_steps_stay_fixed(self):
        f = Materials(); jobs = f.jobs[f.checks['test.yml']['id']]
        jobs[0]['steps'] = [step for step in jobs[0]['steps'] if step['name'] != 'Browser authentication smoke']
        with self.assertRaises(policy.ParallelPolicyError): f.qualify()
        f = Materials(); jobs = f.jobs[f.checks['test.yml']['id']]
        jobs[0]['steps'][0]['conclusion'] = 'skipped'
        with self.assertRaises(policy.ParallelPolicyError): f.qualify()
        f = Materials(); browser = copy.deepcopy(BROWSER_SKIP_STEPS); browser['test.yml'].add('Nonbrowser work')
        with self.assertRaises(policy.ParallelPolicyError): f.qualify(browser_skip_steps=browser)
        f = Materials(); steps = f.jobs[f.checks['test.yml']['id']][0]['steps']
        moved = next(step for step in steps if step['name'] == 'Run tests'); steps.remove(moved)
        f.jobs[f.checks['docs.yml']['id']][0]['steps'].append(moved)
        with self.assertRaises(policy.ParallelPolicyError): f.qualify()

    def test_artifact_metadata_is_required_even_when_downloader_is_permissive(self):
        mutations = [('digest', None), ('digest', 'sha256:' + '0' * 64), ('id', True), ('size_in_bytes', True),
            ('size_in_bytes', 0), ('expired', True), ('expired', None), ('created_at', '2020-01-01T00:00:00Z'),
            ('created_at', 'bad'), ('created_at', '2026-11-08T01:00:00'), ('run_attempt', 1)]
        for field, value in mutations:
            f = Materials(); f.producer[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(policy.ParallelPolicyError): f.qualify()
        for field, value in [('id', 999), ('head_sha', 'b' * 40), ('repository_id', 999), ('repository_id', True), ('run_attempt', 1)]:
            f = Materials(); f.producer['workflow_run'][field] = value
            with self.subTest(origin=field), self.assertRaises(policy.ParallelPolicyError): f.qualify()

    def test_artifact_missing_duplicate_names_and_wrong_attempt_name_fail(self):
        for mutation in ('missing-receipt', 'missing-log', 'duplicate-name', 'duplicate-id', 'wrong-attempt', 'extra'):
            f = Materials(); items = f.artifacts[f.checks['oneclick.yml']['id']]
            if mutation == 'missing-receipt': items.remove(f.receipts['fresh'])
            elif mutation == 'missing-log': items.remove(f.logs['fresh'])
            elif mutation == 'duplicate-name': items.append(copy.deepcopy(f.producer))
            elif mutation == 'duplicate-id': f.logs['fresh']['id'] = f.producer['id']
            elif mutation == 'wrong-attempt': f.receipts['fresh']['name'] = f.receipts['fresh']['name'][:-1] + '1'
            else: f.add(f.checks['oneclick.yml'], 'unexpected artifact', b'x')
            with self.subTest(mutation=mutation), self.assertRaises(policy.ParallelPolicyError): f.qualify()

    def test_earlier_attempt_artifacts_are_never_downloaded_or_reused(self):
        f = Materials()
        oneclick = f.checks['oneclick.yml']; portable = f.checks['portable.yml']
        previous = []
        for run, current in ((oneclick, f.producer), (oneclick, f.receipts['fresh']),
                             (oneclick, f.logs['fresh']), (portable, f.portable['aarch64-gnu'])):
            item = f.add(run, current['name'][:-1] + '1', b'old attempt bytes')
            item['expired'] = True; item['created_at'] = '2026-11-07T00:00:00Z'
            if len(previous) < 2:
                item['run_attempt'] = 1; item['workflow_run']['run_attempt'] = 1
            previous.append(item['id'])
        result = f.qualify()
        self.assertEqual(result['evidence']['producer']['artifact_id'], f.producer['id'])
        self.assertTrue(set(previous).isdisjoint(identity for identity, _ in f.downloads))
        f = Materials(); f.receipts['fresh']['name'] = f.receipts['fresh']['name'][:-1] + '1'
        with self.assertRaises(policy.ParallelPolicyError): f.qualify()

    def test_earlier_attempt_ignore_requires_exact_run_source_repository_and_attempt(self):
        mutations = [('origin', 'id', 999), ('origin', 'id', True),
            ('origin', 'head_sha', 'b' * 40), ('origin', 'repository_id', 999),
            ('origin', 'repository_id', True), ('origin', 'repository_id', None),
            ('origin', 'run_attempt', 2), ('origin', 'run_attempt', True),
            ('origin', 'run_attempt', 0), ('origin', 'run_attempt', '1'),
            ('artifact', 'run_attempt', 2), ('artifact', 'run_attempt', True),
            ('artifact', 'run_attempt', 0), ('artifact', 'run_attempt', '1')]
        for kind in ('kit', 'receipt', 'log', 'portable'):
            for location, field, value in mutations:
                f = Materials()
                current = (f.producer if kind == 'kit' else f.receipts['fresh'] if kind == 'receipt'
                           else f.logs['fresh'] if kind == 'log' else f.portable['aarch64-gnu'])
                run = f.checks['portable.yml' if kind == 'portable' else 'oneclick.yml']
                item = f.add(run, current['name'][:-1] + '1', b'older attempt bytes')
                item['expired'] = True; item['created_at'] = '2026-11-07T00:00:00Z'
                target = item['workflow_run'] if location == 'origin' else item
                target[field] = value
                with self.subTest(kind=kind, location=location, field=field, value=value), \
                        self.assertRaises(policy.ParallelPolicyError):
                    f.qualify()
                self.assertNotIn(item['id'], [identity for identity, _ in f.downloads])
        f = Materials(); run = f.checks['oneclick.yml']
        item = f.add(run, f.producer['name'][:-1] + '1', b'older attempt bytes')
        item.pop('workflow_run')
        with self.assertRaises(policy.ParallelPolicyError): f.qualify()

    def test_transport_digest_size_and_role_bound_limits_are_independent(self):
        f = Materials(); f.bytes[f.producer['id']] += b'tamper'
        with self.assertRaises(policy.ParallelPolicyError): f.qualify()
        f = Materials(); item = f.receipts['fresh']; item['size_in_bytes'] = 65537
        with self.assertRaises(policy.ParallelPolicyError): f.qualify()
        f = Materials(); item = f.receipts['fresh']; f.bytes[item['id']] = b'bad' + f.bytes[item['id']][3:]
        with self.assertRaises(policy.ParallelPolicyError): f.qualify()

    def test_receipt_fields_use_strict_context_scope_schema_and_types(self):
        for field, value in [('source_sha', 'b' * 40), ('version', '0.4.23'), ('role', 'build'),
            ('validation_scope', 'producer-inspection'), ('run_id', 999), ('run_attempt', 1), ('run_attempt', True),
            ('producer_artifact_id', 999), ('producer_artifact_digest', 'sha256:' + '0' * 64),
            ('checksums_sha256', '0' * 64), ('bundle_sha256', '0' * 64), ('schema', True),
            ('old_version', '0.4.14'), ('unexpected', 'field')]:
            f = Materials(); f.change_receipt('fresh', lambda value_dict: value_dict.update({field: value}))
            with self.subTest(field=field), self.assertRaises(policy.ParallelPolicyError): f.qualify()
        f = Materials(); f.change_receipt('upgrade-0.4.14', lambda value: value.update(old_source_sha='b' * 40))
        with self.assertRaises(policy.ParallelPolicyError): f.qualify()
        f = Materials(); f.change_receipt('build', lambda value: value.update(validation_scope='consumer-files'))
        with self.assertRaises(policy.ParallelPolicyError): f.qualify()

    def test_receipt_json_duplicates_nonfinite_bad_utf8_and_zip_members_fail(self):
        for raw in (b'{"schema":1,"schema":1}', b'{"schema":NaN}', b'\xff', b'{}', b'[]', b'x' * 16385):
            f = Materials(); f.change_receipt('fresh', raw=raw)
            with self.subTest(raw=raw[:24]), self.assertRaises(policy.ParallelPolicyError): f.qualify()
        for entries in ([('other.json', b'{}')], [('acceptance-receipt.json', b'{}')] * 2,
                        [('acceptance-receipt.json', b'{}'), ('extra', b'x')]):
            f = Materials(); f.change_receipt('fresh', entries=entries)
            with self.subTest(entries=entries), self.assertRaises(policy.ParallelPolicyError): f.qualify()
        f = Materials(); output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as z:
            item = zipfile.ZipInfo('acceptance-receipt.json'); item.create_system = 3
            item.external_attr = (stat.S_IFLNK | 0o777) << 16; z.writestr(item, b'target')
        f.replace(f.receipts['fresh'], output.getvalue())
        with self.assertRaises(policy.ParallelPolicyError): f.qualify()

    def test_wrong_portable_target_or_transport_identity_fails(self):
        for mutation in ('wrong-target', 'wrong-digest', 'wrong-run', 'wrong-attempt', 'cross-run-id'):
            f = Materials(); item = f.portable['x86_64-musl']
            if mutation == 'wrong-target': f.replace(item, f.bytes[f.portable['aarch64-gnu']['id']])
            elif mutation == 'wrong-digest': item['digest'] = 'sha256:' + '0' * 64
            elif mutation == 'wrong-run': item['workflow_run']['id'] = f.checks['oneclick.yml']['id']
            elif mutation == 'wrong-attempt': item['name'] = item['name'][:-1] + '1'
            else: item['id'] = f.logs['fresh']['id']
            with self.subTest(mutation=mutation), self.assertRaises(policy.ParallelPolicyError): f.qualify()

    def test_pagination_and_final_workflow_reread_fail_closed(self):
        for field, value in [('id', 999), ('head_sha', 'b' * 40), ('run_attempt', 3), ('status', 'in_progress'), ('conclusion', 'failure')]:
            f = Materials(); run = f.checks['test.yml']; f.reread[run['id']] = {**run, field: value}
            with self.subTest(field=field), self.assertRaises(policy.ParallelPolicyError): f.qualify()
        f = Materials(); run = f.checks['test.yml']
        prefix = f"actions/runs/{run['id']}/attempts/2/jobs?per_page=100&page="
        for page in range(1, 11): f.pages[prefix + str(page)] = {'jobs': [{}] * 100}
        with self.assertRaisesRegex(policy.ParallelPolicyError, 'pagination'): f.qualify()
        f = Materials(); run = f.checks['oneclick.yml']
        f.pages[f"actions/runs/{run['id']}/artifacts?per_page=100&page=1"] = {'artifacts': None}
        with self.assertRaises(policy.ParallelPolicyError): f.qualify()


class LatestPublishedTests(unittest.TestCase):
    def fixture(self, annotations=0):
        source = '558687a76cd29f4e4735284926eb05bf6664472e'
        values = {'releases/latest': {'draft': False, 'prerelease': False, 'tag_name': 'v0.4.22'}}
        value = {'type': 'commit', 'sha': source}
        for index in range(annotations):
            identity = str(index + 1) * 40
            values['git/tags/' + identity] = {'object': value}
            value = {'type': 'tag', 'sha': identity}
        values['git/ref/tags/v0.4.22'] = {'object': value}
        return values, source

    def test_known_latest_stable_resolves_direct_and_three_annotated_tags(self):
        for count in range(4):
            values, source = self.fixture(count)
            self.assertEqual(policy.validate_latest_published('0.4.23', values.__getitem__),
                             {'version': '0.4.22', 'source': source})

    def test_latest_release_must_be_known_older_public_and_not_moved(self):
        for mutation in ('draft', 'prerelease', 'nonbool', 'same-version', 'newer', 'unsupported',
                         'bad-tag', 'source', 'bad-object', 'four-annotations'):
            values, _ = self.fixture(4 if mutation == 'four-annotations' else 0)
            latest = values['releases/latest']
            if mutation == 'draft': latest['draft'] = True
            elif mutation == 'prerelease': latest['prerelease'] = True
            elif mutation == 'nonbool': latest['draft'] = 0
            elif mutation == 'same-version': latest['tag_name'] = 'v0.4.23'
            elif mutation == 'newer': latest['tag_name'] = 'v0.4.24'
            elif mutation == 'unsupported': latest['tag_name'] = 'v0.4.18'
            elif mutation == 'bad-tag': latest['tag_name'] = 'v0.4.22-rc.1'
            elif mutation == 'source': values['git/ref/tags/v0.4.22']['object']['sha'] = 'b' * 40
            elif mutation == 'bad-object': values['git/ref/tags/v0.4.22']['object']['type'] = 'tree'
            with self.subTest(mutation=mutation), self.assertRaises(policy.ParallelPolicyError):
                policy.validate_latest_published('0.4.23', values.__getitem__)
        values, _ = self.fixture()
        with self.assertRaises(policy.ParallelPolicyError): policy.validate_latest_published('0.4.22', values.__getitem__)

    def test_candidate24_requires_the_unchanged_public23_tag(self):
        source='be5d8459cdd3eeedce73f2ff6e4663e5eb781e42'
        values={'releases/latest':{'draft':False,'prerelease':False,'tag_name':'v0.4.23'},
                'git/ref/tags/v0.4.23':{'object':{'type':'commit','sha':source}}}
        self.assertEqual(policy.validate_latest_published('0.4.24',values.__getitem__),
                         {'version':'0.4.23','source':source})
        values['git/ref/tags/v0.4.23']['object']['sha']='f'*40
        with self.assertRaises(policy.ParallelPolicyError):policy.validate_latest_published('0.4.24',values.__getitem__)


class ScopeRepository:
    def __init__(self, root):
        self.root = root
        self.git('init', '-q', '--template=')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.test')
        self.git('config', 'core.filemode', 'true')
        self.write('VERSION', b'0.4.23\n')
        self.write('README.md', b'Frozen product\n')
        self.write('app.py', b'Frozen application bytes\n')
        self.write('docs/guide.md', b'Existing guide\n')
        self.base = self.commit()
        self.data = {'schema': 1, 'version': '0.4.23', 'product_commit': self.base,
                     'install_contract': 'parallel-v1', 'browser_policy': 'unverified-non-browser-v1'}
        self.write_policy()
        self.candidate = self.commit()

    def git(self, *args, input=None):
        return subprocess.check_output(['git', '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null',
                                        *args], cwd=self.root, input=input, stderr=subprocess.DEVNULL)

    def write(self, name, data):
        path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)

    def write_policy(self):
        self.write(policy.POLICY_PATH, json.dumps(self.data).encode())

    def commit(self):
        self.git('add', '-f', '--all'); self.git('commit', '-qm', 'Fixture snapshot')
        return self.git('rev-parse', 'HEAD').decode().strip()


@contextmanager
def repository():
    with tempfile.TemporaryDirectory(prefix='vui-policy-fixture-') as directory:
        yield ScopeRepository(Path(directory))


class SourceScopeTests(unittest.TestCase):
    def test_policy_binds_ancestor_version_and_only_regular_documentation(self):
        with repository() as repo:
            repo.write('README.md', b'Updated entry\n')
            repo.write('docs/subdirectory/guide.md', b'New guide\n')
            candidate = repo.commit()
            # Dirty unrelated working bytes do not replace the committed source.
            repo.write('app.py', b'Uncommitted unrelated WIP\n')
            value = policy.validate_source_scope(candidate, '0.4.23', root=repo.root)
            self.assertEqual(value, repo.data)
            self.assertEqual(value['product_commit'], repo.base)
        with self.assertRaises(policy.ParallelPolicyError): policy.validate_source_scope(SHA, '0.4.22')

    def test_application_ci_helper_test_and_publisher_changes_are_not_release_preparation(self):
        for path in ('app.py', 'VERSION', '.github/workflows/test.yml', 'scripts/acceptance_job.py',
                     'tests/test_parallel_release_policy.py', 'scripts/publish_release.py', 'docs/not-markdown.txt'):
            with self.subTest(path=path), repository() as repo:
                repo.write(path, b'Changed source\n'); candidate = repo.commit()
                with self.assertRaises(policy.ParallelPolicyError):
                    policy.validate_source_scope(candidate, '0.4.23', root=repo.root)

    def test_deletion_rename_type_and_mode_changes_are_refused(self):
        for mutation in ('delete', 'rename', 'symlink', 'mode', 'policy-mode', 'policy-link'):
            with self.subTest(mutation=mutation), repository() as repo:
                path = repo.root / 'README.md'
                if mutation == 'delete': path.unlink()
                elif mutation == 'rename': path.rename(repo.root / 'SUPPORT.md')
                elif mutation == 'symlink': path.unlink(); path.symlink_to('app.py')
                elif mutation == 'mode': path.chmod(0o755)
                elif mutation == 'policy-mode': (repo.root / policy.POLICY_PATH).chmod(0o755)
                else:
                    path = repo.root / policy.POLICY_PATH; path.unlink(); path.symlink_to('../README.md')
                candidate = repo.commit()
                with self.assertRaises(policy.ParallelPolicyError):
                    policy.validate_source_scope(candidate, '0.4.23', root=repo.root)

    def test_policy_exact_schema_strict_json_and_size(self):
        variants = [('schema-bool', lambda data: data.update(schema=True)),
            ('version', lambda data: data.update(version='0.4.24')),
            ('product', lambda data: data.update(product_commit='not-a-source')),
            ('contract', lambda data: data.update(install_contract='legacy')),
            ('browser', lambda data: data.update(browser_policy='verified')),
            ('invented-authority', lambda data: data.update(approved=True))]
        for name, mutate in variants:
            with self.subTest(name=name), repository() as repo:
                mutate(repo.data); repo.write_policy(); candidate = repo.commit()
                with self.assertRaises(policy.ParallelPolicyError):
                    policy.validate_source_scope(candidate, '0.4.23', root=repo.root)
        for raw in (b'{"schema":1,"schema":1}', b'{"schema":NaN}', b'\xff', b'{}', b'[]', b'x' * 16385):
            with self.subTest(raw=raw[:20]), repository() as repo:
                repo.write(policy.POLICY_PATH, raw); candidate = repo.commit()
                with self.assertRaises(policy.ParallelPolicyError):
                    policy.validate_source_scope(candidate, '0.4.23', root=repo.root)

    def test_nonancestor_and_wrong_frozen_version_are_refused(self):
        with repository() as repo:
            unrelated = repo.git('commit-tree', 'HEAD^{tree}', input=b'Unrelated fixture\n').decode().strip()
            repo.data['product_commit'] = unrelated; repo.write_policy(); candidate = repo.commit()
            with self.assertRaises(policy.ParallelPolicyError):
                policy.validate_source_scope(candidate, '0.4.23', root=repo.root)
        with repository() as repo:
            repo.write('VERSION', b'0.4.24\n'); changed_base = repo.commit()
            repo.data['product_commit'] = changed_base; repo.write_policy(); candidate = repo.commit()
            with self.assertRaises(policy.ParallelPolicyError):
                policy.validate_source_scope(candidate, '0.4.23', root=repo.root)


if __name__ == '__main__':
    unittest.main()
