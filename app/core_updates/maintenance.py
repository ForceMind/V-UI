"""Non-root private core preparation, backup and bounded state-ID binding.

The fixed CONTROL loader supplies this code. No user payload is imported, no
Root catalog/store/selection/pending is written, and no service is controlled.
The caller Root coordinator owns M5 journals and proof of actual service stop.
"""
import ast
import base64
from copy import deepcopy
import ctypes
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import ssl
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
from .backup_archive import (create as create_backup, MAX_ARCHIVE as MAX_BACKUP,
                             preflight, prepare_restore, place_restore_candidate)
from .binary import open_runtime_artifact, verify_runtime_artifact_fd
from .boot import _runtime_states, _data_root
from .resolver import CoreResolver
from .selection_files import SelectionFiles, _intent, _journal, _raw


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


_CERTIFICATE = re.compile(rb'-----BEGIN CERTIFICATE-----\r?\n'
                          rb'([A-Za-z0-9+/=\r\n]+)\r?\n-----END CERTIFICATE-----')
_PANEL_CERT_LIMIT = 128 * 1024
_PANEL_TLS_TIMEOUT = 3


def _panel_endpoint(config):
    mode, port, domain, bind = (config.get(field) for field in ('certificate_mode', 'port', 'domain', 'bind'))
    _need(mode in ('provided', 'managed') and type(port) is int and 1 <= port <= 65535
          and type(domain) is str and 0 < len(domain) <= 253
          and type(bind) is str, 'PANEL_TLS_CONFIG')
    labels = domain.split('.')
    _need(len(labels) >= 2 and not labels[-1].isdigit()
          and all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in labels),
          'PANEL_TLS_CONFIG')
    address = ipaddress.ip_address(bind)
    if address.is_unspecified:
        address = ipaddress.ip_address('127.0.0.1' if address.version == 4 else '::1')
    _need(not address.is_multicast and address != ipaddress.ip_address('255.255.255.255'), 'PANEL_TLS_CONFIG')
    return mode, address, port, domain


def _panel_ssl_context(pem):
    if pem is None:
        return ssl.create_default_context(), None

    # The saved file contains public certificate blocks only. Use its first
    # certificate as the expected live leaf, and trust only this saved chain.
    leaf = None
    remaining = pem.strip(b' \t\r\n')
    while remaining:
        match = _CERTIFICATE.match(remaining)
        _need(match is not None, 'PANEL_TLS_CHAIN')
        encoded = b''.join(match[1].splitlines())
        der = base64.b64decode(encoded, validate=True)
        _need(bool(der), 'PANEL_TLS_CHAIN')
        if leaf is None:
            leaf = der
        remaining = remaining[match.end():].lstrip(b' \t\r\n')
    _need(leaf is not None, 'PANEL_TLS_CHAIN')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    partial_chain = getattr(ssl, 'VERIFY_X509_PARTIAL_CHAIN', None)
    if partial_chain is None:
        # Python 3.9 lacks the enum on OpenSSL, where the flag is stable.
        _need(ssl.OPENSSL_VERSION.startswith('OpenSSL ') and ssl.OPENSSL_VERSION_INFO >= (1, 0, 2),
              'PANEL_TLS_CHAIN')
        partial_chain = 0x80000
    context.verify_flags |= partial_chain
    context.load_verify_locations(cadata=pem.decode('ascii'))
    return context, leaf


