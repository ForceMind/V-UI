"""Pure/fake-process tests; never operate root accounts, services or cgroups."""
import copy
import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

from scripts import low_resource_root_trace as trace
from scripts import low_resource_root as root
from test_low_resource_root import COMMIT, PARENT, complete_report


class RootTraceRoleTests(unittest.TestCase):
    def setUp(self):
        self.root = Path('/var/lib/v-ui')
        self.process = {'pid': 9, 'ppid': 8, 'starttime_ticks': 123,
                        'uid': [998]*4, 'cgroup': '/'+PARENT+'/worker.service'}
        self.runtime = str(self.root/'releases/b/runtime/python/bin/python3')
        self.controller = str(self.root/'.installer-owned/scripts/deploy.py')

    def role(self, argv, process=None):
        return trace.command_role(argv, process or self.process, PARENT, self.root)

    def test_complete_controller_templates_only(self):
        for action, tail in (('stage', ['bundle.zip', '--sha256', 'a'*64]),
                             ('backup', ['archive.zip']), ('activate', ['release-b'])):
            argv = ['/usr/bin/python3', '-B', self.controller, '--root', str(self.root), action, *tail]
            self.assertEqual(self.role(argv), 'controller-'+action)
            self.assertEqual(self.role(argv+['unexpected']), 'unclassified')
            wrong = argv.copy(); wrong[2] = '/tmp/.installer-owned/scripts/deploy.py'
            self.assertEqual(self.role(wrong), 'unclassified')
            wrong = argv.copy(); wrong[0] = '/bin/sh'
            self.assertEqual(self.role(wrong), 'unclassified')

    def test_runtime_command_patterns(self):
        pip = ['-m', 'pip', '--isolated', '--disable-pip-version-check', 'install',
               '--no-index', '--only-binary=:all:', '--require-hashes', '--find-links',
               '/var/lib/v-ui/releases/b/payload/wheels/x86_64-gnu', '-r',
               '/var/lib/v-ui/releases/b/payload/requirements.x86_64-gnu.lock']
        for tail, expected in ((['-m', 'ensurepip', '--upgrade'], 'runtime-ensurepip'),
                (pip, 'runtime-pip-install'), (['-m', 'pip', '--isolated', 'check'], 'runtime-pip-check'),
                (['-B', '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '0',
                  '--no-proxy-headers', '--no-use-colors'], 'candidate-health')):
            self.assertEqual(self.role([self.runtime, *tail]), expected)
            self.assertEqual(self.role(['/usr/bin/python3', *tail]), 'unclassified')
            self.assertEqual(self.role([self.runtime, *tail, '--injected']), 'unclassified')
        pip[-1] = '/outside/private.lock'
        self.assertEqual(self.role([self.runtime, *pip]), 'unclassified')

    def test_service_roles_require_exact_leaf(self):
        for leaf, role in (('v-ui.service', 'managed-panel'), ('v-ui-http01.service', 'managed-http01')):
            self.assertEqual(self.role([], {**self.process, 'cgroup': '/'+PARENT+'/'+leaf}), role)
            self.assertEqual(self.role([], {**self.process, 'cgroup': '/'+PARENT+'/'+leaf+'-other'}), 'unclassified')

    def test_no_argument_or_secret_is_serialized(self):
        with tempfile.TemporaryDirectory() as name:
            proc = Path(name); (proc/'9').mkdir()
            sentinel = 'DO_NOT_PERSIST_CREDENTIAL_123'
            (proc/'9/cmdline').write_bytes(('/usr/bin/python3\0-c\0'+sentinel+'\0').encode())
            row = trace.observed_process(self.process, PARENT, self.root, lambda *_: self.process, proc)
            self.assertEqual(row, {**self.process, 'role': 'unclassified'})
            self.assertNotIn(sentinel, json.dumps(row))
            self.assertNotIn('/usr/bin/python3', json.dumps(row))

    def test_reused_vanished_malformed_and_oversized_process(self):
        with tempfile.TemporaryDirectory() as name:
            proc = Path(name); (proc/'9').mkdir(); cmdline = proc/'9/cmdline'
            cmdline.write_bytes(b'/usr/bin/python3\0')
            self.assertIsNone(trace.observed_process(self.process, PARENT, self.root,
                lambda *_: {**self.process, 'starttime_ticks': 124}, proc))
            cmdline.write_bytes(b'\xff')
            self.assertIsNone(trace.observed_process(self.process, PARENT, self.root, lambda *_: self.process, proc))
            cmdline.write_bytes(b'x'*(trace.MAX_ROW_BYTES+1))
            self.assertEqual(trace.observed_process(self.process, PARENT, self.root, lambda *_: self.process, proc)['role'], 'unclassified')
            cmdline.unlink()
            self.assertIsNone(trace.observed_process(self.process, PARENT, self.root, lambda *_: self.process, proc))


