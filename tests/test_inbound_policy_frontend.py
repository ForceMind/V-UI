"""Run the shipped Vue setup code, without claiming browser/runtime coverage."""
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const clone=v=>JSON.parse(JSON.stringify(v));
const node=(id,mode='inherit')=>({id,core:'sing-box',protocol:'vless',remark:'node-'+id,port:10400+id,
 enable:true,routing_mode:mode,certificate_id:null,credentials:{has_uuid:true},
 profile:{security:'tls',transport:'direct',flow:'',server_name:'entry.example.test',skip_cert_verify:false}});
function harness(){
 let setup,interceptor,writeFailure=null,writeHold=null,readFailure=null;
 const editors=new Map(),held=new Map(),writes=[],messages=[],events={},redirects=[];
 let rows=[],authStatus=200;
 const app={component(){},use(){},mount(){}};
 const sandbox={console,addEventListener(name,fn){(events[name]??=[]).push(fn)},location:{hash:'',replace(v){redirects.push(v)}},
 Vue:{createApp(o){setup=o.setup;return app},ref:value=>({value}),reactive:v=>v,computed:f=>({get value(){return f()}}),watch(){},onMounted(){}},
 ElementPlus:{ElMessage:{success:m=>messages.push(['success',m]),error:m=>messages.push(['error',m]),warning:m=>messages.push(['warning',m])},ElMessageBox:{}},
 ElementPlusIconsVue:{},axios:{defaults:{headers:{common:{}}},interceptors:{response:{use(ok,bad){interceptor=bad}}},
 async get(url){
  if(url==='/api/auth/me')return{status:authStatus,data:{username:'fixture'}};
  if(url==='/api/inbounds'){if(readFailure)throw readFailure;return{data:clone(rows)}};
  const match=url.match(/^\/api\/inbounds\/(\d+)\/editor$/);
  if(match){const id=Number(match[1]);if(held.has(id))return await held.get(id);return{data:clone(editors.get(id))}};
  return{data:[]};
 },async post(url,body){writes.push({method:'POST',url,body:clone(body)});if(writeHold)await writeHold;if(writeFailure)throw writeFailure;return{data:{}}},
 async put(url,body){writes.push({method:'PUT',url,body:clone(body)});if(writeHold)await writeHold;if(writeFailure)throw writeFailure;return{data:{}}}}};
 vm.runInNewContext(fs.readFileSync('web/js/app.js','utf8'),sandbox);
 return{state:setup(),editors,held,writes,messages,events,redirects,
 rows(value){rows=value},failWrite(value){writeFailure=value},holdWrite(value){writeHold=value},failRead(value){readFailure=value},
 async expire(){authStatus=401;try{await interceptor({response:{status:401}})}catch(_){}},
 pagehide(){events.pagehide.forEach(fn=>fn())}};
}
'''


@unittest.skipUnless(shutil.which('node'), 'Node required for frontend contract check')
class InboundPolicyFrontendTests(unittest.TestCase):
    def run_js(self, body):
        result = subprocess.run(['node', '-e', HARNESS + '\n(async()=>{\n' + body +
                                 '\n})().catch(error=>{console.error(error);process.exitCode=1});'],
                                cwd=ROOT, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_create_edit_modes_cancel_reopen_and_missing_update_not_reset(self):
        self.run_js(r'''
