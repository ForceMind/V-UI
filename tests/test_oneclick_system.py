"""Ephemeral CI host only: exercise the real sudo installer and systemd services."""
from contextlib import ExitStack
import hashlib
import http.client
import json
import os
from pathlib import Path
import pty
import pwd
import re
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
from scripts.ops_menu_smoke import MenuSession
from online_install_helpers import fixture_commands, published_commands, setup_commands, configured_setup_with_tty, global_menu_check, hosted_command_directory, sudo_policy_snapshot


def installer_has_chinese_prompts(version):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise AssertionError('Exact stable installer version required')
    return tuple(map(int, version.split('.'))) >= (0, 4, 10)


def wait_for_managed_https(probe, diagnostics, *, timeout=20):
    """systemd active is not a TLS/authentication readiness observation."""
    started=time.monotonic();deadline=started+timeout;attempts=0;last='no response'
    while True:
        remaining=deadline-time.monotonic()
        if remaining<=0:
            raise AssertionError('Managed HTTPS did not become ready: '+last+'\n'+diagnostics())
        attempts+=1
        try:
            status,_=probe(min(5,remaining))
        except (ConnectionRefusedError,ConnectionResetError,ssl.SSLEOFError,http.client.RemoteDisconnected) as exc:
            last=type(exc).__name__+': '+str(exc)[:200]
            pause=min(.1,max(0,deadline-time.monotonic()))
            if pause:time.sleep(pause)
        except Exception as exc:
            raise AssertionError('Managed HTTPS readiness failed without retry: '+type(exc).__name__+
                                 ': '+str(exc)[:200]+'\n'+diagnostics()) from exc
        else:
            if status!=401:
                raise AssertionError('Managed HTTPS must enforce authentication; got '+str(status)+'\n'+diagnostics())
            elapsed=time.monotonic()-started
            if elapsed>timeout:
                raise AssertionError('Managed HTTPS response exceeded readiness deadline\n'+diagnostics())
            print('Managed HTTPS ready: TLS verified, HTTP401, attempts='+str(attempts)+
                  ', seconds='+format(elapsed,'.3f'))
            return


