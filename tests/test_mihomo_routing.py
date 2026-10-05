import unittest

import yaml

from app.services.mihomo_routing import (
    build_rule_plan,
    default_routing,
    normalize_routing,
    unique_proxy_names,
)
from app.services.mihomo_subscription import mihomo_config
from types import SimpleNamespace


def inbound(**kwargs):
    data = {
        "id": 1,
        "core": "xray",
        "remark": "demo",
        "port": 443,
        "protocol": "vless",
        "settings": {
            "clients": [{"id": "11111111-1111-1111-1111-111111111111"}],
            "decryption": "none",
        },
        "stream_settings": {},
        "enable": True,
        "tag": "demo",
    }
    data.update(kwargs)
    return SimpleNamespace(**data)


def flatten_rules(plan):
    return [
        rule
        for section in plan["sections"]
        for rule in section["rules"]
    ]


class MihomoRoutingTests(unittest.TestCase):
    def test_standard_mode_matches_toclash_group_and_rule_semantics(self):
        routing = default_routing()
        plan = build_rule_plan(routing)
        rules = flatten_rules(plan)

        self.assertEqual(rules[0], "DOMAIN-SUFFIX,localhost,DIRECT")
        self.assertIn("DOMAIN-SUFFIX,openai.com,FORCE_PROXY", rules)
        openai_index = rules.index("DOMAIN-SUFFIX,openai.com,FORCE_PROXY")
        self.assertEqual(
            rules[openai_index + 1],
            "DOMAIN-SUFFIX,openai.com,REJECT",
        )
        self.assertIn("DOMAIN-SUFFIX,github.com,PROXY", rules)
        self.assertIn("DOMAIN-SUFFIX,google.com,PROXY", rules)
        self.assertIn("GEOSITE,CN,DIRECT", rules)
        self.assertIn("GEOIP,CN,DIRECT", rules)
        self.assertEqual(rules[-1], "MATCH,PROXY")

        self.assertEqual(
            plan["dns"]["nameserver-policy"]["+.openai.com"],
            [
                "https://1.1.1.1/dns-query#FORCE_PROXY",
                "https://8.8.8.8/dns-query#FORCE_PROXY",
            ],
        )

        output = yaml.safe_load(
            mihomo_config([inbound()], "example.com", routing)
        )
        groups = {
            group["name"]: group
            for group in output["proxy-groups"]
        }
        self.assertEqual(set(groups), {"PROXY", "AUTO", "FORCE_PROXY"})
        self.assertIn("AUTO", groups["PROXY"]["proxies"])
        self.assertIn("DIRECT", groups["PROXY"]["proxies"])
        self.assertNotIn("DIRECT", groups["FORCE_PROXY"]["proxies"])

    def test_direct_mode_only_proxies_selected_services(self):
        routing = default_routing()
        routing["mode"] = "direct"
        plan = build_rule_plan(routing)
        rules = flatten_rules(plan)

        self.assertNotIn("GEOSITE,CN,DIRECT", rules)
        self.assertNotIn("GEOIP,CN,DIRECT", rules)
        self.assertEqual(rules[-1], "MATCH,DIRECT")
        self.assertIn("DOMAIN-SUFFIX,github.com,FORCE_PROXY", rules)
        self.assertIn("DOMAIN-SUFFIX,google.com,FORCE_PROXY", rules)
        self.assertIn("DOMAIN,cdn.openaimerge.com,FORCE_PROXY", rules)

        output = yaml.safe_load(
            mihomo_config([inbound()], "example.com", routing)
        )
        self.assertEqual(
            [group["name"] for group in output["proxy-groups"]],
            ["FORCE_PROXY"],
        )
        self.assertEqual(output["dns"]["nameserver"], ["system"])

    def test_custom_direct_parent_overrides_proxy_child(self):
        routing = default_routing()
        routing["direct_domains"] = ["example.com"]
        routing["proxy_domains"] = ["api.example.com"]
        plan = build_rule_plan(routing)
        rules = flatten_rules(plan)

        self.assertIn("DOMAIN-SUFFIX,example.com,DIRECT", rules)
        self.assertNotIn(
            "DOMAIN-SUFFIX,api.example.com,FORCE_PROXY",
            rules,
        )
        warnings = {
            warning["code"]: warning["count"]
            for warning in plan["warnings"]
        }
        self.assertEqual(warnings.get("DIRECT_OVERRIDE"), 1)

    def test_intranet_dns_is_used_for_business_and_proxy_bootstrap(self):
        routing = normalize_routing({
            **default_routing(),
            "intranet": [{
                "suffix": "corp.example",
                "nameservers": ["192.0.2.53"],
            }],
        })
        plan = build_rule_plan(routing)
        rules = flatten_rules(plan)
        expected_dns = ["udp://192.0.2.53:53"]

        self.assertIn("DOMAIN-SUFFIX,corp.example,DIRECT", rules)
        self.assertEqual(
            plan["dns"]["nameserver-policy"]["+.corp.example"],
            expected_dns,
        )
        self.assertEqual(
            plan["dns"]["proxy-server-nameserver-policy"]["+.corp.example"],
            expected_dns,
        )
        self.assertIn(
            "+.corp.example",
            plan["dns"]["fake-ip-filter"],
        )

    def test_reserved_and_duplicate_proxy_names_are_stable(self):
        proxies = unique_proxy_names([
            {"name": "PROXY", "type": "vless"},
            {"name": "demo", "type": "vless"},
            {"name": "demo", "type": "vless"},
        ])
        self.assertEqual(
            [proxy["name"] for proxy in proxies],
            ["PROXY 2", "demo", "demo 2"],
        )


if __name__ == "__main__":
    unittest.main()
