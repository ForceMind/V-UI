'use strict';
// Execute the actual controller with deterministic DOM and I/O boundaries.
// These tests do not launch a browser, contact a server, or qualify actual rendering.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const filename = process.argv[2] || path.join(__dirname, '../web/js/certificates.js');
const script = fs.readFileSync(filename, 'utf8');
const deferred = () => {
    let resolve, reject;
    const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
    return {promise, resolve, reject};
};
const flush = () => new Promise(resolve => setImmediate(resolve));
const response = (payload, status = 200) => ({ok:status >= 200 && status < 300, status,
    json:async()=>structuredClone(payload)});
const certificate = (id = 'synthetic-a', changes = {}) => ({id, domain:id + '.example.test',
    environment:'production', status:'valid', days_remaining:20, not_after:1800000000,
    renew_at:1790000000, bindings:[], auto_renew:true, revision:'synthetic-revision',
    latest_job:null, error:null, ...changes});
class Element {
    constructor(tag) {
        this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null;
        this.attributes = {}; this.dataset = {}; this.listeners = {}; this.disabled = false;
        this.hidden = false; this._text = ''; this._value = undefined;
    }
    set textContent(value) {this._text = String(value); this.replaceChildren();}
    get textContent() {return this._text + this.children.map(n=>n.textContent).join('');}
    setAttribute(key, value) {
        this.attributes[key] = String(value);
        if (key === 'value') this._value = String(value);
        if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c)=>c.toUpperCase())] = String(value);
    }
    append(...children) {for (const child of children) {child.parentNode = this; this.children.push(child);}}
    replaceChildren(...children) {for (const child of this.children) child.parentNode = null; this.children = []; this.append(...children);}
    addEventListener(event, fn) {(this.listeners[event] ||= []).push(fn);}
    click() {if (!this.disabled) for (const fn of this.listeners.click || []) fn({target:this, preventDefault(){}});}
    get options() {return this.children.filter(n=>n.tagName === 'OPTION');}
    get value() {
        if (this.tagName !== 'SELECT') return this._value ?? '';
        if (this._value === undefined) return this.options[0]?.value ?? '';
        return this.options.some(n=>n.value === this._value) ? this._value : '';
    }
    set value(value) {this._value = String(value);}
    querySelectorAll(selector) {
        const all = this.children.flatMap(n=>[n, ...n.querySelectorAll('*')]);
        if (selector === '*') return all;
        if (selector.startsWith('[data-')) return all.filter(n=>Object.hasOwn(n.attributes, selector.slice(1, -1)));
        return all.filter(n=>n.tagName === selector.toUpperCase());
    }
}
async function fixture(certs = [certificate()]) {
    const root = new Element('main'), ids = {}, requests = [], pending = [], timers = new Map(), events = {};
    for (const [id, tag] of Object.entries({'notice':'p', 'request-certificate':'form',
        'certificate-list':'div', 'refresh':'button', 'cert-workspace':'div', 'domain':'input',
        'email':'input', 'environment':'select', 'auto-renew':'input', 'terms':'input'})) {
        ids[id] = new Element(tag); root.append(ids[id]);
    }
    const nodes = [{id:7, remark:'TLS A', managed_certificate_eligible:true},
        {id:8, remark:'TLS B', managed_certificate_eligible:true},
        {id:9, remark:'REALITY excluded', managed_certificate_eligible:false}];
    const document = {hidden:false, getElementById:id=>ids[id], createElement:tag=>new Element(tag),
        querySelectorAll:selector=>root.querySelectorAll(selector)};
    const fetch = (url, options = {}) => {
        assert.equal(options.credentials, 'same-origin'); assert.equal(options.cache, 'no-store');
        if (url.startsWith('/api/certificates')) assert.equal(options.headers['X-VUI-Request'], '1');
        const request = {url, method:options.method || 'GET', body:options.body ? JSON.parse(options.body) : undefined};
        requests.push(request);
        if (url === '/api/certificates/capabilities') return Promise.resolve(response({worker_running:true, panel_hot_reload:true}));
        if (url === '/api/inbounds') return Promise.resolve(response(nodes));
        const gate = deferred(); pending.push({...request, ...gate}); return gate.promise;
    };
    let confirmResult = true, reloads = 0; const confirms = [], redirects = [];
    vm.runInNewContext(script, {document, fetch, Date,
        location:{replace:url=>redirects.push(url), reload:()=>reloads++},
        confirm:message=>{confirms.push(message); return confirmResult;},
        setInterval:(fn, ms)=>{assert.equal(ms, 5000); timers.set(1, fn); return 1;},
        clearInterval:id=>timers.delete(id), addEventListener:(event, fn)=>events[event] = fn}, {filename});
    const take = (method = 'GET') => {
        const index = pending.findIndex(r=>r.method === method);
        assert.notEqual(index, -1, 'Expected pending ' + method + ' request');
        return pending.splice(index, 1)[0];
    };
    const card = id=>ids['certificate-list'].querySelectorAll('[data-certificate]').find(n=>n.dataset.certificate === id);
    const button = (label, id = 'synthetic-a')=>card(id).querySelectorAll('button').find(n=>n.textContent === label);
    const select = (id = 'synthetic-a')=>card(id).querySelectorAll('[data-bind-select]')[0];
    await flush(); take().resolve(response(certs)); await flush();
    assert.equal(ids.refresh.disabled, false, 'initial action has completed');
    return {ids, root, document, requests, pending, take, card, button, select, confirms, redirects,
        confirm:value=>{confirmResult = value;}, reloads:()=>reloads, events,
        tick:()=>{for (const callback of timers.values()) callback();},
        listRequests:()=>requests.filter(r=>r.url === '/api/certificates' && r.method === 'GET')};
}
async function pause(f) {
    f.button('暂停自动续期').click(); await flush(); const put = f.take('PUT');
    assert.deepEqual(put.body, {enabled:false}); put.resolve(response({})); await flush();
    f.take().resolve(response([certificate('synthetic-a', {auto_renew:false})])); await flush();
    assert.ok(f.button('启用自动续期')); assert.equal(f.ids.notice.textContent, '自动续期设置已更新。');
}
test('poll preserves each certificate choice and never offers an ineligible node', async()=>{
    const f = await fixture([certificate(), certificate('synthetic-b')]);
    f.select().value = '7'; f.select('synthetic-b').value = '8';
    f.tick(); await flush(); f.take().resolve(response([certificate(), certificate('synthetic-b')])); await flush();
    assert.equal(f.select().value, '7'); assert.equal(f.select('synthetic-b').value, '8');
    assert.deepEqual(f.select().options.map(n=>n.value), ['', '7', '8']);
    assert.equal(f.requests.filter(r=>r.method !== 'GET').length, 0, 'restoring a choice never submits it');
    // A disappeared card must not retain a hidden choice when its ID appears again.
    f.tick(); await flush(); f.take().resolve(response([certificate('synthetic-b')])); await flush();
    f.tick(); await flush(); f.take().resolve(response([certificate(), certificate('synthetic-b')])); await flush();
    assert.equal(f.select().value, ''); assert.equal(f.select('synthetic-b').value, '8');
    // A stale DOM choice absent from the current eligible options falls back to the placeholder.
    const stale = new Element('option'); stale.setAttribute('value', '9'); f.select().append(stale); f.select().value = '9';
    f.tick(); await flush(); f.take().resolve(response([certificate()])); await flush(); assert.equal(f.select().value, '');
});
test('choice made while JSON is pending survives and explicit bind uses that latest choice', async()=>{
    const f = await fixture(); f.select().value = '7'; f.tick(); await flush();
    const json = deferred(); f.take().resolve({ok:true, status:200, json:()=>json.promise}); await flush();
    f.select().value = '8'; json.resolve([certificate()]); await flush(); assert.equal(f.select().value, '8');
    f.button('绑定节点').click(); await flush(); const bind = f.take('POST');
    assert.equal(bind.url, '/api/certificates/synthetic-a/bind-inbound'); assert.deepEqual(bind.body, {inbound_id:8});
    bind.resolve(response({})); await flush(); f.take().resolve(response([certificate()])); await flush();
});
for (const boundary of ['fetch', 'json']) {
    test(`older poll ${boundary} completion cannot repaint a successful mutation`, async()=>{
        const f = await fixture(); f.tick(); await flush(); const old = f.take(); let completion = old;
        if (boundary === 'json') {completion = deferred(); old.resolve({ok:true, status:200, json:()=>completion.promise}); await flush();}
        await pause(f); const card = f.card('synthetic-a'), notice = f.ids.notice.textContent;
        completion.resolve(boundary === 'json' ? [certificate()] : response([certificate()])); await flush();
        assert.equal(f.card('synthetic-a'), card); assert.ok(f.button('启用自动续期')); assert.equal(f.ids.notice.textContent, notice);
    });
}
for (const failure of ['network', 'http', 'json']) {
    test(`older poll ${failure} failure cannot overwrite a successful mutation notice`, async()=>{
        const f = await fixture(); f.tick(); await flush(); const old = f.take(); await pause(f);
        if (failure === 'network') old.reject(new Error('stale network failure'));
        if (failure === 'http') old.resolve(response({detail:'stale HTTP failure'}, 500));
        if (failure === 'json') old.resolve({ok:true, status:200, json:async()=>{throw new Error('stale JSON failure');}});
        await flush(); assert.equal(f.ids.notice.textContent, '自动续期设置已更新。'); assert.equal(f.ids.notice.className, '');
        assert.ok(f.button('启用自动续期'));
    });
}
test('mutation invalidates an older read immediately, before its write completes', async()=>{
    const f = await fixture(); f.tick(); await flush(); const old = f.take();
    f.button('暂停自动续期').click(); await flush(); const put = f.take('PUT'), card = f.card('synthetic-a');
    old.resolve(response([certificate()])); await flush(); assert.equal(f.card('synthetic-a'), card);
    assert.ok(f.root.querySelectorAll('button').every(b=>b.disabled));
    f.button('暂停自动续期').click(); await flush(); assert.equal(f.requests.filter(r=>r.method === 'PUT').length, 1);
    put.resolve(response({})); await flush(); f.take().resolve(response([certificate('synthetic-a', {auto_renew:false})])); await flush();
    assert.equal(f.button('启用自动续期').disabled, false);
});
test('background polling is single-flight without blocking an explicit mutation', async()=>{
    const f = await fixture(); const initial = f.listRequests().length;
    f.document.hidden = true; f.tick(); await flush(); assert.equal(f.listRequests().length, initial);
    f.document.hidden = false; f.tick(); await flush(); const old = f.take(), count = f.listRequests().length;
    f.tick(); f.tick(); await flush(); assert.equal(f.listRequests().length, count);
    assert.equal(f.button('暂停自动续期').disabled, false); await pause(f);
    old.resolve(response([certificate()])); await flush(); f.tick(); await flush();
    assert.equal(f.listRequests().length, count + 2, 'action GET and next poll still execute');
    f.take().resolve(response([certificate('synthetic-a', {auto_renew:false})])); await flush();
});
test('current polling error preserves choices, reports failure, and releases the next poll', async()=>{
    const f = await fixture(); f.select().value = '7'; const card = f.card('synthetic-a');
    f.tick(); await flush(); f.take().reject(new Error('current network failure')); await flush();
    assert.equal(f.card('synthetic-a'), card); assert.equal(f.select().value, '7');
    assert.equal(f.ids.notice.textContent, 'current network failure'); assert.equal(f.ids.notice.className, 'error');
    f.tick(); await flush(); f.take().resolve(response([certificate('synthetic-a', {auto_renew:false})])); await flush();
    assert.ok(f.button('启用自动续期')); assert.equal(f.select().value, '7');
});
test('manual refresh makes one GET and preserves the latest choice', async()=>{
    const f = await fixture(); const count = f.listRequests().length;
    f.ids.refresh.click(); await flush(); f.select().value = '8'; f.take().resolve(response([certificate()])); await flush();
    assert.equal(f.listRequests().length, count + 1); assert.equal(f.select().value, '8'); assert.equal(f.ids.refresh.disabled, false);
});
test('pagehide invalidates pending rendering and polling; persisted pageshow reloads', async()=>{
    const f = await fixture(); f.tick(); await flush(); const old = f.take(), card = f.card('synthetic-a');
    f.events.pagehide(); old.resolve(response([certificate('synthetic-a', {auto_renew:false})])); await flush();
    assert.equal(f.card('synthetic-a'), card); const count = f.requests.length; f.tick(); await flush(); assert.equal(f.requests.length, count);
    f.events.pageshow({persisted:true}); assert.equal(f.reloads(), 1);
    const duringWrite = await fixture(); duringWrite.button('暂停自动续期').click(); await flush();
    const write = duringWrite.take('PUT'), before = duringWrite.ids.notice.textContent, requests = duringWrite.requests.length;
    duringWrite.events.pagehide(); write.resolve(response({})); await flush();
    assert.equal(duringWrite.ids.notice.textContent, before);
    assert.equal(duringWrite.requests.length, requests, 'an already-started write does not launch a new GET after pagehide');
});
test('existing staging, empty-bind, and panel-confirmation safeguards remain', async()=>{
    const f = await fixture([certificate(), certificate('synthetic-stage', {environment:'staging'})]);
    assert.equal(f.button('绑定节点', 'synthetic-stage').disabled, true);
    assert.equal(f.button('应用到面板', 'synthetic-stage').disabled, true);
    f.button('绑定节点').click(); await flush(); assert.equal(f.ids.notice.textContent, '请先选择 TLS 节点');
    assert.equal(f.requests.filter(r=>r.method === 'POST').length, 0);
    f.confirm(false); f.button('应用到面板').click(); await flush();
    assert.equal(f.confirms.length, 1); assert.equal(f.requests.filter(r=>r.method === 'POST').length, 0);
    f.take().resolve(response([certificate()])); await flush();
});
