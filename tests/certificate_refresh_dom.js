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
    append(...children) {for (const child of children) {
        if (child.parentNode) child.parentNode.children = child.parentNode.children.filter(node=>node !== child);
        child.parentNode = this; this.children.push(child);
    }}
    replaceChildren(...children) {for (const child of this.children) child.parentNode = null; this.children = []; this.append(...children);}
    addEventListener(event, fn) {(this.listeners[event] ||= []).push(fn);}
    click() {if (!this.disabled) for (const fn of this.listeners.click || []) fn({target:this, preventDefault(){}});}
    submit() {for (const fn of this.listeners.submit || []) fn({target:this, preventDefault(){}});}
    focus() {this.focused = true;}
    reset() {for (const input of this.querySelectorAll('input')) {input.value = input.attributes.value || ''; input.checked = false;}}
    set innerHTML(value) {throw new Error('Controller must use textContent for server metadata');}
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
async function fixture(certs = [certificate()], options = {}) {
    const root = new Element('main'), ids = {}, requests = [], pending = [], timers = new Map(), events = {};
    for (const [id, tag] of Object.entries({'notice':'p', 'request-certificate':'form',
        'certificate-list':'div', 'refresh':'button', 'cert-workspace':'div', 'domain':'input',
        'email':'input', 'environment':'select', 'auto-renew':'input', 'terms':'input',
        'method':'select', 'dns-method':'option', 'dns-options':'div', 'http-guide':'p',
        'dns-profile':'select', 'coverage':'select', 'dns-names':'p', 'dns-credentials':'details',
        'dns-credential-list':'div', 'dns-credential-form':'form', 'dns-form-title':'h3',
        'dns-label':'input', 'dns-zone':'input', 'dns-token':'input', 'save-dns-profile':'button',
        'cancel-dns-edit':'button', 'add-dns-profile':'button', 'dns-challenges':'section', 'dns-challenge-list':'div'})) {
        ids[id] = new Element(tag); root.append(ids[id]);
    }
    for (const id of ['dns-label','dns-zone','dns-token','save-dns-profile','cancel-dns-edit']) ids['dns-credential-form'].append(ids[id]);
    const option=(value,label)=>{const node=new Element('option');node.setAttribute('value',value);node.textContent=label;return node;};
    ids.method.append(option('http-01','HTTP-01'),ids['dns-method']);ids['dns-method'].setAttribute('value','dns-01');
    ids.coverage.append(option('wildcard','根域名和一级子域名'),option('single','单域名'));
    ids.environment.append(option('production','正式'),option('staging','测试'));
    ids['auto-renew'].checked=true;
    const profiles=options.profiles || [], challenges=options.challenges || [];
    const capabilities={worker_running:true,panel_hot_reload:true,dns01:!!options.admin,dns_credentials_manage:!!options.admin,...options.capabilities};
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
        if (url === '/api/certificates/capabilities') return Promise.resolve(response(capabilities));
        if (url === '/api/inbounds') return Promise.resolve(response(nodes));
        const gate = deferred(); pending.push({...request, ...gate}); return gate.promise;
    };
    let confirmResult = true, reloads = 0; const confirms = [], redirects = [];
    vm.runInNewContext(script, {document, fetch, Date, URL,
        location:{hostname:options.hostname || 'panel.example.test',replace:url=>redirects.push(url), reload:()=>reloads++},
        confirm:message=>{confirms.push(message); return confirmResult;},
        setInterval:(fn, ms)=>{assert.equal(ms, 5000); timers.set(1, fn); return 1;},
        clearInterval:id=>timers.delete(id), addEventListener:(event, fn)=>events[event] = fn}, {filename});
    const take = (method = 'GET', url) => {
        const index = pending.findIndex(r=>r.method === method && (url === undefined || r.url === url));
        assert.notEqual(index, -1, 'Expected pending ' + method + ' request');
        return pending.splice(index, 1)[0];
    };
    const card = id=>ids['certificate-list'].querySelectorAll('[data-certificate]').find(n=>n.dataset.certificate === id);
    const button = (label, id = 'synthetic-a')=>card(id).querySelectorAll('button').find(n=>n.textContent === label);
    const select = (id = 'synthetic-a')=>card(id).querySelectorAll('[data-bind-select]')[0];
    const finishReads=async(nextCerts=certs,nextProfiles=profiles,nextChallenges=challenges)=>{
        take('GET','/api/certificates').resolve(response(nextCerts));await flush();
        if(capabilities.dns_credentials_manage){
            take('GET','/api/certificates/dns-credentials').resolve(response(nextProfiles));await flush();
            take('GET','/api/certificates/dns-challenges').resolve(response(nextChallenges));await flush();
        }
    };
    await flush(); await finishReads();
    assert.equal(ids.refresh.disabled, false, 'initial action has completed');
    return {ids, root, document, requests, pending, take, card, button, select, confirms, redirects, finishReads,
        host:(id='synthetic-a')=>card(id).querySelectorAll('[data-bind-host]')[0],
        challenge:id=>ids['dns-challenge-list'].querySelectorAll('[data-dns-challenge]').find(node=>node.dataset.dnsChallenge===id),
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
    assert.equal(f.ids.notice.textContent, '读取失败，请刷新后重试。'); assert.equal(f.ids.notice.className, 'error');
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
test('DNS maintenance view cannot trigger renewals and DNS errors do not blame port80', async()=>{
    const cert=certificate('synthetic-dns',{method:'dns-01',wildcard:true,
        names:['example.test','*.example.test'],error:'ACME_VALIDATION_FAILED'});
    const f=await fixture([cert]);
    assert.equal(f.button('检查续期',cert.id).disabled,true);
    assert.equal(f.button('暂停自动续期',cert.id).disabled,true);
    assert.equal(f.ids['dns-method'].disabled,true);
    assert.equal(f.ids['dns-credentials'].hidden,true);
    const card=f.card(cert.id);
    assert.match(card.textContent,/DNS-01.*根域名和一级子域名/);
    assert.match(card.textContent,/TXT传播/);assert.doesNotMatch(card.textContent,/80端口/);
    f.button('检查续期',cert.id).click();await flush();
    assert.equal(f.requests.filter(r=>r.method==='POST').length,0);
});

const dnsCertificate=(changes={})=>certificate('synthetic-dns',{method:'dns-01',wildcard:true,
    names:['example.test','*.example.test'],...changes});
const profile=(changes={})=>({id:'1'.repeat(32),label:'合成凭据',zone_name:'example.test',
    revision:'a'.repeat(32),enabled:true,restore_pending:false,status:'configured',references:1,...changes});
const challenge=(changes={})=>({job_id:'2'.repeat(32),credential_id:'1'.repeat(32),domain:'example.test',
    zone_name:'example.test',phase:'cleanup_pending',error:'DNS_CHALLENGE_CREATE_UNKNOWN',revision:'b'.repeat(32),
    count:1,created_at:1800000000,updated_at:1800000001,deadline:1800000900,...changes});
const challengeButton=(f,label,id='2'.repeat(32))=>f.challenge(id).querySelectorAll('button').find(node=>node.textContent===label);

test('DNS connection hostname drafts survive in-flight edits and clearing; only literal covered SNI is sent', async()=>{
    const cert=dnsCertificate(), f=await fixture([cert]);
    assert.equal(f.host(cert.id).value,'panel.example.test','covered current hostname is the first suggestion');
    assert.match(f.card(cert.id).textContent,/实际连接域名.*A\/AAAA.*SNI/);
    f.select(cert.id).value='7';f.tick();await flush();
    const json=deferred();f.take().resolve({ok:true,status:200,json:()=>json.promise});await flush();
    f.host(cert.id).value='draft.example.test';f.select(cert.id).value='8';json.resolve([cert]);await flush();
    assert.equal(f.host(cert.id).value,'draft.example.test');assert.equal(f.select(cert.id).value,'8');
    f.tick();await flush();const old=f.take();f.host(cert.id).value='';old.resolve(response([cert]));await flush();
    assert.equal(f.host(cert.id).value,'','an intentionally empty draft must not be refilled on a poll');
    for(const host of ['', '*.example.test', 'deep.node.example.test']){
        f.host(cert.id).value=host;f.button('绑定节点',cert.id).click();await flush();
        assert.equal(f.requests.filter(request=>request.method==='POST').length,0);
        assert.match(f.ids.notice.textContent,/实际连接域名|连接域名格式|覆盖范围/);
    }
    f.host(cert.id).value='NODE.EXAMPLE.TEST.';f.button('绑定节点',cert.id).click();await flush();
    const bind=f.take('POST');assert.deepEqual(bind.body,{inbound_id:8,server_name:'node.example.test'});
    bind.resolve(response({}));await flush();await f.finishReads([cert]);
    const uncovered=await fixture([dnsCertificate({names:['other.test','*.other.test']}),certificate()]);
    assert.equal(uncovered.host(cert.id).value,'');assert.equal(uncovered.host(),undefined,'HTTP bindings keep the original shape');
});

test('administrator credential save clears Token, never repeats a write, and polling preserves a new Token draft', async()=>{
    const p=profile(),f=await fixture([],{admin:true});
    const token='synthetic-DOM-Token-only-value';
    f.ids['dns-label'].value='合成凭据';f.ids['dns-zone'].value='example.test';f.ids['dns-token'].value=token;
    f.ids['dns-credential-form'].submit();await flush();const save=f.take('POST');
    assert.equal(save.url,'/api/certificates/dns-credentials');
    assert.ok(save.body.api_token===token,'only the request payload receives the temporary Token');
    assert.equal(f.ids['dns-token'].value,'');assert.equal(f.ids['dns-token'].disabled,true);
    f.ids['dns-credential-form'].submit();await flush();assert.equal(f.requests.filter(request=>request.method==='POST').length,1);
    save.resolve(response(p,201));await flush();await f.finishReads([],[p],[]);
    assert.equal(f.ids['dns-token'].value,'');assert.equal(f.ids['dns-token'].disabled,false);
    const edit=f.ids['dns-credential-list'].querySelectorAll('button').find(node=>node.textContent==='替换Token');
    edit.click();await flush();f.ids['dns-token'].value='new-user-draft-not-a-repeated-write';
    await f.finishReads([],[p],[]);
    assert.equal(f.ids['dns-token'].value,'new-user-draft-not-a-repeated-write','readback must not overwrite user editing');
    f.tick();await flush();await f.finishReads([],[p],[]);
    assert.equal(f.ids['dns-token'].value,'new-user-draft-not-a-repeated-write');
    f.ids['dns-credential-form'].submit();await flush();const replace=f.take('PUT');
    assert.equal(replace.body.expected_revision,p.revision);replace.reject(new Error('private network detail must not escape'));
    await flush();assert.equal(f.ids['dns-token'].value,'');assert.match(f.ids.notice.textContent,/写入结果.*先刷新/);
    assert.doesNotMatch(f.ids.notice.textContent,/private|network/);
    f.ids['dns-credential-form'].submit();await flush();
    assert.equal(f.requests.filter(request=>request.method==='PUT').length,1,'unknown results are read back before any next write');
    assert.equal(f.pending.length,0,'write failure does not automatically resubmit or pretend a read succeeded');
    assert.ok(f.requests.every(request=>!request.url.includes(token)));
    assert.ok(f.root.querySelectorAll('*').every(node=>Object.values(node.attributes).every(value=>!value.includes(token))));
});

test('disabled profile permits queued cleanup; unresolved absence stays pending and manual confirmation is explicit', async()=>{
    const p=profile({enabled:false,status:'disabled'}),job=challenge(),f=await fixture([],{admin:true,profiles:[p],challenges:[job]});
    const cleanup=challengeButton(f,'使用当前凭据清理验证记录');assert.equal(cleanup.disabled,false);
    cleanup.click();await flush();const write=f.take('POST');
    assert.equal(write.url,'/api/certificates/dns-challenges/'+job.job_id+'/cleanup');
    assert.deepEqual(write.body,{expected_revision:job.revision,expected_profile_revision:p.revision,confirmed:true});
    cleanup.click();f.tick();await flush();assert.equal(f.requests.filter(request=>request.method==='POST').length,1);
    write.resolve(response({queued:true,phase:'cleanup_pending'},202));await flush();
    const later={...job,revision:'c'.repeat(32)};await f.finishReads([],[p],[later]);
    assert.match(f.ids.notice.textContent,/清理任务已受理.*后台/);assert.doesNotMatch(f.ids.notice.textContent,/已自动清理/);
    assert.match(f.challenge(job.job_id).textContent,/待清理/);
    assert.match(f.challenge(job.job_id).textContent,/查无记录不等于自动清理成功/);
    f.confirm(false);challengeButton(f,'我已在Cloudflare确认没有残留').click();await flush();await f.finishReads([],[p],[later]);
    assert.equal(f.requests.filter(request=>request.method==='POST').length,1);
    f.confirm(true);challengeButton(f,'我已在Cloudflare确认没有残留').click();await flush();
    const manual=f.take('POST');assert.ok(manual.url.endsWith('/confirm-absence'));
    assert.deepEqual(manual.body,{expected_revision:later.revision,expected_profile_revision:p.revision,confirmed:true});
    assert.match(f.confirms.at(-1),/人工确认.*不代表系统自动清理成功/);
    manual.resolve(response({phase:'manual_confirmed'}));await flush();await f.finishReads([],[p],[]);
    assert.match(f.ids.notice.textContent,/这是人工确认，不是系统自动清理成功/);
    assert.equal(f.requests.filter(request=>request.method==='POST'&&request.url==='/api/certificates').length,0,'cleanup never reissues');
});

test('restore-pending profile blocks both cleanup writes and guides explicit verification first', async()=>{
    const p=profile({enabled:false,restore_pending:true,status:'restore_pending'}),job=challenge();
    const f=await fixture([],{admin:true,profiles:[p],challenges:[job]});
    for(const label of ['使用当前凭据清理验证记录','我已在Cloudflare确认没有残留']){
        const button=challengeButton(f,label);assert.equal(button.disabled,true);button.click();
    }
    await flush();assert.equal(f.requests.filter(request=>request.method==='POST').length,0);
    challengeButton(f,'先验证并启用凭据').click();await flush();await f.finishReads([],[p],[job]);
    assert.equal(f.ids['dns-credentials'].open,true);assert.match(f.ids.notice.textContent,/先|验证并启用/);
    assert.equal(f.requests.filter(request=>request.method==='POST').length,0,'guidance cannot enable or clean automatically');
});

test('DNS maintenance never reads or manages admin metadata while allowing existing material binding', async()=>{
    const cert=dnsCertificate(),f=await fixture([cert],{capabilities:{dns01:true,dns_credentials_manage:false}});
    assert.equal(f.ids['dns-challenges'].hidden,true);
    assert.equal(f.ids['dns-challenge-list'].querySelectorAll('button').length,0);
    assert.equal(f.ids['dns-credential-list'].querySelectorAll('button').length,0);
    f.tick();await flush();await f.finishReads([cert]);
    assert.equal(f.requests.filter(request=>/\/dns-(?:credentials|challenges)/.test(request.url)).length,0);
    f.ids['dns-token'].value='synthetic-hidden-input';f.ids['dns-credential-form'].submit();await flush();
    assert.equal(f.ids['dns-token'].value,'');assert.match(f.ids.notice.textContent,/当前授权不允许/);
    assert.equal(f.requests.filter(request=>request.method!=='GET').length,0);
    f.select(cert.id).value='7';f.button('绑定节点',cert.id).click();await flush();
    const bind=f.take('POST');assert.deepEqual(bind.body,{inbound_id:7,server_name:'panel.example.test'});
    bind.resolve(response({}));await flush();await f.finishReads([cert]);
    assert.equal(f.requests.filter(request=>/\/dns-(?:credentials|challenges)/.test(request.url)).length,0);
});

test('challenge CAS conflict is Chinese, blocks blind repetition, and fresh readback selects the new revision', async()=>{
    const p=profile(),job=challenge(),f=await fixture([],{admin:true,profiles:[p],challenges:[job]});
    challengeButton(f,'使用当前凭据清理验证记录').click();await flush();
    f.take('POST').resolve(response({detail:'DNS_CHALLENGE_REVISION_CONFLICT'},409));await flush();
    assert.match(f.ids.notice.textContent,/验证记录已被其他操作更新.*刷新/);
    challengeButton(f,'使用当前凭据清理验证记录').click();await flush();
    assert.equal(f.requests.filter(request=>request.method==='POST').length,1);
    f.ids.refresh.click();await flush();const next={...job,revision:'d'.repeat(32)};await f.finishReads([],[p],[next]);
    challengeButton(f,'使用当前凭据清理验证记录').click();await flush();const fresh=f.take('POST');
    assert.equal(fresh.body.expected_revision,next.revision);
    fresh.resolve(response({detail:{raw:'private object must never be displayed'}},403));await flush();
    assert.match(f.ids.notice.textContent,/当前授权不允许此操作/);assert.doesNotMatch(f.ids.notice.textContent,/private|object|\[object/);
});

test('current JSON failure is fixed Chinese and 401 retains the login redirect', async()=>{
    const f=await fixture([certificate('synthetic-a',{error:'constructor'})]);f.select().value='7';const card=f.card('synthetic-a');
    assert.doesNotMatch(card.textContent,/native code|function Object/,'unknown codes cannot read inherited object properties');
    f.tick();await flush();f.take().resolve({ok:true,status:200,json:async()=>{throw new Error('raw JSON parser detail');}});await flush();
    assert.equal(f.ids.notice.textContent,'读取失败，请刷新后重试。');assert.equal(f.card('synthetic-a'),card);assert.equal(f.select().value,'7');
    f.button('暂停自动续期').click();await flush();f.take('PUT').resolve(response({detail:'constructor'},409));await flush();
    assert.equal(f.ids.notice.textContent,'输入或状态不符合要求，请刷新核对后再操作。');
    f.tick();await flush();f.take().resolve(response({detail:'never echo'},401));await flush();
    assert.deepEqual(f.redirects,['/login']);assert.doesNotMatch(f.ids.notice.textContent,/never echo/);
});

test('pagehide during admin metadata read keeps old DOM, clears Token, and starts no later request', async()=>{
    const p=profile(),job=challenge(),cert=dnsCertificate(),f=await fixture([cert],{admin:true,profiles:[p],challenges:[job]});
    const card=f.card(cert.id);f.ids['dns-token'].value='synthetic-private-draft';f.tick();await flush();
    f.take('GET','/api/certificates').resolve(response([cert]));await flush();
    const pending=f.take('GET','/api/certificates/dns-credentials'),count=f.requests.length;
    f.events.pagehide();assert.equal(f.ids['dns-token'].value,'');pending.resolve(response([p]));await flush();
    assert.equal(f.requests.length,count,'pagehide must prevent the next challenge GET in the chain');
    assert.equal(f.card(cert.id),card);f.tick();await flush();assert.equal(f.requests.length,count);
});
