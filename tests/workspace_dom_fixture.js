'use strict';
// Execute the unmodified browser controller with deterministic DOM/fetch substitutes.
// These controls do not launch a browser, contact a server, or qualify real rendering.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const filename = process.argv[2] || path.join(__dirname, '../web/js/workspace.js');
const script = fs.readFileSync(filename, 'utf8');
const SNAPSHOT = '/api/routing/mihomo/snapshot';
const PREVIEW = '/api/routing/mihomo/preview';
const ROUTING = '/api/routing/mihomo';
const original = {mode:'direct', direct_domains:['direct.example.test'], proxy_domains:['saved.example.test'],
    presets:{openai:true}, bypass_cgnat:false, intranet:[{suffix:'corp.example.test', nameservers:['system']}]};
const replacement = {...original, mode:'standard', proxy_domains:['server.example.test'], presets:{openai:false}};
const R1 = '1'.repeat(64), R2 = '2'.repeat(64), R3 = '3'.repeat(64);
const plan = (name, revision) => ({...(revision === undefined ? {} : {revision}), sections:[{comment:name, rules:['DOMAIN-SUFFIX,' + name + ',DIRECT']}], warnings:[]});
const response = (value, status = 200, etag = R3) => ({ok:status >= 200 && status < 300, status,
    json:async()=>structuredClone(value), headers:{get:key=>key === 'X-VUI-Node-Revision' ? 'a'.repeat(64) : key === 'ETag' ? '"' + etag + '"' : null}});
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
async function fixture({routingFetch, bootstrapFailure = false} = {}) {
    const nodes = new Map(), globalEvents = new Map(), requests = [], confirms = [], queued = [];
    const buttons = ['reload','preview','save','add-zone','refresh-grants','create-grant','forget','copy','download'];
    const get = id => { if (!nodes.has(id)) nodes.set(id, new Element(buttons.includes(id) ? 'button' : 'div')); return nodes.get(id); };
    buttons.forEach(get);
    const ready = deferred(); let hidden = true, savedPreviewRevision = R1;
    Object.defineProperty(get('workspace'), 'hidden', {get:()=>hidden, set:value=>{hidden = value;if (!value) ready.resolve();}});
    const document = {getElementById:get, createElement:tag=>new Element(tag), createTextNode:text=>Object.assign(new Element('#text'), {textContent:text}),
        querySelectorAll:selector=>[...nodes.values()].flatMap(node => [node, ...descendants(node)]).filter(node => matches(node, selector))};
    let confirmed = true;
    const fetch = async (url, options = {}) => {
        requests.push({url, options, method:options.method || 'GET'});
        assert.equal(options.credentials, 'same-origin'); assert.equal(options.cache, 'no-store');
        assert.equal(options.headers['X-VUI-Request'], '1');
        const index = queued.findIndex(item => item.url === url && item.method === (options.method || 'GET'));
        if (index >= 0) return queued.splice(index, 1)[0].reply(options);
        if (routingFetch && url.startsWith(ROUTING)) return routingFetch(url, options);
        if (url === SNAPSHOT) return response({revision:R1, settings:original});
        if (url === PREVIEW) return response(options.method === 'POST' ? plan('draft') : plan('saved', savedPreviewRevision));
        if (url === ROUTING && options.method === 'PUT') { savedPreviewRevision = R3; return response(JSON.parse(options.body)); }
        if (url === '/api/auth/me') return response({username:'synthetic'});
        if (url === '/api/routing/mihomo/catalog') return response({categories:[{id:'service', name_zh:'服务'}], presets:[{id:'openai', category:'service', name_zh:'OpenAI'}]});
        if (['/api/inbounds','/api/subscription/mihomo-warnings','/api/subscriptions'].includes(url)) return response([]);
        assert.fail('Unexpected request: ' + url);
    };
    if (bootstrapFailure) {
        let className = '';
        Object.defineProperty(get('notice'), 'className', {get:()=>className, set:value=>{
            className = value; if (value === 'error') ready.resolve();
        }});
    }
    vm.runInNewContext(script, {document, fetch, location:{origin:'http://example.test'},
        addEventListener:(type, fn)=>globalEvents.set(type, [...(globalEvents.get(type) || []), fn]),
        confirm:text=>{confirms.push(text);return confirmed;}, setTimeout, clearTimeout}, {filename});
    let timer;
    try { await Promise.race([ready.promise, new Promise((_, reject)=>{timer = setTimeout(()=>reject(new Error('controller bootstrap timeout')), 5000);})]); }
    finally { clearTimeout(timer); }
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(get('workspace').hidden, bootstrapFailure, 'controller bootstrap state');
    if (!bootstrapFailure) assert.equal(get('save').disabled, true);
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
    return {get, document, globalEvents, requests, confirms, queue, hold, edit, ui, assertDirty, assertRevision,
        confirm:value=>{confirmed = value;}, click:id=>get(id).emit('click')};
}

module.exports = {fixture, SNAPSHOT, PREVIEW, ROUTING, original, replacement, plan, response, deferred, R1, R2, R3};
