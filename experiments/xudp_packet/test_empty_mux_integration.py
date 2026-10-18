"""Pure wiring/verifier controls only; synthetic rows are NOT Go evidence.

No compiler, core, socket or runtime operation is invoked. The real strict
empty-datagram verifier is exercised, with unrelated shared checks excluded
only inside its explicitly synthetic unit fixture.
"""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
MUX = HERE.parent/'xudp_mux'


def load(name, path):
    previous = list(sys.path)
    try:
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = previous


builder = load('packet_empty_integration_builder', HERE/'build_and_test.py')
verifier = load('packet_empty_integration_verifier', HERE/'verify_evidence.py')
prepare = load('packet_empty_integration_prepare', HERE/'prepare_evidence.py')
boundary = load('packet_empty_integration_boundary', HERE/'packet_boundary.py')
fixture = load('packet_empty_synthetic_fixture', MUX/'test_verify_empty_datagram.py')
PACKAGE = 'github.com/sagernet/sing-vmess'
PATTERN = '^TestMuxEmptyDatagram(Raw|AES|ChaCha)$'
EMPTY_CHECKS = {'mux-empty-datagram-race', 'mux-empty-datagram-debug'}
SHARED_CHECKS = {kind+'-'+mode for kind in
                 ('mux-unit', 'mux-encrypted', 'mux-encrypted-cancel', 'mux-empty-datagram')
                 for mode in ('race', 'debug')} | {'mux-route-race'}
BUILD_CHECKS = {package+'-'+mode for package in
                ('common-network', 'common-bufio', 'protocol-socks')
                for mode in ('race', 'debug', 'darwin-compile', 'windows-compile')} | {
                    'build', 'module-verify'} | SHARED_CHECKS


