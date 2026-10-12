"""Reject false dual-core coverage, crossed lanes and stale process evidence."""
import copy
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import low_resource_dual_core as dual
from scripts import low_resource_acceptance as gate
from scripts.low_resource_accounting import FIELDS

UNIT = 'vui-low-resource-' + 'a' * 32 + '.service'
GROUP = '0::/system.slice/' + UNIT
CG = '/sys/fs/cgroup/system.slice/' + UNIT
COMMIT = 'b' * 40
POLICY = {'mmap_threshold': '131072', 'malloc_tunable_present': False}


def member(pid, ppid=1, group=1):
    return dict(pid=pid, ppid=ppid, starttime_ticks=pid * 10,
                cgroup=GROUP, process_group=group)


def binding(pid):
    return dict(identity=member(pid), argv_sha256='a' * 64,
                executable_device=1, executable_inode=pid, allocator=POLICY.copy())


def tree(watchdog, core):
    return dict(runtime_key='x86_64-gnu', policy=POLICY.copy(),
        parent_role='resource_fixture_worker', worker_pid=1, cleanup_complete=True,
        roles={'watchdog': {**member(watchdog, 1, watchdog), 'allocator': POLICY.copy()},
               'core': {**member(core, watchdog, watchdog), 'allocator': POLICY.copy()}})


def metric():
    return {'memory.current': 100, 'memory.peak': 200,
            'memory.events': dict(oom=0, oom_kill=0, oom_group_kill=0, max=0),
            'memory.stat': dict(anon=50, file=40, kernel=10), 'cpu.stat': dict(usage_usec=100)}


def window(origin, roles):
    bindings = {role: binding(pid) for role, pid in roles.items()}
    samples = []
    for slot in range(7):
        begin = origin + slot * 5
        memory = {key: 4096 for key in FIELDS}; memory['private_bytes'] = 12288
        rows = [dict(pid=pid, start_ticks=pid*10, role=role, cgroup=CG, membership=GROUP,
            read_started_monotonic=begin+.02, read_finished_monotonic=begin+.05,
            memory_bytes=memory.copy()) for role, pid in roles.items()]
        members = {str(pid): CG for pid in roles.values()}
        samples.append(dict(planned_monotonic=begin, started_monotonic=begin+.02,
            finished_monotonic=begin+.1, processes=rows, members_before=members.copy(), members_after=members.copy(),
            metrics_before=metric(), metrics_after=metric(), bindings=dict(started_monotonic=begin,
                finished_monotonic=begin+.01, processes=copy.deepcopy(bindings))))
    return dict(started_monotonic=origin-.5, wall_seconds=30.7, outcome='passed',
        accounting_started_monotonic=origin, accounting_cgroup_root=CG, accounting_roles=roles,
        accounting_samples=samples, process_bindings=bindings)


