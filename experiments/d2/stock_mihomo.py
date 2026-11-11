"""Isolated IPv4 D2 qualification with fixed official cores."""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import hashlib
import io
import json
import os
import platform
import re
import socket
import socketserver
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error

import psutil
import yaml

from loopback_helpers import (
    _recv_exact, _socks_address_from_stream, _socks_udp_target, certificate_files,
)
from proxy_loopback_support import BoundCoreProcess, ReadyMihomoProcess, fresh_lines
from scripts.fetch_mihomo import fetch as fetch_mihomo
from scripts.fetch_test_cores import PINS
from scripts.test_asset_download import download_pinned
from udp_dns_capability_helpers import decode_socks_datagram

DEVICE, DOMESTIC, OVERSEAS = '127.0.0.1', '127.0.0.2', '127.0.0.3'
ALLOWED, TRAP, EGRESS = '127.0.0.4', '127.0.0.5', '127.0.0.6'
FIRST_ID = '11111111-1111-4111-8111-111111111111'
SECOND_ID = '22222222-2222-4222-8222-222222222222'
BAD_ID = '33333333-3333-4333-8333-333333333333'
SNI = 'vpn.example.test'
FIXED_DIGESTS = {
    'sing-box': '07d2866a6c908281a56053f0e7d454918501592f2f258526f2ebe826dab58793',
    'mihomo': '3122d100e8177501776109f1a6253a694611627cf4d7c7ec82705855cf8626a8',
}
WINDOW = 3.0
IDLE_WINDOW = 6.0
CA_REASON = ('x509', 'certificate signed by unknown authority')
SNI_REASON = ('x509', 'certificate is valid for')
UUID_REASON = ('unknown uuid',)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def payload(case, kind, size):
    return hashlib.shake_256(f'd2/{case}/{kind}'.encode()).digest(size)


def clean(text, workspace):
    text = text.replace(str(workspace), '<fixture>')
    return re.sub(r'\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b',
                  '<fixture-uuid>', text, flags=re.I)


def fetch_cores(root):
    pin = PINS['x86_64-gnu']['sing-box']
    archive = download_pinned(pin['url'], max_bytes=100_000_000, timeout=90)
    require(sha256(archive) == pin['sha256'], 'sing-box archive digest differs from the repository pin')
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as bundle:
        member = bundle.getmember(pin['member'])
        require(member.isfile(), 'Pinned sing-box member must be a regular file')
        binary = bundle.extractfile(member).read()
    sing = root / 'sing-box'
    sing.write_bytes(binary)
    sing.chmod(0o700)
    fetch_mihomo(root)
    evidence = {}
    for name, args, prefix in (('sing-box', ['version'], 'sing-box version 1.14.2'),
                               ('mihomo', ['-v'], 'Mihomo Meta v1.19.32 ')):
        path = root / name
        digest = sha256(path.read_bytes())
        require(digest == FIXED_DIGESTS[name], f'{name} executable digest differs from the fixed Linux binary')
        checked = subprocess.run([str(path), *args], text=True, capture_output=True, timeout=15, check=True)
        require(checked.stdout.startswith(prefix), f'{name} version differs from the fixture pin')
        evidence[name] = {'sha256': digest, 'version': checked.stdout.splitlines()[0]}
    return evidence


