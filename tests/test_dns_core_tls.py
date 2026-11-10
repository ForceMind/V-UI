"""Actual pinned cores consume verified root+wildcard material with literal SNI.

Issuance alone uses the existing synthetic signing provider (not DNS/Certbot).
Adapters, runtime validation/launch, served leaf, stock Mihomo and HTTP/TCP are
real only under the explicit Actions flag. No browser, public DNS or host trust.
"""
from contextlib import ExitStack
from copy import deepcopy
import os
from pathlib import Path
import socket
import ssl
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import serialization
import yaml

from tests.core_candidate_context import runtime_for_fixture
from app.certificates.dns_store import DNSCredentialStore
from app.certificates.models import CertificateBinding
from app.models import database
from app.services.core_manager import SingBoxAdapter, XrayAdapter, core_manager
from app.services.core_runtime import CoreRuntime
from app.services.managed_tls import paths as material_paths, server_name
from app.services.validated_export import validated_node
from inbound_routing_real_helpers import yaml_client
from loopback_helpers import start_http_target, unused_port, http_through, require_tls_rejection
from proxy_loopback_support import ReadyMihomoProcess
from test_real_cores import DiagnosticXrayRuntime
import test_certificates as certificate_fixtures

HOST = 'node.example.test'
DEEP = 'a.node.example.test'
UUID = '77777777-7777-4777-8777-777777777777'


