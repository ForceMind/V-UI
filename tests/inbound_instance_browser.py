"""Actual authenticated node APIs and Chromium, with synthetic local nodes only.

The existing opt-in proxy browser fixture has no core binaries: successful DB
changes honestly return saved:true/applied:false. This is not runtime or device
qualification. No request/reply is mocked in this workflow.
"""
from urllib.parse import urlparse


def assert_inbound_instance_conflicts(case):
    from playwright.sync_api import expect

    page, context, base = case.page, case.context, case.base
    page.set_viewport_size({'width': 1440, 'height': 1000})
    headers = {'Origin': base, 'X-VUI-Request': '1'}
    changed = 'Inbound instance changed; refresh nodes and reopen or confirm again'
    old_uuid = '66666666-6666-4666-8666-666666666666'
    new_uuid = '77777777-7777-4777-8777-777777777777'

    def saved_not_applied(response):
        case.assertEqual(response.status, 409, response.text())
        case.assertEqual(response.json()['detail'], {
            'saved': True, 'applied': False,
            'message': 'Pinned core binary is missing or not executable'})

    def rows():
        response = context.request.get(base + '/api/inbounds')
        case.assertEqual(response.status, 200, response.text())
        case.assertNotIn('instance_id', response.text())
        case.assertNotIn(old_uuid, response.text())
        case.assertNotIn(new_uuid, response.text())
        return response.json()

    def create(label, port, uuid):
        saved_not_applied(context.request.post(base + '/api/inbounds', headers=headers, data={
            'core': 'xray', 'protocol': 'vless', 'remark': label, 'port': port,
            'enable': False, 'settings': {'users': [{'id': uuid}]},
            'stream_settings': {}}))
        found = [row for row in rows() if row['remark'] == label]
        case.assertEqual(len(found), 1)
        case.assertRegex(found[0]['mutation_revision'], r'\A[0-9a-f]{64}\Z')
        return found[0]

    def legacy_replace(old, label, port):
        # A separate authenticated caller intentionally exercises documented
        # legacy current-ID semantics. The page itself must never use DELETE.
        saved_not_applied(context.request.delete(
            base + f'/api/inbounds/{old["id"]}', headers=headers))
        replacement = create(label, port, new_uuid)
        case.assertEqual(replacement['id'], old['id'], 'natural SQLite ID reuse required')
        case.assertNotEqual(replacement['mutation_revision'], old['mutation_revision'])
        return replacement

    def refresh():
        with page.expect_response(lambda response: urlparse(response.url).path == '/api/inbounds'
                                  and response.request.method == 'GET') as loaded:
            page.get_by_role('button', name='刷新节点', exact=True).click()
        case.assertEqual(loaded.value.status, 200)

    writes = []
    def record(request):
        if urlparse(request.url).path.startswith('/api/inbounds/') and request.method in ('PUT', 'POST', 'DELETE'):
            writes.append(request)
    page.on('request', record)
    try:
        original = create('identity-original-edit', 10463, old_uuid)
        case.open_inbound_view(page)
        old_row = page.get_by_role('row').filter(has_text='identity-original-edit')
        old_row.get_by_role('button', name='编辑', exact=True).click()
        editor = page.get_by_role('dialog', name='编辑节点', exact=True)
        expect(editor).to_be_visible()
        draft = editor.get_by_placeholder('例如：US-01')
        draft.fill('preserve-original-edit-intent')
        replacement = legacy_replace(original, 'identity-replacement-edit', 10464)
        expect(old_row).to_be_visible()
        with page.expect_response(lambda response: urlparse(response.url).path == f'/api/inbounds/{original["id"]}'
                                  and response.request.method == 'PUT') as rejected:
            editor.get_by_role('button', name='保存并应用', exact=True).click()
        case.assertEqual(rejected.value.status, 409)
        case.assertEqual(rejected.value.json(), {'detail': changed})
        expect(editor).to_be_visible()
        expect(draft).to_have_value('preserve-original-edit-intent')
        expect(page.locator('.el-message--error').filter(has_text='Failed to update inbound: ' + changed)).to_be_visible()
        case.assertEqual(rows(), [replacement], 'rejected stale PUT must not alter replacement')
        case.assertEqual(len(writes), 1, 'identity conflict must not retry automatically')
        case.assertEqual(writes[0].post_data_json['expected_instance_revision'], original['mutation_revision'])
        editor.get_by_role('button', name='取消', exact=True).click()
        expect(editor).not_to_be_visible()

        # The old displayed row must not open a replacement editor by silently
        # adopting its GET revision. No refresh has happened yet.
        with page.expect_response(lambda response: urlparse(response.url).path == f'/api/inbounds/{original["id"]}/editor'
                                  and response.request.method == 'GET') as stale_editor:
            old_row.get_by_role('button', name='编辑', exact=True).click()
        case.assertEqual(stale_editor.value.status, 200)
        case.assertEqual(stale_editor.value.json()['mutation_revision'], replacement['mutation_revision'])
        expect(page.locator('.el-message--error').filter(has_text='Failed to load inbound editor: ' + changed)).to_be_visible()
        expect(editor).not_to_be_visible()
        case.assertEqual(len(writes), 1)
        refresh()
        new_row = page.get_by_role('row').filter(has_text='identity-replacement-edit')
        new_row.get_by_role('button', name='编辑', exact=True).click()
        expect(editor).to_be_visible()
        expect(draft).to_have_value('identity-replacement-edit')
        editor.get_by_role('button', name='取消', exact=True).click()
        expect(editor).not_to_be_visible()

        # The first delete confirmation names the displayed instance. A second
        # caller replaces it while that actual Element Plus dialog is open.
        new_row.get_by_role('button', name='删除', exact=True).click()
        confirmation = page.get_by_role('dialog', name='Warning', exact=True)
        expect(confirmation).to_contain_text('identity-replacement-edit:10464')
        next_row = legacy_replace(replacement, 'identity-replacement-delete', 10465)
        with page.expect_response(lambda response: urlparse(response.url).path == f'/api/inbounds/{replacement["id"]}/delete'
                                  and response.request.method == 'POST') as rejected_delete:
            confirmation.get_by_role('button', name='OK', exact=True).click()
        case.assertEqual(rejected_delete.value.status, 409)
        case.assertEqual(rejected_delete.value.json(), {'detail': changed})
        expect(confirmation).not_to_be_visible()
        expect(page.locator('.el-message--error').filter(has_text='Failed to delete inbound: ' + changed)).to_be_visible()
        case.assertEqual(rows(), [next_row])
        case.assertEqual(len(writes), 2)
        case.assertEqual(writes[-1].post_data_json, {
            'expected_instance_revision': replacement['mutation_revision']})
        case.assertFalse(any(request.method == 'DELETE' for request in writes))

        # Only explicit refresh and a new confirmation may delete the new row.
        refresh()
        page.get_by_role('row').filter(has_text='identity-replacement-delete').get_by_role(
            'button', name='删除', exact=True).click()
        expect(confirmation).to_contain_text('identity-replacement-delete:10465')
        with page.expect_response(lambda response: urlparse(response.url).path == f'/api/inbounds/{next_row["id"]}/delete'
                                  and response.request.method == 'POST') as accepted_delete:
            confirmation.get_by_role('button', name='OK', exact=True).click()
        saved_not_applied(accepted_delete.value)
        expect(page.locator('.el-message--warning')).to_contain_text('节点删除已保存')
        expect(page.get_by_role('row').filter(has_text='identity-replacement-delete')).to_have_count(0)
        case.assertEqual(rows(), [])
        case.assertEqual(len(writes), 3)
        case.assertEqual(writes[-1].post_data_json, {
            'expected_instance_revision': next_row['mutation_revision']})
        case.assertTrue(all(request.method != 'DELETE' for request in writes))
        case.assert_private()
        print('Inbound instance browser: actual stale PUT and guarded POST delete rejected, '
              'draft retained, stale editor GET rejected, explicit refresh/reconfirm accepted; '
              'missing-core saved/not-applied boundary retained')
    finally:
        page.remove_listener('request', record)
