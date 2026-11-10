"""Managed control tests: temporary data, fake services, and non-root child PTYs.

No test invokes a real system service command, installs a kit, or changes users.
"""
from contextlib import ExitStack, contextmanager, redirect_stdout, nullcontext
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from scripts.ops_menu_smoke import MenuSession

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('system_ops_test_module', SOURCE / 'deploy/system_ops.py')
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)
ACCOUNT = types.SimpleNamespace(pw_uid=65534, pw_gid=65534)


@contextmanager
def plain_directory(path, **kwargs):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


class Terminal(io.StringIO):
    def fileno(self):
        return 999
    def isatty(self):
        return True


class TrustTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-root-control-')))

    def test_ownership_modes_and_lock_parent_exception_are_exact(self):
        for owner, mode, directory, sticky, valid in (
            (0, stat.S_IFREG | 0o644, False, False, True),
            (1, stat.S_IFREG | 0o644, False, False, False),
            (0, stat.S_IFREG | 0o666, False, False, False),
            (0, stat.S_IFDIR | 0o755, True, False, True),
            (0, stat.S_IFDIR | 0o1777, True, True, True),
            (0, stat.S_IFDIR | 0o777, True, True, False),
            (0, stat.S_IFDIR | 0o1777, True, False, False),
            (1, stat.S_IFDIR | 0o1777, True, True, False),
            (0, stat.S_IFREG | 0o1777, True, True, False),
        ):
            with self.subTest(owner=owner, mode=oct(mode), sticky=sticky):
                info = types.SimpleNamespace(st_uid=owner, st_mode=mode)
                if valid:
                    ops._root_owned(info, directory, sticky)
                else:
                    with self.assertRaises(ops.Refused):
                        ops._root_owned(info, directory, sticky)

    def test_trusted_walk_rejects_leaf_and_ancestor_symlinks(self):
        parent = self.temp / 'parent'
        parent.mkdir()
        (parent / 'file').write_bytes(b'fixture')
        (self.temp / 'link').symlink_to(parent, target_is_directory=True)
        (parent / 'leaf').symlink_to(parent / 'file')
        with patch.object(ops, '_root_owned'):
            self.assertEqual(ops.trusted_bytes(parent / 'file'), b'fixture')
            for name in (self.temp / 'link/file', parent / 'leaf'):
                with self.subTest(name=name), self.assertRaises(OSError):
                    ops.trusted_bytes(name)

    def test_bounded_regular_single_link_files_only(self):
        target = self.temp / 'file'
        target.write_bytes(b'abc')
        with patch.object(ops, '_root_owned'):
            self.assertEqual(ops.trusted_bytes(target, 3), b'abc')
            with self.assertRaises(ops.Refused):
                ops.trusted_bytes(target, 2)
            os.link(target, self.temp / 'hardlink')
            with self.assertRaises(ops.Refused):
                ops.trusted_bytes(target)
            fifo = self.temp / 'fifo'
            os.mkfifo(fifo)
            with self.assertRaises(ops.Refused):
                ops.trusted_bytes(fifo)
            with self.assertRaises(ops.Refused):
                ops.trusted_bytes(self.temp)

    def test_json_duplicate_nonobject_nan_and_malformed_rejected(self):
        for raw in (b'{"a":1,"a":2}', b'[]', b'{"a":NaN}', b'null', b'{', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(ops.Refused):
                ops.decode_json(raw)

    def test_paths_are_literal_canonical_absolute(self):
        for value in ('relative', '/etc//v-ui/a', '/etc/../tmp/a', '/etc/./a', '/etc/a\n', '/'):
            with self.subTest(value=value), self.assertRaises(ops.Refused):
                ops._parts(value)

    def receipt(self):
        payloads = {str(path): b'root controlled fixture' for path in ops.fixed_files('systemd')}
        payloads[str(ops.CONFIG)] = json.dumps({'service_manager': 'systemd', 'ready': True}).encode()
        python = Path('/usr/lib/fake-python')
        payloads[str(python)] = b'OS interpreter fixture'
        receipt = {'schema': 1, 'service_manager': 'systemd', 'system_python': str(ops.PYTHON),
                   'interpreter_resolved': str(python), 'interpreter_sha256': hashlib.sha256(payloads[str(python)]).hexdigest(),
                   'files': {str(path): hashlib.sha256(payloads[str(path)]).hexdigest()
                             for path in ops.fixed_files('systemd')}}
        return receipt, payloads, python

    def verify(self, receipt, payloads, python):
        payloads = {**payloads, str(ops.RECEIPT): json.dumps(receipt).encode()}
        with patch.object(ops, 'trusted_bytes', side_effect=lambda path, limit: payloads[str(path)]) as read, \
                patch.object(ops, 'trusted_executable', return_value=python), \
                patch.object(ops.pwd, 'getpwnam', return_value=ACCOUNT):
            result = ops.verify_installation()
            self.assertTrue(all('/var/lib/v-ui' not in str(call.args[0]) for call in read.call_args_list))
            return result

    def test_receipt_binds_fixed_assets_and_never_reads_user_data(self):
        receipt, payloads, python = self.receipt()
        self.assertEqual(self.verify(receipt, payloads, python), ('systemd', ACCOUNT))

    def test_receipt_missing_extra_drift_and_interpreter_fail_closed(self):
        mutators = [
            lambda r, p: r.update(schema=True),
            lambda r, p: r.update(schema=2),
            lambda r, p: r.update(service_manager='other'),
            lambda r, p: r.update(system_python='/var/lib/v-ui/runtime/python'),
            lambda r, p: r.update(interpreter_resolved='/tmp/python'),
            lambda r, p: r.update(interpreter_sha256='0' * 64),
            lambda r, p: r.update(extra='ignored?'),
            lambda r, p: r['files'].update({'/var/lib/v-ui/CURRENT.json': '0' * 64}),
            lambda r, p: r['files'].pop(str(ops.WORKER)),
            lambda r, p: r['files'].update({str(ops.WORKER): '../unsafe'}),
            lambda r, p: p.update({str(ops.WORKER): b'changed code'}),
        ]
        for mutate in mutators:
            receipt, payloads, python = self.receipt()
            mutate(receipt, payloads)
            with self.subTest(mutate=mutate), self.assertRaises(ops.Refused):
                self.verify(receipt, payloads, python)

    def test_dns_closure_is_exact_and_each_missing_or_tampered_root_helper_refuses(self):
        names = ('certificate_errors.py','dns_store.py','cloudflare.py','dns_journal.py','dns_backup.py')
        for manager in ('systemd','openrc'):
            self.assertTrue({ops.CONTROL / name for name in names} <= ops.fixed_files(manager))
        for name in names:
            for fault in ('missing', 'changed'):
                receipt, payloads, python = self.receipt(); path = str(ops.CONTROL / name)
                if fault == 'missing': receipt['files'].pop(path)
                else: payloads[path] += b'untrusted change'
                with self.subTest(name=name, fault=fault), self.assertRaises(ops.Refused):
                    self.verify(receipt, payloads, python)

    def test_changed_or_not_ready_config_is_rejected_even_with_matching_hash(self):
        for config in ({'ready': False, 'service_manager': 'systemd'},
                       {'ready': 1, 'service_manager': 'systemd'},
                       {'ready': True, 'service_manager': 'openrc'}):
            receipt, payloads, python = self.receipt()
            payloads[str(ops.CONFIG)] = json.dumps(config).encode()
            receipt['files'][str(ops.CONFIG)] = hashlib.sha256(payloads[str(ops.CONFIG)]).hexdigest()
            with self.assertRaises(ops.Refused):
                self.verify(receipt, payloads, python)

    def test_read_only_installation_lock_does_not_create_missing_file(self):
        lock = self.temp/'lock'
        with patch.object(ops, 'INSTALL_LOCK', lock), patch.object(ops, 'trusted_directory', plain_directory):
            with ops.installation_lock(_readonly=True):
                self.assertFalse(lock.exists())
            self.assertFalse(lock.exists())
            with self.assertRaisesRegex(ops.Refused, 'appeared'):
                with ops.installation_lock(_readonly=True):
                    lock.touch()

    def test_lock_checks_type_owner_mode_links_and_inode_and_preserves_contents(self):
        lock = self.temp / 'lock'
        lock.write_bytes(b'existing lock bytes')
        lock.chmod(0o600)
        real_fstat = os.fstat
        def fake_root(fd):
            info = real_fstat(fd)
            return types.SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino, st_uid=0,
                                         st_mode=info.st_mode, st_nlink=info.st_nlink)
        with patch.object(ops, 'INSTALL_LOCK', lock), patch.object(ops, 'trusted_directory', plain_directory), \
                patch.object(ops.os, 'fstat', side_effect=fake_root):
            with ops.installation_lock():
                self.assertEqual(lock.read_bytes(), b'existing lock bytes')
                with self.assertRaises(ops.Refused):
                    with ops.installation_lock():
                        self.fail('second lock admitted')
            lock.chmod(0o644)
            with self.assertRaises(ops.Refused):
                with ops.installation_lock():
                    pass
            lock.chmod(0o600)
            os.link(lock, self.temp / 'lock-hardlink')
            with self.assertRaises(ops.Refused):
                with ops.installation_lock():
                    pass
            (self.temp / 'lock-hardlink').unlink()
            with self.assertRaises(ops.Refused):
                with ops.installation_lock():
                    lock.rename(self.temp / 'old')
                    lock.write_bytes(b'replacement')
                    lock.chmod(0o600)
        lock.unlink()
        lock.symlink_to(self.temp / 'old')
        with patch.object(ops, 'INSTALL_LOCK', lock), patch.object(ops, 'trusted_directory', plain_directory):
            with self.assertRaises(OSError):
                with ops.installation_lock():
                    pass


