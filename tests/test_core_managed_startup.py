"""Managed startup source/ASGI tests, fake transport and real temporary flocks.

No fixture can grant the production data-owner pending exception. Positive
kernel-Root/service end-to-end authorization remains Linux Actions acceptance.
"""
import ast
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

from app import data_owner as owners
from app.core_updates import managed_startup as m, managed_boot as transport, selection as s
from test_core_selection import initial,APP,TARGET
from test_core_managed_boot import context as root_context,Socket
from test_core_runtime_artifact import pure_runtime

ROOT=Path(__file__).resolve().parents[1]


def fixture_files(root):
    release=root/'releases/old';(release/'payload').mkdir(parents=True,mode=0o700)
    # Intermediate mkdir modes are explicit because production walks are strict.
    (root/'releases').chmod(0o700);release.chmod(0o700)
    (root/'data').mkdir(mode=0o700)
    manifest=dict(schema=1,release_id='old',source_commit=APP,targets=[TARGET],files={})
    raw=json.dumps(manifest).encode()
    ready=dict(runtime_key=TARGET,manifest_sha256=hashlib.sha256(raw).hexdigest())
    for path,body in ((root/'CURRENT.json',json.dumps(dict(release_id='old')).encode()),
                      (release/'payload/MANIFEST.json',raw),(release/'READY.json',json.dumps(ready).encode())):
        path.write_bytes(body);path.chmod(0o600)
    return release,manifest,ready


class ManagedScopeNegativeTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='vui-managed-startup-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.root.chmod(0o700)
        self.release,self.manifest,self.ready=fixture_files(self.root)
        original_file=m._file
        def no_installed_core_metadata(path,**kwargs):
            # Explicit fake Root metadata absence; /etc is a symlink on macOS.
            # This seam can never mint the independent native connection token.
            if kwargs.get('protected'):return None,None
            return original_file(path,**kwargs)
        files=patch.object(m,'_file',side_effect=no_installed_core_metadata);files.start();self.addCleanup(files.stop)
        self.addCleanup(lambda:setattr(m._LOCAL,'session',None))
    def pending(self):
        path=self.root/'RESTORE_PENDING.json';path.write_bytes(b'{"old":"preserve"}');path.chmod(0o600);return path
    def fake_connection(self):
        value=root_context();facts=m.observe(self.root,self.release)
        value.update(application=facts['application'],barriers=facts['barriers'],data_generation=facts['data_generation'])
        fake=Socket([value])
        with patch.object(transport,'_socket_path',return_value=(1,2,0,0,0)), \
                patch.object(transport.wire,'check_root_peer'),patch.object(transport.wire,'peer_credentials',return_value=(42,0,0)):
            connection=transport.claim(app_source=APP,target=TARGET,data_generation=facts['data_generation'],
                application=facts['application'],barriers=facts['barriers'],_socket_factory=lambda *args:fake)
        self.addCleanup(connection.close)
        return connection,facts
    def test_actual_files_data_generation_and_restore_identity_are_observed(self):
        before=m.observe(self.root,self.release)
        self.assertEqual(before['data_generation'],dict(dev=(self.root/'data').stat().st_dev,ino=(self.root/'data').stat().st_ino))
        self.assertEqual(before['application']['manifest_sha256'],self.ready['manifest_sha256'])
        self.pending();after=m.observe(self.root,self.release)
        self.assertNotEqual(before['barriers']['restore_pending_sha256'],after['barriers']['restore_pending_sha256'])
        current=self.root/'CURRENT.json';original=current.read_bytes();replacement=self.root/'replacement'
        replacement.write_bytes(original);replacement.chmod(0o600);os.replace(replacement,current)
        changed=m.observe(self.root,self.release)
        self.assertEqual(after['application'],changed['application'])
        self.assertNotEqual(after['application_identity'],changed['application_identity'])
    def test_fake_transport_or_decoded_context_cannot_mint_startup_or_data_permit(self):
        pending=self.pending();connection,facts=self.fake_connection()
        with self.assertRaises(transport.ManagedBootError):transport.require_native_connection(connection)
        with self.assertRaises(transport.ManagedBootError):m.ManagedStartup(m._TOKEN,connection,self.root,self.release,facts,threading.Event())
        # Even placing an exact-class marker in TLS cannot skip kernel checks.
        marker=object.__new__(m.ManagedStartup);marker.connection=connection
        m._LOCAL.session=marker
        with self.assertRaises(owners.DataOwnerError):
            with owners.data_owner(self.root/'data'):pass
        self.assertEqual(pending.read_bytes(),b'{"old":"preserve"}')
        self.assertFalse(transport.is_native_released(connection))
    def test_normal_data_owner_unchanged_busy_flock_still_refuses_and_pending_not_deleted(self):
        data=self.root/'data'
        with owners.data_owner(data):pass
        errors=[]
        def reader():
            try:
                with owners.data_owner(data):pass
            except BaseException as error:errors.append(error)
        with owners.data_owner(data,exclusive=True):
            thread=threading.Thread(target=reader);thread.start();thread.join()
        self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],owners.DataOwnerError)
        pending=self.pending()
        with self.assertRaises(owners.DataOwnerError):
            with owners.data_owner(data):pass
        self.assertTrue(pending.exists())
    def test_scope_pid_and_thread_checks_do_not_propagate_to_threadpool(self):
        marker=object.__new__(m.ManagedStartup);marker._pid=os.getpid();marker._startup_thread=threading.get_ident()
        marker._control_thread=object();marker._revoked=threading.Event();marker._check_thread()
        errors=[]
        def foreign():
            try:marker._check_thread()
            except BaseException as error:errors.append(error)
            self.assertIsNone(getattr(m._LOCAL,'session',None))
        m._LOCAL.session=marker;thread=threading.Thread(target=foreign);thread.start();thread.join()
        self.assertEqual(len(errors),1)
        with patch.object(m.os,'getpid',return_value=marker._pid+1):
            with self.assertRaises(m.ManagedStartupError):marker._check_thread()
    def test_source_and_symlink_or_wide_restore_file_are_rejected_without_repair(self):
        current=self.root/'CURRENT.json';current.write_bytes(b'{"release_id":"foreign"}')
        with self.assertRaises(m.ManagedStartupError):m.observe(self.root,self.release)
        current.write_bytes(b'{"release_id":"old"}');pending=self.pending();pending.chmod(0o644)
        with self.assertRaises(m.ManagedStartupError):m.observe(self.root,self.release)
        self.assertEqual(pending.stat().st_mode&0o777,0o644)
        pending.unlink();pending.symlink_to(current)
        with self.assertRaises(OSError):m.observe(self.root,self.release)
        self.assertTrue(pending.is_symlink())
    def test_managed_database_preflight_reads_existing_fixture_without_creating_or_migrating(self):
        path=self.root/'data/v-ui.db'
        with sqlite3.connect(path) as database:
            database.execute('CREATE TABLE fixture_only(id INTEGER)')
            database.execute('INSERT INTO fixture_only VALUES (1)')
        path.chmod(0o600);original=path.read_bytes()
        marker=object.__new__(m.ManagedStartup);marker.root=self.root
        marker.require_database();self.assertEqual(path.read_bytes(),original)
        path.chmod(0o644)
        with self.assertRaises(m.ManagedStartupError):marker.require_database()
        self.assertEqual(path.stat().st_mode&0o777,0o644)
        path.rename(self.root/'preserved-fixture.db')
        with self.assertRaises(OSError):marker.require_database()
        self.assertFalse(path.exists())

    def test_normal_no_pending_does_not_claim_and_fake_claim_failure_never_falls_back(self):
        with patch.object(m.transport,'claim') as claim:
            self.assertIsNone(m.try_claim(self.root,self.release,self.manifest,self.ready));claim.assert_not_called()
        self.pending();connection,facts=self.fake_connection()
        with patch.object(m.transport,'claim',return_value=connection),patch.object(m,'_ACTIVE',None):
            with self.assertRaises(transport.ManagedBootError):m.try_claim(self.root,self.release,self.manifest,self.ready)
            self.assertIsNone(m.current())


