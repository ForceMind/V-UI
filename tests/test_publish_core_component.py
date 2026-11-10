"""Offline component promotion evidence and API state machine."""

from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from scripts import build_core_component as builder
from scripts import publish_core_component as promote
from test_core_component_builder import Fixture, zipped

COMMIT = 'a' * 40
VERSION = '0.4.24'
APP_TAG = 'v' + VERSION
RUN = 1234
WORKFLOW_ID = 4312
APP_ID = 77
DRAFT_ID = 88
BRANCH = 'main'
STARTED = '2026-11-10T01:00:00Z'
FINISHED = '2026-11-10T02:00:00Z'
CONTEXT = dict(repository=promote.REPO, repository_id=promote.REPO_ID, run_id=RUN, run_attempt=1)

# Keep the fake API inventory independent of the publisher's allowlist.
FIXTURE_CHECKOUT = 'Run actions/checkout@11d5960a326750d5838078e36cf38b85af677262'
FIXTURE_PYTHON = 'Run actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065'
FIXTURE_DOWNLOAD = 'Run actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093'
FIXTURE_PURE = (FIXTURE_CHECKOUT, FIXTURE_PYTHON, 'Compile and test offline fake contracts only')
FIXTURE_PRODUCER = (FIXTURE_CHECKOUT, FIXTURE_PYTHON, 'Verify exact source and create private candidate workspace',
    'Acquire pinned official materials (not native qualification)',
    'Build four candidate packs and aggregate without executing cores',
    'Preserve candidate files and bounded acquisition/production evidence',
    'Bind small evidence to the completed immutable candidate artifact',
    'Preserve small candidate evidence separately from product ZIPs')
FIXTURE_NATIVE = (FIXTURE_CHECKOUT, FIXTURE_DOWNLOAD, 'Check same-run native binaries and TCP forwarding',
                  'Preserve native TCP evidence')
FIXTURE_JOBS = {
    'pure-contracts (3.9)': FIXTURE_PURE,
    'pure-contracts (3.12)': FIXTURE_PURE,
    'candidate (sing-box)': FIXTURE_PRODUCER,
    'candidate (xray)': FIXTURE_PRODUCER,
    'native-tcp (x86_64-gnu, ubuntu-24.04)': FIXTURE_NATIVE,
    'native-tcp (x86_64-musl, ubuntu-24.04)': FIXTURE_NATIVE,
    'native-tcp (aarch64-gnu, ubuntu-24.04-arm)': FIXTURE_NATIVE,
    'native-tcp (aarch64-musl, ubuntu-24.04-arm)': FIXTURE_NATIVE,
    'protocol-compatibility': (FIXTURE_CHECKOUT, FIXTURE_PYTHON, 'Install non-browser test dependencies',
        FIXTURE_DOWNLOAD, 'Fetch the original fixed clients for compatibility tests',
        'Run existing protocol gates with the verified candidate server binaries',
        'Preserve exact candidate protocol evidence'),
    'managed-arm64': (FIXTURE_CHECKOUT, FIXTURE_PYTHON, 'Run pip install -r requirements-test.txt',
        FIXTURE_DOWNLOAD, 'Build exact ARM64 GNU bundle and stage same-run core fixture',
        'Run installed ARM64 systemd core switch and restore', 'Preserve ARM64 systemd evidence'),
}


def zip_files(files):
    return zipped([(name, raw, stat.S_IFREG | 0o644) for name, raw in files.items()])


def reference(tag, commit):
    return dict(ref='refs/tags/' + tag, url=promote.API + '/git/refs/tags/' + tag,
                object=dict(type='commit', sha=commit, url=promote.API + '/git/commits/' + commit))


def row_identity():
    return dict(id=promote.REPO_ID, full_name=promote.REPO)