class ServiceTests(unittest.TestCase):
    def backend(self, manager='systemd'):
        with patch.object(ops, 'trusted_executable'):
            return ops.Services(manager)

    def properties(self, unit='v-ui.service', active=False):
        socket = unit.endswith('.socket')
        fields = {'LoadState': 'loaded', 'ActiveState': 'active' if active else 'inactive',
                  'SubState': ('listening' if socket else 'running') if active else 'dead',
                  'NeedDaemonReload': 'no', 'FragmentPath': '/etc/systemd/system/' + unit, 'DropInPaths': '',
                  'ControlPID': '0', 'ControlGroup': '/system.slice/' + unit if active else '',
                  'User': '' if socket else 'v-ui', 'Group': '' if socket else 'v-ui', 'KillMode': 'control-group'}
        if not socket:
            fields.update(MainPID='123' if active else '0', ControlGroup='/system.slice/' + unit if active else '',
                          User='v-ui', Group='v-ui', KillMode='control-group')
        return fields

    def result(self, fields):
        return 0, ''.join(key + '=' + value + '\n' for key, value in fields.items()).encode()

    def test_exact_known_service_state_and_fixed_command(self):
        backend = self.backend()
        for unit in ops.SYSTEMD_UNITS:
            for active in (False, True):
                with patch.object(backend, '_run', return_value=self.result(self.properties(unit, active))) as run:
                    self.assertEqual(backend.state(unit), 'active' if active else 'inactive')
                    self.assertEqual(run.call_args.args[0], ['show', '--no-pager', '--all',
                                     '--property=' + ','.join(ops.PROPERTIES), unit])
        with patch.object(ops, 'run_command', return_value=(0, b'')) as run:
            backend.mutate('stop', 'v-ui.service')
            self.assertEqual(run.call_args.args[0], ['/usr/bin/systemctl', 'stop', 'v-ui.service'])

    def test_systemd_unknown_and_drift_never_treated_as_inactive(self):
        cases = {'LoadState': ['not-found', 'masked', 'error', ''],
                 'ActiveState': ['failed', 'activating', 'deactivating', 'unknown', ''],
                 'SubState': ['failed', 'exited', 'auto-restart'],
                 'MainPID': ['1', '-1', '01', '', '0; /bin/sh'],
                 'ControlPID': ['1', '-1', '00', '', 'unknown'],
                 'ControlGroup': ['/user.slice/other', '/system.slice/../v-ui.service', '/'],
                 'User': ['root', '0', ''], 'Group': ['root', '0', ''],
                 'KillMode': ['process', 'none', 'mixed', ''], 'NeedDaemonReload': ['yes', ''],
                 'FragmentPath': ['/run/systemd/system/v-ui.service', '/tmp/unit'],
                 'DropInPaths': ['/etc/systemd/system/v-ui.service.d/override.conf']}
        backend = self.backend()
        for field, values in cases.items():
            for value in values:
                fields = {**self.properties(), field: value}
                with self.subTest(field=field, value=value), patch.object(backend, '_run', return_value=self.result(fields)), \
                        self.assertRaises(ops.Refused):
                    backend.state('v-ui.service')
        fields = self.properties()
        for field in list(fields):
            reduced = {key: value for key, value in fields.items() if key != field}
            with patch.object(backend, '_run', return_value=self.result(reduced)), self.assertRaises(ops.Refused):
                backend.state('v-ui.service')
        for result in ((1, b''), (0, b'LoadState=loaded\nLoadState=loaded\n'), (0, b'\xff'),
                       self.result({**fields, 'ExecStart': '/tmp/evil'})):
            with patch.object(backend, '_run', return_value=result), self.assertRaises(ops.Refused):
                backend.state('v-ui.service')

    def test_socket_real_systemd_properties_and_running_substate(self):
        # systemd v255 dbus.c registers the Socket exec/cgroup/kill vtables;
        # socket.c maps both LISTENING and RUNNING to ACTIVE. No host is used.
        backend = self.backend(); unit = 'v-ui-http01.socket'
        for state, substate in (('active', 'listening'), ('active', 'running'), ('inactive', 'dead')):
            for cgroup in ('', '/system.slice/' + unit):
                for main_pid in (None, '0'):
                    fields = {**self.properties(unit, state == 'active'), 'SubState': substate,
                              'ControlGroup': cgroup}
                    if main_pid is not None: fields['MainPID'] = main_pid
                    with self.subTest(state=state, substate=substate, cgroup=cgroup, main_pid=main_pid), \
                            patch.object(backend, '_run', return_value=self.result(fields)):
                        self.assertEqual(backend.state(unit), state)

    def test_socket_missing_unknown_process_or_drift_properties_refuse(self):
        backend = self.backend(); unit = 'v-ui-http01.socket'
        fields = self.properties(unit, True)
        cases = {'ControlPID': ['1', '-1', '00', '', 'unknown'], 'MainPID': ['1', '-1', '', '00'],
                 'KillMode': ['', 'process', 'none', 'mixed'], 'User': ['root', '0', 'v-ui'],
                 'Group': ['root', '0', 'v-ui'], 'ControlGroup': ['/system.slice/v-ui.service', '/', '/user.slice/x'],
                 'ActiveState': ['activating', 'deactivating', 'failed', 'unknown'],
                 'SubState': ['start-pre', 'stop-pre', 'failed', 'dead', 'unknown']}
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value), \
                        patch.object(backend, '_run', return_value=self.result({**fields, field: value})), \
                        self.assertRaises(ops.Refused):
                    backend.state(unit)
        for field in fields:
            with self.subTest(missing=field), patch.object(backend, '_run',
                    return_value=self.result({key:value for key,value in fields.items() if key != field})), \
                    self.assertRaises(ops.Refused):
                backend.state(unit)

    def test_service_operations_share_deadline_and_emergency_has_fixed_budget(self):
        backend = self.backend()
        backend.deadline = 120
        with patch.object(ops.time, 'monotonic', return_value=119), \
                patch.object(ops, 'run_command', return_value=(0, b'')) as run:
            backend.mutate('stop', 'v-ui.service')
            self.assertEqual(run.call_args.kwargs['timeout'], 1)
            backend.mutate('stop', 'v-ui.service', ignore_cancel=True)
            self.assertEqual(run.call_args.kwargs['timeout'], ops.COMMAND_TIMEOUT)
        with patch.object(ops.time, 'monotonic', return_value=121), \
                patch.object(ops, 'Child', side_effect=AssertionError('expired budget cannot spawn')), self.assertRaises(ops.Refused):
            backend.mutate('stop', 'v-ui.service')

    def test_arbitrary_command_or_service_is_never_executed(self):
        backend = self.backend()
        with patch.object(backend, '_run', side_effect=AssertionError('must not run')):
            for action, unit in (('enable', 'v-ui.service'), ('disable', 'v-ui.service'),
                                 ('restart', 'v-ui.service'), ('stop', 'ssh.service'), ('start', '--all')):
                with self.assertRaises(ops.Refused):
                    backend.mutate(action, unit)
            with self.assertRaises(ops.Refused):
                backend.state('/tmp/evil')

    def test_stop_socket_first_attempts_all_and_requires_proof(self):
        backend = self.backend()
        with patch.object(backend, 'mutate') as mutate, patch.object(backend, 'prove_stopped') as prove:
            backend.stop_all()
            self.assertEqual([call.args for call in mutate.call_args_list],
                             [('stop', 'v-ui-http01.socket'), ('stop', 'v-ui.service'), ('stop', 'v-ui-http01.service')])
            prove.assert_called_once_with()
        with patch.object(backend, 'mutate', side_effect=[ops.Refused('fail'), None, None]) as mutate, \
                patch.object(backend, 'prove_stopped') as prove, self.assertRaises(ops.Refused):
            backend.stop_all()
        self.assertEqual(mutate.call_count, 3)
        prove.assert_not_called()

    def test_emergency_stop_retries_transition_and_clears_only_empty_failed_unit(self):
        backend = self.backend()
        unit = 'v-ui.service'
        failed = dict(self.properties(unit), ActiveState='failed', SubState='failed')
        with patch.object(backend, 'mutate'), patch.object(backend, 'prove_stopped', side_effect=[
                ops.UnitTransition(unit, ('deactivating', 'stop-sigterm')),
                ops.UnitTransition(unit, ('failed', 'failed')), None]) as prove, \
                patch.object(backend, '_systemd_properties', return_value=failed), \
                patch.object(ops, 'cgroup_empty', return_value=True), \
                patch.object(ops.time, 'sleep'), patch.object(backend, '_run', return_value=(0, b'')) as run:
            backend.stop_all(emergency=True)
            self.assertEqual(run.call_args.args, (['reset-failed', unit],))
            self.assertTrue(run.call_args.kwargs['ignore_cancel'])
            self.assertEqual(run.call_args.kwargs['deadline'], prove.call_args.kwargs['emergency_deadline'])
            self.assertEqual(prove.call_count, 3)
        with patch.object(backend, 'mutate'), patch.object(backend, 'prove_stopped', side_effect=[
                ops.UnitTransition(unit, ('failed', 'failed')), ops.UnitTransition(unit, ('failed', 'failed'))]), \
                patch.object(backend, '_systemd_properties', return_value=failed), \
                patch.object(ops, 'cgroup_empty', return_value=False), \
                patch.object(ops.time, 'monotonic', side_effect=[0, 1, 6]), \
                patch.object(ops.time, 'sleep'), patch.object(backend, '_run') as run:
            with self.assertRaises(ops.UnitTransition):
                backend.stop_all(emergency=True)
            run.assert_not_called()

    def test_stopped_requires_every_empty_service_cgroup(self):
        backend = self.backend()
        inactive = {unit: 'inactive' for unit in ops.SYSTEMD_UNITS}
        with patch.object(backend, 'snapshot', return_value=inactive), patch.object(ops, 'cgroup_empty', side_effect=[True, False]), \
                self.assertRaises(ops.Refused):
            backend.prove_stopped()
        with patch.object(backend, 'snapshot', return_value=inactive), \
                patch.object(ops, 'cgroup_empty', side_effect=[True, True, False]) as check, \
                self.assertRaises(ops.Refused):
            backend.prove_stopped()
        self.assertEqual([call.args[0] for call in check.call_args_list], list(ops.SYSTEMD_UNITS))
        with patch.object(backend, 'snapshot', return_value=inactive), \
                patch.object(ops, 'cgroup_empty', return_value=True) as check:
            backend.prove_stopped()
        self.assertEqual([call.args[0] for call in check.call_args_list], list(ops.SYSTEMD_UNITS))
        with patch.object(backend, 'snapshot', return_value={**inactive, 'v-ui-http01.socket': 'active'}), \
                patch.object(ops, 'cgroup_empty', side_effect=AssertionError('do not check yet')), self.assertRaises(ops.Refused):
            backend.prove_stopped()

    def test_cgroup_descendant_residual_and_symlink_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix='vui-cgroup-') as directory:
            root = Path(directory)
            (root / 'cgroup.controllers').write_text('cpu memory pids')
            group = root / 'system.slice/v-ui.service'
            group.mkdir(parents=True)
            (group / 'cgroup.procs').write_text('')
            child = group / 'child'
            child.mkdir()
            (child / 'cgroup.procs').write_text('42\n')
            with patch.object(ops, 'CGROUP_ROOT', root), patch.object(ops, 'trusted_directory', plain_directory), \
                    patch.object(ops, '_root_owned'):
                self.assertFalse(ops.cgroup_empty('v-ui.service'))
                (child / 'cgroup.procs').write_text('')
                self.assertTrue(ops.cgroup_empty('v-ui.service'))
                self.assertTrue(ops.cgroup_empty('v-ui-http01.service'))
                self.assertTrue(ops.cgroup_empty('v-ui-http01.socket'))
                socket_group = root / 'system.slice/v-ui-http01.socket'
                socket_group.mkdir()
                (socket_group / 'cgroup.procs').write_text('314\n')
                self.assertFalse(ops.cgroup_empty('v-ui-http01.socket'))
                (socket_group / 'cgroup.procs').write_text('')
                self.assertTrue(ops.cgroup_empty('v-ui-http01.socket'))
                (child / 'link').symlink_to(root)
                with self.assertRaises(ops.Refused):
                    ops.cgroup_empty('v-ui.service')
                (root / 'cgroup.controllers').unlink()
                with self.assertRaises(OSError):
                    ops.cgroup_empty('v-ui.service')

    def test_restore_only_original_topology_no_enable_or_disable(self):
        backend = self.backend()
        desired = {'v-ui.service': 'active', 'v-ui-http01.service': 'inactive', 'v-ui-http01.socket': 'active'}
        with patch.object(backend, 'mutate') as mutate, patch.object(backend, 'snapshot', return_value=desired):
            backend.restore(desired)
            self.assertEqual([call.args for call in mutate.call_args_list],
                             [('start', 'v-ui-http01.socket'), ('start', 'v-ui.service')])
        with patch.object(backend, 'mutate'), patch.object(backend, 'snapshot', return_value={unit: 'inactive' for unit in desired}), \
                self.assertRaises(ops.Refused):
            backend.restore(desired)

    def test_openrc_return_code_unknown_and_writes_unprovable(self):
        backend = self.backend('openrc')
        for code, expected in ((0, 'active'), (3, 'inactive')):
            with patch.object(backend, '_run', return_value=(code, b'not printed')):
                self.assertEqual(backend.state('v-ui'), expected)
        for code in (1, 2, 4, 127):
            with patch.object(backend, '_run', return_value=(code, b'failed')), self.assertRaises(ops.Refused):
                backend.state('v-ui')
        with patch.object(backend, 'snapshot', return_value={unit: 'inactive' for unit in ops.OPENRC_UNITS}), \
                self.assertRaises(ops.Refused):
            backend.prove_stopped()


