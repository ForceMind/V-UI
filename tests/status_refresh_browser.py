"""Native dashboard action with authenticated, genuine disposable API replies.

The existing Chromium fixture supplies the page/server. Only delivery of actual
status GET replies is delayed, including headers/status/body unchanged. No API
writes, response fabrication, clock changes, controller calls, forced clicks or
local browser launches. This is UI/API read coverage, not core/device qualification.
"""
import json
import re
import sys
import time
from urllib.parse import urlparse


SYSTEM = '/api/system/status'
CORE = '/api/cores/status'
STATUS_PATHS = (SYSTEM, CORE)
HOLD_LIMIT_SECONDS = 4
COMPLETED_READS = '''targets => {
    const entries = performance.getEntriesByType('resource');
    return Object.entries(targets).every(([path, count]) => entries.filter(entry =>
        new URL(entry.name).pathname === path && entry.initiatorType === 'xmlhttprequest' &&
        entry.responseEnd > 0).length >= count);
}'''
BUTTON_GEOMETRY = '''button => {
    const box = element => {
        const rect = element.getBoundingClientRect();
        return {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom,
                width: rect.width, height: rect.height};
    };
    const label = document.createRange(); label.selectNodeContents(button);
    return {button: box(button), toolbar: box(button.closest('.view-header')),
        dashboard: box(button.closest('.dashboard-view')),
        label: Array.from(label.getClientRects()).map(rect => ({left: rect.left, right: rect.right})),
        viewport: document.documentElement.clientWidth,
        pageWidth: document.documentElement.scrollWidth,
        buttonWidth: button.clientWidth, buttonScrollWidth: button.scrollWidth,
        inHeader: !!button.closest('.header-right')};
}'''


