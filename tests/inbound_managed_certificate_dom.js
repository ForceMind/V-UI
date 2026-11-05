// Real app.js in a minimal Vue/HTTP harness; no browser or network is started.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/js/app.js'), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
const certificate = (id = 'cert-a', changes = {}) => ({id, domain:id + '.example.test',
    environment:'production', revision:'synthetic-revision', days_remaining:30,
    not_after:Date.now() / 1000 + 86400 * 30, ...changes});
const deferred = () => {let resolve, reject; const promise = new Promise((yes, no) => {
    resolve = yes; reject = no;
}); return {promise, resolve, reject};};
function fixture(certs = [certificate()], stored = null) {
    let setup, read = async () => ({data:clone(certs)}), write = async () => ({data:{}});
    const messages = [], writes = [], app = {component(){}, use(){}, mount(){}};
    const context = vm.createContext({Date, document:{getElementById(){return null;}},
        addEventListener(){}, location:{hash:'', replace(){}},
        Vue:{createApp(options){setup = options.setup; return app;}, ref:value=>({value}),
            reactive:value=>value, computed:fn=>({get value(){return fn();}}), watch(){}, onMounted(){}},
        ElementPlus:{ElMessage:{error:message=>messages.push(message), success:message=>messages.push(message),
            warning:message=>messages.push(message)}, ElMessageBox:{}}, ElementPlusIconsVue:{},
        axios:{defaults:{headers:{common:{}}}, interceptors:{response:{use(){}}},
            async get(url){if(url === '/api/certificates') return read();
                return {data:url.endsWith('/editor') ? clone(stored) : []};},
            async post(url, body){writes.push({url, body:clone(body)}); return write();},
            async put(url, body){writes.push({url, body:clone(body)}); return write();}}});
    vm.runInContext(source, context);
    const state = setup();
    return {state, messages, writes, certificates:fn=>{read = fn;}, write:fn=>{write = fn;},
        endSession:()=>vm.runInContext('endAdminSession()', context)};
}
const tls = f => {f.state.newInbound.profile.security = 'tls'; f.state.onSecurityChanged();};
const persisted = (changes = {}) => ({id:7, mutation_revision:'a'.repeat(64), core:'xray',
    protocol:'vless', port:10443, enable:true, certificate_id:null,
    profile:{security:'tls', transport:'raw', server_name:'manual.example.test',
        certificate_path:'/original/fullchain.pem', key_path:'/original/key.pem'}, ...changes});

test('unique usable certificate defaults for Xray TLS and sends binding without manual paths', async () => {
    const f = fixture(); await f.state.openAdvancedInbound();
    assert.equal(f.state.newInbound.certificate_id, null, 'plain TCP does not bind TLS material');
    tls(f);
    assert.equal(f.state.newInbound.certificate_id, 'cert-a');
    assert.equal(f.state.newInbound.profile.server_name, 'cert-a.example.test');
    assert.equal(f.state.newInbound.profile.certificate_path, '');
    await f.state.saveInbound();
    assert.equal(f.writes[0].body.certificate_id, 'cert-a');
    assert.equal(f.writes[0].body.core, 'xray');
});

test('multiple available certificates always require a choice even with a matching SNI', async () => {
    const f = fixture([certificate(), certificate('cert-b')]); await f.state.openAdvancedInbound();
    f.state.newInbound.profile.server_name = 'CERT-A.EXAMPLE.TEST.'; tls(f);
    assert.equal(f.state.newInbound.certificate_id, null);
    assert.match(f.state.managedCertificateHint.value, /多张.*选择/);
    await f.state.saveInbound(); assert.equal(f.writes.length, 0);
    assert.match(f.messages.at(-1), /请选择/);
    f.state.newInbound.certificate_id = 'cert-b'; f.state.onManagedCertificateChanged();
    assert.equal(f.state.newInbound.profile.server_name, 'CERT-A.EXAMPLE.TEST.');
    assert.match(f.state.managedCertificateHint.value, /不匹配/);
    f.state.newInbound.profile.server_name = 'cert-b.example.test';
    await f.state.retryManagedCertificates(); assert.equal(f.state.newInbound.certificate_id, 'cert-b');
    await f.state.saveInbound(); assert.equal(f.writes[0].body.certificate_id, 'cert-b');
});

