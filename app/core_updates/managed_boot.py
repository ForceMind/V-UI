"""Private authenticated managed-boot transport, not a data-owner permit.

Only trusted serve code supplies observed file facts and actual probe/settlement
callbacks. A decoded JSON context or this object alone does not authenticate a
Root operation, authorize filesystem writes, widen ordinary boot, or grant core
eligibility. The Root broker must authenticate the exact service process; this
client verifies its Root peer and binds the response to observed app/data facts.
No environment/CLI/UserURL entry point is provided.
"""
from copy import deepcopy
import os
from pathlib import Path
import socket
import select
import struct
import stat
import threading
import time

from deploy import core_boot_broker as wire
from . import selection as s, store as fs


class ManagedBootError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(value, code):
    if not value:
        raise ManagedBootError(code)


def _socket_path(path, owner, anchor):
    """Read-only path trust; alternative owner/anchor are private test seams."""
    _need(isinstance(path, Path) and isinstance(anchor, Path) and path.is_absolute()
          and anchor.is_absolute() and '..' not in path.parts and '..' not in anchor.parts
          and anchor in path.parents and type(owner) is int and owner >= 0, 'SOCKET_PATH')
    parents = list(reversed(path.parents))
    if anchor != Path('/'):
        parents = parents[parents.index(anchor):]
    for parent in parents:
        info = parent.lstat()
        _need(stat.S_ISDIR(info.st_mode) and info.st_uid == owner and not info.st_mode & 0o022,
              'SOCKET_PATH')
    info = path.lstat()
    _need(stat.S_ISSOCK(info.st_mode) and info.st_uid == owner
          and stat.S_IMODE(info.st_mode) == 0o660 and info.st_nlink == 1, 'SOCKET_PATH')
    return info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode


_CONNECTION = object()
_NATIVE_ORIGIN = object()
_NATIVE_SOCKET = socket.socket
_FIXED_ADDRESS = Path('/run/v-ui-maintenance/core-boot.sock')


