"""Real API allowlist regression using isolated SQLite and synthetic secrets."""
import base64
from contextlib import ExitStack
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from app.models import database
from app.api import inbounds as inbound_api, singbox as singbox_api, xray as xray_api
from app.services.core_manager import SingBoxAdapter, XrayAdapter
from app.services.inbound_service import to_dict
import test_auth as auth_tests

UUID = '11111111-1111-4111-8111-111111111111'
PASSWORD = 'synthetic-protocol-response-secret'
OBFS = 'synthetic-obfs-response-secret'
PRIVATE = 'synthetic-private-key-never-in-response'
UNKNOWN = 'synthetic-future-secret-never-in-response'
SECRET_VALUES = (UUID, PASSWORD, OBFS, PRIVATE, UNKNOWN, '/synthetic/private.pem')


def fixtures():
    for core in ('sing-box', 'xray'):
        for protocol in ('vless', 'vmess', 'trojan', 'shadowsocks'):
            if protocol == 'shadowsocks':
                settings = {'method': 'aes-128-gcm', 'password': PASSWORD}
            elif protocol == 'trojan':
                settings = {'users': [{'password': PASSWORD}]}
            else:
                settings = {'users': [{'uuid' if core == 'sing-box' else 'id': UUID}]}
            yield core, protocol, settings
    yield 'sing-box', 'hysteria2', {'users': [{'password': PASSWORD}], 'obfs': {'type': 'salamander', 'password': OBFS}}
    yield 'sing-box', 'tuic', {'users': [{'uuid': UUID, 'password': PASSWORD}]}


