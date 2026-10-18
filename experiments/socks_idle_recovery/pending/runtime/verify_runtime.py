"""Additional strict owner evidence. Never changes original business or safety assertions."""
from collections import Counter
import ipaddress
import json
from pathlib import Path
import re
import runtime_adapter as a

MARKER = 'socks_udp_owner '
FIELDS = {'pid','owner_id','sequence','transition','generation','packet_sequence','accepted_packets',
          'rejected_sources','remapped_packets','cause','source','relay','target'}
COUNTERS = {'pid','owner_id','sequence','generation','packet_sequence','accepted_packets','rejected_sources','remapped_packets'}
TRANSITIONS = {'create','admitting','admitted','retire','join','dormant','closing','closed'}
CAUSES = {'none','session_end','outcome_unknown','pending_at_end','lifecycle','oversize','overload',
          'admission_timeout','idle_timeout','malformed','unsupported','target','canceled','eof','closed','deadline','io_error'}
LOG_FILES = {'overseas':'001-overseas.log','domestic':'002-domestic.log','device-singbox':'003-device-singbox.log'}
need = a.need

def strict_json(text):
    def pairs(items):
        result={}
        for k,v in items:
            need(k not in result,'Duplicate owner JSON key'); result[k]=v
        return result
    def invalid(value): raise ValueError('Nonfinite JSON number')
    return json.loads(text,object_pairs_hook=pairs,parse_constant=invalid)

def endpoint(value, *, unset=False):
    need(isinstance(value,str),'Nontext owner endpoint')
    if unset and value==':0': return value
    host,sep,port=value.rpartition(':')
    need(sep and port.isascii() and port.isdigit() and str(int(port))==port and 0 <= int(port) <=65535,'Invalid owner port')
    addr=ipaddress.ip_address(host)
    need(addr.version==4 and str(addr)==host,'Noncanonical IPv4 owner address')
    return value

def parse_events(text, pid):
    events=[]
    for line in text.splitlines(keepends=True):
        if MARKER not in line: continue
        need(line.endswith('\n') and len(line.encode())<=4096 and line.count(MARKER)==1,'Truncated/unbounded owner event')
        data=strict_json(line.split(MARKER,1)[1].strip())
        need(isinstance(data,dict) and set(data)==FIELDS,'Unknown/missing typed event fields')
        need(all(type(data[k]) is int and 0<=data[k]<2**64 for k in COUNTERS),'Invalid uint64 owner counter')
        need(data['pid']==pid and pid>0 and data['owner_id']>0 and data['sequence']>0,'Wrong owner role PID/ID')
        need(data['transition'] in TRANSITIONS and data['cause'] in CAUSES,'Unknown typed lifecycle value')
        endpoint(data['source']); endpoint(data['relay']); endpoint(data['target'],unset=data['transition']=='create')
        events.append(data); need(len(events)<=128,'Unbounded event ledger')
    return events

def role_texts(text):
    parts=re.split(r'(?m)^(001-overseas\.log|002-domestic\.log|003-device-singbox\.log):\n',text)
    need(parts[0]=='','Unknown fixture log prefix')
    names=list(LOG_FILES.values()); latest={}
    need((len(parts)-1)%6==0,'Incomplete fixture log snapshot')
    for n in range(1,len(parts),6):
        need(parts[n:n+6:2]==names,'Wrong fixture log role order')
        for name,body in zip(parts[n:n+6:2],parts[n+1:n+6:2]):
            need(name not in latest or body.startswith(latest[name]),'Earlier fixture log was replaced/truncated')
            latest[name]=body
    need(set(latest)==set(names),'Missing role logs')
    return {role:latest[name] for role,name in LOG_FILES.items()}

