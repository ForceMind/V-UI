"""D1 real-browser exports between two isolated, authenticated panel instances.

Successful exports always come from the real HTTP endpoint and seeded node
models. Both import preview and save use the target's real parser/private store.
Delayed-response and clipboard-denial fixtures exercise browser lifecycle only;
they do not establish runtime forwarding (covered by the double-hop gate).
VUI_TCP_UPSTREAM_BROWSER=1 requires all dependencies; it never skips on failure.
"""
from copy import deepcopy
from contextlib import contextmanager
import faulthandler
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from types import SimpleNamespace
import unittest

from test_proxy_browser import prepare_browser_payload, observe_browser_pages
from test_upstream_import_capabilities import inbound, UUID, SHORT_ID, HOST

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = 'Synthetic-tcp-upstream-browser-password!'


def report_suspended_test_stack(test_greenlet, stream):
    """faulthandler sees Playwright's dispatcher, not its suspended sync caller."""
    frame = test_greenlet.gr_frame
    print('Suspended browser-test caller:', file=stream, flush=True)
    if frame is not None:
        traceback.print_stack(frame, file=stream)
    else:
        print('Test greenlet is not suspended.', file=stream)
    stream.flush()


def isolated_panel_route_guard(base, external):
    """Capture the panel origin outside Playwright's (route, request) arguments."""
    # Playwright's sync wrapper counts *all* declared parameters, including
    # defaults and keyword-only parameters, before forwarding positional args.
    # Keep this callback single-argument; a default base parameter is unsafe.
    def guard(route):
        if not route.request.url.startswith(base + '/'):
            external.append(route.request.url)
            route.abort()
        else:
            route.continue_()
    return guard


def export_fixture_nodes():
    nodes = []
    for index, reality in enumerate((False, True), 1):
        node = inbound(reality)
        node.id = index
        node.remark = 'Synthetic ' + ('REALITY' if reality else 'TLS')
        node.port = 10442 + index
        nodes.append(deepcopy(vars(node)))
    unsupported = deepcopy(nodes[0])
    unsupported.update(id=3, remark='Unsupported transport', port=10445)
    unsupported['stream_settings']['transport'] = {'type': 'ws', 'path': '/test'}
    nodes.append(unsupported)
    unknown = deepcopy(nodes[0])
    unknown.update(id=4, remark='<img src=x onerror=alert(1)>', port=10446)
    unknown['stream_settings']['unqualified_option'] = 'synthetic-private-option'
    nodes.append(unknown)
    return nodes


