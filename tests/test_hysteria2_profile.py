"""Strict HY2 export scope and non-destructive shared editor roundtrips."""
import base64
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

from fastapi import HTTPException
import yaml

from app.services.core_manager import SingBoxAdapter
from app.services.inbound_service import _prepared_payload, editor_dict, ensure_credentials
from app.services.protocol_profiles import compile_profile, decompile_profile
from app.services.validated_export import ExportError, base64_subscription, export_warnings, share_link, singbox_client_config, validated_node
from app.services.mihomo_subscription import mihomo_config


PASSWORD = 'HY2:p@ss/word?#%with space'


def hy2_node():
    return SimpleNamespace(id=45, core='sing-box', protocol='hysteria2', port=19445,
        remark='HY2 bounded', enable=True, expiry_time=0, tag='hy2-in',
        settings={'users': [{'password': PASSWORD}]}, stream_settings={
            'tls': {'enabled': True, 'server_name': 'vpn.example.test',
                    'certificate_path': '/private/server/cert.pem', 'key_path': '/private/server/key.pem'},
            '_vui': {'security': 'tls', 'server_name': 'vpn.example.test',
                     'skip_cert_verify': False, 'client_fingerprint': ''}})


class Hysteria2EditorTests(unittest.TestCase):
    def test_default_profile_is_native_quic_and_password_stays_hidden_and_stable(self):
        item = hy2_node()
        profile = decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)
        self.assertEqual(profile['transport'], 'quic')
        self.assertEqual(profile['client_fingerprint'], '')
        self.assertIsNone(profile['up_mbps']); self.assertIsNone(profile['down_mbps'])
        self.assertEqual(profile['hysteria2_password'], '')
        self.assertTrue(profile['hysteria2_password_set'])
        self.assertNotIn(PASSWORD, json.dumps(editor_dict(item)))
        before = deepcopy(item.__dict__)
        with patch('app.services.inbound_service.secrets.token_urlsafe') as generate:
            result = _prepared_payload({'profile': profile, 'remark': 'renamed'}, item)
            generate.assert_not_called()
        self.assertEqual((result[3], result[4]), (item.settings, item.stream_settings))
        self.assertEqual(item.__dict__, before)
        profile['hysteria2_password'] = 'replacement:pass/with#escaping'
        result = _prepared_payload({'profile': profile}, item)
        self.assertEqual(result[3]['users'][0]['password'], profile['hysteria2_password'])
        self.assertEqual(item.__dict__, before)

    def test_new_default_does_not_inject_bandwidth_or_fingerprint(self):
        settings, stream = compile_profile('sing-box', 'hysteria2', {
            'security': 'tls', 'transport': 'quic', 'server_name': 'vpn.example.test',
            'certificate_path': '/cert', 'key_path': '/key'}, ensure_credentials('sing-box', 'hysteria2', {}), {})
        self.assertEqual(set(settings), {'users'})
        self.assertEqual(set(stream), {'tls', '_vui'})
        self.assertEqual(stream['_vui']['client_fingerprint'], '')
        self.assertNotIn('alpn', stream['tls'])

    def test_legacy_advanced_fields_remain_drafts_and_obfs_secret_is_preserved(self):
        item = hy2_node()
        item.settings.update(up_mbps=30, down_mbps=200, obfs={'type': 'salamander', 'password': 'obfs-secret'})
        item.stream_settings['tls']['alpn'] = ['h3']
        item.stream_settings['_vui']['client_fingerprint'] = 'chrome'
        original = deepcopy(item.__dict__)
        for profile in ({'security': 'tls'}, decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)):
            with self.subTest(profile=profile):
                settings, stream = compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
                self.assertEqual(settings, item.settings); self.assertEqual(stream, item.stream_settings)
                self.assertNotIn('obfs-secret', json.dumps(editor_dict(item)))
                self.assertEqual(item.__dict__, original)
        profile = decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)
        changed, _ = compile_profile(item.core, item.protocol, {'security': 'tls', 'obfs_password': 'new-obfs'}, item.settings, item.stream_settings)
        self.assertEqual(changed['obfs'], {'type': 'salamander', 'password': 'new-obfs'})
        profile['obfs_type'] = ''
        settings, stream = compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
        self.assertNotIn('obfs', settings)
        self.assertEqual(settings['up_mbps'], 30)
        self.assertEqual(stream['tls']['alpn'], ['h3'])

    def test_unsupported_stored_settings_survive_or_edit_rejects_atomically(self):
        for group, extra in (
            ('settings', {'ignore_client_bandwidth': True}), ('stream_settings', {'transport': {'type': 'quic', 'extra': True}}),
            ('stream_settings', {'extra': 'preserved'})):
            item = hy2_node(); getattr(item, group).update(extra)
            profile = decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)
            settings, stream = compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
            self.assertEqual(settings, item.settings); self.assertEqual(stream, item.stream_settings)
        for target, extra in (('tls', {'min_version': '1.3'}), ('tls', {'alpn': None}),
                               ('tls', {'enabled': 1}), ('_vui', {'unknown': True}),
                               ('_vui', {'skip_cert_verify': 0}), ('_vui', {'server_name': False})):
            item = hy2_node(); item.stream_settings[target].update(extra)
            before = deepcopy(item.__dict__)
            with self.subTest(target=target, extra=extra), self.assertRaises(HTTPException):
                compile_profile(item.core, item.protocol, {'security': 'tls'}, item.settings, item.stream_settings)
            self.assertEqual(item.__dict__, before)
        for settings in ({'obfs': {}}, {'obfs': {'type': 'gecko', 'password': 'secret', 'min_packet_size': 512}},
                         {'up_mbps': None}, {'down_mbps': False}, {'up_mbps': '100'}):
            item = hy2_node(); item.settings.update(settings)
            before = deepcopy(item.__dict__)
            with self.subTest(settings=settings), self.assertRaises(HTTPException):
                compile_profile(item.core, item.protocol, {'security': 'tls'}, item.settings, item.stream_settings)
            self.assertEqual(item.__dict__, before)

    def test_invalid_existing_password_never_generates_a_replacement(self):
        for users in (None, [], [{}], [None], [{'password': value} for value in ('',)],
                      [{'password': None}], [{'password': False}], [{'password': ' '}], [{'password': '\n'}], [{'password': '\ud800'}], [{'password': '\u0080'}]):
            item = hy2_node(); item.settings = {'users': users}
            before = deepcopy(item.__dict__)
            with self.subTest(users=users), patch('app.services.inbound_service.secrets.token_urlsafe') as generate:
                with self.assertRaises(HTTPException): _prepared_payload({'remark': 'rename'}, item)
                generate.assert_not_called()
            self.assertEqual(item.__dict__, before)

    def test_malformed_users_and_security_produce_422_without_repair(self):
        for users in ({'bad': 'x'}, 12, True, None, [], [None], [{}, {}]):
            item = hy2_node(); item.settings['users'] = users
            with self.subTest(users=users), self.assertRaises(HTTPException) as caught:
                editor_dict(item)
            self.assertEqual(caught.exception.status_code, 422)
        for stream in (None, False, 1, [], 'invalid'):
            item = hy2_node(); item.stream_settings = stream
            with self.subTest(stream=stream), self.assertRaises(HTTPException): editor_dict(item)
        for stream in ({}, {'tls': {}}, {'tls': {'enabled': False}}, {'tls': {'enabled': None}}):
            item = hy2_node(); item.stream_settings = deepcopy(stream)
            before = deepcopy(item.__dict__)
            with self.subTest(stream=stream), self.assertRaises(HTTPException) as caught:
                _prepared_payload({'profile': {'hysteria2_password': 'newpassword',
                    'server_name': 'vpn.example.test', 'certificate_path': '/cert', 'key_path': '/key'}}, item)
            self.assertEqual(caught.exception.status_code, 422)
            self.assertEqual(item.__dict__, before)
        for security in (None, False, 0, [], {}, '', 'none', 'reality'):
            item = hy2_node()
            with self.subTest(security=security), self.assertRaises(HTTPException):
                compile_profile(item.core, item.protocol, {'security': security}, item.settings, item.stream_settings)

    def test_malformed_creation_never_mutates_input_or_generates_credentials(self):
        for users in (None, False, 1, {}, [None], [{}, {}], [{'password': False}],
                      [{'password': None}], [{'password': '\ud800'}]):
            payload = {'core': 'sing-box', 'protocol': 'hysteria2', 'port': 10445,
                       'settings': {'users': users}}
            before = deepcopy(payload)
            with self.subTest(users=users), patch('app.services.inbound_service.secrets.token_urlsafe') as generate:
                with self.assertRaises(HTTPException): _prepared_payload(payload)
                generate.assert_not_called()
            self.assertEqual(payload, before)

    def test_literal_invalid_input_is_rejected_without_coercion(self):
        item = hy2_node()
        base = decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)
        changes = [{'hysteria2_password': value} for value in (None, False, 1, [], {}, ' ', '\n', '\ud800', '\u0080', 'x'*257)]
        changes += [{'up_mbps': value} for value in (False, True, 0, -1, '100', [], {})]
        changes += [{'server_name': value} for value in (None, False, ' leading', 'trailing ', '')]
        changes += [{'skip_cert_verify': 0}, {'transport': 'tcp'}, {'port_hopping': True}, {'alpn': ['h3']}, {'host': 'ignored.example.test'}, {'flow': 'xtls-rprx-vision'}, {'zero_rtt_handshake': 0}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(HTTPException):
                compile_profile(item.core, item.protocol, {**base, **change}, item.settings, item.stream_settings)


class Hysteria2ExportTests(unittest.TestCase):
    def assert_rejected(self, item):
        for exporter in (share_link, base64_subscription, mihomo_config, singbox_client_config):
            with self.subTest(exporter=exporter.__name__), self.assertRaises(ExportError):
                exporter(item if exporter is share_link else [item], 'vpn.example.test')
        self.assertEqual(export_warnings([item])[0]['code'], 'UNVERIFIED_EXPORT_PROFILE')

    def test_three_formats_preserve_standard_password_sni_and_verified_native_defaults(self):
        item = hy2_node(); before = deepcopy(item.__dict__)
        node = yaml.safe_load(mihomo_config([item], '2001:db8::1'))['proxies'][0]
        expected = {'name': item.remark, 'type': 'hysteria2', 'server': '2001:db8::1',
                    'port': item.port, 'password': PASSWORD, 'udp': False,
                    'sni': 'vpn.example.test', 'skip-cert-verify': False}
        self.assertEqual(node, expected)
        client = singbox_client_config([item], '2001:db8::1')
        outbound = client['outbounds'][0]
        self.assertEqual(outbound, {'type': 'hysteria2', 'tag': item.remark, 'server': '2001:db8::1',
            'server_port': item.port, 'password': PASSWORD, 'network': 'tcp',
            'tls': {'enabled': True, 'server_name': 'vpn.example.test'}})
        self.assertEqual(client['route']['final'], item.remark)
        self.assertEqual(len(client['outbounds']), 1)
        raw = base64.b64decode(base64_subscription([item], '2001:db8::1')).decode()
        self.assertEqual(raw, share_link(item, '2001:db8::1'))
        uri = urlsplit(raw)
        self.assertEqual((uri.scheme, uri.hostname, uri.port), ('hysteria2', '2001:db8::1', item.port))
        self.assertEqual(unquote(uri.username), PASSWORD); self.assertIsNone(uri.password)
        self.assertEqual(parse_qs(uri.query), {'sni': ['vpn.example.test'], 'insecure': ['0']})
        self.assertEqual(unquote(uri.fragment), item.remark)
        for text in (raw, json.dumps(client), json.dumps(node)):
            for secret in ('PRIVATE KEY', '/private/server', 'certificate_path', 'key_path'):
                self.assertNotIn(secret, text)
        self.assertEqual(item.__dict__, before)
        server = SingBoxAdapter().build_config([item])['inbounds'][0]
        self.assertEqual(server['users'], item.settings['users'])
        self.assertNotIn('_vui', server); self.assertNotIn('transport', server)
        self.assertEqual(export_warnings([item]), [])

    def test_strict_profile_rejects_unverified_or_malformed_fields(self):
        items = []
        for updates in ({'core': 'xray'}, {'enable': False}, {'expiry_time': 1}, {'port': True}):
            item = hy2_node(); item.__dict__.update(updates); items.append(item)
        for settings in ({'up_mbps': 100}, {'down_mbps': None}, {'obfs': {}}, {'ignore_client_bandwidth': False},
                         {'users': []}, {'users': [{'password': PASSWORD}, {'password': PASSWORD}]},
                         {'users': [{'password': PASSWORD, 'unknown': None}]}):
            item = hy2_node(); item.settings.update(settings); items.append(item)
        for value in (None, False, '', ' ', '\r', '\ud800', '\u0080', 'x'*257):
            item = hy2_node(); item.settings['users'][0]['password'] = value; items.append(item)
        for extra in ({'transport': {}}, {'transport': None}, {'multiplex': {}}, {'unknown': False}):
            item = hy2_node(); item.stream_settings.update(extra); items.append(item)
        for target, changes in (('tls', {'alpn': ['h3']}), ('tls', {'alpn': None}), ('tls', {'enabled': 1}),
            ('tls', {'insecure': False}), ('tls', {'server_name': ''}), ('_vui', {'server_name': False}),
            ('_vui', {'server_name': 'conflicting.example.test'}), ('_vui', {'skip_cert_verify': True}),
            ('_vui', {'skip_cert_verify': 0}), ('_vui', {'client_fingerprint': 'chrome'}), ('_vui', {'unknown': ''})):
            item = hy2_node(); item.stream_settings[target].update(changes); items.append(item)
        for item in items:
            with self.subTest(item=item.__dict__): self.assert_rejected(item)


if __name__ == '__main__':
    unittest.main()
