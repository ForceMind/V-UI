"""Temporary Root-selected boot protocol; no service or data-owner authority.

Importing this module performs no filesystem/network/service action. Production
Root loads this control code from its verified fixed closure, never user payload.
Proof/identity callbacks are trusted bounded Root code, not RPC fields. Context
metadata and probe booleans cannot grant a Root operation or a data-owner permit.
"""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import select
import socket
import stat
import struct
import threading
import time

PROTOCOL = 1
MAX_FRAME = 64000
MAX_UINT = 2**64 - 1
_LIVE_INTERVAL = 0.25
TARGETS = ('x86_64-gnu', 'x86_64-musl', 'aarch64-gnu', 'aarch64-musl')
CORES = ('sing-box', 'xray')
ADDRESS = Path('/run/v-ui-maintenance/core-boot.sock')
BARRIER_FIELDS = ('core_pending_sha256', 'core_selection_sha256',
                  'core_selection_fence_sha256', 'restore_pending_sha256')
APPLICATION_FIELDS = ('current_sha256', 'manifest_sha256', 'ready_sha256')
REASONS = ('cancelled', 'deadline', 'proof_failed', 'identity_changed', 'protocol_error',
           'io_error', 'broker_closed')
_BUNDLE = {'kind', 'core', 'version', 'target', 'app_source', 'payload_manifest_sha256',
           'binary_sha256', 'binary_size'}
_COMPONENT = {'kind', 'repository', 'release_id', 'tag', 'core', 'version', 'target', 'builder_source',
              'release_manifest_sha256', 'checksums_sha256', 'pack_sha256', 'pack_manifest_sha256',
              'binary_sha256', 'binary_size'}
_CONTEXT = {'purpose', 'app_source', 'target', 'data_generation', 'application', 'selection', 'intents', 'barriers'}


class CoreBootBrokerError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(value, code='PROTOCOL'):
    if not value:
        raise CoreBootBrokerError(code)


def _keys(value, fields):
    _need(type(value) is dict and set(value) == set(fields))


def _hex(value, size=64):
    return type(value) is str and re.fullmatch('[0-9a-f]{' + str(size) + '}', value) is not None


def _uint(value, maximum=MAX_UINT, *, positive=False):
    return type(value) is int and (0 < value if positive else 0 <= value) and value <= maximum


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def _generation(value):
    _keys(value, ('dev', 'ino'))
    _need(_uint(value['dev']) and _uint(value['ino'], positive=True))


def _hashes(value, fields):
    _keys(value, fields)
    _need(all(_hex(value[name]) for name in fields))


def _observed_context(value):
    _need(_hex(value['app_source'], 40) and type(value['target']) is str and value['target'] in TARGETS)
    _generation(value['data_generation'])


def _selection(value, source, target):
    # Only a coarse closed shape here; the app must independently use complete
    # M3 decoding, trusted resolver and real application/material qualification.
    _keys(value, ('schema', 'app_source', 'target', 'generation', 'revision', 'cores'))
    _need(type(value['schema']) is int and value['schema'] == 1 and value['app_source'] == source
          and value['target'] == target and _uint(value['generation'], 2**63-1) and _hex(value['revision']))
    _keys(value['cores'], CORES)
    for core in CORES:
        slot = value['cores'][core]
        _keys(slot, ('current', 'previous'))
        for index, reference in enumerate((slot['current'], slot['previous'])):
            if reference is None:
                _need(index == 1)
                continue
            _need(type(reference) is dict and reference.get('kind') in ('bundle', 'component'))
            _keys(reference, _BUNDLE if reference['kind'] == 'bundle' else _COMPONENT)
            _need(reference['core'] == core and reference['target'] == target)


def _intents(value, selection):
    _keys(value, CORES)
    for core in CORES:
        item = value[core]
        _keys(item, ('artifact_id', 'revision', 'enabled'))
        _need(_hex(item['artifact_id']) and (item['revision'] is None or _hex(item['revision']))
              and type(item['enabled']) is bool and (item['revision'] is not None or not item['enabled']))
        reference = selection['cores'][core]['current']
        _need(hashlib.sha256(_canonical(reference)).hexdigest() == item['artifact_id'])


