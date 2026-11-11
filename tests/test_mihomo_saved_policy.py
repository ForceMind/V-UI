"""Compile real private Store snapshots without starting a core or resolving DNS."""
from copy import deepcopy
from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest

from app.services.inbound_policy import PolicyError
from app.services.mihomo_merge import serialize_mihomo_merge
from app.services.mihomo_merge_plan import plan_mihomo
from app.services.mihomo_plan_record import encode_plan_record
from app.services.mihomo_saved_policy import compile_saved_yaml_policy, yaml_endpoint_id
from app.services.proxy_policy import DNS_TAG, LOCAL_TAG, UPSTREAM_TAG, VerifiedEndpoint
from app.services.proxy_store import ProxyStore
from test_mihomo_merge_plan import BASELINE


class SavedYamlPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='vui-yaml-compile-')
        self.addCleanup(temporary.cleanup)
        self.store = ProxyStore(Path(temporary.name).resolve())
        before = self.store.read_snapshot()
        candidate = b'rules: ["DOMAIN,example.com,DIRECT", "MATCH,Chosen"]'
        merged = serialize_mihomo_merge(BASELINE, candidate)
        sources = {'baseline': BASELINE.encode(), 'candidate': candidate, 'merged': merged}
        plan = plan_mihomo(BASELINE, candidate, group_members={'Chosen': 'upstream'})
        self.record = encode_plan_record(plan, sources={k: hashlib.sha256(v).hexdigest() for k, v in sources.items()},
                                         group_members={'Chosen': 'upstream'}, dns_upstream='upstream')
        self.saved = self.store.save_yaml_plan(self.record, sources, before['revision'])
        self.base = {'inbounds': [], 'outbounds': [{'type': 'direct', 'tag': 'direct'}],
                     'route': {'final': 'direct'}}

    def enabled_snapshot(self):
        settings = {key: value for key, value in self.saved['settings'].items() if key != 'local_password_set'}
        settings.update(local_proxy_enabled=True, local_username='yaml-user')
        self.store.save_draft(settings, self.saved['revision'], local_password='private-compile-password')
        return self.store.load_draft()

    def endpoints(self, snapshot):
        record = snapshot['yaml_plan'][0]
        return {'upstream': VerifiedEndpoint(yaml_endpoint_id(record.record_id, 'upstream'),
                                            snapshot['revision'], 'relay.example.com', ('1.1.1.1',), 100)}

    def test_disabled_yaml_mode_does_not_change_existing_base_configuration(self):
        snapshot = self.store.load_draft()
        compiled = compile_saved_yaml_policy(self.base, [], snapshot, endpoints={}, self_addresses=())
        self.assertEqual(compiled.config, self.base)
        self.assertEqual(compiled.policy_version, 3)
        self.assertEqual(compiled.yaml_plan_revision, snapshot['yaml_plan'][0].record_id)

    def test_local_listener_and_all_outbounds_bind_the_saved_plan_and_addresses(self):
        snapshot = self.enabled_snapshot()
        original = deepcopy(self.base)
        compiled = compile_saved_yaml_policy(self.base, [], snapshot, endpoints=self.endpoints(snapshot),
                                              self_addresses=('8.8.8.8',))
        self.assertEqual(self.base, original)
        local = next(row for row in compiled.config['inbounds'] if row['tag'] == LOCAL_TAG)
        self.assertEqual((local['listen'], local['users'][0]['username']), ('127.0.0.1', 'yaml-user'))
        upstream = next(row for row in compiled.config['outbounds'] if row['tag'] == UPSTREAM_TAG)
        self.assertEqual((upstream['server'], upstream['tls']['server_name']), ('1.1.1.1', 'relay.example.com'))
        dns = next(row for row in compiled.config['dns']['servers'] if row['tag'] == DNS_TAG)
        self.assertEqual(dns['detour'], UPSTREAM_TAG)
        rules = compiled.config['route']['rules']
        self.assertEqual(rules[0], {'inbound': [LOCAL_TAG], 'network': 'udp', 'action': 'reject'})
        self.assertTrue(all(row['inbound'] == [LOCAL_TAG] for row in rules))
        self.assertIn('1.1.1.1/32', compiled.config['route']['rule_set'][0]['rules'][0]['ip_cidr'])
        self.assertNotIn('relay.example.com', repr(compiled))
        self.assertNotIn('private-compile-password', repr(compiled))

    def test_multiple_named_targets_and_dns_keep_distinct_endpoint_bindings(self):
        candidate = b"""proxies:
- {name: other, type: vless, server: other.example.com, port: 443, uuid: "22222222-2222-4222-8222-222222222222", tls: true, servername: other.example.com}
rules: ["DOMAIN,example.com,other", "MATCH,Chosen"]
"""
        merged = serialize_mihomo_merge(BASELINE, candidate)
        sources = {'baseline': BASELINE.encode(), 'candidate': candidate, 'merged': merged}
        plan = plan_mihomo(BASELINE, candidate, group_members={'Chosen': 'upstream'})
        raw = encode_plan_record(plan, sources={k: hashlib.sha256(v).hexdigest() for k, v in sources.items()},
                                 group_members={'Chosen': 'upstream'}, dns_upstream='other')
        self.saved = self.store.save_yaml_plan(raw, sources, self.saved['revision'])
        snapshot = self.enabled_snapshot()
        record = snapshot['yaml_plan'][0]
        endpoints = {
            'upstream': VerifiedEndpoint(yaml_endpoint_id(record.record_id, 'upstream'), snapshot['revision'],
                                         'relay.example.com', ('1.1.1.1',), 100),
            'other': VerifiedEndpoint(yaml_endpoint_id(record.record_id, 'other'), snapshot['revision'],
                                      'other.example.com', ('9.9.9.9',), 100),
        }
        compiled = compile_saved_yaml_policy(self.base, [], snapshot, endpoints=endpoints,
                                              self_addresses=('8.8.8.8',))
        rows = {row['tag']: row for row in compiled.config['outbounds']}
        self.assertEqual(rows[UPSTREAM_TAG]['server'], '9.9.9.9')
        self.assertEqual(rows['vui-yaml-upstream-0']['server'], '1.1.1.1')
        self.assertEqual(rows[UPSTREAM_TAG]['tls']['server_name'], 'other.example.com')
        self.assertEqual({name for name, _ in compiled.endpoints}, {'other', 'upstream'})
        with self.assertRaises(PolicyError):
            compile_saved_yaml_policy(self.base, [], snapshot, endpoints={'other': endpoints['other']},
                                       self_addresses=('8.8.8.8',))

    def test_private_source_bytes_are_rechecked_before_compile(self):
        snapshot = self.store.load_draft()
        snapshot['yaml_plan'][1]['merged'] = b'changed private source'
        with self.assertRaises(PolicyError):
            compile_saved_yaml_policy(self.base, [], snapshot, endpoints={}, self_addresses=())

    def test_incomplete_stale_or_namespace_conflicting_context_is_rejected(self):
        snapshot = self.enabled_snapshot()
        valid = self.endpoints(snapshot)
        for endpoints in ({}, {'upstream': replace(valid['upstream'], revision='a'*64)},
                          {'upstream': replace(valid['upstream'], host='other.example.com')}):
            with self.subTest(endpoints=endpoints), self.assertRaises(PolicyError):
                compile_saved_yaml_policy(self.base, [], snapshot, endpoints=endpoints, self_addresses=('8.8.8.8',))
        collision = deepcopy(self.base)
        collision['route']['rules'] = [{'rule_set': ['vui-yaml-denied'], 'action': 'route', 'outbound': 'direct'}]
        with self.assertRaises(PolicyError):
            compile_saved_yaml_policy(collision, [], snapshot, endpoints=valid, self_addresses=('8.8.8.8',))
        changed = {'upstream': replace(valid['upstream'], resolved_at=101)}
        first = compile_saved_yaml_policy(self.base, [], snapshot, endpoints=valid, self_addresses=('8.8.8.8',))
        second = compile_saved_yaml_policy(self.base, [], snapshot, endpoints=changed, self_addresses=('8.8.8.8',))
        self.assertNotEqual(first.identity, second.identity)
