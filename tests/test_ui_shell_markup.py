"""Static product-shell contracts; does not launch or emulate a browser."""
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Markup(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.nodes = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


class ShellMarkupTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for shell event contracts')
    def test_real_navigation_and_shell_events(self):
        result = subprocess.run(['node', 'tests/ui_shell_dom.js'], cwd=ROOT, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_each_page_loads_shared_navigation_before_deferred_shell(self):
        for page in ('index', 'workspace', 'proxy', 'certificates', 'account'):
            with self.subTest(page=page):
                text = (ROOT / f'web/{page}.html').read_text()
                nodes = Markup(text).nodes
                nav = next(a for t, a in nodes if t == 'script' and a.get('src') == '/ui/js/navigation.js')
                shell = next(a for t, a in nodes if t == 'script' and a.get('src') == '/ui/js/shell.js')
                self.assertNotIn('defer', nav)
                self.assertNotIn('async', nav)
                self.assertIn('defer', shell)
                self.assertIn('href="/ui/css/shell.css"', text)
                self.assertRegex(text, r'<body data-vui-page="[a-z]+">')
                self.assertEqual(text.count('id="vui-main"'), 1)
                ids = [a['id'] for _, a in nodes if 'id' in a]
                self.assertEqual(len(ids), len(set(ids)), 'duplicate DOM IDs')

    def test_workspace_keeps_protected_initial_visibility(self):
        nodes = Markup((ROOT / 'web/workspace.html').read_text()).nodes
        by_id = {a['id']: a for _, a in nodes if 'id' in a}
        self.assertIn('hidden', by_id['workspace'])
        self.assertIn('hidden', by_id['issued'])
        for key in ('routing', 'subscriptions'):
            self.assertEqual(by_id[key]['data-workspace-section'], key)
        for key in ('routing-form', 'grant-form', 'issued-urls', 'initial-retry', 'grants', 'nodes', 'zones', 'presets'):
            self.assertIn(key, by_id)

    def test_editor_groups_retain_real_fields_and_save_contract(self):
        text = (ROOT / 'web/index.html').read_text()
        self.assertEqual(re.findall(r'data-editor-group="(.*?)"', text),
                         ['basic', 'security', 'transport', 'routing', 'advanced'])
        for binding in ('newInbound.core', 'newInbound.remark', 'newInbound.port', 'newInbound.protocol',
                        'newInbound.enable', 'newInbound.routing_mode', 'newInbound.certificate_id',
                        'newInbound.profile.transport', 'newInbound.profile.security', 'newInbound.profile.flow',
                        'newInbound.profile.shadowsocks_method', 'newInbound.profile.path', 'newInbound.profile.host',
                        'newInbound.profile.service_name', 'newInbound.profile.xhttp_mode',
                        'newInbound.profile.server_name', 'newInbound.profile.certificate_path',
                        'newInbound.profile.key_path', 'newInbound.profile.skip_cert_verify',
                        'newInbound.profile.reality_uuid', 'newInbound.profile.reality_target',
                        'newInbound.profile.reality_server_name', 'newInbound.profile.reality_short_id',
                        'newInbound.profile.client_fingerprint', 'newInbound.profile.hysteria2_password',
                        'newInbound.profile.up_mbps', 'newInbound.profile.down_mbps',
                        'newInbound.profile.obfs_type', 'newInbound.profile.obfs_password',
                        'newInbound.profile.tuic_uuid', 'newInbound.profile.tuic_password',
                        'newInbound.profile.congestion_control', 'newInbound.profile.udp_relay_mode',
                        'newInbound.profile.zero_rtt_handshake'):
            self.assertIn(f'v-model="{binding}"', text)
        self.assertIn('@click="saveInbound"', text)
        self.assertIn(':before-close="beforeInboundEditorClose"', text)
        for action in ('share-close', 'upstream-export-panel', 'inbound-routing-mode'):
            self.assertIn(f'id="{action}"', text)

    def test_service_and_unavailable_views_use_actual_contracts(self):
        text = (ROOT / 'web/index.html').read_text()
        for key in ('cores', 'diagnostics', 'firewall', 'legacy', 'security'):
            self.assertIn(f"currentView === '{key}'", text)
        self.assertIn("coreFieldKnown(core, 'running')", text)
        self.assertIn('@click="restartCore(core)"', text)
        self.assertNotIn('/api/files/upload_site', text)
        self.assertIn('通用面板 / 核心日志读取与下载能力尚未提供', text)
        self.assertIn('该候选尚未发布', text)

    def test_login_remains_independent_and_retains_auth_fields(self):
        text = (ROOT / 'web/account.html').read_text()
        nodes = Markup(text).nodes
        by_id = {a['id']: a for _, a in nodes if 'id' in a}
        for key in ('login-form', 'account-panel'):
            self.assertIn('hidden', by_id[key])
        for key in ('login-password', 'current-password', 'new-password'):
            self.assertEqual(by_id[key]['type'], 'password')
        self.assertIn('全新安装会自动生成管理员用户名与密码', text)
        self.assertIn('升级保留已有账户', text)

    def test_shell_has_no_business_io_or_sensitive_storage(self):
        shell = (ROOT / 'web/js/shell.js').read_text()
        for forbidden in ('fetch(', 'axios.', 'localStorage', 'sessionStorage', 'innerHTML = user', 'iframe'):
            self.assertNotIn(forbidden, shell)
        css = (ROOT / 'web/css/shell.css').read_text()
        self.assertIn('--vui-sidebar-width:220px', css)
        self.assertIn('[hidden]{display:none!important}', css)
        self.assertIn('overflow-x:auto', css)
        self.assertIn('min-height:44px', css)
        user = re.search(r'\.vui-current-user\{([^}]+)\}', css).group(1)
        for rule in ('min-width:0', 'max-width:100%', 'overflow-wrap:anywhere'):
            self.assertIn(rule, user)


if __name__ == '__main__':
    unittest.main()
