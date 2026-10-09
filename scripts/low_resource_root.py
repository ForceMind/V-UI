"""Explicit disposable-runner root installer fixture; never an operational tool."""
from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import subprocess
import sys
import time
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
ROOT = Path('/var/lib/v-ui')
RESERVED = (ROOT, Path('/etc/v-ui'), Path('/usr/local/lib/v-ui'))
UNITS = ('v-ui.service', 'v-ui-http01.service', 'v-ui-http01.socket')
SLICE_RE = re.compile(r'vuiroot[0-9a-f]{32}\.slice')


def checked_slice(name):
    if not isinstance(name, str) or not SLICE_RE.fullmatch(name):
        raise RuntimeError('Invalid owned parent slice')
    return name


def membership(text, parent):
    checked_slice(parent)
    rows = [line.split(':', 2) for line in text.splitlines()]
    unified = [row[2] for row in rows if len(row) == 3 and row[:2] == ['0', '']]
    if len(unified) != 1:
        raise RuntimeError('Expected unified cgroup membership')
    path = Path(unified[0])
    if not path.is_absolute() or '..' in path.parts or parent not in path.parts:
        raise RuntimeError('Process escaped measured parent slice')
    return str(path)


def identity(pid, parent, proc=Path('/proc')):
    base = proc / str(int(pid))
    fields = dict(line.split(':', 1) for line in (base/'status').read_text().splitlines() if ':' in line)
    return {'pid': int(pid), 'ppid': int(fields['PPid']),
            'uid': [int(value) for value in fields['Uid'].split()],
            'starttime_ticks': int((base/'stat').read_text().rsplit(')', 1)[1].split()[19]),
            'cgroup': membership((base/'cgroup').read_text(), parent)}


def run(command, *, timeout=15, check=True, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                          check=check, **kwargs)


def unit_properties(unit):
    result = run(['systemctl', 'show', unit, '--property=LoadState,ActiveState,MainPID,Slice,ControlGroup,FragmentPath,DropInPaths'], check=False)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def lexists(path):
    return os.path.lexists(path)


def preflight(*, reserved=RESERVED, search_paths=None, accounts=True, port=True):
    """Read-only. Call BEFORE acquiring cleanup rights or creating any resource."""
    if accounts:
        for lookup in (pwd.getpwnam, grp.getgrnam):
            try:
                lookup('v-ui')
            except KeyError:
                pass
            else:
                raise RuntimeError('Pre-existing v-ui account/group; refusing test')
    if search_paths is None:
        search_paths = [Path(line) for line in run(['systemd-analyze', 'unit-paths']).stdout.splitlines() if line]
    paths = list(reserved) + [Path('/run/lock/v-ui-install.lock')]
    for base in search_paths:
        paths.extend(base/name for name in UNITS)
        paths.extend(base/(name+'.d') for name in UNITS)
        if base.is_dir():
            for entry in base.iterdir():
                if entry.name.endswith(('.wants', '.requires', '.upholds')) and entry.is_dir():
                    paths.extend(entry/name for name in UNITS)
    occupied = [str(path) for path in paths if lexists(path)]
    if occupied:
        raise RuntimeError('Pre-existing reserved test paths: '+', '.join(occupied))
    for unit in UNITS:
        properties = unit_properties(unit)
        if properties.get('LoadState') != 'not-found' or properties.get('ActiveState') not in ('inactive', 'failed'):
            raise RuntimeError('Pre-existing loaded service: '+unit)
    if port:
        with socket.socket() as probe:
            probe.bind(('0.0.0.0', 80))
    return {'reserved_paths_absent': True, 'units_absent': True,
            'unit_search_paths': [str(path) for path in search_paths], 'http01_port_free': port}


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def synthetic_bundle(source, destination, commit):
    """Only a manifest identity changes. Product entries/modes are compared."""
    with zipfile.ZipFile(source) as original:
        entries = original.infolist()
        if len({entry.filename for entry in entries}) != len(entries):
            raise RuntimeError('Duplicate bundle members')
        manifest = json.loads(original.read('MANIFEST.json'))
        if manifest['source_commit'] != commit:
            raise RuntimeError('Wrong source commit')
        next_manifest = {**manifest, 'release_id': manifest['release_id']+'-root-fixture'}
        with Path(destination).open('xb') as output, zipfile.ZipFile(output, 'w') as archive:
            for entry in entries:
                if entry.filename == 'MANIFEST.json':
                    archive.writestr(entry, json.dumps(next_manifest, sort_keys=True, indent=2))
                else:
                    with original.open(entry) as reader, archive.open(entry, 'w') as writer:
                        while chunk := reader.read(1024*1024):
                            writer.write(chunk)
    with zipfile.ZipFile(source) as a, zipfile.ZipFile(destination) as b:
        if a.namelist() != b.namelist():
            raise RuntimeError('Synthetic bundle member set/order changed')
        for name in a.namelist():
            if a.getinfo(name).external_attr != b.getinfo(name).external_attr:
                raise RuntimeError('Synthetic member mode changed')
            if name != 'MANIFEST.json':
                with a.open(name) as left, b.open(name) as right:
                    if hashlib.file_digest(left, 'sha256').digest() != hashlib.file_digest(right, 'sha256').digest():
                        raise RuntimeError('Synthetic product bytes changed')
        actual = json.loads(b.read('MANIFEST.json'))
        if {**actual, 'release_id': manifest['release_id']} != manifest:
            raise RuntimeError('Synthetic metadata changed beyond release_id')
    return {'source_commit': commit, 'a_release_id': manifest['release_id'],
            'b_release_id': next_manifest['release_id'], 'a_sha256': file_hash(source),
            'b_sha256': file_hash(destination), 'product_members_identical': True,
            'synthetic_release_only': True}