def _panel_tls_exchange(address, port, domain, context, leaf, *, require_login):
    deadline = time.monotonic() + _PANEL_TLS_TIMEOUT

    def remaining():
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise TimeoutError('panel TLS timeout')
        return timeout

    family = socket.AF_INET if address.version == 4 else socket.AF_INET6
    endpoints = socket.getaddrinfo(str(address), port, family, socket.SOCK_STREAM,
                                   socket.IPPROTO_TCP, socket.AI_NUMERICHOST)
    _need(len(endpoints) == 1, 'PANEL_TLS_CONFIG')
    endpoint = endpoints[0][4]
    try:
        with socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP) as connection:
            connection.settimeout(remaining())
            try:
                connection.bind((endpoint[0], 0, *endpoint[2:]))
            except OSError:
                return 'rejected'
            connection.connect(endpoint)
            local = connection.getsockname()
            _need(connection.getpeername() == endpoint
                  and local[0] == endpoint[0] and local[2:] == endpoint[2:], 'PANEL_TLS_ADDRESS')
            connection.settimeout(remaining())
            with context.wrap_socket(connection, server_hostname=domain) as tls:
                if leaf is not None and tls.getpeercert(binary_form=True) != leaf:
                    return 'rejected'
                remaining()
                if not require_login:
                    return 'ready'
                tls.settimeout(remaining())
                tls.sendall(('GET /login HTTP/1.1\r\nHost: '+domain+':'+str(port)
                             +'\r\nConnection: close\r\n\r\n').encode('ascii'))
                response = b''
                while b'\r\n' not in response and len(response) < 256:
                    tls.settimeout(remaining())
                    chunk = tls.recv(256-len(response))
                    if not chunk:
                        break
                    response += chunk
                remaining()
                line, separator, _ = response.partition(b'\r\n')
                if not separator or not re.fullmatch(rb'HTTP/1\.[01] [0-9]{3}(?: [^\r\n]*)?', line):
                    return 'rejected'
                return {b'200': 'ready', b'503': 'pending'}.get(line.split()[1], 'rejected')
    except ssl.SSLCertVerificationError:
        return 'rejected'
    except OSError:
        return 'retry'


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


def _read_panel_tls_material(data_path, generation):
    config_file = tools._maintenance_cycle_file(tools._MAINTENANCE_CONFIG, limit=16384,
                                               owner=0, private=False)
    config = _application_json(config_file[1])
    # The existing bootstrap reader additionally requires root-owned
    # ancestors. Compare its strict read with our exact identity snapshot.
    _need(config == tools._maintenance_read_config(), 'PANEL_TLS_CONFIG')
    endpoint = _panel_endpoint(config)
    material = None
    with _data_root(data_path, os.geteuid(), Path('/')) as (scope, data):
        info = os.fstat(data)
        _need(dict(dev=info.st_dev, ino=info.st_ino) == generation, 'DATA_GENERATION_CHANGED')
        if endpoint[0] == 'provided':
            certs = scope.directory(data, 'certs', mode=0o700)
            fd, size = scope.file(certs, 'provided-fullchain.pem', mode=0o600, limit=_PANEL_CERT_LIMIT)
            raw = os.pread(fd, size+1, 0)
            _need(len(raw) == size, 'PANEL_TLS_CHAIN')
            material = a._file_identity(os.fstat(fd)), raw
    return config_file, endpoint, material


def verify_stopped_restore(root, generation, plan, proof, *, _bundle_anchor=Path('/')):
    """Prove the selected materials and saved intent without starting cores."""
    _need(type(plan) is dict and set(plan) == {'application', 'selection', 'intents'}, 'RESTORE_OFFLINE_CONTEXT')
    with data_owner(root/'data', exclusive=True):
        proof()
        release, pointer, manifest, ready = tools.active_verified(root)
        paths = ((root/'CURRENT.json', 16384), (release/'payload'/tools.MANIFEST, 4_000_000),
                 (release/'READY.json', 16384))
        raw = [tools._maintenance_cycle_file(path, limit=limit, owner=os.geteuid())[1]
               for path, limit in paths]
        _need([_application_json(value) for value in raw] == [pointer, manifest, ready],
              'APPLICATION_CHANGED')
        source = dict(app_source=manifest['source_commit'], target=ready['runtime_key'])
        observed = dict(**source, release_id=release.name, current_sha256=tools.digest(raw[0]),
                        manifest_sha256=tools.digest(raw[1]), ready_sha256=tools.digest(raw[2]))
        _need(observed == plan['application'], 'APPLICATION_CHANGED')
        state = s.decode_selection(_raw(plan['selection']), **source)
        payload = release/'payload'
        resolver = CoreResolver(**source, bundles=bundle_artifacts(
            payload, manifest, ready, versions=_versions(payload, manifest), owner=os.geteuid(),
            anchor=_bundle_anchor))
        runtime = _runtime_states(root/'data', owner=os.geteuid(), anchor=Path('/'),
                                  expected_generation=generation)
        actual = {}
        for core in s.CORES:
            resolver.current(state, core)
            row = runtime.get(core)
            artifact_id = s.reference_identity(state.slot(core).current)
            _need(row is None or row['_schema'] == 2 and row['artifact_id'] == artifact_id,
                  'RESTORE_RUNTIME_CHANGED')
            actual[core] = dict(artifact_id=artifact_id, revision=row['revision'] if row else None,
                                enabled=row['enabled'] if row else False)
        _need(plan['intents'] is None or _intent(plan['intents'], state) == actual,
              'RESTORE_RUNTIME_CHANGED')
        proof()
        return actual


