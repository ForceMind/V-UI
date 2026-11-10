'use strict';
// Synthetic event/fetch harness for production JS. No browser or rendering.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('web/js/maintenance.js', 'utf8');
const flush = async () => { for (let i=0;i<30;i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b}); return {promise,resolve,reject}; };
function boot(hash='',options={}) {
    const nodes = new Map(), events = {}, requests=[], history=[], copies=[], confirmations=[];
    class Element {
        constructor(){this.hidden=false;this.value='';this._text='';this.disabled=false;this.children=[];this.events={};this.dataset={};}
        get textContent(){return this._text}
        set textContent(value){this._text=value;this.children=[]}
        append(node){this.children.push(node)}
        addEventListener(name, fn){this.events[name]=fn}
        focus(){this.focused=true} select(){this.selected=true}
        querySelectorAll(query){return query==='input:checked' ? this.checked || [{value:'diagnose'}] : this.buttons || []}
        fire(name='click'){this.events[name]?.({preventDefault(){}})}
    }
    const el=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id)};
    for(const id of ['maintenance-admin','maintenance-remote','maintenance-tasks-panel','maintenance-audit-panel','maintenance-issued','maintenance-operations','maintenance-core-panel','maintenance-core-work','maintenance-logs-panel'])el(id).hidden=true;
    el('maintenance-duration').value='1800';
    el('maintenance-actions').buttons=['status','backup','restart','start','stop','upgrade'].map(action=>{const e=new Element();e.dataset.task=action;return e});
    const c={console,AbortController,setTimeout:options.setTimeout||setTimeout,clearTimeout:options.clearTimeout||clearTimeout,URL,location:{hash,pathname:'/maintenance',origin:'https://panel.example.test'},
        history:{replaceState(...args){history.push(args)}},
        document:{readyState:'complete',getElementById:el,createElement:()=>new Element()},
        ...(options.operations?{VUI_SIMPLE_OPERATIONS:options.operations}:{}),
        navigator:{clipboard:{async writeText(value){copies.push(value)}}},confirm:message=>{confirmations.push(message);return options.confirm!==false},
        crypto:{getRandomValues:array=>{array.fill(23);return array}},
        addEventListener(name, fn){(events[name]??=[]).push(fn)},
        fetch(url, options){const d=deferred();requests.push({url,options,...d});return d.promise}};
    vm.createContext(c);vm.runInContext(source,c);
    const reply=async(value,status=200)=>{const q=requests.at(-1);q.resolve({ok:status<400,status,async json(){return value}});await flush();return q};
    return {c,el,nodes,requests,history,copies,confirmations,reply,event(name){events[name]?.forEach(fn=>fn())}};
}
async function admin() {
    const h=boot();assert.equal(h.requests[0].url,'/api/auth/me');
    await h.reply({id:1});assert.equal(h.requests.at(-1).url,'/api/maintenance/grants');
    await h.reply([]);assert.equal(h.requests.at(-1).url,'/api/maintenance/audit');await h.reply([]);return h;
}
(async()=>{
    const secret='vui_mg_'+'A'.repeat(43),session='vui_ms_'+'B'.repeat(43);
    {
        let mounts=0;
        const h=boot('#grant='+secret,{operations:{mount(){mounts++;return {destroy(){}}}}});
        await h.reply({token:session,scopes:['diagnose','core_read'],expires_at:1900000000});
        assert.equal(mounts,0);assert.equal(h.requests.length,1);
        for(const id of ['maintenance-operations','maintenance-tasks-panel','maintenance-audit-panel','maintenance-logs-panel'])assert(h.el(id).hidden,id);
        assert(!h.el('maintenance-core-work').hidden);assert(!h.el('maintenance-core-panel').hidden);h.event('pagehide');
    }
    {
        let mounts=0;
        const h=boot('#grant='+secret,{operations:{mount(){mounts++;return {destroy(){}}}}});
        await h.reply({token:session,scopes:['diagnose'],expires_at:1900000000});
        assert.equal(mounts,1);assert.equal(h.requests.at(-1).url,'/api/maintenance/audit');await h.reply([]);
        assert(!h.el('maintenance-tasks-panel').hidden);assert(!h.el('maintenance-audit-panel').hidden);assert(h.el('maintenance-core-work').hidden);h.event('pagehide');
    }
    {
        const html=fs.readFileSync('web/maintenance.html','utf8');
        const advanced=html.match(/<details\b([^>]*)id="maintenance-core-advanced"([^>]*)>([\s\S]*?)<\/details>/);
        assert(advanced);assert(!/\bopen\b/.test(advanced[1]+advanced[2]));
        assert(advanced[3].includes('高级：查看未验证官方信息'));
        for(const id of ['maintenance-core-hints-sing-box','maintenance-core-hints-xray','maintenance-core-hints'])assert(advanced[3].includes('id="'+id+'"'));
        for(const id of ['maintenance-core-authority','maintenance-core-refresh','maintenance-core-check'])assert(!advanced[3].includes('id="'+id+'"'));
    }
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
        const core='vui_cg_'+'b'.repeat(32)+'_'+'c'.repeat(43);
        const h=boot('#grant='+secret+'&core='+core);
        await h.reply({token:session,scopes:['diagnose','core_read'],expires_at:1800000000});
        assert(!h.history[0][2].includes(core));
        assert.equal(h.requests[0].options.headers['X-VUI-Core-Authorization'],undefined);
        assert.equal(h.requests.at(-1).url,'/api/maintenance/core-authority');
        assert.equal(h.requests.at(-1).options.headers['X-VUI-Core-Authorization'],core);
        await h.reply({scopes:['core_operate','core_backup'],expires_at:1800000000,revoked:false});
        await h.reply([]);
        h.el('maintenance-core-refresh').fire();await flush();
        assert(!h.requests.at(-1).options.headers['X-VUI-Core-Authorization']);
        await h.reply({outcome:'bundle_default'});
        h.event('pagehide');
    }
    {
        const core='vui_cg_'+'b'.repeat(32)+'_'+'c'.repeat(43);
        for(const status of [200,401]) {
            const h=boot('#grant='+secret+'&core='+core);
            await h.reply({token:session,scopes:['diagnose','core_read'],expires_at:1800000000});
            assert.equal(h.requests.at(-1).url,'/api/maintenance/core-authority');
            h.event('pagehide');
            await h.reply({scopes:['core_operate'],expires_at:1800000000,revoked:false},status);
            assert(h.el('maintenance-core-panel').hidden);assert(h.el('maintenance-remote').hidden);
            assert(h.el('maintenance-operations').hidden);assert.equal(h.el('maintenance-core-authority').textContent,'');
        }
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
    {
        const h=boot('#grant='+secret);
        await h.reply({token:session,scopes:['core_read'],expires_at:Date.now()/1000+100});
        assert.equal(h.requests.length,1);assert.equal(h.el('maintenance-core-panel').hidden,false);
        assert.equal(h.el('maintenance-tasks-panel').hidden,true);assert.equal(h.el('maintenance-audit-panel').hidden,true);
        h.el('maintenance-core-refresh').fire();await flush();assert.equal(h.requests.at(-1).url,'/api/maintenance/core-selection');
        await h.reply({outcome:'bundle_default'});
        assert(h.el('maintenance-core-selection').children.some(row=>row.textContent.includes('随面板套件')));
        h.event('pagehide');assert.equal(h.el('maintenance-core-panel').hidden,true);assert.equal(h.el('maintenance-core-selection').textContent,'');
    }
    {
        const h=await admin();
        const check={value:'core_read',checked:true};h.el('maintenance-scopes').buttons=[check];
        h.el('maintenance-permission-preset').value='full';h.el('maintenance-permission-preset').fire('change');
        assert.equal(check.checked,false);
    }
    {
        const h=boot('#grant='+secret);await h.reply({token:session,scopes:['core_read'],expires_at:Date.now()/1000+100});
        h.el('maintenance-core-hints-sing-box').fire();await flush();assert.equal(h.requests.at(-1).url,'/api/maintenance/core-candidates/sing-box');
        await h.reply({core:'sing-box',qualification:'unverified',hints:[],truncated:false});
        assert(h.el('maintenance-core-hints').children.some(row=>row.textContent.includes('尚未完成本机兼容')));
        assert(h.el('maintenance-core-hints').children.some(row=>row.textContent.includes('暂无候选')));
        h.el('maintenance-core-hints-xray').fire();await flush();await h.reply({detail:'/private/token'},503);
        assert.equal(h.el('maintenance-core-hints').textContent,'');assert(h.el('maintenance-notice').textContent.includes('核心选择未改变'));
        h.el('maintenance-core-hints-sing-box').fire();await flush();
        await h.reply({core:'sing-box',qualification:'unverified',hints:[{version:'1.14.3',revision:1},{}],truncated:false});
        assert.equal(h.el('maintenance-core-hints').textContent,'');assert(h.el('maintenance-notice').textContent.includes('响应无效'));
        h.event('pagehide');assert.equal(h.el('maintenance-core-hints').textContent,'');
    }
    // Core UI: fixed server facts, no runtime/binary/network execution.
    const rootKey='vui_cg_'+'e'.repeat(32)+'_'+'f'.repeat(43),revision='a'.repeat(64),backupID='b'.repeat(32);
    const facts=()=>({outcome:'observed',app_source:'a'.repeat(40),target:'x86_64-gnu',selection_revision:revision,generation:3,
        cores:{'sing-box':{current:'1.14.2',previous:'1.14.1',candidates:[{release_id:101,version:'1.14.3'}]},xray:{current:'26.3.27',previous:null,candidates:[{release_id:102,version:'26.10.21'}]}},pending:null,backups:[{id:backupID,created_at:1700000000}]});
    const coreRow=(action='observe',state='succeeded',id='1'.repeat(32),result=action==='observe'&&state==='succeeded'?facts():undefined)=>({id,action,state,phase:state==='succeeded'||state==='failed'?'finished':'preflight',created_at:1700000000,updated_at:1700000001,...(result!==undefined?{result}:{})});
    const coreBoot=async(rootScopes=['core_operate','core_backup','core_restore'],options={})=>{
        const timers=new Map();let number=0;
        const h=boot('#grant='+secret+(rootScopes?'&core='+rootKey:''),{...options,setTimeout:(fn,ms)=>{const key=++number;timers.set(key,{fn,ms});return key},clearTimeout:key=>timers.delete(key)});
        await h.reply({token:session,scopes:['core_read'],expires_at:1900000000});
        if(rootScopes)await h.reply({scopes:rootScopes,expires_at:1900000000,revoked:false});
        return {...h,timers,tick:async ms=>{const entry=[...timers.entries()].find(([,row])=>row.ms===ms);assert(entry,'missing timer '+ms);timers.delete(entry[0]);entry[1].fn();await flush()}};
    };
    const observe=async(h,value=facts())=>{h.el('maintenance-core-check').fire();await flush();assert.equal(h.requests.at(-1).url,'/api/maintenance/core-observations');assert(!h.requests.at(-1).options.headers['X-VUI-Core-Authorization']);await h.reply(coreRow('observe','succeeded','1'.repeat(32),value));};
    {
        const h=await coreBoot();await observe(h);
        vm.runInContext('Date.now=()=>1900000000001',h.c);
        await h.tick(60000);
        assert(h.el('maintenance-core-authority').textContent.includes('已到期'));
        assert(h.el('maintenance-core-backup').disabled);
        h.el('maintenance-core-task-refresh').fire();await flush();
        assert.equal(h.requests.at(-1).url,'/api/maintenance/core-tasks');
        assert(!h.requests.at(-1).options.headers['X-VUI-Core-Authorization']);
        await h.reply([]);h.event('pagehide');
    }
    {
        const h=await coreBoot();
        h.el('maintenance-core-task-refresh').fire();await flush();
        assert(!h.requests.at(-1).options.headers['X-VUI-Core-Authorization']);
        await h.reply([coreRow('recover','succeeded','9'.repeat(32),{outcome:'restored_previous',panel_running:false})]);
        const values=h.el('maintenance-core-task-list').children.flatMap(row=>row.children.map(value=>value.textContent));
        assert(values.some(value=>value.includes('已恢复到原有版本')));
        assert(values.some(value=>value.includes('面板服务保持停止')));h.event('pagehide');
    }
    {
        const h=await coreBoot();await observe(h);
        h.el('maintenance-core-candidate-sing-box').value='101';h.el('maintenance-core-candidate-sing-box').fire('change');assert(!h.el('maintenance-core-update-sing-box').disabled);
        h.el('maintenance-core-update-sing-box').fire();await flush();
        const posted=h.requests.at(-1);assert.equal(posted.url,'/api/maintenance/core-tasks');assert.equal(posted.options.headers['X-VUI-Core-Authorization'],rootKey);
        assert.deepEqual(JSON.parse(posted.options.body),{action:'update',parameters:{core:'sing-box',release_id:101,selection_revision:revision}});
        assert.match(posted.options.headers['Idempotency-Key'],/^[a-f0-9]{32}$/);assert(h.confirmations.at(-1).includes('1.14.2')&&h.confirmations.at(-1).includes('1.14.3'));
        await h.reply(coreRow('update','running','2'.repeat(32),undefined));await h.tick(1000);assert(h.requests.at(-1).url.endsWith('/'+'2'.repeat(32)));
        await h.reply({detail:'private original error'},503);await h.tick(5000);assert(h.requests.at(-1).url.endsWith('/'+'2'.repeat(32)));
        await h.reply(coreRow('update','succeeded','2'.repeat(32),{outcome:'switched',version:'1.14.3',selection_revision:'c'.repeat(64)}));
        assert(h.el('maintenance-core-progress').textContent.includes('核心已更新'));assert.equal(h.requests.filter(row=>row.options.method==='POST'&&row.url.endsWith('/core-tasks')).length,1);h.event('pagehide');
    }
    {
        const h=await coreBoot(['core_operate']);await observe(h);
        assert(h.el('maintenance-core-backup').disabled);assert(h.el('maintenance-core-restore').disabled);
        h.el('maintenance-core-rollback-sing-box').fire();await flush();assert.equal(JSON.parse(h.requests.at(-1).options.body).action,'rollback');assert(h.confirmations.at(-1).includes('1.14.1'));
        await h.reply(coreRow('rollback','succeeded','3'.repeat(32),{outcome:'switched'}));h.event('pagehide');
    }
    {
        const h=await coreBoot(['core_operate'],{confirm:false});await observe(h);
        const choice=h.el('maintenance-core-candidate-xray');choice.value='102';choice.fire('change');
        assert(!h.el('maintenance-core-update-xray').disabled);
        const requests=h.requests.length;h.el('maintenance-core-update-xray').fire();await flush();
        assert(h.confirmations.at(-1).includes('26.10.21')&&h.confirmations.at(-1).includes('服务器回环地址'));
        assert(h.confirmations.at(-1).includes('保留当前版本'));assert.equal(h.requests.length,requests);h.event('pagehide');
    }
    {
        const h=await coreBoot(null);await observe(h);assert(h.el('maintenance-core-update-sing-box').disabled);assert(h.el('maintenance-core-backup').disabled);
        const count=h.requests.length;h.el('maintenance-core-backup').fire();await flush();assert.equal(h.requests.length,count);h.event('pagehide');
    }
    {
        const h=await coreBoot();await observe(h);
        h.el('maintenance-core-backup-choice').value=backupID;h.el('maintenance-core-backup-choice').fire('change');assert(!h.el('maintenance-core-restore').disabled);
        h.el('maintenance-core-restore').fire();await flush();assert.deepEqual(JSON.parse(h.requests.at(-1).options.body),{action:'restore',parameters:{backup_task_id:backupID,selection_revision:revision}});
        assert(h.confirmations.at(-1).includes('撤销旧登录'));await h.reply(coreRow('restore','failed','4'.repeat(32),undefined));h.event('pagehide');
    }
    {
        const h=await coreBoot();const value=facts();value.app_source=null;value.pending={recovery_kind:'restore',transaction:'d'.repeat(32),sequence:5,selection_revision:revision};
        for(const row of Object.values(value.cores)){row.current='待恢复';row.candidates=[];}
        await observe(h,value);assert(h.el('maintenance-core-backup').disabled);assert(!h.el('maintenance-core-recover').disabled);
        h.el('maintenance-core-recover').fire();await flush();assert.deepEqual(JSON.parse(h.requests.at(-1).options.body),{action:'recover',parameters:{recovery_kind:'restore',pending_transaction:'d'.repeat(32),pending_sequence:5,selection_revision:revision}});
        await h.reply(coreRow('recover','succeeded','5'.repeat(32),{outcome:'restored_previous'}));h.event('pagehide');
    }
    {
        const h=await coreBoot(['core_backup']);const value=facts();value.pending={recovery_kind:'backup',transaction:'d'.repeat(32),sequence:1,selection_revision:revision};
        for(const row of Object.values(value.cores))row.candidates=[];
        await observe(h,value);assert(!h.el('maintenance-core-recover').disabled);assert(h.el('maintenance-core-backup').disabled);
        h.el('maintenance-core-recover').fire();await flush();assert.deepEqual(JSON.parse(h.requests.at(-1).options.body),{action:'recover',parameters:value.pending&&{recovery_kind:'backup',pending_transaction:'d'.repeat(32),pending_sequence:1,selection_revision:revision}});
        await h.reply(coreRow('recover','succeeded','5'.repeat(32),{outcome:'backup_recovered',panel_running:false}));
        assert(h.el('maintenance-core-progress').textContent.includes('未重新创建备份'));h.event('pagehide');
    }
    {
        const h=await coreBoot();await observe(h);h.el('maintenance-core-backup').fire();await flush();const first=h.requests.at(-1);await h.reply({},503);
        assert(h.el('maintenance-core-check').disabled);h.el('maintenance-core-task-refresh').fire();await flush();
        assert(h.requests.at(-1).url.endsWith('/core-requests/'+first.options.headers['Idempotency-Key']));assert.equal(h.requests.at(-1).options.headers['X-VUI-Core-Authorization'],rootKey);
        await h.reply({not_found:true});assert.equal(h.el('maintenance-core-retry').hidden,false);
        h.el('maintenance-core-retry').fire();await flush();const retry=h.requests.at(-1);assert.equal(retry.options.body,first.options.body);assert.equal(retry.options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
        await h.reply(coreRow('backup','succeeded','6'.repeat(32),{outcome:'backup_created',backup_id:'6'.repeat(32)}));assert(h.el('maintenance-core-progress').textContent.includes('备份已创建'));h.event('pagehide');
    }
    {
        const h=await coreBoot();await observe(h);h.el('maintenance-core-backup').fire();await flush();await h.reply({},503);
        h.el('maintenance-core-task-refresh').fire();await flush();await h.reply(coreRow('backup','running','7'.repeat(32),undefined));
        assert.equal(h.requests.filter(row=>row.options.method==='POST'&&row.url.endsWith('/core-tasks')).length,1);
        await h.tick(1000);assert(h.requests.at(-1).url.endsWith('/'+'7'.repeat(32)));h.event('pagehide');
        await h.reply(coreRow('backup','succeeded','7'.repeat(32),{outcome:'backup_created',backup_id:'7'.repeat(32)}));assert(h.el('maintenance-core-work').hidden);assert.equal(h.el('maintenance-core-progress').textContent,'');
    }
    {
        const h=await coreBoot();const value=facts();value.candidate_error='secret path must never echo';value.cores.xray.candidates=[];await observe(h,value);
        assert(h.el('maintenance-core-progress').textContent.includes('候选查询未完成'));assert(!h.el('maintenance-core-available').children.some(row=>row.textContent.includes('secret path')));
        h.el('maintenance-core-check').fire();await flush();const bad=facts();bad.cores.xray.candidates.push({release_id:104,version:'private-value'});await h.reply(coreRow('observe','succeeded','8'.repeat(32),bad));
        assert.equal(h.el('maintenance-core-available').textContent,'');assert(!h.el('maintenance-notice').textContent.includes('private-value'));h.event('pagehide');
    }
    process.stdout.write('Maintenance fragment/session/scope/cleanup/error/restore synthetic contracts passed\n');
})().catch(error=>{console.error(error);process.exitCode=1});