def service_identity(unit, parent):
    properties = unit_properties(unit)
    group = properties.get('ControlGroup', '')
    expected_dropin = f'/run/systemd/system/{unit}.d/90-vui-resource.conf'
    if (properties.get('Slice') != parent or properties.get('ActiveState') != 'active'
            or expected_dropin not in properties.get('DropInPaths', '').split()):
        raise RuntimeError('Service does not use the owned parent/drop-in: '+unit)
    process = identity(int(properties['MainPID']), parent)
    account = pwd.getpwnam('v-ui')
    if process['uid'] != [account.pw_uid]*4 or process['cgroup'] != group:
        raise RuntimeError('Service ownership or actual membership mismatch')
    return {'properties': properties, 'process': process}


def root_state(parent):
    """Safe summary only: never serialize cookies, database rows or key material."""
    own = identity(os.getpid(), parent)
    if own['uid'] != [0]*4:
        raise RuntimeError('Privileged observation did not enter as root')
    state = {'observer': own, 'services': {}}
    for unit in UNITS[:2]:
        if unit_properties(unit).get('ActiveState') == 'active':
            state['services'][unit] = service_identity(unit, parent)
    if (ROOT/'CURRENT.json').exists():
        state['current'] = json.loads((ROOT/'CURRENT.json').read_text())
    config = Path('/etc/v-ui/service.json')
    if config.exists():
        raw = json.loads(config.read_text())
        state['configuration'] = {key: raw.get(key) for key in (
            'domain', 'email', 'admin', 'port', 'bind', 'certificate_mode',
            'release_id', 'bootstrap_python', 'ready')}
    if ROOT.exists():
        state['backups'] = sorted(path.name for path in ROOT.glob('before-upgrade-*.zip'))
        state['releases'] = sorted(path.name for path in (ROOT/'releases').iterdir()) if (ROOT/'releases').exists() else []
    return state


