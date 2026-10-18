#!/usr/bin/env python3
"""Allowlisted frozen authentication-control text; capacity is never qualified."""
import argparse
from collections import Counter
from pathlib import Path
import json
import hashlib
import os
import evidence as e
import runtime as r
from auth_controls import METHODS,LABELS

def verify(directory,legacy,arm,build_directory=None):
    safe,_=e.modules(legacy)
    names={'input-identity.json','post-identity.json','process-binaries.json','cleanup.json','RESULT.json','suite.log','owned-group.json','group.stdout','group.stderr'}
    names|={'cases/'+label+suffix for label in LABELS for suffix in ('.json','.log')}
    files={n:'json' if n.endswith('.json') else 'text' for n in names}
    build_files={n:f for n,f in safe.allowed_files().items() if not n.startswith('runtime/')} if arm=='candidate' else {}
    inodes=set();budget={'bytes':0}
    members=safe.collect(directory,{**files,**{'build/'+n:f for n,f in build_files.items()},'ARCHIVE.json':'json'},inodes=inodes,budget=budget)
    if build_directory is not None:
        r.need(arm=='candidate' and not any(n.startswith('build/') for n in members),'Duplicate/irrelevant build evidence')
        build=safe.collect(build_directory,build_files,inodes=inodes,budget=budget)
        members.update({'build/'+n:d for n,d in build.items()})
    for n,d in members.items():
        if n.startswith('build/'):safe.validate_bytes(n.removeprefix('build/'),d,build_files[n.removeprefix('build/')])

    archived=members.pop('ARCHIVE.json',None)
    for n,d in members.items():
        if not n.startswith('build/'):safe.validate_bytes(n,d,files[n])
    r.need({n for n in members if not n.startswith('build/')}==names,'Incomplete authentication-control text')
    get=lambda n:e.read(safe,members[n])
    group=get('owned-group.json')
    r.need(group['timeout_seconds']==300 and not group['timeout'] and group['signals']==[] and
           group['returncode'] in (0,1) and group['leader_joined'] and group['process_group_gone'] and
           group['pid']==group['process_group'] and group['pid']>0, 'Authentication group unbounded or not reaped')
    inputs=get('input-identity.json');source=inputs['source_identity'];known=inputs['binaries']
    r.need(source=={'product':r.source(r.ROOT),'legacy':r.source(legacy,legacy=True)} and inputs['arm']==arm,'Control source changed')
    r.need(known['official']['sha256']==r.STOCK and known['official']['version']=='1.14.2' and
           known['mihomo']['sha256']==r.MIHOMO and known['mihomo']['version']=='1.19.32', 'Control stock pins changed')
    r.need(get('post-identity.json')=={'source_identity':source,'binary_sha256':{k:v['sha256'] for k,v in known.items()}},'Control post identity changed')
    if arm=='candidate':
        verifier=safe.h.load('standard_auth_build_verifier',safe.h.HERE/'verify_evidence.py')
        verifier.verify(build_directory or Path(directory)/'build',safe.h.read_contract())
        provenance=get('build/provenance.json');binary=known['candidate']
        hashes={n.removeprefix('build/'):safe.digest(d) for n,d in members.items() if n.startswith('build/')}
        r.need(provenance['source_identity']==safe.h.identity()==binary['build_source_identity'] and
               binary['sha256']==provenance['binary_sha256'] and binary['version']==provenance['version'] and
               binary['provenance_sha256']==safe.digest(members['build/provenance.json']) and
               binary['build_evidence_files']==hashes,'Authentication candidate/build evidence binding differs')
    result=get('RESULT.json');cleanup=get('cleanup.json');roles=get('process-binaries.json')
    r.need(all(x['case'] in LABELS for x in roles), 'Extra authentication process case')

    r.need(result['tests_run']==3 and Counter(x['method'] for x in result['methods'])==Counter(METHODS)
           and result['arm']==arm and result['product_qualified'] is False and result['udp_capacity_qualified'] is False,'Control method/scope changed')
    r.need(Counter(x['case'] for x in cleanup)==Counter(LABELS),'Control fixture coverage changed')
    for case in cleanup:
        owned=[x for x in roles if x['case']==case['case']]
        r.need(Counter(x['launches'][-1]['pid'] for x in owned)==Counter(x['pid'] for x in case['processes']), 'Control launch/cleanup PID differs')
        r.need(case['fixture_removed'] and case['new_threads']==0 and all(x['closed'] and x['joined'] and x['socket_closed'] and not x['errors'] for x in case['targets'])
               and all(x['pid_absent'] and x['returncode']==0 for x in case['processes']),'Unsafe control disposal')
        for role in owned:
            r.need(Counter(x['pid'] for x in role['launches'])==Counter(x['pid'] for x in role['stops']) and
                   all(x['ready'] and x['birth_time']>0 for x in role['launches']) and
                   all(x['returncode']==0 and x['pid_absent'] for x in role['stops']), 'Control restart lifecycle not reaped')
            mihomo=role['role'] in ('device-mihomo','device-reconfigured-mihomo')
            r.need(role['mihomo'] is mihomo,'Authentication role family changed')
            origin='mihomo' if mihomo else ('official' if arm=='stock' else 'candidate')
            r.need(role['origin']==origin and role['sha256']==known[origin]['sha256'] and role['version']==known[origin]['version']
                   and role['owner_option_absent'] and not role['owner_recovery_enabled'] and role['parser_passed'], 'Control role origin changed')
    r.need(result['passed_methods']==sum(x['status']=='passed' for x in result['methods']), 'Control passed-method count differs')
    passed=result['passed_methods']==3 and not result['nonpass'] and not result['failures']
    r.need(result['passed'] is passed,'Control summary differs')
    if passed:
        for case in cleanup:
            label=case['case']; fixture=get('cases/'+label+'.json');owned=[x for x in roles if x['case']==label]
            r.need(fixture['status']=='passed' and not case['dns']['business'] and not case['dns']['ordinary'], 'Passing control fixture/DNS differs')
            expected_roles=Counter({'overseas':1,'domestic':1,'device-mihomo':1})
            if label!=METHODS[2]:expected_roles.update({'domestic-reconfigured':2,'device-reconfigured-mihomo':2})
            r.need(Counter(x['role'] for x in owned)==expected_roles,'Control role coverage changed')
            for role in owned:
                count=2 if label.endswith('-stopped') and role['role']=='overseas' else 1
                r.need(len(role['launches'])==count, 'Control launch count changed')
            observations=fixture['observations']
            if label==METHODS[2]:
                r.need(observations==[{'kind':'tcp-https-double-hop','client':'mihomo','verified_tls':True,'status':200,
                    'body_sha256':hashlib.sha256(b'VUI-VERIFIED-DOUBLE-HOP').hexdigest(),
                    'deliveries':[['127.0.0.4','127.0.0.3','/experimental-tcp-mihomo']]}], 'TCP sanity wire/delivery changed')
                r.need(not case['rejections'] and all(not t['received'] and not t['expected'] for t in case['targets']), 'TCP control sent UDP')
                continue
            upstream=label.startswith(METHODS[0]);fault=label.rsplit('-',1)[1]
            payloads=[b'negative-target-reachable' if upstream else b'device-negative-target-reachable',
                      b'before-fault-positive' if upstream else b'before-device-fault',
                      b'authorized-recovery' if upstream else b'device-authorized-recovery']
            reasons={'uuid':['unknown uuid'],'ca':['x509','unknown authority'],'sni':['x509','not wrong.example.test'],
                     'stopped':['connect: connection refused']}[fault]
            r.need(len(observations)==3 and [x['kind'] for x in observations]==['echo','rejection','echo'], 'Negative control sequence changed')
            for obs,body in zip((observations[0],observations[2]),payloads[1:]):
                r.need(obs=={'kind':'echo','bytes':len(body),'host':'127.0.0.3','peer':'127.0.0.4'}, 'Negative positive/recovery echo changed')
            rejection=observations[1]
            r.need(rejection['reason']==reasons and all(x in rejection['actual'] for x in reasons), 'Actual rejection reason missing')
            r.need(len(case['rejections'])==1,'Fresh rejection window missing')
            proof=case['rejections'][0]
            r.need(type(proof['starts_at_record_boundary']) is bool, 'Unknown fresh log record boundary')
            fresh=proof['fresh_log']
            if not proof['starts_at_record_boundary']:fresh=fresh.partition('\n')[2]
            # Exactly the frozen fresh_lines() view: preserve raw text, exclude
            # pre-checkpoint partial and final unterminated records, lowercase.
            fresh_lines=fresh.lower().split('\n')[:-1]
            rejected=(b'must-not-deliver-' if upstream else b'bad-device-')+fault.encode()
            r.need(proof['payload']=={'bytes':len(rejected),'sha256':hashlib.sha256(rejected).hexdigest()}
                   and proof['reasons']==reasons and proof['window']==2 and proof['completed']>=proof['started']+2
                   and rejection['actual'] in fresh_lines, 'Fresh full rejection proof differs')
            r.need(len(case['targets'])==1,'Unexpected control target')
            target=case['targets'][0]
            expected=[{'peer':peer,'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()} for peer,body in
                      zip(('127.0.0.1','127.0.0.4','127.0.0.4'),payloads)]
            r.need(target['host']=='127.0.0.3' and target['received']==expected and
                   target['expected']==[{**x,'count':1} for x in expected] and fixture['deliveries']==[{'host':'127.0.0.3','count':3}],
                   'Control target sent unexpected/negative/direct/duplicate payload')

    verdict={'arm':arm,'passed':passed,'udp_capacity_qualified':False,'product_qualified':False}
    manifest={'kind':r.KIND+'-auth-controls','verdict':verdict,
        'files':{n:{'bytes':len(d),'sha256':hashlib.sha256(d).hexdigest()} for n,d in members.items()}}
    if archived is not None:
        safe.validate_bytes('ARCHIVE.json',archived,'json')
        stored=e.read(safe,archived)
        r.need(all(type(x['bytes']) is int for x in stored['files'].values()) and stored==manifest,'Control archive manifest differs')
    return members,verdict

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('runtime','legacy-repository','output'):parser.add_argument('--'+key,type=Path,required=True)
    parser.add_argument('--arm',choices=('stock','candidate'),required=True)
    parser.add_argument('--build-evidence',type=Path)
    args=parser.parse_args();legacy=args.legacy_repository.resolve()
    members,verdict=verify(args.runtime,legacy,args.arm,args.build_evidence)
    safe,_=e.modules(legacy)
    manifest={'kind':r.KIND+'-auth-controls','verdict':verdict,
        'files':{n:{'bytes':len(d),'sha256':hashlib.sha256(d).hexdigest()} for n,d in members.items()}}
    e.write_archive(safe,args.output,[args.runtime,args.build_evidence],members,'ARCHIVE.json',manifest)
    _,again=verify(args.output,legacy,args.arm)
    r.need(again==verdict,'Control archive changed during copy')
    print(json.dumps(verdict))
