'use strict';
// Saved-preview revision controls: real routes/storage with simulated DOM/fetch.
// Metadata faults are explicit response mutations, not HTTP/browser qualification.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const {fixture, ROUTING, SNAPSHOT, PREVIEW, original, replacement, plan, response, R1, R2} = require('./workspace_dom_fixture');
const {backend, bounded, quoted} = require('./fixtures/workspace_routing_fixture');

const preview = f => ['rule-preview', 'rule-count', 'warnings'].map(id=>f.get(id).textContent);
const clean = (f, value) => {
    assert.equal(f.get('proxy').value, value);
    assert.equal(f.get('save').disabled, true);
    assert.match(f.get('save-state').textContent, /已与保存版本一致/);
};
const navigation = (f, expected) => {
    const event = {prevented:false, preventDefault(){this.prevented = true;}};
    for (const fn of f.globalEvents.get('beforeunload') || []) fn(event);
    assert.equal(event.prevented, expected, 'beforeunload follows the retained saved baseline');
};
const mismatch = (f, saved = false) => {
    assert.equal(f.get('notice').className, 'error');
    assert.match(f.get('notice').textContent, saved ? /设置已保存，但预览版本已变化或无法确认/ : /版本不一致或无法确认/);
    assert.match(f.get('notice').textContent, /重新载入.*刷新页面/);
};
function scenario(name, run) {
    test(name, {timeout:15000}, async t=>{
        const b = await backend(t), baseline = await b.inspect();
        const f = await fixture({routingFetch:b.fetch});
        await run({f, b, baseline});
    });
}

for (const dirtyStart of [false, true]) {
    scenario(`reload rejects a real second-writer preview and preserves the old state; dirty=${dirtyStart}`, async ({f, b, baseline})=>{
        if (dirtyStart) await f.edit('keep-draft.example.test');
        const before = f.ui();
        assert.equal((await b.commit({...original, proxy_domains:['reader-a.example.test']}, baseline.snapshot.revision)).status, 200);
        const a = await b.inspect();
        f.queue(SNAPSHOT, async options=>{
            const result = await b.fetch(SNAPSHOT, options);
            assert.equal((await b.commit({...original, proxy_domains:['writer-b.example.test']}, a.snapshot.revision)).status, 200);
            return result;
        });
        const count = f.requests.length;
        await f.click('reload'); const winner = await b.inspect();
        assert.notEqual(winner.snapshot.revision, a.snapshot.revision);
        assert.deepEqual(f.ui(), before, 'even confirmed discard cannot commit mixed saved versions');
        mismatch(f); navigation(f, dirtyStart);
        assert.equal(f.confirms.length, dirtyStart ? 1 : 0);
        assert.deepEqual(f.requests.slice(count).map(r=>[r.method, r.url]), [['GET', SNAPSHOT], ['GET', PREVIEW]], 'no automatic retry or write');
        assert.equal(f.get('reload').disabled, false, 'busy lock is released');
        await f.edit(original.proxy_domains[0]); clean(f, original.proxy_domains[0]);
        await f.edit('deliberate-save.example.test'); await f.click('save');
        assert.equal(f.requests.filter(r=>r.method === 'PUT').at(-1).options.headers['If-Match'], quoted(baseline.snapshot.revision));
        assert.equal(b.replies.at(-1).status, 409); assert.deepEqual(await b.inspect(), winner);
        f.assertDirty('deliberate-save.example.test');
        await f.click('reload'); clean(f, 'writer-b.example.test');
        assert.match(f.get('rule-preview').textContent, /writer-b\.example\.test/);
        assert.equal(f.get('preview-label').textContent, '已保存规则预览'); navigation(f, false);
        await f.edit('after-explicit-reload.example.test'); await f.click('save');
        assert.equal(f.requests.filter(r=>r.method === 'PUT').at(-1).options.headers['If-Match'], quoted(winner.snapshot.revision));
        clean(f, 'after-explicit-reload.example.test');
    });
}

