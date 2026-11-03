"""Navigation contracts execute real JS with deterministic history/DOM substitutes."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node required for navigation contract check')
class UINavigationTests(unittest.TestCase):
    def test_real_navigation_and_inbound_lifecycle(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['node', '--test', 'tests/ui-navigation-harness.cjs'],
                                cwd=root, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
