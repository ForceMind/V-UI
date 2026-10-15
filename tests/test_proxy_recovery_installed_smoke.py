"""Local harness regressions are distinct from four-target package acceptance."""
import ast
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import sysconfig
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import portable_stage_smoke
from scripts.proxy_recovery_smoke import EXPECTED, PROBE, installed_proxy_recovery_smoke


SOURCE = Path(__file__).resolve().parents[1]


class InstalledRecoverySmokeTests(unittest.TestCase):
    def fixture(self, top):
        """Separate copied payload and selected interpreter, not a release bundle."""
        payload = top / 'payload'
        shutil.copytree(SOURCE / 'app', payload / 'app',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        runtime = top / 'runtime'
        (runtime / 'bin').mkdir(parents=True)
        python = runtime / 'bin' / 'python'
        python.symlink_to(sys.executable)
        # Reuse already installed test dependencies; do not download or run pip.
        (runtime / 'pyvenv.cfg').write_text(
            'home = ' + str(Path(sys._base_executable).parent) + '\n'
            'include-system-site-packages = false\n')
        lib = runtime / 'lib' / ('python' + sysconfig.get_python_version())
        lib.mkdir(parents=True)
        (lib / 'site-packages').symlink_to(sysconfig.get_path('purelib'), target_is_directory=True)
        root = top / 'installation'
        (root / 'data').mkdir(parents=True, mode=0o700)
        (root / 'data' / 'existing-user-data').write_bytes(b'untouched actual installation sentinel')
        return python, payload, root

    def test_actual_child_uses_selected_runtime_and_copied_payload_not_checkout(self):
        with tempfile.TemporaryDirectory(prefix='vui-recovery-harness-') as temporary:
            top = Path(temporary)
            python, payload, root = self.fixture(top)
            marker = top / 'installed-import.json'
            module = payload / 'app/services/proxy_recovery.py'
            with module.open('a') as output:
                output.write('\nPath(' + repr(str(marker)) + ').write_text(json.dumps({'
                    '"module": __file__, "python": sys.executable, "prefix": sys.prefix}))\n')
            # A hostile inherited checkout path must never leak into this child.
            with patch.dict(os.environ, {'PYTHONPATH': str(SOURCE), 'PYTHONOPTIMIZE': '2'}):
                evidence = installed_proxy_recovery_smoke(python, payload, root)
            self.assertEqual(evidence, EXPECTED)
            imported = json.loads(marker.read_text())
            self.assertEqual(Path(imported['module']), module)
            self.assertEqual(Path(imported['python']), python)
            self.assertEqual(Path(imported['prefix']), top / 'runtime')
            self.assertEqual(list(root.iterdir()), [root / 'data'])
            self.assertEqual(list((root / 'data').iterdir()), [root / 'data/existing-user-data'])
            self.assertEqual((root / 'data/existing-user-data').read_bytes(),
                             b'untouched actual installation sentinel')

    def test_missing_installed_module_never_falls_back_to_checkout(self):
        with tempfile.TemporaryDirectory(prefix='vui-recovery-harness-') as temporary:
            python, payload, root = self.fixture(Path(temporary))
            (payload / 'app/services/proxy_recovery.py').unlink()
            with patch.dict(os.environ, {'PYTHONPATH': str(SOURCE)}):
                with self.assertRaisesRegex(AssertionError, 'Installed isolated proxy recovery smoke failed'):
                    installed_proxy_recovery_smoke(python, payload, root)
            self.assertEqual(list(root.iterdir()), [root / 'data'])

    def test_installed_network_or_core_attempt_cannot_report_success(self):
        for injection in ('__import__("socket").socket()',
                          '__import__("socket").getaddrinfo("example.test", 443)',
                          '__import__("subprocess").run(["false"])',
                          'CoreRuntime("sing-box", Path("/not-a-core"), Path("/not-a-runtime"))'):
            with self.subTest(injection=injection), tempfile.TemporaryDirectory() as temporary:
                python, payload, root = self.fixture(Path(temporary))
                # Import happens after guards are installed; wrap preview to
                # ensure core calls are guarded too, after module initialization.
                module = payload / 'app/services/proxy_recovery.py'
                with module.open('a') as output:
                    output.write('\nfrom app.services.core_runtime import CoreRuntime\n'
                        '_original_preview = preview\n'
                        'def preview(*args, **kwargs):\n    ' + injection + '\n'
                        '    return _original_preview(*args, **kwargs)\n')
                with self.assertRaisesRegex(AssertionError, 'Offline recovery attempted'):
                    installed_proxy_recovery_smoke(python, payload, root)
                self.assertEqual(list(root.iterdir()), [root / 'data'])

    def test_every_output_field_is_required_exactly_typed_and_complete(self):
        invalid = [{}, [], None]
        for key, value in EXPECTED.items():
            missing = copy.deepcopy(EXPECTED)
            del missing[key]
            invalid.append(missing)
            invalid.append({**EXPECTED, key: None})
            if type(value) is bool:
                invalid.extend(({**EXPECTED, key: not value}, {**EXPECTED, key: int(value)}))
            elif type(value) is int:
                invalid.extend(({**EXPECTED, key: value - 1}, {**EXPECTED, key: float(value)},
                                {**EXPECTED, key: True}))
        invalid.extend(({**EXPECTED, 'unknown': True},
                        {**EXPECTED, 'proxy_recovery_states': ['00', '10', '01']},
                        {**EXPECTED, 'proxy_recovery_states': ['00', '10', '01', '01']},
                        {**EXPECTED, 'proxy_recovery': 'checkout-only'}))
        outputs = [json.dumps(value) for value in invalid]
        outputs.extend(('', 'not-json', json.dumps(EXPECTED) + '\n{}',
                        json.dumps(EXPECTED)[:-1] + ',"proxy_recovery_restores":4}',
                        ' ' * 8193 + json.dumps(EXPECTED)))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for stdout in outputs:
                with self.subTest(stdout=stdout[:100]), patch(
                        'scripts.proxy_recovery_smoke.subprocess.run',
                        return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr='')):
                    with self.assertRaisesRegex(AssertionError, 'evidence was invalid or incomplete'):
                        installed_proxy_recovery_smoke(Path(sys.executable), SOURCE, root)
                self.assertEqual(list(root.iterdir()), [])

    def test_exact_runtime_arguments_and_clean_private_child_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python, payload = root / 'runtime/bin/python3', root / 'payload'
            response = SimpleNamespace(returncode=0, stdout=json.dumps(EXPECTED), stderr='')
            with patch.dict(os.environ, {'PYTHONPATH': str(SOURCE), 'PYTHONOPTIMIZE': '1'}), patch(
                    'scripts.proxy_recovery_smoke.subprocess.run', return_value=response) as run:
                self.assertEqual(installed_proxy_recovery_smoke(python, payload, root), EXPECTED)
            command = run.call_args.args[0]
            options = run.call_args.kwargs
            self.assertEqual(command, [str(python), '-B', '-c', PROBE, str(payload), str(python)])
            self.assertEqual(options['cwd'], payload)
            self.assertEqual(options['timeout'], 90)
            self.assertTrue(options['capture_output'])
            self.assertTrue(options['text'])
            env = options['env']
            self.assertEqual(env['PYTHONPATH'], str(payload))
            self.assertNotIn('PYTHONOPTIMIZE', env)
            self.assertTrue(Path(env['VUI_DATA_DIR']).is_relative_to(root))
            self.assertNotEqual(Path(env['VUI_DATA_DIR']), root / 'data')
            self.assertEqual(list(root.iterdir()), [])

    def test_child_failure_cannot_pass_even_with_success_json(self):
        with tempfile.TemporaryDirectory() as temporary, patch(
                'scripts.proxy_recovery_smoke.subprocess.run',
                return_value=SimpleNamespace(returncode=1, stdout=json.dumps(EXPECTED),
                                             stderr='synthetic child failure')):
            with self.assertRaisesRegex(AssertionError, 'synthetic child failure'):
                installed_proxy_recovery_smoke(Path(sys.executable), SOURCE, Path(temporary))
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_portable_gate_calls_smoke_with_actual_staged_python_and_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / 'releases/exact-installed/payload'
            python = root / 'releases/exact-installed/runtime/python/bin/python3'
            with patch.object(sys, 'argv', ['portable_stage_smoke.py', '--bundle', str(root / 'bundle.zip'),
                                           '--sha256', 'a' * 64, '--root', str(root)]), \
                    patch.object(portable_stage_smoke.release_tools, 'stage', return_value='exact-installed'), \
                    patch.object(portable_stage_smoke.release_tools, 'verify_runtime',
                                 return_value={'runtime_key': 'x86_64-gnu'}), \
                    patch.object(portable_stage_smoke.release_tools, 'runtime_python', return_value=python), \
                    patch.object(portable_stage_smoke, 'installed_menu_smoke', return_value={}), \
                    patch.object(portable_stage_smoke, 'installed_proxy_import_smoke', return_value={}), \
                    patch.object(portable_stage_smoke, 'installed_proxy_policy_smoke', return_value={}), \
                    patch.object(portable_stage_smoke, 'portable_menu_stage_smoke', return_value={}), \
                    patch.object(portable_stage_smoke, 'installed_proxy_recovery_smoke',
                                 return_value=EXPECTED) as recovery, \
                    patch.object(portable_stage_smoke.subprocess, 'run',
                                 return_value=SimpleNamespace(returncode=0, stdout='synthetic-version', stderr='')), \
                    patch('builtins.print') as output:
                portable_stage_smoke.main()
            recovery.assert_called_once_with(python, payload, root)
            self.assertTrue(EXPECTED.items() <= json.loads(output.call_args.args[0]).items())

    def test_deployment_gate_calls_smoke_with_staged_runtime(self):
        # Do not enable the real HTTPS/browser/deployment suite in this unit test.
        tree = ast.parse((SOURCE / 'tests/test_release_deployment.py').read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'installed_proxy_recovery_smoke']
        self.assertEqual(len(calls), 1)
        self.assertEqual([ast.unparse(argument) for argument in calls[0].args], ['python', 'payload', 'root'])


if __name__ == '__main__':
    unittest.main()
