"""Fixture/measurement contracts only; these are not native layout evidence."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from dashboard_layout_browser import (BASELINE_CSS, BASELINE_SHA256, BASELINE_SOURCE_COMMIT,
                                      horizontal_excess, read_baseline_css, replacement_headers)


class DashboardLayoutContracts(unittest.TestCase):
    def test_original_css_fixture_integrity_and_source(self):
        self.assertEqual(BASELINE_SOURCE_COMMIT, '8ed33ec446c4b03bf3a76ae7847957c7da58fa07')
        data = read_baseline_css()
        self.assertEqual(hashlib.sha256(data).hexdigest(), BASELINE_SHA256)
        self.assertEqual(data, BASELINE_CSS.read_bytes())
        # The repair appends narrowly-scoped overrides; legacy rules stay intact.
        current = Path(__file__).parents[1] / 'web/css/style.css'
        self.assertTrue(current.read_bytes().startswith(data))

    def test_changed_baseline_bytes_fail_before_browser_use(self):
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / 'baseline.css'
            candidate.write_bytes(read_baseline_css() + b'\n/* modified */\n')
            with self.assertRaisesRegex(ValueError, BASELINE_SOURCE_COMMIT):
                read_baseline_css(candidate)

    def test_containment_metric_distinguishes_a_real_violation(self):
        content = {'left': 50, 'right': 250}
        self.assertEqual(horizontal_excess({'left': 60, 'right': 186}, content), 0)
        self.assertEqual(horizontal_excess({'left': 140, 'right': 266}, content), 16)
        self.assertEqual(horizontal_excess({'left': 40, 'right': 166}, content), 10)

    def test_invalid_measurement_cannot_count_as_baseline_failure(self):
        for value in (float('nan'), float('inf'), -float('inf'), None, '126'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                horizontal_excess({'left': 20, 'right': value}, {'left': 0, 'right': 390})

    def test_fixture_body_replacement_preserves_security_headers(self):
        policy = {'cache-control': 'no-store', 'content-security-policy': "script-src 'self'",
                  'x-content-type-options': 'nosniff', 'content-type': 'text/css'}
        headers = {**policy, 'Content-Length': '999', 'etag': 'old', 'last-modified': 'old'}
        self.assertEqual(replacement_headers(headers), policy)
        self.assertEqual(headers['Content-Length'], '999', 'source response must stay unchanged')


if __name__ == '__main__':
    unittest.main()
