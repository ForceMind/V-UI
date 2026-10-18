import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('packet_coordinator', HERE/'run_runtime.py')
r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)


class CoordinatorTests(unittest.TestCase):
    def row(self, stage='A'):
        return {'stage':stage,'returncode':0,'timeout':False,'cleanup':{
            'signals':[],'leader_joined':True,'process_group_gone':True}}
    def report(self, stage='A'):
        return {'stage':stage,'diagnostic_complete':True,'product_qualified':False,
                'original_suite_passed':False,'stage_capacity_passed':stage in ('C','D'),
                'capacity_failed_leaves':['observed-stock-prefix'] if stage in ('A','B') else []}
    def test_stock_diagnostic_keeps_capacity_red(self):
        self.assertTrue(r.stage_passed(self.row(),self.report()))
    def test_stock_false_green_rejected(self):
        report=self.report();report['stage_capacity_passed']=True
        self.assertFalse(r.stage_passed(self.row(),report))
    def test_stock_missing_red_observation_rejected(self):
        report=self.report();report['capacity_failed_leaves']=[]
        self.assertFalse(r.stage_passed(self.row(),report))
    def test_repaired_capacity_red_fails(self):
        report=self.report('C');report['stage_capacity_passed']=False
        self.assertFalse(r.stage_passed(self.row('C'),report))
    def test_missing_or_wrong_fields_fail(self):
        for key in ('stage','diagnostic_complete','product_qualified','original_suite_passed'):
            with self.subTest(key=key):
                report=self.report();report.pop(key)
                self.assertFalse(r.stage_passed(self.row(),report))
    def test_group_not_joined_fails(self):
        for key,value in (('signals',['SIGTERM']),('leader_joined',False),('process_group_gone',False)):
            with self.subTest(key=key):
                row=self.row();row['cleanup'][key]=value
                self.assertFalse(r.stage_passed(row,self.report()))
    def test_timeout_and_nonzero_fail(self):
        for key,value in (('timeout',True),('returncode',1),('returncode',None)):
            row=self.row();row[key]=value
            self.assertFalse(r.stage_passed(row,self.report()))
    def run_fake(self, bad=None, missing=False):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);seen=[]
            def runner(command):
                stage=command[command.index('--stage')+1];seen.append(stage)
                target=out/stage;target.mkdir()
                if not missing:
                    report=self.report(stage)
                    if stage==bad:report['diagnostic_complete']=False
                    (target/'result.json').write_text(json.dumps(report))
                return self.row(stage)
            result=r.run_stages(out,out/'input.json',runner)
            self.assertEqual(json.loads((out/'stages.json').read_text()),result)
            return seen,result
    def test_complete_four_stages(self):
        seen,rows=self.run_fake();self.assertEqual(seen,list(r.STAGES))
        self.assertTrue(all(x['stage_gate_passed'] for x in rows))
    def test_failure_stops_later_stages(self):
        seen,rows=self.run_fake('B');self.assertEqual(seen,['A','B'])
        self.assertFalse(rows[-1]['stage_gate_passed'])
    def test_missing_report_stops(self):
        seen,rows=self.run_fake(missing=True);self.assertEqual(seen,['A'])
        self.assertFalse(rows[0]['stage_gate_passed'])
    def test_real_owned_process_success(self):
        result=r.run_stage([sys.executable,'-c','print("owned child")'],timeout=2)
        self.assertEqual(result['stdout'],'owned child\n')
        self.assertEqual(result['cleanup'],self.row()['cleanup'])
    def test_real_owned_process_timeout(self):
        result=r.run_stage([sys.executable,'-c','import time;time.sleep(20)'],timeout=.1)
        self.assertTrue(result['timeout']);self.assertIsNone(result['returncode'])
        self.assertTrue(result['cleanup']['leader_joined'])
        self.assertTrue(result['cleanup']['process_group_gone'])
        self.assertEqual(result['cleanup']['signals'],['SIGTERM'])


class UploadTests(unittest.TestCase):
    def module(self):
        spec=importlib.util.spec_from_file_location('packet_upload',HERE/'prepare_evidence.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module
    def test_allowlist_excludes_binary_and_config(self):
        allowed=self.module().allowed_build()
        self.assertIn('mux-encrypted-cancel-debug.stdout',allowed)
        self.assertNotIn('candidate-sing-box',allowed);self.assertNotIn('config.json',allowed)
    def test_partial_failure_manifest_has_size_hash(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);build=root/'build';build.mkdir();out=root/'upload'
            (build/'RESULT.json').write_text('{"passed": false}\n')
            self.module().prepare(build,out)
            manifest=json.loads((out/'MANIFEST.json').read_text())
            self.assertEqual(manifest['count'],1)
            self.assertEqual(set(manifest['files'][0]),{'path','bytes','sha256'})
    def test_unknown_or_symlink_rejected(self):
        for mode in ('unknown','symlink'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                root=Path(d);build=root/'build';build.mkdir()
                if mode=='unknown':(build/'candidate-sing-box').write_text('not allowed')
                else:
                    source=root/'source';source.write_text('{}')
                    (build/'RESULT.json').symlink_to(source)
                with self.assertRaises(Exception):self.module().prepare(build,root/'upload')

if __name__ == '__main__':unittest.main()