def root_operation(action, parent, value=None):
    own = identity(os.getpid(), parent)
    if own['uid'] != [0]*4:
        raise RuntimeError('Expected measured root operation')
    if action == 'state':
        return root_state(parent)
    account = pwd.getpwnam('v-ui')
    marker = ROOT/'data/resource-fixture-marker.txt'
    if action == 'seed':
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            output.write('before-upgrade\n')
            output.flush(); os.fsync(output.fileno())
            os.fchown(output.fileno(), account.pw_uid, account.pw_gid)
        return {'marker_seeded': True, 'observer': own}
    if action == 'mutate':
        if marker.is_symlink() or marker.stat().st_uid != account.pw_uid:
            raise RuntimeError('Wrong marker ownership')
        marker.write_text('after-upgrade\n')
        return {'marker_mutated': True, 'observer': own}
    if action in ('bad-restore', 'restore'):
        if not re.fullmatch(r'before-upgrade-[0-9]+\.zip', value or ''):
            raise RuntimeError('Invalid exact backup name')
        backup = ROOT/value
        if backup.is_symlink() or not backup.is_file() or backup.stat().st_uid != account.pw_uid:
            raise RuntimeError('Backup ownership mismatch')
        current = json.loads((ROOT/'CURRENT.json').read_text())['release_id']
        release = ROOT/'releases'/current
        digest = file_hash(backup)
        command = [str(release/'runtime/python/bin/python3'), '-B',
                   str(release/'payload/scripts/deploy.py'), '--root', str(ROOT),
                   'restore', str(backup), '--sha256', '0'*64 if action == 'bad-restore' else digest]
        from app.release_tools import files_in
        before = marker.read_bytes()
        before_files = files_in(ROOT/'data') if action == 'bad-restore' else None
        result = run(command, timeout=120, check=False, user=account.pw_uid,
                     group=account.pw_gid, extra_groups=[])
        if action == 'bad-restore':
            if result.returncode == 0 or marker.read_bytes() != before or files_in(ROOT/'data') != before_files:
                raise RuntimeError('Bad-digest restore failed to preserve marker')
            if 'digest' not in (result.stdout+result.stderr).lower() and 'sha' not in (result.stdout+result.stderr).lower():
                raise RuntimeError('Bad restore failed for an unrelated reason')
        elif result.returncode != 0 or marker.read_text() != 'before-upgrade\n':
            raise RuntimeError('Verified restore did not recover synthetic data')
        if json.loads((ROOT/'CURRENT.json').read_text())['release_id'] != current:
            raise RuntimeError('Data restore changed active code')
        import sqlite3
        with sqlite3.connect(f'file:{ROOT / "data/v-ui.db"}?mode=ro', uri=True) as db:
            integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity != 'ok':
            raise RuntimeError('Database integrity lost')
        return {'operation': action, 'backup_name': value, 'backup_sha256': digest,
                'returncode': result.returncode, 'current_unchanged': True,
                'marker_sha256': file_hash(marker), 'database_integrity': integrity,
                'observer': own}
    if action == 'permissions':
        result = {}
        for path, mode in ((ROOT, 0o700), (ROOT/'data', 0o700), (marker, 0o600)):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != account.pw_uid or stat.S_IMODE(info.st_mode) != mode:
                raise RuntimeError('Private data ownership/mode mismatch')
            result[str(path)] = {'uid': info.st_uid, 'mode': oct(stat.S_IMODE(info.st_mode))}
        return result
    raise RuntimeError('Unsupported measured root operation')


