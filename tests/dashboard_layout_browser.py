"""Native dashboard geometry in the existing authenticated proxy browser gate.

The archived circular-gauge stylesheet still has a separate integrity check.
The live check measures the current status strip and resource cards.
Normal measurements use actual read-only APIs. Separate, labelled long-text
and Unknown cases alter GET replies only, not accounts, hosts or controllers.
"""
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlparse


BASELINE_SOURCE_COMMIT = '8ed33ec446c4b03bf3a76ae7847957c7da58fa07'
BASELINE_SHA256 = '90a5ef35714aff5321ab49e90268eba19c6c2d5fba937a70d5bd883f9114d2b5'
BASELINE_CSS = Path(__file__).parent / 'fixtures/dashboard_pre_responsive.css'
CARD_IDS = ('cpu-status', 'memory-status', 'xray-core-status', 'singbox-core-status')
NAV_IDS = ('dashboard', 'inbounds', 'routing', 'subscriptions', 'proxy',
           'certificates', 'maintenance', 'cores', 'diagnostics', 'firewall',
           'security', 'account', 'legacy')
VIEWPORTS = ((390, 844, 2), (768, 1024, 2), (1440, 1000, 4))
TOLERANCE = 1.0
HEADER_READY_TIMEOUT_MS = 5000

# Observe natural completion only. Transformed DOM/Range rectangles cannot be
# combined with untransformed CSS padding while ElTag's appear transition runs.
HEADER_LAYOUT_READY = """() => {
    const tags = Array.from(document.querySelectorAll('.header .el-tag'));
    return tags.length === 3 && tags.every(tag => {
        const style = getComputedStyle(tag);
        return style.transform === 'none' && style.opacity === '1' &&
            !Array.from(tag.classList).some(name =>
                /^el-zoom-in-center-(enter|leave)-/.test(name)) &&
            !tag.getAnimations().some(animation =>
                animation.pending || animation.playState === 'running');
    });
}"""


def wait_for_header_layout(page):
    # One prerequisite poll with one total deadline, not a geometry retry.
    page.wait_for_function(HEADER_LAYOUT_READY, timeout=HEADER_READY_TIMEOUT_MS)


# DOM geometry observation only: no state/controller/timer or style injection.
MEASURE_BOX = """element => {
    const rect = element.getBoundingClientRect(), style = getComputedStyle(element);
    const number = name => parseFloat(style[name]) || 0;
    const box = value => ({left: value.left, right: value.right,
        top: value.top, bottom: value.bottom, width: value.width, height: value.height});
    const text = [], walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
        if (!walker.currentNode.textContent.trim()) continue;
        const range = document.createRange();
        range.selectNodeContents(walker.currentNode);
        for (const fragment of range.getClientRects()) {
            if (fragment.width && fragment.height) text.push(box(fragment));
        }
    }
    return {rect: box(rect), content: {
        left: rect.left + number('borderLeftWidth') + number('paddingLeft'),
        right: rect.right - number('borderRightWidth') - number('paddingRight'),
        top: rect.top + number('borderTopWidth') + number('paddingTop'),
        bottom: rect.bottom - number('borderBottomWidth') - number('paddingBottom')},
        clientWidth: element.clientWidth, scrollWidth: element.scrollWidth,
        clientHeight: element.clientHeight, scrollHeight: element.scrollHeight,
        text: text,
        diagnostic: {id: element.id, tag: element.tagName, classes: element.className,
            transform: style.transform, transformOrigin: style.transformOrigin,
            opacity: style.opacity, lineHeight: style.lineHeight,
            transitionDuration: style.transitionDuration,
            borderLeft: number('borderLeftWidth'), borderRight: number('borderRightWidth'),
            paddingLeft: number('paddingLeft'), paddingRight: number('paddingRight'),
            offsetWidth: element.offsetWidth, offsetHeight: element.offsetHeight,
            animations: element.getAnimations().map(animation => ({
                playState: animation.playState, currentTime: animation.currentTime,
                duration: animation.effect ? animation.effect.getTiming().duration : null}))}};
}"""


def read_baseline_css(path=BASELINE_CSS):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != BASELINE_SHA256:
        raise ValueError('Archived dashboard CSS differs from ' + BASELINE_SOURCE_COMMIT)
    return data


def horizontal_excess(inner, outer):
    """A finite, specific containment metric, never an arbitrary test failure."""
    values = [inner['left'], inner['right'], outer['left'], outer['right']]
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
        raise ValueError('Non-finite dashboard geometry')
    return max(outer['left'] - inner['left'], inner['right'] - outer['right'], 0)


