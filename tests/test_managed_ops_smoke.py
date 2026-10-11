"""Fast checks for the package consumer's evidence and fixture boundaries.

These do not replace the real target bundle runs in the portable/deployment
gates, and do not claim a root installation or real service-manager acceptance.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.managed_ops_smoke import (_audit_trace, _copy_control, _database_values,
    _seed_fake_data, _write_drivers, FIXTURE_VERSION, HARNESS_VERSION, WRAPPER_BYTES,
    SUCCESS_ACTIONS)


def evidence():
    result = []
    def event(kind, **values):
        result.append({'kind': kind, 'uid': 1000, **values})
    for action in SUCCESS_ACTIONS:
        event('send', action=action, value={'protocol': 1, 'action': action})
        event('worker_started')
        event('receive', action=action, value={'event': 'prepared'})
        if action not in ('stage', 'start'):
            for unit in ('v-ui-http01.socket', 'v-ui.service', 'v-ui-http01.service'):
                event('fake_service', arguments=['stop', unit])
        event('send', action=action, value={'command': 'proceed'})
        event('receive', action=action, value={'event': 'done', 'ok': True})
        if action != 'stage':
            event('send', action=action, value={'command': 'release_writers'})
            event('receive', action=action, value={'event': 'writers_released'})
            if action not in ('stop', 'recover-restore'):
                for unit in ('v-ui-http01.socket', 'v-ui.service'):
                    event('fake_service', arguments=['start', unit])
        event('send', action=action, value={'command': 'finish'})
        event('exit_zero', action=action)
        event('closed', action=action)
    return result


class ManagedConsumerFixtureTests(unittest.TestCase):
    def test_evidence_versions_explicitly_separate_fixture_and_product(self):
        self.assertEqual(HARNESS_VERSION, 'managed-package-consumer-v1')
        self.assertIn('simulated-root-trust-fake-services', FIXTURE_VERSION)

    def test_generated_drivers_compile_without_production_environment_hooks(self):
        with tempfile.TemporaryDirectory(prefix='vui-managed-driver-unit-') as temporary:
            root = Path(temporary)
            driver = _write_drivers(root, root / 'instance', root / 'python', root / 'config.json')
            for path in (driver, root / 'fixture_worker_driver.py'):
                compile(path.read_text(), str(path), 'exec')
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            worker = (root / 'fixture_worker_driver.py').read_text()
            self.assertIn("worker['_run'](Path(ROOT),api", worker)
            self.assertIn("worker['_load_release_tools']()", worker)
            self.assertIn('os.getuid() != 0 and os.geteuid() != 0', worker)
            self.assertNotIn('os.environ', worker)

    def test_control_copy_requires_manifest_hashes_and_exact_unexecuted_wrapper(self):
        with tempfile.TemporaryDirectory(prefix='vui-managed-copy-unit-') as temporary:
            root = Path(temporary); payload = root / 'payload'; control = root / 'control'
            payload.mkdir(); control.mkdir(); (payload / 'app').mkdir(); (payload / 'deploy').mkdir()
            files = {}
            for name in ('deploy/system_ops.py', 'deploy/ops_worker.py', 'app/release_tools.py', 'deploy/v-ui'):
                raw = WRAPPER_BYTES if name.endswith('/v-ui') else b'# unit fixture, not product code\n'
                (payload / name).write_bytes(raw)
                files[name] = {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            (payload / 'MANIFEST.json').write_text(json.dumps({'files': files}))
            copied = _copy_control(payload, control)
            self.assertEqual(set(copied), set(files))
            self.assertEqual((control / 'v-ui').read_bytes(), WRAPPER_BYTES)
            self.assertFalse(os.access(control / 'v-ui', os.X_OK))
            (payload / 'deploy/ops_worker.py').write_bytes(b'# tampered\n')
            with self.assertRaises(AssertionError): _copy_control(payload, control)

    def test_sqlite_fixture_has_recoverable_nodes_sessions_and_grants(self):
        with tempfile.TemporaryDirectory(prefix='vui-managed-db-unit-') as temporary:
            data = Path(temporary) / 'data'; _seed_fake_data(data)
            self.assertEqual(_database_values(data), {'nodes': [('original-fake-node',)],
                'sessions': [('synthetic-session-digest',)], 'grants': [(0,)]})
            with sqlite3.connect(data / 'v-ui.db') as db:
                db.execute('DELETE FROM admin_sessions')
                db.execute('UPDATE subscription_grants SET revoked=1'); db.commit()
            self.assertEqual(_database_values(data)['sessions'], [])
            self.assertEqual(_database_values(data)['grants'], [(1,)])

    def test_trace_requires_every_successful_action_and_full_handshake(self):
        self.assertEqual(len(_audit_trace(evidence())), len(SUCCESS_ACTIONS))
        events = evidence()
        events = [item for item in events if item.get('value') != {'event': 'writers_released'}]
        with self.assertRaises(AssertionError): _audit_trace(events)

    def test_trace_rejects_claimed_root_worker(self):
        events = evidence(); events[1]['uid'] = 0
        with self.assertRaises(AssertionError): _audit_trace(events)

    def test_trace_rejects_wrong_stop_order_or_unrelated_service(self):
        for change in ('order', 'target'):
            with self.subTest(change=change):
                events = evidence()
                first = next(i for i, item in enumerate(events) if item['kind'] == 'fake_service')
                if change == 'order': events[first], events[first + 1] = events[first + 1], events[first]
                else: events[first]['arguments'] = ['stop', 'ssh.service']
                with self.assertRaises(AssertionError): _audit_trace(events)

    def test_trace_rejects_stage_service_mutation_or_unreaped_worker(self):
        events = evidence()
        events.insert(2, {'kind': 'fake_service', 'uid': 1000, 'arguments': ['stop', 'v-ui.service']})
        with self.assertRaises(AssertionError): _audit_trace(events)
        with self.assertRaises(AssertionError): _audit_trace(evidence()[:-1])

    def test_trace_rejects_early_stop_restart_or_missing_real_worker(self):
        for kind in ('stop', 'start', 'worker'):
            with self.subTest(kind=kind):
                events = evidence()
                if kind == 'worker':
                    events = [event for event in events if event['kind'] != 'worker_started']
                else:
                    selected = next(index for index, event in enumerate(events)
                        if event['kind'] == 'fake_service' and event['arguments'][0] == kind)
                    event = events.pop(selected)
                    prepared = next(index for index, event in enumerate(events)
                        if event.get('action') == 'backup' and event.get('value') == {'event': 'prepared'})
                    events.insert(prepared, event)
                with self.assertRaises(AssertionError): _audit_trace(events)

    def test_failed_stage_is_not_counted_as_a_success(self):
        events = [
            {'kind': 'send', 'uid': 1000, 'action': 'stage', 'value': {'action': 'stage', 'protocol': 1}},
            {'kind': 'worker_started', 'uid': 1000},
            {'kind': 'receive', 'uid': 1000, 'action': 'stage', 'value': {'event': 'error'}},
            {'kind': 'closed', 'uid': 1000, 'action': 'stage'}, *evidence()]
        transactions = _audit_trace(events)
        self.assertEqual(len(transactions), len(SUCCESS_ACTIONS) + 1)
        self.assertEqual(transactions[0]['events'], ['event:error'])


if __name__ == '__main__':
    unittest.main()