def report():
    initial = {'worker': 1, 'panel': 2, 'sing-box_watchdog': 3,
               'sing-box_server': 4, 'xray_watchdog': 5, 'xray_server': 6}
    clients = {name: dict(identity=member(pid), binding=binding(pid), allocator=POLICY.copy(),
        server_port=port, target_port=port+10, config_sha256='c'*64,
        proxy_only=True, tls_verify=True, cleanup_complete=True)
        for name, pid, port in (('sing-box', 7, 10001), ('xray', 8, 10002))}
    generations = {name: [dict(generation=0, tree=tree(w, c), config_sha256='d'*64),
                         dict(generation=1, tree=tree(w+6, c+6), config_sha256='d'*64)]
        for name, w, c in (('sing-box', 3, 4), ('xray', 5, 6))}
    for rows in generations.values():
        for row in rows:
            row['bindings']={role:{**binding(info['pid']),
                'identity':{key:value for key,value in info.items() if key!='allocator'}}
                for role,info in row['tree']['roles'].items()}
    idle = window(100, initial)
    load = window(200, {**initial, 'sing-box_client': 7, 'xray_client': 8})
    for value in (idle,load):
        for name in dual.CORES:
            for suffix,role in (('watchdog','watchdog'),('server','core')):
                value['process_bindings'][name+'_'+suffix]=copy.deepcopy(generations[name][0]['bindings'][role])
        for sample in value['accounting_samples']:
            sample['bindings']['processes']=copy.deepcopy(value['process_bindings'])
    load['lanes'] = {name: dict(target_deliveries=30, requests=[dict(index=i,
        planned_monotonic=200+i, started_monotonic=200+i+.01,
        finished_monotonic=200+i+.1, status=200, body_bytes=len(dual.BODY)) for i in range(30)]) for name in dual.CORES}
    value = dict(unit=UNIT, source_commit=COMMIT, service_cgroup=GROUP, runtime_key='x86_64-gnu',
        cleanup_complete=True, no_direct=True, ports={'sing-box': 10001, 'xray': 10002},
        core_sha256={name: 'f'*64 for name in dual.CORES}, panel_identity=member(2),
        generations=generations, clients=clients, windows={'idle': idle, 'load': load},
        recoveries=[dict(core=name, old_tree=generations[name][0]['tree'],
            stopped_request=dict(attempts=1, target_deliveries=0, server_port=clients[name]['server_port'],
                fresh_connection_refused=True, no_direct=True), peer_deliveries=1,
            restored_deliveries={key: 1 for key in dual.CORES}, panel_healthy=True, new_generation=1,
            watchdog_returncode=0, stop_signal='SIGTERM', old_group_gone=True) for name in dual.CORES])
    names = list(dual.BASE_STAGES[:17]) + list(dual.STAGES) + list(dual.BASE_STAGES[17:])
    stages=[];cursor=1
    for name in names:
        start={dual.STAGES[1]:99,dual.STAGES[3]:199}.get(name,cursor)
        wall=32 if name in (dual.STAGES[1],dual.STAGES[3]) else 1
        stages.append(dict(name=name,outcome='passed',started_monotonic=start,wall_seconds=wall))
        cursor=start+wall+.1
    return dict(unit=UNIT, source_commit=COMMIT, worker_pid=1, service_cgroup=GROUP,
                duration_profile='dual-core', stages=stages, dual_core=value)


