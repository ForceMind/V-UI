#!/usr/bin/env python3
"""Finite frozen first/second-hop rejection controls and independent TCP sanity."""
import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
import importlib
import json
import os
import re
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import runtime as r

METHODS=('test_05_mihomo_upstream_uuid_ca_sni_stop_recovery','test_07_mihomo_device_uuid_ca_sni','test_tcp_https_mihomo_double_hop')
LABELS=tuple(METHODS[0]+'-'+f for f in ('uuid','ca','sni','stopped'))+tuple(METHODS[1]+'-'+f for f in ('uuid','ca','sni'))+(METHODS[2],)

def sanitize_auth(value, clean):
    """Normalize only the already-redacted frozen UUID rejection wording.

    The archive's credential-assignment guard remains unchanged. Unknown
    UUID/token assignments must still fail that guard rather than be erased.
    """
    if isinstance(value,str):
        return re.sub(r'(?i:\bunknown uuid): <fixture>(?=\s|$)', 'unknown uuid [fixture-redacted]', clean(value))
    if isinstance(value,list):return [sanitize_auth(item,clean) for item in value]
    if isinstance(value,dict):return {sanitize_auth(key,clean):sanitize_auth(item,clean) for key,item in value.items()}
    return value

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('legacy-repository','official','mihomo','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    for name in ('candidate','candidate-provenance','build-evidence'):
        parser.add_argument('--'+name,type=Path)
    parser.add_argument('--arm',choices=('stock','candidate'),required=True)
    args=parser.parse_args()
    for name,value in vars(args).items():
        if isinstance(value,Path): setattr(args,name,value.resolve())
    args.output.mkdir(parents=True,exist_ok=False)
    source={'product':r.source(r.ROOT),'legacy':r.source(args.legacy_repository,legacy=True)}
    known=r.binaries(args,source['legacy']); records=[]; cleanups=[]
    r.save(args.output/'input-identity.json',{'source_identity':source,'binaries':known,'arm':args.arm})
    with tempfile.TemporaryDirectory(prefix='vui-auth-data-') as data:
        os.environ.update(VUI_DATA_DIR=data,VUI_TEST_CORES=str(args.official.parent),VUI_TEST_MIHOMO=str(args.mihomo),VUI_RESTRICTED_UDP_PREFLIGHT='1')
        p,c,_=r.load_framework(args.legacy_repository); module,support=c.base.runtime_modules()
        origin='official' if args.arm=='stock' else 'candidate'
        case={'roles':{'overseas':origin,'domestic':origin,'domestic-reconfigured':origin,
                       'device-mihomo':'mihomo','device-reconfigured-mihomo':'mihomo'}}
        base=r.role_fixture(c,module,args.output,known,case,records,r.ROOT)
        psutil=importlib.import_module('psutil')
        class Fixture(base):
            def safe(self,text):
                return sanitize_auth(super().safe(text),c.life.clean)
            def __init__(self,*a,**kw):
                self.before_threads=set(threading.enumerate())
                self.rejection_proofs=[]
                super().__init__(*a,**kw)
            def reject(self,payload,process,reasons,**kwargs):
                before=process.log_path.read_text(errors='replace');offset=len(before);started=time.monotonic()
                value=super().reject(payload,process,reasons,**kwargs)
                self.rejection_proofs.append({'payload':p.ident(payload),'reasons':list(reasons),
                    'started':started,'completed':time.monotonic(),'window':kwargs.get('window',2),
                    'starts_at_record_boundary':not before or before.endswith('\n'),
                    'fresh_log':self.safe(process.log_path.read_text(errors='replace')[offset:])})
                return value
            def __exit__(self,*a):
                try: return super().__exit__(*a)
                finally:
                    proof={'case':self.label,'processes':[{'pid':x.process.pid,'returncode':x.process.poll(),
                        'pid_absent':not psutil.pid_exists(x.process.pid)} for x in self.processes],
                        'targets':[{'closed':t.closed,'joined':not t.thread.is_alive(),'socket_closed':t.server.socket.fileno()==-1,
                            'host':t.host,'port':t.port,'received':[{'peer':peer,**p.ident(body)} for peer,body in t.received],
                            'expected':[{'peer':peer,**p.ident(body),'count':count} for (peer,body),count in t.expected.items()],
                            'errors':t.errors} for t in self.targets],
                        'rejections':self.rejection_proofs,
                        'dns':{'business':self.answers.snapshot() if self.answers else [],'ordinary':self.ordinary_answers.queries if self.ordinary_answers else []},
                        'fixture_removed':not self.root.exists(),
                        'new_threads':len([t for t in threading.enumerate() if t not in self.before_threads])}
                    cleanups.append(proof);r.save(args.output/'cleanup.json',cleanups)
                    r.need(all(x['returncode']==0 and x['pid_absent'] for x in proof['processes']) and
                        all(x['closed'] and x['joined'] and x['socket_closed'] and not x['errors'] for x in proof['targets']) and proof['fixture_removed'] and proof['new_threads']==0,
                        'Authentication control did not dispose all owned resources')
        tcp=c.base.make_tcp_tests(Fixture,support)
        suite=unittest.TestSuite([module.RestrictedUdpPreflightTests(METHODS[0]),module.RestrictedUdpPreflightTests(METHODS[1]),tcp(METHODS[2])])
        with c.base.experiment_environment(args.output/'cases'), patch.object(module,'RestrictedUdpFixture',Fixture), (args.output/'suite.log').open('w') as log:
            with redirect_stdout(log),redirect_stderr(log): result=c.base.run_suite(suite,log)
        r.need(result['tests_run']==3 and Counter(x['method'] for x in result['methods'])==Counter(METHODS),'Authentication method coverage changed')
        passed=result['passed_methods']==3 and not result['nonpass'] and not result['failures']
        result.update(passed=passed,arm=args.arm,product_qualified=False,udp_capacity_qualified=False,
                      failed_prerequisite_does_not_qualify_following_rejections=True)
        result=sanitize_auth(result,c.life.clean)
        r.save(args.output/'RESULT.json',result)
        for path in args.output.rglob('*'):
            if path.is_file() and path.suffix=='.json':r.save(path,sanitize_auth(json.loads(path.read_text()),c.life.clean))
            elif path.is_file() and path.suffix=='.log':path.write_text(sanitize_auth(path.read_text(),c.life.clean))
    r.need(source=={'product':r.source(r.ROOT),'legacy':r.source(args.legacy_repository,legacy=True)},'Control source changed')
    r.save(args.output/'post-identity.json',{'source_identity':source,'binary_sha256':{k:r.sha(v['path']) for k,v in known.items()}})
    print(json.dumps(result),flush=True)
    return 0 if passed else 1

if __name__=='__main__':raise SystemExit(main())
