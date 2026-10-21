"""Execute ordinary share controller lifecycles with synthetic credentials only.

Node effects are deliberately fake; the existing explicit Chromium gate also
runs the same source with pinned Vue and real panel responses separately.
"""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('web/js/app.js','utf8');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return{promise,resolve,reject}};
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve()};
function boot(realVue=process.argv[1]||''){
 const pending=[],events={},timers=new Map(),allTimers=[],copies=[],messages=[],redirects=[],qr=[];
 const container={textContent:'',title:''},output={value:'',focus(){this.focused=true},select(){this.selected=true}};
 let api,interceptor,nextTimer=0,copyAction=async()=>{},renderThrows=false,present=true,nextUpdate=null;
 const app={component(){},use(){},mount(){}};
 const c={console,AbortController,addEventListener(name,callback){(events[name]??=[]).push(callback)},
 location:{hash:'#inbounds',replace(v){redirects.push(v)},assign(v){redirects.push(v)}},
 setTimeout(fn){const id=++nextTimer;timers.set(id,fn);allTimers.push(fn);return id},clearTimeout(id){timers.delete(id)},
 navigator:{clipboard:{writeText(value){copies.push(value);return copyAction()}}},
 ElementPlus:{ElMessage:{success:v=>messages.push(v),error:v=>messages.push(v),warning:v=>messages.push(v)},ElMessageBox:{}},ElementPlusIconsVue:{},
 axios:{defaults:{headers:{common:{}}},interceptors:{response:{use(ok,bad){interceptor=bad}}},
 get(url,options){const d=deferred();pending.push({url,options,...d});return d.promise}},
 QRCode:function(target,options){qr.push(options.text);target.textContent='synthetic QR '+options.text;target.title=options.text;if(renderThrows)throw Error('never echo this credential')}
 };
 vm.createContext(c);
 if(realVue){vm.runInContext(fs.readFileSync(realVue,'utf8'),c);c.Vue={...c.Vue,onMounted(){},createApp({setup}){api=setup();return app}}}
 else c.Vue={createApp({setup}){api=setup();return app},onMounted(){},reactive:v=>v,
 ref(value){const callbacks=[];return{callbacks,get value(){return value},set value(v){if(v!==value){value=v;callbacks.forEach(f=>f(v))}}}},
 watch(target,callback,options){assert.equal(options.flush,'sync');if(target?.callbacks)target.callbacks.push(callback)},
 computed:f=>({get value(){return f()}}),nextTick(){return nextUpdate?.promise||Promise.resolve()}};
 c.document={getElementById(id){return id==='qrcode'?(present?container:null):id==='share-link'?output:null}};
 vm.runInContext(source,c);
 return{api,c,pending,events,timers,allTimers,copies,messages,redirects,container,output,qr,
 event(name){events[name].forEach(f=>f({}))},end(){c.endAdminSession()},
 intercept(error){return interceptor(error)},copyWith(fn){copyAction=fn},throwRender(){renderThrows=true},missingContainer(){present=false},
 holdUpdate(){nextUpdate=deferred();return nextUpdate},
 render(){for(const[id,fn]of [...timers]){timers.delete(id);fn()}},
 start(id=1){const done=api.showQrCode({id});return{done,request:pending.at(-1)}},
 async finish(op,link='vless://synthetic-secret@node.test:443?x=%26%2F#中文'){op.request.resolve({data:link});await op.done},
 clear(){assert.equal(api.qrLink.value,'');assert.equal(api.qrNotice.value,'');assert.equal(container.textContent,'');assert.equal(container.title,'');assert.equal(output.value,'');assert.equal(api.qrBusy.value,false);assert.equal(timers.size,0)}
 };
}
'''

SCENARIOS = {
'reverse_completion_and_duplicate_selection': r'''
const h=boot(),a=h.start(1),b=h.start(2);
assert(a.request.options.signal.aborted);assert(h.api.showQrDialog.value);assert(h.api.qrBusy.value);
assert.equal(a.request.url,'/api/subscription/link/1');assert.equal(b.request.options.timeout,15000);assert.equal(b.request.options.responseType,'text');
const exact='vless://synthetic-B@node.test:443?path=%2F%26#中文';await h.finish(b,exact);h.render();
assert.equal(h.api.qrLink.value,exact);assert.equal(h.container.title,exact);assert.deepEqual(h.qr,[exact]);
await h.finish(a,'stale-A');assert.equal(h.api.qrLink.value,exact);
await h.api.copyLink();assert.deepEqual(h.copies,[exact]);assert.match(h.api.qrNotice.value,/已复制/);
const c=h.start(2);assert.equal(h.api.qrLink.value,'');assert.equal(h.container.title,'');assert.equal(h.container.textContent,'');assert(h.api.qrBusy.value);
await h.finish(c,'new-B');h.render();assert.equal(h.api.qrLink.value,'new-B');
''',
'old_error_timer_and_finally_do_not_own_new_state': r'''
const h=boot(),a=h.start();await h.finish(a,'first');const oldTimer=h.allTimers.at(-1);
const b=h.start(2);await h.finish(b,'second');const newTimer=h.allTimers.at(-1);oldTimer();assert.equal(h.timers.size,1);assert.equal(h.container.title,'');
newTimer();assert.equal(h.container.title,'second');
const c=h.start(3),d=h.start(4);c.request.reject({response:{data:'secret error'}});await c.done;
assert(h.api.qrBusy.value);assert.equal(h.api.qrNotice.value,'');assert(!d.request.options.signal.aborted);
await h.finish(d,'fourth');h.render();assert.equal(h.api.qrLink.value,'fourth');
''',
'close_before_response_render_and_reopen': r'''
const h=boot(),a=h.start();h.api.closeQrDialog();h.clear();assert(a.request.options.signal.aborted);
await h.finish(a,'late-secret');h.clear();assert(!h.api.showQrDialog.value);await h.api.copyLink();assert.equal(h.copies.length,0);
const b=h.start();await h.finish(b,'before-render');const stale=h.allTimers.at(-1);h.api.showQrDialog.value=false;h.clear();
const c=h.start(2);await h.finish(c,'fresh');stale();assert.equal(h.container.title,'');assert.equal(h.timers.size,1);h.render();assert.equal(h.container.title,'fresh');
h.output.value='simulated Vue textarea';h.api.closeQrDialog();h.clear();
''',
'each_navigation_refresh_and_session_boundary': r'''
for(const action of ['pagehide','popstate','view','list','failed-list','menu','profile','session','401']){
 const h=boot(),a=h.start();await h.finish(a,'secret-'+action);h.render();h.output.value=h.api.qrLink.value;
 const b=h.start(2);let extra;
 if(action==='pagehide'||action==='popstate')h.event(action);
 if(action==='view')h.api.currentView.value='dashboard';
 if(action==='list'||action==='failed-list'){extra=h.api.fetchInbounds();const request=h.pending.at(-1);assert.equal(request.url,'/api/inbounds');if(action==='list')request.resolve({data:[]});else request.reject(Error('offline'))}
 if(action==='menu')h.api.handleSelect('proxy');
 if(action==='profile')h.api.updateProfile();
 if(action==='session')h.end();
 if(action==='401') {extra=h.intercept({response:{status:401}}).catch(()=>{});await flush();h.pending.at(-1).resolve({status:200,data:{username:'still-authenticated'}})}
 h.clear();assert(!h.api.showQrDialog.value);assert(b.request.options.signal.aborted);
 await h.finish(b,'late-'+action);await extra;h.clear();assert(!h.api.showQrDialog.value);
 await h.api.copyLink();assert.equal(h.copies.length,0);
 if(action==='session'){const before=h.pending.length;await h.api.showQrCode({id:3});assert.equal(h.pending.length,before)}
 else {const fresh=h.start(3);await h.finish(fresh,'new-session-or-view');assert.equal(h.api.qrLink.value,'new-session-or-view')}
}
''',
'clipboard_failure_and_late_feedback': r'''
const h=boot(),a=h.start();await h.finish(a,'copy-secret');h.render();
h.copyWith(async()=>{throw Error('untrusted sensitive clipboard failure')});await h.api.copyLink();assert.match(h.api.qrNotice.value,/复制失败/);assert(h.output.focused&&h.output.selected);assert(!h.api.qrNotice.value.includes('sensitive'));
for(const reject of [false,true]){
 const copy=deferred();h.copyWith(()=>copy.promise);const before=h.copies.length;const pendingCopy=h.api.copyLink();assert.equal(h.copies.length,before+1);
 h.api.closeQrDialog();await h.api.copyLink();assert.equal(h.copies.length,before+1);
 const b=h.start(2);await h.finish(b,'replacement');assert.equal(h.api.qrNotice.value,'');
 if(reject)copy.reject(Error('stale failure'));else copy.resolve();await pendingCopy;assert.equal(h.api.qrNotice.value,'');assert.equal(h.api.qrLink.value,'replacement');
}
const late=deferred();h.copyWith(()=>late.promise);const pending=h.api.copyLink();h.end();late.resolve();await pending;h.clear();
// An issued write is intentionally not recalled or erased from the browser.
assert(h.copies.includes('copy-secret'));const count=h.copies.length;await h.api.copyLink();assert.equal(h.copies.length,count);
''',
'invalid_response_missing_dom_and_partial_qr_failure': r'''
for(const value of ['',null,{},17]){const h=boot(),a=h.start();await h.finish(a,value);assert.equal(h.api.qrLink.value,'');assert.equal(h.timers.size,0);assert.match(h.api.qrNotice.value,/没有/);assert(!h.api.qrBusy.value);await h.api.copyLink();assert.equal(h.copies.length,0)}
const h=boot(),a=h.start();a.request.reject({response:{data:'private upstream diagnostic'}});await a.done;assert.match(h.api.qrNotice.value,/失败/);assert(!h.api.qrNotice.value.includes('private'));assert(!h.api.qrBusy.value);
const absent=boot();absent.missingContainer();await absent.finish(absent.start());absent.render();assert.equal(absent.qr.length,0);absent.api.closeQrDialog();absent.clear();
const broken=boot();broken.throwRender();await broken.finish(broken.start(),'render-secret');broken.render();assert.equal(broken.container.title,'');assert.equal(broken.container.textContent,'');assert.equal(broken.api.qrLink.value,'render-secret');assert.match(broken.api.qrNotice.value,/二维码生成失败/);
''',
}


def run_node(testcase, body, vue=None):
    code = HARNESS + '\n(async()=>{\n' + body + '\n})().catch(error=>{console.error(error);process.exitCode=1});'
    result = subprocess.run(['node', '-e', code] + ([str(vue)] if vue else []),
                            cwd=ROOT, text=True, capture_output=True, timeout=20)
    testcase.assertEqual(result.returncode, 0, result.stdout + result.stderr)


def run_pinned_vue(testcase, vue):
    """Called by the explicit browser gate after checksum-pinned assets exist."""
    testcase.assertTrue(Path(vue).is_file(), 'Missing checksum-pinned Vue asset')
    for name, body in SCENARIOS.items():
        with testcase.subTest(share_lifecycle=name):
            run_node(testcase, body, vue)


@unittest.skipUnless(shutil.which('node'), 'Node required for share controller contracts')
class ShareDialogControllerTests(unittest.TestCase):
    def test_reverse_completion_and_duplicate_selection(self):
        run_node(self, SCENARIOS['reverse_completion_and_duplicate_selection'])

    def test_old_error_timer_and_finally_do_not_own_new_state(self):
        run_node(self, SCENARIOS['old_error_timer_and_finally_do_not_own_new_state'])

    def test_close_before_response_render_and_reopen(self):
        run_node(self, SCENARIOS['close_before_response_render_and_reopen'])

    def test_each_navigation_refresh_and_session_boundary(self):
        run_node(self, SCENARIOS['each_navigation_refresh_and_session_boundary'])

    def test_clipboard_failure_and_late_feedback(self):
        run_node(self, SCENARIOS['clipboard_failure_and_late_feedback'])

    def test_invalid_response_missing_dom_and_partial_qr_failure(self):
        run_node(self, SCENARIOS['invalid_response_missing_dom_and_partial_qr_failure'])

    def test_close_while_vue_mount_is_pending_invalidates_render(self):
        run_node(self, r'''
const h=boot(),update=h.holdUpdate(),a=h.start();a.request.resolve({data:'mount-secret'});await flush();
assert.equal(h.api.qrLink.value,'mount-secret');h.api.closeQrDialog();h.clear();update.resolve();await a.done;h.clear();
assert(!h.api.showQrDialog.value);assert.equal(h.allTimers.length,0);
''')
