"""Finite diagnostic synthetic proof controls; no core starts or qualification."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import runtime as r
import diagnostic as d
import test_verifier as tv

LEGACY=Path(os.environ['VUI_STOCK_UDP_LEGACY'])

class DiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=r.load('packet_boundary',LEGACY/'experiments/xudp_packet/packet_boundary.py')
        # Load this branch's passive modules, then only the frozen synthetic factory.
        r.load('socks_observation',r.HERE/'socks_observation.py')
        r.load('verify_socks_observation',r.HERE/'verify_socks_observation.py')
        cls.factory=r.load('diagnostic_synthetic_factory',LEGACY/'experiments/xudp_packet/test_socks_observation.py')
        cls.v=r.load('diagnostic_verify',r.HERE/'diagnostic_verify.py')
        tv.VerifierTests.p=cls.p

    def fixture(self,released=True):
        p=self.p;case=d.plan(p,'stock','I')[0]
        case,row,roles,known=tv.VerifierTests().fixture(case_override=case)
        observer,_,_,_,_,_=self.factory.fixture()
        role=roles[-1]
        for role in roles:role.update(case=case['id'],stops=[])
        role=roles[-1];observer.update(case=case['id'],mode=case['mode'],source_identity={},device_role=deepcopy(role))
        initial=observer['associations'][0]
        row['application_associations']=[{'id':0,**{k:deepcopy(initial[k]) for k in ('control','application','relay')}}]
        row.update(lifecycle_window_executed=True,recovery_attempted=True)
        for i,packet in enumerate(row['packets']):
            packet.update(application_socket=initial['application']['local'],socks_relay=initial['relay'],reply_source=initial['relay'],
                reply_monotonic=10 if i==0 else 20.3,terminal_monotonic=10 if i==0 else 20.3,
                target_window={'started':9 if i==0 else 16.2})
            packet['send_calls'][0].update(relay=initial['relay'],started=9 if i==0 else 16.2,completed=9.01 if i==0 else 16.21)
            packet['reply_calls'][0].update(relay=initial['relay'],completed=packet['reply_monotonic'])
        local={'host':'127.0.0.1','port':45000};remote={'host':'127.0.0.2','port':50000}
        def tcp(inode,local,remote):return {'inode':inode,'protocol':'tcp','proc_state':'01','local':local,'remote':remote}
        values={'domestic':[tcp(11,local,remote),tcp(14,{'host':'127.0.0.1','port':60000},{'host':'127.0.0.1','port':55000})],
            'overseas':[tcp(12,remote,local),{'inode':13,'protocol':'udp','proc_state':'07','local':{'host':'127.0.0.4','port':2003}}]}
        byrole={r['role']:r['launches'][0] for r in roles}
        def snap(start,socks):
            return {'monotonic':start,'sample_completed_monotonic':start+.004,
                'processes':[{'role':name,'pid':byrole[name]['pid'],'create_time':byrole[name]['birth_time'],
                    'races':[],'sockets':deepcopy(socks.get(name,[]))} for name in ('domestic','overseas')]}
        baseline=snap(9.1,{})
        samples=[baseline];events=[{'kind':'socket_snapshot','phase':'ready_before_business','snapshot':baseline}]
        for sample in observer['samples']:
            device=sample['device'];device.update(role='device-mihomo',pid=102,birth_time=102.,birth_time_after=102.)
            if 'executable_sha256' in sample:sample['executable_sha256']=r.MIHOMO
            if sample['phase']=='failure':sample['phase']='after_recover'
            if 'hop_sample' in sample:
                start=sample['hop_sample']['started'];empty=released and start>=11.995
                actual=snap(start,{} if empty else values);samples.append(actual)
                sample['hop_sample']={'started':start,'completed':actual['sample_completed_monotonic']}
                phase=('after_large_business' if sample['phase']=='after_large_business' else
                    'large_then_idle_diagnostic'+('_window_complete' if sample['phase']=='window_complete' else ''))
                events.append({'kind':'socket_snapshot','phase':phase,'snapshot':actual})
        proof={'mode':case['mode'],'baseline':baseline,'live':samples[1],
            'leg':{'upstream_port':50000,'domestic_local':local,'keys':[['domestic',100,11],['overseas',101,12],['overseas',101,13]]},
            'ingress_port':60000,'last_business_reply':10,'deadline':16,'first_absence':samples[3] if released else None,
            'final':samples[-1],'samples':samples,'prebaseline_samples':[],'release_passed':released}
        events.append({'kind':'small_endpoint_window','proof':proof})
        life={'kind':case['id'],'status':'observed','events':events,'product_qualified':False,'original_gate_unchanged':True}
        return case,row,roles,known,observer,life

    def check(self,items):
        case,row,roles,known,observer,life=deepcopy(items)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);(out/case['id']).mkdir()
            r.save(out/case['id']/'lifecycle-observation.json',life)
            r.save(out/case['id']/'socks-association-observation.json',observer)
            return d.verify(self.p,case,row,roles,known,out,{},r.verify_case)

    def test_seven_cases_ten_leaves_exact_roles_and_points(self):
        allcases=[c for arm,stages in d.ARMS.items() for stage in stages for c in d.plan(self.p,arm,stage)]
        self.assertEqual((len(allcases),sum(len(c['packets']) for c in allcases)),(7,10))
        self.assertEqual([(c['packets'][0]['direction'],c['packets'][0]['size']) for c in d.plan(self.p,'candidate','E')],
            [('uplink',16374),('uplink',16375),('downlink',16384),('downlink',16385)])
        self.assertFalse(any(s['diagnostic'] or s['expected_prefix'] for c in allcases for s in c['packets']))
        for arm in d.ARMS:
            c=d.plan(self.p,arm,'I')[0]
            self.assertEqual([s['size'] for s in c['packets']],[1200,1200])
            req=[self.p.packet_bodies(c['id']+'/'+s['id'],s)[0] for s in c['packets']]
            self.assertNotEqual(req[0],req[1])
        self.assertEqual(r.ARMS,{'stock':('C','D'),'candidate':('C','D'),'overseas-stock':('B',)})

    def test_full_positive_and_release_false_availability_true_stays_red(self):
        full=self.check(self.fixture());self.assertTrue(full['case_gate_passed'])
        value=self.check(self.fixture(False))
        self.assertTrue(value['execution_safe']);self.assertFalse(value['case_gate_passed'])
        self.assertFalse(value['diagnostic']['release_passed']);self.assertTrue(value['diagnostic']['next_packet_passed'])

    def test_new_window_packet_identity_mutations_refused(self):
        mutations=[
            ('release-forged',lambda c,r,rs,k,o,l:l['events'][-1]['proof'].update(release_passed=False)),
            ('early-deadline',lambda c,r,rs,k,o,l:l['events'][-1]['proof'].update(deadline=15.9)),
            ('window-missing',lambda c,r,rs,k,o,l:l['events'].pop()),
            ('first-missing',lambda c,r,rs,k,o,l:r['packets'].pop(0)),
            ('replayed-body',lambda c,r,rs,k,o,l:r['packets'][1].update(request=deepcopy(r['packets'][0]['request']))),
            ('extra-send',lambda c,r,rs,k,o,l:r['packets'][1]['send_calls'].append(deepcopy(r['packets'][1]['send_calls'][0]))),
            ('send-before-identity',lambda c,r,rs,k,o,l:r['packets'][1]['send_calls'][0].update(started=16.105)),
            ('quiet-send',lambda c,r,rs,k,o,l:r['packets'][1]['send_calls'][0].update(started=13)),
            ('swapped-control-inode',lambda c,r,rs,k,o,l:o['samples'][2]['associations'][0]['control_before'].update(inode=99999)),
            ('swapped-udp-inode',lambda c,r,rs,k,o,l:o['samples'][2]['associations'][0]['application'].update(inode=99999)),
            ('changed-relay',lambda c,r,rs,k,o,l:o['samples'][2]['associations'][0].update(relay=['127.0.0.1',3333])),
            ('changed-executable',lambda c,r,rs,k,o,l:o['samples'][2]['device'].update(executable={**o['samples'][2]['device']['executable'],'inode':999},executable_after={**o['samples'][2]['device']['executable_after'],'inode':999})),
            ('changed-birth',lambda c,r,rs,k,o,l:o['samples'][2]['device'].update(birth_time=888)),
            ('incomplete-window',lambda c,r,rs,k,o,l:o['samples'].pop(3)),
            ('changed-role-hash',lambda c,r,rs,k,o,l:o['device_role'].update(sha256='a'*64)),
            ('target-duplicate',lambda c,r,rs,k,o,l:r['target_ledger']['actual_datagrams'].append(deepcopy(r['target_ledger']['actual_datagrams'][0]))),
            ('close-proof-missing',lambda c,r,rs,k,o,l:r['cleanup'].pop('application_close_proofs')),
        ]
        for name,mutate in mutations:
            with self.subTest(mutation=name):
                items=self.fixture();self.assertTrue(self.check(items)['case_gate_passed']);mutate(*items)
                with self.assertRaises((ValueError,KeyError)):self.check(items)

    def test_absence_and_reappearance_cannot_claim_release(self):
        for released in (False,True):
            items=self.fixture(released);self.check(items)
            proof=items[-1]['events'][-1]['proof']
            if not released:proof['release_passed']=True
            else:
                proof['final']['processes']=deepcopy(proof['live']['processes'])
            with self.assertRaises(ValueError):self.check(items)

    def test_failed_first_tuple_leaves_window_and_recovery_unexecuted(self):
        items=list(self.fixture());case,row,roles,known,o,life=items
        first=row['packets'][0];row['packets']=[first]
        first.update(status='error',full_delivery_passed=False,error={'type':'ValueError','message':'Wrong reply port'})
        first['decoded_reply_before_assertion']['port']+=1
        import socket
        reply=self.p.packet_bodies(first['leaf'],case['packets'][0])[1]
        wire=b'\0\0\0\x01'+socket.inet_aton('127.0.0.3')+(2003).to_bytes(2,'big')+reply
        first['reply_calls'][0]['wire']=first['socks_reply_wire']=self.p.ident(wire)
        row.update(errors=[first['error']],unexecuted_leaves=[case['id']+'/recover'],recovery_attempted=False,lifecycle_window_executed=False)
        ledger=row['target_ledger'];ledger['packets']=ledger['packets'][:1];ledger['actual_datagrams']=ledger['actual_datagrams'][:1]
        ledger.update(planned_count=1,received_count=1)
        o['samples']=[o['samples'][0],o['samples'][-1]];o['samples'][0]['phase']='failure';o['samples'][0].pop('hop_sample')
        o.pop('last_business_reply');o.pop('deadline');life['events']=life['events'][:1];life['status']='error'
        result=self.check(items);self.assertTrue(result['execution_safe']);self.assertFalse(result['case_gate_passed'])
        self.assertFalse(result['diagnostic']['window_executed'])
        for kind in ('window','recovery','deadline','executable'):
            changed=deepcopy(items)
            if kind=='window':changed[1]['lifecycle_window_executed']=True
            elif kind=='recovery':changed[1]['recovery_attempted']=True
            elif kind=='deadline':changed[4]['deadline']=16
            else:
                sample=changed[4]['samples'][-1]['device']
                sample['executable']={**sample['executable'],'inode':999}
                sample['executable_after']={**sample['executable_after'],'inode':999}
            with self.subTest(kind=kind),self.assertRaises((ValueError,KeyError)):self.check(changed)

    def test_complete_archive_and_missing_raw_members_refused(self):
        subprocess.run([sys.executable,'-c','from test_diagnostic import archive_control; archive_control()'],cwd=r.HERE,check=True)

    def test_actual_candidate_import_order_keeps_frozen_verifier_isolated(self):
        script="""
