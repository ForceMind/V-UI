"""Independent exact reduced-build replay; archived paths are metadata only."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import contract as c
r=c.r

def strict_go(path,expected,package):
    rows=[c.strict_json(line) for line in Path(path).read_text().splitlines() if line.strip()]
    c.need(rows and all(isinstance(x,dict) and x.get('Package')==package for x in rows),'Wrong/empty Go package')
    runs=Counter();terminals=Counter();package_end=[];started=set();finished=set()
    for row in rows:
        c.need(not package_end,'Go records after package terminal')
        action=row.get('Action');name=row.get('Test')
        c.need(action in ('start','run','pause','cont','output','pass','skip','fail'),'Unknown Go action')
        c.need(action!='start' or not name,'Test has package start action')
        c.need(action not in ('skip','fail'),'Go failure/skip')
        if name:
            c.need(name in expected,'Unexpected Go test')
            if action=='run':
                c.need(name not in started,'Duplicate Go run')
                parent=name.rpartition('/')[0]
                c.need(not parent or parent in started-finished,'Go child outside parent lifetime')
                started.add(name);runs[name]+=1
            if action=='pass':
                c.need(name in started and name not in finished,'Missing run/duplicate terminal')
                c.need(all(x in finished for x in expected if x.startswith(name+'/')),'Go parent passed before child')
                finished.add(name);terminals[name]+=1
        elif action=='pass':
            c.need(finished==set(expected),'Go package ended before tests');package_end.append(action)
    c.need(runs==terminals==Counter(expected) and package_end==['pass'],'Incomplete exact Go coverage')
    return rows

def expected_tests(h):
    mux=h['mux'];address=h['address'];life=h['lifecycle']
    cancel=r.load('reduced_cancel_contract',Path(mux.__file__).with_name('verify_encrypted_cancel.py'))
    empty=r.load('reduced_empty_contract',Path(mux.__file__).with_name('verify_empty_datagram.py'))
    return {
      'address':set(address.RED)|set(address.GREEN)|set(address.CHILDREN),
      'mux-unit':{'TestMuxBounded'+n for n in mux.UNIT_CASES}|{'TestMuxBoundedPartialFrameCancel/'+s for s in ('b','e','w')},
      'mux-encrypted':set(mux.ENCRYPTED_CASES),
      'mux-encrypted-cancel':set(cancel.CIPHERS)|{t+'/'+s for t in cancel.CIPHERS for s in cancel.LEAVES},
      'mux-empty-datagram':set(empty.CIPHERS)|{t+'/'+s for t in empty.CIPHERS for s in empty.LEAVES},
      'mux-route':set(mux.ROUTE_CASES),
      'lifecycle':set(c.LIFECYCLE_TOP)|set(life.GUARDS),
      'count':{'TestOwnerConnectionManagerConcurrentCount'},
    }

def semantic(group,path,h,rows):
    mux=h['mux']
    if group=='address':c.need(not any('D2_EXPECTED_' in x.get('Output','') for x in rows),'Address assertion failed')
    elif group=='mux-encrypted-cancel':mux.verify_encrypted_cancel(path)
    elif group=='mux-empty-datagram':mux.verify_empty_datagram(path)
    elif group=='mux-route':mux.verify_route(path,'mux')
    elif group=='lifecycle':
        life=h['lifecycle'];events={n:[] for n in expected_tests(h)['lifecycle'] if n not in {life.TEST_PREFIX+'GuardScoping'}}
        pending={}
        for row in rows:
            name=row.get('Test');output=row.get('Output','')
            if not output:continue
            pending[name]=pending.get(name,'')+output
            while '\n' in pending[name]:
                line,pending[name]=pending[name].split('\n',1)
                if life.PREFIX in line:
                    c.need(name in events,'Lifecycle event has wrong test')
                    events[name].append(json.loads(line.split(life.PREFIX,1)[1]))
        c.need(not any(pending.values()),'Incomplete lifecycle output')
        for name,records in events.items():life.verify_events('lifecycle',name,records)

def verify(directory,legacy):
    directory=Path(directory);legacy=Path(legacy);h=c.helpers(legacy)
    read=lambda n:c.read(directory/n)
    data=read('provenance.json');identity=c.identity(legacy);inputs=c.validate_inputs(legacy)
    c.need(type(data.get('schema_version')) is int and data['schema_version']==1 and data['kind']==c.KIND and data['product_qualified'] is False,'Wrong reduced build scope')
    c.need(data['source_identity']==identity and data['fixed_sources']==c.PINS['fixed_sources'],'Reduced source identity differs')
    c.need(data['input_files']==inputs and data['patches']==[{'path':p,'sha256':inputs[p]} for p in c.PATCHES],'Reduced inputs/patch order differs')
    c.need(data['version']==c.version(identity) and re.fullmatch('[0-9a-f]{64}',data['binary_sha256']) and data['binary_sha256'] not in (r.STOCK,r.MIHOMO),'Invalid reduced binary/version')
    expected_build={'go_binary_sha256':c.PINS['go_binary_sha256'],'go_version':'go1.25.14','flags':['-trimpath','-buildvcs=false'],'goos':'linux','goarch':'amd64','cgo_enabled':'0','tags':[]}
    c.need(data['build']==expected_build,'Compiler/build settings differ')
    before=read('source-before.json');after=read('source-after.json');tested=read('source-tested.json');post=read('source-post.json')
    c.validate_source(before,after)
    injected={target:inputs[name] for name,target in c.TESTS.items()}
    c.need(data['injected_tests']==injected and not set(injected)&set(after),'Injected test set differs')
    dirs={f:Path(p) for f,p in data['source_directories'].items()}
    c.need(set(dirs)=={'sing-box','sing-vmess'} and len({p.parent for p in dirs.values()})==1 and all(p.is_absolute() and p.name==f and '..' not in p.parts for f,p in dirs.items()),'Invalid source directory metadata')
    box=dirs['sing-box'];vmess=dirs['sing-vmess'];out=box.parent
    mod_before=(directory/'go.mod.before.txt').read_bytes();mod_after=(directory/'go.mod.after.txt').read_bytes()
    c.need(hashlib.sha256(mod_before).hexdigest()==c.PINS['upstream_go_mod_sha256'],'Original go.mod differs')
    c.need(mod_after==mod_before+b'\nreplace github.com/sagernet/sing-vmess => '+str(vmess).encode()+b'\n','Unexpected go.mod edits')
    c.need(tested==post=={**after,**injected,'sing-box/go.mod':hashlib.sha256(mod_after).hexdigest()},'Test/build source drift')
    c.need(post['sing-box/go.sum']==c.PINS['upstream_go_sum_sha256'],'go.sum changed')
    graph=read('normalized-modules.json')
    c.need(len(graph)==c.PINS['normalized_module_graph_members'] and c.digest(graph)==data['module_graph_sha256']==c.PINS['normalized_module_graph_sha256'],'Reduced module graph differs')
    compiler=read('compiler.exit.json')['args'][0]
    c.need(Path(compiler).is_absolute() and Path(compiler).name=='go','Compiler path malformed')
    base_environment=read('compiler.exit.json')['environment']
    c.need({k:base_environment[k] for k in c.ENV}==c.ENV and set(base_environment)==set(c.ENV)|{'GOMODCACHE','GOCACHE','VUI_LIFECYCLE_PHASE','VUI_MUX_ARM'},'Build environment differs')
    c.need(base_environment['VUI_LIFECYCLE_PHASE']=='lifecycle' and base_environment['VUI_MUX_ARM']=='mux','Wrong test phase')
    c.need(all(Path(base_environment[k]).is_absolute() for k in ('GOMODCACHE','GOCACHE')),'Cache paths malformed')
    checked=set()
    def command(label,argv,cwd,extra=None):
        row=read(label+'.exit.json');checked.add(label)
        c.need(row['args']==list(map(str,argv)) and row['cwd']==str(cwd) and row['environment']=={**base_environment,**(extra or {})},'Wrong command/environment: '+label)
        c.need(type(row['returncode']) is int and row['returncode']==0 and row['timeout'] is False and row['signals']==[] and row['leader_joined'] is True and row['process_group_gone'] is True and type(row['pid']) is int and row['pid']>0 and row['process_group']==row['pid'],'Failed/unjoined command: '+label)
    # Runner paths are used for string comparison only, never opened during replay.
    product_root=Path(read('compiler.exit.json')['cwd'])
    c.need(product_root.is_absolute(),'Invalid product cwd')
    command('compiler',[compiler,'version'],product_root)
    c.need((directory/'compiler.stdout').read_text().strip()=='go version go1.25.14 linux/amd64','Wrong compiler version output')
    legacy_root=None
    for i,name in enumerate(c.PATCHES):
        for suffix,extra in [('check',['--check']),('apply',[])]:
            label=f'patch-{i}-{suffix}';row=read(label+'.exit.json');patch=Path(row['args'][-1])
            root=product_root if i==4 else Path(str(patch)[:-len(name)]).resolve()
            if i<4:
                if legacy_root is None:legacy_root=root
                c.need(root==legacy_root,'Mixed frozen patch roots')
            c.need(patch==root/name,'Wrong patch path')
            command(label,['git','apply',*extra,patch],vmess if i in (0,2) else box)
    command('gofmt-source',[Path(compiler).with_name('gofmt'),'-w',vmess/'mux.go',vmess/'mux_writer.go'],out)
    command('replace',[compiler,'mod','edit','-replace=github.com/sagernet/sing-vmess='+str(vmess)],box)
    for label in ('modules','modules-after'):
        command(label,[compiler,'list','-m','-json','all'],box)
        c.need(h['builder'].verify_module_graph((directory/(label+'.stdout')).read_text(),vmess)==graph,'Raw graph differs')
    groups={};expected=expected_tests(h)
    for group,(pattern,package) in c.GROUPS.items():
        for mode,flags in c.MODES.items():
            label=group+'-'+mode;path=directory/(label+'.stdout')
            command(label,[compiler,'test','-json','-count=1','-timeout=120s',*flags,'-run',pattern,package],box)
            rows=strict_go(path,expected[group],'github.com/sagernet/sing-box/route' if package=='./route' else package)
            semantic(group,path,h,rows);groups[label]={'terminals':len(expected[group]),'all_passed':True}
    for label,package in c.CROSS.items():
        for target in ('darwin','windows'):
            name=f'cross-{label}-{target}'
            command(name,[compiler,'test','-c','-o',out/(name+'.test'),package],box,{'GOOS':target,'CGO_ENABLED':'0'})
    command('build',[compiler,'build','-trimpath','-buildvcs=false','-ldflags=-X github.com/sagernet/sing-box/constant.Version='+data['version'],'-o',out/'reduced-sing-box','./cmd/sing-box'],box,{'CGO_ENABLED':'0'})
    command('module-verify',[compiler,'mod','verify'],box)
    c.need((directory/'module-verify.stdout').read_text().strip()=='all modules verified','Modules not verified')
    command('version',[out/'reduced-sing-box','version'],out,{'CGO_ENABLED':'0'})
    c.need((directory/'version.stdout').read_text().splitlines()[0]=='sing-box version '+data['version'],'Actual binary version differs')
    c.need(checked==set(c.labels()),'Missing/extra command contract')
    expected_checks=[g+'-'+m for g in c.GROUPS for m in c.MODES]+[f'cross-{p}-{os}' for p in c.CROSS for os in ('darwin','windows')]+['build','module-verify']
    c.need(data['checks']==[{'label':x,'returncode':0} for x in expected_checks],'Checks ledger differs')
    return {'kind':c.KIND,'groups':groups,'cross_compile_only':['darwin/amd64','windows/amd64'],'runtime_executed':False,'product_qualified':False}