def _context(value):
    _keys(value, _CONTEXT)
    _need(value['purpose'] in ('trial', 'old_recovery'))
    _observed_context(value)
    _hashes(value['application'], APPLICATION_FIELDS)
    _hashes(value['barriers'], BARRIER_FIELDS)
    _selection(value['selection'], value['app_source'], value['target'])
    _intents(value['intents'], value['selection'])


def _probe(value):
    _keys(value, ('app_source', 'target', 'data_generation', 'application', 'barriers', 'selection_revision', 'cores'))
    _observed_context(value)
    _hashes(value['application'], APPLICATION_FIELDS)
    _hashes(value['barriers'], BARRIER_FIELDS)
    _need(_hex(value['selection_revision']))
    _keys(value['cores'], CORES)
    for core in CORES:
        item = value['cores'][core]
        _keys(item, ('artifact_id', 'binary_sha256', 'revision', 'enabled', 'running', 'owned_listeners_ready'))
        _need(all(type(item[name]) is bool for name in ('enabled', 'running', 'owned_listeners_ready'))
              and (item['revision'] is None or _hex(item['revision']))
              and (item['artifact_id'] is None or _hex(item['artifact_id']))
              and (item['binary_sha256'] is None or _hex(item['binary_sha256']))
              and (not item['running'] or _hex(item['artifact_id']) and _hex(item['binary_sha256']))
              and (not item['owned_listeners_ready'] or item['running']))


def _tree(value, depth=0):
    _need(depth <= 16)
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        _need(_uint(value))
    elif type(value) is str:
        _need(len(value) <= 4096)
        try: value.encode('utf-8')
        except UnicodeError: raise CoreBootBrokerError('PROTOCOL') from None
    elif type(value) is list:
        _need(len(value) <= 512)
        for child in value: _tree(child, depth+1)
    elif type(value) is dict:
        _need(len(value) <= 64 and all(type(key) is str for key in value))
        for key, child in value.items():
            _tree(key, depth+1); _tree(child, depth+1)
    else:
        raise CoreBootBrokerError('PROTOCOL')


def validate_message(message):
    """Pure strict closed protocol schema; not M3 or permission validation."""
    _tree(message)
    _need(type(message) is dict and type(message.get('protocol')) is int and message['protocol'] == PROTOCOL)
    op = message.get('op')
    base = {'protocol', 'op'}
    if op == 'claim':
        _keys(message, base | {'app_source', 'target', 'data_generation'})
        _observed_context(message)
    elif op == 'context':
        _keys(message, base | {'session_id'} | _CONTEXT)
        _context({key: message[key] for key in _CONTEXT})
    elif op in ('startup_ready', 'release_ack'):
        _keys(message, base | {'session_id'})
    elif op == 'request_probe':
        _keys(message, base | {'session_id', 'probe_id'})
        _need(_hex(message['probe_id'], 32))
    elif op == 'probe_result':
        _keys(message, base | {'session_id', 'probe_id', 'ok', 'observed'})
        _need(_hex(message['probe_id'], 32) and type(message['ok']) is bool)
        _probe(message['observed'])
    elif op == 'release':
        _keys(message, base | {'session_id', 'selection_revision', 'barriers'})
        _need(_hex(message['selection_revision']))
        _hashes(message['barriers'], BARRIER_FIELDS)
    elif op == 'released':
        _keys(message, base | {'session_id', 'selection_revision'})
        _need(_hex(message['selection_revision']))
    elif op == 'revoke':
        _keys(message, base | {'session_id', 'reason'})
        _need(message['reason'] in REASONS)
    else:
        raise CoreBootBrokerError('PROTOCOL')
    if op != 'claim': _need(_hex(message['session_id'], 32))
    return deepcopy(message)


def encode_frame(message):
    raw = _canonical(validate_message(message))
    _need(0 < len(raw) <= MAX_FRAME, 'FRAME_LIMIT')
    return struct.pack('!I', len(raw)) + raw


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result, 'DUPLICATE_FIELD')
        result[key] = value
    return result


def _number(raw):
    raise CoreBootBrokerError('PROTOCOL')


def _integer(raw):
    _need(len(raw) <= 20)
    value = int(raw)
    _need(_uint(value))
    return value


