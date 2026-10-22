'use strict';
// Execute the unmodified browser controller with deterministic DOM/fetch substitutes.
// These controls do not launch a browser, contact a server, or qualify real rendering.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const filename = process.argv[2] || path.join(__dirname, '../web/js/workspace.js');
const script = fs.readFileSync(filename, 'utf8');
const SNAPSHOT = '/api/routing/mihomo/snapshot';
const PREVIEW = '/api/routing/mihomo/preview';
const ROUTING = '/api/routing/mihomo';
const original = {mode:'direct', direct_domains:['direct.example.test'], proxy_domains:['saved.example.test'],
    presets:{openai:true}, bypass_cgnat:false, intranet:[{suffix:'corp.example.test', nameservers:['system']}]};
const replacement = {...original, mode:'standard', proxy_domains:['server.example.test'], presets:{openai:false}};
const plan = name => ({sections:[{comment:name, rules:['DOMAIN-SUFFIX,' + name + ',DIRECT']}], warnings:[]});
const response = (value, status = 200, etag = 'r3') => ({ok:status >= 200 && status < 300, status,
    json:async()=>structuredClone(value), headers:{get:key=>key === 'ETag' ? '"' + etag + '"' : null}});
const deferred = () => {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return {promise, resolve, reject};
};
class Element {
    constructor(tag = 'div') {
        this.tagName = tag.toUpperCase(); this.value = ''; this.checked = false; this.disabled = false;
        this.hidden = false; this.dataset = {}; this.children = []; this.events = new Map(); this.textContent = '';
    }
    addEventListener(type, fn) { this.events.set(type, [...(this.events.get(type) || []), fn]); }
    async emit(type, event = {preventDefault(){}}) { for (const fn of this.events.get(type) || []) await fn(event); }
    setAttribute(key, value) {
        if (key.startsWith('data-')) this.dataset[key.slice(5)] = value;
        else this[key] = value;
    }
    append(...children) { for (const node of children) { node.parent = this; this.children.push(node); } }
    replaceChildren(...children) { this.children = []; this.append(...children); }
    remove() { this.parent.children = this.parent.children.filter(node => node !== this); }
    querySelector(selector) { return descendants(this).find(node => matches(node, selector)); }
}
function descendants(node) { return node.children.flatMap(child => [child, ...descendants(child)]); }
function matches(node, selector) {
    if (selector === 'button') return node.tagName === 'BUTTON';
    if (selector === '.zone') return node.class === 'zone';
    if (/^\[data-[a-z]+\]$/.test(selector)) return selector.slice(6, -1) in node.dataset;
    return false;
}
async function fixture() {
    const nodes = new Map(), globalEvents = new Map(), requests = [], confirms = [], queued = [];
    const buttons = ['reload','preview','save','add-zone','refresh-grants','create-grant','forget','copy','download'];
    const get = id => { if (!nodes.has(id)) nodes.set(id, new Element(buttons.includes(id) ? 'button' : 'div')); return nodes.get(id); };
    buttons.forEach(get); get('workspace').hidden = true;
    const document = {getElementById:get, createElement:tag=>new Element(tag), createTextNode:text=>Object.assign(new Element('#text'), {textContent:text}),
        querySelectorAll:selector=>[...nodes.values()].flatMap(node => [node, ...descendants(node)]).filter(node => matches(node, selector))};
    let confirmed = true;
    const fetch = async (url, options = {}) => {
        requests.push({url, options, method:options.method || 'GET'});
        assert.equal(options.credentials, 'same-origin'); assert.equal(options.cache, 'no-store');
        assert.equal(options.headers['X-VUI-Request'], '1');
        const index = queued.findIndex(item => item.url === url && item.method === (options.method || 'GET'));
        if (index >= 0) return queued.splice(index, 1)[0].reply(options);
        if (url === SNAPSHOT) return response({revision:'r1', settings:original});
        if (url === PREVIEW) return response(plan(options.method === 'POST' ? 'draft' : 'saved'));
        if (url === ROUTING && options.method === 'PUT') return response(JSON.parse(options.body));
        if (url === '/api/auth/me') return response({username:'synthetic'});
        if (url === '/api/routing/mihomo/catalog') return response({categories:[{id:'service', name_zh:'服务'}], presets:[{id:'openai', category:'service', name_zh:'OpenAI'}]});
        if (['/api/inbounds','/api/subscription/mihomo-warnings','/api/subscriptions'].includes(url)) return response([]);
        assert.fail('Unexpected request: ' + url);
    };
    vm.runInNewContext(script, {document, fetch, location:{origin:'http://example.test'},
        addEventListener:(type, fn)=>globalEvents.set(type, [...(globalEvents.get(type) || []), fn]),
        confirm:text=>{confirms.push(text);return confirmed;}, setTimeout, clearTimeout}, {filename});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(get('workspace').hidden, false, 'controller bootstrap completes');
    assert.equal(get('save').disabled, true);
    const queue = (url, reply, method = 'GET') => queued.push({url, reply, method});
    const hold = (url, method = 'GET') => {
        const gate = deferred(), started = deferred();
        queue(url, options=>{started.resolve(options);return gate.promise;}, method);
        return {...gate, started:started.promise};
    };
    const edit = async (value, event = 'input') => {get('proxy').value = value;await get('routing-form').emit(event);};
    const ui = () => Object.fromEntries(['proxy','save-state','preview-label','rule-preview','rule-count','warnings'].map(id=>[id,
        id === 'proxy' ? get(id).value : get(id).textContent]).concat([['save-disabled', get('save').disabled]]));
    const assertDirty = value => {
        assert.equal(get('proxy').value, value); assert.match(get('save-state').textContent, /未保存草稿/);
        assert.equal(get('save').disabled, false); assert.match(get('preview-label').textContent, /重新生成/);
        const event = {prevented:false, preventDefault(){this.prevented = true;}};
        for (const fn of globalEvents.get('beforeunload') || []) fn(event);
        assert.equal(event.prevented, true, 'unsaved draft retains navigation protection');
    };
    const assertRevision = async expected => {
        await get('save').emit('click');
        const put = requests.filter(r=>r.method === 'PUT').at(-1);
        assert.equal(put.options.headers['If-Match'], '"' + expected + '"');
    };
    return {get, document, requests, confirms, queue, hold, edit, ui, assertDirty, assertRevision,
        confirm:value=>{confirmed = value;}, click:id=>get(id).emit('click')};
}
for (const dirtyStart of [false, true]) {
    test(`reload keeps newer input while snapshot is pending; initial dirty=${dirtyStart}`, async () => {
        const f = await fixture(); if (dirtyStart) await f.edit('discard-approved.example.test');
        const gate = f.hold(SNAPSHOT), pending = f.click('reload'); await gate.started;
        assert.equal(f.get('reload').disabled, true); assert.equal(f.get('proxy').disabled, false);
        await f.edit('new-unsaved.example.test');
        gate.resolve(response({revision:'r2', settings:replacement})); await pending;
        f.assertDirty('new-unsaved.example.test'); assert.equal(f.confirms.length, dirtyStart ? 1 : 0);
        assert.match(f.get('notice').textContent, /保留/);
        assert.equal(f.requests.filter(r=>r.url === PREVIEW).length, 1, 'stale reload does not request a preview');
        assert(f.requests.every(r=>r.method === 'GET')); await f.assertRevision('r1');
    });
}
test('reload keeps newer input and old revision while preview is pending', async () => {
    const f = await fixture(); await f.edit('discard-approved.example.test'); const before = f.ui();
    f.queue(SNAPSHOT, async()=>response({revision:'r2', settings:replacement}));
    const gate = f.hold(PREVIEW), pending = f.click('reload'); await gate.started;
    assert.equal(f.get('proxy').value, before.proxy, 'snapshot alone must not commit the discarded form');
    assert.equal(f.get('rule-preview').textContent, before['rule-preview']);
    await f.edit('later-preview.example.test', 'change'); gate.resolve(response(plan('stale'))); await pending;
    f.assertDirty('later-preview.example.test'); assert.equal(f.get('rule-preview').textContent, before['rule-preview']);
    assert.match(f.get('notice').textContent, /保留/); await f.assertRevision('r1');
});
test('editing then reverting during reload still invalidates that reload attempt', async () => {
    const f = await fixture(); const gate = f.hold(SNAPSHOT), pending = f.click('reload'); await gate.started;
    await f.edit('temporary.example.test'); await f.edit(original.proxy_domains[0]);
    gate.resolve(response({revision:'r2', settings:replacement})); await pending;
    assert.equal(f.get('proxy').value, original.proxy_domains[0]); assert.equal(f.get('save').disabled, true);
    assert.match(f.get('notice').textContent, /保留/); await f.edit('next.example.test'); await f.assertRevision('r1');
});
for (const dirtyStart of [false, true]) {
    test(`unchanged-form reload adopts snapshot, revision and preview; initial dirty=${dirtyStart}`, async () => {
        const f = await fixture(); if (dirtyStart) await f.edit('discard-approved.example.test');
        f.queue(SNAPSHOT, async()=>response({revision:'r2', settings:replacement}));
        f.queue(PREVIEW, async()=>response(plan('reloaded'))); await f.click('reload');
        assert.equal(f.get('proxy').value, replacement.proxy_domains[0]); assert.equal(f.get('mode').value, 'standard');
        assert.equal(f.document.querySelectorAll('[data-preset]')[0].checked, false);
        assert.match(f.get('save-state').textContent, /已与保存版本一致/); assert.equal(f.get('save').disabled, true);
        assert.match(f.get('rule-preview').textContent, /reloaded/); assert.equal(f.get('preview-label').textContent, '已保存规则预览');
        assert.equal(f.confirms.length, dirtyStart ? 1 : 0); await f.edit('next.example.test'); await f.assertRevision('r2');
    });
}
test('cancelled discard does not request anything or change draft, baseline or revision', async () => {
    const f = await fixture(); await f.edit('keep.example.test'); const before = f.ui(), count = f.requests.length;
    f.confirm(false); await f.click('reload'); assert.equal(f.requests.length, count); assert.deepEqual(f.ui(), before);
    assert.equal(f.confirms.length, 1); await f.assertRevision('r1');
});
for (const stage of [SNAPSHOT, PREVIEW]) {
    for (const failure of ['http', 'json', 'network']) {
        test(`${stage} ${failure} failure preserves draft, saved baseline, revision and preview`, async () => {
            const f = await fixture(); await f.edit('keep-on-error.example.test'); const before = f.ui();
            if (stage === PREVIEW) f.queue(SNAPSHOT, async()=>response({revision:'r2', settings:replacement}));
            f.queue(stage, async()=>{
                if (failure === 'http') return response({detail:'synthetic failure'}, 503);
                if (failure === 'network') throw new Error('synthetic failure');
                return {...response(null), json:async()=>{throw new Error('synthetic failure');}};
            });
            await f.click('reload'); assert.deepEqual(f.ui(), before); assert.equal(f.get('notice').className, 'error');
            assert.match(f.get('notice').textContent, /synthetic failure/); assert(f.requests.every(r=>r.method === 'GET'));
            await f.edit(original.proxy_domains[0]); assert.equal(f.get('save').disabled, true, 'saved baseline stays unchanged');
            await f.edit('keep-on-error.example.test'); await f.assertRevision('r1');
        });
    }
    test(`${stage} failure preserves an initially clean form and disabled save`, async () => {
        const f = await fixture(), before = f.ui();
        if (stage === PREVIEW) f.queue(SNAPSHOT, async()=>response({revision:'r2', settings:replacement}));
        f.queue(stage, async()=>response({detail:'synthetic failure'}, 503)); await f.click('reload');
        assert.deepEqual(f.ui(), before); await f.edit('next.example.test'); await f.assertRevision('r1');
    });
}
test('busy operation ignores repeated reload activation without extra request or confirmation', async () => {
    const f = await fixture(); await f.edit('discard-approved.example.test');
    const gate = f.hold(SNAPSHOT), pending = f.click('reload'); await gate.started;
    const count = f.requests.length; await f.click('reload'); assert.equal(f.requests.length, count); assert.equal(f.confirms.length, 1);
    gate.resolve(response({revision:'r2', settings:replacement})); await pending;
    assert.equal(f.get('proxy').value, replacement.proxy_domains[0]); assert.equal(f.get('reload').disabled, false);
});
test('existing save race retains edits and updates If-Match to the revision actually saved', async () => {
    const f = await fixture(); await f.edit('save-this.example.test');
    const gate = f.hold(ROUTING, 'PUT'), pending = f.click('save'); const options = await gate.started;
    assert.equal(options.headers['If-Match'], '"r1"'); await f.edit('edited-during-save.example.test');
    gate.resolve(response({...original, proxy_domains:['save-this.example.test']}, 200, 'r3')); await pending;
    f.assertDirty('edited-during-save.example.test'); await f.assertRevision('r3');
});
test('existing draft-preview race never replaces a newer input warning or preview', async () => {
    const f = await fixture(); await f.edit('preview-this.example.test'); const oldPreview = f.get('rule-preview').textContent;
    const gate = f.hold(PREVIEW, 'POST'), pending = f.click('preview'); await gate.started;
    await f.edit('edited-during-preview.example.test'); gate.resolve(response(plan('stale draft'))); await pending;
    f.assertDirty('edited-during-preview.example.test'); assert.equal(f.get('rule-preview').textContent, oldPreview);
    assert.equal(f.requests.filter(r=>r.method === 'PUT').length, 0);
});

