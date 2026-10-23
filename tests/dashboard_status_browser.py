"""Real Chromium/components with synthetic GET status replies, not host evidence."""
import re
import time
from urllib.parse import urlparse


def assert_dashboard_status_recovery(case):
    from playwright.sync_api import expect
    from test_dashboard_status import assert_dashboard_status

    # The ordinary suite may have no downloaded assets. This existing explicit
    # asset-enabled browser gate must also execute every deterministic control.
    assert_dashboard_status(case)
    page, context = case.page, case.context
    page.set_viewport_size({'width': 1440, 'height': 1000})
    # setUp.login waits for the menu, not completion of its status reads. End
    # that controller before routing so its late requests cannot occupy held.
    page.goto('about:blank')
    system_path, core_path = '/api/system/status', '/api/cores/status'
    modes = {system_path: 'hold', core_path: 'hold'}
    held, reads, writes, failed = {}, [], [], []
    observed_reads = set()
    system = {'os': 'Synthetic Linux', 'cpu': {'percent': 37, 'cores': 2},
              'memory': {'percent': 64, 'used': '64 MiB', 'total': '100 MiB'}}
    cores = {'xray': {'running': True, 'binary_exists': True},
             'sing-box': {'running': False, 'binary_exists': True}}

    def observe(request):
        if urlparse(request.url).path in modes:
            observed_reads.add(request)
        if urlparse(request.url).path.startswith('/api/') and request.method != 'GET':
            writes.append((request.method, urlparse(request.url).path))

    def observe_failure(request):
        if request in observed_reads:
            failed.append((urlparse(request.url).path, request.failure))

    def status(route):
        path = urlparse(route.request.url).path
        case.assertIn(path, modes)
        case.assertEqual(route.request.method, 'GET')
        reads.append(path)
        if modes[path] == 'hold':
            case.assertNotIn(path, held, 'one in-flight read per endpoint')
            held[path] = route
        elif modes[path] == 'fail':
            route.fulfill(status=503, json={'detail': 'Synthetic status unavailable'})
        else:
            route.fulfill(json=system if path == system_path else cores)

    def known(identifier, value=True, timeout=5000):
        expect(page.locator('#' + identifier)).to_have_attribute(
            'data-status', 'known' if value else 'unknown', timeout=timeout)

    def unknown_system():
        known('system-status-tag', False)
        expect(page.locator('#system-status-tag')).to_have_text('System Unknown')
        for identifier in ('cpu-status', 'memory-status'):
            known(identifier, False)
            expect(page.locator('#' + identifier)).to_contain_text('状态未确认')
            expect(page.locator('#' + identifier + ' .el-progress')).to_have_count(0)
        expect(page.locator('#cpu-status')).not_to_contain_text('2 Cores')
        expect(page.locator('#memory-status')).not_to_contain_text('MiB')

    def unknown_cores():
        for prefix, label in (('xray', 'Xray'), ('singbox', 'sing-box')):
            known(prefix + '-status-tag', False)
            expect(page.locator('#' + prefix + '-status-tag')).to_have_text(label + ' Unknown')
            known(prefix + '-core-status', False)
            expect(page.locator('#' + prefix + '-core-status')).to_contain_text('状态未确认')
            expect(page.locator('#' + prefix + '-core-status')).not_to_contain_text(
                re.compile('Installed|Missing|Running|Stopped'))

    def metrics(cpu, memory):
        known('system-status-tag')
        expect(page.locator('#system-status-tag')).to_have_text('Running on Synthetic Linux')
        for identifier, percentage in (('cpu-status', cpu), ('memory-status', memory)):
            known(identifier)
            expect(page.locator('#' + identifier).get_by_role('progressbar')).to_have_attribute(
                'aria-valuenow', str(percentage))

    page.on('request', observe)
    page.on('requestfailed', observe_failure)
    context.route('**' + system_path, status)
    context.route('**' + core_path, status)
    try:
        # Fresh controller: intercepted initial reads cannot supply a success.
        initial_started = time.monotonic()
        page.goto(case.base + '/ui/')
        deadline = initial_started + 4
        while len(held) != 2 and time.monotonic() < deadline:
            page.wait_for_timeout(20)
        case.assertEqual(set(held), {system_path, core_path})
        unknown_system()
        unknown_cores()
        case.assertLess(time.monotonic() - initial_started, 4,
                        'initial pending assertions must precede the unchanged 5s request timeout')
        case.assertEqual(failed, [], 'initial Unknown must not be timeout evidence')
        modes.update({system_path: 'ok', core_path: 'ok'})
        held.pop(system_path).fulfill(json=system)
        held.pop(core_path).fulfill(json=cores)
        known('cpu-status')
        metrics(37, 64)
        expect(page.locator('#xray-status-tag')).to_have_text('Xray Running')
        expect(page.locator('#singbox-core-status')).to_contain_text('Installed')
        expect(page.locator('#singbox-core-status')).to_contain_text('Stopped')

        # Natural existing 3s poll, no injected timer or restart action.
        modes[system_path] = 'fail'
        known('cpu-status', False, timeout=10000)
        unknown_system()
        known('xray-status-tag')
        expect(page.locator('#xray-status-tag')).to_have_text('Xray Running')
        known('singbox-core-status')

        # System recovery cannot turn a failed independent core read healthy.
        # The real core interval is 10s, so this bounded wait spans that period.
        system['cpu']['percent'] = system['memory']['percent'] = 0
        system['memory']['used'] = '0 MiB'
        modes.update({system_path: 'ok', core_path: 'fail'})
        known('cpu-status', timeout=10000)
        metrics(0, 0)
        known('xray-status-tag', False, timeout=15000)
        unknown_cores()
        metrics(0, 0)

        # False/zero are real confirmed values, unlike absent or failed reads.
        cores = {name: {'running': False, 'binary_exists': False}
                 for name in ('xray', 'sing-box')}
        modes[core_path] = 'ok'
        known('xray-status-tag', timeout=15000)
        for prefix, label in (('xray', 'Xray'), ('singbox', 'sing-box')):
            known(prefix + '-core-status')
            expect(page.locator('#' + prefix + '-status-tag')).to_have_text(label + ' Stopped')
            expect(page.locator('#' + prefix + '-core-status')).to_contain_text('Missing')
            expect(page.locator('#' + prefix + '-core-status')).to_contain_text('Stopped')
        metrics(0, 0)
        case.assertGreaterEqual(reads.count(system_path), 3)
        case.assertGreaterEqual(reads.count(core_path), 3)
        case.assertEqual(writes, [], 'display recovery must issue no API mutations')
        case.assertEqual(failed, [], 'a timed-out initial read must not be hidden by later recovery')
        case.assert_private()
        print('Dashboard status: real Chromium / synthetic GET replies / initial unknown / '
              'independent failures / natural poll recovery / confirmed zero and false OK')
    finally:
        for route in held.values():
            route.abort()
        context.unroute('**' + system_path, status)
        context.unroute('**' + core_path, status)
        page.remove_listener('request', observe)
        page.remove_listener('requestfailed', observe_failure)
