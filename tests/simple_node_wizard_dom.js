'use strict';
// Execute the shipped controller with deterministic Vue/API/lifecycle doubles.
// No browser, HTTP server, core binary, dependency download or real credentials.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/js/app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const copy = value => JSON.parse(JSON.stringify(value));
const error = (status, detail) => ({response:{status, data:{detail}}});
const deferred = () => {let resolve, reject; const promise = new Promise((a,b) => {resolve=a; reject=b;}); return {promise, resolve, reject};};
const certificate = (id='cert-a', changes={}) => ({id, domain:id+'.example.test', environment:'production',
    revision:'synthetic-revision', days_remaining:30, not_after:Date.now()/1000+86400, ...changes});
const node = (changes={}) => ({id:8, mutation_revision:'a'.repeat(64), core:'sing-box', protocol:'vless',
    port:10443, enable:true, remark:'', routing_mode:'direct', share_eligible:true, upstream_export_eligible:true, ...changes});
function fixture(certs=[certificate()]) {
    let setup, interceptor, readCertificates=async()=>({data:copy(certs)}), create=async body=>{
        const row=node(body); rows.push(row); return {data:{inbound:copy(row)}};
    }, rows=[], authStatus=200, readRows=async()=>({data:copy(rows)});
    const reads=[], writes=[], events={}, messages=[], watchers=new WeakMap(), guards=[];
    const ref = initial => {let value=initial; const state={get value(){return value;}, set value(next){
        const previous=value; value=next; if(next!==previous) for(const fn of watchers.get(state)||[]) fn(next,previous);
    }}; return state;};
    const app={component(){},use(){},mount(){}};
    const sandbox={console, Date, AbortController, setTimeout:()=>1, clearTimeout(){},
        document:{getElementById(){return null;}}, addEventListener:(name,fn)=>(events[name]??=[]).push(fn),
        location:{hash:'',hostname:'panel.example.test',origin:'https://panel.example.test:8443',replace(){}},
        localStorage:new Proxy({}, {get(){throw Error('storage access');}}), sessionStorage:new Proxy({}, {get(){throw Error('storage access');}}),
        Vue:{createApp(options){setup=options.setup;return app;},ref,reactive:v=>v,
            computed:fn=>({get value(){return fn();}}), nextTick:async()=>{}, watch(target,fn){watchers.set(target,[...(watchers.get(target)||[]),fn]);},onMounted(){}},
        ElementPlus:{ElMessage:Object.fromEntries(['success','warning','error'].map(kind=>[kind,message=>messages.push({kind,message})])),ElMessageBox:{}},
        ElementPlusIconsVue:{}, axios:{defaults:{headers:{common:{}}},interceptors:{response:{use(ok,bad){interceptor=bad;}}},
            async get(url,options){reads.push({url,options});
                if(url==='/api/certificates')return readCertificates();
                if(url==='/api/auth/me')return {status:authStatus,data:{username:'synthetic'}};
                if(url==='/api/inbounds')return readRows(options);
                if(url.startsWith('/api/subscription/link/'))return {data:'vless://synthetic'};
                return {data:{}};
            }, async post(url,body,options){writes.push({method:'POST',url,body:copy(body),options});
                if(url==='/api/inbounds')return create(copy(body));
                return {headers:{'x-vui-export-profile':'tcp-upstream-v1'},data:'vless://synthetic-upstream'};
            }, async put(url,body,options){writes.push({method:'PUT',url,body:copy(body),options});return {data:{}};}}};
    vm.runInNewContext(source,sandbox);
    const state=setup();
    return {state,reads,writes,messages,guards,
        certificates(fn){readCertificates=fn;}, create(fn){create=fn;}, rows(value){rows=value;}, list(fn){readRows=fn;},
        event(name){for(const fn of events[name]||[])fn({});},
        async expire(){authStatus=401;try{await interceptor(error(401,'expired'));}catch(_){}},
        async ready(purpose='device'){await state.openAddInbound();state.nodeWizard.purpose=purpose;state.nodeWizard.step=2;},
    };
}

test('two purposes create the same minimal direct TLS node without credentials, hidden tags or automatic private reads', async()=>{
    for(const purpose of ['device','server']) {
        const f=fixture(); await f.ready(purpose); const s=f.state;
        assert.equal(s.nodeWizard.certificateId,'cert-a'); assert.equal(s.wizardReady.value,true);
        s.nodeWizard.remark='My outlet'; await s.createSimpleNode();
        assert.deepEqual(f.writes[0].body,{core:'sing-box',protocol:'vless',remark:'My outlet',port:10443,enable:true,
            routing_mode:'direct',profile:{security:'tls',transport:'direct'},certificate_id:'cert-a'});
        assert.equal(s.nodeWizard.step,3);assert.equal(s.nodeWizard.outcome,'created');
        assert.equal(s.nodeWizard.row.id,8);
        assert.equal(f.reads.some(read=>read.url.startsWith('/api/subscription/')),false);
        assert.equal(f.writes.some(write=>write.url.startsWith('/api/subscription/')),false);
        assert.equal(f.writes[0].options.timeout,20000);assert.ok(f.writes[0].options.signal);
        await s.createSimpleNode();assert.equal(f.writes.length,1);
    }
});

