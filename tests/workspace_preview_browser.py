"""Real saved-preview revision races, called only by the opt-in browser gate.

The existing authenticated, disposable server supplies every response. A route
only delays the real preview GET while an independent API writer commits B;
status, headers and body are then delivered unchanged. No controller hooks,
synthetic successes, fixed sleeps, navigation holds or relaxed CSP are needed.
"""
import json
import sys


def assert_workspace_preview_revision_races(test, page, context, base, work, dialogs, expect):
    timeout = 10000
    endpoint = base + '/api/routing/mihomo'
    preview_url = endpoint + '/preview'
    headers = {'Origin': base, 'X-VUI-Request': '1'}
    routing_file = work / 'data' / 'mihomo-routing.json'
    requests = []

    def cleanup(action, label):
        primary = sys.exc_info()[1]
        try:
            action()
        except Exception as error:
            if primary is None:
                raise
            primary.add_note(label + ': ' + str(error))

    def snapshot():
        response = context.request.get(endpoint + '/snapshot', timeout=timeout)
        try:
            test.assertEqual(response.status, 200, response.text())
            value = response.json()
            test.assertRegex(value['revision'], r'^[0-9a-f]{64}$')
            return value
        finally:
            cleanup(response.dispose, 'Snapshot response cleanup')

    def write(settings, revision):
        response = context.request.put(endpoint, headers={**headers, 'If-Match': '"' + revision + '"'},
                                       data=settings, timeout=timeout)
        try:
            test.assertEqual(response.status, 200, response.text())
            value = snapshot()
            test.assertEqual(response.json(), value['settings'])
            test.assertEqual(response.headers.get('etag'), '"' + value['revision'] + '"')
            return value
        finally:
            cleanup(response.dispose, 'Writer response cleanup')

    def record(request):
        if request.url in (endpoint, endpoint + '/snapshot', preview_url):
            requests.append((request.method, request.url.removeprefix(endpoint),
                             request.headers.get('if-match')))

    def form():
        return {
            'mode': page.locator('#mode').input_value(),
            'direct': page.locator('#direct').input_value(),
            'proxy': page.locator('#proxy').input_value(),
            'cgnat': page.locator('#cgnat').is_checked(),
            'presets': [(item.get_attribute('data-preset'), item.is_checked())
                        for item in page.locator('[data-preset]').all()],
            'zones': [(item.locator('[data-suffix]').input_value(), item.locator('[data-dns]').input_value())
                      for item in page.locator('.zone').all()],
        }

    def preview():
        return {name: page.locator('#' + name).text_content()
                for name in ('preview-label', 'rule-count', 'rule-preview', 'warnings')}

    def settled():
        expect(page.locator('#reload')).to_be_enabled(timeout=timeout)

    def click_response(button, method, url):
        with page.expect_response(lambda response: response.url == url and response.request.method == method,
                                  timeout=timeout) as pending:
            page.locator('#' + button).click(timeout=timeout)
        return pending.value

    def coherent_reload(value):
        response = click_response('reload', 'GET', endpoint + '/snapshot')
        test.assertEqual(response.status, 200)
        settled()
        expect(page.locator('#proxy')).to_have_value('\n'.join(value['settings']['proxy_domains']))
        expect(page.locator('#save')).to_be_disabled()
        expect(page.locator('#preview-label')).to_have_text('已保存规则预览')
        expect(page.locator('#notice')).to_contain_text('已读取保存版本')
        for domain in value['settings']['proxy_domains']:
            expect(page.locator('#rule-preview')).to_contain_text(domain)
        test.assertEqual(snapshot(), value)

    original = snapshot()
    test.assertEqual(original['settings']['proxy_domains'], ['draft.example.test'])
    settled()
    page.on('request', record)
    try:
        for kind, newer_input in (('reload', False), ('save', False), ('save', True)):
            label = kind + ('-newer-draft' if newer_input else '-unchanged-input')
            test.assertEqual(snapshot(), original)
            initial_form = form()
            if kind == 'reload':
                page.locator('#proxy').fill('discard-approved-preview.example.test', timeout=timeout)
                value_a = write({**original['settings'], 'proxy_domains': ['snapshot-a.example.test']},
                                original['revision'])
            else:
                page.locator('#proxy').fill('confirmed-save-a.example.test', timeout=timeout)
                value_a = None
            before_form, old_preview = form(), preview()
            before_dialogs, start = len(dialogs), len(requests)
            expected = []
            state = {'calls': 0, 'errors': [], 'timeline': []}

            def mismatch_preview(route):
                actual = None
                try:
                    state['calls'] += 1
                    test.assertEqual(state['calls'], 1, 'no automatic preview retry')
                    test.assertEqual(route.request.method, 'GET')
                    # This request is issued only after the UI has consumed
                    # snapshot A or the genuine, confirmed PUT A response.
                    a = snapshot()
                    test.assertEqual(a['settings']['proxy_domains'],
                                     ['snapshot-a.example.test' if kind == 'reload'
                                      else 'confirmed-save-a.example.test'])
                    if value_a is not None:
                        test.assertEqual(a, value_a)
                    state['a'] = a
                    state['timeline'].append('UI received snapshot/confirmed save A')
                    b = write({**a['settings'], 'proxy_domains': ['external-winner-b.example.test']},
                              a['revision'])
                    test.assertNotEqual(a['revision'], b['revision'])
                    state['b'], state['bytes'] = b, routing_file.read_bytes()
                    state['timeline'].append('authenticated API writer committed B with If-Match A')
                    actual = route.fetch(timeout=timeout)
                    test.assertEqual(actual.status, 200, actual.text())
                    test.assertEqual(actual.json()['revision'], b['revision'])
                    test.assertIn('external-winner-b.example.test', json.dumps(actual.json()['sections']))
                    state['timeline'].append('genuine preview GET returned B')
                    if newer_input:
                        expect(page.locator('#proxy')).to_be_editable(timeout=timeout)
                        page.locator('#proxy').fill('newer-unsaved-preview.example.test', timeout=timeout)
                        state['timeline'].append('normal input created newer draft before preview delivery')
                    route.fulfill(response=actual)
                except Exception as error:
                    state['errors'].append(error)
                    try:
                        route.abort()
                    except Exception as cleanup_error:
                        error.add_note('Preview route cleanup: ' + str(cleanup_error))
                finally:
                    if actual is not None:
                        try:
                            actual.dispose()
                        except Exception as error:
                            state['errors'].append(error)

            page.route(preview_url, mismatch_preview)
            try:
                response = click_response(kind, 'GET' if kind == 'reload' else 'PUT',
                                          endpoint + '/snapshot' if kind == 'reload' else endpoint)
                settled()
                if state['errors']:
                    raise state['errors'][0]
                test.assertEqual(response.status, 200)
                test.assertEqual(state['calls'], 1)
                if kind == 'reload':
                    test.assertEqual(response.json(), state['a'])
                    retained_revision = original['revision']
                    expected.extend([('GET', '/snapshot', None), ('GET', '/preview', None)])
                    expect(page.locator('#notice')).to_contain_text('版本不一致或无法确认')
                    expect(page.locator('#notice')).to_contain_text('已保留')
                    test.assertEqual(form(), before_form, 'confirmed discard must not partially adopt A')
                    test.assertEqual(len(dialogs) - before_dialogs, 1)
                else:
                    test.assertEqual(response.json(), state['a']['settings'])
                    retained_revision = state['a']['revision']
                    test.assertEqual(response.headers.get('etag'), '"' + retained_revision + '"')
                    expected.extend([('PUT', '', '"' + original['revision'] + '"'), ('GET', '/preview', None)])
                    expect(page.locator('#notice')).to_contain_text('设置已保存，但预览版本已变化或无法确认')
                    expect(page.locator('#notice')).to_contain_text('未显示该预览')
                    expected_form = {**before_form, 'proxy': 'newer-unsaved-preview.example.test'} if newer_input else before_form
                    test.assertEqual(form(), expected_form)
                    test.assertEqual(len(dialogs), before_dialogs)
                expect(page.locator('#notice')).to_have_class('error')
                expect(page.locator('#notice')).to_contain_text('重新载入或刷新页面')
                # Saved A must not label old rules as its saved preview.
                # A newer draft keeps its own existing edit-warning label.
                expected_preview = old_preview
                if kind == 'save':
                    expected_preview = {**old_preview, 'preview-label': (
                        '表单已修改，预览需重新生成' if newer_input
                        else '设置已保存 · 预览未更新，请重新载入')}
                test.assertEqual(preview(), expected_preview)
                test.assertNotIn('external-winner-b.example.test', page.locator('#rule-preview').text_content())
                expect(page.locator('#save-state')).to_contain_text(
                    '未保存草稿' if kind == 'reload' or newer_input else '已与保存版本一致')
                test.assertEqual(requests[start:], expected, 'mismatch never retries or writes automatically')
                test.assertEqual(snapshot(), state['b'])
                test.assertEqual(routing_file.read_bytes(), state['bytes'])
            finally:
                cleanup(lambda: page.unroute(preview_url, mismatch_preview), 'Preview unroute cleanup')

            # Baseline stays old on failed reload, but a confirmed save must
            # not roll back its newly acknowledged baseline or revision.
            baseline_proxy = initial_form['proxy'] if kind == 'reload' else 'confirmed-save-a.example.test'
            page.locator('#proxy').fill(baseline_proxy, timeout=timeout)
            expect(page.locator('#save')).to_be_disabled()
            expect(page.locator('#save-state')).to_contain_text('已与保存版本一致')
            page.locator('#proxy').fill('explicit-stale-save.example.test', timeout=timeout)
            expect(page.locator('#save')).to_be_enabled()
            conflict = click_response('save', 'PUT', endpoint)
            settled()
            test.assertEqual(conflict.status, 409)
            test.assertEqual(conflict.json(), {'detail': 'Saved routing changed in another session; reload before saving'})
            expected.append(('PUT', '', '"' + retained_revision + '"'))
            expect(page.locator('#notice')).to_contain_text('保存版本冲突')
            expect(page.locator('#proxy')).to_have_value('explicit-stale-save.example.test')
            expect(page.locator('#save')).to_be_enabled()
            test.assertEqual(page.locator('#rule-preview').text_content(), old_preview['rule-preview'])
            test.assertEqual(requests[start:], expected, '409 never retries or reads a replacement preview')
            test.assertEqual(snapshot(), state['b'])
            test.assertEqual(routing_file.read_bytes(), state['bytes'], '409 preserves the actual winner bytes')
            state['timeline'].append('explicit stale save sent retained If-Match and received real 409')

            coherent_reload(state['b'])
            expected.extend([('GET', '/snapshot', None), ('GET', '/preview', None)])
            test.assertEqual(requests[start:], expected)
            page.locator('#proxy').fill('explicit-recovery.example.test', timeout=timeout)
            recovered = click_response('save', 'PUT', endpoint)
            settled()
            test.assertEqual(recovered.status, 200)
            expect(page.locator('#notice')).to_contain_text('设置已保存。')
            expect(page.locator('#preview-label')).to_have_text('已保存规则预览')
            expect(page.locator('#rule-preview')).to_contain_text('explicit-recovery.example.test')
            expect(page.locator('#rule-preview')).not_to_contain_text('external-winner-b.example.test')
            expect(page.locator('#save')).to_be_disabled()
            recovery = snapshot()
            test.assertEqual(recovery['settings']['proxy_domains'], ['explicit-recovery.example.test'])
            test.assertEqual(recovered.headers.get('etag'), '"' + recovery['revision'] + '"')
            expected.extend([('PUT', '', '"' + state['b']['revision'] + '"'), ('GET', '/preview', None)])
            test.assertEqual(requests[start:], expected)
            state['timeline'].append('explicit reload B then normal save recovered coherently')

            # Restore only this disposable routing fixture, through its
            # real optimistic API, before inherited subscription scenarios.
            restored = write(original['settings'], recovery['revision'])
            test.assertEqual(restored, original)
            coherent_reload(restored)
            expected.extend([('GET', '/snapshot', None), ('GET', '/preview', None)])
            test.assertEqual(requests[start:], expected)
            test.assertEqual(form(), initial_form)
            print('Workspace preview revision browser OK: ' + json.dumps({
                'scenario': label, 'a_revision': state['a']['revision'],
                'b_revision': state['b']['revision'], 'retained_revision': retained_revision,
                'recovered_revision': recovery['revision'], 'ui_requests': requests[start:],
                'timeline': state['timeline'], 'fixture_restored': True,
            }, sort_keys=True), flush=True)
    finally:
        cleanup(lambda: page.remove_listener('request', record), 'Request listener cleanup')
