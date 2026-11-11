"""Promote one accepted component from the official manual release workflow."""

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode

from app.core_updates import artifacts, github
from scripts import acceptance_job, build_core_component as builder
from scripts import check_core_candidate_protocols as protocols
from scripts import fetch_core_candidate_inputs, fetch_mihomo, fetch_test_cores
from scripts import parallel_release_policy as parallel
from scripts import publish_release as release
from scripts import release_acceptance

ROOT = Path(__file__).resolve().parents[1]
REPO = artifacts.REPOSITORY
REPO_ID = artifacts.REPOSITORY_ID
API = 'https://api.github.com/repos/' + REPO
WEB = 'https://github.com/' + REPO
WORKFLOW = 'core-component-candidate.yml'
WORKFLOW_PATH = '.github/workflows/' + WORKFLOW
CORES = ('sing-box', 'xray')
TARGETS = artifacts.TARGETS
MAX_SMALL = 200_000
MAX_PRODUCER = 410_000_000

CHECKOUT = 'Run actions/checkout@11d5960a326750d5838078e36cf38b85af677262'
PYTHON = 'Run actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065'
DOWNLOAD = 'Run actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093'
JOB_STEPS = {
    **{f'pure-contracts ({version})': (CHECKOUT, PYTHON, 'Compile and test offline fake contracts only')
       for version in ('3.9', '3.12')},
    **{f'candidate ({core})': (
        CHECKOUT, PYTHON, 'Verify exact source and create private candidate workspace',
        'Acquire pinned official materials (not native qualification)',
        'Build four candidate packs and aggregate without executing cores',
        'Preserve candidate files and bounded acquisition/production evidence',
        'Bind small evidence to the completed immutable candidate artifact',
        'Preserve small candidate evidence separately from product ZIPs') for core in CORES},
    **{f'native-tcp ({target}, {runner})': (
        CHECKOUT, DOWNLOAD, 'Check same-run native binaries and TCP forwarding',
        'Preserve native TCP evidence')
       for target, runner in (('x86_64-gnu', 'ubuntu-24.04'), ('x86_64-musl', 'ubuntu-24.04'),
                              ('aarch64-gnu', 'ubuntu-24.04-arm'), ('aarch64-musl', 'ubuntu-24.04-arm'))},
    'protocol-compatibility': (
        CHECKOUT, PYTHON, 'Install non-browser test dependencies', DOWNLOAD,
        'Fetch the original fixed clients for compatibility tests',
        'Run existing protocol gates with the verified candidate server binaries',
        'Preserve exact candidate protocol evidence'),
    'managed-arm64': (
        CHECKOUT, PYTHON, 'Run pip install -r requirements-test.txt', DOWNLOAD,
        'Build exact ARM64 GNU bundle and stage same-run core fixture',
        'Run installed ARM64 systemd core switch and restore', 'Preserve ARM64 systemd evidence'),
}


class ComponentPublishError(ValueError):
    """A fixed diagnostic suitable for workflow logs."""


def need(condition, code):
    if not condition:
        raise ComponentPublishError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical_equal(actual, expected):
    return builder._canonical(actual) == builder._canonical(expected)


def fields(value, names, code):
    need(type(value) is dict and set(value) == set(names), code)


def repository(api):
    value = api('')
    need(type(value) is dict and artifacts._positive(value.get('id')) and value['id'] == REPO_ID
         and value.get('full_name') == REPO and value.get('private') is False, 'OFFICIAL_REPOSITORY_REQUIRED')
    branch = value.get('default_branch')
    need(type(branch) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,99}', branch),
         'DEFAULT_BRANCH_INVALID')
    return branch


def exact_head(api, branch, commit):
    need(repository(api) == branch, 'DEFAULT_BRANCH_CHANGED')
    ref = api('git/ref/heads/' + quote(branch, safe=''))
    need(type(ref) is dict and ref.get('ref') == 'refs/heads/' + branch
         and type(ref.get('object')) is dict and ref['object'].get('type') == 'commit'
         and ref['object'].get('sha') == commit, 'DEFAULT_HEAD_CHANGED')