test('multi-certificate, no certificate, malformed, expired, staging and invalid lifetime all fail closed', async()=>{
    const f=fixture([certificate(),certificate('cert-b')]);await f.ready();
    assert.equal(f.state.nodeWizard.certificateId,null);assert.equal(f.state.wizardReady.value,false);
    f.state.nodeWizard.certificateId='cert-b';assert.equal(f.state.wizardReady.value,true);
    for(const changes of [{environment:'staging'},{revision:null},{days_remaining:null},{days_remaining:'30'},
        {days_remaining:-1},{not_after:Date.now()/1000-1},{not_after:'tomorrow'}]){
        const g=fixture([certificate('invalid',changes)]);await g.ready();
        assert.equal(g.state.nodeWizard.certificates.length,0);assert.equal(g.state.wizardReady.value,false);
        await g.state.createSimpleNode();assert.equal(g.writes.length,0);
    }
    const h=fixture([]);await h.ready();assert.equal(h.state.wizardReady.value,false);
    h.certificates(async()=>({data:{error:'malformed'}}));await h.state.loadWizardCertificates();
    assert.equal(h.state.nodeWizard.certificateState,'error');
});

test('failed certificate read retains inputs and deliberate selection; retry never replaces missing choice',async()=>{
    const f=fixture();await f.ready();const s=f.state;s.nodeWizard.remark='Keep';s.nodeWizard.port=11443;
    f.certificates(async()=>{throw Error('/secret/key.pem');});await s.loadWizardCertificates();
    assert.equal(s.nodeWizard.certificateState,'error');assert.equal(s.nodeWizard.certificateId,'cert-a');
    assert.equal(s.nodeWizard.remark,'Keep');assert.equal(s.nodeWizard.port,11443);assert.equal(s.wizardReady.value,false);
    f.certificates(async()=>({data:[certificate('cert-b')]}));await s.loadWizardCertificates();
    assert.equal(s.nodeWizard.certificateId,'cert-a');assert.equal(s.wizardReady.value,false);
    s.nodeWizard.certificateId='cert-b';assert.equal(s.wizardReady.value,true);
});

test('name length and controls match export eligibility before POST without truncating the draft',async()=>{
    for(const name of ['x'.repeat(129),'bad\nname','bad\x00name','bad\x7fname']){
        const f=fixture();await f.ready();f.state.nodeWizard.remark=name;
        assert.equal(f.state.wizardReady.value,false);assert.match(f.state.wizardNameHint.value,/128.*控制字符/);
        await f.state.createSimpleNode();assert.equal(f.writes.length,0);assert.equal(f.state.nodeWizard.remark,name);
    }
    const g=fixture();await g.ready();const name='中'.repeat(128);g.state.nodeWizard.remark=name;
    assert.equal(g.state.wizardReady.value,true);await g.state.createSimpleNode();
    assert.equal(g.writes[0].body.remark,name);assert.equal(g.state.nodeWizard.outcome,'created');
    assert.match(html,/id="node-wizard-name"[^>]*maxlength="128"/);
});

test('port conflicts are checked before POST, including disabled rows, without claiming OS availability',async()=>{
    const f=fixture();f.rows([node({enable:false})]);await f.state.fetchInbounds();await f.ready();
    assert.match(f.state.wizardPortHint.value,/已有节点/);await f.state.createSimpleNode();assert.equal(f.writes.length,0);
    for(const value of [0,443,1023,65536,1.5,null,'10444']){
        f.state.nodeWizard.port=value;assert.equal(f.state.wizardReady.value,false);
        await f.state.createSimpleNode();assert.equal(f.writes.length,0);
    }
    for(const value of [1024,65535]){
        const g=fixture();await g.ready();g.state.nodeWizard.port=value;
        assert.equal(g.state.wizardReady.value,true);await g.state.createSimpleNode();
        assert.equal(g.writes.length,1);assert.equal(g.writes[0].body.port,value);
    }
    f.state.nodeWizard.port=11443;assert.equal(f.state.wizardReady.value,true);
    assert.match(html,/id="node-wizard-port"[^>]*:min="1024"[^>]*:max="65535"/);
    assert.match(html,/未占用不代表系统端口空闲或公网已放行/);
});