@unittest.skipUnless(os.getenv('VUI_SYSTEM_INSTALL_TEST') == '1', 'explicit ephemeral systemd installer gate')
class OneClickSystemTests(unittest.TestCase):
    def test_actual_offline_installer_permissions_socket_and_autostart(self):
        self.assertNotEqual(os.geteuid(), 0)
        source = Path(__file__).resolve().parents[1]
        published_version = os.getenv('VUI_PUBLISHED_INSTALL_VERSION')
        new_installer = installer_has_chinese_prompts(published_version or (source/'VERSION').read_text().strip())
        exercise_confirmations = new_installer and os.getenv('VUI_INSTALLER_YN_TEST') == '1'
        bundle = Path(os.environ['VUI_RELEASE_BUNDLE']) if not published_version else None
        sha = bundle.with_suffix(bundle.suffix+'.sha256').read_text().split()[0] if bundle else None
        root = Path('/var/lib/v-ui')
        # Never clean up or overwrite a pre-existing installation on a test host.
        for path in (root,Path('/etc/v-ui'),Path('/usr/local/lib/v-ui'),
                     Path('/usr/local/sbin/v-ui'),Path('/usr/local/bin/v-ui'),
                     *(Path('/etc/systemd/system')/name for name in
                       ('v-ui.service','v-ui-http01.service','v-ui-http01.socket'))):
            self.assertFalse(os.path.lexists(path),'Empty installer runner required: '+str(path))
        from scripts.low_resource_root import optional_gate
        gate = optional_gate()
        with ExitStack() as stack:
            work = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-system-test-')))
            ca, cert, key = certificate_files(work, 'installer')
            port = unused_port(); domain='vpn.example.test'; origin=f'https://{domain}:{port}'
            password = 'Synthetic-systemd-install-password!'
            node_port = unused_port()
            certificate_args = ['--cert',str(cert),'--key',str(key),'--health-ca',str(ca),
                                '--bind','127.0.0.1','--open-firewall','no']
            if not exercise_confirmations:
                certificate_args.append('--assume-external-ports-open')
            configured_args = ['--domain',domain,'--email','admin@example.test','--admin','installer-admin',
                               '--port',str(port),'--node-port',str(node_port)]
            if exercise_confirmations:
                # Initial installation exercises actual y/n consent below.
                # Same unchanged loopback fixture is explicitly acknowledged on
                # configured repeat/upgrade; no real cloud ingress is claimed.
                configured_args.append('--assume-external-ports-open')
            online = None
            if published_version:
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
            sudoers_before = sudo_policy_snapshot() if online else None
            if online:
                self.assertNotIn('/etc/sudoers.d/v-ui',sudoers_before)
            def cleanup():
                subprocess.run(['sudo','systemctl','disable','--now','v-ui.service','v-ui-http01.socket'],capture_output=True)
                subprocess.run(['sudo','systemctl','stop','v-ui-http01.service'],capture_output=True)
                for name in ('v-ui.service','v-ui-http01.service','v-ui-http01.socket'):
                    subprocess.run(['sudo','rm','-f','/etc/systemd/system/'+name],check=True)
                subprocess.run(['sudo','systemctl','daemon-reload'],check=True)
                subprocess.run(['sudo','rm','-rf','/etc/v-ui','/usr/local/lib/v-ui','/var/lib/v-ui'],check=True)
                subprocess.run(['sudo','rm','-f','/usr/local/bin/v-ui','/usr/local/sbin/v-ui'],check=True)
                subprocess.run(['sudo','userdel','v-ui'],capture_output=True)
            if gate is None:
                stack.callback(cleanup)
            else:
                cmd = gate.command(cmd)
                repeat_cmd = cmd
                upgrade_cmd = [*cmd,'--upgrade']
            child, fd = pty.fork()
            if child == 0:
                os.execvp(cmd[0], cmd)
            output = b''; sent_first=False; sent_second=False; status=None
            domain_prompt = '面板域名（例如 panel.example.com）: ' if new_installer and os.getenv('VUI_SETUP_INSTALL') != '1' else 'Panel domain: '
            email_prompt = '联系邮箱: ' if new_installer else 'Contact email: '
            choices = [(domain_prompt,domain),(email_prompt,'admin@example.test'),
                       ('管理员用户名 [admin]: ','installer-admin'),('面板 HTTPS 端口 [8443]: ',str(port)),
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
                    self.fail('Installer timed out: '+output.decode(errors='replace').replace(password,'[REDACTED]')[-12000:])
            finally: os.close(fd)
            clean=output.decode(errors='replace').replace(password,'[REDACTED]')
            if domain_retry:
                self.assertEqual(sent_domains,len(domain_values),clean[-8000:])
                self.assertFalse(advanced_before_valid_domain,clean[-8000:])
                self.assertFalse(product_changed_before_valid_domain,clean[-8000:])
                self.assertGreaterEqual(clean.count('请重新输入'),3,clean[-8000:])
            if exercise_confirmations:
                self.assertGreaterEqual(confirmations['你是否已在云安全组或上游防火墙中放行 '],2,clean[-8000:])
                self.assertIn('请输入 y/yes 或 n/no',clean)
            self.assertTrue(sent_second,clean[-8000:])
            if online:
                self.assertEqual(len(sent_choices),len(choices),clean[-8000:])
            if os.waitstatus_to_exitcode(status) != 0:
                journal=subprocess.run(['sudo','journalctl','-u','v-ui.service','-u','v-ui-http01.service','-n','60','--no-pager'],capture_output=True,text=True)
                clean += '\n' + journal.stdout.replace(password,'[REDACTED]')
            self.assertEqual(os.waitstatus_to_exitcode(status),0,clean[-20000:])
            self.assertNotIn(password,output.decode(errors='replace'))
            if online:
                self.assertIn('一键安装完成',clean)
                self.assertEqual(sudo_policy_snapshot(),sudoers_before)
                global_menu_check(self,{**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},exercise_start_stop=True)
            if gate:
                gate.installer_receipt('initial_install', output.decode(errors='replace'))
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
            menu=['sudo','-u','v-ui',str(release/'runtime/python/bin/python3'),'-B',
                  str(payload/'scripts/deploy.py'),'--root',str(root),'menu']
            with MenuSession(menu,cwd='/tmp',env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}) as session:
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
                        pieces.append((state.stdout+state.stderr).replace(password,'[REDACTED]')[-6000:])
                    except subprocess.SubprocessError:
                        pieces.append('Readiness diagnostic command did not complete')
                return '\n'.join(pieces)
            wait_for_managed_https(lambda budget:api('/api/certificates',timeout=budget,readiness=True),readiness_diagnostics)
            self.assertEqual(api('/api/certificates')[0],401)
            self.assertEqual(api('/api/auth/login',{'username':'installer-admin','password':password})[0],200)
            caps=json.loads(api('/api/certificates/capabilities')[1])
            self.assertTrue(caps['worker_running']);self.assertTrue(caps['panel_hot_reload'])
            self.assertIn(b'certificate-list',api('/certificates')[1])
            # Socket activation really supplies :80 to an unprivileged responder.
            token='T'*43
            command=['sudo','-u','v-ui','/bin/sh','-c',
                'mkdir -p /var/lib/v-ui/data/certificates/http-webroot/.well-known/acme-challenge && printf %s token.key_authorization > /var/lib/v-ui/data/certificates/http-webroot/.well-known/acme-challenge/' + token]
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
            repeat=(configured_setup_with_tty(repeat_cmd,cancel_existing=cancel_existing)
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
            self.assertEqual(Path('/etc/v-ui/service.json').read_bytes(),repeat_configuration)
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
                with MenuSession(local_command,cwd='/tmp',env=dict(os.environ)) as session:
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
                self.assertEqual(Path('/etc/v-ui/service.json').read_bytes(),repeat_configuration)
                self.assertEqual(api('/api/certificates')[0],200)
                print('Existing bootstrap: offline network namespace / actual receipted local menu / service status / exit / original configuration and authenticated session retained OK')
                # Repair is explicit and still delegates configuration ownership
                # to the frozen authenticated installer. Cancel that inner menu
                # after a real public download; preserve the existing instance.
                repair=configured_setup_with_tty(repeat_cmd,cancel_existing=True,bootstrap_action='3')
                self.assertNotEqual(repair.returncode,0,repair.stdout)
                self.assertIn('可信全局 v-ui 预检通过',repair.stdout)
                self.assertIn('已取消，原配置和数据未更改',repair.stdout)
                self.assertEqual(Path('/etc/v-ui/service.json').read_bytes(),repeat_configuration)
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
            # Rerunning with --upgrade checks/stages and makes a stopped backup.
            upgraded=(configured_setup_with_tty(upgrade_cmd,bootstrap_action='2') if os.getenv('VUI_SETUP_INSTALL') == '1' else
                      subprocess.run(upgrade_cmd,capture_output=True,text=True,timeout=300 if published_version else 120))
            if gate:
                gate.installer_receipt('same_bundle_upgrade', upgraded.stdout)
            self.assertEqual(upgraded.returncode,0,(upgraded.stdout+upgraded.stderr)[-15000:])
            if online:
                global_menu_check(self,{**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},exercise_start_stop=False)
            self.assertEqual(api('/api/auth/me')[0],200)
            self.assertEqual(json.loads(api('/api/proxy/settings')[1]), saved_proxy)
            subprocess.run(['sudo','systemctl','restart','v-ui.service'],check=True)
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                try:
                    if api('/api/auth/me')[0]==200:break
                except (OSError,http.client.HTTPException):time.sleep(.2)
            else:self.fail('Managed panel failed restart')
            check_uid('v-ui.service')
            self.assertTrue(json.loads(api('/api/certificates/capabilities')[1])['worker_running'])
            if gate:
                def login_again():
                    self.assertEqual(api('/api/auth/login', {'username': 'installer-admin', 'password': password})[0], 200)
                gate.finish(self, cmd, api, login_again)
            if os.getenv('VUI_EXISTING_INSTALL_TEST') == '1':
                self.assertIsNone(published_version, 'Exercise the exact new candidate, not the frozen public suite')
                self.assertIsNone(gate, 'Existing-configuration acceptance requires its own empty systemd runner')
                existing_cmd = [*(online['initial'] if online else [*cmd,'--interactive']),
                                '--assume-external-ports-open']
                def interaction(answers, expected=0):
                    with MenuSession(existing_cmd,cwd='/tmp',env=dict(os.environ),timeout=300) as session:
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
                self.assertEqual(Path('/etc/v-ui/service.json').read_bytes(),before)
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
                self.assertEqual(config['admin'],'installer-admin')
                self.assertEqual(api('/api/auth/me')[0],200)
                self.assertEqual(json.loads(api('/api/proxy/settings')[1]),saved_proxy)
                # A real unrelated occupied port is rejected before stopping the panel.
                with socket.socket() as occupied:
                    occupied.bind(('127.0.0.1',0));occupied.listen()
                    busy=occupied.getsockname()[1]
                    bad=[(prompt,str(busy) if prompt=='面板 HTTPS 端口 [' else answer) for prompt,answer in edits]
                    blocked=interaction(bad,1)
                    self.assertIn('已被占用',blocked)
                    self.assertEqual(json.loads(Path('/etc/v-ui/service.json').read_text()),config)
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
                self.assertEqual(json.loads(Path('/etc/v-ui/service.json').read_text()),config)
                self.assertEqual(api('/api/auth/me')[0],200)
                self.assertEqual(json.loads(api('/api/proxy/settings')[1]),saved_proxy)
                self.assertFalse(Path('/etc/v-ui/.install-transaction.json').exists())
                check_uid('v-ui.service')
                global_menu_check(self,{**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},exercise_start_stop=True)
                print('Existing installation: automatic saved configuration / cancel / upgrade / ready:false with absent units / email+HTTPS-port reconfigure / occupied port refusal / real TLS failure recovery / existing authenticated session and proxy state retained OK')
            if online:
                print('Online managed installer transport: '+online['transport']+'; interactive domain/email/admin/ports, real global v-ui menu and stop/start passed')
            if domain_retry:
                print('Domain input retry: blank / URL-with-port-and-path / invalid label rejected before email and product installation; uppercase trailing-dot domain accepted and canonicalized; real installed service, global v-ui and upgrade verified')
            if exercise_confirmations:
                print('Chinese installer confirmation: invalid reply re-prompted, short y accepted for cloud/upstream confirmation; temporary loopback fixture only, not proof of real cloud ingress')
            print('One-command systemd: real sudo setup / non-root workers / private state / HTTPS / fd-passed HTTP-01 port80 / auth / repeat protection / upgrade backup / restart OK')

if __name__=='__main__':unittest.main()
