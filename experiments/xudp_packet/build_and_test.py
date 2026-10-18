#!/usr/bin/env python3
"""Fixed-source packet experiment: local runner binaries, text-only evidence."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'experiments/xudp_mux/runtime'))
import common as c

SING = '87c33f17688fe3dd80eeabef7c3f7a69152ccd42'
KIND = 'vui-complete-packet-source-experiment'
SING_FILES = (
    'common/network/direct.go', 'common/network/packet.go',
    'common/bufio/conn.go', 'common/bufio/packet_complete.go',
    'common/bufio/copy_direct_posix.go', 'common/bufio/copy_direct_windows.go',
    'common/bufio/packet_batch_mmsg.go', 'common/bufio/packet_batch_msgx_darwin.go',
    'protocol/socks/handshake.go',
)
PATCHES = (*c.PATCHES, 'experiments/xudp_packet/receive.patch')
PACKAGES = ('common/network', 'common/bufio', 'protocol/socks')
EXTRA_TESTS = {
    'experiments/xudp_mux/mux_writer_test.go': 'sing-vmess/mux_writer_test.go',
    'experiments/xudp_mux/mux_encrypted_test.go': 'sing-vmess/mux_encrypted_test.go',
    'experiments/xudp_mux/mux_encrypted_cancel_test.go': 'sing-vmess/mux_encrypted_cancel_test.go',
    'experiments/xudp_mux/route_mux_test.go': 'sing-box/route/route_mux_test.go',
    'experiments/xudp_address/route_lifecycle_test.go': 'sing-box/route/route_lifecycle_test.go',
}
EXTRA_CHECKS = {kind+'-'+mode for kind in ('mux-unit','mux-encrypted','mux-encrypted-cancel')
                for mode in ('race','debug')} | {'mux-route-race'}


def save(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def identity():
    result = c.source_identity()
    paths = set(HERE.rglob('*')) | set((ROOT/'experiments/xudp_mux').glob('*'))
    for path in sorted(paths):
        if path.is_file() and path.suffix in ('.py', '.go', '.patch', '.md', '.json'):
            name = str(path.relative_to(ROOT))
            c.need(not path.is_symlink() and c.git('show', 'HEAD:' + name) == path.read_bytes(),
                   'Uncommitted packet source: ' + name)
            result['files'][name] = c.sha256(path)
    return result


def command(args, cwd, env, path, timeout=1200):
    proc = subprocess.run([str(a) for a in args], cwd=cwd, env=env, text=True,
                          capture_output=True, timeout=timeout)
    path.with_suffix('.stdout').write_text(proc.stdout)
    path.with_suffix('.stderr').write_text(proc.stderr)
    save(path.with_suffix('.exit.json'), {'args': [str(a) for a in args], 'returncode': proc.returncode, 'environment': {k: env[k] for k in ('GOOS','GOARCH','CGO_ENABLED','GOTOOLCHAIN','GOWORK') if k in env}})
    return proc


def manifest(tree):
    return {str(p.relative_to(tree)): c.sha256(p) for p in sorted(tree.rglob('*'))
            if p.is_file() and '.git' not in p.parts}


def graph(text, sing, vmess):
    decoder, rows = json.JSONDecoder(), []
    remaining = text.strip()
    while remaining:
        row, end = decoder.raw_decode(remaining)
        rows.append(row)
        remaining = remaining[end:].lstrip()
    source = [row for row in rows if row['Path'] == 'github.com/sagernet/sing']
    c.need(len(source) == 1, 'Missing fixed packet module')
    row = source[0]
    c.need(row.get('Replace', {}).get('Path') == str(sing) and
           not row['Replace'].get('Version') and Path(row.get('Dir', '')).resolve() == sing,
           'Packet dependency did not resolve to exact source')
    # Reuse the frozen graph verifier after explicitly validating the one new
    # local replacement. Its normalized versions must equal mechanism graph.
    del row['Replace']
    normalized = c.builder.verify_module_graph('\n'.join(json.dumps(x) for x in rows), vmess)
    return normalized


def validate_mechanism(directory, source_id, sources, compiler_sha, normalized):
    manifest_data = c.read_json(directory / 'MANIFEST.json')
    members = manifest_data['files']
    c.need(len({x['path'] for x in members}) == len(members), 'Duplicate manifest path')
    c.need(manifest_data['count'] == len(members) and
           {p.name for p in directory.iterdir() if p.is_file()} ==
           {x['path'] for x in members} | {'MANIFEST.json'}, 'Mechanism evidence set changed')
    for entry in members:
        p = directory / entry['path']
        c.need(p.name == entry['path'] and not p.is_symlink() and p.stat().st_size == entry['bytes'] and
               c.sha256(p) == entry['sha256'], 'Mechanism manifest mismatch')
    data = c.read_json(directory / 'mux-provenance.json')
    c.need(data['vui_commit'] == source_id['vui_commit'] and
           data['go_binary_sha256'] == compiler_sha and
           c.read_json(directory / 'mux-normalized-modules.json') == normalized,
           'Mechanism identity/compiler/dependency graph mismatch')
    for name in ('mux.go', 'mux_writer.go', 'xudp.go'):
        c.need(data['source_sha256'][name] == sources['sing-vmess/' + name]['after'],
               'Previously validated mux source differs: ' + name)
    c.need(data['route_sha256'] == sources['sing-box/route/conn.go']['after'],
           'Previously validated route source differs')
    c.need(data['patches'] == [{'name': Path(p).name, 'sha256': source_id['files'][p]}
                               for p in c.PATCHES], 'Mechanism patch chain mismatch')
    verified = c.read_json(directory / 'VERIFICATION.json')
    c.need(verified['product_qualified'] is False and verified['unit'] ==
           {'unit-race': 20, 'unit-debug': 20}, 'Mechanism tests not verified')


def build(args):
    source_id = identity()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    evidence = out / 'evidence'
    evidence.mkdir()
    env = {**os.environ, 'GOTOOLCHAIN': 'local', 'GOWORK': 'off', 'GOENV': 'off',
           'CGO_ENABLED': '1', 'GOOS': 'linux', 'GOARCH': 'amd64', 'GOAMD64': 'v1',
           'GOEXPERIMENT': '', 'GOFLAGS': '-mod=readonly', 'GOPROXY': 'https://proxy.golang.org',
           'GOSUMDB': 'sum.golang.org', 'GOPRIVATE': '', 'GONOPROXY': '', 'GONOSUMDB': '',
           'GOMODCACHE': str(out / 'modules'), 'GOCACHE': str(out / 'cache')}
    go = shutil.which('go')
    c.need(go is not None, 'Fixed existing runner compiler required')
    compiler = command([go, 'version'], ROOT, env, evidence / 'compiler')
    c.need(compiler.returncode == 0 and compiler.stdout.strip() ==
           'go version ' + c.builder.GO_VERSION + ' linux/amd64', 'Wrong Go compiler')
    fixed = {'sing-box': (args.sing_box, c.builder.SING_BOX),
             'sing-vmess': (args.sing_vmess, c.builder.SING_VMESS), 'sing': (args.sing, SING)}
    trees = {}
    for family, (path, commit) in fixed.items():
        original = c.builder.fixed_checkout(path, commit)
        target = out / family
        shutil.copytree(original, target, ignore=shutil.ignore_patterns('.git'))
        trees[family] = target
    before = {family + '/' + name: value for family, tree in trees.items()
              for name, value in manifest(tree).items()}
    for index, name in enumerate(PATCHES):
        target = trees['sing' if index == 4 else ('sing-vmess' if index in (0, 2) else 'sing-box')]
        for suffix, extra in (('check', ['--check']), ('apply', [])):
            result = command(['git', 'apply', *extra, ROOT / name], target, env,
                             evidence / f'patch-{index}-{suffix}')
            c.need(result.returncode == 0, 'Patch did not apply: ' + name)
    formatted = [trees['sing'] / p for p in SING_FILES] + [trees['sing-vmess'] / p for p in ('mux.go', 'mux_writer.go')]
    result = command([Path(go).with_name('gofmt'), '-w', *formatted], ROOT, env, evidence / 'gofmt-source')
    c.need(result.returncode == 0, 'gofmt source failed')
    after = {family + '/' + name: value for family, tree in trees.items()
             for name, value in manifest(tree).items()}
    changed = {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}
    expected = set(c.SOURCE_FILES) | {'sing/' + p for p in SING_FILES}
    c.need(changed == expected, 'Unexpected upstream changed files: ' + str(changed ^ expected))
    source_files = {p: {'before': before.get(p), 'after': after.get(p)} for p in sorted(expected)}
    tests = c.read_json(HERE / 'TEST_CASES.json')
    for item in tests['files']:
        target = trees['sing'] / item['target']
        c.need(not target.exists(), 'Test overwrites upstream file')
        shutil.copyfile(HERE / item['source'], target)
        result = command([Path(go).with_name('gofmt'), '-w', target], ROOT, env,
                         evidence / ('gofmt-test-' + Path(item['source']).stem))
        c.need(result.returncode == 0, 'Test gofmt failed')
    for name, target_name in EXTRA_TESTS.items():
        family, relative = target_name.split('/', 1)
        target = trees[family] / relative
        c.need(not target.exists() and c.git('show','HEAD:'+name) == (ROOT/name).read_bytes(), 'Uncommitted/overwriting shared test')
        shutil.copyfile(ROOT/name,target)
        result = command([Path(go).with_name('gofmt'),'-w',target],ROOT,env,
                         evidence/('gofmt-shared-'+Path(name).stem))
        c.need(result.returncode == 0,'Shared test gofmt failed')
    box, sing, vmess = trees['sing-box'], trees['sing'], trees['sing-vmess']
    result = command([go, 'mod', 'edit', '-replace=github.com/sagernet/sing=' + str(sing),
                      '-replace=github.com/sagernet/sing-vmess=' + str(vmess)], box, env, evidence / 'replace')
    c.need(result.returncode == 0, 'Replacement failed')
    result = command([go, 'list', '-m', '-json', 'all'], box, env, evidence / 'modules')
    c.need(result.returncode == 0, 'Module graph failed')
    normalized = graph(result.stdout, sing, vmess)
    save(evidence / 'normalized-modules.json', normalized)
    validate_mechanism(args.mechanism, source_id, source_files, c.sha256(go), normalized)
    save(evidence / 'mechanism-identity.json', {'same_head': True, 'same_mux_route_sources': True,
                                             'same_compiler': True, 'same_fixed_dependency_versions': True})
    checks = []
    for mode, extra in (('race', ['-race']), ('debug', ['-race', '-tags=debug'])):
        for package in PACKAGES:
            label = package.replace('/', '-') + '-' + mode
            result = command([go, 'test', '-json', '-count=1', '-timeout=90s', *extra,
                              '-run', '^TestVUICompletePacket', 'github.com/sagernet/sing/' + package],
                             box, env, evidence / label)
            checks.append({'label': label, 'returncode': result.returncode})
    for kind, pattern in (('mux-unit','^TestMuxBounded'),
                          ('mux-encrypted','^TestMuxEncrypted(AES|ChaCha)$'),
                          ('mux-encrypted-cancel','^TestMuxEncryptedCancel(AES|ChaCha)$')):
        for mode, extra in (('race',['-race']),('debug',['-race','-tags=debug'])):
            label=kind+'-'+mode
            result=command([go,'test','-json','-count=1','-timeout=90s',*extra,'-run',pattern,
                            'github.com/sagernet/sing-vmess'],box,env,evidence/label)
            checks.append({'label':label,'returncode':result.returncode})
    result=command([go,'test','-json','-race','-count=1','-timeout=45s','-run','^TestMuxRoute','./route'],
                   box,{**env,'VUI_LIFECYCLE_PHASE':'lifecycle','VUI_MUX_ARM':'mux'},evidence/'mux-route-race')
    checks.append({'label':'mux-route-race','returncode':result.returncode})
    for goos in ('darwin', 'windows'):
        for package in PACKAGES:
            label = package.replace('/', '-') + '-' + goos + '-compile'
            result = command([go, 'test', '-c', '-o', out / (label + '.test'),
                              'github.com/sagernet/sing/' + package], box,
                             {**env, 'GOOS': goos, 'CGO_ENABLED': '0'}, evidence / label)
            checks.append({'label': label, 'returncode': result.returncode})
    version = '1.14.2-vui-complete-packet-experiment-' + source_id['vui_commit'][:12]
    binary = out / 'candidate-sing-box'
    flags = '-X github.com/sagernet/sing-box/constant.Version=' + version
    result = command([go, 'build', '-trimpath', '-buildvcs=false', '-ldflags=' + flags,
                      '-o', binary, './cmd/sing-box'], box, {**env, 'CGO_ENABLED': '0'}, evidence / 'build')
    checks.append({'label': 'build', 'returncode': result.returncode})
    result = command([go, 'mod', 'verify'], box, env, evidence / 'module-verify')
    checks.append({'label': 'module-verify', 'returncode': result.returncode})
    final = {family + '/' + name: value for family, tree in trees.items()
             for name, value in manifest(tree).items()}
    expected_test = {'sing/' + item['target'] for item in tests['files']}
    c.need({p for p in after.keys() | final.keys() if after.get(p) != final.get(p)} ==
           {'sing-box/go.mod'} | expected_test | set(EXTRA_TESTS.values()), 'Build changed upstream source/go.sum')
    c.need(identity() == source_id, 'Repository changed during build')
    data = {'schema_version': 1, 'kind': KIND, 'source_identity': source_id,
            'fixed_sources': {'sing_box': c.builder.SING_BOX, 'sing_vmess': c.builder.SING_VMESS, 'sing': SING},
            'patches': [{'path': p, 'sha256': source_id['files'][p]} for p in PATCHES],
            'version': version, 'binary_sha256': c.sha256(binary) if binary.exists() else None,
            'build': {'go_version': c.builder.GO_VERSION, 'go_binary_sha256': c.sha256(go),
                      'goos': 'linux', 'goarch': 'amd64', 'cgo_enabled': '0',
                      'trimpath': True, 'buildvcs': False, 'tags': [], 'gotoolchain': 'local', 'gowork': 'off'},
            'module_graph_sha256': c.graph_digest(normalized), 'source_files': source_files,
            'tests': {**{item['source']: c.sha256(HERE / item['source']) for item in tests['files']},
                      **{name:c.sha256(ROOT/name) for name in EXTRA_TESTS}},
            'checks': checks, 'product_qualified': False}
    save(out / 'provenance.json', data)
    save(evidence / 'provenance.json', data)
    # Verification parses exact test terminals rather than trusting return codes.
    spec = importlib.util.spec_from_file_location('packet_verify', HERE / 'verify_evidence.py')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    problems = []
    try:
        verifier.verify_build(evidence, tests)
    except ValueError as exc:
        problems.append(str(exc))
    if any(item['returncode'] for item in checks):
        problems.append('One or more actual commands failed')
    save(evidence / 'RESULT.json', {'passed': not problems, 'errors': problems, 'product_qualified': False})
    if problems:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('sing-box', 'sing-vmess', 'sing', 'mechanism', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    build(parser.parse_args())

if __name__ == '__main__':
    main()
