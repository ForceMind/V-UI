"""Bounded loopback CPU-quota pressure and recovery qualification.

This is a test-only workload. It is not a throughput promise or a memory /
connection-exhaustion test. Missing sustained pressure is an explicit failure
to demonstrate this scenario, never silently accepted as recovery coverage.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

SOURCE = Path(__file__).resolve().parents[1]
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
from scripts.low_resource_sustained import atomic_json, cleanup_process_group, membership, outside_server
from scripts.low_resource_service_tree import canonical_cgroup, identity

CONCURRENCIES = (8, 32, 64)
PHASE_SECONDS = 20
SAMPLE_SECONDS = 5
BODY = bytes(range(256)) * 4096
MAX_ATTEMPTS = 65536
MAX_BODY_BYTES = MAX_ATTEMPTS * len(BODY)
RECOVERY_SECONDS = 15
RECOVERY_RESERVE = len(CONCURRENCIES) * 16
CPU_KEYS = ('usage_usec', 'nr_periods', 'nr_throttled', 'throttled_usec')


def pressure_windows(samples):
    """Return a conservative fixture-specific trigger and raw window deltas.

    nr_throttled alone is insufficient. Require >=95% one-core consumption,
    >=80% throttled periods and actual successful proxy payloads for three
    adjacent 5-second windows. No endpoint-only latency trigger is permitted.
    """
    if len(samples) != PHASE_SECONDS // SAMPLE_SECONDS + 1:
        raise RuntimeError('Missing CPU pressure samples')
    windows = []
    streak = 0
    streak_wall = 0.0
    demonstrated = False
    for index, row in enumerate(samples):
        stamp = row.get('monotonic')
        if type(stamp) not in (int, float) or not math.isfinite(stamp):
            raise RuntimeError('Invalid CPU pressure sample time')
        if row.get('slot') != index * SAMPLE_SECONDS:
            raise RuntimeError('Missing or reordered pressure sample slot')
        for key in CPU_KEYS:
            if type(row.get('cpu', {}).get(key)) is not int or row['cpu'][key] < 0:
                raise RuntimeError('Missing CPU quota counters')
        if type(row.get('verified_bytes')) is not int or row['verified_bytes'] < 0:
            raise RuntimeError('Invalid successful proxy byte counter')
        if not index:
            continue
        previous = samples[index - 1]
        wall = stamp - previous['monotonic']
        if not 4.75 <= wall <= 5.25:
            raise RuntimeError('Pressure sampling window drifted')
        delta = {key: row['cpu'][key] - previous['cpu'][key] for key in CPU_KEYS}
        completed = row['verified_bytes'] - previous['verified_bytes']
        if min(*delta.values(), completed) < 0 or delta['nr_throttled'] > delta['nr_periods']:
            raise RuntimeError('CPU pressure counters decreased or are inconsistent')
        utilization = delta['usage_usec'] / (wall * 1_000_000)
        ratio = delta['nr_throttled'] / delta['nr_periods'] if delta['nr_periods'] else 0
        hz = row.get('clock_ticks_per_second')
        if type(hz) is not int or hz != os.sysconf('SC_CLK_TCK') or previous.get('clock_ticks_per_second') != hz:
            raise RuntimeError('Missing process CPU clock units')
        role_ticks = {}
        for name in ('worker', 'panel', 'watchdog', 'core'):
            current = row.get('roles', {}).get(name, {})
            prior = previous.get('roles', {}).get(name, {})
            if current.get('identity') != prior.get('identity') or not current.get('identity'):
                raise RuntimeError('Pressure process identity changed')
            if type(current.get('cpu_ticks')) is not int or type(prior.get('cpu_ticks')) is not int:
                raise RuntimeError('Missing role CPU accounting')
            ticks = current['cpu_ticks'] - prior['cpu_ticks']
            if ticks < 0:
                raise RuntimeError('Process CPU counter decreased')
            role_ticks[name] = ticks
        core_share = role_ticks['core'] / hz / (delta['usage_usec'] / 1_000_000) if delta['usage_usec'] else 0
        worker_cpu = role_ticks['worker'] / hz / wall
        read_slack = row.get('read_finished_monotonic', stamp) - stamp
        prior_slack = previous.get('read_finished_monotonic', previous['monotonic']) - previous['monotonic']
        if sum(role_ticks.values()) / hz > delta['usage_usec'] / 1_000_000 + 8 / hz + read_slack + prior_slack:
            raise RuntimeError('Role CPU exceeds inclusive service accounting')
        qualifies = (utilization >= .95 and ratio >= .8 and delta['throttled_usec'] > 0
            and completed > 0 and core_share >= .8 and worker_cpu <= .1)
        streak = streak + 1 if qualifies else 0
        streak_wall = streak_wall + wall if qualifies else 0
        demonstrated |= streak >= 3 and streak_wall >= 15
        windows.append(dict(wall_seconds=wall, cpu_delta=delta,
            verified_bytes=completed, utilization_one_core=utilization,
            throttled_period_ratio=ratio, role_cpu_ticks=role_ticks,
            core_share_of_service_cpu=core_share, worker_cpu_one_core=worker_cpu, qualifies=qualifies))
    return dict(demonstrated=demonstrated, windows=windows)


class Budget:
    """Reserve the full potential response before dispatch, even on failure."""
    def __init__(self):
        self.lock = threading.Lock()
        self.attempts = 0

    def reserve(self, *, recovery=False):
        with self.lock:
            limit = MAX_ATTEMPTS if recovery else MAX_ATTEMPTS - RECOVERY_RESERVE
            if self.attempts >= limit:
                return False
            self.attempts += 1
            return True


def checked_request(request, work, unit, commit):
    if request.get('unit') != unit or request.get('source_commit') != commit:
        raise RuntimeError('CPU pressure request provenance mismatch')
    if request.get('limits') != dict(concurrencies=list(CONCURRENCIES), phase_seconds=PHASE_SECONDS,
            body_bytes=len(BODY), max_attempts=MAX_ATTEMPTS, max_body_bytes=MAX_BODY_BYTES,
            recovery_seconds=RECOVERY_SECONDS):
        raise RuntimeError('CPU pressure limits changed')
    root = work.resolve(strict=True)
    for name in ('binary', 'fixture', 'ca', 'panel_ca'):
        path = Path(request[name]).resolve(strict=True)
        if not path.is_relative_to(root) or path == root:
            raise RuntimeError('Pressure fixture path escaped private work directory')
    if Path(request['binary']).name != 'sing-box':
        raise RuntimeError('Only the installed bundled core is allowed')
    if type(request.get('server_port')) is not int or not 1024 <= request['server_port'] <= 65535:
        raise RuntimeError('Invalid pressure server endpoint')
    canonical_cgroup(request['service_cgroup'], unit)
    endpoint = urlsplit(request.get('panel_origin', ''))
    if (endpoint.scheme != 'https' or endpoint.hostname != '127.0.0.1'
            or endpoint.username or endpoint.password or endpoint.path or endpoint.query or endpoint.fragment
            or not endpoint.port or not 1024 <= endpoint.port <= 65535):
        raise RuntimeError('Panel recovery endpoint must be explicit loopback HTTPS')
    if set(request.get('roles', {})) != {'worker', 'panel', 'watchdog', 'core'}:
        raise RuntimeError('Incomplete pressure service role tree')
    for role in request['roles'].values():
        if role.get('cgroup') != request['service_cgroup'] or identity(role['pid']) != role:
            raise RuntimeError('Pressure service identity mismatch')
    binary = Path(request['binary']).stat()
    live_binary = Path(f"/proc/{request['roles']['core']['pid']}/exe").stat()
    if (binary.st_dev, binary.st_ino) != (live_binary.st_dev, live_binary.st_ino):
        raise RuntimeError('Pressure client binary is not the installed live server executable')


class PressureBroker:
    def __init__(self, work, output, unit, commit):
        self.work, self.output, self.unit, self.commit = work, output, unit, commit
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def __enter__(self):
        outside_server(self.unit, membership())
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set(); self.thread.join(timeout=15)
        if self.thread.is_alive():
            raise RuntimeError('CPU pressure broker did not stop')

    def serve(self):
        path = self.work / 'cpu-pressure.request.json'
        while not path.exists():
            if self.stop.wait(.1):
                return
        evidence = self.output.with_name(self.output.stem + '-pressure.json')
        process = None
        value = dict(outcome='failed')
        try:
            request = json.loads(path.read_text())
            checked_request(request, self.work, self.unit, self.commit)
            with evidence.with_suffix('.log').open('w') as log:
                process = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()),
                    '--request', str(path), '--output', str(evidence)],
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                deadline = time.monotonic() + 210
                while process.poll() is None:
                    if self.stop.wait(.1) or time.monotonic() >= deadline:
                        raise RuntimeError('Pressure helper interrupted or timed out')
                value = json.loads(evidence.read_text())
                if process.returncode or value.get('outcome') != 'passed':
                    raise RuntimeError('CPU pressure scenario not demonstrated or failed')
        except Exception as exc:
            if evidence.exists():
                try: value = json.loads(evidence.read_text())
                except (ValueError, OSError): pass
            value.update(outcome='failed', broker_error_type=type(exc).__name__)
        finally:
            try:
                if process is not None: cleanup_process_group(process)
                value['broker_cleanup_complete'] = True
            except Exception as exc:
                value.update(outcome='failed', broker_cleanup_complete=False,
                    cleanup_error=type(exc).__name__)
            atomic_json(evidence, value)
            atomic_json(self.work / 'cpu-pressure.response.json', value)


def proc_cpu(pid):
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return int(fields[11]) + int(fields[12])  # utime+stime; never child CPU twice.


def sample(request, client_pid, slot, verified_bytes):
    from scripts.low_resource_acceptance import metrics, assert_no_oom
    from scripts.low_resource_service_tree import identity
    started = time.monotonic()
    roles = {}
    for name, expected in request['roles'].items():
        if identity(expected['pid']) != expected:
            raise RuntimeError('Service role changed during pressure')
        roles[name] = dict(identity=expected, cpu_ticks=proc_cpu(expected['pid']))
    observed = metrics(Path(canonical_cgroup(request['service_cgroup'], request['unit'])))
    assert_no_oom(observed)
    external = {}
    for name, pid in (('generator_target', os.getpid()), ('client', client_pid)):
        info = identity(pid); outside_server(request['unit'], info['cgroup'])
        status = Path(f'/proc/{pid}/status').read_text().splitlines()
        rss = next(int(line.split()[1]) * 1024 for line in status if line.startswith('VmRSS:'))
        external[name] = dict(identity=info, cpu_ticks=proc_cpu(pid), rss_bytes=rss)
    return dict(slot=slot, monotonic=started, read_finished_monotonic=time.monotonic(),
        cpu=observed['cpu.stat'], metrics=observed, roles=roles, external=external,
        clock_ticks_per_second=os.sysconf('SC_CLK_TCK'), verified_bytes=verified_bytes)


def target_server(stack):
    lock = threading.Lock()
    counts = dict(entered=0, completed_writes=0, connections=0)
    sockets = set()
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *_): pass
        def setup(self):
            super().setup(); self.connection.settimeout(3)
            with lock:
                counts['connections'] += 1
                sockets.add(self.connection)
        def finish(self):
            try: super().finish()
            finally:
                with lock: sockets.discard(self.connection)
        def do_GET(self):
            if self.path != '/pressure-1m':
                self.send_error(404); return
            with lock: counts['entered'] += 1
            self.send_response(200)
            self.send_header('Content-Length', str(len(BODY)))
            self.end_headers()
            try:
                self.wfile.write(BODY)
                with lock: counts['completed_writes'] += 1
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                self.close_connection = True
    class Target(ThreadingHTTPServer):
        request_queue_size = 128
        daemon_threads = True
    target = Target(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=target.serve_forever, kwargs=dict(poll_interval=.05), daemon=True)
    thread.start()
    def close():
        target.shutdown()
        with lock: pending = list(sockets)
        for connection in pending:
            try: connection.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            connection.close()
        target.server_close(); thread.join(timeout=3)
        if thread.is_alive(): raise RuntimeError('Pressure target survived shutdown')
        deadline = time.monotonic() + 3
        while sockets and time.monotonic() < deadline:
            time.sleep(.01)
        if sockets: raise RuntimeError('Pressure target connection threads survived cleanup')
    stack.callback(close)
    return target, counts


def connection(proxy, target, timeout=3):
    client = http.client.HTTPConnection('127.0.0.1', proxy, timeout=timeout)
    client.set_tunnel('127.0.0.1', target)
    return client


def response(client):
    client.request('GET', '/pressure-1m', headers={'Connection': 'keep-alive'})
    result = client.getresponse()
    body = result.read(len(BODY) + 1)
    if result.status != 200 or body != BODY:
        raise RuntimeError('Proxy pressure response content changed')


def absolute_request(client, deadline, operation):
    """Interrupt connected sockets at an absolute deadline, including drip feed.

    Connect receives the same remaining timeout; _create_connection registers
    the raw socket before HTTP CONNECT parsing. HTTPS handshake itself uses
    the remaining socket timeout, then its SSL socket is registered too.
    """
    sockets = []
    lock = threading.Lock()
    expired = threading.Event()
    create = client._create_connection
    def stop():
        expired.set()
        with lock: active = list(sockets)
        for sock in active:
            try: sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            sock.close()
    def registered(*args, **kwargs):
        sock = create(*args, **kwargs)
        with lock: sockets.append(sock)
        if expired.is_set():
            sock.close(); raise TimeoutError('Absolute request deadline expired during connect')
        return sock
    remaining = deadline - time.monotonic()
    if remaining <= 0: raise TimeoutError('Absolute request deadline already expired')
    client.timeout = remaining
    client._create_connection = registered
    timer = threading.Timer(remaining, stop)
    timer.daemon = True
    timer.start()
    try:
        if client.sock is None: client.connect()
        with lock:
            if client.sock not in sockets: sockets.append(client.sock)
        client.sock.settimeout(max(.001, deadline-time.monotonic()))
        if expired.is_set() or time.monotonic() >= deadline:
            raise TimeoutError('Absolute request deadline expired after connect')
        result = operation(client)
        if expired.is_set() or time.monotonic() > deadline:
            raise TimeoutError('Absolute request deadline expired')
        return result
    except Exception as exc:
        if expired.is_set() or time.monotonic() > deadline:
            raise TimeoutError('Absolute request deadline interrupted I/O') from exc
        raise
    finally:
        timer.cancel(); timer.join(timeout=1)
        client._create_connection = create
        if timer.is_alive(): raise RuntimeError('Request deadline interrupter survived cleanup')


def phase(request, client_pid, proxy_port, target_port, concurrency, budget, checkpoint):
    lock = threading.Lock()
    origin = time.monotonic() + 1
    counts = dict(attempts=0, completed=0, errors=0, budget_exhausted=False)
    row = dict(concurrency=concurrency, origin=origin, duration_seconds=PHASE_SECONDS,
        pressure_stop_monotonic=origin + PHASE_SECONDS, counts=counts, samples=[], lanes=[], outcome='running')
    stop = threading.Event()
    def lane():
        client = connection(proxy_port, target_port)
        lane_record = dict(started_monotonic=time.monotonic(), requests=0, completed=0)
        with lock: row['lanes'].append(lane_record)
        try:
            time.sleep(max(0, origin - time.monotonic()))
            while not stop.is_set() and time.monotonic() < origin + PHASE_SECONDS:
                if not budget.reserve():
                    with lock: counts['budget_exhausted'] = True
                    stop.set(); return
                with lock:
                    counts['attempts'] += 1
                    lane_record['requests'] += 1
                    lane_record.setdefault('first_request_monotonic', time.monotonic())
                try: absolute_request(client, min(time.monotonic()+3, origin+PHASE_SECONDS+3), response)
                except Exception as exc:
                    with lock:
                        counts['errors'] += 1
                        lane_record['error_type'] = type(exc).__name__
                    if isinstance(exc, (OSError, http.client.HTTPException)):
                        return
                    raise
                with lock:
                    counts['completed'] += 1
                    lane_record['completed'] += 1
                    lane_record.setdefault('first_response_monotonic', time.monotonic())
        finally:
            client.close()
            lane_record['finished_monotonic'] = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(lane) for _ in range(concurrency)]
            try:
                for slot in range(0, PHASE_SECONDS + 1, SAMPLE_SECONDS):
                    time.sleep(max(0, origin + slot - time.monotonic()))
                    with lock: completed = counts['completed']
                    row['samples'].append(sample(request, client_pid, slot, completed * len(BODY)))
                    checkpoint(row)
                for future in futures: future.result()
            finally: stop.set()
        row['load_stopped_monotonic'] = time.monotonic()
        if row['load_stopped_monotonic'] > row['pressure_stop_monotonic'] + 5:
            raise RuntimeError('Pressure lanes exceeded fixed drain deadline')
        row['pressure'] = pressure_windows(row['samples'])
        row['outcome'] = 'passed'
        return row
    except Exception:
        row['outcome'] = 'failed'; checkpoint(row)
        raise


def recover(request, row, proxy_port, target_port, client_pid, budget, checkpoint):
    """Drain and all probes share 15 seconds from the pressure cutoff."""
    origin = row['pressure_stop_monotonic']
    deadline = origin + RECOVERY_SECONDS
    endpoint = urlsplit(request['panel_origin'])
    recovery = row['recovery'] = dict(origin=origin, deadline=deadline, probes=[], consecutive_success=0)
    for index in range(RECOVERY_SECONDS):
        slot = max(origin + index, time.monotonic())
        if slot >= deadline:
            break
        time.sleep(max(0, slot - time.monotonic()))
        end = min(deadline, slot + 1)
        if not budget.reserve(recovery=True): raise RuntimeError('Recovery budget exhausted')
        probe = dict(started_monotonic=time.monotonic(), success=False)
        client = connection(proxy_port, target_port, max(.001, end-time.monotonic()))
        try:
            absolute_request(client, end, response)
            remaining = end - time.monotonic()
            if remaining <= 0: raise TimeoutError('Recovery proxy deadline expired')
            panel = http.client.HTTPSConnection(endpoint.hostname, endpoint.port, timeout=remaining,
                context=ssl.create_default_context(cafile=request['panel_ca']))
            def panel_request(connection):
                connection.request('GET', '/api/auth/me', headers={'Cookie': request['cookie']})
                result = connection.getresponse(); body = result.read(65537)
                if result.status != 200 or len(body) > 65536 or not json.loads(body).get('username'):
                    raise RuntimeError('Panel recovery response failed')
            try: absolute_request(panel, end, panel_request)
            finally: panel.close()
            evidence = sample(request, client_pid, index, len(BODY))
            if time.monotonic() > end: raise TimeoutError('Recovery round deadline expired')
            probe.update(success=True, proxy_status=200, panel_status=200, body_bytes=len(BODY),
                service_roles={name: info['identity'] for name, info in evidence['roles'].items()}, sample=evidence)
            recovery['consecutive_success'] += 1
        except (OSError, http.client.HTTPException) as exc:
            probe['error_type'] = type(exc).__name__
            recovery['consecutive_success'] = 0
        finally:
            client.close()
            probe['finished_monotonic'] = time.monotonic()
            recovery['probes'].append(probe)
            checkpoint(row)
        if recovery['consecutive_success'] >= 6:
            recovery['completed_monotonic'] = time.monotonic()
            return
        time.sleep(max(0, min(deadline, slot + 1) - time.monotonic()))
    raise RuntimeError('Service did not recover for six consecutive rounds inside fixed 15 seconds')


def request_pressure(work, output, unit, commit, binary, fixture, ca, port,
                     panel_origin, panel_ca, cookie, roles, observe):
    request = dict(work=str(work), unit=unit, source_commit=commit, binary=str(binary),
        fixture=str(fixture), ca=str(ca), server_port=port, panel_origin=panel_origin,
        panel_ca=str(panel_ca), cookie=cookie, roles=roles, service_cgroup=membership(),
        limits=dict(concurrencies=list(CONCURRENCIES), phase_seconds=PHASE_SECONDS,
            body_bytes=len(BODY), max_attempts=MAX_ATTEMPTS, max_body_bytes=MAX_BODY_BYTES,
            recovery_seconds=RECOVERY_SECONDS))
    path = work / 'cpu-pressure.request.json'
    response_path = work / 'cpu-pressure.response.json'
    if path.exists() or response_path.exists():
        raise RuntimeError('Refusing reused CPU pressure request evidence')
    atomic_json(path, request)
    deadline = time.monotonic() + 225
    while not response_path.exists():
        if time.monotonic() >= deadline: raise RuntimeError('CPU pressure broker response timed out')
        observe(); time.sleep(.25)
    value = json.loads(response_path.read_text())
    validate_result(value, unit, commit, require_trigger=False)
    return value


def validate_result(value, unit, commit, *, require_trigger=True):
    if (value.get('unit') != unit or value.get('source_commit') != commit
            or value.get('outcome') != 'passed' or value.get('cleanup_complete') is not True
            or value.get('broker_cleanup_complete') is not True or value.get('no_direct') is not True
            or value.get('proxy_only') is not True or value.get('tls_verify') is not True
            or value.get('body_sha256') != hashlib.sha256(BODY).hexdigest()):
        raise RuntimeError('CPU pressure result failed or has wrong provenance')
    expected_limits = dict(concurrencies=list(CONCURRENCIES), phase_seconds=PHASE_SECONDS,
        body_bytes=len(BODY), max_attempts=MAX_ATTEMPTS, max_body_bytes=MAX_BODY_BYTES,
        recovery_seconds=RECOVERY_SECONDS)
    if value.get('limits') != expected_limits:
        raise RuntimeError('CPU pressure limits differ')
    canonical_cgroup(value.get('service_cgroup'), unit)
    outside_server(unit, value.get('external_cgroup', ''))
    roles = value.get('roles', {})
    if set(roles) != {'worker', 'panel', 'watchdog', 'core'}:
        raise RuntimeError('CPU pressure roles incomplete')
    if (roles['panel']['ppid'] != roles['worker']['pid']
            or roles['watchdog']['ppid'] != roles['worker']['pid']
            or roles['core']['ppid'] != roles['watchdog']['pid']
            or any(row.get('cgroup') != value['service_cgroup'] for row in roles.values())):
        raise RuntimeError('CPU pressure process tree differs')
    phases = value.get('phases', [])
    if [row.get('concurrency') for row in phases] != list(CONCURRENCIES):
        raise RuntimeError('CPU pressure phases incomplete')
    demonstrated = False; attempts = 0; successes = 0; last_end = 0
    prior_sample = None
    external_roles = None
    def validate_sample(point):
        nonlocal prior_sample, external_roles
        if point.get('clock_ticks_per_second') != os.sysconf('SC_CLK_TCK'):
            raise RuntimeError('CPU sample clock units changed')
        if set(point.get('roles', {})) != set(roles):
            raise RuntimeError('Incomplete service CPU roles')
        if {name: info['identity'] for name, info in point['roles'].items()} != roles:
            raise RuntimeError('Service process identity changed in sample')
        metric = point.get('metrics', {})
        for key in ('memory.current', 'memory.peak'):
            if type(metric.get(key)) is not int or metric[key] <= 0:
                raise RuntimeError('Missing memory accounting under CPU pressure')
        for section, keys in (('memory.events', ('max','oom','oom_kill','oom_group_kill')),
                              ('memory.stat', ('anon','file','kernel')), ('cpu.stat', CPU_KEYS)):
            for key in keys:
                number = metric.get(section, {}).get(key)
                if type(number) is not int or number < 0:
                    raise RuntimeError('Missing pressure accounting counter')
        if metric['cpu.stat'] != point['cpu']:
            raise RuntimeError('CPU sample mismatched memcg boundary')
        if any(metric['memory.events'][key] for key in ('oom','oom_kill','oom_group_kill')):
            raise RuntimeError('OOM during CPU pressure')
        if set(point.get('external', {})) != {'generator_target', 'client'}:
            raise RuntimeError('External CPU/RSS roles incomplete')
        observed_external = {name: info['identity'] for name, info in point['external'].items()}
        if external_roles is not None and observed_external != external_roles:
            raise RuntimeError('External pressure identities changed')
        external_roles = observed_external
        for external in point['external'].values():
            outside_server(unit, external['identity']['cgroup'])
            if (external['identity']['cgroup'] != value['external_cgroup']
                    or type(external.get('cpu_ticks')) is not int or external['cpu_ticks'] < 0
                    or type(external.get('rss_bytes')) is not int or external['rss_bytes'] <= 0):
                raise RuntimeError('External pressure accounting incomplete')
        if prior_sample is not None:
            if point['monotonic'] < prior_sample['read_finished_monotonic']:
                raise RuntimeError('CPU accounting sample timeline moved backward')
            previous = prior_sample['metrics']
            pairs = [(metric['memory.peak'],previous['memory.peak'])]
            pairs += [(metric['cpu.stat'][key],previous['cpu.stat'][key]) for key in CPU_KEYS]
            pairs += [(metric['memory.events'][key],previous['memory.events'][key]) for key in ('max','oom','oom_kill','oom_group_kill')]
            pairs += [(point['roles'][name]['cpu_ticks'],prior_sample['roles'][name]['cpu_ticks']) for name in roles]
            pairs += [(point['external'][name]['cpu_ticks'],prior_sample['external'][name]['cpu_ticks']) for name in external_roles]
            if any(a < b for a,b in pairs):
                raise RuntimeError('Cumulative accounting decreased across pressure/recovery phases')
        prior_sample = point
    for row in phases:
        if (row.get('outcome') != 'passed' or row.get('duration_seconds') != PHASE_SECONDS
                or row['origin'] < last_end or row.get('pressure_stop_monotonic') != row['origin'] + PHASE_SECONDS
                or not row['pressure_stop_monotonic'] <= row.get('load_stopped_monotonic', 0) <= row['pressure_stop_monotonic'] + 5):
            raise RuntimeError('CPU pressure timing or drain incomplete')
        counts = row['counts']
        if (counts.get('budget_exhausted') is not False
                or any(type(counts.get(key)) is not int or counts[key] < 0 for key in ('attempts', 'completed', 'errors'))
                or counts['attempts'] != counts['completed'] + counts['errors']):
            raise RuntimeError('CPU pressure request accounting inconsistent')
        lanes = row.get('lanes', [])
        if (len(lanes) != row['concurrency']
                or sum(lane.get('requests', -1) for lane in lanes) != counts['attempts']
                or sum(lane.get('completed', -1) for lane in lanes) != counts['completed']):
            raise RuntimeError('Requested pressure lanes missing or counts inconsistent')
        for lane in lanes:
            if (not row['origin']-1.25 <= lane.get('started_monotonic', 0) <= lane.get('finished_monotonic', 0) <= row['load_stopped_monotonic']
                    or type(lane.get('requests')) is not int or type(lane.get('completed')) is not int
                    or not 0 <= lane['completed'] <= lane['requests']):
                raise RuntimeError('Invalid pressure lane lifetime')
        samples = row['samples']; assessment = pressure_windows(samples)
        if row.get('pressure') != assessment:
            raise RuntimeError('CPU pressure trigger was not derived from raw counters')
        demonstrated |= assessment['demonstrated']
        for index, point in enumerate(samples):
            validate_sample(point)
            if not row['origin'] + index * 5 <= point['monotonic'] <= row['origin'] + index * 5 + .25:
                raise RuntimeError('Pressure samples outside planned window')
            if not point['monotonic'] <= point['read_finished_monotonic'] <= point['monotonic'] + .25:
                raise RuntimeError('Pressure sample accounting reads too slow')
            if point['verified_bytes'] > counts['completed'] * len(BODY):
                raise RuntimeError('Sample successes exceed full phase count')
        recovery = row.get('recovery', {})
        if (recovery.get('origin') != row['pressure_stop_monotonic']
                or recovery.get('deadline') != row['pressure_stop_monotonic'] + RECOVERY_SECONDS
                or recovery.get('consecutive_success') != 6):
            raise RuntimeError('Pressure recovery deadline or success contract changed')
        probes = recovery.get('probes', [])
        if not 6 <= len(probes) <= RECOVERY_SECONDS:
            raise RuntimeError('Pressure recovery probe count invalid')
        streak = 0; previous = row['load_stopped_monotonic']
        previous_start = None
        for probe in probes:
            if not previous <= probe['started_monotonic'] <= probe['finished_monotonic'] <= recovery['deadline']:
                raise RuntimeError('Recovery probe outside fixed deadline')
            if probe['finished_monotonic'] - probe['started_monotonic'] > 1:
                raise RuntimeError('Recovery probe exceeded one-second round')
            if previous_start is not None and probe['started_monotonic'] - previous_start < .99:
                raise RuntimeError('Recovery probes did not follow one-second cadence')
            previous_start = probe['started_monotonic']
            if probe.get('success') is True:
                if (probe.get('proxy_status'), probe.get('panel_status'), probe.get('body_bytes')) != (200, 200, len(BODY)):
                    raise RuntimeError('Recovery did not verify both proxy and panel')
                if probe.get('service_roles') != roles:
                    raise RuntimeError('Recovery used a changed service process tree')
                point = probe.get('sample', {})
                if not probe['started_monotonic'] <= point.get('monotonic', 0) <= point.get('read_finished_monotonic', 0) <= probe['finished_monotonic']:
                    raise RuntimeError('Recovery accounting outside its probe')
                validate_sample(point)
                streak += 1
            else: streak = 0
            previous = probe['finished_monotonic']
        if streak != 6 or not previous <= recovery['completed_monotonic'] <= recovery['deadline']:
            raise RuntimeError('Incomplete consecutive pressure recovery')
        last_end = recovery['completed_monotonic']
        attempts += counts['attempts'] + len(probes)
        successes += counts['completed'] + sum(p.get('success') is True for p in probes)
    if value.get('pressure_demonstrated') is not demonstrated:
        raise RuntimeError('Pressure trigger declaration differs from observations')
    if value.get('scenario_outcome') != ('demonstrated' if demonstrated else 'not_triggered'):
        raise RuntimeError('Missing structured CPU pressure scenario outcome')
    if require_trigger and not demonstrated:
        raise RuntimeError('Service CPU quota saturation not demonstrated')
    if (value.get('attempts') != attempts or not 0 < attempts <= MAX_ATTEMPTS
            or value.get('reserved_body_bytes') != attempts * len(BODY)):
        raise RuntimeError('CPU pressure attempt/byte budget exceeded or inconsistent')
    target = value.get('target', {})
    if not successes <= target.get('completed_writes', -1) <= target.get('entered', -1) <= attempts:
        raise RuntimeError('Proxy completed-body and target counters inconsistent')


def validate_complete(report, unit, commit):
    from scripts.low_resource_dual_core import BASE_STAGES
    expected = BASE_STAGES[:12] + ('cpu_quota_pressure_and_recovery',) + BASE_STAGES[12:]
    if tuple(row.get('name') for row in report.get('stages', [])) != expected:
        raise RuntimeError('CPU pressure profile lost or moved original smoke stages')
    validate_result(report.get('cpu_pressure', {}), unit, commit)
    value = report['cpu_pressure']
    if report.get('worker_pid') != value['roles']['worker']['pid'] or report.get('service_cgroup') != value['service_cgroup']:
        raise RuntimeError('CPU pressure roles differ from the measured worker')
    tree = report.get('proxy_workload', {}).get('server_tree', {}).get('roles', {})
    for role in ('watchdog', 'core'):
        if any(tree.get(role, {}).get(key) != number for key, number in value['roles'][role].items()):
            raise RuntimeError('CPU pressure did not use original installed proxy tree')
    end = 0
    for row in report['stages']:
        start, duration = row.get('started_monotonic'), row.get('wall_seconds')
        if (row.get('outcome') != 'passed' or type(start) not in (int,float) or not math.isfinite(start)
                or type(duration) not in (int,float) or not math.isfinite(duration) or duration < 0 or start < end):
            raise RuntimeError('CPU pressure profile stage failed or has invalid timeline')
        end = start + duration
    stage = report['stages'][12]
    points = [point for phase in value['phases'] for point in
        [*phase['samples'], *(probe['sample'] for probe in phase['recovery']['probes'] if probe['success'])]]
    for point in points:
        if not stage['started_monotonic'] <= point['monotonic'] <= point['read_finished_monotonic'] <= stage['started_monotonic'] + stage['wall_seconds']:
            raise RuntimeError('Pressure accounting escaped measured stage')
        if point['metrics']['memory.peak'] > stage.get('memory_peak_bytes', 0) or point['metrics']['memory.peak'] > report.get('metrics', {}).get('memory.peak', 0):
            raise RuntimeError('Pressure accounting peak exceeds enclosing total')
    if points[-1]['cpu']['usage_usec']-points[0]['cpu']['usage_usec'] > stage.get('cpu_usage_usec', 0):
        raise RuntimeError('Pressure CPU exceeds measured stage')


@contextmanager
def tracked_cleanup(value):
    stack = ExitStack()
    try:
        yield stack
    finally:
        try:
            stack.close()
            value['cleanup_complete'] = True
        except Exception as exc:
            value.update(cleanup_complete=False, cleanup_error_type=type(exc).__name__)
            raise


def run(request, output):
    sys.path.insert(0, str(SOURCE / 'tests'))
    from scripts.low_resource_acceptance import require_hosted_runner
    from scripts.low_resource_proxy import client_config, assert_no_direct_log
    from loopback_helpers import CoreProcess, unused_port
    require_hosted_runner()
    checked_request(request, Path(request['work']), request['unit'], request['source_commit'])
    outside_server(request['unit'], membership())
    value = dict(schema=1, outcome='running', unit=request['unit'], source_commit=request['source_commit'],
        limits=request['limits'], service_cgroup=request['service_cgroup'], roles=request['roles'],
        external_cgroup=membership(), phases=[], cleanup_complete=False,
        body_sha256=hashlib.sha256(BODY).hexdigest(), proxy_only=True, tls_verify=True)
    budget = Budget()
    atomic_json(output, value)
    try:
        with tracked_cleanup(value) as stack:
            target, counts = target_server(stack)
            port = unused_port(); config = client_config(port, request['server_port'], request['ca'])
            config['log']['level'] = 'warn'
            path = Path(request['fixture']) / 'cpu-pressure-client.json'
            path.write_text(json.dumps(config)); path.chmod(0o600)
            core = CoreProcess(stack, [request['binary'], 'run', '-c', str(path)],
                output.with_name(output.stem + '-client.log'), None)
            core.start(port)
            for concurrency in CONCURRENCIES:
                def checkpoint(row):
                    value['current_phase'] = row
                    atomic_json(output, value)
                row = phase(request, core.process.pid, port, target.server_address[1],
                    concurrency, budget, checkpoint)
                recover(request, row, port, target.server_address[1], core.process.pid, budget, checkpoint)
                value['phases'].append(row); value.pop('current_phase', None)
                atomic_json(output, value)
                if row['counts']['budget_exhausted']: break
            value['pressure_demonstrated'] = any(row['pressure']['demonstrated'] for row in value['phases'])
        value['target'] = dict(counts)
        assert_no_direct_log(core.log_path)
        value.update(no_direct=True, attempts=budget.attempts,
            reserved_body_bytes=budget.attempts * len(BODY))
        if len(value['phases']) != len(CONCURRENCIES) or any(x['counts']['budget_exhausted'] for x in value['phases']):
            raise RuntimeError('Fixed pressure budget exhausted before completing planned scenario')
        value['scenario_outcome'] = 'demonstrated' if value['pressure_demonstrated'] else 'not_triggered'
        value['outcome'] = 'passed'
    except Exception as exc:
        value.update(outcome='failed', error_type=type(exc).__name__, error=str(exc),
            attempts=budget.attempts, reserved_body_bytes=budget.attempts * len(BODY))
    finally:
        atomic_json(output, value)
    return 0 if value['outcome'] == 'passed' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    raise SystemExit(run(json.loads(args.request.read_text()), args.output))