def scope(args, api, root, env):
    need(args.publish is True and args.core in CORES and artifacts._hex(args.commit, 40)
         and artifacts._positive(args.candidate_run), 'EXPLICIT_COMPONENT_PUBLISH_REQUIRED')
    release_acceptance._version(args.version)
    tag = 'core-' + args.core + '-' + fetch_core_candidate_inputs.CANDIDATES[args.core]['tag'] + '-r1'
    need(args.confirm == 'RELEASE CORE ' + tag + ' ' + args.commit, 'COMPONENT_CONFIRMATION_MISMATCH')
    need(env.get('GITHUB_ACTIONS') == 'true' and env.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and env.get('GITHUB_REPOSITORY') == REPO and env.get('GITHUB_REPOSITORY_ID') == str(REPO_ID)
         and env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
         and env.get('GITHUB_SHA') == args.commit, 'TRUSTED_MANUAL_WORKFLOW_REQUIRED')
    branch = repository(api)
    ref = 'refs/heads/' + branch
    need(env.get('GITHUB_REF') == ref
         and env.get('GITHUB_WORKFLOW_REF') == REPO + '/.github/workflows/publish.yml@' + ref,
         'TRUSTED_DEFAULT_WORKFLOW_REQUIRED')
    local = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    clean = subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--'], cwd=root, check=False)
    need(local == args.commit and clean.returncode == 0
         and (root / 'VERSION').read_text(encoding='ascii').strip() == args.version,
         'EXACT_CLEAN_CHECKOUT_REQUIRED')
    exact_head(api, branch, args.commit)
    return branch, tag


def tag_chain(api, tag, *, missing=False):
    encoded = quote(tag, safe='')
    try:
        value = api('git/ref/tags/' + encoded)
    except HTTPError as error:
        error.close()
        if missing and error.code == 404:
            return None
        raise
    need(type(value) is dict and value.get('ref') == 'refs/tags/' + tag
         and value.get('url') == API + '/git/refs/tags/' + encoded, 'TAG_REF_INVALID')
    chain = []
    for _ in range(5):
        obj = value.get('object')
        need(type(obj) is dict and obj.get('type') in ('tag', 'commit')
             and artifacts._hex(obj.get('sha'), 40), 'TAG_OBJECT_INVALID')
        kind, commit = obj['type'], obj['sha']
        path = 'git/' + ('tags/' if kind == 'tag' else 'commits/') + commit
        need(obj.get('url') == API + '/' + path
             and all(previous != commit for _, previous in chain), 'TAG_OBJECT_INVALID')
        chain.append((kind, commit))
        if kind == 'commit':
            return tuple(chain)
        need(len(chain) < 5, 'TAG_DEPTH')
        value = api(path)
        need(type(value) is dict and value.get('sha') == commit and value.get('url') == API + '/' + path,
             'ANNOTATED_TAG_INVALID')
    raise ComponentPublishError('TAG_DEPTH')


def latest_application(api, version, commit):
    tag = 'v' + version
    value = api('releases/latest')
    need(type(value) is dict and artifacts._positive(value.get('id')) and value.get('tag_name') == tag
         and value.get('draft') is False and value.get('prerelease') is False
         and value.get('url') == API + '/releases/' + str(value['id'])
         and value.get('html_url') == WEB + '/releases/tag/' + tag
         and type(value.get('published_at')) is str, 'EXACT_APPLICATION_MUST_BE_LATEST')
    parallel._timestamp(value['published_at'])
    chain = tag_chain(api, tag)
    need(chain[-1] == ('commit', commit), 'LATEST_APPLICATION_TAG_DIFFERS')
    return value['id'], value['published_at'], chain


def candidate_run(api, run_id, branch, commit):
    workflow = api('actions/workflows/' + WORKFLOW)
    need(type(workflow) is dict and artifacts._positive(workflow.get('id'))
         and workflow.get('path') == WORKFLOW_PATH and workflow.get('state') == 'active',
         'COMPONENT_WORKFLOW_INVALID')
    query = urlencode(dict(branch=branch, event='workflow_dispatch', head_sha=commit, per_page=1))
    recent = api('actions/workflows/' + WORKFLOW + '/runs?' + query)
    need(type(recent) is dict and type(recent.get('workflow_runs')) is list
         and len(recent['workflow_runs']) == 1, 'LATEST_COMPONENT_RUN_REQUIRED')
    latest = recent['workflow_runs'][0]
    run = api(f'actions/runs/{run_id}')
    identity = ('id', 'workflow_id', 'run_attempt', 'head_sha', 'head_branch', 'event',
                'status', 'conclusion', 'path', 'run_started_at', 'updated_at')
    need(type(run) is dict and type(latest) is dict and all(run.get(k) == latest.get(k) for k in identity),
         'LATEST_COMPONENT_RUN_REQUIRED')
    need(artifacts._positive(run.get('id')) and run['id'] == run_id
         and artifacts._positive(run.get('workflow_id')) and run['workflow_id'] == workflow['id']
         and artifacts._positive(run.get('run_attempt'))
         and run.get('head_sha') == commit and run.get('head_branch') == branch
         and run.get('event') == 'workflow_dispatch' and run.get('status') == 'completed'
         and run.get('conclusion') == 'success' and type(run.get('path')) is str
         and run['path'].split('@', 1)[0] == WORKFLOW_PATH, 'COMPONENT_RUN_NOT_QUALIFIED')
    for row in (latest, run):
        for key in ('repository', 'head_repository'):
            repo = row.get(key)
            need(type(repo) is dict and artifacts._positive(repo.get('id')) and repo['id'] == REPO_ID
                 and repo.get('full_name') == REPO, 'COMPONENT_RUN_REPOSITORY_DIFFERS')
    need(parallel._timestamp(run.get('run_started_at')) <= parallel._timestamp(run.get('updated_at')),
         'COMPONENT_ATTEMPT_TIME_INVALID')
    return {key: run[key] for key in identity}


