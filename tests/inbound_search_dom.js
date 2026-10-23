'use strict';
// Shipped controller/template and checksum-pinned Vue reactivity/compiler.
// Metadata, HTTP and renderer host are explicit doubles, not browser/API evidence.
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
// An optional controller filename supports bounded, local mutation controls.
const filename = process.argv[2] || path.join(root, 'web/js/app.js');
const source = fs.readFileSync(filename, 'utf8');
const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
const start = html.indexOf('<div v-if="currentView === \'inbounds\'">');
const end = html.indexOf('<div v-if="currentView === \'routing\'"');
assert(start >= 0 && end > start);
const render = V.compile(html.slice(start, end), {decodeEntities(value) {
    assert.doesNotMatch(value, /&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);/i);
    return value;
}});
const deferred = () => {
    let resolve, reject;
    const promise = new Promise((a, b) => {resolve = a; reject = b;});
    return {promise, resolve, reject};
};
const sample = () => [
    {id: 91, remark: 'Cloud ALPHA 北京', port: 10471, core: 'xray', protocol: 'vless', mutation_revision: 'a'.repeat(64)},
    {id: 72, remark: 'Literal [a-z].* Beta', port: 20472, core: 'sing-box', protocol: 'vmess', mutation_revision: 'b'.repeat(64)},
    {id: 38, remark: 'Gamma / Local', port: 30473, core: 'xray', protocol: 'trojan', mutation_revision: 'c'.repeat(64)},
];
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
const flush = async () => {for (let n = 0; n < 12; n++) await Promise.resolve(); await V.nextTick();};

function fixture(t) {
    let options, state, rows = sample();
    const reads = [], writes = [], messages = [], confirmations = [], renderErrors = [], held = [];
    const pending = new Map();
    const appStub = {component() {}, use() {}, mount() {}};
    const forbidden = label => {throw Error('Unexpected ' + label);};
    const location = new Proxy({hash: '#inbounds', replace() {}, assign() {forbidden('navigation');}}, {
        set() {forbidden('URL write');},
    });
    const context = vm.createContext({console, AbortController, location,
        document: {getElementById() {return null;}}, addEventListener() {},
        localStorage: new Proxy({}, {get() {forbidden('localStorage');}}),
        sessionStorage: new Proxy({}, {get() {forbidden('sessionStorage');}}),
        history: new Proxy({}, {get() {forbidden('history');}}),
        setTimeout() {forbidden('new timer');}, clearTimeout() {},
        Vue: {...V, createApp(value) {options = value; return appStub;}, onMounted() {}},
        ElementPlus: {ElMessage: Object.fromEntries(['error', 'warning', 'success'].map(kind =>
            [kind, message => messages.push({kind, message})])),
        ElMessageBox: {confirm(message) {confirmations.push(message); return Promise.reject('cancel');}}},
        ElementPlusIconsVue: {},
        axios: {defaults: {headers: {common: {}}}, interceptors: {response: {use() {}}},
            get(url) {
                reads.push(url);
                if (pending.has(url)) {const gate = pending.get(url); pending.delete(url); return gate.promise;}
                if (url === '/api/inbounds') return Promise.resolve({data: rows});
                if (url === '/api/certificates') return Promise.resolve({data: []});
                const match = url.match(/^\/api\/inbounds\/(\d+)\/editor$/);
                if (match) return Promise.resolve({data: rows.find(row => row.id === Number(match[1]))});
                forbidden('API read ' + url);
            },
            ...Object.fromEntries(['post', 'put', 'delete', 'patch'].map(method => [method, (...args) => {
                writes.push({method, args}); forbidden('API write');
            }])),
        },
    });
    vm.runInContext(source, context, {filename});
    const host = renderer();
    const app = host.createApp({setup() {state = options.setup(); return state;}, render});
    app.config.errorHandler = error => renderErrors.push(error);
    app.component('el-input', {props: ['modelValue'], emits: ['update:modelValue'],
        setup(props, {attrs, emit}) {return () => V.h('input', {...attrs, value: props.modelValue,
            onInput: event => emit('update:modelValue', event.target.value)});}});
    for (const name of ['el-button', 'el-tag']) {
        app.component(name, {setup(props, {attrs, slots}) {
            return () => V.h(name === 'el-button' ? 'button' : 'span', attrs, slots.default?.());
        }});
    }
    app.component('el-table-column', {render() {return null;}});
    app.component('el-table', {props: ['data'], setup(props, {slots}) {
        return () => {
            const columns = slots.default();
            return V.h('table', {rows: props.data}, props.data.map(row => V.h('tr', {row},
                columns.map(column => V.h('td', {}, column.children?.default
                    ? column.children.default({row}) : String(row[column.props.prop] ?? ''))))));
        };
    }});
    app.mount(host.host);
    const find = predicate => {
        const found = walk(host.host).filter(predicate);
        assert.equal(found.length, 1, 'one matching rendered element'); return found[0];
    };
    const input = () => find(node => node.props.id === 'inbound-search');
    const button = label => find(node => node.type === 'button' && text(node) === label);
    t.after(async () => {
        state.closeQrDialog(); state.closeUpstreamExport(); state.closeInboundEditor();
        app.unmount(); await flush();
        assert.deepEqual(renderErrors, [], 'no swallowed render error');
        assert.deepEqual(writes, [], 'this read-only fixture never mutates backend state');
        assert(held.every(gate => gate.settled), 'all held reads must settle');
    });
    return {state, reads, writes, messages, confirmations, context, host, find, button,
        async load(next = rows) {rows = next; await state.fetchInbounds(); await flush();},
        hold(url = '/api/inbounds') {
            const gate = deferred(), result = {...gate, settled: false};
            result.resolve = value => {result.settled = true; gate.resolve(value);};
            pending.set(url, result); held.push(result); return result;
        },
        async search(value) {input().props.onInput({target: {value}}); await flush();},
        ids() {return Array.from(state.filteredInbounds.value, row => row.id);},
        table() {return find(node => node.type === 'table');},
        count() {return find(node => node.props.id === 'inbound-search-count');},
        clear() {return button('清除');},
    };
}

