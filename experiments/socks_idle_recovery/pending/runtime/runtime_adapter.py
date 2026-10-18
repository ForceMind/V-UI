#!/usr/bin/env python3
"""Explicit owner experiment adapter. Frozen packet/observer/coordinator code is unchanged."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
KIND = 'vui-socks-owner-source-experiment'
RUNTIME_KIND = KIND + '-runtime'
OPTION = 'experimental_socks_udp_idle_recovery'
TAG = 'DOMESTIC_ONLY'
ARMS = {'off': ('C', 'D', 'A', 'B', 'U', 'V'), 'on': ('C', 'D')}
PATCHES = ('experiments/xudp_address/remoteaddr.patch', 'experiments/xudp_address/lifecycle.patch',
           'experiments/xudp_mux/mux.patch', 'experiments/xudp_mux/route_cancel.patch',
           'experiments/xudp_packet/receive.patch', 'experiments/socks_idle_recovery/sing_owner.patch',
           'experiments/socks_idle_recovery/vmess_session.patch', 'experiments/socks_idle_recovery/box_owner.patch')
FROZEN = {'packet_boundary.py': 'eca960dcd56b9f424eb7bfd341776baac18222c1dc77d30360d176b5fc695983',
          'run_runtime.py': 'd55c82f189ae27c6ffededea2a6266c9904cda084162203d3b3043d35ca04b73',
          'verify_execution_safety.py': '681cf4beee07dc0fec942782062b04b40a7f40d4f51e55d864aa15c2d2f2de73',
          'socks_observation.py': '780f307f7e4576d0aca86084d70eeb8c41065bc3aa961c6a4effd2122b50eef5',
          'verify_socks_observation.py': 'ab5f718d5ed89e0e6762e0b4d7070a04fea65a553b203b3bff6fb984be548fd9'}

def need(value, message):
    if not value: raise ValueError(message)

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def load_runtime(repository):
    repo = Path(repository).resolve(strict=True)
    old = repo / 'experiments/xudp_packet'; new = repo / 'experiments/socks_idle_recovery'
    for name, digest in FROZEN.items():
        need(not (old/name).is_symlink() and sha(old/name) == digest, 'Frozen runtime changed: '+name)
    sys.path[:0] = [str(new), str(old)]
    p = load('packet_boundary', old/'packet_boundary.py')
    b = load('build_and_test', old/'build_and_test.py')
    load('socks_observation', old/'socks_observation.py')
    socks = load('verify_socks_observation', old/'verify_socks_observation.py')
    safety = load('verify_execution_safety', old/'verify_execution_safety.py')
    failure = load('verify_failed_observations', old/'verify_failed_observations.py')
    runner = load('owner_frozen_runner', old/'run_runtime.py')
    h = load('harness', new/'harness.py')
    verify_build = load('owner_build_verifier', new/'verify_evidence.py')
    prepare = load('owner_build_preparer', new/'prepare_evidence.py')
    return SimpleNamespace(repo=repo, p=p, b=b, socks=socks, safety=safety, failure=failure,
                           runner=runner, h=h, verify_build=verify_build, prepare=prepare)

def evidence_files(directory):
    directory = Path(directory)
    need(directory.is_dir() and not directory.is_symlink(), 'Invalid build evidence root')
    result = {}
    for path in sorted(directory.rglob('*')):
        need(not path.is_symlink(), 'Linked build evidence')
        if path.is_file(): result[path.relative_to(directory).as_posix()] = sha(path)
        else: need(path.is_dir(), 'Nonregular build evidence')
    need(result, 'Empty build evidence')
    return result

def verify_provenance(rt, data, identity):
    p = rt.p
    need(data.get('schema_version') == 1 and type(data['schema_version']) is int and data.get('kind') == KIND,
         'Not the distinct eight-patch owner provenance')
    need(data.get('source_identity') == identity and data.get('fixed_sources') == p.FIXED and
         data.get('product_qualified') is False, 'Owner exact source mismatch')
    need(data.get('patches') == [{'path': n, 'sha256': identity['files'][n]} for n in PATCHES] and
         all(p.digest(identity['files'][n]) for n in PATCHES), 'Wrong eight-patch order/digests')
    need(data.get('same_head_base_validated') is True and data.get('final_sources_equal_base') is False and
         data.get('final_shared_server_writer_unchanged') is True and 'same_mux_route_sources' not in data,
         'False source-equivalence claim')
    suffix = identity['vui_commit'][:12]
    need(data.get('version') == '1.14.2-vui-socks-owner-experiment-'+suffix and
         data.get('base_version') == '1.14.2-vui-complete-packet-experiment-'+suffix and
         all(p.digest(data.get(k)) for k in ('binary_sha256','base_binary_sha256','module_graph_sha256')),
         'Missing distinct final/base binary identity')
    expected = {'go_version':'go1.25.14','goos':'linux','goarch':'amd64','tags':[], 'cgo_enabled':'0',
                'gotoolchain':'local','gowork':'off','trimpath':True,'buildvcs':False}
    build = data.get('build', {})
    need(set(build) == set(expected)|{'go_binary_sha256'} and all(build[k] == v for k,v in expected.items())
         and p.digest(build['go_binary_sha256']), 'Wrong build compiler/flags')
    p.verify_build_record({'checks': data.get('regression_checks')})
    return data

def validate_manifest(rt, data, identity, repository, *, execute=True):
    p = rt.p; repo = Path(repository).resolve()
    need(HERE == rt.h.HERE.resolve() == repo/'experiments/socks_idle_recovery', 'Runtime adapter is not reviewed checkout source')
    need(set(data) == {'schema_version','kind','source_identity','official','candidate','arm','build_evidence'}, 'Wrong owner input fields')
    need(type(data['schema_version']) is int and data['schema_version'] == 1 and data['kind'] == KIND+'-input'
         and data['source_identity'] == identity and data['arm'] in ARMS, 'Wrong owner input identity/mode')
    stock, candidate = data['official'], data['candidate']
    need(set(stock) == {'path','sha256','version'} and stock['sha256'] == p.STOCK_SHA and stock['version'] == '1.14.2', 'Stock identity changed')
    need(set(candidate) == {'path','sha256','version','provenance_path','provenance_sha256'}, 'Wrong final binary fields')
    provenance_path = Path(candidate['provenance_path'])
    need(provenance_path.is_file() and not provenance_path.is_symlink() and sha(provenance_path) == candidate['provenance_sha256'], 'Provenance changed')
    provenance = verify_provenance(rt, p.json_read(provenance_path), identity)
    need(candidate['sha256'] == provenance['binary_sha256'] and candidate['version'] == provenance['version'], 'Final binary mismatch')
    evidence = data['build_evidence']
    need(set(evidence) == {'path','files'}, 'Wrong final build evidence reference')
    directory = Path(evidence['path'])
    need(evidence_files(directory) == evidence['files'] and p.json_read(directory/'provenance.json') == provenance, 'Build evidence changed')
    contract = rt.h.read_contract(); allowed = rt.prepare.owner_files(contract); old_allowed = rt.h.packet_prepare.allowed_build()
    for name in evidence['files']:
        parts = Path(name).parts
        need((len(parts)==1 and name in allowed) or (len(parts)==2 and parts[0] in ('base','regression') and parts[1] in old_allowed), 'Unknown build evidence file')
    result = p.json_read(directory/'RESULT.json')
    need(result.get('passed') is True and result.get('errors') == [] and result.get('product_qualified') is False
         and result.get('runtime_executed') is False, 'Final mechanism gates incomplete')
    rt.verify_build.verify(directory, contract)
    need(evidence_files(directory) == evidence['files'], 'Verifier altered or evidence changed after verification')
    for row in (stock,candidate):
        path = Path(row['path'])
        need(path.is_file() and not path.is_symlink() and os.access(path, os.X_OK) and sha(path)==row['sha256'], 'Actual executable hash/shape differs')
        if execute:
            result = subprocess.run([str(path),'version'],capture_output=True,text=True,timeout=15)
            need(result.returncode==0 and (result.stdout+result.stderr).splitlines()[0]=='sing-box version '+row['version'], 'Actual executable version differs')
    need(not Path(stock['path']).samefile(candidate['path']), 'Final/stock alias')
    return provenance

def configure_role(config, case, role, arm):
    need(arm in ARMS and case['stage'] in ARMS[arm] and role in case['roles'], 'Role outside finite arm')
    def absent(value):
        if isinstance(value,dict):
            need(OPTION not in value, 'Preexisting owner option')
            for child in value.values(): absent(child)
        elif isinstance(value,list):
            for child in value: absent(child)
    absent(config); config = deepcopy(config)
    enabled = arm=='on' and role=='device-singbox'
    if enabled:
        need(case['roles'][role]=='candidate', 'Owner-on stock forbidden')
        inbounds = config.get('inbounds',[])
        need(len(inbounds)==1 and inbounds[0].get('type')=='mixed' and
             [o.get('tag') for o in config.get('outbounds',[])]==[TAG] and config.get('route',{}).get('final')==TAG,
             'Owner-on selected route/inbound changed')
        inbounds[0][OPTION] = {'outbound':TAG}
    return config, {'owner_recovery_enabled':enabled,'owner_recovery_outbound': TAG if enabled else None}

def install(rt, arm):
    need(arm in ARMS, 'Unknown arm')
    p = rt.p; original_fixture = p.role_fixture
    p.KIND = RUNTIME_KIND
    def identity(repo,c):
        need(HERE == rt.h.HERE.resolve() == Path(repo).resolve()/'experiments/socks_idle_recovery', 'Unreviewed adapter execution')
        return rt.h.identity()
    p.source_identity = identity
    p.validate_manifest = lambda data, source, repo, execute=True: validate_manifest(rt,data,source,repo,execute=execute)
    def fixture(c,module,output,binaries,case,records):
        base = original_fixture(c,module,output,binaries,case,records)
        class Fixture(base):
            def process(self,label,config,*,mihomo=False,env=None):
                changed, flags = configure_role(config,case,label,arm)
                core = super().process(label,changed,mihomo=mihomo,env=env)
                row = records[-1]
                row.update(flags)
                row['log_file'] = {'overseas':'001-overseas.log','domestic':'002-domestic.log','device-singbox':'003-device-singbox.log'}[label]
                p.save(output/'process-binaries.json',records)
                return core
        return Fixture
    p.role_fixture = fixture
    rt.runner.STAGES = ARMS[arm]
    return rt

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository',type=Path,required=True); parser.add_argument('--arm',choices=ARMS,required=True)
    parser.add_argument('--stage',required=True); parser.add_argument('--binary-manifest',type=Path)
    parser.add_argument('--output',type=Path); parser.add_argument('--describe',action='store_true')
    args=parser.parse_args(argv); need(args.stage in ARMS[args.arm], 'Stage outside finite arm')
    rt=install(load_runtime(args.repository),args.arm)
    command=['--stage',args.stage,'--repository',str(args.repository)]
    if args.describe: command+=['--describe']
    else:
        need(args.binary_manifest and args.output,'Manifest and fresh output required')
        need(rt.p.json_read(args.binary_manifest).get('arm')==args.arm, 'Input arm differs')
        command+=['--binary-manifest',str(args.binary_manifest),'--output',str(args.output)]
    return rt.p.main(command)

if __name__=='__main__': raise SystemExit(main())
