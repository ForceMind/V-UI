import json
import re
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harness as h
v = h.load('owner_test_verifier', HERE / 'verify_evidence.py')
p = h.load('owner_test_prepare', HERE / 'prepare_evidence.py')

class CommandContractTests(unittest.TestCase):
    def row(self):
        return {'returncode': 0, 'timeout': False, 'leader_joined': True, 'process_group_gone': True,
                'args': ['/compiler/go', 'test', './route'],
                'environment': {'GOOS': 'linux', 'GOARCH': 'amd64', 'CGO_ENABLED': '1',
                                'GOTOOLCHAIN': 'local', 'GOWORK': 'off'}}
    def test_complete(self): v.verify_command(self.row(), ['test', './route'])
    def test_failed(self):
        row = self.row(); row['returncode'] = 1
        with self.assertRaises(ValueError): v.verify_command(row, ['test', './route'])
    def test_timeout(self):
        row = self.row(); row['timeout'] = True
        with self.assertRaises(ValueError): v.verify_command(row, ['test', './route'])
    def test_missing_join(self):
        row = self.row(); del row['leader_joined']
        with self.assertRaises(ValueError): v.verify_command(row, ['test', './route'])
    def test_descendant(self):
        row = self.row(); row['process_group_gone'] = False
        with self.assertRaises(ValueError): v.verify_command(row, ['test', './route'])
    def test_wrong_selection(self):
        with self.assertRaises(ValueError): v.verify_command(self.row(), ['test', './adapter'])
    def test_wrong_environment(self):
        row = self.row(); row['environment']['GOOS'] = 'windows'
        with self.assertRaises(ValueError): v.verify_command(row, ['test', './route'])

class SourceDeltaTests(unittest.TestCase):
    def test_exact(self):
        self.assertEqual(h.validate_delta({'a': 'x'}, {'a': 'y', 'b': 'z'}, {'a', 'b'}),
                         {'a': {'base': 'x', 'final': 'y'}, 'b': {'base': None, 'final': 'z'}})
    def test_unexpected(self):
        with self.assertRaises(ValueError): h.validate_delta({'a': 'x'}, {'a': 'y', 'b': 'z'}, {'a'})
    def test_missing(self):
        with self.assertRaises(ValueError): h.validate_delta({'a': 'x'}, {'a': 'x'}, {'a'})
    def test_fixed_scope(self):
        self.assertEqual(len(h.FINAL_FILES), 16)
        self.assertNotIn('sing-vmess/mux.go', h.FINAL_FILES); self.assertNotIn('sing-vmess/mux_writer.go', h.FINAL_FILES)

class EvidenceSafetyTests(unittest.TestCase):
    def text(self, name, text, jsonl=False):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / name; path.write_text(text); return p.validate(path, jsonl)
    def test_jsonl(self):
        text = json.dumps({'Test': 'TestOwner/b', 'Action': 'pass'}) + '\n'
        self.assertEqual(self.text('test.stdout', text, True), text)
    def test_duplicate_key(self):
        with self.assertRaises(ValueError): self.text('test.stdout', '{"a":1,"a":2}\n', True)
    def test_jsonl_nonobject(self):
        with self.assertRaises(ValueError): self.text('test.stdout', '[]\n', True)
    def test_escaped_nul(self):
        with self.assertRaises(ValueError): self.text('test.json', '{"a":"\\u0000"}')
    def test_key_material(self):
        with self.assertRaises(ValueError): self.text('test.stdout', '-----BEGIN PRIVATE KEY-----')
    def test_bad_utf8(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'test.stdout'; path.write_bytes(b'\xff')
            with self.assertRaises(UnicodeDecodeError): p.validate(path, False)
    def test_unlisted_binary(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); build = root / 'build'; build.mkdir(); (build / 'owner-sing-box').write_text('not accepted')
            with self.assertRaises(ValueError): p.prepare(build, root / 'upload', {'groups': [], 'files': []})
    def test_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); build = root / 'build'; build.mkdir(); (root / 'external').write_text('{}')
            (build / 'RESULT.json').symlink_to(root / 'external')
            with self.assertRaises(ValueError): p.prepare(build, root / 'upload', {'groups': [], 'files': []})
    def test_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); build = root / 'build'; build.mkdir(); (build / 'RESULT.json').write_text('{"passed":false}')
            p.prepare(build, root / 'upload', {'groups': [], 'files': []})
            data = json.loads((root / 'upload/MANIFEST.json').read_text())
            self.assertEqual(data['count'], 1); self.assertEqual(data['files'][0]['bytes'], 16)
            self.assertFalse(data['product_qualified'])

