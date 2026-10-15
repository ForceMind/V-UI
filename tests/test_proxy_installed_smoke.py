"""The installed-runtime probe also runs locally with fake profiles only."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.proxy_import_smoke import installed_proxy_import_smoke


class ProxyInstalledProbeTests(unittest.TestCase):
    def test_actual_child_parser_probe_preserves_existing_install_data(self):
        with tempfile.TemporaryDirectory(prefix='vui-installed-proxy-') as temporary:
            root = Path(temporary)
            data = root / 'data'
            data.mkdir(mode=0o700)
            marker = data / 'existing-node-sentinel'
            marker.write_bytes(b'existing user data is not the smoke data')
            evidence = installed_proxy_import_smoke(
                Path(sys.executable), Path(__file__).resolve().parents[1], root)
            self.assertEqual(evidence['proxy_import_checks'], 7)
            self.assertIs(evidence['proxy_runtime_applied'], False)
            self.assertEqual(marker.read_bytes(), b'existing user data is not the smoke data')
            self.assertEqual(list(root.iterdir()), [data])
            self.assertEqual(list(data.iterdir()), [marker])

    def test_failed_or_incomplete_child_evidence_cannot_pass(self):
        for result in (SimpleNamespace(returncode=1, stdout='', stderr='synthetic failure'),
                       SimpleNamespace(returncode=0, stdout='{}', stderr=''),
                       SimpleNamespace(returncode=0,
                           stdout='{"proxy_import_checks":7,"proxy_runtime_applied":true}', stderr='')):
            with tempfile.TemporaryDirectory(prefix='vui-installed-proxy-') as temporary:
                with patch('scripts.proxy_import_smoke.subprocess.run', return_value=result):
                    with self.assertRaises(AssertionError):
                        installed_proxy_import_smoke(Path(sys.executable), Path.cwd(), Path(temporary))
                self.assertEqual(list(Path(temporary).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
