#!/usr/bin/python3
"""Small root-owned, stdlib-only maintenance controller for managed installs.

Run only through /usr/local/sbin/v-ui using the OS Python with -B -I -S. Never
import application code, select CURRENT, or open service-user data as root.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import select
import signal
import socket
import ssl
import types
import stat
import subprocess
import sys
import termios
import tempfile
import uuid
import zipfile
import time

CONFIG = Path('/etc/v-ui/service.json')
CONTROL = Path('/usr/local/lib/v-ui')
RECEIPT = CONTROL / 'ops-control.json'
WRAPPER = Path('/usr/local/sbin/v-ui')
PYTHON = Path('/usr/bin/python3')
WORKER = CONTROL / 'ops_worker.py'
INSTALL_LOCK = Path('/run/lock/v-ui-install.lock')
CGROUP_ROOT = Path('/sys/fs/cgroup')
PROC_ROOT = Path('/proc')
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
PROPERTIES = ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'ControlPID', 'ControlGroup',
              'User', 'Group', 'KillMode', 'NeedDaemonReload', 'FragmentPath', 'DropInPaths')
ACTIONS = ('start', 'stop', 'restart', 'backup', 'restore', 'activate', 'recover-restore', 'stage')
HEX = re.compile(r'[a-f0-9]{64}\Z')
RELEASE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}\Z')
BACKUP = re.compile(r'maintenance-[a-f0-9]{32}\.zip\Z')


class Refused(RuntimeError):
    """A bounded, intentionally non-sensitive failure."""


class UnitTransition(Refused):
    def __init__(self, unit, state):
        super().__init__('service is not in a stable supported state')
        self.unit = unit
        self.state = state


class IdentityStarting(Refused):
    """Exact systemd executor exists but has not dropped its root UID yet."""


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
            CONTROL / 'release_tools.py', CONTROL / 'data_owner.py', CONTROL / 'proxy_store.py',
            CONTROL / 'upstream_profile.py', CONTROL / 'inbound_policy.py', CONTROL / 'firewall_support.py', WRAPPER,
            *(CONTROL / name for name in (
                'core_artifacts.py', 'core_selection.py', 'core_binary.py', 'core_store.py',
                'core_github.py', 'core_catalog.py', 'core_resolver.py', 'core_application.py',
                'core_selection_files.py', 'core_boot.py', 'core_backup.py', 'core_backup_archive.py',
                'core_maintenance.py', 'core_maintenance_recovery.py', 'core_boot_broker.py',
                'core_maintenance_control.py', 'core_authority.py')),
            *(CONTROL / name for name in ('certificate_errors.py', 'dns_store.py', 'cloudflare.py',
                                          'dns_journal.py', 'dns_backup.py')),
            *(CONTROL / name for name in ('maintenance_daemon.py', 'maintenance_upgrade.py',
              'maintenance_client.py', 'maintenance_cli_worker.py', 'install_control_verifier.py', 'v-ui-maintenance.service'))}
    if manager == 'systemd':
        return base | {Path('/etc/systemd/system') / name for name in (*SYSTEMD_UNITS, 'v-ui-maintenance.service')}
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
def installation_lock(*, _proof=False, _readonly=False):
    """Retain installer exclusion; optional proof is private and PID-scoped.

    The default branch preserves its original yield and cost. Proof users keep
    ancestor FDs until scope exit to avoid the legacy walk's early-close window.
    The proof never re-locks: it observes the original continuously held flock.
    """
    if type(_proof) is not bool or type(_readonly) is not bool or _proof and _readonly:
        raise Refused('invalid installation proof request')
    if not _proof:
        with trusted_directory(INSTALL_LOCK.parent, lock_parent=True) as directory:
            flags = os.O_RDONLY if _readonly else os.O_RDWR | os.O_CREAT
            try:
                fd = os.open(INSTALL_LOCK.name, flags | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=directory)
            except FileNotFoundError:
                if not _readonly:
                    raise
                yield
                try:
                    os.stat(INSTALL_LOCK.name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    return
                raise Refused('installation lock appeared during inspection')
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
        return

    @contextmanager
    def parent_scope():
        pieces = _parts(INSTALL_LOCK.parent)
        with ExitStack() as owned:
            parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            owned.callback(os.close, parent)
            _root_owned(os.fstat(parent), directory=True)
            bindings = []
            for index, part in enumerate(pieces):
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                owned.callback(os.close, child)
                info = os.fstat(child)
                _root_owned(info, directory=True, sticky=index == len(pieces)-1
                            and str(INSTALL_LOCK.parent) == '/run/lock')
                bindings.append((parent, part, child, directory_id(info)))
                parent = child
            yield parent
            for ancestor, name, child, expected in bindings:
                if (directory_id(os.fstat(child)) != expected
                        or directory_id(os.stat(name, dir_fd=ancestor, follow_symlinks=False)) != expected):
                    raise Refused('installation lock parent changed')

    def directory_id(info):
        return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid

    with parent_scope() as directory:
        parent_identity = directory_id(os.fstat(directory))
        fd = os.open(INSTALL_LOCK.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=directory)
        active = False
        pid = os.getpid()
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0
                    or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1):
                raise Refused('invalid installation lock')
            original = _identity(before)
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused('another installer or maintenance operation is running') from None
            def verify():
                opened = os.fstat(fd)
                named = os.stat(INSTALL_LOCK.name, dir_fd=directory, follow_symlinks=False)
                if (_identity(opened) != original or _identity(named) != original
                        or not stat.S_ISREG(opened.st_mode) or opened.st_uid != 0
                        or stat.S_IMODE(opened.st_mode) != 0o600 or opened.st_nlink != 1):
                    raise Refused('installation lock changed')
                with parent_scope() as current:
                    if (directory_id(os.fstat(directory)) != parent_identity
                            or directory_id(os.fstat(current)) != parent_identity):
                        raise Refused('installation lock parent changed')
            verify()
            active = True
            def proof():
                if not active or os.getpid() != pid:
                    raise Refused('installation proof is no longer active')
                verify()
                # The proof linearizes at this final lifecycle observation.
                # A concurrent scope exit revokes before closing the real FD;
                # completed filesystem checks cannot revive a revoked proof.
                # Callers keep this scope until all broker callbacks are joined.
                if not active or os.getpid() != pid:
                    raise Refused('installation proof is no longer active')
            try:
                yield proof
            finally:
                # Closed latch precedes verification/close, including lost ack.
                active = False
            verify()
        finally:
            active = False
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


def run_command(command, cancelled=None, timeout=COMMAND_TIMEOUT, *, cleanup_grace=.05):
    if timeout <= 0:
        raise Refused('operation timed out')
    child = Child(command, timeout, cancelled=cancelled)
    result = bytearray()
    exited = False
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
                exited = True
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
        child.close(grace=.05 if exited else cleanup_grace)


def cgroup_empty(unit):
    if unit not in SYSTEMD_UNITS:
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


def _service_process_identity(pid, service_uid):
    """Fixed /proc identity observation; no cmdline/environ or service action."""
    if (type(pid) is not int or not 0 < pid <= 2**31-1 or type(service_uid) is not int
            or not 0 < service_uid <= 2**32-1):
        raise Refused('invalid managed service identity')
    with ExitStack() as owned:
        root = os.open(PROC_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        owned.callback(os.close, root)
        _root_owned(os.fstat(root), directory=True)
        directory = os.open(str(pid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        owned.callback(os.close, directory)
        info = os.fstat(directory)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, service_uid) or info.st_mode & 0o022:
            raise Refused('unsafe managed process directory')
        directory_id = info.st_dev, info.st_ino

        def read(name, limit):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            owned.callback(os.close, fd)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise Refused('unsafe managed process record')
            result = bytearray()
            while len(result) <= limit:
                part = os.read(fd, min(4096, limit + 1 - len(result)))
                if not part:
                    break
                result.extend(part)
            if not result or len(result) > limit:
                raise Refused('invalid managed process record')
            return bytes(result)

        def snapshot():
            raw = read('stat', 8192)
            prefix = str(pid).encode('ascii') + b' ('
            if not raw.startswith(prefix) or b')' not in raw:
                raise Refused('invalid managed process stat')
            fields = raw.rsplit(b')', 1)[1].split()
            if (len(fields) < 20 or fields[0] not in (b'R', b'S', b'D', b'I')
                    or re.fullmatch(b'[1-9][0-9]{0,19}', fields[19]) is None):
                raise Refused('invalid managed process start identity')
            uid_lines = [line for line in read('status', 16384).splitlines() if line.startswith(b'Uid:')]
            if len(uid_lines) != 1:
                raise Refused('invalid managed process uid')
            values = uid_lines[0].split()[1:]
            if len(values) != 4 or any(re.fullmatch(b'0|[1-9][0-9]{0,9}', value) is None for value in values):
                raise Refused('managed process uid mismatch')
            if read('cgroup', 4096) != b'0::/system.slice/v-ui.service\n':
                raise Refused('managed process cgroup mismatch')
            uids = tuple(int(value) for value in values)
            if uids == (0, 0, 0, 0) and info.st_uid == 0:
                raise IdentityStarting('managed executor has not dropped privileges')
            if uids != (service_uid,) * 4:
                raise Refused('managed process uid mismatch')
            return dict(pid=pid, uid=service_uid, start_identity=fields[19].decode('ascii'),
                        cgroup='/system.slice/v-ui.service')

        before, after = snapshot(), snapshot()
        linked = os.stat(str(pid), dir_fd=root, follow_symlinks=False)
        if before != after or not stat.S_ISDIR(linked.st_mode) or (linked.st_dev, linked.st_ino) != directory_id:
            raise Refused('managed process identity changed')
        return after


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

    def _run(self, arguments, ignore_cancel=False, *, deadline=None):
        timeout = COMMAND_TIMEOUT
        if deadline is not None:
            timeout = min(timeout, deadline - time.monotonic())
        elif self.deadline is not None and not ignore_cancel:
            timeout = min(timeout, self.deadline - time.monotonic())
        return run_command([str(self.executable), *arguments],
                           None if ignore_cancel else self.cancelled, timeout=timeout)

    def state(self, unit, *, emergency_deadline=None):
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
        fields = (self._systemd_properties(unit) if emergency_deadline is None else
                  self._systemd_properties(unit, emergency_deadline=emergency_deadline))
        socket = unit.endswith('.socket')
        state = (fields['ActiveState'], fields['SubState'])
        active_states = {('active', 'listening'), ('active', 'running')} if socket else {('active', 'running')}
        if state in active_states:
            if not socket and (fields['MainPID'] == '0' or fields['ControlGroup'] != '/system.slice/' + unit):
                raise Refused('active service missing process identity')
            return 'active'
        if state == ('inactive', 'dead') and (socket or fields['MainPID'] == '0'):
            return 'inactive'
        if state[0] in ('activating', 'deactivating', 'failed'):
            raise UnitTransition(unit, state)
        raise Refused('service is not in a stable supported state')

    def _systemd_properties(self, unit, *, emergency_deadline=None):
        if self.manager != 'systemd' or unit not in self.units:
            raise Refused('unknown systemd service')
        command = ['show', '--no-pager', '--all', '--property=' + ','.join(PROPERTIES), unit]
        code, output = (self._run(command) if emergency_deadline is None else
                        self._run(command, ignore_cancel=True, deadline=emergency_deadline))
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
        # Socket units share systemd's exec/cgroup/kill properties, but have
        # ControlPID rather than a service MainPID. Missing fields are not zero.
        required = set(PROPERTIES) - {'MainPID'} if socket else set(PROPERTIES)
        if not required <= set(fields):
            raise Refused('incomplete systemd status')
        if (fields['LoadState'] != 'loaded' or fields['NeedDaemonReload'] != 'no'
                or fields['FragmentPath'] != '/etc/systemd/system/' + unit
                or fields['DropInPaths'] != '' or fields['ControlPID'] != '0'):
            raise Refused('systemd configuration drift')
        if socket:
            if (('MainPID' in fields and fields['MainPID'] != '0')
                    or fields['ControlGroup'] not in ('', '/system.slice/' + unit)
                    or fields['User'] != '' or fields['Group'] != ''
                    or fields['KillMode'] != 'control-group'):
                raise Refused('unexpected socket process properties')
        else:
            if (fields['User'] != 'v-ui' or fields['Group'] != 'v-ui'
                    or fields['KillMode'] != 'control-group'
                    or not re.fullmatch(r'0|[1-9][0-9]{0,9}', fields['MainPID'])
                    or fields['ControlGroup'] not in ('', '/system.slice/' + unit)):
                raise Refused('unsafe service process properties')
        return fields

    def managed_identity(self, service_uid):
        """Observe the exact supported panel process; this is not health/grant.

        The fixed deployment uses Type=simple: it becomes active before app
        startup finishes, allowing Root to arm a waiting broker. Transitional
        or unknown states remain unqualified rather than widening state().
        """
        if self.manager != 'systemd' or type(service_uid) is not int or not 0 < service_uid <= 2**32-1:
            raise Refused('managed process identity unavailable')
        before = self._systemd_properties('v-ui.service')
        if ((before['ActiveState'], before['SubState']) != ('active', 'running')
                or before['MainPID'] == '0' or before['ControlGroup'] != '/system.slice/v-ui.service'):
            raise Refused('managed panel process not ready for identity')
        pid = int(before['MainPID'])
        identity = _service_process_identity(pid, service_uid)
        after = self._systemd_properties('v-ui.service')
        if before != after or _service_process_identity(pid, service_uid) != identity:
            raise Refused('managed panel process identity changed')
        return identity

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
        if not emergency or self.manager != 'systemd':
            self.prove_stopped()
            return
        deadline = time.monotonic() + 5
        reset = set()
        while True:
            try:
                self.prove_stopped(emergency_deadline=deadline)
                return
            except UnitTransition as error:
                if time.monotonic() >= deadline:
                    raise
                if error.state == ('failed', 'failed') and error.unit not in reset:
                    fields = self._systemd_properties(error.unit, emergency_deadline=deadline)
                    if ((fields['ActiveState'], fields['SubState']) == error.state
                            and fields.get('MainPID', '0') == '0' and cgroup_empty(error.unit)):
                        code, _ = self._run(['reset-failed', error.unit], ignore_cancel=True, deadline=deadline)
                        if code:
                            raise Refused('service stop unconfirmed') from None
                        reset.add(error.unit)
                time.sleep(.05)

    def prove_stopped(self, *, emergency_deadline=None):
        states = (self.snapshot() if emergency_deadline is None else
                  {unit: self.state(unit, emergency_deadline=emergency_deadline) for unit in self.units})
        if any(value != 'inactive' for value in states.values()):
            raise Refused('services remain active')
        if self.manager != 'systemd':
            raise Refused('OpenRC descendant quiescence cannot be proved')
        if not all(cgroup_empty(unit) for unit in SYSTEMD_UNITS):
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


def require_core_compatibility(action):
    if action == 'stop':
        return
    names = ('CORE_UPDATE_PENDING.json',)
    if action in ('upgrade', 'backup', 'activate', 'restore', 'recover-restore'):
        names += ('core-selection.json', 'core-selection.fence.json', 'core-selection.lock',
                  '.core-update-active', '.core-update-history')
    try:
        os.stat(CONFIG.parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError:
        raise Refused('核心维护状态不可读取，当前操作已停止') from None
    try:
        with trusted_directory(CONFIG.parent) as directory:
            for name in names:
                try:
                    os.stat(name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise Refused('检测到独立核心选择或未完成事务，当前维护操作已停止')
    except OSError:
        raise Refused('核心维护状态不可读取，当前操作已停止') from None


def operate(request, services, account, cancelled, worker_factory=None):
    action = validate_request(request)
    require_core_compatibility(action)
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
        if action in ('restore','recover-restore'):revoke_core_epoch()
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


class _StageArchive:
    """One explicit Root stage input. The handoff directory remains owned by
    Root; service group can traverse/read, never replace directory entries.
    Private test layout injection is not a protocol/path option.
    """
    def __init__(self, request, account, cancelled, *, _parent=Path('/var/tmp'), _anchor=Path('/'), _owner=0):
        self.request,self.account,self.cancelled=request,account,cancelled
        self.parent,self.anchor,self.owner=_parent,_anchor,_owner
        self.retain=False;self.path=None;self.snapshot=None
        self.deadline=time.monotonic()+TIMEOUT
    @staticmethod
    def discard(fd, offset=0, size=0):
        if hasattr(os,'posix_fadvise') and hasattr(os,'POSIX_FADV_DONTNEED'):
            try:os.posix_fadvise(fd,offset,size,os.POSIX_FADV_DONTNEED)
            except OSError:pass
    def check(self):
        if self.cancelled or time.monotonic()>=self.deadline:raise Refused('stage interrupted or timed out')
    @contextmanager
    def material(self):
        if os.geteuid()!=self.owner or not self.parent.is_absolute() or '..' in self.parent.parts:
            raise Refused('invalid stage owner or layout')
        try:parts=self.parent.relative_to(self.anchor).parts
        except ValueError:raise Refused('invalid stage anchor') from None
        with ExitStack() as stack:
            bindings=[]
            parent=os.open(self.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);stack.callback(os.close,parent)
            initial=os.fstat(parent)
            if initial.st_uid!=(0 if self.anchor==Path('/') else self.owner) or initial.st_mode&0o022:
                raise Refused('untrusted stage anchor')
            def directory_id(info):return info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid
            for index,name in enumerate(parts):
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent);stack.callback(os.close,child)
                info=os.fstat(child)
                if (info.st_uid!=self.owner or info.st_mode&0o022 and not
                        (index==len(parts)-1 and stat.S_IMODE(info.st_mode)==0o1777)):
                    raise Refused('untrusted stage parent')
                bindings.append((parent,name,child,directory_id(info)));parent=child
            if not os.fstat(parent).st_mode&0o001:raise Refused('stage parent not traversable')
            def proof():
                if directory_id(os.stat(self.anchor,follow_symlinks=False))!=directory_id(initial):
                    raise Refused('stage anchor changed')
                for outer,name,inner,expected in bindings:
                    if (directory_id(os.fstat(inner))!=expected or
                            directory_id(os.stat(name,dir_fd=outer,follow_symlinks=False))!=expected):
                        raise Refused('stage parent changed')
            name='vui-stage-'+uuid.uuid4().hex
            os.mkdir(name,0o700,dir_fd=parent)
            folder=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent);stack.callback(os.close,folder)
            os.fchown(folder,self.owner,self.account.pw_gid);os.fchmod(folder,0o750)
            folder_id=directory_id(os.fstat(folder));file_fd=None;file_id=None
            self.path=self.parent/name/'bundle.zip'
            try:
                with tempfile.TemporaryFile(prefix='vui-stage-original-',dir=self.parent) as snapshot:
                    self.snapshot=snapshot;checksum=hashlib.sha256();size=0
                    original=os.open(self.request['path'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                    try:
                        info=os.fstat(original)
                        if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=850000000:
                            raise Refused('invalid stage archive')
                        while True:
                            self.check();chunk=os.read(original,min(1024*1024,850000001-size))
                            if not chunk:break
                            size+=len(chunk)
                            if size>850000000:raise Refused('stage archive too large')
                            checksum.update(chunk);snapshot.write(chunk)
                    finally:os.close(original)
                    snapshot.flush();os.fsync(snapshot.fileno());self.discard(snapshot.fileno())
                    if checksum.hexdigest()!=self.request['sha256']:raise Refused('stage archive digest mismatch')
                    snapshot.seek(0)
                    file_fd=os.open('bundle.zip',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=folder)
                    copied=hashlib.sha256();count=0
                    while True:
                        self.check();chunk=snapshot.read(1024*1024)
                        if not chunk:break
                        copied.update(chunk);count+=len(chunk);remaining=memoryview(chunk)
                        while remaining:
                            written=os.write(file_fd,remaining)
                            if not written:raise Refused('stage handoff write failed')
                            remaining=remaining[written:]
                        self.discard(snapshot.fileno(),count-len(chunk),len(chunk))
                    os.fsync(file_fd);self.discard(file_fd);os.fchown(file_fd,self.account.pw_uid,self.account.pw_gid);os.fchmod(file_fd,0o600)
                    file_id=os.fstat(file_fd)
                    if count!=size or copied.hexdigest()!=checksum.hexdigest():raise Refused('stage handoff digest mismatch')
                    proof();snapshot.seek(0)
                    yield self
                    proof()
            finally:
                # Never recursively remove a service-readable namespace. Keep
                # every unknown inode or unconfirmed worker lifetime for review.
                try:
                    proof()
                    named=os.stat(name,dir_fd=parent,follow_symlinks=False)
                    if directory_id(named)!=folder_id:raise Refused('stage handoff directory changed')
                    if not self.retain and file_fd is not None and file_id is not None:
                        current=os.fstat(file_fd);leaf=os.stat('bundle.zip',dir_fd=folder,follow_symlinks=False)
                        if ((leaf.st_dev,leaf.st_ino)!=(file_id.st_dev,file_id.st_ino)
                                or (current.st_dev,current.st_ino)!=(file_id.st_dev,file_id.st_ino)
                                or current.st_nlink!=1 or not stat.S_ISREG(current.st_mode)
                                or current.st_uid!=self.account.pw_uid or stat.S_IMODE(current.st_mode)!=0o600):
                            raise Refused('stage handoff file changed')
                        os.unlink('bundle.zip',dir_fd=folder)
                    if not self.retain:
                        os.rmdir(name,dir_fd=parent)
                finally:
                    if file_fd is not None:os.close(file_fd)  # Exactly once, even on close failure.


def _stage_origin(material, target, helper):
    try:
        with zipfile.ZipFile(material.snapshot) as archive:
            entries=archive.infolist()
            if len(entries)>10000 or len({row.filename for row in entries})!=len(entries):raise Refused('stage archive members refused')
            item=archive.getinfo('MANIFEST.json')
            if (item.is_dir() or item.flag_bits&1 or not 0<item.file_size<=4000000
                    or stat.S_IFMT(item.external_attr>>16) not in (0,stat.S_IFREG)):raise Refused('stage manifest refused')
            raw=archive.read(item)
    except (zipfile.BadZipFile,KeyError,RuntimeError,NotImplementedError):
        raise Refused('stage archive metadata refused') from None
    try:
        meta=json.loads(raw,object_pairs_hook=helper._pairs,parse_int=helper._json_integer,
                        parse_float=helper._number,parse_constant=helper._number)
    except (ValueError,TypeError,UnicodeError,RecursionError):
        raise Refused('stage manifest JSON refused') from None
    if (type(meta) is not dict or type(meta.get('schema')) is not int or meta['schema']!=1
            or meta.get('kind')!='release' or meta.get('platform')!='linux-multi-cpython312'
            or type(meta.get('targets')) is not list or target not in meta['targets']):
        raise Refused('stage manifest identity refused')
    record=helper._application(dict(release_id=meta['release_id'],target=target,app_source=meta['source_commit'],
        manifest_sha256=hashlib.sha256(raw).hexdigest(),archive_sha256=material.request['sha256']))
    helper.CoreAuthority().record_application(record)
    return record


@contextmanager
def accepted_stage(request, account, cancelled):
    expected=hashlib.sha256(trusted_bytes(RECEIPT,MAX_JSON)).hexdigest()
    helper=_load_core_authority(expected,_origin_only=True)
    config=decode_json(trusted_bytes(CONFIG,MAX_JSON))
    with _StageArchive(request,account,cancelled).material() as material:
        material.origin=_stage_origin(material,config['target'],helper)
        yield material


def prepare_candidate(request, account, cancelled, worker_factory):
    with accepted_stage(request,account,cancelled) as material:
        record=material.origin
        handed={**request,'path':str(material.path)}
        material.retain=True  # Constructor failure may leave an unconfirmed child.
        worker=worker_factory([str(PYTHON),'-B','-I','-S',str(WORKER)],
                              max(0,material.deadline-time.monotonic()),account=account,cancelled=cancelled)
        material.retain=False
        try:
            worker.send(handed)
            if worker.receive()!={'event':'prepared'}:raise Refused('candidate preparation preflight failed')
            material.check()
            worker.send({'command':'proceed'})
            result=validate_done(worker.receive(),handed)
            if result['prepared_release']!=record['release_id']:raise Refused('candidate preparation origin mismatch')
            worker.send({'command':'finish'});worker.exit_zero()
            return result
        finally:
            try:worker.close()
            except BaseException:
                material.retain=True
                raise


FIREWALL_PROGRAMS = ('ufw', 'firewall-cmd', 'nft', 'iptables-save')


def firewall_program(name):
    """Resolve a fixed OS command through root-owned paths, never caller PATH."""
    if name not in FIREWALL_PROGRAMS:
        raise Refused('unsupported firewall command')
    for directory in ('/usr/sbin', '/usr/bin', '/sbin', '/bin'):
        candidate = Path(directory) / name
        if candidate.exists():
            return str(trusted_executable(candidate))
    return None


def network_context(cancelled=None):
    """Load only root-owned kit bytes bound to the verified control receipt."""
    receipt = decode_json(trusted_bytes(RECEIPT, MAX_JSON))
    contents = {}
    for path in (CONFIG, CONTROL / 'firewall_support.py'):
        raw = trusted_bytes(path, MAX_JSON if path == CONFIG else MAX_FILE)
        if hashlib.sha256(raw).hexdigest() != receipt.get('files', {}).get(str(path)):
            raise Refused('network control receipt mismatch')
        contents[path] = raw
    module = types.ModuleType('_verified_firewall_support')
    exec(compile(contents[CONTROL / 'firewall_support.py'], '<verified firewall_support.py>', 'exec'), module.__dict__)
    def run(arguments, **kwargs):
        name = Path(arguments[0]).name
        executable = firewall_program(name)
        if executable is None:
            raise Refused('firewall executable unavailable')
        code, output = run_command([executable, *arguments[1:]], cancelled, timeout=10)
        return subprocess.CompletedProcess(arguments, code, output.decode('utf-8', errors='replace'), '')
    module.run = run
    module.shutil = types.SimpleNamespace(which=firewall_program)
    module.subprocess = types.SimpleNamespace(run=run)
    return module, decode_json(contents[CONFIG])


def local_tcp_listener(port):
    """Read Linux listener tables; no connection implies public reachability."""
    readable = False
    for name in ('tcp', 'tcp6'):
        try:
            with Path('/proc/net', name).open() as source:
                for index, line in enumerate(source):
                    if index > 65536:
                        return None
                    fields = line.split()
                    if len(fields) > 3 and fields[3] == '0A':
                        if int(fields[1].rsplit(':', 1)[1], 16) == port:
                            return True
            readable = True
        except (OSError, ValueError, IndexError):
            return None
    return False if readable else None


def local_panel_tls(config, *, require_login=False):
    """Check local panel TLS with system CA and hostname validation enabled."""
    context = ssl.create_default_context()
    port = config.get('port')
    domain = config.get('domain')
    bind = config.get('bind', '0.0.0.0')
    if type(port) is not int or not 1 <= port <= 65535 or not isinstance(domain, str):
        raise Refused('invalid panel endpoint')
    host = '127.0.0.1' if bind == '0.0.0.0' else '::1' if bind == '::' else bind
    if require_login:
        from ipaddress import ip_address
        try: ip_address(host)
        except ValueError: raise Refused('invalid local panel bind') from None
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', domain):
            raise Refused('invalid panel host')
    try:
        with socket.create_connection((host, port), timeout=3) as connection:
            with context.wrap_socket(connection, server_hostname=domain) as tls:
                if require_login:
                    tls.sendall(('GET /login HTTP/1.1\r\nHost: '+domain+':'+str(port)
                                 +'\r\nConnection: close\r\n\r\n').encode('ascii'))
                    deadline = time.monotonic() + 3
                    first = b''
                    while b'\r\n' not in first and len(first) < 256:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0: raise TimeoutError('panel response timeout')
                        tls.settimeout(remaining)
                        chunk = tls.recv(256-len(first))
                        if not chunk: break
                        first += chunk
                    line, separator, _ = first.partition(b'\r\n')
                    if not separator or not re.fullmatch(rb'HTTP/1\.[01] [0-9]{3}(?: [^\r\n]*)?', line):
                        return 'unknown（面板 HTTP 响应无效）'
                    status = line.split()[1]
                    if status == b'503': return 'pending（面板仍在维护）'
                    if status != b'200': return 'unknown（面板登录页未就绪）'
                return 'ready（本机握手、系统 CA 与域名校验通过）'
    except ssl.SSLCertVerificationError:
        return 'unknown（本机系统 CA 或域名校验未通过）'
    except (OSError, ssl.SSLError):
        return 'unknown（本机 TLS 握手未完成）'


def network_diagnostics(cancelled=None):
    firewall, config = network_context(cancelled)
    port = config.get('port')
    if type(port) is not int or not 1 <= port <= 65535:
        raise Refused('invalid panel port')
    info = firewall.detect()
    lines = ['网络/防火墙诊断（只读）', '本机规则后端：' + info['backend']]
    for number in sorted({80, port}):
        status = firewall.port_open(info, number)
        label = 'allowed' if status is True else 'blocked' if status is False else 'unknown'
        listener = local_tcp_listener(number)
        listening = 'listening' if listener is True else 'not-listening' if listener is False else 'unknown'
        lines.append(str(number) + '/tcp：本机规则 ' + label + '；本机监听 ' + listening)
    lines.extend(['面板 TLS：' + local_panel_tls(config),
                  '云规则与公网入站：unknown；本机规则、监听或本机 TLS 成功不能证明外部可达。',
                  '节点端口按实际创建的节点核对；默认声明端口不代表节点已创建。'])
    return lines


def network_open(protocol, port, cancelled=None):
    """The terminal's exact confirmation is required before calling this method."""
    if protocol not in ('tcp', 'udp') or type(port) is not int or not 1 <= port <= 65535 or port == 22:
        raise Refused('invalid manual firewall port')
    firewall, _ = network_context(cancelled)
    info = firewall.detect()
    if info.get('backend') not in ('ufw', 'firewalld') or not info.get('managed'):
        return '当前规则不支持安全修改，未改动；仅支持已启用的 UFW 或明确入站区域的 firewalld。'
    before = firewall.port_open(info, port, protocol=protocol)
    if before is None:
        return '所选端口的本机规则为 unknown，未改动。'
    if before is True:
        return str(port) + '/' + protocol + ' 本机规则已为 allowed，未新增规则。'
    try:
        firewall.open_ports(info, [port], protocol=protocol)
    except RuntimeError:
        raise Refused('precise firewall change unconfirmed') from None
    if firewall.port_open(info, port, protocol=protocol) is not True:
        raise Refused('precise firewall readback unconfirmed')
    return str(port) + '/' + protocol + ' 本机规则已放行并回读；公网可达性未验证。'


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
        '10 / network 网络/防火墙诊断（只读）\n'
        '11 / open-port 手动精确放行端口（明确确认）\n'
        '12 / upgrade 在线升级官方稳定版（选择后才下载，保留配置与数据）\n'
        '13 / grants  创建、列举或撤销临时维护授权\n'
        '14 / restore-components  恢复节点数据及核心版本\n'
        '15 / recover-components 恢复中断的双域事务\n'
        '16 / update-core 更新指定核心\n'
        '17 / rollback-core 回退指定核心\n'
        '18 / recover-core 恢复中断的核心任务（含双域备份）\n'
        '19 / backup-components 备份节点数据及核心版本\n'
        '20 / core-status 查看已选择的核心版本（只读）\n'
        'h            帮助\n'
        '0            退出\n'
        '操作失败后不会自动恢复启动。OpenRC 当前仅支持状态查询与候选准备。')
