"""Real browser API workflow; no third-party assets, actual temporary Uvicorn server."""
import os
import base64
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

@unittest.skipUnless(os.getenv('VUI_WORKSPACE_BROWSER')=='1', 'explicit browser check')
class WorkspaceBrowserTests(unittest.TestCase):
    def assert_stale_node_selection_requires_explicit_refresh(self, page, context, base, work, expect):
        """Keep the real old document open across natural integer-ID reuse."""
        headers = {'Origin':base, 'X-VUI-Request':'1'}
        old_uuid = '44444444-4444-4444-8444-444444444444'
        new_uuid = '55555555-5555-4555-8555-555555555555'
        mismatch = 'Subscription node selection changed; refresh nodes and select them again'

        def saved_not_applied(response):
            self.assertEqual(response.status, 409, response.text())
            detail = response.json()['detail']
            self.assertIs(detail['saved'], True); self.assertIs(detail['applied'], False)
            self.assertEqual(detail['message'], 'Pinned core binary is missing or not executable')

        def create_node(label, port, uuid):
            response = context.request.post(base+'/api/inbounds', headers=headers, data={
                'core':'sing-box', 'protocol':'vless', 'remark':label, 'port':port, 'enable':True,
                'settings':{'users':[{'uuid':uuid}]},
                'stream_settings':{'tls':{'enabled':True, 'server_name':'vpn.example.test'}}})
            saved_not_applied(response)
            response = context.request.get(base+'/api/inbounds')
            self.assertEqual(response.status, 200)
            rows = [row for row in response.json() if row['remark'] == label]
            self.assertEqual(len(rows), 1)
            self.assertNotIn('instance_id', response.text()); self.assertNotIn(uuid, response.text())
            return rows[0]['id']

        def instance(identifier):
            with sqlite3.connect(work/'data/v-ui.db') as db:
                return db.execute('SELECT instance_id FROM inbounds WHERE id=?', (identifier,)).fetchone()[0]

        original = create_node('selection-original', 10446, old_uuid)
        original_instance = instance(original)
        with page.expect_response(lambda response: response.url == base+'/api/inbounds'
                                  and response.request.method == 'GET') as displayed:
            page.goto(base+'/workspace')
        revision = displayed.value.headers.get('x-vui-node-revision')
        self.assertRegex(revision or '', r'^[0-9a-f]{64}$')
        expect(page.locator('#workspace')).to_be_visible()
        expect(page.locator('#create-grant')).to_be_enabled()
        page.locator('#label').fill('stale-page-new-authorization')
        page.locator('#server').fill('vpn.example.test')
        page.locator('#proxy').fill('keep-draft-during-node-refresh.example.test')
        selected = page.locator(f'[data-node="{original}"]')
        selected.check()
        for output_format in ('mihomo.yaml', 'raw', 'sing-box.json'):
            page.locator(f'input[name="format"][value="{output_format}"]').check()
        # Only another authenticated API caller changes inventory. No page
        # navigation or list refresh may conceal the stale selection window.
        saved_not_applied(context.request.delete(base+f'/api/inbounds/{original}', headers=headers))
        replacement = create_node('selection-replacement', 10447, new_uuid)
        self.assertEqual(replacement, original)
        self.assertNotEqual(instance(replacement), original_instance)
        current = context.request.get(base+'/api/inbounds')
        self.assertNotEqual(current.headers.get('x-vui-node-revision'), revision)
        expect(page.locator('#nodes')).to_contain_text('selection-original')
        expect(page.locator('#nodes')).not_to_contain_text('selection-replacement')
        expect(selected).to_be_checked()
        before = context.request.get(base+'/api/subscriptions').json()
        writes = []
        def record(request):
            if request.url == base+'/api/subscriptions' and request.method == 'POST': writes.append(request)
        page.on('request', record)
        try:
            with page.expect_response(lambda response: response.url == base+'/api/subscriptions'
                                      and response.request.method == 'POST') as rejected:
                page.locator('#create-grant').click()
            self.assertEqual(rejected.value.status, 409)
            self.assertEqual(rejected.value.json(), {'detail':mismatch})
            expect(page.locator('#create-grant')).to_be_disabled()
            expect(selected).not_to_be_checked()
            expect(page.locator('#issued')).to_be_hidden()
            expect(page.locator('#notice')).to_contain_text('重新选择')
            self.assertEqual(context.request.get(base+'/api/subscriptions').json(), before)
            self.assertEqual(len(writes), 1, 'a conflict never automatically creates another grant')
            self.assertEqual(writes[0].post_data_json['expected_node_revision'], revision)
            with page.expect_response(lambda response: response.url == base+'/api/inbounds'
                                      and response.request.method == 'GET') as refreshed:
                page.locator('#refresh-grants').click()
            next_revision = refreshed.value.headers.get('x-vui-node-revision')
            self.assertRegex(next_revision or '', r'^[0-9a-f]{64}$')
            self.assertNotEqual(next_revision, revision)
            expect(page.locator('#create-grant')).to_be_enabled()
            expect(page.locator('#nodes')).to_contain_text('selection-replacement')
            expect(page.locator('#nodes')).not_to_contain_text('selection-original')
            expect(selected).not_to_be_checked()
            expect(page.locator('#proxy')).to_have_value('keep-draft-during-node-refresh.example.test')
            expect(page.locator('#save-state')).to_contain_text('未保存草稿')
            self.assertEqual(len(writes), 1)
            selected.check()
            with page.expect_response(lambda response: response.url == base+'/api/subscriptions'
                                      and response.request.method == 'POST') as accepted:
                page.locator('#create-grant').click()
            self.assertEqual(accepted.value.status, 201)
            grant = accepted.value.json()
            expect(page.locator('#issued')).to_be_visible()
            expect(page.locator('#create-grant')).to_be_enabled()
            self.assertEqual(len(writes), 2)
            self.assertEqual(writes[-1].post_data_json['expected_node_revision'], next_revision)
            self.assertEqual(grant['inbound_ids'], [replacement])
            self.assertEqual(set(grant['paths']), {'mihomo.yaml', 'raw', 'sing-box.json'})
            for output_format, path in grant['paths'].items():
                response = context.request.get(base+path)
                self.assertEqual(response.status, 200)
                body = base64.b64decode(response.text(), validate=True).decode() if output_format == 'raw' else response.text()
                self.assertIn(new_uuid, body); self.assertNotIn(old_uuid, body)
            self.assertNotIn(grant['token'], (work/'server.log').read_text())
            self.assertEqual(page.evaluate('Object.keys(localStorage).length+Object.keys(sessionStorage).length'), 0)
        finally:
            page.remove_listener('request', record)

    def assert_identity_reauthorization_workflow(self, page, context, base, work, expect):
        """Actual local API responses drive the UI, without a proxy binary.

        Only the legacy-unbound fixture edits SQLite directly; creation,
        deletion, replacement and all grant operations use the real API.
        """
        headers = {'Origin':base, 'X-VUI-Request':'1'}
        formats = ('mihomo.yaml', 'raw', 'sing-box.json')
        uuids = ('11111111-1111-1111-1111-111111111111',
                 '22222222-2222-2222-2222-222222222222',
                 '33333333-3333-3333-3333-333333333333')
        path = work / 'data' / 'v-ui.db'
        tokens, identities = [], []

        def sql_row(table, identifier):
            self.assertIn(table, ('inbounds', 'subscription_grants'))
            with sqlite3.connect(path) as db:
                db.row_factory = sqlite3.Row
                value = db.execute('SELECT * FROM ' + table + ' WHERE id=?', (identifier,)).fetchone()
                self.assertIsNotNone(value)
                return dict(value)

        def listed():
            response = context.request.get(base + '/api/subscriptions')
            self.assertEqual(response.status, 200)
            values = response.json()
            for value in values:
                self.assertFalse({'instance_id', 'inbound_bindings', 'token', 'token_hash'} & value.keys())
            raw = response.text()
            for secret in tokens + identities:
                self.assertNotIn(secret, raw)
            return {value['id']:value for value in values}

        def create_from_form(identifier, label):
            page.goto(base + '/workspace')
            expect(page.locator('#workspace')).to_be_visible()
            expect(page.locator('#create-grant')).to_be_enabled()
            page.locator('#label').fill(label)
            page.locator('#server').fill('vpn.example.test')
            for checkbox in page.locator('[data-node]').all(): checkbox.uncheck()
            page.locator(f'[data-node="{identifier}"]').check()
            for output_format in formats:
                page.locator(f'input[name="format"][value="{output_format}"]').check()
            with page.expect_response(lambda response: response.url == base + '/api/subscriptions'
                                      and response.request.method == 'POST') as pending:
                page.locator('#create-grant').click()
            response = pending.value
            self.assertEqual(response.status, 201)
            grant = response.json()
            self.assertEqual(set(grant['paths']), set(formats))
            tokens.append(grant['token'])
            self.assertFalse({'instance_id', 'inbound_bindings', 'token_hash'} & grant.keys())
            expect(page.locator('#issued')).to_be_visible()
            expect(page.locator('#create-grant')).to_be_enabled()
            self.assertEqual(set(page.locator('#issued-urls').input_value().splitlines()),
                             {base + value for value in grant['paths'].values()})
            return grant

        def exports(grant, uuid, port):
            for output_format, relative in grant['paths'].items():
                response = context.request.get(base + relative)
                self.assertEqual(response.status, 200)
                body = (base64.b64decode(response.text(), validate=True).decode()
                        if output_format == 'raw' else response.text())
                self.assertIn(uuid, body); self.assertIn(str(port), body)
                for secret in identities + tokens:
                    self.assertNotIn(secret, body)
                self.assertEqual(context.request.head(base + relative).status, 200)

        def public_denied(grant):
            for relative in grant['paths'].values():
                for method in ('get', 'head'):
                    response = getattr(context.request, method)(base + relative)
                    self.assertEqual(response.status, 409)
                    if method == 'get':
                        self.assertEqual(response.json(), {'detail':'Subscription configuration is not ready'})
                    for secret in tokens + identities + list(uuids) + ['10443', '10444', '10445']:
                        self.assertNotIn(secret, response.text())

        def invalid(grant, reason, label):
            value = listed()[grant['id']]
            self.assertTrue(value['invalidated']); self.assertFalse(value['revoked'])
            self.assertEqual(value['invalidation_reason'], reason)
            with page.expect_response(lambda response: response.url == base + '/api/subscriptions'
                                      and response.request.method == 'GET') as pending:
                page.locator('#refresh-grants').click()
            self.assertEqual(pending.value.status, 200)
            expect(page.locator('#refresh-grants')).to_be_enabled()
            row = page.locator(f'#grants tr:has(button[data-rotate="{grant["id"]}"])')
            expect(row).to_contain_text(label)
            expect(row).not_to_contain_text('密码变更')
            expect(row.locator('[data-rotate]')).to_be_disabled()
            before = sql_row('subscription_grants', grant['id'])
            response = context.request.post(base + f'/api/subscriptions/{grant["id"]}/rotate',
                                            headers=headers, data={'expires_days':31})
            self.assertEqual(response.status, 409)
            self.assertIn('create a new subscription', response.json()['detail'])
            self.assertEqual(sql_row('subscription_grants', grant['id']), before)
            for secret in tokens + identities + list(uuids): self.assertNotIn(secret, response.text())
            public_denied(grant)

        # Legacy grants cannot prove a historical instance. This deliberately
        # prepares the nullable migrated field, not a fake successful response.
        identities.append(sql_row('inbounds', 1)['instance_id'])
        legacy = create_from_form(1, 'synthetic legacy-unbound authorization')
        exports(legacy, uuids[0], 10443)
        with sqlite3.connect(path) as db:
            db.execute('UPDATE subscription_grants SET inbound_bindings=NULL WHERE id=?', (legacy['id'],))
        invalid(legacy, 'node_identity_missing', '旧授权缺少节点身份，请重新选择节点并创建订阅')
        sibling = create_from_form(1, 'explicit replacement for legacy authorization')
        self.assertNotEqual(sibling['id'], legacy['id']); exports(sibling, uuids[0], 10443)
        public_denied(legacy)

        def saved_not_applied(response):
            # No fixture core is installed. The real API saves desired rows,
            # reports apply failure, and must not pretend to have applied them.
            self.assertEqual(response.status, 409)
            detail = response.json()['detail']
            self.assertIs(detail['saved'], True); self.assertIs(detail['applied'], False)
            self.assertEqual(detail['message'], 'Pinned core binary is missing or not executable')

        def create_node(label, port, uuid):
            response = context.request.post(base + '/api/inbounds', headers=headers, data={
                'core':'sing-box', 'protocol':'vless', 'remark':label, 'port':port, 'enable':True,
                'settings':{'users':[{'uuid':uuid}]},
                'stream_settings':{'tls':{'enabled':True, 'server_name':'vpn.example.test'}},
            })
            saved_not_applied(response)
            response = context.request.get(base + '/api/inbounds')
            self.assertEqual(response.status, 200)
            matches = [value for value in response.json() if value['remark'] == label]
            self.assertEqual(len(matches), 1)
            self.assertNotIn('instance_id', response.text())
            self.assertNotIn(uuid, response.text())
            return matches[0]['id']

        original_id = create_node('synthetic highest node', 10444, uuids[1])
        self.assertEqual(original_id, 2)
        original_identity = sql_row('inbounds', original_id)['instance_id']; identities.append(original_identity)
        original = create_from_form(original_id, 'authorization before natural ID reuse')
        exports(original, uuids[1], 10444)
        saved_not_applied(context.request.delete(base + f'/api/inbounds/{original_id}', headers=headers))
        self.assertNotIn(original_id, [value['id'] for value in context.request.get(base + '/api/inbounds').json()])
        public_denied(original)
        replacement_id = create_node('synthetic new incarnation', 10445, uuids[2])
        self.assertEqual(replacement_id, original_id)
        replacement_identity = sql_row('inbounds', replacement_id)['instance_id']; identities.append(replacement_identity)
        self.assertNotEqual(replacement_identity, original_identity)
        invalid(original, 'node_identity_changed', '节点已删除、重建或身份异常，请重新创建订阅')
        exports(sibling, uuids[0], 10443)
        replacement = create_from_form(replacement_id, 'explicit new incarnation authorization')
        self.assertNotEqual(replacement['id'], original['id']); exports(replacement, uuids[2], 10445)
        public_denied(original); public_denied(legacy)
        listed()
        self.assertEqual(page.evaluate('Object.keys(localStorage).length+Object.keys(sessionStorage).length'), 0)
        for token in tokens: self.assertNotIn(token, (work / 'server.log').read_text())
        self.assertFalse((work / 'bin').exists() and any((work / 'bin').iterdir()))

    def assert_issued_hide_during_list_refresh(self, page, context, base, work, expect):
        """Real Chromium activation while only a genuine list GET is held.

        No document navigation or synthetic pagehide is used here. Controller
        pagehide/fetch/JSON/blob races have separate deterministic controls.
        """
        writes, tokens = [], []
        previous, identifier = None, None
        def record_write(request):
            if request.url.startswith(base + '/api/subscriptions') and request.method != 'GET':
                writes.append(request)
        page.on('request', record_write)
        try:
            page.locator('#label').fill('synthetic Hide lifecycle')
            page.locator('#server').fill('vpn.example.test')
            for checkbox in page.locator('[data-node]').all(): checkbox.uncheck()
            page.locator('[data-node="1"]').check()
            for checkbox in page.locator('input[name="format"]').all(): checkbox.uncheck()
            page.locator('input[name="format"][value="mihomo.yaml"]').check()
            for kind in ('create', 'rotate'):
                with self.subTest(issued_action=kind, boundary='real post-issue list GET'):
                    pending, actual = [], None
                    pattern = '**/api/subscriptions'
                    def hold(route):
                        if route.request.method == 'GET': pending.append(route)
                        else: route.fallback()
                    page.route(pattern, hold)
                    before_writes = len(writes)
                    old_notice = page.locator('#notice').text_content()
                    endpoint = base + '/api/subscriptions' + ('' if kind == 'create' else f'/{identifier}/rotate')
                    try:
                        with page.expect_response(lambda response: response.url == endpoint
                                                  and response.request.method == 'POST') as request:
                            page.locator('#create-grant' if kind == 'create' else f'[data-rotate="{identifier}"]').click()
                        issued = request.value
                        self.assertEqual(issued.status, 201 if kind == 'create' else 200)
                        value = issued.json(); identifier = value['id']; tokens.append(value['token'])
                        current = base + value['paths']['mihomo.yaml']
                        deadline = time.monotonic() + 10
                        while not pending and time.monotonic() < deadline:
                            page.wait_for_timeout(20)
                        self.assertEqual(len(pending), 1)
                        actual = pending[0].fetch(timeout=10000)
                        self.assertEqual(actual.status, 200)
                        self.assertIn(identifier, [grant['id'] for grant in actual.json()])
                        for token in tokens: self.assertNotIn(token, actual.text())
                        expect(page.locator('#issued')).to_be_visible()
                        expect(page.locator('#issued-urls')).to_have_value(current)
                        for button in ('create-grant', 'copy', 'download', 'refresh-grants'):
                            expect(page.locator('#' + button)).to_be_disabled()
                        expect(page.locator('#forget')).to_be_enabled()
                        # A normal click, never force/evaluate/dispatch_event. On
                        # the baseline disabled button this must fail, not wait
                        # for the held list response to release itself.
                        page.locator('#forget').click(timeout=3000)
                        expect(page.locator('#issued')).to_be_hidden()
                        expect(page.locator('#issued-urls')).to_have_value('')
                        self.assertEqual(context.request.get(current).status, 200, 'Hide does not revoke the issued grant')
                        if previous is not None:
                            self.assertNotEqual(current, previous)
                            self.assertEqual(context.request.get(previous).status, 404, 'normal rotation still invalidates the old token')
                        pending[0].fulfill(response=actual); pending.clear()
                        expect(page.locator('#create-grant')).to_be_enabled()
                        expect(page.locator('#issued')).to_be_hidden()
                        expect(page.locator('#issued-urls')).to_have_value('')
                        expect(page.locator('#download')).to_be_disabled()
                        self.assertEqual(page.locator('#notice').text_content(), old_notice)
                        self.assertEqual(len(writes), before_writes + 1, 'no retry or automatic revoke')
                        self.assertEqual(writes[-1].url, endpoint)
                        self.assertEqual(writes[-1].method, 'POST')
                        previous = current
                    finally:
                        page.unroute(pattern, hold)
                        for route in pending: route.abort()
                        if actual is not None: actual.dispose()
            self.assertEqual(page.evaluate('Object.keys(localStorage).length+Object.keys(sessionStorage).length'), 0)
            for token in tokens: self.assertNotIn(token, (work / 'server.log').read_text())
        finally:
            page.remove_listener('request', record_write)

    def assert_reload_preserves_inflight_drafts(self, page, context, base, dialogs, expect):
        """Delay genuine local API responses, never replace them with synthetic success."""
        writes = []
        def record_write(request):
            if request.url == base + '/api/routing/mihomo' and request.method == 'PUT':
                writes.append(request)
        page.on('request', record_write)
        try:
            for endpoint in ('snapshot', 'preview'):
                for dirty_start in (False, True):
                    with self.subTest(endpoint=endpoint, dirty_start=dirty_start):
                        before = context.request.get(base + '/api/routing/mihomo/snapshot').json()
                        self.assertEqual(before['settings']['proxy_domains'], ['draft.example.test'])
                        if dirty_start:
                            page.locator('#proxy').fill('discard-approved.example.test')
                        dialog_count, write_count = len(dialogs), len(writes)
                        previous_preview = page.locator('#rule-preview').text_content()
                        pending, actual = [], None
                        pattern = '**/api/routing/mihomo/' + endpoint
                        def hold(route):
                            pending.append(route)
                        page.route(pattern, hold)
                        try:
                            page.locator('#reload').click()
                            expect(page.locator('#reload')).to_be_disabled()
                            deadline = time.monotonic() + 10
                            while not pending and time.monotonic() < deadline:
                                page.wait_for_timeout(20)
                            self.assertEqual(len(pending), 1)
                            actual = pending[0].fetch(timeout=10000)
                            self.assertEqual(actual.status, 200)
                            expect(page.locator('#proxy')).to_have_value(
                                'discard-approved.example.test' if dirty_start else 'draft.example.test')
                            expect(page.locator('#proxy')).to_be_editable()
                            page.locator('#proxy').fill('new-unsaved.example.test')
                            pending[0].fulfill(response=actual)
                            pending.clear()
                            expect(page.locator('#reload')).to_be_enabled()
                            expect(page.locator('#proxy')).to_have_value('new-unsaved.example.test')
                            expect(page.locator('#save-state')).to_contain_text('未保存草稿')
                            expect(page.locator('#save')).to_be_enabled()
                            expect(page.locator('#preview-label')).to_contain_text('重新生成')
                            expect(page.locator('#notice')).to_contain_text('保留')
                            self.assertEqual(page.locator('#rule-preview').text_content(), previous_preview)
                            self.assertEqual(len(dialogs) - dialog_count, int(dirty_start))
                            self.assertEqual(len(writes), write_count)
                            self.assertEqual(context.request.get(base + '/api/routing/mihomo/snapshot').json(), before)
                        finally:
                            page.unroute(pattern, hold)
                            for route in pending:
                                route.abort()
                            if actual is not None:
                                actual.dispose()
                        # Saving remains an explicit action using the existing optimistic lock.
                        page.locator('#save').click()
                        expect(page.locator('#save-state')).to_contain_text('已与保存版本一致')
                        self.assertEqual(len(writes), write_count + 1)
                        self.assertEqual(writes[-1].headers['if-match'], '"' + before['revision'] + '"')
                        self.assertEqual(context.request.get(base + '/api/routing/mihomo/snapshot').json()['settings']['proxy_domains'],
                                         ['new-unsaved.example.test'])
                        page.locator('#proxy').fill('draft.example.test')
                        page.locator('#save').click()
                        expect(page.locator('#save-state')).to_contain_text('已与保存版本一致')
                        expect(page.locator('#reload')).to_be_enabled()
        finally:
            page.remove_listener('request', record_write)

    def test_drafts_persistence_grants_rotation_and_revocation(self):
        from playwright.sync_api import sync_playwright, expect
        root = Path(__file__).resolve().parents[1]
        password = 'Synthetic-workspace-password!'
        with tempfile.TemporaryDirectory(prefix='vui-workspace-browser-') as tmp:
            work=Path(tmp)
            env={**os.environ,'PYTHONPATH':str(root),'VUI_PUBLIC_ORIGIN':'','VUI_DATA_DIR':str(work/'data'),'VUI_BIN_DIR':str(work/'bin')}
            setup='''import sys
from app.services.auth_service import provision_admin
from app.models import database
provision_admin('tester',sys.stdin.read())
with database.SessionLocal() as db:
 db.add(database.Inbound(id=1,core='sing-box',protocol='vless',remark='browser node',port=10443,enable=True,settings={'users':[{'uuid':'11111111-1111-1111-1111-111111111111'}]},stream_settings={'tls':{'enabled':True,'server_name':'vpn.example.test'}}))
 db.commit()
'''
            done=subprocess.run([sys.executable,'-c',setup],input=password,text=True,env=env,cwd=work,capture_output=True,timeout=10)
            self.assertEqual(done.returncode,0,done.stderr)
            with (work/'server.log').open('w') as log:
                server=subprocess.Popen([sys.executable,'-m','uvicorn','main:app','--host','127.0.0.1','--port','0','--no-proxy-headers','--no-use-colors'],cwd=work,env=env,stdout=log,stderr=subprocess.STDOUT)
                try:
                    deadline=time.monotonic()+15;match=None
                    while time.monotonic()<deadline:
                        if server.poll() is not None: self.fail((work/'server.log').read_text())
                        match=re.search(r'Uvicorn running on http://127\.0\.0\.1:(\d+)',(work/'server.log').read_text())
                        if match: break
                        time.sleep(.05)
                    self.assertIsNotNone(match)
                    base='http://127.0.0.1:'+match.group(1)
                    with sync_playwright() as p:
                        browser=p.chromium.launch(headless=True)
                        try:
                            context=browser.new_context(viewport={'width':390,'height':844})
                            page=context.new_page();dialogs=[]
                            def accept_dialog(dialog):
                                dialogs.append(dialog.message);dialog.accept()
                            page.on('dialog',accept_dialog)
                            external=[]
                            def guard(route):
                                if not route.request.url.startswith(base): external.append(route.request.url);route.abort()
                                else: route.continue_()
                            page.route('**/*',guard)
                            page.goto(base+'/login')
                            page.locator('#login-name').fill('tester');page.locator('#login-password').fill(password)
                            page.get_by_role('button',name='登录面板').click();page.wait_for_url('**/ui/')
                            # Old dashboard uses CDN until the release-packaging stage.
                            external.clear();page.goto(base+'/workspace')
                            expect(page.locator('#workspace')).to_be_visible()
                            self.assertEqual(external,[])
                            self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth'))
                            page.locator('#mode').select_option('direct')
                            page.locator('#proxy').fill('draft.example.test')
                            page.locator('#preview').click();expect(page.locator('#preview-label')).to_contain_text('草稿')
                            self.assertFalse((work/'data'/'mihomo-routing.json').exists())
                            page.locator('#save').click();expect(page.locator('#save-state')).to_contain_text('已与保存版本一致')
                            page.reload();expect(page.locator('#proxy')).to_have_value('draft.example.test')
                            expect(page.locator('#workspace')).to_be_visible()
                            expect(page.locator('#reload')).to_be_enabled()
                            self.assert_reload_preserves_inflight_drafts(page,context,base,dialogs,expect)
                            page.locator('#label').fill('<img src=x onerror=alert(1)>')
                            page.locator('#server').fill('vpn.example.test');page.locator('[data-node="1"]').check()
                            page.locator('#create-grant').click();expect(page.locator('#issued')).to_be_visible()
                            first=page.locator('#issued-urls').input_value()
                            self.assertEqual(context.request.get(first).status,200)
                            self.assertIn('draft.example.test',context.request.get(first).text())
                            self.assertEqual(page.locator('#grants img').count(),0)
                            page.locator('#proxy').fill('updated.example.test');page.locator('#save').click()
                            expect(page.locator('#save-state')).to_contain_text('已与保存版本一致')
                            self.assertIn('updated.example.test',context.request.get(first).text())
                            page.reload();expect(page.locator('#workspace')).to_be_visible();expect(page.locator('#issued')).to_be_hidden()
                            page.locator('[data-rotate]').click();expect(page.locator('#issued')).to_be_visible()
                            second=page.locator('#issued-urls').input_value()
                            self.assertNotEqual(first,second);self.assertEqual(context.request.get(first).status,404)
                            self.assertEqual(context.request.get(second).status,200)
                            page.locator('[data-revoke]').click();expect(page.locator('#grants')).to_contain_text('已撤销')
                            self.assertEqual(context.request.get(second).status,404)
                            self.assertEqual(page.evaluate('Object.keys(localStorage).length+Object.keys(sessionStorage).length'),0)
                            self.assertNotIn(first.split('/sub/')[1].split('/')[0],(work/'server.log').read_text())
                            self.assertNotIn(second.split('/sub/')[1].split('/')[0],(work/'server.log').read_text())
                            self.assert_issued_hide_during_list_refresh(page,context,base,work,expect)
                            self.assert_identity_reauthorization_workflow(page,context,base,work,expect)
                            self.assert_stale_node_selection_requires_explicit_refresh(page,context,base,work,expect)
                            print('Workspace browser: draft/save/reload races/stable URL/rotate/revoke/Hide during real list GET/legacy reauthorization/natural node ID reuse/stale-view create conflict and explicit reselect/all-format denial/XSS/no-storage/mobile OK')
                        finally: browser.close()
                finally:
                    server.terminate()
                    try: server.wait(timeout=10)
                    except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)

if __name__=='__main__': unittest.main()
