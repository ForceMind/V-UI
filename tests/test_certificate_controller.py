"""Execute certificates.js with deterministic DOM/fetch; not browser/ACME qualification."""
from pathlib import Path
import subprocess
import unittest


class CertificateControllerTests(unittest.TestCase):
    def test_refresh_selection_ordering_and_action_boundaries(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["node", "--test-reporter=tap", "tests/certificate_refresh_dom.js"],
            cwd=root, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for expected in ("tests 13", "pass 13", "fail 0", "cancelled 0", "skipped 0", "todo 0"):
            self.assertRegex(result.stdout, r"(?m)^# " + expected + r"$", result.stdout)


if __name__ == "__main__":
    unittest.main()
