"""Actual Certbot -> Pebble -> HTTP-01 -> certificate validation / renewal."""
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from cryptography import x509
from app.certificates.manager import CertificateManager
from app.certificates.models import Certificate
from app.certificates.provider import CertbotProvider
from app.certificates.material import CertificateError
from app.models import database
import test_auth as auth_tests
from acme_helpers import PebbleFixture


@unittest.skipUnless(os.getenv('VUI_TEST_PEBBLE'), 'explicit real ACME fixture')
class RealACMETests(unittest.TestCase):
    def setUp(self):
        auth_tests.AuthenticationTests.setUp(self)
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-acme-real-')))
        self.root.chmod(0o700)
        self.pebble=PebbleFixture(self.stack,self.root,self.root/'managed/http-webroot')
        provider=CertbotProvider(self.root/'managed',test_directory=self.pebble.directory,test_ca=self.pebble.ca)
        self.manager=CertificateManager(self.root/'managed',provider,trusted_roots=self.pebble.root_pem)

    def execute(self,job):
        self.manager.process_once()
        result=self.manager.job(job['id'])
        if result['state']!='succeeded':
            logs=[]
            for path in self.manager.root.glob('logs/**/*.log'):
                logs.append(path.read_text(errors='replace')[-7000:])
            self.fail(str(result)+'\n'+'\n'.join(logs)+'\n'+self.pebble.log_path.read_text()[-6000:])

    def test_real_issue_and_renewal_keep_valid_material_and_disable_directory_hooks(self):
        job=self.manager.create(self.pebble.domain,'admin@example.test','production',True,True)
        self.execute(job);identity=job['certificate_id']
        first=self.manager.material(identity)
        serial=x509.load_pem_x509_certificate(first[0][0].read_bytes()).serial_number
        with database.SessionLocal() as db:
            row=db.get(Certificate,identity);row.renew_at=0;row.last_attempt=0;db.commit()
        renewed=self.manager.renew(identity);self.execute(renewed)
        second=self.manager.material(identity)
        self.assertNotEqual(first[2],second[2])
        self.assertNotEqual(serial,x509.load_pem_x509_certificate(second[0][0].read_bytes()).serial_number)
        self.assertTrue(first[0][0].exists(),'Prior valid material remains for rollback')
        self.assertFalse(any(p.is_symlink() for p in self.manager.root.rglob('*')),
            'Managed data must remain compatible with no-symlink backups')
        self.assertIn('valid',self.pebble.log_path.read_text().lower())
        print('Real ACME: Certbot CSR, actual HTTP-01 fetch, trusted chain and repeat issuance passed')

    def test_http01_failure_does_not_create_active_certificate(self):
        job=self.manager.create('unresolvable.example.test','admin@example.test','production',True,True)
        self.manager.process_once()
        self.assertEqual(self.manager.job(job['id'])['state'],'failed')
        with self.assertRaises(CertificateError):self.manager.material(job['certificate_id'])
        print('Real ACME: unreachable challenge domain rejected without active material')

if __name__=='__main__':unittest.main()
