"""Exercise the real VMess negative-test callers, without claiming real cores."""
from contextlib import nullcontext
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_vmess_loopback as vmess


class VMessRejectionEvidenceTests(unittest.TestCase):
    CASES = (
        ('test_untrusted_certificate_is_rejected', 'mihomo', 'unknown authority'),
        ('test_wrong_server_name_is_rejected', 'mihomo', 'wrong.example.test'),
        ('test_untrusted_certificate_is_rejected_by_singbox', 'singbox', 'unknown authority'),
        ('test_wrong_server_name_is_rejected_by_singbox', 'singbox', 'wrong.example.test'),
    )

    def invoke(self, case, request, *, initial='Mixed proxy listening\n',
               deliveries=(), sleeping=None, stopping=None):
        name, client, reason = case
        with tempfile.TemporaryDirectory(prefix='vui-vmess-evidence-') as directory:
            subject = vmess.VMessLoopbackTests(name)
            subject.root = Path(directory)
            subject.target_port = 12345
            subject.requests = list(deliveries)
            path = subject.root / ('mihomo-123.log' if client == 'mihomo' else 'singbox-client-123.log')
            path.write_text(initial)
            state = SimpleNamespace(subject=subject, path=path, reason=reason,
                                    calls=[], stops=0, ticks=0)
            self.last_state = state

            def clock():
                state.ticks += 1
                return state.ticks

            def probe(port, host, target):
                self.assertEqual((port, target), (123, subject.target_port))
                self.assertEqual(host, 'forced.example.test' if client == 'mihomo' else '127.0.0.1')
                state.calls.append((port, host, target))
                return request(state)

            def stop():
                state.stops += 1
                if stopping:
                    stopping(state)

            process = SimpleNamespace(log_path=path, stop=stop)
            subject.start_mihomo = lambda **kwargs: (process, 123)
            subject.start_singbox_client = lambda **kwargs: (process, 123)
            row = SimpleNamespace(stream_settings={'tls': {}, '_vui': {}})
            session = SimpleNamespace(get=lambda *args: row, commit=lambda: None)
            with patch.object(vmess.database, 'SessionLocal', return_value=nullcontext(session)), \
                 patch.object(vmess, 'http_through', side_effect=probe), \
                 patch.object(vmess.time, 'monotonic', side_effect=clock), \
                 patch.object(vmess.time, 'sleep', side_effect=lambda _: sleeping(state) if sleeping else None), \
                 patch('builtins.print') as printed:
                getattr(subject, name)()
                state.printed = [args[0] for args, _ in printed.call_args_list]
            return state

    @staticmethod
    def append_reason(state):
        with state.path.open('a') as stream:
            stream.write('tls: x509: ' + state.reason + '\n')
        return 502, b''

    def test_each_actual_tls_caller_retries_transient_before_specific_reason(self):
        for case in self.CASES:
            with self.subTest(case=case[0]):
                def request(state):
                    if len(state.calls) < 3:
                        raise TimeoutError('synthetic listener not ready')
                    return self.append_reason(state)
                state = self.invoke(case, request)
                self.assertEqual(len(state.calls), 3)
                self.assertEqual(state.subject.requests, [])
                self.assertEqual(state.stops, 1)

    def test_each_caller_rejects_timeout_generic_wrong_reason_and_split_lines(self):
        values = ('Mixed proxy listening\n', 'TLS certificate setup complete\n',
                  'x509: an unrelated failure\n',
                  'x509: unrelated failure\nconfiguration label: {reason}\n')
        for case in self.CASES:
            for value in values:
                with self.subTest(case=case[0], value=value):
                    def request(state):
                        with state.path.open('a') as stream:
                            stream.write(value.format(reason=state.reason))
                        raise TimeoutError('synthetic transport timeout')
                    with self.assertRaises(AssertionError):
                        self.invoke(case, request)
                    self.assertEqual(self.last_state.stops, 1)
                    self.assertLessEqual(len(self.last_state.calls), 12)
                    self.assertLessEqual(self.last_state.ticks, 15)

    def test_each_caller_ignores_pre_request_tls_evidence(self):
        for case in self.CASES:
            with self.subTest(case=case[0]):
                with self.assertRaises(AssertionError):
                    self.invoke(case, lambda _: (502, b''),
                                initial='x509: ' + case[2] + '\n')

    def test_each_caller_requires_both_reasons_in_the_fresh_log_suffix(self):
        for case in self.CASES:
            with self.subTest(case=case[0]):
                def request(state):
                    with state.path.open('a') as stream:
                        stream.write(state.reason + '\n')
                    return 502, b''
                with self.assertRaises(AssertionError):
                    self.invoke(case, request, initial='old unfinished x509: ')

    def test_old_partial_line_with_both_fresh_reason_fragments_is_not_a_new_line(self):
        for case in self.CASES:
            with self.subTest(case=case[0]):
                def request(state):
                    if len(state.calls) == 1:
                        return self.append_reason(state)
                    return 502, b''
                with self.assertRaises(AssertionError):
                    self.invoke(case, request, initial='old incomplete configuration: ')

    def test_a_complete_new_line_after_old_partial_line_can_qualify(self):
        for case in self.CASES:
            with self.subTest(case=case[0]):
                state = self.invoke(case, self.append_reason,
                                    initial='old incomplete configuration: ')
                self.assertEqual(len(state.calls), 2)
                self.assertEqual(state.stops, 1)

    def test_report_selects_the_specific_reason_not_first_unrelated_x509_line(self):
        for case in self.CASES:
            with self.subTest(case=case[0]):
                def request(state):
                    if len(state.calls) == 1:
                        with state.path.open('a') as stream:
                            stream.write('x509: unrelated diagnostic\n')
                        return 502, b''
                    return self.append_reason(state)
                state = self.invoke(case, request)
                self.assertEqual(len(state.calls), 2)
                self.assertEqual(len(state.printed), 1)
                self.assertIn('x509: ' + case[2], state.printed[0])
                self.assertNotIn('unrelated diagnostic', state.printed[0])

    def test_expired_retry_start_window_does_not_launch_another_request(self):
        for case in self.CASES:
            with self.subTest(case=case[0]):
                with self.assertRaisesRegex(AssertionError, 'No actual x509 rejection'):
                    self.invoke(case, lambda _: (502, b''))
                # Fake monotonic advances by one per read: deadline=13,
                # starts at 2..12, then rejects at 13 without another probe.
                self.assertEqual(len(self.last_state.calls), 11)
                self.assertEqual(self.last_state.ticks, 13)
                self.assertEqual(self.last_state.stops, 1)

    def test_inflight_request_can_produce_true_reason_after_start_window(self):
        for case in self.CASES:
            with self.subTest(case=case[0]):
                def request(state):
                    state.ticks = 100
                    return self.append_reason(state)
                state = self.invoke(case, request)
                self.assertEqual(len(state.calls), 1)
                self.assertEqual(state.stops, 1)

    def test_each_caller_keeps_zero_delivery_baseline_before_first_request(self):
        for case in self.CASES:
            with self.subTest(case=case[0]):
                with self.assertRaisesRegex(AssertionError, 'DIRECT|reached the target'):
                    self.invoke(case, self.append_reason, deliveries=['late-before-helper'])
                self.assertEqual(self.last_state.calls, [])
                self.assertEqual(self.last_state.stops, 1)

    def test_delivery_on_retry_fails_even_with_specific_reason(self):
        for case in self.CASES:
            with self.subTest(case=case[0]):
                def request(state):
                    if len(state.calls) == 1:
                        raise OSError('synthetic early reset')
                    state.subject.requests.append('forbidden-delivery')
                    return self.append_reason(state)
                with self.assertRaisesRegex(AssertionError, 'DIRECT|reached the target'):
                    self.invoke(case, request)
                self.assertEqual(len(self.last_state.calls), 2)
                self.assertEqual(self.last_state.subject.requests, ['forbidden-delivery'])
                self.assertEqual(self.last_state.stops, 1)

    def test_late_delivery_during_observation_is_not_rebased(self):
        for case in self.CASES:
            with self.subTest(case=case[0]):
                def sleep(state):
                    state.subject.requests.append('late-delivery')
                    self.append_reason(state)
                with self.assertRaisesRegex(AssertionError, 'DIRECT|reached the target'):
                    self.invoke(case, lambda _: (502, b''), sleeping=sleep)
                self.assertEqual(self.last_state.subject.requests, ['late-delivery'])
                self.assertEqual(self.last_state.stops, 1)

    def test_delivery_during_client_stop_fails(self):
        for case in self.CASES:
            with self.subTest(case=case[0]):
                with self.assertRaisesRegex(AssertionError, 'DIRECT|reached the target'):
                    self.invoke(case, self.append_reason,
                                stopping=lambda state: state.subject.requests.append('stop-delivery'))
                self.assertEqual(self.last_state.subject.requests, ['stop-delivery'])
                self.assertEqual(self.last_state.stops, 1)

    def test_http_success_and_unexpected_exceptions_are_not_swallowed(self):
        for case in self.CASES:
            with self.subTest(case=case[0]):
                def success(state):
                    self.append_reason(state)
                    return 200, b'unexpected target body'
                with self.assertRaises(AssertionError):
                    self.invoke(case, success)
                self.assertEqual(self.last_state.stops, 1)
                def unexpected(_):
                    raise RuntimeError('synthetic fixture failure')
                with self.assertRaisesRegex(RuntimeError, 'synthetic fixture failure'):
                    self.invoke(case, unexpected)
                self.assertEqual(self.last_state.stops, 1)

    def test_original_failure_remains_primary_when_final_delivery_check_also_fails(self):
        for case in self.CASES:
            with self.subTest(case=case[0]):
                original = RuntimeError('original synthetic request failure')
                def request(_):
                    raise original
                with self.assertRaises(RuntimeError) as caught:
                    self.invoke(case, request,
                        stopping=lambda state: state.subject.requests.append('stop-delivery'))
                self.assertIs(caught.exception, original)
                self.assertEqual(str(caught.exception), 'original synthetic request failure')
                self.assertIsInstance(caught.exception.__cause__, AssertionError)
                self.assertIn('checked after client stop', str(caught.exception.__cause__))
                self.assertEqual(self.last_state.stops, 1)

    def test_missing_evidence_remains_primary_when_stop_also_fails(self):
        for case in self.CASES:
            with self.subTest(case=case[0]):
                stop_failure = RuntimeError('synthetic stop failure')
                def stop(_):
                    raise stop_failure
                with self.assertRaisesRegex(AssertionError, 'No actual x509 rejection') as caught:
                    self.invoke(case, lambda _: (502, b''), stopping=stop)
                self.assertIs(caught.exception.__cause__, stop_failure)
                self.assertEqual(str(caught.exception.__cause__), 'synthetic stop failure')
                self.assertEqual(self.last_state.stops, 1)

    def test_final_count_is_checked_even_if_stop_raises_and_both_errors_survive(self):
        for case in self.CASES:
            with self.subTest(case=case[0]):
                stop_failure = RuntimeError('synthetic stop and delivery')
                def stop(state):
                    state.subject.requests.append('stop-delivery')
                    raise stop_failure
                with self.assertRaises(RuntimeError) as caught:
                    self.invoke(case, self.append_reason, stopping=stop)
                self.assertIs(caught.exception, stop_failure)
                self.assertEqual(str(caught.exception), 'synthetic stop and delivery')
                self.assertIsInstance(caught.exception.__cause__, AssertionError)
                self.assertIn('checked after client stop', str(caught.exception.__cause__))
                self.assertEqual(self.last_state.subject.requests, ['stop-delivery'])
                self.assertEqual(self.last_state.stops, 1)


if __name__ == '__main__':
    unittest.main()
