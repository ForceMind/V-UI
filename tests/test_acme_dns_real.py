"""Actions-only pinned Certbot DNS-01 -> real Pebble -> real loopback HTTPS/DNS.

Normal unit discovery skips these explicit external-binary/source gates. The
ACME workflow supplies both gates and rejects missing cases or any skipped case.
"""
from contextlib import ExitStack
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shlex
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cryptography import x509
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app import release_tools
from app.certificates import cloudflare, manager as manager_module
from app.certificates.cloudflare import CloudflareClient
from app.certificates.dns_journal import DNSChallengeJournal
from app.certificates.dns_store import DNSCredentialStore
from app.certificates.manager import CertificateManager
from app.certificates.models import Certificate
from app.certificates.provider import CertbotProvider
from app.models import database
from acme_helpers import PebbleFixture
from fake_cloudflare_https import (FakeCloudflareState, CloudflareDNSAnswers, LoopbackCloudflareTransport,
                                   start_cloudflare_https, ZONE_ID, ZONE_NAME, OTHER_ID)
from loopback_helpers import certificate_files

ROOT = Path(__file__).resolve().parents[1]
LEGACY22_SHA = '558687a76cd29f4e4735284926eb05bf6664472e'
DOMAIN = 'example.test'
FAKE_TOKEN = 'fake_CF_token_' + 'x' * 32


class FixtureHookProvider(CertbotProvider):
    """Only the trusted test hook command differs from the real provider."""
    def set_hook(self, path):
        self.fixture_hook = Path(path)
        self.fixture_hook_sha = hashlib.sha256(self.fixture_hook.read_bytes()).hexdigest()

    def _dns_hook_command(self, record, action):
        info = self.fixture_hook.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid() or info.st_nlink != 1
                or hashlib.sha256(self.fixture_hook.read_bytes()).hexdigest() != self.fixture_hook_sha):
            raise AssertionError('trusted test hook identity changed')
        return shlex.join([sys.executable, '-B', '-I', str(self.fixture_hook), action, str(self.root), record['job_id'],
            '--deadline', str(record['dns_deadline']), '--propagation-seconds', str(self.dns_propagation_seconds)])


def write_fixture_hook(path, *, port, ca, events):
    # Source pinning precedes sys.path injection in the independent hook process.
    members = ('app/certificates/dns_hook.py', 'app/certificates/dns_journal.py',
        'app/certificates/cloudflare.py', 'app/certificates/dns_store.py',
        'app/certificates/errors.py', 'app/data_owner.py', 'tests/fake_cloudflare_https.py')
    pins = {str(ROOT / name): hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in members}
    pins[str(ca)] = hashlib.sha256(Path(ca).read_bytes()).hexdigest()
    script = '''import hashlib,json,os,stat,sys
PINS = __PINS__
for path, expected in PINS.items():
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>1048576:
            raise SystemExit('fixture source unsafe')
        with os.fdopen(os.dup(fd),'rb') as stream: raw=stream.read(1048577)
        if hashlib.sha256(raw).hexdigest()!=expected:raise SystemExit('fixture source changed')
    finally:os.close(fd)
sys.path.insert(0,__SOURCE__)
sys.path.insert(0,__TESTS__)
from fake_cloudflare_https import LoopbackCloudflareTransport
from app.certificates import cloudflare,dns_hook
from app.certificates.cloudflare import CloudflareClient
from app.certificates.dns_journal import DNSChallengeJournal
def forbidden(*args,**kwargs):raise AssertionError('real Cloudflare transport forbidden')
cloudflare._https=forbidden
def factory(root):
    return DNSChallengeJournal(root,client_factory=lambda token:CloudflareClient(token,transport=LoopbackCloudflareTransport(__PORT__,__CA__)))
if sys.argv[1]=='auth':
    keys=[key for key in ('CERTBOT_IDENTIFIER','CERTBOT_DOMAIN') if key in os.environ]
    valid=bool(keys) and all(os.environ[key].removeprefix('*.')==__DOMAIN__ for key in keys)
    fd=os.open(__EVENTS__,os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o600:
            raise SystemExit('fixture event file unsafe')
        os.write(fd,(json.dumps({'action':'auth','identifier_keys':keys,'identifier_valid':valid})+'\\n').encode())
        os.fsync(fd)
    finally:os.close(fd)
raise SystemExit(dns_hook.main(sys.argv[1:],journal_factory=factory,wait_seconds=0))
'''
    for key, value in {'__PINS__': repr(pins), '__SOURCE__': repr(str(ROOT)), '__TESTS__': repr(str(ROOT / 'tests')),
        '__PORT__': repr(port), '__CA__': repr(str(ca)), '__EVENTS__': repr(str(events)), '__DOMAIN__': repr(DOMAIN)}.items():
        script = script.replace(key, value)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream: stream.write(script)
    return path


