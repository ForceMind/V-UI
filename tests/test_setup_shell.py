"""Hermetic setup.sh checks: no network, sudo, package installation or host writes.

Only the fixture copy substitutes the fixed command path, root predicate and
published installer digest. Fake tools refuse paths outside the temporary tree;
real Python checks the downloaded bytes before the fake installer can execute.
"""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'setup.sh'
BASH = shutil.which('bash')
PATH_VALUE = '/usr/sbin:/usr/bin:/sbin:/bin'
INSTALLER_SHA = '5aecc3bc2c3ade67946c334091c20ade1e577c7b00c8cadcbe18edf05827312f'
KIT_SHA = 'bae439c5db3586a04406ac5f0455938ed88c4019602c162af89535f1c6c7bde9'

TOOL = r'''
import hashlib, json, os, pathlib, shutil, signal, sys, tempfile
root = pathlib.Path(ROOT_VALUE)
config = json.loads((root/'config.json').read_text())
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
def log(**extra):
    with (root/'commands.jsonl').open('a') as stream:
        stream.write(json.dumps(dict(tool=name,args=args,**extra))+'\n')
def owned(value):
    path = pathlib.Path(value).resolve()
    if root not in path.parents:raise SystemExit('Refusing non-fixture path: '+str(path))
    return path
log()
if name == 'uname':print(config.get('os','Linux'))
elif name == 'sudo':
    if config.get('sudo_status'):raise SystemExit(config['sudo_status'])
    assert args[:3] == ['--','/usr/bin/env','-i'], args
    assert not any('/dev/fd/' in item for item in args)
    (root/'nonroot').unlink()
    os.closerange(3,256)
    os.execv(args[1], args[1:])
elif name == 'mktemp':
    assert args == ['-d','/root/vui-bootstrap.XXXXXXXX'], args
    if config.get('mktemp_empty'):raise SystemExit(0)
    path = pathlib.Path(tempfile.mkdtemp(prefix='owned-',dir=root/'temporary'))
    log(created=str(path),mode=path.stat().st_mode&0o777)
    print(path,flush=True)
    if config.get('signal_stage') == 'mktemp':os.kill(os.getppid(),signal.SIGTERM)
    raise SystemExit(config.get('mktemp_status',0))
elif name == 'curl':
    assert '--proto' in args and args[args.index('--proto')+1] == '=https'
    assert args[args.index('--proto-redir')+1] == '=https'
    assert '--tlsv1.2' in args and '--fail' in args
    assert 'https://github.com/ForceMind/V-UI/releases/download/v0.4.9/install.sh' in args
    destination=owned(args[args.index('-o')+1])
    destination.write_bytes((root/'release-install.sh').read_bytes())
    if config.get('tamper'):destination.write_bytes(b'exit 0\n')
    if config.get('signal_stage') == 'curl':os.kill(os.getppid(),signal.SIGTERM)
    raise SystemExit(config.get('curl_status',0))
elif name == 'sha256sum':
    assert args == ['-c','-']
    digest, path = sys.stdin.read().strip().split('  ',1)
    raise SystemExit(0 if hashlib.sha256(owned(path).read_bytes()).hexdigest() == digest else 1)
elif name == 'apt-get':
    raise SystemExit(config.get('package_status',23))
elif name == 'rm':
    if config.get('rm_status'):raise SystemExit(config['rm_status'])
    assert args[:2] == ['-rf','--']
    for path in args[2:]:shutil.rmtree(owned(path))
elif name == 'cat':
    sys.stdout.write(sys.stdin.read())
else:raise SystemExit('Unexpected fixture command: '+name)
'''

INSTALLER = r'''
import json, os, pathlib, sys
root = pathlib.Path(ROOT_VALUE)
config = json.loads((root/'config.json').read_text())
with (root/'commands.jsonl').open('a') as stream:
    stream.write(json.dumps(dict(tool='installer',args=sys.argv[1:],
                               environment=dict(os.environ),
                               stdin=sys.stdin.readline().rstrip('\n')))+'\n')
raise SystemExit(config.get('installer_status',0))
'''