class InboundResponseRedactionTests(unittest.TestCase):
    def setUp(self):
        auth_tests.AuthenticationTests.setUp(self)
        self.assertEqual(auth_tests.AuthenticationTests.login(self).status_code, 200)
        stack = ExitStack(); self.addCleanup(stack.close)
        for module in (inbound_api, singbox_api, xray_api):
            stack.enter_context(patch.object(module, 'apply_checked', return_value={'applied': True, 'valid': True}))
        self.port = 12000

    def create(self, core, protocol, settings, *, legacy=False, stream=None):
        self.port += 1
        path = '/api/' + ('singbox' if core == 'sing-box' else 'xray') + '/inbounds' if legacy else '/api/inbounds'
        payload = {'protocol': protocol, 'port': self.port, 'remark': 'synthetic-node',
                   'settings': deepcopy(settings), 'stream_settings': deepcopy(stream or {})}
        if not legacy: payload['core'] = core
        response = self.client.post(path, headers=auth_tests.HEADERS, json=payload)
        self.assert_safe(response)
        return response.json()['inbound']['id'], path, payload

    def assert_safe(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('no-store', response.headers['cache-control'])
        for secret in SECRET_VALUES: self.assertNotIn(secret, response.text)
        rows = response.json()
        rows = rows if isinstance(rows, list) else [rows['inbound']]
        for row in rows:
            self.assertNotIn('settings', row); self.assertNotIn('stream_settings', row)
            self.assertIs(type(row['managed_certificate_eligible']), bool)
            self.assertIs(type(row['credentials']['has_uuid']), bool)
            self.assertIs(type(row['credentials']['has_password']), bool)

    def snapshot(self, identity):
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, identity)
            return deepcopy(row.settings), deepcopy(row.stream_settings)

    def test_general_create_list_and_update_hide_existing_credentials_without_changing_internal_models(self):
        for core, protocol, settings in fixtures():
            with self.subTest(core=core, protocol=protocol):
                stream = {'tls': {'enabled': True, 'key_path': '/synthetic/private.pem',
                                  'reality': {'private_key': PRIVATE}},
                          'realitySettings': {'privateKey': PRIVATE}, 'future': {'credential': UNKNOWN}}
                malformed_reality = core == 'sing-box' and protocol == 'vless'
                identity, path, _ = self.create(core, protocol, settings, stream={} if malformed_reality else stream)
                if malformed_reality:
                    # Model an old imported row: strict REALITY creation/editing
                    # now rejects it, but ordinary list responses must stay safe.
                    with database.SessionLocal() as db:
                        db.get(database.Inbound, identity).stream_settings = deepcopy(stream)
                        db.commit()
                original = self.snapshot(identity)
                self.assert_safe(self.client.get(path))
                self.assert_safe(self.client.get(path + '?core=' + core))
                updated = self.client.put(path + '/' + str(identity), headers=auth_tests.HEADERS,
                                          json={'remark': 'updated-synthetic'})
                if malformed_reality:
                    self.assertEqual(updated.status_code, 422, updated.text)
                    for secret in SECRET_VALUES: self.assertNotIn(secret, updated.text)
                else:
                    self.assert_safe(updated)
                self.assertEqual(self.snapshot(identity), original)
                with database.SessionLocal() as db:
                    row = db.get(database.Inbound, identity)
                    adapter = SingBoxAdapter() if core == 'sing-box' else XrayAdapter()
                    actual = json.dumps(adapter.build_config([row]))
                    for secret in (UUID, PASSWORD, OBFS):
                        if secret in json.dumps(settings): self.assertIn(secret, actual)
                    self.assertIn(PRIVATE, actual)

    def test_legacy_both_core_create_list_and_update_use_same_safe_contract(self):
        for core, protocol, settings in fixtures():
            with self.subTest(core=core, protocol=protocol):
                identity, path, payload = self.create(core, protocol, settings, legacy=True,
                                                     stream={'future': {'credential': UNKNOWN}})
                original = self.snapshot(identity)
                self.assert_safe(self.client.get(path))
                self.assert_safe(self.client.put(path + '/' + str(identity), headers=auth_tests.HEADERS,
                                                json={**payload, 'remark': 'legacy-updated'}))
                self.assertEqual(self.snapshot(identity), original)

    def test_generated_and_replaced_hy2_passwords_never_return_to_browser(self):
        payload = {'core': 'sing-box', 'protocol': 'hysteria2', 'port': 19445,
            'profile': {'security': 'tls', 'transport': 'quic', 'server_name': 'vpn.example.test',
                        'certificate_path': '/synthetic/cert.pem', 'key_path': '/synthetic/private.pem'}}
        created = self.client.post('/api/inbounds', headers=auth_tests.HEADERS, json=payload)
        self.assert_safe(created); identity = created.json()['inbound']['id']
        generated = self.snapshot(identity)[0]['users'][0]['password']
        self.assertNotIn(generated, created.text)
        state = self.client.get(f'/api/inbounds/{identity}/editor').json()
        self.assertNotIn(generated, json.dumps(state))
        for password in ('', PASSWORD):
            response = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS,
                json={'profile': {**state['profile'], 'hysteria2_password': password}})
            self.assert_safe(response); self.assertNotIn(generated, response.text)
            self.assertEqual(self.snapshot(identity)[0]['users'][0]['password'], password or generated)
            for path in ('/api/inbounds', '/api/singbox/inbounds'):
                listed = self.client.get(path); self.assert_safe(listed); self.assertNotIn(generated, listed.text)

    def test_unknown_and_malformed_imported_values_never_escape_allowlist(self):
        for settings, stream in (({'future_secret': UNKNOWN}, {'future_private_key': PRIVATE}),
                (None, None), ('not-an-object-' + PASSWORD, ['malformed', PRIVATE]),
                ({'users': {'password': PASSWORD}}, {'tls': PRIVATE}),
                ({'users': [None, PASSWORD, {'uuid': UUID}]}, {'tls': {'enabled': True}})):
            with database.SessionLocal() as db:
                row = database.Inbound(core='sing-box', protocol='hysteria2', port=19345,
                                       settings=settings, stream_settings=stream, enable=True)
                db.add(row); db.commit(); db.refresh(row)
                raw = json.dumps(to_dict(row))
                for secret in SECRET_VALUES: self.assertNotIn(secret, raw)
            self.assert_safe(self.client.get('/api/inbounds'))

    def test_certificate_eligibility_requires_explicit_supported_tls_without_exposing_material(self):
        for core, protocol, stream, expected in (
                ('sing-box', 'vless', {'tls': {'enabled': True}}, True),
                ('sing-box', 'trojan', {'tls': {'enabled': True}}, True),
                ('sing-box', 'vmess', {'tls': {'enabled': True}}, True),
                ('sing-box', 'hysteria2', {'tls': {'enabled': True}}, True),
                ('sing-box', 'vless', {'tls': {'enabled': True, 'reality': {}}}, False),
                ('sing-box', 'vless', {'tls': {'enabled': 'true'}}, False),
                ('sing-box', 'vless', {}, False), ('sing-box', 'tuic', {'tls': {'enabled': True}}, True),
                ('sing-box', 'shadowsocks', {'tls': {'enabled': True}}, False),
                ('xray', 'vless', {'tls': {'enabled': True}}, False)):
            with self.subTest(core=core, protocol=protocol, stream=stream):
                malformed_reality = core == 'sing-box' and protocol == 'vless' and 'reality' in stream.get('tls', {})
                identity, _, _ = self.create(core, protocol, {}, stream={} if malformed_reality else stream)
                if malformed_reality:
                    with database.SessionLocal() as db:
                        db.get(database.Inbound, identity).stream_settings = deepcopy(stream)
                        db.commit()
                row = next(x for x in self.client.get('/api/inbounds').json() if x['id'] == identity)
                self.assertIs(row['managed_certificate_eligible'], expected)

    def test_privileged_editor_retains_manual_path_contract_but_not_secret_contents(self):
        from app.services.protocol_profiles import compile_profile
        settings, stream = compile_profile('sing-box', 'vless', {
            'security': 'reality', 'transport': 'direct', 'flow': 'xtls-rprx-vision', 'client_fingerprint': 'chrome', 'reality_target': 'target.example.test:443',
            'reality_server_name': 'vpn.example.test'}, {'users': [{'uuid': UUID}]}, {})
        identity, _, _ = self.create('sing-box', 'vless', settings, stream=stream)
        private_key = self.snapshot(identity)[1]['tls']['reality']['private_key']
        editor = self.client.get(f'/api/inbounds/{identity}/editor')
        self.assertEqual(editor.status_code, 200, editor.text)
        self.assertNotIn(private_key, editor.text); self.assertNotIn(UUID, editor.text)
        identity, _, _ = self.create('sing-box', 'hysteria2',
            {'users': [{'password': PASSWORD}], 'obfs': {'type': 'salamander', 'password': OBFS}},
            stream={'tls': {'enabled': True, 'server_name': 'vpn.example.test',
                    'certificate_path': '/synthetic/cert.pem', 'key_path': '/synthetic/private.pem'}})
        editor = self.client.get(f'/api/inbounds/{identity}/editor')
        self.assertEqual(editor.status_code, 200, editor.text)
        self.assertNotIn(PASSWORD, editor.text); self.assertNotIn(OBFS, editor.text)
        self.assertEqual(editor.json()['profile']['key_path'], '/synthetic/private.pem')
        with self.client.__class__(self.client.app, base_url=auth_tests.ORIGIN, client=('127.0.0.1', 55461)) as public:
            for path in ('/api/inbounds', '/api/xray/inbounds', '/api/singbox/inbounds', f'/api/inbounds/{identity}/editor'):
                response = public.get(path)
                self.assertEqual(response.status_code, 401); self.assertNotIn('/synthetic/', response.text)

    def test_intentional_admin_and_scoped_client_exports_keep_required_credentials(self):
        stream = {'tls': {'enabled': True, 'server_name': 'vpn.example.test',
                    'certificate_path': '/synthetic/cert.pem', 'key_path': '/synthetic/private.pem'},
                  '_vui': {'security': 'tls', 'server_name': 'vpn.example.test', 'client_fingerprint': '', 'skip_cert_verify': False}}
        for protocol, settings in (('vless', {'users': [{'uuid': UUID}]}), ('trojan', {'users': [{'password': PASSWORD}]}),
                ('vmess', {'users': [{'uuid': UUID}]}), ('hysteria2', {'users': [{'password': PASSWORD}]}),
                ('shadowsocks', {'method': 'aes-128-gcm', 'password': PASSWORD})):
            with self.subTest(protocol=protocol):
                identity, _, _ = self.create('sing-box', protocol, settings, stream={} if protocol == 'shadowsocks' else stream)
                link = self.client.get(f'/api/subscription/link/{identity}?host=vpn.example.test')
                self.assertEqual(link.status_code, 200, link.text)
                grant = self.client.post('/api/subscriptions', headers=auth_tests.HEADERS, json={
                    'label': 'intentional-client-export', 'server': 'vpn.example.test', 'inbound_ids': [identity],
                    'formats': ['raw', 'mihomo.yaml', 'sing-box.json']})
                self.assertEqual(grant.status_code, 201, grant.text)
                with self.client.__class__(self.client.app, base_url=auth_tests.ORIGIN, client=('127.0.0.1', 55460)) as public:
                    raw = public.get(grant.json()['paths']['raw'])
                    mihomo = public.get(grant.json()['paths']['mihomo.yaml'])
                    singbox = public.get(grant.json()['paths']['sing-box.json'])
                self.assertEqual(base64.b64decode(raw.text).decode(), link.text)
                required = UUID if protocol in {'vless', 'vmess'} else PASSWORD
                for response in (mihomo, singbox):
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertIn(required, response.text); self.assertNotIn('/synthetic/', response.text)
                for response in (raw, mihomo, singbox):
                    self.assertIn('no-store', response.headers['cache-control']); self.assertNotIn('set-cookie', response.headers)
