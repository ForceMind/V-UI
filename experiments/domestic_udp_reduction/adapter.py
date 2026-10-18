"""Opt-in two-arm domestic reduction. Old standard/diagnostic defaults unchanged."""
from copy import deepcopy
from pathlib import Path
import os
import subprocess
import contract as c
import diagnostic as d
import evidence as e
import verify_build as vb
r=c.r

def plan(p,arm,stage):
    c.need(arm in c.ARMS and stage=='I','Unknown reduced runtime arm')
    return [{'id':'I_vless_small_idle','stage':'I','protocol':'vless',
        'roles':{'domestic':'candidate','overseas':'official','device-mihomo':'mihomo'},
        'mode':'idle_diagnostic','packets':[p.packet('first',1200),p.packet('recover',1200)]}]

def validate_build_bytes(safe,name,data,fmt,arm='reduced'):
    # Same strict JSON/UTF-8/secret guard; new kind has its own exact verifier.
    return safe.validate_bytes('reduced-provenance.json' if name=='provenance.json' else name,data,fmt)

def reduced_build(directory,legacy):
    safe,_=e.modules(legacy)
    members=safe.collect(directory,c.allowed_files())
    c.need(set(members)==set(c.allowed_files()),'Incomplete reduced build archive')
    for n,data in members.items():validate_build_bytes(safe,n,data,c.allowed_files()[n])
    checked=vb.verify(directory,legacy)
    c.need(c.read(Path(directory)/'VERIFICATION.json')==checked and c.read(Path(directory)/'RESULT.json')==
           {'passed':True,'errors':[],'runtime_executed':False,'product_qualified':False},'Reduced build cached verdict differs')
    return c.read(Path(directory)/'provenance.json'),{n:safe.digest(data) for n,data in members.items()}

def install():
    old_binary=r.binaries;old_identity=e.candidate_identity;old_files=e.build_files;old_build=e.verify_build;old_validate=e.validate_build_bytes
    d.install(idle_repair=True)
    # Retain the same I case and observer, changing only exact role/build identity.
    d.ARMS=c.ARMS;d.KIND=c.RUNTIME_KIND;r.ARMS=c.ARMS;r.KIND=c.RUNTIME_KIND;r.plan=plan;d.plan=plan
    e.KIND=c.RUNTIME_KIND+'-archive'
    def binaries(args,legacy_identity):
        if args.arm=='full-reference':return old_binary(args,legacy_identity)
        c.need(args.arm=='reduced','Unknown binary arm')
        stock=deepcopy(args);stock.arm='stock';known=old_binary(stock,legacy_identity)
        c.need(args.candidate and args.candidate_provenance and args.build_evidence,'Missing reduced build inputs')
        data,files=reduced_build(args.build_evidence,args.legacy_repository)
        path=args.candidate.resolve(strict=True)
        c.need(path.is_file() and os.access(path,os.X_OK) and r.sha(path)==data['binary_sha256'] and c.read(args.candidate_provenance)==data,'Reduced binary/provenance differs')
        call=subprocess.run([str(path),'version'],capture_output=True,text=True,timeout=15)
        c.need(call.returncode==0 and call.stdout.splitlines()[0]=='sing-box version '+data['version'],'Reduced actual version differs')
        known['candidate']={'path':str(path),'sha256':r.sha(path),'version':data['version'],
            'provenance_sha256':r.sha(args.candidate_provenance),'build_source_identity':data['source_identity'],'build_evidence_files':files}
        return known
    def identity(known,legacy,arm):
        if arm!='reduced':return old_identity(known,legacy,arm)
        c.need(known['candidate']['build_source_identity']==c.identity(legacy) and known['candidate']['version']==c.version(c.identity(legacy)), 'Reduced runtime identity differs')
    def files(legacy,arm):return c.allowed_files() if arm=='reduced' else old_files(legacy,arm)
    def build(directory,legacy,arm):return reduced_build(directory,legacy)[0] if arm=='reduced' else old_build(directory,legacy,arm)
    r.binaries=binaries;e.candidate_identity=identity;e.build_files=files;e.verify_build=build
    e.validate_build_bytes=lambda safe,n,data,fmt,arm:validate_build_bytes(safe,n,data,fmt) if arm=='reduced' else old_validate(safe,n,data,fmt,arm)
