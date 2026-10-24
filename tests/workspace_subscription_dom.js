'use strict';
// Execute the real controller with deterministic DOM/fetch substitutes. This is
// not browser rendering, HTTP integration, or a real-core qualification.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const filename = process.argv[2] || path.join(__dirname, '../web/js/workspace.js');
const script = fs.readFileSync(filename, 'utf8');
const GRANTS = '/api/subscriptions';
const SNAPSHOT = '/api/routing/mihomo/snapshot';
const PREVIEW = '/api/routing/mihomo/preview';
const settings = {mode:'direct', direct_domains:[], proxy_domains:[], presets:{}, bypass_cgnat:false, intranet:[]};
const plan = {revision:'1'.repeat(64), sections:[], warnings:[]};
const labels = {
    node_identity_missing:'旧授权缺少节点身份，请重新选择节点并创建订阅',
    node_identity_changed:'节点已删除、重建或身份异常，请重新创建订阅',
    nodes_unavailable:'原节点已停用，恢复后可用',
    credentials_changed:'密码变更后失效',
};
const genericLabel = '授权失效，请重新创建订阅';
const lifecycle = [
    {name:'current', revoked:false, expired:false},
    {name:'revoked', revoked:true, expired:false},
    {name:'expired', revoked:false, expired:true},
    {name:'revoked and expired', revoked:true, expired:true},
];
const grant = (id = 1, changes = {}) => ({id, label:'synthetic grant ' + id, server:'node.example.test',
    inbound_ids:[7], formats:['mihomo.yaml'], expires_at:2000000000,
    revoked:false, expired:false, invalidated:false, invalidation_reason:null, ...changes});
const invalidGrant = (reason, changes = {}) => grant(1, {invalidated:true, invalidation_reason:reason, ...changes});
const response = (value, status = 200) => ({ok:status >= 200 && status < 300, status,
    json:async()=>structuredClone(value), headers:{get:key=>key === 'X-VUI-Node-Revision' ? 'a'.repeat(64) : key === 'ETag' ? '"' + '1'.repeat(64) + '"' : null}});
