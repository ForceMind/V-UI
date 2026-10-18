"""Finite reduced-domestic guard matrix. No runtime or binary is started here."""
from pathlib import Path
import hashlib
import sys
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT/'experiments/domestic_udp_reduction'))
import contract as build
import adapter
import evidence as safe_archive
r=build.r
need=r.need
KIND='vui-domestic-reduced-guards'
TIMEOUT=180
IDS=tuple('upstream-'+x for x in ('uuid','ca','sni','stopped'))+tuple('device-'+x for x in ('uuid','ca','sni'))+('tcp','dns-all','dns-partial','naked-retarget','guarded-retarget','same-port-rebind','literal-ports')
LITERALS=('127.0.0.7','127.0.0.1','127.0.0.2','100.100.100.200','169.254.169.254','10.0.0.1','::1','::ffff:127.0.0.7','::ffff:127.0.0.1','::ffff:169.254.169.254','::ffff:100.100.100.200','64:ff9b::7f00:3','2001:db8::1')
ANSWERS=(('private',('127.0.0.7',)),('mixed',('127.0.0.3','127.0.0.7')),('reverse',('127.0.0.7','127.0.0.3')),('ipv6-sibling',('127.0.0.3','::1')),('public-ipv6-sibling',('127.0.0.3','2001:4860:4860::8888')),('self',('127.0.0.1',)),('upstream',('127.0.0.2',)))
ROLES={'overseas':'official','domestic':'candidate','domestic-reconfigured':'candidate','device-mihomo':'mihomo','device-reconfigured-mihomo':'mihomo'}
def ident(data):return {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
def payload(step):return bytes(range(256))*4+bytes(176) if step=='ordinary' else ('guard-'+step).encode()
def plan(case):
    need(case in IDS,'Unknown guard fixture')
    if case.startswith(('upstream-','device-')):return [('direct-target','direct'),('ordinary','echo'),('before','echo'),('fault','fault'),('negative','reject'),('restore','restore'),('recovery','echo')]
    direct={'tcp':(), 'dns-all':('target','denied'),'dns-partial':('target',),'naked-retarget':('denied',),'guarded-retarget':('denied','ingress','upstream','other'),'same-port-rebind':('target','denied'),'literal-ports':('target','denied','ingress','upstream')}[case]
    rows=[('direct-'+x,'direct') for x in direct]+[('ordinary','echo')]
    tail={
      'tcp':[('https','https')],
      'dns-all':[('literal-before','echo'),('good','raw-echo')]+[('dns-'+x,'raw-reject') for x,_ in ANSWERS]+[('literal-private','reject')],
      'dns-partial':[('literal-before','echo'),('partial','raw-echo'),('both-failed','raw-reject')],
      'naked-retarget':[('naked-restart','naked-restart'),('first','raw-echo'),('retarget','raw-echo')],
      'guarded-retarget':[('first','raw-echo')]+[('change-'+str(i),'raw-echo') for i in range(9)],
      'same-port-rebind':[('first','raw-echo'),('same','raw-echo'),('new','raw-reject')],
      'literal-ports':[('high','echo')]+[('port-'+str(x),'reject') for x in (53,443,1023)]+[('literal-'+str(i),'raw-reject' if ':' in x else 'reject') for i,x in enumerate(LITERALS)],
    }[case]
    return rows+tail

def scope():return {'kind':KIND,'source_methods':9,'fixture_children':14,'maximum_application_payload':1200,'product_qualified':False,'mobile_tun_qualified':False,'idle_qualified':False}
def reasons(case,step):
    if step=='negative':return {'uuid':['unknown uuid'],'ca':['x509','unknown authority'],'sni':['x509','not wrong.example.test'],'stopped':['connect: connection refused']}[case.rsplit('-',1)[1]]
    if step=='both-failed':return ['dns: lookup failed for both-failed.example.test','servfail']
    return ['reject']
def guard_index(step):return 2 if step.startswith('dns-') or step=='new' else 5 if step.startswith('port-') else 0

def expected_dns(case):
    if case=='dns-all':return [('good.example.test',('127.0.0.3',),())]+[(label+'.example.test',values,()) for label,values in ANSWERS]
    if case=='dns-partial':return [('failed-aaaa.example.test',('127.0.0.3',),(28,)),('both-failed.example.test',(),(1,28))]
    if case=='same-port-rebind':return [('same-port-rebind.example.test',('127.0.0.3',),()),('same-port-rebind.example.test',('127.0.0.7',),())]
    return []

def run_binding():
    import os
    return {k:os.environ.get(k,'local') for k in ('GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_SHA','GITHUB_WORKFLOW')}

def files():
    result={'input-identity.json':'json','post-identity.json':'json','matrix.json':'json'}
    for case in IDS:
        for name in ('result.json','process-binaries.json','export-identity.json','fixture.log','owned-group.json','stdout.log','stderr.log'):
            result[case+'/'+name]='json' if name.endswith('.json') else 'text'
    result.update({'build/'+name:fmt for name,fmt in build.allowed_files().items()})
    return result
