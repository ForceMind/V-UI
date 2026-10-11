"""Bounded, read-only attribution samples for the disposable root fixture.

Roles describe observed command templates, not exclusive resource ownership.
Never persist command lines, environment, executable paths or user data.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import resource
import re
import stat
import time

INTERVAL_SECONDS = .5  # Delay after completion; never catch up missed samples.
MAX_SAMPLES = 500
MAX_BYTES = 2 * 1024 * 1024
MAX_ROW_BYTES = 64 * 1024
MAX_GAP_SECONDS = 5
END_RESERVE_BYTES = 1024
ROLES = {'unclassified', 'managed-panel', 'managed-http01', 'controller-stage',
         'controller-backup', 'controller-activate', 'runtime-ensurepip',
         'runtime-pip-install', 'runtime-pip-check', 'candidate-health'}


SCHEMA_VERSION = 2
EXECUTION_SCHEMA_VERSION = 1
MANAGED_UNITS = {'v-ui.service': ('managed-panel', 'panel'),
                 'v-ui-http01.service': ('managed-http01', 'http01')}
SYSTEM_IMAGES = {'manager': Path('/usr/lib/systemd/systemd'),
                 'executor': Path('/usr/lib/systemd/systemd-executor')}
EXE_FIELDS = {'device', 'inode', 'mode', 'uid', 'size', 'mtime_ns', 'ctime_ns'}
EXECUTION_ERRORS = {'identity_changed', 'exe_changed', 'read_denied',
                    'process_vanished', 'invalid_cmdline', 'observation_failed'}
MANAGER_VERSION = re.compile(r'255(?:\.[0-9]+)?(?:-[A-Za-z0-9.+~]+)?')


def executable_identity(path):
    """Numeric executable identity only; never retain a /proc/exe target."""
    info = path.stat()
    return {'device': info.st_dev, 'inode': info.st_ino, 'mode': info.st_mode,
            'uid': info.st_uid, 'size': info.st_size,
            'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns}


def trusted_system_image(path):
    """Fixed system paths and every ancestor must be root-controlled."""
    for ancestor in reversed(path.parents):
        info = ancestor.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) & 0o022):
            raise RuntimeError('Untrusted system image parent')
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) & 0o022):
        raise RuntimeError('Untrusted system image')
    result = executable_identity(path)
    if (result['device'], result['inode'], result['mode'], result['uid']) != (
            info.st_dev, info.st_ino, info.st_mode, info.st_uid):
        raise RuntimeError('System image changed while observed')
    return result


def execution_baseline(proc=Path('/proc')):
    """Only the explicitly supported v255 manager/executor model qualifies."""
    from scripts import low_resource_root as root
    images = {name: trusted_system_image(path) for name, path in SYSTEM_IMAGES.items()}
    control = Path('/usr/bin/systemctl')
    controller = trusted_system_image(control)
    manager_before = executable_identity(proc / '1/exe')
    version = root.run([str(control), 'show', '--property=Version', '--value'], timeout=5).stdout.strip()
    if len(version) > 128 or not MANAGER_VERSION.fullmatch(version):
        raise RuntimeError('Unsupported running systemd manager version')
    # v255 executor.c exits from parse_argv(--version), before fd enumeration
    # or deserialization. build.c prints the project and complete build version.
    output = root.run([str(SYSTEM_IMAGES['executor']), '--version'], timeout=5).stdout
    lines = output.splitlines()
    if (len(output) > 4096 or len(lines) != 2
            or lines[0] != f'systemd 255 ({version})'
            or not re.fullmatch(r'[+A-Za-z0-9_= .-]{1,2048}', lines[1])):
        raise RuntimeError('Executor version differs from running manager')
    if (manager_before != images['manager']
            or executable_identity(proc / '1/exe') != manager_before
            or trusted_system_image(control) != controller
            or any(trusted_system_image(path) != images[name] for name, path in SYSTEM_IMAGES.items())):
        raise RuntimeError('Running manager or system image changed')
    return {'schema_version': EXECUTION_SCHEMA_VERSION, 'systemd_version': 255,
            'manager_version': version, 'executor_version': version, **images}


def service_configuration():
    # Values are used for an exact in-memory template comparison, never copied
    # to execution evidence. The ordinary state summary remains unchanged.
    return json.loads(Path('/etc/v-ui/service.json').read_text())


def managed_template(argv, root, configuration):
    if not argv:
        return 'other', None
    runtime = Path(argv[0])
    try:
        parts = runtime.relative_to(root / 'releases').parts
    except ValueError:
        return 'other', None
    if (len(parts) != 5 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', parts[0])
            or parts[1:] != ('runtime', 'python', 'bin', 'python3')):
        return 'other', None
    args = argv[1:]
    for mode in ('panel', 'http01'):
        if args == ['-B', '/usr/local/lib/v-ui/launcher.py', mode]:
            return 'launcher-' + mode, runtime
    if args == ['-B', '-m', 'app.certificates.http01', '--webroot',
                str(root / 'data/certificates/http-webroot')]:
        return 'http01', runtime
    panel = ['-B', '-m', 'app.serve', '--root', str(root),
             '--origin', configuration['origin'], '--bind', configuration['bind'],
             '--port', str(configuration['port'])]
    if configuration['certificate_mode'] == 'provided':
        panel += ['--cert', str(root / 'data/certs/provided-fullchain.pem'),
                  '--key', str(root / 'data/certs/provided-privkey.pem')]
    if args == panel:
        return 'panel', runtime
    return 'other', runtime


def managed_execution(process, parent, root, identity, proc, baseline, configuration):
    """Keep a known managed/root observation even when a later read fails.

    Identity and executable are read on both sides of the bounded argv read.
    This is a sampled stability check, not an atomic snapshot or an ABA proof.
    """
    evidence = {'schema_version': EXECUTION_SCHEMA_VERSION, 'image': 'other',
                'exe': None, 'template': 'other', 'error': None}
    observed = {**process, 'role': command_role([], process, parent, root), 'execution': evidence}
    try:
        before = identity(process['pid'], parent)
        if before != process:
            evidence['error'] = 'identity_changed'
            return observed
        executable = proc / str(process['pid']) / 'exe'
        evidence['exe'] = executable_identity(executable)
        with (proc / str(process['pid']) / 'cmdline').open('rb') as source:
            raw = source.read(MAX_ROW_BYTES + 1)
        if len(raw) > MAX_ROW_BYTES or not raw.endswith(b'\0'):
            evidence['error'] = 'invalid_cmdline'
        else:
            argv = raw[:-1].decode('utf-8').split('\0')
            template, runtime = managed_template(argv, root, configuration)
            evidence['template'] = template
            if evidence['exe'] == baseline['manager']:
                evidence['image'] = 'systemd-manager'
            elif evidence['exe'] == baseline['executor']:
                evidence['image'] = 'systemd-executor'
            elif runtime is not None and executable_identity(runtime) == evidence['exe']:
                evidence['image'] = 'runtime'
        if executable_identity(executable) != evidence['exe']:
            evidence['error'] = 'exe_changed'
        if identity(process['pid'], parent) != process:
            evidence['error'] = 'identity_changed'
    except (FileNotFoundError, ProcessLookupError):
        evidence['error'] = 'process_vanished'
    except PermissionError:
        evidence['error'] = 'read_denied'
    except UnicodeDecodeError:
        evidence['error'] = 'invalid_cmdline'
    except OSError:
        evidence['error'] = 'observation_failed'
    return observed


def process_identity(process):
    return {key: process[key] for key in ('pid', 'ppid', 'uid', 'starttime_ticks', 'cgroup')}


def managed_unit(process, parent):
    return next((unit for unit in MANAGED_UNITS
                 if process['cgroup'] == '/' + parent + '/' + unit), None)


class SampleBudgetExceeded(RuntimeError):
    pass


class ByteBudgetExceeded(RuntimeError):
    pass


class RowBudgetExceeded(RuntimeError):
    pass


def command_role(argv, process, parent, root):
    """Match complete known templates, never substring/basename-only roles."""
    for leaf, role in (('v-ui.service', 'managed-panel'),
                       ('v-ui-http01.service', 'managed-http01')):
        if process['cgroup'] == '/' + parent + '/' + leaf:
            return role  # Leaf membership; may include service children.
    if not argv:
        return 'unclassified'
    executable = Path(argv[0])
    if not re.fullmatch(r'python(?:3(?:\.[0-9]+)?)?', executable.name):
        return 'unclassified'
    args = argv[1:]
    if args[:1] == ['-B']:
        args = args[1:]
    if args:
        controller = Path(args[0])
        private_controller = (controller.parent.name == 'scripts'
            and controller.name == 'deploy.py'
            and controller.parent.parent.parent == root
            and controller.parent.parent.name.startswith('.installer-'))
        if (private_controller and len(args) >= 5
                and args[1:3] == ['--root', str(root)]):
            action = args[3]
            if action == 'stage' and len(args) == 7 and args[5] == '--sha256':
                return 'controller-stage'
            if action in ('backup', 'activate') and len(args) == 5:
                return 'controller-' + action
    # The runtime path must be the actual argv executable in a release tree.
    # Rechecking /proc identity around this read prevents a reused PID role.
    try:
        parts = executable.relative_to(root / 'releases').parts
    except ValueError:
        return 'unclassified'
    if len(parts) != 5 or parts[1:] != ('runtime', 'python', 'bin', 'python3'):
        return 'unclassified'
    if args == ['-m', 'ensurepip', '--upgrade']:
        return 'runtime-ensurepip'
    if args == ['-m', 'pip', '--isolated', 'check']:
        return 'runtime-pip-check'
    if (len(args) == 12 and args[:9] == ['-m', 'pip', '--isolated',
            '--disable-pip-version-check', 'install', '--no-index', '--only-binary=:all:',
            '--require-hashes', '--find-links'] and args[10] == '-r'
            and Path(args[9]).parent.parent == root / 'releases' / parts[0] / 'payload'
            and Path(args[11]).parent == root / 'releases' / parts[0] / 'payload'):
        return 'runtime-pip-install'
    if args == ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1',
                '--port', '0', '--no-proxy-headers', '--no-use-colors']:
        return 'candidate-health'
    return 'unclassified'


def observed_process(process, parent, root, identity, proc=Path('/proc'), *, baseline=None, configuration=None):
    if managed_unit(process, parent) is not None:
        return managed_execution(process, parent, root, identity, proc, baseline, configuration)
    try:
        with (proc / str(process['pid']) / 'cmdline').open('rb') as source:
            raw = source.read(MAX_ROW_BYTES + 1)
        if identity(process['pid'], parent) != process:
            return None
        argv = raw.rstrip(b'\0').decode('utf-8').split('\0') if len(raw) <= MAX_ROW_BYTES else []
        role = command_role(argv, process, parent, root)
    except (FileNotFoundError, ProcessLookupError, UnicodeDecodeError):
        return None
    return {**process, 'role': role}


def capture(parent):
    from scripts import low_resource_root as root
    from scripts.low_resource_acceptance import metrics, verify_limits
    started = time.monotonic()
    directory = Path('/sys/fs/cgroup') / root.checked_slice(parent)
    observer = root.identity(os.getpid(), parent)
    if observer['uid'] != [0] * 4:
        raise RuntimeError('Accounting observer is not the existing root helper')
    baseline = execution_baseline()
    configuration = service_configuration()
    try:
        state = root.root_state(parent, accounting=True, baseline=baseline, configuration=configuration)
        state_error = None
    except (root.ServiceUnavailable, FileNotFoundError, ProcessLookupError) as exc:
        # Transient service stop/start races must not erase the resource sample.
        state = None
        state_error = type(exc).__name__
    state_monotonic = time.monotonic()
    processes = []
    for process in root.subtree(directory, parent):
        observed = observed_process(process, parent, root.ROOT, root.identity,
                                    baseline=baseline, configuration=configuration)
        if observed is not None:
            processes.append(observed)
    if (executable_identity(Path('/proc/1/exe')) != baseline['manager']
            or any(trusted_system_image(path) != baseline[name] for name, path in SYSTEM_IMAGES.items())):
        raise RuntimeError('Execution baseline changed during sample')
    parent_identity = root.directory_identity(directory)
    limits = verify_limits(directory)
    metric_started = time.monotonic()
    value = metrics(directory)
    metric_finished = time.monotonic()
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {'started_monotonic': started, 'finished_monotonic': time.monotonic(),
            'metrics_started_monotonic': metric_started,
            'metrics_finished_monotonic': metric_finished,
            'parent_identity': parent_identity,
            'limits': limits, 'metrics': value,
            'observer': observer, 'processes': processes, 'launch_baseline': baseline,
            'observer_self_cpu_seconds': own.ru_utime + own.ru_stime,
            'observer_reaped_children_cpu_seconds': children.ru_utime + children.ru_stime,
            'state': state, 'state_error': state_error, 'state_monotonic': state_monotonic}


class Journal:
    """Append once per sample; avoid repeatedly rewriting a growing report."""
    def __init__(self, path, header):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.stream = os.fdopen(fd, 'wb')
        self.byte_count = 0
        self.sample_count = 0
        self.append({'type': 'header', **header})

    def append(self, row):
        data = json.dumps(row, separators=(',', ':'), allow_nan=False).encode() + b'\n'
        limit = MAX_BYTES if row['type'] == 'finished' else MAX_BYTES - END_RESERVE_BYTES
        if len(data) > MAX_ROW_BYTES:
            raise RowBudgetExceeded('Upgrade accounting row budget exhausted')
        if self.byte_count + len(data) > limit:
            raise ByteBudgetExceeded('Upgrade accounting byte budget exhausted')
        if row['type'] == 'sample' and self.sample_count >= MAX_SAMPLES:
            raise SampleBudgetExceeded('Upgrade accounting sample budget exhausted')
        self.stream.write(data)
        self.stream.flush()
        self.byte_count += len(data)
        self.sample_count += row['type'] == 'sample'

    def close(self):
        self.stream.close()


def read_journal(path, uid):
    """Keep complete prefix rows after interruption, but never qualify a suffix."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > MAX_BYTES):
            raise RuntimeError('Unsafe accounting journal')
        data = source.read(MAX_BYTES + 1)
    rows = []
    error = None
    for line in data.splitlines(keepends=True):
        if not line.endswith(b'\n') or len(line) > MAX_ROW_BYTES:
            error = 'incomplete_or_oversized_row'; break
        try:
            row = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            error = 'malformed_row'; break
        rows.append(row)
    return {'rows': rows, 'bytes': len(data), 'read_error': error}