class TCPUpstreamExportStaticTests(unittest.TestCase):
    def test_session_cancel_observes_real_rejection_and_always_removes_listeners(self):
        # Model early dashboard rejection during logout and explicit later
        # revalidation, plus setup/body/cleanup failures, without a browser.
        from unittest.mock import patch
        for timing in ('early', 'explicit', 'delayed-dismiss'):
            failures = ((None,) if timing == 'delayed-dismiss' else
                        (None, 'logout', 'response-wait', 'dialog-wait', 'body',
                         'cleanup', 'body-and-cleanup', 'wrong-dialog'))
            for failure in failures:
                with self.subTest(timing=timing, failure=failure):
                    events, observers, pending_callbacks = [], set(), []
                    page = SimpleNamespace(url='http://127.0.0.1:1/')
                    def register(event, handler):
                        self.assertEqual(event, 'dialog')
                        page.handler = handler
                        events.append('listen')
                    def remove(event, handler):
                        self.assertIs(handler, page.handler)
                        events.append('remove-dialog')
                    page.on, page.remove_listener = register, remove
                    @contextmanager
                    def expect_response(predicate, timeout):
                        self.assertEqual(timeout, 10000)
                        response = SimpleNamespace(url='http://127.0.0.1:1/api/auth/me', status=401)
                        self.assertTrue(predicate(response))
                        self.assertFalse(predicate(SimpleNamespace(url=response.url, status=200)))
                        observers.add('response')
                        yield SimpleNamespace(value=response)
                        if failure == 'response-wait':
                            raise TimeoutError('synthetic response timeout')
                    @contextmanager
                    def expect_event(event, timeout):
                        self.assertEqual((event, timeout), ('dialog', 10000))
                        observers.add('dialog')
                        yield
                        if failure == 'dialog-wait':
                            raise TimeoutError('synthetic dialog timeout')
                    page.expect_response, page.expect_event = expect_response, expect_event
                    def reject_and_navigate():
                        events.append('me401')
                        def dismiss():
                            if timing == 'delayed-dismiss':
                                from greenlet import getcurrent
                                events.append('dismiss-request')
                                getcurrent().parent.switch()
                            events.append('dismiss')
                        dialog = SimpleNamespace(
                            type='alert' if failure == 'wrong-dialog' else 'beforeunload', dismiss=dismiss)
                        if timing == 'delayed-dismiss':
                            from greenlet import greenlet
                            callback = greenlet(lambda: page.handler(dialog))
                            pending_callbacks.append(callback)
                            callback.switch()
                        else:
                            page.handler(dialog)
                    def wait_for_timeout(timeout):
                        self.assertEqual(timeout, 20)
                        events.append('pump-dismiss')
                        pending_callbacks.pop().switch()
                    page.wait_for_timeout = wait_for_timeout
                    def post(url, **kwargs):
                        self.assertEqual(observers, {'response', 'dialog'})
                        self.assertEqual(kwargs['timeout'], 10000)
                        events.append('logout')
                        if timing != 'explicit':
                            reject_and_navigate()
                        return SimpleNamespace(status=500 if failure == 'logout' else 200)
                    def browser_value(expression, arg=None):
                        if 'addEventListener' in expression:
                            events.append('install-beforeunload')
                            return True
                        if 'checkAdminSession' in expression:
                            self.assertEqual(timing, 'explicit')
                            reject_and_navigate()
                        elif 'removeEventListener' in expression:
                            events.append('remove-beforeunload')
                            if failure in ('cleanup', 'body-and-cleanup'):
                                raise RuntimeError('synthetic cleanup failure')
                    def goto(url, **kwargs):
                        self.assertEqual(url, 'http://127.0.0.1:1/login')
                        self.assertEqual(kwargs, {'wait_until': 'domcontentloaded', 'timeout': 10000})
                        events.append('fixture-login')
                    page.goto = goto
                    case = TCPUpstreamExportBrowserTests('test_ordinary_share_real_session_end_blocks_late_link_and_copy')
                    case.page, case.browser_value = page, browser_value
                    case.source = {'base': 'http://127.0.0.1:1',
                                   'context': SimpleNamespace(request=SimpleNamespace(post=post))}
                    output = io.StringIO()
                    with patch('sys.stderr', output):
                        if failure:
                            error = (TimeoutError if failure.endswith('-wait') else
                                     RuntimeError if failure == 'cleanup' else AssertionError)
                            with self.assertRaises(error):
                                with case.end_session_with_canceled_navigation():
                                    if failure in ('body', 'body-and-cleanup'):
                                        raise AssertionError('synthetic original body failure')
                            if failure != 'cleanup':
                                self.assertIn('Traceback', output.getvalue())
                            self.assertNotIn('fixture-login', events)
                        else:
                            with case.end_session_with_canceled_navigation():
                                self.assertIn('dismiss', events)
                                events.append('body')
                    if timing == 'delayed-dismiss':
                        self.assertLess(events.index('pump-dismiss'), events.index('body'))
                        self.assertEqual(pending_callbacks, [])
                    self.assertEqual(events[:3], ['listen', 'install-beforeunload', 'logout'])
                    self.assertEqual(events[-3:] if not failure else events[-2:],
                        ['remove-beforeunload', 'remove-dialog', 'fixture-login'] if not failure else
                        ['remove-beforeunload', 'remove-dialog'])

    def test_bounded_browser_value_keeps_falsy_results_and_rejects_promises(self):
        # Execute the exact generated wrapper with Node, not a DOM stand-in.
        # False/empty values must return once, never become an assertion retry.
        node = shutil.which('node')
        self.assertIsNotNone(node)
        calls, disposed = [], []
        def wait_for_function(expression, *, arg, timeout, polling):
            self.assertEqual((timeout, polling), (5000, 50))
            calls.append(expression)
            result = subprocess.run([node, '-e',
                'const [source,arg]=JSON.parse(process.argv[1]); process.stdout.write(eval(source)(arg));',
                json.dumps([expression, arg])], text=True, capture_output=True, timeout=10)
            if result.returncode:
                raise RuntimeError(result.stderr)
            return SimpleNamespace(json_value=lambda: result.stdout,
                                   dispose=lambda: disposed.append(True))
        case = TCPUpstreamExportBrowserTests('test_ordinary_share_real_session_end_blocks_late_link_and_copy')
        case.page = SimpleNamespace(wait_for_function=wait_for_function)
        for value in (False, '', 0, None, {'cleared': False}):
            self.assertEqual(case.browser_value('arg => arg', value), value)
        with self.assertRaisesRegex(RuntimeError, 'requires a synchronous result'):
            case.browser_value('() => Promise.resolve(true)')
        self.assertEqual(len(calls), 6)
        self.assertEqual(len(disposed), 5)

    def test_browser_diagnostic_includes_suspended_sync_caller(self):
        from greenlet import getcurrent, greenlet
        parent = getcurrent()
        def suspended_browser_test_marker():
            parent.switch()
        child = greenlet(suspended_browser_test_marker)
        child.switch()
        output = io.StringIO()
        try:
            reporter = threading.Thread(target=report_suspended_test_stack, args=(child, output))
            reporter.start()
            reporter.join(timeout=2)
            self.assertFalse(reporter.is_alive())
            self.assertIn('suspended_browser_test_marker', output.getvalue())
        finally:
            child.switch()

    def test_playwright_callback_dispatch_preserves_each_panel_origin_guard(self):
        from playwright._impl._impl_to_api_mapping import ImplToApiMapping
        mapping = ImplToApiMapping()
        external, observed = [], []
        bases = ('http://127.0.0.1:12001', 'http://127.0.0.1:12002')
        guards = [mapping.wrap_handler(isolated_panel_route_guard(base, external)) for base in bases]
        for index, guard in enumerate(guards):
            for url, allowed in ((bases[index] + '/login', True),
                                 (bases[index] + '/api/subscription/upstream/1', True),
                                 (bases[1 - index] + '/login', False),
                                 ('https://third-party.example/script.js', False),
                                 (bases[index] + '.evil.example/script.js', False)):
                with self.subTest(panel=index, url=url):
                    request = SimpleNamespace(url=url)
                    calls = []
                    route = SimpleNamespace(request=request,
                        abort=lambda: calls.append('abort'),
                        continue_=lambda: calls.append('continue'))
                    # This is the actual 1.57 wrapper used by sync BrowserContext
                    # routing. It receives both arguments even for one-arg handlers.
                    guard(route, request)
                    self.assertEqual(calls, ['continue' if allowed else 'abort'])
                    if not allowed:
                        observed.append(url)
        self.assertEqual(external, observed)
        # Verify the regression mechanism itself: a second default parameter
        # would consume Request, not retain a captured panel origin.
        received = []
        def two_argument_handler(route, base=bases[0]):
            received.append(base)
        request = SimpleNamespace(url=bases[1] + '/login')
        mapping.wrap_handler(two_argument_handler)(object(), request)
        self.assertEqual(received, [request])

    def test_dedicated_profile_and_ephemeral_secret_boundary(self):
        script = (ROOT / 'web/js/app.js').read_text()
        section = script.split('        const upstreamExport = reactive(', 1)[1].split('        const siteFiles =', 1)[0]
        for forbidden in ('localStorage', 'sessionStorage', 'console.', 'innerHTML', 'location.'):
            self.assertNotIn(forbidden, section)
        for required in ('AbortController', 'upstreamExportGeneration', 'URL.revokeObjectURL',
                         "addEventListener('pagehide'", "addEventListener('popstate'",
                         'acknowledge_tcp_only: true', "'tcp-upstream-v1'", 'output.select()'):
            self.assertIn(required, section)
        self.assertIn('clearAdminSecrets.forEach(clear => clear());', script)
        html = (ROOT / 'web/index.html').read_text()
        self.assertIn('@click="openUpstreamExport(scope.row)"', html)
        self.assertIn(':value="upstreamExport.content" readonly', html)
        self.assertNotIn('v-html="upstreamExport', html)
        self.assertIn('@click="showQrCode(scope.row)"', html)
        self.assertEqual(len(re.findall(r'id="upstream-export-content"', html)), 1)

    def test_actual_pinned_vue_controller_secret_lifecycle(self):
        assets = os.getenv('VUI_FRONTEND_ASSETS')
        if not assets and os.getenv('VUI_TCP_UPSTREAM_BROWSER') != '1':
            self.skipTest('checksum-pinned Vue assets are prepared by the browser gate')
        self.assertTrue(assets, 'Explicit browser gate requires VUI_FRONTEND_ASSETS')
        vue = Path(assets) / 'vue.js'
        self.assertTrue(vue.is_file(), 'Missing checksum-pinned Vue asset')
        # Real Vue reactivity/watches, with only DOM/network effects replaced.
        # This is controller coverage, not a substitute for Chromium/API tests.
        code = r'''
const vm = require('vm'), fs = require('fs'), assert = require('assert');
const pending = [], events = new EventTarget(), listeners = {}, revoked = [], urls = [];
let api, interceptor;
const context = vm.createContext({ setTimeout, clearTimeout, AbortController, Blob, console,
    location: {hash:'', replace(){}}, navigator:{clipboard:{writeText:async () => {}}},
    addEventListener(name,callback){
        // Native dispatch retains independent listeners instead of overwriting
        // the earlier export cleanup when the editor registers its cleanup.
        (listeners[name]??=new Set()).add(callback);
        events.addEventListener(name,callback);
    },
    URL:{createObjectURL(){const u='blob:'+urls.length;urls.push(u);return u},revokeObjectURL(u){revoked.push(u)}},
    axios:{ defaults:{headers:{common:{}}}, interceptors:{response:{use(_,bad){interceptor=bad}}},
      post(url,data,options){return new Promise((resolve,reject)=>pending.push({url,data,options,resolve,reject}))},
      get(){return Promise.resolve({status:401})}},
    ElementPlus:{ElMessage:{},ElMessageBox:{}}, ElementPlusIconsVue:{} });
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
context.document={getElementById(){return {focus(){},select(){}}},createElement(){return {click(){}}}};
context.Vue={...context.Vue,onMounted(){},createApp({setup}){api=setup();return{component(){},use(){},mount(){}}}};
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
const success=(content='synthetic-credential')=>({data:content,headers:{'x-vui-export-profile':'tcp-upstream-v1'}});
(async()=>{
 const state=api.upstreamExport;
 api.openUpstreamExport({id:1,remark:'one'});
 await api.requestUpstreamExport(); assert.equal(pending.length,0);
 state.server='node.example'; await api.requestUpstreamExport();assert.equal(pending.length,0);
 state.acknowledged=true; const old=api.requestUpstreamExport();assert.equal(pending.length,1);
 state.server='new.example'; assert(!state.acknowledged);assert(pending[0].options.signal.aborted);
 pending.shift().resolve(success('old-secret'));await old;assert.equal(state.content,'');
 state.acknowledged=true;const current=api.requestUpstreamExport();pending.shift().resolve(success());await current;
 assert.equal(state.content,'synthetic-credential');assert(!state.busy);
 api.downloadUpstreamExport();api.closeUpstreamExport();assert.equal(state.content,'');assert.equal(state.inboundId,null);assert(revoked.length);
 api.openUpstreamExport({id:2});state.server='node.example';state.acknowledged=true;
 const failed=api.requestUpstreamExport();pending.shift().reject({response:{status:409,data:'never-show-this'}});await failed;
 assert(!state.busy);assert.equal(state.content,'');assert(state.error.includes('不具备'));assert(!state.error.includes('never-show-this'));
 assert.equal(listeners.pagehide.size,3);assert.equal(listeners.popstate.size,3);
 api.showAddInbound.value=true;api.editingInboundId.value=47;
 api.newInbound.profile.reality_uuid='pagehide-editor-secret';
 const req=api.requestUpstreamExport();events.dispatchEvent(new Event('pagehide'));
 assert(pending[0].options.signal.aborted);
 pending.shift().resolve(success('pagehide-secret'));await req;assert.equal(state.content,'');assert(!state.open);
 assert(!api.showAddInbound.value);assert.equal(api.editingInboundId.value,null);
 assert.equal(api.newInbound.profile.reality_uuid,'');
 api.openUpstreamExport({id:4});state.server='node.example';state.acknowledged=true;
 api.showAddInbound.value=true;api.editingInboundId.value=48;
 api.newInbound.profile.reality_uuid='popstate-editor-secret';
 const popped=api.requestUpstreamExport();events.dispatchEvent(new Event('popstate'));
 assert(pending[0].options.signal.aborted);
 pending.shift().resolve(success('popstate-secret'));await popped;assert.equal(state.content,'');assert(!state.open);
 assert(!api.showAddInbound.value);assert.equal(api.editingInboundId.value,null);
 assert.equal(api.newInbound.profile.reality_uuid,'');
 api.openUpstreamExport({id:3});state.server='node.example';state.acknowledged=true;
 const afterSession=api.requestUpstreamExport();vm.runInContext('endAdminSession()',context);pending.shift().resolve(success('session-secret'));await afterSession;
 assert.equal(state.content,'');assert(!state.open);
 console.log('Actual pinned Vue controller: confirmation, synchronous invalidation, late success, failure, Blob revocation, both pagehide/popstate listeners, session end passed');
})().catch(error=>{console.error(error);process.exitCode=1});

'''
        result = subprocess.run(['node', '-e', code, str(vue), str(ROOT / 'web/js/app.js')],
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('session end passed', result.stdout)

    def test_test_nodes_are_distinct_and_use_only_synthetic_credentials(self):
        nodes = export_fixture_nodes()
        self.assertEqual([node['id'] for node in nodes], [1, 2, 3, 4])
        self.assertEqual(len({node['port'] for node in nodes}), 4)
        self.assertEqual(nodes[0]['settings']['users'][0]['uuid'], UUID)
        self.assertIn('reality', nodes[1]['stream_settings']['tls'])
        self.assertIn('transport', nodes[2]['stream_settings'])
        self.assertIn('unqualified_option', nodes[3]['stream_settings'])

    def test_ordinary_share_actual_pinned_vue_controller_lifecycle(self):
        assets = os.getenv('VUI_FRONTEND_ASSETS')
        if not assets and os.getenv('VUI_TCP_UPSTREAM_BROWSER') != '1':
            self.skipTest('checksum-pinned Vue assets are prepared by the browser gate')
        self.assertTrue(assets, 'Explicit browser gate requires VUI_FRONTEND_ASSETS')
        vue = Path(assets) / 'vue.js'
        self.assertTrue(vue.is_file(), 'Missing checksum-pinned Vue asset')
        from test_share_dialog import run_pinned_vue
        run_pinned_vue(self, vue)


@unittest.skipUnless(os.getenv('VUI_TCP_UPSTREAM_BROWSER') == '1', 'explicit TCP upstream browser gate')
class TCPUpstreamExportBrowserTests(unittest.TestCase):
    def setUp(self):
        # Diagnostic only: include the suspended sync caller, not merely the
        # dispatcher greenlet's asyncio loop. Never call Playwright off-thread.
        faulthandler.dump_traceback_later(90, repeat=True)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)
        from greenlet import getcurrent
        test_greenlet, stop_diagnostic = getcurrent(), threading.Event()
        def report_caller():
            while not stop_diagnostic.wait(90):
                print(self.id(), file=sys.stderr, flush=True)
                report_suspended_test_stack(test_greenlet, sys.stderr)
        threading.Thread(target=report_caller, daemon=True).start()
        self.addCleanup(stop_diagnostic.set)
        from playwright.sync_api import sync_playwright
        self.tmp = tempfile.TemporaryDirectory(prefix='vui-tcp-upstream-browser-')
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.nodes = export_fixture_nodes()
        assets = Path(os.environ['VUI_FRONTEND_ASSETS']).resolve()
        self.payload = prepare_browser_payload(self.work / 'payload', assets)
        self.instances = {name: self.start_panel(name, self.nodes if name == 'source' else [])
                          for name in ('source', 'target')}
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        options = {'headless': True}
        executable = os.getenv('VUI_TEST_CHROMIUM') or os.getenv('VUI_BROWSER_EXECUTABLE')
        if not executable and not Path(self.playwright.chromium.executable_path).exists():
            executable = shutil.which('chromium') or shutil.which('chromium-browser')
        if executable:
            options['executable_path'] = executable
        self.browser = self.playwright.chromium.launch(**options)
        self.addCleanup(self.browser.close)
        self.external, self.errors, self.console, self.export_requests = [], [], [], []
        for name, instance in self.instances.items():
            context = self.browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
            self.addCleanup(context.close)
            instance['context'] = context
            context.add_init_script('''(() => {
                const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
                window.exportBlobAudit = {created: [], revoked: []};
                URL.createObjectURL = value => {const url = create(value); exportBlobAudit.created.push(url); return url};
                URL.revokeObjectURL = url => {exportBlobAudit.revoked.push(url); return revoke(url)};
            })()''')
            observe_browser_pages(context, self.errors, self.console)
            context.route('**/*', isolated_panel_route_guard(instance['base'], self.external))
            page = context.new_page()
            instance['page'] = page
            page.on('request', lambda request: self.export_requests.append(request)
                    if '/api/subscription/upstream/' in request.url else None)
            self.login(instance)
        self.source, self.target = self.instances['source'], self.instances['target']
        self.page = self.source['page']
        self.open_nodes()

    def start_panel(self, name, nodes):
        directory = self.work / name
        directory.mkdir()
        logpath = directory / 'server.log'
        log = logpath.open('w')
        self.addCleanup(log.close)
        env = {**os.environ, 'PYTHONPATH': str(self.payload), 'PYTHONDONTWRITEBYTECODE': '1',
               'VUI_PUBLIC_ORIGIN': '', 'VUI_DATA_DIR': str(directory / 'data'),
               'VUI_BIN_DIR': str(directory / 'no-cores'), 'VUI_CERTIFICATES_ENABLED': '0'}
        code = '''import json,sys,uvicorn
from app.models.database import init_db,SessionLocal,Inbound
from app.services.auth_service import provision_admin
fixture=json.loads(sys.stdin.read())
init_db()
provision_admin('export-tester',fixture['password'])
with SessionLocal() as db:
 for values in fixture['nodes']: db.add(Inbound(**values))
 db.commit()
import main
uvicorn.run(main.app,host='127.0.0.1',port=0,proxy_headers=False,use_colors=False,access_log=False)
'''
        process = subprocess.Popen([sys.executable, '-B', '-c', code], cwd=self.payload, env=env,
                                   stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, text=True)
        def stop():
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self.addCleanup(stop)
        process.stdin.write(json.dumps({'password': PASSWORD, 'nodes': nodes}))
        process.stdin.close()
        deadline, match = time.monotonic() + 20, None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail(logpath.read_text())
            match = re.search(r'Uvicorn running on http://127\.0\.0\.1:(\d+)', logpath.read_text())
            if match:
                break
            time.sleep(.05)
        self.assertIsNotNone(match, logpath.read_text())
        return {'base': 'http://127.0.0.1:' + match.group(1), 'directory': directory, 'logpath': logpath}

    def login(self, instance):
        from playwright.sync_api import expect
        page = instance['page']
        page.goto(instance['base'] + '/login')
        page.locator('#login-name').fill('export-tester')
        page.locator('#login-password').fill(PASSWORD)
        with page.expect_response(lambda response: response.url.endswith('/api/inbounds')) as initial_nodes:
            page.get_by_role('button', name='登录面板', exact=True).click()
            page.wait_for_url('**/ui/')
        self.assertEqual(initial_nodes.value.status, 200)
        expect(page.get_by_role('menuitem', name='代理与中转', exact=True)).to_be_visible()

    def open_nodes(self):
        from playwright.sync_api import expect
        self.page.get_by_role('menuitem', name='入站节点', exact=True).click()
        expect(self.page.get_by_role('button', name='导出为中转上游（仅 TCP）', exact=True)).to_have_count(4)

    def open_export(self, node=1, fmt='uri', server=HOST):
        from playwright.sync_api import expect
        self.page.get_by_role('button', name='导出为中转上游（仅 TCP）', exact=True).nth(node - 1).click()
        expect(self.page.locator('#upstream-export-panel')).to_be_visible()
        expect(self.page.locator('#upstream-export-server')).to_have_value('')
        expect(self.page.locator('#upstream-export-request')).to_be_disabled()
        self.page.locator('#upstream-export-server').fill(server)
        self.page.locator('#upstream-export-format').select_option(fmt)
        expect(self.page.locator('#upstream-export-request')).to_be_disabled()
        self.page.locator('#upstream-export-ack').check()

    def generate(self):
        from playwright.sync_api import expect
        with self.page.expect_response(lambda response: '/api/subscription/upstream/' in response.url) as pending:
            self.page.locator('#upstream-export-request').click()
        response = pending.value
        self.assertEqual(response.status, 200)
        self.assertIn('no-store', response.headers['cache-control'])
        self.assertEqual(response.headers['x-vui-export-profile'], 'tcp-upstream-v1')
        expect(self.page.locator('#upstream-export-content')).to_be_visible()
        value = self.page.locator('#upstream-export-content').input_value()
        self.assertEqual(value, response.text())
        return value

    def close_export(self):
        from playwright.sync_api import expect
        self.page.locator('#upstream-export-cancel').click()
        expect(self.page.locator('#upstream-export-panel')).not_to_be_visible()
        self.assert_cleared()

    def assert_cleared(self):
        self.assertEqual(self.page.evaluate("document.querySelector('#upstream-export-content')?.value || ''"), '')
        self.assertNotIn(UUID, self.page.content())
        self.assertNotIn(SHORT_ID, self.page.content())

    def assert_private(self):
        for instance in self.instances.values():
            page = instance['page']
            self.assertEqual(page.evaluate('Object.keys(localStorage).length+Object.keys(sessionStorage).length'), 0)
            for secret in (UUID, SHORT_ID, PASSWORD, self.nodes[1]['stream_settings']['tls']['reality']['private_key']):
                self.assertNotIn(secret, page.url)
                self.assertNotIn(secret, instance['logpath'].read_text())
                self.assertFalse(any(secret in message for message in self.console))
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external, [])

    def open_share(self, node=1):
        """Use the ordinary endpoint, not the explicit TCP-upstream profile."""
        from playwright.sync_api import expect
        from app.models.database import Inbound
        from app.services.validated_export import share_link
        with self.page.expect_response(lambda response:
                response.url == self.source['base'] + f'/api/subscription/link/{node}') as result:
            self.page.get_by_role('button', name='分享', exact=True).nth(node - 1).click()
        response = result.value
        self.assertEqual(response.status, 200)
        self.assertEqual(response.request.method, 'GET')
        self.assertIsNone(response.request.post_data)
        expected = share_link(Inbound(**self.nodes[node - 1]), '127.0.0.1')
        self.assertEqual(response.text(), expected)
        expect(self.page.locator('#share-panel')).to_be_visible()
        expect(self.page.locator('#share-panel')).to_have_attribute('aria-busy', 'false')
        expect(self.page.locator('#share-link')).to_have_value(expected)
        expect(self.page.locator('#share-link')).to_have_js_property('readOnly', True)
        expect(self.page.locator('#share-copy')).to_be_enabled()
        self.page.wait_for_function("document.querySelector('#qrcode')?.childElementCount > 0")
        # qrcodejs also writes the credential-bearing URI into this attribute.
        expect(self.page.locator('#qrcode')).to_have_attribute('title', expected)
        return expected

    def browser_value(self, expression, arg=None):
        """Bound a synchronous DOM operation, including context acquisition.

        page.evaluate/content have no timeout in Playwright 1.57. An always-
        truthy JSON wrapper uses wait_for_function's timeout without retrying
        false assertion results; primitive json_value needs no DOM evaluation.
        """
        handle = self.page.wait_for_function("""arg => {
            const value = (""" + expression + """)(arg);
            if (value && typeof value.then === 'function')
                throw new Error('browser_value requires a synchronous result');
            return JSON.stringify({value: value === undefined ? null : value});
        }""", arg=arg, timeout=5000, polling=50)
        try:
            return json.loads(handle.json_value())['value']
        finally:
            handle.dispose()

    def assert_share_cleared(self, session_ended=False):
        from playwright.sync_api import expect
        expect(self.page.locator('#share-panel')).not_to_be_visible()
        snapshot = self.browser_value('''sessionEnded => {
            const qr = document.querySelector('#qrcode');
            let controller = null;
            if (sessionEnded) {
                const root = document.querySelector('#app')._vnode.component.proxy;
                const navigation = window.shareSessionNavigation;
                controller = {mounted: document.querySelector('#share-panel') !== null,
                    sameDocument: navigation?.document === document && navigation?.root === root,
                    beforeunload: navigation?.attempts, sessionEnded: adminSessionEnded,
                    open: root.showQrDialog, link: root.qrLink,
                    busy: root.qrBusy, notice: root.qrNotice};
            }
            return {controller, state: {
                value: document.querySelector('#share-link')?.value || '',
                notice: document.querySelector('#share-notice')?.textContent || '',
                title: qr?.getAttribute('title') || '', text: qr?.textContent || '',
                children: qr?.childElementCount || 0}, html: document.documentElement.outerHTML};
        }''', session_ended)
        if session_ended:
            self.assertEqual(snapshot['controller'], {'mounted': True, 'sameDocument': True,
                'beforeunload': 1, 'sessionEnded': True, 'open': False, 'link': '',
                'busy': False, 'notice': ''})
        self.assertEqual(snapshot['state'],
                         {'value': '', 'notice': '', 'title': '', 'text': '', 'children': 0})
        # Hidden Element Plus contents still belong to the DOM. Do not merely
        # assert invisibility, which would miss attributes, images and text.
        for secret in (UUID, SHORT_ID, self.nodes[1]['stream_settings']['tls']['reality']['private_key']):
            self.assertNotIn(secret, snapshot['html'])

    def defer_share_completions(self):
        # Only defer delivery *after* real authenticated HTTP/Axios completes.
        # Aborting an already-completed transport cannot recall these promises,
        # so stale-result guards must work independently of network cancellation.
        self.page.evaluate('''() => {
            const get = axios.get.bind(axios);
            window.shareResponseAudit = [];
            axios.get = (url, options) => {
                if (!url.startsWith('/api/subscription/link/')) return get(url, options);
                const entry = {url, aborted: false, ready: false, released: false};
                shareResponseAudit.push(entry);
                options?.signal?.addEventListener('abort', () => { entry.aborted = true; });
                return get(url, options).then(response => new Promise(resolve => {
                    entry.status = response.status;
                    entry.content = response.data;
                    entry.ready = true;
                    entry.release = () => { entry.released = true; resolve(response); };
                }));
            };
        }''')

    def wait_share_completion(self, count):
        self.page.wait_for_function('''count => shareResponseAudit.length === count
            && shareResponseAudit[count - 1].ready''', arg=count)
        entry = self.page.evaluate('''index => {
            const entry = shareResponseAudit[index];
            return {status: entry.status, content: entry.content, url: entry.url};
        }''', count - 1)
        self.assertEqual(entry['status'], 200)
        self.assertIn(UUID, entry['content'])
        return entry['content']

    def release_share_completion(self, index):
        self.browser_value('index => { shareResponseAudit[index].release(); }', index)
        # Cover queued QR rendering as well as Vue's immediate text update.
        # Also exceed the legacy 100 ms delay so a stale timer cannot pass.
        self.page.wait_for_timeout(200)

    @contextmanager
    def end_session_with_canceled_navigation(self):
        """Inspect a live logged-out document after canceling its real redirect.

        The actual Share click supplies sticky activation. A test-only
        beforeunload listener asks Chromium to confirm the app's real
        location.replace; dismissing that dialog cancels navigation. Unlike
        holding /login's response, this retains the original execution context.
        Logout and /auth/me 401 remain real. Final /login navigation below is
        fixture-controlled, not evidence the canceled redirect completed.
        """
        dialogs, dismissed = [], []
        def dismiss_navigation(dialog):
            dialogs.append(dialog.type)
            # Never leave a JS dialog open, even if its type is unexpected.
            dialog.dismiss()
            dismissed.append(dialog.type)
        self.page.on('dialog', dismiss_navigation)
        try:
            activated = self.browser_value('''() => {
                const state = window.shareSessionNavigation = {document,
                    root: document.querySelector('#app')._vnode.component.proxy,
                    attempts: 0};
                state.listener = event => {
                    state.attempts++;
                    event.preventDefault();
                    event.returnValue = '';
                };
                window.addEventListener('beforeunload', state.listener);
                return navigator.userActivation.hasBeenActive;
            }''')
            self.assertTrue(activated, 'The real Share click must provide sticky activation')
            # Both observers precede revocation. Existing dashboard polling can
            # observe logout while the synchronous request.post is still pending.
            with self.page.expect_response(lambda response:
                    response.url == self.source['base'] + '/api/auth/me' and response.status == 401,
                    timeout=10000) as rejected:
                with self.page.expect_event('dialog', timeout=10000):
                    logout = self.source['context'].request.post(self.source['base'] + '/api/auth/logout',
                        headers={'Origin': self.source['base'], 'X-VUI-Request': '1'}, timeout=10000)
                    self.assertEqual(logout.status, 200)
                    if not dialogs:
                        self.browser_value('() => { void checkAdminSession(true, true); }')
            self.assertEqual(rejected.value.status, 401)
            self.assertEqual(dialogs, ['beforeunload'])
            # Event callbacks use separate greenlets: observing the dialog does
            # not itself prove its dismiss call has received Chromium's reply.
            deadline = time.monotonic() + 10
            while not dismissed and time.monotonic() < deadline:
                self.page.wait_for_timeout(20)
            self.assertEqual(dismissed, ['beforeunload'])
            self.assertNotEqual(self.page.url, self.source['base'] + '/login')
            yield
        except BaseException:
            # unittest otherwise defers this traceback until the whole suite
            # finishes. Preserve the original failure before fixture cleanup.
            traceback.print_exc(file=sys.stderr)
            raise
        finally:
            original_error = sys.exc_info()[0] is not None
            try:
                self.browser_value('''() => {
                    const state = window.shareSessionNavigation;
                    if (state) window.removeEventListener('beforeunload', state.listener);
                    delete window.shareSessionNavigation;
                }''')
            except Exception:
                if not original_error:
                    raise
                # A cleanup error must not replace the product/setup assertion.
                traceback.print_exc(file=sys.stderr)
            finally:
                self.page.remove_listener('dialog', dismiss_navigation)
        self.page.goto(self.source['base'] + '/login', wait_until='domcontentloaded', timeout=10000)

    def test_ordinary_share_real_tls_reality_uri_copy_and_elementplus_dismissal(self):
        from playwright.sync_api import expect
        before = self.source['context'].request.get(self.source['base'] + '/api/inbounds').json()
        # An isolated clipboard boundary records the actual handler's bytes;
        # this does not claim OS clipboard permission or persistence coverage.
        self.page.evaluate('''() => {
            window.shareCopies = [];
            Object.defineProperty(navigator, 'clipboard', {configurable: true,
                value: {writeText: async text => { shareCopies.push(text); }}});
        }''')
        for node in (1, 2):
            with self.subTest(node=node):
                link = self.open_share(node)
                self.assertIn('security=' + ('tls' if node == 1 else 'reality'), link)
                if node == 1:
                    self.assertIn('packetEncoding=xudp', link)
                else:
                    self.assertIn('flow=xtls-rprx-vision', link)
                    self.assertIn(SHORT_ID, link)
                self.assertNotIn('/private/', link)
                self.assertNotIn(self.nodes[1]['stream_settings']['tls']['reality']['private_key'], link)
                self.page.locator('#share-copy').click()
                expect(self.page.locator('#share-notice')).to_contain_text('已复制')
                self.assertEqual(self.page.evaluate('shareCopies.at(-1)'), link)
                if node == 1:
                    self.page.get_by_role('dialog', name='分享节点', exact=True).locator('.el-dialog__headerbtn').click()
                else:
                    self.page.keyboard.press('Escape')
                self.assert_share_cleared()
        self.open_share(1)
        # A physical click in the backdrop, outside the centered dialog, must
        # use Element Plus's own modal dismissal and synchronous state watcher.
        self.page.locator('.el-overlay-dialog').filter(has=self.page.locator('#share-panel')).click(
            position={'x': 5, 'y': 5})
        self.assert_share_cleared()
        self.assertEqual(self.source['context'].request.get(self.source['base'] + '/api/inbounds').json(), before)
        self.assertEqual(self.export_requests, [])
        self.assert_private()

    def test_ordinary_share_reverse_real_completions_cannot_replace_newer_node(self):
        from playwright.sync_api import expect
        self.defer_share_completions()
        self.page.get_by_role('button', name='分享', exact=True).nth(0).click()
        first = self.wait_share_completion(1)
        expect(self.page.locator('#share-panel')).to_be_visible()
        expect(self.page.locator('#share-panel')).to_have_attribute('aria-busy', 'true')
        expect(self.page.locator('#share-link')).to_have_value('')
        expect(self.page.locator('#share-copy')).to_be_disabled()
        # Dispatch the real second button handler to model an already-queued
        # selection while the modal masks the table. No Vue state is fabricated.
        self.page.get_by_role('button', name='分享', exact=True).nth(1).evaluate('(button) => button.click()')
        second = self.wait_share_completion(2)
        self.assertNotEqual(first, second)
        self.assertTrue(self.page.evaluate('shareResponseAudit[0].aborted'))
        self.release_share_completion(1)
        expect(self.page.locator('#share-link')).to_have_value(second)
        expect(self.page.locator('#qrcode')).to_have_attribute('title', second)
        self.release_share_completion(0)
        expect(self.page.locator('#share-link')).to_have_value(second)
        expect(self.page.locator('#qrcode')).to_have_attribute('title', second)
        self.assertNotIn(first, self.page.content())
        self.page.locator('#share-close').click()
        self.assert_share_cleared()
        self.assert_private()

    def test_ordinary_share_close_aborts_pending_transport_without_late_reopen(self):
        from playwright.sync_api import expect
        pending = []
        def delay(route):
            pending.append((route, None))
        def cleanup_pending():
            self.source['context'].unroute('**/api/subscription/link/*', delay)
            while pending:
                route, response = pending.pop()
                try:
                    route.abort()
                finally:
                    if response is not None:
                        response.dispose()
        self.source['context'].route('**/api/subscription/link/*', delay)
        self.addCleanup(cleanup_pending)
        self.page.get_by_role('button', name='分享', exact=True).nth(0).click()
        expect(self.page.locator('#share-panel')).to_be_visible()
        expect(self.page.locator('#share-panel')).to_have_attribute('aria-busy', 'true')
        expect(self.page.locator('#share-link')).to_have_value('')
        expect(self.page.locator('#share-copy')).to_be_disabled()
        deadline = time.monotonic() + 10
        while not pending and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(pending), 1)
        route, _ = pending[0]
        response = route.fetch(timeout=10000)
        pending[0] = (route, response)
        self.assertEqual(response.status, 200)
        self.assertIn(UUID, response.text())
        with self.page.expect_event('requestfailed', predicate=lambda request:
                request.url == self.source['base'] + '/api/subscription/link/1') as aborted:
            self.page.locator('#share-close').click()
        self.assertEqual(aborted.value.failure, 'net::ERR_ABORTED')
        self.assert_share_cleared()
        route, response = pending.pop()
        try:
            route.fulfill(response=response)
        finally:
            response.dispose()
        self.page.wait_for_timeout(200)
        self.assert_share_cleared()
        self.assert_private()

    def test_ordinary_share_displayed_secrets_clear_on_refresh_navigation_and_history(self):
        from playwright.sync_api import expect
        for action in ('refresh', 'navigation', 'popstate', 'pagehide'):
            with self.subTest(action=action):
                self.open_share(2)
                if action == 'refresh':
                    with self.page.expect_response(lambda response: response.url.endswith('/api/inbounds')) as result:
                        self.page.get_by_role('button', name='刷新节点', exact=True).evaluate('(button) => button.click()')
                    self.assertEqual(result.value.status, 200)
                elif action == 'navigation':
                    self.page.get_by_role('menuitem', name='系统状态', exact=True).evaluate('(item) => item.click()')
                else:
                    self.page.evaluate('event => dispatchEvent(new Event(event))', action)
                self.assert_share_cleared()
                if action == 'navigation':
                    self.open_nodes()
                expect(self.page.get_by_role('button', name='分享', exact=True)).to_have_count(4)
        # Keep session errors outside subTest so failure exits immediately;
        # do not continue into privacy checks after an incomplete fixture.
        self.open_share(2)
        with self.end_session_with_canceled_navigation():
            self.assert_share_cleared(session_ended=True)
        self.page.wait_for_url('**/login')
        self.assert_share_cleared()
        self.assert_private()

    def test_ordinary_share_late_real_completion_after_close_refresh_and_history(self):
        self.defer_share_completions()
        for index, action in enumerate(('close', 'escape', 'refresh', 'navigation', 'popstate', 'pagehide')):
            with self.subTest(action=action):
                self.page.get_by_role('button', name='分享', exact=True).nth(index % 2).click()
                self.wait_share_completion(index + 1)
                if action == 'close':
                    self.page.locator('#share-close').click()
                elif action == 'escape':
                    self.page.keyboard.press('Escape')
                elif action == 'refresh':
                    with self.page.expect_response(lambda response: response.url.endswith('/api/inbounds')):
                        self.page.get_by_role('button', name='刷新节点', exact=True).evaluate('(button) => button.click()')
                elif action == 'navigation':
                    self.page.get_by_role('menuitem', name='系统状态', exact=True).evaluate('(item) => item.click()')
                else:
                    self.page.evaluate('event => dispatchEvent(new Event(event))', action)
                self.assertTrue(self.page.evaluate('index => shareResponseAudit[index].aborted', index))
                self.assert_share_cleared()
                self.release_share_completion(index)
                self.assert_share_cleared()
                if action == 'navigation':
                    self.open_nodes()
        self.assert_private()

    def test_ordinary_share_copy_denial_and_late_copy_feedback_are_private(self):
        from playwright.sync_api import expect
        self.open_share()
        self.page.evaluate('''secret => Object.defineProperty(navigator, 'clipboard', {
            configurable: true, value: {writeText: async () => { throw new Error(secret); }}
        })''', 'synthetic-clipboard-denial-' + UUID)
        self.page.locator('#share-copy').click()
        expect(self.page.locator('#share-notice')).to_contain_text('复制失败')
        self.assertNotIn(UUID, self.page.locator('#share-notice').inner_text())
        self.assertEqual(self.page.locator('#share-link').evaluate('(e) => e.selectionEnd - e.selectionStart'),
                         len(self.page.locator('#share-link').input_value()))
        self.page.locator('#share-close').click()
        self.assert_share_cleared()
        for succeed in (True, False):
            with self.subTest(succeed=succeed):
                self.open_share()
                self.page.evaluate('''() => {
                    window.shareCopyAudit = {writes: []};
                    Object.defineProperty(navigator, 'clipboard', {configurable: true,
                        value: {writeText: text => {
                            shareCopyAudit.writes.push(text);
                            return new Promise((resolve, reject) => {
                                shareCopyAudit.resolve = resolve; shareCopyAudit.reject = reject;
                            });
                        }}});
                }''')
                self.page.locator('#share-copy').click()
                self.assertEqual(self.page.evaluate('shareCopyAudit.writes.length'), 1)
                self.page.locator('#share-close').click()
                # Invoke the exposed method as well as checking the disabled UI:
                # guarding only the button must not permit a stale programmatic copy.
                self.page.evaluate("document.querySelector('#app')._vnode.component.proxy.copyLink()")
                self.assertEqual(self.page.evaluate('shareCopyAudit.writes.length'), 1)
                self.page.evaluate('''succeed => succeed ? shareCopyAudit.resolve()
                    : shareCopyAudit.reject(new Error('synthetic late denial'))''', succeed)
                self.assert_share_cleared()
        self.assert_private()

    def test_ordinary_share_real_session_end_blocks_late_link_and_copy(self):
        self.open_share(2)
        self.page.evaluate('''() => {
            window.shareCopyAudit = {writes: []};
            Object.defineProperty(navigator, 'clipboard', {configurable: true,
                value: {writeText: text => {
                    shareCopyAudit.writes.push(text);
                    return new Promise(resolve => { shareCopyAudit.resolve = resolve; });
                }}});
        }''')
        self.page.locator('#share-copy').click()
        self.defer_share_completions()
        self.page.get_by_role('button', name='分享', exact=True).nth(0).evaluate('(button) => button.click()')
        self.wait_share_completion(1)
        # Cancel the real beforeunload confirmation, keeping the original
        # invalidated document live for cleanup and late-completion assertions.
        with self.end_session_with_canceled_navigation():
            self.assert_share_cleared(session_ended=True)
            self.assertTrue(self.browser_value('() => shareResponseAudit[0].aborted'))
            self.browser_value("() => { document.querySelector('#app')._vnode.component.proxy.copyLink(); }")
            self.assertEqual(self.browser_value('() => shareCopyAudit.writes.length'), 1)
            self.browser_value('() => { shareCopyAudit.resolve(); }')
            self.release_share_completion(0)
            self.assert_share_cleared(session_ended=True)
        self.page.wait_for_url('**/login')
        self.assert_share_cleared()
        self.assert_private()

    def test_real_tls_reality_uri_yaml_copy_download_and_target_import(self):
        from playwright.sync_api import expect
        from app.services.proxy_store import ProxyStore
        from app.services.upstream_import import select_upstream
        from app.models.database import Inbound
        from app.services.tcp_upstream_export import tcp_upstream_profile
        target = self.target['page']
        target.goto(self.target['base'] + '/proxy')
        expect(target.locator('#draft-mode')).to_contain_text('00')
        source_before = self.source['context'].request.get(self.source['base'] + '/api/inbounds').json()
        self.source['context'].grant_permissions(['clipboard-read', 'clipboard-write'], origin=self.source['base'])
        expected_profiles = []
        for node in (1, 2):
            for fmt in ('uri', 'clash-yaml'):
                with self.subTest(node=node, format=fmt):
                    prior = len(self.export_requests)
                    self.open_export(node, fmt)
                    self.assertEqual(len(self.export_requests), prior)  # No implicit credential fetch.
                    content = self.generate()
                    request = self.export_requests[-1]
                    self.assertEqual(request.method, 'POST')
                    self.assertEqual(request.post_data_json, {'format': fmt, 'server': HOST, 'acknowledge_tcp_only': True})
                    self.assertEqual(request.headers['x-vui-request'], '1')
                    self.assertNotIn('?', request.url)
                    self.assertIn(UUID, content)
                    self.assertNotIn('private_key', content)
                    self.assertNotIn('/private/', content)
                    if fmt == 'uri':
                        self.page.locator('#upstream-export-copy').click()
                        expect(self.page.locator('#upstream-export-notice')).to_contain_text('已复制')
                        transferred = self.page.evaluate('navigator.clipboard.readText()')
                        self.assertEqual(transferred, content)
                    with self.page.expect_download() as download:
                        self.page.locator('#upstream-export-download').click()
                    self.assertEqual(download.value.suggested_filename,
                                     'upstream-tcp.yaml' if fmt == 'clash-yaml' else 'upstream-tcp.txt')
                    downloaded = Path(download.value.path()).read_text()
                    self.assertEqual(downloaded, content)
                    self.page.wait_for_function('exportBlobAudit.created.every(url => exportBlobAudit.revoked.includes(url))')
                    if fmt == 'clash-yaml':
                        transferred = downloaded
                        self.assertIs(__import__('yaml').safe_load(content)['udp'], False)
                    target.locator('#relay-enabled').check()
                    target.locator('#open-import').click()
                    target.locator('#import-format').select_option(fmt)
                    target.locator('#import-content').fill(transferred)
                    target.locator('#preview-import').click()
                    expect(target.locator('#import-preview')).to_be_visible()
                    expect(target.locator('#save-import')).to_be_disabled()
                    self.assertEqual(target.locator('#candidates input:checked').count(), 0)
                    target.locator('#candidates input[value="0"]').check()
                    target.locator('#save-import').click()
                    expect(target.locator('#import-dialog')).not_to_be_visible()
                    expect(target.locator('#notice')).to_contain_text('上游已保存')
                    expect(target.locator('#import-content')).to_have_value('')
                    canonical = tcp_upstream_profile(Inbound(**self.nodes[node - 1]), HOST).to_dict()
                    self.assertEqual(select_upstream(content, 0, fmt).to_dict(), canonical)
                    expected_profiles.append(canonical)
                    saved = list(ProxyStore(self.target['directory'] / 'data').load_draft()['upstreams'].values())
                    self.assertCountEqual(saved, expected_profiles)
                    self.close_export()
        snapshot = self.target['context'].request.get(self.target['base'] + '/api/proxy/settings').json()
        self.assertEqual(len(snapshot['upstreams']), 4)
        self.assertIs(snapshot['settings']['local_proxy_enabled'], False)
        self.assertIs(snapshot['settings']['relay_enabled'], False)  # Import alone never applies or enables.
        self.assertIsNone(snapshot['active'])
        self.assertNotIn(UUID, json.dumps(snapshot))
        self.assertEqual(self.source['context'].request.get(self.source['base'] + '/api/inbounds').json(), source_before)
        self.assert_private()

    def test_confirmation_changes_copy_failure_close_refresh_and_history_clear(self):
        from playwright.sync_api import expect
        self.open_export()
        self.generate()
        self.page.evaluate("Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async () => {throw new Error('synthetic denial')}}})")
        self.page.locator('#upstream-export-copy').click()
        expect(self.page.locator('#upstream-export-notice')).to_contain_text('复制失败')
        self.assertEqual(self.page.locator('#upstream-export-content').evaluate('(e) => e.selectionEnd - e.selectionStart'),
                         len(self.page.locator('#upstream-export-content').input_value()))
        self.page.locator('#upstream-export-server').fill('changed.example.test')
        self.assert_cleared()
        expect(self.page.locator('#upstream-export-ack')).not_to_be_checked()
        self.page.locator('#upstream-export-ack').check()
        self.generate()
        self.page.locator('#upstream-export-format').select_option('clash-yaml')
        self.assert_cleared()
        expect(self.page.locator('#upstream-export-ack')).not_to_be_checked()
        self.page.locator('#upstream-export-ack').check()
        self.generate()
        self.page.locator('#upstream-export-ack').uncheck()
        self.assert_cleared()
        self.page.locator('#upstream-export-ack').check()
        self.generate()
        self.page.keyboard.press('Escape')
        expect(self.page.locator('#upstream-export-panel')).not_to_be_visible()
        self.assert_cleared()
        self.open_export(2)
        self.generate()
        self.page.reload()
        expect(self.page.get_by_role('menuitem', name='入站节点', exact=True)).to_be_visible()
        self.assert_cleared()
        self.open_nodes()
        self.open_export()
        self.generate()
        self.page.evaluate("dispatchEvent(new PopStateEvent('popstate'))")
        expect(self.page.locator('#upstream-export-panel')).not_to_be_visible()
        self.assert_cleared()
        self.open_export()
        self.generate()
        self.page.get_by_role('button', name='刷新节点', exact=True).evaluate('(element) => element.click()')
        expect(self.page.locator('#upstream-export-panel')).not_to_be_visible()
        self.assert_cleared()
        self.assert_private()

    def test_real_ineligible_unknown_missing_invalid_and_permission_failures(self):
        from playwright.sync_api import expect
        for node in (3, 4):
            self.open_export(node)
            with self.page.expect_response(lambda response: '/api/subscription/upstream/' in response.url) as result:
                self.page.locator('#upstream-export-request').click()
            self.assertEqual(result.value.status, 409)
            expect(self.page.locator('#upstream-export-error')).to_contain_text('不具备')
            self.assert_cleared()
            self.assertEqual(self.page.locator('#upstream-export-panel img').count(), 0)
            self.close_export()
        self.open_export(server='bad.example/path')
        self.page.locator('#upstream-export-request').click()
        expect(self.page.locator('#upstream-export-error')).to_contain_text('有效的字面主机')
        self.assert_cleared()
        self.close_export()
        self.open_export()
        self.generate()
        def deny(route):
            headers = dict(route.request.headers)
            headers.pop('x-vui-request', None)
            route.continue_(headers=headers)
        self.source['context'].route('**/api/subscription/upstream/*', deny)
        with self.page.expect_response(lambda response: '/api/subscription/upstream/' in response.url) as result:
            self.page.locator('#upstream-export-request').click()
        self.assertEqual(result.value.status, 403)
        expect(self.page.locator('#upstream-export-error')).to_contain_text('无权导出')
        self.assert_cleared()
        self.source['context'].unroute('**/api/subscription/upstream/*', deny)
        self.close_export()
        with sqlite3.connect(self.source['directory'] / 'data/v-ui.db') as database:
            database.execute('DELETE FROM inbounds WHERE id=1')
        self.open_export()
        with self.page.expect_response(lambda response: '/api/subscription/upstream/' in response.url) as result:
            self.page.locator('#upstream-export-request').click()
        self.assertEqual(result.value.status, 404)
        expect(self.page.locator('#upstream-export-error')).to_contain_text('节点不存在')
        self.assert_cleared()
        self.close_export()
        self.assert_private()

    def test_real_response_cannot_restore_after_cancel_node_change_or_session_end(self):
        from playwright.sync_api import expect
        pending = []
        def delay(route):
            # Do no fetching/asserting in the event callback. A failed real
            # fetch must fail the test directly, not hide behind a UI timeout.
            pending.append((route, None))
        def wait_pending():
            deadline = time.monotonic() + 10
            while not pending and time.monotonic() < deadline:
                self.page.wait_for_timeout(20)
            self.assertEqual(len(pending), 1)
            route, response = pending[0]
            self.assertIsNone(response)
            response = route.fetch(timeout=10000)
            pending[0] = (route, response)
            self.assertEqual(response.status, 200)
        def fulfill_pending():
            route, response = pending.pop()
            try:
                route.fulfill(response=response)
            finally:
                response.dispose()
        def cleanup_pending():
            self.source['context'].unroute('**/api/subscription/upstream/*', delay)
            while pending:
                route, response = pending.pop()
                try:
                    route.abort()
                finally:
                    if response is not None:
                        response.dispose()
        self.source['context'].route('**/api/subscription/upstream/*', delay)
        self.addCleanup(cleanup_pending)
        self.open_export()
        self.page.locator('#upstream-export-request').click()
        expect(self.page.locator('#upstream-export-request')).to_be_disabled()
        self.page.locator('#upstream-export-request').evaluate('(button) => {button.click(); button.click()}')
        wait_pending()
        self.assertEqual(len(self.export_requests), 1)
        self.close_export()
        self.open_export(2)
        fulfill_pending()
        self.assert_cleared()
        self.page.locator('#upstream-export-request').click()
        wait_pending()
        self.page.locator('#upstream-export-server').fill('new.example.test')
        fulfill_pending()
        self.assert_cleared()
        self.page.locator('#upstream-export-ack').check()
        self.page.locator('#upstream-export-request').click()
        wait_pending()
        # Real server logout revokes the cookie. The dashboard's own session
        # revalidation then clears secrets before an older success can return.
        logout = self.source['context'].request.post(self.source['base'] + '/api/auth/logout',
            headers={'Origin': self.source['base'], 'X-VUI-Request': '1'})
        self.assertEqual(logout.status, 200)
        self.page.evaluate('void checkAdminSession(true, true)')
        self.page.wait_for_url('**/login')
        fulfill_pending()
        self.assert_cleared()
        self.assert_private()

    def test_real_session_401_clears_displayed_export(self):
        self.open_export()
        self.generate()
        logout = self.source['context'].request.post(self.source['base'] + '/api/auth/logout',
            headers={'Origin': self.source['base'], 'X-VUI-Request': '1'})
        self.assertEqual(logout.status, 200)
        self.page.locator('#upstream-export-request').click()
        self.page.wait_for_url('**/login')
        self.assert_cleared()
        self.assert_private()


if __name__ == '__main__':
    unittest.main()