class Gate:
    """Optional hooks for the existing oneclick test; supervisor owns cleanup."""
    def __init__(self, config):
        self.config = config
        self.parent = checked_slice(config['slice'])
        self.directory = Path('/sys/fs/cgroup')/self.parent
        self.output = Path(config['worker_output'])
        self.report = {'source_commit': config['source_commit'], 'slice': self.parent,
                       'stages': [], 'outcome': 'running', 'complete': False,
                       'worker': identity(os.getpid(), self.parent)}
        self.record('worker_started')

    def record(self, name, details=None):
        from scripts.low_resource_acceptance import metrics, assert_no_oom, verify_limits, write_json
        row = {'name': name, 'monotonic': time.monotonic(), 'limits': verify_limits(self.directory),
               'metrics': metrics(self.directory), 'details': details or {}}
        assert_no_oom(row['metrics'])
        self.report['stages'].append(row)
        write_json(self.output, self.report)
        return row

    def command(self, command):
        if command[:2] != ['sudo', 'bash']:
            raise RuntimeError('Unexpected installer invocation')
        return ['sudo', '-n', sys.executable, '-B', str(Path(__file__).resolve()),
                '--root-exec', self.parent, '--', *command[1:]]

    def operation(self, action, value=None):
        command = ['sudo', '-n', sys.executable, '-B', str(Path(__file__).resolve()),
                   '--root-operation', action, '--slice', self.parent]
        if value is not None:
            command += ['--value', value]
        return json.loads(run(command, timeout=135).stdout)

    def installed(self):
        self.record('root_install_and_http01', self.operation('state'))
        self.operation('seed')
        self.record('marker_seeded_before_backups')

    def finish(self, case, command, api, login):
        """Runs after all original install/repeat/same-bundle/restart assertions."""
        import threading
        before = self.operation('state')
        package = self.config['package']
        case.assertEqual(before['current']['release_id'], package['a_release_id'])
        case.assertNotIn(package['b_release_id'], before['releases'])
        case.assertIn('v-ui.service', before['services'])
        self.record('original_oneclick_controls_passed', before)
        new_command = list(command)
        new_command[new_command.index('--bundle')+1] = self.config['bundle_b']
        new_command[new_command.index('--sha256')+1] = package['b_sha256']
        samples = []; errors = []; stop = threading.Event()
        def observe():
            while not stop.wait(.25):
                try:
                    state = self.operation('state')
                    state['monotonic'] = time.monotonic()
                    samples.append(state)
                except Exception as exc:
                    errors.append(type(exc).__name__)
        observer = threading.Thread(target=observe, daemon=True)
        observer.start()
        try:
            upgraded = run(new_command+['--upgrade'], timeout=240, check=False)
        finally:
            stop.set(); observer.join(timeout=140)
        case.assertFalse(observer.is_alive())
        case.assertEqual(upgraded.returncode, 0, (upgraded.stdout+upgraded.stderr)[-12000:])
        # Only accept a sample with B newly present while A is the SAME active
        # generation. Before/after success alone cannot prove live staging.
        old = before['services']['v-ui.service']['process']
        live_stage = [state for state in samples
            if package['b_release_id'] in state.get('releases', [])
            and state.get('current', {}).get('release_id') == package['a_release_id']
            and state.get('services', {}).get('v-ui.service', {}).get('process', {}) == old]
        case.assertTrue(live_stage, 'No real observation of A alive during fresh B staging')
        after = self.operation('state')
        case.assertEqual(after['current']['release_id'], package['b_release_id'])
        backups = sorted(set(after['backups'])-set(before['backups']))
        case.assertEqual(len(backups), 1)
        for key in ('domain', 'email', 'admin', 'port', 'bind', 'certificate_mode'):
            case.assertEqual(after['configuration'][key], before['configuration'][key])
        case.assertTrue(after['configuration']['ready'])
        case.assertEqual(after['configuration']['release_id'], package['b_release_id'])
        case.assertIn('/'+package['b_release_id']+'/', after['configuration']['bootstrap_python'])
        case.assertEqual(api('/api/auth/me')[0], 200)
        self.record('fresh_directory_upgrade', {'before': before, 'after': after,
                    'staging_live_samples': live_stage, 'observer_errors': errors,
                    'backup_name': backups[0]})
        run(['sudo', '-n', 'systemctl', 'stop', 'v-ui-http01.socket', 'v-ui-http01.service', 'v-ui.service'], timeout=50)
        self.operation('mutate')
        self.record('stopped_bad_digest_preserves_data', self.operation('bad-restore', backups[0]))
        self.record('stopped_verified_restore', self.operation('restore', backups[0]))
        self.record('restored_private_permissions', self.operation('permissions'))
        run(['sudo', '-n', 'systemctl', 'start', 'v-ui-http01.socket', 'v-ui.service'], timeout=30)
        deadline = time.monotonic()+20
        while time.monotonic() < deadline:
            try:
                status = api('/api/auth/me')[0]
                if status == 401:
                    break
                case.assertNotEqual(status, 200, 'Restore resurrected an old session')
            except (OSError, ConnectionError):
                pass
            time.sleep(.2)
        else:
            raise RuntimeError('Restored HTTPS did not reject old session within deadline')
        login()
        case.assertEqual(api('/api/auth/me')[0], 200)
        import http.client
        connection = http.client.HTTPConnection('127.0.0.1', 80, timeout=5)
        try:
            connection.request('GET', '/.well-known/acme-challenge/'+'T'*43)
            response = connection.getresponse()
            case.assertEqual(response.status, 200)
            case.assertEqual(response.read(), b'token.key_authorization')
        finally:
            connection.close()
        self.record('restored_new_login_and_membership', self.operation('state'))
        self.report.update(outcome='passed', complete=True)
        from scripts.low_resource_acceptance import write_json
        write_json(self.output, self.report)


