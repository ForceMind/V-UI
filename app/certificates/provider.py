"""Certbot CSR mode: no live symlinks, user shell hooks, or untrusted CA URLs."""
import os
from pathlib import Path
import signal
import shlex
import subprocess
import sys
import time
from urllib.parse import urlsplit
from app.certificates.errors import CertificateError
from app.certificates.dns_journal import DNSChallengeJournal
from app.certificates.dns_store import DNSCredentialStore, DNSStoreError

DIRECTORIES = {
    'production': 'https://acme-v02.api.letsencrypt.org/directory',
    'staging': 'https://acme-staging-v02.api.letsencrypt.org/directory',
}


class CertbotProvider:
    supports_dns = True

    def __init__(self, root: Path, *, test_directory: str | None = None, test_ca: Path | None = None,
                 dns_journal=None, dns_propagation_seconds=60):
        self.root, self.test_directory, self.test_ca = root, test_directory, test_ca
        if type(dns_propagation_seconds) is not int or not 30 <= dns_propagation_seconds <= 300:
            raise CertificateError('INVALID_DNS_PROPAGATION')
        self.dns_propagation_seconds = dns_propagation_seconds
        self.dns_journal = dns_journal or DNSChallengeJournal(root)
        if test_directory:
            url = urlsplit(test_directory)
            if url.scheme != 'https' or url.hostname not in {'127.0.0.1', 'localhost', '::1'} or not test_ca:
                raise CertificateError('INVALID_TEST_DIRECTORY')

    @property
    def webroot(self) -> Path:
        return self.root / 'http-webroot'

    def command(self, record: dict, work: Path) -> list[str]:
        environment = record['environment']
        if environment not in DIRECTORIES:
            raise CertificateError('INVALID_ENVIRONMENT')
        config = self.root / 'accounts' / environment
        logs = self.root / 'logs' / environment
        for directory in (config, logs, self.webroot):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        command = [sys.executable, '-B', '-c', 'from certbot.main import main; raise SystemExit(main())', 'certonly', '--non-interactive', '--quiet',
                '--config', '/dev/null', '--config-dir', str(config), '--work-dir', str(work / 'work'),
                '--logs-dir', str(logs), '--max-log-backups', '2', '--no-directory-hooks',
                '--webroot', '--webroot-path', str(self.webroot), '--preferred-challenges', 'http-01',
                '--agree-tos', '--email', record['email'], '--server', self.test_directory or DIRECTORIES[environment],
                '--csr', str(work / 'request.pem'), '--cert-path', str(work / 'cert.pem'),
                '--chain-path', str(work / 'chain.pem'), '--fullchain-path', str(work / 'fullchain.pem')]
        if record.get('method', 'http-01') == 'dns-01':
            command[:4] = [sys.executable, '-B', '-I', str(Path(__file__).absolute().with_name('dns_certbot.py')), str(self.root)]
            index = command.index('--webroot')
            command[index:index + 5] = ['--manual', '--preferred-challenges', 'dns-01',
                '--manual-auth-hook', self._dns_hook_command(record, 'auth'),
                '--manual-cleanup-hook', self._dns_hook_command(record, 'cleanup')]
        return command

    def _dns_hook_command(self, record, action):
        import re
        if not re.fullmatch('[a-f0-9]{32}', record.get('job_id', '')):
            raise CertificateError('INVALID_DNS_JOB')
        return shlex.join([sys.executable, '-B', '-I', str(Path(__file__).absolute().with_name('dns_hook.py')),
                           action, str(self.root), record['job_id'], '--deadline', str(record['dns_deadline']),
                           '--propagation-seconds', str(self.dns_propagation_seconds)])

    def check_dns_record(self, record):
        try:
            snapshot = DNSCredentialStore(self.root).snapshot(record.credential_id)
        except DNSStoreError as exc:
            raise CertificateError(exc.code) from None
        if any(row['zone_name'] == snapshot['zone_name'] and row['domain'] == record.domain
               for row in self.dns_journal.pending()):
            raise CertificateError('DNS_CHALLENGE_OWNER_BUSY')

    def recover_dns(self, stop):
        return self.dns_journal.recover_all(stop)

    def issue(self, record: dict, work: Path, stop) -> bytes:
        if record.get('method', 'http-01') != 'dns-01':
            return self._issue_process(record, work, stop, deadline=time.monotonic() + 240)
        context = self.dns_journal.begin(record['job_id'], record['credential_id'], record['credential_revision'],
                                         record['domain'], record['names'])
        selected = {**record, 'dns_deadline': context['deadline']}
        deadline = time.monotonic() + max(0, context['deadline'] - time.time())
        failure, chain = None, None
        try:
            chain = self._issue_process(selected, work, stop, deadline=deadline)
        except Exception as exc:
            failure = exc
        try:
            # Cleanup ignores issuance cancellation, but shares its hard time
            # budget. A killed parent leaves durable intents for next startup.
            cleaned = self.dns_journal.cleanup(record['job_id'], deadline=deadline)
        except Exception:
            cleaned = False
        if not cleaned:
            raise CertificateError('DNS_CLEANUP_PENDING') from None
        if failure is not None:
            detail = self.dns_journal.view(record['job_id']).get('error')
            if detail:
                raise CertificateError(detail) from None
            raise failure
        return chain

    def _issue_process(self, record, work, stop, *, deadline):
        # Neither HTTP nor DNS tasks inherit tokens, arbitrary proxies, Python
        # injection, hook configuration or credential environment variables.
        env = {name: os.environ[name] for name in (
            'PATH', 'HOME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
            'MALLOC_ARENA_MAX', 'MALLOC_TRIM_THRESHOLD_', 'MALLOC_MMAP_THRESHOLD_') if name in os.environ}
        if self.test_ca:
            env['REQUESTS_CA_BUNDLE'] = str(self.test_ca)
        # stderr stays off the HTTP response. Certbot's bounded local logs are owner-only.
        process = subprocess.Popen(self.command(record, work), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, cwd=work, start_new_session=True)
        try:
            while process.poll() is None:
                if stop.wait(.2) or time.monotonic() > deadline:
                    raise CertificateError('ISSUANCE_INTERRUPTED_OR_TIMED_OUT')
            if process.returncode:
                raise CertificateError('ACME_VALIDATION_FAILED')
            output = work / 'fullchain.pem'
            if output.is_symlink() or not output.is_file():
                raise CertificateError('CERTBOT_OUTPUT_MISSING')
            with output.open('rb') as handle:
                return handle.read(131073)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
