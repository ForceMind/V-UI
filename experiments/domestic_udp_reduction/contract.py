"""Exact domestic-only reduction contract. No core is run by this module."""
from pathlib import Path
import hashlib
import json
import math
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT/'experiments/standard_client_udp'))
import runtime as r
need=r.need
KIND='vui-domestic-reduced-build'
RUNTIME_KIND='vui-domestic-reduction-idle'
ARMS={'full-reference':('I',),'reduced':('I',)}
MODES={'race':['-race'],'debug':['-race','-tags=debug']}
ENV={'GOOS':'linux','GOARCH':'amd64','CGO_ENABLED':'1','GOTOOLCHAIN':'local','GOWORK':'off','GOENV':'off',
     'GOAMD64':'v1','GOEXPERIMENT':'','GOFLAGS':'-mod=readonly','GOPROXY':'https://proxy.golang.org',
     'GOSUMDB':'sum.golang.org','GOPRIVATE':'','GONOPROXY':'','GONOSUMDB':''}
PINS=json.loads((HERE/'SOURCE_IDENTITIES.json').read_text())
DEPENDENCIES=json.loads((HERE/'DEPENDENCIES.json').read_text())
PATCHES=tuple(x['path'] for x in DEPENDENCIES['patches'][:4])+('experiments/domestic_udp_reduction/count.patch',)
TESTS={
 'experiments/xudp_address/xudp_remoteaddr_experiment_test.go':'sing-vmess/xudp_remoteaddr_experiment_test.go',
 'experiments/xudp_address/route_lifecycle_test.go':'sing-box/route/route_lifecycle_test.go',
 'experiments/xudp_mux/mux_writer_test.go':'sing-vmess/mux_writer_test.go',
 'experiments/xudp_mux/mux_encrypted_test.go':'sing-vmess/mux_encrypted_test.go',
 'experiments/xudp_mux/mux_encrypted_cancel_test.go':'sing-vmess/mux_encrypted_cancel_test.go',
 'experiments/xudp_mux/mux_empty_datagram_test.go':'sing-vmess/mux_empty_datagram_test.go',
 'experiments/xudp_mux/route_mux_test.go':'sing-box/route/route_mux_test.go',
 'experiments/domestic_udp_reduction/count_test.go':'sing-box/route/reduced_count_test.go',
}
LIFECYCLE_TOP=tuple('TestLifecycleXUDP'+s for s in ('IdleCancellation','ActivityUpload','ActivityDownload','NormalUploadEnd',
 'NormalDownloadEnd','ParentCancel','CancelEOFCompetition','BlockedOnClose','BlockedInboundClose',
 'GuardScoping','AlreadyCanceledContext','NilOnClose'))
GROUPS={
 'address':('^TestExperimentXUDP','github.com/sagernet/sing-vmess'),
 'mux-unit':('^TestMuxBounded','github.com/sagernet/sing-vmess'),
 'mux-encrypted':('^TestMuxEncrypted(AES|ChaCha)$','github.com/sagernet/sing-vmess'),
 'mux-encrypted-cancel':('^TestMuxEncryptedCancel(AES|ChaCha)$','github.com/sagernet/sing-vmess'),
 'mux-empty-datagram':('^TestMuxEmptyDatagram(Raw|AES|ChaCha)$','github.com/sagernet/sing-vmess'),
 'mux-route':('^TestMuxRoute','./route'),
 'lifecycle':('^('+ '|'.join(LIFECYCLE_TOP)+')$','./route'),
 'count':('^TestOwnerConnectionManagerConcurrentCount$','./route'),
}
CROSS={'vmess':'github.com/sagernet/sing-vmess','route':'./route'}

def digest(value):return hashlib.sha256(r.canonical(value)).hexdigest()
def strict_json(text):
    def pairs(items):
        result={}
        for key,value in items:
            need(key not in result,'Duplicate JSON key');result[key]=value
        return result
    def constant(value):raise ValueError('Nonfinite JSON value: '+value)
    value=json.loads(text,object_pairs_hook=pairs,parse_constant=constant)
    def finite(item):
        if isinstance(item,float):need(math.isfinite(item),'Nonfinite JSON number')
        elif isinstance(item,str):
            item.encode('utf-8',errors='strict');need('\x00' not in item,'NUL in JSON')
        elif isinstance(item,dict):
            for key,child in item.items():finite(key);finite(child)
        elif isinstance(item,list):
            for child in item:finite(child)
    finite(value)
    return value
