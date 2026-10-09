"""Resource-fixture use of the installed product watchdog and inherited policy.

The fixture worker is the watchdog's parent. This exercises the real installed
watchdog/core pair; it is not a claim that the panel API applied this config.
"""
import os
import math
from pathlib import Path
import re
import subprocess
import time

from scripts.low_resource_accounting import start_ticks


def allocator_fields(environment):
    threshold = environment.get('MALLOC_MMAP_THRESHOLD_')
    if threshold is not None and (not re.fullmatch(r'[0-9]+', threshold)
                                  or int(threshold) > 2 ** 64 - 1):
        raise RuntimeError('Invalid allocator threshold')
    return {'mmap_threshold': threshold,
            'malloc_tunable_present': any(part.startswith('glibc.malloc.')
                for part in environment.get('GLIBC_TUNABLES', '').split(':'))}


def observed_allocator(pid):
    # Never serialize the environment: it may contain unrelated credentials.
    with Path(f'/proc/{pid}/environ').open('rb') as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise RuntimeError('Service environment exceeded observation bound')
    selected = {}
    for entry in raw.split(b'\0'):
        key, separator, value = entry.partition(b'=')
        if separator and key in (b'MALLOC_MMAP_THRESHOLD_', b'GLIBC_TUNABLES'):
            selected[key.decode('ascii')] = value.decode('ascii')
    return allocator_fields(selected)


def identity(pid):
    proc = Path(f'/proc/{pid}')
    stat = (proc / 'stat').read_text()
    fields = stat.rsplit(')', 1)[1].split()
    value = dict(pid=pid, ppid=int(fields[1]), starttime_ticks=start_ticks(stat),
                 cgroup=(proc / 'cgroup').read_text().strip(), process_group=os.getpgid(pid))
    if start_ticks((proc / 'stat').read_text()) != value['starttime_ticks']:
        raise RuntimeError('Service process identity changed')
    return value


def canonical_cgroup(value, unit):
    if not isinstance(value, str) or not re.fullmatch(r'0::/[^\n]+', value):
        raise RuntimeError('Invalid unified service cgroup')
    path = value[3:]
    if any(part in ('', '.', '..') for part in path[1:].split('/')) or Path(path).name != unit:
        raise RuntimeError('Service cgroup does not match unit')
    return '/sys/fs/cgroup' + path


