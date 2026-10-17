#!/usr/bin/env python3
"""Join mechanism and runtime evidence only for identical tested source bytes."""
import argparse
from pathlib import Path
import common as c


def compare_arm(mechanism, runtime, normalized):
    c.need(mechanism['arm'] == runtime['arm'], 'Mechanism/runtime arm differs')
    c.need(mechanism['vui_commit'] == runtime['vui_source_commit'], 'Mechanism/runtime commit differs')
    c.need(mechanism['go_binary_sha256'] == runtime['build']['go_binary_sha256'], 'Compiler bytes differ')
    c.need(c.graph_digest(normalized) == runtime['module_graph_sha256'], 'Fixed dependency graph differs')
    c.need(mechanism['product_qualified'] is False and runtime['product_qualified'] is False,
           'Unexpected product qualification')
    c.need(all(check['returncode'] == 0 for check in mechanism['checks']), 'Mechanism checks did not pass')
    for name in ('mux.go', 'xudp.go'):
        c.need(mechanism['source_sha256'][name] == runtime['source_files']['sing-vmess/' + name]['after'],
               'Runtime source differs from tested mechanism: ' + name)
    c.need(mechanism['route_sha256'] == runtime['source_files']['sing-box/route/conn.go']['after'],
           'Runtime route source differs from tested mechanism')
    c.need(mechanism['source_sha256'].get('mux_writer.go') ==
           runtime['source_files']['sing-vmess/mux_writer.go']['after'], 'Runtime writer differs')
    c.need(mechanism['patches'] == [{'name': Path(row['path']).name, 'sha256': row['sha256']}
                                  for row in runtime['patches']], 'Patch chain differs')


def compare(mechanism, candidates):
    manifest = c.read_json(mechanism / 'MANIFEST.json')
    c.need(manifest['count'] == len(manifest['files']), 'Mechanism manifest count differs')
    c.need({p.name for p in mechanism.iterdir() if p.is_file()} ==
           {r['path'] for r in manifest['files']} | {'MANIFEST.json'}, 'Mechanism artifact members differ')
    for row in manifest['files']:
        path = mechanism / row['path']
        c.need(path.parent == mechanism and not path.is_symlink() and path.is_file(), 'Invalid manifest path')
        c.need(path.stat().st_size == row['bytes'] and c.sha256(path) == row['sha256'], 'Mechanism evidence changed')
    verification = c.read_json(mechanism / 'VERIFICATION.json')
    c.need(verification['product_qualified'] is False and len(verification['route']) == 2,
           'Missing mechanism verification')
    c.need(verification.get('encrypted') == {arm+'-'+label: 2 for arm in c.ARMS
           for label in ('encrypted-race','encrypted-debug')}, 'Missing direct encrypted-flow tests')
    identity = c.source_identity()
    for arm in c.ARMS:
        runtime = c.read_json(candidates / arm / 'provenance.json')
        c.validate_provenance(runtime, arm, identity)
        compare_arm(c.read_json(mechanism / (arm + '-provenance.json')), runtime,
                    c.read_json(mechanism / (arm + '-normalized-modules.json')))
    return {'same_commit_source_compiler_graph': True, 'vui_commit': identity['vui_commit'],
            'product_qualified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mechanism', type=Path, required=True)
    parser.add_argument('--candidates', type=Path, required=True)
    args = parser.parse_args()
    print(compare(args.mechanism.resolve(), args.candidates.resolve()))


if __name__ == '__main__':
    main()
