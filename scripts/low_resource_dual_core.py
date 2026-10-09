"""Short two-core coexistence and controlled restart, not overload qualification.

All servers, installed watchdogs, clients, targets and observation stay in the
existing service cgroup. Distinct credentials prevent a crossed client route
from silently passing through the other server.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import http.client
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import time
from types import SimpleNamespace

from scripts.low_resource_service_tree import WatchedCore, identity, observed_allocator, validate_tree
from scripts.low_resource_accounting import snapshot, validate_stage

SECONDS = 30
INTERVAL = 5
CORES = ('sing-box', 'xray')
XRAY_UUID = '11111111-1111-1111-1111-111111111112'
BODY = b'VUI-LOOPBACK-TARGET'
STAGES = ('dual_core_start', 'dual_core_idle_30_seconds', 'dual_core_clients_start',
          'dual_core_parallel_requests_30_seconds', 'dual_core_sing_box_stop_restart',
          'dual_core_xray_stop_restart', 'dual_core_cleanup')
BASE_STAGES = ('offline_stage_including_wheels', 'activate', 'fake_admin_provision', 'https_startup',
    'anonymous_denied', 'untrusted_ca_rejected', 'login', 'authenticated_read',
    '100_authenticated_reads_concurrency_10', 'panel_only_idle_60_seconds', 'proxy_server_start',
    'panel_single_proxy_idle_60_seconds', 'proxy_client_start', 'proxy_10_requests_concurrency_1',
    'proxy_100_requests_concurrency_10', 'proxy_wrong_uuid_rejected', 'proxy_wrong_ca_rejected',
    'logout', 'logged_out_replayed_cookie_denied', 'login_before_backup', 'stopped_panel', 'stopped_backup',
    'stopped_restore', 'restore_data_and_session_revocation', 'restored_https_startup',
    'old_session_after_restore_denied', 'restored_login', 'restored_logout', 'final_panel_stop')


def process_binding(pid, command=None):
    import psutil
    before = identity(pid)
    argv = psutil.Process(pid).cmdline()
    if command is not None and argv != command:
        raise RuntimeError('Dual-core process command changed')
    executable = Path(f'/proc/{pid}/exe').stat()
    result = dict(identity=before, argv_sha256=hashlib.sha256(json.dumps(argv).encode()).hexdigest(),
                  executable_device=executable.st_dev, executable_inode=executable.st_ino,
                  allocator=observed_allocator(pid))
    if identity(pid) != before: raise RuntimeError('Process changed during binding observation')
    return result


def xray_config(port, cert, key):
    from app.services.core_manager import XrayAdapter
    row = SimpleNamespace(enable=True, protocol='vless', tag='dual-xray', port=port,
        settings={'clients': [{'id': XRAY_UUID, 'flow': ''}], 'decryption': 'none'},
        stream_settings={'network': 'tcp', 'security': 'tls', 'tlsSettings': {
            'serverName': 'vpn.example.test', 'certificates': [
                {'certificateFile': str(cert), 'keyFile': str(key)}]}})
    config = XrayAdapter().build_config([row])
    config['inbounds'][0]['listen'] = '127.0.0.1'
    config['log'] = {'loglevel': 'debug'}
    return config


def paced_lane(request, start, *, seconds=SECONDS):
    rows = []
    for index in range(seconds):
        planned = start + index
        time.sleep(max(0, planned - time.monotonic()))
        began = time.monotonic()
        if began >= planned + 1:
            raise RuntimeError('Dual-core request missed its one-second slot')
        status, body = request()
        if (status, body) != (200, BODY):
            raise RuntimeError('Dual-core positive response mismatch')
        finished = time.monotonic()
        if finished > start + seconds:
            raise RuntimeError('Dual-core response escaped the common load window')
        rows.append(dict(index=index, planned_monotonic=planned,
            started_monotonic=began, finished_monotonic=finished,
            status=status, body_bytes=len(body)))
    time.sleep(max(0, start + seconds - time.monotonic()))
    return rows


def require_stopped_rejection(request, deliveries, log_path, server_port):
    from scripts.low_resource_proxy import assert_no_direct_log
    before = len(deliveries)
    offset = log_path.stat().st_size
    try:
        status, body = request()
    except (OSError, http.client.HTTPException):
        pass
    else:
        if status == 200 or body == BODY:
            raise RuntimeError('Stopped core still delivered a successful response')
    deadline = time.monotonic() + 2
    while True:
        if len(deliveries) != before:
            raise RuntimeError('Stopped core request reached target')
        lines = log_path.read_text(errors='replace')[offset:].lower().splitlines()
        if any(f'127.0.0.1:{server_port}' in line and 'connection refused' in line for line in lines):
            assert_no_direct_log(log_path)
            return {'attempts': 1, 'target_deliveries': 0, 'server_port': server_port,
                    'fresh_connection_refused': True, 'no_direct': True}
        if time.monotonic() >= deadline:
            raise RuntimeError('Stopped core lacks fresh port-specific refusal evidence')
        time.sleep(.02)


def run(root, log_prefix, stage, report, runtime, panel_pid, panel_check,
        directory, read_metrics, checkpoint):
    from loopback_helpers import CoreProcess, certificate_files, http_through, start_http_target, unused_port
    from scripts.low_resource_proxy import UUID, assert_no_direct_log, client_config, server_config
    from app.release_tools import child_env, target_arch
    from deploy.system_launcher import panel_environment
    root.mkdir(mode=0o700)
    payload = runtime['payload']
    env = child_env(payload, runtime['data'])
    env.update(VUI_PUBLIC_ORIGIN=runtime['origin'], VUI_RELEASE_ROOT=str(runtime['root']))
    env = panel_environment(env, runtime['runtime_key'])
    evidence = dict(unit=report['unit'], source_commit=report['source_commit'],
        service_cgroup=report['service_cgroup'], runtime_key=runtime['runtime_key'],
        scope='30-second coexistence and fixed-rate control requests; not per-core budgets, sustained performance or overload',
        accounting='worker/panel/two watchdogs/two servers/two clients/HTTP targets all inside service cgroup',
        generations={name: [] for name in CORES}, recoveries=[], cleanup_complete=False,
        windows={}, clients={}, no_direct=False)
    report['dual_core'] = evidence
    owner = ExitStack()
    servers, clients, targets, configs, ports, all_servers = {}, {}, {}, {}, {}, []
    panel_identity = identity(panel_pid)
    panel_binding = process_binding(panel_pid)

    def healthy(check_panel=True):
        if identity(panel_pid) != panel_identity:
            raise RuntimeError('Panel identity changed during dual-core test')
        if process_binding(panel_pid) != panel_binding:
            raise RuntimeError('Panel executable or command changed during dual-core test')
        for server in servers.values():
            server.check()
            command = [str(runtime['python']), str(payload / 'app/services/core_child.py'),
                       str(os.getpid()), str(server.binary), 'run', '-c', str(server.config)]
            process_binding(server.process.pid, command)
            process_binding(server.core_pid, command[3:])
        for name, client in clients.items():
            if client.process.poll() is not None:
                raise RuntimeError('Dual-core client exited')
            saved = evidence['clients'][name]
            if identity(client.process.pid) != saved['identity'] or observed_allocator(client.process.pid) != saved['allocator']:
                raise RuntimeError('Dual-core client identity or inherited policy changed')
            if process_binding(client.process.pid, client.command) != saved['binding']:
                raise RuntimeError('Dual-core client executable changed')
        if check_panel: panel_check()

    def launch(name):
        number = len(evidence['generations'][name])
        generation_stack = owner.enter_context(ExitStack())
        core = WatchedCore(generation_stack, runtime['python'], payload,
            payload / 'cores' / target_arch() / name, configs[name],
            log_prefix.with_name(log_prefix.name + f'-dual-{name}-{number}.log'),
            env, runtime['runtime_key'])
        # Register before start so failed readiness also retains cleanup evidence.
        record = dict(generation=number, started_monotonic=time.monotonic(), tree=core.evidence())
        evidence['generations'][name].append(record)
        all_servers.append((name, core, generation_stack, record))
        core.start(ports[name])
        record['tree'] = core.evidence()
        servers[name] = core
        return core

    def begin():
        ca, cert, key = certificate_files(root, 'dual')
        evidence['ca_scope'] = 'temporary local test CA; verification enabled'
        for name in CORES:
            ports[name] = unused_port()
            if len(set(ports.values())) != len(ports):
                raise RuntimeError('Dual-core server ports overlap')
            config = (server_config if name == 'sing-box' else xray_config)(ports[name], cert, key)
            path = root / (name + '.json'); path.write_text(json.dumps(config)); path.chmod(0o600)
            configs[name] = path
            launch(name)
        evidence['ports'] = dict(ports)
        manifest = json.loads((payload / 'MANIFEST.json').read_text())
        evidence['core_sha256'] = {name: manifest['files'][f'cores/{target_arch()}/{name}']['sha256'] for name in CORES}
        evidence['panel_identity'] = panel_identity
        return ca

    def roles():
        value = {'worker': os.getpid(), 'panel': panel_pid}
        for name, core in servers.items():
            value[name + '_watchdog'] = core.process.pid
            value[name + '_server'] = core.core_pid
        for name, client in clients.items():
            value[name + '_client'] = client.process.pid
        return value

    def window(name, load=False):
        healthy()
        value = dict(started_monotonic=time.monotonic(), outcome='running',
            accounting_roles=roles(), accounting_cgroup_root=str(directory), accounting_samples=[])
        value['process_bindings'] = {role: process_binding(pid) for role, pid in value['accounting_roles'].items()}
        evidence['windows'][name] = value
        start = time.monotonic() + .5
        value['accounting_started_monotonic'] = start
        before = {core: len(targets[core][1]) for core in CORES} if load else {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {core: pool.submit(paced_lane,
                lambda core=core: http_through(clients[core].listen_port, '127.0.0.1', targets[core][0]), start)
                for core in CORES} if load else {}
            for slot in range(SECONDS // INTERVAL + 1):
                planned = start + slot * INTERVAL
                time.sleep(max(0, planned - time.monotonic()))
                healthy(False)
                for future in futures.values():
                    if future.done(): future.result()  # Propagate failure without waiting for the full window.
                binding_start = time.monotonic()
                bindings = {role: process_binding(pid) for role, pid in value['accounting_roles'].items()}
                binding_end = time.monotonic()
                if bindings != value['process_bindings']:
                    raise RuntimeError('Dual-core process binding changed inside window')
                sample = snapshot(directory, value['accounting_roles'], read_metrics)
                sample['bindings'] = dict(started_monotonic=binding_start, finished_monotonic=binding_end, processes=bindings)
                sample['planned_monotonic'] = planned
                value['accounting_samples'].append(sample)
                if sample['finished_monotonic'] >= planned + INTERVAL:
                    raise RuntimeError('Dual-core accounting missed its slot')
                checkpoint()
            if load:
                value['lanes'] = {core: dict(requests=future.result(),
                    target_deliveries=len(targets[core][1]) - before[core]) for core, future in futures.items()}
        value.update(wall_seconds=time.monotonic() - value['started_monotonic'], outcome='passed')
        validate_stage(value, tuple(value['accounting_roles']), seconds=SECONDS, interval=INTERVAL)
        healthy()

    def start_clients(ca):
        for name in CORES:
            targets[name] = start_http_target(owner)
            port = unused_port()
            credential = UUID if name == 'sing-box' else XRAY_UUID
            config = client_config(port, ports[name], ca, credential)
            path = root / (name + '-client.json'); path.write_text(json.dumps(config)); path.chmod(0o600)
            log = log_prefix.with_name(log_prefix.name + f'-dual-{name}-client.log')
            client = CoreProcess(owner, [str(payload / 'cores' / target_arch() / 'sing-box'), 'run', '-c', str(path)], log, env)
            client.listen_port = port
            clients[name] = client
            client.start(port)
            evidence['clients'][name] = dict(identity=identity(client.process.pid),
                allocator=observed_allocator(client.process.pid), server_port=ports[name],
                binding=process_binding(client.process.pid, client.command), target_port=targets[name][0],
                config_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                proxy_only=True, tls_verify=True, cleanup_complete=False)

    def request(name):
        result = http_through(clients[name].listen_port, '127.0.0.1', targets[name][0])
        if result != (200, BODY): raise RuntimeError('Dual-core recovery response failed')
        return result

    def restart(name):
        other = next(core for core in CORES if core != name)
        previous = servers.pop(name)
        previous.check()
        process = previous.process
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=12)
        if process.returncode != 0:
            raise RuntimeError('Installed watchdog did not stop successfully')
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise RuntimeError('Controlled stop left a live process group')
        # The production watchdog may use its own bounded kill fallback. This
        # proves controlled watchdog exit, not each core's graceful exit status.
        previous.cleanup_complete = True
        previous.process = None  # Proven gone: no later callback may kill a reused pgid.
        generation_stack = next(item[2] for item in all_servers if item[1] is previous)
        generation_stack.close()
        old = previous.evidence()
        before = len(targets[other][1]); request(other)
        if len(targets[other][1]) != before + 1: raise RuntimeError('Peer target count mismatch')
        rejection = require_stopped_rejection(
            lambda: http_through(clients[name].listen_port, '127.0.0.1', targets[name][0]),
            targets[name][1], clients[name].log_path, ports[name])
        healthy()
        fresh = launch(name)
        if any(old['roles'][role]['pid'] == fresh.roles[role]['pid'] and
               old['roles'][role]['starttime_ticks'] == fresh.roles[role]['starttime_ticks']
               for role in ('watchdog', 'core')):
            raise RuntimeError('Restart reused the original process identity')
        counts = {core: len(targets[core][1]) for core in CORES}
        for core in CORES: request(core)
        if any(len(targets[core][1]) != counts[core] + 1 for core in CORES):
            raise RuntimeError('Restart target count mismatch')
        healthy()
        evidence['recoveries'].append(dict(core=name, old_tree=old,
            stopped_request=rejection, peer_deliveries=1, restored_deliveries={core: 1 for core in CORES},
            panel_healthy=True, new_generation=1, watchdog_returncode=0,
            stop_signal='SIGTERM', old_group_gone=True))

    def close():
        try:
            owner.close()
        finally:
            for name, core, generation_stack, record in all_servers:
                record['tree'] = core.evidence()
        for name, client in clients.items():
            if client.process is not None and client.process.poll() is None:
                raise RuntimeError('Dual-core client survived cleanup')
            if name in evidence['clients']: evidence['clients'][name]['cleanup_complete'] = True
            assert_no_direct_log(client.log_path)
        evidence['cleanup_complete'] = True
        evidence['no_direct'] = True

    try:
        ca = stage(STAGES[0], begin)
        stage(STAGES[1], lambda: window('idle'))
        stage(STAGES[2], lambda: start_clients(ca))
        stage(STAGES[3], lambda: window('load', True))
        for index, name in enumerate(CORES): stage(STAGES[4 + index], lambda name=name: restart(name))
        stage(STAGES[6], close)
        panel_check()
    finally:
        try: close()
        finally: checkpoint()


def validate_complete(report, unit, commit, runtime_key):
    value = report.get('dual_core', {})
    if (report.get('duration_profile') != 'dual-core' or value.get('unit') != unit
            or value.get('source_commit') != commit or value.get('service_cgroup') != report.get('service_cgroup')
            or value.get('runtime_key') != runtime_key
            or value.get('cleanup_complete') is not True or value.get('no_direct') is not True):
        raise RuntimeError('Incomplete dual-core source or cleanup evidence')
    stages = report.get('stages', [])
    if (len(stages) != 36 or len({s['name'] for s in stages}) != 36
            or any(s.get('outcome') != 'passed' for s in stages)
            or tuple(s['name'] for s in stages if s['name'] in STAGES) != STAGES
            or tuple(s['name'] for s in stages if s['name'] not in STAGES) != BASE_STAGES):
        raise RuntimeError('Dual-core or original smoke stage coverage changed')
    ports = value.get('ports', {})
    if (set(ports) != set(CORES) or len(set(ports.values())) != 2
            or any(type(port) is not int or not 0 < port < 65536 for port in ports.values())):
        raise RuntimeError('Missing distinct dual-core server ports')
    import re
    if set(value.get('core_sha256', {})) != set(CORES) or any(
            not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha)
            for sha in value['core_sha256'].values()):
        raise RuntimeError('Missing verified bundled core identities')
    generations = value.get('generations', {})
    if set(generations) != set(CORES): raise RuntimeError('Missing core generations')
    identities = set()
    initial_roles = {'worker': report['worker_pid']}
    panel = value.get('panel_identity', {})
    if panel.get('cgroup') != report['service_cgroup'] or type(panel.get('pid')) is not int:
        raise RuntimeError('Panel accounting identity mismatch')
    initial_roles['panel'] = panel['pid']
    for name in CORES:
        rows = generations[name]
        if not isinstance(rows, list) or [row.get('generation') for row in rows] != [0, 1]:
            raise RuntimeError('Missing initial or restarted core generation')
        for row in rows:
            tree = row.get('tree', {})
            validate_tree(tree, runtime_key, report['worker_pid'], report['service_cgroup'])
            for role in ('watchdog', 'core'):
                member = tree['roles'][role]
                key = (member['pid'], member['starttime_ticks'])
                if key in identities: raise RuntimeError('Core generations or roles reused a process identity')
                identities.add(key)
        initial_roles[name + '_watchdog'] = rows[0]['tree']['roles']['watchdog']['pid']
        initial_roles[name + '_server'] = rows[0]['tree']['roles']['core']['pid']
    clients = value.get('clients', {})
    if set(clients) != set(CORES): raise RuntimeError('Missing per-core proxy clients')
    for name in CORES:
        client = clients[name]; member = client.get('identity', {})
        if (client.get('server_port') != ports[name] or client.get('proxy_only') is not True
                or client.get('tls_verify') is not True or client.get('cleanup_complete') is not True
                or member.get('cgroup') != report['service_cgroup'] or member.get('ppid') != report['worker_pid']
                or client.get('allocator') != generations[name][0]['tree']['policy']):
            raise RuntimeError('Client routing, ancestry or accounting mismatch')
    windows = value.get('windows', {})
    if set(windows) != {'idle', 'load'}: raise RuntimeError('Missing coexistence windows')
    for window_name in ('idle', 'load'):
        window = windows[window_name]
        expected = dict(initial_roles)
        if window_name == 'load':
            expected.update({name + '_client': clients[name]['identity']['pid'] for name in CORES})
        if window.get('accounting_roles') != expected:
            raise RuntimeError('Dual-core sample roles do not match actual process generations')
        validate_stage(window, tuple(expected), seconds=SECONDS, interval=INTERVAL)
        outer = next(s for s in stages if s['name'] == (STAGES[1] if window_name == 'idle' else STAGES[3]))
        if not (outer['started_monotonic'] <= window['started_monotonic']
                and window['started_monotonic'] + window['wall_seconds'] <= outer['started_monotonic'] + outer['wall_seconds']):
            raise RuntimeError('Dual-core window is outside its enclosing stage')
        for sample in window['accounting_samples']:
            binding = sample.get('bindings', {})
            if (binding.get('processes') != window.get('process_bindings')
                    or set(binding.get('processes', {})) != set(expected)
                    or not sample['planned_monotonic'] <= binding.get('started_monotonic', -1)
                        <= binding.get('finished_monotonic', -1) <= sample['started_monotonic']):
                raise RuntimeError('Missing same-window executable/argv/allocator observations')
            for member in sample['processes']:
                role = member['role']
                if role in expected:
                    observed = binding['processes'][role]
                    if (observed.get('identity', {}).get('pid') != member['pid']
                            or observed['identity'].get('starttime_ticks') != member['start_ticks']
                            or observed['identity'].get('cgroup') != report['service_cgroup']
                            or not re.fullmatch(r'[a-f0-9]{64}', observed.get('argv_sha256', ''))
                            or type(observed.get('executable_inode')) is not int or observed['executable_inode'] <= 0):
                        raise RuntimeError('Executable observation differs from sampled process')
                if role == 'panel': expected_start = panel.get('starttime_ticks')
                elif role.endswith('_client'):
                    expected_start = clients[role.removesuffix('_client')]['identity'].get('starttime_ticks')
                elif role.endswith(('_server', '_watchdog')):
                    name, suffix = role.rsplit('_', 1)
                    expected_start = generations[name][0]['tree']['roles']['core' if suffix == 'server' else 'watchdog']['starttime_ticks']
                else: continue
                if member['start_ticks'] != expected_start: raise RuntimeError('Dual-core sampled process identity changed')
    load = windows['load']; origin = load['accounting_started_monotonic']
    lanes = load.get('lanes', {})
    if set(lanes) != set(CORES): raise RuntimeError('Missing both simultaneous proxy lanes')
    for lane in lanes.values():
        rows = lane.get('requests', [])
        if len(rows) != SECONDS or lane.get('target_deliveries') != SECONDS:
            raise RuntimeError('Incomplete per-core request or delivery count')
        previous = origin
        for index, row in enumerate(rows):
            started, finished = row.get('started_monotonic'), row.get('finished_monotonic')
            if (any(type(t) not in (int, float) or not math.isfinite(t) for t in (started, finished))
                    or row.get('index') != index or row.get('planned_monotonic') != origin + index
                    or not max(previous, origin + index) <= started < origin + index + 1
                    or not started <= finished <= origin + SECONDS
                    or row.get('status') != 200 or row.get('body_bytes') != len(BODY)):
                raise RuntimeError('Proxy lane did not occupy its actual shared request window')
            previous = finished
    recovery = value.get('recoveries', [])
    if [row.get('core') for row in recovery] != list(CORES): raise RuntimeError('Missing controlled restart coverage')
    for row in recovery:
        if (row.get('old_tree') != generations[row['core']][0]['tree']
                or row.get('stopped_request') != {'attempts': 1, 'target_deliveries': 0,
                    'server_port': ports[row['core']], 'fresh_connection_refused': True, 'no_direct': True}
                or row.get('peer_deliveries') != 1 or row.get('restored_deliveries') != {name: 1 for name in CORES}
                or row.get('panel_healthy') is not True or row.get('new_generation') != 1
                or row.get('watchdog_returncode') != 0 or row.get('stop_signal') != 'SIGTERM'
                or row.get('old_group_gone') is not True):
            raise RuntimeError('Incomplete stopped-path rejection or peer/restart recovery')
