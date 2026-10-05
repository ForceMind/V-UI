"""Disabled legacy modules must not mutate the immutable release directory."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class SiteImportTests(unittest.TestCase):
    def test_disabled_site_import_has_no_filesystem_side_effects(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="vui-site-import-") as directory:
            work = Path(directory)
            env = {**os.environ, "PYTHONPATH": str(source),
                   "VUI_DATA_DIR": str(work / "isolated-data"), "PYTHONDONTWRITEBYTECODE": "1"}
            result = subprocess.run([sys.executable, "-B", "-c", "from app.api import files"],
                                    cwd=work, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(work.iterdir()), [], "Disabled site import must not mutate the installed payload")


if __name__ == "__main__":
    unittest.main()