class FakeServices:
    manager = 'systemd'
    def __init__(self, events, active=True):
        self.events = events
        self.states = {'v-ui.service': 'active' if active else 'inactive',
                       'v-ui-http01.service': 'inactive',
                       'v-ui-http01.socket': 'active' if active else 'inactive'}
        self.fail_stop = False
        self.fail_restore = False
    def snapshot(self):
        self.events.append('snapshot')
        return self.states.copy()
    def prove_stopped(self):
        self.events.append('prove_stopped')
        if any(value != 'inactive' for value in self.states.values()):
            raise ops.Refused('not stopped')
    def stop_all(self, emergency=False):
        self.events.append('emergency_stop' if emergency else 'stop_socket_then_services')
        self.states = {unit: 'inactive' for unit in self.states}
        if self.fail_stop and not emergency:
            raise ops.Refused('stop failed')
    def restore(self, desired):
        self.events.append(('restore', desired.copy()))
        self.states = desired.copy()
        if self.fail_restore:
            raise ops.Refused('restore failed')


class FakeWorker:
    def __init__(self, events, request, responses=None, fail_exit=False, fail_close=False):
        self.events = events
        done = {'event': 'done', 'ok': True}
        if request['action'] in ('backup', 'activate'):
            done.update(backup_name='maintenance-' + 'a' * 32 + '.zip', backup_sha256='b' * 64)
        if request['action'] == 'stage':
            done['prepared_release'] = 'candidate-123'
        if request['action'] == 'activate':
            done['selected_release'] = request['release_id']
        self.responses = iter(responses if responses is not None else [{'event': 'prepared'}, done, {'event': 'writers_released'}])
        self.fail_exit, self.fail_close = fail_exit, fail_close
    def send(self, value):
        self.events.append(('send', value))
    def receive(self):
        value = next(self.responses)
        self.events.append(('receive', value))
        if isinstance(value, Exception):
            raise value
        return value
    def exit_zero(self):
        self.events.append('exit_zero')
        if self.fail_exit:
            raise ops.Refused('exit failed')
    def close(self):
        self.events.append('close')
        if self.fail_close:
            self.fail_close = False
            raise ops.Refused('cleanup failed')


