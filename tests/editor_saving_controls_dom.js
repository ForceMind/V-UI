'use strict';
// Real pinned Vue compiler/reactivity and Element Plus form/disabled helper;
// controls and I/O are simulated. Not Chromium, HTTP, database or core evidence.
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
const runtime = vm.createContext({console, setTimeout, clearTimeout, setInterval, clearInterval});
for (const name of ['vue', 'element-plus']) {
    for (const [key, value] of Object.entries(pins[name]))
        assert.deepEqual(provenance[name][key], value, name + ' provenance ' + key);
    const filename = name === 'vue' ? 'vue.js' : 'element-plus.js';
    const bytes = fs.readFileSync(path.join(assets, filename));
    assert.equal(createHash('sha256').update(bytes).digest('hex'),
        provenance[name].selected_sha256[filename], filename + ' checksum');
    vm.runInContext(bytes.toString(), runtime, {filename});
}
const V = runtime.Vue, E = runtime.ElementPlus;
assert.equal(V.version, pins.vue.version);
assert.equal(E.version, pins['element-plus'].version);
const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
const source = fs.readFileSync(path.join(root, 'web/js/app.js'), 'utf8');
const fields = ['core', 'protocol', 'profile.transport', 'profile.security', 'profile.flow', 'profile.client_fingerprint'];
const dialogStart = html.indexOf('<el-dialog v-model="showAddInbound"');
assert(dialogStart >= 0, 'shipped node editor must exist');
const dialog = html.slice(dialogStart, html.indexOf('<template #footer>', dialogStart));
const form = dialog.match(/<el-form\b[^>]*>/)[0];
// Compile the exact shipped bindings. Only add an identifying fixture prop and
// omit option contents/visibility: eligibility, not widget visibility, is tested.
const controls = fields.map(field => {
    const matches = [...dialog.matchAll(/<(el-radio-group|el-select)\b[^>]*>/g)]
        .filter(match => match[0].includes(`v-model="newInbound.${field}"`));
    assert.equal(matches.length, 1, field + ' has one shipped control');
    const [tag, type] = matches[0];
    assert.match(tag, /:disabled="[^"]+"/, field + ' has an explicit disabled binding');
    return tag.slice(0, -1) + ` field="${field}"></${type}>`;
});
// No browser document is needed to decode these entity-free bindings. Reject
// future entities rather than silently interpreting a different expression.
const render = V.compile(form + controls.join('\n') + '</el-form>', {decodeEntities(value) {
    assert.doesNotMatch(value, /&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);/i);
    return value;
}});
const clone = value => JSON.parse(JSON.stringify(value));
const deferred = () => {
    let resolve, reject;
    const promise = new Promise((a, b) => { resolve = a; reject = b; });
    return {promise, resolve, reject};
};

// Minimal host for Vue's renderer, not an emulated browser or an Element Plus
// replacement. The actual ElForm provides the context consumed by useFormDisabled.
function renderer() {
    const node = type => ({type, children: [], parent: null, props: {}});
    const remove = item => {
        if (item.parent) item.parent.children.splice(item.parent.children.indexOf(item), 1);
        item.parent = null;
    };
    return {host: node('root'), ...V.createRenderer({
        createElement: node, createText: node, createComment: node,
        insert(item, parent, anchor = null) {
            remove(item); item.parent = parent;
            parent.children.splice(anchor ? parent.children.indexOf(anchor) : parent.children.length, 0, item);
        },
        remove, setText(item, text) { item.text = text; },
        setElementText(item, text) { item.text = text; },
        patchProp(item, key, previous, value) { item.props[key] = value; },
        parentNode: item => item.parent,
        nextSibling: item => item.parent?.children[item.parent.children.indexOf(item) + 1] || null,
    })};
}

function probeControl(records) {
    return {
        props: {field: String, modelValue: {}, disabled: {type: Boolean, default: undefined}},
        emits: ['update:modelValue', 'change'],
        setup(props, {emit}) {
            const disabled = E.useFormDisabled();
            records.set(props.field, {
                get disabled() { return disabled.value; },
                choose(value) {
                    if (disabled.value) return false;
                    emit('update:modelValue', value); emit('change', value); return true;
                },
            });
            return () => V.h('control', {disabled: disabled.value, value: props.modelValue});
        },
    };
}

