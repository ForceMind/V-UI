"""Release checks bind all evidence and assets to one exact source commit."""
import hashlib
import io
import json
import unittest
import zipfile
from scripts.publish_release import ASSETS,REQUIRED,ReleaseGateError,select_runs,validate_kit

SHA='a'*40
class ReleasePromotionTests(unittest.TestCase):
    def runs(self):
        return [{'id':i,'path':'.github/workflows/'+path,'head_sha':SHA,'status':'completed','conclusion':'success'} for i,path in enumerate(sorted(REQUIRED),1)]
    def test_requires_all_latest_exact_commit_checks(self):
        runs=self.runs();self.assertEqual(set(select_runs(runs,SHA)),REQUIRED)
        for mutation in ('missing','wrong_sha','failure','queued','newer_failed'):
            value=self.runs()
            if mutation=='missing':value.pop()
            if mutation=='wrong_sha':value[0]['head_sha']='b'*40
            if mutation=='failure':value[0]['conclusion']='failure'
            if mutation=='queued':value[0]['status']='queued'
            if mutation=='newer_failed':value.append({**value[0],'id':999,'conclusion':'failure'})
            with self.subTest(mutation=mutation),self.assertRaises(ReleaseGateError):select_runs(value,SHA)
    def kit(self, commit=SHA, version='0.3.0', corrupt=False):
        bundle=io.BytesIO()
        with zipfile.ZipFile(bundle,'w') as z:z.writestr('MANIFEST.json',json.dumps({'source_commit':commit,'version':version}))
        files={name:b'fixture' for name in ASSETS-{'SHA256SUMS'}}
        files['vui-linux-amd64.zip']=bundle.getvalue()
        files['RELEASE.json']=json.dumps({'source_commit':commit,'version':version}).encode()
        checks=''.join(hashlib.sha256(data).hexdigest()+'  '+name+'\n' for name,data in sorted(files.items()))
        files['SHA256SUMS']=checks.encode()
        if corrupt:files['install.sh']=b'modified'
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w') as z:
            for name,data in files.items():z.writestr(name,data)
        return archive.getvalue()
    def test_kit_integrity_commit_and_version(self):
        self.assertEqual(set(validate_kit(self.kit(),SHA,'0.3.0')),ASSETS)
        for raw in (self.kit(commit='b'*40),self.kit(version='0.3.1'),self.kit(corrupt=True)):
            with self.assertRaises(ReleaseGateError):validate_kit(raw,SHA,'0.3.0')

if __name__=='__main__':unittest.main()