const deferred = () => {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return {promise, resolve, reject};
};
class Element {
    constructor(tag = 'div') {
        this.tagName = tag.toUpperCase(); this.value = ''; this.checked = false; this.disabled = false;
        this.hidden = false; this.dataset = {}; this.children = []; this.events = new Map(); this._text = '';
    }
    get textContent() { return this._text + this.children.map(node => node.textContent).join(''); }
    set textContent(value) { this._text = value === null ? '' : String(value); this.children = []; }
    set innerHTML(value) { assert.fail('Unsafe innerHTML sink: ' + value); }
    set outerHTML(value) { assert.fail('Unsafe outerHTML sink: ' + value); }
    insertAdjacentHTML() { assert.fail('Unsafe insertAdjacentHTML sink'); }
    addEventListener(type, fn) { this.events.set(type, [...(this.events.get(type) || []), fn]); }
    async emit(type, event = {preventDefault(){}}) { for (const fn of this.events.get(type) || []) await fn(event); }
    async click() {
        // Model browser activation: disabled buttons never dispatch click. This
        // deliberately does not call a disabled button's event listener directly.
        if (this.tagName === 'BUTTON' && this.disabled) return;
        await this.emit('click');
    }
    setAttribute(key, value) {
        if (key.startsWith('data-')) this.dataset[key.slice(5)] = String(value);
        else if (key === 'class') this.className = String(value);
        else this[key] = String(value);
    }
    append(...children) { for (const node of children) { node.parent = this; this.children.push(node); } }
    replaceChildren(...children) { this._text = ''; this.children = []; this.append(...children); }
    remove() { this.parent.children = this.parent.children.filter(node => node !== this); }
    querySelector(selector) { return descendants(this).find(node => matches(node, selector)) || null; }
}
function descendants(node) { return node.children.flatMap(child => [child, ...descendants(child)]); }
function matches(node, selector) {
    if (selector === 'button') return node.tagName === 'BUTTON';
    if (selector === '.zone') return node.className === 'zone';
    const attribute = /^\[data-([a-z]+)(?:="([^"]*)")?\]$/.exec(selector);
    return Boolean(attribute && Object.hasOwn(node.dataset, attribute[1]) &&
        (attribute[2] === undefined || node.dataset[attribute[1]] === attribute[2]));
}
async function fixture(initialGrants) {
    const nodes = new Map(), requests = [], confirms = [], queued = [];
    const buttons = ['reload','preview','save','add-zone','refresh-grants','create-grant','forget','copy','download'];
    const get = id => {
        if (!nodes.has(id)) nodes.set(id, new Element(buttons.includes(id) ? 'button' : 'div'));
        return nodes.get(id);
    };
    buttons.forEach(get); get('workspace').hidden = true; get('days').value = '30';
    const document = {getElementById:get, createElement:tag=>new Element(tag),
        createTextNode:text=>Object.assign(new Element('#text'), {textContent:text}),
        querySelectorAll:selector=>[...nodes.values()].flatMap(node=>[node, ...descendants(node)]).filter(node=>matches(node, selector))};
    const fetch = async (url, options = {}) => {
        const method = options.method || 'GET'; requests.push({url, method, options});
        assert.equal(options.credentials, 'same-origin'); assert.equal(options.cache, 'no-store');
        assert.equal(options.headers['X-VUI-Request'], '1');
        const index = queued.findIndex(item=>item.url === url && item.method === method);
        if (index >= 0) return queued.splice(index, 1)[0].reply(options);
        if (method === 'GET' && url === '/api/auth/me') return response({username:'synthetic'});
        if (method === 'GET' && url === '/api/routing/mihomo/catalog') return response({categories:[], presets:[]});
        if (method === 'GET' && url === SNAPSHOT) return response({revision:'1'.repeat(64), settings});
        if (method === 'GET' && url === PREVIEW) return response(plan);
        if (method === 'GET' && ['/api/inbounds','/api/subscription/mihomo-warnings'].includes(url)) return response([]);
        if (method === 'GET' && url === GRANTS) return response(initialGrants);
        const rotate = /^\/api\/subscriptions\/(\d+)\/rotate$/.exec(url);
        if (method === 'POST' && rotate) return response({id:Number(rotate[1]), paths:{'mihomo.yaml':'/sub/synthetic-token/mihomo.yaml'}});
        assert.fail('Unexpected request: ' + method + ' ' + url);
    };
    vm.runInNewContext(script, {document, fetch, location:{origin:'http://example.test'},
        addEventListener(){}, confirm:text=>{confirms.push(text);return true;},
        setTimeout:()=>assert.fail('Unexpected timer'), clearTimeout(){}}, {filename});
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(get('workspace').hidden, false, 'controller bootstrap completes');
    assert.equal(get('notice').className, '', 'bootstrap has no hidden controller error');
    const row = id => {
        const found = get('grants').children.find(item=>item.querySelector(`[data-rotate="${id}"]`));
        assert(found, 'grant row exists: ' + id); return found;
    };
    const rotate = id => row(id).querySelector('[data-rotate]');
    const revoke = id => row(id).querySelector('[data-revoke]');
    const status = id => row(id).children[2].textContent;
    const queue = (url, reply, method = 'GET') => queued.push({url, reply, method});
    const hold = (url, method = 'GET') => {
        const gate = deferred(), started = deferred();
        queue(url, options=>{started.resolve(options);return gate.promise;}, method);
        return {...gate, started:started.promise};
    };
    const assertDisabledClick = async id => {
        assert.equal(rotate(id).disabled, true);
        const count = requests.length, confirmations = confirms.length;
        await rotate(id).click();
        assert.equal(requests.length, count, 'disabled browser activation sends no request');
        assert.equal(confirms.length, confirmations, 'disabled activation does not show confirmation');
    };
    return {get, document, requests, confirms, row, rotate, revoke, status, queue, hold, assertDisabledClick,
        click:id=>get(id).click()};
}

