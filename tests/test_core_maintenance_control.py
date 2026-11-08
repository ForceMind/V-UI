"""Root coordinator order against real temporary M5 files.

Candidate provenance, worker pipes, systemd, native execution and broker I/O
are fixtures. The broker schema and both public metadata transitions are real.
"""
from copy import deepcopy
import hashlib
import json
import sys
import os
from pathlib import Path
import re
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from app.core_updates import artifacts, selection, selection_files, store
from deploy import core_boot_broker as wire
from deploy import core_maintenance_control as control
import test_core_selection as fixtures


CORE = 'sing-box'
PANEL = 'v-ui.service'
SOCKET = 'v-ui-http01.socket'
RESPONDER = 'v-ui-http01.service'
ARCHIVE = 'core-maintenance-' + '7' * 32 + '.zip'
BACKUP_SHA = '8' * 64
TOKEN = '9' * 64
SOURCE = dict(app_source=fixtures.APP, target=fixtures.TARGET)


class FixtureServices:
    def __init__(self, case):
        self.case = case
        self.current = {PANEL: 'active', SOCKET: 'active', RESPONDER: 'inactive'}
        self.pid = 101

    def snapshot(self):
        self.case.trace.append('snapshot')
        return dict(self.current)

    def stop_all(self, emergency=False):
        self.case.trace.append('emergency-stop' if emergency else 'stop')
        if not emergency:
            self.case.at_stop = self.case.files.recovery_view(**SOURCE)
        self.current = dict.fromkeys(self.current, 'inactive')
        self.prove_stopped()

    def prove_stopped(self):
        if any(value != 'inactive' for value in self.current.values()):
            raise AssertionError('writers or boot requested with services still running')
        self.case.trace.append('prove-stopped')

    def restore(self, desired):
        self.case.trace.append('restore-topology')
        self.current = dict(desired)
        if self.current[PANEL] == 'active':
            self.pid += 1
            self.case.trace.append('start-panel')

    def state(self, unit):
        return self.current[unit]

    def managed_identity(self, uid):
        if self.state(PANEL) != 'active':
            raise AssertionError('cannot attest a stopped panel')
        return dict(pid=self.pid, uid=uid, start_identity=str(self.pid), cgroup='/fixture/panel')


class FixtureChild:
    """Scripted service-worker facts; the coordinator still parses the wire."""
    def __init__(self, case):
        self.case = case
        self.pending = None
        self.finished = False
        self.snapshot = None
        self.backup_ack = None

    def send(self, request):
        case = self.case
        if self.pending is not None or self.finished:
            raise AssertionError('unexpected worker request')
        command = request.get('command', request.get('action'))
        case.trace.append(command)
        if command == 'core_prepare':
            result = dict(event='core_context', **deepcopy(case.context))
            if case.fault == 'app-context':
                result['application']['target'] = 'aarch64-gnu'
            elif case.fault == 'worker-extra-field':
                result['unchecked'] = True
        elif command == 'check_core':
            if case.fault == 'cold-check':
                result = dict(event='error', code='MAINTENANCE_REFUSED')
            else:
                result = dict(event='prepared', application=case.context['application'],
                    data_generation=case.context['data_generation'], selection=case.context['selection'],
                    candidate=request['candidate'], intents=case.initial_intents, source_token=TOKEN)
        elif command == 'proceed':
            if case.services.state(PANEL) != 'inactive':
                raise AssertionError('proceed before stop')
            result = dict(event='core_quiesced', final_intents=case.final_intents, source_token=TOKEN)
        elif command == 'backup_core':
            self.snapshot = request['snapshot']
            result = dict(event='core_backed_up', backup_name=ARCHIVE,
                          backup_sha256=BACKUP_SHA,
                          snapshot_sha256=hashlib.sha256(store._canonical(self.snapshot)).hexdigest())
            if case.fault == 'backup-sha':
                result['backup_sha256'] = 'invalid-digest'
            elif case.fault == 'snapshot-sha':
                result['snapshot_sha256'] = '4' * 64
        elif command == 'bind_core_ids':
            self.backup_ack = request['backup_sha256']
            result = dict(event='core_ids_bound', selection=fixtures.raw(case.plan.after),
                          intents=case.intents_for(case.plan.after))
        elif command == 'release_writers_for_trial':
            result = dict(event='trial_writers_released')
        elif command == 'prove_operation':
            result = dict(event='operation_held')
        elif command == 'reacquire_writers':
            if case.services.state(PANEL) != 'inactive' or case.brokers[-1].state != 'closed':
                raise AssertionError('writer reacquisition overlapped a live trial')
            case.at_reacquire = case.files.recovery_view(**SOURCE)
            result = dict(event='writers_reacquired')
        elif command == 'restore_core_ids':
            result = dict(event='core_ids_restored', selection=case.context['selection'], intents=case.final_intents)
        elif command == 'release_reacquired_writers':
            result = dict(event='recovery_writers_released')
        elif command == 'finish':
            self.finished = True
            return
        else:
            raise AssertionError('unexpected fixture command ' + str(command))
        self.pending = deepcopy(result)

    def receive(self):
        if self.pending is None:
            raise AssertionError('missing worker response')
        result, self.pending = self.pending, None
        return result

    def exit_zero(self):
        if not self.finished:
            raise AssertionError('worker exited before finish')
        self.case.trace.append('worker-exit-zero')

    def close(self):
        self.case.trace.append('worker-close')


