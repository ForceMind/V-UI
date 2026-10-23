"""Native dashboard geometry in the existing authenticated proxy browser gate.

The baseline fixture is the exact, unmodified web/css/style.css from source
commit 8ed33ec446c4b03bf3a76ae7847957c7da58fa07. Its hash is checked before use;
only that same-origin CSS response is replaced, never Vue, app.js or the page.
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
VIEWPORTS = ((390, 844, 1), (768, 1024, 2), (1440, 1000, 4))
TOLERANCE = 1.0

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
    baseline = read_baseline_css()
    candidate_css = (Path(__file__).parents[1] / 'web/css/style.css').read_bytes()
    style_url = case.base + '/ui/css/style.css'
    writes, real_reads, old_css_reads = [], set(), []
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

    def old_css(route):
        case.assertEqual(route.request.url, style_url)
        case.assertEqual(route.request.method, 'GET')
        old_css_reads.append(route.request.url)
        response = route.fetch()
        case.assertEqual(response.status, 200)
        route.fulfill(response=response, headers=replacement_headers(response.headers), body=baseline)

    def visit(expected_css=candidate_css):
        nonlocal document_headers
        with page.expect_response(style_url) as stylesheet:
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
        case.assertEqual(stylesheet.value.body(), expected_css,
                         'browser must receive exactly the selected stylesheet bytes')
        expect(page.get_by_role('menuitem', name='系统状态', exact=True)).to_be_visible()
        expect(page.locator('.dashboard-view .el-card')).to_have_count(4)

    def known():
        for identifier in CARD_IDS:
            expect(page.locator('#' + identifier)).to_have_attribute('data-status', 'known')
        expect(page.locator('.header .el-dropdown-link')).not_to_have_text('')
        expect(page.locator('.dashboard-view .el-progress-circle')).to_have_count(2)

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
        aside = page.locator('.aside-menu')
        expect(aside).to_have_css('width', str(width if width < 768 else 220) + 'px')
        dimensions = page.evaluate('''() => ({width: innerWidth,
            document: document.documentElement.scrollWidth, body: document.body.scrollWidth})''')
        case.assertLessEqual(dimensions['document'], width + TOLERANCE, dimensions)
        case.assertLessEqual(dimensions['body'], width + TOLERANCE, dimensions)
        no_horizontal_clip(box('.layout-container > .el-container > .el-main'), 'main', text=False)

        cards = page.locator('.dashboard-view > .el-row > .el-col').evaluate_all('''elements =>
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
        in_viewport(header, 'header')
        measured_header = box('.header')
        no_horizontal_clip(measured_header, 'header')
        controls = page.locator('.header .el-tag, .header button, .header .el-dropdown-link')
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
            for part in ('.el-card__header', '.el-card__body'):
                measured_part = box('#' + identifier + ' ' + part)
                no_horizontal_clip(measured_part, identifier + part)
                case.assertLessEqual(measured_part['scrollHeight'],
                                     measured_part['clientHeight'] + TOLERANCE,
                                     identifier + part + ' clips vertically')
            if with_gauges and identifier in CARD_IDS[:2]:
                body = box('#' + identifier + ' .el-card__body')
                gauge = box('#' + identifier + ' .el-progress-circle')['rect']
                case.assertAlmostEqual(gauge['width'], 126, delta=TOLERANCE)
                case.assertAlmostEqual(gauge['height'], 126, delta=TOLERANCE)
                case.assertLessEqual(horizontal_excess(gauge, body['content']), TOLERANCE,
                                     identifier + ' gauge outside body content')
                case.assertGreaterEqual(gauge['top'], body['content']['top'] - TOLERANCE)
                case.assertLessEqual(gauge['bottom'], body['content']['bottom'] + TOLERANCE)
        # Every existing item is reachable by ordinary vertical scrolling.
        menus = page.get_by_role('menuitem')
        case.assertEqual(menus.count(), 9)
        for item in menus.all():
            item.scroll_into_view_if_needed()
            in_viewport(item, 'menu item')
            no_horizontal_clip(item.evaluate(MEASURE_BOX), 'menu item')
        case.assert_private()
        return {'headerHeight': measured_header['rect']['height'],
                'columnWidths': [card['width'] for card in cards]}

    page.on('request', observe)
    page.on('response', observe_response)
    # End setUp's controller before routing; a same-document /ui/ navigation
    # could otherwise miss the stylesheet interception or retain old polls.
    page.goto('about:blank')
    context.route(style_url, old_css)
    try:
        page.set_viewport_size({'width': 390, 'height': 844})
        visit(expected_css=baseline)
        known()
        case.assertEqual(old_css_reads, [style_url])
        case.assertEqual(real_reads, set(status_paths))
        observed = {}
        for identifier in CARD_IDS[:2]:
            body = box('#' + identifier + ' .el-card__body')
            gauge = box('#' + identifier + ' .el-progress-circle')['rect']
            case.assertAlmostEqual(gauge['width'], 126, delta=TOLERANCE)
            case.assertGreater(body['clientWidth'], 0)
            observed[identifier] = {
                'gaugeOutsideContentPx': horizontal_excess(gauge, body['content']),
                'bodyHorizontalOverflowPx': body['scrollWidth'] - body['clientWidth']}
        case.assertTrue(any(value['gaugeOutsideContentPx'] > TOLERANCE
                            for value in observed.values()),
                        'Known original CSS must show a specific finite gauge containment violation')
        case.assertEqual(case.external, [])
        case.assertEqual(case.errors, [])
        print('Dashboard old-CSS native containment: ' + json.dumps(observed, sort_keys=True))
        page.set_viewport_size({'width': 1440, 'height': 1000})
        expect(page.locator('.aside-menu')).to_have_css('width', '220px')
        desktop_before = {'headerHeight': box('.header')['rect']['height'],
                          'columnWidths': page.locator('.dashboard-view > .el-row > .el-col')
                          .evaluate_all('elements => elements.map(e => e.getBoundingClientRect().width)')}
    finally:
        context.unroute(style_url, old_css)

    try:
        page.goto('about:blank')
        visit()
        known()
        for width, height, columns in VIEWPORTS:
            result = layout(width, height, columns)
            if width == 1440:
                case.assertEqual(result, desktop_before, 'ordinary desktop geometry must be preserved')
        case.assertEqual(old_css_reads, [style_url], 'candidate must use its actual CSS response')

        page.set_viewport_size({'width': 390, 'height': 844})
        proxy_menu = page.get_by_role('menuitem', name='代理与中转', exact=True)
        proxy_menu.scroll_into_view_if_needed()
        in_viewport(proxy_menu, 'proxy menu')
        with page.expect_response(case.base + '/proxy') as proxy_document:
            proxy_menu.click()
        page.wait_for_url(case.base + '/proxy')
        case.assertEqual(proxy_document.value.status, 200)
        case.assertEqual(proxy_document.value.headers.get('cache-control'), 'no-store')
        for directive in ("script-src 'self'", "connect-src 'self'", "style-src 'self'"):
            case.assertIn(directive, proxy_document.value.headers['content-security-policy'])
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
            expect(page.locator('#system-status-tag')).to_have_text('Running on ' + long_os)
            expect(page.locator('.header .el-dropdown-link')).to_have_text(long_username)
            for viewport in VIEWPORTS:
                layout(*viewport)
            page.goto('about:blank')
            mode = phase = 'unknown'
            with page.expect_response(case.base + '/api/system/status') as system_reply, \
                    page.expect_response(case.base + '/api/cores/status') as core_reply:
                visit()
            case.assertEqual(system_reply.value.json(), {})
            case.assertEqual(core_reply.value.json(), {})
            expect(page.locator('.header .el-dropdown-link')).to_have_text(long_username)
            for identifier in CARD_IDS:
                expect(page.locator('#' + identifier)).to_have_attribute('data-status', 'unknown')
                expect(page.locator('#' + identifier)).to_contain_text('状态未确认')
            expect(page.locator('.dashboard-view .el-progress-circle')).to_have_count(0)
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
        print('Dashboard new-layout native success: real API / 390:1 / 768:2 / 1440:4 / '
              'unclipped cards and 126px gauges / normal scroll / proxy and back / '
              'synthetic long-text and Unknown GET presentation / no writes')
    finally:
        page.remove_listener('request', observe)
        page.remove_listener('response', observe_response)