def validate_executable(executable, *, trusted=False):
    if (not isinstance(executable, dict) or executable.keys() != EXE_FIELDS
            or any(type(value) is not int or value < 0 for value in executable.values())
            or executable['inode'] == 0 or not stat.S_ISREG(executable['mode'])
            or (trusted and (executable['uid'] != 0 or executable['mode'] & 0o022))):
        raise RuntimeError('Invalid executable identity')


def validate_baseline(baseline):
    if (not isinstance(baseline, dict) or set(baseline) != {
            'schema_version', 'systemd_version', 'manager_version', 'executor_version', 'manager', 'executor'}
            or type(baseline['schema_version']) is not int
            or baseline['schema_version'] != EXECUTION_SCHEMA_VERSION
            or type(baseline['systemd_version']) is not int or baseline['systemd_version'] != 255
            or not isinstance(baseline['manager_version'], str)
            or len(baseline['manager_version']) > 128
            or not MANAGER_VERSION.fullmatch(baseline['manager_version'])
            or baseline['executor_version'] != baseline['manager_version']):
        raise RuntimeError('Missing supported running manager evidence')
    for image in ('manager', 'executor'):
        validate_executable(baseline[image], trusted=True)
    if baseline['manager'] == baseline['executor']:
        raise RuntimeError('Manager and executor identities are not distinct')


