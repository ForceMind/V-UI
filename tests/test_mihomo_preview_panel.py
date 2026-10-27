"""Actual local controller/template checked with synthetic DOM/fetch, not Chromium."""
from pathlib import Path
import subprocess
import unittest


class MihomoPreviewPanelTests(unittest.TestCase):
    def test_controller_template_lifecycle_and_privacy(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['node', '--test-reporter=tap', 'tests/mihomo_preview_dom.js'],
                                cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for expected in ('tests 25', 'pass 25', 'fail 0', 'cancelled 0', 'skipped 0', 'todo 0'):
            self.assertRegex(result.stdout, r'(?m)^# ' + expected + r'$', result.stdout)


if __name__ == '__main__':
    unittest.main()
