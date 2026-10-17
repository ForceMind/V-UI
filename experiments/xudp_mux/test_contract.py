import hashlib
from pathlib import Path
import re
import unittest
from verify_evidence import verify_tests
ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
class ContractTests(unittest.TestCase):
    def test_old_address_patch_frozen(self):
        self.assertEqual(hashlib.sha256((ROOT/'experiments/xudp_address/remoteaddr.patch').read_bytes()).hexdigest(),'ee08c4049b05d0fd72b1609839af574fe9be59b5e6c1ed4a1da54f0ffcb3a8ae')
    def test_old_route_patch_frozen(self):
        self.assertEqual(hashlib.sha256((ROOT/'experiments/xudp_address/lifecycle.patch').read_bytes()).hexdigest(),'b1ddb2c13896d06cf86d401044cafc9cb8f15ebbe66664fbb443f8ad13847010')
    def test_patch_paths(self):
        p=(HERE/'mux.patch').read_text();self.assertEqual(re.findall(r'^\+\+\+ b/(.+)$',p,re.M),['mux.go','mux_writer.go'])
        p=(HERE/'route_cancel.patch').read_text();self.assertEqual(re.findall(r'^\+\+\+ b/(.+)$',p,re.M),['route/conn.go'])
    def test_one_queue_owner_and_no_write_escape(self):
        p=(HERE/'mux.patch').read_text();added='\n'.join(line[1:] for line in p.splitlines() if line.startswith('+') and not line.startswith('+++'))
        self.assertEqual(added.count('go session.writeLoop()'),1)
        self.assertNotIn('go c.syncClose',added)
        self.assertNotIn('SetWriteDeadline(',added)
        self.assertIn('if op.canceled {err=net.ErrClosed}',added)
    def test_named_tests_are_not_silently_omitted(self):
        from verify_evidence import UNIT_CASES
        actual=re.findall(r'^func (TestMuxBounded\w+)\(', (HERE/'mux_writer_test.go').read_text(),re.M)
        self.assertEqual(set(actual),{'TestMuxBounded'+n for n in UNIT_CASES})
    def test_missing_tests_fail_verifier(self):
        with self.assertRaises(ValueError):verify_tests([{'Action':'pass'}],['TestMissing'])
    def test_skip_fails_verifier(self):
        with self.assertRaises(ValueError):verify_tests([{'Action':'skip','Test':'TestA'},{'Action':'pass'}],['TestA'])
    def test_duplicate_terminal_fails_verifier(self):
        with self.assertRaises(ValueError):verify_tests([{'Action':'pass','Test':'TestA'},{'Action':'pass','Test':'TestA'},{'Action':'pass'}],['TestA'])
    def test_workflow_is_isolated_and_artifact_has_no_binary(self):
        y=(ROOT/'.github/workflows/xudp-mux-experiment.yml').read_text()
        self.assertIn('branches: [experiment/xudp-mux-lifecycle-20261017]',y)
        self.assertIn('path: /tmp/vui-xudp-mux/evidence',y)
        self.assertNotIn('workflow_dispatch',y)
