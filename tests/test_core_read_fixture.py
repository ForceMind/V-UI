"""CI fixture capability binds the installed Root closure to its actual suite."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from scripts.system_maintenance_smoke import installed_core_read_capability

LEGACY={'diagnose','nodes','share','relay','certificates','operate','upgrade','destructive'}

class CoreReadFixtureTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='vui-core-cap-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.control=self.root/'control';self.control.mkdir(mode=0o700)
        self.source=self.root/'source';(self.source/'deploy').mkdir(parents=True)
        self.worker=self.control/'maintenance_cli_worker.py'
    def prepare(self,scopes):
        raw=('SCOPES = '+repr(scopes)+'\n').encode()
        self.worker.write_bytes(raw);self.worker.chmod(0o644)
        (self.source/'deploy/maintenance_cli_worker.py').write_bytes(raw)
        (self.control/'ops-control.json').write_text(json.dumps({'files':{str(self.worker):hashlib.sha256(raw).hexdigest()}}))
        (self.control/'ops-control.json').chmod(0o644)
    def check(self):
        return installed_core_read_capability(self.source,control=self.control,owner=os.geteuid())
    def test_published_legacy_and_new_scope_contracts_are_distinct(self):
        self.prepare(LEGACY);self.assertFalse(self.check())
        self.prepare(LEGACY|{'core_read'});self.assertTrue(self.check())
    def test_new_suite_installed_as_old_bytes_fails_instead_of_skipping(self):
        self.prepare(LEGACY)
        (self.source/'deploy/maintenance_cli_worker.py').write_text('SCOPES = '+repr(LEGACY|{'core_read'}))
        with self.assertRaises(AssertionError):self.check()
    def test_unknown_scope_or_bad_receipt_fails_closed(self):
        self.prepare(LEGACY|{'core_write'})
        with self.assertRaises(AssertionError):self.check()
        self.prepare(LEGACY|{'core_read'})
        (self.control/'ops-control.json').write_text(json.dumps({'files':{str(self.worker):'a'*64}}))
        with self.assertRaises(AssertionError):self.check()
    def test_links_and_writable_worker_fail_before_capability(self):
        self.prepare(LEGACY);self.worker.chmod(0o666)
        with self.assertRaises(AssertionError):self.check()
        self.worker.chmod(0o644);other=self.root/'original';self.worker.rename(other);self.worker.symlink_to(other)
        with self.assertRaises((OSError,AssertionError)):self.check()
