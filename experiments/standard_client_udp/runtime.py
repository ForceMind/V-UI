#!/usr/bin/env python3
"""Strict stock-client research. Capacity RED is a nonzero exit, never expected success."""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LEGACY = 'afbdba100c5f0e3dc6888684fdbab31980720df3'
LEGACY_TREE = '996d37d925be8edd18cbf921d3cc580f43c0730d'
STOCK = '07d2866a6c908281a56053f0e7d454918501592f2f258526f2ebe826dab58793'
MIHOMO = '3122d100e8177501776109f1a6253a694611627cf4d7c7ec82705855cf8626a8'
OPTION = 'experimental_socks_udp_idle_recovery'
ARMS = {'stock': ('C', 'D'), 'candidate': ('C', 'D'), 'overseas-stock': ('B',)}
KIND = 'vui-stock-client-udp-research'

def need(value, message):
    if not value: raise ValueError(message)

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()

def save(path, data):
    text = json.dumps(data, sort_keys=True, indent=2)
    need('PRIVATE KEY-----' not in text and 'BEGIN CERTIFICATE' not in text, 'Material in evidence')
    Path(path).write_text(text + '\n')

def git(repo, *args):
    return subprocess.check_output(['git', *args], cwd=repo, timeout=30)

def source(repo, *, legacy=False):
    repo = Path(repo).resolve(strict=True)
    commit, tree = (git(repo, 'rev-parse', ref).decode().strip() for ref in ('HEAD', 'HEAD^{tree}'))
    if legacy: need((commit, tree) == (LEGACY, LEGACY_TREE), 'Frozen legacy commit/tree changed')
    need(not git(repo, 'diff', '--name-only', 'HEAD', '--').strip(), 'Tracked source differs from HEAD')
    need(not git(repo, 'ls-files', '--others', '--exclude-standard').strip(), 'Untracked source can shadow verified imports')
    files = {}
    for raw in git(repo, 'ls-files', '-z').split(b'\0'):
        if not raw: continue
        name = raw.decode(); path = repo / name
        need(path.is_file() and not path.is_symlink(), 'Source missing or linked: ' + name)
        need(path.read_bytes() == git(repo, 'show', 'HEAD:' + name), 'Source bytes differ: ' + name)
        files[name] = sha(path)
    return {'commit': commit, 'tree': tree, 'files': files}

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def load_framework(legacy):
    sys.path[:0] = [str(HERE), str(legacy / 'experiments/xudp_packet')]
    p = load('packet_boundary', legacy / 'experiments/xudp_packet/packet_boundary.py')
    c = p.load_reuse(legacy)
    c.base.runtime_modules()
    sys.path.insert(0, str(HERE))
    load('socks_observation', HERE / 'socks_observation.py')
    load('lifecycle', HERE / 'lifecycle.py')
    runner = load('case_runner', HERE / 'case_runner.py')
    runner.role_fixture, runner.configs, runner.cleanup = role_fixture, configs, cleanup
    return p, c, runner

def plan(p, arm, stage):
    need(arm in ARMS and stage in ARMS[arm], 'Unknown arm/stage')
    cases = deepcopy([c for c in p.plan(stage) if c['protocol'] == 'vless'])
    server = 'official' if arm == 'stock' else 'candidate'
    overseas = 'official' if arm in ('stock', 'overseas-stock') else 'candidate'
    for case in cases:
        case['roles'] = {'domestic': server, 'overseas': overseas, 'device-mihomo': 'mihomo'}
    expected = {'C': (26, 66), 'D': (2, 4), 'B': (1, 4)}[stage]
    need((len(cases), len(p.leaf_ids(cases))) == expected, 'Frozen VLESS plan coverage changed')
    return cases

def owner_absent(value):
    if isinstance(value, dict):
        need(OPTION not in value, 'Every role must keep owner option absent')
        for child in value.values(): owner_absent(child)
    elif isinstance(value, list):
        for child in value: owner_absent(child)

