#!/usr/bin/python3
"""Small root-owned, stdlib-only maintenance controller for managed installs.

Run only through /usr/local/sbin/v-ui using the OS Python with -B -I -S. Never
import application code, select CURRENT, or open service-user data as root.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import select
import signal
import stat
import subprocess
import sys
import termios
import time

CONFIG = Path('/etc/v-ui/service.json')
CONTROL = Path('/usr/local/lib/v-ui')
RECEIPT = CONTROL / 'ops-control.json'
WRAPPER = Path('/usr/local/sbin/v-ui')
PYTHON = Path('/usr/bin/python3')
WORKER = CONTROL / 'ops_worker.py'
INSTALL_LOCK = Path('/run/lock/v-ui-install.lock')
CGROUP_ROOT = Path('/sys/fs/cgroup')
SYSTEMCTL = Path('/usr/bin/systemctl')
RC_SERVICE = Path('/sbin/rc-service')
MAX_JSON = 16384
MAX_FILE = 4_000_000
MAX_OUTPUT = 65536
TIMEOUT = 600
COMMAND_TIMEOUT = 45
TERM_GRACE = 5
REAP_TIMEOUT = 5
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
       'HOME': '/', 'PYTHONDONTWRITEBYTECODE': '1'}
SYSTEMD_UNITS = ('v-ui.service', 'v-ui-http01.service', 'v-ui-http01.socket')
OPENRC_UNITS = ('v-ui', 'v-ui-http01')
PROPERTIES = ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'ControlGroup',
              'User', 'Group', 'KillMode', 'NeedDaemonReload', 'FragmentPath', 'DropInPaths')
ACTIONS = ('start', 'stop', 'restart', 'backup', 'restore', 'activate', 'recover-restore', 'stage')
HEX = re.compile(r'[a-f0-9]{64}\Z')
RELEASE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}\Z')
BACKUP = re.compile(r'maintenance-[a-f0-9]{32}\.zip\Z')


class Refused(RuntimeError):
    """A bounded, intentionally non-sensitive failure."""


class InputInterrupted(BaseException):
    def __init__(self, number):
        self.number = number


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _root_owned(info, directory=False, sticky=False):
    if info.st_uid != 0 or (directory and not stat.S_ISDIR(info.st_mode)):
        raise Refused('untrusted ownership or type')
    if info.st_mode & 0o022:
        if not (sticky and stat.S_ISDIR(info.st_mode)
                and stat.S_IMODE(info.st_mode) == 0o1777):
            raise Refused('writable trusted path')


def _parts(path):
    value = str(path)
    if (not value.startswith('/') or value != str(Path(value))
            or any(part in ('', '.', '..') for part in value[1:].split('/'))
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise Refused('noncanonical trusted path')
    return value[1:].split('/')


@contextmanager
def trusted_directory(path, lock_parent=False):
    """Walk every ancestor via openat/O_NOFOLLOW, never by resolve()."""
    pieces = _parts(path)
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _root_owned(os.fstat(fd), directory=True)
        for index, part in enumerate(pieces):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                _root_owned(os.fstat(child), directory=True,
                            sticky=lock_parent and index == len(pieces) - 1
                            and str(path) == '/run/lock')
            except BaseException:
                os.close(child)
                raise
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def trusted_bytes(path, limit=MAX_FILE):
    with trusted_directory(Path(path).parent) as directory:
        fd = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(fd)
            _root_owned(before)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                raise Refused('invalid trusted file')
            chunks = []
            size = 0
            while True:
                chunk = os.read(fd, min(65536, limit + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > limit:
                    raise Refused('trusted file too large')
            if (_identity(before) != _identity(os.fstat(fd))
                    or _identity(before) != _identity(os.stat(Path(path).name, dir_fd=directory, follow_symlinks=False))):
                raise Refused('trusted file changed')
            return b''.join(chunks)
        finally:
            os.close(fd)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused('duplicate JSON key')
        result[key] = value
    return result


def decode_json(raw):
    try:
        result = json.loads(raw, object_pairs_hook=_object,
                            parse_constant=lambda value: (_ for _ in ()).throw(Refused('invalid JSON constant')))
    except (ValueError, UnicodeError, RecursionError):
        raise Refused('invalid JSON') from None
    if not isinstance(result, dict):
        raise Refused('JSON object required')
    return result


def trusted_executable(path):
    """OS-owned symlinks such as usr-merged /sbin are allowed, never user links."""
    path = Path(path)
    _parts(path)
    pending = [path]
    seen = set()
    while pending:
        candidate = pending.pop()
        if str(candidate) in seen:
            continue
        seen.add(str(candidate))
        if len(seen) > 40:
            raise Refused('too many executable links')
        for prefix in reversed((candidate, *candidate.parents)):
            info = prefix.lstat()
            if info.st_uid != 0:
                raise Refused('untrusted OS executable')
            if stat.S_ISLNK(info.st_mode):
                target = prefix.resolve(strict=True)
                pending.append(target)
            elif info.st_mode & 0o022:
                raise Refused('writable OS executable')
            elif prefix != candidate and not stat.S_ISDIR(info.st_mode):
                raise Refused('invalid OS executable parent')
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
        raise Refused('OS executable is unavailable')
    return resolved


def fixed_files(manager):
    base = {CONFIG, CONTROL / 'launcher.py', CONTROL / 'system_ops.py', WORKER,
            CONTROL / 'release_tools.py', WRAPPER}
    if manager == 'systemd':
        return base | {Path('/etc/systemd/system') / name for name in SYSTEMD_UNITS}
    if manager == 'openrc':
        return base | {Path('/etc/init.d') / name for name in OPENRC_UNITS}
    raise Refused('unknown service manager')


def verify_installation():
    receipt = decode_json(trusted_bytes(RECEIPT, MAX_JSON))
    if (set(receipt) != {'schema', 'service_manager', 'system_python', 'interpreter_resolved',
                        'interpreter_sha256', 'files'}
            or type(receipt['schema']) is not int or receipt['schema'] != 1
            or receipt['system_python'] != str(PYTHON)):
        raise Refused('unsupported maintenance receipt')
    manager = receipt['service_manager']
    expected = {str(path) for path in fixed_files(manager)}
    files = receipt['files']
    if not isinstance(files, dict) or set(files) != expected:
        raise Refused('incomplete maintenance receipt')
    values = {}
    for name in sorted(expected):
        checksum = files[name]
        if not isinstance(checksum, str) or not HEX.fullmatch(checksum):
            raise Refused('invalid receipt digest')
        content = trusted_bytes(Path(name), MAX_JSON if name == str(CONFIG) else MAX_FILE)
        if hashlib.sha256(content).hexdigest() != checksum:
            raise Refused('trusted installation changed')
        values[name] = content
    python = trusted_executable(PYTHON)
    checksum = receipt['interpreter_sha256']
    if (receipt['interpreter_resolved'] != str(python) or not isinstance(checksum, str)
            or not HEX.fullmatch(checksum)
            or hashlib.sha256(trusted_bytes(python, 32_000_000)).hexdigest() != checksum):
        raise Refused('OS interpreter changed; refresh maintenance receipt')
    config = decode_json(values[str(CONFIG)])
    if config.get('ready') is not True or config.get('service_manager') != manager:
        raise Refused('installation is not ready')
    account = pwd.getpwnam('v-ui')
    if account.pw_uid <= 0 or account.pw_gid <= 0:
        raise Refused('non-root service account required')
    return manager, account


@contextmanager
def installation_lock():
    with trusted_directory(INSTALL_LOCK.parent, lock_parent=True) as directory:
        fd = os.open(INSTALL_LOCK.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=directory)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0
                    or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1):
                raise Refused('invalid installation lock')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused('another installer or maintenance operation is running') from None
            after = os.stat(INSTALL_LOCK.name, dir_fd=directory, follow_symlinks=False)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise Refused('installation lock replaced')
            yield
            after = os.stat(INSTALL_LOCK.name, dir_fd=directory, follow_symlinks=False)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise Refused('installation lock replaced')
        finally:
            os.close(fd)


@contextmanager
def deferred_signals():
    requested = []
    previous = {}
    def remember(number, frame):
        if not requested:
            requested.append(number)
    try:
        for number in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
            previous[number] = signal.signal(number, remember)
        yield requested
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


class Child:
    """Bounded pipes and unreaped-leader identity guard, including cleanup."""
    def __init__(self, command, timeout, account=None, cancelled=None):
        if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
            raise Refused('child identity cannot be guarded')
        self.cancelled = cancelled if cancelled is not None else []
        self.deadline = time.monotonic() + timeout
        self.buffer = b''
        self.process = None
        kwargs = {}
        if account is not None:
            if account.pw_uid <= 0 or account.pw_gid <= 0:
                raise Refused('worker must run without root privileges')
            kwargs.update(user=account.pw_uid, group=account.pw_gid, extra_groups=[])
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGHUP})
        try:
            self.process = subprocess.Popen(command, cwd='/', env=ENV.copy(), start_new_session=True,
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, **kwargs)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        try:
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
        except BaseException:
            self.close()
            raise

    def _observed(self):
        try:
            return os.waitid(os.P_PID, self.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            raise Refused('child identity lost') from None

    def _check(self):
        if self.cancelled:
            raise Refused('operation interrupted')
        if time.monotonic() >= self.deadline:
            raise Refused('operation timed out')

    def send(self, value):
        raw = (json.dumps(value, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')
        if len(raw) > MAX_JSON:
            raise Refused('protocol request too large')
        while raw:
            self._check()
            if self._observed() is not None:
                raise Refused('worker exited early')
            _, writable, _ = select.select([], [self.process.stdin], [], .05)
            if writable:
                try:
                    written = os.write(self.process.stdin.fileno(), raw)
                except BlockingIOError:
                    continue
                if not written:
                    raise Refused('worker pipe closed')
                raw = raw[written:]

    def receive(self):
        while b'\n' not in self.buffer:
            self._check()
            readable, _, _ = select.select([self.process.stdout], [], [], .05)
            if readable:
                chunk = os.read(self.process.stdout.fileno(), MAX_JSON + 1)
                if not chunk:
                    raise Refused('worker closed protocol')
                self.buffer += chunk
            if len(self.buffer) > MAX_JSON:
                raise Refused('worker protocol too large')
            if self._observed() is not None and b'\n' not in self.buffer:
                raise Refused('worker exited early')
        raw, self.buffer = self.buffer.split(b'\n', 1)
        # The request-response protocol never pipelines messages.
        if self.buffer:
            raise Refused('unsolicited worker output')
        return decode_json(raw)

    def exit_zero(self):
        while True:
            self._check()
            observed = self._observed()
            if observed is not None:
                if observed.si_code != os.CLD_EXITED or observed.si_status != 0:
                    raise Refused('child failed')
                readable, _, _ = select.select([self.process.stdout], [], [], 0)
                if readable and os.read(self.process.stdout.fileno(), 1):
                    raise Refused('unexpected child output')
                return
            time.sleep(.02)

    def close(self, grace=TERM_GRACE):
        if self.process is None:
            return
        safe = True
        cleaned = True
        try:
            for number in (signal.SIGTERM, signal.SIGKILL):
                try:
                    self._observed()  # WNOWAIT retains the PID/PGID reuse guard.
                except (Refused, OSError):
                    safe = False
                    cleaned = False
                    break
                try:
                    os.killpg(self.process.pid, number)
                except ProcessLookupError:
                    pass
                except OSError:
                    cleaned = False
                if number == signal.SIGTERM:
                    end = time.monotonic() + grace
                    while time.monotonic() < end:
                        time.sleep(min(.05, max(0, end - time.monotonic())))
            if safe:
                try:
                    self.process.wait(timeout=REAP_TIMEOUT)
                except (OSError, subprocess.SubprocessError):
                    cleaned = False
        finally:
            self.process.stdin.close()
            self.process.stdout.close()
            self.process = None
        if not cleaned:
            raise Refused('child cleanup unconfirmed')


def run_command(command, cancelled=None, timeout=COMMAND_TIMEOUT):
    if timeout <= 0:
        raise Refused('operation timed out')
    child = Child(command, timeout, cancelled=cancelled)
    result = bytearray()
    try:
        while True:
            child._check()
            readable, _, _ = select.select([child.process.stdout], [], [], .05)
            if readable:
                data = os.read(child.process.stdout.fileno(), MAX_OUTPUT + 1)
                result.extend(data)
                if len(result) > MAX_OUTPUT:
                    raise Refused('service output too large')
            observed = child._observed()
            if observed is not None:
                # Drain only immediately available bounded output, never wait for descendant EOF.
                while select.select([child.process.stdout], [], [], 0)[0]:
                    data = os.read(child.process.stdout.fileno(), MAX_OUTPUT + 1)
                    if not data:
                        break
                    result.extend(data)
                    if len(result) > MAX_OUTPUT:
                        raise Refused('service output too large')
                if observed.si_code != os.CLD_EXITED:
                    raise Refused('service command terminated')
                return observed.si_status, bytes(result)
    finally:
        child.close(grace=.05)


def cgroup_empty(unit):
    if unit not in SYSTEMD_UNITS[:2]:
        raise Refused('unknown service cgroup')
    target = CGROUP_ROOT / 'system.slice' / unit
    # Do not treat missing/unmounted cgroup root as proof of inactivity.
    with trusted_directory(CGROUP_ROOT) as root:
        fd = os.open('cgroup.controllers', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise Refused('cgroup v2 is required')
        finally:
            os.close(fd)
    with trusted_directory(CGROUP_ROOT) as hierarchy:
        try:
            parent = os.open('system.slice', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=hierarchy)
        except FileNotFoundError:
            return True
        try:
            _root_owned(os.fstat(parent), directory=True)
            try:
                root = os.open(target.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            except FileNotFoundError:
                return True
        finally:
            os.close(parent)
        pending = [root]
        count = 0
        try:
            while pending:
                current = pending.pop()
                try:
                    _root_owned(os.fstat(current), directory=True)
                    count += 1
                    if count > 4096:
                        raise Refused('cgroup tree too large')
                    processes = os.open('cgroup.procs', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
                    try:
                        if not stat.S_ISREG(os.fstat(processes).st_mode):
                            raise Refused('invalid cgroup process list')
                        if os.read(processes, MAX_JSON).strip():
                            return False
                    finally:
                        os.close(processes)
                    for name in os.listdir(current):
                        info = os.stat(name, dir_fd=current, follow_symlinks=False)
                        if stat.S_ISLNK(info.st_mode):
                            raise Refused('linked cgroup entry')
                        if stat.S_ISDIR(info.st_mode):
                            pending.append(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current))
                finally:
                    os.close(current)
            return True
        finally:
            for fd in pending:
                os.close(fd)


class Services:
    def __init__(self, manager, cancelled=None):
        if manager not in ('systemd', 'openrc'):
            raise Refused('unknown service manager')
        self.manager = manager
        self.deadline = None
        self.cancelled = cancelled if cancelled is not None else []
        self.units = SYSTEMD_UNITS if manager == 'systemd' else OPENRC_UNITS
        self.executable = SYSTEMCTL if manager == 'systemd' else RC_SERVICE
        trusted_executable(self.executable)

    def _run(self, arguments, ignore_cancel=False):
        timeout = COMMAND_TIMEOUT
        if self.deadline is not None and not ignore_cancel:
            timeout = min(timeout, self.deadline - time.monotonic())
        return run_command([str(self.executable), *arguments],
                           None if ignore_cancel else self.cancelled, timeout=timeout)

    def state(self, unit):
        if unit not in self.units:
            raise Refused('unknown service')
        if self.manager == 'openrc':
            code, output = self._run([unit, 'status'])
            # OpenRC return status alone cannot establish descendant quiescence.
            if code == 0:
                return 'active'
            if code == 3:
                return 'inactive'
            raise Refused('unknown OpenRC state')
        code, output = self._run(['show', '--no-pager', '--all', '--property=' + ','.join(PROPERTIES), unit])
        if code:
            raise Refused('systemd query failed')
        try:
            lines = output.decode('ascii').splitlines()
        except UnicodeError:
            raise Refused('invalid systemd status') from None
        fields = {}
        for line in lines:
            if '=' not in line:
                raise Refused('invalid systemd property')
            key, value = line.split('=', 1)
            if key not in PROPERTIES or key in fields:
                raise Refused('unexpected systemd property')
            fields[key] = value
        socket = unit.endswith('.socket')
        common = set(PROPERTIES) - {'User', 'Group', 'KillMode', 'MainPID', 'ControlGroup'}
        required = common if socket else set(PROPERTIES)
        if not required <= set(fields):
            raise Refused('incomplete systemd status')
        if (fields['LoadState'] != 'loaded' or fields['NeedDaemonReload'] != 'no'
                or fields['FragmentPath'] != '/etc/systemd/system/' + unit
                or fields['DropInPaths'] != ''):
            raise Refused('systemd configuration drift')
        if socket:
            if (fields.get('MainPID', '') not in ('', '0')
                    or any(fields.get(key, '') != '' for key in ('ControlGroup', 'User', 'Group', 'KillMode'))):
                raise Refused('unexpected socket process properties')
        else:
            if (fields['User'] != 'v-ui' or fields['Group'] != 'v-ui'
                    or fields['KillMode'] != 'control-group'
                    or not re.fullmatch(r'0|[1-9][0-9]{0,9}', fields['MainPID'])
                    or fields['ControlGroup'] not in ('', '/system.slice/' + unit)):
                raise Refused('unsafe service process properties')
        state = (fields['ActiveState'], fields['SubState'])
        if state == ('active', 'listening' if socket else 'running'):
            if not socket and (fields['MainPID'] == '0' or fields['ControlGroup'] != '/system.slice/' + unit):
                raise Refused('active service missing process identity')
            return 'active'
        if state == ('inactive', 'dead') and (socket or fields['MainPID'] == '0'):
            return 'inactive'
        raise Refused('service is not in a stable supported state')

    def snapshot(self):
        return {unit: self.state(unit) for unit in self.units}

    def mutate(self, action, unit, ignore_cancel=False):
        if action not in ('start', 'stop') or unit not in self.units:
            raise Refused('unsupported service command')
        arguments = [action, unit] if self.manager == 'systemd' else [unit, action]
        code, _ = self._run(arguments, ignore_cancel=ignore_cancel)
        if code:
            raise Refused('service action failed')

    def stop_all(self, emergency=False):
        order = (SYSTEMD_UNITS[2], SYSTEMD_UNITS[0], SYSTEMD_UNITS[1]) if self.manager == 'systemd' else OPENRC_UNITS
        failed = False
        for unit in order:
            try:
                self.mutate('stop', unit, ignore_cancel=emergency)
            except (Refused, OSError, subprocess.SubprocessError):
                failed = True
        if failed:
            raise Refused('service stop unconfirmed')
        self.prove_stopped()

    def prove_stopped(self):
        if any(value != 'inactive' for value in self.snapshot().values()):
            raise Refused('services remain active')
        if self.manager != 'systemd':
            raise Refused('OpenRC descendant quiescence cannot be proved')
        if not all(cgroup_empty(unit) for unit in SYSTEMD_UNITS[:2]):
            raise Refused('service descendants remain')

    def restore(self, desired):
        if set(desired) != set(self.units) or any(value not in ('active', 'inactive') for value in desired.values()):
            raise Refused('invalid service topology')
        order = (SYSTEMD_UNITS[2], SYSTEMD_UNITS[1], SYSTEMD_UNITS[0]) if self.manager == 'systemd' else OPENRC_UNITS[::-1]
        for unit in order:
            if desired[unit] == 'active':
                self.mutate('start', unit)
        actual = self.snapshot()
        # An active socket may legitimately activate its responder while checking.
        for unit, wanted in desired.items():
            if actual[unit] != wanted:
                if not (unit == 'v-ui-http01.service' and desired.get('v-ui-http01.socket') == 'active'
                        and wanted == 'inactive' and actual[unit] == 'active'):
                    raise Refused('service topology was not restored')


def validate_request(request):
    if not isinstance(request, dict) or request.get('protocol') != 1 or type(request.get('protocol')) is not int:
        raise Refused('invalid maintenance request')
    action = request.get('action')
    if action not in ACTIONS:
        raise Refused('unknown action')
    expected = {'protocol', 'action'}
    if action in ('restore', 'stage'):
        expected |= {'path', 'sha256'}
        path = request.get('path')
        if (not isinstance(path, str) or len(path) > 4096 or not path.startswith('/')
                or any(ord(char) < 32 or ord(char) == 127 for char in path)):
            raise Refused('invalid backup path')
        checksum = request.get('sha256')
        if not isinstance(checksum, str) or not HEX.fullmatch(checksum):
            raise Refused('trusted backup checksum required')
    if action == 'activate':
        expected.add('release_id')
        identity = request.get('release_id')
        if not isinstance(identity, str) or not RELEASE.fullmatch(identity):
            raise Refused('invalid release identity')
    if set(request) != expected:
        raise Refused('unexpected request field')
    return action


def validate_done(result, request):
    expected = {'event', 'ok'}
    action = request['action']
    if action in ('backup', 'activate'):
        expected |= {'backup_name', 'backup_sha256'}
        name, checksum = result.get('backup_name'), result.get('backup_sha256')
        if (not isinstance(name, str) or not BACKUP.fullmatch(name)
                or not isinstance(checksum, str) or not HEX.fullmatch(checksum)):
            raise Refused('invalid backup result')
    if action == 'stage':
        expected.add('prepared_release')
        identity = result.get('prepared_release')
        if not isinstance(identity, str) or not RELEASE.fullmatch(identity):
            raise Refused('invalid prepared release')
    if action == 'activate':
        expected.add('selected_release')
        if result.get('selected_release') != request['release_id']:
            raise Refused('unexpected selected release')
    if set(result) != expected or result.get('event') != 'done' or result.get('ok') is not True:
        raise Refused('maintenance result unconfirmed')
    return result


def operate(request, services, account, cancelled, worker_factory=None):
    action = validate_request(request)
    worker_factory = worker_factory or Child
    if action == 'stage':
        return prepare_candidate(request, account, cancelled, worker_factory)
    services.deadline = time.monotonic() + TIMEOUT
    initial = services.snapshot()
    if services.manager != 'systemd':
        # Status remains available. No write operation pretends OpenRC is stopped.
        raise Refused('OpenRC managed writes require a descendant-proof backend')
    if action == 'start' and initial['v-ui.service'] == 'active' and initial['v-ui-http01.socket'] == 'active':
        return {'event': 'already_running'}
    if action == 'start':
        services.prove_stopped()
    worker = None
    stopped = False
    try:
        worker = worker_factory([str(PYTHON), '-B', '-I', '-S', str(WORKER)],
                                max(0, services.deadline - time.monotonic()),
                                account=account, cancelled=cancelled)
        worker.send(request)
        if worker.receive() != {'event': 'prepared'}:
            raise Refused('worker preparation failed')
        if cancelled:
            raise Refused('operation interrupted')
        stopped = True
        if action != 'start':
            services.stop_all()
        else:
            services.prove_stopped()
        worker.send({'command': 'proceed'})
        result = validate_done(worker.receive(), request)
        worker.send({'command': 'release_writers'})
        if worker.receive() != {'event': 'writers_released'}:
            raise Refused('writer release unconfirmed')
        if cancelled:
            raise Refused('operation interrupted')
        services.prove_stopped()
        if action == 'start':
            desired = {unit: 'active' if unit in ('v-ui.service', 'v-ui-http01.socket') else 'inactive'
                       for unit in SYSTEMD_UNITS}
        elif action == 'stop':
            desired = {unit: 'inactive' for unit in SYSTEMD_UNITS}
        else:
            desired = initial
        services.restore(desired)
        worker.send({'command': 'finish'})
        worker.exit_zero()
        worker.close()
        worker = None
        return result
    except BaseException:
        if stopped:
            try:
                services.stop_all(emergency=True)
            except (Refused, OSError, subprocess.SubprocessError):
                pass
        raise
    finally:
        if worker is not None:
            worker.close()


def prepare_candidate(request, account, cancelled, worker_factory):
    worker = worker_factory([str(PYTHON), '-B', '-I', '-S', str(WORKER)], TIMEOUT,
                            account=account, cancelled=cancelled)
    try:
        worker.send(request)
        if worker.receive() != {'event': 'prepared'}:
            raise Refused('candidate preparation preflight failed')
        if cancelled:
            raise Refused('operation interrupted')
        worker.send({'command': 'proceed'})
        result = validate_done(worker.receive(), request)
        worker.send({'command': 'finish'})
        worker.exit_zero()
        return result
    finally:
        worker.close()


HELP = ('V-UI 受管维护菜单\n'
        '1 / status   查看受管服务状态\n'
        '2 / start    启动已停止的服务\n'
        '3 / stop     停止服务与 HTTP-01 socket\n'
        '4 / restart  按当前运行拓扑重启\n'
        '5 / backup   停机创建私有备份\n'
        '6 / restore  校验并停机恢复备份\n'
        '7 / activate 选择已准备版本（升级或代码回退，先备份）\n'
        '8 / stage    准备可信本地候选（不激活）\n'
        '9 / recover-restore 完成中断还原的恢复\n'
        'h            帮助\n'
        '0            退出\n'
        '操作失败后不会自动恢复启动。OpenRC 当前仅支持状态查询与候选准备。')
CHOICES = {'1': 'status', '2': 'start', '3': 'stop', '4': 'restart',
           '5': 'backup', '6': 'restore', '7': 'activate', '8': 'stage', '9': 'recover-restore'}


def read_input(prompt, limit=4096, hidden=False):
    saved = None
    fd = None
    previous = {}
    numbers = {signal.SIGINT, signal.SIGHUP, signal.SIGTERM}
    def interrupted(number, frame):
        raise InputInterrupted(number)
    try:
        for number in numbers:
            previous[number] = signal.signal(number, interrupted)
        if hidden:
            fd = sys.stdin.fileno()
            saved = termios.tcgetattr(fd)
            quiet = saved.copy()
            quiet[3] &= ~(termios.ECHO | termios.ECHONL)
            termios.tcsetattr(fd, termios.TCSANOW, quiet)
        print(prompt, end='', flush=True)
        value = sys.stdin.readline(limit + 2)
    finally:
        # A repeated interrupt must not interrupt restoration of hidden input.
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, numbers)
        try:
            if saved is not None:
                termios.tcsetattr(fd, termios.TCSANOW, saved)
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        if hidden:
            print()
    if not value:
        return None
    value = value[:-1] if value.endswith('\n') else value
    if len(value) > limit:
        raise Refused('terminal input too long')
    return value


def menu():
    if (os.getuid() != 0 or os.geteuid() != 0 or not sys.stdin.isatty()
            or not sys.stdout.isatty() or len(sys.argv) != 1
            or not sys.flags.dont_write_bytecode or not sys.flags.isolated or not sys.flags.no_site
            or Path(sys.executable).resolve(strict=True) != PYTHON.resolve(strict=True)):
        raise Refused('fixed isolated OS interpreter and interactive root terminal required')
    os.umask(0o077)
    print(HELP)
    while True:
        selection = read_input('请选择: ', 32)
        if selection is None or selection == '0':
            return 0
        if selection == 'h':
            print(HELP)
            continue
        action = CHOICES.get(selection, selection)
        if action not in ('status', *ACTIONS):
            print('未知选项，请输入 h 查看帮助。')
            continue
        request = {'protocol': 1, 'action': action}
        if action in ('restore', 'stage'):
            if action == 'stage':
                print('仅准备候选，不激活、不切换版本、不操作服务。\n'
                      '必须使用已从独立可信官方来源核验并接受的本地包及 SHA-256。\n'
                      'SHA-256 只校验字节，不认证来源、不是沙箱；候选代码将以服务用户执行。\n'
                      '同用户并发写入或不可信候选不能隔离；准备工作期最长 600 秒，超时结果未确认。')
            prompt = '备份绝对路径（留空取消）: ' if action == 'restore' else '本地升级包绝对路径（留空取消）: '
            request['path'] = read_input(prompt, hidden=True)
            if not request['path']:
                continue
            request['sha256'] = read_input('可信 SHA-256（留空取消）: ', 64, hidden=True)
            if not request['sha256']:
                continue
        elif action == 'activate':
            request['release_id'] = read_input('已准备版本 ID（留空取消）: ', 101)
            if not request['release_id']:
                continue
        if action == 'recover-restore':
            print('只恢复中断记录中指向的原数据目录，不从其他备份猜测恢复。\n'
                  '被替换的数据会保留；本次开始时服务已停止的，恢复后仍保持停止。')
        if action != 'status':
            confirmation = ('确认仅准备候选 [yes/N]: ' if action == 'stage' else
                            '操作可能中断面板和证书响应；失败后不自动恢复启动，停止未确认时需检查状态。确认 [yes/N]: ')
            answer = read_input(confirmation, 16)
            if answer is None:
                return 0
            if answer != 'yes':
                print('已取消，未执行操作。')
                continue
        with deferred_signals() as cancelled:
            try:
                with installation_lock():
                    manager, account = verify_installation()
                    services = None if action == 'stage' else Services(manager, cancelled)
                    if action == 'status':
                        states = services.snapshot()
                    else:
                        result = operate(request, services, account, cancelled)
                # Do not announce success before the lock's final inode check.
                if action == 'status':
                    for unit, state in states.items():
                        print(unit + ': ' + state)
                else:
                    if result.get('event') == 'already_running':
                        print('服务已运行；未执行启动，也未校验当前版本或恢复状态。')
                    elif action == 'stage':
                        print('候选准备完成，未激活: ' + result['prepared_release'])
                    else:
                        print('操作完成；服务状态已核验。')
                    if 'backup_name' in result:
                        print('私有备份: /var/lib/v-ui/backups/' + result['backup_name'])
                        print('SHA-256: ' + result['backup_sha256'])
                    if 'selected_release' in result:
                        print('已选择版本: ' + result['selected_release'])
            except (Refused, OSError, ValueError, KeyError, subprocess.SubprocessError):
                print('操作未完成或状态无法确认。请检查受信安装与服务；不会自动恢复启动。')
            if cancelled:
                return 128 + cancelled[0]


def main():
    try:
        return menu()
    except InputInterrupted as exc:
        return 128 + exc.number
    except KeyboardInterrupt:
        return 130
    except (Refused, OSError, ValueError, KeyError, subprocess.SubprocessError):
        print('V-UI 维护入口拒绝运行：请通过固定 /usr/local/sbin/v-ui 入口使用交互 root 终端。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
