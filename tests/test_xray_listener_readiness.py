"""Synthetic ownership proofs for Xray's dual-stack wildcard readiness."""
from collections import namedtuple
from itertools import count
from pathlib import Path
import socket
import unittest
from unittest.mock import MagicMock, patch

import psutil
from app.services.core_runtime import CoreRuntime


Address = namedtuple('Address', 'ip port')
Connection = namedtuple('Connection', 'family type laddr raddr status')


def listener(address='::', port=18443, kind=socket.SOCK_STREAM):
    return Connection(socket.AF_INET6 if ':' in address else socket.AF_INET, kind,
                      Address(address, port), (), psutil.CONN_LISTEN)


def established(local='::ffff:127.0.0.1', remote='::ffff:127.0.0.1', remote_port=50001):
    return Connection(socket.AF_INET6, socket.SOCK_STREAM, Address(local, 18443),
                      Address(remote, remote_port), psutil.CONN_ESTABLISHED)


class XrayListenerReadinessTests(unittest.TestCase):
    def setUp(self):
        # No core process or actual connection is created by these tests.
        self.runtime = CoreRuntime('xray', Path('/unused/xray'), Path('/unused/runtime'))
        self.config = {'inbounds': [{'listen': '0.0.0.0', 'port': 18443,
                                    'protocol': 'vless', 'streamSettings': {'network': 'raw'}}]}
        self.child = MagicMock()
        self.probe = MagicMock()
        self.probe.__enter__.return_value = self.probe
        self.probe.getsockname.return_value = ('127.0.0.1', 50001)
        self.probe.getpeername.return_value = ('127.0.0.1', 18443)
        self.connect = patch('app.services.core_runtime.socket.create_connection', return_value=self.probe).start()
        self.clock = count(10.0, 0.005)
        patch('app.services.core_runtime.time.monotonic', side_effect=lambda: next(self.clock)).start()
        self.sleep = patch('app.services.core_runtime.time.sleep').start()
        self.addCleanup(patch.stopall)

    def normalize(self, sockets=None, deadline=20.0):
        return self.runtime._xray_ipv4_wildcard_sockets(
            self.config, sockets if sockets is not None else [listener()], [self.child], deadline)

    def test_owned_dual_stack_connection_adds_ipv4_view_and_closes_probe(self):
        self.child.net_connections.return_value = [listener(), established()]
        result = self.normalize()
        self.assertTrue(CoreRuntime._listeners_ready(self.config, result))
        self.assertEqual(result[0].laddr.ip, '::')
        self.assertEqual(result[1].laddr.ip, '0.0.0.0')
        self.connect.assert_called_once_with(('127.0.0.1', 18443), timeout=0.15)
        self.probe.__exit__.assert_called_once()

    def test_delayed_accept_keeps_same_probe_open_until_owned_socket_appears(self):
        snapshots = iter(([listener()], [listener(), established()]))

        def snapshot(**kwargs):
            self.probe.__exit__.assert_not_called()
            return next(snapshots)

        self.child.net_connections.side_effect = snapshot
        result = self.normalize()
        self.assertTrue(CoreRuntime._listeners_ready(self.config, result))
        self.assertEqual(self.child.net_connections.call_count, 2)
        self.connect.assert_called_once()
        self.sleep.assert_called_once()
        self.probe.__exit__.assert_called_once()

    def test_ipv6_only_core_and_foreign_ipv4_listener_never_count_as_owned(self):
        # The probe connects, but only the foreign process owns its server end.
        self.child.net_connections.return_value = [listener()]
        result = self.normalize()
        self.assertFalse(CoreRuntime._listeners_ready(self.config, result))
        self.assertEqual(result, [listener()])
        self.assertGreater(self.child.net_connections.call_count, 1)
        self.probe.__exit__.assert_called_once()

    def test_owned_connection_must_match_both_ends_and_transport(self):
        for connection in (established(remote_port=50002), established(local='::1'),
                           established(remote='::ffff:127.0.0.2'),
                           established()._replace(family=socket.AF_INET),
                           established()._replace(type=socket.SOCK_DGRAM),
                           established()._replace(status=psutil.CONN_LISTEN)):
            with self.subTest(connection=connection):
                self.child.net_connections.return_value = [listener(), connection]
                self.assertFalse(CoreRuntime._listeners_ready(self.config, self.normalize()))

    def test_separate_owned_ipv4_listener_cannot_prove_ipv6_wildcard_is_dual_stack(self):
        self.child.net_connections.return_value = [listener(), listener('127.0.0.1'), established()]
        result = self.normalize([listener(), listener('127.0.0.1')])
        self.assertFalse(CoreRuntime._listeners_ready(self.config, result))
        self.connect.assert_not_called()

    def test_loopback_requests_remain_exact_and_do_not_probe(self):
        for address in ('127.0.0.1', '::1'):
            with self.subTest(address=address):
                self.config['inbounds'][0]['listen'] = address
                result = self.normalize()
                self.assertFalse(CoreRuntime._listeners_ready(self.config, result))
        self.connect.assert_not_called()

    def test_owned_wildcard_view_does_not_make_a_loopback_request_private(self):
        self.child.net_connections.return_value = [listener(), established()]
        self.config['inbounds'].append({**self.config['inbounds'][0], 'listen': '127.0.0.1'})
        self.assertFalse(CoreRuntime._listeners_ready(self.config, self.normalize()))

    def test_native_ipv4_requires_no_probe(self):
        result = self.normalize([listener('0.0.0.0')])
        self.assertTrue(CoreRuntime._listeners_ready(self.config, result))
        self.connect.assert_not_called()

    def test_non_xray_udp_or_exhausted_budget_does_not_probe(self):
        self.assertFalse(CoreRuntime._listeners_ready(self.config, self.normalize(deadline=10.0)))
        self.config['inbounds'][0]['streamSettings']['network'] = 'quic'
        self.normalize()
        self.config['inbounds'][0]['streamSettings']['network'] = 'raw'
        self.runtime.name = 'sing-box'
        self.normalize()
        self.connect.assert_not_called()

    def test_probe_timeout_uses_remaining_budget_and_expired_proof_is_rejected(self):
        self.child.net_connections.return_value = [listener(), established()]
        with patch('app.services.core_runtime.time.monotonic', side_effect=[10.0, 10.06]):
            result = self.normalize(deadline=10.05)
        self.assertFalse(CoreRuntime._listeners_ready(self.config, result))
        self.assertAlmostEqual(self.connect.call_args.kwargs['timeout'], 0.05)

    def test_probe_failure_or_unavailable_owned_snapshot_fails_closed(self):
        self.connect.side_effect = TimeoutError('synthetic timeout')
        self.assertFalse(CoreRuntime._listeners_ready(self.config, self.normalize()))
        self.connect.side_effect = None
        self.child.net_connections.side_effect = psutil.AccessDenied(42)
        self.assertFalse(CoreRuntime._listeners_ready(self.config, self.normalize()))
        self.probe.__exit__.assert_called_once()


if __name__ == '__main__':
    unittest.main()
