"""Recompute fixture outcomes from packets, DNS wire, lifetime logs and cleanup."""
from collections import Counter
from copy import deepcopy
import hashlib
import ipaddress
import re
import struct
import subprocess
import sys
import os
import tempfile
import guard_contract as g
from observe import dns_wire
r=g.r
ANSI=re.compile(r'\x1b\[[0-9;]*m')
PACKET='inbound/vless[fixture-overseas]: [0] inbound packet connection to '
def lines(text,boundary=True):
    if not boundary:text=text.partition('\n')[2]
    return [ANSI.sub('',x) for x in text.split('\n')[:-1]]
def address(data,socks=False):
    if socks:
        typ=data[0];pos=1
        if typ in (1,4):size={1:4,4:16}[typ];host=str(ipaddress.ip_address(data[pos:pos+size]));pos+=size
        else:
            g.need(typ==3,'Wrong SOCKS type');size=data[pos];pos+=1;host=data[pos:pos+size].decode('ascii');pos+=size
        port=struct.unpack('!H',data[pos:pos+2])[0];return host,port,pos+2
    port=struct.unpack('!H',data[:2])[0];typ=data[2];pos=3
    if typ in (1,3):size={1:4,3:16}[typ];host=str(ipaddress.ip_address(data[pos:pos+size]));pos+=size
    else:
        g.need(typ==2,'Wrong XUDP type');size=data[pos];pos+=1;host=data[pos:pos+size].decode('ascii');pos+=size
    return host,port,pos

def request(wire,kind,first):
    if kind=='socks':
        g.need(wire[:3]==b'\0\0\0','Fragmented/malformed SOCKS request');host,port,pos=address(wire[3:],True);return [host,port],wire[3+pos:]
    n=struct.unpack('!H',wire[:2])[0];header=wire[2:2+n]
    g.need(header[:5]==b'\0\0'+bytes((1 if first else 2,1,2)),'Wrong XUDP request header')
    host,port,pos=address(header[5:]);g.need(pos+5==n,'Trailing XUDP address')
    size=struct.unpack('!H',wire[2+n:4+n])[0];body=wire[4+n:];g.need(len(body)==size,'Truncated XUDP request');return [host,port],body

def response(wire,association,first):
    if association['kind']=='socks':
        g.need(wire[:3]==b'\0\0\0','Bad SOCKS response');host,port,pos=address(wire[3:],True);return [host,port],wire[3+pos:]
    pos=0
    if first:g.need(wire[:2]==b'\0\0','Wrong VLESS response');pos=2
    for _ in range(8):
        size=struct.unpack('!H',wire[pos:pos+2])[0];pos+=2;header=wire[pos:pos+size];pos+=size
        g.need(4<=size<=1024 and len(header)==size and header[:3]==b'\0\0\2' and not header[3]&2,'Wrong XUDP response')
        destination=association['initial']
        if len(header)>4:
            g.need(header[4]==2,'Non-UDP XUDP response');host,port,used=address(header[5:]);g.need(used+5==len(header),'Trailing reply address');destination=[host,port]
        if header[3]&1:
            length=struct.unpack('!H',wire[pos:pos+2])[0];pos+=2;body=wire[pos:];g.need(length==len(body),'Truncated reply');return destination,body
    raise ValueError('No reply in bounded frames')

def expected_destination(case,name,targets):
    t=lambda n:[targets[n]['host'],targets[n]['port']]
    if name=='negative' or name in ('ordinary','before','recovery','literal-before','high'):return t('target'),'target'
    if name=='good':return ['good.example.test',targets['target']['port']],'target'
    if name=='partial':return ['failed-aaaa.example.test',targets['target']['port']],'target'
    if name=='both-failed':return ['both-failed.example.test',targets['target']['port']],None
    if name.startswith('dns-'):return [name[4:]+'.example.test',targets['denied']['port']],None
    if name=='literal-private':return t('denied'),None
    if case=='same-port-rebind':return ['same-port-rebind.example.test',targets['target']['port']],None if name=='new' else 'target'
    if name=='first':return t('target'),'target'
    if name=='retarget':return t('denied'),'denied'
    if name.startswith('change-'):
        values=[t(n) for n in ('denied','ingress','upstream','other')]+[['::1',targets['denied']['port']],['::ffff:127.0.0.7',targets['denied']['port']],['metadata-alias.example.test',targets['denied']['port']],['127.0.0.3',53],['127.0.0.3',443]]
        return values[int(name[7:])],'target'
    if name.startswith('port-'):return ['127.0.0.3',int(name[5:])],None
    if name.startswith('literal-'):
        host=g.LITERALS[int(name[8:])];target={'127.0.0.7':'denied','127.0.0.1':'ingress','127.0.0.2':'upstream'}.get(host,'denied')
        return [host,targets[target]['port']],None
    raise ValueError('Unplanned packet '+name)