class Target:
    """Lifetime ledger for a single local UDP receiver and its fake replies."""
    def __init__(self, stack, host):
        self.rows = []
        self.replies = {}
        self.lock = threading.Lock()
        self.closed = False
        owner = self

        class Server(socketserver.UDPServer):
            max_packet_size = 65535

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                request, stream = self.request
                with owner.lock:
                    owner.rows.append({'peer': list(self.client_address), 'size': len(request),
                                       'sha256': sha256(request)})
                    reply = owner.replies.get(request)
                if reply is not None:
                    stream.sendto(reply, self.client_address)

        self.server = stack.enter_context(Server((host, 0), Handler))
        self.address = self.server.server_address
        require(self.address[1] >= 1024, 'Expected a high application port')
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        stack.callback(self.stop)

    def stop(self):
        if self.closed:
            return
        self.closed = True
        self.server.shutdown()
        self.thread.join(2)
        require(not self.thread.is_alive(), 'UDP receiver did not stop')

    def respond(self, request, reply):
        with self.lock:
            self.replies[request] = reply

    def snapshot(self):
        with self.lock:
            return list(self.rows)

    def direct_control(self):
        request = b'controlled-target-is-reachable'
        self.respond(request, request)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
            stream.bind((DEVICE, 0))
            stream.settimeout(2)
            stream.sendto(request, self.address)
            reply, peer = stream.recvfrom(65535)
        require(reply == request and peer == self.address, 'UDP reachability control failed')
        require(len(self.snapshot()) == 1, 'Expected a single direct control delivery')


class Association:
    """Retain the SOCKS control socket, UDP socket and relay for every send."""
    def __init__(self, stack, port):
        self.control = stack.enter_context(socket.create_connection((DEVICE, port), timeout=3))
        self.control.settimeout(3)
        self.control.sendall(b'\x05\x01\x00')
        require(_recv_exact(self.control, 2) == b'\x05\x00', 'SOCKS negotiation failed')
        self.control.sendall(b'\x05\x03\x00\x01' + b'\x00'*6)
        response = _recv_exact(self.control, 4)
        require(response[:3] == b'\x05\x00\x00', 'SOCKS UDP associate failed')
        host, relay_port = _socks_address_from_stream(self.control, response[3])
        host = DEVICE if host == '0.0.0.0' else host
        require(host == DEVICE and relay_port >= 1024, 'Unexpected fixture relay address')
        self.relay = host, relay_port
        self.udp = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        self.udp.bind((DEVICE, 0))

    def identity(self):
        return {'control': list(self.control.getsockname()), 'udp': list(self.udp.getsockname()),
                'relay': list(self.relay), 'control_fd': self.control.fileno(), 'udp_fd': self.udp.fileno()}

    def send(self, target, request):
        frame = b'\x00\x00\x00' + _socks_udp_target(*target) + request
        require(self.udp.sendto(frame, self.relay) == len(frame), 'Partial SOCKS datagram send')

    def receive(self, timeout=WINDOW):
        self.udp.settimeout(timeout)
        try:
            frame, relay = self.udp.recvfrom(65535)
        except TimeoutError:
            return None
        require(relay == self.relay, 'Reply came from another SOCKS relay')
        host, port, reply = decode_socks_datagram(frame)
        return {'peer': [host, port], 'size': len(reply), 'sha256': sha256(reply)}


def reserved_port(stack, host):
    stream = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
    stream.bind((host, 0))
    require(stream.getsockname()[1] >= 1024, 'Expected a high listener port')
    return stream


