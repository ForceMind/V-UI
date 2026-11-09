'use strict';
// Real shipped orchestration, synthetic HTTP replies. No browser, core,
// service, network, installation or production data is used by this file.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {Flow, suggestedPort, usableCertificates, connectionDomain} = require('../web/js/operation-flow.js');
const {fixture,clone,rev,problem}=require('./fixtures/operation_http_fixture.cjs');
const writes=f=>f.calls.filter(c=>c.method!=='GET'&&!c.path.endsWith('/import/preview'));

test('first direct node prepares unchanged OFF baseline then creates once and confirms real public state',async()=>{
    const f=fixture();await f.flow.load();const result=await f.flow.run('direct',f.choice());
    assert.equal(result.kind,'node');assert.equal(result.node.routing_mode,'direct');
    assert.deepEqual(writes(f).map(c=>c.path),['/api/proxy/check','/api/proxy/apply','/api/inbounds']);
    assert.equal(f.state.settings.relay_enabled,false);assert.equal(f.state.settings.local_proxy_enabled,false);
    assert.equal(f.calls.some(c=>c.path.includes('/subscription')),false,'Creation alone never reads credentials');
    await assert.rejects(f.flow.run('direct',f.choice()),e=>e.code==='NODE_UNCERTAIN');
    assert.equal(f.nodes.length,1);
});
test('unique certificate and suggested port presets reject expired/test CA and preserve occupied disabled ports',()=>{
    const f=fixture();assert.equal(usableCertificates(f.certs).length,1);
    for(const change of [{environment:'staging'},{revision:''},{not_after:1},{days_remaining:-1}])assert.equal(usableCertificates([{...f.certs[0],...change}]).length,0);
    assert.equal(suggestedPort([{port:10443,enable:false},{port:10444}]),10445);
});
test('DNS wildcard needs an explicit covered connection host before any creation or settings write',async()=>{
    const f=fixture();Object.assign(f.certs[0],{domain:'example.test',method:'dns-01',wildcard:true,names:['example.test','*.example.test']});
    await f.flow.load();
    for(const host of ['', '*.example.test', 'deep.node.example.test', 'unrelated.test', 'https://node.example.test']){
        await assert.rejects(f.flow.run('direct',{...f.choice(),connectionDomain:host}),error=>error.code.startsWith('CONNECTION_DOMAIN'));
        assert.equal(writes(f).length,0);
    }
    await f.flow.run('direct',{...f.choice(),connectionDomain:'node.example.test'});
    const created=f.calls.find(call=>call.path==='/api/inbounds'&&call.method==='POST');
    assert.equal(created.body.profile.server_name,'node.example.test');
    assert.equal(f.flow.intent.server,'node.example.test');
    assert.equal(f.calls.some(call=>call.path.includes('/subscription')),false);
});
test('literal certificate host matching never treats a wildcard as the client SNI',()=>{
    const cert={domain:'example.test',method:'dns-01',names:['example.test','*.example.test']};
    assert.equal(connectionDomain(cert,'NODE.example.test.'),'node.example.test');
    for(const host of [0,false,'*.example.test','node.example.test:443','node.example.test/path','a.b.example.test','evil-example.test','127.0.0.1'])
        assert.throws(()=>connectionDomain(cert,host),error=>error.code.startsWith('CONNECTION_DOMAIN'));
    assert.equal(connectionDomain({domain:'old.example.test'},undefined),'old.example.test');
});
test('unknown draft, pending, missing scope and changed revision stop before all writes',async()=>{
    for(const mode of ['draft','pending','scope','changed']){
        const f=fixture({active:true,scopes:mode==='scope'?['diagnose','nodes']:null});await f.flow.load();const choice=f.choice();
        if(mode==='draft')f.state.active.revision=rev(77);
        if(mode==='pending')f.state.pending={id:'other'};
        if(mode==='changed')f.advance();
        await assert.rejects(f.flow.run('direct',choice));assert.equal(writes(f).length,0,mode);
    }
});
test('relay refuses enabled/running Xray without stopping, importing or changing old nodes',async()=>{
    const old={id:2,core:'xray',port:8001,enable:true,mutation_revision:rev(90)};
    const f=fixture({active:true,xray:true,upstream:true,nodes:[old]});await f.flow.load();
    await assert.rejects(f.flow.run('relay',f.choice()),e=>e.code==='XRAY_CONFLICT');
    assert.equal(writes(f).length,0);assert.deepEqual(f.nodes,[old]);
});
test('host proxy presets credentials only once, keeps relay off, and does not require stopping Xray',async()=>{
    const f=fixture({active:true,xray:true,upstream:true});await f.flow.load();const oldX=clone(f.cores.xray);
    const result=await f.flow.run('host',f.choice());assert.equal(result.kind,'host');
    const save=f.calls.find(c=>c.path==='/api/proxy/settings'&&c.method==='PUT');
    assert.match(save.body.local_username,/^vui_[0-9a-f]{8}$/);assert.match(save.body.local_password,/^[0-9a-f]{48}$/);
    assert.equal(save.body.relay_enabled,false);assert.deepEqual(f.cores.xray,oldX);
    assert.ok(!JSON.stringify(result).includes(save.body.local_password));assert.ok(!JSON.stringify(f.events).includes(save.body.local_password));
    const info=await f.flow.localConnection();assert.equal(info.password,save.body.local_password);
});
test('re-enabling preserves existing proxy credentials and never rotates them silently',async()=>{
    const f=fixture({active:true,upstream:true,settings:{local_username:'original',local_password_set:true}});await f.flow.load();
    await f.flow.run('host',f.choice());const save=f.calls.find(c=>c.path==='/api/proxy/settings'&&c.method==='PUT');
    assert.equal(save.body.local_username,'original');assert.ok(!('local_password'in save.body));
});
test('changing the shared exit requires the exact visible affected-instance stamp',async()=>{
    const old={id:2,core:'sing-box',port:8001,enable:true,routing_mode:'relay',mutation_revision:rev(90)};
    const f=fixture({active:true,upstream:true,nodes:[old]});await f.flow.load();
    const choice={...f.choice(),link:'vless://fake-input',upstreamId:null};
    await assert.rejects(f.flow.run('host',choice),e=>e.code==='CONFIRM_IMPACT');assert.equal(writes(f).length,0);
    choice.impactStamp=f.flow.impactStamp();f.nodes[0].mutation_revision=rev(91);
    await assert.rejects(f.flow.run('host',choice),e=>e.code==='CONFIRM_IMPACT');assert.equal(writes(f).length,0);
});
test('one supported import candidate is recommended but saved with CAS before explicit host activation',async()=>{
    const f=fixture({active:true});await f.flow.load();await f.flow.run('host',{...f.choice(),link:'vless://fake-input'});
    const imported=f.calls.find(c=>c.path==='/api/proxy/profiles');assert.equal(imported.body.candidate_index,0);
    assert.deepEqual(writes(f).map(c=>c.path),['/api/proxy/profiles','/api/proxy/settings','/api/proxy/check','/api/proxy/apply']);
});
test('lost committed apply reply resumes the exact transaction and never creates before confirmed readback',async()=>{
    const f=fixture({loseApply:true});await f.flow.load();
    await assert.rejects(f.flow.run('direct',f.choice()),e=>e.code==='APPLY_UNCERTAIN');assert.equal(f.nodes.length,0);
    const first=f.calls.find(c=>c.path==='/api/proxy/apply');const result=await f.flow.resolve();
    const applied=f.calls.filter(c=>c.path==='/api/proxy/apply');assert.equal(applied.length,2);
    assert.equal(applied[1].headers['Idempotency-Key'],first.headers['Idempotency-Key']);
    assert.equal(f.transactions.size,1);assert.equal(result.kind,'node');assert.equal(f.nodes.length,1);
});
test('lost create reply is resolved from the exact public correlation tag, never a second create POST',async()=>{
    const f=fixture({active:true,loseCreate:true});await f.flow.load();await assert.rejects(f.flow.run('direct',f.choice()),e=>e.code==='NODE_UNCERTAIN');
    const result=await f.flow.resolve();assert.equal(result.kind,'node');assert.equal(f.nodes.length,1);
    assert.equal(f.calls.filter(c=>c.path==='/api/inbounds'&&c.method==='POST').length,1);
    await assert.rejects(f.flow.run('direct',f.choice()));
});
test('lost import is reconciled only from one new exact source fingerprint and unchanged public baseline',async()=>{
    const f=fixture({active:true,loseImport:true});await f.flow.load();await assert.rejects(f.flow.run('host',{...f.choice(),link:'vless://fake-input'}));
    const result=await f.flow.resolve();assert.equal(result.kind,'host');
    assert.equal(f.calls.filter(c=>c.path==='/api/proxy/profiles').length,1);
});
test('manual stop requires matching stopped instances, and explicit startup cannot restore an unseen added node',async()=>{
    const old={id:7,core:'sing-box',enable:true,port:9001,mutation_revision:rev(70)};
    const f=fixture({active:true,stopped:true,nodes:[old]});await f.flow.load();
    await assert.rejects(f.flow.run('direct',f.choice()),e=>e.code==='CORE_STOPPED');assert.equal(writes(f).length,0);
    const choice={...f.choice(),startStopped:true,startStamp:f.flow.stoppedStamp()};
    const result=await f.flow.run('direct',choice);assert.equal(result.kind,'node');
    assert.equal(f.calls.filter(c=>c.path==='/api/cores/sing-box/restart').length,0);
    const apply=f.calls.find(c=>c.path==='/api/proxy/apply');assert.ok(apply.body.source_fingerprint);assert.ok(apply.body.runtime_fingerprint);
    assert.equal(f.cores['sing-box'].running,true);
});
test('late page response cannot trigger subsequent writes or retained secrets',async()=>{
    let release;const waiting=new Promise(resolve=>{release=resolve});
    const f=fixture({onCall:async c=>{if(c.path==='/api/proxy/check')await waiting}});await f.flow.load();
    const run=f.flow.run('direct',f.choice());await new Promise(resolve=>setImmediate(resolve));f.flow.destroy();release();
    await assert.rejects(run,e=>e.code==='ENDED');assert.equal(f.calls.some(c=>c.path==='/api/proxy/apply'),false);assert.equal(f.nodes.length,0);
});
test('share requires exact displayed instance and explicit secret-purpose call',async()=>{
    const old={id:2,mutation_revision:rev(80),content_revision:rev(81),share_eligible:true};const f=fixture({active:true,nodes:[old]});await f.flow.load();
    await assert.rejects(f.flow.share(2,rev(79),'vpn.example.test'),e=>e.code==='NODE_CHANGED');
    assert.equal(f.calls.some(c=>c.path.startsWith('/api/subscription')),false);
    assert.equal(await f.flow.share(2,rev(80),'vpn.example.test'),'vless://fake-owned-secret');
});
test('host disable uncertain retry never re-enables the proxy through an earlier host intent',async()=>{
    const f=fixture({active:true,upstream:true,loseApply:true,settings:{local_proxy_enabled:true,local_username:'old',local_password_set:true}});await f.flow.load();
    f.flow.intent={task:'host',choice:f.choice(),nodeSent:false};
    await assert.rejects(f.flow.disableHost(),e=>e.code==='APPLY_UNCERTAIN');const result=await f.flow.resolve();
    assert.equal(result.kind,'host-off');assert.equal(f.state.settings.local_proxy_enabled,false);
});
test('a truncated HTTP200 create reply is uncertain, never permits a second creation',async()=>{
    const f=fixture({active:true});const api=f.flow.request;let lost=false;
    f.flow.request=async(path,method,body,options)=>{const result=await api(path,method,body,options);
        if(path==='/api/inbounds'&&method==='POST'&&!lost){lost=true;throw Object.assign(new Error('Truncated real response'),{status:200});}return result;};
    await f.flow.load();await assert.rejects(f.flow.run('direct',f.choice()),e=>e.code==='NODE_UNCERTAIN');
    assert.equal(f.flow.intent.nodeSent,true);assert.equal(f.nodes.length,1);
    await assert.rejects(f.flow.run('direct',{...f.choice(),port:11443}),e=>e.code==='NODE_UNCERTAIN');
    const result=await f.flow.resolve();assert.equal(result.kind,'node');assert.equal(f.nodes.length,1);
    assert.equal(f.calls.filter(c=>c.method==='POST'&&c.path==='/api/inbounds').length,1);
});
test('truncated HTTP200 apply response keeps exactly the original transaction key for recovery',async()=>{
    const f=fixture();const api=f.flow.request;let lost=false;
    f.flow.request=async(path,method,body,options)=>{const result=await api(path,method,body,options);if(path==='/api/proxy/apply'&&!lost){lost=true;throw Object.assign(new Error('Truncated apply reply'),{status:200});}return result;};
    await f.flow.load();await assert.rejects(f.flow.run('direct',f.choice()),e=>e.code==='APPLY_UNCERTAIN');
    const first=f.calls.find(c=>c.path==='/api/proxy/apply').headers['Idempotency-Key'];
    await assert.rejects(f.flow.run('direct',f.choice()),e=>e.code==='APPLY_UNCERTAIN');
    await f.flow.resolve();const keys=f.calls.filter(c=>c.path==='/api/proxy/apply').map(c=>c.headers['Idempotency-Key']);
    assert.deepEqual([...new Set(keys)],[first]);assert.equal(f.nodes.length,1);
});
