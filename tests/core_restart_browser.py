"""Real native buttons with explicitly synthetic held restart/status replies.

Uses the existing authenticated disposable-server Chromium fixture. Every API
mutation is intercepted: restart POSTs are mocked, any other write is aborted
and fails. No restart reaches a real API/core. This is UI admission/recovery
coverage, not actual restart, runtime, routing or device qualification.
"""
import re
import time
from urllib.parse import urlparse


STATE = 'app._container._vnode.component.setupState'


def assert_core_restart_single_flight(case):
    from playwright.sync_api import expect
    from test_core_restart import assert_core_restart

    # Asset-enabled native CI must also run the exact finite deterministic set.
    assert_core_restart(case)
    page, context = case.page, case.context
    page.set_viewport_size({'width': 1440, 'height': 1000})
    # End login's controller first, without dropping its global error audit.
    page.goto('about:blank')
    core_status = '/api/cores/status'
    restart_path = re.compile(r'^/api/cores/(xray|sing-box)/restart$')
    synthetic_cores = {core: {'running': False, 'binary_exists': False}
                       for core in ('xray', 'sing-box')}
    held_posts, held_status = [], []
    mocked_posts, writes, unexpected_writes, failed = [], [], [], []
    mock_requests = set()
    modes = {'hold_status': False}
    completed_status = []

    def observe(request):
        path = urlparse(request.url).path
        if path.startswith('/api/') and request.method != 'GET':
            writes.append((request.method, path))

    def observe_failure(request):
        if request in mock_requests:
            failed.append((request.method, urlparse(request.url).path, request.failure))

    def intercept(route):
        request = route.request
        if not request.url.startswith(case.base + '/'):
            route.fallback()  # Keep the existing external-request rejection.
            return
        path = urlparse(request.url).path
        if request.method != 'GET':
            # All writes are blocked from the server, even on a failing test.
            if request.method == 'POST' and restart_path.fullmatch(path):
                mocked_posts.append((request.method, path))
                mock_requests.add(request)
                held_posts.append(route)
            else:
                unexpected_writes.append((request.method, path))
                route.abort()
            return
        if path == core_status:
            mock_requests.add(request)
            if modes['hold_status']:
                held_status.append((route, time.monotonic()))
            else:
                route.fulfill(json=synthetic_cores)
            return
        route.fallback()  # Other reads retain the real temporary server.

    def wait_until(predicate, explanation, timeout=2):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            page.wait_for_timeout(10)
        case.assertTrue(predicate(), explanation)

    def button(core):
        return page.locator('.header-right').get_by_role(
            'button', name='Xray' if core == 'xray' else 'sing-box', exact=True)

    def assert_pending(owner):
        case.assertEqual(page.evaluate('() => ' + STATE + '.restartingCore'), owner)
        for core in ('xray', 'sing-box'):
            control = button(core)
            expect(control).to_be_disabled()
            expect(control).to_have_js_property('disabled', True)
            expect(control).to_have_attribute('aria-disabled', 'true')
            if core == owner:
                expect(control).to_have_class(re.compile(r'\bis-loading\b'))
                expect(control.locator('.el-icon.is-loading')).to_have_count(1)
            else:
                expect(control).not_to_have_class(re.compile(r'\bis-loading\b'))
                expect(control.locator('.el-icon.is-loading')).to_have_count(0)

    def assert_idle():
        for core in ('xray', 'sing-box'):
            control = button(core)
            expect(control).to_be_enabled()
            expect(control).to_have_js_property('disabled', False)
            expect(control).to_have_attribute('aria-disabled', 'false')
            expect(control).not_to_have_class(re.compile(r'\bis-loading\b'))
            expect(control.locator('.el-icon.is-loading')).to_have_count(0)
        case.assertEqual(page.evaluate('() => ' + STATE + '.restartingCore'), '')

    page.on('request', observe)
    page.on('requestfailed', observe_failure)
    context.route('**/api/**', intercept)
    try:
        page.goto(case.base + '/ui/')
        expect(page.get_by_role('menuitem', name='系统状态', exact=True)).to_be_visible()
        expect(page.locator('#xray-status-tag')).to_have_text('Xray Stopped')
        expect(page.locator('#singbox-status-tag')).to_have_text('sing-box Stopped')
        assert_idle()
        expected_posts = []
        # Both orderings and normal enabled clicks after success, validation
        # failure and API rejection. No forced click or disabled-event dispatch.
        for first, other in (('xray', 'sing-box'), ('sing-box', 'xray')):
            for core, outcome in ((first, 'success'), (other, 'invalid'),
                                  (first, 'error'), (other, 'success')):
                assert_idle()
                case.assertEqual(held_posts, [])
                case.assertEqual(held_status, [])
                button(core).click()
                expected_posts.append(('POST', '/api/cores/' + core + '/restart'))
                wait_until(lambda: len(held_posts) == 1, 'one synthetic restart POST must be held')
                case.assertEqual(mocked_posts, expected_posts)
                case.assertEqual(writes, expected_posts)
                assert_pending(core)
                route = held_posts[0]
                case.assertIsNone(route.request.post_data, 'restart request body remains absent')
                case.assertEqual(route.request.headers.get('x-vui-request'), '1')
                # Disabled and selected-spinner assertions are native state
                # checks, not calls to a hand-coded component or forced input.
                case.assertEqual(len(mocked_posts), len(expected_posts))
                if outcome == 'error':
                    message = 'Synthetic restart rejection ' + first
                    held_posts[0].fulfill(status=409, json={'detail': message})
                    held_posts.pop(0)
                    expect(page.locator('.el-message--error .el-message__content').filter(
                        has_text=re.compile('^' + re.escape(message) + '$')).last).to_be_visible()
                    assert_idle()
                else:
                    # Preserve the real 5s status timeout. Only the response is
                    # synthetic; no timers/pollers are disabled or redesigned.
                    modes['hold_status'] = True
                    data = {'valid': outcome == 'success'}
                    if outcome == 'invalid':
                        message = 'Synthetic validation failure ' + first
                        data['validation_output'] = message
                    else:
                        message = core + ' restarted'
                    held_posts[0].fulfill(json=data)
                    held_posts.pop(0)
                    wait_until(lambda: bool(held_status), 'restart must await a fresh synthetic status read')
                    assert_pending(core)
                    kind = 'success' if outcome == 'success' else 'error'
                    expect(page.locator('.el-message--' + kind + ' .el-message__content').filter(
                        has_text=re.compile('^' + re.escape(message) + '$')).last).to_be_visible()
                    released = 0
                    # A natural poll can have entered first. The unchanged
                    # singleStatusRequest.fresh queues at most one newer read;
                    # keep ownership through both, never accept stale completion.
                    deadline = time.monotonic() + 4
                    while page.evaluate('() => ' + STATE + '.restartingCore'):
                        case.assertLess(time.monotonic(), deadline,
                                        'synthetic status must settle before its real timeout')
                        if held_status:
                            case.assertEqual(len(held_status), 1, 'status single-flight remains intact')
                            held, started = held_status[0]
                            assert_pending(core)
                            case.assertLess(time.monotonic() - started, 4,
                                            'pending-state evidence must not be a timeout')
                            released += 1
                            case.assertLessEqual(released, 2, 'only older plus queued fresh read permitted')
                            completed_status.append(core)
                            held.fulfill(json=synthetic_cores)
                            held_status.pop(0)
                        page.wait_for_timeout(10)
                    case.assertGreaterEqual(released, 1)
                    modes['hold_status'] = False
                    assert_idle()
                case.assertEqual(held_posts, [])
                case.assertEqual(held_status, [])
                case.assertEqual(mocked_posts, expected_posts, 'no duplicate or automatic retry')
                case.assertEqual(writes, expected_posts, 'all observed writes are explicitly mocked')
                case.assertEqual(unexpected_writes, [], 'zero unintended API mutations')
                case.assertEqual(failed, [], 'no timeout/aborted mock masquerades as recovery')
        case.assertEqual(len(mocked_posts), 8)
        case.assertGreaterEqual(len(completed_status), 6)
        case.assertEqual(case.external, [])
        case.assertEqual(case.errors, [])
        case.assert_private()
        print('Core restart UI: real Chromium/pinned native buttons; both orderings, owner spinner, '
              'shared disabled state, fresh-status hold and success/valid:false/409 recovery; '
              '8 explicitly mocked restart POSTs, synthetic status GETs, 0 server-bound mutations; '
              'no actual restart/API/runtime qualification')
    finally:
        # Resolve held routes while interception still blocks every mutation.
        # Do not clear the existing global pageerror/console/external audit.
        modes['hold_status'] = False
        cleanup_errors = []
        for route in list(held_posts) + [route for route, _ in held_status]:
            try:
                route.abort()
            except Exception as error:
                cleanup_errors.append(str(error))
        held_posts.clear()
        held_status.clear()
        try:
            page.goto('about:blank')
        finally:
            context.unroute('**/api/**', intercept)
            page.remove_listener('request', observe)
            page.remove_listener('requestfailed', observe_failure)
        case.assertEqual(cleanup_errors, [], 'all held synthetic routes must clean up')
