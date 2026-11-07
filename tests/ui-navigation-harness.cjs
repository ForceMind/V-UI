'use strict';
// Real controller sources, deterministic DOM/history/HTTP substitutes. No browser.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const test = require('node:test');
const source = name => fs.readFileSync('web/js/' + name, 'utf8');
const flush = async () => { for (let i = 0; i < 100; i++) await Promise.resolve(); };
function environment(initial = '/ui/#inbounds') {
    let url = new URL(initial, 'https://panel.example.test'), position = 0, setup;
    const events = new Map(), listeners = new Map(), replacements = [], assignments = [], watches = [];
    const entries = [{url: url.href, state: null}], warnings = [];
    const emit = (name, event = {}) => { for (const callback of events.get(name) || []) callback(event); };
    let scanning = false;
    const scan = () => {
        if (scanning) return;
        scanning = true;
        try {
            for (let i = 0; i < 8; i++) {
                let changed = false;
                for (const watch of watches) {
                    const value = typeof watch.target === 'function' ? watch.target() : watch.target.value;
                    const next = JSON.stringify(value);
                    if (next !== watch.previous) { watch.previous = next; watch.callback(value); changed = true; }
                }
                if (!changed) break;
            }
        } finally { scanning = false; }
    };
    const location = {
        get pathname() { return url.pathname; }, get hash() { return url.hash; }, get search() { return url.search; },
        get origin() { return url.origin; }, assign(value) { assignments.push(value); }, replace(value) { replacements.push(value); }, reload() {}
    };
    const history = {
        get state() { return entries[position].state; },
        replaceState(state, _, value) { url = new URL(value, url); entries[position] = {state, url: url.href}; },
        pushState(state, _, value) { url = new URL(value, url); entries.splice(position + 1); entries.push({state, url: url.href}); position++; },
        go(delta) { queueMicrotask(() => {
            const next = position + delta;
            if (next < 0 || next >= entries.length) return;
            const oldHash = url.hash; position = next; url = new URL(entries[position].url);
            emit('popstate', {state: history.state});
            if (oldHash !== url.hash) emit('hashchange');
        }); }, back() { history.go(-1); }, forward() { history.go(1); }
    };
    const app = {component() {}, use() {}, mount() {}};
    const sandbox = {URL, URLSearchParams, Event, AbortController, TextEncoder, TextDecoder, console, setTimeout, clearTimeout,
        location, history, scrollY: 0, scrollTo(_x, y) { sandbox.scrollY = y; },
        addEventListener(name, fn) { events.set(name, [...(events.get(name) || []), fn]); }, dispatchEvent(event) { emit(event.type, event); },
        document: {getElementById() { return null; }, addEventListener(name, fn) { listeners.set(name, fn); }},
        confirm() { return true; },
        Vue: {createApp(options) { setup = options.setup; return app; },
            ref(value) { return Object.defineProperty({}, 'value', {get() { return value; }, set(next) { value = next; scan(); }}); },
            reactive(value) { return new Proxy(value, {set(object, key, next) { object[key] = next; scan(); return true; }}); },
            computed(fn) { return {get value() { return fn(); }}; }, nextTick(fn) { return Promise.resolve().then(fn); },
            watch(target, callback) { watches.push({target, callback, previous: JSON.stringify(typeof target === 'function' ? target() : target.value)}); }, onMounted() {}},
        ElementPlus: {ElMessage: {success() {}, warning(message) { warnings.push(message); }, error(message) { warnings.push(message); }}, ElMessageBox: {}},
        ElementPlusIconsVue: {},
        axios: {defaults: {headers: {common: {}}}, interceptors: {response: {use() {}}},
            async get() { return {data: []}; }, async post() { return {data: {}}; }, async put() { return {data: {}}; }}
    };
    const context = vm.createContext(sandbox);
    vm.runInContext(source('navigation.js'), context);
    return {sandbox, context, history, entries, replacements, assignments, events, emit, warnings,
        get position() { return position; }, app() { vm.runInContext(source('app.js'), context); return setup(); }};
}
test('white-list routes, legacy aliases and login return reject arbitrary URLs', () => {
    const {sandbox: {VUI_NAV: nav}} = environment();
    for (const id of ['dashboard', 'inbounds', 'cores', 'diagnostics', 'firewall', 'security', 'legacy'])
        assert.equal(nav.route('/ui/', '#' + id).id, id);
    for (const id of ['routing', 'subscriptions']) {
        assert.equal(nav.route('/ui/', '#' + id).url, '/workspace#' + id);
        assert.equal(nav.loginURL('/workspace', '#' + id), '/login?return=' + id);
        assert.equal(nav.resumeTarget('?return=' + id), '/workspace#' + id);
    }
    assert.equal(nav.route('/ui/', '#settings').url, '/account');
    assert.equal(nav.route('/ui/', '#site').id, 'legacy');
    for (const value of ['https://evil.test', '//evil.test', '/proxy', 'javascript:alert(1)', '__proto__', 'constructor', '', 'proxy%00'])
        assert.equal(nav.resumeTarget('?return=' + encodeURIComponent(value)), '/ui/#dashboard');
    assert.equal(nav.route('/ui/', '#unknown?token=fake').id, 'dashboard');
});
test('real app route selection, Back/Forward and same-tab click retain non-secret list context', async () => {
    const env = environment('/ui/#dashboard'), state = env.app();
    state.handleSelect('inbounds'); await flush();
    state.inboundSearch.value = 'port 443'; env.sandbox.scrollY = 180;
    state.handleSelect('cores'); await flush();
    assert.equal(state.currentView.value, 'cores');
    const count = env.entries.length;
    state.handleSelect('cores'); assert.equal(env.entries.length, count);
    env.history.back(); await flush();
    assert.equal(state.currentView.value, 'inbounds'); assert.equal(state.inboundSearch.value, 'port 443'); assert.equal(env.sandbox.scrollY, 180);
    env.history.forward(); await flush(); assert.equal(state.currentView.value, 'cores');
    state.handleSelect('routing'); assert.equal(env.assignments.at(-1), '/workspace#routing');
    state.handleSelect('subscriptions'); assert.equal(env.assignments.at(-1), '/workspace#subscriptions');
    state.handleSelect('site'); await flush(); assert.equal(state.currentView.value, 'legacy');
});
test('cross-document navigation synchronously clears real share secrets and aborts late reads', async () => {
    for (const destination of ['proxy', 'certificates', 'account']) {
        for (const pending of [false, true]) {
            const env = environment(), state = env.app();
            let resolveRead, signal;
            env.sandbox.axios.get = (_path, options) => {
                signal = options.signal;
                return new Promise(resolve => { resolveRead = resolve; });
            };
            const opening = state.showQrCode({id: 7});
            if (!pending) { resolveRead({data: 'synthetic-share-secret'}); await opening; }
            assert.equal(state.showQrDialog.value, true);
            const position = env.position;
            assert.equal(env.sandbox.VUI_NAV.navigate(destination), true);
            assert.equal(state.showQrDialog.value, false);
            assert.equal(state.qrBusy.value, false);
            assert.equal(state.qrLink.value, '');
            if (pending) {
                assert.equal(signal.aborted, true);
                resolveRead({data: 'synthetic-late-secret'}); await opening;
            }
            await flush();
            assert.equal(state.qrLink.value, '');
            assert.equal(env.position, position, 'cleanup must not traverse old modal history');
            assert.equal(env.assignments.at(-1), '/' + destination);
            assert.equal(JSON.stringify(env.entries).includes('secret'), false);
        }
    }
});
test('real inbound dirty editor Back blocks before clearing, accepted Back clears secrets and Forward stays closed', async () => {
    const env = environment(), state = env.app();
    await state.openAdvancedInbound(); state.newInbound.remark = 'unsaved'; state.newInbound.profile.reality_uuid = 'fake-sensitive-value';
    env.sandbox.confirm = () => false;
    env.history.back(); await flush();
    assert.equal(state.showAddInbound.value, true); assert.equal(state.newInbound.remark, 'unsaved');
    assert.equal(env.history.state.modal, 'inbound-editor');
    assert.equal(JSON.stringify(env.entries).includes('fake-sensitive-value'), false);
    env.sandbox.confirm = () => true;
    env.history.back(); await flush();
    assert.equal(state.showAddInbound.value, false); assert.equal(state.newInbound.profile.reality_uuid, '');
    env.history.forward(); await flush(); assert.equal(state.showAddInbound.value, false); assert.equal(env.history.state.modal, undefined);
});
test('real inbound saving Back and route clicks cannot dismiss the editor or change view', async () => {
    const env = environment(), state = env.app();
    await state.openAdvancedInbound();
    env.sandbox.axios.post = () => new Promise(() => {});
    void state.saveInbound();
    assert.equal(state.savingInbound.value, true);
    env.history.back(); await flush();
    assert.equal(state.showAddInbound.value, true); assert.equal(env.history.state.modal, 'inbound-editor');
    state.handleSelect('dashboard'); await flush();
    assert.equal(state.currentView.value, 'inbounds'); assert.equal(state.showAddInbound.value, true);
});
test('real app page traversal invalidates an editor read before it resolves', async () => {
    const env = environment('/ui/#dashboard'), state = env.app();
    state.handleSelect('inbounds'); await flush();
    let reply;
    env.sandbox.axios.get = path => path.endsWith('/editor') ? new Promise(resolve => { reply = resolve; }) : Promise.resolve({data: []});
    const opening = state.openEditInbound({id: 8, mutation_revision: 'a'.repeat(64)});
    env.history.back(); await flush();
    reply({data: {id: 8, mutation_revision: 'a'.repeat(64), profile: {}, credentials: {}, core: 'xray', protocol: 'vless'}});
    await opening;
    assert.equal(state.currentView.value, 'dashboard'); assert.equal(state.showAddInbound.value, false);
});
test('cold/refresh modal marker is removed without navigating out and history never serializes callbacks', () => {
    const env = environment('/proxy');
    let closed = 0;
    env.sandbox.VUI_NAV.modal.close('yaml-preview'); assert.equal(env.position, 0);
    env.sandbox.VUI_NAV.modal.open('yaml-preview', () => closed++);
    assert.equal(env.entries.length, 2);
    assert.deepEqual(Object.keys(env.history.state).sort(), ['modal', 'position', 'vuiNavigation']);
    assert.equal(JSON.stringify(env.history.state).includes('close'), false);
    const cold = environment('/proxy');
    assert.equal(cold.history.state.modal, undefined); assert.equal(cold.position, 0); assert.equal(closed, 0);
});

