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
from online_install_helpers import fixture_commands, published_commands, global_menu_check


@unittest.skipUnless(os.getenv('VUI_SYSTEM_INSTALL_TEST') == '1', 'explicit ephemeral systemd installer gate')
class OneClickSystemTests(unittest.TestCase):
    def test_actual_offline_installer_permissions_socket_and_autostart(self):
        self.assertNotEqual(os.geteuid(), 0)
        source = Path(__file__).resolve().parents[1]
        published_version = os.getenv('VUI_PUBLISHED_INSTALL_VERSION')
        bundle = Path(os.environ['VUI_RELEASE_BUNDLE']) if not published_version else None
        sha = bundle.with_suffix(bundle.suffix+'.sha256').read_text().split()[0] if bundle else None
        root = Path('/var/lib/v-ui')
        # Never clean up or overwrite a pre-existing installation on a test host.
        self.assertFalse(root.exists())
        self.assertFalse(Path('/etc/v-ui').exists())
        self.assertFalse(os.path.lexists('/usr/local/sbin/v-ui'))
        self.assertFalse(os.path.lexists('/usr/local/bin/v-ui'))
        from scripts.low_resource_root import optional_gate
        gate = optional_gate()
        with ExitStack() as stack:
            work = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-system-test-')))
            ca, cert, key = certificate_files(work, 'installer')
            port = unused_port(); domain='vpn.example.test'; origin=f'https://{domain}:{port}'
            password = 'Synthetic-systemd-install-password!'
            node_port = unused_port()
            certificate_args = ['--cert',str(cert),'--key',str(key),'--health-ca',str(ca),
                                '--bind','127.0.0.1','--open-firewall','no','--assume-external-ports-open']
            configured_args = ['--domain',domain,'--email','admin@example.test','--admin','installer-admin',
                               '--port',str(port),'--node-port',str(node_port)]
            online = None
            if published_version:
                self.assertIsNone(gate, 'Published online acceptance is separate from the resource fixture')
                online = published_commands(source,published_version,os.environ['VUI_RELEASE_COMMIT'],
                                            certificate_args,configured_args)
            elif os.getenv('VUI_RELEASE_KIT') and gate is None:
                online = fixture_commands(source,work,Path(os.environ['VUI_RELEASE_KIT']),
                                          certificate_args,configured_args)
            cmd = (online['initial'] if online else
                   ['sudo','bash',str(source/'install.sh'),'--bundle',str(bundle),'--sha256',sha,
                    *configured_args,*certificate_args])
            repeat_cmd = online['repeat'] if online else cmd
            upgrade_cmd = online['upgrade'] if online else [*cmd,'--upgrade']
            sudoers_before = hashlib.sha256(subprocess.check_output(['sudo','cat','/etc/sudoers'])).hexdigest()
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
            choices = [('Panel domain: ',domain),('Contact email: ','admin@example.test'),
                       ('管理员用户名 [admin]: ','installer-admin'),('面板 HTTPS 端口 [8443]: ',str(port)),
                       ('默认节点 TCP 端口 [10443]: ',str(node_port))] if online else []
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
                self.assertEqual(hashlib.sha256(subprocess.check_output(['sudo','cat','/etc/sudoers'])).hexdigest(),sudoers_before)
                self.assertFalse(Path('/etc/sudoers.d/v-ui').exists())
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
            def api(path,body=None,*,method=None,extra_headers=()):
                nonlocal cookie
                data=b'' if body is None else json.dumps(body).encode()
                with socket.create_connection(('127.0.0.1',port),timeout=5) as connection:
                    with context.wrap_socket(connection,server_hostname=domain) as tls:
                        headers=[(method or ('GET' if body is None else 'POST'))+' '+path+' HTTP/1.0',
                            'Host: '+domain+':'+str(port),'Origin: '+origin,'X-VUI-Request: 1',
                            'Content-Type: application/json','Content-Length: '+str(len(data))]
                        headers.extend(extra_headers)
                        if cookie:headers.append('Cookie: '+cookie)
                        tls.sendall(('\r\n'.join(headers)+'\r\n\r\n').encode()+data)
                        response=http.client.HTTPResponse(tls);response.begin();raw=response.read()
                        if response.getheader('Set-Cookie'):
                            c=SimpleCookie();c.load(response.getheader('Set-Cookie'));cookie='; '.join(k+'='+v.value for k,v in c.items())
                        return response.status,raw
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
            repeat=subprocess.run(repeat_cmd,capture_output=True,text=True,timeout=300 if published_version else 30)
            if gate:
                gate.installer_receipt('implicit_repeat', repeat.stdout)
            self.assertNotEqual(repeat.returncode,0)
            self.assertIn('already exists',repeat.stderr)
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
            upgraded=subprocess.run(upgrade_cmd,capture_output=True,text=True,timeout=300 if published_version else 120)
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
            if online:
                print('Online managed installer transport: '+online['transport']+'; interactive domain/email/admin/ports, real global v-ui menu and stop/start passed')
            print('One-command systemd: real sudo setup / non-root workers / private state / HTTPS / fd-passed HTTP-01 port80 / auth / repeat refusal / upgrade backup / restart OK')

if __name__=='__main__':unittest.main()
