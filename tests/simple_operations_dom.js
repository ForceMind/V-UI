'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const api=require('../web/js/operation-flow.js');
const {fixture,rev}=require('./fixtures/operation_http_fixture.cjs');
const {Document}=require('./fixtures/operation_dom.cjs');
const source=fs.readFileSync('web/js/simple-operations.js','utf8');
const settle=async()=>{for(let i=0;i<6;i++)await new Promise(resolve=>setImmediate(resolve));};
function screen(options={}){
    const backend=fixture(options),document=new Document(),root=document.createElement('div');document.body.append(root);
    const events={},clipboard=[],confirms=[];let fetches=0;
    const context={document,VUI_OPERATION_FLOW:api,location:{pathname:'/maintenance',hash:'',hostname:'panel.example.test'},
        confirm:text=>{confirms.push(text);return true;},navigator:{clipboard:{writeText:async value=>{if(options.clipboardFailure)throw new Error('synthetic denied');clipboard.push(value);}}},
        addEventListener:(name,fn)=>{(events[name]??=[]).push(fn);},dispatchEvent:event=>{for(const fn of events[event.type]||[])fn(event);},removeEventListener:()=>{},
        CustomEvent:class{constructor(type,options={}){this.type=type;this.detail=options.detail;}},
        setTimeout,clearTimeout,AbortController,fetch:async()=>{fetches++;throw new Error('Unexpected cookie transport');},
        VUI_NAV:{guard:()=>()=>{},modal:{open(){},close(){}},replaceLogin(){throw new Error('Remote view must not use administrator-cookie redirect');}}};
    context.globalThis=context;vm.runInNewContext(source,context);
    const view=context.VUI_SIMPLE_OPERATIONS.mount(root,{dialog:true,request:backend.api,remote:true,scopes:options.scopes??null});
    return {...backend,view,document,root,events,clipboard,confirms,fetches:()=>fetches};
}
const button=(f,text)=>f.root.querySelectorAll('button').find(b=>b.textContent===text);
test('actual form presets certificate/port, creates once, and copy failure keeps the generated link',async()=>{
    const old={id:2,core:'xray',port:10443,enable:false,mutation_revision:rev(90)};
    const f=screen({nodes:[old],clipboardFailure:true});await f.view.open('direct');
    assert.equal(f.view.fields.port.value,'10444');assert.equal(f.view.certificateId,f.certs[0].id);
    assert.equal(f.view.fields.cert,undefined,'Unique certificate does not require a dropdown');
    f.view.form.dispatchEvent({type:'submit'});await settle();
    assert.equal(f.nodes.length,2);assert.ok(f.root.textContent.includes('连接信息'));
    const copy=button(f,'复制连接链接');assert.ok(copy);copy.click();await settle();
    assert.ok(f.view.status.textContent.includes('手动复制'));assert.equal(f.root.querySelector('.ops-delivery').querySelector('textarea').selected,true);
    assert.equal(f.calls.filter(c=>c.path==='/api/inbounds'&&c.method==='POST').length,1);
    assert.equal(f.fetches(),0);
});
test('host has one main action; credentials are read only after explicit disclosure, command contains no password',async()=>{
    const f=screen({active:true,upstream:true});await f.view.open('host');
    f.view.form.dispatchEvent({type:'submit'});await settle();
    assert.equal(f.calls.some(c=>c.path==='/api/proxy/local-connection'),false);
    button(f,'显示连接信息与使用方法（含访问密码）').click();await settle();
    assert.equal(f.calls.filter(c=>c.path==='/api/proxy/local-connection').length,1);
    const password=f.root.querySelector('input[type=password]').value;
    const command=f.root.querySelector('textarea').value;assert.ok(command.startsWith('curl --proxy http://127.0.0.1:'));
    assert.ok(!command.includes(password));assert.ok(f.root.textContent.includes('不是你电脑'));
    f.view.close();assert.equal(f.view.secret,'');assert.equal(f.view.content.children.length,0);
});
test('Xray conflict and missing scope are visible before user enters an upstream',async()=>{
    const f=screen({active:true,xray:true});await f.view.open('relay');
    assert.equal(f.view.submit.disabled,true);assert.ok(f.view.formWarning.textContent.includes('Xray'));
    assert.equal(f.calls.some(c=>c.method==='POST'),false);
    const read=screen({active:true,scopes:['diagnose']});await read.view.open('direct');
    assert.equal(read.view.submit.disabled,true);assert.ok(read.view.formWarning.textContent.includes('授权'));
});
test('multiple certificate selection is deliberate, no-cert path blocks creation without dropping fields',async()=>{
    const f=screen();f.certs.push({...f.certs[0],id:'b'.repeat(32),domain:'second.example.test'});await f.view.open('direct');
    assert.equal(f.view.fields.cert.value,'');assert.equal(f.view.submit.disabled,true);
    f.view.fields.cert.value=f.certs[1].id;f.view.fields.cert.dispatchEvent({type:'change',bubbles:true});assert.equal(f.view.submit.disabled,false);
    const none=screen();none.certs.length=0;await none.view.open('direct');none.view.fields.name.value='保留名称';
    button(none,'设置域名和证书').click();assert.equal(none.view.fields.name.value,'保留名称');assert.equal(none.view.submit.disabled,true);
});
test('shared-exit confirmation appears only when a real different target is chosen',async()=>{
    const f=screen({active:true,upstream:true,settings:{local_proxy_enabled:true,local_username:'old',local_password_set:true}});await f.view.open('host');
    assert.equal(f.view.fields.impactBox.hidden,true);assert.equal(f.view.submit.disabled,false);
    f.view.fields.upstream.value='new';f.view.fields.upstream.dispatchEvent({type:'change',bubbles:true});
    f.view.fields.link.value='vless://fake-other';f.view.fields.link.dispatchEvent({type:'input',bubbles:true});
    assert.equal(f.view.fields.impactBox.hidden,false);assert.equal(f.view.submit.disabled,true);
    f.view.fields.impact.checked=true;f.view.fields.impact.dispatchEvent({type:'change',bubbles:true});assert.equal(f.view.submit.disabled,false);
    f.view.fields.upstream.value='up_a';f.view.fields.upstream.dispatchEvent({type:'change',bubbles:true});assert.equal(f.view.fields.impactBox.hidden,true);
});
test('unknown create outcome has a result-check button and cannot submit a second creation',async()=>{
    const f=screen({active:true,loseCreate:true});await f.view.open('direct');f.view.form.dispatchEvent({type:'submit'});await settle();
    assert.ok(f.view.problem.textContent.includes('核对并完成'));assert.equal(f.view.submit.disabled,true);
    button(f,'核对并完成').click();await settle();assert.ok(f.root.textContent.includes('连接信息'));
    assert.equal(f.calls.filter(c=>c.path==='/api/inbounds'&&c.method==='POST').length,1);
});
test('a recycled node ID never exports credentials, and remote expiry clears the entire view',async()=>{
    const row={id:2,port:8001,core:'xray',mutation_revision:rev(90),share_eligible:true};
    const f=screen({active:true,nodes:[row]});await f.view.open('share',{id:2,revision:rev(89)});await settle();
    assert.ok(f.view.problem.textContent.includes('变化'));assert.equal(f.calls.some(c=>c.path.startsWith('/api/subscription')),false);
    f.view.expire();assert.equal(f.root.querySelector('.ops-view')?.textContent||f.view.host.textContent,'');assert.equal(f.view.secret,'');assert.equal(f.fetches(),0);
});
test('rendered node names and imported labels are text, never interpreted HTML',async()=>{
    const f=screen({active:true,nodes:[{id:4,remark:'<img src=x onerror=bad>',port:9001,core:'xray',protocol:'vless',enable:true,mutation_revision:rev(94)}]});
    // A separate inline instance renders the production list with the same transport.
    const root=f.document.createElement('div');f.document.body.append(root);
    const view=new (f.view.constructor)(root,{request:f.api,remote:true});await settle();
    assert.ok(view.list.textContent.includes('<img src=x'));assert.equal(view.list.querySelector('img'),null);view.destroy();
});
test('refresh keeps current typed node settings without storing them or carrying confirmation',async()=>{
    const f=screen({active:true,upstream:true});await f.view.open('relay');
    f.view.fields.name.value='保留我的节点';f.view.fields.port.value='19443';f.view.dirty=true;
    f.view.fields.upstream.value='new';f.view.fields.link.value='vless://fake-temporary-link';
    await f.view.refresh();
    assert.equal(f.view.fields.name.value,'保留我的节点');assert.equal(f.view.fields.port.value,'19443');
    assert.equal(f.view.fields.link.value,'vless://fake-temporary-link');
    assert.equal(f.view.fields.impact.checked,false);
    f.view.close();assert.equal(f.view.secret,'');assert.equal(f.view.savedForm,null);
});
test('subscription presets use the exact displayed inventory condition and one explicit POST',async()=>{
    const f=screen({active:true});let body;
    f.view.flow.request=async(path,method='GET',data,options={})=>{
        if(path==='/api/inbounds')return {value:[{id:4,remark:'我的节点',port:10443,enable:true,share_eligible:true}],nodeRevision:rev(904)};
        if(path==='/api/subscription/mihomo-warnings')return [];
        if(path==='/api/subscriptions'&&method==='GET')return [];
        if(path==='/api/subscriptions'&&method==='POST'){assert.equal(body,undefined);body=data;return {id:1,paths:{'mihomo.yaml':'/sub/fake_token/mihomo.yaml'}};}
        throw new Error('Unexpected subscription call');
    };
    await f.view.subscriptions();const form=f.view.content.querySelector('form');assert.ok(form);
    assert.equal(form.querySelector('input[type=checkbox]').checked,true);
    form.dispatchEvent({type:'submit'});await settle();
    assert.equal(body.expected_node_revision,rev(904));assert.equal(body.inbound_ids.join(','),'4');
    assert.equal(body.formats.join(','),'mihomo.yaml');assert.equal(body.expires_days,90);
    assert.ok(button(f,'复制订阅地址'));form.dispatchEvent({type:'submit'});await settle();
    button(f,'清除地址显示').click();assert.equal(f.view.secret,'');
});
test('missing inventory header blocks subscription creation, and scopes cannot be broadened by its tabs',async()=>{
    const f=screen({active:true});f.view.flow.request=async path=>path==='/api/inbounds'?{value:[],nodeRevision:null}:[];
    await f.view.subscriptions();assert.equal(button(f,'生成订阅地址'),undefined);
    const r=screen({active:true,scopes:['diagnose']});assert.equal(button(r,'订阅分享').disabled,true);
    await r.view.subscriptions();assert.ok(r.view.problem.textContent.includes('维护授权'));assert.equal(r.calls.some(c=>c.path==='/api/subscriptions'),false);
});
test('certificate setup submits once, follows existing job, then returns to the unchanged node inputs',async()=>{
    const f=screen({active:true});f.certs.length=0;f.view.options.remote=false;await f.view.open('direct');
    f.view.fields.name.value='证书后继续';f.view.fields.port.value='19443';f.view.dirty=true;
    let submitted=0;
    const original=f.view.flow.request;
    f.view.flow.request=async(path,method,body,options)=>{
        if(path==='/api/certificates'&&method==='POST'){submitted++;f.certs.push({id:'c'.repeat(32),domain:'vpn.example.test',environment:'production',revision:rev(999),days_remaining:90});return {id:'d'.repeat(32),state:'queued'};}
        if(path.startsWith('/api/certificates/jobs/'))return {state:'succeeded'};
        return original(path,method,body,options);
    };
    f.view.certificateSetup();f.view.certificateSetup();assert.equal(f.root.querySelectorAll('.ops-certificate').length,1);
    const box=f.root.querySelector('.ops-certificate');box.querySelector('input[type=email]').value='fake@example.test';box.querySelector('input[type=checkbox]').checked=true;
    button(f,'申请正式证书').click();await settle();
    assert.equal(submitted,1);assert.equal(f.view.fields.name.value,'证书后继续');assert.equal(f.view.fields.port.value,'19443');
    assert.equal(f.view.submit.disabled,false);assert.equal(f.root.querySelector('.ops-certificate'),null);
});
test('scope confirmations freeze the displayed list and are invalidated by same-page node changes',async()=>{
    const f=screen({active:true,upstream:true,settings:{local_proxy_enabled:true,local_username:'old',local_password_set:true}});await f.view.open('host');
    f.view.fields.upstream.value='new';f.view.fields.upstream.dispatchEvent({type:'change',bubbles:true});f.view.fields.link.value='vless://fake-new';f.view.fields.link.dispatchEvent({type:'input',bubbles:true});
    f.view.fields.impact.checked=true;f.view.fields.impact.dispatchEvent({type:'change',bubbles:true});
    const acknowledged=f.view.choice().impactStamp;assert.ok(acknowledged);
    f.view.flow.nodes.push({id:9,remark:'刚启用的节点',core:'sing-box',enable:true,routing_mode:'relay',mutation_revision:rev(910),content_revision:rev(911)});
    assert.equal(f.view.choice().impactStamp,acknowledged,'Reading choice cannot broaden the acknowledged scope');
    f.view.changed({nodes:true});assert.equal(f.view.fields.impact.checked,false);assert.equal(f.view.choice().impactStamp,null);
    assert.ok(f.view.fields.impactList.textContent.includes('刚启用的节点'));assert.equal(f.view.submit.disabled,true);
});
test('same-instance content changes between list selection and opening share never read replacement credentials',async()=>{
    const row={id:2,port:8001,core:'xray',mutation_revision:rev(90),content_revision:rev(92),share_eligible:true};
    const f=screen({active:true,nodes:[row]});
    await f.view.open('share',{id:2,revision:rev(90),contentRevision:rev(91)});await settle();
    assert.ok(f.view.problem.textContent.includes('变化'));assert.equal(f.calls.some(c=>c.path.startsWith('/api/subscription')),false);
});