import sys
from pathlib import Path
import runtime as r, diagnostic as d
from test_diagnostic import DiagnosticTests, LEGACY
# Make synthetic raw proof before contamination, never pre-fix the later cache.
DiagnosticTests.setUpClass();test=DiagnosticTests();items=test.fixture()
owner=r.load('standard_owner_adapter',LEGACY/'experiments/socks_idle_recovery/runtime_adapter.py')
frozen=owner.load_runtime(LEGACY)
old=sys.modules['verify_socks_observation']
assert old is frozen.socks and old.ROLE=='device-singbox'
r.load_framework(LEGACY)
assert sys.modules['verify_socks_observation'] is old
assert sys.modules['socks_observation'].ROLE=='device-mihomo'
# Real verification must select its own exact files despite the old cached name.
result=test.check(items)
assert result['case_gate_passed'] and result['diagnostic']['release_passed']
assert result['diagnostic']['next_packet_passed']
observer,verifier=test.v.passive_verifier()
assert Path(observer.__file__).resolve()==r.HERE/'socks_observation.py'
assert Path(verifier.__file__).resolve()==r.HERE/'verify_socks_observation.py'
assert observer.ROLE==verifier.ROLE=='device-mihomo'
assert sys.modules['verify_socks_observation'] is old and old.ROLE=='device-singbox'
# The repair opts into only two idle cases; it cannot repeat E or stock I.
d.install(idle_repair=True)
assert d.KIND=='vui-stock-client-udp-finite-idle-fixture-repair'
assert d.ARMS=={'candidate':('I',),'overseas-stock':('I',)}
cases=[c for arm,stages in d.ARMS.items() for stage in stages for c in d.plan(test.p,arm,stage)]
assert len(cases)==2 and sum(len(c['packets']) for c in cases)==4
for arm,stage in [('stock','I'),('candidate','E')]:
    try:d.plan(test.p,arm,stage)
    except ValueError:pass
    else:raise AssertionError('Repair exceeded exact two-case plan')
