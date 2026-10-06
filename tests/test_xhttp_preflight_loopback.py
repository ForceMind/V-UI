"""Characterization only: Xray/Mihomo XHTTP and the separate HTTPUpgrade gap.

No application compiler/exporter is enabled. Provider `-t` is deliberately not
called proof of URI payload support. Runtime is required in real isolated CI.
"""
from contextlib import ExitStack
import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import time
import unittest

import yaml

from loopback_helpers import (certificate_files, CoreProcess, http_through,
    require_rejection_evidence, start_http_target, unused_port)
from xhttp_helpers import (FAILURES, SNI, TARGET_BODY,
    classify_protocol_bytes, mihomo_config, mihomo_proxy, rejection_reason,
    require_httpupgrade_bad_request, share_uri, singbox_gap_server,
    singbox_xhttp_client, start_protocol_recorder, xray_server)


@unittest.skipUnless(os.getenv('VUI_TEST_CORES') and os.getenv('VUI_TEST_MIHOMO'),
                     'fixed official Xray, sing-box and Mihomo binaries required')
class XHTTPPreflightLoopbackTests(unittest.TestCase):
    clients = ('mihomo', 'mihomo-uri')

    @classmethod
    def setUpClass(cls):
        cls.xray = str(Path(os.environ['VUI_TEST_CORES'])/'xray')
        cls.singbox = str(Path(os.environ['VUI_TEST_CORES'])/'sing-box')
        cls.mihomo = os.environ['VUI_TEST_MIHOMO']
        for command, prefix in (([cls.xray, 'version'], 'Xray 26.3.27 '),
                ([cls.singbox, 'version'], 'sing-box version 1.14.2\n'),
                ([cls.mihomo, '-v'], 'Mihomo Meta v1.19.32 ')):
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            output = result.stdout+result.stderr
            if result.returncode or not output.startswith(prefix):
                raise AssertionError('Unexpected characterization binary: '+output)
            print(output.splitlines()[0])

    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-xhttp-preflight-')))
        self.ca, self.cert, self.key = certificate_files(self.root)
        self.wrong_ca, _, _ = certificate_files(self.root, 'wrong')
        (self.root/'empty-roots').mkdir()
        self.env = {**os.environ, 'SSL_CERT_FILE': str(self.ca),
                    'SSL_CERT_DIR': str(self.root/'empty-roots')}
        self.sequence = 0

    def unique_path(self, label, suffix):
        self.sequence += 1
        return self.root/f'{self.sequence}-{label}.{suffix}'

    def write_config(self, config, label):
        path = self.unique_path(label, 'json')
        path.write_text(json.dumps(config)); path.chmod(0o600)
        return path

    def checked(self, command, *, env=None, reason=None):
        result = subprocess.run(command, capture_output=True, text=True, timeout=20, env=env or self.env)
        output = result.stdout+result.stderr
        if reason is None:
            self.assertEqual(result.returncode, 0, output)
        else:
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn(reason, output.lower())
            print('XHTTP parser rejection: '+reason)
        return output

    def prepare_server(self, port, *, gap=None):
        if gap is None:
            config = xray_server(port, self.cert, self.key)
            path = self.write_config(config, 'xray-server')
            self.checked([self.xray, 'run', '-test', '-c', str(path)])
            return [self.xray, 'run', '-c', str(path)]
        config = singbox_gap_server(port, self.cert, self.key, httpupgrade=gap)
        path = self.write_config(config, 'httpupgrade' if gap else 'plain-tcp-control')
        self.checked([self.singbox, 'check', '-c', str(path)])
        return [self.singbox, 'run', '-c', str(path)]

    def prepare_client(self, client, port, server_port, *, failure=None, httpupgrade=False,
                       invalid_mode=False, network=None):
        home = self.unique_path('mihomo-home', 'data'); home.mkdir()
        if client == 'mihomo-uri':
            provider = home/'uri.txt'
            uri = share_uri(server_port, failure=failure, httpupgrade=httpupgrade)
            if invalid_mode: uri = uri.replace('mode=stream-one', 'mode=invalid-mode')
            provider.write_text(uri); provider.chmod(0o600)
            config = mihomo_config(port, provider=provider)
        else:
            self.assertEqual(client, 'mihomo')
            proxy = mihomo_proxy(server_port, failure=failure, httpupgrade=httpupgrade)
            if invalid_mode: proxy['xhttp-opts']['mode'] = 'invalid-mode'
            if network is not None: proxy['network'] = network
            config = mihomo_config(port, proxy=proxy)
        self.assertNotIn('DIRECT', json.dumps(config))
        self.assertEqual(config['rules'], ['MATCH,ONLY_PROXY'])
        path = self.unique_path(client, 'yaml')
        path.write_text(yaml.safe_dump(config, sort_keys=False)); path.chmod(0o600)
        env = self.env.copy()
        if failure == 'ca': env['SSL_CERT_FILE'] = str(self.wrong_ca)
        self.checked([self.mihomo, '-t', '-d', str(home), '-f', str(path)], env=env,
                     reason='xhttp mode invalid-mode' if invalid_mode and client == 'mihomo' else None)
        return [self.mihomo, '-d', str(home), '-f', str(path)], env

    def logs(self):
        return '\n'.join(path.name+':\n'+path.read_text(errors='replace')
                         for path in sorted(self.root.glob('*.log')))

    def start_target(self):
        self.target_port, self.requests = start_http_target(self.stack)
        connection = http.client.HTTPConnection('127.0.0.1', self.target_port, timeout=3)
        try:
            connection.request('GET', '/reachable-control'); response = connection.getresponse()
            self.assertEqual((response.status, response.read()), (200, TARGET_BODY))
        finally: connection.close()
        # Keep every positive and negative delivery. Never rebase to hide a late request.
        self.assertEqual(len(self.requests), 1)

    def verify_server_alpn(self, port, alpn='h2'):
        context = ssl.create_default_context(cafile=str(self.ca))
        context.set_alpn_protocols([alpn])
        with socket.create_connection(('127.0.0.1', port), timeout=3) as raw:
            with context.wrap_socket(raw, server_hostname=SNI) as tls:
                self.assertEqual(tls.selected_alpn_protocol(), alpn)
                print(f'XHTTP separate verified server TLS probe: {tls.version()} / {alpn}')
        # This probe establishes server ALPN, not client packet capture. Actual
        # forwarding below uses explicit h2 and the pinned no-fallback client.

    def assert_success(self, port):
        before = len(self.requests); last = None
        deadline = time.monotonic()+8
        while time.monotonic() < deadline:
            try:
                last = http_through(port, '127.0.0.1', self.target_port)
                if last == (200, TARGET_BODY):
                    self.assertGreater(len(self.requests), before)
                    return
            except (OSError, http.client.HTTPException) as exc: last = exc
            time.sleep(.08)
        self.fail(f'Characterization HTTP forwarding failed: {last}\n{self.logs()}')

    def assert_failure(self, port, baseline):
        self.assertEqual(len(self.requests), baseline, 'Rejected application request arrived late')
        try:
            status, _ = http_through(port, '127.0.0.1', self.target_port)
            self.assertGreaterEqual(status, 400, self.logs())
        except (OSError, http.client.HTTPException): pass
        time.sleep(.1)
        self.assertEqual(len(self.requests), baseline, 'Rejected application traffic reached target\n'+self.logs())

    def verify_clients(self, failure=None):
        self.start_target()
        server_port = unused_port()
        with ExitStack() as server_stack:
            server = CoreProcess(server_stack, self.prepare_server(server_port), self.unique_path('server', 'log'), self.env)
            server.start(server_port); self.verify_server_alpn(server_port)
            for client in self.clients:
                with self.subTest(client=client, failure=failure):
                    for mutation in ((None, failure) if failure else (None,)):
                        with ExitStack() as client_stack:
                            port = unused_port()
                            command, env = self.prepare_client(client, port, server_port, failure=mutation)
                            process = CoreProcess(client_stack, command, self.unique_path(client, 'log'), env)
                            process.start(port)
                            if mutation is None:
                                self.assert_success(port)
                                self.assertIsNone(process.process.poll(), self.logs())
                                print(f'XHTTP stream-one/h2 {client}: real HTTP body and application delivery; no DIRECT')
                            else:
                                baseline = len(self.requests)
                                side, reasons = rejection_reason(mutation)
                                log_path = server.log_path if side == 'server' else process.log_path
                                offset = len(log_path.read_text(errors='replace'))
                                self.assert_failure(port, baseline)
                                log = require_rejection_evidence(self, log_path,
                                    lambda: self.assert_failure(port, baseline), self.requests, reasons,
                                    expected_count=baseline, log_offset=offset, label='XHTTP '+mutation+' rejection')
                                line = next(line for line in log.splitlines() if all(reason in line for reason in reasons))
                                print(f'XHTTP {client} {mutation}: {line}; application delta=0; no DIRECT')
                                process.stop(); time.sleep(.15)
                                self.assertEqual(len(self.requests), baseline, 'Late rejected application delivery')
                            self.assertIsNone(server.process.poll(), self.logs())

    def observe_httpupgrade_protocols(self):
        captured = None
        for client, expected in (('mihomo', 'httpupgrade'), ('mihomo-uri', 'vless')):
            baseline = len(self.requests)
            with ExitStack() as observation_stack:
                recorder_port, observations = start_protocol_recorder(observation_stack, self.cert, self.key)
                port = unused_port()
                command, env = self.prepare_client(client, port, recorder_port, httpupgrade=True)
                process = CoreProcess(observation_stack, command, self.unique_path('wire-'+client, 'log'), env)
                process.start(port)
                # This no-forward recorder is not a proxy success/rejection
                # oracle. Only actual complete protocol bytes count below.
                self.assert_failure(port, baseline)
                deadline = time.monotonic()+3
                records = observations.snapshot()
                while not records and time.monotonic() < deadline:
                    time.sleep(.02); records = observations.snapshot()
                self.assertTrue(records, 'No actual protocol bytes observed')
                for record in records:
                    self.assertTrue('error' not in record, 'Protocol recorder reported incomplete or failed TLS observation')
                    self.assertEqual(record['sni'], SNI)
                    self.assertEqual(record['alpn'], 'http/1.1')
                    self.assertIn(record['tls'], ('TLSv1.2', 'TLSv1.3'))
                    self.assertEqual(classify_protocol_bytes(record['payload'], self.target_port), expected)
                if expected == 'vless': captured = records[0]['payload']
                process.stop(); time.sleep(.1)
                self.assertEqual(len(self.requests), baseline, 'Recorder traffic reached application target')
                self.assertTrue(observations.snapshot() == records, 'Recorder changed after observation boundary')
                print(f'HTTPUpgrade separate wire recorder {client}: verified TLS/SNI/http1.1; '
                      f'actual {expected} bytes; application delta=0; no forwarding')
        self.assertIsNotNone(captured)
        return captured

    def replay_actual_bad_request(self, server_port, captured, baseline):
        # Replay the exact bytes recorded from the real untouched URI importer.
        # This is a separate observation, not a TLS relay or captured direct-run
        # response. The actual fixed server, not the recorder, supplies HTTP400.
        self.assertEqual(classify_protocol_bytes(captured, self.target_port), 'vless')
        context = ssl.create_default_context(cafile=str(self.ca))
        context.set_alpn_protocols(['http/1.1'])
        with socket.create_connection(('127.0.0.1', server_port), timeout=3) as raw:
            with context.wrap_socket(raw, server_hostname=SNI) as tls:
                self.assertEqual(tls.selected_alpn_protocol(), 'http/1.1')
                tls.sendall(captured)
                response = http.client.HTTPResponse(tls)
                response.begin()
                self.assertEqual(response.version, 11)
                body = response.read(4097)
                require_httpupgrade_bad_request(response.status, response.reason, body)
        self.assertEqual(len(self.requests), baseline, 'Wrong-protocol replay reached application target')
        print('HTTPUpgrade separate unchanged-byte replay: real fixed sing-box HTTP/1.1 400 Bad Request; '
              'verified TLS/SNI/http1.1; application delta=0')

    def test_00_bounded_parser_checks_and_singbox_gap(self):
        self.prepare_server(19443)
        for client in self.clients:
            for failure in (None, *FAILURES):
                with self.subTest(client=client, failure=failure):
                    self.prepare_client(client, 19444, 19443, failure=failure)
        path = self.write_config(singbox_xhttp_client(19443), 'singbox-unsupported-xhttp')
        self.checked([self.singbox, 'check', '-c', str(path)], reason='unknown transport type: xhttp')
        print('XHTTP native configs parse; provider shell parse is not URI payload acceptance; sing-box unsupported')

    def test_01_invalid_mode_and_provider_parser_limitation(self):
        config = xray_server(19443, self.cert, self.key)
        config['inbounds'][0]['streamSettings']['xhttpSettings']['mode'] = 'invalid-mode'
        path = self.write_config(config, 'invalid-xray-mode')
        self.checked([self.xray, 'run', '-test', '-c', str(path)], reason='unsupported mode: invalid-mode')
        self.prepare_client('mihomo', 19444, 19443, invalid_mode=True)
        self.prepare_client('mihomo-uri', 19444, 19443, invalid_mode=True)
        print('Mihomo -t rejects native invalid-mode but accepts the same invalid URI provider shell; runtime mandatory')

    def test_02_httpupgrade_native_and_unknown_network_parser_checks(self):
        self.prepare_server(19443, gap=True)
        self.prepare_client('mihomo', 19444, 19443, httpupgrade=True)
        self.prepare_client('mihomo-uri', 19444, 19443, httpupgrade=True)
        for network in ('httpupgrade', 'not-a-transport'):
            self.prepare_client('mihomo', 19444, 19443, httpupgrade=True, network=network)
        print('Mihomo accepts arbitrary network names; parser success does not establish HTTPUpgrade')

    def test_03_httpupgrade_verified_wire_recorder(self):
        self.start_target()
        self.observe_httpupgrade_protocols()

    def test_verified_http_forwarding(self): self.verify_clients()
    def test_wrong_uuid_rejected(self): self.verify_clients('uuid')
    def test_wrong_ca_rejected(self): self.verify_clients('ca')
    def test_wrong_sni_rejected(self): self.verify_clients('sni')
    def test_wrong_path_rejected(self): self.verify_clients('path')
    def test_wrong_host_rejected(self): self.verify_clients('host')
    def test_wrong_valid_mode_rejected(self): self.verify_clients('mode')

    def test_httpupgrade_actual_uri_wrong_transport_control(self):
        self.start_target()
        captured = self.observe_httpupgrade_protocols()
        # The URI remains type=httpupgrade throughout. Changing only the fixture
        # server demonstrates the wrong TCP semantics; it is not a substitute export.
        for upgrade in (True, False):
            with self.subTest(server='httpupgrade' if upgrade else 'wrong-transport-plain-tcp-control'):
                server_port = unused_port()
                with ExitStack() as server_stack:
                    server = CoreProcess(server_stack, self.prepare_server(server_port, gap=upgrade),
                                         self.unique_path('gap-server', 'log'), self.env)
                    server.start(server_port); self.verify_server_alpn(server_port, 'http/1.1')
                    clients = ('mihomo', 'mihomo-uri') if upgrade else ('mihomo-uri',)
                    for client in clients:
                        with ExitStack() as client_stack:
                            port = unused_port()
                            command, env = self.prepare_client(client, port, server_port, httpupgrade=True)
                            process = CoreProcess(client_stack, command, self.unique_path('gap-'+client, 'log'), env)
                            process.start(port)
                            if upgrade and client == 'mihomo-uri':
                                baseline = len(self.requests)
                                self.assert_failure(port, baseline)
                                self.replay_actual_bad_request(server_port, captured, baseline)
                                print('HTTPUpgrade direct untouched URI: application delta=0; no DIRECT; '
                                      'wrong VLESS bytes and actual parser HTTP400 proven by separate observations')
                                process.stop(); time.sleep(.15)
                                self.assertEqual(len(self.requests), baseline)
                            else:
                                self.assert_success(port)
                                self.assertIsNone(process.process.poll(), self.logs())
                                label = 'canonical YAML HTTPUpgrade' if upgrade else 'URI WRONG-TRANSPORT plain TCP control'
                                print(label+': real HTTP body; no DIRECT; no public support unlocked')
                            self.assertIsNone(server.process.poll(), self.logs())


if __name__ == '__main__': unittest.main()
