"""v0.4.3 bounded VLESS/WS export and shared visual parameter validation."""
import base64
from copy import deepcopy
import json
import unittest
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException
import yaml

from app.services.core_manager import SingBoxAdapter
from app.services.protocol_profiles import compile_profile, decompile_profile
from app.services.validated_export import (
    ExportError, base64_subscription, share_link, singbox_client_config, validated_node,
)
from app.services.mihomo_subscription import mihomo_config
from test_validated_export import vless_node


def ws_node(host="cdn.example.test", path="/vui-ws"):
    item = vless_node(tag="ws-test")
    item.stream_settings["transport"] = {"type": "ws", "path": path}
    if host:
        item.stream_settings["transport"]["headers"] = {"Host": host}
    return item


class VlessWebSocketProfileTests(unittest.TestCase):
    def test_three_public_formats_preserve_ws_tls_without_server_material(self):
        for host in ("", "cdn.example.test"):
            with self.subTest(host=host):
                item = ws_node(host=host)
                item.stream_settings["tls"]["alpn"] = ["http/1.1"]
                item.stream_settings["_vui"] = {"client_fingerprint": "chrome"}
                before = deepcopy(item.__dict__)
                proxy = yaml.safe_load(mihomo_config([item], "2001:db8::1"))["proxies"][0]
                self.assertEqual(proxy["network"], "ws")
                self.assertFalse(proxy["udp"])
                self.assertFalse(proxy["skip-cert-verify"])
                self.assertEqual(proxy["ws-opts"]["path"], "/vui-ws")
                self.assertEqual(proxy["ws-opts"].get("headers", {}).get("Host", ""), host)
                client = singbox_client_config([item], "2001:db8::1")["outbounds"][0]
                self.assertEqual(client["network"], "tcp")
                self.assertEqual(client["transport"], item.stream_settings["transport"])
                self.assertEqual(client["tls"]["server_name"], "vpn.example.test")
                self.assertEqual(client["tls"]["alpn"], ["http/1.1"])
                self.assertEqual(client["tls"]["utls"]["fingerprint"], "chrome")
                raw = base64.b64decode(base64_subscription([item], "2001:db8::1")).decode()
                parsed = urlsplit(raw)
                query = parse_qs(parsed.query)
                self.assertEqual(parsed.scheme, "vless")
                self.assertEqual(parsed.hostname, "2001:db8::1")
                self.assertEqual(query["type"], ["ws"])
                self.assertEqual(query["path"], ["/vui-ws"])
                self.assertEqual(query.get("host", [""]), [host])
                self.assertEqual(query["sni"], ["vpn.example.test"])
                self.assertEqual(query["security"], ["tls"])
                self.assertEqual(query["encryption"], ["none"])
                self.assertEqual(query["alpn"], ["http/1.1"])
                self.assertEqual(query["fp"], ["chrome"])
                for output in (json.dumps(proxy), json.dumps(client), raw):
                    self.assertNotIn("/private/server", output)
                    self.assertNotIn("certificate_path", output)
                    self.assertNotIn("key_path", output)
                    self.assertNotIn("PRIVATE KEY", output)
                self.assertEqual(item.__dict__, before)

    def test_visual_roundtrip_keeps_uuid_and_client_host_out_of_server_headers(self):
        item = ws_node()
        profile = decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)
        self.assertEqual(profile["transport"], "ws")
        self.assertEqual(profile["host"], "cdn.example.test")
        self.assertNotIn(item.settings["users"][0]["uuid"], json.dumps(profile))
        profile.update(path="/edited/path-2", host="other.example.test")
        settings, stream = compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
        self.assertEqual(settings, item.settings)
        self.assertEqual(stream["transport"], {
            "type": "ws", "path": "/edited/path-2", "headers": {"Host": "other.example.test"},
        })
        item.settings, item.stream_settings = settings, stream
        original = deepcopy(stream)
        server = SingBoxAdapter().build_config([item])["inbounds"][0]
        self.assertEqual(server["transport"], {"type": "ws", "path": "/edited/path-2"})
        self.assertEqual(item.stream_settings, original)
        self.assertNotIn("_vui", server)
        self.assertEqual(validated_node(item, "vpn.example.test")["ws-opts"]["headers"], {"Host": "other.example.test"})

    def test_bad_path_and_host_fail_in_both_visual_compiler_and_every_export(self):
        invalid = {
            "path": ("", "relative", "/query?x=1", "/fragment#x", "/encoded%2Fpath", "/with space", "/中文", "/a\r\nb", "/a\\b", "/" + "a" * 256, "/../x", "/./x", 7, None),
            "host": ("https://cdn.example.test", "cdn.example.test:443", "cdn.example.test/x", "a\r\nb", "中文.test", "with space", " leading.test", "trailing.test ", "a" * 64 + ".test", "a..test", "-a.test", "a-.test", "a.test.", 7, None),
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    item = ws_node()
                    profile = decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)
                    profile[field] = value
                    with self.assertRaises(HTTPException) as rejected:
                        compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
                    self.assertEqual(rejected.exception.status_code, 422)
                    if field == "path": item.stream_settings["transport"]["path"] = value
                    else: item.stream_settings["transport"]["headers"]["Host"] = value
                    for exporter in (share_link, mihomo_config, singbox_client_config):
                        with self.assertRaises(ExportError):
                            exporter(item if exporter is share_link else [item], "vpn.example.test")

    def test_unknown_transport_fields_headers_and_unverified_tls_fail_closed(self):
        transports = [
            {"type": "ws"}, {"type": "ws", "path": "/", "max_early_data": 0},
            {"type": "ws", "path": "/", "early_data_header_name": "Sec-WebSocket-Protocol"},
            {"type": "ws", "path": "/", "headers": {"X-Token": "secret"}},
            {"type": "ws", "path": "/", "headers": {"host": "cdn.example.test"}},
            {"type": "ws", "path": "/", "headers": {"Host": ""}},
            {"type": "ws", "path": "/", "headers": None},
            {"type": "grpc", "service_name": "not-supported"},
        ]
        candidates = []
        for transport in transports:
            item = ws_node(); item.stream_settings["transport"] = transport; candidates.append(item)
        for change in ({"core": "xray"}, {"protocol": "vmess"}, {"protocol": "trojan"}):
            item = ws_node(); item.__dict__.update(change); candidates.append(item)
        item = ws_node(); item.settings["users"][0]["flow"] = "xtls-rprx-vision"; candidates.append(item)
        item = ws_node(); item.settings["users"] *= 2; candidates.append(item)
        item = ws_node(); item.stream_settings["tls"]["enabled"] = False; candidates.append(item)
        item = ws_node(); item.stream_settings["_vui"] = {"skip_cert_verify": True}; candidates.append(item)
        for alpn in (["h2"], ["h2", "http/1.1"], [], "http/1.1"):
            item = ws_node(); item.stream_settings["tls"]["alpn"] = alpn; candidates.append(item)
        for item in candidates:
            with self.subTest(item=item.__dict__), self.assertRaises(ExportError):
                validated_node(item, "vpn.example.test")

    def test_visual_edit_preserves_supported_alpn_and_rejects_unrepresented_options(self):
        item = ws_node()
        item.stream_settings['tls']['alpn'] = ['http/1.1']
        profile = decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)
        _, stream = compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
        self.assertEqual(stream['tls']['alpn'], ['http/1.1'])
        for extra in ({'max_early_data': 0}, {'early_data_header_name': ''}, {'headers': {}}):
            with self.subTest(extra=extra), self.assertRaises(HTTPException):
                compile_profile(item.core, item.protocol, {**profile, **extra}, item.settings, item.stream_settings)
        for extra in ({'max_early_data': 0}, {'headers': {'X-Secret': 'not-dropped'}}):
            item = ws_node(); item.stream_settings['transport'].update(extra)
            with self.subTest(extra=extra), self.assertRaises(HTTPException):
                compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
        for target, extra in (('tls', {'alpn': ['h2']}), ('tls', {'min_version': '1.3'}),
                              ('tls', {'reality': {'enabled': False, 'extra': 'draft-option'}}),
                              ('tls', {'reality': {'enabled': 'false'}}),
                              ('_vui', {'unknown': 'not-dropped'})):
            item = ws_node(); item.stream_settings.setdefault(target, {}).update(extra)
            with self.subTest(target=target, extra=extra), self.assertRaises(HTTPException):
                compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
        item = ws_node()
        with self.assertRaises(HTTPException):
            compile_profile(item.core, item.protocol, {**profile, 'transport': 'WS', 'path': '/bad?ed=1'}, item.settings, item.stream_settings)

    def test_invalid_imported_path_needs_explicit_correction_and_flow_is_typed(self):
        for path in ('', None, False, 0, [], {}):
            item = ws_node(); item.stream_settings['transport']['path'] = path
            profile = decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)
            self.assertEqual(profile['path'], path)
            with self.assertRaises(HTTPException):
                compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
            with self.assertRaises(ExportError):
                validated_node(item, 'vpn.example.test')
        for headers in ('bad', [], None, 7):
            item = ws_node(); item.stream_settings['transport']['headers'] = headers
            profile = decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)
            with self.assertRaises(HTTPException):
                compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
            with self.assertRaises(ExportError):
                validated_node(item, 'vpn.example.test')
            # Preserve invalid imports for the real core checker; never silently
            # turn them into an empty valid header block or crash in the adapter.
            self.assertEqual(SingBoxAdapter().build_config([item])['inbounds'][0]['transport']['headers'], headers)
        item = ws_node(); item.stream_settings['transport'].pop('path')
        profile = decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)
        self.assertEqual(profile['path'], '')
        with self.assertRaises(HTTPException):
            compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
        profile['path'] = '/corrected'
        _, stream = compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
        self.assertEqual(stream['transport']['path'], '/corrected')
        for host in (None, False, 0, [], {}, 7, ''):
            item = ws_node(); item.stream_settings['transport']['headers']['Host'] = host
            profile = decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)
            self.assertEqual(profile['host'], None if host == '' else host)
            with self.subTest(host=host), self.assertRaises(HTTPException):
                compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
            # Deliberately clearing the visual Host removes an invalid imported
            # Host; an untouched form must not make that change silently.
            profile['host'] = ''
            _, stream = compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
            self.assertNotIn('headers', stream['transport'])
        for flow in (None, False, 0, [], {}):
            item = ws_node(); item.settings['users'][0]['flow'] = flow
            profile = decompile_profile(item.core, item.protocol, item.settings, item.stream_settings)
            self.assertEqual(profile['flow'], flow)
            with self.subTest(flow=flow), self.assertRaises(HTTPException):
                compile_profile(item.core, item.protocol, profile, item.settings, item.stream_settings)
        for flow in (None, False, 0, [], {}, 'xtls-rprx-vision'):
            item = ws_node(); item.settings['users'][0]['flow'] = flow
            with self.subTest(flow=flow), self.assertRaises(ExportError):
                validated_node(item, 'vpn.example.test')

    def test_root_and_max_path_are_preserved_exactly(self):
        for path in ("/", "/a.b_c-d~e/path", "/" + "a" * 255):
            self.assertEqual(validated_node(ws_node(path=path), "vpn.example.test")["ws-opts"]["path"], path)


if __name__ == "__main__":
    unittest.main()
