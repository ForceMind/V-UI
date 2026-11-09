"""Fixed Root loader against fake receipt bytes; no Root filesystem/code use."""
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


class CoreAuthorityLoaderTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('_core_authority_ops',ROOT/'deploy/system_ops.py')
        self.ops=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.ops)
        self.path=self.ops.CONTROL/'core_authority.py'
        # This inert fixed fake bytecode only defines a class, with no FS access.
        self.code=b'class CoreAuthority:\n    marker = "fixed-private-authority"\n'
        self.receipt=json.dumps({'files':{str(self.path):hashlib.sha256(self.code).hexdigest()}}).encode()
        self.expected=hashlib.sha256(self.receipt).hexdigest()
    def read(self,path,*args):
        if path==self.path:return self.code
        if path==self.ops.RECEIPT:return self.receipt
        raise AssertionError('Unexpected read')
    def test_only_receipted_fixed_helper_loads_and_default_class_has_no_side_effect(self):
        with patch.object(self.ops,'verify_installation',return_value=('systemd',None)),patch.object(self.ops,'trusted_bytes',side_effect=self.read) as reader:
            module=self.ops._load_core_authority(self.expected)
            self.assertEqual(module.CoreAuthority().marker,'fixed-private-authority')
            self.assertEqual({call.args[0] for call in reader.call_args_list},{self.path,self.ops.RECEIPT})
        self.assertIn(self.path,self.ops.fixed_files('systemd'))
    def test_wrong_manager_receipt_or_helper_hash_refuses_before_compile(self):
        with patch.object(self.ops,'verify_installation',return_value=('openrc',None)),patch.object(self.ops,'trusted_bytes',side_effect=self.read):
            with self.assertRaises(self.ops.Refused):self.ops._load_core_authority(self.expected)
        with patch.object(self.ops,'verify_installation',return_value=('systemd',None)),patch.object(self.ops,'trusted_bytes',side_effect=self.read):
            with self.assertRaises(self.ops.Refused):self.ops._load_core_authority('a'*64)
        self.code=b'raise AssertionError("Unreceipted code must never execute")\n'
        with patch.object(self.ops,'verify_installation',return_value=('systemd',None)),patch.object(self.ops,'trusted_bytes',side_effect=self.read):
            with self.assertRaises(self.ops.Refused):self.ops._load_core_authority(self.expected)

    def test_application_origin_uses_only_root_store_and_literal_lookup_keys(self):
        self.code=(b'class CoreAuthority:\n'
                   b'    def application(self, *, release_id, target):\n'
                   b'        return {"release_id":release_id,"target":target,"root_record":True}\n')
        self.receipt=json.dumps({'files':{str(self.path):hashlib.sha256(self.code).hexdigest()}}).encode()
        with patch.object(self.ops,'verify_installation',return_value=('systemd',None)),patch.object(self.ops,'trusted_bytes',side_effect=self.read):
            self.assertEqual(self.ops.application_origin('fixture-release','x86_64-gnu'),
                             {'release_id':'fixture-release','target':'x86_64-gnu','root_record':True})

    def test_application_origin_invalid_lookup_never_loads_or_reads_payload(self):
        with patch.object(self.ops,'trusted_bytes') as read:
            for release,target in (('../outside','x86_64-gnu'),('fixture','unknown'),(True,'x86_64-gnu')):
                with self.subTest(release=release,target=target),self.assertRaises(self.ops.Refused):
                    self.ops.application_origin(release,target)
            read.assert_not_called()
