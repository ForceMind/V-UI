"""Synthetic contract controls only; never launches a core or claims runtime qualification."""
from copy import deepcopy
import errno
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import runtime_adapter as a
import verify_runtime as v
import run_owner_runtime as r

REPO=Path(os.environ.get('VUI_OWNER_REPOSITORY',Path(__file__).resolve().parents[2])).resolve()
rt=a.load_runtime(REPO)
old_fixture=a.load('owner_old_socks_test_fixture',REPO/'experiments/xudp_packet/test_socks_observation.py')

def events(generations=2, packets=2):
    result=[]
    def add(transition,gen,count,cause='none'):
        result.append({'pid':123,'owner_id':1,'sequence':len(result)+1,'transition':transition,'generation':gen,
                       'packet_sequence':count,'accepted_packets':count,'rejected_sources':0,'remapped_packets':0,'cause':cause,
                       'source':'127.0.0.1:0' if transition=='create' else '127.0.0.1:56000',
                       'relay':'127.0.0.1:57000','target':':0' if transition=='create' else '127.0.0.3:1234'})
    add('create',0,0)
    for gen in range(1,generations+1):
        add('admitting',gen,gen);add('admitted',gen,gen)
        if gen<generations:
            add('retire',gen,gen,'session_end');add('join',gen,gen,'session_end');add('dormant',gen,gen)
    add('closing',generations,packets,'eof');add('closed',generations,packets,'eof')
    return result

def verify(rows,**kw):
    return v.verify_owner(rows,pid=123,owner_id=1,source='127.0.0.1:56000',relay='127.0.0.1:57000',target='127.0.0.3:1234',packets=kw.get('packets',2),generations=kw.get('generations',2))

def text(rows):return ''.join('INFO socks_udp_owner '+json.dumps(e)+'\n' for e in rows)

def summary_rows():
    result={arm:[{'stage':stage,'stage_execution_safe':True,'additional_verification_passed':True,
        'baseline_classification_preserved':True,'stage_gate_passed':arm=='on' or r.FROZEN_OFF_GATES[stage],
        'failure_observations':{'applicable':stage in ('A','U','V'),'failed_observations_verified':stage in ('A','U','V')}}
        for stage in stages] for arm,stages in a.ARMS.items()}
    for rows in result.values():
        for row in rows:
            if row['stage']=='D':row['socks_association_observations']={'observations_verified':True}
    return result

def config():return {'inbounds':[{'type':'mixed','listen':'127.0.0.1'}],'outbounds':[{'type':'vless','tag':a.TAG}],'route':{'final':a.TAG}}

def provenance():
    names={package+'-'+mode for package in ('common-network','common-bufio','protocol-socks') for mode in ('race','debug','darwin-compile','windows-compile')}
    names|={'build','module-verify','mux-route-race'}|{kind+'-'+mode for kind in ('mux-unit','mux-encrypted','mux-encrypted-cancel','mux-empty-datagram') for mode in ('race','debug')}
    identity={'vui_commit':'1'*40,'vui_tree':'2'*40,'files':{n:'a'*64 for n in a.PATCHES}}
    result={'schema_version':1,'kind':a.KIND,'source_identity':identity,'fixed_sources':rt.p.FIXED,
            'product_qualified':False,'patches':[{'path':n,'sha256':'a'*64} for n in a.PATCHES],
            'same_head_base_validated':True,'final_sources_equal_base':False,'final_shared_server_writer_unchanged':True,
            'version':'1.14.2-vui-socks-owner-experiment-'+identity['vui_commit'][:12],
            'base_version':'1.14.2-vui-complete-packet-experiment-'+identity['vui_commit'][:12],
            'binary_sha256':'b'*64,'base_binary_sha256':'c'*64,'module_graph_sha256':'d'*64,
            'build':{'go_version':'go1.25.14','goos':'linux','goarch':'amd64','tags':[],'cgo_enabled':'0','gotoolchain':'local','gowork':'off','trimpath':True,'buildvcs':False,'go_binary_sha256':'e'*64},
            'regression_checks':[{'label':n,'returncode':0} for n in sorted(names)]}
    return result,identity