class Fixture:
    def __init__(self, stack, root, cores, *, guarded=True, fault=None):
        self.stack, self.root, self.cores = stack, root, cores
        self.processes = {}
        self.target = Target(stack, ALLOWED)
        self.trap = Target(stack, TRAP)
        self.target.direct_control()
        self.trap.direct_control()
        self.expected = {self.target: self.target.snapshot(), self.trap: self.trap.snapshot()}
        first_ca, first_cert, first_key = certificate_files(root, 'first-hop')
        second_ca, second_cert, second_key = certificate_files(root, 'second-hop')
        wrong_ca, _, _ = certificate_files(root, 'wrong-ca')
        empty = root / 'empty-ca-dir'
        empty.mkdir()
        reservations = {name: reserved_port(stack, host) for name, host in
                        (('overseas', OVERSEAS), ('domestic', DOMESTIC), ('device', DEVICE))}
        ports = {name: sock.getsockname()[1] for name, sock in reservations.items()}
        self.overseas_port, self.device_port = ports['overseas'], ports['device']
        require(self.overseas_port != self.target.address[1], 'Application and VLESS ports must differ')
        log = {'level': 'debug', 'timestamp': False}

        def inbound(host, port, identity, cert, key):
            return {'type': 'vless', 'listen': host, 'listen_port': port,
                    'users': [{'uuid': identity}],
                    'tls': {'enabled': True, 'certificate_path': str(cert), 'key_path': str(key)}}

        def rule(target):
            return {'network': 'udp', 'ip_cidr': [target.address[0] + '/32'], 'port': [target.address[1]],
                    'action': 'route'}

        overseas_rules = [{**rule(target), 'outbound': 'controlled-egress'} for target in (self.target, self.trap)]
        domestic_rules = [{**rule(target), 'outbound': 'overseas-hop', 'udp_connect': guarded, 'udp_timeout': '2s'}
                          for target in ((self.target,) if guarded else (self.target, self.trap))]
        reject = {'action': 'reject'}
        overseas = {'log': log, 'inbounds': [inbound(OVERSEAS, ports['overseas'], SECOND_ID, second_cert, second_key)],
                    'outbounds': [{'type': 'direct', 'tag': 'controlled-egress', 'inet4_bind_address': EGRESS}],
                    'route': {'rules': [*overseas_rules, reject], 'final': 'controlled-egress'}}
        domestic = {'log': log, 'inbounds': [inbound(DOMESTIC, ports['domestic'], FIRST_ID, first_cert, first_key)],
                    'outbounds': [{'type': 'vless', 'tag': 'overseas-hop', 'server': OVERSEAS,
                                   'server_port': ports['overseas'], 'uuid': BAD_ID if fault == 'second-uuid' else SECOND_ID,
                                   'flow': '', 'network': ['tcp', 'udp'], 'packet_encoding': 'xudp',
                                   'tls': {'enabled': True, 'server_name': 'wrong.example.test' if fault == 'second-sni' else SNI}}],
                    'route': {'rules': [*domestic_rules, reject], 'final': 'overseas-hop'}}
        device = {'mixed-port': ports['device'], 'bind-address': DEVICE, 'allow-lan': False, 'mode': 'rule',
                  'log-level': 'debug', 'ipv6': False, 'find-process-mode': 'off', 'geo-auto-update': False,
                  'proxies': [{'name': 'DOMESTIC_ONLY', 'type': 'vless', 'server': DOMESTIC,
                               'port': ports['domestic'], 'uuid': BAD_ID if fault == 'first-uuid' else FIRST_ID,
                               'network': 'tcp', 'udp': True, 'packet-encoding': 'xudp', 'flow': '',
                               'tls': True, 'servername': 'wrong.example.test' if fault == 'first-sni' else SNI,
                               'skip-cert-verify': False}],
                  'rules': ['MATCH,DOMESTIC_ONLY']}
        for name, host, config, ca in (
            ('overseas', OVERSEAS, overseas, second_ca),
            ('domestic', DOMESTIC, domestic, wrong_ca if fault == 'second-ca' else second_ca),
            ('device', DEVICE, device, wrong_ca if fault == 'first-ca' else first_ca),
        ):
            env = {'PATH': '/usr/bin:/bin', 'HOME': str(root), 'SSL_CERT_FILE': str(ca), 'SSL_CERT_DIR': str(empty)}
            path = root / (name + ('.yaml' if name == 'device' else '.json'))
            path.write_text(yaml.safe_dump(config) if name == 'device' else json.dumps(config))
            path.chmod(0o600)
            if name == 'device':
                binary = str(cores / 'mihomo')
                command = [binary, '-d', str(root / 'mihomo-home'), '-f', str(path)]
                check = [*command, '-t']
                process_class = ReadyMihomoProcess
            else:
                binary = str(cores / 'sing-box')
                command = [binary, 'run', '-c', str(path)]
                check = [binary, 'check', '-c', str(path)]
                process_class = BoundCoreProcess
            parse = subprocess.run(check, capture_output=True, text=True, timeout=15, env=env)
            require(parse.returncode == 0, f'{name} configuration rejected: {parse.stdout}{parse.stderr}')
            core = process_class(stack, command, root / (name + '.log'), env)
            self.processes[name] = core
            reservations[name].close()
            core.start(ports[name], host)

    def authorize(self, target, request, reply):
        target.respond(request, reply)
        self.expected[target].append({'size': len(request), 'sha256': sha256(request)})

    def audit(self):
        for target, expected in self.expected.items():
            actual = target.snapshot()
            require(len(actual) == len(expected), f'{target.address} delivery count {len(actual)} expected {len(expected)}')
            for row, want in zip(actual, expected):
                if 'peer' in want:
                    require(row == want, 'Direct reachability control changed')
                else:
                    require(row['peer'][0] == EGRESS and row['size'] == want['size'] and row['sha256'] == want['sha256'],
                            'Wrong egress, truncated request, or changed application bytes')

    def observe(self):
        return {'application_target': list(self.target.address), 'forbidden_target': list(self.trap.address),
                'overseas_listener': [OVERSEAS, self.overseas_port],
                'allowed_deliveries': self.target.snapshot(), 'forbidden_deliveries': self.trap.snapshot()}

    def exchange(self, row, assoc, request_size, reply_size, suffix='business'):
        key = row['id'] + '/' + suffix
        request, reply = payload(key, 'request', request_size), payload(key, 'reply', reply_size)
        self.authorize(self.target, request, reply)
        expected = {'peer': list(self.target.address), 'size': reply_size, 'sha256': sha256(reply)}
        row.setdefault('exchanges', []).append(evidence := {
            'request_size': request_size, 'request_sha256': sha256(request), 'expected_reply': expected})
        assoc.send(self.target.address, request)
        row['sent'] = True
        evidence['actual_reply'] = assoc.receive()
        self.audit()
        require(evidence['actual_reply'] == expected, 'Missing reply or different application payload/address/port')
        return self.target.snapshot()[-1]['peer']

    def offsets(self):
        return {name: len(core.log_path.read_text(errors='replace')) for name, core in self.processes.items()}

    def reject(self, row, assoc, destination, role, reasons):
        offsets = self.offsets()
        request = payload(row['id'], 'rejected', 1200)
        row['negative_request'] = {'target': list(destination), 'sha256': sha256(request), 'size': len(request)}
        assoc.send(destination, request)
        row['sent'] = True
        row['negative_reply'] = assoc.receive()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            self.audit()
            lines = fresh_lines(self.processes[role].log_path, offsets[role])
            matched = next((line for line in lines if all(part in line for part in reasons)), None)
            if matched:
                row['reason'] = {'role': role, 'line': matched}
                break
            time.sleep(.05)
        self.audit()
        require(row['negative_reply'] is None, 'Rejected request received an application reply')
        require('reason' in row, 'No fresh rejection reason from ' + role + ': ' + ' / '.join(reasons))

    def stop(self):
        for core in reversed(list(self.processes.values())):
            core.stop()
            require(core.process is None or core.process.poll() is not None, 'Fixture process was not reaped')
        time.sleep(.2)
        self.target.stop()
        self.trap.stop()


