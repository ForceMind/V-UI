"""Certificate failure paths and real API authorization, using synthetic material."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import patch
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from app.models import database
from app.certificates import manager as manager_module
from app.certificates.manager import CertificateManager, now
from app.certificates.models import Certificate, CertificateBinding, CertificateJob
from app.certificates.material import CertificateError, domain_name, make_csr, validate_material
from app.certificates.provider import CertbotProvider
from app.api import certificates as api
import test_auth as auth_tests


def ca_pair():
    key=ec.generate_private_key(ec.SECP256R1())
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'VUI test CA')])
    now_=datetime.now(timezone.utc)
    ca=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now_-timedelta(minutes=5))
        .not_valid_after(now_+timedelta(days=180))
        .add_extension(x509.BasicConstraints(ca=True,path_length=0),True)
        .add_extension(x509.KeyUsage(True,False,False,False,False,True,True,False,False),True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),False)
        .sign(key,hashes.SHA256()))
    return key,ca


class SigningProvider:
    def __init__(self):
        self.key,self.ca=ca_pair();self.calls=0;self.failure=False
    def issue(self, record, work, stop):
        self.calls+=1
        if self.failure:raise CertificateError('ACME_VALIDATION_FAILED')
        csr=x509.load_pem_x509_csr((work/'request.pem').read_bytes())
        date=datetime.now(timezone.utc)
        cert=(x509.CertificateBuilder().subject_name(csr.subject).issuer_name(self.ca.subject)
            .public_key(csr.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(date-timedelta(minutes=1)).not_valid_after(date+timedelta(days=90))
            .add_extension(csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value,False)
            .add_extension(x509.BasicConstraints(ca=False,path_length=None),True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self.key.public_key()),False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),False)
            .add_extension(x509.KeyUsage(True,False,False,False,False,False,False,False,False),True)
            .sign(self.key,hashes.SHA256()))
        return cert.public_bytes(serialization.Encoding.PEM)+self.ca.public_bytes(serialization.Encoding.PEM)


class CertificateTests(unittest.TestCase):
    login=auth_tests.AuthenticationTests.login
    def setUp(self):
        auth_tests.AuthenticationTests.setUp(self)
        self.provider=SigningProvider()
        self.manager=CertificateManager(Path(self.temp.name)/'certificates',self.provider,
            trusted_roots=self.provider.ca.public_bytes(serialization.Encoding.PEM))
        patch.object(api,'get_manager',return_value=self.manager).start()
        patch.object(manager_module,'_instance',self.manager).start()
        self.login()

    def issue(self, environment='production', domain='panel.example.test'):
        response=self.client.post('/api/certificates',headers=auth_tests.HEADERS,
            json={'domain':domain,'email':'admin@example.test','environment':environment,'auto_renew':True,'accept_terms':True})
        self.assertEqual(response.status_code,202,response.text)
        job=response.json();self.manager.process_once()
        self.assertEqual(self.manager.job(job['id'])['state'],'succeeded',self.manager.job(job['id']))
        return job['certificate_id']

    def due(self, identity):
        with database.SessionLocal() as db:
            row=db.get(Certificate,identity);row.renew_at=0;row.last_attempt=0;row.retry_at=0;db.commit()

    def test_real_api_request_job_material_and_private_key_separation(self):
        identity=self.issue()
        listed=self.client.get('/api/certificates').json()[0]
        self.assertEqual(listed['status'],'valid')
        paths,_,revision=self.manager.material(identity)
        raw=self.client.get('/api/certificates/'+identity+'/fullchain.pem').content
        self.assertTrue(raw.startswith(b'-----BEGIN CERTIFICATE'))
        self.assertNotIn(b'PRIVATE KEY',raw)
        self.assertNotIn(paths[1].read_text(),json.dumps(listed))
        self.assertEqual(paths[0].parent.name,revision)
        self.assertEqual(paths[1].stat().st_mode&0o777,0o600)
        self.assertFalse(list(self.manager.root.glob('.issue-*')))

    def test_request_requires_ownership_terms_and_rejects_options_injection(self):
        base={'domain':'node.example.test','email':'a@example.test','environment':'production','accept_terms':True}
        for payload in ({**base,'accept_terms':False},{**base,'domain':'--deploy-hook evil'},
                        {**base,'domain':'*.example.test'},{**base,'server':'http://127.0.0.1'},
                        {**base,'domain':'127.0.0.1'},{**base,'auto_renew':'true'},
                        {**base,'email':'x@example.test\n--hook'}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post('/api/certificates',json=payload,headers=auth_tests.HEADERS).status_code,422)
        self.assertEqual(self.provider.calls,0)

    def test_staging_material_never_deploys(self):
        identity=self.issue('staging')
        response=self.client.post('/api/certificates/'+identity+'/bind-panel',headers=auth_tests.HEADERS)
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()['detail'],'STAGING_CANNOT_BE_DEPLOYED')
        self.assertIsNone(self.manager.panel_material())

    def test_same_domain_duplicate_and_not_due_renewal_do_not_contact_ca(self):
        identity=self.issue()
        with self.assertRaises(CertificateError):self.manager.create('panel.example.test','x@example.test','production',True,True)
        with self.assertRaisesRegex(CertificateError,'RENEWAL_NOT_DUE'):self.manager.renew(identity)
        self.assertEqual(self.provider.calls,1)

    def test_failed_renewal_keeps_active_revision_and_backs_off(self):
        identity=self.issue();before=self.manager.material(identity)[2]
        self.due(identity);self.provider.failure=True
        job=self.manager.renew(identity);self.manager.process_once()
        self.assertEqual(self.manager.job(job['id'])['state'],'failed')
        self.assertEqual(self.manager.material(identity)[2],before)
        self.assertEqual(self.manager.list_certificates()[0]['status'],'renewal_failed')
        with self.assertRaisesRegex(CertificateError,'RETRY_COOLDOWN'):self.manager.renew(identity)
        self.assertFalse(self.manager.process_once())
        self.assertEqual(self.provider.calls,2)

    def test_scheduler_is_due_based_persistent_and_optional(self):
        identity=self.issue();self.due(identity)
        self.manager.set_auto_renew(identity,False)
        self.assertFalse(self.manager.process_once())
        self.manager.set_auto_renew(identity,True)
        fresh=CertificateManager(self.manager.root,self.provider,trusted_roots=self.manager.trusted_roots)
        self.assertTrue(fresh.process_once());self.assertEqual(self.provider.calls,2)

    def test_interrupted_job_is_not_immediately_duplicated(self):
        job=self.manager.create('retry.example.test','a@example.test','production',True,True)
        with database.SessionLocal() as db:
            db.get(CertificateJob,job['id']).state='running';db.commit()
        self.manager.process_once()
        self.assertEqual(self.manager.job(job['id'])['error'],'WORKER_INTERRUPTED')
        self.assertEqual(self.provider.calls,0)

    def test_panel_binding_validates_domain_and_renewal_applies_automatically(self):
        identity=self.issue();applied=[]
        self.manager.panel_reloader=lambda *paths:applied.append(tuple(paths))
        self.manager.bind(identity,'panel');old=self.manager.panel_material()
        self.due(identity);self.manager.process_once()
        self.assertEqual(len(applied),2)
        self.assertNotEqual(old,self.manager.panel_material())
        wrong=self.issue(domain='other.example.test')
        with self.assertRaisesRegex(CertificateError,'PANEL_DOMAIN_MISMATCH'):self.manager.bind(wrong,'panel')
        self.assertEqual(self.manager.panel_material(),applied[-1])

    def test_failed_panel_apply_is_visible_without_replacing_applied_material(self):
        identity=self.issue();self.manager.panel_reloader=lambda *paths:None
        self.manager.bind(identity,'panel');before=self.manager.panel_material()
        def fail(*args):raise RuntimeError('sensitive diagnostics never echoed')
        self.manager.panel_reloader=fail;self.due(identity);self.manager.process_once()
        item=self.manager.list_certificates()[0]
        self.assertEqual(item['latest_job']['state'],'succeeded')
        self.assertEqual(item['bindings'][0]['error'],'CERTIFICATE_APPLY_FAILED')
        self.assertTrue(item['bindings'][0]['pending'])
        self.assertEqual(self.manager.panel_material(),before)

    def test_file_tampering_blocks_consumers(self):
        identity=self.issue();paths,_,_=self.manager.material(identity)
        paths[0].write_bytes(paths[0].read_bytes()+b'\n')
        with self.assertRaisesRegex(CertificateError,'CERTIFICATE_FILES_CHANGED'):self.manager.material(identity)

    def test_certificate_api_does_not_accept_admin_tokens_as_query_credentials(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/certificates').status_code,401)
        self.assertEqual(self.client.post('/api/certificates',headers=auth_tests.HEADERS,json={}).status_code,401)

    def test_csr_generated_private_key_does_not_match_another_key(self):
        identity=self.issue();paths,_,_=self.manager.material(identity)
        other,_=make_csr('panel.example.test')
        with self.assertRaisesRegex(CertificateError,'KEY_MISMATCH'):
            validate_material(paths[0].read_bytes(),other,'panel.example.test',verify_chain=False)

    def test_vless_and_trojan_node_renewal_bind_new_material_without_starting_stopped_core(self):
        from app.services.core_manager import core_manager
        for index,protocol in enumerate(("vless","trojan"),start=1):
            with self.subTest(protocol=protocol):
                identity=self.issue(domain=f"{protocol}.example.test")
                node_id=100+index
                settings=(
                    {'users':[{'uuid':'11111111-1111-1111-1111-111111111111'}]}
                    if protocol=='vless'
                    else {'users':[{'password':'trojan-password'}]}
                )
                with database.SessionLocal() as db:
                    db.add(database.Inbound(
                        id=node_id,core='sing-box',protocol=protocol,
                        port=10443+index,enable=True,settings=settings,
                        stream_settings={'tls':{
                            'enabled':True,
                            'server_name':f'{protocol}.example.test',
                        }},
                    ))
                    db.commit()

                with patch.object(core_manager.get('sing-box'),'status',return_value={'running':False}), \
                     patch.object(core_manager,'apply_database',return_value={'applied':False}) as apply:
                    with self.assertRaisesRegex(CertificateError,'CORE_STOPPED_PENDING_APPLY'):
                        self.manager.bind(identity,'inbound:'+str(node_id))
                    self.assertFalse(apply.call_args.kwargs['activate'])

                with patch.object(core_manager.get('sing-box'),'status',return_value={'running':True}), \
                     patch.object(core_manager,'apply_database',return_value={'applied':True}) as apply:
                    self.manager.bind(identity,'inbound:'+str(node_id))
                    first=self.manager.material(identity)[0]
                    self.due(identity)
                    self.manager.process_once()
                    with database.SessionLocal() as db:
                        row=db.get(database.Inbound,node_id)
                        self.assertNotEqual(
                            row.stream_settings['tls']['certificate_path'],
                            str(first[0]),
                        )
                        self.assertEqual(
                            db.get(CertificateBinding,'inbound:'+str(node_id)).applied_revision,
                            self.manager.material(identity)[2],
                        )
                    self.assertTrue(apply.call_args.kwargs['activate'])

    def test_offline_bootstrap_is_idempotent_and_reuses_valid_certificate(self):
        from app.certificates.cli import bootstrap_panel
        result=bootstrap_panel(self.manager,'panel.example.test','a@example.test',accept_terms=True)
        self.assertIsNotNone(self.manager.panel_material())
        again=bootstrap_panel(self.manager,'panel.example.test','a@example.test',accept_terms=True)
        self.assertEqual(result,again);self.assertEqual(self.provider.calls,1)

    def test_provider_command_has_no_shell_hooks_or_insecure_tls(self):
        provider=CertbotProvider(self.manager.root)
        work=Path(self.temp.name)/'command';work.mkdir()
        command=provider.command({'environment':'production','email':'a@example.test'},work)
        self.assertIn('--csr',command);self.assertIn('--no-directory-hooks',command)
        self.assertIn('https://acme-v02.api.letsencrypt.org/directory',command)
        self.assertNotIn('--no-verify-ssl',command);self.assertNotIn('--deploy-hook',command)
        self.assertNotIn('--force-renewal',command)

if __name__=='__main__':unittest.main()
