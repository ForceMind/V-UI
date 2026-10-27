"""Failure-only PTY metadata; never inspect unrelated processes or input values."""
import json
import termios
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.ops_menu_smoke import MenuSession


class MenuFailureDiagnosticTests(unittest.TestCase):
    def session(self):
        session = MenuSession.__new__(MenuSession)
        session.process = SimpleNamespace(pid=12345)
        session.fd = 99
        return session

    def test_only_fixed_child_and_terminal_metadata_is_reported(self):
        session = self.session()
        controls = [b'\x00'] * 32
        controls[termios.VINTR] = b'\x03'
        attrs = [0, 0, 0, termios.ISIG | termios.ICANON, 0, 0, controls]
        raw = ('Name:\tPRIVATE-COMMAND\nState:\tS (PRIVATE-TEXT)\n'
               'SigBlk:\t0000000000000002\nSigPnd:\t0000000000000000\n'
               'ShdPnd:\t0000000000000000\nSigIgn:\t0000000000001000\n'
               'SigCgt:\t0000000000000002\nSecret:\tPRIVATE-INPUT\n')
        with patch('scripts.ops_menu_smoke.os.tcgetpgrp', return_value=12345) as foreground, \
                patch('scripts.ops_menu_smoke.os.getpgid', return_value=12345) as group, \
                patch('scripts.ops_menu_smoke.os.getsid', return_value=12345) as sid, \
                patch('scripts.ops_menu_smoke.termios.tcgetattr', return_value=attrs), \
                patch('scripts.ops_menu_smoke.Path.read_text', autospec=True, return_value=raw) as read:
            text = session.failure_diagnostics()
        foreground.assert_called_once_with(99)
        group.assert_called_once_with(12345)
        sid.assert_called_once_with(12345)
        self.assertEqual(str(read.call_args.args[0]), '/proc/12345/status')
        result = json.loads(text.split(': ', 1)[1])
        self.assertEqual(result['terminal'], {'isig': True, 'icanon': True, 'echo': False, 'vintr': 3})
        self.assertEqual(result['child_status']['State'], 'S')
        self.assertEqual(result['child_status']['SigBlk'], '0000000000000002')
        self.assertNotIn('PRIVATE', text)
        self.assertEqual(set(result['child_status']), {'State', 'SigBlk', 'SigPnd', 'ShdPnd', 'SigIgn', 'SigCgt'})

    def test_gone_child_closed_terminal_or_unreadable_proc_is_best_effort(self):
        with patch('scripts.ops_menu_smoke.os.tcgetpgrp', side_effect=OSError), \
                patch('scripts.ops_menu_smoke.os.getpgid', side_effect=ProcessLookupError), \
                patch('scripts.ops_menu_smoke.os.getsid', side_effect=ProcessLookupError), \
                patch('scripts.ops_menu_smoke.termios.tcgetattr', side_effect=termios.error), \
                patch('scripts.ops_menu_smoke.Path.read_text', side_effect=FileNotFoundError):
            result = json.loads(self.session().failure_diagnostics().split(': ', 1)[1])
        self.assertEqual(result['pid'], 12345)
        self.assertTrue(all(value == 'unavailable' for key, value in result.items() if key != 'pid'))

    def test_malformed_status_fields_are_not_echoed(self):
        with patch('scripts.ops_menu_smoke.os.tcgetpgrp', return_value=12345), \
                patch('scripts.ops_menu_smoke.os.getpgid', return_value=12345), \
                patch('scripts.ops_menu_smoke.os.getsid', return_value=12345), \
                patch('scripts.ops_menu_smoke.termios.tcgetattr', return_value=[]), \
                patch('scripts.ops_menu_smoke.Path.read_text', return_value='SigBlk: PRIVATE\nSigPnd: ' + 'f' * 17 + '\nState:\n'):
            result = json.loads(self.session().failure_diagnostics().split(': ', 1)[1])
        self.assertEqual(result['child_status'], {})
        self.assertEqual(result['terminal'], 'unavailable')

    def test_timeout_keeps_original_assertion_and_only_adds_diagnostic(self):
        session = self.session()
        session.timeout = 0
        session.process.poll = lambda: None
        session.text = lambda: 'fixed menu output'
        with patch.object(session, 'failure_diagnostics', return_value=' DIAGNOSTIC') as diagnostic, \
                patch('scripts.ops_menu_smoke.time.monotonic', side_effect=[0, 1]):
            with self.assertRaisesRegex(AssertionError, 'Menu did not exit\nfixed menu output DIAGNOSTIC'):
                session.finish(130)
        diagnostic.assert_called_once_with()

    def test_success_never_collects_diagnostics(self):
        session = self.session()
        session.process.poll = lambda: 0
        session.process.returncode = 0
        session.timeout = 60
        session._read = lambda timeout: False
        session.text = lambda: 'normal output'
        with patch.object(session, 'failure_diagnostics') as diagnostic:
            self.assertEqual(session.finish(), 'normal output')
        diagnostic.assert_not_called()
