"""Bounded mux experiment; never creates a product core or runtime bundle."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'xudp_address'))
from verify_evidence import verify_all
from build_experiment import SING_BOX, SING_VMESS, GO_VERSION, fixed_checkout, sha256, verify_module_graph

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

def run(args,cwd,env,log,timeout=600):
    p=subprocess.run([str(x) for x in args],cwd=cwd,env=env,text=True,capture_output=True,timeout=timeout)
    log.with_suffix('.stdout').write_text(p.stdout)
    log.with_suffix('.stderr').write_text(p.stderr)
    log.with_suffix('.exit.json').write_text(json.dumps({'args':[str(x) for x in args], 'returncode':p.returncode})+'\n')
    return p

def main():
    p=argparse.ArgumentParser();p.add_argument('--sing-box',type=Path,required=True);p.add_argument('--sing-vmess',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    box=fixed_checkout(a.sing_box,SING_BOX);vmess=fixed_checkout(a.sing_vmess,SING_VMESS)
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);evidence=out/'evidence';evidence.mkdir()
    env={**os.environ,'GOTOOLCHAIN':'local','GOWORK':'off','CGO_ENABLED':'1','GOFLAGS':'-mod=readonly','GOPROXY':'https://proxy.golang.org','GOSUMDB':'sum.golang.org','GOMODCACHE':str(out/'modules'),'GOCACHE':str(out/'cache')}
    version=subprocess.check_output(['go','version'],env=env,text=True).strip()
    if version!='go version '+GO_VERSION+' linux/amd64':raise ValueError('Wrong compiler: '+version)
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    graph=None;results=[]
    for arm in ('route-only','mux'):
        b=out/(arm+'-box');v=out/(arm+'-vmess');shutil.copytree(box,b,ignore=shutil.ignore_patterns('.git'));shutil.copytree(vmess,v,ignore=shutil.ignore_patterns('.git'))
        applied=[]
        for path,target in [(ROOT/'experiments/xudp_address/remoteaddr.patch',v),(ROOT/'experiments/xudp_address/lifecycle.patch',b)]+([(HERE/'mux.patch',v),(HERE/'route_cancel.patch',b)] if arm=='mux' else []):
            subprocess.run(['git','apply','--check',str(path)],cwd=target,check=True)
            subprocess.run(['git','apply',str(path)],cwd=target,check=True)
            applied.append({'name':path.name,'sha256':sha256(path)})
        shutil.copyfile(ROOT/'experiments/xudp_address/route_lifecycle_test.go',b/'route/route_lifecycle_test.go')
        shutil.copyfile(HERE/'route_mux_test.go',b/'route/route_mux_test.go')
        if arm=='mux':shutil.copyfile(HERE/'mux_writer_test.go',v/'mux_writer_test.go')
        subprocess.run(['gofmt','-w',str(b/'route/route_lifecycle_test.go'),str(b/'route/route_mux_test.go')]+([str(v/'mux.go'),str(v/'mux_writer.go'),str(v/'mux_writer_test.go')] if arm=='mux' else []),check=True)
        subprocess.run(['go','mod','edit','-replace=github.com/sagernet/sing-vmess='+str(v)],cwd=b,env=env,check=True)
        modules=run(['go','list','-m','-json','all'],b,env,evidence/(arm+'-modules'))
        if modules.returncode:raise ValueError('module graph failed')
        normalized=verify_module_graph(modules.stdout,v)
        if graph is not None and graph!=normalized:raise ValueError('arms select different dependencies')
        graph=normalized
        (evidence/(arm+'-normalized-modules.json')).write_text(json.dumps(normalized,indent=2)+'\n')
        checks=[]
        if arm=='mux':
            for label,extra in [('unit-race',['-race']),('unit-debug',['-tags=debug'])]:
                r=run(['go','test','-json','-count=1','-timeout=90s',*extra,'-run','^TestMuxBounded','github.com/sagernet/sing-vmess'],b,env,evidence/(arm+'-'+label),timeout=600)
                checks.append({'name':label,'returncode':r.returncode})
        r=run(['go','test','-json','-race','-count=1','-timeout=45s','-run','^TestMuxRoute','./route'],b,{**env,'VUI_LIFECYCLE_PHASE':'lifecycle','VUI_MUX_ARM':arm},evidence/(arm+'-route'),timeout=600)
        checks.append({'name':'actual-route-race','returncode':r.returncode})
        verify=run(['go','mod','verify'],b,env,evidence/(arm+'-module-verify'))
        if verify.returncode or sha256(b/'go.sum')!=sha256(box/'go.sum'):raise ValueError('fixed module integrity changed')
        entry={'arm':arm,'vui_commit':commit,'go_version':version,'fixed_box':SING_BOX,'fixed_vmess':SING_VMESS,'patches':applied,'checks':checks,'source_sha256':{str(f.relative_to(v)):sha256(f) for f in (v/'mux.go',v/'xudp.go')},'route_sha256':sha256(b/'route/conn.go'),'product_qualified':False,'go_binary_sha256':sha256(shutil.which('go')),'test_sha256':{path.name:sha256(path) for path in (HERE/'mux_writer_test.go',HERE/'route_mux_test.go',ROOT/'experiments/xudp_address/route_lifecycle_test.go')},'before_sha256':{'mux.go':sha256(vmess/'mux.go'),'xudp.go':sha256(vmess/'xudp.go'),'route/conn.go':sha256(box/'route/conn.go')}}
        if arm=='mux':entry['source_sha256']['mux_writer.go']=sha256(v/'mux_writer.go')
        (evidence/(arm+'-provenance.json')).write_text(json.dumps(entry,indent=2)+'\n');results.append(entry)
    (evidence/'RESULT.json').write_text(json.dumps({'arms':results,'product_qualified':False},indent=2)+'\n')
    verification_error=None
    try:verify_all(evidence)
    except ValueError as exc:
        verification_error=str(exc)
        (evidence/'VERIFICATION_FAILED.json').write_text(json.dumps({'error':verification_error,'product_qualified':False})+'\n')
    manifest=[{'path':p.name,'bytes':p.stat().st_size,'sha256':sha256(p)} for p in sorted(evidence.iterdir()) if p.is_file()]
    (evidence/'MANIFEST.json').write_text(json.dumps({'files':manifest,'count':len(manifest)},indent=2)+'\n')
    if verification_error or any(c['returncode'] for r in results for c in r['checks']):raise SystemExit(1)
if __name__=='__main__':main()
