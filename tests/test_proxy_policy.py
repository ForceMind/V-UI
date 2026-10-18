"""Pure compiler/address regression tests, not runtime/double-hop evidence."""
from copy import deepcopy
import ipaddress
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services.core_manager import SingBoxAdapter
from app.services.proxy_policy import (
    AddressPolicy, DENY_CIDRS, DNS_TAG, LOCAL_TAG, NAMESPACE, NON_GLOBAL_IPV6_CIDRS,
    PolicyError, UPSTREAM_TAG, VerifiedEndpoint, compile_proxy_policy, ingress_coverage,
)

PROFILE_ID = "up_" + "1" * 32
REVISION = "a" * 64
UUID = "11111111-1111-1111-1111-111111111111"
PASSWORD = "fixture-password-only"


def inbound(identifier=1, **changes):
    item = SimpleNamespace(id=identifier, core="sing-box", protocol="vless", port=11000 + identifier,
        routing_mode="inherit", remark="fixture", tag="user-provided-tag-" + str(identifier), enable=True, expiry_time=0,
        settings={"users": [{"uuid": UUID}]}, stream_settings={"tls": {
            "enabled": True, "server_name": "vpn.example.test",
            "certificate_path": "/test/cert.pem", "key_path": "/test/key.pem"}})
    for key, value in changes.items():
        setattr(item, key, value)
    return item


def snapshot(local=False, relay=False, *, server="upstream.example.test", reality=False):
    tls = {"mode": "tls", "server_name": "vpn.example.test", "alpn": ["h2", "http/1.1"],
           "fingerprint": "", "public_key": "", "short_id": ""}
    if reality:
        tls.update(mode="reality", alpn=[], fingerprint="chrome", public_key="A" * 43,
                   short_id="0123456789abcdef")
    profile = {"schema_version": 1, "protocol": "vless", "name": "fixture", "server": server,
               "server_port": 443, "uuid": UUID, "transport": "tcp", "encryption": "none",
               "flow": "xtls-rprx-vision" if reality else "", "tls": tls}
    return {"revision": REVISION, "settings": {"schema_version": 1,
            "local_proxy_enabled": local, "relay_enabled": relay, "selected_upstream_id": PROFILE_ID,
            "local_port": 17891, "local_username": "fixture-user", "local_password_ref": "sec_" + "2" * 32,
            "dns_policy_version": 1, "deny_policy_version": 1}, "upstreams": {PROFILE_ID: profile},
            "local_password": PASSWORD, "core_revision": None, "pending": None}


def endpoint(value=None, addresses=("8.8.8.8",)):
    value = value or snapshot()
    return VerifiedEndpoint(PROFILE_ID, value["revision"], value["upstreams"][PROFILE_ID]["server"],
                            addresses, 1792080000)


def compile_fixture(value=None, rows=None, config=None, **kwargs):
    value = value or snapshot(True, True)
    rows = rows if rows is not None else [inbound()]
    config = config if config is not None else SingBoxAdapter().build_config([r for r in rows if r.core == "sing-box"])
    if "endpoint" not in kwargs:
        kwargs["endpoint"] = endpoint(value)
    kwargs.setdefault("self_addresses", ("127.0.0.1", "9.9.9.9"))
    return compile_proxy_policy(config, rows, value, **kwargs)


