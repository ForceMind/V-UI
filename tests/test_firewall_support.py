import unittest
from unittest.mock import patch
from types import SimpleNamespace
from scripts import firewall_support as f

class FirewallSupportTests(unittest.TestCase):
    def result(self,code=0,out=""):return SimpleNamespace(returncode=code,stdout=out,stderr="")
    def test_ufw_detect_and_query(self):
        def which(name):return "/usr/sbin/"+name if name=="ufw" else None
        with patch.object(f.shutil,"which",side_effect=which),patch.object(f,"run",return_value=self.result(out="Status: active\n80/tcp ALLOW Anywhere\n")):
            info=f.detect();self.assertEqual(info["backend"],"ufw");self.assertTrue(f.port_open(info,80))
    def test_firewalld_detect_and_query(self):
        def which(name):return "/usr/bin/"+name if name=="firewall-cmd" else None
        def run(args):
            if "--state" in args:return self.result(out="running\n")
            if "--get-default-zone" in args:return self.result(out="public\n")
            if "--query-port" in args:return self.result(0,"yes\n")
            return self.result()
        with patch.object(f.shutil,"which",side_effect=which),patch.object(f,"run",side_effect=run):
            info=f.detect();self.assertEqual(info["zone"],"public");self.assertTrue(f.port_open(info,8443))
    def test_custom_nftables_is_never_auto_classified_open(self):
        def which(name):return "/usr/sbin/nft" if name=="nft" else None
        with patch.object(f.shutil,"which",side_effect=which),patch.object(f,"run",return_value=self.result(out="table inet filter {}")):
            info=f.detect();self.assertEqual(info["backend"],"nftables");self.assertIsNone(f.port_open(info,80))
    def test_open_ufw_runs_only_requested_ports(self):
        calls=[]
        with patch.object(f.subprocess,"run",side_effect=lambda args:(calls.append(args) or self.result())):
            f.open_ports({"backend":"ufw"},[8443,80,8443])
        self.assertEqual(calls,[["ufw","allow","80/tcp"],["ufw","allow","8443/tcp"]])

if __name__=="__main__":unittest.main()
