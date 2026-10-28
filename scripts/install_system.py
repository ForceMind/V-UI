#!/usr/bin/env python3
"""受保护的 Linux 安装器：修改主机前检测发行版、libc、服务管理器和防火墙。"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import http.client
import io
import ipaddress
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
try:
    from . import platform_support, firewall_support, service_support
except ImportError:
    import platform_support, firewall_support, service_support

ROOT = Path('/var/lib/v-ui')
CONFIG_DIR = Path('/etc/v-ui')
CONFIG = CONFIG_DIR / 'service.json'
CONTROL = Path('/usr/local/lib/v-ui')
MARKER = '# Managed by V-UI guarded installer v1\n'
UNIT_DIR = Path('/etc/systemd/system')
UNITS = ('v-ui.service', 'v-ui-http01.service', 'v-ui-http01.socket')
MAX_ARCHIVE = 850_000_000
INSTALL_LOCK = Path('/run/lock/v-ui-install.lock')
STREAM_CHUNK = 1024 * 1024
# Complete stdlib-only dependency closure of the offline backup/restore controller.
# Every member must come from the same checksum-verified bundle, never the
# mutable installed payload. Keep the public release-kit asset set unchanged.
CONTROLLER_MEMBERS = ('scripts/deploy.py', 'app/release_tools.py', 'app/data_owner.py',
                      'app/services/proxy_store.py', 'app/services/upstream_profile.py',
                      'app/services/inbound_policy.py')
# Compatibility names for older tooling/tests; new installs select a backend dynamically.
MARKER=service_support.MARKER
UNIT_DIR=service_support.SYSTEMD_DIR
UNITS=('v-ui.service','v-ui-http01.service','v-ui-http01.socket')

def unit_files(config):
    cfg={**config,'service_manager':'systemd','bootstrap_python':config.get('bootstrap_python',str(Path(sys.executable).resolve()))}
    return {path.name:value for path,(value,mode) in service_support.service_files(cfg).items()}

def stop_existing_units(names):
    present=[name for name in names if (UNIT_DIR/name).is_file()]
    if present:command(['systemctl','stop',*present],stderr=subprocess.DEVNULL)


class InstallError(RuntimeError):
    pass


@contextmanager
def _installation_lock(path: Path, *, owner=0):
    """Share the fixed installer/controller lease without following links.

    ``owner`` is an internal fixture seam, never a command-line option. Production
    callers always use the root owner and the fixed INSTALL_LOCK. A root-owned
    sticky lock directory is allowed: it protects an existing root-owned leaf
    even on distributions whose /run/lock is mode 1777.
    """
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise InstallError('安装锁路径无效')
    parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    lock = None
    try:
        parts = path.parent.parts[1:]
        for index in range(len(parts) + 1):
            info = os.fstat(parent)
            sticky_parent = index == len(parts) and bool(info.st_mode & stat.S_ISVTX)
            if info.st_uid not in (0, owner) or (info.st_mode & 0o022 and not sticky_parent):
                raise InstallError('安装锁目录不可信')
            if index < len(parts):
                child = os.open(parts[index], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=parent)
                os.close(parent)
                parent = child
        lock = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                       0o600, dir_fd=parent)
        info = os.fstat(lock)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise InstallError('安装锁必须是所属用户的私有普通文件')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallError('另一个安装器或托管操作正在运行') from None
        named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino):
            raise InstallError('安装锁文件已发生变化')
        yield
    finally:
        if lock is not None:
            os.close(lock)
        os.close(parent)


def command(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def safe_path(name):
    return (isinstance(name, str) and 0 < len(name) < 512 and not name.startswith('/')
            and not any(ord(c) < 32 or ord(c) == 127 or c == '\\' for c in name)
            and all(part not in ('', '.', '..') for part in name.split('/')))


class _PrivateArchiveFile:
    """Bound cache for operation-owned immutable files, never live user data.

    Cache advice is optional. Writeback errors still fail the operation; an
    unsupported advisory syscall only loses the optimization. Keep this helper
    identical in the standalone installer and release controller.
    """
    def __init__(self, handle):
        self.handle = handle
        self.pending = 0
        self.read_pending = 0
        self.advice = getattr(os, 'posix_fadvise', None)
        self.dontneed = getattr(os, 'POSIX_FADV_DONTNEED', None)
        self.page_size = os.sysconf('SC_PAGESIZE')

    def __getattr__(self, name):
        return getattr(self.handle, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.handle.__exit__(*args)

    def _discard(self, offset, size):
        if self.advice is not None and self.dontneed is not None:
            try:
                self.advice(self.handle.fileno(), offset, size, self.dontneed)
            except OSError:
                # Some filesystems/kernels do not implement advisory eviction.
                pass

    def finish_writes(self):
        self.handle.flush()
        if self.pending and self.advice is not None and self.dontneed is not None:
            os.fsync(self.handle.fileno())  # Dirty pages cannot be discarded.
            self._discard(0, 0)
        self.pending = 0

    def write(self, data):
        count = self.handle.write(data)
        self.pending += count
        if self.pending >= 8 * 1024 * 1024:
            self.finish_writes()
        return count

    def read(self, size=-1):
        if size == 0:
            return self.handle.read(0)  # A zero-byte request is not EOF.
        offset = self.handle.tell()
        data = self.handle.read(size)
        # Revisit the consumed prefix in batches: a previous hint can race
        # kernel readahead/LRU insertion. Partial trailing pages are retained.
        self.read_pending += len(data)
        end = ((offset + len(data)) // self.page_size) * self.page_size
        if not data:
            self._discard(0, 0)
        elif self.read_pending >= 8 * 1024 * 1024 and end:
            self._discard(0, end)
            self.read_pending = 0
        return data


def verify_archive(path, sha, *, temp_dir=None):
    if not re.fullmatch(r'[a-f0-9]{64}', sha or ''):
        raise InstallError('需要可信的 64 位 SHA-256 校验值')
    # A private file-backed snapshot preserves digest-before-use even if the
    # caller replaces or modifies the original bundle while installation runs.
    # Select a disk-backed directory on low-memory hosts: /var/tmp can also
    # be tmpfs. The unlinked 0600 file lives until install/dry-run exits.
    snapshot = _PrivateArchiveFile(tempfile.TemporaryFile(prefix='vui-verified-', dir=temp_dir or '/var/tmp'))
    archive = None
    try:
        checksum = hashlib.sha256(); size = 0
        with Path(path).open('rb') as handle:
            while True:
                chunk = handle.read(min(STREAM_CHUNK, MAX_ARCHIVE + 1 - size))
                if not chunk: break
                size += len(chunk)
                if size > MAX_ARCHIVE:
                    raise InstallError('安装包校验值不匹配，未执行安装')
                checksum.update(chunk); snapshot.write(chunk)
        if checksum.hexdigest() != sha:
            raise InstallError('安装包校验值不匹配，未执行安装')
        snapshot.finish_writes()
        snapshot.seek(0)
        archive = zipfile.ZipFile(snapshot)
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if (len(names) > 10000 or len(set(names)) != len(names) or not all(safe_path(n) for n in names)
                or sum(e.file_size for e in entries) > 1_100_000_000):
            raise InstallError('安装包路径或大小不安全')
        for entry in entries:
            if entry.is_dir() or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG) or entry.flag_bits & 1:
                raise InstallError('安装包包含链接、目录或加密条目')
        if 'MANIFEST.json' not in names or archive.getinfo('MANIFEST.json').file_size > 4_000_000:
            raise InstallError('安装包清单缺失或过大')
        meta = json.loads(archive.read('MANIFEST.json'))
        if (not isinstance(meta, dict) or meta.get('schema') != 1 or meta.get('kind') != 'release'
                or meta.get('platform') != 'linux-multi-cpython312'
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', meta.get('release_id', ''))):
            raise InstallError('不是受支持的 V-UI 安装包')
        if not isinstance(meta.get('files'), dict) or set(names) != set(meta['files']) | {'MANIFEST.json'}:
            raise InstallError('安装包文件集合与清单不一致')
        for name, info in meta['files'].items():
            if (type(info['size']) is not int or info['size'] != archive.getinfo(name).file_size
                    or info['mode'] not in (0o600, 0o700)):
                raise InstallError('安装内容的大小或权限与清单不一致')
            checksum = hashlib.sha256(); size = 0
            with archive.open(name) as content:
                for chunk in iter(lambda: content.read(STREAM_CHUNK), b''):
                    size += len(chunk); checksum.update(chunk)
            if info['mode'] not in (0o600, 0o700) or size != info['size'] or checksum.hexdigest() != info['sha256']:
                raise InstallError('安装内容的校验值或权限与清单不一致')
        if platform_support.target_key() not in meta.get('targets', []):
            raise InstallError('安装包不包含当前 Linux 架构/libc 目标：'+platform_support.target_key())
        for name in (*CONTROLLER_MEMBERS, 'deploy/system_launcher.py'):
            if name not in meta['files']:
                raise InstallError('安装包过旧，不支持托管一键安装器')
        snapshot.seek(0)
        return snapshot, archive, meta
    except Exception:
        if archive is not None: archive.close()
        snapshot.close()
        raise


def extract_controller(archive, work, uid, gid):
    """Copy only the verified offline dependency closure, without executing it."""
    for name in CONTROLLER_MEMBERS:
        path = work / name
        parent = work
        for part in Path(name).parts[:-1]:
            parent = parent / part
            if not parent.exists():
                parent.mkdir(mode=0o700)
                os.chown(parent, uid, gid)
        with archive.open(name) as source, path.open('xb') as output:
            shutil.copyfileobj(source, output, STREAM_CHUNK)
        os.chmod(path, 0o600)
        os.chown(path, uid, gid)


def fqdn(value):
    try:
        name = value.strip().rstrip('.').encode('idna').decode('ascii').lower()
    except (AttributeError, UnicodeError):
        raise InstallError('域名无效') from None
    if (len(name) > 253 or '.' not in name or name.split('.')[-1].isdigit()
            or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p) for p in name.split('.'))):
        raise InstallError('请输入 DNS 域名，不要包含 URL、路径、端口、IP 或通配符')
    return name


def validate_options(args):
    args.domain = fqdn(args.domain)
    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}', args.admin):
        raise InstallError('管理员用户名须为 3–64 位字母、数字或 _.-')
    if not re.fullmatch(r'[A-Za-z0-9._%+\-]+@[^@]+', args.email or '') or len(args.email) > 254:
        raise InstallError('联系邮箱无效')
    local, host = args.email.rsplit('@', 1)
    args.email = local + '@' + fqdn(host)
    if not 1024 <= args.port <= 65535:
        raise InstallError('HTTPS 端口须为 1024–65535 范围内的非特权端口')
    if not 1024 <= args.node_port <= 65535:
        raise InstallError('默认节点端口须为 1024–65535 范围内的非特权端口')
    # Older callers construct Namespace objects without this optional field.
    udp_ports = getattr(args, 'node_udp_port', [])
    if (not isinstance(udp_ports, list)
            or any(type(port) is not int or not 1024 <= port <= 65535 for port in udp_ports)):
        raise InstallError('请显式指定 1024–65535 范围内的节点 UDP 非特权端口')
    args.node_udp_port = sorted(set(udp_ports))
    ipaddress.ip_address(args.bind)
    if bool(args.cert) != bool(args.key):
        raise InstallError('请同时提供 --cert 和 --key；自动签发时两项均不提供')
    if not args.cert and not args.accept_terms:
        raise InstallError('请阅读 CA 条款并明确同意后，使用 --accept-terms 授权签发')


def check_platform():
    try: info=platform_support.distro()
    except RuntimeError as exc: raise InstallError('平台检测失败：'+str(exc)) from None
    if info['init'] not in ('systemd','openrc'):
        raise InstallError('检测到 '+info['name']+'，但未运行受支持的服务管理器（需要 systemd 或 OpenRC）')
    if info['init']=='openrc':
        daemon=shutil.which('supervise-daemon')
        if not daemon:
            raise InstallError('检测到 OpenRC，但缺少 supervise-daemon')
        help_text=subprocess.run([daemon,'--help'],capture_output=True,text=True)
        if '--capabilities' not in (help_text.stdout+help_text.stderr):
            raise InstallError('OpenRC 版本过旧或不支持 Linux capabilities')
    return info


def own_file(path):
    return path.is_file() and not path.is_symlink() and path.stat().st_uid == 0 and not path.stat().st_mode & 0o022


def no_symlink_ancestors(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise InstallError('拒绝使用符号链接路径：' + str(path))


def check_reserved(existing, manager='systemd'):
    for path in (ROOT, CONFIG_DIR, CONTROL):
        no_symlink_ancestors(path)
    if not existing:
        if any(path.exists() for path in (ROOT, CONFIG_DIR, CONTROL)):
            raise InstallError('安装专用路径已存在，但没有本安装器标记，拒绝覆盖')
        try: pwd.getpwnam('v-ui')
        except KeyError: pass
        else: raise InstallError('v-ui 账户已存在，且不属于本安装器')
    else:
        if not own_file(CONFIG) or json.loads(CONFIG.read_text()).get('installer') != 1:
            raise InstallError('现有安装元数据不受 root 安全控制')
        account=pwd.getpwnam('v-ui')
        if ROOT.stat().st_uid != account.pw_uid or ROOT.stat().st_mode & 0o077:
            raise InstallError('托管数据根目录的所有者或权限不符合要求')
    for name,path in service_support.service_paths(manager).items():
        if path.exists() or path.is_symlink():
            if not existing or not own_file(path) or not path.read_text().startswith(service_support.MARKER):
                raise InstallError('已有无关系统服务使用名称：'+name)


def check_port(port, bind='0.0.0.0', protocol='tcp'):
    if protocol not in ('tcp', 'udp'):
        raise InstallError('端口协议必须为 tcp 或 udp')
    family = socket.AF_INET6 if ':' in bind else socket.AF_INET
    kind = socket.SOCK_DGRAM if protocol == 'udp' else socket.SOCK_STREAM
    with socket.socket(family, kind) as sock:
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        try:
            sock.bind((bind, port))
        except OSError:
            raise InstallError(f'端口 {port}/{protocol} 已被占用；未停止任何现有服务') from None


def probe_ipv6():
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock.bind(('::', 0))
        return True
    except OSError:
        return False


def write_root_file(path, raw, mode=0o644):
    no_symlink_ancestors(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    # The installer umask is 077. Only these public, root-controlled parents
    # must be traversable by the service account; private data stays 0700.
    if path.parent in (CONFIG_DIR, CONTROL):
        path.parent.chmod(0o755)
    fd, name = tempfile.mkstemp(prefix='.vui-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            if hasattr(raw, 'read'):
                shutil.copyfileobj(raw, handle, STREAM_CHUNK)
            else:
                handle.write(raw)
            handle.flush(); os.fsync(handle.fileno())
        os.chmod(name, mode); os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def as_service(args, *, capture=False, tty=None, cwd=ROOT):
    account=pwd.getpwnam('v-ui')
    env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','HOME':str(ROOT),
         'PYTHONDONTWRITEBYTECODE':'1','VUI_DATA_DIR':str(ROOT/'data')}
    def demote():
        os.setgroups([])
        os.setgid(account.pw_gid);os.setuid(account.pw_uid);os.umask(0o077)
    return command(args,cwd=cwd,env=env,capture_output=capture,text=capture,stdin=tty,
                   timeout=360,preexec_fn=demote)


def create_service_user():
    try:return pwd.getpwnam('v-ui')
    except KeyError:pass
    if shutil.which('useradd'):
        command(['useradd','--system','--user-group','--home-dir',str(ROOT),'--shell',
                 '/usr/sbin/nologin' if Path('/usr/sbin/nologin').exists() else '/sbin/nologin','v-ui'])
    elif shutil.which('adduser') and shutil.which('addgroup'):
        command(['addgroup','-S','v-ui'])
        command(['adduser','-S','-D','-H','-h',str(ROOT),'-s','/sbin/nologin','-G','v-ui','v-ui'])
    else:
        raise InstallError('未找到受支持的本机用户管理命令（useradd/adduser）')
    return pwd.getpwnam('v-ui')


@contextmanager
def controlling_terminal():
    # BufferedRandom (open with text r+) requires a seekable stream. A terminal
    # is not seekable; wrap unbuffered FileIO as getpass does instead.
    with open('/dev/tty', 'rb+', buffering=0) as raw:
        with io.TextIOWrapper(raw, encoding='utf-8', write_through=True) as terminal:
            yield terminal


MAX_TERMINAL_INPUT = 1024


def read_terminal_line(terminal, prompt, *, retry_overlong=False):
    """Bound each read; only domain entry may discard a long line and retry."""
    def read_chunk():
        try:
            value = terminal.readline(MAX_TERMINAL_INPUT + 2)
        except (EOFError, KeyboardInterrupt):
            raise InstallError('安装已取消，尚未完成确认。') from None
        if not value:
            raise InstallError('安装已取消，终端输入已结束。')
        return value

    while True:
        terminal.write(prompt)
        terminal.flush()
        value = read_chunk()
        content = value[:-2] if value.endswith('\r\n') else value.removesuffix('\n')
        if len(content) <= MAX_TERMINAL_INPUT:
            return value.strip()
        if not retry_overlong:
            # Permission prompts never accept a truncated prefix or reuse its tail.
            raise InstallError('输入过长，安装已取消；每行最多 1024 个字符。')
        # Discard the entire physical domain line in bounded chunks. A valid-
        # looking suffix cannot become a new domain or answer another prompt.
        while not value.endswith('\n'):
            value = read_chunk()
        terminal.write('输入过长，每行最多 1024 个字符，请重新输入。\n')
        terminal.flush()


def terminal_confirm(terminal, message):
    while True:
        answer = read_terminal_line(terminal, message + ' [y/N，回车默认否]: ').lower()
        if answer in ('y', 'yes'):
            return True
        if answer in ('', 'n', 'no'):
            return False
        terminal.write('请输入 y/yes 或 n/no；回车默认否。\n')
        terminal.flush()


def tty_confirm(message):
    try:
        with controlling_terminal() as terminal:
            return terminal_confirm(terminal, message)
    except OSError:
        raise InstallError('需要交互确认；请在终端重新运行，或使用明确的非交互参数。') from None


def firewall_description(info):
    """Translate presentation only; backend IDs and detection detail stay intact."""
    detail = info['detail']
    messages = {
        'UFW active': 'UFW 已启用',
        'firewalld ingress zone is ambiguous or unavailable; manual configuration required':
            '无法唯一确定 firewalld 入站区域，需要手动配置',
        'custom nftables rules detected': '检测到自定义 nftables 规则',
        'custom iptables rules detected': '检测到自定义 iptables 规则',
        'no active supported local firewall detected': '未检测到已启用且受支持的本机防火墙',
    }
    prefix = 'firewalld active interface zone: '
    if detail.startswith(prefix):
        return 'firewalld 当前接口区域：' + detail[len(prefix):]
    return messages.get(detail, detail)


def firewall_preflight(args, ports, udp_ports=None):
    ports=sorted(set(ports))
    udp_ports=sorted(set(udp_ports or []))
    info=firewall_support.detect()
    statuses={port:firewall_support.port_open(info,port) for port in ports}
    udp_statuses={port:firewall_support.port_open(info,port,protocol='udp') for port in udp_ports}
    missing=[port for port,value in statuses.items() if value is False]
    unknown=[port for port,value in statuses.items() if value is None]
    missing_udp=[port for port,value in udp_statuses.items() if value is False]
    unknown_udp=[port for port,value in udp_statuses.items() if value is None]
    def labels(tcp, udp):
        return ', '.join([str(port)+'/tcp' for port in tcp]+[str(port)+'/udp' for port in udp])
    result={'backend':info['backend'],'missing':missing,'unknown':unknown,
            'tcp_ports':ports,'udp_ports':udp_ports,'missing_udp':missing_udp,'unknown_udp':unknown_udp}
    print('本机防火墙：',firewall_description(info))
    print('需要放行的入站端口：',labels(ports,udp_ports))
    if udp_ports:
        print('显式节点 UDP 端口用于 QUIC 传输；此操作不会创建节点，也不会启用应用 UDP 转发。')
    if args.dry_run:
        return result
    if missing or missing_udp:
        print('申请修改本机防火墙，放行：',labels(missing,missing_udp))
        choice=args.open_firewall
        if choice=='ask':
            choice='yes' if tty_confirm('本机防火墙需要放行 '+labels(missing,missing_udp)+'，现在放行这些端口吗？') else 'no'
        if choice!='yes':
            raise InstallError('必要的本机防火墙端口仍未放行：'+labels(missing,missing_udp))
        try:
            if missing:firewall_support.open_ports(info,missing)
            if missing_udp:firewall_support.open_ports(info,missing_udp,protocol='udp')
        except RuntimeError as exc:raise InstallError('防火墙修改失败：'+str(exc)) from None
        if (any(firewall_support.port_open(info,p) is not True for p in missing)
                or any(firewall_support.port_open(info,p,protocol='udp') is not True for p in missing_udp)):
            raise InstallError('无法验证防火墙修改结果')
    if unknown or unknown_udp:
        print('无法验证防火墙规则或实际入站区域，已禁用自动修改。')
        print('请手动放行端口：',labels(unknown,unknown_udp))
        if not args.assume_external_ports_open and not tty_confirm('你是否已在实际入站防火墙区域中手动核对并放行 '+labels(unknown,unknown_udp)+'？'):
            raise InstallError('等待手动配置本机防火墙')
    if not args.assume_external_ports_open:
        print('V-UI 无法修改云安全组或服务商防火墙。')
        if not tty_confirm('你是否已在云安全组或上游防火墙中放行 '+labels(ports,udp_ports)+'？'):
            raise InstallError('等待配置云安全组或上游防火墙端口')
    return result


def copy_private(source, target, uid, gid):
    no_symlink_ancestors(target)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chown(target.parent, uid, gid)
    fd, name = tempfile.mkstemp(prefix='.copy-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as out, Path(source).open('rb') as inp:
            shutil.copyfileobj(inp, out); out.flush(); os.fsync(out.fileno())
        os.chown(name, uid, gid); os.chmod(name, 0o600); os.replace(name, target)
    finally:
        if os.path.exists(name): os.unlink(name)


def https_health(config, cafile=None):
    # Connect locally, verify the real hostname and chain; never use -k.
    context = ssl.create_default_context(cafile=cafile)
    address = '::1' if ':' in config['bind'] else '127.0.0.1'
    last = None
    for _ in range(40):
        try:
            with socket.create_connection((address, config['port']), timeout=2) as sock:
                with context.wrap_socket(sock, server_hostname=config['domain']) as tls:
                    tls.sendall(('GET /api/auth/me HTTP/1.0\r\nHost: '+config['domain']+':'+str(config['port'])+'\r\n\r\n').encode())
                    response = http.client.HTTPResponse(tls); response.begin()
                    if response.status != 401:
                        raise InstallError('HTTPS 健康检查发现管理员认证未被强制执行')
                    return
        except (OSError, http.client.HTTPException) as exc:
            last = exc; time.sleep(.25)
    raise InstallError('HTTPS 健康验证失败：' + str(last))


def install(args):
    validate_options(args)
    snapshot, archive, meta = verify_archive(args.bundle, args.sha256, temp_dir=getattr(args, 'archive_temp_dir', None))
    with snapshot, archive:
        return install_verified(args, snapshot, archive, meta)


def install_verified(args, snapshot, archive, meta):
    existing=CONFIG.exists()
    info=check_platform()
    manager=info['init']
    check_reserved(existing,manager)
    ipv6=probe_ipv6()
    config = {'installer': 1, 'domain': args.domain, 'email': args.email, 'admin': args.admin,
              'port': args.port, 'bind': args.bind, 'origin': f'https://{args.domain}:{args.port}',
              'certificate_mode':'provided' if args.cert else 'managed','ipv6':ipv6,
              'service_manager':manager,'distro':info['name'],'target':info['target'],
              'bootstrap_python':str(Path(sys.executable).resolve()),'node_port':args.node_port}
    if existing:
        saved = json.loads(CONFIG.read_text())
        if saved.get('service_manager','systemd') != manager:
            raise InstallError('升级时不支持自动更换服务管理器')
        if any(saved.get(k) != config[k] for k in ('domain','email','admin','port','bind','certificate_mode')):
            raise InstallError('现有实例配置与参数不一致，拒绝静默修改')
        config['ipv6']=saved.get('ipv6',ipv6)
        if not args.upgrade and saved.get('ready'):
            raise InstallError('安装已存在，请使用校验通过的安装包和 --upgrade 升级')
        if args.node_udp_port:
            print('现有安装：已跳过节点 UDP 绑定探测，避免与运行中节点冲突；请手动核对节点端口归属。')
    else:
        check_port(80)
        if ipv6: check_port(80,'::')
        check_port(args.port,args.bind)
        if args.node_port not in (80,args.port):check_port(args.node_port,'0.0.0.0')
        # The default sing-box inbound listens on ::. Its IPv6-only conflicts
        # are invisible to an IPv4 probe, on both systemd and OpenRC hosts.
        if ipv6: check_port(args.node_port,'::')
        # UDP and TCP can share a number, but each needs its own bind probe.
        # Do not infer QUIC ports from the default TCP node port.
        for port in args.node_udp_port:
            check_port(port,'0.0.0.0',protocol='udp')
            if ipv6:check_port(port,'::',protocol='udp')
    if args.cert:
        if Path(args.cert).is_symlink() or Path(args.key).is_symlink():
            raise InstallError('证书输入必须为普通文件')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.cert, args.key)
    firewall=firewall_preflight(args,sorted(set([80,args.port,args.node_port])),args.node_udp_port)
    if args.dry_run:
        print(json.dumps({'checks':'passed','no_changes':True,'domain':args.domain,
              'release_id':meta['release_id'],'distro':info,'service_manager':manager,
              'services':list(service_support.service_paths(manager)),'firewall':firewall,
              'certificate_mode':config['certificate_mode']},indent=2))
        return
    if os.geteuid() != 0:
        raise InstallError('请使用 sudo 运行安装器；安装后的应用不会以 root 运行')
    if not existing:
        account=create_service_user();ROOT.mkdir(mode=0o700);os.chown(ROOT,account.pw_uid,account.pw_gid)
        config['ready'] = False
        write_root_file(CONFIG, json.dumps(config, indent=2).encode())
    account=pwd.getpwnam('v-ui')
    uid, gid = account.pw_uid, account.pw_gid
    previous = json.loads((ROOT/'CURRENT.json').read_text())['release_id'] if (ROOT/'CURRENT.json').exists() else None
    managed_paths=list(service_support.service_paths(manager).values())
    previous_files={path:(path.read_bytes(),stat.S_IMODE(path.stat().st_mode),path.stat().st_uid,path.stat().st_gid)
        for path in (CONFIG,CONTROL/'launcher.py',*managed_paths,
                     ROOT/'data/certs/provided-fullchain.pem',ROOT/'data/certs/provided-privkey.pem')
        if existing and path.is_file() and not path.is_symlink()}
    panel_name='v-ui.service' if manager=='systemd' else 'v-ui'
    http_name='v-ui-http01.socket' if manager=='systemd' else 'v-ui-http01'
    was_running=service_support.is_active(manager,panel_name)
    # A tiny verified controller is extracted as data; it is never executed as root.
    # Its complete stdlib closure runs with isolated/no-site Python, so cwd,
    # PYTHONPATH and service-user site hooks cannot supply its dependencies.
    with tempfile.TemporaryDirectory(prefix='.installer-', dir=ROOT) as directory:
        work = Path(directory); os.chown(work, uid, gid)
        bundle = work / 'bundle.zip'
        snapshot.seek(0)
        with _PrivateArchiveFile(bundle.open('xb')) as output:
            shutil.copyfileobj(snapshot, output, STREAM_CHUNK)
            output.finish_writes()
        os.chmod(bundle, 0o600); os.chown(bundle, uid, gid)
        extract_controller(archive, work, uid, gid)
        controller = work/'scripts/deploy.py'
        prepared = ROOT/'releases'/meta['release_id']/'READY.json'
        if not prepared.exists():
            as_service([sys.executable,'-B','-I','-S',str(controller),'--root',str(ROOT),'stage',str(bundle),'--sha256',args.sha256])
        elif json.loads(prepared.read_text()).get('archive_sha256') != args.sha256:
            raise InstallError('已准备的版本对应另一安装包校验值，拒绝覆盖')
        stopped = False
        try:
            if existing:
                service_support.stop(manager,[panel_name])
                service_support.stop(manager,[name for name in service_support.service_paths(manager) if name!=panel_name])
            stopped = True
            if existing and (ROOT/'data/v-ui.db').exists():
                target = ROOT/('before-upgrade-'+str(time.time_ns())+'.zip')
                as_service([sys.executable,'-B','-I','-S',str(controller),'--root',str(ROOT),'backup',str(target)])
                print('升级前私有备份：', target)
            as_service([sys.executable,'-B','-I','-S',str(controller),'--root',str(ROOT),'activate',meta['release_id']])
            release=ROOT/'releases'/meta['release_id']
            runtime_python=release/'runtime/python/bin/python3'
            config['bootstrap_python']=str(runtime_python)
            with archive.open('deploy/system_launcher.py') as source:
                write_root_file(CONTROL/'launcher.py', source)
            for path,(value,mode) in service_support.service_files(config).items():
                write_root_file(path,value.encode(),mode)
            write_root_file(CONFIG, json.dumps({**config, 'ready': False}, indent=2).encode())
            service_support.reload(manager)
            service_support.enable_start(manager,[http_name])
            release=ROOT/'releases'/meta['release_id'];python=runtime_python;payload=release/'payload'
            if args.cert:
                copy_private(args.cert, ROOT/'data/certs/provided-fullchain.pem', uid, gid)
                copy_private(args.key, ROOT/'data/certs/provided-privkey.pem', uid, gid)
            else:
                as_service([str(python),'-B','-m','app.certificates.cli','bootstrap','--root',str(ROOT),
                            '--domain',args.domain,'--email',args.email,'--accept-terms'], cwd=payload)
            check = as_service([str(python),'-B','-c',
                'from app.models.database import init_db,SessionLocal,User; init_db(); s=SessionLocal(); print(s.query(User).count()); s.close()'], capture=True,cwd=payload)
            if check.stdout.strip() == '0':
                # /dev/tty keeps piped installer input out of the password path.
                with open('/dev/tty', 'rb+', buffering=0) as tty:
                    as_service([str(python),'-B','-m','app.admin','create',args.admin],tty=tty,cwd=payload)
            service_support.enable_start(manager,[panel_name])
            https_health(config, args.health_ca)
            write_root_file(CONFIG, json.dumps({**config, 'ready': True, 'release_id': meta['release_id']}, indent=2).encode())
        except Exception:
            if stopped:
                try:service_support.stop(manager,[panel_name])
                except Exception:pass
                if existing:
                    for path, (content, mode, owner, group) in previous_files.items():
                        write_root_file(path, content, mode); os.chown(path, owner, group)
                    service_support.reload(manager)
                if previous and previous != meta['release_id']:
                    as_service([sys.executable,'-B','-I','-S',str(controller),'--root',str(ROOT),'activate',previous])
                if was_running:
                    service_support.start(manager,[http_name,panel_name])
            raise
    print('检测到 Linux：',info['name'],'|',info['arch'],info['libc'],'|',manager)
    print('安装完成，面板登录及证书管理地址如下：')
    print('Installed:',meta['release_id'])
    print('Panel:', config['origin']+'/login')
    print('Certificates:', config['origin']+'/certificates')
    print('SSH 和无关网站服务未被修改。防火墙仅在明确确认后修改。')


def read_configuration(args):
    """Read missing first-install choices from the controlling terminal only."""
    interactive = getattr(args, 'interactive', False)
    # Explicit values are authoritative and still fail strictly. Validate before
    # asking for an email or CA consent, including in an interactive invocation.
    if args.domain:
        args.domain = fqdn(args.domain)
    if interactive or not args.domain or not args.email:
        with controlling_terminal() as terminal:
            def ask(prompt, default=None, *, retry_overlong=False):
                value = read_terminal_line(terminal, prompt, retry_overlong=retry_overlong)
                return value if value else default

            if not args.domain:
                while True:
                    value = ask('面板域名（例如 panel.example.com）: ', retry_overlong=True)
                    try:
                        args.domain = fqdn(value)
                    except InstallError as exc:
                        terminal.write(str(exc) + '，请重新输入。\n')
                        terminal.flush()
                    else:
                        break
            if not args.email:
                args.email = ask('联系邮箱: ')
            if interactive:
                if args.admin is None:
                    args.admin = ask('管理员用户名 [admin]: ', 'admin')
                for attr, prompt, default in (
                        ('port', '面板 HTTPS 端口 [8443]: ', 8443),
                        ('node_port', '默认节点 TCP 端口 [10443]: ', 10443)):
                    if getattr(args, attr) is None:
                        value = ask(prompt, str(default))
                        if not value.isascii() or not value.isdecimal() or not 1024 <= int(value) <= 65535:
                            raise InstallError('请使用 1024–65535 范围内的非特权端口')
                        setattr(args, attr, int(value))
            if not args.cert and not args.accept_terms:
                args.accept_terms = terminal_confirm(terminal,
                    '我控制该域名，并同意 https://letsencrypt.org/repository/ 的 CA 条款及公开证书记录')
    for attr, default in (('admin', 'admin'), ('port', 8443), ('node_port', 10443)):
        if getattr(args, attr) is None:
            setattr(args, attr, default)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle', required=True, type=Path); p.add_argument('--sha256', required=True)
    p.add_argument('--archive-temp-dir', type=Path, help='私有校验快照使用的现有磁盘目录（默认 /var/tmp；低内存主机请避免 tmpfs）')
    p.add_argument('--domain'); p.add_argument('--email'); p.add_argument('--admin')
    p.add_argument('--port',type=int);p.add_argument('--bind',default='0.0.0.0')
    p.add_argument('--node-port',type=int,help='需要预检或放行的默认节点 TCP 端口')
    p.add_argument('--interactive',action='store_true',help='交互填写缺失的域名、邮箱、管理员用户名和端口')
    p.add_argument('--node-udp-port',type=int,action='append',default=[],metavar='PORT',
                   help='显式预检或放行的节点 UDP/QUIC 端口，可重复指定；不会创建节点')
    p.add_argument('--open-firewall',choices=['ask','yes','no'],default='ask')
    p.add_argument('--assume-external-ports-open',action='store_true',help='显式确认云安全组和上游防火墙已放行，跳过交互确认')
    p.add_argument('--accept-terms', action='store_true'); p.add_argument('--upgrade', action='store_true')
    p.add_argument('--cert'); p.add_argument('--key')
    p.add_argument('--health-ca', help='可选的私有 CA 信任文件，仅用于自带证书的 HTTPS 健康验证')
    p.add_argument('--dry-run',action='store_true')
    args = p.parse_args()
    read_configuration(args)
    if args.health_ca and not args.cert:
        raise InstallError('--health-ca 仅用于显式提供的证书，不用于 ACME 验证')
    if args.dry_run:
        install(args)
        return
    if os.geteuid() == 0:
        os.umask(0o077)
        with _installation_lock(INSTALL_LOCK):
            install(args)
    else:
        if not args.dry_run: raise InstallError('请使用 sudo 运行此受保护安装器')
        install(args)


if __name__ == '__main__':
    try:
        main()
    except (InstallError, OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        print('安装已停止：' + str(exc), file=sys.stderr)
        raise SystemExit(1)
