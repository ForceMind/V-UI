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

    def oneclick_contract(self, version=None):
        from scripts import acceptance_job
        root=Path(__file__).resolve().parents[1]
        workflow=yaml.load((root/'.github/workflows/oneclick.yml').read_text(),Loader=yaml.BaseLoader)
        version=version or (root/'VERSION').read_text().strip()
        matrix=acceptance_job.matrix_for_version(version)
        return root,workflow,version,matrix['include']

    def expected_upgrade_baselines(self, version):
        # Independent pre-parallelization source/path literals, not helper output.
        expected=[('0.4.14','b0cc174d04d05e61277075412586f0ba0dd231db','.published-v0414',False),
                  ('0.4.15','65b1e75d90de733408101ec280a92be935731baa','.published-v0415',False),
                  ('0.4.16','18fe631bcf69b68425b454db1795a101e0378842','.published-v0416',False),
                  ('0.4.17','95c201c6e26ed54960f964c675e515890f2eea2a','.published-v0417',False),
                  ('0.4.19','a2b9c188658f082b189e77d237787a96eb9cf921','.published-v0419',True),
                  ('0.4.20','05173798de1baed5d67a866c4125e72f74fe3f4c','.published-v0420',True),
                  ('0.4.21','2aa5e11e3f09c03d048018f033ae888e7c775604','.published-v0421',True)]
        if tuple(map(int,version.split('.'))) > (0,4,22):
            expected.append(('0.4.22','558687a76cd29f4e4735284926eb05bf6664472e','.published-v0422',True))
        if tuple(map(int,version.split('.'))) > (0,4,23):
            expected.append(('0.4.23','be5d8459cdd3eeedce73f2ff6e4663e5eb781e42','.published-v0423',True))
        if tuple(map(int,version.split('.'))) > (0,4,24):
            expected.append(('0.4.24','8b8d7cf82e55609a205455b351f659eb9de91974','.published-v0424',True))
        if tuple(map(int,version.split('.'))) > (0,4,25):
            expected.append(('0.4.25','32a03a5b6719400f21a07a75d04d278b76e0df09','.published-v0425',True))
        return expected

    def test_installer_language_identity_does_not_depend_on_consent_test_opt_in(self):
        from test_oneclick_system import installer_has_chinese_prompts
        from scripts import acceptance_job
        self.assertFalse(installer_has_chinese_prompts('0.4.9'))
        self.assertTrue(installer_has_chinese_prompts('0.4.10'))
        self.assertTrue(installer_has_chinese_prompts('0.4.11'))
        root=Path(__file__).resolve().parents[1]
        for name in ('short-install.yml','online-release-acceptance.yml'):
            workflow=yaml.load((root/'.github/workflows'/name).read_text(),Loader=yaml.BaseLoader)
            envs=[step.get('env',{}) for job in workflow['jobs'].values() for step in job['steps']]
            selected=[env for env in envs if env.get('VUI_SYSTEM_INSTALL_TEST')=='1']
            ordinary=[env for env in selected if not env.get('VUI_PUBLISHED_UPGRADE_SOURCE')]
            self.assertEqual(len(ordinary),1)
            self.assertEqual(ordinary[0].get('VUI_INSTALLER_YN_TEST'),'1')
            self.assertEqual(len(selected),1)
            self.assertEqual([env for env in selected if env.get('VUI_PUBLISHED_UPGRADE_SOURCE')],[])

        root,workflow,version,rows=self.oneclick_contract()
        expected=self.expected_upgrade_baselines(version)
        self.assertEqual(rows,[dict(version=v,source=sha,path=path,netlink=netlink)
                               for v,sha,path,netlink in expected])
        for candidate,count in [('0.4.22',7),('0.4.23',8),('0.4.24',9),('0.4.25',10),('0.4.26',11)]:
            actual=acceptance_job.matrix_for_version(candidate)['include']
            self.assertEqual(len(actual),count)
            self.assertEqual(actual,[dict(version=v,source=sha,path=path,netlink=netlink)
                                    for v,sha,path,netlink in self.expected_upgrade_baselines(candidate)])
        fresh=acceptance_job.role_recipe(version,'fresh')['env']
        self.assertEqual(fresh,{
            'VUI_NETLINK_SYSTEMD_TEST':'1',
            'VUI_TEST_MIHOMO':'/tmp/vui-oneclick/client/mihomo',
            'VUI_SYSTEM_INSTALL_TEST':'1',
            'VUI_REMOTE_MAINTENANCE_TEST':'1',
            'VUI_DOMAIN_RETRY_TEST':'1',
            'VUI_INSTALLER_YN_TEST':'1',
            'VUI_EXISTING_INSTALL_TEST':'1',
            'VUI_RELEASE_BUNDLE':'/tmp/vui-oneclick/vui-linux.zip',
            'VUI_RELEASE_KIT':'/tmp/vui-oneclick/kit'})
        upgrade_steps=workflow['jobs']['published-upgrade']['steps']
        checkout=next(step for step in upgrade_steps if step.get('with',{}).get('path')=='${{ matrix.path }}')
        self.assertEqual(checkout['with'],{'persist-credentials':'false','ref':'${{ matrix.source }}','path':'${{ matrix.path }}'})
        accepted=next(step for step in upgrade_steps if 'acceptance_job run-test' in step.get('run',''))
        self.assertEqual(accepted['name'],'Test published v${{ matrix.version }} to exact candidate upgrade on ephemeral systemd runner')
        self.assertEqual(accepted['env']['ACCEPTANCE_ROLE'],'upgrade-${{ matrix.version }}')
        self.assertEqual(accepted['env']['OLD_SOURCE'],'${{ github.workspace }}/${{ matrix.path }}')
        self.assertIn('--old-source "$OLD_SOURCE"',accepted['run'])
        self.assertLess(upgrade_steps.index(checkout),upgrade_steps.index(accepted))
        for old_version,commit,path,netlink in expected:
            old_source=str(root/path)
            env=acceptance_job.role_recipe(version,'upgrade-'+old_version,old_source=old_source)['env']
            fixed={'VUI_SYSTEM_INSTALL_TEST':'1','VUI_PUBLISHED_INSTALL_VERSION':old_version,
                   'VUI_REMOTE_MAINTENANCE_TEST':'1','VUI_RELEASE_COMMIT':commit,
                   'VUI_PUBLISHED_UPGRADE_SOURCE':old_source,
                   'VUI_RELEASE_KIT':'/tmp/vui-oneclick/kit',
                   'VUI_RELEASE_BUNDLE':'/tmp/vui-oneclick/vui-linux.zip'}
            if netlink:
                fixed.update(VUI_NETLINK_SYSTEMD_TEST='1',VUI_TEST_MIHOMO='/tmp/vui-oneclick/client/mihomo')
            self.assertEqual(env,fixed,(old_version,'must preserve every original role flag'))
            self.assertNotIn('VUI_EXISTING_INSTALL_TEST',env)
            self.assertNotIn('VUI_SETUP_INSTALL',env)

    def test_installed_netlink_gate_is_required_only_for_fresh_and_public_019_onward(self):
        from scripts import acceptance_job
        root,workflow,version,rows=self.oneclick_contract()
        recipes=[('fresh',acceptance_job.role_recipe(version,'fresh'))]
        recipes.extend(('upgrade-'+row['version'],acceptance_job.role_recipe(
            version,'upgrade-'+row['version'],old_source=str(root/row['path']))) for row in rows)
        selected=[role for role,recipe in recipes if recipe['env'].get('VUI_NETLINK_SYSTEMD_TEST')=='1']
        expected=['fresh','upgrade-0.4.19','upgrade-0.4.20','upgrade-0.4.21']
        if tuple(map(int,version.split('.'))) > (0,4,22):expected.append('upgrade-0.4.22')
        if tuple(map(int,version.split('.'))) > (0,4,23):expected.append('upgrade-0.4.23')
        if tuple(map(int,version.split('.'))) > (0,4,24):expected.append('upgrade-0.4.24')
        if tuple(map(int,version.split('.'))) > (0,4,25):expected.append('upgrade-0.4.25')
        self.assertEqual(selected,expected)
        for role,recipe in recipes:
            commands=recipe['commands']
            self.assertEqual(commands[-1][1:],['-B','-m','unittest','discover','-s','tests','-p','test_oneclick_system.py','-v'])
            fetches=[command for command in commands if 'scripts/fetch_mihomo.py' in command]
            self.assertEqual(len(fetches),1 if role in expected else 0)
            if role in expected:
                self.assertEqual(recipe['env']['VUI_TEST_MIHOMO'],'/tmp/vui-oneclick/client/mihomo')
                self.assertEqual(fetches[0][1:],['-B','scripts/fetch_mihomo.py','/tmp/vui-oneclick/client'])
            else:
                self.assertNotIn('VUI_NETLINK_SYSTEMD_TEST',recipe['env'])
                self.assertNotIn('VUI_TEST_MIHOMO',recipe['env'])
        for job_id in ('system-install','published-upgrade'):
            job=workflow['jobs'][job_id]
            self.assertNotIn('if',job,'Required installed-core job cannot be skipped')
            for step in job['steps']:
                if 'acceptance_job run-test' in step.get('run',''):
                    self.assertNotIn('if',step,'Required installed-core acceptance cannot be skipped')
                    self.assertNotIn('continue-on-error',step)
            self.assertFalse(any('scripts/fetch_mihomo.py' in step.get('run','') for step in job['steps']),
                             'Role helper fetches fixed client without creating skipped workflow steps')

    def test_parallel_oneclick_builds_once_and_consumes_same_run_artifact_by_id(self):
        _,workflow,_,_=self.oneclick_contract()
        jobs=workflow['jobs']
        self.assertEqual(set(jobs),{'build-kit','system-install','published-upgrade'})
        self.assertEqual(workflow['permissions'],{'contents':'read'})
        steps=[step for job in jobs.values() for step in job['steps']]
        self.assertEqual(sum('scripts/build_bundle.py ' in step.get('run','') for step in steps),1)
        self.assertEqual(sum('scripts/build_release_kit.py ' in step.get('run','') for step in steps),1)
        producer=jobs['build-kit']
        self.assertFalse(any('acceptance_job run-test' in step.get('run','') for step in producer['steps']))
        inspected=next(step for step in producer['steps'] if step.get('name')=='Inspect producer candidate kit')
        self.assertIn('acceptance_job producer',inspected['run'])
        self.assertIn('acceptance_job matrix --version "$VUI_CANDIDATE_VERSION"',inspected['run'])
        uploaded=next(step for step in producer['steps'] if step.get('name')=='Upload release kit')
        self.assertEqual(uploaded['id'],'upload-kit')
        self.assertEqual(uploaded['with']['path'],'/tmp/vui-oneclick/kit')
        self.assertEqual(producer['outputs']['artifact-id'],'${{ steps.upload-kit.outputs.artifact-id }}')
        self.assertEqual(producer['outputs']['artifact-digest'],'${{ steps.artifact-identity.outputs.artifact-digest }}')
        recorded=next(step for step in producer['steps'] if step.get('name')=='Record producer receipt')
        self.assertIn('--role build',recorded['run'])
        self.assertNotIn('--test-log',recorded['run'])
        self.assertNotIn('if',recorded)
        self.assertEqual(jobs['published-upgrade']['name'],'published-upgrade-${{ matrix.version }}')
        self.assertEqual(jobs['published-upgrade']['strategy'],{
            'fail-fast':'false','max-parallel':'3',
            'matrix':'${{ fromJSON(needs.build-kit.outputs.upgrade-matrix) }}'})
        for job_id in ('build-kit','system-install','published-upgrade'):
            job=jobs[job_id]
            self.assertEqual(job['runs-on'],'ubuntu-24.04')
            first=job['steps'][0]
            self.assertEqual(first['uses'],'actions/checkout@11d5960a326750d5838078e36cf38b85af677262')
            self.assertEqual(first['with'],{'persist-credentials':'false','ref':'${{ github.event.pull_request.head.sha || github.sha }}'})
            for step in job['steps']:
                if step.get('uses','').startswith('actions/upload-artifact@'):
                    name=step['with']['name']
                    self.assertIn('${{ github.event.pull_request.head.sha || github.sha }}',name)
                    self.assertTrue(name.endswith('-${{ github.run_id }}-${{ github.run_attempt }}'))
            logs=next(step for step in job['steps'] if step.get('name')=='Upload acceptance logs')
            self.assertEqual(logs['if'],'always()')
            if job_id=='build-kit':continue
            self.assertEqual(job['needs'],'build-kit')
            self.assertFalse(any('scripts/build_bundle.py ' in step.get('run','') or
                                 'scripts/build_release_kit.py ' in step.get('run','') for step in job['steps']))
            downloaded=next(step for step in job['steps'] if step.get('name')=='Download exact candidate kit')
            self.assertEqual(downloaded['uses'],'actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093')
            # By-ID downloads use artifact-name subdirectories unless explicitly merged.
            self.assertEqual(downloaded['with'],{'artifact-ids':'${{ needs.build-kit.outputs.artifact-id }}',
                                                 'merge-multiple':'true','path':'/tmp/vui-oneclick/kit'})
            prepared=next(step for step in job['steps'] if step.get('name')=='Prepare verified candidate kit')
            for fragment in ('acceptance_job prepare','--expected-checksums-sha256 "$EXPECTED_CHECKSUMS_SHA256"',
                             '--expected-bundle-sha256 "$EXPECTED_BUNDLE_SHA256"',
                             '--bundle-output /tmp/vui-oneclick/vui-linux.zip',
                             'cmp /tmp/vui-oneclick/kit/vui-linux-x86_64-gnu.zip /tmp/vui-oneclick/vui-linux.zip',
                             'sha256sum -c vui-linux.zip.sha256'):
                self.assertIn(fragment,prepared['run'])
            self.assertEqual(prepared['env']['EXPECTED_CHECKSUMS_SHA256'],'${{ needs.build-kit.outputs.checksums-sha256 }}')
            self.assertEqual(prepared['env']['EXPECTED_BUNDLE_SHA256'],'${{ needs.build-kit.outputs.bundle-sha256 }}')
            tested=next(step for step in job['steps'] if 'acceptance_job run-test' in step.get('run',''))
            receipt=next(step for step in job['steps'] if step.get('name')=='Record acceptance receipt')
            upload=next(step for step in job['steps'] if step.get('name')=='Upload acceptance receipt')
            self.assertIn('--test-log /tmp/vui-oneclick/acceptance.log',receipt['run'])
            for step in (downloaded,prepared,tested,receipt,upload):
                self.assertNotIn('if',step)
                self.assertNotIn('continue-on-error',step)
            self.assertLess(job['steps'].index(downloaded),job['steps'].index(prepared))
            self.assertLess(job['steps'].index(prepared),job['steps'].index(tested))
            self.assertLess(job['steps'].index(tested),job['steps'].index(receipt))
            self.assertLess(job['steps'].index(receipt),job['steps'].index(upload))

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
                                             'ref':'71f0e1689cd356f22e12553f74a9ec4fd8bba388',
                                             'path':'.published-suite'})
        acceptance=next(step for step in steps if 'VUI_SETUP_INSTALL' in step.get('env',{}))
        self.assertEqual(acceptance['env']['VUI_SETUP_RELEASE_SOURCE'],'${{ github.workspace }}/.published-suite')
        self.assertEqual(acceptance['env']['VUI_PUBLISHED_INSTALL_VERSION'],'0.4.26')
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
            self.assertNotIn('--version',result['initial'][2])
            self.assertIn('--version v0.4.9',result['repeat'][2])
            self.assertIn('--version v0.4.9',result['upgrade'][2])
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
