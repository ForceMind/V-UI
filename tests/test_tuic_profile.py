"""Strict TUIC v5 exports and non-destructive shared credential editing."""
import base64
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit
import yaml
from fastapi import HTTPException

from app.services.inbound_service import _prepared_payload, editor_dict
from app.services.validated_export import ExportError, base64_subscription, share_link, singbox_client_config, validated_node
from app.services.mihomo_subscription import mihomo_config
from app.services.mihomo_routing import default_routing

UUID = '11111111-1111-4111-8111-111111111111'
PASSWORD = 'TUIC:p@ss/word?#%with space雪'


def tuic_node():
    return SimpleNamespace(id=46, core='sing-box', protocol='tuic', port=19446,
        remark='TUIC bounded', enable=True, expiry_time=0, tag='tuic-in', user_id=None, up=0, down=0, total=0,
        settings={'users': [{'uuid': UUID, 'password': PASSWORD}]}, stream_settings={
            'tls': {'enabled': True, 'server_name': 'vpn.example.test', 'alpn': ['h3'],
                    'certificate_path': '/private/server.pem', 'key_path': '/private/key.pem'},
            '_vui': {'security': 'tls', 'server_name': 'vpn.example.test',
                     'client_fingerprint': '', 'skip_cert_verify': False}})


class TUICExportTests(unittest.TestCase):
    def test_three_formats_keep_pair_and_h3_without_server_material(self):
        item = tuic_node(); node = validated_node(item, '2001:db8::46')
        self.assertEqual(node, {'name': item.remark, 'type': 'tuic', 'server': '2001:db8::46',
            'port': item.port, 'uuid': UUID, 'password': PASSWORD, 'sni': 'vpn.example.test',
            'skip-cert-verify': False, 'alpn': ['h3'], 'reduce-rtt': False})
        uri = urlsplit(share_link(item, '2001:db8::46'))
        self.assertEqual((uri.scheme, uri.hostname, uri.port), ('tuic', '2001:db8::46', item.port))
        self.assertEqual(unquote(uri.username), UUID); self.assertEqual(unquote(uri.password), PASSWORD)
        self.assertEqual(parse_qs(uri.query), {'sni': ['vpn.example.test'], 'alpn': ['h3']})
        self.assertEqual(base64.b64decode(base64_subscription([item], '2001:db8::46')).decode(), share_link(item, '2001:db8::46'))
        config = yaml.safe_load(mihomo_config([item], '2001:db8::46', {**default_routing(), 'mode': 'direct'}))
        self.assertEqual(config['proxies'], [node])
        self.assertEqual(next(g for g in config['proxy-groups'] if g['name'] == 'FORCE_PROXY')['proxies'], [item.remark])
        outbound = singbox_client_config([item], '2001:db8::46')['outbounds'][0]
        self.assertEqual(outbound, {'type': 'tuic', 'tag': item.remark, 'server': '2001:db8::46',
            'server_port': item.port, 'uuid': UUID, 'password': PASSWORD, 'network': 'tcp',
            'zero_rtt_handshake': False, 'tls': {'enabled': True, 'server_name': 'vpn.example.test', 'alpn': ['h3']}})
        text = json.dumps([node, outbound, config, share_link(item, '2001:db8::46')])
        for forbidden in ('/private/', 'key_path', 'certificate_path', 'PRIVATE KEY', '_vui'): self.assertNotIn(forbidden, text)

    def test_all_unqualified_fields_and_malformed_credentials_fail_closed(self):
        mutations = []
        for field, values in {'token': ['v4'], 'congestion_control': ['bbr', 'new_reno', None, ''],
                'zero_rtt_handshake': [True, 0, None], 'heartbeat': ['10s'], 'auth_timeout': ['3s'],
                'unknown': ['secret'], 'obfs': [{}], 'users': [[], None, {}, ['bad']]}.items():
            mutations += [('settings', field, v) for v in values]
        mutations += [('stream', 'transport', {'type': 'quic'}), ('stream', 'unknown', 1)]
        for field, values in {'enabled': [False, 1, None], 'alpn': [None, [], ['h2'], ['h3', 'h2']],
                'server_name': ['', None, 12, ' vpn.example.test '], 'reality': [{}], 'insecure': [True]}.items():
            mutations += [('tls', field, v) for v in values]
        for field, values in {'skip_cert_verify': [True, 0, None], 'client_fingerprint': ['chrome', None],
                'udp_relay_mode': ['quic', '', None], 'server_name': ['wrong.example.test', None], 'unknown': ['bad']}.items():
            mutations += [('meta', field, v) for v in values]
        for field, values in {'uuid': ['', None, 3, UUID + ' ', 'bad'],
                'password': ['', None, 3, ' ', 'a\n', '\x7f', '\ud800', 'a' * 257], 'unknown': ['bad']}.items():
            mutations += [('user', field, v) for v in values]
        for kind, field, value in mutations:
            item = tuic_node(); target = {'settings': item.settings, 'stream': item.stream_settings,
                'tls': item.stream_settings['tls'], 'meta': item.stream_settings['_vui'], 'user': item.settings['users'][0]}[kind]
            target[field] = value
            with self.subTest(kind=kind, field=field, value=repr(value)), self.assertRaises(ExportError):
                validated_node(item, 'vpn.example.test')
        item = tuic_node(); item.settings['users'] *= 2
        with self.assertRaises(ExportError): validated_node(item, 'vpn.example.test')

    def test_explicit_default_options_match_but_missing_server_alpn_is_never_invented(self):
        original = validated_node(tuic_node(), 'vpn.example.test')
        for field, value in (('congestion_control', 'cubic'), ('zero_rtt_handshake', False)):
            item = tuic_node(); item.settings[field] = value
            self.assertEqual(validated_node(item, 'vpn.example.test'), original)
        item = tuic_node(); item.stream_settings['_vui']['udp_relay_mode'] = 'native'
        self.assertEqual(validated_node(item, 'vpn.example.test'), original)
        del item.stream_settings['tls']['alpn']
        with self.assertRaises(ExportError): validated_node(item, 'vpn.example.test')
        with self.assertRaises(HTTPException): _prepared_payload({'profile': editor_dict(item)['profile']}, item)


