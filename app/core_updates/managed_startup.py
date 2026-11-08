"""Root-authenticated temporary startup/probe scope and closed HTTP lifecycle.

No JSON/phase/test owner can mint the ordinary data-owner exception. Only the
fixed production socket with actual kernel UID0 credentials can do so. Root
coordination, data exchange and external TLS/core health remain outside here.
"""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import asyncio
import hashlib
import json
import os
from pathlib import Path
import select
import stat
import threading
import time

from deploy import core_boot_broker as wire
from . import managed_boot as transport, selection as s
from .selection_files import SelectionFiles

_CORE_ROOT = Path('/etc/v-ui')
_TOKEN = object()
_LOCAL = threading.local()
_ACTIVE = None


class ManagedStartupError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(value, code):
    if not value:
        raise ManagedStartupError(code)


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _directory(path, *, protected=False, optional=False):
    path=Path(path)
    _need(path.is_absolute() and '..' not in path.parts, 'STARTUP_PATH')
    with ExitStack() as stack:
        fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);stack.callback(os.close,fd)
        bindings=[];missing=None
        for name in path.parts[1:]:
            before=os.fstat(fd)
            _need(stat.S_ISDIR(before.st_mode) and before.st_uid in ((0,) if protected else (0,os.geteuid()))
                  and (not before.st_mode&0o022 or not protected and before.st_mode&stat.S_ISVTX), 'STARTUP_PARENT')
            try:child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            except FileNotFoundError:
                if not optional:raise
                missing=fd,name;fd=None;break
            stack.callback(os.close,child);info=os.fstat(child)
            bindings.append((fd,name,child,(info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid)));fd=child
        if fd is not None:
            info=os.fstat(fd)
            _need(info.st_uid in ((0,) if protected else (0,os.geteuid())) and not info.st_mode&0o022,'STARTUP_PARENT')
        yield fd
        for parent,name,child,expected in bindings:
            current=os.stat(name,dir_fd=parent,follow_symlinks=False);opened=os.fstat(child)
            sig=lambda value:(value.st_dev,value.st_ino,value.st_mode,value.st_uid,value.st_gid)
            _need(sig(current)==expected and sig(opened)==expected,'STARTUP_DIRECTORY_CHANGED')
        if missing is not None:
            try:os.stat(missing[1],dir_fd=missing[0],follow_symlinks=False)
            except FileNotFoundError:pass
            else:raise ManagedStartupError('STARTUP_DIRECTORY_CHANGED')


def _file(path, *, owner, mode, limit, optional=False, protected=False):
    with _directory(path.parent,protected=protected,optional=optional) as directory:
        if directory is None:return None,None
        with ExitStack() as stack:
            try:fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
            except FileNotFoundError:
                if optional:return None,None
                raise
            stack.callback(os.close,fd);before=os.fstat(fd)
            _need(stat.S_ISREG(before.st_mode) and before.st_uid==owner and before.st_nlink==1
                  and stat.S_IMODE(before.st_mode)==mode and before.st_size<=limit,'STARTUP_FILE')
            raw=os.pread(fd,before.st_size+1,0)
            _need(len(raw)==before.st_size and _signature(os.fstat(fd))==_signature(before)
                  and _signature(os.stat(path.name,dir_fd=directory,follow_symlinks=False))==_signature(before),
                  'STARTUP_FILE_CHANGED')
            return _signature(before),raw


def _json(raw):
    def pairs(items):
        result={}
        for key,value in items:
            _need(key not in result,'STARTUP_DUPLICATE');result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))


