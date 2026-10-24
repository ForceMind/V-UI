'use strict';
// Actual pinned Vue/Element Plus buttons and shipped header/controller.
// HTTP, renderer host, MouseEvent/native-disabled gate and lifecycle are doubles.
// This is not browser, authenticated API, core restart or runtime qualification.
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
const runtimeErrors = [];
class MouseEventDouble {
    constructor() {this.stopped = false;}
    stopPropagation() {this.stopped = true;}
}
const runtime = vm.createContext({
    console: {...console, warn: (...args) => runtimeErrors.push(args), error: (...args) => runtimeErrors.push(args)},
    MouseEvent: MouseEventDouble, setTimeout, clearTimeout,
});
for (const [name, filename] of [['vue', 'vue.js'], ['element-plus', 'element-plus.js'],
    ['@element-plus/icons-vue', 'icons.js']]) {
    for (const [key, value] of Object.entries(pins[name]))
        assert.deepEqual(provenance[name][key], value, name + ' provenance ' + key);
    const bytes = fs.readFileSync(path.join(assets, filename));
    assert.equal(createHash('sha256').update(bytes).digest('hex'),
        provenance[name].selected_sha256[filename], filename + ' checksum');
    vm.runInContext(bytes.toString(), runtime, {filename});
}
const V = runtime.Vue, E = runtime.ElementPlus, I = runtime.ElementPlusIconsVue;
assert.equal(V.version, pins.vue.version);
assert.equal(E.version, pins['element-plus'].version);
// Optional file inputs are only for finite, local baseline/destructive controls.
const filename = process.argv[2] || path.join(root, 'web/js/app.js');
const source = fs.readFileSync(filename, 'utf8');
const html = fs.readFileSync(process.argv[3] || path.join(root, 'web/index.html'), 'utf8');
const start = html.indexOf('<div class="header-right">');
const end = html.indexOf('<el-dropdown', start);
assert(start > 0 && end > start, 'exact shipped restart header exists');
const header = html.slice(start, end) + '</div>';
assert.equal((header.match(/<el-button\b/g) || []).length, 2);
const render = V.compile(header, {decodeEntities(value) {
    assert.doesNotMatch(value, /&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);/i);
    return value;
}});
const deferred = () => {
    let resolve, reject;
    const promise = new Promise((a, b) => {resolve = a; reject = b;});
    return {promise, resolve, reject, settled: false};
};
const flush = async () => {for (let n = 0; n < 25; n++) await Promise.resolve(); await V.nextTick();};
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
const nodeText = node => (node.type === '#comment' ? '' : node.text) + node.children.map(nodeText).join(' ');
const cores = (running = true) => Object.fromEntries(['xray', 'sing-box'].map(core =>
    [core, {running, binary_exists: true}]));
const restartUrl = core => '/api/cores/' + core + '/restart';

