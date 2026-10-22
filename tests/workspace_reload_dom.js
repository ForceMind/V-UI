'use strict';
// The case bodies stay unchanged; SAVE recovery reuses this small DOM fixture.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const {fixture, SNAPSHOT, PREVIEW, ROUTING, original, replacement, plan, response, deferred} = require('./workspace_dom_fixture');
for (const dirtyStart of [false, true]) {
    test(`reload keeps newer input while snapshot is pending; initial dirty=${dirtyStart}`, async () => {
        const f = await fixture(); if (dirtyStart) await f.edit('discard-approved.example.test');
        const gate = f.hold(SNAPSHOT), pending = f.click('reload'); await gate.started;
        assert.equal(f.get('reload').disabled, true); assert.equal(f.get('proxy').disabled, false);
        await f.edit('new-unsaved.example.test');
        gate.resolve(response({revision:'r2', settings:replacement})); await pending;
        f.assertDirty('new-unsaved.example.test'); assert.equal(f.confirms.length, dirtyStart ? 1 : 0);
        assert.match(f.get('notice').textContent, /保留/);
        assert.equal(f.requests.filter(r=>r.url === PREVIEW).length, 1, 'stale reload does not request a preview');
        assert(f.requests.every(r=>r.method === 'GET')); await f.assertRevision('r1');
    });
}
test('reload keeps newer input and old revision while preview is pending', async () => {
    const f = await fixture(); await f.edit('discard-approved.example.test'); const before = f.ui();
    f.queue(SNAPSHOT, async()=>response({revision:'r2', settings:replacement}));
    const gate = f.hold(PREVIEW), pending = f.click('reload'); await gate.started;
    assert.equal(f.get('proxy').value, before.proxy, 'snapshot alone must not commit the discarded form');
    assert.equal(f.get('rule-preview').textContent, before['rule-preview']);
    await f.edit('later-preview.example.test', 'change'); gate.resolve(response(plan('stale'))); await pending;
    f.assertDirty('later-preview.example.test'); assert.equal(f.get('rule-preview').textContent, before['rule-preview']);
    assert.match(f.get('notice').textContent, /保留/); await f.assertRevision('r1');
});
test('editing then reverting during reload still invalidates that reload attempt', async () => {
    const f = await fixture(); const gate = f.hold(SNAPSHOT), pending = f.click('reload'); await gate.started;
    await f.edit('temporary.example.test'); await f.edit(original.proxy_domains[0]);
    gate.resolve(response({revision:'r2', settings:replacement})); await pending;
    assert.equal(f.get('proxy').value, original.proxy_domains[0]); assert.equal(f.get('save').disabled, true);
    assert.match(f.get('notice').textContent, /保留/); await f.edit('next.example.test'); await f.assertRevision('r1');
});
for (const dirtyStart of [false, true]) {
    test(`unchanged-form reload adopts snapshot, revision and preview; initial dirty=${dirtyStart}`, async () => {
        const f = await fixture(); if (dirtyStart) await f.edit('discard-approved.example.test');
        f.queue(SNAPSHOT, async()=>response({revision:'r2', settings:replacement}));
        f.queue(PREVIEW, async()=>response(plan('reloaded'))); await f.click('reload');
        assert.equal(f.get('proxy').value, replacement.proxy_domains[0]); assert.equal(f.get('mode').value, 'standard');
        assert.equal(f.document.querySelectorAll('[data-preset]')[0].checked, false);
        assert.match(f.get('save-state').textContent, /已与保存版本一致/); assert.equal(f.get('save').disabled, true);
        assert.match(f.get('rule-preview').textContent, /reloaded/); assert.equal(f.get('preview-label').textContent, '已保存规则预览');
        assert.equal(f.confirms.length, dirtyStart ? 1 : 0); await f.edit('next.example.test'); await f.assertRevision('r2');
    });
}
test('cancelled discard does not request anything or change draft, baseline or revision', async () => {
    const f = await fixture(); await f.edit('keep.example.test'); const before = f.ui(), count = f.requests.length;
    f.confirm(false); await f.click('reload'); assert.equal(f.requests.length, count); assert.deepEqual(f.ui(), before);
    assert.equal(f.confirms.length, 1); await f.assertRevision('r1');
});
for (const stage of [SNAPSHOT, PREVIEW]) {
    for (const failure of ['http', 'json', 'network']) {
        test(`${stage} ${failure} failure preserves draft, saved baseline, revision and preview`, async () => {
            const f = await fixture(); await f.edit('keep-on-error.example.test'); const before = f.ui();
            if (stage === PREVIEW) f.queue(SNAPSHOT, async()=>response({revision:'r2', settings:replacement}));
            f.queue(stage, async()=>{
                if (failure === 'http') return response({detail:'synthetic failure'}, 503);
                if (failure === 'network') throw new Error('synthetic failure');
                return {...response(null), json:async()=>{throw new Error('synthetic failure');}};
            });
            await f.click('reload'); assert.deepEqual(f.ui(), before); assert.equal(f.get('notice').className, 'error');
            assert.match(f.get('notice').textContent, /synthetic failure/); assert(f.requests.every(r=>r.method === 'GET'));
            await f.edit(original.proxy_domains[0]); assert.equal(f.get('save').disabled, true, 'saved baseline stays unchanged');
            await f.edit('keep-on-error.example.test'); await f.assertRevision('r1');
        });
    }
    test(`${stage} failure preserves an initially clean form and disabled save`, async () => {
        const f = await fixture(), before = f.ui();
        if (stage === PREVIEW) f.queue(SNAPSHOT, async()=>response({revision:'r2', settings:replacement}));
        f.queue(stage, async()=>response({detail:'synthetic failure'}, 503)); await f.click('reload');
        assert.deepEqual(f.ui(), before); await f.edit('next.example.test'); await f.assertRevision('r1');
    });
}
test('busy operation ignores repeated reload activation without extra request or confirmation', async () => {
    const f = await fixture(); await f.edit('discard-approved.example.test');
    const gate = f.hold(SNAPSHOT), pending = f.click('reload'); await gate.started;
    const count = f.requests.length; await f.click('reload'); assert.equal(f.requests.length, count); assert.equal(f.confirms.length, 1);
    gate.resolve(response({revision:'r2', settings:replacement})); await pending;
    assert.equal(f.get('proxy').value, replacement.proxy_domains[0]); assert.equal(f.get('reload').disabled, false);
});
test('existing save race retains edits and updates If-Match to the revision actually saved', async () => {
    const f = await fixture(); await f.edit('save-this.example.test');
    const gate = f.hold(ROUTING, 'PUT'), pending = f.click('save'); const options = await gate.started;
    assert.equal(options.headers['If-Match'], '"r1"'); await f.edit('edited-during-save.example.test');
    gate.resolve(response({...original, proxy_domains:['save-this.example.test']}, 200, 'r3')); await pending;
    f.assertDirty('edited-during-save.example.test'); await f.assertRevision('r3');
});
test('existing draft-preview race never replaces a newer input warning or preview', async () => {
    const f = await fixture(); await f.edit('preview-this.example.test'); const oldPreview = f.get('rule-preview').textContent;
    const gate = f.hold(PREVIEW, 'POST'), pending = f.click('preview'); await gate.started;
    await f.edit('edited-during-preview.example.test'); gate.resolve(response(plan('stale draft'))); await pending;
    f.assertDirty('edited-during-preview.example.test'); assert.equal(f.get('rule-preview').textContent, oldPreview);
    assert.equal(f.requests.filter(r=>r.method === 'PUT').length, 0);
});

