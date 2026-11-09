import unittest

from app.services.mihomo_merge import MihomoMergeError
from app.services.mihomo_merge_plan import plan_mihomo


BASELINE = """
mode: rule
proxies:
  - name: upstream
    type: vless
    server: relay.example.com
    port: 443
    uuid: "11111111-1111-4111-8111-111111111111"
    tls: true
    servername: relay.example.com
proxy-groups:
  - {name: Chosen, type: select, proxies: [upstream, REJECT]}
"""


class StaticMihomoPlanTests(unittest.TestCase):
    def test_first_match_explicit_targets_and_literal_cidrs(self):
        rules = """
rules:
  - "DOMAIN,exception.example.com,DIRECT"
  - "DOMAIN-SUFFIX,example.com,Chosen"
  - "IP-CIDR,192.0.2.0/24,Chosen,no-resolve"
  - "IP-CIDR6,2001:db8::/32,DIRECT,no-resolve"
  - "MATCH,REJECT"
"""
        plan = plan_mihomo(BASELINE, rules, group_members={'Chosen': 'upstream'})
        self.assertEqual(plan.upstreams[0].name, 'upstream')
        for host, target in (('exception.example.com', 'DIRECT'), ('other.example.com', 'upstream'),
                             ('example.com', 'upstream'), ('badexample.com', 'REJECT'),
                             ('192.0.2.7', 'upstream'), ('192.0.3.7', 'REJECT'),
                             ('2001:db8::7', 'DIRECT'), ('2001:db9::7', 'REJECT')):
            with self.subTest(host=host):
                self.assertEqual(plan.route(host), target)
        self.assertNotIn('relay.example.com', repr(plan))
        with self.assertRaisesRegex(MihomoMergeError, '^PLAN_INVALID_DESTINATION$'):
            plan.route('https://example.com')
        with self.assertRaisesRegex(MihomoMergeError, '^PLAN_INVALID_DESTINATION$'):
            plan.route('fe80::1%eth0')

    def test_requires_explicit_group_member_and_final_target(self):
        with self.assertRaisesRegex(MihomoMergeError, '^PLAN_GROUP_SELECTION_REQUIRED$'):
            plan_mihomo(BASELINE, '{rules: ["MATCH,Chosen"]}')
        with self.assertRaisesRegex(MihomoMergeError, '^PLAN_INVALID_GROUP_SELECTION$'):
            plan_mihomo(BASELINE, '{rules: ["MATCH,Chosen"]}', group_members={'Chosen': 'DIRECT'})
        with self.assertRaisesRegex(MihomoMergeError, '^PLAN_UNUSED_GROUP_SELECTION$'):
            plan_mihomo(BASELINE, '{rules: ["MATCH,REJECT"]}', group_members={'Chosen': 'upstream'})
        with self.assertRaisesRegex(MihomoMergeError, '^PLAN_FINAL_REQUIRED$'):
            plan_mihomo(BASELINE, '{rules: ["DOMAIN,example.com,REJECT"]}')

    def test_rejects_unimplemented_runtime_effects(self):
        for candidate, code in (
            ('{dns: {}, rules: ["MATCH,REJECT"]}', 'PLAN_UNSUPPORTED_SECTION'),
            ('{rules: ["IP-CIDR,192.0.2.0/24,REJECT", "MATCH,REJECT"]}', 'PLAN_UNSUPPORTED_RULE'),
            ('{proxy-groups: [{name: Outer, type: select, proxies: [Chosen]}], rules: ["MATCH,Outer"]}',
             'PLAN_UNSUPPORTED_GROUP'),
        ):
            with self.subTest(code=code), self.assertRaisesRegex(MihomoMergeError, '^'+code+'$'):
                plan_mihomo(BASELINE, candidate)
