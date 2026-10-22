"""Deterministic controls execute the actual workspace.js without browser/network I/O."""
from pathlib import Path
import subprocess
import unittest


class WorkspaceControllerTests(unittest.TestCase):
    def test_reload_draft_revision_and_failure_lifecycle(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["node", "--test-reporter=tap", "tests/workspace_reload_dom.js"], cwd=root,
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for expected in ("tests 24", "pass 24", "fail 0", "cancelled 0", "skipped 0", "todo 0"):
            self.assertRegex(result.stdout, r"(?m)^# " + expected + r"$", result.stdout)


if __name__ == "__main__":
    unittest.main()
