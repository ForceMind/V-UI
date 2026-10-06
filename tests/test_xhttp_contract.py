"""The characterization fixture must not unlock lossy production exports."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock, patch
import uuid
from urllib.parse import parse_qs, urlsplit

from app.services.mihomo_subscription import mihomo_config as public_mihomo_config
from app.services.validated_export import ExportError, base64_subscription, singbox_client_config
from test_validated_export import vless_node
import test_tls_rejection_evidence as evidence_tests
from loopback_helpers import require_rejection_evidence
from xhttp_helpers import (FAILURES, HOST, MODE, PATH, SNI, UUID, WIRE_LIMIT,
    ProtocolObservations, classify_protocol_bytes, read_protocol_bytes,
    require_httpupgrade_bad_request, mihomo_config,
    mihomo_proxy, rejection_reason, share_uri, singbox_gap_server,
    singbox_xhttp_client, xray_server)


class XHTTPContractTests(unittest.TestCase):
    def test_one_explicit_mode_and_independent_tls_host_fields(self):
        server = xray_server(19443, '/tmp/fake-cert', '/tmp/fake-key')['inbounds'][0]
        stream = server['streamSettings']
        self.assertEqual(server['settings'], {'clients': [{'id': UUID}], 'decryption': 'none'})
        self.assertEqual(stream['network'], 'xhttp')
        self.assertEqual(stream['security'], 'tls')
        self.assertEqual(stream['xhttpSettings'], {'path': PATH, 'host': HOST, 'mode': MODE})
        self.assertEqual(stream['tlsSettings']['alpn'], ['h2'])
        proxy = mihomo_proxy(19443)
        self.assertEqual(proxy['network'], 'xhttp')
        self.assertEqual(proxy['xhttp-opts'], stream['xhttpSettings'])
        self.assertFalse(proxy['skip-cert-verify']); self.assertFalse(proxy['udp'])
        self.assertEqual(proxy['client-fingerprint'], 'chrome')
        self.assertEqual(proxy['alpn'], ['h2']); self.assertNotIn('flow', proxy)
        self.assertEqual(mihomo_proxy(19443, failure='sni')['xhttp-opts']['host'], HOST)
        self.assertEqual(mihomo_proxy(19443, failure='host')['servername'], SNI)

    def test_uri_is_actual_provider_input_without_override(self):
        for failure in (None, *FAILURES):
            with self.subTest(failure=failure):
                uri = urlsplit(share_uri(19443, failure=failure)); params = parse_qs(uri.query)
                proxy = mihomo_proxy(19443, failure=failure)
                self.assertEqual(uri.username, proxy['uuid'])
                self.assertEqual(params['type'], ['xhttp'])
                self.assertEqual(params['sni'], [proxy['servername']])
                self.assertEqual(params['fp'], ['chrome']); self.assertEqual(params['alpn'], ['h2'])
                for key in ('path', 'host', 'mode'):
                    self.assertEqual(params[key], [proxy['xhttp-opts'][key]])
                self.assertNotIn('flow', params); self.assertNotIn('extra', params)
        config = mihomo_config(19444, provider='/tmp/fake-provider.txt')
        self.assertEqual(config['proxy-providers'], {'uri': {'type': 'file', 'path': '/tmp/fake-provider.txt'}})
        self.assertEqual(config['proxy-groups'], [{'name': 'ONLY_PROXY', 'type': 'select', 'use': ['uri']}])
        self.assertEqual(config['rules'], ['MATCH,ONLY_PROXY'])
        self.assertNotIn('DIRECT', json.dumps(config)); self.assertNotIn('override', json.dumps(config))
        with self.assertRaises(ValueError): mihomo_config(1)
        with self.assertRaises(ValueError): mihomo_config(1, proxy={}, provider='file')

    def test_singbox_gap_is_explicit_and_no_other_transport_substitution(self):
        client = singbox_xhttp_client(19443)
        self.assertEqual(client['outbounds'][0]['transport']['type'], 'xhttp')
        self.assertEqual(client['outbounds'][0]['network'], 'tcp')
        self.assertNotIn('direct', json.dumps(client))

    def test_httpupgrade_uri_is_not_silently_rewritten(self):
        proxy = mihomo_proxy(19443, httpupgrade=True)
        self.assertEqual(proxy['network'], 'ws')
        self.assertTrue(proxy['ws-opts']['v2ray-http-upgrade'])
        self.assertEqual(proxy['alpn'], ['http/1.1'])
        params = parse_qs(urlsplit(share_uri(19443, httpupgrade=True)).query)
        self.assertEqual(params['type'], ['httpupgrade']); self.assertNotIn('mode', params)
        self.assertNotIn('ed', params); self.assertNotIn('eh', params)
        upgrade = singbox_gap_server(19443, 'cert', 'key')
        plain = singbox_gap_server(19443, 'cert', 'key', httpupgrade=False)
        removed = upgrade['inbounds'][0].pop('transport')
        self.assertEqual(removed, {'type': 'httpupgrade', 'host': HOST, 'path': PATH})
        self.assertEqual(upgrade, plain)

    def test_all_public_formats_still_reject_xhttp_and_httpupgrade(self):
        xray = vless_node(core='xray', settings={'users': [{'id': UUID}], 'decryption': 'none'},
            stream_settings={'method': 'xhttp', 'security': 'tls',
                'xhttpSettings': {'path': PATH, 'host': HOST, 'mode': MODE}})
        sing_xhttp = vless_node(); sing_upgrade = vless_node()
        sing_xhttp.stream_settings['transport'] = {'type': 'xhttp', 'path': PATH, 'host': HOST, 'mode': MODE}
        sing_upgrade.stream_settings['transport'] = {'type': 'httpupgrade', 'path': PATH, 'host': HOST}
        for item in (xray, sing_xhttp, sing_upgrade):
            before = deepcopy(vars(item))
            for export in (lambda: base64_subscription([item], SNI),
                           lambda: singbox_client_config([item], SNI),
                           lambda: public_mihomo_config([item], SNI, {'mode': 'direct'})):
                with self.subTest(core=item.core, transport=item.stream_settings):
                    with self.assertRaises(ExportError): export()
                    self.assertEqual(vars(item), before)


class XHTTPRejectionEvidenceTests(unittest.TestCase):
    setUp = evidence_tests.TLSRejectionEvidenceTests.setUp

    def observe(self, failure, request, offset=0):
        _, reasons = rejection_reason(failure)
        return require_rejection_evidence(self, self.path, request, self.requests, reasons,
            log_offset=offset, timeout=3, label='XHTTP '+failure+' rejection')

    def test_each_authentic_reason_must_be_same_line_and_fresh(self):
        for failure in FAILURES:
            _, reasons = rejection_reason(failure)
            authentic = ' '.join(reasons)+'\n'
            with self.subTest(failure=failure):
                self.now = 0; self.path.write_text('starting\n')
                self.assertIn(reasons[0], self.observe(failure, lambda: self.path.write_text(authentic)))
                self.now = 0; self.path.write_text(authentic)
                with self.assertRaisesRegex(AssertionError, 'No actual XHTTP'):
                    self.observe(failure, lambda: None, offset=len(authentic))
                self.now = 0; self.path.write_text('timeout\nEOF\n')
                with self.assertRaisesRegex(AssertionError, 'No actual XHTTP'):
                    self.observe(failure, lambda: None)

    def test_real_reason_cannot_mask_application_delivery(self):
        _, reasons = rejection_reason('host')
        def request():
            self.requests.append('forbidden')
            self.path.write_text(' '.join(reasons))
        with self.assertRaisesRegex(AssertionError, 'retry reached the target'):
            self.observe('host', request)


class HTTPUpgradeWireEvidenceTests(unittest.TestCase):
    target_port = 23456

    def native(self):
        return (f'GET {PATH} HTTP/1.1\r\nHost: {HOST}\r\n'
                'Connection: Upgrade\r\nUpgrade: websocket\r\n\r\n').encode()

    def vless(self):
        return (b'\x00'+uuid.UUID(UUID).bytes+b'\x00\x01'+self.target_port.to_bytes(2, 'big')
                +b'\x01\x7f\x00\x00\x01'
                +f'GET /probe HTTP/1.1\r\nHost: 127.0.0.1:{self.target_port}\r\n\r\n'.encode())

    def test_exact_actual_protocols_are_distinct(self):
        self.assertEqual(classify_protocol_bytes(self.native(), self.target_port), 'httpupgrade')
        self.assertEqual(classify_protocol_bytes(self.vless(), self.target_port), 'vless')
        conn = Mock(); conn.recv.side_effect = [self.vless()[:17], self.vless()[17:]]
        self.assertEqual(read_protocol_bytes(conn), self.vless())

    def test_timeout_eof_truncated_unknown_and_oversize_never_pass(self):
        for chunks in ([b''], [b'GET /', b''], [TimeoutError()],
                       [self.native()[:-2], TimeoutError()], [b'x'*(WIRE_LIMIT+1)],
                       [self.native()+b'extra']):
            with self.subTest(chunks=repr(chunks)[:70]):
                conn = Mock(); conn.recv.side_effect = chunks
                with self.assertRaises(ValueError): read_protocol_bytes(conn)
        for payload in (b'', b'timeout', b'EOF', b'generic failure', b'\x00',
                        self.native()[:-2], self.vless()[:25], self.vless()[:-2]):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError): classify_protocol_bytes(payload, self.target_port)

    def test_incorrect_vless_uuid_addons_command_and_destination_rejected(self):
        for index, changed, reason in ((1, 0x22, 'UUID'), (17, 1, 'addons'),
                (18, 2, 'command'), (19, 0, 'destination'),
                (21, 2, 'destination'), (25, 2, 'destination')):
            payload = bytearray(self.vless()); payload[index] = changed
            with self.subTest(index=index):
                with self.assertRaisesRegex(ValueError, reason):
                    classify_protocol_bytes(bytes(payload), self.target_port)
        with self.assertRaisesRegex(ValueError, 'application request'):
            classify_protocol_bytes(self.vless().replace(b'/probe', b'/other'), self.target_port)

    def test_ordinary_websocket_or_bad_upgrade_headers_do_not_pass(self):
        variants = [self.native().replace(b'Connection: Upgrade', b'Connection: close'),
            self.native().replace(b'Upgrade: websocket', b'Upgrade: h2c'),
            self.native().replace(HOST.encode(), b'wrong.example.test'),
            self.native().replace(PATH.encode(), b'/wrong/'),
            self.native().replace(b'\r\n\r\n', b'\r\nSec-WebSocket-Key: fake\r\n\r\n'),
            self.native().replace(b'\r\n\r\n', b'\r\nHost: duplicate\r\n\r\n')]
        for payload in variants:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError): classify_protocol_bytes(payload, self.target_port)

    def test_only_actual_specific_http400_response_passes(self):
        require_httpupgrade_bad_request(400, 'Bad Request', b'400 Bad Request')
        for triple in ((502, 'Bad Gateway', b'generic error'), (400, 'Bad Request', b'EOF'),
                       (400, 'Bad Request', b'timeout'), (500, 'Bad Request', b'400 Bad Request'),
                       (400, 'Other', b'400 Bad Request')):
            with self.subTest(response=triple):
                with self.assertRaisesRegex(ValueError, 'actual fixed-server'):
                    require_httpupgrade_bad_request(*triple)

    def observed(self, payload):
        records = ProtocolObservations()
        records.append({'sni': SNI, 'alpn': 'http/1.1', 'tls': 'TLSv1.3', 'payload': payload})
        return records

    def fixture(self):
        import test_xhttp_preflight_loopback as preflight
        # Mocked unit witnesses are not actual TLS/core evidence. Only activated
        # runtime tests may print the recorder's successful-observation summary.
        self.enterContext(patch('builtins.print'))
        fixture = preflight.XHTTPPreflightLoopbackTests()
        fixture.target_port = self.target_port
        fixture.requests = []; fixture.cert = 'fake-cert'; fixture.key = 'fake-key'
        fixture.prepare_client = Mock(return_value=([], {}))
        fixture.unique_path = Mock(return_value='fake-log')
        fixture.assert_failure = Mock()
        return preflight, fixture

    def test_recorded_bytes_are_separate_from_application_delivery(self):
        preflight, fixture = self.fixture()
        with patch.object(preflight, 'start_protocol_recorder', side_effect=[
                    (1111, self.observed(self.native())), (1112, self.observed(self.vless()))]), \
                patch.object(preflight, 'unused_port', return_value=1113), \
                patch.object(preflight, 'CoreProcess'), patch.object(preflight.time, 'sleep'):
            self.assertEqual(fixture.observe_httpupgrade_protocols(), self.vless())
            self.assertEqual(fixture.requests, [])

    def test_valid_record_cannot_hide_late_application_delivery(self):
        preflight, fixture = self.fixture()
        process = Mock(); process.stop.side_effect = lambda: fixture.requests.append('forbidden')
        with patch.object(preflight, 'start_protocol_recorder', return_value=(1111, self.observed(self.native()))), \
                patch.object(preflight, 'unused_port', return_value=1113), \
                patch.object(preflight, 'CoreProcess', return_value=process), patch.object(preflight.time, 'sleep'):
            with self.assertRaisesRegex(AssertionError, 'Recorder traffic reached application'):
                fixture.observe_httpupgrade_protocols()

    def test_observation_error_or_wrong_tls_identity_cannot_pass(self):
        for update in ({'error': 'EOF'}, {'sni': 'wrong.example.test'}, {'alpn': None}, {'tls': None}):
            preflight, fixture = self.fixture()
            records = ProtocolObservations()
            record = self.observed(self.native()).snapshot()[0]; record.update(update); records.append(record)
            with self.subTest(update=update), \
                    patch.object(preflight, 'start_protocol_recorder', return_value=(1111, records)), \
                    patch.object(preflight, 'unused_port', return_value=1113), \
                    patch.object(preflight, 'CoreProcess'), patch.object(preflight.time, 'sleep'):
                with self.assertRaises(AssertionError): fixture.observe_httpupgrade_protocols()


if __name__ == '__main__': unittest.main()
