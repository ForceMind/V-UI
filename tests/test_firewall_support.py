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
            if "--get-active-zones" in args:return self.result(out="public\n  interfaces: eth0\n")
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


class FirewalldZoneRegressions(unittest.TestCase):
    def result(self, code=0, out=''):
        return SimpleNamespace(returncode=code, stdout=out, stderr='')

    def detect(self, active, code=0):
        def run(args):
            if '--state' in args: return self.result(out='running\n')
            if '--get-active-zones' in args: return self.result(code, active)
            if '--get-default-zone' in args: return self.result(out='public\n')
            raise AssertionError('Unexpected firewall command: ' + repr(args))
        with patch.object(f.shutil, 'which', side_effect=lambda name: '/usr/bin/firewall-cmd' if name == 'firewall-cmd' else None), \
             patch.object(f, 'run', side_effect=run) as commands:
            info = f.detect()
        return info, commands.call_args_list

    def test_non_default_single_interface_zone_is_used(self):
        info, calls = self.detect('external\n  interfaces: eth0\n')
        self.assertTrue(info['managed'])
        self.assertEqual(info['zone'], 'external')
        self.assertFalse(any('--get-default-zone' in call.args[0] for call in calls))
        with patch.object(f, 'run', return_value=self.result(out='yes\n')) as query:
            self.assertTrue(f.port_open(info, 8443))
        self.assertEqual(query.call_args.args[0], ['firewall-cmd', '--zone', 'external', '--query-port', '8443/tcp'])
        writes = []
        with patch.object(f, 'detect', return_value=info), \
             patch.object(f.subprocess, 'run', side_effect=lambda args: (writes.append(args) or self.result())):
            f.open_ports(info, [8443, 80, 8443])
        self.assertEqual(writes, [
            ['firewall-cmd', '--zone', 'external', '--add-port', '80/tcp'],
            ['firewall-cmd', '--zone', 'external', '--permanent', '--add-port', '80/tcp'],
            ['firewall-cmd', '--zone', 'external', '--add-port', '8443/tcp'],
            ['firewall-cmd', '--zone', 'external', '--permanent', '--add-port', '8443/tcp'],
        ])

    def test_ambiguous_and_failed_detection_require_manual_handling(self):
        states = [('', 0), ('external\n  interfaces: eth0\ninternal\n  interfaces: eth1\n', 0),
                  ('external\n  sources: 192.0.2.0/24\n', 0),
                  ('external\n  interfaces: eth0\n  sources: 192.0.2.0/24\n', 0),
                  ('external\n', 0), ('external\n  interfaces: eth0\n', 1),
                  ('garbled output\n', 0), ('external\n  unknown: eth0\n', 0)]
        for active, code in states:
            with self.subTest(active=active, code=code):
                info, _ = self.detect(active, code)
                self.assertEqual(info['backend'], 'firewalld')
                self.assertFalse(info['managed'])
                self.assertIsNone(info.get('zone'))
                with patch.object(f, 'run') as query, patch.object(f.subprocess, 'run') as write:
                    self.assertIsNone(f.port_open(info, 80))
                    with self.assertRaises(RuntimeError): f.open_ports(info, [80])
                    query.assert_not_called()
                    write.assert_not_called()

    def test_changed_zone_is_not_modified(self):
        info = {'backend': 'firewalld', 'managed': True, 'zone': 'external'}
        with patch.object(f, 'detect', return_value={**info, 'zone': 'internal'}), \
             patch.object(f.subprocess, 'run') as write:
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                f.open_ports(info, [80])
            write.assert_not_called()

    def test_query_failure_is_unknown_not_closed(self):
        info = {'backend': 'firewalld', 'managed': True, 'zone': 'external'}
        for code, out, expected in ((0, 'yes\n', True), (1, 'no\n', False),
                                    (2, '', None), (0, 'unexpected\n', None)):
            with self.subTest(code=code, out=out), patch.object(f, 'run', return_value=self.result(code, out)):
                self.assertIs(f.port_open(info, 80), expected)

if __name__=="__main__":unittest.main()
