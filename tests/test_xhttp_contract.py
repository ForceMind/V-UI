"""The characterization fixture must not unlock lossy production exports."""
from copy import deepcopy
import json
import unittest
from urllib.parse import parse_qs, urlsplit

from app.services.mihomo_subscription import mihomo_config as public_mihomo_config
from app.services.validated_export import ExportError, base64_subscription, singbox_client_config
from test_validated_export import vless_node
import test_tls_rejection_evidence as evidence_tests
from loopback_helpers import require_rejection_evidence
from xhttp_helpers import (FAILURES, HOST, MODE, PATH, SNI, UUID, mihomo_config,
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


if __name__ == '__main__': unittest.main()
