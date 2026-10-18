#!/usr/bin/env python3
"""One owned process group per finite stage; continue after RED only if safe."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import runtime as r
import evidence

RUNTIME = r.HERE / 'runtime.py'

def group_exists(pgid):
    try: os.killpg(pgid,0);return True
    except ProcessLookupError:return False

def run(command, timeout=1250, *, cwd=None, env=None):
    proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True,cwd=cwd,env=env)
    row={'pid':proc.pid,'process_group':proc.pid,'timeout':False,'signals':[]}
    out = err = ''
    try:
        out,err=proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        row['timeout']=True;out=err=''
    finally:
        for sig in (signal.SIGTERM,signal.SIGKILL):
            if not group_exists(proc.pid):break
            try: os.killpg(proc.pid,sig);row['signals'].append(sig.name)
            except ProcessLookupError: break
            deadline=time.monotonic()+3
            while group_exists(proc.pid) and time.monotonic()<deadline:
                proc.poll();time.sleep(.025)
        proc.wait(timeout=3)
    row.update(returncode=proc.returncode,leader_joined=True,process_group_gone=not group_exists(proc.pid))
    return row,out,err

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('legacy-repository','official','mihomo','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    for name in ('candidate','candidate-provenance','build-evidence'):
        parser.add_argument('--'+name,type=Path)
    parser.add_argument('--arm',choices=r.ARMS,required=True)
    args=parser.parse_args()
    for name,value in vars(args).items():
        if isinstance(value,Path): setattr(args,name,value.resolve())
    args.output.mkdir(parents=True,exist_ok=False)
    rows=[]
    for stage in r.ARMS[args.arm]:
        command=[sys.executable,str(RUNTIME),'--arm',args.arm,'--stage',stage,'--output',str(args.output/stage)]
        for name in ('legacy_repository','official','mihomo','candidate','candidate_provenance','build_evidence'):
            value=getattr(args,name)
            if value is not None:command+=['--'+name.replace('_','-'),str(value.resolve())]
        row,out,err=run(command);row['stage']=stage
        (args.output/(stage+'.stdout')).write_text(out);(args.output/(stage+'.stderr')).write_text(err)
        path=args.output/stage/'result.json'
        report=json.loads(path.read_text()) if path.is_file() else {}
        try:
            checked=evidence.verify_stage(args.output/stage,args.legacy_repository,args.arm,stage)
        except (ValueError,KeyError,TypeError,FileNotFoundError) as exc:
            checked={'execution_safe':False,'capacity_gate_passed':False,'error':str(exc)}
        row['independent_verification']=checked
        row['capacity_gate_passed']=checked.get('capacity_gate_passed') is True
        row['safe_to_continue']=(not row['timeout'] and row['returncode'] in (0,1) and not row['signals'] and
            row['leader_joined'] and row['process_group_gone'] and checked.get('execution_safe') is True)
        rows.append(row);r.save(args.output/'stages.json',rows)
        print(json.dumps(row),flush=True)
        if not row['safe_to_continue']:break
    passed=len(rows)==len(r.ARMS[args.arm]) and all(x['safe_to_continue'] and x['capacity_gate_passed'] for x in rows)
    r.save(args.output/'MATRIX_RESULT.json',{'arm':args.arm,'all_stages_executed':len(rows)==len(r.ARMS[args.arm]),
        'capacity_gate_passed':passed,'product_qualified':False,'stages':rows})
    return 0 if passed else 1

if __name__=='__main__':raise SystemExit(main())

