"""Large synthetic data and stopped backup workload, never production data."""
from __future__ import annotations
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler,ProxyHandler,Request,build_opener

SOURCE=Path(__file__).resolve().parents[1]
NODE_COUNT=1000
LOG_BYTES=256*1024*1024
PATHS=('/api/subscription/raw','/api/subscription/sing-box.json','/api/subscription/mihomo.yaml','/api/routing/mihomo/preview')


def write(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp');temporary.write_text(json.dumps(value,indent=2)+'\n');temporary.replace(path)


def routing_fixture():
    return dict(mode='standard',direct_domains=[f'direct-{n:04d}.example.test' for n in range(512)],
        proxy_domains=[f'proxy-{n:04d}.example.test' for n in range(512)],presets={},bypass_cgnat=False,
        intranet=[dict(suffix=f'corp-{n:04d}.example.test',nameservers=['192.0.2.53','198.51.100.53']) for n in range(64)])


def digest(path):
    result=hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):result.update(block)
    return result.hexdigest()


def check_body(path,raw):
    if len(raw)>8*1024*1024 or b'PRIVATE KEY' in raw or b'certificate_path' in raw or b'key_path' in raw:
        raise RuntimeError('Large export leaked server material or exceeded response bound')
    uuids={f'11111111-1111-1111-1111-{n+1:012d}' for n in range(NODE_COUNT)}
    if path==PATHS[0]:
        decoded=base64.b64decode(raw,validate=True)
        if any(marker in decoded.lower() for marker in (b'private key',b'certificate_path',b'key_path',b'private_key')):
            raise RuntimeError('Decoded raw export leaked server material')
        rows=decoded.decode().splitlines()
        if len(rows)!=NODE_COUNT or len(set(rows))!=NODE_COUNT or any(not row.startswith('vless://') for row in rows) or {urlsplit(row).username for row in rows}!=uuids:
            raise RuntimeError('Raw export lost synthetic nodes')
        return
    if path==PATHS[1]:
        value=json.loads(raw);rows=[row for row in value['outbounds'] if row.get('type')=='vless']
        if len(rows)!=NODE_COUNT or len({row['tag'] for row in rows})!=NODE_COUNT or {row.get('uuid') for row in rows}!=uuids:raise RuntimeError('Sing-box export lost synthetic nodes')
        return
    if path==PATHS[2]:
        import yaml
        value=yaml.safe_load(raw);rows=value['proxies'];rules=value['rules']
        if len(rows)!=NODE_COUNT or len({row['name'] for row in rows})!=NODE_COUNT or {row.get('uuid') for row in rows}!=uuids:raise RuntimeError('Mihomo export lost synthetic nodes')
    else:
        value=json.loads(raw);rules=[rule for section in value['sections'] for rule in section['rules']]
        if value.get('source')!='saved':raise RuntimeError('Preview ignored saved routing')
    wanted=routing_fixture();rules=set(rules);dns=value['dns']['nameserver-policy']
    for field,policy in (('direct_domains','DIRECT'),('proxy_domains','FORCE_PROXY')):
        for domain in wanted[field]:
            if f'DOMAIN-SUFFIX,{domain},{policy}' not in rules:raise RuntimeError('Large routing semantics changed')
    for zone in wanted['intranet']:
        if (f"DOMAIN-SUFFIX,{zone['suffix']},DIRECT" not in rules
                or dns.get('+.'+zone['suffix'])!=['udp://192.0.2.53:53','udp://198.51.100.53:53']):
            raise RuntimeError('Intranet routing semantics changed')


def validate_request(value,work,unit,commit):
    origin=urlsplit(value.get('origin',''))
    if (value.get('unit')!=unit or value.get('source_commit')!=commit or value.get('node_count')!=NODE_COUNT
            or origin.scheme!='https' or origin.hostname!='127.0.0.1' or not origin.port
            or origin.path or origin.query or origin.fragment or origin.username or origin.password):
        raise RuntimeError('Invalid scoped large-data request')
    if not Path(value['ca']).resolve(strict=True).is_relative_to(work.resolve()):raise RuntimeError('CA escaped disposable data fixture')
    if not re.fullmatch(r'__Host-vui_session=[A-Za-z0-9_-]{43}',value.get('cookie','')):raise RuntimeError('Invalid synthetic session')


class DataBroker:
    def __init__(self,work,output,unit,commit):
        self.work,self.output,self.unit,self.commit=work,output,unit,commit
        self.client_unit=unit.removesuffix('.service')+'-data.service'
        self.stop=threading.Event();self.thread=threading.Thread(target=self.serve,daemon=True)
    def __enter__(self):
        from scripts.low_resource_sustained import outside_server,membership
        outside_server(self.unit,membership());self.thread.start();return self
    def __exit__(self,*_):
        self.stop.set();self.thread.join(timeout=60)
        if self.thread.is_alive():raise RuntimeError('Large-data broker did not stop')
    def serve(self):
        from scripts.low_resource_certificates import clean_external
        request=self.work/'data.request.json';response=self.work/'data.response.json'
        while not request.exists():
            if self.stop.wait(.1):return
        evidence=self.output.with_name(self.output.stem+'-data-clients.json');value={'outcome':'failed'};process=None
        try:
            config=json.loads(request.read_text());validate_request(config,self.work,self.unit,self.commit)
            with evidence.with_suffix('.log').open('w') as log:
                process=subprocess.Popen(['sudo','-n','systemd-run','--wait','--pipe','--unit',self.client_unit,
                    '--uid',str(os.getuid()),'--gid',str(os.getgid()),'--property','RuntimeMaxSec=600',
                    '--property','TimeoutStopSec=5s','--property','KillMode=control-group','--property','OOMPolicy=stop',
                    '--setenv=GITHUB_ACTIONS=true','--setenv=RUNNER_ENVIRONMENT=github-hosted',
                    sys.executable,'-B',str(Path(__file__).resolve()),'--request',str(request),'--output',str(evidence)],stdout=log,stderr=subprocess.STDOUT)
                deadline=time.monotonic()+600
                while process.poll() is None:
                    if self.stop.wait(.1) or time.monotonic()>=deadline:raise RuntimeError('Large-data client deadline exceeded')
                value=json.loads(evidence.read_text())
                if process.returncode or value.get('outcome')!='passed':raise RuntimeError('Large-data client failed')
        except Exception as exc:
            if evidence.exists():
                try:value=json.loads(evidence.read_text())
                except (ValueError,OSError):pass
            value.update(outcome='failed',broker_error_type=type(exc).__name__)
        finally:
            try:
                if process is not None:clean_external(self.client_unit,process)
                value['broker_cleanup_complete']=True
            except Exception as exc:value.update(outcome='failed',broker_cleanup_complete=False,cleanup_error=type(exc).__name__)
            write(evidence,value);write(response,value)


