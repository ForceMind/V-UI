"""Official stable-only online upgrade. Network sources and installer flags are fixed.

Trust boundary: the current HTTPS canonical setup.sh supplies the accepted kit
pin. It is parsed as data, never executed. Callers cannot supply a URL or pin.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import ssl
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

SETUP = 'https://raw.githubusercontent.com/ForceMind/V-UI/main/setup.sh'
LATEST = 'https://api.github.com/repos/ForceMind/V-UI/releases/latest'
BASE = 'https://github.com/ForceMind/V-UI/releases/download/'
HOSTS = {'raw.githubusercontent.com', 'api.github.com', 'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}
ASSETS = {'install.sh', 'install_system.py', 'platform_support.py', 'firewall_support.py',
          'service_support.py', 'install_ops_control.py', 'system_launcher.py', 'system_ops.py',
          'ops_worker.py', 'release_tools.py', 'v-ui', 'vui-source.zip', 'RELEASE.json', 'RELEASE_NOTES.md'}
BUNDLES = {'vui-linux-' + target + '.zip' for target in ('x86_64-gnu', 'aarch64-gnu', 'x86_64-musl', 'aarch64-musl')}
VERSION = re.compile(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')


class UpgradeRefused(RuntimeError):
    pass


def url_allowed(url):
    value = urlsplit(url)
    if (value.scheme != 'https' or value.hostname not in HOSTS or value.username is not None
            or value.password is not None or value.port not in (None, 443) or value.fragment):
        raise UpgradeRefused('untrusted download location')


class Redirects(HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        url_allowed(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download(url, path=None, *, limit=1_000_000):
    url_allowed(url)
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), Redirects())
    request = Request(url, headers={'User-Agent': 'V-UI-fixed-maintenance', 'Accept': 'application/vnd.github+json' if url == LATEST else 'application/octet-stream'})
    chunks = []
    size = 0
    digest = hashlib.sha256()
    deadline = time.monotonic() + 300
    output = path.open('xb') if path is not None else None
    try:
        with opener.open(request, timeout=30) as response:
            url_allowed(response.url)
            while True:
                if time.monotonic() > deadline:
                    raise UpgradeRefused('download deadline')
                chunk = response.read(min(1024 * 1024, limit + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise UpgradeRefused('asset too large')
                digest.update(chunk)
                if output is None:
                    chunks.append(chunk)
                else:
                    output.write(chunk)
        if output is not None:
            output.flush(); os.fsync(output.fileno())
        return digest.hexdigest() if path is not None else b''.join(chunks)
    finally:
        if output is not None:
            output.close()


def accepted_release(ops):
    setup = download(SETUP, limit=128_000).decode('utf-8')
    versions = re.findall(r'^  local version=(v[0-9]+\.[0-9]+\.[0-9]+) version_seen=0 argument domain=$', setup, re.M)
    pins = re.findall(r'^  local kit_sha=([a-f0-9]{64})$', setup, re.M)
    if len(versions) != 1 or len(pins) != 1 or not VERSION.fullmatch(versions[0]):
        raise UpgradeRefused('canonical stable pin unavailable')
    release = ops.decode_json(download(LATEST, limit=1_000_000))
    if (release.get('tag_name') != versions[0] or release.get('draft') is not False
            or release.get('prerelease') is not False or not isinstance(release.get('published_at'), str)):
        raise UpgradeRefused('latest release and canonical pin disagree')
    return versions[0], pins[0]


def checksums(raw, pin):
    if hashlib.sha256(raw).hexdigest() != pin:
        raise UpgradeRefused('accepted manifest mismatch')
    result = {}
    for line in raw.decode('ascii').splitlines():
        found = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9._-]+)', line)
        if not found or found[2] in result:
            raise UpgradeRefused('checksum manifest')
        result[found[2]] = found[1]
    bundles = set(result) - ASSETS
    if not ASSETS <= result.keys() or not bundles <= BUNDLES or len(bundles) not in (1, 4):
        raise UpgradeRefused('complete fixed kit required')
    return result


def run(command, ops, *, timeout=1800):
    # No inherited proxy, PYTHONPATH, user input, command output or TTY.
    process = subprocess.Popen(command, cwd='/', env=ops.ENV, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        if process.wait(timeout=timeout) != 0:
            raise UpgradeRefused('installer did not confirm success')
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
        raise


def upgrade(ops):
    with ops.installation_lock():
        manager, account = ops.verify_installation()
        config = ops.decode_json(ops.trusted_bytes(ops.CONFIG, 16384))
        if (manager != 'systemd' or config.get('installer') != 1 or config.get('ready') is not True
                or os.path.lexists(ops.CONFIG.parent / '.install-transaction.json')):
            raise UpgradeRefused('ready managed systemd installation required')
        current = re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-[a-f0-9]{12}', config.get('release_id', ''))
        if not current:
            raise UpgradeRefused('current stable version unknown')
        if config.get('certificate_mode') == 'managed':
            if {'domain': config.get('domain'), 'environment': 'production'} not in config.get('ca_authorizations', []):
                raise UpgradeRefused('new CA authorization requires a local administrator')
        elif config.get('certificate_mode') != 'provided':
            raise UpgradeRefused('certificate configuration')
    version, pin = accepted_release(ops)
    new = VERSION.fullmatch(version)
    if tuple(map(int, new.groups())) <= tuple(map(int, current.groups())):
        return {'version': version, 'upgraded': False, 'reason': 'ALREADY_CURRENT_OR_NEWER'}
    return install_release(ops, config, version, pin)


def install_release(ops, config, version, pin):
    # Private continuation of upgrade(), also exercised on an ephemeral CI
    # host with an exact-candidate local transport adapter. It is not a socket,
    # CLI, environment or HTTP operation and accepts no user-supplied source.
    # Downloads live only in a private root-owned temp directory on disk. All
    # assets are hashed before any downloaded root code can run.
    with ops.trusted_directory(Path('/var/lib/v-ui-maintenance')):
        pass
    with tempfile.TemporaryDirectory(prefix='v-ui-official-', dir='/var/lib/v-ui-maintenance') as temporary:
        directory = Path(temporary)
        manifest = download(BASE + version + '/SHA256SUMS', limit=64000)
        values = checksums(manifest, pin)
        target = 'vui-linux-' + config['target'] + '.zip'
        if target not in values:
            raise UpgradeRefused('release target missing')
        (directory / 'SHA256SUMS').write_bytes(manifest)
        total = 0
        for name, checksum in sorted(values.items()):
            if name in BUNDLES and name != target:
                continue
            if download(BASE + version + '/' + name, directory / name,
                        limit=900_000_000 if name in BUNDLES else 64_000_000) != checksum:
                raise UpgradeRefused('asset checksum mismatch')
            total += (directory / name).stat().st_size
            if total > 3_700_000_000:
                raise UpgradeRefused('kit size')
        release = ops.decode_json((directory / 'RELEASE.json').read_bytes())
        if (release.get('version') != version[1:] or release.get('release_id') != version[1:] + '-' + release.get('source_commit', '')[:12]
                or not re.fullmatch(r'[a-f0-9]{40}', release.get('source_commit', ''))):
            raise UpgradeRefused('stable version metadata mismatch')
        # The currently trusted verifier binds root helper/source bytes to the
        # complete accepted manifest and exact source commit, before execution.
        run([str(ops.PYTHON), '-B', '-I', '-S', str(ops.CONTROL / 'install_control_verifier.py'),
             '--kit', str(directory), '--sha256', pin, '--local-target-only', '--preflight'], ops, timeout=120)
        # Snapshot once more before downloading code is executed. The new
        # unattended mode enforces this same root config and never creates an
        # initial account, accepts a new CA agreement or recovers a stale journal.
        with ops.installation_lock():
            ops.verify_installation()
            if ops.decode_json(ops.trusted_bytes(ops.CONFIG, 16384)) != config:
                raise UpgradeRefused('configuration changed')
        command = [str(ops.PYTHON), '-B', '-E', '-S', str(directory / 'install_system.py'),
                   '--bundle', str(directory / target), '--sha256', values[target],
                   '--upgrade', '--unattended-upgrade', '--open-firewall', 'no']
        run(command, ops)
        # No installation lock around either child: each installer owns that
        # lock itself. The independent daemon and its root-private task survive
        # panel stops and helper replacement. start does not restart this daemon.
        run([str(ops.PYTHON), '-B', '-I', '-S', str(directory / 'install_ops_control.py'),
             '--kit', str(directory), '--sha256', pin, '--local-target-only', '--refresh'], ops, timeout=180)
        return {'version': version, 'upgraded': True, 'health': 'TLS_HOSTNAME_CHAIN_HTTP401'}
