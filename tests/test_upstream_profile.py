"""M1 canonical upstream contract; all credentials and hosts are fixtures."""
import base64
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
import unittest

from app.services.upstream_profile import TLSProfile, UpstreamProfile, UpstreamValidationError

UUID = "11111111-1111-4111-8111-111111111111"
PUBLIC_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
SHORT_ID = "0123456789abcdef"


def profile_dict(reality=False):
    return {"schema_version": 1, "protocol": "vless", "name": "海外出口", "server": "upstream.example.test",
            "server_port": 443, "uuid": UUID, "transport": "tcp", "flow": "xtls-rprx-vision" if reality else "",
            "encryption": "none", "tls": {"mode": "reality" if reality else "tls", "server_name": "tls.example.test",
                "alpn": [], "fingerprint": "chrome" if reality else "", "public_key": PUBLIC_KEY if reality else "",
                "short_id": SHORT_ID if reality else ""}}


class UpstreamProfileTests(unittest.TestCase):
    def reject(self, data, code=None):
        with self.assertRaises(UpstreamValidationError) as caught:
            UpstreamProfile.from_dict(data)
        if code:
            self.assertEqual(caught.exception.code, code)
        self.assertNotIn(UUID, str(caught.exception))
        self.assertNotIn(PUBLIC_KEY, str(caught.exception))
        return caught.exception

    def test_exact_canonical_tls_and_reality_roundtrip(self):
        for reality in (False, True):
            data = profile_dict(reality)
            profile = UpstreamProfile.from_dict(data)
            self.assertEqual(profile.to_dict(), data)
            self.assertEqual(profile, UpstreamProfile.from_dict(profile.to_dict()))
            self.assertEqual(type(profile.tls), TLSProfile)
            self.assertEqual(profile.tls.alpn, ())
            self.assertEqual(data, profile_dict(reality))

    def test_model_is_immutable_and_does_not_alias_mutable_input(self):
        data = profile_dict()
        data["tls"]["alpn"] = ["h2"]
        profile = UpstreamProfile.from_dict(data)
        data["tls"]["alpn"].append("http/1.1")
        self.assertEqual(profile.tls.alpn, ("h2",))
        with self.assertRaises(FrozenInstanceError):
            profile.server = "changed.example.test"
        with self.assertRaises(FrozenInstanceError):
            profile.tls.server_name = "changed.example.test"
        copy = profile.to_dict()
        copy["tls"]["alpn"].append("http/1.1")
        self.assertEqual(profile.tls.alpn, ("h2",))

    def test_redacted_and_repr_have_no_credential_material(self):
        profile = UpstreamProfile.from_dict(profile_dict(True))
        safe = profile.redacted()
        for secret in (UUID, PUBLIC_KEY, SHORT_ID):
            self.assertNotIn(secret, json.dumps(safe))
            self.assertNotIn(secret, repr(profile))
            self.assertNotIn(secret, repr(profile.tls))
        self.assertEqual(set(safe), {"schema_version", "protocol", "name", "server", "server_port", "transport",
                                    "tls", "flow", "encryption", "uuid_set"})
        self.assertEqual(set(safe["tls"]), {"mode", "server_name", "alpn", "fingerprint", "public_key_set", "short_id_set"})
        self.assertTrue(safe["uuid_set"])
        self.assertTrue(safe["tls"]["public_key_set"])
        self.assertTrue(safe["tls"]["short_id_set"])
        self.assertEqual(profile.to_dict()["uuid"], UUID)

    def test_unknown_fields_never_disappear(self):
        for path in (None, "tls"):
            for key in ("insecure", "detour", "private_key", "certificate_path", "extra", "uuid_set"):
                data = profile_dict()
                (data if path is None else data[path])[key] = "private-fixture"
                with self.subTest(path=path, key=key):
                    self.reject(data, "UNKNOWN_FIELD")

    def test_no_coercion_for_root_or_nested_mapping(self):
        for value in (None, [], "{}", 1, True, {1: "value"}):
            self.reject(value, "INVALID_SHAPE")
            data = profile_dict()
            data["tls"] = value
            self.reject(data, "INVALID_SHAPE")

    def test_required_fields_do_not_generate_credentials_or_sni(self):
        for key in ("name", "server", "server_port", "uuid", "tls"):
            data = profile_dict()
            del data[key]
            self.reject(data, "MISSING_FIELD")
        for key in ("mode", "server_name"):
            data = profile_dict()
            del data["tls"][key]
            self.reject(data, "MISSING_FIELD")

    def test_only_documented_model_defaults(self):
        data = profile_dict()
        for key in ("schema_version", "protocol", "transport", "flow", "encryption"):
            data.pop(key)
        for key in ("alpn", "fingerprint", "public_key", "short_id"):
            data["tls"].pop(key)
        self.assertEqual(UpstreamProfile.from_dict(data).to_dict(), profile_dict())

    def test_names_have_unicode_char_limit_and_no_invisible_controls(self):
        for name in ("x", "中" * 128, "a name", "☁️ 海外"):
            data = profile_dict()
            data["name"] = name
            self.assertEqual(UpstreamProfile.from_dict(data).name, name)
        for name in ("", None, False, 1, "x" * 129, "a\nname", "a\tname", "a\x7fname", "a\x85name",
                     "a\u202ename", "a\u200bname", "a\u2028name", "a\u2029name", "a\ufeffname", "a\ud800name"):
            data = profile_dict()
            data["name"] = name
            with self.subTest(name=repr(name)):
                self.reject(data)

    def test_port_is_strict_integer_and_full_network_range(self):
        for port in (1, 443, 65535):
            data = profile_dict()
            data["server_port"] = port
            self.assertEqual(UpstreamProfile.from_dict(data).server_port, port)
        for port in (0, -1, 65536, "443", 443.0, True, False, None, [], 2**64):
            data = profile_dict()
            data["server_port"] = port
            self.reject(data, "INVALID_PORT")

    def test_server_syntax_is_not_a_network_policy_decision(self):
        for host in ("example.test", "HOST.example.test", "single-label", "127.0.0.1", "192.0.2.1", "::1", "2001:db8::1", "0.0.0.0"):
            data = profile_dict()
            data["server"] = host
            self.assertEqual(UpstreamProfile.from_dict(data).server, host)
        for host in ("", None, " https://x", "https://host.test", "host.test:443", "[::1]", "fe80::1%eth0", "${SERVER}",
                     "host.test.", "a_b.test", "127.00.00.1", "-host.test", "a" * 64 + ".test", "目标.test"):
            data = profile_dict()
            data["server"] = host
            self.reject(data)

    def test_sni_is_explicit_literal_dns_not_ip_url_or_expansion(self):
        for value in ("", None, True, "127.0.0.1", "::1", "https://host.test", "*.host.test", "host.test:443",
                      "host.test.", "${SNI}", "host\n.test", "x" * 254):
            data = profile_dict()
            data["tls"]["server_name"] = value
            self.reject(data)

    def test_uuid_is_one_canonical_literal_without_generation(self):
        for uid in ("", None, False, 1, UUID.upper(), UUID.replace("-", ""), "{" + UUID + "}", "urn:uuid:" + UUID,
                    UUID + "\n", "user@example.test", [UUID], "bad-uuid"):
            # This fixture contains no alphabetic hex; use an uppercase-distinct UUID below.
            if uid == UUID:
                continue
            data = profile_dict()
            data["uuid"] = uid
            self.reject(data)
        data = profile_dict()
        data["uuid"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa".upper()
        self.reject(data, "INVALID_UUID")

    def test_protocol_schema_transport_and_encryption_are_closed(self):
        cases = {"schema_version": [0, 2, "1", True, 1.0, None], "protocol": ["vmess", "VLESS", "", None, {}],
                 "transport": ["raw", "ws", "grpc", "xhttp", "httpupgrade", "kcp", "", None],
                 "encryption": ["", "auto", "aes-128-gcm", None, False]}
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    data = profile_dict()
                    data[key] = value
                    self.reject(data)

    def test_tls_flow_and_reality_security_fields_cannot_mix(self):
        for field, value in (("flow", "xtls-rprx-vision"), ("flow", "xtls-rprx-vision-udp443"), ("flow", None)):
            data = profile_dict()
            data[field] = value
            self.reject(data, "UNSUPPORTED_FLOW")
        for key in ("public_key", "short_id"):
            data = profile_dict()
            data["tls"][key] = PUBLIC_KEY if key == "public_key" else SHORT_ID
            self.reject(data, "CONFLICTING_SECURITY")
        for value in ("", "none", "xtls", "TLS", None, []):
            data = profile_dict()
            data["tls"]["mode"] = value
            self.reject(data, "UNSUPPORTED_SECURITY")

    def test_tls_alpn_order_and_fingerprint_matrix(self):
        for alpn in ([], ["h2"], ["http/1.1"], ["h2", "http/1.1"], ["http/1.1", "h2"]):
            for fp in ("", "chrome"):
                data = profile_dict()
                data["tls"].update(alpn=alpn, fingerprint=fp)
                self.assertEqual(UpstreamProfile.from_dict(data).to_dict(), data)
        for alpn in (None, "h2", ("h2",), [""], ["h3"], ["h2", "h2"], ["h2", 1], ["h2", "http/1.1", "h3"]):
            data = profile_dict()
            data["tls"]["alpn"] = alpn
            self.reject(data, "UNSUPPORTED_ALPN")
        for fp in (None, "Chrome", "firefox", "random", False, []):
            data = profile_dict()
            data["tls"]["fingerprint"] = fp
            self.reject(data, "UNSUPPORTED_FINGERPRINT")

    def test_reality_requires_exact_vision_chrome_and_empty_alpn(self):
        for field, value in (("flow", ""), ("flow", "xtls-rprx-vision-udp443"), ("flow", None)):
            data = profile_dict(True)
            data[field] = value
            self.reject(data, "UNSUPPORTED_FLOW")
        for key, value in (("fingerprint", ""), ("fingerprint", "firefox"), ("alpn", ["h2"]), ("alpn", None)):
            data = profile_dict(True)
            data["tls"][key] = value
            self.reject(data)

    def test_reality_key_has_exact_size_alphabet_and_canonical_padding_bits(self):
        for key in ("", None, PUBLIC_KEY + "=", PUBLIC_KEY[:-1], PUBLIC_KEY + "A", "A" * 42 + "B", "/" * 43,
                    base64.urlsafe_b64encode(b"x" * 31).decode().rstrip("="),
                    base64.urlsafe_b64encode(b"x" * 33).decode().rstrip("=")):
            data = profile_dict(True)
            data["tls"]["public_key"] = key
            self.reject(data)

    def test_reality_short_id_is_exact_lowercase_hex_string(self):
        for short in ("", None, SHORT_ID[:-1], SHORT_ID + "0", SHORT_ID.upper(), "g" * 16, 1234567890123456, "0x" + SHORT_ID):
            data = profile_dict(True)
            data["tls"]["short_id"] = short
            self.reject(data)

    def test_direct_construction_and_replace_cannot_bypass_validation(self):
        profile = UpstreamProfile.from_dict(profile_dict())
        for changes in ({"server_port": True}, {"uuid": ""}, {"tls": profile_dict()["tls"]}, {"protocol": "vmess"}):
            with self.assertRaises(UpstreamValidationError):
                replace(profile, **changes)
        with self.assertRaises(UpstreamValidationError):
            TLSProfile("tls", "example.test", alpn=["h2"])


if __name__ == "__main__":
    unittest.main()
