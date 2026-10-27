"""Real PTY consumer checks for the installed menu, reused by all four targets."""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import signal
import socket
import ssl
import stat
import subprocess
import termios
import tempfile
import threading
import time
from urllib.error import URLError
from urllib.request import HTTPSHandler, ProxyHandler, build_opener
import zipfile

PROMPT = '请选择 [1/2/3/4/h/0]: '.encode()
ARCHIVE_PROMPT = '本地升级包路径（留空取消）: '
CHECKSUM_PROMPT = '可信 SHA-256（留空取消）: '
CONFIRM_PROMPT = '确认仅准备候选 [yes/N]: '
STAGE_SUCCESS = '候选已准备，尚未升级'
STAGE_FAILURE = '准备失败或结果未确认'
STAGE_CANCEL = '已取消准备；尚未启动候选处理'


class MenuSession:
    """A controlling terminal: raw ^C/^D exercise the real line discipline."""

    def __init__(self, command, *, cwd, env, timeout=60):
        master, slave = pty.openpty()
        self.fd = master
        self.output = bytearray()
        self.cursor = 0
        self.timeout = timeout
        def terminal():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        try:
            self.process = subprocess.Popen(command, cwd=cwd, env=env, stdin=slave,
                                            stdout=slave, stderr=slave, preexec_fn=terminal)
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        if self.process.poll() is None:
            # Give the menu a chance to reap its separate stage process group.
            os.killpg(self.process.pid, signal.SIGINT)
            try:
                self.process.wait(timeout=620 if self.timeout > 60 else 12)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait()
        os.close(self.fd)

    def _read(self, timeout):
        if select.select([self.fd], [], [], timeout)[0]:
            try:
                chunk = os.read(self.fd, 65536)
            except OSError as exc:
                if exc.errno != errno.EIO:
                    raise
                chunk = b''
            self.output.extend(chunk)
            if len(self.output) > 512 * 1024:
                raise AssertionError('Menu output exceeded smoke bound')
            return bool(chunk)
        return True

    def expect(self, value=PROMPT):
        value = value.encode() if isinstance(value, str) else value
        deadline = time.monotonic() + self.timeout
        while True:
            at = self.output.find(value, self.cursor)
            if at >= 0:
                chunk = bytes(self.output[self.cursor:at + len(value)]).decode(errors='replace')
                self.cursor = at + len(value)
                return chunk
            if time.monotonic() >= deadline or not self._read(.1):
                raise AssertionError('Menu did not produce expected output: ' + repr(value)
                                     + '\n' + self.text())

    def send(self, value):
        os.write(self.fd, value.encode() if isinstance(value, str) else value)

    def failure_diagnostics(self):
        """Best-effort, non-atomic snapshot of this fixture's PTY/child only.

        Never read command lines, environment, input paths or unrelated processes.
        Missing observations do not suppress the original assertion or retry it.
        """
        result = {'pid': self.process.pid}
        for name, read in (
                ('foreground_pgid', lambda: os.tcgetpgrp(self.fd)),
                ('child_pgid', lambda: os.getpgid(self.process.pid)),
                ('child_sid', lambda: os.getsid(self.process.pid))):
            try:
                result[name] = read()
            except (OSError, ValueError):
                result[name] = 'unavailable'
        try:
            attrs = termios.tcgetattr(self.fd)
            intr = attrs[6][termios.VINTR]
            result['terminal'] = {
                'isig': bool(attrs[3] & termios.ISIG),
                'icanon': bool(attrs[3] & termios.ICANON),
                'echo': bool(attrs[3] & termios.ECHO),
                'vintr': intr if isinstance(intr, int) else ord(intr),
            }
        except (OSError, ValueError, TypeError, IndexError, termios.error):
            result['terminal'] = 'unavailable'
        try:
            # Only the child created by this MenuSession; no /proc scan.
            status = Path(f'/proc/{self.process.pid}/status').read_text()
            selected = {}
            for line in status.splitlines():
                key, sep, value = line.partition(':')
                value = value.strip()
                if sep and key in ('SigPnd', 'ShdPnd', 'SigBlk', 'SigIgn', 'SigCgt'):
                    if 1 <= len(value) <= 16 and all(c in '0123456789abcdefABCDEF' for c in value):
                        selected[key] = value.lower()
                elif sep and key == 'State' and value and value[0] in 'RSDZTWtXxIKP':
                    selected[key] = value[0]
            result['child_status'] = selected
        except (OSError, UnicodeError, ValueError):
            result['child_status'] = 'unavailable'
        return '\nPTY failure diagnostics (non-atomic): ' + json.dumps(result, sort_keys=True)

    def finish(self, code=0):
        deadline = time.monotonic() + self.timeout
        while self.process.poll() is None:
            if time.monotonic() >= deadline:
                raise AssertionError('Menu did not exit\n' + self.text() + self.failure_diagnostics())
            self._read(.1)
        while self._read(.02) and select.select([self.fd], [], [], 0)[0]:
            pass
        if self.process.returncode != code:
            raise AssertionError('Unexpected menu exit code: ' + str(self.process.returncode)
                                 + '\n' + self.text())
        if 'Traceback' in self.text():
            raise AssertionError('Menu emitted a traceback\n' + self.text())
        return self.text()

    def text(self):
        return bytes(self.output).decode(errors='replace')


