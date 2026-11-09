"""Closed private worker protocol with fake session and temporary OS pipes.

The isolated loader test only imports copied stdlib Python sources. No core,
service, product install, network, Root metadata or candidate execution occurs.
"""
from contextlib import contextmanager
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

from deploy import ops_worker as w


def command(name,**fields):return dict(command=name,**fields)


class FakeCoreSession:
    def __init__(self,root,trace,**kwargs):
        self.trace=trace;self.closed=0;self._operation_proof=lambda:self.trace.append('proof')
    def prepare(self):self.trace.append('context');return dict(application=dict(app_source='a'*40,target='x86_64-gnu'))
    def check_core(self,value):self.trace.append('coldcheck');return dict(candidate=value)
    def execute(self):self.trace.append('execute');return dict(final_intents={})
    def backup_core(self,value):self.trace.append('backup');return dict(backup_name='private.zip',backup_sha256='a'*64)
    def bind_core_ids(self,value):self.trace.append('bind');return dict(selection={})
    def release_writers_for_trial(self):self.trace.append('trial_release')
    def reacquire_writers(self):self.trace.append('reacquire')
    def _check_reacquired_writers(self):self.trace.append('writer_guard')
    def restore_core_ids(self):self.trace.append('repair');return dict(selection={})
    def release_reacquired_writers(self):self.trace.append('recovery_release')
    def probe_panel_tls(self, *, require_login):
        self.trace.append('tls-login' if require_login else 'tls-handshake')
        return dict(status='ready', trust_sha256='d'*64)
    def close(self):self.trace.append('close');self.closed+=1


class FakeRecoverySession(FakeCoreSession):
    def __init__(self, root, trace, **arguments):
        super().__init__(root, trace)
        self.arguments = arguments

    def prepare(self):
        self.trace.append('recovery_prepare')
        return dict(transaction=self.arguments['transaction'], application=dict(app_source='a'*40),
                    data_generation=dict(dev=1, ino=2))

    def execute(self):
        self.trace.append('recovery_bind')
        return dict(selection=self.arguments['before'], final_intents={}, changed_cores=['sing-box'])

    def release_recovery_writers(self):
        self.trace.append('recovery_release')


