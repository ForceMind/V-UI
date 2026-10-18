"""Fast checks for the package consumer's evidence and fixture boundaries.

These do not replace the real target bundle runs in the portable/deployment
gates, and do not claim a root installation or real service-manager acceptance.
"""
import ast
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.managed_ops_smoke import (_audit_released_writer_leases, _audit_trace,
    _copy_control, _database_values,
    _seed_fake_data, _write_drivers, FIXTURE_VERSION, HARNESS_VERSION, WRAPPER_BYTES,
    SUCCESS_ACTIONS, WRITER_LOCKS)


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


class ManagedReleasedLeaseInventoryTests(unittest.TestCase):
    """Private fake generations; real filesystem metadata and flock checks."""

    EXPECTED_COUNTS = {'generations': 3, 'component_locks': 9, 'stable_siblings': 2}

    @contextmanager
    def layout(self):
        with tempfile.TemporaryDirectory(prefix='vui-managed-leases-unit-') as temporary:
            root = Path(temporary) / 'instance'
            root.mkdir(mode=0o700)
            recovery = root / 'recovery'; recovery.mkdir(mode=0o700)
            retained = recovery / ('before-' + '1' * 32)
            interrupted = recovery / ('interrupted-' + '2' * 32)
            pending_old = recovery / ('before-' + '3' * 32)
            for generation in (root / 'data', retained, interrupted):
                generation.mkdir(mode=0o700)
                for relative in WRITER_LOCKS:
                    lock = generation / relative
                    lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    lock.touch(mode=0o600)
            for lease in self.stable_leases(root, pending_old):
                lease.touch(mode=0o600)
            yield root, retained, interrupted, pending_old

    @staticmethod
    def stable_leases(root, pending_old):
        return root / '.data.owner.lease', pending_old.with_name('.' + pending_old.name + '.owner.lease')

    @staticmethod
    def generations(root, retained, interrupted, pending_old):
        return root / 'data', retained, interrupted

    def test_exact_inventory_preserves_consumed_generation_stable_sibling(self):
        with self.layout() as layout:
            root, retained, interrupted, pending_old = layout
            leases = self.stable_leases(root, pending_old)
            before = {path: path.stat() for path in leases}
            self.assertFalse(pending_old.exists())
            self.assertEqual(set((root / 'recovery').iterdir()), {retained, interrupted, leases[1]})
            for _ in range(2):
                self.assertEqual(_audit_released_writer_leases(*layout), self.EXPECTED_COUNTS)
            self.assertEqual({path: path.stat() for path in leases}, before)
            self.assertFalse(pending_old.exists(), 'Audit must not recreate the consumed generation')

    def test_every_expected_generation_is_mandatory(self):
        for index in range(3):
            with self.subTest(generation=index), self.layout() as layout:
                generation = self.generations(*layout)[index]
                shutil.rmtree(generation)
                with self.assertRaises(AssertionError):
                    _audit_released_writer_leases(*layout)
                self.assertFalse(generation.exists(), 'Missing generations must not be repaired or skipped')

    def test_every_component_lock_is_mandatory_in_every_generation(self):
        for index in range(3):
            for relative in WRITER_LOCKS:
                with self.subTest(generation=index, lock=relative), self.layout() as layout:
                    lock = self.generations(*layout)[index] / relative
                    lock.unlink()
                    with self.assertRaises(AssertionError):
                        _audit_released_writer_leases(*layout)
                    self.assertFalse(lock.exists(), 'Audit must not create a missing writer lock')

    def test_every_writer_ancestor_directory_is_mandatory(self):
        for index in range(3):
            for relative in ('runtime', 'runtime/xray', 'runtime/sing-box', 'certificates'):
                with self.subTest(generation=index, directory=relative), self.layout() as layout:
                    directory = self.generations(*layout)[index] / relative
                    shutil.rmtree(directory)
                    with self.assertRaises(AssertionError):
                        _audit_released_writer_leases(*layout)
                    self.assertFalse(directory.exists())

    def test_both_stable_siblings_are_mandatory(self):
        for index in range(2):
            with self.subTest(lease=index), self.layout() as layout:
                root, _, _, pending_old = layout
                lease = self.stable_leases(root, pending_old)[index]
                lease.unlink()
                with self.assertRaises(AssertionError):
                    _audit_released_writer_leases(*layout)
                self.assertFalse(lease.exists(), 'Audit must not create a missing stable lease')

    def test_every_held_component_lock_is_rejected(self):
        for index in range(3):
            for relative in WRITER_LOCKS:
                for kind in (fcntl.LOCK_EX, fcntl.LOCK_SH):
                    with self.subTest(generation=index, lock=relative, kind=kind), self.layout() as layout:
                        lock = self.generations(*layout)[index] / relative
                        with lock.open('r+b') as held:
                            fcntl.flock(held, kind | fcntl.LOCK_NB)
                            with self.assertRaises(AssertionError):
                                _audit_released_writer_leases(*layout)
                        self.assertEqual(_audit_released_writer_leases(*layout), self.EXPECTED_COUNTS)

    def test_both_held_stable_siblings_are_rejected(self):
        for index in range(2):
            for kind in (fcntl.LOCK_EX, fcntl.LOCK_SH):
                with self.subTest(lease=index, kind=kind), self.layout() as layout:
                    root, _, _, pending_old = layout
                    lease = self.stable_leases(root, pending_old)[index]
                    with lease.open('r+b') as held:
                        fcntl.flock(held, kind | fcntl.LOCK_NB)
                        with self.assertRaises(AssertionError):
                            _audit_released_writer_leases(*layout)
                    self.assertEqual(_audit_released_writer_leases(*layout), self.EXPECTED_COUNTS)

    def test_unknown_recovery_entries_are_not_filtered_out(self):
        entries = [('unexplained-file', 'file'), ('unexplained-directory', 'directory'),
            ('before-' + '4' * 32, 'directory'), ('interrupted-' + '4' * 32, 'directory'),
            ('.before-' + '4' * 32 + '.owner.lease', 'file'),
            ('.before-' + '1' * 32 + '.owner.lease', 'file'),
            ('.interrupted-' + '2' * 32 + '.owner.lease', 'file'),
            ('unexplained-dangling-link', 'symlink')]
        for name, kind in entries:
            with self.subTest(name=name, kind=kind), self.layout() as layout:
                root = layout[0]
                extra = root / 'recovery' / name
                if kind == 'directory': extra.mkdir(mode=0o700)
                elif kind == 'symlink': extra.symlink_to(root.parent / 'missing-target')
                else: extra.touch(mode=0o600)
                with self.assertRaises(AssertionError):
                    _audit_released_writer_leases(*layout)

    def test_symlink_generations_are_rejected_even_when_the_target_is_complete(self):
        for index in range(3):
            for dangling in (False, True):
                with self.subTest(generation=index, dangling=dangling), self.layout() as layout:
                    generation = self.generations(*layout)[index]
                    target = layout[0].parent / 'redirected-generation'
                    if dangling: shutil.rmtree(generation)
                    else: generation.rename(target)
                    generation.symlink_to(target, target_is_directory=True)
                    with self.assertRaises(AssertionError):
                        _audit_released_writer_leases(*layout)

    def test_symlink_component_locks_are_rejected(self):
        for index in range(3):
            for relative in WRITER_LOCKS:
                for dangling in (False, True):
                    with self.subTest(generation=index, lock=relative, dangling=dangling), self.layout() as layout:
                        lock = self.generations(*layout)[index] / relative
                        target = layout[0].parent / 'redirected-lock'
                        if dangling: lock.unlink()
                        else: lock.rename(target)
                        lock.symlink_to(target)
                        with self.assertRaises(AssertionError):
                            _audit_released_writer_leases(*layout)

    def test_symlink_writer_ancestor_directories_are_rejected(self):
        for index in range(3):
            for relative in ('runtime', 'runtime/xray', 'runtime/sing-box', 'certificates'):
                with self.subTest(generation=index, directory=relative), self.layout() as layout:
                    directory = self.generations(*layout)[index] / relative
                    target = layout[0].parent / 'redirected-writer-directory'
                    directory.rename(target)
                    directory.symlink_to(target, target_is_directory=True)
                    with self.assertRaises(AssertionError):
                        _audit_released_writer_leases(*layout)

    def test_symlink_stable_siblings_are_rejected(self):
        for index in range(2):
            for dangling in (False, True):
                with self.subTest(lease=index, dangling=dangling), self.layout() as layout:
                    root, _, _, pending_old = layout
                    lease = self.stable_leases(root, pending_old)[index]
                    target = root.parent / 'redirected-stable-lease'
                    if dangling: lease.unlink()
                    else: lease.rename(target)
                    lease.symlink_to(target)
                    with self.assertRaises(AssertionError):
                        _audit_released_writer_leases(*layout)

    def test_non_regular_component_locks_are_rejected_without_opening_them(self):
        for index in range(3):
            for relative in WRITER_LOCKS:
                for kind in ('directory', 'fifo'):
                    with self.subTest(generation=index, lock=relative, kind=kind), self.layout() as layout:
                        lock = self.generations(*layout)[index] / relative
                        lock.unlink()
                        if kind == 'directory': lock.mkdir(mode=0o700)
                        else: os.mkfifo(lock, mode=0o600)
                        with self.assertRaises(AssertionError):
                            _audit_released_writer_leases(*layout)

    def test_non_regular_stable_siblings_are_rejected_without_opening_them(self):
        for index in range(2):
            for kind in ('directory', 'fifo'):
                with self.subTest(lease=index, kind=kind), self.layout() as layout:
                    root, _, _, pending_old = layout
                    lease = self.stable_leases(root, pending_old)[index]
                    lease.unlink()
                    if kind == 'directory': lease.mkdir(mode=0o700)
                    else: os.mkfifo(lease, mode=0o600)
                    with self.assertRaises(AssertionError):
                        _audit_released_writer_leases(*layout)

    def test_foreign_owned_stable_siblings_are_rejected(self):
        actual_lstat = Path.lstat
        for index in range(2):
            with self.subTest(lease=index), self.layout() as layout:
                root, _, _, pending_old = layout
                lease = self.stable_leases(root, pending_old)[index]
                def foreign_owner(path):
                    info = actual_lstat(path)
                    if path == lease:
                        values = list(info); values[4] = info.st_uid + 1001
                        return os.stat_result(values)
                    return info
                with patch.object(Path, 'lstat', autospec=True, side_effect=foreign_owner):
                    with self.assertRaises(AssertionError):
                        _audit_released_writer_leases(*layout)

    def test_unsafe_stable_sibling_modes_are_rejected_without_repair(self):
        for index in range(2):
            for mode in (0o644, 0o660, 0o400, 0o700, 0o1600):
                with self.subTest(lease=index, mode=oct(mode)), self.layout() as layout:
                    root, _, _, pending_old = layout
                    lease = self.stable_leases(root, pending_old)[index]
                    lease.chmod(mode)
                    with self.assertRaises(AssertionError):
                        _audit_released_writer_leases(*layout)
                    self.assertEqual(lease.stat().st_mode & 0o7777, mode)

    def test_nonempty_stable_siblings_are_rejected_without_truncation(self):
        for index in range(2):
            with self.subTest(lease=index), self.layout() as layout:
                root, _, _, pending_old = layout
                lease = self.stable_leases(root, pending_old)[index]
                lease.write_bytes(b'not a stable lease\n')
                with self.assertRaises(AssertionError):
                    _audit_released_writer_leases(*layout)
                self.assertEqual(lease.read_bytes(), b'not a stable lease\n')

    def test_hardlinked_stable_siblings_are_rejected(self):
        for index in range(2):
            with self.subTest(lease=index), self.layout() as layout:
                root, _, _, pending_old = layout
                lease = self.stable_leases(root, pending_old)[index]
                alias = root.parent / 'extra-stable-link'
                os.link(lease, alias)
                with self.assertRaises(AssertionError):
                    _audit_released_writer_leases(*layout)
                self.assertEqual(lease.stat().st_nlink, 2)
                self.assertEqual(lease.stat().st_ino, alias.stat().st_ino)

    def test_consumed_predecessor_and_restore_journal_must_really_be_absent(self):
        for name in ('pending_old', 'journal'):
            for kind in ('file', 'directory', 'dangling-symlink'):
                with self.subTest(path=name, kind=kind), self.layout() as layout:
                    root, _, _, pending_old = layout
                    unexpected = pending_old if name == 'pending_old' else root / 'RESTORE_PENDING.json'
                    if kind == 'directory': unexpected.mkdir(mode=0o700)
                    elif kind == 'dangling-symlink': unexpected.symlink_to(root.parent / 'missing-target')
                    else: unexpected.write_text('{}')
                    with self.assertRaises(AssertionError):
                        _audit_released_writer_leases(*layout)


