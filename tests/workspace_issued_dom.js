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
const plan = {sections:[], warnings:[]};
const grant = (id = 1, changes = {}) => ({id, label:'synthetic grant ' + id, server:'node.example.test',
    inbound_ids:[7], formats:['mihomo.yaml'], expires_at:2000000000,
    revoked:false, expired:false, invalidated:false, invalidation_reason:null, ...changes});
const response = (value, status = 200) => ({ok:status >= 200 && status < 300, status,
    json:async()=>structuredClone(value), headers:{get:key=>key === 'X-VUI-Node-Revision' ? 'a'.repeat(64) : key === 'ETag' ? '"r1"' : null}});
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
    if (selector === '[data-node]:checked') return Object.hasOwn(node.dataset, 'node') && node.checked;
    if (selector === 'input[name="format"]:checked') return node.name === 'format' && node.checked;
    if (selector === 'button') return node.tagName === 'BUTTON';
    if (selector === '.zone') return node.className === 'zone';
    const attribute = /^\[data-([a-z]+)(?:="([^"]*)")?\]$/.exec(selector);
    return Boolean(attribute && Object.hasOwn(node.dataset, attribute[1]) &&
        (attribute[2] === undefined || node.dataset[attribute[1]] === attribute[2]));
}
async function fixture(initialGrants = [grant()], clipboard = async()=>{}) {
    const nodes = new Map(), requests = [], confirms = [], queued = [], globalEvents = new Map(), redirects = [], downloads = [], blobs = [], focuses = [], clipboardWrites = [], revokedUrls = [];
    let confirmation = true;
    const buttons = ['reload','preview','save','add-zone','refresh-grants','create-grant','forget','copy','download'];
    const get = id => {
        if (!nodes.has(id)) nodes.set(id, new Element(buttons.includes(id) ? 'button' : 'div'));
        return nodes.get(id);
    };
    buttons.forEach(get); get('workspace').hidden = true; get('issued').hidden = true; get('days').value = '30';
    get('synthetic-format').name = 'format'; get('synthetic-format').value = 'mihomo.yaml'; get('synthetic-format').checked = true;
    get('label').value = 'synthetic grant'; get('server').value = 'node.example.test';
    get('issued-urls').focus = () => focuses.push('focus'); get('issued-urls').select = () => focuses.push('select');
    const document = {body:new Element('body'), getElementById:get, createElement:tag=>{ const el = new Element(tag); if (tag === 'a') el.click = () => downloads.push(el.href); return el; },
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
        if (method === 'GET' && url === SNAPSHOT) return response({revision:'r1', settings});
        if (method === 'GET' && url === PREVIEW) return response(plan);
        if (method === 'GET' && url === '/api/inbounds') return response([{id:7,remark:'synthetic node',core:'sing-box',protocol:'vless',enable:true}]);
        if (method === 'GET' && url === '/api/subscription/mihomo-warnings') return response([]);
        if (method === 'GET' && url === GRANTS) return response(initialGrants);
        if (method === 'POST' && url === GRANTS) return response({id:1, paths:{'mihomo.yaml':'/sub/synthetic-token/mihomo.yaml'}},201);
        const rotate = /^\/api\/subscriptions\/(\d+)\/rotate$/.exec(url);
        if (method === 'POST' && rotate) return response({id:Number(rotate[1]), paths:{'mihomo.yaml':'/sub/synthetic-token/mihomo.yaml'}});
        assert.fail('Unexpected request: ' + method + ' ' + url);
    };
    vm.runInNewContext(script, {document, fetch, location:{origin:'http://example.test',replace:url=>redirects.push(url),reload(){}},
        navigator:{clipboard:{writeText:async text=>{clipboardWrites.push(text);return clipboard(text);}}},
        URL:{createObjectURL:blob=>{blobs.push(blob);return 'blob:synthetic';},revokeObjectURL:url=>revokedUrls.push(url)},
        localStorage:new Proxy({}, {get:()=>assert.fail('No Web Storage access')}),
        sessionStorage:new Proxy({}, {get:()=>assert.fail('No Web Storage access')}),
        addEventListener:(type,fn)=>globalEvents.set(type,fn), confirm:text=>{confirms.push(text);return confirmation;},
        setTimeout:fn=>{fn();return 1;}, clearTimeout(){}}, {filename});
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(get('workspace').hidden, false, 'controller bootstrap completes');
    assert.equal(get('notice').className, '', 'bootstrap has no hidden controller error');
    document.querySelectorAll('[data-node]').forEach(input=>{input.checked=true;});
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
    return {get, document, requests, confirms, row, rotate, revoke, status, queue, hold, redirects, downloads, blobs, focuses, clipboardWrites, revokedUrls,
        confirm:value=>{confirmation=value;},
        // This calls a controller listener only; it is not real navigation/bfcache.
        pagehide:()=>globalEvents.get('pagehide')({persisted:true}),
        submit:()=>get('grant-form').emit('submit'),
        click:id=>get(id).click()};
}


