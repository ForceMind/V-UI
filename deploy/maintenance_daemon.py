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
    elif operation not in ('list', 'status', 'logs'):
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
    with ops.trusted_directory(path.parent):
        try:
            path.mkdir(mode=mode)
            os.chown(path, 0, gid)
        except FileExistsError:
            pass
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != gid
                or stat.S_IMODE(info.st_mode) != mode):
            raise ValueError('unsafe state directory')


class Store:
    def __init__(self, ops):
        self.ops = ops

    def read(self, identity):
        if not ID.fullmatch(identity):
            raise ValueError('task')
        path = TASKS / (identity + '.json')
        if stat.S_IMODE(path.lstat().st_mode) != 0o600:
            raise ValueError('task mode')
        value = self.ops.decode_json(self.ops.trusted_bytes(path, MAX_RECORD))
        if value.get('id') != identity:
            raise ValueError('task identity')
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
    manager, account = ops.verify_installation()
    receipt_snapshot = ops.trusted_bytes(ops.RECEIPT, 16384)
    if manager != 'systemd':
        raise ValueError('systemd required')
    directory(ops, STATE)
    directory(ops, TASKS)
    directory(ops, SOCKET.parent, 0o750, account.pw_gid)
    store = Store(ops)
    store.interrupt()
    if os.path.lexists(SOCKET):
        info = SOCKET.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0:
            raise ValueError('socket')
        SOCKET.unlink()
    active = None
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(SOCKET)); os.chown(SOCKET, 0, account.pw_gid); SOCKET.chmod(0o660)
        server.listen(8); server.settimeout(1)
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
                try:
                    value = receive(connection, ops, account.pw_uid)
                    operation = value['operation']
                    if operation == 'get':
                        result = public(store.read(value['task_id']))
                    elif operation == 'list':
                        result = [public(row) for row in store.all()[:100]]
                    elif operation == 'status':
                        # No user-facing arbitrary unit selection.
                        services = ops.Services(manager, [])
                        services.deadline = time.monotonic() + 3
                        result = {'services': services.snapshot(), 'busy': active is not None, **install_summary(ops)}
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
                except Exception:
                    response = {'ok': False, 'error': 'MAINTENANCE_REFUSED'}
                try:
                    connection.sendall(json.dumps(response, separators=(',', ':')).encode())
                except OSError:
                    pass


def main():
    if (len(sys.argv) != 1 or os.getuid() != 0 or os.geteuid() != 0
            or not sys.flags.isolated or not sys.flags.no_site or not sys.flags.dont_write_bytecode):
        return 1
    os.umask(0o077)
    ops = load('system_ops')
    with daemon_lock(ops):
        serve(ops)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('V-UI system maintenance unavailable; inspect trusted installation.', file=sys.stderr)
        raise SystemExit(1)
