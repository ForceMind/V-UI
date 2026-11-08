"""Private fake packs/filesystem only. Never install or execute a proxy binary."""
from contextlib import contextmanager
from copy import deepcopy
import errno
import hashlib
import json
import os
from pathlib import Path
import select
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.core_updates import artifacts as a, store as s
from test_core_artifacts import Fixture, APP, BUILD


def snapshot(root):
    return {str(p.relative_to(root)):(p.read_bytes(),stat.S_IMODE(p.stat().st_mode),p.stat().st_ino)
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


def publisher_ready_marker(stream, timeout=15):
    """Bound the synthetic child handshake, including partial-line stalls."""
    deadline=time.monotonic()+timeout;raw=b''
    while len(raw)<6:
        remaining=deadline-time.monotonic()
        if remaining<=0 or not select.select([stream],[],[],remaining)[0]:
            raise TimeoutError('Synthetic publisher startup timed out')
        block=os.read(stream.fileno(),6-len(raw))
        if not block:raise RuntimeError('Synthetic publisher exited before its marker')
        raw+=block
    return raw


class CoreStoreTests(unittest.TestCase):
    @contextmanager
    def fixture(self,core='sing-box',version='1.14.3',target='x86_64-gnu'):
        with tempfile.TemporaryDirectory() as directory:
            anchor=Path(directory).resolve();anchor.chmod(0o700)
            f=Fixture(core,version);pack=anchor/'input.zip';pack.write_bytes(f.pack_bytes[target])
            layout=s.Layout(anchor/'store',os.geteuid(),anchor)
            store=s.CoreArtifactStore(layout)
            args=dict(authority=a.decode_authority(json.dumps(f.authority).encode()),expected_tag=f.tag,
                      expected_builder_source=BUILD,app_source=APP,target=target)
            (anchor/'unrelated-user-data').write_bytes(b'preserve-user-data')
            yield anchor,f,pack,store,args
            self.assertEqual((anchor/'unrelated-user-data').read_bytes(),b'preserve-user-data')

    def prepare(self,f,pack,store,args):
        return store.prepare(pack,f.manifest_raw,f.table_raw,**args)

    def test_two_cores_all_targets_prepare_read_and_same_id_reentry(self):
        for core,version in (('sing-box','1.14.3'),('xray','26.10.21')):
            for target in a.TARGETS:
                with self.subTest(core=core,target=target),self.fixture(core,version,target) as (anchor,f,pack,store,args):
                    result=self.prepare(f,pack,store,args)
                    self.assertFalse(result.reused)
                    self.assertEqual(result.binary_path.read_bytes(),f.materials[target]['bin/'+core])
                    artifact=result.binary_path.parent.parent
                    before=snapshot(artifact)
                    self.assertEqual(len(before),9)
                    for name,(_,mode,_) in before.items():
                        self.assertEqual(mode,0o555 if name=='bin/'+core else 0o644)
                        self.assertEqual((artifact/name).stat().st_uid,os.geteuid())
                        self.assertEqual((artifact/name).stat().st_nlink,1)
                    for path in (store.layout.root,store.layout.root/'artifacts',artifact,artifact/'bin'):
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o755)
                    self.assertEqual(stat.S_IMODE((store.layout.root/'.staging').stat().st_mode),0o700)
                    self.assertEqual(stat.S_IMODE((store.layout.root/'prepare.lock').stat().st_mode),0o600)
                    self.assertEqual(list((store.layout.root/'.staging').iterdir()),[])
                    read=store.read(result.identity.release_manifest_sha256,**args)
                    self.assertEqual(read.identity,result.identity)
                    again=self.prepare(f,pack,store,args)
                    self.assertTrue(again.reused);self.assertEqual(snapshot(artifact),before)

    def test_bad_authority_metadata_pack_or_input_symlink_never_initializes_store(self):
        for fault in ('authority','manifest','pack','symlink'):
            with self.subTest(fault=fault),self.fixture() as (anchor,f,pack,store,args):
                manifest=f.manifest_raw
                if fault=='authority':args={**args,'authority':None}
                elif fault=='manifest':manifest+=b'changed'
                elif fault=='pack':pack.write_bytes(b'not authorized')
                else:
                    alias=anchor/'alias';alias.symlink_to(pack);pack=alias
                with self.assertRaises(s.CoreStoreError):store.prepare(pack,manifest,f.table_raw,**args)
                self.assertFalse(store.layout.root.exists())

    def test_production_requires_uid_zero_before_reading_or_writing_any_path(self):
        with patch.object(s.os,'geteuid',return_value=12345),patch.object(a,'verify_release') as verify,patch.object(s,'_root') as root:
            with self.assertRaisesRegex(s.CoreStoreError,'ROOT_REQUIRED'):
                s.CoreArtifactStore().prepare('/unused',b'',b'',authority=None,expected_tag='',
                    expected_builder_source=BUILD,app_source=APP,target='x86_64-gnu')
        verify.assert_not_called();root.assert_not_called()

    def test_read_needs_no_writer_uid_or_lock_and_writes_no_bytes(self):
        with self.fixture() as (anchor,f,pack,store,args):
            result=self.prepare(f,pack,store,args);before=snapshot(store.layout.root)
            with patch.object(s.os,'geteuid',return_value=os.geteuid()+1),patch.object(s,'_lock') as lock,patch.object(s.os,'write') as write:
                store.read(result.identity.release_manifest_sha256,**args)
            lock.assert_not_called();write.assert_not_called();self.assertEqual(snapshot(store.layout.root),before)

    def test_each_missing_or_changed_closed_file_is_refused_without_overwrite(self):
        names=['CORE-RELEASE.json','SHA256SUMS','TRUST.json','CORE-PACK.json','LICENSE','SOURCE.tar.gz',
               'PROVENANCE.json','bin/sing-box',a._pack_name('sing-box','1.14.3','x86_64-gnu')]
        for name in names:
            for fault in ('missing','changed'):
                with self.subTest(name=name,fault=fault),self.fixture() as (anchor,f,pack,store,args):
                    result=self.prepare(f,pack,store,args);artifact=result.binary_path.parent.parent
                    path=artifact/name
                    if fault=='missing':path.unlink()
                    else:
                        mode=stat.S_IMODE(path.stat().st_mode);path.chmod(0o600)
                        raw=path.read_bytes();path.write_bytes(bytes([raw[0]^1])+raw[1:]);path.chmod(mode)
                    retained=snapshot(artifact)
                    with self.assertRaises(s.CoreStoreError):store.read(result.identity.release_manifest_sha256,**args)
                    with self.assertRaises(s.CoreStoreError):self.prepare(f,pack,store,args)
                    self.assertEqual(snapshot(artifact),retained)

    def test_extra_files_wrong_modes_owner_symlink_and_hardlink_refuse(self):
        for fault in ('extra','bin-extra','mode','directory-mode','owner','symlink','hardlink'):
            with self.subTest(fault=fault),self.fixture() as (anchor,f,pack,store,args):
                result=self.prepare(f,pack,store,args);artifact=result.binary_path.parent.parent
                path=artifact/'LICENSE'
                if fault=='extra':(artifact/'unknown').write_bytes(b'foreign')
                elif fault=='bin-extra':(artifact/'bin/unknown').write_bytes(b'foreign')
                elif fault=='mode':path.chmod(0o666)
                elif fault=='directory-mode':(artifact/'bin').chmod(0o775)
                elif fault=='owner':store=s.CoreArtifactStore(s.Layout(store.layout.root,os.geteuid()+1,anchor))
                elif fault=='symlink':
                    path.unlink();path.symlink_to(anchor/'unrelated-user-data')
                else:os.link(path,anchor/'foreign-hardlink')
                retained=snapshot(artifact)
                with self.assertRaises(s.CoreStoreError):store.read(result.identity.release_manifest_sha256,**args)
                self.assertEqual(snapshot(artifact),retained)

    def test_same_manifest_different_target_is_never_added_or_overwritten(self):
        with self.fixture() as (anchor,f,pack,store,args):
            result=self.prepare(f,pack,store,args);artifact=result.binary_path.parent.parent;before=snapshot(artifact)
            target='aarch64-musl';other=anchor/'other.zip';other.write_bytes(f.pack_bytes[target]);changed={**args,'target':target}
            with self.assertRaises(s.CoreStoreError):store.read(result.identity.release_manifest_sha256,**changed)
            with self.assertRaises(s.CoreStoreError):self.prepare(f,other,store,changed)
            self.assertEqual(snapshot(artifact),before)

    def test_cache_reordering_is_semantic_but_changed_api_id_or_tag_source_refuses(self):
        with self.fixture() as (anchor,f,pack,store,args):
            result=self.prepare(f,pack,store,args);artifact=result.binary_path.parent.parent
            cache=artifact/'TRUST.json';record=json.loads(cache.read_bytes())
            record['authority']['assets'].reverse()
            cache.write_text(json.dumps(record,indent=2,sort_keys=False))
            self.assertEqual(store.read(result.identity.release_manifest_sha256,**args).identity,result.identity)
            changed=deepcopy(f.authority);changed['assets'][0]['id']+=100
            with self.assertRaises(s.CoreStoreError):store.read(result.identity.release_manifest_sha256,
                **{**args,'authority':a.decode_authority(json.dumps(changed).encode())})
            record['tag_source']='e'*40;cache.write_text(json.dumps(record))
            with self.assertRaises(s.CoreStoreError):store.read(result.identity.release_manifest_sha256,**args)

    def test_cache_cannot_self_authorize_missing_external_context_or_unknown_fields(self):
        for fault in ('no-authority','extra','duplicate','false-source'):
            with self.subTest(fault=fault),self.fixture() as (anchor,f,pack,store,args):
                result=self.prepare(f,pack,store,args);cache=result.binary_path.parent.parent/'TRUST.json'
                if fault=='no-authority':args={**args,'authority':None}
                elif fault=='extra':
                    value=json.loads(cache.read_bytes());value['approved']=True;cache.write_text(json.dumps(value))
                elif fault=='duplicate':cache.write_bytes(b'{"schema":1,"schema":1}')
                else:args={**args,'expected_builder_source':'f'*40}
                with self.assertRaises(s.CoreStoreError):store.read(result.identity.release_manifest_sha256,**args)

    def test_prepublication_write_validation_or_unsupported_publish_failure_cleans_only_own_stage(self):
        for fault in ('write','extract','publish'):
            with self.subTest(fault=fault),self.fixture() as (anchor,f,pack,store,args):
                sentinel=anchor/'unrelated-user-data'
                if fault=='write':context=patch.object(s.os,'write',side_effect=OSError('synthetic'))
                elif fault=='extract':context=patch.object(s,'_extract',side_effect=s.CoreStoreError('SYNTHETIC'))
                else:context=patch.object(s,'_rename_new',side_effect=s.CoreStoreError('UNSUPPORTED'))
                with context,self.assertRaises(s.CoreStoreError):self.prepare(f,pack,store,args)
                self.assertEqual(list((store.layout.root/'artifacts').iterdir()),[])
                self.assertEqual(list((store.layout.root/'.staging').iterdir()),[])
                self.assertEqual(sentinel.read_bytes(),b'preserve-user-data')

    def test_input_or_extracted_bytes_changing_during_copy_refuse_without_publication(self):
        for fault in ('input','extracted'):
            with self.subTest(fault=fault),self.fixture() as (anchor,f,pack,store,args):
                original=s._extract
                def change(stage,*values):
                    original(stage,*values)
                    if fault=='input':pack.write_bytes(pack.read_bytes()+b'changed')
                    else:
                        fd=os.open('sing-box',os.O_WRONLY,dir_fd=stage.bin) if os.geteuid()==0 else None
                        if fd is None:
                            os.chmod('sing-box',0o755,dir_fd=stage.bin)
                            fd=os.open('sing-box',os.O_WRONLY,dir_fd=stage.bin)
                        try:os.write(fd,b'changed')
                        finally:os.close(fd)
                        os.chmod('sing-box',0o555,dir_fd=stage.bin)
                with patch.object(s,'_extract',side_effect=change),self.assertRaises(s.CoreStoreError):self.prepare(f,pack,store,args)
                self.assertEqual(list((store.layout.root/'artifacts').iterdir()),[])
                self.assertEqual(list((store.layout.root/'.staging').iterdir()),[])

    def test_postpublication_fsync_failure_preserves_public_bytes_and_never_unlinks_them(self):
        with self.fixture() as (anchor,f,pack,store,args):
            published=[];original_publish=s._rename_new;original_sync=os.fsync
            def publish(*values):original_publish(*values);published.append(True)
            def sync(fd):
                if published:raise OSError('synthetic postpublication fsync failure')
                return original_sync(fd)
            with patch.object(s,'_rename_new',side_effect=publish),patch.object(s.os,'fsync',side_effect=sync),patch.object(s.os,'unlink',wraps=os.unlink) as unlink:
                with self.assertRaises(s.CoreStoreError):self.prepare(f,pack,store,args)
            unlink.assert_not_called()
            identity=hashlib.sha256(f.manifest_raw).hexdigest()
            self.assertTrue(store.read(identity,**args).binary_path.is_file())
            self.assertTrue(self.prepare(f,pack,store,args).reused)

    def test_rename_success_followed_by_lost_ack_keeps_public_directory_and_returns_failure(self):
        with self.fixture() as (anchor,f,pack,store,args):
            original=s._rename_new
            def lost(*values):original(*values);raise s.CoreStoreError('PUBLISH_UNCONFIRMED')
            with patch.object(s,'_rename_new',side_effect=lost),patch.object(s.os,'unlink',wraps=os.unlink) as unlink:
                with self.assertRaisesRegex(s.CoreStoreError,'PUBLISH_UNCONFIRMED'):self.prepare(f,pack,store,args)
            unlink.assert_not_called()
            self.assertTrue(store.read(hashlib.sha256(f.manifest_raw).hexdigest(),**args).binary_path.is_file())

    def test_first_cleanup_unlink_failure_does_not_short_circuit_remaining_cleanup_or_fd_close(self):
        with self.fixture() as (anchor,f,pack,store,args):
            original=os.unlink;calls=[];owned=[];own=s._Scope.own
            def fail_first(name,*values,**kwargs):
                calls.append(name)
                if len(calls)==1:raise OSError('synthetic cleanup failure')
                return original(name,*values,**kwargs)
            def track(scope,fd):owned.append(fd);return own(scope,fd)
            with patch.object(s,'_rename_new',side_effect=s.CoreStoreError('UNSUPPORTED')),patch.object(s.os,'unlink',side_effect=fail_first),patch.object(s._Scope,'own',track):
                with self.assertRaisesRegex(s.CoreStoreError,'CLEANUP_FAILED') as error:self.prepare(f,pack,store,args)
            self.assertIsInstance(error.exception.__context__,s.CoreStoreError)
            self.assertEqual(error.exception.__context__.code,'UNSUPPORTED')
            self.assertEqual(len(calls),9)
            self.assertEqual(list((store.layout.root/'artifacts').iterdir()),[])
            retained=list((store.layout.root/'.staging').iterdir());self.assertEqual(len(retained),1)
            self.assertEqual([p.name for p in retained[0].iterdir()],['TRUST.json'])
            for fd in set(owned):
                with self.assertRaises(OSError):os.fstat(fd)

    def test_scope_close_attempts_every_fd_even_after_first_close_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();scope=s._Scope(os.geteuid())
            fds=[scope.own(os.open(root,os.O_RDONLY|os.O_DIRECTORY)) for _ in range(3)]
            original=os.close;closed=[]
            def close(fd):
                closed.append(fd);original(fd)
                if len(closed)==1:raise OSError('synthetic close failure')
            with patch.object(s.os,'close',side_effect=close),self.assertRaisesRegex(s.CoreStoreError,'CLOSE_FAILED'):scope.close()
            self.assertEqual(set(closed),set(fds))
            for fd in fds:
                with self.assertRaises(OSError):os.fstat(fd)

    def test_borrowed_source_fd_is_not_closed_or_repositioned(self):
        with self.fixture() as (anchor,f,pack,store,args):
            fd=os.open(pack,os.O_RDONLY)
            try:
                os.lseek(fd,7,os.SEEK_SET)
                result=self.prepare(f,fd,store,args)
                self.assertTrue(result.binary_path.is_file());self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),7)
                self.assertGreater(os.fstat(fd).st_size,0)
            finally:os.close(fd)

    def test_stage_open_failure_cleans_the_already_created_private_namespace(self):
        with self.fixture() as (anchor,f,pack,store,args):
            original=os.open
            def open_(name,*values,**kwargs):
                if type(name) is str and name.startswith('prepare-'):raise OSError('synthetic stage open failure')
                return original(name,*values,**kwargs)
            with patch.object(s.os,'open',side_effect=open_),self.assertRaises(s.CoreStoreError):self.prepare(f,pack,store,args)
            self.assertEqual(list((store.layout.root/'.staging').iterdir()),[])
            self.assertEqual(list((store.layout.root/'artifacts').iterdir()),[])

    def test_preexisting_private_name_collision_is_never_adopted_or_removed(self):
        with self.fixture() as (anchor,f,pack,store,args):
            with s._root(store.layout,create=True) as (scope,root,_):
                scope.directory(root,'.staging',mode=0o700,create=True)
            foreign=store.layout.root/'.staging'/('prepare-'+'0'*32)
            foreign.mkdir(mode=0o700);(foreign/'foreign').write_bytes(b'not ours')
            before=snapshot(foreign)
            with patch.object(s.secrets,'token_hex',return_value='0'*32),patch.object(s.os,'unlink',wraps=os.unlink) as unlink:
                with self.assertRaises(s.CoreStoreError):self.prepare(f,pack,store,args)
            unlink.assert_not_called();self.assertEqual(snapshot(foreign),before)
            self.assertEqual(list((store.layout.root/'artifacts').iterdir()),[])

    def test_store_parent_replacement_never_returns_a_binary_path_in_the_replacement(self):
        with self.fixture() as (anchor,f,pack,store,args):
            original=s._rename_new
            def replace(*values):
                store.layout.root.rename(anchor/'retained-store')
                store.layout.root.mkdir(mode=0o755);(store.layout.root/'artifacts').mkdir(mode=0o755)
                (store.layout.root/'foreign').write_bytes(b'preserve foreign')
                return original(*values)
            with patch.object(s,'_rename_new',side_effect=replace),self.assertRaisesRegex(s.CoreStoreError,'DIRECTORY_CHANGED'):
                self.prepare(f,pack,store,args)
            self.assertEqual((store.layout.root/'foreign').read_bytes(),b'preserve foreign')
            self.assertEqual(list((store.layout.root/'artifacts').iterdir()),[])
            self.assertEqual(len(list((anchor/'retained-store/artifacts').iterdir())),1)

    def test_read_refuses_file_replacement_or_extra_member_added_during_hashing(self):
        for fault in ('replace','extra'):
            with self.subTest(fault=fault),self.fixture() as (anchor,f,pack,store,args):
                result=self.prepare(f,pack,store,args);artifact=result.binary_path.parent.parent
                license=artifact/'LICENSE';inode=license.stat().st_ino;original=a._digest_fd;changed=[]
                def digest(fd,size):
                    value=original(fd,size)
                    if os.fstat(fd).st_ino==inode and not changed:
                        changed.append(True)
                        if fault=='replace':
                            raw=license.read_bytes();license.rename(artifact/'retained-license');license.write_bytes(raw)
                        else:(artifact/'unknown').write_bytes(b'preserve unknown')
                    return value
                with patch.object(a,'_digest_fd',side_effect=digest),self.assertRaises(s.CoreStoreError):
                    store.read(result.identity.release_manifest_sha256,**args)

    def test_streaming_read_and_write_chunks_never_need_the_whole_pack_in_memory(self):
        with self.fixture() as (anchor,f,pack,store,args):
            target=args['target'];binary=b'synthetic bytes\n'*(a.CHUNK//4)
            f.materials[target]['bin/sing-box']=binary
            provenance=json.loads(f.materials[target]['PROVENANCE.json']);provenance['binary_sha256']=hashlib.sha256(binary).hexdigest()
            f.materials[target]['PROVENANCE.json']=json.dumps(provenance).encode();f.refresh_pack(target);f.refresh_release()
            pack.write_bytes(f.pack_bytes[target]);args['authority']=a.decode_authority(json.dumps(f.authority).encode())
            original_read=os.pread;original_write=os.write;reads=[];writes=[]
            def read(fd,length,offset):
                self.assertLessEqual(length,a.CHUNK);reads.append(length);return original_read(fd,length,offset)
            def write(fd,raw):
                self.assertLessEqual(len(raw),a.CHUNK);writes.append(len(raw));return original_write(fd,raw)
            with patch.object(s.os,'pread',side_effect=read),patch.object(s.os,'write',side_effect=write):
                result=self.prepare(f,pack,store,args)
            self.assertTrue(reads and writes);self.assertEqual(result.binary_path.read_bytes(),binary)

    def test_native_publication_never_replaces_even_an_empty_existing_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();(root/'source').mkdir();(root/'destination').mkdir()
            (root/'source/new').write_bytes(b'owned')
            before=(root/'destination').stat().st_ino
            fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
            try:
                with self.assertRaises(s.CoreStoreError):s._rename_new(fd,'source',fd,'destination')
            finally:os.close(fd)
            self.assertEqual((root/'destination').stat().st_ino,before)
            self.assertEqual(list((root/'destination').iterdir()),[])
            self.assertEqual((root/'source/new').read_bytes(),b'owned')
        with patch.object(s.sys,'platform','not-supported'),self.assertRaisesRegex(s.CoreStoreError,'UNSUPPORTED'):
            s._rename_new(0,'unused',0,'unused')

    def test_native_missing_symbol_or_unsupported_filesystem_never_uses_os_rename(self):
        for mode in ('missing','filesystem'):
            native=Mock(return_value=-1)
            library=type('Library',(),{})()
            if mode=='filesystem':library.renameat2=native
            with self.subTest(mode=mode),patch.object(s.sys,'platform','linux'),patch.object(s.ctypes,'CDLL',return_value=library),patch.object(s.ctypes,'get_errno',return_value=errno.ENOSYS),patch.object(s.os,'rename') as rename:
                with self.assertRaisesRegex(s.CoreStoreError,'UNSUPPORTED'):s._rename_new(0,'owned',0,'new')
                rename.assert_not_called()

    def test_linux_without_libc_wrapper_uses_exact_supported_64bit_kernel_operation(self):
        for arch, number in (('x86_64',316), ('aarch64',276)):
            syscall=Mock(return_value=0);library=SimpleNamespace(syscall=syscall)
            with self.subTest(arch=arch),patch.object(s.sys,'platform','linux'), \
                    patch.object(s.ctypes,'CDLL',return_value=library), \
                    patch.object(s.os,'uname',return_value=SimpleNamespace(machine=arch)), \
                    patch.object(s.os,'rename') as unsafe:
                s._rename_new(7,'owned',8,'new')
                syscall.assert_called_once_with(number,7,b'owned',8,b'new',1)
                self.assertEqual(syscall.restype,s.ctypes.c_long)
                self.assertEqual(syscall.argtypes,(s.ctypes.c_long,s.ctypes.c_int,s.ctypes.c_char_p,
                                                   s.ctypes.c_int,s.ctypes.c_char_p,s.ctypes.c_uint))
                unsafe.assert_not_called()

    def test_linux_syscall_fallback_rejects_unknown_abi_and_preserves_no_replace_errors(self):
        for error, expected in ((errno.EEXIST,'DESTINATION_EXISTS'),
                                (errno.ENOSYS,'UNSUPPORTED'),(errno.EOPNOTSUPP,'UNSUPPORTED'),
                                (errno.EIO,'PUBLISH_FAILED')):
            syscall=Mock(return_value=-1);library=SimpleNamespace(syscall=syscall)
            with self.subTest(errno=error),patch.object(s.sys,'platform','linux'), \
                    patch.object(s.ctypes,'CDLL',return_value=library), \
                    patch.object(s.os,'uname',return_value=SimpleNamespace(machine='x86_64')), \
                    patch.object(s.ctypes,'get_errno',return_value=error),patch.object(s.os,'rename') as unsafe:
                with self.assertRaisesRegex(s.CoreStoreError,'^'+expected+'$'):
                    s._rename_new(7,'owned',8,'new')
                self.assertEqual(syscall.call_count,1);unsafe.assert_not_called()
        for arch, size in (('riscv64',8),('x86_64',4),('aarch64',4)):
            syscall=Mock(return_value=0);library=SimpleNamespace(syscall=syscall)
            with self.subTest(arch=arch,bits=size*8),patch.object(s.sys,'platform','linux'), \
                    patch.object(s.ctypes,'CDLL',return_value=library), \
                    patch.object(s.os,'uname',return_value=SimpleNamespace(machine=arch)), \
                    patch.object(s.ctypes,'sizeof',return_value=size),patch.object(s.os,'rename') as unsafe:
                with self.assertRaisesRegex(s.CoreStoreError,'^UNSUPPORTED$'):
                    s._rename_new(7,'owned',8,'new')
                syscall.assert_not_called();unsafe.assert_not_called()

    def test_two_processes_same_id_publish_only_once_and_keep_one_complete_artifact(self):
        with self.fixture() as (anchor,f,pack,store,args):
            # Infrastructure initialized once; candidate is still absent.
            with s._root(store.layout,create=True) as (scope,root,_):
                s._lock(scope,root);scope.directory(root,'.staging',mode=0o700,create=True)
            (anchor/'manifest').write_bytes(f.manifest_raw);(anchor/'table').write_bytes(f.table_raw)
            (anchor/'authority').write_text(json.dumps(f.authority))
            repo=Path(__file__).resolve().parents[1]
            code='''import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from app.core_updates import artifacts as a,store as s
anchor=Path(sys.argv[2]);store=s.CoreArtifactStore(s.Layout(anchor/'store',os.geteuid(),anchor))
args=dict(authority=a.decode_authority((anchor/'authority').read_bytes()),expected_tag='core-sing-box-v1.14.3-r1',expected_builder_source='a'*40,app_source='b'*40,target='x86_64-gnu')
print('ready',flush=True);assert sys.stdin.readline()=='go\\n'
try:
 result=store.prepare(anchor/'input.zip',(anchor/'manifest').read_bytes(),(anchor/'table').read_bytes(),**args)
 print(json.dumps({'ok':True,'reused':result.reused}),flush=True)
except s.CoreStoreError as error:print(json.dumps({'ok':False,'code':error.code}),flush=True)
'''
            children=[]
            try:
                for _ in range(2):children.append(subprocess.Popen([sys.executable,'-B','-I','-S','-c',code,str(repo),str(anchor)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
                for child in children:self.assertEqual(publisher_ready_marker(child.stdout),b'ready\n')
                for child in children:child.stdin.write('go\n');child.stdin.flush()
                results=[]
                for child in children:
                    output,error=child.communicate(timeout=15);self.assertEqual(child.returncode,0,error);results.append(json.loads(output))
                self.assertEqual(sum(row.get('ok') and not row.get('reused') for row in results),1)
                self.assertTrue(all(row.get('ok') or row.get('code')=='STORE_BUSY' for row in results),results)
                self.assertEqual(len(list((store.layout.root/'artifacts').iterdir())),1)
                self.assertEqual(list((store.layout.root/'.staging').iterdir()),[])
                store.read(hashlib.sha256(f.manifest_raw).hexdigest(),**args)
            finally:
                for child in children:
                    if child.poll() is None:child.kill();child.wait(timeout=5)
                    for stream in (child.stdin,child.stdout,child.stderr):stream.close()

    def test_publisher_marker_refuses_eof_and_partial_line_at_deadline(self):
        r,w=os.pipe()
        with os.fdopen(r,'rb',buffering=0) as stream:
            try:
                os.write(w,b'rea')
                with patch.object(time,'monotonic',side_effect=[0,0,16]):
                    with self.assertRaises(TimeoutError):publisher_ready_marker(stream)
            finally:os.close(w)
            with self.assertRaisesRegex(RuntimeError,'exited before'):
                publisher_ready_marker(stream)

    def test_flat_trusted_namespace_import_is_stdlib_only_without_payload_on_sys_path(self):
        repo=Path(__file__).resolve().parents[1]
        code='''import importlib.util,sys,types
from pathlib import Path
for name in ('app','app.core_updates'):
 package=types.ModuleType(name);package.__path__=[];sys.modules[name]=package
for leaf in ('artifacts','store'):
 name='app.core_updates.'+leaf;spec=importlib.util.spec_from_file_location(name,Path(sys.argv[1])/(leaf+'.py'))
 module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
assert callable(module.CoreArtifactStore)
assert not any(x.split('.')[0] in ('cryptography','fastapi','sqlalchemy','httpx','requests') for x in sys.modules)
print('trusted stdlib closure')
'''
        result=subprocess.run([sys.executable,'-B','-I','-S','-c',code,str(repo/'app/core_updates')],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'trusted stdlib closure')


if __name__=='__main__':unittest.main()
