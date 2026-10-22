'use strict';
// Five bounded SAVE controls: real Python routes/storage, simulated DOM/fetch.
// No HTTP, auth, browser, navigation, sockets, or core-process qualification.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const {test} = require('node:test');
const {fixture, ROUTING, SNAPSHOT, PREVIEW, original, response, deferred} = require('./workspace_dom_fixture');
const root = path.resolve(__dirname, '..');
const quoted = revision => '"' + revision + '"';

async function bounded(promise, label, milliseconds = 5000) {
    let timer;
    try { return await Promise.race([promise, new Promise((_, reject)=>{
        timer = setTimeout(()=>reject(new Error(label + ' timed out')), milliseconds);
    })]); } finally { clearTimeout(timer); }
}

async function backend(t) {
    const pending = new Map(), ready = deferred(), closed = deferred(), replies = [];
    let nextId = 0, exitState, stderr = '', dataDir;
    const child = spawn(process.env.VUI_TEST_PYTHON_EXECUTABLE || 'python3',
        ['-u', 'tests/fixtures/workspace_routing_bridge.py'], {
            cwd:root, stdio:['pipe', 'pipe', 'pipe'], timeout:15000,
            env:{...process.env, PYTHONPATH:root, PYTHONDONTWRITEBYTECODE:'1'},
        });
    const fail = error => {ready.reject(error);for (const task of pending.values()) task.reject(error);pending.clear();};
    const emergencyStop = () => child.kill('SIGTERM');
    process.once('exit', emergencyStop);
    child.on('error', fail);
    child.stderr.on('data', chunk=>{stderr += chunk.toString();});
    child.on('close', (code, signal)=>{
        exitState = {code, signal}; closed.resolve(exitState);
        fail(new Error(`Python bridge closed: ${JSON.stringify(exitState)} ${stderr}`));
    });
    const lines = readline.createInterface({input:child.stdout});
    lines.on('line', line=>{
        try {
            const value = JSON.parse(line);
            if (value.ready) {dataDir = value.data_dir;ready.resolve();return;}
            const task = pending.get(value.id);
            assert(task, 'bridge response must match an outstanding request');
            pending.delete(value.id); task.resolve(value);
        } catch (error) { fail(error); }
    });
    async function send(value) {
        assert.equal(exitState, undefined, 'bridge must be running');
        const id = ++nextId, task = deferred(); pending.set(id, task);
        child.stdin.write(JSON.stringify({...value, id}) + '\n', error=>{if (error) fail(error);});
        try { return await bounded(task.promise, 'Python request ' + id); }
        finally { pending.delete(id); }
    }
    // Register cleanup before bootstrap: failed assertions/imports still stop the child.
    t.after(async()=>{
        try {
            if (!exitState) { await send({command:'shutdown'}); child.stdin.end(); }
        } finally {
            if (!exitState) {
                try { await bounded(closed.promise, 'Python shutdown', 3000); }
                catch (error) { child.kill('SIGTERM'); await bounded(closed.promise, 'Python termination', 3000); throw error; }
            }
            process.removeListener('exit', emergencyStop); lines.close();
        }
        assert.deepEqual(exitState, {code:0, signal:null}, stderr);
        assert(dataDir, 'bridge announces its isolated store');
        assert.equal(fs.existsSync(dataDir), false, 'Python removes its temporary data directory');
    });
    await bounded(ready.promise, 'Python bootstrap');
    const fetch = async (url, options = {}) => {
        const reply = await send({url, method:options.method || 'GET',
            payload:options.body ? JSON.parse(options.body) : undefined,
            revision:options.headers?.['If-Match']});
        replies.push({url, method:options.method || 'GET', ...reply});
        return {ok:reply.status >= 200 && reply.status < 300, status:reply.status,
            json:async()=>structuredClone(reply.value), headers:{get:name=>name === 'ETag' ? reply.etag : null}};
    };
    const commit = (settings, revision) => fetch(ROUTING,
        {method:'PUT', body:JSON.stringify(settings), headers:{'If-Match':quoted(revision)}});
    const empty = await (await fetch(SNAPSHOT)).json();
    assert.equal((await commit(original, empty.revision)).status, 200);
    const inspect = async()=>{const {id, ...value} = await send({command:'inspect'});return value;};
    return {fetch, commit, inspect, replies};
}

