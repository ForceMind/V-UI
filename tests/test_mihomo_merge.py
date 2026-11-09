import unittest

from app.services.mihomo_merge import MihomoMergeError, merge_mihomo
from app.services.mihomo_preview import MihomoPreviewError


class MihomoMergeTests(unittest.TestCase):
    def test_preserves_opaque_fields_and_node_order_without_reordering_rules(self):
        baseline = b"""
proxies: [{name: first, token: private}]
dns: {enable: false, reserved: [one, two]}
rules: ["DOMAIN-SUFFIX,example.com,first", "MATCH,REJECT"]
"""
        candidate = b"""
proxies: [{name: second, extra: true}]
dns: {nested: {opaque: value}}
"""
        result = merge_mihomo(baseline, candidate)
        self.assertEqual([node['name'] for node in result['proxies']], ['first', 'second'])
        self.assertEqual(result['dns'], {'enable': False, 'reserved': ['one', 'two'],
                                         'nested': {'opaque': 'value'}})
        self.assertEqual(result['rules'], ['DOMAIN-SUFFIX,example.com,first', 'MATCH,REJECT'])

    def test_conflicts_require_exact_private_choices(self):
        baseline = '{proxies: [{name: shared, token: private}], rules: ["MATCH,DIRECT"], opaque: {value: false}}'
        candidate = '{proxies: [{name: shared, token: alternate}], rules: ["MATCH,REJECT"], opaque: {value: 0}}'
        with self.assertRaisesRegex(MihomoMergeError, '^MERGE_CHOICES_REQUIRED$') as failure:
            merge_mihomo(baseline, candidate)
        self.assertEqual(failure.exception.conflicts, (('proxies', 'shared'), ('rules',), ('opaque', 'value')))
        choices = {path: 'candidate' for path in failure.exception.conflicts}
        merged = merge_mihomo(baseline, candidate, choices=choices)
        self.assertEqual(merged['proxies'][0]['token'], 'alternate')
        self.assertEqual(merged['rules'], ['MATCH,REJECT'])
        self.assertIs(type(merged['opaque']['value']), int)
        with self.assertRaisesRegex(MihomoMergeError, '^MERGE_UNUSED_CHOICES$'):
            merge_mihomo(baseline, candidate, choices={**choices, ('extra',): 'candidate'})

    def test_checks_names_and_references_after_merging(self):
        baseline = '{proxy-groups: [{name: first, type: select, proxies: [second]}]}'
        candidate = '{proxies: [{name: second}], rules: ["MATCH,first"]}'
        self.assertEqual(merge_mihomo(baseline, candidate)['rules'], ['MATCH,first'])
        with self.assertRaisesRegex(MihomoMergeError, '^MERGE_INVALID_REFERENCES$'):
            merge_mihomo(baseline, '{proxy-groups: [{name: second, type: select, proxies: [first]}]}')
        with self.assertRaisesRegex(MihomoMergeError, '^MERGE_NAME_COLLISION$'):
            merge_mihomo(baseline, '{proxies: [{name: first}]}')
        for name in ('DIRECT', 'GLOBAL', 'MATCH'):
            with self.assertRaisesRegex(MihomoMergeError, '^MERGE_NAME_COLLISION$'):
                merge_mihomo('{}', '{proxies: [{name: '+name+'}]}')
        exported = '{proxy-groups: [{name: AUTO, type: select, proxies: [DIRECT]}], rules: ["MATCH,AUTO"]}'
        self.assertEqual(merge_mihomo('{}', exported)['rules'], ['MATCH,AUTO'])
        for invalid in ('{proxy-groups: [{name: first, type: select, proxies: [absent], hidden: true}]}',
                        '{rules: ["IP-CIDR,192.0.2.0/24,absent,no-resolve"]}',
                        '{rules: ["MATCH,AUTO"]}'):
            with self.assertRaisesRegex(MihomoMergeError, '^MERGE_INVALID_REFERENCES$'):
                merge_mihomo('{}', invalid)

    def test_keeps_safe_parser_rejections_and_combined_section_limit(self):
        with self.assertRaisesRegex(MihomoPreviewError, '^DUPLICATE_KEY$'):
            merge_mihomo('{}', '{dns: {}, dns: {}}')
        left = '{proxies: [' + ','.join(f'{{name: left-{i}}}' for i in range(80)) + ']}'
        right = '{proxies: [' + ','.join(f'{{name: right-{i}}}' for i in range(80)) + ']}'
        with self.assertRaisesRegex(MihomoMergeError, '^MERGE_RESULT_LIMIT$'):
            merge_mihomo(left, right)