class RootTraceJournalTests(unittest.TestCase):
    def test_incremental_private_owned_journal(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)/'trace'
            journal = trace.Journal(path, {'source_commit': COMMIT})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            journal.append({'type': 'sample', 'sample': {'count': 1}})
            result = trace.read_journal(path, os.getuid())
            self.assertEqual(len(result['rows']), 2)
            self.assertEqual(result['bytes'], journal.byte_count)
            journal.close()
            with self.assertRaises(FileExistsError):
                trace.Journal(path, {})

    def test_incomplete_or_corrupt_tail_retains_complete_prefix(self):
        for suffix in (b'{"type":"sample"', b'not-json\n', b'x'*(trace.MAX_ROW_BYTES+1)+b'\n'):
            with self.subTest(suffix=suffix[:20]), tempfile.TemporaryDirectory() as name:
                path = Path(name)/'trace'; path.write_bytes(b'{"type":"header"}\n'+suffix)
                path.chmod(0o600)
                result = trace.read_journal(path, os.getuid())
                self.assertEqual(result['rows'], [{'type': 'header'}])
                self.assertIsNotNone(result['read_error'])

    def test_sample_and_byte_caps_preserve_room_for_failed_terminal(self):
        for budget in ('samples', 'bytes'):
            with self.subTest(budget=budget), tempfile.TemporaryDirectory() as name:
                path = Path(name)/'trace'; journal = trace.Journal(path, {})
                if budget == 'samples':
                    journal.sample_count = trace.MAX_SAMPLES
                else:
                    journal.byte_count = trace.MAX_BYTES-trace.END_RESERVE_BYTES
                with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
                    journal.append({'type': 'sample', 'sample': {}})
                journal.append({'type': 'finished', 'error': 'RuntimeError'})
                journal.close()
                self.assertEqual(trace.read_journal(path, os.getuid())['rows'][-1]['error'], 'RuntimeError')

    def test_nonfinite_or_oversized_row_refused(self):
        with tempfile.TemporaryDirectory() as name:
            journal = trace.Journal(Path(name)/'trace', {})
            try:
                with self.assertRaises(ValueError):
                    journal.append({'type': 'sample', 'value': float('nan')})
                with self.assertRaises(RuntimeError):
                    journal.append({'type': 'sample', 'value': 'x'*trace.MAX_ROW_BYTES})
                self.assertEqual(journal.sample_count, 0)
            finally:
                journal.close()

    def test_symlinks_hardlinks_fifo_and_oversized_files_refused(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name); target = base/'target'; target.write_bytes(b'{}\n')
            target.chmod(0o600)
            link = base/'link'; link.symlink_to(target)
            with self.assertRaises(OSError):trace.read_journal(link, os.getuid())
            link.unlink(); os.link(target, link)
            with self.assertRaises(RuntimeError):trace.read_journal(link, os.getuid())
            link.unlink(); os.mkfifo(base/'fifo')
            with self.assertRaises(RuntimeError):trace.read_journal(base/'fifo', os.getuid())
            with target.open('wb') as file:file.truncate(trace.MAX_BYTES+1)
            with self.assertRaises(RuntimeError):trace.read_journal(target, os.getuid())

    def test_public_mode_is_refused(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)/'trace'; path.write_text('{}\n'); path.chmod(0o644)
            with self.assertRaises(RuntimeError):trace.read_journal(path, os.getuid())