def run(request,output):
    sys.path.insert(0,str(SOURCE))
    from scripts.low_resource_acceptance import require_hosted_runner,metrics,assert_no_oom
    from scripts.low_resource_certificates import external_group
    from scripts.low_resource_interactions import NoRedirect
    from scripts.low_resource_sustained import membership,outside_server
    require_hosted_runner();outside_server(request['unit'],membership())
    client_unit=request['unit'].removesuffix('.service')+'-data.service';group=external_group(client_unit)
    if client_unit not in membership().split('/'):raise RuntimeError('Large-data client lacks its own cgroup')
    report=dict(outcome='running',unit=request['unit'],source_commit=request['source_commit'],node_count=NODE_COUNT,
        client_cgroup=membership(),phases=[],cleanup_complete=False)
    context=ssl.create_default_context(cafile=request['ca'])
    def get(path,deadline):
        opener=build_opener(ProxyHandler({}),HTTPSHandler(context=context),NoRedirect())
        with opener.open(Request(request['origin']+path,headers={'Cookie':request['cookie']}),timeout=min(30,max(.1,deadline-time.monotonic()))) as response:
            raw=response.read(8*1024*1024+1)
            if response.status!=200 or len(raw)>8*1024*1024:raise RuntimeError('Export response failed')
            return raw
    try:
        for path in PATHS:
            row=dict(path=path,outcome='running',completed=0,errors=0,baseline=0,serial=0,concurrent=0,concurrency=10,max_response_seconds=0)
            report['phases'].append(row);write(output,report)
            begin=time.monotonic();deadline=begin+120;lock=threading.Lock()
            try:
                raw=get(path,deadline);check_body(path,raw)
                expected=hashlib.sha256(raw).hexdigest();row.update(baseline=1,body_bytes=len(raw),body_sha256=expected,semantics_verified=True)
                def one(kind):
                    before=time.monotonic()
                    try:
                        raw=get(path,deadline)
                        if hashlib.sha256(raw).hexdigest()!=expected:raise RuntimeError('Large export body changed')
                        with lock:
                            row['completed']+=1;row[kind]+=1
                            row['max_response_seconds']=max(row['max_response_seconds'],time.monotonic()-before)
                    except Exception:
                        with lock:row['errors']+=1
                        raise
                for _ in range(30):one('serial');write(output,report)
                with ThreadPoolExecutor(max_workers=10) as pool:list(pool.map(one,['concurrent']*10))
                if time.monotonic()>deadline:raise RuntimeError('Large export exceeded fixed phase deadline')
                row['outcome']='passed'
            finally:
                row['wall_seconds']=time.monotonic()-begin;write(output,report)
        report.update(outcome='passed',cleanup_complete=True)
    except Exception as exc:report.update(outcome='failed',error_type=type(exc).__name__)
    finally:
        report['external_metrics']=metrics(group)
        try:assert_no_oom(report['external_metrics'])
        except RuntimeError:report['outcome']='failed'
        write(output,report)
    return 0 if report['outcome']=='passed' else 1


def request_exports(work,output,unit,commit,origin,ca,cookie,observe):
    request=dict(unit=unit,source_commit=commit,origin=origin,ca=str(ca),cookie=cookie,node_count=NODE_COUNT)
    write(work/'data.request.json',request);deadline=time.monotonic()+650
    while not (work/'data.response.json').exists():
        observe()
        if time.monotonic()>deadline:raise RuntimeError('Large-data response deadline exceeded')
        time.sleep(.2)
    result=json.loads((work/'data.response.json').read_text());validate_exports(result,unit,commit);return result


def validate_exports(value,unit,commit):
    from scripts.low_resource_sustained import outside_server
    from scripts.low_resource_certificates import validate_metrics_record
    if (value.get('outcome')!='passed' or value.get('unit')!=unit or value.get('source_commit')!=commit
            or value.get('node_count')!=NODE_COUNT or value.get('cleanup_complete') is not True or value.get('broker_cleanup_complete') is not True
            or [p.get('path') for p in value.get('phases',[])]!=list(PATHS)):raise RuntimeError('Incomplete large-data export evidence')
    outside_server(unit,value.get('client_cgroup',''));validate_metrics_record(value.get('external_metrics'))
    for row in value['phases']:
        if (row.get('outcome')!='passed' or row.get('completed')!=40 or row.get('baseline')!=1 or row.get('serial')!=30
                or row.get('concurrent')!=10 or row.get('concurrency')!=10 or row.get('errors')!=0
                or row.get('semantics_verified') is not True or type(row.get('body_bytes')) is not int or not 0<row['body_bytes']<=8*1024*1024
                or not re.fullmatch(r'[a-f0-9]{64}',row.get('body_sha256',''))):raise RuntimeError('Incomplete large-data phase')
        for key in ('wall_seconds','max_response_seconds'):
            if type(row.get(key)) not in (int,float) or not math.isfinite(row[key]) or not 0<row[key]<=120:raise RuntimeError('Invalid large-data timing')