CHOICES = {'1': 'status', '2': 'start', '3': 'stop', '4': 'restart',
           '5': 'backup', '6': 'restore', '7': 'activate', '8': 'stage', '9': 'recover-restore', '10': 'network', '11': 'open-port', '12': 'upgrade', '13': 'grants'}
CORE_TASKS = ('restore-components', 'recover-components', 'update-core', 'rollback-core', 'recover-core',
              'backup-components', 'core-status')
CHOICES.update(zip(('14', '15', '16', '17', '18', '19', '20'), CORE_TASKS))
CORE_RESTORE_ARCHIVE = re.compile(r'core-maintenance-[a-f0-9]{32}\.zip\Z')
CORE_RESTORE_BOOTSTRAP = """import hashlib, os, sys, types
if len(sys.argv) not in (4, 5, 6) or os.getuid() != 0 or os.geteuid() != 0:
    raise SystemExit(1)
path = '/usr/local/lib/v-ui/system_ops.py'
with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as source:
    raw = source.read(4000001)
if len(raw) > 4000000 or hashlib.sha256(raw).hexdigest() != sys.argv[1]:
    raise SystemExit(1)
ops = types.ModuleType('_vui_root_restore_ops')
ops.__file__ = path
sys.modules[ops.__name__] = ops
exec(compile(raw, path, 'exec'), ops.__dict__)
with ops.deferred_signals() as cancelled:
    ops._core_terminal_task(ops, sys.argv[3:], cancelled, sys.argv[2])
"""


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


