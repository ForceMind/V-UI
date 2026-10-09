"""Differential contract against the frozen pre-index planner, not timing tests."""
from copy import deepcopy
from pathlib import Path
import random
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services import mihomo_routing as routing
from app.services import routing_validation as public
from app.services.mihomo_subscription import mihomo_config


namespace = vars(routing).copy()
oracle_path = Path(__file__).parent / "fixtures" / "rule_plan_405b64a.py"
exec(compile(oracle_path.read_text(), str(oracle_path), "exec"), namespace)
reference_plan = namespace["build_rule_plan"]


class RuleMatchIndexTests(unittest.TestCase):
    def test_index_matches_pairwise_contract_at_each_insertion(self):
        values = ["example.com", "a.example.com", "notexample.com", "com",
                  "example.com.evil", "EXAMPLE.com", "example.com.", "",
                  "a..example.com", "192.0.2.1/32", "192.0.2.0/24", "::1/128"]
        matches = [{"type": kind, "value": value}
                   for kind in ("DOMAIN", "DOMAIN-SUFFIX", "IP-CIDR", "IP-CIDR6")
                   for value in values]
        random.Random(405).shuffle(matches)
        index = routing._MatchIndex()
        inserted = []
        for match in matches:
            index.add(match)
            inserted.append(match)
            for query in matches:
                self.assertEqual(index.covers(query), any(routing._covers(x, query) for x in inserted))
                self.assertEqual(index.overlaps(query), any(routing._overlaps(x, query) for x in inserted))

    def check_plan(self, payload, export=False):
        before = deepcopy(payload)
        expected = reference_plan(public.normalize_routing(payload))
        actual = public.build_rule_plan(payload)
        self.assertEqual(actual, expected)
        self.assertEqual(payload, before)
        if export:
            inbound = SimpleNamespace(id=1, core="sing-box", protocol="vless", remark="fake",
                port=443, enable=True, tag="fake", settings={"users": [{"uuid": "11111111-1111-1111-1111-111111111111"}]},
                stream_settings={"tls": {"enabled": True, "server_name": "example.com"}})
            actual_yaml = mihomo_config([inbound], "example.com", payload)
            with patch.object(public, "_plan", reference_plan):
                expected_yaml = mihomo_config([inbound], "example.com", payload)
            self.assertEqual(actual_yaml, expected_yaml)

    def test_domain_boundaries_normalization_duplicates_order_and_ips(self):
        targets = ["EXAMPLE.COM.", "https://a.example.com/path", "*.example.com",
                   "notexample.com", "example.com.evil", "bücher.example", "xn--bcher-kva.example",
                   "192.0.2.1", "2001:db8::1", "127.0.0.1", "100.64.0.1", "api.local",
                   "workos.imgix.net", "imgix.net", "a.workos.imgix.net", "openai.com"]
        for mode in ("standard", "direct"):
            for reverse in (False, True):
                for cgnat in (False, True):
                    ordered = list(reversed(targets)) if reverse else targets
                    self.check_plan({"mode": mode, "bypass_cgnat": cgnat,
                        "direct_domains": ordered[::2] + ordered[::3],
                        "proxy_domains": ordered + ordered,
                        "intranet": [{"suffix": "example.com", "nameservers": ["192.0.2.53"]},
                                     {"suffix": "a.example.com", "nameservers": ["[2001:db8::53]:5353"]},
                                     {"suffix": "local", "nameservers": ["system"]}]}, export=True)

    def test_seeded_plans_preserve_all_sections_dns_and_warning_counts(self):
        rng = random.Random(20261009)
        pool = ["example.com", "a.example.com", "b.a.example.com", "notexample.com",
                "api.local", "openai.com", "github.com", "imgix.net", "workos.imgix.net",
                "192.0.2.1", "2001:db8::1", "100.64.0.1", "127.0.0.1"]
        pool += [f"n{i}.zone{j}.example" for i in range(8) for j in range(4)]
        for case in range(80):
            with self.subTest(case=case):
                self.check_plan({"mode": rng.choice(["standard", "direct"]),
                    "bypass_cgnat": rng.choice([True, False]),
                    "direct_domains": rng.choices(pool, k=rng.randrange(60)),
                    "proxy_domains": rng.choices(pool, k=rng.randrange(60)),
                    "presets": {key: rng.choice([True, False]) for key in routing.default_presets()},
                    "intranet": [{"suffix": suffix, "nameservers": ["192.0.2.53"]}
                                 for suffix in rng.sample(["example.com", "zone0.example", "a.example.com", "local"], rng.randrange(5))]},
                    export=case < 4)

    def test_large_fixture_and_repeated_calls_do_not_leak_state(self):
        payload = {"direct_domains": [f"d{i}.example" for i in range(512)],
                   "proxy_domains": [f"p{i}.example" for i in range(512)],
                   "intranet": [{"suffix": f"intra{i}.example", "nameservers": ["192.0.2.53"]} for i in range(64)]}
        self.check_plan(payload, export=True)
        self.check_plan({"direct_domains": ["example.com"], "proxy_domains": ["a.example.com"]})
        self.check_plan({"proxy_domains": ["a.example.com"]})
        self.check_plan(None)

    def test_public_invalid_input_errors_are_identical(self):
        invalid = [[], "x", {"unknown": 1}, {"mode": "other"}, {"bypass_cgnat": 1},
                   {"direct_domains": "example.com"}, {"proxy_domains": [None]},
                   {"proxy_domains": ["a..example.com"]}, {"proxy_domains": ["example.com bad"]},
                   {"direct_domains": ["192.0.2.0/24"]}, {"proxy_domains": ["https://user:pass@example.com"]},
                   {"presets": {"unknown": True}}, {"presets": {"openai": 1}},
                   {"intranet": [{"suffix": "example.com", "nameservers": ["0.0.0.0"]}]},
                   {"intranet": [{"suffix": "example.com", "nameservers": ["192.0.2.53"]},
                                 {"suffix": "EXAMPLE.COM", "nameservers": ["192.0.2.54"]}]}]
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError) as current:
                    public.build_rule_plan(payload)
                with patch.object(public, "_plan", reference_plan):
                    with self.assertRaises(ValueError) as baseline:
                        public.build_rule_plan(payload)
                self.assertEqual(str(current.exception), str(baseline.exception))


if __name__ == "__main__":
    unittest.main()
