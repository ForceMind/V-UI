from pathlib import Path
import tempfile
import unittest

from prepare_evidence import stage


class EvidenceIsolationTests(unittest.TestCase):
    def test_allowlisted_text_is_hashed_and_fixture_values_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logs = root / 'logs'
            logs.mkdir()
            (logs / 'build.log').write_text('fixture=11111111-1111-4111-8111-111111111111 /tmp/vui-d2-abcdef\n')
            rows = stage(logs, root / 'missing-source', root / 'missing-official', root / 'out')
            self.assertEqual(len(rows), 1)
            self.assertNotEqual(rows[0]['original_sha256'], rows[0]['staged_sha256'])
            self.assertIn('<fixture>', (root / 'out/logs/build.log').read_text())

    def test_unknown_binary_secret_symlink_and_bad_json_do_not_create_output(self):
        for mode in ('unknown', 'binary', 'secret', 'symlink', 'json'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                logs = root / 'logs'
                logs.mkdir()
                path = logs / ('unexpected.key' if mode == 'unknown' else 'build.log')
                path.write_text('safe')
                if mode == 'binary': path.write_bytes(b'\x7fELF\x00')
                if mode == 'secret': path.write_text('-----BEGIN PRIVATE KEY-----\nfixture-but-never-upload\n')
                if mode == 'symlink':
                    path.unlink()
                    path.symlink_to(root / 'target')
                if mode == 'json':
                    path.unlink()
                    (logs / 'baseline').mkdir()
                    (logs / 'baseline/result.json').write_text('not json')
                with self.assertRaises(ValueError): stage(logs, root / 'no-source', root / 'no-official', root / 'out')
                self.assertFalse((root / 'out').exists())

    def test_official_binaries_are_never_selected_and_previous_evidence_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cores = root / 'official/cores'
            cores.mkdir(parents=True)
            (cores / 'sing-box').write_bytes(b'\x7fELF\x00')
            (cores / 'provenance.json').write_text('{"public": true}')
            rows = stage(root / 'no-logs', root / 'no-source', root / 'official', root / 'out')
            self.assertEqual([row['path'] for row in rows], ['official/cores/provenance.json'])
            with self.assertRaises(ValueError): stage(root / 'no-logs', root / 'no-source', root / 'official', root / 'out')


if __name__ == '__main__':
    unittest.main()
