"""Pinned Vue/Element Plus helper and shipped controller, not browser evidence."""
import os
from pathlib import Path
import subprocess
import unittest


def assert_editor_saving_controls(test_case):
    """Strict entry point shared with the asset-enabled Chromium gate."""
    test_case.assertTrue(os.environ.get("VUI_FRONTEND_ASSETS"), "pinned frontend assets required")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test-reporter=tap", "tests/editor_saving_controls_dom.js"],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    test_case.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    for expected in ("tests 6", "pass 6", "fail 0", "cancelled 0", "skipped 0", "todo 0"):
        test_case.assertRegex(result.stdout, r"(?m)^# " + expected + r"$", result.stdout)


@unittest.skipUnless(os.environ.get("VUI_FRONTEND_ASSETS"), "explicit pinned frontend assets required")
class EditorSavingControlsTests(unittest.TestCase):
    def test_all_six_controls_and_create_edit_save_lifecycles(self):
        assert_editor_saving_controls(self)


if __name__ == "__main__":
    unittest.main()