test('empty and whitespace searches return the unchanged source array, original order and row identities', async t => {
    const f = fixture(t);
    const rows = sample().map(Object.freeze); Object.freeze(rows);
    await f.load(rows);
    const sourceRows = f.state.inbounds.value;
    for (const query of ['', ' ', '\t\n', '　']) {
        await f.search(query);
        assert.equal(f.state.filteredInbounds.value, sourceRows);
        assert.equal(f.table().props.rows, sourceRows);
        assert.deepEqual(f.ids(), [91, 72, 38]);
        f.state.filteredInbounds.value.forEach((row, index) => assert.equal(row, sourceRows[index]));
    }
});

test('visible metadata uses case-insensitive literal substrings, Chinese and exact regex-like punctuation', async t => {
    const f = fixture(t); await f.load();
    for (const [query, expected] of [
        ['alpha', [91]], ['  ALPHA  ', [91]], ['北京', [91]], ['91', [91]],
        ['20472', [72]], ['SING-BOX', [72]], ['VMESS', [72]], ['XRAY', [91, 38]],
        ['[a-z].*', [72]], ['.*', [72]], ['[', [72]], ['^.*$', []], ['(', []], ['no matches', []],
    ]) {
        await f.search(query); assert.deepEqual(f.ids(), expected, query);
        for (const row of f.state.filteredInbounds.value) assert(f.state.inbounds.value.includes(row));
    }
    assert.deepEqual(f.reads, ['/api/inbounds'], 'typing never fetches');
});

