"""Opt-in accounting boundaries around unchanged offline installation calls.

No cache advice, altered call arguments, omitted verification, or extra product
process. Snapshots and checkpoints remain charged to the original service group.
"""
from contextlib import contextmanager
import functools
import hashlib
import math
from pathlib import Path
import subprocess
import time

BASELINE = '098e15a70665520d0c0abfc056e97237237037dc'
PRODUCT_PATHS = ('app', 'web', 'deploy', 'third_party', 'main.py', 'VERSION',
    'install.sh', 'requirements-runtime.txt', 'scripts/deploy.py',
    'scripts/install_system.py', 'scripts/build_bundle.py',
    'scripts/fetch_portable_runtimes.py', 'scripts/fetch_test_cores.py',
    'scripts/prepare_frontend.py', 'scripts/vendor_frontend.py')
PHASES = ('verify_and_unpack_release', 'extract_portable_runtime', 'ensurepip',
          'offline_pip_install', 'pip_check', 'hash_prepared_runtime',
          'health_including_repeat_integrity_checks')


def product_provenance(source: Path, commit: str):
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=source, stderr=subprocess.PIPE)
    if git('rev-parse', 'HEAD').decode().strip() != commit:
        raise RuntimeError('Diagnostic checkout does not match requested source')
    baseline = git('ls-tree', '-r', '--full-tree', BASELINE, '--', *PRODUCT_PATHS)
    current = git('ls-tree', '-r', '--full-tree', commit, '--', *PRODUCT_PATHS)
    if not baseline or baseline != current:
        raise RuntimeError('Product source or execution entry changed from qualified baseline')
    if git('diff', '--name-only', commit, '--', *PRODUCT_PATHS).strip():
        raise RuntimeError('Product working files differ from committed source')
    return dict(baseline_commit=BASELINE, source_commit=commit,
                product_paths=list(PRODUCT_PATHS), tracked_file_count=len(current.splitlines()),
                product_git_entries_sha256=hashlib.sha256(current).hexdigest(),
                product_bytes_unchanged=True,
                scope='tracked product sources, entry points and builder; diagnostic harness and package metadata differ')


@contextmanager
def trace_installation(tools, trace, read_metrics, checkpoint):
    """Wrap only the existing synchronous stage calls and restore on all exits."""
    originals = {}
    depth = 0

    def snapshot():
        value = dict(started_monotonic=time.monotonic())
        value['metrics'] = read_metrics()
        value['finished_monotonic'] = time.monotonic()
        return value

    def invoke(name, action, args, kwargs):
        nonlocal depth
        if depth:
            return action(*args, **kwargs)  # Health includes its repeated hashes.
        row = dict(name=name, outcome='running', started_monotonic=time.monotonic())
        trace['phases'].append(row)
        row['before'] = snapshot()
        checkpoint()
        depth += 1
        try:
            result = action(*args, **kwargs)
        except BaseException:
            row['outcome'] = 'failed'
            try:
                row['after'] = snapshot()
                row['finished_monotonic'] = time.monotonic()
                checkpoint()
            except Exception as exc:
                # Accounting failures must not conceal the product exception.
                row['accounting_error'] = type(exc).__name__
            raise
        else:
            row['after'] = snapshot()
            row['finished_monotonic'] = time.monotonic()
            row['outcome'] = 'passed'
            checkpoint()
            return result
        finally:
            depth -= 1

    def patch_function(name, phase):
        original = getattr(tools, name)
        originals[name] = original
        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            return invoke(phase, original, args, kwargs)
        setattr(tools, name, wrapped)

    original_run = tools.subprocess.run
    def run(*args, **kwargs):
        command = args[0] if args else kwargs.get('args')
        phase = None
        if isinstance(command, (list, tuple)):
            tail = list(command[1:])
            if tail == ['-m', 'ensurepip', '--upgrade']:
                phase = 'ensurepip'
            elif tail[:5] == ['-m', 'pip', '--isolated', '--disable-pip-version-check', 'install']:
                phase = 'offline_pip_install'
            elif tail == ['-m', 'pip', '--isolated', 'check']:
                phase = 'pip_check'
        return invoke(phase, original_run, args, kwargs) if phase else original_run(*args, **kwargs)

    try:
        patch_function('unpack_verified', PHASES[0])
        patch_function('extract_runtime', PHASES[1])
        patch_function('runtime_tree_digest', PHASES[5])
        patch_function('health_check', PHASES[6])
        tools.subprocess.run = run
        yield
    finally:
        tools.subprocess.run = original_run
        for name, original in originals.items():
            setattr(tools, name, original)


