"""Shipped controller/header with pinned Vue/Element Plus and synthetic I/O/host."""
import os
from pathlib import Path
import subprocess
import unittest


def assert_core_restart(test_case):
    """Strict finite entry point also called by the existing native browser gate."""
    test_case.assertTrue(os.environ.get('VUI_FRONTEND_ASSETS'), 'pinned frontend assets required')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ['node', '--test-reporter=tap', 'tests/core_restart_dom.js'],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    test_case.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    for expected in ('tests 10', 'pass 10', 'fail 0', 'cancelled 0', 'skipped 0', 'todo 0'):
        test_case.assertRegex(result.stdout, r'(?m)^# ' + expected + r'$', result.stdout)


@unittest.skipUnless(os.environ.get('VUI_FRONTEND_ASSETS'), 'explicit pinned frontend assets required')
class CoreRestartTests(unittest.TestCase):
    def test_rendered_pending_ownership_outcomes_and_synchronous_admission(self):
        assert_core_restart(self)


if __name__ == '__main__':
    unittest.main()
