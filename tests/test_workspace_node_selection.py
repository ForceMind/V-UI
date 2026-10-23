"""Node-selection controls execute real workspace.js without browser or network I/O."""
from pathlib import Path
import subprocess
import unittest


class WorkspaceNodeSelectionTests(unittest.TestCase):
    def test_current_inventory_precondition_and_refresh_lifecycle(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["node", "--test-reporter=tap", "tests/workspace_node_selection_dom.js"],
            cwd=root, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for expected in ("tests 28", "pass 28", "fail 0", "cancelled 0", "skipped 0", "todo 0"):
            self.assertRegex(result.stdout, r"(?m)^# " + expected + r"$", result.stdout)


if __name__ == "__main__":
    unittest.main()
