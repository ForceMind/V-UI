'use strict';
// Syntax-only compilation of the shipped #app, including the node wizard/editor.
// Uses checksum-pinned Vue from vendor_frontend.py output. Never mounts, renders,
// starts a browser/server, or downloads anything. No document shim is installed.
const assert = require('node:assert/strict');
const {createHash} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const requested = process.argv[2] || process.env.VUI_FRONTEND_ASSETS;
assert(requested, 'Pass the pinned assets directory or its vue.js file');
const assets = path.resolve(requested.endsWith('.js') ? path.dirname(requested) : requested);
const pins = JSON.parse(fs.readFileSync(path.join(root, 'deploy/frontend-pins.json'), 'utf8'));
const provenance = JSON.parse(fs.readFileSync(path.join(assets, 'PROVENANCE.json'), 'utf8'));
for (const [key, value] of Object.entries(pins.vue))
    assert.deepEqual(provenance.vue[key], value, 'Vue provenance ' + key);
const bytes = fs.readFileSync(path.join(assets, 'vue.js'));
assert.equal(createHash('sha256').update(bytes).digest('hex'), provenance.vue.selected_sha256['vue.js']);
const context = vm.createContext({console});
vm.runInContext(bytes.toString(), context, {filename:'pinned-vue.js'});
const V = context.Vue;
assert.equal(V.version, pins.vue.version);
// The actual template currently uses numeric newlines in placeholders. Handle
// those plus five basic HTML entities and fail closed on any unsupported entity.
// This is a compiler input helper only, not DOM/browser entity-decoding evidence.
function decodeEntities(value) {
    const basic = {amp:'&',lt:'<',gt:'>',quot:'"',apos:"'"};
    return value.replace(/&(#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);/gi, (entity, name) => {
        if (Object.hasOwn(basic, name)) return basic[name];
        if (/^#\d+$/.test(name)) return String.fromCodePoint(Number(name.slice(1)));
        if (/^#x[0-9a-f]+$/i.test(name)) return String.fromCodePoint(parseInt(name.slice(2), 16));
        throw new Error('Unsupported compiler entity: ' + entity);
    });
}
const options = {decodeEntities, onError(error) {throw error;}, onWarn(warning) {throw warning;}};
// Production builds can silence diagnostics by default. Prove this invocation
// really rejects malformed markup before trusting the shipped-template result.
assert.throws(() => V.compile('<div><span></div>', options));
const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
const marker = '<div id="app">', start = html.indexOf(marker), end = html.lastIndexOf('</div>');
assert(start >= 0 && start === html.lastIndexOf(marker) && end > start, 'one shipped #app root');
assert.match(html.slice(end + '</div>'.length), /^\s*<script\b[\s\S]*<\/html>\s*$/);
const render = V.compile(html.slice(start + marker.length, end), options);
assert.equal(typeof render, 'function');
console.log('Compiled shipped #app with pinned Vue ' + V.version + '; no mount, render or browser.');
