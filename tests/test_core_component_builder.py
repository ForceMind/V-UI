"""Offline fake source archives; FakeAuthority proves M0 format only, not trust."""
from contextlib import redirect_stderr
from copy import deepcopy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import warnings
from app.core_updates import artifacts as a
from scripts import build_core_component as b


def sha(raw): return hashlib.sha256(raw).hexdigest()


def tar(entries):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w:gz') as archive:
        for name,raw,kind in entries:
            info=tarfile.TarInfo(name);info.type=kind;info.mode=0o644
            info.size=len(raw) if kind==tarfile.REGTYPE else 0
            if kind==tarfile.SYMTYPE: info.linkname='/foreign'
            archive.addfile(info,io.BytesIO(raw) if info.size else None)
    return output.getvalue()


def zipped(entries):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as archive:
        for name,raw,mode in entries:
            item=zipfile.ZipInfo(name);item.external_attr=mode<<16;item.create_system=3
            with warnings.catch_warnings():
                warnings.simplefilter('ignore',UserWarning)
                archive.writestr(item,raw)
    return output.getvalue()


class Fixture:
    def __init__(self,root,core='sing-box',version='1.14.3'):
        self.root=root;self.core=core;self.version=version;self.license=b'original\r\nlicense\x00\n'
        self.prefix=a.UPSTREAMS[core].split('/')[1]+'-'+'e'*40
        self.source_raw=tar([(self.prefix+'/LICENSE',self.license,tarfile.REGTYPE),
                             (self.prefix+'/source.go',b'complete source',tarfile.REGTYPE)])
        self.source=root/'source.tar.gz';self.source.write_bytes(self.source_raw)
        self.meta=dict(schema=1,repository=a.REPOSITORY,core=core,version=version,revision=1,
            tag='core-'+core+'-v'+version+'-r1',builder_source='a'*40,kind='core-component-build-input-v1',
            compatibility=dict(contract=a.CONTRACT,app_sources=['b'*40]),upstream=dict(
                repository=a.UPSTREAMS[core],tag='v'+version,source_commit='e'*40,
                source_size=len(self.source_raw),source_sha256=sha(self.source_raw),archives=[]))
        self.paths={};self.binaries={};shared={}
        for target in a.TARGETS:
            _,name,member=a._upstream(core,version,target)
            if name not in shared:
                binary=('NOT ELF:'+name).encode()+b'\x00\xff'
                raw=(zipped([(member,binary,stat.S_IFREG|0o755)]) if core=='xray'
                     else tar([(member,binary,tarfile.REGTYPE)]))
                shared[name]=raw,binary
            raw,binary=shared[name];path=root/name
            if not path.exists():path.write_bytes(raw)
            self.paths[target]=path;self.binaries[target]=binary
            self.meta['upstream']['archives'].append(dict(target=target,archive_name=name,member=member,
                archive_size=len(raw),archive_sha256=sha(raw)))
        self.metadata=root/'expected.json';self.save()
    def save(self):self.metadata.write_bytes(b._canonical(self.meta))
    def build(self,target='x86_64-gnu',output=None):
        return b.build_target(self.root,self.metadata,target=target,archive=self.paths[target],
                              source=self.source,destination=Path(output or 'out-'+target))
    def all(self):
        return {target:self.root/('out-'+target)/self.build(target)['name'] for target in a.TARGETS}
    def source_change(self,raw):
        self.source.write_bytes(raw);self.meta['upstream'].update(source_size=len(raw),source_sha256=sha(raw));self.save()
    def archive_change(self,raw):
        path=self.paths['x86_64-gnu'];path.write_bytes(raw)
        for row in self.meta['upstream']['archives']:
            if row['archive_name']==path.name:row.update(archive_size=len(raw),archive_sha256=sha(raw))
        self.save()


class BuilderTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='vui-component-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.root.chmod(0o700)
        self.foreign=self.root/'foreign';self.foreign.write_bytes(b'preserve')
        self.addCleanup(lambda:self.assertEqual(self.foreign.read_bytes(),b'preserve'))
    def fixture(self,core='sing-box',version='1.14.3'):
        root=self.root/core;root.mkdir(mode=0o700);return Fixture(root,core,version)
    def test_two_cores_four_targets_original_bytes_and_m0_closed_six_asset_set(self):
        for core,version in (('sing-box','1.14.3'),('xray','26.10.21')):
            f=self.fixture(core,version);paths=f.all()
            result=b.assemble(f.root,f.metadata,packs=paths,destination=Path('aggregate'))
            self.assertFalse(result['published']);self.assertFalse(result['qualified'])
            output=f.root/'aggregate';values={p.name:p.read_bytes() for p in output.iterdir()}
            self.assertEqual(len(values),6)
            self.assertEqual(len(values['SHA256SUMS'].splitlines()),5)
            self.assertNotIn(b'  SHA256SUMS',values['SHA256SUMS'])
            # These invented IDs are explicitly TEST ONLY, not publisher authority.
            fake=dict(schema=1,repository=a.REPOSITORY,repository_id=a.REPOSITORY_ID,release_id=31,
                tag=f.meta['tag'],draft=False,prerelease=False,published_at='2026-11-08T00:00:00Z',assets=[
                dict(id=i,name=name,size=len(raw),digest='sha256:'+sha(raw))
                for i,(name,raw) in enumerate(sorted(values.items()),1)])
            release=a.verify_release(values['CORE-RELEASE.json'],values['SHA256SUMS'],
                authority=a.decode_authority(b._canonical(fake)),expected_tag=f.meta['tag'],
                expected_builder_source='a'*40,app_source='b'*40)
            for target,path in paths.items():
                identity=a.verify_pack(output/path.name,release,target=target)
                self.assertEqual(identity.binary_sha256,sha(f.binaries[target]))
                with zipfile.ZipFile(path) as archive:
                    self.assertEqual(archive.read('bin/'+core),f.binaries[target])
                    self.assertEqual(archive.read('SOURCE.tar.gz'),f.source_raw)
                    self.assertEqual(archive.read('LICENSE'),f.license)
                    self.assertNotIn('CORE-PACK.json',json.loads(archive.read('CORE-PACK.json'))['files'])
    def test_other_canonical_versions_are_allowed(self):
        for core,version in (('sing-box','1.15.0'),('xray','27.1.2')):
            f=self.fixture(core,version);self.assertEqual(f.build()['name'],a._pack_name(core,version,'x86_64-gnu'))
    def test_closed_metadata_finite_sources_and_official_identity(self):
        f=self.fixture();original=deepcopy(f.meta)
        updates=[dict(schema=True),dict(version='latest'),dict(builder_source='a'*39),dict(extra=True),
                 dict(repository='attacker/V-UI'),dict(revision=True),
                 dict(compatibility=dict(contract=a.CONTRACT,app_sources=[])),
                 dict(compatibility=dict(contract=a.CONTRACT,app_sources=['b'*40]*2))]
        for update in updates:
            f.meta=dict(deepcopy(original),**update);f.save()
            with self.subTest(update=update),self.assertRaises(b.CoreComponentBuildError):f.build()
        f.metadata.write_bytes(b'{"schema":1,"schema":1}')
        with self.assertRaises(b.CoreComponentBuildError):f.build()
    def test_external_archive_source_digests_and_canonical_member(self):
        f=self.fixture();original=deepcopy(f.meta)
        for key,value in (('archive_sha256','0'*64),('member','../bad')):
            f.meta=deepcopy(original);f.meta['upstream']['archives'][0][key]=value;f.save()
            with self.subTest(key=key),self.assertRaises(b.CoreComponentBuildError):f.build()
        f.meta=deepcopy(original);f.save();f.source.write_bytes(b'changed source')
        with self.assertRaisesRegex(b.CoreComponentBuildError,'INPUT_DIGEST'):f.build()
        self.assertFalse((f.root/'out-x86_64-gnu').exists())
    def test_source_duplicate_traversal_link_font_and_wrong_license_refused(self):
        f=self.fixture()
        variants=[[(f.prefix+'/LICENSE',b'a',tarfile.REGTYPE)]*2,
            [(f.prefix+'/../LICENSE',b'a',tarfile.REGTYPE)],[(f.prefix+'/LICENSE',b'',tarfile.SYMTYPE)],
            [(f.prefix+'/LICENSE.TXT',b'a',tarfile.REGTYPE)],
            [(f.prefix+'/LICENSE',b'a',tarfile.REGTYPE),(f.prefix+'/font.woff',b'a',tarfile.REGTYPE)]]
        for entries in variants:
            f.source_change(tar(entries))
            with self.subTest(entries=entries),self.assertRaises(b.CoreComponentBuildError):f.build()
    def test_source_hidden_tar_records_and_corrupt_gzip_rejected(self):
        f=self.fixture()
        for raw in (gzip.compress(gzip.decompress(f.source_raw)+b'hidden data'),f.source_raw[:-4]):
            f.source_change(raw)
            with self.assertRaises(b.CoreComponentBuildError):f.build()
    def test_original_zip_duplicate_link_traversal_and_prefix_rejected(self):
        f=self.fixture('xray','26.10.21');mode=stat.S_IFREG|0o755
        variants=[zipped([('xray',b'a',mode)]*2),zipped([('../xray',b'a',mode)]),
                  zipped([('xray',b'a',stat.S_IFLNK|0o777)]),b'prefix'+zipped([('xray',b'a',mode)])]
        for raw in variants:
            f.archive_change(raw)
            with self.assertRaises(b.CoreComponentBuildError):f.build()
    def test_output_collision_no_adoption_or_overwrite(self):
        f=self.fixture();f.build();output=f.root/'out-x86_64-gnu'
        before={p.name:p.read_bytes() for p in output.iterdir()}
        with self.assertRaisesRegex(b.CoreComponentBuildError,'OUTPUT_EXISTS'):f.build()
        self.assertEqual({p.name:p.read_bytes() for p in output.iterdir()},before)
        (f.root/'partial').mkdir();(f.root/'partial/foreign').write_bytes(b'foreign')
        with self.assertRaises(b.CoreComponentBuildError):f.build(output='partial')
        self.assertEqual((f.root/'partial/foreign').read_bytes(),b'foreign')
    def test_aggregate_context_tamper_missing_and_wrong_target_rejected(self):
        f=self.fixture();paths=f.all()
        for packs in ({},{**paths,'foreign':paths['x86_64-gnu']},{**paths,'aarch64-gnu':paths['x86_64-gnu']}):
            with self.assertRaises(b.CoreComponentBuildError):b.assemble(f.root,f.metadata,packs=packs,destination=Path('bad'))
        original=deepcopy(f.meta);f.meta['builder_source']='c'*40;f.save()
        with self.assertRaises(b.CoreComponentBuildError):b.assemble(f.root,f.metadata,packs=paths,destination=Path('bad'))
        f.meta=original;f.save();paths['x86_64-gnu'].write_bytes(b'tampered')
        with self.assertRaises(b.CoreComponentBuildError):b.assemble(f.root,f.metadata,packs=paths,destination=Path('bad'))
        self.assertFalse((f.root/'bad').exists())

    def test_aggregate_rejects_boolean_common_identity_before_publishing(self):
        f=self.fixture();paths=f.all();path=paths['x86_64-gnu'];original=path.read_bytes()
        for key in ('schema','revision'):
            with zipfile.ZipFile(io.BytesIO(original)) as archive:
                entries=[(info,archive.read(info.filename)) for info in archive.infolist()]
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w') as archive:
                for info,raw in entries:
                    if info.filename=='CORE-PACK.json':
                        record=json.loads(raw);record[key]=True;raw=b._canonical(record)
                    archive.writestr(info,raw)
            path.write_bytes(out.getvalue())
            output=Path('boolean-'+key)
            with self.subTest(field=key),self.assertRaisesRegex(b.CoreComponentBuildError,'PACK_CONTEXT'):
                b.assemble(f.root,f.metadata,packs=paths,destination=output)
            self.assertFalse((f.root/output).exists())
    def test_private_workspace_nofollow_single_link_and_no_escape(self):
        f=self.fixture();alias=f.root/'alias';alias.symlink_to(f.source)
        with self.assertRaises(b.CoreComponentBuildError):
            b.build_target(f.root,f.metadata,target='x86_64-gnu',archive=f.paths['x86_64-gnu'],source=alias,destination=Path('out'))
        alias.unlink();os.link(f.source,alias)
        with self.assertRaises(b.CoreComponentBuildError):f.build()
        alias.unlink();f.source.chmod(0o666)
        with self.assertRaises(b.CoreComponentBuildError):f.build()
        f.source.chmod(0o644)
        with self.assertRaises(b.CoreComponentBuildError):f.build(output='../foreign')
        f.root.chmod(0o755)
        with self.assertRaises(b.CoreComponentBuildError):f.build()
    def test_rename_lost_ack_preserves_complete_published_candidate(self):
        f=self.fixture();rename=b.fs._rename_new
        def lost(*args):rename(*args);raise b.fs.CoreStoreError('PUBLISH_FAILED')
        with patch.object(b.fs,'_rename_new',side_effect=lost):
            with self.assertRaises(b.CoreComponentBuildError):f.build()
        paths=list((f.root/'out-x86_64-gnu').iterdir());self.assertEqual(len(paths),1)
        with zipfile.ZipFile(paths[0]) as archive:self.assertEqual(archive.read('LICENSE'),f.license)
    def test_fsync_failure_only_cleans_owned_stage(self):
        f=self.fixture();before=f.source.read_bytes()
        with patch.object(b.os,'fsync',side_effect=OSError('/sensitive/path')):
            with self.assertRaises(b.CoreComponentBuildError):f.build()
        self.assertFalse((f.root/'out-x86_64-gnu').exists());self.assertFalse(list(f.root.glob('prepare-*')))
        self.assertEqual(f.source.read_bytes(),before)
    def test_publish_foreign_collision_does_not_delete_or_overwrite_foreign(self):
        f=self.fixture();rename=b.fs._rename_new
        def foreign(parent,name,destination,final):
            os.mkdir(final,0o700,dir_fd=destination)
            directory=os.open(final,os.O_RDONLY|os.O_DIRECTORY,dir_fd=destination)
            try:
                fd=os.open('foreign',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,dir_fd=directory)
                try:os.write(fd,b'foreign output')
                finally:os.close(fd)
            finally:os.close(directory)
            rename(parent,name,destination,final)
        with patch.object(b.fs,'_rename_new',side_effect=foreign):
            with self.assertRaises(b.CoreComponentBuildError):f.build()
        self.assertEqual((f.root/'out-x86_64-gnu/foreign').read_bytes(),b'foreign output')
        self.assertFalse(list(f.root.glob('prepare-*')))
    def test_close_lost_ack_attempts_all_owned_descriptors_and_preserves_published_output(self):
        f=self.fixture();opened=[];closed=[];open_fd=os.open;close=os.close
        def observed(*args,**kwargs):
            fd=open_fd(*args,**kwargs);opened.append(fd);return fd
        def lost(fd):
            close(fd);closed.append(fd)
            if len(closed)==1:raise OSError('sensitive close failure')
        with patch.object(b.os,'open',side_effect=observed),patch.object(b.os,'close',side_effect=lost):
            with self.assertRaisesRegex(b.CoreComponentBuildError,'CLOSE_FAILED'):f.build()
        self.assertEqual(sorted(opened),sorted(closed))
        for fd in opened:
            with self.assertRaises(OSError):os.fstat(fd)
        self.assertEqual(len(list((f.root/'out-x86_64-gnu').iterdir())),1)
    def test_source_expansion_and_member_bounds_reject_small_bombs(self):
        f=self.fixture()
        with patch.object(b.a,'MAX_EXPANDED',64):
            with self.assertRaises(b.CoreComponentBuildError):f.build()
        with patch.object(b,'MAX_MEMBERS',1):
            with self.assertRaises(b.CoreComponentBuildError):f.build()
        self.assertFalse((f.root/'out-x86_64-gnu').exists())
    def repack(self,path,changes):
        with zipfile.ZipFile(path) as archive:values={name:archive.read(name) for name in archive.namelist()}
        values.update(changes)
        provenance=json.loads(values['PROVENANCE.json'])
        if 'LICENSE' in changes:provenance['license_sha256']=sha(changes['LICENSE'])
        if 'bin/xray' in changes:provenance['binary_sha256']=sha(changes['bin/xray'])
        values['PROVENANCE.json']=b._canonical(provenance)
        record=json.loads(values['CORE-PACK.json'])
        for name in record['files']:record['files'][name].update(size=len(values[name]),sha256=sha(values[name]))
        values['CORE-PACK.json']=b._canonical(record)
        path.write_bytes(zipped([(name,raw,stat.S_IFREG|(0o555 if name.startswith('bin/') else 0o644))
                                 for name,raw in values.items()]))
    def test_aggregate_rejects_self_consistent_wrong_license_from_unchanged_source(self):
        f=self.fixture();paths=f.all();self.repack(paths['x86_64-gnu'],{'LICENSE':b'forged license'})
        with self.assertRaisesRegex(b.CoreComponentBuildError,'LICENSE_SOURCE'):
            b.assemble(f.root,f.metadata,packs=paths,destination=Path('bad-license'))
        self.assertFalse((f.root/'bad-license').exists())
    def test_same_xray_official_archive_member_cannot_produce_different_binary_identities(self):
        f=self.fixture('xray','26.10.21');paths=f.all()
        self.repack(paths['x86_64-musl'],{'bin/xray':b'forged but self-consistent binary'})
        with self.assertRaisesRegex(b.CoreComponentBuildError,'PACK_ORIGIN_DRIFT'):
            b.assemble(f.root,f.metadata,packs=paths,destination=Path('bad-origin'))

    def test_aggregate_partial_write_preserves_input_packs_and_foreign_data(self):
        f=self.fixture();paths=f.all();before={path:path.read_bytes() for path in paths.values()}
        write=os.write;calls=0
        def fail_after_partial(fd,raw):
            nonlocal calls
            calls+=1
            if calls==1:return write(fd,raw[:5])
            raise OSError('/sensitive/private/output')
        with patch.object(b.os,'write',side_effect=fail_after_partial):
            with self.assertRaises(b.CoreComponentBuildError):
                b.assemble(f.root,f.metadata,packs=paths,destination=Path('partial-aggregate'))
        self.assertFalse((f.root/'partial-aggregate').exists());self.assertFalse(list(f.root.glob('prepare-*')))
        self.assertEqual({path:path.read_bytes() for path in before},before)

    def test_cli_errors_never_echo_sensitive_paths(self):
        output=io.StringIO()
        with redirect_stderr(output):result=b.main(['build-target','--workspace','/sensitive/private'])
        self.assertEqual(result,1);self.assertEqual(output.getvalue(),'Candidate component rejected: CLI_ARGUMENTS\n')


if __name__=='__main__':unittest.main()