def generate_log_fixture(data):
    disk_free=shutil.disk_usage(data).free
    if disk_free<4*1024**3:raise RuntimeError('Large-data fixture requires 4GiB disk headroom')
    folder=data/'resource-fixture/logs';folder.mkdir(parents=True,mode=0o700)
    path=folder/'large.log'
    if path.exists():raise RuntimeError('Refusing to reuse large log fixture')
    # 128-byte nonzero textual records, unique sequence and deterministic digest.
    block=b''.join((f'{n:08x} '+hashlib.sha256(str(n).encode()).hexdigest()).ljust(127).encode()+b'\n' for n in range(8192))
    h=hashlib.sha256()
    with path.open('xb') as handle:
        for _ in range(LOG_BYTES//len(block)):handle.write(block);h.update(block)
        handle.flush();os.fsync(handle.fileno())
    path.chmod(0o600)
    small={}
    for n in range(64):
        item=folder/f'small-{n:03d}.log';raw=(hashlib.sha256(str(n).encode()).hexdigest()*64).encode()
        with item.open('xb') as handle:handle.write(raw);handle.flush();os.fsync(handle.fileno())
        item.chmod(0o600);small[item.name]=hashlib.sha256(raw).hexdigest()
    if path.stat().st_size!=LOG_BYTES or path.stat().st_blocks*512<LOG_BYTES:raise RuntimeError('Large log is incomplete or sparse')
    return dict(large_bytes=LOG_BYTES,large_sha256=h.hexdigest(),small_file_count=64,small_file_bytes=4096,small_sha256=small,
        generator='1MiB deterministic textual blocks repeated; no sparse or zero-filled file',allocated_bytes=path.stat().st_blocks*512,
        disk_free_before_bytes=disk_free,data_allocated_file_bytes=sum(p.lstat().st_blocks*512 for p in data.rglob('*') if p.is_file()))


def verify_log_fixture(data,evidence):
    folder=data/'resource-fixture/logs'
    if (folder/'large.log').stat().st_size!=LOG_BYTES or digest(folder/'large.log')!=evidence['large_sha256']:raise RuntimeError('Large log did not restore')
    for name,expected in evidence['small_sha256'].items():
        if (folder/name).stat().st_size!=4096 or digest(folder/name)!=expected:raise RuntimeError('Small log did not restore')
    if len(list(folder.iterdir()))!=65:raise RuntimeError('Log fixture file count changed')


def business_digest(data):
    with sqlite3.connect(data/'v-ui.db') as db:
        rows=db.execute('SELECT id,core,remark,enable,port,protocol,settings,stream_settings,tag FROM inbounds ORDER BY id').fetchall()
        if len(rows)!=NODE_COUNT:raise RuntimeError('Large data node count changed')
    return hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest()




def seed_nodes(python,payload,data,cert,key):
    from app.release_tools import child_env
    code=r"""import sys
from app.models.database import SessionLocal,Inbound
with SessionLocal() as db:
    if db.query(Inbound).count():raise RuntimeError('Fixture requires empty data')
    for n in range(1000):
        db.add(Inbound(core='sing-box',remark=f'resource-node-{n:04d}',tag=f'resource-{n:04d}',enable=True,
            port=20000+n,protocol='vless',settings={'users':[{'uuid':f'11111111-1111-1111-1111-{n+1:012d}','flow':''}]},
            stream_settings={'tls':{'enabled':True,'server_name':'vpn.example.test','certificate_path':sys.argv[1],'key_path':sys.argv[2]}}))
    db.commit()
"""
    subprocess.run([str(python),'-B','-c',code,str(cert),str(key)],cwd=payload,env=child_env(payload,data),check=True,timeout=30)


def installed_operation(python,payload,data,root,operation,archive,expected=None):
    from app.release_tools import child_env
    code=r'''import json,sys
from pathlib import Path
from app import release_tools as tools
payload,root,operation,archive,expected=map(str,sys.argv[1:])
if not Path(tools.__file__).resolve().is_relative_to(Path(payload).resolve()):raise RuntimeError('Installed backup implementation required')
try:
    if operation in ('backup','reject_live_backup'):result=tools.backup(Path(root),Path(archive))
    else:tools.restore(Path(root),Path(archive),expected);result=None
except tools.ReleaseError as exc:
    if operation=='reject_live_backup' and str(exc)=='Service or another operation is active; stop it first':print(json.dumps({'rejected':True,'reason':'running panel lease'}))
    elif operation=='reject_bad_digest' and str(exc)=='Archive checksum mismatch':print(json.dumps({'rejected':True,'reason':'archive digest'}))
    else:raise
else:
    if operation.startswith('reject_'):raise RuntimeError('Expected archive operation rejection')
    print(json.dumps({'completed':True,'digest':result}))
'''
    result=subprocess.run([str(python),'-B','-c',code,str(payload),str(root),operation,str(archive),expected or ''],
        cwd=payload,env=child_env(payload,data),capture_output=True,text=True,timeout=240)
    if result.returncode:raise RuntimeError('Installed archive operation failed: '+operation)
    return json.loads(result.stdout)


def current_snapshot(root,data):
    files={}
    for path in sorted(data.rglob('*')):
        if path.is_symlink():raise RuntimeError('Unexpected data symlink')
        if path.is_file() and path.name!='owner.lock' and not path.name.endswith(('.db-wal','.db-shm')):
            files[path.relative_to(data).as_posix()]=digest(path)
    return dict(files=files,current=digest(root/'CURRENT.json'),recovery=sorted(p.name for p in (root/'recovery').glob('*')))


def mutate_after_backup(data):
    with sqlite3.connect(data/'v-ui.db') as db:db.execute("UPDATE inbounds SET remark='changed-after-backup' WHERE id=(SELECT MIN(id) FROM inbounds)")
    route=data/'mihomo-routing.json';value=json.loads(route.read_text());value['direct_domains'][0]='changed-after-backup.example.test';route.write_text(json.dumps(value))
    with (data/'resource-fixture/logs/large.log').open('r+b') as handle:handle.write(b'CHANGED!');handle.flush();os.fsync(handle.fileno())


def verify_archive(root,data,archive,log_evidence):
    import zipfile
    with zipfile.ZipFile(archive) as bundle:
        names=bundle.namelist()
        if 'resource-fixture/logs/large.log' not in names or len([n for n in names if n.startswith('resource-fixture/logs/')])!=65:
            raise RuntimeError('Large log files absent from real archive')
        if bundle.getinfo('MANIFEST.json').file_size>4_000_000:raise RuntimeError('Oversized backup manifest')
        manifest=json.loads(bundle.read('MANIFEST.json'))
        for name,wanted in {'large.log':log_evidence['large_sha256'],**log_evidence['small_sha256']}.items():
            info=manifest['files']['resource-fixture/logs/'+name]
            expected_size=LOG_BYTES if name=='large.log' else 4096
            if info['mode']!=0o600 or info['size']!=expected_size or info['sha256']!=wanted:raise RuntimeError('Archive log manifest mismatch')
        total=sum(info.file_size for info in bundle.infolist())
    return dict(archive_bytes=archive.stat().st_size,expanded_bytes=total,sha256=digest(archive),
        log_bytes=log_evidence['large_bytes'],compression_ratio=archive.stat().st_size/total,
        archive_contains_log_files=True,archive_source='installed app.release_tools',
        installed_root_allocated_file_bytes=sum(p.lstat().st_blocks*512 for p in root.rglob('*') if p.is_file()))


def verify_recovery(root,data,original,mutated,log_evidence):
    if business_digest(data)!=original['business'] or digest(data/'mihomo-routing.json')!=original['routing']:raise RuntimeError('Large business data did not restore')
    verify_log_fixture(data,log_evidence)
    with sqlite3.connect(data/'v-ui.db') as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Restored DB is damaged')
        if db.execute('SELECT COUNT(*) FROM admin_sessions').fetchone()[0]!=0:raise RuntimeError('Sessions survived restore')
        grants=db.execute('SELECT revoked FROM subscription_grants').fetchall()
        if grants!=[(1,)]:raise RuntimeError('Synthetic subscription grant not revoked')
    prior=[p for p in (root/'recovery').glob('before-*') if p.name not in mutated['recovery']]
    if len(prior)!=1:raise RuntimeError('Original data recovery copy absent')
    for name,wanted in mutated['files'].items():
        if digest(prior[0]/name)!=wanted:raise RuntimeError('Pre-restore data was not retained intact')
    if digest(root/'CURRENT.json')!=mutated['current']:raise RuntimeError('Restore changed selected code')
    if (root/'RESTORE_PENDING.json').exists() or list(root.glob('.backup-*')) or list(root.glob('.restore-*')):
        raise RuntimeError('Archive operation left staging or recovery journal')
    return dict(business_restored=True,routing_restored=True,log_files_restored=True,database_integrity=True,
        old_sessions_revoked=True,old_grant_revoked=True,pre_restore_data_preserved=True,selected_code_unchanged=True,temporary_cleanup_complete=True)


REQUIRED_STAGES=(
    'seed_1000_synthetic_nodes','large_synthetic_nodes_not_running','large_rules_and_real_conditional_write_rejections',
    'large_anonymous_export_denied','create_256_node_scoped_grant','synthetic_public_grant_before_backup',
    'large_four_format_exports_serial_and_concurrent','large_live_backup_rejected_without_stopping_panel',
    'stopped_panel','generate_256MiB_and_64_small_logs_inside_service','stopped_backup','mutate_large_data_after_backup',
    'large_bad_digest_restore_preserves_current_data','stopped_restore','large_business_logs_revocation_and_original_data_retained',
    'restore_data_and_session_revocation','restored_https_startup','old_session_after_restore_denied',
    'old_subscription_after_large_restore_denied','restored_login','restored_large_nodes_not_running',
    'restored_large_exports_and_rules','restored_logout','final_panel_stop','large_subscription_token_absent_from_logs')



BACKUP_BOUNDARIES=('before_installed_backup','after_installed_backup_before_verification','after_archive_verification')


LIFECYCLE_BOUNDARIES=('before_current_snapshot','after_current_snapshot',
    'before_installed_bad_digest_restore','after_installed_bad_digest_rejection_before_preservation_check',
    'after_bad_digest_preservation_check','before_installed_restore','after_installed_restore',
    'before_recovery_verification','after_recovery_verification')
LIFECYCLE_LIMITS={'max_rows':9,'max_row_bytes':16*1024,'max_total_bytes':128*1024}
LIFECYCLE_SCOPE=('Nine fixed same-cgroup snapshots around unchanged lifecycle calls; the first two occupy the '
    'un-staged gap after mutation. Snapshot observer costs include identity and metric reads only; validation, '
    'compact JSON serialization and checkpoint work are not captured by those costs and remain charged to the '
    'service group. A successful path adds exactly nine checkpoints. Byte caps cover compact sorted-key ASCII '
    'JSON rows, not whole checkpoint I/O. '
    'Lifetime peak and cumulative event deltas include observers and intervening checkpoints; they do not '
    'establish single-operation causation or peak composition.')
_LIFECYCLE_EVENTS=('max','oom','oom_kill','oom_group_kill')
_LIFECYCLE_STAGES=('large_bad_digest_restore_preserves_current_data','stopped_restore',
    'large_business_logs_revocation_and_original_data_retained')


def _lifecycle_number(value):
    return type(value) in (int,float) and 0<=value<=sys.float_info.max and math.isfinite(value)


def _lifecycle_metrics(metric,allow_oom):
    # A fixed-depth shape also bounds serializer allocations for malformed rows.
    if not isinstance(metric,dict) or len(metric)!=5 or set(metric)!={'memory.current','memory.peak','memory.stat','memory.events','cpu.stat'}:
        raise RuntimeError('Invalid lifecycle metric shape')
    for key in ('memory.current','memory.peak'):
        if type(metric[key]) is not int or not 0<=metric[key]<2**64:
            raise RuntimeError('Invalid lifecycle memory accounting')
    if not metric['memory.peak']:raise RuntimeError('Missing lifecycle peak')
    for section,required in (('memory.stat',('anon','file')),('memory.events',_LIFECYCLE_EVENTS),('cpu.stat',('usage_usec',))):
        values=metric[section]
        if (not isinstance(values,dict) or len(values)>128 or not set(required)<=values.keys()
                or any(not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_.]{1,128}',key)
                    or type(value) is not int or not 0<=value<2**64 for key,value in values.items())):
            raise RuntimeError('Invalid lifecycle resource accounting')
    if not allow_oom:
        from scripts.low_resource_certificates import validate_metrics_record
        validate_metrics_record(metric)


