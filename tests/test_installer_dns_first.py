"""Synthetic installer transactions, private TTY input, and DNS retry boundaries."""
import argparse
from contextlib import ExitStack, contextmanager, nullcontext
import io
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import tempfile
import termios
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import install_system as installer
from app.certificates import cli
from app.certificates.dns_store import DNSStoreError
from app.certificates.errors import CertificateError
from app.certificates.models import Certificate, CertificateBinding
from app.models import database
from app.release_tools import writer_operation
from scripts.installer_certificate_state import run as certificate_state
import test_dns_first_install as dnsfixtures
from test_dns_journal import TOKEN, NEW_TOKEN


class Terminal:
    def __init__(self, lines=()): self.lines = iter(lines); self.output = ''
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def write(self, value): self.output += value; return len(value)
    def flush(self): pass
    def readline(self, *args): return next(self.lines, '')


class InstallerDNSFirstTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-installer-dns-')))
        self.root = self.folder / 'instance'
        self.config = self.folder / 'config/service.json'
        self.events = []; self.fixture = None; self.create_mode = None
        self.terminal = Terminal(); self.secret = TOKEN

    def args(self, **changes):
        values = dict(domain='panel.example.test', email='a@example.test', admin='synthetic-admin',
            port=8443, node_port=10443, node_udp_port=[], bind='0.0.0.0', cert=None, key=None,
            certificate_method='dns-01', dns_zone='example.test', enable_http01=False, recover_dns=False,
            accept_terms=True, upgrade=False, dry_run=False, health_ca=None, interactive=False,
            unattended_upgrade=False, sha256='a' * 64, open_firewall='no', assume_external_ports_open=True)
        values.update(changes)
        return argparse.Namespace(**values)

    def start_fixture(self):
        if self.fixture is None:
            case = dnsfixtures.DNSFirstInstallTests()
            # The installer just created this private service-owned tree. Only
            # the fixture's directory allocator is replaced, not ORM/journals.
            with patch.object(dnsfixtures.tempfile, 'TemporaryDirectory', return_value=nullcontext(str(self.root))):
                case.setUp()
            self.addCleanup(case.doCleanups)
            self.fixture = case; case.server.create_mode = self.create_mode
        return self.fixture

    def service(self, args, **options):
        self.events.append(('service', list(args), dict(options)))
        if 'app.certificates.cli' not in args:
            if 'activate' in args:
                (self.root / 'CURRENT.json').write_text(json.dumps({'release_id': 'fixture', 'previous_id': None}))
            return SimpleNamespace(stdout='{}')
        case = self.start_fixture()
        command = args[args.index('app.certificates.cli') + 1]
        request = cli.private_dns_request(io.StringIO(options.get('input_text', '{"version":1}')))
        try:
            with writer_operation(self.root):
                if command == 'dns-bootstrap-status':
                    result = cli.dns_bootstrap_status(case.manager, dnsfixtures.HOST)
                elif command == 'dns-bootstrap-recovery-status':
                    result = cli.dns_bootstrap_recovery_status(case.manager, dnsfixtures.HOST)
                elif command == 'dns-bootstrap-recover':
                    result = cli.recover_dns_bootstrap(case.manager, dnsfixtures.HOST, request)
                else:
                    result = cli.bootstrap_panel(case.manager, dnsfixtures.HOST, 'a@example.test',
                        accept_terms=True, method='dns-01', dns_request=request)
            return SimpleNamespace(stdout=json.dumps(result))
        except (CertificateError, DNSStoreError) as error:
            raise subprocess.CalledProcessError(1, args, stderr=str(error)) from None

    def harness(self):
        account = SimpleNamespace(pw_uid=os.geteuid(), pw_gid=os.getegid())
        case = self
        class RootCalls:
            def __getattr__(self, name): return getattr(os, name)
            def geteuid(self): return 0
            def chown(self, *args): pass
        def write(path, value, mode=0o644):
            self.assertTrue(Path(path).is_relative_to(self.folder))
            if hasattr(value, 'read'): value = value.read()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value); path.chmod(mode)
        def port(number, *args, **kwargs):
            self.events.append(('port', number))
            if number == 80: raise installer.InstallError('synthetic unrelated listener occupies 80')
        @contextmanager
        def account_scope(*args, **kwargs):
            self.assertIn(('https',), self.events)
            self.events.append(('account',)); yield
        patches = [patch.multiple(installer, ROOT=self.root, CONFIG=self.config, CONFIG_DIR=self.config.parent,
                         CONTROL=self.folder / 'control', os=RootCalls()),
            patch.object(installer.service_support, 'SYSTEMD_DIR', self.folder / 'units'),
            patch.object(installer, 'require_legacy_core_installation'), patch.object(installer, 'record_application_origin'),
            patch.object(installer, 'check_reserved'), patch.object(installer, 'probe_ipv6', return_value=True),
            patch.object(installer, 'check_platform', return_value={'init': 'systemd', 'name': 'fixture',
                         'target': 'x86_64-gnu', 'arch': 'x86_64', 'libc': 'gnu'}),
            patch.object(installer, 'own_file', side_effect=lambda path: path.is_file() and not path.is_symlink()),
            patch.object(installer, 'write_root_file', side_effect=write),
            patch.object(installer, 'check_port', side_effect=port),
            patch.object(installer, 'firewall_preflight', side_effect=lambda args, ports, udp: self.events.append(('firewall', ports)) or {}),
            patch.object(installer, 'create_service_user', return_value=account),
            patch.object(installer.pwd, 'getpwnam', return_value=account),
            patch.object(installer, 'extract_controller'), patch.object(installer, 'check_legacy_core_data'),
            patch.object(installer, 'as_service', side_effect=self.service),
            patch.object(installer, 'certificate_state', side_effect=lambda work, action, *values:
                         json.dumps(certificate_state(action, self.root, *values))),
            patch.object(installer, 'controlling_terminal', side_effect=lambda: self.terminal),
            patch.object(installer, 'read_terminal_secret', side_effect=lambda *args: self.secret),
            patch.object(installer, 'https_health', side_effect=lambda *args, **kwargs: self.events.append(('https',))),
            patch.object(installer, 'provision_initial_account', side_effect=account_scope),
            patch.object(installer.service_support, 'is_active', return_value=False),
            patch.object(installer.service_support, 'reload'),
            patch.object(installer.service_support, 'enable_start', side_effect=lambda manager, names, **kwargs: self.events.append(('start', names))),
            patch.object(installer.service_support, 'start', side_effect=lambda manager, names, **kwargs: self.events.append(('start', names))),
            patch.object(installer.service_support, 'stop', side_effect=lambda manager, names: self.events.append(('stop', names))),
            patch.object(installer.service_support, 'disable', side_effect=lambda manager, names: self.events.append(('disable', names))),
            patch('sys.stdout', new=io.StringIO())]
        for item in patches: self.stack.enter_context(item)

    def install(self, args):
        installer.validate_options(args)
        archive = SimpleNamespace(open=lambda name: io.BytesIO(b'# synthetic trusted launcher'))
        return installer.install_verified(args, io.BytesIO(b'synthetic verified archive'), archive, {'release_id': 'fixture'})

    def retry_args(self, **changes):
        args = self.args(**changes)
        installer.read_configuration(args)
        installer.validate_options(args)
        self.assertIs(args._existing_config['ready'], False)
        return args

    def test_dns_dry_run_skips_busy_80_without_reading_token_or_creating_files(self):
        self.harness()
        self.install(self.args(dry_run=True))
        self.assertNotIn(('port', 80), self.events)
        self.assertIn(('firewall', [8443, 10443]), self.events)
        self.assertFalse(self.root.exists()); self.assertFalse(self.config.exists())
        self.assertFalse(any(event[0] == 'service' for event in self.events))

    def test_dns_success_registers_http_units_but_starts_only_panel_after_certificate(self):
        self.harness(); self.install(self.args())
        state = json.loads(self.config.read_text())
        self.assertIs(state['ready'], True); self.assertIs(state['http01_enabled'], False)
        self.assertEqual(state['certificate_method'], 'dns-01')
        self.assertTrue((self.folder / 'units/v-ui-http01.socket').is_file())
        self.assertEqual([event for event in self.events if event[0] == 'start'], [('start', ['v-ui.service'])])
        self.assertLess(self.events.index(('https',)), self.events.index(('account',)))
        self.assertNotIn(('port', 80), self.events)

    def test_provided_dry_run_skips_busy_80_but_explicit_http_still_checks(self):
        self.harness()
        cert = self.folder / 'chain.pem'; key = self.folder / 'key.pem'
        cert.touch(); key.touch()
        with patch.object(installer.ssl, 'SSLContext'):
            self.install(self.args(dry_run=True, cert=str(cert), key=str(key), certificate_method='provided', dns_zone=None))
            self.assertNotIn(('port', 80), self.events)
            for changes in ({'enable_http01': True, 'cert': str(cert), 'key': str(key), 'certificate_method': 'provided', 'dns_zone': None},
                            {'certificate_method': 'http-01', 'dns_zone': None}):
                with self.assertRaisesRegex(installer.InstallError, 'occupies 80'):
                    self.install(self.args(dry_run=True, **changes))

    def test_forbidden_first_install_preserves_config_then_local_token_repair_and_retry(self):
        self.harness(); self.create_mode = 'forbidden'
        with self.assertRaisesRegex(installer.InstallError, 'DNS_PROVIDER_FORBIDDEN'):
            self.install(self.args())
        self.assertIs(json.loads(self.config.read_text())['ready'], False)
        self.assertNotIn(('account',), self.events)
        profile = self.fixture.store.list()[0]
        original = self.fixture.store.snapshot(profile['id'])
        self.terminal = Terminal(['1\n', 'yes\n']); self.secret = NEW_TOKEN
        with self.assertRaisesRegex(installer.InstallError, 'RETRY_COOLDOWN'):
            self.install(self.retry_args(recover_dns=True))
        self.assertIs(json.loads(self.config.read_text())['ready'], False)
        self.assertEqual(self.fixture.store.snapshot(profile['id'])['api_token'], NEW_TOKEN)
        self.assertEqual(self.fixture.store.snapshot(profile['id'], revision=original['revision'])['api_token'], TOKEN)
        self.assertFalse((self.config.parent / installer.TRANSACTION_NAME).exists())
        self.fixture.server.create_mode = None
        with database.SessionLocal() as db:
            row = db.query(Certificate).one(); row.retry_at = row.last_attempt = 0; db.commit()
        self.install(self.retry_args())
        self.assertIs(json.loads(self.config.read_text())['ready'], True)
        self.assertEqual(len(self.fixture.store.list()), 1)
        self.assertNotIn(('port', 80), self.events)

    def test_unknown_first_write_requires_explicit_absence_confirmation_and_keeps_cooldown(self):
        self.harness(); self.create_mode = 'timeout-before'
        with self.assertRaisesRegex(installer.InstallError, 'DNS_CLEANUP_PENDING'):
            self.install(self.args())
        job = self.fixture.journal.pending()[0]['job_id']
        self.terminal = Terminal(['4\n', job + '\n', 'no\n'])
        with self.assertRaisesRegex(installer.InstallError, '未获确认'):
            self.install(self.retry_args(recover_dns=True))
        self.assertEqual(self.fixture.journal.view(job)['phase'], 'cleanup_pending')
        self.terminal = Terminal(['4\n', job + '\n', 'yes\n'])
        with self.assertRaisesRegex(installer.InstallError, 'RETRY_COOLDOWN'):
            self.install(self.retry_args(recover_dns=True))
        self.assertEqual(self.fixture.journal.view(job)['phase'], 'manual_confirmed')
        self.assertIs(json.loads(self.config.read_text())['ready'], False)
        self.assertEqual(sum(method == 'post' for method, _ in self.fixture.server.calls), 1)
        self.assertNotIn(('account',), self.events)

    def test_stopped_dns_upgrade_enable_failure_restores_disabled_boot_state(self):
        self.harness(); self.create_mode = 'forbidden'
        with self.assertRaises(installer.InstallError): self.install(self.args())
        args = self.retry_args(enable_http01=True)
        # Explicit opt-in may probe 80; its failure is injected after enabling.
        with patch.object(installer, 'check_port'), self.assertRaisesRegex(installer.InstallError, 'RETRY_COOLDOWN'):
            self.install(args)
        self.assertIn(('start', ['v-ui-http01.socket']), self.events)
        self.assertIn(('disable', ['v-ui-http01.socket']), self.events)
        self.assertIs(json.loads(self.config.read_text())['http01_enabled'], False)
        self.assertFalse((self.config.parent / installer.TRANSACTION_NAME).exists())

    def test_private_token_only_enters_service_stdin_and_dns_budget_covers_recovery(self):
        account = SimpleNamespace(pw_uid=1234, pw_gid=1234)
        responses = [SimpleNamespace(stdout='{"version":1,"needs_token":true}'), SimpleNamespace(stdout='{}')]
        with patch.object(installer.pwd, 'getpwnam', return_value=account), \
             patch.object(installer, 'command', side_effect=responses) as command, \
             patch.object(installer, 'controlling_terminal', return_value=Terminal()), \
             patch.object(installer, 'read_terminal_secret', return_value=TOKEN):
            installer.bootstrap_dns_certificate('/portable/python', '/payload', self.args())
        args, values = command.call_args
        self.assertNotIn(TOKEN, str(args)); self.assertNotIn(TOKEN, str(values['env']))
        self.assertEqual(json.loads(values['input'])['api_token'], TOKEN)
        self.assertTrue(values['capture_output']); self.assertEqual(values['timeout'], installer.DNS_BOOTSTRAP_TIMEOUT)
        from app.certificates.dns_journal import ISSUE_BUDGET, RECOVERY_BUDGET
        self.assertGreater(installer.DNS_BOOTSTRAP_TIMEOUT, ISSUE_BUDGET + 2 * RECOVERY_BUDGET)
        self.assertEqual(command.call_args_list[0].kwargs['timeout'], 360)

    def test_secret_failure_output_is_redacted_and_existing_dns_needs_no_terminal(self):
        for error in (subprocess.CalledProcessError(1, ['bootstrap'], output=TOKEN, stderr=TOKEN + ' DNS_PROVIDER_FORBIDDEN'),
                      subprocess.TimeoutExpired(['bootstrap'], 1, output=TOKEN, stderr=TOKEN)):
            with patch.object(installer, 'as_service', side_effect=[SimpleNamespace(stdout='{"version":1,"needs_token":false}'), error]), \
                 patch.object(installer, 'controlling_terminal', side_effect=AssertionError('no TTY for existing credentials')):
                with self.assertRaises(installer.InstallError) as failure:
                    installer.bootstrap_dns_certificate('/portable/python', '/payload', self.args())
                self.assertNotIn(TOKEN, str(failure.exception)); self.assertTrue(failure.exception.__suppress_context__)


