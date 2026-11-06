"""Pure fixture checks; these do not qualify any installed upgrade."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from published_upgrade_helpers import (FIREWALL, MAINTENANCE_FILES, PUBLISHED_BASELINES, PublishedUpgrade,
                                       assert_download_boundary, assert_receipt_migration,
                                       assert_upgrade_configuration, fixed_public_baseline,
                                       managed_xray_fixture_program, assert_node_summary_upgrade)


class PublishedUpgradeBoundaryTests(unittest.TestCase):
    def test_summary_additions_are_only_strict_booleans_without_old_field_drift(self):
        before=[{'id':1,'core':'sing-box','port':10443,'remark':'preserve',
                 'enable':True,'routing_mode':'inherit','mutation_revision':'a'*64}]
        after=[{**before[0],'share_eligible':True,'upstream_export_eligible':True}]
        assert_node_summary_upgrade(self,before,after)
        for change in ({'share_eligible':1},{'upstream_export_eligible':None},
                       {'remark':'replaced'},{'routing_mode':'direct'},
                       {'mutation_revision':'b'*64},{'raw_settings':{'secret':'fake'}}):
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
        assert_node_summary_upgrade(self,before,deepcopy(before),'0.4.19')
        for change in ({'share_eligible':False},{'routing_mode':'direct'},{'mutation_revision':'b'*64}):
            with self.subTest(change=change),self.assertRaises(AssertionError):
                assert_node_summary_upgrade(self,before,[{**before[0],**change}],'0.4.19')

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


    def test_only_immutable_public_014_through_017_and_019_sources_are_accepted(self):
        expected={'0.4.14':'b0cc174d04d05e61277075412586f0ba0dd231db',
                  '0.4.15':'65b1e75d90de733408101ec280a92be935731baa',
                  '0.4.16':'18fe631bcf69b68425b454db1795a101e0378842',
                  '0.4.17':'95c201c6e26ed54960f964c675e515890f2eea2a',
                  '0.4.19':'a2b9c188658f082b189e77d237787a96eb9cf921'}
        self.assertEqual(PUBLISHED_BASELINES,expected)
        for version,commit in expected.items():
            self.assertEqual(fixed_public_baseline(version,commit),(version,commit))
        for version,commit in [('0.4.13','a'*40),('0.4.18',expected['0.4.19']),('0.4.19','f'*40),('0.4.17',expected['0.4.16']),
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

if __name__=='__main__':unittest.main()
