'use strict';
// Run the actual proxy workspace controller with synthetic DOM and fetch.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {test}=require('node:test');
const source=fs.readFileSync(__dirname+'/../web/js/proxy.js','utf8');
const html=fs.readFileSync(__dirname+'/../web/proxy.html','utf8');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
class Element {
    constructor(){this.value='';this.checked=false;this.disabled=false;this.hidden=false;this.open=false;this.textContent='';this.events=new Map();this.children=[];}
    addEventListener(name,fn){this.events.set(name,[...(this.events.get(name)||[]),fn]);}
    async emit(name,event={preventDefault(){}}){for(const fn of this.events.get(name)||[])await fn(event);}
    append(...nodes){this.children.push(...nodes);}
    replaceChildren(...nodes){this.children=[...nodes];}
    setAttribute(key,value){this[key]=String(value);}
    close(){this.open=false;}
    querySelector(){return null;}
    querySelectorAll(){return [];}
}
function snapshot(revision){
    return {revision,settings:{schema_version:1,local_proxy_enabled:false,relay_enabled:false,selected_upstream_id:null,
        local_port:7891,local_username:'',local_password_set:false,dns_policy_version:1,deny_policy_version:1},
        upstreams:[],active:null,pending:null};
}
function fixture(settings,routes={}){
    const ids=new Map(),globals=new Map(),requests=[],posted=[],queue=[...settings];
    for(const match of html.matchAll(/\bid="([^"]+)"/g))ids.set(match[1],new Element());
    const get=id=>{assert(ids.has(id),'actual ID '+id);return ids.get(id);};
    vm.runInNewContext(source,{
        document:{getElementById:get,createElement:()=>new Element()},
        addEventListener:(name,fn)=>globals.set(name,[...(globals.get(name)||[]),fn]),
        fetch:async(url,options)=>{
            requests.push(url);
            if(options?.body)posted.push({url,options,body:JSON.parse(options.body)});
            let value;
            if(Object.hasOwn(routes,url))value=await routes[url](options);
            else if(url==='/api/proxy/capabilities')value={};
            else if(url==='/api/inbounds')value=[];
            else {assert.equal(url,'/api/proxy/settings');assert(queue.length);value=await queue.shift();}
            return {status:200,ok:true,json:async()=>value};
        },
        location:{replace(){assert.fail('unexpected login redirect');}},
        setTimeout,clearTimeout,URL,AbortController,crypto:require('node:crypto').webcrypto,
    });
    return {get,requests,posted,queue,emit:async name=>{for(const fn of globals.get(name)||[])await fn({});}};
}
async function settled(f, revision){
    for(let i=0;i<30;i++){
        await flush();
        if(f.get('draft-revision').textContent==='修订 '+revision&&!f.get('refresh').disabled)return;
    }
    assert.fail('workspace did not settle on '+revision);
}
test('core capability display distinguishes the baseline, selection and verified running version',async()=>{
    const f=fixture([snapshot('a'.repeat(64))],{'/api/proxy/capabilities':()=>({cores:{
        'sing-box':{qualification_baseline:'1.14.2',selected_version:'1.14.3',selection_source:'managed',
            running:true,running_version:'1.14.3',identity_state:'verified',matches_selected:true},
        xray:{qualification_baseline:'26.3.27',selected_version:'26.3.27',selection_source:'bundled',
            running:true,running_version:null,identity_state:'unverified',matches_selected:null}
    }})});
    await settled(f,'a'.repeat(64));
    const rows=f.get('core-capabilities').children.map(row=>row.textContent);
    assert(rows[1].includes('兼容基线 1.14.2'));
    assert(rows[1].includes('当前选择 1.14.3'));
    assert(rows[1].includes('已核对运行版本 1.14.3'));
    assert(rows[0].includes('版本身份未核实'));
    assert(!rows[0].includes('已核对运行版本'));
});
test('legacy and contradictory capabilities never display a verified running version',async()=>{
    for(const capabilities of [{},{cores:{'sing-box':{running:true,identity_state:'verified',
        matches_selected:true,running_version:'1.14.3',selected_version:'1.14.2'},
        xray:{qualification_baseline:'<private>',selected_version:'<script>',running:'true',
            identity_state:'verified',running_version:'26.3.27',matches_selected:true}}}]){
        const f=fixture([snapshot('a'.repeat(64))],{'/api/proxy/capabilities':()=>capabilities});
        await settled(f,'a'.repeat(64));
        const text=f.get('core-capabilities').children.map(row=>row.textContent).join(' ');
        assert(!text.includes('已核对运行版本'));
        assert(!text.includes('<private>'));
        assert(!text.includes('<script>'));
    }
});
test('failed capability refresh clears old verified evidence while retaining the draft refresh',async()=>{
    let reads=0;
    const f=fixture([snapshot('a'.repeat(64)),snapshot('b'.repeat(64))],{
        '/api/proxy/capabilities':()=>{
            if(reads++)throw new Error('temporary network failure');
            return {cores:{'sing-box':{qualification_baseline:'1.14.2',selected_version:'1.14.2',
                running:true,running_version:'1.14.2',identity_state:'verified',matches_selected:true}}};
        }
    });
    await settled(f,'a'.repeat(64));
    assert(f.get('core-capabilities').children[1].textContent.includes('已核对运行版本'));
    await f.get('refresh').emit('click');
    await settled(f,'b'.repeat(64));
    assert(!f.get('core-capabilities').children[1].textContent.includes('已核对运行版本'));
    assert(f.get('core-capabilities').children[1].textContent.includes('未核实'));
});
test('save refresh queues behind an older busy response and ends on the latest revision',async()=>{
    const old=deferred(),f=fixture([old.promise,snapshot('b'.repeat(64))]);
    await flush();
    await f.emit('vui-yaml-draft-saved');
    old.resolve(snapshot('a'.repeat(64)));
    await settled(f,'b'.repeat(64));
    assert.equal(f.requests.filter(url=>url==='/api/proxy/settings').length,2);
    assert(!f.get('save').disabled);
});
test('queued save refresh preserves unsaved form values and requires conflict acknowledgement',async()=>{
    const f=fixture([snapshot('a'.repeat(64)),snapshot('b'.repeat(64))]);
    await settled(f,'a'.repeat(64));
    f.get('local-port').value='9999';
    await f.get('settings-form').emit('input');
    await f.emit('vui-yaml-draft-saved');
    await settled(f,'b'.repeat(64));
    assert.equal(f.get('local-port').value,'9999');
    assert(f.get('save').disabled);
    assert(!f.get('conflict').hidden);
});
test('uncertain write keeps further writes blocked until an explicit server refresh',async()=>{
    const f=fixture([snapshot('a'.repeat(64)),snapshot('b'.repeat(64)),snapshot('b'.repeat(64))]);
    await settled(f,'a'.repeat(64));
    await f.emit('vui-yaml-draft-uncertain');
    await settled(f,'b'.repeat(64));
    assert(f.get('save').disabled);assert(f.get('rebase').disabled);assert(f.get('discard').disabled);
    assert(f.get('notice').textContent.includes('未确认'));
    await f.get('refresh').emit('click');
    await settled(f,'b'.repeat(64));
    assert(!f.get('save').disabled);assert(!f.get('rebase').disabled);
});
test('YAML apply requires the separate confirmation and sends the checked private ID',async()=>{
    const revision='a'.repeat(64),checkId='b'.repeat(64),saved=snapshot(revision);
    saved.execution_mode='yaml';saved.yaml_plan={revision:'c'.repeat(64)};
    const active={...saved,active:{revision,settings:saved.settings,execution_mode:'yaml'}};
    const f=fixture([saved,active],{
        '/api/proxy/capabilities':()=>({apply_available:true}),
        '/api/proxy/check':()=>({revision,snapshot:saved,valid:true,phase:'checked',applied:false,
            running:true,enabled:true,coverage:[],source_fingerprint:'d'.repeat(64),
            yaml_plan:{revision:'e'.repeat(64),endpoint_count:1},
            yaml_confirmation:{check_id:checkId,expires_at:Math.floor(Date.now()/1000)+300}}),
        '/api/proxy/apply':()=>({phase:'committed',applied:true,snapshot:active}),
    });
    await settled(f,revision);
    await f.get('apply').emit('click');
    assert(!f.get('apply-review').hidden);
    assert.equal(f.posted.filter(row=>row.url==='/api/proxy/apply').length,0);
    await f.get('cancel-apply-review').emit('click');
    assert(f.get('apply-review').hidden);
    assert.equal(f.posted.filter(row=>row.url==='/api/proxy/apply').length,0);
    await f.get('apply').emit('click');
    await f.get('confirm-apply').emit('click');
    const applied=f.posted.filter(row=>row.url==='/api/proxy/apply');
    assert.equal(applied.length,1);
    assert.equal(applied[0].body.yaml_check_id,checkId);
    assert.equal(applied[0].body.base_revision,revision);
    assert.equal(applied[0].body.source_fingerprint,'d'.repeat(64));
    assert.match(applied[0].options.headers['Idempotency-Key'],/^[a-f0-9]{64}$/);
    assert(f.get('active-state').textContent.includes('YAML'));
});
