'use strict';
// Actual controller + template, synthetic DOM/fetch only. No native browser,
// server sockets, real user files, credentials, storage or runtime core.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const scriptPath = path.join(__dirname, '../web/js/mihomo-preview.js');
const script = fs.readFileSync(scriptPath, 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../web/proxy.html'), 'utf8');
const SECRET = 'synthetic-not-for-notices';
const REPORT = 'Mihomo YAML 离线结构预览（只读）\n不可应用。\n';
const reply = (text=REPORT, status=200) => ({status, ok:status>=200&&status<300,
    json:async()=>({schema_version:1,can_apply:false,text})});
const deferred = () => {let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const flush = () => new Promise(resolve=>setImmediate(resolve));
class Element {
    constructor(id,tag) {this.id=id;this.tagName=tag.toUpperCase();this.value='';this.files=[];this.disabled=false;this.hidden=false;this.open=false;this.textContent='';this.events=new Map();}
    addEventListener(name,fn) {this.events.set(name,[...(this.events.get(name)||[]),fn]);}
    async emit(name,event={preventDefault(){}}) {for(const fn of this.events.get(name)||[])await fn(event);}
    async click() {if(!this.disabled)await this.emit('click');}
    focus() {this.focused=true;}
    showModal() {assert(!this.open);this.open=true;}
    close() {if(this.open){this.open=false;void this.emit('close');}}
    set innerHTML(_) {assert.fail('Unsafe HTML sink');}
}
function fixture() {
    const ids=new Map(), globals=new Map(), requests=[], queued=[], redirects=[];
    for(const match of html.matchAll(/<([\w-]+)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
        assert(!ids.has(match[2]), 'actual template IDs are unique');
        const element=new Element(match[2],match[1]);
        element.hidden=/\shidden(?:\s|>)/.test(match[0]);
        ids.set(match[2],element);
    }
    const get=id=>{assert(ids.has(id),'actual template contains '+id);return ids.get(id);};
    vm.runInNewContext(script, {
        document:{getElementById:get}, TextEncoder, TextDecoder, AbortController,
        addEventListener:(name,fn)=>globals.set(name,[...(globals.get(name)||[]),fn]),
        location:{replace:url=>redirects.push(url)},
        fetch:async(url,options)=>{
            assert.equal(url,'/api/proxy/yaml/preview');assert.equal(options.method,'POST');
            assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store');
            assert.equal(options.headers['X-VUI-Request'],'1');assert.equal(options.headers['Content-Type'],'application/json');
            requests.push({url,options});
            return queued.length?queued.shift()(options):reply();
        },
        localStorage:new Proxy({}, {get(){assert.fail('No localStorage');}}),
        sessionStorage:new Proxy({}, {get(){assert.fail('No sessionStorage');}}),
        console:new Proxy({}, {get(){assert.fail('No source logging');}}),
    }, {filename:scriptPath});
    const emit=async(name,event={})=>{for(const fn of globals.get(name)||[])await fn(event);};
    const input=async(role,value)=>{get('yaml-'+role).value=value;await get('yaml-'+role).emit('input');};
    const select=async(role,file)=>{get('yaml-'+role+'-file').files=[file];get('yaml-'+role+'-file').value='fake-name';await get('yaml-'+role+'-file').emit('change');};
    return {get,requests,queued,redirects,emit,input,select,
        open:()=>get('open-yaml-preview').click(), close:()=>get('close-yaml-preview').click(),
        submit:()=>get('yaml-preview-form').emit('submit'),
        clear:()=>get('clear-yaml-preview').click(),
        notice:()=>get('yaml-preview-notice').textContent, report:()=>get('yaml-preview-result').textContent};
}
function file(text) {const data=new TextEncoder().encode(text);return {name:SECRET,size:data.byteLength,arrayBuffer:async()=>data.buffer};}
async function ready() {const f=fixture();await f.open();await f.input('candidate','{}');return f;}

test('actual template declares isolated controller, limits, privacy and labels',()=>{
    assert(html.includes('/ui/js/mihomo-preview.js'));
    assert(html.includes('发送给当前面板服务器解析，不是浏览器内离线解析'));
    assert(html.includes('即使零诊断也不可应用'));
    assert(html.includes('不能保证浏览器或操作系统内存擦除'));
    for(const id of ['yaml-candidate','yaml-baseline']) {
        const tag=html.match(new RegExp('<textarea[^>]*id="'+id+'"[^>]*>'))[0];
        assert(tag.includes('autocomplete="off"'));assert(tag.includes('spellcheck="false"'));
        assert(html.includes('for="'+id+'"'));
    }
});
test('initialization and opening never send a request; opening starts empty',async()=>{
    const f=fixture();assert.equal(f.requests.length,0);await f.open();
    assert.equal(f.requests.length,0);assert(f.get('yaml-preview-dialog').open);
    assert.equal(f.get('yaml-candidate').value,'');assert(f.get('yaml-candidate').focused);
});
test('explicit submit sends only candidate and optional baseline; safe report rendered',async()=>{
    const f=await ready();await f.input('baseline','mode: rule');await f.submit();
    assert.deepEqual(JSON.parse(f.requests[0].options.body),{candidate:'{}',baseline:'mode: rule'});
    assert.equal(f.report(),REPORT);assert.equal(f.get('yaml-preview-result').hidden,false);
    assert(f.notice().includes('不能用于应用配置'));assert.equal(f.get('yaml-preview-submit').disabled,false);
});
test('empty optional baseline is null; candidate is mandatory',async()=>{
    const f=fixture();await f.open();await f.submit();assert.equal(f.requests.length,0);
    await f.input('candidate','{}');await f.submit();
    assert.equal(JSON.parse(f.requests[0].options.body).baseline,null);
});
test('per input UTF8 byte limit and JSON wire envelope are enforced before fetch',async()=>{
    const f=await ready();await f.input('candidate','中'.repeat(350000));await f.submit();assert.equal(f.requests.length,0);
    await f.input('candidate','\\'.repeat(600000));await f.input('baseline','\\'.repeat(600000));
    await f.submit();assert.equal(f.requests.length,0);assert(f.notice().includes('JSON 转义'));
});
test('in-flight repeat submit cannot launch another request',async()=>{
    const f=await ready(), wait=deferred();f.queued.push(()=>wait.promise);
    const first=f.submit();await flush();assert(f.get('yaml-preview-submit').disabled);
    await f.submit();assert.equal(f.requests.length,1);wait.resolve(reply());await first;
    assert.equal(f.report(),REPORT);
});
test('editing aborts and invalidates old response and old finally cannot reset new busy',async()=>{
    const f=await ready(), first=deferred(), second=deferred();
    f.queued.push(()=>first.promise,()=>second.promise);
    const a=f.submit();await flush();await f.input('candidate','mode: rule');
    assert(f.requests[0].options.signal.aborted);const b=f.submit();await flush();
    first.resolve(reply('obsolete'));await a;
    assert.equal(f.report(),'');assert(f.get('yaml-preview-submit').disabled);
    second.resolve(reply('current report'));await b;assert.equal(f.report(),'current report');
});
test('close and reopen cannot resurrect old report or busy state',async()=>{
    const f=await ready(), first=deferred(), second=deferred();f.queued.push(()=>first.promise,()=>second.promise);
    const a=f.submit();await flush();await f.close();assert.equal(f.get('yaml-candidate').value,'');
    await f.open();await f.input('candidate','mode: direct');const b=f.submit();await flush();
    first.resolve(reply('obsolete'));await a;assert.equal(f.report(),'');assert(f.get('yaml-preview-submit').disabled);
    second.resolve(reply());await b;assert.equal(f.report(),REPORT);
});
test('clear and native Escape close empty inputs, files and report',async()=>{
    const f=await ready();await f.input('baseline','{}');await f.submit();await f.clear();
    assert.equal(f.report(),'');assert.equal(f.get('yaml-candidate').value,'');assert.equal(f.get('yaml-baseline').value,'');
    await f.input('candidate','{}');await f.get('yaml-preview-dialog').emit('cancel');f.get('yaml-preview-dialog').close();
    assert.equal(f.get('yaml-candidate').value,'');assert(!f.get('yaml-preview-dialog').open);
});
test('401 clears sensitive inputs before login and no error body is consumed',async()=>{
    const f=await ready();await f.input('baseline',SECRET);
    f.queued.push(()=>({status:401,ok:false,json:()=>assert.fail('No 401 body read')}));await f.submit();
    assert.deepEqual(f.redirects,['/login']);assert.equal(f.get('yaml-candidate').value,'');
    assert.equal(f.get('yaml-baseline').value,'');assert.equal(f.report(),'');assert(!f.get('yaml-preview-dialog').open);
    assert(f.get('yaml-preview-submit').disabled);assert(!f.notice().includes(SECRET));
});
test('stale 401 after edit does not clear new inputs or redirect',async()=>{
    const f=await ready(), wait=deferred();f.queued.push(()=>wait.promise);
    const pending=f.submit();await flush();await f.input('candidate','mode: rule');
    wait.resolve(reply('',401));await pending;
    assert.deepEqual(f.redirects,[]);assert.equal(f.get('yaml-candidate').value,'mode: rule');
});
test('pagehide clears and invalidates requests; pageshow restores empty closed form',async()=>{
    const f=await ready(), wait=deferred();f.queued.push(()=>wait.promise);const pending=f.submit();await flush();
    await f.emit('pagehide');wait.resolve(reply('old'));await pending;assert.equal(f.report(),'');
    assert.equal(f.get('yaml-candidate').value,'');await f.emit('pageshow',{persisted:true});
    assert(!f.get('yaml-preview-dialog').open);await f.open();assert.equal(f.get('yaml-candidate').value,'');
});
test('parse errors are mapped without echoing arbitrary response details and can retry',async()=>{
    const f=await ready();f.queued.push(()=>({status:422,ok:false,json:async()=>({detail:{code:'DUPLICATE_KEY',source:'baseline',secret:SECRET}})}));
    await f.submit();assert(f.notice().startsWith('基线：'));assert(!f.notice().includes(SECRET));
    assert.equal(f.get('yaml-candidate').value,'{}');await f.submit();assert.equal(f.report(),REPORT);
});
test('unknown server error and malformed JSON stay fixed, no source echo',async()=>{
    const f=await ready();f.queued.push(()=>({status:422,ok:false,json:async()=>({detail:{code:SECRET,source:SECRET}})}));
    await f.submit();assert(!f.notice().includes(SECRET));assert.equal(f.report(),'');
    f.queued.push(()=>({status:200,ok:true,json:async()=>{throw Error(SECRET);}}));await f.submit();
    assert(!f.notice().includes(SECRET));assert.equal(f.report(),'');assert(!f.get('yaml-preview-submit').disabled);
});
test('success response contract rejects apply true, extra fields, invalid version and oversized text',async()=>{
    const f=await ready();
    for(const result of [{schema_version:1,can_apply:true,text:SECRET},{schema_version:2,can_apply:false,text:SECRET},
                         {schema_version:1,can_apply:false,text:SECRET,source:SECRET},{schema_version:1,can_apply:false,text:'x'.repeat(131073)},null]) {
        f.queued.push(()=>({status:200,ok:true,json:async()=>result}));await f.submit();assert.equal(f.report(),'');assert(!f.notice().includes(SECRET));
    }
});
test('HTML-like successful report is assigned as plain text, never markup',async()=>{
    const f=await ready(), text='<img src=x onerror=alert(1)>';f.queued.push(()=>reply(text));
    await f.submit();assert.equal(f.report(),text);
});
test('UTF8 file selection stays local until explicit submit; filename not transmitted',async()=>{
    const f=await ready();await f.select('candidate',file('mode: rule'));assert.equal(f.requests.length,0);
    assert.equal(f.get('yaml-candidate').value,'mode: rule');assert.equal(f.get('yaml-candidate-file').value,'');
    await f.submit();assert(!f.requests[0].options.body.includes(SECRET));assert(!f.notice().includes(SECRET));
});
test('invalid UTF8, empty, whitespace-only, oversized and failed file reads block preview until repaired',async()=>{
    const f=await ready();
    for(const bad of [file(''),file('   '),{size:1,arrayBuffer:async()=>new Uint8Array([255]).buffer},
                     {size:1048577,arrayBuffer:()=>assert.fail('Oversized file never read')},
                     {size:3,arrayBuffer:async()=>{throw Error(SECRET);}}]) {
        await f.select('baseline',bad);assert(f.get('yaml-preview-submit').disabled);
        await f.submit();assert.equal(f.requests.length,0);assert(!f.notice().includes(SECRET));
        await f.input('baseline','');assert(!f.get('yaml-preview-submit').disabled);
    }
    await f.submit();assert.equal(f.requests.length,1);
});
test('manual edit after delayed file selection wins; stale read cannot overwrite or change busy',async()=>{
    const f=await ready(), bytes=deferred();const reading=f.select('candidate',{size:2,arrayBuffer:()=>bytes.promise});
    await flush();assert(f.get('yaml-preview-submit').disabled);await f.input('candidate','mode: rule');
    bytes.resolve(new TextEncoder().encode('{}').buffer);await reading;
    assert.equal(f.get('yaml-candidate').value,'mode: rule');assert(!f.get('yaml-preview-submit').disabled);
});
test('second file selection wins over first and selecting same file can be repeated',async()=>{
    const f=await ready(), bytes=deferred();const reading=f.select('candidate',{size:2,arrayBuffer:()=>bytes.promise});
    await flush();await f.select('candidate',file('mode: direct'));
    bytes.resolve(new TextEncoder().encode('{}').buffer);await reading;assert.equal(f.get('yaml-candidate').value,'mode: direct');
    await f.select('candidate',file('mode: direct'));assert.equal(f.get('yaml-candidate-file').value,'');
    assert.equal(f.get('yaml-candidate').value,'mode: direct');
});
test('close/reopen rejects old file reads and never echoes their failure',async()=>{
    const f=await ready(), bytes=deferred();const reading=f.select('baseline',{size:2,arrayBuffer:()=>bytes.promise});
    await flush();await f.close();await f.open();await f.input('baseline','mode: rule');
    bytes.reject(Error(SECRET));await reading;assert.equal(f.get('yaml-baseline').value,'mode: rule');
    assert(!f.notice().includes(SECRET));assert(!f.get('yaml-preview-submit').disabled);
});
test('candidate and baseline reads are independent; one completion cannot enable before other',async()=>{
    const f=await ready(), a=deferred(),b=deferred();
    const one=f.select('candidate',{size:2,arrayBuffer:()=>a.promise});
    const two=f.select('baseline',{size:2,arrayBuffer:()=>b.promise});await flush();
    a.resolve(new TextEncoder().encode('{}').buffer);await one;assert(f.get('yaml-preview-submit').disabled);
    b.resolve(new TextEncoder().encode('{}').buffer);await two;assert(!f.get('yaml-preview-submit').disabled);
    await f.submit();assert.deepEqual(JSON.parse(f.requests[0].options.body),{candidate:'{}',baseline:'{}'});
});
test('response body decoding is also epoch-guarded across editing and close',async()=>{
    const f=await ready(), body=deferred();f.queued.push(()=>({status:200,ok:true,json:()=>body.promise}));
    const pending=f.submit();await flush();await f.input('candidate','mode: rule');
    body.resolve({schema_version:1,can_apply:false,text:'old'});await pending;assert.equal(f.report(),'');
});
test('error source labels reject prototype names and nonstrings',async()=>{
    const f=await ready();
    for(const source of ['constructor','__proto__',{},null]) {
        f.queued.push(()=>({status:422,ok:false,json:async()=>({detail:{code:'DUPLICATE_KEY',source}})}));
        await f.submit();assert.equal(f.notice(),'YAML 包含重复字段，请检查后重试。');
    }
});
test('queued native close from previous session cannot clear reopened inputs or abort new request',async()=>{
    const f=await ready(), dialog=f.get('yaml-preview-dialog'), wait=deferred();
    // Unlike the basic fake, native close delivery is queued as a later task.
    dialog.close=()=>{dialog.open=false;};
    await f.close();await f.open();await f.input('candidate','mode: direct');
    f.queued.push(()=>wait.promise);const pending=f.submit();await flush();
    await dialog.emit('close');
    assert.equal(f.get('yaml-candidate').value,'mode: direct');
    assert(f.get('yaml-preview-submit').disabled);assert(!f.requests[0].options.signal.aborted);
    wait.resolve(reply());await pending;assert.equal(f.report(),REPORT);
});
