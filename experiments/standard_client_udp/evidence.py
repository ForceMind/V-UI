#!/usr/bin/env python3
"""Fixed-name UTF-8 archive and independent semantic replay; never executes cores."""
from copy import deepcopy
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import runtime as r

KIND = r.KIND + '-archive'

def modules(legacy):
    sys.path.insert(0, str(legacy/'experiments/socks_idle_recovery'))
    r.load('harness', legacy/'experiments/socks_idle_recovery/harness.py')
    safe = r.load('standard_archive_safety', legacy/'experiments/socks_idle_recovery/prepare_runtime_evidence.py')
    p = r.load('packet_boundary', legacy/'experiments/xudp_packet/packet_boundary.py')
    return safe, p

def stage_files(p, arm, stage):
    cases = r.plan(p, arm, stage)
    files = p.expected_artifacts(cases) | {'manifest.json', 'post-identity.json'}
    files |= {c['id']+'/export-identity.json' for c in cases}
    return {n: 'json' if n.endswith('.json') else 'text' for n in files}

def validate(safe, name, data, fmt):
    # The sole intentional UUID field is the exact redacted ordinary exporter
    # result. Regeneration below, not a generic secret-field exception, binds it.
    if name.endswith('/export-identity.json'):
        value = safe.strict_json(data.decode('utf-8'))
        clean = deepcopy(value)
        r.need(clean['artifact']['node'].pop('uuid') == '<fixture>', 'Unredacted export UUID')
        safe.check_clean(clean)
    else:
        safe.validate_bytes(name, data, fmt)

def read(safe, data):
    return safe.strict_json(data.decode('utf-8'))

def export_check(safe, value, port, row, roles):
    with tempfile.TemporaryDirectory(prefix='vui-export-verify-') as tmp:
        call = subprocess.run([sys.executable, str(r.HERE/'export_node.py'), '--port', str(port)],
            cwd=r.ROOT, env={**os.environ, 'VUI_DATA_DIR': tmp}, capture_output=True, text=True, timeout=20, check=True)
    artifact = safe.strict_json(call.stdout)
    node = artifact['node']; digest = lambda x: hashlib.sha256(r.canonical(x)).hexdigest()
    r.need(value['artifact'] == safe.h.c.life.scrub(artifact) and value['artifact_sha256'] == digest(artifact)
           and value['node_sha256'] == value['consumer_node_sha256'] == digest(node)
           and value['node_fields_unchanged'] is True and value['http_api_export_executed_here'] is False
           and value['consumer'] == 'ordinary-yaml-node-in-listener-shell'
           and value['hash_scope'] == 'raw-fake-fixture-export-before-redaction'
           and value['server_configuration'] == 'isolated-research-guarded-not-product-udp', 'Export identity mismatch')
    if row.get('application_associations'):
        port=row['application_associations'][0]['control']['peer'][1]
        config={'mixed-port':port,'bind-address':'127.0.0.1','allow-lan':False,'mode':'rule','log-level':'debug','ipv6':False,
            'find-process-mode':'off','geo-auto-update':False,'profile':{'store-selected':False,'store-fake-ip':False},
            'proxies':[node],'rules':['MATCH,'+node['name']]}
        device=[x for x in roles if x['role']=='device-mihomo']
        r.need(len(device)==1 and device[0]['config_sha256']==digest(config), 'Ordinary export does not match actual device configuration')