class ServeOwnershipTests(unittest.TestCase):
    def setUp(self):
        from app import serve
        self.serve=serve;self.stack=__import__('contextlib').ExitStack();self.addCleanup(self.stack.close)
        root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-serve-gate-'))).resolve()
        self.root=root;self.data=root/'data';self.data.mkdir(mode=0o700)
        argv=['serve','--root',str(root),'--origin','https://panel.example.test:8443']
        self.stack.enter_context(patch.object(sys,'argv',argv))
        self.stack.enter_context(patch.dict(os.environ))
        self.stack.enter_context(patch.object(serve,'supported_environment'))
        self.stack.enter_context(patch.object(serve,'target_arch',return_value='x86_64'))
        auth=types.SimpleNamespace(configured_origin=lambda:'https://panel.example.test:8443')
        self.stack.enter_context(patch.dict(sys.modules,{'app.middleware.auth':auth}))
        self.verified=self.stack.enter_context(patch.object(serve,'active_verified'))
    def test_ordinary_serve_refuses_real_offline_lease_before_verifying_payload(self):
        errors=[]
        def attempt():
            try:self.serve.run()
            except BaseException as error:errors.append(error)
        with owners.data_owner(self.data,exclusive=True):
            thread=threading.Thread(target=attempt);thread.start();thread.join(timeout=3)
        self.assertFalse(thread.is_alive());self.assertEqual(len(errors),1)
        self.assertIsInstance(errors[0],owners.DataOwnerError);self.verified.assert_not_called()
        self.assertEqual(list(self.data.iterdir()),[])
    def test_restore_marker_created_inside_panel_lease_refuses_before_verifying(self):
        original=self.serve.lease
        @contextmanager
        def lease(path):
            with original(path):
                (self.root/'RESTORE_PENDING.json').write_bytes(b'crash marker')
                yield
        with patch.object(self.serve,'lease',side_effect=lease),self.assertRaises(owners.DataOwnerError):
            self.serve.run()
        self.verified.assert_not_called();self.assertEqual(list(self.data.iterdir()),[])
    def test_core_pending_during_verification_does_not_promote_ordinary_startup(self):
        release=self.root/'releases/fake';(release/'payload/app').mkdir(parents=True,mode=0o700)
        pending=self.root/'CORE_UPDATE_PENDING.json'
        def verified(root):
            pending.write_bytes(b'late Root marker')
            return release,{},dict(source_commit=APP),dict(runtime_key=TARGET)
        self.verified.side_effect=verified
        with patch.object(owners,'_CORE_UPDATE_PENDING',pending), \
                patch.object(self.serve,'__file__',str(release/'payload/app/serve.py')), \
                patch.object(m,'try_claim') as claim, \
                self.assertRaisesRegex(self.serve.ReleaseError,'Maintenance began'):
            self.serve.run()
        claim.assert_not_called();self.assertTrue(pending.exists());self.assertEqual(list(self.data.iterdir()),[])
    def test_initially_pending_startup_cannot_fall_back_if_root_claim_disappears(self):
        release=self.root/'releases/fake';(release/'payload/app').mkdir(parents=True,mode=0o700)
        self.verified.return_value=(release,{},dict(source_commit=APP),dict(runtime_key=TARGET))
        (self.root/'RESTORE_PENDING.json').write_bytes(b'pending marker')
        with patch.object(self.serve,'__file__',str(release/'payload/app/serve.py')), \
                patch.object(m,'try_claim',return_value=None), \
                patch.object(self.serve,'data_owner',side_effect=AssertionError('no ordinary fallback')), \
                self.assertRaisesRegex(self.serve.ReleaseError,'Maintenance state changed'):
            self.serve.run()


class GateTests(unittest.TestCase):
    def test_all_http_routes_stay_closed_for_unreleased_or_forged_flag_and_websockets(self):
        async def call(scope,receive,send):await send(dict(type='http.response.start',status=204,headers=[]))
        gate=m.ManagedHTTPGate(call)
        async def request(path,session,kind='http'):
            events=[]
            async def send(value):events.append(value)
            async def receive():return dict(type='http.request',body=b'')
            with patch.object(m,'_ACTIVE',session):await gate(dict(type=kind,path=path),receive,send)
            return events
        for path in ('/','/login','/sub/credential','/api/auth/login','/api/cores','/api/certificates','/ui/index.html'):
            events=asyncio.run(request(path,types.SimpleNamespace(released=True)))
            self.assertEqual(events[0]['status'],503)
            self.assertIn('受管启动',events[1]['body'].decode());self.assertNotIn('session_id',events[1]['body'].decode())
        self.assertEqual(asyncio.run(request('/',None))[0]['status'],204)
        self.assertEqual(asyncio.run(request('/',types.SimpleNamespace(released=False),'websocket'))[0]['code'],1013)


