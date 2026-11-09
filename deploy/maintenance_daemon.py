#!/usr/bin/python3
"""Fixed root stdlib task service. No panel code is ever imported as root."""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import struct
import sys
import time
import uuid

CONTROL = Path('/usr/local/lib/v-ui')
STATE = Path('/var/lib/v-ui-maintenance')
TASKS = STATE / 'tasks'
SOCKET = Path('/run/v-ui-maintenance/control.sock')
DAEMON_LOCK = Path('/run/lock/v-ui-maintenance.lock')
ID = re.compile(r'[a-f0-9]{32}\Z')
ACTIONS = ('status', 'start', 'stop', 'restart', 'backup', 'restore', 'upgrade')
MAX_TASKS = 1024
MAX_BACKUPS = 256
MAX_RECORD = 16384
STARTUP_STAGE = 'ENTRY'


def load(name):
    spec = importlib.util.spec_from_file_location('_vui_fixed_' + name, CONTROL / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate(value):
    if type(value.get('protocol')) is not int or value['protocol'] != 1:
        raise ValueError('protocol')
    operation = value.get('operation')
    expected = {'protocol', 'operation'}
    if operation == 'submit':
        expected.add('action')
        if value.get('action') not in ACTIONS:
            raise ValueError('action')
        if value['action'] == 'restore':
            expected.add('backup_id')
    elif operation == 'get':
        expected.add('task_id')
    elif operation not in ('list', 'status', 'logs', 'core_status'):
        raise ValueError('operation')
    if set(value) != expected:
        raise ValueError('fields')
    for name in ('task_id', 'backup_id'):
        if name in value and (not isinstance(value[name], str) or not ID.fullmatch(value[name])):
            raise ValueError('identity')
    return operation


@contextmanager
def daemon_lock(ops):
    with ops.trusted_directory(DAEMON_LOCK.parent, lock_parent=True) as directory:
        fd = os.open(DAEMON_LOCK.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if (info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError('lock')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)


def directory(ops, path, mode=0o700, gid=0):
    with ops.trusted_directory(path.parent) as parent:
        created = False
        try:
            # Start private even when the caller's umask is unusual. Set the
            # exact published mode only on this newly created, opened inode.
            os.mkdir(path.name, mode=0o700, dir_fd=parent)
            created = True
        except FileExistsError:
            pass
        fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
                raise ValueError('unsafe state directory')
            if created:
                os.fchown(fd, 0, gid)
                os.fchmod(fd, mode)
                info = os.fstat(fd)
            if info.st_gid != gid or stat.S_IMODE(info.st_mode) != mode:
                raise ValueError('unsafe state directory')
            named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError('state directory replaced')
        finally:
            os.close(fd)


class Store:
    def __init__(self, ops):
        self.ops = ops

    def read(self, identity):
        if not ID.fullmatch(identity):
            raise ValueError('task')
        path = TASKS / (identity + '.json')
        def private(info):
            return (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                    and stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= MAX_RECORD)
        for attempt in range(3):
            try:
                before = path.lstat()
            except OSError as error:
                error._vui_task_stage = 'TASK_LSTAT'; raise
            if stat.S_IMODE(before.st_mode) != 0o600:
                error = ValueError('task mode'); error._vui_task_stage = 'TASK_LSTAT'; raise error
            try:
                raw = self.ops.trusted_bytes(path, MAX_RECORD)
                break
            except self.ops.Refused as error:
                error._vui_task_stage = 'TASK_TRUST_READ'
                # A task child publishes state with an atomic Root-owned
                # replacement. Retry only this provable replacement, never a
                # stable unsafe file or a general maintenance refusal.
                after = path.lstat()
                if (attempt == 2 or not private(before) or not private(after)
                        or (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)):
                    raise
        try:
            value = self.ops.decode_json(raw)
        except Exception as error:
            error._vui_task_stage = 'TASK_JSON'; raise
        if type(value) is not dict or value.get('id') != identity:
            error = ValueError('task identity'); error._vui_task_stage = 'TASK_ID'; raise error
        return value

    def write(self, value):
        if not ID.fullmatch(value['id']):
            raise ValueError('task')
        raw = json.dumps(value, separators=(',', ':'), allow_nan=False).encode()
        if len(raw) > MAX_RECORD:
            raise ValueError('task size')
        value['updated_at'] = int(time.time())
        raw = json.dumps(value, separators=(',', ':'), allow_nan=False).encode()
        # All names are generated, parents are root-private and checked at startup.
        temp = TASKS / ('.' + uuid.uuid4().hex)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(raw); output.flush(); os.fsync(output.fileno())
            os.replace(temp, TASKS / (value['id'] + '.json'))
            with self.ops.trusted_directory(TASKS) as parent:
                os.fsync(parent)
        finally:
            temp.unlink(missing_ok=True)

    def all(self):
        names = sorted(path.stem for path in TASKS.iterdir() if path.suffix == '.json')
        if len(names) > MAX_TASKS:
            raise ValueError('task count')
        return sorted((self.read(name) for name in names), key=lambda row: row['created_at'], reverse=True)

    def interrupt(self):
        for row in self.all():
            if row['state'] in ('queued', 'running'):
                row.update(state='failed', error='MAINTENANCE_INTERRUPTED')
                self.write(row)

    def create(self, action, backup_id=None):
        rows = self.all()
        protected_backups = [row for row in rows if row['action'] == 'backup' and row['state'] == 'succeeded']
        # Keep every successful backup-to-ID mapping. Cap only new backup
        # creation so recovery records can never consume all maintenance slots.
        if action == 'backup' and len(protected_backups) >= MAX_BACKUPS:
            raise ValueError('backup task capacity')
        victims = [row for row in reversed(rows) if row['state'] in ('succeeded', 'failed')
                   and not (row['action'] == 'backup' and row['state'] == 'succeeded')]
        needed = max(0, len(rows) - MAX_TASKS + 1)
        if len(victims) < needed:
            raise ValueError('task capacity')
        for row in victims[:needed]:
            # Metadata only. Never remove backup bytes, queued/running work or
            # successful backup mappings needed for a future fixed-ID restore.
            (TASKS / (row['id'] + '.json')).unlink()
        if needed:
            with self.ops.trusted_directory(TASKS) as parent:
                os.fsync(parent)
        row = {'id': uuid.uuid4().hex, 'action': action, 'state': 'queued', 'created_at': int(time.time())}
        if backup_id is not None:
            row['backup_id'] = backup_id
        self.write(row)
        return row


def public(row):
    return {key: row[key] for key in ('id', 'action', 'state', 'created_at', 'updated_at', 'result', 'error') if key in row}


def public_core_selection(value):
    """Only selection metadata; never paths, backup names or source material."""
    if type(value) is not dict:
        raise ValueError('core status')
    outcome = value.get('outcome')
    if outcome in ('bundle_default', 'core_pending') and set(value) == {'outcome'}:
        return {'outcome': outcome}
    if outcome != 'core_selected' or set(value) != {'outcome', 'cores'}:
        raise ValueError('core status')
    cores = value['cores']
    if type(cores) is not dict or set(cores) != {'sing-box', 'xray'}:
        raise ValueError('core status')
    result = {}
    for name, row in cores.items():
        if type(row) is not dict or set(row) != {'current', 'previous'}:
            raise ValueError('core status')
        for key in ('current', 'previous'):
            version = row[key]
            if key == 'previous' and version is None:
                continue
            if type(version) is not str or not 1 <= len(version) <= 64 or not re.fullmatch(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', version):
                raise ValueError('core status')
        result[name] = {'current': row['current'], 'previous': row['previous']}
    return {'outcome': outcome, 'cores': result}


def install_summary(ops):
    config = ops.decode_json(ops.trusted_bytes(ops.CONFIG, 16384))
    target, ready, release = config.get('target'), config.get('ready'), config.get('release_id')
    if (target not in ('x86_64-gnu', 'aarch64-gnu', 'x86_64-musl', 'aarch64-musl')
            or type(ready) is not bool
            or release is not None and (not isinstance(release, str) or not ops.RELEASE.fullmatch(release))):
        raise ValueError('installation summary unavailable')
    # release may be absent during the existing installer's ready:false phase.
    return {'release_id': release, 'target': target, 'ready': ready}


LOG_CODES = (
    ('SSLCertVerificationError', 'TLS_VERIFY_FAILED', 'TLS 证书验证失败'),
    ('SSL: CERTIFICATE_VERIFY_FAILED', 'TLS_VERIFY_FAILED', 'TLS 证书验证失败'),
    ('Launch code failed integrity check', 'CODE_INTEGRITY', '启动代码完整性验证失败'),
    ('Prepared manifest changed', 'MANIFEST_CHANGED', '已准备版本的清单发生变化'),
    ('Portable runtime is missing', 'RUNTIME_MISSING', '已准备运行时缺失'),
    ('PermissionError', 'PERMISSION_DENIED', '服务访问权限不足'),
    ('Address already in use', 'PORT_IN_USE', '服务监听端口已占用'),
    ('ModuleNotFoundError', 'DEPENDENCY_MISSING', '服务依赖缺失'),
    ('MemoryError', 'MEMORY_EXHAUSTED', '服务内存不足'),
    ('Main process exited', 'PROCESS_EXITED', '主服务进程已退出'),
    ('Failed with result', 'SERVICE_FAILED', '服务运行失败'),
    ('Scheduled restart job', 'RESTART_SCHEDULED', '服务已安排重新启动'),
    ('Started V-UI', 'SERVICE_STARTED', 'V-UI 服务已启动'),
    ('V-UI verified release listening with HTTPS', 'HTTPS_LISTENING', '面板已监听 HTTPS'),
)


def safe_logs(raw):
    result = []
    for line in raw.decode('utf-8', errors='replace').splitlines()[-60:]:
        try:
            value = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(value, dict):
            continue
        unit = value.get('_SYSTEMD_UNIT')
        if unit not in ('v-ui.service', 'v-ui-http01.service'):
            continue
        message = value.get('MESSAGE', '')
        if not isinstance(message, str):
            message = ''
        code, summary = next(((code, text) for needle, code, text in LOG_CODES if needle in message),
                             ('UNCLASSIFIED', '未分类服务日志'))
        timestamp = value.get('__REALTIME_TIMESTAMP', '')
        timestamp = int(timestamp) // 1_000_000 if isinstance(timestamp, str) and re.fullmatch(r'[0-9]{1,20}', timestamp) else None
        result.append({'timestamp': timestamp, 'unit': unit, 'code': code, 'summary': summary})
    return result


def logs(ops):
    program = Path('/usr/bin/journalctl')
    ops.trusted_executable(program)
    status, raw = ops.run_command([str(program), '--unit=v-ui.service', '--unit=v-ui-http01.service',
        '--lines=60', '--no-pager', '--output=json', '--output-fields=MESSAGE,__REALTIME_TIMESTAMP,_SYSTEMD_UNIT'], timeout=3)
    if status != 0:
        raise ValueError('logs unavailable')
    return {'entries': safe_logs(raw), 'redacted': True, 'limit': 60}


def execute(ops, store, row):
    try:
        os.setsid()
        def interrupted(*_):
            raise TimeoutError('maintenance interrupted')
        signal.signal(signal.SIGALRM, interrupted)
        signal.signal(signal.SIGTERM, interrupted)
        signal.alarm(3600)
        row['state'] = 'running'; store.write(row)
        if row['action'] == 'upgrade':
            result = load('maintenance_upgrade').upgrade(ops)
        else:
            with ops.installation_lock():
                manager, account = ops.verify_installation()
                services = ops.Services(manager, [])
                if row['action'] == 'status':
                    result = {'services': services.snapshot(), **install_summary(ops)}
                else:
                    request = {'protocol': 1, 'action': row['action']}
                    if row['action'] == 'restore':
                        backup = store.read(row['backup_id'])
                        if backup['action'] != 'backup' or backup['state'] != 'succeeded':
                            raise ValueError('backup unavailable')
                        saved = backup['_backup']
                        if not ops.BACKUP.fullmatch(saved['name']) or not ops.HEX.fullmatch(saved['sha256']):
                            raise ValueError('backup record')
                        request.update(path='/var/lib/v-ui/backups/' + saved['name'], sha256=saved['sha256'])
                    outcome = ops.operate(request, services, account, [])
                    result = {'services': services.snapshot()}
                    if row['action'] == 'backup':
                        row['_backup'] = {'name': outcome['backup_name'], 'sha256': outcome['backup_sha256']}
                        result['backup_id'] = row['id']
        row.update(state='succeeded', result=result)
    except BaseException:
        # Never persist command output, exception messages, caller body or paths.
        row.update(state='failed', error='MAINTENANCE_FAILED')
    store.write(row)
    os._exit(0)


def receive(connection, ops, uid):
    # SO_PEERCRED is kernel supplied, never a request field.
    _, peer_uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if peer_uid not in (0, uid):
        raise ValueError('peer')
    connection.settimeout(2)
    data = bytearray()
    while True:
        part = connection.recv(4097 - len(data))
        if not part:
            break
        data.extend(part)
        if len(data) > 4096:
            raise ValueError('request size')
    if not data.endswith(b'\n') or b'\n' in data[:-1]:
        raise ValueError('framing')
    value = ops.decode_json(bytes(data[:-1]))
    validate(value)
    return value


def serve(ops):
    global STARTUP_STAGE
    STARTUP_STAGE = 'VERIFY_RECEIPT'
    manager, account = ops.verify_installation()
    receipt_snapshot = ops.trusted_bytes(ops.RECEIPT, 16384)
    if manager != 'systemd':
        raise ValueError('systemd required')
    STARTUP_STAGE = 'STATE_DIRECTORY'
    directory(ops, STATE)
    STARTUP_STAGE = 'TASK_DIRECTORY'
    directory(ops, TASKS)
    STARTUP_STAGE = 'SOCKET_DIRECTORY'
    directory(ops, SOCKET.parent, 0o750, account.pw_gid)
    STARTUP_STAGE = 'READ_TASKS'
    store = Store(ops)
    store.interrupt()
    if os.path.lexists(SOCKET):
        info = SOCKET.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0:
            raise ValueError('socket')
        SOCKET.unlink()
    active = None
    STARTUP_STAGE = 'SOCKET_BIND'
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(SOCKET)); os.chown(SOCKET, 0, account.pw_gid); SOCKET.chmod(0o660)
        server.listen(8); server.settimeout(1)
        STARTUP_STAGE = 'SERVING'
        while True:
            if active is not None:
                pid, identity = active
                ended, _ = os.waitpid(pid, os.WNOHANG)
                if ended:
                    row = store.read(identity)
                    if row['state'] in ('queued', 'running'):
                        row.update(state='failed', error='MAINTENANCE_INTERRUPTED'); store.write(row)
                    active = None
                    # Upgrade replaces root helpers; reload only after its task is
                    # durably final. No daemon/service restart kills the upgrader.
                    if row['action'] == 'upgrade' and row['state'] == 'succeeded':
                        os.execve(str(ops.PYTHON), [str(ops.PYTHON), '-B', '-I', '-S', str(CONTROL / 'maintenance_daemon.py')], ops.ENV)
            if active is None:
                # An external terminal installer may refresh this service's
                # root closure too. Reload only after the complete receipt is
                # published and never while one of our own tasks is running.
                try:
                    current_receipt = ops.trusted_bytes(ops.RECEIPT, 16384)
                    if current_receipt != receipt_snapshot:
                        record = ops.decode_json(current_receipt)
                        script = CONTROL / 'maintenance_daemon.py'
                        if record.get('files', {}).get(str(script)) == hashlib.sha256(ops.trusted_bytes(script)).hexdigest():
                            os.execve(str(ops.PYTHON), [str(ops.PYTHON), '-B', '-I', '-S', str(script)], ops.ENV)
                except (OSError, ValueError, ops.Refused):
                    pass
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            with connection:
                request_stage = 'RECEIVE'
                try:
                    value = receive(connection, ops, account.pw_uid)
                    operation = value['operation']
                    request_stage = operation.upper()
                    if operation == 'get':
                        row = store.read(value['task_id'])
                        request_stage = 'PUBLIC'
                        result = public(row)
                    elif operation == 'list':
                        result = [public(row) for row in store.all()[:100]]
                    elif operation == 'status':
                        # No user-facing arbitrary unit selection.
                        services = ops.Services(manager, [])
                        services.deadline = time.monotonic() + 3
                        result = {'services': services.snapshot(), 'busy': active is not None, **install_summary(ops)}
                    elif operation == 'core_status':
                        result = public_core_selection(ops.core_selection_status(ops))
                    elif operation == 'logs':
                        result = logs(ops)
                    else:
                        if active is not None:
                            raise ValueError('busy')
                        # Verify before recording/dispatch; task child checks again
                        # under the installation lock before any OS mutation.
                        ops.verify_installation()
                        row = store.create(value['action'], value.get('backup_id'))
                        try:
                            pid = os.fork()
                        except OSError:
                            row.update(state='failed', error='MAINTENANCE_DISPATCH_FAILED')
                            store.write(row)
                            raise
                        if pid == 0:
                            server.close(); connection.close()
                            execute(ops, store, row)
                        active = (pid, row['id'])
                        result = public(row)
                    response = {'ok': True, 'result': result}
                except Exception as error:
                    # Fixed journal vocabulary only: no request/task bytes,
                    # private paths, exception text or backup metadata.
                    task_stage = getattr(error, '_vui_task_stage', None)
                    if task_stage in ('TASK_LSTAT','TASK_TRUST_READ','TASK_JSON','TASK_ID'):
                        request_stage = task_stage
                    category = ('TRUSTED_INPUT' if isinstance(error, ops.Refused) else
                                'PROTOCOL_OR_STATE' if isinstance(error, ValueError) else
                                'IO' if isinstance(error, OSError) else 'INTERNAL')
                    print('VUI_MAINTENANCE_REFUSED:' + request_stage + ':' + category, flush=True)
                    response = {'ok': False, 'error': 'MAINTENANCE_REFUSED'}
                try:
                    connection.sendall(json.dumps(response, separators=(',', ':')).encode())
                except OSError:
                    pass


def main():
    global STARTUP_STAGE
    if (len(sys.argv) != 1 or os.getuid() != 0 or os.geteuid() != 0
            or not sys.flags.isolated or not sys.flags.no_site or not sys.flags.dont_write_bytecode):
        return 1
    os.umask(0o077)
    STARTUP_STAGE = 'LOAD_CONTROLLER'
    ops = load('system_ops')
    STARTUP_STAGE = 'INSTANCE_LOCK'
    with daemon_lock(ops):
        serve(ops)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        # Fixed stage only: never interpolate exception text, paths or state.
        print('V-UI maintenance startup refused: STARTUP_' + STARTUP_STAGE, file=sys.stderr)
        raise SystemExit(1)