for (const editStage of ['none', 'put', 'preview']) {
    scenario(`confirmed SAVE rejects a real second-writer preview without rollback; newer edit=${editStage}`, async ({f, b, baseline})=>{
        await f.edit('saved-a.example.test'); const oldPreview = preview(f);
        if (editStage === 'put') f.queue(ROUTING, async options=>{
            const result = await b.fetch(ROUTING, options);
            await f.edit('newer-draft.example.test'); return result;
        }, 'PUT');
        let acknowledged, winner;
        f.queue(PREVIEW, async options=>{
            acknowledged = await b.inspect();
            assert.deepEqual(acknowledged.snapshot.settings.proxy_domains, ['saved-a.example.test']);
            assert.equal((await b.commit({...original, proxy_domains:['writer-b.example.test']}, acknowledged.snapshot.revision)).status, 200);
            winner = await b.inspect();
            if (editStage === 'preview') await f.edit('newer-draft.example.test');
            return b.fetch(PREVIEW, options);
        });
        const count = f.requests.length; await f.click('save');
        assert.notEqual(acknowledged.snapshot.revision, baseline.snapshot.revision);
        assert.deepEqual(preview(f), oldPreview); mismatch(f, true);
        assert.deepEqual(f.requests.slice(count).map(r=>[r.method, r.url]), [['PUT', ROUTING], ['GET', PREVIEW]], 'one acknowledged write, no retry');
        if (editStage === 'none') {
            clean(f, 'saved-a.example.test');
            assert.equal(f.get('preview-label').textContent, '设置已保存 · 预览未更新，请重新载入');
        } else f.assertDirty('newer-draft.example.test');
        navigation(f, editStage !== 'none');
        await f.edit(original.proxy_domains[0]); f.assertDirty(original.proxy_domains[0]);
        await f.edit('saved-a.example.test'); clean(f, 'saved-a.example.test');
        await f.edit('deliberate-save.example.test'); await f.click('save');
        assert.equal(f.requests.filter(r=>r.method === 'PUT').at(-1).options.headers['If-Match'], quoted(acknowledged.snapshot.revision));
        assert.equal(b.replies.at(-1).status, 409); assert.deepEqual(await b.inspect(), winner);
        await f.click('reload'); clean(f, 'writer-b.example.test');
        assert.match(f.get('rule-preview').textContent, /writer-b\.example\.test/);
        await f.edit('after-recovery.example.test'); await f.click('save');
        assert.equal(f.requests.filter(r=>r.method === 'PUT').at(-1).options.headers['If-Match'], quoted(winner.snapshot.revision));
        clean(f, 'after-recovery.example.test');
    });
}

for (const action of ['reload', 'save']) {
    scenario(`real same-revision ${action} still displays its saved plan`, async ({f, b, baseline})=>{
        if (action === 'save') await f.edit('same-version.example.test');
        else assert.equal((await b.commit({...original, proxy_domains:['same-version.example.test']}, baseline.snapshot.revision)).status, 200);
        await f.click(action); clean(f, 'same-version.example.test'); navigation(f, false);
        const saved = await b.inspect();
        const savedPlan = b.replies.filter(r=>r.url === PREVIEW).at(-1).value;
        assert.equal(savedPlan.revision, saved.snapshot.revision);
        assert.match(f.get('rule-preview').textContent, /same-version\.example\.test/);
        assert.equal(f.get('preview-label').textContent, '已保存规则预览');
        assert.equal(f.get('notice').className, '');
    });
    for (const fault of ['missing', 'malformed', 'trailing newline']) {
        scenario(`${action} fails closed for ${fault} saved-preview revision metadata`, async ({f, b, baseline})=>{
            await f.edit('local-a.example.test'); const before = f.ui(), oldPreview = preview(f);
            f.queue(PREVIEW, async options=>{
                const result = await b.fetch(PREVIEW, options), value = await result.json();
                if (fault === 'missing') delete value.revision;
                else if (fault === 'malformed') value.revision = {revision:value.revision};
                else value.revision += '\n';
                return {...result, json:async()=>structuredClone(value)};
            });
            const count = f.requests.length; await f.click(action); const disk = await b.inspect();
            mismatch(f, action === 'save'); assert.deepEqual(preview(f), oldPreview);
            assert.equal(f.requests.length - count, 2, 'no automatic reads or writes');
            if (action === 'reload') {
                assert.deepEqual(f.ui(), before); assert.deepEqual(disk, baseline); navigation(f, true);
            } else {
                clean(f, 'local-a.example.test'); navigation(f, false);
                assert.notEqual(disk.snapshot.revision, baseline.snapshot.revision);
                assert.equal(f.get('preview-label').textContent, '设置已保存 · 预览未更新，请重新载入');
            }
            await f.edit('explicit-next-save.example.test'); await f.click('save');
            assert.equal(f.requests.filter(r=>r.method === 'PUT').at(-1).options.headers['If-Match'], quoted(disk.snapshot.revision));
            clean(f, 'explicit-next-save.example.test');
            await f.click('reload'); assert.equal(f.get('preview-label').textContent, '已保存规则预览');
        });
    }
}

