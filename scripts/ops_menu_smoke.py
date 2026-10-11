"""Real PTY consumer checks for the installed menu, reused by all four targets."""
from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path
import pty
import select
import signal
import stat
import subprocess
import termios
import time

PROMPT = '请选择 [1/2/3/h/0]: '.encode()


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

    def finish(self, code=0):
        deadline = time.monotonic() + self.timeout
        while self.process.poll() is None:
            if time.monotonic() >= deadline:
                raise AssertionError('Menu did not exit\n' + self.text())
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
        assert '只读运维菜单' in first and '完整校验通过' not in first, first
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
