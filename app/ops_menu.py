"""Unprivileged installed-payload menu with explicit candidate-only preparation. No application/database imports."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import signal
import stat
import subprocess
import sys
import termios
import time
from urllib.parse import urlsplit


MANAGED_ROOT = Path('/var/lib/v-ui')
SERVICE_CONFIG = Path('/etc/v-ui/service.json')
METADATA_LIMIT = 16 * 1024
MANIFEST_LIMIT = 4_000_000
QUERY_LIMIT = 16 * 1024
QUERY_TIMEOUT = 3
TARGETS = frozenset(('x86_64-gnu', 'aarch64-gnu', 'x86_64-musl', 'aarch64-musl'))
SYSTEMD_UNITS = ('v-ui.service', 'v-ui-http01.socket', 'v-ui-http01.service')
OPENRC_UNITS = ('v-ui', 'v-ui-http01')
STAGE_TIMEOUT = 600
STAGE_TERM_GRACE = 5
STAGE_REAP_TIMEOUT = 5
STAGE_PATH_LIMIT = 4096
MENU = ('\nV-UI 运维菜单\n'
        '1  查看服务状态\n'
        '2  查看面板地址和安装信息\n'
        '3  完整校验活动版本和运行时（读取全部哈希，可能较慢）\n'
        '4  校验并准备本地升级包\n'
        'h  帮助\n'
        '0  退出\n')
PROMPT = '请选择 [1/2/3/4/h/0]: '


class MetadataError(ValueError):
    """Only static, non-sensitive messages cross the menu boundary."""


def _private(info, owner):
    if info.st_uid != owner or stat.S_IMODE(info.st_mode) & 0o077:
        raise MetadataError('metadata ownership or permissions')


@contextmanager
def _directory(path, *, owner=None):
    """Open every absolute component without following symbolic links."""
    path = Path(path).absolute()
    if '..' in path.parts:
        raise MetadataError('parent path component')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        if owner == 0:
            info = os.fstat(fd)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise MetadataError('system root directory permissions')
        for component in path.parts[1:]:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=fd)
            os.close(fd)
            fd = next_fd
            if owner == 0:
                info = os.fstat(fd)
                if info.st_uid != 0 or info.st_mode & 0o022:
                    raise MetadataError('system metadata directory permissions')
        if owner is not None and owner != 0:
            info = os.fstat(fd)
            _private(info, owner)
            if stat.S_IMODE(info.st_mode) != 0o700:
                raise MetadataError('installation directory permissions')
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _child_directory(parent, name, owner):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        _private(os.fstat(fd), owner)
        yield fd
    finally:
        os.close(fd)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MetadataError('duplicate metadata key')
        result[key] = value
    return result


def _json_file(directory, name, owner, limit=METADATA_LIMIT, *, raw_bytes=False):
    # NONBLOCK prevents a substituted FIFO from blocking before fstat rejects it.
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise MetadataError('metadata type or size')
        if owner == 0:
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise MetadataError('system metadata file permissions')
        else:
            _private(info, owner)
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise MetadataError('metadata size')
        value = json.loads(raw, object_pairs_hook=_object)
        if not isinstance(value, dict):
            raise MetadataError('metadata object required')
        return raw if raw_bytes else value
    finally:
        os.close(fd)


def _release_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', value):
        raise MetadataError('invalid release identifier')
    return value


def _selection(directory, owner):
    current = _json_file(directory, 'CURRENT.json', owner)
    identity = _release_id(current.get('release_id'))
    previous = current.get('previous_id')
    if previous is not None:
        previous = _release_id(previous)
    return identity, previous


def installation_metadata(root, *, full=False):
    owner = os.geteuid()
    with _directory(root, owner=owner) as directory:
        identity, previous = _selection(directory, owner)
        with _child_directory(directory, 'releases', owner) as releases:
            with _child_directory(releases, identity, owner) as release:
                ready = _json_file(release, 'READY.json', owner)
                target = ready.get('runtime_key')
                if target not in TARGETS:
                    raise MetadataError('invalid runtime target')
                if full:
                    with _child_directory(release, 'payload', owner) as payload:
                        manifest = _json_file(payload, 'MANIFEST.json', owner, MANIFEST_LIMIT)
                        if (type(manifest.get('schema')) is not int or manifest['schema'] != 1
                                or manifest.get('release_id') != identity
                                or not isinstance(manifest.get('files'), dict)):
                            raise MetadataError('invalid manifest')
                    with _child_directory(release, 'runtime', owner):
                        pass
    return {'release_id': identity, 'previous_id': previous, 'runtime_key': target}


def _origin(value):
    if not isinstance(value, str) or len(value) > 300 or not value.isascii():
        raise MetadataError('invalid origin')
    parsed = urlsplit(value)
    host = parsed.hostname
    if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
            or parsed.path or '?' in value or '#' in value or not host
            or any(ord(char) < 33 or ord(char) == 127 for char in value)
            or not re.fullmatch(r'[A-Za-z0-9.-]{1,253}', host)
            or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                   for label in host.split('.'))
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
        raise MetadataError('invalid origin')
    return value


def managed_metadata(root):
    # A per-user deployment must never inspect an unrelated host installation.
    if Path(root) != MANAGED_ROOT or pwd.getpwnam('v-ui').pw_uid != os.geteuid():
        raise MetadataError('not a managed installation owner')
    with _directory(SERVICE_CONFIG.parent, owner=0) as directory:
        config = _json_file(directory, SERVICE_CONFIG.name, 0)
    if (type(config.get('installer')) is not int or config['installer'] != 1
            or config.get('service_manager') not in ('systemd', 'openrc')
            or type(config.get('ready')) is not bool or config.get('target') not in TARGETS):
        raise MetadataError('invalid installation metadata')
    return {'origin': _origin(config.get('origin')), 'ready': config['ready'],
            'service_manager': config['service_manager'], 'target': config['target']}


def _trusted_binary(manager):
    candidates = (('/usr/bin/systemctl', '/bin/systemctl') if manager == 'systemd'
                  else ('/sbin/rc-service', '/usr/sbin/rc-service'))
    for candidate in candidates:
        try:
            # usr-merge symlinks are normal for executables. Resolve, then check
            # all actual parents; caller-controlled PATH is never consulted.
            path = Path(candidate).resolve(strict=True)
            with _directory(path.parent, owner=0) as directory:
                info = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
                if (stat.S_ISREG(info.st_mode) and info.st_uid == 0
                        and not info.st_mode & 0o022 and info.st_mode & 0o111):
                    return str(path)
        except (OSError, MetadataError, RuntimeError):
            continue
    return None


def _query(argv):
    """Bound time and bytes without communicate() buffering unlimited output."""
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
           'SYSTEMD_PAGER': '', 'SYSTEMD_COLORS': '0', 'SYSTEMD_URLIFY': '0',
           'RC_NOCOLOR': 'yes'}
    process = None
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, env=env, start_new_session=True)
        output = bytearray()
        deadline = time.monotonic() + QUERY_TIMEOUT
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None, '', 'timeout'
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(), min(4096, QUERY_LIMIT + 1 - len(output)))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(chunk)
                        if len(output) > QUERY_LIMIT:
                            return None, '', 'oversized'
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, '', 'timeout'
            return process.wait(timeout=remaining), output.decode('utf-8', errors='replace'), None
    except PermissionError:
        return None, '', 'inaccessible'
    except FileNotFoundError:
        return None, '', 'unavailable'
    except (OSError, subprocess.SubprocessError):
        return None, '', 'unknown'
    finally:
        if process is not None:
            # A process group also covers any status helper still holding the pipe.
            if process.returncode is None:
                try:
                    os.killpg(process.pid, 9)
                except ProcessLookupError:
                    pass
            process.wait()
            process.stdout.close()


def service_status(manager, unit, binary):
    units = SYSTEMD_UNITS if manager == 'systemd' else OPENRC_UNITS
    if manager not in ('systemd', 'openrc') or unit not in units:
        return 'unknown', ''
    argv = ([binary, '--no-pager', '--no-ask-password', 'show', '--property=LoadState',
             '--property=ActiveState', '--property=SubState', unit] if manager == 'systemd'
            else [binary, '--nocolor', unit, 'status'])
    code, output, error = _query(argv)
    if error:
        return error if error in ('inaccessible', 'unavailable') else 'unknown', ''
    if code != 0 and re.search(r'access denied|permission denied|not authorized|operation not permitted', output, re.I):
        return 'inaccessible', ''
    if manager == 'openrc':
        status = re.fullmatch(r'\s*\*?\s*status: (started|stopped|crashed|starting|stopping|inactive)\s*', output)
        if status:
            value = status[1]
            if code == 0 and value == 'started':
                return 'active', ''
            if code == 3 and value == 'stopped':
                return 'inactive', ''
            if code == 32 and value == 'crashed':
                return 'failed', ''
            if (code, value) in ((8, 'starting'), (4, 'stopping')):
                return 'transitional', value
            if code == 16 and value == 'inactive':
                return 'inactive', 'inactive'
        return 'unknown', ''
    if code != 0:
        return 'unknown', ''
    pairs = [line.split('=', 1) for line in output.splitlines() if line]
    if (len(pairs) != 3 or any(len(pair) != 2 for pair in pairs)
            or {pair[0] for pair in pairs} != {'LoadState', 'ActiveState', 'SubState'}):
        return 'unknown', ''
    values = dict(pairs)
    if values['LoadState'] == 'not-found':
        return 'unavailable', ''
    if values['LoadState'] != 'loaded':
        return 'unknown', ''
    state, substate = values['ActiveState'], values['SubState']
    # Only coherent fixed enum pairs are displayed; no log lines cross the boundary.
    stable = {'active': ('listening', 'running') if unit.endswith('.socket') else ('running', 'exited'),
              'inactive': ('dead',), 'failed': ('failed',)}
    transitions = {'activating': ('start-pre', 'start', 'start-post', 'auto-restart'),
                   'deactivating': ('stop', 'stop-sigterm', 'stop-sigkill', 'stop-post'),
                   'reloading': ('reload', 'reload-signal', 'reload-notify')}
    if substate in stable.get(state, ()):
        return state, substate
    if substate in transitions.get(state, ()):
        return 'transitional', substate
    return 'unknown', ''


DATA_ERRORS = (OSError, ValueError, TypeError, KeyError, RecursionError)


def show_services(root, output):
    try:
        config = managed_metadata(root)
    except DATA_ERRORS:
        print('服务状态: unavailable（受管安装信息缺失、不安全或不属于此目录/用户）', file=output)
        return
    manager = config['service_manager']
    binary = _trusted_binary(manager)
    if binary is None:
        print('服务状态: unavailable（缺少可信的服务查询程序）', file=output)
        return
    states = {}
    for unit in SYSTEMD_UNITS if manager == 'systemd' else OPENRC_UNITS:
        state, detail = service_status(manager, unit, binary)
        states[unit] = (state, detail)
        print(unit + ': ' + state + (' (' + detail + ')' if detail else ''), file=output)
    if (states.get('v-ui-http01.socket') == ('active', 'listening')
            and states.get('v-ui-http01.service', ('', ''))[0] == 'inactive'):
        print('HTTP-01: 按需响应已就绪（socket 正在监听；响应服务待连接后启动）', file=output)
    print('状态仅为服务管理器快照，不代表 HTTPS、证书或节点转发健康。', file=output)


def show_metadata(root, output):
    print('安装目录: ' + json.dumps(str(root), ensure_ascii=True), file=output)
    try:
        info = installation_metadata(root)
        print('活动版本: ' + info['release_id'], file=output)
        print('前一版本: ' + (info['previous_id'] or '无'), file=output)
        print('运行时目标: ' + info['runtime_key'], file=output)
    except DATA_ERRORS:
        print('版本信息: unavailable（元数据缺失、不安全或格式无效）', file=output)
    try:
        config = managed_metadata(root)
        print('面板地址: ' + config['origin'], file=output)
        print('服务管理器: ' + config['service_manager'], file=output)
        print('安装就绪标记（安装器历史记录，非实时健康）: ' + ('是' if config['ready'] else '否'), file=output)
        print('安装目标: ' + config['target'], file=output)
    except DATA_ERRORS:
        print('面板地址/受管安装信息: unavailable（不推断自定义实例的地址）', file=output)
    print('以上只读取有限元数据；尚未执行完整性校验。', file=output)


def verify_active(root, output):
    try:
        before = installation_metadata(root, full=True)
    except DATA_ERRORS:
        print('完整校验拒绝: 元数据或目录边界缺失、不安全或格式无效。', file=output)
        return
    print('正在完整校验活动 payload 和 runtime；不启动服务。', file=output, flush=True)
    try:
        from app.release_tools import active
        release, current = active(root)
        after = installation_metadata(root, full=True)
        if (before != after or current.get('release_id') != before['release_id']
                or release != root / 'releases' / before['release_id']):
            print('完整校验未确认: 活动版本选择发生变化，请稍后重试。', file=output)
            return
    except Exception:
        print('完整校验失败: 活动 payload、runtime 或清单不一致/不可读。', file=output)
        return
    print('完整校验通过: 活动 payload 和 runtime 与本地清单一致。', file=output)


class InputTooLong(ValueError):
    pass


def _stage_input(prompt, limit, *, hidden=False):
    # Disable terminal echo before displaying the prompt, so even a fast paste
    # cannot echo an archive path. Tests may supply an in-memory terminal.
    saved = None
    try:
        if hidden:
            try:
                fd = sys.stdin.fileno()
            except (AttributeError, OSError):
                fd = None
            if fd is not None:
                saved = termios.tcgetattr(fd)
                quiet = saved.copy()
                quiet[3] &= ~(termios.ECHO | termios.ECHONL)
                termios.tcsetattr(fd, termios.TCSANOW, quiet)
        print(prompt, end='', flush=True)
        value = sys.stdin.readline(limit + 1)
    finally:
        if saved is not None:
            termios.tcsetattr(fd, termios.TCSANOW, saved)
        if hidden:
            print()
    if len(value) > limit:
        raise InputTooLong()
    return value.rstrip('\n') if value else None


@contextmanager
def _deferred_signals(state):
    """Once confirmed, signals request exit after the bounded operation report."""
    requested = []
    def remember(number, frame):
        # Never raise, print, reap or perform cleanup from a signal handler.
        if not requested:
            requested.append(number)
            state['exit_signal'] = number
    previous = {}
    try:
        for number in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
            previous[number] = signal.signal(number, remember)
        yield requested
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _stage_boundary(root):
    if sys.platform != 'linux' or os.getuid() == 0 or os.geteuid() == 0:
        raise MetadataError('non-root Linux owner required')
    if root == MANAGED_ROOT and pwd.getpwnam('v-ui').pw_uid != os.geteuid():
        raise MetadataError('wrong managed installation owner')
    installation_metadata(root)
    with _directory(root, owner=os.geteuid()) as directory:
        info = os.fstat(directory)
        raw = _json_file(directory, 'CURRENT.json', os.geteuid(), raw_bytes=True)
    return info.st_dev, info.st_ino, raw


def _local_archive(value):
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise MetadataError('invalid local archive path')
    # Literal path only: no shell, URL fetch, glob, tilde or variable expansion.
    path = Path(value).absolute()
    with _directory(path.parent) as directory:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 850_000_000:
                raise MetadataError('invalid archive type or size')
        finally:
            os.close(fd)
    return path


def _stage_worker(root, archive, checksum):
    """Keep the unreaped session leader as the PID/PGID reuse guard.

    A zero exit observed before cleanup is the deploy controller's result.
    TERM/KILL afterwards is bounded residual-group cleanup, not proof that all
    possible descendants (or malicious setsid escapes / OS D-state) are gone.
    No pipe or EOF wait is involved. Never poll/wait before the final signal.
    """
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        return 'unconfirmed'
    payload = Path(__file__).absolute().parents[1]
    command = [sys.executable, '-B', str(payload / 'scripts/deploy.py'),
               '--root', str(root), 'stage', str(archive), '--sha256', checksum]
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
           'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(payload)}
    process = None
    observed = None
    owned = True
    completed = False
    timed_out = False
    cleaned = True
    try:
        # A terminal interrupt can otherwise hit the forked child before setsid.
        # The child intentionally retains INT/HUP blocked throughout preparation;
        # TERM remains available for bounded cleanup. Restore the exact parent
        # mask while our non-raising deferred handlers are still installed.
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGHUP})
        try:
            process = subprocess.Popen(command, cwd=payload, env=env, start_new_session=True,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        deadline = time.monotonic() + STAGE_TIMEOUT
        while time.monotonic() < deadline:
            observed = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if observed is not None:
                completed = True
                break
            time.sleep(.05)
        timed_out = not completed
    except ChildProcessError:
        # An auto-reaper or external wait has invalidated our PID/PGID guard.
        owned = False
        completed = False
    except (OSError, ValueError, subprocess.SubprocessError):
        completed = False
    finally:
        if process is not None:
            # SIGCHLD must be default. Also fail closed on ECHILD before each
            # group signal: an already reaped PID no longer reserves its PGID.
            for number in (signal.SIGTERM, signal.SIGKILL):
                if not owned:
                    cleaned = False
                    break
                try:
                    os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                except ChildProcessError:
                    owned = False
                    cleaned = False
                    break
                except OSError:
                    cleaned = False
                    break
                try:
                    os.killpg(process.pid, number)
                except ProcessLookupError:
                    pass
                except OSError:
                    cleaned = False
                if number == signal.SIGTERM:
                    # An exited zombie can keep killpg(..., 0) observable, so
                    # never wait for the process group to disappear.
                    deadline = time.monotonic() + STAGE_TERM_GRACE
                    while time.monotonic() < deadline:
                        time.sleep(min(.05, max(0, deadline - time.monotonic())))
            if owned:
                try:
                    process.wait(timeout=STAGE_REAP_TIMEOUT)
                except (OSError, subprocess.SubprocessError):
                    cleaned = False
    if timed_out:
        return 'timeout'
    if (completed and cleaned and observed is not None
            and observed.si_code == os.CLD_EXITED and observed.si_status == 0):
        return 'prepared'
    return 'unconfirmed'


def prepare_candidate(root, state):
    print('仅准备候选，不激活、不重启服务；活动选择、旧版本和数据不由菜单修改。\n'
          '仅使用已从独立可信官方来源核验并接受的本地包及其可信 SHA-256。\n'
          'SHA-256 不认证来源，也不是沙箱；准备会以当前用户执行包内代码。\n'
          '离线安装参数不限制任意候选代码联网；不可信包或同用户并发改动无法隔离。\n'
          '确认后最长处理 600 秒，随后 TERM 5 秒及有界 KILL/回收；中断将延后退出。')
    try:
        archive = _stage_input('本地升级包路径（留空取消）: ', STAGE_PATH_LIMIT, hidden=True)
        if archive is None or not archive.strip():
            print('已取消准备；尚未启动候选处理。')
            return 0 if archive is None else None
        checksum = _stage_input('可信 SHA-256（留空取消）: ', 65, hidden=True)
        if checksum is None or not checksum.strip():
            print('已取消准备；尚未启动候选处理。')
            return 0 if checksum is None else None
        if not re.fullmatch(r'[a-fA-F0-9]{64}', checksum):
            print('准备拒绝：需要 64 位十六进制 SHA-256。')
            return None
        answer = _stage_input('确认仅准备候选 [yes/N]: ', 16)
        if answer != 'yes':
            print('已取消准备；尚未启动候选处理。')
            return 0 if answer is None else None
    except InputTooLong:
        print('输入过长，已退出；未执行该行或其后缀。')
        return 2
    except (OSError, ValueError):
        print('准备拒绝：无法安全读取终端输入。')
        return 2
    # Cover launch, outcome inspection AND reporting with non-raising handlers.
    with _deferred_signals(state) as requested:
        try:
            before = _stage_boundary(root)
            archive = _local_archive(archive)
            # Check again immediately before allowing the existing controller
            # to create its operation lock or candidate. No atomic-tree claim.
            if before != _stage_boundary(root):
                raise MetadataError('installation changed before preparation')
        except DATA_ERRORS:
            print('准备拒绝：本地包或安装目录边界无效；尚未启动候选处理。')
        else:
            state['started'] = True
            print('正在准备候选；EOF 不终止处理，信号将在结果报告后退出。', flush=True)
            result = _stage_worker(root, archive, checksum.lower())
            succeeded = result == 'prepared'
            try:
                unchanged = before == _stage_boundary(root)
            except DATA_ERRORS:
                unchanged = False
            if succeeded and unchanged:
                print('候选已准备，尚未升级；活动选择未变。', flush=True)
            elif succeeded:
                print('候选已准备，但活动选择保留未确认；不会自动回退或激活。', flush=True)
            else:
                if result == 'timeout':
                    print('总处理时限已到；已尝试有界终止和回收，结果未确认。', flush=True)
                print('准备失败或结果未确认；可能留下锁或不完整候选，请人工检查。\n'
                      '不会自动删除未知候选、回退或激活；READY 文件本身不证明成功。', flush=True)
        if requested:
            print('已收到退出请求；候选处理结果以上述报告为准。', flush=True)
            return 128 + requested[0]
    return None


def _run_menu(root, state):

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print('运维菜单需要交互终端；未读取输入或安装信息。', file=sys.stderr)
        return 2
    if sys.platform != 'linux' or os.geteuid() == 0 or os.getuid() == 0:
        print('运维菜单须在 Linux 上以非 root 安装目录所有者运行。', file=sys.stderr)
        return 1
    root = Path(root).absolute()
    try:
        with _directory(root, owner=os.geteuid()):
            pass
        if root == MANAGED_ROOT and pwd.getpwnam('v-ui').pw_uid != os.geteuid():
            raise MetadataError('wrong managed installation owner')
    except DATA_ERRORS:
        print('运维菜单拒绝: 安装目录须已存在、无符号链接、由当前用户拥有且权限为 0700。',
              file=sys.stderr)
        return 1
    print(MENU, end='')
    while True:
        if state.get('exit_signal'):
            return 128 + state['exit_signal']
        print(PROMPT, end='', flush=True)
        choice = sys.stdin.readline(65)
        if len(choice) == 65 and not choice.endswith('\n'):
            print('\n输入过长，已退出；未执行该行或其后缀。')
            return 2
        if not choice or choice.strip() in ('0', 'q'):
            print('\n已退出。')
            return 0
        choice = choice.strip()
        if choice == '1':
            show_services(root, sys.stdout)
        elif choice == '2':
            show_metadata(root, sys.stdout)
        elif choice == '3':
            verify_active(root, sys.stdout)
        elif choice == '4':
            exit_code = prepare_candidate(root, state)
            if exit_code is not None:
                return exit_code
        elif choice.lower() == 'h':
            print('1/2/3 为只读查看；4 仅校验并准备本地候选，确认后才写入。\n'
                  '不会提权、激活、修改服务、卸载、修改防火墙/SSH、备份或恢复。\n'
                  '3 读取活动版本完整哈希；4 单独校验并准备候选；首屏不做全量校验。\n'
                  'active=运行，inactive=未运行，failed=失败，transitional=状态切换中；\n'
                  'unknown=查询未确认，inaccessible=无权查询，unavailable=无对应信息/程序。\n'
                  '完整校验依赖本地清单；同用户并发修改无法获得原子全树保证。\n'
                  '主菜单 0 退出；准备确认前空行/EOF/Ctrl-C 取消，确认后信号延后退出。')
            print(MENU, end='')
        else:
            print('无效选择；请输入 1、2、3、4、h 或 0。')


def run_menu(root):
    state = {'started': False}
    try:
        return _run_menu(root, state)
    except KeyboardInterrupt:
        print('\n已退出；此前候选处理结果以上述报告为准。' if state['started']
              else '\n已取消，未修改安装。')
        return 130
