#!/usr/bin/env python3
"""One owned fixture, exact fixed roles and bounded recorded operations."""
import argparse
from copy import deepcopy
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import traceback
import unittest
from unittest.mock import patch
import guard_contract as g
from observe import Observe
from auth_controls import sanitize_auth
r=g.r


def execute(args):
    args.output.mkdir(parents=True,exist_ok=False)
    identity=g.build.identity(args.legacy_repository)
    g.adapter.install();args.arm='reduced';known=r.binaries(args,identity['legacy'])
    r.need(json.loads((args.input/'input-identity.json').read_text())=={'scope':g.scope(),'source':identity,'binaries':known,'run':g.run_binding()},'Child inputs differ')
    row={'case':args.case,'steps':[],'unexecuted':[],'errors':[],'scope':g.scope(),'status':'running'}
    records=[];before_threads=set(threading.enumerate());f=obs=None
    with tempfile.TemporaryDirectory(prefix='vui-guard-data-') as data:
        os.environ.update(VUI_DATA_DIR=data,VUI_TEST_CORES=str(args.official.parent),VUI_TEST_MIHOMO=str(args.mihomo),VUI_RESTRICTED_UDP_PREFLIGHT='1')
        os.environ.pop('VUI_RESTRICTED_UDP_EVIDENCE',None)
        p,c,_=r.load_framework(args.legacy_repository);module,support=c.base.runtime_modules()
        helpers=importlib.import_module('restricted_udp_preflight_helpers');udp=importlib.import_module('udp_dns_capability_helpers')
        test=unittest.TestCase();base=r.role_fixture(c,module,args.output,known,{'roles':g.ROLES},records,r.ROOT)
        class Fixture(base):
            def safe(self,text):return sanitize_auth(super().safe(text),c.life.clean)
            def guarded_config(self,**kw):
                kw.setdefault('udp_timeout','2s');value=super().guarded_config(**kw)
                value['route']['rules'] += [{'domain_regex':['.+'],'action':'reject'}, {'ip_cidr':['127.0.0.0/8'],'invert':True,'action':'reject'}]
                return value
            def upstream_config(self):
                value=super().upstream_config()
                value['route']['rules']=[{'domain_regex':['.+'],'action':'reject'},{'ip_cidr':['127.0.0.0/8'],'invert':True,'action':'reject'}]
                return value
            def client_config(self,kind):
                r.need(kind=='mihomo','No device role expansion')
                return deepcopy(self.ordinary_config)
            def logs(self):
                value=super().logs();self.final_logs=value
                self.final_process_logs={core.log_path.name:core.log_path.read_text(errors='strict') for core in self.processes}
                return value
        try:
            with Fixture(test,args.case) as f:
                obs=Observe(f,row,module,udp,support)
                f.final_logs='';f.final_process_logs={}
                targets={'target':f.target}
                case=args.case
                def target(name,host):
                    targets[name]=f.target_at(host);return targets[name]
                if case in ('dns-all','naked-retarget','literal-ports'):target('denied',module.DENIED)
                if case in ('guarded-retarget','literal-ports'):
                    for name,host in (('denied',module.DENIED),('ingress',module.INGRESS),('upstream',module.UPSTREAM)):
                        if name not in targets:target(name,host)
                if case=='guarded-retarget':target('other',module.TARGET)
                if case=='same-port-rebind':
                    trap=c.base.make_same_port_target(udp.DatagramTarget)(module.DENIED,f.target.port)
                    f.targets.append(trap);targets['denied']=trap
                f.target_names={id(value):name for name,value in targets.items()}
                # Construct the isolated DoH recorder before guarded_config starts it.
                with patch.object(helpers,'doh_target',obs.doh),obs.install():
                    domestic,client=r.configs(f,{'protocol':'vless'},r.ROOT,args.output)
                    f.ordinary_config=deepcopy(client)
                    row['ports']={'ingress':f.ingress_port,'client':f.client_port,'upstream':f.upstream_port,'doh':f.doh_port,'ordinary_dns':f.ordinary_port}
                    row['targets']={name:{'host':value.host,'port':value.port} for name,value in targets.items()}
                    # Public hashes reconstruct all nonsecret routing/containment fields.
                    row['route_contract']={'domestic':domestic['route'],'overseas':f.upstream_config()['route'],'udp_timeout':'2s'}
                    def step(name,kind,fn,**fields):
                        with obs.step(name,kind,**fields) as entry:
                            try:return fn()
                            finally:
                                for sent in entry.get('sends',[]):sent['fresh_logs']=obs.fresh(sent['log_checkpoint'])
                    def echo(name,target_name='target',host=None,port=None,raw=None):
                        t=targets[target_name];host=host or t.host;port=port or t.port;body=g.payload(name)
                        def action():
                            if raw is None:
                                f.echo(body,host=host,target=t)
                            else:module.RestrictedUdpPreflightTests.raw_echo(test,f,raw,host,port,body,actual=t)
                        return step(name,'raw-echo' if raw is not None else 'echo',action,payload=g.ident(body),requested=[host,port],expected_target=target_name)
                    def reject(name,host=None,target_name='target',port=None,raw=False,process=None):
                        t=targets[target_name];host=host or t.host;port=port or t.port;body=g.payload(name);reasons=g.reasons(case,name)
                        def action():
                            func=f.raw_reject if raw else f.reject
                            func(body,process or f.domestic,reasons,host=host,target=t,port=port,window=2)
                        return step(name,'raw-reject' if raw else 'reject',action,payload=g.ident(body),requested=[host,port],expected_target=None,reasons=reasons,window=2)
                    for name,kind in g.plan(case):
                        if kind!='direct':break
                        key=name.removeprefix('direct-');body=g.payload(name)
                        step(name,kind,lambda t=targets[key],body=body:t.direct(body),payload=g.ident(body),expected_target=key)
                    f.start('mihomo',domestic=domestic,client=client)
                    row['startup_completed']=True
                    echo('ordinary')
                    if case.startswith(('upstream-','device-')):
                        echo('before');upstream=case.startswith('upstream-');fault=case.rsplit('-',1)[1];observed=None
                        def inject():
                            nonlocal observed
                            bad=deepcopy(domestic);bad_client=deepcopy(client);env=client_env=None
                            if upstream:
                                if fault=='uuid':bad['outbounds'][0]['uuid']=module.WRONG_UUID;observed=f.upstream
                                elif fault=='ca':env={**f.env,'SSL_CERT_FILE':str(f.wrong_ca)}
                                elif fault=='sni':bad['outbounds'][0]['tls']['server_name']='wrong.example.test'
                                else:
                                    f.upstream.stop();r.need(f.upstream.process.poll() is not None,'Stopped overseas still running')
                                    try:socket.create_connection((module.UPSTREAM,f.upstream_port),timeout=.2)
                                    except ConnectionRefusedError:pass
                                    else:raise AssertionError('Stopped overseas still listening')
                            else:
                                node=bad_client['proxies'][0]
                                if fault=='uuid':node['uuid']=module.WRONG_UUID
                                elif fault=='ca':client_env={**f.env,'SSL_CERT_FILE':str(f.wrong_ca)}
                                else:node['servername']='wrong.example.test'
                            f.restart_domestic_and_device('mihomo',bad,domestic_env=env,client=bad_client,client_env=client_env)
                            if not upstream:observed=f.domestic if fault=='uuid' else f.client
                            elif observed is None:observed=f.domestic
                        step('fault','fault',inject)
                        reject('negative',process=observed)
                        def restore():
                            if fault=='stopped':f.upstream.start(f.upstream_port,module.UPSTREAM)
                            f.restart_domestic_and_device('mihomo',domestic,client=client)
                        step('restore','restore',restore);echo('recovery')
                    elif case=='tcp':
                        def https():
                            port,traffic=support.https_target(f.stack,module.TARGET,f.cert,f.key);row['https_target_port']=port;f.https_traffic=traffic
                            response=support.https_through(f.client_port,module.TARGET,port,f.ca,'guard-tcp',protocol='socks5h')
                            test.assertEqual(response,(200,support.BODY));deadline=time.monotonic()+3
                            while traffic.pending():
                                r.need(time.monotonic()<deadline,'HTTPS pending');time.sleep(.01)
                            expected=[(module.UPSTREAM_EGRESS,module.TARGET,'/guard-tcp')];test.assertEqual(traffic.snapshot(),expected)
                            row['https']={'verified_tls':True,'status':response[0],'body':g.ident(response[1]),'deliveries':traffic.snapshot()}
                        step('https','https',https)
                    elif case in ('dns-all','dns-partial'):
                        echo('literal-before')
                        if case=='dns-all':
                            host='good.example.test';f.answers.records[host]=(module.TARGET,);f.expect_dns(host)
                            with module.RawXudpAssociation(f,host,f.target.port) as association:echo('good',host=host,raw=association)
                            for label,values in g.ANSWERS:
                                host=label+'.example.test';f.answers.records[host]=values;f.expect_dns(host)
                                reject('dns-'+label,host=host,target_name='denied',raw=True)
                            reject('literal-private',target_name='denied')
                        else:
                            host='failed-aaaa.example.test';f.answers.records[host]=(module.TARGET,);f.answers.failed.add((host,28));f.expect_dns(host)
                            with module.RawXudpAssociation(f,host,f.target.port) as association:echo('partial',host=host,raw=association)
                            host='both-failed.example.test';f.answers.failed.add(host);f.expect_dns(host);reject('both-failed',host=host,raw=True)
                    elif case=='naked-retarget':
                        step('naked-restart','naked-restart',lambda:f.restart_domestic_and_device('mihomo',f.domestic_config(),client=client))
                        with module.RawXudpAssociation(f,module.TARGET,f.target.port) as association:
                            echo('first',raw=association);echo('retarget',target_name='denied',raw=association)
                    elif case=='guarded-retarget':
                        with module.RawXudpAssociation(f,module.TARGET,f.target.port) as association:
                            echo('first',raw=association)
                            changes=[(targets[x].host,targets[x].port) for x in ('denied','ingress','upstream','other')]+[('::1',targets['denied'].port),('::ffff:'+module.DENIED,targets['denied'].port),('metadata-alias.example.test',targets['denied'].port),(module.TARGET,53),(module.TARGET,443)]
                            for i,(host,port) in enumerate(changes):echo('change-'+str(i),host=host,port=port,raw=association)
                    elif case=='same-port-rebind':
                        host='same-port-rebind.example.test';f.answers.records[host]=(module.TARGET,);f.expect_dns(host)
                        with module.RawXudpAssociation(f,host,f.target.port) as association:
                            echo('first',host=host,raw=association);f.answers.records[host]=(module.DENIED,)
                            echo('same',host=host,raw=association);f.audit()
                        f.expect_dns(host);reject('new',host=host,target_name='denied',raw=True)
                    else:
                        echo('high')
                        for port in (53,443,1023):reject('port-'+str(port),port=port)
                        for i,host in enumerate(g.LITERALS):
                            key={module.DENIED:'denied',module.INGRESS:'ingress',module.UPSTREAM:'upstream'}.get(host,'denied')
                            reject('literal-'+str(i),host=host,target_name=key,raw=':' in host)
                    f.audit()
            row['status']='completed'
        except Exception as exc:
            row['status']='failed';row['errors'].append({'type':type(exc).__name__,'message':str(exc),'trace':traceback.format_exc()})
        finally:
            if f is not None:
                import psutil
                row['process_logs']=getattr(f,'final_process_logs',{})
                row['observations']=f.observations
                row['target_ledgers']=[{'name':f.target_names.get(id(t),'unknown'),'host':t.host,'port':t.port,'closed':t.closed,'joined':not t.thread.is_alive(),'socket_fd':t.server.socket.fileno(),'errors':t.errors,
                    'received':[{'peer':peer,**g.ident(body)} for peer,body in t.received],
                    'expected':[{'peer':peer,**g.ident(body),'count':count} for (peer,body),count in t.expected.items()]} for t in f.targets]
                row['cleanup']={'processes':[{'pid':x.process.pid,'returncode':x.process.poll(),'pid_absent':not psutil.pid_exists(x.process.pid)} for x in f.processes if x.process],
                    'fixture_removed':not f.root.exists(),'new_threads':len([t for t in threading.enumerate() if t not in before_threads]),
                    'dns_pending':f.answers.pending() if f.answers else None,'https_pending':f.https_traffic.pending() if hasattr(f,'https_traffic') else 0}
                if hasattr(f,'https_traffic'):row['https_after_cleanup']=f.https_traffic.snapshot()
                row['dns']={'queries':f.answers.snapshot() if f.answers else [],'ordinary':f.ordinary_answers.queries if f.ordinary_answers else [],'wire':obs.dns if obs else []}
                row['associations']=obs.associations if obs else [];row['closed_associations']=obs.closed if obs else []
                (args.output/'fixture.log').write_text(f.safe(getattr(f,'final_logs','')))
            row['unexecuted']=[name for name,_ in g.plan(args.case) if name not in [s['id'] for s in row['steps']]]
            row['post_identity']={'source':g.build.identity(args.legacy_repository),'binary_sha256':{k:r.sha(v['path']) for k,v in known.items()}}
            row=sanitize_auth(row,c.life.clean);r.save(args.output/'result.json',row)
            for path in args.output.glob('*.json'):r.save(path,sanitize_auth(json.loads(path.read_text()),c.life.clean))
    return row

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('legacy-repository','official','mihomo','candidate','candidate-provenance','build-evidence','output','input'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--case',choices=g.IDS,required=True)
    args=parser.parse_args()
    for key,value in vars(args).items():
        if isinstance(value,Path):setattr(args,key,value.resolve())
    result=execute(args);print(json.dumps({'case':args.case,'status':result['status']}));raise SystemExit(0 if result['status']=='completed' else 1)
