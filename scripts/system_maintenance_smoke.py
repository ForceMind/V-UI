"""Ephemeral systemd acceptance; never imported or installed by root components.

Actual socket/kernel-peer/task/service checks use installed root-owned code. The
candidate upgrade fixture replaces only network transport in memory; production
contains no URL/path/environment override. Stable selection is tested separately.
"""
from __future__ import annotations
import argparse
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

CONTROL = Path('/usr/local/lib/v-ui')
CLIENT = "import json,runpy,sys;api=runpy.run_path('/usr/local/lib/v-ui/maintenance_client.py');print(json.dumps(api['request'](json.loads(sys.stdin.read()))))"


def invoke(value, *, user='v-ui', expect_success=True):
    result = subprocess.run(['sudo', '-u', user, '/usr/bin/python3', '-B', '-I', '-S', '-c', CLIENT],
        input=json.dumps(value), capture_output=True, text=True, timeout=15)
    if not expect_success:
        assert result.returncode != 0, 'Unexpected maintenance peer accepted'
        return
    assert result.returncode == 0, 'Installed maintenance bridge unavailable'
    return json.loads(result.stdout)


def task(action):
    row = invoke({'protocol': 1, 'operation': 'submit', 'action': action})
    assert row['state'] == 'queued', 'Task not durably registered before response'
    identity = row['id']
    deadline = time.monotonic() + 150
    while row['state'] in ('queued', 'running') and time.monotonic() < deadline:
        time.sleep(.2)
        row = invoke({'protocol': 1, 'operation': 'get', 'task_id': identity})
    assert row['state'] == 'succeeded', 'Installed task did not confirm success: ' + action
    return row


def daemon_pid():
    result = subprocess.check_output(['systemctl', 'show', '--property=MainPID', '--value', 'v-ui-maintenance.service'], text=True).strip()
    assert result.isdecimal() and int(result) > 1, 'Independent root service not running'
    return result