def source_session(namespace_extra=None):
    tree=ast.parse((ROOT/'app/core_updates/managed_startup.py').read_text())
    cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='ManagedStartup')
    namespace=dict(contextmanager=contextmanager,deepcopy=deepcopy,os=os,Path=Path,threading=threading,time=__import__('time'),
        s=s,_need=m._need,ManagedStartupError=m.ManagedStartupError,_TOKEN=object(),_LOCAL=threading.local(),
        transport=types.SimpleNamespace(require_native_connection=lambda connection:None,is_native_released=lambda connection:connection.released))
    if namespace_extra:namespace.update(namespace_extra)
    exec(compile(ast.Module(body=[cls],type_ignores=[]),'<managed-startup-source>','exec'),namespace)
    connection=types.SimpleNamespace(selection=initial(),context=root_context(),released=False,close=Mock())
    facts={key:deepcopy(connection.context[key]) for key in ('app_source','target','application','barriers','data_generation')}
    facts.update(root_identity=(1,2),application_identity=('observed',))
    namespace['observe']=lambda root,release:deepcopy(facts)
    session=namespace['ManagedStartup'](namespace['_TOKEN'],connection,Path('/private-fixture'),Path('/private-fixture/old'),facts,threading.Event())
    listener=types.SimpleNamespace(getsockname=lambda:('127.0.0.1',8443))
    bound=types.SimpleNamespace(is_serving=lambda:True,sockets=(listener,))
    session._server=types.SimpleNamespace(started=True,should_exit=False,servers=[bound],
                                         config=types.SimpleNamespace(port=8443))
    return session,namespace,facts


