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


class FirewallProtocolTests(unittest.TestCase):
    def result(self, code=0, out=''):
        return SimpleNamespace(returncode=code, stdout=out, stderr='')

    def test_ufw_never_confuses_tcp_udp_or_partial_port_numbers(self):
        output = 'Status: active\n10443/tcp ALLOW Anywhere\n20443/udp ALLOW Anywhere\n'
        with patch.object(f, 'run', return_value=self.result(out=output)):
            self.assertTrue(f.port_open({'backend': 'ufw'}, 10443))
            self.assertFalse(f.port_open({'backend': 'ufw'}, 10443, protocol='udp'))
            self.assertTrue(f.port_open({'backend': 'ufw'}, 20443, protocol='udp'))
            self.assertFalse(f.port_open({'backend': 'ufw'}, 443, protocol='udp'))

    def test_ufw_scoped_rules_and_failed_queries_are_not_proof_of_open_port(self):
        for code, output, expected in (
            (1, 'Status: active\n10443/udp ALLOW Anywhere\n', None),
            (0, 'Status: inactive\n', None),
            (0, 'unexpected output\n', None),
            (0, 'Status: active\n10443/udp ALLOW OUT Anywhere\n', False),
            (0, 'Status: active\n10443/udp ALLOW IN 192.0.2.0/24\n', None),
            (0, 'Status: active\n10443/udp DENY IN Anywhere\n10443/udp ALLOW IN Anywhere\n', None),
            (0, 'Status: active\nAnywhere REJECT IN 192.0.2.1\n10443/udp ALLOW IN Anywhere\n', None),
            (0, 'Status: active\n10443/udp LIMIT IN Anywhere\n', None),
            (0, 'Status: active\n10443/udp on eth0 ALLOW IN Anywhere\n', None),
            (0, 'Status: active\n10440:10450/udp ALLOW IN Anywhere\n', False),
        ):
            with self.subTest(code=code, output=output), \
                 patch.object(f, 'run', return_value=self.result(code, output)):
                self.assertIs(f.port_open({'backend': 'ufw'}, 10443, protocol='udp'), expected)

    def test_ufw_requires_both_families_when_ipv6_rules_are_present(self):
        output = ('Status: active\nTo                         Action      From\n'
                  '--                         ------      ----\n'
                  '10443/udp                  ALLOW IN    Anywhere\n'
                  '80/tcp (v6)                ALLOW IN    Anywhere (v6)\n')
        with patch.object(f, 'run', return_value=self.result(out=output)):
            self.assertFalse(f.port_open({'backend': 'ufw'}, 10443, protocol='udp'))
        output += '10443/udp (v6)             ALLOW IN    Anywhere (v6) # explicit QUIC\n'
        with patch.object(f, 'run', return_value=self.result(out=output)):
            self.assertTrue(f.port_open({'backend': 'ufw'}, 10443, protocol='udp'))

    def test_ufw_udp_open_is_exact_deduplicated_and_never_enables_firewall(self):
        with patch.object(f.subprocess, 'run', return_value=self.result()) as write:
            f.open_ports({'backend': 'ufw'}, [20443, 10443, 20443], protocol='udp')
        self.assertEqual([call.args[0] for call in write.call_args_list], [
            ['ufw', 'allow', '10443/udp'], ['ufw', 'allow', '20443/udp'],
        ])

    def test_firewalld_udp_query_and_writes_use_exact_interface_zone(self):
        info = {'backend': 'firewalld', 'managed': True, 'zone': 'external'}
        with patch.object(f, 'run', return_value=self.result(out='yes\n')) as query:
            self.assertTrue(f.port_open(info, 10443, protocol='udp'))
        query.assert_called_once_with(['firewall-cmd', '--zone', 'external', '--query-port', '10443/udp'])
        with patch.object(f, 'detect', return_value=info), \
             patch.object(f.subprocess, 'run', return_value=self.result()) as write:
            f.open_ports(info, [10443, 10443], protocol='udp')
        self.assertEqual([call.args[0] for call in write.call_args_list], [
            ['firewall-cmd', '--zone', 'external', '--add-port', '10443/udp'],
            ['firewall-cmd', '--zone', 'external', '--permanent', '--add-port', '10443/udp'],
        ])

    def test_udp_ambiguous_or_changed_firewalld_zone_is_never_modified(self):
        info = {'backend': 'firewalld', 'managed': True, 'zone': 'external'}
        for current in ({**info, 'zone': 'internal'}, {'backend': 'firewalld', 'managed': False},
                        {'backend': 'ufw', 'managed': True}):
            with self.subTest(current=current), patch.object(f, 'detect', return_value=current), \
                 patch.object(f.subprocess, 'run') as write:
                with self.assertRaisesRegex(RuntimeError, 'changed'):
                    f.open_ports(info, [10443], protocol='udp')
                write.assert_not_called()
        ambiguous = {'backend': 'firewalld', 'managed': False}
        with patch.object(f, 'run') as query, patch.object(f.subprocess, 'run') as write:
            self.assertIsNone(f.port_open(ambiguous, 10443, protocol='udp'))
            with self.assertRaisesRegex(RuntimeError, 'manual'):
                f.open_ports(ambiguous, [10443], protocol='udp')
            query.assert_not_called()
            write.assert_not_called()

    def test_udp_custom_rules_remain_manual(self):
        for backend in ('nftables', 'iptables'):
            info = {'backend': backend, 'managed': False}
            with self.subTest(backend=backend), patch.object(f, 'run') as query, \
                 patch.object(f.subprocess, 'run') as write:
                self.assertIsNone(f.port_open(info, 10443, protocol='udp'))
                with self.assertRaises(RuntimeError):
                    f.open_ports(info, [10443], protocol='udp')
                query.assert_not_called()
                write.assert_not_called()

    def test_invalid_protocol_or_port_is_rejected_before_any_commands(self):
        with patch.object(f, 'run') as query, patch.object(f.subprocess, 'run') as write:
            for protocol in ('quic', 'TCP', 'udp --force', None):
                with self.subTest(protocol=protocol), self.assertRaises(ValueError):
                    f.port_open({'backend': 'ufw'}, 10443, protocol=protocol)
                with self.subTest(protocol=protocol), self.assertRaises(ValueError):
                    f.open_ports({'backend': 'ufw'}, [10443], protocol=protocol)
            for port in (0, 65536, True, '10443', 10443.5):
                with self.subTest(port=port), self.assertRaises(ValueError):
                    f.port_open({'backend': 'ufw'}, port, protocol='udp')
                with self.subTest(port=port), self.assertRaises(ValueError):
                    f.open_ports({'backend': 'ufw'}, [10443, port], protocol='udp')
            query.assert_not_called()
            write.assert_not_called()

    def test_udp_write_failure_is_explicit_and_stops_further_changes(self):
        for backend in ('ufw', 'firewalld'):
            info = {'backend': backend, 'managed': True, 'zone': 'external'}
            with self.subTest(backend=backend), patch.object(f, 'detect', return_value=info), \
                 patch.object(f.subprocess, 'run', return_value=self.result(1)) as write:
                with self.assertRaisesRegex(RuntimeError, '10443/udp'):
                    f.open_ports(info, [10443, 20443], protocol='udp')
                self.assertEqual(write.call_count, 1)

if __name__=="__main__":unittest.main()