def passive():
    data,case,result,roles,source,life=old_fixture.fixture()
    result['packets'][1]['full_delivery_passed']=True
    rows=deepcopy(data['samples'][0]['device']['sockets'])
    for sample in data['samples']:
        if sample['phase']=='failure':sample['phase']='after_recover'
        if sample['phase']=='after_local_cleanup':continue
        sample['associations'][0]['peek'].update(state='would_block',count=-1,errno=errno.EAGAIN)
        device=sample['device'];device['sockets']=deepcopy(rows)
        if sample['phase'] in ('after_large_business','after_recover'):
            inode=2001 if sample['phase']=='after_large_business' else 2002
            device['sockets'].append({'fd':20,'inode':inode,'protocol':'tcp','family':'ipv4','proc_state':'01','psutil_state':'ESTABLISHED',
                'local':{'host':'127.0.0.1','port':45000},'remote':{'host':'127.0.0.1','port':1081}})
        fds=[{'fd':x['fd'],'inode':x['inode']} for x in device['sockets']]
        device['fd_before']=fds;device['fd_after']=deepcopy(fds)
    life['events'].append({'kind':'large_endpoint_proof','proof':{'ingress_port':1081}})
    return data,case,result,roles,source,life

class OwnerRuntimeTests(unittest.TestCase):
    def test_01_valid_two_generations(self):self.assertTrue(verify(events())['final_closed_joined'])
    def test_02_out_of_order_callbacks(self):self.assertEqual(verify(list(reversed(events())))['generations'],2)
    def test_03_missing_join(self):
        rows=events();rows.pop(4)
        with self.assertRaises(ValueError):verify(rows)
    def test_04_duplicate_sequence(self):
        rows=events();rows[3]['sequence']=2
        with self.assertRaises(ValueError):verify(rows)
    def test_05_wrong_pid(self):
        with self.assertRaises(ValueError):v.parse_events(text(events()),124)
    def test_06_json_duplicate(self):
        line=text(events()[:1]).replace('"pid": 123','"pid":123,"pid":123')
        with self.assertRaises(ValueError):v.parse_events(line,123)
    def test_07_truncated_and_unknown_event(self):
        with self.assertRaises(ValueError):v.parse_events(text(events()).rstrip('\n'),123)
        rows=events();rows[0]['extra']=1
        with self.assertRaises(ValueError):v.parse_events(text(rows),123)
    def test_08_wrong_transition_or_cause(self):
        for key in ('transition','cause'):
            rows=events();rows[0][key]='invented'
            with self.assertRaises(ValueError):v.parse_events(text(rows),123)
    def test_09_invalid_counter(self):
        for value in (True,-1,2**64):
            rows=events();rows[0]['accepted_packets']=value
            with self.assertRaises(ValueError):v.parse_events(text(rows),123)
    def test_10_accepted_replay(self):
        rows=events();rows[-1]['accepted_packets']=3
        with self.assertRaises(ValueError):verify(rows)
    def test_11_source_or_target_rebind(self):
        for key in ('source','relay','target'):
            rows=events();rows[4][key]='127.0.0.1:9999'
            with self.assertRaises(ValueError):verify(rows)
    def test_12_no_final_join(self):
        rows=events();rows[-1]['transition']='closing'
        with self.assertRaises(ValueError):verify(rows)
    def test_13_error_end_not_recovery(self):
        rows=events();rows[3]['cause']='io_error'
        with self.assertRaises(ValueError):verify(rows)
    def test_14_optional_second_idle(self):
        rows=events();extra=deepcopy(rows[3:6])
        for x in extra:x.update(generation=2,accepted_packets=2,packet_sequence=2)
        rows[-2:-2]=extra
        for index,row in enumerate(rows,1):row['sequence']=index
        self.assertEqual(verify(rows)['generations'],2)
    def test_15_C_single_owner(self):self.assertEqual(verify(events(1,4),generations=1,packets=4)['new_application_packets'],4)
    def test_16_log_roles_and_duplicate_snapshot(self):
        value=''.join(name+':\nbody\n' for name in v.LOG_FILES.values())
        self.assertEqual(v.role_texts(value+value)['device-singbox'],'body\n')
        with self.assertRaises(ValueError):v.role_texts(value.replace('003-device','004-device'))
    def test_17_disabled_role_event(self):
        case=rt.p.plan('C')[0];roles=[{'role':k,'launches':[{'pid':123}]} for k in case['roles']]
        value=''.join(name+':\n'+(text(events()) if role=='domestic' else '') for role,name in v.LOG_FILES.items())
        with self.assertRaises(ValueError):v.verify_owner_case(case,{},roles,value,'off')
    def test_18_off_matrix_exact_configs(self):
        for stage in a.ARMS['off']:
            case=rt.p.plan(stage)[0]
            for role in case['roles']:
                before=config();after,flags=a.configure_role(before,case,role,'off')
                self.assertEqual(after,before);self.assertIs(flags['owner_recovery_enabled'],False)
    def test_19_on_device_only(self):
        for stage in a.ARMS['on']:
            case=rt.p.plan(stage)[0]
            for role in case['roles']:
                after,flags=a.configure_role(config(),case,role,'on')
                self.assertIs(flags['owner_recovery_enabled'],role=='device-singbox')
                self.assertEqual(a.OPTION in after['inbounds'][0],role=='device-singbox')
    def test_20_forbid_stock_and_preexisting(self):
        with self.assertRaises(ValueError):a.configure_role(config(),rt.p.plan('A')[0],'device-singbox','on')
        cfg=config();cfg['inbounds'][0][a.OPTION]={'outbound':a.TAG}
        with self.assertRaises(ValueError):a.configure_role(cfg,rt.p.plan('C')[0],'domestic','off')
    def test_21_selected_tag_required(self):
        cfg=config();cfg['route']['final']='DIRECT'
        with self.assertRaises(ValueError):a.configure_role(cfg,rt.p.plan('C')[0],'device-singbox','on')
    def test_22_eight_patch_provenance(self):
        data,identity=provenance();self.assertEqual(a.verify_provenance(rt,data,identity),data)
        data['patches']=data['patches'][:5]
        with self.assertRaises(ValueError):a.verify_provenance(rt,data,identity)
    def test_23_graph_and_compiler_and_equivalence(self):
        for key,value in (('module_graph_sha256',None),('same_mux_route_sources',True),('final_sources_equal_base',True)):
            data,identity=provenance();data[key]=value
            with self.assertRaises(ValueError):a.verify_provenance(rt,data,identity)
        data,identity=provenance();data['build']['go_version']='go1.25.13'
        with self.assertRaises(ValueError):a.verify_provenance(rt,data,identity)
    def test_24_all_23_regression_checks(self):
        data,identity=provenance();self.assertEqual(len(data['regression_checks']),23);data['regression_checks'].pop()
        with self.assertRaises(ValueError):a.verify_provenance(rt,data,identity)
    def test_25_build_evidence_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);(path/'actual').write_text('x');(path/'linked').symlink_to(path/'actual')
            with self.assertRaises(ValueError):a.evidence_files(path)
    def test_26_same_owner_existing_samples(self):
        result=v.verify_same_owner_samples(rt,*passive());self.assertTrue(result['business_recovery_passed']);self.assertFalse(result['device_whole_window_continuity_proven'])
    def test_27_eof_and_data_reject(self):
        for state,count in (('eof',0),('data',1)):
            args=list(passive());args[0]['samples'][2]['associations'][0]['peek'].update(state=state,count=count,errno=None)
            with self.assertRaises(ValueError):v.verify_same_owner_samples(rt,*args)
    def test_28_stable_owner_absence_reject(self):
        args=list(passive());device=args[0]['samples'][2]['device'];device.update(sockets=[],fd_before=[],fd_after=[])
        with self.assertRaises(ValueError):v.verify_same_owner_samples(rt,*args)
    def test_29_private_transport_missing_reject(self):
        args=list(passive());sample=next(s for s in args[0]['samples'] if s['phase']=='after_recover');sample['device']['sockets'].pop()
        for k in ('fd_before','fd_after'):sample['device'][k].pop()
        with self.assertRaises(ValueError):v.verify_same_owner_samples(rt,*args)
    def test_30_finite_summary_no_promotion(self):
        rows=summary_rows()
        report=r.summarize(rows,{})
        self.assertTrue(report['finite_experiment_contract_passed']);self.assertFalse(report['original_suite_passed']);self.assertFalse(report['product_qualified'])
        rows['on']=rows['on'][:1];self.assertFalse(r.summarize(rows,{})['finite_experiment_contract_passed'])

