import json
import unittest
from unittest.mock import patch

from app.services.mihomo_merge import MihomoMergeError
from app.services.mihomo_merge_plan import plan_mihomo
from app.services.mihomo_plan_policy import DENIED_SET_TAG, protected_yaml_route


class ProtectedYamlRouteTests(unittest.TestCase):
    def plan(self):
        return plan_mihomo('mode: rule', '''rules:
- "IP-CIDR,192.0.2.0/24,REJECT,no-resolve"
- "DOMAIN,example.com,DIRECT"
- "MATCH,REJECT"
''')

    def route(self, **kwargs):
        defaults = dict(inbounds=('first', 'second'), outbound_tags={'DIRECT': 'direct-out'},
                        dns_tag='protected-dns', self_addresses=('8.8.8.8',), upstream_addresses={})
        defaults.update(kwargs)
        return protected_yaml_route(self.plan(), **defaults)

    def test_selection_precedes_resolve_and_guards_precede_every_routing_action(self):
        route = self.route()
        rules = route['rules']
        self.assertEqual(rules[0]['network'], 'udp')
        cidr = next(index for index, row in enumerate(rules) if row.get('ip_cidr') == ['192.0.2.0/24'])
        resolve = next(index for index, row in enumerate(rules) if row['action'] == 'resolve')
        direct = next(index for index, row in enumerate(rules) if row.get('outbound') == 'direct-out')
        self.assertLess(cidr, resolve)
        self.assertLess(resolve, direct)
        self.assertEqual(rules[resolve]['domain'], ['example.com'])
        self.assertTrue(any(row.get('rule_set') == [DENIED_SET_TAG] and row['action'] == 'reject'
                            for row in rules[resolve + 1:direct]))
        self.assertEqual(rules[-1], {'inbound': ['first', 'second'], 'action': 'reject'})
        self.assertTrue(all(row['inbound'] == ['first', 'second'] for row in rules))
        self.assertEqual(len(route['rule_set']), 1)
        self.assertIn('8.8.8.8/32', route['rule_set'][0]['rules'][0]['ip_cidr'])

    def test_empty_scope_cannot_match_unrelated_inbounds(self):
        self.assertEqual(self.route(inbounds=()), {'rules': [], 'rule_set': []})
        with self.assertRaisesRegex(MihomoMergeError, '^PLAN_MISSING_ADDRESS_CONTEXT$'):
            self.route(self_addresses=())

    def test_named_outbounds_and_dns_leaf_require_exact_address_snapshots(self):
        from test_mihomo_merge_plan import BASELINE
        plan = plan_mihomo(BASELINE, '{rules: ["MATCH,Chosen"]}', group_members={'Chosen': 'upstream'})
        args = dict(inbounds=('source',), outbound_tags={'DIRECT': 'direct', 'upstream': 'up'},
                    dns_tag='dns', self_addresses=('8.8.8.8',))
        for addresses in ({}, {'wrong': ('1.1.1.1',)}):
            with self.assertRaisesRegex(MihomoMergeError, '^PLAN_MISSING_ADDRESS_CONTEXT$'):
                protected_yaml_route(plan, upstream_addresses=addresses, **args)
        route = protected_yaml_route(plan, upstream_addresses={'upstream': ('1.1.1.1',)}, **args)
        self.assertIn('1.1.1.1/32', route['rule_set'][0]['rules'][0]['ip_cidr'])
        with patch('app.services.mihomo_plan_policy.MAX_RECIPE_BYTES', 100):
            with self.assertRaisesRegex(MihomoMergeError, '^PLAN_RECIPE_LIMIT$'):
                protected_yaml_route(plan, upstream_addresses={'upstream': ('1.1.1.1',)}, **args)

    def test_denied_addresses_are_shared_instead_of_repeated_per_rule(self):
        plan = plan_mihomo('mode: rule', 'rules: [' + ','.join(
            '"DOMAIN,host-' + str(index) + '.example.com,DIRECT"' for index in range(200)) + ',"MATCH,REJECT"]')
        route = protected_yaml_route(plan, inbounds=('source',), outbound_tags={'DIRECT': 'direct-out'},
                                     dns_tag='dns', self_addresses=('8.8.8.8',), upstream_addresses={})
        encoded = json.dumps(route)
        self.assertEqual(encoded.count('8.8.8.8/32'), 1)
        self.assertLess(len(encoded), 200000)
