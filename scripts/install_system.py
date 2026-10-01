#!/usr/bin/env python3
"""Guarded Ubuntu24.04/amd64 installer. Root prepares services; application code runs as v-ui."""
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

ROOT = Path('/var/lib/v-ui')
CONFIG_DIR = Path('/etc/v-ui')
CONFIG = CONFIG_DIR / 'service.json'
CONTROL = Path('/usr/local/lib/v-ui')
MARKER = '# Managed by V-UI guarded installer v1\n'
UNIT_DIR = Path('/etc/systemd/system')
UNITS = ('v-ui.service', 'v-ui-http01.service', 'v-ui-http01.socket')
MAX_ARCHIVE = 350_000_000


class InstallError(RuntimeError):
    pass


def command(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def safe_path(name):
    return (isinstance(name, str) and 0 < len(name) < 512 and not name.startswith('/')
            and not any(ord(c) < 32 or ord(c) == 127 or c == '\\' for c in name)
            and all(part not in ('', '.', '..') for part in name.split('/')))


def verify_archive(path, sha):
    if not re.fullmatch(r'[a-f0-9]{64}', sha or ''):
        raise InstallError('A trusted 64-character SHA-256 is required')
    with Path(path).open('rb') as handle:
        raw = handle.read(MAX_ARCHIVE + 1)
    if len(raw) > MAX_ARCHIVE or hashlib.sha256(raw).hexdigest() != sha:
        raise InstallError('Bundle checksum mismatch; nothing was installed')
    archive = zipfile.ZipFile(io.BytesIO(raw))
    entries = archive.infolist()
    names = [item.filename for item in entries]
    if (len(names) > 10000 or len(set(names)) != len(names) or not all(safe_path(n) for n in names)
            or sum(e.file_size for e in entries) > 600_000_000):
        raise InstallError('Unsafe bundle paths or size')
    for entry in entries:
        if entry.is_dir() or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG) or entry.flag_bits & 1:
            raise InstallError('Bundle contains a link, directory or encrypted entry')
    if 'MANIFEST.json' not in names or archive.getinfo('MANIFEST.json').file_size > 4_000_000:
        raise InstallError('Missing or oversized manifest')
    meta = json.loads(archive.read('MANIFEST.json'))
    if (not isinstance(meta, dict) or meta.get('schema') != 1 or meta.get('kind') != 'release'
            or meta.get('platform') != 'linux-amd64-cpython312'
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', meta.get('release_id', ''))):
        raise InstallError('Not a supported V-UI bundle')
    if not isinstance(meta.get('files'), dict) or set(names) != set(meta['files']) | {'MANIFEST.json'}:
        raise InstallError('Manifest file set mismatch')
    for name, info in meta['files'].items():
        content = archive.read(name)
        if info['mode'] not in (0o600, 0o700) or len(content) != info['size'] or hashlib.sha256(content).hexdigest() != info['sha256']:
            raise InstallError('Payload checksum or mode mismatch')
    for name in ('scripts/deploy.py', 'app/release_tools.py', 'deploy/system_launcher.py'):
        if name not in meta['files']:
            raise InstallError('Bundle predates the managed one-command installer')
    return raw, archive, meta


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
    ipaddress.ip_address(args.bind)
    if bool(args.cert) != bool(args.key):
        raise InstallError('Provide both --cert and --key, or neither for automatic issuance')
    if not args.cert and not args.accept_terms:
        raise InstallError('Read the CA terms and pass --accept-terms to authorize issuance')


def check_platform():
    release = platform.freedesktop_os_release()
    if (release.get('ID') != 'ubuntu' or release.get('VERSION_ID') != '24.04'
            or platform.machine() not in ('x86_64', 'amd64')):
        raise InstallError('Only Ubuntu24.04 amd64 is supported by this installer')
    if not Path('/run/systemd/system').is_dir():
        raise InstallError('A booted systemd host is required (not an ordinary container)')


def own_file(path):
    return path.is_file() and not path.is_symlink() and path.stat().st_uid == 0 and not path.stat().st_mode & 0o022


def no_symlink_ancestors(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise InstallError('Refusing symbolic path: ' + str(path))


def check_reserved(existing):
    for path in (ROOT, CONFIG_DIR, CONTROL):
        no_symlink_ancestors(path)
    if not existing:
        if any(path.exists() for path in (ROOT, CONFIG_DIR, CONTROL)):
            raise InstallError('Reserved installation paths already exist without this installer marker; do not overwrite')
        try:
            pwd.getpwnam('v-ui')
        except KeyError:
            pass
        else:
            raise InstallError('Account v-ui already exists but is not owned by this installer')
    else:
        if not own_file(CONFIG) or json.loads(CONFIG.read_text()).get('installer') != 1:
            raise InstallError('Existing installation metadata is not root-controlled')
        account = pwd.getpwnam('v-ui')
        if ROOT.stat().st_uid != account.pw_uid or ROOT.stat().st_mode & 0o077:
            raise InstallError('Managed data root has unexpected ownership or permissions')
    for name in UNITS:
        unit = UNIT_DIR / name
        if unit.exists() or unit.is_symlink():
            if not existing or not own_file(unit) or not unit.read_text().startswith(MARKER):
                raise InstallError('An unrelated system service already uses ' + name)


def stop_existing_units(names):
    # A previous first-install attempt may have prepared no units yet.
    # Only stop our root-controlled units that actually exist.
    present = [name for name in names if (UNIT_DIR / name).is_file()]
    if present:
        command(['systemctl', 'stop', *present], stderr=subprocess.DEVNULL)


def check_port(port, bind='0.0.0.0'):
    family = socket.AF_INET6 if ':' in bind else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        try:
            sock.bind((bind, port))
        except OSError:
            raise InstallError(f'Port {port} is already in use; no existing service was stopped') from None


def probe_ipv6():
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock.bind(('::', 0))
        return True
    except OSError:
        return False


def unit_files(config):
    common = ('User=v-ui\nGroup=v-ui\nUMask=0077\nNoNewPrivileges=true\n'
              'ProtectSystem=strict\nProtectHome=true\nPrivateTmp=true\nPrivateDevices=true\n'
              'CapabilityBoundingSet=\nRestrictSUIDSGID=true\nRestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX\n')
    prefix = '/usr/bin/python3.12 -B /usr/local/lib/v-ui/launcher.py '
    panel = (MARKER + '[Unit]\nDescription=V-UI managed HTTPS panel\nAfter=network-online.target\n'
             'Wants=network-online.target\n\n[Service]\nType=simple\n' + common +
             'WorkingDirectory=/var/lib/v-ui\nReadWritePaths=/var/lib/v-ui\n'
             'ExecStart=' + prefix + 'panel\nRestart=on-failure\nRestartSec=5\nTimeoutStopSec=30\n'
             '\n[Install]\nWantedBy=multi-user.target\n')
    challenge = (MARKER + '[Unit]\nDescription=V-UI HTTP-01 challenge responder\nRequires=v-ui-http01.socket\n'
                 '\n[Service]\nType=simple\n' + common +
                 'WorkingDirectory=/var/lib/v-ui\nExecStart=' + prefix + 'http01\nTimeoutStopSec=15\n')
    socket_unit = (MARKER + '[Unit]\nDescription=V-UI ACME validation socket (not panel HTTP)\n'
                   '\n[Socket]\nListenStream=0.0.0.0:80\n' +
                   ('ListenStream=[::]:80\nBindIPv6Only=ipv6-only\n' if config['ipv6'] else '') +
                   'Accept=no\nService=v-ui-http01.service\n\n[Install]\nWantedBy=sockets.target\n')
    return dict(zip(UNITS, (panel, challenge, socket_unit)))


def write_root_file(path, raw, mode=0o644):
    no_symlink_ancestors(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    fd, name = tempfile.mkstemp(prefix='.vui-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        os.chmod(name, mode); os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def as_service(args, *, capture=False, tty=None, cwd=ROOT):
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': str(ROOT),
           'PYTHONDONTWRITEBYTECODE': '1', 'VUI_DATA_DIR': str(ROOT / 'data')}
    return command(['/usr/sbin/runuser', '-u', 'v-ui', '--', *args], cwd=cwd, env=env,
                   capture_output=capture, text=capture, stdin=tty, timeout=360)


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
    raw, archive, meta = verify_archive(args.bundle, args.sha256)
    existing = CONFIG.exists()
    check_platform()
    check_reserved(existing)
    ipv6 = probe_ipv6()
    config = {'installer': 1, 'domain': args.domain, 'email': args.email, 'admin': args.admin,
              'port': args.port, 'bind': args.bind, 'origin': f'https://{args.domain}:{args.port}',
              'certificate_mode': 'provided' if args.cert else 'managed', 'ipv6': ipv6}
    if existing:
        saved = json.loads(CONFIG.read_text())
        if any(saved[k] != config[k] for k in ('domain', 'email', 'admin', 'port', 'bind', 'certificate_mode')):
            raise InstallError('Existing instance options differ; do not silently reconfigure it')
        config['ipv6'] = saved['ipv6']
        if not args.upgrade and saved.get('ready'):
            raise InstallError('An installation already exists. Use --upgrade with a verified package')
    else:
        check_port(80)
        if ipv6: check_port(80, '::')
        check_port(args.port, args.bind)
    if args.cert:
        if Path(args.cert).is_symlink() or Path(args.key).is_symlink():
            raise InstallError('Certificate inputs must be regular files')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.cert, args.key)
    if args.dry_run:
        print(json.dumps({'checks': 'passed', 'no_changes': True, 'domain': args.domain,
              'release_id': meta['release_id'], 'services': list(UNITS), 'certificate_mode': config['certificate_mode']}, indent=2))
        return
    if os.geteuid() != 0:
        raise InstallError('Use sudo for the installer; the resulting application does not run as root')
    if not Path('/usr/bin/python3.12').exists() or subprocess.run(
            ['/usr/bin/python3.12', '-c', 'import ensurepip'], capture_output=True).returncode:
        command(['apt-get', 'update'])
        command(['apt-get', 'install', '-y', 'python3.12', 'python3.12-venv', 'ca-certificates'])
    if not existing:
        command(['/usr/sbin/useradd', '--system', '--user-group', '--home-dir', str(ROOT),
                 '--shell', '/usr/sbin/nologin', 'v-ui'])
        account = pwd.getpwnam('v-ui'); ROOT.mkdir(mode=0o700); os.chown(ROOT, account.pw_uid, account.pw_gid)
        config['ready'] = False
        write_root_file(CONFIG, json.dumps(config, indent=2).encode())
    account = pwd.getpwnam('v-ui')
    uid, gid = account.pw_uid, account.pw_gid
    previous = json.loads((ROOT/'CURRENT.json').read_text())['release_id'] if (ROOT/'CURRENT.json').exists() else None
    previous_files = {path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode), path.stat().st_uid, path.stat().st_gid)
        for path in (CONFIG, CONTROL/'launcher.py', *(UNIT_DIR/n for n in UNITS),
                     ROOT/'data/certs/provided-fullchain.pem', ROOT/'data/certs/provided-privkey.pem')
        if existing and path.is_file() and not path.is_symlink()}
    was_running = subprocess.run(['systemctl', 'is-active', '--quiet', 'v-ui.service']).returncode == 0
    # A tiny verified controller is extracted as data; it is never executed as root.
    with tempfile.TemporaryDirectory(prefix='.installer-', dir=ROOT) as directory:
        work = Path(directory); os.chown(work, uid, gid)
        bundle = work / 'bundle.zip'; bundle.write_bytes(raw); os.chmod(bundle, 0o600); os.chown(bundle, uid, gid)
        for name in ('scripts/deploy.py', 'app/release_tools.py'):
            path = work/name; path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chown(path.parent, uid, gid); path.write_bytes(archive.read(name)); os.chmod(path, 0o600); os.chown(path, uid, gid)
        controller = work/'scripts/deploy.py'
        prepared = ROOT/'releases'/meta['release_id']/'READY.json'
        if not prepared.exists():
            as_service(['/usr/bin/python3.12', '-B', str(controller), '--root', str(ROOT), 'stage', str(bundle), '--sha256', args.sha256])
        elif json.loads(prepared.read_text()).get('archive_sha256') != args.sha256:
            raise InstallError('Prepared release has another archive digest; refusing overwrite')
        stopped = False
        try:
            if existing:
                stop_existing_units(('v-ui.service',))
                stop_existing_units(('v-ui-http01.service', 'v-ui-http01.socket'))
            stopped = True
            if existing and (ROOT/'data/v-ui.db').exists():
                target = ROOT/('before-upgrade-'+str(time.time_ns())+'.zip')
                as_service(['/usr/bin/python3.12','-B',str(controller),'--root',str(ROOT),'backup',str(target)])
                print('Private pre-update backup:', target)
            as_service(['/usr/bin/python3.12','-B',str(controller),'--root',str(ROOT),'activate',meta['release_id']])
            write_root_file(CONTROL/'launcher.py', archive.read('deploy/system_launcher.py'))
            for name, value in unit_files(config).items():
                write_root_file(UNIT_DIR/name, value.encode())
            write_root_file(CONFIG, json.dumps({**config, 'ready': False}, indent=2).encode())
            command(['systemctl', 'daemon-reload'])
            command(['systemctl', 'enable', '--now', 'v-ui-http01.socket'])
            release = ROOT/'releases'/meta['release_id']; python=release/'venv/bin/python'; payload=release/'payload'
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
            command(['systemctl','enable','--now','v-ui.service'])
            https_health(config, args.health_ca)
            write_root_file(CONFIG, json.dumps({**config, 'ready': True, 'release_id': meta['release_id']}, indent=2).encode())
        except Exception:
            if stopped:
                subprocess.run(['systemctl','stop','v-ui.service'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                if existing:
                    for path, (content, mode, owner, group) in previous_files.items():
                        write_root_file(path, content, mode); os.chown(path, owner, group)
                    subprocess.run(['systemctl','daemon-reload'],check=True)
                if previous and previous != meta['release_id']:
                    as_service(['/usr/bin/python3.12','-B',str(controller),'--root',str(ROOT),'activate',previous])
                if was_running:
                    subprocess.run(['systemctl','start','v-ui-http01.socket','v-ui.service'],check=True)
            raise
    print('Installed:', meta['release_id'])
    print('Panel:', config['origin']+'/login')
    print('Certificates:', config['origin']+'/certificates')
    print('No firewall, SSH configuration or unrelated website service was changed.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle', required=True, type=Path); p.add_argument('--sha256', required=True)
    p.add_argument('--domain'); p.add_argument('--email'); p.add_argument('--admin', default='admin')
    p.add_argument('--port', type=int, default=8443); p.add_argument('--bind', default='0.0.0.0')
    p.add_argument('--accept-terms', action='store_true'); p.add_argument('--upgrade', action='store_true')
    p.add_argument('--cert'); p.add_argument('--key')
    p.add_argument('--health-ca', help='Optional private-CA trust file for provided-certificate HTTPS health verification')
    p.add_argument('--dry-run', action='store_true')
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
