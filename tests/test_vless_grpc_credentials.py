"""gRPC edits must never generate credentials from malformed imported settings."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.api import inbounds as inbound_api
from app.models import database
from app.services.inbound_service import _prepared_payload, ensure_credentials
from app.services.protocol_profiles import decompile_profile
from app.services.validated_export import ExportError, validated_node
import test_auth as auth_tests
from test_inbound_security_binding import BindingManager
from test_vless_grpc_profile import grpc_node


INVALID_SETTINGS = (
    {}, {"users": []}, {"users": [{}]}, {"users": [{"uuid": ""}]},
    {"users": [{"uuid": None}]}, {"users": [{"uuid": False}]},
    {"users": [{"uuid": "not-a-uuid"}]}, {"users": [None]},
)


class GRPCCredentialPreparationTests(unittest.TestCase):
    def test_edit_preparation_rejects_missing_secret_without_generating_or_mutating(self):
        for settings in INVALID_SETTINGS:
            for payload in ({"remark": "rename"}, {"profile": {"transport": "grpc", "security": "tls", "service_name": "Edited"}}, {"profile": {"transport": "direct", "security": "none"}}):
                item = grpc_node(); item.settings = deepcopy(settings)
                before = deepcopy(item.__dict__)
                with self.subTest(settings=settings, payload=payload), patch('app.services.inbound_service.uuid.uuid4') as generate:
                    with self.assertRaises(HTTPException) as caught:
                        _prepared_payload(payload, existing=item)
                    self.assertEqual(caught.exception.status_code, 422)
                    generate.assert_not_called()
                self.assertEqual(item.__dict__, before)
                with self.assertRaises(ExportError): validated_node(item, 'vpn.example.test')

    def test_malformed_case_grpc_cannot_regenerate_missing_uuid_when_leaving(self):
        item = grpc_node()
        item.stream_settings['transport']['type'] = 'GRPC'
        item.settings = {'users': [{'uuid': None}]}
        before = deepcopy(item.__dict__)
        with patch('app.services.inbound_service.uuid.uuid4') as generate:
            with self.assertRaises(HTTPException):
                _prepared_payload({'profile': {'transport': 'direct', 'security': 'none'}}, item)
            generate.assert_not_called()
        self.assertEqual(item.__dict__, before)

    def test_new_credentials_are_generated_only_on_a_copy_and_valid_edits_keep_uuid(self):
        original = {"users": [{"uuid": None}]}
        before = deepcopy(original)
        created = ensure_credentials('sing-box', 'vless', original)
        self.assertEqual(original, before)
        self.assertTrue(created['users'][0]['uuid'])
        with self.assertRaises(HTTPException):
            _prepared_payload({'core': 'sing-box', 'protocol': 'vless', 'port': 10443,
                'settings': original, 'profile': {'transport': 'grpc', 'service_name': '/invalid'}})
        self.assertEqual(original, before)
        item = grpc_node()
        profile = decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)
        with patch('app.services.inbound_service.uuid.uuid4') as generate:
            result = _prepared_payload({'remark': 'rename', 'profile': {**profile, 'service_name': 'Changed.Service'}}, item)
            generate.assert_not_called()
        self.assertEqual(result[3], item.settings)
        self.assertEqual(result[4]['transport']['service_name'], 'Changed.Service')


class GRPCCredentialAPITests(unittest.TestCase):
    def setUp(self):
        auth_tests.AuthenticationTests.setUp(self)
        self.assertEqual(auth_tests.AuthenticationTests.login(self).status_code, 200)
        patch('app.certificates.manager.get_manager', return_value=BindingManager()).start()
        self.apply = patch.object(inbound_api, 'apply_checked', return_value={'applied': True}).start()

    def test_imported_missing_uuid_cannot_be_repaired_by_remark_transport_or_flow_edit(self):
        result = self.client.post('/api/inbounds', headers=auth_tests.HEADERS, json={
            'core': 'sing-box', 'protocol': 'vless', 'remark': 'credential-guard', 'port': 10455,
            'profile': {'security': 'tls', 'transport': 'grpc', 'service_name': 'Credential.Service'},
            'certificate_id': 'a' * 32})
        self.assertEqual(result.status_code, 200, result.text)
        identity = result.json()['inbound']['id']
        state = self.client.get(f'/api/inbounds/{identity}/editor').json()
        self.apply.reset_mock()
        for settings in INVALID_SETTINGS:
            settings = deepcopy(settings)
            for changes in ({'remark': 'unrelated'}, {'profile': state['profile']},
                            {'profile': {**state['profile'], 'flow': ''}},
                            {'profile': {**state['profile'], 'service_name': 'Edited.Service'}}):
                with self.subTest(settings=settings, changes=changes):
                    with database.SessionLocal() as db:
                        row = db.get(database.Inbound, identity)
                        row.settings = deepcopy(settings)
                        db.commit()
                        stream = deepcopy(row.stream_settings)
                    with patch('app.services.inbound_service.uuid.uuid4') as generate:
                        response = self.client.put(f'/api/inbounds/{identity}', headers=auth_tests.HEADERS, json=changes)
                        self.assertEqual(response.status_code, 422, response.text)
                        generate.assert_not_called()
                    with database.SessionLocal() as db:
                        row = db.get(database.Inbound, identity)
                        self.assertEqual(row.settings, settings)
                        self.assertEqual(row.stream_settings, stream)
        self.apply.assert_not_called()


if __name__ == '__main__':
    unittest.main()
