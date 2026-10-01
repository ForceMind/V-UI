"""Root-owned launcher, executed only as the dedicated service user.

This file contains no networking or writable-code-as-root path. Systemd's socket
file descriptors survive exec; LISTEN_PID remains the same for HTTP-01.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import sys

ROOT = Path('/var/lib/v-ui')
CONFIG = Path('/etc/v-ui/service.json')


def load_selected():
    if os.geteuid() == 0 or os.geteuid() != pwd.getpwnam('v-ui').pw_uid:
        raise RuntimeError('Run as the dedicated v-ui service user')
    if CONFIG.is_symlink() or CONFIG.stat().st_uid != 0 or CONFIG.stat().st_mode & 0o022:
        raise RuntimeError('Service configuration is not root-controlled')
    cfg = json.loads(CONFIG.read_text())
    pointer = json.loads((ROOT / 'CURRENT.json').read_text())
    identity = pointer['release_id']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', identity):
        raise RuntimeError('Invalid current version')
    release = ROOT / 'releases' / identity
    if any(path.is_symlink() for path in (ROOT, ROOT / 'releases', release, release / 'payload')):
        raise RuntimeError('Symbolic release paths are not permitted')
    payload = release / 'payload'
    # Protect launch and challenge code, not just main's later verification.
    manifest = json.loads((payload / 'MANIFEST.json').read_text())
    ready = json.loads((release / 'READY.json').read_text())
    if hashlib.sha256((payload / 'MANIFEST.json').read_bytes()).hexdigest() != ready['manifest_sha256']:
        raise RuntimeError('Prepared manifest changed')
    for name in ('app/serve.py', 'app/certificates/http01.py'):
        path = payload / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != manifest['files'][name]['sha256']:
            raise RuntimeError('Launch code failed integrity check')
    return cfg, release, payload


def main():
    mode = sys.argv[1] if len(sys.argv) == 2 else ''
    if mode not in ('panel', 'http01'):
        raise RuntimeError('Choose panel or http01')
    cfg, release, payload = load_selected()
    if mode == 'http01':
        command = ['-m', 'app.certificates.http01', '--webroot',
                   str(ROOT / 'data/certificates/http-webroot')]
    else:
        command = ['-m', 'app.serve', '--root', str(ROOT), '--origin', cfg['origin'],
                   '--bind', cfg['bind'], '--port', str(cfg['port'])]
        if cfg['certificate_mode'] == 'provided':
            command += ['--cert', str(ROOT / 'data/certs/provided-fullchain.pem'),
                        '--key', str(ROOT / 'data/certs/provided-privkey.pem')]
    env = {name: value for name, value in os.environ.items()
           if name in {'LANG', 'LC_ALL', 'LISTEN_FDS', 'LISTEN_PID', 'LISTEN_FDNAMES',
                       'NOTIFY_SOCKET', 'INVOCATION_ID', 'JOURNAL_STREAM'}}
    env.update(PATH='/usr/bin:/bin', HOME=str(ROOT), PYTHONDONTWRITEBYTECODE='1',
               VUI_DATA_DIR=str(ROOT / 'data'), VUI_BIN_DIR=str(payload / 'cores'))
    python = release / 'venv/bin/python'
    os.chdir(payload)
    os.execve(python, [str(python), '-B', *command], env)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        print('V-UI launch refused: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
