"""Private snapshot/FD lifecycle contracts; no subprocess, network or core."""
from contextlib import ExitStack, contextmanager
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

from app.core_updates.binary import RuntimeArtifact, RuntimeArtifactError
from app.core_updates.selection import bundle_reference, _reference_map
from app.services import proxy_diagnostics as service, proxy_diagnostic_worker as worker


def descriptor(version='1.14.3', core='sing-box'):
    return RuntimeArtifact(bundle_reference(core=core, version=version, target='x86_64-gnu',
        app_source='a'*40, payload_manifest_sha256='b'*64, binary_sha256='c'*64, binary_size=10),
        Path('/verified')/core, owner=os.geteuid(), mode=0o700)


def wire(artifact):
    return dict(reference=_reference_map(artifact.reference), path=str(artifact.path),
                owner=artifact.owner, mode=artifact.mode)


class ParentSnapshotTests(unittest.TestCase):
    def request(self, artifact):
        class Captured(Exception):pass
        captured = []
        dumps = json.dumps
        def record(value, **kwargs):
            captured.append(value)
            return dumps(value, **kwargs)
        manager = Mock();manager.runtime_artifact.return_value = artifact
        module = types.ModuleType('app.services.core_manager')
        module.BIN_DIR, module.core_manager = Path('/legacy'), manager
        with patch.dict(sys.modules, {'app.services.core_manager': module}), \
                patch.object(service.json, 'dumps', side_effect=record), \
                patch.object(service.subprocess, 'Popen', side_effect=Captured), \
                self.assertRaises(Captured):
            service._run_worker({'profile': {}}, time.monotonic()+30, threading.Event(), Mock(), '/unused')
        manager.runtime_artifact.assert_called_once_with('sing-box')
        return captured[0]

    def test_parent_uses_same_snapshot_without_runtime_or_parent_file_hash(self):
        value = descriptor()
        with patch('app.core_updates.binary.open_runtime_artifact', side_effect=AssertionError('parent hash')):
            request = self.request(value)
        self.assertEqual(request, {'profile': {}, 'artifact': wire(value)})
        self.assertNotIn('binary', request)

    def test_legacy_parent_keeps_original_private_binary_request(self):
        request = self.request(None)
        self.assertEqual(request, {'profile': {}, 'binary': '/legacy/sing-box'})

    def test_expired_or_cancelled_request_never_reads_manager_snapshot(self):
        manager=Mock();module=types.ModuleType('app.services.core_manager')
        module.BIN_DIR,module.core_manager=Path('/legacy'),manager
        for cancelled,deadline,code in ((True,time.monotonic()+30,'DIAGNOSTIC_CANCELLED'),
                                       (False,time.monotonic()-1,'DIAGNOSTIC_TIMEOUT')):
            event=threading.Event()
            if cancelled:event.set()
            with patch.dict(sys.modules, {'app.services.core_manager':module}), \
                    self.assertRaises(service.DiagnosticsError) as caught:
                service._run_worker({},deadline,event,Mock(),'/unused')
            self.assertEqual(caught.exception.code,code)
        manager.runtime_artifact.assert_not_called()


