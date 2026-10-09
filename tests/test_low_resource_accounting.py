"""Inclusive memory diagnostics reject missing members, identities and evidence."""
import argparse
import copy
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import low_resource_accounting as accounting
from scripts import low_resource_acceptance as gate


def rollup():
    return '\n'.join(f'{key}: 4 kB' for key in accounting.FIELDS)


def metric():
    return {'memory.current': 100, 'memory.peak': 200,
            'memory.events': dict(oom=0, oom_kill=0, oom_group_kill=0, max=0),
            'memory.stat': dict(anon=50, file=40, kernel=10), 'cpu.stat': dict(usage_usec=100)}


def stage():
    samples = []
    for i in range(13):
        begin = 100 + i * 5
        samples.append(dict(planned_monotonic=begin, started_monotonic=begin, finished_monotonic=begin + .1,
                            metrics_before=metric(), metrics_after=metric(),
                            members_before={str(n):'/sys/fs/cgroup/test' for n in (1,2,3)},
                            members_after={str(n):'/sys/fs/cgroup/test' for n in (1,2,3)},
                            processes=[dict(pid=n, start_ticks=n * 10, role=role, cgroup='/sys/fs/cgroup/test', membership='0::/test\n',
                                            read_started_monotonic=begin, read_finished_monotonic=begin+.05,
                                            memory_bytes=accounting.parse_rollup(rollup()))
                                       for n, role in ((1, 'worker'), (2, 'panel'), (3, 'other'))]))
    return dict(accounting_cgroup_root='/sys/fs/cgroup/test', accounting_started_monotonic=100, outcome='passed', wall_seconds=60.01, accounting_roles=dict(worker=1, panel=2), accounting_samples=samples)


