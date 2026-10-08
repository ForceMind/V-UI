"""Duration, separate accounting, fixed-rate and evidence failure contracts."""
import argparse
from contextlib import ExitStack, nullcontext
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts import low_resource_acceptance as gate
from scripts import low_resource_sustained as load

UNIT = 'vui-low-resource-' + 'a' * 32 + '.service'
COMMIT = 'b' * 40


class SustainedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.request = dict(unit=UNIT, source_commit=COMMIT, concurrency=1,
                            duration_seconds=600, binary=str(self.root/'sing-box'),
                            fixture=str(self.root/'fixture'), ca=str(self.root/'ca'), server_port=12345)
        (self.root/'sing-box').touch(); (self.root/'fixture').mkdir(); (self.root/'ca').touch()

    def test_explicit_profile_and_consistent_timeouts(self):
        args = argparse.Namespace(memory_mib=512, bundle=self.root/'b.zip', source_commit=COMMIT)
        self.assertEqual(gate.duration_profile(args), 'smoke')
        self.assertEqual(gate.runtime_seconds(args), 600)
        args.duration_profile = 'sustained'
        self.assertEqual(gate.runtime_seconds(args), 6600)
        command = gate.systemd_command(args, UNIT, self.root, self.root/'worker.json')
        self.assertIn('RuntimeMaxSec=6600', command)
        self.assertEqual(command[-2:], ['--duration-profile', 'sustained'])
        self.assertIn('MemoryMax=512M', command)
        for profile in (320, 384):
            args.memory_mib = profile
            with self.assertRaises(RuntimeError): gate.runtime_seconds(args)
        args.memory_mib = 512; args.duration_profile = 'unknown'
        with self.assertRaises(RuntimeError): gate.runtime_seconds(args)

    def test_only_fixed_fixture_inputs_in_disposable_directory(self):
        load.checked_request(self.request, self.root, UNIT, COMMIT, 1)
        for field, bad in [('unit', 'other'), ('source_commit', 'c'*40), ('concurrency', 50),
                           ('duration_seconds', 1), ('server_port', 80), ('binary', '/bin/sh'),
                           ('ca', '/etc/passwd'), ('fixture', str(self.root))]:
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                load.checked_request({**self.request, field:bad}, self.root, UNIT, COMMIT, 1)
        link = self.root/'outside'; link.symlink_to('/etc/passwd')
        with self.assertRaises(RuntimeError):
            load.checked_request({**self.request,'ca':str(link)}, self.root, UNIT, COMMIT, 1)

    def test_external_cgroup_is_mandatory(self):
        load.outside_server(UNIT, '0::/user.slice/runner.scope')
        for value in ('', '1:memory:/foo', f'0::/system.slice/{UNIT}',
                      f'0::/system.slice/{UNIT}/child', '0::/a\n0::/b'):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                load.outside_server(UNIT, value)

    def test_external_report_rejects_short_or_mixed_or_incomplete_evidence(self):
        good = dict(outcome='passed', source_commit=COMMIT, unit=UNIT, concurrency=1,
                    requested_duration_seconds=600, wall_seconds=600.1, requests=600,
                    target_requests=600, target_connections=1, errors=0, cleanup_complete=True,
                    max_response_seconds=.01, generator_and_target=dict(user_cpu_seconds=1,system_cpu_seconds=.1,max_rss_kib=123),
                    client_children=dict(user_cpu_seconds=1,system_cpu_seconds=.1,max_rss_kib=123),
                    client_cgroup='0::/user.slice/runner.scope', client_core_cgroup='0::/user.slice/runner.scope',
                    no_direct=True, broker_cleanup_complete=True, recovery_requests=1, recovery_connections=1, body_bytes=len(load.BODY),
                    body_sha256=load.hashlib.sha256(load.BODY).hexdigest(), requests_per_connection_per_second=1)
        def attempt(value, subdir):
            work = self.root/subdir; work.mkdir()
            def respond():
                load.atomic_json(work/'external-1.response.json', value)
            with patch.object(load.time,'sleep'):
                return load.request_load(work, UNIT, COMMIT, 1, self.root/'sing-box',
                                         self.root/'fixture',self.root/'ca',12345,respond)
        self.assertEqual(attempt(good,'good'), good)
        for i,(key,value) in enumerate([('outcome','running'),('source_commit','c'*40),('unit','other'),
            ('concurrency',10),('requested_duration_seconds',60),('wall_seconds',599.99),
            ('requests',599),('target_requests',599),('target_connections',2),('errors',1),
            ('wall_seconds',float('nan')),('max_response_seconds',float('inf')),
            ('generator_and_target',{}),('client_children',{}),
            ('generator_and_target',dict(user_cpu_seconds=-1,system_cpu_seconds=0,max_rss_kib=100)),
            ('client_children',dict(user_cpu_seconds=0,system_cpu_seconds=0,max_rss_kib=True)),
            ('cleanup_complete',False),('broker_cleanup_complete',False),('no_direct',False),('recovery_requests',0),('recovery_connections',0),('body_bytes',10),
            ('body_sha256','wrong'),('client_core_cgroup','0::/other'),('requests_per_connection_per_second',2),('client_cgroup',f'0::/system.slice/{UNIT}')]):
            with self.subTest(key=key),self.assertRaises(RuntimeError):
                attempt({**good,key:value},str(i))

    def test_coordinator_requires_both_complete_idles_and_all_load_stages(self):
        stages = [dict(name="panel_only_idle_1800_seconds", outcome="passed", wall_seconds=1800.01),
                  dict(name="panel_single_proxy_idle_1800_seconds", outcome="passed", wall_seconds=1800.01)]
        stages += [dict(name=f"proxy_sustained_{n}_connections_600_seconds", outcome="passed", wall_seconds=600.1)
                   for n in (1, 10, 50)]
        report = dict(stages=stages, sustained_load=[dict(concurrency=n) for n in (1,10,50)])
        with patch.object(load, "validate_result") as validate:
            gate.validate_sustained_stages(report, UNIT, COMMIT)
            self.assertEqual(validate.call_count, 3)
            for i in range(5):
                altered = json.loads(json.dumps(report)); altered["stages"][i]["wall_seconds"] = 1
                with self.subTest(stage=i), self.assertRaises(RuntimeError):
                    gate.validate_sustained_stages(altered, UNIT, COMMIT)
            for bad in (float("nan"), float("inf"), None, "1800"):
                altered = json.loads(json.dumps(report)); altered["stages"][0]["wall_seconds"] = bad
                with self.assertRaises(RuntimeError): gate.validate_sustained_stages(altered, UNIT, COMMIT)
            for n in (0, 1, 2):
                altered = {**report, "sustained_load": report["sustained_load"][:n]}
                with self.assertRaises(RuntimeError): gate.validate_sustained_stages(altered, UNIT, COMMIT)
        # Actual row validation is not optional, even when stage durations pass.
        with self.assertRaises(RuntimeError): gate.validate_sustained_stages(report, UNIT, COMMIT)

    def test_long_harness_refreshes_only_outside_measured_intervals(self):
        source = (Path(__file__).parents[1] / "scripts/low_resource_acceptance.py").read_text()
        first = source.index('stage("panel_only_idle_1800_seconds", idle)')
        refresh = source.index('stage("refresh_session_after_panel_idle", login)')
        proxy = source.index('            run_proxy_smoke(')
        self.assertLess(first, refresh); self.assertLess(refresh, proxy)
        ladder = source.index('                for concurrency in CONCURRENCIES:')
        refresh = source.index('stage(f"refresh_session_before_{concurrency}_connection_load", login)')
        measured = source.index('result = stage(f"proxy_sustained_{concurrency}_connections_600_seconds"')
        self.assertLess(ladder, refresh); self.assertLess(refresh, measured)

    def test_process_tree_records_identity_without_command_or_secret(self):
        (self.root / "cgroup.procs").write_text(f"{os.getpid()}\n999999999\n")
        rows = gate.process_snapshot(self.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pid"], os.getpid())
        self.assertEqual(set(rows[0]), {"pid", "ppid", "name", "threads"})

    def test_group_cleanup_escalates_when_leader_already_exited(self):
        signals = []
        alive = [True]
        class Process:
            pid = 12345
            def poll(self): return 0
            def wait(self, **kwargs): return 0
        def killpg(pid, sig):
            self.assertEqual(pid, 12345)
            signals.append(sig)
            if not alive[0]: raise ProcessLookupError()
            if sig == load.signal.SIGKILL: alive[0] = False
        with patch.object(load.os, "killpg", side_effect=killpg):
            load.cleanup_process_group(Process(), grace_seconds=0, kill_seconds=0)
        self.assertIn(load.signal.SIGTERM, signals)
        self.assertIn(load.signal.SIGKILL, signals)
        self.assertEqual(signals[-1], 0)

    def test_group_cleanup_fails_if_descendant_survives_kill(self):
        class Process:
            pid = 12345
            def poll(self): return 0
            def wait(self, **kwargs): return 0
        with patch.object(load.os, "killpg") as kill:
            with self.assertRaisesRegex(RuntimeError, "survived cleanup"):
                load.cleanup_process_group(Process(), grace_seconds=0, kill_seconds=0)
            self.assertIn(unittest.mock.call(12345, load.signal.SIGKILL), kill.call_args_list)

    def test_evidence_refuses_overwrite(self):
        (self.root/'external-1.response.json').write_text('{}')
        with self.assertRaises(RuntimeError):
            load.request_load(self.root,UNIT,COMMIT,1,self.root/'sing-box',self.root/'fixture',
                              self.root/'ca',12345,lambda:None)

    def test_fixed_load_holds_all_lanes_and_does_not_reconnect(self):
        connections = []
        class Response:
            status=200
            def read(self): return load.BODY
        class Client:
            def __init__(self,*args,**kwargs):
                self.sock=object(); self.requests=0; self.closed=False; connections.append(self)
            def set_tunnel(self,*args): self.tunnel=args
            def connect(self): self.connected=time.monotonic()
            def request(self,*args,**kwargs): self.requests+=1
            def getresponse(self): return Response()
            def close(self): self.closed=time.monotonic()
        for stagger in (False,True):
            connections.clear();progress={}
            with self.subTest(stagger=stagger),patch.object(load.http.client,'HTTPConnection',Client):
                result=load.fixed_load(12345,23456,10,duration=2,interval=.03,progress=progress,record_times=True,stagger=stagger)
            self.assertEqual(result['requests'],20)
            self.assertGreaterEqual(result['wall_seconds'],.06)
            self.assertEqual(len(connections),10)
            self.assertTrue(all(c.requests==2 and c.closed-c.connected>=.06 for c in connections))
            self.assertTrue(all(c.tunnel==('127.0.0.1',23456) for c in connections))
            self.assertEqual(progress['first_response_lanes'],10)
            self.assertEqual(progress['connected_lanes'],10)
            self.assertEqual(len(progress['response_monotonic']),20)
            self.assertTrue(all(t>=progress['started_monotonic'] for t in progress['response_monotonic']))

    def test_fixed_load_wrong_body_or_reconnection_fails_and_closes(self):
        for bad in ('body','reconnect'):
            closed=[]
            class Response:
                status=200
                def read(self): return b'wrong' if bad=='body' else load.BODY
            class Client:
                def __init__(self,*args,**kwargs): self.sock=object()
                def set_tunnel(self,*args): pass
                def connect(self): pass
                def request(self,*args,**kwargs):
                    if bad=='reconnect':self.sock=object()
                def getresponse(self):return Response()
                def close(self):closed.append(True)
            with self.subTest(bad=bad),patch.object(load.http.client,'HTTPConnection',Client):
                with self.assertRaises(RuntimeError):load.fixed_load(1,2,1,duration=1,interval=.01)
            self.assertEqual(closed,[True])

    @unittest.skipUnless(os.getenv("VUI_SUSTAINED_BINARY"), "explicit pinned-core preflight required")
    def test_actual_persistent_connect_preflight(self):
        # Six-second protocol preflight, not the 600-second resource gate.
        from loopback_helpers import CoreProcess, certificate_files, unused_port
        from scripts.low_resource_proxy import server_config, client_config, assert_no_direct_log
        binary = os.environ["VUI_SUSTAINED_BINARY"]
        with ExitStack() as stack:
            ca, cert, key = certificate_files(self.root, "preflight")
            server_port, client_port = unused_port(), unused_port()
            cores = []
            for name, config, port in (("server", server_config(server_port, cert, key), server_port),
                                      ("client", client_config(client_port, server_port, ca), client_port)):
                path = self.root / (name + ".json")
                path.write_text(json.dumps(config))
                core = CoreProcess(stack, [binary, "run", "-c", str(path)], self.root / (name + ".log"), None)
                core.start(port); cores.append(core)
            for concurrency in (1, 10, 50):
                with self.subTest(concurrency=concurrency), ExitStack() as targets:
                    target, counts = load.persistent_target(targets)
                    result = load.fixed_load(client_port, target.server_address[1], concurrency, duration=2)
                    self.assertEqual(result["requests"], concurrency * 2)
                    self.assertEqual(counts, {"connections": concurrency, "requests": concurrency * 2})
                    self.assertGreaterEqual(result["wall_seconds"], 2)
            self.assertTrue(all(core.process.poll() is None for core in cores))
        self.assertTrue(all(core.process.poll() is not None for core in cores))
        assert_no_direct_log(cores[1].log_path)

    def test_workflow_is_opt_in_does_not_change_short_gates(self):
        source=Path(__file__).resolve().parents[1]
        workflow=(source/'.github/workflows/low-resource-sustained.yml').read_text()
        self.assertIn('types: [labeled]',workflow)
        self.assertIn("github.event.label.name == 'run-low-resource-sustained'",workflow)
        self.assertIn('timeout-minutes: 120',workflow)
        self.assertIn('--memory-mib 512 --duration-profile sustained',workflow)
        self.assertIn('if: always()',workflow)
        self.assertIn('ref: ${{ github.event.pull_request.head.sha }}',workflow)
        smoke=(source/'.github/workflows/release.yml').read_text()
        self.assertNotIn('--duration-profile',smoke)
        self.assertIn('timeout-minutes: 35',smoke)
        self.assertEqual(smoke.count('python -B scripts/low_resource_acceptance.py'),3)


if __name__=='__main__':unittest.main()
