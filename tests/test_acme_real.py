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

    def test_real_due_renewal_with_fresh_fake_account_always_revalidates(self):
        # Force 100% authorization reuse at the fake CA. A new account must
        # still perform real HTTP-01 for the same name, deterministically.
        import acme_helpers
        from scripts.low_resource_certificates import retain_fixture_account
        from app.certificates.models import CertificateJob
        root=self.root/'fresh-account';root.mkdir()
        events=[];phase=['issue'];original=acme_helpers.handler_for
        def tracked(webroot):
            base=original(webroot)
            class Handler(base):
                def send_response(self,code,*args):
                    if self.command=='GET' and code==200:events.append(phase[0])
                    return super().send_response(code,*args)
            return Handler
        with patch.object(acme_helpers,'handler_for',tracked):
            self.pebble=PebbleFixture(self.stack,root,root/'managed/http-webroot',authz_reuse_percent=100)
        self.manager=CertificateManager(root/'managed',
            CertbotProvider(root/'managed',test_directory=self.pebble.directory,test_ca=self.pebble.ca),
            trusted_roots=self.pebble.root_pem)
        job=self.manager.create(self.pebble.domain,'admin@example.test','production',True,True)
        self.execute(job);identity=job['certificate_id'];first=self.manager.material(identity)
        self.assertIn('issue',events)
        retained=retain_fixture_account(root);self.assertTrue(retained['retained_files_unchanged'])
        phase[0]='renewal'
        with database.SessionLocal() as db:
            row=db.get(Certificate,identity);row.renew_at=0;row.last_attempt=0;row.retry_at=0;db.commit()
        self.assertTrue(self.manager.process_once())
        with database.SessionLocal() as db:
            jobs=db.query(CertificateJob).order_by(CertificateJob.sequence).all()
            self.assertEqual(len(jobs),2);self.assertTrue(all(row.state=='succeeded' for row in jobs))
        second=self.manager.material(identity)
        self.assertIn('renewal',events);self.assertNotEqual(first[2],second[2])
        self.assertTrue(first[0][0].exists());self.assertFalse(self.manager.process_once())
        self.assertTrue((root/'retained-first-account').is_dir())
        self.assertTrue((root/'managed/accounts/production').is_dir())
        print('Real ACME: fresh fake account requires HTTP-01 even at 100% authorization reuse; due renewal passed')

    def test_background_worker_with_independent_responder_and_fresh_due_renewal(self):
        import time
        from app.certificates.models import CertificateJob
        from scripts.low_resource_certificates import read,write,retain_fixture_account
        from test_low_resource_responder import start_responder
        root=self.root/'background';root.mkdir()
        with ExitStack() as stack:
            process,ready,phase=start_responder(stack,root)
            pebble=PebbleFixture(stack,root,root/'managed/http-webroot',http_port=ready['http_port'],authz_reuse_percent=100)
            manager=CertificateManager(root/'managed',CertbotProvider(root/'managed',test_directory=pebble.directory,test_ca=pebble.ca),trusted_roots=pebble.root_pem)
            phase['directory']=pebble.directory
            manager.start();thread=manager.thread;certificate=[]
            try:
                def execute(name,arm,expected_count):
                    self.assertTrue(manager._process_lock.acquire(timeout=15))
                    try:
                        phase['phase']=name;write(root/'phase.json',phase);arm()
                    finally:manager._process_lock.release()
                    manager.wakeup.set();deadline=time.monotonic()+30
                    while True:
                        self.assertTrue(thread.is_alive());self.assertIsNone(manager.worker_error)
                        with database.SessionLocal() as db:
                            jobs=db.query(CertificateJob).order_by(CertificateJob.sequence).all()
                            self.assertFalse(any(row.state=='failed' for row in jobs),str([(row.state,row.error) for row in jobs]))
                            if len(jobs)==expected_count and all(row.state=='succeeded' for row in jobs):break
                        self.assertLess(time.monotonic(),deadline);time.sleep(.05)
                execute('issue',lambda:certificate.append(manager.create(pebble.domain,'admin@example.test','production',True,True)['certificate_id']),1)
                first=manager.material(certificate[0]);old=first[0][0].read_bytes()
                def due():
                    retain_fixture_account(root)
                    with database.SessionLocal() as db:
                        row=db.get(Certificate,certificate[0]);row.renew_at=0;row.last_attempt=0;row.retry_at=0;db.commit()
                execute('scheduled_renewal',due,2)
                second=manager.material(certificate[0]);self.assertNotEqual(first[2],second[2])
                self.assertEqual(first[0][0].read_bytes(),old)
                self.assertTrue(thread.is_alive())
            finally:
                manager.stop();self.assertFalse(thread.is_alive());self.assertIsNone(manager.worker_error)
        observation=read(root/'responder-report.json')
        self.assertEqual(process.returncode,0)
        self.assertEqual(observation['outcome'],'passed');self.assertTrue(observation['drained'])
        self.assertEqual({row['phase'] for row in observation['challenges']},{'issue','scheduled_renewal'})
        self.assertEqual(len(observation['certbot_processes']),2)
        self.assertTrue(all(row['allocator']==phase['manager_allocator'] for row in observation['certbot_processes']))
        with database.SessionLocal() as db:self.assertEqual(db.query(CertificateJob).count(),2)

    def test_http01_failure_does_not_create_active_certificate(self):
        job=self.manager.create('unresolvable.example.test','admin@example.test','production',True,True)
        self.manager.process_once()
        self.assertEqual(self.manager.job(job['id'])['state'],'failed')
        with self.assertRaises(CertificateError):self.manager.material(job['certificate_id'])
        print('Real ACME: unreachable challenge domain rejected without active material')

if __name__=='__main__':unittest.main()