class LifecycleSourceTests(unittest.TestCase):
    def test_lifespan_ready_alone_does_not_send_private_startup_ready(self):
        session,namespace,_=source_session();session._main_ready.set()
        session._server.started=False
        events=[]
        session.connection._check=lambda *states:events.append('still-claimed')
        session.connection._connection=object()
        namespace['select']=types.SimpleNamespace(select=lambda *args:([],[],[]))
        session.connection.startup_ready=lambda:events.append('sent-ready')
        session.connection.run_controls=lambda **kwargs:events.append('monitoring')
        def became_bound(_):
            self.assertEqual(events,['still-claimed'])
            session._server.started=True
        session._cancel=types.SimpleNamespace(wait=became_bound)
        session._controls()
        self.assertEqual(events,['still-claimed','sent-ready','monitoring'])
        session._server.servers[0].sockets=()
        self.assertFalse(session._panel_listener_ready())

    def test_release_ack_still_gated_and_queued_release_loses_to_revoke(self):
        session,namespace,facts=source_session();callbacks=[]
        session._certificates=types.SimpleNamespace(start=Mock(),stop=Mock())
        session._loop=types.SimpleNamespace(call_soon_threadsafe=callbacks.append)
        session._settled_facts=Mock();session._configured_revision=lambda:session.connection.selection.revision
        session.settle(deepcopy(facts['barriers']))
        self.assertEqual(session._state,'release_acknowledged');self.assertFalse(session.released)
        session.connection.released=True;session._released();self.assertEqual(len(callbacks),1)
        session._revoke();callbacks[0]()
        self.assertFalse(session.released);session._certificates.start.assert_not_called()
    def test_released_runs_final_files_check_before_opening_gate_and_certificates(self):
        session,namespace,facts=source_session();events=[]
        session._loop=types.SimpleNamespace(call_soon_threadsafe=lambda callback:callback())
        session._certificates=types.SimpleNamespace(start=lambda:events.append('cert-start'),stop=lambda:None)
        session._settled_facts=lambda barriers:events.append('stable-final-files')
        session._final_barriers=facts['barriers'];session.connection.released=True
        session._released();self.assertEqual(events,['stable-final-files','cert-start']);self.assertTrue(session.released)
    def test_revoke_attempts_every_core_exit_and_cleanup_failure_cannot_report_clean(self):
        session,namespace,facts=source_session();events=[]
        def bad():events.append('bad-close');raise OSError('close lost ack')
        def good():events.append('good-close')
        session._manager=types.SimpleNamespace(adapters={'a':types.SimpleNamespace(_runtime=types.SimpleNamespace(close=bad)),
            'b':types.SimpleNamespace(_runtime=types.SimpleNamespace(close=good))})
        session._server=types.SimpleNamespace(should_exit=False)
        session._revoke();self.assertEqual(events,['bad-close','good-close']);self.assertTrue(session._server.should_exit)
        self.assertTrue(session.cleanup_failed)
        with self.assertRaisesRegex(m.ManagedStartupError,'STARTUP_CLEANUP_FAILED'):session.close()
    def test_settlement_checks_actual_pending_stable_selection_and_original_generation(self):
        session,namespace,facts=source_session()
        session._configured_revision=lambda:session.connection.selection.revision
        namespace['_CORE_ROOT']=Path('/no-real-core-metadata')
        namespace['SelectionFiles']=lambda:types.SimpleNamespace(read_stable=lambda **kwargs:session.connection.selection)
        namespace['os']=types.SimpleNamespace(path=types.SimpleNamespace(lexists=lambda path:False),getpid=os.getpid)
        final={key:'5'*64 for key in facts['barriers']};facts['barriers']=final
        session.settle(final);self.assertEqual(session._state,'release_acknowledged');self.assertFalse(session.released)
        facts['data_generation']=dict(dev=1,ino=999)
        with self.assertRaisesRegex(m.ManagedStartupError,'STARTUP_FACTS_CHANGED'):session.settle(final)
        facts['data_generation']=deepcopy(session._facts['data_generation'])
        namespace['os'].path.lexists=lambda path:True
        with self.assertRaisesRegex(m.ManagedStartupError,'STARTUP_PENDING_REMAINS'):session.settle(final)
    def test_existing_proxy_reads_never_initialize_or_rewrite_stop_intent(self):
        calls=[]
        class ProxyStore:
            def __init__(self,data):pass
            @contextmanager
            def _locked(self,allow_uncertain=False,initialize=True):
                calls.append(('initialize',initialize));yield None
            def read_snapshot(self,initialize=True):
                with self._locked(initialize=initialize):return {}
            def load_active(self,reject_pending=False):
                with self._locked():return None
            def recoverable_pending(self):
                with self._locked():return None
        session,namespace,facts=source_session()
        namespace['os']=types.SimpleNamespace(path=types.SimpleNamespace(lexists=lambda path:True),getpid=os.getpid)
        session.require_database=Mock()
        runtimes={core:types.SimpleNamespace(state=lambda core=core:dict(
            revision=session.connection.context['intents'][core]['revision'],
            enabled=session.connection.context['intents'][core]['enabled']),
            recover=lambda core=core:calls.append(('recover',core))) for core in s.CORES}
        coordinator=types.SimpleNamespace(store=None,_baseline=lambda active:calls.append(('baseline',None)),
            _recovery_guard=lambda active:calls.append(('policy_guard',None)))
        manager=types.SimpleNamespace(lock=threading.RLock(),proxy_coordinator=coordinator,
            get=lambda core:types.SimpleNamespace(runtime=runtimes[core]))
        fake_module=types.SimpleNamespace(ProxyStore=ProxyStore)
        before=deepcopy(session.connection.context['intents'])
        with patch.dict(sys.modules,{'app.services.proxy_store':fake_module}):session.recover(manager)
        self.assertEqual([item for item in calls if item[0]=='initialize'],[('initialize',False)]*3)
        self.assertEqual([item[1] for item in calls if item[0]=='recover'],list(s.CORES))
        self.assertEqual(session.connection.context['intents'],before)

    def test_actual_probe_reports_runtime_facts_instead_of_expected_artifact_echo(self):
        session,namespace,facts=source_session();source=pure_runtime();fake_ps=types.SimpleNamespace(CONN_LISTEN='LISTEN')
        listener=types.SimpleNamespace(laddr=types.SimpleNamespace(ip='127.0.0.1',port=1234),status='LISTEN',type=__import__('socket').SOCK_STREAM)
        child=types.SimpleNamespace(net_connections=lambda **kwargs:[listener])
        fake_ps.Process=lambda pid:types.SimpleNamespace(children=lambda:[child])
        fake_ps.NoSuchProcess=type('Missing',(Exception,),{});fake_ps.AccessDenied=type('Denied',(Exception,),{})
        source.psutil=fake_ps;adapters={}
        for core in s.CORES:
            intent=session.connection.context['intents'][core]
            runtime=types.SimpleNamespace(state=lambda value=intent:dict(revision=value['revision'],enabled=value['enabled']),
                execution_identity=lambda live=intent['enabled']:dict(selected=None,running=
                    dict(artifact_id='f'*64,binary_sha256='e'*64) if live else None),
                process=types.SimpleNamespace(pid=77,poll=lambda:None) if intent['enabled'] else None,
                active_config=lambda:dict(inbounds=[dict(type='vless',listen='127.0.0.1',listen_port=1234)]),
                _listeners_ready=source.CoreRuntime._listeners_ready)
            adapters[core]=types.SimpleNamespace(runtime=runtime)
        manager=types.SimpleNamespace(lock=threading.RLock(),get=lambda core:adapters[core],
            runtime_artifact=lambda core:types.SimpleNamespace(reference=session.connection.selection.slot(core).current))
        session._manager=manager
        with patch.dict(sys.modules,{'psutil':fake_ps}):observed,ok=session.probe()
        self.assertFalse(ok);self.assertEqual(observed['cores']['sing-box']['artifact_id'],'f'*64)
        self.assertNotEqual(observed['cores']['sing-box']['artifact_id'],session.connection.context['intents']['sing-box']['artifact_id'])
        self.assertTrue(observed['cores']['sing-box']['owned_listeners_ready'])
        self.assertIsNone(observed['cores']['xray']['artifact_id'])
        adapters['sing-box'].runtime.active_config=lambda:dict(inbounds=[])
        child.net_connections=lambda **kwargs:[]
        with patch.dict(sys.modules,{'psutil':fake_ps}):empty,_=session.probe()
        self.assertTrue(empty['cores']['sing-box']['owned_listeners_ready'])
        fake_ps.Process=lambda pid:types.SimpleNamespace(children=lambda:[])
        with patch.dict(sys.modules,{'psutil':fake_ps}):missing,_=session.probe()
        self.assertFalse(missing['cores']['sing-box']['owned_listeners_ready'])