def verify_roles(case, roles, binaries, arm):
    need(Counter(r['role'] for r in roles)==Counter(case['roles'].keys()),'Missing/duplicate role ledger')
    for row in roles:
        role=row['role']; origin=case['roles'][role]; binary=binaries[origin]
        enabled=arm=='on' and role=='device-singbox'
        need(type(row.get('owner_recovery_enabled')) is bool and row['owner_recovery_enabled'] is enabled and
             row.get('owner_recovery_outbound')==(a.TAG if enabled else None),'Role mode/tag mismatch')
        need(row['case']==case['id'] and row['origin']==origin and row['sha256']==binary['sha256'] and
             row['binary']==binary['path'] and row['version']==binary['version'] and
             row['log_file']==LOG_FILES[role] and re.fullmatch('[0-9a-f]{64}',row['config_sha256']), 'Role source/config identity mismatch')
        need(len(row['launches'])==1 and row['launches'][0]['ready'] is True and row['launches'][0]['sha256']==row['sha256']
             and type(row['launches'][0]['pid']) is int and row['launches'][0]['pid']>0,'Wrong role launch')
        if enabled: need(origin=='candidate' and 'vui-socks-owner-experiment-' in row['version'],'Owner-on stock/non-owner binary')
    return True

def verify_owner(events, *, pid, owner_id, source, relay, target, packets, generations):
    need(events,'Missing owner events')
    rows=sorted(events,key=lambda r:r['sequence'])
    need([r['sequence'] for r in rows]==list(range(1,len(rows)+1)),'Duplicate/missing owner sequence')
    need(all(r['pid']==pid and r['owner_id']==owner_id for r in rows),'Mixed owner identity')
    create=rows[0]
    need(create['transition']=='create' and create['generation']==0 and create['cause']=='none' and
         create['source']==source.rsplit(':',1)[0]+':0' and create['target']==':0' and
         create['accepted_packets']==create['packet_sequence']==0,'Not original initial owner')
    previous={k:0 for k in ('generation','packet_sequence','accepted_packets','rejected_sources','remapped_packets')}
    for row in rows:
        need(row['relay']==relay and (row is create or (row['source']==source and row['target']==target)),'Owner tuple/target rebound')
        need(row['accepted_packets']==row['packet_sequence']<=packets and row['rejected_sources']==row['remapped_packets']==0,'Packet replay/synthetic/changed target evidence')
        need(all(row[k]>=v for k,v in previous.items()),'Owner counters decreased')
        previous={k:row[k] for k in previous}
    need([(r['transition'],r['cause']) for r in rows[-2:]]==[('closing','eof'),('closed','eof')] and
         all(r['generation']==generations and r['accepted_packets']==packets for r in rows[-2:]),'Missing terminal EOF/full-join event')
    middle=rows[1:-2]; index=0
    for gen in range(1,generations+1):
        need(len(middle)>=index+2,'Missing generation admission')
        pair=middle[index:index+2]; index+=2
        need([(r['transition'],r['cause']) for r in pair]==[('admitting','none'),('admitted','none')] and
             all(r['generation']==gen and r['accepted_packets']==gen for r in pair),'Unexpected admission/new packet sequence')
        retired=index<len(middle) and middle[index]['transition']=='retire'
        if gen<generations: need(retired,'Next generation before old join')
        if retired:
            suffix=middle[index:index+3]; index+=3
            need([(r['transition'],r['cause']) for r in suffix]==[('retire','session_end'),('join','session_end'),('dormant','none')] and
                 all(r['generation']==gen for r in suffix),'Noncanonical retire/join/dormant')
            if gen<generations: need(all(r['accepted_packets']==gen for r in suffix),'Accepted packet crossed old End')
    need(index==len(middle),'Unexpected owner transition or extra generation')
    return {'owner_id':owner_id,'generations':generations,'new_application_packets':packets,'final_closed_joined':True}

