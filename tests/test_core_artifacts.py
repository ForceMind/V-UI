"""Pure synthetic packs; no actual ELF, provider/network, extraction or install."""
import ast
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app.core_updates import artifacts as a

BUILD = 'a'*40
APP = 'b'*40


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class Fixture:
    def __init__(self,core='sing-box',version='1.14.3'):
        self.core,self.version=core,version
        self.tag='core-'+core+'-v'+version+'-r1'
        self.common=dict(schema=1,repository='ForceMind/V-UI',tag=self.tag,core=core,version=version,
            revision=1,builder_source=BUILD,compatibility={'contract':'core-selection-v1','app_sources':[APP]})
        self.materials={};self.pack_records={};self.pack_bytes={}
        for target in a.TARGETS:
            repository,archive,member=a._upstream(core,version,target)
            source=b'fake source distribution '+target.encode()
            license=b'fake license bytes from declared source'
            binary=b'NOT AN ELF: synthetic binary '+target.encode()
            provenance={'schema':1,'upstream':{'repository':repository,'tag':'v'+version,
                'archive_name':archive,'archive_size':1234,'archive_sha256':'c'*64,'member':member,
                'source_commit':'d'*40,'source_size':len(source),'source_sha256':sha(source),
                'license_member':repository.split('/')[1]+'-'+'d'*40+'/LICENSE'},
                'binary_sha256':sha(binary),'source_sha256':sha(source),'license_sha256':sha(license)}
            self.materials[target]={'bin/'+core:binary,'LICENSE':license,'SOURCE.tar.gz':source,'PROVENANCE.json':encoded(provenance)}
            self.refresh_pack(target)
        self.refresh_release()

    def refresh_pack(self,target,record=None,*,entries=None,force_zip64=False,streamed=False):
        materials=self.materials[target]
        if record is None:
            record={**deepcopy(self.common),'kind':'core-pack-v1','target':target,
                'files':{name:{'size':len(raw),'sha256':sha(raw),'mode':0o555 if name=='bin/'+self.core else 0o644}
                         for name,raw in materials.items()}}
        self.pack_records[target]=record
        raw=encoded(record); contents=list(materials.items())+[('CORE-PACK.json',raw)] if entries is None else entries
        class Unseekable(io.BytesIO):
            def seek(self,*args):raise OSError('synthetic nonseekable producer')
        stream=Unseekable() if streamed else io.BytesIO()
        with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for name,data in contents:
                entry=zipfile.ZipInfo(name)
                entry.external_attr=(stat.S_IFREG | (0o555 if name=='bin/'+self.core else 0o644))<<16
                entry.compress_type=zipfile.ZIP_DEFLATED
                if force_zip64:
                    with archive.open(entry,'w',force_zip64=True) as output:output.write(data)
                else: archive.writestr(entry,data)
        self.pack_bytes[target]=stream.getvalue()

    def refresh_release(self):
        self.manifest={**deepcopy(self.common),'kind':'core-release-v1','packs':[
            {'target':target,'name':a._pack_name(self.core,self.version,target),'size':len(self.pack_bytes[target]),
             'sha256':sha(self.pack_bytes[target]),'manifest_sha256':sha(encoded(self.pack_records[target]))}
            for target in a.TARGETS]}
        self.authorize()

    def authorize(self,manifest=None,table=None):
        self.manifest_raw=encoded(self.manifest) if manifest is None else manifest
        values={'CORE-RELEASE.json':self.manifest_raw,
                **{a._pack_name(self.core,self.version,t):self.pack_bytes[t] for t in a.TARGETS}}
        self.table_raw=(''.join(sha(values[name])+'  '+name+'\n' for name in sorted(values))).encode() if table is None else table
        values['SHA256SUMS']=self.table_raw
        self.authority={'schema':1,'repository':'ForceMind/V-UI','repository_id':1110299182,'release_id':31,
            'tag':self.tag,'draft':False,'prerelease':False,'published_at':'2026-11-08T00:00:00Z','assets':[
                {'id':number,'name':name,'size':len(raw),'digest':'sha256:'+sha(raw)}
                for number,(name,raw) in enumerate(sorted(values.items()),1)]}
        return self

    def release(self,**changes):
        context=dict(authority=a.decode_authority(encoded(self.authority)),expected_tag=self.tag,
                     expected_builder_source=BUILD,app_source=APP)
        context.update(changes)
        return a.verify_release(self.manifest_raw,self.table_raw,**context)

    def verify(self,target='x86_64-gnu'):
        with tempfile.TemporaryDirectory() as directory:
            # Canonicalize only our newly-created fixture root: macOS tempfile
            # may return /var (a symlink), which the production no-follow gate
            # correctly refuses. Deliberate symlink inputs below stay unresolved.
            path=Path(directory).resolve()/'pack.zip';path.write_bytes(self.pack_bytes[target])
            return a.verify_pack(path,self.release(),target=target)