class DualCoreTests(unittest.TestCase):
    def setUp(self):
        # Match the worker's explicit helper path for either discovery or a
        # standalone tests.test_low_resource_dual_core invocation.
        helpers = str(Path(__file__).resolve().parent)
        if helpers not in sys.path:
            sys.path.insert(0, helpers)
            self.addCleanup(sys.path.remove, helpers)

    def test_contract_accepts_complete_distinct_core_windows(self):
        dual.validate_complete(report(), UNIT, COMMIT, 'x86_64-gnu')

    def test_missing_crossed_stale_late_or_unclean_evidence_is_rejected(self):
        mutations = [
            lambda r: r['stages'].pop(0),
            lambda r: r.update(stages=r['stages'][17:24]+r['stages'][:17]+r['stages'][24:]),
            lambda r: r['stages'][18].update(started_monotonic=1),
            lambda r: r['dual_core'].update(source_commit='0'*40),
            lambda r: r['dual_core']['ports'].update(xray=10001),
            lambda r: r['dual_core']['clients']['xray'].update(server_port=10001),
            lambda r: r['dual_core']['clients']['xray'].update(target_port=10011),
            lambda r: r['dual_core']['clients']['xray'].update(tls_verify=False),
            lambda r: r['dual_core']['generations']['xray'][1].update(tree=tree(5,6)),
            lambda r: r['dual_core']['generations']['xray'][0]['tree'].update(cleanup_complete=False),
            lambda r: r['dual_core']['windows']['load']['accounting_samples'].pop(),
            lambda r: r['dual_core']['windows']['load'].update(accounting_cgroup_root='/sys/fs/cgroup/system.slice'),
            lambda r: r['dual_core']['windows']['load']['lanes'].pop('xray'),
            lambda r: r['dual_core']['windows']['load']['lanes']['xray'].update(target_deliveries=29),
            lambda r: r['dual_core']['windows']['load']['lanes']['xray']['requests'][-1].update(finished_monotonic=230.01),
            lambda r: r['dual_core']['windows']['load']['lanes']['xray']['requests'][3].update(started_monotonic=204.1),
            lambda r: r['dual_core']['windows']['load']['accounting_roles'].update(xray_server=4),
            lambda r: r['dual_core']['windows']['load']['accounting_samples'][1]['bindings']['processes']['xray_server'].update(argv_sha256='e'*64),
            lambda r: r['dual_core']['recoveries'][0].update(old_group_gone=False),
            lambda r: r['dual_core']['recoveries'][0]['stopped_request'].update(fresh_connection_refused=False),
            lambda r: r['dual_core']['recoveries'][1].update(panel_healthy=False),
        ]
        for mutation in mutations:
            value=report();mutation(value)
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                dual.validate_complete(value, UNIT, COMMIT, 'x86_64-gnu')

    def test_pacing_observes_tail_and_rejects_late_response(self):
        class Clock:
            value=100.0
            def monotonic(self): return self.value
            def sleep(self, seconds): self.value+=seconds
        clock=Clock()
        with patch.object(dual, 'time', clock):
            rows=dual.paced_lane(lambda: (200, dual.BODY), 100, seconds=3)
        self.assertEqual([r['started_monotonic'] for r in rows], [100,101,102])
        self.assertEqual(clock.value,103)
        clock.value=100
        def late(): clock.value+=1.1;return 200,dual.BODY
        with patch.object(dual,'time',clock), self.assertRaises(RuntimeError):
            dual.paced_lane(late,100,seconds=3)

    def test_consistent_but_wrong_panel_ancestry_or_allocator_is_rejected(self):
        for problem in ('parent','allocator','group'):
            value=report();data=value['dual_core']
            if problem=='parent':data['panel_identity']['ppid']=999
            if problem=='group':data['panel_identity']['process_group']=999
            for window_value in data['windows'].values():
                panel_binding=window_value['process_bindings']['panel']
                panel_binding['identity']=copy.deepcopy(data['panel_identity'])
                if problem=='allocator':panel_binding['allocator']['mmap_threshold']='0'
                for sample in window_value['accounting_samples']:
                    sample['bindings']['processes']['panel']=copy.deepcopy(panel_binding)
            with self.subTest(problem=problem),self.assertRaises(RuntimeError):
                dual.validate_complete(value,UNIT,COMMIT,'x86_64-gnu')

    def test_stopped_request_requires_fresh_exact_port_reason_and_zero_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            log=Path(directory)/'client.log';log.write_text('127.0.0.1:10001 connection refused\n')
            def request():
                with log.open('a') as out: out.write('dial tcp 127.0.0.1:10001: connect: connection refused\n')
                raise ConnectionRefusedError()
            actual=dual.require_stopped_rejection(request,[],log,10001)
            self.assertTrue(actual['fresh_connection_refused'])
            with patch.object(dual.time,'monotonic',side_effect=[0,3]), self.assertRaises(RuntimeError):
                dual.require_stopped_rejection(lambda: (503,b''),[],log,10001)
            def wrong_port():
                with log.open('a') as out:out.write('dial tcp 127.0.0.1:100012: connection refused\n')
                raise ConnectionRefusedError()
            with patch.object(dual.time,'monotonic',side_effect=[0,3]), self.assertRaises(RuntimeError):
                dual.require_stopped_rejection(wrong_port,[],log,10001)
            deliveries=[]
            def wrong(): deliveries.append(1);return 503,b''
            with self.assertRaises(RuntimeError): dual.require_stopped_rejection(wrong,deliveries,log,10001)

    def test_xray_uses_native_tls_and_different_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            config=dual.xray_config(1234,Path(directory)/'cert',Path(directory)/'key')
        inbound=config['inbounds'][0]
        self.assertEqual(inbound['listen'],'127.0.0.1')
        self.assertEqual(inbound['settings']['clients'],[{'id':dual.XRAY_UUID,'flow':''}])
        self.assertEqual(inbound['settings']['decryption'],'none')
        self.assertEqual(inbound['streamSettings']['security'],'tls')

    def test_real_process_binding_rejects_wrong_command(self):
        actual=dual.process_binding(os.getpid())
        self.assertEqual(actual['identity']['pid'],os.getpid())
        with self.assertRaises(RuntimeError):dual.process_binding(os.getpid(),['unrelated'])

    def test_profile_keeps_512_and_bounded_timeout(self):
        from types import SimpleNamespace
        self.assertEqual(gate.runtime_seconds(SimpleNamespace(duration_profile='dual-core',memory_mib=512)),900)
        with self.assertRaises(RuntimeError):gate.duration_profile(SimpleNamespace(duration_profile='dual-core',memory_mib=320))

    def test_partial_start_load_failure_and_controlled_restart_cleanup_every_generation_once(self):
        import loopback_helpers as helpers
        import app.release_tools as release
        for fault in ('second_start', 'load', None):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                root=Path(directory);payload=root/'payload';payload.mkdir()
                (payload/'MANIFEST.json').write_text(json.dumps({'files': {
                    'cores/x86_64/'+name: {'sha256':'f'*64} for name in dual.CORES}}))
                cores=[];clients=[];targets={};target_closes=[];checks=[]
                class Process:
                    def __init__(self): self.pid=100+len(cores)+len(clients);self.stopped=False;self.returncode=None
                    def poll(self):return 0 if self.stopped else None
                    def send_signal(self, signal):self.stopped=True;self.returncode=0
                    def wait(self, timeout):return self.returncode
                class Watched:
                    def __init__(self,owner,python,payload,binary,config,log,env,key):
                        self.binary,self.config=Path(binary),Path(config);self.process=Process()
                        self.roles={};self.cleanup_complete=False;self.close_calls=0;self.port=None
                        owner.callback(self.close);cores.append(self)
                    def start(self,port):
                        self.port=port
                        if fault=='second_start' and len(cores)==2:raise RuntimeError('second start failed')
                        self.roles={'watchdog':member(self.process.pid),'core':member(self.process.pid+1000)}
                    @property
                    def core_pid(self):return self.roles['core']['pid']
                    def check(self):
                        if self.process is None or self.process.poll() is not None:raise RuntimeError('unexpected stopped core')
                    def close(self):
                        self.close_calls+=1
                        if self.process is not None:self.process.stopped=True
                        self.cleanup_complete=True
                    def evidence(self):return dict(roles=copy.deepcopy(self.roles),cleanup_complete=self.cleanup_complete)
                class Client:
                    def __init__(self,owner,command,log,env):
                        self.command=command;self.process=Process();self.log_path=Path(log);self.log_path.write_text('')
                        owner.callback(self.stop);clients.append(self)
                    def start(self,port):self.listen_port=port
                    def stop(self):self.process.stopped=True
                def target(owner):
                    port=30000+len(targets);deliveries=[];targets[port]=deliveries
                    owner.callback(lambda:target_closes.append(port));return port,deliveries
                def request(client_port,host,target_port):
                    client=next(c for c in clients if c.listen_port==client_port)
                    config=json.loads(Path(client.command[-1]).read_text());port=config['outbounds'][0]['server_port']
                    active=any(c.port==port and c.process is not None and c.process.poll() is None for c in cores)
                    if not active:
                        with client.log_path.open('a') as log:log.write(f'dial tcp 127.0.0.1:{port}: connect: connection refused\n')
                        raise ConnectionRefusedError()
                    targets[target_port].append('delivery');return 200,dual.BODY
                def stage(name,action):
                    if name==dual.STAGES[3] and fault=='load':raise RuntimeError('load failed')
                    # These tests exercise lifecycle failures, not duration qualification.
                    if name in (dual.STAGES[1],dual.STAGES[3]):return None
                    return action()
                value={'unit':UNIT,'source_commit':COMMIT,'service_cgroup':GROUP}
                runtime=dict(payload=payload,python=Path('/python'),data=root/'data',root=root,
                             origin='https://127.0.0.1',runtime_key='x86_64-gnu')
                with ExitStack() as patches:
                    for module,name,replacement in ((dual,'WatchedCore',Watched),(helpers,'CoreProcess',Client),
                        (helpers,'start_http_target',target),(helpers,'http_through',request),
                        (dual,'identity',lambda pid:member(pid)),(dual,'process_binding',lambda pid,command=None:binding(pid)),
                        (dual,'observed_allocator',lambda pid:POLICY.copy()),(release,'target_arch',lambda:'x86_64')):
                        patches.enter_context(patch.object(module,name,replacement))
                    patches.enter_context(patch.object(dual.os,'killpg',side_effect=ProcessLookupError))
                    if fault:
                        with self.assertRaisesRegex(RuntimeError,'failed'):
                            dual.run(root/'fixture',root/'logs',stage,value,runtime,2,lambda:checks.append(1),
                                     Path(CG),lambda _:metric(),lambda:None)
                    else:
                        dual.run(root/'fixture',root/'logs',stage,value,runtime,2,lambda:checks.append(1),
                                 Path(CG),lambda _:metric(),lambda:None)
                self.assertTrue(all(c.close_calls==1 and c.cleanup_complete for c in cores))
                self.assertTrue(all(c.process.poll()==0 for c in clients))
                self.assertEqual(len(target_closes),len(targets))
                self.assertTrue(value['dual_core']['cleanup_complete'])
                if fault is None:
                    self.assertEqual(len(cores),4)
                    self.assertEqual([r['core'] for r in value['dual_core']['recoveries']],list(dual.CORES))
                    self.assertGreaterEqual(len(checks),5)


if __name__ == '__main__': unittest.main()
