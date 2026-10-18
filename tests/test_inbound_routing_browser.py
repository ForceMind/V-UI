"""One real Chromium -> authenticated API -> SQLite/store -> official core flow.

VUI_INBOUND_ROUTING_BROWSER=1 is an explicit, fail-closed gate. It requires the
existing checksum-pinned VUI_FRONTEND_ASSETS and VUI_TEST_CORES directories.
No successful editor/check/apply/status response is substituted. The only
browser interception rejects requests outside the isolated panel origin.
This proves UI/persistence/runtime activation, not forwarding or a phone path.
"""
import ast
import hashlib
import inspect
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
from types import SimpleNamespace
import unittest

from scripts.fetch_test_cores import PINS
from test_proxy_browser import prepare_browser_payload, observe_browser_pages


ROOT = Path(__file__).resolve().parents[1]
PASSWORD = 'Synthetic-inbound-routing-browser-password!'
NODE_NAME = 'Synthetic explicit direct'
SERVER_CODE = '''import sys,uvicorn
from app.models.database import init_db
from app.services.auth_service import provision_admin
init_db()
provision_admin('routing-tester',sys.stdin.read())
import main
uvicorn.run(main.app,host='127.0.0.1',port=0,proxy_headers=False,use_colors=False,access_log=False)
'''


def panel_origin_guard(base, external):
    # Exactly one callback argument: Playwright forwards Request to a second
    # argument even when that parameter has a default intended as a closure.
    def guard(route):
        if route.request.url.startswith(base + '/'):
            route.continue_()
        else:
            external.append(route.request.url)
            route.abort()
    return guard


def verified_dependencies(environment):
    """Read/check provenance only; never download or execute a fallback binary."""
    for name in ('VUI_TEST_CORES', 'VUI_FRONTEND_ASSETS'):
        if not environment.get(name):
            raise ValueError('Explicit inbound routing browser gate requires ' + name)
    cores = Path(environment['VUI_TEST_CORES']).resolve()
    assets = Path(environment['VUI_FRONTEND_ASSETS']).resolve()
    evidence = json.loads((cores / 'provenance.json').read_text())['sing-box']
    pin = PINS[evidence['target']]['sing-box']
    if any(evidence.get(key) != value for key, value in pin.items()):
        raise ValueError('Official sing-box provenance does not match repository pins')
    binary = cores / 'sing-box'
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if digest != evidence['binary_sha256'] or not os.access(binary, os.X_OK):
        raise ValueError('Official sing-box binary checksum/permissions mismatch')
    expected = json.loads((ROOT / 'deploy/frontend-pins.json').read_text())
    frontend = json.loads((assets / 'PROVENANCE.json').read_text())
    for name, pin in expected.items():
        item = frontend[name]
        if any(item.get(key) != value for key, value in pin.items()):
            raise ValueError('Frontend provenance does not match repository pins')
        for filename in pin['files'].values():
            actual = hashlib.sha256((assets / filename).read_bytes()).hexdigest()
            if actual != item['selected_sha256'][filename]:
                raise ValueError('Frontend asset checksum mismatch')
    return cores, assets, digest


