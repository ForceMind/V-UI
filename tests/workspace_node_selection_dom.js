'use strict';
// Run the actual controller with deterministic DOM/fetch substitutes only.
// No browser, HTTP, real node mutation, database, credentials or core qualification.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const filename = process.argv[2] || path.join(__dirname, '../web/js/workspace.js');
const script = fs.readFileSync(filename, 'utf8');
const NODES = '/api/inbounds', WARNINGS = '/api/subscription/mihomo-warnings', GRANTS = '/api/subscriptions';
const REVISION = 'a'.repeat(64), REPLACEMENT = 'b'.repeat(64);
const CONFLICT = 'Subscription node selection changed; refresh nodes and select them again';
const settings = {mode:'direct', direct_domains:[], proxy_domains:[], presets:{}, bypass_cgnat:false, intranet:[]};
const node = (id = 7, remark = 'original instance', changes = {}) => ({id, remark, core:'sing-box', protocol:'vless', enable:true, ...changes});
const grant = () => ({id:1, label:'synthetic grant', server:'node.example.test', inbound_ids:[7], formats:['mihomo.yaml'], expires_at:2000000000,
    revoked:false, expired:false, invalidated:false, invalidation_reason:null});
const token = () => ({id:1, paths:{'mihomo.yaml':'/sub/synthetic-token/mihomo.yaml'}});
const response = (value, status = 200, revision = REVISION) => ({ok:status >= 200 && status < 300, status,
    json:async()=>structuredClone(value), headers:{get:key=>key === 'X-VUI-Node-Revision' ? revision : key === 'ETag' ? '"r1"' : null}});
const deferred = () => {let resolve, reject; const promise = new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const flush = () => new Promise(resolve=>setImmediate(resolve));
async function bounded(promise, label) {
    let timer;
    try {return await Promise.race([promise, new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(label + ' timed out')), 2000);})]);}
    finally {clearTimeout(timer);}
}
class Element {
    constructor(tag = 'div') {this.tagName=tag.toUpperCase();this.value='';this.checked=false;this.disabled=false;this.hidden=false;this.dataset={};this.children=[];this.events=new Map();this._text='';}
    get textContent() {return this._text + this.children.map(child=>child.textContent).join('');}
    set textContent(value) {this._text=String(value);this.children=[];}
    set innerHTML(value) {assert.fail('Unsafe HTML sink: ' + value);}
    addEventListener(type, fn) {this.events.set(type,[...(this.events.get(type)||[]),fn]);}
    async emit(type, event = {preventDefault(){}}) {for(const fn of this.events.get(type)||[]) await fn(event);}
    async click() {if (!this.disabled) await this.emit('click');}
    setAttribute(key, value) {if(key.startsWith('data-')) this.dataset[key.slice(5)]=String(value);else if(key==='class')this.className=String(value);else this[key]=String(value);}
    append(...children) {for(const child of children){child.parent=this;this.children.push(child);}}
    replaceChildren(...children) {this._text='';this.children=[];this.append(...children);}
    querySelector(selector) {return descendants(this).find(child=>matches(child,selector))||null;}
    remove() {this.parent.children=this.parent.children.filter(child=>child!==this);}
}
const descendants = el => el.children.flatMap(child=>[child,...descendants(child)]);
function matches(el, selector) {
    if(selector==='button')return el.tagName==='BUTTON';
    if(selector==='.zone')return el.className==='zone';
    if(selector==='[data-node]:checked')return Object.hasOwn(el.dataset,'node') && el.checked;
    if(selector==='input[name="format"]:checked')return el.name==='format' && el.checked;
    const attribute=/^\[data-([a-z]+)(?:="([^"]*)")?\]$/.exec(selector);
    return Boolean(attribute && Object.hasOwn(el.dataset,attribute[1]) && (attribute[2]===undefined || el.dataset[attribute[1]]===attribute[2]));
}
async function fixture({revision = REVISION, nodeValues = [node()], warningValues = []} = {}) {
    const elements=new Map(), requests=[], queued=[], globalEvents=new Map(), redirects=[];
    const buttons=['reload','preview','save','add-zone','refresh-grants','create-grant','forget','copy','download'];
    const get=id=>{if(!elements.has(id))elements.set(id,new Element(buttons.includes(id)?'button':'div'));return elements.get(id);};
    buttons.forEach(get);get('workspace').hidden=true;get('issued').hidden=true;get('days').value='30';get('label').value='synthetic';get('server').value='node.example.test';
    const format=get('synthetic-format');format.name='format';format.value='mihomo.yaml';format.checked=true;
    const document={getElementById:get,createElement:tag=>new Element(tag),createTextNode:text=>Object.assign(new Element('#text'),{textContent:text}),
        querySelectorAll:selector=>[...elements.values()].flatMap(el=>[el,...descendants(el)]).filter(el=>matches(el,selector))};
    const fetch=async(url,options={})=>{
        const method=options.method||'GET';requests.push({url,method,options});
        assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store');assert.equal(options.headers['X-VUI-Request'],'1');
        const index=queued.findIndex(item=>item.url===url && item.method===method);
        if(index>=0)return queued.splice(index,1)[0].reply(options);
        if(url==='/api/auth/me')return response({username:'synthetic'});
        if(url==='/api/routing/mihomo/catalog')return response({categories:[],presets:[]});
        if(url==='/api/routing/mihomo/snapshot')return response({revision:'r1',settings});
        if(url==='/api/routing/mihomo/preview')return response({sections:[],warnings:[]});
        if(url===NODES)return response(nodeValues,200,revision);
        if(url===WARNINGS)return response(warningValues);
        if(url===GRANTS && method==='GET')return response([grant()]);
        if(url===GRANTS && method==='POST')return response(token(),201);
        if(url===GRANTS+'/1/rotate' && method==='POST')return response(token());
        if(url===GRANTS+'/1' && method==='DELETE')return response({});
        assert.fail('Unexpected request: '+method+' '+url);
    };
    vm.runInNewContext(script,{document,fetch,location:{origin:'http://example.test',replace:url=>redirects.push(url),reload(){}},
        addEventListener:(type,fn)=>globalEvents.set(type,fn),confirm:()=>true,setTimeout,clearTimeout},{filename});
    await flush();
    const queue=(url,reply,method='GET')=>queued.push({url,reply,method});
    const hold=(url,method='GET')=>{const gate=deferred(),started=deferred();queue(url,options=>{started.resolve(options);return gate.promise;},method);return {...gate,started:bounded(started.promise,url)};};
    const checkboxes=()=>document.querySelectorAll('[data-node]');
    const select=()=>{assert(checkboxes().length,'a displayed node exists');checkboxes().forEach(el=>{if(!el.disabled)el.checked=true;});};
    const submit=async()=>{await get('grant-form').emit('submit');await flush();};
    const posts=()=>requests.filter(item=>item.url===GRANTS && item.method==='POST');
    const reads=url=>requests.filter(item=>item.url===url && item.method==='GET');
    return {get,document,requests,queue,hold,checkboxes,select,submit,posts,reads,redirects,click:id=>get(id).click(),pagehide:()=>globalEvents.get('pagehide')({persisted:true})};
}
function assertInvalid(f) {
    assert.equal(f.get('create-grant').disabled,true,'creation remains disabled without a current node revision');
    f.checkboxes().forEach(el=>{assert.equal(el.checked,false);assert.equal(el.disabled,true);});
}