def fixed_module(name):
    if name != 'maintenance_client':
        raise Refused('fixed module required')
    verify_installation()
    module = types.ModuleType('_vui_fixed_' + name)
    exec(compile(trusted_bytes(CONTROL / (name + '.py')), '<fixed client>', 'exec'), module.__dict__)
    return module


def _core_grant_link(record, *, ordinary_scopes, root_scopes, seconds):
    """Combine one bounded non-Root creation reply with an independent Root
    capability. Never return a Root secret to the ordinary creation worker.
    """
    expected={'id','scopes','created_at','expires_at','redeemed','revoked','expired','link'}
    now=int(time.time())
    if (type(record) is not dict or set(record)!=expected or type(record['id']) is not int
            or not 0<record['id']<2**63 or type(record['scopes']) is not list
            or any(type(value) is not str for value in record['scopes'])
            or set(record['scopes'])!=set(ordinary_scopes) or len(record['scopes'])!=len(ordinary_scopes)
            or type(record['created_at']) is not int or type(record['expires_at']) is not int
            or not now-60<=record['created_at']<=now+2
            or record['expires_at']!=record['created_at']+seconds or record['expires_at']<=now
            or any(record[key] is not False for key in ('redeemed','revoked','expired'))):
        raise Refused('ordinary grant reply refused')
    config=decode_json(trusted_bytes(CONFIG,MAX_JSON))
    origin=config.get('origin')
    prefix=str(origin)+'/maintenance#grant='
    link=record['link']
    if (type(origin) is not str or not origin.startswith('https://') or type(link) is not str
            or not link.startswith(prefix) or not re.fullmatch(r'vui_mg_[A-Za-z0-9_-]{43}',link[len(prefix):])):
        raise Refused('ordinary grant origin refused')
    token=link[len(prefix):]
    expected_receipt=hashlib.sha256(trusted_bytes(RECEIPT,MAX_JSON)).hexdigest()
    module=_load_core_authority(expected_receipt)
    issued,root_token=module.CoreAuthority().issue(main_id=record['id'],main_digest=hashlib.sha256(token.encode()).hexdigest(),
        scopes=root_scopes,seconds=seconds,not_after=record['expires_at'])
    if issued['expires_at']>record['expires_at']:raise Refused('Root grant deadline refused')
    return {**record,'core_scopes':issued['scopes'],'core_expires_at':issued['expires_at'],
            'link':prefix+token+'&core='+root_token}