class AddressPolicyTests(unittest.TestCase):
    def test_exact_contract_table_and_ipv6_complement(self):
        self.assertEqual(len(DENY_CIDRS), 26)
        self.assertEqual(DENY_CIDRS[:4], ("0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8"))
        self.assertEqual(NON_GLOBAL_IPV6_CIDRS, ("::/3", "4000::/2", "8000::/1"))
        for cidr in DENY_CIDRS:
            with self.subTest(cidr=cidr), self.assertRaises(PolicyError):
                AddressPolicy().check_addresses((str(ipaddress.ip_network(cidr).network_address),))

    def test_public_results_are_canonical_deduplicated_and_stably_sorted(self):
        self.assertEqual(AddressPolicy().check_addresses(("2606:4700:4700::1111", "8.8.8.8", "1.1.1.1", "8.8.8.8")),
                         ("1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"))

    def test_one_bad_answer_rejects_whole_set_in_any_order(self):
        bad = ("10.0.0.1", "100.100.100.200", "169.254.169.254", "192.168.1.1", "::ffff:8.8.8.8",
               "::ffff:127.0.0.1", "64:ff9b::808:808", "64:ff9b:1::1", "2001:db8::1", "2002:808:808::1",
               "300::1", "4000::1", "8000::1", "fe80::1%eth0", "8.8.8.8/32", "localhost", "8.8.8.8 ")
        for target in bad:
            for values in ((target, "1.1.1.1"), ("1.1.1.1", target)):
                with self.subTest(values=values), self.assertRaises(PolicyError):
                    AddressPolicy().check_addresses(values)

    def test_self_and_upstream_are_exact_all_ports_restrictions(self):
        for keyword in ("self_addresses", "excluded_addresses"):
            with self.assertRaises(PolicyError) as caught:
                AddressPolicy().check_addresses(("8.8.8.8", "1.1.1.1"), **{keyword: ("8.8.8.8",)})
            self.assertEqual(caught.exception.code, "SELF_OR_UPSTREAM_ADDRESS")

    def test_empty_malformed_or_coerced_results_are_rejected(self):
        for values in ((), [], "8.8.8.8", {"8.8.8.8"}, [1], [None], [True], ["8.8.8.8"] * 4097):
            with self.subTest(values=repr(values)[:30]), self.assertRaises(PolicyError):
                AddressPolicy().check_addresses(values)

    def test_python_fixture_exception_is_exact_and_cannot_erase_dynamic_deny(self):
        policy = AddressPolicy._for_tests(allowed_addresses=("127.0.0.2",))
        self.assertEqual(policy.check_addresses(("127.0.0.2",)), ("127.0.0.2",))
        for values in (("127.0.0.3",), ("127.0.0.2", "10.0.0.1")):
            with self.assertRaises(PolicyError):
                policy.check_addresses(values)
        with self.assertRaises(PolicyError):
            policy.check_addresses(("127.0.0.2",), self_addresses=("127.0.0.2",))
        networks = [ipaddress.ip_network(cidr) for cidr in policy.denied_cidrs(excluded_addresses=("127.0.0.2",))]
        self.assertTrue(any(ipaddress.ip_address("127.0.0.2") in net for net in networks if net.version == 4))
        with self.assertRaises(PolicyError):
            AddressPolicy._for_tests(allowed_addresses=("8.8.8.8",))


