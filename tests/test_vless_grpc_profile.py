"""Bounded VLESS/gRPC Lite public profile: literal fields and fail-closed exports."""
import base64
from copy import deepcopy
from itertools import product
import json
import unittest
from urllib.parse import parse_qs, unquote, urlsplit

from fastapi import HTTPException
import yaml

from app.services.core_manager import SingBoxAdapter
from app.services.grpc_profile import grpc_service_name
from app.services.protocol_profiles import compile_profile, decompile_profile
from app.services.validated_export import (
    ExportError, base64_subscription, export_warnings, share_link,
    singbox_client_config, validated_node,
)
from app.services.mihomo_subscription import mihomo_config
from test_validated_export import vless_node


SERVICE = "Vless.grpc_0-4.4"
BAD_SERVICES = (
    "", None, False, True, 0, 42, [], {}, "a" * 129, " leading", "trailing ",
    "/service", "name/Tun", "a%2Fb", "a?x=1", "a#part", "a\\b", "a b",
    "a\n", "a\r\nb", "中文", "á", "a\x00b", "a~b", "a:b",
)
EXTRA_TRANSPORTS = (
    {"authority": "proxy.example.test"}, {"headers": {}}, {"host": "edge.example.test"},
    {"idle_timeout": "1m"}, {"ping_timeout": "5s"}, {"permit_without_stream": False},
    {"multi_mode": False}, {"user_agent": "test"}, {"unknown": None},
)


def grpc_node(service=SERVICE):
    item = vless_node(tag="grpc-test")
    item.stream_settings["transport"] = {"type": "grpc", "service_name": service}
    return item


