from copy import deepcopy
import json
from pathlib import Path
import hashlib
import ast
import subprocess
import unittest

from build_experiment import SING_VERSION, verify_module_graph


class DependencyGraphTests(unittest.TestCase):
    def setUp(self):
        self.path = Path('/tmp/fixed-vmess')
        self.rows = [
            {"Path": "github.com/sagernet/sing-box", "Main": True},
            {"Path": "github.com/sagernet/sing", "Version": SING_VERSION},
            {"Path": "github.com/sagernet/sing-vmess", "Version": "v0.2.8", "Dir": str(self.path),
             "Replace": {"Path": str(self.path), "Dir": str(self.path)}},
        ]

    def run_graph(self, rows):
        return verify_module_graph('\n'.join(json.dumps(row) for row in rows), self.path)

    def test_exact_graph_is_normalized_without_cache_path_noise(self):
        wanted = self.run_graph(self.rows)
        noisy = deepcopy(self.rows)
        noisy[1].update(Dir='/cache/sing', Sum='official-module-sum', GoModSum='official-mod-sum')
        self.assertEqual(wanted, self.run_graph(noisy))

    def test_version_replace_workspace_duplicates_and_errors_rejected(self):
        for mode in ('version', 'replace', 'path', 'workspace', 'duplicate', 'error', 'empty'):
            with self.subTest(mode=mode):
                rows = deepcopy(self.rows)
                if mode == 'version': rows[1]['Version'] = 'v0.9.7'
                if mode == 'replace': rows[1]['Replace'] = {'Path': '/wrong'}
                if mode == 'path': rows[2]['Dir'] = '/wrong'
                if mode == 'workspace': rows[1]['Main'] = True
                if mode == 'duplicate': rows.append(rows[1])
                if mode == 'error': rows[1]['Error'] = {'Err': 'not available'}
                if mode == 'empty': rows = []
                with self.assertRaises(ValueError): self.run_graph(rows)


class ProductionIsolationTests(unittest.TestCase):
    def test_runtime_packager_and_publish_workflows_are_unchanged(self):
        root = Path(__file__).resolve().parents[2]
        hashes = {
            'scripts/build_bundle.py': '6a31474fe46560e61f6f8e899185aaa881f64a14396165edbbbad53edb294f54',
            '.github/workflows/publish.yml': '8c2645f89a883684ae5b7f3f7177a89ab8a5b54ef7582cf438777e3dc2003d31',
            '.github/workflows/release.yml': '28111d7097e38f8446c33577f6e3c555aeb6938e8e714795fbb73b8f35a3a4e2',
        }
        for name, expected in hashes.items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), expected, name)
        tree = ast.parse((root / 'scripts/build_bundle.py').read_text())
        allowed = next(ast.literal_eval(node.value) for node in ast.walk(tree) if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == 'allowed_files' for target in node.targets))
        prefixes = next(ast.literal_eval(node.args[0]) for node in ast.walk(tree) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute) and node.func.attr == 'startswith'
                        and isinstance(node.args[0], ast.Tuple))
        for name in ('experiments/xudp_address/remoteaddr.patch', 'experiments/xudp_address/sing-box',
                     '.experiment-source/sing-box', '.github/workflows/xudp-address-experiment.yml'):
            self.assertFalse(name in allowed or name.startswith(prefixes), name)
        for name, expected in {
            'app': 'f56d5f4fa3cbfa343c51c4c401fd5c5162fb48bf',
            'web': '6b1f7f8984e0ac5bb57c006764d9bf890de6aca9',
            'deploy': '4a36e48b2a970d252d1e7a37af31aa3b202f2d96',
        }.items():
            actual = subprocess.check_output(['git', 'rev-parse', 'HEAD:' + name], cwd=root, text=True).strip()
            self.assertEqual(actual, expected, name)


if __name__ == '__main__':
    unittest.main()
