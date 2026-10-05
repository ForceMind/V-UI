"""Actual pinned HTTP forwarding through public HY2 subscription exports.

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
import test_hysteria2_preflight_loopback as preflight


class Hysteria2PublicLoopbackTests(preflight.Hysteria2PreflightLoopbackTests):
    def setUp(self):
        grant_tests.SubscriptionTokenTests.setUp(self)
        super().setUp()
        patch.object(routing_store, 'ROUTING_FILE', self.root / 'routing.json').start()
        save_routing({**default_routing(), 'mode': 'direct'}, read_snapshot()['revision'])

    def server_config(self, port):
        self.server_port = port
        settings, stream = compile_profile('sing-box', 'hysteria2', {
            'security': 'tls', 'transport': 'quic', 'server_name': preflight.SNI,
            'certificate_path': str(self.cert), 'key_path': str(self.key)},
            {'users': [{'password': preflight.PASSWORD}]}, {})
        self.assertEqual(set(settings), {'users'})
        self.assertEqual(stream['_vui']['client_fingerprint'], '')
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, 1)
            row.core = 'sing-box'; row.protocol = 'hysteria2'; row.port = port
            row.remark = 'HY2_PUBLIC'; row.settings = settings; row.stream_settings = stream
            db.commit()
            config = SingBoxAdapter().build_config([row])
            self.good_settings, self.good_stream = deepcopy(settings), deepcopy(stream)
        config['log'] = {'level': 'debug', 'timestamp': False}
        config['inbounds'][0]['listen'] = '127.0.0.1'
        self.assertNotIn('transport', config['inbounds'][0])
        self.assertNotIn('_vui', config['inbounds'][0])
        response = self.client.post('/api/subscriptions', headers=grant_tests.HEADERS, json={
            'label': 'hy2-real', 'server': '127.0.0.1', 'inbound_ids': [1],
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
        if failure == 'password': settings['users'][0]['password'] = 'wrong-hy2-password'
        elif failure == 'sni':
            stream['tls']['server_name'] = 'wrong.example.test'
            stream['_vui']['server_name'] = 'wrong.example.test'
        else: self.assertIn(failure, (None, 'ca'))
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, 1)
            row.settings, row.stream_settings = settings, stream
            db.commit()
        password, sni = settings['users'][0]['password'], stream['tls']['server_name']
        raw = base64.b64decode(self.public_get(self.grant['paths']['raw']).text, validate=True).decode()
        self.assertEqual(len(raw.splitlines()), 1)
        uri = urlsplit(raw)
        self.assertEqual((uri.scheme, uri.hostname, uri.port, uri.path), ('hysteria2', '127.0.0.1', server_port, '/'))
        self.assertEqual(unquote(uri.username), password); self.assertIsNone(uri.password)
        self.assertEqual(parse_qs(uri.query), {'sni': [sni], 'insecure': ['0']})
        if client == 'mihomo':
            config = yaml.safe_load(self.public_get(self.grant['paths']['mihomo.yaml']).text)
            self.assertEqual(config['proxies'], [{'name': 'HY2_PUBLIC', 'type': 'hysteria2',
                'server': '127.0.0.1', 'port': server_port, 'password': password, 'udp': False,
                'sni': sni, 'skip-cert-verify': False}])
            groups = {group['name']: group for group in config['proxy-groups']}
            self.assertEqual(groups['FORCE_PROXY']['proxies'], ['HY2_PUBLIC'])
            self.assertEqual(groups['FORCE_PROXY']['type'], 'select')
            # Production routing bypasses LAN addresses; force only this local
            # reachable test target through the actual exported no-DIRECT group.
            config.update({'mixed-port': port, 'bind-address': '127.0.0.1',
                           'rules': ['MATCH,FORCE_PROXY'], 'log-level': 'debug'})
            return config
        self.assertEqual(client, 'singbox')
        config = self.public_get(self.grant['paths']['sing-box.json']).json()
        self.assertEqual(config['outbounds'], [{'type': 'hysteria2', 'tag': 'HY2_PUBLIC',
            'server': '127.0.0.1', 'server_port': server_port, 'password': password, 'network': 'tcp',
            'tls': {'enabled': True, 'server_name': sni}}])
        self.assertEqual(config['route'], {'final': 'HY2_PUBLIC'})
        self.assertNotIn('direct', [node['type'] for node in config['outbounds']])
        config['inbounds'][0]['listen_port'] = port
        config['log'] = {'level': 'debug', 'timestamp': False}
        return config
