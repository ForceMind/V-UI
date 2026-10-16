from pathlib import Path
import tempfile
import unittest

from prepare_evidence import allowed_names, stage


class EvidenceIsolationTests(unittest.TestCase):
    def test_three_lifecycle_arms_allow_only_the_exact_text_matrix(self):
        allowed = allowed_names()
        for phase in ('baseline', 'patched', 'lifecycle'):
            prefix = 'lifecycle-' + phase + '/'
            names = {name[len(prefix):] for name in allowed['logs'] if name.startswith(prefix)}
            self.assertEqual(len(names), 115)
            self.assertEqual(sum(name.startswith('cases/') for name in names), 70)
            self.assertEqual(sum(name.startswith('delivery-ledgers/') for name in names), 35)
            self.assertEqual(sum(name.startswith('observations/') for name in names), 6)
            self.assertTrue(all(Path(name).suffix in ('.json', '.log') for name in names))
            for name in ('config.json', 'sing-box', 'lifecycle.patch', 'observations/unknown.json'):
                self.assertNotIn(prefix + name, allowed['logs'])
            self.assertIn(phase + '-lifecycle-provenance.json', allowed['source'])
            self.assertIn(phase + '-route-lifecycle-tests.jsonl', allowed['source'])
        self.assertNotIn('lifecycle-provenance.json', allowed['source'])
        self.assertNotIn('lifecycle/provenance.json', allowed['source'])

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

    def test_observations_have_an_exact_text_only_allowlist(self):
        allowed = allowed_names()
        expected = {
            "observations/result.json", "observations/process-binaries.json",
            "observations/suite.log", "observations/runner-error.log",
            "observations/observations/domain-freeze.json", "observations/observations/idle-sockets.json",
            "observations/cases/observe_domain_freeze.json", "observations/cases/observe_domain_freeze.log",
            "observations/cases/observe_idle_sockets.json", "observations/cases/observe_idle_sockets.log",
            "observations/delivery-ledgers/observe_domain_freeze.json",
            "observations/delivery-ledgers/observe_idle_sockets.json",
        }
        self.assertEqual({x for x in allowed["logs"] if x.startswith("observations/")}, expected)
        for name in ("observations/sing-box", "observations/config.json", "observations/unexpected.log"):
            self.assertNotIn(name, allowed["logs"])
        self.assertIn("timeout-observation-verification.json", allowed["source"])

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
