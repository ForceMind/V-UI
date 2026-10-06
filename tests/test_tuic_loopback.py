"""Actual pinned HTTP forwarding through public TUIC subscription exports.

Reuse the independent preflight's processes, target counters and mandatory
rejection-reason assertions. Server and clients here come from application
compiler/adapter and cookie-free public exports, not the bare fixtures.
"""
import base64
from copy import deepcopy
import json
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

import yaml

from app.models import database
from app.services import routing_store
from app.services.core_manager import SingBoxAdapter
from app.services.protocol_profiles import compile_profile
from app.services.mihomo_routing import default_routing
from app.services.routing_store import read_snapshot, save_routing
import test_subscription_tokens as grant_tests
import test_tuic_preflight_loopback as preflight


class TUICPublicLoopbackTests(preflight.TUICPreflightLoopbackTests):
    clients = ("mihomo", "singbox", "mihomo-uri")
    def setUp(self):
        grant_tests.SubscriptionTokenTests.setUp(self)
        super().setUp()
        patch.object(routing_store, 'ROUTING_FILE', self.root / 'routing.json').start()
        save_routing({**default_routing(), 'mode': 'direct'}, read_snapshot()['revision'])

    def server_config(self, port):
        self.server_port = port
        settings, stream = compile_profile('sing-box', 'tuic', {
            'security': 'tls', 'transport': 'quic', 'server_name': preflight.SNI,
            'certificate_path': str(self.cert), 'key_path': str(self.key)},
            {'users': [{'uuid': preflight.UUID, 'password': preflight.PASSWORD}]}, {})
        self.assertEqual(set(settings), {'users'})
        self.assertEqual(stream['_vui']['client_fingerprint'], '')
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, 1)
            row.core = 'sing-box'; row.protocol = 'tuic'; row.port = port
            row.remark = 'TUIC_PUBLIC'; row.settings = settings; row.stream_settings = stream
            db.commit()
            config = SingBoxAdapter().build_config([row])
            self.good_settings, self.good_stream = deepcopy(settings), deepcopy(stream)
        config['log'] = {'level': 'debug', 'timestamp': False}
        config['inbounds'][0]['listen'] = '127.0.0.1'
        self.assertNotIn('transport', config['inbounds'][0])
        self.assertNotIn('_vui', config['inbounds'][0])
        response = self.client.post('/api/subscriptions', headers=grant_tests.HEADERS, json={
            'label': 'tuic-real', 'server': '127.0.0.1', 'inbound_ids': [1],
            'formats': ['raw', 'mihomo.yaml', 'sing-box.json']})
        self.assertEqual(response.status_code, 201, response.text)
        self.grant = response.json()
        return config

    def public_get(self, path):
        with self.client.__class__(self.client.app, base_url=grant_tests.ORIGIN,
                                   client=('127.0.0.1', 54245)) as public:
            response = public.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('no-store', response.headers['cache-control'])
        self.assertNotIn('set-cookie', response.headers)
        for forbidden in (str(self.cert), str(self.key), str(self.ca), 'certificate_path', 'key_path', 'PRIVATE KEY'):
            self.assertNotIn(forbidden, response.text)
        return response

    def client_config(self, client, port, server_port, *, failure=None):
        self.assertEqual(server_port, self.server_port)
        settings, stream = deepcopy(self.good_settings), deepcopy(self.good_stream)
        if failure == 'password': settings['users'][0]['password'] = 'wrong-tuic-password'
        elif failure == 'uuid': settings['users'][0]['uuid'] = preflight.WRONG_UUID
        elif failure == 'sni':
            stream['tls']['server_name'] = 'wrong.example.test'
            stream['_vui']['server_name'] = 'wrong.example.test'
        else: self.assertIn(failure, (None, 'ca'))
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, 1)
            row.settings, row.stream_settings = settings, stream
            db.commit()
        uuid, password, sni = settings['users'][0]['uuid'], settings['users'][0]['password'], stream['tls']['server_name']
        raw = base64.b64decode(self.public_get(self.grant['paths']['raw']).text, validate=True).decode()
        self.assertEqual(len(raw.splitlines()), 1)
        uri = urlsplit(raw)
        self.assertEqual((uri.scheme, uri.hostname, uri.port, uri.path), ('tuic', '127.0.0.1', server_port, ''))
        self.assertEqual(unquote(uri.username), uuid); self.assertEqual(unquote(uri.password), password)
        self.assertEqual(parse_qs(uri.query), {'sni': [sni], 'alpn': ['h3']})
        if client == 'mihomo-uri':
            # The fixed actual provider parser imports the exact exported URI.
            path = self.unique_path('tuic-uri-provider', 'txt')
            path.write_text(raw)
            config = {'mixed-port': port, 'bind-address': '127.0.0.1', 'allow-lan': False,
                'mode': 'rule', 'log-level': 'debug', 'ipv6': False,
                'proxy-providers': {'tuic-uri': {'type': 'file', 'path': str(path)}},
                'proxy-groups': [{'name': 'TUIC_URI_ONLY', 'type': 'select', 'use': ['tuic-uri']}],
                'rules': ['MATCH,TUIC_URI_ONLY']}
            self.assertNotIn('DIRECT', json.dumps(config))
            return config
        if client == 'mihomo':
            config = yaml.safe_load(self.public_get(self.grant['paths']['mihomo.yaml']).text)
            self.assertEqual(config['proxies'], [{'name': 'TUIC_PUBLIC', 'type': 'tuic',
                'server': '127.0.0.1', 'port': server_port, 'uuid': uuid, 'password': password,
                'sni': sni, 'skip-cert-verify': False, 'alpn': ['h3'], 'reduce-rtt': False}])
            groups = {group['name']: group for group in config['proxy-groups']}
            self.assertEqual(groups['FORCE_PROXY']['proxies'], ['TUIC_PUBLIC'])
            self.assertEqual(groups['FORCE_PROXY']['type'], 'select')
            # Production routing bypasses LAN addresses; force only this local
            # reachable test target through the actual exported no-DIRECT group.
            config.update({'mixed-port': port, 'bind-address': '127.0.0.1',
                           'rules': ['MATCH,FORCE_PROXY'], 'log-level': 'debug'})
            return config
        self.assertEqual(client, 'singbox')
        config = self.public_get(self.grant['paths']['sing-box.json']).json()
        self.assertEqual(config['outbounds'], [{'type': 'tuic', 'tag': 'TUIC_PUBLIC',
            'server': '127.0.0.1', 'server_port': server_port, 'uuid': uuid, 'password': password, 'network': 'tcp',
            'zero_rtt_handshake': False, 'tls': {'enabled': True, 'server_name': sni, 'alpn': ['h3']}}])
        self.assertEqual(config['route'], {'final': 'TUIC_PUBLIC'})
        self.assertNotIn('direct', [node['type'] for node in config['outbounds']])
        config['inbounds'][0]['listen_port'] = port
        config['log'] = {'level': 'debug', 'timestamp': False}
        return config
