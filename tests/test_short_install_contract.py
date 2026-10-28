"""No network or installation: bind a new bootstrap to an immutable release."""
import hashlib
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml

import online_install_helpers as helpers


class ShortInstallContractTests(unittest.TestCase):
    stable = 'a' * 40
    checkout = 'b' * 40

    def test_existing_browser_steps_remain_explicit_manual_opt_in(self):
        root=Path(__file__).resolve().parents[1]
        for name in ('test.yml','release.yml','acme.yml'):
            workflow=yaml.load((root/'.github/workflows'/name).read_text(),Loader=yaml.BaseLoader)
            self.assertEqual(workflow['on']['workflow_dispatch']['inputs']['run_browser']['default'],'false')
            browser_steps=[s for j in workflow['jobs'].values() for s in j['steps']
                           if 'browser' in s.get('name','').lower() or 'graphical certificate lifecycle' in s.get('name','').lower()]
            self.assertTrue(browser_steps)
            for step in browser_steps:
                self.assertEqual(step.get('if'),"github.event_name == 'workflow_dispatch' && inputs.run_browser == true",(name,step['name']))

    def test_configured_command_receives_controlling_terminal_and_retains_failure_code(self):
        program = "import os; f=open('/dev/tty','rb+',buffering=0); print('controlling-terminal='+str(os.isatty(f.fileno())),flush=True); raise SystemExit(7)"
        result=helpers.configured_setup_with_tty([sys.executable,'-c',program],timeout=5)
        self.assertEqual(result.returncode,7)
        self.assertIn('controlling-terminal=True',result.stdout)

    def fixture(self, folder, *, release_commit=None, raw=None):
        source = Path(folder)
        (source / 'VERSION').write_text('0.4.9')
        (source / 'install.sh').write_bytes(b'fixed published installer\n')
        (source / 'setup.sh').write_bytes(b'new repository bootstrap\n')
        files = {'install.sh':(source / 'install.sh').read_bytes(), 'SHA256SUMS':b'fixed complete checksum list\n',
                 'RELEASE.json':json.dumps({'version':'0.4.9','source_commit':release_commit or self.stable}).encode()}
        prefix = 'https://github.com/ForceMind/V-UI/releases/download/v0.4.9/'
        assets = [{'name':name, 'browser_download_url':prefix+name,
                   'digest':'sha256:'+hashlib.sha256(files.get(name,b'')).hexdigest()}
                  for name in helpers.ASSETS]
        release = {'tag_name':'v0.4.9','draft':False,'prerelease':False,'assets':assets,
                   'body':helpers.installation_command(files,'0.4.9')}
        mapping = {prefix+name:data for name,data in files.items()}
        mapping['https://api.github.com/repos/ForceMind/V-UI/releases/tags/v0.4.9'] = json.dumps(release).encode()
        mapping['https://raw.githubusercontent.com/ForceMind/V-UI/main/setup.sh'] = (source/'setup.sh').read_bytes() if raw is None else raw
        class Response:
            def __init__(self,url):self.url=url
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def geturl(self):return self.url
            def read(self,limit):return mapping[self.url][:limit]
        class Opener:
            def open(self,request,timeout):return Response(request.full_url)
        return source, Opener()

    def invoke(self, source):
        return helpers.setup_commands(source,'0.4.9',self.stable,self.checkout,'main',
                                      ['--cert','/fixture/cert with spaces.pem'],['--domain','literal.example.test'])

    def test_new_checkout_and_frozen_release_have_separate_identities(self):
        with tempfile.TemporaryDirectory() as folder:
            source,opener=self.fixture(folder)
            with patch.object(helpers,'build_opener',return_value=opener), \
                    patch.object(helpers.subprocess,'check_output',return_value=self.checkout+'\n'):
                result=self.invoke(source)
            self.assertEqual(result['canonical'],'bash <(curl -fsSL https://raw.githubusercontent.com/ForceMind/V-UI/main/setup.sh)')
            self.assertIn(shlex.quote('/fixture/cert with spaces.pem'),result['initial'][2])
            self.assertIn('--upgrade',result['upgrade'][2])
            self.assertIn('release_canonical',result)

    def test_main_advance_or_tamper_is_rejected_before_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            source,opener=self.fixture(folder,raw=b'different public bootstrap')
            with patch.object(helpers,'build_opener',return_value=opener), \
                    patch.object(helpers.subprocess,'check_output',return_value=self.checkout+'\n'):
                with self.assertRaisesRegex(AssertionError,'differs from exact checkout'):self.invoke(source)

    def test_new_checkout_does_not_relabel_the_frozen_release(self):
        with tempfile.TemporaryDirectory() as folder:
            source,opener=self.fixture(folder,release_commit=self.checkout)
            with patch.object(helpers,'build_opener',return_value=opener), \
                    patch.object(helpers.subprocess,'check_output',return_value=self.checkout+'\n'):
                with self.assertRaisesRegex(AssertionError,'Published source identity mismatch'):self.invoke(source)

    def test_wrong_checkout_is_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as folder:
            source,_=self.fixture(folder)
            with patch.object(helpers,'build_opener') as network, \
                    patch.object(helpers.subprocess,'check_output',return_value=self.stable+'\n'):
                with self.assertRaisesRegex(AssertionError,'checkout is not exact'):self.invoke(source)
                network.assert_not_called()

    def test_unapproved_branch_is_rejected_without_network(self):
        with patch.object(helpers,'build_opener') as network:
            with self.assertRaisesRegex(AssertionError,'main or an exact'):
                helpers.setup_commands(Path('/unused'),'0.4.9',self.stable,self.checkout,'dev',[],[])
            network.assert_not_called()

    def test_legacy_published_path_still_requires_its_own_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            source,_=self.fixture(folder)
            with patch.object(helpers.subprocess,'check_output',return_value=self.checkout+'\n'):
                with self.assertRaisesRegex(AssertionError,'checkout is not exact'):
                    helpers.published_commands(source,'0.4.9',self.stable,[],[])
