"""REALITY evidence cannot be satisfied by timeout or reference-only traffic."""
import unittest
from unittest.mock import Mock, patch

from loopback_helpers import require_rejection_evidence
from reality_helpers import ReferenceTraffic, h2_frame
import test_reality_preflight_loopback as preflight
from test_reality_preflight_loopback import safe_log
import test_tls_rejection_evidence as tls_tests


class RealityEvidenceTests(unittest.TestCase):
    setUp = tls_tests.TLSRejectionEvidenceTests.setUp

    def rejection(self, request, *, offset=0):
        return require_rejection_evidence(self, self.path, request, self.requests,
            ('reality authentication failed',), log_offset=offset, timeout=3, label='REALITY rejection')

    def test_actual_authentication_reason_passes(self):
        def request(): self.path.write_text('connect error: REALITY authentication failed')
        self.assertIn('reality authentication failed', self.rejection(request))

    def test_timeout_eof_and_reference_fallback_are_not_authentication_evidence(self):
        for value in ('timeout', 'EOF', 'forwarded SNI: reference.example.test', 'REALITY: processed invalid connection'):
            with self.subTest(value=value):
                self.now = 0; self.path.write_text(value)
                with self.assertRaisesRegex(AssertionError, 'No actual REALITY rejection'):
                    self.rejection(lambda: None)

    def test_old_session_reason_is_not_reused(self):
        old = 'REALITY authentication failed\n'; self.path.write_text(old)
        with self.assertRaisesRegex(AssertionError, 'No actual REALITY rejection'):
            self.rejection(lambda: None, offset=len(old))

    def test_reference_camouflage_is_separate_from_forbidden_application_delivery(self):
        reference = ReferenceTraffic()
        reference.camouflage.append(1)
        def request():
            self.path.write_text('REALITY authentication failed')
            self.requests.append('application delivery is forbidden')
        with self.assertRaisesRegex(AssertionError, 'retry reached the target'):
            self.rejection(request)
        self.assertEqual(reference.snapshot()['camouflage'], [1])

    def test_key_diagnostics_are_sanitized(self):
        log = 'ordinary failure\nREALITY hs.c.AuthKey[:16]: [1 2]\nhello.sessionId: [3]\nkey=fake-key'
        cleaned = safe_log(log, ('fake-key',))
        self.assertIn('ordinary failure', cleaned)
        for value in ('[1 2]', '[3]', 'fake-key'): self.assertNotIn(value, cleaned)

    def test_minimal_h2_response_frame_is_well_formed(self):
        self.assertEqual(h2_frame(1, 4, 1, b'\x88'), b'\x00\x00\x01\x01\x04\x00\x00\x00\x01\x88')

    def fixture(self):
        fixture = preflight.RealityPreflightLoopbackTests()
        fixture.private_key, fixture.public_key, fixture.wrong_public_key = ('fake-private', 'fake-public', 'fake-wrong')
        fixture.requests = []
        return fixture

    def test_startup_failure_is_sanitized_without_chained_exception(self):
        fixture = self.fixture()
        process = Mock()
        process.start.side_effect = AssertionError('fake-private\nREALITY AuthKey[:16]: [1 2]')
        with self.assertRaises(AssertionError) as caught: fixture.start_process(process, 12345)
        self.assertNotIn('fake-private', str(caught.exception)); self.assertNotIn('[1 2]', str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_missing_auth_evidence_failure_is_sanitized(self):
        fixture = self.fixture()
        with patch.object(preflight, 'require_rejection_evidence', side_effect=AssertionError('fake-private\nAuthKey: [1 2]')):
            with self.assertRaises(AssertionError) as caught:
                fixture.failure_evidence('singbox', Mock(), 'public_key', 12345, Mock(), 0)
        self.assertNotIn('fake-private', str(caught.exception)); self.assertNotIn('[1 2]', str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)


if __name__ == '__main__': unittest.main()
