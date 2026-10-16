"""Real export/import boundaries; recognizing a field never grants capability."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from urllib.parse import urlencode, urlsplit

import yaml

from app.services.mihomo_subscription import mihomo_config
from app.services.reality_profile import FLOW, generate_keypair
from app.services.upstream_import import preview_upstreams, select_upstream
from app.services.upstream_profile import UpstreamValidationError
from app.services.validated_export import share_link, singbox_client_config, validated_node


UUID = "11111111-1111-4111-8111-111111111111"
SHORT_ID = "0123456789abcdef"
HOST = "upstream.example.test"
SNI = "tls.example.test"
UDP_CODE = "UDP_CAPABILITY_NOT_QUALIFIED"
UDP_REASON = "UDP and packet encoding are not qualified for dual-proxy upstream import"
SECRET = "fake-private-packet-option"


def inbound(reality=False):
    node = SimpleNamespace(id=1, core="sing-box", protocol="vless", port=10443,
        remark="Import capability fixture", enable=True, expiry_time=0,
        settings={"users": [{"uuid": UUID}]},
        stream_settings={"tls": {"enabled": True, "server_name": SNI,
            "certificate_path": "/private/fake-cert.pem", "key_path": "/private/fake-key.pem"}})
    if reality:
        private, public = generate_keypair()
        node.settings["users"][0]["flow"] = FLOW
        node.stream_settings = {
            "tls": {"enabled": True, "server_name": SNI, "reality": {"enabled": True,
                "handshake": {"server": "127.0.0.1", "server_port": 19445},
                "private_key": private, "short_id": [SHORT_ID]}},
            "_vui": {"security": "reality", "server_name": SNI, "client_fingerprint": "chrome",
                "reality_public_key": public, "reality_short_id": SHORT_ID}}
    return node


def tcp_uri(**extra):
    params = {"type": "tcp", "security": "tls", "sni": SNI, "encryption": "none"}
    params.update(extra)
    return f"vless://{UUID}@{HOST}:443?" + urlencode(params)


def tcp_yaml(**extra):
    result = {"type": "vless", "server": HOST, "port": 443, "uuid": UUID,
              "network": "tcp", "tls": True, "servername": SNI, "udp": False}
    result.update(extra)
    return result


class UpstreamImportCapabilityTests(unittest.TestCase):
    def rejected_candidate(self, content, fmt, code, index=0):
        result = preview_upstreams(content, fmt)
        candidate = result.candidates[index]
        self.assertFalse(candidate.supported)
        self.assertEqual(candidate.reason_code, code)
        if code == UDP_CODE:
            self.assertEqual(candidate.reason, UDP_REASON)
        with self.assertRaises(UpstreamValidationError) as caught:
            select_upstream(content, index, fmt)
        self.assertEqual(caught.exception.code, code)
        for secret in (UUID, SECRET, "/private/", "packetEncoding=", "packet-encoding:"):
            self.assertNotIn(secret, json.dumps(result.to_dict()))
            self.assertNotIn(secret, str(caught.exception))
        return result

    def rejected_document(self, content, fmt, code):
        for parse in (lambda: preview_upstreams(content, fmt), lambda: select_upstream(content, 0, fmt)):
            with self.assertRaises(UpstreamValidationError) as caught:
                parse()
            self.assertEqual(caught.exception.code, code)
            for secret in (UUID, SECRET):
                self.assertNotIn(secret, str(caught.exception))

    def test_existing_tls_exports_remain_rejected_with_exact_capability_reason(self):
        node = inbound()
        before = deepcopy(node.__dict__)
        uri = share_link(node, HOST)
        proxy = validated_node(node, HOST)
        self.assertIn("packetEncoding=xudp", urlsplit(uri).query)
        self.assertIs(proxy["udp"], True)
        self.assertEqual(proxy["packet-encoding"], "xudp")
        self.rejected_candidate(uri, "uri", UDP_CODE)
        self.rejected_candidate(yaml.safe_dump(proxy), "clash-yaml", UDP_CODE)
        preview = self.rejected_candidate(mihomo_config([node], HOST, {"mode": "direct"}),
                                          "clash-yaml", UDP_CODE)
        self.assertIn("dns", preview.ignored_sections)
        self.assertIn("rules", preview.ignored_sections)
        self.assertEqual(node.__dict__, before)

    def test_existing_reality_exports_preserve_profile_and_secrets_stay_private(self):
        node = inbound(True)
        before = deepcopy(node.__dict__)
        uri = share_link(node, HOST)
        proxy = validated_node(node, HOST)
        self.assertNotIn("packetEncoding", urlsplit(uri).query)
        self.assertNotIn("packet-encoding", proxy)
        self.assertIs(proxy["udp"], False)
        selected = []
        for content, fmt in ((uri, "uri"), (yaml.safe_dump(proxy), "clash-yaml"),
                             (mihomo_config([node], HOST, {"mode": "direct"}), "clash-yaml")):
            preview = preview_upstreams(content, fmt)
            self.assertTrue(preview.candidates[0].supported)
            profile = select_upstream(content, 0, fmt).to_dict()
            selected.append(profile)
            self.assertEqual(profile["uuid"], UUID)
            self.assertEqual(profile["flow"], FLOW)
            self.assertEqual(profile["tls"]["public_key"], node.stream_settings["_vui"]["reality_public_key"])
            self.assertEqual(profile["tls"]["short_id"], SHORT_ID)
            for secret in (UUID, SHORT_ID, profile["tls"]["public_key"],
                           node.stream_settings["tls"]["reality"]["private_key"]):
                self.assertNotIn(secret, json.dumps(preview.to_dict()))
        self.assertEqual(selected[0], selected[1])
        self.assertEqual(selected[1], selected[2])
        self.assertEqual(node.__dict__, before)

    def test_singbox_json_is_not_mistaken_for_v2ray_json(self):
        for reality in (False, True):
            document = singbox_client_config([inbound(reality)], HOST)
            self.rejected_document(json.dumps(document), "v2ray-json", "UNKNOWN_FIELD")
            self.rejected_candidate(json.dumps({"outbounds": document["outbounds"]}),
                                    "v2ray-json", "UNSUPPORTED_PROTOCOL")

    def test_packet_encoding_is_rejected_for_every_value_without_echo(self):
        for value in ("xudp", "", "none", SECRET, "false", "0", "\n" + SECRET):
            with self.subTest(fmt="uri", value=value):
                self.rejected_candidate(tcp_uri(packetEncoding=value), "uri", UDP_CODE)
        for value in ("xudp", "", "none", SECRET, False, True, None, 0, 1,
                      [SECRET], {"private-option": SECRET}):
            with self.subTest(fmt="clash-yaml", value=value):
                self.rejected_candidate(yaml.safe_dump(tcp_yaml(**{"packet-encoding": value})),
                                        "clash-yaml", UDP_CODE)

    def test_udp_true_is_distinct_from_invalid_types_and_false_or_absence(self):
        self.rejected_candidate(yaml.safe_dump(tcp_yaml(udp=True)), "clash-yaml", UDP_CODE)
        for value in (None, "true", "false", 0, 1, {}, []):
            self.rejected_candidate(yaml.safe_dump(tcp_yaml(udp=value)), "clash-yaml", "UNSUPPORTED_OPTION")
        explicit = tcp_yaml()
        omitted = deepcopy(explicit)
        del omitted["udp"]
        self.assertEqual(select_upstream(yaml.safe_dump(explicit), 0, "clash-yaml"),
                         select_upstream(yaml.safe_dump(omitted), 0, "clash-yaml"))

    def test_unrelated_unknown_fields_and_alias_spellings_keep_unknown_field(self):
        for unknown in ("packet_encoding", "PacketEncoding", "headerType", "privateKey"):
            self.rejected_candidate(tcp_uri(packetEncoding="xudp", **{unknown: SECRET}),
                                    "uri", "UNKNOWN_FIELD")
        for unknown in ("packet_encoding", "packetEncoding", "smux", "private-key"):
            self.rejected_candidate(yaml.safe_dump(tcp_yaml(**{"packet-encoding": "xudp", unknown: SECRET})),
                                    "clash-yaml", "UNKNOWN_FIELD")
        self.rejected_candidate(tcp_uri(udp="true"), "uri", "UNKNOWN_FIELD")

    def test_nested_reality_unknown_fields_precede_yaml_udp_feedback(self):
        reality = validated_node(inbound(True), HOST)
        for source in (tcp_yaml(), reality):
            for udp_fields in ({"packet-encoding": "xudp"}, {"udp": True},
                               {"packet-encoding": "xudp", "udp": True}):
                for unknown in ("private-key", "privateKey", "spider-x", "certificate"):
                    with self.subTest(reality="reality-opts" in source, udp_fields=udp_fields, unknown=unknown):
                        proxy = deepcopy(source)
                        proxy.update(udp_fields)
                        proxy.setdefault("reality-opts", deepcopy(reality["reality-opts"]))[unknown] = SECRET
                        self.rejected_candidate(yaml.safe_dump(proxy), "clash-yaml", "UNKNOWN_FIELD")

    def test_nested_reality_shape_is_not_hidden_by_yaml_udp_feedback(self):
        for udp_fields in ({"packet-encoding": "xudp"}, {"udp": True}):
            for malformed in (None, [], True, SECRET):
                with self.subTest(udp_fields=udp_fields, malformed=malformed):
                    proxy = tcp_yaml(**udp_fields, **{"reality-opts": malformed})
                    self.rejected_candidate(yaml.safe_dump(proxy), "clash-yaml", "INVALID_SHAPE")
                    # A root unknown still wins over nested shape inspection.
                    proxy["private-key"] = SECRET
                    self.rejected_candidate(yaml.safe_dump(proxy), "clash-yaml", "UNKNOWN_FIELD")

    def test_nested_precheck_does_not_change_non_udp_validation_order(self):
        proxy = tcp_yaml(**{"reality-opts": {"private-key": SECRET}})
        self.rejected_candidate(yaml.safe_dump(proxy), "clash-yaml", "UNKNOWN_FIELD")
        proxy["tls"] = False
        self.rejected_candidate(yaml.safe_dump(proxy), "clash-yaml", "UNSUPPORTED_SECURITY")
        # Missing fields remain a rejected capability when the explicit packet
        # declaration already makes this candidate ineligible, never repaired.
        self.rejected_candidate(yaml.safe_dump(tcp_yaml(**{"packet-encoding": "xudp", "reality-opts": {}})),
                                "clash-yaml", UDP_CODE)

    def test_duplicate_keys_and_malformed_encoding_precede_capability_feedback(self):
        base = tcp_uri(packetEncoding="xudp")
        for suffix in ("&packetEncoding=" + SECRET, "&%70acketEncoding=" + SECRET,
                       "&sni=" + SECRET, "&type=tcp"):
            self.rejected_candidate(base + suffix, "uri", "DUPLICATE_KEY")
        self.rejected_document(base + "%XX", "uri", "INVALID_URI")
        source = yaml.safe_dump(tcp_yaml(**{"packet-encoding": "xudp"}))
        self.rejected_document(source + "packet-encoding: " + SECRET + "\n",
                               "clash-yaml", "DUPLICATE_KEY")

    def test_yaml_resource_and_alias_checks_precede_capability_feedback(self):
        source = yaml.safe_dump(tcp_yaml(**{"packet-encoding": "xudp"}))
        self.rejected_document(source.replace("packet-encoding: xudp", "packet-encoding: &packet xudp"),
                               "clash-yaml", "YAML_REFERENCE")
        self.rejected_document(source.replace("packet-encoding: xudp", "packet-encoding: *packet"),
                               "clash-yaml", "YAML_REFERENCE")
        self.rejected_document(yaml.safe_dump(tcp_yaml(**{"packet-encoding": "x" * 8193})),
                               "clash-yaml", "STRING_LIMIT")
        self.rejected_document(source.replace("packet-encoding: xudp", "packet-encoding: !!str xudp"),
                               "clash-yaml", "YAML_TAG")

    def test_existing_protocol_security_and_insecure_priority_is_retained(self):
        self.rejected_candidate(tcp_uri(packetEncoding="xudp").replace("vless://", "trojan://"),
                                "uri", "UNSUPPORTED_PROTOCOL")
        self.rejected_candidate(yaml.safe_dump(tcp_yaml(type="trojan", **{"packet-encoding": "xudp"})),
                                "clash-yaml", "UNSUPPORTED_PROTOCOL")
        self.rejected_candidate(yaml.safe_dump(tcp_yaml(tls=False, udp=True)),
                                "clash-yaml", "UNSUPPORTED_SECURITY")
        self.rejected_candidate(yaml.safe_dump(tcp_yaml(udp=True, **{"skip-cert-verify": True})),
                                "clash-yaml", "UNSUPPORTED_OPTION")

    def test_mixed_candidates_remain_explicitly_selected_and_never_repaired(self):
        supported = validated_node(inbound(True), HOST)
        unsupported = validated_node(inbound(), HOST)
        content = yaml.safe_dump({"proxies": [unsupported, supported]})
        result = self.rejected_candidate(content, "clash-yaml", UDP_CODE)
        self.assertEqual([item.supported for item in result.candidates], [False, True])
        self.assertEqual(select_upstream(content, 1, "clash-yaml").tls.mode, "reality")


if __name__ == "__main__":
    unittest.main()