def binaries(args, legacy_identity):
    result = {}
    for name, path, digest, version, flags in (
        ('official', args.official, STOCK, '1.14.2', ['version']),
        ('mihomo', args.mihomo, MIHOMO, '1.19.32', ['-v'])):
        path = path.resolve(strict=True)
        need(path.is_file() and os.access(path, os.X_OK) and sha(path) == digest, 'Stock binary mismatch: ' + name)
        call = subprocess.run([str(path), *flags], capture_output=True, text=True, timeout=15)
        need(call.returncode == 0 and version in call.stdout, 'Stock version mismatch: ' + name)
        result[name] = {'path': str(path), 'sha256': digest, 'version': version, 'version_output': call.stdout}
    if args.arm != 'stock':
        need(args.candidate and args.candidate_provenance and args.build_evidence, 'Missing candidate/provenance/build evidence')
        path = args.candidate.resolve(strict=True)
        data = json.loads(args.candidate_provenance.read_text())
        sys.path.insert(0, str(args.legacy_repository / 'experiments/socks_idle_recovery'))
        owner = load('standard_owner_adapter', args.legacy_repository / 'experiments/socks_idle_recovery/runtime_adapter.py')
        rt = owner.load_runtime(args.legacy_repository)
        identity = rt.h.identity()
        need(identity['vui_commit'] == legacy_identity['commit'], 'Candidate source is not frozen legacy')
        owner.verify_provenance(rt, data, identity)
        rt.verify_build.verify(args.build_evidence, rt.h.read_contract())
        need(json.loads((args.build_evidence/'provenance.json').read_text()) == data, 'Build provenance differs')
        need(data['binary_sha256'] == sha(path), 'Candidate executable hash differs')
        call = subprocess.run([str(path), 'version'], capture_output=True, text=True, timeout=15)
        need(call.returncode == 0 and call.stdout.splitlines()[0] == 'sing-box version '+data['version'], 'Candidate version differs')
        result['candidate'] = {'path': str(path), 'sha256': sha(path), 'version': data['version'],
            'provenance_sha256': sha(args.candidate_provenance), 'build_source_identity': identity,
            'build_evidence_files': owner.evidence_files(args.build_evidence)}
    return result

def configs(f, case, product, directory):
    need(case['protocol'] == 'vless', 'No extra protocol scope')
    domestic = f.guarded_config(udp_timeout='2s')
    call = subprocess.run([sys.executable, str(product / 'experiments/standard_client_udp/export_node.py'),
                           '--port', str(f.ingress_port)], cwd=product, env=f.env,
                          text=True, capture_output=True, timeout=20)
    need(call.returncode == 0, 'Current product exporter failed: '+call.stderr)
    artifact = json.loads(call.stdout); node = artifact['node']
    need(node['type'] == 'vless' and node['server'] == '127.0.0.1' and node['port'] == f.ingress_port
         and node['uuid'] == '11111111-1111-4111-8111-111111111111' and node['udp'] is True
         and node['tls'] is True and node.get('skip-cert-verify') is not True, 'Ordinary entry node changed')
    need('22222222-2222-4222-8222-222222222222' not in json.dumps(artifact) and '127.0.0.2' not in json.dumps(artifact),
         'Overseas credentials/address in device export')
    config = {'mixed-port': f.client_port, 'bind-address': '127.0.0.1', 'allow-lan': False,
        'mode': 'rule', 'log-level': 'debug', 'ipv6': False, 'find-process-mode': 'off',
        'geo-auto-update': False, 'profile': {'store-selected': False, 'store-fake-ip': False},
        'proxies': [deepcopy(node)], 'rules': ['MATCH,' + node['name']]}
    need(config['proxies'] == [node], 'Ordinary node fields changed by shell')
    owner_absent(config); owner_absent(domestic)
    save(directory / 'export-identity.json', {'artifact': artifact,
        'artifact_sha256': hashlib.sha256(canonical(artifact)).hexdigest(),
        'node_sha256': hashlib.sha256(canonical(node)).hexdigest(),
        'consumer_node_sha256': hashlib.sha256(canonical(config['proxies'][0])).hexdigest(),
        'node_fields_unchanged': True, 'consumer': 'ordinary-yaml-node-in-listener-shell',
        'server_configuration': 'isolated-research-guarded-not-product-udp',
        'http_api_export_executed_here': False, 'hash_scope': 'raw-fake-fixture-export-before-redaction'})
    return domestic, config