class TransactionTests(unittest.TestCase):
    def execute(self, action, active=True, responses=None, fail_exit=False, fail_close=False, fail_stop=False, fail_restore=False, cancelled=None):
        events = []
        request = {'protocol': 1, 'action': action}
        if action in ('restore', 'stage'):
            request.update(path='/private/backup.zip', sha256='a' * 64)
        if action == 'activate':
            request['release_id'] = 'candidate-123'
        services = FakeServices(events, active=active)
        services.fail_stop, services.fail_restore = fail_stop, fail_restore
        worker = FakeWorker(events, request, responses, fail_exit, fail_close)
        factory = unittest.mock.Mock(return_value=worker)
        def run():
            if action in ('restore','recover-restore'):
                with patch.object(ops,'revoke_core_epoch'):
                    return ops.operate(request,services,ACCOUNT,[] if cancelled is None else cancelled,factory)
            if action=='stage':
                # Wire/order fixture only. Real snapshot/Root origin/cleanup
                # are covered by test_stage_origin and installed Actions.
                material=types.SimpleNamespace(path=Path(request['path']),origin={'release_id':'candidate-123'},
                    deadline=time.monotonic()+10,retain=False,check=lambda:None)
                with patch.object(ops,'accepted_stage',return_value=nullcontext(material)):
                    return ops.operate(request,services,ACCOUNT,[] if cancelled is None else cancelled,factory)
            return ops.operate(request, services, ACCOUNT, [] if cancelled is None else cancelled, factory)
        return run, events, services, factory, request

    def test_protocol_order_and_each_action_restores_only_required_topology(self):
        for action in ops.ACTIONS[:-1]:
            with self.subTest(action=action):
                run, events, services, factory, request = self.execute(action, active=action != 'start')
                initial = services.states.copy()
                result = run()
                self.assertTrue(result['ok'])
                self.assertEqual(factory.call_args.args[0], ['/usr/bin/python3', '-B', '-I', '-S', '/usr/local/lib/v-ui/ops_worker.py'])
                self.assertIs(factory.call_args.kwargs['account'], ACCOUNT)
                commands = [entry[1] for entry in events if isinstance(entry, tuple) and entry[0] == 'send']
                self.assertEqual(commands, [request, {'command': 'proceed'}, {'command': 'release_writers'}, {'command': 'finish'}])
                restore_index = next(index for index, event in enumerate(events) if isinstance(event, tuple) and event[0] == 'restore')
                self.assertLess(events.index(('receive', {'event': 'writers_released'})), restore_index)
                self.assertLess(restore_index, events.index(('send', {'command': 'finish'})))
                if action == 'stop':
                    self.assertTrue(all(value == 'inactive' for value in services.states.values()))
                elif action == 'start':
                    self.assertEqual(services.states['v-ui.service'], 'active')
                    self.assertEqual(services.states['v-ui-http01.socket'], 'active')
                else:
                    self.assertEqual(services.states, initial)

    def test_start_is_readonly_idempotent_when_all_start_targets_active(self):
        run, events, services, factory, _ = self.execute('start')
        self.assertEqual(run(), {'event': 'already_running'})
        factory.assert_not_called()
        self.assertEqual(events, ['snapshot'])

    def test_partially_active_start_refuses_without_worker_or_service_change(self):
        run, events, services, factory, _ = self.execute('start')
        services.states['v-ui-http01.socket'] = 'inactive'
        with self.assertRaises(ops.Refused):
            run()
        factory.assert_not_called()
        self.assertNotIn('stop_socket_then_services', events)

    def test_recovery_from_stopped_topology_never_starts_services(self):
        run, events, services, factory, request = self.execute('recover-restore', active=False)
        self.assertEqual(run(), {'event': 'done', 'ok': True})
        self.assertEqual(request, {'protocol': 1, 'action': 'recover-restore'})
        self.assertIn('stop_socket_then_services', events)
        self.assertTrue(all(value == 'inactive' for value in services.states.values()))
        desired = next(event[1] for event in events if isinstance(event, tuple) and event[0] == 'restore')
        self.assertTrue(all(value == 'inactive' for value in desired.values()))

    def test_stage_protocol_never_inspects_or_mutates_services(self):
        run, events, services, factory, request = self.execute('stage')
        services.manager = 'openrc'
        with patch.object(services, 'snapshot', side_effect=AssertionError('no service queries')):
            result = run()
        self.assertEqual(result, {'event': 'done', 'ok': True, 'prepared_release': 'candidate-123'})
        commands = [entry[1] for entry in events if isinstance(entry, tuple) and entry[0] == 'send']
        self.assertEqual(commands, [request, {'command': 'proceed'}, {'command': 'finish'}])
        self.assertNotIn('stop_socket_then_services', events)
        self.assertNotIn('emergency_stop', events)
        self.assertNotIn('prove_stopped', events)
        self.assertEqual(services.states['v-ui.service'], 'active')

    def test_stage_failure_never_runs_service_cleanup(self):
        for responses in ([ops.Refused('EOF')], [{'event': 'prepared'}, ops.Refused('timeout')],
                          [{'event': 'prepared'}, {'event': 'done', 'ok': True, 'prepared_release': '../evil'}]):
            run, events, services, factory, request = self.execute('stage', responses=responses)
            with self.assertRaises(ops.Refused):
                run()
            self.assertEqual(services.states['v-ui.service'], 'active')
            self.assertNotIn('emergency_stop', events)
            self.assertIn('close', events)

    def test_worker_prepared_failure_never_stops_services(self):
        for response in ({'event': 'ready'}, {'event': 'prepared', 'command': 'stop'}, ops.Refused('EOF')):
            run, events, services, _, _ = self.execute('backup', responses=[response])
            with self.assertRaises(ops.Refused):
                run()
            self.assertNotIn('stop_socket_then_services', events)
            self.assertEqual(services.states['v-ui.service'], 'active')

    def test_all_post_stop_failures_leave_stopped_and_never_auto_rollback(self):
        cases = [dict(responses=[{'event': 'prepared'}, ops.Refused('EOF')]),
                 dict(responses=[{'event': 'prepared'}, {'event': 'done', 'ok': False}]),
                 dict(responses=[{'event': 'prepared'}, {'event': 'done', 'ok': True, 'command': 'reboot'}]),
                 dict(fail_stop=True), dict(fail_restore=True), dict(fail_exit=True), dict(fail_close=True)]
        for options in cases:
            with self.subTest(options=options):
                run, events, services, _, _ = self.execute('restart', **options)
                with self.assertRaises(ops.Refused):
                    run()
                self.assertIn('emergency_stop', events)
                self.assertTrue(all(value == 'inactive' for value in services.states.values()))
                self.assertFalse(any('rollback' in str(event) for event in events))

    def test_writer_ack_is_required_before_start_and_worker_strings_not_commands(self):
        for event in ({'event': 'released'}, {'event': 'writers_released', 'command': '/bin/sh'},
                      {'event': 'error', 'code': 'MAINTENANCE_REFUSED'}):
            run, events, services, _, _ = self.execute('restart', responses=[{'event': 'prepared'}, {'event': 'done', 'ok': True}, event])
            with self.assertRaises(ops.Refused):
                run()
            self.assertFalse(any(isinstance(entry, tuple) and entry[0] == 'restore' for entry in events))
            self.assertTrue(all(value == 'inactive' for value in services.states.values()))

    def test_cancel_before_stop_does_not_start_or_stop(self):
        run, events, _, _, _ = self.execute('restart', cancelled=[signal.SIGINT])
        with self.assertRaises(ops.Refused):
            run()
        self.assertNotIn('stop_socket_then_services', events)
        self.assertNotIn('emergency_stop', events)

    def test_backup_activate_result_basename_and_checksum_are_strict(self):
        request = {'protocol': 1, 'action': 'backup'}
        good = {'event': 'done', 'ok': True, 'backup_name': 'maintenance-' + 'a' * 32 + '.zip', 'backup_sha256': 'b' * 64}
        self.assertEqual(ops.validate_done(good, request), good)
        for name, value in (('backup_name', '../../etc/passwd'), ('backup_name', '/private/file'),
                            ('backup_name', 'maintenance-\x1b[2J.zip'), ('backup_sha256', '0'), ('ok', 1)):
            with self.assertRaises(ops.Refused):
                ops.validate_done({**good, name: value}, request)
        with self.assertRaises(ops.Refused):
            ops.validate_done({**good, 'selected_release': 'other'}, {'protocol': 1, 'action': 'activate', 'release_id': 'expected'})

    def test_request_fields_do_not_expand_root_read_or_command_scope(self):
        cases = [dict(protocol=True, action='stop'), dict(protocol=1, action='enable'),
                 dict(protocol=1, action='backup', path='/etc/passwd'),
                 dict(protocol=1, action='activate', release_id='../../tmp'),
                 dict(protocol=1, action='restore', path='relative.zip', sha256='a' * 64),
                 dict(protocol=1, action='restore', path='/tmp/a\x1b', sha256='a' * 64),
                 dict(protocol=1, action='restore', path='/tmp/a', sha256='not trusted')]
        for request in cases:
            with self.subTest(request=request), self.assertRaises(ops.Refused):
                ops.validate_request(request)

    def test_openrc_fails_before_worker_or_mutation(self):
        run, events, services, factory, _ = self.execute('stop')
        services.manager = 'openrc'
        with self.assertRaises(ops.Refused):
            run()
        factory.assert_not_called()
        self.assertEqual(events, ['snapshot'])


