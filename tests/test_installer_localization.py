"""Bounded Chinese installer prompts preserve explicit configuration and consent."""
import argparse
from contextlib import contextmanager
import io
import unittest
from unittest.mock import patch

from scripts import install_system as installer


class Terminal:
    def __init__(self, answers='', *, error=None, before_read=None):
        self.answers = io.StringIO(answers)
        self.output = io.StringIO()
        self.error = error
        self.before_read = before_read
        self.read_sizes = []

    def write(self, value):
        return self.output.write(value)

    def flush(self):
        pass

    def readline(self, size):
        self.read_sizes.append(size)
        if self.before_read:
            self.before_read()
        if self.error:
            raise self.error
        return self.answers.readline(size)


@contextmanager
def opened_terminal(terminal):
    yield terminal


class InstallerLocalizationTests(unittest.TestCase):
    def args(self, **changes):
        values = dict(domain='panel.example.test', email='admin@example.test', admin='admin',
                      port=8443, node_port=10443, node_udp_port=[], bind='0.0.0.0',
                      cert=None, key=None, accept_terms=False, interactive=True,
                      open_firewall='ask', assume_external_ports_open=True, dry_run=False)
        values.update(changes)
        return argparse.Namespace(**values)

    def test_confirm_and_ca_terms_accept_all_only_documented_answers(self):
        for answer in ('y', 'Y', 'yes', 'YES', 'Yes', 'n', 'N', 'no', 'NO', 'No', '', '  '):
            expected = answer.lower() in ('y', 'yes')
            with self.subTest(answer=answer):
                terminal = Terminal(answer + '\n')
                with patch.object(installer, 'controlling_terminal', side_effect=lambda: opened_terminal(terminal)):
                    self.assertIs(installer.tty_confirm('确认操作？'), expected)
                self.assertIn('回车默认否', terminal.output.getvalue())
                terminal = Terminal(answer + '\n')
                args = self.args()
                with patch.object(installer, 'controlling_terminal', side_effect=lambda: opened_terminal(terminal)):
                    installer.read_configuration(args)
                self.assertIs(args.accept_terms, expected)
                self.assertIn('https://letsencrypt.org/repository/', terminal.output.getvalue())
                self.assertIn('公开证书记录', terminal.output.getvalue())
                if not expected:
                    with self.assertRaises(installer.InstallError):
                        installer.validate_options(args)

    def test_invalid_confirmation_reasks_before_yes_or_no_for_both_paths(self):
        for final in ('y', 'n', ''):
            for kind in ('tty', 'ca'):
                with self.subTest(final=final, kind=kind):
                    terminal = Terminal('ok\ntrue\nyes please\n' + final + '\n')
                    with patch.object(installer, 'controlling_terminal', side_effect=lambda: opened_terminal(terminal)):
                        if kind == 'tty':
                            accepted = installer.tty_confirm('确认操作？')
                        else:
                            args = self.args()
                            installer.read_configuration(args)
                            accepted = args.accept_terms
                    self.assertIs(accepted, final == 'y')
                    self.assertEqual(terminal.output.getvalue().count('请输入 y/yes 或 n/no'), 3)
                    self.assertEqual(len(terminal.read_sizes), 4)

    def test_eof_and_interrupt_cancel_both_confirmation_paths(self):
        for error in (None, EOFError(), KeyboardInterrupt()):
            for kind in ('tty', 'ca'):
                with self.subTest(error=error, kind=kind):
                    terminal = Terminal(error=error)
                    args = self.args()
                    with patch.object(installer, 'controlling_terminal', side_effect=lambda: opened_terminal(terminal)):
                        with self.assertRaises(installer.InstallError):
                            if kind == 'tty':
                                installer.tty_confirm('确认操作？')
                            else:
                                installer.read_configuration(args)
                    self.assertFalse(args.accept_terms)

    def test_domain_retries_before_email_or_terms_and_normalizes_idna(self):
        terminal = Terminal('\nhttps://panel.example.test\nbad-.example.test\nBÜCHER.Example.\nadmin@example.test\ny\n')
        args = self.args(domain=None, email=None)
        with patch.object(installer, 'controlling_terminal', side_effect=lambda: opened_terminal(terminal)):
            installer.read_configuration(args)
        self.assertEqual(args.domain, 'xn--bcher-kva.example')
        self.assertEqual(args.email, 'admin@example.test')
        self.assertTrue(args.accept_terms)
        output = terminal.output.getvalue()
        self.assertEqual(output.count('面板域名（例如 panel.example.com）'), 4)
        self.assertEqual(output.count('请重新输入'), 3)
        self.assertLess(output.rindex('面板域名'), output.index('联系邮箱'))
        self.assertLess(output.index('联系邮箱'), output.index('CA 条款'))
        installer.validate_options(args)

    def test_explicit_domain_is_preserved_normalized_or_strictly_rejected(self):
        args = self.args(domain='PANEL.Example.Test.', interactive=False, accept_terms=True)
        with patch.object(installer, 'controlling_terminal', side_effect=AssertionError('explicit args must not prompt')):
            installer.read_configuration(args)
        self.assertEqual(args.domain, 'panel.example.test')
        for domain in ('https://panel.example.test', '-bad.example.test', '127.0.0.1'):
            args = self.args(domain=domain, interactive=False)
            with self.subTest(domain=domain), patch.object(installer, 'controlling_terminal', side_effect=AssertionError('invalid explicit domain must fail first')):
                with self.assertRaises(installer.InstallError):
                    installer.read_configuration(args)

    def test_oversized_confirm_cancels_without_consuming_tail(self):
        for kind in ('tty', 'ca'):
            with self.subTest(kind=kind):
                terminal = Terminal('y' * 10000 + '\ny\n')
                args = self.args()
                with patch.object(installer, 'controlling_terminal', side_effect=lambda: opened_terminal(terminal)):
                    with self.assertRaises(installer.InstallError):
                        if kind == 'tty':
                            installer.tty_confirm('确认操作？')
                        else:
                            installer.read_configuration(args)
                self.assertEqual(terminal.read_sizes, [installer.MAX_TERMINAL_INPUT + 2])
                self.assertFalse(args.accept_terms)
                self.assertEqual(terminal.answers.tell(), installer.MAX_TERMINAL_INPUT + 2)

    def test_oversized_domain_discards_valid_looking_tail_then_reasks(self):
        for suffix in ('panel.wrong.test', 'yes', 'BÜCHER.wrong'):
            with self.subTest(suffix=suffix):
                terminal = Terminal('x' * (3 * (installer.MAX_TERMINAL_INPUT + 2)) + suffix +
                                    '\npanel.example.test\nadmin@example.test\ny\n')
                args = self.args(domain=None, email=None)
                with patch.object(installer, 'controlling_terminal', side_effect=lambda: opened_terminal(terminal)):
                    installer.read_configuration(args)
                self.assertEqual(args.domain, 'panel.example.test')
                self.assertEqual(args.email, 'admin@example.test')
                self.assertTrue(args.accept_terms)
                self.assertEqual(terminal.read_sizes, [installer.MAX_TERMINAL_INPUT + 2] * 7)
                output = terminal.output.getvalue()
                self.assertEqual(output.count('面板域名（例如 panel.example.com）'), 2)
                self.assertEqual(output.count('请重新输入'), 1)
                self.assertLess(output.rindex('面板域名'), output.index('联系邮箱: '))
                installer.validate_options(args)

    def test_oversized_domain_drain_eof_or_interrupt_still_cancels(self):
        for end in ('', EOFError(), KeyboardInterrupt()):
            with self.subTest(end=end):
                terminal = Terminal()
                args = self.args(domain=None, email=None)
                with patch.object(terminal, 'readline', side_effect=['x' * (installer.MAX_TERMINAL_INPUT + 2), end]) as read, \
                     patch.object(installer, 'controlling_terminal', side_effect=lambda: opened_terminal(terminal)):
                    with self.assertRaises(installer.InstallError):
                        installer.read_configuration(args)
                self.assertEqual(read.call_count, 2)
                self.assertTrue(all(call.args == (installer.MAX_TERMINAL_INPUT + 2,) for call in read.call_args_list))
                self.assertIsNone(args.domain)
                self.assertFalse(args.accept_terms)
                self.assertNotIn('联系邮箱: ', terminal.output.getvalue())
                self.assertNotIn('CA 条款', terminal.output.getvalue())

    def test_domain_eof_and_interrupt_cancel_before_other_prompts(self):
        for error in (None, EOFError(), KeyboardInterrupt()):
            terminal = Terminal(error=error)
            with self.subTest(error=error), patch.object(installer, 'controlling_terminal', return_value=opened_terminal(terminal)):
                with self.assertRaises(installer.InstallError):
                    installer.read_configuration(self.args(domain=None, email=None))
            self.assertNotIn('联系邮箱', terminal.output.getvalue())
            self.assertNotIn('CA 条款', terminal.output.getvalue())

    def test_firewall_waits_for_explicit_yes_and_rechecks_ports(self):
        for answers, permitted in (('ok\ny\n', True), ('n\n', False), ('\n', False), ('', False)):
            info = {'backend': 'ufw', 'managed': True, 'detail': 'UFW active'}
            with self.subTest(answers=answers), patch.object(installer.firewall_support, 'detect', return_value=info), \
                 patch.object(installer.firewall_support, 'port_open', side_effect=[False, False, True, True]) as checked, \
                 patch.object(installer.firewall_support, 'open_ports') as opened, \
                 patch('sys.stdout', new_callable=io.StringIO) as output:
                terminal = Terminal(answers, before_read=opened.assert_not_called)
                with patch.object(installer, 'controlling_terminal', side_effect=lambda: opened_terminal(terminal)):
                    if permitted:
                        result = installer.firewall_preflight(self.args(), [8443, 80])
                        self.assertEqual(result['backend'], 'ufw')
                        self.assertEqual(result['missing'], [80, 8443])
                        opened.assert_called_once_with(info, [80, 8443])
                        self.assertEqual(checked.call_count, 4)
                    else:
                        with self.assertRaises(installer.InstallError):
                            installer.firewall_preflight(self.args(), [8443, 80])
                        opened.assert_not_called()
                self.assertIn('本机防火墙： UFW 已启用', output.getvalue())
                self.assertIn('80/tcp, 8443/tcp', output.getvalue())
                self.assertEqual(info['detail'], 'UFW active')

    def test_custom_rules_and_ambiguous_zone_never_modify_automatically(self):
        fixtures = (
            ('nftables', 'custom nftables rules detected', '自定义 nftables'),
            ('iptables', 'custom iptables rules detected', '自定义 iptables'),
            ('firewalld', 'firewalld ingress zone is ambiguous or unavailable; manual configuration required', '无法唯一确定 firewalld 入站区域'),
        )
        for backend, detail, text in fixtures:
            for answer in ('ok\ny\ny\n', 'n\n', '\n'):
                info = {'backend': backend, 'managed': False, 'detail': detail}
                with self.subTest(backend=backend, answer=answer), \
                     patch.object(installer.firewall_support, 'detect', return_value=info), \
                     patch.object(installer.firewall_support, 'open_ports') as opened, \
                     patch('sys.stdout', new_callable=io.StringIO) as output:
                    terminal = Terminal(answer)
                    with patch.object(installer, 'controlling_terminal', side_effect=lambda: opened_terminal(terminal)):
                        if answer.startswith('ok'):
                            result = installer.firewall_preflight(self.args(assume_external_ports_open=False), [80], [10443])
                            self.assertEqual(result['unknown'], [80])
                            self.assertEqual(result['unknown_udp'], [10443])
                            self.assertIn('实际入站防火墙区域', terminal.output.getvalue())
                            self.assertIn('云安全组或上游防火墙', terminal.output.getvalue())
                        else:
                            with self.assertRaises(installer.InstallError):
                                installer.firewall_preflight(self.args(assume_external_ports_open=False), [80], [10443])
                    opened.assert_not_called()
                    self.assertIn(text, output.getvalue())
                    self.assertIn('已禁用自动修改', output.getvalue())
                    self.assertEqual(info['detail'], detail)

    def test_cloud_ports_confirmation_defaults_no(self):
        info = {'backend': 'none', 'managed': False, 'detail': 'no active supported local firewall detected'}
        for answer in ('n\n', '\n', ''):
            with self.subTest(answer=answer), patch.object(installer.firewall_support, 'detect', return_value=info), \
                 patch.object(installer.firewall_support, 'open_ports') as opened, \
                 patch.object(installer, 'controlling_terminal', return_value=opened_terminal(Terminal(answer))), \
                 patch('sys.stdout', new_callable=io.StringIO):
                with self.assertRaises(installer.InstallError):
                    installer.firewall_preflight(self.args(assume_external_ports_open=False), [80])
                opened.assert_not_called()


if __name__ == '__main__':
    unittest.main()
