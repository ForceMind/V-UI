"""Pure fixture checks; these do not qualify any installed upgrade."""
from contextlib import redirect_stdout
from contextlib import contextmanager
import ast
from copy import deepcopy
import hashlib
import io
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
import zipfile
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from published_upgrade_helpers import (FIREWALL, MAINTENANCE_FILES, PUBLISHED_BASELINES, PublishedUpgrade,
                                       assert_download_boundary, assert_receipt_migration, candidate_origin,
                                       assert_upgrade_configuration, fixed_public_baseline,
                                       managed_xray_fixture_program, assert_node_summary_upgrade, assert_editor_content_addition)
from published_upgrade_helpers import (DNS_FILES, CORE_FILES, AUTHORITY_FILES, YAML_FILES, CERTIFICATE_COLUMNS, BINDING_COLUMNS,
    DNS_CERTIFICATE_COLUMNS, DNS_BINDING_LAYOUTS, snapshot_program, yaml_draft_program)


PRE_DNS_BASELINES = tuple(version for version in PUBLISHED_BASELINES if version not in ('0.4.23', '0.4.24', '0.4.25'))

class PublishedUpgradeBoundaryTests(unittest.TestCase):
    def test_summary_additions_are_only_strict_booleans_without_old_field_drift(self):
        before=[{'id':1,'core':'sing-box','port':10443,'remark':'preserve',
                 'enable':True,'routing_mode':'inherit','mutation_revision':'a'*64}]
        after=[{**before[0],'share_eligible':True,'upstream_export_eligible':True,'content_revision':'b'*64}]
        assert_node_summary_upgrade(self,before,after)
        for change in ({'share_eligible':1},{'upstream_export_eligible':None},
                       {'remark':'replaced'},{'routing_mode':'direct'},
                       {'mutation_revision':'b'*64},{'raw_settings':{'secret':'fake'}},{'content_revision':None},{'content_revision':'X'*64},{'content_revision':'b'*64+'\n'}):
            bad=[{**after[0],**change}]
            with self.subTest(change=change),self.assertRaises(AssertionError):
                assert_node_summary_upgrade(self,before,bad)
        for key in after[0]:
            bad=deepcopy(after);del bad[0][key]
            with self.subTest(missing=key),self.assertRaises(AssertionError):
                assert_node_summary_upgrade(self,before,bad)
        with self.assertRaises(AssertionError):assert_node_summary_upgrade(self,before,after+after)

    def test_019_summary_preserves_existing_capabilities_and_all_other_fields(self):
        before=[{'id':1,'routing_mode':'inherit','share_eligible':True,'upstream_export_eligible':False,
                 'mutation_revision':'a'*64}]
        assert_node_summary_upgrade(self,before,[{**before[0],'content_revision':'c'*64}],'0.4.19')
        for change in ({'share_eligible':False},{'routing_mode':'direct'},{'mutation_revision':'b'*64}):
            with self.subTest(change=change),self.assertRaises(AssertionError):
                assert_node_summary_upgrade(self,before,[{**before[0],'content_revision':'c'*64,**change}],'0.4.19')

    def test_editor_addition_never_excuses_path_binding_credential_or_unknown_field_changes(self):
        before={'id':1,'mutation_revision':'a'*64,'certificate_id':'b'*32,
                'profile':{'certificate_path':'/synthetic/cert','key_path':'/synthetic/key'},
                'credentials':{'has_uuid':True}}
        after={**deepcopy(before),'content_revision':'c'*64}
        assert_editor_content_addition(self,before,after)
        for change in ({'id':2},{'mutation_revision':'d'*64},{'certificate_id':None},
                       {'profile':{}},{'credentials':{}},{'extra':'secret'},
                       {'content_revision':None},{'content_revision':'c'*63}):
            with self.subTest(change=change),self.assertRaises(AssertionError):
                assert_editor_content_addition(self,before,{**after,**change})
        for version in ('0.4.19','0.4.20'):
            row={'id':1,'share_eligible':True,'upstream_export_eligible':False,'mutation_revision':'a'*64}
            assert_node_summary_upgrade(self,[row],[{**row,'content_revision':'c'*64}],version)
            with self.assertRaises(AssertionError):
                assert_node_summary_upgrade(self,[row],[{**row,'content_revision':'c'*64,'share_eligible':False}],version)

    def test_public21_has_no_response_field_addition_or_content_cas_drift(self):
        before={'id':1,'mutation_revision':'a'*64,'content_revision':'b'*64,
                'share_eligible':True,'upstream_export_eligible':False,'remark':'preserve'}
        assert_node_summary_upgrade(self,[before],[dict(before)],'0.4.21')
        assert_editor_content_addition(self,before,dict(before))
        for key,value in [('content_revision','c'*64),('content_revision',None),
                          ('remark','changed'),('share_eligible',False),('unknown',1)]:
            with self.subTest(key=key,value=value),self.assertRaises(AssertionError):
                assert_node_summary_upgrade(self,[before],[{**before,key:value}],'0.4.21')
            with self.subTest(editor_key=key),self.assertRaises(AssertionError):
                assert_editor_content_addition(self,before,{**before,key:value})
        missing=dict(before);del missing['content_revision']
        with self.assertRaises(AssertionError):assert_node_summary_upgrade(self,[before],[missing],'0.4.21')

    def test_configuration_requires_exact_runtime_transition_and_preserved_fields(self):
        old_release=Path('/var/lib/v-ui/releases/old-014')
        new_release=Path('/var/lib/v-ui/releases/candidate-015')
        before={'release_id':old_release.name,
                'bootstrap_python':str(old_release/'runtime/python/bin/python3'),
                'domain':'vpn.example.test','admin':'installation-owner','email':'admin@example.test',
                'port':8443,'node_port':10443,'bind':'127.0.0.1','certificate_mode':'provided',
                'origin':'https://vpn.example.test:8443','ready':True,'future_field':'retain-me'}
        after={**before,'release_id':new_release.name,
               'bootstrap_python':str(new_release/'runtime/python/bin/python3'),'ca_authorizations':[]}
        assert_upgrade_configuration(self,before,after,old_release,new_release)
        self.assertNotIn('ca_authorizations',before,'Comparison must not modify the old observation')
        for field,value in {'domain':'changed.example.test','admin':'replaced-owner','email':'other@example.test',
                            'port':9443,'node_port':11443,'bind':'0.0.0.0','certificate_mode':'managed',
                            'origin':'https://changed.example.test:8443','ready':False,'future_field':'lost',
                            'bootstrap_python':before['bootstrap_python'],
                            'release_id':'unaccepted-release','ca_authorizations':['unexpected']}.items():
            with self.subTest(field=field):
                with self.assertRaises(AssertionError):
                    assert_upgrade_configuration(self,before,{**after,field:value},old_release,new_release)
        missing=deepcopy(after);del missing['certificate_mode']
        with self.assertRaises(AssertionError):
            assert_upgrade_configuration(self,before,missing,old_release,new_release)
        with self.assertRaises(AssertionError):
            assert_upgrade_configuration(self,{**before,'bootstrap_python':'/usr/bin/python3'},after,old_release,new_release)

    def test_existing_ca_authorizations_are_preserved_exactly(self):
        old_release=Path('/var/lib/v-ui/releases/old-014')
        new_release=Path('/var/lib/v-ui/releases/candidate-015')
        before={'release_id':old_release.name,
                'bootstrap_python':str(old_release/'runtime/python/bin/python3'),
                'ca_authorizations':[{'domain':'vpn.example.test','environment':'production'}]}
        after={**before,'release_id':new_release.name,
               'bootstrap_python':str(new_release/'runtime/python/bin/python3')}
        assert_upgrade_configuration(self,before,after,old_release,new_release)
        for authorizations in ([],None,[{'domain':'other.example.test','environment':'production'}]):
            with self.subTest(authorizations=authorizations):
                with self.assertRaises(AssertionError):
                    assert_upgrade_configuration(self,before,{**after,'ca_authorizations':authorizations},old_release,new_release)

    def test_complete_candidate_downloads_match_accepted_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);kit=root/'kit';kit.mkdir();log=root/'downloads.jsonl'
            for name in ('SHA256SUMS','RELEASE.json','vui-linux-x86_64-gnu.zip'):
                (kit/name).write_bytes(name.encode())
            rows=[{'name':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in kit.iterdir()]
            log.write_text('\n'.join(json.dumps(row) for row in rows))
            assert_download_boundary(self,kit,log)
            for changed in (rows[:-1], rows+[rows[0]],
                            [{**rows[0],'name':'vui-linux-aarch64-gnu.zip'},*rows[1:]],
                            [{**rows[0],'sha256':'0'*64},*rows[1:]]):
                with self.subTest(changed=changed):
                    log.write_text('\n'.join(json.dumps(row) for row in changed))
                    with self.assertRaises(AssertionError):assert_download_boundary(self,kit,log)

    def test_receipt_requires_exact_historical_closure_migration(self):
        before={'schema':1,'service_manager':'systemd','system_python':'/usr/bin/python3',
                'interpreter_resolved':'/usr/bin/python3.12','interpreter_sha256':'1'*64,
                'files':{'/usr/local/lib/v-ui/system_ops.py':'2'*64}}
        after=deepcopy(before);after['files'][FIREWALL]='3'*64
        after['files'].update({name:'6'*64 for name in MAINTENANCE_FILES})
        assert_receipt_migration(self,before,after,'0.4.14')
        variants=[]
        missing=deepcopy(after);del missing['files']['/usr/local/lib/v-ui/system_ops.py'];variants.append(missing)
        extra=deepcopy(after);extra['files']['/unrelated']='4'*64;variants.append(extra)
        drift=deepcopy(after);drift['interpreter_sha256']='0'*64;variants.append(drift)
        for name in MAINTENANCE_FILES:
            missing=deepcopy(after);del missing['files'][name];variants.append(missing)
        for changed in variants:
            with self.subTest(changed=changed):
                with self.assertRaises(AssertionError):assert_receipt_migration(self,before,changed,'0.4.14')
        with self.assertRaises(AssertionError):assert_receipt_migration(self,after,after,'0.4.14')


    def test_published22_requires_the_same_complete_response_as21(self):
        before = {'id': 1, 'mutation_revision': 'a' * 64, 'content_revision': 'b' * 64,
                  'share_eligible': True, 'upstream_export_eligible': True, 'remark': 'retained'}
        assert_node_summary_upgrade(self, [before], [dict(before)], '0.4.22')
        for key in before:
            changed = dict(before); changed[key] = 'changed'
            with self.subTest(key=key), self.assertRaises(AssertionError):
                assert_node_summary_upgrade(self, [before], [changed], '0.4.22')
        with self.assertRaises(AssertionError):
            assert_node_summary_upgrade(self, [before], [{**before, 'unknown': True}], '0.4.22')
        with self.assertRaises(AssertionError): fixed_public_baseline('0.4.22', 'f' * 40)

    def test_public23_uses_immutable_source_and_exact_24_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'; source.mkdir()
            (source / 'VERSION').write_text('0.4.24')
            kit = root / 'kit'; kit.mkdir()
            candidate = {'version':'0.4.24', 'source_commit':'a'*40}
            (kit / 'RELEASE.json').write_text(json.dumps(candidate))
            public_source = root / 'public23'
            env = {'VUI_PUBLISHED_INSTALL_VERSION':'0.4.23',
                   'VUI_RELEASE_COMMIT':PUBLISHED_BASELINES['0.4.23'],
                   'VUI_PUBLISHED_UPGRADE_SOURCE':str(public_source), 'VUI_RELEASE_KIT':str(kit)}
            with patch.dict('os.environ', env, clear=True), \
                    patch('published_upgrade_helpers.subprocess.check_output', return_value='a'*40+'\n'), \
                    patch('published_upgrade_helpers.published_commands') as public, \
                    patch('published_upgrade_helpers.fixture_commands') as fixture:
                upgrade = PublishedUpgrade(self, source, root, [], [])
                public.assert_called_once_with(public_source,'0.4.23',PUBLISHED_BASELINES['0.4.23'],[],[])
                fixture.assert_called_once_with(source,root,kit,[],[])
                self.assertEqual(upgrade.candidate,candidate)

    def test_candidate_origin_uses_exact_target_manifest_bytes_and_single_digest(self):
        with tempfile.TemporaryDirectory() as folder:
            kit = Path(folder)
            bundle = kit / 'vui-linux-x86_64-gnu.zip'
            release = dict(version='0.4.24', release_id='0.4.24-'+'a'*12, source_commit='a'*40)
            raw = json.dumps(release, indent=3).encode()
            with zipfile.ZipFile(bundle,'w') as archive:
                archive.writestr('MANIFEST.json',raw)
            digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
            line = digest+'  '+bundle.name+'\n'
            table = kit/'SHA256SUMS'; table.write_text(line)
            self.assertEqual(candidate_origin(kit, release, 'x86_64-gnu'),
                dict(release_id=release['release_id'],target='x86_64-gnu',app_source=release['source_commit'],
                     archive_sha256=digest,manifest_sha256=hashlib.sha256(raw).hexdigest()))
            with self.assertRaises(AssertionError):
                candidate_origin(kit, {**release,'source_commit':'b'*40}, 'x86_64-gnu')
            for invalid in ('',line+line,'wrong  '+bundle.name+'\n'):
                table.write_text(invalid)
                with self.assertRaises(AssertionError):
                    candidate_origin(kit, release, 'x86_64-gnu')

    def test_public22_to_same_candidate22_is_refused_before_public_downloads(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / 'source'; source.mkdir()
            (source / 'VERSION').write_text('0.4.22')
            kit = root / 'kit'; kit.mkdir()
            (kit / 'RELEASE.json').write_text(json.dumps({'version': '0.4.22', 'source_commit': 'a' * 40}))
            env = {'VUI_PUBLISHED_INSTALL_VERSION': '0.4.22',
                   'VUI_RELEASE_COMMIT': '558687a76cd29f4e4735284926eb05bf6664472e',
                   'VUI_RELEASE_KIT': str(kit)}
            with patch.dict('os.environ', env, clear=True), \
                    patch('published_upgrade_helpers.subprocess.check_output', return_value='a' * 40 + '\n'), \
                    patch('published_upgrade_helpers.published_commands') as public, \
                    patch('published_upgrade_helpers.fixture_commands') as fixture, self.assertRaises(AssertionError):
                PublishedUpgrade(self, source, root, [], [])
            public.assert_not_called(); fixture.assert_not_called()

    def test_only_immutable_public_014_through_017_and_019_sources_are_accepted(self):
        expected={'0.4.14':'b0cc174d04d05e61277075412586f0ba0dd231db',
                  '0.4.15':'65b1e75d90de733408101ec280a92be935731baa',
                  '0.4.16':'18fe631bcf69b68425b454db1795a101e0378842',
                  '0.4.17':'95c201c6e26ed54960f964c675e515890f2eea2a',
                  '0.4.19':'a2b9c188658f082b189e77d237787a96eb9cf921',
                  '0.4.20':'05173798de1baed5d67a866c4125e72f74fe3f4c',
                  '0.4.21':'2aa5e11e3f09c03d048018f033ae888e7c775604',
                  '0.4.22':'558687a76cd29f4e4735284926eb05bf6664472e',
                  '0.4.23':'be5d8459cdd3eeedce73f2ff6e4663e5eb781e42',
                  '0.4.24':'8b8d7cf82e55609a205455b351f659eb9de91974',
                  '0.4.25':'32a03a5b6719400f21a07a75d04d278b76e0df09'}
        self.assertEqual(PUBLISHED_BASELINES,expected)
        for version,commit in expected.items():
            self.assertEqual(fixed_public_baseline(version,commit),(version,commit))
        for version,commit in [('0.4.23',expected['0.4.22']),('0.4.24',expected['0.4.23']),('0.4.25',expected['0.4.24']),('0.4.21','f'*40),('0.4.20','f'*40),('0.4.13','a'*40),('0.4.18',expected['0.4.19']),('0.4.19','f'*40),('0.4.17',expected['0.4.16']),
                               ('0.4.16',expected['0.4.15']),('0.4.16','a'*40),
                               ('0.4.14',expected['0.4.15']),('0.4.15',expected['0.4.14']),
                               ('0.4.15','a'*40),(None,None)]:
            with self.subTest(version=version,commit=commit):
                with self.assertRaises(AssertionError):fixed_public_baseline(version,commit)
                with patch.dict('os.environ',{'VUI_PUBLISHED_INSTALL_VERSION':version or '',
                                              'VUI_RELEASE_COMMIT':commit or ''},clear=True), \
                     patch('published_upgrade_helpers.published_commands') as public, \
                     patch('published_upgrade_helpers.fixture_commands') as fixture:
                    with self.assertRaises(AssertionError):
                        PublishedUpgrade(self,Path('/unused'),Path('/unused'),[],[])
                    public.assert_not_called();fixture.assert_not_called()

    def test_receipt_015_and_016_preserve_the_complete_existing_closure(self):
        before={'schema':1,'service_manager':'systemd','system_python':'/usr/bin/python3',
                'interpreter_resolved':'/usr/bin/python3.12','interpreter_sha256':'1'*64,
                'files':{'/usr/local/lib/v-ui/system_ops.py':'2'*64,FIREWALL:'3'*64}}
        after=deepcopy(before)
        after['files'].update({name:'6'*64 for name in MAINTENANCE_FILES})
        # A new candidate may replace verified helper bytes; the exact file set
        # plus the exact new maintenance closure and interpreter identity must
        # survive; each actual helper hash is checked on-host.
        after['files'][FIREWALL]='4'*64
        for version in ('0.4.15','0.4.16'):
            assert_receipt_migration(self,before,after,version)
            variants=[]
            for name in before['files']:
                missing=deepcopy(after);del missing['files'][name];variants.append(missing)
            for name in MAINTENANCE_FILES:
                missing=deepcopy(after);del missing['files'][name];variants.append(missing)
            extra=deepcopy(after);extra['files']['/unrelated']='5'*64;variants.append(extra)
            for field in ('schema','service_manager','system_python','interpreter_resolved','interpreter_sha256'):
                drift=deepcopy(after);drift[field]='changed';variants.append(drift)
            for changed in variants:
                with self.subTest(changed=changed):
                    with self.assertRaises(AssertionError):
                        assert_receipt_migration(self,before,changed,version)
            legacy=deepcopy(before);del legacy['files'][FIREWALL]
            with self.assertRaises(AssertionError):assert_receipt_migration(self,legacy,after,version)
        assert_receipt_migration(self,after,after,'0.4.22')
        with self.assertRaises(AssertionError):assert_receipt_migration(self,before,after,'0.4.22')
        assert_receipt_migration(self,after,after,'0.4.21')
        with self.assertRaises(AssertionError):assert_receipt_migration(self,before,after,'0.4.21')
        assert_receipt_migration(self,after,after,'0.4.20')
        with self.assertRaises(AssertionError):assert_receipt_migration(self,before,after,'0.4.20')
        assert_receipt_migration(self,after,after,'0.4.19')
        with self.assertRaises(AssertionError):assert_receipt_migration(self,before,after,'0.4.19')
        assert_receipt_migration(self,after,after,'0.4.17')
        with self.assertRaises(AssertionError):assert_receipt_migration(self,before,after,'0.4.17')
        missing=deepcopy(after);missing['files'].pop(next(iter(MAINTENANCE_FILES)))
        with self.assertRaises(AssertionError):assert_receipt_migration(self,missing,after,'0.4.17')
        with self.assertRaises(AssertionError):assert_receipt_migration(self,after,missing,'0.4.17')
        extra=deepcopy(after);extra['files']['/unrelated.py']='f'*64
        with self.assertRaises(AssertionError):assert_receipt_migration(self,after,extra,'0.4.17')

    def test_managed_xray_fixture_keeps_local_material_and_pending_binding_without_apply(self):
        # Execute only the pure fixture construction against in-memory models.
        # No app, CA, business core, systemd, network or installed release runs.
        rows=[]
        class Record:
            def __init__(self,**values):self.__dict__.update(values)
        class Session:
            def __enter__(self):return self
            def __exit__(self,*unused):pass
            def add(self,row):rows.append(row)
            def flush(self):
                for row in rows:
                    if hasattr(row,'core'):row.id=31
            def commit(self):pass
        models=ModuleType('app.models')
        models.database=SimpleNamespace(SessionLocal=Session,Inbound=Record)
        certificates=ModuleType('app.certificates.models')
        certificates.Certificate=Record;certificates.CertificateBinding=Record
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'certs').mkdir()
            chain=b'synthetic-local-certificate';key=b'synthetic-local-private-key'
            (root/'certs/provided-fullchain.pem').write_bytes(chain)
            (root/'certs/provided-privkey.pem').write_bytes(key)
            output=io.StringIO()
            with patch.dict('sys.modules',{'app.models':models,'app.certificates.models':certificates}), redirect_stdout(output):
                exec(compile(managed_xray_fixture_program(root),'<managed-upgrade-fixture>','exec'),{})
            self.assertEqual(json.loads(output.getvalue()),{'inbound_id':31,'certificate_id':'b'*32})
            certificate,node,binding=rows
            revision=hashlib.sha256(chain).hexdigest()
            self.assertEqual(certificate.revision,revision)
            self.assertFalse(certificate.auto_renew)
            self.assertEqual(node.core,'xray');self.assertEqual(node.protocol,'vless')
            self.assertFalse(node.enable)
            self.assertEqual(binding.target,'inbound:31')
            self.assertEqual(binding.certificate_id,certificate.id)
            self.assertEqual(binding.error,'CORE_STOPPED_PENDING_APPLY')
            material=node.stream_settings['tlsSettings']['certificates'][0]
            for field,expected in (('certificateFile',chain),('keyFile',key)):
                path=Path(material[field])
                self.assertEqual(path.parent,root/'certificates/revisions'/certificate.id/revision)
                self.assertEqual(path.read_bytes(),expected)
                self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_empty_existing_ca_authorizations_remain_present_and_empty(self):
        old_release=Path('/var/lib/v-ui/releases/old-015')
        new_release=Path('/var/lib/v-ui/releases/candidate-016')
        before={'release_id':old_release.name,
                'bootstrap_python':str(old_release/'runtime/python/bin/python3'),
                'ca_authorizations':[],'future_field':{'preserve':'exactly'}}
        after={**before,'release_id':new_release.name,
               'bootstrap_python':str(new_release/'runtime/python/bin/python3')}
        assert_upgrade_configuration(self,before,after,old_release,new_release)
        missing=deepcopy(after);del missing['ca_authorizations']
        for changed in (missing,{**after,'future_field':{}}):
            with self.assertRaises(AssertionError):
                assert_upgrade_configuration(self,before,changed,old_release,new_release)

class PublishedDNSMigrationTests(unittest.TestCase):
    def receipt(self, old_version):
        before={'schema':1,'service_manager':'systemd','system_python':'/usr/bin/python3',
                'interpreter_resolved':'/usr/bin/python3.12','interpreter_sha256':'1'*64,
                'files':{'/usr/local/lib/v-ui/system_ops.py':'2'*64}}
        if old_version!='0.4.14': before['files'][FIREWALL]='3'*64
        if old_version in ('0.4.17','0.4.19','0.4.20','0.4.21','0.4.22','0.4.23','0.4.24','0.4.25'):
            before['files'].update({name:'4'*64 for name in MAINTENANCE_FILES})
        if old_version in ('0.4.23', '0.4.24', '0.4.25'):
            before['files'].update({name:'5'*64 for name in DNS_FILES})
        if old_version in ('0.4.24', '0.4.25'):
            before['files'].update({name:'6'*64 for name in CORE_FILES | AUTHORITY_FILES})
        if old_version == '0.4.25':
            before['files'].update({name:'7'*64 for name in YAML_FILES})
        after=deepcopy(before)
        after['files'].update({name:'5'*64 for name in MAINTENANCE_FILES|DNS_FILES|{FIREWALL}})
        return before,after

    def test_23_requires_exact_dns_additions_for_every_immutable_old_baseline(self):
        for version in PRE_DNS_BASELINES:
            before,after=self.receipt(version)
            with self.subTest(version=version): assert_receipt_migration(self,before,after,version,'0.4.23')
            variants=[]
            for name in DNS_FILES:
                changed=deepcopy(after);changed['files'].pop(name);variants.append(changed)
            changed=deepcopy(after);changed['files']['/usr/local/lib/v-ui/unreviewed.py']='f'*64;variants.append(changed)
            for name in before['files']:
                changed=deepcopy(after);changed['files'].pop(name);variants.append(changed)
            for field in ('schema','service_manager','system_python','interpreter_resolved','interpreter_sha256'):
                changed=deepcopy(after);changed[field]='changed';variants.append(changed)
            for changed in variants:
                with self.subTest(version=version,after_fields=len(changed['files'])),self.assertRaises(AssertionError):
                    assert_receipt_migration(self,before,changed,version,'0.4.23')
            for names in [*( {name} for name in DNS_FILES),DNS_FILES]:
                contaminated=deepcopy(before);contaminated['files'].update({name:'5'*64 for name in names})
                with self.subTest(version=version,before_dns=len(names)),self.assertRaises(AssertionError):
                    assert_receipt_migration(self,contaminated,after,version,'0.4.23')
            with self.assertRaises(AssertionError): assert_receipt_migration(self,before,after,version,'0.4.22')
            legacy=deepcopy(after)
            for name in DNS_FILES: legacy['files'].pop(name)
            assert_receipt_migration(self,before,legacy,version,'0.4.22')
        for version in ('0.4.24','v0.4.23','0.4.23\n',None,True):
            before,after=self.receipt('0.4.22')
            with self.subTest(target=version),self.assertRaises(AssertionError):
                assert_receipt_migration(self,before,after,'0.4.22',version)

    def test_24_requires_complete_core_authority_and_preserves_every_historical_dns_profile(self):
        for version in (old for old in PUBLISHED_BASELINES if old not in ('0.4.24', '0.4.25')):
            with self.subTest(version=version):
                before, after = self.receipt(version)
                after['files'].update({path:'6'*64 for path in CORE_FILES | AUTHORITY_FILES})
                profiles = dict(core_control=True, core_authority=True)
                assert_receipt_migration(self,before,after,version,'0.4.24',**profiles)
                with self.assertRaises(AssertionError):
                    assert_receipt_migration(self,before,after,version,'0.4.24',core_control=True)
                for path in DNS_FILES | CORE_FILES | AUTHORITY_FILES:
                    missing = deepcopy(after); missing['files'].pop(path)
                    with self.assertRaises(AssertionError):
                        assert_receipt_migration(self,before,missing,version,'0.4.24',**profiles)
        before, after = self.receipt('0.4.23')
        after['files'].update({path:'6'*64 for path in CORE_FILES | AUTHORITY_FILES})
        for path in DNS_FILES:
            missing = deepcopy(before); missing['files'].pop(path)
            with self.assertRaises(AssertionError):
                assert_receipt_migration(self,missing,after,'0.4.23','0.4.24',**profiles)
        for path in CORE_FILES | AUTHORITY_FILES:
            contaminated = deepcopy(before); contaminated['files'][path]='6'*64
            with self.assertRaises(AssertionError):
                assert_receipt_migration(self,contaminated,after,'0.4.23','0.4.24',**profiles)
        with self.assertRaises(AssertionError):
            assert_receipt_migration(self,before,after,'0.4.23','0.4.23',**profiles)

    def test_25_adds_only_yaml_reader_to_complete_public_24_receipt(self):
        before, after = self.receipt('0.4.24')
        after['files'].update({path: '7'*64 for path in YAML_FILES})
        profile = dict(core_control=True, core_authority=True, yaml_control=True)
        assert_receipt_migration(self, before, after, '0.4.24', '0.4.25', **profile)
        for group in (DNS_FILES, CORE_FILES, AUTHORITY_FILES):
            bad = deepcopy(before)
            bad['files'].pop(next(iter(group)))
            with self.assertRaises(AssertionError):
                assert_receipt_migration(self, bad, after, '0.4.24', '0.4.25', **profile)
        with self.assertRaises(AssertionError):
            assert_receipt_migration(self, before, after, '0.4.24', '0.4.24', **profile)
        with self.assertRaises(AssertionError):
            assert_receipt_migration(self, before, before, '0.4.24', '0.4.25', **profile)

    def test_26_requires_the_complete_existing_25_yaml_control_closure(self):
        profile = dict(core_control=True, core_authority=True, yaml_control=True)
        for old in PUBLISHED_BASELINES:
            with self.subTest(old=old):
                before, after = self.receipt(old)
                after['files'].update({path:'7'*64 for path in CORE_FILES | AUTHORITY_FILES | YAML_FILES})
                assert_receipt_migration(self, before, after, old, '0.4.26', **profile)
        before, after = self.receipt('0.4.25')
        for change in (lambda row: row['files'].pop(next(iter(YAML_FILES))),
                       lambda row: row['files'].update({'/usr/local/lib/v-ui/unknown.py':'8'*64})):
            damaged = deepcopy(after)
            change(damaged)
            with self.assertRaises(AssertionError):
                assert_receipt_migration(self, before, damaged, '0.4.25', '0.4.26', **profile)
        with self.assertRaises(AssertionError):
            assert_receipt_migration(self, before, after, '0.4.25', '0.4.25', **profile)

    def test_saved_yaml_fixture_reads_the_exact_plan_and_three_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            def witness(create):
                out = io.StringIO()
                with redirect_stdout(out):
                    exec(yaml_draft_program(create=create, data_root=root), {})
                return json.loads(out.getvalue())
            before = witness(True)
            self.assertEqual(witness(False), before)
            self.assertEqual(set(before['sources']), {'baseline', 'candidate', 'merged'})
            self.assertEqual(len(set(before['sources'].values())), 3)
            self.assertRegex(before['plan'], r'^[a-f0-9]{64}$')

    def test_p3_profile_requires_all_and_only_the_fixed_core_receipt_additions(self):
        from published_upgrade_helpers import CORE_FILES
        for version in PRE_DNS_BASELINES:
            before,after=self.receipt(version)
            after['files'].update({path:'6'*64 for path in CORE_FILES})
            assert_receipt_migration(self,before,after,version,'0.4.23',core_control=True)
            with self.assertRaises(AssertionError):
                assert_receipt_migration(self,before,after,version,'0.4.23')
            for name in CORE_FILES:
                missing=deepcopy(after);missing['files'].pop(name)
                with self.subTest(version=version,missing=name),self.assertRaises(AssertionError):
                    assert_receipt_migration(self,before,missing,version,'0.4.23',core_control=True)
            contaminated=deepcopy(before);contaminated['files'][next(iter(CORE_FILES))]='6'*64
            with self.assertRaises(AssertionError):
                assert_receipt_migration(self,contaminated,after,version,'0.4.23',core_control=True)
        tree=ast.parse((Path(__file__).resolve().parents[1]/'scripts/install_ops_control.py').read_text())
        literal=next(node.value for node in tree.body if isinstance(node,ast.Assign)
            and any(isinstance(target,ast.Name) and target.id=='CORE_SOURCE_DEPENDENCIES' for target in node.targets))
        self.assertEqual(CORE_FILES,{'/usr/local/lib/v-ui/'+name for name in ast.literal_eval(literal)})

    def test_yaml_profile_requires_its_exact_additional_reader(self):
        from published_upgrade_helpers import CORE_FILES, YAML_FILES
        for version in (v for v in PUBLISHED_BASELINES if v not in ('0.4.23', '0.4.24', '0.4.25')):
            before, after = self.receipt(version)
            after['files'].update({path: '6'*64 for path in CORE_FILES | YAML_FILES})
            assert_receipt_migration(self, before, after, version, '0.4.23',
                                     core_control=True, yaml_control=True)
            with self.assertRaises(AssertionError):
                assert_receipt_migration(self, before, after, version, '0.4.23', core_control=True)
            missing = deepcopy(after)
            missing['files'].pop(next(iter(YAML_FILES)))
            with self.assertRaises(AssertionError):
                assert_receipt_migration(self, before, missing, version, '0.4.23',
                                         core_control=True, yaml_control=True)
        tree = ast.parse((Path(__file__).resolve().parents[1]/'scripts/install_ops_control.py').read_text())
        literal = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == 'YAML_SOURCE_DEPENDENCIES'
                               for target in node.targets))
        self.assertEqual(YAML_FILES, {'/usr/local/lib/v-ui/'+name for name in ast.literal_eval(literal)})

    def test_literal_dns_closure_and_snapshot_columns_match_reviewed_production_constants(self):
        source=Path(__file__).resolve().parents[1]
        tree=ast.parse((source/'scripts/install_ops_control.py').read_text())
        dependency=next(node.value for node in tree.body if isinstance(node,ast.Assign)
            and any(isinstance(t,ast.Name) and t.id=='DNS_SOURCE_DEPENDENCIES' for t in node.targets))
        self.assertEqual(DNS_FILES,{'/usr/local/lib/v-ui/'+name for name in ast.literal_eval(dependency)})
        names={'OLD_CERTIFICATE_COLUMNS','NEW_CERTIFICATE_COLUMNS','OLD_BINDING_COLUMNS','NEW_BINDING_COLUMNS'}
        tree=ast.parse((source/'app/certificates/schema.py').read_text())
        constants=[node for node in tree.body if isinstance(node,ast.Assign)
            and any(isinstance(t,ast.Name) and t.id in names for t in node.targets)]
        namespace={'frozenset':frozenset}
        exec(compile(ast.Module(body=constants,type_ignores=[]),'<literal production schema sets>','exec'),namespace)
        self.assertEqual(set(CERTIFICATE_COLUMNS),namespace['OLD_CERTIFICATE_COLUMNS'])
        self.assertEqual(set(DNS_CERTIFICATE_COLUMNS),namespace['NEW_CERTIFICATE_COLUMNS'])
        self.assertEqual(set(BINDING_COLUMNS),namespace['OLD_BINDING_COLUMNS'])
        self.assertTrue(all(set(layout)==namespace['NEW_BINDING_COLUMNS'] for layout in DNS_BINDING_LAYOUTS))
        models=ast.parse((source/'app/certificates/models.py').read_text())
        def column_names(class_name):
            model=next(node for node in models.body if isinstance(node,ast.ClassDef) and node.name==class_name)
            return tuple(node.targets[0].id for node in model.body if isinstance(node,ast.Assign)
                and isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Name) and node.value.func.id=='Column')
        self.assertEqual(column_names('Certificate'),DNS_CERTIFICATE_COLUMNS)
        self.assertIn(column_names('CertificateBinding'),DNS_BINDING_LAYOUTS)

    @contextmanager
    def data(self,modern=False,fresh_binding=False,existing_dns=False):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            old_values={'id':'a'*32,'domain':'vpn.example.test','email':'fixture@example.test',
                'environment':'production','auto_renew':0,'revision':'b'*64,'not_before':123,'not_after':456,
                'renew_at':234,'retry_at':0,'last_attempt':111,'failures':2,'error':'kept-error','created_at':100}
            binding={'target':'inbound:1','certificate_id':'a'*32,'applied_certificate_id':'a'*32,
                'applied_revision':'b'*64,'error':'CORE_STOPPED_PENDING_APPLY'}
            with sqlite3.connect(root/'v-ui.db') as db:
                for sql in ('CREATE TABLE users(id INTEGER,username TEXT,password_hash TEXT)',
                            'CREATE TABLE inbounds(id INTEGER,remark TEXT,settings TEXT)',
                            'CREATE TABLE subscription_grants(id INTEGER,token_hash TEXT,revoked INTEGER)'):
                    db.execute(sql)
                db.execute("INSERT INTO users VALUES(1,'fixture','fake-password-hash')")
                db.execute("INSERT INTO inbounds VALUES(1,'retained','fake-settings')")
                db.execute("INSERT INTO subscription_grants VALUES(1,'fake-token-hash',0)")
                columns=DNS_CERTIFICATE_COLUMNS if modern else CERTIFICATE_COLUMNS
                definitions=[]
                for name in columns:
                    if name=='method': definition="method VARCHAR(16) NOT NULL DEFAULT 'http-01'"
                    elif name=='wildcard': definition='wildcard BOOLEAN NOT NULL DEFAULT 0'
                    elif name=='credential_id': definition='credential_id VARCHAR(32)'
                    else: definition='"'+name+'" '+('INTEGER' if type(old_values[name]) is int else 'TEXT')
                    definitions.append(definition)
                db.execute('CREATE TABLE managed_certificates('+','.join(definitions)+')')
                db.execute('INSERT INTO managed_certificates('+','.join(CERTIFICATE_COLUMNS)+') VALUES('+','.join('?' for _ in CERTIFICATE_COLUMNS)+')',tuple(old_values[name] for name in CERTIFICATE_COLUMNS))
                columns=DNS_BINDING_LAYOUTS[1 if fresh_binding else 0] if modern else BINDING_COLUMNS
                db.execute('CREATE TABLE certificate_bindings('+','.join('server_name VARCHAR(253)' if name=='server_name' else name+' TEXT' for name in columns)+')')
                db.execute('INSERT INTO certificate_bindings('+','.join(BINDING_COLUMNS)+') VALUES('+','.join('?' for _ in BINDING_COLUMNS)+')',tuple(binding[name] for name in BINDING_COLUMNS))
                if modern:
                    db.execute('CREATE TABLE certificate_identity_schema(version INTEGER NOT NULL)')
                    db.execute('INSERT INTO certificate_identity_schema VALUES(1)')
                if existing_dns:
                    db.execute("UPDATE managed_certificates SET method='dns-01',wildcard=1,credential_id=?",('d'*32,))
                    db.execute("UPDATE certificate_bindings SET server_name='fixture.example.test'")
            if existing_dns:
                profile=root/'certificates/dns-credentials'/('d'*32)
                profile.mkdir(parents=True,mode=0o700)
                profile.parent.chmod(0o700)
                for name,contents in (('state.json',b'fake-dns-profile'),('e'*32+'.json',b'fake-dns-credential')):
                    (profile/name).write_bytes(contents)
                    (profile/name).chmod(0o600)
            for directory in ('certs','certificates/revisions/'+'a'*32+'/'+'b'*64):
                folder=root/directory;folder.mkdir(parents=True)
                (folder/'fullchain.pem').write_bytes(b'fake-chain-exact')
                (folder/'privkey.pem').write_bytes(b'fake-private-key-exact')
            yield root

    def snapshot(self,root,*,after=False,target='0.4.23',old='0.4.22'):
        output=io.StringIO()
        with redirect_stdout(output):
            exec(compile(snapshot_program(old,target,after=after,data_root=root),'<readonly service snapshot>','exec'),{})
        text=output.getvalue()
        self.assertNotIn('fake-private-key',text);self.assertNotIn('fake-password-hash',text)
        self.assertNotIn('fake-token-hash',text);self.assertNotIn('kept-error',text)
        self.assertNotIn('fake-dns-credential',text);self.assertNotIn('fake-dns-profile',text)
        return json.loads(text)

    def test_every_legacy_value_and_material_hash_matches_both_known_23_binding_layouts(self):
        with self.data() as old:
            before=self.snapshot(old)
            self.assertEqual(self.snapshot(old,after=True,target='0.4.22'),before)
            for fresh in (False,True):
                with self.subTest(fresh=fresh),self.data(modern=True,fresh_binding=fresh) as new:
                    self.assertEqual(self.snapshot(new,after=True),before)
                    with self.assertRaises(AssertionError):self.snapshot(new)
                    with self.assertRaises(AssertionError):self.snapshot(new,after=True,target='0.4.22')
            with self.assertRaises(AssertionError):self.snapshot(old,after=True)
        for target in ('0.4.27','v0.4.23',None):
            with self.assertRaises(AssertionError):snapshot_program('0.4.22',target)
        with self.assertRaises(AssertionError):snapshot_program('0.4.23','0.4.23')

    def test_23_to_24_keeps_dns_identity_binding_and_private_material_bytes(self):
        def snapshot(root, after=False):
            return self.snapshot(root,after=after,target='0.4.24',old='0.4.23')
        with self.data(modern=True,existing_dns=True) as original:
            before = snapshot(original)
            self.assertEqual(before['managed_certificates']['columns'],list(DNS_CERTIFICATE_COLUMNS))
            self.assertIn('server_name',before['certificate_bindings']['columns'])
            self.assertEqual(len(before['dns_credentials']),2)
            self.assertEqual(snapshot(original,after=True),before)
            with self.data(modern=True,fresh_binding=True,existing_dns=True) as changed:
                self.assertEqual(snapshot(changed,after=True),before)
                with sqlite3.connect(changed/'v-ui.db') as db:
                    db.execute("UPDATE managed_certificates SET method='http-01',wildcard=0,credential_id=NULL")
                    db.execute("UPDATE certificate_bindings SET server_name=NULL")
                after = snapshot(changed,after=True)
                self.assertNotEqual(after['managed_certificates'],before['managed_certificates'])
                self.assertNotEqual(after['certificate_bindings'],before['certificate_bindings'])
                secret=next((changed/'certificates/dns-credentials').glob('*/*[0-9a-f].json'))
                secret.write_bytes(b'inert changed material')
                self.assertNotEqual(snapshot(changed,after=True)['dns_credentials'],before['dns_credentials'])
                secret.chmod(0o644)
                with self.assertRaises(AssertionError):
                    snapshot(changed,after=True)
        with self.data(modern=False) as legacy:
            with self.assertRaises(AssertionError):
                snapshot(legacy)

    def test_each_old_certificate_binding_and_other_data_field_remains_exact(self):
        with self.data() as old:
            before=self.snapshot(old)
            for table,columns in (('managed_certificates',CERTIFICATE_COLUMNS),('certificate_bindings',BINDING_COLUMNS),
                    ('users',('id','username','password_hash')),('inbounds',('id','remark','settings')),
                    ('subscription_grants',('id','token_hash','revoked'))):
                for column in columns:
                    with self.subTest(table=table,column=column),self.data(modern=True) as new:
                        with sqlite3.connect(new/'v-ui.db') as db:db.execute('UPDATE '+table+' SET "'+column+'"=?',('changed',))
                        with self.assertRaises(AssertionError):self.assertEqual(self.snapshot(new,after=True),before)
            for table in ('managed_certificates','certificate_bindings','users','inbounds','subscription_grants'):
                with self.subTest(deleted=table),self.data(modern=True) as new:
                    with sqlite3.connect(new/'v-ui.db') as db:db.execute('DELETE FROM '+table)
                    with self.assertRaises(AssertionError):self.assertEqual(self.snapshot(new,after=True),before)
            for directory in ('certs','certificates/revisions/'+'a'*32+'/'+'b'*64):
                for name in ('fullchain.pem','privkey.pem'):
                    with self.subTest(material=directory+'/'+name),self.data(modern=True) as new:
                        (new/directory/name).write_bytes(b'changed-fake-material')
                        with self.assertRaises(AssertionError):self.assertEqual(self.snapshot(new,after=True),before)

    def test_new_identity_defaults_and_unknown_certificate_columns_are_refused(self):
        for table,column,value in (('managed_certificates','method','dns-01'),('managed_certificates','wildcard',1),
                ('managed_certificates','wildcard',0.5),('managed_certificates','credential_id','c'*32),
                ('certificate_bindings','server_name','vpn.example.test')):
            with self.subTest(table=table,column=column,value=value),self.data(modern=True) as root:
                with sqlite3.connect(root/'v-ui.db') as db:db.execute('UPDATE '+table+' SET '+column+'=?',(value,))
                with self.assertRaises(AssertionError):self.snapshot(root,after=True)
        for modern in (False,True):
            for table in ('managed_certificates','certificate_bindings'):
                with self.subTest(modern=modern,extra=table),self.data(modern=modern) as root:
                    with sqlite3.connect(root/'v-ui.db') as db:db.execute('ALTER TABLE '+table+' ADD COLUMN unknown TEXT')
                    with self.assertRaises(AssertionError):self.snapshot(root,after=modern)

    def test_marker_missing_wrong_duplicate_or_unknown_columns_never_qualifies(self):
        for sql in ('DROP TABLE certificate_identity_schema','DELETE FROM certificate_identity_schema',
                'UPDATE certificate_identity_schema SET version=2','INSERT INTO certificate_identity_schema VALUES(1)',
                'ALTER TABLE certificate_identity_schema ADD COLUMN unknown TEXT'):
            with self.subTest(sql=sql),self.data(modern=True) as root:
                with sqlite3.connect(root/'v-ui.db') as db:db.execute(sql)
                with self.assertRaises(AssertionError):self.snapshot(root,after=True)
        with self.data() as root:
            with sqlite3.connect(root/'v-ui.db') as db:db.executescript('CREATE TABLE certificate_identity_schema(version INTEGER NOT NULL);INSERT INTO certificate_identity_schema VALUES(1)')
            with self.assertRaises(AssertionError):self.snapshot(root)

    def test_wrong_declared_defaults_or_types_fail_even_with_correct_existing_row_values(self):
        for table,old,new in (
                ('managed_certificates', "DEFAULT 'http-01'", "DEFAULT 'dns-01'"),
                ('managed_certificates', 'DEFAULT 0', 'DEFAULT 1'),
                ('managed_certificates', 'credential_id VARCHAR(32)', 'credential_id VARCHAR(64)'),
                ('certificate_bindings', 'server_name VARCHAR(253)', "server_name VARCHAR(253) DEFAULT 'vpn.example.test'")):
            with self.subTest(table=table,changed=new),self.data(modern=True) as root:
                with sqlite3.connect(root/'v-ui.db') as db:
                    sql=db.execute('SELECT sql FROM sqlite_master WHERE name=?',(table,)).fetchone()[0]
                    self.assertIn(old,sql)
                    db.execute('ALTER TABLE '+table+' RENAME TO retained_old')
                    db.execute(sql.replace(old,new))
                    db.execute('INSERT INTO '+table+' SELECT * FROM retained_old')
                    db.execute('DROP TABLE retained_old')
                with self.assertRaises(AssertionError):self.snapshot(root,after=True)

    def test_read_only_snapshot_keeps_database_bytes_and_rejects_added_data_columns(self):
        with self.data() as old,self.data(modern=True) as new:
            before=self.snapshot(old)
            original=(new/'v-ui.db').read_bytes()
            self.assertEqual(self.snapshot(new,after=True),before)
            self.assertEqual((new/'v-ui.db').read_bytes(),original)
            with sqlite3.connect(new/'v-ui.db') as db:db.execute('ALTER TABLE users ADD COLUMN unknown TEXT')
            with self.assertRaises(AssertionError):self.assertEqual(self.snapshot(new,after=True),before)


    def test_authority_profile_is_separate_from_frozen_core_and_public_profiles(self):
        from published_upgrade_helpers import CORE_FILES, AUTHORITY_FILES
        self.assertEqual(AUTHORITY_FILES, {'/usr/local/lib/v-ui/core_authority.py'})
        self.assertTrue(AUTHORITY_FILES.isdisjoint(CORE_FILES))
        for version in PRE_DNS_BASELINES:
            before, after = self.receipt(version)
            after['files'].update({path:'6'*64 for path in CORE_FILES | AUTHORITY_FILES})
            assert_receipt_migration(self,before,after,version,'0.4.23',core_control=True,core_authority=True)
            with self.assertRaises(AssertionError):
                assert_receipt_migration(self,before,after,version,'0.4.23',core_control=True)
            for path in AUTHORITY_FILES | CORE_FILES:
                missing=deepcopy(after);missing['files'].pop(path)
                with self.subTest(version=version,missing=path),self.assertRaises(AssertionError):
                    assert_receipt_migration(self,before,missing,version,'0.4.23',core_control=True,core_authority=True)
            polluted=deepcopy(before);polluted['files'][next(iter(AUTHORITY_FILES))]='7'*64
            with self.assertRaises(AssertionError):
                assert_receipt_migration(self,polluted,after,version,'0.4.23',core_control=True,core_authority=True)
            extra=deepcopy(after);extra['files']['/usr/local/lib/v-ui/foreign.py']='7'*64
            with self.assertRaises(AssertionError):
                assert_receipt_migration(self,before,extra,version,'0.4.23',core_control=True,core_authority=True)
        for controls in ({'core_control':False,'core_authority':True},
                         {'core_control':True,'core_authority':1}):
            with self.subTest(controls=controls),self.assertRaises(AssertionError):
                assert_receipt_migration(self,before,after,'0.4.22','0.4.23',**controls)
        tree=ast.parse((Path(__file__).resolve().parents[1]/'scripts/install_ops_control.py').read_text())
        literal=next(node.value for node in tree.body if isinstance(node,ast.Assign)
            and any(isinstance(target,ast.Name) and target.id=='CORE_AUTHORITY_SOURCE_DEPENDENCIES' for target in node.targets))
        self.assertEqual(AUTHORITY_FILES,{'/usr/local/lib/v-ui/'+name for name in ast.literal_eval(literal)})


if __name__=='__main__':unittest.main()
