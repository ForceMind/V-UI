"""Bounded saved-preview consistency controls, not HTTP or browser qualification."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class WorkspacePreviewRevisionTests(unittest.TestCase):
    def test_saved_preview_matches_the_acknowledged_form_revision(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["node", "--test-reporter=tap", "tests/workspace_preview_revision_dom.js"],
            cwd=root, capture_output=True, text=True, timeout=90,
            env={**os.environ, "VUI_TEST_PYTHON_EXECUTABLE": sys.executable},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for expected in ("tests 17", "pass 17", "fail 0", "cancelled 0", "skipped 0", "todo 0"):
            self.assertRegex(result.stdout, r"(?m)^# " + expected + r"$", result.stdout)


if __name__ == "__main__":
    unittest.main()