class LaunchTransitions:
    """Finite pending obligations only for the two measured service leaves."""
    def __init__(self, parent, service_uid):
        self.parent = parent
        self.service_uid = service_uid
        self.baseline = None
        self.pending = {}
        self.product_seen = set()
        self.application_seen = set()

    def sample(self, sample, index):
        from scripts import low_resource_root as root
        baseline = sample.get('launch_baseline')
        validate_baseline(baseline)
        if self.baseline is not None and baseline != self.baseline:
            raise RuntimeError('Execution baseline changed across samples')
        self.baseline = baseline
        seen = set()
        records = []
        state = sample['state']
        if state is not None:
            if state.get('observation_error') is not None:
                raise RuntimeError('Accounting state observation failed')
            records += [(process, False, False) for process in state.get('observed_subtree', [])
                        if managed_unit(process, self.parent) is not None]
            for collection in ('services', 'service_transitions'):
                entries = state.get(collection, {})
                if not isinstance(entries, dict) or set(entries) - MANAGED_UNITS.keys():
                    raise RuntimeError('Unknown accounting service')
                if set(state.get('services', {})) & set(state.get('service_transitions', {})):
                    raise RuntimeError('Transition claimed as normal service')
                for unit, entry in entries.items():
                    if (not isinstance(entry, dict) or set(entry) != {'properties', 'process', 'execution'}
                            or set(entry['process']) != {'pid', 'ppid', 'uid', 'starttime_ticks', 'cgroup'}):
                        raise RuntimeError('Unknown accounting service evidence fields')
                    process = entry['process']; properties = entry['properties']
                    if (not isinstance(properties, dict) or set(properties) - {
                            'LoadState', 'ActiveState', 'MainPID', 'Slice', 'ControlGroup',
                            'FragmentPath', 'DropInPaths'}):
                        raise RuntimeError('Unknown accounting unit property fields')
                    group = '/' + self.parent + '/' + unit
                    if (properties.get('Slice') != self.parent
                            or properties.get('ControlGroup') != group
                            or properties.get('DropInPaths', '').split() != [
                                f'/run/systemd/system/{unit}.d/90-vui-resource.conf']
                            or str(properties.get('MainPID')) != str(process['pid'])
                            or process['cgroup'] != group):
                        raise RuntimeError('Accounting service binding changed')
                    normal = collection == 'services'
                    if normal and properties.get('ActiveState') != 'active':
                        raise RuntimeError('Normal accounting service is not active')
                    records.append(({**process, 'role': MANAGED_UNITS[unit][0],
                                     'execution': entry.get('execution')}, normal, True))
        records += [(process, False, False) for process in sample['processes']
                    if managed_unit(process, self.parent) is not None]
        for process, normal, main in records:
            if set(process) != {'pid', 'ppid', 'uid', 'starttime_ticks', 'cgroup', 'role', 'execution'}:
                raise RuntimeError('Unknown managed process evidence fields')
            unit = managed_unit(process, self.parent)
            role, template = MANAGED_UNITS[unit]
            root.validate_process(process, self.parent, leaf=unit)
            if process.get('role') != role:
                raise RuntimeError('Managed process role changed')
            evidence = process.get('execution')
            if (not isinstance(evidence, dict) or set(evidence) != {
                    'schema_version', 'image', 'exe', 'template', 'error'}
                    or type(evidence['schema_version']) is not int
                    or evidence['schema_version'] != EXECUTION_SCHEMA_VERSION
                    or evidence['image'] not in {'systemd-manager', 'systemd-executor', 'runtime', 'other'}
                    or evidence['template'] not in {'panel', 'http01', 'launcher-panel', 'launcher-http01', 'other'}):
                raise RuntimeError('Missing managed execution evidence')
            if evidence['error'] is not None:
                raise RuntimeError('Managed execution observation failed')
            validate_executable(evidence['exe'])
            preexec = evidence['image'] in ('systemd-manager', 'systemd-executor')
            if preexec and (evidence['exe'] != baseline[evidence['image'].removeprefix('systemd-')]
                            or evidence['template'] != 'other'):
                raise RuntimeError('Pre-exec image is not the trusted system image')
            if (evidence['image'] == 'runtime'
                    and evidence['exe'] in (baseline['manager'], baseline['executor'])):
                raise RuntimeError('System image mislabeled as product runtime')
            key = (process['pid'], process['starttime_ticks'], process['cgroup'])
            seen.add(key)
            if preexec and key in self.product_seen:
                raise RuntimeError('Product generation regressed to pre-exec')
            uids = process['uid']
            if uids == [0] * 4:
                if not preexec or process['ppid'] != 1 or normal:
                    raise RuntimeError('Root managed process is not proven pre-exec')
                if key not in self.pending:
                    self.pending[key] = {'started': sample['started_monotonic'], 'samples': set()}
            elif uids != [self.service_uid] * 4:
                raise RuntimeError('Managed process credentials changed')
            if main and not normal and key not in self.pending:
                self.pending[key] = {'started': sample['started_monotonic'], 'samples': set()}
            if not preexec:
                self.product_seen.add(key)
            valid_product = (evidence['image'] == 'runtime' and evidence['template'] == template
                             and uids == [self.service_uid] * 4 and process['ppid'] == 1)
            if key in self.application_seen and not valid_product:
                raise RuntimeError('Application generation regressed from product execution')
            if valid_product:
                self.application_seen.add(key)
            if main and not (preexec or (evidence['image'] == 'runtime'
                    and evidence['template'] in (template, 'launcher-' + template))):
                raise RuntimeError('Service MainPID lacks a known launch template')
            if normal and not valid_product:
                raise RuntimeError('Normal service lacks complete product execution')
            if key in self.pending:
                obligation = self.pending[key]
                if process['ppid'] != 1:
                    raise RuntimeError('Pending service ancestry changed')
                if sample['finished_monotonic'] - obligation['started'] > MAX_GAP_SECONDS:
                    raise RuntimeError('Pending launch exceeded time limit')
                if (evidence['image'] == 'other' or (evidence['image'] == 'runtime'
                        and evidence['template'] not in (template, 'launcher-' + template))):
                    raise RuntimeError('Pending launch has the wrong product template')
                if normal and valid_product:
                    del self.pending[key]
                elif preexec or evidence['template'] == 'launcher-' + template:
                    obligation['samples'].add(index)
                    if len(obligation['samples']) > 2:
                        raise RuntimeError('Pending launch exceeded sample limit')
        for key, obligation in self.pending.items():
            if key not in seen:
                raise RuntimeError('Pending launch disappeared or changed generation')
            obligation['samples'].add(index)
            if (len(obligation['samples']) > 2
                    or sample['finished_monotonic'] - obligation['started'] > MAX_GAP_SECONDS):
                raise RuntimeError('Pending launch did not close within bounds')

    def finish(self):
        if self.pending:
            raise RuntimeError('Accounting ended with an unclosed launch')