def replacement_headers(headers):
    # Changed fixture bytes invalidate entity metadata, not security policy.
    # Playwright regenerates content-length from the replacement body.
    return {key: value for key, value in headers.items()
            if key.lower() not in ('content-length', 'etag', 'last-modified')}


def assert_dashboard_responsive_layout(case):
    from playwright.sync_api import expect

    page, context = case.page, case.context
    candidate_css = (Path(__file__).parents[1] / 'web/css/style.css').read_bytes()
    style_url = case.base + '/ui/css/style.css'
    shell_url = case.base + '/ui/css/shell.css'
    shell_css = (Path(__file__).parents[1] / 'web/css/shell.css').read_bytes()
    operations_url = case.base + '/ui/css/simple-operations.css'
    operations_css = (Path(__file__).parents[1] / 'web/css/simple-operations.css').read_bytes()
    writes, real_reads = [], set()
    status_paths = ('/api/auth/me', '/api/system/status', '/api/cores/status')
    document_headers = None
    phase = 'actual'

    def observe(request):
        path = urlparse(request.url).path
        if path.startswith('/api/') and request.method != 'GET':
            writes.append((request.method, path))

    def observe_response(response):
        path = urlparse(response.url).path
        if path in status_paths:
            case.assertEqual(response.status, 200, path)
            case.assertEqual(response.headers.get('cache-control'), 'no-store', path)
            real_reads.add(path)

    def visit():
        nonlocal document_headers
        with page.expect_response(style_url) as stylesheet, page.expect_response(shell_url) as shell_response, \
                page.expect_response(operations_url) as operations_response:
            response = page.goto(case.base + '/ui/')
        case.assertEqual(response.status, 200)
        case.assertEqual(response.headers.get('cache-control'), 'no-store')
        # /ui/ is unchanged StaticFiles and currently has no CSP header. Keep
        # that fact distinct from /proxy's explicit same-source policy below.
        headers = {'content-security-policy': response.headers.get('content-security-policy'),
                   'cache-control': response.headers.get('cache-control')}
        if document_headers is None:
            document_headers = headers
        case.assertEqual(headers, document_headers, 'CSS must not change document policy')
        case.assertEqual(stylesheet.value.status, 200)
        case.assertEqual(stylesheet.value.body(), candidate_css)
        case.assertEqual(shell_response.value.status, 200)
        case.assertEqual(shell_response.value.body(), shell_css)
        case.assertEqual(operations_response.value.status, 200)
        case.assertEqual(operations_response.value.body(), operations_css)
        expect(page.locator('.vui-current-user')).to_be_visible()
        expect(page.locator('.dashboard-view .overview-card')).to_have_count(4)

    def known():
        for identifier in CARD_IDS:
            expect(page.locator('#' + identifier)).to_have_attribute('data-status', 'known')
        expect(page.locator('.header .vui-current-user')).not_to_have_text('')
        expect(page.locator('.dashboard-view .el-progress--line')).to_have_count(2)

    def box(selector):
        return page.locator(selector).evaluate(MEASURE_BOX)

    def no_horizontal_clip(measurement, label, text=True):
        case.assertGreater(measurement['clientWidth'], 0, label)
        case.assertLessEqual(measurement['scrollWidth'],
                             measurement['clientWidth'] + TOLERANCE, label)
        if text:
            case.assertTrue(measurement['text'], label + ' has no rendered text')
            for fragment in measurement['text']:
                case.assertLessEqual(horizontal_excess(fragment, measurement['content']),
                                     TOLERANCE, label + ' clips text: ' + json.dumps({
                                         'measurement': measurement, 'fragment': fragment}, sort_keys=True))

    def in_viewport(locator, label):
        expect(locator).to_be_visible()
        rect = locator.bounding_box()
        case.assertIsNotNone(rect, label)
        viewport = page.viewport_size
        case.assertGreaterEqual(rect['x'], -TOLERANCE, label)
        case.assertLessEqual(rect['x'] + rect['width'], viewport['width'] + TOLERANCE, label)
        case.assertGreaterEqual(rect['y'], -TOLERANCE, label)
        case.assertLessEqual(rect['y'] + rect['height'], viewport['height'] + TOLERANCE, label)

    def layout(width, height, columns, with_gauges=True):
        page.set_viewport_size({'width': width, 'height': height})
        aside = page.locator('.vui-sidebar')
        expect(aside).to_have_css('width', str(min(260, width - 44) if width <= 800 else 220) + 'px')
        dimensions = page.evaluate('''() => ({width: innerWidth,
            document: document.documentElement.scrollWidth, body: document.body.scrollWidth})''')
        case.assertLessEqual(dimensions['document'], width + TOLERANCE, dimensions)
        case.assertLessEqual(dimensions['body'], width + TOLERANCE, dimensions)
        no_horizontal_clip(box('.layout-container > .el-container > .el-main'), 'main', text=False)

        cards = page.locator('.overview-status-grid > div').evaluate_all('''elements =>
            elements.map(element => { const r = element.getBoundingClientRect();
                return {left: r.left, top: r.top, width: r.width}; })''')
        case.assertEqual(len(cards), 4)
        for index, card in enumerate(cards):
            case.assertAlmostEqual(card['width'], cards[0]['width'], delta=TOLERANCE)
            row_start = (index // columns) * columns
            case.assertAlmostEqual(card['top'], cards[row_start]['top'], delta=TOLERANCE)
            if index % columns:
                case.assertGreater(card['left'], cards[index - 1]['left'] + TOLERANCE)
            elif index:
                case.assertGreater(card['top'], cards[index - columns]['top'] + TOLERANCE)
                case.assertAlmostEqual(card['left'], cards[0]['left'], delta=TOLERANCE)

        header = page.locator('.header')
        header.scroll_into_view_if_needed()
        wait_for_header_layout(page)
        print(f'Dashboard natural header transition settled: phase={phase} '
              f'viewport={width}x{height} tags=3')
        in_viewport(header, 'header')
        measured_header = box('.header')
        no_horizontal_clip(measured_header, 'header')
        controls = page.locator('.header .el-tag, .header button, .header .vui-current-user')
        case.assertEqual(controls.count(), 6)
        rectangles = []
        for index, control in enumerate(controls.all()):
            label = f'phase={phase} viewport={width}x{height} columns={columns} header control[{index}]'
            in_viewport(control, label)
            measurement = control.evaluate(MEASURE_BOX)
            if measurement['diagnostic']['transform'] != 'none':
                print('Dashboard layout transformed control observation: ' + json.dumps({
                    'label': label, 'measurement': measurement}, sort_keys=True))
            no_horizontal_clip(measurement, label)
            rect = measurement['rect']
            case.assertLessEqual(horizontal_excess(rect, measured_header['content']), TOLERANCE)
            case.assertGreaterEqual(rect['top'], measured_header['content']['top'] - TOLERANCE)
            case.assertLessEqual(rect['bottom'], measured_header['content']['bottom'] + TOLERANCE)
            for other in rectangles:
                overlap_x = min(rect['right'], other['right']) - max(rect['left'], other['left'])
                overlap_y = min(rect['bottom'], other['bottom']) - max(rect['top'], other['top'])
                case.assertFalse(overlap_x > TOLERANCE and overlap_y > TOLERANCE,
                                 'header controls overlap')
            rectangles.append(rect)
        for button in page.locator('.header button').all():
            expect(button).to_be_enabled()  # Geometry only: never invoke a restart.

        for identifier in CARD_IDS:
            card = page.locator('#' + identifier)
            card.scroll_into_view_if_needed()
            in_viewport(card, identifier)
            no_horizontal_clip(box('#' + identifier), identifier)
            measured_card = box('#' + identifier)
            case.assertLessEqual(measured_card['scrollHeight'], measured_card['clientHeight'] + TOLERANCE,
                                 identifier + ' clips vertically')
            if with_gauges and identifier in CARD_IDS[:2]:
                gauge = box('#' + identifier + ' .el-progress--line')
                case.assertGreater(gauge['rect']['width'], 0)
                case.assertLessEqual(horizontal_excess(gauge['rect'], measured_card['content']), TOLERANCE)
        # Every existing item is reachable by ordinary vertical scrolling.
        if width <= 800:
            page.locator('.vui-menu-toggle').click()
            expect(page.locator('.vui-sidebar')).to_have_css('transform', 'matrix(1, 0, 0, 1, 0, 0)')
        menus = page.locator('.vui-nav-link[data-nav-id]')
        case.assertEqual(tuple(item.get_attribute('data-nav-id') for item in menus.all()), NAV_IDS)
        for item in menus.all():
            item.scroll_into_view_if_needed()
            in_viewport(item, 'menu item')
            no_horizontal_clip(item.evaluate(MEASURE_BOX), 'menu item')
        if width <= 800:
            page.locator('.vui-drawer-close').click()
        case.assert_private()
        return {'headerHeight': measured_header['rect']['height'],
                'columnWidths': [card['width'] for card in cards]}

    page.on('request', observe)
    page.on('response', observe_response)
    try:
        page.goto('about:blank')
        visit()
        known()
        for width, height, columns in VIEWPORTS:
            result = layout(width, height, columns)
            if width == 1440:
                case.assertEqual(len(result['columnWidths']), 4)
                expect(page.locator('.vui-sidebar')).to_have_css('width', '220px')
        case.assertEqual(real_reads, set(status_paths))

        page.set_viewport_size({'width': 390, 'height': 844})
        page.locator('.vui-menu-toggle').click()
        expect(page.locator('.vui-sidebar')).to_have_css('transform', 'matrix(1, 0, 0, 1, 0, 0)')
        proxy_menu = page.locator('.vui-nav-link[data-nav-id="proxy"]')
        proxy_menu.scroll_into_view_if_needed()
        in_viewport(proxy_menu, 'proxy menu')
        with page.expect_response(case.base + '/proxy') as proxy_document:
            proxy_menu.click()
        page.wait_for_url(case.base + '/proxy')
        case.assertEqual(proxy_document.value.status, 200)
        case.assertEqual(proxy_document.value.headers.get('cache-control'), 'no-store')
        for directive in ("script-src 'self'", "connect-src 'self'", "style-src 'self'"):
            case.assertIn(directive, proxy_document.value.headers['content-security-policy'])
        from test_proxy_browser import expand_details
        expand_details(page.locator('#connection-advanced'))
        expect(page.locator('#proxy-workspace')).to_be_visible()
        case.assert_private()
        page.go_back()
        page.wait_for_url(case.base + '/ui/')
        known()
        layout(*VIEWPORTS[0])

        # Stress only otherwise-unavailable long/Unknown GET presentation.
        # Fetch real authenticated replies first, preserving status and headers.
        page.goto('about:blank')
        mode = phase = 'long'
        long_username = 'layout_' + 'x' * 57  # Existing account contract allows 64 characters.
        long_os = 'SyntheticOS-' + 'LongRelease' * 12

        def stress(route):
            case.assertEqual(route.request.method, 'GET')
            response = route.fetch()
            case.assertEqual(response.status, 200)
            payload = response.json()
            path = urlparse(route.request.url).path
            if path == '/api/auth/me':
                payload['username'] = long_username
            elif mode == 'unknown':
                payload = {}
            elif path == '/api/system/status':
                payload['os'] = long_os
            route.fulfill(response=response, headers=replacement_headers(response.headers), json=payload)

        for path in status_paths:
            context.route(case.base + path, stress)
        try:
            visit()
            known()
            expect(page.locator('#system-status-tag')).to_have_text('运行系统：' + long_os)
            expect(page.locator('.header .vui-current-user')).to_have_text(long_username + ' · 账户')
            for viewport in VIEWPORTS:
                layout(*viewport)
            page.goto('about:blank')
            mode = phase = 'unknown'
            with page.expect_response(case.base + '/api/system/status') as system_reply, \
                    page.expect_response(case.base + '/api/cores/status') as core_reply:
                visit()
            case.assertEqual(system_reply.value.json(), {})
            case.assertEqual(core_reply.value.json(), {})
            expect(page.locator('.header .vui-current-user')).to_have_text(long_username + ' · 账户')
            for identifier in CARD_IDS:
                expect(page.locator('#' + identifier)).to_have_attribute('data-status', 'unknown')
                expect(page.locator('#' + identifier)).to_contain_text('状态未确认')
            expect(page.locator('.dashboard-view .el-progress--line')).to_have_count(0)
            for viewport in VIEWPORTS:
                layout(*viewport, with_gauges=False)
        finally:
            page.goto('about:blank')
            for path in status_paths:
                context.unroute(case.base + path, stress)
        visit()
        known()
        case.assertEqual(writes, [], 'layout, resize and navigation must not mutate any API')
        case.assert_private()
        print('Dashboard native success: real API / 390:2 / 768:2 / 1440:4 status columns / '
              'unclipped cards and linear gauges / normal scroll / proxy and back / '
              'synthetic long-text and Unknown GET presentation / no writes')
    finally:
        page.remove_listener('request', observe)
        page.remove_listener('response', observe_response)
