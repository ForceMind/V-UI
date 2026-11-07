'use strict';
// Synthetic event/fetch harness for production JS. No browser or rendering.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('web/js/maintenance.js', 'utf8');
const flush = async () => { for (let i=0;i<30;i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b}); return {promise,resolve,reject}; };
function boot(hash='',options={}) {
    const nodes = new Map(), events = {}, requests=[], history=[], copies=[];
    class Element {
        constructor(){this.hidden=false;this.value='';this.textContent='';this.disabled=false;this.children=[];this.events={};this.dataset={};}
        append(node){this.children.push(node)}
        addEventListener(name, fn){this.events[name]=fn}
        focus(){this.focused=true} select(){this.selected=true}
        querySelectorAll(query){return query==='input:checked' ? this.checked || [{value:'diagnose'}] : this.buttons || []}
        fire(name='click'){this.events[name]?.({preventDefault(){}})}
    }
    const el=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id)};
    for(const id of ['maintenance-admin','maintenance-remote','maintenance-tasks-panel','maintenance-audit-panel','maintenance-issued'])el(id).hidden=true;
    el('maintenance-duration').value='1800';
    el('maintenance-actions').buttons=['status','backup','restart','start','stop','upgrade'].map(action=>{const e=new Element();e.dataset.task=action;return e});
    const c={console,AbortController,setTimeout:options.setTimeout||setTimeout,clearTimeout:options.clearTimeout||clearTimeout,URL,location:{hash,pathname:'/maintenance',origin:'https://panel.example.test'},
        history:{replaceState(...args){history.push(args)}},
        document:{readyState:'complete',getElementById:el,createElement:()=>new Element()},
        navigator:{clipboard:{async writeText(value){copies.push(value)}}},confirm:()=>true,
        addEventListener(name, fn){(events[name]??=[]).push(fn)},
        fetch(url, options){const d=deferred();requests.push({url,options,...d});return d.promise}};
    vm.createContext(c);vm.runInContext(source,c);
    const reply=async(value,status=200)=>{const q=requests.at(-1);q.resolve({ok:status<400,status,async json(){return value}});await flush();return q};
    return {c,el,nodes,requests,history,copies,reply,event(name){events[name]?.forEach(fn=>fn())}};
}
async function admin() {
    const h=boot();assert.equal(h.requests[0].url,'/api/auth/me');
    await h.reply({id:1});assert.equal(h.requests.at(-1).url,'/api/maintenance/grants');
    await h.reply([]);assert.equal(h.requests.at(-1).url,'/api/maintenance/audit');await h.reply([]);return h;
}
(async()=>{
    const secret='vui_mg_'+'A'.repeat(43),session='vui_ms_'+'B'.repeat(43);
    {
        const h=boot('#grant='+secret);
        assert.equal(h.history[0][2],'/maintenance');
        const q=h.requests[0];assert.equal(q.url,'/api/maintenance/exchange');
        assert.equal(q.options.credentials,'omit');assert(!q.options.headers.Authorization);
        assert.equal(JSON.parse(q.options.body).grant,secret);
        await h.reply({token:session,scopes:['diagnose'],expires_at:1800000000});
        assert.equal(h.requests.at(-1).options.headers.Authorization,'Bearer '+session);
        await h.reply([]);
        assert.equal(h.el('maintenance-admin').hidden,true);
        assert.equal(h.el('maintenance-actions').buttons.find(b=>b.dataset.task==='upgrade').disabled,true);
        assert(!h.el('maintenance-session').textContent.includes(session));
        h.el('maintenance-refresh-tasks').fire();await flush();
        const pending=h.requests.at(-1);h.event('pagehide');assert(pending.options.signal.aborted);
        await h.reply([{id:'late',action:'upgrade',state:'succeeded'}]);
        assert.equal(h.el('maintenance-tasks').textContent,'');assert(h.el('maintenance-remote').hidden);
    }
    {
        const h=boot('#grant=malformed');await flush();assert.equal(h.requests.length,0);
        assert(!h.history[0][2].includes('malformed'));
    }
    {
        const h=await admin();
        h.el('maintenance-grant-form').fire('submit');await flush();
        let q=h.requests.at(-1);assert.equal(q.options.credentials,'same-origin');
        assert.equal(JSON.parse(q.options.body).scopes.join(','),'diagnose');
        assert.equal(JSON.parse(q.options.body).expires_seconds,1800);
        const link='https://panel.example.test/maintenance#grant='+secret;
        await h.reply({id:3,link});await h.reply([]);
        assert.equal(h.el('maintenance-link').value,link);
        h.el('maintenance-copy').fire();await flush();assert.deepEqual(h.copies,[link]);
        h.el('maintenance-clear').fire();assert.equal(h.el('maintenance-link').value,'');assert(h.el('maintenance-issued').hidden);
        h.el('maintenance-duration').value='15552000';
        h.el('maintenance-grant-form').fire('submit');await flush();
        assert.equal(JSON.parse(h.requests.at(-1).options.body).expires_seconds,15552000);
        assert.equal(JSON.parse(h.requests.at(-1).options.body).scopes.join(','),'diagnose');
        await h.reply({id:4,link:'https://evil.test/#grant='+secret});
        assert.equal(h.el('maintenance-link').value,'');assert(!h.el('maintenance-notice').textContent.includes(secret));
        h.el('maintenance-backup-id').value='/private/arbitrary';h.el('maintenance-restore').fire();await flush();
        assert(h.el('maintenance-notice').textContent.includes('选择成功完成的私有备份'));
        const before=h.requests.length;h.el('maintenance-backup-id').value='a'.repeat(32);h.el('maintenance-restore').fire();await flush();
        assert.equal(h.requests.length,before+1);q=h.requests.at(-1);assert.equal(JSON.parse(q.options.body).backup_id,'a'.repeat(32));
        await h.reply({id:'b'.repeat(32)});await h.reply([]);h.event('pagehide');
    }
    {
        const h=await admin();h.el('maintenance-refresh-grants').fire();await flush();
        await h.reply({detail:'private-server-message-'+secret},503);
        assert(!h.el('maintenance-notice').textContent.includes(secret));
        assert(h.el('maintenance-notice').textContent.includes('系统维护组件'));
    }
    {
        const h=await admin();h.el('maintenance-refresh-logs').fire();await flush();
        await h.reply({entries:[{timestamp:1800000000,code:'PERMISSION_DENIED',summary:'private-raw-'+secret}]});
        const display=h.el('maintenance-logs').children.at(-1).children[0].textContent;
        assert(display.includes('权限不足'));assert(!display.includes(secret));
        h.el('maintenance-refresh-status').fire();await flush();
        await h.reply({release_id:'0.4.16-'+'a'.repeat(12),services:{'v-ui.service':'active'}});
        assert(h.el('maintenance-installation').textContent.includes('0.4.16-'));assert(h.el('maintenance-installation').textContent.includes('运行中'));
    }
    {
        const timers=new Map();let number=0;
        const h=boot('',{setTimeout:(fn,ms)=>{const key=++number;timers.set(key,{fn,ms});return key;},clearTimeout:key=>timers.delete(key)});
        await h.reply({id:1});await h.reply([]);await h.reply([]);
        h.el('maintenance-actions').buttons.find(b=>b.dataset.task==='upgrade').fire();await flush();
        const submitted=h.requests.at(-1);assert.equal(JSON.parse(submitted.options.body).action,'upgrade');
        await h.reply({id:'c'.repeat(32),state:'queued'});await h.reply([]);
        assert(h.el('maintenance-actions').buttons.find(b=>b.dataset.task==='upgrade').disabled);
        const beforeStatus=h.requests.length;h.el('maintenance-actions').buttons.find(b=>b.dataset.task==='status').fire();await flush();
        assert.equal(h.requests.length,beforeStatus+1);assert.equal(h.requests.at(-1).url,'/api/maintenance/status');
        await h.reply({release_id:'0.4.21-'+'a'.repeat(12),services:{'v-ui.service':'active'}});
        const tick=[...timers.values()].find(t=>t.ms===1000);assert(tick);tick.fn();await flush();
        assert(h.requests.at(-1).url.endsWith('/'+'c'.repeat(32)));assert.equal(h.requests.at(-1).options.method,'GET');
        await h.reply({id:'c'.repeat(32),action:'upgrade',state:'succeeded'});await h.reply([]);
        assert(!h.el('maintenance-actions').buttons.find(b=>b.dataset.task==='upgrade').disabled);
        assert(h.el('maintenance-notice').textContent.includes('成功'));
        assert.equal(h.requests.filter(r=>r.options.method==='POST'&&r.url==='/api/maintenance/tasks').length,1);
        h.event('pagehide');
    }
    process.stdout.write('Maintenance fragment/session/scope/cleanup/error/restore synthetic contracts passed\n');
})().catch(error=>{console.error(error);process.exitCode=1});
