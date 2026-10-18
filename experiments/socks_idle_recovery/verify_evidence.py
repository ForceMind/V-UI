"""Strict final-tree evidence. A base green result never qualifies an owner overlay."""
from pathlib import Path
import harness as h
CROSS_PACKAGES = h.CROSS_PACKAGES

def verify_command(row, args, goos='linux', cgo='1'):
    h.c.need(row['returncode'] == 0 and row.get('timeout') is False and row.get('leader_joined') is True and
             row.get('process_group_gone') is True, 'Failed/unjoined owner command')
    h.c.need(row['environment'] == {'GOOS': goos, 'GOARCH': 'amd64', 'CGO_ENABLED': cgo,
                                   'GOTOOLCHAIN': 'local', 'GOWORK': 'off'}, 'Wrong owner environment')
    h.c.need(row['args'][1:] == args, 'Owner command selection changed')

def verify_preparation(directory, data, contract):
    trees = {name: Path(path) for name, path in data['source_directories'].items()}
    h.c.need(set(trees) == set(h.DELTA) and len({p.parent for p in trees.values()}) == 1 and
             all(path.is_absolute() and path.name == name for name, path in trees.items()),
             'Unexpected replacement source directories')
    compiler = h.c.read_json(directory / 'base/compiler.exit.json')['args'][0]
    for index, (_, patch) in enumerate(h.OVERLAYS, start=5):
        for step, extra in (('check', ['--check']), ('apply', [])):
            row = h.c.read_json(directory / f'patch-{index}-{step}.exit.json'); args = row['args']
            h.c.need(args[0] == 'git' and Path(args[-1]).as_posix().endswith('/' + patch), 'Wrong overlay patch target')
            verify_command(row, ['apply', *extra, args[-1]])
    row = h.c.read_json(directory / 'gofmt-owner.exit.json')
    h.c.need(row['args'][0] == str(Path(compiler).with_name('gofmt')), 'Unknown source formatter')
    verify_command(row, ['-w', *[str(trees[f] / p) for f, names in h.DELTA.items() for p in names]])
    for item in contract['files']:
        label = 'gofmt-new-' + item['source'].replace('/', '-').removesuffix('.go')
        row = h.c.read_json(directory / (label + '.exit.json'))
        h.c.need(row['args'][0] == str(Path(compiler).with_name('gofmt')), 'Unknown test formatter')
        verify_command(row, ['-w', str(trees[item['family']] / item['target'])])
    graph = h.c.read_json(directory / 'normalized-modules.json')
    for label in ('modules', 'modules-after'):
        row = h.c.read_json(directory / (label + '.exit.json'))
        h.c.need(row['args'][0] == compiler, 'Module command compiler differs')
        verify_command(row, ['list', '-m', '-json', 'all'])
        h.c.need(h.packet.graph((directory / (label + '.stdout')).read_text(), trees['sing'], trees['sing-vmess']) == graph,
                 'Actual final module graph differs')
    for check in data['regression_checks']:
        row = h.c.read_json(directory / 'regression' / (check['label'] + '.exit.json'))
        h.c.need(row['args'][0] == compiler and row.get('timeout') is False and row.get('leader_joined') is True and
                 row.get('process_group_gone') is True, 'Final regression command timeout or unjoined descendants')