def _lifecycle_counters(before,after):
    if (after['memory.peak']<before['memory.peak']
            or after['cpu.stat']['usage_usec']<before['cpu.stat']['usage_usec']
            or not before['memory.events'].keys()<=after['memory.events'].keys()
            or any(after['memory.events'][key]<count for key,count in before['memory.events'].items())):
        raise RuntimeError('Lifecycle cumulative counters decreased')


def _lifecycle_stage_floor(stage,metric):
    # Stage CPU is a delta, so only lifetime peak and event counters are anchors.
    if 'memory_peak_bytes' in stage:
        peak=stage['memory_peak_bytes']
        if type(peak) is not int or peak<=0 or metric['memory.peak']<peak:
            raise RuntimeError('Lifecycle peak below completed stage')
    if 'memory_events' in stage:
        events=stage['memory_events']
        if (not isinstance(events,dict) or not set(_LIFECYCLE_EVENTS)<=events.keys()
                or not events.keys()<=metric['memory.events'].keys()
                or any(type(count) is not int or count<0 or metric['memory.events'][key]<count
                    for key,count in events.items())):
            raise RuntimeError('Lifecycle events below completed stage')


def validate_lifecycle_rows(rows,unit,commit,service_cgroup,*,allow_oom=False):
    """Validate a bounded row prefix, without claiming stage completion."""
    from scripts.low_resource_service_tree import canonical_cgroup
    if (not isinstance(unit,str) or not re.fullmatch(r'vui-low-resource-[0-9a-f]{32}\.service',unit)
            or not isinstance(commit,str) or not re.fullmatch(r'[0-9a-f]{40}',commit)
            or not isinstance(service_cgroup,str) or len(service_cgroup)>4096):
        raise RuntimeError('Invalid lifecycle source identity')
    canonical_cgroup(service_cgroup,unit)
    if not isinstance(rows,list) or len(rows)>LIFECYCLE_LIMITS['max_rows']:
        raise RuntimeError('Lifecycle row count exceeded')
    fields={'name','unit','source_commit','service_cgroup','started_monotonic','finished_monotonic',
        'metrics','snapshot_observer_self_cpu_seconds','snapshot_observer_wall_seconds'}
    previous=None;finished=0;total=2  # Brackets in the canonical serialized list.
    encoder=json.JSONEncoder(sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)
    for index,row in enumerate(rows):
        if (not isinstance(row,dict) or len(row)!=len(fields) or set(row)!=fields or row.get('name')!=LIFECYCLE_BOUNDARIES[index]
                or row.get('unit')!=unit or row.get('source_commit')!=commit or row.get('service_cgroup')!=service_cgroup):
            raise RuntimeError('Invalid ordered lifecycle row or source')
        if any(not _lifecycle_number(row[key]) for key in
                ('started_monotonic','finished_monotonic','snapshot_observer_self_cpu_seconds','snapshot_observer_wall_seconds')):
            raise RuntimeError('Invalid lifecycle observation clock or cost')
        if (not finished<=row['started_monotonic']<=row['finished_monotonic']
                or not math.isclose(row['snapshot_observer_wall_seconds'],
                    row['finished_monotonic']-row['started_monotonic'],rel_tol=1e-9,abs_tol=1e-12)):
            raise RuntimeError('Inconsistent lifecycle observation window')
        _lifecycle_metrics(row['metrics'],allow_oom)
        if previous is not None:_lifecycle_counters(previous,row['metrics'])
        previous=row['metrics'];finished=row['finished_monotonic']
        size=0
        for chunk in encoder.iterencode(row):
            size+=len(chunk.encode('ascii'))
            if size>LIFECYCLE_LIMITS['max_row_bytes']:raise RuntimeError('Lifecycle row byte budget exceeded')
        total+=size+(index>0)
        if total>LIFECYCLE_LIMITS['max_total_bytes']:raise RuntimeError('Lifecycle total byte budget exceeded')
    return total


