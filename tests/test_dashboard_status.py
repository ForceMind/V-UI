"""Pinned Vue/rendered dashboard and actual controller, not browser evidence."""
import os
from pathlib import Path
import subprocess
import unittest


def assert_dashboard_status(test_case):
    """Strict entry point shared with the asset-enabled Chromium gate."""
    test_case.assertTrue(os.environ.get("VUI_FRONTEND_ASSETS"), "pinned frontend assets required")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test-reporter=tap", "tests/dashboard_status_dom.js"],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    test_case.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    for expected in ("tests 8", "pass 8", "fail 0", "cancelled 0", "skipped 0", "todo 0"):
        test_case.assertRegex(result.stdout, r"(?m)^# " + expected + r"$", result.stdout)


@unittest.skipUnless(os.environ.get("VUI_FRONTEND_ASSETS"), "explicit pinned frontend assets required")
class DashboardStatusTests(unittest.TestCase):
    def test_initial_failure_recovery_and_independent_status_reads(self):
        assert_dashboard_status(self)


if __name__ == "__main__":
    unittest.main()
