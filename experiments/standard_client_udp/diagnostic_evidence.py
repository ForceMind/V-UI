#!/usr/bin/env python3
"""Strict text archive replay under the explicit diagnostic contract."""
import argparse
import json
from pathlib import Path
import diagnostic as d
import evidence as e

def main():
    d.install()
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('legacy-repository','runtime'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--arm',choices=d.ARMS,required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--build-evidence',type=Path)
    parser.add_argument('--require-safe',action='store_true')
    args=parser.parse_args();args.legacy_repository=args.legacy_repository.resolve()
    if args.require_safe:
        members,verdict=e.verify_matrix(args.runtime,args.legacy_repository,args.arm)
        rows=json.loads(members['stages.json'])
        e.r.need(len(rows)==len(d.ARMS[args.arm]) and all(x['safe_to_continue'] for x in rows),
                 'Unsafe predecessor prevents next diagnostic group')
    elif args.output:e.prepare(args)
    else: print(json.dumps(e.verify(args.runtime,args.legacy_repository,args.arm),sort_keys=True))

if __name__=='__main__':main()
