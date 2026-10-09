"""Read-only, inclusive process/cgroup accounting for warm idle diagnostics.

PSS is proportional mapped memory, not a cgroup charge reconciliation. No
process is moved, cache dropped, or component subtracted from the hard budget.
"""
from pathlib import Path
import math
import time

FIELDS = ('Rss', 'Pss', 'Pss_Anon', 'Pss_File', 'Pss_Shmem',
          'Private_Clean', 'Private_Dirty', 'Private_Hugetlb',
          'Shared_Clean', 'Shared_Dirty', 'Shared_Hugetlb', 'Swap', 'SwapPss')


def parse_rollup(text):
    result = {}
    for line in text.splitlines():
        key, sep, value = line.partition(':')
        if key not in FIELDS:
            continue
        parts = value.split()
        if key in result or not sep or len(parts) != 2 or parts[1] != 'kB' or not parts[0].isdigit():
            raise RuntimeError('Invalid smaps_rollup accounting field')
        result[key] = int(parts[0]) * 1024
    if set(result) != set(FIELDS):
        raise RuntimeError('Incomplete smaps_rollup accounting')
    result['private_bytes'] = sum(result[key] for key in ('Private_Clean', 'Private_Dirty', 'Private_Hugetlb'))
    return result


def start_ticks(text):
    # comm can contain spaces and parentheses; fields after the last ')' start
    # at field 3, while starttime is field 22.
    try:
        return int(text.rsplit(')', 1)[1].split()[19])
    except (IndexError, ValueError):
        raise RuntimeError('Invalid process identity') from None


def members(directory):
    result = {}
    for path in [directory / 'cgroup.procs', *sorted(directory.rglob('cgroup.procs'))]:
        for value in path.read_text().split():
            pid = int(value)
            if pid in result:
                if result[pid] != str(path.parent):
                    raise RuntimeError('Process moved during membership scan')
                continue
            result[pid] = str(path.parent)
    return result


def snapshot(directory, roles, read_metrics, proc_root=Path('/proc')):
    started = time.monotonic()
    before = members(directory)
    if len(set(roles.values())) != len(roles) or not set(roles.values()) <= set(before):
        raise RuntimeError('Required process missing from inclusive service cgroup')
    metrics_before = read_metrics(directory)
    rows = []
    for pid, group in sorted(before.items()):
        proc = proc_root / str(pid)
        identity = start_ticks((proc / 'stat').read_text())
        membership = (proc / 'cgroup').read_text()
        row = {'pid': pid, 'start_ticks': identity, 'cgroup': group,
               'role': next((role for role, value in roles.items() if value == pid), 'other'),
               'read_started_monotonic': time.monotonic(),
               'membership': membership,
               'memory_bytes': parse_rollup((proc / 'smaps_rollup').read_text())}
        if membership != (proc / 'cgroup').read_text() or identity != start_ticks((proc / 'stat').read_text()):
            raise RuntimeError('Process identity changed during accounting')
        row['read_finished_monotonic'] = time.monotonic()
        rows.append(row)
    after = members(directory)
    if before != after:
        raise RuntimeError('Cgroup membership changed during accounting')
    for row in rows:
        if row['membership'] != (proc_root / str(row['pid']) / 'cgroup').read_text() or row['start_ticks'] != start_ticks((proc_root / str(row['pid']) / 'stat').read_text()):
            raise RuntimeError('Process identity changed across snapshot')
    metrics_after = read_metrics(directory)
    return {'started_monotonic': started, 'finished_monotonic': time.monotonic(),
            'members_before': {str(pid): group for pid, group in before.items()},
            'members_after': {str(pid): group for pid, group in after.items()},
            'metrics_before': metrics_before, 'metrics_after': metrics_after, 'processes': rows}


