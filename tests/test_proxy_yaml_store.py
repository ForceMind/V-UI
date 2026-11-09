"""Real private-file YAML draft storage; no service or runtime processes."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from app.services.mihomo_merge import serialize_mihomo_merge
from app.services.mihomo_merge_plan import plan_mihomo
from app.services.mihomo_plan_record import PlanRecordError, decode_check_record, encode_plan_record, yaml_endpoint_id
from app.services.inbound_policy import policy_plan
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


class ProxyYamlConfirmationTests(unittest.TestCase):
    """Private metadata only: no runtime, DNS, service or application authority."""

    setUp = ProxyYamlStoreTests.setUp

    def ticket(self, *, protected=False):
        saved = self.store.save_yaml_plan(self.record, self.sources, self.before['revision'])
        if protected:
            settings = {k: v for k, v in saved['settings'].items() if k != 'local_password_set'}
            settings.update(local_proxy_enabled=True, local_username='yaml-local')
            saved = self.store.save_draft(settings, saved['revision'], local_password='fake-check-password')
        draft = self.store.load_draft()
        source = policy_plan([], draft['settings'])
        plan = draft['yaml_plan'][0]
        now = int(time.time())
        return dict(schema_version=1, kind='yaml-check', revision=saved['revision'],
                    plan_revision=plan.record_id, compiler=1, source_fingerprint=source.source_fingerprint,
                    candidate_revision='a'*64, policy_identity='b'*64, plan_manifest=source.manifest,
                    source_flags={k: draft['settings'][k] for k in ('local_proxy_enabled', 'relay_enabled')},
                    protected_enabled=source.requires_upstream,
                    endpoints=({'upstream': dict(profile_id=yaml_endpoint_id(plan.record_id, 'upstream'),
                               revision=saved['revision'], host='relay.example.com', addresses=['1.1.1.1'],
                               resolved_at=now)} if protected else {}),
                    self_addresses=['8.8.8.8'] if protected else [], dns_upstream='upstream',
                    runtime_fingerprint='c'*64, issuer='d'*32, data_generation=self.store._yaml_data_generation(),
                    created_at=now, expires_at=now+300)

    def read_ticket(self, receipt, value):
        return self.store.load_yaml_check(receipt['check_id'], value['revision'],
                    runtime_fingerprint=value['runtime_fingerprint'], issuer=value['issuer'],
                    source_fingerprint=value['source_fingerprint'])

    def test_private_roundtrip_latest_only_and_detached_data(self):
        value = self.ticket(protected=True)
        receipt = self.store.save_yaml_check(value, value['revision'])
        self.assertEqual(set(receipt), {'check_id', 'expires_at'})
        self.assertEqual(self.store.read_snapshot()['revision'], value['revision'])
        self.assertEqual(self.read_ticket(receipt, value), value)
        self.assertEqual((self.store.path/'secrets/yaml-check.json').stat().st_mode & 0o777, 0o600)
        self.assertNotIn('relay.example.com', repr(decode_check_record(_canonical(value))))
        decoded = decode_check_record(_canonical(value))
        decoded.value['endpoints'].clear()
        self.assertTrue(decoded.value['endpoints'])
        value['candidate_revision'] = 'e'*64
        new = self.store.save_yaml_check(value, value['revision'])
        with self.assertRaises(ProxyConflict):
            self.read_ticket(receipt, value)
        self.assertEqual(self.read_ticket(new, value), value)

    def test_expiry_runtime_issuer_source_and_data_generation_invalidate(self):
        value = self.ticket()
        receipt = self.store.save_yaml_check(value, value['revision'])
        for key in ('runtime_fingerprint', 'issuer', 'source_fingerprint'):
            changed = dict(value)
            changed[key] = 'f'*len(value[key])
            with self.subTest(key=key), self.assertRaises(ProxyConflict):
                self.read_ticket(receipt, changed)
        with patch('app.services.proxy_store.time.time', return_value=value['expires_at']):
            with self.assertRaises(ProxyConflict):
                self.read_ticket(receipt, value)
        with patch.object(self.store, '_yaml_data_generation', return_value={'dev': 0, 'ino': 1}):
            with self.assertRaises(ProxyConflict):
                self.read_ticket(receipt, value)
        self.store.clear_yaml_plan(value['revision'])
        with self.assertRaises(ProxyConflict):
            self.read_ticket(receipt, value)

    def test_private_backup_keeps_metadata_but_restore_requires_new_check(self):
        value = self.ticket(protected=True)
        receipt = self.store.save_yaml_check(value, value['revision'])
        with self.store.backup_snapshot() as entries:
            backed = dict(entries)
        self.assertIn('proxy-state/secrets/yaml-check.json', backed)
        recovered = self.data/'check-recovered'
        recovered.mkdir(mode=0o700)
        for name, raw in backed.items():
            path = recovered/name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_bytes(raw)
            path.chmod(0o600)
        restored = ProxyStore(recovered)
        with self.assertRaises(ProxyConflict):
            restored.load_yaml_check(receipt['check_id'], value['revision'],
                   runtime_fingerprint=value['runtime_fingerprint'], issuer=value['issuer'],
                   source_fingerprint=value['source_fingerprint'])
        with restored.backup_snapshot() as entries:
            self.assertEqual(dict(entries)['proxy-state/secrets/yaml-check.json'], _canonical(value))

    def test_closed_schema_bounds_and_material_binding(self):
        from copy import deepcopy
        value = self.ticket(protected=True)
        for key, changed in (('schema_version', True), ('compiler', True), ('protected_enabled', False),
                             ('expires_at', value['created_at']+301), ('issuer', 'not-an-issuer'),
                             ('self_addresses', ['8.8.8.8', '8.8.8.8']), ('endpoints', []),
                             ('data_generation', {'dev': 0, 'ino': 0})):
            invalid = deepcopy(value)
            invalid[key] = changed
            with self.subTest(key=key), self.assertRaises(PlanRecordError):
                decode_check_record(_canonical(invalid))
        for key, changed in (('host', 'other.example.com'), ('profile_id', 'up_'+'f'*32)):
            invalid = deepcopy(value)
            invalid['endpoints']['upstream'][key] = changed
            with self.subTest(endpoint=key), self.assertRaises(ProxyStorageError):
                self.store.save_yaml_check(invalid, value['revision'])
        with self.assertRaises(PlanRecordError):
            decode_check_record(b'{"schema_version":1,"schema_version":1}')
        with self.assertRaises(PlanRecordError):
            decode_check_record(b'{"created_at":'+b'9'*100000+b'}')
        with self.assertRaises(PlanRecordError):
            decode_check_record(_canonical(value)+b'\n')

    def test_invalid_or_expired_write_preserves_previous_private_record(self):
        value = self.ticket()
        receipt = self.store.save_yaml_check(value, value['revision'])
        with patch('app.services.proxy_store.time.time', return_value=value['expires_at']):
            with self.assertRaises(ProxyStorageError):
                self.store.save_yaml_check(value, value['revision'])
        self.assertEqual(self.read_ticket(receipt, value), value)
        path = self.store.path/'secrets/yaml-check.json'
        malformed = dict(value, plan_revision='f'*64)
        path.write_bytes(_canonical(malformed))
        with self.assertRaises(ProxyStorageError):
            with self.store.backup_snapshot() as entries:
                dict(entries)

    def test_confirmation_file_symlink_never_reads_or_overwrites_target(self):
        value = self.ticket()
        target = self.data/'unrelated-private-file'
        target.write_bytes(b'unchanged')
        target.chmod(0o600)
        path = self.store.path/'secrets/yaml-check.json'
        path.symlink_to(target)
        with self.assertRaises((ProxyStorageError, OSError)):
            self.store.save_yaml_check(value, value['revision'])
        self.assertEqual(target.read_bytes(), b'unchanged')
        self.assertTrue(path.is_symlink())
