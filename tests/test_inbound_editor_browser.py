"""Real browser/core editor transitions with an isolated synthetic certificate CA."""
from contextlib import ExitStack
import base64
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import parse_qs, unquote, urlsplit

import yaml

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
                    saved = pending.value.json()['inbound']
                    self.assertNotIn('settings', saved)
                    self.assertNotIn('stream_settings', saved)
                    self.assertIs(type(saved['managed_certificate_eligible']), bool)
                    return saved['id']
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
                        dialog.get_by_placeholder('example.com:443').fill('127.0.0.1:19445')
                        dialog.get_by_placeholder('必填，例如 reference.example.test').fill('reference.example.test')
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
                    if value == 'reality':
                        self.assertEqual(json.loads(settings)['users'][0]['uuid'], json.loads(created_credentials[identity])['users'][0]['uuid'])
                        self.assertEqual(json.loads(settings)['users'][0]['flow'], 'xtls-rprx-vision')
                    else:
                        self.assertEqual(settings, created_credentials[identity])
                    if private: self.assertEqual(json.loads(stream)['tls']['reality']['private_key'], private)
                    # Explicitly returning to ordinary TLS clears Vision flow.
                    dialog = open_edit(name + '-edited'); security(dialog, 'TLS'); select_certificate(dialog)
                    save(dialog, identity)
                    self.assertEqual(editor(identity)['certificate_id'], certificate_id)
                # VMess already has validated API/export/renewal support. Its actual
                # create/edit form must expose the same managed TLS selector.
                def vmess_draft():
                    page.get_by_role('button', name='添加节点', exact=True).click()
                    dialog = page.get_by_role('dialog')
                    dialog.get_by_text('sing-box', exact=True).click()
                    dialog.locator('.el-form-item').filter(has_text=re.compile(r'^协议')).locator('.el-select').click()
                    page.get_by_role('option', name='VMess', exact=True).click()
                    security(dialog, 'TLS')
                    managed = dialog.locator('.el-form-item').filter(has_text='托管证书')
                    expect(managed).to_be_visible()
                    dialog.get_by_placeholder('例如：US-01').fill('managed-vmess')
                    dialog.get_by_role('spinbutton').fill(str(unused_port()))
                    select_certificate(dialog)
                    return dialog
                dialog = vmess_draft()
                dialog.get_by_role('button', name='取消', exact=True).click()
                self.assertEqual(len(context.request.get(base + '/api/inbounds').json()), len(identities))
                dialog = vmess_draft()
                vmess_id = save(dialog); identities.append(vmess_id)
                with sqlite3.connect(root / 'data/v-ui.db') as db:
                    created_credentials[vmess_id] = db.execute('SELECT settings FROM inbounds WHERE id=?', (vmess_id,)).fetchone()[0]
                vmess_uuid = json.loads(created_credentials[vmess_id])['users'][0]['uuid']
                state = editor(vmess_id)
                self.assertEqual(state['protocol'], 'vmess')
                self.assertEqual(state['certificate_id'], certificate_id)
                self.assertEqual(state['profile']['security'], 'tls')
                self.assertNotIn(vmess_uuid, json.dumps(state))
                dialog = open_edit('managed-vmess')
                expect(dialog.locator('.el-form-item').filter(has_text='托管证书')).to_contain_text('vpn.example.test')
                expect(dialog.locator('.el-form-item').filter(has_text=re.compile(r'^协议')).locator('.el-select__wrapper')).to_have_class(re.compile(r'is-disabled'))
                self.assertNotIn(vmess_uuid, dialog.inner_text())
                dialog.get_by_placeholder('例如：US-01').fill('cancelled-vmess')
                dialog.get_by_role('button', name='取消', exact=True).click()
                self.assertEqual(editor(vmess_id)['remark'], 'managed-vmess')
                page.reload(); page.get_by_text('入站节点', exact=True).first.click()
                dialog = open_edit('managed-vmess')
                expect(dialog.locator('.el-form-item').filter(has_text='托管证书')).to_contain_text('vpn.example.test')
                dialog.get_by_placeholder('例如：US-01').fill('managed-vmess-edited')
                save(dialog, vmess_id)
                page.reload(); page.get_by_text('入站节点', exact=True).first.click()
                dialog = open_edit('managed-vmess-edited')
                expect(dialog.locator('.el-form-item').filter(has_text='托管证书')).to_contain_text('vpn.example.test')
                dialog.get_by_role('button', name='取消', exact=True).click()
                with sqlite3.connect(root / 'data/v-ui.db') as db:
                    self.assertEqual(db.execute('SELECT settings FROM inbounds WHERE id=?', (vmess_id,)).fetchone()[0], created_credentials[vmess_id])
                # VLESS WS uses the same visual editor and managed certificate.
                # Its optional Host is client metadata, never a server Host gate.
                def ws_draft():
                    page.get_by_role('button', name='添加节点', exact=True).click()
                    dialog = page.get_by_role('dialog')
                    dialog.get_by_text('sing-box', exact=True).click()
                    security(dialog, 'TLS')
                    dialog.locator('.el-form-item').filter(has_text='传输方式').locator('.el-select').click()
                    page.get_by_role('option', name='WebSocket', exact=True).click()
                    dialog.get_by_placeholder('例如：US-01').fill('managed-vless-ws')
                    dialog.get_by_role('spinbutton').fill(str(unused_port()))
                    dialog.get_by_placeholder('/', exact=True).fill('/vless-ws')
                    dialog.get_by_placeholder('可选，例如 cdn.example.com').fill('cdn.example.test')
                    expect(dialog).to_contain_text('Host 仅为客户端路由参数')
                    expect(dialog).to_contain_text('sing-box 不校验请求 Host')
                    select_certificate(dialog)
                    return dialog
                dialog = ws_draft()
                dialog.get_by_role('button', name='取消', exact=True).click()
                self.assertEqual(len(context.request.get(base + '/api/inbounds').json()), len(identities))
                dialog = ws_draft()
                ws_id = save(dialog); identities.append(ws_id)
                with sqlite3.connect(root / 'data/v-ui.db') as db:
                    created_credentials[ws_id] = db.execute('SELECT settings FROM inbounds WHERE id=?', (ws_id,)).fetchone()[0]
                    initial_ws_stream = json.loads(db.execute('SELECT stream_settings FROM inbounds WHERE id=?', (ws_id,)).fetchone()[0])
                ws_uuid = json.loads(created_credentials[ws_id])['users'][0]['uuid']
                self.assertEqual(initial_ws_stream['transport'], {'type': 'ws', 'path': '/vless-ws', 'headers': {'Host': 'cdn.example.test'}})
                state = editor(ws_id)
                self.assertEqual(state['protocol'], 'vless')
                self.assertTrue(state['credentials']['has_uuid'])
                self.assertEqual(state['certificate_id'], certificate_id)
                self.assertEqual(state['profile']['transport'], 'ws')
                self.assertEqual(state['profile']['path'], '/vless-ws')
                self.assertEqual(state['profile']['host'], 'cdn.example.test')
                self.assertEqual(state['profile']['server_name'], 'vpn.example.test')
                self.assertFalse(state['profile']['skip_cert_verify'])
                self.assertNotIn(ws_uuid, json.dumps(state))

                def check_ws_prefill(dialog, path, host):
                    expect(dialog.locator('.el-form-item').filter(has_text='传输方式')).to_contain_text('WebSocket')
                    expect(dialog.get_by_placeholder('/', exact=True)).to_have_value(path)
                    expect(dialog.get_by_placeholder('可选，例如 cdn.example.com')).to_have_value(host)
                    expect(dialog.get_by_placeholder('example.com', exact=True)).to_have_value('vpn.example.test')
                    expect(dialog.locator('.el-form-item').filter(has_text='托管证书')).to_contain_text('vpn.example.test')
                    expect(dialog).to_contain_text('已有凭据保留在服务器')
                    expect(dialog.locator('.el-form-item').filter(has_text=re.compile(r'^协议')).locator('.el-select__wrapper')).to_have_class(re.compile(r'is-disabled'))
                    self.assertNotIn(ws_uuid, dialog.inner_text())
                    self.assertNotIn(ws_uuid, str(dialog.locator('input').evaluate_all('(inputs) => inputs.map(input => input.value)')))

                dialog = open_edit('managed-vless-ws')
                check_ws_prefill(dialog, '/vless-ws', 'cdn.example.test')
                # Rejected WS parameters must not alter the saved profile or binding.
                for path, host in (('/vless-ws?ed=1', 'cdn.example.test'),
                                   ('/vless-ws', 'cdn.example.test:443')):
                    dialog.get_by_placeholder('/', exact=True).fill(path)
                    dialog.get_by_placeholder('可选，例如 cdn.example.com').fill(host)
                    with page.expect_response(lambda reply: reply.url.endswith(f'/api/inbounds/{ws_id}') and reply.request.method == 'PUT') as rejected:
                        dialog.get_by_role('button', name='保存并应用', exact=True).click()
                    self.assertEqual(rejected.value.status, 422, rejected.value.text())
                    expect(dialog).to_be_visible()
                    self.assertEqual(editor(ws_id), state)
                dialog.get_by_placeholder('/', exact=True).fill('/cancelled-ws')
                dialog.get_by_placeholder('可选，例如 cdn.example.com').fill('cancelled.example.test')
                dialog.get_by_role('button', name='取消', exact=True).click()
                self.assertEqual(editor(ws_id), state)
                page.reload(); page.get_by_text('入站节点', exact=True).first.click()
                dialog = open_edit('managed-vless-ws')
                check_ws_prefill(dialog, '/vless-ws', 'cdn.example.test')
                dialog.get_by_placeholder('例如：US-01').fill('managed-vless-ws-edited')
                dialog.get_by_placeholder('/', exact=True).fill('/vless-ws-edited')
                dialog.get_by_placeholder('可选，例如 cdn.example.com').fill('cdn2.example.test')
                save(dialog, ws_id)
                page.reload(); page.get_by_text('入站节点', exact=True).first.click()
                dialog = open_edit('managed-vless-ws-edited')
                check_ws_prefill(dialog, '/vless-ws-edited', 'cdn2.example.test')
                dialog.get_by_role('button', name='取消', exact=True).click()
                self.assertEqual(editor(ws_id)['certificate_id'], certificate_id)
                self.assertNotIn(ws_uuid, json.dumps(editor(ws_id)))
                with sqlite3.connect(root / 'data/v-ui.db') as db:
                    settings, ws_stream_before = db.execute('SELECT settings,stream_settings FROM inbounds WHERE id=?', (ws_id,)).fetchone()
                self.assertEqual(settings, created_credentials[ws_id])
                self.assertEqual(json.loads(ws_stream_before)['transport'], {'type': 'ws', 'path': '/vless-ws-edited', 'headers': {'Host': 'cdn2.example.test'}})
                runtime = root / 'data/runtime/sing-box'
                revision = json.loads((runtime / 'state.json').read_text())['revision']
                applied = json.loads((runtime / (revision + '.json')).read_text())
                ws_server = next(item for item in applied['inbounds'] if item.get('users', [{}])[0].get('uuid') == ws_uuid)
                self.assertEqual(ws_server['transport'], {'type': 'ws', 'path': '/vless-ws-edited'})
                # gRPC Lite uses the same managed TLS lifecycle with literal
                # service names, preserved HTTP/2 ALPN and no Host control.
                def grpc_draft():
                    page.get_by_role('button', name='添加节点', exact=True).click()
                    dialog = page.get_by_role('dialog')
                    dialog.get_by_text('sing-box', exact=True).click()
                    security(dialog, 'TLS')
                    dialog.locator('.el-form-item').filter(has_text='传输方式').locator('.el-select').click()
                    page.get_by_role('option', name='gRPC', exact=True).click()
                    dialog.get_by_placeholder('例如：US-01').fill('managed-vless-grpc')
                    dialog.get_by_role('spinbutton').fill(str(unused_port()))
                    dialog.get_by_placeholder('TunService').fill('VUI.Service_1')
                    expect(dialog).to_contain_text('VLESS gRPC Lite 验证范围')
                    expect(dialog).to_contain_text('HTTP/2')
                    expect(dialog).to_contain_text('1–128')
                    expect(dialog).to_contain_text('不支持自定义 authority / Host')
                    expect(dialog.get_by_placeholder('可选，例如 cdn.example.com')).to_have_count(0)
                    select_certificate(dialog)
                    return dialog
                dialog = grpc_draft()
                dialog.get_by_role('button', name='取消', exact=True).click()
                self.assertEqual(len(context.request.get(base + '/api/inbounds').json()), len(identities))
                dialog = grpc_draft()
                grpc_id = save(dialog); identities.append(grpc_id)
                with sqlite3.connect(root / 'data/v-ui.db') as db:
                    created_credentials[grpc_id] = db.execute('SELECT settings FROM inbounds WHERE id=?', (grpc_id,)).fetchone()[0]
                    initial_grpc_stream = json.loads(db.execute('SELECT stream_settings FROM inbounds WHERE id=?', (grpc_id,)).fetchone()[0])
                    # ALPN is not an editable text control; a supported imported
                    # h2 setting must survive all subsequent visual updates.
                    initial_grpc_stream['tls']['alpn'] = ['h2']
                    db.execute('UPDATE inbounds SET stream_settings=? WHERE id=?', (json.dumps(initial_grpc_stream), grpc_id))
                grpc_uuid = json.loads(created_credentials[grpc_id])['users'][0]['uuid']
                self.assertEqual(initial_grpc_stream['transport'], {'type': 'grpc', 'service_name': 'VUI.Service_1'})
                state = editor(grpc_id)
                self.assertEqual(state['certificate_id'], certificate_id)
                self.assertEqual(state['profile']['transport'], 'grpc')
                self.assertEqual(state['profile']['service_name'], 'VUI.Service_1')
                self.assertEqual(state['profile']['server_name'], 'vpn.example.test')
                self.assertFalse(state['profile']['skip_cert_verify'])
                self.assertNotIn(grpc_uuid, json.dumps(state))

                def check_grpc_prefill(dialog, service):
                    expect(dialog.locator('.el-form-item').filter(has_text='传输方式')).to_contain_text('gRPC')
                    expect(dialog.get_by_placeholder('TunService')).to_have_value(service)
                    expect(dialog.get_by_placeholder('可选，例如 cdn.example.com')).to_have_count(0)
                    expect(dialog.get_by_placeholder('example.com', exact=True)).to_have_value('vpn.example.test')
                    expect(dialog.locator('.el-form-item').filter(has_text='托管证书')).to_contain_text('vpn.example.test')
                    expect(dialog).to_contain_text('已有凭据保留在服务器')
                    expect(dialog.locator('.el-form-item').filter(has_text=re.compile(r'^协议')).locator('.el-select__wrapper')).to_have_class(re.compile(r'is-disabled'))
                    self.assertNotIn(grpc_uuid, dialog.inner_text())
                    self.assertNotIn(grpc_uuid, str(dialog.locator('input').evaluate_all('(inputs) => inputs.map(input => input.value)')))

                dialog = open_edit('managed-vless-grpc')
                check_grpc_prefill(dialog, 'VUI.Service_1')
                for service in ('', '/service', ' leading', 'trailing ', 'service?x=1', 'x' * 129):
                    dialog.get_by_placeholder('TunService').fill(service)
                    with page.expect_response(lambda reply: reply.url.endswith(f'/api/inbounds/{grpc_id}') and reply.request.method == 'PUT') as rejected:
                        dialog.get_by_role('button', name='保存并应用', exact=True).click()
                    self.assertEqual(rejected.value.status, 422, rejected.value.text())
                    expect(dialog).to_be_visible()
                    self.assertEqual(editor(grpc_id), state)
                dialog.get_by_placeholder('TunService').fill('Cancelled.Service')
                dialog.get_by_role('button', name='取消', exact=True).click()
                self.assertEqual(editor(grpc_id), state)
                page.reload(); page.get_by_text('入站节点', exact=True).first.click()
                dialog = open_edit('managed-vless-grpc')
                check_grpc_prefill(dialog, 'VUI.Service_1')
                dialog.get_by_placeholder('例如：US-01').fill('managed-vless-grpc-edited')
                dialog.get_by_placeholder('TunService').fill('VUI.Edited-2')
                save(dialog, grpc_id)
                page.reload(); page.get_by_text('入站节点', exact=True).first.click()
                dialog = open_edit('managed-vless-grpc-edited')
                check_grpc_prefill(dialog, 'VUI.Edited-2')
                dialog.get_by_role('button', name='取消', exact=True).click()
                self.assertEqual(editor(grpc_id)['certificate_id'], certificate_id)
                self.assertNotIn(grpc_uuid, json.dumps(editor(grpc_id)))
                with sqlite3.connect(root / 'data/v-ui.db') as db:
                    settings, grpc_stream_before = db.execute('SELECT settings,stream_settings FROM inbounds WHERE id=?', (grpc_id,)).fetchone()
                self.assertEqual(settings, created_credentials[grpc_id])
                self.assertEqual(json.loads(grpc_stream_before)['transport'], {'type': 'grpc', 'service_name': 'VUI.Edited-2'})
                self.assertEqual(json.loads(grpc_stream_before)['tls']['alpn'], ['h2'])
                revision = json.loads((runtime / 'state.json').read_text())['revision']
                applied = json.loads((runtime / (revision + '.json')).read_text())
                grpc_server = next(item for item in applied['inbounds'] if item.get('users', [{}])[0].get('uuid') == grpc_uuid)
                self.assertEqual(grpc_server['transport'], {'type': 'grpc', 'service_name': 'VUI.Edited-2'})
                self.assertEqual(grpc_server['tls']['alpn'], ['h2'])
                # Hysteria2 has a UDP listener with native QUIC defaults. Auth
                # passwords may be supplied, but are never returned for editing.
                hy2_password = 'Synthetic-browser-HY2:p@ss/word?+#%'
                def hy2_draft():
                    page.get_by_role('button', name='添加节点', exact=True).click()
                    dialog = page.get_by_role('dialog')
                    dialog.get_by_text('sing-box', exact=True).click()
                    dialog.locator('.el-form-item').filter(has_text=re.compile(r'^协议')).locator('.el-select').click()
                    page.get_by_role('option', name='Hysteria2', exact=True).click()
                    dialog.get_by_placeholder('例如：US-01').fill('managed-hysteria2')
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                        udp.bind(('127.0.0.1', 0))
                        hy2_port = udp.getsockname()[1]
                    dialog.locator('.el-form-item').filter(has_text=re.compile(r'^端口')).get_by_role('spinbutton').fill(str(hy2_port))
                    expect(dialog).to_contain_text('QUIC')
                    expect(dialog).to_contain_text('UDP ' + str(hy2_port))
                    expect(dialog).to_contain_text('HTTP/TCP')
                    expect(dialog).to_contain_text('未验收草稿')
                    expect(dialog.locator('.el-form-item').filter(has_text='传输方式')).to_have_count(0)
                    for label in ('上传带宽 Mbps', '下载带宽 Mbps'):
                        expect(dialog.locator('.el-form-item').filter(has_text=label).get_by_role('spinbutton')).to_have_value('')
                    auth = dialog.locator('.el-form-item').filter(has_text='Hysteria2 Password').locator('input')
                    expect(auth).to_have_attribute('type', 'password')
                    auth.fill(hy2_password)
                    select_certificate(dialog)
                    return dialog
                dialog = hy2_draft()
                dialog.get_by_role('button', name='取消', exact=True).click()
                self.assertEqual(len(context.request.get(base + '/api/inbounds').json()), len(identities))
                dialog = hy2_draft()
                hy2_id = save(dialog); identities.append(hy2_id)
                with sqlite3.connect(root / 'data/v-ui.db') as db:
                    created_credentials[hy2_id] = db.execute('SELECT settings FROM inbounds WHERE id=?', (hy2_id,)).fetchone()[0]
                self.assertEqual(json.loads(created_credentials[hy2_id]), {'users': [{'password': hy2_password}]})

                def check_hy2_state():
                    state = editor(hy2_id)
                    self.assertEqual(state['protocol'], 'hysteria2')
                    self.assertEqual(state['certificate_id'], certificate_id)
                    self.assertEqual(state['profile']['security'], 'tls')
                    self.assertEqual(state['profile']['transport'], 'quic')
                    self.assertEqual(state['profile']['server_name'], 'vpn.example.test')
                    self.assertEqual(state['profile']['client_fingerprint'], '')
                    self.assertIsNone(state['profile']['up_mbps'])
                    self.assertIsNone(state['profile']['down_mbps'])
                    self.assertFalse(state['profile']['skip_cert_verify'])
                    self.assertTrue(state['profile']['hysteria2_password_set'])
                    self.assertFalse(state['profile'].get('hysteria2_password'))
                    self.assertNotIn(hy2_password, json.dumps(state))
                    return state

                def check_hy2_prefill(dialog):
                    expect(dialog.locator('.el-form-item').filter(has_text='托管证书')).to_contain_text('vpn.example.test')
                    expect(dialog.get_by_placeholder('example.com', exact=True)).to_have_value('vpn.example.test')
                    expect(dialog).to_contain_text('已有凭据保留在服务器')
                    expect(dialog.locator('.el-form-item').filter(has_text=re.compile(r'^协议')).locator('.el-select__wrapper')).to_have_class(re.compile(r'is-disabled'))
                    auth = dialog.locator('.el-form-item').filter(has_text='Hysteria2 Password').locator('input')
                    expect(auth).to_have_attribute('type', 'password')
                    expect(auth).to_have_value('')
                    self.assertNotIn(hy2_password, dialog.inner_text())
                    self.assertNotIn(hy2_password, str(dialog.locator('input').evaluate_all('(inputs) => inputs.map(input => input.value)')))
                    return auth

                hy2_state = check_hy2_state()
                dialog = open_edit('managed-hysteria2')
                check_hy2_prefill(dialog).fill('Cancelled-HY2-password')
                dialog.get_by_placeholder('例如：US-01').fill('cancelled-hysteria2')
                dialog.get_by_role('button', name='取消', exact=True).click()
                self.assertEqual(check_hy2_state(), hy2_state)
                page.reload(); page.get_by_text('入站节点', exact=True).first.click()
                dialog = open_edit('managed-hysteria2')
                check_hy2_prefill(dialog)
                dialog.get_by_placeholder('例如：US-01').fill('managed-hysteria2-edited')
                save(dialog, hy2_id)
                check_hy2_state()
                page.reload(); page.get_by_text('入站节点', exact=True).first.click()
                dialog = open_edit('managed-hysteria2-edited')
                check_hy2_prefill(dialog)
                dialog.get_by_role('button', name='取消', exact=True).click()
                with sqlite3.connect(root / 'data/v-ui.db') as db:
                    settings, hy2_stream_before = db.execute('SELECT settings,stream_settings FROM inbounds WHERE id=?', (hy2_id,)).fetchone()
                self.assertEqual(settings, created_credentials[hy2_id])
                self.assertNotIn('transport', json.loads(hy2_stream_before))
                revision = json.loads((runtime / 'state.json').read_text())['revision']
                applied = json.loads((runtime / (revision + '.json')).read_text())
                hy2_server = next(item for item in applied['inbounds'] if item['type'] == 'hysteria2')
                self.assertEqual(hy2_server['users'], [{'password': hy2_password}])
                self.assertEqual(hy2_server['tls']['server_name'], 'vpn.example.test')
                self.assertTrue(hy2_server['tls']['enabled'])
                for field in ('transport', 'up_mbps', 'down_mbps', 'obfs'):
                    self.assertNotIn(field, hy2_server)
                self.assertNotIn('alpn', hy2_server['tls'])
                self.assertNotIn('utls', hy2_server['tls'])
                # TUIC has a UDP listener with native QUIC defaults. Auth
                # passwords may be supplied, but are never returned for editing.
                tuic_uuid = '11111111-1111-4111-8111-111111111111'
                tuic_password = 'Synthetic-browser-TUIC:p@ss/word?+#%'
                def tuic_draft():
                    page.get_by_role('button', name='添加节点', exact=True).click()
                    dialog = page.get_by_role('dialog')
                    dialog.get_by_text('sing-box', exact=True).click()
                    dialog.locator('.el-form-item').filter(has_text=re.compile(r'^协议')).locator('.el-select').click()
                    page.get_by_role('option', name='TUIC', exact=True).click()
                    dialog.get_by_placeholder('例如：US-01').fill('managed-tuic')
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                        udp.bind(('127.0.0.1', 0))
                        tuic_port = udp.getsockname()[1]
                    dialog.locator('.el-form-item').filter(has_text=re.compile(r'^端口')).get_by_role('spinbutton').fill(str(tuic_port))
                    expect(dialog).to_contain_text('QUIC')
                    expect(dialog).to_contain_text('UDP ' + str(tuic_port))
                    expect(dialog).to_contain_text('HTTP/TCP')
                    expect(dialog).to_contain_text('未验收草稿')
                    expect(dialog.locator('.el-form-item').filter(has_text='传输方式')).to_have_count(0)
                    expect(dialog.locator('.el-form-item').filter(has_text='拥塞控制')).to_contain_text('CUBIC')
                    expect(dialog.locator('.el-form-item').filter(has_text='UDP Relay')).to_contain_text('Native')
                    expect(dialog.get_by_role('checkbox', name=re.compile('启用 0-RTT'))).not_to_be_checked()
                    uid = dialog.locator('.el-form-item').filter(has_text='TUIC UUID').locator('input')
                    expect(uid).to_have_attribute('type', 'password'); uid.fill(tuic_uuid)
                    auth = dialog.locator('.el-form-item').filter(has_text='TUIC Password').locator('input')
                    expect(auth).to_have_attribute('type', 'password')
                    auth.fill(tuic_password)
                    select_certificate(dialog)
                    return dialog
                dialog = tuic_draft()
                dialog.get_by_role('button', name='取消', exact=True).click()
                self.assertEqual(len(context.request.get(base + '/api/inbounds').json()), len(identities))
                dialog = tuic_draft()
                tuic_id = save(dialog); identities.append(tuic_id)
                with sqlite3.connect(root / 'data/v-ui.db') as db:
                    created_credentials[tuic_id] = db.execute('SELECT settings FROM inbounds WHERE id=?', (tuic_id,)).fetchone()[0]
                self.assertEqual(json.loads(created_credentials[tuic_id]), {'users': [{'uuid': tuic_uuid, 'password': tuic_password}]})

                def check_tuic_state():
                    state = editor(tuic_id)
                    self.assertEqual(state['protocol'], 'tuic')
                    self.assertEqual(state['certificate_id'], certificate_id)
                    self.assertEqual(state['profile']['security'], 'tls')
                    self.assertEqual(state['profile']['transport'], 'quic')
                    self.assertEqual(state['profile']['server_name'], 'vpn.example.test')
                    self.assertEqual(state['profile']['client_fingerprint'], '')
                    self.assertEqual(state['profile']['congestion_control'], 'cubic')
                    self.assertEqual(state['profile']['udp_relay_mode'], 'native')
                    self.assertFalse(state['profile']['zero_rtt_handshake'])
                    self.assertTrue(state['profile']['tuic_uuid_set'])
                    self.assertFalse(state['profile'].get('tuic_uuid'))
                    self.assertNotIn(tuic_uuid, json.dumps(state))
                    self.assertFalse(state['profile']['skip_cert_verify'])
                    self.assertTrue(state['profile']['tuic_password_set'])
                    self.assertFalse(state['profile'].get('tuic_password'))
                    self.assertNotIn(tuic_password, json.dumps(state))
                    return state

                def check_tuic_prefill(dialog):
                    expect(dialog.locator('.el-form-item').filter(has_text='托管证书')).to_contain_text('vpn.example.test')
                    expect(dialog.get_by_placeholder('example.com', exact=True)).to_have_value('vpn.example.test')
                    expect(dialog).to_contain_text('已有凭据保留在服务器')
                    expect(dialog.locator('.el-form-item').filter(has_text=re.compile(r'^协议')).locator('.el-select__wrapper')).to_have_class(re.compile(r'is-disabled'))
                    auth = dialog.locator('.el-form-item').filter(has_text='TUIC Password').locator('input')
                    expect(auth).to_have_attribute('type', 'password')
                    expect(auth).to_have_value('')
                    self.assertNotIn(tuic_password, dialog.inner_text())
                    self.assertNotIn(tuic_password, str(dialog.locator('input').evaluate_all('(inputs) => inputs.map(input => input.value)')))
                    uid = dialog.locator('.el-form-item').filter(has_text='TUIC UUID').locator('input')
                    expect(uid).to_have_attribute('type', 'password'); expect(uid).to_have_value('')
                    self.assertNotIn(tuic_uuid, str(dialog.locator('input').evaluate_all('(inputs) => inputs.map(input => input.value)')))
                    return auth

                tuic_state = check_tuic_state()
                dialog = open_edit('managed-tuic')
                check_tuic_prefill(dialog).fill('Cancelled-TUIC-password')
                dialog.get_by_placeholder('例如：US-01').fill('cancelled-tuic')
                dialog.get_by_role('button', name='取消', exact=True).click()
                self.assertEqual(check_tuic_state(), tuic_state)
                page.reload(); page.get_by_text('入站节点', exact=True).first.click()
                dialog = open_edit('managed-tuic')
                check_tuic_prefill(dialog)
                dialog.get_by_placeholder('例如：US-01').fill('managed-tuic-edited')
                save(dialog, tuic_id)
                check_tuic_state()
                page.reload(); page.get_by_text('入站节点', exact=True).first.click()
                dialog = open_edit('managed-tuic-edited')
                check_tuic_prefill(dialog)
                dialog.get_by_role('button', name='取消', exact=True).click()
                with sqlite3.connect(root / 'data/v-ui.db') as db:
                    settings, tuic_stream_before = db.execute('SELECT settings,stream_settings FROM inbounds WHERE id=?', (tuic_id,)).fetchone()
                self.assertEqual(settings, created_credentials[tuic_id])
                self.assertNotIn('transport', json.loads(tuic_stream_before))
                revision = json.loads((runtime / 'state.json').read_text())['revision']
                applied = json.loads((runtime / (revision + '.json')).read_text())
                tuic_server = next(item for item in applied['inbounds'] if item['type'] == 'tuic')
                self.assertEqual(tuic_server['users'], [{'uuid': tuic_uuid, 'password': tuic_password}])
                self.assertEqual(tuic_server['tls']['server_name'], 'vpn.example.test')
                self.assertTrue(tuic_server['tls']['enabled'])
                for field in ('transport', 'up_mbps', 'down_mbps', 'obfs'):
                    self.assertNotIn(field, tuic_server)
                self.assertEqual(tuic_server['tls']['alpn'], ['h3'])
                self.assertFalse(tuic_server.get('zero_rtt_handshake', False))
                self.assertNotIn('utls', tuic_server['tls'])
                # Qualified REALITY has a handshake reference address, no local
                # certificate binding, and hidden independently editable UUID/ID.
                def reality_draft():
                    page.get_by_role('button', name='添加节点', exact=True).click()
                    dialog = page.get_by_role('dialog'); dialog.get_by_text('sing-box', exact=True).click()
                    security(dialog, 'REALITY')
                    dialog.get_by_placeholder('例如：US-01').fill('qualified-reality')
                    dialog.get_by_role('spinbutton').fill(str(unused_port()))
                    dialog.get_by_placeholder('example.com:443').fill('127.0.0.1:19445')
                    dialog.get_by_placeholder('必填，例如 reference.example.test').fill('reference.example.test')
                    expect(dialog).to_contain_text('握手参考端与应用目标不同')
                    expect(dialog).to_contain_text('不绑定托管证书')
                    expect(dialog.locator('.el-form-item').filter(has_text='托管证书')).not_to_be_visible()
                    expect(dialog.locator('.el-form-item').filter(has_text='Flow')).to_contain_text('XTLS Vision')
                    for label in ('REALITY UUID', 'Short ID'):
                        field = dialog.locator('.el-form-item').filter(has_text=re.compile('^'+label)).locator('input')
                        expect(field).to_have_attribute('type', 'password'); expect(field).to_have_value('')
                    return dialog
                dialog = reality_draft(); dialog.get_by_role('button', name='取消', exact=True).click()
                self.assertEqual(len(context.request.get(base + '/api/inbounds').json()), len(identities))
                dialog = reality_draft(); reality_id = save(dialog); identities.append(reality_id)

                def reality_material():
                    with sqlite3.connect(root / 'data/v-ui.db') as db:
                        settings, stream = db.execute('SELECT settings,stream_settings FROM inbounds WHERE id=?', (reality_id,)).fetchone()
                    return json.loads(settings), json.loads(stream)
                reality_settings, reality_stream = reality_material()
                reality_uuid = reality_settings['users'][0]['uuid']
                reality_private = reality_stream['tls']['reality']['private_key']
                reality_public = reality_stream['_vui']['reality_public_key']
                reality_short = reality_stream['tls']['reality']['short_id'][0]
                self.assertEqual(reality_settings['users'][0]['flow'], 'xtls-rprx-vision')
                self.assertEqual(len(reality_short), 16)

                def check_reality_prefill(dialog):
                    state = editor(reality_id)
                    self.assertIsNone(state['certificate_id'])
                    self.assertEqual(state['profile']['security'], 'reality')
                    self.assertEqual(state['profile']['flow'], 'xtls-rprx-vision')
                    self.assertEqual(state['profile']['client_fingerprint'], 'chrome')
                    self.assertEqual(state['profile']['reality_uuid'], '')
                    self.assertEqual(state['profile']['reality_short_id'], '')
                    self.assertTrue(state['profile']['reality_uuid_set'])
                    self.assertTrue(state['profile']['reality_short_id_set'])
                    self.assertTrue(state['profile']['reality_private_key_set'])
                    expect(dialog.get_by_placeholder('example.com:443')).to_have_value('127.0.0.1:19445')
                    expect(dialog.get_by_placeholder('必填，例如 reference.example.test')).to_have_value('reference.example.test')
                    for label in ('REALITY UUID', 'Short ID'):
                        field = dialog.locator('.el-form-item').filter(has_text=re.compile('^'+label)).locator('input')
                        expect(field).to_have_attribute('type', 'password'); expect(field).to_have_value('')
                    for secret in (reality_uuid, reality_private, reality_short):
                        self.assertNotIn(secret, json.dumps(state))
                        self.assertNotIn(secret, dialog.inner_text())
                        self.assertNotIn(secret, str(dialog.locator('input').evaluate_all('(inputs) => inputs.map(input => input.value)')))
                    return state

                dialog = open_edit('qualified-reality'); check_reality_prefill(dialog)
                dialog.get_by_placeholder('例如：US-01').fill('cancelled-reality')
                dialog.locator('.el-form-item').filter(has_text=re.compile('^Short ID')).locator('input').fill('aaaaaaaaaaaaaaaa')
                dialog.get_by_role('button', name='取消', exact=True).click()
                self.assertEqual(reality_material(), (reality_settings, reality_stream))
                page.reload(); page.get_by_text('入站节点', exact=True).first.click()
                dialog = open_edit('qualified-reality'); check_reality_prefill(dialog)
                dialog.get_by_placeholder('例如：US-01').fill('qualified-reality-edited')
                save(dialog, reality_id)
                self.assertEqual(reality_material(), (reality_settings, reality_stream))
                # A malformed replacement is rejected with desired material intact.
                dialog = open_edit('qualified-reality-edited'); check_reality_prefill(dialog)
                short_input = dialog.locator('.el-form-item').filter(has_text=re.compile('^Short ID')).locator('input')
                short_input.fill('ABC')
                with page.expect_response(lambda reply: reply.url.endswith(f'/api/inbounds/{reality_id}') and reply.request.method == 'PUT') as rejected:
                    dialog.get_by_role('button', name='保存并应用', exact=True).click()
                self.assertEqual(rejected.value.status, 422, rejected.value.text())
                self.assertEqual(reality_material(), (reality_settings, reality_stream))
                short_input.fill(''); dialog.get_by_role('button', name='取消', exact=True).click()
                dialog = open_edit('qualified-reality-edited'); check_reality_prefill(dialog)
                dialog.locator('.el-form-item').filter(has_text='REALITY UUID').locator('input').fill('22222222-2222-4222-8222-222222222222')
                save(dialog, reality_id)
                new_settings, new_stream = reality_material()
                self.assertEqual(new_stream, reality_stream)
                reality_uuid = new_settings['users'][0]['uuid']
                self.assertEqual(reality_uuid, '22222222-2222-4222-8222-222222222222')
                dialog = open_edit('qualified-reality-edited'); check_reality_prefill(dialog)
                dialog.locator('.el-form-item').filter(has_text=re.compile('^Short ID')).locator('input').fill('fedcba9876543210')
                save(dialog, reality_id)
                reality_settings, reality_stream = reality_material()
                self.assertEqual(reality_settings, new_settings)
                reality_short = reality_stream['tls']['reality']['short_id'][0]
                self.assertEqual(reality_short, 'fedcba9876543210')
                self.assertEqual(reality_stream['tls']['reality']['private_key'], reality_private)
                self.assertEqual(reality_stream['_vui']['reality_public_key'], reality_public)
                with sqlite3.connect(root / 'data/v-ui.db') as db:
                    created_credentials[reality_id], reality_stream_before = db.execute('SELECT settings,stream_settings FROM inbounds WHERE id=?', (reality_id,)).fetchone()
                page.reload(); page.get_by_text('入站节点', exact=True).first.click()
                dialog = open_edit('qualified-reality-edited'); check_reality_prefill(dialog)
                dialog.get_by_role('button', name='取消', exact=True).click()
                # The separate certificate-management card must offer both TLS
                # targets and complete its existing normal VMess bind action.
                page.goto(base + '/certificates')
                card = page.locator(f'[data-certificate="{certificate_id}"]')
                choices = card.get_by_label('选择绑定节点')
                expect(choices.locator(f'option[value="{vmess_id}"]')).to_have_text('managed-vmess-edited')
                expect(choices.locator(f'option[value="{ws_id}"]')).to_have_text('managed-vless-ws-edited')
                expect(choices.locator(f'option[value="{grpc_id}"]')).to_have_text('managed-vless-grpc-edited')
                expect(choices.locator(f'option[value="{hy2_id}"]')).to_have_text('managed-hysteria2-edited')
                expect(choices.locator(f'option[value="{tuic_id}"]')).to_have_text('managed-tuic-edited')
                expect(choices.locator(f'option[value="{reality_id}"]')).to_have_count(0)
                choices.select_option(str(vmess_id))
                with page.expect_response(lambda reply: reply.url.endswith(f'/api/certificates/{certificate_id}/bind-inbound') and reply.request.method == 'POST') as bound:
                    card.get_by_role('button', name='绑定节点', exact=True).click()
                self.assertEqual(bound.value.status, 200, bound.value.text())
                self.assertEqual(editor(vmess_id)['certificate_id'], certificate_id)
                headers = {'Origin': base, 'X-VUI-Request': '1'}
                response = context.request.post(base + '/api/subscriptions', headers=headers,
                    data={'label': 'editor-export', 'server': 'vpn.example.test', 'inbound_ids': identities, 'formats': ['mihomo.yaml', 'raw', 'sing-box.json']})
                self.assertEqual(response.status, 201, response.text())
                url = base + response.json()['paths']['mihomo.yaml']
                exported = context.request.get(url)
                self.assertEqual(exported.status, 200, exported.text())
                self.assertIn('managed-none-edited', exported.text())
                self.assertIn('managed-reality-edited', exported.text())
                self.assertNotIn('PRIVATE KEY', exported.text())
                self.assertNotIn(str(root), exported.text())
                self.assertIn('managed-vmess-edited', exported.text())
                self.assertIn(vmess_uuid, exported.text())
                ws_proxy = next(item for item in yaml.safe_load(exported.text())['proxies'] if item['name'] == 'managed-vless-ws-edited')
                self.assertEqual(ws_proxy['type'], 'vless')
                self.assertEqual(ws_proxy['uuid'], ws_uuid)
                self.assertEqual(ws_proxy['network'], 'ws')
                self.assertEqual(ws_proxy['ws-opts'], {'path': '/vless-ws-edited', 'headers': {'Host': 'cdn2.example.test'}})
                self.assertTrue(ws_proxy['tls'])
                self.assertEqual(ws_proxy['servername'], 'vpn.example.test')
                self.assertFalse(ws_proxy['skip-cert-verify'])
                grpc_proxy = next(item for item in yaml.safe_load(exported.text())['proxies'] if item['name'] == 'managed-vless-grpc-edited')
                self.assertEqual(grpc_proxy['type'], 'vless')
                self.assertEqual(grpc_proxy['uuid'], grpc_uuid)
                self.assertEqual(grpc_proxy['network'], 'grpc')
                self.assertEqual(grpc_proxy['grpc-opts'], {'grpc-service-name': 'VUI.Edited-2'})
                self.assertEqual(grpc_proxy['alpn'], ['h2'])
                self.assertFalse(grpc_proxy['udp'])
                self.assertTrue(grpc_proxy['tls'])
                self.assertEqual(grpc_proxy['servername'], 'vpn.example.test')
                self.assertFalse(grpc_proxy['skip-cert-verify'])
                hy2_proxy = next(item for item in yaml.safe_load(exported.text())['proxies'] if item['name'] == 'managed-hysteria2-edited')
                self.assertEqual(hy2_proxy['type'], 'hysteria2')
                self.assertEqual(hy2_proxy['password'], hy2_password)
                self.assertEqual(hy2_proxy['sni'], 'vpn.example.test')
                self.assertFalse(hy2_proxy['skip-cert-verify'])
                self.assertFalse(hy2_proxy['udp'])
                for field in ('up', 'down', 'obfs', 'obfs-password', 'alpn', 'client-fingerprint'):
                    self.assertNotIn(field, hy2_proxy)
                tuic_proxy = next(item for item in yaml.safe_load(exported.text())['proxies'] if item['name'] == 'managed-tuic-edited')
                self.assertEqual(tuic_proxy, {'name': 'managed-tuic-edited', 'type': 'tuic',
                    'server': 'vpn.example.test', 'port': tuic_server['listen_port'], 'uuid': tuic_uuid,
                    'password': tuic_password, 'sni': 'vpn.example.test', 'skip-cert-verify': False,
                    'alpn': ['h3'], 'reduce-rtt': False})
                singbox = context.request.get(base + response.json()['paths']['sing-box.json'])
                self.assertEqual(singbox.status, 200, singbox.text())
                vmess_client = next(item for item in singbox.json()['outbounds'] if item.get('type') == 'vmess')
                self.assertEqual(vmess_client['uuid'], vmess_uuid)
                self.assertEqual(vmess_client['tls']['server_name'], 'vpn.example.test')
                self.assertFalse(vmess_client['tls'].get('insecure', False))
                self.assertNotIn(str(root), singbox.text())
                ws_client = next(item for item in singbox.json()['outbounds'] if item.get('tag') == 'managed-vless-ws-edited')
                self.assertEqual(ws_client['uuid'], ws_uuid)
                self.assertEqual(ws_client['transport'], {'type': 'ws', 'path': '/vless-ws-edited', 'headers': {'Host': 'cdn2.example.test'}})
                self.assertTrue(ws_client['tls']['enabled'])
                self.assertEqual(ws_client['tls']['server_name'], 'vpn.example.test')
                self.assertFalse(ws_client['tls'].get('insecure', False))
                grpc_client = next(item for item in singbox.json()['outbounds'] if item.get('tag') == 'managed-vless-grpc-edited')
                self.assertEqual(grpc_client['uuid'], grpc_uuid)
                self.assertEqual(grpc_client['network'], 'tcp')
                self.assertEqual(grpc_client['transport'], {'type': 'grpc', 'service_name': 'VUI.Edited-2'})
                self.assertTrue(grpc_client['tls']['enabled'])
                self.assertEqual(grpc_client['tls']['server_name'], 'vpn.example.test')
                self.assertEqual(grpc_client['tls']['alpn'], ['h2'])
                self.assertFalse(grpc_client['tls'].get('insecure', False))
                hy2_client = next(item for item in singbox.json()['outbounds'] if item.get('tag') == 'managed-hysteria2-edited')
                self.assertEqual(hy2_client['type'], 'hysteria2')
                self.assertEqual(hy2_client['password'], hy2_password)
                self.assertEqual(hy2_client['network'], 'tcp')
                self.assertTrue(hy2_client['tls']['enabled'])
                self.assertEqual(hy2_client['tls']['server_name'], 'vpn.example.test')
                self.assertFalse(hy2_client['tls'].get('insecure', False))
                for field in ('transport', 'up_mbps', 'down_mbps', 'obfs'):
                    self.assertNotIn(field, hy2_client)
                self.assertNotIn('alpn', hy2_client['tls'])
                self.assertNotIn('utls', hy2_client['tls'])
                tuic_client = next(item for item in singbox.json()['outbounds'] if item.get('tag') == 'managed-tuic-edited')
                self.assertEqual(tuic_client['type'], 'tuic')
                self.assertEqual(tuic_client['uuid'], tuic_uuid)
                self.assertEqual(tuic_client['password'], tuic_password)
                self.assertEqual(tuic_client['network'], 'tcp')
                self.assertFalse(tuic_client['zero_rtt_handshake'])
                self.assertEqual(tuic_client['tls'], {'enabled': True, 'server_name': 'vpn.example.test', 'alpn': ['h3']})
                raw = context.request.get(base + response.json()['paths']['raw'])
                self.assertEqual(raw.status, 200, raw.text())
                links = base64.b64decode(raw.text()).decode().splitlines()
                vmess_link = next(item for item in links if item.startswith('vmess://'))
                vmess_payload = json.loads(base64.b64decode(vmess_link[len('vmess://'):]).decode())
                self.assertEqual(vmess_payload['id'], vmess_uuid)
                self.assertEqual(vmess_payload['tls'], 'tls')
                self.assertEqual(vmess_payload['sni'], 'vpn.example.test')
                self.assertNotIn(str(root), json.dumps(vmess_payload))
                ws_link = urlsplit(next(item for item in links if item.startswith('vless://' + ws_uuid + '@')))
                ws_query = parse_qs(ws_link.query, strict_parsing=True)
                self.assertEqual(ws_query['security'], ['tls'])
                self.assertEqual(ws_query['type'], ['ws'])
                self.assertEqual(ws_query['sni'], ['vpn.example.test'])
                self.assertEqual(ws_query['path'], ['/vless-ws-edited'])
                self.assertEqual(ws_query['host'], ['cdn2.example.test'])
                self.assertNotIn('ed', ws_query)
                self.assertNotIn('allowInsecure', ws_query)
                grpc_link = urlsplit(next(item for item in links if item.startswith('vless://' + grpc_uuid + '@')))
                grpc_query = parse_qs(grpc_link.query, strict_parsing=True)
                self.assertEqual(grpc_query['security'], ['tls'])
                self.assertEqual(grpc_query['type'], ['grpc'])
                self.assertEqual(grpc_query['sni'], ['vpn.example.test'])
                self.assertEqual(grpc_query['serviceName'], ['VUI.Edited-2'])
                self.assertEqual(grpc_query['alpn'], ['h2'])
                for field in ('path', 'host', 'authority', 'allowInsecure', 'packetEncoding'):
                    self.assertNotIn(field, grpc_query)
                hy2_link = urlsplit(next(item for item in links if item.startswith('hysteria2://')))
                self.assertEqual(unquote(hy2_link.username), hy2_password)
                self.assertIsNone(hy2_link.password)
                hy2_query = parse_qs(hy2_link.query, strict_parsing=True)
                self.assertEqual(hy2_query, {'sni': ['vpn.example.test'], 'insecure': ['0']})
                tuic_link = urlsplit(next(item for item in links if item.startswith('tuic://')))
                self.assertEqual(unquote(tuic_link.username), tuic_uuid)
                self.assertEqual(unquote(tuic_link.password), tuic_password)
                self.assertEqual(parse_qs(tuic_link.query, strict_parsing=True), {'sni': ['vpn.example.test'], 'alpn': ['h3']})
                reality_proxy = next(item for item in yaml.safe_load(exported.text())['proxies'] if item['name'] == 'qualified-reality-edited')
                self.assertEqual(reality_proxy['uuid'], reality_uuid)
                self.assertEqual(reality_proxy['flow'], 'xtls-rprx-vision')
                self.assertEqual(reality_proxy['client-fingerprint'], 'chrome')
                self.assertEqual(reality_proxy['reality-opts'], {'public-key': reality_public, 'short-id': reality_short})
                self.assertFalse(reality_proxy['udp']); self.assertNotIn('alpn', reality_proxy)
                reality_client = next(item for item in singbox.json()['outbounds'] if item.get('tag') == 'qualified-reality-edited')
                self.assertEqual(reality_client['uuid'], reality_uuid)
                self.assertEqual(reality_client['flow'], 'xtls-rprx-vision')
                self.assertEqual(reality_client['network'], 'tcp')
                self.assertEqual(reality_client['tls']['reality'], {'enabled': True, 'public_key': reality_public, 'short_id': reality_short})
                self.assertEqual(reality_client['tls']['utls'], {'enabled': True, 'fingerprint': 'chrome'})
                reality_link = urlsplit(next(item for item in links if item.startswith('vless://' + reality_uuid + '@')))
                self.assertEqual(parse_qs(reality_link.query, strict_parsing=True), {'security': ['reality'], 'type': ['tcp'],
                    'encryption': ['none'], 'flow': ['xtls-rprx-vision'], 'sni': ['reference.example.test'],
                    'fp': ['chrome'], 'pbk': [reality_public], 'sid': [reality_short]})
                for text in (exported.text(), singbox.text(), '\n'.join(links)):
                    for server_only in (str(root), 'certificate_path', 'key_path', 'PRIVATE KEY', '_vui', reality_private, '127.0.0.1:19445'):
                        self.assertNotIn(server_only, text)

                with sqlite3.connect(root / 'data/v-ui.db') as db:
                    secrets_before = db.execute('SELECT id,settings FROM inbounds ORDER BY id').fetchall()
                    self.assertEqual(dict(secrets_before), created_credentials)
                # Explicit exports above carry client credentials; normal page
                # traffic must not carry raw server documents or their secrets.
                for endpoint in ('/api/inbounds', '/api/singbox/inbounds', '/api/xray/inbounds'):
                    ordinary = context.request.get(base + endpoint)
                    self.assertEqual(ordinary.status, 200, ordinary.text())
                    for node in ordinary.json():
                        self.assertNotIn('settings', node)
                        self.assertNotIn('stream_settings', node)
                        self.assertIs(type(node['managed_certificate_eligible']), bool)
                    self.assertNotIn(str(root), ordinary.text())
                    for secret in (vmess_uuid, ws_uuid, grpc_uuid, hy2_password, tuic_uuid, tuic_password, reality_uuid, reality_private, reality_short):
                        self.assertNotIn(secret, ordinary.text())
                # Stop this page's polling before the fixture restarts on a new
                # random origin; keep the strict network guard on the new page.
                page.close()
                stop()
                archive = root / 'editor-backup.zip'; digest = release_tools.backup(root, archive)
                with sqlite3.connect(root / 'data/v-ui.db') as db:
                    db.execute("UPDATE inbounds SET remark='after-backup'")
                    db.execute('DELETE FROM certificate_bindings')
                    for identity in (ws_id, grpc_id, hy2_id, tuic_id, reality_id):
                        db.execute("UPDATE inbounds SET settings='{}',stream_settings='{}' WHERE id=?", (identity,))
                release_tools.restore(root, archive, digest)
                base = start(); page = new_page(); login()
                for identity in identities:
                    state = editor(identity)
                    self.assertEqual(state['certificate_id'], None if identity == reality_id else certificate_id)
                    self.assertEqual(state['profile']['security'], 'reality' if identity == reality_id else 'tls')
                    self.assertTrue(state['remark'].endswith('-edited'))
                    dialog = open_edit(state['remark'])
                    if identity != reality_id: expect(dialog).to_contain_text('vpn.example.test')
                    if identity == ws_id:
                        self.assertEqual(state['profile']['transport'], 'ws')
                        self.assertEqual(state['profile']['path'], '/vless-ws-edited')
                        self.assertEqual(state['profile']['host'], 'cdn2.example.test')
                        self.assertEqual(state['profile']['server_name'], 'vpn.example.test')
                        self.assertNotIn(ws_uuid, json.dumps(state))
                        check_ws_prefill(dialog, '/vless-ws-edited', 'cdn2.example.test')
                    if identity == grpc_id:
                        self.assertEqual(state['profile']['transport'], 'grpc')
                        self.assertEqual(state['profile']['service_name'], 'VUI.Edited-2')
                        self.assertEqual(state['profile']['server_name'], 'vpn.example.test')
                        self.assertNotIn(grpc_uuid, json.dumps(state))
                        check_grpc_prefill(dialog, 'VUI.Edited-2')
                    if identity == hy2_id:
                        check_hy2_state()
                        check_hy2_prefill(dialog)
                    if identity == tuic_id:
                        check_tuic_state()
                        check_tuic_prefill(dialog)
                    if identity == reality_id:
                        check_reality_prefill(dialog)
                    dialog.get_by_role('button', name='取消', exact=True).click()
                with sqlite3.connect(root / 'data/v-ui.db') as db:
                    self.assertEqual(db.execute('SELECT id,settings FROM inbounds ORDER BY id').fetchall(), secrets_before)
                    self.assertEqual(db.execute('SELECT stream_settings FROM inbounds WHERE id=?', (ws_id,)).fetchone()[0], ws_stream_before)
                    self.assertEqual(db.execute('SELECT stream_settings FROM inbounds WHERE id=?', (grpc_id,)).fetchone()[0], grpc_stream_before)
                    self.assertEqual(db.execute('SELECT stream_settings FROM inbounds WHERE id=?', (hy2_id,)).fetchone()[0], hy2_stream_before)
                    self.assertEqual(db.execute('SELECT stream_settings FROM inbounds WHERE id=?', (tuic_id,)).fetchone()[0], tuic_stream_before)
                    self.assertEqual(db.execute('SELECT stream_settings FROM inbounds WHERE id=?', (reality_id,)).fetchone()[0], reality_stream_before)
                self.assertEqual(external, [])
                self.assertEqual(errors, [])
                self.assertEqual(context.request.get(base + response.json()['paths']['mihomo.yaml']).status, 404)
                print('VMess managed TLS: inbound and certificate-page selectors / card binding / create / cancel / edit / refresh / re-edit / hidden UUID retained / three exports / stopped restore OK')
                print('VLESS WS managed TLS: create / cancel / invalid path and Host rejected / path and client Host edit / refresh / prefill / hidden UUID retained / binding retained / three verified-TLS exports without server paths / stopped restore OK')
                print('VLESS gRPC Lite managed TLS: create / cancel / literal invalid service rejected / service edit / refresh / prefill / hidden UUID and h2 ALPN retained / binding / three exports / stopped restore OK')
                print('Hysteria2 managed TLS: native QUIC UDP listener / create / cancel / edit / refresh / reopen / hidden password retained / binding selectors / three verified-TLS exports / damaged stopped backup restoration OK')
                print('TUIC v5 managed TLS: create / cancel / hidden UUID and password / blank-preserving edit / refresh / reopen / h3 native QUIC / managed selectors / three exports / damaged stopped restore / safe ordinary responses OK')
                print('REALITY/Vision: create / cancel / explicit SNI and Chrome / hidden stable keypair / independent UUID and short-ID replacement / invalid replacement rejection / no managed binding / three exports / damaged stopped restore / safe ordinary responses OK')
                print('Node editor browser: managed TLS create / cancel / protected TLS unbind / none and REALITY edits / refresh / re-edit / TLS export / stopped restore / credentials retained OK')
                stop()
                browser.close()


if __name__ == '__main__':
    unittest.main()
