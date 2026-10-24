'use strict';
// Shared bounded direct-route bridge. No HTTP, auth, browser, sockets or cores.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const {ROUTING, SNAPSHOT, original, deferred} = require('../workspace_dom_fixture');
const root = path.resolve(__dirname, '../..');
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

module.exports = {backend, bounded, quoted};
