"""Synthetic complete reduced I archive factory, used only in Python controls."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import adapter as a
c=a.c;r=c.r;e=a.e

def runtime(root,legacy,build,identity):
    import test_diagnostic as td
    td.DiagnosticTests.setUpClass()
    fixture=td.DiagnosticTests();case,row,roles,known,observer,life=fixture.fixture()
    a.install()
    safe,p=e.modules(legacy);case=a.plan(p,'reduced','I')[0]
    data,hashes=a.reduced_build(build,legacy)
    known['candidate']={'path':'/synthetic/reduced-sing-box','sha256':data['binary_sha256'],'version':data['version'],
        'provenance_sha256':r.sha(build/'provenance.json'),'build_source_identity':identity,'build_evidence_files':hashes}
    role=roles[0];b=known['candidate']
    role.update(origin='candidate',classification='experimental',binary=b['path'],sha256=b['sha256'],version=b['version'])
    role['launches'][0]['sha256']=b['sha256']
    row.update(case=case['id'],stage='I',protocol='vless',roles=case['roles'])
    directory=root/'I';leaf=directory/case['id'];leaf.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        artifact=json.loads(subprocess.check_output([sys.executable,str(r.HERE/'export_node.py'),'--port','12345'],env={**os.environ,'VUI_DATA_DIR':tmp},text=True))
    node=artifact['node'];digest=c.digest
    export={'artifact':safe.h.c.life.scrub(artifact),'artifact_sha256':digest(artifact),'node_sha256':digest(node),'consumer_node_sha256':digest(node),'node_fields_unchanged':True,
      'consumer':'ordinary-yaml-node-in-listener-shell','server_configuration':'isolated-research-guarded-not-product-udp','http_api_export_executed_here':False,'hash_scope':'raw-fake-fixture-export-before-redaction'}
    shell={'mixed-port':row['application_associations'][0]['control']['peer'][1],'bind-address':'127.0.0.1','allow-lan':False,'mode':'rule','log-level':'debug','ipv6':False,'find-process-mode':'off','geo-auto-update':False,'profile':{'store-selected':False,'store-fake-ip':False},'proxies':[node],'rules':['MATCH,'+node['name']]}
    roles[-1]['config_sha256']=digest(shell);observer.update(source_identity=identity,device_role=deepcopy(roles[-1]))
    r.save(leaf/'socks-association-observation.json',observer);r.save(leaf/'lifecycle-observation.json',life)
    row['verification']=r.verify_case(p,case,row,roles,known,directory,identity)
    c.need(row['verification']['case_gate_passed'],'Synthetic complete I did not pass')
    report={'schema_version':1,'kind':r.KIND,'arm':'reduced','stage':'I','product_qualified':False,'original_suite_passed':False,'observations_complete':True,'capacity_gate_passed':True,'cases':[row],'errors':[]}
    for name,value in {'input-identity.json':{'source_identity':identity,'binaries':known},'post-identity.json':{'source_identity':identity,'binary_sha256':{k:v['sha256'] for k,v in known.items()}},'plan.json':[case],
        'result.json':report,'process-binaries.json':roles,case['id']+'/result.json':row,case['id']+'/packets.json':row['packets'],case['id']+'/export-identity.json':export}.items():r.save(directory/name,value)
    rehash(directory)
    checked=e.verify_stage(directory,legacy,'reduced','I')
    stage={'stage':'I','pid':123,'process_group':123,'timeout':False,'signals':[],'returncode':0,'leader_joined':True,'process_group_gone':True,'capacity_gate_passed':True,'safe_to_continue':True,'independent_verification':checked}
    r.save(root/'stages.json',[stage]);r.save(root/'MATRIX_RESULT.json',{'arm':'reduced','all_stages_executed':True,'capacity_gate_passed':True,'product_qualified':False,'stages':[stage]})

def rehash(directory):
    r.save(directory/'manifest.json',{'kind':r.KIND+'-text-evidence','files':{p.relative_to(directory).as_posix():r.sha(p) for p in directory.rglob('*') if p.is_file() and p.name!='manifest.json'}})