test('workspace create always transmits the revision of the displayed nodes', async()=>{
    const f=await fixture();f.select();await f.submit();
    assert.equal(f.posts().length,1);assert.equal(JSON.parse(f.posts()[0].options.body).expected_node_revision,REVISION);
    assert.deepEqual(JSON.parse(f.posts()[0].options.body).inbound_ids,[7]);
    assert.equal(f.reads(NODES).length,1,'post-create list refresh does not silently adopt another inventory');
});
for(const badRevision of [null,'','A'.repeat(64),'a'.repeat(63),'g'.repeat(64),' '+REVISION,REVISION+'\n']) {
    test('missing or invalid node revision fails closed: '+JSON.stringify(badRevision),async()=>{
        const f=await fixture({revision:badRevision});assertInvalid(f);
        // A synthetic submit bypasses disabled-button activation to exercise the handler guard.
        const forged=new Element('input');forged.dataset.node='7';forged.checked=true;f.get('nodes').append(forged);
        await f.submit();assert.equal(f.posts().length,0);assert.match(f.get('notice').textContent,/刷新节点和订阅/);
    });
}
test('stale displayed ID cannot authorize a conceptually deleted and replaced instance',async()=>{
    const f=await fixture();f.select();const old=f.checkboxes()[0];
    f.queue(GRANTS,async options=>{assert.equal(JSON.parse(options.body).expected_node_revision,REVISION);return response({detail:CONFLICT},409);},'POST');
    await f.submit();assert.equal(f.posts().length,1);assertInvalid(f);
    assert.match(f.get('notice').textContent,/刷新节点和订阅.*重新选择节点/);
    assert.equal(f.reads(NODES).length,1,'conflict never silently reloads or retries');assert.equal(f.get('issued').hidden,true);
    old.checked=true;await f.submit();assert.equal(f.posts().length,1,'submit guard blocks stale programmatic selection');
    f.queue(NODES,async()=>response([node(7,'replacement instance')],200,REPLACEMENT));
    await f.click('refresh-grants');assert.notEqual(f.checkboxes()[0],old);assert.equal(f.checkboxes()[0].checked,false);
    assert.match(f.get('nodes').textContent,/replacement instance/);await f.submit();assert.equal(f.posts().length,1,'refresh does not reselect an equal numeric ID');
    f.select();await f.submit();assert.equal(f.posts().length,2);assert.equal(JSON.parse(f.posts()[1].options.body).expected_node_revision,REPLACEMENT);
});
test('unrelated 409 retains its own meaning without claiming node selection changed',async()=>{
    const f=await fixture();f.select();f.queue(GRANTS,async()=>response({detail:'synthetic routing not ready'},409),'POST');
    await f.submit();assert.equal(f.posts().length,1);assert.match(f.get('notice').textContent,/synthetic routing not ready/);
    assert.doesNotMatch(f.get('notice').textContent,/节点列表已变化/);assert.equal(f.checkboxes()[0].checked,true);assert.equal(f.get('create-grant').disabled,false);
});
test('explicit refresh invalidates immediately and commits nodes with their revision only after warnings',async()=>{
    const f=await fixture();f.select();const old=f.checkboxes()[0],gate=f.hold(WARNINGS),start=f.requests.length;
    f.queue(NODES,async()=>response([node(7,'replacement instance')],200,REPLACEMENT));
    const pending=f.click('refresh-grants');await gate.started;await flush();assertInvalid(f);
    assert.equal(f.checkboxes()[0],old,'uncommitted response body cannot replace displayed node frame');
    await f.submit();assert.equal(f.posts().length,0);gate.resolve(response([]));await pending;
    assert.equal(f.checkboxes()[0].checked,false);assert.equal(f.get('create-grant').disabled,false);
    assert.deepEqual(f.requests.slice(start).map(item=>item.url).sort(),[NODES,WARNINGS,GRANTS].sort());
    f.select();await f.submit();assert.equal(JSON.parse(f.posts()[0].options.body).expected_node_revision,REPLACEMENT);
});
for(const failure of ['node request','node body','missing header','invalid header','warnings request','warnings body','node shape','warnings shape']) {
    test('failed explicit refresh keeps old selections invalid: '+failure,async()=>{
        const f=await fixture();f.select();const old=f.checkboxes()[0];
        if(failure==='node request')f.queue(NODES,async()=>{throw Error('synthetic node failure');});
        if(failure==='node body')f.queue(NODES,async()=>({...response([]),json:async()=>{throw Error('synthetic body failure');}}));
        if(failure==='missing header')f.queue(NODES,async()=>response([node(7,'replacement instance')],200,null));
        if(failure==='invalid header')f.queue(NODES,async()=>response([node(7,'replacement instance')],200,'BAD'));
        if(failure==='warnings request')f.queue(WARNINGS,async()=>response({detail:'synthetic warning failure'},503));
        if(failure==='node shape')f.queue(NODES,async()=>response({nodes:[node()]}));
        if(failure==='warnings shape')f.queue(WARNINGS,async()=>response({warnings:[]}));
        if(failure==='warnings body')f.queue(WARNINGS,async()=>({...response([]),json:async()=>{throw Error('synthetic warnings body failure');}}));
        await f.click('refresh-grants');assertInvalid(f);assert.equal(f.checkboxes()[0],old);assert.equal(f.get('notice').className,'error');
        old.checked=true;old.disabled=false;await f.submit();assert.equal(f.posts().length,0,'no fallback to unconditional API');
        assert.equal(f.reads(NODES).length,2,'failed reads are not automatically retried');
    });
}
for(const boundary of ['node body','warnings']) {
    test('pagehide invalidates a late refresh at '+boundary,async()=>{
        const f=await fixture();f.select();const gate=deferred();
        if(boundary==='node body')f.queue(NODES,async()=>({...response([],200,REPLACEMENT),json:()=>gate.promise}));
        else f.queue(WARNINGS,()=>gate.promise);
        const pending=f.click('refresh-grants');await flush();f.pagehide();
        gate.resolve(boundary==='node body'?[node(7,'late replacement')]:response([]));await pending;assertInvalid(f);
        assert.doesNotMatch(f.get('nodes').textContent,/late replacement/);await f.submit();assert.equal(f.posts().length,0);
    });
}
test('401 while node refresh is pending cannot restore creation after redirect',async()=>{
    const f=await fixture();const gate=deferred();f.queue(NODES,()=>gate.promise);f.queue(WARNINGS,async()=>response({},401));
    const pending=f.click('refresh-grants');await flush();assert.deepEqual(f.redirects,['/login']);assertInvalid(f);
    gate.resolve(response([node(7,'late replacement')],200,REPLACEMENT));await pending;await flush();assertInvalid(f);
    assert.doesNotMatch(f.get('nodes').textContent,/late replacement/);await f.submit();assert.equal(f.posts().length,0);
});
test('create rotate and revoke refresh only grants and keep the displayed-node revision',async()=>{
    const f=await fixture();f.select();await f.submit();
    await f.get('grants').querySelector('[data-rotate]').click();await f.get('grants').querySelector('[data-revoke]').click();
    assert.equal(f.reads(NODES).length,1);assert.equal(f.reads(WARNINGS).length,1);assert.equal(f.reads(GRANTS).length,4);
    await f.submit();assert.equal(f.posts().length,2);assert(f.posts().every(item=>JSON.parse(item.options.body).expected_node_revision===REVISION));
});
test('fresh warnings and disabled-node eligibility apply without auto selection',async()=>{
    const f=await fixture();f.select();f.queue(NODES,async()=>response([node(7),node(8,'disabled',{enable:false}),node(9,'warned')],200,REPLACEMENT));
    f.queue(WARNINGS,async()=>response([{inbound_id:9}]));await f.click('refresh-grants');
    assert.deepEqual(f.checkboxes().map(el=>[el.dataset.node,el.disabled,el.checked]),[['7',false,false],['8',true,false],['9',true,false]]);
    f.select();await f.submit();assert.deepEqual(JSON.parse(f.posts()[0].options.body).inbound_ids,[7]);
});
test('node refresh preserves an edited routing draft and its original If-Match revision',async()=>{
    const f=await fixture();f.get('proxy').value='draft.example.test';await f.get('routing-form').emit('input');
    await f.click('refresh-grants');assert.equal(f.get('proxy').value,'draft.example.test');assert.match(f.get('save-state').textContent,/未保存草稿/);
    f.queue('/api/routing/mihomo',async options=>{assert.equal(options.headers['If-Match'],'"r1"');return response({...settings,proxy_domains:['draft.example.test']});},'PUT');
    await f.click('save');assert.equal(f.requests.filter(item=>item.method==='PUT').length,1);
});