def decode_frame(frame):
    _need(type(frame) is bytes and 4 < len(frame) <= MAX_FRAME+4, 'FRAME_LIMIT')
    length = struct.unpack('!I', frame[:4])[0]
    _need(0 < length <= MAX_FRAME and len(frame) == length+4, 'FRAME_LIMIT')
    try:
        value = json.loads(frame[4:].decode('utf-8'), object_pairs_hook=_pairs,
                           parse_int=_integer, parse_float=_number, parse_constant=_number)
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, CoreBootBrokerError): raise
        raise CoreBootBrokerError('PROTOCOL') from None
    return validate_message(value)


def file_digest(identity, raw):
    """Pure exact file/absence digest. It proves neither origin nor a lease."""
    if identity is None or raw is None:
        _need(identity is None and raw is None)
        return hashlib.sha256(_canonical({'absent': True})).hexdigest()
    _need(type(identity) in (tuple, list) and len(identity) == 9 and all(_uint(part) for part in identity)
          and type(raw) is bytes and len(raw) <= MAX_FRAME and identity[6] == len(raw))
    return hashlib.sha256(_canonical(dict(identity=list(identity), bytes_sha256=hashlib.sha256(raw).hexdigest()))).hexdigest()


def _remaining(deadline, stop=None):
    _need(type(deadline) in (int, float) and math.isfinite(deadline), 'DEADLINE')
    if stop is not None:
        _need(callable(stop), 'CANCELLED')
        try: cancelled = stop()
        except CoreBootBrokerError: raise
        except Exception: raise CoreBootBrokerError('CANCELLED') from None
        _need(type(cancelled) is bool, 'CANCELLED')
        _need(not cancelled, 'CANCELLED')
    remaining = deadline-time.monotonic()
    _need(remaining > 0, 'DEADLINE')
    return min(remaining, 0.1)


def send_frame(sock, message, deadline, stop=None):
    raw = memoryview(encode_frame(message))
    while raw:
        timeout = _remaining(deadline, stop)
        try:
            sock.settimeout(timeout)
            count = sock.send(raw)
        except socket.timeout: continue
        except OSError: raise CoreBootBrokerError('IO_ERROR') from None
        _need(count > 0, 'EOF')
        raw = raw[count:]


def recv_frame(sock, deadline, stop=None):
    def exact(size):
        parts, count = [], 0
        while count < size:
            timeout = _remaining(deadline, stop)
            try:
                sock.settimeout(timeout)
                part = sock.recv(size-count)
            except socket.timeout: continue
            except OSError: raise CoreBootBrokerError('IO_ERROR') from None
            _need(bool(part), 'EOF')
            parts.append(part); count += len(part)
        return b''.join(parts)
    header = exact(4)
    size = struct.unpack('!I', header)[0]
    _need(0 < size <= MAX_FRAME, 'FRAME_LIMIT')
    return decode_frame(header+exact(size))


def peer_credentials(sock):
    _need(hasattr(socket, 'SO_PEERCRED') and os.name == 'posix', 'PEERCRED_UNAVAILABLE')
    try: raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('=iII'))
    except OSError: raise CoreBootBrokerError('PEERCRED_FAILED') from None
    _need(len(raw) == struct.calcsize('=iII'), 'PEERCRED_FAILED')
    pid, uid, gid = struct.unpack('=iII', raw)
    _need(_uint(pid, 2**31-1, positive=True) and _uint(uid, 2**32-1) and _uint(gid, 2**32-1), 'PEERCRED_FAILED')
    return pid, uid, gid


def check_root_peer(sock, expected_uid=0):
    _need(_uint(expected_uid, 2**32-1), 'PEERCRED_FAILED')
    peer = peer_credentials(sock)
    _need(peer[1] == expected_uid, 'ROOT_PEER_REQUIRED')
    return peer


@dataclass(frozen=True)
class PeerIdentity:
    pid: int
    uid: int
    start_identity: str
    cgroup: str


def _identity(value, uid):
    _need(type(value) is PeerIdentity and _uint(value.pid, 2**31-1, positive=True)
          and value.uid == uid and type(value.uid) is int
          and type(value.start_identity) is str and 0 < len(value.start_identity) <= 256
          and type(value.cgroup) is str and 0 < len(value.cgroup) <= 4096, 'IDENTITY_CHANGED')


