"""Bare pinned VLESS/TCP/REALITY/Vision compatibility before public support.

Only HTTP/TCP application forwarding is qualified. Reference/camouflage traffic
is expected and counted separately; no third-party handshake site is contacted.
"""
import base64
from contextlib import ExitStack
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unittest
from urllib.parse import urlencode

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
import yaml

from loopback_helpers import CoreProcess, http_through, start_http_target, unused_port, require_rejection_evidence
from reality_helpers import SNI, WRONG_SNI, reference_certificates, start_reference_target

UUID = '11111111-1111-4111-8111-111111111111'
WRONG_UUID = '22222222-2222-4222-8222-222222222222'
FLOW = 'xtls-rprx-vision'
SHORT_ID = '0123456789abcdef'
WRONG_SHORT_ID = 'fedcba9876543210'
TARGET_BODY = b'VUI-LOOPBACK-TARGET'
FAILURES = ('uuid', 'flow', 'public_key', 'short_id', 'sni')


def keypair():
    private = X25519PrivateKey.generate()
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b'=').decode()
    return (encode(private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                          serialization.NoEncryption())),
            encode(private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)))


def safe_log(value, keys=()):
    for key in keys: value = value.replace(key, '[fake key redacted]')
    return re.sub(r'(?im)^.*(?:AuthKey|auth_key|hello\.sessionId).*$', '[derived key diagnostic redacted]', value)


@unittest.skipUnless(os.getenv('VUI_TEST_CORES') and os.getenv('VUI_TEST_MIHOMO'),
                     'pinned real sing-box and Mihomo binaries required')
