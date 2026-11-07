"""Presentation-only connection guide contracts; no browser or network."""
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Markup(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.details = []
        self.ids = {}
        self.feed(source)
        if self.details:
            raise ValueError('Unclosed details')

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            if attrs['id'] in self.ids:
                raise ValueError('Duplicate ID')
            self.ids[attrs['id']] = (tag, attrs, tuple(self.details))
        if tag == 'details':
            self.details.append(attrs.get('id'))

    def handle_endtag(self, tag):
        if tag == 'details':
            if not self.details:
                raise ValueError('Unexpected details close')
            self.details.pop()


class SimpleConnectionPageTests(unittest.TestCase):
    def test_direction_and_real_connection_controls_remain_available(self):
        source = (ROOT/'web/proxy.html').read_text()
        markup = Markup(source)
        self.assertIn('另一台服务器 → 本机 → 网站', source)
        self.assertIn('手机 / 电脑 → 本机 → 目标服务器 → 网站', source)
        self.assertIn('本页无需填写上游', source)
        self.assertIn('新建向导默认直接使用本机出口', source)
        self.assertIn('hidden', markup.ids['proxy-workspace'][1])
        self.assertNotIn('open', markup.ids['connection-setup'][1])
        for key in ('settings-form','draft-fields','local-enabled','relay-enabled','upstream',
                    'save','check','apply','confirm-apply','retry-apply','review-coverage'):
            self.assertIn(key, markup.ids)
            self.assertIn('connection-setup', markup.ids[key][2], key)
        self.assertEqual(markup.ids['notice'][2], ())
        self.assertEqual(markup.ids['simple-connection-operations'][2], ())
        for key in ('save-state','pending-state','conflict'):
            self.assertIn('connection-advanced', markup.ids[key][2], key)
            self.assertNotIn('connection-setup', markup.ids[key][2], key)
        self.assertIn('/ui/js/operation-flow.js', source)
        self.assertIn('/ui/js/simple-operations.js', source)

    def test_advanced_tools_and_explicit_import_guards_not_removed(self):
        source = (ROOT/'web/proxy.html').read_text()
        markup = Markup(source)
        for key in ('diagnostic-consent','diagnostics','cancel-diagnostics','export-server','export-inbound',
                    'export-mihomo','export-singbox','open-yaml-preview','import-format','import-file',
                    'import-content','preview-import','save-import'):
            self.assertIn(key, markup.ids)
        self.assertIn('connection-import-options',markup.ids['import-format'][2])
        self.assertIn('connection-import-options',markup.ids['import-file'][2])
        self.assertNotIn('connection-import-options',markup.ids['import-content'][2])
        self.assertEqual(markup.ids['preview-import'][1]['type'],'button')
        self.assertIn('disabled',markup.ids['save-import'][1])
        self.assertNotIn('onclick=',source)
        script=(ROOT/'web/js/proxy.js').read_text()
        self.assertIn("$('import-format').value='uri'",script)
        for protocol in ('If-Match','base_revision','source_fingerprint','Idempotency-Key'):
            self.assertIn(protocol.lower(),script.lower())

    @unittest.skipUnless(shutil.which('node'),'Node required')
    def test_guide_only_opens_form_and_focuses_heading(self):
        code = r"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync('web/js/connection-guide.js','utf8');
for(const missing of [false,true]) {
 let listener,focused=0;
 const button={addEventListener(name,fn){assert.equal(name,'click');listener=fn}};
 const section={open:false}, title={focus(){focused++}};
 const context={document:{getElementById(id){return missing?null:{'open-connection-setup':button,'connection-setup':section,'connection-setup-title':title}[id]}}};
 vm.runInNewContext(source,context);
 assert.equal(section.open,false);assert.equal(focused,0);
 if(!missing){listener();assert(section.open);assert.equal(focused,1)}else assert.equal(listener,undefined);
}
"""
        result=subprocess.run(['node','-e',code],cwd=ROOT,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
