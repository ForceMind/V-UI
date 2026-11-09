"""Root stage snapshot algorithm in owner-controlled temp files; no host stage."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import stat
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,call,patch
import zipfile

ROOT=Path(__file__).resolve().parents[1]
def load(name):
    spec=importlib.util.spec_from_file_location('_stage_origin_'+name,ROOT/'deploy'/(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


class StageOriginTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='vui-stage-origin-');self.addCleanup(temporary.cleanup)
        self.anchor=Path(temporary.name);self.parent=self.anchor/'stages';self.parent.mkdir(mode=0o755)
        self.ops=load('system_ops');self.authority=load('core_authority')
        self.account=SimpleNamespace(pw_uid=os.geteuid(),pw_gid=os.getegid())
        self.source=self.anchor/'candidate.zip'
        self.raw=json.dumps(dict(schema=1,kind='release',platform='linux-multi-cpython312',
            release_id='candidate-123',source_commit='a'*40,targets=['x86_64-gnu']),indent=4).encode()
        with zipfile.ZipFile(self.source,'w') as archive:archive.writestr('MANIFEST.json',self.raw)
        self.original=self.source.read_bytes();self.request=dict(protocol=1,action='stage',path=str(self.source),
            sha256=hashlib.sha256(self.original).hexdigest())
    def material(self,request=None):
        return self.ops._StageArchive(request or self.request,self.account,[],_parent=self.parent,
            _anchor=self.anchor,_owner=os.geteuid())
    def test_snapshot_and_handoff_survive_original_path_replacement(self):
        stage=self.material();captured=[]
        helper=SimpleNamespace(**vars(self.authority))
        helper.CoreAuthority=lambda:SimpleNamespace(record_application=lambda row:captured.append(row))
        with stage.material() as material:
            self.source.unlink();self.source.write_bytes(b'unrelated replacement')
            row=self.ops._stage_origin(material,'x86_64-gnu',helper)
            self.assertEqual(material.path.read_bytes(),self.original)
            self.assertEqual(row['manifest_sha256'],hashlib.sha256(self.raw).hexdigest())
            self.assertEqual(row['archive_sha256'],self.request['sha256'])
            self.assertEqual(captured,[row]);path=material.path
            self.assertEqual(path.parent.stat().st_mode&0o777,0o750)
            self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertFalse(path.exists());self.assertFalse(path.parent.exists())
        self.assertEqual(self.source.read_bytes(),b'unrelated replacement')
    def test_bad_digest_never_yields_or_leaves_handoff(self):
        stage=self.material({**self.request,'sha256':'b'*64})
        with self.assertRaisesRegex(self.ops.Refused,'digest'):
            with stage.material():self.fail('unverified archive yielded')
        self.assertEqual(list(self.parent.iterdir()),[])
    def test_foreign_leaf_is_preserved_and_never_recursively_removed(self):
        stage=self.material()
        with self.assertRaisesRegex(self.ops.Refused,'file changed'):
            with stage.material() as material:
                path=material.path;retained=path.with_name('retained.bin');path.rename(retained)
                path.write_bytes(b'foreign data')
        self.assertEqual(path.read_bytes(),b'foreign data')
        self.assertEqual(retained.read_bytes(),self.original)
    def test_foreign_directory_is_preserved(self):
        stage=self.material()
        with self.assertRaisesRegex(self.ops.Refused,'directory changed'):
            with stage.material() as material:
                directory=material.path.parent;original=directory.with_name(directory.name+'-retained')
                directory.rename(original);directory.mkdir();(directory/'foreign').write_bytes(b'keep')
        self.assertEqual((directory/'foreign').read_bytes(),b'keep')
        self.assertEqual((original/'bundle.zip').read_bytes(),self.original)
    def test_unknown_worker_lifetime_retains_original_handoff(self):
        stage=self.material()
        with stage.material() as material:
            material.retain=True;path=material.path
        self.assertEqual(path.read_bytes(),self.original)
    def test_close_failure_is_not_retried_on_reused_file_descriptor(self):
        stage=self.material();real_close=os.close;reused=[]
        sentinel=self.anchor/'sentinel';sentinel.write_bytes(b'keep')
        with self.assertRaises(OSError):
            with stage.material():
                def close(fd):
                    if not reused and stat.S_ISREG(os.fstat(fd).st_mode):
                        real_close(fd);replacement=os.open(sentinel,os.O_RDONLY)
                        if replacement!=fd:
                            os.dup2(replacement,fd);real_close(replacement)
                        reused.append(fd)
                        raise OSError('inert close acknowledgment loss')
                    real_close(fd)
                patcher=patch.object(self.ops.os,'close',side_effect=close);patcher.start()
                self.addCleanup(patcher.stop)
        self.assertEqual(os.read(reused[0],4),b'keep');real_close(reused[0])
        patcher.stop()

    def pipeline(self,*,answer='candidate-123',close_error=False):
        material=SimpleNamespace(path=self.anchor/'trusted-handoff.zip',deadline=time.monotonic()+5,
                                 retain=False,check=lambda:None,origin={'release_id':'candidate-123'})
        @contextmanager
        def held():yield material
        stage=SimpleNamespace(material=held)
        worker=Mock();worker.receive.side_effect=[{'event':'prepared'}, {'event':'done','ok':True,'prepared_release':answer}]
        if close_error:worker.close.side_effect=self.ops.Refused('worker cleanup unconfirmed')
        return material,stage,worker
    def run_pipeline(self,material,stage,worker):
        with patch.object(self.ops,'accepted_stage',side_effect=lambda *args:stage.material()):
            return self.ops.prepare_candidate(self.request,self.account,[],Mock(return_value=worker))
    def test_prepared_reply_must_match_root_accepted_id(self):
        material,stage,worker=self.pipeline(answer='different-candidate')
        with self.assertRaisesRegex(self.ops.Refused,'origin mismatch'):self.run_pipeline(material,stage,worker)
        self.assertNotIn(call({'command':'finish'}),worker.send.call_args_list)
        worker.close.assert_called_once()
    def test_worker_cleanup_failure_marks_handoff_to_retain(self):
        material,stage,worker=self.pipeline(close_error=True)
        with self.assertRaises(self.ops.Refused):self.run_pipeline(material,stage,worker)
        self.assertTrue(material.retain)

    def test_hash_correct_non_zip_refuses_without_recording_or_leaking_traceback(self):
        self.source.write_bytes(b'accepted digest but not an application ZIP')
        request={**self.request,'sha256':hashlib.sha256(self.source.read_bytes()).hexdigest()}
        captured=[];helper=SimpleNamespace(**vars(self.authority))
        helper.CoreAuthority=lambda:SimpleNamespace(record_application=lambda row:captured.append(row))
        with self.material(request).material() as material:
            with self.assertRaisesRegex(self.ops.Refused,'metadata refused'):
                self.ops._stage_origin(material,'x86_64-gnu',helper)
        self.assertFalse(captured)
        self.assertEqual(list(self.parent.iterdir()),[])

    def test_invalid_manifest_json_is_fixed_refusal_before_source_record(self):
        with zipfile.ZipFile(self.source,'w') as archive:archive.writestr('MANIFEST.json',b'{"schema":')
        request={**self.request,'sha256':hashlib.sha256(self.source.read_bytes()).hexdigest()}
        captured=[];helper=SimpleNamespace(**vars(self.authority))
        helper.CoreAuthority=lambda:SimpleNamespace(record_application=lambda row:captured.append(row))
        with self.material(request).material() as material:
            with self.assertRaisesRegex(self.ops.Refused,'JSON refused'):
                self.ops._stage_origin(material,'x86_64-gnu',helper)
        self.assertFalse(captured)