class WorkerArtifactTests(unittest.TestCase):
    def test_closed_wire_reconstructs_same_identity_and_rejects_unknown_fields(self):
        value = descriptor();self.assertEqual(worker._decode_artifact(wire(value)), value)
        for bad in ({**wire(value), 'url': 'https://invalid.example'},
                    {**wire(value), 'path': '../relative'},
                    {**wire(value), 'owner': True},
                    wire(descriptor(core='xray')),
                    {**wire(value), 'reference': {'kind': 'arbitrary'}}):
            with self.subTest(bad=bad), self.assertRaises(service.DiagnosticsError):
                worker._decode_artifact(bad)

    def test_descriptor_binding_rejects_different_process_or_fd_before_hash(self):
        value = descriptor()
        with patch.object(worker, 'verify_runtime_artifact_fd') as verify:
            for binary, fd in (('/proc/1/fd/7', 7), ('/verified/sing-box', 7),
                               (f'/proc/{os.getpid()}/fd/7', True)):
                with self.subTest(binary=binary, fd=fd), self.assertRaises(service.DiagnosticsError):
                    worker._check_material(binary, value, fd)
            verify.assert_not_called()

    def test_changed_held_material_is_fixed_unavailable_without_leaking_details(self):
        with patch.object(worker, 'verify_runtime_artifact_fd', side_effect=RuntimeArtifactError('private-path')), \
                self.assertRaises(service.DiagnosticsError) as caught:
            worker._check_material(f'/proc/{os.getpid()}/fd/7', descriptor(), 7)
        self.assertEqual(caught.exception.code, 'CORE_UNAVAILABLE')
        self.assertNotIn('private', str(caught.exception))

    def validate(self, output, *, artifact=None, change_before_check=False):
        binary = f'/proc/{os.getpid()}/fd/7' if artifact is not None else '/legacy/sing-box'
        failures = [None, RuntimeArtifactError('changed')] if change_before_check else None
        child = Mock();child.wait.return_value = 0
        with patch.object(worker.Path, 'is_file', return_value=True), \
                patch.object(worker.os, 'access', return_value=True), \
                patch.object(worker, 'verify_runtime_artifact_fd', side_effect=failures) as verify, \
                patch.object(worker, '_capture', return_value=(0, output)) as capture, \
                patch.object(worker, '_spawn', return_value=child) as spawn, \
                patch.object(worker, '_reap') as reap:
            try:
                worker._validate_core(binary, Path('/private/config'), artifact=artifact,
                                      fd=7 if artifact is not None else None)
            except service.DiagnosticsError as error:
                return error.code, verify.call_count, capture.call_count, spawn.call_count, reap.call_count
            return None, verify.call_count, capture.call_count, spawn.call_count, reap.call_count

    def test_new_version_uses_snapshot_and_rechecks_before_config_execution(self):
        self.assertEqual(self.validate(b'sing-box version 1.14.3\n', artifact=descriptor()), (None, 2, 1, 1, 1))

    def test_new_version_never_accepts_the_old_fixed_version_as_fallback(self):
        self.assertEqual(self.validate(b'sing-box version 1.14.2\n', artifact=descriptor()),
                         ('CORE_VERSION_MISMATCH', 1, 1, 0, 0))

    def test_mutation_between_version_and_config_prevents_config_execution(self):
        self.assertEqual(self.validate(b'sing-box version 1.14.3\n', artifact=descriptor(), change_before_check=True),
                         ('CORE_UNAVAILABLE', 2, 1, 0, 0))

    def test_legacy_keeps_fixed_version_and_original_three_phase_checks(self):
        self.assertEqual(self.validate(b'sing-box version 1.14.2\n'), (None, 0, 1, 1, 1))
        self.assertEqual(self.validate(b'sing-box version 1.14.3\n'), ('CORE_VERSION_MISMATCH', 0, 1, 0, 0))

    def lifecycle(self, failure=None, close_failure=False, extra=None, pipe_failure=False):
        value = descriptor();events = [];emitted = []
        request = dict(profile={}, profile_id='up_'+'a'*32, revision='a'*64,
                       panel_origin=None, artifact=wire(value))
        if extra:request.update(extra)
        @contextmanager
        def opened(artifact):
            self.assertEqual(artifact, value);events.append('open')
            try:yield 7
            finally:
                events.append('close')
                if close_failure:raise RuntimeArtifactError('private close detail')
        def run(converted, _directory, *, artifact, fd):
            self.assertEqual(artifact, value);self.assertEqual(fd, 7)
            self.assertEqual(converted['binary'], f'/proc/{os.getpid()}/fd/7')
            self.assertNotIn('artifact', converted)
            events.append('run');child=Mock();child.poll.return_value=None;worker._CHILDREN.append(child)
            if failure:raise failure
            return '9.9.9.9'
        def reap(child):
            events.append('reap');child.poll.return_value=0
            if pipe_failure:raise OSError('fake pipe cleanup error after death')
            worker._CHILDREN.remove(child)
        self.assertEqual(worker._CHILDREN, [])
        with patch.object(worker.sys, 'argv', ['worker', '1234', '/unused']), \
                patch.object(worker.sys, 'stdin', types.SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))), \
                patch.object(worker.signal, 'signal'), patch.object(worker, '_install_limits'), \
                patch.object(worker, 'open_runtime_artifact', side_effect=opened) as opener, \
                patch.object(worker, '_run', side_effect=run), patch.object(worker, '_reap', side_effect=reap), \
                patch.object(worker, '_emit', side_effect=emitted.append):
            self.assertEqual(worker.main(), 0)
        self.assertEqual(worker._CHILDREN, [])
        return events, emitted, opener.call_count

    def test_worker_holds_fd_until_all_core_children_are_reaped(self):
        events, emitted, count = self.lifecycle()
        self.assertEqual(events, ['open', 'run', 'reap', 'close'])
        self.assertEqual(emitted[-1]['code'], 'DIAGNOSTIC_COMPLETE');self.assertEqual(count, 1)

    def test_cancellation_and_failed_configuration_keep_same_cleanup_order(self):
        for error, code in ((worker._Cancelled(), 'DIAGNOSTIC_CANCELLED'),
                            (service.DiagnosticsError('CORE_CONFIG_REJECTED'), 'CORE_CONFIG_REJECTED')):
            events, emitted, _ = self.lifecycle(error)
            self.assertEqual(events, ['open', 'run', 'reap', 'close'])
            self.assertEqual(emitted[-1]['code'], code)

    def test_cleanup_failure_never_reports_success(self):
        events, emitted, _ = self.lifecycle(close_failure=True)
        self.assertEqual(events, ['open', 'run', 'reap', 'close'])
        self.assertEqual(emitted[-1], {'event': 'result', 'code': 'CORE_UNAVAILABLE', 'exit_ip': None})

    def test_mixed_binary_and_artifact_request_cannot_open_or_execute(self):
        events, emitted, count = self.lifecycle(extra={'binary': '/evil'})
        self.assertEqual(events, []);self.assertEqual(count, 0)
        self.assertEqual(emitted[-1]['code'], 'INVALID_DIAGNOSTIC_REQUEST')

    def test_dead_child_with_pipe_cleanup_error_still_closes_material_and_fails(self):
        events,emitted,_=self.lifecycle(pipe_failure=True)
        self.assertEqual(events,['open','run','reap','close'])
        self.assertEqual(emitted[-1],{'event':'result','code':'DIAGNOSTIC_WORKER_FAILED','exit_ip':None})

    def test_unconfirmed_child_attempts_all_reaps_then_aborts_without_closing_material(self):
        value=descriptor();events=[];emitted=[];held=ExitStack()
        first,second=Mock(),Mock()
        first.poll.return_value=None;second.poll.return_value=None
        request=dict(profile={},profile_id='up_'+'a'*32,revision='a'*64,panel_origin=None,artifact=wire(value))
        @contextmanager
        def opened(_artifact):
            events.append('open')
            try:yield 7
            finally:events.append('close')
        def run(*args,**kwargs):worker._CHILDREN.extend([second,first]);return '9.9.9.9'
        def reap(child):
            events.append('reap-first' if child is first else 'reap-second')
            if child is first:raise OSError('still live')
            child.poll.return_value=0;worker._CHILDREN.remove(child)
        def abort():
            events.append('abort-group')
            # Model whole-group termination while holding ExitStack outside
            # main, so mock return/GC cannot pretend an explicit close is safe.
            first.poll.return_value=-9;worker._CHILDREN.clear()
        try:
            with patch.object(worker,'ExitStack',return_value=held), \
                    patch.object(worker.sys,'argv',['worker','1234','/unused']), \
                    patch.object(worker.sys,'stdin',types.SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))), \
                    patch.object(worker.signal,'signal'),patch.object(worker,'_install_limits'), \
                    patch.object(worker,'open_runtime_artifact',side_effect=opened), \
                    patch.object(worker,'_run',side_effect=run),patch.object(worker,'_reap',side_effect=reap), \
                    patch.object(worker,'_abort_unreaped',side_effect=abort) as terminate, \
                    patch.object(worker,'_emit',side_effect=emitted.append):
                self.assertEqual(worker.main(),70)
            self.assertEqual(events,['open','reap-first','reap-second','abort-group'])
            self.assertEqual(emitted,[]);terminate.assert_called_once_with()
        finally:
            first.poll.return_value=-9;second.poll.return_value=-9
            worker._CHILDREN.clear();held.close()

    def test_abort_cannot_return_and_never_kills_an_unrelated_process_group(self):
        class Terminated(BaseException):pass
        for group,error in ((123,None),(456,None),(123,OSError('synthetic signal failure'))):
            with patch.object(worker.os,'getpid',return_value=123), \
                    patch.object(worker.os,'getpgrp',return_value=group), \
                    patch.object(worker.os,'killpg',side_effect=error) as kill, \
                    patch.object(worker.os,'_exit',side_effect=Terminated) as terminate, \
                    self.assertRaises(Terminated):
                try:worker._abort_unreaped()
                finally:
                    terminate.assert_called_once_with(70)
                    if group==123:kill.assert_called_once_with(123,worker.signal.SIGKILL)
                    else:kill.assert_not_called()


if __name__ == '__main__':unittest.main()
