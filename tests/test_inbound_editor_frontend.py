"""Execute the real Vue setup/payload code with a minimal Node harness."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node required for frontend contract check')
class InboundEditorFrontendTests(unittest.TestCase):
    def test_reality_dedicated_payload_hidden_credentials_and_explicit_security_transition(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
let setup; const writes=[]; const clone=v=>JSON.parse(JSON.stringify(v));
const stored={id:47,core:'sing-box',protocol:'vless',remark:'reality',port:10447,enable:true,certificate_id:null,
 credentials:{has_uuid:true},profile:{security:'reality',transport:'direct',flow:'xtls-rprx-vision',
 reality_target:'127.0.0.1:19445',reality_server_name:'reference.example.test',client_fingerprint:'chrome',
 skip_cert_verify:false,reality_uuid:'',reality_short_id:'',reality_uuid_set:true,reality_short_id_set:true,reality_private_key_set:true}};
const app={component(){},use(){},mount(){}};
const sandbox={addEventListener(){},location:{hash:''},
 Vue:{createApp(o){setup=o.setup;return app},ref:v=>({value:v}),reactive:v=>v,computed:f=>({get value(){return f()}}),onMounted(){}},
 ElementPlus:{ElMessage:{success(){},error(m){throw Error(m)}},ElMessageBox:{}},ElementPlusIconsVue:{},
 axios:{defaults:{headers:{common:{}}},interceptors:{response:{use(){}}},
 async get(url){return {data:url==='/api/inbounds/47/editor'?clone(stored):[]}},
 async post(url,body){writes.push({url,body:clone(body)});return {data:{}}},
 async put(url,body){writes.push({url,body:clone(body)});return {data:{}}}}};
vm.runInNewContext(fs.readFileSync('web/js/app.js','utf8'),sandbox);
(async()=>{
 const state=setup();await state.openAddInbound();state.newInbound.core='sing-box';state.onCoreChanged();
 const p=state.newInbound.profile;state.newInbound.certificate_id='old-managed';
 p.security='reality';state.onSecurityChanged();
 assert.equal(p.flow,'xtls-rprx-vision');assert.equal(p.transport,'direct');assert.equal(p.client_fingerprint,'chrome');
 assert.equal(state.newInbound.certificate_id,null);assert.equal(p.reality_uuid,'');assert.equal(p.reality_short_id,'');
 p.reality_target='127.0.0.1:19445';p.reality_server_name='reference.example.test';await state.saveInbound();
 assert.deepEqual(Object.keys(writes[0].body.profile).sort(),['security','transport','flow','reality_target','reality_server_name','reality_uuid','reality_short_id','client_fingerprint','skip_cert_verify'].sort());
 assert.equal(writes[0].body.profile.reality_uuid,'');assert.equal(writes[0].body.profile.reality_short_id,'');
 await state.openEditInbound({id:47});assert.equal(p.reality_uuid,'');assert.equal(p.reality_short_id,'');
 assert.equal(p.reality_private_key_set,true);assert.equal(p.reality_short_id_set,true);
 p.reality_short_id='cancelled';state.showAddInbound.value=false;await state.openEditInbound({id:47});
 assert.equal(p.reality_short_id,'');assert.equal(writes.length,1);
 await state.saveInbound();assert.equal(writes[1].body.profile.reality_uuid,'');assert.equal(writes[1].body.profile.reality_short_id,'');
 await state.openEditInbound({id:47});p.reality_uuid='22222222-2222-4222-8222-222222222222';await state.saveInbound();
 assert.equal(writes[2].body.profile.reality_short_id,'');
 await state.openEditInbound({id:47});p.reality_short_id='fedcba9876543210';await state.saveInbound();
 assert.equal(writes[3].body.profile.reality_uuid,'');
 await state.openEditInbound({id:47});p.security='tls';state.onSecurityChanged();assert.equal(p.flow,'');
 await state.saveInbound();assert.equal('reality_uuid' in writes[4].body.profile,false);
 for(const w of writes){assert.equal('reality_private_key' in w.body.profile,false);assert.equal('settings' in w.body,false)}
 const html=fs.readFileSync('web/index.html','utf8');
 assert.match(html,/v-model="newInbound.profile.reality_uuid" type="password"/);
 assert.ok(html.includes('留空保持当前 Short ID'));assert.ok(html.includes('不绑定托管证书'));
 assert.ok(html.includes('握手参考端'));assert.ok(html.includes('必填，例如 reference.example.test'));
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', script], cwd=root, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_vless_websocket_create_edit_prefill_preserves_public_profile(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const vm = require('node:vm'), fs = require('node:fs'), assert = require('node:assert/strict');
let setup;
const submitted = [], certificate = 'a'.repeat(32);
const stored = {
  id: 7, core: 'sing-box', protocol: 'vless', remark: 'ws-node', port: 10443, enable: true,
  certificate_id: certificate, credentials: {has_uuid: true, has_password: false},
  profile: {security: 'tls', transport: 'ws', path: '/vless-ws', host: 'cdn.example.test',
    server_name: 'vpn.example.test', certificate_path: '/managed/fullchain.pem',
    key_path: '/managed/privkey.pem', flow: '', skip_cert_verify: false}
};
const clone = value => JSON.parse(JSON.stringify(value));
const app = {component() {}, use() {}, mount() {}};
const sandbox = {
  addEventListener() {}, location: {hash: ''},
  Vue: {createApp(options) {setup = options.setup; return app;}, ref: value => ({value}),
    reactive: value => value, computed: fn => ({get value() {return fn();}}), onMounted() {}},
  ElementPlus: {ElMessage: {success() {}, error(message) {throw Error(message);}}, ElMessageBox: {}},
  ElementPlusIconsVue: {},
  axios: {defaults: {headers: {common: {}}}, interceptors: {response: {use() {}}},
    async get(url) {
      if (url === '/api/inbounds/7/editor') return {data: clone(stored)};
      if (url === '/api/certificates') return {data: [{id: certificate, domain: 'vpn.example.test',
        environment: 'production', revision: 'test-revision', days_remaining: 30}]};
      return {data: []};
    },
    async put(url, body) {submitted.push({method: 'PUT', url, body: clone(body)}); return {data: {}};},
    async post(url, body) {submitted.push({method: 'POST', url, body: clone(body)}); return {data: {}};}}
};
vm.runInNewContext(fs.readFileSync('web/js/app.js', 'utf8'), sandbox);
(async () => {
  const state = setup();
  await state.openAddInbound();
  state.newInbound.core = 'sing-box'; state.onCoreChanged();
  Object.assign(state.newInbound.profile, stored.profile);
  state.newInbound.certificate_id = certificate;
  await state.saveInbound();
  assert.equal(submitted[0].method, 'POST');
  assert.equal(submitted[0].url, '/api/inbounds');
  assert.equal(submitted[0].body.core, 'sing-box');
  assert.equal(submitted[0].body.protocol, 'vless');
  assert.equal(submitted[0].body.profile.transport, 'ws');
  assert.equal(submitted[0].body.profile.path, '/vless-ws');
  assert.equal(submitted[0].body.profile.host, 'cdn.example.test');
  assert.equal(submitted[0].body.certificate_id, certificate);
  assert.equal(state.showAddInbound.value, false);

  await state.openEditInbound({id: 7});
  assert.equal(state.newInbound.profile.transport, 'ws');
  assert.equal(state.newInbound.profile.path, '/vless-ws');
  assert.equal(state.newInbound.profile.host, 'cdn.example.test');
  assert.equal(state.newInbound.profile.server_name, 'vpn.example.test');
  assert.equal(state.newInbound.certificate_id, certificate);
  assert.equal(state.inboundCredentials.value.has_uuid, true);
  assert.deepEqual(Object.keys(state.newInbound.settings), []);
  assert.deepEqual(Object.keys(state.newInbound.stream_settings), []);

  // Reopening a dismissed draft must reload persisted values, without a write.
  state.newInbound.profile.path = '/discarded';
  state.newInbound.profile.host = 'discarded.example.test';
  state.showAddInbound.value = false;
  await state.openEditInbound({id: 7});
  assert.equal(submitted.length, 1);
  assert.equal(state.newInbound.profile.path, '/vless-ws');
  assert.equal(state.newInbound.profile.host, 'cdn.example.test');
  state.newInbound.profile.path = '/vless-ws-edited';
  state.newInbound.profile.host = 'cdn2.example.test';
  await state.saveInbound();
  const update = submitted[1];
  assert.equal(update.method, 'PUT');
  assert.equal(update.url, '/api/inbounds/7');
  assert.equal(update.body.profile.path, '/vless-ws-edited');
  assert.equal(update.body.profile.host, 'cdn2.example.test');
  assert.equal(update.body.profile.transport, 'ws');
  assert.equal(update.body.profile.server_name, 'vpn.example.test');
  assert.equal(update.body.profile.skip_cert_verify, false);
  assert.equal(update.body.profile.flow, '');
  assert.equal(update.body.certificate_id, certificate);
  for (const {body} of submitted) {
    for (const field of ['settings', 'stream_settings', 'uuid', 'password']) assert.equal(field in body, false);
    for (const field of ['uuid', 'password', 'max_early_data', 'early_data_header_name'])
      assert.equal(field in body.profile, false);
  }
  await state.openAddInbound();
  assert.equal(state.newInbound.profile.path, '/');
  assert.equal(state.newInbound.profile.host, '');
  assert.equal(state.newInbound.certificate_id, null);
  assert.equal(Object.keys(state.inboundCredentials.value).length, 0);

  const html = fs.readFileSync('web/index.html', 'utf8');
  assert.match(html, /label="WebSocket" value="ws"/);
  for (const field of ['transport', 'path', 'host']) assert.ok(html.includes(`v-model="newInbound.profile.${field}"`));
  const warning = html.match(/<el-alert\s+v-if="([^"]+)"[^>]*description="([^"]*Host 仅为客户端路由参数[^"]*)"/);
  assert.ok(warning, 'WS Host scope and validation boundary must be visible in the form');
  assert.ok(warning[2].includes('sing-box 不校验请求 Host'));
  assert.ok(warning[2].includes('SNI'));
  assert.ok(warning[2].includes('256'));
  assert.ok(warning[2].includes('early data'));
  const warningVisible = new Function('newInbound', `return (${warning[1]});`);
  assert.equal(warningVisible(stored), true);
  for (const transport of ['direct', 'grpc', 'httpupgrade'])
    assert.equal(warningVisible({...stored, profile: {...stored.profile, transport}}), false);
  for (const protocol of ['vmess', 'trojan'])
    assert.equal(warningVisible({...stored, protocol}), false);
  assert.equal(warningVisible({...stored, core: 'xray'}), false);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
        result = subprocess.run(['node', '-e', script], cwd=root, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_vless_grpc_create_edit_prefill_preserves_literal_profile(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const vm = require('node:vm'), fs = require('node:fs'), assert = require('node:assert/strict');
let setup;
const submitted = [], certificate = 'a'.repeat(32);
const stored = {
  id: 7, core: 'sing-box', protocol: 'vless', remark: 'grpc-node', port: 10443, enable: true,
  certificate_id: certificate, credentials: {has_uuid: true, has_password: false},
  profile: {security: 'tls', transport: 'grpc', service_name: 'VUI.Service_1',
    server_name: 'vpn.example.test', certificate_path: '/managed/fullchain.pem',
    key_path: '/managed/privkey.pem', flow: '', skip_cert_verify: false}
};
const clone = value => JSON.parse(JSON.stringify(value));
const app = {component() {}, use() {}, mount() {}};
const sandbox = {
  addEventListener() {}, location: {hash: ''},
  Vue: {createApp(options) {setup = options.setup; return app;}, ref: value => ({value}),
    reactive: value => value, computed: fn => ({get value() {return fn();}}), onMounted() {}},
  ElementPlus: {ElMessage: {success() {}, error(message) {throw Error(message);}}, ElMessageBox: {}},
  ElementPlusIconsVue: {},
  axios: {defaults: {headers: {common: {}}}, interceptors: {response: {use() {}}},
    async get(url) {
      if (url === '/api/inbounds/7/editor') return {data: clone(stored)};
      if (url === '/api/certificates') return {data: [{id: certificate, domain: 'vpn.example.test',
        environment: 'production', revision: 'test-revision', days_remaining: 30}]};
      return {data: []};
    },
    async put(url, body) {submitted.push({method: 'PUT', url, body: clone(body)}); return {data: {}};},
    async post(url, body) {submitted.push({method: 'POST', url, body: clone(body)}); return {data: {}};}}
};
vm.runInNewContext(fs.readFileSync('web/js/app.js', 'utf8'), sandbox);
(async () => {
  const state = setup();
  await state.openAddInbound();
  state.newInbound.core = 'sing-box'; state.onCoreChanged();
  Object.assign(state.newInbound.profile, stored.profile);
  state.newInbound.certificate_id = certificate;
  await state.saveInbound();
  assert.equal(submitted[0].method, 'POST');
  assert.equal(submitted[0].url, '/api/inbounds');
  assert.equal(submitted[0].body.core, 'sing-box');
  assert.equal(submitted[0].body.protocol, 'vless');
  assert.equal(submitted[0].body.profile.transport, 'grpc');
  assert.equal(submitted[0].body.profile.service_name, 'VUI.Service_1');
  assert.equal(submitted[0].body.certificate_id, certificate);
  assert.equal(state.showAddInbound.value, false);

  await state.openEditInbound({id: 7});
  assert.equal(state.newInbound.profile.transport, 'grpc');
  assert.equal(state.newInbound.profile.service_name, 'VUI.Service_1');
  assert.equal(state.newInbound.profile.server_name, 'vpn.example.test');
  assert.equal(state.newInbound.certificate_id, certificate);
  assert.equal(state.inboundCredentials.value.has_uuid, true);
  assert.deepEqual(Object.keys(state.newInbound.settings), []);
  assert.deepEqual(Object.keys(state.newInbound.stream_settings), []);

  // Reopening a dismissed draft must reload persisted values, without a write.
  state.newInbound.profile.service_name = 'Discarded.Service';
  state.showAddInbound.value = false;
  await state.openEditInbound({id: 7});
  assert.equal(submitted.length, 1);
  assert.equal(state.newInbound.profile.service_name, 'VUI.Service_1');
  state.newInbound.profile.service_name = 'VUI.Edited-2';
  await state.saveInbound();
  const update = submitted[1];
  assert.equal(update.method, 'PUT');
  assert.equal(update.url, '/api/inbounds/7');
  assert.equal(update.body.profile.service_name, 'VUI.Edited-2');
  assert.equal(update.body.profile.transport, 'grpc');
  assert.equal(update.body.profile.server_name, 'vpn.example.test');
  assert.equal(update.body.profile.skip_cert_verify, false);
  assert.equal(update.body.profile.flow, '');
  assert.equal(update.body.certificate_id, certificate);
  for (const {body} of submitted) {
    for (const field of ['settings', 'stream_settings', 'uuid', 'password']) assert.equal(field in body, false);
    for (const field of ['uuid', 'password', 'authority', 'headers', 'multi_mode', 'user_agent', 'idle_timeout', 'ping_timeout', 'permit_without_stream'])
      assert.equal(field in body.profile, false);
  }
  // Untouched malformed imported JSON must remain malformed in the actual
  // payload; a control's display value is not permission to sanitize storage.
  for (const value of [null, false, 0, [], {}, ' ', ' leading', 'trailing ']) {
    stored.profile.service_name = value;
    stored.profile.flow = value;
    await state.openEditInbound({id: 7});
    await state.saveInbound();
    const body = submitted[submitted.length - 1].body;
    assert.deepEqual(body.profile.service_name, value);
    assert.deepEqual(body.profile.flow, value);
  }
  await state.openAddInbound();
  assert.equal(state.newInbound.profile.service_name, '');
  assert.equal(state.newInbound.profile.path, '/');
  assert.equal(state.newInbound.profile.host, '');
  assert.equal(state.newInbound.certificate_id, null);
  assert.equal(Object.keys(state.inboundCredentials.value).length, 0);

  const html = fs.readFileSync('web/index.html', 'utf8');
  assert.match(html, /label="gRPC" value="grpc"/);
  assert.ok(html.includes('v-model="newInbound.profile.service_name"'));
  const warning = html.match(/<el-alert\s+v-if="([^"]+)"[^>]*description="([^"]*gRPC Lite[^"]*)"/);
  assert.ok(warning, 'gRPC Lite bounds must be visible in the form');
  for (const text of ['HTTP/2', 'h2', '1–128', 'authority', 'Host', 'UDP', 'SNI', '空 flow'])
    assert.ok(warning[2].includes(text), text);
  const warningVisible = new Function('newInbound', `return (${warning[1]});`);
  assert.equal(warningVisible(stored), true);
  for (const transport of ['direct', 'ws', 'httpupgrade'])
    assert.equal(warningVisible({...stored, profile: {...stored.profile, transport}}), false);
  for (const protocol of ['vmess', 'trojan'])
    assert.equal(warningVisible({...stored, protocol}), false);
  assert.equal(warningVisible({...stored, core: 'xray'}), false);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
        result = subprocess.run(['node', '-e', script], cwd=root, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_non_tls_payload_cannot_resubmit_hidden_managed_certificate(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const vm = require('node:vm'), fs = require('node:fs'), assert = require('node:assert/strict');
let setup, submitted;
const app = {component() {}, use() {}, mount() {}};
const sandbox = {
  addEventListener() {}, location: {hash: ''},
  Vue: {createApp(options) {setup = options.setup; return app;}, ref: value => ({value}),
    reactive: value => value, computed: fn => ({get value() {return fn();}}), onMounted() {}},
  ElementPlus: {ElMessage: {success() {}, error(message) {throw Error(message);}}, ElMessageBox: {}},
  ElementPlusIconsVue: {},
  axios: {defaults: {headers: {common: {}}}, interceptors: {response: {use() {}}},
    async get() {return {data: []};},
    async put(url, body) {submitted = body; return {data: {}};},
    async post(url, body) {submitted = body; return {data: {}};}}
};
vm.runInNewContext(fs.readFileSync('web/js/app.js', 'utf8'), sandbox);
(async () => {
  for (const editing of [null, 7]) for (const security of ['none', 'reality']) {
    const state = setup();
    state.editingInboundId.value = editing;
    state.newInbound.certificate_id = 'a'.repeat(32);
    state.newInbound.profile.security = security;
    await state.saveInbound();
    assert.equal(submitted.profile.security, security);
    assert.equal(submitted.certificate_id, null, `hidden certificate submitted for ${security}`);
  }
  const state = setup();
  state.editingInboundId.value = 7;
  state.newInbound.certificate_id = 'a'.repeat(32);
  state.newInbound.profile.security = 'none';
  state.newInbound.profile.certificate_path = '/managed/cert.pem';
  state.newInbound.profile.key_path = '/managed/key.pem';
  state.onSecurityChanged();
  assert.equal(state.newInbound.certificate_id, null);
  assert.equal(state.newInbound.profile.certificate_path, '');
  assert.equal(state.newInbound.profile.key_path, '');
  const html = fs.readFileSync('web/index.html', 'utf8');
  assert.match(html, /v-model="newInbound.profile.security"\s+@change="onSecurityChanged"/);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
        result = subprocess.run(['node', '-e', script], cwd=root, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_hysteria2_defaults_prefill_cancel_and_blank_password_payload(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const vm = require('node:vm'), fs = require('node:fs'), assert = require('node:assert/strict');
let setup;
const writes=[], clone=value=>JSON.parse(JSON.stringify(value));
const stored={id:45,core:'sing-box',protocol:'hysteria2',port:10445,remark:'hy2',enable:true,
 certificate_id:'a'.repeat(32),credentials:{has_password:true},profile:{security:'tls',transport:'quic',
 server_name:'vpn.example.test',certificate_path:'/cert',key_path:'/key',client_fingerprint:'',
 skip_cert_verify:false,up_mbps:null,down_mbps:null,obfs_type:'',obfs_password:'',
 hysteria2_password:'',hysteria2_password_set:true}};
const app={component(){},use(){},mount(){}};
const sandbox={addEventListener(){},location:{hash:''},
 Vue:{createApp(o){setup=o.setup;return app;},ref:v=>({value:v}),reactive:v=>v,
 computed:fn=>({get value(){return fn();}}),onMounted(){}},
 ElementPlus:{ElMessage:{success(){},error(m){throw Error(m);}},ElMessageBox:{}},ElementPlusIconsVue:{},
 axios:{defaults:{headers:{common:{}}},interceptors:{response:{use(){}}},
 async get(url){return {data:url.endsWith('/editor')?clone(stored):[]};},
 async post(url,body){writes.push({url,body:clone(body)});return {data:{}};},
 async put(url,body){writes.push({url,body:clone(body)});return {data:{}};}}};
vm.runInNewContext(fs.readFileSync('web/js/app.js','utf8'),sandbox);
(async()=>{
 const state=setup();await state.openAddInbound();
 state.newInbound.core='sing-box';state.onCoreChanged();state.newInbound.protocol='hysteria2';state.onProtocolChanged();
 const p=state.newInbound.profile;
 assert.equal(p.security,'tls');assert.equal(p.transport,'quic');assert.equal(p.client_fingerprint,'');
 assert.equal(p.up_mbps,null);assert.equal(p.down_mbps,null);assert.equal(p.obfs_type,'');
 assert.equal(p.hysteria2_password,'');assert.equal(p.hysteria2_password_set,false);
 p.server_name='vpn.example.test';state.newInbound.certificate_id=stored.certificate_id;
 await state.saveInbound();assert.equal(writes[0].body.profile.hysteria2_password,'');
 await state.openEditInbound({id:45});assert.equal(p.hysteria2_password,'');assert.equal(p.hysteria2_password_set,true);
 p.hysteria2_password='discarded';p.up_mbps=100;state.showAddInbound.value=false;
 await state.openEditInbound({id:45});assert.equal(writes.length,1);assert.equal(p.hysteria2_password,'');
 assert.equal(p.up_mbps,null);assert.equal(p.client_fingerprint,'');
 await state.saveInbound();assert.equal(writes[1].url,'/api/inbounds/45');
 assert.equal(writes[1].body.profile.hysteria2_password,'');assert.equal(writes[1].body.certificate_id,stored.certificate_id);
 assert.equal('settings' in writes[1].body,false);assert.equal('stream_settings' in writes[1].body,false);
 await state.openEditInbound({id:45});p.hysteria2_password='explicit-replacement';await state.saveInbound();
 assert.equal(writes[2].body.profile.hysteria2_password,'explicit-replacement');
 const html=fs.readFileSync('web/index.html','utf8');
 assert.ok(html.includes('v-model="newInbound.profile.hysteria2_password"'));
 assert.ok(html.includes('留空保持当前 Hysteria2 密码'));assert.ok(html.includes('TCP 放行不能代替 UDP'));
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result = subprocess.run(['node', '-e', script], cwd=root, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_tuic_defaults_prefill_cancel_and_blank_password_payload(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const vm = require('node:vm'), fs = require('node:fs'), assert = require('node:assert/strict');
let setup;
const writes=[], clone=value=>JSON.parse(JSON.stringify(value));
const stored={id:45,core:'sing-box',protocol:'tuic',port:10445,remark:'tuic',enable:true,
 certificate_id:'a'.repeat(32),credentials:{has_password:true},profile:{security:'tls',transport:'quic',
 server_name:'vpn.example.test',certificate_path:'/cert',key_path:'/key',client_fingerprint:'',
 skip_cert_verify:false,up_mbps:null,down_mbps:null,obfs_type:'',obfs_password:'',
 tuic_password:'',tuic_password_set:true,tuic_uuid:'',tuic_uuid_set:true,congestion_control:'cubic',udp_relay_mode:'native',zero_rtt_handshake:false}};
const app={component(){},use(){},mount(){}};
const sandbox={addEventListener(){},location:{hash:''},
 Vue:{createApp(o){setup=o.setup;return app;},ref:v=>({value:v}),reactive:v=>v,
 computed:fn=>({get value(){return fn();}}),onMounted(){}},
 ElementPlus:{ElMessage:{success(){},error(m){throw Error(m);}},ElMessageBox:{}},ElementPlusIconsVue:{},
 axios:{defaults:{headers:{common:{}}},interceptors:{response:{use(){}}},
 async get(url){return {data:url.endsWith('/editor')?clone(stored):[]};},
 async post(url,body){writes.push({url,body:clone(body)});return {data:{}};},
 async put(url,body){writes.push({url,body:clone(body)});return {data:{}};}}};
vm.runInNewContext(fs.readFileSync('web/js/app.js','utf8'),sandbox);
(async()=>{
 const state=setup();await state.openAddInbound();
 state.newInbound.core='sing-box';state.onCoreChanged();state.newInbound.protocol='tuic';state.onProtocolChanged();
 const p=state.newInbound.profile;
 assert.equal(p.security,'tls');assert.equal(p.transport,'quic');assert.equal(p.client_fingerprint,'');
 assert.equal(p.up_mbps,null);assert.equal(p.down_mbps,null);assert.equal(p.obfs_type,'');
 assert.equal(p.tuic_password,'');assert.equal(p.tuic_password_set,false);
 assert.equal(p.tuic_uuid,'');assert.equal(p.tuic_uuid_set,false);
 assert.equal(p.congestion_control,'cubic');assert.equal(p.udp_relay_mode,'native');assert.equal(p.zero_rtt_handshake,false);
 p.server_name='vpn.example.test';state.newInbound.certificate_id=stored.certificate_id;
 await state.saveInbound();assert.equal(writes[0].body.profile.tuic_password,'');
 await state.openEditInbound({id:45});assert.equal(p.tuic_password,'');assert.equal(p.tuic_password_set,true);
 p.tuic_uuid='discarded-uuid';p.tuic_password='discarded';p.up_mbps=100;state.showAddInbound.value=false;
 await state.openEditInbound({id:45});assert.equal(writes.length,1);assert.equal(p.tuic_password,'');
 assert.equal(p.up_mbps,null);assert.equal(p.client_fingerprint,'');assert.equal(p.tuic_uuid,'');assert.equal(p.tuic_uuid_set,true);
 await state.saveInbound();assert.equal(writes[1].url,'/api/inbounds/45');
 assert.equal(writes[1].body.profile.tuic_password,'');assert.equal(writes[1].body.certificate_id,stored.certificate_id);
 assert.equal('settings' in writes[1].body,false);assert.equal('stream_settings' in writes[1].body,false);
 await state.openEditInbound({id:45});p.tuic_password='explicit-replacement';await state.saveInbound();
 assert.equal(writes[2].body.profile.tuic_password,'explicit-replacement');
 const html=fs.readFileSync('web/index.html','utf8');
 assert.ok(html.includes('v-model="newInbound.profile.tuic_password"'));
 assert.ok(html.includes('留空保持当前 TUIC 密码'));assert.ok(html.includes('Mihomo 的 TUIC UDP 能力不能靠 udp:false 关闭'));
 assert.ok(html.includes('v-model="newInbound.profile.tuic_uuid"'));
 await state.openEditInbound({id:45});p.tuic_uuid='explicit-uuid';await state.saveInbound();
 assert.equal(writes[3].body.profile.tuic_uuid,'explicit-uuid');assert.equal(writes[3].body.profile.tuic_password,'');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result = subprocess.run(['node', '-e', script], cwd=root, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_managed_certificate_selector_matches_validated_tls_protocols(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require('node:fs'), assert = require('node:assert/strict');
const html = fs.readFileSync('web/index.html', 'utf8');
const selector = html.match(/<template v-if="([^"]+)">\s*<el-form-item label="托管证书" v-if="([^"]+)"/);
assert.ok(selector, 'managed selector must remain inside the TLS template');
const visible = new Function('newInbound', `return (${selector[1]}) && (${selector[2]});`);
for (const protocol of ['vless', 'trojan', 'vmess', 'hysteria2', 'tuic']) {
  assert.equal(visible({core: 'sing-box', protocol, profile: {security: 'tls'}}), true, protocol);
  for (const security of ['none', 'reality'])
    assert.equal(visible({core: 'sing-box', protocol, profile: {security}}), false, `${protocol}/${security}`);
  assert.equal(visible({core: 'xray', protocol, profile: {security: 'tls'}}), false, `xray/${protocol}`);
}
for (const protocol of ['shadowsocks'])
  assert.equal(visible({core: 'sing-box', protocol, profile: {security: 'tls'}}), false, protocol);
'''
        result = subprocess.run(['node', '-e', script], cwd=root, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_certificate_page_binding_filter_includes_only_validated_tls_nodes(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('web/js/certificates.js', 'utf8');
const match = source.match(/nodes\.filter\((.*?)\)\.forEach/);
assert.ok(match, 'actual certificate-page binding filter must exist');
const accepted = vm.runInNewContext('(' + match[1] + ')');
assert.equal(accepted({managed_certificate_eligible: true}), true);
for (const managed_certificate_eligible of [false, undefined, null, 1, 'true'])
  assert.equal(accepted({managed_certificate_eligible}), false);
// Ordinary summaries deliberately contain no raw TLS documents.
assert.equal(accepted({core:'sing-box',protocol:'vless',stream_settings:{tls:{enabled:true}}}), false);
'''
        result = subprocess.run(['node', '-e', script], cwd=root, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