def _validate_lifecycle_report(report,unit,commit,complete):
    if (not isinstance(report,dict) or report.get('duration_profile')!='data-backup'
            or report.get('unit')!=unit or report.get('source_commit')!=commit
            or type(report.get('requested_memory_mib')) is not int or report['requested_memory_mib']!=512):
        raise RuntimeError('Lifecycle report source mismatch')
    if complete and (report.get('outcome')!='passed' or report.get('complete') is not True):
        raise RuntimeError('Lifecycle report did not complete successfully')
    limits=report.get('limits',{});cpu=limits.get('cpu.max','') if isinstance(limits,dict) else ''
    if (not isinstance(limits,dict) or type(limits.get('memory.max')) is not int
            or limits['memory.max']!=512*1024*1024 or type(limits.get('memory.swap.max')) is not int
            or limits['memory.swap.max']!=0 or not isinstance(cpu,str)
            or not re.fullmatch(r'[1-9][0-9]* [1-9][0-9]*',cpu) or cpu.split()[0]!=cpu.split()[1]):
        raise RuntimeError('Invalid lifecycle service limits')
    value=report.get('large_data',{})
    if not isinstance(value,dict):raise RuntimeError('Invalid lifecycle accounting object')
    rows=value.get('lifecycle_boundaries',[])
    if complete or 'large_data' in report:
        bounds=value.get('lifecycle_boundary_limits')
        if (type(value.get('lifecycle_boundary_schema')) is not int or value['lifecycle_boundary_schema']!=1
                or not isinstance(bounds,dict) or bounds!=LIFECYCLE_LIMITS
                or any(type(v) is not int for v in bounds.values())
                or value.get('lifecycle_boundary_scope')!=LIFECYCLE_SCOPE
                or 'lifecycle_boundaries' not in value):
            raise RuntimeError('Missing fixed lifecycle schema, scope or budgets')
    validate_lifecycle_rows(rows,unit,commit,report.get('service_cgroup'),allow_oom=not complete)
    if complete and len(rows)!=len(LIFECYCLE_BOUNDARIES):raise RuntimeError('Incomplete lifecycle boundary accounting')
    stages=report.get('stages',[])
    if not isinstance(stages,list) or any(not isinstance(stage,dict) or not isinstance(stage.get('name'),str) for stage in stages):
        raise RuntimeError('Invalid lifecycle stage records')
    by_name={stage['name']:stage for stage in stages}
    if len(by_name)!=len(stages):raise RuntimeError('Duplicate lifecycle stages')
    relevant=('stopped_backup','mutate_large_data_after_backup',*_LIFECYCLE_STAGES,'restore_data_and_session_revocation')
    observed=[stage['name'] for stage in stages if stage['name'] in relevant]
    if observed!=list(relevant[:len(observed)]):raise RuntimeError('Lifecycle stage progress out of order')
    for index,stage in enumerate(stages):
        outcome=stage.get('outcome')
        if outcome not in ('running','failed','passed') or (complete or index<len(stages)-1) and outcome!='passed':
            raise RuntimeError('Lifecycle stage started after unfinished predecessor')
    for name,needed,done in zip(_LIFECYCLE_STAGES,(2,5,7),(5,7,9)):
        stage=by_name.get(name)
        if stage is not None and (len(rows)<needed or stage.get('outcome')=='passed' and len(rows)<done):
            raise RuntimeError('Lifecycle prefix conflicts with stage progress')
    if any(stage['name'] in REQUIRED_STAGES[REQUIRED_STAGES.index(_LIFECYCLE_STAGES[-1])+1:] for stage in stages) and len(rows)!=9:
        raise RuntimeError('Later stage started before lifecycle completed')
    if not rows:
        final=report.get('metrics')
        if final is not None:
            _lifecycle_metrics(final,not complete)
            if 'accounting_error' not in report:
                for stage in stages:_lifecycle_stage_floor(stage,final)
        return {'observation_count':0,'qualification':False,'oom_observed':bool(final and any(
            final.get('memory.events',{}).get(key,0) for key in ('oom','oom_kill','oom_group_kill')))}
    mutate=by_name.get('mutate_large_data_after_backup',{})
    if mutate.get('outcome')!='passed':raise RuntimeError('Snapshot gap lacks completed mutation')

    def window(stage):
        start=stage.get('started_monotonic')
        if not _lifecycle_number(start):raise RuntimeError('Missing lifecycle stage clock')
        wall=stage.get('wall_seconds')
        if 'wall_seconds' not in stage:
            if complete or stage.get('outcome')=='passed':raise RuntimeError('Missing completed lifecycle stage clock')
            return start,None
        if not _lifecycle_number(wall) or not math.isfinite(start+wall):raise RuntimeError('Invalid lifecycle stage duration')
        return start,start+wall

    previous_end=0
    for name in observed:
        start,end=window(by_name[name])
        if start<previous_end:raise RuntimeError('Overlapping lifecycle stages')
        previous_end=start if end is None else end
    _,gap_start=window(mutate)
    if gap_start is None:raise RuntimeError('Missing snapshot gap start')
    bad_stage=by_name.get(_LIFECYCLE_STAGES[0]);gap_end=window(bad_stage)[0] if bad_stage else None
    if complete and gap_end is None:raise RuntimeError('Missing snapshot gap end')
    for row in rows[:2]:
        if row['started_monotonic']<gap_start or gap_end is not None and row['finished_monotonic']>gap_end:
            raise RuntimeError('Initial lifecycle snapshots outside un-staged gap')
    # Original backup measurements remain a separate contract. Use their last
    # observed boundary as the cumulative anchor, never a stage CPU delta.
    backup=value.get('backup_boundaries')
    if (not isinstance(backup,list) or len(backup)!=len(BACKUP_BOUNDARIES)
            or any(not isinstance(row,dict) or row.get('name')!=name for row,name in zip(backup,BACKUP_BOUNDARIES))):
        raise RuntimeError('Missing earlier backup boundary anchor')
    anchor=backup[-1]
    if (anchor.get('unit')!=unit or anchor.get('source_commit')!=commit
            or anchor.get('service_cgroup')!=report['service_cgroup']
            or not _lifecycle_number(anchor.get('started_monotonic'))
            or not _lifecycle_number(anchor.get('finished_monotonic'))
            or not anchor['started_monotonic']<=anchor['finished_monotonic']<=rows[0]['started_monotonic']):
        raise RuntimeError('Invalid earlier backup boundary anchor')
    anchor_start,anchor_end=window(by_name['stopped_backup'])
    if (anchor['started_monotonic']<anchor_start
            or anchor_end is not None and anchor['finished_monotonic']>anchor_end):
        raise RuntimeError('Backup anchor outside its stage')
    _lifecycle_metrics(anchor.get('metrics'),not complete)
    _lifecycle_counters(anchor['metrics'],rows[0]['metrics'])
    # Known prior stage endpoints precede these observations in the unchanged
    # control flow, even though stage wall time excludes its final checkpoint.
    indices={stage['name']:index for index,stage in enumerate(stages)}
    preceding=(indices['mutate_large_data_after_backup']+1,)*2
    preceding+=tuple(indices[name] for name,count in zip(_LIFECYCLE_STAGES,(3,2,2))
        if name in indices for _ in range(count))
    for row,stop in zip(rows,preceding):
        for earlier in stages[:stop]:_lifecycle_stage_floor(earlier,row['metrics'])
    for name,first,last in zip(_LIFECYCLE_STAGES,(2,5,7),(5,7,9)):
        selected=rows[first:last]
        if not selected:continue
        stage=by_name.get(name)
        if stage is None:raise RuntimeError('Lifecycle observation lacks its stage')
        start,end=window(stage)
        for row in selected:
            if row['started_monotonic']<start or end is not None and row['finished_monotonic']>end:
                raise RuntimeError('Lifecycle observation outside enclosing stage')
            peak=stage.get('memory_peak_bytes');events=stage.get('memory_events')
            if 'memory_peak_bytes' in stage:
                if type(peak) is not int or peak<=0 or row['metrics']['memory.peak']>peak:
                    raise RuntimeError('Lifecycle peak exceeds enclosing stage')
            elif complete or stage.get('outcome')=='passed':raise RuntimeError('Missing completed stage peak')
            if 'memory_events' in stage:
                if (not isinstance(events,dict) or any(type(events.get(key)) is not int or events[key]<0
                        or row['metrics']['memory.events'][key]>events[key] for key in row['metrics']['memory.events'])):
                    raise RuntimeError('Lifecycle events exceed enclosing stage')
            elif complete or stage.get('outcome')=='passed':raise RuntimeError('Missing completed stage events')
        cpu=stage.get('cpu_usage_usec')
        if 'cpu_usage_usec' in stage:
            if (type(cpu) is not int or cpu<0
                    or selected[-1]['metrics']['cpu.stat']['usage_usec']-selected[0]['metrics']['cpu.stat']['usage_usec']>cpu):
                raise RuntimeError('Lifecycle CPU delta exceeds enclosing stage')
        elif complete or stage.get('outcome')=='passed':raise RuntimeError('Missing completed stage CPU')
    final=report.get('metrics')
    if complete or final is not None:
        _lifecycle_metrics(final,not complete)
        if complete or 'accounting_error' not in report:
            _lifecycle_counters(rows[-1]['metrics'],final)
            for stage in stages:_lifecycle_stage_floor(stage,final)
    return {'observation_count':len(rows),'qualification':False,
        'oom_observed':any(row['metrics']['memory.events'][key] for row in [anchor,*rows]
            for key in ('oom','oom_kill','oom_group_kill')) or bool(final and any(
                final.get('memory.events',{}).get(key,0) for key in ('oom','oom_kill','oom_group_kill')))}


