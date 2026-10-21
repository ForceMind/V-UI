"""Managed opt-in plumbing only: fake exec/collector, no Tailscale invocation."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from app.services import tailscale_status
from deploy import system_launcher as launcher


FLAG = 'VUI_TAILSCALE_STATUS_ENABLED'
TARGETS = ('x86_64-gnu', 'aarch64-gnu', 'x86_64-musl', 'aarch64-musl')
MODES = ('panel', 'http01', 'http01-direct')
CONFIG = {'origin': 'https://127.0.0.1:8443', 'bind': '127.0.0.1',
          'port': 8443, 'certificate_mode': 'provided'}


class TailscaleLauncherTests(unittest.TestCase):
    def launch(self, target, mode, inherited):
        with patch.object(launcher, 'load_selected', return_value=(
                CONFIG, Path('/release'), Path('/payload'), Path('/runtime/python'), target)), \
                patch.object(launcher.sys, 'argv', ['launcher', mode]), \
                patch.object(launcher.os, 'chdir'), \
                patch.object(launcher.os, 'execve') as execute, \
                patch.dict(os.environ, inherited, clear=True):
            try:
                launcher.main()
            except RuntimeError:
                execute.assert_not_called()
                raise
        execute.assert_called_once()
        return execute.call_args.args[2]

    def test_managed_exec_preserves_only_explicit_panel_opt_in(self):
        for target in TARGETS:
            for mode in MODES:
                for value in (None, '0', '1'):
                    with self.subTest(target=target, mode=mode, value=value):
                        inherited = {} if value is None else {FLAG: value}
                        environment = self.launch(target, mode, inherited)
                        if mode == 'panel' and value is not None:
                            self.assertEqual(environment[FLAG], value)
                        else:
                            self.assertNotIn(FLAG, environment)
                        self.assertEqual(environment.get('MALLOC_MMAP_THRESHOLD_'),
                                         '131072' if mode == 'panel' and target.endswith('-gnu') else None)

    def test_invalid_panel_values_refuse_before_exec_without_echoing_input(self):
        for target in TARGETS:
            for value in ('', 'true', 'false', '01', ' 1', '1\n', '１', 'secret-value' * 100):
                with self.subTest(target=target, value=value):
                    with self.assertRaises(RuntimeError) as caught:
                        self.launch(target, 'panel', {FLAG: value})
                    self.assertEqual(str(caught.exception), FLAG + ' must be 0 or 1')

    def test_http01_ignores_even_invalid_panel_option(self):
        for target in TARGETS:
            for mode in ('http01', 'http01-direct'):
                with self.subTest(target=target, mode=mode):
                    self.assertNotIn(FLAG, self.launch(target, mode, {FLAG: 'invalid'}))

    def test_strict_filter_and_input_mappings_unchanged(self):
        excluded = {'TS_AUTHKEY': 'fake-secret', 'TAILSCALE_AUTHKEY': 'fake-secret',
                    'TS_SOCKET': '/untrusted/socket', 'VUI_TAILSCALE_SOCKET': '/untrusted/socket',
                    'VUI_TAILSCALE_BINARY': '/untrusted/program', 'PYTHONPATH': '/untrusted',
                    'LD_PRELOAD': '/untrusted', 'GLIBC_TUNABLES': 'untrusted'}
        for target in TARGETS:
            for mode in MODES:
                with self.subTest(target=target, mode=mode):
                    environment = self.launch(target, mode, {FLAG: '1', **excluded})
                    self.assertTrue(excluded.keys().isdisjoint(environment))
                    self.assertEqual(environment['PATH'], '/usr/bin:/bin')
            base, inherited = {'PATH': '/safe'}, {FLAG: '1', **excluded}
            result = launcher.system_panel_environment(base, target, inherited)
            self.assertEqual(base, {'PATH': '/safe'})
            self.assertEqual(inherited, {FLAG: '1', **excluded})
            self.assertIsNot(result, base)
            self.assertTrue(excluded.keys().isdisjoint(result))

    def test_managed_panel_option_reaches_observer_without_real_probe(self):
        for target in TARGETS:
            for value in (None, '0', '1'):
                with self.subTest(target=target, value=value):
                    inherited = {} if value is None else {FLAG: value}
                    environment = self.launch(target, 'panel', inherited)
                    collector = Mock(return_value=json.dumps({
                        'Version': '1.104.2', 'BackendState': 'Running',
                        'Self': {'Online': True}}).encode())
                    observer = tailscale_status.TailscaleStatus(_collector=collector)
                    with patch.dict(os.environ, environment, clear=True):
                        result = observer.read()
                    if value == '1':
                        self.assertTrue(result['enabled'])
                        self.assertEqual(result['state'], 'online')
                        collector.assert_called_once_with()
                    else:
                        self.assertEqual(result, {'enabled': False})
                        collector.assert_not_called()