test('single flight freezes create; confirmed save/apply failure cannot be POSTed again',async()=>{
    const f=fixture();await f.ready();const gate=deferred();f.create(()=>gate.promise);
    const saving=f.state.createSimpleNode();await f.state.createSimpleNode();
    assert.equal(f.writes.length,1);assert.equal(f.state.nodeWizard.busy,true);
    let closed=false;f.state.beforeNodeWizardClose(()=>{closed=true;});assert.equal(closed,false);
    gate.reject(error(409,{saved:true,applied:false,message:'/private/material.pem'}));await saving;
    assert.equal(f.state.nodeWizard.outcome,'saved');assert.equal(f.state.nodeWizard.step,3);
    assert.match(f.state.nodeWizard.error,/已保存.*核心未应用/);assert.doesNotMatch(f.state.nodeWizard.error,/private/);
    await f.state.createSimpleNode();await f.state.refreshWizardResult();assert.equal(f.writes.length,1);
});

test('network, server, conflict, post-save binding and malformed success outcomes require refresh without automatic recreation',async()=>{
    for(const failure of [Error('timeout'),error(500,'secret'),error(409,'CERTIFICATE_BIND_FAILED'),error(404,'CERTIFICATE_NOT_FOUND')]){
        const f=fixture();await f.ready();f.create(async()=>{throw failure;});await f.state.createSimpleNode();
        assert.equal(f.state.nodeWizard.outcome,'uncertain');assert.match(f.state.nodeWizard.error,/不要重复创建/);
        await f.state.createSimpleNode();await f.state.refreshWizardResult();assert.equal(f.writes.length,1);
    }
    const g=fixture();await g.ready();g.create(async()=>({data:{}}));await g.state.createSimpleNode();
    assert.equal(g.state.nodeWizard.outcome,'uncertain');assert.equal(g.state.nodeWizard.row,null);
});

test('post-success list read and manual refresh have finite timeouts, unlock after timeout and never recreate',async()=>{
    const f=fixture();await f.ready();const scheduled=[];
    const neverCompletes = options => new Promise((resolve,reject)=>{
        assert.equal(options.timeout,15000);
        scheduled.push({delay:options.timeout,fire:()=>reject(Object.assign(Error('synthetic timeout'),{code:'ECONNABORTED'}))});
    });
    f.list(neverCompletes);const saving=f.state.createSimpleNode();
    for(let n=0;n<12;n++)await Promise.resolve();
    assert.equal(f.state.nodeWizard.step,3);assert.equal(f.state.nodeWizard.outcome,'created');
    assert.equal(f.state.nodeWizard.busy,true);assert.equal(scheduled.length,1);
    scheduled.shift().fire();await saving;
    assert.equal(f.messages.at(-1).message,'节点列表读取失败，请检查连接后重试；运行状态未能确认。');
    assert.equal(f.state.nodeWizard.busy,false);assert.equal(f.state.nodeWizard.row,null);
    await f.state.createSimpleNode();assert.equal(f.writes.length,1);
    f.list(async()=>({data:[node()]}));await f.state.refreshWizardResult();
    assert.equal(f.state.nodeWizard.row.id,8,'same created-instance key recovers');
    f.list(neverCompletes);const refreshing=f.state.refreshWizardResult();
    for(let n=0;n<12;n++)await Promise.resolve();
    assert.equal(f.state.nodeWizard.busy,true);scheduled.shift().fire();await refreshing;
    assert.equal(f.state.nodeWizard.busy,false);assert.equal(f.writes.length,1);
    let closed=false;f.state.beforeNodeWizardClose(()=>{closed=true;});assert.equal(closed,true);
});

test('parameter rejection keeps the draft and shows safe Chinese failure',async()=>{
    const f=fixture();await f.ready();f.state.nodeWizard.remark='Retry';
    f.create(async()=>{throw error(422,[{message:'private path'}]);});await f.state.createSimpleNode();
    assert.equal(f.state.nodeWizard.step,2);assert.equal(f.state.nodeWizard.outcome,'draft');
    assert.equal(f.state.nodeWizard.remark,'Retry');assert.match(f.state.nodeWizard.error,/参数不符合/);
    assert.doesNotMatch(f.state.nodeWizard.error,/private/);
});

test('only explicit delivery reads credentials; server delivery uses suggested hostname and acknowledged existing export endpoint',async()=>{
    const f=fixture();await f.ready('device');await f.state.createSimpleNode();f.state.wizardDeliver();
    await Promise.resolve();assert.equal(f.reads.at(-1).url,'/api/subscription/link/8');f.state.closeQrDialog();
    const g=fixture();await g.ready('server');await g.state.createSimpleNode();g.state.wizardDeliver();
    assert.equal(g.state.upstreamExport.open,true);assert.equal(g.state.upstreamExport.server,'cert-a.example.test');
    assert.equal(g.state.upstreamExport.format,'uri');assert.equal(g.state.upstreamExport.acknowledged,false);
    await g.state.requestUpstreamExport();assert.equal(g.writes.length,1,'opening never acknowledges TCP purpose');
    g.state.upstreamExport.acknowledged=true;await g.state.requestUpstreamExport();
    assert.deepEqual(g.writes[1].body,{format:'uri',server:'cert-a.example.test',acknowledge_tcp_only:true});
    g.state.closeUpstreamExport();assert.equal(g.state.upstreamExport.content,'');
    g.state.openUpstreamExport(node());assert.equal(g.state.upstreamExport.server,'panel.example.test');
});

