"""Deterministic recorder lifecycle witnesses, not real-core qualifications."""
from contextlib import ExitStack
from pathlib import Path
import socket
import socketserver
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import test_xhttp_contract as contract
import test_xhttp_preflight_loopback as preflight
from loopback_helpers import certificate_files
from xhttp_helpers import ProtocolObservations, SNI, read_protocol_bytes, start_protocol_recorder


class RecorderConsumerEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.enterContext(patch.object(preflight.time, 'monotonic', side_effect=lambda: self.now))
        self.enterContext(patch.object(preflight.time, 'sleep', side_effect=self.sleep))
        self.enterContext(patch('builtins.print'))
        self.fixture = preflight.XHTTPPreflightLoopbackTests()
        self.fixture.target_port = 23456
        self.fixture.requests = []
        self.fixture.cert, self.fixture.key = 'fake-cert', 'fake-key'
        self.fixture.prepare_client = Mock(return_value=([], {}))
        self.fixture.unique_path = Mock(return_value='fake-log')
        self.fixture.assert_failure = Mock(side_effect=self.request)
        self.enterContext(patch.object(preflight, 'unused_port', return_value=1234))
        self.process = Mock()
        self.process.process.poll.return_value = None
        self.enterContext(patch.object(preflight, 'CoreProcess', side_effect=self.core))
        self.observers = []
        self.finished = []
        self.calls = []
        self.on_request = lambda: self.add_valid()
        self.on_finish = lambda: None
        self.on_sleep = lambda: None
        self.enterContext(patch.object(preflight, 'start_protocol_recorder', side_effect=self.recorder))

    def core(self, stack, *args):
        stack.callback(self.process.stop)
        return self.process

    def sleep(self, delay):
        self.now += delay
        self.on_sleep()

    def recorder(self, stack, cert, key):
        obs = ProtocolObservations()
        self.observers.append(obs)
        done = False
        def finish():
            nonlocal done
            if not done:
                done = True
                self.finished.append(obs)
                self.on_finish()
        # The old consumer invokes only the stack callback, after its last
        # snapshot check. The revised consumer must explicitly close first.
        stack.callback(finish)
        return 4321, obs

    def record(self):
        witness = contract.HTTPUpgradeWireEvidenceTests()
        payload = witness.native() if len(self.observers) == 1 else witness.vless()
        return {'sni': SNI, 'alpn': 'http/1.1', 'tls': 'TLSv1.3', 'payload': payload}

    def add_valid(self):
        self.observers[-1].append(self.record())

    def request(self, port, baseline):
        self.calls.append((len(self.observers), baseline))
        self.on_request()

    def observe(self):
        return self.fixture.observe_httpupgrade_protocols()

    def test_fresh_request_after_first_attempt_yields_no_connection(self):
        def request():
            if len(self.calls) > 1:
                self.add_valid()
        self.on_request = request
        self.assertEqual(self.observe(), contract.HTTPUpgradeWireEvidenceTests().vless())
        self.assertEqual(self.calls, [(1, 0), (1, 0), (2, 0)])
        self.assertEqual(len(self.finished), 2)

    def test_empty_observation_has_finite_attempt_budget_and_client_label(self):
        self.on_request = lambda: None
        with self.assertRaisesRegex(AssertionError, 'No actual protocol bytes.*mihomo.*attempts=3'):
            self.observe()
        self.assertEqual(len(self.calls), 3)
        self.assertGreaterEqual(self.process.stop.call_count, 1)

    def test_no_request_after_first_complete_valid_record(self):
        self.observe()
        self.assertEqual(self.calls, [(1, 0), (2, 0)])

    def test_error_then_valid_is_not_erased_or_retried(self):
        def request():
            self.observers[-1].append({'error': 'EOF before complete protocol bytes'})
            self.add_valid()
        self.on_request = request
        with self.assertRaisesRegex(AssertionError, 'incomplete or failed TLS'):
            self.observe()
        self.assertEqual(len(self.calls), 1)
        self.assertGreaterEqual(self.process.stop.call_count, 1)

    def test_late_error_while_finishing_cannot_escape_last_snapshot(self):
        self.on_finish = lambda: self.observers[-1].append({'error': 'late TLS failure'})
        with self.assertRaisesRegex(AssertionError, 'incomplete or failed TLS'):
            self.observe()

    def test_late_bad_bytes_while_finishing_are_validated(self):
        def finish():
            record = self.record(); record['payload'] = b'timeout'
            self.observers[-1].append(record)
        self.on_finish = finish
        with self.assertRaisesRegex(ValueError, 'Unknown protocol bytes'):
            self.observe()

    def test_late_application_delivery_while_finishing_fails(self):
        self.on_finish = lambda: self.fixture.requests.append('forbidden')
        with self.assertRaisesRegex(AssertionError, 'Recorder traffic reached application'):
            self.observe()

    def test_dead_client_cannot_supply_success_from_prior_bytes(self):
        self.process.process.poll.return_value = 17
        with self.assertRaisesRegex(AssertionError, 'Recorder client exited.*mihomo'):
            self.observe()

    def test_partial_tls_record_is_failure_not_permission_to_retry(self):
        def request():
            record = self.record(); record['alpn'] = None
            self.observers[-1].append(record)
        self.on_request = request
        with self.assertRaises(AssertionError):
            self.observe()
        self.assertEqual(len(self.calls), 1)
        self.assertGreaterEqual(self.process.stop.call_count, 1)

    def test_original_error_survives_stop_failure_and_delivery_is_checked(self):
        def request():
            self.observers[-1].append({'error': 'EOF'})
        def stop():
            self.fixture.requests.append('forbidden')
            raise RuntimeError('synthetic stop failure')
        self.on_request = request
        self.process.stop.side_effect = stop
        with self.assertRaisesRegex(AssertionError, 'incomplete or failed TLS') as raised:
            self.observe()
        self.assertIsInstance(raised.exception.__cause__, RuntimeError)
        self.assertIn('stop failure', str(raised.exception.__cause__))
        self.assertIsInstance(raised.exception.__cause__.__cause__, AssertionError)
        self.assertIn('Recorder traffic reached application', str(raised.exception.__cause__.__cause__))
        self.assertEqual(len(self.finished), 1)

    def test_finish_failure_cannot_skip_target_check(self):
        def finish():
            self.fixture.requests.append('forbidden')
            raise RuntimeError('synthetic drain failure')
        self.on_finish = finish
        with self.assertRaisesRegex(RuntimeError, 'drain failure') as raised:
            self.observe()
        self.assertIsInstance(raised.exception.__cause__, AssertionError)
        self.assertIn('Recorder traffic reached application', str(raised.exception.__cause__))

    def test_attempts_are_spaced_and_empty_window_remains_open(self):
        starts = []
        self.on_request = lambda: starts.append(self.now)
        with self.assertRaisesRegex(AssertionError, 'No actual protocol bytes'):
            self.observe()
        self.assertEqual(len(starts), 3)
        self.assertGreaterEqual(starts[1]-starts[0], 4)
        self.assertGreaterEqual(starts[2]-starts[1], 4)
        self.assertGreaterEqual(self.now, 12)

    def test_inflight_handler_suppresses_additional_attempts(self):
        self.on_request = lambda: self.observers[-1].begin()
        with self.assertRaisesRegex(AssertionError, 'No actual protocol bytes.*attempts=1'):
            self.observe()
        self.assertEqual(len(self.calls), 1)

    def test_between_client_delivery_cannot_be_rebased(self):
        def print_result(*args, **kwargs):
            if len(self.observers) == 1:
                self.fixture.requests.append('forbidden between clients')
        with patch('builtins.print', side_effect=print_result):
            with self.assertRaisesRegex(AssertionError, 'Recorder traffic reached application'):
                self.observe()
        self.assertEqual(self.calls, [(1, 0)])

    def test_late_record_after_third_empty_attempt_is_still_observed(self):
        def request():
            if len(self.observers) == 2:
                self.add_valid()
        def later():
            if self.now >= 10 and not self.observers[-1].snapshot():
                self.add_valid()
        self.on_request, self.on_sleep = request, later
        self.assertEqual(self.observe(), contract.HTTPUpgradeWireEvidenceTests().vless())
        self.assertEqual(self.calls, [(1, 0), (1, 0), (1, 0), (2, 0)])
        self.assertGreaterEqual(self.now, 10)

    def test_delivery_before_retry_is_not_hidden_by_fresh_valid_bytes(self):
        self.on_request = lambda: None
        self.on_sleep = lambda: self.fixture.requests.append('forbidden')
        with self.assertRaisesRegex(AssertionError, 'Recorder traffic reached application'):
            self.observe()
        self.assertEqual(self.calls, [(1, 0)])

    def test_delivery_during_attempt_cannot_be_rebased(self):
        def request():
            self.fixture.requests.append('forbidden')
            self.add_valid()
        self.on_request = request
        with self.assertRaisesRegex(AssertionError, 'Recorder traffic reached application'):
            self.observe()
        self.assertEqual(self.calls, [(1, 0)])

    def test_nested_cleanup_cause_survives_later_delivery_failure(self):
        inner = RuntimeError('nested cleanup cause')
        outer = RuntimeError('outer cleanup failure')
        outer.__cause__ = inner
        def finish():
            self.fixture.requests.append('forbidden')
            raise outer
        self.on_finish = finish
        with self.assertRaisesRegex(RuntimeError, 'outer cleanup') as raised:
            self.observe()
        self.assertIs(raised.exception.__cause__, inner)
        self.assertIsInstance(inner.__cause__, AssertionError)
        self.assertIn('Recorder traffic reached application', str(inner.__cause__))

    def test_original_failure_keeps_existing_cause_before_cleanup_errors(self):
        source = ValueError('original source cause')
        original = RuntimeError('original request failure')
        original.__cause__ = source
        stop = RuntimeError('synthetic stop failure')
        self.on_request = Mock(side_effect=original)
        self.process.stop.side_effect = stop
        with self.assertRaisesRegex(RuntimeError, 'original request failure') as raised:
            self.observe()
        self.assertIs(raised.exception, original)
        self.assertIs(original.__cause__, source)
        self.assertIs(source.__cause__, stop)


