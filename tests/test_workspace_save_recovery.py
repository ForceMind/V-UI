"""Five controller/direct-backend SAVE cases, not HTTP or browser qualification."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class WorkspaceSaveRecoveryTests(unittest.TestCase):
    def test_save_acknowledgment_and_explicit_recovery(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["node", "--test-reporter=tap", "tests/workspace_save_recovery_dom.js"],
            cwd=root, capture_output=True, text=True, timeout=90,
            env={**os.environ, "VUI_TEST_PYTHON_EXECUTABLE": sys.executable},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for expected in ("tests 5", "pass 5", "fail 0", "cancelled 0", "skipped 0", "todo 0"):
            self.assertRegex(result.stdout, r"(?m)^# " + expected + r"$", result.stdout)


if __name__ == "__main__":
    unittest.main()