def verify_stage(directory, legacy, arm, stage, expected_source=None):
    safe, p = modules(legacy)
    files = stage_files(p, arm, stage); members = safe.collect(directory, files)
    for name, data in members.items(): validate(safe, name, data, files[name])
    def get(name): return read(safe, members[name])
    manifest = get('manifest.json')
    r.need(manifest == {'kind': r.KIND+'-text-evidence', 'files': {n: safe.digest(d) for n,d in members.items() if n != 'manifest.json'}}, 'Stage manifest differs')
    report = get('result.json'); cases = r.plan(p, arm, stage)
    r.need(report['schema_version'] == 1 and report['kind'] == r.KIND and report['arm'] == arm and report['stage'] == stage
           and report['product_qualified'] is False and report['original_suite_passed'] is False, 'Stage scope changed')
    r.need(get('plan.json') == cases, 'Recorded plan differs from frozen contract')
    identity = get('input-identity.json'); source = identity['source_identity']; known = identity['binaries']
    r.need(source['legacy'] == r.source(legacy, legacy=True), 'Frozen source manifest differs')
    r.need(source['product'] == (expected_source or r.source(r.ROOT)), 'Product source manifest differs')
    r.need(set(known) == ({'official','mihomo'} if arm == 'stock' else {'official','mihomo','candidate'}), 'Unexpected binary inputs')
    for name, sha, version in [('official',r.STOCK,'1.14.2'),('mihomo',r.MIHOMO,'1.19.32')]:
        r.need(known[name]['sha256'] == sha and known[name]['version'] == version, 'Stock binary identity differs')
    if arm != 'stock':
        r.need(known['candidate']['build_source_identity']['vui_commit'] == r.LEGACY
               and known['candidate']['version'] == '1.14.2-vui-socks-owner-experiment-'+r.LEGACY[:12], 'Candidate identity differs')
    roles = get('process-binaries.json'); rows = report['cases']
    r.need([x['case'] for x in rows] == [c['id'] for c in cases[:len(rows)]], 'Case sequence missing/extra/reordered')
    r.need(all(x['case'] in {c['case'] for c in rows} for x in roles), 'Unstarted process role')
    verdicts=[]
    for case,row in zip(cases,rows):
        base = case['id']+'/'
        r.need(get(base+'result.json') == row and get(base+'packets.json') == row['packets'], 'Case/raw packets differ')
        owned=[x for x in roles if x['case']==case['id']]
        if row['packets'] or owned:
            r.need(base+'export-identity.json' in members, 'Started process/packet lacks ordinary export identity')
        if base+'export-identity.json' in members:
            value = get(base+'export-identity.json')
            export_check(safe,value,value['artifact']['node']['port'],row,owned)
        owned=[x for x in roles if x['case']==case['id']]
        try:
            verdict=r.verify_case(p,case,row,owned,known,Path(directory),source)
            r.need(row['verification'] == verdict, 'Cached verification differs from independent replay')
        except (ValueError,KeyError,TypeError,FileNotFoundError) as exc:
            verdict={'execution_safe':False,'case_gate_passed':False,'error':str(exc)}
        verdicts.append(verdict)
    complete=len(rows)==len(cases) and all(x['execution_safe'] for x in verdicts)
    passed=complete and not report['errors'] and all(x['case_gate_passed'] for x in verdicts)
    if complete:
        r.need(get('post-identity.json') == {'source_identity':source,'binary_sha256':{k:v['sha256'] for k,v in known.items()}}, 'Post-run identities changed')
    r.need(report['observations_complete'] is complete and report['capacity_gate_passed'] is passed, 'Summary contradicts independent replay')
    return {'observations_complete':complete,'execution_safe':complete and not report['errors'], 'capacity_gate_passed':passed,'cases':verdicts}

def matrix_files(p, arm, legacy):
    files={'MATRIX_RESULT.json':'json','stages.json':'json'}
    for stage in r.ARMS[arm]:
        files.update({stage+'/'+n:f for n,f in stage_files(p,arm,stage).items()})
        files.update({stage+'.stdout':'text',stage+'.stderr':'text'})
    if arm != 'stock':
        safe,_ = modules(legacy)
        files.update({'build/'+n:f for n,f in safe.allowed_files().items() if not n.startswith('runtime/')})
    return files

def verify_matrix(directory, legacy, arm):
    safe,p=modules(legacy); files=matrix_files(p,arm,legacy); members=safe.collect(directory,files)
    for n,d in members.items(): validate(safe,n,d,files[n])
    rows=read(safe,members['stages.json']); report=read(safe,members['MATRIX_RESULT.json'])
    r.need([x['stage'] for x in rows] == list(r.ARMS[arm][:len(rows)]), 'Matrix stage sequence differs')
    verdicts=[]
    for index,row in enumerate(rows):
        r.need(index==0 or rows[index-1]['safe_to_continue'] is True, 'Stage launched after unsafe predecessor')
        verdict=verify_stage(Path(directory)/row['stage'],legacy,arm,row['stage'])
        owned=(not row['timeout'] and row['returncode'] in (0,1) and row['signals']==[] and row['leader_joined'] is True and row['process_group_gone'] is True)
        r.need(row['independent_verification'] == verdict, 'Recorded independent stage verdict differs')
        r.need(row['safe_to_continue'] is (owned and verdict['execution_safe']) and row['capacity_gate_passed'] is verdict['capacity_gate_passed'], 'Coordinator summary differs')
        verdicts.append(verdict)
    passed=len(rows)==len(r.ARMS[arm]) and all(x['safe_to_continue'] and x['capacity_gate_passed'] for x in rows)
    r.need(report == {'arm':arm,'all_stages_executed':len(rows)==len(r.ARMS[arm]),'capacity_gate_passed':passed,'product_qualified':False,'stages':rows}, 'Matrix verdict differs')
    return members, {'arm':arm,'capacity_gate_passed':passed,'product_qualified':False,'stages':verdicts}

