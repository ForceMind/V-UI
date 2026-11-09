"""Stdlib-only private-file/SQLite and tiny release-package fixtures; no live services."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app import release_tools as tools
from app.certificates.dns_backup import (DNSBackupError, validate_dns_snapshot, require_dns_release,
                                         prepare_dns_staging, suspend_restored_dns)
from app.certificates.dns_journal import DNSChallengeJournal
from app.certificates.dns_store import DNSCredentialStore


class DNSBackupTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-dns-backup-'))).resolve()
        self.data = self.root / 'data'; self.data.mkdir(mode=0o700)
        self.stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('No network')))
        self.stack.enter_context(patch('socket.getaddrinfo', side_effect=AssertionError('No DNS network')))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('No runtime')))
        self.stack.enter_context(patch.object(tools, 'target_key', return_value='x86_64-gnu'))
        self.database(self.data)
        self.store = DNSCredentialStore(self.data / 'certificates')

    def database(self, data):
        with sqlite3.connect(data / 'v-ui.db') as db:
            db.executescript('''
                CREATE TABLE users(id INTEGER, username TEXT);
                CREATE TABLE inbounds(id INTEGER, remark TEXT);
                CREATE TABLE admin_sessions(token_hash TEXT);
                CREATE TABLE subscription_grants(revoked INTEGER);
                CREATE TABLE maintenance_grants(id INTEGER);
                CREATE TABLE maintenance_sessions(id INTEGER);
                INSERT INTO users VALUES(1,'synthetic'); INSERT INTO inbounds VALUES(1,'preserved');
                INSERT INTO admin_sessions VALUES('fake-session'); INSERT INTO subscription_grants VALUES(0);
                INSERT INTO maintenance_grants VALUES(1); INSERT INTO maintenance_sessions VALUES(1);
                CREATE TABLE managed_certificates(id TEXT PRIMARY KEY, domain TEXT, environment TEXT,
                    method TEXT, wildcard INTEGER, credential_id TEXT, auto_renew INTEGER);
                CREATE TABLE certificate_bindings(target TEXT, certificate_id TEXT, applied_certificate_id TEXT,
                    applied_revision TEXT, error TEXT, server_name TEXT);
                CREATE TABLE certificate_jobs(id TEXT);
                CREATE TABLE certificate_identity_schema(version INTEGER NOT NULL);
                INSERT INTO certificate_identity_schema VALUES(1);
            ''')
        (data / 'routing.json').write_text('preserved fixture')

    def profile(self):
        return self.store.create(label='Synthetic DNS', zone_name='example.test', zone_id='1' * 32,
                                 api_token='fake_private_DNS_token_' + 'x' * 32)

    def certificate(self, profile, *, method='dns-01', wildcard=1, credential=None):
        with sqlite3.connect(self.data / 'v-ui.db') as db:
            db.execute('INSERT INTO managed_certificates VALUES (?,?,?,?,?,?,?)',
                ('a' * 32, 'node.example.test', 'production', method, wildcard,
                 profile['id'] if credential is None else credential, 1))

    def profile_with_journal(self):
        profile = self.profile(); self.certificate(profile)
        frozen = self.store.snapshot(profile['id'])
        journal = DNSChallengeJournal(self.data / 'certificates')
        job = journal.begin('b' * 32, profile['id'], frozen['revision'], 'node.example.test',
                            ['node.example.test', '*.node.example.test'])
        return profile, frozen, journal, job

    def byte_tree(self, data=None):
        return {path.relative_to(data or self.data).as_posix(): path.read_bytes()
                for path in (data or self.data).rglob('*') if path.is_file()}

    def forged(self, kind, *, missing=None):
        copy = self.root / ('archive-source-' + str(len(list(self.root.glob('archive-source-*')))))
        shutil.copytree(self.data, copy, ignore=shutil.ignore_patterns('owner.lock'))
        if missing: (copy / missing).unlink()
        destination = self.root / (copy.name + '.zip')
        checksum = tools.create_archive(copy, destination,
            {'kind': kind, 'data_path': str(self.data), 'platform': tools.PLATFORM})
        return destination, checksum

    def prepared(self, identity, capability):
        source = self.root / ('source-' + identity); source.mkdir(mode=0o700)
        (source / 'main.py').write_text('raise AssertionError("candidate must never execute in pure tests")\n')
        if capability is not None:
            parent = source / 'app' / 'certificates'; parent.mkdir(parents=True, mode=0o700)
            (parent / 'schema.py').write_text(capability)
        archive = self.root / (identity + '.zip')
        checksum = tools.create_archive(source, archive, {'kind': 'release', 'platform': tools.PLATFORM,
            'release_id': identity, 'targets': ['x86_64-gnu']})
        release = self.root / 'releases' / identity; release.mkdir(parents=True, mode=0o700)
        tools.unpack_verified(archive, checksum, release / 'payload')
        runtime = release / 'runtime' / 'python' / 'bin'; runtime.mkdir(parents=True)
        executable = runtime / 'python3'; executable.write_text('#!/bin/sh\n'); executable.chmod(0o700)
        tools.atomic_json(release / 'READY.json', {'manifest_sha256': tools.digest((release / 'payload' / tools.MANIFEST).read_bytes()),
            'runtime_key': 'x86_64-gnu', 'runtime_tree_sha256': tools.runtime_tree_digest(release / 'runtime')})
        return release

    def test_legacy_without_dns_is_allowed_and_retains_legacy_backup_kind(self):
        self.assertFalse(validate_dns_snapshot(self.data))
        require_dns_release(self.data, self.root / 'nonexistent-old-payload')
        destination = self.root / 'legacy.zip'; tools.backup(self.root, destination)
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(json.loads(archive.read(tools.MANIFEST))['kind'], 'backup')
        with sqlite3.connect(self.data / 'v-ui.db') as db:
            db.executescript('DROP TABLE certificate_identity_schema; DROP TABLE managed_certificates;'
                            'DROP TABLE certificate_bindings; DROP TABLE certificate_jobs')
        self.assertFalse(validate_dns_snapshot(self.data))

    def test_every_profile_revision_and_journal_reference_form_one_read_only_closure(self):
        profile, frozen, journal, job = self.profile_with_journal()
        changed = self.store.replace(profile['id'], expected_revision=profile['revision'], api_token='y' * 40)
        journal.authorize_recovery(job['job_id'], job['revision'], changed['revision'], confirmed=True)
        before = self.byte_tree()
        self.assertTrue(validate_dns_snapshot(self.data))
        self.assertEqual(self.byte_tree(), before)
        old = self.store.root / profile['id'] / (frozen['revision'] + '.json'); old.unlink()
        with self.assertRaises(DNSBackupError): validate_dns_snapshot(self.data)

    def test_declared_dns_missing_profile_marker_fields_or_invalid_identity_is_rejected(self):
        profile = self.profile(); self.certificate(profile)
        path = self.data / 'v-ui.db'; baseline = path.read_bytes()
        for change in ("UPDATE managed_certificates SET credential_id='missing'",
            'UPDATE managed_certificates SET wildcard=2', "UPDATE managed_certificates SET method='unknown'",
            "UPDATE managed_certificates SET domain='outside.invalid'", 'UPDATE certificate_identity_schema SET version=2',
            'DROP TABLE certificate_identity_schema', 'ALTER TABLE managed_certificates DROP COLUMN wildcard'):
            with self.subTest(change=change):
                path.write_bytes(baseline)
                with sqlite3.connect(path) as db: db.execute(change)
                with self.assertRaises(DNSBackupError): validate_dns_snapshot(self.data)
        path.write_bytes(baseline)
        shutil.rmtree(self.store.root)
        with self.assertRaises(DNSBackupError): validate_dns_snapshot(self.data)

    def test_journal_wrong_provider_zone_profile_or_historical_revision_is_rejected(self):
        profile, frozen, journal, job = self.profile_with_journal()
        path = journal.root / (job['job_id'] + '.json'); original = path.read_bytes()
        for key, value in [('zone_id', '2' * 32), ('zone_name', 'other.test'), ('provider', 'other'),
                           ('credential_id', 'c' * 32), ('credential_revision', 'd' * 32),
                           ('recovery_credential_revision', 'e' * 32)]:
            with self.subTest(key=key):
                item = json.loads(original); item[key] = value
                path.write_text(json.dumps(item))
                with self.assertRaises(DNSBackupError): validate_dns_snapshot(self.data)
        path.write_bytes(original)
        with patch.object(DNSChallengeJournal, 'validate_for_backup', side_effect=RuntimeError('private fake failure')):
            with self.assertRaises(DNSBackupError) as caught: validate_dns_snapshot(self.data)
        self.assertNotIn('private fake failure', str(caught.exception))

    def test_backup_new_kind_preserves_all_private_bytes_and_excludes_transient_locks(self):
        profile, frozen, journal, job = self.profile_with_journal()
        fragment = self.store.root / profile['id'] / ('.write-' + 'e' * 32)
        fragment.write_bytes(b'{"incomplete":'); fragment.chmod(0o600)
        before = self.byte_tree(); destination = self.root / 'dns.zip'
        checksum = tools.backup(self.root, destination)
        self.assertEqual(checksum, tools.digest(destination.read_bytes()))
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        with zipfile.ZipFile(destination) as archive:
            meta = json.loads(archive.read(tools.MANIFEST))
            self.assertEqual((meta['schema'], meta['kind']), (1, 'backup-dns01-v1'))
            for name, raw in before.items():
                if name.startswith('certificates/dns-') and not name.endswith('owner.lock'):
                    self.assertEqual(archive.read(name), raw)
            self.assertFalse(any(name.endswith('owner.lock') for name in archive.namelist()))
        for name, raw in before.items(): self.assertEqual((self.data / name).read_bytes(), raw)

    def test_unpublished_pending_bytes_are_included_without_becoming_a_profile(self):
        profile = self.profile()
        pending = self.store.root / ('pending-' + 'f' * 32); pending.mkdir(mode=0o700)
        fragment = pending / ('.write-' + 'd' * 32); fragment.write_bytes(b''); fragment.chmod(0o600)
        before = fragment.read_bytes()
        self.assertEqual(set(self.store.validate_snapshot()), {profile['id']})
        destination = self.root / 'pending.zip'; tools.backup(self.root, destination)
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(archive.read(fragment.relative_to(self.data).as_posix()), before)

    def test_new_restore_disables_profiles_preserves_auto_renew_and_revokes_all_old_sessions(self):
        profile, frozen, journal, job = self.profile_with_journal()
        destination = self.root / 'restore-dns.zip'; checksum = tools.backup(self.root, destination)
        (self.data / 'routing.json').write_text('changed after backup')
        tools.restore(self.root, destination, checksum)
        restored = self.store.list()[0]
        self.assertFalse(restored['enabled']); self.assertTrue(restored['restore_pending'])
        self.assertEqual((self.data / 'routing.json').read_text(), 'preserved fixture')
        self.assertEqual(self.store.validate_snapshot()[profile['id']]['credential_revision'], frozen['revision'])
        with sqlite3.connect(self.data / 'v-ui.db') as db:
            self.assertEqual(db.execute('SELECT auto_renew FROM managed_certificates').fetchone()[0], 1)
            for table in ('admin_sessions', 'maintenance_grants', 'maintenance_sessions'):
                self.assertEqual(db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT revoked FROM subscription_grants').fetchone()[0], 1)
        self.assertTrue(validate_dns_snapshot(self.data))

    def test_restore_rejects_legacy_label_or_missing_secret_before_touching_existing_data(self):
        profile, frozen, journal, job = self.profile_with_journal()
        before = self.byte_tree()
        for kind, missing in [('backup', None), ('unknown-format', None), ('backup-dns01-v1',
                'certificates/dns-credentials/' + profile['id'] + '/' + frozen['revision'] + '.json')]:
            archive, checksum = self.forged(kind, missing=missing)
            with self.subTest(kind=kind), self.assertRaises(tools.ReleaseError): tools.restore(self.root, archive, checksum)
            for name, raw in before.items(): self.assertEqual((self.data / name).read_bytes(), raw)
            self.assertFalse((self.root / 'RESTORE_PENDING.json').exists())

    def test_failed_dns_restore_rename_keeps_original_authority_and_private_bytes(self):
        profile = self.profile(); self.certificate(profile)
        archive = self.root / 'rollback-dns.zip'; checksum = tools.backup(self.root, archive)
        (self.data / 'routing.json').write_text('latest original')
        before = self.byte_tree(); replace = os.replace
        def fail_incoming(source, destination, **kwargs):
            if '.restore-' in str(source) and Path(source).name == 'data':
                raise OSError('synthetic incoming data rename failure')
            return replace(source, destination, **kwargs)
        with patch.object(tools.os, 'replace', side_effect=fail_incoming):
            with self.assertRaises(tools.ReleaseError): tools.restore(self.root, archive, checksum)
        self.assertEqual(self.byte_tree(), before)
        self.assertTrue(self.store.list()[0]['enabled'])
        self.assertFalse(self.store.list()[0]['restore_pending'])
        self.assertFalse((self.root / 'RESTORE_PENDING.json').exists())

    def test_only_new_staging_directories_may_be_normalized_or_suspended(self):
        profile = self.profile(); before = self.byte_tree()
        with self.assertRaises(DNSBackupError): suspend_restored_dns(self.data)
        self.assertEqual(self.byte_tree(), before)
        stage = self.root / '.restore-synthetic'; stage.mkdir(mode=0o700)
        target = stage / 'data'; shutil.copytree(self.data, target, ignore=shutil.ignore_patterns('owner.lock'))
        for path in (target / 'certificates', target / 'certificates/dns-credentials', target / 'certificates/dns-credentials' / profile['id']):
            path.chmod(0o755)
        self.assertTrue(suspend_restored_dns(target))
        self.assertTrue(DNSCredentialStore(target / 'certificates').list()[0]['restore_pending'])
        self.assertTrue(self.store.list()[0]['enabled'])
        for path in (target / 'certificates', target / 'certificates/dns-credentials', target / 'certificates/dns-credentials' / profile['id']):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)

    def test_reader_capability_is_literal_bounded_and_never_executes_candidate_code(self):
        profile = self.profile(); self.certificate(profile)
        source = self.root / 'payload' / 'app' / 'certificates'; source.mkdir(parents=True)
        path = source / 'schema.py'
        for text in ('DNS_IDENTITY_SCHEMA_MAX = True\n', 'DNS_IDENTITY_SCHEMA_MAX = 0\n',
            'DNS_IDENTITY_SCHEMA_MAX = int(1)\n', 'DNS_IDENTITY_SCHEMA_MAX = 1\nDNS_IDENTITY_SCHEMA_MAX=2\n',
            'if True:\n    DNS_IDENTITY_SCHEMA_MAX=1\n', 'x' * (512 * 1024 + 1)):
            path.write_text(text); path.chmod(0o600)
            with self.subTest(text=text[:30]), self.assertRaises(DNSBackupError): require_dns_release(self.data, source.parent.parent)
        path.write_text('DNS_IDENTITY_SCHEMA_MAX = 1\nraise AssertionError("must never execute")\n')
        require_dns_release(self.data, source.parent.parent)
        alias = self.root / 'capability-alias'; os.link(path, alias)
        with self.assertRaises(DNSBackupError): require_dns_release(self.data, source.parent.parent)

    def test_activate_and_restore_guard_old_reader_even_without_any_proxy_state(self):
        old = self.prepared('old-reader', None)
        new = self.prepared('dns-reader', 'DNS_IDENTITY_SCHEMA_MAX = 1\n')
        profile = self.profile(); self.certificate(profile)
        self.assertFalse((self.data / '.proxy-state-initialized').exists())
        with patch.object(tools, 'health_check') as health:
            with self.assertRaises(tools.ReleaseError): tools.activate(self.root, 'old-reader')
            health.assert_not_called()
            tools.activate(self.root, 'dns-reader')
        self.assertEqual(tools.active(self.root)[1]['release_id'], 'dns-reader')
        archive = self.root / 'guarded-restore.zip'; checksum = tools.backup(self.root, archive)
        tools.atomic_json(self.root / 'CURRENT.json', {'release_id': 'old-reader', 'previous_id': None})
        before = self.byte_tree()
        with self.assertRaises(tools.ReleaseError): tools.restore(self.root, archive, checksum)
        for name, raw in before.items(): self.assertEqual((self.data / name).read_bytes(), raw)
        self.assertEqual(tools.active(self.root)[1]['release_id'], 'old-reader')


if __name__ == '__main__':
    unittest.main()