def role_fixture(c, module, output, known, case, records, product):
    class Fixture(module.RestrictedUdpFixture):
        def process(self, label, config, *, mihomo=False, env=None):
            need(label in case['roles'] and mihomo is (label == 'device-mihomo'), 'Unexpected process role/type')
            owner_absent(config)
            origin = case['roles'][label]; binary = known[origin]
            row = {'case': self.label, 'role': label, 'origin': origin, 'binary': binary['path'],
                'sha256': binary['sha256'], 'version': binary['version'], 'mihomo': mihomo,
                'classification': 'experimental' if origin == 'candidate' else 'official',
                'owner_recovery_enabled': False, 'owner_option_absent': True,
                'config_sha256': hashlib.sha256(canonical(config)).hexdigest(),
                'parser_passed': False, 'launches': []}
            records.append(row); save(output / 'process-binaries.json', records)
            previous = self.singbox
            if not mihomo: self.singbox = binary['path']
            try: core = super().process(label, config, mihomo=mihomo, env=env)
            finally: self.singbox = previous
            need(Path(core.command[0]).resolve() == Path(binary['path']), 'Actual role command differs')
            row['parser_passed'] = True; row['log_file'] = core.log_path.name
            original = core.start
            def start(*args, **kwargs):
                need(sha(core.command[0]) == binary['sha256'], 'Binary changed before start')
                launch = {'ready': False, 'sha256': binary['sha256']}; row['launches'].append(launch)
                try:
                    value = original(*args, **kwargs); launch['ready'] = True
                    launch['birth_time'] = importlib.import_module('psutil').Process(core.process.pid).create_time()
                    return value
                finally:
                    launch['pid'] = core.process.pid if core.process else None
                    save(output / 'process-binaries.json', records)
            core.start = start
            return core
    return Fixture

def cleanup(f, before_threads, psutil):
    """Independently prove disposal. Preserve failed data audit separately."""
    audit_errors = []; processes = []
    for core in f.processes:
        need(core.process is not None and core.process.poll() is not None and not psutil.pid_exists(core.process.pid),
             'Owned process survived stop/wait')
        processes.append({'pid': core.process.pid, 'returncode': core.process.returncode, 'reaped': True})
    for target in f.targets:
        try: target.audit()
        except Exception as exc: audit_errors.append({'type': type(exc).__name__, 'message': str(exc)})
        need(target.closed and not target.thread.is_alive(), 'Target worker survived cleanup')
        stream = target.stream if hasattr(target, 'stream') else target.server.socket
        need(stream.fileno() == -1, 'Target socket survived cleanup')
    need(not f.root.exists(), 'Temporary fixture material survived')
    need(getattr(f, 'application_close_proofs', None) and all(x['control_fd'] == x['application_fd'] == -1 for x in f.application_close_proofs),
         'Application/control descriptors not explicitly closed')
    need(not [t for t in threading.enumerate() if t not in before_threads], 'Unjoined fixture worker')
    need(f.answers is not None and f.answers.pending() == 0, 'DNS worker retained work')
    return {'processes': processes, 'threads_joined': True, 'fixture_removed': True,
            'all_target_workers_joined': True, 'application_sockets_closed': True,
            'application_close_proofs': f.application_close_proofs,
            'execution_safe': True, 'data_audit_errors': audit_errors,
            'original_cleanup_gate_passed': not audit_errors}

def tuple_ok(value, host):
    return isinstance(value, list) and len(value) == 2 and value[0] == host and type(value[1]) is int and 0 < value[1] <= 65535

def verify_associations(case, row):
    associations = row.get('application_associations', [])
    proofs = row['cleanup'].get('application_close_proofs', [])
    expected = 2 if case['mode'] == 'close' and row['recovery_attempted'] else 1
    need(len(associations) == expected and [a['id'] for a in associations] == list(range(expected)),
         'Wrong created application association identities')
    need(Counter(p['id'] for p in proofs) == Counter(range(expected)) and
         all(p['control_fd'] == p['application_fd'] == -1 for p in proofs), 'Application close proof missing/duplicated/open')
    for a in associations:
        for name in ('control', 'application'):
            item = a[name]
            need(type(item['fd']) is int and item['fd'] >= 0 and type(item['inode']) is int and item['inode'] > 0
                 and tuple_ok(item['local'], '127.0.0.1'), 'Invalid created application descriptor identity')
        need(a['control']['inode'] != a['application']['inode'] and tuple_ok(a['relay'], '127.0.0.1'), 'Ambiguous association sockets')
        need(tuple_ok(a['control']['peer'], '127.0.0.1') and a['application']['peer'] is None, 'Wrong control/application socket kinds')
    for packet in row['packets']:
        wanted = 1 if expected == 2 and packet['leaf'].endswith('/recover') else 0
        need(packet.get('association_id') == wanted, 'Packet replaced original application object')
        a = associations[wanted]
        need(packet['application_socket'] == a['application']['local'] and packet['socks_relay'] == a['relay'],
             'Packet tuple differs from actual created association')

