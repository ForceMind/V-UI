"""Shipped simple-node controller with synthetic API and lifecycle inputs only."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node required for frontend controller check')
class SimpleNodeWizardTests(unittest.TestCase):
    def test_purpose_certificate_create_recovery_and_explicit_delivery(self):
        result = subprocess.run(
            ['node', '--test-reporter=tap', 'tests/simple_node_wizard_dom.js'],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for expected in ('tests 14', 'pass 14', 'fail 0', 'cancelled 0', 'skipped 0', 'todo 0'):
            self.assertRegex(result.stdout, r'(?m)^# ' + expected + r'$', result.stdout)


if __name__ == '__main__':
    unittest.main()