def _create_core_grant(account, arguments, ordinary_scopes, root_scopes, seconds):
    child=Child([str(PYTHON),'-B','-I','-S',str(CONTROL/'maintenance_cli_worker.py'),*arguments],
                60,account=account,cancelled=[])
    try:
        record=child.receive();child.exit_zero()
    finally:
        child.close()
    # The normal grant may remain for explicit revocation if Root issuance
    # fails. Neither partial reply nor a fabricated success link is displayed.
    return _core_grant_link(record,ordinary_scopes=ordinary_scopes,root_scopes=root_scopes,seconds=seconds)


def terminal_grants():
    print('临时授权: 1 创建  2 列举  3 撤销  4 核心维护授权  0 返回。链接仅本次显示，请妥善保存。')
    choice = read_input('请选择授权操作: ', 2)
    if choice in (None, '', '0'):
        return
    manager, account = verify_installation()
    if choice == '1':
        print('1 只读（默认）：诊断、状态与脱敏日志\n'
              '2 日常维护：另含节点、分享、中转、证书、服务操作与升级\n'
              '3 完整维护：另含删除与备份恢复，可能永久改变业务数据\n4 代理核心只读：查看选择和候选信息')
        preset = read_input('请选择权限 [1/2/3/4，默认 1]: ', 1) or '1'
        choices = {'1': 'diagnose',
                   '2': 'diagnose,nodes,share,relay,certificates,operate,upgrade',
                   '3': 'diagnose,nodes,share,relay,certificates,operate,upgrade,destructive',
                   '4': 'diagnose,core_read'}
        minutes = read_input('有效分钟数 1–259200，或输入180天/180d（默认30分钟）: ', 6) or '30'
        if minutes in ('180天', '180d', '180D'):
            minutes = '259200'
        if (preset not in choices or not minutes.isascii() or not minutes.isdecimal()
                or not 1 <= int(minutes) <= 259200):
            raise Refused('invalid grant input')
        arguments = ['create', choices[preset], str(int(minutes) * 60)]
    elif choice == '4':
        if manager!='systemd':raise Refused('当前系统暂不支持独立核心维护授权')
        print('1 核心维护：更新、回退、未完成操作恢复及双域备份\n'
              '2 完整维护：所有面板功能及核心恢复；恢复仍须确认具体备份')
        preset=read_input('请选择核心维护权限 [1/2，默认1]: ',1) or '1'
        minutes=read_input('有效分钟数 1–259200，或输入180天/180d（默认30分钟）: ',6) or '30'
        if minutes in ('180天','180d','180D'):minutes='259200'
        if preset not in ('1','2') or not minutes.isascii() or not minutes.isdecimal() or not 1<=int(minutes)<=259200:
            raise Refused('invalid core grant input')
        ordinary=['diagnose','core_read'] if preset=='1' else ['diagnose','nodes','share','relay','certificates','operate','upgrade','destructive','core_read']
        root_scopes=['core_operate','core_backup']+(['core_restore'] if preset=='2' else [])
        result=_create_core_grant(account,['create',','.join(ordinary),str(int(minutes)*60)],ordinary,root_scopes,int(minutes)*60)
        print(json.dumps(result,ensure_ascii=False))
        return
    elif choice == '2':
        arguments = ['list']
    elif choice == '3':
        identity = read_input('撤销授权 ID: ', 19)
        if not identity or not re.fullmatch(r'[1-9][0-9]{0,18}', identity):
            raise Refused('invalid grant identity')
        arguments = ['revoke', identity]
    else:
        raise Refused('invalid grant operation')
    # Kernel credential demotion precedes the OS interpreter and all payload
    # access. No service-owned code or runtime can ever execute as root here.
    subprocess.run([str(PYTHON), '-B', '-I', '-S', str(CONTROL / 'maintenance_cli_worker.py'), *arguments],
                   env=ENV, cwd='/', user=account.pw_uid, group=account.pw_gid, extra_groups=[],
                   stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=True)


