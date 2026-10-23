"""Execute the shipped Vue controller with deterministic, non-network I/O doubles."""
from pathlib import Path
import subprocess
import unittest


class InboundInstanceFrontendTests(unittest.TestCase):
    def test_mutation_conditions_and_editor_confirmation_lifecycle(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ['node', '--test-reporter=tap', 'tests/inbound_instance_dom.js'],
            cwd=root, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for expected in ('tests 48', 'pass 48', 'fail 0', 'cancelled 0', 'skipped 0', 'todo 0'):
            self.assertRegex(result.stdout, r'(?m)^# ' + expected + r'$', result.stdout)


if __name__ == '__main__':
    unittest.main()
