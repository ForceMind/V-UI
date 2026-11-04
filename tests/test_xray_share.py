"""Bounded Xray export fields; synthetic credentials and no core process."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs, urlsplit

from app.services.validated_export import ExportError, share_link, validated_node, singbox_client_config


def xray_node(**changes):
    values = dict(id=7, core='xray', protocol='vless', port=10443, remark='示例 Xray',
        enable=True, expiry_time=0,
        settings={'users': [{'id': '11111111-1111-1111-1111-111111111111', 'flow': ''}], 'decryption': 'none'},
        stream_settings={'method': 'raw', 'security': 'tls', 'tlsSettings': {
            'serverName': 'vpn.example.test', 'certificates': [{
                'certificateFile': '/private/fake/cert.pem', 'keyFile': '/private/fake/key.pem'}]},
            '_vui': {'security': 'tls', 'server_name': 'vpn.example.test', 'client_fingerprint': '', 'skip_cert_verify': False}})
    values.update(changes)
    return SimpleNamespace(**values)


class XrayShareTests(unittest.TestCase):
    def test_raw_tcp_tls_outputs_preserve_identity_without_material_or_udp_claim(self):
        for network in ('raw', 'tcp'):
            row = xray_node(); row.stream_settings['method'] = network
            original = deepcopy(vars(row))
            link = share_link(row, '127.0.0.1')
            params = parse_qs(urlsplit(link).query)
            self.assertEqual(params['sni'], ['vpn.example.test'])
            self.assertEqual(params['type'], ['tcp'])
            self.assertNotIn('packetEncoding', params)
            node = validated_node(row, '127.0.0.1')
            self.assertFalse(node['udp']); self.assertFalse(node['skip-cert-verify'])
            config = singbox_client_config([row], '127.0.0.1')
            self.assertEqual(config['outbounds'][0]['network'], 'tcp')
            for value in (link, json.dumps(node), json.dumps(config)):
                self.assertNotIn('/private/', value); self.assertNotIn('keyFile', value)
                self.assertIn('11111111-1111-1111-1111-111111111111', value)
            self.assertEqual(vars(row), original)

    def test_unknown_advanced_ambiguous_and_unverified_fields_rejected(self):
        changes = [
            lambda r: r.stream_settings.update(method='ws'),
            lambda r: r.stream_settings.update(network='grpc'),
            lambda r: r.stream_settings.update(security='reality'),
            lambda r: r.stream_settings.update(sockopt={'dialerProxy':'secret'}),
            lambda r: r.stream_settings['tlsSettings'].update(allowInsecure=True),
            lambda r: r.stream_settings['_vui'].update(skip_cert_verify=True),
            lambda r: r.settings['users'][0].update(flow='xtls-rprx-vision'),
            lambda r: r.settings['users'].append({'id':'22222222-2222-2222-2222-222222222222'}),
            lambda r: r.settings.update(clients=[{'id':'22222222-2222-2222-2222-222222222222'}]),
            lambda r: r.stream_settings['tlsSettings'].update(serverName=''),
            lambda r: r.settings['users'][0].update(id='bad'),
            lambda r: setattr(r,'enable',False),
            lambda r: setattr(r,'protocol','vmess')]
        for change in changes:
            row = xray_node(); change(row)
            with self.subTest(change=change), self.assertRaises(ExportError): share_link(row, 'vpn.example.test')
