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
    focus(){this.focused=true;}
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
            const status=value?.responseStatus||200;
            return {status,ok:status===200,json:async()=>value};
        },
        location:{replace(){assert.fail('unexpected login redirect');}},
        setTimeout,clearTimeout,URL,TextEncoder,AbortController,crypto:require('node:crypto').webcrypto,
    });
    return {get,requests,posted,queue,emit:async (name,event={})=>{for(const fn of globals.get(name)||[])await fn(event);}};
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
test('failed snapshot refresh invalidates the prior running-version observation',async()=>{
    const revision='a'.repeat(64);
    let snapshotUnavailable=false;
    const f=fixture([],{
        '/api/proxy/settings':()=>{
            if(snapshotUnavailable)throw new Error('temporary snapshot failure');
            return snapshot(revision);
        },
        '/api/proxy/capabilities':()=>({cores:{'sing-box':{
            qualification_baseline:'1.14.2',selected_version:'1.14.2',running:true,
            running_version:'1.14.2',identity_state:'verified',matches_selected:true
        }}})
    });
    await settled(f,revision);
    assert(f.get('core-capabilities').children[1].textContent.includes('已核对运行版本'));
    snapshotUnavailable=true;
    await f.get('refresh').emit('click');
    await settled(f,revision);
    assert(f.get('core-capabilities').children[1].textContent.includes('运行版本未核实'));
    assert.equal(f.get('notice').className,'error');
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
test('pasted links show qualification and add only the explicitly supplied SNI to the chosen row',async()=>{
    const revision='a'.repeat(64);
    const foreign='vmess://synthetic';
    const legacy='vless://00000000-0000-4000-8000-000000000001@node.example.test:443?type=tcp&security=tls#Legacy';
    const eligible=legacy.replace('#Legacy','&sni=other.example.test#Existing');
    const f=fixture([snapshot(revision)],{
        '/api/proxy/import/preview':options=>{
            const {content}=JSON.parse(options.body);
            const repaired=content.includes('&sni=cert.example.test#Legacy');
            return {source_format:'uri',ignored_sections:[],notice_codes:[],candidates:[
                {index:0,protocol:'vmess',supported:false,reason_code:'UNSUPPORTED_PROTOCOL'},
                {index:1,protocol:'vless',transport:'tcp',tls_mode:'tls',name:'Legacy',supported:repaired,
                    reason_code:repaired?'':'MISSING_TLS_SERVER_NAME'},
                {index:2,protocol:'vless',transport:'tcp',tls_mode:'tls',name:'Existing',supported:true}
            ]};
        }
    });
    await settled(f,revision);
    f.get('import-dialog').open=true;
    f.get('import-format').value='uri';
    f.get('import-content').value=[foreign,'',legacy,eligible].join('\n');
    await f.get('preview-import').emit('click');
    const [unsupported,missing,editor,supported]=f.get('candidates').children;
    assert(unsupported.children[0].disabled);
    assert(missing.children[0].disabled);
    assert.equal(supported.children[0].disabled,false);
    assert(supported.children[1].children.some(node=>node.textContent==='可导入'));
    assert(unsupported.children[1].children.some(node=>node.textContent.includes('仅支持 VLESS')));
    assert(f.get('save-import').disabled);
    assert.equal(editor.children[0].value,'');
    editor.children[0].value='cert.example.test';
    await editor.children[1].emit('click');
    const requests=f.posted.filter(row=>row.url==='/api/proxy/import/preview');
    assert.equal(requests.length,2);
    assert.equal(requests[1].body.content,[foreign,'',legacy.replace('#','&sni=cert.example.test#'),eligible].join('\n'));
    assert.equal(f.get('candidates').children[1].children[0].disabled,false);
    assert(f.get('save-import').disabled);
    assert.equal(f.posted.filter(row=>row.url==='/api/proxy/profiles').length,0);
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
    const checkCount=f.posted.length;
    await f.emit('vui-yaml-draft-view',{detail:revision});
    assert.equal(f.posted.length,checkCount);
    await f.get('yaml-review-saved').emit('click');
    assert(!f.get('apply-review').hidden);
    assert(f.get('connection-advanced').open && f.get('connection-setup').open);
    assert(f.get('review-heading').focused);
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
    assert.match(f.get('yaml-transaction-state').textContent,/已确认活动修订/);
});

function yamlFixture(){
    const revision='a'.repeat(64),previous='b'.repeat(64),checkId='c'.repeat(64);
    const saved={...snapshot(revision),execution_mode:'yaml',yaml_plan:{revision:'d'.repeat(64)}};
    saved.active={revision:previous,settings:saved.settings,execution_mode:'yaml'};
    const check={revision,snapshot:saved,valid:true,phase:'checked',applied:false,running:true,enabled:true,
        coverage:[],source_fingerprint:'e'.repeat(64),yaml_plan:{revision:'f'.repeat(64),endpoint_count:1},
        yaml_confirmation:{check_id:checkId,expires_at:Math.floor(Date.now()/1000)+300}};
    return {revision,previous,saved,check,capabilities:{apply_available:true}};
}
test('port refusal and rollback report the preserved revision only after a fresh matching read',async()=>{
    const {revision,previous,saved,check,capabilities}=yamlFixture();
    for(const [code,expected] of [['PROXY_LOCAL_PORT_OCCUPIED',/端口占用.*原活动修订/],
        ['PROXY_APPLY_FAILED',/已回退.*原活动修订/],['PROXY_TRANSACTION_NOT_ACTIVE',/原事务.*原活动修订/]]){
        const f=fixture([saved,saved],{'/api/proxy/capabilities':()=>capabilities,'/api/proxy/check':()=>check,
            '/api/proxy/apply':()=>({responseStatus:409,detail:code})});
        await settled(f,revision);
        await f.get('yaml-review-saved').emit('click');
        assert.match(f.get('review-active-revision').textContent,new RegExp(previous));
        await f.get('confirm-apply').emit('click');
        assert.match(f.get('yaml-transaction-state').textContent,expected);
        assert.match(f.get('yaml-active-state').textContent,new RegExp(previous.slice(0,12)));
        assert.equal(f.requests.filter(url=>url==='/api/proxy/settings').length,2);
    }
    const f=fixture([saved,{...saved,pending:{revision}}],{'/api/proxy/capabilities':()=>capabilities,
        '/api/proxy/check':()=>check,'/api/proxy/apply':()=>({responseStatus:409,detail:'PROXY_APPLY_FAILED'})});
    await settled(f,revision);
    await f.get('yaml-review-saved').emit('click');
    await f.get('confirm-apply').emit('click');
    assert.match(f.get('yaml-transaction-state').textContent,/事务尚未结束/);
    assert.doesNotMatch(f.get('yaml-transaction-state').textContent,/已回退/);
});

test('pending recovery and unknown apply keep refresh read-only and retry the original confirmation',async()=>{
    const {revision,saved,check,capabilities}=yamlFixture();
    const pending={...saved,pending:{revision}}, active={...saved,active:{...saved.active,revision}};
    let sent=0;
    const f=fixture([saved,pending,saved,active],{'/api/proxy/capabilities':()=>capabilities,'/api/proxy/check':()=>check,
        '/api/proxy/apply':()=> ++sent===1 ? {responseStatus:503,detail:'PROXY_RECOVERY_REQUIRED'} :
            {applied:true,phase:'committed',snapshot:active}});
    await settled(f,revision);
    await f.get('yaml-review-saved').emit('click');
    await f.get('confirm-apply').emit('click');
    assert.match(f.get('yaml-transaction-state').textContent,/事务尚未结束/);
    assert(f.get('yaml-review-saved').disabled);
    assert(f.get('yaml-retry-apply').disabled);
    await f.get('yaml-refresh-state').emit('click');
    assert.equal(sent,1);
    assert.match(f.get('yaml-transaction-state').textContent,/应用结果待核对/);
    await f.get('yaml-retry-apply').emit('click');
    const applies=f.posted.filter(row=>row.url==='/api/proxy/apply');
    assert.deepEqual(applies[0].body,applies[1].body);
    assert.equal(applies[0].options.headers['Idempotency-Key'],applies[1].options.headers['Idempotency-Key']);
    assert.match(f.get('yaml-transaction-state').textContent,/已确认活动修订/);
});
