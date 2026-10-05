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
                            page=context.new_page();page.on('dialog',lambda dialog:dialog.accept())
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
                            print('Workspace browser: draft/save/reload/stable URL/rotate/revoke/XSS/no-storage/mobile OK')
                        finally: browser.close()
                finally:
                    server.terminate()
                    try: server.wait(timeout=10)
                    except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)

if __name__=='__main__': unittest.main()
