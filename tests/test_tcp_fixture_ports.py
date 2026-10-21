"""D1 fixture allocation controls, not new protocol or runtime qualification."""
import errno
from pathlib import Path
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.services import proxy_apply
from app.services.proxy_policy import LOCAL_TAG
import proxy_loopback_support
import tcp_upstream_export_helpers as helper


def config(port):
    return {'inbounds': [{'tag': LOCAL_TAG, 'type': 'mixed',
                          'listen': '127.0.0.1', 'listen_port': port}]}


class FixturePortTests(unittest.TestCase):
    def test_candidates_are_distinct_unprivileged_and_outside_inclusive_range(self):
        for raw, expected in (('32768\t60999\n', {1024, 32767, 61000, 65535}),
                              ('0 65534', {65535}), ('1025 65535', {1024})):
            with self.subTest(raw=raw), patch.object(Path, 'read_text', return_value=raw), \
                    patch.object(helper.random, 'SystemRandom') as rng, \
                    patch.object(proxy_apply, 'preflight_local_listener') as guard:
                def sample(population, count):
                    self.assertEqual(len(population), len(set(population)))
                    low, high = map(int, raw.split())
                    self.assertTrue(all(1024 <= port <= 65535 and not low <= port <= high
                                        for port in population))
                    self.assertTrue(expected.issubset(population))
                    self.assertEqual(count, min(128, len(population)))
                    return population[:count]
                rng.return_value.sample.side_effect = sample
                result = helper.local_proxy_port()
                self.assertIn(result, expected)
                guard.assert_called_once()
                self.assertEqual(guard.call_args.args[0], config(result))
                self.assertIsNone(guard.call_args.args[1].process)

    def test_invalid_missing_and_full_ranges_never_probe_or_fallback(self):
        for raw in ('', '32768', '1 2 3', '65000 1000', '-1 65000', '0 65536',
                    '0x400 60000', '１ ６５５３５', '1\n65535', '1024 65535', '0 65535'):
            with self.subTest(raw=raw), patch.object(Path, 'read_text', return_value=raw), \
                    patch.object(proxy_apply, 'preflight_local_listener') as guard:
                with self.assertRaises(AssertionError): helper.local_proxy_port()
                guard.assert_not_called()
        for error in (FileNotFoundError(), PermissionError(), UnicodeError()):
            with self.subTest(error=type(error)), patch.object(Path, 'read_text', side_effect=error), \
                    patch.object(proxy_apply, 'preflight_local_listener') as guard:
                with self.assertRaisesRegex(AssertionError, 'Cannot read'):
                    helper.local_proxy_port()
                guard.assert_not_called()

    def test_occupied_retries_are_bounded_and_other_failures_propagate(self):
        candidates = list(range(1024, 1152))
        with patch.object(Path, 'read_text', return_value='32768 60999'), \
                patch.object(helper.random, 'SystemRandom') as rng:
            rng.return_value.sample.return_value = candidates
            for successes in (126, 127):
                with self.subTest(successes=successes), \
                        patch.object(proxy_apply, 'preflight_local_listener', side_effect=
                            [proxy_apply.ProxyApplyError('LOCAL_PORT_OCCUPIED')] * successes + [None]) as guard:
                    self.assertEqual(helper.local_proxy_port(), candidates[successes])
                    self.assertEqual(guard.call_count, successes + 1)
            with patch.object(proxy_apply, 'preflight_local_listener',
                              side_effect=proxy_apply.ProxyApplyError('LOCAL_PORT_OCCUPIED')) as guard:
                with self.assertRaisesRegex(AssertionError, '128 candidate limit'):
                    helper.local_proxy_port()
                self.assertEqual(guard.call_count, 128)
                self.assertEqual(len({call.args[0]['inbounds'][0]['listen_port']
                                      for call in guard.call_args_list}), 128)
            for error in (proxy_apply.ProxyApplyError('LOCAL_PORT_UNAVAILABLE'), PermissionError(), RuntimeError()):
                with self.subTest(error=type(error)), \
                        patch.object(proxy_apply, 'preflight_local_listener', side_effect=error) as guard:
                    with self.assertRaises(type(error)): helper.local_proxy_port()
                    guard.assert_called_once()

    def test_small_candidate_set_is_exhausted_without_repeating(self):
        with patch.object(Path, 'read_text', return_value='1025 65535'), \
                patch.object(proxy_apply, 'preflight_local_listener',
                             side_effect=proxy_apply.ProxyApplyError('LOCAL_PORT_OCCUPIED')) as guard:
            with self.assertRaises(AssertionError): helper.local_proxy_port()
            guard.assert_called_once()
            self.assertEqual(guard.call_args.args[0], config(1024))

    def test_released_ephemeral_port_can_be_stolen_but_new_choice_is_outside_pool(self):
        # A deterministic socket model proves the race, not the owner in an old
        # CI run. Keep the production preflight function completely unchanged.
        bound, sockets, probes = set(), [], []
        class FakeSocket:
            def __init__(self, family=socket.AF_INET, kind=socket.SOCK_STREAM):
                self.family, self.kind, self.binding, self.closed = family, kind, None, False
                sockets.append(self)
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
            def setsockopt(self, *args): pass
            def bind(self, address):
                port = address[1] or 40000
                identity = self.family, self.kind, port
                probes.append(identity)
                if identity in bound: raise OSError(errno.EADDRINUSE, 'synthetic occupied port')
                bound.add(identity)
                self.binding = identity
            def getsockname(self): return '127.0.0.1', self.binding[2]
            def close(self):
                if self.binding is not None: bound.remove(self.binding)
                self.binding, self.closed = None, True
        existing = SimpleNamespace(laddr=SimpleNamespace(port=40000), type=socket.SOCK_STREAM,
                                   family=socket.AF_INET, status='ESTABLISHED', pid=100)
        with patch.object(socket, 'socket', FakeSocket), \
                patch.object(proxy_apply.psutil, 'net_connections', return_value=[existing]), \
                patch.object(Path, 'read_text', return_value='32768 60999'), \
                patch.object(helper.random, 'SystemRandom') as rng:
            old = proxy_loopback_support.listen_port()
            self.assertEqual(old, 40000)
            self.assertFalse(bound)
            client = FakeSocket()
            client.bind(('127.0.0.1', 0))
            with self.assertRaises(proxy_apply.ProxyApplyError) as caught:
                proxy_apply.preflight_local_listener(config(old), SimpleNamespace(process=None))
            self.assertEqual(caught.exception.code, 'LOCAL_PORT_OCCUPIED')
            rng.return_value.sample.return_value = [20000]
            new = helper.local_proxy_port()
            self.assertEqual(new, 20000)
            self.assertEqual(bound, {(socket.AF_INET, socket.SOCK_STREAM, 40000)})
            self.assertTrue({(family, kind, 20000) for family in (socket.AF_INET, socket.AF_INET6)
                             for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM)}.issubset(probes))
            client.close()
        self.assertFalse(bound)
        self.assertTrue(all(item.closed for item in sockets))

    def test_native_choice_still_rejects_exclusive_competing_binding(self):
        low, high = map(int, Path('/proc/sys/net/ipv4/ip_local_port_range').read_text().split())
        port = helper.local_proxy_port()
        self.assertFalse(low <= port <= high)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as competitor:
            competitor.bind(('127.0.0.1', port))
            with self.assertRaises(proxy_apply.ProxyApplyError) as caught:
                proxy_apply.preflight_local_listener(config(port), SimpleNamespace(process=None))
            self.assertEqual(caught.exception.code, 'LOCAL_PORT_OCCUPIED')
        proxy_apply.preflight_local_listener(config(port), SimpleNamespace(process=None))

    def test_d1_allocates_once_before_save_and_never_retries_http_failure(self):
        from test_tcp_upstream_export_real import TCPUpstreamExportRealTests
        subject = TCPUpstreamExportRealTests()
        subject.local_port = None
        subject.destination = SimpleNamespace(events=[], request=Mock())
        snapshot = Mock(headers={'etag': 'synthetic'})
        snapshot.json.return_value = {'settings': {'local_password_set': True}}
        failure = AssertionError('synthetic HTTP 409 must remain fatal')
        subject.destination.request.side_effect = [snapshot, failure, snapshot, failure]
        with patch('test_tcp_upstream_export_real.local_proxy_port', return_value=20000) as choose:
            for profile in ('first', 'later-negative'):
                with self.assertRaisesRegex(AssertionError, 'HTTP 409'):
                    subject.select_apply(profile)
            choose.assert_called_once_with()
        self.assertEqual(subject.local_port, 20000)
        self.assertEqual(subject.destination.events,
                         [{'event': 'local-proxy-port-selected', 'port': 20000}])
        requests = subject.destination.request.call_args_list
        self.assertEqual([call.args[0] for call in requests], ['GET', 'PUT', 'GET', 'PUT'])
        for call in requests[1::2]:
            self.assertEqual(call.kwargs['json']['local_port'], 20000)
            self.assertTrue(call.kwargs['json']['local_proxy_enabled'])

    def test_d1_check_and_apply_failures_are_not_retried(self):
        from test_tcp_upstream_export_real import TCPUpstreamExportRealTests
        for fail_at, count in (('check', 1), ('apply', 2)):
            with self.subTest(fail_at=fail_at):
                subject = TCPUpstreamExportRealTests()
                subject.sequence = 0
                checked = Mock()
                checked.json.return_value = {'valid': True, 'applied': False}
                failure = AssertionError('synthetic HTTP 409 remains fatal')
                replies = [failure] if fail_at == 'check' else [checked, failure]
                subject.destination = SimpleNamespace(request=Mock(side_effect=replies))
                with self.assertRaisesRegex(AssertionError, 'HTTP 409'):
                    subject.apply_revision('a' * 64, 'fixture')
                self.assertEqual(subject.destination.request.call_count, count)
                self.assertEqual([call.args[:2] for call in subject.destination.request.call_args_list],
                                 [('POST', '/api/proxy/check'), ('POST', '/api/proxy/apply')][:count])