def nonroot_smoke(check, source, kit, api, expected_proxy):
    assert os.geteuid() != 0
    assert os.getenv('GITHUB_ACTIONS') == 'true' and os.getenv('RUNNER_ENVIRONMENT') == 'github-hosted'
    assert os.getenv('VUI_SYSTEM_INSTALL_TEST') == '1'
    pid = daemon_pid()
    status = invoke({'protocol': 1, 'operation': 'status'})
    check.assertEqual(status['services']['v-ui.service'], 'active')
    check.assertTrue(status['ready'])
    check.assertRegex(status['release_id'], r'^[0-9]+\.[0-9]+\.[0-9]+-[a-f0-9]{12}$')
    check.assertIn(status['target'], ('x86_64-gnu', 'aarch64-gnu', 'x86_64-musl', 'aarch64-musl'))
    invoke({'protocol': 1, 'operation': 'status'}, user='nobody', expect_success=False)
    # Fixed JSON field contract is also enforced across the installed channel.
    invoke({'protocol': 1, 'operation': 'submit', 'action': 'status', 'unit': 'sshd.service'}, expect_success=False)
    task('status')
    backup = task('backup')
    check.assertEqual(backup['result']['backup_id'], backup['id'])
    task('stop')
    check.assertEqual(daemon_pid(), pid)
    check.assertEqual(invoke({'protocol': 1, 'operation': 'status'})['services']['v-ui.service'], 'inactive')
    # The prior persisted task is retrievable while the entire panel is stopped.
    check.assertEqual(invoke({'protocol': 1, 'operation': 'get', 'task_id': backup['id']})['state'], 'succeeded')
    task('start')
    check.assertEqual(daemon_pid(), pid)
    task('restart')
    check.assertEqual(daemon_pid(), pid)
    logs = invoke({'protocol': 1, 'operation': 'logs'})
    check.assertTrue(logs['redacted'])
    check.assertLessEqual(len(logs['entries']), 60)
    for row in logs['entries']:
        check.assertEqual(set(row), {'timestamp', 'unit', 'code', 'summary'})
    # Real installed grant launcher validates the selected package and runtime
    # after dropping to the service user. Never echo its one-time link to CI.
    def grants(*arguments):
        result = subprocess.run(['sudo', '-u', 'v-ui', '/usr/bin/python3', '-B', '-I', '-S',
            str(CONTROL / 'maintenance_cli_worker.py'), *arguments], capture_output=True, text=True, timeout=60)
        check.assertEqual(result.returncode, 0, 'Installed nonroot grant CLI failed')
        return json.loads(result.stdout)
    grant = grants('create', 'diagnose', '600')
    check.assertIn('/maintenance#grant=', grant['link'])
    check.assertTrue(any(row['id'] == grant['id'] for row in grants('list')))
    grants('revoke', str(grant['id']))
    from online_install_helpers import PrivateMenuSession
    with PrivateMenuSession(['sudo', '/usr/local/sbin/v-ui'], cwd='/tmp', env=dict(os.environ), timeout=60) as terminal:
        terminal.expect('请选择: '); terminal.send('13\n')
        terminal.expect('请选择授权操作: '); terminal.send('1\n')
        terminal.expect('请选择权限 [1/2/3，默认 1]: '); terminal.send('\n')
        terminal.expect('有效分钟数 1–120（默认 30）: '); terminal.send('\n')
        issued = terminal.expect('请选择: ')
        records = [json.loads(line) for line in issued.splitlines() if line.startswith('{')]
        check.assertEqual(len(records), 1, 'Terminal grant result missing')
        check.assertEqual(records[0]['scopes'], ['diagnose'])
        terminal.send('12\n'); terminal.expect('确认下载官方稳定版本并沿用原配置升级 [y/N]: ')
        terminal.send('n\n'); cancelled = terminal.expect('请选择: ')
        check.assertIn('已取消，未下载或更改安装', cancelled)
        terminal.send('0\n'); terminal.finish()
    grants('revoke', str(records[0]['id']))
    # Run the real upgrade tail from a task worker independent of the panel.
    # Same-package activation is intentional; old14/15 product upgrades retain
    # their separate real published-source acceptance in this workflow.
    pin = hashlib.sha256((Path(kit) / 'SHA256SUMS').read_bytes()).hexdigest()
    result = subprocess.run(['sudo', '/usr/bin/python3', '-B', '-I', '-S',
        str(Path(source) / 'scripts/system_maintenance_smoke.py'), '--candidate-kit', str(kit), '--sha256', pin],
        capture_output=True, text=True, timeout=900)
    check.assertEqual(result.returncode, 0, 'Exact candidate isolated upgrade task failed; output withheld')
    record = json.loads(result.stdout)
    check.assertEqual(record['state'], 'succeeded')
    check.assertEqual(record['failed_state'], 'failed')
    check.assertEqual(record['downloaded_bundles'], [record['target_bundle']])
    check.assertEqual(daemon_pid(), pid)
    check.assertEqual(invoke({'protocol': 1, 'operation': 'get', 'task_id': record['id']})['state'], 'succeeded')
    deadline = time.monotonic() + 30
    while True:
        try:
            if api('/api/auth/me')[0] == 200: break
        except (OSError, http.client.HTTPException):
            pass
        if time.monotonic() >= deadline: raise AssertionError('Preserved authenticated session unavailable after maintenance upgrade')
        time.sleep(.2)
    check.assertEqual(json.loads(api('/api/proxy/settings')[1]), expected_proxy)
    # A daemon restart retains final task state instead of inventing success.
    subprocess.run(['sudo', 'systemctl', 'restart', 'v-ui-maintenance.service'], check=True, timeout=50)
    deadline = time.monotonic() + 20
    while True:
        try:
            persisted = invoke({'protocol': 1, 'operation': 'get', 'task_id': record['id']})
            break
        except AssertionError:
            if time.monotonic() >= deadline: raise
            time.sleep(.2)
    check.assertEqual(persisted['state'], 'succeeded')
    print('Remote system acceptance: SO_PEERCRED/file permissions, closed JSON, persistent async status/backup/stop/start/restart, independent root PID, redacted logs, nonroot grants, exact-candidate upgrade with single-target transport and retained auth/proxy state passed; official newer-release network selection remains a separate unit boundary.')