test('hidden fields are not searched or inspected, and objects or booleans are not coerced', async t => {
    const f = fixture(t), row = sample()[0];
    for (const name of ['uuid', 'password', 'settings', 'stream_settings', 'config', 'profile',
        'credentials', 'instance_id', 'mutation_revision', 'revision'])
        Object.defineProperty(row, name, {get() {throw Error('Hidden read: ' + name);}, enumerable: true});
    // The existing table reads routing status, but search must not match it or revision.
    row.routing_mode = 'relay'; row.routing_status = {state: 'pending', policy_identity: 'hidden-policy'};
    await f.load([row]);
    for (const query of ['a'.repeat(64), 'hidden-policy', 'relay', 'pending', 'synthetic-secret', 'has_uuid']) {
        await f.search(query); assert.deepEqual(f.ids(), [], query);
    }
    // Test values outside the displayed scalar contract through the computed
    // view directly, not by letting presentation stringify a hostile object.
    f.state.currentView.value = 'dashboard'; await flush();
    f.state.inbounds.value = [{id: 8, remark: {toString() {throw Error('Object coercion');}},
        port: null, core: false, protocol: ['not-a-visible-string']}];
    for (const query of ['object', 'false', 'null', 'undefined', 'not-a-visible-string']) {
        f.state.inboundSearch.value = query; assert.deepEqual(f.ids(), []);
    }
    f.state.inboundSearch.value = '8'; assert.deepEqual(f.ids(), [8]);
});

test('reactive source edits, additions and removals update the computed view without cloned rows', async t => {
    const f = fixture(t); await f.load(); await f.search('alpha');
    const rows = f.state.inbounds.value, beta = rows[1];
    beta.remark = 'ALPHA added'; await flush();
    assert.deepEqual(f.ids(), [91, 72]); assert.equal(f.state.filteredInbounds.value[1], beta);
    rows.push({id: 4, remark: 'alpha new', core: 'xray', protocol: 'vless', port: 4444}); await flush();
    assert.deepEqual(f.ids(), [91, 72, 4]);
    rows.splice(0, 1); await flush(); assert.deepEqual(f.ids(), [72, 4]);
    assert.equal(f.state.inbounds.value, rows);
    assert.equal(f.state.filteredInbounds.value[0], beta);
    assert.equal(text(f.count()), '显示 2 / 3 个节点');
});

test('rendered label, live count, model binding and keyboard-capable clear use the actual template', async t => {
    const f = fixture(t); await f.load();
    assert.equal(text(f.find(node => node.type === 'label' && node.props.for === 'inbound-search')), '搜索节点');
    assert.equal(f.find(node => node.props.id === 'inbound-search').props['aria-describedby'], 'inbound-search-count');
    assert.equal(f.find(node => node.props.id === 'inbound-search').props.autocomplete, 'off');
    assert.equal(f.count().props.role, 'status'); assert.equal(f.count().props['aria-live'], 'polite');
    assert.equal(f.count().props['aria-atomic'], 'true');
    assert.equal(f.clear().props.disabled, true); assert.equal(f.clear().props['aria-label'], '清除节点搜索');
    await f.search('beta'); assert.equal(text(f.count()), '显示 1 / 3 个节点');
    assert.equal(f.table().children.length, 1); assert.equal(f.table().children[0].props.row, f.state.inbounds.value[1]);
    assert.equal(f.clear().props.disabled, false);
    f.clear().props.onClick(); await flush();
    assert.equal(f.state.inboundSearch.value, ''); assert.equal(f.clear().props.disabled, true);
    assert.equal(text(f.count()), '显示 3 / 3 个节点'); assert.equal(f.table().props.rows, f.state.inbounds.value);
});

test('refresh retains the exact query and current generation, never applying a late stale list', async t => {
    const f = fixture(t); await f.load(); await f.search(' ALPHA ');
    const oldRows = f.state.inbounds.value, held = f.hold(), stale = f.state.fetchInbounds();
    const replacement = sample(); replacement[1].remark = 'alpha replacement';
    await f.load(replacement);
    assert.equal(f.state.inboundSearch.value, ' ALPHA '); assert.deepEqual(f.ids(), [91, 72]);
    const currentRows = f.state.inbounds.value; assert.notEqual(currentRows, oldRows);
    held.resolve({data: sample()}); await stale; await flush();
    assert.equal(f.state.inbounds.value, currentRows); assert.deepEqual(f.ids(), [91, 72]);
    assert.equal(f.state.filteredInbounds.value[1], currentRows[1]);
});

