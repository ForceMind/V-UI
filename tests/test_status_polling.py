"""Execute actual dashboard polling/session code with deterministic async clocks."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node required for frontend contracts')
class StatusPollingTests(unittest.TestCase):
    def test_visibility_single_flight_resume_stop_and_session_broadcast(self):
        root=Path(__file__).resolve().parents[1]
        script=r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('web/js/app.js','utf8');
const channels=[],messages=[];
class Channel {
 constructor(name){this.name=name;this.closed=false;channels.push(this)}
 postMessage(data){messages.push(data);for(const c of channels)if(c!==this&&!c.closed&&c.name===this.name)c.onmessage?.({data})}
 close(){this.closed=true}
}
function context(ChannelType){
 let errorHandler;
 const c={addEventListener(){},location:{hash:'',replace(value){c.redirects.push(value)}},redirects:[],stops:0,authStatus:401,authChecks:0,
  Vue:{createApp(options){c.setup=options.setup;return {component(){},use(){},mount(){}}},ref:v=>({value:v}),reactive:v=>v,computed:f=>({get value(){return f()}}),onMounted(fn){c.mounted=fn}},ElementPlus:{ElMessage:{success(){},error(){}},ElMessageBox:{}},ElementPlusIconsVue:{},
  axios:{defaults:{headers:{common:{}}},interceptors:{response:{use(ok,bad){errorHandler=bad}}},get:async(url,options)=>{assert.equal(url,'/api/auth/me');assert.equal(options.timeout,5000);assert.equal(options.validateStatus(401),true);assert.equal(options.validateStatus(500),false);c.authChecks++;return {status:c.authStatus,data:{username:'fresh'}}}},console};
 if(ChannelType)c.BroadcastChannel=ChannelType;
 vm.createContext(c);vm.runInContext(source,c);c.error=()=>errorHandler;
 return c;
}
function environment(hidden=false){
 let next=0;const timers=new Map(),listeners=new Set();
 const document={hidden,addEventListener(name,fn){assert.equal(name,'visibilitychange');listeners.add(fn)},removeEventListener(name,fn){listeners.delete(fn)}};
 return {document,timers,listeners,setTimeout(fn,delay){const id=next++;timers.set(id,{fn,delay});return id},clearTimeout(id){timers.delete(id)},
  onError(error){throw error},show(hidden){document.hidden=hidden;for(const fn of listeners)fn()},
  fire(){const [id,t]=timers.entries().next().value;timers.delete(id);return t.fn()}};
}
const flush=async()=>{for(let i=0;i<10;i++)await Promise.resolve()};
(async()=>{
 const c=context();
 // Normal timing, true hidden pause and repeated transitions never duplicate timers.
 let calls=0;const e=environment();const task=c.singleStatusRequest(async()=>{calls++});
 const poll=c.visibleStatusPoller(task,3000,e);
 assert.equal(e.timers.size,1);assert.equal(calls,0);
 await e.fire();assert.equal(calls,1);assert.equal(e.timers.size,1);
 e.show(true);assert.equal(e.timers.size,0);await flush();assert.equal(calls,1);
 e.show(false);await flush();assert.equal(calls,2);assert.equal(e.timers.size,1);
 for(let i=0;i<10;i++){e.show(true);e.show(false);await flush();assert.equal(e.timers.size,1)}
 poll.stop();assert.equal(e.timers.size,0);assert.equal(e.listeners.size,0);
 e.show(false);await flush();assert.equal(calls,12);
 // Both a manual caller and the timer share one real request. Resume during a
 // slow request queues exactly one fresh result after that request completes.
 const e2=environment();let starts=0,active=0,maxActive=0;const finish=[];
 const slow=c.singleStatusRequest(()=>{starts++;active++;maxActive=Math.max(maxActive,active);return new Promise(resolve=>finish.push(()=>{active--;resolve()}))});
 const manual=slow();const duplicate=slow();assert.equal(manual,duplicate);
 await flush();assert.equal(starts,1);
 const p2=c.visibleStatusPoller(slow,3000,e2);const running=e2.fire();await flush();assert.equal(starts,1);
 e2.show(true);e2.show(false);e2.show(true);e2.show(false);
 finish.shift()();await flush();assert.equal(starts,2);assert.equal(maxActive,1);
 finish.shift()();await flush();await running;assert.equal(starts,2);assert.equal(e2.timers.size,1);
 const last=e2.fire();await flush();p2.stop();finish.shift()();await last;await flush();assert.equal(e2.timers.size,0);
 // Hidden initial page does not create a timer until actually shown.
 const e3=environment(true);let count3=0;const p3=c.visibleStatusPoller(async()=>count3++,10000,e3);
 assert.equal(e3.timers.size,0);e3.show(false);await flush();assert.equal(count3,1);p3.stop();
 // A single authenticated 401 ends both tabs, including a hidden/non-polling
 // tab, without credentials or persistent browser storage in the message.
 const a=context(Channel),b=context(Channel);
 vm.runInContext('statusPollers.push({stop(){stops++}})',a);
 vm.runInContext('statusPollers.push({stop(){stops++}})',b);
 await assert.rejects(a.error()({response:{status:401}}));await flush();
 assert.deepEqual(a.redirects,['/login']);assert.deepEqual(b.redirects,['/login']);
 assert.equal(a.stops,1);assert.equal(b.stops,1);assert.deepEqual(messages,['session-ended']);
 a.endAdminSession(true);assert.equal(messages.length,1);
 const fresh=context(Channel);fresh.authStatus=200;fresh.draft='new edits';
 vm.runInContext('statusPollers.push({stop(){stops++}})',fresh);
 await assert.rejects(fresh.error()({response:{status:401}}));await flush();
 assert.deepEqual(fresh.redirects,[]);assert.equal(fresh.stops,0);assert.equal(fresh.draft,'new edits');
 const delayed=new Channel('vui-admin-session');delayed.postMessage('session-ended');await flush();
 assert.deepEqual(fresh.redirects,[]);assert.equal(fresh.stops,0);
 const before=fresh.authChecks;
 await Promise.allSettled([fresh.error()({response:{status:401}}),fresh.error()({response:{status:401}})]);
 assert.equal(fresh.authChecks,before+1);assert.deepEqual(fresh.redirects,[]);
 // A newer logout received while an old /me 200 is in flight must not be lost.
 const hidden=context(Channel),checks=[];
 hidden.axios.get=()=>new Promise(resolve=>checks.push(resolve));
 const oldCheck=hidden.checkAdminSession();await flush();assert.equal(checks.length,1);
 delayed.postMessage('session-ended');await flush();assert.equal(checks.length,1);
 checks[0]({status:200,data:{username:'old-session'}});await flush();assert.equal(checks.length,2);
 assert.deepEqual(hidden.redirects,[]);
 checks[1]({status:401,data:{}});await oldCheck;await flush();assert.deepEqual(hidden.redirects,['/login']);
 // Actual Vue restart must await a fresh read, never reuse pre-mutation data.
 const mutation=context();let requests=0,resolveOld;
 mutation.axios.get=url=>url==='/api/cores/status'?(++requests===1?new Promise(resolve=>resolveOld=resolve):Promise.resolve({status:200,data:{'sing-box':{running:true}}})):Promise.resolve({status:200,data:{}});
 mutation.axios.post=async()=>({status:200,data:{valid:true}});
 const mutationEnvironment=environment();mutation.document=mutationEnvironment.document;mutation.setTimeout=mutationEnvironment.setTimeout;mutation.clearTimeout=mutationEnvironment.clearTimeout;
 const state=mutation.setup();const mounted=mutation.mounted();await flush();assert.equal(requests,1);
 const restart=state.restartCore('sing-box');await flush();assert.equal(requests,1);
 resolveOld({status:200,data:{'sing-box':{running:false}}});await restart;await mounted;
 vm.runInContext('statusPollers.forEach(poller=>poller.stop())',mutation);
 assert.equal(requests,2);assert.equal(state.coreStatus.value['sing-box'].running,true);assert.equal(state.restartingCore.value,'');
 class BrokenChannel {postMessage(){throw Error('synthetic channel unavailable')}close(){throw Error('closed')}}
 const broken=context(BrokenChannel);broken.endAdminSession(true);assert.deepEqual(broken.redirects,['/login']);
 const e4=environment(),errors=[];e4.onError=error=>errors.push(error.message);
 const p4=c.visibleStatusPoller(async()=>{throw Error('synthetic network failure')},3000,e4);
 await e4.fire();assert.deepEqual(errors,['synthetic network failure']);assert.equal(e4.timers.size,1);p4.stop();
 assert(source.includes("axios.get('/api/system/status', { timeout: 5000 })"));
 assert(source.includes("axios.get('/api/cores/status', { timeout: 5000 })"));
 assert(!source.includes('localStorage'));assert(!source.includes('sessionStorage'));
 console.log('Actual polling: hidden pause, bounded timers, manual/timer single flight, fresh resume, stop and cross-tab 401 passed');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        result=subprocess.run(['node','-e',script],cwd=root,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_account_logout_and_profile_change_publish_no_credentials(self):
        root=Path(__file__).resolve().parents[1]
        script=r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const ids=new Map(),messages=[];
function element(id){if(!ids.has(id))ids.set(id,{value:'synthetic-value',hidden:false,textContent:'',events:{},addEventListener(name,fn){this.events[name]=fn}});return ids.get(id)}
class Channel {constructor(name){assert.equal(name,'vui-admin-session')}postMessage(value){messages.push(value)}}
const context={BroadcastChannel:Channel,addEventListener(){},document:{getElementById:element,querySelectorAll(){return []}},location:{replace(){}},
 fetch:async()=>({ok:true,status:200,json:async()=>({username:'tester'})})};
vm.createContext(context);const source=fs.readFileSync('web/js/account.js','utf8');vm.runInContext(source,context);
(async()=>{
 await element('logout').events.click();
 await element('profile-form').events.submit({preventDefault(){}});
 assert.deepEqual(messages,['session-ended','session-ended']);
 for(const key of ['login-password','current-password','new-password'])assert.equal(element(key).value,'');
 assert(!source.includes('localStorage'));assert(!source.includes('sessionStorage'));
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        result=subprocess.run(['node','-e',script],cwd=root,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_delayed_account_me_cannot_restore_invalidated_or_newer_state(self):
        root=Path(__file__).resolve().parents[1]
        script=r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('web/js/account.js','utf8');
function make(timed=false){
 const ids=new Map(),pending=[],timers=new Map();let channel,nextTimer=0;
 const element=id=>{if(!ids.has(id))ids.set(id,{value:'draft',hidden:id==='account-panel'||id==='login-form',textContent:'',events:{},addEventListener(n,fn){this.events[n]=fn}});return ids.get(id)};
 class Channel{constructor(){channel=this}postMessage(){}}
 const c={BroadcastChannel:Channel,addEventListener(){},document:{getElementById:element,querySelectorAll(){return []}},location:{replace(){}},
  fetch:(url,options)=>new Promise((resolve,reject)=>{pending.push({url,options,resolve,reject});options.signal?.addEventListener('abort',()=>reject(Error('synthetic timeout')))})};
 if(timed){c.AbortController=AbortController;c.setTimeout=fn=>{const id=++nextTimer;timers.set(id,fn);return id};c.clearTimeout=id=>timers.delete(id)}
 vm.createContext(c);vm.runInContext(source,c);
 return {pending,element,channel,timers};
}
const flush=async()=>{for(let n=0;n<12;n++)await Promise.resolve()};
const response=(status,user)=>({ok:status===200,status,json:async()=>status===200?{username:user}:{detail:'Authentication required'}});
(async()=>{
 const a=make();assert.equal(a.pending.length,1);
 const checked=a.channel.onmessage({data:'session-ended'});assert.equal(a.pending.length,2);
 a.pending[1].resolve(response(401));await checked;
 assert.equal(a.element('account-panel').hidden,true);assert.equal(a.element('login-form').hidden,false);
 const message=a.element('message').textContent;
 a.pending[0].resolve(response(200,'stale-user'));await flush();
 assert.equal(a.element('account-panel').hidden,true);assert.equal(a.element('login-form').hidden,false);assert.equal(a.element('message').textContent,message);
 const b=make();const current=b.channel.onmessage({data:'session-ended'});
 b.pending[1].resolve(response(200,'new-user'));await current;
 assert.equal(b.element('account-panel').hidden,false);assert.equal(b.element('login-form').hidden,true);
 b.pending[0].resolve(response(200,'stale-user'));await flush();
 assert.equal(b.element('current-name').textContent,'new-user');assert.equal(b.element('profile-name').value,'new-user');
 const preserved=b.channel.onmessage({data:'session-ended'});b.element('profile-name').value='unsaved-name';
 b.pending[2].resolve(response(200,'new-user'));await preserved;
 assert.equal(b.element('profile-name').value,'unsaved-name');assert.equal(b.element('account-panel').hidden,false);
 const timeout=make(true);const verifying=timeout.channel.onmessage({data:'session-ended'});
 assert.equal(timeout.pending.length,2);assert.equal(timeout.element('login-form').hidden,true);
 timeout.timers.get(2)();await verifying;
 assert.equal(timeout.element('account-panel').hidden,true);assert.equal(timeout.element('login-form').hidden,false);
 assert(timeout.element('message').textContent.includes('刷新'));
 timeout.pending[0].resolve(response(200,'stale-after-timeout'));await flush();
 assert.equal(timeout.element('account-panel').hidden,true);assert.equal(timeout.timers.size,0);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        result=subprocess.run(['node','-e',script],cwd=root,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':unittest.main()