def validate_lifecycle_boundaries(report,unit,commit):
    """Require all nine observations; no diagnostic-prefix fallback."""
    _validate_lifecycle_report(report,unit,commit,True)


def validate_lifecycle_prefix(report,unit,commit):
    """Validate only available failure evidence, never qualify a completed run."""
    return _validate_lifecycle_report(report,unit,commit,False)


def validate_backup_boundaries(report,unit,commit):
    from scripts.low_resource_certificates import validate_metrics_record
    rows=report.get('large_data',{}).get('backup_boundaries')
    if not isinstance(rows,list) or [r.get('name') for r in rows]!=list(BACKUP_BOUNDARIES):
        raise RuntimeError('Missing ordered backup boundary accounting')
    stage=next(s for s in report['stages'] if s['name']=='stopped_backup')
    start=stage.get('started_monotonic');wall=stage['wall_seconds']
    if type(start) not in (int,float) or not math.isfinite(start) or start<0:
        raise RuntimeError('Missing backup stage clock')
    previous_finished=start;previous=None
    for row in rows:
        if (row.get('unit')!=unit or row.get('source_commit')!=commit
                or row.get('service_cgroup')!=report.get('service_cgroup')
                or not isinstance(row.get('service_cgroup'),str)
                or not row['service_cgroup'].startswith('0::/')
                or not row['service_cgroup'].endswith('/'+unit)):
            raise RuntimeError('Backup boundary source or cgroup mismatch')
        times=[row.get('started_monotonic'),row.get('finished_monotonic')]
        if (any(type(t) not in (int,float) or not math.isfinite(t) for t in times)
                or not previous_finished<=times[0]<=times[1]<=start+wall):
            raise RuntimeError('Backup boundary clock outside stage')
        metric=row.get('metrics');validate_metrics_record(metric)
        if metric['memory.peak']>stage['memory_peak_bytes']:
            raise RuntimeError('Backup boundary peak exceeds final stage peak')
        for key in ('max','oom','oom_kill','oom_group_kill'):
            if metric['memory.events'][key]>stage['memory_events'][key]:
                raise RuntimeError('Backup boundary events exceed final stage events')
        if previous is not None:
            if (metric['memory.peak']<previous['memory.peak']
                    or metric['cpu.stat']['usage_usec']<previous['cpu.stat']['usage_usec']
                    or any(metric['memory.events'][k]<previous['memory.events'][k] for k in ('max','oom','oom_kill','oom_group_kill'))):
                raise RuntimeError('Backup boundary cumulative counters decreased')
        previous=metric;previous_finished=times[1]
    if rows[-1]['metrics']['cpu.stat']['usage_usec']-rows[0]['metrics']['cpu.stat']['usage_usec']>stage['cpu_usage_usec']:
        raise RuntimeError('Backup boundary CPU exceeds entire stage')