class FullVerifierControls(unittest.TestCase):
    """Synthetic checker mutations only; they do not qualify any Go execution."""
    def fixture(self, root):
        for name in ('base', 'regression'): (root / name).mkdir()
        digest = 'a' * 64
        test = {'family': 'sing', 'source': 'tests/example_test.go', 'target': 'example_test.go'}
        group = {'label': 'owner-example', 'package': './route', 'go_package': 'github.com/sagernet/sing-box/route',
                 'regex': '^TestExample$', 'leaves': ['TestExample/one']}
        contract = {'files': [test], 'groups': [group]}
        files = {name: digest for name in list(h.packet.PATCHES) + [p for _, p in h.OVERLAYS]}
        files['experiments/socks_idle_recovery/' + test['source']] = digest
        source = {'vui_commit': 'b' * 40, 'vui_tree': 'c' * 40, 'files': files}
        before = {name: digest for name in h.FINAL_FILES - h.NEW_FILES}
        before.update({'sing-vmess/mux.go': digest, 'sing-vmess/mux_writer.go': digest})
        after = {**before, **{name: 'd' * 64 for name in h.FINAL_FILES}}
        for label, data in (('base-source-manifest.json', before), ('final-source-manifest.json', after),
                            ('normalized-modules.json', []), ('base/normalized-modules.json', [])):
            h.packet.save(root / label, data)
        formatted = {'sing/example_test.go': digest}
        h.packet.save(root / 'post-test-source-manifest.json', {**after, **formatted})
        build = {'go_version': h.c.builder.GO_VERSION, 'go_binary_sha256': digest, 'goos': 'linux',
                 'goarch': 'amd64', 'cgo_enabled': '0', 'trimpath': True, 'buildvcs': False,
                 'tags': [], 'gotoolchain': 'local', 'gowork': 'off'}
        base = {'source_identity': source, 'build': build,
                'fixed_sources': {'sing_box': h.c.builder.SING_BOX, 'sing_vmess': h.c.builder.SING_VMESS, 'sing': h.packet.SING},
                'patches': [{'path': name, 'sha256': digest} for name in h.packet.PATCHES],
                'source_files': {'sing-vmess/mux.go': {'before': digest, 'after': digest}}, 'binary_sha256': digest,
                'version': '1.14.2-vui-complete-packet-experiment-' + 'b' * 12,
                'module_graph_sha256': h.c.graph_digest([]), 'tests': {'legacy_test.go': digest}}
        h.packet.save(root / 'base/provenance.json', base)
        (root / 'base/compiler.stdout').write_text('go version ' + h.c.builder.GO_VERSION + ' linux/amd64\n')
        h.packet.save(root / 'base/compiler.exit.json', {'returncode': 0, 'args': ['/compiler/go', 'version']})
        regression_checks = [{'label': 'synthetic-regression', 'returncode': 0}]
        h.packet.save(root / 'regression/provenance.json', {'checks': regression_checks})
        h.packet.save(root / 'regression/synthetic-regression.exit.json', CommandContractTests().row())
        trees = {family: '/tmp/owner/base/' + family for family in h.DELTA}
        def cmd(label, binary, args):
            row = CommandContractTests().row(); row['args'] = [binary, *args]; h.packet.save(root / (label + '.exit.json'), row)
        for index, (_, path) in enumerate(h.OVERLAYS, start=5):
            for step, extra in (('check', ['--check']), ('apply', [])):
                cmd(f'patch-{index}-{step}', 'git', ['apply', *extra, '/repo/' + path])
        cmd('gofmt-owner', '/compiler/gofmt', ['-w', *[trees[f] + '/' + path for f, names in h.DELTA.items() for path in names]])
        cmd('gofmt-new-tests-example_test', '/compiler/gofmt', ['-w', trees['sing'] + '/' + test['target']])
        for label in ('modules', 'modules-after'):
            cmd(label, '/compiler/go', ['list', '-m', '-json', 'all']); (root / (label + '.stdout')).write_text('synthetic module graph')
        checks = []
        for mode, extra in h.MODES.items():
            label = group['label'] + '-' + mode; row = CommandContractTests().row()
            row['args'] = ['/compiler/go', 'test', '-json', '-count=1', '-timeout=180s', *extra, '-run', group['regex'], group['package']]
            h.packet.save(root / (label + '.exit.json'), row)
            events = [{'Package': group['go_package'], 'Action': 'pass', 'Test': n} for n in ('TestExample/one', 'TestExample')]
            events += [{'Package': group['go_package'], 'Action': 'pass'}]
            (root / (label + '.stdout')).write_text(''.join(json.dumps(e) + '\n' for e in events))
            checks.append({'label': label, 'returncode': 0})
        for index, package in enumerate(h.CROSS_PACKAGES):
            for goos in ('darwin', 'windows'):
                label = f'owner-cross-{index}-{goos}'; row = CommandContractTests().row()
                row['args'] = ['/compiler/go', 'test', '-c', '-o', '/tmp/' + label + '.test', package]
                row['environment'].update(GOOS=goos, CGO_ENABLED='0')
                h.packet.save(root / (label + '.exit.json'), row); checks.append({'label': label, 'returncode': 0})
        data = {'kind': h.KIND, 'schema_version': 1, 'product_qualified': False, 'same_head_base_validated': True,
                'final_sources_equal_base': False, 'final_shared_server_writer_unchanged': True, 'source_identity': source,
                'build': build, 'fixed_sources': base['fixed_sources'], 'formatted_test_files': formatted,
                'post_test_manifest_sha256': h.c.sha256(root / 'post-test-source-manifest.json'),
                'patches': base['patches'] + [{'path': path, 'sha256': digest} for _, path in h.OVERLAYS],
                'base_manifest_sha256': h.c.sha256(root / 'base-source-manifest.json'),
                'final_manifest_sha256': h.c.sha256(root / 'final-source-manifest.json'),
                'final_delta': h.validate_delta(before, after, h.FINAL_FILES), 'base_source_files': base['source_files'],
                'module_graph_sha256': h.c.graph_digest([]), 'binary_sha256': 'e' * 64, 'base_binary_sha256': digest,
                'version': '1.14.2-vui-socks-owner-experiment-' + 'b' * 12, 'base_version': base['version'],
                'legacy_tests': base['tests'], 'tests': {test['source']: digest}, 'source_directories': trees,
                'regression_checks': regression_checks, 'checks': checks, 'cross_packages': list(h.CROSS_PACKAGES)}
        h.packet.save(root / 'provenance.json', data); return contract, data
    def check(self, change=None, log_change=None):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); contract, data = self.fixture(root)
            if change: change(data); h.packet.save(root / 'provenance.json', data)
            if log_change: log_change(root)
            def graph(text, sing, vmess): return [] if text == 'synthetic module graph' else ['mutated graph']
            with patch.object(h.packet_verify, 'verify_build', return_value={'synthetic_legacy_fixture': True}), \
                 patch.object(h.packet, 'graph', side_effect=graph): return v.verify(root, contract)
    def mutate_row(self, name, fn):
        def change(root):
            path = root / name; row = h.c.read_json(path); fn(row); h.packet.save(path, row)
        return change
    def test_complete_synthetic_fixture(self): self.assertEqual(len(self.check()['new_owner_groups']), 2)
    def test_no_new_checks(self):
        with self.assertRaises(ValueError): self.check(lambda d: d.update(checks=[]))
    def test_duplicate_new_check(self):
        with self.assertRaises(ValueError): self.check(lambda d: d['checks'].append(d['checks'][0]))
    def test_final_claimed_same_base(self):
        with self.assertRaises(ValueError): self.check(lambda d: d.update(final_sources_equal_base=True))
    def test_patch_omission(self):
        with self.assertRaises(ValueError): self.check(lambda d: d['patches'].pop())
    def test_test_hash_changed(self):
        with self.assertRaises(ValueError): self.check(lambda d: d.update(tests={}))
    def test_zero_actual_tests(self):
        def change(root):
            (root / 'owner-example-race.stdout').write_text(json.dumps({'Package': 'github.com/sagernet/sing-box/route', 'Action': 'pass'}) + '\n')
        with self.assertRaises(ValueError): self.check(log_change=change)
    def test_skipped_actual_leaf(self):
        def change(root):
            path = root / 'owner-example-race.stdout'; path.write_text(path.read_text().replace('"Action": "pass"', '"Action": "skip"', 1))
        with self.assertRaises(ValueError): self.check(log_change=change)
    def test_cross_wrong_platform(self):
        with self.assertRaises(ValueError): self.check(log_change=self.mutate_row('owner-cross-0-windows.exit.json', lambda r: r['environment'].update(GOOS='linux')))
    def test_missing_patch(self):
        with self.assertRaises(FileNotFoundError): self.check(log_change=lambda r: (r / 'patch-5-apply.exit.json').unlink())
    def test_failed_patch(self):
        with self.assertRaises(ValueError): self.check(log_change=self.mutate_row('patch-6-apply.exit.json', lambda r: r.update(returncode=1)))
    def test_wrong_patch(self):
        def change(row): row['args'][-1] = '/repo/wrong.patch'
        with self.assertRaises(ValueError): self.check(log_change=self.mutate_row('patch-7-check.exit.json', change))
    def test_final_graph_changed(self):
        with self.assertRaises(ValueError): self.check(log_change=lambda r: (r / 'modules-after.stdout').write_text('different graph'))
    def test_final_regression_unjoined(self):
        with self.assertRaises(ValueError): self.check(log_change=self.mutate_row('regression/synthetic-regression.exit.json', lambda r: r.update(process_group_gone=False)))
    def test_missing_test_formatter(self):
        with self.assertRaises(FileNotFoundError): self.check(log_change=lambda r: (r / 'gofmt-new-tests-example_test.exit.json').unlink())
    def test_wrong_owner_compiler(self):
        def change(row): row['args'][0] = '/another/go'
        with self.assertRaises(ValueError): self.check(log_change=self.mutate_row('owner-example-race.exit.json', change))
    def test_wrong_cross_compiler(self):
        def change(row): row['args'][0] = '/another/go'
        with self.assertRaises(ValueError): self.check(log_change=self.mutate_row('owner-cross-0-windows.exit.json', change))
    def test_missing_formatted_test(self):
        with self.assertRaises(ValueError): self.check(lambda d: d.update(formatted_test_files={}))
    def test_formatted_test_changed(self):
        with self.assertRaises(ValueError): self.check(lambda d: d['formatted_test_files'].update({'sing/example_test.go': 'f' * 64}))
    def test_post_test_source_changed(self):
        with self.assertRaises(ValueError): self.check(log_change=lambda r: (r / 'post-test-source-manifest.json').write_text('{}'))

