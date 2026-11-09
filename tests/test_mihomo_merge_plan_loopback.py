"""Compare the private YAML plan against fixed stock Mihomo and sing-box in CI."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
from queue import Queue
import socketserver
import subprocess
import tempfile
import unittest
import time

import yaml

from app.services.mihomo_merge import merge_mihomo
from app.services.mihomo_merge_plan import plan_mihomo, render_sing_box_rules
from app.services.proxy_policy import AddressPolicy, _upstream
from app.services.mihomo_plan_policy import DENIED_SET_TAG, protected_yaml_route
from loopback_helpers import DnsAnswers, certificate_files
from proxy_loopback_support import (
    BODY, BoundCoreProcess, ReadyMihomoProcess, https_control, https_target, https_through, listen_port, ordinary_dns_target, serve,
)


@unittest.skipUnless(os.environ.get('VUI_MIHOMO_MERGE_LOOPBACK') == '1', 'dedicated real-core workflow')
class MihomoMergePlanLoopbackTests(unittest.TestCase):
    def start_sing_box(self, stack, binary, config, log, env, port):
        subprocess.run([str(binary), 'check', '-c', str(config)],
                       env=env, check=True, timeout=15, capture_output=True)
        process = BoundCoreProcess(stack, [str(binary), 'run', '-c', str(config)], log, env)
        process.start(port)
        return process

    def test_same_explicit_decisions_and_failed_upstream_cannot_go_direct(self):
        sing_box = Path(os.environ['VUI_TEST_CORES']) / 'sing-box'
        mihomo = Path(os.environ['VUI_TEST_MIHOMO'])
        with ExitStack() as stack:
            directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-yaml-plan-')))
            ca, cert, key = certificate_files(directory, 'yaml-plan')
            target_port, traffic = https_target(stack, '127.0.0.1', cert, key)
            relay_port, relayed = https_target(stack, '127.0.0.2', cert, key)
            denied_port, denied_traffic = https_target(stack, '127.0.0.3', cert, key)
            self.assertEqual(https_control('127.0.0.3', denied_port, ca, 'reject-control'), (200, BODY))
            denied_control = denied_traffic.snapshot()
            attempted = Queue()

            class UnavailableUpstream(socketserver.BaseRequestHandler):
                def handle(self):
                    # Observe the TLS attempt, then reply with invalid TLS.
                    self.request.settimeout(2)
                    attempted.put(self.request.recv(1))
                    self.request.sendall(b'not TLS')

            upstream_port = serve(stack, socketserver.ThreadingTCPServer(
                ('127.0.0.1', 0), UnavailableUpstream))
            empty_roots = directory / 'empty-roots'
            empty_roots.mkdir()
            env = dict(PATH='/usr/bin:/bin', HOME=str(directory),
                       SSL_CERT_FILE=str(ca), SSL_CERT_DIR=str(empty_roots))
            relay_in = listen_port()
            uuid = '11111111-1111-4111-8111-111111111111'
            relay_config = directory / 'relay.json'
            relay_config.write_text(json.dumps({
                'inbounds': [{'type': 'vless', 'listen': '127.0.0.1', 'listen_port': relay_in,
                              'users': [{'uuid': uuid}], 'tls': {'enabled': True,
                              'certificate_path': str(cert), 'key_path': str(key)}}],
                'outbounds': [{'type': 'direct', 'tag': 'relay-egress', 'inet4_bind_address': '127.0.0.4'}],
            }))
            self.start_sing_box(stack, sing_box, relay_config, directory/'relay.log', env, relay_in)
            base_node = {'type': 'vless', 'server': '127.0.0.1', 'uuid': uuid, 'tls': True,
                         'servername': 'vpn.example.test'}
            baseline = yaml.safe_dump({
                'mode': 'rule',
                'proxies': [
                    dict(base_node, name='fixture-upstream', port=relay_in),
                    dict(base_node, name='fixture-invalid-tls', port=upstream_port),
                ],
                'proxy-groups': [{'name': 'Selected', 'type': 'select', 'proxies': ['fixture-upstream']}],
            })
            candidate = yaml.safe_dump({'rules': [
                'DOMAIN,localhost,DIRECT',
                'DOMAIN-SUFFIX,localhost,REJECT',
                'IP-CIDR,127.0.0.1/32,fixture-invalid-tls,no-resolve',
                'IP-CIDR,127.0.0.2/32,Selected,no-resolve',
                'MATCH,REJECT',
            ]})
            plan = plan_mihomo(baseline, candidate, group_members={'Selected': 'fixture-upstream'})
            self.assertEqual(plan.route('localhost'), 'DIRECT')
            self.assertEqual(plan.route('sub.localhost'), 'REJECT')
            self.assertEqual(plan.route('127.0.0.1'), 'fixture-invalid-tls')
            self.assertEqual(plan.route('127.0.0.2'), 'fixture-upstream')

            sing_port, mihomo_port = listen_port(), listen_port()
            self.assertNotEqual(sing_port, mihomo_port)
            route_tags = {'DIRECT': 'fixture-direct', 'fixture-upstream': 'fixture-proxy',
                          'fixture-invalid-tls': 'fixture-invalid'}
            outbounds = [{'type': 'direct', 'tag': route_tags['DIRECT']}]
            for profile in plan.upstreams:
                outbound = _upstream(profile, '127.0.0.1')
                outbound['tag'] = route_tags[profile.name]
                outbounds.append(outbound)
            sing_config = directory / 'sing-box.json'
            sing_config.write_text(json.dumps({
                'log': {'level': 'debug'},
                'inbounds': [{'type': 'mixed', 'tag': 'fixture-in', 'listen': '127.0.0.1',
                              'listen_port': sing_port}],
                'outbounds': outbounds,
                'route': {'rules': render_sing_box_rules(plan, inbound='fixture-in', outbound_tags=route_tags)},
            }))
            self.start_sing_box(stack, sing_box, sing_config, directory/'sing.log', env, sing_port)
            reference = merge_mihomo(baseline, candidate)
            reference.update({'mixed-port': mihomo_port, 'allow-lan': False, 'bind-address': '127.0.0.1'})
            mihomo_config = directory / 'mihomo.yaml'
            mihomo_config.write_text(yaml.safe_dump(reference))
            mihomo_home = directory / 'mihomo'
            mihomo_home.mkdir()
            oracle = ReadyMihomoProcess(stack, [str(mihomo), '-d', str(mihomo_home), '-f', str(mihomo_config)],
                                        directory/'mihomo.log', env)
            oracle.start(mihomo_port)

            for name, port in (('sing-box', sing_port), ('mihomo', mihomo_port)):
                with self.subTest(core=name):
                    marker = name + '-direct'
                    self.assertEqual(https_through(port, 'localhost', target_port, ca, marker,
                                                   protocol='socks5h'), (200, BODY))
                    self.assertEqual(traffic.snapshot()[-1][2], '/' + marker)
                    delivered = traffic.snapshot()
                    relay_marker = name + '-relay'
                    self.assertEqual(https_through(port, '127.0.0.2', relay_port, ca, relay_marker,
                                                   protocol='socks5h'), (200, BODY))
                    self.assertEqual((relayed.snapshot()[-1][0], relayed.snapshot()[-1][2]),
                                     ('127.0.0.4', '/' + relay_marker))
                    self.assertTrue(attempted.empty(), 'Unexpected earlier TLS attempt')
                    with self.assertRaises(OSError) as failed:
                        https_through(port, '127.0.0.1', target_port, ca, name+'-upstream', protocol='socks5h')
                    self.assertNotIsInstance(failed.exception, TimeoutError)
                    self.assertEqual(attempted.get(timeout=2), b'\x16')
                    with self.assertRaises(OSError) as denied:
                        https_through(port, '127.0.0.3', denied_port, ca, name+'-final', protocol='socks5h')
                    self.assertNotIsInstance(denied.exception, TimeoutError)
                    self.assertEqual(denied_traffic.snapshot(), denied_control)
                    self.assertEqual(traffic.snapshot(), delivered)
                    recovery_marker = name + '-direct-after-denials'
                    self.assertEqual(https_through(port, 'localhost', target_port, ca, recovery_marker,
                                                   protocol='socks5h'), (200, BODY))
                    self.assertEqual(traffic.snapshot()[:-1], delivered)
                    self.assertEqual(traffic.snapshot()[-1][2], '/' + recovery_marker)

    def test_protected_dns_does_not_change_prior_no_resolve_selection(self):
        sing_box = Path(os.environ['VUI_TEST_CORES']) / 'sing-box'
        mihomo = Path(os.environ['VUI_TEST_MIHOMO'])
        with ExitStack() as stack:
            directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-yaml-guard-')))
            ca, cert, key = certificate_files(directory, 'yaml-guard')
            target_port, delivered = https_target(stack, '127.0.0.1', cert, key)
            blocked_port, blocked = https_target(stack, '127.0.0.2', cert, key)
            self.assertEqual(https_control('127.0.0.2', blocked_port, ca, 'reachable-control'), (200, BODY))
            blocked_before = blocked.snapshot()
            answers = DnsAnswers('yaml-guard', {'match.example.test': '127.0.0.1',
                                               'blocked.example.test': '127.0.0.2'})
            dns_port = ordinary_dns_target(stack, answers)
            candidate = yaml.safe_dump({'rules': [
                'IP-CIDR,127.0.0.1/32,REJECT,no-resolve',
                'DOMAIN,match.example.test,DIRECT',
                'DOMAIN,blocked.example.test,DIRECT',
                'MATCH,REJECT',
            ]})
            plan = plan_mihomo('mode: rule', candidate)
            self.assertEqual(plan.route('match.example.test'), 'DIRECT')
            self.assertEqual(plan.route('127.0.0.1'), 'REJECT')
            empty_roots = directory/'empty-roots'
            empty_roots.mkdir()
            env = dict(PATH='/usr/bin:/bin', HOME=str(directory), SSL_CERT_FILE=str(ca),
                       SSL_CERT_DIR=str(empty_roots))
            sing_port, mihomo_port = listen_port(), listen_port()
            self.assertNotEqual(sing_port, mihomo_port)
            policy = AddressPolicy._for_tests(allowed_addresses=('127.0.0.1',))
            route = protected_yaml_route(plan, inbounds=('guarded',), outbound_tags={'DIRECT': 'direct-out'},
                                         dns_tag='fixture-dns', self_addresses=('127.0.0.9',),
                                         upstream_addresses={}, policy=policy)
            route['default_domain_resolver'] = 'fixture-dns'
            sing_config = directory/'sing-box.json'
            sing_config.write_text(json.dumps({
                'log': {'level': 'debug'},
                'inbounds': [{'type': 'mixed', 'tag': 'guarded', 'listen': '127.0.0.1', 'listen_port': sing_port}],
                'outbounds': [{'type': 'direct', 'tag': 'direct-out'}],
                'dns': {'servers': [{'type': 'udp', 'tag': 'fixture-dns', 'server': '127.0.0.1',
                                    'server_port': dns_port}]},
                'route': route,
            }))
            sing_log = directory/'sing.log'
            actual = self.start_sing_box(stack, sing_box, sing_config, sing_log, env, sing_port)
            reference = merge_mihomo('mode: rule', candidate)
            reference.update({'mixed-port': mihomo_port, 'allow-lan': False, 'bind-address': '127.0.0.1',
                              'dns': {'enable': True, 'enhanced-mode': 'redir-host', 'use-hosts': False,
                                      'nameserver': ['udp://127.0.0.1:' + str(dns_port)]}})
            reference_path = directory/'mihomo.yaml'
            reference_path.write_text(yaml.safe_dump(reference))
            home = directory/'mihomo'
            home.mkdir()
            oracle = ReadyMihomoProcess(stack, [str(mihomo), '-d', str(home), '-f', str(reference_path)],
                                        directory/'mihomo.log', env)
            oracle.start(mihomo_port)
            for name, port in (('sing-box', sing_port), ('mihomo', mihomo_port)):
                self.assertEqual(https_through(port, 'match.example.test', target_port, ca,
                                               name+'-selected-before-dns', protocol='socks5h'), (200, BODY))
                self.assertEqual(delivered.snapshot()[-1][2], '/'+name+'-selected-before-dns')
                before = delivered.snapshot()
                with self.assertRaises(OSError) as rejected:
                    https_through(port, '127.0.0.1', target_port, ca, name+'-literal-reject', protocol='socks5h')
                self.assertNotIsInstance(rejected.exception, TimeoutError)
                self.assertEqual(delivered.snapshot(), before)
                self.assertIsNone((actual if name == 'sing-box' else oracle).process.poll())
            offset = len(sing_log.read_text())
            with self.assertRaises(OSError) as rebinding:
                https_through(sing_port, 'blocked.example.test', blocked_port, ca, 'guard-reject', protocol='socks5h')
            self.assertNotIsInstance(rebinding.exception, TimeoutError)
            deadline = time.monotonic() + 3
            while not any(DENIED_SET_TAG in line and 'reject' in line
                          for line in sing_log.read_text()[offset:].splitlines()):
                self.assertLess(time.monotonic(), deadline, 'No actual denied rule-set rejection reason')
                time.sleep(.01)
            self.assertEqual(blocked.snapshot(), blocked_before)
            self.assertTrue(any(name == 'blocked.example.test' and kind == 1 for name, kind in answers.queries))
            self.assertEqual(https_through(sing_port, 'match.example.test', target_port, ca,
                                           'after-guard-rejection', protocol='socks5h'), (200, BODY))
            self.assertEqual(blocked.snapshot(), blocked_before)
