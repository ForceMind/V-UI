import copy
from contextlib import ExitStack
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.low_resource_service_tree import (WatchedCore, allocator_fields,
    canonical_cgroup, identity, observed_allocator, validate_tree)
from deploy.system_launcher import panel_environment


class ServiceTreeTests(unittest.TestCase):
    def test_real_installed_watchdog_inherits_policy_and_cleans_group(self):
        self.exercise_watchdog(0)

    def test_preexec_child_is_not_mistaken_for_a_ready_wrong_core(self):
        self.exercise_watchdog(.4)

    def exercise_watchdog(self, preexec_delay):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'app/services').mkdir(parents=True)
            shutil.copy(Path(__file__).resolve().parents[1] / 'app/services/core_child.py',
                        root / 'app/services/core_child.py')
            if preexec_delay:
                child=root/'app/services/core_child.py'
                child.write_text(child.read_text().replace('def parent_death():',
                    'def parent_death():\n        import time\n        time.sleep('+str(preexec_delay)+')'))
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            (root / 'run').write_text('import socket,time\ns=socket.socket()\ns.bind(("127.0.0.1",'+str(port)+'))\ns.listen()\ntime.sleep(60)\n')
            config = root / 'config.json'; config.write_text('{}')
            environment = panel_environment(dict(os.environ), 'x86_64-gnu')
            environment.pop('GLIBC_TUNABLES', None)
            with ExitStack() as stack:
                core = WatchedCore(stack, sys.executable, root, sys.executable, config,
                                   root / 'core.log', environment, 'x86_64-gnu')
                core.start(port)
                core.check()
                self.assertNotEqual(core.process.pid, core.core_pid)
                self.assertEqual(observed_allocator(core.core_pid), allocator_fields(environment))
                original = copy.deepcopy(core.roles)
                core.roles['core']['starttime_ticks'] += 1
                with self.assertRaisesRegex(RuntimeError, 'identity'): core.check()
                core.roles = original
            value = core.evidence()
            validate_tree(value, 'x86_64-gnu', os.getpid(), identity(os.getpid())['cgroup'])
            for path in ('policy', 'roles'):
                invalid = copy.deepcopy(value); invalid[path] = {}
                with self.assertRaises(RuntimeError):
                    validate_tree(invalid, 'x86_64-gnu', os.getpid(), identity(os.getpid())['cgroup'])
            for row in value['roles'].values():
                self.assertFalse(Path('/proc', str(row['pid'])).exists())

    def test_wrong_ready_command_extra_children_and_stuck_preexec_are_rejected(self):
        import psutil
        from unittest.mock import Mock
        for mode in ('wrong_ready','wrong_pending','extra','stuck'):
            with self.subTest(mode=mode):
                core=WatchedCore.__new__(WatchedCore)
                core.python=Path('/python');core.payload=Path('/payload');core.binary=Path('/core')
                core.config=Path('/config');core.environment={};core.log=None
                command=['/python','/payload/app/services/core_child.py',str(os.getpid()),'/core','run','-c','/config']
                child=Mock();child.cmdline.return_value=command if mode=='stuck' else ['/unexpected']
                child.net_connections.return_value=[SimpleNamespace(status=psutil.CONN_LISTEN,laddr=SimpleNamespace(port=1234))] if mode=='wrong_ready' else []
                process=Mock(pid=123);process.poll.return_value=None
                owner=Mock();owner.children.return_value=[child,child] if mode=='extra' else [child]
                with patch('scripts.low_resource_service_tree.subprocess.Popen',return_value=process), \
                        patch('psutil.Process',return_value=owner), \
                        patch('scripts.low_resource_service_tree.time.monotonic',side_effect=[0,1,13]), \
                        patch('scripts.low_resource_service_tree.time.sleep'):
                    with self.assertRaises(RuntimeError):core.start(1234)

    def test_exec_during_listener_probe_does_not_pair_stale_argv(self):
        import psutil
        from unittest.mock import Mock
        core=WatchedCore.__new__(WatchedCore)
        core.python=Path('/python');core.payload=Path('/payload');core.binary=Path('/core')
        core.config=Path('/config');core.environment={};core.log=None;core.roles={}
        command=['/python','/payload/app/services/core_child.py',str(os.getpid()),'/core','run','-c','/config']
        transitioned=[]
        child=Mock(pid=124)
        def probe(**kwargs):
            transitioned.append(True)
            return [SimpleNamespace(status=psutil.CONN_LISTEN,laddr=SimpleNamespace(port=1234))]
        child.net_connections.side_effect=probe
        child.cmdline.side_effect=lambda:command[3:] if transitioned else command
        owner=Mock();owner.children.return_value=[child]
        process=Mock(pid=123);process.poll.return_value=None
        with patch('scripts.low_resource_service_tree.subprocess.Popen',return_value=process), \
                patch('psutil.Process',return_value=owner), \
                patch('scripts.low_resource_service_tree.identity',side_effect=lambda pid:dict(pid=pid)), \
                patch('scripts.low_resource_service_tree.observed_allocator',return_value={}), \
                patch.object(core,'check') as checked:
            core.start(1234)
        checked.assert_called_once();self.assertEqual(core.core_pid,124)

    def test_allowed_environment_fields_and_exact_cgroup(self):
        value = allocator_fields({'MALLOC_MMAP_THRESHOLD_':'131072', 'SECRET':'never record'})
        self.assertEqual(value, dict(mmap_threshold='131072', malloc_tunable_present=False))
        for bad in ('-1','١','18446744073709551616','1\n'):
            with self.assertRaises(RuntimeError): allocator_fields({'MALLOC_MMAP_THRESHOLD_':bad})
        self.assertEqual(canonical_cgroup('0::/system.slice/test.service','test.service'), '/sys/fs/cgroup/system.slice/test.service')
        for bad in ('0::/a/../test.service','0::/a//test.service','0::/wrong','1::/test.service','0::/test.service\n0::/test.service'):
            with self.assertRaises(RuntimeError): canonical_cgroup(bad,'test.service')

    def test_load_samples_bind_window_and_all_four_service_identities(self):
        from scripts.low_resource_service_tree import validate_sample_bindings
        root='/sys/fs/cgroup/test'
        roles={name:dict(pid=pid,starttime_ticks=pid*10) for pid,name in ((3,'watchdog'),(4,'core'))}
        processes=[dict(pid=pid,starttime_ticks=pid*10) for pid in (1,2,3,4)]
        metric={'memory.current':100,'memory.peak':200,'memory.stat':dict(anon=50,file=50),
                'cpu.stat':dict(usage_usec=1),'memory.events':dict(max=0,oom=0,oom_kill=0,oom_group_kill=0)}
        baseline=[dict(pid=pid,start_ticks=pid*10,role=role) for pid,role in ((1,'worker'),(2,'panel'))]
        report=dict(duration_profile='sustained',worker_pid=1,service_cgroup='0::/test',
            proxy_workload=dict(server_tree=dict(roles=roles)),
            stages=[dict(name='panel_only_idle_1800_seconds',accounting_samples=[dict(processes=baseline)]),
                dict(name='proxy_sustained_1_connections_600_seconds',started_monotonic=2000,wall_seconds=605,
                    samples=[dict(copy.deepcopy(metric),observed_monotonic=2000+i*30,processes=copy.deepcopy(processes)) for i in range(21)])],
            sustained_load=[dict(concurrency=1,wall_seconds=600,partial_load=dict(started_monotonic=2002))])
        validate_sample_bindings(report)
        for mutation in ('duplicate','nan','outside','missing_panel','wrong_worker','gap','missing_metrics','counter'):
            bad=copy.deepcopy(report);samples=bad['stages'][1]['samples']
            if mutation=='duplicate':samples[1]['observed_monotonic']=samples[0]['observed_monotonic']
            elif mutation=='nan':samples[1]['observed_monotonic']=float('nan')
            elif mutation=='outside':samples[-1]['observed_monotonic']=9000
            elif mutation=='missing_panel':samples[1]['processes'].pop(1)
            elif mutation=='wrong_worker':samples[1]['processes'][0]['starttime_ticks']=999
            elif mutation=='gap':samples[1]['observed_monotonic']=2001
            elif mutation=='missing_metrics':samples[1].pop('memory.stat')
            elif mutation=='counter':samples[1]['cpu.stat']['usage_usec']=0
            with self.subTest(mutation=mutation),self.assertRaises(RuntimeError):validate_sample_bindings(bad)
