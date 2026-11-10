"""Root coordinator order against real temporary M5 files.

Candidate provenance, worker pipes, systemd, native execution and broker I/O
are fixtures. The broker schema and both public metadata transitions are real.
"""
from copy import deepcopy
from contextlib import nullcontext
import hashlib
from io import StringIO
import json
import sys
import os
from pathlib import Path
import re
import select
import signal
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from app.core_updates import artifacts, selection, selection_files, store
from deploy import core_boot_broker as wire
from deploy import core_maintenance_control as control
from deploy import ops_worker
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
        self.recovering = False
        self.confirmed_selection = None

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
        elif command in ('core_backup','core_backup_recover'):
            self.backup_only = True
            result = dict(event='core_context', **dict(case.context, selection_initialized=True))
        elif command == 'core_recover':
            self.recovering = True
            self.confirmed_selection = request.get('confirmed_selection')
            result = dict(event='core_recovery_prepared', transaction=request['transaction'],
                          application=case.context['application'], data_generation=case.context['data_generation'])
        elif command == 'probe_panel_tls':
            digest = getattr(self, 'restore_trust', 'b'*64)
            if case.fault == 'provided-ca-drift' and case.brokers and case.brokers[-1].purpose == 'trial':
                digest = 'c'*64
            result = dict(event='panel_tls', status='ready', trust_sha256=digest)
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
            if self.recovering:
                state = (selection.decode_selection(selection_files._raw(self.confirmed_selection), **SOURCE)
                         if self.confirmed_selection is not None else case.before)
                result = dict(event='core_recovery_bound', selection=selection_files._selection_map(state),
                              final_intents=case.intents_for(state), changed_cores=[])
            elif getattr(self, 'backup_only', False):
                result = dict(event='core_quiesced', final_intents=case.final_intents)
            else:
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
        elif command == 'release_backup_writers':
            result = dict(event='backup_writers_released')
        elif command == 'prove_operation':
            result = dict(event='operation_held')
        elif command in ('core_restore_observe', 'core_restore_observe_old', 'core_restore_observe_early'):
            journal = ops_worker._restore_observer_request(request)
            self.restore_observer_request = journal
            if 'tls' in journal:
                self.restore_trust = journal['tls']['trust_sha256']
            result = dict(event='restore_observer_ready', transaction=journal['transaction'])
            if journal.get('early'):
                result['final_intents'] = self.final_restore_intents
            if case.fault == 'restore-observer-ready-tx':
                result['transaction'] = '0'*32
        elif command in ('core_restore_exchange', 'core_restore_rollback'):
            journal = ops_worker._restore_exchange_request(request)
            receipt = getattr(self, 'restore_receipt', None)
            if receipt is None:
                receipt = dict(transaction=journal['transaction'], sequence=journal['sequence'],
                    data=case.restore_trial_data, tree_sha256=journal['quiesced']['prepared_tree_sha256'])
            case.assertEqual(journal['sequence'], receipt['sequence'])
            result = dict(event='restore_data_exchanged', receipt=receipt)
        elif command == 'core_restore_prepare':
            result = dict(event='restore_preflight', context=dict(case.context, selection_initialized=True),
                incoming_selection=fixtures.raw(case.before), incoming_intents=case.initial_intents,
                incoming_generation=case.restore_trial_data['live'], preflight_tree_sha256='b'*64,
                original_trust_sha256=None, incoming_trust_sha256=None)
        elif command == 'stage_restore':
            case.services.prove_stopped()
            case.assertEqual(request['journal']['phase'], 'prepared')
            result = dict(event='restore_staged', final_intents=case.initial_intents,
                restore_data=dict(live=case.restore_trial_data['original'], original=None,
                                  incoming=case.restore_trial_data['live'], rejected=None),
                original_tree_sha256='d'*64, prepared_tree_sha256='e'*64)
        elif command == 'observe_restore_data':
            frames = []
            with patch('app.core_updates.backup_archive.observe_restore_trial', return_value=self.restore_data):
                ops_worker._observe_restore_data(
                    types.SimpleNamespace(send=lambda event, **fields: frames.append(dict(event=event, **fields))),
                    types.SimpleNamespace(root=case.root, _operation_proof=lambda: None),
                    case.files.read_restore_recovery(), request)
            result, = frames
            if (case.fault == 'restore-observation-tx' or case.fault == 'restore-late-observation'
                    and case.trace.count('observe_restore_data') == 2):
                result['transaction'] = '0'*32
        elif command == 'reacquire_writers':
            if case.services.state(PANEL) != 'inactive' or case.brokers[-1].state != 'closed':
                raise AssertionError('writer reacquisition overlapped a live trial')
            case.at_reacquire = case.files.recovery_view(**SOURCE)
            result = dict(event='writers_reacquired')
        elif command == 'restore_core_ids':
            result = dict(event='core_ids_restored', selection=case.context['selection'], intents=case.final_intents)
        elif command in ('release_reacquired_writers', 'release_recovery_writers'):
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

    def listen(self, *, recover_stale=False, rename_noreplace=None):
        self.case.assertEqual(recover_stale, self.purpose == 'old_recovery')
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
            store=types.SimpleNamespace(_canonical=store._canonical, _rename_new=store._rename_new,
                                        CoreArtifactStore=object),
            catalog=types.SimpleNamespace(CoreCatalog=object), broker=broker)
        self.accepted_origin={key:self.context['application'][key] for key in ('release_id','target','app_source','manifest_sha256')}
        self.accepted_origin['archive_sha256']='f'*64
        ops = types.SimpleNamespace(RELEASE=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}\Z'),
                                    application_origin=lambda release_id,target:deepcopy(self.accepted_origin),
                                    revoke_core_epoch=lambda:None,
                                    local_panel_tls=self.tls)
        self.switch = control._Switch(ops, modules, self.services, types.SimpleNamespace(pw_uid=501, pw_gid=501),
            self.worker, self.installation_proof, dict(target=fixtures.TARGET),
            deadline=time.monotonic()+10, cancelled=[])

    def test_restore_task_rejects_changed_receipt_under_installation_lock(self):
        ops = types.SimpleNamespace(TIMEOUT=1, MAX_JSON=16384, RECEIPT=self.root/'receipt',
            installation_lock=lambda **_: nullcontext(lambda: None),
            trusted_bytes=lambda *args: b'refreshed receipt',
            verify_installation=lambda: self.fail('must reject before controller dispatch'))
        with self.assertRaisesRegex(control.CoreControlError, 'CORE_CONTROL_CHANGED'):
            control._run_task(ops, [], None, restore_recovery=True, receipt_sha256='a'*64)

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

    def test_remote_admission_refusal_prevents_first_pending_and_service_stop(self):
        def refuse(summary,**kwargs):raise control.CoreControlError('ROOT_PERMISSION_REVOKED')
        self.switch.admission_guard=types.SimpleNamespace(live=lambda:None,enter=refuse)
        with self.assertRaisesRegex(control.CoreControlError,'ROOT_PERMISSION_REVOKED'):self.run_switch()
        self.assertFalse((self.root/selection_files.PENDING).exists())
        self.assertEqual(self.services.state(PANEL),'active')
        self.assertNotIn('stop',self.trace)

    def test_private_ca_uses_worker_and_keeps_the_saved_trust_on_trial_drift(self):
        self.switch.config['certificate_mode']='provided'
        self.switch.ops.local_panel_tls=lambda *args,**kwargs:self.fail('private CA bypassed worker')
        self.fault='provided-ca-drift'
        result=self.run_switch()
        self.assertEqual(result['outcome'],'restored_previous')
        self.assertEqual(self.switch.trust_sha256,'b'*64)
        self.assert_order('probe_panel_tls','stop','probe:trial','probe_panel_tls','close:trial',
                          'reacquire_writers','probe:old_recovery','probe_panel_tls','release:old_recovery')

    def test_fresh_root_recovery_reuses_saved_topology_and_refuses_changed_config(self):
        scan={core:dict(schema=1 if row['revision'] else None, revision=row['revision'], enabled=row['enabled'])
              for core,row in self.initial_intents.items()}
        service=dict(panel_running=True,cores=self.initial_intents)
        self.files.bootstrap(self.before,writer_observation=scan,service_snapshot=service)
        context=dict(application=self.context['application'],data_generation=self.context['data_generation'],
            services=dict(self.services.current),config_sha256=selection_files._digest(selection_files._raw(self.switch.config)),
            certificate_mode='managed',trust_sha256=None)
        tx=self.files.begin(self.plan,expected_revision=self.before.revision,
                            service_snapshot=service,switch_context=context)
        self.switch.config['port']=9443
        with self.assertRaisesRegex(control.CoreControlError,'CORE_RECOVERY_CONFIG_CHANGED'):
            self.switch.run_recovery()
        self.assertNotIn('stop',self.trace);self.assertNotIn('core_recover',self.trace)
        self.switch.config.pop('port')
        self.services.current[SOCKET]='inactive'
        result=self.switch.run_recovery()
        self.assertEqual(result['outcome'],'restored_previous')
        self.assertEqual(self.services.current,context['services'])
        self.assertEqual(self.files.read_stable(**SOURCE).slots,self.before.slots)
        self.assertEqual(self.files.recovery_view(**SOURCE)['transaction'],tx)
        self.assert_order('core_recover','stop','proceed','release_recovery_writers','probe:old_recovery',
                          'release:old_recovery','finish')

    def assert_confirmed_restart(self, *, rollback):
        if rollback:
            self.fault = 'trial-probe'
        with patch.object(self.files, '_archive_and_clear', side_effect=OSError('interrupted after confirm')):
            with self.assertRaisesRegex(control.CoreControlError, 'CORE_RECOVERY_REQUIRED'):
                self.run_switch()
        durable = self.files.read_switch_recovery()
        self.assertEqual(durable['phase'], 'confirmed')
        self.assertEqual(durable['rollback'] is not None, rollback)
        confirmed = selection.decode_selection(selection_files._raw(durable['confirmed_selection']), **SOURCE)
        self.assertEqual(confirmed.slots, self.before.slots if rollback else self.plan.after.slots)
        if rollback:
            self.assertGreater(confirmed.generation, self.plan.after.generation)
        self.fault = None
        self.worker.close()
        self.child = FixtureChild(self)
        self.worker = control._Worker(self.child)
        self.addCleanup(self.worker.close)
        self.switch.worker = self.worker
        with patch.object(self.files, 'rollback_metadata', side_effect=AssertionError('confirmed state reverted')):
            result = self.switch.run_recovery()
        self.assertEqual(result['outcome'], 'resumed_confirmed')
        self.assertEqual(self.files.read_stable(**SOURCE), confirmed)
        self.assertEqual(self.child.confirmed_selection, durable['confirmed_selection'])
        self.assert_order('core_recover', 'stop', 'proceed', 'release_recovery_writers',
                          'probe:old_recovery', 'release:old_recovery', 'finish')

    def test_interrupted_confirmed_update_resumes_exact_candidate(self):
        self.assert_confirmed_restart(rollback=False)

    def test_interrupted_confirmed_rollback_resumes_exact_old_generation(self):
        self.assert_confirmed_restart(rollback=True)

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

    def restore_trial_fixture(self, *, provided=False, panel_running=True, quiesced=True):
        switch = self.switch
        if not panel_running:
            self.services.current[PANEL] = 'inactive'
        switch.source = SOURCE
        switch.application = self.context['application']
        switch.initial = self.services.snapshot()
        before = self.context['data_generation']
        incoming = dict(dev=before['dev'], ino=before['ino']+1)
        snapshot = dict(panel_running=panel_running, cores=self.initial_intents)
        if provided:
            switch.config['certificate_mode'] = 'provided'
        self.files.bootstrap(self.before, writer_observation={
            name: dict(schema=1, revision=row['revision'], enabled=row['enabled'])
            if row['revision'] is not None else dict(schema=None, revision=None, enabled=False)
            for name, row in self.initial_intents.items()}, service_snapshot=snapshot)
        switch.transaction = self.files.begin_restore(self.before, **SOURCE,
            expected_revision=self.before.revision, service_snapshot=snapshot,
            source_backup_sha256='a'*64, restored_intents=self.initial_intents,
            switch_context=dict(application=switch.application, data_generation=before,
                services=switch.initial, config_sha256=selection_files._digest(selection_files._raw(switch.config)),
                certificate_mode='provided' if provided else 'managed', trust_sha256='f'*64 if provided else None),
            data_plan=dict(slot='b'*32, incoming_generation=incoming,
                preflight_tree_sha256='c'*64, incoming_trust_sha256='e'*64 if provided else None))
        self.services.stop_all()
        staged = dict(live=before, original=None, incoming=incoming, rejected=None)
        trial = dict(live=incoming, original=before, incoming=None, rejected=None)
        if not quiesced:
            journal = self.files.read_restore_recovery()
            return dict(transaction=switch.transaction, sequence=journal['sequence'],
                        data=dict(staged, incoming=None), tree_sha256=None), trial, incoming
        self.files.record_quiesced(switch.transaction, dict(panel_stopped=True,
            writers_exclusive=True, cores=self.initial_intents), restore_data=staged,
            original_tree_sha256='d'*64, prepared_tree_sha256='e'*64, **SOURCE)
        self.files.record_backup(switch.transaction, dict(kind='backup-components-v1',
            name=ARCHIVE, sha256=BACKUP_SHA, selection_revision=self.before.revision,
            backup_id=selection_files._digest((ARCHIVE+':'+BACKUP_SHA).encode()),
            intents_sha256=selection_files._digest(selection_files._raw(self.initial_intents))), **SOURCE)
        journal = self.files.read_restore_recovery()
        receipt = dict(transaction=switch.transaction, sequence=journal['sequence'], data=trial, tree_sha256='e'*64)
        return receipt, trial, incoming

    def test_restore_trial_broker_uses_incoming_generation_and_confirms_current_data(self):
        switch = self.switch
        receipt, trial, incoming = self.restore_trial_fixture()
        switch._start_restore_observer()
        with self.assertRaisesRegex(selection_files.CoreSelectionFilesError, 'RESTORE_RECEIPT'):
            switch._activate_restore_trial(dict(receipt, sequence=0), lambda: trial)
        self.assertFalse(self.brokers)
        self.child.restore_data = trial
        self.fault = 'restore-observation-tx'
        with self.assertRaisesRegex(control.CoreControlError, 'CORE_RESTORE_CONTEXT'):
            switch._activate_restore_trial(receipt)
        self.assertEqual(self.files.read_restore_recovery()['phase'], 'backed_up')
        self.assertFalse(self.brokers)
        self.fault = None
        state = switch._activate_restore_trial(receipt)
        self.assertEqual(self.trace.count('observe_restore_data'), 4)
        self.assertEqual(self.brokers[0].context['data_generation'], incoming)
        self.assertEqual(self.files.read_stable(**SOURCE), state)

    def remote_backup_fixture(self, *, running=True):
        from deploy import core_authority
        self.files.bootstrap(self.before,writer_observation={
            name:dict(schema=1,revision=row['revision'],enabled=row['enabled'])
            if row['revision'] is not None else dict(schema=None,revision=None,enabled=False)
            for name,row in self.initial_intents.items()},
            service_snapshot=dict(panel_running=running,cores=self.initial_intents))
        if not running:self.services.current=dict.fromkeys(self.services.current,'inactive')
        self.authority=core_authority.CoreAuthority(_root=self.root.parent/'authority',
            _anchor=self.root.parent,_owner=os.geteuid())
        _,self.backup_token=self.authority.issue(main_id=7,main_digest='a'*64,scopes=['core_backup'])
        self.remote_sequence=1
        intent,_=self.authority.reserve(token=self.backup_token,main_id=7,main_digest='a'*64,
            request_id=format(self.remote_sequence,'032x'),action='backup',
            parameters={'selection_revision':self.before.revision})
        self.switch.admission_guard=control._AdmissionGuard(self.authority,intent)
        return intent

    def interrupt_remote_backup(self):
        actual=self.worker.command
        def failed(command,*args,**kwargs):
            if command in ('backup_core','release_backup_writers'):
                raise BrokenPipeError('inert exited backup worker')
            return actual(command,*args,**kwargs)
        with patch.object(self.worker,'command',side_effect=failed):
            with self.assertRaises(BrokenPipeError):self.switch.run_backup(self.before.revision)
        return self.authority.backup_recovery()

    def new_backup_recovery(self):
        pending=self.authority.backup_recovery()
        self.remote_sequence+=1
        intent,_=self.authority.reserve(token=self.backup_token,main_id=7,main_digest='a'*64,
            request_id=format(self.remote_sequence,'032x'),action='recover',parameters=dict(
            recovery_kind='backup',pending_transaction=pending['transaction'],pending_sequence=pending['sequence'],
            selection_revision=pending['before']['revision']))
        old=self.switch
        self.child=FixtureChild(self);self.worker=control._Worker(self.child)
        self.addCleanup(self.worker.close)
        self.switch=control._Switch(old.ops,old.modules,self.services,old.account,self.worker,
            self.installation_proof,dict(old.config),deadline=time.monotonic()+10,cancelled=[])
        self.switch.admission_guard=control._AdmissionGuard(self.authority,intent)
        return pending

    def test_remote_backup_retains_original_root_context_before_stop_and_clears_success(self):
        intent=self.remote_backup_fixture()
        actual=self.services.stop_all
        def stop(*args,**kwargs):
            pending=self.authority.backup_recovery()
            self.assertEqual(pending['transaction'],intent['id'])
            self.assertEqual(pending['context']['facts']['application'],self.context['application'])
            self.assertEqual(pending['context']['services'][PANEL],'active')
            self.assertEqual(self.authority.admission(task_id=intent['id'],live=False)['phase'],'mutating')
            return actual(*args,**kwargs)
        with patch.object(self.services,'stop_all',side_effect=stop):
            self.switch.run_backup(self.before.revision)
        self.assertIsNone(self.authority.backup_recovery())

    def test_backup_pending_save_unknown_fsync_never_reaches_first_service_stop(self):
        self.remote_backup_fixture()
        original=os.fsync;directory=self.authority.root.stat()
        def lost(fd):
            info=os.fstat(fd)
            if (info.st_dev,info.st_ino)==(directory.st_dev,directory.st_ino):
                raise OSError('inert mutation directory ack loss')
            original(fd)
        with patch('deploy.core_authority.os.fsync',side_effect=lost):
            with self.assertRaises(ValueError):self.switch.run_backup(self.before.revision)
        self.assertNotIn('stop',self.trace)
        self.assertNotIn('emergency-stop',self.trace)
        self.assertEqual(self.services.current[PANEL],'active')
        pending=self.authority.backup_recovery()
        self.assertEqual(pending['transaction'],self.switch.admission_guard.intent['id'])
        self.assertEqual(self.authority.admission(task_id=pending['transaction'],live=False)['phase'],'mutating')

    def test_backup_io_worker_exit_leaves_durable_context_without_replaying_archive(self):
        self.remote_backup_fixture();pending=self.interrupt_remote_backup()
        self.assertIsNotNone(pending)
        self.assertTrue(all(state=='inactive' for state in self.services.current.values()))
        before=list(self.trace)
        from deploy import core_authority
        reloaded=core_authority.CoreAuthority(_root=self.root.parent/'authority',
            _anchor=self.root.parent,_owner=os.geteuid())
        self.assertEqual(reloaded.backup_recovery(),pending)
        self.assertEqual(self.trace,before)
        self.assertEqual(self.files.read_stable(**SOURCE),self.before)
        self.assertNotIn('restore-topology',self.trace)

    def test_sigkill_after_fixture_stop_retains_root_context_without_automatic_replay(self):
        self.remote_backup_fixture()
        read,write=os.pipe()
        child=os.fork()
        if child==0:
            os.close(read)
            original=self.services.stop_all
            def stopped(*args,**kwargs):
                original(*args,**kwargs)
                os.write(write,b'S')
                while True:signal.pause()
            self.services.stop_all=stopped
            try:self.switch.run_backup(self.before.revision)
            except BaseException:os._exit(2)
            os._exit(3)
        os.close(write)
        try:
            self.assertTrue(select.select([read],[],[],5)[0],'fixture task did not stop')
            self.assertEqual(os.read(read,1),b'S')
        finally:
            os.close(read)
            os.kill(child,signal.SIGKILL)
            _,status=os.waitpid(child,0)
        self.assertTrue(os.WIFSIGNALED(status))
        self.assertEqual(os.WTERMSIG(status),signal.SIGKILL)
        pending=self.authority.backup_recovery()
        self.assertEqual(pending['context']['services'][PANEL],'active')
        self.assertEqual(self.files.read_stable(**SOURCE),self.before)
        self.assertNotIn('backup_core',self.trace)
        self.assertEqual(self.authority.backup_recovery(),pending)

    def test_explicit_backup_recovery_resumes_original_services_without_backing_up(self):
        self.remote_backup_fixture();self.interrupt_remote_backup()
        pending=self.new_backup_recovery();self.trace.clear()
        result=self.switch.run_backup_recovery(pending)
        self.assertEqual(result,dict(outcome='backup_recovered',selection_revision=self.before.revision,panel_running=True))
        self.assertIsNone(self.authority.backup_recovery())
        self.assert_order('core_backup_recover','stop','proceed','release_backup_writers','start-panel','finish')
        self.assertNotIn('backup_core',self.trace)
        self.assertEqual(self.files.read_stable(**SOURCE),self.before)

    def test_explicit_backup_recovery_preserves_original_stopped_topology_and_latest_core_stop(self):
        self.remote_backup_fixture(running=False);self.interrupt_remote_backup()
        self.final_intents[CORE]['enabled']=False
        pending=self.new_backup_recovery();self.trace.clear()
        result=self.switch.run_backup_recovery(pending)
        self.assertFalse(result['panel_running'])
        self.assertFalse(self.switch.final[CORE]['enabled'])
        self.assertTrue(all(state=='inactive' for state in self.services.current.values()))
        self.assertNotIn('start-panel',self.trace)
        self.assertIsNone(self.authority.backup_recovery())

    def test_local_recovery_reuses_original_context_without_remote_bearer_or_admission(self):
        self.remote_backup_fixture();self.interrupt_remote_backup()
        pending=self.new_backup_recovery()
        # The existing explicit local Root command owns the installation lock;
        # it uses its receipt-bound authority rather than a remote capability.
        self.switch.admission_guard=None
        self.switch.backup_authority=self.authority
        self.authority.revoke_main(main_id=7,main_digest='a'*64)
        self.trace.clear()
        with patch.object(self.authority,'admission',side_effect=AssertionError('local recovery needs no bearer')):
            result=self.switch.run_backup_recovery(pending)
        self.assertEqual(result['outcome'],'backup_recovered')
        self.assertTrue(result['panel_running'])
        self.assertIsNone(self.authority.backup_recovery())
        self.assertNotIn('backup_core',self.trace)

    def test_backup_recovery_interruption_retains_original_record_for_fresh_authorization(self):
        self.remote_backup_fixture();self.interrupt_remote_backup()
        pending=self.new_backup_recovery()
        with patch.object(self.worker,'command',side_effect=BrokenPipeError('inert recovery exit')):
            with self.assertRaises(BrokenPipeError):self.switch.run_backup_recovery(pending)
        self.assertEqual(self.authority.backup_recovery(),pending)
        self.assertTrue(all(state=='inactive' for state in self.services.current.values()))
        self.assertEqual(self.new_backup_recovery(),pending)
        self.assertEqual(self.switch.run_backup_recovery(pending)['outcome'],'backup_recovered')
        self.assertIsNone(self.authority.backup_recovery())

    def test_backup_recovery_context_or_config_change_refuses_before_stop(self):
        self.remote_backup_fixture();self.interrupt_remote_backup()
        for field in ('application','data_generation','selection','config'):
            pending=self.new_backup_recovery();self.trace.clear()
            previous=deepcopy(self.context)
            if field=='application':self.context[field]['ready_sha256']='f'*64
            elif field=='data_generation':self.context[field]['ino']+=1
            elif field=='selection':self.context[field]['generation']+=1
            else:self.switch.config['listen_port']=12345
            with self.assertRaises((control.CoreControlError,artifacts.CoreArtifactError,selection.CoreSelectionError)):
                self.switch.run_backup_recovery(pending)
            self.assertNotIn('stop',self.trace)
            self.assertEqual(self.authority.backup_recovery(),pending)
            self.context=previous

    def test_backup_recovery_revoked_or_wrong_pending_cas_never_stops_services(self):
        self.remote_backup_fixture();self.interrupt_remote_backup()
        pending=self.new_backup_recovery();self.trace.clear()
        self.switch.admission_guard.intent['parameters']['pending_sequence']=2
        with self.assertRaises(control.CoreControlError):self.switch.run_backup_recovery(pending)
        self.assertNotIn('stop',self.trace)
        pending=self.new_backup_recovery();self.trace.clear()
        self.authority.revoke_main(main_id=7,main_digest='a'*64)
        with self.assertRaises(ValueError):self.switch.run_backup_recovery(pending)
        self.assertNotIn('stop',self.trace)
        self.assertEqual(self.authority.backup_recovery(),pending)

    def test_backup_recovery_cleanup_unknown_does_not_stop_restored_service(self):
        self.remote_backup_fixture();self.interrupt_remote_backup()
        pending=self.new_backup_recovery();self.trace.clear()
        actual=self.authority.finish_backup_recovery
        original=os.fsync;directory=self.authority.root.stat()
        def fail_directory(fd):
            info=os.fstat(fd)
            if (info.st_dev,info.st_ino)==(directory.st_dev,directory.st_ino):
                raise OSError('inert cleanup directory ack loss')
            original(fd)
        def lost(value):
            with patch('deploy.core_authority.os.fsync',side_effect=fail_directory):actual(value)
        with patch.object(self.authority,'finish_backup_recovery',side_effect=lost):
            with self.assertRaises(ValueError):self.switch.run_backup_recovery(pending)
        self.assertEqual(self.services.current[PANEL],'active')
        self.assertIsNone(self.authority.backup_recovery())
        self.assertNotIn('emergency-stop',self.trace)

    def test_private_backup_keeps_selection_and_restores_topology(self):
        self.files.bootstrap(self.before, writer_observation={
            name: dict(schema=1, revision=row['revision'], enabled=row['enabled'])
            if row['revision'] is not None else dict(schema=None, revision=None, enabled=False)
            for name, row in self.initial_intents.items()},
            service_snapshot=dict(panel_running=True, cores=self.initial_intents))
        result = self.switch.run_backup(self.before.revision)
        self.assertEqual(result, dict(outcome='backup_created', backup_name=ARCHIVE,
                                     backup_sha256=BACKUP_SHA, panel_running=True))
        self.assert_order('core_backup', 'stop', 'proceed', 'backup_core',
                          'release_backup_writers', 'start-panel', 'finish')
        self.assertEqual(self.files.read_stable(**SOURCE), self.before)
        self.assertFalse((self.root/selection_files.PENDING).exists())
        self.assertFalse(self.brokers)

    def test_failed_backup_releases_writers_and_restores_original_topology(self):
        self.files.bootstrap(self.before, writer_observation={
            name: dict(schema=1, revision=row['revision'], enabled=row['enabled'])
            if row['revision'] is not None else dict(schema=None, revision=None, enabled=False)
            for name, row in self.initial_intents.items()},
            service_snapshot=dict(panel_running=True, cores=self.initial_intents))
        original=self.services.snapshot()
        self.fault='backup-sha'
        with self.assertRaisesRegex(control.CoreControlError,'CORE_BACKUP_RECEIPT'):
            self.switch.run_backup(self.before.revision)
        self.assertEqual(self.services.snapshot(),original)
        self.assert_order('backup_core','release_backup_writers','restore-topology','finish')
        self.assertEqual(self.files.read_stable(**SOURCE),self.before)
        self.assertFalse((self.root/selection_files.PENDING).exists())

    def test_interrupted_backup_with_unconfirmed_writer_release_remains_stopped(self):
        self.files.bootstrap(self.before, writer_observation={
            name: dict(schema=1, revision=row['revision'], enabled=row['enabled'])
            if row['revision'] is not None else dict(schema=None, revision=None, enabled=False)
            for name, row in self.initial_intents.items()},
            service_snapshot=dict(panel_running=True, cores=self.initial_intents))
        with patch.object(self.switch,'_write_backup',side_effect=KeyboardInterrupt),patch.object(
                self.switch.worker,'command',wraps=self.switch.worker.command) as command:
            actual=command._mock_wraps
            def uncertain(name,*args,**kwargs):
                if name=='release_backup_writers':raise RuntimeError('inert worker loss')
                return actual(name,*args,**kwargs)
            command.side_effect=uncertain
            with self.assertRaises(KeyboardInterrupt):self.switch.run_backup(self.before.revision)
        self.assertTrue(all(state=='inactive' for state in self.services.snapshot().values()))
        self.assertNotIn('restore-topology',self.trace)
        self.assertEqual(self.files.read_stable(**SOURCE),self.before)

    def test_private_restore_coordinates_preflight_before_stop_and_trial_after_backup(self):
        before = self.context['data_generation']
        incoming = dict(dev=before['dev'], ino=before['ino']+1)
        self.restore_trial_data = dict(live=incoming, original=before, incoming=None, rejected=None)
        self.files.bootstrap(self.before, writer_observation={
            name: dict(schema=1, revision=row['revision'], enabled=row['enabled'])
            if row['revision'] is not None else dict(schema=None, revision=None, enabled=False)
            for name, row in self.initial_intents.items()},
            service_snapshot=dict(panel_running=True, cores=self.initial_intents))
        exchange, observer = FixtureChild(self), FixtureChild(self)
        observer.restore_data = self.restore_trial_data
        with patch.object(control, '_start_worker', side_effect=[control._Worker(exchange), control._Worker(observer)]):
            result = self.switch.run_restore(ARCHIVE, BACKUP_SHA, self.before.revision)
        self.assertEqual(result['outcome'], 'restored_backup')
        self.assert_order('core_restore_prepare', 'stop', 'stage_restore', 'backup_core',
                          'core_restore_exchange', 'core_restore_observe', 'start-panel', 'release:trial')
        self.assertEqual(self.files.read_stable(**SOURCE).revision, result['selection_revision'])
        self.assertTrue(all(child.finished for child in (self.child, exchange, observer)))

    def test_restore_worker_failure_frame_is_closed_and_never_success(self):
        for request, frame, code in (
            ({'command': 'stage_restore'}, {'event': 'restore_stage_failed', 'phase': 'PLACE'},
             'RESTORE_STAGE_STAGE_PLACE'),
            ({'command': 'stage_restore'}, {'event': 'restore_stage_failed', 'phase': 'PRIVATE'},
             'CORE_WORKER_PROTOCOL'),
            ({'command': 'stage_restore'}, {'event': 'restore_stage_failed', 'phase': 'PLACE', 'extra': 'PRIVATE'},
             'CORE_WORKER_PROTOCOL'),
            ({'command': 'other'}, {'event': 'restore_stage_failed', 'phase': 'PLACE'}, 'CORE_WORKER_PROTOCOL'),
        ):
            with self.subTest(request=request, frame=frame):
                child = types.SimpleNamespace(send=lambda value: None, receive=lambda: frame)
                with self.assertRaisesRegex(control.CoreControlError, '^'+code+'$'):
                    control._Worker(child).exchange(request, 'restore_staged')

    def test_restore_worker_reports_one_fixed_failure_frame_even_if_cleanup_fails(self):
        class Session:
            restore_stage = 'PREPARE'

            def prepare(self):
                return {}

            def stage_restore(self, journal):
                raise ValueError('private staging details')

            def close(self):
                raise OSError('private cleanup details')

        read, write = os.pipe()
        self.addCleanup(os.close, read)
        with os.fdopen(write, 'wb') as requests:
            requests.write(b'{"protocol":1,"action":"core_restore_prepare","archive_name":"fixture",'
                           b'"archive_sha256":"fixture","expected_selection_revision":"fixture"}\n'
                           b'{"command":"stage_restore","journal":{}}\n')
        output = StringIO()
        with patch.object(ops_worker.os, 'geteuid', return_value=1000), \
                patch.object(ops_worker, '_load_core_session', return_value=lambda *a, **kw: Session()), \
                patch.object(ops_worker, '_quiet_candidate_io', side_effect=nullcontext):
            self.assertEqual(ops_worker._run(None, None, read, output), 1)
        self.assertEqual([json.loads(line) for line in output.getvalue().splitlines()], [
            {'event': 'restore_preflight'}, {'event': 'restore_stage_failed', 'phase': 'PREPARE'},
        ])

    def test_restore_recovery_moves_data_without_confirming_or_starting_service(self):
        receipt, trial, _ = self.restore_trial_fixture()
        data = dict(live=trial['original'], original=None, incoming=None, rejected=trial['live'])
        self.child.restore_receipt = dict(receipt, data=data, tree_sha256='d'*64)
        result = self.switch._recover_restore_data()
        self.assertEqual(result, self.child.restore_receipt)
        self.assertEqual(self.services.state(PANEL), 'inactive')
        self.assertEqual(self.files.read_restore_recovery()['phase'], 'backed_up')
        self.assertTrue((self.root/selection_files.PENDING).exists())
        self.assertFalse(self.brokers)

    def restore_original_fixture(self, *, panel_running=True):
        receipt, trial, _ = self.restore_trial_fixture(panel_running=panel_running)
        data = dict(live=trial['original'], original=None, incoming=None, rejected=trial['live'])
        self.child.restore_receipt = dict(receipt, data=data, tree_sha256='d'*64)
        observer = FixtureChild(self)
        observer.restore_data = data
        return observer, trial

    def test_restore_recovery_boots_original_generation_before_clearing_pending(self):
        observer, trial = self.restore_original_fixture()
        result = self.switch._resume_restore_original(_new_worker=lambda: control._Worker(observer))
        self.assertEqual(result['outcome'], 'restored_previous')
        broker, = self.brokers
        self.assertEqual(broker.context['data_generation'], trial['original'])
        self.assertEqual(broker.purpose, 'old_recovery')
        self.assert_order('core_restore_rollback', 'worker-exit-zero', 'core_restore_observe_old',
                          'start-panel', 'probe:old_recovery', 'release:old_recovery')
        self.assertFalse((self.root/selection_files.PENDING).exists())
        self.assertEqual(self.files.read_stable(**SOURCE).revision, result['selection_revision'])

    def test_early_restore_recovery_uses_observed_intent_instead_of_pre_stop_snapshot(self):
        receipt, trial, _ = self.restore_trial_fixture(quiesced=False)
        self.child.restore_receipt = receipt
        observer = FixtureChild(self)
        observer.restore_data = receipt['data']
        observer.final_restore_intents = deepcopy(self.initial_intents)
        observer.final_restore_intents[CORE]['enabled'] = False
        result = self.switch._resume_restore_original(_new_worker=lambda: control._Worker(observer))
        self.assertEqual(result['outcome'], 'restored_previous')
        broker, = self.brokers
        self.assertEqual(broker.context['data_generation'], trial['original'])
        self.assertFalse(broker.context['intents'][CORE]['enabled'])
        self.assertNotIn('core_restore_exchange', self.trace)
        self.assertFalse((self.root/selection_files.PENDING).exists())

    def test_restore_recovery_keeps_originally_stopped_panel_stopped(self):
        observer, _ = self.restore_original_fixture(panel_running=False)
        result = self.switch._resume_restore_original(_new_worker=lambda: control._Worker(observer))
        self.assertFalse(result['panel_running'])
        self.assertEqual(self.services.state(PANEL), 'inactive')
        self.assertFalse(self.brokers)
        self.assertIn('offline', observer.restore_observer_request)
        self.assertEqual(self.files.read_stable(**SOURCE).revision, result['selection_revision'])

    def test_confirmed_stopped_restore_cleanup_does_not_start_panel(self):
        receipt, trial, _ = self.restore_trial_fixture(panel_running=False)
        self.child.restore_data = trial
        self.switch._start_restore_observer()
        with patch.object(self.files, '_archive_and_clear', side_effect=OSError('fixture interruption')):
            with self.assertRaises(selection_files.CoreSelectionFilesError):
                self.switch._activate_restore_trial(receipt)
        journal = self.files.read_restore_recovery()
        observer = FixtureChild(self)
        observer.restore_data = trial
        self.switch.worker = control._Worker(observer)
        result = self.switch._resume_confirmed_restore(journal)
        self.assertFalse(result['panel_running'])
        self.assertFalse(self.brokers)
        self.assertIn('offline', observer.restore_observer_request)
        self.assertEqual(self.files.read_stable(**SOURCE).revision, result['selection_revision'])

    def test_confirmed_restore_cleanup_keeps_incoming_generation_without_exchange(self):
        receipt, trial, incoming = self.restore_trial_fixture()
        with patch.object(self.files, '_archive_and_clear', side_effect=OSError('fixture interruption')):
            with self.assertRaises(selection_files.CoreSelectionFilesError):
                self.switch._activate_restore_trial(receipt, lambda: trial)
        journal = self.files.read_restore_recovery()
        self.assertEqual(journal['phase'], 'confirmed')
        self.assertTrue((self.root/selection_files.PENDING).exists())
        self.switch.worker.close()
        observer = FixtureChild(self)
        observer.restore_data = trial
        self.switch.worker = control._Worker(observer)
        result = self.switch._resume_confirmed_restore(journal)
        self.assertEqual(result['outcome'], 'resumed_confirmed')
        self.assertEqual(self.brokers[-1].context['data_generation'], incoming)
        self.assertNotIn('core_restore_rollback', self.trace)
        self.assertNotIn('core_restore_exchange', self.trace)
        self.assertEqual(self.files.read_stable(**SOURCE).revision, result['selection_revision'])

    def test_confirmed_restore_rollback_cleanup_keeps_original_generation(self):
        observer, trial = self.restore_original_fixture()
        with patch.object(self.files, '_archive_and_clear', side_effect=OSError('fixture interruption')):
            with self.assertRaises(selection_files.CoreSelectionFilesError):
                self.switch._resume_restore_original(_new_worker=lambda: control._Worker(observer))
        journal = self.files.read_restore_recovery()
        self.assertIsNotNone(journal['rollback'])
        resumed = FixtureChild(self)
        resumed.restore_data = observer.restore_data
        self.switch.worker = control._Worker(resumed)
        result = self.switch._resume_confirmed_restore(journal)
        self.assertEqual(result['outcome'], 'resumed_confirmed')
        self.assertEqual(self.brokers[-1].context['data_generation'], trial['original'])
        self.assertEqual(self.trace.count('core_restore_rollback'), 1)
        self.assertEqual(self.files.read_stable(**SOURCE).revision, result['selection_revision'])

    def test_restore_recovery_late_observation_failure_stops_and_keeps_pending(self):
        observer, _ = self.restore_original_fixture()
        self.fault = 'restore-late-observation'
        with self.assertRaisesRegex(control.CoreControlError, 'CORE_RESTORE_DATA_CHANGED'):
            self.switch._resume_restore_original(_new_worker=lambda: control._Worker(observer))
        self.assertIn('probe:old_recovery', self.trace)
        self.assertNotIn('release:old_recovery', self.trace)
        self.assertEqual(self.services.state(PANEL), 'inactive')
        self.assertTrue((self.root/selection_files.PENDING).exists())

    def test_restore_handoff_finishes_writer_before_creating_observer(self):
        receipt, trial, incoming = self.restore_trial_fixture()
        observer = FixtureChild(self)
        observer.restore_data = trial
        def new_worker():
            self.assertTrue(self.child.finished)
            self.assertTrue(self.worker._closed)
            self.trace.append('new-observer')
            return control._Worker(observer)
        with self.assertRaisesRegex(selection_files.CoreSelectionFilesError, 'RESTORE_RECEIPT'):
            self.switch._handoff_restore_trial(dict(receipt, sequence=0), _new_worker=new_worker)
        self.assertFalse(self.child.finished)
        self.child.restore_receipt = receipt
        state = self.switch._exchange_and_activate_restore_trial(_new_worker=new_worker)
        self.assert_order('core_restore_exchange', 'worker-exit-zero', 'worker-close', 'new-observer',
                          'core_restore_observe', 'start-panel', 'release:trial')
        self.assertTrue(observer.finished)
        self.assertEqual(self.brokers[0].context['data_generation'], incoming)
        self.assertEqual(self.files.read_stable(**SOURCE), state)

    def test_restore_handoff_closes_failed_observer_without_starting_service(self):
        receipt, _, _ = self.restore_trial_fixture()
        self.fault = 'restore-observer-ready-tx'
        observer = control._Worker(FixtureChild(self))
        with self.assertRaisesRegex(control.CoreControlError, 'CORE_RESTORE_CONTEXT'):
            self.switch._handoff_restore_trial(receipt, _new_worker=lambda: observer)
        self.assertTrue(observer._closed)
        self.assertEqual(self.services.state(PANEL), 'inactive')
        self.assertEqual(self.files.read_restore_recovery()['phase'], 'backed_up')
        self.assertFalse(self.brokers)

    def test_restore_trial_cancelled_during_last_observation_retains_pending(self):
        receipt, trial, _ = self.restore_trial_fixture()
        count = 0
        def observe():
            nonlocal count
            count += 1
            if count == 3:
                self.switch.cancelled.append(True)
            return trial
        with self.assertRaisesRegex(control.CoreControlError, 'CORE_TASK_CANCELLED'):
            self.switch._activate_restore_trial(receipt, observe)
        self.assertEqual(self.files.read_restore_recovery()['phase'], 'validated')
        self.assertNotIn('release:trial', self.trace)
        self.assertEqual(self.services.state(PANEL), 'inactive')

    def test_restore_trial_provided_observer_pins_incoming_trust(self):
        receipt, trial, _ = self.restore_trial_fixture(provided=True)
        self.child.restore_data = trial
        self.switch._start_restore_observer()
        state = self.switch._activate_restore_trial(receipt)
        self.assertEqual(self.child.restore_trust, 'e'*64)
        self.assertEqual(self.trace.count('probe_panel_tls'), 2)
        self.assertEqual(self.files.read_stable(**SOURCE), state)

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

    def test_root_origin_absent_or_conflicting_rejects_before_candidate_and_stop(self):
        original=deepcopy(self.accepted_origin)
        cases=[None,{**original,'app_source':'b'*40},{**original,'manifest_sha256':'b'*64},
               {**original,'release_id':'other-release'},{**original,'target':'aarch64-gnu'},
               {**original,'archive_sha256':True},{**original,'private_path':'/private/source'}]
        for value in cases:
            with self.subTest(value=value):
                self.accepted_origin=value
                with self.assertRaisesRegex(control.CoreControlError,'CORE_APPLICATION_ORIGIN'):
                    self.switch._accept_context(deepcopy(self.context),CORE,None)
                self.assertNotIn('stop',self.trace)
        self.accepted_origin=original

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