test('expired, staging, missing revision, null lifetime and expired instant never become defaults', async () => {
    const unavailable = [certificate('expired', {days_remaining:-1}), certificate('staging', {environment:'staging'}),
        certificate('unissued', {revision:null}), certificate('unknown', {days_remaining:null}),
        certificate('expired-now', {days_remaining:0, not_after:Date.now() / 1000 - 1})];
    const f = fixture(unavailable); await f.state.openAdvancedInbound(); tls(f);
    assert.equal(f.state.managedCertificates.value.length, 0);
    assert.equal(f.state.newInbound.certificate_id, null); assert.match(f.state.managedCertificateHint.value, /暂无可用/);
    const g = fixture([...unavailable, certificate()]); await g.state.openAdvancedInbound(); tls(g);
    assert.equal(g.state.newInbound.certificate_id, 'cert-a');
});

test('SNI conflict prevents auto choice and only automatic choices clear after SNI changes', async () => {
    const f = fixture(); await f.state.openAdvancedInbound();
    f.state.newInbound.profile.server_name = 'other.example.test'; tls(f);
    assert.equal(f.state.newInbound.certificate_id, null); assert.match(f.state.managedCertificateHint.value, /不匹配/);
    f.state.newInbound.profile.server_name = 'CERT-A.EXAMPLE.TEST.'; f.state.reconcileManagedCertificate();
    assert.equal(f.state.newInbound.certificate_id, 'cert-a');
    f.state.newInbound.profile.server_name = 'other.example.test'; f.state.reconcileManagedCertificate();
    assert.equal(f.state.newInbound.certificate_id, null);
    f.state.newInbound.certificate_id = 'cert-a'; f.state.onManagedCertificateChanged();
    f.state.reconcileManagedCertificate(); assert.equal(f.state.newInbound.certificate_id, 'cert-a');
});

test('load failure stays explicit, preserves choice, blocks save, and retry recovers', async () => {
    const f = fixture(); await f.state.openAdvancedInbound(); tls(f);
    f.certificates(async () => {throw Error('secret server pathname /private/key.pem');});
    await f.state.retryManagedCertificates();
    assert.equal(f.state.newInbound.certificate_id, 'cert-a');
    assert.equal(f.state.managedCertificateState.value, 'error');
    assert.match(f.state.managedCertificateHint.value, /读取失败.*重试/);
    await f.state.saveInbound(); assert.equal(f.writes.length, 0); assert.match(f.messages.at(-1), /读取.*重试/);
    f.certificates(async () => ({data:[certificate()]})); await f.state.retryManagedCertificates();
    assert.equal(f.state.managedCertificateState.value, 'ready');
    await f.state.saveInbound(); assert.equal(f.writes.length, 1);
    const g = fixture(); g.certificates(async () => ({data:{detail:'bad'}})); await g.state.openAdvancedInbound();
    assert.equal(g.state.managedCertificateState.value, 'error');
    assert.equal(g.state.showAddInbound.value, true);
});

test('editor preserves original manual values and existing bindings without auto replacement', async () => {
    const stored = persisted(), f = fixture([certificate()], stored);
    await f.state.openEditInbound(stored);
    assert.equal(f.state.certificateMode.value, 'manual');
    assert.equal(f.state.newInbound.certificate_id, null);
    assert.equal(f.state.newInbound.profile.certificate_path, '/original/fullchain.pem');
    await f.state.retryManagedCertificates(); await f.state.saveInbound();
    assert.equal(f.writes[0].body.profile.server_name, 'manual.example.test');
    assert.equal(f.writes[0].body.profile.key_path, '/original/key.pem');
    const bound = persisted({certificate_id:'original-id'}), g = fixture([certificate()], bound);
    await g.state.openEditInbound(bound); await g.state.retryManagedCertificates();
    assert.equal(g.state.newInbound.certificate_id, 'original-id');
    assert.equal(g.state.newInbound.profile.server_name, 'manual.example.test');
    assert.match(g.state.managedCertificateHint.value, /原绑定已保留/);
    await g.state.saveInbound(); assert.equal(g.writes.length, 0);
    const unknown = persisted({certificate_binding_unavailable:true}), h = fixture([], unknown);
    await h.state.openEditInbound(unknown); assert.equal(h.state.showAddInbound.value, false);
    assert.match(h.messages.at(-1), /原证书绑定/);
});

