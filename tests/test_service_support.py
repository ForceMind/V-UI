import hashlib
import unittest
from scripts import service_support as s

class ServiceSupportTests(unittest.TestCase):
    def config(self,manager):
        return {"service_manager":manager,"bootstrap_python":"/usr/bin/python3","ipv6":True}
    def test_systemd_has_socket_activation_and_nonroot_panel(self):
        values=s.service_files(self.config("systemd"))
        text="\n".join(v[0] for v in values.values())
        self.assertIn("User=v-ui",text);self.assertIn("ListenStream=0.0.0.0:80",text)
        self.assertIn("ListenStream=[::]:80",text);self.assertNotIn("User=root",text)
        self.assertNotIn("cap_net_bind_service",text)
    def test_only_panel_can_open_netlink_without_additional_privileges(self):
        values=s.service_files(self.config("systemd"))
        for name,families in (("v-ui.service","AF_INET AF_INET6 AF_UNIX AF_NETLINK"),
                              ("v-ui-http01.service","AF_INET AF_INET6 AF_UNIX")):
            text,mode=values[s.SYSTEMD_DIR/name]
            with self.subTest(unit=name):
                lines=text.splitlines()
                for key,value in (("RestrictAddressFamilies",families),("User","v-ui"),
                                  ("Group","v-ui"),("NoNewPrivileges","true"),("CapabilityBoundingSet","")):
                    self.assertEqual([line for line in lines if line.startswith(key+"=")],[key+"="+value])
                self.assertNotIn("AmbientCapabilities=",text)
                self.assertEqual(mode,0o644)

    def test_http01_unit_bytes_and_permissions_remain_frozen(self):
        # Pre-fix 0b6e2d8 output, fixed interpreter, both supported socket modes.
        challenge="e61db54d31a5d10351938116ea64a68d675f5a0f6ab65f31a6d2b630acfa2bd0"
        sockets={False:"e47c40234ffaefda460a81cb19aacfc5e09aa79a18062d126c89e47d33080a69",
                 True:"8c944190d1e06e5a4fb75f93e98f71dbc37ea9d4001b392f40e88eb6831455bf"}
        for ipv6,socket_hash in sockets.items():
            values=s.service_files({**self.config("systemd"),"ipv6":ipv6})
            for name,expected in (("v-ui-http01.service",challenge),("v-ui-http01.socket",socket_hash)):
                with self.subTest(ipv6=ipv6,unit=name):
                    text,mode=values[s.SYSTEMD_DIR/name]
                    self.assertEqual(hashlib.sha256(text.encode()).hexdigest(),expected)
                    self.assertEqual(mode,0o644)

    def test_openrc_uses_ambient_capability_only_for_challenge(self):
        values=s.service_files(self.config("openrc"))
        panel=values[s.OPENRC_DIR/"v-ui"][0];challenge=values[s.OPENRC_DIR/"v-ui-http01"][0]
        self.assertIn('command_user="v-ui:v-ui"',panel)
        self.assertNotIn("cap_net_bind_service",panel)
        self.assertIn('capabilities="^cap_net_bind_service"',challenge)
        self.assertIn("http01-direct",challenge)
        self.assertEqual(values[s.OPENRC_DIR/"v-ui"][1],0o755)

if __name__=="__main__":unittest.main()