// Independent review additions: six controls, no browser or network.
for (const stage of [SNAPSHOT, PREVIEW]) {
    test(`independent check: editing during ${stage} JSON parsing preserves old revision`, async () => {
        const f = await fixture();
        const parsed = deferred(), started = deferred();
        if (stage === PREVIEW) f.queue(SNAPSHOT, async()=>response({revision:'r2', settings:replacement}));
        f.queue(stage, async()=>({...response(null), json:()=>{started.resolve();return parsed.promise;}}));
        const pending=f.click('reload'); await started.promise;
        await f.edit('edited-during-json.example.test');
        parsed.resolve(stage === SNAPSHOT ? {revision:'r2', settings:replacement} : plan('new-plan'));
        await pending; f.assertDirty('edited-during-json.example.test');
        assert.match(f.get('notice').textContent, /保留/); await f.assertRevision('r1');
    });
    test(`independent check: revert to original during ${stage} remains clean without adopting response`, async () => {
        const f = await fixture(); const before = f.ui();
        if (stage === PREVIEW) f.queue(SNAPSHOT, async()=>response({revision:'r2', settings:replacement}));
        const gate=f.hold(stage), pending=f.click('reload'); await gate.started;
        await f.edit('temporary.example.test'); await f.edit(original.proxy_domains[0]);
        gate.resolve(stage === SNAPSHOT ? response({revision:'r2', settings:replacement}) : response(plan('new-plan')));
        await pending;
        assert.equal(f.get('proxy').value, original.proxy_domains[0]);
        assert.equal(f.get('save').disabled, true);
        assert.match(f.get('save-state').textContent, /已与保存版本一致/);
        assert.match(f.get('notice').textContent, /保留/);
        assert.equal(f.get('rule-preview').textContent, before['rule-preview']);
        await f.edit('fresh.example.test'); await f.assertRevision('r1');
    });
    test(`independent check: newer edits survive ${stage} failure`, async () => {
        const f = await fixture(); const before = f.ui();
        if (stage === PREVIEW) f.queue(SNAPSHOT, async()=>response({revision:'r2', settings:replacement}));
        const gate=f.hold(stage), pending=f.click('reload'); await gate.started;
        await f.edit('new-before-error.example.test'); gate.reject(new Error('independent failure')); await pending;
        f.assertDirty('new-before-error.example.test'); assert.equal(f.get('rule-preview').textContent, before['rule-preview']);
        assert.equal(f.get('notice').className, 'error'); await f.assertRevision('r1');
    });
}
