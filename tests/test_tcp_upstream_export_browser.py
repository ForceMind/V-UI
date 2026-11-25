"""D1 real-browser exports between two isolated, authenticated panel instances.

Successful exports use real HTTP endpoints and either seeded nodes or a
TLS node created through the server-use wizard. Both import preview and save use the target's real parser/private store.
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
from urllib.parse import parse_qs, urlparse
import unittest

from test_inbound_routing_browser import stop_owned_panel, verified_dependencies
from test_proxy_browser import (
    expand_details, observe_browser_pages, open_proxy_settings, prepare_browser_payload,
)
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
        self.assertIn('@click="openSimpleShare(scope.row)"', html)
        shared=(ROOT/'web/js/simple-operations.js').read_text()
        self.assertIn('给中转服务器使用（仅TCP，含凭据）',shared)
        self.assertIn('row.upstream_export_eligible===true',shared)
        self.assertIn(':value="upstreamExport.content" readonly', html)
        self.assertNotIn('v-html="upstreamExport', html)
        self.assertIn('@click="openSimpleShare(scope.row)"', html)
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
 assert.equal(listeners.pagehide.size,4);assert.equal(listeners.popstate.size,4);
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
        for instance in self.instances.values():
            self.connect_panel(instance)
        self.source, self.target = self.instances['source'], self.instances['target']
        self.page = self.source['page']
        self.open_nodes()

    def connect_panel(self, instance):
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

    def start_panel(self, name, nodes, managed_tls=False):
        directory = self.work / name
        directory.mkdir()
        logpath = directory / 'server.log'
        log = logpath.open('w')
        self.addCleanup(log.close)
        env = {**os.environ, 'PYTHONPATH': str(self.payload), 'PYTHONDONTWRITEBYTECODE': '1',
               'VUI_PUBLIC_ORIGIN': '', 'VUI_DATA_DIR': str(directory / 'data'),
               'VUI_BIN_DIR': str(directory / 'no-cores'), 'VUI_CERTIFICATES_ENABLED': '0'}
        if managed_tls:
            cores, _, _ = verified_dependencies(os.environ)
            env['VUI_BIN_DIR'] = str(cores)
        env['PYTHONPATH'] = os.pathsep.join((str(self.payload), str(ROOT / 'tests')))
        code = '''import json,sys,uvicorn
from pathlib import Path
from app.models.database import init_db,SessionLocal,Inbound
from app.services.auth_service import provision_admin
fixture=json.loads(sys.stdin.read())
init_db()
provision_admin('export-tester',fixture['password'])
if fixture['managed_tls']:
 from cryptography.hazmat.primitives import serialization
 from app.certificates import manager as module
 from test_certificates import SigningProvider
 provider=SigningProvider()
 ca=provider.ca.public_bytes(serialization.Encoding.PEM)
 module._instance=module.CertificateManager(Path(fixture['data'])/'certificates',provider,trusted_roots=ca)
 job=module._instance.create('vpn.example.test','test@example.test','production',False,True)
 module._instance.process_once()
 assert module._instance.job(job['id'])['state']=='succeeded'
with SessionLocal() as db:
 for values in fixture['nodes']: db.add(Inbound(**values))
 db.commit()
import main
uvicorn.run(main.app,host='127.0.0.1',port=0,proxy_headers=False,use_colors=False,access_log=False)
'''
        process = subprocess.Popen([sys.executable, '-B', '-c', code], cwd=self.payload, env=env,
                                   stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(stop_owned_panel, process)
        process.stdin.write(json.dumps({'password': PASSWORD, 'nodes': nodes,
                                        'managed_tls': managed_tls, 'data': str(directory / 'data')}))
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
            page.wait_for_url('**/ui/#dashboard')
        self.assertEqual(initial_nodes.value.status, 200)
        expect(page.locator('.vui-nav-link[data-nav-id="proxy"]')).to_be_visible()

    def open_nodes(self, page=None):
        from playwright.sync_api import expect
        page = page or self.page
        page.locator('.vui-nav-link[data-nav-id="inbounds"]').click()
        expect(page.get_by_role('heading', name='节点管理', exact=True)).to_be_visible()

    def initialize_wizard_routing(self, instance):
        from playwright.sync_api import expect
        page, base = instance['page'], instance['base']
        page.goto(base + '/proxy')
        open_proxy_settings(page)
        expect(page.locator('#local-enabled')).not_to_be_checked()
        expect(page.locator('#relay-enabled')).not_to_be_checked()
        expect(page.locator('#upstream')).to_have_value('')
        with page.expect_response(lambda reply: urlparse(reply.url).path == '/api/proxy/check'
                                  and reply.request.method == 'POST', timeout=20000) as reviewed:
            page.locator('#apply').click()
        self.assertEqual(reviewed.value.status, 200, reviewed.value.text())
        self.assertTrue(reviewed.value.json()['valid'])
        expect(page.locator('#apply-review')).to_be_visible()
        with page.expect_response(lambda reply: urlparse(reply.url).path == '/api/proxy/apply'
                                  and reply.request.method == 'POST', timeout=25000) as applied:
            page.locator('#confirm-apply').click()
        self.assertEqual(applied.value.status, 200, applied.value.text())
        self.assertTrue(applied.value.json()['applied'])
        snapshot = instance['context'].request.get(base + '/api/proxy/settings').json()
        self.assertIsNotNone(snapshot['active'])
        self.assertIsNone(snapshot['pending'])
        self.assertFalse(snapshot['active']['settings']['local_proxy_enabled'])
        self.assertFalse(snapshot['active']['settings']['relay_enabled'])
        self.assertIsNone(snapshot['active']['settings']['selected_upstream_id'])

    def open_wizard_export(self):
        from playwright.sync_api import expect
        from loopback_helpers import unused_port
        wizard_panel = self.start_panel('wizard', [], managed_tls=True)
        self.instances['wizard'] = wizard_panel
        self.connect_panel(wizard_panel)
        self.initialize_wizard_routing(wizard_panel)
        page = wizard_panel['page']
        self.open_nodes(page)
        page.get_by_role('button', name='创建节点', exact=True).click()
        wizard = page.locator('#node-wizard')
        wizard.get_by_role('button', name='给另一台服务器用', exact=False).click()
        wizard.locator('#node-wizard-name').fill('wizard-tcp-browser')
        wizard.locator('#node-wizard-port').fill(str(unused_port()))
        with page.expect_response(lambda reply: urlparse(reply.url).path == '/api/inbounds'
                                  and reply.request.method == 'POST', timeout=25000) as created:
            page.locator('#node-wizard-create').click()
        self.assertEqual(created.value.status, 200, created.value.text())
        self.assertGreater(created.value.json()['inbound']['id'], 0)
        expect(wizard.get_by_role('heading', name='节点已创建', exact=True)).to_be_visible()
        page.locator('#node-wizard-deliver').click()
        expect(page.locator('#upstream-export-panel')).to_be_visible()
        expect(page.locator('#upstream-export-server')).to_have_value('vpn.example.test')
        expect(page.locator('#upstream-export-request')).to_be_disabled()
        return page, wizard_panel

    def generate(self, page):
        from playwright.sync_api import expect
        with page.expect_response(lambda response: '/api/subscription/upstream/' in response.url) as pending:
            page.locator('#upstream-export-request').click()
        response = pending.value
        self.assertEqual(response.status, 200)
        self.assertIn('no-store', response.headers['cache-control'])
        self.assertEqual(response.headers['x-vui-export-profile'], 'tcp-upstream-v1')
        expect(page.locator('#upstream-export-content')).to_be_visible()
        value = page.locator('#upstream-export-content').input_value()
        self.assertEqual(value, response.text())
        return value

    def close_export(self, page):
        from playwright.sync_api import expect
        page.locator('#upstream-export-cancel').click()
        expect(page.locator('#upstream-export-panel')).not_to_be_visible()
        self.assert_cleared(page)

    def assert_cleared(self, page):
        self.assertEqual(page.evaluate("document.querySelector('#upstream-export-content')?.value || ''"), '')
        self.assertNotIn(UUID, page.content())
        self.assertNotIn(SHORT_ID, page.content())

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

    @property
    def share_dialog(self):
        return self.page.locator('#simple-node-operations dialog')

    @property
    def delivery(self):
        return self.share_dialog.locator('.ops-delivery')

    def select_share(self, node):
        self.page.locator(f'#share-node-{node}').click()

    def close_share(self):
        from playwright.sync_api import expect
        self.share_dialog.get_by_role('button', name='关闭', exact=True).click()
        expect(self.share_dialog).not_to_be_visible()
        self.page.wait_for_function("history.state?.modal !== 'simple-operations'")

    def open_share(self, node=1, show_qr=False):
        from playwright.sync_api import expect
        from app.models.database import Inbound
        from app.services.validated_export import share_link
        with self.page.expect_response(lambda response:
                urlparse(response.url).path == f'/api/subscription/link/{node}') as result:
            self.select_share(node)
        response = result.value
        self.assertEqual(response.status, 200)
        self.assertEqual(response.request.method, 'GET')
        self.assertIsNone(response.request.post_data)
        self.assertIn('no-store', response.headers['cache-control'])
        params = parse_qs(urlparse(response.url).query)
        self.assertEqual(params['host'], ['127.0.0.1'])
        row = next(row for row in self.read_nodes() if row['id'] == node)
        self.assertEqual(params['expected_instance_revision'], [row['mutation_revision']])
        self.assertEqual(params['expected_content_revision'], [row['content_revision']])
        expected = share_link(Inbound(**self.nodes[node - 1]), '127.0.0.1')
        self.assertEqual(response.text(), expected)
        expect(self.delivery.locator('textarea')).to_have_value(expected)
        expect(self.delivery.locator('textarea')).to_have_js_property('readOnly', True)
        expect(self.delivery.locator('.ops-qr > *')).to_have_count(0)
        if show_qr:
            self.delivery.get_by_role('button', name='显示二维码', exact=True).click()
            expect(self.delivery.locator('.ops-qr')).to_have_attribute('title', expected)
            expect(self.delivery.locator('.ops-qr canvas')).to_have_count(1)
        return expected

    def read_nodes(self):
        reply = self.source['context'].request.get(self.source['base'] + '/api/inbounds')
        self.assertEqual(reply.status, 200)
        return reply.json()

    def read_upstream(self, node, fmt, *, acknowledge=True, header=True, server=HOST):
        base = self.source['base']
        headers = {'Origin': base}
        if header:
            headers['X-VUI-Request'] = '1'
        return self.source['context'].request.post(base + f'/api/subscription/upstream/{node}',
            headers=headers, data={'format': fmt, 'server': server, 'acknowledge_tcp_only': acknowledge})

    def open_target_import(self):
        page = self.target['page']
        page.goto(self.target['base'] + '/proxy')
        open_proxy_settings(page)
        return page

    def import_upstream(self, page, text, fmt):
        from playwright.sync_api import expect
        page.locator('#open-import').click()
        if fmt != 'uri':
            expand_details(page.locator('#connection-import-options'))
            page.locator('#import-format').select_option(fmt)
        page.locator('#import-content').fill(text)
        page.locator('#preview-import').click()
        expect(page.locator('#import-preview')).to_be_visible()
        expect(page.locator('#save-import')).to_be_disabled()
        self.assertEqual(page.locator('#candidates input:checked').count(), 0)
        page.locator('#candidates input[value="0"]').check()
        page.locator('#save-import').click()
        expect(page.locator('#import-dialog')).not_to_be_visible()
        expect(page.locator('#notice')).to_contain_text('上游已保存')
        expect(page.locator('#import-content')).to_have_value('')

    def assert_draft_only(self, expected):
        from app.services.proxy_store import ProxyStore
        reply = self.target['context'].request.get(self.target['base'] + '/api/proxy/settings')
        self.assertEqual(reply.status, 200)
        snapshot = reply.json()
        self.assertEqual(len(snapshot['upstreams']), len(expected))
        self.assertIs(snapshot['settings']['local_proxy_enabled'], False)
        self.assertIs(snapshot['settings']['relay_enabled'], False)
        self.assertIsNone(snapshot['active'])
        self.assertNotIn(UUID, reply.text())
        saved = list(ProxyStore(self.target['directory'] / 'data').load_draft()['upstreams'].values())
        self.assertCountEqual(saved, expected)

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

    def assert_share_cleared(self):
        from playwright.sync_api import expect
        expect(self.share_dialog).not_to_be_visible()
        expect(self.delivery).to_have_count(0)
        for secret in (UUID, SHORT_ID, self.nodes[1]['stream_settings']['tls']['reality']['private_key']):
            self.assertNotIn(secret, self.page.content())

    @contextmanager
    def held_endpoints(self, prefix, page=None):
        page = page or self.page
        pending = []
        origin = urlparse(page.url).netloc

        def matches(url):
            parsed = urlparse(url)
            return parsed.scheme == 'http' and parsed.netloc == origin and parsed.path.startswith(prefix)

        def hold(route):
            pending.append(route)

        page.route(matches, hold)
        try:
            yield pending
        finally:
            while pending:
                pending.pop().abort()
            page.unroute(matches, hold)

    def wait_for_held_reply(self, pending, page=None):
        page = page or self.page
        deadline = time.monotonic() + 10
        while not pending and time.monotonic() < deadline:
            page.wait_for_timeout(20)
        self.assertEqual(len(pending), 1, 'matching browser request was not intercepted')

    def release_reply(self, pending, status=200, index=0):
        route = pending.pop(index)
        response = route.fetch(timeout=10000)
        try:
            self.assertEqual(response.status, status)
            route.fulfill(response=response)
        finally:
            response.dispose()

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


    def test_existing_tls_and_reality_share_copy_and_dismiss(self):
        from playwright.sync_api import expect
        before = self.read_nodes()
        self.source['context'].grant_permissions(['clipboard-read', 'clipboard-write'], origin=self.source['base'])
        for node in (1, 2):
            with self.subTest(node=node):
                link = self.open_share(node, show_qr=True)
                self.assertIn('security=' + ('tls' if node == 1 else 'reality'), link)
                self.assertIn('packetEncoding=xudp' if node == 1 else 'flow=xtls-rprx-vision', link)
                if node == 2:
                    self.assertIn(SHORT_ID, link)
                self.assertNotIn('/private/', link)
                self.assertNotIn(self.nodes[1]['stream_settings']['tls']['reality']['private_key'], link)
                self.delivery.get_by_role('button', name='复制连接链接', exact=True).click()
                expect(self.share_dialog.locator('.ops-status')).to_contain_text('已复制')
                self.assertEqual(self.page.evaluate('navigator.clipboard.readText()'), link)
                if node == 1:
                    self.close_share()
                else:
                    self.page.keyboard.press('Escape')
                    self.page.wait_for_function("history.state?.modal !== 'simple-operations'")
                self.assert_share_cleared()
                expect(self.page.locator(f'#share-node-{node}')).to_be_focused()
        self.assertEqual(self.read_nodes(), before)
        self.assertEqual(self.export_requests, [])
        self.assert_private()

    def test_ordinary_share_reverse_real_completions_cannot_replace_newer_node(self):
        from playwright.sync_api import expect
        with self.held_endpoints('/api/subscription/link/') as pending:
            for node in (1, 2):
                with self.page.expect_request(lambda request:
                        urlparse(request.url).path == f'/api/subscription/link/{node}'):
                    self.select_share(node)
                expect(self.delivery).to_have_count(0)
                if node == 1:
                    self.close_share()
                    self.assert_share_cleared()
            self.assertEqual(len(pending), 2)
            self.release_reply(pending, index=1)
            expect(self.delivery.locator('textarea')).to_have_value(re.compile(r'security=reality'))
            self.delivery.get_by_role('button', name='显示二维码', exact=True).click()
            current = self.delivery.locator('textarea').input_value()
            self.release_reply(pending)
            self.page.wait_for_timeout(200)
            expect(self.delivery.locator('textarea')).to_have_value(current)
            expect(self.delivery.locator('.ops-qr')).to_have_attribute('title', current)
            self.close_share()
        self.assert_share_cleared()
        self.assert_private()

    def test_ordinary_share_does_not_reopen_after_late_success(self):
        from playwright.sync_api import expect
        with self.held_endpoints('/api/subscription/link/') as pending:
            with self.page.expect_request(lambda request: urlparse(request.url).path == '/api/subscription/link/1'):
                self.select_share(1)
            expect(self.share_dialog).to_be_visible()
            expect(self.delivery.locator('textarea')).to_have_count(0)
            expect(self.delivery.get_by_role('button', name='复制连接链接', exact=True)).to_have_count(0)
            self.close_share()
            self.release_reply(pending)
            self.page.wait_for_timeout(200)
            self.assert_share_cleared()
        self.assert_private()

    def test_ordinary_share_displayed_secrets_clear_on_refresh_navigation_and_history(self):
        for action in ('reload', 'back', 'navigation'):
            with self.subTest(action=action):
                self.open_share(2, show_qr=True)
                if action == 'reload':
                    self.page.reload()
                elif action == 'back':
                    self.page.go_back()
                    self.page.go_forward()
                else:
                    # Request real same-document navigation while the modal
                    # owns the active route, then verify its payload removal.
                    self.page.locator('.vui-nav-link[data-nav-id="dashboard"]').evaluate('link => link.click()')
                    self.page.wait_for_url('**/ui/#dashboard')
                self.assert_share_cleared()
                self.open_nodes()
        self.assert_private()

    def test_ordinary_share_retries_a_failed_read_without_showing_credentials(self):
        from playwright.sync_api import expect
        pattern = re.compile(re.escape(self.source['base']) + r'/api/subscription/link/1\?')
        reads = []

        def unavailable_once(route):
            reads.append(route.request.method)
            if len(reads) == 1:
                route.fulfill(status=503, json={'detail': 'Synthetic share unavailable'})
            else:
                route.continue_()

        self.page.route(pattern, unavailable_once)
        try:
            self.select_share(1)
            expect(self.share_dialog.locator('.ops-status')).to_contain_text('未能读取')
            expect(self.delivery).to_have_count(0)
            retry = self.share_dialog.get_by_role('button', name=re.compile('重试.*读取'))
            retry.click()
            expect(self.delivery.locator('textarea')).to_have_value(re.compile('security=tls'))
            self.assertEqual(reads, ['GET', 'GET'])
            self.close_share()
            self.assert_share_cleared()
        finally:
            self.page.unroute(pattern, unavailable_once)
        self.assert_private()

    def test_ordinary_share_copy_denial_and_late_copy_feedback_are_private(self):
        from playwright.sync_api import expect
        self.open_share()
        self.page.evaluate("""secret => Object.defineProperty(navigator, 'clipboard', {
            configurable:true, value:{writeText:async () => { throw new Error(secret); }}
        })""", 'synthetic-clipboard-denial-' + UUID)
        self.delivery.get_by_role('button', name='复制连接链接', exact=True).click()
        status = self.share_dialog.locator('.ops-status')
        expect(status).to_contain_text('自动复制未成功')
        self.assertNotIn(UUID, status.inner_text())
        field = self.delivery.locator('textarea')
        self.assertEqual(field.evaluate('field => field.selectionEnd - field.selectionStart'), len(field.input_value()))
        self.close_share()
        for succeeded in (True, False):
            with self.subTest(succeeded=succeeded):
                self.open_share()
                self.page.evaluate("""() => {
                    window.shareCopyAudit = {writes: []};
                    Object.defineProperty(navigator, 'clipboard', {configurable: true,
                        value: {writeText: text => {
                            shareCopyAudit.writes.push(text);
                            return new Promise((resolve, reject) => {
                                shareCopyAudit.resolve = resolve;
                                shareCopyAudit.reject = reject;
                            });
                        }}});
                }""")
                self.delivery.get_by_role('button', name='复制连接链接', exact=True).click()
                self.page.wait_for_function('shareCopyAudit.writes.length === 1')
                self.close_share()
                self.page.evaluate("""succeeded => succeeded ? shareCopyAudit.resolve()
                    : shareCopyAudit.reject(new Error('synthetic late denial'))""", succeeded)
                self.assert_share_cleared()
                self.assertEqual(self.page.evaluate('shareCopyAudit.writes.length'), 1)
        self.assert_private()

    def test_ordinary_share_real_session_end_blocks_late_link_and_copy(self):
        self.open_share(2, show_qr=True)
        self.page.evaluate("""() => {
            window.shareCopyAudit = {writes: []};
            Object.defineProperty(navigator, 'clipboard', {configurable: true,
                value: {writeText: text => {
                    shareCopyAudit.writes.push(text);
                    return new Promise(resolve => { shareCopyAudit.resolve = resolve; });
                }}});
        }""")
        self.delivery.get_by_role('button', name='复制连接链接', exact=True).click()
        self.page.wait_for_function('shareCopyAudit.writes.length === 1')
        with self.end_session_with_canceled_navigation():
            self.assert_share_cleared()
            self.browser_value('() => { shareCopyAudit.resolve(); }')
            self.assertEqual(self.browser_value('() => shareCopyAudit.writes.length'), 1)
            self.assert_share_cleared()
        self.page.wait_for_url('**/login')
        self.assert_share_cleared()

        self.login(self.source)
        self.open_nodes()
        with self.held_endpoints('/api/subscription/link/') as pending:
            with self.page.expect_request(lambda request: urlparse(request.url).path == '/api/subscription/link/1'):
                self.select_share(1)
            self.wait_for_held_reply(pending)
            response = pending[0].fetch(timeout=10000)
            try:
                self.assertEqual(response.status, 200)
                self.assertIn(UUID, response.text())
                with self.end_session_with_canceled_navigation():
                    self.assert_share_cleared()
                    pending.pop().fulfill(response=response)
                    self.page.wait_for_timeout(200)
                    self.assert_share_cleared()
            finally:
                response.dispose()
        self.page.wait_for_url('**/login')
        self.assert_share_cleared()
        self.assert_private()

    def test_real_tls_reality_uri_yaml_copy_download_and_target_import(self):
        import yaml
        from app.models.database import Inbound
        from app.services.tcp_upstream_export import tcp_upstream_profile
        from app.services.upstream_import import select_upstream
        from playwright.sync_api import expect

        target = self.open_target_import()
        source_before = self.read_nodes()
        self.source['context'].grant_permissions(['clipboard-read', 'clipboard-write'], origin=self.source['base'])
        expected = []
        for node in (1, 2):
            with self.subTest(node=node, format='uri', source='browser'):
                self.open_share(node)
                prior = len(self.export_requests)
                button = self.delivery.get_by_role('button', name='给中转服务器使用（仅TCP，含凭据）', exact=True)
                with self.page.expect_response(lambda reply: urlparse(reply.url).path ==
                                               f'/api/subscription/upstream/{node}') as exported:
                    button.click()
                response = exported.value
                self.assertEqual(response.status, 200)
                self.assertIn('no-store', response.headers['cache-control'])
                self.assertEqual(response.headers['x-vui-export-profile'], 'tcp-upstream-v1')
                self.assertEqual(len(self.export_requests), prior + 1)
                row = next(row for row in source_before if row['id'] == node)
                request = response.request
                self.assertEqual(request.method, 'POST')
                self.assertEqual(request.headers['x-vui-request'], '1')
                self.assertFalse(urlparse(request.url).query)
                self.assertEqual(request.post_data_json, {'format': 'uri', 'server': '127.0.0.1',
                    'acknowledge_tcp_only': True, 'expected_instance_revision': row['mutation_revision'],
                    'expected_content_revision': row['content_revision']})
                expect(self.delivery.locator('textarea')).to_have_value(response.text())
                self.delivery.get_by_role('button', name='复制TCP出口链接', exact=True).click()
                expect(self.share_dialog.locator('.ops-status')).to_contain_text('已复制')
                transferred = self.page.evaluate('navigator.clipboard.readText()')
                self.assertEqual(transferred, response.text())
                canonical = tcp_upstream_profile(Inbound(**self.nodes[node - 1]), '127.0.0.1').to_dict()
                self.assertEqual(select_upstream(transferred, 0, 'uri').to_dict(), canonical)
                self.import_upstream(target, transferred, 'uri')
                expected.append(canonical)
                self.assert_draft_only(expected)
                self.close_share()
                self.assert_share_cleared()
            with self.subTest(node=node, format='clash-yaml', source='API'):
                response = self.read_upstream(node, 'clash-yaml')
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers['x-vui-export-profile'], 'tcp-upstream-v1')
                self.assertIn('no-store', response.headers['cache-control'])
                self.assertIn('upstream-tcp.yaml', response.headers['content-disposition'])
                self.assertIs(yaml.safe_load(response.text())['udp'], False)
                self.assertNotIn('private_key', response.text())
                self.assertNotIn('/private/', response.text())
                canonical = tcp_upstream_profile(Inbound(**self.nodes[node - 1]), HOST).to_dict()
                self.assertEqual(select_upstream(response.text(), 0, 'clash-yaml').to_dict(), canonical)
                self.import_upstream(target, response.text(), 'clash-yaml')
                expected.append(canonical)
                self.assert_draft_only(expected)
        self.assertEqual(self.read_nodes(), source_before)
        self.assert_private()

    def test_ineligible_nodes_are_not_offered_and_api_rejects_bad_requests(self):
        from playwright.sync_api import expect
        for node in (3, 4):
            with self.subTest(node=node):
                row = next(row for row in self.read_nodes() if row['id'] == node)
                self.assertFalse(row['upstream_export_eligible'])
                if row['share_eligible']:
                    self.select_share(node)
                    expect(self.delivery.locator('textarea')).to_be_visible()
                    expect(self.delivery.get_by_role('button', name=re.compile('给中转服务器'))).to_have_count(0)
                    self.close_share()
                else:
                    expect(self.page.locator(f'#share-node-{node}')).to_have_count(0)
                denied = self.read_upstream(node, 'uri')
                self.assertEqual(denied.status, 409)
                self.assertNotIn(UUID, denied.text())
        for options, status in (({'server': 'bad.example/path'}, 422), ({'header': False}, 403),
                                ({'acknowledge': False}, 422)):
            denied = self.read_upstream(1, 'uri', **options)
            self.assertEqual(denied.status, status)
            self.assertNotIn(UUID, denied.text())
        self.assertEqual(self.read_upstream(9999, 'uri').status, 404)
        self.assertEqual(self.export_requests, [])
        self.assert_share_cleared()
        self.assert_private()

    def test_wizard_tls_confirmation_copy_download_and_target_import(self):
        import yaml
        from playwright.sync_api import expect
        from app.services.upstream_import import select_upstream
        target = self.open_target_import()
        page, source = self.open_wizard_export()
        source['context'].grant_permissions(['clipboard-read', 'clipboard-write'], origin=source['base'])
        expected = []
        for fmt, filename in (('uri', 'upstream-tcp.txt'), ('clash-yaml', 'upstream-tcp.yaml')):
            with self.subTest(format=fmt):
                start = len(self.export_requests)
                page.locator('#upstream-export-format').select_option(fmt)
                expect(page.locator('#upstream-export-request')).to_be_disabled()
                self.assertEqual(len(self.export_requests), start)
                page.locator('#upstream-export-ack').check()
                content = self.generate(page)
                self.assertEqual(len(self.export_requests), start + 1)
                self.assertEqual(self.export_requests[-1].post_data_json, {'format': fmt, 'server': 'vpn.example.test',
                    'acknowledge_tcp_only': True})
                if fmt == 'clash-yaml':
                    self.assertIs(yaml.safe_load(content)['udp'], False)
                self.assertNotIn('private_key', content)
                self.assertNotIn('/private/', content)
                page.locator('#upstream-export-copy').click()
                expect(page.locator('#upstream-export-notice')).to_contain_text('已复制')
                self.assertEqual(page.evaluate('navigator.clipboard.readText()'), content)
                with page.expect_download() as downloaded:
                    page.locator('#upstream-export-download').click()
                self.assertEqual(downloaded.value.suggested_filename, filename)
                self.assertEqual(Path(downloaded.value.path()).read_bytes(), content.encode())
                page.wait_for_function('exportBlobAudit.created.every(url => exportBlobAudit.revoked.includes(url))')
                self.import_upstream(target, content, fmt)
                expected.append(select_upstream(content, 0, fmt).to_dict())
                self.assert_draft_only(expected)
        self.close_export(page)
        self.assertNotIn(content, page.content())
        self.assert_private()

    def test_wizard_tls_confirmation_changes_and_copy_failure(self):
        from playwright.sync_api import expect
        page, _ = self.open_wizard_export()
        page.locator('#upstream-export-ack').check()
        content = self.generate(page)
        page.evaluate("""() => Object.defineProperty(navigator, 'clipboard', {configurable: true,
            value: {writeText: async () => { throw new Error('synthetic rejection'); }}})""")
        page.locator('#upstream-export-copy').click()
        expect(page.locator('#upstream-export-notice')).to_contain_text('复制失败')
        field = page.locator('#upstream-export-content')
        self.assertEqual(field.evaluate('field => field.selectionEnd - field.selectionStart'), len(content))
        page.locator('#upstream-export-server').fill('different.example.test')
        expect(field).to_have_count(0)
        expect(page.locator('#upstream-export-ack')).not_to_be_checked()
        expect(page.locator('#upstream-export-request')).to_be_disabled()
        self.assertNotIn(content, page.content())
        self.close_export(page)
        self.assert_private()

    def test_wizard_tls_close_stops_transport_and_rejects_late_output(self):
        from playwright.sync_api import expect
        page, _ = self.open_wizard_export()
        with self.held_endpoints('/api/subscription/upstream/', page) as pending:
            page.locator('#upstream-export-ack').check()
            with page.expect_request(lambda request: urlparse(request.url).path.startswith('/api/subscription/upstream/')):
                page.locator('#upstream-export-request').click()
            expect(page.locator('#upstream-export-request')).to_be_disabled()
            page.locator('#upstream-export-request').evaluate('button => {button.click(); button.click()}')
            self.wait_for_held_reply(pending, page)
            real = pending[0].fetch(timeout=10000)
            try:
                self.assertEqual(real.status, 200)
                with page.expect_event('requestfailed', predicate=lambda request:
                                       urlparse(request.url).path.startswith('/api/subscription/upstream/')) as failed:
                    self.close_export(page)
                self.assertEqual(failed.value.failure, 'net::ERR_ABORTED')
                pending.pop().fulfill(response=real)
                page.wait_for_timeout(200)
                self.assert_cleared(page)
                self.assertNotIn(real.text(), page.content())
            finally:
                real.dispose()
        self.assert_private()


if __name__ == '__main__':
    unittest.main()
