'use strict';
// Shipped Vue controller with deterministic API/model and confirmation doubles.
// No HTTP, browser rendering, SQLite, core process, sockets or real credentials.
// Backend transaction/identity tests independently exercise the real implementation.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const filename = process.argv[2] || path.join(__dirname, '../web/js/app.js');
const script = fs.readFileSync(filename, 'utf8');
const A = 'a'.repeat(64), B = 'b'.repeat(64), C = 'c'.repeat(64);
const CONFLICT = 'Inbound instance changed; refresh nodes and reopen or confirm again';
const clone = value => structuredClone(value);
const plain = value => JSON.parse(JSON.stringify(value));
const error = (status, detail) => ({response:{status, data:{detail}}});
const deferred = () => {
    let resolve, reject;
    const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
    return {promise, resolve, reject};
};
const node = (id = 1, revision = A, changes = {}) => ({id, mutation_revision:revision,
    core:'sing-box', protocol:'vless', remark:'Original node', port:10443,
    enable:true, routing_mode:'inherit', certificate_id:null, credentials:{has_uuid:true},
    profile:{security:'tls', transport:'direct', flow:'', server_name:'example.test', skip_cert_verify:false}, ...changes});
function fixture({legacy = false} = {}) {
    let setup, interceptor, authStatus = 200;
    const rows = new Map([[1, node()]]), reads = [], writes = [], messages = [], confirmations = [], events = {}, redirects = [];
    const gates = new Map(), failures = new Map(), editorValues = new Map(), confirmationsPending = [];
    const app = {component(){}, use(){}, mount(){}};
    const watchers = new WeakMap();
    const ref = initial => {
        let value = initial;
        const reference = {get value(){return value;}, set value(next){
            const previous = value; value = next;
            if (next !== previous) for (const callback of watchers.get(reference) || []) callback(next, previous);
        }};
        return reference;
    };
    const perform = async (method, url, body) => {
        const request = {method, url, ...(body === undefined ? {} : {body:plain(body)})};
        (method === 'GET' ? reads : writes).push(request);
        const key = method + ' ' + url;
        if (gates.has(key)) {
            const gate = gates.get(key); gates.delete(key);
            const response = await gate.promise;
            if (response !== undefined) return response;
        }
        if (failures.has(key)) throw failures.get(key);
        if (url === '/api/auth/me') return {status:authStatus, data:{username:'synthetic-admin'}};
        if (url === '/api/certificates') return {data:[]};
        if (url === '/api/cores/status') return {data:{}};
        if (method === 'GET' && url === '/api/inbounds') return {data:clone([...rows.values()])};
        const match = url.match(/^\/api\/inbounds\/(\d+)(\/editor|\/delete)?$/);
        if (method === 'POST' && url === '/api/inbounds') {
            const created = node(2, C, plain(body)); rows.set(2, created); return {data:clone(created)};
        }
        assert.ok(match, 'Unexpected request: ' + key);
        const id = Number(match[1]), current = rows.get(id);
        if (method === 'GET' && match[2] === '/editor') return {data:clone(editorValues.has(id) ? editorValues.get(id) : current)};
        if (legacy && method === 'POST' && match[2] === '/delete') throw error(404, 'Not Found');
        if (legacy && method === 'PUT' && Object.hasOwn(body, 'expected_instance_revision')) throw error(422, 'Extra inputs are not permitted');
        if (Object.hasOwn(body || {}, 'expected_instance_revision') &&
            (!current || body.expected_instance_revision !== current.mutation_revision)) throw error(409, CONFLICT);
        // Old methods intentionally remain available here, so baseline regressions
        // mutate the replacement rather than failing because of a too-strict fake.
        if (method === 'DELETE' || (method === 'POST' && match[2] === '/delete')) {
            rows.delete(id); return {data:{}};
        }
        assert.equal(method, 'PUT'); assert.ok(current);
        const {expected_instance_revision, ...values} = plain(body);
        Object.assign(current, values); return {data:clone(current)};
    };
    const sandbox = {console, document:{getElementById(){return null;}},
        addEventListener:(name, callback) => {(events[name] ||= []).push(callback);},
        location:{hash:'', replace:url => redirects.push(url)},
        Vue:{createApp(options){setup = options.setup; return app;}, ref, reactive:v => v,
            computed:callback => ({get value(){return callback();}}),
            watch(target, callback){watchers.set(target, [...(watchers.get(target) || []), callback]);}, onMounted(){}},
        ElementPlus:{ElMessage:Object.fromEntries(['success', 'error', 'warning'].map(kind => [kind, text => messages.push({kind, text})])),
            ElMessageBox:{confirm(...args){confirmations.push(args); const gate = deferred(); confirmationsPending.push(gate); return gate.promise;}}},
        ElementPlusIconsVue:{}, axios:{defaults:{headers:{common:{}}}, interceptors:{response:{use(ok, bad){interceptor = bad;}}},
            get:url => perform('GET', url), put:(url, body) => perform('PUT', url, body),
            post:(url, body) => perform('POST', url, body), delete:url => perform('DELETE', url)}};
    vm.runInNewContext(script, sandbox, {filename});
    const state = setup();
    return {state, rows, reads, writes, messages, confirmations, redirects, failures, editorValues,
        hold(method, url){const gate = deferred(); gates.set(method + ' ' + url, gate); return gate;},
        confirm(){assert.ok(confirmationsPending.length); confirmationsPending.shift().resolve();},
        cancel(reason = 'cancel'){assert.ok(confirmationsPending.length); confirmationsPending.shift().reject(reason);},
        event(name){for (const callback of events[name] || []) callback({});},
        async expire(){authStatus = 401; try {await interceptor(error(401, 'expired'));} catch (_) {}},
        async load(){await state.fetchInbounds(); return state.inbounds.value[0];},
        async edit(){const row = await this.load(); await state.openEditInbound(row); return row;},
        replace(changes = {}){const replacement = node(1, B, {remark:'Replacement node', port:10444, ...changes}); rows.set(1, replacement); return replacement;}};
}
const assertNoSuccess = f => assert.equal(f.messages.filter(message => message.kind === 'success').length, 0);
const assertConflict = f => assert.ok(f.messages.some(message => message.kind === 'error' && message.text.includes(CONFLICT)), plain(f.messages));