def normal_dest(value):
    host,port=value
    try:host=str(ipaddress.ip_address(host))
    except ValueError:pass
    return [host,port]

def core_dest(value):
    host,port=value
    try:
        address=ipaddress.ip_address(host);host=str(address.ipv4_mapped or address) if address.version==6 else str(address)
    except ValueError:pass
    return [host,port]

def normalized_target(text):
    host,port=text.rsplit(':',1);host=host.strip('[]')
    try:
        address=ipaddress.ip_address(host);host=str(address.ipv4_mapped or address) if address.version==6 else str(address)
    except ValueError:pass
    return [host,int(port)]

def packet_log_targets(logs):
    result=[]
    for line in logs:
        if PACKET in line:result.append(normalized_target(line.split(PACKET,1)[1]))
    return result

def domestic_route():
    from restricted_udp_preflight_helpers import AddressPolicy
    policy=AddressPolicy._for_tests(allowed_addresses=('127.0.0.3','127.0.0.2','127.0.0.6'),doh=('127.0.0.6',12345,'vpn.example.test'))
    denied=policy.denied_cidrs(('127.0.0.1',),('127.0.0.2',))+['::/0']
    guard={'inbound':['fixture-domestic'],'ip_cidr':denied,'action':'reject'}
    return {'final':'fixture-domestic-direct','default_domain_resolver':'fixture-ordinary-dns','rules':[deepcopy(guard),
        {'inbound':['fixture-domestic'],'action':'resolve','server':'fixture-business-dns','strategy':'prefer_ipv4','disable_cache':True,'disable_optimistic_cache':True},deepcopy(guard),
        {'inbound':['fixture-domestic'],'network':'udp','ip_cidr':['0.0.0.0/0'],'action':'route','outbound':'fixture-upstream','port_range':['1024:65535'],'udp_connect':True,'udp_timeout':'2s'},
        {'inbound':['fixture-domestic'],'network':'tcp','ip_cidr':['0.0.0.0/0'],'action':'route','outbound':'fixture-upstream'},
        {'inbound':['fixture-domestic'],'action':'reject'},
        {'domain_regex':['.+'],'action':'reject'},{'ip_cidr':['127.0.0.0/8'],'invert':True,'action':'reject'}]}

def shape(value,required,optional=()):
    g.need(type(value) is dict and set(required)<=set(value)<=set(required)|set(optional),'Unknown/missing evidence schema fields')

def strict_types(value):
    booleans={'passed','execution_safe','product_qualified','mobile_tun_qualified','idle_qualified','fixture_removed','pid_absent','closed','joined','ready','mihomo','parser_passed','owner_option_absent','owner_recovery_enabled','tls_verified','record_boundary','startup_completed','verified_tls','invert','udp_connect','disable_cache','disable_optimistic_cache'}
    integers={'pid','fd','inode','stream_fd','control_fd','socket_fd','new_threads','dns_pending','https_pending','offset','bytes','write_return','port','count','source_methods','fixture_children','maximum_application_payload'}
    if isinstance(value,dict):
        for key,child in value.items():
            if key in ('binary_sha256','source','process_logs'):continue
            if key in booleans:g.need(type(child) is bool,'Non-boolean evidence flag: '+key)
            if key in integers:g.need(type(child) is int,'Non-integer evidence count: '+key)
            if key in ('started','completed','birth_time'):g.need(type(child) in (int,float),'Non-number observation time')
            strict_types(child)
    elif isinstance(value,list):
        for child in value:strict_types(child)

