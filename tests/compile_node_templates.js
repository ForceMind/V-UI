'use strict';
// Syntax-only compilation of the shipped #app, including the node wizard/editor.
// Uses the same-version diagnostic Vue from vendor_frontend.py --template-compiler. Never mounts, renders,
// starts a browser/server, or downloads anything. No document shim is installed.
const assert = require('node:assert/strict');
const {createHash} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const requested = process.argv[2] || process.env.VUI_FRONTEND_ASSETS;
assert(requested, 'Pass the pinned compiler directory or its vue-compiler.js file');
const assets = path.resolve(requested.endsWith('.js') ? path.dirname(requested) : requested);
const pins = JSON.parse(fs.readFileSync(path.join(root, 'deploy/frontend-pins.json'), 'utf8'));
const provenance = JSON.parse(fs.readFileSync(path.join(assets, 'PROVENANCE.json'), 'utf8'));
// Source URL/version/archive integrity stay identical to deployment Vue. Only
// the selected archive member differs; production assets/pins stay untouched.
const compilerPin = {...pins.vue, files:{
    'package/dist/vue.global.js':'vue-compiler.js',
    'package/LICENSE':'licenses/vue-LICENSE',
}};
for (const [key, value] of Object.entries(compilerPin))
    assert.deepEqual(provenance.vue[key], value, 'Vue provenance ' + key);
const bytes = fs.readFileSync(path.join(assets, 'vue-compiler.js'));
assert.equal(createHash('sha256').update(bytes).digest('hex'), provenance.vue.selected_sha256['vue-compiler.js']);
const context = vm.createContext({console});
vm.runInContext(bytes.toString(), context, {filename:'pinned-vue-compiler.js'});
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
// The production global build did not report the missing-end-tag control in
// Actions. A diagnostic compiler must prove its hook reports markup, expression
// and structural-directive errors before it can qualify the shipped template.
for (const [template, expected] of [
    ['<div><span></div>', /Element is missing end tag/],
    ['<div v-if="(">invalid expression</div>', /Error parsing JavaScript expression/],
    ['<div v-else>orphan branch</div>', /v-else\/v-else-if has no adjacent v-if/],
]) {
    let reported = null;
    assert.throws(() => V.compile(template, {...options, onError(error) {
        reported = error; throw error;
    }}), error => error === reported && Number.isInteger(error.code) && expected.test(error.message),
    'diagnostic compiler must reject ' + template);
    assert.ok(reported, 'negative control must reach the compiler error hook');
}
const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
const marker = '<div id="app">', start = html.indexOf(marker), boundary = html.indexOf('<div id="simple-node-operations"'), end = html.lastIndexOf('</div>', boundary);
assert(start >= 0 && start === html.lastIndexOf(marker) && end > start, 'one shipped #app root');
assert(boundary > end && boundary === html.lastIndexOf('<div id="simple-node-operations"'), 'one vanilla operations root outside Vue');
assert.match(html.slice(end + '</div>'.length), /^\s*<div id="simple-node-operations"[^>]*><\/div>\s*<script\b[\s\S]*<\/html>\s*$/);
const render = V.compile(html.slice(start + marker.length, end), options);
assert.equal(typeof render, 'function');
console.log('Compiled shipped #app with pinned diagnostic Vue ' + V.version + '; no mount, render or browser.');
