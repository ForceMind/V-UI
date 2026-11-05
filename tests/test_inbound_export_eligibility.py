"""Safe configuration eligibility, not DNS/runtime/public-server qualification."""
import base64
from contextlib import ExitStack
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from app.models.database import Inbound
from app.services.inbound_export_eligibility import export_eligibility
from app.services.inbound_service import to_dict
from app.services.tcp_upstream_export import tcp_upstream_profile
from app.services.validated_export import share_link

UUID = "11111111-1111-4111-8111-111111111111"
PASSWORD = "synthetic-eligibility-password"
PRIVATE = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
PUBLIC = base64.urlsafe_b64encode(X25519PrivateKey.from_private_bytes(bytes(range(32)))
    .public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode().rstrip("=")
SHORT = "0123456789abcdef"
CERTIFICATE = "/synthetic/private/eligibility-cert.pem"
KEY = "/synthetic/private/eligibility-key.pem"


def inbound(**changes):
    values = dict(id=7, instance_id="a" * 32, routing_mode="direct", core="sing-box",
        protocol="vless", port=10443, remark="synthetic eligibility", enable=True,
        expiry_time=0, tag="synthetic-tag", settings={"users": [{"uuid": UUID}]},
        stream_settings={"tls": {"enabled": True, "server_name": "tls.example.test",
            "certificate_path": CERTIFICATE, "key_path": KEY},
            "_vui": {"security": "tls", "server_name": "tls.example.test",
                "skip_cert_verify": False}})
    values.update(changes)
    return Inbound(**values)


def reality():
    item = inbound(settings={"users": [{"uuid": UUID, "flow": "xtls-rprx-vision"}]})
    item.stream_settings = {
        "tls": {"enabled": True, "server_name": "tls.example.test", "reality": {
            "enabled": True, "handshake": {"server": "target.example.test", "server_port": 443},
            "private_key": PRIVATE, "short_id": [SHORT]}},
        "_vui": {"security": "reality", "server_name": "tls.example.test",
            "client_fingerprint": "chrome", "reality_public_key": PUBLIC,
            "reality_short_id": SHORT}}
    return item


def xray():
    return inbound(core="xray", settings={"users": [{"id": UUID, "flow": ""}], "decryption": "none"},
        stream_settings={"method": "raw", "security": "tls", "tlsSettings": {
            "serverName": "tls.example.test", "certificates": [
                {"certificateFile": CERTIFICATE, "keyFile": KEY}]}})


def snapshot(item):
    return deepcopy({column.name: getattr(item, column.name) for column in Inbound.__table__.columns})


class InboundExportEligibilityTests(unittest.TestCase):
    def assert_eligibility(self, item, share, upstream):
        original = snapshot(item)
        expected = {"share_eligible": share, "upstream_export_eligible": upstream}
        self.assertEqual(export_eligibility(item), expected)
        summary = to_dict(item)
        for key, value in expected.items():
            self.assertIs(summary[key], value)
        self.assertEqual(snapshot(item), original)
        self.assertEqual(summary["routing_mode"], original["routing_mode"])
        self.assertIsNotNone(summary["mutation_revision"])
        self.assertEqual(summary["mutation_revision"], to_dict(item)["mutation_revision"])
        encoded = json.dumps(summary, ensure_ascii=False)
        for secret in (UUID, PASSWORD, PRIVATE, PUBLIC, SHORT, CERTIFICATE, KEY):
            self.assertNotIn(secret, encoded)
        self.assertNotIn("settings", summary)
        self.assertNotIn("stream_settings", summary)

    def test_tls_and_reality_both_use_actual_qualified_exporters_without_mutation(self):
        for item in (inbound(), reality()):
            with self.subTest(reality="reality" in item.stream_settings["tls"]):
                self.assert_eligibility(item, True, True)
                # The same saved profile must pass the real export authorities;
                # eligibility's placeholder does not establish this real host.
                self.assertIn(UUID, share_link(item, "actual.example.test"))
                self.assertEqual(tcp_upstream_profile(item, "actual.example.test").uuid, UUID)

    def test_supported_ordinary_share_profiles_do_not_gain_upstream_qualification(self):
        rows = [xray()]
        tcp = xray(); tcp.stream_settings["method"] = "tcp"; rows.append(tcp)
        for protocol in ("trojan", "vmess", "hysteria2", "tuic"):
            settings = {"users": [{"uuid": UUID}]} if protocol == "vmess" else {"users": [{"password": PASSWORD}]}
            if protocol == "tuic":
                settings["users"][0]["uuid"] = UUID
            item = inbound(protocol=protocol, settings=settings)
            if protocol == "tuic":
                item.stream_settings["tls"]["alpn"] = ["h3"]
            rows.append(item)
        rows.append(inbound(protocol="shadowsocks", settings={"method": "aes-128-gcm", "password": PASSWORD}, stream_settings={}))
        for transport in ({"type": "ws", "path": "/vless"}, {"type": "grpc", "service_name": "vless"}):
            item = inbound(); item.stream_settings["transport"] = transport; rows.append(item)
        for item in rows:
            with self.subTest(core=item.core, protocol=item.protocol, stream=item.stream_settings):
                self.assert_eligibility(item, True, False)

    def test_unknown_advanced_corrupt_insecure_and_nonstring_profiles_fail_closed(self):
        changes = [
            lambda n: setattr(n, "enable", False), lambda n: setattr(n, "enable", 1),
            lambda n: setattr(n, "expiry_time", 1), lambda n: setattr(n, "expiry_time", False),
            lambda n: setattr(n, "expiry_time", None), lambda n: setattr(n, "expiry_time", "0"),
            lambda n: setattr(n, "core", None), lambda n: setattr(n, "protocol", 0),
            lambda n: setattr(n, "remark", 0), lambda n: setattr(n, "port", True),
            lambda n: setattr(n, "port", "10443"), lambda n: setattr(n, "settings", None),
            lambda n: setattr(n, "settings", []), lambda n: setattr(n, "stream_settings", None),
            lambda n: setattr(n, "stream_settings", "malformed"),
            lambda n: n.settings.update(future_secret=PASSWORD),
            lambda n: n.settings["users"].append({"uuid": UUID}),
            lambda n: n.settings.update(users=[]), lambda n: n.settings.update(users=[None]),
            lambda n: n.settings["users"][0].update(uuid=0),
            lambda n: n.settings["users"][0].update(flow="xtls-rprx-vision"),
            lambda n: n.settings["users"][0].update(unknown=PASSWORD),
            lambda n: n.stream_settings.update(transport={"type": "tcp"}),
            lambda n: n.stream_settings.update(transport={"type": "httpupgrade", "path": "/"}),
            lambda n: n.stream_settings.update(unknown=PASSWORD),
            lambda n: n.stream_settings["tls"].update(enabled="true"),
            lambda n: n.stream_settings["tls"].update(server_name=0),
            lambda n: n.stream_settings["tls"].update(insecure=True),
            lambda n: n.stream_settings["_vui"].update(skip_cert_verify=True),
            lambda n: n.stream_settings["_vui"].update(client_fingerprint=["chrome"]),
            lambda n: n.stream_settings["_vui"].update(unknown=PASSWORD),
        ]
        for change in changes:
            item = inbound(); change(item)
            with self.subTest(change=change):
                self.assert_eligibility(item, False, False)

    def test_reality_credentials_flow_and_server_settings_are_not_repaired(self):
        for change in (
            lambda n: n.settings["users"][0].update(flow=""),
            lambda n: n.settings["users"].append({"uuid": UUID}),
            lambda n: n.stream_settings["tls"]["reality"].update(private_key="invalid"),
            lambda n: n.stream_settings["tls"]["reality"].update(short_id=[SHORT, SHORT]),
            lambda n: n.stream_settings["tls"]["reality"]["handshake"].update(extra=PASSWORD),
            lambda n: n.stream_settings["_vui"].update(client_fingerprint="firefox"),
            lambda n: n.stream_settings.update(transport={"type": "ws", "path": "/"}),
        ):
            item = reality(); change(item)
            with self.subTest(change=change):
                self.assert_eligibility(item, False, False)

    def test_xray_advanced_profiles_are_neither_shareable_nor_upstreams(self):
        for change in (
            lambda n: n.settings["users"].append({"id": UUID}),
            lambda n: n.settings["users"][0].update(flow="xtls-rprx-vision"),
            lambda n: n.stream_settings.update(method="ws"),
            lambda n: n.stream_settings.update(sockopt={"dialerProxy": PASSWORD}),
            lambda n: n.stream_settings["tlsSettings"].update(allowInsecure=True),
        ):
            item = xray(); change(item)
            with self.subTest(change=change):
                self.assert_eligibility(item, False, False)

    def test_summary_is_pure_without_dns_network_database_runtime_or_credential_generation(self):
        rows = (inbound(), reality(), xray())
        with ExitStack() as stack:
            for target in (
                "socket.getaddrinfo", "socket.create_connection", "subprocess.Popen",
                "app.models.database.SessionLocal", "app.models.database.engine.connect",
                "app.services.inbound_service.ensure_credentials",
                "app.services.reality_profile.generate_keypair", "secrets.token_urlsafe",
            ):
                stack.enter_context(patch(target, side_effect=AssertionError(target + " must not run")))
            for item in rows:
                self.assert_eligibility(item, True, item.core == "sing-box")


if __name__ == "__main__":
    unittest.main()
