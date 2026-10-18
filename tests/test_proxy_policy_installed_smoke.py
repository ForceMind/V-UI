"""Do not confuse fixture responses with actual target-bundle acceptance."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import release_tools
from scripts.proxy_policy_smoke import EXPECTED, installed_proxy_policy_smoke
from tests import test_proxy_recovery_installed_smoke as recovery_fixture


class InstalledPolicySmokeHarnessTests(unittest.TestCase):
    def test_requires_all_configuration_and_nonactivation_evidence(self):
        valid = dict(EXPECTED)
        invalid = [{}, {**valid,'proxy_policy_config_checks':7},
                   {**valid,'proxy_policy_mixed_config_checks':7},
                   {**valid,'proxy_policy_same_config_distinct_identities':3},
                   {**valid,'proxy_runtime_applied':True}, {**valid,'proxy_double_hop_tested':True},
                   {**valid,'proxy_resolution_worker':'not-run'},
                   {**valid,'proxy_policy_mixed_config_checks':8.0},
                   {**valid,'unknown':True}, [], None]
        for evidence in invalid:
            with self.subTest(evidence=evidence), tempfile.TemporaryDirectory() as temporary:
                result = SimpleNamespace(returncode=0,stdout=json.dumps(evidence),stderr='')
                with patch('scripts.proxy_policy_smoke.subprocess.run',return_value=result):
                    with self.assertRaises(AssertionError):
                        installed_proxy_policy_smoke(Path(sys.executable),Path.cwd(),Path(temporary))
                self.assertEqual(list(Path(temporary).iterdir()), [])
        with tempfile.TemporaryDirectory() as temporary:
            result = SimpleNamespace(returncode=0,stdout=json.dumps(valid),stderr='')
            with patch('scripts.proxy_policy_smoke.subprocess.run',return_value=result) as run:
                self.assertEqual(installed_proxy_policy_smoke(
                    Path(sys.executable),Path.cwd(),Path(temporary)),valid)
                self.assertEqual(run.call_args.kwargs['timeout'],90)
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_actual_copied_payload_checks_both_modes_without_launching(self):
        # A fake check-only executable proves harness selection and matrix
        # completeness; this test is not pinned-binary configuration evidence.
        with tempfile.TemporaryDirectory(prefix='vui-policy-harness-') as temporary:
            top = Path(temporary)
            python, payload, root = recovery_fixture.InstalledRecoverySmokeTests().fixture(top)
            marker, checked = top/'installed-import.txt', top/'checked.jsonl'
            module = payload/'app/services/inbound_policy.py'
            with module.open('a') as out:
                out.write('\n__import__("pathlib").Path(' + repr(str(marker)) + ').write_text(__file__)\n')
            binary = payload/'cores'/release_tools.target_arch()/'sing-box'
            binary.parent.mkdir(parents=True)
            binary.write_text('#!' + str(python) + '\n'
                'import json,sys\nfrom pathlib import Path\n'
                'if sys.argv[1:] == ["version"]: print("sing-box version 1.14.2")\n'
                'else:\n'
                ' assert sys.argv[1:3] == ["check","-c"] and len(sys.argv) == 4\n'
                ' config=json.loads(Path(sys.argv[3]).read_text())\n'
                ' with Path(' + repr(str(checked)) + ').open("a") as out: '
                'out.write(json.dumps(config)+"\\n")\n')
            binary.chmod(0o700)
            with patch.dict(os.environ, {'PYTHONPATH':str(Path.cwd()), 'PYTHONOPTIMIZE':'2'}):
                self.assertEqual(installed_proxy_policy_smoke(python,payload,root), EXPECTED)
            self.assertEqual(marker.read_text(), str(module))
            configs = [json.loads(line) for line in checked.read_text().splitlines()]
            self.assertEqual(len(configs), 16)
            mixed = [value for value in configs if len(
                [item for item in value['inbounds'] if item['type'] == 'vless']) == 2]
            self.assertEqual(len(mixed), 8)
            for value in mixed:
                tags = [item['tag'] for item in value['inbounds']]
                self.assertIn('synthetic-node-1', tags)
                self.assertIn('vui-relay-ingress-2', tags)
                for rule in value['route']['rules']:
                    self.assertNotIn('synthetic-node-1', rule['inbound'])
            self.assertEqual(list(root.iterdir()), [root/'data'])
            self.assertEqual((root/'data/existing-user-data').read_bytes(),
                             b'untouched actual installation sentinel')

    def test_missing_installed_policy_module_never_falls_back_to_checkout(self):
        with tempfile.TemporaryDirectory(prefix='vui-policy-harness-') as temporary:
            python, payload, root = recovery_fixture.InstalledRecoverySmokeTests().fixture(Path(temporary))
            (payload/'app/services/inbound_policy.py').unlink()
            with patch.dict(os.environ, {'PYTHONPATH':str(Path.cwd())}):
                with self.assertRaisesRegex(AssertionError, 'Installed proxy policy check failed'):
                    installed_proxy_policy_smoke(python,payload,root)
            self.assertEqual(list(root.iterdir()), [root/'data'])

    def test_child_failure_does_not_publish_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = SimpleNamespace(returncode=1,stdout='{}',stderr='synthetic failure')
            with patch('scripts.proxy_policy_smoke.subprocess.run',return_value=result):
                with self.assertRaises(AssertionError):
                    installed_proxy_policy_smoke(Path(sys.executable),Path.cwd(),Path(temporary))
            self.assertEqual(list(Path(temporary).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