def assert_status_refresh(case):
    from playwright.sync_api import expect
    from test_status_refresh import assert_status_refresh_control

    assert_status_refresh_control(case)  # This asset-enabled gate may not skip controls.
    page, context, base = case.page, case.context, case.base
    page.goto('about:blank')  # End setUp's controller before recording this scenario.
    requests = {path: [] for path in STATUS_PATHS}
    finished = {path: [] for path in STATUS_PATHS}
    active = {path: set() for path in STATUS_PATHS}
    maximum_active = {path: 0 for path in STATUS_PATHS}
    held = {path: [] for path in STATUS_PATHS}
    originals, responses, started = {}, {}, {}
    writes, failures, console_errors, timeline = [], [], [], []
    phase = {'width': None, 'hold': True, 'closing': False}
    origin = time.monotonic()

    def record(event, request):
        timeline.append({'event': event, 'width': phase['width'],
                         'path': urlparse(request.url).path,
                         'read': requests[urlparse(request.url).path].index(request) + 1,
                         'elapsed_ms': round((time.monotonic() - origin) * 1000, 3)})

    def observe(request):
        path = urlparse(request.url).path
        if path.startswith('/api/') and request.method != 'GET':
            writes.append((request.method, path))
        if path in requests:
            case.assertEqual(request.method, 'GET')
            requests[path].append(request)
            started[request] = time.monotonic()
            active[path].add(request)
            maximum_active[path] = max(maximum_active[path], len(active[path]))
            record('request', request)

    def received(response):
        if response.request in started:
            responses[response.request] = response
            record('response', response.request)

    def complete(request):
        if request in started:
            path = urlparse(request.url).path
            active[path].discard(request)
            finished[path].append(request)
            record('finished', request)

    def failed(request):
        if request in started:
            # Destroying a finished scenario's page can cancel a natural poll.
            # Only that explicit teardown's exact Chromium cancellation is exempt.
            if phase['closing'] and request not in originals and request.failure == 'net::ERR_ABORTED':
                record('navigation-cancelled-natural-poll', request)
            else:
                failures.append((urlparse(request.url).path, request.failure))
                record('failed', request)
            active[urlparse(request.url).path].discard(request)

    def console(message):
        if message.type == 'error':
            console_errors.append(message.text)

    def intercept(route):
        request = route.request
        if not request.url.startswith(base + '/'):
            route.fallback()  # Preserve the fixture's external-request denial.
            return
        path = urlparse(request.url).path
        if request.method != 'GET':
            route.abort()  # Unexpected writes never reach even this disposable server.
            return
        if path not in STATUS_PATHS or not phase['hold'] or len(requests[path]) > 2:
            # Only initial plus own queued fresh replies are delayed. Any
            # later natural polls keep genuine delivery and unchanged timing.
            route.fallback()
            return
        case.assertEqual(request.headers.get('x-vui-request'), '1')
        response = route.fetch(timeout=5000, max_redirects=0)
        case.assertEqual(response.url, base + path)
        case.assertEqual(response.status, 200, response.text())
        entry = {'route': route, 'request': request, 'response': response,
                 'body': response.body(), 'headers': response.headers}
        originals[request] = entry
        held[path].append(entry)
        record('held-genuine-response', request)
        # The original Axios 5000ms deadline remains active. Only delivery is held.

    def remaining_ms():
        pending = [entry for values in held.values() for entry in values]
        oldest = min((started[entry['request']] for entry in pending), default=time.monotonic())
        remaining = (oldest + HOLD_LIMIT_SECONDS - time.monotonic()) * 1000
        case.assertGreater(remaining, 0, 'observations must finish before unchanged status timeout')
        return max(1, int(remaining))

    def await_held(paths):
        # Bounded delivery observation; this short pump does not replace browser
        # clocks or make a failed/timeout response count as completed evidence.
        deadline = time.monotonic() + HOLD_LIMIT_SECONDS
        while not all(held[path] for path in paths):
            case.assertLess(time.monotonic(), deadline, 'expected genuine held status reads')
            remaining_ms()
            page.wait_for_timeout(10)
        for path in paths:
            case.assertEqual(len(held[path]), 1, 'single-flight held read per endpoint')
        case.assertEqual(failures, [])

    def control():
        return page.locator('.dashboard-view').get_by_role('button', name='刷新状态', exact=True)

    def pending():
        expect(control()).to_be_disabled(timeout=remaining_ms())
        expect(control()).to_have_js_property('disabled', True, timeout=remaining_ms())
        expect(control()).to_have_attribute('aria-disabled', 'true', timeout=remaining_ms())
        expect(control()).to_have_class(re.compile(r'\bis-loading\b'), timeout=remaining_ms())
        expect(control().locator('.el-icon.is-loading')).to_have_count(1, timeout=remaining_ms())

    def idle():
        expect(control()).to_be_enabled(timeout=4000)
        expect(control()).to_have_js_property('disabled', False)
        expect(control()).to_have_attribute('aria-disabled', 'false')
        expect(control()).not_to_have_class(re.compile(r'\bis-loading\b'))
        expect(control().locator('.el-icon.is-loading')).to_have_count(0)
        expect(control()).to_have_attribute('type', 'button')
        expect(control()).to_have_attribute('aria-describedby', 'status-refresh-help')
        expect(page.locator('#status-refresh-help')).to_have_text('只读取最新状态，不会重启核心。')

    def release(path, expect_new=False):
        case.assertEqual(len(held[path]), 1)
        entry = held[path][0]
        remaining = remaining_ms()
        record('release-unchanged-response', entry['request'])
        if expect_new:
            before = len(requests[path])
            with page.expect_request(lambda request: urlparse(request.url).path == path and
                                     request != entry['request'], timeout=remaining):
                entry['route'].fulfill(response=entry['response'])
                held[path].remove(entry)
            await_held([path])
            case.assertEqual(len(requests[path]), before + 1, 'one queued fresh read')
        else:
            with page.expect_response(lambda response: response.request == entry['request'], timeout=remaining):
                entry['route'].fulfill(response=entry['response'])
                held[path].remove(entry)
        case.assertLess(time.monotonic() - started[entry['request']], HOLD_LIMIT_SECONDS,
                        'release must not be timeout-based recovery')

    def completed(targets):
        page.wait_for_function(COMPLETED_READS, arg=targets, timeout=4000)
        required = {request for path, count in targets.items() for request in requests[path][:count]}
        known = lambda: {request for values in finished.values() for request in values}
        if not required <= known():
            page.wait_for_event('requestfinished',
                                predicate=lambda request: required <= known() | {request}, timeout=4000)
        case.assertTrue(required <= known())
        for request in required:
            case.assertIn(request, originals)
            case.assertIn(request, responses)
            original, actual = originals[request], responses[request]
            case.assertEqual(actual.status, original['response'].status)
            case.assertEqual(actual.body(), original['body'])
            for name, value in original['headers'].items():
                case.assertEqual(actual.headers.get(name), value, name)

    def geometry():
        control().scroll_into_view_if_needed()
        measurements = control().evaluate(BUTTON_GEOMETRY)
        case.assertFalse(measurements['inHeader'])
        case.assertLessEqual(measurements['pageWidth'], measurements['viewport'] + 1)
        case.assertLessEqual(measurements['buttonScrollWidth'], measurements['buttonWidth'] + 1)
        button = measurements['button']
        case.assertGreater(button['width'], 0)
        case.assertGreater(button['height'], 0)
        for container in (measurements['toolbar'], measurements['dashboard'],
                          {'left': 0, 'right': measurements['viewport']}):
            case.assertGreaterEqual(button['left'], container['left'] - 1)
            case.assertLessEqual(button['right'], container['right'] + 1)
        for rect in measurements['label']:
            case.assertGreaterEqual(rect['left'], button['left'] - 1)
            case.assertLessEqual(rect['right'], button['right'] + 1)
        expect(control()).to_be_visible()
        control().focus()
        expect(control()).to_be_focused()
        return measurements

    page.on('request', observe)
    page.on('response', received)
    page.on('requestfinished', complete)
    page.on('requestfailed', failed)
    page.on('console', console)
    context.route('**/api/**', intercept)
    try:
        for width, height, first in ((1440, 1000, SYSTEM), (390, 844, CORE)):
            phase['width'] = width
            page.set_viewport_size({'width': width, 'height': height})
            # Every viewport gets a new controller with its genuine initial
            # reads held, proving the manual action queues NEW status reads.
            page.goto(base + '/ui/', wait_until='domcontentloaded')
            await_held(STATUS_PATHS)
            idle()
            case.assertEqual([len(requests[path]) for path in STATUS_PATHS], [1, 1])
            control().click(timeout=remaining_ms())  # Ordinary enabled native input.
            pending()
            case.assertEqual([len(requests[path]) for path in STATUS_PATHS], [1, 1])
            for path in STATUS_PATHS:
                release(path, expect_new=True)
                pending()
            case.assertEqual([len(requests[path]) for path in STATUS_PATHS], [2, 2])
            release(first)
            pending()  # The other own fresh read still owns the spinner/admission.
            release(CORE if first == SYSTEM else SYSTEM)
            idle()
            completed({SYSTEM: 2, CORE: 2})
            for identifier in ('system-status-tag', 'cpu-status', 'memory-status',
                               'xray-status-tag', 'singbox-status-tag'):
                expect(page.locator('#' + identifier)).to_have_attribute('data-status', 'known')
            phase['hold'] = False
            print('Status refresh native geometry: ' + json.dumps({'width': width, **geometry()}))
            expect(page.locator('.el-message--success')).to_have_count(0)
            case.assertEqual(maximum_active, {SYSTEM: 1, CORE: 1})
            case.assertEqual(writes, [])
            case.assertEqual(failures, [])
            case.assertEqual(console_errors, [])
            case.assertEqual(case.external, [])
            case.assertEqual(case.errors, [])
            case.assert_private()
            phase['closing'] = True
            page.goto('about:blank')
            case.assertEqual(failures, [])
            # Preserve global error/write audit and timeline; only the new page's
            # native resource-count observations restart for the next viewport.
            for path in STATUS_PATHS:
                case.assertEqual(held[path], [])
                requests[path].clear()
                finished[path].clear()
                active[path].clear()
                maximum_active[path] = 0
            originals.clear()
            responses.clear()
            started.clear()
            phase['hold'] = True
            phase['closing'] = False
        print('Status refresh: real Chromium / genuine authenticated read-only API replies; '
              'desktop and phone-width normal clicks; native disabled/loading/focus/layout; '
              'one normal click per viewport, queued fresh reads and both completion orders; '
              'later natural polls stay unchanged; unchanged response bytes; '
              'zero writes, no controller/clock changes; no core or device qualification')
    finally:
        original_error = sys.exc_info()[1]
        cleanup_errors = []

        def cleanup(action):
            try:
                action()
            except Exception as error:
                cleanup_errors.append(error)

        phase['hold'] = False
        for path in STATUS_PATHS:
            for entry in list(held[path]):
                cleanup(lambda entry=entry: entry['route'].fulfill(response=entry['response']))
            held[path].clear()
        cleanup(lambda: page.goto('about:blank'))
        cleanup(lambda: context.unroute('**/api/**', intercept))
        for event, handler in (('request', observe), ('response', received),
                               ('requestfinished', complete), ('requestfailed', failed), ('console', console)):
            cleanup(lambda event=event, handler=handler: page.remove_listener(event, handler))
        print('Status refresh native request timeline: ' + json.dumps(timeline))
        if cleanup_errors:
            target = original_error or cleanup_errors[0]
            for error in cleanup_errors:
                target.add_note('Status refresh cleanup also failed: ' + repr(error))
            if original_error is None:
                raise target
