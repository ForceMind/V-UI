"""Strict qualified REALITY exports never reuse the permissive draft exporter."""
import base64
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs, urlsplit

import yaml
from app.services.validated_export import ExportError, validated_node, share_link, singbox_client_config, base64_subscription
from app.services.mihomo_subscription import mihomo_config
from test_reality_preflight_loopback import keypair, UUID, FLOW, SHORT_ID
from reality_helpers import SNI


def reality_node():
    private, public = keypair()
    return SimpleNamespace(id=47, core='sing-box', protocol='vless', port=10447, remark='REALITY_TEST',
        enable=True, expiry_time=0, tag='reality-test',
        settings={'users': [{'uuid': UUID, 'flow': FLOW}]}, stream_settings={
            'tls': {'enabled': True, 'server_name': SNI, 'reality': {'enabled': True,
                'handshake': {'server': '127.0.0.1', 'server_port': 19445}, 'private_key': private, 'short_id': [SHORT_ID]}},
            '_vui': {'security': 'reality', 'server_name': SNI, 'client_fingerprint': 'chrome',
                     'reality_public_key': public, 'reality_short_id': SHORT_ID}})


class RealityExportTests(unittest.TestCase):
    def test_three_formats_preserve_exact_client_contract_without_server_material(self):
        item = reality_node()
        private = item.stream_settings['tls']['reality']['private_key']
        public = item.stream_settings['_vui']['reality_public_key']
        node = validated_node(item, 'vpn.example.test')
        self.assertEqual(node, {'name': 'REALITY_TEST', 'type': 'vless', 'server': 'vpn.example.test', 'port': 10447,
            'uuid': UUID, 'flow': FLOW, 'network': 'tcp', 'udp': False, 'tls': True, 'servername': SNI,
            'client-fingerprint': 'chrome', 'skip-cert-verify': False,
            'reality-opts': {'public-key': public, 'short-id': SHORT_ID}})
        document = mihomo_config([item], 'vpn.example.test', {'mode': 'direct'})
        self.assertEqual(yaml.safe_load(document)['proxies'], [node])
        uri = share_link(item, 'vpn.example.test'); parsed = urlsplit(uri)
        self.assertEqual((parsed.scheme, parsed.username, parsed.hostname, parsed.port), ('vless', UUID, 'vpn.example.test', 10447))
        self.assertEqual(parse_qs(parsed.query), {'security': ['reality'], 'type': ['tcp'], 'sni': [SNI],
            'encryption': ['none'], 'flow': [FLOW], 'fp': ['chrome'], 'pbk': [public], 'sid': [SHORT_ID]})
        self.assertEqual(base64.b64decode(base64_subscription([item], 'vpn.example.test')).decode(), uri)
        client = singbox_client_config([item], 'vpn.example.test')
        self.assertEqual(client['outbounds'], [{'type': 'vless', 'tag': 'REALITY_TEST', 'server': 'vpn.example.test',
            'server_port': 10447, 'uuid': UUID, 'flow': FLOW, 'network': 'tcp', 'tls': {'enabled': True, 'server_name': SNI,
                'utls': {'enabled': True, 'fingerprint': 'chrome'},
                'reality': {'enabled': True, 'public_key': public, 'short_id': SHORT_ID}}}])
        for text in (document, uri, json.dumps(client)):
            for forbidden in (private, 'private_key', 'private-key', 'handshake', '19445', 'certificate_path', 'key_path', '_vui'):
                self.assertNotIn(forbidden, text)
        self.assertEqual(client['route'], {'final': 'REALITY_TEST'})

    def test_unsupported_fields_and_mismatched_material_reject_all_exports(self):
        _, wrong_public = keypair()
        changes = [
            lambda n: n.settings['users'][0].update(flow=''),
            lambda n: n.settings['users'][0].update(flow=FLOW+'-udp443'),
            lambda n: n.settings['users'].append(deepcopy(n.settings['users'][0])),
            lambda n: n.settings.update(multiplex={}),
            lambda n: n.stream_settings.update(transport={'type': 'grpc', 'service_name': 'a'}),
            lambda n: n.stream_settings['tls'].update(alpn=['h2']),
            lambda n: n.stream_settings['tls'].update(alpn=None),
            lambda n: n.stream_settings['tls'].update(certificate_path='/server.pem'),
            lambda n: n.stream_settings['tls']['reality'].update(short_id=[SHORT_ID, 'fedcba9876543210']),
            lambda n: n.stream_settings['tls']['reality'].update(short_id=['ABC']),
            lambda n: n.stream_settings['tls']['reality'].update(max_time_difference='1m'),
            lambda n: n.stream_settings['tls']['reality']['handshake'].update(detour='hidden'),
            lambda n: n.stream_settings['_vui'].update(reality_public_key=wrong_public),
            lambda n: n.stream_settings['_vui'].update(reality_short_id='fedcba9876543210'),
            lambda n: n.stream_settings['_vui'].update(client_fingerprint='firefox'),
            lambda n: n.stream_settings['_vui'].update(skip_cert_verify=True),
            lambda n: n.stream_settings['_vui'].update(server_name='conflict.example.test'),
        ]
        for change in changes:
            item = reality_node(); change(item)
            before = deepcopy(item.__dict__)
            for exporter in (lambda: validated_node(item, 'vpn.example.test'),
                             lambda: share_link(item, 'vpn.example.test'),
                             lambda: singbox_client_config([item], 'vpn.example.test'),
                             lambda: mihomo_config([item], 'vpn.example.test', {'mode': 'direct'})):
                with self.subTest(change=changes.index(change), exporter=exporter):
                    with self.assertRaises(ExportError): exporter()
                    self.assertEqual(item.__dict__, before)

    def test_xray_and_empty_candidates_remain_rejected_without_fallback(self):
        item = reality_node(); item.core = 'xray'
        with self.assertRaises(ExportError): singbox_client_config([item], 'vpn.example.test')
        with self.assertRaises(ExportError): singbox_client_config([], 'vpn.example.test')


if __name__ == '__main__': unittest.main()
