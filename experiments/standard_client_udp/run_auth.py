#!/usr/bin/env python3
"""Owned bounded authentication group, only after independent safe-stage replay."""
import argparse
import json
from pathlib import Path
import sys
import evidence as e
import runtime as r
from run_matrix import run

def safe_predecessor(legacy, arm, directory):
    members,_=e.verify_matrix(directory,legacy,arm)
    rows=json.loads(members['stages.json'])
    r.need(rows and all(x['safe_to_continue'] for x in rows), 'Unsafe predecessor prevents any new core group')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('legacy-repository','official','mihomo','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    for name in ('candidate','candidate-provenance','build-evidence'):
        parser.add_argument('--'+name,type=Path)
    parser.add_argument('--arm',choices=('stock','candidate'),required=True)
    parser.add_argument('--prior',action='append',required=True,help='arm=absolute matrix directory')
    args=parser.parse_args()
    for name,value in vars(args).items():
        if isinstance(value,Path):setattr(args,name,value.resolve())
    for prior in args.prior:
        arm,directory=prior.split('=',1);safe_predecessor(args.legacy_repository,arm,Path(directory))
    command=[sys.executable,str(r.HERE/'auth_controls.py'),'--arm',args.arm]
    for name in ('legacy_repository','official','mihomo','candidate','candidate_provenance','build_evidence','output'):
        value=getattr(args,name)
        if value is not None:command+=['--'+name.replace('_','-'),str(value)]
    row,out,err=run(command,timeout=300)
    row.update(command=command,timeout_seconds=300)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'group.stdout').write_text(out);(args.output/'group.stderr').write_text(err)
    r.save(args.output/'owned-group.json',row)
    print(json.dumps(row),flush=True)
    return 0 if row['returncode']==0 and not row['timeout'] and not row['signals'] and row['leader_joined'] and row['process_group_gone'] else 1

if __name__=='__main__':raise SystemExit(main())
