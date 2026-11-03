"""Run the TLS certificate editor contract without starting a browser."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node required for frontend contract check')
class InboundManagedCertificateFrontendTests(unittest.TestCase):
    def test_managed_certificate_choices_and_async_editor_boundaries(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ['node', '--test', 'tests/inbound_managed_certificate_dom.js'],
            cwd=root, text=True, capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
