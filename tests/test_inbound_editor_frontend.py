"""Execute the real Vue setup/payload code with a minimal Node harness."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node required for frontend contract check')
class InboundEditorFrontendTests(unittest.TestCase):
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


    def test_managed_certificate_selector_matches_validated_tls_protocols(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require('node:fs'), assert = require('node:assert/strict');
const html = fs.readFileSync('web/index.html', 'utf8');
const selector = html.match(/<template v-if="([^"]+)">\s*<el-form-item label="托管证书" v-if="([^"]+)"/);
assert.ok(selector, 'managed selector must remain inside the TLS template');
const visible = new Function('newInbound', `return (${selector[1]}) && (${selector[2]});`);
for (const protocol of ['vless', 'trojan', 'vmess']) {
  assert.equal(visible({core: 'sing-box', protocol, profile: {security: 'tls'}}), true, protocol);
  for (const security of ['none', 'reality'])
    assert.equal(visible({core: 'sing-box', protocol, profile: {security}}), false, `${protocol}/${security}`);
  assert.equal(visible({core: 'xray', protocol, profile: {security: 'tls'}}), false, `xray/${protocol}`);
}
for (const protocol of ['shadowsocks', 'hysteria2', 'tuic'])
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
for (const protocol of ['vless', 'trojan', 'vmess']) {
  const node = {core: 'sing-box', protocol, stream_settings: {tls: {enabled: true}}};
  assert.equal(accepted(node), true, protocol);
  assert.equal(Boolean(accepted({...node, core: 'xray'})), false, `xray/${protocol}`);
  for (const stream_settings of [{}, {tls: {enabled: false}}, {tls: {enabled: true, reality: {}}}])
    assert.equal(Boolean(accepted({...node, stream_settings})), false, protocol);
}
for (const protocol of ['shadowsocks', 'hysteria2', 'tuic'])
  assert.equal(Boolean(accepted({core: 'sing-box', protocol, stream_settings: {tls: {enabled: true}}})), false, protocol);
'''
        result = subprocess.run(['node', '-e', script], cwd=root, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