class VlessGRPCProfileTests(unittest.TestCase):
    def assert_all_exports_rejected(self, item):
        before = deepcopy(item.__dict__)
        for exporter in (share_link, base64_subscription, mihomo_config, singbox_client_config):
            with self.subTest(exporter=exporter.__name__), self.assertRaises(ExportError):
                exporter(item if exporter is share_link else [item], "vpn.example.test")
        self.assertEqual(item.__dict__, before)
        self.assertEqual(export_warnings([item])[0]["code"], "UNVERIFIED_EXPORT_PROFILE")

    def test_three_formats_preserve_complete_literal_profile_without_server_material(self):
        for service, fingerprint, alpn in product((SERVICE, ".", "..", "_", "-", "A" * 128), (False, True), (False, True)):
            with self.subTest(service=service, fingerprint=fingerprint, alpn=alpn):
                item = grpc_node(service)
                if fingerprint:
                    item.stream_settings["_vui"] = {"client_fingerprint": "chrome"}
                if alpn:
                    item.stream_settings["tls"]["alpn"] = ["h2"]
                before = deepcopy(item.__dict__)
                config = yaml.safe_load(mihomo_config([item], "2001:db8::1"))
                node = config["proxies"][0]
                self.assertEqual(node["network"], "grpc")
                self.assertEqual(node["grpc-opts"], {"grpc-service-name": service})
                self.assertEqual(node["uuid"], item.settings["users"][0]["uuid"])
                self.assertFalse(node["udp"])
                self.assertFalse(node["skip-cert-verify"])
                self.assertTrue(node["tls"])
                self.assertNotIn("packet-encoding", node)
                client = singbox_client_config([item], "2001:db8::1")
                outbound = client["outbounds"][0]
                self.assertEqual(outbound["network"], "tcp")
                self.assertEqual(outbound["transport"], item.stream_settings["transport"])
                self.assertEqual(outbound["tls"].get("alpn"), ["h2"] if alpn else None)
                self.assertEqual(outbound["tls"].get("utls", {}).get("fingerprint"), "chrome" if fingerprint else None)
                self.assertEqual(outbound["tls"]["server_name"], "vpn.example.test")
                self.assertEqual(outbound["uuid"], node["uuid"])
                self.assertEqual(len(client["outbounds"]), 1)
                self.assertEqual(client["route"]["final"], outbound["tag"])
                self.assertNotIn("packet_encoding", outbound)
                self.assertNotIn("insecure", outbound["tls"])
                raw = base64.b64decode(base64_subscription([item], "2001:db8::1")).decode()
                self.assertEqual(raw, share_link(item, "2001:db8::1"))
                uri = urlsplit(raw)
                self.assertEqual((uri.scheme, uri.hostname, uri.port), ("vless", "2001:db8::1", item.port))
                self.assertEqual(uri.username, node["uuid"])
                self.assertEqual(unquote(uri.fragment), item.remark)
                query = {"type": ["grpc"], "serviceName": [service], "security": ["tls"],
                         "sni": ["vpn.example.test"], "encryption": ["none"]}
                if fingerprint: query["fp"] = ["chrome"]
                if alpn: query["alpn"] = ["h2"]
                self.assertEqual(parse_qs(uri.query, keep_blank_values=True), query)
                for output in (raw, json.dumps(node), json.dumps(client)):
                    for secret in ("/private/server", "certificate_path", "key_path", "PRIVATE KEY"):
                        self.assertNotIn(secret, output)
                self.assertEqual(item.__dict__, before)
                self.assertEqual(export_warnings([item]), [])

    def test_literal_service_name_validation_is_shared_and_never_coerces(self):
        for value in BAD_SERVICES:
            with self.subTest(value=value):
                with self.assertRaises(ValueError): grpc_service_name(value)
                item = grpc_node(value)
                self.assert_all_exports_rejected(item)
                profile = decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)
                self.assertEqual(profile["service_name"], value)
                before = deepcopy(item.__dict__)
                with self.assertRaises(HTTPException) as caught:
                    compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
                self.assertEqual(caught.exception.status_code, 422)
                self.assertEqual(item.__dict__, before)
        for service in (SERVICE, ".", "..", "_", "-", "A" * 128):
            self.assertEqual(grpc_service_name(service), service)

    def test_edit_roundtrip_preserves_uuid_service_case_alpn_and_binding_material(self):
        item = grpc_node()
        item.stream_settings["tls"]["alpn"] = ["h2"]
        profile = decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)
        self.assertNotIn(item.settings["users"][0]["uuid"], json.dumps(profile))
        profile["service_name"] = "Edited.Mixed_Case-2"
        before = deepcopy(item.__dict__)
        settings, stream = compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
        self.assertEqual(settings, item.settings)
        self.assertEqual(item.__dict__, before)
        self.assertEqual(stream["transport"], {"type": "grpc", "service_name": "Edited.Mixed_Case-2"})
        self.assertEqual(stream["tls"]["alpn"], ["h2"])
        for field in ("certificate_path", "key_path", "server_name"):
            self.assertEqual(stream["tls"][field], item.stream_settings["tls"][field])
        item.settings, item.stream_settings = settings, stream
        server = SingBoxAdapter().build_config([item])["inbounds"][0]
        self.assertEqual(server["transport"], stream["transport"])
        self.assertNotIn("_vui", server)
        self.assertEqual(validated_node(item, "vpn.example.test")["grpc-opts"]["grpc-service-name"], "Edited.Mixed_Case-2")

    def test_unknown_transport_fields_reject_export_and_edit_including_leaving_grpc(self):
        for extra in EXTRA_TRANSPORTS:
            item = grpc_node(); item.stream_settings["transport"].update(extra)
            with self.subTest(extra=extra):
                self.assert_all_exports_rejected(item)
                profile = decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)
                before = deepcopy(item.__dict__)
                for transport in ("grpc", "direct", "ws"):
                    with self.subTest(transport=transport), self.assertRaises(HTTPException):
                        compile_profile(item.core, item.protocol, {**profile, "transport": transport}, item.settings, item.stream_settings)
                self.assertEqual(item.__dict__, before)
                self.assertEqual(SingBoxAdapter().build_config([item])["inbounds"][0]["transport"], item.stream_settings["transport"])

    def test_unverified_core_protocol_security_settings_and_alpn_fail_closed(self):
        candidates = []
        for updates in ({"core": "xray"}, {"protocol": "vmess"}, {"protocol": "trojan"}, {"enable": False}, {"expiry_time": 1}):
            item = grpc_node(); item.__dict__.update(updates); candidates.append(item)
        for value in (None, False, 0, [], {}, " ", "xtls-rprx-vision"):
            item = grpc_node(); item.settings["users"][0]["flow"] = value; candidates.append(item)
        for value in ("bad", None, 1):
            item = grpc_node(); item.settings["users"][0]["uuid"] = value; candidates.append(item)
        item = grpc_node(); item.settings["users"] *= 2; candidates.append(item)
        item = grpc_node(); item.settings["extra"] = False; candidates.append(item)
        for value in (None, ["http/1.1"], ["h2", "http/1.1"], [], "h2", ["h2", "h2"], [None]):
            item = grpc_node(); item.stream_settings["tls"]["alpn"] = value; candidates.append(item)
        for key, value in (("enabled", False), ("enabled", 1), ("server_name", ""), ("insecure", True), ("min_version", "1.3")):
            item = grpc_node(); item.stream_settings["tls"][key] = value; candidates.append(item)
        for meta in ({"skip_cert_verify": True}, {"skip_cert_verify": "false"}, {"security": "reality"},
                     {"client_fingerprint": "firefox"}, {"server_name": "different.example.test"}, {"unknown": None},
                     *({"server_name": value} for value in (None, False, 0, [], {}))):
            item = grpc_node(); item.stream_settings["_vui"] = meta; candidates.append(item)
        for transport in ({"type": "grpc"}, {"type": "GRPC", "service_name": SERVICE}, None, []):
            item = grpc_node(); item.stream_settings["transport"] = transport
            if transport is None:
                # None is the verified TCP profile, so use a malformed nonempty block instead.
                item.stream_settings["transport"] = {"service_name": SERVICE}
            candidates.append(item)
        for item in candidates:
            with self.subTest(item=item.__dict__): self.assert_all_exports_rejected(item)

    def test_malformed_imported_type_cannot_hide_options_or_normalize_into_public(self):
        for kind in ('GRPC', 'Grpc', 'gRpC'):
            for extra in ({}, {'authority': 'must-not-disappear'}):
                item = grpc_node()
                item.stream_settings['transport'].update(type=kind, **extra)
                before = deepcopy(item.__dict__)
                profile = decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)
                self.assertEqual(profile['transport'], kind)
                self.assertEqual(profile['service_name'], SERVICE)
                for desired in (kind, 'grpc', 'direct'):
                    with self.subTest(kind=kind, desired=desired, extra=extra), self.assertRaises(HTTPException):
                        compile_profile(item.core, item.protocol,
                            {**profile, 'transport': desired, 'service_name': 'Corrected.Service'},
                            item.settings, item.stream_settings)
                self.assertEqual(item.__dict__, before)
                self.assert_all_exports_rejected(item)

    def test_missing_service_requires_deliberate_correction(self):
        item = grpc_node(); item.stream_settings["transport"].pop("service_name")
        profile = decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)
        self.assertEqual(profile["service_name"], "")
        with self.assertRaises(HTTPException):
            compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
        profile["service_name"] = "corrected.Service"
        _, stream = compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
        self.assertEqual(stream["transport"]["service_name"], "corrected.Service")


if __name__ == "__main__":
    unittest.main()