def run_exchange(fixture, row, request_size, reply_size):
    assoc = Association(fixture.stack, fixture.device_port)
    row['association'] = assoc.identity()
    fixture.exchange(row, assoc, request_size, reply_size)


def run_retarget(fixture, row):
    assoc = Association(fixture.stack, fixture.device_port)
    row['association'] = assoc.identity()
    request = payload(row['id'], 'warmup', 1200)
    fixture.authorize(fixture.target, request, request)
    assoc.send(fixture.target.address, request)
    row['sent'] = True
    row['warmup_reply'] = assoc.receive()
    fixture.audit()
    require(row['warmup_reply'] is not None, 'Cannot test retarget without a live association')
    fixture.reject(row, assoc, fixture.trap.address, 'domestic', ('reject',))
    require(assoc.identity() == row['association'], 'Application association changed during retarget')


def run_fault(fixture, row, role, reasons):
    assoc = Association(fixture.stack, fixture.device_port)
    row['association'] = assoc.identity()
    fixture.reject(row, assoc, fixture.target.address, role, reasons)


def run_stop(fixture, row):
    fixture.processes['overseas'].stop()
    run_fault(fixture, row, 'domestic', ('connect: connection refused',))


def run_idle(fixture, row, leaves):
    assoc = Association(fixture.stack, fixture.device_port)
    identity = assoc.identity()
    row['association'] = identity
    leaves['idle-release']['reason'] = 'Full 60000-byte guarded baseline has not passed'
    leaves['idle-recovery']['reason'] = 'Full guarded idle release has not passed'
    old_peer = fixture.exchange(row, assoc, 60000, 60000)
    upstream = psutil.Process(fixture.processes['overseas'].process.pid)

    def descriptors():
        return [item.fd for item in upstream.net_connections(kind='udp') if list(item.laddr) == old_peer]

    idle = leaves['idle-release']
    try:
        idle['old_peer'] = old_peer
        idle['old_udp_fds'] = descriptors()
        require(idle['old_udp_fds'], 'Original overseas UDP socket was not observed')
        sibling = Association(fixture.stack, fixture.device_port)
        start = time.monotonic()
        count = 0
        while time.monotonic() - start < IDLE_WINDOW:
            fixture.exchange(idle, sibling, 1200, 1200, f'sibling-{count}')
            count += 1
            time.sleep(.5)
        idle['window_seconds'] = round(time.monotonic() - start, 3)
        idle['remaining_udp_fds'] = descriptors()
        require(count > 1 and not idle['remaining_udp_fds'], 'Old UDP socket survived the idle window')
        idle.update(status='passed', reason='Old socket released and independent traffic remained live')
    except (OSError, AssertionError, psutil.Error) as error:
        idle.update(status='failed', reason=clean(str(error), fixture.root))
        return
    recovered = leaves['idle-recovery']
    try:
        recovered['association'] = assoc.identity()
        require(recovered['association'] == identity, 'Application SOCKS control/socket/relay changed')
        fixture.exchange(recovered, assoc, 32768, 32768)
        require(assoc.identity() == identity, 'Recovery replaced the application association')
        recovered.update(status='passed', reason='Exact large reply on the original application association')
    except (OSError, AssertionError) as error:
        recovered.update(status='failed', reason=clean(str(error), fixture.root))


