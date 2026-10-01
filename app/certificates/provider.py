"""Certbot CSR mode: no live symlinks, user shell hooks, or untrusted CA URLs."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit
from app.certificates.material import CertificateError

DIRECTORIES = {
    'production': 'https://acme-v02.api.letsencrypt.org/directory',
    'staging': 'https://acme-staging-v02.api.letsencrypt.org/directory',
}


class CertbotProvider:
    def __init__(self, root: Path, *, test_directory: str | None = None, test_ca: Path | None = None):
        self.root, self.test_directory, self.test_ca = root, test_directory, test_ca
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
        return [sys.executable, '-B', '-c', 'from certbot.main import main; raise SystemExit(main())', 'certonly', '--non-interactive', '--quiet',
                '--config', '/dev/null', '--config-dir', str(config), '--work-dir', str(work / 'work'),
                '--logs-dir', str(logs), '--max-log-backups', '2', '--no-directory-hooks',
                '--webroot', '--webroot-path', str(self.webroot), '--preferred-challenges', 'http-01',
                '--agree-tos', '--email', record['email'], '--server', self.test_directory or DIRECTORIES[environment],
                '--csr', str(work / 'request.pem'), '--cert-path', str(work / 'cert.pem'),
                '--chain-path', str(work / 'chain.pem'), '--fullchain-path', str(work / 'fullchain.pem')]

    def issue(self, record: dict, work: Path, stop) -> bytes:
        env = dict(os.environ)
        # Do not inherit arbitrary proxy, hook, Python path or CA overrides in production.
        for name in list(env):
            if name.lower().endswith('_proxy') or name in {'PYTHONPATH', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE'}:
                env.pop(name, None)
        if self.test_ca:
            env['REQUESTS_CA_BUNDLE'] = str(self.test_ca)
        # stderr stays off the HTTP response. Certbot's bounded local logs are owner-only.
        process = subprocess.Popen(self.command(record, work), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, cwd=work, start_new_session=True)
        try:
            deadline = time.monotonic() + 240
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