def verify_case(p, case, row, roles, known, output, identity):
    expected = p.leaf_ids([case]); started = [x['leaf'] for x in row['packets']]
    need(started == expected[:len(started)] and row['unexecuted_leaves'] == expected[len(started):],
         'Missing/extra/duplicate or out-of-order started leaves')
    need(row['cleanup'].get('execution_safe') is True, 'Unsafe cleanup prevents continuing')
    p.verify_cleanup(row, roles)
    verify_associations(case, row)
    need(all(x['returncode'] == 0 for x in row['cleanup']['processes']), 'Core crashed or exited abnormally')
    need(Counter(r['role'] for r in roles) == Counter(case['roles'].keys()), 'Wrong runtime role set')
    for r in roles:
        origin = case['roles'][r['role']]; b = known[origin]
        need(r['origin'] == origin and r['binary'] == b['path'] and r['sha256'] == b['sha256']
             and r['version'] == b['version'] and r['classification'] == ('experimental' if origin == 'candidate' else 'official')
             and r['parser_passed'] is True and r['owner_option_absent'] is True and r['owner_recovery_enabled'] is False,
             'Role input differs from actual process')
        need(len(r['launches']) == 1 and r['launches'][0]['ready'] is True and r['launches'][0]['sha256'] == b['sha256'],
             'Role launched more than once or not ready')
    ledger = row['target_ledger']
    need(ledger['closed'] and ledger['thread_joined'] and ledger['socket_closed'] and not ledger['errors'], 'Target not disposed or recorder error')
    need(ledger['received_count'] == len(ledger['actual_datagrams']) == len(ledger['packets']), 'Raw target ledger incomplete')
    for raw, item in zip(ledger['actual_datagrams'], ledger['packets']):
        need(raw['target'] == item['target'] and raw['source'] == item['source'] and raw['payload'] == item['request_actual']
             and raw['flags'] == 0 and raw['ancillary_count'] == 0, 'Raw datagram/ledger mismatch')
    need(not row['dns']['ordinary'] and not row['dns']['business'] and not row['dns']['expected'], 'Unexpected DNS/fallback')
    full = not row['errors'] and not row['unexecuted_leaves'] and not row['cleanup']['data_audit_errors']
    by_leaf = {x['leaf']: x for x in ledger['packets']}
    need(len(by_leaf) == len(ledger['packets']) and set(by_leaf) <= set(started), 'Duplicate/unstarted target leaf')
    need(len(by_leaf) <= ledger['planned_count'] <= len(started), 'Target announcement count differs')
    if case['mode'] != 'close' and row['packets']:
        need(len({tuple(x['application_socket']) for x in row['packets']}) == 1 and len({tuple(x['socks_relay']) for x in row['packets']}) == 1,
             'Original application/relay tuple changed')
    need(all(x['target'] == [ledger['host'], ledger['port']] for x in ledger['packets']), 'Target tuple differs from actual bound target')
    if case['mode'] == 'packets' and ledger['packets']:
        need(len({tuple(x['source']) for x in ledger['packets']}) == 1, 'Target source changed in live association')
    if full:
        target_leaves = [case['id']+'/'+x['id'] for x in case['packets'] if x['direction'] != 'local_emsgsize']
        need(Counter(by_leaf.keys()) == Counter(target_leaves) and ledger['planned_count'] == ledger['received_count'] == len(target_leaves),
             'Complete target leaf/count mismatch')
    sent = 0
    for spec, packet in zip(case['packets'], row['packets']):
        first = sent == 0 or (case['mode'] == 'close' and spec['id'] == 'recover')
        need(packet['leaf'] == case['id']+'/'+spec['id'] and packet['direction'] == spec['direction'] and packet['first_udp'] is first,
             'Packet plan identity/direction/first-packet state differs')
        need(tuple_ok(packet['application_socket'], '127.0.0.1') and tuple_ok(packet['socks_relay'], '127.0.0.1') and
             tuple_ok(packet['requested_target'], '127.0.0.3') and packet['requested_target'] == [ledger['host'], ledger['port']],
             'Missing or wrong complete application/relay/target tuple')
        request, reply = p.packet_bodies(packet['leaf'], spec)
        need(packet['request'] == p.ident(request) and packet['expected_reply'] == p.ident(reply), 'Input bytes changed')
        calls = packet.get('send_calls', [])
        need(len(calls) == 1, 'Application packet was not sent exactly once')
        call = calls[0]
        wire = b'\0\0\0\x01' + socket.inet_aton(packet['requested_target'][0]) + packet['requested_target'][1].to_bytes(2, 'big') + request
        need(call['wire'] == p.ident(wire) and call['relay'] == packet['socks_relay'] and call['completed'] >= call['started'], 'Raw send identity differs')
        if spec['direction'] == 'local_emsgsize':
            need(call.get('errno') == 90 and 'return' not in call, 'Oversize send was not locally rejected')
        else:
            need(call.get('return') == len(wire) and 'errno' not in call and packet['send_state'] == 'sent', 'Application send did not finish')
        replies = packet.get('reply_calls', [])
        need(len(replies) <= 1, 'More than one application reply consumed')
        decoded = packet.get('decoded_reply_before_assertion')
        evidence_status = 'raw-and-decoded-reply-verified' if decoded else 'no-decoded-reply-no-explicit-reject-claim'
        if decoded:
            need(len(replies) == 1, 'Decoded reply lacks raw receive')
            n = decoded['payload']['bytes']
            need(0 <= n <= len(reply) and decoded['payload'] == p.ident(reply[:n]), 'Decoded reply is not exact expected prefix')
            raw_wire = b'\0\0\0\x01' + socket.inet_aton(decoded['host']) + decoded['port'].to_bytes(2, 'big') + reply[:n]
            need(replies[0]['wire'] == p.ident(raw_wire) == packet['socks_reply_wire'] and replies[0]['relay'] == packet['socks_relay']
                 and replies[0]['flags'] == 0 and replies[0]['ancillary_count'] == 0, 'Raw reply tuple/payload cannot verify decoded evidence')
        for window_name, terminal in (('target_window', 'zero_observed'), ('reply_window', 'timeout')):
            window = packet.get(window_name, {})
            if window.get('status') == terminal:
                need(window['deadline'] == window['started'] + 4 and window['completed'] >= window['deadline'], 'Incomplete original failed observation window')
        if packet['status'] in ('full_delivery_passed', 'local_emsgsize_verified', 'capacity_failed_exact_prefix'):
            p.verify_packet(case, spec, packet, by_leaf.get(packet['leaf']), first)
        else:
            need(packet['status'] == 'error' and packet.get('error') and packet['full_delivery_passed'] is False,
                 'Nonterminal or contradictory failure')
            item = by_leaf.get(packet['leaf'])
            if item:
                n = item['request_actual']['bytes']
                need(0 <= n <= len(request) and item['request_actual'] == p.ident(request[:n])
                     and item['request_expected'] == p.ident(request) and item['source'][0] == '127.0.0.4'
                     and item['target'] == packet['requested_target'] and tuple_ok(item['source'], '127.0.0.4')
                     and item['request_exact_prefix'] is True and item['request_full'] is (n == len(request)),
                     'Failed prefix/target/source unaccounted')
                sent_reply = reply if spec['direction'] in ('downlink', 'uplink_ack') else request[:n]
                need(item['reply_sent'] == p.ident(sent_reply) and item['sendto_return'] == len(sent_reply), 'Failed target reply incomplete')
            full = False
        packet['io_verification'] = evidence_status
        if packet['direction'] != 'local_emsgsize':
            sent += 1
            full = full and packet['full_delivery_passed']
        else:
            need(packet.get('errno') == 90 and packet.get('local_zero_delivery') is True and packet.get('socks_wire_bytes') == 65508,
                 'Missing local EMSGSIZE')
    if full and case['mode'] in ('close', 'idle'):
        p.verify_lifecycle(case, row, roles)
        observer = load('standard_verify_socks', HERE/'verify_socks_observation.py')
        observer.verify_case(json.loads((output/case['id']/'socks-association-observation.json').read_text()),
            case, row, roles, identity, json.loads((output/case['id']/'lifecycle-observation.json').read_text()))
    return {'execution_safe': True, 'case_gate_passed': bool(full), 'started': len(started),
        'planned': len(expected), 'unexecuted': row['unexecuted_leaves']}

