"""Current user entry points must follow VERSION without rewriting history."""
from pathlib import Path
import shutil
import tempfile
import unittest
from scripts.check_docs import CURRENT_INSTALL_GUIDES, ROOT, check_current_versions


class CurrentDocumentationTests(unittest.TestCase):
    def test_current_candidate_versions_match(self):
        check_current_versions(ROOT, (ROOT / 'VERSION').read_text().strip())

    def test_each_stale_entry_point_is_rejected(self):
        version = (ROOT / 'VERSION').read_text().strip()
        names = (*CURRENT_INSTALL_GUIDES, 'docs/README.md', 'docs/RELEASE_NOTES.md')
        for stale in names:
            with self.subTest(path=stale), tempfile.TemporaryDirectory(prefix='vui-doc-version-') as temporary:
                root = Path(temporary)
                for name in names:
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / name, target)
                target = root / stale
                target.write_text(target.read_text().replace(version, '0.0.0'))
                with self.assertRaisesRegex(ValueError, 'version mismatch'):
                    check_current_versions(root, version)

    def test_historical_guides_do_not_enter_current_version_contract(self):
        self.assertNotIn('docs/DEPLOYMENT_RC2.md', CURRENT_INSTALL_GUIDES)
