"""Authenticated TestClient + real isolated parser; no server sockets or core runs."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import mihomo_preview as endpoint
from app.services.mihomo_preview import MihomoPreviewError
from test_mihomo_preview import SECRET, document, encode, node
import main

ORIGIN = 'https://panel.example.test'
HEADERS = {'Origin': ORIGIN, 'X-VUI-Request': '1'}
URL = '/api/proxy/yaml/preview'


class MihomoPreviewApiTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch('app.middleware.auth.configured_origin', return_value=ORIGIN).start()
        self.auth = patch('app.middleware.auth.authenticate', return_value={'id': 1}).start()
        # Existing authentication may read session storage in production; these
        # guards only assert that the preview business route never uses stores
        # or runtime application services.
        self.store = patch('app.api.proxy.get_store', side_effect=AssertionError('No proxy store access')).start()
        self.apply = patch.object(main.core_manager, 'apply_database', side_effect=AssertionError('No core apply')).start()
        self.client = TestClient(main.app, base_url=ORIGIN)

    def post(self, payload, **kwargs):
        response = self.client.post(URL, json=payload, headers=kwargs.pop('headers', HEADERS), **kwargs)
        self.assertNotIn(SECRET, response.text)
        self.assertIn('no-store', response.headers['cache-control'])
        self.store.assert_not_called()
        self.apply.assert_not_called()
        return response

    def test_real_preview_outputs_value_free_text_without_config_access(self):
        candidate = {**document(), 'secret': SECRET}
        response = self.post({'candidate': encode(candidate), 'baseline': 'mode: direct'})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(set(result), {'schema_version', 'can_apply', 'text'})
        self.assertEqual(result['schema_version'], 1)
        self.assertIs(result['can_apply'], False)
        self.assertIn('不可应用', result['text'])
        self.assertIn('结构有变化', result['text'])
        self.assertIn('隐藏的其他区块：1 个', result['text'])
        for value in (node()['name'], node()['uuid'], node()['server'], 'example.test'):
            self.assertNotIn(value, response.text)

    def test_baseline_is_optional_or_null_never_implicitly_reads_configuration(self):
        for payload in ({'candidate': '{}'}, {'candidate': '{}', 'baseline': None}):
            with self.subTest(payload=payload):
                response = self.post(payload)
                self.assertEqual(response.status_code, 200)
                self.assertIn('未提供基线，未比较', response.json()['text'])

    def test_conflicts_are_read_only_success_not_apply_authorization(self):
        response = self.post({'candidate': 'rules: ["MATCH,DIRECT", "MATCH,REJECT"]'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('RULE_TARGET_CONFLICT', response.json()['text'])
        self.assertFalse(response.json()['can_apply'])

    def test_authentication_and_csrf_precede_parser(self):
        with patch.object(endpoint, 'preview_mihomo') as parser:
            self.auth.return_value = None
            response = self.post({'candidate': SECRET})
            self.assertEqual(response.status_code, 401)
            self.auth.return_value = {'id': 1}
            for headers in ({}, {'Origin': ORIGIN}, {'X-VUI-Request': '1'},
                            {**HEADERS, 'Origin': 'https://elsewhere.test'}):
                response = self.post({'candidate': SECRET}, headers=headers)
                self.assertEqual(response.status_code, 403)
            parser.assert_not_called()

    def test_strict_payload_errors_do_not_echo_input_or_unknown_field_names(self):
        payloads = ({}, {'candidate': ''}, {'candidate': 1}, {'candidate': False},
                    {'candidate': {}}, {'candidate': '{}', SECRET: SECRET},
                    {'candidate': '{}', 'baseline': ''}, {'candidate': '{}', 'baseline': []},
                    {'candidate': '{}', 'baseline': 7}, {'candidate': 'x' * 1048577})
        with patch.object(endpoint, 'preview_mihomo') as parser:
            for payload in payloads:
                with self.subTest(kind=list(payload)):
                    response = self.post(payload)
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(response.json(), {'detail': 'Invalid request'})
            parser.assert_not_called()

    def test_utf8_byte_limit_independent_of_character_limit(self):
        for source in ('candidate', 'baseline'):
            payload = {'candidate': '{}', source: '中' * 350000}
            response = self.post(payload)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.json()['detail'], {'code': 'INPUT_LIMIT', 'source': source})

    def test_real_yaml_errors_show_fixed_source_and_code_only(self):
        for source in ('candidate', 'baseline'):
            payload = {'candidate': '{}', source: 'x: &anchor ' + SECRET}
            response = self.post(payload)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()['detail'], {'code': 'YAML_REFERENCE', 'source': source})

    def test_parser_capacity_and_runtime_errors_are_controlled(self):
        for code, status in (('PARSER_BUSY', 429), ('PARSE_TIMEOUT', 503), ('PARSER_FAILED', 503),
                             ('PARSER_UNAVAILABLE', 503), ('PARSER_RESOURCE_LIMIT', 503),
                             ('PARSER_LIMIT_UNAVAILABLE', 503), ('PARSE_SYNTAX', 422)):
            with self.subTest(code=code), patch.object(endpoint, 'preview_mihomo', side_effect=MihomoPreviewError(code)):
                response = self.post({'candidate': SECRET})
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()['detail'], {'code': code, 'source': 'pair'})

    def test_malformed_success_report_is_rejected_before_rendering(self):
        with patch.object(endpoint, 'preview_mihomo', return_value={'text': SECRET}):
            response = self.post({'candidate': '{}'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['detail']['code'], 'PARSER_FAILED')

    def test_actual_body_limit_includes_json_escaping_and_rejects_before_parser(self):
        with patch.object(endpoint, 'preview_mihomo') as parser:
            response = self.client.post(URL, content=b' ' * (2097152 + 1),
                                        headers={**HEADERS, 'Content-Type': 'application/json'})
            self.assertEqual(response.status_code, 413)
            self.assertIn('no-store', response.headers['cache-control'])
            parser.assert_not_called()

    def test_malformed_json_is_fixed_and_get_is_not_an_inspection_route(self):
        with patch.object(endpoint, 'preview_mihomo') as parser:
            response = self.client.post(URL, content='{"candidate":"' + SECRET,
                                        headers={**HEADERS, 'Content-Type': 'application/json'})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json(), {'detail': 'Invalid request'})
            self.assertNotIn(SECRET, response.text)
            response = self.client.get(URL)
            self.assertEqual(response.status_code, 405)
            parser.assert_not_called()

    def test_selected_candidate_and_baseline_are_not_modified(self):
        # The endpoint accepts content, never filenames; explicit local reads
        # in this fixture demonstrate that the original selected files stay put.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current, baseline = root / 'current.yml', root / 'baseline.yml'
            current.write_text(encode(document()))
            baseline.write_text('{}')
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            response = self.post({'candidate': current.read_text(), 'baseline': baseline.read_text()})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})


if __name__ == '__main__':
    unittest.main()
