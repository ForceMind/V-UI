'use strict';
// Actual controller, pinned Vue compiler/reactivity and exact shipped templates.
// HTTP, timers and renderer host are simulated: not browser/server/core evidence.
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
for (const [key, value] of Object.entries(pins.vue))
    assert.deepEqual(provenance.vue[key], value, 'Vue provenance ' + key);
const bytes = fs.readFileSync(path.join(assets, 'vue.js'));
assert.equal(createHash('sha256').update(bytes).digest('hex'), provenance.vue.selected_sha256['vue.js']);
const runtime = vm.createContext({console});
vm.runInContext(bytes.toString(), runtime, {filename: 'vue.js'});
const V = runtime.Vue;
assert.equal(V.version, pins.vue.version);
const source = fs.readFileSync(path.join(root, 'web/js/app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
const headerStart = html.indexOf('<div class="header-left">');
const headerEnd = html.indexOf('<div class="header-right">');
const dashboardStart = html.indexOf('<div v-if="currentView === \'dashboard\'"');
const dashboardEnd = html.indexOf('<div v-if="currentView === \'inbounds\'"');
assert(headerStart >= 0 && headerEnd > headerStart && dashboardStart > headerEnd && dashboardEnd > dashboardStart);
const render = V.compile('<section>' + html.slice(headerStart, headerEnd) +
    html.slice(dashboardStart, dashboardEnd) + '</section>', {decodeEntities(value) {
    assert.doesNotMatch(value, /&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);/i);
    return value;
}});
const deferred = () => {
    let resolve, reject;
    const promise = new Promise((a, b) => {resolve = a; reject = b;});
    return {promise, resolve, reject};
};
function renderer() {
    const node = (type, text = '') => ({type, text, children: [], parent: null, props: {}});
    const remove = item => {
        if (item.parent) item.parent.children.splice(item.parent.children.indexOf(item), 1);
        item.parent = null;
    };
    return {host: node('root'), ...V.createRenderer({
        createElement: node, createText: text => node('#text', text), createComment: text => node('#comment', text),
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
const nodeText = node => (node.type === '#comment' ? '' : node.text) + node.children.map(nodeText).join(' ');
const text = node => nodeText(node).replace(/\s+/g, ' ').trim();
const flush = async () => {for (let n = 0; n < 20; n++) await Promise.resolve(); await V.nextTick();};
const SYSTEM = '/api/system/status', CORE = '/api/cores/status';
const system = (cpu = 37, memory = 64) => ({os: 'Synthetic Linux', cpu: {percent: cpu, cores: 2},
    memory: {percent: memory, used: memory + ' MiB', total: '100 MiB'}});
const cores = () => ({xray: {running: true, binary_exists: true}, 'sing-box': {running: false, binary_exists: true}});

function fixture(t) {
    let options, state, mounted, timerID = 0;
    const requests = [], errors = [], renderErrors = [], timers = new Map(), listeners = new Set();
    const document = {hidden: false, getElementById() {return null;},
        addEventListener(name, cb) {assert.equal(name, 'visibilitychange'); listeners.add(cb);},
        removeEventListener(name, cb) {listeners.delete(cb);}};
    const stub = {component() {}, use() {}, mount() {}};
    const pending = url => requests.filter(request => request.url === url && !request.settled);
    const context = vm.createContext({
        console: {error: (...args) => errors.push(args)}, addEventListener() {}, document,
        location: {hash: '', replace() {throw Error('No session invalidation expected');}},
        setTimeout(cb, delay) {const id = ++timerID; timers.set(id, {cb, delay}); return id;},
        clearTimeout(id) {timers.delete(id);},
        Vue: {...V, createApp(value) {options = value; return stub;}, onMounted(cb) {mounted = cb;}},
        ElementPlus: {ElMessage: {error(message) {throw Error('Unexpected message: ' + message);},
            success() {}, warning() {}}, ElMessageBox: {}}, ElementPlusIconsVue: {},
        axios: {defaults: {headers: {common: {}}}, interceptors: {response: {use() {}}}, get(url, config) {
            if ([SYSTEM, CORE].includes(url)) {
                assert.equal(config.timeout, 5000, 'existing request timeout unchanged');
                assert.equal(pending(url).length, 0, url + ' must stay single-flight');
                const gate = deferred(); requests.push({url, config, gate, settled: false}); return gate.promise;
            }
            const allowed = ['/api/auth/me', '/api/inbounds', '/api/routing/mihomo/catalog',
                '/api/routing/mihomo', '/api/routing/mihomo/preview', '/api/subscription/mihomo-warnings'];
            assert(allowed.includes(url), 'Unexpected API read: ' + url);
            return Promise.resolve({status: 200, data: url === '/api/inbounds' || url.endsWith('warnings') ? [] : {}});
        }},
    });
    vm.runInContext(source, context, {filename: 'web/js/app.js'});
    const host = renderer();
    const app = host.createApp({setup() {state = options.setup(); return state;}, render});
    app.config.errorHandler = error => renderErrors.push(error);
    // Only Element Plus presentation is probed. Vue evaluates all shipped
    // interpolations, conditional branches, status props and accessibility text.
    for (const name of ['el-tag', 'el-col', 'el-row', 'el-card', 'el-progress']) {
        app.component(name, {setup(props, {attrs, slots}) {
            return () => V.h(name, attrs, [slots.header?.(), slots.default?.()]);
        }});
    }
    app.mount(host.host);
    t.after(() => {
        vm.runInContext('statusPollers.forEach(poller => poller.stop())', context);
        app.unmount(); assert.equal(timers.size, 0); assert.equal(listeners.size, 0);
        assert.deepEqual(renderErrors, [], 'malformed responses must not crash the rendered dashboard');
        assert.equal(requests.filter(request => !request.settled).length, 0, 'fixture must settle every read');
    });
    const find = id => {
        const matches = walk(host.host).filter(node => node.props.id === id);
        assert.equal(matches.length, 1, id + ' must have one stable rendered selector'); return matches[0];
    };
    const snapshot = () => ({text: text(host.host),
        tags: walk(host.host).filter(node => node.type === 'el-tag').map(node => ({type: node.props.type, text: text(node)})),
        percentages: walk(host.host).filter(node => node.type === 'el-progress').map(node => node.props.percentage)});
    const settle = (url, kind, data) => {
        const waiting = pending(url); assert.equal(waiting.length, 1, url + ' exactly one pending');
        const request = waiting[0]; request.settled = true;
        if (kind === 'ok') request.gate.resolve({status: 200, data});
        else request.gate.reject(kind === '500' ? {response: {status: 500}} :
            {code: 'ECONNABORTED', message: 'timeout of 5000ms exceeded'});
    };
    const fire = delay => {
        const entry = [...timers].find(([, timer]) => timer.delay === delay);
        assert(entry, 'timer ' + delay); timers.delete(entry[0]); return entry[1].cb();
    };
    const show = hidden => {document.hidden = hidden; for (const listener of listeners) listener();};
    const start = async (systemData = system(), coreData = cores()) => {
        const mounting = mounted(); await flush();
        settle(SYSTEM, 'ok', systemData); settle(CORE, 'ok', coreData); await mounting; await flush();
    };
    const read = async (url, kind, data) => {
        const reading = fire(url === SYSTEM ? 3000 : 10000); await flush();
        settle(url, kind, data); await reading; await flush();
    };
    return {snapshot, find, mount: () => mounted(), start, read, settle, fire, pending,
        requests, errors, state, timers, show};
}

function assertField(f, id, known, content) {
    const node = f.find(id);
    assert.equal(node.props['data-status'], known ? 'known' : 'unknown', id);
    assert.match(text(node), content, id);
    if (!known) {
        assert.notEqual(node.props.type, 'success', id + ' unknown must not be green');
        if (id.endsWith('-status') && !id.endsWith('-tag'))
            assert(walk(node).some(child => child.props.role === 'status' && /状态未确认/.test(text(child))),
                id + ' unknown needs an accessible notice');
    }
}
function assertSystemUnknown(f) {
    assert.deepEqual(f.snapshot().percentages, [], 'unconfirmed metrics must not become 0 or stale progress');
    assertField(f, 'system-status-tag', false, /^System Unknown$/);
    assertField(f, 'cpu-status', false, /Unknown/); assertField(f, 'memory-status', false, /Unknown/);
    assert.doesNotMatch(text(f.find('cpu-status')), /2 Cores/);
    assert.doesNotMatch(text(f.find('memory-status')), /MiB/);
}
function assertCoreUnknown(f) {
    assert.deepEqual(f.snapshot().tags.filter(tag => /Xray|sing-box/.test(tag.text)).map(tag => tag.text),
        ['Xray Unknown', 'sing-box Unknown']);
    for (const [prefix, name] of [['xray', 'Xray'], ['singbox', 'sing-box']]) {
        assertField(f, prefix + '-status-tag', false, new RegExp('^' + name + ' Unknown$'));
        assertField(f, prefix + '-core-status', false, /Unknown/);
        assert.doesNotMatch(text(f.find(prefix + '-core-status')), /Installed|Missing|Running|Stopped/);
    }
}
function assertHealthy(f, percentages = [37, 64]) {
    assert.deepEqual(f.snapshot().percentages, percentages);
    assertField(f, 'system-status-tag', true, /^Running on Synthetic Linux$/);
    assertField(f, 'cpu-status', true, /CPU/); assertField(f, 'memory-status', true, /Memory/);
    assertField(f, 'xray-status-tag', true, /^Xray Running$/);
    assertField(f, 'singbox-status-tag', true, /^sing-box Stopped$/);
    assertField(f, 'xray-core-status', true, /Installed Running/);
    assertField(f, 'singbox-core-status', true, /Installed Stopped/);
}

test('initial, pending and first failed reads remain Unknown without fabricated zeros', async t => {
    const f = fixture(t);
    assertSystemUnknown(f); assertCoreUnknown(f); const initial = f.snapshot();
    const mounting = f.mount(); await flush();
    assert.equal(f.pending(SYSTEM).length, 1); assert.equal(f.pending(CORE).length, 1);
    assert.deepEqual(f.snapshot(), initial);
    f.settle(SYSTEM, '500'); f.settle(CORE, '500'); await mounting; await flush();
    assertSystemUnknown(f); assertCoreUnknown(f); assert.equal(f.errors.length, 2);
    assert.deepEqual([...f.timers.values()].map(timer => timer.delay).sort((a, b) => a - b), [3000, 10000]);
});

test('ordinary pending polls keep confirmed display, and system failure invalidates only system', async t => {
    const f = fixture(t); await f.start(); assertHealthy(f); const healthy = f.snapshot();
    const pending = f.fire(3000); await flush(); assert.deepEqual(f.snapshot(), healthy);
    f.settle(SYSTEM, '500'); await pending; await flush(); assertSystemUnknown(f);
    assertField(f, 'xray-status-tag', true, /^Xray Running$/);
    assertField(f, 'singbox-core-status', true, /Installed Stopped/);
    assert.equal(f.state.coreStatus.value.xray.running, true);
    await f.read(SYSTEM, 'ok', system(12, 34)); assertHealthy(f, [12, 34]);
});

test('core failure does not invalidate metrics and independent 200 recovery restores core display', async t => {
    const f = fixture(t); await f.start(); const pending = f.fire(10000); await flush(); assertHealthy(f);
    f.settle(CORE, '500'); await pending; await flush(); assertCoreUnknown(f);
    assert.deepEqual(f.snapshot().percentages, [37, 64]);
    assertField(f, 'system-status-tag', true, /^Running on Synthetic Linux$/);
    await f.read(CORE, 'ok', cores()); assertHealthy(f);
});

test('synthetic timeouts invalidate each endpoint, and false or zero values are confirmed facts', async t => {
    const f = fixture(t); await f.start();
    await f.read(SYSTEM, 'timeout'); assertSystemUnknown(f);
    assertField(f, 'xray-status-tag', true, /^Xray Running$/);
    await f.read(CORE, 'timeout'); assertCoreUnknown(f); assertSystemUnknown(f);
    await f.read(SYSTEM, 'ok', system(0, 0)); assertCoreUnknown(f);
    assert.deepEqual(f.snapshot().percentages, [0, 0]);
    await f.read(CORE, 'ok', {xray: {running: false, binary_exists: false}, 'sing-box': {running: false, binary_exists: false}});
    for (const prefix of ['xray', 'singbox']) {
        assertField(f, prefix + '-status-tag', true, /Stopped$/);
        assertField(f, prefix + '-core-status', true, /Missing Stopped/);
    }
});

test('malformed or missing 200 system fields are Unknown, not coerced percentages or stale details', async t => {
    const f = fixture(t); await f.start();
    for (const data of [null, [], 'invalid', {}, {os: false, cpu: {percent: '37'}, memory: {percent: null}},
        {os: '', cpu: {percent: -1}, memory: {percent: 101}},
        {os: ' ', cpu: {percent: NaN}, memory: {percent: Infinity}}]) {
        await f.read(SYSTEM, 'ok', data); assertSystemUnknown(f);
        assertField(f, 'xray-status-tag', true, /^Xray Running$/);
    }
    await f.read(SYSTEM, 'ok', {os: 'Synthetic Linux', cpu: {percent: 0}, memory: {}});
    assert.deepEqual(f.snapshot().percentages, [0]);
    assertField(f, 'cpu-status', true, /CPU/); assertField(f, 'memory-status', false, /Unknown/);
    await f.read(SYSTEM, 'ok', system()); assertHealthy(f);
});

test('missing and nonboolean core fields cannot imply Stopped or Missing after a successful read', async t => {
    const f = fixture(t); await f.start();
    for (const data of [null, [], 'invalid', {}, {xray: {running: 'false', binary_exists: 1},
        'sing-box': {running: null, binary_exists: 'false'}}]) {
        await f.read(CORE, 'ok', data); assertCoreUnknown(f);
        assert.deepEqual(f.snapshot().percentages, [37, 64]);
    }
    await f.read(CORE, 'ok', {xray: {running: false}, 'sing-box': {binary_exists: false}});
    assertField(f, 'xray-status-tag', true, /^Xray Stopped$/);
    assertField(f, 'xray-core-status', false, /Unknown Stopped/);
    assertField(f, 'singbox-status-tag', false, /^sing-box Unknown$/);
    assertField(f, 'singbox-core-status', false, /Missing Unknown/);
    await f.read(CORE, 'ok', cores()); assertHealthy(f);
});

test('hidden pause and repeated visibility changes preserve single flight and queue one fresh result', async t => {
    const f = fixture(t); await f.start(); f.show(true); await flush(); assert.equal(f.timers.size, 0);
    const before = f.requests.length; await flush(); assert.equal(f.requests.length, before);
    f.show(false); await flush(); assert.equal(f.pending(SYSTEM).length, 1); assert.equal(f.pending(CORE).length, 1);
    f.show(true); f.show(false); f.show(true); f.show(false); await flush();
    assert.equal(f.requests.length, before + 2); assertHealthy(f);
    f.settle(SYSTEM, '500'); f.settle(CORE, '500'); await flush();
    assertSystemUnknown(f); assertCoreUnknown(f);
    assert.equal(f.pending(SYSTEM).length, 1); assert.equal(f.pending(CORE).length, 1);
    assert.equal(f.requests.length, before + 4, 'one queued fresh read per endpoint');
    f.settle(SYSTEM, 'ok', system()); f.settle(CORE, 'ok', cores()); await flush(); assertHealthy(f);
    assert.equal(f.timers.size, 2); f.show(true); await flush(); assert.equal(f.timers.size, 0);
});

test('first timeout recovery does not confirm the other endpoint while its initial read is pending', async t => {
    const f = fixture(t); const mounting = f.mount(); await flush();
    f.settle(SYSTEM, 'timeout'); await flush(); assertSystemUnknown(f); assertCoreUnknown(f);
    assert.equal(f.pending(CORE).length, 1);
    f.settle(CORE, 'ok', cores()); await mounting; await flush();
    assertSystemUnknown(f); assertField(f, 'xray-status-tag', true, /^Xray Running$/);
    const recovering = f.fire(3000); await flush(); assertSystemUnknown(f);
    f.settle(SYSTEM, 'ok', system()); await recovering; await flush(); assertHealthy(f);
});