def optional_gate():
    value = os.environ.get('VUI_ROOT_RESOURCE_CONFIG')
    return Gate(json.loads(Path(value).read_text())) if value else None


def bounded_stop(unit, records):
    """The unit name is owned by the already completed empty-host preflight."""
    try:
        stopped = run(['systemctl', 'stop', unit], timeout=12, check=False)
        stop_returncode = stopped.returncode
    except subprocess.TimeoutExpired:
        stop_returncode = 'timeout'
    state = unit_properties(unit)
    if state.get('ActiveState') not in ('inactive', 'failed') or int(state.get('MainPID', '0')):
        run(['systemctl', 'kill', '--kill-whom=all', '--signal=KILL', unit], check=False, timeout=5)
        run(['systemctl', 'stop', unit], check=False, timeout=5)
        state = unit_properties(unit)
    if state.get('ActiveState') not in ('inactive', 'failed') or int(state.get('MainPID', '0')):
        raise RuntimeError('Owned unit did not stop: '+unit)
    records.append({'unit': unit, 'stop_returncode': stop_returncode, 'final': state})


def cleanup_owned(parent, worker, report):
    """Only called after complete empty-host preflight established ownership."""
    import shutil
    from scripts.low_resource_acceptance import metrics
    records = []; errors = []
    # Prevent socket activation BEFORE stopping the responder/panel/worker.
    for unit in ('v-ui-http01.socket', 'v-ui-http01.service', 'v-ui.service', worker):
        try:
            bounded_stop(unit, records)
        except Exception as exc:
            errors.append({'unit': unit, 'error': type(exc).__name__})
    directory = Path('/sys/fs/cgroup')/parent
    if directory.exists():
        report['final_metrics'] = metrics(directory)
    else:
        errors.append({'error': 'MissingParentBeforeFinalAccounting'})
    report['stops'] = records
    if errors:
        # Never unlink state or delete an account while an owned process lives.
        report['cleanup_errors'] = errors
        return False
    for unit in UNITS:
        run(['systemctl', 'disable', unit], timeout=10, check=False)
    try:
        account = pwd.getpwnam('v-ui')
    except KeyError:
        account = None
    for path in RESERVED:
        if lexists(path):
            info = path.lstat()
            expected = account.pw_uid if path == ROOT and account else 0
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != expected:
                raise RuntimeError('Unexpected cleanup root ownership: '+str(path))
            shutil.rmtree(path)
    for unit in UNITS:
        path = Path('/etc/systemd/system')/unit
        if lexists(path):
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
                raise RuntimeError('Unexpected owned unit identity')
            if not path.read_text().startswith('# Managed by V-UI'):
                raise RuntimeError('Owned unit lost installer marker')
            path.unlink()
    for unit in UNITS[:2]:
        directory = Path('/run/systemd/system')/(unit+'.d')
        dropin = directory/'90-vui-resource.conf'
        expected = '[Service]\nSlice='+parent+'\n'
        if dropin.is_symlink() or dropin.read_text() != expected:
            raise RuntimeError('Owned drop-in identity changed')
        dropin.unlink(); directory.rmdir()
    if account:
        run(['userdel', 'v-ui'], timeout=10)
        try:
            group = grp.getgrnam('v-ui')
        except KeyError:
            group = None
        if group:
            if group.gr_gid != account.pw_gid or group.gr_mem:
                raise RuntimeError('Unexpected service group ownership')
            run(['groupdel', 'v-ui'], timeout=10)
    lock = Path('/run/lock/v-ui-install.lock')
    if lexists(lock):
        info = lock.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1:
            raise RuntimeError('Unexpected installer lock identity')
        lock.unlink()
    run(['systemctl', 'daemon-reload'], timeout=15)
    run(['systemctl', 'reset-failed', *UNITS, worker], timeout=10, check=False)
    preflight(port=False)
    run(['systemctl', 'stop', parent], timeout=10)
    path = Path('/run/systemd/system')/parent
    expected = '[Slice]\nMemoryMax=536870912\nMemorySwapMax=0\nCPUQuota=100%\nCPUQuotaPeriodSec=100ms\n'
    if path.is_symlink() or path.read_text() != expected:
        raise RuntimeError('Owned slice identity changed')
    path.unlink()
    run(['systemctl', 'daemon-reload'], timeout=15)
    report['cleanup_confirmed'] = True
    return True


