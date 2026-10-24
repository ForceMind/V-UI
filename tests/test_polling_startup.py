"""Pinned Vue/Axios and actual controller with synthetic I/O, clock and host."""
import os
from pathlib import Path
import subprocess
import unittest


def assert_polling_startup_control(case):
    """Strict callable required by the existing asset-enabled browser gate."""
    case.assertTrue(os.environ.get('VUI_FRONTEND_ASSETS'), 'pinned frontend assets required')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ['node', '--test-reporter=tap', 'tests/polling_startup_dom.js'],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    case.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    case.assertRegex(result.stdout, r'(?m)^1\.\.19$', result.stdout)
    for expected in ('tests 19', 'pass 19', 'fail 0', 'cancelled 0', 'skipped 0', 'todo 0'):
        case.assertRegex(result.stdout, r'(?m)^# ' + expected + r'$', result.stdout)


@unittest.skipUnless(os.environ.get('VUI_FRONTEND_ASSETS'), 'explicit pinned frontend assets required')
class PollingStartupTests(unittest.TestCase):
    def test_auxiliary_pending_does_not_block_authenticated_status_polling(self):
        assert_polling_startup_control(self)


if __name__ == '__main__':
    unittest.main()
