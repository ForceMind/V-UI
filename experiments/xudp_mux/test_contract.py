import hashlib
from pathlib import Path
import re
import unittest
from verify_evidence import verify_tests, verify_route
import copy
import json
import tempfile
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

class RouteVerifierMutationTests(unittest.TestCase):
    def fixture(self, arm):
        data=[]
        for name in ('TestMuxRouteReplyBackpressure','TestMuxRouteEndBackpressure'):
            events=[{'case':name,'event':'mux_arm_snapshot','seq':1,'arm':arm,'parent_writers':int(arm=='mux'),'route_copies':0 if arm=='mux' else (2 if 'Reply' in name else 1),'route_callbacks':0,'cancelers':0,'parent_transport_open':True,'owned_outbound_closed':True},
                    {'case':name,'event':'restore_reads','seq':2}, {'case':name,'event':'cleanup_start','seq':3},
                    {'case':name,'event':'cleanup_joined','seq':4,'route_copy_workers':0,'route_callback_workers':0,'canceler_workers':0,'mux_cleanup_workers':0,'active_transport_writes':0,'test_owned_workers_joined':True,'actual_route_workers_joined':True}]
            data.extend({'Action':'output','Test':name,'Output':'XUDP_ROUTE_LIFECYCLE '+json.dumps(row)} for row in events)
            data.append({'Action':'pass','Test':name})
        data.append({'Action':'pass'})
        return data
    def check(self, data, arm):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'go.jsonl';path.write_text('\n'.join(json.dumps(row) for row in data)+'\n')
            return verify_route(path,arm)
    def test_valid_red_and_green_are_distinct(self):
        for arm in ('route-only','mux'):self.assertEqual(self.check(self.fixture(arm),arm)['arm'],arm)
    def test_mutated_ownership_worker_order_or_join_is_rejected(self):
        mutations=[('mux_arm_snapshot','parent_writers',0),('mux_arm_snapshot','route_copies',1),('mux_arm_snapshot','route_callbacks',1),('mux_arm_snapshot','cancelers',1),('mux_arm_snapshot','parent_transport_open',False),('mux_arm_snapshot','owned_outbound_closed',False),('mux_arm_snapshot','seq',3),('cleanup_joined','actual_route_workers_joined',False),('cleanup_joined','active_transport_writes',1)]
        for event,key,value in mutations:
            with self.subTest(event=event,key=key):
                data=self.fixture('mux')
                for row in data:
                    if 'Output' not in row:continue
                    parsed=json.loads(row['Output'].split('XUDP_ROUTE_LIFECYCLE ',1)[1])
                    if parsed['event']==event:
                        parsed[key]=value;row['Output']='XUDP_ROUTE_LIFECYCLE '+json.dumps(parsed);break
                with self.assertRaises(ValueError):self.check(data,'mux')
    def test_missing_snapshot_or_test_is_rejected(self):
        for action in ('snapshot','test'):
            data=self.fixture('mux')
            if action=='snapshot':data.pop(0)
            else:data=[row for row in data if row.get('Test')!='TestMuxRouteReplyBackpressure']
            with self.assertRaises(ValueError):self.check(data,'mux')