def supervisor(args):
    """Privileged coordinator is outside the limit and never runs product code."""
    import uuid
    from scripts.low_resource_acceptance import metrics, verify_limits, peak_assessment, assert_no_oom, write_json
    if (os.geteuid() != 0 or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted' or args.runner_uid <= 0):
        raise RuntimeError('Explicit disposable hosted runner required')
    parent = 'vuiroot'+uuid.uuid4().hex+'.slice'
    worker = parent.removesuffix('.slice')+'.service'
    output = args.output.resolve()
    work = output.parent
    config_path = work/'root-worker-config.json'
    worker_output = work/'root-worker.json'
    report = {'source_commit': args.source_commit, 'slice': parent, 'worker_unit': worker,
              'outcome': 'failed', 'complete': False, 'cleanup_confirmed': False,
              'scope': 'same-product new-release-directory root installation/upgrade/restore; not whole VPS or version migration',
              'outside_parent': ['coordinator', 'package preparation', 'PID1 and socket host overhead']}
    # Nothing may be stopped, removed, or registered for cleanup until this passes.
    report['preflight'] = preflight()
    bundle_b = work/'synthetic-b.zip'
    report['package'] = synthetic_bundle(args.bundle, bundle_b, args.source_commit)
    bundle_b.chmod(0o644)
    config = {'slice': parent, 'source_commit': args.source_commit, 'package': report['package'],
              'bundle_b': str(bundle_b), 'worker_output': str(worker_output)}
    config_path.write_text(json.dumps(config)); config_path.chmod(0o644)
    # The isolated worker must be able to write its own partial result.
    worker_output.touch(mode=0o600); os.chown(worker_output, args.runner_uid, args.runner_gid)
    os.chown(work, args.runner_uid, args.runner_gid)
    slice_path = Path('/run/systemd/system')/parent
    created = []
    try:
        with slice_path.open('x') as file:
            file.write('[Slice]\nMemoryMax=536870912\nMemorySwapMax=0\nCPUQuota=100%\nCPUQuotaPeriodSec=100ms\n')
        created.append(str(slice_path))
        for unit in UNITS[:2]:
            directory = Path('/run/systemd/system')/(unit+'.d')
            directory.mkdir(mode=0o755)
            created.append(str(directory))
            with (directory/'90-vui-resource.conf').open('x') as file:
                file.write('[Service]\nSlice='+parent+'\n')
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'start', parent])
        directory = Path('/sys/fs/cgroup')/parent
        report['limits'] = verify_limits(directory)
        if report['limits']['cpu.max'] != '100000 100000':
            raise RuntimeError('Unexpected quota period')
        report['initial_memory_peak'] = int((directory/'memory.peak').read_text())
        command = ['systemd-run', '--wait', '--pipe', '--unit', worker, '--slice', parent,
            '--uid', str(args.runner_uid), '--gid', str(args.runner_gid),
            '--property=RuntimeMaxSec=900', '--property=KillMode=control-group', '--property=OOMPolicy=stop',
            '--working-directory='+str(SOURCE), '--setenv=VUI_SYSTEM_INSTALL_TEST=1',
            '--setenv=VUI_RELEASE_BUNDLE='+str(args.bundle.resolve()),
            '--setenv=VUI_ROOT_RESOURCE_CONFIG='+str(config_path),
            '--setenv=GITHUB_ACTIONS=true', '--setenv=RUNNER_ENVIRONMENT=github-hosted',
            sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_oneclick_system.py', '-v']
        start = time.monotonic()
        # File output avoids buffering arbitrary subprocess output in supervisor RAM.
        with (work/'root-worker.log').open('w') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=920)
        report['worker_exit'] = result.returncode
        report['wall_seconds'] = time.monotonic()-start
        report['worker'] = json.loads(worker_output.read_text())
        if result.returncode or report['worker'].get('outcome') != 'passed' or not report['worker'].get('complete'):
            raise RuntimeError('Measured root worker failed')
        report['outcome'] = 'passed'
    except Exception as exc:
        report['error'] = type(exc).__name__
    finally:
        report['created_paths'] = created
        try:
            if not cleanup_owned(parent, worker, report):
                report['outcome'] = 'failed'
        except Exception as exc:
            report['outcome'] = 'failed'; report['cleanup_error'] = type(exc).__name__
        try:
            assert_no_oom(report['final_metrics'])
            report['peak_assessment'] = peak_assessment(report['final_metrics']['memory.peak'], 536870912, os.sysconf('SC_PAGE_SIZE'))
            if not report['peak_assessment']['within_fixed_page_allowance']:
                raise RuntimeError('Parent peak exceeded fixed budget allowance')
        except Exception as exc:
            report['outcome'] = 'failed'; report['accounting_error'] = type(exc).__name__
        report['complete'] = True
        write_json(output, report)
        os.chown(output, args.runner_uid, args.runner_gid)
    return 0 if report['outcome'] == 'passed' else 1


