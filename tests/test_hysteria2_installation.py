"""Explicit QUIC UDP preflight only; never modify the test host's firewall."""
import argparse
from contextlib import ExitStack
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from scripts import install_system as installer
from scripts import firewall_support as firewall


class Hysteria2InstallationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))

    def args(self, **changes):
        values = dict(domain='panel.example.test', email='admin@example.test', admin='admin',
                      port=8443, node_port=10443, node_udp_port=[], bind='0.0.0.0',
                      cert=None, key=None, accept_terms=True, dry_run=True,
                      open_firewall='ask', assume_external_ports_open=False,
                      upgrade=False, bundle='unused.zip', sha256='0' * 64)
        values.update(changes)
        return argparse.Namespace(**values)

    def test_udp_ports_are_optional_explicit_repeatable_and_deduplicated(self):
        args = self.args(node_udp_port=[20443, 10443, 20443, 65535, 1024])
        installer.validate_options(args)
        self.assertEqual(args.node_udp_port, [1024, 10443, 20443, 65535])
        legacy = self.args()
        del legacy.node_udp_port
        installer.validate_options(legacy)
        self.assertEqual(legacy.node_udp_port, [])

    def test_udp_ports_reject_privileged_out_of_range_and_coerced_values(self):
        for values in ([0], [1023], [65536], [-1], [True], ['10443'], [10443.0], None, (10443,)):
            with self.subTest(values=values), self.assertRaisesRegex(installer.InstallError, 'UDP ports'):
                installer.validate_options(self.args(node_udp_port=values))

    def test_cli_exposes_repeatable_udp_ports_without_changing_tcp_default(self):
        argv = ['install_system', '--bundle', 'unused.zip', '--sha256', '0' * 64,
                '--domain', 'panel.example.test', '--email', 'admin@example.test',
                '--accept-terms', '--dry-run', '--node-udp-port', '10443', '--node-udp-port', '20443']
        with patch('sys.argv', argv), patch.object(installer, 'install') as install:
            installer.main()
        args = install.call_args.args[0]
        self.assertEqual(args.node_udp_port, [10443, 20443])
        self.assertEqual(args.node_port, 10443)
        self.assertEqual(args.open_firewall, 'ask')

    def test_udp_conflict_probe_uses_datagram_socket_for_each_address_family(self):
        for bind, family in (('0.0.0.0', socket.AF_INET), ('::', socket.AF_INET6)):
            with self.subTest(bind=bind), patch.object(installer.socket, 'socket') as create:
                installer.check_port(10443, bind, protocol='udp')
                create.assert_called_once_with(family, socket.SOCK_DGRAM)
                sock = create.return_value.__enter__.return_value
                sock.bind.assert_called_once_with((bind, 10443))
                if family == socket.AF_INET6:
                    sock.setsockopt.assert_called_once_with(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                else:
                    sock.setsockopt.assert_not_called()
                create.return_value.__exit__.assert_called_once()

    def test_udp_bind_conflict_reports_protocol_and_does_not_stop_services(self):
        with patch.object(installer.socket, 'socket') as create, \
             patch.object(installer.service_support, 'stop') as stop:
            create.return_value.__enter__.return_value.bind.side_effect = OSError('in use')
            with self.assertRaisesRegex(installer.InstallError, r'10443.*udp.*no existing service was stopped'):
                installer.check_port(10443, protocol='udp')
            create.return_value.__exit__.assert_called_once()
            stop.assert_not_called()

    def test_real_loopback_udp_conflict_preserves_the_existing_socket(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
            listener.settimeout(2)
            with self.assertRaisesRegex(installer.InstallError, 'udp'):
                installer.check_port(port, '127.0.0.1', protocol='udp')
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.sendto(b'fixture', ('127.0.0.1', port))
                self.assertEqual(listener.recvfrom(32)[0], b'fixture')

    def test_unknown_bind_protocol_is_rejected_before_socket_creation(self):
        with patch.object(installer.socket, 'socket') as create:
            with self.assertRaises(installer.InstallError):
                installer.check_port(10443, protocol='quic')
            create.assert_not_called()

    def install_preflight(self, args, *, manager='systemd', target='x86_64-gnu', ipv6=True, conflict=None):
        output = io.StringIO()
        blocked = Mock(side_effect=AssertionError('Unexpected host modification'))
        def check(port, bind='0.0.0.0', protocol='tcp'):
            if conflict == (port, bind, protocol):
                raise installer.InstallError(f'Port {port} is already in use ({protocol})')
        with patch.object(installer, 'CONFIG', self.root / 'service.json'), \
             patch.object(installer, 'verify_archive', return_value=(b'', None, {'release_id': 'fixture'})), \
             patch.object(installer, 'check_platform', return_value={'init': manager, 'name': 'fixture', 'target': target}), \
             patch.object(installer, 'check_reserved'), \
             patch.object(installer, 'probe_ipv6', return_value=ipv6), \
             patch.object(installer, 'check_port', side_effect=check) as checked, \
             patch.object(installer, 'firewall_preflight', return_value={}) as preflight, \
             patch.object(installer, 'create_service_user', blocked), \
             patch.object(installer, 'write_root_file', blocked), \
             patch.object(installer.service_support, 'stop', blocked), \
             patch.object(installer.service_support, 'enable_start', blocked), \
             patch('sys.stdout', output):
            if conflict:
                with self.assertRaisesRegex(installer.InstallError, 'udp'):
                    installer.install(args)
                preflight.assert_not_called()
            else:
                installer.install(args)
            blocked.assert_not_called()
        return checked.call_args_list, preflight.call_args, output.getvalue()

    def test_udp_preflight_on_all_four_linux_targets_and_both_managers(self):
        for target in ('x86_64-gnu', 'x86_64-musl', 'aarch64-gnu', 'aarch64-musl'):
            for manager in ('systemd', 'openrc'):
                with self.subTest(target=target, manager=manager):
                    args = self.args(node_udp_port=[20443, 10443, 20443])
                    checked, preflight, output = self.install_preflight(args, manager=manager, target=target)
                    self.assertEqual([c for c in checked if c.kwargs.get('protocol') == 'udp'], [
                        call(10443, '0.0.0.0', protocol='udp'), call(10443, '::', protocol='udp'),
                        call(20443, '0.0.0.0', protocol='udp'), call(20443, '::', protocol='udp'),
                    ])
                    self.assertEqual(preflight, call(args, [80, 8443, 10443], [10443, 20443]))
                    self.assertIn('"no_changes": true', output)

    def test_udp_ipv4_only_and_default_tcp_install_remain_explicit(self):
        checked, _, _ = self.install_preflight(self.args(node_udp_port=[10443]), ipv6=False)
        self.assertEqual([c for c in checked if c.kwargs.get('protocol') == 'udp'],
                         [call(10443, '0.0.0.0', protocol='udp')])
        checked, preflight, _ = self.install_preflight(self.args())
        self.assertFalse(any(c.kwargs.get('protocol') == 'udp' for c in checked))
        self.assertEqual(preflight.args[2], [])

    def test_udp_conflict_precedes_firewall_and_writes_for_both_managers(self):
        for manager in ('systemd', 'openrc'):
            for bind in ('0.0.0.0', '::'):
                with self.subTest(manager=manager, bind=bind):
                    self.install_preflight(self.args(node_udp_port=[10443], dry_run=False),
                                           manager=manager, conflict=(10443, bind, 'udp'))

    def test_upgrade_discloses_skipped_bind_probes_and_still_checks_udp_firewall(self):
        args = self.args(node_udp_port=[10443], upgrade=True)
        saved = {key: getattr(args, key) for key in ('domain', 'email', 'admin', 'port', 'bind')}
        saved.update(certificate_mode='managed', ready=True, service_manager='systemd', ipv6=True)
        (self.root / 'service.json').write_text(json.dumps(saved))
        checked, preflight, output = self.install_preflight(args)
        self.assertEqual(checked, [])
        self.assertEqual(preflight, call(args, [80, 8443, 10443], [10443]))
        self.assertIn('node UDP bind probes are skipped', output)
        self.assertIn('verify node port ownership manually', output)

    def test_firewall_dry_run_discloses_protocols_and_never_changes_or_confirms(self):
        info = {'backend': 'ufw', 'managed': True, 'detail': 'fixture'}
        def status(info, port, protocol='tcp'):
            return None if protocol == 'udp' and port == 20443 else False
        output = io.StringIO()
        with patch.object(firewall, 'detect', return_value=info), \
             patch.object(firewall, 'port_open', side_effect=status) as query, \
             patch.object(firewall, 'open_ports') as write, \
             patch.object(installer, 'tty_confirm') as confirm, patch('sys.stdout', output):
            result = installer.firewall_preflight(self.args(open_firewall='yes'), [8443, 10443], [20443, 10443, 10443])
        self.assertEqual(result['tcp_ports'], [8443, 10443])
        self.assertEqual(result['udp_ports'], [10443, 20443])
        self.assertEqual(result['missing'], [8443, 10443])
        self.assertEqual(result['missing_udp'], [10443])
        self.assertEqual(result['unknown_udp'], [20443])
        self.assertEqual(query.call_count, 4)
        self.assertIn('8443/tcp, 10443/tcp, 10443/udp, 20443/udp', output.getvalue())
        self.assertIn('does not create a node or enable application UDP forwarding', output.getvalue())
        write.assert_not_called()
        confirm.assert_not_called()

    def test_tcp_and_udp_changes_require_one_explicit_yes_for_exact_ports(self):
        info = {'backend': 'ufw', 'managed': True, 'detail': 'fixture'}
        for choice, answer, allowed in (('no', True, False), ('ask', False, False),
                                        ('ask', True, True), ('yes', False, True)):
            args = self.args(dry_run=False, open_firewall=choice, assume_external_ports_open=True)
            output = io.StringIO()
            with self.subTest(choice=choice, answer=answer), \
                 patch.object(firewall, 'detect', return_value=info), \
                 patch.object(firewall, 'port_open', side_effect=[False, False, True, True]), \
                 patch.object(firewall, 'open_ports') as write, \
                 patch.object(installer, 'tty_confirm', return_value=answer) as confirm, \
                 patch('sys.stdout', output):
                if allowed:
                    installer.firewall_preflight(args, [10443], [10443])
                    self.assertEqual(write.call_args_list, [call(info, [10443]), call(info, [10443], protocol='udp')])
                else:
                    with self.assertRaisesRegex(installer.InstallError, '10443/tcp, 10443/udp'):
                        installer.firewall_preflight(args, [10443], [10443])
                    write.assert_not_called()
                self.assertEqual(confirm.called, choice == 'ask')
                if confirm.called:
                    self.assertIn('10443/tcp, 10443/udp', confirm.call_args.args[0])
                self.assertIn('Local firewall changes requested: 10443/tcp, 10443/udp', output.getvalue())

    def test_udp_only_change_does_not_reopen_tcp_and_must_verify_udp(self):
        info = {'backend': 'ufw', 'managed': True, 'detail': 'fixture'}
        for final in (True, False, None):
            with self.subTest(final=final), patch.object(firewall, 'detect', return_value=info), \
                 patch.object(firewall, 'port_open', side_effect=[True, False, final]) as query, \
                 patch.object(firewall, 'open_ports') as write, patch('sys.stdout', io.StringIO()):
                args = self.args(dry_run=False, open_firewall='yes', assume_external_ports_open=True)
                if final is True:
                    installer.firewall_preflight(args, [10443], [10443])
                else:
                    with self.assertRaisesRegex(installer.InstallError, 'could not be verified'):
                        installer.firewall_preflight(args, [10443], [10443])
                write.assert_called_once_with(info, [10443], protocol='udp')
                self.assertEqual(query.call_args, call(info, 10443, protocol='udp'))

    def test_custom_or_ambiguous_udp_firewall_remains_manual_even_with_yes(self):
        for backend in ('nftables', 'iptables', 'firewalld'):
            info = {'backend': backend, 'managed': False, 'detail': 'manual fixture'}
            with self.subTest(backend=backend), patch.object(firewall, 'detect', return_value=info), \
                 patch.object(firewall, 'open_ports') as write, patch.object(firewall, 'run') as query, \
                 patch.object(installer, 'tty_confirm', return_value=False) as confirm, \
                 patch('sys.stdout', io.StringIO()):
                with self.assertRaisesRegex(installer.InstallError, 'manual firewall configuration'):
                    installer.firewall_preflight(self.args(dry_run=False, open_firewall='yes'), [8443], [10443])
                self.assertIn('8443/tcp, 10443/udp', confirm.call_args.args[0])
                self.assertIn('actual ingress', confirm.call_args.args[0])
                write.assert_not_called()
                query.assert_not_called()

    def test_cloud_udp_confirmation_is_required_even_when_local_firewall_open(self):
        info = {'backend': 'none', 'managed': False, 'detail': 'no firewall'}
        with patch.object(firewall, 'detect', return_value=info), \
             patch.object(firewall, 'open_ports') as write, \
             patch.object(installer, 'tty_confirm', return_value=False) as confirm, \
             patch('sys.stdout', io.StringIO()):
            with self.assertRaisesRegex(installer.InstallError, 'cloud/upstream'):
                installer.firewall_preflight(self.args(dry_run=False, open_firewall='yes'), [80, 8443], [10443])
            self.assertIn('80/tcp, 8443/tcp, 10443/udp', confirm.call_args.args[0])
            self.assertIn('cloud/upstream', confirm.call_args.args[0])
            write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