class AccountingTests(unittest.TestCase):
    def test_parser_exact_units_missing_duplicate_and_process_comm(self):
        parsed = accounting.parse_rollup(rollup())
        self.assertEqual(parsed['Pss'], 4096)
        self.assertEqual(parsed['private_bytes'], 12288)
        for text in (rollup().replace('Pss: 4 kB', ''), rollup()+'\nPss: 4 kB', rollup().replace('kB','MB'), rollup().replace('4 kB','-1 kB')):
            with self.assertRaises(RuntimeError): accounting.parse_rollup(text)
        self.assertEqual(accounting.start_ticks('1 (comm with ) spaces) S '+' '.join(['0']*18+['123','0'])),123)

    def test_inclusive_descendants_and_real_self_rollup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); (root/'cgroup.procs').write_text(str(os.getpid()))
            (root/'child').mkdir(); (root/'child'/'cgroup.procs').write_text('')
            actual=accounting.snapshot(root, {'worker':os.getpid()},lambda _:metric())
            self.assertGreater(actual['processes'][0]['memory_bytes']['Rss'],0)
            (root/'child'/'cgroup.procs').write_text('987654')
            self.assertIn(987654,accounting.members(root))
            with self.assertRaises(FileNotFoundError): accounting.snapshot(root,{'worker':os.getpid()},lambda _:metric())
            with self.assertRaises(RuntimeError): accounting.snapshot(root,{'panel':123456},lambda _:metric())

    def test_real_child_and_permission_failure_are_not_omitted(self):
        child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary)
                (root/'cgroup.procs').write_text(f'{os.getpid()}\n{child.pid}')
                sample=accounting.snapshot(root,{'worker':os.getpid(),'panel':child.pid},lambda _:metric())
                self.assertEqual({row['pid'] for row in sample['processes']},{os.getpid(),child.pid})
                original=Path.read_text
                def read(path,*args,**kwargs):
                    if path.name=='smaps_rollup':raise PermissionError('denied')
                    return original(path,*args,**kwargs)
                with patch.object(Path,'read_text',read):
                    with self.assertRaises(PermissionError):accounting.snapshot(root,{'worker':os.getpid()},lambda _:metric())
        finally:
            child.terminate();child.wait(timeout=5)

    def test_membership_change_and_pid_reuse_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); (root/'cgroup.procs').write_text(str(os.getpid()))
            with patch.object(accounting,'members',side_effect=[{os.getpid():str(root)},{}]):
                with self.assertRaises(RuntimeError):accounting.snapshot(root,{'worker':os.getpid()},lambda _:metric())
            with patch.object(accounting,'start_ticks',side_effect=[1,2]):
                with self.assertRaises(RuntimeError):accounting.snapshot(root,{'worker':os.getpid()},lambda _:metric())

    def test_complete_stage_and_corrupt_evidence(self):
        value=stage(); accounting.validate_stage(value,('worker','panel'))
        for mutate in (
            lambda v:v.update(wall_seconds=59),
            lambda v:v.update(wall_seconds=float('nan')),
            lambda v:[sample['processes'].pop() for sample in v['accounting_samples']],
            lambda v:v['accounting_samples'][1]['metrics_after']['cpu.stat'].update(usage_usec=1),
            lambda v:v['accounting_samples'][1]['metrics_after'].update(**{'memory.peak':100}),
            lambda v:[row.update(membership='0::/wrong') for sample in v['accounting_samples'] for row in sample['processes']],
            lambda v:v['accounting_samples'].pop(),
            lambda v:v['accounting_roles'].update(panel=42),
            lambda v:v['accounting_samples'][1]['processes'].pop(),
            lambda v:v['accounting_samples'][1]['processes'][0].update(start_ticks=99),
            lambda v:v['accounting_samples'][0]['processes'][0]['memory_bytes'].pop('Pss'),
            lambda v:v['accounting_samples'][0]['metrics_before']['memory.events'].update(oom=1),
            lambda v:v['accounting_samples'][0].update(finished_monotonic=float('nan')),
        ):
            bad=copy.deepcopy(value);mutate(bad)
            with self.assertRaises(RuntimeError):accounting.validate_stage(bad,('worker','panel'))

    def test_first_slot_jitter_does_not_shorten_planned_observation(self):
        value=stage()
        first=value['accounting_samples'][0]
        first['started_monotonic']+=.2
        first['finished_monotonic']+=.2
        for row in first['processes']:
            row['read_started_monotonic']+=.2
            row['read_finished_monotonic']+=.2
        accounting.validate_stage(value,('worker','panel'))
        for mutate in (
            lambda v:v['accounting_samples'][1].update(planned_monotonic=101),
            lambda v:v['accounting_samples'][1].update(finished_monotonic=111),
            lambda v:v['accounting_samples'][1]['processes'][2].update(membership='0::/elsewhere'),
        ):
            bad=copy.deepcopy(value);mutate(bad)
            with self.assertRaises(RuntimeError):accounting.validate_stage(bad,('worker','panel'))

    def test_explicit_profile_unchanged_limits(self):
        args=argparse.Namespace(duration_profile='accounting',memory_mib=512)
        self.assertEqual(gate.runtime_seconds(args),900)
        for memory in (320,384):
            args.memory_mib=memory
            with self.assertRaises(RuntimeError):gate.runtime_seconds(args)
        with self.assertRaises(RuntimeError):accounting.validate_complete({'stages':[]})
        panel=stage();panel['name']='panel_only_idle_60_seconds'
        core=copy.deepcopy(panel);core['name']='panel_single_proxy_idle_60_seconds'
        core['accounting_roles']['core']=4
        for sample in core['accounting_samples']:
            row=copy.deepcopy(sample['processes'][0]);row.update(pid=4,start_ticks=40,role='core')
            sample['processes'].append(row)
            sample['members_before']['4']=sample['members_after']['4']='/sys/fs/cgroup/test'
        report={'unit':'test','stages':[panel,core]}
        accounting.validate_complete(report)
        for sample in core['accounting_samples']:
            sample['processes'][1]['start_ticks']=42
        with self.assertRaises(RuntimeError):accounting.validate_complete(report)