def read(path):return strict_json(Path(path).read_text())
def version(identity):return '1.14.2-vui-domestic-reduced-'+identity['product']['commit'][:12]
def identity(legacy):return {'product':r.source(ROOT),'legacy':r.source(legacy,legacy=True)}
def source_file(name,legacy):return (ROOT if name.startswith('experiments/domestic_udp_reduction/') else legacy)/name

def validate_inputs(legacy):
    need(DEPENDENCIES['legacy_commit']==r.LEGACY and len(DEPENDENCIES['patches'])==8,'Changed dependency scope')
    for i,row in enumerate(DEPENDENCIES['patches']):
        path=legacy/row['path']
        need(row['index']==i and path.stat().st_size==row['bytes'] and r.sha(path)==row['sha256'],'Frozen patch differs')
    original=(legacy/'experiments/socks_idle_recovery/box_owner.patch').read_text()
    a=original.index('@@ -47,6 +47,8 @@');b=original.index('@@ -154,',a)
    expected='diff --git a/route/conn.go b/route/conn.go\n--- a/route/conn.go\n+++ b/route/conn.go\n'+original[a:b]
    need((HERE/'count.patch').read_text()==expected and r.sha(HERE/'count.patch')==PINS['count_patch_sha256'],'Count hunk differs')
    old=(legacy/'experiments/socks_idle_recovery/tests/sing-box/route/packet_session_owner_test.go').read_text()
    body=old[old.index('// No socket I/O'):]
    new=(HERE/'count_test.go').read_text()
    need(new[new.index('// No socket I/O'):]==body and body.count('func Test')==1,'Count regression body differs')
    return {name:r.sha(source_file(name,legacy)) for name in (*PATCHES,*TESTS)}

def manifest(trees):
    result={}
    for family,tree in trees.items():
        for p in sorted(tree.rglob('*')):
            need(not p.is_symlink(),'Linked source member')
            if p.is_file() and '.git' not in p.parts:result[family+'/'+p.relative_to(tree).as_posix()]=r.sha(p)
    return result

def validate_source(before,after):
    need(len(before)==PINS['baseline_manifest_members'] and digest(before)==PINS['baseline_manifest_sha256'],'Official complete source baseline differs')
    need({n for n in before.keys()|after.keys() if before.get(n)!=after.get(n)}==set(PINS['source_files']),'Unexpected production source delta')
    for n,pair in PINS['source_files'].items():need({'before':before.get(n),'after':after.get(n)}==pair,'Wrong exact source bytes: '+n)

def labels():
    preparation=['compiler']+[f'patch-{i}-{s}' for i in range(5) for s in ('check','apply')]
    preparation+=['gofmt-source','replace','modules','modules-after','build','module-verify','version']
    return preparation+[g+'-'+m for g in GROUPS for m in MODES]+[f'cross-{p}-{os}' for p in CROSS for os in ('darwin','windows')]

def allowed_files():
    files={name+suffix:('json' if suffix=='.exit.json' else 'text') for name in labels() for suffix in ('.exit.json','.stdout','.stderr')}
    files.update({n:'json' for n in ('provenance.json','source-before.json','source-after.json','source-tested.json','source-post.json',
                                    'normalized-modules.json','VERIFICATION.json','RESULT.json')})
    files.update({g+'-'+m+'.stdout':'jsonl' for g in GROUPS for m in MODES})
    files.update({'go.mod.before.txt':'text','go.mod.after.txt':'text'})
    return files

def helpers(legacy):
    sys.path.insert(0,str(legacy/'experiments/xudp_mux'))
    names={
      'builder':'experiments/xudp_address/build_experiment.py',
      'mux':'experiments/xudp_mux/verify_evidence.py',
      'address':'experiments/xudp_address/verify_go_controls.py',
      'lifecycle':'experiments/xudp_address/verify_route_lifecycle.py',
    }
    modules={k:r.load('domestic_reduced_'+k,legacy/v) for k,v in names.items()}
    return modules