class Element {
    constructor(id = '', tag = 'div') { Object.assign(this, {id, tagName: tag.toUpperCase(), value: '', hidden: false, checked: false, disabled: false, open: false, textContent: '', dataset: {}, children: [], events: new Map(), files: []}); }
    addEventListener(name, fn) { this.events.set(name, [...(this.events.get(name) || []), fn]); }
    async emit(name, event = {preventDefault() {}}) { for (const fn of this.events.get(name) || []) await fn(event); }
    setAttribute(key, value) { if (key.startsWith('data-')) this.dataset[key.slice(5)] = value; else this[key] = value; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    querySelectorAll(selector) { const all = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]); return selector === '*' ? all : all.filter(child => child.tagName === 'INPUT' && (selector === 'input' || child.checked)); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    showModal() { this.open = true; }
    close() { this.open = false; void this.emit('close'); }
    focus() {}
}
function attachDOM(env, html) {
    const ids = new Map();
    for (const match of html.matchAll(/<([\w-]+)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
        const item = new Element(match[2], match[1]); item.hidden = /\shidden(?:\s|>)/.test(match[0]);
        const section = match[0].match(/data-workspace-section="([^"]+)"/); if (section) item.dataset.workspaceSection = section[1];
        ids.set(match[2], item);
    }
    Object.assign(env.sandbox.document, {getElementById(id) { assert(ids.has(id), id); return ids.get(id); },
        createElement(tag) { return new Element('', tag); }, querySelectorAll(selector) { return selector === '[data-workspace-section]' ? [...ids.values()].filter(item => item.dataset.workspaceSection) : []; }});
    return id => env.sandbox.document.getElementById(id);
}
async function proxyEnvironment() {
    const env = environment('/proxy');
    const get = attachDOM(env, fs.readFileSync('web/proxy.html', 'utf8'));
    const snapshot = {revision: 'a'.repeat(64), settings: {schema_version: 1, local_proxy_enabled: false, relay_enabled: false,
        selected_upstream_id: null, local_port: 7891, local_username: '', local_password_set: false, dns_policy_version: 1, deny_policy_version: 1}, upstreams: [], active: null, pending: null};
    const requests = [];
    env.sandbox.fetch = async (url, options) => {
        requests.push({url, options});
        const value = url.endsWith('/capabilities') ? {apply_available: true} : url === '/api/inbounds' ? [] :
            url.endsWith('/check') ? {valid: true, phase: 'checked', applied: false, revision: snapshot.revision, snapshot, coverage: []} :
            url.endsWith('/import/preview') ? {candidates: [{index: 0, supported: true}], ignored_sections: []} : snapshot;
        return {ok: true, status: 200, json: async () => value};
    };
    env.sandbox.crypto = {getRandomValues(bytes) { return bytes.fill(7); }};
    vm.runInContext(source('proxy.js'), env.context); await flush();
    return {...env, get, requests, snapshot};
}
test('real proxy import Back respects unsaved confirmation, clears raw text and keeps page active', async () => {
    const env = await proxyEnvironment();
    await env.get('open-import').emit('click'); env.get('import-content').value = 'fake-sensitive-import';
    env.sandbox.confirm = () => false;
    env.history.back(); await flush();
    assert.equal(env.get('import-dialog').open, true); assert.equal(env.get('import-content').value, 'fake-sensitive-import');
    env.sandbox.confirm = () => true;
    env.history.back(); await flush();
    assert.equal(env.get('import-dialog').open, false); assert.equal(env.get('import-content').value, '');
    await env.get('refresh').emit('click');
    assert.equal(env.get('notice').textContent.includes('重新读取'), true);
    assert.equal(JSON.stringify(env.entries).includes('fake-sensitive-import'), false);
});
test('real proxy import saving Back blocks without clearing its request state', async () => {
    const env = await proxyEnvironment();
    await env.get('open-import').emit('click'); env.get('import-content').value = 'fake-sensitive-import';
    await env.get('preview-import').emit('click');
    const radio = env.get('candidates').querySelectorAll('input')[0]; radio.checked = true; await radio.emit('change');
    const fetch = env.sandbox.fetch;
    env.sandbox.fetch = (url, options) => url.endsWith('/profiles') ? new Promise(() => {}) : fetch(url, options);
    void env.get('import-form').emit('submit'); await flush();
    env.history.back(); await flush();
    assert.equal(env.get('import-dialog').open, true); assert.equal(env.history.state.modal, 'upstream-import');
    assert.equal(env.get('import-content').value, 'fake-sensitive-import');
});
test('real proxy apply review Back cancels only review, and applying Back cannot abandon an in-flight transaction', async () => {
    const env = await proxyEnvironment();
    await env.get('apply').emit('click'); await flush();
    assert.equal(env.get('apply-review').hidden, false, env.get('notice').textContent); assert.equal(env.history.state.modal, 'apply-review');
    env.history.back(); await flush();
    assert.equal(env.get('apply-review').hidden, true);
    await env.get('apply').emit('click'); await flush();
    const fetch = env.sandbox.fetch;
    env.sandbox.fetch = (url, options) => url.endsWith('/apply') ? new Promise(() => {}) : fetch(url, options);
    void env.get('confirm-apply').emit('click'); await flush();
    assert.equal(env.get('apply-review').hidden, true);
    env.history.back(); await flush();
    assert.equal(env.get('proxy-workspace').hidden, false);
    assert.equal(env.assignments.length, 0);
});
test('real YAML preview Back clears input, aborts old requests and Forward remains closed', async () => {
    const env = environment('/proxy'), get = attachDOM(env, fs.readFileSync('web/proxy.html', 'utf8'));
    vm.runInContext(source('mihomo-preview.js'), env.context);
    await get('open-yaml-preview').emit('click'); get('yaml-candidate').value = 'fake-sensitive-yaml';
    let request;
    env.sandbox.fetch = (_url, options) => { request = options; return new Promise(() => {}); };
    void get('yaml-preview-form').emit('submit');
    env.history.back(); await flush();
    assert.equal(get('yaml-preview-dialog').open, false); assert.equal(get('yaml-candidate').value, ''); assert.equal(request.signal.aborted, true);
    env.history.forward(); await flush(); assert.equal(get('yaml-preview-dialog').open, false);
});
test('real login resumes an enumerated original page and account update clears passwords then requires login', async () => {
    const env = environment('/login?return=subscriptions'), get = attachDOM(env, fs.readFileSync('web/account.html', 'utf8'));
    const requests = [];
    env.sandbox.fetch = async (url, options) => { requests.push({url, options}); return {ok: options.method === 'POST', status: options.method === 'POST' ? 200 : 401, json: async () => ({detail: 'Authentication required'})}; };
    vm.runInContext(source('account.js'), env.context); await flush();
    get('login-name').value = 'fake-admin'; get('login-password').value = 'fake-password';
    await get('login-form').emit('submit');
    assert.equal(env.replacements.at(-1), '/workspace#subscriptions'); assert.equal(get('login-password').value, '');
    assert.equal(JSON.stringify(env.entries).includes('fake-password'), false);
    const account = environment('/account'), a = attachDOM(account, fs.readFileSync('web/account.html', 'utf8'));
    account.sandbox.fetch = async () => ({ok: true, status: 200, json: async () => ({username: 'fake-admin'})});
    vm.runInContext(source('account.js'), account.context); await flush();
    a('current-password').value = 'old-fake-password'; a('new-password').value = 'new-fake-password';
    await a('profile-form').emit('submit');
    assert.equal(account.replacements.at(-1), '/login?return=account'); assert.equal(a('current-password').value, ''); assert.equal(a('new-password').value, '');
    assert.equal(a('account-panel').hidden, true);
});
test('real workspace switches sections without copying drafts, clears issued URL and restores section scroll', async () => {
    const env = environment('/workspace#subscriptions'), get = attachDOM(env, fs.readFileSync('web/workspace.html', 'utf8'));
    const revision = 'a'.repeat(64), settings = {mode: 'standard', direct_domains: [], proxy_domains: [], presets: {}, bypass_cgnat: false, intranet: []};
    env.sandbox.fetch = async url => ({ok: true, status: 200,
        headers: {get(name) { return name === 'ETag' ? '"' + revision + '"' : revision; }},
        json: async () => url.endsWith('/catalog') ? {categories: [], presets: []} : url.endsWith('/snapshot') ? {revision, settings} :
            url.endsWith('/preview') ? {revision, sections: [], warnings: []} : url === '/api/auth/me' ? {username: 'fake-admin'} : []});
    vm.runInContext(source('workspace.js'), env.context); await flush();
    assert.equal(get('routing').hidden, true); assert.equal(get('subscriptions').hidden, false); assert.equal(get('issued').hidden, true);
    get('issued-urls').value = 'fake-token-url'; get('issued').hidden = false; env.sandbox.scrollY = 220;
    assert.equal(env.sandbox.VUI_NAV.navigate('routing'), true, get('notice').textContent); await flush();
    assert.equal(get('issued-urls').value, ''); assert.equal(get('issued').hidden, true); assert.equal(get('routing').hidden, false);
    get('proxy').value = 'unsaved.example.test';
    env.history.back(); await flush();
    assert.equal(get('proxy').value, 'unsaved.example.test'); assert.equal(get('subscriptions').hidden, false); assert.equal(env.sandbox.scrollY, 220);
    assert.equal(JSON.stringify(env.entries).includes('fake-token-url'), false);
});
test('refresh preserves valid history cursor so rejected multi-step Back restores the exact dirty modal entry', async () => {
    const env = environment('/ui/#dashboard');
    env.sandbox.VUI_NAV.navigate('inbounds'); env.sandbox.VUI_NAV.navigate('cores');
    assert.equal(env.history.state.position, 2);
    // A real document refresh replaces listeners and executes the real helper.
    env.events.clear(); vm.runInContext(source('navigation.js'), env.context);
    assert.equal(env.history.state.position, 2);
    env.sandbox.VUI_NAV.navigate('inbounds');
    let closed = false;
    env.sandbox.VUI_NAV.modal.open('inbound-editor', () => { closed = true; }, () => false);
    const expectedPosition = env.position;
    env.history.go(-3); await flush();
    assert.equal(env.position, expectedPosition); assert.equal(env.sandbox.location.hash, '#inbounds');
    assert.equal(env.history.state.modal, 'inbound-editor'); assert.equal(closed, false);
});
test('real app forced session expiry clears editor and can replace login while a save is busy', async () => {
    const env = environment('/ui/#inbounds'), state = env.app();
    await state.openAdvancedInbound(); state.newInbound.profile.reality_uuid = 'fake-sensitive-value';
    env.sandbox.axios.post = () => new Promise(() => {}); void state.saveInbound();
    vm.runInContext('endAdminSession()', env.context);
    assert.equal(state.showAddInbound.value, false); assert.equal(state.newInbound.profile.reality_uuid, '');
    assert.equal(env.replacements.at(-1), '/login?return=inbounds');
    let blocked = false;
    env.emit('beforeunload', {preventDefault() { blocked = true; }});
    assert.equal(blocked, false);
});
test('address-bar hash navigation obeys real editor dirty guard and restores the original cursor', async () => {
    const env = environment('/ui/#inbounds'), state = env.app();
    await state.openAdvancedInbound(); state.newInbound.remark = 'unsaved'; env.sandbox.confirm = () => false;
    const originalPosition = env.position;
    // Browser-created hash entries initially inherit the old state.
    env.history.pushState(env.history.state, '', '/ui/#cores'); env.emit('hashchange'); await flush();
    assert.equal(env.position, originalPosition); assert.equal(state.currentView.value, 'inbounds'); assert.equal(state.showAddInbound.value, true);
    env.history.forward(); await flush();
    assert.equal(env.position, originalPosition); assert.equal(state.showAddInbound.value, true);
    env.sandbox.confirm = () => true;
    env.history.forward(); await flush();
    assert.equal(state.currentView.value, 'cores'); assert.equal(state.showAddInbound.value, false);
});
test('simple operation dialog uses the actual navigation modal layer and Back clears delivered data',async()=>{
    const f=environment();const {Document}=require('./fixtures/operation_dom.cjs');
    const document=new Document(),root=document.createElement('div');document.body.append(root);
    f.sandbox.document=document;f.sandbox.removeEventListener=()=>{};
    f.sandbox.VUI_OPERATION_FLOW=require('../web/js/operation-flow.js');
    f.sandbox.CustomEvent=class{constructor(type,options={}){this.type=type;this.detail=options.detail;}};
    const backend=require('./fixtures/operation_http_fixture.cjs').fixture({active:true});
    vm.runInContext(source('simple-operations.js'),f.context);
    const view=new f.sandbox.VUI_SIMPLE_OPERATIONS.View(root,{dialog:true,request:backend.api});
    await view.open('direct');assert.equal(f.history.state.modal,'simple-operations');assert.equal(f.position,1);
    view.secret='fake-ephemeral-connection';view.info={kind:'share'};
    f.history.back();await flush();assert.equal(view.dialog.open,false);assert.equal(view.secret,'');assert.equal(view.content.children.length,0);
    f.history.forward();await flush();assert.equal(view.dialog.open,false);assert.equal(f.history.state.modal,undefined);
    view.destroy();
});
