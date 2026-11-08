"""Synthetic signed materials and database bindings; no real cores or DNS."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import test_certificates as certificate_tests
from app.certificates.errors import CertificateError
from app.certificates.models import Certificate, CertificateBinding
from app.certificates.dns_store import DNSCredentialStore
from app.models import database
from app.services.core_manager import core_manager
from app.services import managed_tls


class DNSCertificateLifecycleTests(unittest.TestCase):
    login = certificate_tests.CertificateTests.login
    issue_http = certificate_tests.CertificateTests.issue
    due = certificate_tests.CertificateTests.due

    def setUp(self):
        certificate_tests.CertificateTests.setUp(self)
        self.provider.supports_dns = True
        self.profile = DNSCredentialStore(self.manager.root).create(label='合成凭据', zone_name='example.test',
            zone_id='c' * 32, api_token='fake_lifecycle_token_' + 'x' * 32)

    def issue_dns(self, wildcard=True):
        job = self.manager.create('example.test', 'admin@example.test', 'production', True, True,
            method='dns-01', wildcard=wildcard, credential_id=self.profile['id'])
        self.manager.process_once()
        self.assertEqual(self.manager.job(job['id'])['state'], 'succeeded', self.manager.job(job['id']))
        return job['certificate_id']

    def test_same_root_three_immutable_requests_coexist_and_public_metadata_has_no_secrets(self):
        http = self.issue_http(domain='example.test')
        single, wildcard = self.issue_dns(False), self.issue_dns(True)
        self.assertEqual(len({http, single, wildcard}), 3)
        listed = {row['id']: row for row in self.manager.list_certificates()}
        self.assertEqual(listed[http]['names'], ['example.test'])
        self.assertEqual(listed[wildcard]['names'], ['example.test', '*.example.test'])
        for row in listed.values():
            self.assertNotIn('credential_id', row)
            self.assertNotIn('api_token', row)
        with self.assertRaisesRegex(CertificateError, 'CERTIFICATE_ALREADY_EXISTS'):
            self.manager.create('example.test', 'admin@example.test', 'production', True, True,
                method='dns-01', wildcard=True, credential_id=self.profile['id'])

    def test_connection_host_is_literal_covered_and_required_for_dns_only(self):
        identity = self.issue_dns()
        with self.assertRaisesRegex(CertificateError, 'CONNECTION_DOMAIN_REQUIRED'):
            self.manager.connection_domain(identity)
        for host in ('*.example.test', 'https://node.example.test'):
            with self.subTest(host=host), self.assertRaisesRegex(CertificateError, 'INVALID_CONNECTION_DOMAIN'):
                self.manager.connection_domain(identity, host)
        with self.assertRaisesRegex(CertificateError, 'CERTIFICATE_DOMAIN_MISMATCH'):
            self.manager.connection_domain(identity, 'deep.node.example.test')
        self.assertEqual(self.manager.connection_domain(identity, 'node.example.test'), 'node.example.test')
        http = self.issue_http(domain='old.example.test')
        self.assertEqual(self.manager.connection_domain(http), 'old.example.test')

    def test_panel_can_use_covered_existing_origin_and_renew_without_changing_it(self):
        identity = self.issue_dns()
        reloads = []
        self.manager.panel_reloader = lambda *paths: reloads.append(paths)
        self.manager.bind(identity, 'panel')
        first = self.manager.panel_material()
        with database.SessionLocal() as db:
            self.assertEqual(db.get(CertificateBinding, 'panel').server_name, 'panel.example.test')
        self.due(identity); self.manager.process_once()
        self.assertEqual(len(reloads), 2)
        self.assertNotEqual(first, self.manager.panel_material())
        with database.SessionLocal() as db:
            self.assertEqual(db.get(CertificateBinding, 'panel').server_name, 'panel.example.test')

    def test_sing_box_and_xray_renewal_preserves_host_credentials_and_stopped_intent(self):
        identity = self.issue_dns()
        original_revision = self.manager.material(identity)[2]
        settings = {'users': [{'uuid': '11111111-1111-1111-1111-111111111111'}]}
        for node_id, core in ((700, 'sing-box'), (701, 'xray')):
            host = ('sing' if core == 'sing-box' else 'xray') + '.example.test'
            stream = ({'tls': {'enabled': True, 'server_name': host}, '_vui': {'server_name': host}}
                      if core == 'sing-box' else {'method': 'raw', 'security': 'tls',
                          'tlsSettings': {'serverName': host, 'certificates': []}, '_vui': {'server_name': host}})
            with database.SessionLocal() as db:
                db.add(database.Inbound(id=node_id, core=core, protocol='vless', port=10443 + node_id,
                    settings=deepcopy(settings), stream_settings=stream, enable=True))
                db.commit()
            with patch.object(core_manager, 'validate_inbound_change', return_value=None), \
                 patch.object(core_manager.get(core), 'status', return_value={'running': True}), \
                 patch.object(core_manager, 'apply_database', return_value={'applied': True}):
                self.manager.bind(identity, 'inbound:' + str(node_id), server_name=host)
        self.due(identity)
        with patch.object(core_manager, 'validate_inbound_change', return_value=None), \
             patch.object(core_manager.get('sing-box'), 'status', return_value={'running': False}), \
             patch.object(core_manager.get('xray'), 'status', return_value={'running': False}), \
             patch.object(core_manager, 'apply_database', return_value={'applied': False}) as apply:
            self.manager.process_once()
            self.assertEqual(apply.call_count, 2)
            self.assertTrue(all(call.kwargs['activate'] is False for call in apply.call_args_list))
        self.assertNotEqual(self.manager.material(identity)[2], original_revision)
        with database.SessionLocal() as db:
            for node_id, core in ((700, 'sing-box'), (701, 'xray')):
                row = db.get(database.Inbound, node_id)
                host = ('sing' if core == 'sing-box' else 'xray') + '.example.test'
                self.assertEqual(managed_tls.server_name(core, row.stream_settings), host)
                self.assertEqual(row.settings, settings)
                binding = db.get(CertificateBinding, 'inbound:' + str(node_id))
                self.assertEqual(binding.server_name, host)
                self.assertEqual(binding.applied_revision, original_revision)
                self.assertEqual(binding.error, 'CORE_STOPPED_PENDING_APPLY')

    def test_certificate_and_host_only_edit_keeps_saved_transport_and_unknown_options(self):
        from app.api.inbounds import _prepare_inbound_edit
        identity = self.issue_dns()
        stream = {'tls': {'enabled': True, 'server_name': 'old.example.test'},
                  'transport': {'type': 'ws', 'path': '/existing', 'headers': {'Host': 'front.example.test'},
                                'max_early_data': 0}, '_vui': {'server_name': 'old.example.test'},
                  'preserved_advanced_option': {'value': 'synthetic'}}
        settings = {'users': [{'uuid': '11111111-1111-1111-1111-111111111111'}]}
        with database.SessionLocal() as db:
            row = database.Inbound(id=702, core='sing-box', protocol='vless', port=11702,
                settings=deepcopy(settings), stream_settings=deepcopy(stream), enable=True)
            db.add(row); db.commit()
            prepared, binding = _prepare_inbound_edit(row, {'certificate_id': identity,
                'profile': {'server_name': 'node.example.test'}})
            self.assertNotIn('profile', prepared)
            self.assertEqual(prepared['stream_settings']['transport'], stream['transport'])
            self.assertEqual(prepared['stream_settings']['preserved_advanced_option'], stream['preserved_advanced_option'])
            self.assertEqual(prepared['stream_settings']['tls']['server_name'], 'node.example.test')
            self.assertEqual(row.stream_settings, stream, 'Preparation never changes the saved node before guarded update')
            self.assertEqual(row.settings, settings)
            self.assertTrue(binding[0]); self.assertEqual(binding[1], identity)


if __name__ == '__main__':
    unittest.main()
