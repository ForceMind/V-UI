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

    def interrupted(*_):
        raise _Deadline()

    signal.signal(signal.SIGALRM, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        if os.geteuid() == 0:
            raise WorkerError('Worker must run as the service user')
        os.umask(0o077)
        action, arguments = _request(channel.read())
        session = release_tools._MaintenanceSession(root)
        session.prepare(action, **arguments)
        channel.send('prepared')
        channel.expect('proceed')
        with _quiet_candidate_io():
            result = session.execute()
        channel.send('done', ok=True, **result)
        if action != 'stage':
            channel.expect('release_writers')
            session.release_writers()
            channel.send('writers_released')
        channel.expect('finish')
        return 0
    except (Exception, _Deadline):
        try: channel.send('error', code='MAINTENANCE_REFUSED')
        except (OSError, ValueError): pass
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        try:
            if session is not None:
                session.close()
        finally:
            signal.signal(signal.SIGALRM, previous_alarm)
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)


def _load_release_tools():
    # The root controller verified these fixed sibling files against its receipt.
    # -I -S excludes cwd/PYTHONPATH/site hooks. Do not add a package payload (or
    # any directory) to sys.path: create inert, pathless namespaces and load only
    # the three exact stdlib dependencies needed by backup/restore. No app startup,
    # parser, core binary, or service-owned installed code is imported here.
    directory = Path(__file__).resolve().parent
    names = ('app', 'app.services', 'app.data_owner', 'app.services.upstream_profile',
             'app.services.proxy_store')
    if any(name in sys.modules for name in names):
        raise WorkerError('Unexpected application modules in isolated worker')
    loaded = []
    try:
        for name in names[:2]:
            package = types.ModuleType(name)
            package.__path__ = []
            sys.modules[name] = package
            loaded.append(name)
        for name, filename in (('app.data_owner', 'data_owner.py'),
                               ('app.services.upstream_profile', 'upstream_profile.py'),
                               ('app.services.proxy_store', 'proxy_store.py'),
                               ('_vui_managed_release_tools', 'release_tools.py')):
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
