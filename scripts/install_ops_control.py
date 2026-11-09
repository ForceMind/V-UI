#!/usr/bin/python3
"""Explicitly install the trusted system controller from a complete accepted kit.

Run this separately with the OS Python's -B -I -S flags. --sha256 authenticates the
entire SHA256SUMS file using an independently trusted digest, not a digest copied
from the same untrusted download. The online --with-global-cli flow explicitly
calls this tool after the panel installer succeeds.
Installs/starts only the fixed independent maintenance service on systemd.
No account, sudoers, SUID, package runtime or firewall is changed here.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import pwd
import re
import stat
import subprocess
import sys
import tempfile
import types
import zipfile

ROOT = Path('/var/lib/v-ui')
CONFIG = Path('/etc/v-ui/service.json')
CONTROL = Path('/usr/local/lib/v-ui')
ENTRY = Path('/usr/local/sbin/v-ui')
COMMAND_ALIAS = Path('/usr/local/bin/v-ui')
RECEIPT = CONTROL / 'ops-control.json'
LAUNCHER = CONTROL / 'launcher.py'
SYSTEM_PYTHON = Path('/usr/bin/python3')
SYSTEMD_DIR = Path('/etc/systemd/system')
OPENRC_DIR = Path('/etc/init.d')
LOCK = Path('/run/lock/v-ui-install.lock')
TARGETS = {'x86_64-gnu', 'aarch64-gnu', 'x86_64-musl', 'aarch64-musl'}
COMMON_ASSETS = {
    'install.sh', 'install_system.py', 'platform_support.py', 'firewall_support.py',
    'service_support.py', 'install_ops_control.py', 'system_launcher.py',
    'system_ops.py', 'ops_worker.py', 'release_tools.py', 'v-ui',
    'vui-source.zip', 'RELEASE.json', 'RELEASE_NOTES.md',
}
CODE_ASSETS = {'firewall_support.py', 'service_support.py', 'system_launcher.py', 'install_ops_control.py',
               'system_ops.py', 'ops_worker.py', 'release_tools.py', 'v-ui', 'RELEASE.json'}
SOURCE_MEMBERS = {
    'install.sh': 'install.sh', 'install_system.py': 'scripts/install_system.py',
    'platform_support.py': 'scripts/platform_support.py',
    'firewall_support.py': 'scripts/firewall_support.py',
    'service_support.py': 'scripts/service_support.py',
    'install_ops_control.py': 'scripts/install_ops_control.py',
    'system_launcher.py': 'deploy/system_launcher.py', 'system_ops.py': 'deploy/system_ops.py',
    'ops_worker.py': 'deploy/ops_worker.py', 'release_tools.py': 'app/release_tools.py',
    'v-ui': 'deploy/v-ui', 'RELEASE_NOTES.md': 'docs/RELEASE_NOTES.md',
}
# Internal controller dependencies are authenticated by the complete source ZIP
# digest and commit binding above; they are not additional top-level kit assets.
PRE_DNS_SOURCE_DEPENDENCIES = {
    'data_owner.py': 'app/data_owner.py',
    'proxy_store.py': 'app/services/proxy_store.py',
    'upstream_profile.py': 'app/services/upstream_profile.py',
    'inbound_policy.py': 'app/services/inbound_policy.py',
    'maintenance_daemon.py': 'deploy/maintenance_daemon.py',
    'maintenance_upgrade.py': 'deploy/maintenance_upgrade.py',
    'maintenance_client.py': 'deploy/maintenance_client.py',
    'maintenance_cli_worker.py': 'deploy/maintenance_cli_worker.py',
    'install_control_verifier.py': 'scripts/install_ops_control.py',
    'v-ui-maintenance.service': 'deploy/v-ui-maintenance.service',
}
# Exact pre-DNS closure, including the published v0.4.22 controller. Do not
# derive historical shapes from the expanded current dependency set.
DNS_SOURCE_DEPENDENCIES = {
    'certificate_errors.py': 'app/certificates/errors.py',
    'dns_store.py': 'app/certificates/dns_store.py',
    'cloudflare.py': 'app/certificates/cloudflare.py',
    'dns_journal.py': 'app/certificates/dns_journal.py',
    'dns_backup.py': 'app/certificates/dns_backup.py',
}
# Keep both historical closures fixed. Core helpers are one complete new group.
CORE_SOURCE_DEPENDENCIES = {
    'core_artifacts.py': 'app/core_updates/artifacts.py',
    'core_selection.py': 'app/core_updates/selection.py',
    'core_binary.py': 'app/core_updates/binary.py',
    'core_store.py': 'app/core_updates/store.py',
    'core_github.py': 'app/core_updates/github.py',
    'core_catalog.py': 'app/core_updates/catalog.py',
    'core_resolver.py': 'app/core_updates/resolver.py',
    'core_application.py': 'app/core_updates/application.py',
    'core_selection_files.py': 'app/core_updates/selection_files.py',
    'core_boot.py': 'app/core_updates/boot.py',
    'core_backup.py': 'app/core_updates/backup.py',
    'core_backup_archive.py': 'app/core_updates/backup_archive.py',
    'core_maintenance.py': 'app/core_updates/maintenance.py',
    'core_maintenance_recovery.py': 'app/core_updates/maintenance_recovery.py',
    'core_boot_broker.py': 'deploy/core_boot_broker.py',
    'core_maintenance_control.py': 'deploy/core_maintenance_control.py',
}
YAML_SOURCE_DEPENDENCIES = {'mihomo_plan_record.py': 'app/services/mihomo_plan_record.py'}
SOURCE_DEPENDENCIES = (PRE_DNS_SOURCE_DEPENDENCIES | DNS_SOURCE_DEPENDENCIES
                       | CORE_SOURCE_DEPENDENCIES | YAML_SOURCE_DEPENDENCIES)
RECEIPT_KEYS = {'schema', 'service_manager', 'system_python', 'interpreter_resolved',
                'interpreter_sha256', 'files'}
MAX_CODE = 4_000_000
MAX_JSON = 16_384
MAX_INTERPRETER = 32_000_000
MAX_ASSET = 900_000_000
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C', 'HOME': '/'}
PROBE_CAPABILITIES = {'stdlib', 'tar_filter', 'pidfd', 'waitid', 'process_control'}
SYSTEM_PROBE = '''import fcntl, hashlib, inspect, json, os, selectors, signal, sqlite3, ssl, subprocess, sys, tarfile, termios, zipfile, zlib
from urllib.request import ProxyHandler, build_opener
capabilities = {
    "stdlib": callable(sqlite3.Connection.backup) and callable(ssl.SSLContext),
    "tar_filter": callable(getattr(tarfile, "data_filter", None)) and "filter" in inspect.signature(tarfile.TarFile.extractall).parameters,
    "pidfd": False,
    "waitid": callable(getattr(os, "waitid", None)) and all(hasattr(os, name) for name in ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT", "CLD_EXITED")),
    "process_control": callable(getattr(signal, "pthread_sigmask", None)) and callable(getattr(signal, "setitimer", None)) and {"user", "group", "extra_groups", "start_new_session"} <= inspect.signature(subprocess.Popen).parameters.keys(),
}
try:
    fd = os.pidfd_open(os.getpid())
    os.close(fd)
    capabilities["pidfd"] = True
except (AttributeError, OSError):
    pass
print(json.dumps({"version": list(sys.version_info[:2]), "capabilities": capabilities}))
'''


class InstallError(RuntimeError):
    pass


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def owned_by_root(info):
    return info.st_uid == 0


def trusted_parents(path, *, lock=False):
    """Never traverse a writable/symbolic root-control ancestor."""
    for parent in reversed(path.parents):
        info = parent.lstat()
        sticky_lock = (lock and parent == LOCK.parent and str(parent) == '/run/lock'
                       and stat.S_IMODE(info.st_mode) == 0o1777)
        if (not stat.S_ISDIR(info.st_mode) or not owned_by_root(info)
                or (info.st_mode & 0o022 and not sticky_lock)):
            raise InstallError('Untrusted root-controlled directory: ' + str(parent))


def check_regular(info, path, *, root=False, mode=None):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or (root and not owned_by_root(info))
            or (mode is not None and stat.S_IMODE(info.st_mode) != mode)):
        raise InstallError('Unsafe file ownership, type, links or mode: ' + str(path))


def same_inode(first, second):
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def read_file(path, *, root=False, mode=None, limit=MAX_CODE, keep=True):
    """Hash/read one opened regular inode, never follow a symlink or hardlink."""
    path = Path(path)
    if root:
        trusted_parents(path)
    else:
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise InstallError('Symbolic kit path: ' + str(path))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        check_regular(before, path, root=root, mode=mode)
        if before.st_size > limit:
            raise InstallError('File exceeds size limit: ' + str(path))
        hasher = hashlib.sha256()
        result = bytearray()
        count = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            count += len(chunk)
            if count > limit:
                raise InstallError('File exceeds size limit: ' + str(path))
            hasher.update(chunk)
            if keep:
                result.extend(chunk)
        after = os.fstat(fd)
        if (not same_inode(after, path.lstat()) or not same_inode(before, after)
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise InstallError('File changed during verification: ' + str(path))
        return bytes(result), hasher.hexdigest()
    finally:
        os.close(fd)


def load_json(raw):
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise InstallError('Duplicate JSON key')
            result[name] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique)
    except (UnicodeError, ValueError) as exc:
        raise InstallError('Invalid JSON metadata') from exc


def verify_kit(directory, sha256, *, local_target_only=False):
    if not re.fullmatch(r'[a-f0-9]{64}', sha256 or ''):
        raise InstallError('An independently trusted SHA256SUMS digest is required')
    directory = Path(directory).absolute()
    raw, actual = read_file(directory / 'SHA256SUMS', limit=64_000)
    if actual != sha256:
        raise InstallError('Trusted SHA256SUMS digest mismatch; nothing was installed')
    values = {}
    try:
        lines = raw.decode('ascii').splitlines()
    except UnicodeError as exc:
        raise InstallError('Invalid kit checksums') from exc
    for line in lines:
        match = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9._-]+)', line)
        if not match or match[2] in values:
            raise InstallError('Invalid or duplicate kit checksum')
        values[match[2]] = match[1]
    bundles = set(values) - COMMON_ASSETS
    permitted = {'vui-linux-' + target + '.zip' for target in TARGETS}
    # Remote upgrades keep the complete authenticated manifest, while omitting
    # only bundles for other machine targets. The machine target is detected
    # locally, never supplied by a request, flag value or environment variable.
    selected = COMMON_ASSETS | {'vui-linux-' + local_target() + '.zip'} if local_target_only else set(values)
    if (not COMMON_ASSETS <= values.keys() or not bundles <= permitted
            or len(bundles) not in (1, len(TARGETS))
            or not selected <= values.keys()
            or {p.name for p in directory.iterdir()} != selected | {'SHA256SUMS'}):
        raise InstallError('Complete fixed release kit required; unexpected or missing member')
    verified = {}
    for name, expected in sorted(values.items()):
        if name not in selected:
            continue
        raw, actual = read_file(directory / name, limit=MAX_CODE if name in CODE_ASSETS else MAX_ASSET,
                                keep=name in CODE_ASSETS)
        if actual != expected:
            raise InstallError('Kit checksum mismatch: ' + name)
        if name in CODE_ASSETS:
            verified[name] = raw
    record = load_json(verified['RELEASE.json'])
    if (not isinstance(record, dict) or type(record.get('schema')) is not int or record['schema'] != 1
            or record.get('platform') != 'linux-multi-cpython312'
            or not isinstance(record.get('targets'), list)
            or not all(isinstance(target, str) and target in TARGETS for target in record['targets'])
            or len(set(record['targets'])) != len(record['targets'])
            or {'vui-linux-' + target + '.zip' for target in record['targets']} != bundles
            or not isinstance(record.get('source_commit'), str)
            or not re.fullmatch(r'[a-f0-9]{40}', record.get('source_commit', ''))):
        raise InstallError('Unsupported or inconsistent kit release metadata')
    # The manifest checksum is the trust anchor. This additional check prevents
    # a builder from labelling dirty-working-tree control bytes as another commit.
    # Re-read into one bounded, rehashed snapshot; never extract or execute it.
    source_raw, source_hash = read_file(directory / 'vui-source.zip', limit=64_000_000)
    if source_hash != values['vui-source.zip']:
        raise InstallError('Source archive changed after verification')
    try:
        with zipfile.ZipFile(io.BytesIO(source_raw)) as source:
            if source.comment != record['source_commit'].encode():
                raise InstallError('Source archive commit differs from release metadata')
            names = source.namelist()
            if len(names) > 10000 or len(set(names)) != len(names):
                raise InstallError('Source archive has unsafe or duplicate members')
            for name, member in SOURCE_MEMBERS.items():
                entry = source.getinfo(member)
                if (entry.is_dir() or entry.flag_bits & 1 or entry.file_size > MAX_CODE
                        or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)):
                    raise InstallError('Source archive contains an unsafe control member')
                if digest(source.read(entry)) != values[name]:
                    raise InstallError('Control asset differs from exact-commit source: ' + name)
            for name, member in SOURCE_DEPENDENCIES.items():
                entry = source.getinfo(member)
                if (entry.is_dir() or entry.flag_bits & 1 or entry.file_size > MAX_CODE
                        or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)):
                    raise InstallError('Source archive contains an unsafe dependency member')
                verified[name] = source.read(entry)
    except (zipfile.BadZipFile, KeyError, RuntimeError) as exc:
        if isinstance(exc, InstallError):
            raise
        raise InstallError('Source archive is incomplete or invalid') from exc
    return verified, record


def verify_system_python():
    """Accept the fixed OS symlink chain, never a service-owned portable runtime."""
    current = SYSTEM_PYTHON
    for _ in range(40):
        trusted_parents(current)
        info = current.lstat()
        if not owned_by_root(info):
            raise InstallError('OS Python is not root-owned')
        if not stat.S_ISLNK(info.st_mode):
            check_regular(info, current, root=True)
            if info.st_mode & 0o022 or not info.st_mode & 0o111:
                raise InstallError('OS Python is writable or not executable')
            break
        target = Path(os.readlink(current))
        current = Path(os.path.abspath(target if target.is_absolute() else current.parent / target))
    else:
        raise InstallError('OS Python symlink chain is excessive')
    _, fingerprint = read_file(current, root=True, limit=MAX_INTERPRETER, keep=False)
    try:
        result = subprocess.run([str(SYSTEM_PYTHON), '-B', '-I', '-S', '-c', SYSTEM_PROBE],
                                env=ENV, cwd='/', capture_output=True, text=True, timeout=10, check=True)
    except subprocess.SubprocessError as exc:
        raise InstallError('Fixed OS Python lacks required standard-library/runtime support') from exc
    probe = load_json(result.stdout)
    version = probe.get('version') if isinstance(probe, dict) else None
    if (not isinstance(version, list) or len(version) != 2
            or any(type(value) is not int for value in version) or tuple(version) < (3, 9)):
        raise InstallError('Fixed OS /usr/bin/python3 >= 3.9 is required; no portable fallback')
    capabilities = probe.get('capabilities')
    if (set(probe) != {'version', 'capabilities'} or not isinstance(capabilities, dict)
            or set(capabilities) != PROBE_CAPABILITIES
            or any(value is not True for value in capabilities.values())):
        raise InstallError('OS Python requires sqlite3/ssl, tar data filters, Linux pidfd, WNOWAIT and process controls')
    # Recheck the pathname and binary after the bounded probe.
    if SYSTEM_PYTHON.resolve(strict=True) != current:
        raise InstallError('OS Python changed during verification')
    if read_file(current, root=True, limit=MAX_INTERPRETER, keep=False)[1] != fingerprint:
        raise InstallError('OS Python changed during verification')
    return {'system_python': str(SYSTEM_PYTHON), 'interpreter_resolved': str(current),
            'interpreter_sha256': fingerprint}


def local_target():
    arches = {'x86_64': 'x86_64', 'amd64': 'x86_64', 'aarch64': 'aarch64', 'arm64': 'aarch64'}
    arch = arches.get(platform.machine().lower())
    if not arch:
        raise InstallError('Unsupported Linux architecture')
    musl = (bool(list(Path('/lib').glob('ld-musl-*.so.1'))
                 or list(Path('/usr/lib').glob('ld-musl-*.so.1')))
            or platform.libc_ver()[0].lower() == 'musl')
    return arch + ('-musl' if musl else '-gnu')


def service_templates(verified, config):
    # Only bytes already bound to the caller's independently trusted manifest are
    # compiled. Never import a neighbouring module or the installed user payload.
    module = types.ModuleType('_vui_verified_service_templates')
    exec(compile(verified['service_support.py'], '<verified service_support.py>', 'exec'), module.__dict__)
    module.SYSTEMD_DIR = SYSTEMD_DIR
    module.OPENRC_DIR = OPENRC_DIR
    return module.service_files(config)


def inspect_instance(verified, record):
    raw, config_hash = read_file(CONFIG, root=True, mode=0o644, limit=MAX_JSON)
    config = load_json(raw)
    if (not isinstance(config, dict) or type(config.get('installer')) is not int
            or config['installer'] != 1 or config.get('ready') is not True
            or config.get('service_manager') not in ('systemd', 'openrc')
            or type(config.get('ipv6')) is not bool
            or config.get('target') != local_target()
            or config['target'] not in record['targets']
            or not re.fullmatch(r'/var/lib/v-ui/releases/[A-Za-z0-9][A-Za-z0-9._-]{0,100}/runtime/python/bin/python3',
                                config.get('bootstrap_python', ''))):
        raise InstallError('Existing managed installation is incomplete or incompatible')
    try:
        account = pwd.getpwnam('v-ui')
    except KeyError as exc:
        raise InstallError('Existing dedicated v-ui account is required') from exc
    if account.pw_uid == 0 or account.pw_gid == 0:
        raise InstallError('Dedicated v-ui account must be unprivileged')
    trusted_parents(ROOT)
    info = ROOT.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != account.pw_uid
            or info.st_gid != account.pw_gid or stat.S_IMODE(info.st_mode) != 0o700):
        raise InstallError('Existing managed data root has unsafe ownership or permissions')
    launcher, launcher_hash = read_file(LAUNCHER, root=True, mode=0o644)
    if launcher != verified['system_launcher.py']:
        raise InstallError('Existing launcher differs from the trusted kit')
    hashes = {str(CONFIG): config_hash, str(LAUNCHER): launcher_hash}
    templates = service_templates(verified, config)
    expected = ({SYSTEMD_DIR / name for name in ('v-ui.service', 'v-ui-http01.service', 'v-ui-http01.socket')}
                if config['service_manager'] == 'systemd'
                else {OPENRC_DIR / name for name in ('v-ui', 'v-ui-http01')})
    if set(templates) != expected:
        raise InstallError('Unexpected canonical service file set')
    for path, (text, mode) in templates.items():
        if mode != (0o644 if config['service_manager'] == 'systemd' else 0o755):
            raise InstallError('Unexpected canonical service mode')
        raw, fingerprint = read_file(path, root=True, mode=mode)
        if raw != text.encode():
            raise InstallError('Existing service differs from the trusted canonical template: ' + str(path))
        hashes[str(path)] = fingerprint
    return config, hashes


def destinations():
    paths = {CONTROL / name: (name, 0o644) for name in ('system_ops.py', 'ops_worker.py', 'release_tools.py', 'firewall_support.py', *SOURCE_DEPENDENCIES)} | {
        ENTRY: ('v-ui', 0o755)}
    manager = (load_json(read_file(CONFIG, root=True, limit=MAX_JSON)[0]).get('service_manager', 'systemd')
               if CONFIG.exists() else 'systemd')
    if manager == 'systemd':
        paths[SYSTEMD_DIR / 'v-ui-maintenance.service'] = ('v-ui-maintenance.service', 0o644)
    return paths


def check_command_alias():
    """Never adopt an unrelated PATH entry; an absent bin directory is creatable."""
    trusted_parents(COMMAND_ALIAS if COMMAND_ALIAS.parent.exists() else COMMAND_ALIAS.parent)
    if os.path.lexists(COMMAND_ALIAS):
        info = COMMAND_ALIAS.lstat()
        if (not stat.S_ISLNK(info.st_mode) or not owned_by_root(info)
                or os.readlink(COMMAND_ALIAS) != str(ENTRY)):
            raise InstallError('Unrelated v-ui command already exists; inspect manually')


def check_partial_control(verified):
    for path, (name, mode) in destinations().items():
        if os.path.lexists(path):
            raw, _ = read_file(path, root=True, mode=mode)
            if raw != verified[name]:
                raise InstallError('Partial control installation differs from this trusted kit; inspect manually')


def preflight(directory, sha256, *, local_target_only=False):
    """Authenticate the kit and OS interpreter before the online panel install."""
    if os.geteuid() != 0:
        raise InstallError('Explicit root invocation is required')
    if platform.system() != 'Linux':
        raise InstallError('Linux only; nothing was installed')
    verified, record = verify_kit(directory, sha256, local_target_only=local_target_only)
    check_command_alias()
    interpreter = verify_system_python()
    paths = destinations()
    if any(os.path.lexists(path) for path in (*paths, RECEIPT)):
        if not os.path.lexists(RECEIPT):
            # A known partial copy is repairable; unrelated bytes are not.
            check_partial_control(verified)
        else:
            previous = load_json(read_file(RECEIPT, root=True, mode=0o644, limit=MAX_JSON)[0])
            manager = previous.get('service_manager') if isinstance(previous, dict) else None
            if manager not in ('systemd', 'openrc'):
                raise InstallError('Existing control receipt is unrelated or incompatible')
            units = ({SYSTEMD_DIR / name: 0o644 for name in ('v-ui.service', 'v-ui-http01.service', 'v-ui-http01.socket')}
                     if manager == 'systemd' else {OPENRC_DIR / name: 0o755 for name in ('v-ui', 'v-ui-http01')})
            current = {CONFIG: 0o644, LAUNCHER: 0o644, **units}
            maintenance_unit = SYSTEMD_DIR / 'v-ui-maintenance.service'
            if manager == 'systemd' and str(maintenance_unit) in previous.get('files', {}):
                current[maintenance_unit] = 0o644
            raw, config_hash = read_file(CONFIG, root=True, mode=0o644, limit=MAX_JSON)
            config = load_json(raw)
            incomplete = (isinstance(config, dict) and type(config.get('installer')) is int
                          and config['installer'] == 1 and config.get('ready') is False
                          and config.get('service_manager', 'systemd') == manager)
            hashes = {str(CONFIG): config_hash}
            missing_instance = set()
            for path, mode in current.items():
                if path == CONFIG: continue
                if incomplete and not os.path.lexists(path):
                    missing_instance.add(str(path))
                    continue
                hashes[str(path)] = read_file(path, root=True, mode=mode)[1]
            # These are pre-upgrade service bytes. Their canonical replacement
            # is checked by the panel installer, then inspect_instance below.
            validate_existing(hashes, interpreter, manager, True, verified,
                              missing_instance=missing_instance)
    return verified, record, interpreter


def validate_existing(hashes, interpreter, manager, refresh, verified, *, missing_instance=()):
    paths = destinations()
    unit_dir = SYSTEMD_DIR if manager == 'systemd' else OPENRC_DIR
    unit_names = ('v-ui.service','v-ui-http01.service','v-ui-http01.socket') if manager == 'systemd' else ('v-ui','v-ui-http01')
    allowed_missing = {str(LAUNCHER)} | {str(unit_dir/name) for name in unit_names}
    if manager == 'systemd':
        allowed_missing.add(str(SYSTEMD_DIR / 'v-ui-maintenance.service'))
    if missing_instance and (not refresh or not set(missing_instance) <= allowed_missing):
        raise InstallError('Only guarded incomplete-instance preflight may defer missing service files')
    for path in (*paths, RECEIPT):
        trusted_parents(path)
    if not RECEIPT.exists() and not RECEIPT.is_symlink():
        present = [path for path in paths if path.exists() or path.is_symlink()]
        if present:
            if not refresh:
                raise InstallError('Unrelated or partial control files exist; explicit --refresh is required')
            # A failed publication deliberately removes the enabling receipt and
            # wrapper. Only an explicit retry of this exact accepted kit may
            # fill missing files, and every surviving byte must already match.
            # Never adopt an unknown, linked, wrong-mode or mixed-version file.
            check_partial_control(verified)
            return
        if refresh:
            raise InstallError('--refresh requires a complete matching managed receipt')
        return
    receipt = load_json(read_file(RECEIPT, root=True, mode=0o644, limit=MAX_JSON)[0])
    expected = set(hashes) | {str(path) for path in paths} | set(missing_instance)
    dependencies = {str(CONTROL / name) for name in PRE_DNS_SOURCE_DEPENDENCIES}
    dns_dependencies = {str(CONTROL / name) for name in DNS_SOURCE_DEPENDENCIES}
    core_dependencies = {str(CONTROL / name) for name in CORE_SOURCE_DEPENDENCIES}
    # Explicit refresh migrates only known complete historical closures. The
    # pre-network controller lacks firewall_support; older closures may also
    # lack the proxy dependencies or data-owner helper. Existing bytes must all
    # still match their receipt; no arbitrary subset or unreceipted file is used.
    firewall_dependency = {str(CONTROL / 'firewall_support.py')}
    historical = (dependencies, {str(CONTROL / 'data_owner.py')}, firewall_dependency,
                     dependencies | firewall_dependency,
                     {str(CONTROL / 'data_owner.py')} | firewall_dependency)
    maintenance = {str(CONTROL / name) for name in PRE_DNS_SOURCE_DEPENDENCIES
                   if name.startswith('maintenance_') or name in ('install_control_verifier.py', 'v-ui-maintenance.service')}
    if manager == 'systemd':
        maintenance.add(str(SYSTEMD_DIR / 'v-ui-maintenance.service'))
    # Permit only complete historical closures plus the whole new closure.
    pre_dns_missing = (set(),) + historical + (maintenance,) + tuple(item | maintenance for item in historical)
    # Every historical shape predates all five DNS helpers. Mixed partial DNS
    # closures were never published and must not be accepted as legacy.
    # Published pre-core DNS closures have every DNS helper, and no core helper.
    # Older closures lack all DNS and all core helpers. Never accept a partial
    # core group or derive old shapes by subtracting arbitrary current files.
    yaml_dependencies = {str(CONTROL / name) for name in YAML_SOURCE_DEPENDENCIES}
    known_missing = (yaml_dependencies,) + tuple(
        yaml_dependencies | older for older in
        ((core_dependencies,) + tuple(core_dependencies | dns_dependencies | item for item in pre_dns_missing)))
    missing = (expected - set(receipt['files']) if isinstance(receipt, dict)
               and isinstance(receipt.get('files'), dict) else set())
    legacy = (refresh and isinstance(receipt, dict) and isinstance(receipt.get('files'), dict)
              and missing in known_missing
              and set(receipt['files']) == expected - missing)
    if (not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS
            or type(receipt.get('schema')) is not int or receipt['schema'] != 1
            or receipt.get('service_manager') != manager
            or not isinstance(receipt.get('files'), dict) or (set(receipt['files']) != expected and not legacy)
            or receipt.get('system_python') != str(SYSTEM_PYTHON)
            or not isinstance(receipt.get('interpreter_resolved'), str)
            or not Path(receipt['interpreter_resolved']).is_absolute()
            or '..' in Path(receipt['interpreter_resolved']).parts
            or not isinstance(receipt.get('interpreter_sha256'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', receipt['interpreter_sha256'])
            or any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value)
                   for value in receipt['files'].values())):
        raise InstallError('Existing control receipt is unrelated or incompatible')
    actual = dict(hashes)
    for path, (_, mode) in paths.items():
        if str(path) in missing_instance:
            continue
        if legacy and str(path) in missing:
            if path.exists() or path.is_symlink():
                raise InstallError('Unreceipted dependency already exists; inspect manually')
            continue
        actual[str(path)] = read_file(path, root=True, mode=mode)[1]
    if any(receipt['files'][str(path)] != actual[str(path)] for path in paths
           if str(path) not in missing_instance and (not legacy or str(path) not in missing)):
        raise InstallError('Existing control code drift; refusing to re-endorse changed helpers or wrapper')
    if not refresh:
        if receipt['files'] != actual or any(receipt[key] != value for key, value in interpreter.items()):
            raise InstallError('Existing control receipt drift; explicit --refresh with a trusted kit is required')
        raise InstallError('Managed control already exists; explicit --refresh is required')
    # An OS upgrade or the ordinary managed installer may legitimately update
    # Python/config/launcher/units. inspect_instance and verify_system_python
    # independently checked their *current* trust and exact kit/canonical bytes.
    # Only explicit refresh can rebind those baselines; old control code itself
    # must still be intact. Never delete a receipt to bypass this transition.


@contextmanager
def installation_lock():
    trusted_parents(LOCK, lock=True)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    try:
        check_regular(os.fstat(fd), LOCK, root=True, mode=0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InstallError('Another installer or system operation is running') from exc
        if not same_inode(os.fstat(fd), LOCK.lstat()):
            raise InstallError('Installation lock pathname changed')
        yield
        if not same_inode(os.fstat(fd), LOCK.lstat()):
            raise InstallError('Installation lock pathname changed')
    finally:
        os.close(fd)  # Never unlink the lock inode.


def stage_file(path, raw, mode):
    trusted_parents(path)
    fd, name = tempfile.mkstemp(prefix='.vui-control-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        check_regular(Path(name).lstat(), name, root=True, mode=mode)
        return Path(name)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def publish(verified, receipt):
    """Prepare all bytes, then enable the wrapper last; interruptions fail closed."""
    staged = {}
    staged_alias = None
    changed = False
    try:
        check_command_alias()
        if not COMMAND_ALIAS.parent.exists():
            COMMAND_ALIAS.parent.mkdir(mode=0o755)
            COMMAND_ALIAS.parent.chmod(0o755)
        trusted_parents(COMMAND_ALIAS)
        fd, name = tempfile.mkstemp(prefix='.vui-command-', dir=COMMAND_ALIAS.parent)
        os.close(fd)
        Path(name).unlink()
        staged_alias = Path(name)
        os.symlink(str(ENTRY), staged_alias)
        for path, (name, mode) in destinations().items():
            staged[path] = stage_file(path, verified[name], mode)
        staged[RECEIPT] = stage_file(RECEIPT, (json.dumps(receipt, sort_keys=True, indent=2) + '\n').encode(), 0o644)
        # No code has changed yet. Disable the existing entry before replacing a
        # single helper. Receipt is removed too, so direct controller calls fail.
        changed = True
        ENTRY.unlink(missing_ok=True)
        RECEIPT.unlink(missing_ok=True)
        for path in destinations():
            if path != ENTRY:
                os.replace(staged[path], path)
        os.replace(staged[RECEIPT], RECEIPT)
        os.replace(staged[ENTRY], ENTRY)
        os.replace(staged_alias, COMMAND_ALIAS)
        for parent in {ENTRY.parent, COMMAND_ALIAS.parent, CONTROL}:
            fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    except BaseException:
        if changed:
            ENTRY.unlink(missing_ok=True)
            RECEIPT.unlink(missing_ok=True)
        raise
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)
        if staged_alias is not None:
            staged_alias.unlink(missing_ok=True)


def install(directory, sha256, *, refresh=False, local_target_only=False):
    verified, record, interpreter = preflight(directory, sha256, local_target_only=local_target_only)
    with installation_lock():
        config, hashes = inspect_instance(verified, record)
        validate_existing(hashes, interpreter, config['service_manager'], refresh, verified)
        files = dict(hashes)
        for path, (name, _) in destinations().items():
            files[str(path)] = digest(verified[name])
        receipt = {'schema': 1, 'service_manager': config['service_manager'],
                   **interpreter, 'files': files}
        publish(verified, receipt)
    if config['service_manager'] == 'systemd':
        start_maintenance_service()
    return receipt


def start_maintenance_service():
    # Starting an already active daemon must not kill its in-flight upgrade.
    for args in (['daemon-reload'], ['enable', '--now', 'v-ui-maintenance.service']):
        subprocess.run(['/usr/bin/systemctl', *args], env=ENV, cwd='/',
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=45, check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kit', required=True, help='Complete independently accepted release kit directory')
    parser.add_argument('--sha256', required=True, help='Independently trusted SHA-256 of its entire SHA256SUMS')
    parser.add_argument('--refresh', action='store_true',
                        help='Refresh an intact controller or complete a receipt-free partial copy of this exact kit')
    parser.add_argument('--preflight', action='store_true',
                        help='Only authenticate the kit, command path and fixed OS interpreter; do not install')
    parser.add_argument('--local-target-only', action='store_true',
                        help='Retain the complete accepted manifest but download only the locally detected target bundle')
    args = parser.parse_args(argv)
    if (not sys.dont_write_bytecode or not sys.flags.isolated or not sys.flags.no_site
            or Path(sys.executable).resolve() != SYSTEM_PYTHON.resolve()):
        raise InstallError('Invoke with the fixed OS /usr/bin/python3 -B -I -S; no portable runtime')
    if args.preflight:
        if args.refresh:
            raise InstallError('--preflight does not refresh an installation')
        preflight(args.kit, args.sha256, local_target_only=args.local_target_only)
        print('可信全局 v-ui 预检通过；尚未更改任何安装。')
        return
    install(args.kit, args.sha256, refresh=args.refresh, local_target_only=args.local_target_only)
    print('可信系统控制组件已安装；独立维护服务已启动，账户、sudoers 和 SUID 设置保持。')


if __name__ == '__main__':
    try:
        main()
    except (InstallError, OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        print('V-UI 控制组件安装已拒绝：' + str(exc), file=sys.stderr)
        raise SystemExit(1)
