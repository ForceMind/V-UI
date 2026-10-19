#!/usr/bin/env python3
"""Strict finite text archive; failed/incomplete children stay red and unsafe."""
import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
import guard_contract as g
import verify_case
r=g.r

def case_members(raw,case,inputs,safe):
    allowed={n.removeprefix(case+'/'):fmt for n,fmt in g.files().items() if n.startswith(case+'/')}
    try:
        for name,data in raw.items():
            if name=='export-identity.json':g.safe_archive.validate(safe,case+'/'+name,data,'json')
            else:safe.validate_bytes(name,data,allowed[name])
        g.need(set(raw)==set(allowed),'Incomplete child evidence members')
        row=g.safe_archive.read(safe,raw['result.json']);roles=g.safe_archive.read(safe,raw['process-binaries.json']);export=g.safe_archive.read(safe,raw['export-identity.json'])
        g.need(row['case']==case,'Wrong fixture directory identity')
        joined='\n'.join(name+':\n'+text for name,text in sorted(row['process_logs'].items()))
        g.need(raw['fixture.log'].decode('utf-8')==joined,'Combined fixture log differs from process lifetime logs')
        verify_case.verify(row,roles,inputs,export,safe,safety_only=True)
        try:return verify_case.verify(row,roles,inputs,export,safe)
        except (ValueError,KeyError,TypeError,IndexError,AssertionError) as exc:
            return {'case':case,'execution_safe':True,'passed':False,'verification_error':type(exc).__name__+': '+str(exc),'product_qualified':False}
    except (ValueError,KeyError,TypeError,IndexError,FileNotFoundError,AssertionError) as exc:
        return {'case':case,'execution_safe':False,'passed':False,'verification_error':type(exc).__name__+': '+str(exc),'product_qualified':False}

def case_verdict(directory,case,inputs,safe):
    allowed={n.removeprefix(case+'/'):fmt for n,fmt in g.files().items() if n.startswith(case+'/')}
    return case_members(safe.collect(Path(directory),allowed),case,inputs,safe)


def group_safe(group):
    return (type(group.get('returncode')) is int and group['returncode'] in (0,1) and group.get('timeout') is False and group.get('signals')==[] and group.get('leader_joined') is True and group.get('process_group_gone') is True and group.get('pid')==group.get('process_group') and type(group.get('pid')) is int and group['pid']>0 and group.get('timeout_seconds')==g.TIMEOUT)