const flush = () => new Promise(resolve=>setImmediate(resolve));
const tokenReply = () => ({id:1,paths:{'mihomo.yaml':'/sub/synthetic-token/mihomo.yaml'}});
const beginIssue = async (f, kind) => kind === 'rotate' ? f.rotate(1).click() : f.submit();
for (const kind of ['create', 'rotate']) {
    const url = kind === 'create' ? GRANTS : GRANTS + '/1/rotate';
    for (const boundary of ['fetch', 'json body']) {
        test(`${kind}: pagehide prevents late ${boundary} response from repopulating a bearer URL`, async () => {
            const f = await fixture([grant()]);
            const gate = boundary === 'fetch' ? f.hold(url,'POST') : deferred();
            if (boundary === 'json body') f.queue(url, async()=>({...response({}), json:()=>gate.promise}), 'POST');
            const pending = beginIssue(f, kind); await flush();
            assert.equal(f.get('issued').hidden,true);
            f.pagehide(); assert.equal(f.get('issued-urls').value,'');
            gate.resolve(boundary === 'fetch' ? response(tokenReply()) : tokenReply());
            await pending; await flush();
            assert.equal(f.requests.filter(r=>r.method==='POST').length,1,'one authorized issue request, no retry');
            assert.equal(f.get('issued').hidden,true,'pagehide must remain effective after pending issue settles');
            assert.equal(f.get('issued-urls').value,'');
        });
    }
    test(`${kind}: a visible bearer URL can be hidden while the following list refresh is pending`, async () => {
        const f = await fixture([grant()]), gate = f.hold(GRANTS);
        const pending = beginIssue(f,kind); await gate.started;
        assert.equal(f.get('issued').hidden,false,'the bearer URL is already visible');
        assert.match(f.get('issued-urls').value,/synthetic-token/);
        await f.click('forget');
        const hiddenAfterRealClick = f.get('issued').hidden;
        gate.resolve(response([grant()])); await pending; await flush();
        assert.equal(hiddenAfterRealClick,true,'browser activation of Hide should immediately clear the already-visible URL');
    });
}
test('pagehide prevents a pending sensitive YAML body from starting a later download', async () => {
    const f = await fixture([grant()]); await f.rotate(1).click();
    const gate = deferred();
    f.queue('/sub/synthetic-token/mihomo.yaml', async()=>({...response({}),blob:()=>gate.promise}));
    const pending = f.click('download'); await flush(); f.pagehide();
    gate.resolve({synthetic:'client-configuration'}); await pending;
    assert.equal(f.get('issued').hidden,true); assert.equal(f.get('issued-urls').value,'');
    assert.equal(f.blobs.length,0,'no object URL after pagehide');
    assert.equal(f.downloads.length,0,'no download click after pagehide');
});
test('idle Hide clears an issued token and does not revoke or rotate it', async () => {
    const f = await fixture([grant()]); await f.rotate(1).click();
    const count=f.requests.length; await f.click('forget');
    assert.equal(f.get('issued').hidden,true); assert.equal(f.get('issued-urls').value,'');
    assert.equal(f.requests.length,count);
});
test('401 from the refresh after issuance clears the token and redirects without retry', async () => {
    const f = await fixture([grant()]); f.queue(GRANTS,async()=>response({},401));
    await f.rotate(1).click();
    assert.equal(f.get('issued').hidden,true); assert.equal(f.get('issued-urls').value,'');
    assert.deepEqual(f.redirects,['/login']);
    assert.equal(f.requests.filter(r=>r.method==='POST').length,1);
});
test('foreground rotation still exposes exactly one token', async () => {
    const f = await fixture([grant()]); await f.rotate(1).click();
    assert.equal(f.get('issued').hidden,false); assert.match(f.get('issued-urls').value,/synthetic-token/);
    assert.equal(f.get('forget').disabled,false); assert.equal(f.get('download').disabled,false);
});

