"""Only private fake catalog files and the real M2 client with fake transport."""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import stat
import subprocess
import sys
import tempfile
from unittest import TestCase,main
from unittest.mock import patch

from app.core_updates import artifacts as a,store as fs,github as g,catalog as c
from test_core_github import Transport,Response
from test_core_artifacts import APP,BUILD,Fixture


def snapshot(root):
    return {str(path.relative_to(root)):(path.read_bytes(),stat.S_IMODE(path.stat().st_mode),path.stat().st_ino)
            for path in root.rglob('*') if path.is_file() and not path.is_symlink()}


class CoreCatalogTests(TestCase):
    @contextmanager
    def fixture(self,core='sing-box',version='1.14.3'):
        with tempfile.TemporaryDirectory() as directory:
            anchor=Path(directory).resolve();anchor.chmod(0o700)
            transport=Transport(Fixture(core,version));client=g.GitHubCoreClient(opener=transport)
            candidate=client.inspect(31,app_source=APP)
            catalog=c.CoreCatalog(fs.Layout(anchor/'catalog',os.geteuid(),anchor))
            for name in ('selection','artifact-store','user-data'):(anchor/name).write_bytes(b'do not change')
            yield anchor,transport,client,candidate,catalog
            for name in ('selection','artifact-store','user-data'):self.assertEqual((anchor/name).read_bytes(),b'do not change')
            transport.closed(self)

    def record(self,catalog,candidate,client,app_source=APP):
        return catalog.record(candidate,app_source=app_source,client=client)

    def test_default_record_uses_safe_m2_and_persists_only_three_bounded_public_context_files(self):
        for core,version in (('sing-box','1.14.3'),('xray','26.10.21'),('sing-box','1.15.0')):
            with self.subTest(core=core,version=version),self.fixture(core,version) as (anchor,t,client,candidate,catalog):
                before=t.counts[g.API]
                with patch.object(g,'_opener',return_value=t):entry=catalog.record(candidate,app_source=APP)
                self.assertEqual(t.counts[g.API],before+1);self.assertFalse(entry.reused)
                root=catalog.layout.root/entry.manifest_sha256
                self.assertEqual(set(os.listdir(root)),set(c.FILES))
                for name in c.FILES:
                    path=root/name;info=path.stat()
                    self.assertEqual(info.st_uid,os.geteuid());self.assertEqual(info.st_nlink,1)
                    self.assertEqual(stat.S_IMODE(info.st_mode),0o644);self.assertLessEqual(info.st_size,64000)
                for path in (catalog.layout.root,root):self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o755)
                self.assertEqual(stat.S_IMODE((catalog.layout.root/'.staging').stat().st_mode),0o700)
                self.assertEqual(stat.S_IMODE((catalog.layout.root/'prepare.lock').stat().st_mode),0o600)
                self.assertEqual(list((catalog.layout.root/'.staging').iterdir()),[])
                context=json.loads((root/'CONTEXT.json').read_bytes())
                self.assertEqual(set(context),{'schema','kind','authority','tag_source'})
                self.assertEqual(context['kind'],'core-catalog-v1');self.assertEqual(context['tag_source'],BUILD)
                self.assertNotIn('token',repr(context));self.assertNotIn('nonce',repr(context));self.assertNotIn('approved',context)
                self.assertEqual((root/'CORE-RELEASE.json').read_bytes(),candidate.manifest)
                self.assertEqual((root/'SHA256SUMS').read_bytes(),candidate.checksums)
                self.assertEqual(entry.authority,candidate.authority);self.assertEqual(entry.tag_source,BUILD)

    def test_production_writer_uid_gate_precedes_client_creation_and_all_filesystem_access(self):
        with patch.object(c.os,'geteuid',return_value=12345),patch.object(c,'GitHubCoreClient') as client,patch.object(c,'_root') as root:
            with self.assertRaisesRegex(c.CoreCatalogError,'ROOT_REQUIRED'):
                c.CoreCatalog().record({},app_source=APP)
        client.assert_not_called();root.assert_not_called()

    def test_plain_json_backup_flags_or_non_m2_client_cannot_seed_the_live_catalog(self):
        for mode in ('plain-json','backup','client','fresh-flag'):
            with self.subTest(mode=mode),self.fixture() as (anchor,t,client,candidate,catalog):
                before=len(t.calls)
                with self.assertRaises((c.CoreCatalogError,TypeError)):
                    if mode=='plain-json':catalog.record(json.loads(candidate.manifest),app_source=APP,client=client)
                    elif mode=='backup':catalog.record({'authority':json.loads(candidate.manifest),'tag_source':BUILD},app_source=APP,client=client)
                    elif mode=='client':catalog.record(candidate,app_source=APP,client=type('Fake',(),{'recheck':lambda *args:True})())
                    else:catalog.record(candidate,app_source=APP,client=client,fresh=True)
                self.assertFalse(catalog.layout.root.exists());self.assertEqual(len(t.calls),before)

    def test_constructed_candidate_is_not_proof_and_must_match_fresh_official_context(self):
        for mode in ('api-id','tag-source','material'):
            with self.subTest(mode=mode),self.fixture() as (anchor,t,client,candidate,catalog):
                if mode=='api-id':
                    assets=list(candidate.authority.assets);assets[0]=replace(assets[0],id=assets[0].id+100)
                    authority=replace(candidate.authority,assets=tuple(assets))
                    forged=replace(candidate,authority=authority,verified_release=replace(candidate.verified_release,authority=authority))
                elif mode=='tag-source':forged=replace(candidate,tag_source='e'*40)
                else:forged=replace(candidate,manifest=candidate.manifest+b'changed')
                before=t.counts[g.API]
                with self.assertRaisesRegex(c.CoreCatalogError,'AUTHORITY_REJECTED'):self.record(catalog,forged,client)
                self.assertGreater(t.counts[g.API],before);self.assertFalse(catalog.layout.root.exists())

    def test_offline_or_changed_api_record_never_creates_cache_even_after_prior_inspect(self):
        for mode in ('offline','private','release-change'):
            with self.subTest(mode=mode),self.fixture() as (anchor,t,client,candidate,catalog):
                if mode=='offline':t.actions[g.API]=[Response(g.API,status=503),Response(g.API,status=504)]
                elif mode=='private':t.repo['private']=True
                else:t.release['id']=32
                with self.assertRaisesRegex(c.CoreCatalogError,'AUTHORITY_REJECTED'):self.record(catalog,candidate,client)
                self.assertFalse(catalog.layout.root.exists())

    def test_given_current_app_source_is_reverified_independently_from_candidate(self):
        with self.fixture() as (anchor,t,client,candidate,catalog):
            before=t.counts[g.API]
            with self.assertRaisesRegex(c.CoreCatalogError,'METADATA_REJECTED'):self.record(catalog,candidate,client,app_source='f'*40)
            self.assertGreater(t.counts[g.API],before);self.assertFalse(catalog.layout.root.exists())

    def test_read_is_offline_nonroot_no_lock_or_init_and_returned_context_can_feed_m1(self):
        with self.fixture() as (anchor,t,client,candidate,catalog):
            recorded=self.record(catalog,candidate,client);before=snapshot(catalog.layout.root);calls=len(t.calls)
            with patch.object(c,'GitHubCoreClient',side_effect=AssertionError('read must not construct a client')),patch.object(g.GitHubCoreClient,'recheck',side_effect=AssertionError('read must not perform API IO')),patch.object(fs,'_lock',side_effect=AssertionError('nonroot read must not touch lock')),patch.object(c.os,'geteuid',return_value=os.geteuid()+1):
                entry=catalog.read(recorded.manifest_sha256,app_source=APP)
                self.assertEqual(entry.authority,candidate.authority);self.assertEqual(entry.tag_source,BUILD)
                self.assertEqual(entry.verified_release,candidate.verified_release)
            self.assertEqual(len(t.calls),calls);self.assertEqual(snapshot(catalog.layout.root),before)
            # These are precisely M1.read's independent trusted arguments; no
            # artifact/TRUST or backup JSON was used to obtain either source.
            arguments={'authority':entry.authority,'expected_tag':entry.authority.tag,
                'expected_builder_source':entry.tag_source,'app_source':APP}
            self.assertEqual(arguments['expected_builder_source'],BUILD)
        with self.fixture() as (anchor,t,client,candidate,catalog):
            with patch.object(c,'GitHubCoreClient',side_effect=AssertionError('must stay offline')):
                with self.assertRaisesRegex(c.CoreCatalogError,'CATALOG_MISSING'):catalog.read('e'*64,app_source=APP)
            self.assertFalse(catalog.layout.root.exists())

    def test_same_id_reuse_reauthenticates_but_never_rewrites_the_existing_snapshot(self):
        with self.fixture() as (anchor,t,client,candidate,catalog):
            entry=self.record(catalog,candidate,client);before=snapshot(catalog.layout.root/entry.manifest_sha256)
            count=t.counts[g.API];again=self.record(catalog,candidate,client)
            self.assertTrue(again.reused);self.assertEqual(t.counts[g.API],count+1)
            self.assertEqual(snapshot(catalog.layout.root/entry.manifest_sha256),before)
            t.actions[g.API]=[Response(g.API,status=503),Response(g.API,status=504)]
            with self.assertRaisesRegex(c.CoreCatalogError,'AUTHORITY_REJECTED'):self.record(catalog,candidate,client)
            self.assertEqual(snapshot(catalog.layout.root/entry.manifest_sha256),before)
            with patch.object(c,'GitHubCoreClient',side_effect=AssertionError('offline read')):
                self.assertEqual(catalog.read(entry.manifest_sha256,app_source=APP).authority,entry.authority)

    def test_same_manifest_id_with_new_fresh_api_identity_is_not_overwritten(self):
        with self.fixture() as (anchor,t,client,candidate,catalog):
            entry=self.record(catalog,candidate,client);directory=catalog.layout.root/entry.manifest_sha256;before=snapshot(directory)
            first=t.release['assets'][0];first['id']+=100;first['url']=g.API+'/releases/assets/'+str(first['id'])
            t.f.authority['assets'][0]['id']=first['id']
            fresh=client.inspect(31,app_source=APP)
            self.assertEqual(fresh.verified_release.manifest_sha256,entry.manifest_sha256)
            self.assertNotEqual(fresh.authority,entry.authority)
            with self.assertRaisesRegex(c.CoreCatalogError,'CATALOG_IDENTITY_CHANGED'):self.record(catalog,fresh,client)
            self.assertEqual(snapshot(directory),before)

    def test_context_key_asset_order_and_json_whitespace_are_not_identity_changes(self):
        with self.fixture() as (anchor,t,client,candidate,catalog):
            entry=self.record(catalog,candidate,client);directory=catalog.layout.root/entry.manifest_sha256
            path=directory/'CONTEXT.json';value=json.loads(path.read_bytes());value['authority']['assets'].reverse()
            path.write_text(json.dumps(value,indent=2,sort_keys=False));before=snapshot(directory)
            self.assertEqual(catalog.read(entry.manifest_sha256,app_source=APP).authority,entry.authority)
            self.assertTrue(self.record(catalog,candidate,client).reused);self.assertEqual(snapshot(directory),before)

    def test_each_file_missing_changed_wrong_mode_symlink_or_hardlink_refuses_without_repair(self):
        for name in c.FILES:
            for fault in ('missing','changed','mode','symlink','hardlink'):
                with self.subTest(name=name,fault=fault),self.fixture() as (anchor,t,client,candidate,catalog):
                    entry=self.record(catalog,candidate,client);directory=catalog.layout.root/entry.manifest_sha256;path=directory/name
                    if fault=='missing':path.unlink()
                    elif fault=='changed':
                        raw=path.read_bytes();path.write_bytes(b'!'+raw[1:])
                    elif fault=='mode':path.chmod(0o666)
                    elif fault=='symlink':
                        path.unlink();path.symlink_to(anchor/'user-data')
                    else:os.link(path,anchor/'hardlink')
                    before=snapshot(directory)
                    with self.assertRaises(c.CoreCatalogError):catalog.read(entry.manifest_sha256,app_source=APP)
                    with self.assertRaises(c.CoreCatalogError):self.record(catalog,candidate,client)
                    self.assertEqual(snapshot(directory),before)

    def test_closed_context_schema_unknown_fields_duplicates_and_tag_binding_refuse(self):
        for mode in ('unknown','duplicate','nan','source','kind','missing-authority'):
            with self.subTest(mode=mode),self.fixture() as (anchor,t,client,candidate,catalog):
                entry=self.record(catalog,candidate,client);path=catalog.layout.root/entry.manifest_sha256/'CONTEXT.json'
                value=json.loads(path.read_bytes())
                if mode=='unknown':value['approved']=True
                elif mode=='source':value['tag_source']='e'*40
                elif mode=='kind':value['kind']='core-trust-cache-v1'  # artifact/TRUST is NOT live catalog.
                elif mode=='missing-authority':value.pop('authority')
                if mode=='duplicate':path.write_bytes(b'{"schema":1,"schema":1}')
                elif mode=='nan':path.write_bytes(b'{"schema":NaN}')
                else:path.write_text(json.dumps(value))
                with self.assertRaises(c.CoreCatalogError):catalog.read(entry.manifest_sha256,app_source=APP)

    def test_each_context_file_owner_is_checked_without_real_chown(self):
        for name in c.FILES:
            with self.subTest(name=name),self.fixture() as (anchor,t,client,candidate,catalog):
                entry=self.record(catalog,candidate,client);path=catalog.layout.root/entry.manifest_sha256/name
                selected=path.stat();original=os.fstat
                def wrong_owner(fd):
                    info=original(fd)
                    if (info.st_dev,info.st_ino)==(selected.st_dev,selected.st_ino):
                        values={key:getattr(info,key) for key in ('st_dev','st_ino','st_mode','st_uid','st_gid',
                            'st_nlink','st_size','st_mtime_ns','st_ctime_ns')}
                        values['st_uid']+=1
                        return SimpleNamespace(**values)
                    return info
                before=snapshot(catalog.layout.root/entry.manifest_sha256)
                with patch.object(c.os,'fstat',side_effect=wrong_owner),self.assertRaisesRegex(c.CoreCatalogError,'FILE_UNTRUSTED'):
                    catalog.read(entry.manifest_sha256,app_source=APP)
                self.assertEqual(snapshot(catalog.layout.root/entry.manifest_sha256),before)

    def test_unknown_files_directory_mode_owner_wrong_id_or_app_refuse(self):
        for mode in ('extra','dir-mode','owner','id','app'):
            with self.subTest(mode=mode),self.fixture() as (anchor,t,client,candidate,catalog):
                entry=self.record(catalog,candidate,client);directory=catalog.layout.root/entry.manifest_sha256
                if mode=='extra':(directory/'unknown').write_bytes(b'foreign')
                elif mode=='dir-mode':directory.chmod(0o775)
                elif mode=='owner':catalog=c.CoreCatalog(fs.Layout(catalog.layout.root,os.geteuid()+1,anchor))
                elif mode=='id':directory.rename(catalog.layout.root/('f'*64))
                identifier='f'*64 if mode=='id' else entry.manifest_sha256
                with self.assertRaises(c.CoreCatalogError):catalog.read(identifier,app_source='e'*40 if mode=='app' else APP)

    def test_oversized_file_is_rejected_before_read_and_no_record_accepts_raw_context(self):
        with self.fixture() as (anchor,t,client,candidate,catalog):
            entry=self.record(catalog,candidate,client);path=catalog.layout.root/entry.manifest_sha256/'CONTEXT.json'
            with path.open('wb') as output:output.truncate(a.MAX_JSON+1)
            with self.assertRaises(c.CoreCatalogError):catalog.read(entry.manifest_sha256,app_source=APP)
            self.assertTrue(path.exists())

    def test_prepublication_failure_cleans_only_its_own_private_context(self):
        for fault in ('write','unsupported','readback'):
            with self.subTest(fault=fault),self.fixture() as (anchor,t,client,candidate,catalog):
                if fault=='write':injection=patch.object(c.os,'write',side_effect=OSError('fake write failure'))
                elif fault=='unsupported':injection=patch.object(fs,'_rename_new',side_effect=fs.CoreStoreError('UNSUPPORTED'))
                else:injection=patch.object(catalog,'_read_at',side_effect=c.CoreCatalogError('CATALOG_INVALID'))
                with injection,self.assertRaises(c.CoreCatalogError):self.record(catalog,candidate,client)
                self.assertEqual(list((catalog.layout.root/'.staging').iterdir()),[])
                self.assertFalse((catalog.layout.root/candidate.verified_release.manifest_sha256).exists())

    def test_lost_publish_ack_or_postpublish_fsync_never_deletes_public_cache(self):
        for fault in ('lost-ack','fsync'):
            with self.subTest(fault=fault),self.fixture() as (anchor,t,client,candidate,catalog):
                original=fs._rename_new;original_sync=os.fsync;published=[]
                def publish(*values):
                    original(*values);published.append(True)
                    if fault=='lost-ack':raise fs.CoreStoreError('PUBLISH_UNCONFIRMED')
                def sync(fd):
                    if published and fault=='fsync':raise OSError('fake postpublish fsync failure')
                    return original_sync(fd)
                with patch.object(fs,'_rename_new',side_effect=publish),patch.object(c.os,'fsync',side_effect=sync),patch.object(c.os,'unlink',wraps=os.unlink) as unlink:
                    with self.assertRaises(c.CoreCatalogError):self.record(catalog,candidate,client)
                unlink.assert_not_called()
                self.assertEqual(catalog.read(candidate.verified_release.manifest_sha256,app_source=APP).authority,candidate.authority)
                self.assertTrue(self.record(catalog,candidate,client).reused)

    def test_cleanup_first_unlink_failure_still_handles_other_files_and_closes_every_fd(self):
        with self.fixture() as (anchor,t,client,candidate,catalog):
            original=os.unlink;calls=[];owned=[];own=fs._Scope.own
            def fail_first(name,*values,**kwargs):
                calls.append(name)
                if len(calls)==1:raise OSError('fake private unlink failure')
                return original(name,*values,**kwargs)
            def track(scope,fd):owned.append(fd);return own(scope,fd)
            with patch.object(fs,'_rename_new',side_effect=fs.CoreStoreError('UNSUPPORTED')),patch.object(c.os,'unlink',side_effect=fail_first),patch.object(fs._Scope,'own',track):
                with self.assertRaisesRegex(c.CoreCatalogError,'CLEANUP_FAILED'):self.record(catalog,candidate,client)
            self.assertEqual(len(calls),3)
            self.assertFalse((catalog.layout.root/candidate.verified_release.manifest_sha256).exists())
            for fd in set(owned):
                with self.assertRaises(OSError):os.fstat(fd)

    def test_catalog_scope_close_error_still_attempts_all_owned_fds_and_returns_no_entry(self):
        with self.fixture() as (anchor,t,client,candidate,catalog):
            entry=self.record(catalog,candidate,client);original_close=os.close;closed=[];owned=[];own=fs._Scope.own
            def track(scope,fd):owned.append(fd);return own(scope,fd)
            def close(fd):
                original_close(fd);closed.append(fd)
                if len(closed)==1:raise OSError('fake close acknowledgement failure')
            with patch.object(c.os,'close',side_effect=close),patch.object(fs._Scope,'own',track),self.assertRaisesRegex(c.CoreCatalogError,'CLOSE_FAILED'):
                catalog.read(entry.manifest_sha256,app_source=APP)
            self.assertEqual(set(owned),set(closed))
            for fd in set(owned):
                with self.assertRaises(OSError):os.fstat(fd)

    def test_collision_with_preexisting_private_namespace_is_never_adopted(self):
        with self.fixture() as (anchor,t,client,candidate,catalog):
            with c._root(catalog.layout,create=True) as (scope,root):scope.directory(root,'.staging',mode=0o700,create=True)
            foreign=catalog.layout.root/'.staging'/('prepare-'+'0'*32);foreign.mkdir(mode=0o700)
            (foreign/'foreign').write_bytes(b'preserve');before=snapshot(foreign)
            with patch.object(fs.secrets,'token_hex',return_value='0'*32),patch.object(c.os,'unlink',wraps=os.unlink) as unlink:
                with self.assertRaises(c.CoreCatalogError):self.record(catalog,candidate,client)
            unlink.assert_not_called();self.assertEqual(snapshot(foreign),before)

    def test_native_no_replace_rejects_empty_existing_same_id_without_adopting_or_repairing(self):
        with self.fixture() as (anchor,t,client,candidate,catalog):
            with c._root(catalog.layout,create=True) as (scope,root):pass
            directory=catalog.layout.root/candidate.verified_release.manifest_sha256;directory.mkdir(mode=0o755)
            inode=directory.stat().st_ino
            with self.assertRaises(c.CoreCatalogError):self.record(catalog,candidate,client)
            self.assertEqual(directory.stat().st_ino,inode);self.assertEqual(list(directory.iterdir()),[])

    def test_parent_replacement_or_file_change_during_read_never_returns_offline_trust(self):
        for mode in ('parent','file','extra'):
            with self.subTest(mode=mode),self.fixture() as (anchor,t,client,candidate,catalog):
                entry=self.record(catalog,candidate,client);directory=catalog.layout.root/entry.manifest_sha256
                original=fs._Scope.bytes;changed=[]
                def bytes_(scope,parent,name,**kwargs):
                    raw=original(scope,parent,name,**kwargs)
                    if name=='SHA256SUMS' and not changed:
                        changed.append(True)
                        if mode=='parent':
                            catalog.layout.root.rename(anchor/'retained-catalog');catalog.layout.root.mkdir(mode=0o755)
                        elif mode=='file':
                            path=directory/'CONTEXT.json';content=path.read_bytes();path.rename(directory/'old');path.write_bytes(content)
                        else:(directory/'extra').write_bytes(b'foreign')
                    return raw
                with patch.object(fs._Scope,'bytes',bytes_),self.assertRaises(c.CoreCatalogError):catalog.read(entry.manifest_sha256,app_source=APP)

    def test_two_processes_same_id_use_real_m2_fake_io_and_publish_only_one_context(self):
        with self.fixture() as (anchor,t,client,candidate,catalog):
            with c._root(catalog.layout,create=True) as (scope,root):
                fs._lock(scope,root);scope.directory(root,'.staging',mode=0o700,create=True)
            repo=Path(__file__).resolve().parents[1]
            code='''import json,os,sys
from pathlib import Path
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/tests']
from app.core_updates import catalog as c,github as g,store as fs
from test_core_github import Transport
anchor=Path(sys.argv[2]);transport=Transport();client=g.GitHubCoreClient(opener=transport)
candidate=client.inspect(31,app_source='b'*40);catalog=c.CoreCatalog(fs.Layout(anchor/'catalog',os.geteuid(),anchor))
print('ready',flush=True);assert sys.stdin.readline()=='go\\n'
try:
 result=catalog.record(candidate,app_source='b'*40,client=client)
 print(json.dumps({'ok':True,'reused':result.reused}),flush=True)
except c.CoreCatalogError as error:print(json.dumps({'ok':False,'code':error.code}),flush=True)
'''
            children=[]
            try:
                for _ in range(2):children.append(subprocess.Popen([sys.executable,'-B','-I','-S','-c',code,str(repo),str(anchor)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
                for child in children:self.assertEqual(child.stdout.readline(),'ready\n')
                for child in children:child.stdin.write('go\n');child.stdin.flush()
                outcomes=[]
                for child in children:
                    output,error=child.communicate(timeout=15);self.assertEqual(child.returncode,0,error);outcomes.append(json.loads(output))
                self.assertEqual(sum(row.get('ok') and not row.get('reused') for row in outcomes),1)
                self.assertTrue(all(row.get('ok') or row.get('code')=='STORE_BUSY' for row in outcomes),outcomes)
                roots=[path for path in catalog.layout.root.iterdir() if len(path.name)==64]
                self.assertEqual(len(roots),1);self.assertEqual(list((catalog.layout.root/'.staging').iterdir()),[])
                catalog.read(candidate.verified_release.manifest_sha256,app_source=APP)
            finally:
                for child in children:
                    if child.poll() is None:child.kill();child.wait(timeout=5)
                    for stream in (child.stdin,child.stdout,child.stderr):stream.close()

    def test_pathless_standalone_loading_is_stdlib_only_and_does_not_construct_network(self):
        repo=Path(__file__).resolve().parents[1]
        code='''import importlib.util,sys,types
from pathlib import Path
for name in ('app','app.core_updates'):
 package=types.ModuleType(name);package.__path__=[];sys.modules[name]=package
for leaf in ('artifacts','store','github','catalog'):
 name='app.core_updates.'+leaf;spec=importlib.util.spec_from_file_location(name,Path(sys.argv[1])/(leaf+'.py'))
 module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
class Forbidden:
 def __init__(self,*args,**kwargs):raise AssertionError('must not construct network')
module.GitHubCoreClient=Forbidden;module.CoreCatalog()
assert not any(x.split('.')[0] in ('cryptography','fastapi','sqlalchemy','httpx','requests') for x in sys.modules)
print('catalog offline stdlib')
'''
        result=subprocess.run([sys.executable,'-B','-I','-S','-c',code,str(repo/'app/core_updates')],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'catalog offline stdlib')


if __name__=='__main__':main()