"""
        subprocess.run([sys.executable,'-c',script],cwd=r.HERE,check=True)

    def test_independent_install_and_archive_fixed_allowlist(self):
        script="""
import diagnostic as d, evidence as e, runtime as r
from pathlib import Path
import os
legacy=Path(os.environ['VUI_STOCK_UDP_LEGACY'])
d.install();safe,p=e.modules(legacy)
assert r.KIND==d.KIND and r.ARMS==d.ARMS
assert 'I_vless_small_idle/lifecycle-observation.json' in e.stage_files(p,'stock','I')
assert 'I_vless_small_idle/socks-association-observation.json' in e.stage_files(p,'stock','I')
assert not any(n.startswith('C/') or n.startswith('D/') for n in e.matrix_files(p,'candidate',legacy))
try:d.plan(p,'stock','E')
except ValueError:pass
else:raise AssertionError('Extra stock boundary allowed')
"""
        subprocess.run([sys.executable,'-c',script],cwd=r.HERE,check=True)

def archive_control():
    import hashlib
    from types import SimpleNamespace
    from unittest.mock import patch
    import evidence as e
    DiagnosticTests.setUpClass();test=DiagnosticTests()
    d.install()
    source={'commit':'a'*40,'tree':'b'*40,'files':{'synthetic.py':'c'*64}}
    safe,p=e.modules(LEGACY)
    with tempfile.TemporaryDirectory() as tmp, patch.object(r,'source',return_value=source):
        root=Path(tmp)/'runtime';stage=root/'I';case,row,roles,known,o,life=test.fixture()
        directory=stage/case['id'];directory.mkdir(parents=True)
        identity={'product':source,'legacy':source};o['source_identity']=identity
        with tempfile.TemporaryDirectory() as data:
            artifact=json.loads(subprocess.check_output([sys.executable,str(r.HERE/'export_node.py'),'--port','12345'],
                env={**os.environ,'VUI_DATA_DIR':data},text=True))
        node=artifact['node'];digest=lambda x:hashlib.sha256(r.canonical(x)).hexdigest()
        shell={'mixed-port':1080,'bind-address':'127.0.0.1','allow-lan':False,'mode':'rule','log-level':'debug','ipv6':False,
            'find-process-mode':'off','geo-auto-update':False,'profile':{'store-selected':False,'store-fake-ip':False},
            'proxies':[node],'rules':['MATCH,'+node['name']]}
        roles[-1]['config_sha256']=digest(shell);o['device_role']=deepcopy(roles[-1])
        export={'artifact':safe.h.c.life.scrub(artifact),'artifact_sha256':digest(artifact),'node_sha256':digest(node),
            'consumer_node_sha256':digest(node),'node_fields_unchanged':True,'consumer':'ordinary-yaml-node-in-listener-shell',
            'server_configuration':'isolated-research-guarded-not-product-udp','http_api_export_executed_here':False,
            'hash_scope':'raw-fake-fixture-export-before-redaction'}
        r.save(directory/'lifecycle-observation.json',life);r.save(directory/'socks-association-observation.json',o)
        row.update(case=case['id'],stage='I',protocol='vless',roles=case['roles'])
        row['verification']=r.verify_case(p,case,row,roles,known,stage,identity)
        assert row['verification']['case_gate_passed']
        report={'schema_version':1,'kind':r.KIND,'arm':'stock','stage':'I','product_qualified':False,'original_suite_passed':False,
            'observations_complete':True,'capacity_gate_passed':True,'cases':[row],'errors':[]}
        for name,value in {'input-identity.json':{'source_identity':identity,'binaries':known},'plan.json':r.plan(p,'stock','I'),
            'result.json':report,'process-binaries.json':roles,case['id']+'/result.json':row,
            case['id']+'/packets.json':row['packets'],case['id']+'/export-identity.json':export,
            'post-identity.json':{'source_identity':identity,'binary_sha256':{k:v['sha256'] for k,v in known.items()}}}.items():r.save(stage/name,value)
        def rehash():
            r.save(stage/'manifest.json',{'kind':r.KIND+'-text-evidence','files':{x.relative_to(stage).as_posix():r.sha(x)
                for x in stage.rglob('*') if x.is_file() and x.name!='manifest.json'}})
        rehash();checked=e.verify_stage(stage,LEGACY,'stock','I');assert checked['capacity_gate_passed']
        owned={'stage':'I','pid':123,'process_group':123,'timeout':False,'signals':[],'returncode':0,'leader_joined':True,
            'process_group_gone':True,'capacity_gate_passed':True,'safe_to_continue':True,'independent_verification':checked}
        r.save(root/'stages.json',[owned]);r.save(root/'MATRIX_RESULT.json',{'arm':'stock','all_stages_executed':True,
            'capacity_gate_passed':True,'product_qualified':False,'stages':[owned]})
        out=Path(tmp)/'archive'
        e.prepare(SimpleNamespace(runtime=root,output=out,legacy_repository=LEGACY,arm='stock',build_evidence=None))
        assert e.verify(out,LEGACY,'stock')['capacity_gate_passed']
        for name in ('lifecycle-observation.json','socks-association-observation.json','export-identity.json','packets.json'):
            member=directory/name;raw=member.read_bytes();member.unlink();rehash()
            try:
                try:
                    verdict=e.verify_stage(stage,LEGACY,'stock','I')
                    assert not verdict['execution_safe'] and not verdict['capacity_gate_passed']
                except (ValueError,KeyError,FileNotFoundError):pass
                else:raise AssertionError('Missing required member accepted: '+name)
            finally:member.write_bytes(raw);rehash()
        plan=json.loads((stage/'plan.json').read_text());plan[0]['packets'][0]['size']=1199;r.save(stage/'plan.json',plan);rehash()
        try:e.verify_stage(stage,LEGACY,'stock','I')
        except ValueError:pass
        else:raise AssertionError('Changed diagnostic size accepted')

if __name__=='__main__':unittest.main()