def verify_owner_case(case,result,roles,text,arm):
    logs=role_texts(text); events={}
    for row in roles:
        role=row['role']; events[role]=parse_events(logs[role],row['launches'][0]['pid'])
        if role!='device-singbox' or arm=='off': need(events[role]==[],'Owner event on disabled role')
    if arm=='off': return {'owner_enabled':False}
    rows=events['device-singbox']; device=next(r for r in roles if r['role']=='device-singbox'); pid=device['launches'][0]['pid']
    owners=sorted({r['owner_id'] for r in rows})
    need(owners==([1,2] if case['mode']=='close' else [1]),'Owner recreation/extra association')
    packets=[r for r in result['packets'] if r['direction']!='local_emsgsize']
    need(all(r['send_state']=='sent' and r['full_delivery_passed'] is True for r in packets),'Owner proof without full original business')
    output=[]
    for index,owner_id in enumerate(owners):
        selected=[packets[index]] if case['mode']=='close' else packets
        def addr(key):
            values={tuple(r[key]) for r in selected}; need(len(values)==1,'Original datagram tuples differ')
            host,port=next(iter(values)); return f'{host}:{port}'
        output.append(verify_owner([r for r in rows if r['owner_id']==owner_id],pid=pid,owner_id=owner_id,
                                  source=addr('application_socket'),relay=addr('socks_relay'),target=addr('requested_target'),
                                  packets=len(selected),generations=2 if case['mode']=='idle' else 1))
    return {'owner_enabled':True,'owners':output,'device_whole_window_continuity_proven':False}

def verify_private_transport_samples(rt,data,role,lifecycle):
    proofs=[e['proof'] for e in lifecycle['events'] if e['kind']=='large_endpoint_proof']
    need(len(proofs)==1,'Missing original lifecycle proof')
    ingress=proofs[0]['ingress_port']; samples={s['phase']:s for s in data['samples'] if s['phase']!='release_window'}
    selected={}
    for phase in ('after_large_business','before_recover','after_recover'):
        rows,inodes,stable=rt.socks.device_rows(samples[phase],role)
        need(stable,'Private transport sample is inconclusive')
        tcp=[r for r in rows if r['protocol']=='tcp' and r['family']=='ipv4' and r['remote']=={'host':'127.0.0.1','port':ingress}]
        need(len(tcp)==(0 if phase=='before_recover' else 1),'Private transport presence differs')
        if tcp: need(tcp[0]['proc_state']=='01' and tcp[0]['local']['host']=='127.0.0.1','Private TCP tuple/state differs')
        selected[phase]=(tcp,inodes)
    old=selected['after_large_business'][0][0]; new=selected['after_recover'][0][0]
    need(old['inode'] not in selected['before_recover'][1] and new['inode']!=old['inode'],'Old private transport reused')
    return {'old_inode':old['inode'],'new_inode':new['inode'],'old_local':old['local'],'new_local':new['local'],
            'sampled_private_transport_replacement':True,'device_whole_window_continuity_proven':False}

def verify_same_owner_samples(rt,data,case,result,roles,source,lifecycle):
    observed=rt.socks.verify_case(data,case,result,roles,source,lifecycle)
    need(observed['business_recovery_passed'] is True,'Original business recovery failed')
    role=next(r for r in roles if r['role']=='device-singbox')
    transport=verify_private_transport_samples(rt,data,role,lifecycle)
    if case['mode']=='close':
        initial,recovery=data['associations']
        need(all(initial[k]['inode']!=recovery[k]['inode'] for k in ('control','application')),'Explicit close reused local sockets')
    else:
        need(observed['first_eof_completed'] is None,'Original control observed EOF')
        warm=data['samples'][0]; rows,_,stable=rt.socks.device_rows(warm,role); need(stable,'Warm identity inconclusive')
        original={r['inode']:r for r in rows if r['inode'] in (observed['initial_control_inode'],observed['initial_relay_inode'])}
        need(len(original)==2,'Missing warm original sockets')
        for sample in data['samples']:
            if sample['phase']=='after_local_cleanup': continue
            need(sample['associations'][0]['peek']['state']=='would_block','Original control not PEEK/EAGAIN')
            rows,_,stable=rt.socks.device_rows(sample,role)
            if sample['phase']=='before_recover': need(stable,'Before-recover identity inconclusive')
            if stable: need({r['inode']:r for r in rows if r['inode'] in original}==original,'Original owner sockets absent/rebound in stable sample')
    return {**observed,'private_transport':transport,'typed_owner_required_separately':True}

