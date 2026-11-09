"""Certificate identity/SAN contracts with synthetic SQLite and a private test CA only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import ipaddress
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from sqlalchemy import CheckConstraint, create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.models import database
from app.certificates.models import Certificate, CertificateBinding
from app.certificates.material import (CertificateError, certificate_names, covers_domain,
                                       make_csr, validate_material)
from app.certificates.schema import CertificateIdentitySchemaError


class CertificateIdentityMigrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-certificate-identity-')))
        self.path = self.root / 'v-ui.db'
        self.engine = create_engine(f'sqlite:///{self.path}', poolclass=NullPool)
        self.stack.callback(self.engine.dispose)
        self.stack.enter_context(patch.object(database, 'engine', self.engine))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('No runtime processes')))
        self.stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('No network')))
        self.stack.enter_context(patch('socket.getaddrinfo', side_effect=AssertionError('No DNS')))
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)

    def reset(self):
        self.engine.dispose()
        if self.path.exists(): self.path.unlink()

    def legacy(self, *, unique=True):
        with sqlite3.connect(self.path) as db:
            db.executescript('''
                CREATE TABLE managed_certificates (
                    id VARCHAR(32) NOT NULL PRIMARY KEY, domain VARCHAR(253) NOT NULL,
                    email VARCHAR(254) NOT NULL, environment VARCHAR(16) NOT NULL,
                    auto_renew BOOLEAN NOT NULL, revision VARCHAR(64), not_before INTEGER, not_after INTEGER,
                    renew_at INTEGER NOT NULL, retry_at INTEGER NOT NULL, last_attempt INTEGER NOT NULL,
                    failures INTEGER NOT NULL, error VARCHAR(64), created_at INTEGER NOT NULL
                    ''' + (', UNIQUE(domain, environment)' if unique else '') + ''');
                CREATE TABLE certificate_jobs (
                    id VARCHAR(32) NOT NULL PRIMARY KEY, certificate_id VARCHAR(32) NOT NULL,
                    sequence INTEGER NOT NULL, state VARCHAR(16) NOT NULL, error VARCHAR(64),
                    created_at INTEGER NOT NULL, started_at INTEGER, finished_at INTEGER);
                CREATE INDEX ix_certificate_jobs_certificate_id ON certificate_jobs(certificate_id);
                CREATE INDEX ix_certificate_jobs_sequence ON certificate_jobs(sequence);
                CREATE TABLE certificate_bindings (
                    target VARCHAR(64) NOT NULL PRIMARY KEY, certificate_id VARCHAR(32) NOT NULL,
                    applied_certificate_id VARCHAR(32), applied_revision VARCHAR(64), error VARCHAR(64));
                CREATE INDEX ix_certificate_bindings_certificate_id ON certificate_bindings(certificate_id);
            ''')
            db.execute('INSERT INTO managed_certificates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('a' * 32, 'panel.example.test', 'fixture@example.test', 'production', 0,
                 'b' * 64, 123, 456, 345, 777, 222, 3, 'RENEWAL_FAILED', 111))
            db.execute('INSERT INTO certificate_bindings VALUES (?,?,?,?,?)',
                ('panel', 'a' * 32, 'a' * 32, 'b' * 64, 'PENDING_APPLY'))
            db.execute('INSERT INTO certificate_jobs VALUES (?,?,?,?,?,?,?,?)',
                ('c' * 32, 'a' * 32, 17, 'failed', 'ACME_VALIDATION_FAILED', 9, 10, 11))

    def rows(self, table):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY 1')]

    def test_legacy_atomic_migration_preserves_every_old_value_binding_job_and_id(self):
        self.legacy()
        before = {name: self.rows(name) for name in ('managed_certificates', 'certificate_bindings', 'certificate_jobs')}
        database.init_db()
        row = self.rows('managed_certificates')[0]
        self.assertEqual({key: row[key] for key in before['managed_certificates'][0]}, before['managed_certificates'][0])
        self.assertEqual((row['method'], row['wildcard'], row['credential_id']), ('http-01', 0, None))
        binding = self.rows('certificate_bindings')[0]
        self.assertIsNone(binding.pop('server_name'))
        self.assertEqual(binding, before['certificate_bindings'][0])
        self.assertEqual(self.rows('certificate_jobs'), before['certificate_jobs'])
        self.assertEqual(self.rows('certificate_identity_schema'), [{'version': 1}])
        snapshot = {name: self.rows(name) for name in ('managed_certificates', 'certificate_bindings', 'certificate_jobs')}
        for _ in range(3): database.init_db()
        self.assertEqual({name: self.rows(name) for name in snapshot}, snapshot)

    def test_new_database_and_unmarked_empty_create_all_initialize_once(self):
        for create_all in (False, True):
            with self.subTest(create_all=create_all):
                self.reset()
                if create_all: database.Base.metadata.create_all(self.engine)
                database.init_db(); database.init_db()
                self.assertEqual(self.rows('certificate_identity_schema'), [{'version': 1}])
                self.assertEqual(self.rows('managed_certificates'), [])

    def test_same_root_http_dns_and_wildcard_coexist_but_identity_duplicates_fail(self):
        database.init_db()
        for identifier, method, wildcard in [('1', 'http-01', False), ('2', 'dns-01', False), ('3', 'dns-01', True)]:
            with self.sessions() as db:
                db.add(Certificate(id=identifier * 32, domain='example.test', email='fixture@example.test',
                    environment='production', method=method, wildcard=wildcard,
                    credential_id=None if method == 'http-01' else 'd' * 32, created_at=1))
                db.commit()
        self.assertEqual(len(self.rows('managed_certificates')), 3)
        with self.sessions() as db:
            db.add(Certificate(id='4' * 32, domain='example.test', email='fixture@example.test',
                environment='production', method='dns-01', wildcard=True, credential_id='e' * 32, created_at=2))
            with self.assertRaises(IntegrityError): db.commit()
        database.init_db()
        with self.sessions() as db:
            row = db.get(Certificate, '1' * 32)
            self.assertEqual((row.method, row.wildcard, row.credential_id), ('http-01', False, None))
            for field, value in [('method', 'dns-01'), ('domain', 'other.example.test'),
                                 ('environment', 'staging'), ('wildcard', True)]:
                with self.subTest(field=field), self.assertRaisesRegex(CertificateError, 'CERTIFICATE_IDENTITY_IMMUTABLE'):
                    setattr(row, field, value)
            row.domain = 'EXAMPLE.TEST.'  # The same normalized identity is unchanged.
            dns = db.get(Certificate, '2' * 32)
            with self.assertRaisesRegex(CertificateError, 'CERTIFICATE_IDENTITY_IMMUTABLE'):
                dns.credential_id = 'e' * 32
            dns.credential_id = 'd' * 32
            with self.assertRaisesRegex(CertificateError, 'CERTIFICATE_IDENTITY_IMMUTABLE'):
                row.credential_id = 'd' * 32
        for value in (0, 1, 'false', None):
            with self.subTest(wildcard=value), self.assertRaises(CertificateError): Certificate(wildcard=value)
        for value in ('short', 'A' * 32, 1):
            with self.subTest(credential=value), self.assertRaises(CertificateError): Certificate(credential_id=value)

    def test_old_insert_defaults_and_literal_binding_name(self):
        database.init_db()
        with self.engine.begin() as db:
            db.execute(text('''INSERT INTO managed_certificates
                (id,domain,email,environment,auto_renew,renew_at,retry_at,last_attempt,failures,created_at)
                VALUES (:id,'example.test','fixture@example.test','production',0,0,0,0,0,1)'''), {'id': 'a' * 32})
        with self.sessions() as db:
            row = db.get(Certificate, 'a' * 32)
            self.assertEqual((row.method, row.wildcard, row.credential_id), ('http-01', False, None))
            binding = CertificateBinding(target='inbound:1', certificate_id=row.id, server_name='NODE.Example.Test.')
            db.add(binding); db.commit()
            self.assertEqual(binding.server_name, 'node.example.test')
            with self.assertRaises(CertificateError): binding.server_name = '*.example.test'
        database.init_db()
        self.assertEqual(self.rows('certificate_bindings')[0]['server_name'], 'node.example.test')

    def test_failure_after_table_replacement_binding_or_marker_rolls_back_all_bytes(self):
        for boundary in ('DROP TABLE managed_certificates', 'ALTER TABLE certificate_bindings',
                         'INSERT INTO certificate_identity_schema'):
            with self.subTest(boundary=boundary):
                self.reset(); self.legacy(); before = self.path.read_bytes()
                def fail(conn, cursor, statement, parameters, context, executemany):
                    if statement.startswith(boundary): raise OSError('synthetic certificate migration failure')
                event.listen(self.engine, 'after_cursor_execute', fail)
                try:
                    with self.assertRaises(OSError): database.init_db()
                finally: event.remove(self.engine, 'after_cursor_execute', fail)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertNotIn('certificate_identity_schema', inspect(self.engine).get_table_names())
                database.init_db()
                self.assertEqual(self.rows('managed_certificates')[0]['id'], 'a' * 32)

    def test_concurrent_initializers_preserve_one_stable_fenced_identity(self):
        self.legacy()
        with ThreadPoolExecutor(max_workers=4) as workers:
            for result in [workers.submit(database.init_db) for _ in range(4)]: result.result(timeout=20)
        self.assertEqual(self.rows('certificate_identity_schema'), [{'version': 1}])
        self.assertEqual(self.rows('managed_certificates')[0]['id'], 'a' * 32)
        self.assertEqual(self.rows('certificate_jobs')[0]['sequence'], 17)

    def test_marker_field_loss_duplicate_wrong_type_or_version_refuses_before_create_all(self):
        changes = ('ALTER TABLE managed_certificates RENAME COLUMN method TO missing_method',
            'ALTER TABLE certificate_bindings DROP COLUMN server_name', 'DROP TABLE certificate_jobs',
            'UPDATE certificate_identity_schema SET version=2', 'DELETE FROM certificate_identity_schema',
            'INSERT INTO certificate_identity_schema VALUES (1)',
            'ALTER TABLE certificate_identity_schema ADD COLUMN unknown TEXT',
            'DROP TABLE certificate_identity_schema; CREATE TABLE certificate_identity_schema(version TEXT NOT NULL);'
            "INSERT INTO certificate_identity_schema VALUES ('1')")
        for change in changes:
            with self.subTest(change=change):
                self.reset(); self.legacy(); database.init_db()
                with sqlite3.connect(self.path) as db: db.executescript(change)
                before = self.path.read_bytes()
                with self.assertRaises(CertificateIdentitySchemaError): database.init_db()
                self.assertEqual(self.path.read_bytes(), before)

    def test_unmarked_populated_new_schema_cannot_be_relabelled_as_legacy(self):
        self.legacy(); database.init_db()
        with sqlite3.connect(self.path) as db: db.execute('DROP TABLE certificate_identity_schema')
        before = self.path.read_bytes()
        with self.assertRaises(CertificateIdentitySchemaError): database.init_db()
        self.assertEqual(self.path.read_bytes(), before)

    def test_same_named_but_changed_check_expressions_do_not_pass_the_schema_fence(self):
        constraints = [item for item in Certificate.__table__.constraints if isinstance(item, CheckConstraint)]
        for constraint in constraints:
            for replacement in ('1=1', '0=1'):
                with self.subTest(constraint=constraint.name, replacement=replacement):
                    self.reset(); self.legacy(); database.init_db()
                    with sqlite3.connect(self.path) as db:
                        sql = db.execute("SELECT sql FROM sqlite_master WHERE name='managed_certificates'").fetchone()[0]
                        original = 'CONSTRAINT ' + constraint.name + ' CHECK (' + str(constraint.sqltext) + ')'
                        self.assertIn(original, sql)
                        changed = sql.replace(original, 'CONSTRAINT ' + constraint.name + ' CHECK (' + replacement + ')', 1)
                        db.execute('PRAGMA writable_schema=ON')
                        db.execute("UPDATE sqlite_master SET sql=? WHERE name='managed_certificates'", (changed,))
                        version = db.execute('PRAGMA schema_version').fetchone()[0]
                        db.execute('PRAGMA schema_version=' + str(version + 1))
                        db.execute('PRAGMA writable_schema=OFF')
                    before = self.path.read_bytes()
                    with self.assertRaises(CertificateIdentitySchemaError): database.init_db()
                    self.assertEqual(self.path.read_bytes(), before)

    def test_unknown_columns_triggers_indexes_or_foreign_keys_are_not_dropped(self):
        changes = ('ALTER TABLE managed_certificates ADD COLUMN unknown TEXT',
            'CREATE INDEX operator_index ON managed_certificates(email)',
            'CREATE TRIGGER operator_trigger AFTER UPDATE ON managed_certificates BEGIN SELECT 1; END',
            'CREATE TABLE operator_reference (id INTEGER, certificate_id VARCHAR(32) REFERENCES managed_certificates(id) ON DELETE CASCADE)')
        for change in changes:
            with self.subTest(change=change):
                self.reset(); self.legacy()
                with sqlite3.connect(self.path) as db: db.execute(change)
                before = self.path.read_bytes()
                with self.assertRaises(CertificateIdentitySchemaError): database.init_db()
                self.assertEqual(self.path.read_bytes(), before)

    def test_duplicate_or_corrupt_legacy_identity_is_rejected_without_losing_any_row(self):
        self.legacy(unique=False)
        with sqlite3.connect(self.path) as db:
            db.execute('''INSERT INTO managed_certificates SELECT ?,domain,email,environment,auto_renew,revision,
                not_before,not_after,renew_at,retry_at,last_attempt,failures,error,created_at FROM managed_certificates''', ('d' * 32,))
        before = self.path.read_bytes()
        with self.assertRaises(CertificateIdentitySchemaError): database.init_db()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.rows('managed_certificates')), 2)
        self.reset(); self.legacy(); database.init_db()
        with sqlite3.connect(self.path) as db: db.execute("UPDATE managed_certificates SET domain='*.example.test'")
        before = self.path.read_bytes()
        with self.assertRaises(CertificateIdentitySchemaError): database.init_db()
        self.assertEqual(self.path.read_bytes(), before)


class CertificateNamesAndMaterialTests(unittest.TestCase):
    domain = 'example.test'

    @classmethod
    def setUpClass(cls):
        cls.ca_key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Private synthetic identity CA')])
        now = datetime.now(timezone.utc)
        cls.ca = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(cls.ca_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(cls.ca_key.public_key()), False)
            .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), True)
            .sign(cls.ca_key, hashes.SHA256()))
        cls.roots = cls.ca.public_bytes(serialization.Encoding.PEM)

    def leaf(self, names, *, expired=False):
        key = ec.generate_private_key(ec.SECP256R1()); now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, self.domain)]))
            .issuer_name(self.ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=2 if expired else 1))
            .not_valid_after(now - timedelta(days=1) if expired else now + timedelta(days=10))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(x509.SubjectAlternativeName(names), False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self.ca_key.public_key()), False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
            .sign(self.ca_key, hashes.SHA256()))
        return (cert.public_bytes(serialization.Encoding.PEM) + self.roots,
                key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))

    def test_canonical_names_csr_shapes_and_legacy_signature(self):
        self.assertEqual(certificate_names('EXAMPLE.TEST.'), ['example.test'])
        self.assertEqual(certificate_names('example.test', True), ['example.test', '*.example.test'])
        self.assertEqual(certificate_names('bücher.example.test'), ['xn--bcher-kva.example.test'])
        for value in (0, 1, 'true', None):
            with self.subTest(wildcard=value), self.assertRaises(CertificateError): certificate_names(self.domain, value)
        for names in (None, [self.domain], ['*.' + self.domain, self.domain]):
            _, csr = make_csr(self.domain, names=names)
            actual = x509.load_pem_x509_csr(csr).extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            self.assertEqual(actual.get_values_for_type(x509.DNSName), certificate_names(self.domain, names is not None and len(names) == 2))
        for names in ([self.domain, self.domain], ['*.' + self.domain], [self.domain, 'other.example.test'], 'example.test'):
            with self.subTest(names=names), self.assertRaises(CertificateError): make_csr(self.domain, names=names)

    def test_literal_hosts_and_exactly_one_wildcard_label(self):
        names = certificate_names(self.domain, True)
        for host in (self.domain, 'node.example.test', 'NODE.EXAMPLE.TEST.'):
            self.assertTrue(covers_domain(names, host), host)
        for host in ('a.b.example.test', '*.example.test', 'example.test.evil.test', 'badexample.test',
                     '127.0.0.1', 'https://node.example.test', ' node.example.test', ''):
            self.assertFalse(covers_domain(names, host), host)
        self.assertFalse(covers_domain([self.domain], 'node.example.test'))
        self.assertFalse(covers_domain(['*.*.example.test'], 'node.example.test'))

    def test_exact_single_and_wildcard_sans_preserve_production_chain_verification(self):
        for wildcard in (False, True):
            expected = certificate_names(self.domain, wildcard)
            chain, key = self.leaf([x509.DNSName(name) for name in reversed(expected)])
            kwargs = {'expected_names': expected} if wildcard else {}
            result = validate_material(chain, key, self.domain, trusted_roots=self.roots, **kwargs)
            self.assertLess(result['not_before'], result['renew_at']); self.assertLess(result['renew_at'], result['not_after'])

    def test_duplicate_extra_missing_and_non_dns_sans_are_rejected_even_for_staging(self):
        expected = certificate_names(self.domain, True)
        bad = [[self.domain], ['*.' + self.domain], [self.domain, self.domain, '*.' + self.domain],
            [self.domain.upper(), self.domain, '*.' + self.domain],
            [self.domain, '*.' + self.domain, 'other.example.test']]
        for names in bad:
            chain, key = self.leaf([x509.DNSName(name) for name in names])
            with self.subTest(names=names), self.assertRaisesRegex(CertificateError, 'DOMAIN_MISMATCH'):
                validate_material(chain, key, self.domain, expected_names=expected, verify_chain=False)
        chain, key = self.leaf([x509.DNSName(self.domain), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))])
        with self.assertRaisesRegex(CertificateError, 'DOMAIN_MISMATCH'):
            validate_material(chain, key, self.domain, verify_chain=False)

    def test_wrong_key_expired_material_and_untrusted_ca_still_fail(self):
        names = certificate_names(self.domain, True)
        chain, key = self.leaf([x509.DNSName(name) for name in names])
        other, _ = make_csr(self.domain)
        with self.assertRaisesRegex(CertificateError, 'KEY_MISMATCH'):
            validate_material(chain, other, self.domain, expected_names=names, verify_chain=False)
        chain, key = self.leaf([x509.DNSName(name) for name in names], expired=True)
        with self.assertRaisesRegex(CertificateError, 'CERTIFICATE_EXPIRED_OR_NOT_YET_VALID'):
            validate_material(chain, key, self.domain, expected_names=names, verify_chain=False)
        chain, key = self.leaf([x509.DNSName(name) for name in names])
        with self.assertRaisesRegex(CertificateError, 'CERTIFICATE_CA_STORE_UNAVAILABLE'):
            validate_material(chain, key, self.domain, expected_names=names, trusted_roots=b'')
        other_key = ec.generate_private_key(ec.SECP256R1())
        other_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Unrelated private test CA')])
        now = datetime.now(timezone.utc)
        other_ca = (x509.CertificateBuilder().subject_name(other_name).issuer_name(other_name)
            .public_key(other_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(other_key.public_key()), False)
            .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), True)
            .sign(other_key, hashes.SHA256()))
        with self.assertRaisesRegex(CertificateError, 'CERTIFICATE_CHAIN_VERIFICATION_FAILED'):
            validate_material(chain, key, self.domain, expected_names=names,
                              trusted_roots=other_ca.public_bytes(serialization.Encoding.PEM))


if __name__ == '__main__':
    unittest.main()
