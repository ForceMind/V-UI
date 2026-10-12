"""Artifact admission only: synthetic API/archives, no network or sudo."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from scripts import fetch_managed_ops_kit as gate
from scripts import publish_release as artifacts

COMMIT = 'a' * 40
ENV = {'GITHUB_REPOSITORY': gate.REPO, 'GITHUB_EVENT_NAME': 'pull_request'}


def event():
    repo = {'full_name': gate.REPO}
    return {'action':'labeled', 'label':{'name':gate.LABEL},
            'pull_request':{'number':33,'state':'open','head':{'sha':COMMIT,'repo':repo},'base':{'repo':repo}}}


def zipped(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name,value in files.items(): archive.writestr(name,value)
    return output.getvalue()


class KitAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root/'VERSION').write_text('0.4.7\n')
        record = {'source_commit':COMMIT,'version':'0.4.7','platform':'linux-multi-cpython312','targets':['x86_64-gnu']}
        files = {name:b'synthetic, never executed' for name in artifacts.KIT_ASSETS-{'SHA256SUMS'}}
        files['RELEASE.json'] = json.dumps(record).encode()
        files['vui-linux-x86_64-gnu.zip'] = zipped({'MANIFEST.json':json.dumps(record).encode()})
        files['SHA256SUMS'] = ''.join(hashlib.sha256(value).hexdigest()+'  '+name+'\n' for name,value in sorted(files.items())).encode()
        self.raw = zipped(files)
        self.runs = [{'id':index+10,'path':'.github/workflows/'+name,'head_sha':COMMIT,
            'event':'pull_request','status':'completed','conclusion':'success','run_attempt':1,
            'repository':{'full_name':gate.REPO},'head_repository':{'full_name':gate.REPO},
            'run_started_at':'2026-10-11T18:00:00Z','updated_at':'2026-10-11T18:10:00Z'}
            for index,name in enumerate(sorted(artifacts.REQUIRED))]
        self.run = next(row for row in self.runs if row['path'].endswith('/oneclick.yml'))
        self.artifact = {'id':123,'name':'vui-release-kit-'+COMMIT,'expired':False,
            'workflow_run':{'id':self.run['id'],'head_sha':COMMIT},'size_in_bytes':len(self.raw),
            'digest':'sha256:'+hashlib.sha256(self.raw).hexdigest(),'created_at':'2026-10-11T18:09:00Z'}
        self.api = Mock(side_effect=self.reply); self.download = Mock(side_effect=lambda _:self.raw)

    def reply(self, path):
        if path == 'pulls/33': return event()['pull_request']
        if path.startswith('actions/runs?'): return {'workflow_runs':self.runs}
        if path == 'actions/runs/'+str(self.run['id'])+'/artifacts?per_page=100&page=1':
            return {'artifacts':[self.artifact]}
        raise AssertionError('Unexpected API path: '+path)

    def prepare(self, value=None, environment=None):
        with patch.object(gate.os,'geteuid',return_value=1000), \
                patch.object(gate.subprocess,'check_output',return_value=COMMIT+'\n'), \
                patch.object(gate.subprocess,'run'):
            return gate.prepare(value or event(),environment or ENV,self.root/'output',self.root,
                                api=self.api,download=self.download)

    def test_exact_same_head_successful_original_kit_is_written_without_execution(self):
        row=self.prepare()
        self.assertEqual(row['source_commit'],COMMIT)
        self.assertFalse(row['qualification_executed'])
        self.assertEqual(set(path.name for path in (self.root/'output/kit').iterdir()), artifacts.KIT_ASSETS)
        self.assertEqual(sum(call.args[0]=='pulls/33' for call in self.api.call_args_list),2)
        self.assertTrue(all(len(call.args)==1 and not call.kwargs for call in self.api.call_args_list))
        self.assertEqual((self.root/'output/kit/install_ops_control.py').stat().st_mode & 0o777,0o600)

    def test_fork_wrong_event_label_repository_and_closed_pr_reject_before_api(self):
        cases=[]
        for path,value in [('action','synchronize'),('label',{'name':'run-low-resource-root-install'})]:
            item=event();item[path]=value;cases.append((item,ENV))
        for side in ('head','base'):
            item=copy.deepcopy(event());item['pull_request'][side]['repo']['full_name']='outsider/fork';cases.append((item,ENV))
        item=event();item['pull_request']['state']='closed';cases.append((item,ENV))
        cases += [(event(),{**ENV,'GITHUB_EVENT_NAME':'push'}),(event(),{**ENV,'GITHUB_REPOSITORY':'other/repo'})]
        for item,environment in cases:
            with self.subTest(item=item,env=environment),self.assertRaises(gate.AdmissionError):self.prepare(item,environment)
        self.api.assert_not_called();self.download.assert_not_called()

    def test_latest_failed_or_missing_workflow_cannot_reuse_older_success(self):
        for missing in (False,True):
            self.runs = [row for row in self.runs if not row['path'].endswith('/docs.yml')]
            if not missing:
                self.runs += [{**self.run,'path':'.github/workflows/docs.yml','id':100,'conclusion':'failure'}]
            with self.assertRaises(artifacts.ReleaseGateError):self.prepare()
        self.download.assert_not_called()

    def test_wrong_head_origin_run_digest_expired_and_stale_attempt_fail(self):
        original=copy.deepcopy(self.artifact)
        mutations=[{'workflow_run':{'id':999,'head_sha':COMMIT}},
                   {'workflow_run':{'id':self.run['id'],'head_sha':'b'*40}},
                   {'digest':'sha256:'+'0'*64},{'digest':None},{'expired':True},
                   {'created_at':'2026-10-11T17:59:00Z'},{'created_at':'2026-10-11T19:00:00Z'}]
        for mutation in mutations:
            self.artifact={**original,**mutation}
            with self.subTest(mutation=mutation),self.assertRaises((gate.AdmissionError,artifacts.ReleaseGateError)):
                self.prepare()
            self.assertFalse((self.root/'output').exists())

    def test_pr_change_during_download_prevents_writes(self):
        original=self.reply;calls=0
        def reply(path):
            nonlocal calls
            value=original(path)
            if path=='pulls/33':
                calls+=1
                if calls==2:value['head']['sha']='b'*40
            return value
        self.api.side_effect=reply
        with self.assertRaises(gate.AdmissionError):self.prepare()
        self.assertFalse((self.root/'output').exists())

    def test_truncated_changed_archive_and_existing_destination_refuse(self):
        self.raw=self.raw[:-1]
        with self.assertRaises(gate.AdmissionError):self.prepare()
        (self.root/'output').symlink_to(self.root,target_is_directory=True)
        self.api.reset_mock()
        with self.assertRaises(gate.AdmissionError):self.prepare()
        self.api.assert_not_called()

    def test_api_pagination_is_bounded_and_complete(self):
        api=Mock(side_effect=[{'artifacts':list(range(100))},{'artifacts':[100]}])
        self.assertEqual(gate.pages(api,'actions/runs/1/artifacts','artifacts'),list(range(101)))
        self.assertIn('page=2',api.call_args.args[0])
        with self.assertRaises(gate.AdmissionError):gate.pages(lambda _:{'artifacts':[{}]*100},'x','artifacts')

    def test_workflow_is_label_only_pinned_read_only_and_separates_token(self):
        value=(Path(__file__).resolve().parents[1]/'.github/workflows/managed-system-control.yml').read_text()
        self.assertIn('types: [labeled]',value);self.assertNotIn('workflow_dispatch:',value);self.assertNotIn('push:',value)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository",value)
        self.assertIn('timeout-minutes: 35',value);self.assertIn('runs-on: ubuntu-24.04',value)
        self.assertNotIn('secrets.',value);self.assertNotIn('contents: write',value)
        self.assertEqual(value.count('GH_TOKEN:'),1)
        self.assertNotIn('GH_TOKEN:',value.split('- name: Explicit new-component',1)[1])
        self.assertNotIn('run-low-resource-root-install',value)


if __name__=='__main__':unittest.main()
