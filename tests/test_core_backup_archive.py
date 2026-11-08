"""Complete fake data+component archive checks; no restore/service/native run."""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app import release_tools as tools
from app.services.proxy_store import ProxyStore as ProxyStoreForTest
from app.core_updates import backup_archive as full, selection as s, catalog as c, store as fs
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
        self.archive=self.root/'complete.zip'

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


if __name__=='__main__':unittest.main()
