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


def observed_process(process, parent, root, identity, proc=Path('/proc')):
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
    try:
        state = root.root_state(parent)
        state_error = None
    except (root.ServiceUnavailable, FileNotFoundError, ProcessLookupError) as exc:
        # Transient service stop/start races must not erase the resource sample.
        state = None
        state_error = type(exc).__name__
    state_monotonic = time.monotonic()
    processes = []
    for process in root.subtree(directory, parent):
        observed = observed_process(process, parent, root.ROOT, root.identity)
        if observed is not None:
            processes.append(observed)
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
            'observer': observer, 'processes': processes,
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
            'max_samples': MAX_SAMPLES, 'max_bytes': MAX_BYTES}:
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
    for row in samples:
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
        identities = [{key: value for key, value in process.items() if key != 'role'} for process in processes]
        if (len({process['pid'] for process in processes}) != len(processes)
                or report['worker']['worker'] not in identities or sample['observer'] not in identities):
            raise RuntimeError('Missing or duplicate live worker/observer identity')
        for process in processes:
            # sudo legitimately has mixed real/effective UIDs in this same leaf.
            uids = process['uid']
            if len(uids) != 4 or any(type(uid) is not int or uid < 0 for uid in uids):
                raise RuntimeError('Invalid observed UIDs')
            root.validate_process({**process, 'uid': [uids[0]] * 4}, report['slice'])
            if process['role'] not in ROLES:
                raise RuntimeError('Unknown process role')
            role = process['role']
            if role != 'unclassified':
                leaf = {'managed-panel': 'v-ui.service', 'managed-http01': 'v-ui-http01.service'}.get(role, report['worker_unit'])
                root.validate_process(process, report['slice'], uid=service_uid, leaf=leaf)
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
