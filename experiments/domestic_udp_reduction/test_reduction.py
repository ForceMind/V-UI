"""Synthetic verifier controls only; no Go compile or protocol claim."""
from copy import deepcopy
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import contract as c
import verify_build as v
import adapter
r=c.r
LEGACY=Path(os.environ['VUI_STOCK_UDP_LEGACY']).resolve()
BOX=Path(os.environ['VUI_REDUCED_SING_BOX']).resolve()
VMESS=Path(os.environ['VUI_REDUCED_SING_VMESS']).resolve()

class ReductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h=c.helpers(LEGACY);cls.tests=v.expected_tests(cls.h)
        cls.before=c.manifest({'sing-box':BOX,'sing-vmess':VMESS})
        cls.inputs=c.validate_inputs(LEGACY)

    def test_exact_patch_and_count_closure(self):
        self.assertEqual(len(c.PATCHES),5);self.assertEqual(len(c.TESTS),8)
        self.assertEqual(c.PATCHES[:4],tuple(x['path'] for x in c.DEPENDENCIES['patches'][:4]))
        self.assertNotIn('PacketSession', (c.HERE/'count_test.go').read_text())
        self.assertEqual(len(c.LIFECYCLE_TOP),12)
        self.assertNotIn('TestLifecycleXUDPBlockedInboundWrite',c.LIFECYCLE_TOP)

    def test_exact_two_cases_four_leaves_and_roles(self):
        p=r.load('reduced_test_packet',LEGACY/'experiments/xudp_packet/packet_boundary.py')
        cases=[adapter.plan(p,a,'I')[0] for a in c.ARMS]
        self.assertEqual((len(cases),sum(len(x['packets']) for x in cases)),(2,4))
        for case in cases:
            self.assertEqual(case['roles'],{'domestic':'candidate','overseas':'official','device-mihomo':'mihomo'})
            self.assertEqual([x['size'] for x in case['packets']],[1200,1200])
            payload=[p.packet_bodies(case['id']+'/'+x['id'],x)[0] for x in case['packets']]
            self.assertNotEqual(*payload)
        for arm,stage in [('stock','I'),('reduced','C'),('full-reference','E')]:
            with self.assertRaisesRegex(ValueError,'Unknown reduced'):adapter.plan(p,arm,stage)

    def test_full_production_manifest_and_four_changes(self):
        after={**self.before,**{n:d['after'] for n,d in c.PINS['source_files'].items()}}
        c.validate_source(self.before,after)
        for name,value in [('sing/protocol/socks/association.go','a'*64),('sing-box/route/conn.go',self.before['sing-box/route/conn.go']),('sing-vmess/xudp.go','b'*64)]:
            bad={**after,name:value}
            with self.assertRaises(ValueError):c.validate_source(self.before,bad)
        bad=dict(self.before);bad.pop(next(iter(bad)))
        with self.assertRaises(ValueError):c.validate_source(bad,after)

    def test_go_exact_sets_and_positive(self):
        self.assertEqual({g:len(s) for g,s in self.tests.items()},dict(zip(c.GROUPS,(13,20,2,12,27,2,17,1))))
        self.assertEqual(sum(map(len,self.tests.values())),94)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'go.jsonl'
            for group,names in self.tests.items():
                rows=self.rows(names);self.write_rows(path,rows);v.strict_go(path,names,'example/package')

    def rows(self,names):
        rows=[]
        def walk(name):
            rows.append(dict(Package='example/package',Action='run',Test=name))
            for child in sorted(n for n in names if n.rpartition('/')[0]==name):walk(child)
            rows.append(dict(Package='example/package',Action='pass',Test=name))
        for top in sorted(n for n in names if '/' not in n):walk(top)
        return rows+[dict(Package='example/package',Action='pass')]

    def semantic_rows(self,group,names):
        rows=self.rows(names);events=[]
        if group in ('mux-encrypted-cancel','mux-empty-datagram'):
            filename='test_verify_encrypted_cancel.py' if group=='mux-encrypted-cancel' else 'test_verify_empty_datagram.py'
            factory=r.load('reduced_synthetic_'+group,LEGACY/'experiments/xudp_mux'/filename)
            events=[x for x in factory.synthetic_rows() if x.get('Action')=='output']
        elif group=='lifecycle':
            sys.path.insert(0,str(LEGACY/'experiments/xudp_address'))
            factory=r.load('reduced_lifecycle_factory',LEGACY/'experiments/xudp_address/test_route_lifecycle_verifier.py')
            for name in names-{'TestLifecycleXUDPGuardScoping'}:
                events.extend({'Action':'output','Test':name,'Output':'XUDP_ROUTE_LIFECYCLE '+json.dumps(x)+'\n'} for x in factory.synthetic_events('lifecycle',name))
        elif group=='mux-route':
            for name in names:
                data=[{'event':'mux_arm_snapshot','arm':'mux','parent_writers':1,'route_copies':0,'route_callbacks':0,'cancelers':0,'parent_transport_open':True,'owned_outbound_closed':True},
                    {'event':'restore_reads'},{'event':'cleanup_start'},
                    {'event':'cleanup_joined','route_copy_workers':0,'route_callback_workers':0,'canceler_workers':0,'mux_cleanup_workers':0,'active_transport_writes':0,'test_owned_workers_joined':True,'actual_route_workers_joined':True}]
                events.extend({'Action':'output','Test':name,'Output':'XUDP_ROUTE_LIFECYCLE '+json.dumps({**x,'case':name,'seq':i})+'\n'} for i,x in enumerate(data))
        result=[]
        for row in rows:
            if row['Action']=='pass' and row.get('Test'):result.extend(e for e in events if e.get('Test')==row['Test'])
            result.append(row)
        return result
    def write_rows(self,path,rows):path.write_text(''.join(json.dumps(x)+'\n' for x in rows))

    def test_go_missing_extra_repeated_skipped_and_wrong_package_refused(self):
        names={'TestA','TestB'};good=self.rows(names)
        mutations=[lambda x:x.pop(0),lambda x:x.pop(1),lambda x:x.append(dict(Package='example/package',Action='pass')),
            lambda x:x.append(dict(x[0])),lambda x:x[1].update(Action='skip'),lambda x:x[1].update(Package='wrong'),
            lambda x:x[0].update(Test='TestExtra'),lambda x:x.reverse(),
            lambda x:x.insert(0,dict(Package='example/package',Action='pass')),
            lambda x:x.append(dict(Package='example/package',Action='run-extra'))]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'go.jsonl'
            for mutate in mutations:
                rows=deepcopy(good);mutate(rows);self.write_rows(path,rows)
                with self.assertRaises(ValueError):v.strict_go(path,names,'example/package')

        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'go.jsonl'
            for raw in ('{"Package":"wrong","Package":"example/package","Action":"pass"}',
                        '{"Package":"example/package","Action":"run","Action":"pass"}',
                        '{"Package":"example/package","Action":"pass","Elapsed":NaN}',
                        '{"Package":"example/package","Action":"pass","Elapsed":1e999}'):
                path.write_text(raw+'\n')
                with self.assertRaises(ValueError):v.strict_go(path,names,'example/package')

    def fixture(self,directory):
        identity={'product':{'commit':'a'*40,'tree':'b'*40,'files':{}},'legacy':{'commit':r.LEGACY,'tree':r.LEGACY_TREE,'files':{}}}
        dirs={'sing-box':Path('/synthetic/build/sing-box'),'sing-vmess':Path('/synthetic/build/sing-vmess')};box=dirs['sing-box'];vmess=dirs['sing-vmess'];out=box.parent
        after={**self.before,**{n:d['after'] for n,d in c.PINS['source_files'].items()}}
        injected={target:self.inputs[name] for name,target in c.TESTS.items()}
        mod_before=(BOX/'go.mod').read_bytes();mod_after=mod_before+b'\nreplace github.com/sagernet/sing-vmess => '+str(vmess).encode()+b'\n'
        tested={**after,**injected,'sing-box/go.mod':__import__('hashlib').sha256(mod_after).hexdigest()}
        graph=c.read(c.HERE/'MODULE_GRAPH.json')
        data={'schema_version':1,'kind':c.KIND,'product_qualified':False,'source_identity':identity,'fixed_sources':c.PINS['fixed_sources'],
          'input_files':self.inputs,'patches':[{'path':p,'sha256':self.inputs[p]} for p in c.PATCHES],
          'version':c.version(identity),'binary_sha256':'c'*64,'source_directories':{k:str(p) for k,p in dirs.items()},
          'build':{'go_binary_sha256':c.PINS['go_binary_sha256'],'go_version':'go1.25.14','flags':['-trimpath','-buildvcs=false'],'goos':'linux','goarch':'amd64','cgo_enabled':'0','tags':[]},
          'injected_tests':injected,'module_graph_sha256':c.digest(graph),
          'checks':[{'label':n,'returncode':0} for n in [g+'-'+m for g in c.GROUPS for m in c.MODES]+[f'cross-{p}-{os}' for p in c.CROSS for os in ('darwin','windows')]+['build','module-verify']]}
        for name,value in [('source-before',self.before),('source-after',after),('source-tested',tested),('source-post',tested),('normalized-modules',graph),('provenance',data)]:r.save(directory/(name+'.json'),value)
        (directory/'go.mod.before.txt').write_bytes(mod_before);(directory/'go.mod.after.txt').write_bytes(mod_after)
        env={**c.ENV,'GOMODCACHE':'/synthetic/mod','GOCACHE':'/synthetic/cache','VUI_LIFECYCLE_PHASE':'lifecycle','VUI_MUX_ARM':'mux'};go='/synthetic/go/bin/go';product=Path('/synthetic/product')
        def command(label,args,cwd,extra=None,stdout=''):
            r.save(directory/(label+'.exit.json'),{'args':list(map(str,args)),'cwd':str(cwd),'environment':{**env,**(extra or {})},'pid':42,'process_group':42,'returncode':0,'timeout':False,'signals':[],'leader_joined':True,'process_group_gone':True})
            (directory/(label+'.stdout')).write_text(stdout);(directory/(label+'.stderr')).write_text('')
        command('compiler',[go,'version'],product,stdout='go version go1.25.14 linux/amd64\n')
        for i,name in enumerate(c.PATCHES):
            for suffix,extra in [('check',['--check']),('apply',[])]:command(f'patch-{i}-{suffix}',['git','apply',*extra,(product if i==4 else Path('/synthetic/legacy'))/name],vmess if i in (0,2) else box)
        command('gofmt-source',[Path(go).with_name('gofmt'),'-w',vmess/'mux.go',vmess/'mux_writer.go'],out)
        command('replace',[go,'mod','edit','-replace=github.com/sagernet/sing-vmess='+str(vmess)],box)
        raw=[]
        for row in graph:
            x={'Path':row['path'],'Version':row['version'],'Main':row['main']}
            if row['replace']:x.update(Replace={'Path':str(vmess)},Dir=str(vmess))
            raw.append(x)
        for name in ('modules','modules-after'):command(name,[go,'list','-m','-json','all'],box,stdout='\n'.join(json.dumps(x) for x in raw))
        for group,(pattern,package) in c.GROUPS.items():
            for mode,flags in c.MODES.items():
                label=group+'-'+mode;command(label,[go,'test','-json','-count=1','-timeout=120s',*flags,'-run',pattern,package],box)
                rows=self.semantic_rows(group,self.tests[group])
                for row in rows:row['Package']='github.com/sagernet/sing-box/route' if package=='./route' else package
                self.write_rows(directory/(label+'.stdout'),rows)
        for label,package in c.CROSS.items():
            for target in ('darwin','windows'):
                name=f'cross-{label}-{target}';command(name,[go,'test','-c','-o',out/(name+'.test'),package],box,{'GOOS':target,'CGO_ENABLED':'0'})
        command('build',[go,'build','-trimpath','-buildvcs=false','-ldflags=-X github.com/sagernet/sing-box/constant.Version='+data['version'],'-o',out/'reduced-sing-box','./cmd/sing-box'],box,{'CGO_ENABLED':'0'})
        command('module-verify',[go,'mod','verify'],box,stdout='all modules verified\n')
        command('version',[out/'reduced-sing-box','version'],out,{'CGO_ENABLED':'0'},'sing-box version '+data['version']+'\n')
        return identity

    def check_fixture(self,directory,identity):
        # Synthetic metadata, real verifier and frozen semantic factories.
        with patch.object(c,'identity',return_value=identity):return v.verify(directory,LEGACY)

    def test_complete_structural_build_positive(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);identity=self.fixture(directory)
            result=self.check_fixture(directory,identity)
            self.assertEqual(len(result['groups']),16);self.assertFalse(result['runtime_executed'])

    def test_build_identity_patch_source_and_module_mutations_refused(self):
        mutations=[('provenance.json',lambda d:d.update(version='1.14.2')),
          ('provenance.json',lambda d:d['patches'].reverse()),
          ('provenance.json',lambda d:d['build'].update(go_binary_sha256='0'*64)),
          ('source-post.json',lambda d:d.update({'sing-vmess/xudp.go':'0'*64})),
          ('source-after.json',lambda d:d.update({'sing-box/route/packet_session.go':'0'*64})),
          ('normalized-modules.json',lambda d:d[0].update(version='wrong')),
          ('count-race.exit.json',lambda d:d['args'].remove('-race')),
          ('mux-route-debug.exit.json',lambda d:d['environment'].update(VUI_MUX_ARM='route-only')),
          ('build.exit.json',lambda d:d.update(process_group_gone=False)),
          ('cross-route-windows.exit.json',lambda d:d['environment'].update(GOOS='linux'))]
        for name,mutate in mutations:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as tmp:
                directory=Path(tmp);identity=self.fixture(directory);data=c.read(directory/name);mutate(data);r.save(directory/name,data)
                with self.assertRaises(ValueError):self.check_fixture(directory,identity)

    def test_reduced_build_archive_missing_extra_and_sensitive_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);identity=self.fixture(directory)
            with patch.object(c,'identity',return_value=identity):
                verdict=v.verify(directory,LEGACY);r.save(directory/'VERIFICATION.json',verdict)
                r.save(directory/'RESULT.json',{'passed':True,'errors':[],'runtime_executed':False,'product_qualified':False})
                adapter.reduced_build(directory,LEGACY)
                (directory/'unknown.txt').write_text('extra')
                with self.assertRaises(ValueError):adapter.reduced_build(directory,LEGACY)
                (directory/'unknown.txt').unlink()
                saved=(directory/'count-race.stdout').read_bytes();(directory/'count-race.stdout').unlink()
                with self.assertRaises(ValueError):adapter.reduced_build(directory,LEGACY)
                (directory/'count-race.stdout').write_bytes(saved)
                (directory/'build.stderr').write_text('token: unexpected-value')
                with self.assertRaises(ValueError):adapter.reduced_build(directory,LEGACY)

    def test_opt_in_adapter_isolated_and_old_defaults_unchanged(self):
        script="""import adapter,contract as c;import evidence as e;import runtime as r
assert set(r.ARMS)=={'stock','candidate','overseas-stock'}
old=r.verify_case
adapter.install()
assert r.ARMS==c.ARMS and r.KIND==c.RUNTIME_KIND and e.KIND==c.RUNTIME_KIND+'-archive'
assert r.verify_case is not old
assert e.build_files(None,'reduced')==c.allowed_files()
"""
        call=subprocess.run([sys.executable,'-c',script],cwd=c.HERE,capture_output=True,text=True,timeout=30)
        self.assertEqual(call.returncode,0,call.stderr)

    def test_owned_build_runner_cwd_and_environment(self):
        import run_matrix
        with tempfile.TemporaryDirectory() as tmp:
            env={**os.environ,'REDUCED_CONTROL':'present'}
            row,out,err=run_matrix.run([sys.executable,'-c','import os;print(os.getcwd());print(os.environ["REDUCED_CONTROL"])'],cwd=tmp,env=env,timeout=5)
            self.assertEqual(out.splitlines(),[tmp,'present']);self.assertFalse(row['timeout']);self.assertTrue(row['process_group_gone'])

    def test_complete_reduced_archive_roundtrip_in_isolated_process(self):
        script="""from test_reduction import *
from types import SimpleNamespace
import archive_fixture as f
ReductionTests.setUpClass();test=ReductionTests()
with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp);build=root/'build';build.mkdir();identity=test.fixture(build)
 with patch.object(c,'identity',return_value=identity),patch.object(r,'source',side_effect=lambda repo,**kw:identity['legacy' if kw.get('legacy') else 'product']):
  report=v.verify(build,LEGACY);r.save(build/'VERIFICATION.json',report);r.save(build/'RESULT.json',{'passed':True,'errors':[],'runtime_executed':False,'product_qualified':False})
  f.runtime(root/'runtime',LEGACY,build,identity)
  args=SimpleNamespace(runtime=root/'runtime',output=root/'archive',legacy_repository=LEGACY,arm='reduced',build_evidence=build)
  adapter.e.prepare(args)
  assert adapter.e.verify(args.output,LEGACY,'reduced')['capacity_gate_passed']
  stage=root/'runtime/I'
  for filename,mutation in [('input-identity.json',lambda d:d['binaries']['candidate'].update(version='1.14.2-vui-socks-owner-experiment-'+r.LEGACY[:12])),('process-binaries.json',lambda d:d[0].update(sha256=r.STOCK))]:
   path=stage/filename;saved=path.read_bytes();value=c.read(path);mutation(value);r.save(path,value);f.rehash(stage)
   try:adapter.e.verify_stage(stage,LEGACY,'reduced','I')
   except (ValueError,KeyError):pass
   else:raise AssertionError('swapped build/role identity accepted')
   path.write_bytes(saved);f.rehash(stage)
  for filename in ('post-identity.json','I_vless_small_idle/packets.json','I_vless_small_idle/socks-association-observation.json'):
   path=stage/filename;saved=path.read_bytes();path.unlink();f.rehash(stage)
   try:adapter.e.verify_stage(stage,LEGACY,'reduced','I')
   except (ValueError,KeyError,FileNotFoundError):pass
   else:raise AssertionError('missing raw/post member accepted')
   path.write_bytes(saved);f.rehash(stage)
  original=c.read(args.output/'ARCHIVE.json');bad=deepcopy(original);bad['schema_version']=True;r.save(args.output/'ARCHIVE.json',bad)
  try:adapter.e.verify(args.output,LEGACY,'reduced')
  except ValueError:pass
  else:raise AssertionError('boolean schema accepted')
  r.save(args.output/'ARCHIVE.json',original)
  (args.output/'build/count-race.stdout').unlink()
  try:adapter.e.verify(args.output,LEGACY,'reduced')
  except (ValueError,KeyError,FileNotFoundError):pass
  else:raise AssertionError('missing build evidence accepted')
"""
        call=subprocess.run([sys.executable,'-c',script],cwd=c.HERE,env=os.environ,capture_output=True,text=True,timeout=90)
        self.assertEqual(call.returncode,0,call.stdout+call.stderr)

    def test_partial_failed_build_archive_never_qualifies(self):
        from types import SimpleNamespace
        import build_failure as f
        identity={'synthetic':True}
        with tempfile.TemporaryDirectory() as tmp,patch.object(c,'identity',return_value=identity):
            root=Path(tmp);raw=root/'raw';raw.mkdir()
            r.save(raw/'failure.json',{'kind':f.KIND,'failed':True,'error_type':'ValueError','error':'synthetic failure','runtime_executed':False,'product_qualified':False})
            args=SimpleNamespace(runtime=raw,output=root/'archive',legacy_repository=LEGACY)
            f.prepare(args);self.assertFalse(f.verify(args.output,LEGACY)['build_qualified'])
            value=c.read(args.output/'BUILD_FAILURE.json');value['build_qualified']=True;r.save(args.output/'BUILD_FAILURE.json',value)
            with self.assertRaises(ValueError):f.verify(args.output,LEGACY)

if __name__=='__main__':unittest.main()
