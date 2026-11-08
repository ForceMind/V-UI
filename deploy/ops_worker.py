"""Private non-root maintenance worker for the root-owned managed controller.

The installed copy loads only its trusted sibling modules. It never
controls services and never accepts commands that skip ownership or leases.
The root controller must prove service quiescence before sending proceed, then
keep the worker alive until service recovery is verified and finish is sent.
"""
from __future__ import annotations

import importlib.util
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import selectors
import signal
import sys
import time
import types


ROOT = Path('/var/lib/v-ui')
TIMEOUT = 600
LINE_LIMIT = 8192
PATH_LIMIT = 4096


class WorkerError(RuntimeError):
    pass


class _Deadline(BaseException):
    pass


def _object(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise WorkerError('Duplicate protocol field')
        value[name] = item
    return value


class _Channel:
    def __init__(self, input_fd, output, deadline):
        self.input_fd, self.output, self.deadline = input_fd, output, deadline
        self.buffer = bytearray()

    def read(self):
        while b'\n' not in self.buffer:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise WorkerError('Worker deadline reached')
            with selectors.DefaultSelector() as selector:
                selector.register(self.input_fd, selectors.EVENT_READ)
                if not selector.select(remaining):
                    raise WorkerError('Worker deadline reached')
            part = os.read(self.input_fd, LINE_LIMIT + 1 - len(self.buffer))
            if not part:
                raise WorkerError('Controller disconnected')
            self.buffer.extend(part)
            if len(self.buffer) > LINE_LIMIT:
                raise WorkerError('Protocol line exceeded limit')
        line, _, rest = self.buffer.partition(b'\n')
        self.buffer = bytearray(rest)
        value = json.loads(line.decode('utf-8'), object_pairs_hook=_object)
        if not isinstance(value, dict):
            raise WorkerError('Protocol object required')
        return value

    def expect(self, command):
        if self.read() != {'command': command}:
            raise WorkerError('Unexpected controller command')

    def send(self, event, **values):
        self.output.write(json.dumps({'event': event, **values}, separators=(',', ':')) + '\n')
        self.output.flush()



def _command(channel, allowed):
    value = channel.read()
    if (set(value) != {'command'} or type(value.get('command')) is not str
            or value['command'] not in allowed):
        raise WorkerError('Unexpected controller command')
    return value['command']


def _prove_released_operation(session):
    # The phase is enforced by the private wire state machine. This is M9's
    # actual held-FD/closed-latch/PID proof, never an alive/approved bool.
    proof = getattr(session, '_operation_proof', None)
    if not callable(proof) or proof() is not None:
        raise WorkerError('Maintenance operation proof unavailable')


def _complete_writer_handshake(channel, session, action):
    if action == 'stage':
        channel.expect('finish')
        return
    command = _command(channel, ('release_writers', 'release_writers_for_trial'))
    if command == 'release_writers':
        session.release_writers()
        channel.send('writers_released')
        channel.expect('finish')
        return
    session.release_writers_for_trial()
    channel.send('trial_writers_released')
    phase = 'trial_writers_released'
    while True:
        allowed = (('prove_operation', 'reacquire_writers', 'finish') if phase == 'trial_writers_released'
                   else ('release_reacquired_writers',) if phase == 'writers_reacquired'
                   else ('prove_operation', 'finish'))
        command = _command(channel, allowed)
        if command == 'finish':
            # Root must have independently confirmed stable files/managed
            # released before this internal cleanup. finish grants nothing.
            return
        if command == 'prove_operation':
            _prove_released_operation(session)
            channel.send('operation_held')
        elif command == 'reacquire_writers':
            session.reacquire_writers()
            session._check_reacquired_writers()
            phase = 'writers_reacquired'
            channel.send(phase)
        else:
            session.release_reacquired_writers()
            phase = 'recovery_writers_released'
            channel.send(phase)



CORE_IMPLEMENTATIONS = (
    ('app.core_updates.artifacts', 'core_artifacts.py'),
    ('app.core_updates.selection', 'core_selection.py'),
    ('app.core_updates.binary', 'core_binary.py'),
    ('app.core_updates.store', 'core_store.py'),
    ('app.core_updates.github', 'core_github.py'),
    ('app.core_updates.catalog', 'core_catalog.py'),
    ('app.core_updates.resolver', 'core_resolver.py'),
    ('app.core_updates.application', 'core_application.py'),
    ('app.core_updates.selection_files', 'core_selection_files.py'),
    ('app.core_updates.boot', 'core_boot.py'),
    ('app.core_updates.backup', 'core_backup.py'),
    ('app.core_updates.backup_archive', 'core_backup_archive.py'),
    ('app.core_updates.maintenance', 'core_maintenance.py'),
)


def _load_core_session(release_tools):
    """Only private requests need this fixed CONTROL closure, never payload."""
    directory = Path(__file__).resolve().parent
    names = ('app.core_updates', 'app.release_tools') + tuple(name for name, _ in CORE_IMPLEMENTATIONS)
    if any(name in sys.modules for name in names):
        raise WorkerError('Unexpected core maintenance namespace')
    if any(name not in sys.modules or getattr(sys.modules[name], '__path__', None) != []
           for name in ('app', 'app.services', 'app.certificates')):
        raise WorkerError('Core maintenance requires isolated control namespaces')
    loaded = []
    try:
        package = types.ModuleType('app.core_updates'); package.__path__ = []
        sys.modules['app.core_updates'] = package; loaded.append('app.core_updates')
        sys.modules['app.release_tools'] = release_tools; loaded.append('app.release_tools')
        for name, filename in CORE_IMPLEMENTATIONS:
            spec = importlib.util.spec_from_file_location(name, directory / filename)
            if spec is None or spec.loader is None:
                raise WorkerError('Core maintenance implementation unavailable')
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; loaded.append(name)
            spec.loader.exec_module(module)
        return module.CoreMaintenanceSession
    except BaseException:
        for name in reversed(loaded):sys.modules.pop(name, None)
        raise


def _core_request(value):
    if (set(value) != {'protocol', 'action', 'core', 'expected_selection_revision'}
            or type(value.get('protocol')) is not int or value['protocol'] != 1
            or value.get('action') != 'core_prepare' or value.get('core') not in ('sing-box', 'xray')
            or not (value['expected_selection_revision'] is None or type(value['expected_selection_revision']) is str
                    and re.fullmatch('[a-f0-9]{64}', value['expected_selection_revision']))):
        raise WorkerError('Invalid private core maintenance request')
    return dict(core=value['core'], expected_selection_revision=value['expected_selection_revision'])


def _core_fields(channel, command, fields=()):
    value = channel.read()
    if set(value) != {'command', *fields} or value.get('command') != command:
        raise WorkerError('Unexpected private core command')
    return value


def _complete_core_handshake(channel, session):
    with _quiet_candidate_io():facts = session.prepare()
    channel.send('core_context', **facts)
    value = channel.read()
    if value == {'command': 'finish'}:return
    if set(value) != {'command', 'candidate'} or value.get('command') != 'check_core':
        raise WorkerError('Unexpected private core command')
    with _quiet_candidate_io():facts = session.check_core(value['candidate'])
    channel.send('prepared', **facts)
    channel.expect('proceed')
    with _quiet_candidate_io():facts = session.execute()
    channel.send('core_quiesced', **facts)
    value = _core_fields(channel, 'backup_core', ('snapshot',))
    with _quiet_candidate_io():facts = session.backup_core(value['snapshot'])
    channel.send('core_backed_up', **facts)
    value = _core_fields(channel, 'bind_core_ids', ('backup_sha256',))
    with _quiet_candidate_io():facts = session.bind_core_ids(value['backup_sha256'])
    channel.send('core_ids_bound', **facts)
    channel.expect('release_writers_for_trial')
    session.release_writers_for_trial();channel.send('trial_writers_released')
    phase = 'trial_writers_released'
    while True:
        allowed = (('prove_operation', 'reacquire_writers', 'finish') if phase == 'trial_writers_released'
                   else ('restore_core_ids',) if phase == 'writers_reacquired'
                   else ('release_reacquired_writers',) if phase == 'core_ids_restored'
                   else ('prove_operation', 'finish'))
        command = _command(channel, allowed)
        if command == 'finish':return
        if command == 'prove_operation':
            _prove_released_operation(session);channel.send('operation_held')
        elif command == 'reacquire_writers':
            session.reacquire_writers();session._check_reacquired_writers()
            phase = 'writers_reacquired';channel.send(phase)
        elif command == 'restore_core_ids':
            with _quiet_candidate_io():facts = session.restore_core_ids()
            phase = 'core_ids_restored';channel.send(phase, **facts)
        else:
            session.release_reacquired_writers();phase = 'recovery_writers_released';channel.send(phase)


def _request(value):
    action = value.get('action')
    if type(value.get('protocol')) is not int or value['protocol'] != 1 or not isinstance(action, str):
        raise WorkerError('Unsupported maintenance protocol')
    fields = {'protocol', 'action'}
    if action in ('restore', 'stage'):
        fields |= {'path', 'sha256'}
    elif action == 'activate':
        fields.add('release_id')
    elif action not in ('start', 'stop', 'restart', 'backup', 'recover-restore'):
        raise WorkerError('Unknown maintenance action')
    if set(value) != fields:
        raise WorkerError('Unexpected maintenance fields')
    result = {}
    if action in ('restore', 'stage'):
        path, checksum = value['path'], value['sha256']
        if (not isinstance(path, str) or not 0 < len(path) <= PATH_LIMIT
                or any(ord(char) < 32 or ord(char) == 127 for char in path)
                or not Path(path).is_absolute() or '..' in Path(path).parts
                or not isinstance(checksum, str) or not re.fullmatch(r'[a-f0-9]{64}', checksum)):
            raise WorkerError('Invalid restore input')
        result.update(archive=Path(path), sha256=checksum)
    if action == 'activate':
        identity = value['release_id']
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', identity):
            raise WorkerError('Invalid release identifier')
        result['identity'] = identity
    return action, result


@contextmanager
def _quiet_candidate_io():
    """Candidate subprocesses get neither protocol input nor event output."""
    sys.stdout.flush(); sys.stderr.flush()
    saved = [os.dup(fd) for fd in (0, 1, 2)]
    for fd in saved:
        os.set_inheritable(fd, False)
    sink = os.open(os.devnull, os.O_RDWR)
    try:
        for fd in (0, 1, 2):
            os.dup2(sink, fd)
        yield
    finally:
        sys.stdout.flush(); sys.stderr.flush()
        for fd, original in zip((0, 1, 2), saved):
            os.dup2(original, fd); os.close(original)
        os.close(sink)


def _run(root, release_tools, input_fd, output, *, timeout=TIMEOUT):
    """Private testable implementation; the executable has no root/path flags."""
    deadline = time.monotonic() + timeout
    channel = _Channel(input_fd, output, deadline)
    session = None
    previous_alarm = signal.getsignal(signal.SIGALRM)
    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)

    deadline_active = True

    def interrupted(number, _frame):
        if number != signal.SIGALRM or deadline_active:
            raise _Deadline()

    signal.signal(signal.SIGALRM, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        try:
            if os.geteuid() == 0:
                raise WorkerError('Worker must run as the service user')
            os.umask(0o077)
            request = channel.read()
            if request.get('action') == 'core_prepare':
                arguments = _core_request(request)
                implementation = _load_core_session(release_tools)
                session = implementation(root, **arguments)
                _complete_core_handshake(channel, session)
            else:
                action, arguments = _request(request)
                session = release_tools._MaintenanceSession(root)
                session.prepare(action, **arguments)
                channel.send('prepared')
                channel.expect('proceed')
                with _quiet_candidate_io():
                    result = session.execute()
                channel.send('done', ok=True, **result)
                _complete_writer_handshake(channel, session, action)
            status = 0
        finally:
            # The selector can reach the same deadline before SIGALRM is
            # dispatched. Retire it before encoding the fixed error frame.
            deadline_active = False
            signal.setitimer(signal.ITIMER_REAL, 0)
    except (Exception, _Deadline):
        deadline_active = False
        status = 1
        try: channel.send('error', code='MAINTENANCE_REFUSED')
        except (OSError, ValueError, _Deadline): pass
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        try:
            if session is not None:
                session.close()
        except (Exception, _Deadline):
            # A cleanup failure is still a failed worker transaction, including
            # after finish; do not override it with an earlier success return.
            try: channel.send('error', code='MAINTENANCE_REFUSED')
            except (OSError, ValueError): pass
            status = 1
        finally:
            signal.signal(signal.SIGALRM, previous_alarm)
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)
    return status