def main():
    sys.path.insert(0, str(SOURCE))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--source-commit')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--supervisor', action='store_true')
    parser.add_argument('--runner-uid', type=int)
    parser.add_argument('--runner-gid', type=int)
    parser.add_argument('--root-operation', choices=('state', 'seed', 'mutate', 'bad-restore', 'restore', 'permissions'))
    parser.add_argument('--slice')
    parser.add_argument('--value')
    parser.add_argument('--root-exec')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.root_exec:
        own = identity(os.getpid(), args.root_exec)
        if own['uid'] != [0]*4 or not args.command or args.command[0] != '--':
            raise RuntimeError('Expected measured root installer entry')
        print('ROOT_ENTRY '+json.dumps(own), flush=True)
        os.execvp(args.command[1], args.command[1:])
    if args.root_operation:
        print(json.dumps(root_operation(args.root_operation, args.slice, args.value)))
        return 0
    if args.supervisor:
        return supervisor(args)
    from scripts.low_resource_acceptance import require_hosted_runner
    require_hosted_runner()
    if not re.fullmatch('[0-9a-f]{40}', args.source_commit or ''):
        raise RuntimeError('Exact source commit required')
    if run(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD']).stdout.strip() != args.source_commit:
        raise RuntimeError('Source checkout mismatch')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.run(['sudo', '-n', '--preserve-env=GITHUB_ACTIONS,RUNNER_ENVIRONMENT',
        sys.executable, '-B', str(Path(__file__).resolve()), '--supervisor',
        '--runner-uid', str(os.getuid()), '--runner-gid', str(os.getgid()),
        '--bundle', str(args.bundle.resolve()), '--output', str(args.output.resolve()),
        '--source-commit', args.source_commit], timeout=1250).returncode


if __name__ == '__main__':
    raise SystemExit(main())