class RecorderLifecycleEvidenceTests(unittest.TestCase):
    def test_connection_overflow_stays_failed_after_valid_records(self):
        obs = ProtocolObservations()
        for _ in range(16):
            self.assertTrue(obs.begin())
            obs.append({'payload': b'complete'})
            obs.end()
        self.assertFalse(obs.begin())
        self.assertEqual(obs.state(), {'accepted': 16, 'active': 0})
        self.assertIn('connection limit', obs.snapshot()[-1]['error'])
        with self.assertRaisesRegex(AssertionError, 'connection limit'):
            obs.drain(timeout=0)

    def test_record_overflow_cannot_lose_its_failure(self):
        obs = ProtocolObservations()
        for _ in range(18):
            obs.append({'payload': b'complete'})
        self.assertEqual(len(obs.snapshot()), 17)
        self.assertIn('record limit', obs.snapshot()[-1]['error'])

    def test_drain_timeout_sticks_after_handler_later_finishes(self):
        obs = ProtocolObservations(); obs.begin()
        with self.assertRaisesRegex(AssertionError, 'drain timed out'):
            obs.drain(timeout=0)
        obs.append({'payload': b'complete'}); obs.end()
        with self.assertRaisesRegex(AssertionError, 'drain timed out'):
            obs.drain(timeout=0)
        self.assertIn('drain timed out', obs.snapshot()[-1]['error'])

    def test_missing_handler_record_cannot_pass_completed_drain(self):
        obs = ProtocolObservations(); obs.begin(); obs.end()
        with self.assertRaisesRegex(AssertionError, 'completion count'):
            obs.drain(timeout=0)

    def test_fragmented_reads_share_one_absolute_deadline(self):
        conn = Mock(); conn.recv.side_effect = [b'GET /', b'probe']
        with patch('time.monotonic', side_effect=[1, 3, 4]):
            with self.assertRaisesRegex(ValueError, 'Timed out before complete'):
                read_protocol_bytes(conn, deadline=4)
        self.assertEqual(conn.recv.call_count, 2)
        self.assertEqual([call.args for call in conn.settimeout.call_args_list], [(3,), (1,)])

    def test_expired_deadline_never_starts_another_read(self):
        conn = Mock()
        with patch('time.monotonic', return_value=5):
            with self.assertRaisesRegex(ValueError, 'Timed out before complete'):
                read_protocol_bytes(conn, deadline=4)
        conn.recv.assert_not_called()


