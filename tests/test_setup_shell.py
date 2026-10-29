"""Hermetic setup.sh checks: no network, sudo, package installation or host writes.

Only the fixture copy substitutes the fixed command path, root predicate and
published installer digest. Fake tools refuse paths outside the temporary tree;
real Python checks the downloaded bytes before the fake installer can execute.
"""
import ast
import errno
import fcntl
import hashlib
import io
import json
import os
import pty
import re
import select
import signal
import termios
import time
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'setup.sh'
BASH = shutil.which('bash')
PATH_VALUE = '/usr/sbin:/usr/bin:/sbin:/bin'
INSTALLER_SHA = '6c47bb6f05aafe8595bd1763fe227a3d2c4a08c7d74237a18fd82070a3d4f68b'
KIT_SHA = '8cf078530059c7483e294c1b6c7d61c3767ce8dd6a75e61f07c0ac276df5f5e6'

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
    assert 'https://github.com/ForceMind/V-UI/releases/download/v0.4.11/install.sh' in args
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
        text=text.replace(INSTALLER_SHA,fixture_sha).replace('/usr/bin/python3',sys.executable)
        text=text.replace('/etc/v-ui/service.json',str(self.root/'existing-service.json'))
        text=text.replace('[[ "$EUID" -ne 0 ]]','[[ -f '+shlex.quote(str(self.root/'nonroot'))+' ]]')
        self.script.write_text(text)

    def test_existing_install_defers_configuration_to_verified_installer_without_domain_prompt(self):
        # Presence alone defers questions; mutable metadata is never executed or
        # parsed by Bash. The authenticated installer owns all trust checks.
        existing=self.root/'existing-service.json'
        existing.write_text('$(exit 99)\nnot JSON, must not be sourced\n')
        result=self.run_setup(default_domain=False)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.logs('installer')[0]['args'][-2:],['--domain',''])
        self.assertNotIn('Panel domain',result.stdout+result.stderr)

    def run_setup(self,*args,stdin='fixture-password\n',process_substitution=False,default_domain=True):
        if default_domain and not any(value == '--domain' or value.startswith('--domain=') for value in args):
            args=('--domain','panel.example.test',*args)
        (self.root/'config.json').write_text(json.dumps(self.config))
        command=[BASH,str(self.script),*args]
        if process_substitution:
            # Match bash <(curl ...), with a local producer instead of the network.
            command=[BASH,'-c','exec '+shlex.quote(BASH)+' <(/bin/cat '+shlex.quote(str(self.script))+') "$@"','fixture',*args]
        env={'PATH':str(self.bin),'HOME':str(self.root),'PYTHONPATH':'untrusted',
             'PYTHONHOME':'untrusted','LD_LIBRARY_PATH':'untrusted','TMPDIR':'untrusted',
             'HTTPS_PROXY':'untrusted','BASH_ENV':str(self.root/'nonexistent-startup')}
        return subprocess.run(command,input=stdin,text=True,capture_output=True,env=env,timeout=15)

    def run_setup_pty(self, answers, *args, nonroot=False):
        """A real controlling terminal, with a separate stdin password pipe."""
        (self.root/'config.json').write_text(json.dumps(self.config))
        if nonroot:
            (self.root/'nonroot').touch()
        master, slave = pty.openpty()
        def terminal_session():
            os.setsid()
            fcntl.ioctl(slave,termios.TIOCSCTTY,0)
        process=subprocess.Popen([BASH,str(self.script),*args],stdin=subprocess.PIPE,
                                 stdout=slave,stderr=slave,preexec_fn=terminal_session,
                                 pass_fds=(slave,),env={'PATH':str(self.bin)})
        os.close(slave)
        process.stdin.write(b'fixture-password\n')
        process.stdin.close()
        output=b''
        sent=0
        deadline=time.monotonic()+15
        try:
            while time.monotonic()<deadline:
                if select.select([master],[],[],.1)[0]:
                    try:
                        chunk=os.read(master,65536)
                    except OSError as error:
                        if error.errno != errno.EIO:
                            raise
                        chunk=b''
                    output+=chunk
                    prompts=output.count(b'Panel domain: ')
                    if prompts > sent and sent < len(answers):
                        self.assertFalse(self.logs('curl'),'download started before valid domain')
                        self.assertFalse(self.logs('installer'))
                        os.write(master,answers[sent])
                        sent+=1
                if process.poll() is not None:
                    break
            else:
                self.fail('PTY setup timed out: '+output.decode(errors='replace'))
            self.assertEqual(sent,len(answers),output.decode(errors='replace'))
            return process.returncode,output.decode(errors='replace')
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGKILL)
                process.wait()
            os.close(master)

    def logs(self,name=None):
        path=self.root/'commands.jsonl'
        logs=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [line for line in logs if name is None or line['tool']==name]

    def assert_clean(self):
        self.assertEqual(list((self.root/'temporary').iterdir()),[])

    def test_production_fixed_trust_and_environment(self):
        self.assertIn('local version=v0.4.11 ',self.source)
        self.assertIn('[[ "$version" == v0.4.11 ]]',self.source)
        self.assertIn('local installer_sha='+INSTALLER_SHA,self.source)
        self.assertIn('local kit_sha='+KIT_SHA,self.source)
        self.assertIn('mktemp -d /root/vui-bootstrap.XXXXXXXX',self.source)
        self.assertEqual(self.source.count('PATH='+PATH_VALUE),3)
        self.assertIn('exec /usr/bin/env -i',self.source)
        self.assertIn('exec sudo -- /usr/bin/env -i',self.source)
        self.assertIn('/usr/bin/python3 -B -I -S -',self.source)
        self.assertIn("with open('/dev/tty', 'rb+', buffering=0)",self.source)
        self.assertIn('"$(declare -f setup_main); setup_main \\"\\$@\\""'.replace('\\\\','\\'), self.source)
        result=subprocess.run([BASH,'-n',str(SOURCE)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_root_flow_preserves_options_stdin_and_clean_environment(self):
        values=['--version','v0.4.11','--domain','panel.example.test',
                '--cert','/path with spaces/$(touch never); cert.pem','--node-udp-port','18000',
                '--node-udp-port=18001','--accept-terms','--upgrade']
        result=self.run_setup(*values)
        self.assertEqual(result.returncode,0,result.stderr)
        installer=self.logs('installer')[0]
        self.assertEqual(installer['args'],['--version','v0.4.11','--with-global-cli',
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
        self.assertEqual(self.logs('installer')[0]['args'][:2],['--version','v0.4.11'])
        self.assertEqual(self.logs('installer')[0]['args'][-2:],['--port','12345'])
        self.assert_clean()

    def test_rejects_untrusted_versions_and_managed_or_unknown_options(self):
        cases=[['--version','v0.4.12'],['--version','v0.4.10'],['--version','v0.4.9'],['--version=v0.4.8'],['--version'],
               ['--version','v0.4.11','--version','v0.4.11'],['--interactive'],
               ['--with-global-cli'],['--kit-sha256','0'*64],['--kit-sha256='+'0'*64],
               ['--bundle','/untrusted.zip'],['--sha256','0'*64],['--dry-run'],
               ['--password','secret'],['--port'],['--domain','--upgrade'],['--unknown']]
        for args in cases:
            with self.subTest(args=args):
                result=self.run_setup(*args)
                self.assertEqual(result.returncode,2,result.stderr)
                self.assertTrue(any(word in result.stderr for word in ('版本','选项')),result.stderr)
                self.assertFalse(self.logs())

    def test_help_and_os_refusal_never_download_or_elevate(self):
        result=self.run_setup('--help')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('v0.4.11',result.stdout)
        self.assertIn('用法：bash setup.sh',result.stdout)
        self.assertIn('域名每行最多 4096 个 UTF-8 字节',result.stdout)
        self.assertEqual([line['tool'] for line in self.logs()],['cat'])
        (self.root/'commands.jsonl').unlink()
        self.config['os']='Darwin'
        result=self.run_setup()
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertIn('仅支持 Linux',result.stderr)
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

    def run_domain_program(self, physical_input, *, interrupt_at=None):
        """Execute the production reader against a size-checking duplex terminal."""
        program=self.source.split("<<'PYDOMAIN'\n",1)[1].split('\nPYDOMAIN',1)[0]
        class Terminal:
            def __init__(self):
                self.input=io.BytesIO(physical_input)
                self.output=bytearray()
                self.sizes=[]
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def write(self,value):self.output.extend(value);return len(value)
            def readline(self,size):
                self.sizes.append(size)
                if not 0 < size <= 4097:
                    raise AssertionError('Domain reader must use bounded reads')
                if interrupt_at == len(self.sizes):
                    raise KeyboardInterrupt
                return self.input.readline(size)
        terminal=Terminal()
        stdout=io.StringIO()
        stderr=io.StringIO()
        status=0
        with patch('builtins.open',return_value=terminal) as opened, \
                patch.object(sys,'argv',['domain-reader','']), \
                patch.object(sys,'stdout',stdout),patch.object(sys,'stderr',stderr):
            try:
                exec(compile(program,'setup-domain-reader','exec'),{})
            except SystemExit as error:
                status=error.code
            opened.assert_called_once_with('/dev/tty','rb+',buffering=0)
        return status,stdout.getvalue(),terminal,stderr.getvalue()

    def test_oversized_physical_line_discards_valid_looking_suffix(self):
        # The suffix starts exactly after the first read, but belongs to the
        # rejected physical line. Only the following physical line may succeed.
        physical=b'x'*4097+b'wrong.example.test\n'+b'  B\xc3\x9cCHER.Example.  \n'
        status,output,terminal,_=self.run_domain_program(physical)
        self.assertEqual(status,0)
        self.assertEqual(output,'xn--bcher-kva.example\n')
        self.assertEqual(terminal.output.decode().count('域名无效'),1)
        self.assertEqual(terminal.output.decode().count('Panel domain: '),2)
        self.assertEqual(len(terminal.sizes),3)
        self.assertEqual(terminal.input.read(),b'')

    def test_oversized_input_uses_bounded_chunks_and_preserves_eof_or_interrupt(self):
        for data,interruption in [(b'x'*20000,None),(b'x'*20000,3)]:
            with self.subTest(interrupt_at=interruption):
                status,output,terminal,stderr=self.run_domain_program(data,interrupt_at=interruption)
                self.assertNotEqual(status,0)
                self.assertEqual(output,'')
                if interruption:
                    self.assertEqual(status,130)
                    self.assertIn('输入已中断；安装已取消',stderr)
                else:
                    self.assertIn('输入已结束；安装已取消',status)
                self.assertTrue(all(size == 4097 for size in terminal.sizes))

    def test_domain_physical_line_byte_boundary(self):
        for raw,invalid in [(b' '*(4096-len(b'panel.example.test\n'))+b'panel.example.test\n',0),
                            (b' '*(4097-len(b'ignored.example.test\n'))+b'ignored.example.test\n',1)]:
            with self.subTest(size=len(raw)):
                status,output,terminal,_=self.run_domain_program(raw+b'panel.example.test\n')
                self.assertEqual(status,0)
                self.assertEqual(output,'panel.example.test\n')
                self.assertEqual(terminal.output.decode().count('域名无效'),invalid)

    def test_domain_validator_matches_installer_fqdn_including_idna(self):
        embedded=self.source.split("<<'PYDOMAIN'\n",1)[1].split('\nPYDOMAIN',1)[0]
        def load_function(source,error):
            function=next(node for node in ast.parse(source).body
                          if isinstance(node,ast.FunctionDef) and node.name == 'fqdn')
            namespace={'re':re,'InstallError':error}
            exec(compile(ast.Module(body=[function],type_ignores=[]),'fqdn-contract','exec'),namespace)
            return namespace['fqdn']
        validator=load_function(embedded,ValueError)
        installer=load_function((ROOT/'scripts/install_system.py').read_text(),ValueError)
        corpus=['','   ','example','https://example.com','example.com:443','192.0.2.1',
                '2001:db8::1','*.example.com','bad_label.example','-bad.example',
                'bad-.example','a..example','a'*64+'.com','a.'+'1'*63,
                '.'.join(['a'*63]*4),' PANEL.Example.COM... ',
                '例子.测试','BÜCHER.Example.','ＦＯＯ．example','faß.de',
                'xn--bcher-kva.example','123.example','example.123','bad/label.example',
                'a\x00.example','例子。测试',None]
        for value in corpus:
            with self.subTest(value=value):
                try:
                    expected=installer(value)
                except ValueError:
                    with self.assertRaises(ValueError):validator(value)
                else:
                    self.assertEqual(validator(value),expected)

    def test_explicit_valid_domain_normalizes_without_tty(self):
        for args,expected in [(['--domain','  PANEL.Example.TEST... '],'panel.example.test'),
                              (['--domain=BÜCHER.Example.'],'xn--bcher-kva.example'),
                              (['--domain','例子.测试'],'xn--fsqu00a.xn--0zwm56d')]:
            with self.subTest(args=args):
                result=self.run_setup(*args)
                self.assertEqual(result.returncode,0,result.stderr)
                received=self.logs('installer')[-1]['args']
                self.assertEqual(received[received.index('--domain')+1],expected)
                self.assertNotIn('Panel domain: ',result.stderr)
                self.assert_clean()

    def test_invalid_explicit_domain_without_tty_fails_before_download(self):
        malicious='$(touch '+str(self.root/'must-not-exist')+').example; echo bad'
        for value in ('','   ','https://example.com','example.com:443','192.0.2.1',
                      '*.example.com','bad_label.example',malicious):
            with self.subTest(value=value):
                result=self.run_setup('--domain',value)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('域名输入需要可用的控制终端',result.stderr)
                self.assertFalse(self.logs('curl'))
                self.assertFalse(self.logs('installer'))
                self.assertFalse((self.root/'must-not-exist').exists())
                self.assert_clean()

    def test_tty_reprompts_empty_invalid_then_idna_after_sudo_closes_fds(self):
        answers=[b'\n',b'   \n',b'https://example.com\n',b'example.com:443\n',
                 b'192.0.2.1\n',b'*.example.com\n',b'bad_label.example\n',
                 b'-bad.example\n','  BÜCHER.Example.  \n'.encode()]
        status,output=self.run_setup_pty(answers,nonroot=True)
        self.assertEqual(status,0,output)
        self.assertEqual(output.count('域名无效'),len(answers)-1,output)
        received=self.logs('installer')[0]
        self.assertEqual(received['args'][-2:],['--domain','xn--bcher-kva.example'])
        self.assertEqual(received['stdin'],'fixture-password')
        self.assertEqual(len(self.logs('sudo')),1)
        self.assert_clean()

    def test_invalid_explicit_domain_can_be_corrected_at_tty(self):
        status,output=self.run_setup_pty([b'panel.example.test\n'],'--domain=')
        self.assertEqual(status,0,output)
        self.assertEqual(self.logs('installer')[0]['args'][-2:],['--domain','panel.example.test'])
        self.assert_clean()

    def test_nonroot_flag_like_domain_can_be_corrected_after_sudo(self):
        status,output=self.run_setup_pty([b'panel.example.test\n'],'--domain=--invalid.example',nonroot=True)
        self.assertEqual(status,0,output)
        self.assertEqual(self.logs('installer')[0]['args'][-2:],['--domain','panel.example.test'])
        self.assertEqual(len(self.logs('sudo')),1)
        self.assert_clean()

    def test_tty_eof_and_interrupt_abort_before_download(self):
        for answer in (b'\x04',b'\x03'):
            with self.subTest(answer=answer):
                status,output=self.run_setup_pty([answer])
                self.assertNotEqual(status,0,output)
                if answer == b'\x03':self.assertEqual(status,130,output)
                self.assertFalse(self.logs('curl'))
                self.assertFalse(self.logs('installer'))
                self.assert_clean()

    def test_missing_fixed_python_attempts_os_package_manager_and_stops(self):
        self.script.write_text(self.script.read_text().replace(sys.executable,str(self.root/'missing-system-python')))
        self.config={'package_status':23}
        result=self.run_setup()
        self.assertEqual(result.returncode,23,result.stderr)
        self.assertEqual(self.logs('apt-get')[0]['args'],['update'])
        self.assertFalse(self.logs('curl'))
        self.assertFalse(self.logs('installer'))
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
        self.assertIn('无法删除安装入口的临时目录',result.stderr)
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