def observe(root, release):
    """Actual fixed-path application/data/barrier observations, no grant."""
    root,release=Path(root),Path(release)
    current_id,current=_file(root/'CURRENT.json',owner=os.geteuid(),mode=0o600,limit=16384)
    manifest_id,manifest=_file(release/'payload/MANIFEST.json',owner=os.geteuid(),mode=0o600,limit=4_000_000)
    ready_id,ready=_file(release/'READY.json',owner=os.geteuid(),mode=0o600,limit=64000)
    pointer,meta,prepared=_json(current),_json(manifest),_json(ready)
    _need(pointer.get('release_id')==release.name and meta.get('release_id')==release.name
          and hashlib.sha256(manifest).hexdigest()==prepared.get('manifest_sha256'),'STARTUP_APPLICATION_CHANGED')
    with _directory(root) as parent:
        root_id=os.fstat(parent)
        _need(root_id.st_uid==os.geteuid() and stat.S_IMODE(root_id.st_mode)==0o700,'STARTUP_ROOT')
        data=os.open('data',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        try:
            info=os.fstat(data)
            _need(info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o700,'STARTUP_DATA')
            generation=dict(dev=info.st_dev,ino=info.st_ino)
            _need(_signature(os.stat('data',dir_fd=parent,follow_symlinks=False))==_signature(info),'STARTUP_DATA_CHANGED')
        finally:os.close(data)
    barriers={}
    for key,path,owner,mode,protected in (
        ('core_pending_sha256',_CORE_ROOT/'CORE_UPDATE_PENDING.json',0,0o644,True),
        ('core_selection_sha256',_CORE_ROOT/'core-selection.json',0,0o644,True),
        ('core_selection_fence_sha256',_CORE_ROOT/'core-selection.fence.json',0,0o644,True),
        ('restore_pending_sha256',root/'RESTORE_PENDING.json',os.geteuid(),0o600,False)):
        identity,raw=_file(path,owner=owner,mode=mode,limit=64000,optional=True,protected=protected)
        barriers[key]=wire.file_digest(identity,raw)
    return dict(application=dict(current_sha256=hashlib.sha256(current).hexdigest(),
        manifest_sha256=hashlib.sha256(manifest).hexdigest(),ready_sha256=hashlib.sha256(ready).hexdigest()),
        barriers=barriers,data_generation=generation,app_source=meta.get('source_commit'),target=prepared.get('runtime_key'),
        root_identity=(root_id.st_dev,root_id.st_ino),application_identity=(current_id,manifest_id,ready_id))


def current():
    # Returning a foreign-PID object keeps its HTTP gate closed after fork; it
    # never turns that child into an ordinary ungated application.
    return _ACTIVE


def _pending_permitted(parent):
    """Called only after ordinary shared data_owner obtained its real flock."""
    session=getattr(_LOCAL,'session',None)
    if type(session) is not ManagedStartup:return False
    try:
        transport.require_native_connection(session.connection)
        session._check_thread()
        info=os.fstat(parent)
        _need((info.st_dev,info.st_ino)==session._facts['root_identity'],'STARTUP_DATA_CHANGED')
        session._check_initial()
        return True
    except Exception:return False


class ManagedHTTPGate:
    """No public/Root HTTP exception: independent Root TLS plus private probe."""
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type'] not in ('http','websocket'):
            await self.app(scope,receive,send)
            return
        from app.data_owner import core_update_pending
        try:
            pending=core_update_pending()
            session=current()
            blocked=pending or session is not None and not (type(session) is ManagedStartup and session.released)
        except Exception:
            blocked=True
        if blocked:
            if scope['type']=='http':
                body=json.dumps({'detail':'核心维护或受管启动正在等待验证完成'},ensure_ascii=False,separators=(',',':')).encode()
                await send(dict(type='http.response.start',status=503,headers=[
                    (b'content-type',b'application/json'),(b'content-length',str(len(body)).encode()),(b'cache-control',b'no-store')]))
                await send(dict(type='http.response.body',body=body));return
            if scope['type']=='websocket':
                await send(dict(type='websocket.close',code=1013));return
        await self.app(scope,receive,send)


class ManagedStartup:
    def __init__(self,token,connection,root,release,facts,cancel):
        _need(token is _TOKEN,'STARTUP_CONNECTION_REQUIRED')
        transport.require_native_connection(connection)
        self.connection,self.root,self.release=connection,Path(root),Path(release)
        self._facts,self._cancel=deepcopy(facts),cancel
        self._pid,self._startup_thread=os.getpid(),threading.get_ident()
        self._state='claimed';self._main_ready=threading.Event();self._revoked=threading.Event()
        self._manager=self._certificates=self._server=self._loop=None
        self._configured=None;self._final_barriers=None;self.cleanup_failed=False
        self._control_thread=threading.Thread(target=self._controls,name='vui-managed-startup',daemon=True)
    def __copy__(self):raise ManagedStartupError('STARTUP_CONNECTION_REQUIRED')
    def __deepcopy__(self,memo):raise ManagedStartupError('STARTUP_CONNECTION_REQUIRED')
    def __reduce__(self):raise ManagedStartupError('STARTUP_CONNECTION_REQUIRED')
    @property
    def released(self):
        return (self._pid==os.getpid() and self._state=='released' and not self._revoked.is_set()
                and transport.is_native_released(self.connection))
    def _check_thread(self):
        _need(self._pid==os.getpid() and not self._revoked.is_set(),'STARTUP_SCOPE_REVOKED')
        _need(threading.get_ident()==self._startup_thread or threading.current_thread() is self._control_thread,
              'STARTUP_THREAD_REQUIRED')
    def _check_initial(self):
        self._check_thread();transport.require_native_connection(self.connection)
        facts=observe(self.root,self.release)
        _need(all(facts[key]==self._facts[key] for key in ('app_source','target','application','application_identity',
            'root_identity','data_generation','barriers')),'STARTUP_FACTS_CHANGED')
        return facts
    @contextmanager
    def scope(self):
        self._check_initial();previous=getattr(_LOCAL,'session',None)
        _need(previous is None or previous is self,'STARTUP_SCOPE_CONFLICT')
        _LOCAL.session=self
        try:yield
        finally:_LOCAL.session=previous
    def start_monitor(self):self._control_thread.start()
    def configure(self,manager,*,payload,manifest,ready,data,versions):
        from .boot import configure_managed
        with self.scope():
            self._configured=configure_managed(manager,session=self,payload=payload,manifest=manifest,ready=ready,data=data,versions=versions)
            self._manager=manager
    def require_database(self):
        path=self.root/'data/v-ui.db'
        with _directory(path.parent) as parent, ExitStack() as stack:
            fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent);stack.callback(os.close,fd)
            info=os.fstat(fd)
            _need(stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid() and info.st_nlink==1
                  and stat.S_IMODE(info.st_mode)==0o600 and os.pread(fd,16,0)==b'SQLite format 3\x00','STARTUP_DATABASE_REQUIRED')
    def bind_certificates(self,manager):self._certificates=manager
    def attach_server(self,server):self._server=server
    def recover(self,manager):
        """Existing state/config recovery only; no DDL/apply/restore rewrite."""
        self._check_initial();self.require_database()
        with manager.lock:
            data=self.root/'data'
            if os.path.lexists(data/'proxy-state') or os.path.lexists(data/'.proxy-state-initialized'):
                coordinator=manager.proxy_coordinator
                from app.services.proxy_store import ProxyStore
                class ExistingProxyStore(ProxyStore):
                    # Every inherited read must keep initialization disabled,
                    # including load_active/recoverable_pending helper reads.
                    @contextmanager
                    def _locked(self,allow_uncertain=False,initialize=False):
                        with super()._locked(allow_uncertain=allow_uncertain,initialize=False) as value:
                            yield value
                coordinator.store=ExistingProxyStore(data)
                coordinator.store.read_snapshot(initialize=False)
                active=coordinator.store.load_active(reject_pending=True)
                if coordinator.store.recoverable_pending() is not None:
                    raise ManagedStartupError('STARTUP_PROXY_RECOVERY_REQUIRED')
                coordinator._baseline(active);coordinator._recovery_guard(active)
                plan=coordinator.store.context_plan(active.get('runtime_context'),active['settings']) if active else None
                if active and (active['settings']['relay_enabled'] or plan and plan.relay_ids):
                    manager._check_relay_runtime_gate(True)
            else:
                from app.services.inbound_policy import policy_plan
                from app.services.proxy_store import _defaults
                plan=policy_plan(manager._all_rows(),_defaults())
                _need(plan.legacy and not plan.relay_ids,'STARTUP_PROXY_RECOVERY_REQUIRED')
            for core in s.CORES:
                self._check_initial();runtime=manager.get(core).runtime
                actual=runtime.state();intent=self.connection.context['intents'][core]
                _need(actual=={'revision':intent['revision'],'enabled':intent['enabled']},'STARTUP_RUNTIME_INTENT_CHANGED')
                runtime.recover();self._check_initial()
        self._state='starting'
    def ready(self,loop):
        self._check_initial();self._loop=loop;self._state='gated_ready';self._main_ready.set()
    def _configured_revision(self):
        state=self.connection.selection
        slots=tuple(s.CoreSlot(core,self._manager.runtime_artifact(core).reference,state.slot(core).previous) for core in s.CORES)
        return s._build(state.app_source,state.target,state.generation,slots).revision
    def probe(self):
        with self.scope(), self._manager.lock:
            facts=self._check_initial()
            observed={key:deepcopy(facts[key]) for key in ('app_source','target','data_generation','application','barriers')}
            observed['selection_revision']=self._configured_revision();observed['cores']={};ok=True
            import psutil
            for core in s.CORES:
                runtime=self._manager.get(core).runtime;state=runtime.state();execution=runtime.execution_identity()
                running=runtime.process is not None and runtime.process.poll() is None
                listeners=False
                if running:
                    value=runtime.active_config()
                    try:
                        children=psutil.Process(runtime.process.pid).children()
                        sockets=[item for child in children for item in child.net_connections(kind='inet')]
                        if core=='xray':sockets=runtime._xray_ipv4_wildcard_sockets(value,sockets,children,time.monotonic()+0.2)
                        ports=[row.get('listen_port',row.get('port',0)) for row in value.get('inbounds',[])]
                        listeners=bool(children) and any(type(port) is int and port>0 for port in ports) and runtime._listeners_ready(value,sockets)
                    except (psutil.NoSuchProcess,psutil.AccessDenied):listeners=False
                actual=execution['running']
                observed['cores'][core]=dict(artifact_id=actual['artifact_id'] if actual else None,
                    binary_sha256=actual['binary_sha256'] if actual else None,revision=state['revision'],
                    enabled=state['enabled'],running=running,owned_listeners_ready=listeners)
                intent=self.connection.context['intents'][core]
                ok=ok and state=={'revision':intent['revision'],'enabled':intent['enabled']} and running==intent['enabled']
                if running:ok=ok and actual is not None and actual['artifact_id']==intent['artifact_id'] and listeners
            self._check_initial()
            return observed,bool(ok)
    def _settled_facts(self,final_barriers):
        facts=observe(self.root,self.release)
        _need(all(facts[key]==self._facts[key] for key in ('app_source','target','application','application_identity',
            'root_identity','data_generation')),'STARTUP_FACTS_CHANGED')
        _need(facts['barriers']==final_barriers,'STARTUP_FINAL_BARRIERS_CHANGED')
        _need(not os.path.lexists(_CORE_ROOT/'CORE_UPDATE_PENDING.json')
              and not os.path.lexists(self.root/'RESTORE_PENDING.json'),'STARTUP_PENDING_REMAINS')
        stable=SelectionFiles().read_stable(app_source=facts['app_source'],target=facts['target'])
        _need(stable==self.connection.selection and self._configured_revision()==stable.revision,'STARTUP_SELECTION_CHANGED')
    def settle(self,final_barriers):
        self._check_thread();transport.require_native_connection(self.connection)
        self._settled_facts(final_barriers)
        self._final_barriers=deepcopy(final_barriers)
        self._state='release_acknowledged'
    def _released(self):
        def finish():
            try:
                _need(not self._revoked.is_set() and transport.is_native_released(self.connection),'STARTUP_SCOPE_REVOKED')
                self._settled_facts(self._final_barriers)
                if self._certificates is not None:self._certificates.start()
                self._state='released'
            except BaseException:self._revoke()
        if self._loop is not None:self._loop.call_soon_threadsafe(finish)
        else:self._revoke()
    def _request_exit(self):
        if self._server is not None:self._server.should_exit=True
    def _stop_cores(self):
        if self._manager is None:return
        for adapter in self._manager.adapters.values():
            runtime=adapter._runtime
            if runtime is None:continue
            try:runtime.close()
            except BaseException:self.cleanup_failed=True
            else:adapter._runtime=None
    def _revoke(self):
        self._revoked.set();self._state='revoked';self._cancel.set();self._request_exit()
        try:self.connection.close()
        except BaseException:self.cleanup_failed=True
        if self._certificates is not None:
            try:self._certificates.stop()
            except BaseException:self.cleanup_failed=True
        self._stop_cores()
    def _controls(self):
        try:
            while not self._main_ready.wait(0.05):
                self.connection._check('claimed')
                if select.select([self.connection._connection],[],[],0)[0]:
                    self.connection._receive()
                    raise ManagedStartupError('STARTUP_UNEXPECTED_CONTROL')
            self.connection.startup_ready()
            self.connection.run_controls(probe=self.probe,settle=self.settle,on_released=self._released,on_revoked=self._revoke)
        except BaseException:self._revoke()
    def close(self):
        self._cancel.set()
        if not self.released:self._revoke()
        try:self.connection.close()
        except BaseException:self.cleanup_failed=True
        finally:
            if self._control_thread.ident is not None and threading.current_thread() is not self._control_thread:
                self._control_thread.join(timeout=1)
                if self._control_thread.is_alive():self.cleanup_failed=True
            self._stop_cores()
        if self.cleanup_failed:
            self._request_exit()
            raise ManagedStartupError('STARTUP_CLEANUP_FAILED')


def try_claim(root,release,manifest,ready):
    """Production-only integration; pending existence decides attempt, not grant."""
    global _ACTIVE
    if not (os.path.lexists(root/'RESTORE_PENDING.json') or os.path.lexists(_CORE_ROOT/'CORE_UPDATE_PENDING.json')):
        return None
    _need(_ACTIVE is None,'STARTUP_ALREADY_ACTIVE')
    facts=observe(root,release)
    _need(facts['app_source']==manifest.get('source_commit') and facts['target']==ready.get('runtime_key'),
          'STARTUP_APPLICATION_CHANGED')
    cancel=threading.Event()
    connection=transport.claim(app_source=facts['app_source'],target=facts['target'],
        data_generation=facts['data_generation'],application=facts['application'],barriers=facts['barriers'],_stop=cancel.is_set)
    try:
        session=ManagedStartup(_TOKEN,connection,root,release,facts,cancel)
        _ACTIVE=session;session.start_monitor()
        return session
    except BaseException:
        connection.close();_ACTIVE=None;raise


def clear(session):
    global _ACTIVE
    if _ACTIVE is session:_ACTIVE=None
