"""ACME DNS fixtures reserve both transports without masking real failures."""
from contextlib import ExitStack
import errno
import socket
import struct
import unittest
from unittest.mock import Mock, patch

import acme_helpers
from loopback_helpers import DnsAnswers, dns_query


class AcmeDnsFixtureTests(unittest.TestCase):
    def test_both_transports_serve_the_same_bound_port(self):
        answers = DnsAnswers('PEBBLE_DNS', {'panel.example.test': '127.0.0.1'})
        query = dns_query('panel.example.test', 1)
        expected = answers.answer(query)
        with ExitStack() as stack:
            port = acme_helpers.start_dual_dns(stack, answers)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                udp.settimeout(3)
                udp.sendto(query, ('127.0.0.1', port))
                self.assertEqual(udp.recvfrom(4096)[0], expected)
            with socket.create_connection(('127.0.0.1', port), timeout=3) as tcp:
                tcp.sendall(struct.pack('!H', len(query)) + query)
                response = b''
                while len(response) < len(expected) + 2:
                    chunk = tcp.recv(4096)
                    self.assertTrue(chunk)
                    response += chunk
                self.assertEqual(response, struct.pack('!H', len(expected)) + expected)

    def server(self, port):
        return Mock(server_address=('127.0.0.1', port))

    def test_udp_collision_closes_tcp_and_retries_before_starting_threads(self):
        first, second, udp = self.server(12345), self.server(12346), self.server(12346)
        collision = OSError(errno.EADDRINUSE, 'synthetic UDP port collision')
        with patch.object(acme_helpers.socketserver, 'ThreadingTCPServer', side_effect=[first, second]) as tcp_make, \
             patch.object(acme_helpers.socketserver, 'ThreadingUDPServer', side_effect=[collision, udp]) as udp_make, \
             patch.object(acme_helpers.threading, 'Thread') as thread:
            with ExitStack() as stack:
                self.assertEqual(acme_helpers.start_dual_dns(stack, Mock()), 12346)
                first.server_close.assert_called_once_with()
                first.shutdown.assert_not_called()
                second.server_close.assert_not_called()
                self.assertEqual(tcp_make.call_count, 2)
                self.assertEqual([call.args[0] for call in udp_make.call_args_list],
                                 [('127.0.0.1', 12345), ('127.0.0.1', 12346)])
                self.assertEqual(thread.call_count, 2)
                self.assertEqual(thread.return_value.start.call_count, 2)
            for server in (second, udp):
                server.shutdown.assert_called_once_with()
                server.server_close.assert_called_once_with()

    def test_retry_is_bounded_and_all_reserved_sockets_are_closed(self):
        servers = [self.server(12345 + index) for index in range(10)]
        with patch.object(acme_helpers.socketserver, 'ThreadingTCPServer', side_effect=servers) as tcp_make, \
             patch.object(acme_helpers.socketserver, 'ThreadingUDPServer', side_effect=OSError(errno.EADDRINUSE, 'collision')), \
             patch.object(acme_helpers.threading, 'Thread') as thread, ExitStack() as stack:
            with self.assertRaises(OSError) as raised:
                acme_helpers.start_dual_dns(stack, Mock())
            self.assertEqual(raised.exception.errno, errno.EADDRINUSE)
            self.assertEqual(tcp_make.call_count, 10)
            thread.assert_not_called()
        for server in servers:
            server.server_close.assert_called_once_with()

    def test_permission_denial_is_not_retried_or_hidden(self):
        for transport in ('tcp', 'udp'):
            with self.subTest(transport=transport):
                server = self.server(12345)
                denied = PermissionError(errno.EPERM, 'synthetic socket denial')
                with patch.object(acme_helpers.socketserver, 'ThreadingTCPServer',
                        side_effect=denied if transport == 'tcp' else None, return_value=server) as tcp_make, \
                     patch.object(acme_helpers.socketserver, 'ThreadingUDPServer', side_effect=denied) as udp_make, \
                     patch.object(acme_helpers.threading, 'Thread') as thread, ExitStack() as stack:
                    with self.assertRaises(PermissionError) as raised:
                        acme_helpers.start_dual_dns(stack, Mock())
                    self.assertIs(raised.exception, denied)
                    self.assertEqual(tcp_make.call_count, 1)
                    self.assertEqual(udp_make.call_count, 0 if transport == 'tcp' else 1)
                    if transport == 'udp':
                        server.server_close.assert_called_once_with()
                    thread.assert_not_called()


if __name__ == '__main__':
    unittest.main()
