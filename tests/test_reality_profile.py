"""Literal REALITY/Vision validation and non-destructive credential lifecycle."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from app.api import inbounds as inbound_api
from app.models import database
from app.services import reality_profile as reality
from app.services.inbound_service import _prepared_payload, editor_dict, to_dict
from app.services.protocol_profiles import compile_profile, decompile_profile
import test_auth as auth_tests

UUID = '11111111-1111-4111-8111-111111111111'
SHORT = '0123456789abcdef'


def form():
    return {'security': 'reality', 'transport': 'direct', 'flow': reality.FLOW,
            'reality_target': 'reference.example.test:443',
            'reality_server_name': 'reference.example.test', 'client_fingerprint': 'chrome',
            'skip_cert_verify': False, 'reality_uuid': '', 'reality_short_id': ''}


def reality_node():
    private, public = reality.generate_keypair()
    return SimpleNamespace(id=47, core='sing-box', protocol='vless', port=10447,
        remark='REALITY bounded', enable=True, expiry_time=0, tag='reality-in',
        user_id=None, up=0, down=0, total=0,
        settings={'users': [{'uuid': UUID, 'flow': reality.FLOW}]}, stream_settings={
            'tls': {'enabled': True, 'server_name': 'reference.example.test', 'reality': {
                'enabled': True, 'handshake': {'server': 'reference.example.test', 'server_port': 443},
                'private_key': private, 'short_id': [SHORT]}},
            '_vui': {'security': 'reality', 'server_name': 'reference.example.test',
                'client_fingerprint': 'chrome', 'reality_public_key': public, 'reality_short_id': SHORT}})


def state(item):
    return deepcopy((item.settings, item.stream_settings))


def mutations():
    return {
        'settings': {'unknown': ['secret'], 'users': [None, [], {}, ['bad'], [{}, {}]]},
        'user': {'uuid': ['', None, 1, 'AAAAAAAA-1111-4111-8111-111111111111', UUID + ' ', 'bad'],
                 'flow': ['', None, False, 'xtls-rprx-vision-udp443', ' xtls-rprx-vision'],
                 'name': [None, {}], 'unknown': ['secret']},
        'stream': {'unknown': ['secret'], 'transport': [None, {}, {'type': 'tcp'}, {'type': 'ws'}, {'type': []}, {'type': {}}],
                   'multiplex': [{}, {'enabled': False}]},
        'tls': {'enabled': [False, 1, 'true'], 'server_name': ['', None, ' reference.example.test',
                    'reference.example.test.', '*.example.test', '127.0.0.1', '::1', 'example.test:443',
                    'https://example.test', 'référence.test', 'a' * 64 + '.test'],
                'alpn': [None, [], ['h2']], 'certificate_path': ['', '/private/cert.pem'],
                'key_path': ['', '/private/key.pem'], 'certificate': [[]], 'insecure': [False]},
        'reality': {'enabled': [False, 1, None], 'short_id': [[], [SHORT, SHORT], SHORT,
                    [''], ['AB0123456789abcd'], ['1234'], ['0123456789abcde'], ['0123456789abcdef00']],
                    'private_key': ['', None, 'a' * 42, 'a' * 44, 'a' * 43 + '='],
                    'max_time_difference': ['1m'], 'unknown': ['secret']},
        'handshake': {'server': ['', None, ' reference.example.test ', '0.0.0.0', '::',
                     'https://example.test', 'example.test/path', '*.example.test', 'fe80::1%eth0'],
                     'server_port': [None, False, 0, 65536, '443'], 'detour': ['secret']},
        'meta': {'security': [None, 'REALITY', 'tls'], 'server_name': [None, 'other.example.test'],
                 'client_fingerprint': ['', None, 'firefox', 'Chrome'], 'skip_cert_verify': [True, 0, None],
                 'reality_short_id': [None, '1123456789abcdef'], 'reality_public_key': ['', None, 'a' * 42],
                 'mihomo_compatibility': [None, 'xray-v26.7.11-plus-risk'], 'unknown': ['secret']},
    }


def target(item, name):
    paths = {'settings': (), 'user': ('users', 0), 'stream': (), 'tls': ('tls',),
             'reality': ('tls', 'reality'), 'handshake': ('tls', 'reality', 'handshake'), 'meta': ('_vui',)}
    result = item.settings if name in ('settings', 'user') else item.stream_settings
    for part in paths[name]: result = result[part]
    return result


class RealityValidationTests(unittest.TestCase):
    def test_shared_validator_contract_and_canonical_pair(self):
        item = reality_node()
        value = reality.validate_stored(item.settings, item.stream_settings)
        self.assertEqual(set(value), {'uuid', 'flow', 'server_name', 'handshake_server',
            'handshake_port', 'private_key', 'public_key', 'short_id'})
        self.assertEqual(value['uuid'], UUID)
        self.assertEqual(value['flow'], reality.FLOW)
        self.assertEqual(len(reality.key_bytes(value['private_key'])), 32)
        self.assertEqual(len(reality.key_bytes(value['public_key'])), 32)
        self.assertNotEqual(value['private_key'], value['public_key'])

    def test_every_unsupported_or_nonliteral_persisted_field_rejected_without_mutation(self):
        for location, fields in mutations().items():
            for field, values in fields.items():
                for value in values:
                    item = reality_node(); target(item, location)[field] = value
                    before = state(item)
                    with self.subTest(location=location, field=field, value=repr(value)):
                        with self.assertRaises(ValueError): reality.validate_stored(*before)
                        self.assertEqual(state(item), before)

    def test_missing_required_fields_rejected(self):
        for location, fields in {'user': ('uuid', 'flow'), 'tls': ('enabled', 'server_name', 'reality'),
                'reality': ('enabled', 'private_key', 'short_id', 'handshake'),
                'handshake': ('server', 'server_port'),
                'meta': ('security', 'client_fingerprint', 'reality_public_key')}.items():
            for field in fields:
                item = reality_node(); del target(item, location)[field]
                with self.subTest(location=location, field=field), self.assertRaises(ValueError):
                    reality.validate_stored(item.settings, item.stream_settings)

    def test_mismatched_pair_and_noncanonical_trailing_base64_bits_rejected(self):
        item = reality_node(); _, other_public = reality.generate_keypair()
        item.stream_settings['_vui']['reality_public_key'] = other_public
        with self.assertRaisesRegex(ValueError, 'does not match'):
            reality.validate_stored(item.settings, item.stream_settings)
        alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
        for location, field in (('reality', 'private_key'), ('meta', 'reality_public_key')):
            item = reality_node(); mapping = target(item, location); original = mapping[field]
            mapping[field] = original[:-1] + alphabet[alphabet.index(original[-1]) + 1]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'canonical'):
                reality.validate_stored(item.settings, item.stream_settings)

    def test_detection_is_broad_but_non_direct_drafts_are_separate(self):
        for value in (None, {}, False, 0, [], 'bad'):
            self.assertTrue(reality.has_reality({'tls': {'reality': value}}))
        for stream in ({'_vui': {'security': 'REALITY'}}, {'_vui': {'reality_public_key': None}},
                       {'realitySettings': {}}, {'security': 'reality'}):
            self.assertTrue(reality.has_reality(stream))
        self.assertFalse(reality.has_reality(None))
        self.assertFalse(reality.has_reality({'tls': {'enabled': True}}))
        item = reality_node(); item.stream_settings['transport'] = {'type': 'grpc', 'service_name': 'Service'}
        self.assertFalse(reality.is_direct_candidate(item.stream_settings))
        with self.assertRaises(ValueError): reality.validate_stored(item.settings, item.stream_settings)

    def test_literal_sni_case_and_bracketed_target_preserved(self):
        profile = {**form(), 'reality_server_name': 'Reference.Example.Test',
                   'reality_target': '[2001:db8::1]:8443'}
        result = _prepared_payload({'core': 'sing-box', 'protocol': 'vless', 'port': 10447, 'profile': profile})
        value = reality.validate_stored(*result[3:])
        self.assertEqual(value['server_name'], 'Reference.Example.Test')
        self.assertEqual((value['handshake_server'], value['handshake_port']), ('2001:db8::1', 8443))
        self.assertEqual(reality.editor_profile(*result[3:])['reality_target'], '[2001:db8::1]:8443')


class RealityEditorTests(unittest.TestCase):
    def test_creation_generates_once_then_blank_and_omitted_edits_preserve_all_secrets(self):
        with patch.object(reality, 'generate_keypair', wraps=reality.generate_keypair) as keys, \
             patch.object(reality.secrets, 'token_hex', wraps=reality.secrets.token_hex) as shorts, \
             patch.object(reality.uuid, 'uuid4', wraps=reality.uuid.uuid4) as uuids:
            result = _prepared_payload({'core': 'sing-box', 'protocol': 'vless', 'port': 10447, 'profile': form()})
            self.assertEqual((keys.call_count, shorts.call_count, uuids.call_count), (1, 1, 1))
            item = reality_node(); item.settings, item.stream_settings = result[3:]
            expected = state(item)
            keys.reset_mock(); shorts.reset_mock(); uuids.reset_mock()
            for _ in range(3):
                editor = editor_dict(item); profile = editor['profile']
                self.assertEqual(profile['reality_uuid'], '')
                self.assertEqual(profile['reality_short_id'], '')
                self.assertNotIn('reality_private_key', profile)
                for flag in ('reality_uuid_set', 'reality_short_id_set', 'reality_private_key_set'):
                    self.assertIs(profile[flag], True)
                value = reality.validate_stored(*expected)
                for secret in (value['uuid'], value['short_id'], value['private_key']):
                    self.assertNotIn(secret, json.dumps(editor))
                    self.assertNotIn(secret, json.dumps(to_dict(item)))
                for changes in ({'profile': profile}, {'profile': {'reality_uuid': '', 'reality_short_id': ''}},
                                {'remark': 'rename'}, {'profile': {}}):
                    self.assertEqual(_prepared_payload(changes, item)[3:], expected)
            keys.assert_not_called(); shorts.assert_not_called(); uuids.assert_not_called()

    def test_replacements_are_independent_and_do_not_mutate_input(self):
        for field, replacement in (('reality_uuid', '22222222-2222-4222-8222-222222222222'),
                                   ('reality_short_id', 'fedcba9876543210')):
            item = reality_node(); before = state(item)
            result = _prepared_payload({'profile': {field: replacement}}, item)
            value = reality.validate_stored(*result[3:]); original = reality.validate_stored(*before)
            expected = {**original, 'uuid' if field == 'reality_uuid' else 'short_id': replacement}
            self.assertEqual(value, expected)
            self.assertEqual(state(item), before)

    def test_malformed_editor_mapping_and_transport_return_controlled_rejection(self):
        for settings in (None, [], 'invalid', {'users': [None]}, {'users': ['invalid']}):
            item = reality_node(); item.settings = settings
            with self.subTest(settings=settings), self.assertRaises(HTTPException) as caught:
                editor_dict(item)
            self.assertEqual(caught.exception.status_code, 422)
        for transport in (None, {}, {'type': []}, {'type': {}}, {'type': 'GRPC'}):
            item = reality_node(); item.stream_settings['transport'] = transport
            with self.subTest(transport=transport), self.assertRaises(HTTPException) as caught:
                _prepared_payload({'profile': form()}, item)
            self.assertEqual(caught.exception.status_code, 422)

    def test_raw_creation_preserves_valid_imported_pair_without_generating(self):
        item = reality_node()
        with patch.object(reality, 'generate_keypair') as keys, patch.object(reality.uuid, 'uuid4') as uuids, \
             patch.object(reality.secrets, 'token_hex') as shorts:
            result = _prepared_payload({'core': item.core, 'protocol': item.protocol, 'port': item.port,
                'settings': item.settings, 'stream_settings': item.stream_settings})
        self.assertEqual(result[3:], state(item))
        keys.assert_not_called(); uuids.assert_not_called(); shorts.assert_not_called()

    def test_optional_stored_metadata_representation_is_not_normalized(self):
        item = reality_node()
        del item.stream_settings['_vui']['server_name']; del item.stream_settings['_vui']['reality_short_id']
        item.stream_settings['_vui'].update(skip_cert_verify=False, mihomo_compatibility='supported')
        self.assertEqual(_prepared_payload({'profile': editor_dict(item)['profile']}, item)[3:], state(item))

    def test_malformed_existing_credentials_never_generate_even_on_leave_or_rename(self):
        for location, field, value in (('user', 'uuid', None), ('user', 'uuid', ''),
                ('reality', 'private_key', ''), ('meta', 'reality_public_key', ''),
                ('reality', 'short_id', []), ('settings', 'users', [])):
            item = reality_node(); target(item, location)[field] = value; before = state(item)
            for payload in ({'remark': 'rename'}, {'profile': {}}, {'profile': form()},
                            {'profile': {'security': 'none', 'transport': 'direct', 'flow': ''}}):
                with self.subTest(location=location, field=field, payload=payload), \
                     patch.object(reality, 'generate_keypair') as keys, patch.object(reality.uuid, 'uuid4') as uuids, \
                     patch.object(reality.secrets, 'token_hex') as shorts:
                    with self.assertRaises(HTTPException) as caught: _prepared_payload(payload, item)
                    self.assertEqual(caught.exception.status_code, 422)
                    keys.assert_not_called(); uuids.assert_not_called(); shorts.assert_not_called()
                    self.assertEqual(state(item), before)

    def test_unknown_fields_reject_editor_and_edit_including_explicit_leave(self):
        for location in ('settings', 'user', 'stream', 'tls', 'reality', 'handshake', 'meta'):
            item = reality_node(); target(item, location)['unknown'] = 'must-not-disappear'; before = state(item)
            with self.subTest(location=location):
                with self.assertRaises(HTTPException): editor_dict(item)
                for profile in (form(), {'security': 'none', 'transport': 'direct', 'flow': ''}):
                    with self.assertRaises(HTTPException): _prepared_payload({'profile': profile}, item)
                self.assertEqual(state(item), before)

    def test_invalid_visual_fields_and_types_are_never_coerced(self):
        invalid = {'security': ['REALITY', None, True], 'transport': ['DIRECT', [], None, 'tcp'],
            'flow': ['', None, True], 'client_fingerprint': ['Chrome', '', None, 'firefox'],
            'skip_cert_verify': [True, 0, None], 'reality_uuid': [None, False, UUID + ' '],
            'reality_short_id': [None, False, '1234', SHORT.upper()],
            'reality_target': [None, '', ' reference.example.test:443', 'https://example.test', '::1:443',
                               'example.test:0443', '[example.test]:443'],
            'reality_server_name': [None, '', '127.0.0.1', '*.example.test'],
            'alpn': [None, ['h2']], 'multiplex': [{}], 'certificate_path': ['', '/private/cert.pem'],
            'reality_private_key': ['', 'secret'], 'reality_public_key': ['', 'secret'],
            'reality_uuid_set': [0, None], 'host': [''], 'unknown': [None]}
        for field, values in invalid.items():
            for value in values:
                item = reality_node(); before = state(item)
                with self.subTest(field=field, value=repr(value)), self.assertRaises(HTTPException):
                    _prepared_payload({'profile': {**form(), field: value}}, item)
                self.assertEqual(state(item), before)

    def test_initial_transition_preserves_uuid_and_return_to_tls_removes_vision(self):
        item = reality_node(); item.settings['users'][0].pop('flow')
        item.stream_settings = {'tls': {'enabled': True, 'server_name': 'vpn.example.test',
            'certificate_path': '/managed/cert.pem', 'key_path': '/managed/key.pem'},
            '_vui': {'security': 'tls', 'server_name': 'vpn.example.test'}}
        result = _prepared_payload({'profile': form()}, item)
        self.assertEqual(result[3]['users'][0], {'uuid': UUID, 'flow': reality.FLOW})
        item.settings, item.stream_settings = result[3:]
        result = _prepared_payload({'profile': {'security': 'tls', 'transport': 'direct', 'flow': '',
            'server_name': 'vpn.example.test', 'certificate_path': '/cert.pem', 'key_path': '/key.pem'}}, item)
        self.assertEqual(result[3]['users'][0], {'uuid': UUID})
        self.assertNotIn('reality', result[4]['tls'])
        self.assertEqual(result[4]['_vui']['security'], 'tls')

    def test_imported_migration_options_cannot_be_erased(self):
        for location, field, value in (('settings', 'unknown', True), ('user', 'flow', 'unsupported-flow'),
                ('tls', 'alpn', ['h2']), ('tls', 'unknown', True), ('meta', 'unknown', True),
                ('stream', 'multiplex', {'enabled': False})):
            item = reality_node(); item.settings['users'][0].pop('flow')
            item.stream_settings = {'tls': {'enabled': True, 'server_name': 'vpn.example.test',
                'certificate_path': '/cert.pem', 'key_path': '/key.pem'}, '_vui': {'security': 'tls'}}
            target(item, location)[field] = value; before = state(item)
            with self.subTest(location=location, field=field), self.assertRaises(HTTPException):
                _prepared_payload({'profile': form()}, item)
            self.assertEqual(state(item), before)

    def test_xray_reality_draft_behavior_is_separate(self):
        settings, stream = compile_profile('xray', 'vless', {**form(), 'transport': 'raw',
            'client_fingerprint': 'firefox', 'reality_short_id': 'abcd'},
            {'users': [{'id': UUID}]}, {})
        self.assertEqual(stream['realitySettings']['shortIds'], ['abcd'])
        self.assertEqual(stream['_vui']['client_fingerprint'], 'firefox')
        self.assertEqual(decompile_profile('xray', 'vless', settings, stream)['security'], 'reality')


class RealityEditorAPITests(unittest.TestCase):
    def setUp(self):
        auth_tests.AuthenticationTests.setUp(self)
        self.assertEqual(auth_tests.AuthenticationTests.login(self).status_code, 200)
        self.apply = patch.object(inbound_api, 'apply_checked', return_value={'applied': True}).start()

    def test_create_roundtrip_replacement_and_invalid_edit_are_atomic_and_redacted(self):
        response = self.client.post('/api/inbounds', headers=auth_tests.HEADERS, json={
            'core': 'sing-box', 'protocol': 'vless', 'port': 10447,
            'profile': {**form(), 'reality_uuid': UUID, 'reality_short_id': SHORT}})
        self.assertEqual(response.status_code, 200, response.text)
        identity = response.json()['inbound']['id']
        with database.SessionLocal() as db:
            saved = state(db.get(database.Inbound, identity))
        for secret in (UUID, SHORT, saved[1]['tls']['reality']['private_key']):
            self.assertNotIn(secret, response.text)
        for _ in range(2):
            response = self.client.get(f'/api/inbounds/{identity}/editor')
            self.assertEqual(response.status_code, 200, response.text)
            profile = response.json()['profile']
            self.assertEqual(profile['reality_short_id'], '')
            result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS,
                json={'profile': profile, 'remark': 'changed'})
            self.assertEqual(result.status_code, 200, result.text)
            with database.SessionLocal() as db:
                self.assertEqual(state(db.get(database.Inbound, identity)), saved)
        self.apply.reset_mock()
        result = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS,
            json={'profile': {**profile, 'reality_short_id': 'bad'}})
        self.assertEqual(result.status_code, 422, result.text)
        self.apply.assert_not_called()
        with database.SessionLocal() as db:
            self.assertEqual(state(db.get(database.Inbound, identity)), saved)


if __name__ == '__main__': unittest.main()