def check_jobs(api, run):
    rows = parallel._pages(api, f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", 'jobs')
    need(len(rows) == len(JOB_STEPS) and all(type(job) is dict and type(job.get('name')) is str for job in rows)
         and {job['name'] for job in rows} == set(JOB_STEPS), 'COMPONENT_JOB_SET_DIFFERS')
    seen = set()
    for job in rows:
        need(artifacts._positive(job.get('id')) and job['id'] not in seen
             and artifacts._positive(job.get('run_id')) and job['run_id'] == run['id']
             and job.get('head_sha') == run['head_sha'] and job.get('status') == 'completed'
             and job.get('conclusion') == 'success'
             and ('run_attempt' not in job or type(job['run_attempt']) is int
                  and job['run_attempt'] == run['run_attempt']), 'COMPONENT_JOB_NOT_QUALIFIED')
        seen.add(job['id'])
        steps = job.get('steps')
        need(type(steps) is list and steps, 'COMPONENT_STEPS_MISSING')
        names, numbers = [], set()
        for step in steps:
            need(type(step) is dict and type(step.get('name')) is str and step['name']
                 and step['name'] not in names and artifacts._positive(step.get('number'))
                 and step['number'] not in numbers and step.get('status') == 'completed'
                 and step.get('conclusion') == 'success', 'COMPONENT_STEP_NOT_QUALIFIED')
            names.append(step['name'])
            numbers.add(step['number'])
        required = JOB_STEPS[job['name']]
        need(set(required) <= set(names) and [name for name in names if name in required] == list(required),
             'COMPONENT_REQUIRED_STEPS_DIFFERS')


def evidence_names(run):
    suffix = f"{run['head_sha']}-{run['id']}-{run['run_attempt']}"
    return {**{core: f'core-candidate-{core}-{suffix}' for core in CORES},
            **{core + '-evidence': f'core-candidate-evidence-{core}-{suffix}' for core in CORES},
            **{target: f'core-native-tcp-{target}-{suffix}' for target in TARGETS},
            'protocols': f'core-candidate-protocols-x86_64-gnu-{suffix}',
            'arm': f'core-managed-arm64-{suffix}'}


def artifact_files(item, names, limit, download):
    raw = parallel._download(item, limit + 65_536, download)
    return release._archive_files(raw, set(names), limit)


def one_file(item, name, limit, download):
    return artifact_files(item, {name}, limit, download)[name]


def check_context(row, kind, context, source, source_field, code):
    need(type(row.get('schema')) is int and row['schema'] == 1 and row.get('kind') == kind
         and row.get(source_field) == source and canonical_equal(row.get('context'), context), code)


def unqualified(row, scope, code):
    need(row.get('scope') == scope and row.get('qualified') is False and row.get('published') is False, code)


@dataclass(frozen=True)
class ProducerEvidence:
    metadata: dict
    rows: dict
    aggregate: dict
    receipts: dict
    artifact: dict


PACK_FIELDS = {'target', 'name', 'size', 'sha256', 'manifest_sha256'}
CANDIDATE_FLAGS = {'scope', 'qualified', 'published'}


def check_acquisition(row, metadata, pin, source):
    fields(row, {'schema', 'kind', 'builder_source', 'app_sources', 'upstream', 'upstream_identity_sha256',
                 'archives', 'source'} | CANDIDATE_FLAGS, 'ACQUISITION_RECEIPT_INVALID')
    unqualified(row, 'candidate-inputs-only', 'ACQUISITION_RECEIPT_INVALID')
    need(type(row['schema']) is int and row['schema'] == 1 and row['kind'] == 'core-candidate-acquisition-v1'
         and row['builder_source'] == source and row['app_sources'] == [source], 'ACQUISITION_CONTEXT_DIFFERS')
    upstream = row['upstream']
    fields(upstream, {'repository', 'release_id', 'tag', 'draft', 'prerelease', 'assets', 'tag_chain'},
           'ACQUISITION_UPSTREAM_INVALID')
    repo = upstream['repository']
    need(type(repo) is dict and artifacts._positive(repo.get('id'))
         and repo.get('full_name') == pin['repository'] and repo.get('html_url') == 'https://github.com/' + pin['repository']
         and type(upstream['release_id']) is int and upstream['release_id'] == pin['release_id']
         and upstream['tag'] == pin['tag'] and upstream['draft'] is False and upstream['prerelease'] is False
         and canonical_equal(upstream['assets'], sorted(pin['assets'], key=lambda asset: asset['name']))
         and row['upstream_identity_sha256'] == sha(builder._canonical(upstream)), 'ACQUISITION_UPSTREAM_DIFFERS')
    chain = upstream['tag_chain']
    need(type(chain) is list and 1 <= len(chain) <= 5
         and all(type(item) is dict and set(item) == {'type', 'sha'} and artifacts._hex(item['sha'], 40) for item in chain)
         and len({item['sha'] for item in chain}) == len(chain)
         and all(item['type'] == 'tag' for item in chain[:-1])
         and chain[-1] == dict(type='commit', sha=pin['source_commit']), 'ACQUISITION_TAG_DIFFERS')
    expected = metadata['upstream']
    need(expected['source_commit'] == pin['source_commit']
         and canonical_equal(row['source'], dict(
             url='https://codeload.github.com/' + pin['repository'] + '/tar.gz/' + pin['source_commit'],
             commit=pin['source_commit'], size=expected['source_size'], sha256=expected['source_sha256'],
             digest_basis='observed-fixed-commit-verified-https-response', independently_published_digest=False)),
         'ACQUISITION_SOURCE_DIFFERS')
    pinned = {asset['name']: asset for asset in pin['assets']}
    need(all(item['archive_name'] in pinned and item['archive_size'] == pinned[item['archive_name']]['size']
             and 'sha256:' + item['archive_sha256'] == pinned[item['archive_name']]['digest']
             for item in expected['archives']), 'ACQUISITION_PACK_PIN_DIFFERS')
    need(canonical_equal(row['archives'], [
        dict(name=asset['name'], asset_id=asset['id'], size=asset['size'], sha256=asset['digest'][7:],
             digest_basis='official-release-api-digest-and-reviewed-pin') for asset in pin['assets']]),
         'ACQUISITION_ASSETS_DIFFER')


def producer_evidence(raw, core, source, context, artifact):
    value = release_acceptance._json(raw, MAX_SMALL)
    fields(value, {'schema', 'kind', 'core', 'builder_source', 'context', 'qualified', 'published',
                   'producer_artifact', 'expected', 'acquisition', 'production'}, 'PRODUCER_EVIDENCE_INVALID')
    check_context(value, 'core-candidate-evidence-v1', context, source, 'builder_source', 'PRODUCER_CONTEXT_DIFFERS')
    need(value['core'] == core and value['qualified'] is False and value['published'] is False
         and canonical_equal(value['producer_artifact'], {key: artifact[key] for key in ('id', 'name', 'digest')}),
         'PRODUCER_ARTIFACT_DIFFERS')
    expected_raw = builder._canonical(value['expected'])
    acquisition_raw = builder._canonical(value['acquisition'])
    # These are the original fixed producer serializations used in the workflow.
    production_raw = json.dumps(value['production'], sort_keys=True, indent=2).encode('ascii')
    need(all(0 < len(raw) <= artifacts.MAX_JSON for raw in (expected_raw, acquisition_raw, production_raw)),
         'PRODUCER_RECEIPT_LIMIT')
    metadata = builder._metadata(expected_raw)
    pin = fetch_core_candidate_inputs.CANDIDATES[core]
    need(metadata['core'] == core and metadata['builder_source'] == source
         and metadata['compatibility']['app_sources'] == [source]
         and metadata['tag'] == 'core-' + core + '-' + pin['tag'] + '-r1', 'PRODUCER_METADATA_DIFFERS')
    check_acquisition(value['acquisition'], metadata, pin, source)
    production = value['production']
    fields(production, {'schema', 'kind', 'builder_source', 'core', 'context', 'acquisition_receipt_sha256',
                        'expected_sha256', 'targets', 'aggregate'} | CANDIDATE_FLAGS, 'PRODUCTION_RECEIPT_INVALID')
    check_context(production, 'core-candidate-production-v1', context, source, 'builder_source', 'PRODUCTION_CONTEXT_DIFFERS')
    unqualified(production, 'candidate-format-only', 'PRODUCTION_RECEIPT_INVALID')
    need(production['core'] == core and production['expected_sha256'] == sha(expected_raw)
         and production['acquisition_receipt_sha256'] == sha(acquisition_raw), 'PRODUCTION_RECEIPT_HASH_DIFFERS')
    rows = {}
    need(type(production['targets']) is list and len(production['targets']) == 4, 'PRODUCTION_TARGET_SET_DIFFERS')
    for row in production['targets']:
        fields(row, PACK_FIELDS | CANDIDATE_FLAGS | {'binary_size', 'binary_sha256'}, 'PRODUCTION_TARGET_INVALID')
        unqualified(row, 'candidate-format-only', 'PRODUCTION_TARGET_INVALID')
        target = row['target']
        need(type(target) is str and target in TARGETS and target not in rows
             and row['name'] == artifacts._pack_name(core, metadata['version'], target)
             and artifacts._positive(row['size'], artifacts.MAX_PACK)
             and artifacts._positive(row['binary_size'], artifacts.MAX_EXPANDED)
             and all(artifacts._hex(row[key]) for key in ('sha256', 'manifest_sha256', 'binary_sha256')),
             'PRODUCTION_TARGET_INVALID')
        rows[target] = row
    aggregate = production['aggregate']
    fields(aggregate, {'manifest_sha256', 'checksums_sha256', 'packs'} | CANDIDATE_FLAGS, 'AGGREGATE_INVALID')
    unqualified(aggregate, 'candidate-format-only', 'AGGREGATE_INVALID')
    packs = [{key: rows[target][key] for key in PACK_FIELDS} for target in TARGETS]
    need(all(artifacts._hex(aggregate[key]) for key in ('manifest_sha256', 'checksums_sha256'))
         and canonical_equal(aggregate['packs'], packs), 'AGGREGATE_TARGETS_DIFFER')
    return ProducerEvidence(metadata, rows, aggregate,
        {'inputs/expected.json': expected_raw, 'inputs/acquisition-receipt.json': acquisition_raw,
         'production-receipt.json': production_raw}, artifact)


def protocol_core(evidence, target):
    row = evidence.rows[target]
    return dict(version=evidence.metadata['version'], tag=evidence.metadata['tag'],
                pack_manifest_sha256=row['manifest_sha256'], pack_sha256=row['sha256'],
                checksums_sha256=evidence.aggregate['checksums_sha256'],
                release_manifest_sha256=evidence.aggregate['manifest_sha256'],
                binary_sha256=row['binary_sha256'], binary_size=row['binary_size'],
                production_receipt_sha256=sha(evidence.receipts['production-receipt.json']),
                acquisition_receipt_sha256=sha(evidence.receipts['inputs/acquisition-receipt.json']),
                artifact=evidence.artifact['name'])


def check_native(raw, target, context, source, producers):
    row = artifacts._json(raw)
    fields(row, {'schema', 'kind', 'target', 'source', 'context', 'cores'}, 'NATIVE_RECEIPT_INVALID')
    check_context(row, 'core-candidate-native-tcp-v1', context, source, 'source', 'NATIVE_CONTEXT_DIFFERS')
    expected = {name: dict(version=evidence.metadata['version'], binary_sha256=evidence.rows[target]['binary_sha256'],
                           pack_sha256=evidence.rows[target]['sha256']) for name, evidence in producers.items()}
    need(row['target'] == target and canonical_equal(row['cores'], expected), 'NATIVE_PRODUCER_BYTES_DIFFER')


def check_protocols(raw, context, source, producers):
    row = artifacts._json(raw)
    fields(row, {'schema', 'kind', 'scope', 'qualified', 'published', 'target', 'source', 'context',
                 'cores', 'clients', 'suites'}, 'PROTOCOL_RECEIPT_INVALID')
    check_context(row, 'core-candidate-protocols-v1', context, source, 'source', 'PROTOCOL_CONTEXT_DIFFERS')
    unqualified(row, 'ci-protocol-evidence', 'PROTOCOL_RECEIPT_INVALID')
    need(row['target'] == protocols.TARGET and canonical_equal(
         row['cores'], {name: protocol_core(evidence, protocols.TARGET) for name, evidence in producers.items()}),
         'PROTOCOL_PRODUCER_BYTES_DIFFER')
    suites = {name: dict(count=sum(classes.values()), ids_sha256=digest)
              for name, (classes, digest) in protocols.SUITES.items()}
    need(len(suites) == 27 and sum(suite['count'] for suite in suites.values()) == 165
         and canonical_equal(row['suites'], suites), 'PROTOCOL_SUITE_EVIDENCE_DIFFERS')
    pins = {'sing-box': fetch_test_cores.PINS[protocols.TARGET]['sing-box'], 'mihomo': fetch_mihomo.PIN}
    fields(row['clients'], pins, 'PROTOCOL_CLIENTS_INVALID')
    for name, pin in pins.items():
        client = row['clients'][name]
        fields(client, {'version', 'archive_sha256', 'binary_sha256'}, 'PROTOCOL_CLIENT_INVALID')
        need(client['version'] == pin['version'] and client['archive_sha256'] == pin['sha256']
             and artifacts._hex(client['binary_sha256']), 'PROTOCOL_CLIENT_PIN_DIFFERS')


def selected_assets(evidence, download):
    expected_names = {'CORE-RELEASE.json', 'SHA256SUMS'} | {row['name'] for row in evidence.rows.values()}
    candidate = {'candidate/' + name for name in expected_names}
    files = artifact_files(evidence.artifact, candidate | set(evidence.receipts) | {'acquisition-summary.json'},
                           MAX_PRODUCER, download)
    need(all(files[name] == raw for name, raw in evidence.receipts.items()), 'SELECTED_PRODUCER_RECEIPT_DIFFERS')
    metadata = evidence.metadata
    summary = artifacts._json(files['acquisition-summary.json'])
    need(canonical_equal(summary, dict(scope='candidate-inputs-only', published=False, qualified=False,
         core=metadata['core'], version=metadata['version'], source_sha256=metadata['upstream']['source_sha256'],
         expected_sha256=sha(evidence.receipts['inputs/expected.json']),
         receipt_sha256=sha(evidence.receipts['inputs/acquisition-receipt.json']))), 'ACQUISITION_SUMMARY_DIFFERS')
    assets = {name: files['candidate/' + name] for name in expected_names}
    manifest, checksums = assets['CORE-RELEASE.json'], assets['SHA256SUMS']
    expected_manifest = builder._canonical(dict(builder._common(metadata), kind='core-release-v1',
                                                packs=evidence.aggregate['packs']))
    digests = {row['name']: row['sha256'] for row in evidence.rows.values()}
    digests['CORE-RELEASE.json'] = sha(manifest)
    expected_table = ''.join(digests[name] + '  ' + name + '\n' for name in sorted(digests)).encode('ascii')
    need(manifest == expected_manifest and checksums == expected_table
         and sha(manifest) == evidence.aggregate['manifest_sha256']
         and sha(checksums) == evidence.aggregate['checksums_sha256'], 'SELECTED_AGGREGATE_BYTES_DIFFER')
    for target, row in evidence.rows.items():
        raw = assets[row['name']]
        need(len(raw) == row['size'] and sha(raw) == row['sha256'], 'SELECTED_PACK_BYTES_DIFFER')
        with tempfile.TemporaryFile() as stream:
            stream.write(raw)
            stream.flush()
            pack, binary = builder._candidate(stream.fileno(), len(raw), metadata, target)
        need(canonical_equal(pack, {name: row[name] for name in PACK_FIELDS})
             and binary == (row['binary_sha256'], row['binary_size']), 'SELECTED_PACK_VALIDATION_DIFFERS')
    return assets


def accepted_assets(api, run, core, download):
    names = evidence_names(run)
    source = run['head_sha']
    context = dict(repository=REPO, repository_id=REPO_ID, run_id=run['id'], run_attempt=run['run_attempt'])
    items = parallel._artifacts(api, run, source, REPO_ID, set(names.values()))
    for item in items.values():
        origin = item['workflow_run']
        need('head_repository_id' not in origin or type(origin['head_repository_id']) is int
             and origin['head_repository_id'] == REPO_ID, 'ARTIFACT_HEAD_REPOSITORY_DIFFERS')
    producers = {}
    for name in CORES:
        raw = one_file(items[names[name + '-evidence']], 'candidate-evidence.json', MAX_SMALL, download)
        producers[name] = producer_evidence(raw, name, source, context, items[names[name]])
    for target in TARGETS:
        raw = one_file(items[names[target]], 'native-tcp.json', artifacts.MAX_JSON, download)
        check_native(raw, target, context, source, producers)
    raw = one_file(items[names['protocols']], 'candidate-protocols.json', artifacts.MAX_JSON, download)
    check_protocols(raw, context, source, producers)
    log = one_file(items[names['arm']], 'managed-arm64.log', acceptance_job.MAX_LOG, download)
    lines = log.splitlines()
    need(sum(re.fullmatch(rb'Ran 1 test in [0-9]+(?:\.[0-9]+)?s', line) is not None for line in lines) == 1
         and lines.count(b'OK') == 1, 'ARM_TEST_RESULT_MISSING')
    acceptance_job.validate_core_switch_evidence(log)
    return selected_assets(producers[core], download)


def releases_with_tag(api, tag):
    matches, seen = [], set()
    for page in range(1, parallel.MAX_PAGES + 1):
        rows = api(f'releases?per_page=100&page={page}')
        need(type(rows) is list and len(rows) <= 100, 'RELEASE_LIST_INVALID')
        for row in rows:
            need(type(row) is dict and artifacts._positive(row.get('id')) and row['id'] not in seen
                 and type(row.get('tag_name')) is str, 'RELEASE_LIST_IDENTITY_INVALID')
            seen.add(row['id'])
            if row['tag_name'] == tag:
                matches.append(row)
        if len(rows) < 100:
            return matches
    raise ComponentPublishError('RELEASE_LIST_LIMIT')


def release_identity(value, tag, commit, *, draft, release_id=None):
    need(type(value) is dict and artifacts._positive(value.get('id'))
         and (release_id is None or value['id'] == release_id)
         and value.get('tag_name') == tag and value.get('target_commitish') == commit
         and value.get('draft') is draft and value.get('prerelease') is False
         and value.get('url') == API + '/releases/' + str(value['id']), 'COMPONENT_RELEASE_IDENTITY_DIFFERS')
    if not draft:
        need(value.get('html_url') == WEB + '/releases/tag/' + tag, 'PUBLIC_RELEASE_URL_DIFFERS')
        parallel._timestamp(value.get('published_at'))
    return value['id']


def asset_identity(row, name, raw, *, public_tag=None):
    need(type(row) is dict and artifacts._positive(row.get('id')) and row.get('name') == name
         and type(row.get('size')) is int and row['size'] == len(raw)
         and row.get('digest') == 'sha256:' + sha(raw) and row.get('state') == 'uploaded',
         'COMPONENT_ASSET_IDENTITY_DIFFERS')
    if public_tag is not None:
        need(row.get('url') == API + '/releases/assets/' + str(row['id'])
             and row.get('browser_download_url') == WEB + '/releases/download/' + public_tag + '/' + name,
             'PUBLIC_ASSET_URL_DIFFERS')
    return row['id']


def check_release_assets(rows, assets, uploaded, *, public_tag=None):
    need(type(rows) is list and len(rows) == len(assets)
         and all(type(row) is dict and type(row.get('name')) is str for row in rows)
         and {row['name'] for row in rows} == set(assets), 'COMPONENT_RELEASE_ASSET_SET_DIFFERS')
    checked = {row['name']: asset_identity(row, row['name'], assets[row['name']], public_tag=public_tag)
               for row in rows}
    need(checked == uploaded, 'COMPONENT_RELEASE_ASSET_IDS_DIFFER')


def read_release(api, release_id, tag, commit, assets, uploaded, *, draft):
    value = api(f'releases/{release_id}')
    release_identity(value, tag, commit, draft=draft, release_id=release_id)
    listed = api(f'releases/{release_id}/assets?per_page=100&page=1')
    for rows in (value.get('assets'), listed):
        check_release_assets(rows, assets, uploaded, public_tag=None if draft else tag)
    return value


def verify_anonymous_public(client, release_id, tag, args, assets, uploaded, application, draft_tag):
    deadline = time.monotonic() + 120
    try:
        if client is None:
            client = github.GitHubCoreClient()
        candidate = client.inspect(release_id, app_source=args.commit, deadline=deadline)
        authority = candidate.authority
        expected = tuple(artifacts.Asset(uploaded[name], name, len(assets[name]), sha(assets[name]))
                         for name in sorted(assets))
        need(authority.release_id == release_id and authority.tag == tag and authority.assets == expected
             and candidate.tag_source == args.commit and candidate.verified_release.app_sources == (args.commit,)
             and candidate.manifest == assets['CORE-RELEASE.json'] and candidate.checksums == assets['SHA256SUMS'],
             'ANONYMOUS_COMPONENT_DIFFERS')

        def get(path):
            value, _, _ = client._get('/' + path, deadline=deadline, stop=None)
            return value

        if draft_tag is not None:
            need(tag_chain(get, tag) == draft_tag, 'ANONYMOUS_COMPONENT_TAG_CHANGED')
        need(latest_application(get, args.version, args.commit) == application, 'ANONYMOUS_LATEST_APPLICATION_CHANGED')
    except Exception:
        raise ComponentPublishError('COMPONENT_PUBLIC_READBACK_UNCONFIRMED') from None


def write_once(api, path, method, data, *, binary=False, stage):
    try:
        return api(path, method, data, binary)
    except Exception:
        raise ComponentPublishError(stage + '_OUTCOME_UNCONFIRMED') from None


def promote(args, *, api=release.request, download=release.download_artifact, root=ROOT, env=None, public_client=None):
    branch, tag = scope(args, api, root, os.environ if env is None else env)
    application = latest_application(api, args.version, args.commit)
    run = candidate_run(api, args.candidate_run, branch, args.commit)
    check_jobs(api, run)
    need(tag_chain(api, tag, missing=True) is None and not releases_with_tag(api, tag), 'COMPONENT_TAG_ALREADY_EXISTS')
    assets = accepted_assets(api, run, args.core, download)

    def recheck():
        need(candidate_run(api, args.candidate_run, branch, args.commit) == run, 'COMPONENT_ATTEMPT_CHANGED')
        exact_head(api, branch, args.commit)
        need(latest_application(api, args.version, args.commit) == application, 'LATEST_APPLICATION_CHANGED')

    recheck()
    need(tag_chain(api, tag, missing=True) is None and not releases_with_tag(api, tag), 'COMPONENT_TAG_ALREADY_EXISTS')
    value = write_once(api, 'releases', 'POST',
        dict(tag_name=tag, target_commitish=args.commit, name=tag, draft=True, prerelease=False,
             body=f'Compatible application source: {args.commit}\nAccepted candidate run: {run["id"]}, attempt {run["run_attempt"]}.\n'),
        stage='DRAFT_CREATE')
    release_id = release_identity(value, tag, args.commit, draft=True)
    upload = f'https://uploads.github.com/repos/{REPO}/releases/{release_id}/assets'
    need(value.get('upload_url') == upload + '{?name,label}', 'COMPONENT_UPLOAD_URL_INVALID')
    draft_tag = tag_chain(api, tag, missing=True)
    need(draft_tag is None or draft_tag[-1] == ('commit', args.commit), 'DRAFT_TAG_SOURCE_DIFFERS')
    uploaded = {}
    for name in sorted(assets):
        raw = assets[name]
        result = write_once(api, upload + '?name=' + quote(name, safe=''), 'POST', raw,
                            binary=True, stage='ASSET_UPLOAD')
        identity = asset_identity(result, name, raw)
        need(identity not in uploaded.values(), 'DUPLICATE_UPLOADED_ASSET_ID')
        uploaded[name] = identity
    read_release(api, release_id, tag, args.commit, assets, uploaded, draft=True)
    recheck()
    matches = releases_with_tag(api, tag)
    need(len(matches) == 1, 'COMPONENT_DRAFT_LIST_DIFFERS')
    release_identity(matches[0], tag, args.commit, draft=True, release_id=release_id)
    check_release_assets(matches[0].get('assets'), assets, uploaded)
    need(tag_chain(api, tag, missing=True) == draft_tag, 'DRAFT_TAG_CHANGED')
    value = write_once(api, f'releases/{release_id}', 'PATCH', {'draft': False, 'make_latest': 'false'},
                       stage='COMPONENT_PROMOTION')
    release_identity(value, tag, args.commit, draft=False, release_id=release_id)
    public = read_release(api, release_id, tag, args.commit, assets, uploaded, draft=False)
    verify_anonymous_public(public_client, release_id, tag, args, assets, uploaded, application, draft_tag)
    return dict(release=public['html_url'], tag=tag, commit=args.commit, run_id=run['id'], run_attempt=run['run_attempt'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', choices=CORES, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--candidate-run', type=int, required=True)
    parser.add_argument('--confirm', required=True)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = promote(args)
    except ComponentPublishError as error:
        raise SystemExit(str(error)) from None
    except Exception:
        raise SystemExit('COMPONENT_RELEASE_GATE_UNCONFIRMED') from None
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