class FixtureBroker:
    def __init__(self, case, context, *, service_uid, service_gid, proof, identity_query, timeout):
        wire._context(context)
        self.case, self.context = case, deepcopy(context)
        self.purpose, self.proof = context['purpose'], proof
        self.identity_query = identity_query
        self.state = 'new'
        self.final_barriers = None
        self.pending_digest = case.public_barrier(selection_files.PENDING)
        self.at_probe = None
        case.brokers.append(self)

    def event(self, name):
        self.case.trace.append(name + ':' + self.purpose)

    def listen(self):
        self.proof()
        self.event('listen')
        self.state = 'listening'

    def arm(self, identity):
        self.proof()
        if identity != self.identity_query():
            raise AssertionError('broker armed for a different panel')
        self.event('arm')

    def wait_ready(self):
        self.proof()
        self.state = 'ready'
        self.event('ready')

    def request_probe(self):
        self.proof()
        self.event('probe')
        self.at_probe = self.case.files.recovery_view(**SOURCE)
        context = self.context
        observed = {key: context[key] for key in ('app_source', 'target', 'data_generation', 'application', 'barriers')}
        observed['selection_revision'] = context['selection']['revision']
        observed['cores'] = {}
        for core, intent in context['intents'].items():
            active = intent['enabled']
            ref = context['selection']['cores'][core]['current']
            observed['cores'][core] = dict(intent,
                artifact_id=intent['artifact_id'] if active else None,
                binary_sha256=ref['binary_sha256'] if active else None,
                running=active, owned_listeners_ready=active)
        wire._probe(observed)
        rejected = self.purpose == 'trial' and self.case.fault == 'trial-probe'
        return dict(ok=not rejected, observed=observed)

    def release(self, final_barriers):
        self.proof()
        self.event('release')
        self.final_barriers = dict(final_barriers)
        actual = self.case.files.read_stable(**SOURCE)
        if actual.revision != self.context['selection']['revision']:
            raise AssertionError('release requested before durable confirmation')
        self.state = 'released'

    def close(self):
        self.event('close')
        self.state = 'closed'


class CoreMaintenanceControlTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='vui-core-control-')
        self.addCleanup(temporary.cleanup)
        anchor = Path(temporary.name).resolve()
        self.root = anchor/'metadata'
        self.files = selection_files.SelectionFiles(selection_files.Layout(self.root, os.geteuid(), anchor))
        self.before = fixtures.initial()
        self.plan = selection.plan_component(self.before, core=CORE, identity=fixtures.material(),
                                             expected_revision=self.before.revision)
        self.initial_intents = {
            name: dict(artifact_id=selection.reference_identity(self.before.slot(name).current),
                       revision='3'*64 if name == CORE else None, enabled=name == CORE)
            for name in selection.CORES
        }
        self.final_intents = deepcopy(self.initial_intents)
        bundles = {name: selection._reference_map(self.before.slot(name).current) for name in selection.CORES}
        self.context = dict(application=dict(**SOURCE, release_id='fixture-release', current_sha256='1'*64,
                manifest_sha256=bundles[CORE]['payload_manifest_sha256'], ready_sha256='2'*64),
            data_generation=dict(dev=3, ino=7), selection=fixtures.raw(self.before),
            selection_initialized=False, bundles=bundles, intents=self.initial_intents,
            runtime_schemas={CORE: 1, 'xray': None})
        self.trace, self.brokers = [], []
        self.fault, self.at_stop, self.at_reacquire = None, None, None
        self.services = FixtureServices(self)
        self.services.deadline = None
        self.child = FixtureChild(self)
        self.worker = control._Worker(self.child)
        self.addCleanup(self.worker.close)
        # Only the live interfaces and candidate acquisition are replaced.
        # All M5 calls, transition guards and barrier reads use private files.
        broker = types.SimpleNamespace(**vars(wire))
        broker.CoreBootBroker = lambda context, **options: FixtureBroker(self, context, **options)
        metadata = types.SimpleNamespace(**vars(selection_files))
        metadata.SelectionFiles = lambda: self.files
        modules = types.SimpleNamespace(artifacts=artifacts, selection=selection, selection_files=metadata,
            store=types.SimpleNamespace(_canonical=store._canonical, CoreArtifactStore=object),
            catalog=types.SimpleNamespace(CoreCatalog=object), broker=broker)
        ops = types.SimpleNamespace(RELEASE=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}\Z'),
                                    local_panel_tls=self.tls)
        self.switch = control._Switch(ops, modules, self.services, types.SimpleNamespace(pw_uid=501, pw_gid=501),
            self.worker, self.installation_proof, dict(target=fixtures.TARGET),
            deadline=time.monotonic()+10, cancelled=[])

    def test_only_typed_uid_drop_waits_before_arming(self):
        from deploy import system_ops
        self.switch.ops.IdentityStarting=system_ops.IdentityStarting
        identity=object()
        with patch.object(self.switch,'_identity',side_effect=[system_ops.IdentityStarting(),identity]) as check, \
             patch.object(control.time,'sleep') as pause:
            self.assertIs(self.switch._await_identity(),identity)
            self.assertEqual(check.call_count,2);pause.assert_called_once()
        with patch.object(self.switch,'_identity',side_effect=system_ops.Refused('configuration drift')) as check, \
             patch.object(control.time,'sleep') as pause:
            with self.assertRaises(system_ops.Refused):self.switch._await_identity()
            check.assert_called_once();pause.assert_not_called()
        clock=[time.monotonic()]
        self.switch.deadline=clock[0]+60
        def late_identity():
            self.assertLessEqual(self.services.deadline,clock[0]+10)
            clock[0]+=11
            return identity
        with patch.object(control.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(self.switch,'_identity',side_effect=late_identity):
            with self.assertRaisesRegex(control.CoreControlError,'CORE_PANEL_IDENTITY_TIMEOUT'):
                self.switch._await_identity()
        self.assertIsNone(self.services.deadline)

    def test_broker_timeout_cancels_and_joins_callbacks_before_releasing_scope(self):
        calls=[]
        broker=types.SimpleNamespace(callbacks_finished=False)
        def close(**kwargs):
            calls.append(kwargs)
            if len(calls)==1:raise wire.CoreBootBrokerError('THREAD_STILL_RUNNING')
            self.assertTrue(self.switch.cancelled)
            self.assertIs(self.switch.broker,broker)
            broker.callbacks_finished=True
        broker.close=close;self.switch.broker=broker;self.switch.broker_closed=False
        with self.assertRaisesRegex(wire.CoreBootBrokerError,'THREAD_STILL_RUNNING'):self.switch._close_broker()
        self.assertEqual(calls,[{},dict(_join_timeout=15)])
        self.assertIsNone(self.switch.broker);self.assertTrue(self.switch.broker_closed)
        calls.clear();self.switch.cancelled.clear()
        def finished_before_observation(**kwargs):
            calls.append(kwargs)
            if len(calls)==1:raise wire.CoreBootBrokerError('THREAD_STILL_RUNNING')
            self.assertFalse(self.switch.cancelled)
        broker.close=finished_before_observation
        self.switch.broker=broker;self.switch.broker_closed=False
        with self.assertRaisesRegex(wire.CoreBootBrokerError,'THREAD_STILL_RUNNING'):self.switch._close_broker()
        self.assertEqual(calls,[{},dict(_join_timeout=15)])
        self.assertIsNone(self.switch.broker);self.assertTrue(self.switch.broker_closed)

        calls.clear();self.switch.cancelled.clear();broker.callbacks_finished=False
        def finished_on_second_join(**kwargs):
            calls.append(kwargs)
            if len(calls)==2:broker.callbacks_finished=True
            raise wire.CoreBootBrokerError('THREAD_STILL_RUNNING')
        broker.close=finished_on_second_join
        broker._finish_close=lambda:calls.append('cleanup')
        self.switch.broker=broker;self.switch.broker_closed=False
        with self.assertRaisesRegex(wire.CoreBootBrokerError,'THREAD_STILL_RUNNING'):self.switch._close_broker()
        self.assertEqual(calls,[{},dict(_join_timeout=15),'cleanup'])
        self.assertIsNone(self.switch.broker);self.assertTrue(self.switch.broker_closed)

    def test_root_loader_compiles_real_fixed_bytes_and_rejects_receipt_drift(self):
        from scripts import install_ops_control as installer
        root=Path(__file__).resolve().parents[1]
        directory=Path('/fixed-control-fixture')
        receipt=directory/'ops-control.json'
        values={directory/filename:(root/installer.CORE_SOURCE_DEPENDENCIES[filename]).read_bytes()
                for _,filename in control._CONTROL_MODULES}
        values[receipt]=json.dumps(dict(files={str(path):hashlib.sha256(raw).hexdigest()
                                               for path,raw in values.items()})).encode()
        paths=tuple(sys.path)
        ops=types.SimpleNamespace(CONTROL=directory,RECEIPT=receipt,MAX_JSON=16384,MAX_FILE=4_000_000,
            decode_json=json.loads,trusted_bytes=lambda path,limit:values[path])
        with patch.object(control.os,'geteuid',return_value=0):
            with control._modules(ops) as loaded:
                self.assertEqual(loaded.__path__,[])
                self.assertIs(loaded.selection.PackIdentity,loaded.artifacts.PackIdentity)
                self.assertIs(loaded.catalog.Candidate,loaded.github.Candidate)
            values[directory/'core_selection_files.py']+=b'\n# drift'
            with self.assertRaisesRegex(control.CoreControlError,'CORE_CONTROL_CHANGED'):
                with control._modules(ops):pass
        self.assertNotIn('_vui_fixed_core_control',sys.modules)
        self.assertEqual(tuple(sys.path),paths)

    def intents_for(self, state):
        return {name: dict(intent, artifact_id=selection.reference_identity(state.slot(name).current))
                for name, intent in self.final_intents.items()}

    def installation_proof(self):
        self.trace.append('installation-proof')
        return False if self.fault == 'installation-proof' else None

    def tls(self, config, *, require_login):
        if not self.brokers:
            self.trace.append('pre-stop-login')
            return 'ready（fixture）'
        broker = self.brokers[-1]
        mode = 'login' if require_login else 'handshake'
        self.trace.append('tls-' + mode + ':' + broker.purpose)
        if broker.purpose == 'trial' and ((not require_login and self.fault == 'trial-tls')
                                           or (require_login and self.fault == 'post-confirm-gate')):
            return 'rejected（fixture）'
        return 'ready（fixture）'

    def candidate(self, core, action, release_id):
        self.trace.append('candidate')
        self.assertEqual((core, action, release_id), (CORE, 'update', 123))
        self.assertEqual(self.switch.source, SOURCE)
        self.assertEqual(self.switch.before, self.before)
        return self.plan

    def run_switch(self):
        with patch.object(self.switch, '_plan', side_effect=self.candidate):
            return self.switch.run(CORE, 'update', None, 123)

    def assert_order(self, *events):
        index = 0
        for event in events:
            self.assertIn(event, self.trace[index:], self.trace)
            index = self.trace.index(event, index)+1

    def public_barrier(self, name):
        path = self.root/name
        return wire.file_digest(artifacts._file_identity(path.stat()), path.read_bytes())

    def journal(self):
        transaction = self.files.recovery_view(**SOURCE)['transaction']
        with self.files._io(write=True) as (scope, root):
            _, document, _ = self.files._load(scope, root, transaction, **SOURCE)
            return document

    def assert_no_stop(self):
        self.assertNotIn('stop', self.trace)
        self.assertNotIn('emergency-stop', self.trace)
        self.assertNotIn('proceed', self.trace)
        self.assertFalse((self.root/selection_files.PENDING).exists())
        self.assertIsNone(self.files.read_boot(**SOURCE))

    def test_first_use_publishes_only_after_probe_and_preserves_before_backup(self):
        # Keep the selected core enabled and retain the other core's last stop.
        self.context['runtime_schemas']['xray'] = 1
        self.initial_intents['xray'].update(revision='5'*64, enabled=True)
        self.final_intents['xray'].update(revision='5'*64, enabled=False)
        result = self.run_switch()
        self.assertEqual(result, dict(outcome='switched', selection_revision=self.plan.after.revision,
                                     panel_running=True, backup_name=ARCHIVE, backup_sha256=BACKUP_SHA))
        self.assert_order('core_prepare', 'candidate', 'check_core', 'pre-stop-login', 'installation-proof',
            'stop', 'proceed', 'backup_core', 'bind_core_ids', 'release_writers_for_trial',
            'listen:trial', 'start-panel', 'arm:trial', 'ready:trial', 'probe:trial',
            'tls-handshake:trial', 'release:trial', 'tls-login:trial', 'close:trial', 'finish', 'worker-exit-zero')
        self.assertEqual(self.at_stop['phase'], 'prepared')
        self.assertEqual(self.at_stop['pre_stop'], dict(panel_running=True, cores=self.initial_intents))
        self.assertEqual(self.at_stop['actual_files'], dict(selection=None, fence=None))
        self.assertEqual(self.child.snapshot['selection'], fixtures.raw(self.before))
        self.assertEqual(self.child.snapshot['intents'], self.final_intents)
        self.assertEqual(self.child.backup_ack, BACKUP_SHA)
        broker, = self.brokers
        absence = wire.file_digest(None, None)
        self.assertEqual(broker.at_probe['phase'], 'trial')
        self.assertTrue(broker.at_probe['pending'])
        self.assertEqual(broker.context['barriers'], dict(core_pending_sha256=broker.pending_digest,
            core_selection_sha256=absence, core_selection_fence_sha256=absence, restore_pending_sha256=absence))
        self.assertEqual(broker.final_barriers, dict(core_pending_sha256=absence, restore_pending_sha256=absence,
            core_selection_sha256=self.public_barrier(selection_files.SELECTION),
            core_selection_fence_sha256=self.public_barrier(selection_files.FENCE)))
        self.assertEqual(self.files.read_stable(**SOURCE), self.plan.after)
        rows = self.journal()['validation']['cores']
        self.assertTrue(rows[CORE]['owned_listeners_verified'])
        self.assertFalse(rows['xray']['enabled'])
        self.assertFalse(rows['xray']['owned_listeners_verified'])

    def test_initially_stopped_panel_keeps_topology_without_health_attestation(self):
        self.services.current[PANEL] = 'inactive'
        before = dict(self.services.current)
        result = self.run_switch()
        self.assertEqual(result['outcome'], 'switched')
        self.assertFalse(result['panel_running'])
        self.assertEqual(self.services.current, before)
        self.assertEqual(self.brokers, [])
        self.assertNotIn('start-panel', self.trace)
        self.assertNotIn('pre-stop-login', self.trace)
        validation = self.journal()['validation']
        self.assertFalse(validation['panel_running'])
        self.assertFalse(validation['panel_health_verified'])
        self.assertTrue(validation['cores'][CORE]['enabled'])
        self.assertFalse(validation['cores'][CORE]['owned_listeners_verified'])

    def test_failed_cold_check_never_stops_or_starts_a_transaction(self):
        self.fault = 'cold-check'
        with self.assertRaisesRegex(control.CoreControlError, '^CORE_WORKER_PROTOCOL$'):
            self.run_switch()
        self.assert_order('core_prepare', 'candidate', 'check_core')
        self.assert_no_stop()

    def test_unproved_installation_never_stops_or_starts_a_transaction(self):
        self.fault = 'installation-proof'
        with self.assertRaisesRegex(control.CoreControlError, '^INSTALLATION_PROOF$'):
            self.run_switch()
        self.assertIn('installation-proof', self.trace)
        self.assert_no_stop()

    def test_wrong_application_context_rejected_before_candidate_or_stop(self):
        self.fault = 'app-context'
        with self.assertRaisesRegex(control.CoreControlError, '^CORE_APPLICATION_CONTEXT$'):
            self.run_switch()
        self.assertNotIn('candidate', self.trace)
        self.assert_no_stop()

    def test_worker_response_extra_fields_rejected_before_candidate_or_stop(self):
        self.fault = 'worker-extra-field'
        with self.assertRaisesRegex(control.CoreControlError, '^CORE_WORKER_PROTOCOL$'):
            self.run_switch()
        self.assertNotIn('candidate', self.trace)
        self.assert_no_stop()

    def assert_bad_backup_retained(self, fault):
        self.fault = fault
        with self.assertRaisesRegex(control.CoreControlError, '^CORE_RECOVERY_REQUIRED$'):
            self.run_switch()
        self.assert_order('stop', 'proceed', 'backup_core', 'emergency-stop')
        self.assertNotIn('bind_core_ids', self.trace)
        self.assertNotIn('finish', self.trace)
        self.assertEqual(self.brokers, [])
        view = self.files.recovery_view(**SOURCE)
        self.assertTrue(view['pending'])
        self.assertTrue(view['requires_recovery'])
        self.assertEqual(view['phase'], 'quiesced')
        self.assertIsNone(view['backup'])

    def test_malformed_archive_sha_retains_pending_without_binding_ids(self):
        self.assert_bad_backup_retained('backup-sha')

    def test_wrong_backup_snapshot_sha_retains_pending_without_binding_ids(self):
        self.assert_bad_backup_retained('snapshot-sha')

    def assert_trial_rollback(self, fault):
        self.fault = fault
        result = self.run_switch()
        self.assertEqual(result['outcome'], 'restored_previous')
        self.assert_order('start-panel', 'probe:trial', 'close:trial', 'emergency-stop',
            'reacquire_writers', 'restore_core_ids', 'release_reacquired_writers',
            'listen:old_recovery', 'start-panel', 'probe:old_recovery', 'tls-handshake:old_recovery',
            'release:old_recovery', 'tls-login:old_recovery', 'close:old_recovery', 'finish')
        self.assertNotIn('release:trial', self.trace)
        self.assertEqual(self.at_reacquire['phase'], 'rollback_required')
        self.assertTrue(self.at_reacquire['pending'])
        trial, recovery = self.brokers
        self.assertEqual(trial.context['selection'], fixtures.raw(self.plan.after))
        self.assertEqual(recovery.pending_digest, trial.pending_digest)
        restored = self.files.read_stable(**SOURCE)
        self.assertEqual(restored.slots, self.before.slots)
        self.assertGreater(restored.generation, self.plan.after.generation)
        self.assertEqual(recovery.context['selection'], fixtures.raw(restored))
        self.assertEqual(recovery.context['intents'], self.final_intents)
        self.assertEqual(result['selection_revision'], restored.revision)
        self.assertEqual(self.services.state(PANEL), 'active')

    def test_probe_failure_closes_trial_before_reacquiring_and_boots_old_generation(self):
        self.assert_trial_rollback('trial-probe')

    def test_preconfirmation_tls_failure_reacquires_and_repairs_only_old_ids(self):
        self.assert_trial_rollback('trial-tls')
        self.assertIn('tls-handshake:trial', self.trace)

    def test_confirmed_selection_remains_when_public_login_gate_never_opens(self):
        self.fault = 'post-confirm-gate'
        with self.assertRaisesRegex(control.CoreControlError, '^CORE_RECOVERY_REQUIRED$'):
            self.run_switch()
        self.assert_order('probe:trial', 'tls-handshake:trial', 'release:trial',
                          'tls-login:trial', 'close:trial', 'emergency-stop')
        self.assertNotIn('reacquire_writers', self.trace)
        self.assertNotIn('restore_core_ids', self.trace)
        self.assertNotIn('finish', self.trace)
        self.assertEqual(len(self.brokers), 1)
        self.assertEqual(self.files.read_stable(**SOURCE), self.plan.after)
        self.assertFalse((self.root/selection_files.PENDING).exists())
        self.assertEqual(self.services.state(PANEL), 'inactive')


if __name__ == '__main__':
    unittest.main()
