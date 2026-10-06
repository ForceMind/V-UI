"""TUIC credential evidence reuses the tested TLS collector, with fresh logs."""
import unittest
import test_tls_rejection_evidence as tls_tests
from loopback_helpers import require_rejection_evidence


class TUICCredentialEvidenceTests(unittest.TestCase):
    setUp = tls_tests.TLSRejectionEvidenceTests.setUp

    def auth(self, request, *, offset=0):
        return require_rejection_evidence(self, self.path, request, self.requests,
            ('authentication:', 'token mismatch'), timeout=3, log_offset=offset)

    def test_specific_auth_reason_after_retry_passes(self):
        def request(): self.path.write_text('connection failed: authentication: token mismatch')
        self.assertIn('token mismatch', self.auth(request))

    def test_timeout_wrong_reason_or_split_fragments_never_pass(self):
        for value in ('timeout', 'authentication: unknown user', 'authentication:\nlabel: token mismatch'):
            with self.subTest(value=value):
                self.now = 0; self.path.write_text(value)
                with self.assertRaisesRegex(AssertionError, 'No actual credential rejection'):
                    self.auth(lambda: None)

    def test_previous_session_reason_cannot_satisfy_new_client(self):
        old = 'authentication: token mismatch\n'; self.path.write_text(old)
        with self.assertRaisesRegex(AssertionError, 'No actual credential rejection'):
            self.auth(lambda: None, offset=len(old))

    def test_delivered_target_is_not_hidden_by_real_auth_reason(self):
        def request():
            self.requests.append('forbidden'); self.path.write_text('authentication: token mismatch')
        with self.assertRaisesRegex(AssertionError, 'retry reached the target'): self.auth(request)
