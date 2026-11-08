"""Archive-only original fake packs; no native execution, network or user data."""
from contextlib import contextmanager
from copy import copy, deepcopy
from dataclasses import replace
import json
import os
import pickle
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core_updates import artifacts as a, catalog as c, store as fs, selection as s
from app.core_updates.binary import RuntimeArtifact
from app.core_updates.github import GitHubCoreClient
from test_core_artifacts import Fixture, APP, BUILD
from test_core_github import Transport

NEW_APP = 'f'*40


class CoreArchiveTests(unittest.TestCase):
    @contextmanager
    def fixture(self, core='sing-box', version='1.14.3', target='x86_64-gnu', prepare=True):
        with tempfile.TemporaryDirectory(prefix='vui-core-archive-') as temporary:
            root=Path(temporary).resolve();root.chmod(0o700)
            material=Fixture(core,version);transport=Transport(material)
            client=GitHubCoreClient(opener=transport)
            catalog=c.CoreCatalog(fs.Layout(root/'catalog',os.geteuid(),root))
            candidate=client.inspect(31,app_source=APP)
            entry=catalog.record(candidate,app_source=APP,client=client)
            pack=root/'original.zip';pack.write_bytes(material.pack_bytes[target]);pack.chmod(0o600)
            store=fs.CoreArtifactStore(fs.Layout(root/'store',os.geteuid(),root))
            args=dict(authority=entry.authority,expected_tag=material.tag,
                      expected_builder_source=BUILD,app_source=APP,target=target)
            identity=a.verify_pack(pack,entry.verified_release,target=target)
            reference=s.component_reference(identity)
            if prepare:store.prepare(pack,material.manifest_raw,material.table_raw,**args)
            sentinel=root/'unrelated-user-data';sentinel.write_bytes(b'preserve')
            yield root,material,transport,catalog,store,pack,reference,args
            self.assertEqual(sentinel.read_bytes(),b'preserve')
            transport.closed(self)

    def test_two_cores_four_targets_can_archive_original_bytes_without_current_eligibility(self):
        for core,version in (('sing-box','1.14.3'),('xray','26.10.21')):
            for target in a.TARGETS:
                with self.subTest(core=core,target=target),self.fixture(core,version,target) as (_,m,t,catalog,store,_,ref,args):
                    calls=len(t.calls)
                    with store.open_archive(ref,target=target,catalog=catalog) as reader:
                        self.assertEqual(reader.reference,ref)
                        self.assertEqual(reader.size,len(m.pack_bytes[target]))
                        self.assertEqual(b''.join(reader.chunks()),m.pack_bytes[target])
                    self.assertEqual(len(t.calls),calls)
                    with self.assertRaises(c.CoreCatalogError):catalog.read(ref.release_manifest_sha256,app_source=NEW_APP)
                    with self.assertRaises(fs.CoreStoreError):store.read(ref.release_manifest_sha256,**dict(args,app_source=NEW_APP))

    def test_archive_catalog_has_declared_history_but_no_executable_release_or_app_context(self):
        with self.fixture() as (_,_,t,catalog,_,_,ref,_):
            count=len(t.calls);entry=catalog.read_archive(ref.release_manifest_sha256)
            self.assertIs(type(entry),c.ArchiveCatalogEntry)
            self.assertEqual(entry.declared_app_sources,(APP,))
            self.assertFalse(any(hasattr(entry,key) for key in ('verified_release','app_source','binary_path')))
            self.assertEqual(len(t.calls),count)

    def test_external_manifest_digest_checked_before_using_its_source_declaration(self):
        with self.fixture() as (_,_,_,catalog,_,_,ref,_):
            path=catalog.layout.root/ref.release_manifest_sha256/'CORE-RELEASE.json'
            value=json.loads(path.read_bytes());value['compatibility']['app_sources']=[NEW_APP]
            altered=json.dumps(value).encode();path.write_bytes(altered)
            parse=a._json;parsed=[]
            def observed(raw):
                parsed.append(raw);return parse(raw)
            with patch.object(a,'_json',side_effect=observed),self.assertRaisesRegex(c.CoreCatalogError,'EXTERNAL_DIGEST'):
                catalog.read_archive(ref.release_manifest_sha256)
            self.assertNotIn(altered,parsed)
            self.assertEqual(path.read_bytes(),altered)

    def test_missing_independent_catalog_cannot_fall_back_to_valid_store_trust(self):
        with self.fixture() as (root,_,_,_,store,_,ref,_):
            absent=c.CoreCatalog(fs.Layout(root/'absent-catalog',os.geteuid(),root))
            with self.assertRaisesRegex(fs.CoreStoreError,'ARCHIVE_AUTHORITY_UNAVAILABLE'):
                with store.open_archive(ref,target=ref.target,catalog=absent):pass
            self.assertFalse(absent.layout.root.exists())

    def test_bare_archive_entry_cannot_replace_live_catalog(self):
        with self.fixture() as (_,_,_,catalog,store,_,ref,_):
            entry=catalog.read_archive(ref.release_manifest_sha256)
            with self.assertRaisesRegex(fs.CoreStoreError,'ARCHIVE_CATALOG'):
                with store.open_archive(ref,target=ref.target,catalog=entry):pass

    def test_restoring_archived_material_does_not_grant_new_app_eligibility(self):
        with self.fixture(prepare=False) as (_,_,_,catalog,store,pack,ref,args):
            result=store.prepare_archive(pack,ref,target=ref.target,catalog=catalog)
            self.assertIs(type(result),fs.ArchivedArtifact);self.assertEqual(result.reference,ref)
            self.assertFalse(result.reused)
            self.assertFalse(any(hasattr(result,key)for key in ('identity','binary_path','app_source')))
            self.assertTrue(store.prepare_archive(pack,ref,target=ref.target,catalog=catalog).reused)
            with self.assertRaises(fs.CoreStoreError):store.read(ref.release_manifest_sha256,**dict(args,app_source=NEW_APP))
            self.assertEqual(store.read(ref.release_manifest_sha256,**args).identity.app_source,APP)

    def test_wrong_reference_is_rejected_before_store_initialization(self):
        with self.fixture(prepare=False) as (_,_,_,catalog,store,pack,ref,_):
            for wrong in (replace(ref,binary_sha256='0'*64),replace(ref,release_id=32),
                          replace(ref,builder_source='0'*40),replace(ref,pack_sha256='0'*64)):
                with self.subTest(reference=wrong),self.assertRaises(fs.CoreStoreError):
                    store.prepare_archive(pack,wrong,target=ref.target,catalog=catalog)
                self.assertFalse(store.layout.root.exists())

    def test_known_host_target_must_match_reference_before_any_catalog_read(self):
        with self.fixture() as (_,_,_,catalog,store,_,ref,_):
            with patch.object(c.CoreCatalog,'read_archive') as read,self.assertRaisesRegex(fs.CoreStoreError,'ARCHIVE_REFERENCE'):
                with store.open_archive(ref,target='aarch64-gnu',catalog=catalog):pass
            read.assert_not_called()

    def test_complete_store_closure_is_checked_instead_of_zip_only(self):
        for fault in ('extra','binary'):
            with self.subTest(fault=fault),self.fixture() as (_,_,_,catalog,store,_,ref,_):
                folder=store.layout.root/'artifacts'/ref.release_manifest_sha256
                path=folder/'foreign' if fault=='extra' else folder/'bin'/ref.core
                if fault=='binary':path.chmod(0o700)  # Only mutate this owned fake executable.
                path.write_bytes(b'changed')
                if fault=='binary':path.chmod(0o555)
                with self.assertRaises(fs.CoreStoreError):
                    with store.open_archive(ref,target=ref.target,catalog=catalog):pass
                self.assertEqual(path.read_bytes(),b'changed')

    def test_stream_and_partially_consumed_iterator_refuse_use_after_context_without_reading_fd(self):
        with self.fixture() as (_,_,_,catalog,store,_,ref,_):
            with store.open_archive(ref,target=ref.target,catalog=catalog) as reader:
                with patch.object(a,'CHUNK',64):
                    chunks=reader.chunks();self.assertEqual(len(next(chunks)),64)
            with patch.object(fs.os,'pread',side_effect=AssertionError('must not read a reused fd')) as read:
                with self.assertRaisesRegex(fs.CoreStoreError,'ARCHIVE_CLOSED'):next(chunks)
                with self.assertRaisesRegex(fs.CoreStoreError,'ARCHIVE_CLOSED'):next(reader.chunks())
                read.assert_not_called()

    def test_pack_mutation_during_stream_is_rejected_without_success_or_repair(self):
        with self.fixture() as (_,_,_,catalog,store,pack,ref,_):
            with self.assertRaises(fs.CoreStoreError):
                with store.open_archive(ref,target=ref.target,catalog=catalog) as reader:
                    path=store.layout.root/'artifacts'/ref.release_manifest_sha256/a._pack_name(ref.core,ref.version,ref.target)
                    raw=path.read_bytes();path.write_bytes(b'Q'+raw[1:])
            with self.assertRaisesRegex(fs.CoreStoreError,'ARCHIVE_CLOSED'):next(reader.chunks())

    def test_production_prepare_requires_root_before_catalog_or_pack_access(self):
        with patch.object(fs.os,'geteuid',return_value=12345),patch.object(c.CoreCatalog,'read_archive') as read, \
                patch.object(a,'_opened') as opened:
            with self.assertRaisesRegex(fs.CoreStoreError,'ROOT_REQUIRED'):
                fs.CoreArtifactStore().prepare_archive('/unused',None,target='x86_64-gnu')
        read.assert_not_called();opened.assert_not_called()

    def test_archive_stream_cannot_be_used_as_runtime_reference(self):
        with self.fixture() as (_,_,_,catalog,store,_,ref,_):
            with store.open_archive(ref,target=ref.target,catalog=catalog) as reader:
                with self.assertRaises(s.CoreSelectionError):RuntimeArtifact(reader,Path('/unused'))
                self.assertFalse(hasattr(reader,'binary_path'))

    def test_archive_stream_cannot_copy_or_serialize_its_borrowed_descriptor_lifetime(self):
        with self.fixture() as (_,_,_,catalog,store,_,ref,_):
            with store.open_archive(ref,target=ref.target,catalog=catalog) as reader:
                for operation in (copy,deepcopy,pickle.dumps):
                    with self.subTest(operation=operation),self.assertRaisesRegex(fs.CoreStoreError,'ARCHIVE_CONTEXT'):
                        operation(reader)


if __name__=='__main__':unittest.main()
