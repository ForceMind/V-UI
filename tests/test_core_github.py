"""Offline official-API/stream fakes. No network, installed core or root writes."""
from contextlib import contextmanager,redirect_stderr,redirect_stdout
from copy import deepcopy
import errno
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import zipfile
from unittest import TestCase,main
from unittest.mock import Mock,patch
from urllib.error import HTTPError,URLError

from app.core_updates import artifacts as a,github as g
from test_core_artifacts import Fixture,APP,BUILD


def encoded(value):return json.dumps(value,separators=(',',':')).encode()


class Response:
    def __init__(self,url,raw=b'',status=200,headers=None,*,chunk=None,timeout_after=None):
        self.url,self.stream,self.status=url,io.BytesIO(raw),status
        self.headers={} if headers is None else headers
        self.chunk,self.timeout_after=chunk,timeout_after
        self.reads=[];self.closed=False
    def geturl(self):return self.url
    def getcode(self):return self.status
    def read1(self,size):
        self.reads.append(size)
        if self.timeout_after is not None and len(self.reads)>self.timeout_after:raise socket.timeout('fake body timeout')
        return self.stream.read(min(size,self.chunk) if self.chunk else size)
    read=read1
    def close(self):self.closed=True;self.stream.close()


class Transport:
    def __init__(self,f=None):
        self.f=f or Fixture();f=self.f
        self.repo={'id':a.REPOSITORY_ID,'full_name':a.REPOSITORY,'private':False,'ignored':{'score':0.25}}
        self.release={'id':31,'url':g.API+'/releases/31','html_url':g.WEB+'/releases/tag/'+f.tag,
            'tag_name':f.tag,'draft':False,'prerelease':False,'published_at':f.authority['published_at'],
            'target_commitish':'THIS-IS-NOT-A-SOURCE','body':'do-not-echo-untrusted-body',
            'assets':[{**item,'state':'uploaded','url':g.API+'/releases/assets/'+str(item['id']),
                'browser_download_url':g.WEB+'/releases/download/'+f.tag+'/'+item['name']} for item in f.authority['assets']]}
        self.ref={'ref':'refs/tags/'+f.tag,'url':g.API+'/git/refs/tags/'+f.tag,
            'object':{'type':'commit','sha':BUILD,'url':g.API+'/git/commits/'+BUILD}}
        self.tags={};self.pages={1:([],{})};self.actions={};self.calls=[];self.responses=[];self.counts={};self.on_open=None
        self.data={'CORE-RELEASE.json':f.manifest_raw,'SHA256SUMS':f.table_raw,
            **{a._pack_name(f.core,f.version,t):f.pack_bytes[t] for t in a.TARGETS}}
    def open(self,request,timeout):
        url=request.full_url;self.calls.append((url,dict(request.header_items()),timeout));self.counts[url]=self.counts.get(url,0)+1
        if self.on_open:self.on_open(url,self.counts[url])
        pending=self.actions.get(url,[])
        if pending:
            action=pending.pop(0)
            if isinstance(action,BaseException):raise action
            response=action(url) if callable(action) else action
        elif url==g.API:response=Response(url,encoded(self.repo))
        elif url==g.API+'/releases/31':response=Response(url,encoded(self.release))
        elif url==g.API+'/git/ref/tags/'+self.f.tag:response=Response(url,encoded(self.ref))
        elif url.startswith(g.API+'/git/tags/'):
            response=Response(url,encoded(self.tags[url.rsplit('/',1)[1]]))
        elif url.startswith(g.API+'/releases?per_page=20&page='):
            page=int(url.rsplit('=',1)[1]);rows,headers=self.pages.get(page,([],{}));response=Response(url,encoded(rows),headers=headers)
        elif url.startswith(g.API+'/releases/assets/'):
            identifier=int(url.rsplit('/',1)[1]);asset=next(item for item in self.f.authority['assets'] if item['id']==identifier)
            response=Response(url,self.data[asset['name']])
        else:raise AssertionError('Unexpected fake URL')
        self.responses.append(response);return response
    def asset_url(self,name):return g.API+'/releases/assets/'+str(next(item['id'] for item in self.f.authority['assets'] if item['name']==name))
    def candidate(self):return g.GitHubCoreClient(opener=self).inspect(31,app_source=APP)
    def closed(self,test):test.assertTrue(all(response.closed for response in self.responses))


def hint(identifier,tag,draft=False,prerelease=False):
    return {'id':identifier,'tag_name':tag,'url':g.API+'/releases/'+str(identifier),
            'draft':draft,'prerelease':prerelease,'published_at':'2026-11-08T00:00:00Z'}