class ChildTests(unittest.TestCase):
    def child(self, script, timeout=2):
        return ops.Child([sys.executable, '-B', '-I', '-S', '-c', script], timeout)

    def test_protocol_real_nonroot_child_and_identity_safe_reap_order(self):
        if os.geteuid() == 0:
            self.skipTest('this real-child fixture is deliberately non-root')
        child = self.child('import json,sys; q=json.loads(sys.stdin.readline()); print(json.dumps({"event":"prepared"}),flush=True); q=json.loads(sys.stdin.readline())')
        real_killpg = os.killpg
        observations = []
        def guarded_signal(pid, number):
            observed = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            observations.append(observed)
            return real_killpg(pid, number)
        try:
            child.send({'protocol': 1, 'action': 'stop'})
            self.assertEqual(child.receive(), {'event': 'prepared'})
            child.send({'command': 'finish'})
            child.exit_zero()
        finally:
            with patch.object(ops.os, 'killpg', side_effect=guarded_signal):
                child.close(grace=.01)
        self.assertEqual(len(observations), 2)
        self.assertTrue(all(item is not None for item in observations))

    def test_protocol_eof_oversize_timeout_and_unsolicited_bytes(self):
        if os.geteuid() == 0:
            self.skipTest('this real-child fixture is deliberately non-root')
        scripts = ('pass', 'print("x"*17000,flush=True)',
                   'import time; time.sleep(20)',
                   'print(\'{"event":"prepared"}\\n{"event":"done"}\',flush=True)')
        for script in scripts:
            with self.subTest(script=script):
                child = self.child(script, timeout=.15)
                try:
                    with self.assertRaises(ops.Refused):
                        child.receive()
                finally:
                    child.close(grace=.01)

    def test_pipe_configuration_failure_cleans_the_spawned_child(self):
        fake = unittest.mock.Mock()
        with patch.object(ops.subprocess, 'Popen', return_value=fake), \
                patch.object(ops.os, 'set_blocking', side_effect=OSError('setup failure')), \
                patch.object(ops.Child, 'close') as close, self.assertRaises(OSError):
            ops.Child(['fixture'], 1)
        close.assert_called_once_with()

    def test_blocked_protocol_write_is_bounded(self):
        if os.geteuid() == 0:
            self.skipTest('this real-child fixture is deliberately non-root')
        child = self.child('import time; time.sleep(20)', timeout=.15)
        try:
            with self.assertRaises(ops.Refused):
                for unused in range(20):
                    child.send({'path': 'x' * 4000})
        finally:
            child.close(grace=.01)

    def test_echild_cleanup_never_signals_reused_group(self):
        process = unittest.mock.Mock(pid=4321)
        child = object.__new__(ops.Child)
        child.process = process
        with patch.object(ops.os, 'waitid', side_effect=ChildProcessError), patch.object(ops.os, 'killpg') as kill:
            with self.assertRaises(ops.Refused):
                child.close(grace=0)
            kill.assert_not_called()
            process.wait.assert_not_called()

    def test_child_launch_identity_and_environment_are_fixed(self):
        fake = unittest.mock.Mock()
        fake.stdin.fileno.return_value = 30
        fake.stdout.fileno.return_value = 31
        with patch.object(ops.subprocess, 'Popen', return_value=fake) as popen, patch.object(ops.os, 'set_blocking'):
            ops.Child(['/usr/bin/python3', '-B', '-I', '-S', str(ops.WORKER)], 600, account=ACCOUNT)
        kwargs = popen.call_args.kwargs
        self.assertEqual(kwargs['user'], ACCOUNT.pw_uid)
        self.assertEqual(kwargs['group'], ACCOUNT.pw_gid)
        self.assertEqual(kwargs['extra_groups'], [])
        self.assertEqual(kwargs['cwd'], '/')
        self.assertEqual(kwargs['env'], ops.ENV)
        self.assertTrue(kwargs['start_new_session'])
        self.assertEqual(kwargs['stdin'], subprocess.PIPE)
        self.assertEqual(kwargs['stdout'], subprocess.PIPE)
        self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
        self.assertNotIn('preexec_fn', kwargs)
        self.assertNotIn('shell', kwargs)
        with self.assertRaises(ops.Refused):
            ops.Child(['not run'], 1, account=types.SimpleNamespace(pw_uid=0, pw_gid=0))

    def test_service_command_output_is_bounded_and_never_waits_for_descendant_eof(self):
        if os.geteuid() == 0:
            self.skipTest('this real-child fixture is deliberately non-root')
        with self.assertRaises(ops.Refused):
            ops.run_command([sys.executable, '-B', '-I', '-S', '-c', 'print("x"*70000)'])
        code, output = ops.run_command([sys.executable, '-B', '-I', '-S', '-c', 'import os,time; pid=os.fork(); time.sleep(20) if pid==0 else print("parent",flush=True)'])
        self.assertEqual((code, output), (0, b'parent\n'))


@unittest.skipIf(os.geteuid() == 0, 'End-to-end worker fixtures run only as a non-root test user')
class IntegratedWorkerTests(unittest.TestCase):
    """Keep the real consumer/worker wire contract exercised together in CI."""
    def setUp(self):
        fixture_spec = importlib.util.spec_from_file_location('_maintenance_fixtures', SOURCE / 'tests/test_maintenance_worker.py')
        fixtures = importlib.util.module_from_spec(fixture_spec)
        fixture_spec.loader.exec_module(fixtures)
        self.fixture = fixtures.MaintenanceWorkerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.events = []
        self.services = FakeServices(self.events, active=True)
        fixture = self.fixture
        code = ('import runpy,sys,json;from pathlib import Path;'
                'm=runpy.run_path(sys.argv[1]);api=m["_load_release_tools"]();'
                'api._maintenance_read_config=lambda:json.loads(Path(sys.argv[3]).read_text());'
                'raise SystemExit(m["_run"](Path(sys.argv[2]),api,sys.stdin.fileno(),sys.stdout))')
        class FixtureChild(ops.Child):
            def __init__(self, command, timeout, account, cancelled):
                # Test-only launch seam: current non-root UID, temporary ROOT/config.
                # Production still uses the fixed worker and explicit user/group.
                super().__init__([sys.executable, '-B', '-I', '-S', '-c', code,
                                  str(fixture.control / 'ops_worker.py'), str(fixture.root), str(fixture.config)],
                                 timeout, cancelled=cancelled)
            def close(self):
                super().close(grace=.01)
        self.factory = FixtureChild

    def execute(self, action, **values):
        if action=='stage':
            # Preserve the real demoted consumer protocol independently from
            # the Root producer's isolated filesystem tests.
            material=types.SimpleNamespace(path=Path(values['path']),origin={'release_id':'integrated-stage'},
                deadline=time.monotonic()+10,retain=False,check=lambda:None)
            with patch.object(ops,'accepted_stage',return_value=nullcontext(material)):
                return ops.operate({'protocol':1,'action':action,**values},self.services,ACCOUNT,[],self.factory)
        with patch.object(ops,'revoke_core_epoch'):
            return ops.operate({'protocol': 1, 'action': action, **values}, self.services,
                               ACCOUNT, [], self.factory)

    def test_real_controller_worker_backup_restore_activate_and_control_protocol(self):
        backup = self.execute('backup')
        self.assertTrue((self.fixture.root / 'backups' / backup['backup_name']).is_file())
        self.execute('restore', path=str(self.fixture.root / 'backups' / backup['backup_name']),
                     sha256=backup['backup_sha256'])
        self.fixture.prepared('integrated-candidate')
        activation = self.execute('activate', release_id='integrated-candidate')
        self.assertEqual(activation['selected_release'], 'integrated-candidate')
        current = json.loads((self.fixture.root / 'CURRENT.json').read_text())
        self.assertEqual(current['release_id'], 'integrated-candidate')
        self.assertEqual(current['previous_id'], 'old')
        with sqlite3.connect(self.fixture.data / 'v-ui.db') as database:
            rows_before = database.execute('SELECT * FROM inbounds').fetchall()
        routing_before = (self.fixture.data / 'routing.json').read_bytes()
        rollback = self.execute('activate', release_id='old')
        self.assertNotEqual(rollback['backup_name'], activation['backup_name'])
        self.assertTrue((self.fixture.root / 'backups' / rollback['backup_name']).is_file())
        current = json.loads((self.fixture.root / 'CURRENT.json').read_text())
        self.assertEqual(current['release_id'], 'old')
        self.assertEqual(current['previous_id'], 'integrated-candidate')
        with sqlite3.connect(self.fixture.data / 'v-ui.db') as database:
            self.assertEqual(database.execute('SELECT * FROM inbounds').fetchall(), rows_before)
        self.assertEqual((self.fixture.data / 'routing.json').read_bytes(), routing_before)
        self.execute('stop')
        self.assertTrue(all(value == 'inactive' for value in self.services.states.values()))
        self.execute('start')
        self.assertEqual(self.services.states['v-ui.service'], 'active')
        self.execute('restart')
        self.assertEqual(self.services.states['v-ui-http01.socket'], 'active')

    def test_real_recovery_restores_journal_original_preserves_replacement_and_stays_stopped(self):
        root, data = self.fixture.root, self.fixture.data
        recovery = root / 'recovery'
        recovery.mkdir(mode=0o700)
        original = recovery / ('before-' + '1' * 32)
        shutil.copytree(data, original)
        with sqlite3.connect(data / 'v-ui.db') as database:
            database.execute("UPDATE inbounds SET remark='replacement-preserved'")
        journal = root / 'RESTORE_PENDING.json'
        journal.write_text(json.dumps({'old': 'recovery/' + original.name}))
        journal.chmod(0o600)
        current_before = (root / 'CURRENT.json').read_bytes()
        self.services.states = {unit: 'inactive' for unit in self.services.states}
        with self.assertRaises(ops.Refused):
            self.execute('start')
        self.assertTrue(journal.exists())
        self.assertTrue(all(value == 'inactive' for value in self.services.states.values()))
        result = self.execute('recover-restore')
        self.assertEqual(result, {'event': 'done', 'ok': True})
        self.assertFalse(journal.exists())
        self.assertFalse(original.exists())
        self.assertEqual((root / 'CURRENT.json').read_bytes(), current_before)
        with sqlite3.connect(data / 'v-ui.db') as database:
            self.assertEqual(database.execute('SELECT remark FROM inbounds').fetchone()[0], 'original-node')
        retained = list(recovery.glob('interrupted-*'))
        self.assertEqual(len(retained), 1)
        with sqlite3.connect(retained[0] / 'v-ui.db') as database:
            self.assertEqual(database.execute('SELECT remark FROM inbounds').fetchone()[0], 'replacement-preserved')
        self.assertTrue(all(value == 'inactive' for value in self.services.states.values()))
        desired = [event[1] for event in self.events if isinstance(event, tuple) and event[0] == 'restore']
        self.assertEqual(len(desired), 1)
        self.assertTrue(all(value == 'inactive' for value in desired[0].values()))

    def test_real_stage_worker_never_queries_services_or_changes_current(self):
        archive, checksum = self.fixture.stage_archive('integrated-stage')
        before = (self.fixture.root / 'CURRENT.json').read_bytes()
        result = self.execute('stage', path=str(archive), sha256=checksum)
        self.assertEqual(result['prepared_release'], 'integrated-stage')
        self.assertEqual((self.fixture.root / 'CURRENT.json').read_bytes(), before)
        self.assertEqual(self.events, [])


