"""Ephemeral CI host only: exercise the real sudo installer and systemd services."""
from contextlib import ExitStack
import hashlib
import grp
import http.client
import json
import os
from pathlib import Path
import pty
import pwd
import re
import secrets
import select
import signal
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
from http.cookies import SimpleCookie
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from loopback_helpers import certificate_files, unused_port
from online_install_helpers import PrivateMenuSession as MenuSession
from online_install_helpers import fixture_commands, published_commands, setup_commands, configured_setup_with_tty, global_menu_check, hosted_command_directory, sudo_policy_snapshot
from online_install_helpers import private_installer_credentials, redact_installer_output


def installer_has_chinese_prompts(version):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise AssertionError('Exact stable installer version required')
    return tuple(map(int, version.split('.'))) >= (0, 4, 10)


def wait_for_managed_https(probe, diagnostics, *, timeout=20, private_values=()):
    """systemd active is not a TLS/authentication readiness observation."""
    def safe(text):return redact_installer_output(text,*private_values)
    started=time.monotonic();deadline=started+timeout;attempts=0;last='no response'
    while True:
        remaining=deadline-time.monotonic()
        if remaining<=0:
            raise AssertionError(safe('Managed HTTPS did not become ready: '+last+'\n'+diagnostics()))
        attempts+=1
        try:
            status,_=probe(min(5,remaining))
        except (ConnectionRefusedError,ConnectionResetError,ssl.SSLEOFError,http.client.RemoteDisconnected) as exc:
            last=type(exc).__name__+': '+safe(str(exc))[:200]
            pause=min(.1,max(0,deadline-time.monotonic()))
            if pause:time.sleep(pause)
        except Exception as exc:
            error=AssertionError(safe('Managed HTTPS readiness failed without retry: '+type(exc).__name__+
                                 ': '+safe(str(exc))[:200]+'\n'+diagnostics()))
            if private_values:raise error from None
            raise error from exc
        else:
            if status!=401:
                raise AssertionError(safe('Managed HTTPS must enforce authentication; got '+str(status)+'\n'+diagnostics()))
            elapsed=time.monotonic()-started
            if elapsed>timeout:
                raise AssertionError(safe('Managed HTTPS response exceeded readiness deadline\n'+diagnostics()))
            print('Managed HTTPS ready: TLS verified, HTTP401, attempts='+str(attempts)+
                  ', seconds='+format(elapsed,'.3f'))
            return


