"""Pure fixture checks; these do not qualify any installed upgrade."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from published_upgrade_helpers import (FIREWALL, assert_download_boundary,
                                       assert_receipt_migration, assert_upgrade_configuration)


class PublishedUpgradeBoundaryTests(unittest.TestCase):
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
        assert_receipt_migration(self,before,after)
        variants=[]
        missing=deepcopy(after);del missing['files']['/usr/local/lib/v-ui/system_ops.py'];variants.append(missing)
        extra=deepcopy(after);extra['files']['/unrelated']='4'*64;variants.append(extra)
        drift=deepcopy(after);drift['interpreter_sha256']='0'*64;variants.append(drift)
        for changed in variants:
            with self.subTest(changed=changed):
                with self.assertRaises(AssertionError):assert_receipt_migration(self,before,changed)
        with self.assertRaises(AssertionError):assert_receipt_migration(self,after,after)


if __name__=='__main__':unittest.main()