def tree_snapshot(root):
    """No access-time assertion; reading is allowed. Detect writes/extra entries."""
    values = {}
    for path in [root, *sorted(root.rglob('*'))]:
        info = path.lstat()
        values[str(path.relative_to(root))] = (stat.S_IFMT(info.st_mode),
            stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid, info.st_size,
            info.st_mtime_ns, os.readlink(path) if path.is_symlink() else None)
    return values


def installed_menu_smoke(python, payload, root):
    """Use package Python + package controller, with no checkout on sys.path."""
    command = [str(python), '-B', str(payload / 'scripts/deploy.py'), '--root', str(root), 'menu']
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
           'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(payload)}
    initial = tree_snapshot(root)
    with MenuSession(command, cwd=payload, env=env) as session:
        first = session.expect()
        assert 'V-UI 运维菜单' in first and '完整校验通过' not in first, first
        assert tree_snapshot(root) == initial, 'First screen wrote to the installation'
        for choice, expected in (('bad', '无效选择'), ('h', '同用户并发修改'),
                                 ('2', '活动版本:'), ('1', '服务状态: unavailable'),
                                 ('2', '尚未执行完整性校验')):
            session.send(choice + '\n')
            text = session.expect()
            assert expected in text, text
        assert tree_snapshot(root) == initial, 'Read-only actions wrote to the installation'
        session.send('3\n')
        text = session.expect()
        assert '完整校验通过' in text, text
        session.send('0\n')
        session.finish()
    assert tree_snapshot(root) == initial, 'Full verification wrote to the installation'
    for key, code in ((b'\x04', 0), (b'\x03', 130)):
        with MenuSession(command, cwd=payload, env=env) as session:
            session.expect()
            session.send(key)
            session.finish(code)
    nonterminal = subprocess.run(command, input='3\n', capture_output=True, text=True,
                                 cwd=payload, env=env, timeout=15)
    assert nonterminal.returncode == 2 and '需要交互终端' in nonterminal.stderr, nonterminal
    assert tree_snapshot(root) == initial, 'Exit/non-TTY paths wrote to the installation'
    with MenuSession(command, cwd=payload, env=env) as session:
        session.expect()
        session.send('x' * 65 + '3\n')
        text = session.finish(2)
        assert '输入过长' in text and '正在完整校验' not in text, text
    absent = root / 'must-not-create-menu-root'
    missing = [str(python), '-B', str(payload / 'scripts/deploy.py'), '--root', str(absent), 'menu']
    with MenuSession(missing, cwd=payload, env=env) as session:
        session.finish(1)
    assert not absent.exists(), 'Menu created a missing root'

    # The actual package entrypoint is also run with in-process deny probes.
    # These are test-only instrumentation, never production environment hooks.
    guard = ('import runpy,sys;from app import release_tools,ops_menu;'
             'deny=lambda *a,**k:(_ for _ in ()).throw(SystemExit("FORBIDDEN-CALL"));'
             'release_tools.active=deny;ops_menu._query=deny;'
             'sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name="__main__")')
    guarded = [str(python), '-B', '-c', guard, *command[2:]]
    with MenuSession(guarded, cwd=payload, env=env) as session:
        session.expect()
        for choice in ('bad', 'h', '2', '1', '2'):
            session.send(choice + '\n')
            session.expect()
        session.send('0\n')
        assert 'FORBIDDEN-CALL' not in session.finish()
    with MenuSession(guarded, cwd=payload, env=env) as session:
        session.expect()
        session.send('x' * 65 + '3\n')
        assert 'FORBIDDEN-CALL' not in session.finish(2)
    current = root / 'CURRENT.json'
    before = current.read_bytes()
    original_stat = current.stat()
    try:
        for kind in ('malformed', 'symlink'):
            current.unlink()
            if kind == 'malformed':
                current.write_bytes(b'{"release_id":')
                current.chmod(0o600)
            else:
                current.symlink_to(root / 'absent-metadata')
            snapshot = tree_snapshot(root)
            with MenuSession(guarded, cwd=payload, env=env) as session:
                session.expect()
                for choice in ('2', '3', '1'):
                    session.send(choice + '\n')
                    session.expect()
                session.send('0\n')
                text = session.finish()
                assert '完整校验拒绝' in text and 'FORBIDDEN-CALL' not in text, text
            assert tree_snapshot(root) == snapshot, 'Rejected metadata path wrote files'
    finally:
        current.unlink(missing_ok=True)
        current.write_bytes(before)
        current.chmod(stat.S_IMODE(original_stat.st_mode))
        os.utime(current, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    # Tamper only with this disposable fixture, never production or runtime code.
    marker = payload / '.menu-smoke-extra'
    try:
        marker.write_bytes(b'fake payload tamper\n')
        marker.chmod(0o600)
        with MenuSession(command, cwd=payload, env=env) as session:
            session.expect()
            session.send('3\n')
            text = session.expect()
            assert '完整校验失败' in text and '完整校验通过' not in text, text
            session.send('0\n')
            session.finish()
    finally:
        marker.unlink(missing_ok=True)
    return {'menu': 'installed-PTY-read-only/explicit-verify/EOF/Ctrl-C/non-TTY/tamper OK'}


def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def content_snapshot(root):
    """Freeze private fixture metadata and bytes, excluding only access times."""
    return (tree_snapshot(root), {str(path.relative_to(root)): file_digest(path)
            for path in sorted(root.rglob('*')) if path.is_file() and not path.is_symlink()})


def fixture_bundle(source, destination, identity, *, targets=None, broken_health=False, health_marker=None):
    """Derive a synthetic test fixture from this gate's exact current-built bundle.

    The successful candidate changes ONLY MANIFEST.release_id; every runtime,
    wheel, application and provenance byte is copied unchanged. Negative target
    and broken-health fixtures explicitly change the one field/file under test.
    These fixtures are never offered as official release artifacts.
    """
    with zipfile.ZipFile(source) as original:
        manifest = json.loads(original.read('MANIFEST.json'))
        manifest['release_id'] = identity
        if targets is not None:
            manifest['targets'] = targets
        broken = b'raise RuntimeError("synthetic menu candidate health failure")\n'
        if health_marker is not None:
            assert broken_health, "A health marker is only valid for the broken fixture"
            broken = ("from pathlib import Path\nPath(" + repr(str(health_marker))
                      + ").write_bytes(b'candidate-health-started\\n')\n").encode() + broken
        if broken_health:
            manifest['files']['main.py'] = {**manifest['files']['main.py'],
                'sha256': hashlib.sha256(broken).hexdigest(), 'size': len(broken)}
        with destination.open('xb') as output, zipfile.ZipFile(output, 'w') as result:
            for entry in original.infolist():
                raw = original.read(entry.filename)
                if entry.filename == 'MANIFEST.json':
                    raw = json.dumps(manifest, sort_keys=True, indent=2).encode()
                elif broken_health and entry.filename == 'main.py':
                    raw = broken
                result.writestr(entry, raw)
    destination.chmod(0o600)
    return file_digest(destination)


def _process_identity(process):
    assert process.poll() is None, 'Old panel exited during candidate preparation'
    # Linux proc starttime protects against observing a replacement PID.
    fields = Path(f'/proc/{process.pid}/stat').read_text().rsplit(')', 1)[1].split()
    return process.pid, fields[19]


def _lease_busy(path):
    assert path.is_file() and not path.is_symlink(), 'Expected real instance lease'
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def installed_menu_stage_smoke(python, payload, root, bundle, checksum, *, panel, probe):
    """Real package PTY stage while the original installed HTTPS panel stays live.

    The caller owns a disposable root, fake data and a TLS-verifying HTTPS probe.
    Only the test harness selects the old release. The menu must not activate,
    control services, migrate the current data or stop the existing panel.
    """
    assert file_digest(bundle) == checksum, 'Consumer bundle differs from gate input'
    old_release = payload.parent
    ready = json.loads((old_release / 'READY.json').read_text())
    assert ready['archive_sha256'] == checksum, 'Old installation is not from gate input'
    current = (root / 'CURRENT.json').read_bytes()
    old_tree = content_snapshot(old_release)
    data = content_snapshot(root / 'data')
    old_pid = _process_identity(panel)
    releases = {path.name for path in (root / 'releases').iterdir()}
    assert _lease_busy(root / '.panel.lease'), 'Old HTTPS panel must own the panel lease'
    assert not _lease_busy(root / '.operation.lock'), 'Unexpected active operation'
    probe()
    command = [str(python), '-B', str(payload / 'scripts/deploy.py'), '--root', str(root), 'menu']
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
           'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(payload)}
    stop = threading.Event()
    observations, failures = [], []

    def watch_panel():
        while not stop.is_set():
            try:
                assert _process_identity(panel) == old_pid, 'Old panel identity changed'
                probe()
                # Observe the stage-owned temporary directory without briefly
                # taking flock ourselves and racing the child's nonblocking lock.
                observations.append((time.monotonic(), bool(list(root.glob('.stage-*')))))
            except Exception as exc:
                failures.append(repr(exc))
                return
            stop.wait(.15)

    def preserved(*, candidate=None):
        assert not failures, 'Old HTTPS failed while menu was open: ' + repr(failures)
        assert _process_identity(panel) == old_pid, 'Old panel was replaced'
        probe()
        assert _lease_busy(root / '.panel.lease'), 'Menu released the old panel lease'
        assert not _lease_busy(root / '.operation.lock'), 'Stage operation lease leaked'
        assert (root / 'CURRENT.json').read_bytes() == current, 'Menu changed active selection'
        assert content_snapshot(old_release) == old_tree, 'Menu changed the old release'
        assert content_snapshot(root / 'data') == data, 'Menu changed existing fake user data'
        assert {p.name for p in (root / 'releases').iterdir()} == releases | ({candidate} if candidate else set()), 'Unexpected candidate release'
        assert not list(root.glob('.stage-*')), 'Stage temporary directory leaked'

    def begin(session, archive, sha):
        session.send('4\n')
        session.expect(ARCHIVE_PROMPT)
        session.send(str(archive) + '\n')
        session.expect(CHECKSUM_PROMPT)
        session.send(sha + '\n')
        session.expect(CONFIRM_PROMPT)

    def refused(archive, sha):
        with MenuSession(command, cwd=payload, env=env, timeout=660) as session:
            session.expect()
            begin(session, archive, sha)
            session.send('yes\n')
            text = session.expect()
            assert STAGE_FAILURE in text and STAGE_SUCCESS not in text, text
            session.send('0\n')
            session.finish()
        preserved()

    watcher = threading.Thread(target=watch_panel, daemon=True)
    watcher.start()
    try:
        with tempfile.TemporaryDirectory(prefix='vui-menu-candidates-') as directory:
            fixture = Path(directory)
            identity = 'menu-stage-fixture-' + hashlib.sha256(current).hexdigest()[:16]
            candidate = fixture / 'manifest-only-candidate.zip'
            candidate_sha = fixture_bundle(bundle, candidate, identity)
            # Every cancellation occurs before any stage subprocess or write.
            before_cancel = tree_snapshot(root)
            with MenuSession(command, cwd=payload, env=env) as session:
                session.expect()
                session.send('4\n'); session.expect(ARCHIVE_PROMPT); session.send('\n')
                assert STAGE_CANCEL in session.expect()
                session.send('4\n'); session.expect(ARCHIVE_PROMPT)
                session.send(str(candidate) + '\n'); session.expect(CHECKSUM_PROMPT); session.send('\n')
                assert STAGE_CANCEL in session.expect()
                begin(session, candidate, candidate_sha); session.send('N\n')
                assert STAGE_CANCEL in session.expect()
                session.send('0\n'); session.finish()
            assert tree_snapshot(root) == before_cancel, 'Canceled preparation wrote to installation'
            preserved()
            wrong_sha = ('0' if candidate_sha[0] != '0' else '1') + candidate_sha[1:]
            refused(candidate, wrong_sha)
            cross_target = fixture / 'cross-target.zip'
            other = 'aarch64-gnu' if ready['runtime_key'] != 'aarch64-gnu' else 'x86_64-gnu'
            cross_sha = fixture_bundle(bundle, cross_target, 'menu-cross-target-fixture', targets=[other])
            refused(cross_target, cross_sha)
            cross_target.unlink()
            # Occupied operation lease refuses without interfering with the
            # separate panel lease. Merely being live must not forbid staging.
            with (root / '.operation.lock').open('r+b') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with MenuSession(command, cwd=payload, env=env) as session:
                    session.expect(); begin(session, candidate, candidate_sha); session.send('yes\n')
                    text = session.expect()
                    assert STAGE_FAILURE in text and STAGE_SUCCESS not in text, text
                    session.send('0\n'); session.finish()
                fcntl.flock(lock, fcntl.LOCK_UN)
            preserved()
            broken = fixture / 'broken-health.zip'
            health_marker = fixture / 'broken-health-started'
            broken_sha = fixture_bundle(bundle, broken, 'menu-broken-health-fixture',
                broken_health=True, health_marker=health_marker)
            refused(broken, broken_sha)
            assert health_marker.read_bytes() == b'candidate-health-started\n', 'Broken candidate did not reach its real startup health check'
            assert not (root / 'releases' / 'menu-broken-health-fixture').exists()
            broken.unlink()
            with MenuSession(command, cwd=payload, env=env, timeout=660) as session:
                session.expect(); begin(session, candidate, candidate_sha)
                started = time.monotonic(); session.send('yes\n')
                deadline = started + 20
                while not list(root.glob('.stage-*')):
                    assert session.process.poll() is None, 'Menu exited before real stage ran'
                    assert time.monotonic() < deadline, 'Stage never acquired operation lease'
                    time.sleep(.02)
                assert _lease_busy(root / '.operation.lock'), 'Real stage did not hold operation lease'
                # A second real installed menu cannot enter the active stage.
                with MenuSession(command, cwd=payload, env=env) as reentry:
                    reentry.expect(); begin(reentry, candidate, candidate_sha); reentry.send('yes\n')
                    text = reentry.expect()
                    assert STAGE_FAILURE in text and STAGE_SUCCESS not in text, text
                    reentry.send('0\n'); reentry.finish()
                text = session.expect()
                finished = time.monotonic()
                assert STAGE_SUCCESS in text and STAGE_FAILURE not in text, text
                session.send('0\n'); session.finish()
            assert sum(started <= at <= finished and busy for at, busy in observations) >= 2, 'No continued HTTPS evidence while stage held its operation lease'
            preserved(candidate=identity)
            prepared = root / 'releases' / identity
            prepared_ready = json.loads((prepared / 'READY.json').read_text())
            assert prepared_ready['archive_sha256'] == candidate_sha
            verification = subprocess.run([str(python), '-B', '-c',
                'from pathlib import Path;import sys;from app.release_tools import verify_payload,verify_runtime;'
                'root=Path(sys.argv[1]);verify_payload(root/"payload");verify_runtime(root)', str(prepared)],
                cwd=payload, env=env, capture_output=True, text=True, timeout=60)
            assert verification.returncode == 0, verification.stderr
            candidate_tree = content_snapshot(prepared)
            # Duplicate candidate is not a successful operation and cannot
            # overwrite a version that has already passed the real health check.
            with MenuSession(command, cwd=payload, env=env, timeout=660) as session:
                session.expect(); begin(session, candidate, candidate_sha); session.send('yes\n')
                text = session.expect()
                assert STAGE_FAILURE in text and STAGE_SUCCESS not in text, text
                session.send('0\n'); session.finish()
            assert content_snapshot(prepared) == candidate_tree, 'Duplicate stage overwrote candidate'
            preserved(candidate=identity)
    finally:
        stop.set(); watcher.join(timeout=5)
        assert not watcher.is_alive(), 'HTTPS monitor did not finish'
    assert not failures, 'Old HTTPS continuity failed: ' + repr(failures)
    return {'menu_stage': 'installed-PTY synthetic-manifest-only candidate / real offline stage+health / cancel / bad SHA / cross-target / broken health / duplicate / operation lease+reentry / unchanged CURRENT+old release+data+PID / continued verified HTTPS OK'}


def portable_menu_stage_smoke(python, payload, root, bundle, checksum, *, managed_smoke=None):
    """All native target gates exercise a real installed HTTPS panel, not systemd."""
    # Generate a disposable certificate with installed cryptography, so the
    # portable gate's distro Python only needs its standard library.
    certificate_code = '''
from datetime import datetime,timedelta,timezone
import ipaddress,os,sys
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
os.umask(0o077)
root=Path(sys.argv[1]); key=ec.generate_private_key(ec.SECP256R1())
name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'VUI disposable menu HTTPS fixture')])
now=datetime.now(timezone.utc)
cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
.serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
.not_valid_after(now+timedelta(hours=1))
.add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
.add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
.sign(key,hashes.SHA256()))
(root/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
(root/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
'''
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
           'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(payload)}
    with tempfile.TemporaryDirectory(prefix='vui-menu-https-') as directory:
        fixture = Path(directory)
        result = subprocess.run([str(python), '-B', '-c', certificate_code, str(fixture)],
            cwd=payload, env=env, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        data = root / 'data'; data.mkdir(mode=0o700, exist_ok=True)
        marker = data / 'synthetic-user-data.txt'
        marker.write_bytes(b'Fake portable menu fixture; preserve these bytes.\n'); marker.chmod(0o600)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        origin = f'https://127.0.0.1:{port}'
        context = ssl.create_default_context(cafile=str(fixture / 'cert.pem'))
        opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
        def probe():
            with opener.open(origin + '/login', timeout=3) as response:
                assert response.status == 200 and response.read(), 'Old HTTPS login unavailable'
        with (fixture / 'panel.log').open('w') as log:
            panel = subprocess.Popen([str(python), '-B', str(payload / 'scripts/deploy.py'),
                '--root', str(root), 'run', '--origin', origin, '--cert', str(fixture / 'cert.pem'),
                '--key', str(fixture / 'key.pem'), '--port', str(port)],
                cwd=payload, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 30
                while True:
                    assert panel.poll() is None, 'Old HTTPS panel exited: ' + (fixture / 'panel.log').read_text()
                    try:
                        probe(); break
                    except (OSError, URLError):
                        assert time.monotonic() < deadline, 'Old HTTPS panel not ready: ' + (fixture / 'panel.log').read_text()
                        time.sleep(.1)
                result = installed_menu_stage_smoke(python, payload, root, bundle, checksum,
                    panel=panel, probe=probe)
                if managed_smoke is not None:
                    result.update(managed_smoke(python, payload, root, bundle, checksum,
                        panel=panel, probe=probe))
                return result
            finally:
                panel.terminate()
                try:
                    panel.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    panel.kill(); panel.wait(timeout=5)
