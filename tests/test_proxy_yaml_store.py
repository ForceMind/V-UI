"""Real private-file YAML draft storage; no service or runtime processes."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.mihomo_merge import serialize_mihomo_merge
from app.services.mihomo_merge_plan import plan_mihomo
from app.services.mihomo_plan_record import encode_plan_record
from app.services.proxy_store import ProxyConflict, ProxyStorageError, ProxyStore, ProxyValidationError, _canonical
from test_mihomo_merge_plan import BASELINE


class ProxyYamlStoreTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-yaml-store-'))).resolve()
        self.data = root
        self.store = ProxyStore(root)
        self.before = self.store.read_snapshot()
        candidate = b'rules: ["MATCH,Chosen"]'
        merged = serialize_mihomo_merge(BASELINE, candidate)
        self.sources = {'baseline': BASELINE.encode(), 'candidate': candidate, 'merged': merged}
        plan = plan_mihomo(BASELINE, candidate, group_members={'Chosen': 'upstream'})
        self.record = encode_plan_record(plan, sources={k: hashlib.sha256(v).hexdigest() for k, v in self.sources.items()},
                                         group_members={'Chosen': 'upstream'}, dns_upstream='upstream')

    def test_save_reload_private_material_and_monotonic_fence(self):
        saved = self.store.save_yaml_plan(self.record, self.sources, self.before['revision'])
        self.assertEqual(saved['execution_mode'], 'yaml')
        self.assertEqual(self.store.policy_schema_version(), 3)
        for secret in ('relay.example.com', '11111111-', 'MATCH'):
            self.assertNotIn(secret, json.dumps(saved))
        reloaded = ProxyStore(self.data).load_draft()
        record, sources = reloaded['yaml_plan']
        self.assertEqual(sources, self.sources)
        self.assertEqual(record.value['dns_upstream'], 'upstream')
        for path in (self.store.path/'revisions').iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        cleared = self.store.clear_yaml_plan(saved['revision'])
        self.assertEqual(cleared['execution_mode'], 'single')
        self.assertEqual(self.store.policy_schema_version(), 3)
        self.assertIsNone(self.store.load_draft()['yaml_plan'])
        with self.assertRaises(ProxyConflict):
            self.store.save_yaml_plan(self.record, self.sources, self.before['revision'])

    def test_existing_classic_commit_stays_active_and_discard_preserves_fence(self):
        self.store.begin_apply(self.before['revision'], 'classic')
        self.store.commit_apply('classic', 'a' * 64)
        active = self.store.load_active()
        self.store.save_yaml_plan(self.record, self.sources, self.before['revision'])
        self.assertEqual(self.store.load_active(), active)
        snapshot = self.store.read_snapshot()
        self.assertEqual(snapshot['active']['revision'], self.before['revision'])
        self.store.discard_draft(snapshot['revision'], self.before['revision'])
        self.assertEqual(self.store.read_snapshot()['revision'], self.before['revision'])
        self.assertEqual(self.store.policy_schema_version(), 3)
        state_path = self.store.path/'state.json'
        state = json.loads(state_path.read_bytes())
        state['schema_version'] = 2
        state_path.write_bytes(_canonical(state))
        with self.assertRaises(ProxyStorageError):
            self.store.read_snapshot()

    def test_yaml_switches_do_not_require_a_separate_classic_upstream(self):
        saved = self.store.save_yaml_plan(self.record, self.sources, self.before['revision'])
        settings = {key: value for key, value in saved['settings'].items() if key != 'local_password_set'}
        settings.update(local_proxy_enabled=True, local_username='yaml-local')
        enabled = self.store.save_draft(settings, saved['revision'], local_password='private-test-password')
        self.assertIsNone(enabled['settings']['selected_upstream_id'])
        revisions_before = sorted((self.store.path/'revisions').iterdir())
        with self.assertRaises(ProxyValidationError):
            self.store.clear_yaml_plan(enabled['revision'])
        self.assertEqual(sorted((self.store.path/'revisions').iterdir()), revisions_before)
        self.assertEqual(self.store.read_snapshot()['revision'], enabled['revision'])
        settings['local_proxy_enabled'] = False
        disabled = self.store.save_draft(settings, enabled['revision'])
        self.assertEqual(self.store.clear_yaml_plan(disabled['revision'])['execution_mode'], 'single')

    def test_backup_roundtrip_includes_private_sources_and_rejects_tampering(self):
        self.store.save_yaml_plan(self.record, self.sources, self.before['revision'])
        with self.store.backup_snapshot() as entries:
            backed = dict(entries)
        recovered = self.data/'recovered'
        recovered.mkdir(mode=0o700)
        for name, raw in backed.items():
            path = recovered/name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_bytes(raw)
            path.chmod(0o600)
        restored = ProxyStore(recovered)
        self.assertEqual(restored.load_draft()['yaml_plan'][1], self.sources)
        state = json.loads((restored.path/'state.json').read_bytes())
        state['schema_version'] = 2
        (restored.path/'state.json').write_bytes(_canonical(state))
        with self.assertRaises(ProxyStorageError):
            restored.read_snapshot()

    def test_partial_save_keeps_old_draft_and_preserves_new_capability(self):
        for failed_write in range(1, 7):
            with self.subTest(failed_write=failed_write):
                root = self.data/('failure-' + str(failed_write))
                root.mkdir(mode=0o700)
                store = ProxyStore(root)
                before = store.read_snapshot()
                original = store._immutable
                calls = []

                def fail_selected(directory, name, value):
                    calls.append(name)
                    if len(calls) == failed_write:
                        raise OSError('synthetic immutable write failure')
                    return original(directory, name, value)

                with patch.object(store, '_immutable', side_effect=fail_selected):
                    with self.assertRaises(ProxyStorageError):
                        store.save_yaml_plan(self.record, self.sources, before['revision'])
                self.assertEqual(store.read_snapshot()['revision'], before['revision'])
                self.assertEqual(store.policy_schema_version(), 3)
                with store.backup_snapshot() as entries:
                    self.assertTrue(dict(entries))
                store.save_yaml_plan(self.record, self.sources, before['revision'])

    def test_wrong_source_and_legacy_apply_cannot_activate_new_draft(self):
        wrong = {**self.sources, 'merged': b'other'}
        with self.assertRaises(ProxyValidationError):
            self.store.save_yaml_plan(self.record, wrong, self.before['revision'])
        self.assertEqual(self.store.read_snapshot(), self.before)
        saved = self.store.save_yaml_plan(self.record, self.sources, self.before['revision'])
        with self.assertRaises(ProxyValidationError):
            self.store.begin_apply(saved['revision'], 'legacy-apply')
        self.assertIsNone(self.store.read_snapshot()['pending'])
