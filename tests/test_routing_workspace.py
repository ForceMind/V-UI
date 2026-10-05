"""Saved settings, draft previews and scoped subscriptions form one workflow."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import yaml
from app.services import routing_store
from app.services.routing_store import read_snapshot, save_routing, RoutingConflict, RoutingStorageError
from app.services.mihomo_routing import default_routing
import test_subscription_tokens as grant_tests
HEADERS = grant_tests.HEADERS
from app.models import database

class RoutingWorkspaceTests(unittest.TestCase):
    issue = grant_tests.SubscriptionTokenTests.issue
    public_get = grant_tests.SubscriptionTokenTests.public_get

    def setUp(self):
        grant_tests.SubscriptionTokenTests.setUp(self)
        self.routing_temp = tempfile.TemporaryDirectory(prefix='vui-routing-')
        self.addCleanup(self.routing_temp.cleanup)
        self.path = Path(self.routing_temp.name) / 'mihomo-routing.json'
        patch.object(routing_store, 'ROUTING_FILE', self.path).start()

    def put(self, settings, revision):
        return self.client.put('/api/routing/mihomo', headers={**HEADERS, 'If-Match':'"'+revision+'"'}, json=settings)

    def test_preview_is_draft_only_and_does_not_write_settings(self):
        before = read_snapshot()
        draft = {**default_routing(), 'proxy_domains':['custom.example.test']}
        response = self.client.post('/api/routing/mihomo/preview', headers=HEADERS, json=draft)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['source'], 'draft')
        self.assertIn('DOMAIN-SUFFIX,custom.example.test,FORCE_PROXY', str(response.json()))
        self.assertFalse(self.path.exists())
        self.assertEqual(read_snapshot(), before)

    def test_saved_revision_survives_new_process(self):
        before = read_snapshot()
        settings = {**default_routing(), 'mode':'direct', 'direct_domains':['persist.example.test']}
        response = self.put(settings, before['revision'])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['etag'], '"'+read_snapshot()['revision']+'"')
        env = {**os.environ, 'VUI_DATA_DIR':self.routing_temp.name}
        result = subprocess.run([sys.executable, '-c', 'import json;from app.services.routing_store import read_snapshot;print(json.dumps(read_snapshot()))'], env=env, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), read_snapshot())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_missing_or_stale_if_match_never_overwrites(self):
        before = read_snapshot()
        self.assertEqual(self.client.put('/api/routing/mihomo', headers=HEADERS, json={}).status_code, 428)
        self.assertEqual(self.put({'mode':'direct'}, before['revision']).status_code, 200)
        saved = self.path.read_bytes()
        self.assertEqual(self.put({'mode':'standard'}, before['revision']).status_code, 409)
        self.assertEqual(saved, self.path.read_bytes())

    def test_invalid_input_preserves_last_saved_file(self):
        initial = self.put({'mode':'direct'}, read_snapshot()['revision'])
        self.assertEqual(initial.status_code, 200)
        before = read_snapshot()
        for payload in ({'proxy_domains':['https://example.com:99999']}, {'presets':{'unknown':True}},
                        {'bypass_cgnat':'false'}, {'intranet':[{'suffix':'','nameservers':['system']}]},
                        {'mode':'direct','unknown':'discard-me'}, {'intranet':[{'suffix':'corp.example','nameservers':[]}]},
                        {'presets':{'openai':'false'}}):
            with self.subTest(payload=payload):
                self.assertEqual(self.put(payload, before['revision']).status_code, 422)
                self.assertEqual(read_snapshot(), before)

    def test_corruption_blocks_admin_and_public_export_without_default_fallback(self):
        grant = self.issue()
        self.path.write_text('{broken')
        self.assertEqual(self.client.get('/api/routing/mihomo/snapshot').status_code, 503)
        public = self.public_get(grant['paths']['mihomo.yaml'])
        self.assertEqual(public.status_code, 409)
        self.assertNotIn('MATCH,DIRECT', public.text)
        with self.assertRaises(RoutingStorageError): read_snapshot()
        self.assertEqual(self.path.read_text(), '{broken')

    def test_same_subscription_url_uses_latest_saved_settings_only(self):
        grant = self.issue()
        url = grant['paths']['mihomo.yaml']
        original = yaml.safe_load(self.public_get(url).text)
        settings = {**default_routing(), 'mode':'direct', 'proxy_domains':['release.example.test']}
        self.client.post('/api/routing/mihomo/preview', headers=HEADERS, json=settings)
        self.assertEqual(yaml.safe_load(self.public_get(url).text), original)
        self.assertEqual(self.put(settings, read_snapshot()['revision']).status_code, 200)
        changed = yaml.safe_load(self.public_get(url).text)
        self.assertEqual(changed['proxies'], original['proxies'])
        self.assertEqual(changed['rules'][-1], 'MATCH,DIRECT')
        self.assertIn('DOMAIN-SUFFIX,release.example.test,FORCE_PROXY', changed['rules'])
        self.assertEqual(list(changed['dns']['nameserver-policy']['+.release.example.test']), ['https://1.1.1.1/dns-query#FORCE_PROXY','https://8.8.8.8/dns-query#FORCE_PROXY'])

    def test_concurrent_saves_have_one_winner(self):
        old = read_snapshot()['revision']
        barrier = threading.Barrier(2)
        results = []
        def save(mode):
            barrier.wait()
            try: save_routing({'mode':mode}, old); results.append('saved')
            except RoutingConflict: results.append('conflict')
        threads = [threading.Thread(target=save,args=(mode,)) for mode in ('direct','standard')]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=5)
        self.assertCountEqual(results, ['saved','conflict'])

    def test_failure_before_atomic_commit_preserves_file(self):
        save_routing({}, read_snapshot()['revision'])
        before = self.path.read_bytes()
        with patch('app.services.routing_store.os.replace',side_effect=OSError('injected disk failure')):
            with self.assertRaises(RoutingStorageError): save_routing({'mode':'direct'},read_snapshot()['revision'])
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(list(self.path.parent.glob('.routing-stage-*')))

    def test_unverified_node_cannot_create_a_broken_subscription(self):
        with database.SessionLocal() as db:
            db.get(database.Inbound,1).protocol = 'tuic'; db.commit()
        response = self.client.post('/api/subscriptions', headers=HEADERS, json={
            'label':'bad-profile', 'server':'vpn.example.test', 'inbound_ids':[1]})
        self.assertEqual(response.status_code,409)
        self.assertNotIn('vui_s_',response.text)
        self.assertEqual(self.client.get('/api/subscriptions').json(),[])

if __name__=='__main__': unittest.main()
