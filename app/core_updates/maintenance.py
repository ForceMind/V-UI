"""Non-root private core preparation, backup and bounded state-ID binding.

The fixed CONTROL loader supplies this code. No user payload is imported, no
Root catalog/store/selection/pending is written, and no service is controlled.
The caller Root coordinator owns M5 journals and proof of actual service stop.
"""
import ast
from copy import deepcopy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time
import uuid

from app import release_tools as tools
from app.data_owner import data_owner
from . import artifacts as a, selection as s, store as fs
from .application import bundle_artifacts
from .backup import CoreBackup, _snapshot
from .backup_archive import create as create_backup, MAX_ARCHIVE as MAX_BACKUP
from .binary import open_runtime_artifact, verify_runtime_artifact_fd
from .boot import _runtime_states, _data_root
from .resolver import CoreResolver
from .selection_files import SelectionFiles


class CoreMaintenanceError(ValueError):
    def __init__(self,code):self.code=code;super().__init__(code)


def _need(value,code):
    if not value:raise CoreMaintenanceError(code)


def _canonical(value):return fs._canonical(value)


def _state_map(state):return a._json(s.encode_selection(state))


def _application_json(raw):
    # Application MANIFEST has its own 4MB bound, distinct from M0 metadata.
    _need(type(raw) is bytes and 0 < len(raw) <= 4_000_000,'APPLICATION_CODE')
    try:
        value=json.loads(raw.decode('utf-8'),object_pairs_hook=a._pairs,
                         parse_float=a._number,parse_constant=a._number,parse_int=a._integer)
        a._unicode(value)
    except (ValueError, UnicodeError, RecursionError):
        raise CoreMaintenanceError('APPLICATION_CODE') from None
    _need(type(value) is dict,'APPLICATION_CODE')
    return value


def _versions(payload,manifest):
    name='app/services/core_runtime.py';path=payload/name
    with _data_root(path.parent,os.geteuid(),Path('/')) as (scope,directory):
        fd,size=scope.file(directory,path.name,mode=0o600,limit=512000)
        expected=manifest['files'][name];raw=os.pread(fd,size+1,0)
        _need(len(raw)==size and hashlib.sha256(raw).hexdigest()==expected['sha256']
              and expected['size']==len(raw),'APPLICATION_CODE')
        tree=ast.parse(raw)
        values=[ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign)
                and any(isinstance(target,ast.Name) and target.id=='VERSIONS' for target in node.targets)]
        _need(len(values)==1 and type(values[0]) is dict and set(values[0])==set(s.CORES),'APPLICATION_CODE')
        scope.stable()
        return values[0]


def _require_clean_subtree():
    # A live child yields None; an exited child yields its unreaped status.
    # Only ECHILD proves the worker has no children after adoption/reaping.
    wait_all_children = 0x40000000  # Linux __WALL includes clone children.
    try:
        os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT | wait_all_children)
    except ChildProcessError:
        return
    raise CoreMaintenanceError('CORE_CHECK_DESCENDANTS')


def _command_group():
    pid = os.getpid()
    _need(sys.platform == 'linux' and os.getpgrp() == pid and os.getsid(0) == pid
          and signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL, 'CORE_CHECK_ISOLATION')
    # Linux reparents orphaned descendants to this worker before reporting
    # their parent's exit. Keep subreaping for the lifetime of the worker.
    libc = ctypes.CDLL(None, use_errno=True)
    set_child_subreaper = 36  # Linux PR_SET_CHILD_SUBREAPER.
    _need(libc.prctl(set_child_subreaper, ctypes.c_ulong(1), ctypes.c_ulong(0),
                    ctypes.c_ulong(0), ctypes.c_ulong(0)) == 0, 'CORE_CHECK_ISOLATION')
    _require_clean_subtree()
    return pid


def _abort_command_group(pid):
    # Keep material FDs through fail-closed group shutdown. The Root controller
    # can still signal this group while retaining our unreaped worker PID.
    try:
        if os.getpid() == pid and os.getpgrp() == pid and os.getsid(0) == pid:
            os.killpg(pid, signal.SIGKILL)
    finally:
        os._exit(70)


