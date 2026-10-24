'use strict';
// Shipped controller/template, checksum-pinned Vue, Axios and real ElButton.
// HTTP adapter, clock, visibility and renderer/native-disabled host are synthetic.
// These finite controls are not native browser, server, runtime or device proof.
const assert = require('node:assert/strict');
const {createHash} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const assets = process.env.VUI_FRONTEND_ASSETS;
assert(assets, 'VUI_FRONTEND_ASSETS must point to checksum-pinned frontend assets');
const pins = JSON.parse(fs.readFileSync(path.join(root, 'deploy/frontend-pins.json')));
const provenance = JSON.parse(fs.readFileSync(path.join(assets, 'PROVENANCE.json')));
const assetSources = {};
for (const [name, filename] of [['vue', 'vue.js'], ['axios', 'axios.js'],
    ['element-plus', 'element-plus.js'], ['@element-plus/icons-vue', 'icons.js']]) {
    for (const [key, value] of Object.entries(pins[name]))
        assert.deepEqual(provenance[name][key], value, name + ' provenance ' + key);
    const bytes = fs.readFileSync(path.join(assets, filename));
    assert.equal(createHash('sha256').update(bytes).digest('hex'),
        provenance[name].selected_sha256[filename], filename + ' checksum');
    assetSources[name] = bytes.toString();
}
const runtimeErrors = [];
class MouseEventDouble {
    constructor() {this.stopped = false;}
    stopPropagation() {this.stopped = true;}
}
const runtime = vm.createContext({console: {...console,
    warn: (...args) => runtimeErrors.push(args), error: (...args) => runtimeErrors.push(args)},
    MouseEvent: MouseEventDouble, setTimeout, clearTimeout});
for (const name of ['vue', 'element-plus', '@element-plus/icons-vue'])
    vm.runInContext(assetSources[name], runtime, {filename: name});
const V = runtime.Vue, E = runtime.ElementPlus, I = runtime.ElementPlusIconsVue;
assert.equal(V.version, pins.vue.version); assert.equal(E.version, pins['element-plus'].version);
// Optional source arguments support finite local destructive controls only.
const filename = process.argv[2] || path.join(root, 'web/js/app.js');
const source = fs.readFileSync(filename, 'utf8');
const html = fs.readFileSync(process.argv[3] || path.join(root, 'web/index.html'), 'utf8');
const h1 = html.indexOf('<div class="header-left">'), h2 = html.indexOf('<div class="header-right">');
const d1 = html.indexOf('<div v-if="currentView === \'dashboard\'"'),
    d2 = html.indexOf('<div v-if="currentView === \'inbounds\'"');
assert(h1 >= 0 && h2 > h1 && d1 > h2 && d2 > d1);
assert.equal((html.slice(h2, d1).match(/<el-button\b/g) || []).length, 2, 'restart header unchanged');
const render = V.compile('<section>' + html.slice(h1, h2) + html.slice(d1, d2) + '</section>', {
    decodeEntities(value) {assert.doesNotMatch(value, /&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);/i); return value;}
});
const SYSTEM = '/api/system/status', CORE = '/api/cores/status', AUTH = '/api/auth/me';
const unrelated = ['/api/inbounds', '/api/routing/mihomo/catalog', '/api/routing/mihomo',
    '/api/routing/mihomo/preview', '/api/subscription/mihomo-warnings'];
const system = (percent = 37) => ({os: 'Synthetic Linux', cpu: {percent, cores: 2},
    memory: {percent, used: percent + ' MiB', total: '100 MiB'}});
const cores = (running = true) => ({xray: {running, binary_exists: true},
    'sing-box': {running, binary_exists: true}});
