"""Bound parallel CI evidence without authorizing publication or rebuilding assets.

The future publication caller must separately validate source scope and the latest
known public baseline. CI-only qualification of the current 0.4.22 is supported;
receipts establish identity, while exact-attempt job and step outcomes establish
execution. Browser acceptance remains explicitly unverified.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import io
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import zipfile

from scripts import acceptance_job as roles
from scripts import release_acceptance as core

POLICY_PATH = '.github/release-acceptance.json'
POLICY_KEYS = frozenset({'schema', 'version', 'product_commit', 'install_contract', 'browser_policy'})
DOCUMENTS = frozenset({'README.md', 'CHANGELOG.md', 'ROADMAP.md', 'AGENTS.md', 'CONTRIBUTING.md',
                       'SECURITY.md', 'SUPPORT.md', 'CODE_OF_CONDUCT.md'})
WORKFLOWS = frozenset({'test.yml', 'docs.yml', 'toclash.yml', 'loopback.yml', 'release.yml',
                       'acme.yml', 'short-install.yml', 'portable.yml', 'oneclick.yml'})
TARGETS = ('aarch64-gnu', 'x86_64-musl', 'aarch64-musl')
BROWSER_STEPS = {
    'test.yml': frozenset({'Browser authentication smoke', 'Browser routing and subscription workflow',
        'Browser proxy import, drafts and runtime UI lifecycle',
        'Browser per-inbound direct activation through actual API and core',
        'Browser two-instance TCP upstream export and import',
        'Browser managed-certificate editor transitions and restore',
        'Install test browser', 'Prepare checksum-pinned browser frontend'}),
    'release.yml': frozenset({'Verify non-root install HTTPS browser and recovery', 'Install explicit test browser'}),
    'acme.yml': frozenset({'Test graphical certificate lifecycle against Pebble', 'Install explicit test browser'}),
}
PORTABLE_STEPS = {
    'native-arm64-glibc': 'Build exact aarch64 glibc target',
    'musl (ubuntu-24.04, x86_64)': 'Build and stage native Alpine/musl target',
    'musl (ubuntu-24.04-arm, aarch64)': 'Build and stage native Alpine/musl target',
    'distro-probe': 'Probe representative distro families without a whitelist',
}
BUILD_STEPS = frozenset({'Build exact candidate for installer test',
    'Assemble exact candidate kit for combined installation acceptance',
    'Inspect producer candidate kit', 'Upload release kit', 'Record producer receipt',
    'Upload producer receipt', 'Upload acceptance logs'})
CONSUMER_STEPS = frozenset({'Download exact candidate kit', 'Prepare verified candidate kit',
    'Record acceptance receipt', 'Upload acceptance receipt', 'Upload acceptance logs'})
FRESH_TEST = 'Test interactive installer and global v-ui on ephemeral systemd runner'
MAX_ARTIFACT = 900_000_000
MAX_RECEIPT_ARCHIVE = 65_536
MAX_POLICY = 16_384
MAX_PAGES = 10


class ParallelPolicyError(ValueError):
    """Qualification refused; no public mutation is performed by this module."""


def _need(condition, message):
    if not condition:
        raise ParallelPolicyError(message)


def _identity(commit, version, *, publish=False):
    try:
        core._source_version(commit, version)
        _need(core._version(version) > (0, 4, 22) if publish else core._version(version) >= (0, 4, 22),
              'unsupported parallel version')
    except core.ReleaseAcceptanceError as error:
        raise ParallelPolicyError('exact stable source and version required') from error


def _git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL)


def _document(path):
    parts = PurePosixPath(path).parts
    return (path in DOCUMENTS or path.startswith('docs/') and path.endswith('.md')) and bool(parts) and (
        '/'.join(parts) == path and all(part not in ('.', '..') for part in parts))


def validate_source_scope(commit, version, root=None):
    """Read policy from the commit, then prove only ordinary documentation follows freeze."""
    _identity(commit, version, publish=True)
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    try:
        entry = _git(root, 'ls-tree', '-z', commit, '--', POLICY_PATH)
        _need(re.fullmatch(rb'100644 blob [a-f0-9]{40}\t' + re.escape(POLICY_PATH.encode()) + b'\x00', entry),
              'policy must be a regular 100644 tracked file')
        size = _git(root, 'cat-file', '-s', commit + ':' + POLICY_PATH)
        _need(re.fullmatch(rb'[0-9]+\n', size) and 0 < int(size) <= MAX_POLICY, 'source policy exceeds size bound')
        policy = core._json(_git(root, 'show', commit + ':' + POLICY_PATH), MAX_POLICY)
        _need(set(policy) == POLICY_KEYS and type(policy.get('schema')) is int and policy['schema'] == 1
              and policy.get('version') == version and core._hex(policy.get('product_commit'), 40)
              and policy.get('install_contract') == 'parallel-v1'
              and policy.get('browser_policy') == 'unverified-non-browser-v1', 'invalid source policy')
        product = policy['product_commit']
        _need(product != commit, 'product freeze must precede release preparation')
        subprocess.run(['git', 'merge-base', '--is-ancestor', product, commit], cwd=root, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        expected = (version + '\n').encode()
        _need(_git(root, 'show', product + ':VERSION') == expected
              and _git(root, 'show', commit + ':VERSION') == expected, 'frozen version mismatch')
        raw = _git(root, 'diff', '--raw', '-z', '--no-renames', '--abbrev=40', product, commit)
        fields = raw.split(b'\x00')
        _need(fields[-1] == b'' and len(fields) % 2 == 1, 'invalid source diff')
        for header, path_bytes in zip(fields[:-1:2], fields[1::2]):
            match = re.fullmatch(rb':([0-9]{6}) ([0-9]{6}) [a-f0-9]{40} [a-f0-9]{40} ([AM])', header)
            _need(match is not None, 'source deletion, rename or type change refused')
            old, new, status = match.groups()
            _need(new == b'100644' and old == (b'000000' if status == b'A' else b'100644'),
                  'source mode change or nonregular file refused')
            path = path_bytes.decode('utf-8')
            _need(path == POLICY_PATH or _document(path), 'post-freeze source exceeds documentation scope')
        return policy
    except (subprocess.SubprocessError, OSError, UnicodeError, core.ReleaseAcceptanceError) as error:
        raise ParallelPolicyError('cannot prove frozen source scope') from error


def validate_latest_published(version, api):
    """Resolve only a supported immutable public baseline; never infer a new one."""
    _identity('0' * 40, version, publish=True)
    latest = api('releases/latest')
    _need(type(latest) is dict and latest.get('draft') is False and latest.get('prerelease') is False,
          'latest release is not a public stable release')
    tag = latest.get('tag_name')
    _need(type(tag) is str and tag.startswith('v'), 'latest stable tag required')
    old = tag[1:]
    try:
        _need(core._version(old) < core._version(version), 'latest release must precede candidate')
        rows = roles.matrix_for_version(version)['include']
    except core.ReleaseAcceptanceError as error:
        raise ParallelPolicyError('latest stable version invalid') from error
    _need(old in core.BASELINES and any(row['version'] == old and row['source'] == core.BASELINES[old]
                                       for row in rows), 'latest release lacks a fixed upgrade baseline')
    reference = api('git/ref/tags/' + tag)
    _need(type(reference) is dict, 'latest tag reference missing')
    item = reference.get('object')
    for depth in range(4):
        _need(type(item) is dict and core._hex(item.get('sha'), 40), 'latest tag object invalid')
        if item.get('type') == 'commit':
            _need(item['sha'] == core.BASELINES[old], 'latest tag source differs from immutable baseline')
            return {'version': old, 'source': item['sha']}
        _need(item.get('type') == 'tag' and depth < 3, 'latest tag annotation limit or type')
        tag_object = api('git/tags/' + item['sha'])
        _need(type(tag_object) is dict, 'latest annotated tag missing')
        item = tag_object.get('object')
    raise ParallelPolicyError('latest tag did not resolve')


def _pages(api, path, key):
    rows = []
    separator = '&' if '?' in path else '?'
    for page in range(1, MAX_PAGES + 1):
        value = api(path + separator + 'per_page=100&page=' + str(page))
        _need(type(value) is dict and type(value.get(key)) is list and len(value[key]) <= 100,
              'malformed or oversized API page')
        rows.extend(value[key])
        if len(value[key]) < 100:
            return rows
    raise ParallelPolicyError('API pagination exceeds bound')


def _run(run, commit, path):
    _need(type(run) is dict and core._positive(run.get('id')) and core._positive(run.get('run_attempt'))
          and run.get('head_sha') == commit and run.get('status') == 'completed'
          and run.get('conclusion') == 'success' and type(run.get('path')) is str
          and run['path'].split('@')[0] == '.github/workflows/' + path, 'workflow identity or state invalid')


def revalidate_runs(checks, commit, api):
    """Call again immediately before any public mutation; a rerun invalidates evidence."""
    _need(type(checks) is dict and set(checks) == WORKFLOWS and core._hex(commit, 40),
          'all nine exact-source workflows required')
    for path, previous in checks.items():
        _run(previous, commit, path)
        current = api('actions/runs/' + str(previous['id']))
        _run(current, commit, path)
        _need(current['id'] == previous['id'] and current['run_attempt'] == previous['run_attempt'],
              'workflow changed during qualification')


def _roles(version):
    result = {'build': ('build-kit', BUILD_STEPS), 'fresh': ('system-install', CONSUMER_STEPS | {FRESH_TEST})}
    for row in roles.matrix_for_version(version)['include']:
        old = row['version']
        result['upgrade-' + old] = ('published-upgrade-' + old, CONSUMER_STEPS | {
            f'Test published v{old} to exact candidate upgrade on ephemeral systemd runner',
            f'Check out immutable published v{old} source for real upgrade acceptance'})
    return result


def _jobs(checks, commit, version, api, browser, required):
    _need(type(browser) is dict and all(isinstance(value, (set, frozenset)) for value in browser.values())
          and {key: frozenset(value) for key, value in browser.items()} == BROWSER_STEPS,
          'only the fixed twelve browser skips are permitted')
    _need(type(required) is dict and set(required) <= WORKFLOWS - {'oneclick.yml'},
          'required steps must remain workflow-bound')
    _need(all(isinstance(value, (set, frozenset)) and all(type(name) is str and name for name in value)
              for value in required.values()), 'invalid required step contract')
    role_jobs = _roles(version)
    seen_ids = set()
    for path, run in checks.items():
        rows = _pages(api, f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", 'jobs')
        count = len(role_jobs) if path == 'oneclick.yml' else 4 if path == 'portable.yml' else 1
        _need(len(rows) == count, 'missing or unexpected workflow jobs')
        expected_names = ({name for name, _ in role_jobs.values()} if path == 'oneclick.yml'
                          else set(PORTABLE_STEPS) if path == 'portable.yml' else None)
        if expected_names is not None:
            _need(all(type(job) is dict and type(job.get('name')) is str for job in rows)
                  and {job['name'] for job in rows} == expected_names,
                  'missing, duplicated or misplaced role jobs')
        workflow_steps = set()
        for job in rows:
            _need(type(job) is dict and core._positive(job.get('id')) and job['id'] not in seen_ids
                  and core._positive(job.get('run_id')) and job['run_id'] == run['id']
                  and job.get('head_sha') == commit and job.get('status') == 'completed'
                  and job.get('conclusion') == 'success', 'exact-attempt job is not successful or unique')
            seen_ids.add(job['id'])
            steps = job.get('steps')
            _need(type(steps) is list and steps, 'job steps missing')
            names = set()
            for step in steps:
                _need(type(step) is dict and type(step.get('name')) is str and step['name']
                      and step.get('status') == 'completed'
                      and (step.get('conclusion') == 'success' or step.get('conclusion') == 'skipped'
                           and step['name'] in browser.get(path, ())), 'failed, unknown or unverified job step')
                names.add(step['name'])
            workflow_steps.update(names)
            if path == 'oneclick.yml':
                own_required = next(steps for name, steps in role_jobs.values() if name == job['name'])
                _need(own_required <= names, 'role is missing its own required acceptance steps')
                if job['name'] == 'build-kit':
                    sequence = ['Build exact candidate for installer test',
                        'Assemble exact candidate kit for combined installation acceptance',
                        'Inspect producer candidate kit', 'Upload release kit',
                        'Record producer receipt', 'Upload producer receipt', 'Upload acceptance logs']
                else:
                    test_name = FRESH_TEST if job['name'] == 'system-install' else (
                        'Test published v' + job['name'][len('published-upgrade-'):]
                        + ' to exact candidate upgrade on ephemeral systemd runner')
                    sequence = ['Download exact candidate kit', 'Prepare verified candidate kit', test_name,
                                'Record acceptance receipt', 'Upload acceptance receipt', 'Upload acceptance logs']
                ordered = [step['name'] for step in steps]
                positions = [ordered.index(name) for name in sequence]
                _need(positions == sorted(positions), 'role acceptance steps are out of order')
                if job['name'].startswith('published-upgrade-'):
                    old = job['name'][len('published-upgrade-'):]
                    ordered = [step['name'] for step in steps]
                    _need(ordered.index(f'Check out immutable published v{old} source for real upgrade acceptance')
                          < ordered.index(f'Test published v{old} to exact candidate upgrade on ephemeral systemd runner'),
                          'old checkout must precede its upgrade test')
            elif path == 'portable.yml':
                _need(PORTABLE_STEPS[job['name']] in names, 'portable role is missing its own build or probe')
        _need(frozenset(browser.get(path, ())) | frozenset(required.get(path, ())) <= workflow_steps,
              'required workflow acceptance or browser declaration missing')


def _timestamp(value):
    _need(type(value) is str, 'artifact attempt timestamps missing')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ParallelPolicyError('artifact attempt timestamp invalid') from error
    _need(parsed.tzinfo is not None and parsed.utcoffset() is not None, 'artifact timestamp must have timezone')
    return parsed


def _artifact_metadata(artifact, run, commit, repository_id, limit):
    _need(type(artifact) is dict and core._positive(artifact.get('id'))
          and type(artifact.get('name')) is str and 0 < len(artifact['name']) <= 255
          and artifact.get('expired') is False and core._positive(artifact.get('size_in_bytes'))
          and artifact['size_in_bytes'] <= limit and type(artifact.get('digest')) is str
          and re.fullmatch(r'sha256:[a-f0-9]{64}', artifact['digest']), 'artifact metadata incomplete or invalid')
    origin = artifact.get('workflow_run')
    _need(type(origin) is dict and core._positive(origin.get('id')) and origin['id'] == run['id']
          and origin.get('head_sha') == commit and core._positive(origin.get('repository_id'))
          and origin['repository_id'] == repository_id, 'artifact belongs to another workflow source')
    for value in (artifact, origin):
        if 'run_attempt' in value:
            _need(core._positive(value['run_attempt']) and value['run_attempt'] == run['run_attempt'],
                  'artifact belongs to another attempt')
    _need(_timestamp(run.get('run_started_at')) <= _timestamp(artifact.get('created_at'))
          <= _timestamp(run.get('updated_at')), 'artifact is outside the accepted attempt')


def _artifacts(api, run, commit, repository_id, expected):
    rows = _pages(api, f"actions/runs/{run['id']}/artifacts", 'artifacts')
    names = set(); ids = set(); result = {}
    for item in rows:
        _need(type(item) is dict and core._positive(item.get('id')) and type(item.get('name')) is str
              and 0 < len(item['name']) <= 255, 'artifact list identity missing')
        _need(item['name'] not in names and item['id'] not in ids, 'duplicate artifact name or identity')
        names.add(item['name']); ids.add(item['id'])
        if item['name'] not in expected:
            # GitHub retains earlier attempt artifacts in this same run. Their
            # identity is never reused, downloaded, or counted as qualification.
            prefix, separator, attempt = item['name'].rpartition('-')
            allowed_prefixes = {name.rpartition('-')[0] for name in expected}
            _need(separator and prefix in allowed_prefixes and re.fullmatch(r'[1-9][0-9]{0,18}', attempt)
                  and int(attempt) < run['run_attempt'], 'unexpected role artifact or attempt')
            origin = item.get('workflow_run')
            _need(type(origin) is dict and core._positive(origin.get('id')) and origin['id'] == run['id']
                  and origin.get('head_sha') == commit and core._positive(origin.get('repository_id'))
                  and origin['repository_id'] == repository_id, 'older artifact belongs to another workflow source')
            for value in (item, origin):
                if 'run_attempt' in value:
                    _need(core._positive(value['run_attempt']) and value['run_attempt'] == int(attempt),
                          'older artifact attempt differs from its name')
            continue
        _artifact_metadata(item, run, commit, repository_id, MAX_ARTIFACT)
        result[item['name']] = item
    _need(set(result) == set(expected), 'missing current-attempt role artifacts')
    return result


def _download(artifact, limit, download):
    _need(artifact['size_in_bytes'] <= limit, 'artifact exceeds its role limit')
    raw = download(artifact, max_bytes=limit)
    _need(type(raw) is bytes and len(raw) == artifact['size_in_bytes'] and len(raw) <= limit
          and 'sha256:' + hashlib.sha256(raw).hexdigest() == artifact['digest'],
          'artifact transport bytes or digest mismatch')
    return raw


def _receipt(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            _need(archive.namelist() == ['acceptance-receipt.json'], 'receipt archive must have one exact member')
            item = archive.infolist()[0]
            _need(not item.is_dir() and not item.flag_bits & 1 and 0 < item.file_size <= core.MAX_JSON
                  and item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                  and stat.S_IFMT(item.external_attr >> 16) in (0, stat.S_IFREG), 'unsafe receipt archive member')
            return archive.read(item)
    except (zipfile.BadZipFile, RuntimeError, OSError, ValueError) as error:
        if isinstance(error, ParallelPolicyError):
            raise
        raise ParallelPolicyError('receipt archive invalid') from error


def validate_parallel_runs(checks, commit, version, *, api, download, validate_kit,
                           validate_target_artifact, browser_skip_steps, required_other_steps):
    """Validate original bytes and independent exact-attempt role executions, read-only."""
    _identity(commit, version)
    _need(type(checks) is dict and set(checks) == WORKFLOWS, 'all nine exact-source workflows required')
    ids = set()
    for path, run in checks.items():
        _run(run, commit, path)
        _need(run['id'] not in ids, 'duplicate workflow identity'); ids.add(run['id'])
    _jobs(checks, commit, version, api, browser_skip_steps, required_other_steps)
    repository = api('')
    _need(type(repository) is dict and core._positive(repository.get('id')), 'official repository identity missing')
    run = checks['oneclick.yml']
    role_jobs = _roles(version)
    def name(kind, role):
        return roles.artifact_name(kind, source=commit, version=version, run_id=run['id'],
                                   run_attempt=run['run_attempt'], role=role)
    kit_name = name('kit', 'build')
    expected = {kit_name} | {name(kind, role) for role in role_jobs for kind in ('receipt', 'log')}
    items = _artifacts(api, run, commit, repository['id'], expected)
    producer = items[kit_name]
    try:
        files = validate_kit(_download(producer, MAX_ARTIFACT, download), commit, version)
    except (ValueError, RuntimeError, KeyError, zipfile.BadZipFile) as error:
        if isinstance(error, ParallelPolicyError):
            raise
        raise ParallelPolicyError('producer kit validation failed') from error
    _need(type(files) is dict and set(files) == core.KIT_FILES and all(type(raw) is bytes for raw in files.values()),
          'validated kit must contain exactly the original sixteen files')
    table = hashlib.sha256(files['SHA256SUMS']).hexdigest()
    bundle = hashlib.sha256(files[core.BUNDLE]).hexdigest()
    receipts = {}
    for role in role_jobs:
        item = items[name('receipt', role)]
        raw = _receipt(_download(item, MAX_RECEIPT_ARCHIVE, download))
        try:
            receipt = core.validate_receipt(raw, expected_source=commit, candidate_version=version,
                run_id=run['id'], run_attempt=run['run_attempt'], role=role, producer_artifact_id=producer['id'],
                producer_artifact_digest=producer['digest'], expected_checksums_sha256=table,
                expected_bundle_sha256=bundle)
        except core.ReleaseAcceptanceError as error:
            raise ParallelPolicyError('role receipt differs from accepted source, run or producer') from error
        receipts[role] = {'artifact_id': item['id'], 'artifact_digest': item['digest'], 'receipt': receipt}
    portable = checks['portable.yml']
    portable_names = {f"vui-linux-{target}-{commit}-{portable['id']}-{portable['run_attempt']}": target
                      for target in TARGETS}
    portable_items = _artifacts(api, portable, commit, repository['id'], portable_names)
    _need({item['id'] for item in portable_items.values()}.isdisjoint(item['id'] for item in items.values()),
          'artifact identity reused across workflow runs')
    bundles = {}; portable_evidence = {}
    for artifact_name, target in portable_names.items():
        item = portable_items[artifact_name]
        raw = _download(item, MAX_ARTIFACT, download)
        try:
            bundles[target] = validate_target_artifact(raw, commit, version, target)
        except (ValueError, RuntimeError, KeyError, zipfile.BadZipFile) as error:
            raise ParallelPolicyError('portable target validation failed') from error
        _need(type(bundles[target]) is bytes, 'portable validator must return original bundle bytes')
        portable_evidence[target] = {'artifact_id': item['id'], 'artifact_digest': item['digest']}
    revalidate_runs(checks, commit, api)
    evidence = {'source_sha': commit, 'version': version, 'browser_verified': False,
        'runs': {path: {'id': value['id'], 'run_attempt': value['run_attempt']} for path, value in checks.items()},
        'producer': {'artifact_id': producer['id'], 'artifact_digest': producer['digest'],
                     'checksums_sha256': table, 'bundle_sha256': bundle},
        'receipts': receipts, 'portable': portable_evidence}
    return {'kit_files': files, 'portable_bundles': bundles, 'evidence': evidence}