const h=harness(),s=h.state;
await s.openAddInbound();assert.equal(s.newInbound.routing_mode,'inherit');
s.newInbound.routing_mode='relay';s.closeInboundEditor();assert.equal(h.writes.length,0);
await s.openAddInbound();assert.equal(s.newInbound.routing_mode,'inherit');await s.saveInbound();
assert.equal(h.writes[0].body.routing_mode,'inherit');
for(const mode of ['inherit','direct','relay']){
 h.editors.set(7,node(7,mode));await s.openEditInbound({id:7});assert.equal(s.newInbound.routing_mode,mode);
 await s.saveInbound();assert.equal(h.writes.at(-1).body.routing_mode,mode);
 assert.equal(h.writes.at(-1).method,'PUT');assert.equal(h.writes.at(-1).url,'/api/inbounds/7');
 assert.equal('routing_mode' in h.writes.at(-1).body.profile,false);
 await s.openEditInbound({id:7});assert.equal(s.newInbound.routing_mode,mode);s.closeInboundEditor();
}
const old=node(8);delete old.routing_mode;h.editors.set(8,old);
await s.openEditInbound({id:8});assert.equal(s.newInbound.routing_mode,null);await s.saveInbound();
assert.equal('routing_mode' in h.writes.at(-1).body,false);
await s.openEditInbound({id:8,routing_mode:'relay'});assert.equal(s.newInbound.routing_mode,null);
await s.saveInbound();assert.equal('routing_mode' in h.writes.at(-1).body,false);
await s.openEditInbound({id:8,routing_mode:'relay'});s.newInbound.routing_mode='direct';
await s.saveInbound();assert.equal(h.writes.at(-1).body.routing_mode,'direct');
h.editors.set(8,{...old,routing_mode:null});await s.openEditInbound({id:8,routing_mode:'relay'});
assert.equal(s.newInbound.routing_mode,null);await s.saveInbound();assert.equal('routing_mode' in h.writes.at(-1).body,false);
''')

    def test_saved_and_applied_are_independent_and_unsupported_modes_not_hidden(self):
        self.run_js(r'''
const h=harness(),s=h.state;
for(const mode of ['inherit','direct','relay']){
 const row=node(7,mode);assert.equal(s.inboundSavedRouting(row),{inherit:'跟随全局',direct:'指定直连',relay:'指定中转'}[mode]);
 assert.match(s.inboundAppliedRouting(row),/未确认/);
 row.routing_status={state:'applied',applied_effective_mode:'relay',policy_identity:'a'.repeat(64)};
 assert.match(s.inboundAppliedRouting(row),/已应用中转/);
 row.routing_status.state='pending';assert.match(s.inboundAppliedRouting(row),/待应用/);assert.doesNotMatch(s.inboundAppliedRouting(row),/已应用中转/);
 row.routing_status.state='stopped';assert.match(s.inboundAppliedRouting(row),/停止/);
 row.routing_status.state='unknown';assert.match(s.inboundAppliedRouting(row),/未确认/);
 row.routing_status.state='applied';row.routing_status.policy_identity=null;assert.match(s.inboundAppliedRouting(row),/未确认/);
}
for(const invalid of [null,'DIRECT','',{},42])assert.equal(s.inboundSavedRouting({routing_mode:invalid}),'策略未确认');
await s.openAddInbound();s.newInbound.routing_mode='relay';assert.match(s.inboundRoutingHint.value,/服务器严格核验/);
s.newInbound.core='sing-box';s.onCoreChanged();s.newInbound.profile.security='tls';
assert.match(s.inboundRoutingHint.value,/先在代理与中转页选择并应用上游/);
assert.match(s.inboundRoutingHint.value,/UDP/);s.newInbound.routing_mode='direct';assert.match(s.inboundRoutingHint.value,/既有/);
''')

    def test_delayed_editor_cancellation_switching_list_reads_and_401(self):
        self.run_js(r'''
const h=harness(),s=h.state;let resolveFirst,resolveSecond;
h.held.set(1,new Promise(r=>resolveFirst=r));h.held.set(2,new Promise(r=>resolveSecond=r));
const first=s.openEditInbound({id:1}),second=s.openEditInbound({id:2});
resolveSecond({data:node(2,'relay')});await second;assert.equal(s.editingInboundId.value,2);
resolveFirst({data:node(1,'direct')});await first;assert.equal(s.editingInboundId.value,2);assert.equal(s.newInbound.routing_mode,'relay');
let resolveLate;h.held.set(3,new Promise(r=>resolveLate=r));const late=s.openEditInbound({id:3});
s.closeInboundEditor();resolveLate({data:node(3,'direct')});await late;assert.equal(s.showAddInbound.value,false);
h.rows([{...node(4,'relay'),routing_status:{state:'applied',applied_effective_mode:'relay',policy_identity:'b'.repeat(64)}}]);
await s.fetchInbounds();assert.match(s.inboundAppliedRouting(s.inbounds.value[0]),/已应用中转/);
h.failRead(Error('offline'));await s.fetchInbounds();assert.match(s.inboundAppliedRouting(s.inbounds.value[0]),/未确认/);
h.editors.set(5,node(5,'relay'));await s.openEditInbound({id:5});s.newInbound.profile.reality_uuid='synthetic-secret';
await h.expire();assert.equal(s.showAddInbound.value,false);assert.equal(s.newInbound.profile.reality_uuid,'');
assert.equal(s.inbounds.value.length,0);assert.deepEqual(h.redirects,['/login']);
await s.openEditInbound({id:5});assert.equal(s.showAddInbound.value,false);
''')

    def test_duplicate_save_saved_only_failure_and_page_exit_do_not_claim_applied(self):
        self.run_js(r'''