def build_cases():
    cases = []
    for guarded in (False, True):
        for size in (1, 1200):
            name = ('guarded' if guarded else 'unbound-control') + f'-echo-{size}'
            cases.append((name, guarded, None, run_exchange, (size, size)))
    for size in (8192, 32768, 60000):
        cases.append((f'guarded-up-{size}', True, None, run_exchange, (size, 1)))
        cases.append((f'guarded-down-{size}', True, None, run_exchange, (1, size)))
    cases.append(('guarded-retarget', True, None, run_retarget, ()))
    for hop in ('first', 'second'):
        for fault, reasons in (('uuid', UUID_REASON), ('ca', CA_REASON), ('sni', SNI_REASON)):
            role = ('domestic' if hop == 'first' else 'overseas') if fault == 'uuid' else (
                'device' if hop == 'first' else 'domestic')
            name = f'{hop}-{fault}'
            cases.append((name, True, name, run_fault, (role, reasons)))
    cases.extend((('overseas-stopped', True, None, run_stop, ()),
                  ('idle-baseline', True, None, run_idle, ())))
    return cases


def persist(directory, report):
    path = directory / 'report.json'
    pending = path.with_suffix('.tmp')
    pending.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    pending.replace(path)


def run_case(directory, cores, case, rows):
    name, guarded, fault, operation, args = case
    row = rows[name]
    fixture = None
    phase = 'fixture'
    with tempfile.TemporaryDirectory(prefix='vui-d2-') as temp:
        root = Path(temp)
        try:
            with ExitStack() as stack:
                fixture = Fixture(stack, root, cores, guarded=guarded, fault=fault)
                phase = 'exercise'
                try:
                    if operation is run_idle:
                        operation(fixture, row, rows)
                    else:
                        operation(fixture, row, *args)
                    row.update(status='passed', reason='Expected observations verified')
                finally:
                    # Observe late traffic after any success or failure; cleanup
                    # must not erase the first assertion or the target ledger.
                    try:
                        fixture.stop()
                        row['cleanup'] = 'Core processes and target workers joined'
                    except (OSError, AssertionError, subprocess.SubprocessError) as error:
                        row['cleanup_error'] = clean(str(error), root)
                    try:
                        fixture.audit()
                    except AssertionError as error:
                        row['final_audit_error'] = str(error)
                    row.update(fixture.observe())
        except (OSError, ValueError, AssertionError, subprocess.SubprocessError, psutil.Error) as error:
            status = 'failed' if row.get('sent') else 'not_run'
            row.update(status=status, reason=clean(f'{phase}: {type(error).__name__}: {error}', root))
        if row['status']=='passed' and ('cleanup_error' in row or 'final_audit_error' in row):
            row.update(status='failed', reason='Final ledger or process cleanup failed')
        logs = {}
        for path in sorted(root.glob('*.log')):
            # Fixed fixtures only. Preserve the failure evidence without keys,
            # configs, upstream download URLs, or datagram contents.
            with path.open('rb') as stream:
                logs[path.name] = clean(stream.read(131_072).decode(errors='replace'), root)
        if logs:
            output = directory / (name + '.json')
            output.write_text(json.dumps(logs, indent=2, sort_keys=True) + '\n')
            row['log'] = output.name