class RestorePanelProbe:
    """Pin the provided chain on the exact data generation selected for startup."""

    def __init__(self, root, generation, trust, config_sha256, proof):
        self.data = root/'data'
        self.generation = generation
        self.proof = proof
        self.proof()
        self.material = _read_panel_tls_material(self.data, generation)
        config, endpoint, certificate = self.material
        _need(endpoint[0] == 'provided' and certificate is not None
              and hashlib.sha256(certificate[1]).hexdigest() == trust
              and hashlib.sha256(_raw(_application_json(config[1]))).hexdigest() == config_sha256,
              'RESTORE_TLS_CONTEXT')
        self.trust = trust
        self.proof()

    def __call__(self, *, require_login):
        self.proof()
        try:
            _need(_read_panel_tls_material(self.data, self.generation) == self.material, 'PANEL_TLS_CHANGED')
            _, (_, address, port, domain), certificate = self.material
            context, leaf = _panel_ssl_context(certificate[1])
            status = _panel_tls_exchange(address, port, domain, context, leaf, require_login=require_login)
            _need(_read_panel_tls_material(self.data, self.generation) == self.material, 'PANEL_TLS_CHANGED')
        except (OSError, ValueError, tools.ReleaseError):
            status = 'rejected'
        self.proof()
        return dict(status=status, trust_sha256=self.trust if status != 'rejected' else None)


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
        self._panel_tls_material = None

    def _read_panel_tls_material(self):
        return _read_panel_tls_material(self.root/'data', self._data_generation)

    def probe_panel_tls(self, *, require_login):
        phase = self._state
        _need(os.geteuid() != 0 and type(require_login) is bool
              and (phase == 'prepared' and self._core_phase in ('checked', 'backup_checked') and require_login
                   or phase in ('trial_writers_released', 'recovery_writers_released')), 'PANEL_TLS_PHASE')

        def proof():
            self._state_is(phase)
            _need(self._operation_active and callable(self._operation_proof)
                  and self._operation_proof() is None, 'PANEL_TLS_OPERATION')
            self._state_is(phase)

        proof()
        trust_sha256 = None
        try:
            self._journal_clear()
            material = self._read_panel_tls_material()
            if self._panel_tls_material is None:
                self._panel_tls_material = material
            _need(self._panel_tls_material == material, 'PANEL_TLS_CHANGED')
            _, (_, address, port, domain), certificate = material
            pem = certificate[1] if certificate is not None else None
            context, leaf = _panel_ssl_context(pem)
            trust_sha256 = hashlib.sha256(pem).hexdigest() if pem is not None else None
            proof()
            status = _panel_tls_exchange(address, port, domain, context, leaf, require_login=require_login)
            _need(self._read_panel_tls_material() == material, 'PANEL_TLS_CHANGED')
            self._journal_clear()
        except (OSError, ValueError, tools.ReleaseError):
            status, trust_sha256 = 'rejected', None
        proof()
        return dict(status=status, trust_sha256=trust_sha256)

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


class CoreBackupSession(CoreMaintenanceSession):
    """Back up a settled core selection without changing either domain."""

    def __init__(self, root, *, expected_selection_revision, **layout):
        _need(a._hex(expected_selection_revision), 'SELECTION_REQUIRED')
        super().__init__(root, core='sing-box', expected_selection_revision=expected_selection_revision, **layout)

    def prepare(self):
        try:
            result = super().prepare()
            _need(self._initialized and all(value in (None, 2) for value in result['runtime_schemas'].values()),
                  'BACKUP_RUNTIME_NOT_BOOTABLE')
            self._core_phase = 'backup_checked'
            return result
        except BaseException:
            self.close()
            raise

    def execute(self):
        _need(self._core_phase == 'backup_checked', 'CORE_PHASE')
        tools._MaintenanceSession.execute(self)
        self._unchanged_application()
        states = self._observe_states()
        _need(all(row['_schema'] == 2 for row in states.values()), 'BACKUP_RUNTIME_NOT_BOOTABLE')
        self._final_intents = self._intents(states, self._before)
        for core in s.CORES:
            self._resolver.current(self._before, core)
        self._policy()
        self._core_phase = 'quiesced'
        return dict(final_intents=deepcopy(self._final_intents))

    def release_backup_writers(self):
        _need(self._core_phase == 'backed_up', 'CORE_PHASE')
        tools._MaintenanceSession.release_writers_for_trial(self)