def stop_owned_panel(process):
    """Bounded cleanup, including watchdog/core descendants after early failure."""
    import psutil
    try:
        descendants = psutil.Process(process.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        descendants = []
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    # psutil Process methods guard PID reuse with creation time. Only processes
    # discovered below this fixture's own panel are eligible for termination.
    for child in descendants:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(descendants, timeout=3)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(alive, timeout=3)
    if alive:
        raise AssertionError('Owned panel/core processes did not exit within cleanup deadline')


class InboundRoutingBrowserControls(unittest.TestCase):
    def test_origin_guard_continues_real_api_and_rejects_external_origins(self):
        base, external = 'http://127.0.0.1:12345', []
        guard = panel_origin_guard(base, external)
        self.assertEqual(len(inspect.signature(guard).parameters), 1)
        for suffix in ('/login', '/ui/vendor/vue.js', '/api/inbounds',
                       '/api/proxy/check', '/api/proxy/apply', '/api/cores/status'):
            calls = []
            guard(SimpleNamespace(request=SimpleNamespace(url=base + suffix),
                                  continue_=lambda: calls.append('continue'),
                                  abort=lambda: calls.append('abort')))
            self.assertEqual(calls, ['continue'])
        for url in (base + '.evil.example/api/proxy/apply', 'https://cdn.example/vue.js',
                    'http://127.0.0.1:12346/api/inbounds'):
            calls = []
            guard(SimpleNamespace(request=SimpleNamespace(url=url),
                                  continue_=lambda: calls.append('continue'),
                                  abort=lambda: calls.append('abort')))
            self.assertEqual(calls, ['abort'])
        self.assertEqual(len(external), 3)

    def test_real_flow_has_no_success_response_or_runtime_substitutions(self):
        tree = ast.parse(inspect.getsource(InboundRoutingBrowserTests))
        calls = [node.func for node in ast.walk(tree) if isinstance(node, ast.Call)]
        forbidden = {'fulfill', 'route_from_har', 'patch', 'mock', 'evaluate', 'add_init_script'}
        self.assertFalse(any(isinstance(call, ast.Attribute) and call.attr in forbidden
                             for call in calls))
        self.assertNotIn('override', SERVER_CODE)
        self.assertIn('import main', SERVER_CODE)
        self.assertIn('uvicorn.run(main.app', SERVER_CODE)
        self.assertNotIn('Inbound(', SERVER_CODE)  # No seeded node replaces the editor.

    def test_explicit_gate_rejects_missing_and_tampered_dependencies(self):
        with self.assertRaisesRegex(ValueError, 'requires VUI_TEST_CORES'):
            verified_dependencies({})
        with tempfile.TemporaryDirectory(prefix='vui-browser-checksum-control-') as directory:
            root = Path(directory)
            binary = root / 'sing-box'
            binary.write_bytes(b'synthetic checksum control, never executable code')
            binary.chmod(0o700)
            evidence = {**PINS['x86_64-gnu']['sing-box'], 'target': 'x86_64-gnu',
                        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
            (root / 'provenance.json').write_text(json.dumps({'sing-box': evidence}))
            binary.write_bytes(b'tampered checksum control')
            with self.assertRaisesRegex(ValueError, 'binary checksum'):
                verified_dependencies({'VUI_TEST_CORES': directory, 'VUI_FRONTEND_ASSETS': directory})


@unittest.skipUnless(os.getenv('VUI_INBOUND_ROUTING_BROWSER') == '1',
                     'explicit VUI_INBOUND_ROUTING_BROWSER=1 real API/core browser gate')
class InboundRoutingBrowserTests(unittest.TestCase):
    def test_first_direct_node_real_editor_check_review_apply_and_refresh(self):
        from playwright.sync_api import sync_playwright, expect
        import psutil
        from loopback_helpers import certificate_files, unused_port

        cores, assets, binary_digest = verified_dependencies(os.environ)
        started_at = time.time()
        temporary = tempfile.TemporaryDirectory(prefix='vui-inbound-routing-browser-')
        self.addCleanup(temporary.cleanup)
        work = Path(temporary.name)
        payload = prepare_browser_payload(work / 'payload', assets)
        _, certificate, key = certificate_files(work, 'inbound-routing')
        data = work / 'data'
        log = (work / 'panel.log').open('w')
        self.addCleanup(log.close)
        environment = {**os.environ, 'PYTHONPATH': str(payload), 'PYTHONDONTWRITEBYTECODE': '1',
                       'VUI_DATA_DIR': str(data), 'VUI_BIN_DIR': str(cores),
                       'VUI_PUBLIC_ORIGIN': '', 'VUI_CERTIFICATES_ENABLED': '0'}
        process = subprocess.Popen([sys.executable, '-B', '-c', SERVER_CODE], cwd=payload,
                                   env=environment, stdin=subprocess.PIPE, stdout=log,
                                   stderr=subprocess.STDOUT, text=True)
        self.addCleanup(stop_owned_panel, process)
        process.stdin.write(PASSWORD)
        process.stdin.close()
        base = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            self.assertIsNone(process.poll(), 'Isolated panel exited before readiness')
            match = re.search(r'Uvicorn running on http://127\.0\.0\.1:(\d+)',
                              (work / 'panel.log').read_text())
            if match:
                base = 'http://127.0.0.1:' + match.group(1)
                break
            time.sleep(.05)
        self.assertIsNotNone(base, 'Isolated panel did not become ready within 20 seconds')
        playwright = sync_playwright().start()
        self.addCleanup(playwright.stop)
        options = {'headless': True, 'timeout': 15000}
        executable = os.getenv('VUI_TEST_CHROMIUM') or os.getenv('VUI_BROWSER_EXECUTABLE')
        if not executable and not Path(playwright.chromium.executable_path).exists():
            executable = shutil.which('chromium') or shutil.which('chromium-browser')
        if executable:
            options['executable_path'] = executable
        browser = playwright.chromium.launch(**options)
        self.addCleanup(browser.close)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, service_workers='block')
        self.addCleanup(context.close)
        context.set_default_timeout(10000)
        context.set_default_navigation_timeout(15000)
        errors, console, external, writes = [], [], [], []
        observe_browser_pages(context, errors, console)
        context.route('**/*', panel_origin_guard(base, external))
        page = context.new_page()
        page.on('request', lambda request: writes.append(request)
                if request.method in ('POST', 'PUT', 'DELETE') and '/api/' in request.url else None)

        def read(endpoint):
            response = context.request.get(base + endpoint, timeout=10000)
            self.assertEqual(response.status, 200, 'Real authenticated GET failed: ' + endpoint)
            return response.json()

        def response_for(endpoint, method):
            return lambda response: response.url == base + endpoint and response.request.method == method

        def nodes():
            page.get_by_role('menuitem', name='入站节点', exact=True).click()
            return page.locator('.el-table__row').filter(has_text=NODE_NAME)

        page.goto(base + '/login')
        page.locator('#login-name').fill('routing-tester')
        page.locator('#login-password').fill(PASSWORD)
        page.get_by_role('button', name='登录面板', exact=True).click()
        page.wait_for_url('**/ui/')
        nodes()
        initial = read('/api/proxy/settings')
        self.assertIsNone(initial['active'])
        self.assertEqual(initial['upstreams'], [])
        page.get_by_role('button', name='添加节点', exact=True).click()
        dialog = page.get_by_role('dialog')
        dialog.get_by_text('sing-box', exact=True).click()
        dialog.get_by_placeholder('例如：US-01').fill(NODE_NAME)
        port = unused_port()
        dialog.get_by_role('spinbutton').fill(str(port))
        expect(dialog.locator('#inbound-routing-mode')).to_have_value('inherit')
        dialog.locator('#inbound-routing-mode').select_option('direct')
        dialog.locator('.el-form-item').filter(has_text='传输安全').locator('.el-select').click()
        page.get_by_role('option', name='TLS', exact=True).click()
        dialog.get_by_placeholder('example.com', exact=True).fill('vpn.example.test')
        dialog.get_by_placeholder('/etc/letsencrypt/live/example.com/fullchain.pem').fill(str(certificate))
        dialog.get_by_placeholder('/etc/letsencrypt/live/example.com/privkey.pem').fill(str(key))
        expect(dialog.get_by_role('checkbox', name='客户端跳过证书校验（仅测试 / 自签证书）')).not_to_be_checked()
        with page.expect_response(response_for('/api/inbounds', 'POST'), timeout=20000) as created:
            dialog.get_by_role('button', name='创建并应用', exact=True).click()
        self.assertEqual(created.value.status, 409)
        self.assertIs(created.value.json()['detail']['saved'], True)
        self.assertIs(created.value.json()['detail']['applied'], False)
        expect(dialog).not_to_be_visible()
        request = created.value.request.post_data_json
        self.assertEqual((request['routing_mode'], request['core'], request['protocol'], request['enable']),
                         ('direct', 'sing-box', 'vless', True))
        self.assertEqual(request['profile']['transport'], 'direct')
        self.assertIs(request['profile']['skip_cert_verify'], False)
        saved = read('/api/inbounds')
        self.assertEqual(len(saved), 1)
        identity = saved[0]['id']
        self.assertEqual(saved[0]['routing_mode'], 'direct')
        self.assertEqual(saved[0]['routing_status'], {'desired_effective_mode': 'direct',
            'applied_effective_mode': None, 'state': 'unknown', 'policy_identity': None})
        self.assertIsNone(read('/api/proxy/settings')['active'])
        self.assertFalse(read('/api/cores/status')['sing-box']['running'])
        with sqlite3.connect(data / 'v-ui.db') as database:
            self.assertEqual(database.execute('SELECT routing_mode,enable FROM inbounds WHERE id=?',
                                             (identity,)).fetchone(), ('direct', 1))
        page.reload()
        row = nodes()
        expect(row).to_contain_text('指定直连')
        expect(row).to_contain_text('已应用出口未确认')
        row.get_by_role('button', name='编辑', exact=True).click()
        expect(dialog.locator('#inbound-routing-mode')).to_have_value('direct')
        dialog.locator('#inbound-routing-mode').select_option('inherit')
        dialog.get_by_role('button', name='取消', exact=True).click()
        self.assertEqual(read(f'/api/inbounds/{identity}/editor')['routing_mode'], 'direct')
        self.assertIsNone(read('/api/proxy/settings')['active'])

        page.get_by_role('menuitem', name='代理与中转', exact=True).click()
        page.wait_for_url('**/proxy')
        expect(page.locator('#local-enabled')).not_to_be_checked()
        expect(page.locator('#relay-enabled')).not_to_be_checked()
        expect(page.locator('#upstream')).to_have_value('')
        expect(page.locator('#check')).to_be_enabled()
        with page.expect_response(response_for('/api/proxy/check', 'POST'), timeout=20000) as checked:
            page.locator('#check').click()
        self.assertEqual(checked.value.status, 200)
        first_check = checked.value.json()
        self.assertIs(first_check['valid'], True)
        self.assertIs(first_check['applied'], False)
        self.assertEqual(first_check['phase'], 'checked')
        self.assertIsNone(first_check['snapshot']['active'])
        expect(page.locator('#check-summary')).to_contain_text('核心配置检查通过')
        with page.expect_response(response_for('/api/proxy/check', 'POST'), timeout=20000) as reviewed:
            page.locator('#apply').click()
        self.assertEqual(reviewed.value.status, 200)
        review = reviewed.value.json()
        self.assertEqual(review['snapshot'], first_check['snapshot'])
        self.assertEqual(review['candidate_revision'], first_check['candidate_revision'])
        self.assertEqual(review['source_fingerprint'], first_check['source_fingerprint'])
        fingerprint, revision = review['source_fingerprint'], review['revision']
        self.assertRegex(fingerprint, r'^[0-9a-f]{64}$')
        self.assertEqual(revision, initial['revision'])
        self.assertEqual(reviewed.value.request.post_data_json, {'base_revision': revision})
        expect(page.locator('#apply-review')).to_be_visible()
        expect(page.locator('#review-revision')).to_contain_text(fingerprint)
        expect(page.locator('#review-revision')).to_contain_text(revision)
        expect(page.locator('#review-upstream')).to_have_text('没有使用中的上游。')
        self.assertIsNone(read('/api/proxy/settings')['active'])
        with page.expect_response(response_for('/api/proxy/apply', 'POST'), timeout=25000) as applied:
            page.locator('#confirm-apply').click()
        self.assertEqual(applied.value.status, 200)
        self.assertEqual(applied.value.request.post_data_json,
                         {'base_revision': revision, 'source_fingerprint': fingerprint})
        self.assertRegex(applied.value.request.headers['idempotency-key'], r'^[0-9a-f]{64}$')
        result = applied.value.json()
        self.assertIs(result['applied'], True)
        self.assertEqual(result['phase'], 'committed')
        self.assertEqual(result['candidate_revision'], review['candidate_revision'])
        expect(page.locator('#notice')).to_contain_text('应用事务已提交，并已重新读取活动修订确认')
        snapshot = read('/api/proxy/settings')
        self.assertEqual(snapshot, result['snapshot'])
        self.assertEqual(snapshot['active']['revision'], revision)
        self.assertIsNone(snapshot['pending'])
        self.assertFalse(snapshot['settings']['local_proxy_enabled'])
        self.assertFalse(snapshot['settings']['relay_enabled'])
        self.assertIsNone(snapshot['settings']['selected_upstream_id'])
        self.assertEqual(snapshot['upstreams'], [])
        status = read('/api/cores/status')['sing-box']
        self.assertTrue(status['running'])
        self.assertTrue(status['enabled'])
        self.assertFalse(status['dirty'])
        self.assertEqual(status['version_required'], '1.14.2')
        self.assertEqual(status['applied_revision'], review['candidate_revision'])
        self.assertEqual(status['desired_revision'], status['applied_revision'])
        # The public PID belongs to the runtime watchdog. Verify its actual
        # child executable, birth, parentage, config bytes and owned listener.
        watchdog = psutil.Process(status['pid'])
        self.assertEqual(watchdog.ppid(), process.pid)
        children = watchdog.children()
        self.assertEqual(len(children), 1)
        core = children[0]
        birth = core.create_time()
        self.assertGreaterEqual(birth, started_at - 1)
        self.assertEqual(Path(core.exe()).resolve(), (cores / 'sing-box').resolve())
        self.assertEqual(hashlib.sha256(Path(core.exe()).read_bytes()).hexdigest(), binary_digest)
        command = core.cmdline()
        self.assertEqual(command[1:3], ['run', '-c'])
        raw = Path(command[3]).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), status['applied_revision'])
        self.assertTrue(any(connection.status == psutil.CONN_LISTEN and connection.laddr.port == port
                            for connection in core.net_connections(kind='tcp')))
        self.assertEqual(psutil.Process(core.pid).create_time(), birth)
        state = json.loads((data / 'proxy-state/state.json').read_text())
        transaction = state['transactions'][state['active_transaction_id']]
        runtime_context = transaction['runtime_context']
        self.assertEqual(state['schema_version'], 2)
        self.assertEqual(runtime_context['source_fingerprint'], fingerprint)
        self.assertEqual(runtime_context['candidate_core_revision'], status['applied_revision'])
        self.assertFalse(runtime_context['requires_upstream'])
        self.assertIsNone(runtime_context['endpoint'])
        self.assertEqual([(item['id'], item['mode'], item['effective'])
                          for item in runtime_context['plan_manifest']], [(identity, 'direct', 'direct')])
        applied_row = read('/api/inbounds')[0]
        self.assertEqual(applied_row['routing_status'], {'desired_effective_mode': 'direct',
            'applied_effective_mode': 'direct', 'state': 'applied',
            'policy_identity': runtime_context['policy_identity']})
        page.goto(base + '/ui/')
        row = nodes()
        expect(row).to_contain_text('指定直连')
        expect(row).to_contain_text('已应用普通直连')
        page.reload()
        expect(nodes()).to_contain_text('已应用普通直连')
        endpoints = [request.url.removeprefix(base) for request in writes]
        self.assertEqual(endpoints.count('/api/inbounds'), 1)
        self.assertEqual(endpoints.count('/api/proxy/check'), 2)
        self.assertEqual(endpoints.count('/api/proxy/apply'), 1)
        self.assertFalse(any(request.method == 'PUT' for request in writes), 'Cancelled edit was submitted')
        self.assertFalse(external, 'Browser attempted a request outside the isolated panel')
        self.assertFalse(errors, 'Browser page error occurred')
        for secret in (PASSWORD, key.read_text(), json.loads(raw)['inbounds'][0]['users'][0]['uuid']):
            self.assertFalse(any(secret in message for message in console), 'Synthetic secret reached browser console')


if __name__ == '__main__':
    unittest.main()