class EmptyMuxIntegrationTests(unittest.TestCase):
    def evidence(self, root):
        for label in sorted(EMPTY_CHECKS):
            debug = label.endswith('-debug')
            args = ['go', 'test', '-json', '-count=1', '-timeout=120s', '-race',
                    *(['-tags=debug'] if debug else []), '-run', PATTERN, PACKAGE]
            exit_row = dict(args=args, returncode=0, environment={
                'GOOS': 'linux', 'GOARCH': 'amd64', 'CGO_ENABLED': '1',
                'GOTOOLCHAIN': 'local', 'GOWORK': 'off'})
            (root/(label+'.exit.json')).write_text(json.dumps(exit_row)+'\n')
            (root/(label+'.stdout')).write_text(''.join(json.dumps(row)+'\n' for row in fixture.synthetic_rows()))
            (root/(label+'.stderr')).write_text('')

    def shared(self, root):
        # The fixture isolates the two new checks. Their actual strict verifier
        # is NOT mocked; every empty-datagram leaf/event is still required.
        previous = list(sys.path)
        try:
            with patch.object(verifier, 'EXTRA_CHECKS', EMPTY_CHECKS):
                return verifier.verify_shared(root)
        finally:
            sys.path[:] = previous

    def test_source_injection_and_runner_selection(self):
        self.assertEqual(builder.EXTRA_TESTS['experiments/xudp_mux/mux_empty_datagram_test.go'],
                         'sing-vmess/mux_empty_datagram_test.go')
        self.assertEqual(builder.EXTRA_CHECKS, SHARED_CHECKS)
        self.assertEqual(verifier.EXTRA_CHECKS, SHARED_CHECKS)
        tree = ast.parse((HERE/'build_and_test.py').read_text())
        loops = [node for node in ast.walk(tree) if isinstance(node, ast.For) and
                 isinstance(node.target, ast.Tuple) and
                 [getattr(item, 'id', None) for item in node.target.elts] == ['kind', 'pattern']]
        self.assertEqual(len(loops), 1)
        pairs = dict(ast.literal_eval(loops[0].iter))
        self.assertEqual(pairs['mux-empty-datagram'], PATTERN)
        modes = [node for node in ast.walk(loops[0]) if isinstance(node, ast.For) and
                 isinstance(node.target, ast.Tuple) and
                 [getattr(item, 'id', None) for item in node.target.elts] == ['mode', 'extra']]
        self.assertEqual(len(modes), 1)
        self.assertEqual(ast.literal_eval(modes[0].iter),
                         (('race', ['-race']), ('debug', ['-race', '-tags=debug'])))
        source = ast.get_source_segment((HERE/'build_and_test.py').read_text(), loops[0])
        self.assertIn("'github.com/sagernet/sing-vmess'],box,env", source)
        self.assertIn("'-timeout=120s' if kind=='mux-empty-datagram'", source)

    def test_same_fixed_patch_chain_and_failure_guard(self):
        self.assertEqual(builder.PATCHES, (
            'experiments/xudp_address/remoteaddr.patch',
            'experiments/xudp_address/lifecycle.patch',
            'experiments/xudp_mux/mux.patch',
            'experiments/xudp_mux/route_cancel.patch',
            'experiments/xudp_packet/receive.patch'))
        source = (HERE/'build_and_test.py').read_text()
        self.assertIn("c.need(result.returncode == 0, 'Patch did not apply: ' + name)", source)
        self.assertIn("'-replace=github.com/sagernet/sing=' + str(sing)", source)
        self.assertIn("'-replace=github.com/sagernet/sing-vmess=' + str(vmess)", source)
        self.assertIn("'product_qualified': False", source)
        self.assertIn("'Build changed upstream source/go.sum'", source)

    def test_exact_build_check_set_23_and_new_failures_rejected(self):
        self.assertEqual(len(SHARED_CHECKS), 9)
        self.assertEqual(len(BUILD_CHECKS), 23)
        valid = {'checks': [dict(label=label, returncode=0) for label in sorted(BUILD_CHECKS)]}
        boundary.verify_build_record(valid)
        # The former otherwise-green 21-check record cannot qualify this head.
        old = copy.deepcopy(valid)
        old['checks'] = [row for row in old['checks'] if row['label'] not in EMPTY_CHECKS]
        with self.assertRaises(ValueError): boundary.verify_build_record(old)
        for label in sorted(EMPTY_CHECKS):
            for failure in ('missing', 'duplicate', 'failed', 'bool'):
                with self.subTest(label=label, failure=failure):
                    mutated = copy.deepcopy(valid)
                    row = next(row for row in mutated['checks'] if row['label'] == label)
                    if failure == 'missing': mutated['checks'].remove(row)
                    elif failure == 'duplicate': mutated['checks'].append(copy.deepcopy(row))
                    else: row['returncode'] = 1 if failure == 'failed' else False
                    with self.assertRaises(ValueError): boundary.verify_build_record(mutated)

    def test_real_strict_shared_verifier_accepts_complete_synthetic_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.evidence(root); report = self.shared(root)
            self.assertEqual(set(report), EMPTY_CHECKS)
            for result in report.values():
                self.assertEqual((result['top_level_cases'], result['leaf_cases'], result['events']), (3, 24, 120))
                self.assertEqual(result['scope'], 'isolated-mux-empty-datagram')
                self.assertIs(result['product_qualified'], False)

    def test_shared_command_flag_environment_and_selection_mutations_rejected(self):
        for label in sorted(EMPTY_CHECKS):
            for kind in ('no-race', 'debug-flags', 'wrong-pattern', 'wrong-package', 'wrong-env', 'failed'):
                with self.subTest(label=label, kind=kind), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory); self.evidence(root)
                    path = root/(label+'.exit.json'); row = json.loads(path.read_text())
                    if kind == 'no-race': row['args'].remove('-race')
                    elif kind == 'debug-flags':
                        if '-tags=debug' in row['args']: row['args'].remove('-tags=debug')
                        else: row['args'].insert(2, '-tags=debug')
                    elif kind == 'wrong-pattern': row['args'][row['args'].index('-run')+1] = '^TestMuxEmptyDatagramAES$'
                    elif kind == 'wrong-package': row['args'][-1] = 'github.com/sagernet/sing'
                    elif kind == 'wrong-env': row['environment']['CGO_ENABLED'] = '0'
                    else: row['returncode'] = 1
                    path.write_text(json.dumps(row)+'\n')
                    with self.assertRaises(ValueError): self.shared(root)

    def test_shared_empty_leaf_event_and_package_mutations_rejected(self):
        for label in sorted(EMPTY_CHECKS):
            for kind in ('missing-leaf', 'skip', 'tuple', 'delivery', 'end', 'join', 'package', 'tail'):
                with self.subTest(label=label, kind=kind), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory); self.evidence(root); rows = fixture.synthetic_rows()
                    if kind == 'missing-leaf': rows = [r for r in rows if r.get('Test') != 'TestMuxEmptyDatagramAES/StatusNewReadFrom']
                    elif kind == 'skip': next(r for r in rows if r['Action'] == 'pass' and '/' in r.get('Test', ''))['Action'] = 'skip'
                    elif kind == 'tuple': fixture.modify(rows, 'case_verified', 'tuple_payload_exact', False)
                    elif kind == 'delivery': fixture.modify(rows, 'case_verified', 'pipe_and_address_completed', False)
                    elif kind == 'end': fixture.modify(rows, 'ends_drained', 'end_count', 2)
                    elif kind == 'join': fixture.modify(rows, 'parent_joined', 'writer_joined', False)
                    elif kind == 'package': rows[0]['Package'] = 'another-package'
                    else: rows[0]['Output'] = rows[0]['Output'][:10]
                    (root/(label+'.stdout')).write_text(''.join(json.dumps(row)+'\n' for row in rows))
                    with self.assertRaises(ValueError): self.shared(root)

    def test_text_allowlist_and_jsonl_classifier_include_both_checks(self):
        allowed = prepare.allowed_build()
        expected = {label+suffix for label in EMPTY_CHECKS |
                    {'gofmt-shared-mux_empty_datagram_test'}
                    for suffix in ('.stdout', '.stderr', '.exit.json')}
        self.assertEqual(len(expected), 9)
        self.assertTrue(expected <= allowed)
        self.assertEqual({name for name in allowed if 'empty' in name}, expected)
        self.assertTrue({label+'.stdout' for label in EMPTY_CHECKS} <= prepare.GO_JSONL)
        self.assertFalse({label+'.stderr' for label in EMPTY_CHECKS} & prepare.GO_JSONL)
        self.assertEqual(len(prepare.GO_JSONL), 15)
        self.assertNotIn('candidate-sing-box', allowed)
        self.assertNotIn('mux-empty-datagram-race.test', allowed)

    def test_new_jsonl_preserved_as_structured_text_not_runtime_qualification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); build = root/'build'; build.mkdir(); self.evidence(build)
            output = root/'upload'
            prepare.prepare(build, output)
            manifest = json.loads((output/'MANIFEST.json').read_text())
            self.assertEqual(manifest['count'], 6)
            for label in sorted(EMPTY_CHECKS):
                self.assertEqual((output/(label+'.stdout')).read_bytes(), (build/(label+'.stdout')).read_bytes())
            self.assertEqual(set(self.shared(output)), EMPTY_CHECKS)
            invalid = output/'mux-empty-datagram-race.stdout'
            invalid.write_text('{"Package":"one","Package":"two"}\n')
            with self.assertRaises(ValueError): prepare.validate_text(invalid)


if __name__ == '__main__':
    unittest.main()
