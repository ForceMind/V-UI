"""Real browser/core editor transitions with an isolated synthetic certificate CA."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from app import release_tools
from loopback_helpers import unused_port
from scripts.prepare_frontend import prepare


@unittest.skipUnless(os.getenv('VUI_NODE_BROWSER') == '1', 'explicit node editor browser/core gate')
class InboundEditorBrowserTests(unittest.TestCase):
    def test_managed_security_transitions_refresh_export_and_restore(self):
        from playwright.sync_api import sync_playwright, expect
        source = Path(__file__).resolve().parents[1]
        cores = Path(os.environ['VUI_TEST_CORES']).resolve()
        assets = Path(os.environ['VUI_FRONTEND_ASSETS']).resolve()
        password = 'Synthetic-node-editor-password!'
        with ExitStack() as stack:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-node-editor-browser-')))
            payload = root / 'payload'
            payload.mkdir()
            for name in ('app', 'web'):
                shutil.copytree(source / name, payload / name, ignore=shutil.ignore_patterns('__pycache__', 'vendor'))
            shutil.copyfile(source / 'main.py', payload / 'main.py')
            shutil.copytree(assets, payload / 'web/vendor')
            prepare(payload)
            env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(payload), str(source / 'tests')]),
                   'VUI_DATA_DIR': str(root / 'data'), 'VUI_BIN_DIR': str(cores),
                   'VUI_PUBLIC_ORIGIN': '', 'VUI_CERTIFICATES_ENABLED': '0'}
            code = '''import sys,uvicorn
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from app.certificates import manager as module
from app.services.auth_service import provision_admin
from app.models.database import init_db
root=Path(sys.argv[1]); init_db()
trust=root/'test-ca.pem'
if not trust.exists():
 from test_certificates import SigningProvider
 provider=SigningProvider(); trust.write_bytes(provider.ca.public_bytes(serialization.Encoding.PEM))
 module._instance=module.CertificateManager(root/'data/certificates',provider,trusted_roots=trust.read_bytes())
 provision_admin('editor-admin',sys.stdin.read())
 job=module._instance.create('vpn.example.test','test@example.test','production',False,True)
 module._instance.process_once()
 assert module._instance.job(job['id'])['state']=='succeeded'
 (root/'certificate-id').write_text(job['certificate_id'])
else:
 sys.stdin.read()
 module._instance=module.CertificateManager(root/'data/certificates',trusted_roots=trust.read_bytes())
import main
uvicorn.run(main.app,host='127.0.0.1',port=0,proxy_headers=False,use_colors=False,access_log=False)
'''
            running = []
            def stop():
                if running:
                    process = running.pop()
                    process.terminate()
                    try: process.wait(timeout=15)
                    except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            stack.callback(stop)
            def start():
                logpath = root / f'server-{time.monotonic_ns()}.log'
                log = stack.enter_context(logpath.open('w'))
                process = subprocess.Popen([sys.executable, '-B', '-c', code, str(root)], cwd=payload,
                    env=env, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, text=True)
                running.append(process)
                process.stdin.write(password); process.stdin.close()
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if process.poll() is not None: self.fail(logpath.read_text())
                    match = re.search(r'Uvicorn running on http://127\.0\.0\.1:(\d+)', logpath.read_text())
                    if match: return 'http://127.0.0.1:' + match.group(1)
                    time.sleep(.05)
                self.fail(logpath.read_text())
            base = start()
            certificate_id = (root / 'certificate-id').read_text()
            with sync_playwright() as p:
                options = {'headless': True}
                if os.getenv('VUI_TEST_CHROMIUM'): options['executable_path'] = os.environ['VUI_TEST_CHROMIUM']
                browser = p.chromium.launch(**options)
                context = browser.new_context(viewport={'width': 1280, 'height': 900})
                errors = []; external = []
                def local(route):
                    if not route.request.url.startswith(base + '/'): external.append(route.request.url); route.abort()
                    else: route.continue_()
                def new_page():
                    page = context.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.route('**/*', local)
                    return page
                page = new_page()
                def login():
                    page.goto(base + '/login')
                    page.locator('#login-name').fill('editor-admin'); page.locator('#login-password').fill(password)
                    page.get_by_role('button', name='登录面板', exact=True).click(); page.wait_for_url('**/ui/')
                    page.get_by_text('入站节点', exact=True).first.click()
                def open_edit(name):
                    page.locator('.el-table__row').filter(has_text=name).get_by_role('button', name='编辑', exact=True).click()
                    return page.get_by_role('dialog')
                def security(dialog, label):
                    dialog.locator('.el-form-item').filter(has_text='传输安全').locator('.el-select').click()
                    page.get_by_role('option', name=label, exact=True).click()
                def select_certificate(dialog):
                    dialog.locator('.el-form-item').filter(has_text='托管证书').locator('.el-select').click()
                    page.get_by_role('option', name='vpn.example.test', exact=True).click()
                def save(dialog, identity=None):
                    endpoint = '/api/inbounds' + (f'/{identity}' if identity else '')
                    with page.expect_response(lambda response: response.url.endswith(endpoint) and response.request.method in ('POST', 'PUT'), timeout=20000) as pending:
                        dialog.get_by_role('button', name='保存并应用' if identity else '创建并应用', exact=True).click()
                    self.assertEqual(pending.value.status, 200, pending.value.text())
                    expect(dialog).not_to_be_visible(timeout=20000)
                    return pending.value.json()['inbound']['id']
                def editor(identity):
                    response = context.request.get(base + f'/api/inbounds/{identity}/editor')
                    self.assertEqual(response.status, 200, response.text())
                    return response.json()
                login()
                identities = []
                created_credentials = {}
                for label, value in (('None', 'none'), ('REALITY', 'reality')):
                    name = 'managed-' + value
                    page.get_by_role('button', name='添加节点', exact=True).click()
                    dialog = page.get_by_role('dialog'); dialog.get_by_text('sing-box', exact=True).click()
                    security(dialog, 'TLS')
                    dialog.get_by_placeholder('例如：US-01').fill(name)
                    dialog.get_by_role('spinbutton').fill(str(unused_port()))
                    select_certificate(dialog)
                    identity = save(dialog); identities.append(identity)
                    with sqlite3.connect(root / 'data/v-ui.db') as db:
                        created_credentials[identity] = db.execute('SELECT settings FROM inbounds WHERE id=?', (identity,)).fetchone()[0]
                    before = editor(identity)
                    self.assertEqual(before['certificate_id'], certificate_id)
                    self.assertEqual(before['profile']['security'], 'tls')
                    # Dismissing a changed draft must leave the stored TLS binding intact.
                    dialog = open_edit(name); security(dialog, label)
                    expect(dialog.locator('.el-form-item').filter(has_text='托管证书')).not_to_be_visible()
                    dialog.get_by_role('button', name='取消', exact=True).click()
                    self.assertEqual(editor(identity)['certificate_id'], certificate_id)
                    dialog = open_edit(name)
                    expect(dialog).to_contain_text('已有凭据保留在服务器')
                    # Staying on TLS cannot clear managed renewal while retaining its files.
                    managed = dialog.locator('.el-form-item').filter(has_text='托管证书').locator('.el-select')
                    managed.hover(); managed.locator('.el-select__clear').click()
                    expect(dialog.get_by_placeholder('/etc/letsencrypt/live/example.com/fullchain.pem')).to_have_value('')
                    with page.expect_response(lambda response: response.url.endswith(f'/api/inbounds/{identity}') and response.request.method == 'PUT') as rejected:
                        dialog.get_by_role('button', name='保存并应用', exact=True).click()
                    self.assertEqual(rejected.value.status, 409)
                    self.assertEqual(editor(identity)['certificate_id'], certificate_id)
                    # Reopen the stored binding and switch security directly, leaving its selector hidden.
                    dialog.get_by_role('button', name='取消', exact=True).click(); dialog = open_edit(name)
                    security(dialog, label)
                    if value == 'reality':
                        dialog.get_by_placeholder('example.com:443').fill('target.example.test:443')
                        dialog.get_by_placeholder('留空则使用目标站点域名').fill('target.example.test')
                    save(dialog, identity)
                    state = editor(identity)
                    self.assertEqual(state['profile']['security'], value)
                    self.assertIsNone(state['certificate_id'])
                    self.assertNotIn('reality_private_key', state['profile'])
                    with sqlite3.connect(root / 'data/v-ui.db') as db:
                        stored_stream = json.loads(db.execute('SELECT stream_settings FROM inbounds WHERE id=?', (identity,)).fetchone()[0])
                    private = stored_stream['tls']['reality']['private_key'] if value == 'reality' else None
                    if private: self.assertNotIn(private, json.dumps(state))
                    page.reload(); page.get_by_text('入站节点', exact=True).first.click(); dialog = open_edit(name)
                    expect(dialog.locator('.el-form-item').filter(has_text='传输安全')).to_contain_text(label)
                    dialog.get_by_placeholder('例如：US-01').fill(name + '-edited')
                    save(dialog, identity)
                    self.assertEqual(editor(identity)['profile']['security'], value)
                    self.assertIsNone(editor(identity)['certificate_id'])
                    with sqlite3.connect(root / 'data/v-ui.db') as db:
                        settings, stream = db.execute('SELECT settings,stream_settings FROM inbounds WHERE id=?', (identity,)).fetchone()
                    self.assertEqual(settings, created_credentials[identity])
                    if private: self.assertEqual(json.loads(stream)['tls']['reality']['private_key'], private)
                    # Return to the already-supported TLS export profile; no REALITY support expansion.
                    dialog = open_edit(name + '-edited'); security(dialog, 'TLS'); select_certificate(dialog)
                    save(dialog, identity)
                    self.assertEqual(editor(identity)['certificate_id'], certificate_id)
                headers = {'Origin': base, 'X-VUI-Request': '1'}
                response = context.request.post(base + '/api/subscriptions', headers=headers,
                    data={'label': 'editor-export', 'server': 'vpn.example.test', 'inbound_ids': identities, 'formats': ['mihomo.yaml']})
                self.assertEqual(response.status, 201, response.text())
                url = base + response.json()['paths']['mihomo.yaml']
                exported = context.request.get(url)
                self.assertEqual(exported.status, 200, exported.text())
                self.assertIn('managed-none-edited', exported.text())
                self.assertIn('managed-reality-edited', exported.text())
                self.assertNotIn('PRIVATE KEY', exported.text())
                self.assertNotIn(str(root), exported.text())
                with sqlite3.connect(root / 'data/v-ui.db') as db:
                    secrets_before = db.execute('SELECT id,settings FROM inbounds ORDER BY id').fetchall()
                    self.assertEqual(dict(secrets_before), created_credentials)
                # Stop this page's polling before the fixture restarts on a new
                # random origin; keep the strict network guard on the new page.
                page.close()
                stop()
                archive = root / 'editor-backup.zip'; digest = release_tools.backup(root, archive)
                with sqlite3.connect(root / 'data/v-ui.db') as db:
                    db.execute("UPDATE inbounds SET remark='after-backup'")
                    db.execute('DELETE FROM certificate_bindings')
                release_tools.restore(root, archive, digest)
                base = start(); page = new_page(); login()
                for identity in identities:
                    state = editor(identity)
                    self.assertEqual(state['certificate_id'], certificate_id)
                    self.assertEqual(state['profile']['security'], 'tls')
                    self.assertTrue(state['remark'].endswith('-edited'))
                    dialog = open_edit(state['remark'])
                    expect(dialog).to_contain_text('vpn.example.test')
                    dialog.get_by_role('button', name='取消', exact=True).click()
                with sqlite3.connect(root / 'data/v-ui.db') as db:
                    self.assertEqual(db.execute('SELECT id,settings FROM inbounds ORDER BY id').fetchall(), secrets_before)
                self.assertEqual(external, [])
                self.assertEqual(errors, [])
                self.assertEqual(context.request.get(base + response.json()['paths']['mihomo.yaml']).status, 404)
                print('Node editor browser: managed TLS create / cancel / protected TLS unbind / none and REALITY edits / refresh / re-edit / TLS export / stopped restore / credentials retained OK')
                stop()
                browser.close()


if __name__ == '__main__':
    unittest.main()
