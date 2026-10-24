"""Real Chromium and authenticated disposable API reads, with one response delayed.

Only the initial warnings GET is held after fetching its genuine response. Status
requests, data, timers and visibility are not replaced. No write, restart, apply
or core runtime qualification is involved. Synthetic hidden/resume controls live
in the ordinary suite; this native scenario does not claim background-tab proof.
"""
import json
import sys
import time
from urllib.parse import urlparse


SYSTEM = '/api/system/status'
CORE = '/api/cores/status'
AUXILIARY = '/api/subscription/mihomo-warnings'
POLL_OBSERVATION_MS = 15000
COMPLETED_READS = '''targets => {
    const entries = performance.getEntriesByType('resource');
    return Object.entries(targets).every(([path, count]) => entries.filter(entry =>
        new URL(entry.name).pathname === path && entry.initiatorType === 'xmlhttprequest' &&
        entry.responseEnd > 0).length >= count);
}'''


def assert_polling_startup(case):
    from playwright.sync_api import expect
    from test_polling_startup import assert_polling_startup_control

    # This existing asset-enabled browser gate may not skip ordinary controls.
    assert_polling_startup_control(case)
    page, base = case.page, case.base
    page.set_viewport_size({'width': 1440, 'height': 1000})
    # End setUp's dashboard before observation; its late GETs must not count as
    # this controller's initial or periodic reads. Keep all global error logs.
    page.goto('about:blank')
    paths = (SYSTEM, CORE, AUXILIARY)
    requests = {path: [] for path in paths}
    finished = {path: [] for path in paths}
    active = {path: set() for path in paths}
    maximum_active = {path: 0 for path in paths}
    responses, held, failures, writes, console_errors = {}, [], [], [], []
    started_at, timeline = time.monotonic(), []

    def record(event, request):
        path = urlparse(request.url).path
        timeline.append({'event': event, 'path': path,
                         'request': requests[path].index(request) + 1,
                         'elapsed_ms': round((time.monotonic() - started_at) * 1000, 3)})

    def observe(request):
        path = urlparse(request.url).path
        if path.startswith('/api/') and request.method != 'GET':
            writes.append((request.method, path))
        if path in requests:
            case.assertEqual(request.method, 'GET')
            requests[path].append(request)
            record('request', request)
            active[path].add(request)
            maximum_active[path] = max(maximum_active[path], len(active[path]))

    def response_received(response):
        if response.request in active.get(urlparse(response.url).path, ()):
            responses[response.request] = response
            record('response', response.request)

    def complete(request):
        path = urlparse(request.url).path
        if request in active.get(path, ()):
            active[path].remove(request)
            finished[path].append(request)
            record('finished', request)

    def failed(request):
        path = urlparse(request.url).path
        if request in active.get(path, ()):
            failures.append((path, request.failure))
            record('failed', request)
            active[path].remove(request)

    def console(message):
        if message.type == 'error':
            console_errors.append(message.text)

    def hold_initial_warning(route):
        case.assertEqual(route.request.method, 'GET')
        case.assertEqual(route.request.url, base + AUXILIARY)
        case.assertEqual(len(held), 0, 'only one initial auxiliary GET may be held')
        response = route.fetch(timeout=5000, max_redirects=0)
        case.assertEqual(response.url, base + AUXILIARY)
        case.assertEqual(response.status, 200, response.text())
        held.append((route, response, response.body()))
        # Deliberately return without fulfillment. Playwright keeps this single
        # request pending while the actual page/controller continue naturally.

    def wait_for_completed(targets):
        # Observe native resource completions, not a fixed sleep, synthetic
        # response, timer advancement, manual controller call or retry.
        deadline = time.monotonic() + POLL_OBSERVATION_MS / 1000
        page.wait_for_function(COMPLETED_READS, arg=targets, timeout=POLL_OBSERVATION_MS)
        # ResourceTiming already observes full native completions. If delivery
        # of their Python callbacks lags, synchronize only those exact request
        # objects within the same deadline; never use unbounded finished().
        required = set()
        for path, count in targets.items():
            case.assertGreaterEqual(len(requests[path]), count)
            required.update(requests[path][:count])

        def completed():
            return {request for group in finished.values() for request in group}

        if not required <= completed():
            remaining = (deadline - time.monotonic()) * 1000
            case.assertGreater(remaining, 0, 'native completion deadline exceeded')
            page.wait_for_event('requestfinished',
                                predicate=lambda request: required <= completed() | {request},
                                timeout=remaining)
        case.assertTrue(required <= completed(), 'exact native request completion callbacks required')

    def assert_status_reads():
        for path in (SYSTEM, CORE):
            case.assertGreaterEqual(len(finished[path]), 2)
            case.assertEqual(len(set(requests[path])), len(requests[path]), 'distinct actual requests')
            case.assertEqual(maximum_active[path], 1, 'at most one full request per status endpoint')
            for request in finished[path]:
                case.assertIn(request, responses)
                case.assertEqual(responses[request].status, 200, responses[request].text())
        case.assertEqual(page.evaluate('statusPollers.length'), 2, 'read-only handle count; no duplicate pollers')
        for identifier in ('system-status-tag', 'cpu-status', 'memory-status',
                           'xray-status-tag', 'singbox-status-tag'):
            expect(page.locator('#' + identifier)).to_have_attribute('data-status', 'known')
        case.assertEqual(failures, [])
        case.assertEqual(writes, [])
        case.assertEqual(console_errors, [])
        case.assert_private()

    page.on('request', observe)
    page.on('response', response_received)
    page.on('requestfinished', complete)
    page.on('requestfailed', failed)
    page.on('console', console)
    page.route(base + AUXILIARY, hold_initial_warning)
    try:
        page.goto(base + '/ui/', wait_until='domcontentloaded')
        wait_for_completed({SYSTEM: 2, CORE: 2})
        case.assertEqual(len(held), 1)
        case.assertEqual(len(requests[AUXILIARY]), 1)
        case.assertEqual(finished[AUXILIARY], [])
        case.assertEqual(len(active[AUXILIARY]), 1, 'unrelated initial GET still pending across natural polls')
        assert_status_reads()

        # Release the exact fetched response, including original status, headers
        # and body. This is delayed genuine API data, not a product-response mock.
        route, response, original_body = held.pop()
        before = {path: len(finished[path]) for path in (SYSTEM, CORE)}
        record('release', route.request)
        route.fulfill(response=response)
        wait_for_completed({AUXILIARY: 1, SYSTEM: before[SYSTEM] + 1, CORE: before[CORE] + 1})
        case.assertEqual(len(requests[AUXILIARY]), 1)
        case.assertEqual(len(finished[AUXILIARY]), 1)
        case.assertEqual(active[AUXILIARY], set())
        case.assertEqual(held, [])
        auxiliary_response = responses[finished[AUXILIARY][0]]
        case.assertEqual(auxiliary_response.status, response.status)
        case.assertEqual(auxiliary_response.body(), original_body)
        for header, value in response.headers.items():
            case.assertEqual(auxiliary_response.headers.get(header), value, header)
        assert_status_reads()
        print('Dashboard startup: real Chromium / authenticated genuine status GETs / '
              'real warnings response held across natural status polls / '
              'exact response released / two poller handles / single-flight / no writes OK')
    finally:
        # If an assertion failed, settle only the held genuine read for cleanup;
        # the failure still propagates and never counts as red-side proof.
        original_error = sys.exc_info()[1]
        cleanup_errors = []

        def cleanup(action):
            try:
                action()
            except Exception as error:
                cleanup_errors.append(error)

        for route, response, _ in held:
            cleanup(lambda: route.fulfill(response=response))
        cleanup(lambda: page.unroute(base + AUXILIARY, hold_initial_warning))
        for event, handler in (('request', observe), ('response', response_received),
                               ('requestfinished', complete), ('requestfailed', failed),
                               ('console', console)):
            cleanup(lambda: page.remove_listener(event, handler))
        print('Dashboard polling native request/completion observations: ' + json.dumps(timeline))
        if cleanup_errors:
            target = original_error or cleanup_errors[0]
            for error in cleanup_errors:
                target.add_note('Polling startup cleanup also failed: ' + repr(error))
            if original_error is None:
                raise target