def _load_core_controller(expected_receipt):
    manager, _ = verify_installation()
    if manager != 'systemd':
        raise Refused('core restore requires systemd')
    path = CONTROL/'core_maintenance_control.py'
    raw = trusted_bytes(path)
    receipt_raw = trusted_bytes(RECEIPT, MAX_JSON)
    if hashlib.sha256(receipt_raw).hexdigest() != expected_receipt:
        raise Refused('core restore receipt changed')
    receipt = decode_json(receipt_raw)
    if hashlib.sha256(raw).hexdigest() != receipt['files'].get(str(path)):
        raise Refused('core controller changed')
    module = types.ModuleType('_vui_terminal_core_restore')
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def _load_core_authority(expected_receipt, *, _origin_only=False):
    """Load the single fixed Root helper only after the complete receipt check.

    No caller-selected module, source path or service-owned code is compiled.
    Constructing the class does not create authority state or mint a grant.
    """
    manager, _ = verify_installation()
    if type(_origin_only) is not bool or manager not in ('systemd','openrc') or manager!='systemd' and not _origin_only:
        raise Refused('core authority requires systemd')
    path = CONTROL / 'core_authority.py'
    raw = trusted_bytes(path)
    receipt_raw = trusted_bytes(RECEIPT, MAX_JSON)
    if hashlib.sha256(receipt_raw).hexdigest() != expected_receipt:
        raise Refused('core authority receipt changed')
    receipt = decode_json(receipt_raw)
    if hashlib.sha256(raw).hexdigest() != receipt['files'].get(str(path)):
        raise Refused('core authority changed')
    module = types.ModuleType('_vui_fixed_core_authority')
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def revoke_core_epoch():
    expected=hashlib.sha256(trusted_bytes(RECEIPT,MAX_JSON)).hexdigest()
    return _load_core_authority(expected,_origin_only=True).CoreAuthority().advance_epoch()


