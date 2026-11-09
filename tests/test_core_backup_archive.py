"""Complete fake data+component archive checks; no restore/service/native run."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from app import release_tools as tools
from app.services.proxy_store import ProxyStore as ProxyStoreForTest
from app.core_updates import backup_archive as full, selection as s, catalog as c, store as fs
from app.core_updates import selection_files as sf
import test_core_backup as fixtures


class CompleteBackupTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.CoreBackupTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root/'instance';self.root.mkdir(mode=0o700)
        self.data=self.root/'data';self.data.mkdir(mode=0o700)
        self.guard=tools._StoppedGuard(self.root)  # Explicit fake stopped proof.
        self.addCleanup(lambda:setattr(self.guard,'_valid',False))
        with sqlite3.connect(self.data/'v-ui.db') as connection:
            connection.executescript('CREATE TABLE users(id INTEGER,username TEXT,password_hash TEXT);'
                'CREATE TABLE inbounds(id INTEGER,raw_config TEXT);CREATE TABLE admin_sessions(token_hash TEXT);'
                'CREATE TABLE subscription_grants(id INTEGER,revoked INTEGER);'
                "INSERT INTO users VALUES(1,'fake-user','fake-hash');"
                "INSERT INTO inbounds VALUES(7,'original node bytes');"
                "INSERT INTO admin_sessions VALUES('fake-session');"
                'INSERT INTO subscription_grants VALUES(1,0);')
        (self.data/'v-ui.db').chmod(0o600)
        (self.data/'hidden-keep.json').write_bytes(b'private original')
        (self.data/'.unapproved-hidden').write_bytes(b'must not broaden hidden copy')
        for name in ('hidden-keep.json', '.unapproved-hidden'):
            (self.data/name).chmod(0o600)
        self.archive=self.root/'complete.zip'

    def restore_marker(self, journal):
        metadata = self.root/'metadata'
        metadata.mkdir(mode=0o755)
        metadata.chmod(0o755)
        files = sf.SelectionFiles(sf.Layout(metadata, os.geteuid(), self.root))
        transaction = 'a'*32
        marker = metadata/sf.PENDING
        marker.write_bytes(sf._raw(dict(schema=1, kind='core-update-pending-v1', transaction=transaction)))
        marker.chmod(0o644)
        with files._io() as (scope, directory):
            _, record = sf._read(scope, directory, sf.PENDING)
        journal.update(operation='restore', transaction=transaction, original_files=dict(pending=record))
        return files

    def test_restore_exchange_retries_after_each_first_rename(self):
        from app.core_updates.selection_files import restore_data_position
        recovery = self.root / 'recovery'
        recovery.mkdir(mode=0o700)
        slot = 'b' * 32
        incoming = recovery / ('core-restore-' + slot + '-incoming')
        incoming.mkdir(mode=0o700)
        (incoming / 'fixture').write_bytes(b'candidate')
        (incoming / 'fixture').chmod(0o600)

        def generation(path):
            info = path.stat()
            return dict(dev=info.st_dev, ino=info.st_ino)

        with tools.stopped(self.root) as guard:
            journal = dict(phase='backed_up', sequence=3,
                switch_context=dict(data_generation=generation(self.data)),
                restore_target=dict(kind='core-restore-target-v2',
                    data=dict(slot=slot, incoming_generation=generation(incoming))),
                quiesced=dict(original_tree_sha256=tools.recovery_tree_digest(self.data),
                              prepared_tree_sha256=tools.recovery_tree_digest(incoming)))
            files = self.restore_marker(journal)
            wrong = dict(journal, transaction='f'*32)
            with self.assertRaisesRegex(sf.CoreSelectionFilesError, 'PENDING_CHANGED'):
                full.exchange_restore_data(guard, wrong, _selection_files=files)
            self.assertTrue(incoming.is_dir())
            for rollback, expected in ((False, 'trial'), (True, 'restored')):
                def lose_ack(*args):
                    fs._rename_new(*args)
                    raise OSError('fixture lost acknowledgement')
                with patch.object(full, '_rename_new', side_effect=lose_ack), self.assertRaises(OSError):
                    full.exchange_restore_data(guard, journal, rollback=rollback, _selection_files=files)
                receipt = full.exchange_restore_data(guard, journal, rollback=rollback, _selection_files=files)
                self.assertEqual(restore_data_position(journal, receipt['data']), expected)
                self.assertEqual(receipt['transaction'], journal['transaction'])
                self.assertEqual(receipt['sequence'], journal['sequence'])
                self.assertEqual(receipt['tree_sha256'], tools.recovery_tree_digest(self.data))
            self.assertTrue((self.data / 'v-ui.db').is_file())
            rejected = recovery / ('core-restore-' + slot + '-rejected')
            self.assertEqual((rejected / 'fixture').read_bytes(), b'candidate')
            marker = files.layout.root/sf.PENDING
            copy = marker.with_suffix('.copy')
            copy.write_bytes(marker.read_bytes())
            copy.chmod(0o644)
            os.replace(copy, marker)
            with self.assertRaisesRegex(sf.CoreSelectionFilesError, 'PENDING_CHANGED'):
                full.exchange_restore_data(guard, journal, rollback=True, _selection_files=files)
            self.assertTrue((self.data / 'v-ui.db').is_file())

    def test_prepared_candidate_is_retained_in_journal_slot_after_context_closes(self):
        state, intents = self.state(components=False)
        certs = self.data/'certs'
        certs.mkdir(mode=0o700)
        chain = certs/'provided-fullchain.pem'
        chain.write_bytes(b'fixture public chain')
        chain.chmod(0o600)
        checksum = self.create(state, intents)
        before = (self.data/'v-ui.db').read_bytes()
        slot = 'c'*32
        with self.preflight(checksum) as checked, tools.stopped(self.root) as guard, \
                self.prepare(guard, checked, state) as prepared:
            info = self.data.stat()
            journal = dict(phase='prepared', sequence=0, quiesced=None,
                before=json.loads(s.encode_selection(state)),
                after=json.loads(s.encode_selection(s._build(state.app_source, state.target,
                    state.generation+1, state.slots))),
                switch_context=dict(data_generation=dict(dev=info.st_dev, ino=info.st_ino)),
                restore_target=dict(kind='core-restore-target-v2', source_backup_sha256=checksum,
                    intents=deepcopy(intents),
                    data=dict(slot=slot, incoming_generation=checked.data_generation,
                              incoming_trust_sha256=hashlib.sha256(chain.read_bytes()).hexdigest(),
                              preflight_tree_sha256=checked.data_sha256)))
            files = self.restore_marker(journal)
            receipt = full.exchange_restore_data(guard, journal, rollback=True, _selection_files=files)
            self.assertIsNone(receipt['data']['incoming'])
            self.assertIsNone(receipt['tree_sha256'])
            wrong = deepcopy(journal)
            wrong['restore_target']['intents']['sing-box']['enabled'] = False
            with self.assertRaisesRegex(tools.ReleaseError, 'selection or intents'):
                full.place_restore_candidate(guard, wrong, prepared,
                    core_backup=self.fixture.tool, current_selection=state, _selection_files=files)
            self.assertTrue(prepared.data_path.is_dir())
            wrong = deepcopy(journal)
            wrong['restore_target']['data']['incoming_trust_sha256'] = '0'*64
            with self.assertRaisesRegex(tools.ReleaseError, 'public certificate chain'):
                full.place_restore_candidate(guard, wrong, prepared,
                    core_backup=self.fixture.tool, current_selection=state, _selection_files=files)
            self.assertTrue(prepared.data_path.is_dir())
            original = full.place_restore_candidate(guard, journal, prepared,
                core_backup=self.fixture.tool, current_selection=state, _selection_files=files)
            self.assertEqual(original['data_generation'], journal['switch_context']['data_generation'])
        incoming = self.root/'recovery'/('core-restore-'+slot+'-incoming')
        self.assertTrue((incoming/'v-ui.db').is_file())
        self.assertEqual((self.data/'v-ui.db').read_bytes(), before)

    def state(self,components=True,legacy=False):
        state=self.fixture.state() if components else self.fixture.initial
        intents={}
        for core in s.CORES:
            directory=self.data/'runtime'/core;directory.mkdir(mode=0o700,parents=True,exist_ok=True)
            (self.data/'runtime').chmod(0o700)
            raw=json.dumps({'fake_config_for':core},separators=(',',':')).encode()
            revision=hashlib.sha256(raw).hexdigest();enabled=core=='sing-box'
            (directory/(revision+'.json')).write_bytes(raw);(directory/(revision+'.json')).chmod(0o600)
            identity=s.reference_identity(state.slot(core).current)
            value=dict(revision=revision,enabled=enabled)
            if not legacy:value.update(schema=2,artifact_id=identity)
            (directory/'state.json').write_text(json.dumps(value));(directory/'state.json').chmod(0o600)
            intents[core]=dict(artifact_id=identity,revision=revision,enabled=enabled)
        return state,intents

    def create(self,state,intents):
        return full.create(self.guard,self.archive,core_backup=self.fixture.tool,
            selection=state,intents=intents,panel_was_running=True)

    def preflight(self,checksum,tool=None):
        return full.preflight(self.archive,checksum,self.fixture.workspace,
            core_backup=self.fixture.tool if tool is None else tool,expected_data_path=self.data)

    def prepare(self, guard, checked, state):
        return full.prepare_restore(guard, checked, core_backup=self.fixture.tool, current_selection=state)

    def validate_prepared(self, guard, prepared, state):
        return full.validate_restore_against_current(guard, prepared,
            core_backup=self.fixture.tool, current_selection=state)

    def dns_fixture(self):
        from app.certificates.dns_store import DNSCredentialStore
        from app.certificates.dns_journal import DNSChallengeJournal
        with sqlite3.connect(self.data/'v-ui.db') as db:
            db.executescript("CREATE TABLE managed_certificates(id TEXT PRIMARY KEY, domain TEXT, environment TEXT,"
                "method TEXT, wildcard INTEGER, credential_id TEXT, auto_renew INTEGER);"
                "CREATE TABLE certificate_bindings(target TEXT, certificate_id TEXT, applied_certificate_id TEXT,"
                "applied_revision TEXT, error TEXT, server_name TEXT); CREATE TABLE certificate_jobs(id TEXT);"
                "CREATE TABLE certificate_identity_schema(version INTEGER NOT NULL);"
                "INSERT INTO certificate_identity_schema VALUES(1);")
        store=DNSCredentialStore(self.data/'certificates')
        profile=store.create(label='Fake DNS',zone_name='example.test',zone_id='1'*32,api_token='fake-token-'+'x'*40)
        frozen=store.snapshot(profile['id'])
        with sqlite3.connect(self.data/'v-ui.db') as db:
            db.execute('INSERT INTO managed_certificates VALUES(?,?,?,?,?,?,?)',
                ('a'*32,'node.example.test','production','dns-01',1,profile['id'],1))
        journal=DNSChallengeJournal(self.data/'certificates')
        journal.begin('b'*32,profile['id'],frozen['revision'],'node.example.test',
                      ['node.example.test','*.node.example.test'])
        fragment=store.root/profile['id']/('.write-'+'e'*32)
        fragment.write_bytes(b'');fragment.chmod(0o600)
        return store,profile

    def crafted(self,checksum,mutate,mutate_core=None):
        with tempfile.TemporaryDirectory(prefix='.restore-forgery-',dir=self.root) as temporary:
            work=Path(temporary);outer=work/'outer'
            metadata=tools.unpack_verified(self.archive,checksum,outer);(outer/tools.MANIFEST).unlink()
            inner=outer/'data/snapshot.zip';data=work/'data'
            inner_meta=tools.unpack_verified(inner,tools.file_digest(inner),data);(data/tools.MANIFEST).unlink()
            mutate(data)
            inner.unlink()
            tools.create_archive(data,inner,{k:v for k,v in inner_meta.items() if k not in ('schema','files')})
            if mutate_core is not None:mutate_core(outer/'core')
            self.archive=self.root/'crafted.zip'
            return tools.create_archive(outer,self.archive,{k:v for k,v in metadata.items() if k not in ('schema','files')})

    def test_dns_profiles_pending_fragments_and_journal_survive_without_suspension(self):
        state,intents=self.state();store,profile=self.dns_fixture()
        original={p.relative_to(self.data).as_posix():p.read_bytes() for p in (self.data/'certificates').rglob('*')
                  if p.is_file() and not p.name.endswith('owner.lock')}
        checksum=self.create(state,intents)
        with self.preflight(checksum) as checked:
            self.assertEqual(checked.data_kind,'backup-dns01-v1')
            for name,raw in original.items():self.assertEqual((checked.data_path/name).read_bytes(),raw)
            self.assertEqual(store.list()[0]['revision'],profile['revision'])
            with sqlite3.connect(checked.data_path/'v-ui.db') as db:
                self.assertEqual(db.execute('SELECT auto_renew FROM managed_certificates').fetchone()[0],1)
        for name,raw in original.items():self.assertEqual((self.data/name).read_bytes(),raw)

    def test_proxy_private_marker_and_empty_secrets_roundtrip_without_archiving_lock(self):
        from app.services.proxy_store import ProxyStore
        state,intents=self.state();snapshot=ProxyStore(self.data).read_snapshot()
        marker=(self.data/'.proxy-state-initialized').read_bytes()
        checksum=self.create(state,intents)
        with zipfile.ZipFile(self.archive) as outer,zipfile.ZipFile(io.BytesIO(outer.read('data/snapshot.zip'))) as inner:
            self.assertIn('.proxy-state-initialized',inner.namelist())
            self.assertNotIn('.proxy-state.lock',inner.namelist())
        with self.preflight(checksum) as checked:
            self.assertEqual((checked.data_path/'.proxy-state-initialized').read_bytes(),marker)
            self.assertEqual(ProxyStore(checked.data_path).read_snapshot(initialize=False),snapshot)
        self.assertEqual(ProxyStore(self.data).read_snapshot(initialize=False),snapshot)

    def active_proxy(self,state,intents):
        from app.services.proxy_store import ProxyStore
        store=ProxyStore(self.data);initial=store.read_snapshot()
        saved=store.save_upstream({'name':'Fake upstream','server':'upstream.example','server_port':443,
            'uuid':'11111111-1111-4111-8111-111111111111','tls':{'mode':'tls','server_name':'upstream.example'}},initial['revision'])
        settings={k:v for k,v in saved['snapshot']['settings'].items() if k!='local_password_set'}
        settings.update(relay_enabled=True,local_proxy_enabled=True,local_username='fake-user',selected_upstream_id=saved['upstream_id'])
        draft=store.save_draft(settings,saved['snapshot']['revision'],local_password='fake-password-for-tests')
        config={'inbounds':[{'tag':'vui-relay-ingress-1','type':'vless','listen_port':24443}],
                'outbounds':[{'type':'direct','tag':'direct'}]}
        raw=json.dumps(config,sort_keys=True,separators=(',',':')).encode();revision=hashlib.sha256(raw).hexdigest()
        directory=self.data/'runtime/sing-box';(directory/(revision+'.json')).write_bytes(raw);(directory/(revision+'.json')).chmod(0o600)
        value=dict(schema=2,artifact_id=intents['sing-box']['artifact_id'],revision=revision,enabled=True)
        (directory/'state.json').write_text(json.dumps(value))
        intents['sing-box']=dict(artifact_id=value['artifact_id'],revision=revision,enabled=True)
        context=dict(schema_version=1,kind='apply',candidate_core_revision=revision,previous_core_revision=None,
            previous_enabled=False,policy_identity='b'*64,
            endpoint=dict(profile_id=saved['upstream_id'],revision=draft['revision'],host='upstream.example',
                          addresses=['8.8.4.4'],resolved_at=1),self_addresses=['127.0.0.1'])
        store.begin_apply(draft['revision'],'fake-apply',runtime_context=context)
        store.commit_apply('fake-apply',revision)
        return store

    def test_active_proxy_schema2_uses_exact_component_binding_without_relaxing_old_reader(self):
        state,intents=self.state();store=self.active_proxy(state,intents);original=store.load_active()
        with self.assertRaises(tools.ReleaseError):tools._policy_runtime(self.data,'sing-box')
        with self.assertRaises(tools.ReleaseError):tools._policy_runtime(self.data,'sing-box',_expected_artifact_id='f'*64)
        checksum=self.create(state,intents)
        with self.preflight(checksum) as checked:
            self.assertEqual(ProxyStoreForTest(checked.data_path).load_active(),original)
        self.assertEqual(store.load_active(),original)

    def test_active_proxy_and_enabled_xray_are_rejected_in_writer_and_rehashed_reader(self):
        state,intents=self.state();self.active_proxy(state,intents);checksum=self.create(state,intents)
        def mutate(data):
            path=data/'runtime/xray/state.json';value=json.loads(path.read_bytes());value['enabled']=True
            path.write_text(json.dumps(value))
        def mutate_core(core):
            path=core/'SNAPSHOT.json';value=json.loads(path.read_bytes());value['intents']['xray']['enabled']=True
            path.write_text(json.dumps(value))
        checksum=self.crafted(checksum,mutate,mutate_core)
        with self.assertRaises(tools.ReleaseError):
            with self.preflight(checksum):pass
        mutate(self.data);intents['xray']['enabled']=True
        with self.assertRaises(tools.ReleaseError):self.create(state,intents)

    def test_policy_checker_rejects_partial_unknown_or_invalid_binding_maps(self):
        state,intents=self.state()
        for identities in ({},{'sing-box':'a'*64},{'sing-box':'a'*64,'xray':True},
                           {'sing-box':'a'*64,'xray':'b'*64,'extra':'c'*64}):
            with self.subTest(identities=identities),self.assertRaises(tools.ReleaseError):
                tools._validate_restore_policy(self.data,_artifact_ids=identities)

    def test_rehashed_archive_with_runtime_mismatch_is_not_a_restore_input(self):
        state,intents=self.state();checksum=self.create(state,intents)
        def mutate(data):
            path=data/'runtime/sing-box/state.json';value=json.loads(path.read_bytes());value['enabled']=False
            path.write_text(json.dumps(value))
        checksum=self.crafted(checksum,mutate)
        with self.assertRaises(tools.ReleaseError):
            with self.preflight(checksum):pass
        self.assertTrue(json.loads((self.data/'runtime/sing-box/state.json').read_bytes())['enabled'])

    def test_rehashed_dns_archive_missing_private_reference_is_rejected(self):
        state,intents=self.state();store,profile=self.dns_fixture();checksum=self.create(state,intents)
        def mutate(data):
            path=next((data/'certificates/dns-credentials'/profile['id']).glob('*.json'))
            if path.name=='state.json':
                path=next(p for p in path.parent.glob('*.json') if p.name!='state.json')
            path.unlink()
        checksum=self.crafted(checksum,mutate)
        with self.assertRaises(tools.ReleaseError):
            with self.preflight(checksum):pass
        self.assertEqual(store.list()[0]['revision'],profile['revision'])

    def test_full_modern_archive_preserves_original_data_and_all_four_components(self):
        state,intents=self.state();before=(self.data/'v-ui.db').read_bytes()
        original_states={core:(self.data/'runtime'/core/'state.json').read_bytes()for core in s.CORES}
        checksum=self.create(state,intents)
        self.assertEqual(tools.file_digest(self.archive),checksum)
        self.assertEqual(self.archive.stat().st_mode&0o777,0o600)
        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual(len(archive.infolist()),15)
            self.assertEqual(json.loads(archive.read(tools.MANIFEST))['kind'],full.KIND)
        with self.preflight(checksum) as checked:
            extracted=checked.data_path
            self.assertEqual(checked.core_closure.selection,state)
            self.assertEqual(len(checked.core_closure.components),4)
            self.assertEqual(checked.requires_legacy_migration,())
            for core in s.CORES:self.assertEqual((extracted/'runtime'/core/'state.json').read_bytes(),original_states[core])
            self.assertFalse((extracted/'.unapproved-hidden').exists())
            self.assertEqual((extracted/'hidden-keep.json').read_bytes(),b'private original')
            with sqlite3.connect(extracted/'v-ui.db') as connection:
                self.assertEqual(connection.execute('SELECT raw_config FROM inbounds').fetchone()[0],'original node bytes')
                self.assertEqual(connection.execute('SELECT count(*) FROM admin_sessions').fetchone()[0],1)
                self.assertEqual(connection.execute('SELECT revoked FROM subscription_grants').fetchone()[0],0)
        self.assertFalse(extracted.exists())
        self.assertEqual((self.data/'v-ui.db').read_bytes(),before)
        self.assertEqual(list(self.root.glob('RESTORE_PENDING*')),[])

    def test_legacy_bundle_bytes_are_reported_for_explicit_migration_without_rewriting(self):
        state,intents=self.state(components=False,legacy=True)
        checksum=self.create(state,intents)
        with self.preflight(checksum) as checked:
            self.assertEqual(set(checked.requires_legacy_migration),set(s.CORES))
            for core in s.CORES:
                self.assertEqual((checked.data_path/'runtime'/core/'state.json').read_bytes(),
                                 (self.data/'runtime'/core/'state.json').read_bytes())
                self.assertNotIn('schema',json.loads((checked.data_path/'runtime'/core/'state.json').read_bytes()))

    def test_component_current_cannot_adopt_legacy_state_without_artifact_binding(self):
        state,intents=self.state(legacy=True)
        with self.assertRaises(tools.ReleaseError):self.create(state,intents)
        self.assertFalse(self.archive.exists())

    def test_invalid_or_unfenced_database_modes_cannot_be_archived(self):
        state,intents=self.state(components=False)
        with sqlite3.connect(self.data/'v-ui.db') as db:db.execute('ALTER TABLE inbounds ADD COLUMN routing_mode')
        for value in ('invalid-mode',None,3,'direct','relay'):
            with self.subTest(value=value):
                with sqlite3.connect(self.data/'v-ui.db') as db:db.execute('UPDATE inbounds SET routing_mode=?',(value,))
                with self.assertRaises(tools.ReleaseError):self.create(state,intents)
                self.assertFalse(self.archive.exists())

    def test_rehashed_archive_with_unfenced_database_modes_is_refused(self):
        state,intents=self.state(components=False);checksum=self.create(state,intents)
        def mutate(data):
            with sqlite3.connect(data/'v-ui.db') as db:
                db.execute('ALTER TABLE inbounds ADD COLUMN routing_mode')
                db.execute("UPDATE inbounds SET routing_mode='relay'")
        checksum=self.crafted(checksum,mutate)
        with self.assertRaises(tools.ReleaseError):
            with self.preflight(checksum):pass
        with sqlite3.connect(self.data/'v-ui.db') as db:
            self.assertNotIn('routing_mode',{row[1]for row in db.execute('PRAGMA table_info(inbounds)')})

    def test_actual_revision_enabled_and_artifact_binding_must_match_snapshot(self):
        state,intents=self.state()
        for field,value in (('revision','f'*64),('enabled',False),('artifact_id','f'*64)):
            wrong=deepcopy(intents);wrong['sing-box'][field]=value
            with self.subTest(field=field),self.assertRaises(tools.ReleaseError):self.create(state,wrong)
            self.assertFalse(self.archive.exists())

    def test_inner_data_budget_is_not_increased_by_component_budget(self):
        state,intents=self.state(components=False)
        with patch.object(tools,'MAX_EXPANDED',100),self.assertRaises(tools.ReleaseError):self.create(state,intents)
        self.assertFalse(self.archive.exists())

    def test_inner_manifest_counts_against_unchanged_expanded_budget_before_publication(self):
        state,intents=self.state(components=False)
        original=tools.create_archive
        def create_with_boundary(payload,destination,metadata,**kwargs):
            checksum=original(payload,destination,metadata,**kwargs)
            if metadata['kind']=='backup':
                with zipfile.ZipFile(destination) as inner:
                    content=sum(item.file_size for item in inner.infolist() if item.filename!=tools.MANIFEST)
                self.boundary_patch=patch.object(tools,'MAX_EXPANDED',content)
                self.boundary_patch.start();self.addCleanup(self.boundary_patch.stop)
            return checksum
        with patch.object(tools,'create_archive',side_effect=create_with_boundary),self.assertRaises(tools.ReleaseError):
            self.create(state,intents)
        self.assertFalse(self.archive.exists())

    def test_missing_independent_catalog_cannot_be_supplied_by_backup_metadata(self):
        state,intents=self.state();checksum=self.create(state,intents)
        absent=c.CoreCatalog(fs.Layout(self.fixture.root/'absent',os.geteuid(),self.fixture.root))
        tool=self.fixture.reader(catalog=absent)
        with self.assertRaises(tools.ReleaseError):
            with self.preflight(checksum,tool):pass
        self.assertFalse(absent.layout.root.exists())

    def test_wrong_archive_sha_or_data_path_leaves_live_data_unchanged(self):
        state,intents=self.state(components=False);checksum=self.create(state,intents)
        before=(self.data/'v-ui.db').read_bytes()
        with self.assertRaises(tools.ReleaseError):
            with self.preflight('0'*64):pass
        with self.assertRaises(tools.ReleaseError):
            with full.preflight(self.archive,checksum,self.fixture.workspace,
                    core_backup=self.fixture.tool,expected_data_path=self.fixture.root/'foreign'):pass
        self.assertEqual((self.data/'v-ui.db').read_bytes(),before)

    def test_existing_public_backup_is_not_overwritten_or_deleted(self):
        state,intents=self.state(components=False);self.archive.write_bytes(b'foreign backup')
        with self.assertRaises(tools.ReleaseError):self.create(state,intents)
        self.assertEqual(self.archive.read_bytes(),b'foreign backup')

    def test_generic_restore_still_rejects_new_kind_without_exchanging_data(self):
        state,intents=self.state(components=False);checksum=self.create(state,intents)
        before=(self.data/'v-ui.db').read_bytes()
        with self.assertRaises(tools.ReleaseError):tools._restore_locked(self.guard,self.archive,checksum)
        self.assertEqual((self.data/'v-ui.db').read_bytes(),before)
        self.assertFalse((self.root/'RESTORE_PENDING.json').exists())

    def test_source_symlink_hardlink_or_total_budget_are_refused(self):
        state,intents=self.state(components=False);checksum=self.create(state,intents)
        alias=self.root/'alias.zip';alias.symlink_to(self.archive)
        with self.assertRaises(tools.ReleaseError):
            with full.preflight(alias,checksum,self.fixture.workspace,core_backup=self.fixture.tool,expected_data_path=self.data):pass
        alias.unlink();os.link(self.archive,alias)
        with self.assertRaises(tools.ReleaseError):
            with self.preflight(checksum):pass
        alias.unlink()
        with patch.object(full,'MAX_ARCHIVE',10),self.assertRaises(tools.ReleaseError):
            with self.preflight(checksum):pass

    def test_prepare_revokes_private_authority_suspends_dns_and_preserves_config_and_missing_core(self):
        state, intents = self.state(components=False, legacy=True)
        xray = self.data/'runtime/xray'
        for path in xray.iterdir():
            path.unlink()
        xray.rmdir()
        intents['xray'].update(revision=None, enabled=False)
        _, profile = self.dns_fixture()
        for name in ('parent', 'parent/child', 'parent/child/leaf'):
            (self.data/name).mkdir(mode=0o700)
        payload = self.data/'parent/child/leaf/original.json'
        payload.write_bytes(b'preserved');payload.chmod(0o600)
        with sqlite3.connect(self.data/'v-ui.db') as db:
            db.executescript("CREATE TABLE maintenance_sessions(id TEXT);"
                "CREATE TABLE maintenance_grants(id TEXT); INSERT INTO maintenance_sessions VALUES('fake');"
                "INSERT INTO maintenance_grants VALUES('fake');")
        live_db = (self.data/'v-ui.db').read_bytes()
        config = self.data/'runtime/sing-box'/(intents['sing-box']['revision']+'.json')
        profile_name = Path('certificates/dns-credentials')/profile['id']/'state.json'
        live_dns = (self.data/profile_name).read_bytes()
        checksum = self.create(state, intents)
        with self.preflight(checksum) as checked:
            self.archive.rename(self.root/'retained.zip')  # Preparation consumes the open preflight, not the ZIP.
            self.assertEqual(checked.data_sha256, tools.recovery_tree_digest(checked.data_path))
            self.assertEqual(checked.data_generation, dict(dev=checked.data_path.stat().st_dev,
                                                          ino=checked.data_path.stat().st_ino))
            with tools.stopped(self.root) as guard, self.prepare(guard, checked, state) as prepared:
                stage = prepared.data_path
                self.assertEqual(prepared.archive_sha256, checksum)
                self.assertEqual(prepared.migrated_cores, ('sing-box',))
                self.assertEqual(prepared.data_generation, dict(dev=stage.stat().st_dev, ino=stage.stat().st_ino))
                self.assertEqual((stage/'parent').stat().st_mode & 0o777, 0o700)
                self.assertEqual((stage/payload.relative_to(self.data)).read_bytes(), b'preserved')
                self.assertEqual((stage/config.relative_to(self.data)).read_bytes(), config.read_bytes())
                self.assertFalse((stage/'runtime/xray').exists())
                self.assertEqual(json.loads((stage/'runtime/sing-box/state.json').read_bytes()),
                                 dict(schema=2, **intents['sing-box']))
                dns = json.loads((stage/profile_name).read_bytes())
                self.assertFalse(dns['enabled']);self.assertTrue(dns['restore_pending'])
                with sqlite3.connect(stage/'v-ui.db') as db:
                    for table in ('admin_sessions', 'maintenance_sessions', 'maintenance_grants'):
                        self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone(), (0,))
                    self.assertEqual(db.execute('SELECT revoked FROM subscription_grants').fetchone(), (1,))
                    self.assertEqual(db.execute('SELECT raw_config FROM inbounds').fetchone(), ('original node bytes',))
                    self.assertEqual(db.execute('SELECT username FROM users').fetchone(), ('fake-user',))
                    self.assertEqual(db.execute('SELECT auto_renew FROM managed_certificates').fetchone(), (1,))
                self.assertEqual(tools.recovery_tree_digest(stage), prepared.data_sha256)
                self.assertNotEqual(prepared.data_sha256, checked.data_sha256)
                old = self.validate_prepared(guard, prepared, state)
                self.assertEqual(old['data_sha256'], tools.recovery_tree_digest(self.data))
                self.assertEqual(old['data_generation'], dict(dev=self.data.stat().st_dev, ino=self.data.stat().st_ino))
                with self.assertRaises(tools.ReleaseError):
                    with self.prepare(guard, checked, state):pass
        self.assertFalse(stage.exists())
        self.assertEqual((self.data/'v-ui.db').read_bytes(), live_db)
        self.assertEqual((self.data/profile_name).read_bytes(), live_dns)
        self.assertFalse((self.root/'RESTORE_PENDING.json').exists())

    def test_prepare_requires_unmodified_preflight_and_cannot_be_forged_or_reused(self):
        state, intents = self.state(components=False)
        checksum = self.create(state, intents)
        original = (self.data/'v-ui.db').read_bytes()
        with self.preflight(checksum) as checked, tools.stopped(self.root) as guard:
            with self.assertRaises(tools.ReleaseError):
                with self.prepare(guard, replace(checked, data_path=self.data), state):pass
            (checked.data_path/'hidden-keep.json').write_bytes(b'private tamper')
            with self.assertRaisesRegex(tools.ReleaseError, 'staging changed after preflight'):
                with self.prepare(guard, checked, state):pass
        with self.assertRaises(tools.ReleaseError):
            with self.prepare(self.guard, checked, state):pass
        self.assertEqual((self.data/'v-ui.db').read_bytes(), original)

    def test_prepare_without_live_exclusive_refuses_before_any_stage_mutation(self):
        state, intents = self.state(components=False, legacy=True)
        self.dns_fixture()
        checksum = self.create(state, intents)
        live_db = (self.data/'v-ui.db').read_bytes()
        with self.preflight(checksum) as checked:
            stage_db = (checked.data_path/'v-ui.db').read_bytes()
            stage_runtime = (checked.data_path/'runtime/sing-box/state.json').read_bytes()
            with patch.object(full, '_migrate_stage_runtime') as migrate, \
                    patch.object(tools, '_suspend_restore_dns') as suspend, \
                    patch.object(tools, '_revoke_restore_access') as revoke:
                with self.assertRaisesRegex(tools.ReleaseError, 'requires exclusive data ownership'):
                    with self.prepare(self.guard, checked, state):pass
                for operation in (migrate, suspend, revoke):
                    operation.assert_not_called()
            self.assertEqual((checked.data_path/'v-ui.db').read_bytes(), stage_db)
            self.assertEqual((checked.data_path/'runtime/sing-box/state.json').read_bytes(), stage_runtime)
        self.assertEqual((self.data/'v-ui.db').read_bytes(), live_db)

    def test_sqlite_open_time_symlink_to_live_is_refused_before_any_dml(self):
        state, intents = self.state(components=False)
        checksum = self.create(state, intents)
        live = self.data/'v-ui.db'
        before = live.read_bytes()
        with self.preflight(checksum) as checked, tools.stopped(self.root) as guard:
            staged = checked.data_path/'v-ui.db'
            retained = staged.with_name('retained-original.db')
            connect = sqlite3.connect
            statements = []

            def redirected(location, *args, **kwargs):
                self.assertIn(str(location), (str(staged), staged.as_uri()+'?mode=rw'))
                staged.rename(retained)
                staged.symlink_to(live)
                try:
                    connection = connect(location, *args, **kwargs)
                    connection.set_trace_callback(statements.append)
                    return connection
                finally:
                    staged.unlink()
                    retained.rename(staged)

            with patch.object(tools.sqlite3, 'connect', side_effect=redirected):
                with self.assertRaisesRegex(tools.ReleaseError, 'Restored database changed'):
                    with self.prepare(guard, checked, state):pass
            self.assertEqual(statements, [])
        self.assertEqual(live.read_bytes(), before)

    def test_strict_cleanup_refuses_an_unmatched_connection_inode(self):
        state, intents = self.state(components=False)
        checksum = self.create(state, intents)
        with self.preflight(checksum) as checked, tools.stopped(self.root) as guard:
            db = checked.data_path/'v-ui.db'
            before = db.read_bytes()
            original = tools._writable_regular_fds()
            unexpected = {**original, max(original, default=2)+1: (0, 1)}
            with patch.object(tools, '_writable_regular_fds', side_effect=[original, unexpected]):
                with self.assertRaisesRegex(tools.ReleaseError, 'Restored database changed'):
                    with self.prepare(guard, checked, state):pass
            self.assertEqual(db.read_bytes(), before)

    def test_stage_runtime_parent_replacement_never_writes_through_live_symlink(self):
        state, intents = self.state(components=False, legacy=True)
        checksum = self.create(state, intents)
        live = self.data/'runtime/sing-box'
        before = (live/'state.json').read_bytes()
        with self.preflight(checksum) as checked, tools.stopped(self.root) as guard:
            staged = checked.data_path/'runtime/sing-box'
            retained = staged.with_name('retained-sing-box')
            replace_entry = os.replace
            swaps = []

            def redirected(source, destination, **options):
                self.assertEqual(destination, 'state.json')
                self.assertEqual(options['src_dir_fd'], options['dst_dir_fd'])
                staged.rename(retained)
                staged.symlink_to(live, target_is_directory=True)
                swaps.append(True)
                return replace_entry(source, destination, **options)

            with patch.object(full.os, 'replace', side_effect=redirected):
                with self.assertRaises(tools.ReleaseError):
                    with self.prepare(guard, checked, state):pass
            self.assertEqual(swaps, [True])
            self.assertEqual(json.loads((retained/'state.json').read_bytes()), dict(schema=2, **intents['sing-box']))
        self.assertEqual((live/'state.json').read_bytes(), before)

    def test_validate_requires_real_live_exclusive_lease_and_unmodified_prepared_bytes(self):
        state, intents = self.state(components=False)
        checksum = self.create(state, intents)
        with self.preflight(checksum) as checked, tools.stopped(self.root) as guard, \
                self.prepare(guard, checked, state) as prepared:
            errors = []

            def other_thread():
                try:
                    self.validate_prepared(guard, prepared, state)
                except Exception as error:
                    errors.append(error)

            thread = threading.Thread(target=other_thread)
            thread.start()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], tools.ReleaseError)
            self.assertIn('requires exclusive data ownership', str(errors[0]))
            (prepared.data_path/'hidden-keep.json').write_bytes(b'private tamper')
            with self.assertRaisesRegex(tools.ReleaseError, 'Prepared component restore data changed'):
                self.validate_prepared(guard, prepared, state)

    def test_validate_requires_actual_current_core_ids_even_without_proxy(self):
        state, intents = self.state()
        checksum = self.create(state, intents)
        path = self.data/'runtime/sing-box/state.json'
        value = json.loads(path.read_bytes());value['artifact_id'] = 'f'*64
        path.write_text(json.dumps(value))
        with self.preflight(checksum) as checked, tools.stopped(self.root) as guard, \
                self.prepare(guard, checked, state) as prepared:
            with self.assertRaisesRegex(tools.ReleaseError, 'Current runtime artifact binding differs'):
                self.validate_prepared(guard, prepared, state)
        self.assertEqual(json.loads(path.read_bytes()), value)

    def test_validate_refuses_turning_off_committed_proxy(self):
        state, intents = self.state(components=False)
        checksum = self.create(state, intents)
        store = self.active_proxy(state, intents)
        active = store.load_active()
        with self.preflight(checksum) as checked, tools.stopped(self.root) as guard, \
                self.prepare(guard, checked, state) as prepared:
            with self.assertRaisesRegex(tools.ReleaseError, 'turn off an active proxy'):
                self.validate_prepared(guard, prepared, state)
        self.assertEqual(store.load_active(), active)

    def test_validate_preserves_exact_protected_bindings_and_never_creates_live_proxy_lock(self):
        state, intents = self.state()
        store = self.active_proxy(state, intents)
        checksum = self.create(state, intents)
        active = store.load_active()
        lock = self.data/'.proxy-state.lock'
        lock.unlink()
        with self.preflight(checksum) as checked, tools.stopped(self.root) as guard, \
                self.prepare(guard, checked, state) as prepared:
            self.validate_prepared(guard, prepared, state)
            self.assertFalse(lock.exists())
            self.assertFalse((prepared.data_path/'.proxy-state.lock').exists())
            directory = self.data/'runtime/sing-box'
            raw = (directory/(intents['sing-box']['revision']+'.json')).read_bytes().replace(b'24443',b'24444')
            revision = hashlib.sha256(raw).hexdigest()
            changed = directory/(revision+'.json')
            changed.write_bytes(raw);changed.chmod(0o600)
            store.begin_apply(active['revision'], 'new-binding',
                runtime_context=dict(active['runtime_context'], candidate_core_revision=revision,
                                     previous_core_revision=active['core_revision'], previous_enabled=True))
            store.commit_apply('new-binding', revision)
            (directory/'state.json').write_text(json.dumps(dict(schema=2, **dict(intents['sing-box'], revision=revision))))
            with self.assertRaisesRegex(tools.ReleaseError, 'changes committed protected inbound bindings'):
                self.validate_prepared(guard, prepared, state)

    def test_validate_refuses_proxy_fence_downgrade_without_migrating_sqlite(self):
        state, intents = self.state(components=False)
        checksum = self.create(state, intents)
        with sqlite3.connect(self.data/'v-ui.db') as db:
            db.execute("ALTER TABLE inbounds ADD COLUMN routing_mode TEXT NOT NULL DEFAULT 'inherit'")
        store = ProxyStoreForTest(self.data)
        store.ensure_policy_v2()
        with self.preflight(checksum) as checked, tools.stopped(self.root) as guard, \
                self.prepare(guard, checked, state) as prepared:
            original = (prepared.data_path/'v-ui.db').read_bytes()
            with self.assertRaisesRegex(tools.ReleaseError, 'downgrade the current proxy capability fence'):
                self.validate_prepared(guard, prepared, state)
            self.assertEqual((prepared.data_path/'v-ui.db').read_bytes(), original)
            self.assertFalse((prepared.data_path/'.proxy-state-initialized').exists())
        self.assertEqual(store.policy_schema_version(), 2)

    def test_authorization_triggers_and_unhandled_foreign_keys_fail_closed(self):
        state, intents = self.state(components=False)
        for kind, sql, error in (
                ('trigger', "CREATE TRIGGER auth_mutation AFTER DELETE ON admin_sessions "
                            "BEGIN UPDATE inbounds SET raw_config='unexpected'; END", 'unexpected trigger'),
                ('foreign', "CREATE UNIQUE INDEX session_token ON admin_sessions(token_hash);"
                            "CREATE TABLE business_ref(token TEXT REFERENCES admin_sessions(token_hash));"
                            "INSERT INTO business_ref VALUES('fake-session');", 'invalid database references')):
            with self.subTest(kind=kind):
                self.archive = self.root/(kind+'.zip')
                with sqlite3.connect(self.data/'v-ui.db') as db:
                    if kind == 'foreign':
                        db.execute('DROP TRIGGER auth_mutation')
                    db.executescript(sql)
                checksum = self.create(state, intents)
                with self.preflight(checksum) as checked, tools.stopped(self.root) as guard:
                    with self.assertRaisesRegex(tools.ReleaseError, error):
                        with self.prepare(guard, checked, state):pass
                    with sqlite3.connect(checked.data_path/'v-ui.db') as db:
                        self.assertEqual(db.execute('SELECT count(*) FROM admin_sessions').fetchone(), (1,))
                        self.assertEqual(db.execute('SELECT raw_config FROM inbounds').fetchone(), ('original node bytes',))


if __name__=='__main__':unittest.main()