def validate_complete(report,unit,commit):
    from scripts.low_resource_certificates import validate_metrics_record
    if report.get('duration_profile')!='data-backup':raise RuntimeError('Wrong data profile')
    stages=report.get('stages',[]);names=[s.get('name') for s in stages]
    if len(names)!=len(set(names)) or any(s.get('outcome')!='passed' for s in stages) or any(n not in names for n in REQUIRED_STAGES):
        raise RuntimeError('Large-data stage coverage incomplete')
    if [n for n in names if n in REQUIRED_STAGES]!=list(REQUIRED_STAGES):raise RuntimeError('Large-data stages out of order')
    for stage in stages:
        if type(stage.get('wall_seconds')) not in (int,float) or not math.isfinite(stage['wall_seconds']) or stage['wall_seconds']<0:
            raise RuntimeError('Missing large-data stage timing')
        for key in ('memory_current_bytes','memory_peak_bytes','cpu_usage_usec'):
            if type(stage.get(key)) is not int or stage[key]<0:raise RuntimeError('Missing large-data stage resource accounting')
        validate_metrics_record({'memory.current':stage['memory_current_bytes'],'memory.peak':stage['memory_peak_bytes'],
            'cpu.stat':{'usage_usec':stage['cpu_usage_usec']},'memory.stat':stage.get('memory_stat'),'memory.events':stage.get('memory_events')})
    validate_backup_boundaries(report,unit,commit)
    validate_lifecycle_boundaries(report,unit,commit)
    value=report.get('large_data',{});validate_exports(value.get('exports',{}),unit,commit)
    if value.get('routing_rejections')!=[428,409,422] or any(value.get(k) is not True for k in
            ('live_backup_rejected','bad_digest_preserved_data','restored_exports_verified','synthetic_token_not_logged')):raise RuntimeError('Missing large-data control evidence')
    logs=value.get('logs',{});small=logs.get('small_sha256',{})
    for key in ('large_bytes','allocated_bytes','disk_free_before_bytes','data_allocated_file_bytes'):
        if type(logs.get(key)) is not int or logs[key]<=0:raise RuntimeError('Missing finite log storage accounting')
    if (logs.get('large_bytes')!=LOG_BYTES or logs.get('allocated_bytes',0)<LOG_BYTES
            or logs['disk_free_before_bytes']<4*1024**3 or logs['data_allocated_file_bytes']<LOG_BYTES+64*4096
            or logs.get('small_file_count')!=64 or logs.get('small_file_bytes')!=4096
            or set(small)!={f'small-{n:03d}.log' for n in range(64)}
            or not re.fullmatch(r'[a-f0-9]{64}',logs.get('large_sha256',''))
            or any(not re.fullmatch(r'[a-f0-9]{64}',v) for v in small.values())):raise RuntimeError('Incomplete large-log fixture')
    archive=value.get('archive',{})
    for key in ('archive_bytes','expanded_bytes','installed_root_allocated_file_bytes'):
        if type(archive.get(key)) is not int or archive[key]<=0:raise RuntimeError('Missing finite archive storage accounting')
    ratio=archive.get('compression_ratio')
    if type(ratio) not in (int,float) or not math.isfinite(ratio) or not math.isclose(ratio,archive['archive_bytes']/archive['expanded_bytes'],rel_tol=1e-12):
        raise RuntimeError('Invalid archive compression ratio')
    if (archive.get('archive_contains_log_files') is not True or archive.get('archive_source')!='installed app.release_tools'
            or archive.get('archive_bytes',0)<=0 or archive.get('expanded_bytes',0)<LOG_BYTES+64*4096
            or not re.fullmatch(r'[a-f0-9]{64}',archive.get('sha256',''))):raise RuntimeError('Missing installed backup evidence')
    recovery=value.get('recovery',{})
    for key in ('business_restored','routing_restored','log_files_restored','database_integrity','old_sessions_revoked',
                'old_grant_revoked','pre_restore_data_preserved','selected_code_unchanged','temporary_cleanup_complete'):
        if recovery.get(key) is not True:raise RuntimeError('Incomplete large-data recovery')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--request',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();os.umask(0o077);raise SystemExit(run(json.loads(args.request.read_text()),args.output))
