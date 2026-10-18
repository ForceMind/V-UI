"""Pure per-inbound plans and compilation; not runtime or double-hop evidence."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json
from unittest.mock import patch
import unittest

from app.services.inbound_policy import PolicyError, normalize_mode, plan_from_manifest, policy_plan, row_mode
from app.services.proxy_policy import DNS_TAG, LOCAL_TAG, NAMESPACE, NORMAL_DNS_TAG, UPSTREAM_TAG, prepare_policy
from app.services.core_manager import SingBoxAdapter
from test_proxy_policy import inbound, snapshot, compile_fixture, endpoint, UUID


class InboundPolicyPlanTests(unittest.TestCase):
    def test_strict_mode_rejects_every_non_enum_including_missing(self):
        for mode in (None, "", "DIRECT", " inherit", "relay ", "other", False, 1, [], {}):
            with self.subTest(mode=mode), self.assertRaises(PolicyError):
                normalize_mode(mode)
            with self.assertRaises(PolicyError):
                row_mode(inbound(routing_mode=mode))
        row = inbound(); del row.routing_mode
        with self.assertRaisesRegex(PolicyError, "MISSING_ROUTING_MODE"):
            row_mode(row)
        for mode in ("inherit", "direct", "relay"):
            self.assertEqual(normalize_mode(mode), mode)

    def test_three_modes_times_four_flags(self):
        rows = [inbound(3, routing_mode="relay"), inbound(2, routing_mode="direct"), inbound(1)]
        for local in (False, True):
            for global_relay in (False, True):
                plan = policy_plan(rows, snapshot(local, global_relay)["settings"])
                self.assertEqual(plan.relay_ids, (1, 3) if global_relay else (3,))
                self.assertTrue(plan.requires_upstream)
                self.assertFalse(plan.legacy)
                self.assertEqual(plan.policy_version, 2)
                self.assertEqual([item["id"] for item in plan.manifest], [1, 2, 3])
                self.assertEqual([item["effective"] for item in plan.manifest],
                                 ["relay" if global_relay else "direct", "direct", "relay"])

    def test_disabled_drafts_are_bound_but_do_not_require_upstream(self):
        rows = [inbound(routing_mode="relay", enable=False)]
        plan = policy_plan(rows, snapshot()["settings"])
        self.assertEqual(plan.relay_ids, ())
        self.assertFalse(plan.requires_upstream)
        self.assertFalse(plan.legacy)
        self.assertEqual(plan.manifest[0]["effective"], "relay")
        self.assertFalse(plan.manifest[0]["enable"])
        for local, relay in ((True, False), (False, True), (True, True)):
            self.assertTrue(policy_plan([], snapshot(local, relay)["settings"]).requires_upstream)
        self.assertTrue(policy_plan([], snapshot()["settings"]).legacy)

    def test_manifest_immutable_roundtrip_private_and_full_source_fingerprint(self):
        rows, settings = [inbound(routing_mode="direct"), inbound(2, enable=False)], snapshot()["settings"]
        plan = policy_plan(rows, settings)
        with self.assertRaises(FrozenInstanceError):
            plan.requires_upstream = True
        detached = plan.manifest; detached[0]["mode"] = "relay"
        self.assertEqual(plan.manifest[0]["mode"], "direct")
        self.assertEqual(plan_from_manifest(plan.manifest, settings), plan)
        self.assertEqual(plan.fingerprint, plan.source_fingerprint)
        raw = json.dumps(plan.manifest) + repr(plan)
        for secret in (UUID, "/test/key.pem", "/test/cert.pem", "certificate_path", "key_path"):
            self.assertNotIn(secret, raw)
        changes = (("remark", "changed"), ("expiry_time", 123), ("settings", {"unknown": "secret"}),
                   ("stream_settings", {"tls": {"certificate_path": "changed"}}), ("enable", True),
                   ("routing_mode", "direct"), ("tag", "changed"), ("port", 12000),
                   ("core", "xray"), ("protocol", "vmess"), ("total", 1), ("user_id", 4))
        for key, value in changes:
            new_rows = deepcopy(rows); setattr(new_rows[1], key, value)
            with self.subTest(field=key):
                self.assertNotEqual(plan.fingerprint, policy_plan(new_rows, settings).fingerprint)
        new_settings = deepcopy(settings); new_settings["selected_upstream_id"] = "up_" + "2" * 32
        self.assertNotEqual(plan.fingerprint, policy_plan(rows, new_settings).fingerprint)
        new_rows = deepcopy(rows); new_rows[0].up = 123; new_rows[0].down = 456
        self.assertEqual(plan.fingerprint, policy_plan(new_rows, settings).fingerprint)
        self.assertEqual(plan.fingerprint, policy_plan(list(reversed(rows)), settings).fingerprint)

    def test_manifest_strict_structure_order_types_and_effective_consistency(self):
        settings = snapshot()["settings"]
        manifest = policy_plan([inbound(), inbound(2, enable=False)], settings).manifest
        bads = [None, {}, tuple(manifest), list(reversed(manifest)), manifest + [deepcopy(manifest[0])]]
        for key, value in (("id", True), ("id", 0), ("enable", 1), ("port", True), ("port", 0),
                           ("core", None), ("protocol", ""), ("tag", ""), ("mode", None),
                           ("source_sha256", "f" * 63), ("source_sha256", "F" * 64), ("effective", "relay")):
            changed = deepcopy(manifest); changed[0][key] = value; bads.append(changed)
        changed = deepcopy(manifest); changed[0]["secret"] = "never accepted"; bads.append(changed)
        changed = deepcopy(manifest); del changed[0]["mode"]; bads.append(changed)
        for bad in bads:
            with self.subTest(bad=repr(bad)[:60]), self.assertRaises(PolicyError):
                plan_from_manifest(bad, settings)

    def test_all_rows_ids_and_disabled_corrupt_modes_reject(self):
        for rows in ([inbound(), inbound(enable=False)], [inbound(id=True, enable=False)],
                     [inbound(), inbound(2, routing_mode=None, enable=False)]):
            with self.assertRaises(PolicyError):
                policy_plan(rows, snapshot()["settings"])


class InboundPolicyCompilerTests(unittest.TestCase):
    def test_legacy_exact_config_and_identity_goldens_for_four_states_and_empty_sets(self):
        # Captured from unmodified 6a89 compiler before this feature. Metadata
        # fields below must never enter legacy config, coverage or identity.
        goldens = {
            (False, False, 0): ("afcd65022df9859a6363cb546fadf87aa04da6eca051aee0720b55ae5ddf9730", "afcd65022df9859a6363cb546fadf87aa04da6eca051aee0720b55ae5ddf9730"),
            (False, False, 1): ("c665ae12c6e0e21456ee838e569bac6bac07b397e137cb590835ad5edc21960d", "c665ae12c6e0e21456ee838e569bac6bac07b397e137cb590835ad5edc21960d"),
            (False, True, 0): ("43c9686e4c8c473d35baf13be62cd7c4ab7ff0e1e781611e23761b1ca5e39249", "83f816e1123eb30ac4b386707415ca84426cc7011a1f3064d9a88d9957490f4f"),
            (False, True, 1): ("9e707623d3464a1ae0dadad2b9030bc9b92d05dcced3a67e3a7395643b5af435", "68e46ccd39ac6f2e33636c1864611fa0c1d0aaed57f27a9565b6640514e0a5dd"),
            (True, False, 0): ("58ef60a01439fa89486c1e76092e44faa05dfd8804ce68dc91eb1ec7a513f998", "8f1b49fdde228950bee48e9734a7d9485def2fb13fca7b636e782bd719bd3183"),
            (True, False, 1): ("912b20bd575599c3a039cde520cc7344a3520797b1b8352a390978ed1fd46e10", "f8147554b497de8f54fba1c3bd142a79546eaf0b6cf422efca41f39ed46a8ec6"),
            (True, True, 0): ("bf8157ccc5a1e7250a304d6fc1da0a47983330e2f52cdfa75d222806ec25bcbb", "469b4e816d8c9a8ba620f9cfc2dab25cf3f55fa665c3dae8b490b454645d676c"),
            (True, True, 1): ("fa6a7297cb515126aa278b829c01080a7944b9d62d0b8ccaa24a41babb9da5df", "4de4455c3fc26bcc932de6bdebf6e8dfa67b4467e467754b65493b8b41e7878d"),
        }
        for (local, relay, count), (identity, config_digest) in goldens.items():
            with self.subTest(local=local, relay=relay, count=count):
                result = compile_fixture(snapshot(local, relay), [inbound(i+1) for i in range(count)])
                self.assertEqual(result.policy_version, 1)
                self.assertEqual(result.identity, identity)
                self.assertEqual(hashlib.sha256(json.dumps(result.config, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), config_digest)

    def test_same_config_mode_change_changes_identity_both_global_states(self):
        for relay, mode in ((False, "direct"), (True, "relay")):
            value = snapshot(False, relay)
            legacy = compile_fixture(value, [inbound()])
            explicit = compile_fixture(value, [inbound(routing_mode=mode)])
            self.assertEqual(legacy.config, explicit.config)
            self.assertEqual(legacy.coverage, explicit.coverage)
            self.assertNotEqual(legacy.identity, explicit.identity)
            self.assertEqual(explicit.policy_version, 2)
            self.assertNotEqual(legacy.source_fingerprint, explicit.source_fingerprint)

    def test_mixed_scoping_tcp_udp_dns_and_ordinary_rule_preservation_four_states(self):
        rows = [inbound(1), inbound(2, routing_mode="direct"), inbound(3, routing_mode="relay")]
        base = SingBoxAdapter().build_config(rows)
        ordinary = {"inbound": [rows[1].tag], "domain": ["blocked.example.test"], "action": "reject"}
        base["route"]["rules"] = [ordinary]
        before = deepcopy((base, [vars(row) for row in rows]))
        for local in (False, True):
            for relay in (False, True):
                result = compile_fixture(snapshot(local, relay), rows, base)
                protected = ([NAMESPACE + "ingress-1"] if relay else []) + [NAMESPACE + "ingress-3"]
                if local: protected.append(LOCAL_TAG)
                rules = result.config["route"]["rules"]
                self.assertEqual(rules[0], {"inbound": protected, "network": "udp", "action": "reject"})
                self.assertEqual(rules[-1], ordinary)
                self.assertEqual(rules[-2], {"inbound": protected, "action": "reject"})
                self.assertTrue(all(rule["inbound"] == protected for rule in rules[:-1]))
                self.assertNotIn(rows[1].tag, protected)
                self.assertEqual(result.config["inbounds"][1], base["inbounds"][1])
                self.assertEqual(result.config["dns"]["final"], NORMAL_DNS_TAG)
                self.assertEqual(result.config["route"]["default_domain_resolver"], NORMAL_DNS_TAG)
                self.assertEqual(result.config["dns"]["servers"][0], {"type": "local", "tag": NORMAL_DNS_TAG})
                self.assertEqual(next(rule["server"] for rule in rules if rule["action"] == "resolve"), DNS_TAG)
        self.assertEqual((base, [vars(row) for row in rows]), before)

    def test_explicit_relay_off_off_requires_valid_upstream_and_endpoint(self):
        rows = [inbound(routing_mode="relay")]
        for change in ("missing", "model", "endpoint"):
            value = snapshot()
            kwargs = {"endpoint": endpoint()}
            if change == "missing": value["settings"]["selected_upstream_id"] = None
            if change == "model": value["upstreams"] = {}
            if change == "endpoint": kwargs["endpoint"] = None
            with self.subTest(change=change), self.assertRaises(PolicyError):
                compile_fixture(value, rows, **kwargs)
        self.assertEqual(compile_fixture(snapshot(), rows).config["outbounds"][-1]["tag"], UPSTREAM_TAG)

    def test_preparation_is_pure_validates_binding_model_and_never_grants_config(self):
        rows = [inbound(routing_mode="relay")]
        value = snapshot()
        base = SingBoxAdapter().build_config(rows)
        with patch("socket.getaddrinfo", side_effect=AssertionError("must not resolve")):
            plan = prepare_policy(base, rows, value)
        self.assertTrue(plan.requires_upstream)
        self.assertFalse(hasattr(plan, "config"))
        self.assertEqual(base["inbounds"][0]["tag"], rows[0].tag)
        for badbase, badvalue in (({**base, "inbounds": []}, value), (base, {**value, "upstreams": {}})):
            with self.assertRaises(PolicyError):
                prepare_policy(badbase, rows, badvalue)
        with self.assertRaises(PolicyError):
            compile_fixture(value, rows, endpoint=None)

    def test_disabled_relay_is_draft_with_no_overlay_and_empty_sources_never_match_all(self):
        rows = [inbound(routing_mode="relay", enable=False), inbound(2, routing_mode="direct")]
        result = compile_fixture(snapshot(), rows, endpoint=None, self_addresses=None)
        self.assertEqual(result.config, SingBoxAdapter().build_config(rows))
        for local in (False, True):
            result = compile_fixture(snapshot(local, True), rows)
            rules = result.config["route"].get("rules", [])
            self.assertTrue(all(rule["inbound"] == [LOCAL_TAG] for rule in rules))
            self.assertEqual(bool(rules), local)
            self.assertEqual(result.config["dns"]["final"], NORMAL_DNS_TAG)

    def test_ordinary_inherited_protocol_capabilities_not_narrowed_by_relay_subset(self):
        ordinary = inbound(2, protocol="shadowsocks", settings={"method": "aes-128-gcm", "password": "fixture", "network": "tcp,udp"})
        rows = [inbound(routing_mode="relay"), ordinary]
        base = SingBoxAdapter().build_config(rows)
        result = compile_fixture(snapshot(), rows)
        self.assertEqual(result.config["inbounds"][1], base["inbounds"][1])
        self.assertFalse(result.coverage[1]["qualified"])
        self.assertTrue(all(rule["inbound"] == [NAMESPACE + "ingress-1"] for rule in result.config["route"]["rules"]))
        # Ordinary WS Host stripping and metadata are the existing adapter's
        # behavior. Binding must not require new relay qualifications for it.
        ordinary.protocol = "vless"; ordinary.settings = {"users": [{"uuid": UUID}], "_metadata": "private"}
        ordinary.stream_settings["transport"] = {"type": "ws", "path": "/ws", "headers": {"Host": "example.test"}}
        result = compile_fixture(snapshot(), rows)
        self.assertEqual(result.config["inbounds"][1], SingBoxAdapter().build_config(rows)["inbounds"][1])

    def test_unqualified_override_cross_core_or_effective_relay_rejected(self):
        for mode in ("direct", "relay"):
            for change in ({"core": "xray"}, {"protocol": "vmess"}, {"expiry_time": 1}):
                with self.subTest(mode=mode, change=change), self.assertRaises(PolicyError):
                    compile_fixture(snapshot(), [inbound(routing_mode=mode, **change)])
        with self.assertRaisesRegex(PolicyError, "MIXED_CORE_RELAY_UNSUPPORTED"):
            compile_fixture(snapshot(), [inbound(routing_mode="relay"), inbound(2, core="xray")])
        with self.assertRaisesRegex(PolicyError, "UNQUALIFIED_INGRESS"):
            compile_fixture(snapshot(False, True), [inbound(routing_mode="direct"), inbound(2, protocol="vmess")])

    def test_full_binding_reserved_namespace_and_new_direct_without_upstream(self):
        rows = [inbound(routing_mode="direct"), inbound(2, routing_mode="relay")]
        for change in ("extra", "missing", "wrong-port", "reserved-reference", "duplicate-tag"):
            base = SingBoxAdapter().build_config(rows)
            if change == "extra": base["inbounds"].append(SingBoxAdapter().build_config([inbound(3)])["inbounds"][0])
            if change == "missing": base["inbounds"].pop(0)
            if change == "wrong-port": base["inbounds"][0]["listen_port"] += 10
            if change == "reserved-reference": base["route"]["final"] = UPSTREAM_TAG
            if change == "duplicate-tag": base["inbounds"][0]["tag"] = base["inbounds"][1]["tag"]
            with self.subTest(change=change), self.assertRaises(PolicyError):
                compile_fixture(snapshot(), rows, base)
        base = SingBoxAdapter().build_config(rows[:1]); base["route"]["final"] = UPSTREAM_TAG
        with self.assertRaisesRegex(PolicyError, "RESERVED_TAG_REFERENCE"):
            compile_fixture(snapshot(), rows[:1], base)

    def test_disabled_source_change_invalidates_new_identity_without_changing_config(self):
        rows = [inbound(routing_mode="direct"), inbound(2, enable=False)]
        first = compile_fixture(snapshot(), rows)
        rows[1].stream_settings["tls"]["key_path"] = "/different/fixture/key.pem"
        second = compile_fixture(snapshot(), rows)
        self.assertEqual(first.config, second.config)
        self.assertNotEqual(first.identity, second.identity)
        self.assertNotEqual(first.source_fingerprint, second.source_fingerprint)


if __name__ == "__main__":
    unittest.main()
