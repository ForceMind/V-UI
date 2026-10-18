#!/usr/bin/env python3
"""Run one finite diagnostic stage; never launches strict C/D/B."""
import argparse
from pathlib import Path
import diagnostic as d
import runtime as r

def main():
    d.install()
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('legacy-repository','official','mihomo','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    for name in ('candidate','candidate-provenance','build-evidence'):
        parser.add_argument('--'+name,type=Path)
    parser.add_argument('--arm',choices=d.ARMS,required=True)
    parser.add_argument('--stage',choices=('E','I'),required=True)
    args=parser.parse_args()
    for name,value in vars(args).items():
        if isinstance(value,Path):setattr(args,name,value.resolve())
    r.need(args.stage in d.ARMS[args.arm], 'Wrong diagnostic stage')
    return r.execute(args)

if __name__=='__main__':raise SystemExit(main())
