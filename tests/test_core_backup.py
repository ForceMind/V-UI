"""Fake M0/M2 material and private filesystem only; not data/Root restore proof."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core_updates import artifacts as a, backup as b, catalog as c, github as g, selection as s, store as fs
from app.core_updates.binary import RuntimeArtifact
from test_core_artifacts import Fixture, APP, BUILD
from test_core_github import Transport

NEW_APP='f'*40
TARGET='x86_64-gnu'


class CoreBackupTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='vui-core-backup-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.root.chmod(0o700)
        self.workspace=self.root/'workspace';self.workspace.mkdir(mode=0o700)
        self.catalog=c.CoreCatalog(fs.Layout(self.root/'catalog',os.geteuid(),self.root))
        self.store=fs.CoreArtifactStore(fs.Layout(self.root/'store',os.geteuid(),self.root))
        self.bundles={}
        for core,version in (('sing-box','1.14.2'),('xray','26.3.27')):
            path=self.root/('bundle-'+core);raw=('fake bundle '+core).encode();path.write_bytes(raw);path.chmod(0o700)
            ref=s.bundle_reference(core=core,version=version,target=TARGET,app_source=NEW_APP,
                payload_manifest_sha256='d'*64,binary_sha256=hashlib.sha256(raw).hexdigest(),binary_size=len(raw))
            self.bundles[core]=RuntimeArtifact(ref,path,os.geteuid(),self.root,0o700)
        self.initial=s.initial_selection(app_source=NEW_APP,target=TARGET,
                                        bundles={core:item.reference for core,item in self.bundles.items()})
        self.tool=self.reader()
        self.materials={};self.transports=[]
        self.foreign=self.root/'unrelated-user-data';self.foreign.write_bytes(b'preserve data')
        self.addCleanup(lambda:self.assertEqual(self.foreign.read_bytes(),b'preserve data'))
        self.addCleanup(lambda:[transport.closed(self) for transport in self.transports])
    def reader(self,store=None,catalog=None,**kwargs):
        return b.CoreBackup(app_source=NEW_APP,target=TARGET,bundles=self.bundles,
            catalog=self.catalog if catalog is None else catalog,store=self.store if store is None else store,
            anchor=self.root,**kwargs)
    def add(self,core,version,source=NEW_APP):
        fixture=Fixture(core,version)
        fixture.common['compatibility']['app_sources']=[source]
        for target in a.TARGETS:fixture.refresh_pack(target)
        fixture.refresh_release()
        transport=Transport(fixture);self.transports.append(transport)
        client=g.GitHubCoreClient(opener=transport);candidate=client.inspect(31,app_source=source)
        self.catalog.record(candidate,app_source=source,client=client)
        pack=self.root/(core+'-'+version+'.zip');pack.write_bytes(fixture.pack_bytes[TARGET])
        stored=self.store.prepare(pack,fixture.manifest_raw,fixture.table_raw,authority=candidate.authority,
            expected_tag=fixture.tag,expected_builder_source=BUILD,app_source=source,target=TARGET)
        ref=s.component_reference(stored.identity);self.materials[ref.release_manifest_sha256]=fixture
        return ref
    def state(self,current=True,historical=True):
        slots=[]
        for core,new,old in (('sing-box','1.14.3','1.14.1'),('xray','26.10.21','26.3.26')):
            active=self.add(core,new) if current else self.bundles[core].reference
            previous=self.add(core,old,APP) if historical else None
            slots.append(s.CoreSlot(core,active,previous))
        return s._build(NEW_APP,TARGET,7,tuple(slots))
    def intents(self,state):
        return {core:dict(artifact_id=s.reference_identity(state.slot(core).current),
                         revision=hashlib.sha256(core.encode()).hexdigest(),enabled=core=='sing-box') for core in s.CORES}
    def stage(self,state=None,name='closure'):
        state=self.initial if state is None else state
        return self.tool.stage(self.workspace,name,selection=state,intents=self.intents(state),panel_was_running=False)
    def closure(self,state=None):
        self.stage(state);return self.workspace/'closure'
    def snapshot(self,path):return json.loads((path/'SNAPSHOT.json').read_bytes())
    def snapshot_write(self,path,value):(path/'SNAPSHOT.json').write_bytes(b._raw(value))
    def test_four_components_preserve_original_zip_metadata_and_restore_without_store(self):
        state=self.state();calls=[len(t.calls) for t in self.transports]
        staged=self.stage(state);path=self.workspace/'closure'
        self.assertEqual(len(list(path.iterdir())),13)
        self.assertEqual(len(staged.components),4)
        self.assertEqual(staged.selection,state);self.assertFalse(staged.panel_was_running)
        for fact in staged.components:
            fixture=self.materials[fact.reference.release_manifest_sha256]
            self.assertEqual(fact.pack_path.read_bytes(),fixture.pack_bytes[TARGET])
            self.assertEqual((path/b._names(fact.reference)[1]).read_bytes(),fixture.manifest_raw)
            self.assertEqual((path/b._names(fact.reference)[2]).read_bytes(),fixture.table_raw)
            self.assertEqual(fact.pack_path.stat().st_mode&0o777,0o600)
            self.assertFalse(any(hasattr(fact,key) for key in ('identity','binary_path','authority')))
        absent=fs.CoreArtifactStore(fs.Layout(self.root/'absent-store',os.geteuid(),self.root))
        with patch.object(fs.CoreArtifactStore,'read',side_effect=AssertionError('inspect must not need store')), \
                patch.object(fs.CoreArtifactStore,'open_archive',side_effect=AssertionError('inspect must not open store')):
            inspected=self.reader(store=absent).inspect(path)
        self.assertEqual(inspected,staged);self.assertFalse(absent.layout.root.exists())
        self.assertEqual([len(t.calls) for t in self.transports],calls)
    def test_archived_previous_lacks_new_app_eligibility_and_cannot_be_promoted_by_snapshot(self):
        state=self.state();path=self.closure(state)
        previous=state.slot('sing-box').previous
        self.assertEqual(self.catalog.read_archive(previous.release_manifest_sha256).declared_app_sources,(APP,))
        with self.assertRaises(c.CoreCatalogError):self.catalog.read(previous.release_manifest_sha256,app_source=NEW_APP)
        promoted=s._build(NEW_APP,TARGET,8,tuple(s.CoreSlot(slot.core,slot.previous,slot.current)
            if slot.core=='sing-box' else slot for slot in state.slots))
        value=self.snapshot(path);value['selection']=a._json(s.encode_selection(promoted));value['intents']=self.intents(promoted)
        self.snapshot_write(path,value)
        with self.assertRaises(b.CoreBackupError):self.tool.inspect(path)
    def test_bundle_previous_is_external_dependency_without_copy_or_execution(self):
        current=self.initial.slot('sing-box').current
        old=s.bundle_reference(core='sing-box',version='1.13.0',target=TARGET,app_source=APP,
            payload_manifest_sha256='e'*64,binary_sha256='c'*64,binary_size=99)
        state=s._build(NEW_APP,TARGET,1,(s.CoreSlot('sing-box',current,old),self.initial.slot('xray')))
        result=self.stage(state);path=self.workspace/'closure'
        self.assertEqual([p.name for p in path.iterdir()],['SNAPSHOT.json'])
        self.assertIn(old,result.external_bundles);self.assertEqual(result.components,())
        self.assertEqual(self.tool.inspect(path),result)
        changed=dict(self.bundles);changed['sing-box']=replace(changed['sing-box'],reference=replace(current,binary_sha256='0'*64))
        tool=b.CoreBackup(app_source=NEW_APP,target=TARGET,bundles=changed,catalog=self.catalog,anchor=self.root)
        with self.assertRaises(b.CoreBackupError):tool.inspect(path)
    def test_missing_catalog_never_trusts_backup_metadata_or_store_trust(self):
        state=self.state(historical=False);path=self.closure(state)
        absent=c.CoreCatalog(fs.Layout(self.root/'absent-catalog',os.geteuid(),self.root))
        with self.assertRaises(b.CoreBackupError):self.reader(catalog=absent).inspect(path)
        self.assertFalse(absent.layout.root.exists())
        with self.assertRaises(b.CoreBackupError):self.reader(catalog=absent).stage(self.workspace,'bad',
            selection=state,intents=self.intents(state),panel_was_running=False)
        self.assertFalse((self.workspace/'bad').exists())
    def test_raw_manifest_table_zip_and_reference_tamper_rejected_without_repair(self):
        state=self.state(historical=False);path=self.closure(state)
        reference=state.slot('sing-box').current
        for name in b._names(reference):
            file=path/name;original=file.read_bytes();file.write_bytes(b'changed')
            with self.subTest(name=name),self.assertRaises(b.CoreBackupError):self.tool.inspect(path)
            self.assertEqual(file.read_bytes(),b'changed');file.write_bytes(original)
        changed=replace(reference,binary_sha256='0'*64)
        modified=s._build(NEW_APP,TARGET,8,(s.CoreSlot('sing-box',changed,None),state.slot('xray')))
        value=self.snapshot(path);value['selection']=a._json(s.encode_selection(modified));value['intents']=self.intents(modified)
        self.snapshot_write(path,value)
        with self.assertRaises(b.CoreBackupError):self.tool.inspect(path)
    def test_snapshot_intents_duplicates_unknown_fields_and_actual_context_are_strict(self):
        path=self.closure();original=self.snapshot(path)
        variants=[]
        for update in (dict(schema=True),dict(panel_was_running=1),dict(pending={}),dict(authority={}),dict(nonce='x')):
            variants.append(dict(deepcopy(original),**update))
        for key,value in (('artifact_id','0'*64),('revision',None),('enabled',1)):
            item=deepcopy(original);item['intents']['sing-box'][key]=value;variants.append(item)
        for value in variants:
            self.snapshot_write(path,value)
            with self.subTest(value=value),self.assertRaises(b.CoreBackupError):self.tool.inspect(path)
        (path/'SNAPSHOT.json').write_bytes(b'{"schema":1,"schema":1}')
        with self.assertRaises(b.CoreBackupError):self.tool.inspect(path)
        self.snapshot_write(path,original)
        with self.assertRaises(b.CoreBackupError):
            b.CoreBackup(app_source=APP,target=TARGET,bundles=self.bundles).inspect(path)
    def test_exact_members_private_modes_symlinks_hardlinks_and_missing_files(self):
        state=self.state(historical=False);path=self.closure(state);ref=state.slot('sing-box').current
        file=path/b._names(ref)[0];raw=file.read_bytes()
        extra=path/'TRUST.json';extra.write_bytes(b'{}');extra.chmod(0o600)
        with self.assertRaises(b.CoreBackupError):self.tool.inspect(path)
        extra.unlink();file.chmod(0o644)
        with self.assertRaises(b.CoreBackupError):self.tool.inspect(path)
        file.chmod(0o600);link=self.root/'hardlink';os.link(file,link)
        with self.assertRaises(b.CoreBackupError):self.tool.inspect(path)
        link.unlink();file.unlink()
        with self.assertRaises(b.CoreBackupError):self.tool.inspect(path)
        outside=self.root/'outside-pack';outside.write_bytes(raw);outside.chmod(0o600);file.symlink_to(outside)
        with self.assertRaises(b.CoreBackupError):self.tool.inspect(path)
        self.assertTrue(file.is_symlink());file.unlink();file.write_bytes(raw);file.chmod(0o600)
        path.chmod(0o755)
        with self.assertRaises(b.CoreBackupError):self.tool.inspect(path)
        self.assertEqual(path.stat().st_mode&0o777,0o755)
    def test_total_budget_and_same_manifest_different_reference_are_refused(self):
        state=self.state(historical=False)
        with patch.object(b,'MAX_ZIP_BYTES',1):
            with self.assertRaisesRegex(b.CoreBackupError,'BACKUP_TOTAL_LIMIT'):self.stage(state)
        self.assertFalse((self.workspace/'closure').exists())
        current=state.slot('sing-box').current;bad=replace(current,pack_sha256='0'*64)
        conflicting=s._build(NEW_APP,TARGET,8,(s.CoreSlot('sing-box',current,bad),state.slot('xray')))
        with self.assertRaisesRegex(b.CoreBackupError,'BACKUP_REFERENCE_CONFLICT'):self.stage(conflicting)
    def test_existing_closure_is_not_reused_overwritten_or_cleaned(self):
        self.stage();path=self.workspace/'closure';original=(path/'SNAPSHOT.json').read_bytes()
        with self.assertRaisesRegex(b.CoreBackupError,'BACKUP_EXISTS'):self.stage()
        self.assertEqual((path/'SNAPSHOT.json').read_bytes(),original)
        foreign=self.workspace/'foreign';foreign.mkdir(mode=0o700);(foreign/'data').write_bytes(b'foreign')
        with self.assertRaises(b.CoreBackupError):self.stage(name='foreign')
        self.assertEqual((foreign/'data').read_bytes(),b'foreign')
    def test_archive_context_exit_failure_does_not_publish_even_after_all_bytes_copied(self):
        state=self.state(historical=False);opened=fs.CoreArtifactStore.open_archive
        @contextmanager
        def failed(store,*args,**kwargs):
            with opened(store,*args,**kwargs) as stream:yield stream
            raise fs.CoreStoreError('CLOSE_FAILED')
        with patch.object(fs.CoreArtifactStore,'open_archive',new=failed):
            with self.assertRaisesRegex(b.CoreBackupError,'CLOSE_FAILED'):self.stage(state)
        self.assertFalse((self.workspace/'closure').exists());self.assertFalse(list(self.workspace.glob('prepare-*')))
    def test_close_lost_ack_after_publish_preserves_complete_closure(self):
        directory=b._directory;close=fs._Scope.close;owned=[]
        @contextmanager
        def captured(path,**kwargs):
            with directory(path,**kwargs) as value:
                if path==self.workspace:owned.append(value[0])
                yield value
        def failed(scope):
            close(scope)
            if scope in owned:raise fs.CoreStoreError('CLOSE_FAILED')
        with patch.object(b,'_directory',new=captured),patch.object(fs._Scope,'close',new=failed):
            with self.assertRaisesRegex(b.CoreBackupError,'CLOSE_FAILED'):self.stage()
        path=self.workspace/'closure';self.assertTrue(path.is_dir());self.tool.inspect(path)
        self.assertFalse(list(self.workspace.glob('prepare-*')))
    def test_historical_store_checks_all_extracted_material_not_just_original_zip(self):
        state=self.state();previous=state.slot('sing-box').previous
        binary=self.store.layout.root/'artifacts'/previous.release_manifest_sha256/'bin/sing-box'
        original=binary.read_bytes();binary.chmod(0o700);binary.write_bytes(b'changed historical material');binary.chmod(0o555)
        with self.assertRaises(b.CoreBackupError):self.stage(state)
        self.assertFalse((self.workspace/'closure').exists())
        self.assertEqual(binary.read_bytes(),b'changed historical material')
        self.assertNotEqual(binary.read_bytes(),original)
    def test_inspect_budget_and_metadata_limit_are_enforced_without_mutation(self):
        state=self.state(historical=False);path=self.closure(state)
        with patch.object(b,'MAX_ZIP_BYTES',1):
            with self.assertRaisesRegex(b.CoreBackupError,'BACKUP_TOTAL_LIMIT'):self.tool.inspect(path)
        snapshot=path/'SNAPSHOT.json';original=snapshot.read_bytes();snapshot.write_bytes(b' '*64001)
        with self.assertRaises(b.CoreBackupError):self.tool.inspect(path)
        self.assertEqual(snapshot.read_bytes(),b' '*64001);snapshot.write_bytes(original)
        self.tool.inspect(path)
    def test_only_one_invalid_intent_can_block_stage_before_any_workspace_output(self):
        invalid=self.intents(self.initial);invalid['xray']['enabled']=True;invalid['xray']['revision']=None
        with self.assertRaisesRegex(b.CoreBackupError,'BACKUP_INTENT'):
            self.tool.stage(self.workspace,'invalid-intent',selection=self.initial,intents=invalid,panel_was_running=True)
        self.assertEqual(list(self.workspace.iterdir()),[])

    def test_no_replace_lost_ack_and_foreign_collision_never_delete_public_content(self):
        rename=fs._rename_new
        def lost(*args):rename(*args);raise fs.CoreStoreError('PUBLISH_FAILED')
        with patch.object(fs,'_rename_new',side_effect=lost):
            with self.assertRaises(b.CoreBackupError):self.stage()
        self.tool.inspect(self.workspace/'closure')
        def race(parent,name,destination,final):
            os.mkdir(final,0o700,dir_fd=destination)
            return rename(parent,name,destination,final)
        with patch.object(fs,'_rename_new',side_effect=race):
            with self.assertRaises(b.CoreBackupError):self.stage(name='foreign-race')
        self.assertEqual(list((self.workspace/'foreign-race').iterdir()),[])
        self.assertFalse(list(self.workspace.glob('prepare-*')))


if __name__=='__main__':unittest.main()
