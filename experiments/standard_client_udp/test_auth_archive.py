"""Synthetic authentication archive controls, never a real-core claim."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import prepare_controls as a
import runtime as r

class AuthArchiveTests(unittest.TestCase):
    def fixture(self):
        source={'commit':'a'*40,'tree':'b'*40,'files':{'synthetic.py':'c'*64}}
        identity={'product':source,'legacy':source}
        known={'official':{'sha256':r.STOCK,'version':'1.14.2'},'mihomo':{'sha256':r.MIHOMO,'version':'1.19.32'}}
        value={'input-identity.json':{'source_identity':identity,'binaries':known,'arm':'stock'},
            'post-identity.json':{'source_identity':identity,'binary_sha256':{k:v['sha256'] for k,v in known.items()}},
            'owned-group.json':{'timeout_seconds':300,'timeout':False,'signals':[],'returncode':0,'leader_joined':True,'process_group_gone':True,'pid':99,'process_group':99},
            'RESULT.json':{'tests_run':3,'methods':[{'method':m,'status':'passed'} for m in a.METHODS],
                'arm':'stock','product_qualified':False,'udp_capacity_qualified':False,'passed_methods':3,'nonpass':[],'failures':[],'passed':True},
            'cleanup.json':[],'process-binaries.json':[],'suite.log':'synthetic\n','group.stdout':'synthetic\n','group.stderr':''}
        pid=100
        ident=lambda body:{'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}
        for label in a.LABELS:
            tcp=label==a.METHODS[2];upstream=label.startswith(a.METHODS[0]);fault=label.rsplit('-',1)[-1]
            names=['overseas','domestic','device-mihomo']+([] if tcp else ['domestic-reconfigured','device-reconfigured-mihomo']*2)
            processes=[]
            for name in names:
                origin='mihomo' if name.startswith('device') else 'official';launches=[];stops=[]
                for _ in range(2 if fault=='stopped' and name=='overseas' else 1):
                    pid+=1;launches.append({'pid':pid,'birth_time':float(pid),'ready':True});stops.append({'pid':pid,'returncode':0,'pid_absent':True})
                value['process-binaries.json'].append({'case':label,'role':name,'mihomo':origin=='mihomo','origin':origin,**known[origin],
                    'owner_option_absent':True,'owner_recovery_enabled':False,'parser_passed':True,'launches':launches,'stops':stops})
                processes.append(stops[-1])
            target={'closed':True,'joined':True,'socket_closed':True,'host':'127.0.0.3','port':3000,'errors':[],'received':[],'expected':[]}
            cleanup={'case':label,'processes':processes,'targets':[target],'fixture_removed':True,'new_threads':0,'rejections':[],
                     'dns':{'business':[],'ordinary':[]}}
            if tcp:
                observations=[{'kind':'tcp-https-double-hop','client':'mihomo','verified_tls':True,'status':200,
                    'body_sha256':ident(b'VUI-VERIFIED-DOUBLE-HOP')['sha256'],'deliveries':[['127.0.0.4','127.0.0.3','/experimental-tcp-mihomo']]}]
            else:
                payloads=[b'negative-target-reachable' if upstream else b'device-negative-target-reachable',
                    b'before-fault-positive' if upstream else b'before-device-fault',b'authorized-recovery' if upstream else b'device-authorized-recovery']
                target['received']=[{'peer':peer,**ident(body)} for peer,body in zip(('127.0.0.1','127.0.0.4','127.0.0.4'),payloads)]
                target['expected']=[{**x,'count':1} for x in target['received']]
                reasons={'uuid':['unknown uuid'],'ca':['x509','unknown authority'],'sni':['x509','not wrong.example.test'],'stopped':['connect: connection refused']}[fault]
                actual=' '.join(reasons)
                observations=[{'kind':'echo','bytes':len(payloads[1]),'host':'127.0.0.3','peer':'127.0.0.4'},
                    {'kind':'rejection','reason':reasons,'actual':actual},
                    {'kind':'echo','bytes':len(payloads[2]),'host':'127.0.0.3','peer':'127.0.0.4'}]
                rejected=(b'must-not-deliver-' if upstream else b'bad-device-')+fault.encode()
                cleanup['rejections']=[{'payload':ident(rejected),'reasons':reasons,'started':1,'completed':3,'window':2,'fresh_log':actual}]
            value['cleanup.json'].append(cleanup)
            value['cases/'+label+'.json']={'status':'passed','observations':observations,'deliveries':[{'host':'127.0.0.3','count':len(target['received'])}]}
            value['cases/'+label+'.log']='synthetic\n'
        return source,value

    def write(self,path,value):
        for name,data in value.items():
            dest=path/name;dest.parent.mkdir(parents=True,exist_ok=True)
            if isinstance(data,str):dest.write_text(data)
            else:r.save(dest,data)

    def test_complete_positive_and_mutated_negative_proof_refused(self):
        mutations=[
            ('reason',lambda v:v['cases/'+a.LABELS[0]+'.json']['observations'][1].update(actual='timeout')),
            ('fresh-window',lambda v:v['cleanup.json'][0]['rejections'][0].update(completed=2.9)),
            ('negative-delivery',lambda v:v['cleanup.json'][0]['targets'][0]['received'].append({'peer':'127.0.0.4','bytes':2,'sha256':'0'*64})),
            ('direct-source',lambda v:v['cleanup.json'][0]['targets'][0]['received'][1].update(peer='127.0.0.1')),
            ('fixed-hash',lambda v:v['input-identity.json']['binaries']['official'].update(sha256='0'*64)),
            ('tcp-body',lambda v:v['cases/'+a.METHODS[2]+'.json']['observations'][0].update(body_sha256='0'*64)),
            ('group-live',lambda v:v['owned-group.json'].update(process_group_gone=False)),
            ('launch-extra',lambda v:v['process-binaries.json'][0]['launches'].append({'pid':999,'ready':True,'birth_time':999})),
            ('method-failed',lambda v:v['RESULT.json']['methods'][0].update(status='failed'))]
        source,original=self.fixture()
        for name,mutate in [('positive',None),*mutations]:
            with self.subTest(mutation=name),tempfile.TemporaryDirectory() as tmp,patch.object(r,'source',return_value=source):
                value=deepcopy(original)
                if mutate:mutate(value)
                self.write(Path(tmp),value)
                if mutate:
                    with self.assertRaises(ValueError):a.verify(Path(tmp),Path(os.environ['VUI_STOCK_UDP_LEGACY']).resolve(),'stock')
                else:self.assertTrue(a.verify(Path(tmp),Path(os.environ['VUI_STOCK_UDP_LEGACY']).resolve(),'stock')[1]['passed'])

if __name__=='__main__':unittest.main()