def write_archive(safe, output, roots, members, manifest_name, manifest):
    output=safe.absolute_path(output)
    roots=[safe.absolute_path(p) for p in roots if p is not None]
    r.need(len(roots)==len(set(roots)), 'Duplicate evidence roots')
    for root in roots:
        r.need(root!=output and root not in output.parents and output not in root.parents, 'Archive and evidence roots overlap')
    raw=safe.canonical(manifest)
    r.need(len(raw)<=safe.MAX_FILE_BYTES and sum(len(d) for d in members.values())+len(raw)<=safe.MAX_TOTAL_BYTES,
           'Combined archive budget exceeded')
    parent=safe.open_directory(output.parent)
    try:
        os.mkdir(output.name,mode=0o700,dir_fd=parent)
        out=os.open(output.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
    finally: os.close(parent)
    try:
        for name,data in [*sorted(members.items()),(manifest_name,raw)]:
            safe.member_name(name); fd=os.dup(out)
            try:
                parts=PurePosixPath(name).parts
                for part in parts[:-1]:
                    try:os.mkdir(part,mode=0o700,dir_fd=fd)
                    except FileExistsError:pass
                    child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                    os.close(fd);fd=child
                child=os.open(parts[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                with os.fdopen(child,'wb') as stream:stream.write(data)
            finally:os.close(fd)
    finally:os.close(out)

def prepare(args):
    safe,p=modules(args.legacy_repository)
    members, verdict=verify_matrix(args.runtime,args.legacy_repository,args.arm)
    inodes=set();budget={'bytes':0}
    again=safe.collect(args.runtime,matrix_files(p,args.arm,args.legacy_repository),inodes=inodes,budget=budget)
    r.need(again==members,'Runtime changed during preparation')
    if args.arm != 'stock':
        r.need(args.build_evidence is not None, 'Candidate archive needs complete build evidence')
        build_files={n:f for n,f in safe.allowed_files().items() if not n.startswith('runtime/')}
        build=safe.collect(args.build_evidence,build_files,inodes=inodes,budget=budget)
        for n,d in build.items():
            safe.validate_bytes(n,d,build_files[n]);members['build/'+n]=d
    manifest={'schema_version':1,'kind':KIND,'arm':args.arm,'product_qualified':False,
        'files':{n:{'bytes':len(d),'sha256':safe.digest(d)} for n,d in members.items()},'verdict':verdict,
        'run':{k:os.environ.get(k) for k in ('GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_SHA','GITHUB_WORKFLOW')}}
    write_archive(safe,args.output,[args.runtime,args.build_evidence],members,'ARCHIVE.json',manifest)
    verify(args.output,args.legacy_repository,args.arm)

def verify(directory,legacy,arm):
    safe,p=modules(legacy); files={**matrix_files(p,arm,legacy),'ARCHIVE.json':'json'}
    members=safe.collect(directory,files); raw=members.pop('ARCHIVE.json')
    safe.validate_bytes('ARCHIVE.json',raw,'json'); manifest=read(safe,raw)
    r.need(set(manifest)=={'schema_version','kind','arm','product_qualified','files','verdict','run'}
           and type(manifest['schema_version']) is int and type(manifest['files']) is dict
           and set(manifest['run'])=={'GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_SHA','GITHUB_WORKFLOW'}, 'Wrong archive schema fields/types')
    for entry in manifest['files'].values():
        r.need(set(entry)=={'bytes','sha256'} and type(entry['bytes']) is int and entry['bytes']>=0, 'Wrong member metadata')

    # Matrix verifier deliberately does not accept ARCHIVE.json in its input.
    with tempfile.TemporaryDirectory(prefix='vui-archive-check-') as tmp:
        for n,d in members.items():
            dest=Path(tmp)/n;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(d)
        _,verdict=verify_matrix(Path(tmp),legacy,arm)
    if arm != 'stock':
        verifier=safe.h.load('standard_archive_build_verifier',safe.h.HERE/'verify_evidence.py')
        verifier.verify(Path(directory)/'build',safe.h.read_contract())
        provenance=read(safe,members['build/provenance.json'])
        r.need(provenance['source_identity'] == safe.h.identity(), 'Archived build source changed')
        hashes={n.removeprefix('build/'):safe.digest(d) for n,d in members.items() if n.startswith('build/')}
        for stage in r.ARMS[arm]:
            key=stage+'/input-identity.json'
            if key not in members: continue
            binary=read(safe,members[key])['binaries']['candidate']
            r.need(binary['build_evidence_files']==hashes and binary['sha256']==provenance['binary_sha256']
                   and binary['provenance_sha256']==safe.digest(members['build/provenance.json'])
                   and binary['build_source_identity']==provenance['source_identity'], 'Archived runtime/build identity differs')
    r.need(manifest['schema_version']==1 and manifest['kind']==KIND and manifest['arm']==arm and manifest['product_qualified'] is False
           and manifest['files']=={n:{'bytes':len(d),'sha256':safe.digest(d)} for n,d in members.items()}
           and manifest['verdict']==verdict,'Archive manifest differs')
    for k in ('GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_SHA','GITHUB_WORKFLOW'):
        if os.environ.get(k) is not None: r.need(manifest['run'][k] == os.environ[k], 'Archive run binding differs')
    return verdict

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-repository',type=Path,required=True)
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--arm',choices=r.ARMS,required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--build-evidence',type=Path)
    args=parser.parse_args()
    for name,value in vars(args).items():
        if name=='legacy_repository': setattr(args,name,value.resolve())
    if args.output: prepare(args)
    else: print(json.dumps(verify(args.runtime,args.legacy_repository,args.arm),sort_keys=True))