function scenario(name, run) {
    test(name, {timeout:15000}, async t=>{
        const b = await backend(t), baseline = await b.inspect();
        const f = await fixture({routingFetch:b.fetch});
        const puts = () => f.requests.filter(item=>item.method === 'PUT');
        const lastPut = revision => assert.equal(puts().at(-1).options.headers['If-Match'], quoted(revision));
        const assertClean = value => {
            assert.equal(f.get('proxy').value, value); assert.equal(f.get('save').disabled, true);
            assert.match(f.get('save-state').textContent, /已与保存版本一致/);
        };
        await run({f, b, baseline, puts, lastPut, assertClean});
    });
}

scenario('real 422 preserves disk, dirty draft and old revision; corrected SAVE succeeds', async ({f, b, baseline, puts, lastPut, assertClean})=>{
    const invalid = 'https://invalid.example.test:99999';
    await f.edit(invalid); await f.click('save');
    assert.equal(b.replies.at(-1).status, 422); assert.equal(puts().length, 1);
    assert.deepEqual(await b.inspect(), baseline); f.assertDirty(invalid); lastPut(baseline.snapshot.revision);
    assert.equal(f.get('notice').className, 'error'); assert.doesNotMatch(f.get('notice').textContent, /设置已保存/);
    await f.edit(original.proxy_domains[0]); assertClean(original.proxy_domains[0]);
    await f.edit('corrected.example.test'); await f.click('save'); lastPut(baseline.snapshot.revision);
    assert.equal(puts().length, 2); assertClean('corrected.example.test');
    const saved = await b.inspect(); assert.notEqual(saved.disk, baseline.disk);
    assert.deepEqual(saved.snapshot.settings.proxy_domains, ['corrected.example.test']);
    assert.equal(f.get('preview-label').textContent, '已保存规则预览');
});

scenario('real second-writer 409 preserves winner and draft until explicit reload', async ({f, b, baseline, puts, lastPut, assertClean})=>{
    await f.edit('local-draft.example.test');
    assert.equal((await b.commit({...original, proxy_domains:['winner.example.test']}, baseline.snapshot.revision)).status, 200);
    const winner = await b.inspect();
    await f.click('save'); assert.equal(b.replies.at(-1).status, 409);
    assert.equal(puts().length, 1, 'conflict does not auto retry'); lastPut(baseline.snapshot.revision);
    f.assertDirty('local-draft.example.test'); assert.deepEqual(await b.inspect(), winner);
    assert.match(f.get('notice').textContent, /冲突/); assert.equal(f.confirms.length, 0);
    await f.click('save'); assert.equal(b.replies.at(-1).status, 409);
    assert.equal(puts().length, 2); lastPut(baseline.snapshot.revision);
    assert.deepEqual(await b.inspect(), winner); f.assertDirty('local-draft.example.test');
    await f.click('reload'); assert.equal(f.confirms.length, 1); assertClean('winner.example.test');
    await f.edit('after-reload.example.test'); await f.click('save'); lastPut(winner.snapshot.revision);
    assert.equal(puts().length, 3); assertClean('after-reload.example.test');
    assert.deepEqual((await b.inspect()).snapshot.settings.proxy_domains, ['after-reload.example.test']);
});

scenario('committed SAVE with unparseable acknowledgment stays dirty; retry conflicts and reload resolves', async ({f, b, baseline, puts, lastPut, assertClean})=>{
    await f.edit('committed-unacknowledged.example.test');
    f.queue(ROUTING, async options=>{
        const acknowledgment = await b.fetch(ROUTING, options);
        assert.equal(acknowledgment.status, 200, 'real backend has committed');
        return {...acknowledgment, json:async()=>{throw new SyntaxError('synthetic truncated acknowledgment');}};
    }, 'PUT');
    await f.click('save'); const committed = await b.inspect();
    assert.notEqual(committed.disk, baseline.disk); assert.notEqual(committed.snapshot.revision, baseline.snapshot.revision);
    assert.deepEqual(committed.snapshot.settings.proxy_domains, ['committed-unacknowledged.example.test']);
    f.assertDirty('committed-unacknowledged.example.test'); assert.equal(puts().length, 1);
    assert.equal(f.get('notice').className, 'error'); assert.match(f.get('notice').textContent, /truncated acknowledgment/);
    await f.edit(original.proxy_domains[0]); assertClean(original.proxy_domains[0]);
    await f.edit('committed-unacknowledged.example.test'); await f.click('save');
    assert.equal(b.replies.at(-1).status, 409); lastPut(baseline.snapshot.revision);
    assert.equal(puts().length, 2); assert.deepEqual(await b.inspect(), committed);
    f.assertDirty('committed-unacknowledged.example.test');
    await f.click('reload'); assert.equal(f.confirms.length, 1); assertClean('committed-unacknowledged.example.test');
    await f.edit('after-ack-recovery.example.test'); await f.click('save'); lastPut(committed.snapshot.revision);
    assert.equal(puts().length, 3); assertClean('after-ack-recovery.example.test');
    assert.deepEqual((await b.inspect()).snapshot.settings.proxy_domains, ['after-ack-recovery.example.test']);
});

