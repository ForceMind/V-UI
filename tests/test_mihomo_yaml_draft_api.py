"""Authenticated YAML draft writes in private temporary state, without core apply."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.services.mihomo_merge_review import review_mihomo_merge
from app.services.proxy_store import ProxyStore
from test_mihomo_merge_plan import BASELINE
import main


ORIGIN = 'https://panel.example.test'
HEADERS = {'Origin': ORIGIN, 'X-VUI-Request': '1'}
URL = '/api/proxy/yaml/draft'


class MihomoYamlDraftApiTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        directory = tempfile.TemporaryDirectory(prefix='vui-yaml-api-')
        self.addCleanup(directory.cleanup)
        self.store = ProxyStore(Path(directory.name).resolve())
        self.before = self.store.read_snapshot()
        patch('app.middleware.auth.configured_origin', return_value=ORIGIN).start()
        self.auth = patch('app.middleware.auth.authenticate', return_value={'id': 1}).start()
        patch('app.api.proxy.get_store', return_value=self.store).start()
        self.apply = patch.object(main.core_manager, 'apply_proxy',
                                  side_effect=AssertionError('Draft save cannot apply')).start()
        self.client = TestClient(main.app, base_url=ORIGIN)
        candidate = 'rules: ["MATCH,Chosen"]'
        review = review_mihomo_merge(BASELINE, candidate)
        self.payload = {'baseline': BASELINE, 'candidate': candidate, 'fingerprint': review['fingerprint'],
                        'selections': [], 'group_members': {'Chosen': 'upstream'}, 'dns_upstream': 'upstream'}

    def post(self, payload=None, headers=None):
        return self.client.post(URL, json=self.payload if payload is None else payload,
                                headers=headers or {**HEADERS, 'If-Match': self.before['revision']})

    def test_save_and_clear_require_cas_and_keep_private_material_out_of_response(self):
        saved = self.post()
        self.assertEqual(saved.status_code, 200, saved.text)
        snapshot = saved.json()
        self.assertEqual(saved.headers['etag'], '"' + snapshot['revision'] + '"')
        self.assertEqual(snapshot['execution_mode'], 'yaml')
        self.assertIn('no-store', saved.headers['cache-control'])
        for secret in ('relay.example.com', '11111111-', 'MATCH'):
            self.assertNotIn(secret, saved.text)
        self.assertEqual(self.store.load_draft()['yaml_plan'][1]['baseline'], BASELINE.encode())
        stale = self.post()
        self.assertEqual(stale.status_code, 409)
        cleared = self.client.delete(URL, headers={**HEADERS, 'If-Match': snapshot['revision']})
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(cleared.json()['execution_mode'], 'single')
        self.assertEqual(self.store.policy_schema_version(), 3)
        self.apply.assert_not_called()

    def test_invalid_or_stale_review_never_mutates_saved_state(self):
        for overrides in ({'candidate': self.payload['candidate']+'\n'},
                          {'group_members': {'Chosen': 'missing'}},
                          {'dns_upstream': 'missing'}):
            with self.subTest(overrides=overrides):
                rejected = self.post({**self.payload, **overrides})
                self.assertEqual(rejected.status_code, 422, rejected.text)
                self.assertEqual(self.store.read_snapshot(), self.before)
        self.apply.assert_not_called()

    def test_auth_origin_and_revision_admission_precede_worker(self):
        with patch('app.services.mihomo_merge_download.prepare_mihomo_draft') as worker:
            self.auth.return_value = None
            self.assertEqual(self.post().status_code, 401)
            self.auth.return_value = {'id': 1}
            self.assertEqual(self.post(headers={'If-Match': self.before['revision']}).status_code, 403)
            self.assertEqual(self.post(headers=HEADERS).status_code, 428)
            self.assertEqual(self.post(headers={**HEADERS, 'If-Match': '*'}).status_code, 422)
            duplicate = self.client.post(URL, json=self.payload,
                                        headers=list(HEADERS.items()) + [('If-Match', self.before['revision'])]*2)
            self.assertEqual(duplicate.status_code, 422)
            worker.assert_not_called()
        self.assertEqual(self.store.read_snapshot(), self.before)

    def test_duplicate_plan_selection_fields_are_refused_before_worker(self):
        with patch('app.services.mihomo_merge_download.prepare_mihomo_draft') as worker:
            body = '{"baseline":"{}", "candidate":"{}", "group_members":{}, "group_members":{"x":"y"}}'
            response = self.client.post(URL, content=body,
                                        headers={**HEADERS, 'If-Match': self.before['revision'],
                                                 'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 422)
        worker.assert_not_called()
