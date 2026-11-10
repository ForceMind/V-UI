"""Public install examples follow the frozen short entry; notes follow VERSION."""
from pathlib import Path
import shutil
import tempfile
import unittest
from scripts.check_docs import CURRENT_INSTALL_GUIDES, ROOT, check_current_versions, public_install_version


class CurrentDocumentationTests(unittest.TestCase):
    def test_current_candidate_versions_match(self):
        check_current_versions(ROOT, (ROOT / 'VERSION').read_text().strip())

    def test_each_stale_entry_point_is_rejected(self):
        version = (ROOT / 'VERSION').read_text().strip()
        public = public_install_version(ROOT)
        names = (*CURRENT_INSTALL_GUIDES, 'docs/README.md', 'docs/RELEASE_NOTES.md', 'setup.sh')
        for stale in names:
            with self.subTest(path=stale), tempfile.TemporaryDirectory(prefix='vui-doc-version-') as temporary:
                root = Path(temporary)
                for name in names:
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / name, target)
                target = root / stale
                expected = version if stale == 'docs/RELEASE_NOTES.md' else public
                target.write_text(target.read_text().replace(expected, '0.0.0'))
                with self.assertRaisesRegex(ValueError, 'version mismatch'):
                    check_current_versions(root, version)

    def test_candidate_notes_do_not_change_public_install_examples(self):
        public = public_install_version(ROOT)
        candidate = '.'.join((*public.split('.')[:2], str(int(public.split('.')[2])+1)))
        with tempfile.TemporaryDirectory(prefix='vui-doc-candidate-') as temporary:
            root=Path(temporary)
            for name in (*CURRENT_INSTALL_GUIDES,'docs/README.md','setup.sh'):
                target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(ROOT/name,target)
            (root/'docs/RELEASE_NOTES.md').write_text('# V-UI '+candidate+'\n')
            check_current_versions(root,candidate)
            guide=root/'README.md';guide.write_text(guide.read_text().replace('--version v'+public,'--version v'+candidate))
            with self.assertRaisesRegex(ValueError,'Current installation version mismatch'):
                check_current_versions(root,candidate)

    def test_historical_guides_do_not_enter_current_version_contract(self):
        self.assertNotIn('docs/DEPLOYMENT_RC2.md', CURRENT_INSTALL_GUIDES)