def _command(command,*,timeout,expected_version=None):
    """Fixed native commands only, bounded output, never protocol stdin/logs."""
    _need(os.geteuid()!=0,'NONROOT_REQUIRED')
    group = _command_group()
    process=None;deadline=time.monotonic()+timeout
    try:
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if expected_version is not None else subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if expected_version is not None:
            output=bytearray()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout,selectors.EVENT_READ)
                while True:
                    remaining=deadline-time.monotonic();_need(remaining>0,'CORE_CHECK_TIMEOUT')
                    _need(selector.select(remaining),'CORE_CHECK_TIMEOUT')
                    raw=os.read(process.stdout.fileno(),min(4096,65537-len(output)))
                    if not raw:break
                    output.extend(raw);_need(len(output)<=65536,'CORE_VERSION_OUTPUT')
            first=bytes(output).splitlines()[0] if output else b''
            _need(expected_version.encode() in first.split(),'CORE_VERSION_MISMATCH')
        remaining=deadline-time.monotonic();_need(remaining>0,'CORE_CHECK_TIMEOUT')
        _need(process.wait(timeout=remaining)==0,'CORE_CONFIGURATION_REJECTED')
    finally:
        try:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=3)
        finally:
            try:
                _require_clean_subtree()
            except BaseException:
                _abort_command_group(group)
            if process is not None and process.stdout is not None:
                process.stdout.close()


