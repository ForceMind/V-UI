"""Fake transport only, including offline acquisition-to-M0-candidate packaging."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from app.core_updates import artifacts as a
from scripts import fetch_core_candidate_inputs as f, build_core_component as b
from test_core_component_builder import Fixture, sha

SOURCE='a'*40


class Response:
    def __init__(self,url,raw=b'',code=200,headers=None,fail_read=False,fail_close=False):
        self.url=url;self.raw=io.BytesIO(raw);self.code=code
        self.headers={'Content-Length':str(len(raw))} if headers is None else headers
        self.closed=False;self.fail_read=fail_read;self.reads=0;self.fail_close=fail_close
    def geturl(self):return self.url
    def getcode(self):return self.code
    def read(self,size):
        assert 0<size<=a.CHUNK
        self.reads+=1
        if self.fail_read and self.reads==2:raise TimeoutError('private timeout diagnostic')
        return self.raw.read(size)
    def close(self):
        self.closed=True
        if self.fail_close:raise OSError('private close diagnostic')


class Transport:
    def __init__(self,routes):self.routes=routes;self.calls=[];self.responses=[];self.requests=[]
    def open(self,request,timeout):
        url=request.full_url;self.calls.append(url);self.requests.append(request)
        assert timeout<=10
        assert all(key.lower() not in ('authorization','cookie','proxy-authorization') for key in request.headers)
        value=self.routes[url]
        if isinstance(value,list):value=value.pop(0)
        if isinstance(value,Exception):raise value
        response=value(url) if callable(value) else Response(url,value)
        self.responses.append(response)
        return response
    def closed(self,test):test.assertTrue(all(response.closed for response in self.responses))


class CandidateInputTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='vui-inputs-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.root.chmod(0o700)
        self.foreign=self.root/'foreign';self.foreign.write_bytes(b'preserve')
        self.addCleanup(lambda:self.assertEqual(self.foreign.read_bytes(),b'preserve'))
    def fixture(self,core='sing-box',annotated=False):
        material=self.root/'materials';material.mkdir(mode=0o700)
        source=Fixture(material,core,'1.14.3' if core=='sing-box' else '26.10.21')
        pin=dict(core=core,repository=a.UPSTREAMS[core],tag='v'+source.version,
                 source_commit='e'*40,release_id=31,draft=False,prerelease=False,assets=[])
        names={row['archive_name'] for row in source.meta['upstream']['archives']}
        for index,name in enumerate(sorted(names),1):
            raw=(material/name).read_bytes()
            pin['assets'].append(dict(id=100+index,name=name,size=len(raw),digest='sha256:'+sha(raw),
                state='uploaded',browser_download_url='https://github.com/'+pin['repository']+'/releases/download/'+pin['tag']+'/'+name))
        api='https://api.github.com/repos/'+pin['repository']
        release=dict(id=31,tag_name=pin['tag'],draft=False,prerelease=False,url=api+'/releases/31',
            html_url='https://github.com/'+pin['repository']+'/releases/tag/'+pin['tag'],assets=pin['assets'],
            target_commitish='UNTRUSTED branch must be ignored')
        obj=dict(type='tag' if annotated else 'commit',sha='d'*40 if annotated else pin['source_commit'],
                 url=api+'/git/'+('tags/'+'d'*40 if annotated else 'commits/'+pin['source_commit']))
        routes={api:json.dumps(dict(id=7,full_name=pin['repository'],html_url='https://github.com/'+pin['repository'])).encode(),
            api+'/releases/31':json.dumps(release).encode(),
            api+'/git/ref/tags/'+pin['tag']:json.dumps(dict(ref='refs/tags/'+pin['tag'],object=obj)).encode()}
        if annotated:
            routes[api+'/git/tags/'+'d'*40]=json.dumps(dict(sha='d'*40,object=dict(type='commit',
                sha=pin['source_commit'],url=api+'/git/commits/'+pin['source_commit']))).encode()
        for row in pin['assets']:routes[api+'/releases/assets/'+str(row['id'])]=(material/row['name']).read_bytes()
        source_url='https://codeload.github.com/'+pin['repository']+'/tar.gz/'+pin['source_commit']
        routes[source_url]=source.source_raw
        transport=Transport(routes)
        return source,pin,transport,api,source_url
    def acquire(self,pin,transport,**kwargs):
        with patch.object(f,'CANDIDATES',{pin['core']:pin}):
            return f.acquire(self.root,core=pin['core'],builder_source=SOURCE,destination=Path('inputs'),
                             opener=transport,head=lambda:SOURCE,**kwargs)
    def test_two_cores_tag_peel_deduplicated_assets_and_end_to_end_candidate(self):
        for core in ('sing-box','xray'):
            with self.subTest(core=core):
                # Separate fixture roots for both complete candidate paths.
                case=self.root/core;case.mkdir(mode=0o700);previous=self.root;self.root=case
                try:
                    source,pin,transport,api,url=self.fixture(core,annotated=True)
                    result=self.acquire(pin,transport)
                    self.assertFalse(result['qualified']);self.assertFalse(result['published'])
                    expected=json.loads((case/'inputs/expected.json').read_bytes())
                    self.assertEqual(expected['compatibility']['app_sources'],[SOURCE])
                    receipt=json.loads((case/'inputs/acquisition-receipt.json').read_bytes())
                    self.assertFalse(receipt['source']['independently_published_digest'])
                    self.assertEqual(receipt['source']['digest_basis'],'observed-fixed-commit-verified-https-response')
                    self.assertEqual((case/'inputs/SOURCE.tar.gz').read_bytes(),source.source_raw)
                    source_request=next(request for request in transport.requests if request.full_url==url)
                    self.assertEqual(source_request.get_header('Accept'),'application/octet-stream')
                    self.assertEqual(sum('/releases/assets/' in value for value in transport.calls),4 if core=='sing-box' else 2)
                    paths={}
                    for row in expected['upstream']['archives']:
                        target=row['target'];output='pack-'+target
                        built=b.build_target(case,Path('inputs/expected.json'),target=target,
                            archive=Path('inputs')/row['archive_name'],source=Path('inputs/SOURCE.tar.gz'),destination=Path(output))
                        paths[target]=Path(output)/built['name']
                    aggregate=b.assemble(case,Path('inputs/expected.json'),packs=paths,destination=Path('candidate'))
                    self.assertEqual(len(list((case/'candidate').iterdir())),6)
                    self.assertFalse(aggregate['qualified']);transport.closed(self)
                finally:self.root=previous
    def test_source_and_api_redirects_are_refused_and_closed(self):
        source,pin,transport,api,url=self.fixture()
        for route in (api,url):
            with self.subTest(route=route):
                old=transport.routes[route]
                transport.routes[route]=lambda uri:Response(uri,code=302,headers={'Location':'https://foreign.invalid/private'})
                with self.assertRaises(f.CandidateInputError):self.acquire(pin,transport)
                transport.closed(self);self.assertFalse((self.root/'inputs').exists())
                transport.routes[route]=old
    def test_asset_redirect_allowlist_and_hop_limit(self):
        source,pin,transport,api,url=self.fixture()
        asset=api+'/releases/assets/'+str(pin['assets'][0]['id']);raw=transport.routes[asset]
        cdn='https://release-assets.githubusercontent.com/fixed/test?signature=public'
        transport.routes[asset]=lambda uri:Response(uri,code=302,headers={'Location':cdn})
        transport.routes[cdn]=raw
        self.acquire(pin,transport);transport.closed(self)
        self.assertIn(cdn,transport.calls)
    def test_foreign_asset_redirect_never_receives_request(self):
        source,pin,transport,api,url=self.fixture()
        asset=api+'/releases/assets/'+str(pin['assets'][0]['id'])
        transport.routes[asset]=lambda uri:Response(uri,code=302,headers={'Location':'https://foreign.invalid/private'})
        with self.assertRaises(f.CandidateInputError):self.acquire(pin,transport)
        self.assertFalse(any('foreign.invalid' in route for route in transport.calls));transport.closed(self)
    def test_redirect_loops_stop_at_three_and_close_every_response(self):
        source,pin,transport,api,url=self.fixture()
        asset=api+'/releases/assets/'+str(pin['assets'][0]['id'])
        transport.routes[asset]=lambda uri:Response(uri,code=302,headers={'Location':asset})
        with self.assertRaises(f.CandidateInputError):self.acquire(pin,transport)
        self.assertEqual(transport.calls.count(asset),4);transport.closed(self)
    def test_typed_transient_retry_is_same_url_and_resets_partial_sink(self):
        source,pin,transport,api,url=self.fixture()
        asset=api+'/releases/assets/'+str(pin['assets'][0]['id']);raw=transport.routes[asset]
        transport.routes[asset]=[lambda uri:Response(uri,raw,fail_read=True),raw]
        transport.routes[url]=[lambda uri:Response(uri,code=503),source.source_raw]
        self.acquire(pin,transport)
        self.assertEqual(transport.calls.count(asset),2);self.assertEqual(transport.calls.count(url),2)
        self.assertEqual((self.root/'inputs'/pin['assets'][0]['name']).read_bytes(),raw);transport.closed(self)
    def test_403_429_tls_and_schema_errors_never_retry(self):
        source,pin,transport,api,url=self.fixture();old=transport.routes[api]
        for error in (lambda uri:Response(uri,code=403),lambda uri:Response(uri,code=429),
                      URLError(ssl.SSLError('private certificate failure')),b'{"id":true}'):
            transport.routes[api]=error;transport.calls=[]
            with self.subTest(error=type(error)),self.assertRaises(f.CandidateInputError):self.acquire(pin,transport)
            self.assertEqual(transport.calls,[api]);transport.closed(self)
        transport.routes[api]=old
    def test_metadata_or_peel_drift_and_target_commitish_do_not_authorize_source(self):
        source,pin,transport,api,url=self.fixture()
        repo=json.loads(transport.routes[api]);changed=dict(repo,id=8)
        transport.routes[api]=[json.dumps(repo).encode(),json.dumps(changed).encode()]
        with self.assertRaisesRegex(f.CandidateInputError,'UPSTREAM_METADATA_CHANGED'):self.acquire(pin,transport)
        self.assertFalse((self.root/'inputs').exists());transport.closed(self)
    def test_wrong_digest_length_truncation_limit_and_close_fail_closed(self):
        source,pin,transport,api,url=self.fixture();asset=api+'/releases/assets/'+str(pin['assets'][0]['id']);raw=transport.routes[asset]
        variants=[lambda uri:Response(uri,b'wrong bytes'),lambda uri:Response(uri,raw[:-1],headers={'Content-Length':str(len(raw))}),
                  lambda uri:Response(uri,raw,headers={'Content-Length':'9999999999'}),
                  lambda uri:Response(uri,raw,fail_close=True)]
        for response in variants:
            transport.routes[asset]=response;transport.calls=[]
            with self.assertRaises(f.CandidateInputError):self.acquire(pin,transport)
            self.assertEqual(transport.calls.count(asset),1);self.assertFalse((self.root/'inputs').exists());transport.closed(self)
    def test_builder_source_must_be_actual_head_before_network(self):
        source,pin,transport,api,url=self.fixture()
        with patch.object(f,'CANDIDATES',{pin['core']:pin}):
            with self.assertRaisesRegex(f.CandidateInputError,'BUILDER_SOURCE_MISMATCH'):
                f.acquire(self.root,core=pin['core'],builder_source='b'*40,destination=Path('inputs'),
                          opener=transport,head=lambda:SOURCE)
        self.assertEqual(transport.calls,[])
    def test_token_environment_ignored_and_fixed_opener_requires_verified_tls(self):
        source,pin,transport,api,url=self.fixture()
        with patch.dict(os.environ,dict(GITHUB_TOKEN='secret',GH_TOKEN='secret',HTTPS_PROXY='http://foreign.invalid')):
            self.acquire(pin,transport)
        self.assertFalse(any('foreign.invalid' in route for route in transport.calls))
        transport.closed(self)
        self.assertTrue(f.g._opener().handlers)
    def test_no_overwrite_and_lost_publish_ack_preserve_results(self):
        source,pin,transport,api,url=self.fixture();rename=b.fs._rename_new
        def lost(*args):rename(*args);raise b.fs.CoreStoreError('PUBLISH_FAILED')
        with patch.object(b.fs,'_rename_new',side_effect=lost):
            with self.assertRaises(f.CandidateInputError):self.acquire(pin,transport)
        before={p.name:p.read_bytes() for p in (self.root/'inputs').iterdir()}
        with self.assertRaises(f.CandidateInputError):self.acquire(pin,transport)
        self.assertEqual({p.name:p.read_bytes() for p in (self.root/'inputs').iterdir()},before);transport.closed(self)
    def test_http_error_objects_are_closed_before_same_url_retry(self):
        source,pin,transport,api,url=self.fixture();raw=transport.routes[api]
        body=Response(api,b'private failure',code=503)
        transport.responses.append(body)
        transport.routes[api]=[HTTPError(api,503,'private failure',{},body),raw,raw]
        self.acquire(pin,transport);self.assertTrue(body.closed);transport.closed(self)
    def test_502_504_timeouts_retry_at_most_twice_only_on_original_url(self):
        source,pin,transport,api,url=self.fixture();original=transport.routes[api]
        for first in (lambda uri:Response(uri,code=502),lambda uri:Response(uri,code=504),
                      URLError(TimeoutError('private timeout'))):
            transport.routes[api]=[first,original,original];transport.calls=[]
            # Keep each output isolated without deleting any prior acquisition.
            with patch.object(f,'CANDIDATES',{pin['core']:pin}):
                f.acquire(self.root,core=pin['core'],builder_source=SOURCE,
                    destination=Path('retry-'+str(len(list(self.root.glob('retry-*'))))),
                    opener=transport,head=lambda:SOURCE)
            self.assertEqual(transport.calls.count(api),3);transport.closed(self)
        transport.routes[api]=lambda uri:Response(uri,code=503);transport.calls=[]
        with self.assertRaises(f.CandidateInputError):self.acquire(pin,transport)
        self.assertEqual(transport.calls,[api,api]);transport.closed(self)
    def test_metadata_limit_and_tag_release_asset_schema_fail_without_origin_shortcuts(self):
        source,pin,transport,api,url=self.fixture();original=transport.routes[api]
        transport.routes[api]=lambda uri:Response(uri,b'{}',headers={'Content-Length':'1000001'})
        with self.assertRaises(f.CandidateInputError):self.acquire(pin,transport)
        self.assertEqual(transport.calls,[api]);transport.closed(self)
        transport.routes[api]=original
        release_url=api+'/releases/31';release=json.loads(transport.routes[release_url])
        variants=[]
        for change in (dict(draft=True),dict(prerelease=True),dict(id=True)):
            variants.append(dict(release,**change))
        bad=deepcopy(release);bad['assets'][0]['state']='new';variants.append(bad)
        for value in variants:
            transport.routes[release_url]=json.dumps(value).encode();transport.calls=[]
            with self.assertRaises(f.CandidateInputError):self.acquire(pin,transport)
            self.assertEqual(transport.calls,[api,release_url]);transport.closed(self)
        transport.routes[release_url]=json.dumps(release).encode()
        ref_url=api+'/git/ref/tags/'+pin['tag'];ref=json.loads(transport.routes[ref_url])
        ref['object']['url']='https://foreign.invalid/private'
        transport.routes[ref_url]=json.dumps(ref).encode();transport.calls=[]
        with self.assertRaises(f.CandidateInputError):self.acquire(pin,transport)
        self.assertFalse(any('foreign.invalid' in route for route in transport.calls));transport.closed(self)
    def test_existing_output_rejected_before_network_and_workflow_python_is_syntax_only(self):
        source,pin,transport,api,url=self.fixture()
        (self.root/'inputs').mkdir(mode=0o700)
        (self.root/'inputs/foreign').write_bytes(b'preserve partial')
        with self.assertRaises(f.CandidateInputError):self.acquire(pin,transport)
        self.assertEqual(transport.calls,[])
        workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/core-component-candidate.yml').read_text()
        self.assertIn('contents: read',workflow);self.assertNotIn('contents: write',workflow)
        self.assertIn('persist-credentials: false',workflow)
        self.assertNotIn('secrets.',workflow);self.assertNotIn('gh release',workflow)
        code=workflow.split("python - <<'PY'",1)[1].split('\n          PY',1)[0]
        import textwrap
        compile(textwrap.dedent(code),'<workflow-candidate-python>','exec')

    def test_cli_has_no_url_token_or_source_override(self):
        error=io.StringIO()
        with redirect_stderr(error):result=f.main(['--url','https://private.invalid/secret'])
        self.assertEqual(result,1)
        self.assertEqual(error.getvalue(),'Candidate acquisition rejected: CLI_ARGUMENTS\n')

    def test_small_evidence_binds_run_and_uploaded_digest_without_overwriting_existing_receipt(self):
        source,pin,transport,_,_=self.fixture()
        self.acquire(pin,transport)
        raw_expected=(self.root/'inputs/expected.json').read_bytes()
        raw_acquisition=(self.root/'inputs/acquisition-receipt.json').read_bytes()
        context=dict(repository=a.REPOSITORY,repository_id=a.REPOSITORY_ID,run_id=123,run_attempt=1)
        production=dict(schema=1,kind='core-candidate-production-v1',qualified=False,published=False,
                        builder_source=SOURCE,core='sing-box',context=context,
                        expected_sha256=sha(raw_expected),acquisition_receipt_sha256=sha(raw_acquisition))
        target=self.root/'production-receipt.json'
        workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/core-component-candidate.yml').read_text()
        import textwrap
        code=compile(textwrap.dedent(workflow.split("python - <<'PY_EVIDENCE'",1)[1]
                     .split('\n          PY_EVIDENCE',1)[0]),'<workflow-small-evidence>','exec')
        env=dict(CANDIDATE_WORK=str(self.root),EXPECTED_SOURCE=SOURCE,CORE='sing-box',
                 GITHUB_REPOSITORY=a.REPOSITORY,GITHUB_REPOSITORY_ID=str(a.REPOSITORY_ID),
                 GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1',CANDIDATE_ARTIFACT_ID='71',
                 CANDIDATE_ARTIFACT_DIGEST='sha256:'+'b'*64,
                 CANDIDATE_ARTIFACT_NAME='core-candidate-sing-box-'+SOURCE+'-123-1')
        evidence=self.root/'candidate-evidence.json'
        with patch.dict(os.environ,env),redirect_stdout(io.StringIO()):
            bad=dict(production,schema=True)
            target.write_text(json.dumps(bad))
            with self.assertRaises(AssertionError):exec(code,{})
            self.assertFalse(evidence.exists())
            bad=deepcopy(production);bad['context']['run_attempt']=2
            target.write_text(json.dumps(bad))
            with self.assertRaises(AssertionError):exec(code,{})
            self.assertFalse(evidence.exists())
            target.write_text(json.dumps(production))
            with patch.dict(os.environ,{'CANDIDATE_ARTIFACT_DIGEST':'invalid'}),self.assertRaises(AssertionError):
                exec(code,{})
            self.assertFalse(evidence.exists())
            exec(code,{})
            raw=evidence.read_bytes();value=json.loads(raw)
            self.assertEqual(value['context'],context)
            self.assertEqual(value['producer_artifact'],dict(id=71,name=env['CANDIDATE_ARTIFACT_NAME'],digest='sha256:'+'b'*64))
            self.assertIs(value['qualified'],False)
            self.assertFalse(value['acquisition']['source']['independently_published_digest'])
            with self.assertRaises(FileExistsError):exec(code,{})
            self.assertEqual(evidence.read_bytes(),raw)


if __name__=='__main__':unittest.main()