class AdditionalBoundaries(unittest.TestCase):
    def role_fixture(self, stage='C', arm='on'):
        case=rt.p.plan(stage)[0]
        binaries={origin:{'path':'/'+origin,'sha256':('a' if origin=='candidate' else 'b')*64,
                    'version':'1.14.2-vui-socks-owner-experiment-'+'1'*12 if origin=='candidate' else '1.14.2'}
                    for origin in ('candidate','official')}
        rows=[]
        for role,origin in case['roles'].items():
            _,flags=a.configure_role(config(),case,role,arm)
            rows.append({'case':case['id'],'role':role,'origin':origin,'binary':binaries[origin]['path'],
                'sha256':binaries[origin]['sha256'],'version':binaries[origin]['version'],'config_sha256':'c'*64,
                'log_file':v.LOG_FILES[role],**flags,
                'launches':[{'pid':123,'ready':True,'birth_time':1,'sha256':binaries[origin]['sha256']}]})
        return case,rows,binaries,arm
    def test_40_fixture_hash_and_log_binding(self):
        fresh=a.install(a.load_runtime(REPO),'on');case,_,binaries,_=self.role_fixture()
        class Base:
            singbox='/unused';label=case['id']
            def process(self,label,config,**kwargs):
                self.config=config
                return SimpleNamespace(command=[self.singbox],log_path=Path(v.LOG_FILES[label]),start=lambda:None,process=None)
        with tempfile.TemporaryDirectory() as tmp:
            records=[];fixture=fresh.p.role_fixture(None,SimpleNamespace(RestrictedUdpFixture=Base),Path(tmp),binaries,case,records)()
            fixture.process('device-singbox',config());row=records[0]
            self.assertEqual(row['log_file'],'003-device-singbox.log');self.assertTrue(row['owner_recovery_enabled'])
            self.assertEqual(fixture.config['inbounds'][0][a.OPTION],{'outbound':a.TAG})
            self.assertEqual(row['config_sha256'],a.hashlib.sha256(json.dumps(fixture.config,sort_keys=True,separators=(',',':')).encode()).hexdigest())
    def test_41_original_checker_failure_cannot_hide_behind_off_RED(self):
        for stage in ('A','V','D'):
            arms=summary_rows();row=next(x for x in arms['off'] if x['stage']==stage)
            row['failure_observation_error']={'type':'ValueError','message':'rejected raw proof'}
            self.assertFalse(r.summarize(arms,{})['finite_experiment_contract_passed'])
        for stage in ('A','U','V'):
            arms=summary_rows();row=next(x for x in arms['off'] if x['stage']==stage)
            row['failure_observations']['failed_observations_verified']=False
            self.assertFalse(r.summarize(arms,{})['finite_experiment_contract_passed'])
    def test_42_off_original_gate_map_and_checker_presence(self):
        for stage in a.ARMS['off']:
            arms=summary_rows();row=next(x for x in arms['off'] if x['stage']==stage)
            row['stage_gate_passed']=not row['stage_gate_passed']
            self.assertFalse(r.summarize(arms,{})['finite_experiment_contract_passed'])
        arms=summary_rows();arms['off'][2]['failure_observations']={'applicable':False,'failed_observations_verified':False}
        self.assertFalse(r.summarize(arms,{})['finite_experiment_contract_passed'])
        arms=summary_rows();arms['off'][0].pop('failure_observations')
        self.assertFalse(r.summarize(arms,{})['finite_experiment_contract_passed'])
        arms=summary_rows();arms['off'][1]['socks_association_observations']['observations_verified']=False
        self.assertFalse(r.summarize(arms,{})['finite_experiment_contract_passed'])
    def test_31_explicit_role_mode_required(self):
        args=self.role_fixture();self.assertTrue(v.verify_roles(*args));args[1][0].pop('owner_recovery_enabled')
        with self.assertRaises(ValueError):v.verify_roles(*args)
    def test_32_role_config_source_identity(self):
        for key,value in (('owner_recovery_outbound','DIRECT'),('sha256','d'*64),('config_sha256',''),('version','stock')):
            args=self.role_fixture();args[1][-1][key]=value
            with self.assertRaises(ValueError):v.verify_roles(*args)
    def test_33_wrong_provenance_kind(self):
        data,identity=provenance();data['kind']='vui-complete-packet-source-experiment'
        with self.assertRaises(ValueError):a.verify_provenance(rt,data,identity)
    def test_34_private_transport_race(self):
        args=list(passive());sample=next(s for s in args[0]['samples'] if s['phase']=='after_recover')
        sample['device']['races']=[{'kind':'stable_proc_socket_omitted_by_psutil'}];args[0]['status']='complete_with_inconclusive_samples'
        with self.assertRaises(ValueError):v.verify_same_owner_samples(rt,*args)
    def test_39_sample_role_is_bound_to_case_not_first_stage_PID(self):
        args=list(passive()); unrelated=deepcopy(args[3][0]);unrelated['case']='unrelated';unrelated['launches'][0]['pid']=999
        args[3].insert(0,unrelated);self.assertTrue(v.verify_same_owner_samples(rt,*args)['business_recovery_passed'])
    def test_35_intermediate_race_remains_inconclusive(self):
        args=list(passive());sample=args[0]['samples'][2];sample['device']['races']=[{'kind':'stable_proc_socket_omitted_by_psutil'}]
        args[0]['status']='complete_with_inconclusive_samples';result=v.verify_same_owner_samples(rt,*args)
        self.assertEqual(result['inconclusive_device_samples'],1);self.assertFalse(result['device_whole_window_continuity_proven'])
    def fake_arm(self, unsafe=None, business_bad=None):
        fresh=a.install(a.load_runtime(REPO),'on'); seen=[]
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp);manifest=output/'input.json';case,roles,binaries,_=self.role_fixture()
            fresh.p.save(manifest,{'arm':'on',**binaries})
            def runner(command):
                self.assertEqual(Path(command[1]).name,'runtime_adapter.py');self.assertEqual(command[-2:],['--arm','on'])
                stage=command[command.index('--stage')+1];seen.append(stage);directory=output/stage;directory.mkdir()
                report={'stage':stage,'diagnostic_complete':stage!=business_bad,'stage_capacity_passed':stage!=business_bad,
                        'original_suite_passed':False,'product_qualified':False,'capacity_failed_leaves':[],'cases':[],'errors':[]}
                fresh.p.save(directory/'result.json',report);fresh.p.save(directory/'process-binaries.json',[])
                return {'returncode':0,'timeout':False,'cleanup':{'signals':[],'leader_joined':True,'process_group_gone':True}}
            def safety(directory,stage,*unused):
                if stage==unsafe:raise ValueError('unknown cleanup')
                return {'stage_execution_safe':True}
            def nofail(*unused):return {'applicable':False,'failed_observations_verified':False}
            def socks(*unused):return {'observations_verified':True}
            with patch.object(v,'verify_roles',return_value=True),patch('builtins.print'):
                rows=r.run_arm(fresh,output,manifest,'on',runner=runner,safety_checker=safety,failure_checker=nofail,
                        socks_checker=socks,additional_checker=lambda *unused:{'verified':True})
        return seen,rows
    def test_36_only_C_D_finite_loop(self):
        seen,rows=self.fake_arm();self.assertEqual(seen,['C','D']);self.assertTrue(all(x['stage_execution_safe'] for x in rows))
    def test_37_safe_business_failure_continues(self):
        seen,rows=self.fake_arm(business_bad='C');self.assertEqual(seen,['C','D']);self.assertFalse(rows[0]['stage_gate_passed'])
    def test_38_unsafe_C_stops_D(self):
        seen,rows=self.fake_arm(unsafe='C');self.assertEqual(seen,['C']);self.assertFalse(rows[0]['stage_execution_safe'])

if __name__=='__main__':unittest.main()
