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



RUNTIME_KEYS = {'x86_64-gnu', 'aarch64-gnu', 'x86_64-musl', 'aarch64-musl'}


def panel_environment(environment, runtime_key):
    """Keep freed large GNU allocations releasable; preserve explicit settings.

    This applies at panel exec and is inherited by its service children. It is
    not a password-cost change, cache reset, or a limit on allocated memory.
    """
    if runtime_key not in RUNTIME_KEYS:
        raise RuntimeError('Unknown selected runtime target')
    result = dict(environment)
    if runtime_key.endswith('-gnu'):
        result.setdefault('MALLOC_MMAP_THRESHOLD_', '131072')
    return result


def system_panel_environment(environment, runtime_key, inherited):
    # Keep the existing strict filter. Only this allocator input is added;
    # arbitrary loader/GLIBC_TUNABLES overrides remain excluded here.
    result = dict(environment)
    if runtime_key.endswith('-gnu') and 'MALLOC_MMAP_THRESHOLD_' in inherited:
        value = inherited['MALLOC_MMAP_THRESHOLD_']
        if not re.fullmatch(r'[0-9]{1,20}', value) or int(value) > 2**64 - 1:
            raise RuntimeError('MALLOC_MMAP_THRESHOLD_ must be an unsigned 64-bit decimal')
        result['MALLOC_MMAP_THRESHOLD_'] = value
    return panel_environment(result, runtime_key)


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
    ready=json.loads((release/'READY.json').read_text())
    runtime=release/'runtime'/'python'/'bin'/'python3'
    if not runtime.exists(): raise RuntimeError('Portable runtime is missing')
    runtime_key = ready['runtime_key']
    if runtime_key not in RUNTIME_KEYS:
        raise RuntimeError('Unknown selected runtime target')
    return cfg, release, payload, runtime, runtime_key


def main():
    mode = sys.argv[1] if len(sys.argv) == 2 else ''
    if mode not in ('panel', 'http01', 'http01-direct'):
        raise RuntimeError('Choose panel or http01')
    cfg, release, payload, runtime, runtime_key = load_selected()
    arch = runtime_key.split('-', 1)[0]
    if mode in ('http01', 'http01-direct'):
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
               VUI_DATA_DIR=str(ROOT / 'data'), VUI_BIN_DIR=str(payload / 'cores' / arch))
    if mode == 'panel':
        env = system_panel_environment(env, runtime_key, os.environ)
    if mode == 'http01-direct':
        env['VUI_HTTP01_DIRECT']='1'
        env['VUI_HTTP01_IPV6']='1' if cfg.get('ipv6') else '0'
    os.chdir(payload)
    os.execve(runtime, [str(runtime), '-B', *command], env)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        print('V-UI launch refused: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