@unittest.skipUnless(os.getenv('VUI_SYSTEM_INSTALL_TEST') == '1', 'explicit ephemeral systemd installer gate')
class OneClickSystemTests(unittest.TestCase):
    def test_actual_offline_installer_permissions_socket_and_autostart(self):
        self.assertNotEqual(os.geteuid(), 0)
        source = Path(__file__).resolve().parents[1]
        published_version = os.getenv('VUI_PUBLISHED_INSTALL_VERSION')
        product_version=published_version or (source/'VERSION').read_text().strip()
        new_installer = installer_has_chinese_prompts(product_version)
        automatic_credentials=tuple(map(int,product_version.split('.'))) >= (0,4,13)
        startup_wait_fix=tuple(map(int,product_version.split('.'))) >= (0,4,14)
        exercise_confirmations = new_installer and not automatic_credentials and os.getenv('VUI_INSTALLER_YN_TEST') == '1'
        bundle = Path(os.environ['VUI_RELEASE_BUNDLE']) if not published_version else None
        sha = bundle.with_suffix(bundle.suffix+'.sha256').read_text().split()[0] if bundle else None
        root = Path('/var/lib/v-ui')
        # Never clean up or overwrite a pre-existing installation on a test host.
        for path in (root,Path('/etc/v-ui'),Path('/usr/local/lib/v-ui'),
                     Path('/usr/local/sbin/v-ui'),Path('/usr/local/bin/v-ui'),
                     Path('/var/lib/v-ui-maintenance'),Path('/run/v-ui-maintenance'),
                     Path('/etc/systemd/system/v-ui-maintenance.service'),
                     Path('/etc/systemd/system/v-ui.service.d'),
                     *(Path('/etc/systemd/system')/name for name in
                       ('v-ui.service','v-ui-http01.service','v-ui-http01.socket'))):
            self.assertFalse(os.path.lexists(path),'Empty installer runner required: '+str(path))
        from scripts.low_resource_root import optional_gate
        gate = optional_gate()
        if startup_wait_fix and gate is None:
            self.assertEqual(os.getenv('GITHUB_ACTIONS'),'true')
            self.assertEqual(os.getenv('RUNNER_ENVIRONMENT'),'github-hosted')
            self.assertEqual(os.getenv('GITHUB_REPOSITORY'),'ForceMind/V-UI')
        with ExitStack() as stack:
            work = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-system-test-')))
            ca, cert, key = certificate_files(work, 'installer')
            port = unused_port(); domain='vpn.example.test'; origin=f'https://{domain}:{port}'
            password = 'Synthetic-systemd-install-password!'
            admin_username='installer-admin'
            installation_admin=admin_username
            private_values=[password]
            def safe_output(text):return redact_installer_output(text,*private_values)
            node_port = unused_port()
            certificate_args = ['--cert',str(cert),'--key',str(key),'--health-ca',str(ca),
                                '--bind','127.0.0.1','--open-firewall','no']
            if not exercise_confirmations:
                certificate_args.append('--assume-external-ports-open')
            configured_args = ['--domain',domain,'--email','admin@example.test',
                               '--port',str(port),'--node-port',str(node_port)]
            if not automatic_credentials:configured_args.extend(['--admin',admin_username])
            if exercise_confirmations:
                # Legacy invocation remains accepted; ordinary installation
                # must not request firewall or cloud confirmations.
                configured_args.append('--assume-external-ports-open')
            online = None
            published_upgrade = None
            if os.getenv('VUI_PUBLISHED_UPGRADE_SOURCE'):
                self.assertIsNone(gate, 'Public old-to-new upgrade requires its own empty hosted runner')
                from published_upgrade_helpers import PublishedUpgrade
                published_upgrade = PublishedUpgrade(self,source,work,certificate_args,configured_args)
                online = published_upgrade.old
            elif published_version:
                self.assertIsNone(gate, 'Published online acceptance is separate from the resource fixture')
                if os.getenv('VUI_SETUP_INSTALL') == '1':
                    online = setup_commands(source,published_version,os.environ['VUI_RELEASE_COMMIT'],
                                            os.environ['VUI_SETUP_CHECKOUT'],os.environ['VUI_SETUP_REF'],
                                            certificate_args,configured_args,
                                            release_source=Path(os.environ['VUI_SETUP_RELEASE_SOURCE']))
                else:
                    online = published_commands(source,published_version,os.environ['VUI_RELEASE_COMMIT'],
                                                certificate_args,configured_args)
            elif os.getenv('VUI_RELEASE_KIT') and gate is None:
                online = fixture_commands(source,work,Path(os.environ['VUI_RELEASE_KIT']),
                                          certificate_args,configured_args)
            if online:
                stack.enter_context(hosted_command_directory())
            cmd = (online['initial'] if online else
                   ['sudo','bash',str(source/'install.sh'),'--bundle',str(bundle),'--sha256',sha,
                    *configured_args,*certificate_args])
            repeat_cmd = online['repeat'] if online else cmd
            upgrade_cmd = online['upgrade'] if online else [*cmd,'--upgrade']
            if published_upgrade:
                upgrade_cmd = published_upgrade.new['upgrade']
            sudoers_before = sudo_policy_snapshot() if online else None
            if online:
                self.assertNotIn('/etc/sudoers.d/v-ui',sudoers_before)
            core_switch_fixture = bool(os.getenv('VUI_CORE_SWITCH_FIXTURE'))
            if core_switch_fixture:
                self.assertIsNone(gate)
                self.assertFalse(os.path.lexists('/var/lib/v-ui-core'))
            def cleanup():
                subprocess.run(['sudo','systemctl','disable','--now','v-ui-maintenance.service'],capture_output=True)
                subprocess.run(['sudo','systemctl','disable','--now','v-ui.service','v-ui-http01.socket'],capture_output=True)
                subprocess.run(['sudo','systemctl','stop','v-ui-http01.service'],capture_output=True)
                for name in ('v-ui.service','v-ui-http01.service','v-ui-http01.socket','v-ui-maintenance.service'):
                    subprocess.run(['sudo','rm','-f','/etc/systemd/system/'+name],check=True)
                subprocess.run(['sudo','systemctl','daemon-reload'],check=True)
                subprocess.run(['sudo','rm','-rf','/etc/v-ui','/usr/local/lib/v-ui','/var/lib/v-ui'],check=True)
                subprocess.run(['sudo','rm','-rf','/var/lib/v-ui-maintenance','/run/v-ui-maintenance'],check=True)
                if core_switch_fixture:
                    subprocess.run(['sudo','rm','-rf','/var/lib/v-ui-core'],check=True)
                subprocess.run(['sudo','rm','-f','/usr/local/bin/v-ui','/usr/local/sbin/v-ui'],check=True)
                subprocess.run(['sudo','userdel','v-ui'],capture_output=True)
            if gate is None:
                stack.callback(cleanup)
            else:
                cmd = gate.command(cmd)
                repeat_cmd = cmd
                upgrade_cmd = [*cmd,'--upgrade']
            startup_fixture = startup_wait_fix and gate is None
            dropin=Path('/etc/systemd/system/v-ui.service.d')
            fixture_root=Path('/run/v-ui-startup-fixture-'+secrets.token_hex(12))
            if startup_fixture:
                # This fixture belongs exclusively to the empty GitHub-hosted
                # runner. It changes systemd scheduling, never verified payload
                # bytes or a production installer environment/argument.
                for lookup in (pwd.getpwnam,grp.getgrnam):
                    with self.assertRaises(KeyError):lookup('v-ui')
                self.assertFalse(os.path.lexists(fixture_root))
                subprocess.run(['sudo','install','-d','-m','0700',str(fixture_root)],check=True)
                subprocess.run(['sudo','install','-d','-m','0755',str(dropin)],check=True)
                def cleanup_startup_fixture():
                    subprocess.run(['sudo','rm','-rf',str(dropin),str(fixture_root)],check=True)
                    subprocess.run(['sudo','systemctl','daemon-reload'],check=True)
                stack.callback(cleanup_startup_fixture)
                fixture_script=work/'startup-once.py'
                fixture_script.write_text("import pathlib,time\n"
                    "flag=pathlib.Path(__file__).with_name('mode')\n"
                    "if flag.exists():\n"
                    " mode=flag.read_text();flag.unlink()\n"
                    " if mode=='exit':\n"
                    "  print('RuntimeError: synthetic startup fixture',flush=True)\n"
                    "  raise SystemExit(23)\n"
                    " if mode=='timeout':time.sleep(130)\n")
                subprocess.run(['sudo','install','-m','0700',str(fixture_script),str(fixture_root/'once.py')],check=True)
                def configure_startup_fixture(mode):
                    content='[Service]\nTimeoutStartSec=150\n'
                    if mode=='slow':content+='ExecStartPre=/bin/sleep 12\n'
                    else:
                        marker=work/'startup-mode';marker.write_text(mode)
                        subprocess.run(['sudo','install','-m','0600',str(marker),str(fixture_root/'mode')],check=True)
                        content+='Restart=no\nExecStartPre=+/usr/bin/python3 -B -I -S '+str(fixture_root/'once.py')+'\n'
                    override=work/'startup-override.conf';override.write_text(content)
                    subprocess.run(['sudo','install','-m','0644',str(override),str(dropin/'startup.conf')],check=True)
                    subprocess.run(['sudo','systemctl','daemon-reload'],check=True)
                configure_startup_fixture('slow')
            child, fd = pty.fork()
            if child == 0:
                os.execvp(cmd[0], cmd)
            output = b''; sent_first=False; sent_second=False; status=None
            domain_prompt = '面板域名（例如 panel.example.com）: ' if automatic_credentials or (new_installer and os.getenv('VUI_SETUP_INSTALL') != '1') else 'Panel domain: '
            email_prompt = '联系邮箱: ' if new_installer else 'Contact email: '
            choices = [(domain_prompt,domain),(email_prompt,'admin@example.test'),
                       *([] if automatic_credentials else [('管理员用户名 [admin]: ',admin_username)]),('面板 HTTPS 端口 [8443]: ',str(port)),
                       ('默认节点 TCP 端口 [10443]: ',str(node_port))] if online else []
            domain_retry = os.getenv('VUI_SETUP_INSTALL') == '1' or os.getenv('VUI_DOMAIN_RETRY_TEST') == '1'
            domain_values = ['', 'https://vpn.example.test:3215/path', 'bad..example.test', domain.upper()+'.'] if domain_retry else []
            if domain_retry:
                choices = choices[1:]
            sent_domains = 0
            advanced_before_valid_domain = False
            product_changed_before_valid_domain = False
            confirmations = {prefix:0 for prefix in ('你是否已在实际入站防火墙区域中手动核对并放行 ',
                                                       '你是否已在云安全组或上游防火墙中放行 ')} if exercise_confirmations else {}
            sent_choices = set()
            deadline=time.monotonic()+300
            try:
                while time.monotonic()<deadline:
                    ready,_,_=select.select([fd],[],[],.2)
                    if ready:
                        try: chunk=os.read(fd,65536)
                        except OSError: chunk=b''
                        output+=chunk
                        decoded=output.decode(errors='replace')
                        if domain_retry:
                            if email_prompt in decoded and sent_domains < len(domain_values):
                                advanced_before_valid_domain = True
                            if decoded.count(domain_prompt) > sent_domains and sent_domains < len(domain_values):
                                product_changed_before_valid_domain |= any(os.path.lexists(path) for path in (
                                    root, Path('/etc/v-ui'), Path('/usr/local/lib/v-ui'),
                                    Path('/usr/local/sbin/v-ui'), Path('/usr/local/bin/v-ui'),
                                    Path('/etc/systemd/system/v-ui.service')))
                                os.write(fd,(domain_values[sent_domains]+'\n').encode())
                                sent_domains += 1
                        for prefix,count in tuple(confirmations.items()):
                            if decoded.count(prefix) > count:
                                os.write(fd,('maybe\n' if count == 0 else 'y\n').encode())
                                confirmations[prefix] += 1
                        for prompt,value in choices:
                            if prompt in decoded and prompt not in sent_choices:
                                os.write(fd,(value+'\n').encode());sent_choices.add(prompt)
                        if 'Password (15' in decoded and not sent_first:
                            os.write(fd,(password+'\n').encode());sent_first=True
                        if 'Confirm password:' in decoded and not sent_second:
                            os.write(fd,(password+'\n').encode());sent_second=True
                    exited, value=os.waitpid(child,os.WNOHANG)
                    if exited: status=value;break
                if status is None:
                    os.kill(child,signal.SIGTERM)
                    grace=time.monotonic()+2
                    while time.monotonic()<grace:
                        if os.waitpid(child,os.WNOHANG)[0]:break
                        time.sleep(.05)
                    else:
                        os.kill(child,signal.SIGKILL)
                        grace=time.monotonic()+2
                        while time.monotonic()<grace:
                            if os.waitpid(child,os.WNOHANG)[0]:break
                            time.sleep(.05)
                    self.fail('Installer timed out: '+safe_output(output.decode(errors='replace'))[-12000:])
            finally:
                if status is not None:
                    while select.select([fd],[],[],0)[0]:
                        try:remaining=os.read(fd,65536)
                        except OSError:break
                        if not remaining:break
                        output+=remaining
                os.close(fd)
            if automatic_credentials and os.waitstatus_to_exitcode(status)==0:
                try:admin_username,password=private_installer_credentials(output.decode(errors='replace'))
                except AssertionError:
                    self.fail('Automatic private credential delivery missing or inconsistent\n'+safe_output(output.decode(errors='replace'))[-8000:])
                installation_admin=admin_username
                private_values.extend([admin_username,password])
                self.assertTrue(re.fullmatch(r'vui_[0-9a-f]{12}',admin_username) is not None,'Random default username required')
                self.assertTrue(len(password)>=40,'Strong generated password required')
            clean=safe_output(output.decode(errors='replace'))
            if domain_retry:
                self.assertEqual(sent_domains,len(domain_values),clean[-8000:])
                self.assertFalse(advanced_before_valid_domain,clean[-8000:])
                self.assertFalse(product_changed_before_valid_domain,clean[-8000:])
                self.assertGreaterEqual(clean.count('请重新输入'),3,clean[-8000:])
            if exercise_confirmations:
                self.assertTrue(all(count == 0 for count in confirmations.values()), clean[-8000:])
                self.assertNotIn('云安全组或服务商防火墙未验证', clean)
                self.assertNotIn('请手动放行端口', clean)
            if automatic_credentials:
                self.assertFalse(sent_first or sent_second,'Automatic credentials must not request a password')
                self.assertNotIn('管理员用户名 [admin]',clean)
                self.assertNotIn('你是否已在',clean)
            else:self.assertTrue(sent_second,clean[-8000:])
            if online:
                self.assertEqual(len(sent_choices),len(choices),clean[-8000:])
            if os.waitstatus_to_exitcode(status) != 0:
                journal=subprocess.run(['sudo','journalctl','-u','v-ui.service','-u','v-ui-http01.service','-n','60','--no-pager'],capture_output=True,text=True)
                clean += '\n' + safe_output(journal.stdout)
            self.assertEqual(os.waitstatus_to_exitcode(status),0,clean[-20000:])
            if startup_fixture:
                observations=re.findall(r'HTTPS 就绪验证通过：[^\n]*用时 ([0-9.]+) 秒',clean)
                self.assertTrue(observations,clean[-12000:])
                self.assertGreaterEqual(float(observations[0]),10,'Slow startup did not exercise the old ten-second health window')
                subprocess.run(['sudo','rm',str(dropin/'startup.conf')],check=True)
                subprocess.run(['sudo','systemctl','daemon-reload'],check=True)
                print('Startup fixture: unchanged verified bundle, systemd activating for 12s, TLS/hostname verified HTTP401 after >10s OK')
            if not automatic_credentials:self.assertNotIn(password,output.decode(errors='replace'))
            if online:
                self.assertIn('一键安装完成',clean)
                self.assertEqual(sudo_policy_snapshot(),sudoers_before)
                global_menu_check(self,{**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},exercise_start_stop=True)
            if gate:
                gate.installer_receipt('initial_install', clean)
            uid=pwd.getpwnam('v-ui').pw_uid;self.assertGreater(uid,0)
            for name in ('v-ui.service','v-ui-http01.socket'):
                self.assertEqual(subprocess.run(['systemctl','is-enabled','--quiet',name]).returncode,0)
                self.assertEqual(subprocess.run(['systemctl','is-active','--quiet',name]).returncode,0)
            def check_uid(unit):
                pid=int(subprocess.check_output(['systemctl','show','--property=MainPID','--value',unit]))
                self.assertGreater(pid,0)
                status=Path(f'/proc/{pid}/status').read_text()
                line=next(line for line in status.splitlines() if line.startswith('Uid:'))
                self.assertEqual(int(line.split()[1]),uid)
            check_uid('v-ui.service')
            # Read the actual managed installation using its portable runtime,
            # always as v-ui, never executing service-owned code as root.
            installed=json.loads(Path('/etc/v-ui/service.json').read_text())
            identity=installed['release_id']
            self.assertRegex(identity,r'\A[A-Za-z0-9][A-Za-z0-9._-]{0,100}\Z')
            release=root/'releases'/identity;payload=release/'payload'
            if published_version and os.getenv('VUI_SETUP_INSTALL') == '1':
                manifest = json.loads(subprocess.check_output([
                    'sudo', '-u', 'v-ui', '/bin/cat', str(payload / 'MANIFEST.json')]))
                expected_source = os.environ['VUI_RELEASE_COMMIT']
                self.assertEqual((manifest['version'], manifest['source_commit']),
                                 (published_version, expected_source))
                print(f'Short installer default installed v{published_version} from {expected_source}')
            menu=['sudo','-u','v-ui',str(release/'runtime/python/bin/python3'),'-B',
                  str(payload/'scripts/deploy.py'),'--root',str(root),'menu']
            with MenuSession(menu,cwd='/tmp',env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},private_values=private_values) as session:
                session.expect()
                session.send('1\n');status=session.expect()
                self.assertIn('v-ui.service: active (running)',status)
                self.assertIn('v-ui-http01.socket: active (listening)',status)
                self.assertIn('v-ui-http01.service: inactive (dead)',status)
                self.assertIn('HTTP-01: 按需响应已就绪',status)
                session.send('2\n');metadata=session.expect()
                self.assertIn('面板地址: '+origin,metadata)
                self.assertIn('服务管理器: systemd',metadata)
                self.assertNotIn('bootstrap_python',metadata)
                session.send('0\n');session.finish()
            context=ssl.create_default_context(cafile=ca)
            cookie=''
            def api(path,body=None,*,method=None,extra_headers=(),timeout=5,readiness=False):
                nonlocal cookie
                deadline=time.monotonic()+timeout
                def budget():
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise TimeoutError('Managed HTTPS request exceeded readiness budget')
                    return remaining
                data=b'' if body is None else json.dumps(body).encode()
                with socket.create_connection(('127.0.0.1',port),timeout=timeout) as connection:
                    if readiness:connection.settimeout(budget())
                    with context.wrap_socket(connection,server_hostname=domain) as tls:
                        headers=[(method or ('GET' if body is None else 'POST'))+' '+path+' HTTP/1.0',
                            'Host: '+domain+':'+str(port),'Origin: '+origin,'X-VUI-Request: 1',
                            'Content-Type: application/json','Content-Length: '+str(len(data))]
                        headers.extend(extra_headers)
                        if cookie:headers.append('Cookie: '+cookie)
                        if readiness:tls.settimeout(budget())
                        tls.sendall(('\r\n'.join(headers)+'\r\n\r\n').encode()+data)
                        if readiness:tls.settimeout(budget())
                        response=http.client.HTTPResponse(tls);response.begin()
                        if readiness:tls.settimeout(budget())
                        raw=response.read()
                        if response.getheader('Set-Cookie'):
                            c=SimpleCookie();c.load(response.getheader('Set-Cookie'));cookie='; '.join(k+'='+v.value for k,v in c.items())
                        return response.status,raw
            def readiness_diagnostics():
                pieces=[]
                for command in (['systemctl','show','v-ui.service','--property=MainPID,ActiveState,SubState,Result'],
                                ['sudo','journalctl','-u','v-ui.service','-n','40','--no-pager']):
                    try:
                        state=subprocess.run(command,capture_output=True,text=True,timeout=5)
                        pieces.append(safe_output(state.stdout+state.stderr)[-6000:])
                    except subprocess.SubprocessError:
                        pieces.append('Readiness diagnostic command did not complete')
                return '\n'.join(pieces)
            wait_for_managed_https(lambda budget:api('/api/certificates',timeout=budget,readiness=True),
                                   readiness_diagnostics,private_values=private_values)
            self.assertEqual(api('/api/certificates')[0],401)
            self.assertEqual(api('/api/auth/login',{'username':admin_username,'password':password})[0],200)
            if automatic_credentials:
                initial_username,initial_password=admin_username,password
                admin_username='owner_'+secrets.token_hex(6)
                password=secrets.token_urlsafe(32)
                private_values.extend([admin_username,password])
                self.assertEqual(api('/api/auth/update_profile',{'username':admin_username,
                    'current_password':initial_password,'password':password})[0],200)
                self.assertEqual(api('/api/auth/me')[0],401)
                self.assertEqual(api('/api/auth/login',{'username':initial_username,'password':initial_password})[0],401)
                self.assertEqual(api('/api/auth/login',{'username':admin_username,'password':initial_password})[0],401)
                self.assertEqual(api('/api/auth/login',{'username':admin_username,'password':password})[0],200)
                configuration=Path('/etc/v-ui/service.json').read_bytes()
                self.assertTrue(initial_password.encode() not in configuration and password.encode() not in configuration,
                                'Passwords must never enter installer configuration')
                print('Automatic credentials: random default username and strong password delivered only to PTY; '
                      'real login / profile rename and password change / old credentials rejected / new login passed')
            product_version = published_version or (source/'VERSION').read_text().strip()
            if tuple(map(int,product_version.split('.'))) >= (0,4,12):
                from installed_system_ca_helpers import installed_system_ca_check
                self.assertEqual(api('/api/auth/me')[0],200)
                check_uid('v-ui.service')
                installed_system_ca_check(self,release)
                self.assertEqual(api('/api/auth/me')[0],200)
                check_uid('v-ui.service')
            caps=json.loads(api('/api/certificates/capabilities')[1])
            self.assertTrue(caps['worker_running']);self.assertTrue(caps['panel_hot_reload'])
            self.assertIn(b'certificate-list',api('/certificates')[1])
            # Socket activation really supplies :80 to an unprivileged responder.
            token='T'*43
            # This fixture runs outside the service unit's UMask=0077.
            # Match it so the later restore can fingerprint the whole private tree.
            challenge_dir='/var/lib/v-ui/data/certificates/http-webroot/.well-known/acme-challenge'
            command=['sudo','-u','v-ui','/bin/sh','-c',
                'umask 077; mkdir -p "$1" && printf %s token.key_authorization > "$1/$2"',
                'http01-fixture',challenge_dir,token]
            subprocess.run(command,check=True)
            for path,expected in ((f'/.well-known/acme-challenge/{token}',200),('/api/auth/me',404),('/login',404)):
                connection=http.client.HTTPConnection('127.0.0.1',80,timeout=10)
                connection.request('GET',path);response=connection.getresponse();raw=response.read();connection.close()
                self.assertEqual(response.status,expected)
                if expected==200:self.assertEqual(raw,b'token.key_authorization')
            check_uid('v-ui-http01.service')
            if gate:
                gate.installed()
            # A repeated install without explicit upgrade must not touch the running instance.
            cancel_existing = bool(os.getenv('VUI_SETUP_INSTALL') == '1' and published_version
                                   and tuple(map(int,published_version.split('.'))) >= (0,4,11))
            repeat_configuration=Path('/etc/v-ui/service.json').read_bytes()
            repeat=(configured_setup_with_tty(repeat_cmd,cancel_existing=cancel_existing,private_values=private_values)
                    if os.getenv('VUI_SETUP_INSTALL') == '1' else
                    subprocess.run(repeat_cmd,capture_output=True,text=True,timeout=300 if published_version else 30))
            if gate:
                gate.installer_receipt('implicit_repeat', repeat.stdout)
            if cancel_existing:
                self.assertEqual(repeat.returncode,0,repeat.stdout)
                self.assertIn('已退出，未下载套件',repeat.stdout)
                self.assertNotIn('Detected release target:',repeat.stdout)
            else:
                self.assertNotEqual(repeat.returncode,0)
            self.assertTrue(Path('/etc/v-ui/service.json').read_bytes()==repeat_configuration,'Repeat changed protected configuration')
            self.assertIn('原配置和数据未更改' if cancel_existing else
                          ('安装已存在' if new_installer else 'already exists'),repeat.stdout+repeat.stderr)
            if cancel_existing:
                # Disable all IP networking for the entire bootstrap process,
                # including any dependency/download children. The initial tiny
                # raw script itself has already been verified by setup_commands.
                # Existing systemd management still uses the host's Unix socket.
                # This is a disposable hosted runner, never a production host.
                local_command=['sudo','unshare','--net','/usr/bin/env','-i',
                               'PATH=/usr/sbin:/usr/bin:/sbin:/bin','HOME=/root',
                               'LANG=C.UTF-8','/bin/bash',str(source/'setup.sh')]
                with MenuSession(local_command,cwd='/tmp',env=dict(os.environ),private_values=private_values) as session:
                    session.expect('请选择 [1/2/3/0，回车退出]: ')
                    session.send('1\n')
                    session.expect('请选择: ')
                    session.send('1\n')
                    status_text=session.expect('请选择: ')
                    self.assertIn('v-ui.service',status_text)
                    self.assertNotIn('操作未完成',status_text)
                    session.send('0\n')
                    session.expect('请选择 [1/2/3/0，回车退出]: ')
                    session.send('0\n')
                    session.finish()
                    self.assertNotIn('Detected release target:',session.text())
                    self.assertNotIn('可信全局 v-ui 预检',session.text())
                self.assertTrue(Path('/etc/v-ui/service.json').read_bytes()==repeat_configuration,'Offline menu changed protected configuration')
                self.assertEqual(api('/api/certificates')[0],200)
                print('Existing bootstrap: offline network namespace / actual receipted local menu / service status / exit / original configuration and authenticated session retained OK')
                # Repair is explicit and still delegates configuration ownership
                # to the frozen authenticated installer. Cancel that inner menu
                # after a real public download; preserve the existing instance.
                repair=configured_setup_with_tty(repeat_cmd,cancel_existing=True,bootstrap_action='3',private_values=private_values)
                self.assertNotEqual(repair.returncode,0,repair.stdout)
                self.assertIn('可信全局 v-ui 预检通过',repair.stdout)
                self.assertIn('已取消，原配置和数据未更改',repair.stdout)
                self.assertTrue(Path('/etc/v-ui/service.json').read_bytes()==repeat_configuration,'Repair changed protected configuration')
                self.assertEqual(api('/api/certificates')[0],200)
            check_uid('v-ui.service')
            # Existing fake M1 proxy drafts must survive the actual installer
            # controller's stopped backup. No apply or network change is made.
            status, raw = api('/api/proxy/settings')
            self.assertEqual(status, 200)
            initial = json.loads(raw)
            status, raw = api('/api/proxy/profiles', {
                'format': 'uri', 'candidate_index': 0,
                'content': 'vless://11111111-1111-4111-8111-111111111111@upstream.example:443'
                           '?security=tls&type=tcp&sni=upstream.example#Upgrade-fixture'},
                extra_headers=['If-Match: ' + initial['revision']])
            self.assertEqual(status, 201, raw)
            profile = json.loads(raw)
            settings = {key: value for key, value in profile['snapshot']['settings'].items()
                        if key != 'local_password_set'}
            settings.update(local_proxy_enabled=True, relay_enabled=True,
                            selected_upstream_id=profile['upstream_id'],
                            local_username='upgrade-fixture', local_password='Fake-upgrade-password!')
            status, raw = api('/api/proxy/settings', settings, method='PUT',
                              extra_headers=['If-Match: ' + profile['snapshot']['revision']])
            self.assertEqual(status, 200, raw)
            saved_proxy = json.loads(raw)
            self.assertIsNone(saved_proxy['active'])
            self.assertTrue(saved_proxy['settings']['local_password_set'])
            self.assertNotIn(b'Fake-upgrade-password!', raw)
            if published_upgrade:
                published_upgrade.before(release,api,private_values)
                if published_upgrade.yaml_state is not None:
                    status, raw = api('/api/proxy/settings')
                    self.assertEqual(status, 200)
                    saved_proxy = json.loads(raw)
                    self.assertEqual(saved_proxy['revision'], published_upgrade.yaml_state['revision'])
                    self.assertEqual(saved_proxy['execution_mode'], 'yaml')
                    self.assertIsNone(saved_proxy['active'])
            # Rerunning with --upgrade checks/stages and makes a stopped backup.
            upgraded=(configured_setup_with_tty(upgrade_cmd,bootstrap_action='2',private_values=private_values) if os.getenv('VUI_SETUP_INSTALL') == '1' else
                      subprocess.run(upgrade_cmd,capture_output=True,text=True,timeout=300 if published_version else 120))
            if gate:
                gate.installer_receipt('same_bundle_upgrade', upgraded.stdout)
            self.assertEqual(upgraded.returncode,0,safe_output(upgraded.stdout+upgraded.stderr)[-15000:])
            if online:
                global_menu_check(self,{**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},exercise_start_stop=False)
            if online and os.getenv('VUI_REMOTE_MAINTENANCE_TEST') == '1':
                from scripts.system_maintenance_smoke import nonroot_smoke
                nonroot_smoke(self,source,Path(os.environ['VUI_RELEASE_KIT']),api,saved_proxy)
            self.assertEqual(api('/api/auth/me')[0],200)
            self.assertEqual(json.loads(api('/api/proxy/settings')[1]), saved_proxy)
            if startup_fixture and os.getenv('VUI_EXISTING_INSTALL_TEST') == '1':
                # A one-shot failure is consumed by ExecStartPre; recovery starts
                # the original service without the fault and retains live data.
                before_config=Path('/etc/v-ui/service.json').read_bytes()
                def read_pointer():
                    return json.loads(subprocess.check_output(
                        ['sudo','-u','v-ui','/bin/cat',str(root/'CURRENT.json')],timeout=10))
                before_pointer=read_pointer()
                for mode in ('exit','timeout'):
                    configure_startup_fixture(mode)
                    started_failure=time.monotonic()
                    failed=subprocess.run(upgrade_cmd,capture_output=True,text=True,timeout=300)
                    elapsed_failure=time.monotonic()-started_failure
                    failure_text=safe_output(failed.stdout+failed.stderr)
                    self.assertNotEqual(failed.returncode,0,failure_text[-15000:])
                    self.assertIn('启动失败诊断（本次启动，恢复前）',failure_text)
                    self.assertIn('已恢复上次操作前',failure_text)
                    if mode=='exit':
                        self.assertIn('服务已终止',failure_text)
                        self.assertIn('status=23',failure_text)
                        self.assertLess(elapsed_failure,110,'Terminal exit waited for the full readiness timeout')
                    else:
                        self.assertIn('120 秒启动预算已耗尽',failure_text)
                        self.assertGreaterEqual(elapsed_failure,120)
                        self.assertLess(elapsed_failure,210,'Timeout/diagnostics/recovery were not bounded')
                    self.assertLess(failure_text.index('启动失败诊断（本次启动，恢复前）'),
                                    failure_text.index('已恢复上次操作前'))
                    self.assertEqual(Path('/etc/v-ui/service.json').read_bytes(),before_config)
                    self.assertEqual(read_pointer(),before_pointer)
                    self.assertFalse(Path('/etc/v-ui/.install-transaction.json').exists())
                    self.assertEqual(api('/api/auth/me')[0],200)
                    self.assertEqual(json.loads(api('/api/proxy/settings')[1]),saved_proxy)
                    for line in failure_text.splitlines():
                        if line.startswith(('启动失败诊断（','服务状态：','本次启动日志摘要：',
                                            'Control process exited','Main process exited','RuntimeError',
                                            '已恢复上次操作前')):
                            print(line)
                    subprocess.run(['sudo','rm',str(dropin/'startup.conf')],check=True)
                    subprocess.run(['sudo','systemctl','daemon-reload'],check=True)
                    print('Startup fixture '+mode+': elapsed='+format(elapsed_failure,'.3f')+
                          's, pre-rollback bounded diagnostics, exact saved config/pointer, owner login/session and proxy data retained OK')
            subprocess.run(['sudo','systemctl','restart','v-ui.service'],check=True)
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                try:
                    if api('/api/auth/me')[0]==200:break
                except (OSError,http.client.HTTPException):time.sleep(.2)
            else:self.fail('Managed panel failed restart')
            check_uid('v-ui.service')
            self.assertTrue(json.loads(api('/api/certificates/capabilities')[1])['worker_running'])
            if published_upgrade:
                # A surviving cookie is separate from fresh password validation.
                published_upgrade.after(api)
                cookie=''
                wait_for_managed_https(lambda budget:api('/api/certificates',timeout=budget,readiness=True),
                                       readiness_diagnostics,private_values=private_values)
                published_upgrade.public_subscription(api)
                self.assertEqual(api('/api/auth/login',{'username':admin_username,'password':password})[0],200)
                self.assertEqual(api('/api/auth/me')[0],200)
                global_menu_check(self,{**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},exercise_start_stop=True)
            if gate:
                def login_again():
                    self.assertEqual(api('/api/auth/login', {'username': admin_username, 'password': password})[0], 200)
                gate.finish(self, cmd, api, login_again)
            if os.getenv('VUI_EXISTING_INSTALL_TEST') == '1':
                self.assertIsNone(published_version, 'Exercise the exact new candidate, not the frozen public suite')
                self.assertIsNone(gate, 'Existing-configuration acceptance requires its own empty systemd runner')
                existing_cmd = [*(online['initial'] if online else [*cmd,'--interactive']),
                                '--assume-external-ports-open']
                def interaction(answers, expected=0):
                    with MenuSession(existing_cmd,cwd='/tmp',env=dict(os.environ),timeout=300,private_values=private_values) as session:
                        session.expect('请选择 [1/2/0，回车取消]: ')
                        prefix=session.text()
                        self.assertNotIn('联系邮箱:',prefix)
                        self.assertNotIn('管理员用户名 [admin]',prefix)
                        self.assertNotIn('面板域名（例如',prefix)
                        for prompt,answer in answers:
                            if prompt: session.expect(prompt)
                            session.send(answer+'\n')
                        text=session.finish(expected)
                        self.assertNotIn('Password (15',text)
                        print(text)
                        return text
                # Cancellation preserves config bytes and the live authenticated service.
                before=Path('/etc/v-ui/service.json').read_bytes()
                interaction([(None,'0')],1)
                self.assertTrue(Path('/etc/v-ui/service.json').read_bytes()==before,'Cancellation changed protected configuration')
                self.assertEqual(api('/api/auth/me')[0],200)
                interaction([(None,'1')])
                self.assertEqual(json.loads(api('/api/proxy/settings')[1]),saved_proxy)
                # Real ready:false recovery with existing data and missing unit files.
                subprocess.run(['sudo','systemctl','stop','v-ui.service','v-ui-http01.socket','v-ui-http01.service'],check=True)
                subprocess.run(['sudo','/usr/bin/python3','-B','-I','-S','-c',
                    "import json;from pathlib import Path;p=Path('/etc/v-ui/service.json');c=json.loads(p.read_text());c['ready']=False;p.write_text(json.dumps(c))"],check=True)
                subprocess.run(['sudo','rm','-f',*(str(Path('/etc/systemd/system')/name) for name in
                    ('v-ui.service','v-ui-http01.service','v-ui-http01.socket'))],check=True)
                subprocess.run(['sudo','systemctl','daemon-reload'],check=True)
                resumed=interaction([(None,'1')])
                self.assertIn('尚未完成，可继续安装',resumed)
                self.assertEqual(api('/api/auth/me')[0],200)
                self.assertEqual(json.loads(api('/api/proxy/settings')[1]),saved_proxy)
                old_port=port
                changed_port=unused_port()
                edits=[(None,'2'),('面板域名 [',''),('联系邮箱 [','new-contact@example.test'),
                       ('面板 HTTPS 端口 [',str(changed_port)),('监听 IP [',''),
                       ('节点 TCP 预检端口 [',''),('确认保留数据，按上述配置继续安装','y')]
                changed=interaction(edits)
                self.assertIn('升级前私有备份',changed)
                port=changed_port;origin=f'https://{domain}:{port}'
                config=json.loads(Path('/etc/v-ui/service.json').read_text())
                self.assertEqual(config['email'],'new-contact@example.test')
                self.assertEqual(config['port'],port)
                self.assertTrue(config['admin']==installation_admin,'Installer username metadata must remain unchanged')
                self.assertEqual(api('/api/auth/me')[0],200)
                self.assertEqual(json.loads(api('/api/proxy/settings')[1]),saved_proxy)
                # A real unrelated occupied port is rejected before stopping the panel.
                with socket.socket() as occupied:
                    occupied.bind(('127.0.0.1',0));occupied.listen()
                    busy=occupied.getsockname()[1]
                    bad=[(prompt,str(busy) if prompt=='面板 HTTPS 端口 [' else answer) for prompt,answer in edits]
                    blocked=interaction(bad,1)
                    self.assertIn('已被占用',blocked)
                    self.assertTrue(json.loads(Path('/etc/v-ui/service.json').read_text())==config,'Port conflict changed protected configuration')
                    self.assertEqual(api('/api/auth/me')[0],200)
                # A syntactically valid but wrong certificate hostname causes real
                # TLS health failure, and then restores the old binding/config/service.
                # Keep only the saved leaf: real fullchain files often omit the
                # root. Recovery must still verify that exact saved certificate,
                # without disabling hostname, signature or expiration checks.
                subprocess.run(['sudo','-u','v-ui',str(release/'runtime/python/bin/python3'),'-B','-I','-S','-c',
                    "from pathlib import Path;p=Path('/var/lib/v-ui/data/certs/provided-fullchain.pem');p.write_bytes(p.read_bytes().split(b'-----END CERTIFICATE-----')[0]+b'-----END CERTIFICATE-----\\n')"],check=True)
                bad=[(prompt,'wrong.example.test' if prompt=='面板域名 [' else
                      str(unused_port()) if prompt=='面板 HTTPS 端口 [' else answer) for prompt,answer in edits]
                failed=interaction(bad,1)
                self.assertIn('HTTPS 健康验证失败',failed)
                self.assertIn('已恢复上次操作前',failed)
                self.assertTrue(json.loads(Path('/etc/v-ui/service.json').read_text())==config,'Recovery changed protected configuration')
                self.assertEqual(api('/api/auth/me')[0],200)
                self.assertEqual(json.loads(api('/api/proxy/settings')[1]),saved_proxy)
                self.assertFalse(Path('/etc/v-ui/.install-transaction.json').exists())
                check_uid('v-ui.service')
                global_menu_check(self,{**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},exercise_start_stop=True)
                print('Existing installation: automatic saved configuration / cancel / upgrade / ready:false with absent units / email+HTTPS-port reconfigure / occupied port refusal / real TLS failure recovery / existing authenticated session and proxy state retained OK')
            # Every historical upgrade/data/recovery assertion above completes
            # before disposing only known CI drafts for the installed-core gate.
            if os.getenv('VUI_NETLINK_SYSTEMD_TEST') == '1':
                if published_upgrade:
                    published_upgrade.clear_yaml_fixture(api)
                from scripts.system_maintenance_smoke import installed_netlink_smoke
                def login_after_restore():
                    self.assertEqual(api('/api/auth/login', {'username': admin_username, 'password': password})[0], 200)
                installed_netlink_smoke(self,source,work,api,ca,cert,key,
                    published_upgrade.nodes if published_upgrade else [],private_values,
                    expected_username=admin_username, login_again=login_after_restore)
            if online:
                print('Online managed installer transport: '+online['transport']+'; interactive domain/email/admin/ports, real global v-ui menu and stop/start passed')
            if domain_retry:
                print('Domain input retry: blank / URL-with-port-and-path / invalid label rejected before email and product installation; uppercase trailing-dot domain accepted and canonicalized; real installed service, global v-ui and upgrade verified')
            if exercise_confirmations:
                print('Legacy installer invocation: no firewall/cloud confirmation or unknown warning; real local TLS readiness checked, public ingress not inferred')
            print('One-command systemd: real sudo setup / non-root workers / private state / HTTPS / fd-passed HTTP-01 port80 / auth / repeat protection / upgrade backup / restart OK')

if __name__=='__main__':unittest.main()