class GitHubCoreTests(TestCase):
    @contextmanager
    def sink(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'sink';fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
            try:yield path,fd
            finally:os.close(fd)

    def test_inspect_uses_official_repo_and_commit_ref_not_target_commitish_then_rechecks(self):
        for core,version in (('sing-box','1.14.3'),('xray','26.10.21'),('sing-box','1.15.0')):
            t=Transport(Fixture(core,version));candidate=t.candidate()
            self.assertEqual(candidate.tag_source,BUILD);self.assertEqual(candidate.verified_release.builder_source,BUILD)
            self.assertEqual(candidate.authority,a.decode_authority(encoded(t.f.authority)))
            self.assertEqual(candidate.manifest,t.f.manifest_raw);self.assertEqual(candidate.checksums,t.f.table_raw)
            self.assertEqual(t.counts[g.API],2);self.assertEqual(t.counts[g.API+'/releases/31'],2)
            self.assertNotIn('do-not-echo-untrusted-body',repr(candidate));self.assertNotIn('THIS-IS-NOT-A-SOURCE',repr(candidate))
            for url,headers,timeout in t.calls:
                self.assertTrue(url.startswith(g.API));self.assertLessEqual(timeout,10)
                self.assertTrue(set(key.lower() for key in headers).isdisjoint({'authorization','cookie','proxy-authorization'}))
            self.assertFalse(any(url.endswith('/latest') for url,_,_ in t.calls));t.closed(self)

    def test_annotated_peel_is_bounded_cycle_safe_and_commit_only(self):
        t=Transport();first='e'*40;second='f'*40
        t.ref['object']={'type':'tag','sha':first,'url':g.API+'/git/tags/'+first}
        t.tags[first]={'sha':first,'url':g.API+'/git/tags/'+first,'object':{'type':'tag','sha':second,'url':g.API+'/git/tags/'+second}}
        t.tags[second]={'sha':second,'url':g.API+'/git/tags/'+second,'object':{'type':'commit','sha':BUILD,'url':g.API+'/git/commits/'+BUILD}}
        self.assertEqual(t.candidate().tag_source,BUILD)
        for mode in ('tree','blob','cycle','depth','wrong-sha','wrong-ref','foreign-url'):
            t=Transport()
            if mode in ('tree','blob'):t.ref['object']['type']=mode
            elif mode=='wrong-ref':t.ref['ref']='refs/tags/other'
            elif mode=='foreign-url':t.ref['object']['url']='https://api.github.com/repos/fork/repo/git/commits/'+BUILD
            else:
                t.ref['object']={'type':'tag','sha':first,'url':g.API+'/git/tags/'+first}
                for index,source in enumerate((first,second,'1'*40,'2'*40,'3'*40)):
                    target=source if mode=='cycle' else (second,'1'*40,'2'*40,'3'*40,'4'*40)[index]
                    t.tags[source]={'sha':BUILD if mode=='wrong-sha' else source,'url':g.API+'/git/tags/'+source,
                        'object':{'type':'tag','sha':target,'url':g.API+'/git/tags/'+target}}
            with self.subTest(mode=mode),self.assertRaises(g.CoreGitHubError):t.candidate()
            self.assertLessEqual(len([url for url,_,_ in t.calls if '/git/tags/' in url]),4);t.closed(self)

    def test_repository_release_asset_status_url_size_digest_and_app_compatibility_are_strict(self):
        for mode in ('repo-id','repo-name','private','release-id','release-url','draft','prerelease','application-tag',
                     'asset-id','asset-url','browser-url','asset-state','asset-size','digest','extra-asset','app'):
            t=Transport()
            if mode=='repo-id':t.repo['id']+=1
            elif mode=='repo-name':t.repo['full_name']='fork/V-UI'
            elif mode=='private':t.repo['private']=True
            elif mode=='release-id':t.release['id']=True
            elif mode=='release-url':t.release['url']=g.API+'/releases/32'
            elif mode in ('draft','prerelease'):t.release[mode]=True
            elif mode=='application-tag':t.release['tag_name']='v0.4.24'
            elif mode=='extra-asset':t.release['assets'].append(deepcopy(t.release['assets'][0]))
            elif mode.startswith('asset-') or mode in ('browser-url','digest'):
                row=t.release['assets'][0]
                key={'asset-id':'id','asset-url':'url','browser-url':'browser_download_url','asset-state':'state','asset-size':'size','digest':'digest'}[mode]
                row[key]={'id':True,'url':'https://api.github.com/repos/fork/V-UI/releases/assets/1',
                    'browser_download_url':'https://github.com/fork/V-UI/file','state':'new','size':True,'digest':'sha256:'+'A'*64}[key]
            with self.subTest(mode=mode),self.assertRaises(g.CoreGitHubError):
                g.GitHubCoreClient(opener=t).inspect(31,app_source='f'*40 if mode=='app' else APP)
            t.closed(self)

    def test_context_change_is_rejected_but_json_and_asset_order_are_not_identity_changes(self):
        for mode in ('asset-id','asset-size','asset-digest','tag-source','order'):
            t=Transport()
            def change(url,count):
                if url==g.API+'/releases/31' and count==2:
                    if mode=='order':t.release['assets'].reverse()
                    elif mode=='tag-source':t.ref['object']={'type':'commit','sha':'e'*40,'url':g.API+'/git/commits/'+'e'*40}
                    else:
                        key=mode.split('-')[1];t.release['assets'][0][key]=t.release['assets'][0][key]+100 if key in ('id','size') else 'sha256:'+'e'*64
                        if key=='id':t.release['assets'][0]['url']=g.API+'/releases/assets/'+str(t.release['assets'][0]['id'])
            t.on_open=change
            with self.subTest(mode=mode):
                if mode=='order':t.candidate()
                else:
                    with self.assertRaisesRegex(g.CoreGitHubError,'CONTEXT_CHANGED'):t.candidate()
            t.closed(self)

    def test_metadata_redirect_never_follows_and_body_is_not_read(self):
        t=Transport();response=Response(g.API,status=302,headers={'Location':g.API+'/other'})
        t.actions[g.API]=[response]
        with self.assertRaisesRegex(g.CoreGitHubError,'METADATA_REDIRECT'):t.candidate()
        self.assertEqual(len(t.calls),1);self.assertEqual(response.reads,[]);self.assertTrue(response.closed)

    def test_small_asset_only_follows_official_https_hosts_three_hops_and_closes_every_response(self):
        t=Transport();url=t.asset_url('CORE-RELEASE.json');cdn='https://release-assets.githubusercontent.com/fake?signature=secret'
        redirect=Response(url,status=302,headers={'Location':cdn});response=Response(cdn,t.f.manifest_raw)
        t.actions[url]=[redirect];t.actions[cdn]=[response]
        candidate=t.candidate();self.assertEqual(candidate.manifest,t.f.manifest_raw)
        self.assertTrue(redirect.closed);self.assertEqual(redirect.reads,[]);t.closed(self)
        for target in ('http://objects.githubusercontent.com/file','https://example.invalid/file',
                       'https://user:password@objects.githubusercontent.com/file',
                       g.API+'/releases/assets/999','https://api.github.com/repos/other/repo/releases/assets/1'):
            t=Transport();url=t.asset_url('CORE-RELEASE.json');t.actions[url]=[Response(url,status=302,headers={'Location':target})]
            with self.subTest(target=target),self.assertRaises(g.CoreGitHubError):t.candidate()
            self.assertFalse(any(call[0]==target for call in t.calls));t.closed(self)
        t=Transport();url=t.asset_url('CORE-RELEASE.json');t.actions[url]=[Response(url,status=302,headers={'Location':url}) for _ in range(4)]
        with self.assertRaisesRegex(g.CoreGitHubError,'REDIRECT_LIMIT'):t.candidate()
        self.assertEqual(t.counts[url],4);t.closed(self)

    def test_get_transient_or_explicit_timeout_retries_exact_same_url_once_after_close(self):
        for failure in (502,503,504,TimeoutError('fake'),socket.timeout('fake'),URLError(socket.timeout('fake'))):
            t=Transport()
            if type(failure) is int:
                stream=io.BytesIO(b'private error body');error=HTTPError(g.API,failure,'private',{},stream)
                t.actions[g.API]=[error]
                def opened(url,count):
                    if url==g.API and count==2:self.assertTrue(stream.closed)
                t.on_open=opened
            else:t.actions[g.API]=[failure]
            t.candidate();self.assertEqual(t.counts[g.API],3);t.closed(self)
            self.assertEqual(t.calls[0][0],t.calls[1][0])
        t=Transport();t.actions[g.API]=[Response(g.API,status=503),Response(g.API,status=504)]
        with self.assertRaisesRegex(g.CoreGitHubError,'HTTP_504'):t.candidate()
        self.assertEqual(t.counts[g.API],2);t.closed(self)

    def test_forbidden_rate_limit_ssl_generic_url_json_schema_and_digest_are_not_retried(self):
        for failure in (403,404,429,500,ssl.SSLCertVerificationError('private TLS'),URLError('timed out'),
                        URLError(ssl.SSLCertVerificationError('private TLS')),OSError('private OS')):
            t=Transport();t.actions[g.API]=[Response(g.API,status=failure) if type(failure) is int else failure]
            with self.subTest(failure=type(failure).__name__),self.assertRaises(g.CoreGitHubError):t.candidate()
            self.assertEqual(t.counts[g.API],1);t.closed(self)
        for raw in (b'{"id":1,"id":2}',b'{"x":NaN}',b'{"x":Infinity}',b'\xff',b'{"x":"\\ud800"}',b'{"x":'+b'['*30+b'0'+b']'*30+b'}'):
            t=Transport();t.actions[g.API]=[Response(g.API,raw)]
            with self.subTest(raw=raw[:25]),self.assertRaises(g.CoreGitHubError):t.candidate()
            self.assertEqual(t.counts[g.API],1);t.closed(self)
        t=Transport();url=t.asset_url('CORE-RELEASE.json');t.actions[url]=[Response(url,b'bad bytes')]
        with self.assertRaisesRegex(g.CoreGitHubError,'ASSET_DIGEST'):t.candidate()
        self.assertEqual(t.counts[url],1);t.closed(self)

    def test_partial_metadata_timeout_is_closed_then_discarded_without_byte_concatenation(self):
        t=Transport();first=Response(g.API,b'invalid-prefix',chunk=5,timeout_after=1);t.actions[g.API]=[first]
        def opened(url,count):
            if url==g.API and count==2:self.assertTrue(first.closed)
        t.on_open=opened;t.candidate();self.assertEqual(t.counts[g.API],3);t.closed(self)

    def test_response_url_and_content_size_are_checked_without_echoing_body_or_signed_url(self):
        for mode in ('url','length','body'):
            t=Transport()
            if mode=='url':response=Response('https://example.invalid/private?secret=sentinel',b'private body')
            elif mode=='length':response=Response(g.API,b'private body',headers={'Content-Length':str(g.MAX_METADATA+1)})
            else:response=Response(g.API,b'x'*(g.MAX_METADATA+1))
            t.actions[g.API]=[response]
            with self.assertRaises(g.CoreGitHubError) as error:t.candidate()
            self.assertNotIn('private',str(error.exception));self.assertNotIn('sentinel',str(error.exception));t.closed(self)

    def test_discovery_filters_application_and_unstable_tags_and_returns_explicit_completeness(self):
        t=Transport();t.pages[1]=([hint(1,'v0.4.24'),hint(2,'core-xray-v26.10.21-r1'),
            hint(3,'core-sing-box-v1.14.3-r1',draft=True),hint(4,'core-sing-box-v1.14.3-r2',prerelease=True),
            hint(5,'core-sing-box-v1.15.0-r1')],{})
        result=g.GitHubCoreClient(opener=t).discover('sing-box')
        self.assertFalse(result.truncated);self.assertEqual((result.pages,result.examined),(1,5))
        self.assertEqual([row.release_id for row in result.hints],[5]);t.closed(self)

    def test_discovery_limits_are_truncated_even_when_no_candidate_not_claimed_no_update(self):
        t=Transport()
        for page in range(1,6):t.pages[page]=([hint((page-1)*20+index+1,'v0.0.'+str((page-1)*20+index)) for index in range(20)],{})
        result=g.GitHubCoreClient(opener=t).discover('sing-box')
        self.assertEqual((result.truncated,result.examined,result.pages,result.hints),(True,100,5,()))
        self.assertFalse(any('page=6' in url for url,_,_ in t.calls))
        t=Transport()
        for page in (1,2):t.pages[page]=([hint((page-1)*20+index+1,'core-sing-box-v1.14.3-r'+str((page-1)*20+index+1)) for index in range(20)],{})
        result=g.GitHubCoreClient(opener=t).discover('sing-box')
        self.assertTrue(result.truncated);self.assertEqual(len(result.hints),32);self.assertEqual(result.pages,2)

    def test_discovery_bad_link_duplicate_schema_and_http_fail_without_partial_success(self):
        for mode in ('link','duplicate','badtag','schema','http'):
            t=Transport();first=hint(1,'core-sing-box-v1.14.3-r1')
            if mode=='link':t.pages[1]=([first],{'Link':'<https://example.invalid/releases?page=2>; rel="next"'})
            elif mode=='duplicate':t.pages[1]=([first,first],{})
            elif mode=='badtag':t.pages[1]=([hint(1,'core-sing-box-v01.2.3-r1')],{})
            elif mode=='schema':t.pages[1]=([{'id':True}],{})
            else:t.actions[g.API+'/releases?per_page=20&page=1']=[Response(g.API+'/releases?per_page=20&page=1',status=403)]
            with self.subTest(mode=mode),self.assertRaises(g.CoreGitHubError):g.GitHubCoreClient(opener=t).discover('sing-box')
            t.closed(self)
        t=Transport();t.pages[1]=([hint(1,'v0.4.24')],{'Link':'<'+g.API+'/releases?per_page=20&page=2>; rel="next"'})
        t.pages[2]=([hint(2,'core-sing-box-v1.14.3-r1')],{'Link':'<'+g.API+'/releases?per_page=20&page=1>; rel="prev"'})
        self.assertEqual(len(g.GitHubCoreClient(opener=t).discover('sing-box').hints),1)

    def test_deadline_cancel_and_invalid_inputs_do_not_open_or_reclassify_as_no_update(self):
        for args,code in (({'deadline':time.monotonic()-1},'DEADLINE'),({'deadline':True},'DEADLINE_INPUT'),
                ({'deadline':float('nan')},'DEADLINE_INPUT'),({'deadline':10**1000},'DEADLINE_INPUT'),
                ({'stop':lambda:True},'CANCELLED'),({'stop':lambda:None},'CANCEL_INPUT')):
            t=Transport()
            with self.subTest(code=code),self.assertRaisesRegex(g.CoreGitHubError,code):g.GitHubCoreClient(opener=t).inspect(31,app_source=APP,**args)
            self.assertEqual(t.calls,[])
        t=Transport();g.GitHubCoreClient(opener=t).inspect(31,app_source=APP,deadline=time.monotonic()+2)
        self.assertTrue(all(0<timeout<=2 for _,_,timeout in t.calls))
        for identifier in (True,0,'https://example.invalid',-1):
            t=Transport()
            with self.assertRaises(g.CoreGitHubError):g.GitHubCoreClient(opener=t).inspect(identifier,app_source=APP)
            self.assertEqual(t.calls,[])

    def test_default_verified_tls_no_proxy_no_global_opener_and_no_authentication_environment(self):
        with patch.object(g,'build_opener') as build:
            g._opener()
        handlers=build.call_args.args
        self.assertEqual(handlers[0].proxies,{})
        self.assertTrue(handlers[1]._context.check_hostname);self.assertEqual(handlers[1]._context.verify_mode,ssl.CERT_REQUIRED)
        self.assertIsNone(handlers[2].redirect_request(None,None,302,'',{},'https://elsewhere'))
        t=Transport();capture=io.StringIO()
        class HiddenEnvironment(dict):
            def __getitem__(self,key):raise AssertionError('Credential environment must not be consulted')
            def get(self,*args):raise AssertionError('Credential environment must not be consulted')
        with patch.object(g.os,'environ',HiddenEnvironment(GITHUB_TOKEN='never-send-token',HTTPS_PROXY='http://never-use-proxy')),redirect_stdout(capture),redirect_stderr(capture):t.candidate()
        self.assertEqual(capture.getvalue(),'');self.assertNotIn('never-send-token',repr(t.calls))

    def test_explicit_pack_download_retains_caller_fd_offset_and_rechecks_context(self):
        t=Transport();client=g.GitHubCoreClient(opener=t);candidate=client.inspect(31,app_source=APP)
        with self.sink() as (path,fd):
            identity=client.download_pack(candidate,'x86_64-gnu',fd)
            self.assertEqual(path.read_bytes(),t.f.pack_bytes['x86_64-gnu']);self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),0)
            self.assertEqual(identity.pack_sha256,dict((p.target,p.sha256) for p in candidate.verified_release.packs)['x86_64-gnu'])
            self.assertEqual(os.fstat(fd).st_nlink,1)
        self.assertEqual(t.counts[g.API+'/releases/31'],4);t.closed(self)

    def test_sink_is_empty_private_owned_single_link_nonappend_readwrite_fd_not_an_arbitrary_file(self):
        for mode in ('content','permissions','append','readonly','link','offset'):
            t=Transport();client=g.GitHubCoreClient(opener=t);candidate=client.inspect(31,app_source=APP);before=len(t.calls)
            with self.subTest(mode=mode),self.sink() as (path,fd):
                selected=fd;other=None
                if mode=='content':os.pwrite(fd,b'preserve old',0)
                elif mode=='permissions':path.chmod(0o644)
                elif mode in ('append','readonly'):
                    other=os.open(path,os.O_RDWR|os.O_APPEND if mode=='append' else os.O_RDONLY);selected=other
                elif mode=='link':os.link(path,path.with_name('other'))
                else:os.lseek(fd,1,os.SEEK_SET)
                original=path.read_bytes()
                try:
                    with self.assertRaises(g.CoreGitHubError):client.download_pack(candidate,'x86_64-gnu',selected)
                finally:
                    if other is not None:os.close(other)
                self.assertEqual(path.read_bytes(),original);self.assertEqual(len(t.calls),before)

    def test_partial_pack_timeout_restarts_empty_after_close_and_never_appends(self):
        t=Transport();client=g.GitHubCoreClient(opener=t);candidate=client.inspect(31,app_source=APP)
        url=t.asset_url(a._pack_name(t.f.core,t.f.version,'x86_64-gnu'))
        first=Response(url,t.f.pack_bytes['x86_64-gnu'],chunk=8,timeout_after=1);t.actions[url]=[first]
        with self.sink() as (path,fd):
            def opened(next_url,count):
                if next_url==url and count==2:self.assertTrue(first.closed);self.assertEqual(os.fstat(fd).st_size,0)
            t.on_open=opened;client.download_pack(candidate,'x86_64-gnu',fd)
            self.assertEqual(path.read_bytes(),t.f.pack_bytes['x86_64-gnu']);self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),0)
        self.assertEqual(t.counts[url],2);t.closed(self)

    def test_second_timeout_failure_keeps_untrusted_partial_for_caller_and_does_not_close_fd(self):
        t=Transport();client=g.GitHubCoreClient(opener=t);candidate=client.inspect(31,app_source=APP)
        url=t.asset_url(a._pack_name(t.f.core,t.f.version,'x86_64-gnu'))
        t.actions[url]=[Response(url,t.f.pack_bytes['x86_64-gnu'],chunk=8,timeout_after=1) for _ in range(2)]
        with self.sink() as (path,fd):
            with self.assertRaisesRegex(g.CoreGitHubError,'IO_TIMEOUT'):client.download_pack(candidate,'x86_64-gnu',fd)
            self.assertEqual(path.read_bytes(),t.f.pack_bytes['x86_64-gnu'][:8]);self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),0)
        self.assertEqual(t.counts[url],2);t.closed(self)

    def test_pack_wrong_hash_local_write_error_or_post_download_api_change_never_returns_success(self):
        for fault in ('hash','write','context','sink-change'):
            t=Transport();client=g.GitHubCoreClient(opener=t);candidate=client.inspect(31,app_source=APP)
            url=t.asset_url(a._pack_name(t.f.core,t.f.version,'x86_64-gnu'))
            if fault=='hash':t.actions[url]=[Response(url,b'x'*len(t.f.pack_bytes['x86_64-gnu']))]
            with self.subTest(fault=fault),self.sink() as (path,fd):
                def opened(current,count):
                    if current==g.API+'/releases/31' and count==4:
                        if fault=='context':
                            t.release['assets'][0]['id']+=100;t.release['assets'][0]['url']=g.API+'/releases/assets/'+str(t.release['assets'][0]['id'])
                        if fault=='sink-change':os.pwrite(fd,b'changed',0)
                t.on_open=opened
                if fault=='write':
                    with patch.object(g.os,'pwrite',side_effect=socket.timeout('local sink timeout')),self.assertRaisesRegex(g.CoreGitHubError,'SINK_WRITE'):
                        client.download_pack(candidate,'x86_64-gnu',fd)
                else:
                    with self.assertRaises(g.CoreGitHubError):client.download_pack(candidate,'x86_64-gnu',fd)
                self.assertGreaterEqual(os.fstat(fd).st_size,0);self.assertEqual(t.counts[url],1)
            t.closed(self)

    def test_redirect_budget_is_shared_across_both_original_url_attempts(self):
        t=Transport();url=t.asset_url('CORE-RELEASE.json')
        first='https://objects.githubusercontent.com/one';second='https://objects.githubusercontent.com/two'
        t.actions[url]=[Response(url,status=302,headers={'Location':first}) for _ in range(2)]
        t.actions[first]=[Response(first,status=302,headers={'Location':second}) for _ in range(2)]
        t.actions[second]=[Response(second,status=503)]
        with self.assertRaisesRegex(g.CoreGitHubError,'REDIRECT_LIMIT'):t.candidate()
        self.assertEqual(t.counts[url],2);self.assertEqual(t.counts[first],2);self.assertEqual(t.counts[second],1)
        t.closed(self)

    def test_headers_are_case_insensitive_and_read1_is_preferred_with_small_read_fallback(self):
        t=Transport();t.pages[1]=([hint(1,'v0.4.24')],{'link':'<'+g.API+'/releases?page=2&per_page=20>; rel="next"'})
        t.pages[2]=([hint(2,'core-sing-box-v1.14.3-r1')],{})
        self.assertEqual(len(g.GitHubCoreClient(opener=t).discover('sing-box').hints),1)
        t=Transport();response=Response(g.API,encoded(t.repo))
        response.read=Mock(side_effect=AssertionError('must use read1'))
        t.actions[g.API]=[response];t.candidate();response.read.assert_not_called();t.closed(self)
        t=Transport();large={**t.repo,'ignored_large_field':'x'*100000};response=Response(g.API,encoded(large))
        response.read1=None;t.actions[g.API]=[response];t.candidate()
        self.assertTrue(response.reads);self.assertLessEqual(max(response.reads),65536);t.closed(self)
        t=Transport();response=Response(g.API,b'{}',headers={'content-length':str(g.MAX_METADATA+1)})
        t.actions[g.API]=[response]
        with self.assertRaisesRegex(g.CoreGitHubError,'HTTP_SIZE'):t.candidate()
        self.assertEqual(response.reads,[]);t.closed(self)

    def test_broken_http_body_is_a_fixed_nonretryable_transport_error(self):
        for failure in (http.client.IncompleteRead(b'do-not-echo-private'),EOFError('do-not-echo-private')):
            t=Transport();response=Response(g.API)
            def read(size):raise failure
            response.read1=read;t.actions[g.API]=[response]
            with self.subTest(failure=type(failure).__name__),self.assertRaisesRegex(g.CoreGitHubError,'TRANSPORT_FAILED') as error:t.candidate()
            self.assertNotIn('private',str(error.exception));self.assertEqual(t.counts[g.API],1);t.closed(self)

    def test_pack_read1_and_pwrite_chunks_are_bounded_for_a_large_synthetic_archive(self):
        f=Fixture();target='x86_64-gnu';binary=b'not-an-ELF; fake data\n'*(a.CHUNK//8)
        f.materials[target]['bin/sing-box']=binary
        provenance=json.loads(f.materials[target]['PROVENANCE.json']);provenance['binary_sha256']=hashlib.sha256(binary).hexdigest()
        f.materials[target]['PROVENANCE.json']=encoded(provenance);f.refresh_pack(target)
        raw=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(f.pack_bytes[target])) as old,zipfile.ZipFile(raw,'w') as new:
            for entry in old.infolist():
                content=old.read(entry);entry.compress_type=zipfile.ZIP_STORED;new.writestr(entry,content)
        f.pack_bytes[target]=raw.getvalue();f.refresh_release()
        t=Transport(f);client=g.GitHubCoreClient(opener=t);candidate=client.inspect(31,app_source=APP)
        url=t.asset_url(a._pack_name(f.core,f.version,target));response=Response(url,f.pack_bytes[target]);t.actions[url]=[response]
        original=os.pwrite;writes=[]
        def write(fd,content,offset):
            self.assertLessEqual(len(content),a.CHUNK);writes.append(len(content));return original(fd,content,offset)
        with self.sink() as (path,fd),patch.object(g.os,'pwrite',side_effect=write):
            client.download_pack(candidate,target,fd)
            self.assertEqual(path.read_bytes(),f.pack_bytes[target])
        self.assertGreater(len(writes),1);self.assertLessEqual(max(response.reads),a.CHUNK);t.closed(self)

    def test_between_block_cancel_or_deadline_stops_download_and_closes_response_without_success(self):
        for fault in ('cancel','deadline'):
            t=Transport();client=g.GitHubCoreClient(opener=t);candidate=client.inspect(31,app_source=APP)
            url=t.asset_url(a._pack_name(t.f.core,t.f.version,'x86_64-gnu'));response=Response(url,t.f.pack_bytes['x86_64-gnu'],chunk=8)
            stopped=[False];clock=[0.0];original=response.read1
            def read(size):
                value=original(size);stopped[0]=True;clock[0]=2.0;return value
            response.read1=read;t.actions[url]=[response]
            with self.subTest(fault=fault),self.sink() as (path,fd):
                with patch.object(g.time,'monotonic',side_effect=lambda:clock[0]),self.assertRaisesRegex(g.CoreGitHubError,'CANCELLED' if fault=='cancel' else 'DEADLINE'):
                    client.download_pack(candidate,'x86_64-gnu',fd,**({'stop':lambda:stopped[0]} if fault=='cancel' else {'deadline':1.0}))
                self.assertEqual(os.fstat(fd).st_size,0);self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),0)
            self.assertTrue(response.closed);self.assertEqual(t.counts[url],1)

    def test_sink_duplicate_close_failure_never_closes_caller_or_reports_success(self):
        t=Transport();client=g.GitHubCoreClient(opener=t);candidate=client.inspect(31,app_source=APP)
        original_dup=os.dup;original_close=os.close;owned=[]
        def duplicate(fd):
            value=original_dup(fd)
            if not owned:owned.append(value)
            return value
        def close(fd):
            original_close(fd)
            if fd==owned[0]:raise OSError('fake close acknowledgement failure')
        with self.sink() as (path,fd):
            with patch.object(g.os,'dup',side_effect=duplicate),patch.object(g.os,'close',side_effect=close),self.assertRaisesRegex(g.CoreGitHubError,'SINK_CLOSE'):
                client.download_pack(candidate,'x86_64-gnu',fd)
            self.assertGreater(os.fstat(fd).st_size,0);self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),0)

    def test_pathless_trusted_namespace_loads_stdlib_without_user_payload_search(self):
        source=Path(__file__).resolve().parents[1]/'app/core_updates'
        code='''import importlib.util,sys,types
from pathlib import Path
for name in ('app','app.core_updates'):
 package=types.ModuleType(name);package.__path__=[];sys.modules[name]=package
for leaf in ('artifacts','github'):
 name='app.core_updates.'+leaf;spec=importlib.util.spec_from_file_location(name,Path(sys.argv[1])/(leaf+'.py'))
 module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
assert callable(module.GitHubCoreClient)
assert not any(x.split('.')[0] in ('cryptography','fastapi','sqlalchemy','httpx','requests') for x in sys.modules)
print('stdlib only')
'''
        result=subprocess.run([sys.executable,'-B','-I','-S','-c',code,str(source)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'stdlib only')

    def test_content_length_equals_actual_metadata_small_asset_and_stream_bytes_at_eof(self):
        for kind in ('metadata','small-asset','stream'):
            t=Transport();client=g.GitHubCoreClient(opener=t)
            with self.subTest(kind=kind):
                if kind=='metadata':
                    url=g.API+'/releases?per_page=20&page=1'
                    response=Response(url,b'[]',headers={'Content-Length':'10'});t.actions[url]=[response]
                    with self.assertRaisesRegex(g.CoreGitHubError,'HTTP_LENGTH'):client.discover('sing-box')
                elif kind=='small-asset':
                    url=t.asset_url('CORE-RELEASE.json');raw=t.f.manifest_raw
                    response=Response(url,raw[:-1],headers={'Content-Length':str(len(raw))});t.actions[url]=[response]
                    with self.assertRaisesRegex(g.CoreGitHubError,'HTTP_LENGTH'):client.inspect(31,app_source=APP)
                else:
                    candidate=client.inspect(31,app_source=APP)
                    url=t.asset_url(a._pack_name(t.f.core,t.f.version,'x86_64-gnu'));raw=t.f.pack_bytes['x86_64-gnu']
                    response=Response(url,raw[:-1],headers={'Content-Length':str(len(raw))});t.actions[url]=[response]
                    with self.sink() as (path,fd):
                        with self.assertRaisesRegex(g.CoreGitHubError,'HTTP_LENGTH'):client.download_pack(candidate,'x86_64-gnu',fd)
                        self.assertEqual(path.read_bytes(),raw[:-1]);self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),0)
                self.assertEqual(t.counts[url],1);self.assertTrue(response.closed);t.closed(self)
        # Exact declared length remains valid; it is not treated as truncation.
        t=Transport();url=g.API+'/releases?per_page=20&page=1'
        t.actions[url]=[Response(url,b'[]',headers={'Content-Length':'2'})]
        result=g.GitHubCoreClient(opener=t).discover('sing-box')
        self.assertEqual(result.hints,());self.assertFalse(result.truncated)

    def test_sink_change_immediately_after_real_m0_verification_cannot_become_a_new_baseline(self):
        t=Transport();client=g.GitHubCoreClient(opener=t);candidate=client.inspect(31,app_source=APP)
        original=a.verify_pack;calls=[]
        def verify_then_change(fd,release,*,target):
            identity=original(fd,release,target=target)
            calls.append(identity)
            os.pwrite(fd,b'!',0)
            return identity
        with self.sink() as (path,fd):
            with patch.object(a,'verify_pack',side_effect=verify_then_change),self.assertRaisesRegex(g.CoreGitHubError,'SINK_CHANGED'):
                client.download_pack(candidate,'x86_64-gnu',fd)
            self.assertEqual(len(calls),1)
            self.assertNotEqual(hashlib.sha256(path.read_bytes()).hexdigest(),calls[0].pack_sha256)
            self.assertEqual(os.fstat(fd).st_size,len(t.f.pack_bytes['x86_64-gnu']))
            self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),0)
        # Failure must be caught before the final fresh API recheck starts.
        self.assertEqual(t.counts[g.API+'/releases/31'],3);t.closed(self)


if __name__=='__main__':main()
