"""Do not confuse fixture responses with actual target-bundle acceptance."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.proxy_policy_smoke import installed_proxy_policy_smoke


class InstalledPolicySmokeHarnessTests(unittest.TestCase):
    def test_requires_all_configuration_and_nonactivation_evidence(self):
        valid = {'proxy_policy_config_checks':8, 'proxy_resolution_worker':'bounded numeric-only',
                 'proxy_runtime_applied':False, 'proxy_double_hop_tested':False}
        invalid = [{}, {**valid,'proxy_policy_config_checks':7},
                   {**valid,'proxy_runtime_applied':True}, {**valid,'proxy_double_hop_tested':True},
                   {**valid,'proxy_resolution_worker':'not-run'}]
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

    def test_child_failure_does_not_publish_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = SimpleNamespace(returncode=1,stdout='{}',stderr='synthetic failure')
            with patch('scripts.proxy_policy_smoke.subprocess.run',return_value=result):
                with self.assertRaises(AssertionError):
                    installed_proxy_policy_smoke(Path(sys.executable),Path.cwd(),Path(temporary))
            self.assertEqual(list(Path(temporary).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