def schema(row,roles):
    shape(row,('case','scope','status','steps','unexecuted','errors','ports','targets','route_contract','post_identity','associations','closed_associations','failed_constructions','target_ledgers','process_logs','cleanup','dns'),('startup_completed','https','https_target_port','https_after_cleanup'))
    shape(row['cleanup'],('processes','fixture_removed','new_threads','dns_pending','https_pending'))
    shape(row['dns'],('queries','ordinary','wire'))
    for item in row['cleanup']['processes']:shape(item,('pid','returncode','pid_absent'))
    for item in row['target_ledgers']:shape(item,('name','host','port','closed','joined','socket_fd','errors','received','expected'))
    for role in roles:shape(role,('case','role','origin','sha256','version','mihomo','parser_passed','owner_option_absent','owner_recovery_enabled','route_shape','route_sha256','config_sha256','launches','stops','log_file','binary','classification'))
    for item in row['steps']:
        shape(item,('id','kind','started','completed','status'),('error','payload','requested','expected_target','reasons','window','sends','receives','receive_errors','decoded'))
        for sent in item.get('sends',[]):shape(sent,('method','association','wire','wire_hex','destination','started','completed','log_checkpoint','fresh_logs'),('return','errno','raw_log_offsets'))
    strict_types(row);strict_types(roles)

