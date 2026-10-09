"""Do not confuse ordinary throttling or generator latency with overload."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socketserver
import ssl
import tempfile
import threading
import time
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

from scripts import low_resource_cpu_pressure as pressure

UNIT = 'vui-low-resource-' + 'a' * 32 + '.service'
GROUP = '0::/system.slice/' + UNIT
EXTERNAL = '0::/user.slice/test.scope'
COMMIT = 'b' * 40


def samples():
    return [dict(slot=index * 5, monotonic=100 + index * 5,
        verified_bytes=index * len(pressure.BODY), clock_ticks_per_second=100,
        roles={name: dict(identity={'pid': pid}, cpu_ticks=index * ticks)
            for name, pid, ticks in [('worker', 1, 5), ('panel', 2, 10), ('watchdog', 3, 1), ('core', 4, 450)]}, cpu=dict(
            usage_usec=index * 4_800_000, nr_periods=index * 50,
            nr_throttled=index * 45, throttled_usec=index * 500_000))
        for index in range(5)]


class PressureContractTests(unittest.TestCase):
    def test_three_consecutive_full_quota_windows(self):
        self.assertTrue(pressure.pressure_windows(samples())['demonstrated'])

    def test_ordinary_throttling_is_not_pressure(self):
        rows = samples()
        for index, row in enumerate(rows):
            row['cpu']['usage_usec'] = index * 500_000
            row['roles']['core']['cpu_ticks'] = index * 30
        self.assertFalse(pressure.pressure_windows(rows)['demonstrated'])

    def test_requires_every_trigger_component(self):
        for key, value in [('usage_usec', 4_700_000), ('nr_throttled', 39), ('throttled_usec', 0)]:
            rows = samples()
            for index, row in enumerate(rows):
                row['cpu'][key] = index * value
            self.assertFalse(pressure.pressure_windows(rows)['demonstrated'], key)
        rows = samples()
        for row in rows:
            row['verified_bytes'] = 0
        self.assertFalse(pressure.pressure_windows(rows)['demonstrated'])

    def test_rejects_missing_corrupt_and_shifted_accounting(self):
        good = samples()
        for index, field, value in [(1, 'monotonic', float('nan')),
                                   (1, 'monotonic', 106), (2, 'slot', 15),
                                   (2, 'verified_bytes', -1)]:
            rows = copy.deepcopy(good); rows[index][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                pressure.pressure_windows(rows)
        with self.assertRaises(RuntimeError):
            pressure.pressure_windows(good[:-1])
        rows = samples(); rows[2]['cpu']['usage_usec'] = 1
        with self.assertRaises(RuntimeError):
            pressure.pressure_windows(rows)

    def test_nonadjacent_pressure_does_not_qualify(self):
        rows = samples()
        for index in range(2, 5):
            rows[index]['cpu']['usage_usec'] -= 1_000_000
            rows[index]['roles']['core']['cpu_ticks'] -= 100
        self.assertFalse(pressure.pressure_windows(rows)['demonstrated'])

    def test_observer_cpu_cannot_fake_proxy_pressure(self):
        for role, ticks in [('worker', 60), ('core', 300)]:
            rows = samples()
            for index, row in enumerate(rows):
                row['roles'][role]['cpu_ticks'] = index * ticks
                if role == 'worker': row['roles']['core']['cpu_ticks'] = index * 400
            self.assertFalse(pressure.pressure_windows(rows)['demonstrated'])

    def test_budget_is_atomic_and_reserves_recovery_even_after_pressure_exhausts(self):
        with patch.object(pressure, 'MAX_ATTEMPTS', 128), patch.object(pressure, 'RECOVERY_RESERVE', 16):
            budget = pressure.Budget()
            with ThreadPoolExecutor(max_workers=32) as pool:
                allowed = list(pool.map(lambda _: budget.reserve(), range(1000)))
            self.assertEqual(sum(allowed), 112)
            self.assertEqual(sum(budget.reserve(recovery=True) for _ in range(100)), 16)
            self.assertEqual(budget.attempts, 128)

    def test_complete_report_rejects_wrong_source_deadlines_roles_and_counts(self):
        good = full_result()
        pressure.validate_result(good, UNIT, COMMIT)
        changes = [
            lambda d: d.update(source_commit='c'*40),
            lambda d: d.update(pressure_demonstrated=False),
            lambda d: d.update(attempts=d['attempts']+1),
            lambda d: d.update(external_cgroup=GROUP),
            lambda d: d.update(broker_cleanup_complete=False),
            lambda d: d['phases'].pop(),
            lambda d: d['phases'][0]['counts'].update(budget_exhausted=True),
            lambda d: d['phases'][0]['pressure'].update(demonstrated=False),
            lambda d: d['phases'][0]['recovery'].update(origin=123),
            lambda d: d['phases'][0]['recovery']['probes'][0].update(panel_status=401),
            lambda d: d['phases'][0]['recovery']['probes'][0].update(service_roles={}),
            lambda d: d['phases'][0]['samples'][0]['external']['client']['identity'].update(cgroup=GROUP),
            lambda d: d['phases'][0]['samples'][0]['metrics']['memory.events'].update(oom_kill=1),
            lambda d: d['target'].update(completed_writes=0),
            lambda d: d['phases'][0]['samples'][0].update(external={}),
            lambda d: d['phases'][0]['samples'][0].update(clock_ticks_per_second=1),
            lambda d: d['phases'][1]['samples'][0]['cpu'].update(usage_usec=0),
            lambda d: d['phases'][0]['samples'][0]['metrics'].pop('memory.current'),
        ]
        for change in changes:
            value = copy.deepcopy(good); change(value)
            with self.subTest(change=changes.index(change)), self.assertRaises(RuntimeError):
                pressure.validate_result(value, UNIT, COMMIT)

    def test_not_triggered_retains_complete_functional_report_but_fails_qualification(self):
        value=full_result()
        for phase in value['phases']:
            points=phase['samples']+[probe['sample'] for probe in phase['recovery']['probes']]
            for point in points:point['roles']['core']['cpu_ticks']=0
            phase['pressure']=pressure.pressure_windows(phase['samples'])
        value.update(pressure_demonstrated=False,scenario_outcome='not_triggered')
        pressure.validate_result(value,UNIT,COMMIT,require_trigger=False)
        with self.assertRaisesRegex(RuntimeError,'not demonstrated'):
            pressure.validate_result(value,UNIT,COMMIT)

    def test_three_windows_must_cover_actual_fifteen_seconds(self):
        rows=samples();rows[0]['monotonic']+=.25
        rows[4]['cpu']['usage_usec']=rows[3]['cpu']['usage_usec']+4_000_000
        rows[4]['roles']['core']['cpu_ticks']=rows[3]['roles']['core']['cpu_ticks']+370
        self.assertFalse(pressure.pressure_windows(rows)['demonstrated'])

    def test_same_instant_recovery_is_not_sustained_recovery(self):
        value=full_result();phase=value['phases'][0];first=phase['recovery']['probes'][0]
        phase['recovery']['probes']=[copy.deepcopy(first) for _ in range(6)]
        with self.assertRaises(RuntimeError):pressure.validate_result(value,UNIT,COMMIT)

    def test_profile_preserves_original_stage_order_and_all_controls(self):
        from scripts.low_resource_dual_core import BASE_STAGES
        from scripts import low_resource_acceptance as gate
        from argparse import Namespace
        expected = BASE_STAGES[:12] + ('cpu_quota_pressure_and_recovery',) + BASE_STAGES[12:]
        value = full_result()
        stages = []; origin = 70
        for name in expected:
            wall = 200 if name == 'cpu_quota_pressure_and_recovery' else 1
            stages.append(dict(name=name, outcome='passed', started_monotonic=origin,
                wall_seconds=wall, memory_peak_bytes=200, cpu_usage_usec=200_000_000))
            origin += wall
        report = dict(cpu_pressure=value, stages=stages, worker_pid=1, service_cgroup=GROUP,
            metrics={'memory.peak':200}, proxy_workload={'server_tree':{'roles':{
                name:value['roles'][name] for name in ('watchdog','core')}}})
        pressure.validate_complete(report, UNIT, COMMIT)
        report['stages'][0], report['stages'][1] = report['stages'][1], report['stages'][0]
        with self.assertRaises(RuntimeError): pressure.validate_complete(report, UNIT, COMMIT)
        self.assertEqual(gate.runtime_seconds(Namespace(duration_profile='cpu-pressure', memory_mib=512)), 900)
        for size in (320, 384):
            with self.assertRaises(RuntimeError): gate.duration_profile(Namespace(duration_profile='cpu-pressure', memory_mib=size))

    def test_absolute_deadline_interrupts_real_http_and_https_drip_body(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                self.send_response(200); self.send_header('Content-Length', str(len(pressure.BODY)))
                self.end_headers()
                try:
                    for start in range(0, len(pressure.BODY), 65536):
                        self.wfile.write(pressure.BODY[start:start+65536]); self.wfile.flush(); time.sleep(.12)
                except (OSError, ValueError): pass
        for tls in (False, True):
            with self.subTest(tls=tls), tempfile.TemporaryDirectory() as directory:
                server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); server.daemon_threads = True
                context = None
                if tls:
                    from tests.loopback_helpers import certificate_files
                    ca,cert,key = certificate_files(Path(directory), 'deadline')
                    serving = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); serving.load_cert_chain(cert,key)
                    server.socket = serving.wrap_socket(server.socket, server_side=True)
                    context = ssl.create_default_context(cafile=ca)
                thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval':.01},daemon=True)
                thread.start()
                client = (http.client.HTTPSConnection('127.0.0.1', server.server_port,context=context)
                    if tls else http.client.HTTPConnection('127.0.0.1',server.server_port))
                started = time.monotonic()
                try:
                    with self.assertRaises(TimeoutError):
                        pressure.absolute_request(client, started+.15, pressure.response)
                    self.assertLess(time.monotonic()-started, .8)
                finally:
                    client.close(); server.shutdown(); server.server_close(); thread.join(timeout=1)
                self.assertFalse(thread.is_alive())

    def test_absolute_deadline_also_covers_connect_response_headers(self):
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.recv(4096)
                try:
                    for byte in b'HTTP/1.1 200 Connection established\r\n\r\n':
                        self.request.sendall(bytes([byte])); time.sleep(.03)
                except OSError: pass
        server = socketserver.ThreadingTCPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
        thread = threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
        client = http.client.HTTPConnection(*server.server_address);client.set_tunnel('127.0.0.1',12345)
        started=time.monotonic()
        try:
            with self.assertRaises(TimeoutError): pressure.absolute_request(client,started+.15,pressure.response)
            self.assertLess(time.monotonic()-started,.8)
        finally:
            client.close();server.shutdown();server.server_close();thread.join(timeout=1)

    def test_cleanup_status_survives_original_failure_and_reports_cleanup_failure(self):
        result={};called=[]
        with self.assertRaisesRegex(ValueError,'work failed'):
            with pressure.tracked_cleanup(result) as stack:
                stack.callback(lambda:called.append(True))
                raise ValueError('work failed')
        self.assertEqual(called,[True]);self.assertTrue(result['cleanup_complete'])
        def bad():raise OSError('cleanup failed')
        with self.assertRaises(OSError):
            with pressure.tracked_cleanup(result) as stack:stack.callback(bad)
        self.assertFalse(result['cleanup_complete'])

    def test_broker_failure_preserves_partial_and_cleans_before_response(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);output=root/'summary.json';request=root/'cpu-pressure.request.json'
            request.write_text('{}')
            evidence=root/'summary-pressure.json'
            evidence.write_text('{"outcome":"failed","partial_count":7,"cleanup_complete":true}')
            broker=pressure.PressureBroker(root,output,UNIT,COMMIT)
            process=Mock();process.poll.return_value=1;process.returncode=1
            with patch.object(pressure,'checked_request'),patch.object(pressure.subprocess,'Popen',return_value=process),patch.object(pressure,'cleanup_process_group') as cleanup:
                broker.serve()
            cleanup.assert_called_once_with(process)
            result=pressure.json.loads((root/'cpu-pressure.response.json').read_text())
            self.assertEqual(result['partial_count'],7)
            self.assertTrue(result['broker_cleanup_complete'])
            self.assertTrue(result['cleanup_complete'])
            self.assertEqual(result['outcome'],'failed')

    def test_bad_body_phase_failure_keeps_attempt_and_cleanup_evidence(self):
        clients=[];checkpoints=[]
        def client(*_):
            value=Mock();clients.append(value);return value
        def fail(*_):raise RuntimeError('bad body')
        with patch.object(pressure,'PHASE_SECONDS',1),patch.object(pressure,'SAMPLE_SECONDS',1),patch.object(pressure,'connection',side_effect=client),patch.object(pressure,'absolute_request',side_effect=fail),patch.object(pressure,'sample',return_value={}),patch.object(pressure,'pressure_windows',return_value=dict(demonstrated=False,windows=[])):
            with self.assertRaisesRegex(RuntimeError,'bad body'):
                pressure.phase({},1,12345,12346,2,pressure.Budget(),lambda row:checkpoints.append(copy.deepcopy(row)))
        self.assertEqual(len(clients),2)
        for value in clients:value.close.assert_called_once()
        last=checkpoints[-1]
        self.assertEqual(last['outcome'],'failed')
        self.assertEqual(last['counts']['attempts'],last['counts']['errors'])
        self.assertTrue(all('finished_monotonic' in lane for lane in last['lanes']))


def full_result():
    roles = {name: dict(pid=pid, ppid=parent, starttime_ticks=pid*10,
        process_group=1, cgroup=GROUP) for name, pid, parent in
        [('worker', 1, 99), ('panel', 2, 1), ('watchdog', 3, 1), ('core', 4, 3)]}
    value = dict(unit=UNIT, source_commit=COMMIT, outcome='passed', cleanup_complete=True,
        broker_cleanup_complete=True, no_direct=True, proxy_only=True, tls_verify=True,
        body_sha256=hashlib.sha256(pressure.BODY).hexdigest(), service_cgroup=GROUP,
        external_cgroup=EXTERNAL, roles=roles, phases=[], pressure_demonstrated=True,
        scenario_outcome='demonstrated', attempts=30, reserved_body_bytes=30*len(pressure.BODY),
        target=dict(entered=30, completed_writes=30, connections=30),
        limits=dict(concurrencies=list(pressure.CONCURRENCIES), phase_seconds=20,
            body_bytes=len(pressure.BODY), max_attempts=pressure.MAX_ATTEMPTS,
            max_body_bytes=pressure.MAX_BODY_BYTES, recovery_seconds=15))
    for phase_index, concurrency in enumerate(pressure.CONCURRENCIES):
        origin = 100 + phase_index*40
        points = samples()
        for index, point in enumerate(points):
            point['monotonic'] += phase_index*40
            point['read_finished_monotonic'] = point['monotonic'] + .01
            for key, increment in dict(usage_usec=4_800_000,nr_periods=50,nr_throttled=45,throttled_usec=500_000).items():
                point['cpu'][key] += phase_index*10*increment
            for name in roles:
                point['roles'][name]['identity'] = copy.deepcopy(roles[name])
                point['roles'][name]['cpu_ticks'] += phase_index*10*{'worker':5,'panel':10,'watchdog':1,'core':450}[name]
            point['metrics'] = {'cpu.stat': copy.deepcopy(point['cpu']),
                'memory.current':100, 'memory.peak':200, 'memory.stat':dict(anon=50,file=40,kernel=10),
                'memory.events': dict(max=0, oom=0, oom_kill=0, oom_group_kill=0)}
            point['external'] = {name:dict(identity=dict(pid=pid, ppid=99, starttime_ticks=pid*10,
                cgroup=EXTERNAL,process_group=90), cpu_ticks=(phase_index*10+index)*100,rss_bytes=1000)
                for name,pid in [('client',91),('generator_target',90)]}
        probes = []
        for index in range(6):
            point = copy.deepcopy(points[-1])
            point['monotonic'] = origin+20.11+index
            point['read_finished_monotonic'] = origin+20.12+index
            probes.append(dict(started_monotonic=origin+20.1+index, finished_monotonic=origin+20.2+index,
                success=True, proxy_status=200, panel_status=200, body_bytes=len(pressure.BODY),
                service_roles=copy.deepcopy(roles),sample=point))
        lanes = [dict(started_monotonic=origin-.5, finished_monotonic=origin+20,
            requests=4 if i==0 else 0,completed=4 if i==0 else 0) for i in range(concurrency)]
        value['phases'].append(dict(concurrency=concurrency, origin=origin, duration_seconds=20,
            pressure_stop_monotonic=origin+20, load_stopped_monotonic=origin+20.05,
            counts=dict(attempts=4, completed=4, errors=0, budget_exhausted=False), lanes=lanes,
            samples=points, pressure=pressure.pressure_windows(points), outcome='passed',
            recovery=dict(origin=origin+20, deadline=origin+35, probes=probes,
                consecutive_success=6, completed_monotonic=origin+25.3)))
    return value


if __name__ == '__main__':
    unittest.main()
