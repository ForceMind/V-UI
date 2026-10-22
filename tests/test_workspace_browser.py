"""Real browser API workflow; no third-party assets, actual temporary Uvicorn server."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest

@unittest.skipUnless(os.getenv('VUI_WORKSPACE_BROWSER')=='1', 'explicit browser check')
class WorkspaceBrowserTests(unittest.TestCase):
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
                            print('Workspace browser: draft/save/reload races at snapshot and preview/stable URL/rotate/revoke/XSS/no-storage/mobile OK')
                        finally: browser.close()
                finally:
                    server.terminate()
                    try: server.wait(timeout=10)
                    except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)

if __name__=='__main__': unittest.main()