function fixture(t) {
    let options, state, mounted;
    const posts = [], gets = [], messages = [], errors = [], expectedErrors = [], renderErrors = [], timers = new Set();
    const appStub = {component() {}, use() {}, mount() {}};
    const unexpected = action => {throw Error('Unexpected ' + action);};
    const context = vm.createContext({
        console: {...console, error: (...args) => errors.push(args)},
        addEventListener() {}, location: {hash: ''},
        document: {hidden: false, getElementById() {return null;}, addEventListener() {}, removeEventListener() {}},
        setTimeout(fn, delay) {assert([3000, 10000].includes(delay)); const timer = {fn, delay}; timers.add(timer); return timer;},
        clearTimeout(timer) {timers.delete(timer);},
        Vue: {...V, createApp(value) {options = value; return appStub;}, onMounted(fn) {mounted = fn;}},
        ElementPlus: {ElMessage: Object.fromEntries(['success', 'error', 'warning'].map(kind =>
            [kind, text => messages.push({kind, text})])), ElMessageBox: {}},
        ElementPlusIconsVue: I,
        axios: {defaults: {headers: {common: {}}}, interceptors: {response: {use() {}}},
            post(...args) {
                assert.equal(args.length, 1, 'existing restart has only its URL, no changed body/options');
                assert.match(args[0], /^\/api\/cores\/(xray|sing-box)\/restart$/);
                const request = {...deferred(), url: args[0]}; posts.push(request); return request.promise;
            },
            get(url, config) {
                if (url === '/api/cores/status') {
                    assert.equal(config.timeout, 5000, 'unchanged status timeout');
                    const request = {...deferred(), url}; gets.push(request); return request.promise;
                }
                const initial = {'/api/auth/me': {username: 'synthetic'}, '/api/system/status': {},
                    '/api/inbounds': [], '/api/subscription/mihomo-warnings': [],
                    '/api/routing/mihomo/catalog': {categories: [], presets: []},
                    '/api/routing/mihomo': {}, '/api/routing/mihomo/preview': {}};
                assert(Object.hasOwn(initial, url), 'only existing startup reads: ' + url);
                return Promise.resolve({data: initial[url]});
            },
            put() {unexpected('PUT');}, delete() {unexpected('DELETE');}, patch() {unexpected('PATCH');},
        },
    });
    vm.runInContext(source, context, {filename});
    const host = renderer();
    const app = host.createApp({setup() {state = options.setup(); return state;}, render});
    app.config.errorHandler = error => renderErrors.push(error);
    app.component('el-button', E.ElButton); app.component('el-icon', E.ElIcon); app.component('Refresh', I.Refresh);
    app.mount(host.host);
    const button = core => {
        const found = walk(host.host).filter(node => node.type === 'button' &&
            nodeText(node).trim() === (core === 'xray' ? 'Xray' : 'sing-box'));
        assert.equal(found.length, 1, 'one actual rendered button for ' + core); return found[0];
    };
    const pending = owner => {
        assert.equal(state.restartingCore.value, owner, 'one unchanged pending owner');
        for (const core of ['xray', 'sing-box']) {
            assert.equal(button(core).props.disabled, true, core + ' native disabled while ' + owner + ' pending');
            assert.equal(button(core).props.ariaDisabled, true, core + ' aria-disabled');
            assert.equal(button(core).props.class.split(' ').includes('is-loading'), core === owner,
                'only owning core has loading spinner');
        }
    };
    const idle = () => {
        assert.equal(state.restartingCore.value, '');
        for (const core of ['xray', 'sing-box']) {
            assert.equal(button(core).props.disabled, false, core + ' restored');
            assert.equal(button(core).props.ariaDisabled, false);
            assert.equal(button(core).props.class.split(' ').includes('is-loading'), false);
        }
    };
    // Explicit native-disabled host double. Enabled controls execute the REAL
    // ElButton handler. No forced disabled event is counted as an ordinary click.
    const click = async core => {
        if (button(core).props.disabled === true) {await flush(); return false;}
        button(core).props.onClick(new MouseEventDouble()); await flush(); return true;
    };
    const settle = async (request, value, reject = false) => {
        assert(request && !request.settled, 'only settle a live request');
        request.settled = true;
        request[reject ? 'reject' : 'resolve'](value);
        await flush();
    };
    t.after(async () => {
        // Settle all doubles even on red assertions, then remove genuine effects.
        for (const request of posts.filter(request => !request.settled))
            await settle(request, {response: {data: {detail: 'Synthetic cleanup'}}}, true);
        for (let index = 0; index < gets.length; index++)
            if (!gets[index].settled) await settle(gets[index], {data: cores()});
        vm.runInContext('statusPollers.forEach(poller => poller.stop())', context);
        app.unmount(); await flush();
        assert.equal(timers.size, 0);
        assert(posts.concat(gets).every(request => request.settled));
        assert.deepEqual(errors, expectedErrors, 'no unexpected application errors');
        assert.deepEqual(renderErrors, [], 'no swallowed render errors');
        assert.deepEqual(runtimeErrors, [], 'no swallowed pinned-runtime errors');
    });
    return {state, posts, gets, messages, button, click, pending, idle, mounted: () => mounted(),
        resolvePost: (index, data = {valid: true}) => settle(posts[index], {data}),
        rejectPost: (index, error) => settle(posts[index], error, true),
        resolveGet: (index, data = cores()) => settle(gets[index], {data}),
        rejectGet(index, error) {
            expectedErrors.push(['Failed to fetch core status', error]);
            return settle(gets[index], error, true);
        },
    };
}