def fake_producer(root, core, version):
    root.mkdir(mode=0o700)
    fixture = Fixture(root, core, version)
    meta = fixture.meta
    meta['compatibility']['app_sources'] = [COMMIT]
    fixture.save()
    rows = [fixture.build(target) for target in promote.TARGETS]
    packs = {row['target']: root / ('out-' + row['target']) / row['name'] for row in rows}
    aggregate = builder.assemble(root, fixture.metadata, packs=packs, destination=Path('candidate'))
    upstream = meta['upstream']
    repo = upstream['repository']
    pinned_assets = []
    for item in upstream['archives']:
        name = item['archive_name']
        if name in {asset['name'] for asset in pinned_assets}:
            continue
        pinned_assets.append(dict(id=len(pinned_assets) + 10, name=name, size=item['archive_size'],
            digest='sha256:' + item['archive_sha256'], state='uploaded',
            browser_download_url='https://github.com/' + repo + '/releases/download/v' + version + '/' + name))
    pin = dict(core=core, repository=repo, tag='v' + version, source_commit=upstream['source_commit'],
               release_id=45, draft=False, prerelease=False, assets=pinned_assets)
    snapshot = dict(repository=dict(id=15, full_name=repo, html_url='https://github.com/' + repo),
        release_id=45, tag='v' + version, draft=False, prerelease=False,
        assets=sorted(pinned_assets, key=lambda asset: asset['name']),
        tag_chain=[dict(type='commit', sha=upstream['source_commit'])])
    acquisition = dict(schema=1, kind='core-candidate-acquisition-v1', scope='candidate-inputs-only',
        published=False, qualified=False, builder_source=COMMIT, app_sources=[COMMIT], upstream=snapshot,
        upstream_identity_sha256=promote.sha(builder._canonical(snapshot)),
        archives=[dict(name=asset['name'], asset_id=asset['id'], size=asset['size'], sha256=asset['digest'][7:],
                       digest_basis='official-release-api-digest-and-reviewed-pin') for asset in pinned_assets],
        source=dict(url='https://codeload.github.com/' + repo + '/tar.gz/' + upstream['source_commit'],
                    commit=upstream['source_commit'], size=upstream['source_size'], sha256=upstream['source_sha256'],
                    digest_basis='observed-fixed-commit-verified-https-response', independently_published_digest=False))
    expected_raw = builder._canonical(meta)
    acquisition_raw = builder._canonical(acquisition)
    production = dict(schema=1, kind='core-candidate-production-v1', scope='candidate-format-only',
        published=False, qualified=False, builder_source=COMMIT, core=core, context=CONTEXT,
        expected_sha256=promote.sha(expected_raw), acquisition_receipt_sha256=promote.sha(acquisition_raw),
        targets=rows, aggregate=aggregate)
    production_raw = json.dumps(production, sort_keys=True, indent=2).encode('ascii')
    summary = dict(scope='candidate-inputs-only', published=False, qualified=False, core=core, version=version,
        source_sha256=upstream['source_sha256'], expected_sha256=promote.sha(expected_raw),
        receipt_sha256=promote.sha(acquisition_raw))
    assets = {path.name: path.read_bytes() for path in (root/'candidate').iterdir()}
    files = {'candidate/' + name: raw for name, raw in assets.items()}
    files.update({'inputs/expected.json': expected_raw, 'inputs/acquisition-receipt.json': acquisition_raw,
                  'production-receipt.json': production_raw, 'acquisition-summary.json': builder._canonical(summary)})
    return pin, dict(expected=meta, acquisition=acquisition, production=production), files, assets