for (const reason of ['node_identity_missing', 'node_identity_changed']) {
    for (const state of lifecycle) {
        test(`${reason} gives re-create guidance and disables rotation when ${state.name}`, async () => {
            const f = await fixture([invalidGrant(reason, state)]);
            assert.equal(f.status(1), labels[reason]); assert.doesNotMatch(f.status(1), /密码/);
            assert.match(f.rotate(1).title, /轮换不能恢复旧节点授权.*重新选择节点并创建订阅/);
            assert.equal(f.revoke(1).disabled, state.revoked);
            await f.assertDisabledClick(1);
            assert(f.requests.every(request=>request.method === 'GET'));
        });
    }
}
for (const state of lifecycle) {
    test(`credentials_changed retains password-specific status and permits rotation when ${state.name}`, async () => {
        const f = await fixture([invalidGrant('credentials_changed', state)]);
        assert.equal(f.status(1), state.revoked ? '已撤销' : state.expired ? '已过期' : labels.credentials_changed);
        assert.equal(f.rotate(1).disabled, false);
        await f.rotate(1).click();
        const mutations = f.requests.filter(request=>request.method !== 'GET');
        assert.equal(mutations.length, 1); assert.equal(mutations[0].url, GRANTS + '/1/rotate');
        assert.equal(mutations[0].method, 'POST'); assert.deepEqual(JSON.parse(mutations[0].options.body), {expires_days:30});
        assert.equal(f.confirms.length, 1); assert.equal(f.rotate(1).disabled, false);
        assert.equal(f.get('issued').hidden, false); assert.match(f.get('notice').textContent, /已轮换/);
    });
}
for (const state of lifecycle) {
    test(`nodes_unavailable disables rotation when ${state.name}`, async () => {
        const f = await fixture([invalidGrant('nodes_unavailable', state)]);
        assert.equal(f.status(1), state.revoked ? '已撤销' : state.expired ? '已过期' : labels.nodes_unavailable);
        assert.doesNotMatch(f.status(1), /密码/); await f.assertDisabledClick(1);
        assert(f.requests.every(request=>request.method === 'GET'));
    });
}
for (const reason of ['future_reason', 'credential_changed', null, undefined, '__proto__', 'constructor', 'toString', '<img src=x onerror=alert(1)>']) {
    test(`unknown invalidation reason ${String(reason)} gets generic, non-password guidance`, async () => {
        const f = await fixture([invalidGrant(reason)]);
        assert.equal(f.status(1), genericLabel); assert.doesNotMatch(f.status(1), /密码/);
        assert(f.requests.every(request=>request.method === 'GET'));
    });
}
test('an active valid grant remains valid and can rotate through the real controller', async () => {
    const f = await fixture([grant()]);
    assert.equal(f.status(1), '有效'); assert.equal(f.rotate(1).disabled, false);
    await f.rotate(1).click();
    const posts = f.requests.filter(request=>request.method === 'POST');
    assert.equal(posts.length, 1); assert.equal(posts[0].url, GRANTS + '/1/rotate');
    assert.deepEqual(JSON.parse(posts[0].options.body), {expires_days:30});
    assert.equal(f.confirms.length, 1); assert.equal(f.rotate(1).disabled, false);
    assert.equal(f.get('issued').hidden, false); assert.equal(f.get('download').disabled, false);
});
for (const reason of ['node_identity_missing', 'node_identity_changed', 'nodes_unavailable', 'credentials_changed']) {
    test(`refresh preserves newly rendered ${reason} rotation state after operation finally`, async () => {
        const f = await fixture([grant()]), oldRotate = f.rotate(1);
        const gate = f.hold(GRANTS), pending = f.click('refresh-grants'); await gate.started;
        assert.equal(oldRotate.disabled, true); assert.equal(f.get('refresh-grants').disabled, true);
        await f.assertDisabledClick(1);
        gate.resolve(response([invalidGrant(reason)])); await pending;
        assert.notEqual(f.rotate(1), oldRotate, 'refresh actually replaces the grant row');
        assert.equal(f.status(1), labels[reason]);
        assert.equal(f.rotate(1).disabled, reason !== 'credentials_changed');
        assert.equal(f.get('refresh-grants').disabled, false);
        assert.equal(f.rotate(1).dataset.disabled, undefined, 'no operation snapshot leaks into new rows');
        if (reason !== 'credentials_changed') await f.assertDisabledClick(1);
        assert(f.requests.every(request=>request.method === 'GET'));
    });
}
for (const outcome of ['success', 'http error', 'network error']) {
    test(`unrelated reload ${outcome} preserves each existing grant's disabled state in finally`, async () => {
        const values = [invalidGrant('node_identity_missing', {id:1}), invalidGrant('node_identity_changed', {id:2}),
            invalidGrant('nodes_unavailable', {id:3}), invalidGrant('credentials_changed', {id:4}), grant(5), grant(6, {revoked:true})];
        const f = await fixture(values), originalButtons = values.map(value=>f.rotate(value.id));
        const gate = f.hold(SNAPSHOT), pending = f.click('reload'); await gate.started;
        for (const value of values) await f.assertDisabledClick(value.id);
        if (outcome === 'success') {
            f.queue(PREVIEW, async()=>response({...plan, revision:'2'.repeat(64)}));
            gate.resolve(response({revision:'2'.repeat(64), settings}));
        }
        else if (outcome === 'http error') gate.resolve(response({detail:'synthetic read failure'}, 503));
        else gate.reject(new Error('synthetic read failure'));
        await pending;
        values.forEach((value, index) => {
            assert.equal(f.rotate(value.id), originalButtons[index], 'reload leaves grant rows in place');
            assert.equal(f.rotate(value.id).disabled, value.id <= 3);
            assert.equal(f.revoke(value.id).disabled, value.revoked);
            assert.equal(f.rotate(value.id).dataset.disabled, undefined);
        });
        for (const id of [1, 2, 3]) await f.assertDisabledClick(id);
        assert.equal(f.get('reload').disabled, false); assert.equal(f.get('save').disabled, true);
        assert.equal(f.get('notice').className, outcome === 'success' ? '' : 'error');
        assert(f.requests.every(request=>request.method === 'GET'));
    });
}
for (const failure of ['http', 'network']) {
    test(`failed grant refresh (${failure}) retains rows and their disabled status`, async () => {
        const f = await fixture([invalidGrant('node_identity_missing'), invalidGrant('node_identity_changed', {id:2}),
            invalidGrant('nodes_unavailable', {id:3}), grant(4)]);
        const originalRows = [1, 2, 3, 4].map(id=>f.row(id));
        f.queue(GRANTS, async()=>{
            if (failure === 'network') throw new Error('synthetic refresh failure');
            return response({detail:'synthetic refresh failure'}, 503);
        });
        await f.click('refresh-grants');
        [1, 2, 3, 4].forEach((id, index)=>assert.equal(f.row(id), originalRows[index]));
        for (const id of [1, 2, 3]) await f.assertDisabledClick(id);
        assert.equal(f.rotate(4).disabled, false); assert.equal(f.get('refresh-grants').disabled, false);
        assert.equal(f.get('notice').className, 'error'); assert.match(f.get('notice').textContent, /synthetic refresh failure/);
        assert(f.requests.every(request=>request.method === 'GET'));
    });
}
test('untrusted grant labels, server names, formats and unknown reasons remain literal text', async () => {
    const payload = '<img src=x onerror="globalThis.compromised=true">';
    const f = await fixture([invalidGrant(payload, {label:payload, server:'<script>compromised=true</script>', formats:[payload]})]);
    const name = f.row(1).children[0];
    assert.equal(name._text, payload); assert.equal(name.children.length, 1);
    assert.equal(name.children[0].tagName, 'SMALL'); assert.equal(name.children[0].textContent, '<script>compromised=true</script>');
    assert.equal(f.row(1).children[1].textContent, '1 节点 / ' + payload); assert.equal(f.status(1), genericLabel);
    assert.deepEqual(descendants(f.get('grants')).map(node=>node.tagName), ['TR','TD','SMALL','TD','TD','TD','TD','BUTTON','BUTTON']);
    assert(f.requests.every(request=>request.method === 'GET'));
});
