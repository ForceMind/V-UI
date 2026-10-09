#!/usr/bin/env python3
"""Guarded Linux installer. Detect distro/libc/init/firewall before changing the host."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import http.client
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
STREAM_CHUNK = 1024 * 1024
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
        raise InstallError('A trusted 64-character SHA-256 is required')
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
                    raise InstallError('Bundle checksum mismatch; nothing was installed')
                checksum.update(chunk); snapshot.write(chunk)
        if checksum.hexdigest() != sha:
            raise InstallError('Bundle checksum mismatch; nothing was installed')
        snapshot.finish_writes()
        snapshot.seek(0)
        archive = zipfile.ZipFile(snapshot)
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if (len(names) > 10000 or len(set(names)) != len(names) or not all(safe_path(n) for n in names)
                or sum(e.file_size for e in entries) > 1_100_000_000):
            raise InstallError('Unsafe bundle paths or size')
        for entry in entries:
            if entry.is_dir() or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG) or entry.flag_bits & 1:
                raise InstallError('Bundle contains a link, directory or encrypted entry')
        if 'MANIFEST.json' not in names or archive.getinfo('MANIFEST.json').file_size > 4_000_000:
            raise InstallError('Missing or oversized manifest')
        meta = json.loads(archive.read('MANIFEST.json'))
        if (not isinstance(meta, dict) or meta.get('schema') != 1 or meta.get('kind') != 'release'
                or meta.get('platform') != 'linux-multi-cpython312'
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', meta.get('release_id', ''))):
            raise InstallError('Not a supported V-UI bundle')
        if not isinstance(meta.get('files'), dict) or set(names) != set(meta['files']) | {'MANIFEST.json'}:
            raise InstallError('Manifest file set mismatch')
        for name, info in meta['files'].items():
            if (type(info['size']) is not int or info['size'] != archive.getinfo(name).file_size
                    or info['mode'] not in (0o600, 0o700)):
                raise InstallError('Payload size or mode mismatch')
            checksum = hashlib.sha256(); size = 0
            with archive.open(name) as content:
                for chunk in iter(lambda: content.read(STREAM_CHUNK), b''):
                    size += len(chunk); checksum.update(chunk)
            if info['mode'] not in (0o600, 0o700) or size != info['size'] or checksum.hexdigest() != info['sha256']:
                raise InstallError('Payload checksum or mode mismatch')
        if platform_support.target_key() not in meta.get('targets', []):
            raise InstallError('Bundle does not contain this Linux architecture/libc target: '+platform_support.target_key())
        for name in ('scripts/deploy.py', 'app/release_tools.py', 'deploy/system_launcher.py'):
            if name not in meta['files']:
                raise InstallError('Bundle predates the managed one-command installer')
        snapshot.seek(0)
        return snapshot, archive, meta
    except Exception:
        if archive is not None: archive.close()
        snapshot.close()
        raise


def fqdn(value):
    try:
        name = value.strip().rstrip('.').encode('idna').decode('ascii').lower()
    except (AttributeError, UnicodeError):
        raise InstallError('Invalid domain') from None
    if (len(name) > 253 or '.' not in name or name.split('.')[-1].isdigit()
            or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p) for p in name.split('.'))):
        raise InstallError('Use a DNS domain without URL, path, port, IP or wildcard')
    return name


def validate_options(args):
    args.domain = fqdn(args.domain)
    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}', args.admin):
        raise InstallError('Administrator name must be 3–64 letters/digits/_.-')
    if not re.fullmatch(r'[A-Za-z0-9._%+\-]+@[^@]+', args.email or '') or len(args.email) > 254:
        raise InstallError('Invalid contact email')
    local, host = args.email.rsplit('@', 1)
    args.email = local + '@' + fqdn(host)
    if not 1024 <= args.port <= 65535:
        raise InstallError('Use an unprivileged HTTPS port in 1024–65535')
    if not 1024 <= args.node_port <= 65535:
        raise InstallError('Use an unprivileged default node port in 1024–65535')
    # Older callers construct Namespace objects without this optional field.
    udp_ports = getattr(args, 'node_udp_port', [])
    if (not isinstance(udp_ports, list)
            or any(type(port) is not int or not 1024 <= port <= 65535 for port in udp_ports)):
        raise InstallError('Use explicit unprivileged node UDP ports in 1024–65535')
    args.node_udp_port = sorted(set(udp_ports))
    ipaddress.ip_address(args.bind)
    if bool(args.cert) != bool(args.key):
        raise InstallError('Provide both --cert and --key, or neither for automatic issuance')
    if not args.cert and not args.accept_terms:
        raise InstallError('Read the CA terms and pass --accept-terms to authorize issuance')


def check_platform():
    try: info=platform_support.distro()
    except RuntimeError as exc: raise InstallError(str(exc)) from None
    if info['init'] not in ('systemd','openrc'):
        raise InstallError('Detected '+info['name']+', but no supported service manager is running (systemd or OpenRC required)')
    if info['init']=='openrc':
        daemon=shutil.which('supervise-daemon')
        if not daemon:
            raise InstallError('OpenRC detected but supervise-daemon is missing')
        help_text=subprocess.run([daemon,'--help'],capture_output=True,text=True)
        if '--capabilities' not in (help_text.stdout+help_text.stderr):
            raise InstallError('OpenRC is too old or lacks Linux capabilities support')
    return info


def own_file(path):
    return path.is_file() and not path.is_symlink() and path.stat().st_uid == 0 and not path.stat().st_mode & 0o022


def no_symlink_ancestors(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise InstallError('Refusing symbolic path: ' + str(path))


def check_reserved(existing, manager='systemd'):
    for path in (ROOT, CONFIG_DIR, CONTROL):
        no_symlink_ancestors(path)
    if not existing:
        if any(path.exists() for path in (ROOT, CONFIG_DIR, CONTROL)):
            raise InstallError('Reserved installation paths already exist without this installer marker; do not overwrite')
        try: pwd.getpwnam('v-ui')
        except KeyError: pass
        else: raise InstallError('Account v-ui already exists but is not owned by this installer')
    else:
        if not own_file(CONFIG) or json.loads(CONFIG.read_text()).get('installer') != 1:
            raise InstallError('Existing installation metadata is not root-controlled')
        account=pwd.getpwnam('v-ui')
        if ROOT.stat().st_uid != account.pw_uid or ROOT.stat().st_mode & 0o077:
            raise InstallError('Managed data root has unexpected ownership or permissions')
    for name,path in service_support.service_paths(manager).items():
        if path.exists() or path.is_symlink():
            if not existing or not own_file(path) or not path.read_text().startswith(service_support.MARKER):
                raise InstallError('An unrelated system service already uses '+name)


def check_port(port, bind='0.0.0.0', protocol='tcp'):
    if protocol not in ('tcp', 'udp'):
        raise InstallError('Port protocol must be tcp or udp')
    family = socket.AF_INET6 if ':' in bind else socket.AF_INET
    kind = socket.SOCK_DGRAM if protocol == 'udp' else socket.SOCK_STREAM
    with socket.socket(family, kind) as sock:
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        try:
            sock.bind((bind, port))
        except OSError:
            raise InstallError(f'Port {port} is already in use ({protocol}); no existing service was stopped') from None


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
        raise InstallError('No supported local user-management command (useradd/adduser)')
    return pwd.getpwnam('v-ui')


def tty_confirm(message):
    try:
        with open('/dev/tty','r+') as terminal:
            terminal.write(message+' [yes/NO]: ');terminal.flush()
            return terminal.readline().strip().lower()=='yes'
    except OSError:
        raise InstallError('Interactive confirmation is required; rerun from a terminal or use the explicit non-interactive flags') from None


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
    print('Local firewall:',info['detail'])
    print('Required inbound firewall ports:',labels(ports,udp_ports))
    if udp_ports:
        print('Explicit node UDP ports are for QUIC transport; this does not create a node or enable application UDP forwarding.')
    if args.dry_run:
        return result
    if missing or missing_udp:
        print('Local firewall changes requested:',labels(missing,missing_udp))
        choice=args.open_firewall
        if choice=='ask':
            choice='yes' if tty_confirm('Local firewall needs '+labels(missing,missing_udp)+'. Open these ports now?') else 'no'
        if choice!='yes':
            raise InstallError('Required local firewall ports remain closed: '+labels(missing,missing_udp))
        try:
            if missing:firewall_support.open_ports(info,missing)
            if missing_udp:firewall_support.open_ports(info,missing_udp,protocol='udp')
        except RuntimeError as exc:raise InstallError(str(exc)) from None
        if (any(firewall_support.port_open(info,p) is not True for p in missing)
                or any(firewall_support.port_open(info,p,protocol='udp') is not True for p in missing_udp)):
            raise InstallError('Firewall change could not be verified')
    if unknown or unknown_udp:
        print('Firewall rules or ingress zone could not be verified; automatic modification is disabled.')
        print('Open ports manually:',labels(unknown,unknown_udp))
        if not args.assume_external_ports_open and not tty_confirm('Have you manually verified and opened '+labels(unknown,unknown_udp)+' in the actual ingress firewall zones?'):
            raise InstallError('Waiting for manual firewall configuration')
    if not args.assume_external_ports_open:
        print('V-UI cannot modify cloud security groups or provider firewalls.')
        if not tty_confirm('Have you opened '+labels(ports,udp_ports)+' in the cloud/upstream firewall?'):
            raise InstallError('Waiting for cloud/upstream port configuration')
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
                        raise InstallError('HTTPS health check did not enforce administrator authentication')
                    return
        except (OSError, http.client.HTTPException) as exc:
            last = exc; time.sleep(.25)
    raise InstallError('HTTPS health verification failed: ' + str(last))


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
            raise InstallError('Changing service manager during upgrade is not automatic')
        if any(saved.get(k) != config[k] for k in ('domain','email','admin','port','bind','certificate_mode')):
            raise InstallError('Existing instance options differ; do not silently reconfigure it')
        config['ipv6']=saved.get('ipv6',ipv6)
        if not args.upgrade and saved.get('ready'):
            raise InstallError('An installation already exists. Use --upgrade with a verified package')
        if args.node_udp_port:
            print('Existing installation: node UDP bind probes are skipped to avoid conflicting with running nodes; verify node port ownership manually.')
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
            raise InstallError('Certificate inputs must be regular files')
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
        raise InstallError('Use sudo for the installer; the resulting application does not run as root')
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
    with tempfile.TemporaryDirectory(prefix='.installer-', dir=ROOT) as directory:
        work = Path(directory); os.chown(work, uid, gid)
        bundle = work / 'bundle.zip'
        snapshot.seek(0)
        with _PrivateArchiveFile(bundle.open('xb')) as output:
            shutil.copyfileobj(snapshot, output, STREAM_CHUNK)
            output.finish_writes()
        os.chmod(bundle, 0o600); os.chown(bundle, uid, gid)
        for name in ('scripts/deploy.py', 'app/release_tools.py'):
            path = work/name; path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chown(path.parent, uid, gid)
            with archive.open(name) as source, path.open('xb') as output:
                shutil.copyfileobj(source, output, STREAM_CHUNK)
            os.chmod(path, 0o600); os.chown(path, uid, gid)
        controller = work/'scripts/deploy.py'
        prepared = ROOT/'releases'/meta['release_id']/'READY.json'
        if not prepared.exists():
            as_service([sys.executable,'-B',str(controller),'--root',str(ROOT),'stage',str(bundle),'--sha256',args.sha256])
        elif json.loads(prepared.read_text()).get('archive_sha256') != args.sha256:
            raise InstallError('Prepared release has another archive digest; refusing overwrite')
        stopped = False
        try:
            if existing:
                service_support.stop(manager,[panel_name])
                service_support.stop(manager,[name for name in service_support.service_paths(manager) if name!=panel_name])
            stopped = True
            if existing and (ROOT/'data/v-ui.db').exists():
                target = ROOT/('before-upgrade-'+str(time.time_ns())+'.zip')
                as_service([sys.executable,'-B',str(controller),'--root',str(ROOT),'backup',str(target)])
                print('Private pre-update backup:', target)
            as_service([sys.executable,'-B',str(controller),'--root',str(ROOT),'activate',meta['release_id']])
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
                    as_service([sys.executable,'-B',str(controller),'--root',str(ROOT),'activate',previous])
                if was_running:
                    service_support.start(manager,[http_name,panel_name])
            raise
    print('Detected Linux:',info['name'],'|',info['arch'],info['libc'],'|',manager)
    print('Installed:',meta['release_id'])
    print('Panel:', config['origin']+'/login')
    print('Certificates:', config['origin']+'/certificates')
    print('SSH and unrelated website services were not changed. Firewall changes occur only after explicit confirmation.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle', required=True, type=Path); p.add_argument('--sha256', required=True)
    p.add_argument('--archive-temp-dir', type=Path, help='Existing disk-backed directory for the private verification snapshot (default: /var/tmp; avoid tmpfs on low-memory hosts)')
    p.add_argument('--domain'); p.add_argument('--email'); p.add_argument('--admin', default='admin')
    p.add_argument('--port',type=int,default=8443);p.add_argument('--bind',default='0.0.0.0')
    p.add_argument('--node-port',type=int,default=10443,help='Default node TCP port to preflight/open')
    p.add_argument('--node-udp-port',type=int,action='append',default=[],metavar='PORT',
                   help='Explicit node UDP/QUIC port to preflight/open; repeat for each port; does not create a node')
    p.add_argument('--open-firewall',choices=['ask','yes','no'],default='ask')
    p.add_argument('--assume-external-ports-open',action='store_true',help='Skip cloud/upstream firewall confirmation')
    p.add_argument('--accept-terms', action='store_true'); p.add_argument('--upgrade', action='store_true')
    p.add_argument('--cert'); p.add_argument('--key')
    p.add_argument('--health-ca', help='Optional private-CA trust file for provided-certificate HTTPS health verification')
    p.add_argument('--dry-run',action='store_true')
    args = p.parse_args()
    if not args.domain or not args.email:
        with open('/dev/tty', 'r+') as terminal:
            for attr, prompt in (('domain', 'Panel domain: '), ('email', 'Contact email: ')):
                if not getattr(args, attr):
                    terminal.write(prompt); terminal.flush(); setattr(args, attr, terminal.readline().strip())
            if not args.cert and not args.accept_terms:
                terminal.write('I control this domain and agree to https://letsencrypt.org/repository/ and public certificate records [yes/NO]: ')
                terminal.flush(); args.accept_terms = terminal.readline().strip() == 'yes'
    if args.health_ca and not args.cert:
        raise InstallError('--health-ca is only for an explicitly provided certificate, not ACME verification')
    if args.dry_run:
        install(args)
        return
    if os.geteuid() == 0:
        os.umask(0o077)
        with open('/run/lock/v-ui-install.lock', 'a+') as lock:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise InstallError('Another installer is running') from None
            install(args)
    else:
        if not args.dry_run: raise InstallError('Run this guarded setup with sudo')
        install(args)


if __name__ == '__main__':
    try:
        main()
    except (InstallError, OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        print('Installation stopped: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