def _load_release_tools():
    # The root controller verified these fixed sibling files against its receipt.
    # -I -S excludes cwd/PYTHONPATH/site hooks. Do not add a package payload (or
    # any directory) to sys.path: create inert, pathless namespaces and load only
    # the exact stdlib dependency closure needed by backup/restore. No app startup,
    # parser, core binary, or service-owned installed code is imported here.
    directory = Path(__file__).resolve().parent
    packages = ('app', 'app.services', 'app.certificates')
    implementations = (('app.data_owner', 'data_owner.py'),
                       ('app.services.upstream_profile', 'upstream_profile.py'),
                       ('app.services.inbound_policy', 'inbound_policy.py'),
                       ('app.services.proxy_store', 'proxy_store.py'),
                       ('app.certificates.errors', 'certificate_errors.py'),
                       ('app.certificates.dns_store', 'dns_store.py'),
                       ('app.certificates.cloudflare', 'cloudflare.py'),
                       ('app.certificates.dns_journal', 'dns_journal.py'),
                       ('app.certificates.dns_backup', 'dns_backup.py'),
                       ('_vui_managed_release_tools', 'release_tools.py'))
    names = packages + tuple(name for name, _ in implementations)
    if any(name in sys.modules for name in names):
        raise WorkerError('Unexpected application modules in isolated worker')
    loaded = []
    try:
        for name in packages:
            package = types.ModuleType(name)
            package.__path__ = []
            sys.modules[name] = package
            loaded.append(name)
        for name, filename in implementations:
            spec = importlib.util.spec_from_file_location(name, directory / filename)
            if spec is None or spec.loader is None:
                raise WorkerError('Maintenance implementation unavailable')
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            loaded.append(name)
            spec.loader.exec_module(module)
        return module
    except BaseException:
        for name in reversed(loaded):
            sys.modules.pop(name, None)
        raise


def main():
    if len(sys.argv) != 1 or os.geteuid() == 0:
        return 1
    try:
        return _run(ROOT, _load_release_tools(), sys.stdin.fileno(), sys.stdout)
    except Exception:
        print('{"event":"error","code":"MAINTENANCE_REFUSED"}', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
