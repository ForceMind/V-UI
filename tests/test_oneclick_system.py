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


@unittest.skipUnless(os.getenv('VUI_SYSTEM_INSTALL_TEST') == '1', 'explicit ephemeral systemd installer gate')
class OneClickSystemTests(unittest.TestCase):
    def test_actual_offline_installer_permissions_socket_and_autostart(self):
        self.assertNotEqual(os.geteuid(), 0)
        source = Path(__file__).resolve().parents[1]
        bundle = Path(os.environ['VUI_RELEASE_BUNDLE'])
        sha = bundle.with_suffix(bundle.suffix+'.sha256').read_text().split()[0]
        root = Path('/var/lib/v-ui')
        # Never clean up or overwrite a pre-existing installation on a test host.
        self.assertFalse(root.exists())
        self.assertFalse(Path('/etc/v-ui').exists())
        from scripts.low_resource_root import optional_gate
        gate = optional_gate()
        with ExitStack() as stack:
            work = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-system-test-')))
            ca, cert, key = certificate_files(work, 'installer')
            port = unused_port(); domain='vpn.example.test'; origin=f'https://{domain}:{port}'
            password = 'Synthetic-systemd-install-password!'
            cmd = ['sudo','bash',str(source/'install.sh'),'--bundle',str(bundle),'--sha256',sha,
                   '--domain',domain,'--email','admin@example.test','--admin','installer-admin',
                   '--cert',str(cert),'--key',str(key),'--health-ca',str(ca),
                   '--bind','127.0.0.1','--port',str(port),'--open-firewall','no','--assume-external-ports-open']
            def cleanup():
                subprocess.run(['sudo','systemctl','disable','--now','v-ui.service','v-ui-http01.socket'],capture_output=True)
                subprocess.run(['sudo','systemctl','stop','v-ui-http01.service'],capture_output=True)
                for name in ('v-ui.service','v-ui-http01.service','v-ui-http01.socket'):
                    subprocess.run(['sudo','rm','-f','/etc/systemd/system/'+name],check=True)
                subprocess.run(['sudo','systemctl','daemon-reload'],check=True)
                subprocess.run(['sudo','rm','-rf','/etc/v-ui','/usr/local/lib/v-ui','/var/lib/v-ui'],check=True)
                subprocess.run(['sudo','userdel','v-ui'],capture_output=True)
            if gate is None:
                stack.callback(cleanup)
            else:
                cmd = gate.command(cmd)
            child, fd = pty.fork()
            if child == 0:
                os.execvp(cmd[0], cmd)
            output = b''; sent_first=False; sent_second=False; status=None
            deadline=time.monotonic()+300
            try:
                while time.monotonic()<deadline:
                    ready,_,_=select.select([fd],[],[],.2)
                    if ready:
                        try: chunk=os.read(fd,65536)
                        except OSError: chunk=b''
                        output+=chunk
                        decoded=output.decode(errors='replace')
                        if 'Password (15' in decoded and not sent_first:
                            os.write(fd,(password+'\n').encode());sent_first=True
                        if 'Confirm password:' in decoded and not sent_second:
                            os.write(fd,(password+'\n').encode());sent_second=True
                    exited, value=os.waitpid(child,os.WNOHANG)
                    if exited: status=value;break
                if status is None:
                    os.kill(child,signal.SIGTERM);os.waitpid(child,0)
                    self.fail('Installer timed out: '+output.decode(errors='replace').replace(password,'[REDACTED]')[-12000:])
            finally: os.close(fd)
            clean=output.decode(errors='replace').replace(password,'[REDACTED]')
            self.assertTrue(sent_second,clean[-8000:])
            if os.waitstatus_to_exitcode(status) != 0:
                journal=subprocess.run(['sudo','journalctl','-u','v-ui.service','-u','v-ui-http01.service','-n','60','--no-pager'],capture_output=True,text=True)
                clean += '\n' + journal.stdout.replace(password,'[REDACTED]')
            self.assertEqual(os.waitstatus_to_exitcode(status),0,clean[-20000:])
            self.assertNotIn(password,output.decode(errors='replace'))
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
            context=ssl.create_default_context(cafile=ca)
            cookie=''
            def api(path,body=None):
                nonlocal cookie
                data=b'' if body is None else json.dumps(body).encode()
                with socket.create_connection(('127.0.0.1',port),timeout=5) as connection:
                    with context.wrap_socket(connection,server_hostname=domain) as tls:
                        headers=[('GET' if body is None else 'POST')+' '+path+' HTTP/1.0',
                            'Host: '+domain+':'+str(port),'Origin: '+origin,'X-VUI-Request: 1',
                            'Content-Type: application/json','Content-Length: '+str(len(data))]
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
            repeat=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
            self.assertNotEqual(repeat.returncode,0)
            self.assertIn('already exists',repeat.stderr)
            check_uid('v-ui.service')
            # Rerunning with --upgrade checks/stages and makes a stopped backup.
            upgraded=subprocess.run(cmd+['--upgrade'],capture_output=True,text=True,timeout=120)
            self.assertEqual(upgraded.returncode,0,(upgraded.stdout+upgraded.stderr)[-15000:])
            self.assertEqual(api('/api/auth/me')[0],200)
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
            print('One-command systemd: real sudo setup / non-root workers / private state / HTTPS / fd-passed HTTP-01 port80 / auth / repeat refusal / upgrade backup / restart OK')

if __name__=='__main__':unittest.main()
