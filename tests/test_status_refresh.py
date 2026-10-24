"""Actual pinned UI/controller with synthetic HTTP/clock/host, not native proof."""
import os
from pathlib import Path
import subprocess
import unittest


def assert_status_refresh_control(case):
    """Strict finite entry point, also required by the asset-enabled native gate."""
    case.assertTrue(os.environ.get('VUI_FRONTEND_ASSETS'), 'pinned frontend assets required')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ['node', '--test-reporter=tap', 'tests/status_refresh_dom.js'],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    case.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    case.assertRegex(result.stdout, r'(?m)^1\.\.18$', result.stdout)
    for expected in ('tests 18', 'pass 18', 'fail 0', 'cancelled 0', 'skipped 0', 'todo 0'):
        case.assertRegex(result.stdout, r'(?m)^# ' + expected + r'$', result.stdout)


@unittest.skipUnless(os.environ.get('VUI_FRONTEND_ASSETS'), 'explicit pinned frontend assets required')
class StatusRefreshTests(unittest.TestCase):
    def test_readonly_refresh_authentication_single_flight_and_independent_recovery(self):
        assert_status_refresh_control(self)


if __name__ == '__main__':
    unittest.main()