@unittest.skipUnless(BASH, 'Bash is required')
class SetupShellTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='vui-setup-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.bin = self.root/'bin'
        self.bin.mkdir()
        (self.root/'temporary').mkdir()
        self.config = {}
        self.script = self.root/'setup.sh'
        self.source = SOURCE.read_text()
        tool = TOOL.replace('ROOT_VALUE',repr(str(self.root)))
        for name in ('uname','sudo','mktemp','curl','sha256sum','rm','cat','apt-get'):
            path=self.bin/name
            path.write_text('#!'+sys.executable+'\n'+tool)
            path.chmod(0o700)
        helper=self.root/'installer.py'
        helper.write_text(INSTALLER.replace('ROOT_VALUE',repr(str(self.root))))
        fixture='exec '+shlex.quote(sys.executable)+' '+shlex.quote(str(helper))+' "$@"\n'
        (self.root/'release-install.sh').write_text(fixture)
        fixture_sha=hashlib.sha256(fixture.encode()).hexdigest()
        text=self.source.replace(PATH_VALUE,str(self.bin)).replace('/bin/bash',BASH)
        text=text.replace(INSTALLER_SHA,fixture_sha)
        text=text.replace('[[ "$EUID" -ne 0 ]]','[[ -f '+shlex.quote(str(self.root/'nonroot'))+' ]]')
        self.script.write_text(text)

    def run_setup(self,*args,stdin='fixture-password\n',process_substitution=False):
        (self.root/'config.json').write_text(json.dumps(self.config))
        command=[BASH,str(self.script),*args]
        if process_substitution:
            # Match bash <(curl ...), with a local producer instead of the network.
            command=[BASH,'-c','exec '+shlex.quote(BASH)+' <(/bin/cat '+shlex.quote(str(self.script))+') "$@"','fixture',*args]
        env={'PATH':str(self.bin),'HOME':str(self.root),'PYTHONPATH':'untrusted',
             'PYTHONHOME':'untrusted','LD_LIBRARY_PATH':'untrusted','TMPDIR':'untrusted',
             'HTTPS_PROXY':'untrusted','BASH_ENV':str(self.root/'nonexistent-startup')}
        return subprocess.run(command,input=stdin,text=True,capture_output=True,env=env,timeout=15)

    def logs(self,name=None):
        path=self.root/'commands.jsonl'
        logs=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [line for line in logs if name is None or line['tool']==name]

    def assert_clean(self):
        self.assertEqual(list((self.root/'temporary').iterdir()),[])

    def test_production_fixed_trust_and_environment(self):
        self.assertIn('local installer_sha='+INSTALLER_SHA,self.source)
        self.assertIn('local kit_sha='+KIT_SHA,self.source)
        self.assertIn('mktemp -d /root/vui-bootstrap.XXXXXXXX',self.source)
        self.assertEqual(self.source.count('PATH='+PATH_VALUE),3)
        self.assertIn('exec /usr/bin/env -i',self.source)
        self.assertIn('exec sudo -- /usr/bin/env -i',self.source)
        self.assertIn('"$(declare -f setup_main); setup_main \\"\\$@\\""'.replace('\\\\','\\'), self.source)
        result=subprocess.run([BASH,'-n',str(SOURCE)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_root_flow_preserves_options_stdin_and_clean_environment(self):
        values=['--version','v0.4.9','--domain','literal $(touch never); example',
                '--cert','/path with spaces/cert.pem','--node-udp-port','18000',
                '--node-udp-port=18001','--accept-terms','--upgrade']
        result=self.run_setup(*values)
        self.assertEqual(result.returncode,0,result.stderr)
        installer=self.logs('installer')[0]
        self.assertEqual(installer['args'],['--version','v0.4.9','--with-global-cli',
                                         '--kit-sha256',KIT_SHA,'--interactive',*values[2:]])
        self.assertEqual(installer['stdin'],'fixture-password')
        self.assertNotIn('fixture-password',installer['args'])
        for name in ('PYTHONPATH','PYTHONHOME','LD_LIBRARY_PATH','TMPDIR','HTTPS_PROXY','BASH_ENV'):
            self.assertNotIn(name,installer['environment'])
        self.assertEqual(installer['environment']['HOME'],'/root')
        self.assertEqual(self.logs('mktemp')[1]['mode'],0o700)
        self.assert_clean()

    def test_process_substitution_survives_simulated_sudo_descriptor_closure(self):
        (self.root/'nonroot').touch()
        result=self.run_setup('--port','12345',process_substitution=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(len(self.logs('sudo')),1)
        self.assertEqual(self.logs('installer')[0]['args'][-2:],['--port','12345'])
        self.assert_clean()

    def test_rejects_untrusted_versions_and_managed_or_unknown_options(self):
        cases=[['--version','v0.4.10'],['--version=v0.4.8'],['--version'],
               ['--version','v0.4.9','--version','v0.4.9'],['--interactive'],
               ['--with-global-cli'],['--kit-sha256','0'*64],['--kit-sha256='+'0'*64],
               ['--bundle','/untrusted.zip'],['--sha256','0'*64],['--dry-run'],
               ['--password','secret'],['--port'],['--domain','--upgrade'],['--unknown']]
        for args in cases:
            with self.subTest(args=args):
                result=self.run_setup(*args)
                self.assertEqual(result.returncode,2,result.stderr)
                self.assertFalse(self.logs())

    def test_help_and_os_refusal_never_download_or_elevate(self):
        result=self.run_setup('--help')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('v0.4.9',result.stdout)
        self.assertEqual([line['tool'] for line in self.logs()],['cat'])
        (self.root/'commands.jsonl').unlink()
        self.config['os']='Darwin'
        result=self.run_setup()
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertIn('Linux only',result.stderr)
        self.assertEqual([line['tool'] for line in self.logs()],['uname'])

    def test_download_or_checksum_failure_never_runs_installer(self):
        for config,status in [({'curl_status':22},22),({'tamper':True},1)]:
            with self.subTest(config=config):
                self.config=config
                result=self.run_setup()
                self.assertEqual(result.returncode,status,result.stderr)
                self.assertFalse(self.logs('installer'))
                self.assert_clean()

    def test_installer_exit_status_and_sudo_denial_propagate(self):
        self.config={'installer_status':37}
        result=self.run_setup()
        self.assertEqual(result.returncode,37,result.stderr)
        self.assert_clean()
        (self.root/'nonroot').touch()
        self.config={'sudo_status':19}
        result=self.run_setup()
        self.assertEqual(result.returncode,19,result.stderr)
        self.assertEqual(len(self.logs('installer')),1)
        self.assert_clean()

    def test_prerequisite_failure_stops_before_download(self):
        (self.bin/'curl').unlink()
        self.config={'package_status':23}
        result=self.run_setup()
        self.assertEqual(result.returncode,23,result.stderr)
        self.assertEqual(self.logs('apt-get')[0]['args'],['update'])
        self.assertFalse(self.logs('mktemp'))
        self.assertFalse(self.logs('installer'))
        self.assert_clean()

    def test_cleanup_failure_warns_without_masking_installer_status(self):
        self.config={'installer_status':37,'rm_status':19}
        result=self.run_setup()
        self.assertEqual(result.returncode,37,result.stderr)
        self.assertIn('could not remove setup directory',result.stderr)
        self.assertEqual(len(list((self.root/'temporary').iterdir())),1)

    def test_mktemp_failure_or_signal_owns_and_removes_printed_path(self):
        for config,status in [({'mktemp_status':9},9),({'mktemp_empty':True},1),
                              ({'signal_stage':'mktemp'},143),({'signal_stage':'curl'},143)]:
            with self.subTest(config=config):
                self.config=config
                result=self.run_setup()
                self.assertEqual(result.returncode,status,result.stderr)
                self.assertFalse(self.logs('installer'))
                self.assert_clean()


if __name__ == '__main__':
    unittest.main()
