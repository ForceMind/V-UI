"""Pure D1 qualification/fidelity; no claim of real-core UDP enforcement."""
import base64
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
import yaml

from app.models.database import Inbound
from app.services.mihomo_subscription import mihomo_config
from app.services.tcp_upstream_export import (
    INELIGIBLE, INVALID_REQUEST, TCPUpstreamExportError, export_tcp_upstream,
    tcp_upstream_profile,
)
from app.services.upstream_import import preview_upstreams, select_upstream
from app.services.validated_export import (
    base64_subscription, share_link, singbox_client_config, validated_node,
)

UUID = "11111111-1111-4111-8111-111111111111"
SNI = "tls.example.test"
SERVER = "upstream.example.test"
SHORT_ID = "0123456789abcdef"
FLOW = "xtls-rprx-vision"
PRIVATE = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
PUBLIC = base64.urlsafe_b64encode(X25519PrivateKey.from_private_bytes(bytes(range(32)))
    .public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode().rstrip("=")
SECRET = "fake-secret-that-must-not-be-echoed"


def inbound(reality=False, **changes):
    values = dict(id=1, routing_mode="inherit", core="sing-box", protocol="vless", port=10443,
        remark="TCP upstream / 中文", enable=True, expiry_time=0, tag="source-inbound",
        settings={"users": [{"uuid": UUID, "name": "source user"}]},
        stream_settings={"tls": {"enabled": True, "server_name": SNI,
            "certificate_path": "/private/fake-cert.pem", "key_path": "/private/fake-key.pem",
            "alpn": ["h2", "http/1.1"]},
            "_vui": {"security": "tls", "server_name": SNI,
                     "client_fingerprint": "chrome", "skip_cert_verify": False}})
    if reality:
        values["settings"]["users"][0]["flow"] = FLOW
        values["stream_settings"] = {
            "tls": {"enabled": True, "server_name": SNI, "reality": {"enabled": True,
                "handshake": {"server": "127.0.0.1", "server_port": 19445},
                "private_key": PRIVATE, "short_id": [SHORT_ID]}},
            "_vui": {"security": "reality", "server_name": SNI, "client_fingerprint": "chrome",
                "reality_public_key": PUBLIC, "reality_short_id": SHORT_ID}}
    values.update(changes)
    return Inbound(**values)


def node_state(item):
    return deepcopy({column.name: getattr(item, column.name) for column in Inbound.__table__.columns})


def expected_profile(item, server=SERVER):
    reality = "reality" in item.stream_settings["tls"]
    return {"schema_version": 1, "protocol": "vless", "name": item.remark,
        "server": server, "server_port": item.port, "uuid": UUID, "transport": "tcp",
        "encryption": "none", "flow": FLOW if reality else "",
        "tls": {"mode": "reality" if reality else "tls", "server_name": SNI,
                "alpn": [] if reality else ["h2", "http/1.1"], "fingerprint": "chrome",
                "public_key": PUBLIC if reality else "", "short_id": SHORT_ID if reality else ""}}


class TCPUpstreamExportTests(unittest.TestCase):
    def test_tls_reality_uri_yaml_round_trip_preserves_all_client_fields(self):
        for reality in (False, True):
            item = inbound(reality)
            before = node_state(item)
            expected = expected_profile(item)
            self.assertEqual(tcp_upstream_profile(item, SERVER).to_dict(), expected)
            for fmt in ("uri", "clash-yaml"):
                with self.subTest(reality=reality, format=fmt):
                    artifact = export_tcp_upstream(item, SERVER, fmt)
                    self.assertEqual(artifact.profile, "tcp-upstream-v1")
                    self.assertEqual(select_upstream(artifact.content, 0, fmt).to_dict(), expected)
                    preview = preview_upstreams(artifact.content, fmt).to_dict()
                    self.assertTrue(preview["candidates"][0]["supported"])
                    self.assertEqual(preview["ignored_sections"], [])
                    self.assertEqual(len(preview["candidates"]), 1)
                    for secret in (UUID, PUBLIC, SHORT_ID, PRIVATE):
                        self.assertNotIn(secret, json.dumps(preview))
                        self.assertNotIn(secret, repr(artifact))
                    for forbidden in (PRIVATE, "/private/", "certificate_path", "key_path", "handshake",
                                      "packetEncoding", "packet-encoding", "_vui"):
                        self.assertNotIn(forbidden, artifact.content)
                    if fmt == "clash-yaml":
                        node = yaml.safe_load(artifact.content)
                        self.assertIs(node["udp"], False)
                        self.assertEqual(node["network"], "tcp")
                        self.assertNotIn("proxies", node)
                        self.assertNotIn("rules", node)
                    else:
                        query = parse_qs(urlsplit(artifact.content).query)
                        self.assertNotIn("udp", query)
                    self.assertEqual(node_state(item), before)

    def test_optional_tls_fields_and_order_are_exact(self):
        for alpn in (None, ["h2"], ["http/1.1"], ["h2", "http/1.1"], ["http/1.1", "h2"]):
            for fingerprint in ("", "chrome"):
                item = inbound()
                if alpn is None:
                    item.stream_settings["tls"].pop("alpn")
                else:
                    item.stream_settings["tls"]["alpn"] = alpn
                item.stream_settings["_vui"]["client_fingerprint"] = fingerprint
                for fmt in ("uri", "clash-yaml"):
                    with self.subTest(alpn=alpn, fingerprint=fingerprint, format=fmt):
                        artifact = export_tcp_upstream(item, SERVER, fmt)
                        result = select_upstream(artifact.content, 0, fmt)
                        self.assertEqual(result.tls.alpn, tuple(alpn or []))
                        self.assertEqual(result.tls.fingerprint, fingerprint)

    def test_literal_sni_case_is_preserved_not_normalized(self):
        item = inbound()
        item.stream_settings["tls"]["server_name"] = "TLS.Example.Test"
        item.stream_settings["_vui"]["server_name"] = "TLS.Example.Test"
        for fmt in ("uri", "clash-yaml"):
            artifact = export_tcp_upstream(item, SERVER, fmt)
            self.assertEqual(select_upstream(artifact.content, 0, fmt).tls.server_name, "TLS.Example.Test")

    def test_host_only_ascii_case_matches_uri_parsing_in_both_formats(self):
        for server in ("UPSTREAM.Example.Test", "2001:0DB8:0000::ABCD"):
            item = inbound()
            expected = tcp_upstream_profile(item, server).to_dict()
            self.assertEqual(expected["server"], server.lower())
            for fmt in ("uri", "clash-yaml"):
                with self.subTest(server=server, format=fmt):
                    artifact = export_tcp_upstream(item, server, fmt)
                    self.assertEqual(select_upstream(artifact.content, 0, fmt).to_dict(), expected)

    def test_host_is_pure_parsing_and_private_hosts_are_not_application_approval(self):
        with patch("socket.getaddrinfo", side_effect=AssertionError("DNS is forbidden")):
            for server in (SERVER, "127.0.0.1", "169.254.169.254", "192.168.1.2", "2001:db8::1"):
                for fmt in ("uri", "clash-yaml"):
                    artifact = export_tcp_upstream(inbound(), server, fmt)
                    self.assertEqual(select_upstream(artifact.content, 0, fmt).server, server)

    def test_host_injection_and_coercion_never_become_a_target(self):
        for server in (None, True, 123, [], {}, "", " host.test", "host.test ", "host.test.",
                       "https://host.test", "user@host.test", "host.test:443", "host.test/path",
                       "host.test?x=1", "host.test#fragment", "host.test\r\nX: injected",
                       "host.test\u202e", "éxample.test", "$(secret)", "[::1]", "fe80::1%eth0", "0.0.0.0", "::"):
            with self.subTest(server=server), self.assertRaises(TCPUpstreamExportError) as caught:
                export_tcp_upstream(inbound(), server, "uri")
            self.assertEqual(str(caught.exception), INVALID_REQUEST)

    def test_unsupported_nodes_and_fields_fail_closed_without_mutation(self):
        mutations = [
            lambda n: setattr(n, "enable", False), lambda n: setattr(n, "enable", 1),
            lambda n: setattr(n, "expiry_time", 1), lambda n: setattr(n, "expiry_time", False),
            lambda n: setattr(n, "expiry_time", None), lambda n: setattr(n, "core", "xray"),
            lambda n: setattr(n, "protocol", "trojan"), lambda n: setattr(n, "port", True),
            lambda n: setattr(n, "port", "443"), lambda n: setattr(n, "port", 0),
            lambda n: setattr(n, "remark", 0), lambda n: setattr(n, "remark", "hidden\u202ename"),
            lambda n: n.settings["users"].append(deepcopy(n.settings["users"][0])),
            lambda n: n.settings["users"][0].update(uuid=SECRET),
            lambda n: n.settings["users"][0].update(uuid="ABCDEFAB-1111-4111-8111-111111111111"),
            lambda n: n.settings["users"][0].update(name=None),
            lambda n: n.settings["users"][0].update(extra=SECRET),
            lambda n: n.settings.update(packet_encoding="xudp"),
            lambda n: n.stream_settings.update(unknown=SECRET),
            lambda n: n.stream_settings.update(transport=None),
            lambda n: n.stream_settings.update(transport={"type": "tcp"}),
            lambda n: n.stream_settings.update(transport={"type": "ws", "path": "/"}),
            lambda n: n.stream_settings.update(transport={"type": "grpc", "service_name": "test"}),
            lambda n: n.stream_settings["tls"].update(enabled=False),
            lambda n: n.stream_settings["tls"].update(server_name=" tls.example.test"),
            lambda n: n.stream_settings["tls"].update(server_name="tls.example.test."),
            lambda n: n.stream_settings["tls"].update(insecure=True),
            lambda n: n.stream_settings["_vui"].update(skip_cert_verify=True),
            lambda n: n.stream_settings["_vui"].update(server_name=0),
            lambda n: n.stream_settings["_vui"].update(server_name=None),
            lambda n: n.stream_settings["_vui"].update(server_name=""),
            lambda n: n.stream_settings["_vui"].update(client_fingerprint="firefox"),
            lambda n: n.stream_settings["_vui"].update(extra=SECRET),
        ]
        for reality in (False, True):
            for index, mutate in enumerate(mutations):
                item = inbound(reality)
                mutate(item)
                before = node_state(item)
                for fmt in ("uri", "clash-yaml"):
                    with self.subTest(reality=reality, mutation=index, format=fmt):
                        with self.assertRaises(TCPUpstreamExportError) as caught:
                            export_tcp_upstream(item, SERVER, fmt)
                        self.assertEqual(str(caught.exception), INELIGIBLE)
                        for secret in (UUID, PRIVATE, PUBLIC, SHORT_ID, SECRET):
                            self.assertNotIn(secret, str(caught.exception))
                        self.assertEqual(node_state(item), before)

    def test_unsupported_tls_values_are_not_silently_dropped(self):
        changes = [lambda n: n.settings["users"][0].update(flow=FLOW),
            lambda n: n.stream_settings["tls"].update(alpn=None),
            lambda n: n.stream_settings["tls"].update(alpn=[]),
            lambda n: n.stream_settings["tls"].update(alpn=["h3"]),
            lambda n: n.stream_settings["tls"].update(alpn=["h2", "h2"]),
            lambda n: n.stream_settings["tls"].update(certificate_path=123),
            lambda n: n.stream_settings["tls"].update(key_path=None)]
        for index, change in enumerate(changes):
            item = inbound(); change(item)
            with self.subTest(change=index), self.assertRaises(TCPUpstreamExportError):
                export_tcp_upstream(item, SERVER, "uri")

    def test_reality_keypair_flow_short_id_and_server_options_are_strict(self):
        changes = [lambda n: n.settings["users"][0].update(flow=""),
            lambda n: n.stream_settings["tls"].update(alpn=["h2"]),
            lambda n: n.stream_settings["tls"]["reality"].update(private_key="A" * 43),
            lambda n: n.stream_settings["tls"]["reality"].update(short_id=[SHORT_ID, SHORT_ID]),
            lambda n: n.stream_settings["tls"]["reality"].update(max_time_difference="1m"),
            lambda n: n.stream_settings["tls"]["reality"]["handshake"].update(detour=SECRET),
            lambda n: n.stream_settings["_vui"].update(reality_public_key="A" * 43),
            lambda n: n.stream_settings["_vui"].update(reality_short_id="fedcba9876543210"),
            lambda n: n.stream_settings["_vui"].update(client_fingerprint="")]
        for index, change in enumerate(changes):
            item = inbound(True); change(item)
            with self.subTest(change=index), self.assertRaises(TCPUpstreamExportError):
                export_tcp_upstream(item, SERVER, "uri")

    def test_unknown_format_never_falls_back_to_an_ordinary_export(self):
        for fmt in (None, 1, True, [], {}, "raw", "sing-box.json", "URI"):
            with self.subTest(format=fmt), self.assertRaises(TCPUpstreamExportError) as caught:
                export_tcp_upstream(inbound(), SERVER, fmt)
            self.assertEqual(str(caught.exception), INVALID_REQUEST)

    def test_ordinary_tls_uri_node_and_subscription_golden_remain_unchanged(self):
        item = inbound()
        expected_uri = (f"vless://{UUID}@{SERVER}:10443?security=tls&type=tcp&sni={SNI}"
            "&encryption=none&packetEncoding=xudp&fp=chrome&alpn=h2%2Chttp%2F1.1"
            "#TCP%20upstream%20%2F%20%E4%B8%AD%E6%96%87")
        expected_node = {"name": item.remark, "type": "vless", "server": SERVER,
            "port": 10443, "uuid": UUID, "udp": True, "tls": True, "servername": SNI,
            "network": "tcp", "packet-encoding": "xudp", "skip-cert-verify": False,
            "client-fingerprint": "chrome", "alpn": ["h2", "http/1.1"]}
        for _ in range(2):
            self.assertEqual(share_link(item, SERVER), expected_uri)
            self.assertEqual(validated_node(item, SERVER), expected_node)
            self.assertEqual(base64_subscription([item], SERVER),
                             base64.b64encode(expected_uri.encode()).decode())
            ordinary = mihomo_config([item], SERVER, {"mode": "direct"})
            self.assertEqual(yaml.safe_load(ordinary)["proxies"], [expected_node])
            for document, fmt in ((expected_uri, "uri"), (ordinary, "clash-yaml")):
                rejected = preview_upstreams(document, fmt).candidates[0]
                self.assertFalse(rejected.supported)
                self.assertEqual(rejected.reason_code, "UDP_CAPABILITY_NOT_QUALIFIED")
            for fmt in ("uri", "clash-yaml"):
                export_tcp_upstream(item, SERVER, fmt)

    def test_full_ordinary_tls_and_reality_export_golden_bytes_are_unchanged(self):
        # Captured from the unchanged D0 exporters and deterministic fake model.
        goldens = {
            False: {"uri": "bb4ef18dd1bd3549d426250dfe0339dfa7cdd5fa24a739e6d6eca061d68ad519",
                "subscription": "b23c50eba717add6eac9e01d4c6d64b88ddcc84643c07a5f40d560000ce80af7",
                "clash": "b6e42abd757c9db5aa4627d9652d95dce7ef8dad479667957331814a9e66548e",
                "sing-box": "4699aac8ca74ab5a885de080ffcf62847d605893ce272bb5157e705c73aa9ba2"},
            True: {"uri": "c5c9254dc19121a696042f1f9d978f4b107a95890567511e714d6532077f7996",
                "subscription": "079a259433361503d90afd9fa39a38a871a1d748e24a353540bee56cc164ea51",
                "clash": "bc342e6b67c733d3384179548c83c7491b18813c96a6ec2d3c2aacca6c73e6f6",
                "sing-box": "1d1345bf2cf784ec874282085d48eae4ba48c1fdea291f24c209cf0e42abfd59"},
        }
        for reality in (False, True):
            item = inbound(reality)
            for _ in range(2):
                outputs = {"uri": share_link(item, SERVER),
                    "subscription": base64_subscription([item], SERVER),
                    "clash": mihomo_config([item], SERVER, {"mode": "direct"}),
                    "sing-box": json.dumps(singbox_client_config([item], SERVER),
                                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)}
                for fmt, content in outputs.items():
                    with self.subTest(reality=reality, format=fmt):
                        self.assertEqual(hashlib.sha256(content.encode()).hexdigest(), goldens[reality][fmt])
                for fmt in ("uri", "clash-yaml"):
                    export_tcp_upstream(item, SERVER, fmt)


if __name__ == "__main__":
    unittest.main()
