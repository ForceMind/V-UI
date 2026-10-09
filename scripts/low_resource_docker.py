"""Native musl smoke coordinator with independent host/container cgroup proof."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import uuid

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from scripts import low_resource_acceptance as gate
from scripts.low_resource_service_tree import validate_tree, validate_sample_bindings


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
    gate.assert_no_oom(host);gate.assert_no_oom(worker)
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
    if len(stages)!=29 or any(s.get('outcome')!='passed' for s in stages):
        raise RuntimeError('Smoke stage sequence incomplete')
    validate_tree(report['proxy_workload']['server_tree'],token['target'],report['worker_pid'],'0::/')
    validate_sample_bindings(report);gate.assert_no_oom(report['metrics'])
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
    cid=None
    try:
        expected=dict(image_id=image['Id'],user=f'{os.getuid()}:{os.getgid()}',memory_bytes=gate.memory_bytes(args.memory_mib))
        command=['create','--init','--user',expected['user'],'--cgroupns=private',
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
        cid=docker(*command).stdout.strip()
        if not re.fullmatch(r'[0-9a-f]{64}',cid):raise RuntimeError('Docker create returned invalid ID')
        summary['container_id']=cid;docker('start',cid)
        ready=wait_report(args.output/'ready.json',cid,120)
        if any(ready.get(k)!=v for k,v in token.items()) or ready.get('gid')!=os.getgid():raise RuntimeError('Wrong READY source or group')
        info=inspect_container(cid);validate_config(info,expected)
        init=host_identity(info['State']['Pid']);directory=host_directory(cid,init)
        bound=bind_worker(directory,init,ready,os.getuid())
        limits=gate.verify_limits(directory,args.memory_mib)
        if limits!=ready['limits'] or ready['page_size']!=os.sysconf('SC_PAGE_SIZE'):
            raise RuntimeError('Host/container limits or page sizes differ')
        summary.update(image_id=image['Id'],image_architecture=image['Architecture'],host_init=init,
                       host_worker=bound,host_cgroup=str(directory),limits=limits,ready_metrics=gate.metrics(directory))
        gate.write_json(args.output/'host-ready.json',summary)
        gate.write_json(args.output/'go.json',token)
        terminal=wait_report(args.output/'terminal.json',cid,gate.runtime_seconds(args)+60)
        if (any(terminal.get(k)!=v for k,v in token.items()) or terminal.get('returncode')!=0
                or terminal.get('worker')!=ready['worker'] or terminal.get('init')!=ready['init']
                or terminal.get('cleanup_error')):
            raise RuntimeError('Container worker failed or changed identity')
        info=inspect_container(cid);validate_config(info,expected)
        if host_identity(info['State']['Pid'])!=init or bind_worker(directory,init,ready,os.getuid())!=bound:
            raise RuntimeError('Container restarted or replaced a process')
        report=json.loads((args.output/'worker.json').read_text());validate_report(report,token,ready,args.memory_mib)
        if report['bundle_sha256']!=bundle_digest:raise RuntimeError('Worker installed different bundle bytes')
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
        if cid:
            try:
                info=inspect_container(cid)
                if info['State']['Running']:
                    docker('stop','--time','10',cid,timeout=20,check=False)
                    if inspect_container(cid)['State']['Running']:
                        docker('kill',cid,timeout=10)
                    docker('wait',cid,timeout=20)
                info=inspect_container(cid)
                summary['exit_state']=info['State'];summary['restart_count']=info['RestartCount']
                logs=docker('logs',cid,check=False)
                (args.output/'container.log').write_text(logs.stdout+logs.stderr)
                if info['State']['Running']:
                    raise RuntimeError('Container remained running after stop/kill')
                docker('rm',cid)
                if docker('inspect',cid,check=False).returncode==0:raise RuntimeError('Container still exists after removal')
                summary['cleanup_complete']=True
                if info['State']['OOMKilled'] or info['RestartCount']:
                    raise RuntimeError('Container OOM or restart status failed')
            except Exception as exc:
                summary['cleanup_error']=str(exc);summary['outcome']='failed'
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
    a=p.parse_args();a.duration_profile='smoke';os.umask(0o077)
    return coordinator(a)


if __name__=='__main__':raise SystemExit(main())
