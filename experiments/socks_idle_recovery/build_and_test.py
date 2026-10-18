#!/usr/bin/env python3
"""Run the exact five-patch base, then a separately identified complete owner tree."""
from __future__ import annotations
import argparse
from pathlib import Path
import shutil
from types import SimpleNamespace
import harness as h

def regressions(go, box, env, out, evidence, version):
    checks = []
    def run(label, args, run_env=None):
        result = h.command(args, box, run_env or env, evidence / label)
        checks.append({'label': label, 'returncode': result.returncode})
    for mode, extra in h.MODES.items():
        for package in h.packet.PACKAGES:
            run(package.replace('/', '-') + '-' + mode,
                [go, 'test', '-json', '-count=1', '-timeout=90s', *extra,
                 '-run', '^TestVUICompletePacket', 'github.com/sagernet/sing/' + package])
    for kind, pattern in (('mux-unit', '^TestMuxBounded'), ('mux-encrypted', '^TestMuxEncrypted(AES|ChaCha)$'),
                          ('mux-encrypted-cancel', '^TestMuxEncryptedCancel(AES|ChaCha)$'),
                          ('mux-empty-datagram', '^TestMuxEmptyDatagram(Raw|AES|ChaCha)$')):
        for mode, extra in h.MODES.items():
            run(kind + '-' + mode, [go, 'test', '-json', '-count=1', '-timeout=120s', *extra,
                                    '-run', pattern, 'github.com/sagernet/sing-vmess'])
    run('mux-route-race', [go, 'test', '-json', '-race', '-count=1', '-timeout=45s',
                          '-run', '^TestMuxRoute', './route'],
        {**env, 'VUI_LIFECYCLE_PHASE': 'lifecycle', 'VUI_MUX_ARM': 'mux'})
    for goos in ('darwin', 'windows'):
        for package in h.packet.PACKAGES:
            label = package.replace('/', '-') + '-' + goos + '-compile'
            run(label, [go, 'test', '-c', '-o', out / (label + '.test'), 'github.com/sagernet/sing/' + package],
                {**env, 'GOOS': goos, 'CGO_ENABLED': '0'})
    run('build', [go, 'build', '-trimpath', '-buildvcs=false',
                 '-ldflags=-X github.com/sagernet/sing-box/constant.Version=' + version,
                 '-o', out / 'owner-sing-box', './cmd/sing-box'], {**env, 'CGO_ENABLED': '0'})
    run('module-verify', [go, 'mod', 'verify'])
    h.packet.save(evidence / 'provenance.json', {'checks': checks, 'product_qualified': False})
    return checks

