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
    def test_openrc_uses_ambient_capability_only_for_challenge(self):
        values=s.service_files(self.config("openrc"))
        panel=values[s.OPENRC_DIR/"v-ui"][0];challenge=values[s.OPENRC_DIR/"v-ui-http01"][0]
        self.assertIn('command_user="v-ui:v-ui"',panel)
        self.assertNotIn("cap_net_bind_service",panel)
        self.assertIn('capabilities="^cap_net_bind_service"',challenge)
        self.assertIn("http01-direct",challenge)
        self.assertEqual(values[s.OPENRC_DIR/"v-ui"][1],0o755)

if __name__=="__main__":unittest.main()
