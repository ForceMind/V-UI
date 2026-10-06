"""Compiler and anonymous public exports run through actual pinned clients.

Negative mutations are applied to the successfully exported client documents;
the strict exporter must not itself emit an invalid or mismatched server pair.
"""
import base64
from copy import deepcopy
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import yaml
from app.models import database
from app.services import routing_store
from app.services.core_manager import SingBoxAdapter
from app.services.protocol_profiles import compile_profile
from app.services.mihomo_routing import default_routing
from app.services.routing_store import read_snapshot, save_routing
import test_subscription_tokens as grant_tests
import test_reality_preflight_loopback as preflight
from reality_helpers import SNI, WRONG_SNI


class RealityPublicLoopbackTests(preflight.RealityPreflightLoopbackTests):
    evidence_label = 'REALITY public subscription'
    def setUp(self):
        grant_tests.SubscriptionTokenTests.setUp(self)
        super().setUp()
        patch.object(routing_store, 'ROUTING_FILE', self.root/'routing.json').start()
        save_routing({**default_routing(), 'mode': 'direct'}, read_snapshot()['revision'])

    def server_config(self, port):
        self.server_port = port
        settings, stream = compile_profile('sing-box', 'vless', {
            'security': 'reality', 'transport': 'direct', 'flow': preflight.FLOW,
            'reality_target': '127.0.0.1:'+str(self.reference_port), 'reality_server_name': SNI,
            'client_fingerprint': 'chrome', 'reality_uuid': preflight.UUID, 'reality_short_id': preflight.SHORT_ID},
            {'users': [{'uuid': preflight.UUID}]}, {})
        self.private_key = stream['tls']['reality']['private_key']
        self.public_key = stream['_vui']['reality_public_key']
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, 1)
            row.core='sing-box'; row.protocol='vless'; row.port=port; row.remark='REALITY_PUBLIC'
            row.settings=settings; row.stream_settings=stream; db.commit()
            config = SingBoxAdapter().build_config([row])
        config['log']={'level':'debug','timestamp':False}
        config['inbounds'][0]['listen']='127.0.0.1'
        self.assertNotIn('_vui', config['inbounds'][0])
        response = self.client.post('/api/subscriptions', headers=grant_tests.HEADERS, json={
            'label':'reality-real', 'server':'127.0.0.1', 'inbound_ids':[1], 'formats':['raw','mihomo.yaml','sing-box.json']})
        self.assertEqual(response.status_code, 201, response.text)
        self.grant=response.json()
        return config

    def public_get(self, path):
        with self.client.__class__(self.client.app, base_url=grant_tests.ORIGIN, client=('127.0.0.1', 54245)) as public:
            response=public.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('no-store', response.headers['cache-control']); self.assertNotIn('set-cookie', response.headers)
        for forbidden in (self.private_key, str(self.root), 'private_key', 'certificate_path', 'key_path', 'handshake', '_vui'):
            self.assertNotIn(forbidden, response.text)
        return response

    def client_config(self, client, port, server_port, *, failure=None):
        self.assertEqual(server_port, self.server_port)
        uid = preflight.WRONG_UUID if failure=='uuid' else preflight.UUID
        flow = '' if failure=='flow' else preflight.FLOW
        public = self.wrong_public_key if failure=='public_key' else self.public_key
        short = preflight.WRONG_SHORT_ID if failure=='short_id' else preflight.SHORT_ID
        sni = WRONG_SNI if failure=='sni' else SNI
        raw=base64.b64decode(self.public_get(self.grant['paths']['raw']).text, validate=True).decode()
        self.assertEqual(len(raw.splitlines()),1)
        uri=urlsplit(raw); query=parse_qs(uri.query)
        self.assertEqual((uri.scheme,uri.username,uri.hostname,uri.port),('vless',preflight.UUID,'127.0.0.1',server_port))
        self.assertEqual(query, {'security':['reality'],'type':['tcp'],'encryption':['none'],'sni':[SNI],
            'flow':[preflight.FLOW],'fp':['chrome'],'pbk':[self.public_key],'sid':[preflight.SHORT_ID]})
        if client=='mihomo-uri':
            values={k:v[0] for k,v in query.items()}; values.update(sni=sni,pbk=public,sid=short)
            if flow: values['flow']=flow
            else: values.pop('flow')
            uri=uri._replace(netloc=f'{uid}@127.0.0.1:{server_port}', query=urlencode(values))
            path=self.unique_path('public-reality-uri','txt');path.write_text(urlunsplit(uri));path.chmod(0o600)
            return {'mixed-port':port,'bind-address':'127.0.0.1','allow-lan':False,'mode':'rule','log-level':'debug','ipv6':False,
                'proxy-providers':{'reality-uri':{'type':'file','path':str(path)}},
                'proxy-groups':[{'name':'REALITY_ONLY','type':'select','use':['reality-uri']}], 'rules':['MATCH,REALITY_ONLY']}
        if client=='mihomo':
            config=yaml.safe_load(self.public_get(self.grant['paths']['mihomo.yaml']).text)
            self.assertEqual(len(config['proxies']),1)
            node=config['proxies'][0]
            self.assertEqual(node['uuid'],preflight.UUID);self.assertEqual(node['flow'],preflight.FLOW)
            self.assertEqual(node['reality-opts'],{'public-key':self.public_key,'short-id':preflight.SHORT_ID})
            self.assertFalse(node['udp']);self.assertEqual(node['client-fingerprint'],'chrome');self.assertNotIn('alpn',node)
            groups={g['name']:g for g in config['proxy-groups']}
            self.assertEqual(groups['FORCE_PROXY']['proxies'],['REALITY_PUBLIC'])
            self.assertNotIn('DIRECT',groups['FORCE_PROXY']['proxies'])
            node.update(uuid=uid,flow=flow,servername=sni)
            node['reality-opts']={'public-key':public,'short-id':short}
            config.update({'mixed-port':port,'bind-address':'127.0.0.1','rules':['MATCH,FORCE_PROXY'],'log-level':'debug'})
            return config
        self.assertEqual(client,'singbox')
        config=self.public_get(self.grant['paths']['sing-box.json']).json()
        self.assertEqual(len(config['outbounds']),1)
        node=config['outbounds'][0]
        self.assertEqual(node['type'],'vless');self.assertEqual(node['uuid'],preflight.UUID)
        self.assertEqual(node['flow'],preflight.FLOW);self.assertEqual(node['network'],'tcp')
        self.assertEqual(node['tls']['reality'],{'enabled':True,'public_key':self.public_key,'short_id':preflight.SHORT_ID})
        self.assertEqual(config['route'],{'final':'REALITY_PUBLIC'})
        node.update(uuid=uid,flow=flow);node['tls']['server_name']=sni
        node['tls']['reality'].update(public_key=public,short_id=short)
        config['inbounds'][0]['listen_port']=port;config['log']={'level':'debug','timestamp':False}
        return config
