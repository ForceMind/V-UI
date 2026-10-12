"""Read-only CI artifact admission for the explicitly labelled system test.

This never publishes, invokes sudo, installs a kit or executes downloaded code.
The runtime token is used only by the existing GET/download helpers. Call the
separate, default-closed qualification program only after this admission passes.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from scripts import publish_release as artifacts

REPO = 'ForceMind/V-UI'
LABEL = 'run-managed-system-control'
MAX_PAGES = 10


class AdmissionError(RuntimeError):
    pass


def event_head(event, environment):
    if (environment.get('GITHUB_REPOSITORY') != REPO
            or environment.get('GITHUB_EVENT_NAME') != 'pull_request'
            or not isinstance(event, dict) or event.get('action') != 'labeled'
            or event.get('label', {}).get('name') != LABEL):
        raise AdmissionError('explicit_same_repository_label_event_required')
    pr = event.get('pull_request', {})
    head = pr.get('head', {})
    number = pr.get('number')
    if (type(number) is not int or number <= 0 or pr.get('state') != 'open'
            or head.get('repo', {}).get('full_name') != REPO
            or pr.get('base', {}).get('repo', {}).get('full_name') != REPO
            or not isinstance(head.get('sha'), str)
            or not re.fullmatch(r'[a-f0-9]{40}', head['sha'])):
        raise AdmissionError('fork_closed_or_invalid_pull_request')
    return number, head['sha']


def live_head(api, number, commit):
    pr = api('pulls/' + str(number))
    if (pr.get('number') != number or pr.get('state') != 'open'
            or pr.get('head', {}).get('sha') != commit
            or pr.get('head', {}).get('repo', {}).get('full_name') != REPO
            or pr.get('base', {}).get('repo', {}).get('full_name') != REPO):
        raise AdmissionError('pull_request_changed')


def pages(api, path, key):
    rows = []
    separator = '&' if '?' in path else '?'
    for page in range(1, MAX_PAGES + 1):
        value = api(path + separator + 'per_page=100&page=' + str(page))
        batch = value.get(key)
        if not isinstance(batch, list) or len(batch) > 100:
            raise AdmissionError('malformed_api_page')
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise AdmissionError('api_pagination_limit')


def current_attempt_artifact(artifact, run, commit):
    identity = artifact.get('workflow_run', {})
    if (type(artifact.get('id')) is not int or artifact['id'] <= 0
            or identity.get('id') != run['id'] or identity.get('head_sha') != commit
            or type(artifact.get('size_in_bytes')) is not int
            or not 0 < artifact['size_in_bytes'] <= 900_000_000
            or not isinstance(artifact.get('digest'), str)
            or not re.fullmatch(r'sha256:[a-f0-9]{64}', artifact['digest'])):
        raise AdmissionError('artifact_identity_or_digest_missing')
    try:
        created = datetime.fromisoformat(artifact['created_at'].replace('Z', '+00:00'))
        started = datetime.fromisoformat(run['run_started_at'].replace('Z', '+00:00'))
        finished = datetime.fromisoformat(run['updated_at'].replace('Z', '+00:00'))
        if created < started or created > finished:
            raise AdmissionError('artifact_not_from_current_successful_attempt')
    except (KeyError, TypeError, ValueError, AttributeError):
        raise AdmissionError('artifact_attempt_time_unconfirmed') from None


def prepare(event, environment, destination, source, *, api=artifacts.request,
            download=artifacts.download_artifact):
    if os.geteuid() == 0:
        raise AdmissionError('artifact_admission_must_be_nonroot')
    number, commit = event_head(event, environment)
    source, destination = Path(source), Path(destination)
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True, timeout=15).strip()
    if actual != commit:
        raise AdmissionError('checkout_head_mismatch')
    subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--'], cwd=source, check=True, timeout=15)
    if destination.exists() or destination.is_symlink():
        raise AdmissionError('artifact_destination_must_be_new')
    live_head(api, number, commit)
    runs = pages(api, 'actions/runs?head_sha=' + commit, 'workflow_runs')
    selected = artifacts.select_runs(runs, commit)
    for run in selected.values():
        if (run.get('event') not in ('pull_request', 'push')
                or run.get('repository', {}).get('full_name') != REPO
                or run.get('head_repository', {}).get('full_name') != REPO):
            raise AdmissionError('untrusted_workflow_source')
    run = selected['oneclick.yml']
    items = pages(api, 'actions/runs/' + str(run['id']) + '/artifacts', 'artifacts')
    artifact = artifacts.unique_artifact(items, 'vui-release-kit-' + commit)
    current_attempt_artifact(artifact, run, commit)
    raw = download(artifact)
    if (len(raw) != artifact['size_in_bytes']
            or 'sha256:' + hashlib.sha256(raw).hexdigest() != artifact['digest']):
        raise AdmissionError('artifact_transport_mismatch')
    version = (source / 'VERSION').read_text().strip()
    files = artifacts.validate_kit(raw, commit, version)
    # A newer PR update while downloading cannot silently change the target.
    live_head(api, number, commit)
    destination.mkdir(mode=0o700)
    kit = destination / 'kit'; kit.mkdir(mode=0o700)
    for name, value in files.items():
        # validate_kit enforces the exact flat member allowlist before any write.
        with (kit / name).open('xb') as handle:
            handle.write(value)
        (kit / name).chmod(0o600)
    digest = hashlib.sha256(files['SHA256SUMS']).hexdigest()
    record = {'schema': 1, 'repository': REPO, 'pull_request': number,
              'source_commit': commit, 'kit_artifact_id': artifact['id'],
              'kit_artifact_digest': artifact['digest'], 'manifest_sha256': digest,
              'oneclick_run_id': run['id'], 'oneclick_run_attempt': run['run_attempt'],
              'required_successful_runs': {name: {'id': value['id'], 'attempt': value['run_attempt']}
                                           for name, value in selected.items()},
              'qualification_executed': False}
    (destination / 'admission.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    (destination / 'manifest.sha256').write_text(digest + '\n')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    event_path = Path(os.environ['GITHUB_EVENT_PATH'])
    if event_path.stat().st_size > 1_000_000:
        raise AdmissionError('event_too_large')
    event = json.loads(event_path.read_text())
    prepare(event, os.environ, args.destination, Path(__file__).resolve().parents[1])
    print('Exact-head successful kit admitted; no privileged operation executed.')


if __name__ == '__main__':
    main()
