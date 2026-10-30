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

    def test_installer_language_identity_does_not_depend_on_consent_test_opt_in(self):
        from test_oneclick_system import installer_has_chinese_prompts
        self.assertFalse(installer_has_chinese_prompts('0.4.9'))
        self.assertTrue(installer_has_chinese_prompts('0.4.10'))
        self.assertTrue(installer_has_chinese_prompts('0.4.11'))
        root=Path(__file__).resolve().parents[1]
        for name in ('oneclick.yml','short-install.yml','online-release-acceptance.yml'):
            workflow=yaml.load((root/'.github/workflows'/name).read_text(),Loader=yaml.BaseLoader)
            envs=[step.get('env',{}) for job in workflow['jobs'].values() for step in job['steps']]
            selected=[env for env in envs if env.get('VUI_SYSTEM_INSTALL_TEST')=='1']
            self.assertEqual(len(selected),1)
            self.assertEqual(selected[0].get('VUI_INSTALLER_YN_TEST'),'1')

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

    def fake_configured_session(self, chunks, returncode):
        class Session:
            def __init__(self):
                self.chunks=list(chunks)
                self.output=''
                self.sent=[]
                self.sent_outputs=[]
                self.process=self
                self.returncode=None
            def __enter__(self):return self
            def __exit__(self,*unused):pass
            def poll(self):
                if not self.chunks:self.returncode=returncode
                return self.returncode
            def _read(self,timeout):
                if not self.chunks:return False
                self.output+=self.chunks.pop(0)
                return True
            def text(self):return self.output
            def send(self,value):
                self.sent.append(value)
                self.sent_outputs.append(self.output)
        return Session()

    def test_existing_menu_cancel_sends_only_zero_once_after_complete_prompt(self):
        session=self.fake_configured_session([
            'Panel domain: ', '请选择 [1/2/0，回车', '取消]: ',
            '请选择 [1/2/0，回车取消]: ', '已取消，原配置和数据未更改'],17)
        with patch.object(helpers,'MenuSession',return_value=session):
            result=helpers.configured_setup_with_tty(['synthetic-menu'],timeout=5,cancel_existing=True)
        self.assertEqual(session.sent,['0\n'])
        self.assertEqual(session.sent_outputs,['Panel domain: 请选择 [1/2/0，回车取消]: '])
        self.assertEqual(result.returncode,17)
        self.assertIn('已取消，原配置和数据未更改',result.stdout)

    def test_existing_menu_cancel_requires_exact_prompt_and_never_answers_other_prompts(self):
        session=self.fake_configured_session([
            'Panel domain: ', '请选择 [1/2/0，回车取消]:', '已取消，原配置和数据未更改'],7)
        with patch.object(helpers,'MenuSession',return_value=session):
            with self.assertRaisesRegex(AssertionError,'menu prompt was not observed'):
                helpers.configured_setup_with_tty(['synthetic-menu'],timeout=5,cancel_existing=True)
        self.assertEqual(session.sent,[])
        self.assertEqual(session.returncode,7)

    def test_configured_setup_default_never_injects_menu_input(self):
        session=self.fake_configured_session(['请选择 [1/2/0，回车取消]: ','original failure'],9)
        with patch.object(helpers,'MenuSession',return_value=session):
            result=helpers.configured_setup_with_tty(['synthetic-menu'],timeout=5)
        self.assertEqual(session.sent,[])
        self.assertEqual(result.returncode,9)
        self.assertIn('original failure',result.stdout)

    def test_bootstrap_cancel_and_online_choices_require_complete_distinct_prompt(self):
        for action,cancel,expected in ((None,True,'0'),('2',False,'2'),('3',False,'3')):
            with self.subTest(action=action):
                session=self.fake_configured_session([
                    '请选择 [1/2/3/0，回车', '退出]: ', 'done'],0)
                with patch.object(helpers,'MenuSession',return_value=session):
                    result=helpers.configured_setup_with_tty(['synthetic'],timeout=5,
                                cancel_existing=cancel,bootstrap_action=action)
                self.assertEqual(result.returncode,0)
                self.assertEqual(session.sent,[expected+'\n'])
                self.assertEqual(session.sent_outputs,['请选择 [1/2/3/0，回车退出]: '])

    def test_bootstrap_repair_then_inner_cancel_are_separate_choices(self):
        session=self.fake_configured_session(['请选择 [1/2/3/0，回车退出]: ',
            '请选择 [1/2/0，回车取消]: ', 'cancelled'],17)
        with patch.object(helpers,'MenuSession',return_value=session):
            result=helpers.configured_setup_with_tty(['synthetic'],timeout=5,
                                                    cancel_existing=True,bootstrap_action='3')
        self.assertEqual(session.sent,['3\n','0\n'])
        self.assertEqual(result.returncode,17)

    def test_bootstrap_choice_refuses_missing_or_incomplete_prompt(self):
        session=self.fake_configured_session(['请选择 [1/2/3/0，回车退出]:'],0)
        with patch.object(helpers,'MenuSession',return_value=session):
            with self.assertRaisesRegex(AssertionError,'Bootstrap menu prompt'):
                helpers.configured_setup_with_tty(['synthetic'],timeout=5,bootstrap_action='2')
        self.assertEqual(session.sent,[])

    def test_workflow_checks_out_frozen_release_source_separately(self):
        root=Path(__file__).resolve().parents[1]
        workflow=yaml.load((root/'.github/workflows/short-install.yml').read_text(),Loader=yaml.BaseLoader)
        steps=workflow['jobs']['short-install']['steps']
        checkouts=[step for step in steps if step.get('uses','').startswith('actions/checkout@')]
        self.assertEqual(len(checkouts),2)
        self.assertEqual(checkouts[0]['with']['persist-credentials'],'false')
        self.assertEqual(checkouts[1]['with'],{'persist-credentials':'false',
                                             'ref':'265e47d76ef5166c0399f916e8cf780d6c6ae22d',
                                             'path':'.published-suite'})
        acceptance=next(step for step in steps if 'VUI_SETUP_INSTALL' in step.get('env',{}))
        self.assertEqual(acceptance['env']['VUI_SETUP_RELEASE_SOURCE'],'${{ github.workspace }}/.published-suite')
        self.assertEqual(acceptance['env']['VUI_PUBLISHED_INSTALL_VERSION'],'0.4.11')
        self.assertEqual(acceptance['env']['VUI_RELEASE_COMMIT'],checkouts[1]['with']['ref'])

    def fixture(self, folder, *, release_commit=None, raw=None):
        source = Path(folder)/'new-bootstrap'
        release_source = Path(folder)/'frozen-release'
        source.mkdir()
        release_source.mkdir()
        (source / 'VERSION').write_text('0.4.10')
        (source / 'install.sh').write_bytes(b'new installer differs from the frozen release\n')
        (source / 'setup.sh').write_bytes(b'new repository bootstrap\n')
        (release_source / 'VERSION').write_text('0.4.9')
        (release_source / 'install.sh').write_bytes(b'fixed published installer\n')
        files = {'install.sh':(release_source / 'install.sh').read_bytes(), 'SHA256SUMS':b'fixed complete checksum list\n',
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
            def __init__(self,url,mapping):self.url=url;self.mapping=mapping
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def geturl(self):return self.url
            def read(self,limit):return self.mapping[self.url][:limit]
        class Opener:
            def __init__(self,mapping):self.mapping=mapping
            def open(self,request,timeout):return Response(request.full_url,self.mapping)
        return source, release_source, Opener(mapping)

    def exact_heads(self,source,release_source,*,bootstrap=None,published=None):
        def read(command,*,cwd,text):
            self.assertEqual(command,['git','rev-parse','HEAD'])
            self.assertTrue(text)
            if Path(cwd)==source:return (bootstrap or self.checkout)+'\n'
            self.assertEqual(Path(cwd),release_source)
            return (published or self.stable)+'\n'
        return read

    def invoke(self, source, release_source):
        return helpers.setup_commands(source,'0.4.9',self.stable,self.checkout,'main',
                                      ['--cert','/fixture/cert with spaces.pem'],['--domain','literal.example.test'],
                                      release_source=release_source)

    def test_new_checkout_and_frozen_release_have_separate_identities(self):
        with tempfile.TemporaryDirectory() as folder:
            source,release_source,opener=self.fixture(folder)
            self.assertEqual((source/'VERSION').read_text(),'0.4.10')
            self.assertNotEqual((source/'install.sh').read_bytes(),(release_source/'install.sh').read_bytes())
            with patch.object(helpers,'build_opener',return_value=opener), \
                    patch.object(helpers.subprocess,'check_output',side_effect=self.exact_heads(source,release_source)):
                result=self.invoke(source,release_source)
            self.assertEqual(result['canonical'],'bash <(curl -fsSL https://raw.githubusercontent.com/ForceMind/V-UI/main/setup.sh)')
            self.assertIn(shlex.quote('/fixture/cert with spaces.pem'),result['initial'][2])
            self.assertIn('--version v0.4.9',result['initial'][2])
            self.assertIn('--upgrade',result['upgrade'][2])
            self.assertIn('release_canonical',result)

    def test_main_advance_or_tamper_is_rejected_before_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            source,release_source,opener=self.fixture(folder,raw=b'different public bootstrap')
            with patch.object(helpers,'build_opener',return_value=opener), \
                    patch.object(helpers.subprocess,'check_output',side_effect=self.exact_heads(source,release_source)):
                with self.assertRaisesRegex(AssertionError,'differs from exact checkout'):self.invoke(source,release_source)

    def test_new_checkout_does_not_relabel_the_frozen_release(self):
        with tempfile.TemporaryDirectory() as folder:
            source,release_source,opener=self.fixture(folder,release_commit=self.checkout)
            with patch.object(helpers,'build_opener',return_value=opener), \
                    patch.object(helpers.subprocess,'check_output',side_effect=self.exact_heads(source,release_source)):
                with self.assertRaisesRegex(AssertionError,'Published source identity mismatch'):self.invoke(source,release_source)

    def test_wrong_bootstrap_or_release_checkout_is_rejected_before_network(self):
        for wrong in ('bootstrap','published'):
            with self.subTest(checkout=wrong),tempfile.TemporaryDirectory() as folder:
                source,release_source,_=self.fixture(folder)
                with patch.object(helpers,'build_opener') as network, \
                        patch.object(helpers.subprocess,'check_output',side_effect=self.exact_heads(source,release_source,**{wrong:'c'*40})):
                    with self.assertRaisesRegex(AssertionError,'checkout is not exact'):self.invoke(source,release_source)
                    network.assert_not_called()

    def test_wrong_release_source_version_is_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as folder:
            source,release_source,_=self.fixture(folder)
            (release_source/'VERSION').write_text('0.4.10')
            with patch.object(helpers,'build_opener') as network, \
                    patch.object(helpers.subprocess,'check_output',side_effect=self.exact_heads(source,release_source)):
                with self.assertRaisesRegex(AssertionError,'Published version differs'):self.invoke(source,release_source)
                network.assert_not_called()

    def test_changed_frozen_installer_bytes_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source,release_source,opener=self.fixture(folder)
            (release_source/'install.sh').write_bytes(b'changed release source installer\n')
            with patch.object(helpers,'build_opener',return_value=opener), \
                    patch.object(helpers.subprocess,'check_output',side_effect=self.exact_heads(source,release_source)):
                with self.assertRaisesRegex(AssertionError,'Published installer differs'):self.invoke(source,release_source)

    def test_complete_assets_digest_and_long_command_remain_required(self):
        for corruption,reason in [('assets','complete fixed asset set'),('digest','metadata/digest mismatch'),
                                  ('body','installation command differs')]:
            with self.subTest(corruption=corruption),tempfile.TemporaryDirectory() as folder:
                source,release_source,opener=self.fixture(folder)
                url='https://api.github.com/repos/ForceMind/V-UI/releases/tags/v0.4.9'
                release=json.loads(opener.mapping[url])
                self.assertEqual(len(release['assets']),19)
                if corruption=='assets':release['assets'].pop()
                elif corruption=='digest':
                    next(item for item in release['assets'] if item['name']=='install.sh')['digest']='sha256:'+'0'*64
                else:release['body']='unverified replacement command'
                opener.mapping[url]=json.dumps(release).encode()
                with patch.object(helpers,'build_opener',return_value=opener), \
                        patch.object(helpers.subprocess,'check_output',side_effect=self.exact_heads(source,release_source)):
                    with self.assertRaisesRegex(AssertionError,reason):self.invoke(source,release_source)

    def test_unapproved_branch_is_rejected_without_network(self):
        with patch.object(helpers,'build_opener') as network:
            with self.assertRaisesRegex(AssertionError,'main or an exact'):
                helpers.setup_commands(Path('/unused'),'0.4.9',self.stable,self.checkout,'dev',[],[],
                                       release_source=Path('/unused-release'))
            network.assert_not_called()

    def test_legacy_published_path_still_requires_its_own_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            source,release_source,_=self.fixture(folder)
            with patch.object(helpers.subprocess,'check_output',return_value=self.checkout+'\n'), \
                    patch.object(helpers,'build_opener') as network:
                with self.assertRaisesRegex(AssertionError,'checkout is not exact'):
                    helpers.published_commands(release_source,'0.4.9',self.stable,[],[])
                network.assert_not_called()