def validate_trace(report, unit, commit, expected_provenance):
    from scripts.low_resource_certificates import validate_metrics_record
    from scripts.low_resource_service_tree import canonical_cgroup
    value = report.get('installation_trace', {})
    if (report.get('duration_profile') != 'smoke' or report.get('requested_memory_mib') not in (320, 512)
            or value.get('unit') != unit or value.get('source_commit') != commit
            or value.get('service_cgroup') != report.get('service_cgroup')
            or value.get('product_provenance') != expected_provenance):
        raise RuntimeError('Installation trace provenance or profile mismatch')
    canonical_cgroup(value.get('service_cgroup'), unit)
    stages = [s for s in report.get('stages', []) if s.get('name') == 'offline_stage_including_wheels']
    if len(stages) != 1 or stages[0].get('outcome') != 'passed':
        raise RuntimeError('Missing enclosing offline stage')
    stage = stages[0]
    rows = value.get('phases')
    if not isinstance(rows, list) or [r.get('name') for r in rows] != list(PHASES):
        raise RuntimeError('Installation phase coverage or order changed')
    def finite(number):
        return type(number) in (int, float) and math.isfinite(number) and number >= 0
    start, wall = stage.get('started_monotonic'), stage.get('wall_seconds')
    if not finite(start) or not finite(wall):
        raise RuntimeError('Missing enclosing installation clock')
    previous_time, previous_metric = start, None
    first_cpu = None
    for row in rows:
        if row.get('outcome') != 'passed' or 'accounting_error' in row:
            raise RuntimeError('Incomplete installation phase')
        before, after = row.get('before', {}), row.get('after', {})
        times = [row.get('started_monotonic'), before.get('started_monotonic'), before.get('finished_monotonic'),
                 after.get('started_monotonic'), after.get('finished_monotonic'), row.get('finished_monotonic')]
        if any(not finite(t) for t in times) or times != sorted(times) or times[0] < previous_time or times[-1] > start + wall:
            raise RuntimeError('Installation boundaries outside enclosing stage or out of order')
        for snap in (before, after):
            metric = snap.get('metrics'); validate_metrics_record(metric)
            if metric['memory.peak'] > stage['memory_peak_bytes']:
                raise RuntimeError('Installation boundary peak exceeds enclosing stage')
            for key in ('max', 'oom', 'oom_kill', 'oom_group_kill'):
                if metric['memory.events'][key] > stage['memory_events'][key]:
                    raise RuntimeError('Installation boundary events exceed enclosing stage')
            if previous_metric is not None and (
                    metric['memory.peak'] < previous_metric['memory.peak']
                    or metric['cpu.stat']['usage_usec'] < previous_metric['cpu.stat']['usage_usec']
                    or any(metric['memory.events'][k] < previous_metric['memory.events'][k]
                           for k in ('max', 'oom', 'oom_kill', 'oom_group_kill'))):
                raise RuntimeError('Installation cumulative counters decreased')
            if first_cpu is None:
                first_cpu = metric['cpu.stat']['usage_usec']
            previous_metric = metric
        previous_time = times[-1]
    if previous_metric['cpu.stat']['usage_usec'] - first_cpu > stage['cpu_usage_usec']:
        raise RuntimeError('Installation phase CPU exceeds enclosing stage')
