"""Explicit local A/B diagnostic of a verified bundle; not a cgroup qualification.

Run against the pre-optimization bundle to compare unset versus static GNU
allocation policy. A newer launcher may itself supply a default; the report
records the requested environment override, never claims libc introspection.
"""
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, build_opener, ProxyHandler, HTTPSHandler, HTTPCookieProcessor

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE / 'tests'))
from app import release_tools as tools
from loopback_helpers import certificate_files, unused_port
from scripts.low_resource_acceptance import filesystem_type, write_json
from scripts.low_resource_accounting import parse_rollup


def run(args):
    if args.output.exists():
        raise RuntimeError('Refusing to replace prior diagnostic evidence')
    if not re.fullmatch('[0-9a-f]{40}', args.source_commit):
        raise RuntimeError('Exact bundle source commit required')
    filesystem = filesystem_type(args.work_dir.resolve())
    if filesystem in ('tmpfs', 'ramfs'):
        raise RuntimeError('Use an existing disk-backed work directory')
    if 'GLIBC_TUNABLES' in os.environ:
        raise RuntimeError('Conflicting inherited GLIBC_TUNABLES; use a clean test environment')
    with args.bundle.open('rb') as source:
        actual = hashlib.file_digest(source, 'sha256').hexdigest()
    if actual != args.sha256:
        raise RuntimeError('Bundle digest mismatch')
    report = dict(source_commit=args.source_commit, bundle_sha256=actual, filesystem=filesystem,
                  scope='local installed HTTPS panel A/B; no cgroup, proxy core, 30-minute or 160MiB qualification',
                  outcome='running', samples=[], requests_per_round=100, concurrency=10,
                  environment_policy='unset requested versus MALLOC_MMAP_THRESHOLD_=131072; selected launcher may add its own default')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def save(): write_json(args.output, report)
    save()
    try:
        with tempfile.TemporaryDirectory(prefix='panel-allocator-', dir=args.work_dir) as temporary:
            root = Path(temporary)
            identity = tools.stage(args.bundle, actual, root); tools.activate(root, identity)
            release, _ = tools.active(root); payload = release / 'payload'
            python = release / 'runtime/python/bin/python3'
            manifest = tools.verify_payload(payload)
            if manifest['source_commit'] != args.source_commit:
                raise RuntimeError('Installed source mismatch')
            runtime_key = json.loads((release / 'READY.json').read_text())['runtime_key']
            if runtime_key not in ('x86_64-gnu', 'aarch64-gnu'):
                raise RuntimeError('This diagnostic compares the GNU allocator only')
            report['runtime_key'] = runtime_key
            password = 'Synthetic-panel-memory-probe-password!'
            for index, override in enumerate((None, '131072', None, '131072')):
                env = tools.child_env(payload, root / 'data')
                env.pop('MALLOC_MMAP_THRESHOLD_', None)
                if override is not None: env['MALLOC_MMAP_THRESHOLD_'] = override
                subprocess.run([str(python), '-B', '-c',
                    'from app.services.auth_service import provision_admin;import sys;'
                    'provision_admin("resource-admin",sys.stdin.read(),reset='+str(index > 0)+')'],
                    input=password, text=True, cwd=payload, env=env, check=True, timeout=30)
                certdir = root / f'fake-cert-{index}'; certdir.mkdir(mode=0o700)
                ca, cert, key = certificate_files(certdir, 'panel-probe')
                cert.chmod(0o600); key.chmod(0o600)
                port = unused_port(); origin = f'https://127.0.0.1:{port}'
                context = ssl.create_default_context(cafile=ca)
                jar = CookieJar()
                client = build_opener(ProxyHandler({}), HTTPSHandler(context=context), HTTPCookieProcessor(jar))
                with (root / f'panel-{index}.log').open('w') as log:
                    process = subprocess.Popen([str(python), '-B', str(payload / 'scripts/deploy.py'),
                        '--root', str(root), 'run', '--origin', origin, '--port', str(port),
                        '--cert', str(cert), '--key', str(key)], cwd=payload, env=env,
                        stdout=log, stderr=subprocess.STDOUT)
                    try:
                        deadline = time.monotonic() + 30
                        while True:
                            if process.poll() is not None: raise RuntimeError('Panel exited before ready')
                            try:
                                with client.open(origin + '/login', timeout=.5) as response:
                                    if response.status != 200: raise RuntimeError('Panel readiness failed')
                                break
                            except OSError:
                                if time.monotonic() >= deadline: raise
                                time.sleep(.05)
                        def record(phase, wall=None):
                            report['samples'].append(dict(round=index, requested_override=override, phase=phase,
                                wall_seconds=wall, memory_bytes=parse_rollup(Path(f'/proc/{process.pid}/smaps_rollup').read_text())))
                            save()
                        record('ready_before_login')
                        request = Request(origin + '/api/auth/login', data=json.dumps(dict(username='resource-admin', password=password)).encode(),
                                          headers={'Origin':origin, 'X-VUI-Request':'1', 'Content-Type':'application/json'})
                        started = time.monotonic()
                        with client.open(request, timeout=15) as response:
                            if response.status != 200: raise RuntimeError('Login failed')
                            response.read()
                        record('after_login', time.monotonic() - started)
                        cookie = '; '.join(item.name + '=' + item.value for item in jar)
                        def one(_):
                            local = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
                            with local.open(Request(origin + '/api/auth/me', headers={'Cookie':cookie}), timeout=15) as response:
                                if response.status != 200 or json.loads(response.read())['username'] != 'resource-admin':
                                    raise RuntimeError('Authenticated read failed')
                        started = time.monotonic()
                        with ThreadPoolExecutor(max_workers=10) as pool: list(pool.map(one, range(100)))
                        record('after_100_reads_concurrency10', time.monotonic() - started)
                        time.sleep(3); record('idle_3_seconds')
                    finally:
                        process.terminate()
                        try: process.wait(timeout=12)
                        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        report['outcome'] = 'passed'
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        report['outcome'] = 'failed'
        raise
    finally: save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args())