def application_origin(release_id, target):
    """Lookup Root-accepted bytes, never infer an origin from service data."""
    if (type(release_id) is not str or not RELEASE.fullmatch(release_id)
            or type(target) is not str or target not in ('x86_64-gnu','x86_64-musl','aarch64-gnu','aarch64-musl')):
        raise Refused('invalid application origin lookup')
    expected=hashlib.sha256(trusted_bytes(RECEIPT,MAX_JSON)).hexdigest()
    module=_load_core_authority(expected)
    return module.CoreAuthority().application(release_id=release_id,target=target)


def core_selection_status(ops):
    """Root-only read; the controller rechecks receipt under a readonly lock."""
    expected = hashlib.sha256(trusted_bytes(RECEIPT, MAX_JSON)).hexdigest()
    module = _load_core_controller(expected)
    return module.inspect_core_selection(ops, _receipt_sha256=expected)


def _core_terminal_task(ops, arguments, cancelled, expected_receipt):
    module = _load_core_controller(expected_receipt)
    if arguments == ['recover-components']:
        result = module.recover_restore_backup(ops, cancelled=cancelled, _receipt_sha256=expected_receipt)
    elif arguments == ['recover-core']:
        result = module.recover(ops, cancelled=cancelled, _receipt_sha256=expected_receipt)
    elif arguments == ['core-status']:
        result = module.inspect_core_selection(ops, _receipt_sha256=expected_receipt)
    elif arguments == ['backup-components']:
        selection = decode_json(trusted_bytes(CONFIG.parent/'core-selection.json', MAX_JSON))
        result = module.backup_components(ops, expected_revision=selection['revision'],
                                          cancelled=cancelled, _receipt_sha256=expected_receipt)
    elif len(arguments) == 3 and arguments[0] == 'restore-components':
        selection = decode_json(trusted_bytes(CONFIG.parent/'core-selection.json', MAX_JSON))
        try:
            result = module.restore_backup(ops, archive_name=arguments[1], archive_sha256=arguments[2],
                                           expected_revision=selection['revision'], cancelled=cancelled,
                                           _receipt_sha256=expected_receipt)
        except module.CoreControlError as error:
            stages = {'RESTORE_STAGE_' + stage for stage in module.RESTORE_STAGES}
            if str(error) in stages:
                print(str(error), flush=True)
            raise
    elif ((len(arguments) == 2 and arguments[0] == 'rollback-core')
          or (len(arguments) == 3 and arguments[0] == 'update-core')):
        try:
            selection = decode_json(trusted_bytes(CONFIG.parent/'core-selection.json', MAX_JSON))
            revision = selection['revision']
        except FileNotFoundError:
            revision = None
        update = arguments[0] == 'update-core'
        result = module.switch(ops, core=arguments[1], action='update' if update else 'rollback',
                               expected_revision=revision, release_id=int(arguments[2]) if update else None,
                               cancelled=cancelled, _receipt_sha256=expected_receipt)
    else:
        raise Refused('invalid core restore task')
    print(json.dumps(result, separators=(',', ':')), flush=True)