def execute(args):
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'kind': KIND, 'arm': args.arm, 'stage': args.stage,
              'product_qualified': False, 'original_suite_passed': False,
              'observations_complete': False, 'capacity_gate_passed': False, 'cases': [], 'errors': []}
    try:
        identities = {'product': source(ROOT), 'legacy': source(args.legacy_repository, legacy=True)}
        known = binaries(args, identities['legacy'])
        save(args.output/'input-identity.json', {'source_identity': identities, 'binaries': known})
        with tempfile.TemporaryDirectory(prefix='vui-stock-client-data-') as data:
            os.environ.update(VUI_DATA_DIR=data, VUI_TEST_CORES=str(args.official.parent), VUI_TEST_MIHOMO=str(args.mihomo))
            p, c, runner = load_framework(args.legacy_repository)
            cases = plan(p, args.arm, args.stage); save(args.output/'plan.json', cases)
            records = []; save(args.output/'process-binaries.json', records)
            with c.base.experiment_environment(args.output/'frozen-fixture'), (args.output/'suite.log').open('w') as log:
                with redirect_stdout(log), redirect_stderr(log):
                    module, _ = c.base.runtime_modules(); psutil = importlib.import_module('psutil')
                    stage_started = time.monotonic()
                    for case in cases:
                        need(time.monotonic() - stage_started < 1200, 'Stage exceeded twenty-minute budget; remaining leaves unexecuted')
                        row = runner.run_case(case,c,module,args.output,known,records,psutil,ROOT)
                        report['cases'].append(row)
                        owned = [r for r in records if r['case'] == case['id']]
                        try: row['verification'] = verify_case(p,case,row,owned,known,args.output,identities)
                        except Exception as exc:
                            row['verification'] = {'execution_safe': False, 'error': str(exc), 'case_gate_passed': False}
                            save(args.output/case['id']/'result.json', row)
                            raise
                        save(args.output/case['id']/'result.json', row)
            report['observations_complete'] = len(report['cases']) == len(cases)
            report['capacity_gate_passed'] = report['observations_complete'] and all(r['verification']['case_gate_passed'] for r in report['cases'])
        need(source(ROOT) == identities['product'] and source(args.legacy_repository,legacy=True) == identities['legacy'], 'Source changed during run')
        for b in known.values(): need(sha(b['path']) == b['sha256'], 'Binary changed during run')
    except Exception as exc:
        report['errors'].append({'type':type(exc).__name__,'message':str(exc)})
        (args.output/'runner-error.log').write_text(traceback.format_exc())
    if 'c' in locals():
        report = c.life.scrub(report)
        for path in args.output.rglob('*'):
            if path.is_file() and path.suffix == '.log': path.write_text(c.life.clean(path.read_text()))
            elif path.is_file() and path.suffix == '.json': save(path,c.life.scrub(json.loads(path.read_text())))
    save(args.output/'result.json',report)
    files = {str(p.relative_to(args.output)):sha(p) for p in sorted(args.output.rglob('*')) if p.is_file()}
    save(args.output/'manifest.json',{'kind': KIND+'-text-evidence','files':files})
    print(json.dumps({k:report[k] for k in ('arm','stage','observations_complete','capacity_gate_passed','errors')}),flush=True)
    return 0 if report['capacity_gate_passed'] and not report['errors'] else 1

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-repository',type=Path,required=True)
    parser.add_argument('--official',type=Path,required=True)
    parser.add_argument('--mihomo',type=Path,required=True)
    parser.add_argument('--candidate',type=Path)
    parser.add_argument('--candidate-provenance',type=Path)
    parser.add_argument('--build-evidence',type=Path)
    parser.add_argument('--arm',choices=ARMS,required=True)
    parser.add_argument('--stage',choices=('C','D','B'),required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    for key in ('legacy_repository','official','mihomo','output','candidate','candidate_provenance','build_evidence'):
        value=getattr(args,key)
        if value is not None: setattr(args,key,value.resolve())
    need(args.stage in ARMS[args.arm], 'Stage outside arm')
    return execute(args)

if __name__ == '__main__': raise SystemExit(main())
