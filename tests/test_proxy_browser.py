"""Real Chromium lifecycle checks for the proxy UI.

Import/draft tests use the authenticated application and isolated real parser /
private store. Runtime/apply/diagnostic response fixtures test browser behaviour
only: they are not evidence of a proxy connection or an external device path.
VUI_PROXY_BROWSER=1 is an opt-in gate; missing browser/dependencies fail when on.
The real login lands on the dashboard, so its checksum-pinned frontend assets
must be prepared too; blocking its development CDN leaves app.js without Axios.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import urlparse

from scripts.prepare_frontend import ASSETS, prepare


ROOT = Path(__file__).resolve().parents[1]
UUID = '11111111-1111-4111-8111-111111111111'
URI = f'vless://{UUID}@upstream.example.test:443?security=tls&sni=upstream.example.test#Synthetic%20TLS'
PROFILE_ID = 'up_' + '1' * 32
PASSWORD = 'Synthetic-proxy-browser-password!'


def prepare_browser_payload(destination, assets):
    """Exercise the existing packaged UI, including the real login destination."""
    # Assets are fetched and integrity-checked by scripts/vendor_frontend.py in
    # CI, exactly as for the node editor browser gate. Never stub libraries or
    # allow the browser to fetch a CDN as a fallback.
    for target in ASSETS.values():
        if not (assets / Path(target).name).is_file():
            raise ValueError('Missing pinned browser frontend asset: ' + target)
    destination.mkdir()
    for name in ('app', 'web'):
        shutil.copytree(ROOT / name, destination / name,
                        ignore=shutil.ignore_patterns('__pycache__', 'vendor'))
    shutil.copyfile(ROOT / 'main.py', destination / 'main.py')
    shutil.copytree(assets, destination / 'web/vendor')
    prepare(destination)
    return destination


def observe_browser_pages(context, errors, console):
    """Register before creating pages so the second tab shares the same audit."""
    def observe(page):
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: console.append(message.text))
    context.on('page', observe)


IMPORT_CONTROLS = ('import-format', 'import-file', 'import-content',
                   'preview-import', 'import-name', 'save-import')


def expect_import_fieldset(page, disabled, expect):
    # Playwright 1.57 roleUtils.ts excludes FIELDSET itself from its native
    # disabled matcher, but checks disabled-fieldset ancestry on form controls.
    fieldset = expect(page.locator('#import-fields'))
    fieldset.to_have_js_property('disabled', disabled)
    if disabled:
        fieldset.to_have_attribute('disabled', '')
    else:
        fieldset.not_to_have_attribute('disabled', '')
    for identifier in IMPORT_CONTROLS:
        control = expect(page.locator('#' + identifier))
        if disabled:
            control.to_be_disabled()
        elif identifier != 'save-import':  # Separately gated by explicit selection.
            control.to_be_enabled()


def outbound(name='Synthetic JSON'):
    return {'protocol': 'vless', 'tag': name,
            'settings': {'vnext': [{'address': 'upstream.example.test', 'port': 443,
                                   'users': [{'id': UUID, 'encryption': 'none'}]}]},
            'streamSettings': {'network': 'tcp', 'security': 'tls',
                               'tlsSettings': {'serverName': 'upstream.example.test'}}}


def fixture_snapshot(revision='a' * 64):
    settings = {'schema_version': 1, 'local_proxy_enabled': False,
                'relay_enabled': True, 'selected_upstream_id': PROFILE_ID,
                'local_port': 7891, 'local_username': 'synthetic-user',
                'local_password_set': True, 'dns_policy_version': 1,
                'deny_policy_version': 1}
    return {'revision': revision, 'settings': settings,
            'upstreams': [{'id': PROFILE_ID, 'name': 'Synthetic upstream',
                           'server': 'upstream.example.test', 'server_port': 443,
                           'tls': {'mode': 'tls'}, 'uuid_set': True}],
            'active': None, 'pending': None}


class ProxyPageStaticTests(unittest.TestCase):
    def test_import_fieldset_assertions_require_property_attribute_and_controls(self):
        # Unit-check assertion wiring, not Chromium's disabled implementation.
        def check(disabled, missing=None):
            observed = []
            def expect(selector):
                def verify(kind, *args):
                    item = (selector, kind, args)
                    observed.append(item)
                    if item == missing:
                        raise AssertionError('Synthetic fieldset/control regression')
                return SimpleNamespace(**{
                    kind: lambda *args, kind=kind: verify(kind, *args)
                    for kind in ('to_have_js_property', 'to_have_attribute',
                                 'not_to_have_attribute', 'to_be_disabled', 'to_be_enabled')})
            expect_import_fieldset(SimpleNamespace(locator=lambda selector: selector), disabled, expect)
            return observed
        for disabled in (True, False):
            expected = [('#import-fields', 'to_have_js_property', ('disabled', disabled)),
                        ('#import-fields', 'to_have_attribute' if disabled else 'not_to_have_attribute',
                         ('disabled', ''))]
            expected += [('#' + identifier, 'to_be_disabled' if disabled else 'to_be_enabled', ())
                         for identifier in IMPORT_CONTROLS if disabled or identifier != 'save-import']
            self.assertEqual(check(disabled), expected)
            for item in expected:
                with self.subTest(disabled=disabled, assertion=item), self.assertRaises(AssertionError):
                    check(disabled, missing=item)

    def test_all_browser_pages_keep_errors_and_console_in_shared_audit(self):
        class Events:
            def __init__(self):
                self.handlers = {}
            def on(self, event, handler):
                self.handlers[event] = handler
            def emit(self, event, value):
                self.handlers[event](value)
        context = Events()
        errors, console = [], []
        observe_browser_pages(context, errors, console)
        pages = [Events(), Events()]
        for index, page in enumerate(pages):
            context.emit('page', page)
            page.emit('pageerror', RuntimeError('page-' + str(index)))
            page.emit('console', SimpleNamespace(text='message-' + str(index)))
        self.assertEqual(errors, ['page-0', 'page-1'])
        self.assertEqual(console, ['message-0', 'message-1'])

    def test_login_payload_has_local_libraries_before_dashboard_script(self):
        # Synthetic bytes test packaging only; real browser CI consumes the
        # unchanged checksum-pinned assets, not these placeholder files.
        original = (ROOT / 'web/index.html').read_bytes()
        with tempfile.TemporaryDirectory(prefix='vui-proxy-payload-') as tmp:
            root = Path(tmp)
            assets = root / 'assets'
            assets.mkdir()
            for target in ASSETS.values():
                (assets / Path(target).name).write_text('/* synthetic packaging fixture */')
            payload = prepare_browser_payload(root / 'payload', assets)
            html = (payload / 'web/index.html').read_text()
            scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
            self.assertIn('vendor/axios.js', scripts)
            self.assertLess(scripts.index('vendor/axios.js'), scripts.index('js/app.js'))
            self.assertFalse(any('://' in source for source in scripts))
            for source in scripts:
                self.assertTrue((payload / 'web' / source).is_file(), source)
            for path in ('main.py', 'web/proxy.html', 'web/js/proxy.js', 'web/js/account.js'):
                self.assertEqual((payload / path).read_bytes(), (ROOT / path).read_bytes())
        self.assertEqual((ROOT / 'web/index.html').read_bytes(), original)

    def test_missing_pinned_browser_asset_fails_without_cdn_fallback(self):
        with tempfile.TemporaryDirectory(prefix='vui-proxy-payload-') as tmp:
            root = Path(tmp)
            assets = root / 'assets'
            assets.mkdir()
            for target in ASSETS.values():
                if target != 'vendor/axios.js':
                    (assets / Path(target).name).write_text('/* synthetic packaging fixture */')
            with self.assertRaisesRegex(ValueError, 'Missing pinned browser frontend asset: vendor/axios.js'):
                prepare_browser_payload(root / 'payload', assets)
            self.assertFalse((root / 'payload').exists())

    def test_secret_storage_and_offline_assets_contract(self):
        script = (ROOT / 'web/js/proxy.js').read_text()
        html = (ROOT / 'web/proxy.html').read_text()
        self.assertNotIn('localStorage', script)
        self.assertNotIn('sessionStorage', script)
        self.assertNotIn('innerHTML', script)
        self.assertNotIn('console.', script)
        self.assertIn("addEventListener('pagehide'", script)
        self.assertIn("addEventListener('popstate'", script)
        self.assertIn("'If-Match'", script)
        self.assertIn("'Idempotency-Key'", script)
        self.assertIn('crypto.getRandomValues', script)
        self.assertNotRegex(html, r'<script[^>]*src="https?://')
        self.assertIn('socks5h', html)
        self.assertIn('Cloudflare', html)
        self.assertIn('ipify', html)
        self.assertNotIn(UUID, html)
        ids = re.findall(r'id="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(set(re.findall(r"\$\('([^']+)'\)", script)) - set(ids))


class ProxyPageRouteTests(unittest.TestCase):
    def test_page_csp_navigation_and_api_auth_boundary(self):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        import main
        origin = 'https://panel.example.test'
        with patch('app.middleware.auth.configured_origin', return_value=origin), \
                patch('app.middleware.auth.authenticate', return_value=None):
            client = TestClient(main.app, base_url=origin)
            response = client.get('/proxy')
            self.assertEqual(response.status_code, 200)
            self.assertIn('id="proxy-workspace"', response.text)
            self.assertEqual(response.headers['content-security-policy'],
                             client.get('/certificates').headers['content-security-policy'])
            self.assertIn("script-src 'self'", response.headers['content-security-policy'])
            self.assertIn("connect-src 'self'", response.headers['content-security-policy'])
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertEqual(client.get('/api/proxy/settings').status_code, 401)
            self.assertNotIn(UUID, response.text)
        index = (ROOT / 'web/index.html').read_text()
        script = (ROOT / 'web/js/app.js').read_text()
        self.assertIn('index="proxy"', index)
        self.assertIn("if (key === 'proxy') { location.assign('/proxy'); return; }", script)

    def test_ci_uses_exact_head_and_explicit_non_skipping_browser_gate(self):
        import yaml
        workflow = yaml.safe_load((ROOT / '.github/workflows/test.yml').read_text())
        steps = workflow['jobs']['test']['steps']
        checkout = next(step for step in steps if step.get('uses', '').startswith('actions/checkout@'))
        self.assertEqual(checkout['with']['ref'], '${{ github.event.pull_request.head.sha || github.sha }}')
        self.assertIs(checkout['with']['persist-credentials'], False)
        self.assertTrue(any('git rev-parse HEAD' in step.get('run', '') for step in steps))
        browser = next(step for step in steps if step.get('env', {}).get('VUI_PROXY_BROWSER') == '1')
        self.assertIn('test_proxy_browser.py', browser['run'])
        self.assertNotIn('continue-on-error', browser)
        assets = next(step for step in steps if 'scripts/vendor_frontend.py ' in step.get('run', ''))
        self.assertLess(steps.index(assets), steps.index(browser))
        self.assertEqual(browser['env']['VUI_FRONTEND_ASSETS'], '/tmp/vui-editor-web')
        self.assertEqual(assets['run'], 'python scripts/vendor_frontend.py /tmp/vui-editor-web')
        self.assertTrue(any('node --check web/js/proxy.js' in step.get('run', '') for step in steps))


@unittest.skipUnless(os.getenv('VUI_PROXY_BROWSER') == '1', 'explicit proxy browser gate')
class ProxyBrowserTests(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright
        self.tmp = tempfile.TemporaryDirectory(prefix='vui-proxy-browser-')
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        payload = prepare_browser_payload(self.work / 'payload',
                                          Path(os.environ['VUI_FRONTEND_ASSETS']).resolve())
        self.logpath = self.work / 'server.log'
        self.log = self.logpath.open('w')
        self.addCleanup(self.log.close)
        env = {**os.environ, 'PYTHONPATH': str(payload), 'PYTHONDONTWRITEBYTECODE': '1',
               'VUI_PUBLIC_ORIGIN': '', 'VUI_DATA_DIR': str(self.work / 'data'),
               'VUI_BIN_DIR': str(self.work / 'no-cores'), 'VUI_CERTIFICATES_ENABLED': '0'}
        code = '''import sys,uvicorn
from app.services.auth_service import provision_admin
provision_admin('tester',sys.stdin.read())
import main
uvicorn.run(main.app,host='127.0.0.1',port=0,proxy_headers=False,use_colors=False,access_log=False)
'''
        self.server = subprocess.Popen([sys.executable, '-B', '-c', code], cwd=payload,
                                       env=env, stdin=subprocess.PIPE, stdout=self.log,
                                       stderr=subprocess.STDOUT, text=True)
        self.server.stdin.write(PASSWORD)
        self.server.stdin.close()
        self.addCleanup(self.stop_server)
        deadline = time.monotonic() + 15
        match = None
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                self.fail(self.logpath.read_text())
            match = re.search(r'Uvicorn running on http://127\.0\.0\.1:(\d+)', self.logpath.read_text())
            if match:
                break
            time.sleep(.05)
        self.assertIsNotNone(match, self.logpath.read_text())
        self.base = 'http://127.0.0.1:' + match.group(1)
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        executable = os.environ.get('VUI_BROWSER_EXECUTABLE')
        if not executable and not Path(self.playwright.chromium.executable_path).exists():
            executable = shutil.which('chromium') or shutil.which('chromium-browser')
        options = {'headless': True}
        if executable:
            options['executable_path'] = executable
        self.browser = self.playwright.chromium.launch(**options)
        self.addCleanup(self.browser.close)
        self.context = self.browser.new_context(viewport={'width': 390, 'height': 844})
        self.external, self.errors, self.console = [], [], []
        observe_browser_pages(self.context, self.errors, self.console)
        self.page = self.context.new_page()
        def guard(route):
            if not route.request.url.startswith(self.base + '/'):
                self.external.append(route.request.url)
                route.abort()
            else:
                route.continue_()
        self.context.route('**/*', guard)
        self.login(self.page)

    def stop_server(self):
        self.server.terminate()
        try:
            self.server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.server.kill()
            self.server.wait(timeout=5)

    def login(self, page):
        from playwright.sync_api import expect
        page.goto(self.base + '/login')
        page.locator('#login-name').fill('tester')
        page.locator('#login-password').fill(PASSWORD)
        page.get_by_role('button', name='登录面板', exact=True).click()
        page.wait_for_url('**/ui/')
        # Wait for the real dashboard to mount, rather than navigating away
        # before its scripts execute. Keep every login error/request recorded.
        expect(page.get_by_role('menuitem', name='代理与中转', exact=True)).to_be_visible()
        self.assertTrue(page.evaluate("typeof axios === 'function' && typeof Vue.createApp === 'function'"))
        self.assertEqual(self.external, [])
        self.assertEqual(self.errors, [])

    def open_page(self, page=None):
        from playwright.sync_api import expect
        page = page or self.page
        # Exercise the CSP-protected M3 page backed by authenticated APIs.
        page.goto(self.base + '/proxy')
        expect(page.locator('#proxy-workspace')).to_be_visible()
        expect(page.locator('#refresh')).to_be_enabled()
        return page

    def open_inbound_view(self, page=None):
        """Navigate the mounted dashboard through its real menu and observe its GET.

        Changing only /ui/#inbounds is same-document navigation after login;
        it does not run Vue setup again or select a new dashboard view.
        """
        page = page or self.page
        with page.expect_response(lambda response: urlparse(response.url).path == '/api/inbounds'
                                  and response.request.method == 'GET') as loaded:
            page.get_by_role('menuitem', name='入站节点', exact=True).click()
        self.assertEqual(loaded.value.status, 200, loaded.value.text())
        return page

    def draft_only(self):
        self.context.route('**/api/proxy/capabilities', lambda route: route.fulfill(
            json={'apply_available': False, 'diagnostics_available': False}))

    def import_text(self, content=URI, source='uri', page=None):
        from playwright.sync_api import expect
        page = page or self.page
        page.locator('#open-import').click()
        page.locator('#import-format').select_option(source)
        page.locator('#import-content').fill(content)
        page.locator('#preview-import').click()
        expect(page.locator('#import-preview')).to_be_visible()
        expect_import_fieldset(page, False, expect)

    def save_import(self, index=0, page=None):
        from playwright.sync_api import expect
        page = page or self.page
        page.locator(f'#candidates input[value="{index}"]').check()
        for checkbox in page.locator('#ignored-sections input').all():
            checkbox.check()
        page.locator('#save-import').click()
        expect(page.locator('#import-dialog')).not_to_be_visible()
        expect(page.locator('#notice')).to_contain_text('上游已保存')
        expect(page.locator('#import-content')).to_have_value('')
        expect(page.locator('#import-file')).to_have_value('')

    def assert_private(self):
        self.assertEqual(self.page.evaluate('Object.keys(localStorage).length+Object.keys(sessionStorage).length'), 0)
        self.assertNotIn(UUID, self.page.content())
        self.assertNotIn(PASSWORD, self.page.content())
        self.assertNotIn(UUID, self.logpath.read_text())
        self.assertNotIn(PASSWORD, self.logpath.read_text())
        self.assertFalse(any(UUID in message or PASSWORD in message for message in self.console))
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
        self.assertEqual(self.external, [])
        self.assertEqual(self.errors, [])

    def test_mocked_tailscale_readonly_card_disabled_stale_failure_and_history(self):
        from playwright.sync_api import expect
        page = self.open_page()
        expect(page.locator('#tailscale-card')).to_be_hidden()
        self.assertEqual(self.context.request.get(self.base + '/api/management/tailscale').json(), {'enabled': False})
        calls = []
        payload = {'enabled': True, 'state': 'online', 'reason': 'control_connected',
                   'observed_at': 1234, 'stale': False, 'valid_for_seconds': 2,
                   'ignored_secret': '<img src=x>100.64.0.7 private@example.test'}
        def reply(route):
            calls.append(route.request.method)
            route.fulfill(status=200, content_type='application/json', body=json.dumps(payload))
        page.route('**/api/management/tailscale', reply)
        page.reload()
        expect(page.locator('#tailscale-card')).to_be_visible()
        expect(page.locator('#tailscale-state')).to_have_text('控制平面在线')
        self.assertNotIn('100.64.0.7', page.locator('#tailscale-card').inner_text())
        self.assertEqual(page.locator('#tailscale-card img').count(), 0)
        expect(page.locator('#tailscale-state')).to_have_text('未知：请手动刷新', timeout=4000)
        self.assertEqual(calls, ['GET'])
        payload['valid_for_seconds'] = 30
        page.locator('#tailscale-refresh').click()
        expect(page.locator('#tailscale-state')).to_have_text('控制平面在线')
        page.unroute('**/api/management/tailscale', reply)
        page.route('**/api/management/tailscale', lambda route: route.fulfill(status=503))
        page.locator('#tailscale-refresh').click()
        expect(page.locator('#tailscale-state')).to_have_text('未知：请手动刷新')
        page.goto(self.base + '/account')
        page.go_back()
        expect(page.locator('#tailscale-state')).not_to_have_text('控制平面在线')
        self.assertEqual(self.external, [])
        self.assertEqual(self.errors, [])
        self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth'))

    def test_actual_import_four_states_and_write_only_credentials(self):
        from playwright.sync_api import expect
        self.draft_only()
        page = self.open_page()
        expect(page.locator('#draft-mode')).to_contain_text('00')
        expect(page.locator('#upstream-area')).to_be_visible()  # Selection is available before the first explicit relay.
        page.locator('#relay-enabled').check()
        expect(page.locator('#draft-mode')).to_contain_text('01')
        expect(page.locator('#local-settings')).not_to_be_visible()
        self.import_text()
        expect(page.locator('#save-import')).to_be_disabled()
        self.assertEqual(page.locator('#candidates input:checked').count(), 0)
        self.save_import()
        expect(page.locator('#upstream')).to_have_value('')
        upstream_id = self.context.request.get(self.base + '/api/proxy/settings').json()['upstreams'][0]['id']
        page.locator('#upstream').select_option(upstream_id)
        page.locator('#save').click()
        expect(page.locator('#draft-state')).to_contain_text('01')
        expect(page.locator('#active-state')).to_contain_text('尚无')
        expect(page.locator('#apply')).to_be_disabled()
        expect(page.locator('#diagnostics')).to_be_disabled()
        page.locator('#local-enabled').check()
        expect(page.locator('#draft-mode')).to_contain_text('11')
        page.locator('#local-username').fill('synthetic-local')
        page.locator('#local-password').fill('Synthetic-local-secret-1')
        page.locator('#save').click()
        expect(page.locator('#draft-state')).to_contain_text('11')
        expect(page.locator('#local-password')).to_have_value('')
        expect(page.locator('#local-username')).to_have_value('')
        expect(page.locator('#credential-state')).to_contain_text('密码已设置')
        first = self.context.request.get(self.base + '/api/proxy/settings').json()
        page.locator('#relay-enabled').uncheck()
        page.locator('#save').click()
        expect(page.locator('#draft-state')).to_contain_text('10')
        retained = self.context.request.get(self.base + '/api/proxy/settings').json()
        self.assertEqual(retained['settings']['local_username'], 'synthetic-local')
        self.assertTrue(retained['settings']['local_password_set'])
        self.assertNotEqual(first['revision'], retained['revision'])
        page.locator('#local-enabled').uncheck()
        expect(page.locator('#upstream-area')).to_be_visible()  # Selection is available before the first explicit relay.
        page.locator('#save').click()
        expect(page.locator('#draft-state')).to_contain_text('00')
        page.reload()
        expect(page.locator('#draft-state')).to_contain_text('00')
        page.locator('#relay-enabled').check()
        expect(page.locator('#upstream')).to_have_value(upstream_id)
        self.assertEqual(len(self.context.request.get(self.base + '/api/proxy/settings').json()['upstreams']), 1)
        self.assert_private()

    def test_actual_json_yaml_acknowledgement_upload_limits_and_safe_text(self):
        from playwright.sync_api import expect
        self.draft_only()
        page = self.open_page()
        page.locator('#relay-enabled').check()
        content = json.dumps({'outbounds': [outbound('<img src=x onerror=alert(1)>'), outbound('Second candidate'),
                                            {'protocol': 'trojan', 'tag': 'Unsupported candidate'}],
                              'dns': {'servers': ['1.1.1.1']}, 'routing': {'rules': []}})
        self.import_text(content, 'v2ray-json')
        self.assertEqual(page.locator('#candidates input').count(), 3)
        self.assertEqual(page.locator('#candidates img').count(), 0)
        expect(page.locator('#candidates')).to_contain_text('<img src=x onerror=alert(1)>')
        expect(page.locator('#candidates input[value="2"]')).to_be_disabled()
        page.locator('#candidates input[value="1"]').check()
        expect(page.locator('#save-import')).to_be_disabled()
        page.locator('#ignored-sections input').first.check()
        expect(page.locator('#save-import')).to_be_disabled()
        self.save_import(index=1)
        page.locator('#open-import').click()
        page.locator('#import-format').select_option('clash-yaml')
        yaml = f'''proxies:
  - name: YAML candidate
    type: vless
    server: upstream.example.test
    port: 443
    uuid: {UUID}
    tls: true
    servername: upstream.example.test
    udp: false
rules:
  - MATCH,anything
'''
        page.locator('#import-file').set_input_files({'name': 'synthetic.yaml', 'mimeType': 'text/yaml', 'buffer': yaml.encode()})
        expect(page.locator('#import-content')).to_have_value(yaml)
        page.locator('#preview-import').click()
        expect(page.locator('#import-preview')).to_be_visible()
        self.save_import()
        page.locator('#open-import').click()
        page.locator('#import-file').set_input_files({'name': 'too-large.txt', 'mimeType': 'text/plain', 'buffer': b'x' * (1048576 + 1)})
        expect(page.locator('#import-notice')).to_contain_text('超过 1 MiB')
        expect(page.locator('#import-content')).to_have_value('')
        page.locator('#import-file').set_input_files({'name': 'invalid.txt', 'mimeType': 'text/plain', 'buffer': b'\xff\xfe'})
        expect(page.locator('#import-notice')).to_contain_text('有效 UTF-8')
        page.locator('#import-content').fill('界' * 349526)
        expect(page.locator('#import-content')).to_have_value('')
        expect(page.locator('#import-notice')).to_contain_text('超过 1 MiB')
        page.locator('#close-import').click()
        result = self.context.request.get(self.base + '/api/proxy/settings').json()
        self.assertEqual(len(result['upstreams']), 2)
        self.assertNotIn(UUID, json.dumps(result))
        # Independent server enforcement: bypassing the UI still cannot submit >1 MiB content.
        response = self.context.request.post(self.base + '/api/proxy/import/preview',
            headers={'X-VUI-Request': '1', 'Origin': self.base},
            data={'format': 'uri', 'content': 'x' * 1048577})
        self.assertIn(response.status, (413, 422))
        self.assert_private()

    def test_actual_udp_capability_rejection_preserves_saved_state(self):
        from playwright.sync_api import expect
        self.draft_only()
        page = self.open_page()
        before_response = self.context.request.get(self.base + '/api/proxy/settings')
        self.assertEqual(before_response.status, 200)
        before = before_response.json()
        writes = []
        self.context.on('request', lambda request: writes.append(request.url)
                        if request.method == 'POST' and request.url.endswith('/api/proxy/profiles') else None)
        page.locator('#relay-enabled').check()
        inputs = [
            ('uri', URI.split('#')[0] + '&packetEncoding=xudp#UDP-candidate'),
            ('clash-yaml', json.dumps({'name': 'UDP candidate', 'type': 'vless',
                 'server': 'upstream.example.test', 'port': 443, 'uuid': UUID,
                 'tls': True, 'servername': 'upstream.example.test', 'udp': True,
                 'packet-encoding': 'xudp'})),
        ]
        for source, content in inputs:
            with self.subTest(source=source):
                self.import_text(content, source)
                expect(page.locator('#candidates')).to_contain_text('尚未获得双开关上游导入资格')
                expect(page.locator('#candidates')).to_contain_text('不会自动删除这些字段')
                expect(page.locator('#candidates input')).to_be_disabled()
                expect(page.locator('#save-import')).to_be_disabled()
                # A real disabled control must not post even after repeated native activation.
                page.locator('#save-import').evaluate('(button) => { button.click(); button.click(); }')
                self.assertEqual(writes, [])
                page.locator('#close-import').click()
                expect(page.locator('#import-dialog')).not_to_be_visible()
                expect(page.locator('#import-content')).to_have_value('')
                expect(page.locator('#import-file')).to_have_value('')
                self.assertEqual(self.context.request.get(self.base + '/api/proxy/settings').json(), before)
        page.reload()
        expect(page.locator('#proxy-workspace')).to_be_visible()
        expect(page.locator('#draft-mode')).to_contain_text('00')
        expect(page.locator('#relay-enabled')).not_to_be_checked()
        expect(page.locator('#local-enabled')).not_to_be_checked()
        self.assertEqual(writes, [])
        self.assert_private()

    def test_actual_two_tabs_conflict_preserves_edits_and_requires_review(self):
        from playwright.sync_api import expect
        self.draft_only()
        first = self.open_page()
        first.locator('#relay-enabled').check()
        self.import_text()
        self.save_import()
        upstream = self.context.request.get(self.base + '/api/proxy/settings').json()['upstreams'][0]['id']
        first.locator('#upstream').select_option(upstream)
        first.locator('#save').click()
        expect(first.locator('#draft-state')).to_contain_text('01')
        second = self.open_page(self.context.new_page())
        first.locator('#local-enabled').check()
        first.locator('#local-username').fill('first-user')
        first.locator('#local-password').fill('Synthetic-first-secret')
        first.locator('#local-port').fill('7991')
        first.locator('#save').click()
        expect(first.locator('#notice')).to_contain_text('草稿已保存')
        second.locator('#local-enabled').check()
        second.locator('#local-username').fill('second-user')
        second.locator('#local-password').fill('Synthetic-second-secret')
        second.locator('#local-port').fill('7992')
        second.locator('#save').click()
        expect(second.locator('#conflict')).to_be_visible()
        expect(second.locator('#local-port')).to_have_value('7992')
        expect(second.locator('#save')).to_be_disabled()
        expect(second.locator('#notice')).to_contain_text('冲突')
        expect(second.locator('#active-state')).to_contain_text('尚无')
        second.locator('#rebase').click()
        second.locator('#save').click()
        expect(second.locator('#notice')).to_contain_text('草稿已保存')
        self.assertEqual(self.context.request.get(self.base + '/api/proxy/settings').json()['settings']['local_port'], 7992)
        first.locator('#refresh').click()
        expect(first.locator('#local-port')).to_have_value('7992')
        second.close()
        self.assert_private()

    def test_cancel_refresh_history_and_expired_session_clear_secrets(self):
        from playwright.sync_api import expect
        self.draft_only()
        page = self.open_page()
        page.locator('#relay-enabled').check()
        # Cancel while an actual browser fetch is unresolved; a late response
        # must not reopen the dialog or restore the raw credential-bearing input.
        held = []
        page.route('**/api/proxy/import/preview', lambda route: held.append(route))
        page.locator('#open-import').click()
        page.locator('#import-content').fill(URI)
        with page.expect_request('**/api/proxy/import/preview'):
            page.locator('#preview-import').click()
        expect_import_fieldset(page, True, expect)
        # Native click() honours inherited fieldset disabling. No forced event
        # dispatch or shortened timeout: repeated activation must not reach the
        # handler and issue another preview while the first fetch is unresolved.
        clicks = page.locator('#preview-import').evaluate('''button => {
            let clicks = 0;
            const record = () => clicks++;
            button.addEventListener('click', record);
            button.click(); button.click();
            button.removeEventListener('click', record);
            return clicks;
        }''')
        self.assertEqual(clicks, 0)
        self.assertEqual(len(held), 1)
        expect(page.locator('#close-import')).to_be_enabled()
        page.locator('#close-import').click()
        expect(page.locator('#import-content')).to_have_value('')
        expect(page.locator('#import-file')).to_have_value('')
        expect(page.locator('#import-name')).to_have_value('')
        expect(page.locator('#save-import')).to_be_disabled()
        # Even a late native activation after close cannot resubmit cleared data.
        page.locator('#preview-import').evaluate('button => button.click()')
        self.assertEqual(len(held), 1)
        held[0].fulfill(json={'candidates': [], 'ignored_sections': [], 'notice_codes': []})
        page.unroute('**/api/proxy/import/preview')
        expect(page.locator('#import-dialog')).not_to_be_visible()
        expect(page.locator('#import-content')).to_have_value('')
        expect(page.locator('#import-file')).to_have_value('')
        expect(page.locator('#import-preview')).not_to_be_visible()
        self.assertEqual(len(held), 1)
        self.import_text()
        page.locator('#close-import').click()
        expect(page.locator('#import-content')).to_have_value('')
        page.locator('#open-import').click()
        expect(page.locator('#import-preview')).not_to_be_visible()
        expect(page.locator('#import-content')).to_have_value('')
        page.locator('#import-content').fill(URI)
        page.reload()
        expect(page.locator('#proxy-workspace')).to_be_visible()
        expect(page.locator('#import-dialog')).not_to_be_visible()
        expect(page.locator('#import-content')).to_have_value('')
        page.locator('#relay-enabled').check()
        page.locator('#open-import').click()
        page.locator('#import-content').fill(URI)
        page.goto(self.base + '/account')
        page.go_back()
        expect(page.locator('#proxy-workspace')).to_be_visible()
        expect(page.locator('#import-content')).to_have_value('')
        page.locator('#relay-enabled').check()
        page.locator('#open-import').click()
        page.locator('#import-content').fill(URI)
        self.context.clear_cookies()
        page.locator('#preview-import').click()
        page.wait_for_url('**/login')
        self.login(page)
        page.goto(self.base + '/proxy')
        expect(page.locator('#proxy-workspace')).to_be_visible()
        expect(page.locator('#import-content')).to_have_value('')
        self.assertEqual(len(self.context.request.get(self.base + '/api/proxy/settings').json()['upstreams']), 0)
        self.assert_private()

    def test_mocked_apply_uncertainty_double_click_and_get_commit_confirmation(self):
        from playwright.sync_api import expect
        state = fixture_snapshot()
        requests = []
        mode = {'apply': 'abort', 'get_active': False}
        def runtime(route):
            path = urlparse(route.request.url).path
            if path.endswith('/capabilities'):
                route.fulfill(json={'apply_available': True, 'diagnostics_available': False})
            elif path.endswith('/settings'):
                current = deepcopy(state)
                if mode['get_active']:
                    current['active'] = {'revision': state['revision'], 'settings': state['settings'], 'core_revision': 'c' * 64}
                route.fulfill(json=current)
            elif path.endswith('/apply'):
                requests.append((route.request.headers.get('idempotency-key'), route.request.post_data_json))
                if mode['apply'] == 'abort':
                    route.abort('failed')
                else:
                    committed = deepcopy(state)
                    committed['active'] = {'revision': state['revision'], 'settings': state['settings'], 'core_revision': 'c' * 64}
                    route.fulfill(json={'applied': True, 'phase': 'committed', 'snapshot': committed})
            elif path.endswith('/check'):
                route.fulfill(json={'valid': True, 'applied': False, 'phase': 'checked', 'revision': state['revision'], 'snapshot': state, 'coverage': [
                    {'inbound_id': 7, 'core': 'sing-box', 'protocol': 'vless', 'transport': 'tcp', 'qualified': True, 'reason': 'QUALIFIED_VLESS_TCP_TLS'}],
                    'pinned_address': '8.8.8.8', 'resolved_at': 1})
            else:
                route.fulfill(status=404, json={'detail': 'not found'})
        self.context.route('**/api/proxy/**', runtime)
        page = self.open_page()
        page.locator('#check').click()
        expect(page.locator('#check-summary')).to_contain_text('未进行 TLS')
        expect(page.locator('#coverage')).to_contain_text('符合本期接入资格')
        page.locator('#apply').click()
        expect(page.locator('#apply-review')).to_be_visible()
        self.assertEqual(requests, [], 'Preflight review must never apply automatically')
        # Two same-turn explicit confirm clicks must produce only one request.
        page.locator('#confirm-apply').evaluate('(button)=>{button.click();button.click();}')
        expect(page.locator('#retry-apply')).to_be_visible()
        self.assertEqual(len(requests), 1)
        self.assertRegex(requests[0][0], r'^[0-9a-f]{64}$')
        self.assertEqual(requests[0][1], {'base_revision': state['revision']})
        expect(page.locator('#active-state')).to_contain_text('尚无')
        mode['apply'] = 'success'
        page.locator('#retry-apply').click()
        expect(page.locator('#notice')).to_contain_text('未确认当前活动修订')
        self.assertEqual(requests[0], requests[1])
        expect(page.locator('#active-state')).to_contain_text('尚无')
        mode['get_active'] = True
        page.locator('#apply').click()
        expect(page.locator('#apply-review')).to_be_visible()
        page.locator('#confirm-apply').click()  # Explicitly starts a new transaction.
        expect(page.locator('#notice')).to_contain_text('重新读取活动修订确认')
        self.assertNotEqual(requests[1][0], requests[2][0])
        expect(page.locator('#active-state')).to_contain_text('01')
        expect(page.locator('#external-state')).to_contain_text('未测')
        self.assert_private()

    def test_mocked_apply_review_invalidations_and_relay_off_warning(self):
        from playwright.sync_api import expect
        state = fixture_snapshot()
        state['active'] = {'revision': 'b' * 64, 'settings': deepcopy(state['settings']), 'core_revision': 'c' * 64}
        state['settings']['relay_enabled'] = False
        requests = []
        def runtime(route):
            path = urlparse(route.request.url).path
            if path.endswith('/capabilities'):
                route.fulfill(json={'apply_available': True})
            elif path.endswith('/settings'):
                route.fulfill(json=state)
            elif path.endswith('/check'):
                route.fulfill(json={'valid': True, 'phase': 'checked', 'applied': False,
                    'revision': state['revision'], 'snapshot': state, 'coverage': [
                    {'inbound_id': 7, 'core': 'sing-box', 'protocol': 'vless', 'transport': 'tcp', 'qualified': True}]})
            elif path.endswith('/apply'):
                requests.append(route.request.post_data_json)
                route.fulfill(status=409, json={'detail': 'PROXY_CAPACITY_EXHAUSTED'})
            else:
                route.fulfill(status=404, json={'detail': 'not found'})
        self.context.route('**/api/proxy/**', runtime)
        page = self.open_page()
        page.locator('#apply').click()
        expect(page.locator('#review-mode')).to_contain_text('00')
        expect(page.locator('#review-disable-warning')).to_be_visible()
        expect(page.locator('#review-disable-warning')).to_contain_text('恢复普通服务器出口')
        expect(page.locator('#review-coverage')).to_contain_text('入口 7')
        self.assertEqual(requests, [])
        page.locator('#relay-enabled').check()
        expect(page.locator('#apply-review')).not_to_be_visible()
        expect(page.locator('#confirm-apply')).to_be_disabled()
        page.locator('#relay-enabled').uncheck()
        page.locator('#apply').click()
        expect(page.locator('#apply-review')).to_be_visible()
        page.locator('#refresh').click()
        expect(page.locator('#apply-review')).not_to_be_visible()
        page.locator('#apply').click()
        expect(page.locator('#apply-review')).to_be_visible()
        page.locator('#confirm-apply').click()
        expect(page.locator('#notice')).to_contain_text('历史容量已满')
        expect(page.locator('#notice')).to_contain_text('原运行配置保持不变')
        self.assertEqual(requests, [{'base_revision': state['revision']}])
        expect(page.locator('#active-state')).to_contain_text('01')
        self.assert_private()

    def test_mocked_strict_export_uses_active_revision_and_download_without_preview(self):
        from playwright.sync_api import expect
        state = fixture_snapshot()
        state['active'] = {'revision': 'b' * 64, 'settings': deepcopy(state['settings']), 'core_revision': 'c' * 64}
        state['settings']['relay_enabled'] = False  # Newer draft is not exportable capability.
        requests = []
        fault = {'code': None}
        def runtime(route):
            path = urlparse(route.request.url).path
            if path.endswith('/capabilities'):
                route.fulfill(json={'apply_available': True, 'strict_exports_available': True})
            elif path.endswith('/settings'):
                route.fulfill(json=state)
            elif '/exports/' in path:
                requests.append((path, route.request.post_data_json))
                if fault['code']:
                    route.fulfill(status=409, json={'detail': fault['code']})
                else:
                    route.fulfill(body='test-only-uuid: ' + UUID, headers={'Content-Type': 'application/yaml',
                        'ETag': '"' + state['active']['revision'] + '"'})
            else:
                route.fulfill(status=404, json={'detail': 'not found'})
        self.context.route('**/api/proxy/**', runtime)
        page = self.open_page()
        expect(page.locator('#export-mihomo')).to_be_enabled()
        page.locator('#export-inbound').fill('7')
        page.locator('#export-server').fill('node.example.test')
        page.locator('#export-mihomo').click()
        expect(page.locator('#export-notice')).to_contain_text('不接受域名')
        self.assertEqual(requests, [])
        page.locator('#export-server').fill('8.8.8.8')
        with page.expect_download() as event:
            page.locator('#export-mihomo').evaluate('(button)=>{button.click();button.click();}')
        downloaded = event.value
        self.assertEqual(downloaded.suggested_filename, 'vui-strict-relay.yaml')
        self.assertEqual(Path(downloaded.path()).read_text(), 'test-only-uuid: ' + UUID)
        expect(page.locator('#export-notice')).to_contain_text('未执行设备端到端验证')
        self.assertEqual(requests, [('/api/proxy/exports/mihomo.yaml',
            {'base_revision': 'b' * 64, 'inbound_id': 7, 'server': '8.8.8.8'})])
        fault['code'] = 'STRICT_EXPORT_STATE_MISMATCH'
        page.locator('#export-singbox').click()
        expect(page.locator('#export-notice')).to_contain_text('严格导出已阻止')
        state['active']['settings']['relay_enabled'] = False
        page.locator('#refresh').click()
        expect(page.locator('#export-mihomo')).to_be_enabled()  # Explicit relay can remain active with default off.
        state['active'] = None
        page.locator('#refresh').click()
        expect(page.locator('#export-mihomo')).to_be_disabled()
        self.assert_private()

    def test_mocked_diagnostics_layers_stale_and_cancellation(self):
        from playwright.sync_api import expect
        state = fixture_snapshot()
        job = {'job_id': 'diagnostic-fixture', 'upstream_id': PROFILE_ID,
               'revision': state['revision'], 'catalog_version': 1, 'state': 'completed',
               'stale': False, 'pinned_address': '8.8.8.8', 'resolved_at': 1,
               'exit_ip': '9.9.9.9', 'stages': [
                   {'stage': stage, 'status': 'passed', 'code': ''}
                   for stage in ('configuration', 'tcp', 'tls_or_reality', 'proxy_forwarding', 'https_exit')
               ] + [{'stage': 'dns', 'status': 'insufficient_evidence', 'code': 'DNS_PATH_BOUND_TO_HTTPS'},
                    {'stage': 'external_client', 'status': 'not_tested', 'code': ''}]}
        starts, cancels = [], []
        def runtime(route):
            path = urlparse(route.request.url).path
            if path.endswith('/capabilities'):
                route.fulfill(json={'apply_available': True, 'diagnostics_available': True})
            elif path.endswith('/settings'):
                route.fulfill(json=state)
            elif path.endswith('/diagnostics'):
                starts.append(route.request.post_data_json)
                route.fulfill(json=job)
            elif '/diagnostics/' in path:
                if route.request.method == 'DELETE':
                    cancels.append(path)
                    job['state'] = 'cancelled'
                route.fulfill(json=job)
            else:
                route.fulfill(status=404, json={'detail': 'not found'})
        self.context.route('**/api/proxy/**', runtime)
        page = self.open_page()
        expect(page.locator('#diagnostics')).to_be_disabled()
        page.locator('#diagnostic-consent').check()
        page.locator('#diagnostics').click()
        expect(page.locator('#diagnostic-summary')).to_contain_text('已结束')
        self.assertEqual(starts, [{'upstream_id': PROFILE_ID, 'base_revision': state['revision']}])
        expect(page.locator('[data-stage="dns"]')).to_have_attribute('data-status', 'insufficient_evidence')
        expect(page.locator('[data-stage="external_client"]')).to_have_attribute('data-status', 'not_tested')
        expect(page.locator('[data-stage="https_exit"]')).to_have_attribute('data-status', 'passed')
        state['revision'] = 'b' * 64
        page.locator('#refresh').click()
        expect(page.locator('#diagnostic-summary')).to_contain_text('不能沿用结果')
        expect(page.locator('[data-stage="https_exit"]')).to_have_attribute('data-status', 'expired')
        job['state'] = 'running'
        job['revision'] = state['revision']
        page.locator('#diagnostics').click()
        expect(page.locator('#cancel-diagnostics')).to_be_visible()
        page.locator('#cancel-diagnostics').click()
        expect(page.locator('#diagnostic-summary')).to_contain_text('已取消')
        self.assertEqual(len(cancels), 1)
        expect(page.locator('#external-state')).to_contain_text('未测')
        self.assert_private()

    @staticmethod
    def policy_row(identity, mode, effective=None, state='applied'):
        effective = effective or ('relay' if mode == 'relay' else 'direct')
        return {'id': identity, 'mutation_revision': format(identity, '064x'),
                'remark': 'policy-node-' + str(identity), 'core': 'sing-box',
                'protocol': 'vless', 'port': 10400 + identity, 'enable': True,
                'routing_mode': mode, 'certificate_id': None, 'credentials': {'has_uuid': True},
                'profile': {'security': 'tls', 'transport': 'direct', 'flow': '',
                            'server_name': 'entry.example.test', 'skip_cert_verify': False},
                'routing_status': {'desired_effective_mode': effective,
                                   'applied_effective_mode': effective if state == 'applied' else None,
                                   'state': state, 'policy_identity': 'd' * 64 if state == 'applied' else None}}

    def test_actual_inbound_instance_conflicts_preserve_target_and_draft(self):
        from inbound_instance_browser import assert_inbound_instance_conflicts
        assert_inbound_instance_conflicts(self)

    def test_mocked_dashboard_status_unavailable_and_recovery(self):
        from dashboard_status_browser import assert_dashboard_status_recovery
        assert_dashboard_status_recovery(self)

    def test_actual_dashboard_responsive_layout_and_navigation(self):
        from dashboard_layout_browser import assert_dashboard_responsive_layout
        assert_dashboard_responsive_layout(self)

    def test_mocked_inbound_policy_editor_create_cancel_reopen_saved_only_and_failure(self):
        from playwright.sync_api import expect
        rows = [self.policy_row(1, 'direct'), self.policy_row(2, 'relay')]
        writes, fault, held = [], {'save': None}, []
        def inbound(route):
            path, method = urlparse(route.request.url).path, route.request.method
            if method == 'GET' and path.endswith('/editor'):
                identity = int(path.split('/')[-2])
                route.fulfill(json=next(row for row in rows if row['id'] == identity))
            elif method == 'GET':
                route.fulfill(json=rows)
            else:
                body = route.request.post_data_json
                writes.append((method, body))
                if fault['save'] == 'hold':
                    held.append(route)
                    return
                if fault['save'] == 'invalid':
                    route.fulfill(status=422, json={'detail': 'Explicit policy rejected by fixture'})
                    return
                if method == 'POST':
                    row = self.policy_row(3, body['routing_mode'], state='pending')
                    row['remark'] = body['remark']
                    rows.append(row)
                else:
                    row = next(item for item in rows if item['id'] == int(path.split('/')[-1]))
                    row.update(body)
                    row['routing_status']['state'] = 'pending'
                    row['routing_status']['applied_effective_mode'] = None
                if fault['save'] == 'saved':
                    route.fulfill(status=409, json={'detail': {'saved': True, 'applied': False, 'message': 'synthetic runtime unavailable'}})
                else:
                    route.fulfill(json=row)
        self.context.route('**/api/inbounds**', inbound)
        page = self.page
        page.set_viewport_size({'width': 1440, 'height': 1000})
        self.open_inbound_view(page)
        expect(page.get_by_text('policy-node-1', exact=True)).to_be_visible()
        page.get_by_role('button', name='添加节点', exact=True).click()
        dialog = page.get_by_role('dialog', name='添加节点', exact=True)
        expect(dialog.locator('#inbound-routing-mode')).to_have_value('inherit')
        dialog.locator('#inbound-routing-mode').select_option('relay')
        expect(dialog).to_contain_text('服务器严格核验')
        dialog.get_by_role('button', name='取消', exact=True).click()
        expect(dialog).not_to_be_visible()
        self.assertEqual(writes, [])
        page.get_by_role('button', name='添加节点', exact=True).click()
        expect(dialog.locator('#inbound-routing-mode')).to_have_value('inherit')
        dialog.get_by_placeholder('例如：US-01').fill('created-policy-node')
        dialog.locator('#inbound-routing-mode').select_option('relay')
        fault['save'] = 'saved'
        dialog.get_by_role('button', name='创建并应用', exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page.locator('.el-message--warning')).to_contain_text('已保存为期望状态')
        row = page.get_by_role('row').filter(has_text='created-policy-node')
        expect(row).to_contain_text('指定中转')
        expect(row).to_contain_text('待应用')
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][1]['routing_mode'], 'relay')
        self.assertNotIn('routing_mode', writes[0][1]['profile'])
        row.get_by_role('button', name='编辑', exact=True).click()
        editor = page.get_by_role('dialog', name='编辑节点', exact=True)
        expect(editor.locator('#inbound-routing-mode')).to_have_value('relay')
        editor.locator('#inbound-routing-mode').select_option('direct')
        fault['save'] = 'invalid'
        editor.get_by_role('button', name='保存并应用', exact=True).click()
        expect(page.locator('.el-message--error')).to_contain_text('Explicit policy rejected')
        expect(editor).to_be_visible()
        expect(editor.locator('#inbound-routing-mode')).to_have_value('direct')
        editor.get_by_role('button', name='取消', exact=True).click()
        page.get_by_role('button', name='刷新节点', exact=True).click()
        row.get_by_role('button', name='编辑', exact=True).click()
        expect(editor.locator('#inbound-routing-mode')).to_have_value('relay')
        editor.locator('#inbound-routing-mode').select_option('direct')
        fault['save'] = 'hold'
        editor.get_by_role('button', name='保存并应用', exact=True).evaluate('(button)=>{button.click();button.click();}')
        expect(editor.get_by_role('button', name='保存并应用', exact=True)).to_be_disabled()
        expect(editor.get_by_role('button', name='取消', exact=True)).to_be_disabled()
        page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.assertEqual(len(writes), 3)
        held.pop().fulfill(json=rows[-1])
        expect(editor).not_to_be_visible()
        page.reload()
        self.open_inbound_view(page)
        row.get_by_role('button', name='编辑', exact=True).click()
        expect(editor.locator('#inbound-routing-mode')).to_have_value('relay')
        editor.get_by_role('button', name='取消', exact=True).click()
        self.assert_private()

    def test_mocked_inbound_saving_lock_preserves_all_six_control_guards(self):
        """Real pinned components, synthetic save replies; not runtime qualification."""
        from playwright.sync_api import expect
        from test_editor_saving_controls import assert_editor_saving_controls
        assert_editor_saving_controls(self)
        row = self.policy_row(7, 'direct')
        row.update(core='xray', remark='saving-lock-fixture')
        row['profile'].update(security='reality', transport='raw', client_fingerprint='chrome')
        held, writes = [], []
        def inbound(route):
            path, method = urlparse(route.request.url).path, route.request.method
            if method == 'GET':
                route.fulfill(json=row if path.endswith('/editor') else [row])
            else:
                writes.append((method, route.request.post_data_json))
                held.append(route)
        self.context.route('**/api/inbounds**', inbound)
        page = self.page
        page.set_viewport_size({'width': 1440, 'height': 1000})
        self.open_inbound_view(page)
        def control(dialog, label):
            return dialog.locator('.el-form-item').filter(has_text=re.compile('^' + label)).locator('.el-select')
        def check_controls(dialog, pending, editing):
            for label in ('Xray', 'sing-box'):
                target = expect(dialog.get_by_role('radio', name=label, exact=True))
                target.to_be_disabled() if pending or editing else target.to_be_enabled()
            for label in ('协议', '传输方式', '传输安全', 'Flow', '浏览器指纹'):
                target = control(dialog, label)
                disabled = pending or (editing and label == '协议')
                if disabled:
                    expect(target.locator('.el-select__wrapper')).to_have_class(re.compile('is-disabled'))
                    expect(target.get_by_role('combobox')).to_be_disabled()
                else:
                    expect(target.locator('.el-select__wrapper')).not_to_have_class(re.compile('is-disabled'))
                    expect(target.get_by_role('combobox')).to_be_enabled()
        try:
            for editing in (False, True):
                if editing:
                    page.get_by_role('row').filter(has_text='saving-lock-fixture').get_by_role('button', name='编辑', exact=True).click()
                else:
                    page.get_by_role('button', name='添加节点', exact=True).click()
                dialog = page.get_by_role('dialog', name='编辑节点' if editing else '添加节点', exact=True)
                if not editing:
                    dialog.get_by_text('Xray', exact=True).click()
                    control(dialog, '传输安全').click()
                    page.get_by_role('option', name='REALITY', exact=True).click()
                remark = 'keep-until-reply-' + ('edit' if editing else 'create')
                dialog.get_by_placeholder('例如：US-01').fill(remark)
                for status in (422, 200):
                    check_controls(dialog, False, editing)
                    before = len(writes)
                    dialog.get_by_role('button', name='保存并应用' if editing else '创建并应用', exact=True).click()
                    expect(dialog.get_by_role('button', name='取消', exact=True)).to_be_disabled()
                    check_controls(dialog, True, editing)
                    deadline = time.monotonic() + 5
                    while not held and time.monotonic() < deadline:
                        page.wait_for_timeout(20)
                    self.assertEqual(len(held), 1)
                    self.assertEqual(len(writes), before + 1)
                    method, body = writes[-1]
                    self.assertEqual(method, 'PUT' if editing else 'POST')
                    self.assertEqual(body['remark'], remark)
                    self.assertEqual(body['profile']['security'], 'reality')
                    self.assertEqual(body['profile']['transport'], 'raw')
                    expect(dialog.get_by_placeholder('例如：US-01')).to_have_value(remark)
                    if status == 422:
                        detail = 'Synthetic saving-lock rejection: ' + ('edit' if editing else 'create')
                        held.pop().fulfill(status=422, json={'detail': detail})
                        expect(page.locator('.el-message--error').filter(has_text=detail)).to_contain_text(detail)
                        expect(dialog).to_be_visible()
                        expect(dialog.get_by_role('button', name='取消', exact=True)).to_be_enabled()
                        check_controls(dialog, False, editing)
                        expect(dialog.get_by_placeholder('例如：US-01')).to_have_value(remark)
                    else:
                        held.pop().fulfill(json=row)
                        expect(dialog).not_to_be_visible()
                        # The dialog hides before list/status refresh finishes.
                        # Its retained native select reflects savingInbound until finally.
                        expect(page.locator('#inbound-routing-mode')).to_be_enabled()
                self.assertEqual(len(writes), 4 if editing else 2)
            self.assert_private()
            print('Node editor saving lock: all six real component guards / create and edit / held synthetic replies / 422 unlock with draft preserved / success close OK')
        finally:
            for route in held:
                route.abort()

    def test_mocked_inbound_policy_delayed_editor_cancellation_and_401_clear(self):
        from playwright.sync_api import expect
        rows = [self.policy_row(1, 'direct'), self.policy_row(2, 'relay')]
        held = []
        def inbound(route):
            path = urlparse(route.request.url).path
            if path.endswith('/1/editor'):
                held.append(route)
            elif path.endswith('/2/editor'):
                route.fulfill(json=rows[1])
            else:
                route.fulfill(json=rows)
        self.context.route('**/api/inbounds**', inbound)
        page = self.page
        page.set_viewport_size({'width': 1440, 'height': 1000})
        self.open_inbound_view(page)
        first = page.get_by_role('row').filter(has_text='policy-node-1')
        second = page.get_by_role('row').filter(has_text='policy-node-2')
        first.get_by_role('button', name='编辑', exact=True).click()
        second.get_by_role('button', name='编辑', exact=True).click()
        editor = page.get_by_role('dialog', name='编辑节点', exact=True)
        expect(editor.locator('#inbound-routing-mode')).to_have_value('relay')
        held.pop().fulfill(json=rows[0])
        expect(editor.locator('#inbound-routing-mode')).to_have_value('relay')
        editor.get_by_role('button', name='取消', exact=True).click()
        first.get_by_role('button', name='编辑', exact=True).click()
        page.get_by_role('button', name='添加节点', exact=True).click()
        page.get_by_role('dialog', name='添加节点', exact=True).get_by_role('button', name='取消', exact=True).click()
        held.pop().fulfill(json=rows[0])
        page.wait_for_timeout(100)
        expect(page.get_by_role('dialog')).not_to_be_visible()
        second.get_by_role('button', name='编辑', exact=True).click()
        expect(editor.locator('#inbound-routing-mode')).to_have_value('relay')
        self.context.route('**/api/auth/me', lambda route: route.fulfill(status=401, json={'detail': 'expired'}))
        self.context.route('**/api/inbounds/2', lambda route: route.fulfill(status=401, json={'detail': 'expired'}))
        editor.get_by_role('button', name='保存并应用', exact=True).click()
        page.wait_for_url('**/login')
        expect(page.locator('#inbound-routing-mode')).to_have_count(0)
        self.assert_private()

    def test_mocked_policy_sources_fingerprint_retry_stale_conflict_and_missing_token(self):
        from playwright.sync_api import expect
        state = fixture_snapshot()
        state['settings']['relay_enabled'] = False
        rows = [self.policy_row(1, 'direct'), self.policy_row(2, 'relay')]
        token = {'value': 'e' * 64, 'apply': 'abort'}
        requests = []
        self.context.route('**/api/inbounds', lambda route: route.fulfill(json=rows))
        def runtime(route):
            path = urlparse(route.request.url).path
            if path.endswith('/capabilities'):
                route.fulfill(json={'apply_available': True})
            elif path.endswith('/settings'):
                route.fulfill(json=state)
            elif path.endswith('/check'):
                checked = {'valid': True, 'phase': 'checked', 'applied': False, 'revision': state['revision'],
                           'snapshot': state, 'coverage': [
                    {'inbound_id': 1, 'core': 'sing-box', 'protocol': 'vless', 'transport': 'tcp',
                     'qualified': False, 'routing_mode': 'direct', 'effective_mode': 'direct'},
                    {'inbound_id': 2, 'core': 'sing-box', 'protocol': 'vless', 'transport': 'tcp',
                     'qualified': True, 'routing_mode': 'relay', 'effective_mode': 'relay'}]}
                if token['value'] is not None:
                    checked['source_fingerprint'] = token['value']
                route.fulfill(json=checked)
            elif path.endswith('/apply'):
                requests.append((route.request.headers['idempotency-key'], route.request.post_data_json))
                if token['apply'] == 'abort':
                    route.abort('failed')
                else:
                    route.fulfill(status=409, json={'detail': 'PROXY_REVISION_CONFLICT'})
            else:
                route.fulfill(status=404, json={'detail': 'not found'})
        self.context.route('**/api/proxy/**', runtime)
        page = self.open_page()
        expect(page.locator('#upstream-area')).to_be_visible()
        page.locator('#apply').click()
        expect(page.locator('#review-coverage')).to_contain_text('指定直连 → 普通直连')
        expect(page.locator('#review-coverage')).to_contain_text('指定中转 → 中转')
        expect(page.locator('#review-revision')).to_contain_text('e' * 64)
        page.locator('#confirm-apply').evaluate('(button)=>{button.click();button.click();}')
        expect(page.locator('#retry-apply')).to_be_visible()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][1], {'base_revision': state['revision'], 'source_fingerprint': 'e' * 64})
        token['value'], token['apply'] = 'f' * 64, 'conflict'
        rows[1]['routing_mode'] = 'inherit'
        page.locator('#retry-apply').click()
        expect(page.locator('#conflict')).to_be_visible()
        expect(page.locator('#apply-review')).not_to_be_visible()
        self.assertEqual(requests[0], requests[1], 'Retry must retain the original exact token and key')
        page.locator('#discard').click()
        page.locator('#apply').click()
        expect(page.locator('#review-revision')).to_contain_text('f' * 64)
        page.locator('#cancel-apply-review').click()
        expect(page.locator('#apply-review')).not_to_be_visible()
        token['value'] = None
        page.locator('#apply').click()
        expect(page.locator('#notice')).to_contain_text('指纹缺失或无效')
        expect(page.locator('#confirm-apply')).to_be_disabled()
        self.assertEqual(len(requests), 2)
        self.assert_private()

    def test_mocked_policy_four_flags_and_selected_strict_export_global_off(self):
        from playwright.sync_api import expect
        state = fixture_snapshot()
        rows = [self.policy_row(1, 'inherit'), self.policy_row(2, 'direct'), self.policy_row(3, 'relay')]
        requests = []
        self.context.route('**/api/inbounds', lambda route: route.fulfill(json=rows))
        def runtime(route):
            path = urlparse(route.request.url).path
            if path.endswith('/capabilities'):
                route.fulfill(json={'apply_available': True, 'strict_exports_available': True})
            elif path.endswith('/settings'):
                route.fulfill(json=state)
            elif '/exports/' in path:
                requests.append(route.request.post_data_json)
                route.fulfill(body='synthetic selected-entry-only config', headers={
                    'Content-Type': 'application/yaml', 'ETag': '"' + state['active']['revision'] + '"'})
            else:
                route.fulfill(status=404, json={'detail': 'not found'})
        self.context.route('**/api/proxy/**', runtime)
        state['active'] = {'revision': 'b' * 64, 'settings': deepcopy(state['settings'])}
        page = self.open_page()
        for local, relay in ((False, False), (True, False), (False, True), (True, True)):
            state['settings']['local_proxy_enabled'] = local
            state['settings']['relay_enabled'] = relay
            state['active']['settings'] = deepcopy(state['settings'])
            rows[0]['routing_status']['desired_effective_mode'] = 'relay' if relay else 'direct'
            rows[0]['routing_status']['applied_effective_mode'] = 'relay' if relay else 'direct'
            page.locator('#refresh').click()
            expect(page.locator('#draft-state')).to_contain_text(('1' if local else '0') + ('1' if relay else '0'))
            expect(page.locator('#inbound-policy-summary')).to_contain_text('已保存期望中转入口：' + ('2' if relay else '1'))
            expect(page.locator('#inbound-policy-list')).to_contain_text('入口 2 · 指定直连 · 已应用普通直连')
            expect(page.locator('#inbound-policy-list')).to_contain_text('入口 3 · 指定中转 · 已应用 中转')
            expect(page.locator('#upstream-area')).to_be_visible()
        state['settings']['local_proxy_enabled'] = state['settings']['relay_enabled'] = False
        state['active']['settings'] = deepcopy(state['settings'])
        page.locator('#refresh').click()
        page.locator('#export-server').fill('8.8.8.8')
        page.locator('#export-inbound').fill('2')
        expect(page.locator('#export-mihomo')).to_be_disabled()
        expect(page.locator('#export-state')).to_contain_text('所选入口未确认已应用中转')
        page.locator('#export-inbound').fill('3')
        expect(page.locator('#export-mihomo')).to_be_enabled()
        with page.expect_download() as event:
            page.locator('#export-mihomo').click()
        self.assertEqual(Path(event.value.path()).read_text(), 'synthetic selected-entry-only config')
        self.assertEqual(requests, [{'base_revision': 'b' * 64, 'inbound_id': 3, 'server': '8.8.8.8'}])
        rows[2]['routing_status']['state'] = 'pending'
        page.locator('#refresh').click()
        expect(page.locator('#export-notice')).to_have_text('')
        expect(page.locator('#export-mihomo')).to_be_disabled()
        expect(page.locator('#inbound-policy-list')).to_contain_text('待应用')
        rows[2].pop('routing_status')
        page.locator('#refresh').click()
        expect(page.locator('#inbound-policy-list')).to_contain_text('已应用出口未确认')
        expect(page.locator('#inbound-policy-summary')).to_contain_text('数量未确认')
        self.assert_private()


if __name__ == '__main__':
    unittest.main()