class ProxyPolicyTests(unittest.TestCase):
    def test_four_states_preserve_inputs_and_source_scope(self):
        rows = [inbound(), inbound(2)]
        base = SingBoxAdapter().build_config(rows)
        base["route"]["rules"] = [{"domain": ["ordinary.example.test"], "action": "route", "outbound": "direct"}]
        for local in (False, True):
            for relay in (False, True):
                with self.subTest(local=local, relay=relay):
                    value = snapshot(local, relay)
                    before = deepcopy((base, value, [vars(row) for row in rows]))
                    result = compile_fixture(value, rows, base)
                    self.assertEqual((base, value, [vars(row) for row in rows]), before)
                    self.assertEqual(sum(i["tag"] == LOCAL_TAG for i in result.config["inbounds"]), int(local))
                    if not local and not relay:
                        self.assertEqual(result.config, base)
                        self.assertIsNot(result.config, base)
                        continue
                    groups = ([NAMESPACE + "ingress-1", NAMESPACE + "ingress-2"] if relay else []) + ([LOCAL_TAG] if local else [])
                    rules = result.config["route"]["rules"]
                    self.assertEqual(rules[0], {"inbound": groups, "network": "udp", "action": "reject"})
                    self.assertEqual(rules[-2], {"inbound": groups, "action": "reject"})
                    self.assertEqual(rules[-1], base["route"]["rules"][0])
                    routes = [r for r in rules if r.get("outbound") == UPSTREAM_TAG]
                    self.assertEqual(len(routes), 1)
                    self.assertEqual(routes[0]["network"], "tcp")
                    self.assertEqual(result.config["route"]["final"], "direct")
                    self.assertEqual(len(result.coverage), 2)
                    if not relay:
                        self.assertEqual(result.config["inbounds"][:2], base["inbounds"])

    def test_off_ignores_dangling_or_corrupt_upstreams_and_endpoint(self):
        value = snapshot()
        value["upstreams"] = {"broken": object()}
        value["settings"]["selected_upstream_id"] = "no-longer-present"
        base = {"inbounds": [], "custom": {"legacy": [1, 2, 3]}}
        result = compile_proxy_policy(base, [], value, endpoint=object())
        self.assertEqual(result.config, base)

    def test_local_only_allows_existing_xray_and_other_ordinary_nodes(self):
        rows = [inbound(), inbound(2, core="xray"), inbound(3, protocol="vmess")]
        result = compile_fixture(snapshot(True, False), rows)
        self.assertEqual(len(result.config["inbounds"]), 3)
        self.assertEqual([r["qualified"] for r in result.coverage], [True, False, False])
        self.assertTrue(all(r.get("inbound") == [LOCAL_TAG] for r in result.config["route"]["rules"]))

    def test_any_unqualified_enabled_ingress_blocks_whole_relay(self):
        variations = [inbound(2, core="xray"), inbound(2, protocol="trojan"), inbound(2, expiry_time=123),
                      inbound(2, settings={"users": [{"uuid": UUID}], "tag": "injected"}),
                      inbound(2, settings={"users": [{"uuid": UUID}, {"uuid": UUID}]}),
                      inbound(2, settings={"users": [{"uuid": UUID, "flow": "xtls-rprx-vision"}]})]
        for transport in ("tcp", "ws", "grpc", "httpupgrade", "xhttp"):
            row = inbound(2)
            row.stream_settings["transport"] = {"type": transport}
            variations.append(row)
        for bad in variations:
            for local in (False, True):
                with self.subTest(core=bad.core, protocol=bad.protocol, local=local), self.assertRaises(PolicyError) as caught:
                    compile_fixture(snapshot(local, True), [inbound(), bad])
                self.assertEqual(caught.exception.code, "UNQUALIFIED_INGRESS")
                self.assertEqual(len(caught.exception.coverage), 2)
                self.assertNotIn(UUID, str(caught.exception) + repr(caught.exception.coverage))

    def test_disabled_unqualified_ingress_does_not_block(self):
        result = compile_fixture(rows=[inbound(), inbound(2, core="xray", enable=False)])
        self.assertEqual(len(result.coverage), 1)

    def test_local_auth_and_loopback_are_explicit(self):
        value = compile_fixture(snapshot(True, False)).config["inbounds"][-1]
        self.assertEqual(value, {"type": "mixed", "tag": LOCAL_TAG, "listen": "127.0.0.1", "listen_port": 17891,
                               "set_system_proxy": False, "users": [{"username": "fixture-user", "password": PASSWORD}]})
        for username, password, port in (("", PASSWORD, 17891), ("u", "", 17891), ("u", "a b" * 5, 17891),
                                          ("u", PASSWORD, 80), ("u", PASSWORD, True)):
            value = snapshot(True)
            value["settings"].update(local_username=username, local_port=port)
            value["local_password"] = password
            with self.assertRaises(PolicyError):
                compile_fixture(value)

    def test_snapshot_local_port_collision_includes_other_cores(self):
        rows = [inbound(), inbound(2, core="xray", port=17891)]
        with self.assertRaises(PolicyError) as caught:
            compile_fixture(snapshot(True, False), rows)
        self.assertEqual(caught.exception.code, "LOCAL_PORT_COLLISION")

    def test_qualifying_source_is_bound_to_exact_config_and_deterministic_id(self):
        row = inbound(tag="UNTRUSTED_DISPLAY_TAG")
        result = compile_fixture(rows=[row])
        self.assertEqual(result.config["inbounds"][0]["tag"], NAMESPACE + "ingress-1")
        for key, val in (("listen_port", 11002), ("listen", "127.0.0.1"), ("type", "mixed"), ("detour", "direct"),
                         ("users", [{"uuid": "22222222-2222-2222-2222-222222222222"}])):
            base = SingBoxAdapter().build_config([row])
            base["inbounds"][0][key] = val
            with self.subTest(key=key), self.assertRaises(PolicyError) as caught:
                compile_fixture(rows=[row], config=base)
            self.assertEqual(caught.exception.code, "INGRESS_SNAPSHOT_MISMATCH")

    def test_extra_missing_duplicate_ids_and_colliding_tags_reject(self):
        for rows in ([inbound(), inbound()], [inbound(id=True)], [inbound(2, tag="same"), inbound(3, tag="same")]):
            with self.assertRaises(PolicyError):
                compile_fixture(rows=rows)
        for suffix in ([], [inbound(2)]):
            with self.assertRaises(PolicyError):
                compile_fixture(rows=[inbound()], config=SingBoxAdapter().build_config(suffix))
        for section, entry in (("outbounds", {"type": "direct", "tag": UPSTREAM_TAG}),
                               ("inbounds", {"type": "mixed", "tag": LOCAL_TAG}),
                               ("outbounds", {"type": "direct", "tag": "direct"})):
            base = SingBoxAdapter().build_config([inbound()])
            base[section].append(entry)
            with self.assertRaises(PolicyError):
                compile_fixture(config=base)

    def test_dangling_reserved_reference_is_never_activated(self):
        base = SingBoxAdapter().build_config([inbound()])
        base["route"]["rules"] = [{"inbound": [LOCAL_TAG], "outbound": "direct"}]
        with self.assertRaises(PolicyError):
            compile_fixture(config=base)

    def test_upstream_pins_sorted_checked_ip_keeps_original_sni_and_does_not_resolve(self):
        value = snapshot(True, True)
        with patch("socket.getaddrinfo", side_effect=AssertionError("compiler must be pure")):
            result = compile_fixture(value, endpoint=endpoint(value, ("2606:4700:4700::1111", "8.8.8.8", "1.1.1.1")))
        outbound = result.config["outbounds"][-1]
        self.assertEqual((outbound["server"], outbound["tls"]["server_name"]), ("1.1.1.1", "vpn.example.test"))
        self.assertEqual(outbound["network"], "tcp")
        self.assertNotIn("detour", outbound)
        self.assertNotIn("insecure", outbound["tls"])
        self.assertEqual(result.pinned_address, "1.1.1.1")

    def test_reality_map_and_strict_profile_revalidation(self):
        result = compile_fixture(snapshot(True, True, reality=True))
        value = result.config["outbounds"][-1]
        self.assertEqual(value["flow"], "xtls-rprx-vision")
        self.assertEqual(value["tls"]["utls"], {"enabled": True, "fingerprint": "chrome"})
        self.assertEqual(value["tls"]["reality"], {"enabled": True, "public_key": "A" * 43, "short_id": "0123456789abcdef"})
        for key, val in (("flow", ""), ("transport", "ws"), ("detour", "direct")):
            raw = snapshot(True, True, reality=True)
            raw["upstreams"][PROFILE_ID][key] = val
            with self.assertRaises(PolicyError) as caught:
                compile_fixture(raw)
            self.assertEqual(caught.exception.code, "INVALID_SELECTED_UPSTREAM")

    def test_endpoint_is_bound_to_id_revision_host_and_literal_ip(self):
        value = snapshot(True, True)
        for field, other in (("profile_id", "up_" + "2" * 32), ("revision", "b" * 64),
                             ("host", "other.example.test"), ("resolved_at", True), ("addresses", ["8.8.8.8"])):
            args = vars(endpoint(value)).copy(); args[field] = other
            with self.subTest(field=field), self.assertRaises(PolicyError):
                compile_fixture(value, endpoint=VerifiedEndpoint(**args))
        literal = snapshot(True, True, server="8.8.8.8")
        with self.assertRaises(PolicyError):
            compile_fixture(literal, endpoint=endpoint(literal, ("1.1.1.1",)))
        self.assertEqual(compile_fixture(literal).pinned_address, "8.8.8.8")

    def test_address_snapshot_required_and_any_prohibited_result_fails(self):
        for addresses in (None, (), []):
            with self.assertRaises(PolicyError):
                compile_fixture(self_addresses=addresses)
        for addresses in (("8.8.8.8", "127.0.0.1"), ("9.9.9.9",), ("100.100.100.200",), ("::ffff:8.8.8.8",)):
            with self.subTest(addresses=addresses), self.assertRaises(PolicyError):
                compile_fixture(endpoint=endpoint(addresses=addresses))

    def test_business_dns_explicit_selected_detour_and_ordered_fail_closed_rules(self):
        result = compile_fixture().config
        dns = next(value for value in result["dns"]["servers"] if value["tag"] == DNS_TAG)
        self.assertEqual(dns, {"type": "https", "tag": DNS_TAG, "server": "1.1.1.1", "server_port": 443,
                              "path": "/dns-query", "tls": {"enabled": True, "server_name": "cloudflare-dns.com"},
                              "detour": UPSTREAM_TAG})
        rules = result["route"]["rules"]
        self.assertEqual([r["action"] for r in rules], ["reject", "reject", "reject", "resolve", "reject", "reject", "route", "reject"])
        self.assertEqual(rules[3]["server"], DNS_TAG)
        self.assertTrue(rules[3]["disable_cache"])
        self.assertTrue(rules[3]["disable_optimistic_cache"])
        for rule in (rules[1], rules[4]):
            self.assertTrue(set(DENY_CIDRS).issubset(rule["ip_cidr"]))
            self.assertTrue(set(NON_GLOBAL_IPV6_CIDRS).issubset(rule["ip_cidr"]))
            self.assertIn("9.9.9.9/32", rule["ip_cidr"])
            self.assertIn("8.8.8.8/32", rule["ip_cidr"])
        self.assertEqual(rules[6]["ip_cidr"], ["0.0.0.0/0", "::/0"])

    def test_preserves_existing_dns_and_ordinary_rules_without_business_fallback(self):
        base = SingBoxAdapter().build_config([inbound()])
        base["dns"] = {"servers": [{"type": "local", "tag": "existing-dns"}], "final": "existing-dns"}
        result = compile_fixture(snapshot(True, False), config=base).config
        self.assertEqual(result["dns"]["servers"][0], base["dns"]["servers"][0])
        self.assertEqual(result["dns"]["final"], "existing-dns")
        self.assertEqual([r["server"] for r in result["route"]["rules"] if r["action"] == "resolve"], [DNS_TAG])

    def test_empty_relay_does_not_emit_match_everything_rules(self):
        value = compile_fixture(snapshot(False, True), rows=[]).config
        self.assertNotIn("rules", value["route"])
        self.assertNotIn(LOCAL_TAG, json.dumps(value))

    def test_coverage_and_repr_never_leak_material_or_untrusted_text(self):
        bad = inbound(2, core=PASSWORD, protocol=UUID)
        coverage = ingress_coverage([inbound(), bad])
        raw = json.dumps(coverage)
        for forbidden in (UUID, PASSWORD, "certificate_path", "key_path", "/test", "user-provided-tag"):
            self.assertNotIn(forbidden, raw)
        result = compile_fixture()
        self.assertNotIn(PASSWORD, repr(result))
        self.assertNotIn(UUID, repr(result))

    def test_identity_covers_all_addresses_secrets_and_policy_inputs(self):
        value = snapshot(True, True)
        base = compile_fixture(value)
        self.assertEqual(base.identity, compile_fixture(value).identity)
        changed = deepcopy(value); changed["local_password"] = "other-fixture-password"
        self.assertNotEqual(base.identity, compile_fixture(changed).identity)
        self.assertNotEqual(base.identity, compile_fixture(value, self_addresses=("127.0.0.1", "4.2.2.1")).identity)
        self.assertNotEqual(base.identity, compile_fixture(value, endpoint=endpoint(value, ("8.8.8.8", "8.8.4.4"))).identity)

    def test_flags_and_policy_versions_are_strict(self):
        for key, value in (("relay_enabled", 1), ("local_proxy_enabled", "false"), ("dns_policy_version", 2),
                           ("deny_policy_version", True), ("schema_version", None), ("allow_private", True)):
            raw = snapshot()
            raw["settings"][key] = value
            with self.subTest(key=key), self.assertRaises(PolicyError):
                compile_fixture(raw)


if __name__ == "__main__":
    unittest.main()