class NetworkTests(unittest.TestCase):
    def fake_firewall(self, status=False, backend='ufw', managed=True):
        from unittest.mock import Mock
        self.firewall = types.SimpleNamespace(
            detect=Mock(return_value={'backend': backend, 'managed': managed}),
            port_open=Mock(return_value=status), open_ports=Mock())
        return self.firewall

    def test_diagnostics_are_readonly_and_separate_rule_listener_tls_and_external(self):
        firewall = self.fake_firewall(status=None, backend='nftables', managed=False)
        config = {'port': 3215, 'domain': 'panel.example.test'}
        with patch.object(ops, 'network_context', return_value=(firewall, config)), \
             patch.object(ops, 'local_tcp_listener', return_value=True), \
             patch.object(ops, 'local_panel_tls', return_value='ready（本机）'):
            lines = ops.network_diagnostics()
        text = '\n'.join(lines)
        self.assertIn('3215/tcp：本机规则 unknown；本机监听 listening', text)
        self.assertIn('云规则与公网入站：unknown', text)
        self.assertIn('不能证明外部可达', text)
        self.assertNotIn('10443', text)
        firewall.open_ports.assert_not_called()

    def test_open_exact_protocol_port_requires_known_managed_block_and_readback(self):
        firewall = self.fake_firewall()
        firewall.port_open.side_effect = [False, True]
        with patch.object(ops, 'network_context', return_value=(firewall, {})):
            result = ops.network_open('udp', 20443)
        firewall.open_ports.assert_called_once_with(firewall.detect.return_value, [20443], protocol='udp')
        self.assertIn('20443/udp 本机规则已放行并回读', result)
        self.assertIn('公网可达性未验证', result)

    def test_unknown_inactive_custom_and_already_allowed_never_modify_rules(self):
        for backend, managed, status in (('ufw', True, None), ('ufw', False, False),
                                        ('nftables', False, False), ('firewalld', False, False),
                                        ('ufw', True, True)):
            firewall = self.fake_firewall(status, backend, managed)
            with self.subTest(backend=backend, managed=managed, status=status), \
                 patch.object(ops, 'network_context', return_value=(firewall, {})):
                ops.network_open('tcp', 3215)
                firewall.open_ports.assert_not_called()

    def test_readback_failure_does_not_claim_success(self):
        for final in (False, None):
            firewall = self.fake_firewall()
            firewall.port_open.side_effect = [False, final]
            with self.subTest(final=final), patch.object(ops, 'network_context', return_value=(firewall, {})):
                with self.assertRaises(ops.Refused):
                    ops.network_open('tcp', 3215)

    def test_invalid_protocol_port_and_ssh_rejected_before_trusted_reads(self):
        with patch.object(ops, 'network_context') as read:
            for protocol, port in (('udp;sh', 3215), ('tcp', 22), ('tcp', 0), ('tcp', 65536), ('tcp', True)):
                with self.subTest(protocol=protocol, port=port), self.assertRaises(ops.Refused):
                    ops.network_open(protocol, port)
            read.assert_not_called()

    def test_network_module_bytes_are_receipt_bound_and_never_import_payload(self):
        raw = (SOURCE / 'scripts/firewall_support.py').read_bytes()
        config = b'{"port":3215,"domain":"panel.example.test"}'
        receipt = {'files': {str(ops.CONFIG): hashlib.sha256(config).hexdigest(),
                            str(ops.CONTROL / 'firewall_support.py'): hashlib.sha256(raw).hexdigest()}}
        def trusted(path, limit=None):
            return json.dumps(receipt).encode() if path == ops.RECEIPT else config if path == ops.CONFIG else raw
        with patch.object(ops, 'trusted_bytes', side_effect=trusted), \
             patch.object(ops, 'firewall_program', return_value='/usr/sbin/ufw'), \
             patch.object(ops, 'run_command', return_value=(0, b'Status: active\n')) as run:
            module, value = ops.network_context()
            self.assertEqual(value['port'], 3215)
            self.assertEqual(module.detect()['backend'], 'ufw')
            run.assert_called_once_with(['/usr/sbin/ufw', 'status'], None, timeout=10)
        receipt['files'][str(ops.CONTROL / 'firewall_support.py')] = '0' * 64
        with patch.object(ops, 'trusted_bytes', side_effect=trusted), \
             patch.object(ops, 'run_command') as run:
            with self.assertRaises(ops.Refused):
                ops.network_context()
            run.assert_not_called()

    def test_local_tls_keeps_ca_and_hostname_validation_and_uses_no_proxy(self):
        context = ops.ssl.create_default_context()
        from unittest.mock import MagicMock
        with patch.object(ops.ssl, 'create_default_context', return_value=context), \
             patch.object(ops.socket, 'create_connection', return_value=MagicMock()) as connect, \
             patch.object(context, 'wrap_socket', return_value=MagicMock()) as wrap:
            result = ops.local_panel_tls({'port':3215, 'domain':'panel.example.test', 'bind':'0.0.0.0'})
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ops.ssl.CERT_REQUIRED)
        self.assertIn('ready', result)
        connect.assert_called_once_with(('127.0.0.1', 3215), timeout=3)
        self.assertEqual(wrap.call_args.kwargs['server_hostname'], 'panel.example.test')

    def test_fixed_firewall_commands_reject_arbitrary_executable(self):
        with patch.object(ops, 'trusted_executable') as executable:
            with self.assertRaises(ops.Refused):
                ops.firewall_program('sh')
            executable.assert_not_called()


