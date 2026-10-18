"""Finite plan and fail-closed controls, not core runtime qualification."""
import ast
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import runtime as r
LEGACY=Path(os.environ.get('VUI_STOCK_UDP_LEGACY', HERE.parents[2]/'legacy')).resolve()

class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=r.load('contract_frozen_packet',LEGACY/'experiments/xudp_packet/packet_boundary.py')

    def test_exact_three_arms_and_counts(self):
        self.assertEqual(r.ARMS,{'stock':('C','D'),'candidate':('C','D'),'overseas-stock':('B',)})
        for arm,stages in r.ARMS.items():
            for stage in stages:
                cases=r.plan(self.p,arm,stage)
                self.assertEqual((len(cases),len(self.p.leaf_ids(cases))),{'C':(26,66),'D':(2,4),'B':(1,4)}[stage])

    def test_frozen_packet_sequences_unchanged(self):
        for arm,stages in r.ARMS.items():
            for stage in stages:
                expected=[x for x in self.p.plan(stage) if x['protocol']=='vless']
                actual=r.plan(self.p,arm,stage)
                for a,b in zip(actual,expected):
                    a.pop('roles');b.pop('roles');self.assertEqual(a,b)

    def test_device_always_stock_and_owner_absent(self):
        for arm,stages in r.ARMS.items():
            for stage in stages:
                for case in r.plan(self.p,arm,stage):
                    self.assertEqual(case['roles']['device-mihomo'],'mihomo')
                    self.assertNotIn('device-singbox',case['roles'])

    def test_stock_servers_are_official(self):
        for case in r.plan(self.p,'stock','C'):
            self.assertEqual(case['roles'],{'domestic':'official','overseas':'official','device-mihomo':'mihomo'})

    def test_b_only_changes_overseas_from_candidate(self):
        self.assertEqual(r.plan(self.p,'overseas-stock','B')[0]['roles'],
                         {'domestic':'candidate','overseas':'official','device-mihomo':'mihomo'})

    def test_unsupported_arms_stages_refused(self):
        for arm,stage in [('on','C'),('stock','A'),('stock','B'),('overseas-stock','D')]:
            with self.assertRaises(ValueError):r.plan(self.p,arm,stage)

    def test_nested_owner_option_refused_even_false(self):
        for value in (False,{},None,{'outbound':'DOMESTIC_ONLY'}):
            with self.assertRaises(ValueError):r.owner_absent({'inbounds':[{r.OPTION:value}]})
        r.owner_absent({'inbounds':[{'type':'mixed'}]})

    def test_plan_does_not_mutate_frozen_roles(self):
        before=deepcopy(self.p.ROLES)
        r.plan(self.p,'stock','C');self.assertEqual(self.p.ROLES,before)

    def test_all_original_sizes_and_local_oversize(self):
        cases=r.plan(self.p,'candidate','C')
        self.assertEqual({p['size'] for c in cases for p in c['packets']},{0,1,7,8192,32768,60000,65497,65498})
        self.assertEqual(sum(p['direction']=='local_emsgsize' for c in cases for p in c['packets']),1)

    def test_strict_c_d_diagnostic_b_remains_separate(self):
        for stage in ('C','D'):
            self.assertFalse(any(p['diagnostic'] for c in r.plan(self.p,'candidate',stage) for p in c['packets']))
        self.assertEqual([p['diagnostic'] for p in r.plan(self.p,'overseas-stock','B')[0]['packets']],[False,True,True,False])

    def test_immutable_exchange_imported_not_reimplemented(self):
        tree=ast.parse((HERE/'case_runner.py').read_text())
        self.assertNotIn('exchange',[n.name for n in tree.body if isinstance(n,ast.FunctionDef)])
        self.assertIn('from packet_boundary import',(HERE/'case_runner.py').read_text())
        self.assertIn('exchange(association, target, case',(HERE/'lifecycle.py').read_text())

    def test_full_original_idle_window_and_control_proof(self):
        text=(HERE/'lifecycle.py').read_text()
        self.assertIn('deadline, gone = last + 6, None',text)
        self.assertIn('while time.monotonic() < deadline:',text)
        self.assertIn('association.control.fileno() == -1',text)
        self.assertEqual(text.count('association = helper.record_association(SocksDatagrams(f.client_port))'),2)

    def test_export_production_node_no_overseas_fields(self):
        with tempfile.TemporaryDirectory() as data:
            call=subprocess.run([sys.executable,str(HERE/'export_node.py'),'--port','15321'],
                env={**os.environ,'VUI_DATA_DIR':data},text=True,capture_output=True,check=True)
        value=json.loads(call.stdout);node=value['node']
        self.assertEqual(node['uuid'],'11111111-1111-4111-8111-111111111111')
        self.assertEqual(node['servername'],'vpn.example.test')
        self.assertTrue(node['udp']);self.assertFalse(node['skip-cert-verify'])
        self.assertNotIn('22222222-',call.stdout);self.assertNotIn('127.0.0.2',call.stdout)

    def test_source_rejects_untracked_and_changed_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp)
            subprocess.run(['git','init','-q',tmp],check=True)
            subprocess.run(['git','config','user.name','Fixture'],cwd=repo,check=True)
            subprocess.run(['git','config','user.email','fixture@example.test'],cwd=repo,check=True)
            (repo/'a.py').write_text('x=1\n')
            subprocess.run(['git','add','a.py'],cwd=repo,check=True)
            subprocess.run(['git','commit','-qm','fixture'],cwd=repo,check=True)
            self.assertEqual(set(r.source(repo)['files']),{'a.py'})
            (repo/'shadow.py').write_text('x=2\n')
            with self.assertRaisesRegex(ValueError,'Untracked'):r.source(repo)
            (repo/'shadow.py').unlink();(repo/'a.py').write_text('x=2\n')
            with self.assertRaisesRegex(ValueError,'Tracked'):r.source(repo)

    def test_evidence_rejects_private_material(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'Material'):r.save(Path(tmp)/'bad.json',{'key':'-----BEGIN PRIVATE KEY-----'})

    def test_cleanup_does_not_hide_capacity_failure(self):
        text=(HERE/'runtime.py').read_text()
        self.assertIn("'data_audit_errors': audit_errors",text)
        self.assertIn("'original_cleanup_gate_passed': not audit_errors",text)
        self.assertIn("and not row['cleanup']['data_audit_errors']",text)

    def test_nonzero_exit_for_capacity_red(self):
        text=(HERE/'runtime.py').read_text()
        self.assertIn("return 0 if report['capacity_gate_passed'] and not report['errors'] else 1",text)
        self.assertNotIn('expectedFailure',text)

    def test_no_product_paths_changed_by_research(self):
        text=(HERE/'runtime.py').read_text()
        self.assertIn("f.guarded_config(udp_timeout='2s')",text)
        self.assertIn("'server_configuration': 'isolated-research-guarded-not-product-udp'",text)
        self.assertIn("'hash_scope': 'raw-fake-fixture-export-before-redaction'",text)

if __name__=='__main__':unittest.main()