def terminal_core_action(action):
    arguments = [action]
    if action == 'restore-components':
        name = read_input('受管备份文件名（留空取消）: ', 64, hidden=True)
        if not name:
            return
        checksum = read_input('备份 SHA-256（留空取消）: ', 64, hidden=True)
        if not checksum:
            return
        if not CORE_RESTORE_ARCHIVE.fullmatch(name) or not HEX.fullmatch(checksum):
            raise Refused('invalid core restore input')
        arguments.extend((name, checksum))
        print('恢复会停服、备份当前数据，并撤销备份中的旧登录和维护授权。')
    elif action in ('update-core', 'rollback-core'):
        core = read_input('核心 sing-box/xray（留空取消）: ', 8)
        if not core:
            return
        if core not in ('sing-box', 'xray'):
            raise Refused('invalid core name')
        arguments.append(core)
        if action == 'update-core':
            release = read_input('官方组件 Release 数字 ID（留空取消）: ', 19)
            if not release:
                return
            if not re.fullmatch('[1-9][0-9]{0,18}', release) or int(release) >= 2**63:
                raise Refused('invalid core release ID')
            arguments.append(release)
        print('操作会短暂停服并创建当前数据备份。')
    elif action == 'recover-core':
        print('按中断记录核验并恢复核心选择及服务状态；中断的双域备份只恢复服务，不重新备份。')
    elif action == 'backup-components':
        print('短暂停服后备份节点数据及核心版本，完成后恢复原服务状态。')
    elif action != 'core-status':
        print('按中断记录恢复已确认代次或原数据，保留失败副本。')
    if action != 'core-status':
        answer = read_input('确认执行 [y/N]: ', 16)
        if answer is None or answer.strip().lower() not in ('y', 'yes'):
            print('已取消，未执行操作。')
            return
    manager, _ = verify_installation()
    if manager != 'systemd':
        raise Refused('core restore requires systemd')
    receipt_raw = trusted_bytes(RECEIPT, MAX_JSON)
    receipt = decode_json(receipt_raw)
    checksum = receipt['files'][str(CONTROL/'system_ops.py')]
    if not HEX.fullmatch(checksum):
        raise Refused('invalid controller checksum')
    with deferred_signals() as cancelled:
        code, output = run_command([str(PYTHON), '-B', '-I', '-S', '-c', CORE_RESTORE_BOOTSTRAP,
                               checksum, hashlib.sha256(receipt_raw).hexdigest(), *arguments],
                              cancelled, timeout=TIMEOUT+60, cleanup_grace=180)
    if code != 0:
        if action == 'restore-components' and re.fullmatch(
                rb'RESTORE_STAGE_(?:PREFLIGHT|STOP|STAGE(?:_(?:GUARD|POLICY|PREPARE|PLACE|RECEIPT))?'
                rb'|BACKUP|EXCHANGE|OBSERVE|ACTIVATE|FINISH)\n', output):
            print('恢复失败阶段 ' + output.decode('ascii').strip())
        raise Refused('core restore unconfirmed')
    result = decode_json(output)
    if type(result) is not dict or type(result.get('outcome')) is not str:
        raise Refused('unexpected core task result')
    outcome = result.get('outcome')
    if action == 'core-status':
        show_core_selection(result)
        return
    needs_backup = action == 'restore-components'
    if action in ('update-core', 'rollback-core'):
        messages = {'already_current': '核心已是所选版本。',
                    'switched': '核心切换完成，已核对服务状态。',
                    'restored_previous': '核心试跑失败，已恢复操作前的版本。'}
        needs_backup = outcome != 'already_current'
    elif action == 'restore-components':
        messages = {'restored_backup': '恢复完成，已核对数据与服务状态。'}
    elif action == 'backup-components':
        messages = {'backup_created': '双域备份完成，已核对服务状态。'}
        needs_backup = True
    else:
        messages = {key: '恢复完成，已核对数据与服务状态。'
                    for key in ('restored_previous', 'resumed_confirmed')}
        if action == 'recover-core':
            messages['backup_recovered'] = '备份中断恢复完成，已核对原服务状态；未重新创建备份。'
    if outcome not in messages:
        raise Refused('unexpected core task result')
    name, digest = result.get('backup_name'), result.get('backup_sha256')
    if needs_backup or name is not None or digest is not None:
        if (type(name) is not str or not CORE_RESTORE_ARCHIVE.fullmatch(name)
                or type(digest) is not str or not HEX.fullmatch(digest)):
            raise Refused('invalid core backup receipt')
        print('原数据备份文件 ' + name)
        print('SHA-256 ' + digest)
    print(messages[outcome])


