import copy
import json
import hashlib
import os
from contextlib import ExitStack
from pathlib import Path
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import low_resource_acceptance as gate
from scripts import low_resource_container as entry
from scripts import low_resource_docker as host

UNIT='vui-low-resource-'+('a'*32)+'.service'
CID='b'*64


class ContainerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.args=SimpleNamespace(cgroup_backend='docker-private',duration_profile='smoke',unit=UNIT,memory_mib=512)

    def test_private_root_is_explicit_and_native_still_rejects_it(self):
        for pid in ('self','1'):
            (self.root/pid).mkdir();(self.root/pid/'cgroup').write_text('0::/\n')
        (self.root/'mounts').write_text('cgroup '+str(self.root)+' cgroup2 ro,nosuid,nodev 0 0\n')
        with patch.object(gate,'verify_limits'),patch.object(gate,'metrics'):
            self.assertEqual(entry.private_directory(self.args,self.root,self.root),self.root)
            for field,value in [('duration_profile','sustained'),('cgroup_backend','systemd'),('unit','wrong')]:
                bad=copy.copy(self.args);setattr(bad,field,value)
                with self.assertRaises(RuntimeError):entry.private_directory(bad,self.root,self.root)
            (self.root/'1/cgroup').write_text('0::/other\n')
            with self.assertRaises(RuntimeError):entry.private_directory(self.args,self.root,self.root)
        with self.assertRaises(RuntimeError):gate.cgroup_directory(UNIT,'0::/\n',self.root)

    def test_host_path_requires_exact_container_not_root_or_another_id(self):
        (self.root/('docker-'+CID+'.scope')).mkdir()
        wanted='0::/docker-'+CID+'.scope'
        self.assertEqual(host.host_directory(CID,{'cgroup':wanted},self.root).name,'docker-'+CID+'.scope')
        for value in ['0::/','0::/docker-'+('c'*64)+'.scope','0::/../docker-'+CID+'.scope','1:memory:/x']:
            with self.subTest(value=value),self.assertRaises(RuntimeError):host.host_directory(CID,{'cgroup':value},self.root)

    def config(self):
        expected=dict(image_id='sha256:'+'a'*64,user='1001:1001',memory_bytes=512*1024**2)
        value=dict(Image=expected['image_id'],Config={'User':'1001:1001'},RestartCount=0,
            State={'Running':True,'OOMKilled':False},HostConfig=dict(Memory=512*1024**2,MemorySwap=512*1024**2,
            NanoCpus=10**9,CgroupnsMode='private',Init=True,Privileged=False,CapAdd=None,PidMode='',
            RestartPolicy={'Name':'no'},AutoRemove=False))
        return value,expected

    def test_docker_flags_are_checked_but_do_not_replace_kernel_limits(self):
        value,expected=self.config();host.validate_config(value,expected)
        for field,badvalue in [('Memory',0),('MemorySwap',-1),('NanoCpus',0),('CgroupnsMode','host'),
                ('Init',False),('Privileged',True),('CapAdd',['SYS_ADMIN']),('PidMode','host'),('AutoRemove',True)]:
            bad=copy.deepcopy(value);bad['HostConfig'][field]=badvalue
            with self.subTest(field=field),self.assertRaises(RuntimeError):host.validate_config(bad,expected)
        for field,badvalue in [('Running',False),('OOMKilled',True)]:
            bad=copy.deepcopy(value);bad['State'][field]=badvalue
            with self.assertRaises(RuntimeError):host.validate_config(bad,expected)
        for section,key,badvalue in [('Config','User','0:0'),('HostConfig','RestartPolicy',{'Name':'always'})]:
            bad=copy.deepcopy(value);bad[section][key]=badvalue
            with self.assertRaises(RuntimeError):host.validate_config(bad,expected)
        (self.root/'memory.max').write_text(str(512*1024**2));(self.root/'memory.swap.max').write_text('1');(self.root/'cpu.max').write_text('100000 100000')
        with self.assertRaises(RuntimeError):gate.verify_limits(self.root)

    def bindings(self):
        group='0::/docker-'+CID+'.scope'
        init=dict(pid=400,starttime_ticks=10,namespace_pids=[400,1],uid=[1001]*4,cgroup=group,
                  pid_namespace='pid:[2]',cgroup_namespace='cgroup:[2]')
        worker=dict(init,pid=401,starttime_ticks=11,namespace_pids=[401,7])
        inside_init=dict(pid=1,starttime_ticks=10,cgroup='0::/',pid_namespace='pid:[2]',cgroup_namespace='cgroup:[2]')
        inside_worker=dict(inside_init,pid=7,starttime_ticks=11)
        return init,worker,dict(init=inside_init,worker=inside_worker,uid=1001)

    def test_host_and_namespace_pids_cannot_be_interchanged_or_reused(self):
        init,worker,ready=self.bindings();(self.root/'cgroup.procs').write_text('400\n401\n')
        def get(pid,proc):return {400:init,401:worker}[pid]
        with patch.object(host,'host_identity',side_effect=get),patch.object(host.os,'readlink',return_value='host:[1]'):
            self.assertEqual(host.bind_worker(self.root,init,ready,1001),worker)
            for field,badvalue in [('pid',401),('starttime_ticks',12),('pid_namespace','pid:[99]'),('cgroup','0::/outside')]:
                bad=copy.deepcopy(ready);bad['worker'][field]=badvalue
                with self.subTest(field=field),self.assertRaises(RuntimeError):host.bind_worker(self.root,init,bad,1001)
            bad=copy.deepcopy(ready);bad['init']['starttime_ticks']=20
            with self.assertRaises(RuntimeError):host.bind_worker(self.root,init,bad,1001)
            worker['namespace_pids']=[401,400,7]
            with self.assertRaises(RuntimeError):host.bind_worker(self.root,init,ready,1001)

    def test_handshakes_refuse_wrong_identity_timeout_and_early_exit(self):
        path=self.root/'go.json';token={'unit':UNIT}
        path.write_text(json.dumps(token));entry.wait_marker(path,token,1)
        with self.assertRaises(RuntimeError):entry.wait_marker(path,{'unit':'other'},1)
        with self.assertRaises(RuntimeError):entry.wait_marker(self.root/'missing',token,0)
        with patch.object(host,'inspect_container',return_value={'State':{'Running':False}}):
            with self.assertRaises(RuntimeError):host.wait_report(self.root/'missing',CID,1)

    def test_final_accounting_is_monotonic_and_oom_is_not_only_init_exit(self):
        before={'memory.current':90,'memory.peak':100,'memory.stat':{'anon':40,'file':20},
                'cpu.stat':{'usage_usec':40},'memory.events':dict(max=2,oom=0,oom_kill=0,oom_group_kill=0)}
        after=copy.deepcopy(before);after['memory.peak']=110;after['cpu.stat']['usage_usec']=42
        host.validate_counters(after,before)
        for section,key,value in [('memory.events','oom_kill',1),('cpu.stat','usage_usec',39),('memory.events','max',1)]:
            bad=copy.deepcopy(after);bad[section][key]=value
            with self.assertRaises(RuntimeError):host.validate_counters(bad,before)
        after['memory.peak']=99
        with self.assertRaises(RuntimeError):host.validate_counters(after,before)
        after['memory.peak']=110
        before['cpu.stat']['usage_usec']=float('nan')
        with self.assertRaises(RuntimeError):host.validate_counters(after,before)

    def test_cleanup_keeps_going_after_stop_timeout_and_log_failure(self):
        calls=[]
        def run(*args,**kwargs):
            calls.append(args[0])
            if args[0]=='stop':raise subprocess.TimeoutExpired('docker stop',20)
            if args[0]=='logs':raise RuntimeError('log retrieval unavailable')
            if args[0]=='inspect':return SimpleNamespace(returncode=1,stdout='[]',stderr='Error: No such object: '+CID)
            return SimpleNamespace(returncode=0,stdout='',stderr='')
        info={'State':{'Running':False,'OOMKilled':False},'RestartCount':0}
        with patch.object(host,'docker',side_effect=run),patch.object(host,'inspect_container',return_value=info):
            result=host.cleanup_container(CID,self.root)
        self.assertEqual(calls,['stop','kill','wait','logs','rm','inspect'])
        self.assertTrue(result['cleanup_complete']);self.assertIn('log_error',result)
        with patch.object(host,'docker') as tool:
            self.assertFalse(host.cleanup_container('other',self.root)['cleanup_complete']);tool.assert_not_called()

    def test_daemon_failure_does_not_count_as_confirmed_absence(self):
        def run(*args,**kwargs):
            return SimpleNamespace(returncode=int(args[0]=='inspect'),stdout='',stderr='Cannot connect to Docker daemon')
        info={'State':{'Running':False,'OOMKilled':False},'RestartCount':0}
        with patch.object(host,'docker',side_effect=run),patch.object(host,'inspect_container',return_value=info):
            result=host.cleanup_container(CID,self.root)
        self.assertFalse(result['cleanup_complete']);self.assertIn('cleanup_error',result)

    def test_uncertain_create_recovery_requires_unique_name_label_and_image(self):
        name='vui-resource-'+('a'*32);token={'unit':UNIT}
        row={'Id':CID,'Name':'/'+name,'Image':'sha256:fixture','Config':{'Labels':{'vui.resource.unit':UNIT}}}
        def run(*args,**kwargs):return SimpleNamespace(returncode=0,stdout=json.dumps([row]),stderr='')
        with patch.object(host,'docker',side_effect=run):
            self.assertEqual(host.recover_created(name,token,'sha256:fixture'),CID)
            row['Config']['Labels']['vui.resource.unit']='another'
            with self.assertRaises(RuntimeError):host.recover_created(name,token,'sha256:fixture')

    def report_fixture(self, token, ready):
        policy={'mmap_threshold':None,'malloc_tunable_present':False}
        metrics={'memory.current':90,'memory.peak':100,'memory.stat':{'anon':40,'file':20},
                 'cpu.stat':{'usage_usec':40},'memory.events':dict(max=2,oom=0,oom_kill=0,oom_group_kill=0)}
        roles={role:dict(pid=pid,ppid=parent,starttime_ticks=100+pid,cgroup='0::/',process_group=8,allocator=policy)
               for role,pid,parent in [('watchdog',8,7),('core',9,8)]}
        report=dict(source_commit=token['source_commit'],unit=token['unit'],cgroup_backend='docker-private',
            service_cgroup='0::/',duration_profile='smoke',outcome='passed',complete=True,worker_pid=7,
            limits=ready['limits'],base_page_size_bytes=ready['page_size'],requested_memory_mib=512,
            bundle_sha256=hashlib.sha256(b'bundle').hexdigest(),metrics=metrics,
            concurrent_workload={'requests':100,'concurrency':10,'path':'/api/auth/me','status_200':100},
            proxy_workload=dict(cleanup_complete=True,no_direct=True,
                positive=[dict(concurrency=n,requests=q,status_200=q,target_deliveries=q,body_bytes=19) for n,q in [(1,10),(10,100)]],
                negative={name:dict(attempts=1,target_deliveries=0,no_direct=True,reason_markers=markers)
                    for name,markers in [('wrong_uuid',['unknown uuid']),('wrong_ca',['x509','unknown authority'])]},
                server_tree=dict(parent_role='resource_fixture_worker',worker_pid=7,runtime_key='x86_64-musl',
                    policy=policy,roles=roles,cleanup_complete=True)),stages=[])
        start=20
        for name in host.SMOKE_STAGES:
            wall=60 if name.endswith('_idle_60_seconds') else 2
            report['stages'].append(dict(name=name,outcome='passed',started_monotonic=start,wall_seconds=wall,
                cpu_usage_usec=1,memory_current_bytes=90,memory_peak_bytes=100,
                memory_stat=metrics['memory.stat'],memory_events=metrics['memory.events']))
            start+=wall
        return report

    def test_report_rejects_missing_auth_negative_body_and_wrong_runtime(self):
        token=dict(source_commit='a'*40,unit=UNIT,target='x86_64-musl')
        ready=dict(worker={'pid':7},limits={'memory.max':512*1024**2,'memory.swap.max':0,'cpu.max':'100000 100000'},page_size=4096)
        report=self.report_fixture(token,ready);host.validate_report(report,token,ready,512)
        for mode in ('auth','negative','body','target','duplicate_stage','short_idle','nan','stage_events','final_events'):
            bad=copy.deepcopy(report)
            if mode=='auth':del bad['concurrent_workload']
            elif mode=='negative':bad['proxy_workload']['negative']['wrong_ca']['reason_markers']=[]
            elif mode=='body':bad['proxy_workload']['positive'][0]['body_bytes']=1
            elif mode=='target':bad['proxy_workload']['server_tree']['runtime_key']='x86_64-gnu'
            elif mode=='duplicate_stage':bad['stages'][-1]=bad['stages'][-2]
            elif mode=='short_idle':bad['stages'][9]['wall_seconds']=59
            elif mode=='nan':bad['metrics']['cpu.stat']['usage_usec']=float('nan')
            elif mode=='stage_events':bad['stages'][0]['memory_events']=dict(bad['stages'][0]['memory_events'],max=3)
            else:bad['metrics']['memory.events']=dict(bad['metrics']['memory.events'],max=1)
            with self.subTest(mode=mode),self.assertRaises(RuntimeError):host.validate_report(bad,token,ready,512)

    def test_coordinator_failures_never_ack_and_always_attempt_exact_cleanup(self):
        for mode in ('success','bad_report','host_oom','missing_terminal','invalid_create','empty_create'):
            with self.subTest(mode=mode),ExitStack() as stack:
                bundle=self.root/'bundle.zip';bundle.write_bytes(b'bundle')
                bundle.with_suffix('.zip.sha256').write_text(hashlib.sha256(b'bundle').hexdigest())
                output=self.root/mode
                args=SimpleNamespace(bundle=bundle,output=output,source_commit='a'*40,target='x86_64-musl',
                    image='fixture',memory_mib=512,duration_profile='smoke')
                config,expected=self.config();config['Config']['User']=f'{os.getuid()}:{os.getgid()}';config['State']['Pid']=400
                image=dict(Id=expected['image_id'],Architecture='amd64')
                init,worker,bindings=self.bindings()
                ready=dict(worker=bindings['worker'],init=bindings['init'],uid=os.getuid(),gid=os.getgid(),
                    page_size=os.sysconf('SC_PAGE_SIZE'),limits={'memory.max':512*1024**2,'memory.swap.max':0,'cpu.max':'100000 100000'},observed_monotonic=1)
                record={}
                def run(*words,**kwargs):
                    if words[:2]==('image','inspect'):return SimpleNamespace(stdout=json.dumps([image]),stderr='',returncode=0)
                    if words[0]=='create':
                        unit=words[words.index('--unit')+1];record['token']=dict(unit=unit,source_commit=args.source_commit,target=args.target)
                        value='invalid' if mode=='invalid_create' else ('' if mode=='empty_create' else CID)
                        return SimpleNamespace(stdout=value,stderr='',returncode=0)
                    return SimpleNamespace(stdout='0' if words[0]=='wait' else '',stderr='',returncode=0)
                def wait(path,cid,timeout):
                    token=record['token'];value=dict(ready,**token)
                    report=self.report_fixture(token,value);value['metrics']=copy.deepcopy(report['metrics'])
                    record['metrics']=copy.deepcopy(value['metrics'])
                    if path.name=='ready.json':return value
                    if mode=='missing_terminal':raise RuntimeError('missing terminal')
                    if mode=='bad_report':del report['concurrent_workload']
                    (output/'worker.json').write_text(json.dumps(report))
                    return dict(value,returncode=0,started_monotonic=15,finished_monotonic=196,wall_seconds=181)
                def metrics(path):
                    value=copy.deepcopy(record['metrics'])
                    if mode=='host_oom':value['memory.events']['oom_kill']=1
                    return value
                stack.enter_context(patch.object(host.gate,'require_hosted_runner'))
                stack.enter_context(patch.object(host.platform,'machine',return_value='x86_64'))
                stack.enter_context(patch.object(host,'docker',side_effect=run))
                stack.enter_context(patch.object(host,'wait_report',side_effect=wait))
                stack.enter_context(patch.object(host,'inspect_container',return_value=config))
                stack.enter_context(patch.object(host,'host_identity',return_value=init))
                stack.enter_context(patch.object(host,'host_directory',return_value=self.root))
                stack.enter_context(patch.object(host,'bind_worker',return_value=worker))
                stack.enter_context(patch.object(host.gate,'verify_limits',return_value=ready['limits']))
                stack.enter_context(patch.object(host.gate,'metrics',side_effect=metrics))
                stack.enter_context(patch.object(host.time,'monotonic',side_effect=[10,200]))
                cleanup=stack.enter_context(patch.object(host,'cleanup_container',return_value={'cleanup_complete':True}))
                recover=stack.enter_context(patch.object(host,'recover_created',return_value=CID))
                with patch('builtins.print'):
                    status=host.coordinator(args)
                self.assertEqual(status,0 if mode=='success' else 1)
                self.assertEqual((output/'ack.json').exists(),mode=='success')
                cleanup.assert_called_once_with(CID,output)
                if mode in ('invalid_create','empty_create'):recover.assert_called_once()


if __name__=='__main__':unittest.main()
