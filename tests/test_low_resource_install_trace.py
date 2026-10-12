"""Finite install-call tracing preserves product semantics and strict accounting."""
import copy
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from scripts import low_resource_install_trace as trace
from scripts import low_resource_acceptance as gate

UNIT='vui-low-resource-'+('a'*32)+'.service'
COMMIT='b'*40


class InstallTraceTests(unittest.TestCase):
    def fixture(self):
        calls=[]
        def called(name):
            def call(*args,**kwargs):
                calls.append((name,args,kwargs))
                return name
            return call
        tools=SimpleNamespace(unpack_verified=called('unpack'),extract_runtime=called('runtime'),
            runtime_tree_digest=called('hash'),subprocess=SimpleNamespace(run=called('run')))
        def health(*args,**kwargs):
            calls.append(('health',args,kwargs));tools.runtime_tree_digest('nested-runtime');return 'healthy'
        tools.health_check=health
        return tools,calls

    def test_exact_calls_arguments_return_values_and_nested_health_hash_preserved(self):
        tools,calls=self.fixture();originals=vars(tools).copy();original_run=tools.subprocess.run
        value={'phases':[]};clock=0
        def metrics():
            nonlocal clock
            clock+=1
            return {'memory.current':1000,'memory.peak':2000+clock,'memory.stat':{'anon':100,'file':200},
                    'memory.events':{'max':0,'oom':0,'oom_kill':0,'oom_group_kill':0},'cpu.stat':{'usage_usec':clock}}
        began=time.monotonic()
        with trace.trace_installation(tools,value,metrics,lambda:None):
            self.assertEqual(tools.unpack_verified('archive','sha','destination'),'unpack')
            tools.extract_runtime('runtime','destination')
            for cmd in (['python','-m','ensurepip','--upgrade'],
                        ['python','-m','pip','--isolated','--disable-pip-version-check','install','--no-index'],
                        ['python','-m','pip','--isolated','check']):
                self.assertEqual(tools.subprocess.run(cmd,check=True,timeout=180),'run')
            tools.runtime_tree_digest('runtime')
            self.assertEqual(tools.health_check('release'),'healthy')
        self.assertEqual([r['name'] for r in value['phases']],list(trace.PHASES))
        self.assertEqual([r[0] for r in calls],['unpack','runtime','run','run','run','hash','health','hash'])
        self.assertEqual(calls[0][1],('archive','sha','destination'))
        self.assertEqual(calls[2][2],{'check':True,'timeout':180})
        for key,original in originals.items():self.assertIs(getattr(tools,key),original)
        self.assertIs(tools.subprocess.run,original_run)
        proof={'baseline_commit':trace.BASELINE,'source_commit':COMMIT,'product_bytes_unchanged':True}
        value.update(unit=UNIT,source_commit=COMMIT,service_cgroup='0::/system.slice/'+UNIT)
        report=dict(duration_profile='smoke',requested_memory_mib=512,service_cgroup=value['service_cgroup'],installation_trace=value,
                    stages=[dict(name='offline_stage_including_wheels',outcome='passed',started_monotonic=began,
                        wall_seconds=time.monotonic()-began,memory_peak_bytes=3000,cpu_usage_usec=100,
                        memory_events={'max':0,'oom':0,'oom_kill':0,'oom_group_kill':0})])
        trace.validate_trace(report,UNIT,COMMIT,proof)
        for kind in ('missing','order','source','unit','cgroup','proof','clock','nan','counter','peak','oom','cpu','profile'):
            bad=copy.deepcopy(report);bad_proof=copy.deepcopy(proof);v=bad['installation_trace'];rows=v['phases']
            if kind=='missing':rows.pop()
            elif kind=='order':rows.reverse()
            elif kind=='source':v['source_commit']='c'*40
            elif kind=='unit':v['unit']='other'
            elif kind=='cgroup':v['service_cgroup']='0::/wrong'
            elif kind=='proof':bad_proof['product_bytes_unchanged']=False
            elif kind=='clock':rows[-1]['finished_monotonic']+=100
            elif kind=='nan':rows[0]['before']['started_monotonic']=float('nan')
            elif kind=='counter':rows[1]['before']['metrics']['cpu.stat']['usage_usec']=0
            elif kind=='peak':rows[-1]['after']['metrics']['memory.peak']=4000
            elif kind=='oom':rows[0]['before']['metrics']['memory.events']['oom']=1
            elif kind=='cpu':bad['stages'][0]['cpu_usage_usec']=0
            elif kind=='profile':bad['requested_memory_mib']=384
            with self.subTest(kind=kind),self.assertRaises(RuntimeError):trace.validate_trace(bad,UNIT,COMMIT,bad_proof)

    def test_core_observation_order_and_descriptors_close_with_trace_scope(self):
        tools,_=self.fixture();tools.target_arch=lambda:'x86_64'
        events=[]
        observer=SimpleNamespace(capture=lambda *args:events.append(('capture',args)),
            observe=lambda:events.append(('observe',)) or {},close=lambda:events.append(('close',)))
        value={'phases':[]}
        with trace.trace_installation(tools,value,lambda:{},lambda:None,observer):
            tools.unpack_verified('archive','sha','destination')
            tools.subprocess.run(['python','-m','ensurepip','--upgrade'])
            tools.subprocess.run(['python','-m','pip','--isolated','--disable-pip-version-check','install'])
        self.assertEqual([event[0] for event in events],['capture','observe','observe','observe','close'])
        self.assertEqual(events[0][1],(Path('destination'),'x86_64','unpack'))
        self.assertTrue(all('payload_core_residency' in row for row in value['phases']))
        events.clear()
        def failed(*args):raise RuntimeError('product')
        tools.extract_runtime=failed
        with self.assertRaises(RuntimeError):
            with trace.trace_installation(tools,{'phases':[]},lambda:{},lambda:None,observer):
                tools.extract_runtime('a','b')
        self.assertEqual(events,[('close',)])

    def test_product_exception_identity_and_original_functions_survive_probe_failure(self):
        tools,_=self.fixture();failure=RuntimeError('product failed');count=0
        def product(*args):raise failure
        tools.extract_runtime=product
        original=tools.unpack_verified;original_run=tools.subprocess.run
        def checkpoint():
            nonlocal count
            count+=1
            if count==2:raise OSError('diagnostic write failed')
        value={'phases':[]}
        with self.assertRaises(RuntimeError) as caught:
            with trace.trace_installation(tools,value,lambda:{},checkpoint):tools.extract_runtime('a','b')
        self.assertIs(caught.exception,failure)
        self.assertIs(tools.extract_runtime,product);self.assertIs(tools.unpack_verified,original)
        self.assertIs(tools.subprocess.run,original_run)
        self.assertEqual(value['phases'][0]['outcome'],'failed')
        self.assertEqual(value['phases'][0]['accounting_error'],'OSError')

    def test_unrelated_run_is_forwarded_without_a_trace(self):
        tools,calls=self.fixture();value={'phases':[]}
        command=['python','-c','pass']
        with trace.trace_installation(tools,value,lambda:{},lambda:None):
            self.assertEqual(tools.subprocess.run(command,timeout=2),'run')
        self.assertEqual(value['phases'],[]);self.assertIs(calls[0][1][0],command)

    def test_provenance_rejects_changed_products_or_dirty_worktree(self):
        entries=b'100644 blob '+b'1'*40+b'\tapp/release_tools.py\n'
        for case in ('valid','allowed_controller','wrong_head','changed','dirty'):
            responses=[(COMMIT if case!='wrong_head' else 'c'*40).encode()+b'\n',entries,
                       (b'changed' if case=='changed' else entries.replace(b'1'*40,b'2'*40) if case=='allowed_controller' else entries),b'app/main.py\n' if case=='dirty' else b'']
            with self.subTest(case=case),patch.object(trace.subprocess,'check_output',side_effect=responses):
                if case in ('valid','allowed_controller'):
                    proof=trace.product_provenance(Path('.'),COMMIT)
                    self.assertEqual(proof['product_bytes_unchanged'],case=='valid');self.assertEqual(proof['tracked_file_count'],1)
                    self.assertEqual(proof['changed_product_files'],[] if case=='valid' else ['app/release_tools.py'])
                else:
                    with self.assertRaises(RuntimeError):trace.product_provenance(Path('.'),COMMIT)

    def test_provenance_rejects_other_product_changes_and_file_removal(self):
        base=b'100644 blob '+b'1'*40+b'\tapp/main.py\n'
        for current in (base.replace(b'1'*40,b'2'*40),b''):
            with patch.object(trace.subprocess,'check_output',side_effect=[COMMIT.encode(),base,current]):
                with self.assertRaises(RuntimeError):trace.product_provenance(Path('.'),COMMIT)

    def test_opt_in_flag_preserves_default_command_and_limits(self):
        args=SimpleNamespace(memory_mib=320,duration_profile='smoke',bundle=Path('/tmp/bundle'),source_commit=COMMIT)
        original=gate.systemd_command(args,UNIT,Path('/tmp/work'),Path('/tmp/report'))
        self.assertNotIn('--trace-install',original)
        args.trace_install=True
        command=gate.systemd_command(args,UNIT,Path('/tmp/work'),Path('/tmp/report'))
        self.assertEqual(command,original+['--trace-install'])
        self.assertIn('MemoryMax=320M',command);self.assertIn('RuntimeMaxSec=600',command)
        for memory,profile in ((384,'smoke'),(512,'sustained')):
            args.memory_mib=memory;args.duration_profile=profile
            with patch.object(gate,'require_hosted_runner'),self.assertRaisesRegex(RuntimeError,'Installation trace requires'):
                gate.coordinator(args)

    def test_workflow_builds_once_and_runs_both_original_caps(self):
        text=(Path(__file__).resolve().parents[1]/'.github/workflows/low-resource-install-trace.yml').read_text()
        self.assertEqual(text.count('python scripts/build_bundle.py'),1)
        self.assertIn('for memory in 512 320',text)
        self.assertIn('--trace-install',text)
        self.assertIn("github.event.label.name == 'run-low-resource-install-trace'",text)
        self.assertIn('ref: ${{ github.event.pull_request.head.sha }}',text)
        self.assertNotIn('duration-profile sustained',text)
        block=text.split('      - name: Preserve all smoke checks')[1].split('      - name: Preserve diagnostics')[0]
        script=block.split('        run: |\n')[1]
        script='\n'.join(line[10:] for line in script.splitlines())
        # A failed first lane must still run the second, and aggregate failure.
        with tempfile.TemporaryDirectory() as directory:
            log=Path(directory)/'calls'
            stub='python() { echo called >> '+str(log)+'; return 1; }\n'
            result=subprocess.run(['bash','-c',stub+script],env={'VUI_SOURCE_COMMIT':COMMIT},capture_output=True)
            self.assertEqual(result.returncode,1)
            self.assertEqual(log.read_text().splitlines(),['called','called'])
        import inspect
        self.assertNotIn('product_provenance(',inspect.getsource(gate.worker))
        self.assertIn('scripts/platform_support.py',trace.PRODUCT_PATHS)


if __name__=='__main__':unittest.main()
