"""Release checks bind all evidence and target assets to one exact source commit."""
import hashlib
import io
import json
import unittest
import zipfile

from scripts.publish_release import (
    ASSETS,
    KIT_ASSETS,
    REQUIRED,
    TARGETS,
    ReleaseGateError,
    finalize_assets,
    select_runs,
    validate_kit,
    validate_target_artifact,
)

SHA='a'*40


def bundle(target,commit=SHA,version='0.3.1'):
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,'w') as z:
        z.writestr('MANIFEST.json',json.dumps({
            'schema':1,
            'kind':'release',
            'source_commit':commit,
            'version':version,
            'platform':'linux-multi-cpython312',
            'targets':[target],
        }))
    return raw.getvalue()


class ReleasePromotionTests(unittest.TestCase):
    def runs(self):
        return [
            {'id':i,'path':'.github/workflows/'+path,'head_sha':SHA,
             'status':'completed','conclusion':'success'}
            for i,path in enumerate(sorted(REQUIRED),1)
        ]

    def test_requires_all_latest_exact_commit_checks(self):
        runs=self.runs()
        self.assertEqual(set(select_runs(runs,SHA)),REQUIRED)
        for mutation in ('missing','wrong_sha','failure','queued','newer_failed'):
            value=self.runs()
            if mutation=='missing':value.pop()
            if mutation=='wrong_sha':value[0]['head_sha']='b'*40
            if mutation=='failure':value[0]['conclusion']='failure'
            if mutation=='queued':value[0]['status']='queued'
            if mutation=='newer_failed':
                value.append({**value[0],'id':999,'conclusion':'failure'})
            with self.subTest(mutation=mutation),self.assertRaises(ReleaseGateError):
                select_runs(value,SHA)

    def kit(self,commit=SHA,version='0.3.1',corrupt=False):
        files={name:b'fixture' for name in KIT_ASSETS-{'SHA256SUMS'}}
        files['vui-linux-x86_64-gnu.zip']=bundle('x86_64-gnu',commit,version)
        files['RELEASE.json']=json.dumps({
            'source_commit':commit,'version':version,'targets':['x86_64-gnu']
        }).encode()
        files['SHA256SUMS']=(''.join(
            hashlib.sha256(data).hexdigest()+'  '+name+'\n'
            for name,data in sorted(files.items())
        )).encode()
        if corrupt:
            files['install.sh']=b'modified'
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w') as z:
            for name,data in files.items():
                z.writestr(name,data)
        return archive.getvalue()

    def portable(self,target,commit=SHA,version='0.3.1',corrupt=False):
        name='vui-linux-'+target+'.zip'
        data=bundle(target,commit,version)
        checksum=hashlib.sha256(data).hexdigest()
        if corrupt:
            checksum='0'*64
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr(name,data)
            z.writestr(name+'.sha256',checksum+'  '+name+'\n')
        return archive.getvalue()

    def test_kit_integrity_commit_version_and_target(self):
        files=validate_kit(self.kit(),SHA,'0.3.1')
        self.assertEqual(set(files),KIT_ASSETS)
        for raw in (
            self.kit(commit='b'*40),
            self.kit(version='0.3.2'),
            self.kit(corrupt=True),
        ):
            with self.assertRaises(ReleaseGateError):
                validate_kit(raw,SHA,'0.3.1')

    def test_each_portable_target_is_bound_to_commit_version_and_checksum(self):
        for target in TARGETS[1:]:
            with self.subTest(target=target):
                raw=self.portable(target)
                self.assertEqual(
                    validate_target_artifact(raw,SHA,'0.3.1',target),
                    bundle(target),
                )
                with self.assertRaises(ReleaseGateError):
                    validate_target_artifact(self.portable(target,corrupt=True),SHA,'0.3.1',target)
                with self.assertRaises(ReleaseGateError):
                    validate_target_artifact(self.portable(target,commit='b'*40),SHA,'0.3.1',target)

    def test_final_assets_recompute_global_checksums_for_all_targets(self):
        files=validate_kit(self.kit(),SHA,'0.3.1')
        for target in TARGETS[1:]:
            files['vui-linux-'+target+'.zip']=bundle(target)
        final=finalize_assets(files,SHA,'0.3.1')
        self.assertEqual(set(final),ASSETS)
        checks={}
        for line in final['SHA256SUMS'].decode().splitlines():
            digest,name=line.split('  ',1)
            checks[name]=digest
        self.assertEqual(set(checks),ASSETS-{'SHA256SUMS'})
        for name,digest in checks.items():
            self.assertEqual(hashlib.sha256(final[name]).hexdigest(),digest)
        record=json.loads(final['RELEASE.json'])
        self.assertEqual(record['targets'],list(TARGETS))


if __name__=='__main__':
    unittest.main()