test('matching but malformed snapshot and preview revisions cannot authorize a reload', async()=>{
    const f = await fixture(); await f.edit('keep.example.test'); const before = f.ui();
    for (const revision of [undefined, null, '', 'r1', 'a'.repeat(63), 'A'.repeat(64), 'a'.repeat(64) + '\n']) {
        f.queue(SNAPSHOT, async()=>response({revision, settings:replacement}));
        f.queue(PREVIEW, async()=>response({...plan('wrong'), revision}));
        await f.click('reload'); assert.deepEqual(f.ui(), before); mismatch(f);
    }
    await f.assertRevision(R1);
});

test('reload newer-input protection wins over an unrelated preview revision', async()=>{
    const f = await fixture(); const oldPreview = preview(f);
    f.queue(SNAPSHOT, async()=>response({revision:R2, settings:replacement}));
    const gate = f.hold(PREVIEW), pending = f.click('reload'); await bounded(gate.started, 'preview request');
    await f.edit('new-input.example.test'); gate.resolve(response(plan('unrelated', R1))); await bounded(pending, 'reload completion');
    f.assertDirty('new-input.example.test'); assert.deepEqual(preview(f), oldPreview);
    assert.match(f.get('notice').textContent, /载入期间表单已修改/);
    assert.equal(f.get('notice').className, ''); await f.assertRevision(R1);
});

test('startup mismatch keeps the workspace hidden and gives reachable page-refresh guidance', async t=>{
    const b = await backend(t);
    const f = await fixture({bootstrapFailure:true, routingFetch:async (url, options)=>{
        const result = await b.fetch(url, options);
        if (url === SNAPSHOT) {
            const value = await result.json();
            assert.equal((await b.commit({...original, proxy_domains:['startup-writer.example.test']}, value.revision)).status, 200);
        }
        return result;
    }});
    assert.equal(f.get('workspace').hidden, true); mismatch(f);
    assert.equal(f.get('proxy').value, ''); assert.equal(f.get('rule-preview').textContent, '');
    assert(f.requests.every(r=>r.method === 'GET'));
    assert.equal(f.requests.filter(r=>r.url === SNAPSHOT).length, 1);
    assert.equal(f.requests.filter(r=>r.url === PREVIEW).length, 1);
});

test('draft POST preview still needs no saved revision and never persists', async()=>{
    const f = await fixture(); await f.edit('draft-only.example.test');
    f.queue(PREVIEW, async()=>response(plan('draft without revision')), 'POST');
    await f.click('preview');
    assert.equal(f.get('preview-label').textContent, '草稿预览 · 尚未保存');
    assert.match(f.get('rule-preview').textContent, /draft without revision/);
    assert.equal(f.requests.filter(r=>r.method === 'PUT').length, 0); navigation(f, true);
});