def main():
    output, expected = Path(sys.argv[1]), sys.argv[2]
    output.mkdir(parents=True, exist_ok=True)
    cases = build_cases()
    order = [case[0] for case in cases] + ['idle-release', 'idle-recovery', 'mux-backpressure']
    rows = {name: {'id': name, 'status': 'not_run', 'reason': 'Not reached'} for name in order}
    rows['mux-backpressure']['reason'] = 'Requires a qualified baseline and an independent controlled shared-mux write-block witness'
    report = {'schema': 1, 'base': 'dd4c22eb8a4b98f88bba436cadbebdb49f5ce7c4', 'source': expected,
              'run_id': os.environ.get('GITHUB_RUN_ID'), 'attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
              'verdict': 'blocked', 'scenarios': [rows[name] for name in order]}
    persist(output, report)
    try:
        require(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
                and os.environ.get('GITHUB_REPOSITORY')=='ForceMind/V-UI'
                and sys.platform=='linux' and platform.machine()=='x86_64' and os.geteuid()!=0,
                'D2 execution requires an unprivileged hosted Linux x86_64 runner')
        source = subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip()
        require(re.fullmatch('[0-9a-f]{40}', expected) and source == expected, 'D2 checkout differs from expected source')
        subprocess.run(['git','merge-base','--is-ancestor',report['base'],source], check=True)
        with tempfile.TemporaryDirectory(prefix='vui-d2-cores-') as temp:
            cores = Path(temp)
            report['binaries'] = fetch_cores(cores)
            persist(output, report)
            for case in cases:
                run_case(output, cores, case, rows)
                persist(output, report)
                print(f"{case[0]}: {rows[case[0]]['status']} — {rows[case[0]]['reason']}", flush=True)
    except urllib.error.HTTPError as error:
        report['preflight_error'] = 'Official artifact fetch HTTP ' + str(error.code)
    except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as error:
        report['preflight_error'] = type(error).__name__ + ' during fixed-source preflight'
    report['counts'] = {status: sum(row['status']==status for row in rows.values())
                        for status in ('passed','failed','not_run')}
    bounded = [row for name, row in rows.items() if name != 'mux-backpressure']
    if 'preflight_error' not in report and all(row['status']=='passed' for row in bounded):
        report['verdict'] = 'bounded-scenarios-passed'
    persist(output, report)
    print(json.dumps({'verdict': report['verdict'], **report['counts']}, sort_keys=True))
    return 0 if report['verdict']=='bounded-scenarios-passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
