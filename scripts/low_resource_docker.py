"""Native musl smoke coordinator with independent host/container cgroup proof."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time
import uuid

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from scripts import low_resource_acceptance as gate
from scripts.low_resource_service_tree import validate_tree, validate_sample_bindings
from scripts.low_resource_certificates import validate_metrics_record

SMOKE_STAGES = (
    'offline_stage_including_wheels','activate','fake_admin_provision','https_startup',
    'anonymous_denied','untrusted_ca_rejected','login','authenticated_read',
    '100_authenticated_reads_concurrency_10','panel_only_idle_60_seconds',
    'proxy_server_start','panel_single_proxy_idle_60_seconds','proxy_client_start',
    'proxy_10_requests_concurrency_1','proxy_100_requests_concurrency_10',
    'proxy_wrong_uuid_rejected','proxy_wrong_ca_rejected','logout',
    'logged_out_replayed_cookie_denied','login_before_backup','stopped_panel',
    'stopped_backup','stopped_restore','restore_data_and_session_revocation',
    'restored_https_startup','old_session_after_restore_denied','restored_login',
    'restored_logout','final_panel_stop')


def docker(*args, timeout=30, check=True):
    return subprocess.run(['docker', *map(str,args)], capture_output=True, text=True,
                          timeout=timeout, check=check)


def inspect_container(container_id):
    if not re.fullmatch(r'[0-9a-f]{64}', container_id):
        raise RuntimeError('Expected exact container ID')
    rows = json.loads(docker('inspect', container_id).stdout)
    if len(rows) != 1 or rows[0].get('Id') != container_id:
        raise RuntimeError('Container identity mismatch')
    return rows[0]


def recover_created(name, token, image_id):
    """Resolve only this invocation's unique name after an uncertain create."""
    result=docker('inspect',name,check=False)
    if result.returncode:
        if 'No such object: '+name in result.stderr or 'No such container: '+name in result.stderr:return None
        raise RuntimeError('Cannot resolve uncertain container creation')
    rows=json.loads(result.stdout)
    if (len(rows)!=1 or rows[0].get('Name')!='/'+name or rows[0].get('Image')!=image_id
            or rows[0].get('Config',{}).get('Labels',{}).get('vui.resource.unit')!=token['unit']
            or not re.fullmatch(r'[0-9a-f]{64}',rows[0].get('Id',''))):
        raise RuntimeError('Uncertain create resolved to an unrelated container')
    return rows[0]['Id']


def cleanup_container(cid, output):
    """Each exact-ID cleanup attempt continues even if earlier tooling failed."""
    result={'cleanup_complete':False,'cleanup_diagnostics':[]}
    if not re.fullmatch(r'[0-9a-f]{64}',cid):
        return dict(result,cleanup_error='Refusing cleanup without exact container ID')
    for operation,args,timeout in [('stop',('stop','--time','10',cid),20),
                                   ('kill',('kill',cid),10),('wait',('wait',cid),20)]:
        try:
            value=docker(*args,timeout=timeout,check=False)
            result['cleanup_diagnostics'].append(dict(operation=operation,returncode=value.returncode))
        except Exception as exc:
            result['cleanup_diagnostics'].append(dict(operation=operation,error=type(exc).__name__))
    try:
        info=inspect_container(cid)
        result['exit_state']=info['State'];result['restart_count']=info['RestartCount']
        if info['State']['Running'] or info['State']['OOMKilled'] or info['RestartCount']:
            result['cleanup_error']='Container running, OOM or restart status failed'
    except Exception as exc:
        result['cleanup_error']='Exit state unavailable: '+type(exc).__name__
    try:
        logs=docker('logs',cid,check=False)
        (output/'container.log').write_text(logs.stdout+logs.stderr)
        if logs.returncode:result['log_error']='Unable to collect complete Docker log'
    except Exception as exc:
        result['log_error']=type(exc).__name__
    try:
        removed=docker('rm','--force',cid,check=False)
        if removed.returncode:raise RuntimeError('Exact container removal failed')
        absent=docker('inspect',cid,check=False)
        if (absent.returncode==0 or not any(text+cid in absent.stderr for text in ('No such object: ','No such container: '))):
            raise RuntimeError('Container absence not confirmed')
        result['cleanup_complete']=True
    except Exception as exc:
        result['cleanup_error']='Removal unconfirmed: '+type(exc).__name__
    return result