const failureText = url => url === SYSTEM ? 'Failed to fetch status' : 'Failed to fetch core status';
function renderer() {
    const node = (type, text = '') => ({type, text, props: {}, children: [], parent: null});
    const remove = item => {
        if (item.parent) item.parent.children.splice(item.parent.children.indexOf(item), 1);
        item.parent = null;
    };
    return {host: node('root'), ...V.createRenderer({
        createElement: type => node(type), createText: text => node('#text', text), createComment: text => node('#comment', text),
        insert(item, parent, anchor = null) {
            remove(item); item.parent = parent;
            parent.children.splice(anchor ? parent.children.indexOf(anchor) : parent.children.length, 0, item);
        },
        remove, setText(item, text) {item.text = text;},
        setElementText(item, text) {item.text = text; item.children = [];},
        patchProp(item, key, previous, value) {item.props[key] = value;},
        parentNode: item => item.parent,
        nextSibling: item => item.parent?.children[item.parent.children.indexOf(item) + 1] || null,
    })};
}
const walk = node => [node, ...node.children.flatMap(walk)];
const text = node => ((node.type === '#comment' ? '' : node.text) +
    node.children.map(text).join(' ')).replace(/\s+/g, ' ').trim();
const flush = async () => {for (let i = 0; i < 100; i++) await Promise.resolve(); await V.nextTick();};