class CoreArtifactTests(unittest.TestCase):
    def test_valid_two_cores_four_targets_and_new_canonical_version_use_same_contract(self):
        for core,version in (('sing-box','1.14.3'),('xray','26.10.21'),('sing-box','1.15.0'),('xray','27.1.2')):
            f=Fixture(core,version)
            for target in a.TARGETS:
                with self.subTest(core=core,version=version,target=target):
                    identity=f.verify(target)
                    self.assertEqual(identity.repository,'ForceMind/V-UI')
                    self.assertEqual(identity.builder_source,BUILD);self.assertEqual(identity.app_source,APP)
                    self.assertEqual(identity.target,target);self.assertEqual(identity.version,version)
                    self.assertEqual(identity.binary_sha256,sha(f.materials[target]['bin/'+core]))
                    self.assertNotIn('passed',identity.__dict__);self.assertNotIn('installed',identity.__dict__)

    def test_authority_rejects_wrong_repo_status_ids_assets_digests_and_unknown_keys(self):
        f=Fixture()
        for field,value in (('repository','attacker/V-UI'),('repository_id',True),('release_id',True),('release_id',0),
                            ('draft',True),('prerelease',None),('published_at',None),
                            ('published_at','2026-99-08T00:00:00Z'),('tag','v1.14.3'),('unknown','secret')):
            changed=deepcopy(f.authority);changed[field]=value
            with self.subTest(field=field),self.assertRaises(a.CoreArtifactError):a.decode_authority(encoded(changed))
        for field,value in (('id',True),('size',0),('size',a.MAX_PACK+1),('digest','sha256:'+'A'*64),('name','../bad')):
            changed=deepcopy(f.authority);changed['assets'][0][field]=value
            with self.subTest(asset=field),self.assertRaises(a.CoreArtifactError):a.decode_authority(encoded(changed))
        for change in ('missing','duplicate-name','duplicate-id','extra'):
            changed=deepcopy(f.authority)
            if change=='missing':changed['assets'].pop()
            elif change=='duplicate-name':changed['assets'][0]['name']=changed['assets'][1]['name']
            elif change=='duplicate-id':changed['assets'][0]['id']=changed['assets'][1]['id']
            else:changed['assets'].append(deepcopy(changed['assets'][0]))
            with self.subTest(change=change),self.assertRaises(a.CoreArtifactError):a.decode_authority(encoded(changed))

    def test_strict_json_duplicate_nonfinite_utf8_depth_and_numbers_in_authority_and_release(self):
        f=Fixture()
        raw_cases=(b'{"schema":1,"schema":1}',b'{"x":NaN}',b'{"x":Infinity}',b'{"x":1.5}',b'\xff',
                   b'{"x":"\\ud800"}',b'{"x":'+b'['*30+b'0'+b']'*30+b'}',b'{"x":'+b'9'*100+b'}')
        for raw in raw_cases:
            with self.subTest(raw=raw[:25]),self.assertRaises(a.CoreArtifactError):a.decode_authority(raw)
            f.authorize(manifest=raw)
            with self.subTest(release=raw[:25]),self.assertRaises(a.CoreArtifactError):f.release()
        with self.assertRaises(a.CoreArtifactError):a.decode_authority({})
        with self.assertRaises(a.CoreArtifactError):a.decode_authority(b' '* (a.MAX_JSON+1))

    def test_external_metadata_digests_and_explicit_tag_builder_app_context_are_required(self):
        f=Fixture();authority=a.decode_authority(encoded(f.authority))
        for manifest,table in ((f.manifest_raw+b' ',f.table_raw),(f.manifest_raw,f.table_raw+b' ')):
            with self.assertRaisesRegex(a.CoreArtifactError,'EXTERNAL_DIGEST'):
                a.verify_release(manifest,table,authority=authority,expected_tag=f.tag,expected_builder_source=BUILD,app_source=APP)
        for changes in ({'authority':f.authority},{'expected_tag':'core-sing-box-v1.14.3-r2'},
                        {'expected_builder_source':'e'*40},{'expected_builder_source':'main'},
                        {'app_source':'f'*40},{'app_source':'b'*39}):
            with self.subTest(changes=tuple(changes)),self.assertRaises(a.CoreArtifactError):f.release(**changes)
        for tag in ('core-sing-box-v01.14.3-r1','core-sing-box-v1.14.3-r0','core-sing-box-v1.14.3-r1\n',
                    'core-sing-box-v1.14.3-rc1-r1','core-other-v1.2.3-r1'):
            f=Fixture();f.authority['tag']=tag
            with self.subTest(tag=tag),self.assertRaises(a.CoreArtifactError):f.release()

    def test_release_identity_compatibility_and_all_four_pack_descriptors_are_closed(self):
        for field,value in (('repository','other/repo'),('core','xray'),('version','1.14.4'),('revision',True),
                ('builder_source','f'*40),('schema',True),('kind','backup'),('unknown',True)):
            f=Fixture();f.manifest[field]=value;f.authorize()
            with self.subTest(field=field),self.assertRaises(a.CoreArtifactError):f.release()
        for compatibility in ({'contract':'future','app_sources':[APP]},{'contract':a.CONTRACT,'app_sources':[APP,APP]},
                              {'contract':a.CONTRACT,'app_sources':['e'*40,APP]},
                              {'contract':a.CONTRACT,'app_sources':['e'*40]},{'contract':a.CONTRACT,'app_sources':[APP],'approved':True}):
            f=Fixture();f.manifest['compatibility']=compatibility;f.authorize()
            with self.subTest(compatibility=compatibility),self.assertRaises(a.CoreArtifactError):f.release()
        for field,value in (('target','arm64'),('target','aarch64-musl'),('name','wrong.zip'),('size',True),
                            ('size',1),('sha256','f'*64),('manifest_sha256','A'*64),('extra',1)):
            f=Fixture();f.manifest['packs'][0][field]=value;f.authorize()
            with self.subTest(pack_field=field),self.assertRaises(a.CoreArtifactError):f.release()

    def test_checksum_table_has_five_unique_exact_non_self_referential_entries(self):
        for mode in ('duplicate','missing','extra','wrong-hash','unsafe','non-ascii'):
            f=Fixture();lines=f.table_raw.splitlines()
            if mode=='duplicate':lines[-1]=lines[0]
            elif mode=='missing':lines.pop()
            elif mode=='extra':lines.append(b'a'*64+b'  SHA256SUMS')
            elif mode=='wrong-hash':lines[0]=b'f'*64+lines[0][64:]
            elif mode=='unsafe':lines[0]=b'a'*64+b'  ../escape'
            else:lines[0]=b'\xff'
            f.authorize(table=b'\n'.join(lines)+b'\n')
            with self.subTest(mode=mode),self.assertRaises(a.CoreArtifactError):f.release()

    def test_outer_pack_digest_and_target_are_checked_before_zip_parsing(self):
        f=Fixture()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'pack.zip';path.write_bytes(b'tampered')
            with patch.object(a.zipfile,'ZipFile') as parse,self.assertRaisesRegex(a.CoreArtifactError,'PACK_DIGEST'):
                a.verify_pack(path,f.release(),target='x86_64-gnu')
            parse.assert_not_called()
            for target in ('x86_64','../target',None):
                with self.assertRaises(a.CoreArtifactError):a.verify_pack(path,f.release(),target=target)

    def test_unknown_missing_duplicate_traversal_directory_and_symlink_members_refuse(self):
        target='x86_64-gnu'
        for mode in ('unknown','missing','duplicate','traversal','nul','directory','symlink','permissions','encrypted'):
            f=Fixture();entries=list(f.materials[target].items())+[('CORE-PACK.json',encoded(f.pack_records[target]))]
            if mode=='missing':entries.pop()
            elif mode=='duplicate':entries[-1]=entries[0]
            elif mode in ('unknown','traversal','nul','directory'):
                entries[0]=({'unknown':'unknown','traversal':'../escape','nul':'bin/sing-box\x00extra','directory':'bin/'}[mode],entries[0][1])
            f.refresh_pack(target,entries=entries)
            if mode in ('symlink','permissions'):
                raw=bytearray(f.pack_bytes[target]);central=raw.find(b'PK\x01\x02')
                struct.pack_into('<L',raw,central+38,((stat.S_IFLNK|0o777) if mode=='symlink' else (stat.S_IFREG|0o777))<<16)
                f.pack_bytes[target]=bytes(raw)
            if mode=='encrypted':
                raw=bytearray(f.pack_bytes[target]);central=raw.find(b'PK\x01\x02');struct.pack_into('<H',raw,central+8,1);f.pack_bytes[target]=bytes(raw)
            f.refresh_release()
            with self.subTest(mode=mode),self.assertRaises(a.CoreArtifactError):f.verify()

    def test_manifest_and_provenance_json_are_strict_and_every_material_digest_is_checked(self):
        target='x86_64-gnu'
        for field in ('LICENSE','SOURCE.tar.gz','bin/sing-box','PROVENANCE.json'):
            f=Fixture();record=deepcopy(f.pack_records[target]);f.materials[target][field]+=b'changed'
            f.refresh_pack(target,record);f.refresh_release()
            with self.subTest(material=field),self.assertRaises(a.CoreArtifactError):f.verify()
        for raw in (b'{"schema":1,"schema":1}',b'{"value":NaN}',b'\xff'):
            f=Fixture();f.materials[target]['PROVENANCE.json']=raw;f.refresh_pack(target);f.refresh_release()
            with self.subTest(provenance=raw),self.assertRaises(a.CoreArtifactError):f.verify()
            f=Fixture();entries=list(f.materials[target].items())+[('CORE-PACK.json',raw)]
            f.refresh_pack(target,entries=entries);f.refresh_release()
            with self.subTest(manifest=raw),self.assertRaises(a.CoreArtifactError):f.verify()

    def test_inner_identity_and_file_record_unknown_fields_modes_and_hashes_refuse(self):
        target='x86_64-gnu'
        for key,value in (('target','x86_64-musl'),('builder_source','f'*40),('unknown',True),('kind','release')):
            f=Fixture();record=deepcopy(f.pack_records[target]);record[key]=value
            f.refresh_pack(target,record);f.refresh_release()
            with self.subTest(key=key),self.assertRaises(a.CoreArtifactError):f.verify()
        for key,value in (('mode',0o777),('size',True),('sha256','f'*64),('extra',True)):
            f=Fixture();record=deepcopy(f.pack_records[target]);record['files']['LICENSE'][key]=value
            f.refresh_pack(target,record);f.refresh_release()
            with self.subTest(file_key=key),self.assertRaises(a.CoreArtifactError):f.verify()

    def test_upstream_repo_version_archive_member_license_and_byte_bindings_refuse_drift(self):
        target='x86_64-gnu'
        for key,value in (('repository','fork/sing-box'),('tag','v1.14.2'),('archive_name','other.tar.gz'),
                ('member','../../binary'),('archive_sha256','x'*64),('archive_size',True),
                ('source_commit','main'),('source_size',True),('source_sha256','f'*64),('license_member','other/LICENSE')):
            f=Fixture();provenance=json.loads(f.materials[target]['PROVENANCE.json']);provenance['upstream'][key]=value
            f.materials[target]['PROVENANCE.json']=encoded(provenance);f.refresh_pack(target);f.refresh_release()
            with self.subTest(key=key),self.assertRaises(a.CoreArtifactError):f.verify()
        for key in ('binary_sha256','license_sha256','source_sha256'):
            f=Fixture();provenance=json.loads(f.materials[target]['PROVENANCE.json']);provenance[key]='f'*64
            f.materials[target]['PROVENANCE.json']=encoded(provenance);f.refresh_pack(target);f.refresh_release()
            with self.subTest(key=key),self.assertRaises(a.CoreArtifactError):f.verify()

    def test_path_symlink_hardlink_directory_fifo_and_parent_symlink_refuse(self):
        f=Fixture();release=f.release()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();path=root/'pack.zip';path.write_bytes(f.pack_bytes['x86_64-gnu'])
            symlink=root/'symlink';symlink.symlink_to(path)
            for item in (symlink,root,Path('relative.zip')):
                with self.subTest(item=item.name),self.assertRaises(a.CoreArtifactError):a.verify_pack(item,release,target='x86_64-gnu')
            hard=root/'hard';os.link(path,hard)
            with self.assertRaises(a.CoreArtifactError):a.verify_pack(path,release,target='x86_64-gnu')
            hard.unlink()
            fifo=root/'fifo';os.mkfifo(fifo)
            with self.assertRaises(a.CoreArtifactError):a.verify_pack(fifo,release,target='x86_64-gnu')
            actual=root/'actual';actual.mkdir();(actual/'pack.zip').write_bytes(path.read_bytes())
            alias=root/'alias';alias.symlink_to(actual,target_is_directory=True)
            with self.assertRaises(a.CoreArtifactError):a.verify_pack(alias/'pack.zip',release,target='x86_64-gnu')

    def test_fd_is_borrowed_and_offset_preserved_and_each_read_is_bounded(self):
        f=Fixture();release=f.release()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'pack';path.write_bytes(f.pack_bytes['x86_64-gnu'])
            fd=os.open(path,os.O_RDONLY);self.addCleanup(os.close,fd);os.lseek(fd,7,os.SEEK_SET)
            original=os.pread;reads=[]
            def read(descriptor,size,offset):
                self.assertLessEqual(size,a.CHUNK);reads.append(size);return original(descriptor,size,offset)
            with patch.object(a.os,'pread',side_effect=read):identity=a.verify_pack(fd,release,target='x86_64-gnu')
            self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),7);self.assertGreater(os.fstat(fd).st_size,0)
            self.assertEqual(identity.pack_sha256,sha(f.pack_bytes['x86_64-gnu']));self.assertTrue(reads)

    def test_changed_file_public_path_or_parent_during_verification_never_returns_identity(self):
        for mode in ('bytes','file-path','parent'):
            f=Fixture();release=f.release()
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                root=Path(directory).resolve();parent=root/'parent';parent.mkdir();path=parent/'pack';path.write_bytes(f.pack_bytes['x86_64-gnu'])
                original=a._digest_fd;calls=[]
                def mutate(fd,size):
                    value=original(fd,size);calls.append(1)
                    if len(calls)==1:
                        if mode=='bytes':
                            with path.open('r+b') as handle:handle.write(b'changed')
                        elif mode=='file-path':
                            path.rename(parent/'old');path.write_bytes(f.pack_bytes['x86_64-gnu'])
                        else:
                            parent.rename(root/'moved');parent.mkdir();(parent/'pack').write_bytes(f.pack_bytes['x86_64-gnu'])
                    return value
                with patch.object(a,'_digest_fd',side_effect=mutate),self.assertRaises(a.CoreArtifactError):
                    a.verify_pack(path,release,target='x86_64-gnu')

    def test_metadata_limits_are_enforced_before_zipfile_and_local_force_zip64_is_supported(self):
        f=Fixture();target='x86_64-gnu'
        f.refresh_pack(target,force_zip64=True);f.refresh_release();f.verify()
        for field,value in ((10,65535),(12,a.MAX_METADATA+1)):
            f=Fixture();raw=bytearray(f.pack_bytes[target]);position=raw.rfind(b'PK\x05\x06')
            struct.pack_into('<H' if field==10 else '<L',raw,position+field,value)
            f.pack_bytes[target]=bytes(raw);f.refresh_release()
            with patch.object(a.zipfile,'ZipFile') as parse,self.assertRaises(a.CoreArtifactError):f.verify()
            parse.assert_not_called()
        f=Fixture()
        with patch.object(a,'MAX_EXPANDED',1),self.assertRaises(a.CoreArtifactError):f.verify()

    def test_each_member_disk_start_must_be_zero_even_with_valid_external_digests(self):
        # EOCD stays single-volume. Changing only one central member's disk
        # start must fail independently, including members after the first.
        for selected in (0,2,4):
            f=Fixture();target='x86_64-gnu';raw=bytearray(f.pack_bytes[target])
            end=raw.rfind(b'PK\x05\x06');cursor=struct.unpack_from('<L',raw,end+16)[0]
            for index in range(5):
                self.assertEqual(raw[cursor:cursor+4],b'PK\x01\x02')
                if index==selected:struct.pack_into('<H',raw,cursor+34,1)
                name,extra,comment=struct.unpack_from('<3H',raw,cursor+28)
                cursor+=46+name+extra+comment
            f.pack_bytes[target]=bytes(raw);f.refresh_release()
            with self.subTest(member=selected),self.assertRaisesRegex(a.CoreArtifactError,'ZIP_ENTRY'):
                f.verify()

    def test_pack_manifest_hash_mismatch_errors_are_fixed_and_no_archive_is_extracted(self):
        f=Fixture();f.manifest['packs'][0]['manifest_sha256']='f'*64;f.authorize()
        with patch.object(a.zipfile.ZipFile,'extract') as extract,patch.object(a.zipfile.ZipFile,'extractall') as extractall:
            with self.assertRaises(a.CoreArtifactError) as error:f.verify()
        self.assertEqual(str(error.exception),error.exception.code)
        self.assertRegex(error.exception.code,r'^[A-Z_]+$');extract.assert_not_called();extractall.assert_not_called()

    def test_streamed_local_descriptors_with_and_without_force_zip64_are_supported(self):
        for wide in (False,True):
            f=Fixture();f.refresh_pack('x86_64-gnu',streamed=True,force_zip64=wide);f.refresh_release()
            with self.subTest(wide=wide):f.verify()

    def test_malformed_compressed_bytes_and_hidden_prefix_fail_with_fixed_error(self):
        for fault in ('compressed','prefix'):
            f=Fixture();raw=bytearray(f.pack_bytes['x86_64-gnu'])
            if fault=='prefix':raw=bytearray(b'unlisted-prefix')+raw
            else:
                offset=30+len(('bin/'+f.core).encode())
                raw[offset]=0xff
            f.pack_bytes['x86_64-gnu']=bytes(raw);f.refresh_release()
            with self.subTest(fault=fault),self.assertRaises(a.CoreArtifactError) as error:f.verify()
            self.assertRegex(str(error.exception),r'^[A-Z_]+$')

    def test_large_synthetic_member_streams_and_oversize_file_is_refused_before_hash(self):
        f=Fixture();target='x86_64-gnu'
        raw=b'fake data block\n'*(a.CHUNK//8)
        f.materials[target]['bin/sing-box']=raw
        provenance=json.loads(f.materials[target]['PROVENANCE.json']);provenance['binary_sha256']=sha(raw)
        f.materials[target]['PROVENANCE.json']=encoded(provenance);f.refresh_pack(target);f.refresh_release()
        f.verify()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'too-large'
            with path.open('wb') as output:output.truncate(a.MAX_PACK+1)
            with patch.object(a,'_digest_fd') as digest,self.assertRaises(a.CoreArtifactError):
                a.verify_pack(path,f.release(),target=target)
            digest.assert_not_called()

    def test_close_failure_still_attempts_every_owned_descriptor_and_refuses_success(self):
        f=Fixture()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'pack';path.write_bytes(f.pack_bytes['x86_64-gnu'])
            original=os.close;closed=[]
            def close(fd):
                closed.append(fd);original(fd)
                if len(closed)==1:raise OSError('synthetic close acknowledgement failure')
            with patch.object(a.os,'close',side_effect=close),self.assertRaisesRegex(a.CoreArtifactError,'FILE_CLOSE'):
                a.verify_pack(path,f.release(),target='x86_64-gnu')
            self.assertEqual(len(closed),len(set(closed)));self.assertGreaterEqual(len(closed),3)
            for fd in closed:
                with self.assertRaises(OSError):os.fstat(fd)

    def test_isolated_stdlib_import_does_not_load_product_dependencies_or_network(self):
        source=Path(__file__).resolve().parents[1]
        code="import sys;sys.path.insert(0,sys.argv[1]);from app.core_updates.artifacts import decode_authority;assert callable(decode_authority);assert not any(x.split('.')[0] in ('sqlalchemy','fastapi','cryptography','requests','httpx') for x in sys.modules);print('stdlib only')"
        result=subprocess.run([sys.executable,'-B','-I','-S','-c',code,str(source)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'stdlib only')
        ast.parse((source/'app/core_updates/artifacts.py').read_bytes(),feature_version=(3,9))

    def test_root_style_flat_module_loading_needs_neither_app_namespace_nor_payload_path(self):
        source=Path(__file__).resolve().parents[1]/'app/core_updates/artifacts.py'
        code="import importlib.util,sys;spec=importlib.util.spec_from_file_location('_trusted_core_artifacts',sys.argv[1]);module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module);assert callable(module.verify_pack);assert not any(x=='app' or x.startswith('app.') or x.split('.')[0] in ('fastapi','sqlalchemy','cryptography') for x in sys.modules);print('flat stdlib only')"
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'core_artifacts.py';path.write_bytes(source.read_bytes())
            result=subprocess.run([sys.executable,'-B','-I','-S','-c',code,str(path)],cwd=directory,
                capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'flat stdlib only')


if __name__=='__main__':unittest.main()