class WatchedCore:
    def __init__(self, stack, python, payload, binary, config, log_path, environment, runtime_key):
        self.python, self.payload, self.binary = Path(python), Path(payload), Path(binary)
        self.config, self.log_path = Path(config), Path(log_path)
        self.environment, self.runtime_key = environment, runtime_key
        self.policy = allocator_fields(environment)
        if self.policy['malloc_tunable_present']:
            raise RuntimeError('Conflicting allocator tunable in fixture')
        self.process = None
        self.roles = {}
        self.cleanup_complete = False
        self.log = stack.enter_context(self.log_path.open('w'))
        stack.callback(self.close)

    def start(self, port):
        import psutil
        command = [str(self.python), str(self.payload / 'app/services/core_child.py'),
                   str(os.getpid()), str(self.binary), 'run', '-c', str(self.config)]
        self.process = subprocess.Popen(command, cwd=self.payload, env=self.environment,
            stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('Installed watchdog exited before core readiness')
            children = psutil.Process(self.process.pid).children()
            if len(children) == 1:
                core = children[0]
                if core.cmdline() != command[3:]:
                    raise RuntimeError('Unexpected watchdog child command')
                if any(s.status == psutil.CONN_LISTEN and s.laddr.port == port
                       for s in core.net_connections(kind='inet') if s.laddr):
                    self.roles = {'watchdog': identity(self.process.pid), 'core': identity(core.pid)}
                    for row in self.roles.values():
                        row['allocator'] = observed_allocator(row['pid'])
                    self.check()
                    return
            time.sleep(.05)
        raise RuntimeError('Installed core listener readiness deadline exceeded')

    @property
    def core_pid(self):
        return self.roles['core']['pid']

    def check(self):
        if self.process is None or self.process.poll() is not None or not self.roles:
            raise RuntimeError('Installed watchdog/core unavailable')
        parent = identity(os.getpid())
        for name, expected in self.roles.items():
            current = identity(expected['pid'])
            if current != {key: val for key, val in expected.items() if key != 'allocator'}:
                raise RuntimeError('Service process identity changed')
            if observed_allocator(expected['pid']) != self.policy:
                raise RuntimeError('Service allocator inheritance mismatch')
            if current['cgroup'] != parent['cgroup'] or current['process_group'] != self.process.pid:
                raise RuntimeError('Service escaped worker accounting or process group')
            if current['ppid'] != (os.getpid() if name == 'watchdog' else self.process.pid):
                raise RuntimeError('Service ancestry mismatch')

    def close(self):
        if self.process is not None:
            from scripts.low_resource_sustained import cleanup_process_group
            cleanup_process_group(self.process)
        self.cleanup_complete = True

    def evidence(self):
        return dict(parent_role='resource_fixture_worker', worker_pid=os.getpid(),
                    runtime_key=self.runtime_key, policy=self.policy, roles=self.roles,
                    cleanup_complete=self.cleanup_complete)


def validate_tree(value, runtime_key, worker_pid, service_cgroup):
    from deploy.system_launcher import panel_environment
    expected = allocator_fields(panel_environment({}, runtime_key))
    if value.get('runtime_key') != runtime_key or value.get('policy') != expected:
        raise RuntimeError('Service allocator policy or platform mismatch')
    if value.get('parent_role') != 'resource_fixture_worker' or value.get('worker_pid') != worker_pid:
        raise RuntimeError('Service parent evidence mismatch')
    if value.get('cleanup_complete') is not True or set(value.get('roles', {})) != {'watchdog', 'core'}:
        raise RuntimeError('Incomplete watched service tree')
    watchdog, core = (value['roles'][name] for name in ('watchdog', 'core'))
    for row, parent in ((watchdog, worker_pid), (core, watchdog.get('pid'))):
        if any(type(row.get(key)) is not int or row[key] <= 0 for key in ('pid','ppid','starttime_ticks','process_group')):
            raise RuntimeError('Invalid service process identity')
        if (row['ppid'] != parent or row['cgroup'] != service_cgroup
                or row['process_group'] != watchdog['pid'] or row.get('allocator') != expected):
            raise RuntimeError('Service ancestry, accounting or allocator mismatch')
    if len({worker_pid, watchdog['pid'], core['pid']}) != 3:
        raise RuntimeError('Service process roles overlap')


def validate_sample_bindings(report):
    """Tie phase samples to the independently declared worker and watched pair."""
    group = report['service_cgroup']
    tree = report['proxy_workload']['server_tree']
    roles = tree['roles']
    idle = [row for row in report.get('stages', []) if row.get('name')=='panel_only_idle_1800_seconds']
    idle_roles = {}
    if report.get('duration_profile')=='sustained':
        if len(idle)!=1 or not idle[0].get('accounting_samples'):
            raise RuntimeError('Missing stable panel/worker identity baseline')
        idle_roles = {row['role']:dict(pid=row['pid'],starttime_ticks=row['start_ticks'])
            for row in idle[0]['accounting_samples'][0]['processes'] if row['role'] in ('worker','panel')}
        if set(idle_roles)!={'worker','panel'} or idle_roles['worker']['pid']!=report['worker_pid']:
            raise RuntimeError('Invalid panel/worker identity baseline')
    for stage in report.get('stages', []):
        account = stage.get('accounting_samples')
        if account:
            for sample in account:
                expected = {'worker': {'pid': report['worker_pid']}}
                if 'single_proxy_idle' in stage['name']:
                    expected.update(roles)
                for role, binding in expected.items():
                    rows = [row for row in sample['processes'] if row['role'] == role]
                    if len(rows) != 1 or rows[0]['pid'] != binding['pid']:
                        raise RuntimeError('Accounting role differs from actual service tree')
                    if 'starttime_ticks' in binding and rows[0]['start_ticks'] != binding['starttime_ticks']:
                        raise RuntimeError('Accounting service process restarted')
        if report.get('duration_profile') == 'sustained' and (
                'single_proxy_idle' in stage['name'] or stage['name'].startswith('proxy_sustained_')):
            samples = stage.get('samples', [])
            if len(samples) < (60 if 'idle' in stage['name'] else 20):
                raise RuntimeError('Watched service duration samples missing')
            phase_start=stage.get('started_monotonic');wall=stage.get('wall_seconds')
            stamps=[sample.get('observed_monotonic') for sample in samples]
            if (any(type(value) not in (int,float) or not math.isfinite(value) for value in (phase_start,wall,*stamps))
                    or wall<=0 or any(b<=a or b-a>35 for a,b in zip(stamps,stamps[1:]))
                    or stamps[0]<phase_start or stamps[-1]>phase_start+wall):
                raise RuntimeError('Invalid watched service sampling timeline')
            if stage['name'].startswith('proxy_sustained_'):
                count=int(stage['name'].split('_')[2])
                selected=[row for row in report.get('sustained_load',[]) if row.get('concurrency')==count]
                if len(selected)!=1:raise RuntimeError('Missing actual load timing')
                load=selected[0];start=load.get('partial_load',{}).get('started_monotonic');duration=load.get('wall_seconds')
                if (any(type(value) not in (int,float) or not math.isfinite(value) for value in (start,duration))
                        or duration<600 or start<phase_start or start+duration>phase_start+wall):
                    raise RuntimeError('Load timing escaped service stage')
                inside=[stamp for stamp in stamps if start<=stamp<=start+duration]
                if len(inside)<19 or inside[0]>start+35 or inside[-1]<start+duration-35:
                    raise RuntimeError('Service samples do not span actual load')
            from scripts.low_resource_certificates import validate_metrics_record
            previous_counters=None
            for sample in samples:
                validate_metrics_record(sample)
                counters=[sample['memory.peak'],sample['cpu.stat']['usage_usec'],
                          *[sample['memory.events'][name] for name in ('max','oom','oom_kill','oom_group_kill')]]
                if previous_counters is not None and any(new<old for new,old in zip(counters,previous_counters)):
                    raise RuntimeError('Service cumulative metrics moved backwards')
                previous_counters=counters
                for binding in {**idle_roles,**roles}.values():
                    rows = [row for row in sample['processes'] if row['pid'] == binding['pid']]
                    if len(rows) != 1 or rows[0].get('starttime_ticks') != binding['starttime_ticks']:
                        raise RuntimeError('Watched service disappeared or restarted during load')
    if report.get('duration_profile') == 'certificates':
        declared = report.get('certificate_service_roles', {})
        if declared.get('worker',{}).get('pid') != report.get('worker_pid'):
            raise RuntimeError('Certificate sampled worker differs from actual parent')
        for role, binding in (('proxy', roles['core']), ('watchdog', roles['watchdog'])):
            if declared.get(role) != {key: binding[key] for key in ('pid', 'starttime_ticks')}:
                raise RuntimeError('Certificate service roles differ from watched core')

        service = report.get('certificate_overlap', {}).get('service', {})
        manager = service.get('manager_identity', {})
        if manager.get('ppid') != report.get('worker_pid') or manager.get('cgroup') != group:
            raise RuntimeError('Certificate manager outside worker accounting tree')
        for name, binding in (('manager', manager), ('responder', service.get('responder', {}).get('identity', {}))):
            if declared.get(name) != {key:binding.get(key) for key in ('pid','starttime_ticks')}:
                raise RuntimeError('Certificate sampled roles differ from actual service identity')

        if report.get('certificate_manager_thread') != service.get('manager_thread'):
            raise RuntimeError('Certificate sampled thread differs from actual background worker')
