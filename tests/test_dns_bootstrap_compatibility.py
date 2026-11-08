"""Actual manager PKI/DB compatibility; synthetic CA, no DNS/core/system writes."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import test_certificates as fixtures
from app.certificates.cli import bootstrap_panel, ensure
from app.certificates.dns_store import DNSCredentialStore
from app.certificates.errors import CertificateError
from app.certificates.models import Certificate, CertificateBinding
from app.models import database

HOST = 'panel.example.test'


class DNSBootstrapCompatibilityTests(unittest.TestCase):
    login = fixtures.CertificateTests.login
    due = fixtures.CertificateTests.due

    def setUp(self):
        fixtures.CertificateTests.setUp(self)
        self.provider.supports_dns = True
        self.manager.panel_reloader = lambda *args: None
        self.store = DNSCredentialStore(self.manager.root)
        self.profile = self.store.create(label='bootstrap-fake', zone_name='example.test',
            zone_id='c' * 32, api_token='fake_bootstrap_credential_' + 'x' * 32)

    def issue_dns(self):
        job = self.manager.create('example.test', 'dns@example.test', 'production', True, True,
                                  method='dns-01', wildcard=True, credential_id=self.profile['id'])
        self.manager.process_once()
        self.assertEqual(self.manager.job(job['id'])['state'], 'succeeded', self.manager.job(job['id']))
        return job['certificate_id']

    def binding(self):
        with database.SessionLocal() as db:
            row = db.get(CertificateBinding, 'panel')
            return {key: deepcopy(getattr(row, key)) for key in
                    ('certificate_id','applied_certificate_id','applied_revision','server_name','error')}

    def test_http_ensure_does_not_choose_same_root_dns_certificate(self):
        dns = self.issue_dns(); count = self.provider.calls
        http = ensure(self.manager, 'example.test', 'http@example.test', accept_terms=True)
        self.assertNotEqual(http, dns); self.assertEqual(self.provider.calls, count + 1)
        with database.SessionLocal() as db:
            self.assertEqual(db.get(Certificate, http).method, 'http-01')
            self.assertFalse(db.get(Certificate, http).wildcard)
            self.assertEqual(db.get(Certificate, dns).email, 'dns@example.test')
        self.assertEqual(ensure(self.manager, 'example.test', 'http@example.test', accept_terms=True), http)
        self.assertEqual(self.provider.calls, count + 1)

    def test_valid_applied_dns_is_reused_without_ca_calls_or_credential_access_even_after_restore(self):
        dns = self.issue_dns(); self.manager.bind(dns, 'panel')
        before = self.binding(); count = self.provider.calls
        self.store.suspend_restored()
        with patch.object(self.manager.provider, 'issue', side_effect=AssertionError('no issuance')):
            for _ in range(2):
                result = bootstrap_panel(self.manager, HOST, 'unused@example.test', accept_terms=True)
                self.assertEqual(result, {'certificate_id': dns, 'domain': HOST, 'revision': before['applied_revision'],
                                          'used_existing': True, 'pending_apply': False})
        self.assertEqual(self.binding(), before); self.assertEqual(self.provider.calls, count)
        self.assertEqual(self.store.list()[0]['status'], 'restore_pending')

    def test_same_id_pending_revision_and_error_keep_actual_applied_material_without_promotion(self):
        dns = self.issue_dns(); self.manager.bind(dns, 'panel'); old = self.binding()
        self.manager.panel_reloader = lambda *args: (_ for _ in ()).throw(RuntimeError('synthetic reload refusal'))
        self.due(dns); self.manager.process_once()
        before = self.binding()
        self.assertEqual(before['certificate_id'], before['applied_certificate_id'])
        self.assertEqual(before['applied_revision'], old['applied_revision'])
        self.assertIsNotNone(before['error'])
        with database.SessionLocal() as db:
            current = db.get(Certificate, dns).revision
            self.assertNotEqual(current, before['applied_revision'])
        count = self.provider.calls
        result = bootstrap_panel(self.manager, HOST, 'unused@example.test', accept_terms=True)
        self.assertTrue(result['used_existing']); self.assertTrue(result['pending_apply'])
        self.assertEqual(result['revision'], old['applied_revision'])
        self.assertEqual(self.binding(), before); self.assertEqual(self.provider.calls, count)
        with database.SessionLocal() as db: self.assertEqual(db.get(Certificate, dns).revision, current)

    def test_different_desired_id_or_literal_host_cannot_silently_fall_back(self):
        dns = self.issue_dns(); self.manager.bind(dns, 'panel')
        count = self.provider.calls
        for host in ('other.example.test', 'deep.panel.example.test'):
            before = self.binding()
            with self.assertRaises(CertificateError):
                bootstrap_panel(self.manager, host, 'unused@example.test', accept_terms=True)
            self.assertEqual(self.binding(), before); self.assertEqual(self.provider.calls, count)
        http = ensure(self.manager, 'example.test', 'http@example.test', accept_terms=True)
        with database.SessionLocal() as db:
            row = db.get(CertificateBinding, 'panel'); row.certificate_id = http; row.error = 'PENDING'; db.commit()
        before = self.binding(); count = self.provider.calls
        with self.assertRaisesRegex(CertificateError, 'PANEL_CERTIFICATE_BINDING_CONFLICT'):
            bootstrap_panel(self.manager, HOST, 'unused@example.test', accept_terms=True)
        self.assertEqual(self.binding(), before); self.assertEqual(self.provider.calls, count)

    def test_corrupt_applied_material_or_missing_terms_does_not_modify_binding_or_request_ca(self):
        dns = self.issue_dns(); self.manager.bind(dns, 'panel'); before = self.binding(); count = self.provider.calls
        with self.assertRaisesRegex(CertificateError, 'TERMS_NOT_ACCEPTED'):
            bootstrap_panel(self.manager, HOST, 'unused@example.test', accept_terms=False)
        paths, _, _ = self.manager.material(dns)
        original = paths[0].read_bytes(); paths[0].write_bytes(b'corrupted synthetic material')
        try:
            with self.assertRaises(CertificateError):
                bootstrap_panel(self.manager, HOST, 'unused@example.test', accept_terms=True)
            self.assertEqual(self.binding(), before); self.assertEqual(self.provider.calls, count)
        finally:
            paths[0].write_bytes(original)


if __name__ == '__main__': unittest.main()
