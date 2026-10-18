#!/usr/bin/env python3
"""Copy only named UTF-8 build evidence; never upload experimental binaries."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent/'xudp_mux/runtime'))
import common as c


def allowed_build():
    contract=json.loads((HERE/'TEST_CASES.json').read_text())
    labels={'compiler','gofmt-source','replace','modules','build','module-verify'}
    labels |= {'patch-'+str(n)+'-'+step for n in range(5) for step in ('check','apply')}
    labels |= {'gofmt-test-'+Path(item['source']).stem for item in contract['files']}
    for package in ('common-network','common-bufio','protocol-socks'):
        labels |= {package+'-'+mode for mode in ('race','debug','darwin-compile','windows-compile')}
    labels |= {'gofmt-shared-'+name for name in ('mux_writer_test','mux_encrypted_test','mux_encrypted_cancel_test','route_mux_test','route_lifecycle_test')}
    labels |= {kind+'-'+mode for kind in ('mux-unit','mux-encrypted','mux-encrypted-cancel') for mode in ('race','debug')} | {'mux-route-race'}
    return {label+suffix for label in labels for suffix in ('.stdout','.stderr','.exit.json')} | {
        'normalized-modules.json','mechanism-identity.json','provenance.json','RESULT.json','VERIFICATION.json'}


def prepare(build,output,runtime=None):
    c.need(build.is_dir(),'No build evidence created')
    output.mkdir(parents=True,exist_ok=False)
    allowed=allowed_build()
    entries=[]
    for p in sorted(build.iterdir()):
        c.need(p.is_file() and not p.is_symlink() and p.name in allowed,'Unexpected evidence member: '+p.name)
        text=p.read_text()
        c.need('\x00' not in text and c.life.clean(text)==text,'Binary or unsanitized evidence')
        if p.suffix=='.json':
            data=json.loads(text)
            c.need(c.life.scrub(data)==data,'Unsanitized structured evidence')
        target=output/p.name
        target.write_text(text)
        entries.append({'path':p.name,'bytes':target.stat().st_size,'sha256':c.sha256(target)})
    if runtime is not None and runtime.exists():
        import packet_boundary as boundary
        allowed_runtime={'stages.json','RUNTIME_RESULT.json'}
        for stage in ('A','B','C','D'):
            allowed_runtime |= {stage+'/'+name for name in boundary.expected_artifacts(boundary.plan(stage))}
            allowed_runtime.add(stage+'/manifest.json')
        for p in sorted(runtime.rglob('*')):
            c.need(not p.is_symlink(),'Linked runtime evidence')
            if not p.is_file():continue
            name=str(p.relative_to(runtime))
            c.need(name in allowed_runtime,'Unexpected runtime evidence: '+name)
            text=p.read_text()
            c.need('\x00' not in text and c.life.clean(text)==text,'Unsanitized runtime evidence')
            if p.suffix=='.json':
                data=json.loads(text);c.need(c.life.scrub(data)==data,'Unsanitized runtime JSON')
            target=output/'runtime'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text)
            entries.append({'path':'runtime/'+name,'bytes':target.stat().st_size,'sha256':c.sha256(target)})
    c.need(bool(entries),'No evidence members')
    (output/'MANIFEST.json').write_text(json.dumps({'files':entries,'count':len(entries)},indent=2)+'\n')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--runtime',type=Path)
    a=parser.parse_args();prepare(a.build,a.output,a.runtime)