test('protocol/core changes retain common TLS draft but reset credentials and incompatible transport', async () => {
    const f = fixture(); await f.state.openAdvancedInbound(); tls(f);
    f.state.newInbound.profile.path = '/old-transport';
    f.state.newInbound.profile.hysteria2_password = 'must-not-carry';
    f.state.newInbound.core = 'sing-box'; f.state.onCoreChanged();
    assert.equal(f.state.newInbound.profile.transport, 'direct');
    assert.equal(f.state.newInbound.profile.hysteria2_password, '');
    f.state.newInbound.protocol = 'trojan'; f.state.onProtocolChanged();
    assert.equal(f.state.newInbound.certificate_id, 'cert-a');
    assert.equal(f.state.newInbound.profile.server_name, 'cert-a.example.test');
    f.state.newInbound.protocol = 'vless'; f.state.onProtocolChanged();
    f.state.newInbound.profile.security = 'reality'; f.state.onSecurityChanged();
    assert.equal(f.state.newInbound.certificate_id, null);
    assert.equal(f.state.newInbound.profile.certificate_path, '');
    await f.state.saveInbound(); assert.equal(f.writes[0].body.certificate_id, null);
    const manual = fixture(); await manual.state.openAdvancedInbound(); tls(manual);
    manual.state.certificateMode.value = 'manual'; manual.state.onCertificateModeChanged();
    manual.state.newInbound.profile.certificate_path = '/custom/cert';
    manual.state.newInbound.profile.key_path = '/custom/key';
    manual.state.newInbound.profile.security = 'none'; manual.state.onSecurityChanged();
    tls(manual); assert.equal(manual.state.newInbound.profile.certificate_path, '/custom/cert');
    assert.equal(manual.state.newInbound.profile.key_path, '/custom/key');
});

test('Xray eligibility excludes other protocols, transports, flow and REALITY', async () => {
    const f = fixture(); await f.state.openAdvancedInbound(); tls(f);
    for (const transport of ['ws','grpc','xhttp']) {
        f.state.newInbound.profile.transport = transport;
        assert.equal(f.state.managedCertificateEligible.value, false);
        await f.state.saveInbound(); assert.equal(f.writes.length, 0);
        assert.match(f.messages.at(-1), /不支持托管证书/);
    }
    f.state.newInbound.profile.transport = 'raw'; f.state.newInbound.profile.flow = 'xtls-rprx-vision';
    assert.equal(f.state.managedCertificateEligible.value, false);
    f.state.newInbound.profile.flow = '';
    for (const protocol of ['trojan','vmess','shadowsocks']) {
        f.state.newInbound.protocol = protocol; assert.equal(f.state.managedCertificateEligible.value, false);
    }
    f.state.newInbound.core = 'sing-box';
    for (const protocol of ['vless','vmess','trojan','hysteria2','tuic']) {
        f.state.newInbound.protocol = protocol; assert.equal(f.state.managedCertificateEligible.value, true);
    }
    f.state.newInbound.profile.security = 'reality'; assert.equal(f.state.managedCertificateEligible.value, false);
});

test('out-of-order editor reads, retry reads, close and session end cannot replace current list', async () => {
    const f = fixture(), first = deferred(), second = deferred();
    let count = 0; f.certificates(() => (++count === 1 ? first : second).promise);
    const old = f.state.openAdvancedInbound(), current = f.state.openAdvancedInbound();
    second.resolve({data:[certificate('new')]}); await current;
    first.resolve({data:[certificate('old')]}); await old;
    assert.equal(f.state.managedCertificates.value[0].id, 'new');
    const slow = deferred(); f.certificates(() => slow.promise);
    const retry = f.state.retryManagedCertificates(); f.state.closeInboundEditor();
    slow.resolve({data:[certificate('closed')]}); await retry;
    assert.equal(f.state.showAddInbound.value, false);
    assert.equal(f.state.managedCertificates.value[0].id, 'new');
    const g = fixture(), pending = deferred(); g.certificates(() => pending.promise);
    const open = g.state.openAdvancedInbound(); g.endSession(); pending.resolve({data:[certificate()]}); await open;
    assert.equal(g.state.showAddInbound.value, false); assert.equal(g.state.managedCertificates.value.length, 0);
});

test('saving suppresses duplicate reads and UI mutations, unknown errors stay Chinese without secrets', async () => {
    const f = fixture(); await f.state.openAdvancedInbound(); tls(f);
    const pending = deferred(); f.write(() => pending.promise);
    const save = f.state.saveInbound(); assert.equal(f.state.savingInbound.value, true);
    let reads = 0; f.certificates(async () => {reads++; return {data:[]};});
    await f.state.retryManagedCertificates(); f.state.onCoreChanged();
    assert.equal(reads, 0); assert.equal(f.state.newInbound.certificate_id, 'cert-a');
    pending.reject({response:{status:500, data:{detail:'internal secret /srv/private/key.pem password=abc'}}});
    await save;
    assert.equal(f.messages.at(-1), '添加节点失败，请稍后重试。');
    assert.equal(f.state.savingInbound.value, false); assert.equal(f.state.showAddInbound.value, true);
});
