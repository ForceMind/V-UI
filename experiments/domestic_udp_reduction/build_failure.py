#!/usr/bin/env python3
"""Retain bounded partial build text as explicitly unqualified failure evidence."""
import argparse
import os
from pathlib import Path
import adapter as a
c=a.c;r=c.r;e=a.e
KIND='vui-domestic-reduced-build-failure'

def collect(directory,legacy):
    safe,_=e.modules(legacy);allowed={**c.allowed_files(),'failure.json':'json'}
    members=safe.collect(directory,allowed)
    c.need('failure.json' in members,'Missing actual builder failure')
    for n,data in members.items():a.validate_build_bytes(safe,n,data,allowed[n])
    failure=safe.strict_json(members['failure.json'].decode())
    c.need(set(failure)=={'kind','failed','error_type','error','runtime_executed','product_qualified'} and failure['kind']==KIND and failure['failed'] is True and failure['runtime_executed'] is False and failure['product_qualified'] is False and all(type(failure[k]) is str and failure[k] for k in ('error_type','error')),'Wrong builder failure record')
    return safe,members,sorted(set(c.allowed_files())-members.keys())

def verify(directory,legacy):
    safe,_=e.modules(legacy);allowed={**c.allowed_files(),'failure.json':'json','BUILD_FAILURE.json':'json'}
    members=safe.collect(directory,allowed);raw=members.pop('BUILD_FAILURE.json');safe.validate_bytes('BUILD_FAILURE.json',raw,'json');manifest=safe.strict_json(raw.decode())
    c.need(set(manifest)=={'schema_version','kind','source_identity','files','missing','run','build_qualified','runtime_executed','product_qualified'} and type(manifest['schema_version']) is int and manifest['schema_version']==1 and manifest['kind']==KIND and manifest['build_qualified'] is False and manifest['runtime_executed'] is False and manifest['product_qualified'] is False,'Wrong failure archive scope')
    c.need(manifest['source_identity']==c.identity(legacy) and manifest['files']=={n:{'bytes':len(d),'sha256':safe.digest(d)} for n,d in members.items()} and manifest['missing']==sorted(set(c.allowed_files())-members.keys()),'Failure archive identity/members differ')
    c.need(set(manifest['run'])=={'GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_SHA','GITHUB_WORKFLOW'},'Wrong failure run keys')
    for k,value in manifest['run'].items():
        if os.environ.get(k) is not None:c.need(value==os.environ[k],'Failure run identity differs')
    # Collect only fixed members into a new temporary replay directory. This
    # checks actual failure schema without trusting cached manifest fields.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        for n,d in members.items():(Path(tmp)/n).write_bytes(d)
        collect(Path(tmp),legacy)
    return {'build_qualified':False,'runtime_executed':False,'product_qualified':False}

def prepare(args):
    safe,members,missing=collect(args.runtime,args.legacy_repository)
    manifest={'schema_version':1,'kind':KIND,'source_identity':c.identity(args.legacy_repository),
        'files':{n:{'bytes':len(d),'sha256':safe.digest(d)} for n,d in members.items()},'missing':missing,
        'run':{k:os.environ.get(k) for k in ('GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_SHA','GITHUB_WORKFLOW')},
        'build_qualified':False,'runtime_executed':False,'product_qualified':False}
    e.write_archive(safe,args.output,[args.runtime],members,'BUILD_FAILURE.json',manifest)
    verify(args.output,args.legacy_repository)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('runtime','legacy-repository'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--output',type=Path);args=p.parse_args();args.legacy_repository=args.legacy_repository.resolve()
    if args.output:prepare(args)
    else:print(verify(args.runtime,args.legacy_repository))
