"""Synthetic real-shape evidence. No socket, core or semantic-verifier mock."""
from collections import Counter
from copy import deepcopy
import struct
import guard_contract as g
import verify_case as v
from observe import dns_wire


def make(case,safe):
    import export_node
    import restricted_udp_preflight_helpers as frozen
    from loopback_helpers import _socks_udp_target
    source={'product':{'commit':'a'*40},'legacy':{'commit':g.r.LEGACY}}
    known={'official':{'sha256':g.r.STOCK,'version':'1.14.2'},'mihomo':{'sha256':g.r.MIHOMO,'version':'1.19.32'},'candidate':{'sha256':'b'*64,'version':g.build.version(source),'build_source_identity':source}}
    inputs={'scope':g.scope(),'source':source,'binaries':known}
    ports={'ingress':22001,'client':22002,'upstream':22003,'doh':22004,'ordinary_dns':22005}
    names={'target':'127.0.0.3'}
    for name,kind in g.plan(case):
        if kind=='direct':names[name[7:]]={'target':'127.0.0.3','denied':'127.0.0.7','ingress':'127.0.0.1','upstream':'127.0.0.2','other':'127.0.0.3'}[name[7:]]
    targets={name:{'host':host,'port':23001+i} for i,(name,host) in enumerate(names.items())}
    if case=='same-port-rebind':targets['denied']['port']=targets['target']['port']
    artifact=export_node.export(ports['ingress']);digest=lambda x:g.build.digest(x)
    export={'artifact':safe.h.c.life.scrub(artifact),'artifact_sha256':digest(artifact),'node_sha256':digest(artifact['node']),'consumer_node_sha256':digest(artifact['node']),'node_fields_unchanged':True,'consumer':'ordinary-yaml-node-in-listener-shell','server_configuration':'isolated-research-guarded-not-product-udp','http_api_export_executed_here':False,'hash_scope':'raw-fake-fixture-export-before-redaction'}
    def config(i):
        node=deepcopy(artifact['node'])
        if i==1 and case in ('device-uuid','device-sni'):node['uuid' if case.endswith('uuid') else 'servername']='33333333-3333-4333-8333-333333333333' if case.endswith('uuid') else 'wrong.example.test'
        return {'mixed-port':ports['client'],'bind-address':'127.0.0.1','allow-lan':False,'mode':'rule','log-level':'debug','ipv6':False,'find-process-mode':'off','geo-auto-update':False,'profile':{'store-selected':False,'store-fake-ip':False},'proxies':[node],'rules':['MATCH,'+node['name']]}
    route=v.domestic_route();overseas={'final':'fixture-overseas-egress','rules':[{'domain_regex':['.+'],'action':'reject'},{'ip_cidr':['127.0.0.0/8'],'invert':True,'action':'reject'}]}
    labels=['overseas','domestic','device-mihomo']
    if case.startswith(('upstream-','device-')):labels+=['domestic-reconfigured','device-reconfigured-mihomo']*2
    if case=='naked-retarget':labels+=['domestic-reconfigured','device-reconfigured-mihomo']
    roles=[];device=0
    for i,name in enumerate(labels):
        origin=g.ROLES[name];pid=100+i*3;launches=[{'pid':pid,'ready':True,'birth_time':1000+pid,'sha256':known[origin]['sha256']}]
        if case=='upstream-stopped' and name=='overseas':launches.append({'pid':pid+1,'ready':True,'birth_time':1001+pid,'sha256':known[origin]['sha256']})
        shape=None if name.startswith('device-') else overseas if name=='overseas' else {'final':'fixture-upstream'} if case=='naked-retarget' and name=='domestic-reconfigured' else route
        roles.append({'binary':'/synthetic/'+origin,'classification':'experimental' if origin=='candidate' else 'official','log_file':str(i+1).zfill(3)+'-'+name+'.log','case':case,'role':name,'origin':origin,'sha256':known[origin]['sha256'],'version':known[origin]['version'],'mihomo':name.startswith('device-'),'parser_passed':True,'owner_option_absent':True,'owner_recovery_enabled':False,'route_shape':shape,'route_sha256':digest(shape),'config_sha256':digest(config(device)) if name.startswith('device-') else 'c'*64,'launches':launches,'stops':[{'pid':x['pid'],'returncode':0,'pid_absent':True} for x in launches]})
        if name.startswith('device-'):device+=1
    row={'case':case,'scope':g.scope(),'status':'completed','steps':[],'unexecuted':[],'errors':[],'ports':ports,'targets':targets,'route_contract':{'domestic':route,'overseas':overseas,'udp_timeout':'2s'},'post_identity':{'source':source,'binary_sha256':{k:x['sha256'] for k,x in known.items()}},'associations':[],'closed_associations':[],'failed_constructions':[],'target_ledgers':[],'process_logs':{},'cleanup':{'processes':[{'pid':x['launches'][-1]['pid'],'returncode':0,'pid_absent':True} for x in roles],'fixture_removed':True,'new_threads':0,'dns_pending':0,'https_pending':0},'dns':{'queries':[],'ordinary':[],'wire':[]}}
    paths={'domestic':'002-domestic.log','upstream':'001-overseas.log','client':'003-device-mihomo.log'};all_logs={x['log_file']:'' for x in roles};accumulated={'domestic':'','upstream':'','client':''};ledger={n:[] for n in targets};shared=None;used=Counter();dns_records=g.expected_dns(case);dns_i=0
    for i,(name,kind) in enumerate(g.plan(case)):
        start=100+i*10;step={'id':name,'kind':kind,'started':start,'completed':start+3,'status':'passed'};row['steps'].append(step);body=g.payload(name)
        if kind=='direct':
            target=name[7:];step.update(payload=g.ident(body),expected_target=target);ledger[target].append({'peer':'127.0.0.1',**g.ident(body)});continue
        if kind in ('fault','restore','naked-restart'):
            stage=2 if kind=='restore' else 1
            paths['domestic']=[x for x in roles if x['role'].startswith('domestic')][stage]['log_file'];paths['client']=[x for x in roles if x['role'].startswith('device-')][stage]['log_file']
            accumulated['domestic']=all_logs[paths['domestic']];accumulated['client']=all_logs[paths['client']]
            continue
        if kind=='https':
            row['https']={'verified_tls':True,'status':200,'body':g.ident(b'VUI-VERIFIED-DOUBLE-HOP'),'deliveries':[['127.0.0.4','127.0.0.3','/guard-tcp']]};row['https_after_cleanup']=row['https']['deliveries'];continue
        raw=kind.startswith('raw-');dest,target=v.expected_destination(case,name,targets)
        if kind.endswith('reject'):target=None
        step.update(payload=g.ident(body),requested=dest,expected_target=target)
        share=raw and kind=='raw-echo' and case in ('naked-retarget','guarded-retarget','same-port-rebind')
        if share and shared is not None:association=row['associations'][shared]
        else:
            aid=len(row['associations']);association={'id':aid,'kind':'raw' if raw else 'socks','stream':{'fd':10+aid,'inode':100+aid,'local':['127.0.0.1',24000+aid],'peer':['127.0.0.1',ports['ingress']] if raw else None}}
            if raw:association.update(initial=dest,tls_verified=True,server_name='vpn.example.test',tls_version='TLSv1.3')
            else:association.update(control={'fd':50+aid,'inode':300+aid,'local':['127.0.0.1',25000+aid],'peer':['127.0.0.1',ports['client']]},relay=['127.0.0.1',26000+aid])
            row['associations'].append(association);row['closed_associations'].append({'id':aid,'stream_fd':-1,**({} if raw else {'control_fd':-1})})
            if share:shared=aid
        aid=association['id'];first=not used[aid];used[aid]+=1
        wire=frozen.xudp_frame(*dest,body,first=first) if raw else b'\0\0\0'+_socks_udp_target(*dest)+body
        before={role:{'offset':len(text),'record_boundary':True,'path':paths[role],'text':text} for role,text in accumulated.items()}
        fresh={role:'' for role in accumulated}
        if target:
            if first:fresh['upstream']=f'INFO[0000] [123 0ms] {v.PACKET}{targets[target]["host"]}:{targets[target]["port"]}\n'
            ledger[target].append({'peer':'127.0.0.4',**g.ident(body)})
            answer=[targets[target]['host'],targets[target]['port']]
            if raw:
                header=b'\0\0\2\1\2'+frozen.xudp_address(*answer);reply=(b'\0\0' if first else b'')+struct.pack('!H',len(header))+header+struct.pack('!H',len(body))+body
                step['decoded']={'host':answer[0],'port':answer[1],'payload':g.ident(body)}
            else:reply=b'\0\0\0'+_socks_udp_target(*answer)+body
            step['receives']=[{'method':'recv' if raw else 'recvfrom','association':aid,'wire':g.ident(reply),'wire_hex':reply.hex(),'completed':start+.5,**({} if raw else {'peer':association['relay']})}]
        else:
            step.update(reasons=g.reasons(case,name),window=2)
            role='upstream' if case=='upstream-uuid' else 'client' if case in ('device-ca','device-sni') else 'domestic'
            fresh[role]='ERROR[0000] [123 0ms] '+ ' '.join(step['reasons'])+'\n'
            if not case.startswith(('upstream-','device-')) and name!='both-failed':fresh['domestic']+=f'INFO[0000] [123 0ms] inbound/vless[fixture-domestic]: [0] inbound packet connection to {v.core_dest(dest)[0]}:{dest[1]}\nDEBUG[0000] [123 1ms] router: match[{g.guard_index(name)}] ip_cidr=fixture => reject\n'
        new={role:{'offset':before[role]['offset'],'record_boundary':True,'path':paths[role],'text':text} for role,text in fresh.items()}
        step['sends']=[{'method':'sendall' if raw else 'sendto','association':aid,'wire':g.ident(wire),'wire_hex':wire.hex(),'destination':None if raw else association['relay'],'return':None if raw else len(wire),'started':start+.1,'completed':start+.2,'log_checkpoint':before,'fresh_logs':new}]
        for role,text in fresh.items():
            accumulated[role]+=text;all_logs[paths[role]]=accumulated[role]
        is_dns=(case=='dns-all' and (name=='good' or name.startswith('dns-'))) or(case=='dns-partial' and name in ('partial','both-failed')) or(case=='same-port-rebind' and name in ('first','new'))
        if is_dns:
            host,values,failed=dns_records[dns_i];dns_i+=1
            for typ in (1,28):
                encoded=b''.join(bytes([len(x)])+x.encode() for x in host.split('.'))+b'\0';question=encoded+struct.pack('!HH',typ,1)
                query=struct.pack('!6H',dns_i,0x100,1,0,0,0)+question;answers=[]
                import ipaddress
                for value in values:
                    a=ipaddress.ip_address(value)
                    if a.version=={1:4,28:6}[typ] and typ not in failed:answers.append(b'\xc0\x0c'+struct.pack('!HHIH',typ,1,0,len(a.packed))+a.packed)
                reply=struct.pack('!6H',dns_i,0x8182 if typ in failed else 0x8180,1,len(answers),0,0)+question+b''.join(answers)
                row['dns']['queries'].append(['127.0.0.4',host,typ]);row['dns']['wire'].append({'peer':'127.0.0.4','query_hex':query.hex(),'response_hex':reply.hex(),'query':dns_wire(query),'response':dns_wire(reply),'write_return':len(reply),'started':start+.3,'completed':start+.4})
    row['process_logs']=all_logs
    for name,items in ledger.items():row['target_ledgers'].append({'name':name,**targets[name],'closed':True,'joined':True,'socket_fd':-1,'errors':[],'received':items,'expected':[{**x,'count':1} for x in items]})
    return row,roles,inputs,export
