"""Exact-source helpers for the isolated owner overlay; never a product builder."""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import signal
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

packet = load('owner_base_packet_builder', HERE.parent / 'xudp_packet/build_and_test.py')
c = packet.c
packet_verify = load('owner_base_packet_verify', HERE.parent / 'xudp_packet/verify_evidence.py')
packet_prepare = load('owner_base_packet_prepare', HERE.parent / 'xudp_packet/prepare_evidence.py')
KIND = 'vui-socks-owner-source-experiment'
OVERLAYS = (
    ('sing', 'experiments/socks_idle_recovery/sing_owner.patch'),
    ('sing-vmess', 'experiments/socks_idle_recovery/vmess_session.patch'),
    ('sing-box', 'experiments/socks_idle_recovery/box_owner.patch'),
)
DELTA = {
    'sing': ('protocol/socks/handshake.go', 'protocol/socks/association.go',
             'protocol/socks/association_packet.go', 'common/network/packet_session.go', 'common/bufio/chunk.go'),
    'sing-vmess': ('xudp.go', 'client.go'),
    'sing-box': ('option/simple.go', 'protocol/mixed/inbound.go', 'adapter/packet_session.go',
                 'protocol/vmess/outbound.go', 'protocol/vless/outbound.go',
                 'common/dialer/packet_session.go', 'route/route.go', 'route/conn.go', 'route/packet_session.go'),
}
FINAL_FILES = {family + '/' + name for family, names in DELTA.items() for name in names}
NEW_FILES = {'sing/protocol/socks/association.go', 'sing/protocol/socks/association_packet.go',
             'sing/common/network/packet_session.go', 'sing-box/adapter/packet_session.go',
             'sing-box/common/dialer/packet_session.go', 'sing-box/route/packet_session.go'}
MODES = {'race': ['-race'], 'debug': ['-race', '-tags=debug']}
ENV_KEYS = ('GOOS', 'GOARCH', 'CGO_ENABLED', 'GOTOOLCHAIN', 'GOWORK')
CROSS_PACKAGES = ('github.com/sagernet/sing/common/network', 'github.com/sagernet/sing/common/bufio',
                  'github.com/sagernet/sing/protocol/socks', 'github.com/sagernet/sing-vmess',
                  'github.com/sagernet/sing-vmess/vless', './adapter', './common/dialer',
                  './protocol/mixed', './protocol/vmess', './protocol/vless', './route')

def identity():
    result = packet.identity()
    for path in sorted(HERE.rglob('*')):
        if path.is_file() and path.suffix in ('.py', '.go', '.patch', '.md', '.json'):
            name = str(path.relative_to(ROOT))
            c.need(not path.is_symlink() and c.git('show', 'HEAD:' + name) == path.read_bytes(),
                   'Uncommitted owner source: ' + name)
            result['files'][name] = c.sha256(path)
    return result

def environment(out):
    return {**os.environ, 'GOTOOLCHAIN': 'local', 'GOWORK': 'off', 'GOENV': 'off',
            'CGO_ENABLED': '1', 'GOOS': 'linux', 'GOARCH': 'amd64', 'GOAMD64': 'v1',
            'GOEXPERIMENT': '', 'GOFLAGS': '-mod=readonly', 'GOPROXY': 'https://proxy.golang.org',
            'GOSUMDB': 'sum.golang.org', 'GOPRIVATE': '', 'GONOPROXY': '', 'GONOSUMDB': '',
            'GOMODCACHE': str(out / 'modules'), 'GOCACHE': str(out / 'cache')}

def command(args, cwd, env, path, timeout=1200):
    """Only the owned process group is signalled; descendants cannot count as joined."""
    args = [str(a) for a in args]
    proc = subprocess.Popen(args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = proc.communicate(timeout=15)
    try:
        os.killpg(proc.pid, 0)
    except ProcessLookupError:
        group_gone = True
    else:
        os.killpg(proc.pid, signal.SIGKILL)
        group_gone = False
    path.with_suffix('.stdout').write_text(stdout)
    path.with_suffix('.stderr').write_text(stderr)
    packet.save(path.with_suffix('.exit.json'), {
        'args': args, 'returncode': proc.returncode, 'environment': {k: env[k] for k in ENV_KEYS if k in env},
        'timeout': timed_out, 'leader_joined': proc.poll() is not None, 'process_group_gone': group_gone})
    c.need(not timed_out and group_gone, 'Owned command timed out or left descendants: ' + path.name)
    return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)

def full_manifest(trees):
    return {family + '/' + name: digest for family, tree in trees.items()
            for name, digest in packet.manifest(tree).items()}

def validate_delta(before, after, expected):
    changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    c.need(changed == set(expected), 'Unexpected overlay delta: ' + str(changed ^ set(expected)))
    return {name: {'base': before.get(name), 'final': after.get(name)} for name in sorted(changed)}

def read_contract():
    result = c.read_json(HERE / 'TEST_CASES.json')
    c.need(result['schema_version'] == 1 and result['kind'] == KIND, 'Wrong owner test contract')
    c.need(result['groups'] and result['files'], 'Missing owner tests')
    labels, targets, sources = set(), set(), set()
    for group in result['groups']:
        c.need(group['label'] not in labels and group['leaves'] and
               len(group['leaves']) == len(set(group['leaves'])) and group['regex'].startswith('^Test'),
               'Duplicate/empty owner test group')
        labels.add(group['label'])
    for item in result['files']:
        source, target = Path(item['source']), Path(item['target'])
        key = item['family'] + '/' + item['target']
        c.need(item['family'] in DELTA and key not in targets and item['source'] not in sources and
               target.name.endswith('_test.go') and not target.is_absolute() and '..' not in target.parts and
               not source.is_absolute() and source.parts and source.parts[0] == 'tests' and
               '..' not in source.parts and source.name.endswith('_test.go'), 'Invalid/duplicate test destination')
        targets.add(key); sources.add(item['source'])
    return result
