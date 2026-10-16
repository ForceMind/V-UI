"""Security-center UI contracts; the real setup runs without network or host access."""
from pathlib import Path
import shutil
import subprocess
import unittest


class SecurityCenterFrontendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for frontend contract check')
    def test_no_firewall_action_or_request_survives_repeated_navigation(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const vm = require('node:vm'), fs = require('node:fs'), assert = require('node:assert/strict');
let setup;
const requests = [], messages = [];
const app = {component() {}, use() {}, mount() {}};
const request = async (url) => {requests.push(url); return {data: []};};
const sandbox = {
  addEventListener() {}, location: {hash: ''},
  Vue: {createApp(options) {setup = options.setup; return app;}, ref: value => ({value}),
    reactive: value => value, computed: fn => ({get value() {return fn();}}), watch(){},onMounted() {}},
  ElementPlus: {ElMessage: {success: message => messages.push(message),
    error: message => messages.push(message), warning: message => messages.push(message)}, ElMessageBox: {}},
  ElementPlusIconsVue: {},
  axios: {defaults: {headers: {common: {}}}, interceptors: {response: {use() {}}},
    get: request, post: request, put: request, delete: request}
};
vm.runInNewContext(fs.readFileSync('web/js/app.js', 'utf8'), sandbox);
(async () => {
  for (let visit = 0; visit < 2; visit++) {
    const state = setup();
    for (const view of ['security', 'security', 'dashboard', 'security']) {
      state.handleSelect(view);
      assert.equal(state.currentView.value, view);
      // Exercise an accidentally restored handler, rather than merely matching its spelling.
      if (view === 'security' && typeof state.banIp === 'function') {
        state.banIpInput.value = '192.0.2.1';
        await state.banIp();
      }
    }
    assert.deepEqual(requests, [], 'security navigation must never issue a request');
    assert.equal('banIp' in state, false, 'remove the unsupported action, not only its button');
    assert.equal('banIpInput' in state, false);
  }
  const html = fs.readFileSync('web/index.html', 'utf8');
  const menu = html.match(/<el-menu-item\b[^>]*\bindex="security"[^>]*>/);
  assert.ok(menu, 'security navigation remains available');
  const attrs = new Map([...menu[0].matchAll(/([:@\w.-]+)="([^"]*)"/g)].map(match => [match[1], match[2]]));
  assert.equal(attrs.get('tabindex'), '0', 'the security menu must be reachable by Tab');
  const keys = ['@keydown.enter.prevent', '@keydown.space.prevent'];
  assert.deepEqual([...attrs.keys()].filter(key => key.startsWith('@keydown')).sort(), keys);
  const state = setup();
  for (const key of keys) {
    state.handleSelect('dashboard');
    vm.runInNewContext(attrs.get(key), state);
    assert.equal(state.currentView.value, 'security', key);
    assert.deepEqual(requests, [], 'keyboard activation must only select the read-only view');
  }
  assert.deepEqual(messages, [], 'an unavailable action must not report success or a late failure');
  assert.doesNotMatch(fs.readFileSync('web/js/app.js', 'utf8'), /\/api\/security\//);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
        result = subprocess.run(['node', '-e', script], cwd=root, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_scope_is_visible_without_an_interactive_form(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / 'web/index.html').read_text()
        section = html.split('v-if="currentView === \'security\'"', 1)[1].split('</el-main>', 1)[0]
        self.assertIn('aria-labelledby="security-center-heading"', section)
        self.assertIn('id="security-center-heading"', section)
        self.assertIn('本版本不支持主机防火墙与 SSH 管理', section)
        self.assertIn('无法封禁 IP 或开放端口', section)
        self.assertIn('不采集 SSH 登录日志', section)
        self.assertIn('请通过主机或云服务商的管理工具处理', section)
        self.assertIn(':closable="false"', section)
        self.assertNotRegex(section, r'<(?:el-)?(?:input|button|form)\b|@(?:click|submit)|v-model=')


if __name__ == '__main__':
    unittest.main()