class CorePrepareWorkerTests(unittest.TestCase):
    def request(self):return dict(protocol=1,action='core_prepare',core='sing-box',expected_selection_revision=None)
    def prefix(self):return [command('check_core',candidate={'kind':'fake-reference'}),command('proceed'),
        command('backup_core',snapshot={'kind':'fake-snapshot'}),command('bind_core_ids',backup_sha256='a'*64),
        command('release_writers_for_trial')]
    def run_worker(self,commands,*,request=None,close_error=False,guard_error=False,proof_result=None,uid=501,
                   timeout=1,hold=False,session_type=FakeCoreSession,prepare_error=False):
        trace=[];sessions=[];read,write=os.pipe();output=io.StringIO()
        def factory(root,**kwargs):
            instance=session_type(root,trace,**kwargs)
            if prepare_error:
                def prepare():
                    trace.append('prepare-refused')
                    raise ValueError('private failure context')
                instance.prepare=prepare
            if close_error:
                def close():instance.closed+=1;trace.append('close');raise RuntimeError('close lost ack')
                instance.close=close
            if guard_error:
                def guard():trace.append('writer_guard');raise RuntimeError('revoked guard')
                instance._check_reacquired_writers=guard
            if proof_result is not None:instance._operation_proof=lambda:proof_result
            sessions.append(instance);return instance
        @contextmanager
        def quiet():yield
        os.write(write,(''.join(json.dumps(item)+'\n' for item in [request or self.request()]+commands)).encode())
        if not hold:os.close(write);write=None
        try:
            with patch.object(w,'_load_core_session',return_value=factory),patch.object(w,'_quiet_candidate_io',new=quiet), \
                    patch.object(w.os,'geteuid',return_value=uid),patch.object(w.os,'umask'):
                status=w._run(Path('/explicit-fake-root'),types.SimpleNamespace(),read,output,timeout=timeout)
        finally:
            os.close(read)
            if write is not None:os.close(write)
        return status,[json.loads(line) for line in output.getvalue().splitlines()],trace,sessions

    def test_private_restore_dispatch_refuses_extra_fields(self):
        request = dict(protocol=1, action='core_restore_observe', transaction='a'*32,
            slot='b'*32, pending=dict(identity=[1]*9, sha256='c'*64, mode=0o644),
            original=dict(dev=1, ino=2), incoming=dict(dev=1, ino=3))
        exchange = dict(request, action='core_restore_exchange', sequence=3,
                        original_tree_sha256='d'*64, prepared_tree_sha256='e'*64)
        rollback = dict(exchange, action='core_restore_rollback',
                        original_tree_sha256=None, prepared_tree_sha256=None)
        for value, handler in ((request, '_complete_restore_observer'), (exchange, '_complete_restore_exchange'),
                               (rollback, '_complete_restore_exchange')):
            with self.subTest(action=value['action']), patch.object(w, handler) as complete:
                status, _, _, sessions = self.run_worker([], request=value)
                self.assertEqual(status, 0)
                self.assertEqual(sessions, [])
                self.assertEqual(complete.call_args.args[3]['transaction'], value['transaction'])
                complete.reset_mock()
                status, frames, _, _ = self.run_worker([], request=dict(value, path='/unexpected'))
                self.assertEqual(status, 1)
                self.assertEqual(frames, [dict(event='error', code='MAINTENANCE_REFUSED')])
                complete.assert_not_called()

    def test_restore_stage_journal_uses_its_bounded_frame_only(self):
        for stage, size, accepted in ((False, 9000, False), (True, 9000, True), (True, 17000, False)):
            with self.subTest(stage=stage, size=size):
                reader, writer = socket.socketpair()
                with reader, writer:
                    value = dict(command='stage_restore', journal={'fixture': 'a'*size})
                    payload = (json.dumps(value)+'\n').encode()
                    def send():
                        try:
                            writer.sendall(payload)
                        except OSError:
                            pass  # The reader may reject the bounded frame early.
                    sender = threading.Thread(target=send)
                    sender.start()
                    channel = w._Channel(reader.fileno(), io.StringIO(), time.monotonic()+2)
                    try:
                        if accepted:
                            self.assertEqual(channel.read(restore_stage=stage), value)
                        else:
                            with self.assertRaisesRegex(w.WorkerError, 'line exceeded'):
                                channel.read(restore_stage=stage)
                    finally:
                        reader.shutdown(socket.SHUT_RDWR)
                        sender.join(2)
                        self.assertFalse(sender.is_alive())

    def recovery_request(self, **overrides):
        request = dict(protocol=1, action='core_recover', transaction='a'*32, context={}, before={},
                       after={}, final_intents=None, backup=None, allow_after_ids=False)
        return dict(request, **overrides)

    def run_recovery_worker(self, commands, *, request=None, **options):
        return self.run_worker(commands, request=self.recovery_request() if request is None else request,
                               session_type=FakeRecoverySession, **options)

    def test_private_recovery_releases_writers_before_serial_proof_and_tls(self):
        request = self.recovery_request(before={'fixture': 'old-selection'})
        status, events, trace, sessions = self.run_recovery_worker([
            command('proceed'), command('release_recovery_writers'), command('prove_operation'),
            command('probe_panel_tls', require_login=False), command('probe_panel_tls', require_login=True),
            command('prove_operation'), command('finish')], request=request)
        self.assertEqual(status, 0)
        self.assertEqual(events, [
            dict(event='core_recovery_prepared', transaction='a'*32, application=dict(app_source='a'*40),
                 data_generation=dict(dev=1, ino=2)),
            dict(event='core_recovery_bound', selection=request['before'], final_intents={}, changed_cores=['sing-box']),
            dict(event='recovery_writers_released'), dict(event='operation_held'),
            dict(event='panel_tls', status='ready', trust_sha256='d'*64),
            dict(event='panel_tls', status='ready', trust_sha256='d'*64), dict(event='operation_held')])
        self.assertEqual(trace, ['recovery_prepare', 'recovery_bind', 'recovery_release',
                                 'proof', 'tls-handshake', 'tls-login', 'proof', 'close'])
        self.assertEqual(sessions[0].arguments, {k:v for k,v in request.items() if k not in ('action','protocol')})

    def test_confirmed_recovery_selection_is_optional_and_requires_exact_final_intent(self):
        selection = {'fixture': 'confirmed'}
        request = self.recovery_request(confirmed_selection=selection, final_intents={})
        status, _, trace, sessions = self.run_recovery_worker([
            command('proceed'), command('release_recovery_writers'), command('finish')], request=request)
        self.assertEqual(status, 0)
        self.assertEqual(trace, ['recovery_prepare', 'recovery_bind', 'recovery_release', 'close'])
        self.assertEqual(sessions[0].arguments['confirmed_selection'], selection)
        for overrides in (dict(confirmed_selection=None, final_intents={}),
                          dict(confirmed_selection=[], final_intents={}),
                          dict(confirmed_selection=selection)):
            status, events, trace, sessions = self.run_recovery_worker(
                [command('proceed')], request=self.recovery_request(**overrides))
            self.assertEqual((status, trace, sessions), (1, [], []))
            self.assertEqual(events, [dict(event='error', code='MAINTENANCE_REFUSED')])
        self.assertNotIn('confirmed_selection', w._core_recovery_request(self.recovery_request()))

    def test_recovery_rejects_invalid_request_and_unproven_prepare_before_writers(self):
        from app import release_tools
        self.assertNotIn('core_recover', release_tools._MaintenanceSession.ACTIONS)
        with self.assertRaises(w.WorkerError):
            w._request(self.recovery_request())
        for changes in (dict(protocol=True),dict(transaction='BAD'),dict(context=[]),dict(before=None),
                        dict(final_intents=[]),dict(allow_after_ids=1),dict(allow_after_ids=True),
                        dict(backup={'path':'/untrusted.zip'}),dict(path='/untrusted'),dict(_bundle_anchor='/')):
            with self.subTest(changes=changes):
                status, events, trace, sessions = self.run_recovery_worker(
                    [command('proceed')], request=self.recovery_request(**changes))
                self.assertEqual((status, trace, sessions), (1, [], []))
                self.assertEqual(events, [dict(event='error',code='MAINTENANCE_REFUSED')])
        status, events, trace, _ = self.run_recovery_worker([command('proceed')], prepare_error=True)
        self.assertEqual(status, 1)
        self.assertEqual(trace, ['prepare-refused', 'close'])
        self.assertEqual(events, [dict(event='error',code='MAINTENANCE_REFUSED')])

    def test_recovery_phase_command_set_never_reacquires_or_runs_update(self):
        proceed, release = command('proceed'), command('release_recovery_writers')
        tls = command('probe_panel_tls', require_login=True)
        for commands in ([tls], [command('prove_operation')], [command('proceed', approved=True)],
                         [proceed,tls], [proceed,command('finish')],
                         [proceed,release,command('reacquire_writers')],
                         [proceed,release,command('restore_core_ids')],
                         [proceed,release,command('backup_core', snapshot={})],
                         [proceed,release,command('prove_operation', path='/unsafe')],
                         [proceed,release,dict(tls, host='127.0.0.1')]):
            with self.subTest(commands=commands):
                status, events, trace, sessions = self.run_recovery_worker(commands)
                self.assertEqual(status, 1)
                self.assertEqual(events[-1], dict(event='error',code='MAINTENANCE_REFUSED'))
                self.assertFalse(set(trace) & {'reacquire', 'repair', 'backup', 'proof', 'tls-login'})
                self.assertEqual(sessions[0].closed, 1)
        status, events, trace, _ = self.run_recovery_worker(
            [proceed, release, command('prove_operation')], proof_result=True)
        self.assertEqual(status, 1)
        self.assertNotIn('operation_held', [event['event'] for event in events])

    def test_recovery_only_initial_frame_can_hold_three_full_selections(self):
        from app.core_updates import artifacts, selection, selection_files
        import test_core_selection as fixture

        def reference(core, seed):
            prefix, suffix = f'core-{core}-v', '-r123456789'
            version = '1'*(100-len(prefix)-len(suffix)-4)+'.1.1'
            material = replace(fixture.material(core, version=version, target='aarch64-musl', seed=seed),
                tag=prefix+version+suffix, release_id=2**63-1, binary_size=artifacts.MAX_EXPANDED)
            return selection.component_reference(material)

        before = selection._build(fixture.APP, 'aarch64-musl', selection.MAX_GENERATION-1,
            tuple(selection.CoreSlot(core, reference(core,core+'1'), reference(core,core+'2'))
                  for core in selection.CORES))
        after = selection._next(before, 'sing-box', reference('sing-box','candidate'),
                                before.slot('sing-box').current, 'component').after
        digest = 'b'*64
        context = dict(application=dict(app_source=fixture.APP,target=before.target,release_id='r'*101,
                current_sha256=digest,manifest_sha256=digest,ready_sha256=digest),
            data_generation=dict(dev=2**64-1,ino=2**64-1),
            services={'v-ui.service':'active','v-ui-http01.service':'inactive','v-ui-http01.socket':'inactive'},
            config_sha256=digest,certificate_mode='provided',trust_sha256=digest)
        self.assertEqual(selection_files._switch_context(context,before,True), context)
        intents = {core:dict(artifact_id=selection.reference_identity(before.slot(core).current),
                             revision=digest,enabled=True) for core in selection.CORES}
        receipt = dict(kind='backup-components-v1', name='core-maintenance-'+'1'*32+'.zip',
                       backup_id=digest, sha256=digest, selection_revision=before.revision, intents_sha256=digest)
        request = self.recovery_request(context=context, before=fixture.raw(before), after=fixture.raw(after),
                                        confirmed_selection=fixture.raw(after), final_intents=intents,
                                        backup=receipt, allow_after_ids=True)
        raw = json.dumps(request,separators=(',',':')).encode()+b'\n'
        self.assertGreater(len(raw), w.LINE_LIMIT)
        self.assertLessEqual(len(raw), w.RECOVERY_REQUEST_LIMIT)
        left, right = socket.socketpair()
        with left, right:
            right.settimeout(1)
            right.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, w.RECOVERY_REQUEST_LIMIT*2)
            right.sendall(raw+b'{"command":"proceed"}\n')
            channel = w._Channel(left.fileno(),io.StringIO(),time.monotonic()+1)
            self.assertEqual(channel.read(initial=True),request)
            self.assertEqual(w._core_recovery_request(request)['backup'],receipt)
            channel.expect('proceed')
        for value, initial in ((raw,False),(raw.replace(b'core_recover',b'core_prepare'),True),
                               (raw[:-1]+b' '*w.RECOVERY_REQUEST_LIMIT+b'\n',True)):
            with self.subTest(initial=initial, length=len(value)):
                left, right = socket.socketpair()
                with left, right:
                    right.settimeout(1)
                    right.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,w.RECOVERY_REQUEST_LIMIT*2)
                    right.sendall(value)
                    channel = w._Channel(left.fileno(),io.StringIO(),time.monotonic()+1)
                    with self.assertRaisesRegex(w.WorkerError,'Protocol line exceeded limit'):
                        channel.read(initial=initial)
    def test_two_step_source_context_precedes_candidate_and_success_finish(self):
        status,events,trace,sessions=self.run_worker(self.prefix()+[command('prove_operation'),command('finish')])
        self.assertEqual(status,0)
        self.assertEqual([v['event'] for v in events],['core_context','prepared','core_quiesced','core_backed_up',
            'core_ids_bound','trial_writers_released','operation_held'])
        self.assertEqual(trace,['context','coldcheck','execute','backup','bind','trial_release','proof','close'])
        self.assertEqual(sessions[0].closed,1)
    def test_abort_while_root_prepares_material_never_coldchecks_or_stops(self):
        status,events,trace,_=self.run_worker([command('finish')])
        self.assertEqual(status,0);self.assertEqual([v['event'] for v in events],['core_context'])
        self.assertEqual(trace,['context','close'])
    def test_recovery_guard_repair_terminal_release_and_real_proof_order(self):
        status,events,trace,_=self.run_worker(self.prefix()+[command(name) for name in (
            'prove_operation','reacquire_writers','restore_core_ids','release_reacquired_writers','prove_operation','finish')])
        self.assertEqual(status,0)
        self.assertEqual([v['event'] for v in events][-5:],['operation_held','writers_reacquired','core_ids_restored',
                                                           'recovery_writers_released','operation_held'])
        self.assertEqual(trace.count('execute'),1)
        self.assertLess(trace.index('writer_guard'),trace.index('repair'))
        self.assertLess(trace.index('repair'),trace.index('recovery_release'))

    def test_panel_tls_stays_on_private_serial_pipe_before_stop_during_trial_and_after_recovery(self):
        login = command('probe_panel_tls', require_login=True)
        handshake = command('probe_panel_tls', require_login=False)
        commands = self.prefix()
        commands.insert(1, login)
        commands += [handshake, command('prove_operation'), login, command('reacquire_writers'),
                     command('restore_core_ids'), command('release_reacquired_writers'), handshake,
                     command('prove_operation'), login, command('finish')]
        status, events, trace, _ = self.run_worker(commands)
        self.assertEqual(status, 0)
        replies = [reply for reply in events if reply['event'] == 'panel_tls']
        self.assertEqual(replies, [dict(event='panel_tls', status='ready', trust_sha256='d'*64)]*5)
        checks = [step for step in trace if step in {'tls-login', 'tls-handshake', 'execute', 'trial_release',
                                                     'proof', 'reacquire', 'repair', 'recovery_release'}]
        self.assertEqual(checks, ['tls-login', 'execute', 'trial_release', 'tls-handshake', 'proof',
                                  'tls-login', 'reacquire', 'repair', 'recovery_release',
                                  'tls-handshake', 'proof', 'tls-login'])

    def test_panel_tls_rejects_endpoints_material_wrong_types_and_writer_phases(self):
        probe = command('probe_panel_tls', require_login=True)
        attempts = [
            [probe],
            self.prefix()[:1]+[command('probe_panel_tls', require_login=False)],
            self.prefix()[:2]+[probe],
            self.prefix()+[command('reacquire_writers'), probe],
            self.prefix()+[command('reacquire_writers'), command('restore_core_ids'), probe],
        ]
        attempts += [self.prefix()+[dict(probe, **extra)] for extra in (
            dict(path='/fake.pem'), dict(url='https://example.invalid'), dict(certificate='fake'),
            dict(require_login=1), dict(host='127.0.0.1'))]
        for commands in attempts:
            with self.subTest(commands=commands):
                status, events, trace, _ = self.run_worker(commands)
                self.assertEqual(status, 1)
                self.assertEqual(events[-1], dict(event='error', code='MAINTENANCE_REFUSED'))
                self.assertFalse(any(step.startswith('tls-') for step in trace))
    def test_request_is_pathless_closed_and_not_a_public_action(self):
        from app import release_tools
        self.assertNotIn('core_prepare',release_tools._MaintenanceSession.ACTIONS)
        variants=[dict(self.request(),candidate={}),dict(self.request(),path='/tmp/fake'),
                  dict(self.request(),protocol=True),dict(self.request(),expected_selection_revision='BAD'),
                  dict(self.request(),core='other')]
        for value in variants:
            with self.subTest(value=value):
                status,events,trace,sessions=self.run_worker([],request=value)
                self.assertEqual(status,1);self.assertEqual(trace,[]);self.assertEqual(sessions,[])
                self.assertEqual(events,[dict(event='error',code='MAINTENANCE_REFUSED')])
    def test_every_phase_rejects_arbitrary_paths_flags_and_command_reordering(self):
        cases=[[],[command('proceed')],self.prefix()[:2]+[command('bind_core_ids',backup_sha256='a'*64)],
            self.prefix()+[command('release_writers')],self.prefix()+[command('restore_core_ids')],
            self.prefix()+[command('reacquire_writers'),command('finish')],
            self.prefix()+[command('reacquire_writers'),command('prove_operation')],
            self.prefix()+[command('reacquire_writers'),command('restore_core_ids'),command('execute')],
            self.prefix()+[command('reacquire_writers'),command('restore_core_ids'),command('release_reacquired_writers'),command('reacquire_writers')]]
        for values in cases:
            with self.subTest(values=values):
                status,events,trace,sessions=self.run_worker(values)
                self.assertEqual(status,1);self.assertEqual(events[-1]['event'],'error')
                self.assertLessEqual(trace.count('execute'),1);self.assertEqual(sessions[0].closed,1)
        for index in range(len(self.prefix())):
            values=self.prefix();values[index]=dict(values[index],approved=True)
            status,events,trace,_=self.run_worker(values+[command('finish')])
            self.assertEqual(status,1);self.assertEqual(events[-1]['event'],'error')
    def test_guard_proof_deadline_eof_and_close_failure_never_report_success(self):
        for options,values in [(dict(guard_error=True),self.prefix()+[command('reacquire_writers')]),
            (dict(proof_result=True),self.prefix()+[command('prove_operation')]),
            (dict(close_error=True),[command('finish')]),
            (dict(hold=True,timeout=.03),[])]:
            status,events,trace,sessions=self.run_worker(values,**options)
            self.assertEqual(status,1);self.assertEqual(events[-1],dict(event='error',code='MAINTENANCE_REFUSED'))
            self.assertEqual(sessions[0].closed,1)
        status,events,trace,sessions=self.run_worker([],uid=0)
        self.assertEqual(status,1);self.assertEqual(trace,[])
    def test_fixed_control_closure_imports_in_isolated_python_without_payload_path(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='vui-core-fixed-closure-') as temporary:
            directory=Path(temporary)
            source={'ops_worker.py':root/'deploy/ops_worker.py','release_tools.py':root/'app/release_tools.py',
                'data_owner.py':root/'app/data_owner.py','upstream_profile.py':root/'app/services/upstream_profile.py',
                'inbound_policy.py':root/'app/services/inbound_policy.py','proxy_store.py':root/'app/services/proxy_store.py',
                'certificate_errors.py':root/'app/certificates/errors.py'}
            for name in ('dns_store','cloudflare','dns_journal','dns_backup'):
                source[name+'.py']=root/'app/certificates'/(name+'.py')
            for module,name in w.CORE_IMPLEMENTATIONS:source[name]=root/Path(*module.split('.')).with_suffix('.py')
            for name,path in source.items():shutil.copyfile(path,directory/name)
            script="""import importlib.util,sys
spec=importlib.util.spec_from_file_location('fixed_worker',sys.argv[1]);w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
t=w._load_release_tools(); recovery=sys.argv[2]=='recovery'; cls=w._load_core_session(t,recovery=recovery)
assert cls.__module__==('app.core_updates.maintenance_recovery' if recovery else 'app.core_updates.maintenance')
assert all(sys.modules[n].__path__==[] for n in ('app','app.services','app.certificates','app.core_updates'))
assert not any(n in sys.modules for n in ('psutil','main','app.services.core_runtime'))
try:w._load_core_session(t)
except w.WorkerError:pass
else:raise AssertionError('namespace reuse accepted')
"""
            for action in ('prepare','recovery'):
                with self.subTest(action=action):
                    result=subprocess.run([sys.executable,'-I','-S','-c',script,str(directory/'ops_worker.py'),action],
                                          stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
                    self.assertEqual(result.returncode,0,result.stderr.decode())
    def test_actual_session_end_to_end_pipe_backup_binding_and_repair(self):
        # App/bootstrap/native execution remain explicit fake boundaries; all
        # leases, source observations, M13 archive and state-ID writes are real.
        import test_core_maintenance_session as fixture
        from app.core_updates import selection as selection
        case=fixture.CoreMaintenanceTests();case.setUp();self.addCleanup(case.doCleanups)
        read,write=os.pipe();events=[];context={}
        class RootReplies(io.StringIO):
            def write(self,raw):
                event=json.loads(raw);events.append(event);name=event['event'];reply=None
                if name=='core_context':
                    context.update(event);reply=command('check_core',candidate=selection._reference_map(case.candidate))
                elif name=='prepared':reply=command('proceed')
                elif name=='core_quiesced':reply=command('backup_core',snapshot=dict(schema=1,kind='core-backup-closure-v1',
                    selection=context['selection'],intents=event['final_intents'],panel_was_running=True))
                elif name=='core_backed_up':reply=command('bind_core_ids',backup_sha256=event['backup_sha256'])
                else:
                    steps={'core_ids_bound':'release_writers_for_trial','trial_writers_released':'reacquire_writers',
                        'writers_reacquired':'restore_core_ids','core_ids_restored':'release_reacquired_writers',
                        'recovery_writers_released':'prove_operation','operation_held':'finish'}
                    if name in steps:reply=command(steps[name])
                if reply is not None:os.write(write,(json.dumps(reply)+'\n').encode())
                return super().write(raw)
        @contextmanager
        def quiet():yield
        output=RootReplies();os.write(write,(json.dumps(self.request())+'\n').encode())
        try:
            with patch.object(w,'_load_core_session',return_value=lambda root,**kwargs:case.session()), \
                    patch.object(w,'_quiet_candidate_io',new=quiet):
                status=w._run(case.root,types.SimpleNamespace(),read,output,timeout=10)
        finally:os.close(read);os.close(write)
        self.assertEqual(status,0,[event['event'] for event in events])
        self.assertEqual([event['event'] for event in events],['core_context','prepared','core_quiesced','core_backed_up',
            'core_ids_bound','trial_writers_released','writers_reacquired','core_ids_restored',
            'recovery_writers_released','operation_held'])
        for core,(raw,revision) in case.saved.items():
            self.assertEqual((case.data/'runtime'/core/(revision+'.json')).read_bytes(),raw)
            self.assertEqual(case.read_state(core)['artifact_id'],context['intents'][core]['artifact_id'])
        self.assertEqual(len(list((case.root/'backups').glob('*.zip'))),1)
        self.assertFalse(case.cycle.busy(case.root/'.operation.lock'))


if __name__=='__main__':unittest.main()