const h=harness(),s=h.state;await s.openAddInbound();s.newInbound.routing_mode='relay';
let release;h.holdWrite(new Promise(r=>release=r));const first=s.saveInbound(),second=s.saveInbound();
assert.equal(h.writes.length,1);assert.equal(s.savingInbound.value,true);
h.failWrite({response:{status:409,data:{detail:{saved:true,applied:false,message:'runtime rejected'}}}});
release();await Promise.all([first,second]);assert.equal(s.showAddInbound.value,false);assert.equal(s.savingInbound.value,false);
assert.equal(h.messages.filter(v=>v[0]==='success').length,0);assert.match(h.messages.at(-1)[1],/已保存为期望状态/);
await s.saveInbound();assert.equal(h.writes.length,1);
h.holdWrite(null);h.failWrite({response:{status:422,data:{detail:'invalid routing'}}});
await s.openAddInbound();s.newInbound.routing_mode='relay';await s.saveInbound();
assert.equal(s.showAddInbound.value,true);assert.equal(s.newInbound.routing_mode,'relay');assert.equal(s.savingInbound.value,false);
s.closeInboundEditor();await s.openAddInbound();assert.equal(s.newInbound.routing_mode,'inherit');
h.failWrite(null);let exitRelease;h.holdWrite(new Promise(r=>exitRelease=r));const exiting=s.saveInbound();
h.pagehide();exitRelease();await exiting;assert.equal(s.showAddInbound.value,false);
assert.equal(h.messages.filter(v=>v[0]==='success').length,0);
''')


class InboundPolicyMarkupTests(unittest.TestCase):
    def test_ordinary_share_and_subscription_show_scoped_capability_note(self):
        class Notes(HTMLParser):
            def __init__(self):
                super().__init__()
                self.current = None
                self.notes = {}

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                identifier = attrs.get('id')
                if tag == 'p' and identifier in ('share-routing-capability', 'subscription-routing-capability'):
                    self.current = identifier
                    self.notes[identifier] = {'attrs': attrs, 'text': ''}

            def handle_data(self, data):
                if self.current:
                    self.notes[self.current]['text'] += data

            def handle_endtag(self, tag):
                if tag == 'p':
                    self.current = None

        html = (ROOT / 'web/index.html').read_text()
        parser = Notes()
        parser.feed(html)
        expected = ('普通配置描述入口协议，不证明已应用出口；已应用中转入口本轮仅 TCP，'
                    '服务器拒绝 UDP，具体以节点的已应用状态为准。普通直连既有能力不变。')
        self.assertEqual(set(parser.notes), {'share-routing-capability', 'subscription-routing-capability'})
        for note in parser.notes.values():
            self.assertEqual(note['text'], expected)
            self.assertEqual(note['attrs']['role'], 'note')
            self.assertNotIn('v-if', note['attrs'])
            self.assertNotIn('v-show', note['attrs'])
        subscriptions = html[html.index("<div v-if=\"currentView === 'subscriptions'\">"):
                             html.index("<div v-if=\"currentView === 'site'\">")]
        share = html[html.index('<el-dialog v-model="showQrDialog"'):
                     html.index('</el-dialog>', html.index('<el-dialog v-model="showQrDialog"'))]
        self.assertIn('id="subscription-routing-capability"', subscriptions)
        self.assertIn('id="share-routing-capability"', share)
        self.assertIn('{{ qrLink }}', share)


if __name__ == '__main__':
    unittest.main()