class InstallerDNSHiddenInputTests(unittest.TestCase):
    def test_real_pty_hides_token_and_restores_echo(self):
        master, slave = pty.openpty()
        result = []
        def read():
            with io.TextIOWrapper(os.fdopen(os.dup(slave), 'rb+', buffering=0), encoding='utf-8', write_through=True) as terminal:
                result.append(installer.read_terminal_secret(terminal, 'Token: '))
        before = termios.tcgetattr(slave)
        thread = threading.Thread(target=read)
        try:
            thread.start()
            self.assertTrue(select.select([master], [], [], 5)[0])
            self.assertIn(b'Token: ', os.read(master, 256))
            self.assertFalse(termios.tcgetattr(slave)[3] & termios.ECHO)
            os.write(master, TOKEN.encode() + b'\n')
            thread.join(5); self.assertFalse(thread.is_alive())
            self.assertEqual(result, [TOKEN]); self.assertEqual(termios.tcgetattr(slave), before)
            if select.select([master], [], [], .1)[0]: self.assertNotIn(TOKEN.encode(), os.read(master, 1024))
        finally:
            os.close(master); os.close(slave)

    def test_overlong_or_interrupted_secret_restores_echo_and_flushes_tail(self):
        for failure in (KeyboardInterrupt(), installer.InstallError('overlong synthetic input')):
            master, slave = pty.openpty()
            try:
                before = termios.tcgetattr(slave)
                with io.TextIOWrapper(os.fdopen(os.dup(slave), 'rb+', buffering=0), encoding='utf-8', write_through=True) as terminal:
                    def fail_read(*args):
                        self.assertFalse(termios.tcgetattr(slave)[3] & termios.ECHO)
                        os.write(master, (TOKEN + '-remaining-tail\n').encode())
                        raise failure
                    with patch.object(installer, 'read_terminal_line', side_effect=fail_read), self.assertRaises(type(failure)):
                        installer.read_terminal_secret(terminal, 'Token: ')
                self.assertEqual(termios.tcgetattr(slave), before)
                self.assertFalse(select.select([slave], [], [], .05)[0])
            finally:
                os.close(master); os.close(slave)

    def test_real_overlong_pty_line_is_rejected_without_echo_or_pending_suffix(self):
        master, slave = pty.openpty(); errors = []
        before = termios.tcgetattr(slave)
        def read():
            try:
                with io.TextIOWrapper(os.fdopen(os.dup(slave), 'rb+', buffering=0), encoding='utf-8', write_through=True) as terminal:
                    installer.read_terminal_secret(terminal, 'Token: ')
            except installer.InstallError as error:
                errors.append(str(error))
        thread = threading.Thread(target=read)
        try:
            thread.start()
            self.assertTrue(select.select([master], [], [], 5)[0])
            self.assertIn(b'Token: ', os.read(master, 256))
            os.write(master, (TOKEN + 'x' * 2000 + '\n').encode())
            thread.join(5); self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1); self.assertNotIn(TOKEN, errors[0])
            self.assertEqual(termios.tcgetattr(slave), before)
            self.assertFalse(select.select([slave], [], [], .05)[0])
            if select.select([master], [], [], .1)[0]: self.assertNotIn(TOKEN.encode(), os.read(master, 4096))
        finally:
            os.close(master); os.close(slave)


if __name__ == '__main__':
    unittest.main()
