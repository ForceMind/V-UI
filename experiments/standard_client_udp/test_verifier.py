"""Synthetic verifier controls. These do not claim runtime packet evidence."""
from copy import deepcopy
import os
from pathlib import Path
import socket
import tempfile
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import runtime as r

class VerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=r.load('packet_boundary',Path(os.environ['VUI_STOCK_UDP_LEGACY'])/'experiments/xudp_packet/packet_boundary.py')

    def fixture(self, oversize=False, index=1, case_override=None):
        p=self.p; case=case_override or r.plan(p,'stock','C')[-1 if oversize else index]
        known={'official':{'path':'/inert/sing-box','sha256':r.STOCK,'version':'1.14.2'},
               'mihomo':{'path':'/inert/mihomo','sha256':r.MIHOMO,'version':'1.19.32'}}
        roles=[]
        for i,(role,origin) in enumerate(case['roles'].items(),100):
            roles.append({'role':role,'origin':origin,'binary':known[origin]['path'],**{k:known[origin][k] for k in ('sha256','version')},
                'classification':'official','parser_passed':True,'owner_option_absent':True,'owner_recovery_enabled':False,
                'launches':[{'pid':i,'birth_time':float(i),'ready':True,'sha256':known[origin]['sha256']}]})
        app=['127.0.0.1',2000]; relay=['127.0.0.1',2001]; target=['127.0.0.3',2002]; source=['127.0.0.4',2003]
        row={'packets':[],'unexecuted_leaves':[],'errors':[],'recovery_attempted':True,
            'process_identity':[{'pid':i,'birth_time':float(i)} for i in range(100,103)],
            'cleanup':{'execution_safe':True,'threads_joined':True,'fixture_removed':True,'all_target_workers_joined':True,
                       'application_sockets_closed':True,'data_audit_errors':[],
                       'application_close_proofs':[{'id':0,'control_fd':-1,'application_fd':-1}],
                       'processes':[{'pid':i,'returncode':0,'reaped':True} for i in range(100,103)]},
            'application_associations':[{'id':0,'control':{'fd':8,'inode':88,'local':['127.0.0.1',2004],'peer':['127.0.0.1',2005]},
                'application':{'fd':9,'inode':99,'local':app,'peer':None},'relay':relay}],
            'frozen_target_ledgers':[{'closed':True,'thread_joined':True,'socket_closed':True,'received':[],'expected':[],'errors':[]}],
            'dns':{'ordinary':[],'business':[],'expected':[]},
            'target_ledger':{'host':target[0],'port':target[1],'closed':True,'thread_joined':True,'socket_closed':True,'errors':[],
                             'packets':[],'actual_datagrams':[]}}
        sent=0
        wire=lambda body: b'\0\0\0\x01'+socket.inet_aton(target[0])+target[1].to_bytes(2,'big')+body
        for spec in case['packets']:
            leaf=case['id']+'/'+spec['id']; req,reply=p.packet_bodies(leaf,spec)
            packet={'leaf':leaf,'direction':spec['direction'],'first_udp':sent==0,'request':p.ident(req),'expected_reply':p.ident(reply),
                'application_socket':app,'socks_relay':relay,'requested_target':target,'association_id':0,
                'send_calls':[{'wire':p.ident(wire(req)),'relay':relay,'started':1,'completed':2}]}
            if spec['direction']=='local_emsgsize':
                packet.update(status='local_emsgsize_verified',full_delivery_passed=False,errno=90,local_zero_delivery=True,socks_wire_bytes=65508)
                packet['send_calls'][0]['errno']=90
            else:
                sent+=1
                item={'leaf':leaf,'target':target,'source':source,'request_expected':p.ident(req),'request_actual':p.ident(req),
                    'request_full':True,'request_exact_prefix':True,'reply_sent':p.ident(reply),'sendto_return':len(reply)}
                row['target_ledger']['packets'].append(item)
                row['target_ledger']['actual_datagrams'].append({'target':target,'source':source,'payload':p.ident(req),'flags':0,'ancillary_count':0})
                packet.update(target=item,status='full_delivery_passed',send_state='sent',full_delivery_passed=True,
                    actual_reply=p.ident(reply),reply_source=relay,reply_target=target,reply_exact_prefix=True,socks_reply_wire=p.ident(wire(reply)),
                    decoded_reply_before_assertion={'host':target[0],'port':target[1],'payload':p.ident(reply)},
                    reply_calls=[{'wire':p.ident(wire(reply)),'relay':relay,'flags':0,'ancillary_count':0,'completed':3}])
                packet['send_calls'][0]['return']=len(wire(req))
            row['packets'].append(packet)
        row['target_ledger'].update(planned_count=sent,received_count=sent)
        return case,row,roles,known

    def check(self, items): return r.verify_case(self.p,*items,Path('/inert'),{})

    def test_complete_synthetic_positive(self):
        self.assertTrue(self.check(self.fixture())['case_gate_passed'])
        self.assertTrue(self.check(self.fixture(True))['case_gate_passed'])

    def test_mutated_leaf_tuple_send_reply_roles_cleanup_refused(self):
        # Every mutation starts from a passing full two-leaf synthetic record.
        mutations=[
            ('leaf-order',lambda c,v,rs,k:v['packets'].reverse()),
            ('leaf-duplicate',lambda c,v,rs,k:v['packets'].append(deepcopy(v['packets'][0]))),
            ('unexecuted',lambda c,v,rs,k:v['unexecuted_leaves'].append('fake')),
            ('app-tuple',lambda c,v,rs,k:v['packets'][1].update(application_socket=['127.0.0.1',5000])),
            ('relay-tuple',lambda c,v,rs,k:v['packets'][1].update(socks_relay=['127.0.0.1',5000])),
            ('bound-target',lambda c,v,rs,k:v['target_ledger'].update(port=5000)),
            ('direction',lambda c,v,rs,k:v['packets'][0].update(direction='downlink')),
            ('first',lambda c,v,rs,k:v['packets'][1].update(first_udp=True)),
            ('request',lambda c,v,rs,k:v['packets'][0]['request'].update(sha256='0'*64)),
            ('target-flags',lambda c,v,rs,k:v['target_ledger']['actual_datagrams'][0].update(flags=1)),
            ('target-ancillary',lambda c,v,rs,k:v['target_ledger']['actual_datagrams'][0].update(ancillary_count=1)),
            ('reply-shortwrite',lambda c,v,rs,k:v['target_ledger']['packets'][0].update(sendto_return=0)),
            ('send-zero',lambda c,v,rs,k:v['packets'][0].update(send_calls=[])),
            ('send-double',lambda c,v,rs,k:v['packets'][0]['send_calls'].append(deepcopy(v['packets'][0]['send_calls'][0]))),
            ('send-short',lambda c,v,rs,k:v['packets'][0]['send_calls'][0].update(returned=0, **{'return':0})),
            ('send-errno',lambda c,v,rs,k:v['packets'][0]['send_calls'][0].update(errno=90)),
            ('reply-missing',lambda c,v,rs,k:v['packets'][0].pop('reply_calls')),
            ('decoded-missing',lambda c,v,rs,k:v['packets'][0].pop('decoded_reply_before_assertion')),
            ('reply-double',lambda c,v,rs,k:v['packets'][0]['reply_calls'].append(deepcopy(v['packets'][0]['reply_calls'][0]))),
            ('reply-hash',lambda c,v,rs,k:v['packets'][0]['reply_calls'][0]['wire'].update(sha256='0'*64)),
            ('decoded-port',lambda c,v,rs,k:v['packets'][0]['decoded_reply_before_assertion'].update(port=3000)),
            ('owner-on',lambda c,v,rs,k:rs[0].update(owner_recovery_enabled=True)),
            ('role-origin',lambda c,v,rs,k:rs[-1].update(origin='candidate')),
            ('role-missing',lambda c,v,rs,k:rs.pop()),
            ('role-relaunch',lambda c,v,rs,k:rs[0]['launches'].append(deepcopy(rs[0]['launches'][0]))),
            ('birth',lambda c,v,rs,k:v['process_identity'][0].update(birth_time=300)),
            ('fd-proof-missing',lambda c,v,rs,k:v['cleanup'].pop('application_close_proofs')),
            ('fd-proof-duplicate',lambda c,v,rs,k:v['cleanup']['application_close_proofs'].append(deepcopy(v['cleanup']['application_close_proofs'][0]))),
            ('fd-open',lambda c,v,rs,k:v['cleanup']['application_close_proofs'][0].update(control_fd=8)),
            ('association-replaced',lambda c,v,rs,k:v['packets'][1].update(association_id=1)),
            ('target-live',lambda c,v,rs,k:v['target_ledger'].update(thread_joined=False)),
            ('core-crash',lambda c,v,rs,k:v['cleanup']['processes'][0].update(returncode=1)),
        ]
        for name,mutate in mutations:
            with self.subTest(mutation=name):
                items=deepcopy(self.fixture()); self.assertTrue(self.check(items)['case_gate_passed'])
                mutate(*items)
                with self.assertRaises((ValueError,KeyError)): self.check(items)

    def test_oversize_mutations_refused(self):
        for field,value in [('errno',1),('socks_wire_bytes',65507),('local_zero_delivery',False)]:
            with self.subTest(field=field):
                items=self.fixture(True);items[1]['packets'][0][field]=value
                with self.assertRaises(ValueError):self.check(items)

    def test_failed_d_prerequisite_cannot_claim_window_or_recovery(self):
        case=r.plan(self.p,'stock','D')[0]
        items=self.fixture(case_override=case);row=items[1]
        row['packets']=row['packets'][:1]
        row['packets'][0].update(status='error',full_delivery_passed=False,error={'type':'ValueError','message':'prerequisite failed'})
        row['unexecuted_leaves']=[case['id']+'/recover'];row['errors']=[{'type':'ValueError','message':'prerequisite failed'}]
        row.update(recovery_attempted=False,lifecycle_window_executed=False)
        ledger=row['target_ledger'];ledger['packets']=ledger['packets'][:1];ledger['actual_datagrams']=ledger['actual_datagrams'][:1]
        ledger.update(planned_count=1,received_count=1)
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)
            checked=r.verify_case(self.p,*items,output,{})
            self.assertTrue(checked['execution_safe']);self.assertFalse(checked['case_gate_passed'])
            for field in ('recovery_attempted','lifecycle_window_executed'):
                changed=deepcopy(items);changed[1][field]=True
                with self.subTest(field=field),self.assertRaises(ValueError):r.verify_case(self.p,*changed,output,{})
            (output/case['id']).mkdir()
            r.save(output/case['id']/'lifecycle-observation.json',{'events':[{'phase':'large_then_close'}]})
            changed=deepcopy(items);changed[1]['lifecycle_window_executed']=True
            with self.assertRaisesRegex(ValueError,'60000'):r.verify_case(self.p,*changed,output,{})

    def test_capacity_failure_remains_safe_and_red(self):
        items=self.fixture();row=items[1]
        row['errors']=[{'type':'ValueError','message':'strict failure'}]
        row['packets'][0].update(status='error',full_delivery_passed=False,error={'type':'ValueError','message':'strict failure'})
        checked=self.check(items)
        self.assertTrue(checked['execution_safe']);self.assertFalse(checked['case_gate_passed'])

if __name__=='__main__':unittest.main()