class RootTraceFailureRetentionTests(unittest.TestCase):
    def test_root_identity_failure_is_never_hidden_as_transition(self):
        with patch.object(root, 'identity', return_value={'uid': [0]*4}), \
                patch.object(root, 'root_state', side_effect=RuntimeError('wrong effective slice')):
            with self.assertRaisesRegex(RuntimeError, 'wrong effective slice'):
                trace.capture(PARENT)

    def test_timeout_and_failed_command_keep_samples_and_terminal(self):
        from types import SimpleNamespace
        for outcome in ('timeout', 'nonzero'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as name:
                gate = object.__new__(root.Gate); gate.parent = PARENT
                gate.output = Path(name)/'worker.json'
                gate.config = {'source_commit': COMMIT, 'bundle_b': 'b.zip', 'package': {
                    'a_release_id': 'a', 'b_release_id': 'a-root-fixture', 'b_sha256': 'b'*64}}
                state = {'current': {'release_id': 'a'}, 'releases': ['a'], 'ready_releases': ['a'],
                         'services': {'v-ui.service': {'process': {'pid': 10}}}}
                sample = {'state': state, 'state_monotonic': 1}
                gate.operation = lambda action: copy.deepcopy(sample if action == 'accounting' else state)
                gate.record = lambda *_: None
                gate.installer_receipt = lambda *_: None
                result = SimpleNamespace(returncode=1, stdout='', stderr='synthetic failure')
                with patch.object(root, 'run', side_effect=subprocess.TimeoutExpired('fixture', 240)
                        if outcome == 'timeout' else None, return_value=result):
                    with self.assertRaises(subprocess.TimeoutExpired if outcome == 'timeout' else AssertionError):
                        gate.finish(self, ['--bundle', 'a.zip', '--sha256', 'a'*64], None, None)
                rows = trace.read_journal(Path(name)/'root-upgrade.jsonl', os.getuid())['rows']
                self.assertEqual([row['type'] for row in rows], ['header', 'sample', 'sample', 'finished'])
                self.assertEqual(rows[-1]['error'], 'TimeoutExpired' if outcome == 'timeout' else None)
                self.assertEqual(rows[-1]['returncode'], None if outcome == 'timeout' else 1)


class RootTraceValidatorTests(unittest.TestCase):
    def test_valid_complete_and_mixed_uid_observations(self):
        report = complete_report()
        trace.validate(report)
        sample = report['upgrade_accounting']['rows'][1]['sample']
        sample['processes'].append({**sample['processes'][0], 'pid': 99, 'uid': [1001, 0, 0, 0]})
        trace.validate(report)

    def test_corrupt_or_incomplete_trace_is_not_success(self):
        def sample(report):return report['upgrade_accounting']['rows'][1]['sample']
        mutations = [
            lambda r: r.pop('upgrade_accounting'),
            lambda r: r['upgrade_accounting'].update(read_error='truncated'),
            lambda r: r['upgrade_accounting']['rows'].pop(),
            lambda r: r['upgrade_accounting']['rows'][-1].update(error='TimeoutExpired'),
            lambda r: r['upgrade_accounting']['rows'][-1].update(sample_count=0),
            lambda r: r['upgrade_accounting']['rows'][-1].update(returncode=1),
            lambda r: r['upgrade_accounting']['rows'][0].update(source_commit='c'*40),
            lambda r: sample(r).update(parent_identity={'device': 99, 'inode': 99}),
            lambda r: sample(r).update(limits={}),
            lambda r: sample(r).update(started_monotonic=float('nan')),
            lambda r: sample(r).update(metrics_started_monotonic=4.9),
            lambda r: sample(r).update(state_monotonic=99),
            lambda r: sample(r).update(observer_self_cpu_seconds=float('nan')),
            lambda r: sample(r)['metrics'].update({'memory.peak': 0}),
            lambda r: sample(r)['metrics']['memory.events'].update(oom=1),
            lambda r: sample(r)['observer'].update(uid=[1001, 0, 0, 0]),
            lambda r: sample(r)['processes'][0].update(role='unbounded-new-role'),
            lambda r: sample(r).update(processes=[]),
            lambda r: sample(r)['processes'].append(copy.deepcopy(sample(r)['processes'][0])),
            lambda r: sample(r).update(state_error='RuntimeError'),
            lambda r: r['upgrade_accounting']['rows'][2]['sample'].update(state=None, state_error='ServiceUnavailable'),
            lambda r: sample(r)['processes'][0].update(cgroup='/system.slice/escape.service'),
            lambda r: sample(r)['processes'][0].update(role='managed-panel'),
            lambda r: r['upgrade_accounting']['rows'][-1].update(command_started_monotonic=1),
            lambda r: r['upgrade_accounting']['rows'][-1].update(command_finished_monotonic=99),
        ]
        for mutate in mutations:
            report = complete_report(); mutate(report)
            with self.subTest(mutate=mutate), self.assertRaises(RuntimeError):
                trace.validate(report)

    def test_parent_event_envelope_and_sample_gap_are_required(self):
        report = complete_report()
        for row in report['upgrade_accounting']['rows'][1:-1]:
            row['sample']['metrics']['memory.events']['max'] = 1
        with self.assertRaisesRegex(RuntimeError, 'exceeds enclosing'):
            trace.validate(report)
        report = complete_report()
        report['worker']['stages'][3]['monotonic'] = -10
        with self.assertRaisesRegex(RuntimeError, 'gap/order/window'):
            trace.validate(report)


if __name__ == '__main__':
    unittest.main()