function fixture(t) {
    let options, state;
    const records = new Map(), requests = [], messages = [], errors = [], gate = deferred();
    const stored = {id: 7, mutation_revision: 'a'.repeat(64), core: 'xray', protocol: 'vless', remark: 'synthetic-editor',
        port: 10443, enable: false, routing_mode: 'inherit', certificate_id: null,
        credentials: {has_uuid: true}, profile: {security: 'reality', transport: 'raw',
            flow: '', client_fingerprint: 'chrome', reality_target: 'reference.example.test:443',
            reality_server_name: 'reference.example.test'}};
    const write = (method, url, body) => {
        requests.push({method, url, body: clone(body)}); return gate.promise;
    };
    const appStub = {component() {}, use() {}, mount() {}};
    const context = vm.createContext({
        console: {...console, error: (...args) => errors.push(args)},
        document: {getElementById() { return null; }}, addEventListener() {},
        location: {hash: '#inbounds'},
        // Suppress unrelated startup polling only; retain genuine refs, watchers,
        // computed values, setup scope and nextTick from the pinned Vue runtime.
        Vue: {...V, createApp(value) { options = value; return appStub; }, onMounted() {}},
        ElementPlus: {ElMessage: Object.fromEntries(['success', 'error', 'warning'].map(
            kind => [kind, text => messages.push({kind, text})])), ElMessageBox: {}},
        ElementPlusIconsVue: {},
        axios: {defaults: {headers: {common: {}}}, interceptors: {response: {use() {}}},
            async get(url) {
                assert(['/api/inbounds/7/editor', '/api/certificates', '/api/inbounds', '/api/cores/status'].includes(url), url);
                return {data: url.endsWith('/editor') ? clone(stored) : []};
            },
            post: (url, body) => write('POST', url, body),
            put: (url, body) => write('PUT', url, body)},
    });
    vm.runInContext(source, context, {filename: 'web/js/app.js'});
    const host = renderer(), Probe = probeControl(records);
    const app = host.createApp({setup() { state = options.setup(); return state; }, render});
    app.component('el-form', E.ElForm);
    app.component('el-select', Probe); app.component('el-radio-group', Probe);
    app.mount(host.host);
    t.after(async () => {
        gate.resolve({data: {saved: true}}); await V.nextTick(); app.unmount();
        assert.deepEqual(errors, [], 'no unexpected controller or Vue errors');
    });
    assert.equal(records.size, 6);
    const disabled = () => fields.map(field => records.get(field).disabled);
    const choose = async (field, value) => {
        const accepted = records.get(field).choose(value); await V.nextTick(); return accepted;
    };
    return {state, records, requests, messages, gate, disabled, choose};
}

test('pinned Element Plus explicit false overrides a disabled form', {timeout: 5000}, t => {
    const records = new Map(), Probe = probeControl(records), host = renderer();
    const app = host.createApp({render: () => V.h(E.ElForm, {disabled: true}, {
        default: () => [V.h(Probe, {field: 'inherited'}),
            V.h(Probe, {field: 'false', disabled: false}), V.h(Probe, {field: 'true', disabled: true})],
    })});
    app.mount(host.host); t.after(() => app.unmount());
    assert.equal(records.get('inherited').disabled, true);
    assert.equal(records.get('false').disabled, false);
    assert.equal(records.get('true').disabled, true);
});