test('filtered row edit, export, share and cancelled delete keep the selected original identity', async t => {
    const f = fixture(t); await f.load(); await f.search('beta');
    const selected = f.state.inbounds.value[1];
    assert.equal(f.table().children[0].props.row, selected);
    await f.button('编辑').props.onClick(); await flush();
    assert.equal(f.state.editingInboundId.value, selected.id);
    assert.equal(f.state.newInbound.remark, selected.remark);
    assert.deepEqual(f.reads, ['/api/inbounds', '/api/inbounds/72/editor', '/api/certificates']);
    f.state.closeInboundEditor();
    f.button('导出为中转上游（仅 TCP）').props.onClick();
    assert.equal(f.state.upstreamExport.inboundId, selected.id); assert.equal(f.state.upstreamExport.name, selected.remark);
    f.state.closeUpstreamExport();
    const held = f.hold('/api/subscription/link/72'), sharing = f.button('分享').props.onClick();
    assert.equal(f.reads.at(-1), '/api/subscription/link/72');
    f.state.closeQrDialog(); held.resolve({data: 'synthetic-late-link'}); await sharing;
    assert.equal(f.state.qrLink.value, '');
    await f.button('删除').props.onClick();
    assert.deepEqual(f.confirmations, ['Delete Literal [a-z].* Beta:20472?']);
    assert.equal(f.state.inbounds.value[1], selected); assert.deepEqual(f.writes, []);
});

test('typing and clearing have no I/O or persistence; navigation retains and fresh setup resets query', async t => {
    const f = fixture(t); await f.load();
    for (const value of ['北京', 'beta', '[a-z].*', '', 'beta']) await f.search(value);
    assert.deepEqual(f.reads, ['/api/inbounds']); assert.deepEqual(f.writes, []);
    f.state.handleSelect('dashboard'); await flush();
    f.state.handleSelect('inbounds'); await flush();
    assert.equal(f.state.inboundSearch.value, 'beta'); assert.deepEqual(f.ids(), [72]);
    assert.deepEqual(f.reads, ['/api/inbounds', '/api/inbounds']);
    assert.equal(f.context.location.hash, '#inbounds');
    const fresh = fixture(t); await fresh.load();
    assert.equal(fresh.state.inboundSearch.value, ''); assert.deepEqual(fresh.ids(), [91, 72, 38]);
});

test('session cleanup clears source and filtered rows and invalidates an in-flight refresh', async t => {
    const f = fixture(t); await f.load(); await f.search('beta');
    const held = f.hold(), loading = f.state.fetchInbounds();
    vm.runInContext('endAdminSession()', f.context); await flush();
    assert.deepEqual(f.ids(), []); assert.equal(text(f.count()), '显示 0 / 0 个节点');
    held.resolve({data: sample()}); await loading; await flush();
    assert.deepEqual(f.ids(), []); assert.equal(f.state.inbounds.value.length, 0);
});

test('empty inventory and no match counts remain distinct, with clear restoring the original list', async t => {
    const f = fixture(t); await f.load([]); await f.search('none');
    assert.equal(text(f.count()), '显示 0 / 0 个节点');
    await f.load(); // Refresh of the intentionally empty synthetic inventory.
    assert.equal(f.state.inboundSearch.value, 'none'); assert.equal(text(f.count()), '显示 0 / 0 个节点');
    await f.load(sample()); assert.equal(text(f.count()), '显示 0 / 3 个节点');
    f.clear().props.onClick(); await flush();
    assert.equal(text(f.count()), '显示 3 / 3 个节点'); assert.equal(f.state.filteredInbounds.value, f.state.inbounds.value);
});
