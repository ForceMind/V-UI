"""First-install DNS with real SQLite/PKI/journals and a synthetic Cloudflare API."""
from contextlib import ExitStack
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.certificates.cli import (bootstrap_panel, dns_bootstrap_status, private_dns_request,
                                  dns_bootstrap_recovery_status, recover_dns_bootstrap)
from app.certificates.dns_credentials import DNSCredentials
from app.certificates.dns_journal import DNSChallengeJournal
from app.certificates.dns_store import DNSCredentialStore
from app.certificates.errors import CertificateError
from app.certificates.manager import CertificateManager
from app.certificates.models import Certificate, CertificateBinding, CertificateJob
from app.certificates.provider import CertbotProvider
from app.models import database
from app.release_tools import writer_operation
from scripts.installer_certificate_state import run as certificate_state
from test_certificates import SigningProvider
from test_dns_journal import Server, TOKEN, NEW_TOKEN, VALUE, ZONE

HOST = 'panel.example.test'


class DNSFirstInstallTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-dns-first-')))
        self.data = self.root / 'data'; self.data.mkdir(mode=0o700)
        self.engine = create_engine('sqlite:///' + str(self.data / 'v-ui.db'))
        self.stack.callback(self.engine.dispose)
        self.stack.enter_context(patch.multiple(database, engine=self.engine,
            SessionLocal=sessionmaker(bind=self.engine, autoflush=False)))
        database.init_db()
        self.certs = self.data / 'certificates'
        self.store = DNSCredentialStore(self.certs)
        self.server = Server()
        self.discovery_error = None
        self.journal = DNSChallengeJournal(self.certs, client_factory=self.server.factory)
        self.provider = CertbotProvider(self.certs, dns_journal=self.journal)
        self.signer = SigningProvider()
        self.manager = CertificateManager(self.certs, self.provider,
            trusted_roots=self.signer.ca.public_bytes(serialization.Encoding.PEM))
        self.stack.enter_context(patch.object(self.provider, '_issue_process', side_effect=self.issue))
        self.stack.enter_context(patch('app.certificates.dns_credentials.DNSCredentials',
            side_effect=lambda root: DNSCredentials(root, client_factory=self.credential_client)))

    def credential_client(self, token):
        self.assertIn(token, (TOKEN, NEW_TOKEN))
        case = self
        class Client:
            def discover_zone(self, name):
                if case.discovery_error:
                    raise CertificateError(case.discovery_error)
                return {'zone_id': ZONE, 'zone_name': name}
        return Client()

    def issue(self, record, work, stop, *, deadline):
        if record['method'] == 'dns-01':
            self.journal.present(record['job_id'], record['domain'], VALUE, deadline=deadline)
        return self.signer.issue(record, work, stop)

    def bootstrap(self, request=None):
        if request is None:
            request = {'version': 1, 'zone_name': 'example.test', 'api_token': TOKEN}
        with writer_operation(self.root):
            return bootstrap_panel(self.manager, HOST, 'a@example.test', accept_terms=True,
                                   method='dns-01', dns_request=request)

    def status(self):
        with writer_operation(self.root):
            return dns_bootstrap_status(self.manager, HOST)

    def expect_failure(self, code, request=None):
        with self.assertRaisesRegex(CertificateError, '^' + code + '$') as caught:
            self.bootstrap(request)
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_first_install_issues_single_name_dns_and_reuses_applied_revision(self):
        self.assertEqual(self.status(), {'version': 1, 'needs_token': True})
        result = self.bootstrap()
        with database.SessionLocal() as db:
            row = db.query(Certificate).one()
            self.assertEqual((row.domain, row.method, row.wildcard), (HOST, 'dns-01', False))
            self.assertEqual(row.credential_id, self.store.list()[0]['id'])
            binding = db.get(CertificateBinding, 'panel')
            self.assertEqual((binding.applied_certificate_id, binding.server_name), (row.id, HOST))
            self.assertEqual(binding.applied_revision, result['revision'])
        self.assertEqual(self.server.records, {})
        self.assertEqual(self.journal.pending(), [])
        self.assertEqual(self.status(), {'version': 1, 'needs_token': False})
        with (patch.object(self.provider, 'issue', side_effect=AssertionError('no repeated issuance')),
              patch.object(self.store, 'snapshot', side_effect=AssertionError('no credentials for applied reuse'))):
            self.assertTrue(self.bootstrap({'version': 1})['used_existing'])
        self.assertEqual(self.signer.calls, 1)

    def test_zone_permission_failure_creates_no_profile_or_certificate(self):
        self.discovery_error = 'DNS_PROVIDER_FORBIDDEN'
        self.expect_failure('DNS_PROVIDER_FORBIDDEN')
        self.assertEqual(self.store.list(), [])
        with database.SessionLocal() as db:
            self.assertEqual(db.query(Certificate).count(), 0)
            self.assertIsNone(db.get(CertificateBinding, 'panel'))
        self.assertEqual(self.server.calls, [])

    def test_txt_permission_failure_retains_profile_and_job_without_panel_binding(self):
        self.server.create_mode = 'forbidden'
        self.expect_failure('DNS_PROVIDER_FORBIDDEN')
        profile = self.store.list()[0]
        self.assertEqual(self.store.snapshot(profile['id'])['api_token'], TOKEN)
        with database.SessionLocal() as db:
            job = db.query(CertificateJob).one()
            self.assertEqual((job.state, job.error), ('failed', 'DNS_PROVIDER_FORBIDDEN'))
            self.assertEqual(self.journal.view(job.id)['phase'], 'cleaned')
            self.assertIsNone(db.get(CertificateBinding, 'panel'))
        self.assertEqual(self.status(), {'version': 1, 'needs_token': False})
        self.expect_failure('RETRY_COOLDOWN', {'version': 1})
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(sum(method == 'post' for method, _ in self.server.calls), 1)

    def test_unknown_remote_write_survives_installer_restore_then_recovers_original_revision(self):
        before = certificate_state('snapshot', self.root)
        self.server.create_mode = 'timeout-after'
        self.server.delete_mode = 'timeout-before'
        self.expect_failure('DNS_CLEANUP_PENDING')
        profile = self.store.list()[0]
        snapshot = self.store.snapshot(profile['id'])
        pending = self.journal.pending()[0]
        private = json.loads((self.journal.root / (pending['job_id'] + '.json')).read_text())
        self.assertEqual(private['credential_revision'], snapshot['revision'])
        self.assertTrue(private['entries'][0]['ownership'].startswith('v-ui:'))
        self.assertEqual(len(self.server.records), 1)
        preserved = {path: path.read_bytes() for path in self.certs.rglob('*.json')}
        certificate_state('restore', self.root, json.dumps(before))
        for path, raw in preserved.items():
            self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(self.status(), {'version': 1, 'needs_token': False})
        self.expect_failure('DNS_CLEANUP_PENDING', {'version': 1})
        self.assertEqual(sum(method == 'post' for method, _ in self.server.calls), 1)
        self.server.create_mode = self.server.delete_mode = None
        self.server.tokens.clear()
        self.expect_failure('RETRY_COOLDOWN', {'version': 1})
        self.assertEqual(self.server.records, {})
        self.assertEqual(self.journal.pending(), [])
        self.assertTrue(self.server.tokens)
        self.assertEqual(set(self.server.tokens), {TOKEN})
        self.assertEqual(self.store.snapshot(profile['id'])['revision'], snapshot['revision'])
        with database.SessionLocal() as db:
            self.assertEqual(db.query(Certificate).count(), 1)
            self.assertEqual(db.query(CertificateJob).count(), 1)
            self.assertIsNone(db.get(CertificateBinding, 'panel'))
            row = db.query(Certificate).one(); row.retry_at = row.last_attempt = 0; db.commit()
        result = self.bootstrap({'version': 1})
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.manager.panel_material()[0].parent.name, result['revision'])

    def test_unknown_write_without_observed_record_never_auto_confirms_absence(self):
        self.server.create_mode = 'timeout-before'
        self.expect_failure('DNS_CLEANUP_PENDING')
        self.assertEqual(self.server.records, {})
        pending = self.journal.pending()[0]
        for _ in range(2):
            self.expect_failure('DNS_CLEANUP_PENDING', {'version': 1})
        self.assertEqual(self.journal.view(pending['job_id'])['phase'], 'cleanup_pending')
        self.assertEqual(sum(method == 'post' for method, _ in self.server.calls), 1)

    def test_interrupted_running_job_recovers_txt_but_does_not_duplicate_issuance(self):
        profile = self.store.create(label='fake first install', zone_name='example.test', zone_id=ZONE, api_token=TOKEN)
        with writer_operation(self.root):
            job = self.manager.create(HOST, 'a@example.test', 'production', True, True,
                                      method='dns-01', credential_id=profile['id'])
            with database.SessionLocal() as db:
                db.get(CertificateJob, job['id']).state = 'running'; db.commit()
            self.journal.begin(job['id'], profile['id'], self.store.snapshot(profile['id'])['revision'], HOST, [HOST])
            self.journal.present(job['id'], HOST, VALUE)
        self.expect_failure('WORKER_INTERRUPTED', {'version': 1})
        self.assertEqual(self.server.records, {})
        self.assertEqual(self.journal.pending(), [])
        with database.SessionLocal() as db:
            self.assertEqual(db.query(CertificateJob).count(), 1)
            self.assertEqual(db.get(CertificateJob, job['id']).error, 'WORKER_INTERRUPTED')
            self.assertIsNone(db.get(CertificateBinding, 'panel'))
        self.assertEqual(self.signer.calls, 0)
        self.expect_failure('RETRY_COOLDOWN', {'version': 1})

    def test_saved_dns_choice_does_not_replace_actual_applied_http_binding(self):
        with writer_operation(self.root):
            first = bootstrap_panel(self.manager, HOST, 'a@example.test', accept_terms=True)
        self.assertEqual(self.status(), {'version': 1, 'needs_token': False})
        second = self.bootstrap({'version': 1})
        self.assertEqual(second, first)
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.signer.calls, 1)

    def test_private_protocol_is_bounded_and_rejects_duplicates_and_secret_arguments(self):
        for raw in ('{"version":1,"version":1}', '{"version":true}',
                    '{"version":1,"api_token":"' + TOKEN + '"}',
                    json.dumps({'version': 1, 'zone_name': 'example.test', 'api_token': TOKEN, 'command': 'anything'}),
                    json.dumps({'version': 1, 'zone_name': 'example.test', 'api_token': 'a' * 5000})):
            with self.subTest(raw_size=len(raw)), self.assertRaises(CertificateError) as caught:
                private_dns_request(io.StringIO(raw))
            self.assertEqual(str(caught.exception), 'DNS_BOOTSTRAP_REQUEST_INVALID')
            self.assertNotIn(TOKEN, str(caught.exception))
        self.assertEqual(private_dns_request(io.StringIO('{"version":1}')), {'version': 1})

    def recovery_request(self, action, *, api_token=None):
        with writer_operation(self.root):
            state = dns_bootstrap_recovery_status(self.manager, HOST)
        profile = state['credential']
        request = {'version': 1, 'action': action, 'credential_id': profile['id'],
                   'expected_profile_revision': profile['revision']}
        if api_token is not None:
            request['api_token'] = api_token
        if action in ('cleanup', 'confirm-absent'):
            job = state['pending'][0]
            request.update(job_id=job['job_id'], expected_revision=job['revision'])
        return request

    def recover(self, request):
        with writer_operation(self.root):
            return recover_dns_bootstrap(self.manager, HOST, private_dns_request(io.StringIO(json.dumps(request))))

    def test_local_token_replacement_uses_cas_same_zone_and_preserves_old_revisions(self):
        self.server.create_mode = 'forbidden'
        self.expect_failure('DNS_PROVIDER_FORBIDDEN')
        profile = self.store.list()[0]
        old = self.store.snapshot(profile['id'])
        request = self.recovery_request('replace-token', api_token=NEW_TOKEN)
        self.assertEqual(self.recover(request)['phase'], 'credential-replaced')
        current = self.store.snapshot(profile['id'])
        self.assertNotEqual(old['revision'], current['revision'])
        self.assertEqual(current['api_token'], NEW_TOKEN)
        self.assertEqual(self.store.snapshot(profile['id'], revision=old['revision'])['api_token'], TOKEN)
        with self.assertRaisesRegex(Exception, 'DNS_CREDENTIAL_REVISION_CONFLICT'):
            self.recover(request)
        self.expect_failure('RETRY_COOLDOWN', {'version': 1})
        self.assertEqual(sum(method == 'post' for method, _ in self.server.calls), 1)

    def test_changed_token_needs_explicit_journal_authorization_for_original_txt_cleanup(self):
        self.server.create_mode = 'timeout-after'; self.server.delete_mode = 'timeout-before'
        self.expect_failure('DNS_CLEANUP_PENDING')
        job = self.journal.pending()[0]['job_id']
        path = self.journal.root / (job + '.json')
        original = json.loads(path.read_text())
        self.recover(self.recovery_request('replace-token', api_token=NEW_TOKEN))
        self.assertEqual(json.loads(path.read_text())['credential_revision'], original['credential_revision'])
        self.server.reject_tokens.add(TOKEN); self.server.delete_mode = None
        self.expect_failure('DNS_CLEANUP_PENDING', {'version': 1})
        self.assertIsNone(json.loads(path.read_text())['recovery_credential_revision'])
        self.server.tokens.clear()
        result = self.recover(self.recovery_request('cleanup'))
        self.assertEqual(result['phase'], 'cleaned')
        final = json.loads(path.read_text())
        self.assertEqual(final['credential_revision'], original['credential_revision'])
        self.assertIsNotNone(final['recovery_credential_revision'])
        self.assertIsNotNone(final['recovery_authorized_at'])
        self.assertEqual(set(self.server.tokens), {NEW_TOKEN})
        self.assertEqual(self.server.records, {})

    def test_manual_absence_confirmation_is_explicit_cas_and_never_reports_cleaned(self):
        self.server.create_mode = 'timeout-before'
        self.expect_failure('DNS_CLEANUP_PENDING')
        request = self.recovery_request('confirm-absent')
        stale = {**request, 'expected_revision': 'f' * 32}
        with self.assertRaisesRegex(CertificateError, 'DNS_CHALLENGE_REVISION_CONFLICT'):
            self.recover(stale)
        self.assertEqual(self.journal.pending()[0]['phase'], 'cleanup_pending')
        result = self.recover(request)
        self.assertEqual(result['phase'], 'manual_confirmed')
        job = self.journal.view(request['job_id'])
        self.assertEqual(job['phase'], 'manual_confirmed')
        self.assertEqual(job['error'], 'DNS_CHALLENGE_MANUAL_CONFIRMATION')
        self.assertEqual(self.journal.pending(), [])
        self.expect_failure('RETRY_COOLDOWN', {'version': 1})
        self.assertEqual(sum(method == 'post' for method, _ in self.server.calls), 1)

    def test_restored_profile_stays_frozen_until_separate_explicit_enable(self):
        self.server.create_mode = 'forbidden'
        self.expect_failure('DNS_PROVIDER_FORBIDDEN')
        self.store.suspend_restored()
        self.recover(self.recovery_request('replace-token', api_token=NEW_TOKEN))
        self.assertEqual(self.store.list()[0]['status'], 'restore_pending')
        self.assertEqual(self.recover(self.recovery_request('enable'))['phase'], 'credential-enabled')
        self.assertEqual(self.store.list()[0]['status'], 'configured')
        self.assertEqual(self.store.snapshot(self.store.list()[0]['id'])['api_token'], NEW_TOKEN)

    def test_local_recovery_refuses_any_already_bound_panel(self):
        self.bootstrap()
        with writer_operation(self.root), self.assertRaisesRegex(CertificateError, 'DNS_BOOTSTRAP_RECOVERY_UNAVAILABLE'):
            dns_bootstrap_recovery_status(self.manager, HOST)


if __name__ == '__main__':
    unittest.main()