class MainSourceTests(unittest.TestCase):
    def load(self,managed=None):
        tree=ast.parse((ROOT/'main.py').read_text());names={'startup_event','shutdown_event','_shutdown_ordinary_resources'}
        nodes=[node for node in tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name in names]
        for node in nodes:node.decorator_list=[]
        events=[];app=types.SimpleNamespace(state=types.SimpleNamespace())
        @contextmanager
        def owner(path):events.append('owner-enter');yield;events.append('owner-exit')
        certificate=types.SimpleNamespace(start=lambda:events.append('cert-start'),stop=lambda:events.append('cert-stop'))
        manager=types.SimpleNamespace(recover_all=lambda:events.append('ordinary-recover'),stop_all=lambda:events.append('core-stop'))
        namespace=dict(managed_startup=types.SimpleNamespace(current=lambda:managed),data_owner=owner,DB_PATH=Path('/does-not-exist'),
            app=app,configured_origin=lambda:events.append('origin'),install_log_redaction=lambda:events.append('redact'),
            init_db=lambda:events.append('DDL-init'),core_manager=manager,certificates=types.SimpleNamespace(get_manager=lambda:certificate),
            proxy_diagnostics=types.SimpleNamespace(close_diagnostics_if_created=lambda:events.append('diag-stop')),os=os,asyncio=asyncio)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<main-startup-source>','exec'),namespace)
        return namespace,events
    def test_ordinary_main_lifecycle_keeps_original_initialization_and_certificate_worker(self):
        namespace,events=self.load()
        with patch.dict(os.environ,{'VUI_CERTIFICATES_ENABLED':'1'}):asyncio.run(namespace['startup_event']())
        self.assertIn('DDL-init',events);self.assertIn('ordinary-recover',events);self.assertIn('cert-start',events)
        asyncio.run(namespace['shutdown_event']());self.assertIn('owner-exit',events)
    def test_managed_main_never_ddl_initializes_or_calls_mutating_ordinary_recovery(self):
        events=[]
        @contextmanager
        def scope():events.append('scope-enter');yield;events.append('scope-exit')
        managed=types.SimpleNamespace(scope=scope,recover=lambda manager:events.append('managed-recover'),
            ready=lambda loop:events.append('gated-ready'),close=lambda:events.append('managed-close'))
        namespace,normal=self.load(managed)
        with patch.dict(os.environ,{'VUI_CERTIFICATES_ENABLED':'1'}):asyncio.run(namespace['startup_event']())
        self.assertEqual(events,['scope-enter','managed-recover','gated-ready','scope-exit'])
        self.assertNotIn('DDL-init',normal);self.assertNotIn('ordinary-recover',normal);self.assertNotIn('cert-start',normal)
        asyncio.run(namespace['shutdown_event']());self.assertIn('managed-close',events);self.assertIn('owner-exit',normal)


if __name__=='__main__':unittest.main()
