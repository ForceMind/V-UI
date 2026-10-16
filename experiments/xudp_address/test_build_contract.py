from copy import deepcopy
import json
from pathlib import Path
import hashlib
import ast
import subprocess
import unittest
import yaml

from build_experiment import SING_VERSION, PHASES, ROUTE_SOURCE_SHA256, verify_module_graph


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
    def test_route_blocking_witness_is_published_before_readiness(self):
        source = Path(__file__).with_name('route_lifecycle_test.go').read_text()
        for event, signal in (('fixture_pre_close_blocked', 'preCloseEntered'),
                              ('fixture_close_blocked', 'closeBlocked')):
            witness = 'c.log.event("' + event + '",map[string]any{"role":c.role})'
            ready = 'close(c.' + signal + ')'
            self.assertEqual(source.count(witness), 1)
            self.assertEqual(source.count(ready), 1)
            self.assertLess(source.index(witness), source.index(ready))

    def test_lifecycle_build_has_three_separate_source_identities(self):
        self.assertEqual(PHASES, ('baseline', 'patched', 'lifecycle'))
        self.assertEqual(ROUTE_SOURCE_SHA256, 'af68e556e07dfa861f1dc46c2f7232595c7912acb491b6077e555c61dda8b78c')
        source = Path(__file__).with_name('build_experiment.py').read_text()
        self.assertIn('vui-xudp-lifecycle-source-experiment', source)
        self.assertIn('schema_version=2', source)
        self.assertIn('lifecycle-provenance.json', source)
        self.assertIn('"before_sha256": source_hashes["sing_box_route_conn"]', source)
        self.assertIn('"after_sha256": provenance["phase_source_hashes"]["route_conn"]', source)
        self.assertIn('env={**env, "VUI_LIFECYCLE_PHASE": phase}', source)

    def test_independent_observation_never_overrides_original_failure(self):
        root = Path(__file__).resolve().parents[2]
        workflow = yaml.safe_load((root / ".github/workflows/xudp-address-experiment.yml").read_text())
        job = workflow["jobs"]["source-experiment"]
        self.assertNotIn("continue-on-error", job)
        for step in job["steps"]:
            self.assertNotIn("continue-on-error", step)
        build = next(s for s in job["steps"] if s.get("id") == "source_build")
        self.assertIn("verify_go_controls.py", build["run"])
        self.assertIn("verify_timeout_observations.py", build["run"])
        self.assertEqual(build["run"].count("--include-lifecycle"), 2)
        self.assertIn("verify_route_lifecycle.py", build["run"])
        observe = next(s for s in job["steps"] if "observe_runtime.py" in s.get("run", ""))
        self.assertEqual(observe["if"], "${{ !cancelled() && steps.source_build.outcome == 'success' }}")
        self.assertIn("--phase patched", observe["run"])
        self.assertIn("/tmp/vui-xudp-source/patched/sing-box", observe["run"])
        originals = [s for s in job["steps"] if "run_experiment.py" in s.get("run", "")]
        self.assertEqual(len(originals), 2)
        self.assertTrue(all("set -euo pipefail" in s["run"] for s in originals))
        lifecycle = [s for s in job['steps'] if 'lifecycle_runtime.py' in s.get('run', '')]
        self.assertEqual(len(lifecycle), 3)
        for phase, step in zip(PHASES, lifecycle):
            self.assertEqual(step['if'], "${{ !cancelled() && steps.source_build.outcome == 'success' }}")
            self.assertIn('--phase ' + phase, step['run'])
            self.assertIn('/' + phase + '/lifecycle-provenance.json', step['run'])
            self.assertIn('--output /tmp/vui-xudp-logs/lifecycle-' + phase, step['run'])
            self.assertIn('set -euo pipefail', step['run'])

    def test_runtime_packager_and_publish_workflows_are_unchanged(self):
        root = Path(__file__).resolve().parents[2]
        hashes = {
            'experiments/xudp_address/run_experiment.py': 'f87addb8df97e801566ffe0266eb1f0ee349f9967ea1dabcfdc2ee70f2f04544',
            'experiments/xudp_address/observe_runtime.py': '318e539cb9ea7d1a205b0aee904956d48824a05bbd349dc11472fe7e653dfd64',
            'experiments/xudp_address/remoteaddr.patch': 'ee08c4049b05d0fd72b1609839af574fe9be59b5e6c1ed4a1da54f0ffcb3a8ae',
            'experiments/xudp_address/xudp_remoteaddr_experiment_test.go': '84fdd740d5024125c13404423fc4fce17746745fa3db6f871d89067b60499d9b',
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