def validate_stage(value, expected_roles, *, seconds=60, interval=5):
    if (seconds, interval) not in ((60, 5), (1800, 30)):
        raise RuntimeError('Unsupported accounting contract')
    wall = value.get('wall_seconds')
    if value.get('outcome') != 'passed' or type(wall) not in (int, float) or not math.isfinite(wall) or wall < seconds:
        raise RuntimeError('Accounting idle did not complete contracted duration')
    expected_pids = value.get('accounting_roles', {})
    if set(expected_pids) != set(expected_roles) or any(type(pid) is not int or pid <= 0 for pid in expected_pids.values()) or len(set(expected_pids.values())) != len(expected_roles):
        raise RuntimeError('Missing declared accounting identities')
    samples = value.get('accounting_samples', [])
    if len(samples) != seconds // interval + 1:
        raise RuntimeError('Accounting idle samples missing')
    group_root = Path(value.get('accounting_cgroup_root', ''))
    if not group_root.is_relative_to('/sys/fs/cgroup') or group_root == Path('/sys/fs/cgroup'):
        raise RuntimeError('Invalid service cgroup root')
    cumulative = None
    identities = None
    previous = None
    origin = value.get('accounting_started_monotonic')
    if type(origin) not in (int, float) or not math.isfinite(origin):
        raise RuntimeError('Missing accounting origin')
    for index, sample in enumerate(samples):
        begin, end = sample['started_monotonic'], sample['finished_monotonic']
        if not all(type(n) in (int, float) and math.isfinite(n) for n in (begin, end)) or end < begin or (previous is not None and begin < previous):
            raise RuntimeError('Invalid accounting sampling window')
        planned = origin + index * interval
        if sample.get('planned_monotonic') != planned or not planned <= begin <= end < planned + interval:
            raise RuntimeError('Missing or late accounting slot')
        previous = end
        rows = sample['processes']
        recorded = {str(row['pid']): row['cgroup'] for row in rows}
        if sample.get('members_before') != recorded or sample.get('members_after') != recorded:
            raise RuntimeError('Process accounting omitted or changed members')
        roles = [row['role'] for row in rows if row['role'] != 'other']
        if sorted(roles) != sorted(expected_roles) or len({row['pid'] for row in rows}) != len(rows):
            raise RuntimeError('Accounting roles incomplete')
        current = {(row['pid'], row['start_ticks'], row['role'], row['cgroup'], row['membership']) for row in rows}
        if identities is not None and current != identities:
            raise RuntimeError('Idle process membership or identity changed')
        identities = current
        for row in rows:
            if (type(row['pid']) is not int or row['pid'] <= 0 or type(row['start_ticks']) is not int or row['start_ticks'] <= 0 or not isinstance(row['cgroup'], str) or not row['cgroup'].startswith('/')):
                raise RuntimeError('Invalid process identity record')
            group = Path(row['cgroup'])
            membership = row['membership'].splitlines()
            unified = [line.split(':', 2)[2] for line in membership if line.startswith('0::')]
            if len(unified) != 1 or not unified[0].startswith('/') or '..' in Path(unified[0]).parts or group != Path('/sys/fs/cgroup') / unified[0].lstrip('/') or not group.is_relative_to(group_root):
                raise RuntimeError('Process membership is outside measured cgroup')
            if row['role'] != 'other' and expected_pids[row['role']] != row['pid']:
                raise RuntimeError('Process role identity mismatch')
            if not begin <= row['read_started_monotonic'] <= row['read_finished_monotonic'] <= end:
                raise RuntimeError('Invalid process read window')
            memory = row['memory_bytes']
            if set(memory) != set(FIELDS) | {'private_bytes'} or any(type(n) is not int or n < 0 for n in memory.values()):
                raise RuntimeError('Incomplete process memory accounting')
            if memory['private_bytes'] != sum(memory[key] for key in ('Private_Clean', 'Private_Dirty', 'Private_Hugetlb')):
                raise RuntimeError('Invalid private memory total')
        from scripts.low_resource_certificates import validate_metrics_record
        for metric in (sample['metrics_before'], sample['metrics_after']):
            validate_metrics_record(metric)
            if type(metric['memory.stat'].get('kernel')) is not int or metric['memory.stat']['kernel'] < 0:
                raise RuntimeError('Missing kernel memory accounting')
            counters = [metric['memory.peak'], metric['cpu.stat']['usage_usec'], *[metric['memory.events'][key] for key in ('max', 'oom', 'oom_kill', 'oom_group_kill')]]
            if cumulative is not None and any(new < old for new, old in zip(counters, cumulative)):
                raise RuntimeError('Cumulative cgroup accounting moved backwards')
            cumulative = counters
    if samples[-1]['started_monotonic'] < origin + seconds:
        raise RuntimeError('Accounting samples do not cover idle window')


def validate_complete(report, *, seconds=60, interval=5):
    stages = report.get('stages', [])
    panel_identities = None
    for name, roles in [(f'panel_only_idle_{seconds}_seconds', ('worker', 'panel')),
                        (f'panel_single_proxy_idle_{seconds}_seconds', ('worker', 'panel', 'core', 'watchdog'))]:
        selected = [stage for stage in stages if stage.get('name') == name]
        if len(selected) != 1:
            raise RuntimeError('Accounting idle stage missing or duplicated')
        from scripts.low_resource_service_tree import canonical_cgroup
        expected_root = canonical_cgroup(report.get('service_cgroup'), report.get('unit'))
        if selected[0].get('accounting_cgroup_root') != expected_root:
            raise RuntimeError('Accounting root does not match worker unit')
        validate_stage(selected[0], roles, seconds=seconds, interval=interval)
        current = {(row['role'], row['pid'], row['start_ticks']) for row in selected[0]['accounting_samples'][0]['processes'] if row['role'] in ('worker', 'panel')}
        if selected[0]['accounting_roles']['worker'] != report.get('worker_pid'):
            raise RuntimeError('Accounting worker differs from report identity')
        if panel_identities is not None and panel_identities != current:
            raise RuntimeError('Worker or panel changed between idle phases')
        panel_identities = current
