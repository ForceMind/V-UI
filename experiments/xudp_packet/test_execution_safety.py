"""Mutation controls for safe continuation, never for packet qualification."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import packet_boundary as p
import verify_execution_safety as v
from test_packet_boundary import records


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.live = patch.object(v, 'validate_live_input')
        self.live_mock = self.live.start(); self.addCleanup(self.live.stop)

    def fixture(self, root):
        cases, rows, processes, binaries = records('B')
        directory = root/'B'; directory.mkdir()
        provenance = root/'provenance.json'; p.save(provenance, {'fixed': 'test-only'})
        binaries['candidate'].update(provenance_path=str(provenance), provenance_sha256=p.sha(provenance))
        source = {'vui_commit': '1'*40, 'vui_tree': '2'*40, 'files': {'fixture': '3'*64}}
        expected = {'source_identity': source, **binaries}
        input_path = root/'input.json'; p.save(input_path, expected)
        identity = {'source_identity': source, **copy.deepcopy(binaries),
                    'manifest_sha256': p.sha(input_path), 'build_provenance': p.json_read(provenance)}
        for case, row in zip(cases, rows):
            row.update(stage='B', protocol=case['protocol'], roles=case['roles'], unexecuted_leaves=[])
        report = {'kind': p.KIND, 'stage': 'B', 'product_qualified': False, 'original_suite_passed': False,
                  'diagnostic_complete': False, 'stage_capacity_passed': False, 'cases': rows}
        execution = {'returncode': 1, 'timeout': False, 'cleanup': {
            'signals': [], 'leader_joined': True, 'process_group_gone': True}}
        return {'root': directory, 'cases': cases, 'rows': rows, 'processes': processes,
                'expected': expected, 'identity': identity, 'report': report,
                'execution': execution, 'manifest_sha256': p.sha(input_path)}

    def seal(self, data):
        root = data['root']
        p.save(root/'plan.json', data['cases']); p.save(root/'input-identity.json', data['identity'])
        p.save(root/'result.json', data['report']); p.save(root/'process-binaries.json', data['processes'])
        (root/'suite.log').write_text('fixture-only\n')
        (root/'frozen-fixture').mkdir(exist_ok=True)
        for row in data['rows']:
            case = row['case']; directory = root/case; directory.mkdir(exist_ok=True)
            p.save(directory/'result.json', row); p.save(directory/'packets.json', row['packets'])
            p.save(root/'frozen-fixture'/(case+'.json'), {'status': 'failed', 'deliveries': [
                {'host': '127.0.0.3', 'count': 0},
                {'host': '127.0.0.3', 'count': row['target_ledger']['received_count']}]})
            (root/'frozen-fixture'/(case+'.log')).write_text('fixture-only\n')
        p.save(root/'manifest.json', {'schema_version': 1, 'kind': p.KIND+'-evidence', 'files': {
            str(path.relative_to(root)): p.sha(path) for path in root.rglob('*')
            if path.is_file() and path.name != 'manifest.json'}})

    def verify(self, data):
        return v.verify(data['root'], 'B', data['execution'], data['expected'], data['manifest_sha256'])

    def test_red_stage_can_be_resource_safe_without_becoming_pass(self):
        with tempfile.TemporaryDirectory() as name:
            data = self.fixture(Path(name)); self.seal(data)
            result = self.verify(data)
            self.assertTrue(result['stage_execution_safe']); self.assertFalse(result['capacity_evaluated'])
            self.live_mock.assert_called_once_with(data['expected'])
            self.assertFalse(data['report']['diagnostic_complete'] or data['report']['stage_capacity_passed'])

    def test_failed_packet_terminal_is_safe_only_for_continuation(self):
        with tempfile.TemporaryDirectory() as name:
            data = self.fixture(Path(name)); row=data['rows'][0]
            row.update(status='error', errors=[{'type': 'TimeoutError', 'message': 'test fixture'}])
            row['packets'][0].update(status='error', error={'type':'TimeoutError','message':'test fixture'})
            self.seal(data)
            self.assertTrue(self.verify(data)['stage_execution_safe'])

    def test_execution_timeout_signal_or_unknown_status_rejected(self):
        changes = [lambda r:r.update(timeout=True), lambda r:r.update(returncode=None),
                   lambda r:r.update(returncode=2), lambda r:r['cleanup'].update(signals=['SIGTERM']),
                   lambda r:r['cleanup'].update(leader_joined=False),
                   lambda r:r['cleanup'].update(process_group_gone=False)]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as name:
                data=self.fixture(Path(name)); change(data['execution']); self.seal(data)
                with self.assertRaises(ValueError): self.verify(data)

    def test_identity_role_and_startup_mutations_rejected(self):
        changes = [lambda d:d['identity']['source_identity'].update(vui_commit='9'*40),
                   lambda d:d['identity'].update(manifest_sha256='9'*64),
                   lambda d:d['processes'][0].update(sha256='9'*64),
                   lambda d:d['processes'][0]['launches'][0].update(ready=False),
                   lambda d:d['processes'].pop(),
                   lambda d:d['processes'].append(copy.deepcopy(d['processes'][0]))]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as name:
                data=self.fixture(Path(name)); data['identity']=copy.deepcopy(data['identity'])
                change(data); self.seal(data)
                with self.assertRaises(ValueError): self.verify(data)

    def test_cleanup_and_pid_birth_mutations_rejected(self):
        changes = [lambda r:r['cleanup'].update(threads_joined=False),
                   lambda r:r['cleanup'].update(fixture_removed=False),
                   lambda r:r['cleanup'].update(application_sockets_closed=False),
                   lambda r:r['cleanup']['processes'][0].update(reaped=False),
                   lambda r:r['cleanup']['processes'][0].update(returncode=-9),
                   lambda r:r['cleanup']['processes'][0].update(returncode=1),
                   lambda r:r['cleanup']['processes'][0].update(pid=99999),
                   lambda r:r['process_identity'][0].update(birth_time=999),
                   lambda r:r['frozen_target_ledgers'][0].update(received=[{}])]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as name:
                data=self.fixture(Path(name)); change(data['rows'][0]); self.seal(data)
                with self.assertRaises(ValueError): self.verify(data)

    def test_target_dns_and_pending_packet_mutations_rejected(self):
        changes = [lambda r:r['target_ledger'].update(thread_joined=False),
                   lambda r:r['target_ledger'].update(socket_closed=False),
                   lambda r:r['target_ledger']['errors'].append('unexpected datagram'),
                   lambda r:r['target_ledger']['actual_datagrams'][0].update(flags=32),
                   lambda r:r['target_ledger']['packets'][0]['request_actual'].update(sha256='9'*64),
                   lambda r:r['target_ledger']['packets'][0].update(sendto_return=0),
                   lambda r:r['dns']['ordinary'].append('unexpected'),
                   lambda r:r['packets'][0].update(status='running'),
                   lambda r:r['packets'][0].update(status='error'),
                   lambda r:r.update(unexecuted_leaves=['false-omission']),
                   lambda r:r['packets'].clear()]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as name:
                data=self.fixture(Path(name)); change(data['rows'][0]); self.seal(data)
                with self.assertRaises(ValueError): self.verify(data)

    def test_live_source_and_binary_validation_is_not_only_historical(self):
        self.live.stop()
        with patch.object(p, 'source_identity', return_value={'current':True}) as source, \
             patch.object(p, 'validate_manifest') as validate:
            v.validate_live_input({'expected':True})
        source.assert_called_once_with(v.b.ROOT, v.b.c)
        validate.assert_called_once_with({'expected':True}, {'current':True}, v.b.ROOT, execute=False)

    def test_missing_case_rejected_even_with_resealed_manifest(self):
        with tempfile.TemporaryDirectory() as name:
            data=self.fixture(Path(name)); data['rows'].pop(); self.seal(data)
            with self.assertRaises(ValueError): self.verify(data)

    def test_missing_changed_extra_or_linked_raw_evidence_rejected(self):
        for change in ('missing', 'changed', 'extra', 'linked'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as name:
                data=self.fixture(Path(name)); self.seal(data); root=data['root']
                if change=='missing': (root/'suite.log').unlink()
                elif change=='changed': (root/'suite.log').write_text('changed\n')
                elif change=='extra': (root/'unexpected').write_text('unexpected\n')
                else: (root/'link').symlink_to(root/'suite.log')
                with self.assertRaises(ValueError): self.verify(data)


if __name__ == '__main__': unittest.main()