function fixture(t, held = [], initiallyHidden = false) {
    let now = 0, timerID = 0, options, state, mountedDone = false, authStatus = 200;
    const holds = new Set(held), timers = new Map(), listeners = new Set();
    const requests = [], errors = [], expectedErrors = [], messages = [], redirects = [], channels = [], renderErrors = [];
    const later = (fn, delay) => {const id = ++timerID; timers.set(id, {fn, delay, due: now + delay}); return id;};
    const cancel = id => timers.delete(id);
    const document = {hidden: initiallyHidden, getElementById() {return null;},
        addEventListener(name, fn) {assert.equal(name, 'visibilitychange'); listeners.add(fn);},
        removeEventListener(name, fn) {assert.equal(name, 'visibilitychange'); listeners.delete(fn);}};
    const context = vm.createContext({console: {...console, error: (...args) => errors.push(args)},
        setTimeout: later, clearTimeout: cancel, addEventListener() {}, document,
        location: {hash: '', replace: target => redirects.push(target)},
        BroadcastChannel: class {constructor() {channels.push(this);} postMessage() {} close() {}},
        ElementPlus: {ElMessage: Object.fromEntries(['error', 'success', 'warning'].map(kind =>
            [kind, message => messages.push({kind, message})])), ElMessageBox: {}}, ElementPlusIconsVue: I});
    vm.runInContext(assetSources.axios, context, {filename: 'pinned/axios.js'});
    const A = context.axios; assert.equal(A.VERSION, pins.axios.version); assert.equal(A.defaults.timeout, 0);
    const dataFor = url => url === AUTH ? {username: 'synthetic-admin'} : url === SYSTEM ? system() :
        url === CORE ? cores() : url === '/api/inbounds' || url.endsWith('warnings') ? [] : {};
    const pending = url => requests.filter(request => request.url === url && !request.settled);
    A.defaults.adapter = config => {
        assert.equal(config.method, 'get', 'no writes, restart or other mutation');
        assert([AUTH, SYSTEM, CORE, ...unrelated].includes(config.url), 'no new API: ' + config.url);
        if ([SYSTEM, CORE].includes(config.url)) {
            assert.equal(config.timeout, 5000, 'original status limit');
            assert.equal(pending(config.url).length, 0, 'one request per endpoint');
        }
        const request = {url: config.url, config, settled: false, started: now}; requests.push(request);
        return new Promise((resolve, reject) => {
            let timer;
            request.finish = (status = 200, data = dataFor(config.url)) => {
                assert(!request.settled, 'one settlement'); request.settled = true; request.status = status;
                if (timer) cancel(timer);
                const response = {status, data, headers: {}, config, statusText: String(status)};
                if (config.validateStatus && !config.validateStatus(status))
                    reject(new A.AxiosError('Synthetic HTTP ' + status, 'ERR_BAD_RESPONSE', config, null, response));
                else resolve(response);
            };
            if (config.timeout > 0) timer = later(() => {
                request.settled = true; request.timedOut = true;
                reject(new A.AxiosError('Synthetic timeout', 'ECONNABORTED', config));
            }, config.timeout);
            if (!holds.has(config.url)) request.finish(config.url === AUTH ? authStatus : 200);
        });
    };
    const stub = {component() {}, use() {}, mount() {}};
    context.Vue = {...V, createApp(value) {options = value; return stub;},
        onMounted(cb) {V.onMounted(async () => {await cb(); mountedDone = true;});}};
    vm.runInContext(source, context, {filename});
    const host = renderer(), app = host.createApp({setup() {state = options.setup(); return state;}, render});
    app.config.errorHandler = error => renderErrors.push(error);
    app.component('el-button', E.ElButton);
    // Presentation-only probes; the action itself uses the actual pinned component.
    for (const name of ['el-tag', 'el-row', 'el-col', 'el-card', 'el-progress'])
        app.component(name, {setup(props, {attrs, slots}) {
            return () => V.h(name, attrs, [slots.header?.(), slots.default?.()]);
        }});
    app.mount(host.host);
    const find = id => walk(host.host).filter(node => node.props.id === id);
    const button = () => {const found = find('refresh-status'); assert.equal(found.length, 1); return found[0];};
    const stateOfButton = (disabled, loading) => {
        const control = button(); assert.equal(control.type, 'button'); assert.equal(text(control), '刷新状态');
        assert.equal(control.props.disabled, disabled, 'native disabled state');
        assert.equal(control.props.ariaDisabled, disabled, 'component aria-disabled state');
        assert.equal(control.props.class.split(' ').includes('is-loading'), loading, 'loading state');
        assert.equal(control.props.type, 'button', 'not a form submit');
        assert.equal(control.props['aria-describedby'], 'status-refresh-help');
        assert.equal(text(find('status-refresh-help')[0]), '只读取最新状态，不会重启核心。');
        assert.equal(state.refreshingStatus.value, loading);
    };
    const click = async () => {
        // Synthetic native host admission, not a forced click on disabled DOM.
        if (button().props.disabled) {await flush(); return false;}
        button().props.onClick(new MouseEventDouble()); await flush(); return true;
    };
    const finish = async (url, status = 200, data = dataFor(url)) => {
        const waiting = pending(url); assert.equal(waiting.length, 1, 'one pending request for ' + url);
        if ([SYSTEM, CORE].includes(url) && status !== 200) expectedErrors.push(failureText(url));
        waiting[0].finish(status, data); await flush();
    };
    const advance = async delta => {
        const end = now + delta; let steps = 0; await flush();
        for (;;) {
            const next = [...timers].filter(([, timer]) => timer.due <= end)
                .sort((a, b) => a[1].due - b[1].due || a[0] - b[0])[0];
            if (!next) break;
            assert(++steps < 1000, 'bounded synthetic clock'); now = next[1].due;
            timers.delete(next[0]); next[1].fn(); await flush();
        }
        now = end; await flush();
    };
    const show = async hidden => {document.hidden = hidden; for (const cb of listeners) cb(); await flush();};
    t.after(async () => {
        holds.clear(); vm.runInContext('statusPollers.forEach(p => p.stop())', context);
        for (let round = 0; round < 5; round++) {
            for (const request of requests.filter(request => !request.settled)) request.finish();
            await flush();
        }
        vm.runInContext('statusPollers.forEach(p => p.stop())', context); app.unmount(); await flush();
        assert(requests.every(request => request.settled)); assert.equal(timers.size, 0); assert.equal(listeners.size, 0);
        assert.deepEqual(errors.map(args => args[0]), expectedErrors, 'all error paths explicit');
        assert.deepEqual(messages, [], 'no misleading success toast or new notifications');
        assert.deepEqual(renderErrors, []); assert.deepEqual(runtimeErrors, []);
    });
    return {state, requests, errors, expectedErrors, redirects, holds, timers, listeners, button, click,
        pending: () => stateOfButton(true, true), idle: () => stateOfButton(false, false),
        unavailable: () => stateOfButton(true, false), finish, advance, show,
        count: url => requests.filter(request => request.url === url).length,
        pollers: () => vm.runInContext('statusPollers.length', context),
        get done() {return mountedDone;}, setAuth(value) {authStatus = value;},
        invalidate() {channels[0].onmessage({data: 'session-ended'});},
        snapshot: () => ({system: state.systemOsKnown.value, core: state.coreFieldKnown('xray', 'running'),
            cpu: state.systemStatus.value.cpu?.percent,
            percentages: walk(host.host).filter(node => node.type === 'el-progress').map(node => node.props.percentage),
            cpuTag: find('cpu-status')[0].props['data-status'],
            coreTag: text(find('xray-status-tag')[0])})};
}
const counts = f => [f.count(SYSTEM), f.count(CORE)];
const holdStatus = f => {f.holds.add(SYSTEM); f.holds.add(CORE);};