class RealTLSRecorderLifecycleTests(unittest.TestCase):
    """Actual local sockets and temporary verified TLS, not core binaries."""
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-recorder-test-')))
        self.ca, self.cert, self.key = certificate_files(root)
        self.recorder_stack = ExitStack(); self.addCleanup(self.recorder_stack.close)
        self.port, self.obs = start_protocol_recorder(self.recorder_stack, self.cert, self.key)

    def connect(self):
        context = ssl.create_default_context(cafile=str(self.ca))
        context.set_alpn_protocols(['http/1.1'])
        raw = socket.create_connection(('127.0.0.1', self.port), timeout=2)
        return context.wrap_socket(raw, server_hostname=SNI)

    def wait(self, predicate):
        deadline = time.monotonic()+2
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(predicate())

    def test_completed_record_is_held_then_sealed_and_drained(self):
        with self.connect() as conn:
            conn.sendall(contract.HTTPUpgradeWireEvidenceTests().native())
            self.wait(lambda: bool(self.obs.snapshot()))
            self.assertEqual(self.obs.state(), {'accepted': 1, 'active': 1})
            self.recorder_stack.close()
            self.assertEqual(self.obs.state(), {'accepted': 1, 'active': 0})
            self.assertEqual(len(self.obs.snapshot()), 1)
            with self.assertRaises(OSError):
                socket.create_connection(('127.0.0.1', self.port), timeout=.1)

    def test_accepted_partial_tls_is_recorded_as_error_before_close_returns(self):
        with self.connect() as conn:
            conn.sendall(b'GET /')
            self.wait(lambda: self.obs.state()['active'] == 1)
        self.recorder_stack.close()
        self.assertEqual(self.obs.state()['active'], 0)
        self.assertEqual(len(self.obs.snapshot()), 1)
        self.assertTrue(self.obs.snapshot()[0]['error'])
        self.assertNotIn('payload', self.obs.snapshot()[0])

    def test_unexpected_handler_exception_is_sticky_and_accounted(self):
        with patch('xhttp_helpers.read_protocol_bytes', side_effect=RuntimeError('synthetic')):
            with self.connect():
                self.wait(lambda: bool(self.obs.snapshot()))
        self.recorder_stack.close()
        self.assertEqual(self.obs.state(), {'accepted': 1, 'active': 0})
        self.assertEqual(self.obs.snapshot(), [{'error': 'Unexpected protocol handler failure'}])

    def test_thread_start_failure_is_sticky_and_does_not_leak_inflight(self):
        original = threading.Thread.start
        def start(thread):
            if getattr(thread._target, '__name__', '') == 'process_request_thread':
                raise RuntimeError('synthetic dispatch failure')
            return original(thread)
        with patch.object(threading.Thread, 'start', new=start):
            with socket.create_connection(('127.0.0.1', self.port), timeout=2):
                self.wait(lambda: bool(self.obs.snapshot()))
        self.recorder_stack.close()
        self.assertEqual(self.obs.state(), {'accepted': 1, 'active': 0})
        self.assertEqual(self.obs.snapshot(), [{'error': 'Protocol handler dispatch failed'}])

    def test_handler_teardown_error_cannot_pass_after_valid_bytes(self):
        with self.connect() as conn:
            conn.sendall(contract.HTTPUpgradeWireEvidenceTests().native())
            self.wait(lambda: bool(self.obs.snapshot()))
            with patch.object(socketserver.TCPServer, 'shutdown_request',
                              side_effect=RuntimeError('synthetic teardown failure')), \
                    patch.object(threading, 'excepthook'):
                with self.assertRaises(AssertionError):
                    self.recorder_stack.close()
        self.assertEqual(self.obs.state()['active'], 0)
        self.assertTrue(any('error' in item for item in self.obs.snapshot()))

    def test_close_error_still_attempts_later_cleanup_and_drain(self):
        with self.connect() as conn:
            conn.sendall(contract.HTTPUpgradeWireEvidenceTests().native())
            self.wait(lambda: bool(self.obs.snapshot()))
            original = socketserver.BaseServer.shutdown
            def shutdown(server):
                original(server)
                raise RuntimeError('synthetic shutdown failure')
            with patch.object(socketserver.BaseServer, 'shutdown', new=shutdown), \
                    patch.object(self.obs, 'drain', wraps=self.obs.drain) as drain:
                with self.assertRaisesRegex(RuntimeError, 'shutdown failure'):
                    self.recorder_stack.close()
                drain.assert_called_once()
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', self.port), timeout=.1)

    def test_valid_record_does_not_skip_other_accepted_tls_timeout(self):
        with self.connect() as conn:
            conn.sendall(contract.HTTPUpgradeWireEvidenceTests().native())
            self.wait(lambda: bool(self.obs.snapshot()))
            with socket.create_connection(('127.0.0.1', self.port), timeout=2):
                self.wait(lambda: self.obs.state()['accepted'] == 2)
                self.recorder_stack.close()
        records = self.obs.snapshot()
        self.assertEqual(self.obs.state(), {'accepted': 2, 'active': 0})
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]['payload'], contract.HTTPUpgradeWireEvidenceTests().native())
        self.assertIn('TimeoutError', records[1]['error'])


if __name__ == '__main__':
    unittest.main()
