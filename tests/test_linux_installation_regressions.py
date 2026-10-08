"""Socket and command-only checks: never modify host services or firewalls."""
import argparse
from contextlib import ExitStack, nullcontext
import io
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.request import ProxyHandler, build_opener

from app.certificates import http01
from scripts import firewall_support as firewall
from scripts import install_system as installer


class LinuxInstallationRegressions(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))

    def ipv6_listener(self):
        try:
            listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            self.stack.callback(listener.close)
            listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            listener.bind(('::', 0))
            listener.listen()
        except OSError as exc:
            self.skipTest('IPv6 is unavailable: ' + str(exc))
        return listener

    def serve(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.stack.callback(close)

    def test_http01_ipv4_and_ipv6_serve_same_temporary_port(self):
        self.ipv6_listener()  # Only skip if the host cannot provide IPv6.
        challenge = self.root / '.well-known' / 'acme-challenge'
        challenge.mkdir(parents=True)
        token = 'A' * 43
        (challenge / token).write_bytes(b'challenge.key_authorization')
        server4 = http01.ChallengeServer(('0.0.0.0', 0), http01.handler_for(self.root))
        self.serve(server4)
        port = server4.server_port
        # With bindv6only=0 this raises EADDRINUSE before the fix. The service
        # must explicitly select IPv6-only mode before binding its second socket.
        server6 = http01.ChallengeServerV6(('::', port), http01.handler_for(self.root))
        self.serve(server6)
        self.assertEqual(server6.socket.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY), 1)
        opener = build_opener(ProxyHandler({}))
        for host in ('127.0.0.1', '[::1]'):
            with opener.open(f'http://{host}:{port}{http01.PREFIX}{token}', timeout=3) as response:
                self.assertEqual(response.read(), b'challenge.key_authorization')

    def test_openrc_main_serves_both_families_before_clean_shutdown(self):
        self.ipv6_listener()
        challenge = self.root / '.well-known' / 'acme-challenge'
        challenge.mkdir(parents=True)
        token = 'B' * 43
        (challenge / token).write_bytes(b'challenge.key_authorization')
        server4_class, server6_class = http01.ChallengeServer, http01.ChallengeServerV6
        opened = []
        def server4(address, handler):
            server = server4_class((address[0], 0), handler)
            opened.append(server)
            self.stack.callback(server.server_close)
            return server
        def server6(address, handler):
            server = server6_class((address[0], opened[0].server_port), handler)
            opened.append(server)
            self.stack.callback(server.server_close)
            return server
        def fetch_while_running():
            opener = build_opener(ProxyHandler({}))
            for host in ('127.0.0.1', '[::1]'):
                with opener.open(f'http://{host}:{opened[0].server_port}{http01.PREFIX}{token}', timeout=3) as response:
                    self.assertEqual(response.read(), b'challenge.key_authorization')
        fake_threading = SimpleNamespace(Thread=threading.Thread,
            BoundedSemaphore=threading.BoundedSemaphore,
            Event=lambda: SimpleNamespace(wait=fetch_while_running, set=lambda: None))
        with patch.dict('os.environ', {'VUI_HTTP01_DIRECT': '1', 'VUI_HTTP01_IPV6': '1'}), \
             patch('sys.argv', ['http01', '--webroot', str(self.root)]), \
             patch.object(http01.os, 'geteuid', return_value=1000), \
             patch.object(http01, 'ChallengeServer', side_effect=server4), \
             patch.object(http01, 'ChallengeServerV6', side_effect=server6), \
             patch.object(http01, 'threading', fake_threading), \
             patch.object(http01.signal, 'signal'):
            http01.main()
        self.assertEqual(len(opened), 2)
        self.assertTrue(all(server.socket.fileno() == -1 for server in opened))

    def test_openrc_ipv6_bind_failure_closes_only_its_new_ipv4_listener(self):
        listener = self.ipv6_listener()
        port = listener.getsockname()[1]
        server4_class, server6_class = http01.ChallengeServer, http01.ChallengeServerV6
        opened = []
        def server4(address, handler):
            server = server4_class((address[0], port), handler)
            opened.append(server)
            self.stack.callback(server.server_close)
            return server
        with patch.dict('os.environ', {'VUI_HTTP01_DIRECT': '1', 'VUI_HTTP01_IPV6': '1'}), \
             patch('sys.argv', ['http01', '--webroot', str(self.root)]), \
             patch.object(http01.os, 'geteuid', return_value=1000), \
             patch.object(http01, 'ChallengeServer', side_effect=server4), \
             patch.object(http01, 'ChallengeServerV6', side_effect=lambda address, handler: server6_class((address[0], port), handler)):
            with self.assertRaisesRegex(SystemExit, 'IPv6 HTTP-01 binding failed'):
                http01.main()
        self.assertEqual(opened[0].socket.fileno(), -1)
        installer.check_port(port)  # The newly opened IPv4 socket was released.
        with socket.create_connection(('::1', port), timeout=2):
            accepted, _ = listener.accept()
            accepted.close()

    def test_socket_activation_does_not_bind_new_ipv6_socket(self):
        with patch.object(http01.ChallengeServerV6, 'server_bind') as bind:
            server = http01.ChallengeServerV6(('::1', 0), http01.handler_for(self.root),
                                             bind_and_activate=False)
            self.stack.callback(server.server_close)
            bind.assert_not_called()

    def assert_ipv6_conflict_precedes_writes(self, manager, conflict_kind):
        listener = self.ipv6_listener()
        port = listener.getsockname()[1]
        args = argparse.Namespace(domain='panel.example.test', email='a@example.test',
            admin='admin', port=8443, bind='0.0.0.0', cert=None, key=None,
            accept_terms=True, node_port=port if conflict_kind == 'node' else 10443,
            open_firewall='yes', assume_external_ports_open=True, dry_run=False,
            upgrade=False, bundle='unused.zip', sha256='0' * 64)
        original_check = installer.check_port
        def check(checked_port, bind='0.0.0.0'):
            # Remap privileged HTTP-01 to a real ephemeral port; other unrelated
            # port checks are recorded but never touch the host's fixed ports.
            if conflict_kind == 'http01' and checked_port == 80:
                return original_check(port, bind)
            if conflict_kind == 'node' and checked_port == port:
                return original_check(port, bind)
        blocked = Mock(side_effect=AssertionError('Host mutation reached before port conflict'))
        with patch.object(installer, 'CONFIG', self.root / 'service.json'), \
             patch.object(installer, 'verify_archive', return_value=(io.BytesIO(), nullcontext(), {'release_id': 'test'})), \
             patch.object(installer, 'check_platform', return_value={'init': manager, 'name': 'fixture', 'target': 'x86_64-gnu'}), \
             patch.object(installer, 'check_reserved'), \
             patch.object(installer, 'probe_ipv6', return_value=True), \
             patch.object(installer, 'check_port', side_effect=check), \
             patch.object(installer, 'firewall_preflight', blocked), \
             patch.object(installer, 'create_service_user', blocked), \
             patch.object(installer, 'write_root_file', blocked), \
             patch.object(installer.service_support, 'stop', blocked), \
             patch.object(installer.service_support, 'enable_start', blocked):
            with self.assertRaisesRegex(installer.InstallError, f'Port {port} is already in use'):
                installer.install(args)
        blocked.assert_not_called()
        self.assertFalse((self.root / 'service.json').exists())
        # Verify the original listener is alive, accepting, and untouched.
        with socket.create_connection(('::1', port), timeout=2) as client:
            accepted, _ = listener.accept()
            with accepted:
                client.sendall(b'alive')
                self.assertEqual(accepted.recv(5), b'alive')

    def test_ipv6_node_conflict_precedes_all_writes_for_both_managers(self):
        for manager in ('systemd', 'openrc'):
            with self.subTest(manager=manager):
                self.assert_ipv6_conflict_precedes_writes(manager, 'node')

    def test_ipv6_http01_conflict_precedes_all_writes_for_both_managers(self):
        for manager in ('systemd', 'openrc'):
            with self.subTest(manager=manager):
                self.assert_ipv6_conflict_precedes_writes(manager, 'http01')

    def test_firewall_changes_require_explicit_yes(self):
        info = {'backend': 'firewalld', 'managed': True, 'zone': 'external', 'detail': 'fixture'}
        for choice, answer, allowed in (('no', True, False), ('ask', False, False),
                                        ('ask', True, True), ('yes', False, True)):
            args = argparse.Namespace(open_firewall=choice, dry_run=False,
                                      assume_external_ports_open=True)
            with self.subTest(choice=choice, answer=answer), \
                 patch.object(firewall, 'detect', return_value=info), \
                 patch.object(firewall, 'port_open', side_effect=[False, True]), \
                 patch.object(firewall, 'open_ports') as change, \
                 patch.object(installer, 'tty_confirm', return_value=answer) as confirm, \
                 patch('sys.stdout', new=io.StringIO()):
                if allowed:
                    installer.firewall_preflight(args, [8443])
                    change.assert_called_once_with(info, [8443])
                else:
                    with self.assertRaises(installer.InstallError):
                        installer.firewall_preflight(args, [8443])
                    change.assert_not_called()
                self.assertEqual(confirm.called, choice == 'ask')

    def test_ambiguous_zone_requires_manual_confirmation_even_with_yes(self):
        info = {'backend': 'firewalld', 'managed': False, 'detail': 'ambiguous ingress'}
        args = argparse.Namespace(open_firewall='yes', dry_run=False,
                                  assume_external_ports_open=False)
        with patch.object(firewall, 'detect', return_value=info), \
             patch.object(firewall, 'run') as query, \
             patch.object(firewall, 'open_ports') as change, \
             patch.object(installer, 'tty_confirm', return_value=False) as confirm, \
             patch('sys.stdout', new=io.StringIO()):
            with self.assertRaisesRegex(installer.InstallError, 'manual firewall configuration'):
                installer.firewall_preflight(args, [80, 8443, 10443])
            query.assert_not_called()
            change.assert_not_called()
            self.assertIn('actual ingress', confirm.call_args.args[0])

    def test_firewall_dry_run_never_writes_or_confirms(self):
        args = argparse.Namespace(open_firewall='yes', dry_run=True,
                                  assume_external_ports_open=False)
        for info in ({'backend': 'firewalld', 'managed': False, 'detail': 'ambiguous ingress'},
                     {'backend': 'firewalld', 'managed': True, 'zone': 'external', 'detail': 'fixture'}):
            status = False if info['managed'] else None
            with self.subTest(info=info), \
                 patch.object(firewall, 'detect', return_value=info), \
                 patch.object(firewall, 'port_open', return_value=status), \
                 patch.object(firewall, 'open_ports') as change, \
                 patch.object(installer, 'tty_confirm') as confirm, \
                 patch('sys.stdout', new=io.StringIO()):
                result = installer.firewall_preflight(args, [80])
                self.assertEqual(result['missing' if info['managed'] else 'unknown'], [80])
                change.assert_not_called()
                confirm.assert_not_called()

    def test_ipv4_only_install_preflight_has_no_ipv6_probes(self):
        args = argparse.Namespace(domain='panel.example.test', email='a@example.test',
            admin='admin', port=8443, bind='0.0.0.0', cert=None, key=None,
            accept_terms=True, node_port=10443, open_firewall='no',
            assume_external_ports_open=True, dry_run=True, upgrade=False,
            bundle='unused.zip', sha256='0' * 64)
        with patch.object(installer, 'CONFIG', self.root / 'service.json'), \
             patch.object(installer, 'verify_archive', return_value=(io.BytesIO(), nullcontext(), {'release_id': 'test'})), \
             patch.object(installer, 'check_platform', return_value={'init': 'openrc', 'name': 'fixture', 'target': 'x86_64-gnu'}), \
             patch.object(installer, 'check_reserved'), \
             patch.object(installer, 'probe_ipv6', return_value=False), \
             patch.object(installer, 'check_port') as check, \
             patch.object(installer, 'firewall_preflight', return_value={}), \
             patch.object(installer, 'create_service_user') as create, \
             patch('sys.stdout', new=io.StringIO()):
            installer.install(args)
        self.assertEqual([call.args for call in check.call_args_list],
                         [(80,), (8443, '0.0.0.0'), (10443, '0.0.0.0')])
        create.assert_not_called()

    def test_confirmation_requires_word_yes(self):
        for answer, accepted in (('yes\n', True), (' YES \n', True), ('y\n', False), ('\n', False), ('no\n', False)):
            terminal = Mock()
            terminal.__enter__ = Mock(return_value=terminal)
            terminal.__exit__ = Mock(return_value=False)
            terminal.readline.return_value = answer
            with self.subTest(answer=answer), patch('builtins.open', return_value=terminal):
                self.assertEqual(installer.tty_confirm('Open ports?'), accepted)


if __name__ == '__main__':
    unittest.main()
