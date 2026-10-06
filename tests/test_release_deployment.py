"""Selected deployment gate: offline install, real HTTPS, local UI and rollback."""
from contextlib import ExitStack
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, HTTPSHandler, ProxyHandler, Request, build_opener
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from app import release_tools as tools
from loopback_helpers import certificate_files, unused_port


@unittest.skipUnless(os.getenv('VUI_RELEASE_BUNDLE'), 'explicit built-package deployment gate')
class ReleaseDeploymentTests(unittest.TestCase):
    def test_nonroot_offline_https_browser_restore_and_code_rollback(self):
        tools.supported_environment()
        self.assertNotEqual(os.geteuid(),0)
        source=Path(__file__).resolve().parents[1]
        bundle=Path(os.environ['VUI_RELEASE_BUNDLE'])
        checksum=bundle.with_suffix(bundle.suffix+'.sha256').read_text().split()[0]
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-installed-')))
            # No pip network, index or extra dependency resolution during staging.
            identity=tools.stage(bundle,checksum,root)
            with self.assertRaises(tools.ReleaseError):tools.stage(bundle,checksum,root)
            tools.activate(root,identity)
            release,current=tools.active(root);payload=release/'payload';python=release/'runtime/python/bin/python3'
            manifest=tools.verify_payload(payload)
            self.assertEqual(manifest['platform'],tools.PLATFORM)
            self.assertEqual(manifest['source_commit'],os.environ['VUI_RELEASE_COMMIT'])
            self.assertEqual(manifest['protocol_profile'],
                'bounded cumulative sing-box exports (including VLESS REALITY/Vision); see docs/COMPATIBILITY.md for exact combinations and application UDP limits')
            self.assertIn('docs/COMPATIBILITY.md',manifest['files'])
            packed_page=(payload/'web/index.html').read_text()
            xray=re.search(r'<el-alert\s+v-if="newInbound.core === \'xray\'"[^>]*>',packed_page)
            self.assertIsNotNone(xray)
            self.assertIn('title="Xray REALITY 尚未验收，不能公开导出；已验收的 sing-box VLESS/direct TCP/REALITY/Vision 仅限 docs/COMPATIBILITY.md 中列明的范围。"',xray[0])
            self.assertIn('type="warning"',xray[0])
            self.assertNotIn('当前已验证导出仅覆盖 sing-box VLESS/TCP/TLS',packed_page)
            self.assertNotIn('REALITY 在本版未验收',packed_page)
            lock=payload/('requirements.'+tools.target_key()+'.lock')
            self.assertTrue(lock.is_file())
            self.assertTrue(all('--hash=sha256:' in line for line in lock.read_text().splitlines()))
            self.assertFalse(any(name.endswith(('.ttf','.otf','.woff','.woff2')) for name in manifest['files']))
            data=root/'data';password='Synthetic-release-test-password!'
            provision=subprocess.run([str(python),'-B','-c',
                'import sys;from app.services.auth_service import provision_admin;provision_admin("release-admin",sys.stdin.read())'],
                input=password,text=True,cwd=payload,env=tools.child_env(payload,data),capture_output=True,timeout=15)
            self.assertEqual(provision.returncode,0,provision.stderr)
            certdir=data/'certs';certdir.mkdir(mode=0o700)
            ca,cert,key=certificate_files(certdir,'panel')
            port,node_port=unused_port(),unused_port();origin=f'https://127.0.0.1:{port}'
            context=ssl.create_default_context(cafile=ca)
            opener=build_opener(ProxyHandler({}),HTTPSHandler(context=context),HTTPCookieProcessor(CookieJar()))
            logs=[];running=[]
            def stop():
                if running:
                    process=running.pop();process.terminate()
                    try:process.wait(timeout=12)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            stack.callback(stop)
            def start():
                log_path=root/f'service-{len(logs)}.log';logs.append(log_path)
                output=stack.enter_context(log_path.open('w'))
                command=[sys.executable,'-B',str(source/'scripts/deploy.py'),'--root',str(root),'run',
                         '--origin',origin,'--cert',str(cert),'--key',str(key),'--port',str(port)]
                process=subprocess.Popen(command,cwd=root,stdout=output,stderr=subprocess.STDOUT)
                running.append(process)
                deadline=time.monotonic()+25
                while time.monotonic()<deadline:
                    if process.poll() is not None:self.fail('HTTPS entrypoint failed:\n'+log_path.read_text())
                    try:
                        with opener.open(origin+'/login',timeout=.5) as response:
                            if response.status==200:return
                    except (OSError,URLError):time.sleep(.1)
                self.fail('HTTPS listener did not become ready:\n'+log_path.read_text())
            def api(path,body=None,method=None):
                headers={'Origin':origin,'X-VUI-Request':'1','Content-Type':'application/json'}
                request=Request(origin+path,data=None if body is None else json.dumps(body).encode(),headers=headers,method=method)
                with opener.open(request,timeout=10) as response:
                    raw=response.read();return response.status,response.headers,raw
            def login():
                status,headers,raw=api('/api/auth/login',{'username':'release-admin','password':password})
                self.assertEqual(status,200)
                self.assertIn('Secure',headers['Set-Cookie']);self.assertIn('__Host-vui_session',headers['Set-Cookie'])
            start()
            with self.assertRaises(HTTPError) as error:api('/api/inbounds')
            self.assertEqual(error.exception.code,401)
            with self.assertRaises(tools.ReleaseError):tools.backup(root,root/'forbidden-live.zip')
            with self.assertRaises(tools.ReleaseError):tools.activate(root,identity)
            # Certificate validation is ON: independent default CA must reject this fixture.
            untrusted=build_opener(ProxyHandler({}),HTTPSHandler(context=ssl.create_default_context()))
            with self.assertRaises(URLError):untrusted.open(origin+'/login',timeout=3)
            plain=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
            try:
                try:plain.request('GET','/api/inbounds');response=plain.getresponse();self.assertNotEqual(response.status,200)
                except (OSError,http.client.HTTPException):pass
            finally:plain.close()
            login()
            with self.assertRaises(HTTPError) as error:api('/api/security/ban_ip?ip=192.0.2.1',{},'POST')
            self.assertEqual(error.exception.code,501)
            # Use actual offline-vendored Vue/Element Plus and create a real TLS node.
            from playwright.sync_api import sync_playwright,expect
            leaf=x509.load_pem_x509_certificate(cert.read_bytes())
            spki=leaf.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
            cert_pin=base64.b64encode(hashlib.sha256(spki).digest()).decode()
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,args=['--ignore-certificate-errors-spki-list='+cert_pin])
                try:
                    browser_context=browser.new_context(viewport={'width':1280,'height':900})
                    page=browser_context.new_page();external=[];errors=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    def only_local(route):
                        if not route.request.url.startswith(origin+'/'):external.append(route.request.url);route.abort()
                        else:route.continue_()
                    page.route('**/*',only_local)
                    page.goto(origin+'/login')
                    page.locator('#login-name').fill('release-admin');page.locator('#login-password').fill(password)
                    page.get_by_role('button',name='登录面板',exact=True).click();page.wait_for_url('**/ui/')
                    expect(page.locator('.logo')).to_contain_text('V-UI')
                    page.get_by_text('入站节点',exact=True).first.click()
                    page.get_by_role('button',name='添加节点',exact=True).click()
                    dialog=page.get_by_role('dialog')
                    dialog.get_by_text('sing-box',exact=True).click()
                    security_item=dialog.locator('.el-form-item').filter(has_text='传输安全')
                    security_item.locator('.el-select').click()
                    page.get_by_role('option',name='TLS',exact=True).click()
                    dialog.get_by_placeholder('例如：US-01').fill('release-browser-node')
                    dialog.get_by_role('spinbutton').fill(str(node_port))
                    dialog.get_by_placeholder('example.com',exact=True).fill('vpn.example.test')
                    dialog.get_by_placeholder('/etc/letsencrypt/live/example.com/fullchain.pem').fill(str(cert))
                    dialog.get_by_placeholder('/etc/letsencrypt/live/example.com/privkey.pem').fill(str(key))
                    with page.expect_response(
                        lambda response: response.url.endswith('/api/inbounds')
                        and response.request.method == 'POST',
                        timeout=20000,
                    ) as pending:
                        dialog.get_by_role('button',name='创建并应用',exact=True).click()
                    created=pending.value
                    if created.status != 200:
                        self.fail('Inbound create failed '+str(created.status)+': '+created.text())
                    expect(dialog).not_to_be_visible(timeout=20000)
                    expect(page.locator('.el-table')).to_contain_text('release-browser-node')

                    row=page.locator('.el-table__row').filter(has_text='release-browser-node')
                    row.get_by_role('button',name='编辑',exact=True).click()
                    edit=page.get_by_role('dialog')
                    expect(edit).to_contain_text('编辑节点')
                    expect(edit).to_contain_text('已有凭据保留在服务器')
                    edit.get_by_placeholder('例如：US-01').fill('release-browser-node-edited')
                    edit.get_by_placeholder('example.com',exact=True).fill('vpn-edited.example.test')
                    edit.get_by_role('button',name='保存并应用',exact=True).click()
                    expect(edit).not_to_be_visible(timeout=20000)
                    expect(page.locator('.el-table')).to_contain_text('release-browser-node-edited')

                    page.reload()
                    page.get_by_text('入站节点',exact=True).first.click()
                    row=page.locator('.el-table__row').filter(has_text='release-browser-node-edited')
                    row.get_by_role('button',name='编辑',exact=True).click()
                    edit=page.get_by_role('dialog')
                    expect(edit.get_by_placeholder('例如：US-01')).to_have_value('release-browser-node-edited')
                    expect(edit.get_by_placeholder('example.com',exact=True)).to_have_value('vpn-edited.example.test')
                    edit.get_by_role('button',name='取消',exact=True).click()

                    page.get_by_text('Mihomo 分流',exact=True).first.click();page.wait_for_url('**/workspace')
                    expect(page.locator('#workspace')).to_be_visible()
                    self.assertEqual(external,[],'Packaged UI must not load CDN assets')
                    self.assertEqual(errors,[],'The real legacy dashboard must run, not just return HTML')
                finally:browser.close()
            rows=json.loads(api('/api/inbounds')[2]);self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['core'],'sing-box')
            state=json.loads(api('/api/cores/status')[2]);self.assertTrue(state['sing-box']['running'])
            self.assertFalse(state['sing-box']['dirty'])
            grant=json.loads(api('/api/subscriptions',{'label':'installed-grant','server':'127.0.0.1','inbound_ids':[rows[0]['id']],'formats':['mihomo.yaml']})[2])
            url=grant['paths']['mihomo.yaml'];token=grant['token']
            raw=api(url)[2];self.assertIn(b'proxies:',raw);self.assertIn(b'FORCE_PROXY',raw)
            self.assertNotIn(str(key).encode(),raw)
            stop();start();login()
            self.assertEqual(len(json.loads(api('/api/inbounds')[2])),1)
            self.assertTrue(json.loads(api('/api/cores/status')[2])['sing-box']['running'])
            stop()
            backup=root/'stopped-backup.zip';backup_sha=tools.backup(root,backup)
            with sqlite3.connect(data/'v-ui.db') as db:db.execute("UPDATE inbounds SET remark='modified-after-backup'")
            tools.restore(root,backup,backup_sha)
            with sqlite3.connect(data/'v-ui.db') as db:
                self.assertEqual(db.execute('SELECT remark FROM inbounds').fetchone()[0],'release-browser-node-edited')
                self.assertEqual(db.execute('SELECT count(*) FROM admin_sessions').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT revoked FROM subscription_grants').fetchone()[0],1)
            start()
            with self.assertRaises(HTTPError) as error:api(url)
            self.assertEqual(error.exception.code,404)
            login();self.assertEqual(len(json.loads(api('/api/inbounds')[2])),1);stop()
            # A second synthetic candidate exercises mechanics, not compatibility
            # with an untested historical release or a new database migration.
            second_source=root/'second-source';shutil.copytree(payload,second_source)
            second_meta={k:v for k,v in manifest.items() if k not in ('schema','files')}
            second_meta['release_id']='rc2-upgrade-fixture'
            second_zip=root/'second.zip';second_sha=tools.create_archive(second_source,second_zip,second_meta)
            second_id=tools.stage(second_zip,second_sha,root);tools.activate(root,second_id)
            self.assertEqual(tools.active(root)[1]['previous_id'],identity)
            start();login();self.assertEqual(len(json.loads(api('/api/inbounds')[2])),1);stop()
            rollback=subprocess.run([sys.executable,'-B',str(source/'scripts/deploy.py'),'--root',str(root),'rollback'],capture_output=True,text=True,timeout=40)
            self.assertEqual(rollback.returncode,0,rollback.stderr)
            self.assertEqual(tools.active(root)[1]['release_id'],identity)
            start();login();stop()
            # Checksum-valid test package but broken candidate code: stage
            # must fail health validation and leave active/data untouched.
            (second_source/'main.py').write_text('raise RuntimeError("synthetic broken candidate")\n')
            second_meta['release_id']='rc2-broken-fixture';broken=root/'broken.zip'
            broken_sha=tools.create_archive(second_source,broken,second_meta)
            before=(root/'CURRENT.json').read_bytes()
            with self.assertRaises(tools.ReleaseError):tools.stage(broken,broken_sha,root)
            self.assertEqual((root/'CURRENT.json').read_bytes(),before)
            self.assertFalse((root/'releases'/'rc2-broken-fixture').exists())
            for log_path in logs:
                self.assertNotIn(token,log_path.read_text());self.assertNotIn(password,log_path.read_text())
            print('rc.2: non-root offline install / exact dependencies / HTTPS / actual local Vue UI / TLS node / restart recovery / stopped backup-restore / token revocation / candidate health / code rollback OK')

if __name__=='__main__':unittest.main()