for (const [first, other] of [['xray', 'sing-box'], ['sing-box', 'xray']]) {
    test('ordinary rendered buttons admit only ' + first + ' until POST and fresh status finish', async t => {
        const f = fixture(t); f.idle();
        assert.equal(await f.click(first), true); f.pending(first);
        for (const core of [first, other, first]) assert.equal(await f.click(core), false);
        assert.deepEqual(f.posts.map(request => request.url), [restartUrl(first)]);
        assert.equal(f.gets.length, 0); assert.deepEqual(f.messages, []);
        // Separate component-level dispatch control, NOT native usability proof.
        for (const core of [first, other]) {
            const event = new MouseEventDouble(); f.button(core).props.onClick(event); await flush();
            assert.equal(event.stopped, true, 'actual pinned handler stops disabled/loading emission');
        }
        assert.equal(f.posts.length, 1);
        await f.resolvePost(0); f.pending(first);
        assert.deepEqual(f.messages, [{kind: 'success', text: first + ' restarted'}]);
        assert.equal(f.gets.length, 1); assert.equal(f.gets[0].settled, false);
        assert.equal(await f.click(other), false); assert.equal(f.posts.length, 1);
        await f.resolveGet(0); f.idle();
        assert.equal(f.state.coreStatus.value[other].running, true);
        assert.equal(await f.click(other), true); f.pending(other);
        assert.deepEqual(f.posts.map(request => request.url), [restartUrl(first), restartUrl(other)]);
        await f.resolvePost(1); f.pending(other); await f.resolveGet(1); f.idle();
        assert.deepEqual(f.messages, [first, other].map(core => ({kind: 'success', text: core + ' restarted'})));
    });
    test('direct controller same-tick admission guard preserves ' + first + ' before DOM flush', async t => {
        const f = fixture(t);
        // Deliberate controller calls, not clicks on a disabled native element.
        const owner = f.state.restartCore(first);
        const duplicates = [f.state.restartCore(first), f.state.restartCore(other), f.state.restartCore(first)];
        assert.equal(f.posts.length, 1, 'guard must run synchronously before Vue flush');
        assert.equal(f.state.restartingCore.value, first);
        await Promise.all(duplicates); await flush(); f.pending(first);
        await f.resolvePost(0); f.pending(first);
        await f.state.restartCore(other); assert.equal(f.posts.length, 1, 'fresh read still owns admission');
        await f.resolveGet(0); await owner; f.idle();
        assert.equal(await f.click(other), true); f.pending(other);
        await f.rejectPost(1, {response: {data: {detail: 'Synthetic later failure'}}}); f.idle();
        assert.deepEqual(f.messages, [{kind: 'success', text: first + ' restarted'},
            {kind: 'error', text: 'Synthetic later failure'}]);
    });
}
for (const [label, data, expected] of [
    ['explicit validation message', {valid: false, validation_output: 'Synthetic invalid config'}, 'Synthetic invalid config'],
    ['fallback validation message', {valid: false}, 'xray config validation failed'],
]) {
    test(label + ' preserves existing notification and held status lifecycle', async t => {
        const f = fixture(t); await f.click('xray'); f.pending('xray');
        await f.resolvePost(0, data); f.pending('xray');
        assert.deepEqual(f.messages, [{kind: 'error', text: expected}]);
        assert.equal(f.gets.length, 1); assert.equal(await f.click('sing-box'), false);
        await f.resolveGet(0, cores(false)); f.idle();
        assert.equal(f.state.coreStatus.value.xray.running, false);
        assert.equal(await f.click('sing-box'), true); f.pending('sing-box');
        await f.resolvePost(1); await f.resolveGet(1); f.idle();
        assert.equal(f.posts.length, 2);
    });
}
for (const [label, error, expected] of [
    ['API detail', {response: {status: 409, data: {detail: 'Synthetic restart rejected'}}}, 'Synthetic restart rejected'],
    ['network fallback', new Error('Synthetic network rejection'), 'Failed to restart sing-box'],
]) {
    test(label + ' releases both buttons without inventing a status read or retry', async t => {
        const f = fixture(t); await f.click('sing-box'); f.pending('sing-box');
        await f.rejectPost(0, error); f.idle();
        assert.equal(f.gets.length, 0); assert.equal(f.posts.length, 1);
        assert.deepEqual(f.messages, [{kind: 'error', text: expected}]);
        assert.equal(await f.click('xray'), true); f.pending('xray');
        await f.resolvePost(1); f.pending('xray'); await f.resolveGet(0); f.idle();
        assert.equal(f.posts.length, 2);
    });
}
test('an older status read cannot release the restart before its queued fresh read', async t => {
    const f = fixture(t); const startup = f.mounted(); await flush();
    assert.equal(f.gets.length, 1);
    await f.click('xray'); f.pending('xray'); await f.resolvePost(0); f.pending('xray');
    assert.equal(f.gets.length, 1, 'fresh read is queued behind existing read');
    await f.resolveGet(0, cores(false)); await startup; f.pending('xray');
    assert.equal(f.gets.length, 2, 'one actual new status read must start');
    assert.equal(f.state.coreStatus.value.xray.running, false);
    assert.equal(await f.click('sing-box'), false); assert.equal(f.posts.length, 1);
    await f.resolveGet(1, cores(true)); f.idle();
    assert.equal(f.state.coreStatus.value.xray.running, true);
});
test('fresh status rejection preserves existing Unknown handling and releases admission', async t => {
    const f = fixture(t); await f.click('sing-box'); f.pending('sing-box');
    await f.resolvePost(0); f.pending('sing-box');
    await f.rejectGet(0, new Error('Synthetic status rejection')); f.idle();
    assert.equal(f.state.coreFieldKnown('sing-box', 'running'), false);
    assert.deepEqual(f.messages, [{kind: 'success', text: 'sing-box restarted'}]);
    assert.equal(f.posts.length, 1); assert.equal(f.gets.length, 1);
    assert.equal(await f.click('xray'), true); f.pending('xray');
    await f.resolvePost(1); await f.resolveGet(1); f.idle();
    assert.equal(f.state.coreFieldKnown('xray', 'running'), true);
});