def host_identity(pid, proc=Path('/proc')):
    if type(pid) is not int or pid <= 0:
        raise RuntimeError('Invalid host PID')
    directory = proc/str(pid)
    stat = (directory/'stat').read_text()
    ticks = int(stat.rsplit(')',1)[1].split()[19])
    status = dict(row.split(':',1) for row in (directory/'status').read_text().splitlines() if ':' in row)
    result = dict(pid=pid,starttime_ticks=ticks,
                  namespace_pids=[int(x) for x in status['NSpid'].split()],
                  uid=[int(x) for x in status['Uid'].split()],
                  cgroup=(directory/'cgroup').read_text().strip(),
                  pid_namespace=os.readlink(directory/'ns/pid'),
                  cgroup_namespace=os.readlink(directory/'ns/cgroup'))
    if int((directory/'stat').read_text().rsplit(')',1)[1].split()[19]) != ticks:
        raise RuntimeError('Host process identity changed')
    return result


def host_directory(container_id, binding, root=Path('/sys/fs/cgroup')):
    value=binding.get('cgroup','')
    if not re.fullmatch(r'0::/[^\n]+',value):
        raise RuntimeError('Missing actual host container cgroup')
    path=Path(value[3:])
    if ('..' in path.parts or path.name not in (container_id, 'docker-'+container_id+'.scope')):
        raise RuntimeError('Host cgroup is not the exact Docker container')
    result=root/str(path).lstrip('/')
    if not result.is_dir():raise RuntimeError('Host cgroup vanished')
    return result


def validate_config(value, expected):
    config=value['HostConfig'];state=value['State']
    if (value['Image']!=expected['image_id'] or value['Config']['User']!=expected['user']
            or config.get('Memory')!=expected['memory_bytes']
            or config.get('MemorySwap')!=expected['memory_bytes']
            or config.get('NanoCpus')!=1_000_000_000
            or config.get('CgroupnsMode')!='private' or config.get('Init') is not True
            or config.get('Privileged') is not False or config.get('CapAdd')
            or config.get('PidMode') or config.get('RestartPolicy',{}).get('Name')!='no'
            or config.get('AutoRemove') is not False
            or state.get('Running') is not True or state.get('OOMKilled') is not False
            or value.get('RestartCount')!=0):
        raise RuntimeError('Unexpected Docker image/user/resource/isolation configuration')


def bind_worker(directory, host_init, ready, uid, proc=Path('/proc')):
    inside_init=ready['init'];inside_worker=ready['worker']
    if (inside_init['pid']!=1 or inside_init['cgroup']!='0::/'
            or inside_worker['cgroup']!='0::/' or ready['uid']!=uid
            or host_init['uid']!=[uid]*4 or inside_worker.get('ppid')!=1
            or host_init['namespace_pids'][-1]!=1
            or host_init['pid_namespace']!=inside_init['pid_namespace']
            or host_init['cgroup_namespace']!=inside_init['cgroup_namespace']
            or host_init['starttime_ticks']!=inside_init['starttime_ticks']
            or inside_worker['pid_namespace']!=inside_init['pid_namespace']
            or inside_worker['cgroup_namespace']!=inside_init['cgroup_namespace']):
        raise RuntimeError('Container init namespace identity mismatch')
    rows=[host_identity(int(pid),proc) for pid in (directory/'cgroup.procs').read_text().split()]
    selected=[r for r in rows if r['namespace_pids'][-1]==inside_worker['pid']
              and r['pid_namespace']==host_init['pid_namespace']]
    if len(selected)!=1:raise RuntimeError('Container worker lacks unique host mapping')
    row=selected[0]
    if (row['starttime_ticks']!=inside_worker['starttime_ticks'] or row['uid']!=[uid]*4
            or row['cgroup']!=host_init['cgroup']
            or row['cgroup_namespace']!=host_init['cgroup_namespace']
            or len(row['namespace_pids'])!=2 or len(host_init['namespace_pids'])!=2):
        raise RuntimeError('Container worker identity escaped scoped accounting')
    if {r['pid'] for r in rows}!={host_init['pid'],row['pid']}:
        raise RuntimeError('Unexpected container processes at handshake')
    if (row['pid_namespace']==os.readlink(proc/'self/ns/pid')
            or row['cgroup_namespace']==os.readlink(proc/'self/ns/cgroup')):
        raise RuntimeError('Container did not obtain independent namespaces')
    return row