scenario('acknowledged PUT followed by preview failure keeps new baseline/revision and old preview', async ({f, b, baseline, puts, lastPut, assertClean})=>{
    const oldPreview = f.get('rule-preview').textContent, oldCount = f.get('rule-count').textContent;
    await f.edit('saved-preview-failed.example.test');
    f.queue(PREVIEW, async()=>response({detail:'synthetic saved preview unavailable'}, 503));
    await f.click('save'); const committed = await b.inspect();
    assert.notEqual(committed.snapshot.revision, baseline.snapshot.revision); assert.notEqual(committed.disk, baseline.disk);
    assert.deepEqual(committed.snapshot.settings.proxy_domains, ['saved-preview-failed.example.test']);
    assertClean('saved-preview-failed.example.test'); assert.equal(puts().length, 1);
    assert.equal(f.get('rule-preview').textContent, oldPreview); assert.equal(f.get('rule-count').textContent, oldCount);
    assert.match(f.get('preview-label').textContent, /重新生成/);
    assert.equal(f.get('notice').className, 'error'); assert.match(f.get('notice').textContent, /saved preview unavailable/);
    assert.doesNotMatch(f.get('notice').textContent, /设置已保存/);
    await f.edit(original.proxy_domains[0]); f.assertDirty(original.proxy_domains[0]);
    await f.edit('saved-preview-failed.example.test'); assertClean('saved-preview-failed.example.test');
    await f.edit('next-after-preview-failure.example.test'); await f.click('save'); lastPut(committed.snapshot.revision);
    assert.equal(puts().length, 2); assertClean('next-after-preview-failure.example.test');
    assert.deepEqual((await b.inspect()).snapshot.settings.proxy_domains, ['next-after-preview-failure.example.test']);
});

scenario('newer edit during post-SAVE preview remains dirty, suppresses stale preview and uses acknowledged revision', async ({f, b, baseline, puts, lastPut, assertClean})=>{
    const oldPreview = f.get('rule-preview').textContent;
    await f.edit('acknowledged-before-preview.example.test');
    const gate = f.hold(PREVIEW), pending = f.click('save'); await bounded(gate.started, 'saved preview request');
    try {
        const committed = await b.inspect();
        assert.notEqual(committed.snapshot.revision, baseline.snapshot.revision);
        assert.deepEqual(committed.snapshot.settings.proxy_domains, ['acknowledged-before-preview.example.test']);
        const stalePreview = await b.fetch(PREVIEW);
        await f.edit('newer-during-preview.example.test'); gate.resolve(stalePreview); await bounded(pending, 'SAVE completion');
        f.assertDirty('newer-during-preview.example.test'); assert.equal(f.get('rule-preview').textContent, oldPreview);
        assert.deepEqual(await b.inspect(), committed); assert.equal(puts().length, 1);
        await f.click('save'); lastPut(committed.snapshot.revision); assert.equal(puts().length, 2);
        assertClean('newer-during-preview.example.test');
        assert.deepEqual((await b.inspect()).snapshot.settings.proxy_domains, ['newer-during-preview.example.test']);
        assert.match(f.get('rule-preview').textContent, /newer-during-preview\.example\.test/);
    } finally { gate.resolve(response({sections:[], warnings:[]})); await bounded(pending, 'released SAVE'); }
});
