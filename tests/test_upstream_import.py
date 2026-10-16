"""Real isolated-parser tests plus deterministic limit/cleanup failure paths."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import yaml

from app.services import upstream_import as importer
from app.services.upstream_import import (
    ImportPreview, UpstreamImportError, import_upstreams, preview_upstreams, select_upstream,
)
from app.services.upstream_profile import UpstreamProfile, UpstreamValidationError

UUID = "11111111-1111-4111-8111-111111111111"
PUBLIC_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
SHORT_ID = "0123456789abcdef"


def uri(reality=False, **changes):
    query = {"type": "tcp", "security": "reality" if reality else "tls", "encryption": "none",
             "sni": "tls.example.test", "flow": "xtls-rprx-vision" if reality else ""}
    if reality:
        query.update(fp="chrome", pbk=PUBLIC_KEY, sid=SHORT_ID)
    query.update(changes)
    return f"vless://{UUID}@upstream.example.test:443?" + urlencode(query) + "#Upstream"


def xray(reality=False):
    tls = {"serverName": "tls.example.test"}
    if reality:
        tls.update(fingerprint="chrome", publicKey=PUBLIC_KEY, shortId=SHORT_ID)
    else:
        tls.update(allowInsecure=False)
    return {"tag": "Upstream", "protocol": "vless", "settings": {"vnext": [{"address": "upstream.example.test",
            "port": 443, "users": [{"id": UUID, "encryption": "none", "flow": "xtls-rprx-vision" if reality else ""}]}]},
            "streamSettings": {"network": "tcp", "security": "reality" if reality else "tls",
                               "realitySettings" if reality else "tlsSettings": tls}}


def clash(reality=False):
    result = {"name": "Upstream", "type": "vless", "server": "upstream.example.test", "port": 443, "uuid": UUID,
              "network": "tcp", "tls": True, "servername": "tls.example.test", "skip-cert-verify": False, "udp": False,
              "flow": "xtls-rprx-vision" if reality else ""}
    if reality:
        result.update({"client-fingerprint": "chrome", "reality-opts": {"public-key": PUBLIC_KEY, "short-id": SHORT_ID}})
    return result


def as_yaml(value):
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False)


class UpstreamImportTests(unittest.TestCase):
    def reject_input(self, value, code=None, source_format="auto"):
        with self.assertRaises(UpstreamValidationError) as caught:
            preview_upstreams(value, source_format)
        if code:
            self.assertEqual(caught.exception.code, code)
        self.assertNotIn(UUID, str(caught.exception))
        self.assertNotIn(PUBLIC_KEY, str(caught.exception))
        return caught.exception

    def unsupported(self, value, code=None, source_format="auto", index=0):
        preview = preview_upstreams(value, source_format)
        candidate = preview.candidates[index]
        self.assertFalse(candidate.supported)
        if code:
            self.assertEqual(candidate.reason_code, code)
        with self.assertRaises(UpstreamValidationError) as caught:
            select_upstream(value, index, source_format)
        self.assertEqual(caught.exception.code, candidate.reason_code)
        self.assertNotIn(UUID, str(caught.exception))
        return preview

    def test_tls_and_reality_three_formats_are_exactly_equivalent(self):
        for reality in (False, True):
            values = ((uri(reality), "uri"), (json.dumps(xray(reality)), "v2ray-json"), (as_yaml(clash(reality)), "clash-yaml"))
            canonical = []
            for value, source_format in values:
                with self.subTest(reality=reality, source_format=source_format):
                    preview = preview_upstreams(value, source_format)
                    self.assertEqual(preview.source_format, source_format)
                    self.assertEqual(preview.source_fingerprint, hashlib.sha256(value.encode()).hexdigest())
                    self.assertEqual(len(preview.candidates), 1)
                    self.assertTrue(preview.candidates[0].supported)
                    canonical.append(select_upstream(value, 0, source_format).to_dict())
            self.assertEqual(canonical[0], canonical[1])
            self.assertEqual(canonical[1], canonical[2])

    def test_single_candidates_still_require_explicit_selection(self):
        result = import_upstreams(uri())
        self.assertIsInstance(result, ImportPreview)
        self.assertNotIsInstance(result, UpstreamProfile)
        self.assertIsInstance(import_upstreams(uri(), selected_index=0), UpstreamProfile)
        for value in (None, True, False, -1, 128, "0", 0.0):
            with self.assertRaises(UpstreamImportError) as caught:
                select_upstream(uri(), value)
            self.assertEqual(caught.exception.code, "INVALID_SELECTION")
        with self.assertRaises(UpstreamImportError):
            select_upstream(uri(), 1)

    def test_unsupported_nodes_retained_and_duplicate_names_selected_by_index(self):
        first, second = xray(), xray(True)
        value = json.dumps({"outbounds": [{"protocol": "freedom"}, first, {"protocol": "blackhole"}, second]})
        preview = preview_upstreams(value)
        self.assertEqual([candidate.index for candidate in preview.candidates], [0, 1, 2, 3])
        self.assertEqual([candidate.supported for candidate in preview.candidates], [False, True, False, True])
        self.assertEqual(preview.candidates[1].name, preview.candidates[3].name)
        self.assertEqual(select_upstream(value, 3).tls.mode, "reality")
        self.assertEqual(select_upstream(value, 1).tls.mode, "tls")
        for index in (0, 2):
            with self.assertRaises(UpstreamImportError):
                select_upstream(value, index)

    def test_preview_is_safe_fixed_allowlist_and_has_no_raw_document_or_secrets(self):
        source = json.dumps({"outbounds": [xray(True)], "api": {"tag": "hidden-admin-material"}})
        preview = preview_upstreams(source)
        safe = preview.to_dict()
        for secret in (UUID, PUBLIC_KEY, SHORT_ID, "hidden-admin-material", "realitySettings", "settings"):
            self.assertNotIn(secret, json.dumps(safe))
            self.assertNotIn(secret, repr(preview))
        self.assertEqual(set(safe), {"source_format", "source_fingerprint", "candidates", "ignored_sections", "notice_codes"})
        self.assertEqual(set(safe["candidates"][0]), {"index", "name", "protocol", "server", "server_port", "transport",
                                                   "tls_mode", "supported", "reason_code", "reason"})
        self.assertEqual(safe["ignored_sections"], ["api"])

    def test_known_client_sections_are_reported_and_not_imported(self):
        for fmt, template, key, sections in (("v2ray-json", xray(), "outbounds", importer.XRAY_IGNORED_SECTIONS),
                                            ("clash-yaml", clash(), "proxies", importer.CLASH_IGNORED_SECTIONS)):
            document = {section: {"ignored-private-value": "fixture"} for section in sections}
            document[key] = [template]
            value = json.dumps(document) if fmt == "v2ray-json" else as_yaml(document)
            preview = preview_upstreams(value, fmt)
            self.assertEqual(preview.ignored_sections, tuple(sorted(sections)))
            self.assertEqual(select_upstream(value, 0, fmt), select_upstream(json.dumps(template) if fmt == "v2ray-json" else as_yaml(template), 0, fmt))

    def test_remote_provider_is_never_fetched_or_selected(self):
        provider = {"unsafe": {"type": "http", "url": "http://127.0.0.1:1/private", "path": "/never/read/provider.yaml"}}
        value = as_yaml({"proxy-providers": provider})
        preview = preview_upstreams(value)
        self.assertEqual(preview.candidates, ())
        self.assertEqual(preview.notice_codes, ("NO_EMBEDDED_NODES",))
        self.assertEqual(preview.ignored_sections, ("proxy-providers",))
        with self.assertRaises(UpstreamImportError):
            select_upstream(value, 0)
        embedded = as_yaml({"proxy-providers": provider, "proxies": [clash()]})
        self.assertTrue(preview_upstreams(embedded).candidates[0].supported)
        self.assertEqual(select_upstream(embedded, 0).server, "upstream.example.test")

    def test_unknown_top_levels_and_executable_sections_reject(self):
        for section in ("script", "scripts", "include", "plugin", "unknown", "global-client-fingerprint"):
            for fmt, key, node in (("v2ray-json", "outbounds", xray()), ("clash-yaml", "proxies", clash())):
                document = {key: [node], section: "private-fixture"}
                value = json.dumps(document) if fmt == "v2ray-json" else as_yaml(document)
                with self.subTest(section=section, fmt=fmt):
                    self.reject_input(value, "UNKNOWN_FIELD", fmt)

    def test_unknown_selected_fields_and_material_paths_are_not_dropped(self):
        for key in ("mux", "sendThrough", "proxySettings", "detour", "privateKey", "certificateFile", "extra"):
            node = xray()
            node[key] = "private-fixture"
            self.unsupported(json.dumps(node), "UNKNOWN_FIELD")
        for key in ("dialer-proxy", "smux", "packet-encoding", "ws-opts", "grpc-opts", "plugin", "certificate", "encryption"):
            node = clash()
            node[key] = "private-fixture"
            expected = "UDP_CAPABILITY_NOT_QUALIFIED" if key == "packet-encoding" else "UNKNOWN_FIELD"
            self.unsupported(as_yaml(node), expected)
        for key in ("allowInsecure", "insecure", "path", "host", "headerType", "serviceName", "spx", "ech", "privateKey"):
            self.unsupported(uri(**{key: "private-fixture"}), "UNKNOWN_FIELD")

    def test_json_nested_allowlists_are_strict_even_for_empty_unknown_values(self):
        for path, key in ((["settings"], "servers"), (["settings", "vnext", 0], "extra"),
                          (["settings", "vnext", 0, "users", 0], "email"), (["streamSettings"], "sockopt"),
                          (["streamSettings"], "tcpSettings"), (["streamSettings", "tlsSettings"], "certificates")):
            node = xray()
            target = node
            for component in path:
                target = target[component]
            target[key] = {}
            self.unsupported(json.dumps(node), "UNKNOWN_FIELD")
        node = xray(True)
        node["streamSettings"]["realitySettings"]["spiderX"] = ""
        self.unsupported(json.dumps(node), "UNKNOWN_FIELD")

    def test_multiple_servers_or_users_reject_without_silent_selection(self):
        for values in ([], [None], None, {}, [xray()["settings"]["vnext"][0]] * 2):
            node = xray()
            node["settings"]["vnext"] = values
            self.unsupported(json.dumps(node))
        for users in ([], None, {}, [None], [{"id": UUID}, {"id": UUID}]):
            node = xray()
            node["settings"]["vnext"][0]["users"] = users
            self.unsupported(json.dumps(node))

    def test_protocols_and_transport_matrix_remain_unsupported(self):
        for protocol in ("vmess", "trojan", "ss", "hysteria2", "tuic", "freedom", "blackhole"):
            node = xray()
            node["protocol"] = protocol
            self.unsupported(json.dumps(node), "UNSUPPORTED_PROTOCOL")
        for protocol in ("vmess", "trojan", "ss", "hysteria2", "tuic"):
            self.unsupported(protocol + "://dummy@example.test:443", "UNSUPPORTED_PROTOCOL")
        for network in ("ws", "grpc", "raw", "xhttp", "httpupgrade", "kcp", "http", "quic"):
            self.unsupported(uri(type=network), "UNSUPPORTED_TRANSPORT")
            node = xray()
            node["streamSettings"]["network"] = network
            self.unsupported(json.dumps(node), "UNSUPPORTED_TRANSPORT")
            node = clash()
            node["network"] = network
            self.unsupported(as_yaml(node), "UNSUPPORTED_TRANSPORT")

    def test_insecure_and_udp_enabled_or_coerced_values_reject(self):
        for value in (True, None, "false", 0, 1):
            node = xray()
            node["streamSettings"]["tlsSettings"]["allowInsecure"] = value
            self.unsupported(json.dumps(node), "UNSUPPORTED_OPTION")
            for key in ("skip-cert-verify", "udp"):
                node = clash()
                node[key] = value
                expected = "UDP_CAPABILITY_NOT_QUALIFIED" if key == "udp" and value is True else "UNSUPPORTED_OPTION"
                self.unsupported(as_yaml(node), expected)
        for value in (False, "true", 1, None):
            node = clash()
            node["tls"] = value
            self.unsupported(as_yaml(node), "UNSUPPORTED_SECURITY")

    def test_conflicting_tls_and_reality_are_rejected(self):
        for reality in (False, True):
            node = xray(reality)
            node["streamSettings"]["tlsSettings" if reality else "realitySettings"] = {}
            self.unsupported(json.dumps(node), "CONFLICTING_SECURITY")
        self.unsupported(uri(pbk=PUBLIC_KEY, sid=SHORT_ID), "CONFLICTING_SECURITY")
        node = clash()
        node["reality-opts"] = {}
        self.unsupported(as_yaml(node), "MISSING_FIELD")

    def test_required_sni_security_and_credentials_are_not_guessed(self):
        for query in ("sni=tls.example.test", "security=tls", "security=tls&sni="):
            value = f"vless://{UUID}@upstream.example.test:443?" + query
            self.unsupported(value)
        for path, key in (([], "settings"), ([], "streamSettings"), (["streamSettings"], "security"),
                          (["streamSettings", "tlsSettings"], "serverName"),
                          (["settings", "vnext", 0, "users", 0], "id")):
            node = xray()
            target = node
            for component in path:
                target = target[component]
            del target[key]
            self.unsupported(json.dumps(node), "MISSING_FIELD")
        for key in ("tls", "servername", "uuid"):
            node = clash()
            del node[key]
            self.unsupported(as_yaml(node), "MISSING_FIELD")

    def test_documented_defaults_are_the_same_across_all_formats(self):
        minimal_uri = f"vless://{UUID}@upstream.example.test:443?security=tls&sni=tls.example.test"
        node = xray()
        del node["tag"]
        del node["streamSettings"]["network"]
        node["settings"]["vnext"][0]["users"][0] = {"id": UUID}
        proxy = clash()
        for key in ("name", "network", "flow", "udp", "skip-cert-verify"):
            del proxy[key]
        self.assertEqual(select_upstream(minimal_uri, 0), select_upstream(json.dumps(node), 0))
        self.assertEqual(select_upstream(minimal_uri, 0), select_upstream(as_yaml(proxy), 0))
        self.assertEqual(select_upstream(minimal_uri, 0).name, "Imported upstream")

    def test_explicit_null_alpn_is_never_defaulted(self):
        node = xray()
        node["streamSettings"]["tlsSettings"]["alpn"] = None
        self.unsupported(json.dumps(node), "UNSUPPORTED_ALPN")
        node = clash()
        node["alpn"] = None
        self.unsupported(as_yaml(node), "UNSUPPORTED_ALPN")

    def test_tls_alpn_and_fingerprint_are_preserved_in_order(self):
        for alpn in (["h2"], ["http/1.1"], ["h2", "http/1.1"], ["http/1.1", "h2"]):
            expected = select_upstream(uri(alpn=",".join(alpn), fp="chrome"), 0)
            node = xray()
            node["streamSettings"]["tlsSettings"].update(alpn=alpn, fingerprint="chrome")
            proxy = clash()
            proxy.update(alpn=alpn, **{"client-fingerprint": "chrome"})
            self.assertEqual(select_upstream(json.dumps(node), 0), expected)
            self.assertEqual(select_upstream(as_yaml(proxy), 0), expected)

    def test_reality_requires_exact_contract_in_every_format(self):
        for change, code in (({"flow": ""}, "UNSUPPORTED_FLOW"), ({"flow": "xtls-rprx-vision-udp443"}, "UNSUPPORTED_FLOW"),
                             ({"fp": ""}, "UNSUPPORTED_FINGERPRINT"), ({"pbk": PUBLIC_KEY + "="}, "INVALID_PUBLIC_KEY"),
                             ({"sid": SHORT_ID.upper()}, "INVALID_SHORT_ID"), ({"alpn": "h2"}, "UNSUPPORTED_ALPN")):
            self.unsupported(uri(True, **change), code)
        for key in ("publicKey", "shortId", "fingerprint", "serverName"):
            node = xray(True)
            del node["streamSettings"]["realitySettings"][key]
            self.unsupported(json.dumps(node), "MISSING_FIELD")

    def test_uri_unicode_name_and_bracketed_ipv6(self):
        value = uri().replace("upstream.example.test", "[2001:db8::1]").replace("#Upstream", "#%E6%B5%B7%E5%A4%96")
        profile = select_upstream(value, 0)
        self.assertEqual(profile.name, "海外")
        self.assertEqual(profile.server, "2001:db8::1")
        self.assertEqual(profile.server_port, 443)

    def test_uri_malformed_percent_encoding_and_query_duplicates(self):
        for suffix in ("&sni=other.example.test", "&%73ni=other.example.test", "&type=tcp"):
            self.unsupported(uri().replace("#Upstream", suffix), "DUPLICATE_KEY")
        for value in (uri().replace("#Upstream", "#%XX"), uri().replace("#Upstream", "#%")):
            self.reject_input(value, "INVALID_URI")
        self.unsupported(uri().replace("#Upstream", "#%FF"), "INVALID_URI")
        self.unsupported(uri().replace("#Upstream", "&broken"), "INVALID_URI")

    def test_uri_credentials_authority_and_path_are_strict(self):
        for value in (uri().replace(":443", ""), uri().replace(":443", ":0"), uri().replace(":443", ":65536"),
                      uri().replace(":443", ":0443"), uri().replace(":443", ":true"),
                      uri().replace(UUID + "@", UUID + ":pass@"), uri().replace(UUID + "@", "@"),
                      uri().replace(UUID + "@", UUID + "@other@"), uri().replace(":443?", ":443/path?"),
                      uri().replace("upstream.example.test", "[fe80::1%25eth0]")):
            self.unsupported(value)
        for value in (uri().replace("upstream", "up\nstream"), uri().replace("upstream", "up\tstream")):
            self.reject_input(value, "INVALID_STRING")
        self.unsupported(uri().replace("#Upstream", "#%0Ainjected"), "INVALID_STRING")

    def test_remote_urls_files_and_base64_subscription_are_not_followed(self):
        for value in ("https://example.test/subscription", "http://127.0.0.1:1/private", "file:///etc/passwd", "ftp://example.test"):
            self.reject_input(value, "UNSUPPORTED_FORMAT")
        self.reject_input(base64.b64encode(uri().encode()).decode(), "INVALID_SHAPE")

    def test_json_duplicate_keys_at_every_depth_and_escaped_duplicates(self):
        for value in ('{"outbounds": [], "outbounds": []}', '{"outbounds": [], "out\\u0062ounds": []}',
                      '{"outbounds":[],"dns":{"a":1,"a":2}}',
                      json.dumps(xray()).replace('"port": 443', '"port": 443,"port": 444')):
            self.reject_input(value, "DUPLICATE_KEY")

    def test_json_nan_infinity_overflow_integer_and_trailing_document(self):
        for scalar in ("NaN", "Infinity", "-Infinity", "1e400", "-1e400"):
            self.reject_input('{"outbounds":[],"dns":' + scalar + '}', "NONFINITE_NUMBER")
        for scalar in (str(2**63), str(-2**63 - 1), "9" * 1000):
            self.reject_input('{"outbounds":[],"dns":' + scalar + '}', "INTEGER_LIMIT")
        for scalar in (str(2**63 - 1), str(-2**63), "0.5"):
            self.assertEqual(preview_upstreams('{"outbounds":[],"dns":' + scalar + '}').candidates, ())
        for value in ('{}{}', json.dumps(xray()) + "\n{}", '{"x":1,}', '{"outbounds":[}', '{"outbounds": [] /*comment*/}'):
            self.reject_input(value, "PARSE_SYNTAX")

    def test_yaml_duplicates_anchors_aliases_merge_tags_and_multidocument(self):
        fixtures = (("proxies: []\nproxies: []", "DUPLICATE_KEY"),
                    ('proxies: []\ndns: {"a": 1, "a": 2}', "DUPLICATE_KEY"),
                    ("proxies: &p []", "YAML_REFERENCE"), ("proxies: *p", "YAML_REFERENCE"),
                    ("proxies: []\ndns: {<<: {x: y}}", "YAML_MERGE"),
                    ("proxies: !!seq []", "YAML_TAG"), ("proxies: !custom []", "YAML_TAG"),
                    ("!!python/object/apply:os.system ['echo forbidden']", "YAML_TAG"),
                    ("%YAML 1.1\n---\nproxies: []", "YAML_TAG"),
                    ("proxies: []\n---\nproxies: []", "YAML_MULTIDOCUMENT"),
                    ("---\nproxies: []\n...\n---\nproxies: []", "YAML_MULTIDOCUMENT"))
        for value, code in fixtures:
            with self.subTest(value=value):
                self.reject_input(value, code, "clash-yaml")
        self.assertEqual(preview_upstreams("---\nproxies: []\n...", "clash-yaml").candidates, ())

    def test_yaml_nonfinite_integer_timestamp_and_nonstring_keys_reject(self):
        for scalar in (".nan", ".inf", "-.inf", "+.Inf"):
            self.reject_input("proxies: []\ndns: " + scalar, "NONFINITE_NUMBER", "clash-yaml")
        self.reject_input("proxies: []\ndns: " + str(2**63), "INTEGER_LIMIT", "clash-yaml")
        self.reject_input("proxies: []\ndns: " + "9" * 1000, "NUMBER_LIMIT", "clash-yaml")
        self.reject_input("proxies: []\ndns: 2026-10-15", "UNSUPPORTED_VALUE", "clash-yaml")
        for key in ("1", "true", "[one, two]"):
            self.reject_input("proxies: []\ndns: {" + key + ": value}", "INVALID_SHAPE", "clash-yaml")

    def test_depth_limit_before_object_tree_construction(self):
        for fmt in ("v2ray-json", "clash-yaml"):
            accepted = '{"outbounds":[],"dns":' + '[' * 15 + '0' + ']' * 15 + '}'
            rejected = '{"outbounds":[],"dns":' + '[' * 16 + '0' + ']' * 16 + '}'
            if fmt == "clash-yaml":
                accepted, rejected = accepted.replace("outbounds", "proxies"), rejected.replace("outbounds", "proxies")
            self.assertEqual(preview_upstreams(accepted, fmt).candidates, ())
            self.reject_input(rejected, "DEPTH_LIMIT", fmt)
        with patch.object(importer.json, "loads", wraps=json.loads) as loader:
            with self.assertRaises(UpstreamImportError):
                importer._load_json('[' * 100000 + '0' + ']' * 100000)
            loader.assert_not_called()
        with patch.object(yaml, "load", wraps=yaml.load) as loader:
            with self.assertRaises(UpstreamImportError):
                importer._load_yaml('[' * 100 + '0' + ']' * 100)
            loader.assert_not_called()

    def test_candidate_limit_is_exact_and_checked_before_construction(self):
        for fmt, key in (("v2ray-json", "outbounds"), ("clash-yaml", "proxies")):
            accepted = json.dumps({key: [None] * 128})
            self.assertEqual(len(preview_upstreams(accepted, fmt).candidates), 128)
            self.reject_input(json.dumps({key: [None] * 129}), "CANDIDATE_LIMIT", fmt)
        with patch.object(yaml, "load", wraps=yaml.load) as loader:
            with self.assertRaises(UpstreamImportError) as error:
                importer._load_yaml("proxies: [" + ",".join("{}" for _ in range(129)) + "]")
            self.assertEqual(error.exception.code, "CANDIDATE_LIMIT")
            loader.assert_not_called()

    def test_document_and_uri_byte_limits_are_not_character_limits(self):
        self.reject_input(b" " * (importer.MAX_INPUT_BYTES + 1), "INPUT_LIMIT")
        self.reject_input("中" * (importer.MAX_INPUT_BYTES // 3 + 1), "INPUT_LIMIT")
        # Exactly 1 MiB is accepted as a document after bounded whitespace trimming.
        value = json.dumps(xray()).encode()
        self.assertTrue(preview_upstreams(value + b" " * (importer.MAX_INPUT_BYTES - len(value))).candidates[0].supported)
        self.reject_input(uri() + "x" * importer.MAX_URI_BYTES, "URI_LIMIT")
        # Exact URI size passes the input limit but its excessive name is unsupported.
        value = uri() + "x" * (importer.MAX_URI_BYTES - len(uri().encode()))
        self.unsupported(value, "NAME_LIMIT")

    def test_string_limits_count_decoded_utf8_bytes(self):
        for fmt, key in (("v2ray-json", "outbounds"), ("clash-yaml", "proxies")):
            self.assertEqual(preview_upstreams(json.dumps({key: [], "dns": "x" * 8192}), fmt).candidates, ())
            self.reject_input(json.dumps({key: [], "dns": "x" * 8193}), "STRING_LIMIT", fmt)
            self.reject_input(json.dumps({key: [], "dns": "中" * 2731}), "STRING_LIMIT", fmt)
        self.assertEqual(preview_upstreams('{"outbounds":[],"dns":"' + r'\u0061' * 8192 + '"}').candidates, ())
        self.reject_input('{"outbounds":[],"dns":"' + r'\u0061' * 8193 + '"}', "STRING_LIMIT")

    def test_token_limit_is_bounded_before_object_tree_construction(self):
        for fmt, key in (("v2ray-json", "outbounds"), ("clash-yaml", "proxies")):
            self.reject_input('{"' + key + '":[],"dns":[' + ",".join("0" for _ in range(20000)) + "]}", "TOKEN_LIMIT", fmt)

    def test_utf8_controls_bidi_and_escaped_surrogate_reject(self):
        self.reject_input(b"\xff", "INVALID_UTF8")
        self.reject_input("\ud800", "INVALID_UTF8")
        for value in (None, 1, {}, bytearray(b"{}")):
            self.reject_input(value, "INVALID_INPUT")
        for char in ("\n", "\t", "\x00", "\x7f", "\x85", "\u2028", "\u202e", "\u200b", "\ufeff", "\ud800"):
            for fmt, key in (("v2ray-json", "outbounds"), ("clash-yaml", "proxies")):
                self.reject_input(json.dumps({key: [], "dns": "a" + char + "b"}), "INVALID_STRING", fmt)
        for empty in ("", " \r\n\t"):
            self.reject_input(empty, "EMPTY_INPUT")

    def test_top_level_shapes_empty_containers_and_format_selection(self):
        for value in ("[]", "null", "123", '"literal"'):
            self.reject_input(value, "INVALID_SHAPE", "v2ray-json")
        for value in ('{"outbounds":{}}', '{"outbounds":null}', '{"outbounds":1}'):
            self.reject_input(value, "INVALID_SHAPE")
        self.reject_input("{}", "MISSING_CANDIDATES")
        for fmt in ("v2ray-json", "json", "xray-json"):
            self.assertEqual(preview_upstreams(json.dumps(xray()), fmt).source_format, "v2ray-json")
        for fmt in ("clash-yaml", "yaml"):
            self.assertEqual(preview_upstreams(as_yaml(clash()), fmt).source_format, "clash-yaml")
        for fmt in (None, [], "toml", "JSON"):
            self.reject_input(uri(), "UNSUPPORTED_FORMAT", fmt)

    def test_process_deadline_resource_limit_and_reap(self):
        processes = []
        real_popen = subprocess.Popen
        def recording(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process
        with patch.object(importer.subprocess, "Popen", side_effect=recording), patch.object(importer, "PARSER_TIMEOUT_SECONDS", 0.15):
            with self.assertRaises(UpstreamImportError) as caught:
                importer._bounded_worker_io([sys.executable, "-I", "-c", "import time; time.sleep(30)"], b"")
        self.assertEqual(caught.exception.code, "PARSE_TIMEOUT")
        self.assertIsNotNone(processes[0].returncode)
        self.assertTrue(processes[0].stdin.closed)
        self.assertTrue(processes[0].stdout.closed)

    def test_parent_caps_output_before_json_parsing_and_reaps_worker(self):
        command = [sys.executable, "-I", "-c", "import sys; sys.stdout.buffer.write(b'x' * 300000); sys.stdout.flush()"]
        with patch.object(importer.json, "loads") as load:
            with self.assertRaises(UpstreamImportError) as caught:
                importer._bounded_worker_io(command, b"")
            self.assertEqual(caught.exception.code, "PARSER_RESOURCE_LIMIT")
            load.assert_not_called()

    def test_concurrent_parsers_are_limited_and_slots_recover_after_failure(self):
        acquired = [importer._PARSER_SLOTS.acquire(blocking=False) for _ in range(importer.MAX_CONCURRENT_PARSERS)]
        self.assertTrue(all(acquired))
        try:
            with patch.object(importer.subprocess, "Popen") as popen:
                self.reject_input(uri(), "PARSER_BUSY")
                popen.assert_not_called()
        finally:
            for acquired_slot in acquired:
                if acquired_slot:
                    importer._PARSER_SLOTS.release()
        with patch.object(importer, "_bounded_worker_io", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                preview_upstreams(uri())
        self.assertTrue(preview_upstreams(uri()).candidates[0].supported)

    def test_resource_caps_are_installed_and_failure_is_closed(self):
        import resource
        with patch.object(resource, "setrlimit") as setter:
            importer._set_worker_limits()
        self.assertEqual(setter.call_args_list[0].args, (resource.RLIMIT_AS, (128 * 1024 * 1024,) * 2))
        self.assertEqual(setter.call_args_list[1].args, (resource.RLIMIT_CPU, (2, 2)))
        with patch.object(resource, "setrlimit", side_effect=OSError):
            with self.assertRaises(UpstreamImportError) as caught:
                importer._set_worker_limits()
        self.assertEqual(caught.exception.code, "PARSER_LIMIT_UNAVAILABLE")

    def test_process_errors_do_not_expose_input_or_stderr_and_do_not_fallback(self):
        with patch.object(importer, "_bounded_worker_io", side_effect=OSError("private detail")):
            # OS errors belong to the bounded I/O helper; no in-process parse fallback exists.
            with self.assertRaises(OSError):
                preview_upstreams(uri())
        with patch.object(importer.subprocess, "Popen", side_effect=OSError(UUID)):
            self.reject_input(uri(), "PARSER_UNAVAILABLE")
        for output in (b"not-json", b"[]", b'{"ok":true}', b'{"ok":"yes"}', b'{"ok":false,"code":1}'):
            with patch.object(importer, "_bounded_worker_io", return_value=output):
                self.reject_input(uri(), "PARSER_FAILED")

    def test_yaml_long_digit_prefixed_names_are_not_mistaken_for_numbers(self):
        proxy = clash()
        proxy["name"] = "1" + "x" * 127
        self.assertEqual(select_upstream(as_yaml(proxy), 0).name, proxy["name"])

    def test_rejected_candidates_still_have_safe_host_and_name_summaries(self):
        for value in (json.dumps(dict(xray(), extra=True)), as_yaml(dict(clash(), extra=True)), uri(extra="true")):
            preview = self.unsupported(value, "UNKNOWN_FIELD")
            self.assertEqual(preview.candidates[0].name, "Upstream")
            self.assertEqual(preview.candidates[0].server, "upstream.example.test")
            self.assertEqual(preview.candidates[0].server_port, 443)

    def test_cancellation_kills_and_reaps_the_already_started_child(self):
        processes = []
        real_popen = subprocess.Popen
        def recording(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process
        with patch.object(importer.subprocess, "Popen", side_effect=recording):
            with patch.object(importer.selectors.DefaultSelector, "select", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    importer._bounded_worker_io([sys.executable, "-I", "-c", "import time; time.sleep(30)"], b"")
        self.assertIsNotNone(processes[0].returncode)
        self.assertTrue(processes[0].stdin.closed)
        self.assertTrue(processes[0].stdout.closed)

    def test_malicious_yaml_does_not_execute_script_or_create_file(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "must-not-exist"
            payload = "!!python/object/apply:os.system [\"touch " + str(marker) + "\"]"
            self.reject_input(payload, "YAML_TAG", "clash-yaml")
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
