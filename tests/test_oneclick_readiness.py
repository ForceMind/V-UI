"""Protocol readiness is bounded; authentication/TLS failures cannot be retried away."""
import http.client
import ssl
import unittest
from unittest.mock import Mock, patch

from test_oneclick_system import wait_for_managed_https


class ManagedHTTPSReadinessTests(unittest.TestCase):
    def test_transient_startup_connections_then_real_unauthorized_response(self):
        probe=Mock(side_effect=[ConnectionRefusedError('not bound'),ConnectionResetError('starting'),
                               ssl.SSLEOFError('starting'),http.client.RemoteDisconnected('starting'),(401,b'auth')])
        diagnostic=Mock(side_effect=AssertionError('not a failure'))
        with patch('test_oneclick_system.time.monotonic',return_value=0), \
             patch('test_oneclick_system.time.sleep') as sleep:
            wait_for_managed_https(probe,diagnostic)
        self.assertEqual(probe.call_count,5)
        self.assertEqual(sleep.call_count,4)
        self.assertTrue(all(call.args==(5,) for call in probe.call_args_list))
        diagnostic.assert_not_called()

    def test_tls_identity_errors_and_request_timeout_fail_immediately(self):
        for error in (ssl.SSLCertVerificationError('wrong hostname'),TimeoutError('deadline')):
            with self.subTest(error=error):
                probe=Mock(side_effect=error);diagnostic=Mock(return_value='bounded diagnostic')
                with patch('test_oneclick_system.time.monotonic',return_value=0), \
                     patch('test_oneclick_system.time.sleep') as sleep:
                    with self.assertRaisesRegex(AssertionError,'failed without retry') as caught:
                        wait_for_managed_https(probe,diagnostic)
                self.assertIs(caught.exception.__cause__,error)
                probe.assert_called_once();sleep.assert_not_called();diagnostic.assert_called_once()

    def test_wrong_http_status_is_never_accepted_or_retried(self):
        for status in (200,302,403,404,500):
            probe=Mock(return_value=(status,b'body'));diagnostic=Mock(return_value='bounded diagnostic')
            with self.subTest(status=status),patch('test_oneclick_system.time.monotonic',return_value=0):
                with self.assertRaisesRegex(AssertionError,'must enforce authentication'):
                    wait_for_managed_https(probe,diagnostic)
            probe.assert_called_once();diagnostic.assert_called_once()

    def test_deadline_exhaustion_keeps_original_refusal_and_diagnostics(self):
        probe=Mock(side_effect=ConnectionRefusedError('not bound'))
        diagnostic=Mock(return_value='service result=failed')
        with patch('test_oneclick_system.time.monotonic',side_effect=[0,0,20,20]), \
             patch('test_oneclick_system.time.sleep') as sleep:
            with self.assertRaisesRegex(AssertionError,'ConnectionRefusedError.*not bound') as caught:
                wait_for_managed_https(probe,diagnostic)
        self.assertIn('service result=failed',str(caught.exception))
        probe.assert_called_once();sleep.assert_not_called();diagnostic.assert_called_once()

    def test_probe_gets_remaining_budget_and_late_401_is_rejected(self):
        probe=Mock(return_value=(401,b'auth'));diagnostic=Mock(return_value='diagnostic')
        with patch('test_oneclick_system.time.monotonic',side_effect=[0,19.5,19.6]):
            wait_for_managed_https(probe,diagnostic)
        probe.assert_called_once_with(.5)
        diagnostic.assert_not_called()
        with patch('test_oneclick_system.time.monotonic',side_effect=[0,0,21]):
            with self.assertRaisesRegex(AssertionError,'exceeded readiness deadline'):
                wait_for_managed_https(Mock(return_value=(401,b'auth')),diagnostic)


if __name__=='__main__':unittest.main()