@unittest.skipUnless(os.getenv('VUI_TEST_PEBBLE') and os.getenv('VUI_TEST_DNS_ACME') == '1', 'explicit real DNS ACME gate')
class RealDNSACMETests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(importlib.metadata.version('certbot'), '5.8.0')
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-dns-acme-real-'))).resolve()
        self.root.chmod(0o700); self.data = self.root / 'data'; self.data.mkdir(mode=0o700)
        engine = create_engine(f'sqlite:///{self.data / "v-ui.db"}', poolclass=NullPool)
        self.stack.callback(engine.dispose)
        self.stack.enter_context(patch.multiple(database, engine=engine,
            SessionLocal=sessionmaker(bind=engine, autoflush=False)))
        database.init_db()
        self.state = FakeCloudflareState(FAKE_TOKEN)
        self.answers = CloudflareDNSAnswers(self.state)
        self.pebble = PebbleFixture(self.stack, self.root, self.root / 'unused-http-webroot',
            domain=DOMAIN, authz_reuse_percent=100, dns_answers=self.answers)
        cf_ca, cf_cert, cf_key = certificate_files(self.root, 'independent-fake-cf')
        self.cf_ca = cf_ca; self.transport_ca = cf_ca
        self.cf_port = start_cloudflare_https(self.stack, self.state, cf_cert, cf_key)
        self.events = self.root / 'hook-events.jsonl'
        managed = self.data / 'certificates'
        self.store = DNSCredentialStore(managed)
        self.profile = self.store.create(label='Synthetic CA acceptance', zone_name=ZONE_NAME,
                                        zone_id=ZONE_ID, api_token=FAKE_TOKEN)
        self.journal = DNSChallengeJournal(managed, client_factory=lambda token: CloudflareClient(token,
            transport=LoopbackCloudflareTransport(self.cf_port, self.transport_ca)))
        self.provider = FixtureHookProvider(managed, test_directory=self.pebble.directory, test_ca=self.pebble.ca,
                                            dns_journal=self.journal, dns_propagation_seconds=30)
        self.provider.set_hook(write_fixture_hook(self.root / 'trusted-dns-hook.py',
            port=self.cf_port, ca=self.transport_ca, events=self.events))
        self.manager = CertificateManager(managed, self.provider, trusted_roots=self.pebble.root_pem)
        self.stack.enter_context(patch.object(cloudflare, '_https', side_effect=AssertionError('real CF transport forbidden')))
        self.csrs = []; original = manager_module.make_csr
        def tracked(*args, **kwargs):
            result = original(*args, **kwargs); self.csrs.append(result[1]); return result
        self.stack.enter_context(patch.object(manager_module, 'make_csr', side_effect=tracked))

    def create(self):
        return self.manager.create(DOMAIN, 'admin@example.test', 'production', True, True,
                                   method='dns-01', wildcard=True, credential_id=self.profile['id'])

    def process(self, job, *, success=True):
        self.assertTrue(self.manager.process_once())
        result = self.manager.job(job['id'])
        self.assertEqual(result['state'], 'succeeded' if success else 'failed',
                         'Pinned DNS ACME result: ' + str(result['state']) + '/' + str(result['error']))
        return result

    def material(self, identifier):
        value = self.manager.material(identifier)
        leaf = x509.load_pem_x509_certificate(value[0][0].read_bytes())
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        self.assertEqual(len(san), 2)
        self.assertTrue(all(isinstance(name, x509.DNSName) for name in san))
        self.assertEqual(set(san.get_values_for_type(x509.DNSName)), {DOMAIN, '*.' + DOMAIN})
        for raw in self.csrs:
            csr = x509.load_pem_x509_csr(raw)
            names = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            self.assertEqual(len(names), 2)
            self.assertEqual(set(names.get_values_for_type(x509.DNSName)), {DOMAIN, '*.' + DOMAIN})
        return value, leaf.serial_number

    def fresh_account(self, phase):
        source = self.manager.root / 'accounts' / 'production'
        retained = self.root / ('retained-account-' + phase)
        def fingerprint(root):
            return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in root.rglob('*') if p.is_file()}
        self.assertTrue(source.is_dir()); before = fingerprint(source)
        self.assertTrue(any(name.endswith('private_key.json') for name in before))
        source.rename(retained); self.assertTrue(fingerprint(retained) == before, 'Retained fake account bytes changed')
        with self.state.lock: self.state.phase = phase

    def due(self, identifier):
        with database.SessionLocal() as db:
            row = db.get(Certificate, identifier); row.renew_at = 0; row.last_attempt = 0; row.retry_at = 0; db.commit()

    def assert_cleanup(self):
        summary = self.state.summary()
        self.assertTrue(summary['unrelated_preserved']); self.assertEqual(summary['owned_remaining'], 0)
        self.assertEqual(set(self.state.created), set(self.state.deleted))
        self.assertNotIn(OTHER_ID, self.state.deleted)
        self.assertEqual(summary['protocol_errors'], 0)
        self.assertEqual(self.journal.pending(), [])
        return summary

    def assert_hook_environment(self, minimum):
        events = [json.loads(line) for line in self.events.read_text().splitlines()]
        self.assertGreaterEqual(len(events), minimum)
        self.assertTrue(all(event['identifier_valid'] and event['identifier_keys']
            and set(event['identifier_keys']) <= {'CERTBOT_IDENTIFIER', 'CERTBOT_DOMAIN'} for event in events))
        return sorted({name for event in events for name in event['identifier_keys']})

    def test_real_root_wildcard_and_fresh_account_renewal(self):
        job = self.create(); self.process(job); identifier = job['certificate_id']
        first, serial = self.material(identifier); old_chain = first[0][0].read_bytes()
        self.fresh_account('renewal'); self.due(identifier)
        renewed = self.manager.renew(identifier); self.process(renewed)
        second, next_serial = self.material(identifier)
        self.assertNotEqual(first[2], second[2]); self.assertNotEqual(serial, next_serial)
        self.assertEqual(first[0][0].read_bytes(), old_chain)
        summary = self.assert_cleanup()
        self.assertEqual(summary['max_owned_same_owner'], 2); self.assertEqual(summary['created'], 4)
        # Pinned Pebble 2.10.1 deliberately sets dnsClient.Net="tcp" for a
        # custom resolver (official va/va.go:128-132); it never tries UDP.
        # https://github.com/letsencrypt/pebble/blob/v2.10.1/va/va.go#L128-L132
        self.assertEqual(summary['udp_queries'], 0)
        self.assertGreaterEqual(summary['tcp_queries'], 4)
        self.assertGreaterEqual(summary['queries_by_phase'].get('issue', 0), 2)
        self.assertGreater(summary['returned_owned'], 0)
        self.assertGreaterEqual(summary['queries_by_phase'].get('renewal', 0), 2)
        keys = self.assert_hook_environment(4)
        print('Pinned DNS ACME: exact root/wildcard CSR+SAN; two own TXT coexist; real fixed-CA TCP DNS; '
              'fresh-account renewal; only own IDs cleaned; hook identifiers=' + ','.join(keys))

    def test_nonpropagation_failure_retains_previous_material(self):
        job = self.create(); self.process(job); identifier = job['certificate_id']
        first, _ = self.material(identifier); old_chain = first[0][0].read_bytes()
        self.fresh_account('no-propagation'); self.due(identifier)
        with self.state.lock: self.state.mode = 'no-propagation'
        renewed = self.manager.renew(identifier); result = self.process(renewed, success=False)
        self.assertEqual(result['error'], 'ACME_VALIDATION_FAILED')
        unchanged, _ = self.material(identifier)
        self.assertEqual(first[2], unchanged[2]); self.assertEqual(first[0][0].read_bytes(), old_chain)
        self.assertEqual(len(list((self.manager.root / 'revisions' / identifier).iterdir())), 1)
        self.assertGreater(self.state.summary()['queries_by_phase'].get('no-propagation', 0), 0)
        self.assert_cleanup(); self.assert_hook_environment(4)
        print('Pinned DNS ACME: withheld DNS answers rejected by real CA; prior revision retained; own TXT cleaned')

    def test_wrong_fake_cf_tls_ca_never_publishes_material(self):
        # ACME HTTPS still trusts Pebble correctly. Only the independent CF
        # HTTPS channel gets an unrelated CA, so it must fail before TXT writes.
        self.transport_ca = self.pebble.ca
        self.provider.set_hook(write_fixture_hook(self.root / 'wrong-ca-dns-hook.py',
            port=self.cf_port, ca=self.transport_ca, events=self.events))
        job = self.create(); result = self.process(job, success=False)
        self.assertEqual(result['error'], 'DNS_PROVIDER_TLS_FAILED')
        with database.SessionLocal() as db: self.assertIsNone(db.get(Certificate, job['certificate_id']).revision)
        self.assertFalse((self.manager.root / 'revisions' / job['certificate_id']).exists())
        self.assertEqual(self.state.summary()['created'], 0); self.assertEqual(self.state.summary()['http_requests'], 0)
        self.assertTrue(self.state.summary()['unrelated_preserved'])
        self.assert_hook_environment(1)
        # The next real Certbot attempt gets working CF TLS, but an intentionally
        # ambiguous POST response after the fixture persisted the own record.
        # No error path may publish material even if cleanup later succeeds.
        self.fresh_account('write-unknown'); self.due(job['certificate_id'])
        self.transport_ca = self.cf_ca
        self.provider.set_hook(write_fixture_hook(self.root / 'unknown-write-dns-hook.py',
            port=self.cf_port, ca=self.transport_ca, events=self.events))
        with self.state.lock: self.state.mode = 'write-unknown'
        next_job = self.manager.renew(job['certificate_id']); unknown = self.process(next_job, success=False)
        self.assertEqual(unknown['error'], 'DNS_PROVIDER_WRITE_UNKNOWN')
        with database.SessionLocal() as db: self.assertIsNone(db.get(Certificate, job['certificate_id']).revision)
        self.assertFalse((self.manager.root / 'revisions' / job['certificate_id']).exists())
        summary = self.assert_cleanup()
        self.assertGreater(summary['created'], 0)
        self.assertEqual(summary['unique_ownership_posts'], summary['created'], 'Ambiguous POST must not be retried')
        self.assert_hook_environment(2)
        print('Pinned DNS ACME: wrong CF TLS had zero writes; ambiguous POST was not retried; own IDs cleaned; no material promoted')


