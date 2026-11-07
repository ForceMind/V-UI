"""Shared user-task controller tests, not a browser or real installation."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node required for controller tests')
class OperationFlowTests(unittest.TestCase):
    def test_real_controller_synthetic_requests_and_recovery(self):
        result = subprocess.run(['node', '--test-reporter=tap', 'tests/operation_flow_dom.js'],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r'(?m)^# fail 0$')
        self.assertRegex(result.stdout, r'(?m)^# skipped 0$')

    def test_real_view_button_states_with_synthetic_dom(self):
        result = subprocess.run(['node', '--test-reporter=tap', 'tests/simple_operations_dom.js'],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r'(?m)^# fail 0$')
        self.assertRegex(result.stdout, r'(?m)^# skipped 0$')