@dataclass(frozen=True)
class BrokerLayout:
    directory: Path = Path('/run/v-ui-maintenance')
    owner: int = 0
    anchor: Path = Path('/')


PRODUCTION_LAYOUT = BrokerLayout()


class CoreBootBroker:
    def __init__(self, context, *, service_uid, service_gid, proof, identity_query,
                 timeout=60, layout=PRODUCTION_LAYOUT, credentials=None):
        _context(context)
        _tree(context)
        _need(_uint(service_uid, 2**32-1, positive=True) and _uint(service_gid, 2**32-1, positive=True), 'SERVICE_IDENTITY')
        _need(callable(proof) and callable(identity_query) and type(timeout) in (int, float)
              and math.isfinite(timeout) and 0 < timeout <= 300, 'BROKER_CONTEXT')
        _need(type(layout) is BrokerLayout and (layout.anchor != Path('/') or layout == PRODUCTION_LAYOUT), 'LAYOUT')
        _need(credentials is None or layout != PRODUCTION_LAYOUT and layout.anchor != Path('/'), 'TEST_SEAM')
        self._context = deepcopy(context)
        self.session_id = secrets.token_hex(16)
        self.endpoint = layout.directory / 'core-boot.sock'
        self._uid, self._gid, self._layout = service_uid, service_gid, layout
        self._proof, self._query = proof, identity_query
        self._credentials = peer_credentials if credentials is None else credentials
        self._owner_pid, self._timeout = os.getpid(), timeout
        self._lock = threading.RLock()
        self._stop, self._armed, self._ready, self._finished = (threading.Event() for _ in range(4))
        self._commands = queue.Queue()
        self._expected = None
        self._claimed = self._released = self._command_pending = self._cleanup_failed = False
        self._state, self._error, self._reason = 'new', None, 'broker_closed'
        self._listener = self._connection = self._thread = self._parent = self._owned_inode = None
        self._files = ExitStack()
        self._deadline = None
    def __reduce__(self): raise CoreBootBrokerError('BROKER_SCOPE')
    def __copy__(self): raise CoreBootBrokerError('BROKER_SCOPE')
    def __deepcopy__(self, memo): raise CoreBootBrokerError('BROKER_SCOPE')
    @property
    def state(self):
        with self._lock: return self._state
    def _owner(self): _need(self._owner_pid == os.getpid(), 'BROKER_SCOPE')
    def _open_parent(self):
        layout = self._layout
        _need(os.geteuid() == (0 if layout.anchor == Path('/') else layout.owner), 'ROOT_REQUIRED')
        _need(type(layout.owner) is int and layout.owner >= 0 and isinstance(layout.directory, Path)
              and isinstance(layout.anchor, Path) and layout.directory.is_absolute() and layout.anchor.is_absolute()
              and '..' not in layout.directory.parts and '..' not in layout.anchor.parts
              and layout.directory != layout.anchor, 'LAYOUT')
        try: parts = layout.directory.relative_to(layout.anchor).parts
        except ValueError: raise CoreBootBrokerError('LAYOUT') from None
        fd = os.open(layout.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self._files.callback(os.close, fd)
        anchor_info = os.fstat(fd)
        _need(stat.S_ISDIR(anchor_info.st_mode) and anchor_info.st_uid == (0 if layout.anchor == Path('/') else layout.owner)
              and not anchor_info.st_mode & 0o022, 'PARENT_UNSAFE')
        self._bindings = []
        for index, name in enumerate(parts):
            last = index == len(parts)-1
            created = False
            if last:
                try: os.mkdir(name, 0o750, dir_fd=fd); created = True
                except FileExistsError: pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            self._files.callback(os.close, child)
            if created:
                os.fchown(child, layout.owner, self._gid); os.fchmod(child, 0o750); os.fsync(child); os.fsync(fd)
            info = os.fstat(child)
            _need(info.st_uid == layout.owner and stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o022
                  and (not last or stat.S_IMODE(info.st_mode) == 0o750 and info.st_gid == self._gid), 'PARENT_UNSAFE')
            self._bindings.append((fd, name, child, self._dir_id(info)))
            fd = child
        self._parent = fd
    @staticmethod
    def _dir_id(info): return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid
    def _parent_bound(self):
        for parent, name, fd, expected in self._bindings:
            _need(self._dir_id(os.fstat(fd)) == expected
                  and self._dir_id(os.stat(name, dir_fd=parent, follow_symlinks=False)) == expected, 'PARENT_CHANGED')
    def listen(self):
        self._owner()
        with self._lock:
            _need(self._state == 'new', 'BROKER_STATE')
            try:
                _need(os.geteuid() == (0 if self._layout.anchor == Path('/') else self._layout.owner), 'ROOT_REQUIRED')
                _need(self._credentials is not peer_credentials or hasattr(socket, 'SO_PEERCRED'), 'PEERCRED_UNAVAILABLE')
                self._open_parent()
                self._parent_bound()
                try: os.stat('core-boot.sock', dir_fd=self._parent, follow_symlinks=False)
                except FileNotFoundError: pass
                else: raise CoreBootBrokerError('ENDPOINT_EXISTS')
                self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._listener.bind(str(self.endpoint))
                info = os.stat('core-boot.sock', dir_fd=self._parent, follow_symlinks=False)
                _need(stat.S_ISSOCK(info.st_mode) and info.st_uid == self._layout.owner, 'ENDPOINT_CHANGED')
                self._owned_inode = info.st_dev, info.st_ino
                os.chown('core-boot.sock', self._layout.owner, self._gid, dir_fd=self._parent, follow_symlinks=False)
                os.chmod('core-boot.sock', 0o660, dir_fd=self._parent, follow_symlinks=False)
                self._parent_bound()
                self._listener.listen(8)
                self._deadline = time.monotonic()+self._timeout
                self._state = 'listening'
                self._thread = threading.Thread(target=self._run, name='vui-core-boot', daemon=True)
                self._thread.start()
            except BaseException as error:
                self._state = 'failed'
                self.close()
                if isinstance(error, CoreBootBrokerError) or not isinstance(error, Exception):
                    raise
                raise CoreBootBrokerError('LISTEN_FAILED') from None
    def _live(self, connection=None):
        self._owner()
        _remaining(self._deadline, self._stop.is_set)
        self._parent_bound()
        try: result = self._proof()
        except Exception: raise CoreBootBrokerError('PROOF_FAILED') from None
        _need(result is None, 'PROOF_FAILED')
        expected = self._expected
        _need(expected is not None, 'NOT_ARMED')
        try: observed = self._query()
        except Exception: raise CoreBootBrokerError('IDENTITY_CHANGED') from None
        _identity(observed, self._uid)
        _need(observed == expected, 'IDENTITY_CHANGED')
        if connection is not None:
            peer = self._credentials(connection)
            _need(type(peer) is tuple and len(peer) == 3 and peer[:2] == (expected.pid, expected.uid), 'IDENTITY_CHANGED')
        _remaining(self._deadline, self._stop.is_set)
    def _watched_stop(self, connection):
        """Bounded scheduling for live checks while no complete frame arrives.

        Callbacks themselves must be trusted and bounded. This cannot preempt
        an arbitrary blocking proof/query; close reports a still-live thread.
        The explicit before/after checks at control boundaries remain required.
        """
        next_check = time.monotonic() + _LIVE_INTERVAL
        def stopped():
            nonlocal next_check
            if self._stop.is_set():
                return True
            if time.monotonic() >= next_check:
                self._live(connection)
                next_check = time.monotonic() + _LIVE_INTERVAL
            return False
        return stopped

    def arm(self, expected):
        self._owner()
        with self._lock:
            _need(self._state == 'listening' and self._expected is None and not self._claimed, 'BROKER_STATE')
            _identity(expected, self._uid)
            self._expected = expected
            try: self._live(); self._live()
            except BaseException:
                self.revoke('proof_failed'); raise
            self._armed.set()
    def _session(self, message, op):
        if message['op'] == 'revoke':
            _need(message.get('session_id') == self.session_id, 'PROTOCOL')
            raise CoreBootBrokerError('CLIENT_REVOKED')
        _need(message['op'] == op and message.get('session_id') == self.session_id, 'PROTOCOL')
    def _claim(self):
        while True:
            _remaining(self._deadline, self._stop.is_set)
            self._listener.settimeout(min(0.1, self._deadline-time.monotonic()))
            try: connection, _ = self._listener.accept()
            except socket.timeout: continue
            while not self._armed.is_set():
                try: _remaining(self._deadline, self._stop.is_set)
                except BaseException: connection.close(); raise
                self._armed.wait(0.02)
            try:
                peer = self._credentials(connection)
                if peer[:2] != (self._expected.pid, self._uid):
                    connection.close(); continue
                self._connection = connection
                self._live(connection)
                request = recv_frame(connection, self._deadline, self._stop.is_set)
                _need(request['op'] == 'claim', 'PROTOCOL')
                _need(all(request[name] == self._context[name] for name in ('app_source', 'target', 'data_generation')), 'CONTEXT_CHANGED')
                self._live(connection)
                with self._lock:
                    _need(not self._claimed, 'CLAIM_CONSUMED')
                    self._claimed = True; self._state = 'claimed'
                send_frame(connection, dict(protocol=PROTOCOL, op='context', session_id=self.session_id,
                                           **self._context), self._deadline, self._stop.is_set)
                return connection
            except BaseException:
                if self._connection is not connection: connection.close()
                raise
    def _check_probe(self, message):
        observed = message['observed']
        for key in ('app_source', 'target', 'data_generation', 'application', 'barriers'):
            _need(observed[key] == self._context[key], 'CONTEXT_CHANGED')
        if message['ok']:
            _need(observed['selection_revision'] == self._context['selection']['revision'], 'CONTEXT_CHANGED')
            for core in CORES:
                row, intent = observed['cores'][core], self._context['intents'][core]
                _need(row['enabled'] == intent['enabled'] and row['revision'] == intent['revision']
                      and row['running'] == intent['enabled'] and row['owned_listeners_ready'] == intent['enabled'], 'CONTEXT_CHANGED')
                if row['running']:
                    _need(row['artifact_id'] == intent['artifact_id'] and row['binary_sha256'] ==
                          self._context['selection']['cores'][core]['current']['binary_sha256'], 'CONTEXT_CHANGED')
    def _run(self):
        try:
            connection = self._claim()
            self._live(connection)
            watched_stop = self._watched_stop(connection)
            ready = recv_frame(connection, self._deadline, watched_stop)
            self._session(ready, 'startup_ready')
            self._live(connection)
            with self._lock: self._state = 'ready'
            self._ready.set()
            while True:
                _remaining(self._deadline, watched_stop)
                # An idle authenticated peer can revoke/disconnect before the
                # coordinator's next command. Observe EOF instead of retaining
                # a seemingly live claim until its total deadline expires.
                if select.select([connection], [], [], 0)[0]:
                    unsolicited = recv_frame(connection, self._deadline, watched_stop)
                    self._session(unsolicited, 'no_unsolicited_message')
                try: command = self._commands.get(timeout=0.05)
                except queue.Empty: continue
                op, data, event, result = command
                try:
                    self._live(connection)
                    if op == 'request_probe':
                        probe_id = secrets.token_hex(16)
                        send_frame(connection, dict(protocol=PROTOCOL, op=op, session_id=self.session_id, probe_id=probe_id),
                                   self._deadline, watched_stop)
                        answer = recv_frame(connection, self._deadline, watched_stop)
                        self._session(answer, 'probe_result')
                        _need(answer['probe_id'] == probe_id, 'PROTOCOL')
                        self._check_probe(answer)
                        self._live(connection)
                        with self._lock: self._state = 'probed' if answer['ok'] else 'probe_failed'
                        result['value'] = answer
                    else:
                        send_frame(connection, dict(protocol=PROTOCOL, op='release', session_id=self.session_id,
                            selection_revision=self._context['selection']['revision'], barriers=data),
                            self._deadline, watched_stop)
                        self._session(recv_frame(connection, self._deadline, watched_stop), 'release_ack')
                        self._live(connection)
                        send_frame(connection, dict(protocol=PROTOCOL, op='released', session_id=self.session_id,
                            selection_revision=self._context['selection']['revision']), self._deadline, watched_stop)
                        with self._lock: self._released = True; self._state = 'released'
                        result['value'] = None
                        return
                finally:
                    with self._lock: self._command_pending = False
                    event.set()
        except BaseException as error:
            with self._lock:
                self._error = error.code if isinstance(error, CoreBootBrokerError) else 'BROKER_FAILED'
                if not self._released: self._state = 'revoked'
            self._best_revoke()
        finally:
            self._ready.set(); self._finished.set()
            self._close_sockets()
    def _best_revoke(self):
        if self._released or self._connection is None: return
        reason = self._reason
        if not self._stop.is_set():
            reason = ('deadline' if self._error == 'DEADLINE' else 'identity_changed' if self._error == 'IDENTITY_CHANGED'
                      else 'proof_failed' if self._error == 'PROOF_FAILED' else 'protocol_error')
        try:
            self._connection.settimeout(0.1)
            self._connection.sendall(encode_frame(dict(protocol=PROTOCOL, op='revoke', session_id=self.session_id, reason=reason)))
        except Exception: pass
    def wait_ready(self):
        self._owner()
        while not self._ready.wait(0.05): _remaining(self._deadline, self._stop.is_set)
        _need(self.state in ('ready', 'probed', 'probe_failed'), self._error or 'BROKER_STATE')
    def _command(self, op, data=None):
        self._owner()
        with self._lock:
            _need(self._state in (('ready', 'probed', 'probe_failed') if op == 'request_probe' else ('probed',))
                  and not self._command_pending and not self._stop.is_set(), 'BROKER_STATE')
            self._command_pending = True
            event, result = threading.Event(), {}
            self._commands.put((op, data, event, result))
        while not event.wait(0.02):
            _need(not self._finished.is_set(), self._error or 'BROKER_FAILED')
            _remaining(self._deadline, self._stop.is_set)
        _need('value' in result, self._error or 'BROKER_FAILED')
        return result['value']
    def request_probe(self): return self._command('request_probe')
    def release(self, final_barriers):
        _hashes(final_barriers, BARRIER_FIELDS)
        return self._command('release', deepcopy(final_barriers))
    def revoke(self, reason='cancelled'):
        self._owner(); _need(reason in REASONS, 'PROTOCOL')
        with self._lock:
            if self._released: return
            self._reason = reason; self._stop.set(); self._armed.set()
    def _close_sockets(self):
        with self._lock:
            sockets = (self._connection, self._listener)
            self._connection = self._listener = None
        failed = False
        for sock in sockets:
            if sock is not None:
                try: sock.close()
                except OSError: failed = True
        if failed:
            with self._lock: self._cleanup_failed = True
        return failed
    @property
    def callbacks_finished(self):
        thread = self._thread
        return thread is None or not thread.is_alive()

    def close(self, *, _join_timeout=1):
        self._owner()
        _need(type(_join_timeout) in (int, float) and 0 < _join_timeout <= 15, 'BROKER_CONTEXT')
        self.revoke('broker_closed')
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            # start() can fail before the thread has an identity. Such a failure
            # still owns the listener/parent FDs and must not skip their cleanup.
            if thread.ident is not None: thread.join(timeout=_join_timeout)
            _need(not thread.is_alive(), 'THREAD_STILL_RUNNING')
        self._finish_close()

    def _finish_close(self):
        # No join/retry of a released FD; this is the final owned-endpoint
        # cleanup after a thread that cannot be restarted has actually ended.
        self._owner()
        _need(self.callbacks_finished, 'THREAD_STILL_RUNNING')
        failed = self._close_sockets() or self._cleanup_failed
        try:
            if self._owned_inode is not None and self._parent is not None:
                try: info = os.stat('core-boot.sock', dir_fd=self._parent, follow_symlinks=False)
                except FileNotFoundError: info = None
                if info is not None:
                    if (info.st_dev, info.st_ino) != self._owned_inode or not stat.S_ISSOCK(info.st_mode):
                        failed = True
                    else:
                        os.unlink('core-boot.sock', dir_fd=self._parent); os.fsync(self._parent)
                self._owned_inode = None
        finally:
            try: self._files.close()
            except OSError: failed = True
            finally:
                self._parent = None
                if not self._released: self._state = 'closed'
        _need(not failed, 'CLEANUP_FAILED')