class TestPathControls(unittest.TestCase):
    def contract(self):
        return {'schema_version': 1, 'kind': h.KIND, 'groups': [{'label': 'one', 'regex': '^TestOne', 'leaves': ['TestOne']}],
                'files': [{'family': 'sing', 'source': 'tests/one_test.go', 'target': 'protocol/socks/one_test.go'}]}
    def check(self, field, value):
        data = self.contract(); data['files'][0][field] = value
        with patch.object(h.c, 'read_json', return_value=data): h.read_contract()
    def test_absolute_target(self):
        with self.assertRaises(ValueError): self.check('target', '/tmp/one_test.go')
    def test_parent_target(self):
        with self.assertRaises(ValueError): self.check('target', '../one_test.go')
    def test_absolute_source(self):
        with self.assertRaises(ValueError): self.check('source', '/tmp/one_test.go')
    def test_parent_source(self):
        with self.assertRaises(ValueError): self.check('source', 'tests/../one_test.go')
    def test_not_test_source(self):
        with self.assertRaises(ValueError): self.check('source', 'private/one_test.go')
    def test_empty_source(self):
        with self.assertRaises(ValueError): self.check('source', '')
    def test_duplicate_source(self):
        data = self.contract(); data['files'].append({**data['files'][0], 'target': 'protocol/socks/two_test.go'})
        with patch.object(h.c, 'read_json', return_value=data):
            with self.assertRaises(ValueError): h.read_contract()

class IntegratedManifestControls(unittest.TestCase):
    def test_exact_group_inventory(self):
        contract = h.read_contract()
        self.assertEqual(len(contract['files']), 16)
        self.assertEqual({g['label']: len(g['leaves']) for g in contract['groups']}, {
            'sing_socks_owner': 62, 'sing_chunk_owner': 1, 'box_owner_adapter': 2,
            'box_owner_dialer': 13, 'box_owner_mixed': 1, 'box_owner_route': 633,
            'owner-vmess': 92, 'owner-vless': 4, 'box_owner_constructor': 30})
    def test_package_groups_select_disjoint_exact_tops(self):
        groups = h.read_contract()['groups']
        for group in groups:
            for leaf in group['leaves']:
                top = leaf.split('/')[0]
                selected = [g['label'] for g in groups if g['go_package'] == group['go_package']
                            and re.search(g['regex'], top)]
                self.assertEqual(selected, [group['label']], (group['label'], leaf, selected))

if __name__ == '__main__': unittest.main()