test('edit/get replacement race refuses to open the replacement from the old row', async () => {
    const f = fixture(), row = await f.load(), held = f.hold('GET', '/api/inbounds/1/editor');
    const opening = f.state.openEditInbound(row); f.replace(); held.resolve(); await opening;
    assert.equal(f.state.showAddInbound.value, false); assert.equal(f.state.editingInboundId.value, null);
    assert.equal(f.writes.length, 0); assertConflict(f);
});
test('rejected editor snapshot leaves an existing draft untouched', async () => {
    const f = fixture(), row = await f.edit(); f.state.newInbound.remark = 'Unsaved original intent';
    f.replace(); await f.state.openEditInbound(row);
    assert.equal(f.state.newInbound.remark, 'Unsaved original intent'); assert.equal(f.state.showAddInbound.value, true);
    await f.state.saveInbound(); assert.equal(f.rows.get(1).remark, 'Replacement node'); assertConflict(f);
});
test('row changes during editor fetch cannot substitute its id or revision', async () => {
    const f = fixture(), row = await f.load(), held = f.hold('GET', '/api/inbounds/1/editor');
    const opening = f.state.openEditInbound(row); Object.assign(row, node(2, C));
    held.resolve({data:node()}); await opening; f.state.newInbound.remark = 'Edited original'; await f.state.saveInbound();
    assert.equal(f.writes[0].url, '/api/inbounds/1'); assert.equal(f.writes[0].body.expected_instance_revision, A);
});
for (const value of [undefined, null, '', 'A'.repeat(64), 'a'.repeat(63), A + '\n', 7, [A]]) {
    test(`missing/invalid displayed revision ${JSON.stringify(value)} blocks edit and delete`, async () => {
        const f = fixture(), row = node(1, value); row.mutation_revision = value;
        await f.state.openEditInbound(row); const deleting = f.state.deleteInbound(row);
        if (f.confirmations.length) f.cancel(); await deleting;
        assert.equal(f.reads.length, 0); assert.equal(f.writes.length, 0); assert.equal(f.confirmations.length, 0);
        assert.equal(f.state.showAddInbound.value, false);
        assert.equal(f.messages.length, 2); assert.ok(f.messages.every(message => /refresh|reload/i.test(message.text)));
    });
}
for (const value of [undefined, null, '', 'A'.repeat(64), 'a'.repeat(63), A + '\n', [A]]) {
    test(`missing/invalid editor revision ${JSON.stringify(value)} cannot create a usable editor`, async () => {
        const f = fixture(), row = await f.load(); f.editorValues.set(1, {...node(), mutation_revision:value});
        await f.state.openEditInbound(row); await f.state.saveInbound();
        assert.equal(f.state.showAddInbound.value, false); assert.equal(f.writes.length, 0);
        assert.ok(f.messages.some(message => /refresh|reload/i.test(message.text)));
    });
}
for (const value of [null, undefined, []]) {
    test(`malformed editor response ${JSON.stringify(value)} fails closed with refresh guidance`, async () => {
        const f = fixture(), row = await f.load(); f.editorValues.set(1, value);
        await f.state.openEditInbound(row); await f.state.saveInbound();
        assert.equal(f.state.showAddInbound.value, false); assert.equal(f.writes.length, 0);
        assert.ok(f.messages.some(message => /refresh|reload/i.test(message.text)));
    });
}
test('refresh to the same integer id cannot rebase an open edit; conflict preserves all draft intent', async () => {
    const f = fixture(); await f.edit(); const replacement = clone(f.replace());
    f.state.newInbound.remark = 'Draft original'; f.state.newInbound.port = 15443;
    f.state.newInbound.profile.server_name = 'draft.example.test'; f.state.newInbound.routing_mode = 'direct';
    const draft = plain(f.state.newInbound); await f.state.fetchInbounds();
    assert.equal(f.state.inbounds.value[0].mutation_revision, B);
    await f.state.saveInbound(); assert.deepEqual(f.rows.get(1), replacement);
    assert.deepEqual(plain(f.state.newInbound), draft); assert.equal(f.state.showAddInbound.value, true);
    assert.equal(f.state.savingInbound.value, false); assert.equal(f.writes.length, 1);
    assert.equal(f.writes[0].body.expected_instance_revision, A); assertConflict(f); assertNoSuccess(f);
    // An explicit repeat is still bound to the original, never silently retried or rebased.
    await f.state.saveInbound(); assert.equal(f.writes.length, 2);
    assert.equal(f.writes[1].body.expected_instance_revision, A); assert.equal(f.rows.get(1).remark, 'Replacement node');
    f.state.closeInboundEditor(); await f.state.openEditInbound(f.state.inbounds.value[0]);
    f.state.newInbound.remark = 'Explicit replacement edit'; await f.state.saveInbound();
    assert.equal(f.writes[2].body.expected_instance_revision, B); assert.equal(f.rows.get(1).remark, 'Explicit replacement edit');
});
test('same-instance UUID/password edits and unrelated inventory changes do not conflict', async () => {
    const f = fixture(); await f.edit();
    Object.assign(f.rows.get(1), {synthetic_uuid:'new-synthetic-uuid', synthetic_password:'new-synthetic-password'});
    f.rows.set(9, node(9, C)); await f.state.fetchInbounds(); f.state.newInbound.remark = 'Same instance edited';
    await f.state.saveInbound(); assert.equal(f.writes[0].body.expected_instance_revision, A);
    assert.equal(f.rows.get(1).synthetic_uuid, 'new-synthetic-uuid'); assert.equal(f.rows.get(1).synthetic_password, 'new-synthetic-password');
    assert.equal(f.rows.get(1).remark, 'Same instance edited'); assert.equal(f.state.showAddInbound.value, false);
    assert.equal(f.messages.filter(message => message.kind === 'success').length, 1);
});
test('delete confirmation captures row identity before await and survives replacement/list refresh safely', async () => {
    const f = fixture(), row = await f.load(), deleting = f.state.deleteInbound(row);
    assert.match(f.confirmations[0][0], /Original node:10443/);
    const replacement = clone(f.replace()); await f.state.fetchInbounds();
    f.confirm(); await deleting;
    assert.deepEqual(f.writes, [{method:'POST', url:'/api/inbounds/1/delete', body:{expected_instance_revision:A}}]);
    assert.deepEqual(f.rows.get(1), replacement); assertConflict(f); assertNoSuccess(f);
    // A refreshed row must be selected and confirmed anew before replacement deletion.
    const next = f.state.deleteInbound(f.state.inbounds.value[0]); assert.equal(f.confirmations.length, 2);
    assert.match(f.confirmations[1][0], /Replacement node:10444/); f.confirm(); await next;
    assert.equal(f.rows.has(1), false); assert.equal(f.writes[1].body.expected_instance_revision, B);
});
for (const status of [401, 403, 404, 405, 422]) {
    test(`${status} edit/delete responses never strip conditions or fall back to DELETE`, async () => {
        const f = fixture(); await f.edit(); f.state.newInbound.remark = 'Keep this draft';
        f.failures.set('PUT /api/inbounds/1', error(status, 'Synthetic rejection'));
        f.failures.set('POST /api/inbounds/1/delete', error(status, 'Synthetic rejection'));
        f.failures.set('DELETE /api/inbounds/1', error(status, 'Synthetic rejection'));
        await f.state.saveInbound(); const deleting = f.state.deleteInbound(f.state.inbounds.value[0]); f.confirm(); await deleting;
        assert.deepEqual(f.writes.map(request => [request.method, request.url]), [['PUT','/api/inbounds/1'], ['POST','/api/inbounds/1/delete']]);
        assert.ok(f.writes.every(request => request.body.expected_instance_revision === A));
        assert.equal(f.state.newInbound.remark, 'Keep this draft'); assert.equal(f.state.showAddInbound.value, true);
        assert.equal(f.rows.get(1).remark, 'Original node'); assertNoSuccess(f);
    });
}
test('new controller against a downlevel API fails closed without changing the model', async () => {
    const f = fixture({legacy:true}); await f.edit(); f.state.newInbound.remark = 'Do not apply';
    await f.state.saveInbound(); const deleting = f.state.deleteInbound(f.state.inbounds.value[0]); f.confirm(); await deleting;
    assert.equal(f.rows.get(1).remark, 'Original node'); assert.equal(f.writes.length, 2);
    assert.deepEqual(f.writes.map(request => request.method), ['PUT','POST']); assertNoSuccess(f);
});
test('delayed save is single-flight and its captured condition is immutable', async () => {
    const f = fixture(); await f.edit(); f.state.newInbound.remark = 'First intent';
    const held = f.hold('PUT', '/api/inbounds/1'), saving = f.state.saveInbound();
    f.replace(); await f.state.fetchInbounds(); await f.state.saveInbound(); await f.state.openEditInbound(f.state.inbounds.value[0]);
    assert.equal(f.writes.length, 1); assert.equal(f.state.savingInbound.value, true);
    assert.equal(f.writes[0].body.expected_instance_revision, A); held.resolve(); await saving;
    assert.equal(f.state.newInbound.remark, 'First intent'); assert.equal(f.state.savingInbound.value, false); assertConflict(f);
});
for (const boundary of ['close', 'pagehide', 'popstate', 'view', 'session']) {
    test(`delayed editor after ${boundary} cannot resurrect old content or a usable condition`, async () => {
        const f = fixture(), row = await f.load(), held = f.hold('GET', '/api/inbounds/1/editor');
        const opening = f.state.openEditInbound(row);
        if (boundary === 'close') f.state.closeInboundEditor();
        else if (boundary === 'view') f.state.currentView.value = 'inbounds';
        else if (boundary === 'session') await f.expire();
        else f.event(boundary);
        held.resolve({data:node()}); await opening; await f.state.saveInbound();
        assert.equal(f.state.showAddInbound.value, false); assert.equal(f.state.editingInboundId.value, null);
        assert.equal(f.writes.length, 0); assert.equal(f.messages.length, 0);
    });
}
for (const completion of ['success', 'conflict']) {
    test(`save ${completion} after close cannot claim success or overwrite a newer editor`, async () => {
        const f = fixture(); await f.edit(); const held = f.hold('PUT', '/api/inbounds/1');
        const saving = f.state.saveInbound(); f.state.closeInboundEditor();
        if (completion === 'success') held.resolve({data:node()}); else held.reject(error(409, CONFLICT));
        await saving; assert.equal(f.state.showAddInbound.value, false); assert.equal(f.messages.length, 0);
        f.replace(); await f.edit(); f.state.newInbound.remark = 'New explicit draft'; await f.state.saveInbound();
        assert.equal(f.writes[1].body.expected_instance_revision, B); assert.equal(f.rows.get(1).remark, 'New explicit draft');
    });
}
test('older editor replies cannot overwrite a newer selected instance and condition', async () => {
    const f = fixture(), old = await f.load(), held = f.hold('GET', '/api/inbounds/1/editor');
    const opening = f.state.openEditInbound(old); f.rows.set(2, node(2, C));
    await f.state.openEditInbound(node(2, C)); held.resolve({data:node()}); await opening;
    await f.state.saveInbound(); assert.equal(f.writes[0].url, '/api/inbounds/2'); assert.equal(f.writes[0].body.expected_instance_revision, C);
});
test('delete cancelled or session-ended while confirming never writes', async () => {
    const f = fixture(), row = await f.load(); const cancelled = f.state.deleteInbound(row); f.cancel(); await cancelled;
    const expired = f.state.deleteInbound(row); await f.expire(); f.confirm(); await expired;
    assert.equal(f.writes.length, 0); assert.equal(f.messages.length, 0); assert.deepEqual(f.redirects, ['/login']);
});
test('saved-but-not-applied edit/delete responses retain the established separate commit semantics', async () => {
    const f = fixture(); await f.edit();
    f.failures.set('PUT /api/inbounds/1', error(409, {saved:true, applied:false, message:'synthetic runtime failure'}));
    await f.state.saveInbound(); assert.equal(f.state.showAddInbound.value, false); assertNoSuccess(f);
    assert.ok(f.messages.some(message => message.kind === 'warning' && /已保存为期望状态/.test(message.text)));
    f.failures.set('POST /api/inbounds/1/delete', error(409, {saved:true, applied:false, message:'synthetic runtime failure'}));
    f.failures.set('DELETE /api/inbounds/1', error(409, {saved:true, applied:false, message:'synthetic runtime failure'}));
    const deleting = f.state.deleteInbound(f.state.inbounds.value[0]); f.confirm(); await deleting;
    assert.ok(f.messages.some(message => message.kind === 'warning' && /节点删除已保存/.test(message.text)));
    assert.equal(f.reads.filter(request => request.url === '/api/inbounds').length, 3);
    assert.ok(f.writes.every(request => request.body.expected_instance_revision === A));
});

