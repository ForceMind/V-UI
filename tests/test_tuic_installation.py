"""TUIC's native UDP listener reuses explicit, protocol-aware installer consent."""
import io
import unittest
from unittest.mock import call, patch
import test_hysteria2_installation as shared
from scripts import install_system as installer, firewall_support as firewall


class TUICInstallationTests(unittest.TestCase):
    setUp = shared.Hysteria2InstallationTests.setUp
    args = shared.Hysteria2InstallationTests.args
    install_preflight = shared.Hysteria2InstallationTests.install_preflight

    def test_declared_tuic_port_stays_independent_of_same_number_tcp_on_all_targets(self):
        for target in ('x86_64-gnu', 'x86_64-musl', 'aarch64-gnu', 'aarch64-musl'):
            with self.subTest(target=target):
                args = self.args(node_port=19446, node_udp_port=[19446])
                checked, prompt, output = self.install_preflight(args, target=target)
                self.assertIn(call(19446, '0.0.0.0'), checked)
                self.assertIn(call(19446, '0.0.0.0', protocol='udp'), checked)
                self.assertIn(call(19446, '::', protocol='udp'), checked)
                self.assertEqual(prompt, call(args, [80, 8443, 19446], [19446]))
                self.assertIn('"no_changes": true', output)

    def test_tuic_udp_rule_needs_exact_consent_without_reopening_tcp(self):
        info = {'backend': 'ufw', 'managed': True, 'detail': 'synthetic'}
        for answer in (False, True):
            with self.subTest(answer=answer), patch.object(firewall, 'detect', return_value=info), \
                 patch.object(firewall, 'port_open', side_effect=[True, False, True]), \
                 patch.object(firewall, 'open_ports') as write, \
                 patch.object(installer, 'tty_confirm', return_value=answer) as confirm, \
                 patch('sys.stdout', io.StringIO()):
                args = self.args(dry_run=False, open_firewall='ask', assume_external_ports_open=True)
                if answer:
                    installer.firewall_preflight(args, [19446], [19446])
                    write.assert_called_once_with(info, [19446], protocol='udp')
                else:
                    with self.assertRaises(installer.InstallError): installer.firewall_preflight(args, [19446], [19446])
                    write.assert_not_called()
                self.assertIn('19446/udp', confirm.call_args.args[0])
                self.assertNotIn('19446/tcp', confirm.call_args.args[0])