class TUICEditorTests(unittest.TestCase):
    def test_blank_create_generates_once_and_refresh_never_exposes_or_replaces_pair(self):
        result = _prepared_payload({'core': 'sing-box', 'protocol': 'tuic', 'port': 10446,
            'profile': {'security': 'tls', 'transport': 'quic', 'server_name': 'vpn.example.test',
                        'certificate_path': '/cert.pem', 'key_path': '/key.pem', 'tuic_uuid': '', 'tuic_password': ''}})
        item = tuic_node(); item.settings, item.stream_settings = result[3:]
        self.assertEqual(set(item.settings), {'users'}); self.assertEqual(item.stream_settings['tls']['alpn'], ['h3'])
        self.assertEqual(item.stream_settings['_vui']['client_fingerprint'], '')
        original = deepcopy(result[3:])
        for _ in range(3):
            editor = editor_dict(item)
            self.assertEqual(editor['profile']['tuic_uuid'], ''); self.assertEqual(editor['profile']['tuic_password'], '')
            for secret in item.settings['users'][0].values(): self.assertNotIn(secret, json.dumps(editor))
            self.assertEqual(_prepared_payload({'profile': editor['profile']}, item)[3:], original)

    def test_explicit_replacements_are_independent_and_ordinary_responses_stay_redacted(self):
        from app.services.inbound_service import to_dict
        for field, replacement in (('uuid', '22222222-2222-4222-8222-222222222222'), ('password', 'new:p@ss/word?#%雪')):
            item = tuic_node(); profile = editor_dict(item)['profile']; profile['tuic_' + field] = replacement
            result = _prepared_payload({'profile': profile}, item)
            expected = deepcopy(item.settings); expected['users'][0][field] = replacement
            self.assertEqual(result[3], expected); self.assertEqual(result[4], item.stream_settings)
            self.assertEqual(item.settings, tuic_node().settings)
            item.settings = result[3]
            response = json.dumps(to_dict(item))
            for secret in (UUID, PASSWORD, replacement): self.assertNotIn(secret, response)
            self.assertNotIn('stream_settings', response); self.assertNotIn('/private/', response)

    def test_advanced_drafts_preserve_values_but_never_become_public(self):
        item = tuic_node(); item.settings.update(congestion_control='bbr', zero_rtt_handshake=True)
        item.stream_settings['_vui'].update(udp_relay_mode='quic', client_fingerprint='chrome', skip_cert_verify=True)
        item.stream_settings['tls']['alpn'] = ['custom']
        self.assertEqual(_prepared_payload({'profile': editor_dict(item)['profile']}, item)[3:], (item.settings, item.stream_settings))
        with self.assertRaises(ExportError): validated_node(item, 'vpn.example.test')

    def test_unknown_imports_reject_edit_without_mutation(self):
        for location, field, value in (('settings', 'heartbeat', '1s'), ('stream', 'transport', {}),
                ('tls', 'certificate', ['inline']), ('meta', 'unsupported', True), ('user', 'token', 'v4')):
            item = tuic_node(); objects = {'settings': item.settings, 'stream': item.stream_settings,
                'tls': item.stream_settings['tls'], 'meta': item.stream_settings['_vui'], 'user': item.settings['users'][0]}
            objects[location][field] = value; before = deepcopy(item.__dict__)
            with self.subTest(location=location), self.assertRaises(HTTPException):
                _prepared_payload({'profile': {'security': 'tls'}}, item)
            self.assertEqual(item.__dict__, before)

    def test_malformed_existing_pair_never_gets_an_automatic_repair(self):
        for users in (None, {}, [], ['bad'], [{'uuid': UUID}], [{'password': PASSWORD}],
                      [{'uuid': UUID, 'password': 4}], [{'uuid': UUID, 'password': PASSWORD}] * 2):
            item = tuic_node(); item.settings['users'] = users; before = deepcopy(item.__dict__)
            with self.subTest(users=users), patch('app.services.inbound_service.secrets.token_urlsafe') as generate:
                with self.assertRaises(HTTPException): _prepared_payload({}, item)
                generate.assert_not_called(); self.assertEqual(item.__dict__, before)

    def test_invalid_visual_literals_and_security_are_not_coerced(self):
        for field, values in {'tuic_uuid': [None, 0, UUID + ' ', 'bad'], 'tuic_password': [None, 0, '\n', '\ud800'],
                'zero_rtt_handshake': [None, 0, 'false'], 'skip_cert_verify': [None, 0, 'false'],
                'security': [None, 0, 'none', 'reality'], 'server_name': [None, ' vpn.example.test '],
                'transport': ['tcp', None], 'up_mbps': [100], 'obfs_type': ['salamander'], 'unknown': ['bad']}.items():
            for value in values:
                item = tuic_node(); profile = editor_dict(item)['profile']; profile[field] = value
                with self.subTest(field=field, value=repr(value)), self.assertRaises(HTTPException):
                    _prepared_payload({'profile': profile}, item)

    def test_existing_absent_tls_needs_explicit_security_selection(self):
        item = tuic_node(); item.stream_settings = {}
        original = deepcopy(item.__dict__)
        partial = {'server_name': 'vpn.example.test', 'certificate_path': '/cert.pem', 'key_path': '/key.pem'}
        with self.assertRaisesRegex(HTTPException, 'Explicit TLS selection'):
            _prepared_payload({'profile': partial}, item)
        self.assertEqual(item.__dict__, original)
        self.assertEqual(_prepared_payload({}, item)[3:], (item.settings, {}))
        with self.assertRaises(ExportError): validated_node(item, 'vpn.example.test')
        repaired = _prepared_payload({'profile': {**partial, 'security': 'tls'}}, item)
        self.assertEqual(repaired[4]['tls']['alpn'], ['h3'])
        self.assertEqual(repaired[3], item.settings)

    def test_malformed_imported_options_cannot_be_normalized(self):
        for location, field, values in (('settings', 'congestion_control', [None, '', 0]),
                ('settings', 'zero_rtt_handshake', [None, 0, 'false']), ('tls', 'alpn', [None, [], 'h3']),
                ('tls', 'enabled', [None, 0]), ('meta', 'skip_cert_verify', [None, 0]), ('meta', 'server_name', [None, 7])):
            for value in values:
                item = tuic_node(); target = item.settings if location == 'settings' else item.stream_settings['tls' if location == 'tls' else '_vui']
                target[field] = value
                with self.subTest(location=location, field=field, value=value), self.assertRaises(HTTPException):
                    _prepared_payload({'profile': {'security': 'tls'}}, item)