test('delivery uses authoritative eligibility booleans and exact instance, never core/protocol guesses',async()=>{
    for(const purpose of ['device','server']) {
        for (const eligible of [false, null, 1, 'true', undefined]) {
            const f=fixture();await f.ready(purpose);f.create(async body=>{
                const row=node({...body,share_eligible:eligible,upstream_export_eligible:eligible});f.rows([row]);return {data:{inbound:row}};
            });await f.state.createSimpleNode();f.state.wizardDeliver();
            assert.equal(f.state.nodeWizard.open,true);assert.equal(f.state.upstreamExport.open,false);
            assert.equal(f.reads.some(read=>read.url.startsWith('/api/subscription/')),false);
        }
    }
    const g=fixture();await g.ready();g.create(async body=>{
        g.rows([node({...body,mutation_revision:'b'.repeat(64)})]);return {data:{inbound:node(body)}};
    });await g.state.createSimpleNode();assert.equal(g.state.nodeWizard.row,null);
    g.state.wizardDeliver();assert.equal(g.state.nodeWizard.open,true);
    // A failed/stale read must not discard the immutable created-instance key:
    // a later authoritative refresh can restore delivery for that exact node.
    g.rows([node()]);await g.state.refreshWizardResult();assert.equal(g.state.nodeWizard.row.id,8);
});

test('out-of-order certificate reads, close, pagehide, view and 401 invalidate late data and abort pending creates',async()=>{
    const f=fixture(),old=deferred();f.certificates(()=>old.promise);const first=f.state.openAddInbound();
    f.certificates(async()=>({data:[certificate('new')]}));await f.state.openAddInbound();
    old.resolve({data:[certificate('old')]});await first;assert.equal(f.state.nodeWizard.certificateId,'new');
    for(const exit of ['close','pagehide','view','session']){
        const g=fixture();await g.ready();const gate=deferred();g.create(()=>gate.promise);const creating=g.state.createSimpleNode();
        if(exit==='close')g.state.closeNodeWizard();if(exit==='pagehide')g.event('pagehide');
        if(exit==='view')g.state.currentView.value='inbounds';if(exit==='session')await g.expire();
        assert.equal(g.writes[0].options.signal.aborted,true,exit);
        gate.resolve({data:{inbound:node()}});await creating;
        assert.equal(g.state.nodeWizard.open,false,exit);assert.equal(g.state.nodeWizard.row,null,exit);
    }
});

test('advanced creation stays independent and guarded enable only sends the requested field and revision',async()=>{
    const f=fixture();await f.ready();f.state.nodeWizard.remark='Advanced';f.state.nodeWizard.port=12443;
    await f.state.wizardAdvanced();assert.equal(f.state.nodeWizard.open,false);assert.equal(f.state.showAddInbound.value,true);
    assert.equal(f.state.newInbound.core,'xray');assert.equal(f.state.newInbound.routing_mode,'inherit');
    assert.equal(f.state.newInbound.remark,'Advanced');assert.equal(f.state.newInbound.port,12443);
    f.state.closeInboundEditor();await f.state.toggleInbound(node());
    assert.deepEqual(f.writes[0].body,{expected_instance_revision:'a'.repeat(64),enable:false});
    assert.equal(f.writes[0].method,'PUT');
    await f.state.toggleInbound(node({mutation_revision:null}));assert.equal(f.writes.length,1);
});

test('shipped flow exposes three steps and existing secret-safe delivery without browser storage or hidden purpose fields',()=>{
    for(const text of ['1 选用途','2 确认信息','3 交付连接','给手机或电脑用','给另一台服务器用','外部连接未验证','服务启动不代表公网可达'])assert.ok(html.includes(text),text);
    assert.match(html,/@click="openSimpleOperation\('direct'\)"/);assert.match(html,/@click="wizardAdvanced"/);
    assert.match(html,/scope.row.share_eligible === true/);assert.match(fs.readFileSync(path.join(__dirname,'../web/js/simple-operations.js'),'utf8'),/row.upstream_export_eligible===true/);
    assert.match(html,/内容含连接凭据/);assert.match(html,/v-model="upstreamExport.acknowledged"/);
    assert.match(html,/<details class="node-more">[\s\S]*?@click="deleteInbound\(scope.row\)"/);
});
