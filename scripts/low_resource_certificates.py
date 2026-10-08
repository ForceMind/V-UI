"""Disposable real Certbot/Pebble overlap; service work stays inside its cgroup.

Only synthetic .test names and an explicit loopback test CA. Private materials,
account files and challenge contents never enter the evidence directory.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import hashlib
import importlib.metadata
import json
import math
import signal
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

SOURCE = Path(__file__).resolve().parents[1]
DURATION = 600
DOMAIN = 'overlap.example.test'


def write(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def read(path):
    return json.loads(path.read_text())


def member(pid='self'):
    return Path(f'/proc/{pid}/cgroup').read_text().strip()


def wait_file(path, deadline, alive=lambda: None):
    while not path.exists():
        alive()
        if time.monotonic() >= deadline: raise RuntimeError('Certificate fixture deadline exceeded')
        time.sleep(.1)
    return read(path)


def external_group(unit):
    if not re.fullmatch(r'vui-low-resource-[0-9a-f]{32}-certificates\.service', unit):
        raise RuntimeError('Invalid disposable certificate client unit')
    return Path('/sys/fs/cgroup/system.slice') / unit


def clean_external(unit, process):
    group = external_group(unit)
    try:
        subprocess.run(['sudo','-n','systemctl','stop',unit],capture_output=True,timeout=20)
    except subprocess.TimeoutExpired:
        subprocess.run(['sudo','-n','systemctl','kill','--kill-whom=all','--signal=KILL',unit],capture_output=True,timeout=10)
    deadline = time.monotonic()+5
    while group.exists() and any(p.read_text().strip() for p in group.rglob('cgroup.procs')):
        if time.monotonic() >= deadline: raise RuntimeError('Certificate external cgroup survived cleanup')
        time.sleep(.1)
    process.wait(timeout=5)
    subprocess.run(['sudo','-n','systemctl','reset-failed',unit],capture_output=True,timeout=10)


def validate_request(value, work, unit, commit, service_report):
    from scripts.low_resource_sustained import checked_request
    checked_request(value, work, unit, commit, 10)
    if Path(value['root']).resolve()!=work.resolve()/'certificate-fixture' or Path(value['service_report']).resolve()!=service_report.resolve():
        raise RuntimeError('Certificate fixture or evidence path escaped its scope')
    ready = value.get('responder', {})
    if (type(ready.get('http_port')) is not int or not 1024 <= ready['http_port'] <= 65535
            or ready.get('unit') != unit or ready.get('source_commit') != commit
            or ready.get('service_cgroup') != member(ready.get('pid'))):
        raise RuntimeError('Certificate responder provenance mismatch')
    if unit not in ready['service_cgroup'].split('/'):
        raise RuntimeError('HTTP-01 responder escaped service accounting')


class CertificateBroker:
    def __init__(self, work, output, unit, commit):
        self.work,self.output,self.unit,self.commit=work,output,unit,commit
        self.client_unit=unit.removesuffix('.service')+'-certificates.service'
        self.stop=threading.Event();self.thread=threading.Thread(target=self.serve,daemon=True)
    def __enter__(self):
        from scripts.low_resource_sustained import outside_server
        outside_server(self.unit,member());self.thread.start();return self
    def __exit__(self,*_):
        self.stop.set();self.thread.join(timeout=60)
        if self.thread.is_alive():raise RuntimeError('Certificate broker failed to stop')
    def serve(self):
        request=self.work/'certificates.request.json';response=self.work/'certificates.response.json'
        while not request.exists():
            if self.stop.wait(.1):return
        evidence=self.output.with_name(self.output.stem+'-certificate-clients.json')
        result={'outcome':'failed'};process=None
        try:
            value=read(request);validate_request(value,self.work,self.unit,self.commit,self.output.with_name(self.output.stem+'-worker-certificate-service.json'))
            with evidence.with_suffix('.log').open('w') as log:
                command=['sudo','-n','systemd-run','--wait','--pipe','--unit',self.client_unit,
                    '--uid',str(os.getuid()),'--gid',str(os.getgid()),
                    '--property','RuntimeMaxSec=900','--property','KillMode=control-group',
                    '--property','TimeoutStopSec=5s','--property','OOMPolicy=stop',
                    '--setenv=GITHUB_ACTIONS=true','--setenv=RUNNER_ENVIRONMENT=github-hosted',
                    '--setenv=VUI_TEST_PEBBLE='+os.environ['VUI_TEST_PEBBLE'],
                    sys.executable,'-B',str(Path(__file__).resolve()),'external',
                    '--request',str(request),'--output',str(evidence)]
                process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
                deadline=time.monotonic()+900
                while process.poll() is None:
                    if self.stop.wait(.1) or time.monotonic()>=deadline:raise RuntimeError('Certificate helper deadline exceeded')
                result=read(evidence)
                if process.returncode!=0 or result.get('outcome')!='passed':raise RuntimeError('Certificate helper failed')
        except Exception as exc:
            if evidence.exists():
                try:result=read(evidence)
                except (ValueError,OSError):pass
            result.update(outcome='failed',broker_error_type=type(exc).__name__)
        finally:
            try:
                if process is not None:clean_external(self.client_unit,process)
                result['broker_cleanup_complete']=True
            except Exception as exc:
                result.update(outcome='failed',broker_cleanup_complete=False,cleanup_error=type(exc).__name__)
            write(evidence,result);write(response,result)


def new_successful_job(rows, previous, certificate_id):
    added=[row for row in rows if row['id'] not in previous]
    if (len(added)!=1 or len(rows)!=len(previous)+1 or len({row['id'] for row in rows})!=len(rows)
            or any(row['certificate_id']!=certificate_id or row['state']!='succeeded' or row['error'] is not None for row in rows)):
        raise RuntimeError('Certificate job set changed unexpectedly')
    new=added[0]
    if any(row['sequence']>=new['sequence'] for row in rows if row['id'] in previous):
        raise RuntimeError('Certificate job sequence did not advance')
    return new


def observe_certbot(root, expected_cgroup, directory, proc=Path('/proc')):
    """Read-only PID identity during an actual challenge; no provider replacement."""
    found=[]
    for entry in proc.iterdir():
        if not entry.name.isdigit():continue
        try:
            stat=entry.joinpath('stat').read_text().rsplit(')',1)[1].split()
            if int(stat[1])!=os.getpid():continue
            args=entry.joinpath('cmdline').read_bytes().rstrip(b'\0').decode().split('\0')
            if (len(args)<4 or args[1:4]!=['-B','-c','from certbot.main import main; raise SystemExit(main())']
                    or Path(args[0]).resolve()!=Path(sys.executable).resolve()):continue
            def option(name):return args[args.index(name)+1]
            if (option('--server')!=directory or Path(option('--webroot-path')).resolve()!=root/'managed/http-webroot'
                    or not Path(option('--csr')).resolve().is_relative_to(root/'managed')):continue
            membership=entry.joinpath('cgroup').read_text().strip()
            if membership!=expected_cgroup:continue
            again=entry.joinpath('stat').read_text().rsplit(')',1)[1].split()
            if again[19]!=stat[19]:continue
            found.append(dict(pid=int(entry.name),ppid=int(stat[1]),starttime_ticks=int(stat[19]),cgroup=membership,role='real-certbot'))
        except (OSError,ValueError,IndexError):continue
    return found


def observed_handler(base,root,report,active,lock,identity=member):
    class Observed(base):
        def do_GET(self):
            self.result_status=None
            super().do_GET()
            if self.result_status==200:
                with lock:
                    report['challenges'].append(dict(phase=active['phase'],status=200,monotonic=time.monotonic(),
                        token_sha256=hashlib.sha256(self.path.rsplit('/',1)[-1].encode()).hexdigest()))
        def send_response(self,code,*args):
            self.result_status=code
            if self.command=='GET' and code==200:
                try:
                    children=observe_certbot(root,identity(),active['directory'])
                    if len(children)!=1:raise RuntimeError('Expected one waiting Certbot')
                    with lock:
                        child=children[0];child.update(phase=active['phase'],observed_monotonic=time.monotonic())
                        if not any(c['pid']==child['pid'] and c['phase']==child['phase'] for c in report['certbot_processes']):
                            report['certbot_processes'].append(child)
                except Exception as exc:
                    with lock:report['observation_errors'].append(type(exc).__name__)
            return super().send_response(code,*args)
    return Observed


def service(request, output):
    """Executed by installed Python, with isolated VUI_DATA_DIR before app import."""
    if (os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted'
            or sys.platform!='linux' or os.geteuid()==0):raise RuntimeError('Only disposable hosted runner certificate service')
    payload=Path(request['payload']).resolve();root=Path(request['root'])
    unit=request['unit'];commit=request['source_commit']
    sys.path.insert(0,str(payload))
    from app.models import database
    from app.certificates.manager import CertificateManager
    from app.certificates.models import Certificate, CertificateJob
    from app.certificates.provider import CertbotProvider
    from app.certificates.http01 import ChallengeServer, handler_for
    from cryptography import x509
    if (not all(Path(module.__file__).resolve().is_relative_to(payload) for module in
                    (database,sys.modules[CertificateManager.__module__],sys.modules[CertbotProvider.__module__]))
            or Path(database.DB_PATH).resolve()!=root/'data/v-ui.db'
            or importlib.metadata.version('certbot')!='5.8.0'
            or unit not in member().split('/')):
        raise RuntimeError('Installed certificate service provenance failed')
    database.init_db()
    report=dict(outcome='running',source_commit=commit,unit=unit,service_cgroup=member(),pid=os.getpid(),
        imported_installed_app=True,isolated_database=True,certbot_version='5.8.0',jobs=[],challenges=[],certbot_processes=[],cleanup_complete=False)
    lock=threading.Lock();active={'phase':None,'directory':None}
    report['observation_errors']=[]
    def checkpoint():
        with lock:write(output,report)
    Observed=observed_handler(handler_for(root/'managed/http-webroot'),root,report,active,lock)
    server=ChallengeServer(('127.0.0.1',0),Observed)
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.05},daemon=True);thread.start()
    checkpoint()
    write(root/'responder.json',dict(http_port=server.server_port,pid=os.getpid(),service_cgroup=member(),unit=unit,source_commit=commit))
    try:
        ca=wait_file(root/'ca.json',time.monotonic()+60)
        url=urlsplit(ca['directory'])
        if url.scheme!='https' or url.hostname!='127.0.0.1' or url.path!='/dir':raise RuntimeError('Only explicit fake loopback CA allowed')
        for key in ('transport_ca','issuance_root'):
            if not Path(ca[key]).resolve(strict=True).is_relative_to(root.resolve()):raise RuntimeError('Fake CA escaped fixture')
        active['directory']=ca['directory']
        provider=CertbotProvider(root/'managed',test_directory=ca['directory'],test_ca=Path(ca['transport_ca']))
        manager=CertificateManager(root/'managed',provider,trusted_roots=Path(ca['issuance_root']).read_bytes())
        signal.signal(signal.SIGTERM,lambda *_:manager.stop_event.set())
        deadline=time.monotonic()+60
        while True:
            if time.monotonic()>deadline:raise RuntimeError('No live overlap traffic')
            if (root/'load-progress.json').exists():
                load=read(root/'load-progress.json')
                if load.get('connected_lanes')==10 and load.get('first_response_lanes')==10:break
            time.sleep(.1)
        started=load['started_monotonic'];report['load_started_monotonic']=started
        def execute(name, queue=None):
            active['phase']=name
            before=read(root/'load-progress.json')
            row=dict(name=name,started_monotonic=time.monotonic(),requests_before=before['completed_requests'])
            report['jobs'].append(row);checkpoint()
            if time.monotonic()>started+570:raise RuntimeError('Certificate work missed overlap window')
            with database.SessionLocal() as db:previous={job.id for job in db.query(CertificateJob).all()}
            if queue:queue()
            if not manager.process_once():raise RuntimeError('Certificate manager did not process a real job')
            with database.SessionLocal() as db:
                jobs=[{key:getattr(job,key) for key in ('id','certificate_id','sequence','state','error')}
                      for job in db.query(CertificateJob).order_by(CertificateJob.sequence).all()]
                report['observed_jobs']=jobs
                row.update(new_successful_job(jobs,previous,identity[0]))
            # The file is checkpointed every .1s; wait for a post-job live sample.
            finished=time.monotonic()
            while True:
                progress=read(root/'load-progress.json')
                if progress.get('observed_monotonic',0)>=finished:break
                if time.monotonic()>started+570:raise RuntimeError('No traffic progress after certificate job')
                time.sleep(.05)
            row.update(finished_monotonic=finished,requests_after=progress['completed_requests'])
            # Exact response timestamps are checked against each job window by
            # the coordinator; coarse progress counters alone are insufficient.
            checkpoint()
        identity=[]
        def create():identity.append(manager.create(DOMAIN,'admin@example.test','production',True,True)['certificate_id'])
        execute('issue',create)
        first=manager.material(identity[0]);old=[p.read_bytes() for p in first[0]]
        serial=x509.load_pem_x509_certificate(old[0]).serial_number
        with database.SessionLocal() as db:
            if db.query(CertificateJob).filter(CertificateJob.state.in_(('queued','running'))).count():raise RuntimeError('Concurrent certificate job')
            row=db.get(Certificate,identity[0]);row.renew_at=0;row.last_attempt=0;row.retry_at=0;db.commit()
        execute('scheduled_renewal')
        second=manager.material(identity[0])
        second_serial=x509.load_pem_x509_certificate(second[0][0].read_bytes()).serial_number
        if first[2]==second[2] or serial==second_serial or [p.read_bytes() for p in first[0]]!=old:raise RuntimeError('Renewal did not preserve old valid material')
        if manager.process_once():raise RuntimeError('Renewal unexpectedly queued again')
        with database.SessionLocal() as db:
            if db.query(Certificate).count()!=1 or db.query(CertificateJob).count()!=2:
                raise RuntimeError('Certificate fixture row count changed')
        report.update(no_duplicate_due_job=True,first_revision=first[2],renewed_revision=second[2],first_serial=str(serial),renewed_serial=str(second_serial),
            old_material_unchanged=True,validated_san_key_chain=True,automatic_due_scheduling=True,outcome='passed')
        if any(row['finished_monotonic']>=started+570 for row in report['jobs']):raise RuntimeError('Certificate jobs exceeded overlap deadline')
    except Exception as exc:
        report.update(outcome='failed',error_type=type(exc).__name__,error=str(exc))
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
        report['cleanup_complete']=not thread.is_alive()
        if not report['cleanup_complete']:report['outcome']='failed'
        checkpoint()
    return 0 if report['outcome']=='passed' else 1


def external(request, output):
    sys.path.insert(0,str(SOURCE));sys.path.insert(0,str(SOURCE/'tests'))
    from scripts.low_resource_acceptance import require_hosted_runner,metrics,assert_no_oom
    from scripts.low_resource_sustained import outside_server,fixed_load,persistent_target,BODY
    from scripts.low_resource_proxy import client_config,assert_no_direct_log
    from acme_helpers import PebbleFixture
    from loopback_helpers import CoreProcess,unused_port
    require_hosted_runner();outside_server(request['unit'],member())
    unit=request['unit'].removesuffix('.service')+'-certificates.service';group=external_group(unit)
    if unit not in member().split('/'):raise RuntimeError('External fixture lacks its own cgroup')
    root=Path(request['root']);value=dict(outcome='running',unit=request['unit'],source_commit=request['source_commit'],
        client_cgroup=member(),concurrency=10,requested_duration_seconds=DURATION,errors=0,cleanup_complete=False,
        body_bytes=len(BODY),body_sha256=hashlib.sha256(BODY).hexdigest(),requests_per_connection_per_second=1,
        schedule='one request per lane per second; ten lanes staggered at 100ms offsets')
    write(output,value)
    try:
        with ExitStack() as stack:
            ca_root=root/'pebble';ca_root.mkdir()
            pebble=PebbleFixture(stack,ca_root,root/'managed/http-webroot',DOMAIN,http_port=request['responder']['http_port'])
            issuance_root=ca_root/'issuance-root.pem';issuance_root.write_bytes(pebble.root_pem)
            value['pebble_cgroup']=member(pebble.process.pid)
            if value['pebble_cgroup']!=member():raise RuntimeError('Pebble escaped external accounting')
            write(root/'ca.json',dict(directory=pebble.directory,transport_ca=str(pebble.ca),issuance_root=str(issuance_root)))
            target,counts=persistent_target(stack);port=unused_port()
            config=client_config(port,request['server_port'],request['ca']);config['log']['level']='warn'
            path=root/'client.json';path.write_text(json.dumps(config))
            core=CoreProcess(stack,[request['binary'],'run','-c',str(path)],output.with_name(output.stem+'-core.log'),None);core.start(port)
            value['client_core_cgroup']=member(core.process.pid)
            if value['client_core_cgroup']!=member():raise RuntimeError('Client escaped external accounting')
            progress={};result={};error=[]
            def traffic():
                try:result.update(fixed_load(port,target.server_address[1],10,progress=progress,record_times=True,stagger=True))
                except Exception as exc:error.append(exc)
            thread=threading.Thread(target=traffic);thread.start()
            try:
                while thread.is_alive():
                    snapshot={k:v for k,v in progress.items() if k!='response_monotonic'}
                    snapshot.update(observed_monotonic=time.monotonic(),target_requests=counts['requests'])
                    write(root/'load-progress.json',snapshot)
                    value['partial_load']=snapshot;write(output,value)
                    if core.process.poll() is not None or pebble.process.poll() is not None:raise RuntimeError('External core or CA exited')
                    thread.join(.1)
            finally:
                # fixed_load has bounded per-request deadlines; stop the client on
                # outer failure so no lane survives the external unit cleanup.
                if thread.is_alive():core.process.terminate();thread.join(timeout=10)
                if thread.is_alive():raise RuntimeError('Traffic lanes did not stop')
            if error:raise error[0]
            value.update(result,target_requests=counts['requests'],target_connections=counts['connections'],response_monotonic=progress['response_monotonic'],
                load_started_monotonic=progress['started_monotonic'],first_response_lanes=progress['first_response_lanes'])
            if counts!={'requests':6000,'connections':10}:raise RuntimeError('Certificate overlap delivery mismatch')
            service_result=read(Path(request['service_report']))
            if service_result.get('outcome')!='passed':raise RuntimeError('Real certificate service failed')
            before=dict(counts);fixed_load(port,target.server_address[1],1,duration=1)
            value.update(recovery_requests=counts['requests']-before['requests'],recovery_connections=counts['connections']-before['connections'])
        assert_no_direct_log(core.log_path)
        value.update(outcome='passed',cleanup_complete=True,no_direct=True)
    except Exception as exc:
        value.update(outcome='failed',error_type=type(exc).__name__,error=str(exc))
    finally:
        value['external_metrics']=metrics(group)
        try:assert_no_oom(value['external_metrics'])
        except RuntimeError:value['outcome']='failed'
        value['external_accounting_scope']='Pebble/private DNS/client/target/generator cgroup; final wrapper cleanup excluded'
        write(output,value)
    return 0 if value['outcome']=='passed' else 1


def request_overlap(work, output, unit, commit, payload, python, binary, fixture, ca, port, observe):
    from app.release_tools import child_env
    root=work/'certificate-fixture';root.mkdir(mode=0o700)
    service_output=output.with_name(output.stem+'-certificate-service.json')
    service_request=root/'service.request.json'
    write(service_request,dict(root=str(root),payload=str(payload),unit=unit,source_commit=commit))
    with service_output.with_suffix('.log').open('w') as log:
        process=subprocess.Popen([str(python),'-B',str(Path(__file__).resolve()),'service','--request',str(service_request),'--output',str(service_output)],
            cwd=payload,env=child_env(payload,root/'data'),stdout=log,stderr=subprocess.STDOUT)
        try:
            def alive():
                observe()
                if process.poll() is not None and not (root/'responder.json').exists():raise RuntimeError('Certificate responder failed startup')
            ready=wait_file(root/'responder.json',time.monotonic()+30,alive)
            request=dict(root=str(root),unit=unit,source_commit=commit,binary=str(binary),fixture=str(fixture),ca=str(ca),server_port=port,
                duration_seconds=DURATION,concurrency=10,responder=ready,service_report=str(service_output))
            write(work/'certificates.request.json',request)
            result=wait_file(work/'certificates.response.json',time.monotonic()+800,observe)
            process.wait(timeout=5)
            if process.returncode!=0:raise RuntimeError('Certificate service exited unsuccessfully')
            service_result=read(service_output)
            validate_result(result,service_result,unit,commit)
            return dict(external=result,service=service_result)
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=12)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


def validate_metrics_record(metric):
    from scripts.low_resource_acceptance import assert_no_oom
    def integer(value):return type(value) is int and value>=0
    if not isinstance(metric,dict):raise RuntimeError('Missing resource accounting')
    for key in ('memory.current','memory.peak'):
        if not integer(metric.get(key)):raise RuntimeError('Missing memory accounting')
    if metric['memory.peak']<=0:raise RuntimeError('Missing memory peak')
    for section,keys in (('memory.events',('oom','oom_kill','oom_group_kill','max')),('memory.stat',('anon','file')),('cpu.stat',('usage_usec',))):
        if any(not integer(metric.get(section,{}).get(key)) for key in keys):raise RuntimeError('Missing resource accounting')
    assert_no_oom(metric)


def validate_service_samples(samples, roles):
    def integer(value):return type(value) is int and value>0
    if (not isinstance(samples,list) or len(samples)<100 or not isinstance(roles,dict)
            or set(roles)!={'worker','panel','proxy'}):raise RuntimeError('Incomplete service samples or roles')
    for role in roles.values():
        if any(not integer(role.get(key)) for key in ('pid','starttime_ticks')):raise RuntimeError('Incomplete role identity')
    if len({role['pid'] for role in roles.values()})!=3:raise RuntimeError('Service roles overlap')
    previous=-1;cpu=-1;peak=-1
    for sample in samples:
        stamp=sample.get('observed_monotonic')
        if type(stamp) not in (int,float) or not math.isfinite(stamp) or stamp<previous:raise RuntimeError('Invalid service timestamp')
        previous=stamp;validate_metrics_record(sample)
        if sample['cpu.stat']['usage_usec']<cpu or sample['memory.peak']<peak:raise RuntimeError('Cumulative service accounting decreased')
        cpu=sample['cpu.stat']['usage_usec'];peak=sample['memory.peak']
        processes=sample.get('processes',[])
        if not isinstance(processes,list) or not processes:raise RuntimeError('Missing service processes')
        for role in roles.values():
            match=[p for p in processes if p.get('pid')==role['pid']]
            if (len(match)!=1 or match[0].get('starttime_ticks')!=role['starttime_ticks']
                    or not integer(match[0].get('threads')) or not isinstance(match[0].get('name'),str)
                    or not match[0]['name'] or type(match[0].get('ppid')) is not int or match[0]['ppid']<0):
                raise RuntimeError('Service role disappeared or restarted')
    if samples[-1]['observed_monotonic']-samples[0]['observed_monotonic']<590:raise RuntimeError('Incomplete service duration')


def validate_result(external, service, unit, commit):
    from scripts.low_resource_sustained import outside_server,BODY
    from scripts.low_resource_acceptance import assert_no_oom
    def number(value):return type(value) in (int,float) and math.isfinite(value) and value>=0
    for field in ('wall_seconds','max_response_seconds'):
        if not number(external.get(field)):raise RuntimeError('Invalid external timing')
    times=external.get('response_monotonic',[])
    if len(times)!=6000 or any(not number(t) for t in times):raise RuntimeError('Missing exact delivery timestamps')
    for value in (external,service):
        if value.get('outcome')!='passed' or value.get('unit')!=unit or value.get('source_commit')!=commit or value.get('cleanup_complete') is not True:
            raise RuntimeError('Certificate overlap provenance or completion failed')
    outside_server(unit,external.get('client_cgroup',''))
    if (external.get('client_core_cgroup')!=external['client_cgroup'] or external.get('pebble_cgroup')!=external['client_cgroup']
            or unit not in service.get('service_cgroup','').split('/')):
        raise RuntimeError('Incorrect certificate role accounting')
    if (external.get('schedule')!='one request per lane per second; ten lanes staggered at 100ms offsets'
            or external.get('concurrency')!=10 or external.get('requested_duration_seconds')!=600
            or external.get('body_bytes')!=len(BODY) or external.get('body_sha256')!=hashlib.sha256(BODY).hexdigest()
            or external.get('requests_per_connection_per_second')!=1
            or external.get('broker_cleanup_complete') is not True or external.get('requests')!=6000
            or external.get('target_requests')!=6000 or external.get('target_connections')!=10
            or external.get('wall_seconds',0)<600 or external.get('errors')!=0
            or external.get('recovery_requests')!=1 or external.get('recovery_connections')!=1
            or external.get('no_direct') is not True):raise RuntimeError('Incomplete overlap traffic evidence')
    def integer(value):return type(value) is int and value>=0
    validate_metrics_record(external.get('external_metrics'))
    if (service.get('imported_installed_app') is not True or service.get('isolated_database') is not True
            or service.get('certbot_version')!='5.8.0' or service.get('automatic_due_scheduling') is not True
            or service.get('no_duplicate_due_job') is not True or service.get('observation_errors')!=[]
            or service.get('old_material_unchanged') is not True or service.get('validated_san_key_chain') is not True
            or service.get('first_revision')==service.get('renewed_revision')
            or service.get('first_serial')==service.get('renewed_serial')):raise RuntimeError('Incomplete real renewal evidence')
    jobs=service.get('jobs',[]);children=service.get('certbot_processes',[])
    if [r.get('name') for r in jobs]!=['issue','scheduled_renewal'] or len(children)!=2:raise RuntimeError('Expected exactly two real certificate jobs')
    new_successful_job(jobs[:1],set(),jobs[0].get('certificate_id'))
    new_successful_job(jobs,{jobs[0]['id']},jobs[0].get('certificate_id'))
    for key in ('first_revision','renewed_revision'):
        if not re.fullmatch(r'[a-f0-9]{64}',service.get(key,'')):raise RuntimeError('Invalid material digest')
    started=service['load_started_monotonic']
    if (not number(started) or started!=external.get('load_started_monotonic') or external.get('first_response_lanes')!=10
            or any(not started<=t<=started+external['wall_seconds'] for t in times) or times!=sorted(times)):
        raise RuntimeError('Invalid overlap origin or response window')
    if not integer(service.get('pid')) or service['pid']<=0:raise RuntimeError('Missing certificate service PID')
    if len({child.get('pid') for child in children})!=2:raise RuntimeError('Certificate PID identity reused')
    tokens=[]
    for row,child in zip(jobs,children):
        if not all(number(row.get(k)) for k in ('started_monotonic','finished_monotonic')):raise RuntimeError('Invalid job timing')
        if (any(not integer(child.get(key)) or child[key]<=0 for key in ('pid','ppid','starttime_ticks'))
                or child['ppid']!=service['pid'] or child.get('role')!='real-certbot'
                or not number(child.get('observed_monotonic'))
                or not row['started_monotonic']<=child['observed_monotonic']<=row['finished_monotonic']):
            raise RuntimeError('Incomplete certificate child identity')
        if (row.get('state')!='succeeded' or not started<=row['started_monotonic']<row['finished_monotonic']<started+570
                or not any(row['started_monotonic']<=t<=row['finished_monotonic'] for t in external.get('response_monotonic',[]))
                or child.get('phase')!=row['name']
                or child.get('cgroup')!=service.get('service_cgroup')):raise RuntimeError('Certificate job lacks live overlap or correct accounting')
        challenges=[c for c in service.get('challenges',[]) if c.get('phase')==row['name'] and c.get('status')==200
                    and row['started_monotonic']<=c.get('monotonic',0)<=row['finished_monotonic']]
        if not challenges:raise RuntimeError('No actual HTTP-01 fetch in certificate job')
        if any(not re.fullmatch(r'[a-f0-9]{64}',c.get('token_sha256','')) for c in challenges):raise RuntimeError('Invalid challenge digest')
        tokens.append({c['token_sha256'] for c in challenges})
    if tokens[0]&tokens[1]:raise RuntimeError('Renewal reused old challenge evidence')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mode',choices=('service','external'))
    parser.add_argument('--request',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();os.umask(0o077)
    raise SystemExit((service if args.mode=='service' else external)(read(args.request),args.output))