def validate(report):
    from scripts import low_resource_root as root
    from scripts.low_resource_acceptance import assert_no_oom
    trace = report.get('upgrade_accounting')
    if not isinstance(trace, dict):
        raise RuntimeError('Missing accounting journal')
    rows = trace['rows']
    if trace['read_error'] is not None or not 0 < trace['bytes'] <= MAX_BYTES:
        raise RuntimeError('Incomplete accounting journal')
    if len(rows) < 4 or rows[0] != {'type': 'header', 'source_commit': report['source_commit'],
            'slice': report['slice'], 'interval_seconds': INTERVAL_SECONDS,
            'max_samples': MAX_SAMPLES, 'max_bytes': MAX_BYTES, 'schema_version': SCHEMA_VERSION}:
        raise RuntimeError('Missing accounting provenance/budget')
    samples = rows[1:-1]; end = rows[-1]
    if (any(row.get('type') != 'sample' for row in samples)
            or not 2 <= len(samples) <= MAX_SAMPLES or end.get('type') != 'finished'
            or end.get('returncode') != 0 or end.get('error') is not None
            or end.get('sample_count') != len(samples)):
        raise RuntimeError('Accounting capture did not finish')
    stages = report['worker']['stages']
    start_stage, end_stage = stages[3], stages[4]
    previous_time = start_stage['monotonic']
    previous_metric_time = previous_time
    previous = start_stage['metrics']
    service_uid = stages[1]['details']['services']['v-ui.service']['process']['uid'][0]
    launches = LaunchTransitions(report['slice'], service_uid)
    for index, row in enumerate(samples):
        sample = row['sample']
        times = [sample.get(key) for key in ('started_monotonic', 'metrics_started_monotonic',
            'metrics_finished_monotonic', 'finished_monotonic')]
        if (any(type(t) not in (int, float) or not math.isfinite(t) for t in times)
                or times != sorted(times) or times[0] < previous_time
                or times[0] - previous_time > MAX_GAP_SECONDS
                or times[2] - previous_metric_time > MAX_GAP_SECONDS
                or times[-1] > end_stage['monotonic'] or times[-1] - times[0] > MAX_GAP_SECONDS):
            raise RuntimeError('Accounting sample gap/order/window invalid')
        if sample['parent_identity'] != report['parent_identity'] or sample['limits'] != report['limits']:
            raise RuntimeError('Accounting parent or limit changed')
        root.validate_process(sample['observer'], report['slice'], uid=0, leaf=report['worker_unit'])
        state_time = sample['state_monotonic']
        if (type(state_time) not in (int, float) or not math.isfinite(state_time)
                or not times[0] <= state_time <= times[1]):
            raise RuntimeError('State observation outside sample window')
        if sample['state_error'] not in (None, 'ServiceUnavailable', 'FileNotFoundError', 'ProcessLookupError'):
            raise RuntimeError('Hard state observation failure')
        if (sample['state'] is None) != (sample['state_error'] is not None):
            raise RuntimeError('State failure status is inconsistent')
        for name in ('observer_self_cpu_seconds', 'observer_reaped_children_cpu_seconds'):
            if type(sample[name]) not in (int, float) or not math.isfinite(sample[name]) or sample[name] < 0:
                raise RuntimeError('Invalid observer CPU')
        processes = sample['processes']
        identities = [process_identity(process) for process in processes]
        if (len({process['pid'] for process in processes}) != len(processes)
                or report['worker']['worker'] not in identities or sample['observer'] not in identities):
            raise RuntimeError('Missing or duplicate live worker/observer identity')
        for process in processes:
            allowed = {'pid', 'ppid', 'uid', 'starttime_ticks', 'cgroup', 'role'}
            if managed_unit(process, report['slice']) is not None:
                allowed.add('execution')
            if set(process) != allowed:
                raise RuntimeError('Unknown observed process evidence fields')
            # sudo legitimately has mixed real/effective UIDs in this same leaf.
            uids = process['uid']
            if len(uids) != 4 or any(type(uid) is not int or uid < 0 for uid in uids):
                raise RuntimeError('Invalid observed UIDs')
            root.validate_process({**process, 'uid': [uids[0]] * 4}, report['slice'])
            if process['role'] not in ROLES:
                raise RuntimeError('Unknown process role')
            role = process['role']
            unit = managed_unit(process, report['slice'])
            if unit is not None:
                if role != MANAGED_UNITS[unit][0]:
                    raise RuntimeError('Managed process was declassified')
            elif role != 'unclassified':
                root.validate_process(process, report['slice'], uid=service_uid, leaf=report['worker_unit'])
            if role in ('managed-panel', 'managed-http01') and unit is None:
                raise RuntimeError('Managed role outside exact service leaf')
        launches.sample(sample, index)
        value = sample['metrics']; root.validate_metrics(value); assert_no_oom(value)
        if (value['memory.peak'] < previous['memory.peak']
                or value['cpu.stat']['usage_usec'] < previous['cpu.stat']['usage_usec']
                or any(value['memory.events'][k] < previous['memory.events'][k]
                       for k in ('max', 'oom', 'oom_kill', 'oom_group_kill'))):
            raise RuntimeError('Accounting cumulative totals regressed')
        previous = value; previous_time = times[-1]; previous_metric_time = times[2]
    if (previous_time > end_stage['monotonic'] or end_stage['monotonic'] - previous_metric_time > MAX_GAP_SECONDS
            or previous['memory.peak'] > end_stage['metrics']['memory.peak']
            or previous['cpu.stat']['usage_usec'] > end_stage['metrics']['cpu.stat']['usage_usec']
            or any(previous['memory.events'][k] > end_stage['metrics']['memory.events'][k]
                   for k in ('max', 'oom', 'oom_kill', 'oom_group_kill'))):
        raise RuntimeError('Accounting exceeds enclosing upgrade')
    launches.finish()
    first, last = samples[0]['sample'], samples[-1]['sample']
    command_times = [end.get('command_started_monotonic'), end.get('command_finished_monotonic')]
    if (any(type(t) not in (int, float) or not math.isfinite(t) for t in command_times)
            or not first['finished_monotonic'] <= command_times[0] < command_times[1] <= last['started_monotonic']):
        raise RuntimeError('Accounting does not enclose the complete command')
    change = end_stage['details']
    old = change['before']['services']['v-ui.service']['process']
    package = report['package']
    live = []
    for row in samples:
        sample = row['sample']; state = sample['state']
        if (state is not None and package['b_release_id'] in state.get('releases', [])
                and package['b_release_id'] not in state.get('ready_releases', [])
                and state.get('current', {}).get('release_id') == package['a_release_id']
                and state.get('services', {}).get('v-ui.service', {}).get('process', {}) == old):
            live.append({**state, 'monotonic': sample['state_monotonic']})
    if not live or live != change['staging_live_samples']:
        raise RuntimeError('Live staging proof is not bound to accounting samples')