def wait_report(path, container_id, timeout):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        if path.exists():return json.loads(path.read_text())
        if not inspect_container(container_id)['State']['Running']:
            raise RuntimeError('Container exited before required evidence')
        time.sleep(.25)
    raise RuntimeError('Timed out waiting for container evidence')


def validate_counters(host, worker):
    validate_metrics_record(host);validate_metrics_record(worker)
    for field in ('memory.peak',):
        if host[field]<worker[field]:raise RuntimeError('Host peak precedes worker final accounting')
    for record,fields in [('cpu.stat',('usage_usec',)),('memory.events',('max','oom','oom_kill','oom_group_kill'))]:
        for field in fields:
            if host[record][field]<worker[record][field]:
                raise RuntimeError('Host cumulative counter precedes worker final accounting')


def validate_report(report, token, ready, memory_mib):
    if (report.get('source_commit')!=token['source_commit'] or report.get('unit')!=token['unit']
            or report.get('cgroup_backend')!='docker-private' or report.get('service_cgroup')!='0::/'
            or report.get('duration_profile')!='smoke' or report.get('outcome')!='passed'
            or report.get('complete') is not True or report.get('worker_pid')!=ready['worker']['pid']
            or report.get('limits')!=ready['limits'] or report.get('base_page_size_bytes')!=ready['page_size']
            or report.get('requested_memory_mib')!=memory_mib
            or not re.fullmatch(r'[0-9a-f]{64}',report.get('bundle_sha256',''))):
        raise RuntimeError('Worker report source/backend/identity/limits incomplete')
    stages=report.get('stages',[])
    if report.get('concurrent_workload')!={'requests':100,'concurrency':10,'path':'/api/auth/me','status_200':100}:
        raise RuntimeError('Authenticated concurrent workload incomplete')
    if tuple(s.get('name') for s in stages)!=SMOKE_STAGES or any(s.get('outcome')!='passed' for s in stages):
        raise RuntimeError('Smoke stage sequence incomplete')
    previous_peak=0;previous_end=0;previous_events={k:0 for k in ('max','oom','oom_kill','oom_group_kill')}
    for stage in stages:
        wall=stage.get('wall_seconds')
        start=stage.get('started_monotonic')
        if (any(type(v) not in (int,float) or not math.isfinite(v) for v in (wall,start))
                or wall<0 or start<previous_end):
            raise RuntimeError('Invalid smoke stage duration')
        previous_end=start+wall
        if stage['name'].endswith('_idle_60_seconds') and wall<60:
            raise RuntimeError('Smoke idle was shortened')
        validate_metrics_record({'memory.current':stage.get('memory_current_bytes'),
            'memory.peak':stage.get('memory_peak_bytes'),'memory.stat':stage.get('memory_stat'),
            'memory.events':stage.get('memory_events'),'cpu.stat':{'usage_usec':stage.get('cpu_usage_usec')}})
        if stage['memory_peak_bytes']<previous_peak:raise RuntimeError('Stage peak moved backwards')
        previous_peak=stage['memory_peak_bytes']
        for key,value in previous_events.items():
            if stage['memory_events'][key]<value:raise RuntimeError('Stage event counter moved backwards')
        previous_events={key:stage['memory_events'][key] for key in previous_events}
    proxy=report['proxy_workload']
    if proxy.get('cleanup_complete') is not True or proxy.get('no_direct') is not True:
        raise RuntimeError('Proxy cleanup or explicit routing incomplete')
    if [(v.get('concurrency'),v.get('requests'),v.get('status_200'),v.get('target_deliveries'))
            for v in proxy.get('positive',[])]!=[(1,10,10,10),(10,100,100,100)]:
        raise RuntimeError('Proxy positive workload incomplete')
    if any(row.get('body_bytes')!=19 for row in proxy['positive']):raise RuntimeError('Unexpected proxy body size')
    for name,markers in [('wrong_uuid',{'unknown uuid'}),('wrong_ca',{'x509','unknown authority'})]:
        row=proxy.get('negative',{}).get(name,{})
        if (row.get('attempts')!=1 or row.get('target_deliveries')!=0 or row.get('no_direct') is not True
                or not markers<=set(row.get('reason_markers',[]))):
            raise RuntimeError('Proxy negative rejection evidence incomplete')
    validate_tree(report['proxy_workload']['server_tree'],token['target'],report['worker_pid'],'0::/')
    validate_sample_bindings(report);validate_metrics_record(report['metrics'])
    if report['metrics']['memory.peak']<previous_peak:raise RuntimeError('Final peak precedes stage peak')
    if any(report['metrics']['memory.events'][key]<value for key,value in previous_events.items()):
        raise RuntimeError('Final event counter precedes stage counter')
    if not gate.peak_assessment(report['metrics']['memory.peak'],gate.memory_bytes(memory_mib),ready['page_size'])['within_fixed_page_allowance']:
        raise RuntimeError('Worker exceeded unchanged fixed page allowance')