class CoreRestorePreparation(CoreMaintenanceSession):
    """Keep the checked backup under one operation lease until it is staged."""

    def __init__(self, root, *, archive_name, archive_sha256, expected_selection_revision, **layout):
        _need(type(archive_name) is str and re.fullmatch(r'core-maintenance-[a-f0-9]{32}\.zip', archive_name)
              and a._hex(archive_sha256) and a._hex(expected_selection_revision), 'RESTORE_INPUT')
        super().__init__(root, core='sing-box', expected_selection_revision=expected_selection_revision, **layout)
        self._restore_archive = archive_name
        self._restore_sha256 = archive_sha256

    def prepare(self):
        try:
            facts = super().prepare()
            _need(self._initialized, 'SELECTION_REQUIRED')
            backup = CoreBackup(app_source=self._source, target=self._target, bundles=self._bundle_map,
                                catalog=self._catalog, store=self._store)
            checked = self._operation.enter_context(preflight(
                self.root/'backups'/self._restore_archive, self._restore_sha256, self.root,
                core_backup=backup, expected_data_path=self.root/'data', _managed=True))
            for core in s.CORES:
                self._resolver.current(checked.core_closure.selection, core)
            self._restore_backup, self._restore_input = backup, checked
            _, endpoint, certificate = self._read_panel_tls_material()
            original_trust = incoming_trust = None
            if endpoint[0] == 'provided':
                _, _, incoming_certificate = _read_panel_tls_material(checked.data_path, checked.data_generation)
                for chain in (certificate[1], incoming_certificate[1]):
                    _panel_ssl_context(chain)
                original_trust = hashlib.sha256(certificate[1]).hexdigest()
                incoming_trust = hashlib.sha256(incoming_certificate[1]).hexdigest()
            self._unchanged_application()
            self._core_phase = 'restore_preflight'
            return dict(context=facts, incoming_selection=_state_map(checked.core_closure.selection),
                        incoming_intents=checked.core_closure.intents,
                        incoming_generation=checked.data_generation, preflight_tree_sha256=checked.data_sha256,
                        original_trust_sha256=original_trust, incoming_trust_sha256=incoming_trust)
        except BaseException:
            self.close()
            raise

    def stage_restore(self, journal):
        _need(self._core_phase == 'restore_preflight', 'CORE_PHASE')
        journal = _journal(journal, self._source, self._target)
        _need(journal['operation'] == 'restore' and journal['phase'] == 'prepared'
              and journal['restore_target']['kind'] == 'core-restore-target-v2'
              and journal['switch_context']['application'] == self._app_facts
              and journal['switch_context']['data_generation'] == self._data_generation,
              'RESTORE_CONTEXT')
        with self._files.hold_restore_pending(journal):
            tools._MaintenanceSession.execute(self)
            self._check()
            trust = journal['switch_context']['trust_sha256']
            if trust is not None:
                _, _, certificate = self._read_panel_tls_material()
                _need(certificate is not None and hashlib.sha256(certificate[1]).hexdigest() == trust,
                      'RESTORE_TLS_CONTEXT')
            self._final_intents = self._intents(self._observe_states(), self._before)
            self._policy()
            for core in s.CORES:
                self._resolver.current(self._restore_input.core_closure.selection, core)
            with prepare_restore(self, self._restore_input, core_backup=self._restore_backup,
                                 current_selection=self._before) as prepared:
                original = place_restore_candidate(self, journal, prepared, core_backup=self._restore_backup,
                                                   current_selection=self._before, _selection_files=self._files)
                self._core_phase = 'quiesced'
                return dict(final_intents=deepcopy(self._final_intents),
                            restore_data=dict(live=original['data_generation'], original=None,
                                              incoming=prepared.data_generation, rejected=None),
                            original_tree_sha256=original['data_sha256'],
                            prepared_tree_sha256=prepared.data_sha256)
