"""Actual Xray VLESS TLS and stock Mihomo consume ordinary URI/YAML exports.

Runs only in the existing Actions loopback step. Fake CA, local target and
isolated per-child trust; no host trust, browser, public CA or real credentials.
"""
from contextlib import ExitStack
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml
from cryptography.hazmat.primitives import serialization

from tests.core_candidate_context import runtime_for_fixture
from app.api import inbounds as inbound_api, subscription as subscription_api
from app.models import database
from app.services.core_manager import XrayAdapter, core_manager
from test_real_cores import DiagnosticXrayRuntime
import test_auth as auth_tests
import test_certificates as certificate_fixtures
from loopback_helpers import certificate_files, start_http_target, unused_port, http_through, require_tls_rejection
from proxy_loopback_support import ReadyMihomoProcess
from inbound_routing_real_helpers import yaml_client, uri_client


@unittest.skipUnless(os.getenv('VUI_TEST_MIHOMO') and os.getenv('VUI_TEST_CORES'), 'pinned real binaries required')
class XrayShareLoopbackTests(unittest.TestCase):
    setUp = certificate_fixtures.CertificateTests.setUp
    login = auth_tests.AuthenticationTests.login
    issue = certificate_fixtures.CertificateTests.issue

    def start_client(self, stack, root, kind, config, port, env):
        # One reviewed stock-client constructor, always using its post-up gate.
        path = root / (kind + '.yaml'); path.write_text(yaml.safe_dump(config, allow_unicode=True))
        log = root / (kind + '.log')
        client = ReadyMihomoProcess(stack, [os.environ['VUI_TEST_MIHOMO'], '-d', str(root / (kind + '-data')), '-f', str(path)], log, env)
        client.start(port)
        return client, log

    def test_real_uri_provider_and_yaml_forward_and_wrong_ca_rejects(self):
        with ExitStack() as stack:
            root = Path(self.temp.name)
            certificate = self.issue(domain='vpn.example.test')
            ca = root / 'fixture-roots.pem'
            ca.write_bytes(self.provider.ca.public_bytes(serialization.Encoding.PEM))
            empty = root / 'empty'; empty.mkdir()
            env = {**os.environ, 'SSL_CERT_FILE': str(ca), 'SSL_CERT_DIR': str(empty)}
            server_port = unused_port()
            adapter = XrayAdapter()
            runtime = runtime_for_fixture(DiagnosticXrayRuntime, 'xray', Path(os.environ['VUI_TEST_CORES'])/'xray', root/'xray-runtime')
            stack.callback(runtime.close); adapter._runtime = runtime
            def apply(core, *, activate=True, **kwargs):
                self.assertEqual(core,'xray')
                with database.SessionLocal() as db:
                    rows = db.query(database.Inbound).filter_by(core='xray').all()
                    return runtime.apply(adapter.build_config(rows), activate=activate)
            stack.enter_context(patch.dict(core_manager.adapters, {'xray':adapter}))
            # Issuance and unrelated global policy are fixture controls. Node
            # auth/revision/CRUD, adapter generation and runtime are production.
            stack.enter_context(patch.object(core_manager,'validate_inbound_change'))
            stack.enter_context(patch.object(core_manager,'apply_database',side_effect=apply))
            stack.enter_context(patch.object(inbound_api,'apply_checked',side_effect=apply))
            stack.enter_context(patch.object(subscription_api,'load_routing',return_value={'mode':'direct'}))
            granted = self.client.post('/api/maintenance/grants', headers=auth_tests.HEADERS,
                json={'scopes':['diagnose','nodes','share','certificates'],'expires_seconds':1800})
            self.assertEqual(granted.status_code,201,granted.text)
            grant_id = granted.json()['id']
            credential = granted.json()['link'].split('#grant=',1)[1]
            exchanged = self.client.post('/api/maintenance/exchange',json={'grant':credential},headers={'X-VUI-Request':'1'})
            self.assertEqual(exchanged.status_code,200)
            headers = {'Authorization':'Bearer '+exchanged.json()['token'],'X-VUI-Request':'1'}
            created = self.client.post('/api/inbounds',headers=headers,json={'core':'xray','protocol':'vless',
                'port':server_port,'remark':'synthetic-xray-share','profile':{'security':'tls','transport':'raw'},'certificate_id':certificate})
            self.assertEqual(created.status_code,200,created.text)
            identity = created.json()['inbound']['id']
            self.assertTrue(runtime.status()['running'])
            editor = self.client.get(f'/api/inbounds/{identity}/editor',headers=headers).json()
            changed = self.client.put(f'/api/inbounds/{identity}',headers=headers,
                json={'remark':'synthetic-edited','expected_instance_revision':editor['mutation_revision']})
            self.assertEqual(changed.status_code,200,changed.text)
            target_port, deliveries = start_http_target(stack)
            link = self.client.get(f'/api/subscription/link/{identity}?host=127.0.0.1',headers=headers)
            self.assertEqual(link.status_code,200,link.text)
            subscription = self.client.get('/api/subscription/raw?host=127.0.0.1&core=xray',headers=headers)
            self.assertEqual(subscription.status_code,200,subscription.text)
            self.assertEqual(base64.b64decode(subscription.text).decode().strip(),link.text.strip())
            # Keep the actual URI subscription bytes inside Mihomo's own data
            # directory. Do not bypass its provider SAFE_PATHS protection.
            uri = root / 'uri-data' / 'ordinary.txt'; uri.parent.mkdir(mode=0o700)
            uri.write_text(subscription.text)
            artifact = self.client.get('/api/subscription/mihomo.yaml?host=127.0.0.1&core=xray',headers=headers)
            self.assertEqual(artifact.status_code,200,artifact.text)
            node = yaml.safe_load(artifact.text)['proxies'][0]
            self.assertFalse(node['skip-cert-verify']); self.assertFalse(node['udp'])
            self.assertNotIn(str(self.manager.root),artifact.text)
            for kind in ('yaml', 'uri'):
                with self.subTest(kind=kind):
                    proxy_port, controller = unused_port(), unused_port()
                    config = (yaml_client({'proxies':[node]}, [node['name']], proxy_port, controller) if kind == 'yaml'
                              else uri_client(uri, proxy_port, controller))
                    client, _ = self.start_client(stack, root, kind, config, proxy_port, env)
                    before = len(deliveries)
                    status, body = http_through(proxy_port, '127.0.0.1', target_port)
                    self.assertEqual(status, 200); self.assertEqual(body, b'VUI-LOOPBACK-TARGET')
                    self.assertEqual(len(deliveries), before + 1)
                    client.stop()
            # Use the same generated node bytes with an unrelated trust root.
            other_ca, _, _ = certificate_files(root, 'untrusted')
            bad_env = {**env, 'SSL_CERT_FILE':str(other_ca)}
            port = unused_port(); config = yaml_client({'proxies':[node]}, [node['name']], port, unused_port())
            rejected, log = self.start_client(stack, root, 'untrusted', config, port, bad_env)
            expected = len(deliveries)
            def attempt():
                try:
                    result = http_through(port, '127.0.0.1', target_port)
                    self.assertNotEqual(result[0], 200)
                except OSError:
                    pass
            require_tls_rejection(self, log, attempt, deliveries, 'unknown authority', expected_count=expected)
            current = self.client.get(f'/api/inbounds/{identity}/editor',headers=headers).json()
            stopped = self.client.put(f'/api/inbounds/{identity}',headers=headers,
                json={'enable':False,'expected_instance_revision':current['mutation_revision']})
            self.assertEqual(stopped.status_code,200,stopped.text)
            self.assertFalse(runtime.status()['running'])
            revoked = self.client.delete(f'/api/maintenance/grants/{grant_id}',headers=auth_tests.HEADERS)
            self.assertEqual(revoked.status_code,200)
            self.assertEqual(self.client.get('/api/cores/status',headers=headers).status_code,401)