class FakeAPI:
    def __init__(self, producers):
        self.run = dict(id=RUN, workflow_id=WORKFLOW_ID, run_attempt=1, head_sha=COMMIT, head_branch=BRANCH,
            event='workflow_dispatch', status='completed', conclusion='success', path=promote.WORKFLOW_PATH,
            run_started_at=STARTED, updated_at=FINISHED, repository=row_identity(), head_repository=row_identity())
        self.latest_run = None
        self.latest = dict(id=APP_ID, tag_name=APP_TAG, draft=False, prerelease=False, published_at=STARTED,
            url=promote.API + '/releases/' + str(APP_ID), html_url=promote.WEB + '/releases/tag/' + APP_TAG)
        self.tags = {APP_TAG: reference(APP_TAG, COMMIT)}
        self.head = COMMIT
        self.draft = None
        self.existing = []
        self.create_tag_with_draft = False
        self.bad_upload_url = False
        self.fail_write = None
        self.after_upload = None
        self.bad_draft_readback = False
        self.bad_final_draft_listing = False
        self.bad_public_readback = False
        self.keep_latest = True
        self.writes = []
        self.downloaded = []
        self.jobs = [dict(id=90 + index, name=name, run_id=RUN, head_sha=COMMIT, status='completed',
                          conclusion='success', steps=[dict(name=step, number=step_index + 1,
                              status='completed', conclusion='success') for step_index, step in enumerate(steps)])
                     for index, (name, steps) in enumerate(FIXTURE_JOBS.items())]
        self.names = promote.evidence_names(self.run)
        self.artifacts, self.archives, self.files, self.records = {}, {}, {}, {}
        for core, (_, records, files, _) in producers.items():
            self.set_artifact(core, files)
            metadata = self.artifacts[core]
            value = dict(schema=1, kind='core-candidate-evidence-v1', qualified=False, published=False, core=core,
                builder_source=COMMIT, context=CONTEXT,
                producer_artifact={key: metadata[key] for key in ('id', 'name', 'digest')}, **records)
            self.set_record(core + '-evidence', 'candidate-evidence.json', value)
        for target in promote.TARGETS:
            data = {}
            for core, (_, records, _, _) in producers.items():
                row = next(item for item in records['production']['targets'] if item['target'] == target)
                data[core] = dict(version=records['expected']['version'], binary_sha256=row['binary_sha256'],
                                  pack_sha256=row['sha256'])
            self.set_record(target, 'native-tcp.json', dict(schema=1, kind='core-candidate-native-tcp-v1',
                                                           source=COMMIT, target=target, context=CONTEXT, cores=data))
        client_pins = {'sing-box': promote.fetch_test_cores.PINS[promote.protocols.TARGET]['sing-box'],
                       'mihomo': promote.fetch_mihomo.PIN}
        cores = {}
        for name, (_, records, files, _) in producers.items():
            prod, expected = records['production'], records['expected']
            row = next(item for item in prod['targets'] if item['target'] == promote.protocols.TARGET)
            cores[name] = dict(version=expected['version'], tag=expected['tag'], binary_sha256=row['binary_sha256'],
                binary_size=row['binary_size'], pack_sha256=row['sha256'], pack_manifest_sha256=row['manifest_sha256'],
                release_manifest_sha256=prod['aggregate']['manifest_sha256'], checksums_sha256=prod['aggregate']['checksums_sha256'],
                production_receipt_sha256=promote.sha(files['production-receipt.json']),
                acquisition_receipt_sha256=promote.sha(files['inputs/acquisition-receipt.json']), artifact=self.names[name])
        protocol = dict(schema=1, kind='core-candidate-protocols-v1', scope='ci-protocol-evidence',
            published=False, qualified=False, source=COMMIT, target=promote.protocols.TARGET, context=CONTEXT, cores=cores,
            clients={name: dict(version=pin['version'], archive_sha256=pin['sha256'], binary_sha256='c' * 64)
                     for name, pin in client_pins.items()},
            suites={name: dict(count=sum(classes.values()), ids_sha256=digest)
                    for name, (classes, digest) in promote.protocols.SUITES.items()})
        self.set_record('protocols', 'candidate-protocols.json', protocol)
        evidence = promote.acceptance_job
        self.arm_log = b'\n'.join([
            evidence.CORE_SWITCH_MARKER + b' sing-box fixture',
            evidence.CORE_SWITCH_MARKER + b' xray fixture',
            evidence.CORE_INTERRUPT_MARKER + b' fixture',
            evidence.CORE_RESTORE_MARKER + b' fixture',
            *(marker + b' fixture' for marker, _ in evidence.CORE_REMOTE_MARKERS),
            b'Ran 1 test in 12.34s', b'', b'OK', b''])
        self.set_artifact('arm', {'managed-arm64.log': self.arm_log})

    def set_artifact(self, role, files):
        identity = self.artifacts.get(role, {}).get('id', 301 + len(self.artifacts))
        raw = zip_files(files)
        self.files[role] = files.copy()
        self.archives[role] = raw
        self.artifacts[role] = dict(id=identity, name=self.names[role], expired=False,
            size_in_bytes=len(raw), digest='sha256:' + promote.sha(raw), created_at=FINISHED,
            workflow_run=dict(id=RUN, repository_id=promote.REPO_ID, head_repository_id=promote.REPO_ID,
                              head_sha=COMMIT, run_attempt=1))

    def set_record(self, role, name, value):
        self.records[role] = name, deepcopy(value)
        self.set_artifact(role, {name: builder._canonical(value)})

    def change_record(self, role, change):
        name, value = self.records[role]
        change(value)
        self.set_record(role, name, value)

    def download(self, artifact, *, max_bytes):
        role, = [role for role, item in self.artifacts.items() if item['id'] == artifact['id']]
        self.downloaded.append(role)
        return self.archives[role]

    def get(self, path):
        if path == '':
            return dict(**row_identity(), private=False, default_branch=BRANCH)
        if path == 'git/ref/heads/main':
            return dict(ref='refs/heads/main', object=dict(type='commit', sha=self.head))
        if path == 'releases/latest':
            return deepcopy(self.latest)
        if path.startswith('git/ref/tags/'):
            tag = path[len('git/ref/tags/'):]
            if tag not in self.tags:
                raise HTTPError('https://api.github.com/', 404, 'missing', {}, BytesIO())
            return deepcopy(self.tags[tag])
        if path == 'actions/workflows/' + promote.WORKFLOW:
            return dict(id=WORKFLOW_ID, path=promote.WORKFLOW_PATH, state='active')
        if path.startswith('actions/workflows/' + promote.WORKFLOW + '/runs?'):
            query = parse_qs(urlsplit(path).query)
            assert query == dict(branch=[BRANCH], event=['workflow_dispatch'], head_sha=[COMMIT], per_page=['1'])
            return {'workflow_runs': [deepcopy(self.latest_run or self.run)]}
        if path == f'actions/runs/{RUN}':
            return deepcopy(self.run)
        if path.startswith(f"actions/runs/{RUN}/attempts/{self.run['run_attempt']}/jobs?"):
            return {'jobs': deepcopy(self.jobs)}
        if path.startswith(f'actions/runs/{RUN}/artifacts?'):
            return {'artifacts': deepcopy(list(self.artifacts.values()))}
        if path == 'releases?per_page=100&page=1':
            rows = deepcopy([self.latest] + self.existing + ([self.draft] if self.draft is not None else []))
            if self.bad_final_draft_listing and self.draft is not None:
                rows[-1]['assets'][0]['digest'] = 'sha256:' + 'f' * 64
            return rows
        if path == f'releases/{DRAFT_ID}':
            return deepcopy(self.draft)
        if path == f'releases/{DRAFT_ID}/assets?per_page=100&page=1':
            rows = deepcopy(self.draft['assets'])
            if self.bad_draft_readback and self.draft['draft'] or self.bad_public_readback and not self.draft['draft']:
                rows[0]['digest'] = 'sha256:' + 'f' * 64
            return rows
        raise AssertionError('Unexpected read: ' + path)

    def __call__(self, path, method='GET', data=None, binary=False):
        if method == 'GET':
            return self.get(path)
        self.writes.append((method, path, data, binary))
        if self.fail_write == len(self.writes):
            raise TimeoutError('inert unknown write')
        if path == 'releases' and method == 'POST':
            assert data['draft'] is True and not binary and self.draft is None
            tag = data['tag_name']
            self.draft = dict(id=DRAFT_ID, tag_name=tag, target_commitish=data['target_commitish'], draft=True,
                prerelease=False, assets=[], url=promote.API + '/releases/' + str(DRAFT_ID),
                upload_url=f'https://uploads.github.com/repos/{promote.REPO}/releases/{DRAFT_ID}/assets{{?name,label}}')
            if self.bad_upload_url:
                self.draft['upload_url'] = f'https://uploads.github.com/repos/{promote.REPO}/releases/999/assets{{?name,label}}'
            if self.create_tag_with_draft:
                self.tags[tag] = reference(tag, COMMIT)
            return deepcopy(self.draft)
        if path.startswith('https://uploads.github.com/') and method == 'POST':
            assert binary and self.draft['draft']
            url = urlsplit(path)
            assert url.path == f'/repos/{promote.REPO}/releases/{DRAFT_ID}/assets'
            name, = parse_qs(url.query)['name']
            identity = 100 + len(self.draft['assets'])
            tag = self.draft['tag_name']
            row = dict(id=identity, name=name, size=len(data), state='uploaded', digest='sha256:' + promote.sha(data),
                url=promote.API + '/releases/assets/' + str(identity),
                browser_download_url=promote.WEB + '/releases/download/' + tag + '/' + name)
            self.draft['assets'].append(row)
            if len(self.draft['assets']) == 6 and self.after_upload:
                self.after_upload()
            return deepcopy(row)
        if path == f'releases/{DRAFT_ID}' and method == 'PATCH':
            assert data == {'draft':False, 'make_latest':'false'} and not binary
            tag = self.draft['tag_name']
            self.draft.update(draft=False, published_at=FINISHED, html_url=promote.WEB + '/releases/tag/' + tag)
            self.tags.setdefault(tag, reference(tag, COMMIT))
            if not self.keep_latest:
                self.latest = deepcopy(self.draft)
            return deepcopy(self.draft)
        raise AssertionError('Unexpected write: ' + path)


