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
function fixture(settings){
    const ids=new Map(),globals=new Map(),requests=[],queue=[...settings];
    for(const match of html.matchAll(/\bid="([^"]+)"/g))ids.set(match[1],new Element());
    const get=id=>{assert(ids.has(id),'actual ID '+id);return ids.get(id);};
    vm.runInNewContext(source,{
        document:{getElementById:get,createElement:()=>new Element()},
        addEventListener:(name,fn)=>globals.set(name,[...(globals.get(name)||[]),fn]),
        fetch:async(url,options)=>{
            requests.push(url);
            let value;
            if(url==='/api/proxy/capabilities')value={};
            else if(url==='/api/inbounds')value=[];
            else {assert.equal(url,'/api/proxy/settings');assert(queue.length);value=await queue.shift();}
            return {status:200,ok:true,json:async()=>value};
        },
        location:{replace(){assert.fail('unexpected login redirect');}},
        setTimeout,clearTimeout,URL,AbortController,
    });
    return {get,requests,queue,emit:async name=>{for(const fn of globals.get(name)||[])await fn({});}};
}
async function settled(f, revision){
    for(let i=0;i<30;i++){
        await flush();
        if(f.get('draft-revision').textContent==='修订 '+revision&&!f.get('refresh').disabled)return;
    }
    assert.fail('workspace did not settle on '+revision);
}
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
