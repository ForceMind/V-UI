"""Opt-in Chromium source for initial-read retry in the disposable auth fixture.

Only the named snapshot/preview GETs receive synthetic transport aborts. They
are not backend outages. Every success is the genuine local server response;
one recovery response is held while native disabled/loading state is checked.
No controller access, forced clicks, fake success, clock changes or sleeps.
The caller retains its original page; these read-only pages cannot save a draft
or change nodes, subscriptions, credentials, or the fixture's routing file.
"""
from collections import Counter
import json
import sys
from test_workspace_initial_retry import assert_workspace_initial_retry_controls


def assert_workspace_initial_read_retry(test, context, base, work, expect):
    # Reuse the strict controller preflight, never a second weaker runner.
    assert_workspace_initial_retry_controls(test)
    timeout = 10000
    routing = '/api/routing/mihomo'
    prefix = ['/api/auth/me', routing + '/catalog', routing + '/snapshot']
    node_reads = ['/api/inbounds', '/api/subscription/mihomo-warnings', '/api/subscriptions']
    routing_file = work / 'data' / 'mihomo-routing.json'

    def cleanup(actions):
        """Run every cleanup, preserving the first failure and its traceback."""
        primary, errors = sys.exc_info()[1], []
        for label, action in actions:
            try:
                action()
            except Exception as error:
                error.add_note(label)
                errors.append(error)
        if primary is not None:
            for error in errors:
                primary.add_note('Cleanup: ' + str(error) + ' ' + str(error.__notes__))
        elif errors:
            for error in errors[1:]:
                errors[0].add_note('Additional cleanup: ' + str(error))
            raise errors[0]

    def read(path):
        response = context.request.get(base + path, timeout=timeout)
        try:
            test.assertEqual(response.status, 200, response.text())
            return response.json()
        finally:
            cleanup([('Dispose fixture read', response.dispose)])

    original = read(routing + '/snapshot')
    test.assertRegex(original['revision'], r'^[0-9a-f]{64}$')
    catalog = read(routing + '/catalog')
    preset_ids = [preset['id'] for category in catalog['categories']
                  for preset in catalog['presets'] if preset['category'] == category['id']]
    test.assertEqual(len(preset_ids), 40)
    test.assertEqual(len(set(preset_ids)), 40)
    original_bytes = routing_file.read_bytes() if routing_file.exists() else None

    for endpoint, abort_count in (('snapshot', 2), ('preview', 1)):
        with test.subTest(initial_read=endpoint, synthetic_aborts=abort_count):
            page = context.new_page()
            target = base + routing + '/' + endpoint
            requests, finished, failed, console_errors, page_errors = [], [], [], [], []
            aborted, route_errors, external, writes, dialogs = [], [], [], [], []
            responses, phase_calls = {}, Counter()
            state = {'phase': 0, 'recoveries': 0, 'post_pair_reads': []}
            retry = page.get_by_role('button', name='重试初次载入', exact=True)

            def record(request):
                requests.append(request)

            def completed(request):
                finished.append(request)

            def failed_request(request):
                failed.append((request, request.failure))

            def received(response):
                responses[response.request] = response

            def console(message):
                if message.type == 'error':
                    console_errors.append((message.text, message.location.get('url')))

            def page_error(error):
                page_errors.append(str(error))

            def dialog(value):
                dialogs.append((value.type, value.message))
                value.dismiss()

            def api_requests():
                return [(request.method, request.url.removeprefix(base)) for request in requests
                        if request.url.startswith(base + '/api/')]

            def no_duplicate_catalog():
                test.assertEqual(page.locator('[data-preset]').evaluate_all(
                    '(inputs) => inputs.map(input => input.dataset.preset)'), preset_ids)
                test.assertEqual(page.locator('#presets > fieldset').count(), len(catalog['categories']))

            def uninitialized():
                expect(page.locator('#workspace')).to_be_hidden(timeout=timeout)
                for identifier in ('reload', 'preview', 'save', 'add-zone', 'refresh-grants', 'create-grant'):
                    expect(page.locator('#' + identifier)).to_be_hidden()
                expect(page.locator('#proxy')).to_have_value('')
                expect(page.locator('#save-state')).to_have_text('尚未加载')
                expect(page.locator('#preview-label')).to_have_text('尚未生成预览')
                expect(page.locator('#issued')).to_be_hidden()
                expect(page.locator('#issued-urls')).to_have_value('')
                test.assertEqual(page.locator('[data-node]').count(), 0)
                no_duplicate_catalog()

            def guard(route):
                request = route.request
                if not request.url.startswith(base + '/'):
                    external.append(request.url)
                    route.abort('blockedbyclient')
                elif request.method != 'GET':
                    writes.append((request.method, request.url))
                    route.abort('blockedbyclient')
                else:
                    try:
                        path = request.url.removeprefix(base)
                        if path in node_reads:
                            # Pair adoption must precede even the node/grant GETs.
                            expect(page.locator('#initial-retry')).to_be_hidden()
                            expect(page.locator('#workspace')).to_be_visible()
                            state['post_pair_reads'].append(path)
                        route.continue_()
                    except Exception as error:
                        route_errors.append(error)
                        cleanup([('Abort failed read guard', lambda: route.abort('failed'))])

            def fault_or_hold(route):
                actual = None
                try:
                    test.assertEqual(route.request.method, 'GET')
                    phase_calls[state['phase']] += 1
                    test.assertEqual(phase_calls[state['phase']], 1, 'no automatic or duplicate initial read')
                    if state['phase'] < abort_count:
                        aborted.append(route.request)
                        route.abort('failed')  # Explicit synthetic transport fault, not a real outage.
                        return
                    state['recoveries'] += 1
                    test.assertEqual(state['recoveries'], 1)
                    before = api_requests()
                    expect(retry).to_be_visible()
                    expect(retry).to_be_disabled()
                    expect(page.locator('#notice')).to_have_text('正在初次载入已保存的设置…')
                    uninitialized()
                    # Fetch and deliver the unchanged genuine response. A held
                    # preview also proves that snapshot alone cannot expose UI.
                    actual = route.fetch(timeout=timeout)
                    test.assertEqual(actual.status, 200, actual.text())
                    test.assertEqual(actual.json()['revision'], original['revision'])
                    expect(retry).to_be_disabled()
                    test.assertEqual(api_requests(), before, 'busy initialization has no duplicate GET')
                    route.fulfill(response=actual)
                except Exception as error:
                    route_errors.append(error)
                    cleanup([('Abort failed recovery route', lambda: route.abort('failed'))])
                finally:
                    if actual is not None:
                        try:
                            actual.dispose()
                        except Exception as error:
                            route_errors.append(error)

            listeners = [('request', record), ('requestfinished', completed), ('requestfailed', failed_request),
                         ('response', received), ('console', console), ('pageerror', page_error), ('dialog', dialog)]
            try:
                for name, listener in listeners:
                    page.on(name, listener)
                page.route('**/*', guard)
                page.route(target, fault_or_hold)
                failed_sequence = prefix + ([routing + '/preview'] if endpoint == 'preview' else [])
                expected = []
                for attempt in range(abort_count):
                    state['phase'] = attempt
                    with page.expect_event('requestfailed', predicate=lambda request: request.url == target,
                                           timeout=timeout) as pending:
                        if attempt == 0:
                            page.goto(base + '/workspace', timeout=timeout)
                        else:
                            expect(retry).to_be_enabled()
                            retry.click(timeout=timeout)  # Normal native enabled-button activation only.
                    test.assertIs(pending.value, aborted[-1])
                    test.assertEqual(pending.value.failure, 'net::ERR_FAILED')
                    expect(retry).to_be_visible()
                    expect(retry).to_be_enabled()
                    expect(retry).to_have_attribute('id', 'initial-retry')
                    expect(retry).to_have_attribute('type', 'button')
                    expect(retry).to_have_attribute('aria-describedby', 'notice')
                    expect(page.locator('#notice')).to_have_class('error')
                    uninitialized()
                    expected.extend(('GET', path) for path in failed_sequence)
                    test.assertEqual(api_requests(), expected, 'failure awaits an explicit retry')

                state['phase'] = abort_count
                expect(retry).to_be_enabled()
                retry.click(timeout=timeout)
                expect(page.locator('#workspace')).to_be_visible(timeout=timeout)
                expect(page.locator('#refresh-grants')).to_be_enabled(timeout=timeout)
                expect(page.locator('#create-grant')).to_be_enabled(timeout=timeout)
                expect(page.locator('#initial-retry')).to_be_hidden()
                expect(page.locator('#notice')).to_have_text('节点和订阅已刷新，请重新选择节点。')
                expect(page.locator('#save')).to_be_disabled()
                expect(page.locator('#save-state')).to_contain_text('已与保存版本一致')
                expect(page.locator('#preview-label')).to_have_text('已保存规则预览')
                no_duplicate_catalog()
                expected.extend(('GET', path) for path in prefix + [routing + '/preview'])
                observed = api_requests()
                test.assertEqual(observed[:len(expected)], expected)
                test.assertEqual(Counter(observed[len(expected):]), Counter(('GET', path) for path in node_reads))
                test.assertEqual(observed[-1], ('GET', '/api/subscriptions'))
                test.assertEqual(Counter(state['post_pair_reads']), Counter(node_reads))
                test.assertEqual(phase_calls, Counter({attempt: 1 for attempt in range(abort_count + 1)}))
                test.assertEqual(state['recoveries'], 1)

                # Observe complete exact Request objects, not response headers
                # alone; allow only the deliberately aborted objects to fail.
                required = set(requests)
                if not required <= set(finished) | {request for request, _ in failed}:
                    page.wait_for_event('requestfinished', timeout=timeout, predicate=lambda request:
                                        required <= set(finished) | {item for item, _ in failed} | {request})
                test.assertEqual(set(requests), set(finished) | {request for request, _ in failed})
                test.assertEqual(len(set(requests)), len(requests))
                test.assertEqual(len(finished) + len(failed), len(requests))
                test.assertEqual(api_requests(), observed)
                test.assertEqual(failed, [(request, 'net::ERR_FAILED') for request in aborted])
                test.assertEqual(len(aborted), abort_count)
                test.assertEqual(console_errors, [('Failed to load resource: net::ERR_FAILED', target)] * abort_count)
                test.assertEqual(page_errors, [])
                test.assertEqual(route_errors, [])
                test.assertEqual(external, [])
                test.assertEqual(writes, [])
                test.assertEqual(dialogs, [])
                for request in finished:
                    test.assertEqual(responses[request].status, 200, request.url)
                successful = {request.url: responses[request] for request in finished}
                saved = successful[base + routing + '/snapshot'].json()
                plan = successful[base + routing + '/preview'].json()
                test.assertEqual(saved, original)
                test.assertEqual(plan['revision'], saved['revision'])
                settings = saved['settings']
                for identifier, value in (('mode', settings['mode']), ('direct', '\n'.join(settings['direct_domains'])),
                                          ('proxy', '\n'.join(settings['proxy_domains']))):
                    expect(page.locator('#' + identifier)).to_have_value(value)
                test.assertEqual(page.locator('#cgnat').is_checked(), settings['bypass_cgnat'])
                test.assertEqual(page.locator('[data-preset]').evaluate_all(
                    '(inputs) => Object.fromEntries(inputs.map(input => [input.dataset.preset, input.checked]))'),
                    {identifier: bool(settings['presets'].get(identifier)) for identifier in preset_ids})
                test.assertEqual(page.locator('#rule-preview').text_content(), '\n\n'.join(
                    '# ' + section['comment'] + '\n' + '\n'.join(section['rules']) for section in plan['sections']))
                test.assertEqual(page.evaluate('Object.keys(localStorage).length + Object.keys(sessionStorage).length'), 0)
                test.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth'))
                test.assertEqual(read(routing + '/snapshot'), original)
                test.assertEqual(routing_file.read_bytes() if routing_file.exists() else None, original_bytes)
                report = {
                    'fault': 'synthetic route.abort(failed)', 'endpoint': endpoint, 'aborts': abort_count,
                    'recovery': 'unchanged genuine authenticated API responses', 'revision': original['revision'],
                    'native_disabled_during_recovery': True, 'catalog_presets': len(preset_ids),
                    'ui_requests': observed, 'fixture_unchanged': True,
                }
            except Exception as error:
                if route_errors and route_errors[0] is not error:
                    route_errors[0].add_note('Subsequent browser observation: ' + str(error))
                    raise route_errors[0] from error
                raise
            finally:
                cleanup([('Remove fault route', lambda: page.unroute(target, fault_or_hold)),
                         ('Remove read-only/external guard', lambda: page.unroute('**/*', guard)),
                         *[('Remove ' + name + ' listener', lambda name=name, listener=listener:
                            page.remove_listener(name, listener)) for name, listener in listeners],
                         ('Close isolated retry page', page.close)])
            print('Workspace initial-read retry browser OK: ' + json.dumps(report, sort_keys=True), flush=True)