def coordinator(args):
    gate.require_hosted_runner()
    expected_arch={'x86_64-musl':'x86_64','aarch64-musl':'aarch64'}[args.target]
    if platform.machine()!=expected_arch:raise RuntimeError('Musl resource gate must be native')
    if not re.fullmatch(r'[0-9a-f]{40}',args.source_commit):raise RuntimeError('Exact source required')
    if args.output.exists():raise RuntimeError('Refusing to overwrite container evidence')
    with args.bundle.open('rb') as stream:
        bundle_digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if args.bundle.with_suffix(args.bundle.suffix+'.sha256').read_text().split()[0]!=bundle_digest:
        raise RuntimeError('Input bundle sidecar differs from actual bytes')
    args.output.mkdir(parents=True,mode=0o700)
    image=json.loads(docker('image','inspect',args.image).stdout)[0]
    if image['Architecture']!={'x86_64':'amd64','aarch64':'arm64'}[expected_arch]:
        raise RuntimeError('Harness image architecture mismatch')
    token=dict(unit='vui-low-resource-'+uuid.uuid4().hex+'.service',source_commit=args.source_commit,target=args.target)
    summary=dict(schema=1,**token,outcome='failed',backend='docker-private',bundle_sha256=bundle_digest,
                 scope='native musl smoke; limited container includes init/worker/product/clients; host OS/build/prior cache excluded')
    cid=None;directory=None;name='vui-resource-'+uuid.uuid4().hex
    try:
        expected=dict(image_id=image['Id'],user=f'{os.getuid()}:{os.getgid()}',memory_bytes=gate.memory_bytes(args.memory_mib))
        command=['create','--name',name,'--label','vui.resource.unit='+token['unit'],
                 '--cidfile',str(args.output.resolve()/'container.cid'),
                 '--init','--user',expected['user'],'--cgroupns=private',
                 '--memory',str(expected['memory_bytes']),'--memory-swap',str(expected['memory_bytes']),
                 '--cpus','1','--restart=no',
                 '--mount',f'type=bind,src={SOURCE},dst=/src,readonly',
                 '--mount',f'type=bind,src={args.bundle.resolve().parent},dst=/input,readonly',
                 '--mount',f'type=bind,src={args.output.resolve()},dst=/evidence',
                 '-e','GITHUB_ACTIONS=true','-e','RUNNER_ENVIRONMENT=github-hosted',
                 '-e','TMPDIR=/work','-w','/src',image['Id'],'/opt/harness/bin/python','-B',
                 '/src/scripts/low_resource_container.py','--bundle','/input/'+args.bundle.name,
                 '--source-commit',args.source_commit,'--target',args.target,'--unit',token['unit'],
                 '--output','/evidence/worker.json','--work-dir','/work','--memory-mib',str(args.memory_mib)]
        created=docker(*command).stdout.strip()
        if not re.fullmatch(r'[0-9a-f]{64}',created):raise RuntimeError('Docker create returned invalid ID')
        cid=created
        summary['container_id']=cid;docker('start',cid)
        ready=wait_report(args.output/'ready.json',cid,120)
        if any(ready.get(k)!=v for k,v in token.items()) or ready.get('gid')!=os.getgid():raise RuntimeError('Wrong READY source or group')
        validate_metrics_record(ready['metrics'])
        info=inspect_container(cid);validate_config(info,expected)
        init=host_identity(info['State']['Pid']);directory=host_directory(cid,init)
        bound=bind_worker(directory,init,ready,os.getuid())
        limits=gate.verify_limits(directory,args.memory_mib)
        if limits!=ready['limits'] or ready['page_size']!=os.sysconf('SC_PAGE_SIZE'):
            raise RuntimeError('Host/container limits or page sizes differ')
        summary.update(image_id=image['Id'],image_architecture=image['Architecture'],host_init=init,
                       host_worker=bound,host_cgroup=str(directory),limits=limits,ready_metrics=gate.metrics(directory))
        validate_counters(summary['ready_metrics'],ready['metrics'])
        gate.write_json(args.output/'host-ready.json',summary)
        go_time=time.monotonic()
        gate.write_json(args.output/'go.json',token)
        terminal=wait_report(args.output/'terminal.json',cid,gate.runtime_seconds(args)+60)
        if (any(terminal.get(k)!=v for k,v in token.items()) or terminal.get('returncode')!=0
                or terminal.get('worker')!=ready['worker'] or terminal.get('init')!=ready['init']
                or terminal.get('cleanup_error')):
            raise RuntimeError('Container worker failed or changed identity')
        validate_counters(terminal['metrics'],ready['metrics'])
        info=inspect_container(cid);validate_config(info,expected)
        if host_identity(info['State']['Pid'])!=init or bind_worker(directory,init,ready,os.getuid())!=bound:
            raise RuntimeError('Container restarted or replaced a process')
        report=json.loads((args.output/'worker.json').read_text());validate_report(report,token,ready,args.memory_mib)
        if report['bundle_sha256']!=bundle_digest:raise RuntimeError('Worker installed different bundle bytes')
        stamps=[ready.get('observed_monotonic'),terminal.get('started_monotonic'),terminal.get('finished_monotonic'),terminal.get('wall_seconds')]
        if (any(type(v) not in (int,float) or not math.isfinite(v) for v in stamps)
                or not stamps[0]<=go_time<=stamps[1]<stamps[2]<=time.monotonic()
                or not 120<=stamps[3]<=gate.runtime_seconds(args)+5
                or abs((stamps[2]-stamps[1])-stamps[3])>1
                or report['stages'][0]['started_monotonic']<stamps[1]
                or report['stages'][-1]['started_monotonic']+report['stages'][-1]['wall_seconds']>stamps[2]):
            raise RuntimeError('Container smoke escaped attested measurement window')
        validate_counters(report['metrics'],ready['metrics'])
        validate_counters(terminal['metrics'],report['metrics'])
        final=gate.metrics(directory);gate.verify_limits(directory,args.memory_mib)
        validate_counters(final,report['metrics']);validate_counters(final,terminal['metrics'])
        summary['host_terminal_metrics']=final
        summary['peak_budget_assessment']=gate.peak_assessment(final['memory.peak'],expected['memory_bytes'],ready['page_size'])
        if not summary['peak_budget_assessment']['within_fixed_page_allowance']:
            raise RuntimeError('Host peak exceeded unchanged fixed page allowance')
        gate.write_json(args.output/'host-terminal.json',summary)
        gate.write_json(args.output/'ack.json',token)
        waited=docker('wait',cid,timeout=30)
        if waited.stdout.strip()!='0':raise RuntimeError('Container did not exit successfully after ACK')
        summary['outcome']='passed'
    except Exception as exc:
        summary['error']=str(exc)
    finally:
        if cid is None:
            try:
                cid=recover_created(name,token,image['Id'])
                if cid:summary['recovered_container_id']=cid
            except Exception as exc:
                summary['cleanup_error']=str(exc)
        if cid:
            if summary['outcome']!='passed' or getattr(args,'cancelled',False):
                try:
                    if directory is None:
                        info=inspect_container(cid)
                        directory=host_directory(cid,host_identity(info['State']['Pid']))
                    summary['host_failure_metrics']=gate.metrics(directory)
                except Exception as exc:
                    summary['failure_accounting_error']=type(exc).__name__
                try:gate.write_json(args.output/'host-failure.json',summary)
                except Exception as exc:summary['failure_checkpoint_error']=type(exc).__name__
            summary.update(cleanup_container(cid,args.output))
        if (getattr(args,'cancelled',False) or summary.get('cleanup_error')
                or summary.get('log_error') or not summary.get('cleanup_complete')):
            summary['outcome']='failed'
        gate.write_json(args.output/'summary.json',summary)
    print(json.dumps(summary))
    return 0 if summary['outcome']=='passed' else 1


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image',required=True)
    p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--source-commit',required=True)
    p.add_argument('--target',choices=('x86_64-musl','aarch64-musl'),required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--memory-mib',type=int,choices=gate.MEMORY_PROFILES,default=512)
    a=p.parse_args();a.duration_profile='smoke';a.cancelled=False;os.umask(0o077)
    def terminate(signum,frame):
        if not a.cancelled:
            a.cancelled=True
            raise RuntimeError('Host coordinator interrupted; cleaning exact container')
    signal.signal(signal.SIGTERM,terminate);signal.signal(signal.SIGINT,terminate)
    return coordinator(a)


if __name__=='__main__':raise SystemExit(main())
