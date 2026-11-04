"""Static and synthetic event contracts, never a browser test."""
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RemoteMaintenanceFrontendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_production_controller_synthetic_lifecycles(self):
        result = subprocess.run(['node','tests/maintenance_dom.js'], cwd=ROOT,
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fragment_consumed_before_navigation_and_static_markup(self):
        html = (ROOT/'web/maintenance.html').read_text()
        self.assertLess(html.index('/ui/js/maintenance.js'), html.index('/ui/js/navigation.js'))
        self.assertNotIn('onclick=',html)
        script = (ROOT/'web/js/maintenance.js').read_text()
        for forbidden in ('localStorage','sessionStorage','console.log','innerHTML','eval('):
            self.assertNotIn(forbidden,script)
        self.assertIn("credentials: token || exchange ? 'omit' : 'same-origin'",script)
        self.assertIn("history.replaceState(null, '', location.pathname)",script)
        self.assertIn("addEventListener('pagehide', end)",script)