test('mutating a row object during confirmation cannot retarget the guarded delete', async () => {
    const f = fixture(), row = await f.load(), deleting = f.state.deleteInbound(row);
    f.rows.set(2, node(2, C)); Object.assign(row, node(2, C)); f.confirm(); await deleting;
    assert.deepEqual(f.writes, [{method:'POST', url:'/api/inbounds/1/delete', body:{expected_instance_revision:A}}]);
    assert.equal(f.rows.has(1), false); assert.equal(f.rows.has(2), true);
});
test('an editor response for a different integer id cannot open despite a malformed matching revision', async () => {
    const f = fixture(), row = await f.load(); f.editorValues.set(1, node(2, A));
    await f.state.openEditInbound(row); await f.state.saveInbound();
    assert.equal(f.state.showAddInbound.value, false); assert.equal(f.writes.length, 0); assertConflict(f);
});
test('save refuses an editing id without a verified editor snapshot', async () => {
    const f = fixture(); await f.state.openAddInbound(); f.state.editingInboundId.value = 1;
    f.state.newInbound.remark = 'Keep unverified draft'; await f.state.saveInbound();
    assert.equal(f.writes.length, 0); assert.equal(f.state.savingInbound.value, false);
    assert.equal(f.state.showAddInbound.value, true); assert.equal(f.state.newInbound.remark, 'Keep unverified draft');
    assert.ok(f.messages.some(message => /refresh|reload/i.test(message.text)));
});
test('same-instance changes while editor fetch is pending use its returned snapshot, not the old list', async () => {
    const f = fixture(), row = await f.load(), held = f.hold('GET', '/api/inbounds/1/editor');
    const opening = f.state.openEditInbound(row); f.rows.get(1).remark = 'Newer same-instance config';
    held.resolve(); await opening; assert.equal(f.state.newInbound.remark, 'Newer same-instance config');
    await f.state.saveInbound(); assert.equal(f.writes[0].body.expected_instance_revision, A);
    assert.equal(f.state.showAddInbound.value, false);
});
for (const completion of ['success', 'conflict']) {
    test(`delete ${completion} after session expiry stays quiet and cannot repaint old rows`, async () => {
        const f = fixture(), row = await f.load(), held = f.hold('POST', '/api/inbounds/1/delete');
        const legacyHeld = f.hold('DELETE', '/api/inbounds/1');
        const deleting = f.state.deleteInbound(row); f.confirm();
        // Resolving confirmation queues the POST before the explicit session end.
        await new Promise(resolve => setImmediate(resolve)); assert.equal(f.writes.length, 1); await f.expire();
        const active = f.writes[0].method === 'POST' ? held : legacyHeld;
        if (completion === 'success') active.resolve({data:{}}); else active.reject(error(409, CONFLICT));
        await deleting; assert.equal(f.messages.length, 0); assert.equal(f.state.inbounds.value.length, 0);
        assert.deepEqual(f.redirects, ['/login']); assert.equal(f.writes[0].body.expected_instance_revision, A);
    });
}

test('new-node creation remains unconditioned after an edit is closed', async () => {
    const f = fixture(); await f.edit(); f.state.closeInboundEditor(); await f.state.openAddInbound(); await f.state.saveInbound();
    assert.equal(f.writes.length, 1); assert.equal(f.writes[0].url, '/api/inbounds'); assert.equal(f.writes[0].method, 'POST');
    assert.equal('expected_instance_revision' in f.writes[0].body, false);
});
