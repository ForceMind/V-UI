"""Real Chromium/components and authenticated temporary-DB APIs with fake nodes.

No GET response is mocked. Fixture-only authenticated creates write synthetic
nodes to the existing disposable server; its absent cores honestly return
saved:true/applied:false. Search, refresh, open/cancel and clear must not write.
This is UI/list identity coverage, not runtime, routing or device qualification.
"""
from urllib.parse import urlparse


STATE = 'app._container._vnode.component.setupState'


def assert_inbound_local_search(case):
    from playwright.sync_api import expect
    from test_inbound_search import assert_inbound_search

    assert_inbound_search(case)
    page, context, base = case.page, case.context, case.base
    page.set_viewport_size({'width': 1440, 'height': 1000})
    headers = {'Origin': base, 'X-VUI-Request': '1'}
    uuids = ('11111111-1111-4111-8111-111111111171',
             '22222222-2222-4222-8222-222222222272',
             '33333333-3333-4333-8333-333333333373',
             '44444444-4444-4444-8444-444444444474')
    fixture_writes, page_writes, node_reads = [], [], []

    def rows():
        response = context.request.get(base + '/api/inbounds')
        case.assertEqual(response.status, 200, response.text())
        for uuid in uuids:
            case.assertNotIn(uuid, response.text())
        for row in response.json():
            case.assertFalse({'uuid', 'password', 'settings', 'stream_settings', 'instance_id'} & row.keys())
        return response.json()

    def create(label, port, uuid):
        response = context.request.post(base + '/api/inbounds', headers=headers, data={
            'core': 'xray', 'protocol': 'vless', 'remark': label, 'port': port,
            'enable': False, 'settings': {'users': [{'id': uuid}]}, 'stream_settings': {}})
        fixture_writes.append(('POST', '/api/inbounds'))
        case.assertEqual(response.status, 409, response.text())
        case.assertEqual(response.json()['detail'], {
            'saved': True, 'applied': False,
            'message': 'Pinned core binary is missing or not executable'})
        found = [row for row in rows() if row['remark'] == label]
        case.assertEqual(len(found), 1)
        return found[0]

    def observe(request):
        path = urlparse(request.url).path
        if path.startswith('/api/') and request.method != 'GET':
            page_writes.append((request.method, path))
        if path.startswith('/api/inbounds') and request.method == 'GET':
            node_reads.append(path)

    def refresh():
        with page.expect_response(lambda response: urlparse(response.url).path == '/api/inbounds'
                                  and response.request.method == 'GET') as loaded:
            page.get_by_role('button', name='刷新节点', exact=True).click()
        case.assertEqual(loaded.value.status, 200, loaded.value.text())

    def matching(inventory, query):
        query = query.strip().lower()
        return [row for row in inventory if not query or any(
            query in str(row[field]).lower() for field in ('remark', 'id', 'port', 'core', 'protocol'))]

    def assert_view(expected, inventory, original=None):
        expect(page.locator('#inbound-search-count')).to_have_text(
            f'显示 {len(expected)} / {len(inventory)} 个节点')
        rendered = page.locator('.el-table__body-wrapper .el-table__row')
        expect(rendered).to_have_count(len(expected))
        case.assertEqual(rendered.evaluate_all(
            'elements => elements.map(row => Number(row.querySelector("td").textContent.trim()))'),
            [row['id'] for row in expected], 'rendered order and IDs must match the filtered inventory')
        identity = page.evaluate('''original => {
            const state = ''' + STATE + ''';
            return {sameSource: !original || state.inbounds === original,
                sameRows: state.filteredInbounds.every(row => state.inbounds.includes(row)),
                blankUsesSource: state.inboundSearch.trim() !== '' || state.filteredInbounds === state.inbounds};
        }''', original)
        case.assertEqual(identity, {'sameSource': True, 'sameRows': True, 'blankUsesSource': True})

    def assert_toolbar(width, height):
        page.set_viewport_size({'width': width, 'height': height})
        toolbar = page.get_by_role('search', name='筛选入站节点', exact=True)
        toolbar.scroll_into_view_if_needed()
        geometry = toolbar.evaluate('''element => {
            const box = node => {const r = node.getBoundingClientRect();
                return {left: r.left, right: r.right, width: r.width, height: r.height,
                    scroll: node.scrollWidth, client: node.clientWidth};};
            return {viewport: innerWidth, toolbar: box(element),
                controls: Array.from(element.children, box)};
        }''')
        case.assertGreater(geometry['toolbar']['width'], 0)
        case.assertGreaterEqual(geometry['toolbar']['left'], -1)
        case.assertLessEqual(geometry['toolbar']['right'], width + 1)
        case.assertLessEqual(geometry['toolbar']['scroll'], geometry['toolbar']['client'] + 1)
        for control in geometry['controls']:
            case.assertGreater(control['width'], 0)
            case.assertGreater(control['height'], 0)
            case.assertGreaterEqual(control['left'], geometry['toolbar']['left'] - 1)
            case.assertLessEqual(control['right'], geometry['toolbar']['right'] + 1)
            case.assertLessEqual(control['scroll'], control['client'] + 1)
        # Keep the already-established navigation modes; no drawer or new state.
        expect(page.locator('.layout-container')).to_have_css(
            'flex-direction', 'column' if width < 768 else 'row')
        if width >= 768:
            expect(page.locator('.aside-menu')).to_have_css('width', '220px')

    create('Cloud ALPHA 北京', 10471, uuids[0])
    selected = create('Literal [a-z].* Beta', 10472, uuids[1])
    create('Gamma / Local', 10473, uuids[2])
    inventory = rows()
    case.assertEqual(len(inventory), 3)
    page.on('request', observe)
    original = None
    try:
        case.open_inbound_view(page)
        search = page.get_by_role('searchbox', name='搜索节点', exact=True)
        clear = page.get_by_role('button', name='清除节点搜索', exact=True)
        expect(search).to_have_attribute('aria-describedby', 'inbound-search-count')
        expect(page.locator('#inbound-search-count')).to_have_attribute('role', 'status')
        expect(page.locator('#inbound-search-count')).to_have_attribute('aria-live', 'polite')
        expect(search).to_have_value('')
        expect(clear).to_be_disabled()
        assert_view(inventory, inventory)
        original = page.evaluate_handle('() => ' + STATE + '.inbounds')
        initial_url, initial_reads = page.url, list(node_reads)
        for query in ('alpha', '  ALPHA  ', '北京', str(selected['id']), '10472', 'XRAY', 'VLESS',
                      '[a-z].*', '.*', '[', '^.*$', '(', 'no matching node',
                      selected['mutation_revision'], uuids[1], 'inherit', '   '):
            search.fill(query)
            expect(search).to_have_value(query)
            assert_view(matching(inventory, query), inventory, original)
        case.assertEqual(node_reads, initial_reads, 'typing must not request a new list or editor')
        case.assertEqual(page.url, initial_url, 'the query must not enter URL/history state')
        case.assertEqual(rows(), inventory, 'typing must not alter the actual temporary DB')

        # Narrow/medium/desktop toolbar containment, with real native controls.
        for width, height in ((390, 844), (768, 1024), (1440, 1000)):
            search.fill('[a-z].*')
            assert_view([selected], inventory, original)
            assert_toolbar(width, height)
            clear.focus()
            clear.press('Enter')
            expect(search).to_have_value('')
            expect(clear).to_be_disabled()
            assert_view(inventory, inventory, original)

        # A filtered row must open its actual authenticated editor, not a
        # positional neighbour from the unfiltered list. Cancel performs no PUT.
        search.fill('beta')
        assert_view([selected], inventory, original)
        filtered_row = page.locator('.el-table__body-wrapper .el-table__row')
        with page.expect_response(lambda response: urlparse(response.url).path ==
                                  f'/api/inbounds/{selected["id"]}/editor') as editor_response:
            filtered_row.get_by_role('button', name='编辑', exact=True).click()
        case.assertEqual(editor_response.value.status, 200)
        case.assertEqual(editor_response.value.json()['id'], selected['id'])
        case.assertEqual(editor_response.value.json()['mutation_revision'], selected['mutation_revision'])
        editor = page.get_by_role('dialog', name='编辑节点', exact=True)
        expect(editor).to_be_visible()
        expect(editor.get_by_placeholder('例如：US-01')).to_have_value(selected['remark'])
        editor.get_by_role('button', name='取消', exact=True).click()
        expect(editor).not_to_be_visible()
        expect(search).to_have_value('beta')
        assert_view([selected], inventory, original)
        filtered_row.get_by_role('button', name='删除', exact=True).click()
        confirmation = page.get_by_role('dialog', name='Warning', exact=True)
        expect(confirmation).to_contain_text(f'{selected["remark"]}:{selected["port"]}')
        confirmation.get_by_role('button', name='Cancel', exact=True).click()
        expect(confirmation).not_to_be_visible()
        case.assertEqual(rows(), inventory, 'opening/cancelling must preserve all records')

        # This is a fourth explicit fixture create, not a search side effect.
        search.fill(' ALPHA ')
        added = create('ALPHA refreshed node', 10474, uuids[3])
        refresh()
        inventory = rows()
        expect(search).to_have_value(' ALPHA ')
        assert_view(matching(inventory, ' ALPHA '), inventory)
        case.assertIn(added['id'], [row['id'] for row in matching(inventory, ' ALPHA ')])
        case.assertFalse(page.evaluate('original => ' + STATE + '.inbounds === original', original))
        original.dispose()
        original = page.evaluate_handle('() => ' + STATE + '.inbounds')
        page.get_by_role('menuitem', name='系统状态', exact=True).click()
        case.open_inbound_view(page)
        expect(search).to_have_value(' ALPHA ')
        assert_view(matching(inventory, ' ALPHA '), inventory)

        # Reload starts fresh memory; there is no query persistence mechanism.
        page.reload()
        expect(page.get_by_role('menuitem', name='入站节点', exact=True)).to_be_visible()
        case.open_inbound_view(page)
        expect(search).to_have_value('')
        assert_view(inventory, inventory)
        case.assertEqual(page.url, initial_url)
        case.assertEqual(page.evaluate('Object.keys(localStorage).length + Object.keys(sessionStorage).length'), 0)
        case.assertEqual(page_writes, [], 'search/clear/refresh/open/cancel never issue API mutations')
        case.assertEqual(fixture_writes, [('POST', '/api/inbounds')] * 4)
        case.assertEqual(case.external, [])
        case.assertEqual(case.errors, [])
        for uuid in uuids:
            case.assertNotIn(uuid, page.content())
            case.assertFalse(any(uuid in message for message in case.console))
        print('Inbound search: real Chromium and authenticated temporary-DB list/editor APIs; '
              '4 explicit synthetic fixture creates saved but not applied; visible-only literal filtering, '
              'original row identity, clear/count, refresh/query retention, reload reset; '
              '390/768/1440px toolbar; 0 page API writes; no mocked GET or core qualification')
    finally:
        if original is not None:
            original.dispose()
        page.remove_listener('request', observe)
