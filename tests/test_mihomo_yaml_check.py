"""YAML check uses fake resolution/runtime and real private Store files."""
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from app.services.mihomo_merge import serialize_mihomo_merge
from app.services.mihomo_merge_plan import plan_mihomo
from app.services.mihomo_plan_record import encode_plan_record
from app.services.proxy_apply import ProxyApplyCoordinator, ProxyApplyError
from app.services.core_runtime import CoreError
from app.services.proxy_policy import VerifiedEndpoint
from app.services.proxy_store import ProxyConflict, ProxyStore
from tests.test_proxy_apply import FakeRuntime
from test_mihomo_merge_plan import BASELINE


class MihomoYamlCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='vui-yaml-check-')
        self.addCleanup(temporary.cleanup)
        self.store = ProxyStore(Path(temporary.name).resolve())
        self.store.read_snapshot()
        self.runtime = FakeRuntime()
        self.preflight = Mock()
        self.addresses = Mock(return_value=('8.8.8.8',))
        self.resolve = Mock(side_effect=lambda identifier, revision, profile, **kwargs:
                            VerifiedEndpoint(identifier, revision, profile.server, ('1.1.1.1',), 100))
        self.coordinator = ProxyApplyCoordinator(
            self.store, self.runtime,
            lambda rows: {'inbounds': [], 'outbounds': [{'type': 'direct', 'tag': 'direct'}]},
            threading.RLock(), rows_provider=lambda: [], resolver=self.resolve,
            address_provider=self.addresses, preflight=self.preflight)

    def save(self, baseline=BASELINE, candidate='rules: ["MATCH,Chosen"]', groups=None, dns='upstream', enabled=True):
        groups = {'Chosen': 'upstream'} if groups is None else groups
        sources = {'baseline': baseline.encode(), 'candidate': candidate.encode(),
                   'merged': serialize_mihomo_merge(baseline, candidate)}
        plan = plan_mihomo(baseline, candidate, group_members=groups)
        record = encode_plan_record(plan, sources={k: hashlib.sha256(v).hexdigest() for k, v in sources.items()},
                                     group_members=groups, dns_upstream=dns)
        saved = self.store.save_yaml_plan(record, sources, self.store.read_snapshot()['revision'])
        if enabled:
            settings = {k: v for k, v in saved['settings'].items() if k != 'local_password_set'}
            settings.update(local_proxy_enabled=True, local_username='yaml-user')
            saved = self.store.save_draft(settings, saved['revision'], local_password='private-test-password')
        return saved

    def test_check_preflights_local_port_without_starting_or_committing(self):
        saved = self.save()
        before_state = self.runtime.state()
        result = self.coordinator.check(saved['revision'])
        self.assertTrue(result['valid'])
        self.assertEqual(result['phase'], 'checked')
        self.assertEqual(result['yaml_plan']['endpoint_count'], 1)
        self.assertEqual(self.runtime.state(), before_state)
        self.assertEqual([call[0] for call in self.runtime.calls], ['check'])
        self.assertIsNone(self.store.read_snapshot()['pending'])
        self.assertIsNone(self.store.read_snapshot()['active'])
        self.preflight.assert_called_once()
        self.assertNotIn('private-test-password', repr(result))
        self.assertNotIn('relay.example.com', repr(result))
        calls = list(self.runtime.calls)
        with self.assertRaisesRegex(ProxyApplyError, 'runtime application has not been enabled'):
            self.coordinator.apply(saved['revision'], 'not-enabled')
        self.assertEqual(self.runtime.calls, calls)

    def test_unused_upstreams_are_not_resolved_and_shared_hosts_keep_distinct_ids(self):
        candidate = """proxies:
- {name: other, type: vless, server: relay.example.com, port: 8443, uuid: "22222222-2222-4222-8222-222222222222", tls: true, servername: other.example.com}
rules: ["DOMAIN,example.com,other", "MATCH,Chosen"]
"""
        saved = self.save(candidate=candidate, dns='other')
        result = self.coordinator.check(saved['revision'])
        self.assertEqual(result['yaml_plan']['endpoint_count'], 2)
        self.assertEqual(self.resolve.call_count, 1)
        self.preflight.assert_called_once()

    def test_all_reject_still_preflights_local_listener_without_bootstrap(self):
        saved = self.save(candidate='rules: ["MATCH,REJECT"]', groups={}, dns=None)
        result = self.coordinator.check(saved['revision'])
        self.assertEqual(result['yaml_plan']['endpoint_count'], 0)
        self.resolve.assert_not_called()
        self.preflight.assert_called_once()

    def test_all_off_does_not_bootstrap_or_probe_an_absent_local_listener(self):
        saved = self.save(enabled=False)
        self.coordinator.check(saved['revision'])
        self.resolve.assert_not_called()
        self.addresses.assert_not_called()
        self.preflight.assert_not_called()

    def test_aggregate_bootstrap_timeout_rejects_before_runtime_check(self):
        saved = self.save()
        before = self.store.read_snapshot()
        with patch('app.services.proxy_apply.time.monotonic', side_effect=[0, 0, 31]):
            with self.assertRaises(ProxyApplyError) as rejected:
                self.coordinator.check(saved['revision'])
        self.assertEqual(rejected.exception.code, 'YAML_BOOTSTRAP_TIMEOUT')
        self.assertEqual(self.runtime.calls, [])
        self.assertEqual(self.store.read_snapshot(), before)

    def test_checked_yaml_mints_only_private_id_and_runtime_changes_refuse(self):
        saved = self.save()
        context = {'issuer': 'd'*32, 'runtime_fingerprint': 'c'*64}
        self.coordinator.confirmation_provider = Mock(return_value=context)
        result = self.coordinator.check(saved['revision'])
        receipt = result['yaml_confirmation']
        self.assertEqual(set(receipt), {'check_id', 'expires_at'})
        private = self.store.load_yaml_check(receipt['check_id'], saved['revision'],
                      source_fingerprint=result['source_fingerprint'], **context)
        self.assertEqual(private['candidate_revision'], result['candidate_revision'])
        self.assertEqual(private['endpoints']['upstream']['addresses'], ['1.1.1.1'])
        self.assertNotIn('1.1.1.1', repr(result))
        self.coordinator.confirmation_provider = Mock(side_effect=[context, {**context, 'issuer': 'e'*32}])
        with self.assertRaises(ProxyConflict):
            self.coordinator.check(saved['revision'])
        self.assertEqual(self.store.load_yaml_check(receipt['check_id'], saved['revision'],
                      source_fingerprint=result['source_fingerprint'], **context), private)

    def test_failed_check_never_issues_confirmation(self):
        saved = self.save()
        self.coordinator.confirmation_provider = Mock(return_value={'issuer': 'd'*32, 'runtime_fingerprint': 'c'*64})
        self.runtime.fail_check = True
        with self.assertRaises(CoreError):
            self.coordinator.check(saved['revision'])
        self.assertFalse((self.store.path/'secrets/yaml-check.json').exists())

    def test_provider_absence_has_no_confirmation_or_application_authority(self):
        saved = self.save()
        self.assertNotIn('yaml_confirmation', self.coordinator.check(saved['revision']))
        self.assertFalse((self.store.path/'secrets/yaml-check.json').exists())
        with self.assertRaisesRegex(ProxyApplyError, 'runtime application has not been enabled'):
            self.coordinator.apply(saved['revision'], 'still-not-enabled')

    def test_mutable_context_and_draft_changes_during_check_refuse_new_record(self):
        saved = self.save()
        context = {'issuer': 'd'*32, 'runtime_fingerprint': 'c'*64}
        self.coordinator.confirmation_provider = Mock(return_value=context)
        self.runtime.on_check = lambda: context.update(runtime_fingerprint='f'*64)
        with self.assertRaises(ProxyConflict):
            self.coordinator.check(saved['revision'])
        self.assertFalse((self.store.path/'secrets/yaml-check.json').exists())
        settings = {key: value for key, value in saved['settings'].items() if key != 'local_password_set'}
        settings['local_proxy_enabled'] = False
        self.runtime.on_check = lambda: self.store.save_draft(settings, saved['revision'])
        with self.assertRaises(ProxyConflict):
            self.coordinator.check(saved['revision'])
        self.assertFalse((self.store.path/'secrets/yaml-check.json').exists())

    def test_all_off_can_issue_a_record_without_resolution(self):
        saved = self.save(enabled=False)
        self.coordinator.confirmation_provider = Mock(return_value={'issuer': 'd'*32, 'runtime_fingerprint': 'c'*64})
        result = self.coordinator.check(saved['revision'])
        self.assertIn('yaml_confirmation', result)
        self.resolve.assert_not_called()