const assertHidden = f => {
    assert.equal(f.get('issued').hidden, true); assert.equal(f.get('issued-urls').value, '');
    assert.equal(f.get('download').disabled, true);
};
for (const kind of ['create', 'rotate']) {
    const url = kind === 'create' ? GRANTS : GRANTS + '/1/rotate';
    test(`${kind}: a current request owns its result after clearing an older visible token`, async () => {
        const f = await fixture(); await f.rotate(1).click();
        const gate = f.hold(url, 'POST'), pending = beginIssue(f, kind); await gate.started;
        assert.equal(f.get('issued').hidden, true); assert.equal(f.get('issued-urls').value, '');
        const replacement = {id:2, paths:{raw:'/sub/synthetic-new-token/raw'}};
        gate.resolve(response(replacement)); await pending; await flush();
        assert.equal(f.get('issued').hidden, false);
        assert.equal(f.get('issued-urls').value, 'http://example.test/sub/synthetic-new-token/raw');
        assert.equal(f.get('download').disabled, true, 'no YAML path remains disabled');
        assert.equal(f.requests.filter(r=>r.method==='POST').length, 2);
    });
    test(`${kind}: a fresh explicit request works after a prior pagehide-cancelled request settles`, async () => {
        const f = await fixture(), gate = f.hold(url, 'POST');
        const pending = beginIssue(f, kind); await gate.started; f.pagehide();
        gate.resolve(response(tokenReply())); await pending; await flush(); assertHidden(f);
        if (kind === 'create') {
            // pagehide also invalidates node selection; a new create needs an explicit fresh choice.
            await f.click('refresh-grants');
            f.document.querySelectorAll('[data-node]').forEach(input=>{input.checked=true;});
        }
        f.queue(url, async()=>response({id:1, paths:{'mihomo.yaml':'/sub/new-generation/mihomo.yaml'}}), 'POST');
        await beginIssue(f, kind); await flush();
        assert.equal(f.get('issued-urls').value, 'http://example.test/sub/new-generation/mihomo.yaml');
        assert.equal(f.get('issued').hidden, false);
        assert.equal(f.requests.filter(r=>r.method==='POST').length, 2);
    });
    for (const invalidator of ['Hide', 'pagehide']) {
        test(`${kind}: ${invalidator} during list refresh suppresses stale success guidance`, async () => {
            const f = await fixture(), gate = f.hold(GRANTS);
            const pending = beginIssue(f, kind); await gate.started;
            assert.equal(f.get('forget').disabled, false, 'local Hide remains enabled while busy');
            for (const id of ['copy', 'download', 'create-grant', 'refresh-grants']) assert.equal(f.get(id).disabled, true, id);
            const oldNotice = f.get('notice').textContent;
            if (invalidator === 'Hide') await f.click('forget'); else f.pagehide();
            assert.equal(f.get('issued').hidden, true); assert.equal(f.get('issued-urls').value, '');
            gate.resolve(response([grant()])); await pending; await flush(); assertHidden(f);
            assert.equal(f.get('notice').textContent, oldNotice, 'no request to save a now-hidden address');
            assert.equal(f.requests.filter(r=>r.method!=='GET').length, 1, 'no automatic revoke/rotate/retry');
        });
    }
    test(`${kind}: Hide remains effective if the post-issue list refresh fails`, async () => {
        const f = await fixture(), gate = f.hold(GRANTS);
        const pending = beginIssue(f, kind); await gate.started; await f.click('forget');
        gate.resolve(response({detail:'Synthetic list failure'}, 503)); await pending; await flush(); assertHidden(f);
        assert.equal(f.get('notice').className, 'error');
        assert.equal(f.get('create-grant').disabled, false);
        assert.equal(f.requests.filter(r=>r.method!=='GET').length, 1);
    });
    test(`${kind}: busy submission cannot issue a second request or change ownership`, async () => {
        const f = await fixture(), gate = f.hold(url, 'POST');
        const pending = beginIssue(f, kind); await gate.started;
        await beginIssue(f, kind); await f.submit();
        assert.equal(f.requests.filter(r=>r.method==='POST').length, 1);
        gate.resolve(response(tokenReply())); await pending; await flush();
        assert.equal(f.get('issued').hidden, false); assert.match(f.get('issued-urls').value, /synthetic-token/);
    });
    test(`${kind}: unauthorized issuance clears the previous token without retry`, async () => {
        const f = await fixture(); await f.rotate(1).click();
        f.queue(url, async()=>response({}, 401), 'POST');
        await beginIssue(f, kind); await flush(); assertHidden(f);
        assert.deepEqual(f.redirects, ['/login']);
        assert.equal(f.requests.filter(r=>r.method==='POST').length, 2);
    });
    test(`${kind}: malformed issuance JSON cannot restore the prior token`, async () => {
        const f = await fixture(); await f.rotate(1).click();
        f.queue(url, async()=>({...response({}), json:async()=>{throw new Error('Synthetic JSON failure');}}), 'POST');
        await beginIssue(f, kind); await flush(); assertHidden(f);
        assert.equal(f.get('notice').className, 'error'); assert.equal(f.get('create-grant').disabled, false);
    });
}
for (const invalidator of ['Hide', 'pagehide']) {
    for (const boundary of ['fetch', 'blob']) {
        test(`download: ${invalidator} during ${boundary} prevents later body/object URL/download side effects`, async () => {
            const f = await fixture(); await f.rotate(1).click();
            const gate = deferred(); let reads = 0;
            const reply = {...response({}), blob:async()=>{reads++;return boundary==='blob' ? gate.promise : {synthetic:'YAML'};}};
            f.queue('/sub/synthetic-token/mihomo.yaml', ()=>boundary==='fetch' ? gate.promise : reply);
            const pending = f.click('download'); await flush();
            if (invalidator === 'Hide') await f.click('forget'); else f.pagehide();
            gate.resolve(boundary==='fetch' ? reply : {synthetic:'YAML'}); await pending;
            assertHidden(f); assert.equal(f.blobs.length, 0); assert.equal(f.downloads.length, 0);
            assert.equal(reads, boundary==='fetch' ? 0 : 1, 'do not consume body after an invalidated fetch');
            assert.equal(f.requests.filter(r=>r.url.startsWith('/sub/')).length, 1);
        });
    }
    for (const outcome of ['resolve', 'reject']) {
        test(`clipboard: ${invalidator} suppresses stale ${outcome} notice and selection, not the issued clipboard call`, async () => {
            const gate = deferred(), f = await fixture([grant()], ()=>gate.promise); await f.rotate(1).click();
            const oldNotice = f.get('notice').textContent, pending = f.click('copy'); await flush();
            assert.deepEqual(f.clipboardWrites, ['http://example.test/sub/synthetic-token/mihomo.yaml']);
            if (invalidator === 'Hide') await f.click('forget'); else f.pagehide();
            gate[outcome](outcome==='reject' ? new Error('Synthetic clipboard rejection') : undefined); await pending;
            assertHidden(f); assert.equal(f.get('notice').textContent, oldNotice); assert.deepEqual(f.focuses, []);
            assert.equal(f.clipboardWrites.length, 1, 'an already-issued write cannot be recalled or retried');
        });
    }
}
test('foreground download makes one object URL/click and revokes its temporary URL', async () => {
    const f = await fixture(); await f.rotate(1).click();
    f.queue('/sub/synthetic-token/mihomo.yaml', async()=>({...response({}),blob:async()=>({synthetic:'YAML'})}));
    await f.click('download');
    assert.equal(f.blobs.length, 1); assert.deepEqual(f.downloads, ['blob:synthetic']);
    assert.deepEqual(f.revokedUrls, ['blob:synthetic']); assert.equal(f.document.body.children.length, 0);
    await f.click('forget'); assertHidden(f);
    assert.equal(f.downloads.length, 1, 'Hide cannot retract an already-started download');
});
test('unauthorized YAML download clears the visible token and never reads a body', async () => {
    const f = await fixture(); await f.rotate(1).click();
    f.queue('/sub/synthetic-token/mihomo.yaml', async()=>({...response({},401),blob:()=>assert.fail('401 body must not be downloaded')}));
    await f.click('download'); assertHidden(f); assert.deepEqual(f.redirects, ['/login']);
    assert.equal(f.blobs.length, 0); assert.equal(f.downloads.length, 0);
});
for (const outcome of ['resolve', 'reject']) {
    test(`foreground clipboard ${outcome} still provides current feedback`, async () => {
        const f = await fixture([grant()], async()=>{if(outcome==='reject')throw new Error('Synthetic clipboard rejection');});
        await f.rotate(1).click(); await f.click('copy');
        assert.equal(f.clipboardWrites.length, 1);
        assert.equal(f.get('issued').hidden, false);
        assert.match(f.get('notice').textContent, outcome==='resolve' ? /地址已复制/ : /无法自动复制/);
        assert.deepEqual(f.focuses, outcome==='resolve' ? [] : ['focus','select']);
    });
}
test('cancelled rotation preserves the visible token without sending a request', async () => {
    const f = await fixture(); await f.rotate(1).click(); const count=f.requests.length;
    f.confirm(false); await f.rotate(1).click();
    assert.equal(f.requests.length,count); assert.equal(f.get('issued').hidden,false);
    assert.match(f.get('issued-urls').value,/synthetic-token/); assert.equal(f.get('forget').disabled,false);
});
test('invalid rotation duration preserves visible token and does not send a request', async () => {
    const f = await fixture(); await f.rotate(1).click(); const count=f.requests.length;
    f.get('days').value='0'; await f.rotate(1).click();
    assert.equal(f.requests.length,count); assert.equal(f.get('issued').hidden,false);
    assert.match(f.get('notice').textContent,/有效天数/);
});
