"""Pure fixture checks; these do not qualify any installed upgrade."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from published_upgrade_helpers import FIREWALL, assert_download_boundary, assert_receipt_migration


class PublishedUpgradeBoundaryTests(unittest.TestCase):
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