test('all six shipped bindings preserve idle eligibility and lock only while saving', {timeout: 5000}, async t => {
    const f = fixture(t), s = f.state;
    // Ordered as core, protocol, transport, security, flow, fingerprint.
    const cases = [
        [null, 'xray', 'vless', 'none', 'raw', [false, false, false, false, false, false]],
        [7, 'xray', 'vless', 'reality', 'raw', [true, true, false, false, false, false]],
        [null, 'sing-box', 'vless', 'tls', 'direct', [false, false, false, false, false, true]],
        [7, 'sing-box', 'vless', 'reality', 'direct', [true, true, true, false, true, true]],
        [7, 'sing-box', 'vless', 'reality', 'ws', [true, true, false, false, false, false]],
        [7, 'sing-box', 'trojan', 'tls', 'direct', [true, true, false, true, false, true]],
        [null, 'sing-box', 'hysteria2', 'tls', 'quic', [false, false, false, true, false, false]],
        [7, 'sing-box', 'tuic', 'tls', 'quic', [true, true, false, true, false, false]],
    ];
    for (const [id, core, protocol, security, transport, expected] of cases) {
        s.editingInboundId.value = id;
        Object.assign(s.newInbound, {core, protocol});
        Object.assign(s.newInbound.profile, {security, transport});
        await V.nextTick(); assert.deepEqual(f.disabled(), expected, `${core}/${protocol}/${security}/${transport} idle`);
        s.savingInbound.value = true; await V.nextTick();
        for (const field of fields) assert.equal(f.records.get(field).disabled, true, field + ' must lock while saving');
        s.savingInbound.value = false; await V.nextTick();
        assert.deepEqual(f.disabled(), expected, 'settled state must restore exact eligibility');
    }
});

for (const editing of [false, true]) for (const outcome of ['success', 'rejection']) {
    test(`${editing ? 'edit PUT' : 'create POST'} blocks new choices until ${outcome} settles`, {timeout: 5000}, async t => {
        const f = fixture(t), s = f.state;
        if (editing) await s.openEditInbound({id: 7, mutation_revision: 'a'.repeat(64)});
        else {
            await s.openAddInbound();
            s.newInbound.profile.security = 'reality'; s.onSecurityChanged();
        }
        s.newInbound.remark = 'synthetic-unsaved-draft';
        s.newInbound.enable = false;
        await V.nextTick();
        const eligible = [editing, editing, false, false, false, false];
        assert.deepEqual(f.disabled(), eligible);
        assert.equal(await f.choose('profile.client_fingerprint', 'firefox'), true, 'idle choice works');
        const draft = clone(s.newInbound);
        const pending = s.saveInbound(); await V.nextTick();
        assert.equal(s.savingInbound.value, true); assert.equal(s.showAddInbound.value, true);
        assert.equal(f.requests.length, 1);
        assert.equal(f.requests[0].method, editing ? 'PUT' : 'POST');
        assert.equal(f.requests[0].url, editing ? '/api/inbounds/7' : '/api/inbounds');
        assert.equal(f.requests[0].body.profile.security, 'reality');
        assert.equal(f.requests[0].body.profile.client_fingerprint, 'firefox');
        const submitted = clone(f.requests[0].body);
        const alternatives = ['sing-box', 'vmess', 'websocket', 'none', 'xtls-rprx-vision', 'safari'];
        for (const [index, field] of fields.entries()) {
            assert.equal(f.records.get(field).disabled, true, field + ' locked during deferred save');
            assert.equal(await f.choose(field, alternatives[index]), false, field + ' cannot accept a later choice');
        }
        assert.deepEqual(clone(s.newInbound), draft, 'pending choices cannot change the draft');
        await s.saveInbound(); assert.equal(f.requests.length, 1, 'duplicate save does not write');
        assert.equal(f.messages.length, 0, 'no premature success or failure');
        if (outcome === 'success') f.gate.resolve({data: {saved: true}});
        else f.gate.reject({response: {status: 422, data: {detail: 'synthetic rejection'}}});
        await pending; await V.nextTick();
        assert.equal(s.savingInbound.value, false);
        assert.deepEqual(f.requests[0].body, submitted, 'only the original request was submitted');
        assert.equal(f.requests.length, 1);
        if (outcome === 'success') {
            assert.equal(s.showAddInbound.value, false);
            assert.equal(f.messages.at(-1).kind, 'success');
            await s.openAddInbound(); await V.nextTick();
            assert.deepEqual(f.disabled(), [false, false, false, false, false, false], 'fresh editor unlocks');
        } else {
            assert.equal(s.showAddInbound.value, true);
            assert.deepEqual(clone(s.newInbound), draft, 'rejection preserves the original draft');
            assert.equal(f.messages.at(-1).kind, 'error');
            assert.match(f.messages.at(-1).text, /synthetic rejection/);
            assert.deepEqual(f.disabled(), eligible, 'rejection restores original eligibility');
        }
        assert.equal(await f.choose('profile.security', 'tls'), true, 'settled editor accepts edits again');
        assert.equal(s.newInbound.profile.security, 'tls');
    });
}
