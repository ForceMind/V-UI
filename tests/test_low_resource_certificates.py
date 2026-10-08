"""Certificate overlap evidence fails closed; mocks are not real ACME acceptance."""
import copy
from contextlib import ExitStack
import json
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
from urllib.request import build_opener,ProxyHandler
from urllib.error import HTTPError
import unittest
from unittest.mock import patch,Mock

from scripts import low_resource_certificates as cert
from scripts import low_resource_acceptance as gate
from scripts.low_resource_sustained import BODY

UNIT='vui-low-resource-'+('a'*32)+'.service'
COMMIT='b'*40
SERVICE='0::/system.slice/'+UNIT
CLIENT='0::/system.slice/'+UNIT.removesuffix('.service')+'-certificates.service'


def evidence():
    external=dict(outcome='passed',unit=UNIT,source_commit=COMMIT,cleanup_complete=True,
        broker_cleanup_complete=True,client_cgroup=CLIENT,client_core_cgroup=CLIENT,pebble_cgroup=CLIENT,
        concurrency=10,requested_duration_seconds=600,schedule='one request per lane per second; ten lanes staggered at 100ms offsets',body_bytes=len(BODY),body_sha256=hashlib.sha256(BODY).hexdigest(),
        requests_per_connection_per_second=1,requests=6000,target_requests=6000,target_connections=10,
        wall_seconds=600.01,max_response_seconds=.01,errors=0,recovery_requests=1,recovery_connections=1,no_direct=True,
        response_monotonic=[100+i//10 for i in range(6000)],load_started_monotonic=100,first_response_lanes=10,
        external_metrics={'memory.current':1000,'memory.peak':2000,'memory.stat':{'anon':100,'file':100},'cpu.stat':{'usage_usec':1000},
                          'memory.events':{'oom':0,'oom_kill':0,'oom_group_kill':0,'max':0}})
    service=dict(outcome='passed',unit=UNIT,source_commit=COMMIT,cleanup_complete=True,service_cgroup=SERVICE,pid=50,
        imported_installed_app=True,isolated_database=True,certbot_version='5.8.0',automatic_due_scheduling=True,
        old_material_unchanged=True,validated_san_key_chain=True,no_duplicate_due_job=True,observation_errors=[],
        first_revision='a'*64,renewed_revision='b'*64,first_serial='100',renewed_serial='200',load_started_monotonic=100,
        jobs=[],challenges=[],certbot_processes=[],
        renewal_account_fixture=dict(policy='fresh fake ACME account for deterministic renewal revalidation; original retained',
            retained_file_count=3,retained_files_unchanged=True,fresh_account_key_distinct=True))
    for index,name in enumerate(('issue','scheduled_renewal')):
        start=110+index*10
        service['jobs'].append(dict(name=name,id=str(index),certificate_id='fixture',sequence=index+1,state='succeeded',error=None,
            started_monotonic=start,finished_monotonic=start+5,requests_before=100,requests_after=110))
        service['challenges'].append(dict(phase=name,status=200,monotonic=start+2,token_sha256=str(index)*64))
        service['certbot_processes'].append(dict(phase=name,pid=index+100,ppid=50,starttime_ticks=1000+index,role='real-certbot',cgroup=SERVICE,observed_monotonic=start+2))
    return external,service


class CertificateResourceTests(unittest.TestCase):
    def test_service_cannot_start_outside_explicit_hosted_runner(self):
        with patch.dict(os.environ,{},clear=True),self.assertRaises(RuntimeError):cert.service({},Path('/unused'))

    def test_complete_evidence_and_original_limits(self):
        cert.validate_result(*evidence(),UNIT,COMMIT)
        self.assertEqual(gate.runtime_seconds(SimpleNamespace(duration_profile='certificates',memory_mib=512)),1800)
        for memory in (320,384):
            with self.assertRaises(RuntimeError):gate.duration_profile(SimpleNamespace(duration_profile='certificates',memory_mib=memory))
        self.assertEqual(gate.runtime_seconds(SimpleNamespace(duration_profile='smoke',memory_mib=320)),600)

    def test_incomplete_traffic_accounting_and_provenance_rejected(self):
        mutations=[('source_commit','c'*40),('unit','other'),('cleanup_complete',False),('broker_cleanup_complete',False),
            ('requests',5999),('target_requests',5999),('target_connections',11),('wall_seconds',599.9),('wall_seconds',float('nan')),
            ('max_response_seconds',float('inf')),('schedule','unverified'),('concurrency',1),('requested_duration_seconds',60),('body_bytes',1),
            ('body_sha256','a'*64),('requests_per_connection_per_second',2),('errors',1),('recovery_requests',0),
            ('recovery_connections',0),('no_direct',False),('client_cgroup',SERVICE),('client_core_cgroup',SERVICE),
            ('pebble_cgroup',SERVICE),('load_started_monotonic',99),('first_response_lanes',9),('response_monotonic',[]),('response_monotonic',[float('nan')]*6000),
            ('external_metrics',{'memory.events':{'oom':1,'oom_kill':0}}),('external_metrics',{'memory.events':{'oom':0}})]
        for key,value in mutations:
            external,service=evidence();external[key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):cert.validate_result(external,service,UNIT,COMMIT)

    def test_real_jobs_material_and_challenge_evidence_required(self):
        for key,value in [('outcome','running'),('cleanup_complete',False),('imported_installed_app',False),('isolated_database',False),
            ('certbot_version','other'),('automatic_due_scheduling',False),('no_duplicate_due_job',False),
            ('old_material_unchanged',False),('validated_san_key_chain',False),('observation_errors',['OSError']),
            ('first_revision','b'*64),('first_serial','200'),('challenges',[]),('certbot_processes',[]),('service_cgroup',CLIENT)]:
            external,service=evidence();service[key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):cert.validate_result(external,service,UNIT,COMMIT)
        for changed in ('before','after','no_overlap','failed','reused_token','wrong_child','wrong_certificate','old_sequence'):
            external,service=evidence();job=service['jobs'][1]
            if changed=='before':job['started_monotonic']=99
            elif changed=='after':job['finished_monotonic']=671
            elif changed=='no_overlap':external['response_monotonic']=[100]*6000
            elif changed=='failed':job['state']='failed'
            elif changed=='reused_token':service['challenges'][1]['token_sha256']=service['challenges'][0]['token_sha256']
            elif changed=='wrong_child':service['certbot_processes'][1]['cgroup']=CLIENT
            elif changed=='wrong_certificate':job['certificate_id']='other'
            else:job['sequence']=1
            with self.subTest(changed=changed),self.assertRaises(RuntimeError):cert.validate_result(external,service,UNIT,COMMIT)

    def test_exactly_one_new_successful_job(self):
        jobs=evidence()[1]['jobs']
        self.assertEqual(cert.new_successful_job(jobs,{'0'},'fixture')['id'],'1')
        for rows in (jobs[:1],jobs+[dict(jobs[1],id='extra')],jobs+[jobs[1]]):
            with self.assertRaises(RuntimeError):cert.new_successful_job(rows,{'0'},'fixture')
        bad=copy.deepcopy(jobs);bad[1]['error']='ACME_VALIDATION_FAILED'
        with self.assertRaises(RuntimeError):cert.new_successful_job(bad,{'0'},'fixture')

    def test_read_only_certbot_identity_not_same_named_python(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);proc=root/'proc';proc.mkdir();entry=proc/'123';entry.mkdir()
            fixture=root/'fixture';fixture.mkdir()
            def setup(parent=os.getpid(),membership=SERVICE,server='https://127.0.0.1:12345/dir',webroot=None,command=None):
                fields=['S',str(parent)]+['0']*17+['900']
                (entry/'stat').write_text('123 (python3) '+' '.join(fields))
                args=command or [cert.sys.executable,'-B','-c','from certbot.main import main; raise SystemExit(main())',
                    '--server',server,'--webroot-path',str(webroot or fixture/'managed/http-webroot'),
                    '--csr',str(fixture/'managed/.issue-fixture/request.pem')]
                (entry/'cmdline').write_bytes(b'\0'.join(x.encode() for x in args)+b'\0')
                (entry/'cgroup').write_text(membership)
            setup();found=cert.observe_certbot(fixture,SERVICE,'https://127.0.0.1:12345/dir',proc)
            self.assertEqual(len(found),1);self.assertEqual(found[0]['pid'],123);self.assertEqual(found[0]['starttime_ticks'],900)
            for options in ({'parent':1},{'membership':CLIENT},{'server':'https://acme-v02.api.letsencrypt.org/directory'},
                {'webroot':root/'other'},{'command':[cert.sys.executable,'-c','print(1)']}):
                setup(**options)
                with self.subTest(options=options):self.assertEqual(cert.observe_certbot(fixture,SERVICE,'https://127.0.0.1:12345/dir',proc),[])
            setup();(entry/'stat').unlink()
            self.assertEqual(cert.observe_certbot(fixture,SERVICE,'https://127.0.0.1:12345/dir',proc),[])

    def test_observer_never_changes_real_http01_response(self):
        from app.certificates.http01 import ChallengeServer,handler_for
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);webroot=root/'managed/http-webroot'
            token='a'*24;path=webroot/'.well-known/acme-challenge'/token
            path.parent.mkdir(parents=True);path.write_text(token+'.synthetic')
            for fails in (False,True):
                report=dict(challenges=[],certbot_processes=[],observation_errors=[])
                handler=cert.observed_handler(handler_for(webroot),root,report,{'phase':'issue','directory':'https://127.0.0.1:12345/dir'},threading.Lock())
                server=ChallengeServer(('127.0.0.1',0),handler)
                thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01});thread.start()
                try:
                    with patch.object(cert,'observe_certbot',side_effect=OSError('synthetic') if fails else None,
                                      return_value=[dict(pid=123,ppid=os.getpid(),cgroup=SERVICE,starttime_ticks=1)]) as observed:
                        opener=build_opener(ProxyHandler({}));origin='http://127.0.0.1:'+str(server.server_port)
                        with opener.open(origin+'/.well-known/acme-challenge/'+token) as response:
                            self.assertEqual(response.status,200);self.assertEqual(response.read(),(token+'.synthetic').encode())
                        from urllib.request import Request
                        with opener.open(Request(origin+'/.well-known/acme-challenge/'+token,method='HEAD')) as response:
                            self.assertEqual(response.read(),b'')
                        with self.assertRaises(HTTPError):opener.open(origin+'/not-a-challenge')
                        self.assertEqual(observed.call_count,1)
                finally:server.shutdown();server.server_close();thread.join(3)
                self.assertEqual(len(report['challenges']),1)
                self.assertEqual(report['challenges'][0]['token_sha256'],hashlib.sha256(token.encode()).hexdigest())
                self.assertEqual(len(report['observation_errors']),int(fails))
                self.assertEqual(len(report['certbot_processes']),int(not fails))

    def test_service_samples_require_complete_stable_roles_and_accounting(self):
        roles={name:dict(pid=index+1,starttime_ticks=100+index) for index,name in enumerate(('worker','panel','proxy'))}
        metric=evidence()[0]['external_metrics']
        samples=[dict(copy.deepcopy(metric),observed_monotonic=100+i*6,
                      processes=[dict(role,threads=1,name=name,ppid=0) for name,role in roles.items()]) for i in range(101)]
        cert.validate_service_samples(samples,roles)
        for mutation in ('nan','reverse','short','cpu','memory','events','processes','restart','worker','panel','proxy','decrease'):
            bad=copy.deepcopy(samples)
            if mutation=='nan':bad[0]['observed_monotonic']=float('nan')
            elif mutation=='reverse':bad[1]['observed_monotonic']=0
            elif mutation=='short':bad=bad[:10]
            elif mutation=='cpu':bad[5]['cpu.stat']={}
            elif mutation=='memory':bad[5]['memory.peak']=0
            elif mutation=='events':bad[5]['memory.events']={}
            elif mutation=='processes':bad[5]['processes']=[]
            elif mutation=='restart':bad[5]['processes'][0]['starttime_ticks']=999
            elif mutation=='decrease':bad[5]['cpu.stat']['usage_usec']=0
            else:bad[5]['processes']=[p for p in bad[5]['processes'] if p['name']!=mutation]
            with self.subTest(mutation=mutation),self.assertRaises(RuntimeError):cert.validate_service_samples(bad,roles)

    def test_external_ca_does_not_create_an_unaccounted_responder(self):
        import acme_helpers
        with tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
            root=Path(directory);process=Mock();process.poll.return_value=None
            opener=Mock();opener.open.return_value.__enter__=Mock(return_value=Mock(read=Mock(return_value=b'fake-root')))
            opener.open.return_value.__exit__=Mock(return_value=False)
            with patch.object(acme_helpers,'certificate_files',return_value=(root/'ca',root/'cert',root/'key')), \
                 patch.object(acme_helpers,'ThreadingHTTPServer') as http, \
                 patch.object(acme_helpers,'start_dual_dns',return_value=12346), \
                 patch.object(acme_helpers,'unused_port',side_effect=[12347,12348,12349]), \
                 patch.object(acme_helpers.ssl,'create_default_context'), \
                 patch.object(acme_helpers,'build_opener',return_value=opener), \
                 patch.object(acme_helpers.subprocess,'Popen',return_value=process), \
                 patch.dict(os.environ,{'VUI_TEST_PEBBLE':'/fake/pebble','PEBBLE_VA_ALWAYS_VALID':'1'}):
                fixture=acme_helpers.PebbleFixture(stack,root,root/'webroot',http_port=12345)
                self.assertIsNone(fixture.http);http.assert_not_called()
                self.assertEqual(json.loads((root/'pebble.json').read_text())['pebble']['httpPort'],12345)
                environment=acme_helpers.subprocess.Popen.call_args.kwargs['env']
                self.assertNotIn('PEBBLE_VA_ALWAYS_VALID',environment)
                self.assertEqual(environment['PEBBLE_AUTHZREUSE'],'0')

    def test_old_fake_account_is_retained_and_never_reused_or_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'managed/accounts/production';source.mkdir(parents=True)
            (source/'private_key.json').write_text('synthetic-key');(source/'regr.json').write_text('synthetic-registration')
            result=cert.retain_fixture_account(root)
            self.assertTrue(result['retained_files_unchanged']);self.assertFalse(source.exists())
            self.assertEqual((root/'retained-first-account/private_key.json').read_text(),'synthetic-key')
            source.mkdir();(source/'private_key.json').write_text('second-synthetic-key')
            with self.assertRaises(RuntimeError):cert.retain_fixture_account(root)
            self.assertEqual((source/'private_key.json').read_text(),'second-synthetic-key')

    def test_scoped_external_unit(self):
        self.assertEqual(cert.external_group(UNIT.removesuffix('.service')+'-certificates.service').name,
                         UNIT.removesuffix('.service')+'-certificates.service')
        for unit in ('ssh.service',UNIT,'../anything'):
            with self.assertRaises(RuntimeError):cert.external_group(unit)


if __name__=='__main__':unittest.main()