def load(name):
    spec = importlib.util.spec_from_file_location('_vui_acceptance_' + name, CONTROL / (name + '.py'))
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


def root_candidate_fixture(kit, pin):
    # This acceptance-only entry is never packaged in a deployed application.
    # Its input comes from the workflow's exact-head candidate kit and digest.
    assert os.getuid() == 0 and sys.flags.isolated and sys.flags.no_site
    ops = load('system_ops'); ops.verify_installation()
    config = ops.decode_json(ops.trusted_bytes(ops.CONFIG, 16384))
    upgrade = load('maintenance_upgrade')
    daemon = load('maintenance_daemon')
    kit = Path(kit)
    raw = (kit / 'SHA256SUMS').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == pin
    upgrade.checksums(raw, pin)
    release = json.loads((kit / 'RELEASE.json').read_bytes())
    version = 'v' + release['version']
    base = upgrade.BASE + version + '/'
    downloaded = []
    def fixed_transport(url, path=None, *, limit):
        assert url.startswith(base) and url.count('/') == base.count('/')
        name = url[len(base):]
        assert name in upgrade.ASSETS | upgrade.BUNDLES | {'SHA256SUMS'}
        blob = (kit / name).read_bytes()
        assert len(blob) <= limit
        if path is None: return blob
        with path.open('xb') as output: output.write(blob)
        downloaded.append(name)
        return hashlib.sha256(blob).hexdigest()
    upgrade.download = fixed_transport
    daemon.load = lambda name: types_upgrade if name == 'maintenance_upgrade' else None
    # The real worker/store finalizes the persisted task. Only trusted candidate
    # transport and same-version selection are injected in this disposable test.
    import types
    types_upgrade = types.SimpleNamespace(upgrade=lambda controller: upgrade.install_release(controller, config, version, pin))
    store = daemon.Store(ops)
    # Corrupted target bytes never reach the installer and still produce a
    # durable failed task. Original service/configuration remain available.
    def corrupt_transport(url, path=None, *, limit):
        digest = fixed_transport(url, path, limit=limit)
        return '0' * 64 if path is not None and path.name in upgrade.BUNDLES else digest
    upgrade.download = corrupt_transport
    failed = {'id': uuid.uuid4().hex, 'action': 'upgrade', 'state': 'queued', 'created_at': int(time.time())}
    store.write(failed)
    bad_pid = os.fork()
    if bad_pid == 0:
        daemon.execute(ops, store, failed)
    os.waitpid(bad_pid, 0)
    failed = store.read(failed['id'])
    assert failed['state'] == 'failed'
    assert ops.decode_json(ops.trusted_bytes(ops.CONFIG, 16384)) == config
    ops.verify_installation()
    upgrade.download = fixed_transport
    row = {'id': uuid.uuid4().hex, 'action': 'upgrade', 'state': 'queued', 'created_at': int(time.time())}
    store.write(row)
    # Record selected file names across fork without disclosing URLs or bytes.
    trace = Path('/var/lib/v-ui-maintenance') / ('.acceptance-' + uuid.uuid4().hex)
    original = upgrade.download
    def traced(*args, **kwargs):
        result = original(*args, **kwargs)
        if len(args) > 1 and args[1] is not None:
            with trace.open('a') as output: output.write(args[1].name + '\n')
        return result
    upgrade.download = traced
    pid = os.fork()
    if pid == 0: daemon.execute(ops, store, row)
    _, status = os.waitpid(pid, 0)
    result = store.read(row['id'])
    names = trace.read_text().splitlines() if trace.exists() else []
    trace.unlink(missing_ok=True)
    assert os.waitstatus_to_exitcode(status) == 0 and result['state'] == 'succeeded'
    print(json.dumps({'id': row['id'], 'state': result['state'], 'failed_state': failed['state'], 'target_bundle': 'vui-linux-' + config['target'] + '.zip',
        'downloaded_bundles': sorted(set(names) & upgrade.BUNDLES)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-kit', required=True)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    root_candidate_fixture(args.candidate_kit, args.sha256)