test('failed initial revision can recover only through explicit refresh and selection',async()=>{
    const f=await fixture({revision:null});assertInvalid(f);assert.equal(f.get('workspace').hidden,false,'refresh remains reachable after failed bootstrap');
    f.queue(NODES,async()=>response([node(7,'freshly read instance')],200,REPLACEMENT));await f.click('refresh-grants');
    assert.equal(f.checkboxes()[0].checked,false);await f.submit();assert.equal(f.posts().length,0);
    f.select();await f.submit();assert.equal(f.posts().length,1);assert.equal(JSON.parse(f.posts()[0].options.body).expected_node_revision,REPLACEMENT);
});
test('pagehide while grant list is pending cannot restore node validity or stale refresh guidance',async()=>{
    const f=await fixture();const gate=f.hold(GRANTS),pending=f.click('refresh-grants');await gate.started;
    f.pagehide();const previousNotice=f.get('notice').textContent;gate.resolve(response([grant()]));await pending;
    assertInvalid(f);assert.equal(f.get('notice').textContent,previousNotice);await f.submit();assert.equal(f.posts().length,0);
});
test('grant-list failure does not discard a successfully refreshed node frame or restore old choices',async()=>{
    const f=await fixture();f.select();
    f.queue(NODES,async()=>response([node(7,'newly confirmed instance')],200,REPLACEMENT));
    f.queue(GRANTS,async()=>response({detail:'synthetic grant-list failure'},503));await f.click('refresh-grants');
    assert.equal(f.get('notice').className,'error');assert.match(f.get('notice').textContent,/synthetic grant-list failure/);
    assert.equal(f.get('create-grant').disabled,false);assert.equal(f.checkboxes()[0].checked,false);
    await f.submit();assert.equal(f.posts().length,0);f.select();await f.submit();
    assert.equal(f.posts().length,1);assert.equal(JSON.parse(f.posts()[0].options.body).expected_node_revision,REPLACEMENT);
});
