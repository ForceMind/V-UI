'use strict';
// Actual controller and template; synthetic DOM events/fetch, not a browser,
// HTTP server, account, storage, core, or rendering/accessibility qualification.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const filename = process.argv[2] || path.join(__dirname, '../web/js/workspace.js');
const htmlname = process.argv[3] || path.join(__dirname, '../web/workspace.html');
const script = fs.readFileSync(filename, 'utf8'), html = fs.readFileSync(htmlname, 'utf8');
const AUTH = '/api/auth/me', CATALOG = '/api/routing/mihomo/catalog';
const ROUTING = '/api/routing/mihomo', SNAPSHOT = ROUTING + '/snapshot', PREVIEW = ROUTING + '/preview';
const NODES = '/api/inbounds', WARNINGS = '/api/subscription/mihomo-warnings', GRANTS = '/api/subscriptions';
const R1 = '1'.repeat(64), R2 = '2'.repeat(64);
const settings = {mode:'direct', direct_domains:[], proxy_domains:['saved.example.test'], presets:{openai:true}, bypass_cgnat:false, intranet:[]};
const catalog = {categories:[{id:'service', name_zh:'服务'}], presets:[{id:'openai', category:'service', name_zh:'OpenAI'}]};
const plan = (revision = R1) => ({revision, sections:[{comment:'saved', rules:['DOMAIN-SUFFIX,saved.example.test,PROXY']}], warnings:[]});
const response = (value, status = 200, revision = R1) => ({ok:status >= 200 && status < 300, status,
    json:async()=>structuredClone(value), headers:{get:key=>key === 'X-VUI-Node-Revision' ? 'a'.repeat(64) : key === 'ETag' ? '"' + revision + '"' : null}});