class CoreMaintenanceSession(tools._MaintenanceSession):
    """One real base-session operation/writer lifetime; not a public action."""
    ACTIONS=frozenset(('core_prepare',))
    def __init__(self,root,*,core,expected_selection_revision,_selection_files=None,_catalog=None,_store=None,_executor=None,
                 _bundle_anchor=Path('/')):
        super().__init__(root)
        _need(type(core) is str and core in s.CORES,'CORE')
        _need(expected_selection_revision is None or a._hex(expected_selection_revision),'SELECTION_CONFLICT')
        self.core=core;self.expected_revision=expected_selection_revision
        self._files=SelectionFiles() if _selection_files is None else _selection_files
        self._catalog=_catalog;self._store=_store;self._executor=_command if _executor is None else _executor
        self._bundle_anchor=_bundle_anchor
        self._core_phase='new';self._backup_sha=None;self._candidate=None
        self._before=None;self._after=None;self._prepared_states=None;self._final_intents=None
    def _observe_states(self):
        return _runtime_states(self.root/'data',owner=os.geteuid(),anchor=Path('/'))
    def _intents(self,states,state):
        result={}
        for core in s.CORES:
            item=states.get(core);reference=state.slot(core).current
            if item is not None:
                _need(item['_schema']==2 and item['artifact_id']==s.reference_identity(reference)
                      or item['_schema']==1 and type(reference) is s.BundleReference,'RUNTIME_IDENTITY')
            result[core]=dict(artifact_id=s.reference_identity(reference),revision=item['revision'] if item else None,
                              enabled=item['enabled'] if item else False)
        return result
    def _application_facts(self,application=None):
        release,current,manifest,ready=tools.active_verified(self.root) if application is None else application
        _need(current==self._selection,'APPLICATION_CHANGED')
        paths=((self.root/'CURRENT.json',16384),(release/'payload'/tools.MANIFEST,4_000_000),
               (release/'READY.json',16384))
        observed=tuple(tools._maintenance_cycle_file(path,limit=limit,owner=os.geteuid()) for path,limit in paths)
        _need(all(_canonical(_application_json(item[1]))==_canonical(expected)
                  for item,expected in zip(observed,(current,manifest,ready)))
              and tools.digest(observed[1][1])==ready['manifest_sha256'],'APPLICATION_CHANGED')
        self._observed_application_files=observed
        return dict(app_source=manifest['source_commit'],target=ready['runtime_key'],release_id=release.name,
            current_sha256=tools.digest(observed[0][1]),
            manifest_sha256=ready['manifest_sha256'],ready_sha256=tools.digest(observed[2][1]))
    def prepare(self):
        _need(os.geteuid()!=0,'NONROOT_REQUIRED')
        try:
            super().prepare('core_prepare')
            release,current,manifest,ready=self._active_application
            self._release,self._manifest,self._ready=release,manifest,ready
            self._source=manifest['source_commit'];self._target=ready['runtime_key']
            self._bundle_map=bundle_artifacts(release/'payload',manifest,ready,versions=_versions(release/'payload',manifest),owner=os.geteuid(),
                                             anchor=self._bundle_anchor)
            self._resolver=CoreResolver(app_source=self._source,target=self._target,bundles=self._bundle_map,
                                        catalog=self._catalog,store=self._store)
            self._before=self._files.read_boot(app_source=self._source,target=self._target)
            initialized=self._before is not None
            self._initialized=initialized
            if not initialized:
                _need(self.expected_revision is None,'SELECTION_CONFLICT')
                self._before=s.initial_selection(app_source=self._source,target=self._target,
                    bundles={core:descriptor.reference for core,descriptor in self._bundle_map.items()})
            else:_need(self._before.revision==self.expected_revision,'SELECTION_CONFLICT')
            with data_owner(self.root/'data'):
                states=self._observe_states()
                if not initialized:_need(all(item['_schema']==1 for item in states.values()),'SELECTION_REQUIRED')
                intents=self._intents(states,self._before)
                for core in s.CORES:self._resolver.current(self._before,core)
            self._app_facts=self._application_facts(self._active_application)
            self._application_identity=self._observed_application_files
            info=(self.root/'data').lstat();self._data_generation=dict(dev=info.st_dev,ino=info.st_ino)
            self._core_phase='context'
            return dict(application=deepcopy(self._app_facts),data_generation=self._data_generation,
                selection=_state_map(self._before),selection_initialized=initialized,
                bundles={core:s._reference_map(value.reference) for core,value in self._bundle_map.items()},
                intents=intents,runtime_schemas={core:states[core]['_schema'] if core in states else None for core in s.CORES})
        except BaseException:self.close();raise
    def _unchanged_application(self):
        self._operation_proof()
        _need(self._application_facts()==self._app_facts,'APPLICATION_CHANGED')
        _need(self._observed_application_files==self._application_identity,'APPLICATION_CHANGED')
        info=(self.root/'data').lstat()
        _need(stat.S_ISDIR(info.st_mode) and dict(dev=info.st_dev,ino=info.st_ino)==self._data_generation,'DATA_GENERATION_CHANGED')
        _need(tools._maintenance_anchor(self.root)==self._anchor,'APPLICATION_CHANGED')
    def _policy(self):
        data=self.root/'data'
        reader=tools._readonly_proxy_store_type()
        tools._database_mode_fence(data,_existing_store_type=reader)
        if os.path.lexists(data/'proxy-state') or os.path.lexists(data/'.proxy-state-initialized'):
            snapshot=reader(data).read_snapshot(initialize=False)
            tools._validate_restore_policy(data,_artifact_ids={core:s.reference_identity(self._before.slot(core).current) for core in s.CORES},
                                           _existing_store_type=reader)
            return snapshot
        return None

    def check_core(self,candidate):
        _need(self._core_phase=='context','CORE_PHASE')
        self._unchanged_application()
        reference=s._decode_reference(candidate)
        _need(reference.core==self.core and reference.target==self._target,'CANDIDATE_CONTEXT')
        descriptor=self._resolver.resolve(reference)
        if type(reference) is s.ComponentReference:
            slot=self._before.slot(self.core)
            self._after=s._next(self._before,self.core,reference,slot.current,'component').after if reference!=slot.current else self._before
        else:self._after=s.plan_bundle(self._before,core=self.core,bundle=reference,expected_revision=self._before.revision).after
        _need(self._after!=self._before,'CANDIDATE_UNCHANGED')
        with data_owner(self.root/'data'),open_runtime_artifact(descriptor) as binary:
            for core in s.CORES:self._resolver.current(self._before,core)
            states=self._observe_states();intents=self._intents(states,self._before)
            if not self._initialized:
                _need(all(item['_schema']==1 for item in states.values()),'SELECTION_REQUIRED')
            policy=self._policy()
            binary_path=f'/proc/{os.getpid()}/fd/{binary}'
            verify_runtime_artifact_fd(binary,descriptor)
            self._executor([binary_path,'version'],timeout=5,expected_version=descriptor.version)
            verify_runtime_artifact_fd(binary,descriptor)
            item=states.get(self.core)
            if item is not None:
                runtime=self.root/'data/runtime'/self.core
                with _data_root(runtime,os.geteuid(),Path('/')) as (scope,directory):
                    fd,size=scope.file(directory,item['revision']+'.json',mode=0o600,limit=16*1024*1024)
                    _need(a._digest_fd(fd,size)==item['revision'],'PREFLIGHT_SOURCE_CHANGED')
                    config=f'/proc/{os.getpid()}/fd/{fd}'
                    command=([binary_path,'run','-test','-format=json','-c',config] if self.core=='xray'
                             else [binary_path,'check','-c',config])
                    self._executor(command,timeout=15)
                    scope.stable()
                verify_runtime_artifact_fd(binary,descriptor)
            _need(self._observe_states()==states,'PREFLIGHT_SOURCE_CHANGED')
            _need(self._policy()==policy,'PREFLIGHT_SOURCE_CHANGED')
            self._unchanged_application()
            self._prepared_states=states;self._prepared_intents=intents
        self._candidate=reference;self._core_phase='checked'
        self._source_token=hashlib.sha256(_canonical(dict(application=self._app_facts,data_generation=self._data_generation,
            selection=_state_map(self._before),states=self._prepared_states))).hexdigest()
        return dict(application=self._app_facts,data_generation=self._data_generation,selection=_state_map(self._before),
            candidate=s._reference_map(reference),intents=intents,source_token=self._source_token)
    def execute(self):
        _need(self._core_phase=='checked','CORE_PHASE')
        super().execute()
        self._unchanged_application();states=self._observe_states();final=self._intents(states,self._before)
        for core in s.CORES:self._resolver.current(self._before,core)
        self._policy()
        for core in s.CORES:
            old,new=self._prepared_states.get(core),states.get(core)
            _need((old is None)==(new is None),'PREFLIGHT_SOURCE_CHANGED')
            if old is not None:
                _need(all(old[key]==new[key] for key in old if key!='enabled'),'PREFLIGHT_SOURCE_CHANGED')
        self._final_intents=final;self._final_states=states;self._core_phase='quiesced'
        return dict(final_intents=deepcopy(final),source_token=self._source_token)
    def _check(self):
        if self._state=='executing':return super()._check()
        token = self._writer_generation
        self._check_writer_generation('done', token)
        self._journal_clear()
        self._unchanged_application()
        self._check_writer_generation('done', token)

    def _check_writer_generation(self, phase, token):
        self._state_is(phase)
        _need(self._operation_active and callable(self._operation_proof), 'WRITERS_REQUIRED')
        self._operation_proof()
        self._state_is(phase)
        _need(self._operation_active and self._valid and token is not None
              and self._writer_generation is token, 'WRITERS_REQUIRED')
    def backup_core(self,snapshot):
        _need(self._core_phase=='quiesced','CORE_PHASE');self._check()
        state=_snapshot(snapshot,self._source,self._target)
        _need(_canonical(_state_map(state))==_canonical(_state_map(self._before))
              and _canonical(snapshot['intents'])==_canonical(self._final_intents),'BEFORE_SNAPSHOT_CHANGED')
        self._before_snapshot=deepcopy(snapshot)
        parent=self.root/'backups'
        with tools._maintenance_directory(parent,create=True):pass
        name='core-maintenance-'+uuid.uuid4().hex+'.zip'
        backup=CoreBackup(app_source=self._source,target=self._target,bundles=self._bundle_map,
                           catalog=self._catalog,store=self._store)
        checksum=create_backup(self,parent/name,core_backup=backup,selection=self._before,
                               intents=self._final_intents,panel_was_running=snapshot['panel_was_running'])
        self._backup_sha=checksum;self._backup_path=parent/name
        self._backup_size=self._backup_path.lstat().st_size;self._core_phase='backed_up'
        return dict(backup_name=name,backup_sha256=checksum,
                    snapshot_sha256=hashlib.sha256(_canonical(snapshot)).hexdigest())
    def _bind(self,state,*,restore):
        phase = 'writers_reacquired' if restore else 'done'
        token = self._writer_generation
        if restore:
            self._check_reacquired_writers()
        else:
            self._check()
        states=self._observe_states()
        if not restore:_need(states==self._final_states,'RUNTIME_STATE_CHANGED')
        allowed={core:{s.reference_identity(self._before.slot(core).current),s.reference_identity(self._after.slot(core).current)} for core in s.CORES}
        for core in s.CORES:
            value=states.get(core);intent=self._final_intents[core]
            _need((value is None)==(intent['revision'] is None),'RUNTIME_STATE_CHANGED')
            if value is None:continue
            _need(value['revision']==intent['revision'] and value['enabled']==intent['enabled'],'RUNTIME_STATE_CHANGED')
            _need(value['_schema']==2 and value['artifact_id'] in allowed[core]
                  or value['_schema']==1 and type(self._before.slot(core).current) is s.BundleReference,'RUNTIME_IDENTITY')
        for core,value in states.items():
            intent=self._final_intents[core]
            target=dict(schema=2,artifact_id=s.reference_identity(state.slot(core).current),
                        revision=intent['revision'],enabled=intent['enabled'])
            if value['_schema']==2 and value['artifact_id']==target['artifact_id']:continue
            self._check_writer_generation(phase, token)
            tools.atomic_json(self.root/'data/runtime'/core/'state.json',target)
        self._check_writer_generation(phase, token)
        self._intents_bound={core:dict(value,artifact_id=s.reference_identity(state.slot(core).current))
                             for core,value in self._final_intents.items()}
        self._core_phase='restored' if restore else 'bound'
        return dict(selection=_state_map(state),intents=deepcopy(self._intents_bound))
    def bind_core_ids(self,checksum):
        _need(self._core_phase=='backed_up' and checksum==self._backup_sha and a._hex(checksum),'BACKUP_NOT_CONFIRMED')
        with _data_root(self._backup_path.parent,os.geteuid(),Path('/')) as (scope,directory):
            fd,size=scope.file(directory,self._backup_path.name,mode=0o600,limit=MAX_BACKUP)
            _need(size==self._backup_size and a._digest_fd(fd,size)==checksum,'BACKUP_CHANGED')
            scope.stable()
        return self._bind(self._after,restore=False)
    def restore_core_ids(self):
        _need(self._core_phase=='bound' and self._state=='writers_reacquired','CORE_PHASE')
        return self._bind(self._before,restore=True)
    def release_writers_for_trial(self):
        _need(self._core_phase=='bound','CORE_PHASE');return super().release_writers_for_trial()