class FakePublicClient:
    def __init__(self, api):
        self.api = api
        self.hidden = False
        self.changed_file = None
        self.changed_asset = False
        self.latest_drift = False
        self.reads = []

    def inspect(self, release_id, *, app_source, deadline):
        self.reads.append(('inspect', release_id, app_source))
        if self.hidden or self.api.draft['draft']:
            raise promote.github.CoreGitHubError('HTTP_404')
        public = self.api.draft
        authority = promote.artifacts.decode_authority(builder._canonical(dict(
            schema=1, repository=promote.REPO, repository_id=promote.REPO_ID, release_id=release_id,
            tag=public['tag_name'], draft=False, prerelease=False, published_at=public['published_at'],
            assets=[{key: row[key] for key in ('id','name','size','digest')} for row in public['assets']])))
        assets = {parse_qs(urlsplit(path).query)['name'][0]: raw
                  for _, path, raw, binary in self.api.writes if binary}
        manifest, table = assets['CORE-RELEASE.json'], assets['SHA256SUMS']
        verified = promote.artifacts.verify_release(manifest, table, authority=authority,
            expected_tag=public['tag_name'], expected_builder_source=app_source, app_source=app_source)
        if self.changed_asset:
            authority = replace(authority, assets=(replace(authority.assets[0], id=999), *authority.assets[1:]))
        if self.changed_file == 'CORE-RELEASE.json':
            manifest += b'changed public manifest'
        if self.changed_file == 'SHA256SUMS':
            table += b'changed public table'
        return SimpleNamespace(authority=authority, verified_release=verified, tag_source=app_source,
                               manifest=manifest, checksums=table)

    def _get(self, suffix, *, deadline, stop):
        assert suffix.startswith('/')
        self.reads.append(('get', suffix))
        value = self.api.get(suffix[1:])
        if self.latest_drift and suffix == '/releases/latest':
            value['id'] = 999
        return value, builder._canonical(value), {}


class CoreComponentPublisherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='vui-component-publisher-fakes-')
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name).resolve()
        (cls.root / 'VERSION').write_text(VERSION + '\n')
        cls.producers = {name: fake_producer(cls.root/name, name, version)
                         for name, version in (('sing-box', '1.14.3'), ('xray', '26.10.21'))}

    def setUp(self):
        self.api = FakeAPI(self.producers)
        self.args = SimpleNamespace(core='sing-box', version=VERSION, commit=COMMIT, candidate_run=RUN, publish=True,
                                    confirm='RELEASE CORE core-sing-box-v1.14.3-r1 ' + COMMIT)
        self.env = dict(GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT='github-hosted',
            GITHUB_REPOSITORY=promote.REPO, GITHUB_REPOSITORY_ID=str(promote.REPO_ID),
            GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_SHA=COMMIT, GITHUB_REF='refs/heads/main',
            GITHUB_WORKFLOW_REF=promote.REPO + '/.github/workflows/publish.yml@refs/heads/main')
        head = patch.object(promote.subprocess, 'check_output', return_value=COMMIT + '\n')
        self.git_head = head.start()
        self.addCleanup(head.stop)
        clean = patch.object(promote.subprocess, 'run', return_value=SimpleNamespace(returncode=0))
        self.git_clean = clean.start()
        self.addCleanup(clean.stop)
        pins = patch.object(promote.fetch_core_candidate_inputs, 'CANDIDATES',
                            {name: value[0] for name, value in self.producers.items()})
        pins.start()
        self.addCleanup(pins.stop)

    def run_gate(self, client=None):
        self.public_client = FakePublicClient(self.api) if client is None else client
        return promote.promote(self.args, api=self.api, download=self.api.download, root=self.root, env=self.env,
                               public_client=self.public_client)

    def refused_before_write(self):
        with self.assertRaises((ValueError, promote.release.ReleaseGateError)):
            self.run_gate()
        self.assertEqual(self.api.writes, [])
        self.assertIsNone(self.api.draft)

    def test_success_promotes_only_selected_six_original_bytes_and_keeps_app_latest(self):
        self.args.core = 'xray'
        self.args.confirm = 'RELEASE CORE core-xray-v26.10.21-r1 ' + COMMIT
        self.api.create_tag_with_draft = True
        result = self.run_gate()
        self.assertEqual(result['tag'], 'core-xray-v26.10.21-r1')
        self.assertEqual(result['commit'], COMMIT)
        self.assertEqual(self.api.latest['tag_name'], APP_TAG)
        self.assertEqual(len(self.api.writes), 8)
        self.assertEqual(self.api.writes[0][0:2], ('POST', 'releases'))
        self.assertEqual(self.api.writes[-1][0:3],
                         ('PATCH', f'releases/{DRAFT_ID}', {'draft':False,'make_latest':'false'}))
        uploaded = {parse_qs(urlsplit(path).query)['name'][0]: raw
                    for _, path, raw, binary in self.api.writes[1:-1] if binary}
        self.assertEqual(uploaded, self.producers['xray'][3])
        self.assertEqual(self.api.downloaded, ['sing-box-evidence','xray-evidence',*promote.TARGETS,'protocols','arm','xray'])
        self.assertFalse(self.api.draft['draft'])
        self.assertTrue(all(not row['production']['qualified'] for _, row, _, _ in self.producers.values()))
        self.assertEqual(self.public_client.reads, [('inspect',DRAFT_ID,COMMIT),
            ('get','/git/ref/tags/core-xray-v26.10.21-r1'), ('get','/releases/latest'), ('get','/git/ref/tags/'+APP_TAG)])

    def test_publish_flag_confirmation_and_official_manual_scope_are_required(self):
        for change in ({'publish':False}, {'confirm':'RELEASE OTHER'}, {'candidate_run':True}):
            with self.subTest(change=change):
                previous = vars(self.args).copy()
                vars(self.args).update(change)
                self.refused_before_write()
                vars(self.args).update(previous)
        for key in ('GITHUB_ACTIONS','RUNNER_ENVIRONMENT','GITHUB_REPOSITORY','GITHUB_REPOSITORY_ID',
                    'GITHUB_EVENT_NAME','GITHUB_SHA','GITHUB_REF','GITHUB_WORKFLOW_REF'):
            with self.subTest(key=key):
                saved = self.env[key]
                self.env[key] = 'foreign'
                self.refused_before_write()
                self.env[key] = saved

    def test_dirty_checkout_default_head_or_latest_application_mismatch_never_writes(self):
        self.git_clean.return_value.returncode = 1
        self.refused_before_write()
        self.git_clean.return_value.returncode = 0
        self.api.head = 'f' * 40
        self.refused_before_write()
        self.api.head = COMMIT
        self.api.tags[APP_TAG] = reference(APP_TAG, 'f' * 40)
        self.refused_before_write()

    def test_old_or_non_dispatch_run_and_wrong_attempt_fail_closed(self):
        self.api.latest_run = dict(self.api.run, id=RUN + 1)
        self.refused_before_write()
        self.api.latest_run = None
        self.api.run['event'] = 'pull_request'
        self.refused_before_write()
        self.api.run['event'] = 'workflow_dispatch'
        self.api.run['run_attempt'] = 2
        self.refused_before_write()

    def test_every_job_requires_unique_successful_steps_and_exact_source(self):
        item = self.api.jobs[0]
        for change in ({'head_sha':'f'*40}, {'conclusion':'skipped'}, {'run_attempt':2},
                       {'steps':item['steps'][:-1]}, {'steps':item['steps']+[item['steps'][0]]}):
            with self.subTest(change=change):
                saved = item.copy()
                item.update(change)
                self.refused_before_write()
                item.clear()
                item.update(saved)
        item['steps'][0]['conclusion'] = 'skipped'
        self.refused_before_write()
        item['steps'][0]['conclusion'] = 'success'
        native = next(job for job in self.api.jobs if job['name'] == 'native-tcp (aarch64-musl, ubuntu-24.04-arm)')
        native['name'] = 'native-tcp (aarch64-musl)'
        self.refused_before_write()

    def test_existing_tag_or_draft_is_refused_without_downloading(self):
        tag = 'core-sing-box-v1.14.3-r1'
        self.api.tags[tag] = reference(tag, COMMIT)
        self.refused_before_write()
        self.api.tags.pop(tag)
        self.api.existing.append(dict(id=90, tag_name=tag, draft=True))
        self.refused_before_write()
        self.assertEqual(self.api.downloaded, [])

    def test_producer_artifact_identity_digest_and_context_cannot_be_substituted(self):
        for mutation in (
                lambda item: item['workflow_run'].update(run_attempt=2),
                lambda item: item['workflow_run'].update(head_repository_id=999),
                lambda item: item.update(digest='sha256:'+'d'*64)):
            with self.subTest(mutation=mutation):
                item = self.api.artifacts['sing-box-evidence']
                old = deepcopy(item)
                mutation(item)
                self.refused_before_write()
                item.clear()
                item.update(old)
        self.api.change_record('xray-evidence', lambda row: row['producer_artifact'].update(id=999))
        self.refused_before_write()
        self.assertNotIn('sing-box', self.api.downloaded)

    def test_native_protocol_or_arm_receipt_drift_refuses_before_selected_zip(self):
        cases = (
            ('x86_64-gnu', lambda row: row['cores']['xray'].update(binary_sha256='f'*64)),
            ('protocols', lambda row: row['cores']['sing-box'].update(pack_manifest_sha256='f'*64)),
            ('protocols', lambda row: row['cores']['xray'].update(production_receipt_sha256='f'*64)),
            ('protocols', lambda row: row['clients']['mihomo'].update(archive_sha256='f'*64)),
            ('protocols', lambda row: row['suites'].pop('test_real_cores')),
            ('protocols', lambda row: row.update(qualified=True)),
        )
        for role, change in cases:
            with self.subTest(role=role, change=change):
                self.api = FakeAPI(self.producers)
                self.api.change_record(role, change)
                self.refused_before_write()
                self.assertNotIn('sing-box', self.api.downloaded)
        self.api = FakeAPI(self.producers)
        marker = promote.acceptance_job.CORE_REMOTE_MARKERS[-1][0]
        self.api.set_artifact('arm', {'managed-arm64.log':self.api.arm_log.replace(marker, b'missing')})
        self.refused_before_write()
        self.assertNotIn('sing-box', self.api.downloaded)

    def test_selected_archive_bytes_must_match_original_receipts_and_targets(self):
        for path in ('production-receipt.json', 'candidate/SHA256SUMS',
                     next(name for name in self.api.files['sing-box'] if name.startswith('candidate/vui-core-'))):
            with self.subTest(path=path):
                self.api = FakeAPI(self.producers)
                files = self.api.files['sing-box'].copy()
                files[path] += b'\ncorruption'
                self.api.set_artifact('sing-box', files)
                self.api.change_record('sing-box-evidence',
                    lambda row: row['producer_artifact'].update(digest=self.api.artifacts['sing-box']['digest']))
                self.refused_before_write()
                self.assertIn('sing-box', self.api.downloaded)
                self.assertNotIn('xray', self.api.downloaded)

    def test_bad_upload_endpoint_and_unknown_upload_leave_draft_without_retry(self):
        self.api.bad_upload_url = True
        with self.assertRaisesRegex(ValueError, 'COMPONENT_UPLOAD_URL_INVALID'):
            self.run_gate()
        self.assertTrue(self.api.draft['draft'])
        self.assertEqual(len(self.api.writes), 1)
        self.api = FakeAPI(self.producers)
        self.api.fail_write = 3
        with self.assertRaisesRegex(ValueError, 'ASSET_UPLOAD_OUTCOME_UNCONFIRMED'):
            self.run_gate()
        self.assertTrue(self.api.draft['draft'])
        self.assertEqual(len(self.api.draft['assets']), 1)
        self.assertEqual(len(self.api.writes), 3)
        self.assertTrue(all(method == 'POST' for method, *_ in self.api.writes))

    def test_readback_run_head_latest_or_tag_drift_retains_draft(self):
        def changed_attempt(api):
            api.run.update(run_attempt=2, updated_at='2026-11-10T02:01:00Z')
        for mutate in (changed_attempt, lambda api: setattr(api, 'head', 'f'*40),
                lambda api: api.latest.update(id=90), lambda api: api.tags.update({
                    'core-sing-box-v1.14.3-r1': reference('core-sing-box-v1.14.3-r1', COMMIT)})):
            with self.subTest(mutate=mutate):
                self.api = FakeAPI(self.producers)
                self.api.after_upload = lambda: mutate(self.api)
                with self.assertRaises(ValueError):
                    self.run_gate()
                self.assertTrue(self.api.draft['draft'])
                self.assertEqual(len(self.api.writes), 7)
        for attr in ('bad_draft_readback', 'bad_final_draft_listing'):
            with self.subTest(attr=attr):
                self.api = FakeAPI(self.producers)
                setattr(self.api, attr, True)
                with self.assertRaisesRegex(ValueError, 'COMPONENT_ASSET_IDENTITY_DIFFERS'):
                    self.run_gate()
                self.assertTrue(self.api.draft['draft'])
                self.assertEqual(len(self.api.writes), 7)

    def test_unknown_creation_or_promotion_is_never_retried_or_deleted(self):
        for attempt, code in ((1,'DRAFT_CREATE'), (8,'COMPONENT_PROMOTION')):
            with self.subTest(attempt=attempt):
                self.api = FakeAPI(self.producers)
                self.api.fail_write = attempt
                with self.assertRaisesRegex(ValueError, code + '_OUTCOME_UNCONFIRMED'):
                    self.run_gate()
                self.assertEqual(len(self.api.writes), attempt)
                self.assertFalse(any(method == 'DELETE' for method, *_ in self.api.writes))

    def test_public_readback_detects_asset_or_latest_change_without_further_writes(self):
        for attr in ('bad_public_readback', 'keep_latest'):
            with self.subTest(attr=attr):
                self.api = FakeAPI(self.producers)
                setattr(self.api, attr, attr == 'bad_public_readback')
                with self.assertRaises(ValueError):
                    self.run_gate()
                self.assertFalse(self.api.draft['draft'])
                self.assertEqual(len(self.api.writes), 8)

    def test_authenticated_public_state_does_not_replace_anonymous_component_or_latest_readback(self):
        for failure in ({'hidden':True}, {'changed_asset':True}, {'changed_file':'CORE-RELEASE.json'},
                        {'changed_file':'SHA256SUMS'}, {'latest_drift':True}):
            with self.subTest(failure=failure):
                self.api = FakeAPI(self.producers)
                client = FakePublicClient(self.api)
                vars(client).update(failure)
                with self.assertRaisesRegex(ValueError, '^COMPONENT_PUBLIC_READBACK_UNCONFIRMED$'):
                    self.run_gate(client)
                self.assertEqual(client.reads[0], ('inspect',DRAFT_ID,COMMIT))
                self.assertFalse(self.api.draft['draft'])
                self.assertEqual(self.api.latest['id'], APP_ID)
                self.assertEqual(len(self.api.writes),8)
                self.assertEqual(sum(method=='PATCH' for method, *_ in self.api.writes),1)



if __name__ == '__main__':
    unittest.main()