@unittest.skipUnless(os.getenv('VUI_DNS_CORE_TLS') == '1', 'explicit Actions DNS/core TLS gate')
class DNSCoreTLSTests(unittest.TestCase):
    login = certificate_fixtures.CertificateTests.login
    due = certificate_fixtures.CertificateTests.due

    def setUp(self):
        # Enabling the gate with absent binaries fails, never becomes a skip.
        self.assertTrue(os.getenv('VUI_TEST_CORES'), 'Pinned server binaries required')
        self.assertTrue(os.getenv('VUI_TEST_MIHOMO'), 'Pinned stock Mihomo required')
        for path in (Path(os.environ['VUI_TEST_CORES']) / 'sing-box',
                     Path(os.environ['VUI_TEST_CORES']) / 'xray', Path(os.environ['VUI_TEST_MIHOMO'])):
            self.assertTrue(path.is_file(), 'Missing pinned binary: ' + str(path))
        certificate_fixtures.CertificateTests.setUp(self)
        self.ports = set()

    def port(self):
        for _ in range(32):
            port = unused_port()
            if port not in self.ports:
                self.ports.add(port)
                return port
        self.fail('No distinct fixture port available')

    def node(self, identity):
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, identity)
            node = validated_node(row, '127.0.0.1')
            saved = {key: deepcopy(getattr(row, key)) for key in
                     ('core', 'protocol', 'port', 'tag', 'remark', 'enable', 'expiry_time', 'routing_mode', 'settings')}
            self.assertEqual(server_name(row.core, row.stream_settings), HOST)
            static_stream = deepcopy(row.stream_settings)
            if row.core == 'sing-box':
                static_stream['tls'].pop('certificate_path'); static_stream['tls'].pop('key_path')
            else:
                for certificate in static_stream['tlsSettings']['certificates']:
                    certificate.pop('certificateFile'); certificate.pop('keyFile')
            saved['stream_without_material_paths'] = static_stream
            return node, saved, material_paths(row.core, row.stream_settings)

    def start_client(self, stack, root, label, node, env):
        port = self.port()
        config = yaml_client({'proxies': [node]}, [node['name']], port, self.port())
        self.assertEqual(config['rules'], ['MATCH,ENTRY'])
        self.assertTrue(all('DIRECT' not in group['proxies'] for group in config['proxy-groups']))
        self.assertFalse(config['proxies'][0]['skip-cert-verify'])
        path, log = root / (label + '.yaml'), root / (label + '.log')
        path.write_text(yaml.safe_dump(config, sort_keys=False))
        process = ReadyMihomoProcess(stack, [os.environ['VUI_TEST_MIHOMO'], '-d', str(root / (label + '-data')),
                                            '-f', str(path)], log, env)
        process.start(port)
        return process, port, log

    def served_leaf(self, port, ca, fullchain):
        leaf = x509.load_pem_x509_certificates(fullchain.read_bytes())[0]
        self.assertEqual(set(leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                             .get_values_for_type(x509.DNSName)), {'example.test', '*.example.test'})
        context = ssl.create_default_context(cafile=str(ca))
        with socket.create_connection(('127.0.0.1', port), timeout=3) as stream:
            with context.wrap_socket(stream, server_hostname=HOST) as tls:
                self.assertEqual(tls.getpeercert(binary_form=True), leaf.public_bytes(serialization.Encoding.DER))
        return leaf.serial_number

    def exercise(self, core):
        with ExitStack() as stack:
            root = Path(self.temp.name) / ('dns-core-' + core); root.mkdir(mode=0o700)
            target_port, deliveries = start_http_target(stack); self.ports.add(target_port)
            self.provider.supports_dns = True
            profile = DNSCredentialStore(self.manager.root).create(label='synthetic-core-tls', zone_name='example.test',
                zone_id='d' * 32, api_token='fake_core_tls_token_' + 'x' * 32)
            job = self.manager.create('example.test', 'admin@example.test', 'production', True, True,
                                      method='dns-01', wildcard=True, credential_id=profile['id'])
            self.manager.process_once()
            self.assertEqual(self.manager.job(job['id'])['state'], 'succeeded', self.manager.job(job['id']))
            certificate = job['certificate_id']
            paths, _, revision = self.manager.material(certificate)
            ca = root / 'private-roots.pem'; ca.write_bytes(self.provider.ca.public_bytes(serialization.Encoding.PEM)); ca.chmod(0o600)
            empty = root / 'empty-roots'; empty.mkdir(mode=0o700)
            env = {**os.environ, 'SSL_CERT_FILE': str(ca), 'SSL_CERT_DIR': str(empty)}
            port = self.port()
            if core == 'sing-box':
                settings = {'users': [{'uuid': UUID}]}
                stream = {'tls': {'enabled': True, 'server_name': HOST, 'certificate_path': str(paths[0]), 'key_path': str(paths[1])}}
                adapter = SingBoxAdapter()
                runtime = runtime_for_fixture(CoreRuntime, core, Path(os.environ['VUI_TEST_CORES']) / core, root / 'runtime')
            else:
                settings = {'users': [{'id': UUID}], 'decryption': 'none'}
                stream = {'network': 'raw', 'security': 'tls', 'tlsSettings': {'serverName': HOST,
                          'certificates': [{'certificateFile': str(paths[0]), 'keyFile': str(paths[1])}]}}
                adapter = XrayAdapter()
                runtime = runtime_for_fixture(DiagnosticXrayRuntime, core, Path(os.environ['VUI_TEST_CORES']) / core, root / 'runtime')
            stream['_vui'] = {'security': 'tls', 'server_name': HOST, 'skip_cert_verify': False}
            stack.callback(runtime.close); adapter._runtime = runtime
            with database.SessionLocal() as db:
                row = database.Inbound(core=core, protocol='vless', port=port, settings=settings,
                    stream_settings=stream, enable=True, expiry_time=0, routing_mode='inherit',
                    remark='synthetic-wildcard-' + core, tag='wildcard-' + core)
                db.add(row); db.commit(); identity = row.id
            def apply(selected, *, activate=True, **kwargs):
                self.assertEqual(selected, core)
                with database.SessionLocal() as db:
                    config = adapter.build_config(db.query(database.Inbound).filter_by(core=core).all())
                # Listener address is the only test-local server endpoint change.
                for inbound in config['inbounds']: inbound['listen'] = '127.0.0.1'
                return runtime.apply(config, activate=activate)
            stack.enter_context(patch.dict(core_manager.adapters, {core: adapter}))
            stack.enter_context(patch.object(core_manager, 'validate_inbound_change'))
            stack.enter_context(patch.object(core_manager, 'apply_database', side_effect=apply))
            apply(core)
            self.manager.bind(certificate, 'inbound:' + str(identity), server_name=HOST)
            node, original, original_paths = self.node(identity)
            self.assertEqual(node['uuid'], UUID); self.assertEqual(node['servername'], HOST)
            first_serial = self.served_leaf(port, ca, paths[0])
            first_client, proxy_port, _ = self.start_client(stack, root, 'first', node, env)
            self.assertEqual(http_through(proxy_port, '127.0.0.1', target_port), (200, b'VUI-LOOPBACK-TARGET'))
            self.assertEqual(len(deliveries), 1); first_client.stop()
            runtime.stop()
            self.assertFalse(runtime.status()['running']); self.assertFalse(runtime.state()['enabled'])
            self.due(certificate); self.manager.process_once()
            renewed, _, new_revision = self.manager.material(certificate)
            self.assertNotEqual(new_revision, revision)
            self.assertNotEqual(tuple(map(str, renewed)), tuple(map(str, paths)))
            self.assertFalse(runtime.status()['running'], 'Renewal resurrected a stopped actual core')
            self.assertFalse(runtime.state()['enabled'])
            with database.SessionLocal() as db:
                binding = db.get(CertificateBinding, 'inbound:' + str(identity))
                self.assertEqual(binding.server_name, HOST)
                self.assertEqual(binding.applied_revision, revision)
                self.assertEqual(binding.error, 'CORE_STOPPED_PENDING_APPLY')
            after, saved, changed_paths = self.node(identity)
            self.assertEqual(saved, original); self.assertEqual(after, node)
            self.assertNotEqual(changed_paths, original_paths)
            apply(core)  # Explicit restart, rather than an implicit renewal start.
            self.assertEqual(self.manager.apply_existing(certificate), [{'target': 'inbound:' + str(identity), 'applied': True}])
            self.assertNotEqual(self.served_leaf(port, ca, renewed[0]), first_serial)
            second_client, proxy_port, _ = self.start_client(stack, root, 'renewed', after, env)
            self.assertEqual(http_through(proxy_port, '127.0.0.1', target_port), (200, b'VUI-LOOPBACK-TARGET'))
            self.assertEqual(len(deliveries), 2); second_client.stop()
            bad = deepcopy(after); bad['servername'] = DEEP
            rejected, proxy_port, log = self.start_client(stack, root, 'deep-name', bad, env)
            baseline = len(deliveries)
            def attempt():
                try:
                    status, _ = http_through(proxy_port, '127.0.0.1', target_port)
                    self.assertNotEqual(status, 200)
                except OSError:
                    pass
            evidence = require_tls_rejection(self, log, attempt, deliveries, DEEP, expected_count=baseline)
            self.assertIn('x509', evidence); self.assertNotIn('using DIRECT', log.read_text())
            rejected.stop(); runtime.stop()
            self.assertEqual(len(deliveries), baseline, 'Deep-name rejection delivered HTTP, possibly via DIRECT')
            self.assertEqual(self.node(identity)[:2], (after, original))

    def test_sing_box_actual_dns_wildcard_tls_rotation_and_deep_name_rejection(self):
        self.exercise('sing-box')

    def test_xray_actual_dns_wildcard_tls_rotation_and_deep_name_rejection(self):
        self.exercise('xray')