// Independent review additions: six controls, no browser or network.
for (const stage of [SNAPSHOT, PREVIEW]) {
    test(`independent check: editing during ${stage} JSON parsing preserves old revision`, async () => {
        const f = await fixture();
        const parsed = deferred(), started = deferred();
        if (stage === PREVIEW) f.queue(SNAPSHOT, async()=>response({revision:'r2', settings:replacement}));
        f.queue(stage, async()=>({...response(null), json:()=>{started.resolve();return parsed.promise;}}));
        const pending=f.click('reload'); await started.promise;
        await f.edit('edited-during-json.example.test');
        parsed.resolve(stage === SNAPSHOT ? {revision:'r2', settings:replacement} : plan('new-plan'));
        await pending; f.assertDirty('edited-during-json.example.test');
        assert.match(f.get('notice').textContent, /保留/); await f.assertRevision('r1');
    });
    test(`independent check: revert to original during ${stage} remains clean without adopting response`, async () => {
        const f = await fixture(); const before = f.ui();
        if (stage === PREVIEW) f.queue(SNAPSHOT, async()=>response({revision:'r2', settings:replacement}));
        const gate=f.hold(stage), pending=f.click('reload'); await gate.started;
        await f.edit('temporary.example.test'); await f.edit(original.proxy_domains[0]);
        gate.resolve(stage === SNAPSHOT ? response({revision:'r2', settings:replacement}) : response(plan('new-plan')));
        await pending;
        assert.equal(f.get('proxy').value, original.proxy_domains[0]);
        assert.equal(f.get('save').disabled, true);
        assert.match(f.get('save-state').textContent, /已与保存版本一致/);
        assert.match(f.get('notice').textContent, /保留/);
        assert.equal(f.get('rule-preview').textContent, before['rule-preview']);
        await f.edit('fresh.example.test'); await f.assertRevision('r1');
    });
    test(`independent check: newer edits survive ${stage} failure`, async () => {
        const f = await fixture(); const before = f.ui();
        if (stage === PREVIEW) f.queue(SNAPSHOT, async()=>response({revision:'r2', settings:replacement}));
        const gate=f.hold(stage), pending=f.click('reload'); await gate.started;
        await f.edit('new-before-error.example.test'); gate.reject(new Error('independent failure')); await pending;
        f.assertDirty('new-before-error.example.test'); assert.equal(f.get('rule-preview').textContent, before['rule-preview']);
        assert.equal(f.get('notice').className, 'error'); await f.assertRevision('r1');
    });
}
