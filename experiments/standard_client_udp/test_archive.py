"""Synthetic red archive/missing evidence controls, not runtime qualification."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import evidence as e
import runtime as r
import test_verifier

class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.legacy=Path(os.environ['VUI_STOCK_UDP_LEGACY']).resolve()
        cls.safe,cls.p=e.modules(cls.legacy)
        test_verifier.VerifierTests.setUpClass()
        cls.source={'commit':'a'*40,'tree':'b'*40,'files':{'synthetic.py':'c'*64}}

    def fixture(self, root):
        case,row,roles,known=test_verifier.VerifierTests().fixture(index=0)
        for role in roles:role['case']=case['id']
        row.update(case=case['id'],stage='C',protocol='vless',roles=case['roles'])
        with tempfile.TemporaryDirectory() as data:
            artifact=json.loads(subprocess.check_output([sys.executable,str(r.HERE/'export_node.py'),'--port','12345'],
                env={**os.environ,'VUI_DATA_DIR':data},text=True))
        node=artifact['node'];digest=lambda x:hashlib.sha256(r.canonical(x)).hexdigest()
        export={'artifact':self.safe.h.c.life.scrub(artifact),'artifact_sha256':digest(artifact),'node_sha256':digest(node),
            'consumer_node_sha256':digest(node),'node_fields_unchanged':True,'consumer':'ordinary-yaml-node-in-listener-shell',
            'server_configuration':'isolated-research-guarded-not-product-udp','http_api_export_executed_here':False,
            'hash_scope':'raw-fake-fixture-export-before-redaction'}
        shell={'mixed-port':2005,'bind-address':'127.0.0.1','allow-lan':False,'mode':'rule','log-level':'debug','ipv6':False,
            'find-process-mode':'off','geo-auto-update':False,'profile':{'store-selected':False,'store-fake-ip':False},
            'proxies':[node],'rules':['MATCH,'+node['name']]}
        roles[-1]['config_sha256']=digest(shell)
        identity={'product':self.source,'legacy':self.source}
        row['verification']=r.verify_case(self.p,case,row,roles,known,root,identity)
        directory=root/'C';leaf=directory/case['id'];leaf.mkdir(parents=True)
        report={'schema_version':1,'kind':r.KIND,'arm':'stock','stage':'C','product_qualified':False,'original_suite_passed':False,
            'observations_complete':False,'capacity_gate_passed':False,'cases':[row],'errors':[]}
        for name,value in {'input-identity.json':{'source_identity':identity,'binaries':known},'plan.json':r.plan(self.p,'stock','C'),
                'result.json':report,'process-binaries.json':roles,case['id']+'/result.json':row,
                case['id']+'/packets.json':row['packets'],case['id']+'/export-identity.json':export}.items():r.save(directory/name,value)
        self.rehash(directory)
        checked=e.verify_stage(directory,self.legacy,'stock','C')
        stage={'stage':'C','pid':123,'process_group':123,'timeout':False,'signals':[],'returncode':1,'leader_joined':True,
               'process_group_gone':True,'capacity_gate_passed':False,'safe_to_continue':False,'independent_verification':checked}
        r.save(root/'stages.json',[stage])
        r.save(root/'MATRIX_RESULT.json',{'arm':'stock','all_stages_executed':False,'capacity_gate_passed':False,'product_qualified':False,'stages':[stage]})
        return case

    def rehash(self,directory):
        r.save(directory/'manifest.json',{'kind':r.KIND+'-text-evidence','files':{p.relative_to(directory).as_posix():r.sha(p)
            for p in directory.rglob('*') if p.is_file() and p.name!='manifest.json'}})

    def test_minimal_red_archive_and_unknown_sensitive_metadata_refused(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(r,'source',return_value=self.source):
            root=Path(tmp)/'runtime';root.mkdir();self.fixture(root)
            output=Path(tmp)/'archive'
            e.prepare(SimpleNamespace(runtime=root,output=output,legacy_repository=self.legacy,arm='stock',build_evidence=None))
            self.assertFalse(e.verify(output,self.legacy,'stock')['capacity_gate_passed'])
            original=json.loads((output/'ARCHIVE.json').read_text())
            for kind in ('sensitive','boolean-schema'):
                with self.subTest(kind=kind):
                    value=deepcopy(original)
                    if kind=='sensitive':value['password']='synthetic-not-a-secret'
                    else:value['schema_version']=True
                    r.save(output/'ARCHIVE.json',value)
                    with self.assertRaises(ValueError):e.verify(output,self.legacy,'stock')

    def test_missing_export_rehashed_manifest_refused(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(r,'source',return_value=self.source):
            root=Path(tmp);case=self.fixture(root)
            (root/'C'/case['id']/'export-identity.json').unlink();self.rehash(root/'C')
            with self.assertRaisesRegex(ValueError,'export'):e.verify_stage(root/'C',self.legacy,'stock','C')

    def test_actual_device_config_hash_not_self_asserted(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(r,'source',return_value=self.source):
            root=Path(tmp);self.fixture(root)
            path=root/'C'/'process-binaries.json';value=json.loads(path.read_text());value[-1]['config_sha256']='0'*64;r.save(path,value);self.rehash(root/'C')
            with self.assertRaisesRegex(ValueError,'configuration'):e.verify_stage(root/'C',self.legacy,'stock','C')

    def test_no_follow_output_overlap_and_total_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();link=root/'link';link.symlink_to(source,target_is_directory=True)
            with self.assertRaises(OSError):e.write_archive(self.safe,link/'output',[],{'a.txt':b'a'},'M.json',{})
            with self.assertRaisesRegex(ValueError,'overlap'):e.write_archive(self.safe,source/'output',[source],{},'M.json',{})
            with patch.object(self.safe,'MAX_TOTAL_BYTES',1):
                with self.assertRaisesRegex(ValueError,'budget'):e.write_archive(self.safe,root/'output',[],{'a.txt':b'abc'},'M.json',{})

    def test_strict_json_duplicate_nonfinite_and_unknown_file(self):
        for text in ('{"a":1,"a":2}','{"a":NaN}','{"a":Infinity}'):
            with self.subTest(text=text),self.assertRaises(ValueError):self.safe.strict_json(text)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'extra.py').write_text('x=1')
            with self.assertRaisesRegex(ValueError,'Unexpected'):self.safe.collect(tmp,{'result.json':'json'})

if __name__=='__main__':unittest.main()
