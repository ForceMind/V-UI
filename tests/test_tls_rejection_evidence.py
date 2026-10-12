"""Reason collection cannot turn timeouts, generic logs or delivery into a pass."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from loopback_helpers import require_tls_rejection, require_rejection_evidence


class TLSRejectionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'client.log'
        self.path.write_text('Mixed proxy listening\n')
        self.requests = []
        self.now = 0
        def elapsed():
            self.now += 1
            return self.now
        self.clock = patch('loopback_helpers.time.monotonic', side_effect=elapsed)
        self.clock.start(); self.addCleanup(self.clock.stop)
        self.sleep = patch('loopback_helpers.time.sleep')
        self.sleep.start(); self.addCleanup(self.sleep.stop)

    def observe(self, request, reason='unknown authority', timeout=3, expected_count=0):
        return require_tls_rejection(self, self.path, request, self.requests, reason, timeout=timeout, expected_count=expected_count)

    def test_real_reason_after_a_rejected_retry_is_required(self):
        calls = []
        def request():
            calls.append(1)
            self.path.write_text('tls: x509: certificate signed by unknown authority\n')
        log = self.observe(request)
        self.assertEqual(calls, [1])
        self.assertIn('x509', log)
        self.assertEqual(self.requests, [])

    def test_timeout_or_only_generic_tls_words_never_pass(self):
        for value in ('Mixed proxy listening\n', 'TLS certificate setup complete\n',
                      'x509: certificate valid for another.name, not wrong.example.test\n',
                      'x509: unrelated failure\nconfiguration label: unknown authority\n'):
            with self.subTest(value=value):
                self.now = 0; self.path.write_text(value)
                calls = []
                with self.assertRaisesRegex(AssertionError, 'No actual x509 rejection'):
                    self.observe(lambda: calls.append('rejected'))
                self.assertEqual(len(calls), 2)

    def test_delivery_before_reason_collection_cannot_become_the_baseline(self):
        self.requests.append('late-before-helper')
        self.path.write_text('x509: unknown authority')
        with self.assertRaisesRegex(AssertionError, 'request reached the target'):
            self.observe(lambda: self.fail('Must reject before sending another request'))
        self.assertEqual(self.requests, ['late-before-helper'])

    def test_target_delivery_on_retry_fails_even_if_true_tls_reason_appears(self):
        def request():
            self.requests.append('forbidden-delivery')
            self.path.write_text('x509: unknown authority')
        with self.assertRaisesRegex(AssertionError, 'retry reached the target'):
            self.observe(request)
        self.assertEqual(self.requests, ['forbidden-delivery'])

    def test_late_delivery_between_observations_is_not_erased(self):
        def request():
            self.path.write_text('x509: unknown authority')
        with patch('loopback_helpers.time.sleep', side_effect=lambda _: self.requests.append('late-delivery')):
            with self.assertRaisesRegex(AssertionError, 'request reached the target'):
                self.observe(request)
        self.assertEqual(self.requests, ['late-delivery'])

    def test_existing_positive_count_is_retained_as_the_fixed_baseline(self):
        self.requests.append('positive-control')
        def request():
            self.path.write_text('x509: certificate valid for vpn.example.test, not wrong.example.test')
        self.assertIn('wrong.example.test', self.observe(request, 'wrong.example.test', expected_count=1))
        self.assertEqual(self.requests, ['positive-control'])

    def test_success_or_unexpected_request_exception_is_not_swallowed(self):
        def request():
            raise AssertionError('unexpected HTTP success')
        with self.assertRaisesRegex(AssertionError, 'unexpected HTTP success'):
            self.observe(request)
        def unexpected():
            raise RuntimeError('fixture failure')
        with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
            self.observe(unexpected)

    def test_offset_inside_old_line_does_not_accept_its_appended_reason(self):
        old = 'old unfinished configuration: '
        self.path.write_text(old + 'x509: unknown authority\n')
        with self.assertRaisesRegex(AssertionError, 'No actual credential rejection'):
            require_rejection_evidence(self, self.path, lambda: None, self.requests,
                ('x509', 'unknown authority'), log_offset=len(old), timeout=3)

    def test_complete_line_after_partial_old_line_can_supply_the_reason(self):
        old = 'old unfinished configuration: '
        self.path.write_text(old + 'x509: unrelated old-line tail\n')
        calls = []
        def request():
            calls.append(1)
            with self.path.open('a') as stream:
                stream.write('x509: unknown authority\n')
        log = require_rejection_evidence(self, self.path, request, self.requests,
            ('x509', 'unknown authority'), log_offset=len(old), timeout=3)
        self.assertEqual(calls, [1])
        self.assertNotIn('old-line tail', log)
        self.assertIn('x509: unknown authority', log)

    def test_offsets_on_or_after_newline_accept_only_the_following_line(self):
        old = 'x509: obsolete cause\n'
        self.path.write_text(old + 'x509: unknown authority\n')
        for offset in (len(old) - 1, len(old)):
            with self.subTest(offset=offset):
                log = require_rejection_evidence(self, self.path,
                    lambda: self.fail('A complete fresh cause is already present'),
                    self.requests, ('x509', 'unknown authority'),
                    log_offset=offset, timeout=3)
                self.assertNotIn('obsolete cause', log)
                self.assertIn('x509: unknown authority', log)


if __name__ == '__main__':
    unittest.main()
