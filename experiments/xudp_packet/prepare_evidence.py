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
    labels |= {'gofmt-shared-'+name for name in ('mux_writer_test','mux_encrypted_test','mux_encrypted_cancel_test','mux_empty_datagram_test','route_mux_test','route_lifecycle_test')}
    labels |= {kind+'-'+mode for kind in ('mux-unit','mux-encrypted','mux-encrypted-cancel','mux-empty-datagram') for mode in ('race','debug')} | {'mux-route-race'}
    return {label+suffix for label in labels for suffix in ('.stdout','.stderr','.exit.json')} | {
        'normalized-modules.json','mechanism-identity.json','provenance.json','RESULT.json','VERIFICATION.json'}


GO_JSONL = {label+'-'+mode+'.stdout' for label in
            ('common-network', 'common-bufio', 'protocol-socks', 'mux-unit', 'mux-encrypted', 'mux-encrypted-cancel', 'mux-empty-datagram')
            for mode in ('race', 'debug')} | {'mux-route-race.stdout'}


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            c.need(key not in result, 'Duplicate JSON key in evidence')
            result[key] = value
        return result
    def check(value):
        if isinstance(value, str): c.need('\x00' not in value, 'NUL in structured evidence')
        elif isinstance(value, list):
            for item in value: check(item)
        elif isinstance(value, dict):
            for key, item in value.items(): check(key); check(item)
    data = json.loads(text, object_pairs_hook=pairs)
    check(data)
    return data


def validate_text(path):
    text = path.read_text()
    c.need('\x00' not in text and 'PRIVATE KEY-----' not in text and 'BEGIN CERTIFICATE' not in text, 'Binary or key material in evidence: ' + path.name)
    # Go emits JSONL. A byte-literal regex must never inspect JSON syntax:
    # a test name ending /b followed by its quote is not Python b"payload".
    go_jsonl = path.name in GO_JSONL
    if path.suffix == '.json':
        data = strict_json(text)
        c.need(c.life.scrub(data) == data, 'Unsanitized structured evidence: ' + path.name)
    elif go_jsonl:
        for line in text.splitlines():
            if line.strip():
                data = strict_json(line)
                c.need(isinstance(data, dict) and c.life.scrub(data) == data,
                       'Unsanitized Go JSONL evidence: ' + path.name)
    else:
        c.need(c.life.clean(text) == text, 'Unsanitized text evidence: ' + path.name)
    return text


def prepare(build,output,runtime=None):
    c.need(build.is_dir(),'No build evidence created')
    output.mkdir(parents=True,exist_ok=False)
    allowed=allowed_build()
    entries=[]
    for p in sorted(build.iterdir()):
        c.need(p.is_file() and not p.is_symlink() and p.name in allowed,'Unexpected evidence member: '+p.name)
        text=validate_text(p)
        target=output/p.name
        target.write_text(text)
        entries.append({'path':p.name,'bytes':target.stat().st_size,'sha256':c.sha256(target)})
    if runtime is not None and runtime.exists():
        import packet_boundary as boundary
        allowed_runtime={'stages.json','RUNTIME_RESULT.json'}
        for stage in boundary.ROLES:
            allowed_runtime |= {stage+'/'+name for name in boundary.expected_artifacts(boundary.plan(stage))}
            allowed_runtime.add(stage+'/manifest.json')
        for p in sorted(runtime.rglob('*')):
            c.need(not p.is_symlink(),'Linked runtime evidence')
            if not p.is_file():continue
            name=str(p.relative_to(runtime))
            c.need(name in allowed_runtime,'Unexpected runtime evidence: '+name)
            text=validate_text(p)
            target=output/'runtime'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text)
            entries.append({'path':'runtime/'+name,'bytes':target.stat().st_size,'sha256':c.sha256(target)})
    c.need(bool(entries),'No evidence members')
    (output/'MANIFEST.json').write_text(json.dumps({'files':entries,'count':len(entries)},indent=2)+'\n')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--runtime',type=Path)
    a=parser.parse_args();prepare(a.build,a.output,a.runtime)
