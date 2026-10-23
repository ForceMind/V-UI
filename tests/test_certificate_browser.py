"""Graphical certificate issuance through real Certbot/Pebble, no fake success jobs."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from acme_helpers import PebbleFixture
from certificate_browser_helpers import (hold_next_certificate_read, wait_certificate_read,
    release_certificate_read, restore_certificate_reads)

@unittest.skipUnless(os.getenv('VUI_CERT_BROWSER')=='1' and os.getenv('VUI_TEST_PEBBLE'),'explicit ACME browser gate')
class CertificateBrowserTests(unittest.TestCase):
    def test_actual_issuance_status_and_renewal_policy_ui(self):
        from playwright.sync_api import sync_playwright,expect
        source=Path(__file__).resolve().parents[1]
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-acme-browser-')))
            data=root/'data';certroot=data/'certificates'
            pebble=PebbleFixture(stack,root,certroot/'http-webroot')
            trust=root/'issuance-ca.pem';trust.write_bytes(pebble.root_pem)
            password='Synthetic-ACME-browser-password!'
            env={**os.environ,'PYTHONPATH':str(source),'PYTHONDONTWRITEBYTECODE':'1','VUI_DATA_DIR':str(data),'VUI_BIN_DIR':str(root/'no-cores'),'VUI_PUBLIC_ORIGIN':'','VUI_CERTIFICATES_ENABLED':'1'}
            # Test-only constructor injection cannot be configured over the API.
            code='''import sys,uvicorn
from pathlib import Path
from app.certificates import manager as module
from app.certificates.provider import CertbotProvider
from app.services.auth_service import provision_admin
root=Path(sys.argv[1])
module._instance=module.CertificateManager(root,CertbotProvider(root,test_directory=sys.argv[2],test_ca=Path(sys.argv[3])),trusted_roots=Path(sys.argv[4]).read_bytes())
provision_admin('tester',sys.stdin.read())
import main
uvicorn.run(main.app,host='127.0.0.1',port=0,proxy_headers=False,use_colors=False,access_log=False)
'''
            logpath=root/'server.log';log=stack.enter_context(logpath.open('w'))
            process=subprocess.Popen([sys.executable,'-B','-c',code,str(certroot),pebble.directory,str(pebble.ca),str(trust)],cwd=root,env=env,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,text=True)
            process.stdin.write(password);process.stdin.close()
            def stop():
                process.terminate()
                try:process.wait(timeout=15)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            stack.callback(stop)
            deadline=time.monotonic()+15;match=None
            while time.monotonic()<deadline:
                if process.poll() is not None:self.fail(logpath.read_text())
                match=re.search(r'Uvicorn running on http://127\.0\.0\.1:(\d+)',logpath.read_text())
                if match:break
                time.sleep(.05)
            self.assertIsNotNone(match);base='http://127.0.0.1:'+match.group(1)
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                try:
                    context=browser.new_context(viewport={'width':390,'height':844})
                    page=context.new_page();external=[]
                    def local(route):
                        if not route.request.url.startswith(base+'/'):external.append(route.request.url);route.abort()
                        else:route.continue_()
                    page.route('**/*',local)
                    page.goto(base+'/login');page.locator('#login-name').fill('tester');page.locator('#login-password').fill(password)
                    page.get_by_role('button',name='登录面板',exact=True).click();page.wait_for_url('**/ui/')
                    external.clear();page.goto(base+'/certificates')
                    expect(page.locator('#cert-workspace')).to_be_visible()
                    page.locator('#domain').fill('panel.example.test');page.locator('#email').fill('a@example.test')
                    page.locator('#environment').select_option('production');page.locator('#terms').check()
                    page.locator('#request-certificate button[type=submit]').click()
                    card=page.locator('[data-certificate]').first
                    expect(card.locator('[data-job-state="succeeded"]')).to_be_visible(timeout=30000)
                    expect(card).to_contain_text('有效')
                    expect(page.locator('#refresh')).to_be_enabled()
                    # The next real five-second poll reads auto_renew=True.
                    # Delay only delivery, then pause through the normal UI.
                    hold_next_certificate_read(page)
                    try:
                        older = wait_certificate_read(page)
                        self.assertEqual(older['status'], 200)
                        self.assertEqual(older['intercepted'], 1)
                        self.assertEqual(len(older['body']), 1)
                        self.assertIs(older['body'][0]['auto_renew'], True)
                        card.get_by_role('button',name='暂停自动续期',exact=True).click()
                        expect(card.get_by_role('button',name='启用自动续期',exact=True)).to_be_visible()
                        expect(page.locator('#refresh')).to_be_enabled()
                        current = context.request.get(base+'/api/certificates').json()
                        self.assertIs(current[0]['auto_renew'], False)
                        notice = page.locator('#notice').text_content()
                        page.evaluate('''() => {
                            const audit = certificateReadAudit;
                            audit.staleRepaint = false;
                            const inspect = records => {
                                for (const record of records) for (const node of record.addedNodes) {
                                    if (node.querySelectorAll && Array.from(node.querySelectorAll('button'))
                                        .some(button => button.textContent === '暂停自动续期')) audit.staleRepaint = true;
                                }
                            };
                            audit.observer = new MutationObserver(inspect);
                            audit.observer.observe(document.getElementById('certificate-list'), {childList:true});
                            audit.finishRepaintAudit = () => {
                                inspect(audit.observer.takeRecords()); audit.observer.disconnect();
                                return audit.staleRepaint;
                            };
                        }''')
                        release_certificate_read(page)
                        # Do not auto-retry this assertion: the next normal
                        # poll could repair a stale repaint and hide the bug.
                        # The observer also catches a transient repaint if CI
                        # pauses between delivery and this one-shot snapshot.
                        rendered = page.evaluate('''() => ({
                            buttons:Array.from(document.querySelector('[data-certificate]').querySelectorAll('button'),
                                button => button.textContent),
                            notice:document.getElementById('notice').textContent,
                            staleRepaint:certificateReadAudit.finishRepaintAudit()
                        })''')
                        self.assertFalse(rendered['staleRepaint'])
                        self.assertEqual(rendered['buttons'].count('启用自动续期'), 1)
                        self.assertNotIn('暂停自动续期', rendered['buttons'])
                        self.assertEqual(rendered['notice'], notice)
                    finally:
                        restore_certificate_reads(page)
                    page.reload();expect(page.locator('[data-certificate]').first).to_contain_text('启用自动续期')
                    cards=context.request.get(base+'/api/certificates').json();self.assertEqual(len(cards),1)
                    chain=context.request.get(base+'/api/certificates/'+cards[0]['id']+'/fullchain.pem').body()
                    self.assertIn(b'BEGIN CERTIFICATE',chain);self.assertNotIn(b'PRIVATE KEY',chain)
                    page.locator('#domain').fill('panel.example.test');page.locator('#email').fill('a@example.test')
                    page.locator('#environment').select_option('staging');page.locator('#terms').check()
                    page.locator('#request-certificate button[type=submit]').click()
                    expect(page.locator('[data-certificate]')).to_have_count(2)
                    stage=page.locator('[data-certificate]').filter(has=page.get_by_text('panel.example.test · 测试',exact=True))
                    expect(stage.locator('[data-job-state="succeeded"]')).to_be_visible(timeout=30000)
                    expect(stage.get_by_role('button',name='应用到面板',exact=True)).to_be_disabled()
                    self.assertEqual(external,[])
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth'))
                    self.assertEqual(page.evaluate('Object.keys(localStorage).length+Object.keys(sessionStorage).length'),0)
                    self.assertNotIn(password,logpath.read_text())
                    print('Certificate browser: real HTTP-01 issuance / status / stale real poll after pause rejected / pause-reload / staging isolation / public chain / no storage / mobile OK')
                finally:browser.close()

if __name__=='__main__':unittest.main()