def show_core_selection(result):
    if result == {'outcome': 'bundle_default'}:
        print('未发现独立核心选择记录。')
        return
    if result == {'outcome': 'core_pending'}:
        print('有待恢复的核心事务，当前选择待确认。')
        return
    cores = result.get('cores')
    if (set(result) != {'outcome', 'cores'} or result['outcome'] != 'core_selected'
            or type(cores) is not dict or set(cores) != {'sing-box', 'xray'}):
        raise Refused('invalid core selection summary')
    version = re.compile(r'(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2}\Z')
    def valid_version(value):
        return type(value) is str and len(value) <= 100 and version.fullmatch(value)
    for core in ('sing-box', 'xray'):
        row = cores[core]
        if (type(row) is not dict or set(row) != {'current', 'previous'}
                or not valid_version(row['current'])
                or row['previous'] is not None and not valid_version(row['previous'])):
            raise Refused('invalid core selection version')
    for core in ('sing-box', 'xray'):
        row = cores[core]
        print(core + ' 已选择 ' + row['current'] + '，上一个版本 ' + (row['previous'] or '无'))


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
        if action not in ('status', 'network', 'open-port', 'upgrade', 'grants',
                          *CORE_TASKS, *ACTIONS):
            print('未知选项，请输入 h 查看帮助。')
            continue
        if action in CORE_TASKS:
            try:
                terminal_core_action(action)
            except (Refused, OSError, ValueError, KeyError, subprocess.SubprocessError):
                print('恢复未完成。请检查服务状态及待恢复记录。')
            continue
        if action in ('upgrade', 'grants'):
            try:
                if action == 'upgrade':
                    answer = read_input('确认下载官方稳定版本并沿用原配置升级 [y/N]: ', 16)
                    if answer is None or answer.strip().lower() not in ('y', 'yes'):
                        print('已取消，未下载或更改安装。')
                        continue
                    client = fixed_module('maintenance_client')
                    task = client.request({'protocol': 1, 'operation': 'submit', 'action': 'upgrade'})
                    print('升级任务已登记: ' + task['id'] + '；面板停止期间由独立维护服务继续执行。')
                    while task['state'] in ('queued', 'running'):
                        time.sleep(1)
                        task = client.request({'protocol': 1, 'operation': 'get', 'task_id': task['id']})
                    if task['state'] != 'succeeded':
                        print('升级未完成；任务及原配置恢复证据已保留，请检查受管安装。')
                    elif task.get('result', {}).get('upgraded'):
                        print('升级完成，TLS 健康已核验。')
                    else:
                        print('当前已是相同或更高版本，未降级。')
                else:
                    terminal_grants()
            except (Exception, KeyboardInterrupt):
                print('系统维护请求未完成；已登记任务不会因终端退出取消，请通过面板查询。')
            continue
        if action in ('network', 'open-port'):
            try:
                if action == 'open-port':
                    protocol = read_input('协议 tcp/udp（留空取消）: ', 3)
                    if not protocol:
                        continue
                    value = read_input('精确端口 1–65535（22 除外，留空取消）: ', 5)
                    if not value:
                        continue
                    if protocol not in ('tcp', 'udp') or not value.isascii() or not value.isdecimal():
                        print('协议或端口无效，未执行操作。')
                        continue
                    port = int(value)
                    if not 1 <= port <= 65535 or port == 22:
                        print('协议或端口无效，未执行操作。')
                        continue
                    print('仅新增所选本机规则，不自动启用防火墙、不创建节点；UDP 放行不等于应用 UDP 已获支持。')
                    answer = read_input('确认放行 ' + str(port) + '/' + protocol + ' [yes/N]: ', 16)
                    if answer is None:
                        return 0
                    if answer != 'yes':
                        print('已取消，未执行操作。')
                        continue
                with deferred_signals() as cancelled:
                    with installation_lock():
                        verify_installation()
                        lines = network_diagnostics(cancelled) if action == 'network' else [network_open(protocol, port, cancelled)]
                    for line in lines:
                        print(line)
                    if cancelled:
                        return 128 + cancelled[0]
            except (Refused, OSError, ValueError, KeyError, subprocess.SubprocessError):
                print('网络检查或规则修改未完成；所选规则状态无法确认，请重新只读诊断。')
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
