import unittest
from types import SimpleNamespace
import yaml

from app.services.client_export import mihomo_proxy, singbox_client_config
from app.services.core_manager import SingBoxAdapter, XrayAdapter
from app.services.inbound_service import ensure_credentials
from app.services.protocol_profiles import compile_profile


def obj(core, protocol, settings, stream):
    return SimpleNamespace(id=7, core=core, remark="profile-test", port=443,
        protocol=protocol, settings=settings, stream_settings=stream, enable=True, tag="profile-test")


class ProtocolProfileTests(unittest.TestCase):
    def test_xray_pin_uses_clients_and_network(self):
        item = obj("xray", "vless", {"users": [{"id": "11111111-1111-1111-1111-111111111111"}],
            "decryption": "none"}, {"method": "raw", "security": "none"})
        inbound = XrayAdapter().build_config([item])["inbounds"][0]
        self.assertIn("clients", inbound["settings"])
        self.assertNotIn("users", inbound["settings"])
        self.assertEqual(inbound["streamSettings"]["network"], "raw")
        self.assertNotIn("method", inbound["streamSettings"])

    def test_singbox_reality_keeps_private_key_server_only(self):
        settings, stream = compile_profile("sing-box", "vless", {
            "security": "reality", "transport": "direct", "flow": "xtls-rprx-vision",
            "reality_target": "example.com:443", "reality_server_name": "example.com",
            "client_fingerprint": "chrome"}, ensure_credentials("sing-box", "vless", {}), {})
        item = obj("sing-box", "vless", settings, stream)
        server = SingBoxAdapter().build_config([item])["inbounds"][0]
        private_key = server["tls"]["reality"]["private_key"]
        public_key = stream["_vui"]["reality_public_key"]
        self.assertTrue(private_key)
        self.assertTrue(public_key)
        self.assertNotEqual(private_key, public_key)
        self.assertNotIn("_vui", server)
        proxy = mihomo_proxy(item, "vpn.example.com")
        self.assertEqual(proxy["reality-opts"]["public-key"], public_key)
        self.assertNotIn(private_key, yaml.safe_dump(proxy))
        client = singbox_client_config([item], "vpn.example.com")
        self.assertEqual(client["outbounds"][0]["tls"]["reality"]["public_key"], public_key)
        self.assertNotIn(private_key, yaml.safe_dump(client))

    def test_hysteria2_profile_exports_bandwidth_obfs_and_tls(self):
        settings, stream = compile_profile("sing-box", "hysteria2", {
            "security": "tls", "server_name": "hy.example.com", "certificate_path": "/etc/ssl/hy.crt",
            "key_path": "/etc/ssl/hy.key", "up_mbps": 30, "down_mbps": 200,
            "obfs_type": "salamander", "obfs_password": "obfs-secret"},
            ensure_credentials("sing-box", "hysteria2", {}), {})
        item = obj("sing-box", "hysteria2", settings, stream)
        proxy = mihomo_proxy(item, "hy.example.com")
        self.assertEqual(proxy["up"], "30 Mbps")
        self.assertEqual(proxy["down"], "200 Mbps")
        self.assertEqual(proxy["obfs"], "salamander")
        self.assertEqual(proxy["obfs-password"], "obfs-secret")
        self.assertEqual(proxy["sni"], "hy.example.com")
        server = SingBoxAdapter().build_config([item])["inbounds"][0]
        self.assertEqual(server["tls"]["certificate_path"], "/etc/ssl/hy.crt")
        self.assertNotIn("_vui", server)

    def test_tuic_profile_exports_client_preferences(self):
        settings, stream = compile_profile("sing-box", "tuic", {
            "security": "tls", "server_name": "tuic.example.com", "certificate_path": "/etc/ssl/tuic.crt",
            "key_path": "/etc/ssl/tuic.key", "congestion_control": "bbr", "udp_relay_mode": "native",
            "zero_rtt_handshake": True}, ensure_credentials("sing-box", "tuic", {}), {})
        proxy = mihomo_proxy(obj("sing-box", "tuic", settings, stream), "tuic.example.com")
        self.assertEqual(proxy["congestion-controller"], "bbr")
        self.assertEqual(proxy["udp-relay-mode"], "native")
        self.assertTrue(proxy["reduce-rtt"])


if __name__ == "__main__": unittest.main()
