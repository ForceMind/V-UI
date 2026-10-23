"""Actual controller/template and pinned Vue with explicit synthetic I/O/host."""
import os
from pathlib import Path
import subprocess
import unittest


def assert_inbound_search(test_case):
    """Strict callable also required by the existing asset-enabled browser gate."""
    test_case.assertTrue(os.environ.get('VUI_FRONTEND_ASSETS'), 'pinned frontend assets required')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ['node', '--test-reporter=tap', 'tests/inbound_search_dom.js'],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    test_case.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    for expected in ('tests 10', 'pass 10', 'fail 0', 'cancelled 0', 'skipped 0', 'todo 0'):
        test_case.assertRegex(result.stdout, r'(?m)^# ' + expected + r'$', result.stdout)


@unittest.skipUnless(os.environ.get('VUI_FRONTEND_ASSETS'), 'explicit pinned frontend assets required')
class InboundSearchTests(unittest.TestCase):
    def test_visible_only_literal_filter_and_original_row_lifecycle(self):
        assert_inbound_search(self)


if __name__ == '__main__':
    unittest.main()