def verify(row,roles,inputs,export,safe,*,safety_only=False):
    schema(row,roles)
    case=row['case'];g.need(row['scope']==g.scope(),'Wrong case scope');plan=g.plan(case);steps=row['steps'];targets=row.get('targets',{})
    g.need([(x['id'],x['kind']) for x in steps]==plan[:len(steps)] and len(steps)<=len(plan),'Steps not exact planned prefix')
    g.need(row['unexecuted']==[name for name,_ in plan[len(steps):]],'Unexecuted steps differ')
    g.need(all(x['status']=='passed' for x in steps[:-1]) and all(x['completed']>=x['started'] for x in steps),'Continued after failed prerequisite')
    complete=len(steps)==len(plan) and all(x['status']=='passed' for x in steps) and row['status']=='completed' and not row['errors']
    g.need(row['post_identity']=={'source':inputs['source'],'binary_sha256':{k:v['sha256'] for k,v in inputs['binaries'].items()}},'Missing/changed post identity')
    known=inputs['binaries'];g.need(set(known)=={'official','mihomo','candidate'} and known['official']['sha256']==r.STOCK and known['official']['version']=='1.14.2' and known['mihomo']['sha256']==r.MIHOMO and known['mihomo']['version']=='1.19.32','Wrong stock identities')
    g.need(known['candidate']['build_source_identity']==inputs['source'] and known['candidate']['version']==g.build.version(inputs['source']),'Wrong reduced identity')
    g.need(row['route_contract']=={'domestic':domestic_route(),'overseas':{'final':'fixture-overseas-egress','rules':[{'domain_regex':['.+'],'action':'reject'},{'ip_cidr':['127.0.0.0/8'],'invert':True,'action':'reject'}]},'udp_timeout':'2s'},'Frozen routing/containment contract changed')
    ports=row['ports'];associations=row['associations'];closed=row['closed_associations']
    g.need(set(ports)=={'ingress','client','upstream','doh','ordinary_dns'} and all(type(x) is int and 1024<=x<=65535 for x in ports.values()),'Invalid fixture ports')
    hosts={'target':'127.0.0.3','denied':'127.0.0.7','ingress':'127.0.0.1','upstream':'127.0.0.2','other':'127.0.0.3'}
    names={'target'}|{name[7:] for name,kind in plan if kind=='direct'}
    g.need(set(targets)==names and all(t['host']==hosts[n] and type(t['port']) is int and 1024<=t['port']<=65535 and t['port']!=ports['upstream'] for n,t in targets.items()),'Target identity/port changed')
    g.need(all(type(x['id']) is int for x in associations+closed),'Invalid association ID type')
    g.need([a['id'] for a in associations]==list(range(len(associations))) and Counter(x['id'] for x in closed)==Counter(range(len(associations))),'Missing/duplicate FD identity')
    for association,proof in zip(associations,sorted(closed,key=lambda x:x['id'])):
        g.need(proof['stream_fd']==-1 and association['stream']['fd']>=0 and association['stream']['inode']>0,'Application FD not closed')
        if association['kind']=='socks':
            g.need(proof['control_fd']==-1 and association['control']['fd']>=0 and association['control']['inode']>0 and association['control']['peer']==['127.0.0.1',ports['client']] and r.tuple_ok(association['relay'],'127.0.0.1'),'Wrong SOCKS association')
        else:g.need(association['kind']=='raw' and association['tls_verified'] is True and association['server_name']=='vpn.example.test' and association['stream']['peer']==['127.0.0.1',ports['ingress']],'Unverified raw TLS association')
    g.need(all(x['stream_fd']==-1 and x.get('control_fd',-1)==-1 for x in row.get('failed_constructions',[])), 'Failed construction leaked sockets')
    g.need(row['cleanup']['fixture_removed'] and row['cleanup']['new_threads']==0 and row['cleanup']['dns_pending']==row['cleanup']['https_pending']==0,'Unjoined fixture resource')
    cleanup=row['cleanup']['processes']
    g.need(all(x['returncode']==0 and x['pid_absent'] for x in cleanup),'Unreaped process')
    g.need(Counter(x['pid'] for x in cleanup)==Counter(x['launches'][-1]['pid'] for x in roles),'Process cleanup identity differs')
    pids=[x['pid'] for role in roles for x in role['launches']]
    g.need(len(pids)==len(set(pids)),'Reused/duplicated process PID')
    for role in roles:
        name=role['role'];g.need(name in g.ROLES and role['case']==case,'Unknown role')
        origin=g.ROLES[name];g.need(role['origin']==origin and role['mihomo'] is name.startswith('device-') and role['sha256']==known[origin]['sha256'] and role['version']==known[origin]['version'] and role['parser_passed'] and role['owner_option_absent'] and not role['owner_recovery_enabled'],'Role boundary changed')
        g.need(Counter(x['pid'] for x in role['launches'])==Counter(x['pid'] for x in role['stops']) and all(x['ready'] and x['birth_time']>0 and x['sha256']==known[origin]['sha256'] for x in role['launches']) and all(x['returncode']==0 and x['pid_absent'] for x in role['stops']),'Role restart not reaped')
        g.need(role['route_sha256']==hashlib.sha256(r.canonical(role['route_shape'])).hexdigest(),'Actual route digest differs')
        if name=='overseas':g.need(role['route_shape']=={'final':'fixture-overseas-egress','rules':[{'domain_regex':['.+'],'action':'reject'},{'ip_cidr':['127.0.0.0/8'],'invert':True,'action':'reject'}]},'Overseas containment differs')
        elif not name.startswith('device-'):
            expected={'final':'fixture-upstream'} if case=='naked-retarget' and name=='domestic-reconfigured' else row['route_contract']['domestic']
            g.need(role['route_shape']==expected,'Domestic role route changed')
    g.need(set(row['process_logs'])=={x['log_file'] for x in roles},'Missing/extra process lifetime log')
    expected_roles=Counter({'overseas':1,'domestic':1,'device-mihomo':1})
    for name in ('fault','restore','naked-restart'):
        if any(x['id']==name and x['status']=='passed' for x in steps):expected_roles.update({'domestic-reconfigured':1,'device-reconfigured-mihomo':1})
    if complete:g.need(Counter(x['role'] for x in roles)==expected_roles,'Role count differs')
    for role in roles:
        expected_launch=2 if case=='upstream-stopped' and role['role']=='overseas' and any(x['id']=='restore' and x['status']=='passed' for x in steps) else 1
        g.need(len(role['launches'])==expected_launch,'Unexpected core relaunch')
    if associations:
        ordinary_roles=[x for x in roles if x['role']=='device-mihomo']
        socks=[a for a in associations if a['kind']=='socks']
        r.need(socks,'Ordinary precondition not observed')
        g.safe_archive.export_check(safe,export,ports['ingress'],{'application_associations':[{'control':socks[0]['control']}]},ordinary_roles)
        # Every reconfigured device consumes the same node except the one specified fault.
        with tempfile.TemporaryDirectory(prefix='guard-export-check-') as temporary:
            call=subprocess.run([sys.executable,str(r.HERE/'export_node.py'),'--port',str(ports['ingress'])],cwd=r.ROOT,env={**os.environ,'VUI_DATA_DIR':temporary},capture_output=True,text=True,timeout=20,check=True)
        artifact=safe.strict_json(call.stdout);node=artifact['node']
        for i,role in enumerate(x for x in roles if x['role'].startswith('device-')):
            client={'mixed-port':ports['client'],'bind-address':'127.0.0.1','allow-lan':False,'mode':'rule','log-level':'debug','ipv6':False,'find-process-mode':'off','geo-auto-update':False,'profile':{'store-selected':False,'store-fake-ip':False},'proxies':[deepcopy(node)],'rules':['MATCH,'+node['name']]}
            if i==1 and case in ('device-uuid','device-sni'):client['proxies'][0]['uuid' if case.endswith('uuid') else 'servername']='33333333-3333-4333-8333-333333333333' if case.endswith('uuid') else 'wrong.example.test'
            g.need(role['config_sha256']==hashlib.sha256(r.canonical(client)).hexdigest(),'Reconfigured ordinary node changed')
    for ledger in row['target_ledgers']:
        g.need(ledger['closed'] and ledger['joined'] and ledger['socket_fd']==-1,'Unclosed target')
    if safety_only:return True
    # Strict lifetime target allowlist comes from planned sends, not target.expected.
    allowed={name:Counter() for name in targets};required={name:Counter() for name in targets};used=Counter();seen_replies=Counter();packet_expected=[]
    for step in steps:
        name,kind=step['id'],step['kind'];passed=step['status']=='passed'
        if kind=='direct':
            target=name.removeprefix('direct-');g.need(step['payload']==g.ident(g.payload(name)) and step['expected_target']==target,'Direct control changed')
            key=('127.0.0.1',len(g.payload(name)),g.ident(g.payload(name))['sha256']);allowed[target][key]+=1
            if passed:required[target][key]+=1
            continue
        if kind not in ('echo','reject','raw-echo','raw-reject'):continue
        dest,target=expected_destination(case,name,targets)
        if kind in ('reject','raw-reject'):target=None
        g.need(step['requested']==dest and step['expected_target']==target and step['payload']==g.ident(g.payload(name)),'Packet spec/target changed')
        sends=step.get('sends',[])
        if not sends:g.need(not passed,'Passed packet has no send');continue
        g.need(len(sends)==1,'Packet replay/missing send');sent=sends[0];a=associations[sent['association']];wire=bytes.fromhex(sent['wire_hex'])
        g.need(sent['wire']==g.ident(wire) and sent['started']>=step['started'] and sent['completed']>=sent['started'],'Bad send identity/time')
        wanted='raw' if kind.startswith('raw-') else 'socks';g.need(a['kind']==wanted,'Protocol observer changed')
        actual,body=request(wire,wanted,used[a['id']]==0);g.need(normal_dest(actual)==normal_dest(dest) and body==g.payload(name),'Actual sent tuple/payload differs')
        used[a['id']]+=1
        g.need(sent.get('return')==(len(wire) if wanted=='socks' else None) and 'errno' not in sent,'Send not complete')
        if wanted=='socks':g.need(sent['destination']==a['relay'],'SOCKS relay changed')
        g.need(set(sent['log_checkpoint'])==set(sent['fresh_logs'])=={'domestic','upstream','client'},'Missing/extra log checkpoint')
        stage=2 if name=='recovery' and case.startswith(('upstream-','device-')) else 1 if (name=='negative' and case.startswith(('upstream-','device-'))) or (case=='naked-retarget' and name in ('first','retarget')) else 0
        current={'upstream':[x for x in roles if x['role']=='overseas'][0], 'domestic':[x for x in roles if x['role'].startswith('domestic')][stage], 'client':[x for x in roles if x['role'].startswith('device-')][stage]}
        for role,checkpoint in sent['log_checkpoint'].items():
            fresh=sent['fresh_logs'][role];boundary=not checkpoint['text'] or checkpoint['text'].endswith('\n')
            g.need(type(checkpoint['offset']) is int and checkpoint['offset']==len(checkpoint['text']) and checkpoint['record_boundary'] is boundary and fresh['record_boundary'] is boundary and fresh['path']==checkpoint['path']==current[role]['log_file'] and fresh['offset']==checkpoint['offset'] and row['process_logs'][fresh['path']].startswith(checkpoint['text']+fresh['text']),'Fresh log not actual role/lifetime slice')
        replies=step.get('receives',[])
        for event in replies:g.need(event['association']==a['id'] and event['wire']==g.ident(bytes.fromhex(event['wire_hex'])) and event['completed']>=sent['completed'],'Raw recv identity differs')
        if target is not None:
            key=('127.0.0.4',len(body),hashlib.sha256(body).hexdigest());allowed[target][key]+=1
            if passed:
                required[target][key]+=1;g.need(replies and (wanted=='raw' or len(replies)==1),'Positive has no/extra reply')
                if wanted=='socks':g.need(replies[0]['peer']==a['relay'],'Reply relay changed')
                answer,payload=response(b''.join(bytes.fromhex(x['wire_hex']) for x in replies),a,seen_replies[a['id']]==0);seen_replies[a['id']]+=1
                possibilities=[[targets[target]['host'],targets[target]['port']]]
                if wanted=='raw' and a['initial'][1]==targets[target]['port']:possibilities.append(a['initial'])
                g.need(answer in possibilities and payload==body,'Actual reply tuple/payload mismatch')
                if wanted=='raw':g.need(step['decoded']=={'host':answer[0],'port':answer[1],'payload':g.ident(body)},'Decoded/raw reply mismatch')
            if used[a['id']]==1:packet_expected.append([targets[target]['host'],targets[target]['port']])
        elif passed:
            g.need(step['reasons']==g.reasons(case,name) and step['window']==2 and step['completed']>=sent['completed']+2,'Short/replaced rejection window')
            if wanted=='socks':g.need(not replies,'Rejected SOCKS returned data')
            else:
                # Reuse the unchanged bounded empty-control-only parser.
                import restricted_udp_preflight_helpers as frozen
                frozen.assert_xudp_rejection_response(b''.join(bytes.fromhex(x['wire_hex']) for x in replies))
            observed='upstream' if case=='upstream-uuid' else 'client' if case in ('device-ca','device-sni') else 'domestic'
            fresh=sent['fresh_logs'][observed];fresh_lines=lines(fresh['text'],fresh['record_boundary'])
            g.need(any(all(word in line.lower() for word in step['reasons']) for line in fresh_lines),'Fresh actual rejection absent')
            if not case.startswith(('upstream-','device-')) and name!='both-failed':
                domestic=sent['fresh_logs']['domestic'];dom_lines=lines(domestic['text'],domestic['record_boundary']);connections=[]
                for line in dom_lines:
                    marker='inbound/vless[fixture-domestic]: [0] inbound packet connection to '
                    if marker in line and normalized_target(line.split(marker,1)[1])==core_dest(dest):
                        match=re.search(r'\[(\d+) [^]]+\]',line);g.need(match,'Missing connection ID');connections.append(match.group(1))
                index=g.guard_index(name)
                g.need(len(connections)==1 and any('['+connections[0]+' ' in line and 'router: match['+str(index)+']' in line and '=> reject' in line for line in dom_lines),'Original guard/connection/destination not observed')
            overseas=sent['fresh_logs']['upstream'];g.need(not packet_log_targets(lines(overseas['text'],overseas['record_boundary'])),'Rejected packet reached overseas')
    if complete:g.need(set(used)==set(range(len(associations))),'Unused or missing application association')
    if case in ('guarded-retarget','naked-retarget','same-port-rebind'):
        shared=[s['sends'][0]['association'] for s in steps if s['kind']=='raw-echo' and s.get('sends')]
        g.need(len(set(shared))<=1,'Bound target probe replaced original association')
    # Lifetime audit includes late data after a rejected window or a subsequent positive.
    for ledger in row['target_ledgers']:
        name=ledger['name'];g.need(name in targets and ledger['host']==targets[name]['host'] and ledger['port']==targets[name]['port'],'Target binding changed')
        g.need(ledger['closed'] and ledger['joined'] and ledger['socket_fd']==-1 and not ledger['errors'],'Target not disposed')
        actual=Counter((x['peer'],x['bytes'],x['sha256']) for x in ledger['received'])
        g.need(not(actual-allowed[name]) and not(required[name]-actual),'Missing/late/duplicate/wrong-source target')
        if complete:g.need(actual==allowed[name],'Incomplete target lifetime')
    g.need(Counter(x['name'] for x in row['target_ledgers'])==Counter(targets.keys()),'Missing target ledger')
    overseas_logs=[text for name,text in row['process_logs'].items() if name.endswith('-overseas.log')]
    actual_requests=[dest for text in overseas_logs for dest in packet_log_targets(lines(text))]
    g.need(not(Counter(tuple(x) for x in actual_requests)-Counter(tuple(x) for x in packet_expected)),'Late/extra overseas packet request')
    if complete:g.need(actual_requests==packet_expected,'Missing/out-of-order overseas packet request')
    if any(x['id']=='ordinary' and x['status']=='passed' for x in steps):g.need([targets['target']['host'],targets['target']['port']] in actual_requests,'Ordinary positive did not validate overseas log observer')
    for name,text in row['process_logs'].items():
        if 'domestic' in name:g.need('outbound/direct[fixture-domestic-direct]' not in text.lower(),'Domestic DIRECT observed')
    dns=row['dns'];g.need(not dns['ordinary'],'Ordinary DNS fallback')
    expected=[]
    for host,values,failed in g.expected_dns(case):
        for typ in (1,28):expected.append((host,typ,2 if typ in failed else 0,tuple(v for v in values if ipaddress.ip_address(v).version=={1:4,28:6}[typ] and typ not in failed)))
    actual_dns=[]
    for item in dns['wire']:
        q=dns_wire(bytes.fromhex(item['query_hex']));answer=dns_wire(bytes.fromhex(item['response_hex']))
        g.need(q==item['query'] and answer==item['response'] and q['id']==answer['id'] and q['name']==answer['name'] and q['type']==answer['type'] and q['flags']==0x100 and answer['flags'] in (0x8180,0x8182) and item['peer']=='127.0.0.4' and item['write_return']==len(bytes.fromhex(item['response_hex'])) and 'error' not in item,'DNS actual wire/source/send differs')
        actual_dns.append((q['name'],q['type'],answer['rcode'],tuple(answer['answers'])))
    g.need(not(Counter(actual_dns)-Counter(expected)),'Extra/incorrect DNS answer')
    g.need(Counter(tuple(x) for x in dns['queries'])==Counter((x['peer'],x['query']['name'],x['query']['type']) for x in dns['wire']),'Query vs wire DNS ledger differs')
    if complete:g.need(Counter(actual_dns)==Counter(expected),'Missing DNS answers')
    dns_steps={'dns-all':['good']+['dns-'+x for x,_ in g.ANSWERS],'dns-partial':['partial','both-failed'],'same-port-rebind':['first','new']}.get(case,[])
    for step in steps:
        selected=[x for x in dns['wire'] if step['started']<=x['started']<=x['completed']<=step['completed']]
        if step['status']=='passed':g.need(sorted(x['query']['type'] for x in selected)==([1,28] if step['id'] in dns_steps else []),'DNS duplicate/relookup/wrong phase')
    g.need(all(sum(s['started']<=x['started']<=x['completed']<=s['completed'] for s in steps)==1 for x in dns['wire']),'Late/unbound DNS reply')
    if case=='tcp' and complete:
        g.need(row['https']=={'verified_tls':True,'status':200,'body':g.ident(b'VUI-VERIFIED-DOUBLE-HOP'),'deliveries':[['127.0.0.4','127.0.0.3','/guard-tcp']]} and row['https_after_cleanup']==row['https']['deliveries'],'TCP TLS/target differs')
    return {'case':case,'execution_safe':True,'passed':complete,'started_steps':len(steps),'unexecuted':row['unexecuted'],'product_qualified':False}
