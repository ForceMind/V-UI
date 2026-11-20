"""Follow the panel's managed TLS creation and exact-node subscription requests."""
import json
import unittest
from unittest.mock import patch

import yaml
from cryptography.hazmat.primitives.serialization import Encoding

from app.models import database
from app.services import routing_store
from app.services.core_manager import SingBoxAdapter, core_manager
from loopback_helpers import CoreProcess, unused_port
from proxy_loopback_support import ReadyMihomoProcess
from test_auth import HEADERS, ORIGIN, TestClient
from test_certificates import CertificateTests
from test_xhttp_preflight_loopback import XHTTPFixture, fixed_cores_available
from xhttp_helpers import mihomo_config


@unittest.skipUnless(fixed_cores_available(), 'pinned server and client binaries required')
class PanelDeliveryTests(XHTTPFixture, unittest.TestCase):
    login = CertificateTests.login
    issue = CertificateTests.issue

    def setUp(self):
        CertificateTests.setUp(self)
        super().setUp()
        self.stack.enter_context(patch.object(routing_store, 'ROUTING_FILE', self.root / 'routing.json'))
        self.ca.write_bytes(self.provider.ca.public_bytes(Encoding.PEM))

    def test_create_node_issue_scoped_subscription_and_forward_http(self):
        domain = 'panel-delivery.example.test'
        cert_id = self.issue(domain=domain)
        port = unused_port()
        with patch.object(core_manager, 'validate_inbound_change'), patch('app.api.inbounds.apply_checked', return_value={}):
            response = self.client.post('/api/inbounds', headers=HEADERS, json={
                'core': 'sing-box', 'protocol': 'vless', 'remark': 'ONLY_PROXY', 'enable': True,
                'port': port, 'routing_mode': 'direct', 'profile': {'security': 'tls', 'transport': 'direct'},
                'certificate_id': cert_id,
            })
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()['inbound']
        self.assertEqual(row['transport'], 'tcp')
        self.assertEqual(row['subscription_formats'], ['mihomo.yaml', 'raw', 'sing-box.json'])
        inventory = self.client.get('/api/inbounds')
        self.assertEqual(inventory.json()[0]['mutation_revision'], row['mutation_revision'])
        payload = {'label': 'delivery', 'server': '127.0.0.1', 'inbound_ids': [row['id']],
                   'formats': ['mihomo.yaml'], 'expected_node_revision': inventory.headers['X-VUI-Node-Revision']}
        with TestClient(self.client.app, base_url=ORIGIN, client=('127.0.0.1', 55002)) as anonymous:
            self.assertEqual(anonymous.post('/api/subscriptions', headers=HEADERS, json=payload).status_code, 401)
            self.assertEqual(self.client.post('/api/subscriptions', headers=HEADERS,
                json={**payload, 'expected_node_revision': '0' * 64}).status_code, 409)
            grant = self.client.post('/api/subscriptions', headers=HEADERS, json=payload)
            self.assertEqual(grant.status_code, 201)
            paths = grant.json()['paths']
            self.assertEqual(set(paths), {'mihomo.yaml'})
            exported = anonymous.get(paths['mihomo.yaml'])
            self.assertEqual(exported.status_code, 200)
            self.assertEqual(exported.headers['Cache-Control'], 'no-store')
            self.assertEqual(anonymous.get(paths['mihomo.yaml'].replace('mihomo.yaml', 'raw')).status_code, 404)
        proxy, = yaml.safe_load(exported.text)['proxies']
        self.assertEqual((proxy['servername'], proxy['port'], proxy['skip-cert-verify']), (domain, port, False))
        self.assertNotIn(proxy['uuid'], response.text)
        self.assertNotIn(proxy['uuid'], inventory.text)
        self.assertNotIn('certificate_path', exported.text)
        with database.SessionLocal() as db:
            config = SingBoxAdapter().build_config([db.get(database.Inbound, row['id'])])
        config['inbounds'][0]['listen'] = '127.0.0.1'
        server_path = self.write_config(config, 'panel-server')
        self.checked([self.singbox, 'check', '-c', str(server_path)])
        server = CoreProcess(self.stack, [self.singbox, 'run', '-c', str(server_path)],
                             self.unique_path('server', 'log'), self.env)
        server.start(port)
        self.start_target()
        client_port = unused_port()
        client_config = mihomo_config(client_port, proxy=proxy)
        client_config['ipv6'] = ':' in self.target_host
        self.assertEqual(client_config['rules'], ['MATCH,ONLY_PROXY'])
        self.assertNotIn('DIRECT', json.dumps(client_config))
        config_file = self.unique_path('panel-client', 'yaml')
        config_file.write_text(yaml.safe_dump(client_config))
        home = self.root / 'mihomo'
        home.mkdir()
        command = [self.mihomo, '-d', str(home), '-f', str(config_file)]
        self.checked([*command, '-t'], env=self.env)
        mihomo = ReadyMihomoProcess(self.stack, command, self.unique_path('client', 'log'), self.env)
        mihomo.start(client_port)
        self.assert_success(client_port)
        print('Panel delivery: exact node, scoped YAML, verified TLS, real HTTP delivery, no DIRECT')
