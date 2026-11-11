"""The actual scoped XHTTP exports drive official Mihomo against Xray."""
import base64
from copy import deepcopy
import json
from urllib.parse import parse_qsl, urlencode, urlsplit
import unittest
from unittest.mock import patch

import yaml

from app.models import database
from app.services import routing_store
from app.services.core_manager import XrayAdapter, core_manager
from test_subscription_tokens import HEADERS
import test_subscription_tokens as grants
from test_xhttp_preflight_loopback import XHTTPFixture, fixed_cores_available
from loopback_helpers import http_authority, unused_port
from xhttp_helpers import HOST, MODE, PATH, SNI, UUID, mihomo_config, xhttp_values


@unittest.skipUnless(fixed_cores_available(), 'fixed official Xray, sing-box and Mihomo binaries required')
class XHTTPPublicLoopbackTests(XHTTPFixture, unittest.TestCase):
    profile_label = 'XHTTP public exports'

    def setUp(self):
        grants.SubscriptionTokenTests.setUp(self)
        super().setUp()
        self.stack.enter_context(patch.object(routing_store, 'ROUTING_FILE', self.root / 'routing.json'))
        self.server_port = unused_port()
        profile = {
            'transport': 'xhttp', 'security': 'tls', 'flow': '',
            'server_name': SNI, 'host': HOST, 'path': PATH, 'xhttp_mode': MODE,
            'xhttp_alpn_h2': True, 'client_fingerprint': 'chrome', 'skip_cert_verify': False,
            'certificate_path': str(self.cert), 'key_path': str(self.key),
        }
        # Production API, row compiler and database. The test starts the exact
        # generated server below, with only the test bind and logging adjusted.
        with patch.object(core_manager, 'validate_inbound_change'), patch('app.api.inbounds.apply_checked', return_value={}):
            created = self.client.post('/api/inbounds', headers=HEADERS, json={
                'core': 'xray', 'protocol': 'vless', 'port': self.server_port,
                'remark': 'ONLY_PROXY', 'settings': {'users': [{'id': UUID}]}, 'profile': profile,
            })
        self.assertEqual(created.status_code, 200, created.text)
        row = created.json()['inbound']
        self.assertTrue(row['share_eligible'])
        self.assertFalse(row['upstream_export_eligible'])
        self.assertNotIn(UUID, created.text)
        self.assertNotIn(str(self.root), created.text)
        self.node_id = row['id']
        issued = grants.SubscriptionTokenTests.issue(self, server='127.0.0.1', inbound_ids=[self.node_id])
        with grants.TestClient(grants.main.app, base_url=grants.ORIGIN, client=('127.0.0.1', 55001)) as anonymous:
            native = anonymous.get(issued['paths']['mihomo.yaml'])
            raw = anonymous.get(issued['paths']['raw'])
        link = self.client.get(f'/api/subscription/link/{self.node_id}?host=127.0.0.1', headers=HEADERS)
        for result in (native, raw, link):
            self.assertEqual(result.status_code, 200)
            self.assertNotIn(str(self.root), result.text)
        self.uri = base64.b64decode(raw.content, validate=True).decode('utf-8')
        self.assertEqual(link.text, self.uri)
        self.assertNotIn(str(self.root), self.uri)
        self.proxy, = yaml.safe_load(native.text)['proxies']
        self.assertEqual(self.proxy, {
            'name': 'ONLY_PROXY', 'type': 'vless', 'server': '127.0.0.1', 'port': self.server_port,
            'uuid': UUID, 'udp': False, 'tls': True, 'servername': SNI, 'network': 'xhttp',
            'skip-cert-verify': False, 'client-fingerprint': 'chrome', 'alpn': ['h2'],
            'xhttp-opts': {'path': PATH, 'host': HOST, 'mode': MODE},
        })
        parts = urlsplit(self.uri)
        self.assertEqual((parts.scheme, parts.username, parts.hostname, parts.port),
                         ('vless', UUID, '127.0.0.1', self.server_port))
        self.assertCountEqual(parse_qsl(parts.query), {
            'type': 'xhttp', 'security': 'tls', 'sni': SNI, 'host': HOST, 'path': PATH,
            'mode': MODE, 'fp': 'chrome', 'alpn': 'h2', 'encryption': 'none',
        }.items())

    def prepare_server(self, port):
        self.assertEqual(port, self.server_port)
        with database.SessionLocal() as db:
            config = XrayAdapter().build_config([db.get(database.Inbound, self.node_id)])
        inbound, = config['inbounds']
        self.assertEqual(inbound['streamSettings']['network'], 'xhttp')
        self.assertEqual(inbound['streamSettings']['tlsSettings']['alpn'], ['h2'])
        self.assertNotIn('_vui', inbound['streamSettings'])
        inbound['listen'] = '127.0.0.1'
        config['log']['loglevel'] = 'info'
        path = self.write_config(config, 'public-xray')
        self.checked([self.xray, 'run', '-test', '-c', str(path)])
        return [self.xray, 'run', '-c', str(path)]

    def prepare_client(self, client, port, server_port, *, failure=None):
        self.assertEqual(server_port, self.server_port)
        home = self.unique_path('mihomo-home', 'data')
        home.mkdir()
        values = xhttp_values(failure)
        if client == 'mihomo-uri':
            uri = self.uri
            if failure == 'uuid':
                uri = uri.replace('vless://' + UUID + '@', 'vless://' + values['uuid'] + '@', 1)
            elif failure in ('sni', 'path', 'host', 'mode'):
                parts = urlsplit(uri)
                uri = parts._replace(query=urlencode([
                    (key, values[failure] if key == failure else value)
                    for key, value in parse_qsl(parts.query)
                ])).geturl()
            provider = home / 'uri.txt'
            provider.write_text(uri)
            provider.chmod(0o600)
            config = mihomo_config(port, provider=provider)
        else:
            proxy = deepcopy(self.proxy)
            if failure == 'uuid':
                proxy['uuid'] = values['uuid']
            elif failure == 'sni':
                proxy['servername'] = values['sni']
            elif failure in ('path', 'host', 'mode'):
                proxy['xhttp-opts'][failure] = values[failure]
            config = mihomo_config(port, proxy=proxy)
        # The tested target is reachable without the proxy. Isolate the test
        # routing so it cannot use the shipped LAN/direct or DNS rules.
        config['ipv6'] = ':' in self.target_host
        self.assertNotIn('DIRECT', json.dumps(config))
        self.assertEqual(config['rules'], ['MATCH,ONLY_PROXY'])
        path = self.unique_path(client, 'yaml')
        path.write_text(yaml.safe_dump(config, sort_keys=False))
        path.chmod(0o600)
        env = self.env.copy()
        if failure == 'ca':
            env['SSL_CERT_FILE'] = str(self.wrong_ca)
        command = [self.mihomo, '-d', str(home), '-f', str(path)]
        self.checked([*command, '-t'], env=env)
        return command, env

    def assert_success(self, port):
        before = len(self.requests)
        super().assert_success(port)
        self.assertEqual(self.requests[before:], [(http_authority(self.target_host, self.target_port), '/probe')])

    def verify_clients(self, failure=None):
        super().verify_clients(failure, server_port=self.server_port)

    def test_verified_http_forwarding(self):
        self.verify_clients()

    def test_wrong_uuid_rejected(self):
        self.verify_clients('uuid')

    def test_wrong_ca_rejected(self):
        self.verify_clients('ca')

    def test_wrong_sni_rejected(self):
        self.verify_clients('sni')

    def test_wrong_path_rejected(self):
        self.verify_clients('path')

    def test_wrong_host_rejected(self):
        self.verify_clients('host')

    def test_wrong_valid_mode_rejected(self):
        self.verify_clients('mode')