def build(args):
    source_id, contract = h.identity(), h.read_contract()
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    evidence = out / 'evidence'; evidence.mkdir()
    base = out / 'base'
    try:
        h.packet.build(SimpleNamespace(sing_box=args.sing_box, sing_vmess=args.sing_vmess,
                                      sing=args.sing, mechanism=args.mechanism, output=base))
    finally:
        if (base / 'evidence').is_dir(): shutil.copytree(base / 'evidence', evidence / 'base')
    base_provenance = h.c.read_json(base / 'provenance.json')
    trees = {family: base / family for family in h.DELTA}
    before = h.full_manifest(trees)
    h.packet.save(evidence / 'base-source-manifest.json', before)
    env, go = h.environment(base), shutil.which('go')
    h.c.need(go and h.c.sha256(go) == base_provenance['build']['go_binary_sha256'], 'Compiler changed after base')
    for index, (family, name) in enumerate(h.OVERLAYS, start=5):
        for suffix, extra in (('check', ['--check']), ('apply', [])):
            result = h.command(['git', 'apply', *extra, h.ROOT / name], trees[family], env, evidence / f'patch-{index}-{suffix}')
            h.c.need(result.returncode == 0, 'Owner patch failed: ' + name)
    result = h.command([Path(go).with_name('gofmt'), '-w', *[trees[f] / p for f, paths in h.DELTA.items() for p in paths]],
                       h.ROOT, env, evidence / 'gofmt-owner')
    h.c.need(result.returncode == 0, 'Owner source formatting failed')
    after = h.full_manifest(trees)
    delta = h.validate_delta(before, after, h.FINAL_FILES)
    h.packet.save(evidence / 'final-source-manifest.json', after)
    for name in ('sing-vmess/mux.go', 'sing-vmess/mux_writer.go'):
        h.c.need(before[name] == after[name], 'Owner overlay changed shared server writer')
    for item in contract['files']:
        target = trees[item['family']] / item['target']
        h.c.need(not target.exists() and target.parent.is_dir(), 'Owner test overwrites/misplaces source')
        shutil.copyfile(h.HERE / item['source'], target)
        label = 'gofmt-new-' + item['source'].replace('/', '-').removesuffix('.go')
        result = h.command([Path(go).with_name('gofmt'), '-w', target], h.ROOT, env, evidence / label)
        h.c.need(result.returncode == 0, 'Owner test formatting failed')
    injected = {item['family'] + '/' + item['target'] for item in contract['files']}
    frozen = h.full_manifest(trees)
    h.validate_delta(after, frozen, injected)
    formatted_tests = {name: frozen[name] for name in sorted(injected)}
    box, sing, vmess = trees['sing-box'], trees['sing'], trees['sing-vmess']
    result = h.command([go, 'list', '-m', '-json', 'all'], box, env, evidence / 'modules')
    h.c.need(result.returncode == 0, 'Final module graph failed')
    graph = h.packet.graph(result.stdout, sing, vmess)
    h.c.need(graph == h.c.read_json(base / 'evidence/normalized-modules.json'), 'Final module versions changed')
    h.packet.save(evidence / 'normalized-modules.json', graph)
    version = '1.14.2-vui-socks-owner-experiment-' + source_id['vui_commit'][:12]
    regression = evidence / 'regression'; regression.mkdir()
    old_checks = regressions(go, box, env, out, regression, version)
    checks = []
    for group in contract['groups']:
        for mode, extra in h.MODES.items():
            label = group['label'] + '-' + mode
            result = h.command([go, 'test', '-json', '-count=1', '-timeout=180s', *extra,
                                '-run', group['regex'], group['package']], box, env, evidence / label)
            checks.append({'label': label, 'returncode': result.returncode})
    for index, package in enumerate(h.CROSS_PACKAGES):
        for goos in ('darwin', 'windows'):
            label = f'owner-cross-{index}-{goos}'
            result = h.command([go, 'test', '-c', '-o', out / (label + '.test'), package], box,
                               {**env, 'GOOS': goos, 'CGO_ENABLED': '0'}, evidence / label)
            checks.append({'label': label, 'returncode': result.returncode})
    final = h.full_manifest(trees)
    h.c.need(final == frozen, 'Tests changed final source or formatted test bytes')
    h.packet.save(evidence / 'post-test-source-manifest.json', final)
    result = h.command([go, 'list', '-m', '-json', 'all'], box, env, evidence / 'modules-after')
    h.c.need(result.returncode == 0 and h.packet.graph(result.stdout, sing, vmess) == graph, 'Tests changed final module graph')
    h.c.need(h.identity() == source_id, 'Repository changed during owner build')
    binary = out / 'owner-sing-box'
    provenance = {
        'schema_version': 1, 'kind': h.KIND, 'source_identity': source_id,
        'fixed_sources': base_provenance['fixed_sources'],
        'patches': base_provenance['patches'] + [{'path': p, 'sha256': source_id['files'][p]} for _, p in h.OVERLAYS],
        'base_source_files': base_provenance['source_files'], 'final_delta': delta,
        'source_directories': {family: str(tree) for family, tree in trees.items()},
        'formatted_test_files': formatted_tests,
        'post_test_manifest_sha256': h.c.sha256(evidence / 'post-test-source-manifest.json'),
        'base_manifest_sha256': h.c.sha256(evidence / 'base-source-manifest.json'),
        'final_manifest_sha256': h.c.sha256(evidence / 'final-source-manifest.json'),
        'base_binary_sha256': base_provenance['binary_sha256'], 'base_version': base_provenance['version'], 'version': version,
        'binary_sha256': h.c.sha256(binary) if binary.exists() else None, 'build': base_provenance['build'],
        'module_graph_sha256': h.c.graph_digest(graph),
        'tests': {item['source']: h.c.sha256(h.HERE / item['source']) for item in contract['files']},
        'legacy_tests': base_provenance['tests'], 'checks': checks, 'regression_checks': old_checks,
        'cross_packages': list(h.CROSS_PACKAGES), 'same_head_base_validated': True,
        'final_sources_equal_base': False, 'final_shared_server_writer_unchanged': True, 'product_qualified': False,
    }
    h.packet.save(out / 'provenance.json', provenance); h.packet.save(evidence / 'provenance.json', provenance)
    verifier = h.load('owner_final_verifier', h.HERE / 'verify_evidence.py')
    problems = []
    try: verifier.verify(evidence, contract)
    except (ValueError, KeyError, FileNotFoundError) as exc: problems.append(str(exc))
    h.packet.save(evidence / 'RESULT.json', {'passed': not problems, 'errors': problems,
                                           'product_qualified': False, 'runtime_executed': False})
    if problems: raise SystemExit(1)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('sing-box', 'sing-vmess', 'sing', 'mechanism', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    build(parser.parse_args())