class MenuTests(unittest.TestCase):
    def run_menu(self, data, input_tty=True, output_tty=True):
        output = Terminal() if output_tty else io.StringIO()
        with patch.object(ops.os, 'getuid', return_value=0), patch.object(ops.os, 'geteuid', return_value=0), \
                patch.object(ops.sys, 'argv', ['v-ui']), \
                patch.object(ops.sys, 'stdin', Terminal(data) if input_tty else io.StringIO(data)), \
                patch.object(ops.sys, 'stdout', output), patch.object(ops.sys, 'stderr', output), \
                patch.object(ops, 'PYTHON', Path(sys.executable)), \
                patch.object(ops.sys, 'flags', types.SimpleNamespace(dont_write_bytecode=1, isolated=1, no_site=1)), \
                patch.object(ops.os, 'umask'), patch.object(ops.termios, 'tcgetattr', return_value=[0, 0, 0, 10]), \
                patch.object(ops.termios, 'tcsetattr'):
            return ops.main(), output.getvalue()

    def test_menu_first_screen_unknown_help_cancel_and_eof_no_external_action(self):
        with patch.object(ops, 'installation_lock', side_effect=AssertionError('host lock')), \
                patch.object(ops, 'verify_installation', side_effect=AssertionError('host reads')):
            for data in ('0\n', '', 'h\ninvalid\n0\n', '2\nno\n0\n', '2\n', '6\n\n0\n', '7\n\n0\n', '12\nn\n0\n',
                         '12\n\n0\n', '13\n0\n0\n', '14\n\n0\n', '15\nn\n0\n',
                         '16\nsing-box\n\n0\n', '17\nxray\nn\n0\n', '18\nn\n0\n'):
                code, text = self.run_menu(data)
                self.assertEqual(code, 0, text)
                self.assertIn('V-UI 受管维护菜单', text)
                self.assertNotIn('操作完成', text)

    def test_core_restore_menu_only_runs_fixed_child_after_confirmation(self):
        digest = 'b'*64
        name = 'core-maintenance-'+'a'*32+'.zip'
        receipt = json.dumps({'files': {str(ops.CONTROL/'system_ops.py'): digest}}).encode()
        for choice, action, values, outcome, message in (
                ('14', 'restore-components', [name, digest], 'restored_backup', '恢复完成'),
                ('15', 'recover-components', [], 'resumed_confirmed', '恢复完成'),
                ('16', 'update-core', ['sing-box', '42'], 'switched', '核心切换完成'),
                ('17', 'rollback-core', ['xray'], 'restored_previous', '核心试跑失败'),
                ('18', 'recover-core', [], 'restored_previous', '恢复完成'),
                ('19', 'backup-components', [], 'backup_created', '双域备份完成')):
            with self.subTest(action=action), patch.object(ops, 'verify_installation', return_value=('systemd', None)), \
                    patch.object(ops, 'trusted_bytes', return_value=receipt), \
                    patch.object(ops, 'run_command', return_value=(0, json.dumps(
                        dict(outcome=outcome, backup_name=name, backup_sha256=digest)).encode())) as run:
                code, output = self.run_menu('\n'.join([choice, *values, 'y', '0', '']))
                self.assertEqual(code, 0)
                self.assertIn(message, output)
                self.assertIn('原数据备份文件 '+name, output)
                self.assertIn('SHA-256 '+digest, output)
                args = run.call_args.args[0]
                self.assertEqual(args[1:6], ['-B', '-I', '-S', '-c', ops.CORE_RESTORE_BOOTSTRAP])
                self.assertEqual(args[6:], [digest, hashlib.sha256(receipt).hexdigest(), action, *values])
                compile(args[5], '<fixture bootstrap>', 'exec')

    def test_core_backup_recovery_menu_confirms_fixed_action_without_new_archive(self):
        digest='b'*64
        receipt=json.dumps({'files':{str(ops.CONTROL/'system_ops.py'):digest}}).encode()
        with patch.object(ops,'verify_installation',return_value=('systemd',None)), \
                patch.object(ops,'trusted_bytes',return_value=receipt), \
                patch.object(ops,'run_command',return_value=(0,json.dumps(
                    dict(outcome='backup_recovered',selection_revision='c'*64,panel_running=True)).encode())) as run:
            code,output=self.run_menu('18\ny\n0\n')
        self.assertEqual(code,0)
        self.assertIn('备份中断恢复完成',output)
        self.assertIn('未重新创建备份',output)
        self.assertNotIn('原数据备份文件',output)
        self.assertEqual(run.call_args.args[0][-1],'recover-core')

    def test_core_restore_menu_reports_only_fixed_failure_stages(self):
        digest = 'b'*64
        name = 'core-maintenance-'+'a'*32+'.zip'
        receipt = json.dumps({'files': {str(ops.CONTROL/'system_ops.py'): digest}}).encode()
        for raw, visible in ((b'RESTORE_STAGE_ACTIVATE\n', True),
                             (b'RESTORE_STAGE_ACTIVATE\nprivate-output\n', False)):
            with self.subTest(visible=visible), \
                    patch.object(ops, 'verify_installation', return_value=('systemd', None)), \
                    patch.object(ops, 'trusted_bytes', return_value=receipt), \
                    patch.object(ops, 'run_command', return_value=(1, raw)):
                code, output = self.run_menu(f'14\n{name}\n{digest}\ny\n0\n')
            self.assertEqual(code, 0)
            self.assertEqual('RESTORE_STAGE_ACTIVATE' in output, visible)
            self.assertIn('恢复未完成', output)
            self.assertNotIn('恢复完成，', output)
            self.assertNotIn('private-output', output)

    def test_core_status_is_read_only_and_shows_current_and_previous(self):
        digest = 'b'*64
        receipt = json.dumps({'files': {str(ops.CONTROL/'system_ops.py'): digest}}).encode()
        summary = dict(outcome='core_selected', cores={
            'sing-box': dict(current='1.14.3', previous='1.14.2'),
            'xray': dict(current='26.3.27', previous=None)})
        with patch.object(ops, 'verify_installation', return_value=('systemd', None)), \
                patch.object(ops, 'trusted_bytes', return_value=receipt), \
                patch.object(ops, 'run_command', return_value=(0, json.dumps(summary).encode())) as run:
            code, output = self.run_menu('20\n0\n')
        self.assertEqual(code, 0)
        self.assertNotIn('确认执行', output)
        self.assertIn('sing-box 已选择 1.14.3，上一个版本 1.14.2', output)
        self.assertEqual(run.call_args.args[0][-1], 'core-status')

    def test_core_restore_cancel_keeps_a_bounded_child_cleanup_window(self):
        child = unittest.mock.Mock()
        child._check.side_effect = ops.Refused('operation interrupted')
        with patch.object(ops, 'Child', return_value=child):
            with self.assertRaisesRegex(ops.Refused, 'operation interrupted'):
                ops.run_command(['/fixed'], [2], cleanup_grace=180)
        child.close.assert_called_once_with(grace=180)

    def test_core_switch_menu_refuses_invalid_or_missing_success_evidence(self):
        receipt = json.dumps({'files': {str(ops.CONTROL/'system_ops.py'): 'b'*64}}).encode()
        for result in ({'outcome': []}, {'outcome': 'switched'}, {'outcome': 'restored_backup'}):
            with self.subTest(result=result), patch.object(ops, 'verify_installation', return_value=('systemd', None)), \
                    patch.object(ops, 'trusted_bytes', return_value=receipt), \
                    patch.object(ops, 'run_command', return_value=(0, json.dumps(result).encode())):
                code, output = self.run_menu('17\nxray\ny\n0\n')
            self.assertEqual(code, 0)
            self.assertIn('恢复未完成', output)
            self.assertNotIn('核心切换完成', output)

    def test_online_upgrade_accepts_y_and_yes_without_other_confirmation(self):
        client = types.SimpleNamespace(request=lambda request: {'id': 'a' * 32, 'state': 'succeeded', 'result': {'upgraded': False}})
        for answer in ('y', 'yes', 'Y'):
            with self.subTest(answer=answer), patch.object(ops, 'fixed_module', return_value=client) as load:
                code, text = self.run_menu('12\n' + answer + '\n0\n')
            self.assertEqual(code, 0)
            self.assertIn('当前已是相同或更高版本', text)
            load.assert_called_once_with('maintenance_client')

    def test_nonroot_noninteractive_and_argument_injection_refused(self):
        for input_tty, output_tty in ((False, True), (True, False), (False, False)):
            self.assertEqual(self.run_menu('0\n', input_tty, output_tty)[0], 1)
        with patch.object(ops.os, 'getuid', return_value=1000), patch.object(ops.os, 'geteuid', return_value=1000), \
                patch.object(ops.sys, 'stderr', io.StringIO()):
            self.assertEqual(ops.main(), 1)

    def test_root_entry_requires_isolated_os_interpreter_and_no_arguments(self):
        for isolated, no_site, executable, arguments in (
            (0, 1, Path(sys.executable), ['v-ui']),
            (1, 0, Path(sys.executable), ['v-ui']),
            (1, 1, Path('/bin/sh'), ['v-ui']),
            (1, 1, Path(sys.executable), ['v-ui', '--unsafe']),
        ):
            with self.subTest(isolated=isolated, no_site=no_site, executable=executable, arguments=arguments), \
                    patch.object(ops.os, 'getuid', return_value=0), patch.object(ops.os, 'geteuid', return_value=0), \
                    patch.object(ops.sys, 'stdin', Terminal('0\n')), patch.object(ops.sys, 'stdout', Terminal()), \
                    patch.object(ops.sys, 'stderr', io.StringIO()), patch.object(ops.sys, 'argv', arguments), \
                    patch.object(ops.sys, 'flags', types.SimpleNamespace(dont_write_bytecode=1, isolated=isolated, no_site=no_site)), \
                    patch.object(ops, 'PYTHON', executable), \
                    patch.object(ops, 'installation_lock', side_effect=AssertionError('no host lock')):
                self.assertEqual(ops.main(), 1)

    def test_root_entry_requires_explicit_no_bytecode_flag(self):
        with patch.object(ops.os, 'getuid', return_value=0), patch.object(ops.os, 'geteuid', return_value=0), \
                patch.object(ops.sys, 'stdin', Terminal('0\n')), patch.object(ops.sys, 'stdout', Terminal()), \
                patch.object(ops.sys, 'stderr', io.StringIO()), patch.object(ops.sys, 'argv', ['v-ui']), \
                patch.object(ops.sys, 'flags', types.SimpleNamespace(dont_write_bytecode=0, isolated=1, no_site=1)), \
                patch.object(ops, 'PYTHON', Path(sys.executable)), \
                patch.object(ops, 'installation_lock', side_effect=AssertionError('no host lock')):
            self.assertEqual(ops.main(), 1)

    def test_long_input_suffix_never_becomes_menu_command(self):
        with patch.object(ops, 'installation_lock', side_effect=AssertionError('host lock')):
            code, text = self.run_menu('x' * 40 + '\n2\nyes\n')
            self.assertEqual(code, 1)
            self.assertNotIn('操作完成', text)
        with patch.object(ops.sys, 'stdin', io.StringIO('a' * 64 + '\n')), redirect_stdout(io.StringIO()):
            self.assertEqual(ops.read_input('hash', 64), 'a' * 64)

    def test_failure_text_does_not_echo_private_data_or_exception(self):
        @contextmanager
        def fake_lock():
            yield
        with patch.object(ops, 'installation_lock', fake_lock), \
                patch.object(ops, 'verify_installation', side_effect=ops.Refused('/private/path secret-token')):
            code, text = self.run_menu('1\n0\n')
        self.assertEqual(code, 0)
        self.assertIn('操作未完成', text)
        self.assertNotIn('secret-token', text)
        self.assertNotIn('/private/path', text)

    def test_hidden_input_restores_terminal_on_success_eof_and_error(self):
        for value in ('private-path\n', '', 'x' * 100):
            with patch.object(ops.sys, 'stdin', Terminal(value)), redirect_stdout(io.StringIO()), \
                    patch.object(ops.termios, 'tcgetattr', return_value=[0, 0, 0, 15]) as get, \
                    patch.object(ops.termios, 'tcsetattr') as put:
                if len(value) > 64:
                    with self.assertRaises(ops.Refused):
                        ops.read_input('hidden', 64, hidden=True)
                else:
                    ops.read_input('hidden', 64, hidden=True)
                self.assertEqual(put.call_count, 2)
                self.assertEqual(put.call_args.args, (999, ops.termios.TCSANOW, [0, 0, 0, 15]))
                self.assertEqual(put.call_args_list[0].args[2][3] & (ops.termios.ECHO | ops.termios.ECHONL), 0)

    def test_failed_final_lock_identity_check_prevents_success_message(self):
        @contextmanager
        def replaced_lock():
            yield
            raise ops.Refused('lock inode replaced')
        with patch.object(ops, 'installation_lock', replaced_lock), \
                patch.object(ops, 'verify_installation', return_value=('systemd', ACCOUNT)), \
                patch.object(ops, 'Services'), patch.object(ops, 'operate', return_value={'event': 'done', 'ok': True}):
            code, text = self.run_menu('2\nyes\n0\n')
        self.assertEqual(code, 0)
        self.assertIn('操作未完成', text)
        self.assertNotIn('操作完成', text)

    def test_already_running_message_is_not_a_success_claim(self):
        @contextmanager
        def fake_lock():
            yield
        with patch.object(ops, 'installation_lock', fake_lock), \
                patch.object(ops, 'verify_installation', return_value=('systemd', ACCOUNT)), \
                patch.object(ops, 'Services'), patch.object(ops, 'operate', return_value={'event': 'already_running'}):
            code, text = self.run_menu('2\nyes\n0\n')
        self.assertEqual(code, 0)
        self.assertIn('未校验当前版本或恢复状态', text)
        self.assertNotIn('操作完成', text)

    def test_recovery_menu_is_explicit_and_cancel_does_not_open_journal(self):
        with patch.object(ops, 'installation_lock', side_effect=AssertionError('no operation')), \
                patch.object(ops, 'verify_installation', side_effect=AssertionError('no data read')):
            code, text = self.run_menu('9\nno\n0\n')
        self.assertEqual(code, 0)
        self.assertIn('只恢复中断记录中指向的原数据目录', text)
        self.assertIn('恢复后仍保持停止', text)
        self.assertNotIn('操作完成', text)

    def test_stage_menu_constructs_no_backend_and_uses_explicit_warning(self):
        @contextmanager
        def fake_lock():
            yield
        with patch.object(ops, 'installation_lock', fake_lock), \
                patch.object(ops, 'verify_installation', return_value=('openrc', ACCOUNT)), \
                patch.object(ops, 'Services', side_effect=AssertionError('no host service query')), \
                patch.object(ops, 'operate', return_value={'event': 'done', 'ok': True, 'prepared_release': 'candidate-123'}) as operate:
            code, text = self.run_menu('8\n/private/fixture.zip\n' + 'a' * 64 + '\nyes\n0\n')
        self.assertEqual(code, 0, text)
        self.assertIn('不是沙箱', text)
        self.assertIn('候选准备完成，未激活', text)
        self.assertNotIn('/private/fixture.zip', text)
        self.assertNotIn('a' * 64, text)
        self.assertIsNone(operate.call_args.args[1])

    def test_network_cancel_and_invalid_input_never_lock_read_or_write(self):
        with patch.object(ops, 'installation_lock', side_effect=AssertionError('no host lock')), \
             patch.object(ops, 'network_open', side_effect=AssertionError('no change')):
            for data in ('11\n\n0\n', '11\ntcp\n3215\nno\n0\n', '11\ntcp\n22\n0\n',
                         '11\nudp\n3215\n\n0\n'):
                code, text = self.run_menu(data)
                self.assertEqual(code, 0, text)

    def test_network_diagnostics_need_no_write_confirmation_or_service_operation(self):
        @contextmanager
        def fake_lock():
            yield
        with patch.object(ops, 'installation_lock', fake_lock), \
             patch.object(ops, 'verify_installation', return_value=('systemd', ACCOUNT)), \
             patch.object(ops, 'network_diagnostics', return_value=['只读诊断 unknown']) as diagnose, \
             patch.object(ops, 'Services', side_effect=AssertionError('no service operation')), \
             patch.object(ops, 'network_open', side_effect=AssertionError('no firewall change')):
            code, text = self.run_menu('10\n0\n')
        self.assertEqual(code, 0, text)
        self.assertIn('只读诊断 unknown', text)
        diagnose.assert_called_once()

    def test_manual_port_confirmation_names_exact_protocol_and_port(self):
        @contextmanager
        def fake_lock():
            yield
        with patch.object(ops, 'installation_lock', fake_lock), \
             patch.object(ops, 'verify_installation', return_value=('systemd', ACCOUNT)), \
             patch.object(ops, 'network_open', return_value='精确规则已回读') as opened:
            code, text = self.run_menu('11\nudp\n20443\nyes\n0\n')
        self.assertEqual(code, 0, text)
        self.assertIn('确认放行 20443/udp [yes/N]', text)
        self.assertEqual(opened.call_args.args[:2], ('udp', 20443))

    def test_real_pty_uses_only_fake_backend_and_cancel_or_eof_does_nothing(self):
        if os.geteuid() == 0:
            self.skipTest('this PTY fixture is deliberately non-root')
        script = '''import importlib.util,sys
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('fixture',sys.argv[1]); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
with patch.object(m.os,'getuid',return_value=0), patch.object(m.os,'geteuid',return_value=0), patch.object(m.os,'umask'), patch.object(m,'PYTHON',m.Path(sys.executable)), patch.object(m.sys,'argv',['v-ui']), patch.object(m,'installation_lock',side_effect=AssertionError('real host action forbidden')):
 raise SystemExit(m.main())
'''
        for end in ('no\n0\n', b'\x04', b'\x03'):
            with self.subTest(end=end), MenuSession([sys.executable, '-B', '-I', '-S', '-c', script, str(SOURCE / 'deploy/system_ops.py')],
                                                    cwd=SOURCE, env=os.environ.copy()) as terminal:
                terminal.expect('请选择: ')
                terminal.send('2\n')
                terminal.expect('确认 [yes/N]: ')
                terminal.send(end)
                text = terminal.finish(130 if end == b'\x03' else 0)
                self.assertNotIn('操作完成', text)
                self.assertNotIn('real host action forbidden', text)

    def test_real_pty_hidden_restore_input_never_echoes_and_restores_on_signals(self):
        if os.geteuid() == 0:
            self.skipTest('this PTY fixture is deliberately non-root')
        script = """import importlib.util,sys
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('fixture',sys.argv[1]); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
with patch.object(m.os,'getuid',return_value=0), patch.object(m.os,'geteuid',return_value=0), patch.object(m.os,'umask'), patch.object(m,'PYTHON',m.Path(sys.executable)), patch.object(m.sys,'argv',['v-ui']), patch.object(m,'installation_lock',side_effect=AssertionError('real host action forbidden')):
 raise SystemExit(m.main())
"""
        for number in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
            with self.subTest(number=number), MenuSession([sys.executable, '-B', '-I', '-S', '-c', script, str(SOURCE / 'deploy/system_ops.py')],
                                                       cwd=SOURCE, env=os.environ.copy()) as terminal:
                terminal.expect('请选择: ')
                before = ops.termios.tcgetattr(terminal.fd)
                terminal.send('6\n')
                terminal.expect('备份绝对路径（留空取消）: ')
                self.assertFalse(ops.termios.tcgetattr(terminal.fd)[3] & ops.termios.ECHO)
                terminal.send('/private/never-echo-this')
                os.kill(terminal.process.pid, number)
                text = terminal.finish(128 + number)
                self.assertNotIn('never-echo-this', text)
                self.assertEqual(ops.termios.tcgetattr(terminal.fd)[3], before[3])

    def test_shell_entry_keeps_fixed_os_python_and_only_existing_sudo_policy(self):
        script = (SOURCE / 'deploy/v-ui').read_text()
        self.assertIn('/usr/bin/env -i ', script)
        self.assertIn('/usr/bin/python3 -B -I -S /usr/local/lib/v-ui/system_ops.py "$@"', script)
        delegation='exec sudo -- /usr/local/sbin/v-ui "$@"'
        self.assertEqual(script.count(delegation),1)
        self.assertLess(script.index('id -u'),script.index(delegation))
        self.assertLess(script.index(delegation),script.index('/usr/bin/env -i'))
        for forbidden in ('su ', 'bootstrap_python', '/var/lib/v-ui', 'PYTHONPATH', 'eval ', 'source ', 'sudoers', 'chmod +s'):
            self.assertNotIn(forbidden, script)


if __name__ == '__main__':
    unittest.main()