class ManagedConsumerFixtureTests(unittest.TestCase):
    def fake_services(self):
        """Run the actual emitted fixture backend with the product state parser."""
        source = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('managed_fixture_product_ops', source / 'deploy/system_ops.py')
        ops = importlib.util.module_from_spec(spec); spec.loader.exec_module(ops)
        with tempfile.TemporaryDirectory(prefix='vui-managed-state-unit-') as temporary:
            root = Path(temporary)
            driver = _write_drivers(root, root / 'instance', root / 'python', root / 'config.json')
            fixture_class = next(item for item in ast.parse(driver.read_text()).body
                if isinstance(item, ast.ClassDef) and item.name == 'FakeServices')
        states = {unit: 'inactive' if unit == 'v-ui-http01.service' else 'active' for unit in ops.SYSTEMD_UNITS}
        namespace = {'ops': ops, 'fake_states': states, 'record': lambda *args, **kwargs: None,
            'locks': lambda *args: self.fail('State-only unit must not mutate services')}
        exec(compile(ast.Module(body=[fixture_class], type_ignores=[]), '<actual fixture backend>', 'exec'), namespace)
        return ops, namespace['FakeServices']('systemd')

    def test_evidence_versions_explicitly_separate_fixture_and_product(self):
        self.assertEqual(HARNESS_VERSION, 'managed-package-consumer-v1')
        self.assertIn('simulated-root-trust-fake-services', FIXTURE_VERSION)

    def test_fake_socket_has_real_exec_cgroup_properties_and_active_substates(self):
        ops, services = self.fake_services()
        unit = 'v-ui-http01.socket'
        command = ['show', '--no-pager', '--all', '--property=' + ','.join(ops.PROPERTIES), unit]
        for responder, substate in (('inactive', 'listening'), ('active', 'running')):
            with self.subTest(responder=responder):
                services.states['v-ui-http01.service'] = responder
                code, output = services._run(command)
                fields = dict(line.split('=', 1) for line in output.decode().splitlines())
                self.assertEqual(code, 0)
                self.assertEqual(fields['KillMode'], 'control-group')
                self.assertEqual(fields['ControlPID'], '0')
                self.assertEqual(fields['ControlGroup'], '/system.slice/' + unit)
                self.assertEqual(fields['User'], '')
                self.assertEqual(fields['Group'], '')
                self.assertNotIn('MainPID', fields)
                self.assertEqual(fields['SubState'], substate)
                self.assertEqual(services.state(unit), 'active')
        with patch.object(ops, 'cgroup_empty', return_value=True) as empty:
            services.states.update({unit: 'inactive' for unit in ops.SYSTEMD_UNITS})
            services.prove_stopped()
            self.assertEqual([call.args[0] for call in empty.call_args_list], list(ops.SYSTEMD_UNITS))

    def test_fake_socket_contract_does_not_hide_unsafe_or_unknown_states(self):
        ops, services = self.fake_services()
        original = services._run
        for field, value in (('ControlPID', '7'), ('KillMode', ''), ('ControlGroup', '/system.slice/ssh.service'),
                             ('SubState', 'start-pre'), ('User', 'v-ui')):
            with self.subTest(field=field, value=value):
                def response(arguments, **kwargs):
                    code, raw = original(arguments, **kwargs)
                    fields = dict(line.split('=', 1) for line in raw.decode().splitlines())
                    fields[field] = value
                    return code, ('\n'.join(name + '=' + item for name, item in fields.items()) + '\n').encode()
                with patch.object(services, '_run', side_effect=response):
                    with self.assertRaises(ops.Refused): services.state('v-ui-http01.socket')

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
            payload.mkdir(); control.mkdir(); (payload / 'app/services').mkdir(parents=True); (payload / 'deploy').mkdir()
            files = {}
            for name in ('deploy/system_ops.py', 'deploy/ops_worker.py', 'app/release_tools.py', 'deploy/v-ui',
                         'app/data_owner.py', 'app/services/proxy_store.py', 'app/services/upstream_profile.py',
                         'app/services/inbound_policy.py'):
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