@unittest.skipUnless(os.getenv('VUI_TEST_LEGACY22_SOURCE'), 'explicit immutable public v0.4.22 source gate')
class Legacy22DNSReaderTests(unittest.TestCase):
    def test_real_published_22_reader_refuses_new_dns_backup_before_replacement(self):
        source = Path(os.environ['VUI_TEST_LEGACY22_SOURCE']).resolve()
        self.assertEqual(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip(), LEGACY22_SHA)
        path = source / 'app' / 'release_tools.py'
        expected = subprocess.check_output(['git', 'show', LEGACY22_SHA + ':app/release_tools.py'], cwd=source)
        self.assertFalse(path.is_symlink()); self.assertTrue(path.read_bytes() == expected, 'Immutable legacy reader differs')
        spec = importlib.util.spec_from_file_location('vui_real_published_22_reader', path)
        legacy = importlib.util.module_from_spec(spec); spec.loader.exec_module(legacy)
        with tempfile.TemporaryDirectory(prefix='vui-real-old-reader-') as folder:
            root = Path(folder).resolve(); root.chmod(0o700); data = root / 'data'; data.mkdir(mode=0o700)
            with sqlite3.connect(data / 'v-ui.db') as db:
                db.executescript('''CREATE TABLE users(id INTEGER); CREATE TABLE inbounds(id INTEGER);
                    CREATE TABLE admin_sessions(token_hash TEXT); CREATE TABLE subscription_grants(revoked INTEGER);
                    INSERT INTO users VALUES(1); INSERT INTO inbounds VALUES(1); INSERT INTO admin_sessions VALUES('fake');
                    INSERT INTO subscription_grants VALUES(0);
                    CREATE TABLE managed_certificates(id TEXT,domain TEXT,environment TEXT,method TEXT,wildcard INTEGER,credential_id TEXT);
                    CREATE TABLE certificate_bindings(server_name TEXT); CREATE TABLE certificate_jobs(id TEXT);
                    CREATE TABLE certificate_identity_schema(version INTEGER NOT NULL); INSERT INTO certificate_identity_schema VALUES(1);''')
            store = DNSCredentialStore(data / 'certificates')
            store.create(label='Private synthetic backup', zone_name=ZONE_NAME, zone_id=ZONE_ID, api_token=FAKE_TOKEN)
            marker = data / 'business-marker'; marker.write_bytes(b'unchanged private synthetic business data')
            archive = root / 'dns-backup.zip'; checksum = release_tools.backup(root, archive)
            with zipfile.ZipFile(archive) as zipped:
                self.assertEqual(json.loads(zipped.read(release_tools.MANIFEST))['kind'], 'backup-dns01-v1')
            before = {p.relative_to(data).as_posix(): p.read_bytes() for p in data.rglob('*') if p.is_file()}
            observed = []; original = legacy.unpack_verified
            def tracked(*args, **kwargs):
                value = original(*args, **kwargs); observed.append(value['kind']); return value
            with patch.object(legacy, 'unpack_verified', side_effect=tracked):
                with self.assertRaises(legacy.ReleaseError): legacy._restore_locked(legacy._StoppedGuard(root), archive, checksum)
            self.assertEqual(observed, ['backup-dns01-v1'])
            self.assertTrue(before == {p.relative_to(data).as_posix(): p.read_bytes() for p in data.rglob('*') if p.is_file()},
                            'Legacy reader modified private fixture data before refusing the format')
            self.assertFalse((root / 'RESTORE_PENDING.json').exists()); self.assertFalse((root / 'recovery').exists())
        print('Immutable public v0.4.22 reader: actual new DNS backup kind rejected before replacing any source data')


if __name__ == '__main__':
    unittest.main()