def verify(directory,legacy,build_directory=None):
    directory=Path(directory);safe,_=g.safe_archive.modules(legacy)
    r.load_framework(legacy)
    files=g.files();inodes=set();budget={'bytes':0}
    members=safe.collect(directory,{**files,'ARCHIVE.json':'json'},inodes=inodes,budget=budget)
    archived=members.pop('ARCHIVE.json',None)
    if build_directory is not None:
        g.need(not any(n.startswith('build/') for n in members),'Duplicate build input')
        build=safe.collect(build_directory,g.build.allowed_files(),inodes=inodes,budget=budget)
        members.update({'build/'+name:data for name,data in build.items()})
    g.need({n.removeprefix('build/') for n in members if n.startswith('build/')}==set(g.build.allowed_files()),'Incomplete 124 build proof')
    for name,data in members.items():
        if name.startswith('build/'):g.adapter.validate_build_bytes(safe,name.removeprefix('build/'),data,files[name])
        elif name.endswith('/export-identity.json'):g.safe_archive.validate(safe,name,data,'json')
        else:safe.validate_bytes(name,data,files[name])
    get=lambda name:g.safe_archive.read(safe,members[name])
    inputs=get('input-identity.json');identity=g.build.identity(legacy)
    g.need(inputs['scope']==g.scope() and inputs['source']==identity,'Wrong guard source/scope')
    g.need(set(inputs)=={'scope','source','binaries','run'} and set(inputs['run'])==set(g.run_binding()),'Unknown input schema')
    for key,value in inputs['run'].items():
        g.need(type(value) is str,'Bad run metadata')
        if os.environ.get(key) is not None:g.need(value==os.environ[key],'Run binding differs')
    if inputs['run']['GITHUB_SHA']!='local':g.need(inputs['run']['GITHUB_SHA']==identity['product']['commit'],'Run/source SHA mismatch')
    # Replay one collected byte snapshot; never re-read caller paths for semantics.
    with tempfile.TemporaryDirectory(prefix='vui-guard-build-snapshot-') as temporary:
        snapshot=Path(temporary)
        for name,data in members.items():
            if not name.startswith('build/'):continue
            target=snapshot/name.removeprefix('build/')
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(data)
        provenance,hashes=g.adapter.reduced_build(snapshot,legacy)
    binary=inputs['binaries']['candidate']
    g.need(binary['sha256']==provenance['binary_sha256'] and binary['version']==provenance['version'] and binary['provenance_sha256']==safe.digest(members['build/provenance.json']) and binary['build_source_identity']==identity and binary['build_evidence_files']==hashes,'Runtime/124 build binding differs')
    matrix=get('matrix.json');g.need(set(matrix)=={'kind','cases','unexecuted','passed','product_qualified'} and matrix['kind']==g.KIND and matrix['product_qualified'] is False,'Wrong matrix fields')
    cases=matrix['cases'];g.need([x['case'] for x in cases]==list(g.IDS[:len(cases)]) and len(cases)<=len(g.IDS),'Wrong child execution order')
    expected_names={'input-identity.json','post-identity.json','matrix.json'}|{'build/'+n for n in g.build.allowed_files()}
    verdicts=[]
    for i,item in enumerate(cases):
        case=item['case'];prefix=case+'/'
        # Missing child files can be retained only as a fail-closed unsafe terminal.
        for name in ('owned-group.json','stdout.log','stderr.log'):g.need(prefix+name in members,'Missing owned child terminal')
        expected_names|={n for n in files if n.startswith(prefix)}
        group=get(prefix+'owned-group.json');checked=case_members({n.removeprefix(prefix):data for n,data in members.items() if n.startswith(prefix)},case,inputs,safe)
        safe_to_continue=group_safe(group) and checked['execution_safe']
        passed=safe_to_continue and group['returncode']==0 and checked['passed']
        expected={'case':case,'verification':checked,'safe_to_continue':safe_to_continue,'passed':passed}
        g.need(item==expected,'Cached child verdict differs')
        g.need(i==0 or verdicts[-1]['safe_to_continue'],'Unsafe predecessor was followed by new child')
        verdicts.append(expected)
    g.need(set(members)<=expected_names and all(n in members for n in ('input-identity.json','matrix.json','post-identity.json')),'Unexpected unstarted member')
    g.need(get('post-identity.json')=={'source':identity,'binary_sha256':{k:v['sha256'] for k,v in inputs['binaries'].items()}},'Missing coordinator post identity')
    g.need(matrix['unexecuted']==list(g.IDS[len(cases):]),'Wrong unexecuted children')
    g.need(len(cases)==len(g.IDS) or (cases and not verdicts[-1]['safe_to_continue']),'Stopped finite run without unsafe terminal')
    passed=len(cases)==len(g.IDS) and all(x['passed'] for x in verdicts)
    g.need(matrix['passed'] is passed,'Matrix outcome differs')
    verdict={'kind':g.KIND,'passed':passed,'execution_safe':bool(cases) and all(x['safe_to_continue'] for x in verdicts),'children':len(cases),'unexecuted':matrix['unexecuted'],'product_qualified':False}
    manifest={'schema_version':1,'kind':g.KIND+'-archive','run':inputs['run'],'verdict':verdict,'files':{n:{'bytes':len(data),'sha256':safe.digest(data)} for n,data in members.items()}}
    if archived is not None:
        safe.validate_bytes('ARCHIVE.json',archived,'json');old=g.safe_archive.read(safe,archived)
        g.need(type(old.get('schema_version')) is int and all(type(v['bytes']) is int for v in old['files'].values()) and old==manifest,'Archive manifest/schema changed')
    return members,manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('runtime','legacy-repository'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--build-evidence',type=Path);parser.add_argument('--output',type=Path);parser.add_argument('--require-safe',action='store_true')
    args=parser.parse_args();members,manifest=verify(args.runtime,args.legacy_repository.resolve(),args.build_evidence)
    if args.output:
        safe,_=g.safe_archive.modules(args.legacy_repository.resolve());g.safe_archive.write_archive(safe,args.output,[args.runtime,args.build_evidence],members,'ARCHIVE.json',manifest)
        _,again=verify(args.output,args.legacy_repository.resolve());g.need(again==manifest,'Copy changed archive')
    if args.require_safe:g.need(manifest['verdict']['execution_safe'],'Unsafe owned stage')
    print(json.dumps(manifest['verdict']))
