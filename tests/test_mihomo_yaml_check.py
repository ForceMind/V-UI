"""YAML check uses fake resolution/runtime and real private Store files."""
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from app import release_tools
from app.services.mihomo_merge import serialize_mihomo_merge
from app.services.mihomo_merge_plan import plan_mihomo
from app.services.mihomo_plan_record import encode_plan_record
from app.services.proxy_apply import ProxyApplyCoordinator, ProxyApplyError
from app.services.core_runtime import CoreError, encode
from app.services.proxy_policy import VerifiedEndpoint
from app.services.proxy_store import ProxyConflict, ProxyStore
from tests.test_proxy_apply import FakeRuntime, SimulatedCrash, build_config, row
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
        with self.assertRaisesRegex(ProxyConflict, 'confirm the checked revision'):
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
        with self.assertRaisesRegex(ProxyConflict, 'confirm the checked revision'):
            self.coordinator.apply(saved['revision'], 'still-not-enabled')

    def confirmed(self, saved):
        self.runtime.apply({'inbounds': [{'tag': 'existing'}]})
        self.coordinator.confirmation_provider = Mock(
            return_value={'issuer': 'd'*32, 'runtime_fingerprint': 'c'*64})
        result = self.coordinator.check(saved['revision'])
        return {'expected_source_fingerprint': result['source_fingerprint'],
                'yaml_check_id': result['yaml_confirmation']['check_id']}

    def test_apply_binds_the_ticket_and_replays_the_same_request_after_expiry(self):
        saved = self.save()
        args = self.confirmed(saved)
        applied = self.coordinator.apply(saved['revision'], 'apply-yaml', **args)
        self.assertTrue(applied['applied'])
        self.assertEqual(applied['snapshot']['active']['execution_mode'], 'yaml')
        self.assertNotIn('relay.example.com', repr(applied))
        self.assertNotIn('1.1.1.1', repr(applied))
        before = list(self.runtime.calls)
        with patch('app.services.proxy_store.time.time', return_value=10**11):
            again = self.coordinator.apply(saved['revision'], 'apply-yaml', **args)
        self.assertTrue(again['already_completed'])
        self.assertEqual(self.runtime.calls, before)
        with self.assertRaises(ProxyConflict):
            self.coordinator.apply(saved['revision'], 'apply-yaml', **{**args, 'yaml_check_id': 'f'*64})

    def test_maintenance_check_cannot_resolve_or_replace_the_admin_ticket(self):
        saved = self.save()
        args = self.confirmed(saved)
        self.resolve.reset_mock()
        with self.assertRaises(ProxyApplyError) as rejected:
            self.coordinator.check(saved['revision'], allow_yaml=False)
        self.assertEqual(rejected.exception.code, 'YAML_ADMIN_REQUIRED')
        self.resolve.assert_not_called()
        self.assertTrue(self.coordinator.apply(saved['revision'], 'still-valid', **args)['applied'])

    def test_expiry_replaced_ticket_dns_change_and_stopped_core_refuse_before_write(self):
        saved = self.save()
        args = self.confirmed(saved)
        with patch('app.services.proxy_store.time.time', return_value=10**11), self.assertRaises(ProxyConflict):
            self.coordinator.apply(saved['revision'], 'expired', **args)
        self.resolve.side_effect = lambda identifier, revision, profile, **kwargs: VerifiedEndpoint(
            identifier, revision, profile.server, ('9.9.9.9',), 101)
        with self.assertRaisesRegex(ProxyConflict, 'addresses changed'):
            self.coordinator.apply(saved['revision'], 'address-changed', **args)
        self.coordinator.check(saved['revision'])
        with self.assertRaisesRegex(ProxyConflict, 'confirmation changed'):
            self.coordinator.apply(saved['revision'], 'replaced', **args)
        self.runtime.stop()
        with self.assertRaisesRegex(ProxyApplyError, 'Start the managed'):
            self.coordinator.apply(saved['revision'], 'stopped', **args)
        self.assertIsNone(self.store.recoverable_pending())
        for key in ('expired', 'address-changed', 'replaced', 'stopped'):
            self.assertIsNone(self.store.transaction(key))

    def test_local_address_change_during_final_core_check_cannot_prepare(self):
        saved = self.save()
        args = self.confirmed(saved)
        previous = self.runtime.state()
        self.runtime.on_check = lambda: setattr(self.addresses, 'return_value', ('8.8.4.4',))
        with self.assertRaisesRegex(ProxyConflict, 'Local addresses changed'):
            self.coordinator.apply(saved['revision'], 'local-change', **args)
        self.assertIsNone(self.store.recoverable_pending())
        self.assertEqual(self.runtime.state(), previous)
        self.assertTrue(self.runtime.running)

    def test_interruption_recovers_committed_yaml_with_saved_pins(self):
        saved = self.save()
        self.coordinator.apply(saved['revision'], 'old-yaml', **self.confirmed(saved))
        previous = self.runtime.state()
        changed = self.save(candidate='rules: ["MATCH,REJECT"]', groups={}, dns=None)
        result = self.coordinator.check(changed['revision'])
        with patch.object(self.store, 'commit_apply', side_effect=SimulatedCrash), self.assertRaises(SimulatedCrash):
            self.coordinator.apply(changed['revision'], 'interrupted-yaml',
                expected_source_fingerprint=result['source_fingerprint'],
                yaml_check_id=result['yaml_confirmation']['check_id'])
        self.assertIsNotNone(self.store.recoverable_pending())
        self.resolve.reset_mock()
        self.assertEqual(self.coordinator.recover()['phase'], 'aborted')
        self.assertEqual(self.runtime.state(), previous)
        self.assertEqual(self.store.load_active()['revision'], saved['revision'])
        self.resolve.assert_not_called()

    def test_interruption_keeps_evidence_when_runtime_has_an_unrelated_revision(self):
        saved = self.save()
        args = self.confirmed(saved)
        with patch.object(self.store, 'commit_apply', side_effect=SimulatedCrash), self.assertRaises(SimulatedCrash):
            self.coordinator.apply(saved['revision'], 'uncertain', **args)
        self.runtime.record['revision'] = 'a'*64
        calls = len(self.runtime.calls)
        with self.assertRaisesRegex(ProxyApplyError, 'recovery could not be confirmed'):
            self.coordinator.recover()
        self.assertEqual(self.store.recoverable_pending()['transaction_id'], 'uncertain')
        self.assertNotIn('restore', [call[0] for call in self.runtime.calls[calls:]])

    def test_reconciliation_rotates_an_inbound_certificate_with_committed_yaml_pins(self):
        inbound = row()
        self.coordinator.rows_provider = lambda: [inbound]
        self.coordinator.build_config = build_config
        saved = self.save()
        args = self.confirmed(saved)
        self.coordinator.apply(saved['revision'], 'before-rotation', **args)
        before = self.store.load_active()
        inbound.stream_settings['tls']['certificate_path'] = '/test-only-rotated-certificate'
        self.resolve.reset_mock()
        self.coordinator.validate_inbound_change([inbound])
        self.coordinator.reconcile([inbound], 'certificate-rotation')
        after = self.store.load_active()
        self.assertEqual(after['revision'], before['revision'])
        self.assertNotEqual(after['core_revision'], before['core_revision'])
        self.assertEqual(after['runtime_context']['yaml_check']['endpoints'],
                         before['runtime_context']['yaml_check']['endpoints'])
        self.assertEqual(self.coordinator.recover()['phase'], 'recovered')
        self.resolve.assert_not_called()

    def test_independent_xray_change_does_not_invalidate_committed_yaml_recovery(self):
        inbound, xray = row(), row(2, 24444)
        xray.core = 'xray'
        self.coordinator.rows_provider = lambda: [inbound, xray]
        self.coordinator.build_config = build_config
        saved = self.save()
        stale = self.confirmed(saved)
        xray.remark = 'Independent core metadata'
        with self.assertRaises(ProxyConflict):
            self.coordinator.apply(saved['revision'], 'stale-source', **stale)
        result = self.coordinator.check(saved['revision'])
        self.coordinator.apply(saved['revision'], 'local-yaml',
            expected_source_fingerprint=result['source_fingerprint'],
            yaml_check_id=result['yaml_confirmation']['check_id'])
        before = self.runtime.state()
        xray.remark = 'New independent core metadata'
        self.resolve.reset_mock()
        self.assertEqual(self.coordinator.recover()['phase'], 'recovered')
        self.assertEqual(self.runtime.state(), before)
        self.resolve.assert_not_called()
        runtime = self.store.data_dir / 'runtime' / 'sing-box'
        runtime.mkdir(mode=0o700, parents=True)
        for name, value in (('state.json', before), (before['revision'] + '.json',
                                                    self.runtime.saved[before['revision']])):
            path = runtime / name
            path.write_bytes(encode(value))
            path.chmod(0o600)
        with patch.object(ProxyStore, 'policy_rows', return_value=[inbound, xray]), \
                patch.object(release_tools, '_database_mode_fence', return_value=False):
            release_tools._validate_restore_policy(self.store.data_dir)
            xray.port = saved['settings']['local_port']
            with self.assertRaisesRegex(release_tools.ReleaseError, 'differ from'):
                release_tools._validate_restore_policy(self.store.data_dir)

    def test_interrupted_certificate_reconciliation_restores_previous_runtime(self):
        inbound = row()
        self.coordinator.rows_provider = lambda: [inbound]
        self.coordinator.build_config = build_config
        saved = self.save()
        self.coordinator.apply(saved['revision'], 'baseline', **self.confirmed(saved))
        before = self.runtime.state()
        inbound.stream_settings['tls']['certificate_path'] = '/test-only-new-certificate'
        with patch.object(self.store, 'commit_apply', side_effect=SimulatedCrash), self.assertRaises(SimulatedCrash):
            self.coordinator.reconcile(transaction_id='interrupted-rotation')
        self.resolve.reset_mock()
        self.assertEqual(self.coordinator.recover()['phase'], 'aborted')
        self.assertEqual(self.runtime.state(), before)
        self.assertEqual(self.store.transaction('interrupted-rotation')['phase'], 'aborted')
        self.resolve.assert_not_called()

    def test_same_second_recheck_invalidates_the_first_confirmation(self):
        saved = self.save()
        with patch('app.services.proxy_store.time.time', return_value=1000):
            first = self.confirmed(saved)
            second = self.coordinator.check(saved['revision'])['yaml_confirmation']['check_id']
            self.assertNotEqual(first['yaml_check_id'], second)
            with self.assertRaises(ProxyConflict):
                self.coordinator.apply(saved['revision'], 'same-second', **first)

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