def verify(directory, contract):
    directory = Path(directory)
    data = h.c.read_json(directory / 'provenance.json')
    h.c.need(data['kind'] == h.KIND and data['schema_version'] == 1 and data['product_qualified'] is False and
             data['same_head_base_validated'] is True and data['final_sources_equal_base'] is False and
             data['final_shared_server_writer_unchanged'] is True, 'Wrong owner qualification scope')
    base = h.c.read_json(directory / 'base/provenance.json'); source = data['source_identity']
    h.c.need(h.c.digest(source['vui_commit'], 40) and h.c.digest(source['vui_tree'], 40) and
             all(h.c.digest(v) for v in source['files'].values()), 'Invalid exact source identity')
    h.c.need(base['source_identity']['vui_commit'] == source['vui_commit'] and
             base['source_identity']['vui_tree'] == source['vui_tree'] and data['build'] == base['build'] and
             data['fixed_sources'] == base['fixed_sources'], 'Base/final identity mismatch')
    h.c.need(all(source['files'].get(n) == d for n, d in base['source_identity']['files'].items()), 'Base input files differ')
    h.c.need(data['fixed_sources'] == {'sing_box': h.c.builder.SING_BOX, 'sing_vmess': h.c.builder.SING_VMESS,
                                      'sing': h.packet.SING}, 'Changed fixed upstream revisions')
    expected_build = {'go_version': h.c.builder.GO_VERSION, 'goos': 'linux', 'goarch': 'amd64', 'cgo_enabled': '0',
                      'trimpath': True, 'buildvcs': False, 'tags': [], 'gotoolchain': 'local', 'gowork': 'off'}
    h.c.need(set(data['build']) == set(expected_build) | {'go_binary_sha256'} and
             all(data['build'][k] == v for k, v in expected_build.items()) and
             h.c.digest(data['build']['go_binary_sha256']), 'Wrong compiler/build flags')
    h.c.need((directory / 'base/compiler.stdout').read_text().strip() == 'go version ' + h.c.builder.GO_VERSION + ' linux/amd64' and
             h.c.read_json(directory / 'base/compiler.exit.json')['returncode'] == 0, 'Actual base compiler identity missing')
    expected_patches = list(h.packet.PATCHES) + [p for _, p in h.OVERLAYS]
    h.c.need(data['patches'] == [{'path': p, 'sha256': source['files'][p]} for p in expected_patches] and
             base['patches'] == data['patches'][:5], 'Owner patch chain mismatch')
    for name, label in (('base-source-manifest.json', 'base_manifest_sha256'), ('final-source-manifest.json', 'final_manifest_sha256')):
        h.c.need(h.c.sha256(directory / name) == data[label], 'Source manifest digest mismatch')
    before = h.c.read_json(directory / 'base-source-manifest.json'); after = h.c.read_json(directory / 'final-source-manifest.json')
    for manifest in (before, after):
        h.c.need(manifest and all(h.c.digest(digest) and not Path(name).is_absolute() and '..' not in Path(name).parts
                                  for name, digest in manifest.items()), 'Malformed upstream manifest')
    h.c.need(h.validate_delta(before, after, h.FINAL_FILES) == data['final_delta'], 'Final delta mismatch')
    h.c.need(all((n not in before) if n in h.NEW_FILES else h.c.digest(before.get(n)) for n in h.FINAL_FILES),
             'New/existing overlay file boundary changed')
    expected_targets = {item['family'] + '/' + item['target'] for item in contract['files']}
    formatted = data['formatted_test_files']
    h.c.need(set(formatted) == expected_targets and not expected_targets.intersection(after) and
             all(h.c.digest(v) for v in formatted.values()), 'Wrong formatted test targets/digests')
    h.c.need(h.c.sha256(directory / 'post-test-source-manifest.json') == data['post_test_manifest_sha256'] and
             h.c.read_json(directory / 'post-test-source-manifest.json') == {**after, **formatted},
             'Post-test source or formatted test bytes changed')
    for name in ('sing-vmess/mux.go', 'sing-vmess/mux_writer.go'):
        h.c.need(before[name] == after[name], 'Shared server writer changed')
    h.c.need(data['base_source_files'] == base['source_files'], 'Base source proof mismatch')
    for name, values in base['source_files'].items(): h.c.need(before[name] == values['after'], 'Base manifest/source mismatch')
    graph = h.c.read_json(directory / 'normalized-modules.json')
    h.c.need(graph == h.c.read_json(directory / 'base/normalized-modules.json') and
             h.c.graph_digest(graph) == data['module_graph_sha256'] == base['module_graph_sha256'], 'Final graph differs')
    h.c.need(h.c.digest(data['binary_sha256']) and h.c.digest(data['base_binary_sha256']) and
             data['base_binary_sha256'] == base['binary_sha256'] and
             data['version'] == '1.14.2-vui-socks-owner-experiment-' + source['vui_commit'][:12] and
             data['base_version'] == base['version'], 'Missing/mislabeled binary identity')
    h.c.need(base['version'] == '1.14.2-vui-complete-packet-experiment-' + source['vui_commit'][:12], 'Base binary mislabeled')
    h.c.need(data['legacy_tests'] == base['tests'] and data['tests'] == {
        item['source']: source['files']['experiments/socks_idle_recovery/' + item['source']] for item in contract['files']},
        'Injected test identity mismatch')
    verify_preparation(directory, data, contract)
    old_contract = h.c.read_json(h.HERE.parent / 'xudp_packet/TEST_CASES.json')
    base_report = h.packet_verify.verify_build(directory / 'base', old_contract)
    regression = h.packet_verify.verify_build(directory / 'regression', old_contract)
    h.c.need(base_report == regression, 'Final tree lost unchanged regression coverage')
    h.c.need(data['regression_checks'] == h.c.read_json(directory / 'regression/provenance.json')['checks'], 'Regression ledger mismatch')
    expected = {g['label'] + '-' + m for g in contract['groups'] for m in h.MODES}
    expected |= {f'owner-cross-{i}-{os}' for i in range(len(CROSS_PACKAGES)) for os in ('darwin', 'windows')}
    checks = data['checks']
    h.c.need(len(checks) == len(expected) and {r['label'] for r in checks} == expected and
             all(r['returncode'] == 0 for r in checks), 'Missing, duplicate or failed owner check')
    compiler = h.c.read_json(directory / 'base/compiler.exit.json')['args'][0]
    groups = {}
    for group in contract['groups']:
        for mode, extra in h.MODES.items():
            label = group['label'] + '-' + mode; row = h.c.read_json(directory / (label + '.exit.json'))
            h.c.need(row['args'][0] == compiler, 'Owner test compiler differs')
            verify_command(row, ['test', '-json', '-count=1', '-timeout=180s', *extra, '-run', group['regex'], group['package']])
            groups[label] = h.packet_verify.verify_go(directory / (label + '.stdout'), group['leaves'], group['go_package'])
    h.c.need(data['cross_packages'] == list(CROSS_PACKAGES), 'Changed cross compile scope')
    for index, package in enumerate(CROSS_PACKAGES):
        for goos in ('darwin', 'windows'):
            label = f'owner-cross-{index}-{goos}'; row = h.c.read_json(directory / (label + '.exit.json')); args = row['args']
            h.c.need(args[0] == compiler, 'Owner cross compiler differs')
            h.c.need(len(args) == 6 and Path(args[4]).name == label + '.test', 'Wrong cross output')
            verify_command(row, ['test', '-c', '-o', args[4], package], goos, '0')
    result = {'new_owner_groups': groups, 'final_legacy_regression': regression, 'base_same_head_regression': base_report,
              'cross_compile_only': ['darwin/amd64', 'windows/amd64'], 'product_qualified': False, 'runtime_executed': False}
    h.packet.save(directory / 'VERIFICATION.json', result)
    return result
