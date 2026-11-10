"""Real atomic-replace race with fake Root ownership; no host service or data."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

class Refused(RuntimeError):pass

class TaskRecordReadTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('_task_race_daemon',Path(__file__).resolve().parents[1]/'deploy/maintenance_daemon.py')
        self.daemon=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.daemon)
        temporary=tempfile.TemporaryDirectory(prefix='vui-task-read-');self.addCleanup(temporary.cleanup)
        self.directory=Path(temporary.name);self.daemon.TASKS=self.directory
        self.identity='a'*32;self.path=self.directory/(self.identity+'.json');self.publish('running')
        original=Path.lstat
        def root_stat(path,*args,**kwargs):
            info=original(path,*args,**kwargs)
            return SimpleNamespace(st_mode=info.st_mode,st_uid=0,st_nlink=info.st_nlink,st_size=info.st_size,st_dev=info.st_dev,st_ino=info.st_ino)
        self.patch=patch.object(Path,'lstat',root_stat);self.patch.start();self.addCleanup(self.patch.stop)
    def publish(self,state,mode=0o600):
        target=self.directory/('new-'+state)
        target.write_text(json.dumps({'id':self.identity,'action':'stop','state':state,'created_at':1}))
        target.chmod(mode);os.replace(target,self.path)
    def store(self,reader):
        return self.daemon.Store(SimpleNamespace(Refused=Refused,decode_json=json.loads,trusted_bytes=reader))
    def test_owned_atomic_replacement_is_retried_and_returns_current_record(self):
        calls=0
        def read(path,limit):
            nonlocal calls
            calls+=1
            with open(path,'rb') as stream:
                before=os.fstat(stream.fileno())
                if calls==1:self.publish('succeeded')
                raw=stream.read(limit)
                after=os.fstat(stream.fileno());linked=path.lstat()
                if after.st_nlink!=1 or (before.st_dev,before.st_ino)!=(linked.st_dev,linked.st_ino):
                    raise Refused('trusted file changed')
                return raw
        value=self.store(read).read(self.identity)
        self.assertEqual(value['state'],'succeeded');self.assertEqual(calls,2)
    def test_stable_refusal_is_never_retried(self):
        calls=0
        def read(*args):
            nonlocal calls
            calls+=1;raise Refused('stable unsafe file')
        with self.assertRaises(Refused):self.store(read).read(self.identity)
        self.assertEqual(calls,1)
    def test_replacement_with_unsafe_permissions_is_not_retried(self):
        calls=0
        def read(*args):
            nonlocal calls
            calls+=1;self.publish('unsafe',0o666);raise Refused('trusted file changed')
        with self.assertRaises(Refused):self.store(read).read(self.identity)
        self.assertEqual(calls,1)
    def test_continuous_root_replacement_has_fixed_attempt_limit(self):
        calls=0
        def read(*args):
            nonlocal calls
            calls+=1;self.publish(str(calls));raise Refused('trusted file changed')
        with self.assertRaises(Refused):self.store(read).read(self.identity)
        self.assertEqual(calls,3)

    def test_journal_keeps_only_exact_fixed_refusal_stage_and_category(self):
        from scripts.system_maintenance_smoke import safe_maintenance_journal
        messages=['VUI_MAINTENANCE_REFUSED:TASK_TRUST_READ:TRUSTED_INPUT',
                  'VUI_MAINTENANCE_REFUSED:GET:IO /private/secret',
                  'VUI_MAINTENANCE_REFUSED:unknown:INTERNAL',
                  'VUI_MAINTENANCE_REFUSED:GET:IO PermissionError']
        raw='\n'.join(json.dumps({'MESSAGE':value}) for value in messages).encode()
        self.assertEqual(safe_maintenance_journal(raw),['REQUEST_TASK_TRUST_READ_TRUSTED_INPUT','UNCLASSIFIED','UNCLASSIFIED','UNCLASSIFIED'])

    def test_refusal_prefix_has_priority_over_all_legacy_journal_needles(self):
        from scripts.system_maintenance_smoke import safe_maintenance_journal
        suffixes=['V-UI maintenance startup refused: STARTUP_SERVING',
                  'V-UI system maintenance unavailable','PermissionError','ModuleNotFoundError',
                  'SyntaxError','Main process exited','Failed with result','Scheduled restart job']
        raw='\n'.join(json.dumps({'MESSAGE':'VUI_MAINTENANCE_REFUSED:GET:IO '+value}) for value in suffixes).encode()
        self.assertEqual(safe_maintenance_journal(raw),['UNCLASSIFIED']*len(suffixes))
