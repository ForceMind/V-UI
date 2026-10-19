#!/usr/bin/env python3
"""Fourteen bounded fixture children; independently stop after unsafe cleanup."""
import argparse
import json
from pathlib import Path
import sys
import guard_contract as g
import archive
from run_matrix import run
r=g.r
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('legacy-repository','official','mihomo','candidate','candidate-provenance','build-evidence','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    for key,value in vars(args).items():setattr(args,key,value.resolve())
    args.output.mkdir(parents=True,exist_ok=False);identity=g.build.identity(args.legacy_repository)
    g.adapter.install();args.arm='reduced';known=r.binaries(args,identity['legacy'])
    inputs={'scope':g.scope(),'source':identity,'binaries':known,'run':g.run_binding()};r.save(args.output/'input-identity.json',inputs)
    safe,_=g.safe_archive.modules(args.legacy_repository);r.load_framework(args.legacy_repository);rows=[]
    for case in g.IDS:
        command=[sys.executable,str(g.HERE/'run_case.py'),'--case',case,'--input',str(args.output),'--output',str(args.output/case)]
        for name in ('legacy_repository','official','mihomo','candidate','candidate_provenance','build_evidence'):command+=['--'+name.replace('_','-'),str(getattr(args,name))]
        group,out,err=run(command,timeout=g.TIMEOUT);group['timeout_seconds']=g.TIMEOUT
        directory=args.output/case;directory.mkdir(exist_ok=True)
        r.save(directory/'owned-group.json',group);(directory/'stdout.log').write_text(out);(directory/'stderr.log').write_text(err)
        checked=archive.case_verdict(directory,case,inputs,safe);safe_to_continue=archive.group_safe(group) and checked['execution_safe']
        rows.append({'case':case,'verification':checked,'safe_to_continue':safe_to_continue,'passed':safe_to_continue and group['returncode']==0 and checked['passed']})
        r.save(args.output/'matrix.json',{'kind':g.KIND,'cases':rows,'unexecuted':list(g.IDS[len(rows):]),'passed':len(rows)==len(g.IDS) and all(x['passed'] for x in rows),'product_qualified':False})
        print(json.dumps(rows[-1]),flush=True)
        if not safe_to_continue:break
    r.save(args.output/'post-identity.json',{'source':g.build.identity(args.legacy_repository),'binary_sha256':{k:r.sha(v['path']) for k,v in known.items()}})
    _,checked=archive.verify(args.output,args.legacy_repository,args.build_evidence)
    print(json.dumps(checked['verdict']),flush=True);raise SystemExit(0 if checked['verdict']['passed'] else 1)
