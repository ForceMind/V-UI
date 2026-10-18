#!/usr/bin/env python3
"""Build and test only the fixed reduced domestic core; never publish its binary."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import contract as c
import run_matrix
r=c.r

def command(args,cwd,env,path):
    args=[str(x) for x in args]
    row,out,err=run_matrix.run(args,timeout=1200,cwd=cwd,env=env)
    row.update(args=args,cwd=str(cwd),environment={k:env[k] for k in (*c.ENV,'GOMODCACHE','GOCACHE','VUI_LIFECYCLE_PHASE','VUI_MUX_ARM')})
    r.save(path.with_suffix('.exit.json'),row);path.with_suffix('.stdout').write_text(out);path.with_suffix('.stderr').write_text(err)
    c.need(not row['timeout'] and not row['signals'] and row['leader_joined'] and row['process_group_gone'],'Unsafe build command: '+path.name)
    return row['returncode'],out

def build(args):
    legacy=args.legacy_repository.resolve();identity=c.identity(legacy);input_files=c.validate_inputs(legacy);h=c.helpers(legacy)
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False);evidence=out/'evidence';evidence.mkdir()
    env={**os.environ,**c.ENV,'GOMODCACHE':str(args.module_cache.resolve()),'GOCACHE':str(args.build_cache.resolve()),'VUI_LIFECYCLE_PHASE':'lifecycle','VUI_MUX_ARM':'mux'}
    env.pop('GOROOT',None)
    go=shutil.which('go');c.need(go and r.sha(go)==c.PINS['go_binary_sha256'],'Fixed compiler executable differs')
    def run(name,argv,cwd,extra=None):return command(argv,cwd,{**env,**(extra or {})},evidence/name)
    rc,stdout=run('compiler',[go,'version'],c.ROOT)
    c.need(rc==0 and stdout.strip()=='go version go1.25.14 linux/amd64','Wrong compiler version')
    trees={};before={};after={};tested={};checks=[]
    official={'sing-box':(args.sing_box,c.PINS['fixed_sources']['sing_box']),
              'sing-vmess':(args.sing_vmess,c.PINS['fixed_sources']['sing_vmess'])}
    for family,(path,sha) in official.items():
        original=h['builder'].fixed_checkout(path,sha);trees[family]=out/family
        shutil.copytree(original,trees[family],ignore=shutil.ignore_patterns('.git'))
    before=c.manifest(trees);r.save(evidence/'source-before.json',before)
    for i,name in enumerate(c.PATCHES):
        target=trees['sing-vmess' if i in (0,2) else 'sing-box'];patch=c.source_file(name,legacy)
        for suffix,extra in [('check',['--check']),('apply',[])]:
            rc,_=run(f'patch-{i}-{suffix}',['git','apply',*extra,patch],target)
            c.need(rc==0,'Patch failed: '+name)
    rc,_=run('gofmt-source',[Path(go).with_name('gofmt'),'-w',trees['sing-vmess']/'mux.go',trees['sing-vmess']/'mux_writer.go'],out)
    c.need(rc==0,'Source gofmt failed')
    after=c.manifest(trees);c.validate_source(before,after);r.save(evidence/'source-after.json',after)
    # Go accepts these committed bytes directly; no opaque formatter transform.
    injected={}
    for name,target in c.TESTS.items():
        path=out/target;c.need(not path.exists() and path.parent.is_dir(),'Test overwrites source')
        source=c.source_file(name,legacy);shutil.copyfile(source,path);injected[target]=r.sha(source)
    box,vmess=trees['sing-box'],trees['sing-vmess']
    (evidence/'go.mod.before.txt').write_bytes((box/'go.mod').read_bytes())
    rc,_=run('replace',[go,'mod','edit','-replace=github.com/sagernet/sing-vmess='+str(vmess)],box)
    c.need(rc==0,'Module replacement failed')
    (evidence/'go.mod.after.txt').write_bytes((box/'go.mod').read_bytes())
    rc,raw=run('modules',[go,'list','-m','-json','all'],box)
    c.need(rc==0,'Module graph failed');graph=h['builder'].verify_module_graph(raw,vmess)
    c.need(c.digest(graph)==c.PINS['normalized_module_graph_sha256'] and len(graph)==c.PINS['normalized_module_graph_members'],'Fixed graph differs')
    r.save(evidence/'normalized-modules.json',graph)
    tested=c.manifest(trees)
    c.need(tested=={**after,**injected,'sing-box/go.mod':r.sha(box/'go.mod')},'Test injection changed production source')
    r.save(evidence/'source-tested.json',tested)
    for group,(pattern,package) in c.GROUPS.items():
        for mode,extra in c.MODES.items():
            label=group+'-'+mode
            rc,_=run(label,[go,'test','-json','-count=1','-timeout=120s',*extra,'-run',pattern,package],box,
                     {'VUI_LIFECYCLE_PHASE':'lifecycle','VUI_MUX_ARM':'mux'})
            checks.append({'label':label,'returncode':rc})
    for label,package in c.CROSS.items():
        for target in ('darwin','windows'):
            name=f'cross-{label}-{target}'
            rc,_=run(name,[go,'test','-c','-o',out/(name+'.test'),package],box,{'GOOS':target,'CGO_ENABLED':'0'})
            checks.append({'label':name,'returncode':rc})
    version=c.version(identity);binary=out/'reduced-sing-box'
    rc,_=run('build',[go,'build','-trimpath','-buildvcs=false','-ldflags=-X github.com/sagernet/sing-box/constant.Version='+version,
                      '-o',binary,'./cmd/sing-box'],box,{'CGO_ENABLED':'0'});checks.append({'label':'build','returncode':rc})
    rc,_=run('module-verify',[go,'mod','verify'],box);checks.append({'label':'module-verify','returncode':rc})
    rc,raw=run('modules-after',[go,'list','-m','-json','all'],box)
    c.need(rc==0 and h['builder'].verify_module_graph(raw,vmess)==graph,'Post graph changed')
    rc,raw=run('version',[binary,'version'],out,{'CGO_ENABLED':'0'})
    c.need(rc==0 and raw.splitlines()[0]=='sing-box version '+version,'Actual reduced version differs')
    post=c.manifest(trees);r.save(evidence/'source-post.json',post)
    c.need(post==tested and c.identity(legacy)==identity and r.sha(go)==c.PINS['go_binary_sha256'],'Source/compiler changed during build')
    data={'schema_version':1,'kind':c.KIND,'source_identity':identity,'fixed_sources':c.PINS['fixed_sources'],
          'patches':[{'path':p,'sha256':input_files[p]} for p in c.PATCHES],'input_files':input_files,
          'source_directories':{f:str(p) for f,p in trees.items()},'binary_sha256':r.sha(binary),'version':version,
          'build':{'go_binary_sha256':r.sha(go),'go_version':'go1.25.14','flags':['-trimpath','-buildvcs=false'],
                   'goos':'linux','goarch':'amd64','cgo_enabled':'0','tags':[]},
          'module_graph_sha256':c.digest(graph),'injected_tests':injected,'checks':checks,'product_qualified':False}
    r.save(out/'provenance.json',data);r.save(evidence/'provenance.json',data)
    import verify_build
    errors=[]
    try:verified=verify_build.verify(evidence,legacy);r.save(evidence/'VERIFICATION.json',verified)
    except (ValueError,KeyError,TypeError,FileNotFoundError) as exc:errors.append(str(exc))
    r.save(evidence/'RESULT.json',{'passed':not errors,'errors':errors,'runtime_executed':False,'product_qualified':False})
    c.need(not errors,'Reduced build proof failed: '+str(errors))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('legacy-repository','sing-box','sing-vmess','output','module-cache','build-cache'):
        parser.add_argument('--'+key,type=Path,required=True)
    build(parser.parse_args())
