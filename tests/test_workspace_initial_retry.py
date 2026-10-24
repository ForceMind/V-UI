"""Actual workspace controller/template, bounded synthetic DOM/fetch controls only."""
from pathlib import Path
import subprocess
import unittest


def assert_workspace_initial_retry_controls(test):
    """Use the same strict finite controls in ordinary and opt-in browser gates."""
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test-reporter=tap", "tests/workspace_initial_retry_dom.js"],
        cwd=root, capture_output=True, text=True, timeout=15,
    )
    test.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    for expected in ("tests 24", "pass 24", "fail 0", "cancelled 0", "skipped 0", "todo 0"):
        test.assertRegex(result.stdout, r"(?m)^# " + expected + r"$", result.stdout)


class WorkspaceInitialRetryTests(unittest.TestCase):
    def test_initial_load_retry_recovery_and_guards(self):
        assert_workspace_initial_retry_controls(self)


if __name__ == "__main__":
    unittest.main()
