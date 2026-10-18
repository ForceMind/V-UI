"""Official pinned core syntax checks; no socket forwarding qualification."""
import json
import os
import subprocess
import unittest

from loopback_helpers import unused_port
from test_proxy_policy import inbound
from test_proxy_policy_real import ProxyPolicyRealTests as _Fixture

setup_parser = _Fixture.setUp
configuration = _Fixture.configuration
del _Fixture


@unittest.skipUnless(os.getenv('VUI_TEST_CORES'), 'Set VUI_TEST_CORES for official core syntax checks')
class InboundRoutingParserTests(unittest.TestCase):
    setUp = setup_parser
    configuration = configuration

    def test_mixed_three_modes_four_flags_tls_and_reality_upstreams(self):
        checked = []
        for local in (False, True):
            for relay in (False, True):
                for reality in (False, True):
                    with self.subTest(local=local, relay=relay, reality=reality):
                        rows = [inbound(1, port=unused_port()),
                                inbound(2, port=unused_port(), routing_mode='direct'),
                                inbound(3, port=unused_port(), routing_mode='relay')]
                        result, _ = self.configuration(local, relay, reality, rows=rows)
                        path = self.root / 'mixed-check.json'
                        path.write_text(json.dumps(result.config))
                        path.chmod(0o600)
                        command = subprocess.run([str(self.binary), 'check', '-c', str(path)],
                            capture_output=True, text=True, timeout=10, env=self.env)
                        self.assertEqual(command.returncode, 0, command.stdout + command.stderr)
                        checked.append((local, relay, reality))
        self.assertEqual(len(set(checked)), 8)
        print('Pinned sing-box 1.14.2: eight mixed-mode syntax checks passed; no forwarding was run')


if __name__ == '__main__':
    unittest.main()
