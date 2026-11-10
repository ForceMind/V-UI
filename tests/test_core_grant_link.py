"""Private terminal grant combination; fake replies, no process/Root state."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
class CoreGrantLinkTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('_grant_ops',ROOT/'deploy/system_ops.py')
        self.ops=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.ops)
        self.normal='vui_mg_'+'a'*43;self.root_token='vui_cg_'+'b'*32+'_'+'c'*43
        self.scopes=['diagnose','core_read'];self.root_scopes=['core_operate','core_backup']
        self.record=dict(id=7,scopes=self.scopes,created_at=1000000,expires_at=1001800,
                         redeemed=False,revoked=False,expired=False,
                         link='https://panel.example.test/maintenance#grant='+self.normal)
        self.store=SimpleNamespace(issue=Mock(return_value=({'scopes':self.root_scopes,'expires_at':1001799},self.root_token)))
    def combine(self,record):
        def read(path,*args):
            return json.dumps({'origin':'https://panel.example.test'}).encode() if path==self.ops.CONFIG else b'{}'
        with patch.object(self.ops.time,'time',return_value=1000001),patch.object(self.ops,'trusted_bytes',side_effect=read),\
             patch.object(self.ops,'_load_core_authority',return_value=SimpleNamespace(CoreAuthority=lambda:self.store)):
            return self.ops._core_grant_link(record,ordinary_scopes=self.scopes,root_scopes=self.root_scopes,seconds=1800)
    def test_combined_link_is_built_from_root_origin_and_separate_token(self):
        result=self.combine(deepcopy(self.record))
        self.assertEqual(result['link'],self.record['link']+'&core='+self.root_token)
        self.assertEqual(result['core_expires_at'],1001799)
        self.store.issue.assert_called_once_with(main_id=7,main_digest=hashlib.sha256(self.normal.encode()).hexdigest(),
            scopes=self.root_scopes,seconds=1800,not_after=1001800)
    def test_foreign_origin_and_extra_fragment_never_receive_root_secret(self):
        for link in ('https://evil.test/maintenance#grant='+self.normal,self.record['link']+'&extra=1',
                     self.record['link'].replace('/maintenance','/other')):
            with self.subTest(link=link),self.assertRaises(self.ops.Refused):self.combine({**self.record,'link':link})
        self.store.issue.assert_not_called()
    def test_wrong_scopes_lifetime_types_and_used_reply_are_rejected(self):
        for key,value in (('id',True),('scopes',['diagnose']),('scopes',['diagnose','core_read','core_read']),
                          ('created_at',False),('expires_at',1001900),('redeemed',True),('expired',True)):
            with self.subTest(key=key),self.assertRaises(self.ops.Refused):self.combine({**self.record,key:value})
        self.store.issue.assert_not_called()
    def test_root_issuance_failure_never_returns_partial_combined_success(self):
        self.store.issue.side_effect=ValueError('fixed Root refusal')
        with self.assertRaises(ValueError):self.combine(deepcopy(self.record))
    def test_root_secret_is_never_sent_to_nonroot_capture_process(self):
        child=Mock();child.receive.return_value=self.record
        account=SimpleNamespace(pw_uid=1001,pw_gid=1001)
        with patch.object(self.ops,'Child',return_value=child) as spawn,\
             patch.object(self.ops,'_core_grant_link',return_value={'link':'combined'}) as combine:
            result=self.ops._create_core_grant(account,['create','diagnose,core_read','1800'],self.scopes,self.root_scopes,1800)
        self.assertEqual(result,{'link':'combined'});child.close.assert_called_once();child.exit_zero.assert_called_once()
        self.assertNotIn(self.root_token,str(spawn.call_args));combine.assert_called_once()