def verify_stage(rt,directory,stage,report,arm,binaries):
    p=rt.p; directory=Path(directory); roles=p.json_read(directory/'process-binaries.json'); source=p.json_read(directory/'input-identity.json')['source_identity']
    manifest=p.json_read(directory/'manifest.json')['files']; cases=p.plan(stage); results=report['cases']; output=[]
    p.verify_leaf_set([c['id'] for c in cases],[r['case'] for r in results],'owner case')
    for case,result in zip(cases,results):
        need(result['case']==case['id'],'Reordered owner cases')
        owned=[r for r in roles if r['case']==case['id']]; verify_roles(case,owned,binaries,arm)
        name='frozen-fixture/'+case['id']+'.log'; need(p.sha(directory/name)==manifest.get(name),'Typed owner log hash differs')
        item=verify_owner_case(case,result,owned,(directory/name).read_text(),arm)
        if arm=='on' and stage=='D':
            item['samples']=verify_same_owner_samples(rt,p.json_read(directory/case['id']/'socks-association-observation.json'),
                       case,result,roles,source,p.json_read(directory/case['id']/'lifecycle-observation.json'))
        output.append({'case':case['id'],**item})
    if arm=='on':
        assessed=p.assess(cases,results,roles,binaries)
        need(assessed['diagnostic_complete'] is True and assessed['stage_capacity_passed'] is True and
             report['diagnostic_complete'] is True and report['stage_capacity_passed'] is True,'Original strict owner-on gate failed')
    return {'verified':True,'arm':arm,'cases':output,'product_qualified':False,'device_whole_window_continuity_proven':False}

def verify_off_idle_red(rt,directory,report):
    p=rt.p; directory=Path(directory); cases=p.plan('D'); roles=p.json_read(directory/'process-binaries.json')
    observed=rt.socks.verify_stage(directory,'D',report); bycase={r['case']:r for r in observed['cases']}
    need(report['diagnostic_complete'] is False and report['stage_capacity_passed'] is False,'Historical D RED promoted')
    for case,result in zip(cases,report['cases']):
        need(result['case']==case['id'],'Historical D cases reordered')
        ledger={r['leaf']:r for r in result['target_ledger']['packets']}
        for spec,row in zip(case['packets'],result['packets']):
            if row['full_delivery_passed']: p.verify_packet(case,spec,row,ledger.get(row['leaf']),row['first_udp'])
        if case['mode']=='close':
            need(result['status']=='observed' and bycase[case['id']]['business_recovery_passed'] is True,'Historical close failed')
            p.verify_lifecycle(case,result,[r for r in roles if r['case']==case['id']])
        else:
            need(result['status']=='error' and len(result['packets'])==2 and len(ledger)==1 and
                 bycase[case['id']]['association_state_before_recovery']=='peer_eof_and_device_control_relay_absent','Historical idle failure class changed')
            row=result['packets'][1]; window=row['target_window']; request,reply=p.packet_bodies(row['leaf'],case['packets'][1])
            need(row['status']=='error' and row['send_state']=='sent' and row['full_delivery_passed'] is False and
                 row['error']['type']=='TimeoutError' and row['request']==p.ident(request) and row['expected_reply']==p.ident(reply) and
                 window['status']=='zero_observed' and window['deadline']==window['started']+4 and
                 window['completed']>=window['deadline'] and row['terminal_monotonic']>=window['deadline'] and
                 row['reply_window']=={'status':'not_started'},'Historical single-send/zero-delivery timeout changed')
    return {'historical_failure_preserved':True,'explicit_close_passed':3,'idle_failed':3,'product_qualified':False}