class RealityPreflightLoopbackTests(unittest.TestCase):
    clients = ('mihomo', 'singbox', 'mihomo-uri')

    @classmethod
    def setUpClass(cls):
        cls.singbox = str(Path(os.environ['VUI_TEST_CORES']) / 'sing-box')
        cls.mihomo = os.environ['VUI_TEST_MIHOMO']
        for command, prefix in (([cls.singbox, 'version'], 'sing-box version 1.14.2\n'),
                                ([cls.mihomo, '-v'], 'Mihomo Meta v1.19.32 ')):
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            output = result.stdout + result.stderr
            if result.returncode or not output.startswith(prefix): raise AssertionError(output)
            if command[0] == cls.singbox and 'with_utls' not in output: raise AssertionError('with_utls missing')
            print(output.splitlines()[0])

    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-reality-preflight-')))
        self.ca, self.cert, self.key = reference_certificates(self.root)
        (self.root/'empty-roots').mkdir()
        self.env = {**os.environ, 'SSL_CERT_FILE': str(self.ca), 'SSL_CERT_DIR': str(self.root/'empty-roots')}
        self.private_key, self.public_key = keypair()
        _, self.wrong_public_key = keypair()
        self.reference_port = 19445
        self.sequence = 0

    def unique_path(self, label, suffix):
        self.sequence += 1
        return self.root/f'{self.sequence}-{label}.{suffix}'

    def sanitize(self, value):
        return safe_log(value, (self.private_key, self.public_key, self.wrong_public_key))

    def logs(self):
        return self.sanitize('\n'.join(path.name+':\n'+path.read_text(errors='replace')
                                        for path in sorted(self.root.glob('*.log'))))

    def start_process(self, process, port):
        try:
            process.start(port)
        except AssertionError as exc:
            # Shared helpers include raw process logs in assertion messages.
            # Suppress the original exception context as well as its key bytes.
            raise AssertionError(self.sanitize(str(exc))) from None

    def server_config(self, port):
        return {'log': {'level': 'debug', 'timestamp': False},
            'inbounds': [{'type': 'vless', 'tag': 'reality-in', 'listen': '127.0.0.1', 'listen_port': port,
                'users': [{'uuid': UUID, 'flow': FLOW}], 'tls': {'enabled': True, 'server_name': SNI,
                    'reality': {'enabled': True, 'handshake': {'server': '127.0.0.1', 'server_port': self.reference_port},
                                'private_key': self.private_key, 'short_id': [SHORT_ID]}}}],
            'outbounds': [{'type': 'direct', 'tag': 'application'}], 'route': {'final': 'application'}}

    def client_config(self, client, port, server_port, *, failure=None):
        uid = WRONG_UUID if failure == 'uuid' else UUID
        flow = '' if failure == 'flow' else FLOW
        public = self.wrong_public_key if failure == 'public_key' else self.public_key
        short = WRONG_SHORT_ID if failure == 'short_id' else SHORT_ID
        sni = WRONG_SNI if failure == 'sni' else SNI
        if client == 'mihomo-uri':
            params = {'security': 'reality', 'type': 'tcp', 'encryption': 'none', 'sni': sni,
                      'fp': 'chrome', 'pbk': public, 'sid': short}
            if flow: params['flow'] = flow
            path = self.unique_path('reality-provider', 'txt')
            path.write_text(f'vless://{uid}@127.0.0.1:{server_port}?'+urlencode(params)+'#REALITY_URI')
            path.chmod(0o600)
            config = {'mixed-port': port, 'bind-address': '127.0.0.1', 'allow-lan': False,
                'mode': 'rule', 'log-level': 'debug', 'ipv6': False,
                'proxy-providers': {'reality-uri': {'type': 'file', 'path': str(path)}},
                'proxy-groups': [{'name': 'REALITY_ONLY', 'type': 'select', 'use': ['reality-uri']}],
                'rules': ['MATCH,REALITY_ONLY']}
        elif client == 'mihomo':
            config = {'mixed-port': port, 'bind-address': '127.0.0.1', 'allow-lan': False,
                'mode': 'rule', 'log-level': 'debug', 'ipv6': False,
                'proxies': [{'name': 'REALITY_ONLY', 'type': 'vless', 'server': '127.0.0.1', 'port': server_port,
                    'uuid': uid, 'flow': flow, 'network': 'tcp', 'udp': False, 'tls': True, 'servername': sni,
                    'client-fingerprint': 'chrome', 'skip-cert-verify': False,
                    'reality-opts': {'public-key': public, 'short-id': short}}], 'rules': ['MATCH,REALITY_ONLY']}
        else:
            self.assertEqual(client, 'singbox')
            config = {'log': {'level': 'debug', 'timestamp': False},
                'inbounds': [{'type': 'mixed', 'listen': '127.0.0.1', 'listen_port': port}],
                'outbounds': [{'type': 'vless', 'tag': 'REALITY_ONLY', 'server': '127.0.0.1', 'server_port': server_port,
                    'uuid': uid, 'flow': flow, 'network': 'tcp', 'tls': {'enabled': True, 'server_name': sni,
                        'utls': {'enabled': True, 'fingerprint': 'chrome'},
                        'reality': {'enabled': True, 'public_key': public, 'short_id': short}}}],
                'route': {'final': 'REALITY_ONLY'}}
        self.assertNotIn('DIRECT', json.dumps(config))
        if client == 'singbox': self.assertEqual([x['type'] for x in config['outbounds']], ['vless'])
        return config

    def check_command(self, command):
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, env=self.env)
        self.assertEqual(result.returncode, 0, self.sanitize(result.stdout+result.stderr))

    def prepare_server(self, port):
        path = self.unique_path('server', 'json')
        path.write_text(json.dumps(self.server_config(port))); path.chmod(0o600)
        self.check_command([self.singbox, 'check', '-c', str(path)])
        return [self.singbox, 'run', '-c', str(path)]

    def prepare_client(self, client, port, server_port, **options):
        config = self.client_config(client, port, server_port, **options)
        if client.startswith('mihomo'):
            path = self.unique_path(client, 'yaml')
            home = self.unique_path('mihomo-home', 'data'); home.mkdir()
            for name, provider in config.get('proxy-providers', {}).items():
                dest = home/(name+'.txt'); dest.write_bytes(Path(provider['path']).read_bytes()); dest.chmod(0o600)
                provider['path'] = str(dest)
            path.write_text(yaml.safe_dump(config, sort_keys=False)); path.chmod(0o600)
            self.check_command([self.mihomo, '-t', '-d', str(home), '-f', str(path)])
            command = [self.mihomo, '-d', str(home), '-f', str(path)]
        else:
            path = self.unique_path(client, 'json')
            path.write_text(json.dumps(config)); path.chmod(0o600)
            self.check_command([self.singbox, 'check', '-c', str(path)])
            command = [self.singbox, 'run', '-c', str(path)]
        return command, self.env.copy()

    def assert_success(self, port):
        before = len(self.requests); last = None
        deadline = time.monotonic()+8
        while time.monotonic() < deadline:
            try:
                last = http_through(port, '127.0.0.1', self.target_port)
                if last == (200, TARGET_BODY):
                    self.assertGreater(len(self.requests), before); return
            except (OSError, http.client.HTTPException) as exc: last = exc
            time.sleep(.08)
        self.fail(f'REALITY/Vision HTTP forwarding failed: {last}\n{self.logs()}')

    def assert_failure(self, port):
        self.assertEqual(self.requests, [], 'A rejected application request arrived late')
        try:
            status, _ = http_through(port, '127.0.0.1', self.target_port)
            self.assertGreaterEqual(status, 400, self.logs())
        except (OSError, http.client.HTTPException): pass
        time.sleep(.1)
        self.assertEqual(self.requests, [], 'Rejected application traffic reached target\n'+self.logs())

    def failure_evidence(self, client, process, failure, port, server, offset):
        if failure in ('uuid', 'flow'):
            reasons = ('unknown uuid:', WRONG_UUID) if failure == 'uuid' else ('flow mismatch:', 'expected '+FLOW, 'got none')
            log_path = server.log_path
        else:
            reasons = ('reality verification failed',) if client == 'singbox' else ('reality authentication failed',)
            log_path = process.log_path; offset = 0
        try:
            log = require_rejection_evidence(self, log_path, lambda: self.assert_failure(port), self.requests,
                                            reasons, log_offset=offset, label='REALITY/VLESS rejection')
        except AssertionError as exc:
            raise AssertionError(self.sanitize(str(exc))) from None
        evidence = next(line for line in log.splitlines() if all(reason in line for reason in reasons))
        print(f'REALITY {client} {failure}: '+self.sanitize(evidence))

    def verify_clients(self, *, failure=None):
        self.target_port, self.requests = start_http_target(self.stack)
        control = http.client.HTTPConnection('127.0.0.1', self.target_port, timeout=3)
        try:
            control.request('GET', '/reachable-control'); response = control.getresponse()
            self.assertEqual((response.status, response.read()), (200, TARGET_BODY))
        finally: control.close()
        self.assertEqual(len(self.requests), 1); self.requests.clear()
        self.reference_port, traffic = start_reference_target(self.stack, self.cert, self.key)
        with ExitStack() as processes:
            server_port = unused_port()
            server = CoreProcess(processes, self.prepare_server(server_port), self.unique_path('server', 'log'), self.env)
            self.start_process(server, server_port)
            for client in self.clients:
                with self.subTest(client=client, failure=failure):
                    for mutation in ((None, failure) if failure else (None,)):
                        port = unused_port()
                        command, env = self.prepare_client(client, port, server_port, failure=mutation)
                        process = CoreProcess(processes, command, self.unique_path(client, 'log'), env)
                        self.start_process(process, port)
                        before = traffic.snapshot()
                        try:
                            if mutation is None:
                                self.assert_success(port)
                            else:
                                self.requests.clear()
                                offset = len(server.log_path.read_text(errors='replace'))
                                self.assert_failure(port)
                                self.failure_evidence(client, process, mutation, port, server, offset)
                            after = traffic.snapshot()
                            self.assertGreater(len(after['client_hellos']), len(before['client_hellos']))
                            expected_sni = WRONG_SNI if mutation == 'sni' else SNI
                            self.assertIn(expected_sni, after['client_hellos'][len(before['client_hellos']):])
                            if mutation in ('public_key', 'short_id', 'sni'):
                                deadline = time.monotonic()+4
                                while len(after['handshakes']) <= len(before['handshakes']) and time.monotonic() < deadline:
                                    time.sleep(.05); after = traffic.snapshot()
                                # The clients start their camouflage GET in a
                                # goroutine, then the failed dial's caller can
                                # close the connection first. TLS fallback is
                                # observable; completed HTTP HEADERS are not
                                # guaranteed and must only be counted, not forced.
                                self.assertGreater(len(after['handshakes']), len(before['handshakes']), self.logs())
                                self.assertTrue(all(x == ('TLSv1.3', 'h2') for x in after['handshakes']))
                            if mutation: self.assertEqual(self.requests, [], 'Late application delivery')
                            self.assertIsNone(process.process.poll(), self.logs())
                            self.assertIsNone(server.process.poll(), self.logs())
                            print(f'REALITY {client} {mutation or "HTTP success"}: application requests={len(self.requests)}; '
                                  f'reference ClientHellos={len(after["client_hellos"])-len(before["client_hellos"])}; '
                                  f'reference completed TLS={len(after["handshakes"])-len(before["handshakes"])}; '
                                  f'camouflage HEADERS={len(after["camouflage"])-len(before["camouflage"])}; no DIRECT')
                        finally: process.stop()
                    self.requests.clear()

    def test_00_bare_pinned_config_checks(self):
        self.prepare_server(19443)
        for client in self.clients:
            for failure in (None, *FAILURES):
                with self.subTest(client=client, failure=failure): self.prepare_client(client, 19444, 19443, failure=failure)
        print('REALITY bare server, both clients and actual URI provider accept bounded configuration')

    def test_verified_http_forwarding(self): self.verify_clients()
    def test_wrong_uuid_rejected(self): self.verify_clients(failure='uuid')
    def test_missing_flow_rejected(self): self.verify_clients(failure='flow')
    def test_wrong_valid_public_key_rejected(self): self.verify_clients(failure='public_key')
    def test_wrong_short_id_rejected(self): self.verify_clients(failure='short_id')
    def test_disallowed_sni_rejected(self): self.verify_clients(failure='sni')


if __name__ == '__main__': unittest.main()