def _kernel_peer(connection):
    _need(type(connection) is _NATIVE_SOCKET and hasattr(socket, 'SO_PEERCRED'), 'NATIVE_ROOT_REQUIRED')
    try:
        return struct.unpack('=iII', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    except (OSError, struct.error):
        raise ManagedBootError('NATIVE_ROOT_REQUIRED') from None


def require_native_connection(connection):
    """Private production boundary: fixture owners/paths/factories never pass."""
    _need(type(connection) is ManagedBootConnection and connection._native_origin is _NATIVE_ORIGIN,
          'NATIVE_ROOT_REQUIRED')
    connection._check('claimed', 'ready', 'release_acknowledged')
    peer = _kernel_peer(connection._connection)
    _need(peer == connection._peer and peer[1] == 0, 'NATIVE_ROOT_REQUIRED')
    # The control reader consumes messages. A queued EOF or complete revoke is
    # already a dead scope even if that reader has not yet been scheduled.
    try:
        if select.select([connection._connection], [], [], 0)[0]:
            raw = connection._connection.recv(wire.MAX_FRAME + 4, socket.MSG_PEEK | socket.MSG_DONTWAIT)
            _need(bool(raw), 'ROOT_DISCONNECTED')
            if len(raw) >= 4:
                size = int.from_bytes(raw[:4], 'big')
                _need(0 < size <= wire.MAX_FRAME, 'ROOT_PROTOCOL')
                if len(raw) >= size+4:
                    _need(wire.decode_frame(raw[:size+4])['op'] != 'revoke', 'ROOT_REVOKED')
    except (OSError, wire.CoreBootBrokerError):
        raise ManagedBootError('ROOT_DISCONNECTED') from None


def is_native_released(connection):
    """Completed production Root transcript, never a fixture's released flag."""
    return (type(connection) is ManagedBootConnection and connection._native_origin is _NATIVE_ORIGIN
            and connection._pid == os.getpid() and connection.released and not connection.revoked
            and type(connection._peer) is tuple and connection._peer[1] == 0)


class ManagedBootConnection:
    """One socket/PID-bound claim; no copying, serialization or reconnect."""
    def __init__(self, token, connection, peer, context, selection, deadline, stop):
        _need(token is _CONNECTION, 'CONNECTION_REQUIRED')
        self._connection, self._peer = connection, peer
        self._context, self._selection = deepcopy(context), selection
        self._deadline, self._stop = deadline, stop
        self._pid, self._state = os.getpid(), 'claimed'
        self._revoked = threading.Event()
        self._native_origin = None

    def __copy__(self):
        raise ManagedBootError('CONNECTION_REQUIRED')

    def __deepcopy__(self, memo):
        raise ManagedBootError('CONNECTION_REQUIRED')

    def __reduce__(self):
        raise ManagedBootError('CONNECTION_REQUIRED')

    @property
    def selection(self):
        return self._selection

    @property
    def context(self):
        return deepcopy(self._context)

    @property
    def released(self):
        return self._state == 'released'

    @property
    def revoked(self):
        return self._revoked.is_set()

    def _check(self, *states):
        _need(self._pid == os.getpid() and self._state in states and self._connection is not None,
              'CONNECTION_STATE')
        _need(not self._revoked.is_set() and time.monotonic() < self._deadline, 'CONNECTION_EXPIRED')
        _need(self._stop is None or not self._stop(), 'CONNECTION_CANCELLED')
        _need(wire.peer_credentials(self._connection) == self._peer, 'ROOT_PEER_CHANGED')

    def _send(self, message):
        wire.send_frame(self._connection, message, self._deadline, self._stop)

    def _receive(self):
        message = wire.recv_frame(self._connection, self._deadline, self._stop)
        _need(message.get('session_id') == self._context['session_id'], 'SESSION_CHANGED')
        if message['op'] == 'revoke':
            raise ManagedBootError('ROOT_REVOKED')
        return message

    def _close_transport(self):
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except OSError:
                raise ManagedBootError('CONNECTION_CLOSE_FAILED') from None

    def startup_ready(self):
        try:
            self._check('claimed')
            self._send(dict(protocol=wire.PROTOCOL, op='startup_ready', session_id=self._context['session_id']))
            self._state = 'ready'
        except BaseException:
            self._state = 'revoked'; self._revoked.set()
            self._close_transport()
            raise

    def run_controls(self, *, probe, settle, on_released, on_revoked):
        """Trusted callbacks only; settlement must inspect actual final files.

        All HTTP/background gates remain closed after release_ack. Only a
        complete Root released frame following its post-ack proof completes
        the transport. Before that, EOF/cancel/error always revokes.
        """
        try:
            _need(all(callable(value) for value in (probe, settle, on_released, on_revoked)), 'CALLBACK_REQUIRED')
            self._check('ready')
            while True:
                message = self._receive()
                self._check('ready')
                if message['op'] == 'request_probe':
                    observed, ok = probe()
                    self._check('ready')
                    response = dict(protocol=wire.PROTOCOL, op='probe_result', session_id=self._context['session_id'],
                        probe_id=message['probe_id'], ok=ok, observed=observed)
                    self._send(response)
                elif message['op'] == 'release':
                    _need(message['selection_revision'] == self._selection.revision, 'SELECTION_CHANGED')
                    _need(settle(deepcopy(message['barriers'])) is None, 'SETTLEMENT_REQUIRED')
                    self._check('ready')
                    self._state = 'release_acknowledged'
                    self._send(dict(protocol=wire.PROTOCOL, op='release_ack', session_id=self._context['session_id']))
                    final = self._receive()
                    self._check('release_acknowledged')
                    _need(final['op'] == 'released' and final['selection_revision'] == self._selection.revision,
                          'RELEASE_NOT_CONFIRMED')
                    self._close_transport()
                    self._state = 'released'
                    on_released()
                    return
                else:
                    raise ManagedBootError('UNEXPECTED_CONTROL')
        except BaseException:
            self._state = 'revoked'; self._revoked.set()
            try:
                self._close_transport()
            finally:
                if callable(on_revoked):
                    on_revoked()
            raise

    def close(self):
        if self._state != 'released':
            self._state = 'revoked'; self._revoked.set()
        self._close_transport()


def claim(*, app_source, target, data_generation, application, barriers, timeout=120,
          _address=None, _owner=0, _anchor=Path('/'), _socket_factory=None, _stop=None):
    """Claim once from the fixed Root broker, never from a grant/phase marker.

    Observations must come from trusted startup's actual verified files. The
    alternative socket/owner/anchor/factory are private Python fixture seams,
    not environment, request fields, CLI options or authority overrides.
    """
    _need(type(timeout) in (int, float) and 0 < timeout <= 600, 'CONNECTION_BUDGET')
    address = wire.ADDRESS if _address is None else _address
    address = Path(address)
    deadline = time.monotonic() + timeout
    connection = None
    try:
        request = dict(protocol=wire.PROTOCOL, op='claim', app_source=app_source,
                       target=target, data_generation=deepcopy(data_generation))
        wire.validate_message(request)
        before = _socket_path(address, _owner, _anchor)
        factory = socket.socket if _socket_factory is None else _socket_factory
        connection = factory(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(min(5, timeout))
        connection.connect(str(address))
        wire.check_root_peer(connection, expected_uid=_owner)
        peer = wire.peer_credentials(connection)
        _need(_socket_path(address, _owner, _anchor) == before, 'SOCKET_CHANGED')
        wire.send_frame(connection, request, deadline, _stop)
        response = wire.recv_frame(connection, deadline, _stop)
        _need(response['op'] == 'context' and response['app_source'] == app_source
              and response['target'] == target and response['data_generation'] == data_generation
              and response['application'] == application and response['barriers'] == barriers, 'BOOT_FACTS_CHANGED')
        selection = s.decode_selection(fs._canonical(response['selection']), app_source=app_source, target=target)
        for core in s.CORES:
            intent = response['intents'][core]
            _need(intent['artifact_id'] == s.reference_identity(selection.slot(core).current), 'BOOT_INTENT_CHANGED')
        _need(wire.peer_credentials(connection) == peer, 'ROOT_PEER_CHANGED')
        result = ManagedBootConnection(_CONNECTION, connection, peer, response, selection, deadline, _stop)
        # These are necessary provenance conditions, not caller-approved flags.
        # Direct kernel credentials are checked independently of test seams.
        if (_address is None and address == _FIXED_ADDRESS and _owner == 0 and _anchor == Path('/')
                and _socket_factory is None and type(connection) is _NATIVE_SOCKET):
            native = _kernel_peer(connection)
            _need(native == peer and native[1] == 0, 'NATIVE_ROOT_REQUIRED')
            result._native_origin = _NATIVE_ORIGIN
        connection = None
        return result
    except ManagedBootError:
        raise
    except (wire.CoreBootBrokerError, s.CoreSelectionError, OSError, ValueError, TypeError, KeyError):
        raise ManagedBootError('MANAGED_BOOT_UNAVAILABLE') from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                raise ManagedBootError('CONNECTION_CLOSE_FAILED') from None