test('pre-auth disabled and synchronous admission cannot treat initial currentUser object as authentication', async t => {
    const f = fixture(t, [AUTH]); f.unavailable();
    assert.equal(f.state.currentUser.value.username, ''); assert.equal(await f.click(), false);
    await f.state.refreshStatus(); await f.advance(60000);
    assert.deepEqual(f.requests.map(request => request.url), [AUTH]);
    assert.equal(f.pollers(), 0); f.unavailable();
    f.holds.delete(AUTH); await f.finish(AUTH); f.idle(); assert.equal(f.done, true);
    assert.deepEqual(counts(f), [1, 1]); assert.equal(f.pollers(), 2);
});
for (const status of [401, 503]) test('failed initial/current authentication leaves action unavailable: ' + status, async t => {
    const f = fixture(t, [AUTH]); await flush(); f.setAuth(status); f.holds.delete(AUTH);
    await f.finish(AUTH, status); f.unavailable(); await f.state.refreshStatus();
    await f.advance(60000); assert.equal(f.count(AUTH), 2); assert.deepEqual(counts(f), [0, 0]);
    assert.equal(f.pollers(), 0); assert.equal(f.timers.size, 0);
    assert.deepEqual(f.redirects, status === 401 ? ['/login'] : []);
});
test('current successful revalidation after initial failure enables the same existing initialization', async t => {
    const f = fixture(t, [AUTH]); await flush(); f.holds.delete(AUTH); await f.finish(AUTH, 503);
    f.idle(); assert.equal(f.count(AUTH), 2); assert.deepEqual(counts(f), [1, 1]);
    assert.equal(f.pollers(), 2); assert.equal(f.done, true); assert.deepEqual(f.redirects, []);
});
for (const first of [SYSTEM, CORE]) test('native component action holds busy until both fresh reads finish, first ' + first, async t => {
    const f = fixture(t); await flush(); f.idle(); holdStatus(f);
    assert.equal(await f.click(), true); f.pending(); assert.deepEqual(counts(f), [2, 2]);
    assert.equal(await f.click(), false); assert.deepEqual(counts(f), [2, 2]);
    const event = new MouseEventDouble(); f.button().props.onClick(event); await flush();
    assert.equal(event.stopped, true, 'pinned ElButton rejects disabled handler dispatch');
    await f.finish(first); f.pending(); assert.equal(await f.click(), false);
    await f.finish(first === SYSTEM ? CORE : SYSTEM); f.idle();
    assert.equal(await f.click(), true); f.pending(); assert.deepEqual(counts(f), [3, 3]);
    await f.finish(SYSTEM, 200, system(0)); f.pending(); await f.finish(CORE, 200, cores(false)); f.idle();
    assert.deepEqual(f.snapshot().percentages, [0, 0]); assert.equal(f.snapshot().coreTag, 'Xray Stopped');
});
test('same-tick direct calls have one synchronous manual admission before rendering', async t => {
    const f = fixture(t); await flush(); holdStatus(f);
    const owner = f.state.refreshStatus();
    assert.equal(f.state.refreshingStatus.value, true);
    await Promise.all([f.state.refreshStatus(), f.state.refreshStatus(), f.state.refreshStatus()]);
    await flush(); f.pending(); assert.deepEqual(counts(f), [2, 2]);
    await f.finish(SYSTEM); f.pending(); await f.state.refreshStatus(); assert.deepEqual(counts(f), [2, 2]);
    await f.finish(CORE); await owner; f.idle();
});
test('initial reads are not reused as manual fresh completion and do not duplicate pollers', async t => {
    const f = fixture(t, [SYSTEM, CORE]); await flush(); f.idle(); assert.equal(f.pollers(), 0);
    await f.click(); f.pending(); assert.deepEqual(counts(f), [1, 1]);
    await f.finish(SYSTEM, 200, system(12)); f.pending(); assert.deepEqual(counts(f), [2, 1]);
    await f.finish(CORE, 200, cores(false)); f.pending(); assert.deepEqual(counts(f), [2, 2]);
    assert.equal(f.pollers(), 2); assert.equal(f.done, true);
    await f.finish(SYSTEM, 200, system(24)); f.pending(); await f.finish(CORE); f.idle();
    assert.equal(f.snapshot().cpu, 24); assert.equal(f.snapshot().coreTag, 'Xray Running');
    assert.equal(f.pollers(), 2); assert.equal(f.listeners.size, 2);
});
test('natural synthetic timer reads stay single-flight and manual fresh waits for its newer reads', async t => {
    const f = fixture(t); await flush(); await f.advance(9000); assert.deepEqual(counts(f), [4, 1]);
    holdStatus(f); await f.advance(3000); assert.deepEqual(counts(f), [5, 2]);
    await f.click(); f.pending(); assert.deepEqual(counts(f), [5, 2]);
    await f.finish(CORE, 200, cores(false)); f.pending(); assert.deepEqual(counts(f), [5, 3]);
    await f.finish(SYSTEM, 200, system(12)); f.pending(); assert.deepEqual(counts(f), [6, 3]);
    await f.finish(SYSTEM, 200, system(24)); f.pending(); await f.finish(CORE); f.idle();
    assert.equal(f.snapshot().cpu, 24); assert.equal(f.snapshot().coreTag, 'Xray Running');
    f.holds.clear(); await f.advance(10000); assert.deepEqual(counts(f), [9, 4]);
    assert.equal(f.pollers(), 2); assert.equal(f.listeners.size, 2);
});
for (const failed of [SYSTEM, CORE]) test('independent failed read shows Unknown and deliberate retry recovers: ' + failed, async t => {
    const f = fixture(t); await flush(); holdStatus(f); await f.click();
    assert.equal(f.snapshot().system, true); assert.equal(f.snapshot().core, true);
    await f.finish(failed, 503); f.pending();
    assert.equal(f.snapshot().system, failed !== SYSTEM); assert.equal(f.snapshot().core, failed !== CORE);
    if (failed === SYSTEM) {assert.equal(f.snapshot().cpuTag, 'unknown'); assert.deepEqual(f.snapshot().percentages, []);}
    else assert.equal(f.snapshot().coreTag, 'Xray Unknown');
    await f.finish(failed === SYSTEM ? CORE : SYSTEM); f.idle();
    assert.equal(await f.click(), true); f.pending(); await f.finish(SYSTEM, 200, system(0));
    await f.finish(CORE, 200, cores(false)); f.idle();
    assert.equal(f.snapshot().system, true); assert.equal(f.snapshot().core, true);
    assert.deepEqual(f.snapshot().percentages, [0, 0]); assert.equal(f.snapshot().coreTag, 'Xray Stopped');
});
test('malformed replies remain Unknown without claiming success and next valid read recovers', async t => {
    const f = fixture(t); await flush(); holdStatus(f); await f.click();
    await f.finish(SYSTEM, 200, []); await f.finish(CORE, 200, {xray: {running: 'false'}}); f.idle();
    assert.equal(f.snapshot().system, false); assert.equal(f.snapshot().core, false);
    assert.deepEqual(f.snapshot().percentages, []); assert.equal(f.snapshot().coreTag, 'Xray Unknown');
    await f.click(); await f.finish(SYSTEM); await f.finish(CORE); f.idle();
    assert.equal(f.snapshot().system, true); assert.equal(f.snapshot().core, true);
});
test('original modeled 5000ms timeouts retain busy until settlement and allow later explicit retry', async t => {
    const f = fixture(t, [], true); await flush(); holdStatus(f); await f.click();
    f.expectedErrors.push(failureText(SYSTEM), failureText(CORE));
    await f.advance(4999); f.pending(); assert.deepEqual(counts(f), [2, 2]);
    await f.advance(1); f.idle(); assert.equal(f.snapshot().system, false); assert.equal(f.snapshot().core, false);
    assert.equal(f.requests.filter(request => request.timedOut).length, 2);
    f.holds.clear(); await f.click(); f.idle(); assert.deepEqual(counts(f), [3, 3]);
    assert.equal(f.snapshot().system, true); assert.equal(f.snapshot().core, true); assert.equal(f.timers.size, 0);
});
test('confirmed ended session disables action and cannot create any further reads', async t => {
    const f = fixture(t); await flush(); f.setAuth(401); f.invalidate(); await flush(); f.unavailable();
    assert.deepEqual(f.redirects, ['/login']); assert.equal(await f.click(), false);
    await f.state.refreshStatus(); await f.advance(60000); await f.show(true); await f.show(false);
    assert.deepEqual(counts(f), [1, 1]); assert.equal(f.listeners.size, 0); assert.equal(f.timers.size, 0);
});
test('session end cancels admission to queued fresh work without new status reads or late pollers', async t => {
    const f = fixture(t, [SYSTEM, CORE]); await flush(); await f.click(); f.pending();
    f.setAuth(401); f.invalidate(); await flush(); assert.deepEqual(f.redirects, ['/login']);
    await f.state.refreshStatus(); assert.deepEqual(counts(f), [1, 1]);
    await f.finish(SYSTEM); f.pending(); await f.finish(CORE); f.unavailable();
    await f.advance(60000); assert.deepEqual(counts(f), [1, 1]); assert.equal(f.pollers(), 0);
    assert.equal(f.listeners.size, 0); assert.equal(f.timers.size, 0);
});
test('a stale 401 with current valid authentication preserves readiness and independent Unknown semantics', async t => {
    const f = fixture(t); await flush(); holdStatus(f); await f.click();
    await f.finish(SYSTEM, 401); f.pending(); assert.equal(f.count(AUTH), 2);
    assert.deepEqual(f.redirects, []); assert.equal(f.snapshot().system, false);
    await f.finish(CORE); f.idle(); await f.click(); await f.finish(SYSTEM); await f.finish(CORE); f.idle();
    assert.deepEqual(counts(f), [3, 3]); assert.equal(f.snapshot().system, true);
});
test('navigation hides only the dashboard action and retains one in-flight manual owner on return', async t => {
    const f = fixture(t); await flush(); holdStatus(f); await f.click(); f.pending();
    f.state.handleSelect('inbounds'); await flush(); assert.equal(f.state.currentView.value, 'inbounds');
    f.state.handleSelect('dashboard'); await flush(); f.pending(); assert.equal(await f.click(), false);
    await f.finish(SYSTEM); await f.finish(CORE); f.idle(); assert.deepEqual(counts(f), [2, 2]);
});
test('hidden pause and visible recovery use original polling cadence without new manual timers', async t => {
    const f = fixture(t); await flush(); await f.show(true); assert.equal(f.timers.size, 0);
    await f.advance(60000); assert.deepEqual(counts(f), [1, 1]);
    await f.click(); f.idle(); assert.deepEqual(counts(f), [2, 2]); assert.equal(f.timers.size, 0);
    await f.show(false); assert.deepEqual(counts(f), [3, 3]);
    assert.deepEqual([...f.timers.values()].map(timer => timer.delay).sort((a, b) => a - b), [3000, 10000]);
    await f.advance(10000); assert.deepEqual(counts(f), [6, 4]); assert.equal(f.pollers(), 2);
});