const deferred = () => {let resolve, reject; const promise = new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const flush = () => new Promise(resolve=>setImmediate(resolve));
async function bounded(promise) {
    let timer;
    try {return await Promise.race([promise, new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('controller control timed out')),2000);})]);}
    finally {clearTimeout(timer);}
}
class Element {
    constructor(tag) {this.tagName=tag.toUpperCase();this.value='';this.checked=false;this.disabled=false;this.hidden=false;this.dataset={};this.children=[];this.events=new Map();this._text='';this.attrs={};}
    get textContent() {return this._text + this.children.map(child=>child.textContent).join('');}
    set textContent(value) {this._text=String(value);this.children=[];}
    set innerHTML(value) {assert.fail('Unexpected unsafe HTML sink: '+value);}
    setAttribute(key,value) {
        this.attrs[key]=String(value);
        if(key.startsWith('data-'))this.dataset[key.slice(5)]=String(value);
        else if(['hidden','disabled','checked'].includes(key))this[key]=true;
        else if(key==='class')this.className=String(value);
        else this[key]=String(value);
    }
    addEventListener(type,fn) {this.events.set(type,[...(this.events.get(type)||[]),fn]);}
    async emit(type,event={preventDefault(){}}) {for(const fn of this.events.get(type)||[])await fn(event);}
    async click() {if(!this.disabled && visible(this))await this.emit('click');}
    append(...children) {for(const child of children){child.parent=this;this.children.push(child);}}
    replaceChildren(...children) {this._text='';this.children=[];this.append(...children);}
    remove() {this.parent.children=this.parent.children.filter(child=>child!==this);}
    querySelector(selector) {return descendants(this).find(child=>matches(child,selector))||null;}
}
const descendants = node => node.children.flatMap(child=>[child,...descendants(child)]);
const visible = node => Boolean(node && !node.hidden && (!node.parent || visible(node.parent)));
function matches(node,selector) {
    if(selector==='button')return node.tagName==='BUTTON';
    if(selector==='.zone')return node.className==='zone';
    if(selector==='[data-node]:checked')return Object.hasOwn(node.dataset,'node') && node.checked;
    if(selector==='input[name="format"]:checked')return node.name==='format' && node.checked;
    const attribute=/^\[data-([a-z]+)\]$/.exec(selector);
    return Boolean(attribute && Object.hasOwn(node.dataset,attribute[1]));
}
function template() {
    // Small structural HTML reader for this static template, not an HTML engine.
    // Preserve real IDs, attributes, and hidden ancestors; never invent an ID.
    const root=new Element('document'), stack=[root], ids=new Map();
    for(const token of html.match(/<!--[^]*?-->|<![^>]*>|<[^>]*>|[^<]+/g)) {
        if(token.startsWith('<!'))continue;
        if(token.startsWith('</')) {assert.equal(stack.pop().tagName,token.slice(2,-1).trim().toUpperCase());continue;}
        if(!token.startsWith('<')) {stack.at(-1).append(Object.assign(new Element('#text'),{textContent:token}));continue;}
        const tag=/^<([\w-]+)/.exec(token)[1], node=new Element(tag);
        for(const [,key,quoted,bare] of token.slice(tag.length+1,-1).matchAll(/([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|([^\s>]+)))?/g))node.setAttribute(key,quoted ?? bare ?? '');
        if(node.id){assert(!ids.has(node.id),'unique template ID');ids.set(node.id,node);}
        stack.at(-1).append(node);
        if(!['meta','link','img','input','br','hr'].includes(tag))stack.push(node);
    }
    assert.equal(stack.length,1);
    return {root,get:id=>ids.get(id)||null};
}
async function fixture(initial=[]) {
    const {root,get}=template(), requests=[], queued=[...initial], redirects=[], globals=new Map(), confirms=[];
    let confirmValue=true, saved=structuredClone(settings), revision=R1;
    const document={body:descendants(root).find(node=>node.tagName==='BODY'),getElementById:get,
        createElement:tag=>new Element(tag),createTextNode:text=>Object.assign(new Element('#text'),{textContent:text}),
        querySelectorAll:selector=>descendants(root).filter(node=>matches(node,selector))};
    const fetch=async(url,options={})=>{
        const method=options.method||'GET';requests.push({url,method,options});
        assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store');assert.equal(options.headers['X-VUI-Request'],'1');
        const index=queued.findIndex(item=>item.url===url && (item.method||'GET')===method);
        if(index>=0)return queued.splice(index,1)[0].reply(options);
        if(url===AUTH)return response({username:'synthetic'});
        if(url===CATALOG)return response(catalog);
        if(url===SNAPSHOT)return response({revision,settings:saved});
        if(url===PREVIEW)return response(plan(revision));
        if(url===ROUTING && method==='PUT'){saved=JSON.parse(options.body);revision=R2;return response(saved,200,revision);}
        if(url===NODES)return response([{id:7,remark:'synthetic',core:'sing-box',protocol:'vless',enable:true}]);
        if(url===WARNINGS || url===GRANTS && method==='GET')return response([]);
        assert.fail('Unexpected request: '+method+' '+url);
    };
    vm.runInNewContext(script,{document,fetch,location:{origin:'http://example.test',replace:url=>redirects.push(url),reload(){}},
        addEventListener:(name,fn)=>globals.set(name,[...(globals.get(name)||[]),fn]),confirm:text=>{confirms.push(text);return confirmValue;},
        setTimeout:()=>assert.fail('No automatic retry timer'),clearTimeout(){},
        localStorage:new Proxy({}, {get:()=>assert.fail('No persistent state')}),sessionStorage:new Proxy({}, {get:()=>assert.fail('No persistent state')})},{filename});
    await flush();
    const queue=(url,reply,method='GET')=>queued.push({url,reply,method});
    const hold=url=>{const gate=deferred(),started=deferred();queue(url,()=>{started.resolve();return gate.promise;});return {...gate,started:started.promise};};
    const click=id=>{assert(get(id),'actual template has '+id);return get(id).click();};
    return {get,document,requests,redirects,globals,confirms,queue,hold,click,confirm:value=>{confirmValue=value;},
        edit:async value=>{get('proxy').value=value;await get('routing-form').emit('input');},
        count:url=>requests.filter(item=>item.url===url).length};
}
const fail = (url,kind='http') => ({url,reply:async()=>{
    if(kind==='network')throw Error('synthetic network failure');
    if(kind==='json')return {...response(null),json:async()=>{throw Error('synthetic JSON failure');}};
    return response({detail:'synthetic HTTP failure'},503);
}});
function closed(f) {
    assert.equal(f.get('workspace').hidden,true,'uninitialized workspace stays hidden');
    for(const id of ['reload','preview','save','refresh-grants','create-grant'])assert.equal(visible(f.get(id)),false,id+' remains unreachable');
    assert.equal(f.count(NODES),0);assert.equal(f.count(GRANTS),0);
    assert.equal(f.get('issued').hidden,true);assert.equal(f.get('issued-urls').value,'');
    assert(f.requests.every(item=>item.method==='GET'),'recovery initialization is read-only');
}
function retryable(f) {
    closed(f);const retry=f.get('initial-retry');assert(retry,'initial read failure needs an actual page-local retry button');
    assert.equal(visible(retry),true,'retry is outside the hidden workspace');assert.equal(retry.disabled,false);
    assert.equal(retry.tagName,'BUTTON');assert.equal(retry.type,'button');assert.equal(retry.attrs['aria-describedby'],'notice');assert.equal(retry.attrs.tabindex,undefined,'ordinary native keyboard focus');
    assert.match(retry.textContent,/重试初次载入/);assert.equal((retry.events.get('click')||[]).length,1);
    assert.equal(f.get('notice').className,'error');
}
function ready(f) {
    assert.equal(f.get('workspace').hidden,false);assert.equal(f.get('initial-retry').hidden,true);
    assert.equal(f.get('proxy').value,settings.proxy_domains[0]);assert.equal(f.get('save').disabled,true);
    assert.equal(f.document.querySelectorAll('[data-preset]').length,1);assert.equal(f.get('presets').children.length,1);
    assert.equal(f.get('preview-label').textContent,'已保存规则预览');assert.equal(f.get('create-grant').disabled,false);
}
for(const url of [SNAPSHOT,PREVIEW])for(const kind of ['http','json','network']) {
    test(`initial ${url} ${kind} failure exposes read-only retry and explicit recovery`,async()=>{
        const f=await fixture([fail(url,kind)]);retryable(f);const count=f.requests.length;await flush();assert.equal(f.requests.length,count,'no automatic retry');
        await f.click('initial-retry');ready(f);assert(f.requests.every(item=>item.method==='GET'));
        assert.equal(f.count(AUTH),2);assert.equal(f.count(CATALOG),2);assert.equal(f.count(NODES),1);assert.equal(f.count(GRANTS),1);
    });
}
for(const url of [AUTH,CATALOG]) {
    test(`failed initial ${url} cannot skip authentication on explicit retry`,async()=>{
        const f=await fixture([fail(url)]);retryable(f);assert.equal(f.count(SNAPSHOT),0);
        await f.click('initial-retry');ready(f);assert.equal(f.count(AUTH),2);
        assert.deepEqual(f.requests.slice(0,f.requests.findIndex(item=>item.url===SNAPSHOT)).map(item=>item.url),url===AUTH?[AUTH,AUTH,CATALOG]:[AUTH,CATALOG,AUTH,CATALOG]);
    });
}
test('repeated failures rebuild one catalog and retain exactly one retry listener',async()=>{
    const f=await fixture([fail(PREVIEW)]);retryable(f);
    for(let n=0;n<2;n++){f.queue(PREVIEW,fail(PREVIEW).reply);await f.click('initial-retry');retryable(f);assert.equal(f.document.querySelectorAll('[data-preset]').length,1);assert.equal(f.get('presets').children.length,1);}
    await f.click('initial-retry');ready(f);assert.equal(f.count(AUTH),4);assert.equal(f.count(CATALOG),4);
    assert.equal((f.get('routing-form').events.get('input')||[]).length,1);assert.equal((f.globals.get('beforeunload')||[]).length,1);
});
for(const url of [AUTH,SNAPSHOT,PREVIEW]) {
    test(`retry stays visible but disabled and single-flight while ${url} is pending`,async()=>{
        const f=await fixture([fail(SNAPSHOT)]);retryable(f);const gate=f.hold(url), pending=f.click('initial-retry');await bounded(gate.started);
        closed(f);assert.equal(visible(f.get('initial-retry')),true);assert.equal(f.get('initial-retry').disabled,true);assert.match(f.get('notice').textContent,/正在.*载入/);
        const count=f.requests.length;await f.click('initial-retry');await f.get('initial-retry').emit('click');assert.equal(f.requests.length,count,'both disabled activation and direct duplicate listener are rejected synchronously');
        gate.resolve(response(url===AUTH?{username:'synthetic'}:url===SNAPSHOT?{revision:R1,settings}:plan()));await bounded(pending);ready(f);
    });
}
for(const revision of [R2,'1'.repeat(63)]) {
    test(`initial preview revision ${String(revision)} stays closed until a valid pair`,async()=>{
        const f=await fixture([{url:PREVIEW,reply:()=>response(plan(revision))}]);
        retryable(f);assert.match(f.get('notice').textContent,/版本不一致或无法确认/);await f.click('initial-retry');ready(f);
    });
}
test('missing initial saved-preview revision stays closed until valid pair',async()=>{
    const value=plan();delete value.revision;const f=await fixture([{url:PREVIEW,reply:()=>response(value)}]);retryable(f);await f.click('initial-retry');ready(f);
});
for(const url of [AUTH,CATALOG,SNAPSHOT,PREVIEW]) {
    test(`retried ${url} 401 redirects without reopening retry or write controls`,async()=>{
        const f=await fixture([fail(SNAPSHOT)]);retryable(f);f.queue(url,()=>response({detail:'expired'},401));await f.click('initial-retry');
        closed(f);assert.deepEqual(f.redirects,['/login']);assert.equal(f.get('initial-retry').hidden,true);assert.match(f.get('notice').textContent,/登录已过期/);
        const count=f.requests.length;await f.get('initial-retry').emit('click');assert.equal(f.requests.length,count,'401 latch rejects stale initial retry');
    });
}
for(const url of [NODES,GRANTS]) {
    test(`failure after initial reveal at ${url} uses existing node/grant refresh only`,async()=>{
        const f=await fixture([fail(url)]);assert.equal(f.get('workspace').hidden,false);assert.equal(f.get('initial-retry').hidden,true);
        assert.equal(visible(f.get('refresh-grants')),true);const count=f.requests.length;await f.get('initial-retry').emit('click');assert.equal(f.requests.length,count);
        await f.click('refresh-grants');ready(f);assert.equal(f.count(AUTH),1);assert.equal(f.count(SNAPSHOT),1);
    });
}
test('successful initialization cannot be reentered over an active draft; normal confirmation and If-Match remain',async()=>{
    const f=await fixture();ready(f);await f.edit('draft.example.test');const count=f.requests.length;
    await f.get('initial-retry').emit('click');assert.equal(f.requests.length,count);assert.equal(f.get('proxy').value,'draft.example.test');
    f.confirm(false);await f.click('reload');assert.equal(f.requests.length,count);assert.equal(f.confirms.length,1);
    f.confirm(true);f.queue(PREVIEW,()=>response(plan(R2)));await f.click('reload');
    assert.equal(f.get('initial-retry').hidden,true,'active reload mismatch never reopens initial retry');assert.equal(f.get('workspace').hidden,false);
    assert.match(f.get('notice').textContent,/版本不一致或无法确认/);assert.equal(f.get('proxy').value,'draft.example.test');
    await f.click('save');assert.equal(f.requests.find(item=>item.method==='PUT').options.headers['If-Match'],'"'+R1+'"');assert.equal(f.get('proxy').value,'draft.example.test');assert.equal(f.get('save').disabled,true);
});

for(const url of [AUTH,PREVIEW]) {
    test(`first-load ${url} 401 never offers initial recovery`,async()=>{
        const f=await fixture([{url,reply:()=>response({detail:'expired'},401)}]);closed(f);assert.deepEqual(f.redirects,['/login']);
        assert(f.get('initial-retry'),'actual template retry control exists');assert.equal(f.get('initial-retry').hidden,true);
        assert.match(f.get('notice').textContent,/登录已过期/);const count=f.requests.length;await f.get('initial-retry').emit('click');assert.equal(f.requests.length,count);
    });
}
