"""Opt-in, exact-source qualification of the real managed system controller.

This is a disposable GitHub ubuntu-24.04 fixture, never an operational installer.
Environment markers are intentional opt-in guardrails, not unforgeable proof of
host ownership or user authorization. The operator must separately approve it.
Ordinary test discovery skips the privileged gate; its workflow is opt-in only.
The non-root supervisor reuses the original oneclick fixture's lifecycle hooks;
only its cleanup is replaced. Production installer/controller/worker code is not
patched. Root helpers below are bounded, source-bound test harness code, and never
import a package payload or deserialize a callable supplied by the service user.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import grp
import hashlib
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import platform
import pwd
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(SOURCE))
ROOT = Path('/var/lib/v-ui')
CONFIG_DIR = Path('/etc/v-ui')
CONTROL = Path('/usr/local/lib/v-ui')
ENTRY = Path('/usr/local/sbin/v-ui')
COMMAND_ALIAS = Path('/usr/local/bin/v-ui')
LOCK = Path('/run/lock/v-ui-install.lock')
UNITS = ('v-ui.service', 'v-ui-http01.service', 'v-ui-http01.socket')
UNIT_DIR = Path('/etc/systemd/system')
RESERVED = (ROOT, CONFIG_DIR, CONTROL, ENTRY, COMMAND_ALIAS, LOCK)
AUTH_PREFIX = 'ROOT-SYSTEMD:'
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
       'HOME': '/tmp', 'PYTHONDONTWRITEBYTECODE': '1'}
MAX_OUTPUT = 512 * 1024
DEPENDENCIES = ('scripts/managed_system_acceptance.py', 'scripts/install_ops_control.py',
                'scripts/low_resource_root.py', 'scripts/ops_menu_smoke.py',
                'scripts/service_support.py', 'tests/test_oneclick_system.py',
                'tests/loopback_helpers.py', 'deploy/system_ops.py',
                'scripts/fetch_managed_ops_kit.py', 'scripts/publish_release.py',
                'tests/online_install_helpers.py',
                '.github/workflows/managed-system-control.yml', 'requirements-test.txt')
REQUIRED_PHASES = ('baseline', 'control_installed', 'menu_cancel', 'stop_start', 'restart',
                   'backup_restore', 'stage_activate_rollback', 'post_stop_failure',
                   'recover_restore', 'partial_refresh', 'cleanup')


CHECKPOINT_NAMES = ('stop_menu', 'stop_proof', 'stop_timestamps', 'start_menu', 'start_api')
CHECKPOINT_LIMIT = 2 * len(CHECKPOINT_NAMES)
ERROR_CODES = frozenset({
    'backup_result_missing',
    'bounded_child_failed',
    'bundle_source_metadata_mismatch',
    'cancel_changed_services',
    'checkout_head_mismatch',
    'checkpoint_contract_violation',
    'cleanup_ownership_not_established',
    'command_output_limit',
    'control_install_failed',
    'duplicate_json',
    'evidence_directory_must_be_empty',
    'exact_source_commit_required',
    'explicit_exact_head_disposable_runner_authorization_required',
    'fixture_account_replaced',
    'fixture_configuration_changed',
    'fixture_configuration_replaced',
    'harness_dependency_blob_mismatch',
    'install_changed_service_state',
    'insufficient_menu_cleanup_budget',
    'invalid_stop_timestamp',
    'kit_source_or_target_mismatch',
    'no_cleanup_rights',
    'other_fixture_gate_must_not_be_active',
    'owned_resource_replaced',
    'panel_response_not_established',
    'partial_files_not_claimed',
    'partial_fixture_target_changed',
    'partial_install_adopted_without_refresh',
    'privileged_cleanup_unconfirmed',
    'qualification_deadline',
    'qualification_phases_incomplete',
    'real_worker_identity_not_observed',
    'real_worker_identity_unobserved',
    'real_systemd_cgroup_v2_required',
    'recovery_preservation_failed',
    'restore_data_not_established',
    'same_kit_partial_refresh_failed',
    'supervisor_must_be_nonroot',
    'ubuntu_24_04_required',
    'ubuntu_x86_64_only',
    'unclaimable_resource',
    'unclaimed_partial_installation',
    'unclaimed_resources_remain',
    'unexpected_baseline_command',
    'unexpected_fixture_account',
    'unit_not_fixture_canonical',
    'unknown_service_fixture_action',
    'worker_did_not_demote',
    'wrapper_not_accepted_kit',
})


class QualificationError(RuntimeError):
    """Only bounded diagnostic codes are emitted; never raw child output."""


def safe_error(exc):
    """Static diagnostics only: exception text/argv/paths are never serialized."""
    if type(exc) is QualificationError:
        code=exc.args[0] if len(exc.args)==1 and type(exc.args[0]) is str else None
        return {'error_type':'QualificationError',
                'error_code':code if code in ERROR_CODES else 'qualification_refused'}
    for kind, label, code in ((subprocess.TimeoutExpired,'TimeoutExpired','child_timeout'),
            (subprocess.SubprocessError,'SubprocessError','child_process_error'),
            (AssertionError,'AssertionError','assertion_failed'),
            (OSError,'OSError','host_operation_error'),
            (RuntimeError,'RuntimeError','runtime_refused'),
            (KeyboardInterrupt,'KeyboardInterrupt','interrupted'),
            (SystemExit,'SystemExit','interrupted')):
        if isinstance(exc,kind):return {'error_type':label,'error_code':code}
    return {'error_type':'UnexpectedError','error_code':'unexpected_error'}


def stop_timestamp(raw):
    """A bounded positive uint64 observation, never command-dispatch evidence."""
    if type(raw) is not str or not re.fullmatch(r'[1-9][0-9]{0,19}\n?',raw):
        raise QualificationError('invalid_stop_timestamp')
    value=int(raw)
    if value>2**64-1:raise QualificationError('invalid_stop_timestamp')
    return value


def require_gate(commit, environ=None, *, uid=None):
    environ = os.environ if environ is None else environ
    uid = os.geteuid() if uid is None else uid
    if not isinstance(commit, str) or not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise QualificationError('exact_source_commit_required')
    if uid == 0:
        raise QualificationError('supervisor_must_be_nonroot')
    if any(environ.get(key) for key in ('VUI_ROOT_RESOURCE_CONFIG', 'VUI_SYSTEM_INSTALL_TEST')):
        raise QualificationError('other_fixture_gate_must_not_be_active')
    expected = {'VUI_MANAGED_SYSTEM_ACCEPTANCE': AUTH_PREFIX + commit,
                'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                'ImageOS': 'ubuntu24', 'RUNNER_ARCH': 'X64'}
    if any(environ.get(key) != value for key, value in expected.items()):
        raise QualificationError('explicit_exact_head_disposable_runner_authorization_required')
    if platform.system() != 'Linux' or platform.machine().lower() not in ('x86_64', 'amd64'):
        raise QualificationError('ubuntu_x86_64_only')


def file_hash(path):
    with Path(path).open('rb') as stream:
        checksum = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
        return checksum.hexdigest()


def checked_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise QualificationError('duplicate_json')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def source_contract(kit, sha256, commit, *, source=SOURCE):
    """Read-only trust checks precede every privileged command and cleanup right."""
    from scripts import install_ops_control
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True, timeout=15).strip()
    if head != commit:
        raise QualificationError('checkout_head_mismatch')
    subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--'], cwd=source, check=True, timeout=15)
    verified, record = install_ops_control.verify_kit(kit, sha256)
    if record.get('source_commit') != commit or record.get('targets') != ['x86_64-gnu']:
        raise QualificationError('kit_source_or_target_mismatch')
    hashes = {}
    with zipfile.ZipFile(Path(kit) / 'vui-source.zip') as archive:
        for name in DEPENDENCIES:
            expected = archive.read(name)
            if (source / name).is_symlink() or (source / name).read_bytes() != expected:
                raise QualificationError('harness_dependency_blob_mismatch')
            hashes[name] = hashlib.sha256(expected).hexdigest()
    bundle = Path(kit) / 'vui-linux-x86_64-gnu.zip'
    with zipfile.ZipFile(bundle) as archive:
        manifest = checked_json(archive.read('MANIFEST.json'))
    if (manifest.get('source_commit') != commit or manifest.get('kind') != 'release'
            or manifest.get('targets') != ['x86_64-gnu']
            or manifest.get('version') != record.get('version')
            or manifest.get('release_id') != record.get('release_id')):
        raise QualificationError('bundle_source_metadata_mismatch')
    # The initial fixture executes install.sh from this exact kit, never a dirty
    # checkout helper. The source-bound test hooks are separate from product code.
    return {'source_commit': commit, 'tree': subprocess.check_output(
                ['git', 'rev-parse', 'HEAD^{tree}'], cwd=source, text=True, timeout=15).strip(),
            'manifest_sha256': sha256, 'bundle_sha256': file_hash(bundle),
            'source_archive_sha256': file_hash(Path(kit) / 'vui-source.zip'),
            'harness_blobs': hashes, 'release_id': manifest['release_id'],
            'supervisor_python': {'resolved':str(Path(sys.executable).resolve()),
                                  'sha256':file_hash(Path(sys.executable).resolve()),
                                  'version':platform.python_version()}}, verified


def empty_host():
    from scripts import low_resource_root
    # Reuse the stricter integrated oneclick preflight, including every systemd
    # search path, drop-in prefix and enablement symlink. Port binding remains the
    # real installer's pre-account root check; no privileged probe is hidden here.
    result = low_resource_root.preflight(reserved=RESERVED, port=False)
    if not Path('/run/systemd/system').is_dir() or not Path('/sys/fs/cgroup/cgroup.controllers').is_file():
        raise QualificationError('real_systemd_cgroup_v2_required')
    release = Path('/etc/os-release').read_text()
    if not re.search(r'^ID=ubuntu$', release, re.M) or not re.search(r'^VERSION_ID="?24\.04"?$', release, re.M):
        raise QualificationError('ubuntu_24_04_required')
    return result


def fingerprint(path):
    info = Path(path).lstat()
    result = {'device': info.st_dev, 'inode': info.st_ino, 'uid': info.st_uid,
            'gid': info.st_gid, 'type': stat.S_IFMT(info.st_mode),
            'mode': stat.S_IMODE(info.st_mode), 'links': info.st_nlink if stat.S_ISREG(info.st_mode) else 0}
    if stat.S_ISLNK(info.st_mode):
        result['target'] = os.readlink(path)
    return result


def cleanup_paths():
    return {str(path) for path in RESERVED} | {str(UNIT_DIR / name) for name in UNITS}


def validate_cleanup_ledger(ledger, current):
    """A pure identity check, shared with the isolated root cleanup program."""
    allowed = {'/var/lib/v-ui', '/etc/v-ui', '/usr/local/lib/v-ui', '/usr/local/sbin/v-ui', '/usr/local/bin/v-ui',
               '/run/lock/v-ui-install.lock', '/etc/systemd/system/v-ui.service',
               '/etc/systemd/system/v-ui-http01.service', '/etc/systemd/system/v-ui-http01.socket'}
    if not isinstance(ledger, dict) or not ledger or not set(ledger) <= allowed or set(current) != set(ledger):
        raise RuntimeError('cleanup_not_owned')
    for path, recorded in ledger.items():
        actual = current[path]
        if path == '/usr/local/bin/v-ui':
            if (actual != recorded or actual['type'] != 0o120000 or actual['uid'] != 0
                    or actual.get('target') != '/usr/local/sbin/v-ui'):
                raise RuntimeError('cleanup_identity_changed')
            continue
        if actual != recorded or actual['type'] not in (0o040000, 0o100000):
            raise RuntimeError('cleanup_identity_changed')
        if actual['type'] == 0o100000 and actual['links'] != 1:
            raise RuntimeError('cleanup_linked_file')
        if actual['mode'] & 0o022:
            raise RuntimeError('cleanup_writable_resource')


def owned_child_signal(pid, number):
    """Only a still-owned direct child, retained with WNOWAIT, can be signalled."""
    try:
        observed = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        raise RuntimeError('child_identity_lost') from None
    if os.getpgid(pid) != pid:
        raise RuntimeError('child_group_changed')
    # The unreaped direct child pins this process-group ID even after exit.
    os.killpg(pid, number)
    return observed


def process_identity(pid, proc=Path('/proc')):
    base=proc/str(pid)
    fields=dict(line.split(':',1) for line in (base/'status').read_text().splitlines() if ':' in line)
    argv=(base/'cmdline').read_bytes()
    if len(argv)>8192:raise RuntimeError('process_arguments_too_large')
    return {'pid':pid,'starttime':int((base/'stat').read_text().rsplit(')',1)[1].split()[19]),
            'ppid':int(fields['PPid']),'uid':[int(value) for value in fields['Uid'].split()],
            'gid':[int(value) for value in fields['Gid'].split()],
            'groups':[int(value) for value in fields.get('Groups','').split()],
            'cap_eff':fields.get('CapEff','').strip(),
            'argv':[item.decode() for item in argv.split(b'\0') if item],
            'exe':os.readlink(base/'exe')}


def stable_identity(pid, parent=None, reader=process_identity):
    try:
        first=reader(pid);second=reader(pid)
        if first!=second or (parent is not None and first['ppid']!=parent['pid']):return None
        if parent is not None and reader(parent['pid'])!=parent:return None
        return first
    except (FileNotFoundError,ProcessLookupError,PermissionError):return None


def _root_menu(request):
    """Isolated root harness: actual wrapper, PTY, bounded own-child supervision."""
    import errno, fcntl, hashlib, json, os, pty, re, select, signal, subprocess, termios, time
    if os.geteuid() != 0 or set(request) != {'action', 'arguments', 'expect_failure', 'work_seconds'}:
        raise RuntimeError('invalid_root_menu_request')
    action, arguments = request['action'], request['arguments']
    if type(request['work_seconds']) is not int or not 1<=request['work_seconds']<=600:raise RuntimeError('invalid_menu_budget')
    if action not in ('status', 'start', 'stop', 'restart', 'backup', 'restore', 'activate', 'stage', 'recover-restore', 'cancel'):
        raise RuntimeError('invalid_root_menu_action')
    if not isinstance(arguments, list) or any(not isinstance(value, str) or '\n' in value or '\r' in value for value in arguments):
        raise RuntimeError('invalid_root_menu_arguments')
    if action in ('restore', 'stage'):
        if len(arguments) != 2 or not re.fullmatch(r'/var/lib/v-ui/(?:backups/maintenance-[a-f0-9]{32}\.zip|qualification-inputs/(?:candidate|invalid)\.zip)', arguments[0]) or not re.fullmatch(r'[a-f0-9]{64}', arguments[1]):
            raise RuntimeError('invalid_fixed_input')
    elif action == 'activate':
        if len(arguments) != 1 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', arguments[0]):
            raise RuntimeError('invalid_release_id')
    elif arguments:
        raise RuntimeError('unexpected_menu_arguments')
    master, slave = pty.openpty()
    def session():
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
    child = subprocess.Popen(['/usr/local/sbin/v-ui'], stdin=slave, stdout=slave, stderr=slave,
                             preexec_fn=session, close_fds=True, env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'})
    os.close(slave)
    output = bytearray(); cursor = 0; observations = {}; worker_fds={};unobserved=0
    deadline = time.monotonic() + request['work_seconds']
    def sample():
        nonlocal unobserved
        # Enumerate only this still-owned root child's descendants. No environ,
        # system-wide /proc scan, unrelated PIDs or arbitrary process signalling.
        pending=[(child.pid,None)]; seen=set()
        while pending and len(seen)<64:
            pid,parent=pending.pop()
            if pid in seen: continue
            seen.add(pid)
            try:
                row=stable_identity(pid,parent)
                if row is None:unobserved+=1;continue
                with open('/proc/%d/task/%d/children' % (pid,pid)) as stream:
                    children=[int(value) for value in stream.read(4096).split()]
                if stable_identity(pid,parent)!=row:unobserved+=1;continue
                pending.extend((value,row) for value in children)
                if row['argv'] == ['/usr/bin/python3','-B','-I','-S','/usr/local/lib/v-ui/ops_worker.py']:
                    key=(pid,row['starttime'])
                    if row['exe']!=os.path.realpath('/usr/bin/python3'):raise RuntimeError('worker_executable_changed')
                    if key not in worker_fds:
                        fd=os.pidfd_open(pid)
                        if stable_identity(pid,parent)!=row:os.close(fd);unobserved+=1;continue
                        worker_fds[key]=fd
                    observations[key]=row
            except (FileNotFoundError,ProcessLookupError): pass
    def until(marker):
        nonlocal cursor
        encoded=marker.encode()
        while True:
            sample()
            at=output.find(encoded,cursor)
            if at>=0:
                chunk=bytes(output[cursor:at+len(encoded)]).decode(errors='replace');cursor=at+len(encoded);return chunk
            if time.monotonic()>=deadline: raise RuntimeError('menu_deadline')
            if select.select([master],[],[],.01)[0]:
                try: part=os.read(master,65536)
                except OSError as exc:
                    if exc.errno!=errno.EIO: raise
                    part=b''
                if not part: raise RuntimeError('menu_closed_early')
                output.extend(part)
                if len(output)>524288: raise RuntimeError('menu_output_limit')
    try:
        until('请选择: ')
        command='backup' if action=='cancel' else action
        os.write(master,(command+'\n').encode())
        for argument, prompt in zip(arguments, (('备份绝对路径（留空取消）: ','可信 SHA-256（留空取消）: ') if action=='restore' else
                ('本地升级包绝对路径（留空取消）: ','可信 SHA-256（留空取消）: ') if action=='stage' else ('已准备版本 ID（留空取消）: ',))):
            until(prompt);os.write(master,(argument+'\n').encode())
        if action!='status':
            until('[yes/N]: ');os.write(master,b'no\n' if action=='cancel' else b'yes\n')
        text=until('请选择: ')
        failed='操作未完成或状态无法确认' in text
        if failed != bool(request['expect_failure']): raise RuntimeError('unexpected_menu_result')
        if not failed and action not in ('status','cancel') and not any(value in text for value in ('操作完成；','候选准备完成，未激活:','服务已运行；')):
            raise RuntimeError('success_not_established')
        if action=='cancel' and '已取消' not in text: raise RuntimeError('cancel_not_established')
        os.write(master,b'0\n')
        end=time.monotonic()+10
        while os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT) is None:
            if time.monotonic()>end: raise RuntimeError('menu_exit_deadline')
            time.sleep(.01)
        # Do not reap before group cleanup: descendants cannot outlive a reused PGID.
        owned_child_signal(child.pid, signal.SIGTERM)
        owned_child_signal(child.pid, signal.SIGKILL)
        if any(not select.select([fd],[],[],0)[0] for fd in worker_fds.values()):raise RuntimeError('worker_exit_unconfirmed')
        code=child.wait(timeout=5)
        if code: raise RuntimeError('menu_nonzero_exit')
        result={'action':action,'expected_failure':failed,'workers':list(observations.values()),
                'transcript_sha256':hashlib.sha256(output).hexdigest(),'transcript_bytes':len(output)}
        result['unobserved_samples']=unobserved
        match=re.search(r'私有备份: /var/lib/v-ui/backups/(maintenance-[a-f0-9]{32}\.zip)\r?\nSHA-256: ([a-f0-9]{64})',text)
        if match: result.update(backup_name=match[1],backup_sha256=match[2])
        return result
    finally:
        try:
            try:
                owned_child_signal(child.pid,signal.SIGTERM)
                # Product cancellation may spend three separate 45s emergency
                # service-command budgets, followed by worker-group cleanup.
                # Never mislabel that as part of its 600s normal work deadline.
                end=time.monotonic()+160
                while os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT) is None and time.monotonic()<end:time.sleep(.02)
                owned_child_signal(child.pid,signal.SIGKILL)
                child.wait(timeout=3)
            except ChildProcessError: pass
            except RuntimeError as exc:
                if str(exc)!='child_identity_lost': raise
        finally:
            for fd in worker_fds.values():os.close(fd)
            os.close(master)


def _root_cleanup(request):
    """Delete only supervisor-claimed resources, using nofollow parent dirfds."""
    import grp, hashlib, json, os, pwd, shutil, stat, subprocess
    if os.geteuid()!=0 or set(request)!={'ledger','account','unit_hashes','stop_only'}:raise RuntimeError('invalid_cleanup_request')
    ledger=request['ledger']; account=request['account']; hashes=request['unit_hashes']
    current={path:fingerprint(path) for path in ledger}
    validate_cleanup_ledger(ledger,current)
    actual=pwd.getpwnam('v-ui')
    if {'uid':actual.pw_uid,'gid':actual.pw_gid,'home':actual.pw_dir}!=account or actual.pw_uid<=0 or actual.pw_gid<=0:
        raise RuntimeError('cleanup_account_changed')
    units=('v-ui.service','v-ui-http01.service','v-ui-http01.socket')
    if set(hashes)!=set(units):raise RuntimeError('cleanup_unit_ownership_incomplete')
    group=grp.getgrnam('v-ui')
    if group.gr_gid!=account['gid'] or group.gr_mem or any(user.pw_gid==group.gr_gid and user.pw_name!='v-ui' for user in pwd.getpwall()):
        raise RuntimeError('cleanup_group_changed')
    for name, expected in hashes.items():
        path='/etc/systemd/system/'+name
        if name not in units or path not in ledger or hashlib.sha256(open(path,'rb').read()).hexdigest()!=expected:
            raise RuntimeError('cleanup_unit_changed')
    # All ownership validation precedes even the first service mutation.
    rows=[]
    for name in ('v-ui-http01.socket','v-ui.service','v-ui-http01.service'):
        if name not in hashes:continue
        row={'unit':name}
        try:
            result=subprocess.run(['/usr/bin/systemctl','stop',name],capture_output=True,timeout=12)
            row['stop_returncode']=result.returncode
        except subprocess.TimeoutExpired:row['stop_returncode']='timeout'
        result=subprocess.run(['/usr/bin/systemctl','show','--property=ActiveState,MainPID','--value',name],capture_output=True,text=True,timeout=5)
        values=result.stdout.splitlines()
        if result.returncode or not ('inactive' in values and (name.endswith('.socket') or '0' in values)):
            subprocess.run(['/usr/bin/systemctl','kill','--kill-whom=all','--signal=KILL',name],capture_output=True,timeout=5)
            result=subprocess.run(['/usr/bin/systemctl','show','--property=ActiveState,MainPID','--value',name],capture_output=True,text=True,timeout=5)
            values=result.stdout.splitlines()
            if result.returncode or not ('inactive' in values and (name.endswith('.socket') or '0' in values)):
                raise RuntimeError('cleanup_stop_unconfirmed')
        rows.append(row)
    if not os.path.isfile('/sys/fs/cgroup/cgroup.controllers'):
        raise RuntimeError('cleanup_cgroup_v2_missing')
    for name in hashes:
        path='/sys/fs/cgroup/system.slice/'+name
        if os.path.lexists(path):
            pending=[os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)]
            try:
                while pending:
                    fd=pending.pop()
                    try:
                        info=os.fstat(fd)
                        if info.st_uid!=0 or info.st_mode&0o022:raise RuntimeError('cleanup_untrusted_cgroup')
                        process_fd=os.open('cgroup.procs',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
                        try:
                            if os.read(process_fd,32768).strip():raise RuntimeError('cleanup_cgroup_not_empty')
                        finally:os.close(process_fd)
                        with os.scandir(fd) as entries:
                            for entry in entries:
                                if entry.is_symlink():raise RuntimeError('cleanup_symbolic_cgroup')
                                if entry.is_dir(follow_symlinks=False):
                                    pending.append(os.open(entry.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd))
                    finally:os.close(fd)
            finally:
                for fd in pending:os.close(fd)
    if request['stop_only']:
        return {'owned_resources_removed':False,'stops':rows,'reason':'privileged_child_cleanup_unconfirmed'}
    # Remove only the two exact enablement links generated by the fixture.
    for folder,name in (('multi-user.target.wants','v-ui.service'),('sockets.target.wants','v-ui-http01.socket')):
        path='/etc/systemd/system/'+folder+'/'+name
        if os.path.lexists(path):
            info=os.lstat(path)
            if not stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or os.readlink(path)!='/etc/systemd/system/'+name or name not in hashes:
                raise RuntimeError('cleanup_unknown_enablement')
            os.unlink(path)
    if not shutil.rmtree.avoids_symlink_attacks:raise RuntimeError('safe_rmtree_unavailable')
    # Recheck immediately before deletion and never traverse a replaced parent.
    ordered=sorted(ledger,key=lambda path: (path in ('/var/lib/v-ui','/etc/v-ui','/usr/local/lib/v-ui'),path))
    for path in ordered:
        validate_cleanup_ledger({path:ledger[path]},{path:fingerprint(path)})
        pieces=path.strip('/').split('/');fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            for piece in pieces[:-1]:
                child=os.open(piece,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=child
            info=os.stat(pieces[-1],dir_fd=fd,follow_symlinks=False)
            if (info.st_dev,info.st_ino)!=(ledger[path]['device'],ledger[path]['inode']):raise RuntimeError('cleanup_leaf_changed')
            if stat.S_ISDIR(info.st_mode):shutil.rmtree(pieces[-1],dir_fd=fd)
            else:os.unlink(pieces[-1],dir_fd=fd)
        finally:os.close(fd)
    subprocess.run(['/usr/bin/systemctl','daemon-reload'],check=True,timeout=12,capture_output=True)
    subprocess.run(['/usr/sbin/userdel','v-ui'],check=True,timeout=12,capture_output=True)
    try:group=grp.getgrnam('v-ui')
    except KeyError:pass
    else:
        if group.gr_gid!=account['gid'] or group.gr_mem or any(user.pw_gid==group.gr_gid for user in pwd.getpwall()):
            raise RuntimeError('cleanup_group_changed')
        subprocess.run(['/usr/sbin/groupdel','v-ui'],check=True,timeout=12,capture_output=True)
    return {'owned_resources_removed':True,'stops':rows}


def root_program(function):
    imports='import os, stat\nfrom pathlib import Path\n'
    helpers = (fingerprint, validate_cleanup_ledger) if function is _root_cleanup else (owned_child_signal,process_identity,stable_identity)
    return imports + '\n'.join(inspect.getsource(value) for value in (*helpers,function)) + \
        '\nimport json, sys\nprint(json.dumps('+function.__name__+'(json.loads(sys.argv[1])),sort_keys=True))\n'


def service_program(action, value=None):
    """Fixed synthetic-fixture mutations, executed only as the v-ui account."""
    if action not in ('state','seed','mutate','make-invalid','prepare-recovery'):
        raise QualificationError('unknown_service_fixture_action')
    return ('''import hashlib,json,os,shutil,sqlite3,uuid
+from pathlib import Path
+root=Path('/var/lib/v-ui'); marker=root/'data/qualification-marker.txt'
+action=''' + repr(action) + '''
+if os.geteuid()==0:raise RuntimeError('service_uid_required')
+if action=='seed':
+    fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
+    with os.fdopen(fd,'w') as stream:stream.write('before-backup\\n')
+elif action=='mutate':marker.write_text('after-backup\\n')
+elif action=='make-invalid':
+    path=root/'qualification-inputs/invalid.zip';path.write_bytes(b'known invalid synthetic backup');path.chmod(0o600)
+elif action=='prepare-recovery':
+    parent=root/'recovery';parent.mkdir(exist_ok=True,mode=0o700)
+    old=parent/('before-'+uuid.uuid4().hex);os.rename(root/'data',old)
+    shutil.copytree(old,root/'data');(root/'data/qualification-marker.txt').write_text('interrupted-replacement\\n')
+    journal=root/'RESTORE_PENDING.json';journal.write_text(json.dumps({'old':str(old.relative_to(root))}));journal.chmod(0o600)
+state={'current':json.loads((root/'CURRENT.json').read_text()),'data_exists':(root/'data').is_dir(),'journal_exists':(root/'RESTORE_PENDING.json').exists()}
+if marker.exists():state['marker_sha256']=hashlib.sha256(marker.read_bytes()).hexdigest()
+if (root/'data/v-ui.db').exists():
+    with sqlite3.connect('file:'+str(root/'data/v-ui.db')+'?mode=ro',uri=True) as db:state['database_integrity']=db.execute('PRAGMA integrity_check').fetchone()[0]
+state['recovery_names']=sorted(path.name for path in (root/'recovery').iterdir()) if (root/'recovery').is_dir() else []
+print(json.dumps(state))
+''').replace('\n+','\n')


class Qualification:
    def __init__(self, kit, provenance, verified, output, *, budget=1800):
        self.kit=Path(kit);self.provenance=provenance;self.verified=verified;self.output=Path(output)
        self.deadline=time.monotonic()+min(1800,budget);self.creation_started=False;self.menu_in_flight=False
        self.cleanup_uncertain=False
        self.ledger={};self.account=None;self.unit_hashes={};self.work=None;self.config_hash=None;self.control_records={}
        self.report={'schema':1,'provenance':provenance,'outcome':'running','phases':[],'checkpoints':[],
                     'root_programs':{name:hashlib.sha256(root_program(value).encode()).hexdigest()
                                      for name,value in (('menu',_root_menu),('cleanup',_root_cleanup))}}

    def remaining(self, limit):
        value=min(limit,self.deadline-time.monotonic())
        if value<=0:raise QualificationError('qualification_deadline')
        return value

    def record(self, phase, **details):
        # Freeze each observation before later lifecycle operations mutate ledgers.
        self.report['phases'].append(json.loads(json.dumps({'phase':phase,**details})))
        self.output.write_text(json.dumps(self.report,sort_keys=True,indent=2)+'\n')

    @contextmanager
    def checkpoint(self, name):
        rows=self.report['checkpoints']
        if (name not in CHECKPOINT_NAMES or any(row['name']==name for row in rows)
                or len(rows)+2>CHECKPOINT_LIMIT):
            raise QualificationError('checkpoint_contract_violation')
        rows.append({'name':name,'status':'begin'})
        self.output.write_text(json.dumps(self.report,sort_keys=True,indent=2)+'\n')
        try:
            yield
        except BaseException as exc:
            rows.append({'name':name,'status':'failed',**safe_error(exc)})
            raise
        else:
            rows.append({'name':name,'status':'passed'})
        finally:
            self.output.write_text(json.dumps(self.report,sort_keys=True,indent=2)+'\n')

    def finalize_report(self):
        actual={row['phase'] for row in self.report['phases']}
        clean=any(row['phase']=='cleanup' and row.get('owned_resources_removed') is True
                  for row in self.report['phases'])
        if self.report['outcome']=='awaiting_cleanup':
            self.report['outcome']='passed' if (set(REQUIRED_PHASES)<=actual and clean
                and not self.cleanup_uncertain and not self.menu_in_flight) else 'failed'
        self.output.write_text(json.dumps(self.report,sort_keys=True,indent=2)+'\n')

    def run(self, command, *, timeout=30, check=True, env=None):
        try:
            result=subprocess.run(command,capture_output=True,text=True,timeout=self.remaining(timeout),env=env or ENV)
            if len(result.stdout)+len(result.stderr)>MAX_OUTPUT:raise QualificationError('command_output_limit')
            if check and result.returncode:raise QualificationError('bounded_child_failed')
            return result
        except BaseException:
            # subprocess.run can reap sudo without proving every privileged
            # descendant exited. Keep the fixture on this disposable runner.
            if command and Path(command[0]).name=='sudo':self.cleanup_uncertain=True
            raise

    def root(self, function, request, *, timeout=650):
        code=root_program(function)
        result=self.run(['/usr/bin/sudo','-n','/usr/bin/python3','-B','-I','-S','-c',code,json.dumps(request)],timeout=timeout)
        return checked_json(result.stdout)

    def service(self, action):
        code=service_program(action)
        result=self.run(['/usr/bin/sudo','-n','-u','v-ui','/usr/bin/python3','-B','-I','-S','-c',code])
        return checked_json(result.stdout)

    def capture_owned(self, *, baseline_replacement=False, entry_replacement=False):
        if not self.creation_started:raise QualificationError('no_cleanup_rights')
        try:account=pwd.getpwnam('v-ui')
        except KeyError:return
        if account.pw_uid<=0 or account.pw_gid<=0 or account.pw_dir!=str(ROOT):raise QualificationError('unexpected_fixture_account')
        identity={'uid':account.pw_uid,'gid':account.pw_gid,'home':account.pw_dir}
        if self.account is not None and identity!=self.account:raise QualificationError('fixture_account_replaced')
        self.account=identity
        config=CONFIG_DIR/'service.json'
        if not config.is_file() or config.is_symlink():raise QualificationError('unclaimed_partial_installation')
        value=checked_json(config.read_bytes())
        if value.get('installer')!=1 or value.get('domain')!='vpn.example.test' or value.get('admin')!='installer-admin' or value.get('certificate_mode')!='provided' or value.get('bind')!='127.0.0.1':
            raise QualificationError('fixture_configuration_changed')
        if self.config_hash is not None and not baseline_replacement and file_hash(config)!=self.config_hash:
            raise QualificationError('fixture_configuration_replaced')
        self.config_hash=file_hash(config)
        from scripts import service_support
        expected_units=service_support.service_files(value)
        for path in (*RESERVED,*(UNIT_DIR/name for name in UNITS)):
            if not os.path.lexists(path):continue
            row=fingerprint(path)
            mode=0o777 if path==COMMAND_ALIAS else 0o700 if path==ROOT else 0o755 if path in (CONFIG_DIR,CONTROL,ENTRY) else 0o600 if path==LOCK else 0o644
            kind=stat.S_IFLNK if path==COMMAND_ALIAS else stat.S_IFDIR if path in (ROOT,CONFIG_DIR,CONTROL) else stat.S_IFREG
            owner=account.pw_uid if path==ROOT else 0
            if row['type']!=kind or row['mode']!=mode or row['uid']!=owner or (kind==stat.S_IFREG and row['links']!=1):
                raise QualificationError('unclaimable_resource')
            if path==COMMAND_ALIAS and row.get('target')!=str(ENTRY):
                raise QualificationError('unclaimable_resource')
            old=self.ledger.get(str(path))
            if old is not None and row!=old and not (baseline_replacement and path.parent==UNIT_DIR) and not (entry_replacement and path in (ENTRY,COMMAND_ALIAS)):
                raise QualificationError('owned_resource_replaced')
            if path.parent==UNIT_DIR:
                text,_=expected_units[path]
                if path.read_bytes()!=text.encode():raise QualificationError('unit_not_fixture_canonical')
                self.unit_hashes[path.name]=file_hash(path)
            if path==ENTRY and path.read_bytes()!=self.verified['v-ui']:raise QualificationError('wrapper_not_accepted_kit')
            self.ledger[str(path)]=row

    def command(self, command):
        if command[:2]!=['sudo','bash'] or Path(command[2])!=SOURCE/'install.sh':raise QualificationError('unexpected_baseline_command')
        self.creation_started=True
        return ['/usr/bin/sudo','-n','/usr/bin/env','-i','PATH=/usr/sbin:/usr/bin:/sbin:/bin',
                'LANG=C.UTF-8','LC_ALL=C.UTF-8','HOME=/','PYTHONDONTWRITEBYTECODE=1',
                '/bin/bash',str(self.kit/'install.sh'),*command[3:]]

    def installer_receipt(self, phase, text):
        # Never retain installer output, credentials, certificate material or the
        # cleartext password used by the existing oneclick fixture.
        self.capture_owned(baseline_replacement=True)
        self.record('installer_'+phase,transcript_sha256=hashlib.sha256(text.encode()).hexdigest())

    def installed(self):
        self.capture_owned()

    def states(self):
        from deploy import system_ops
        backend=system_ops.Services('systemd')
        return backend.snapshot()

    def stopped(self):
        from deploy import system_ops
        system_ops.Services('systemd').prove_stopped()

    def menu(self, action, *arguments, failure=False):
        work_seconds=int(min(600,self.remaining(800)-180))
        if work_seconds<1:raise QualificationError('insufficient_menu_cleanup_budget')
        self.menu_in_flight=True
        result=self.root(_root_menu,{'action':action,'arguments':list(arguments),'expect_failure':failure,
                                    'work_seconds':work_seconds},timeout=work_seconds+175)
        if action not in ('status','cancel') and not result['workers']:
            raise QualificationError('real_worker_identity_unobserved')
        for row in result['workers']:
            if row['uid']!=[self.account['uid']]*4 or row['gid']!=[self.account['gid']]*4 or row['groups'] or int(row['cap_eff'],16):
                raise QualificationError('worker_did_not_demote')
        self.menu_in_flight=False
        self.record('menu_action',**result)
        return result

    def control_install(self, refresh=False):
        command=['/usr/bin/sudo','-n','/usr/bin/python3','-B','-I','-S',str(self.kit/'install_ops_control.py'),
                 '--kit',str(self.kit),'--sha256',self.provenance['manifest_sha256']]
        if refresh:command.append('--refresh')
        result=self.run(command,timeout=180,check=False)
        self.capture_owned(entry_replacement=True)
        if result.returncode==0:
            from deploy import system_ops
            system_ops.verify_installation()
            self.control_records={str(path):{'identity':fingerprint(path),'sha256':file_hash(path)}
                                  for path in (ENTRY,CONTROL/'ops-control.json',CONTROL/'ops_worker.py')}
        return result.returncode

    def partial_refresh(self):
        from deploy import system_ops
        system_ops.verify_installation()
        paths=(ENTRY,CONTROL/'ops-control.json',CONTROL/'ops_worker.py')
        if set(self.control_records)!={str(path) for path in paths}:raise QualificationError('partial_files_not_claimed')
        for path in paths:
            recorded=self.control_records[str(path)]
            actual=fingerprint(path)
            if (actual!=recorded['identity'] or actual['uid']!=0 or actual['type']!=stat.S_IFREG
                    or actual['links']!=1 or actual['mode']!=(0o755 if path==ENTRY else 0o644)
                    or file_hash(path)!=recorded['sha256']):
                raise QualificationError('partial_fixture_target_changed')
        self.run(['/usr/bin/sudo','-n','/usr/bin/rm','--',*(str(path) for path in paths)])
        self.ledger.pop(str(ENTRY),None)
        if self.control_install()==0:raise QualificationError('partial_install_adopted_without_refresh')
        if self.control_install(refresh=True)!=0:raise QualificationError('same_kit_partial_refresh_failed')
        system_ops.verify_installation();self.record('partial_refresh',constructed_same_kit_residue=True)

    def api_ready(self, api, expected):
        import http.client
        end=time.monotonic()+self.remaining(20)
        while time.monotonic()<end:
            try:
                status,_=api('/api/auth/me')
                if status==expected:return
            except (OSError,http.client.HTTPException):pass
            time.sleep(.1)
        raise QualificationError('panel_response_not_established')

    def stop_start(self, api):
        with self.checkpoint('stop_menu'):
            self.menu('stop')
        with self.checkpoint('stop_proof'):
            # Keep the production exact-three-unit inactive/cgroup proof. The
            # timestamps below neither substitute for nor weaken that proof.
            self.stopped()
        with self.checkpoint('stop_timestamps'):
            times={}
            for name in UNITS:
                raw=self.run(['/usr/bin/systemctl','show',
                    '--property=InactiveEnterTimestampMonotonic','--value',name]).stdout
                times[name]=stop_timestamp(raw)
            # Requires= plus socket Before=service can complete responder first
            # inside the socket stop transaction. Completion order cannot prove
            # controller dispatch order; the latter has a source-bound unit test.
            self.record('stop_timestamp_observation',inactive_monotonic=times,
                        interpretation='unit_completion_times_not_command_dispatch_order')
        with self.checkpoint('start_menu'):
            self.menu('start')
        with self.checkpoint('start_api'):
            self.api_ready(api,200)
        self.record('stop_start',states=self.states())

    def finish(self, case, command, api, login):
        self.capture_owned();self.record('baseline',states=self.states())
        before=self.states()
        if self.control_install()!=0:raise QualificationError('control_install_failed')
        from deploy import system_ops
        manager,account=system_ops.verify_installation()
        if manager!='systemd' or account.pw_uid!=self.account['uid'] or self.states()!=before:raise QualificationError('install_changed_service_state')
        receipt=checked_json((CONTROL/'ops-control.json').read_bytes())
        self.record('control_installed',receipt=receipt,paths=self.ledger)
        self.menu('status');self.menu('cancel')
        if self.states()!=before:raise QualificationError('cancel_changed_services')
        self.record('menu_cancel')
        # Activate the real socket responder before testing all three stop times.
        import http.client
        connection=http.client.HTTPConnection('127.0.0.1',80,timeout=5)
        try:connection.request('GET','/login');response=connection.getresponse();response.read();case.assertEqual(response.status,404)
        finally:connection.close()
        self.stop_start(api)
        before=self.states();self.menu('restart');case.assertEqual(self.states(),before)
        self.record('restart')
        before_data=self.service('seed')
        backup=self.menu('backup')
        if not backup.get('backup_name'):raise QualificationError('backup_result_missing')
        self.service('mutate')
        self.menu('restore',str(ROOT/'backups'/backup['backup_name']),backup['backup_sha256'])
        restored=self.service('state')
        if restored['marker_sha256']!=before_data['marker_sha256'] or restored.get('database_integrity')!='ok':raise QualificationError('restore_data_not_established')
        self.api_ready(api,401);login();self.api_ready(api,200)
        self.record('backup_restore',backup=backup,state=restored)
        from scripts.low_resource_root import synthetic_bundle
        candidate=self.work/'candidate.zip'
        derivation=synthetic_bundle(self.kit/'vui-linux-x86_64-gnu.zip',candidate,self.provenance['source_commit'])
        self.run(['/usr/bin/sudo','-n','-u','v-ui','/bin/mkdir','-m','700',str(ROOT/'qualification-inputs')])
        self.run(['/usr/bin/sudo','-n','/usr/bin/install','-o','v-ui','-g','v-ui','-m','600',str(candidate),str(ROOT/'qualification-inputs/candidate.zip')])
        before=self.service('state');topology=self.states()
        staged=self.menu('stage',str(ROOT/'qualification-inputs/candidate.zip'),derivation['b_sha256'])
        if not staged['workers']:raise QualificationError('real_worker_identity_not_observed')
        case.assertEqual(self.service('state')['current'],before['current']);case.assertEqual(self.states(),topology)
        activated=self.menu('activate',derivation['b_release_id'])
        case.assertEqual(self.service('state')['current']['release_id'],derivation['b_release_id'])
        rollback=self.menu('activate',derivation['a_release_id'])
        case.assertEqual(self.service('state')['current']['release_id'],derivation['a_release_id'])
        self.record('stage_activate_rollback',derivation=derivation,stage=staged,activate=activated,rollback=rollback)
        self.service('make-invalid')
        invalid=hashlib.sha256(b'known invalid synthetic backup').hexdigest()
        self.menu('restore',str(ROOT/'qualification-inputs/invalid.zip'),invalid,failure=True);self.stopped()
        self.record('post_stop_failure',state=self.service('state'))
        original=self.service('state');self.service('prepare-recovery')
        self.menu('recover-restore');self.stopped();recovered=self.service('state')
        if recovered['marker_sha256']!=original['marker_sha256'] or recovered['journal_exists'] or not any(name.startswith('interrupted-') for name in recovered['recovery_names']):
            raise QualificationError('recovery_preservation_failed')
        self.menu('start');self.record('recover_restore',state=recovered)
        # Construct a same-kit partial publication, not a claimed disk-fault test.
        self.partial_refresh()

    def cleanup(self):
        if not self.creation_started:return
        self.capture_owned()
        if not self.account:
            if any(os.path.lexists(path) for path in RESERVED):raise QualificationError('unclaimed_resources_remain')
            return
        if not self.ledger:raise QualificationError('cleanup_ownership_not_established')
        # Reserve a separate, bounded teardown window even if the work budget
        # expired. Never delete data after an unconfirmed root-menu child exit.
        self.deadline=max(self.deadline,time.monotonic()+120)
        result=self.root(_root_cleanup,{'ledger':self.ledger,'account':self.account,
                        'unit_hashes':self.unit_hashes,'stop_only':self.menu_in_flight or self.cleanup_uncertain},timeout=115)
        self.record('cleanup',**result)
        if not result['owned_resources_removed']:raise QualificationError('privileged_cleanup_unconfirmed')


@contextmanager
def fixture_environment(values):
    original=dict(os.environ)
    try:
        os.environ.clear();os.environ.update(ENV);os.environ.update(values);yield
    finally:
        os.environ.clear();os.environ.update(original)


def run_qualification(args):
    require_gate(args.source_commit)
    provenance,verified=source_contract(args.kit,args.sha256,args.source_commit)
    preflight=empty_host()  # No cleanup callback or privileged command before this.
    output=Path(args.evidence_dir)
    if output.exists() and any(output.iterdir()):raise QualificationError('evidence_directory_must_be_empty')
    output.mkdir(parents=True,exist_ok=True,mode=0o700)
    qualification=Qualification(args.kit,provenance,verified,output/'managed-system.json')
    qualification.record('preflight',**preflight)
    with tempfile.TemporaryDirectory(prefix='vui-managed-qualification-') as temporary:
        qualification.work=Path(temporary)
        # The old fixture expects a sidecar. Keep it outside the immutable kit.
        bundle=qualification.work/'baseline.zip'
        import shutil
        shutil.copyfile(Path(args.kit)/'vui-linux-x86_64-gnu.zip',bundle)
        bundle.with_suffix('.zip.sha256').write_text(provenance['bundle_sha256']+'  baseline.zip\n')
        from scripts import low_resource_root
        previous_path=list(sys.path)
        try:
            sys.path.insert(0,str(SOURCE/'tests'))
            spec=importlib.util.spec_from_file_location('_vui_actual_oneclick_fixture',SOURCE/'tests/test_oneclick_system.py')
            module=importlib.util.module_from_spec(spec)
            with fixture_environment({'VUI_SYSTEM_INSTALL_TEST':'1','VUI_RELEASE_BUNDLE':str(bundle)}):
                spec.loader.exec_module(module)
                case=module.OneClickSystemTests('test_actual_offline_installer_permissions_socket_and_autostart')
                with patch.object(low_resource_root,'optional_gate',return_value=qualification), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    try:
                        case.test_actual_offline_installer_permissions_socket_and_autostart()
                        qualification.report['outcome']='awaiting_cleanup'
                    except BaseException as exc:
                        # The original fixture also owns sudo/PTY children. An
                        # exception is not proof that those descendants exited.
                        qualification.cleanup_uncertain=True
                        qualification.report.update(outcome='failed',**safe_error(exc))
                        raise
                    finally:
                        try:qualification.cleanup()
                        except BaseException as exc:
                            diagnostic=safe_error(exc)
                            qualification.report.update(outcome='failed',cleanup_error=diagnostic['error_type'],
                                                        cleanup_error_code=diagnostic['error_code'])
                            raise
                        finally:
                            qualification.finalize_report()
        finally:sys.path[:]=previous_path
    actual={row['phase'] for row in qualification.report['phases']}
    if not set(REQUIRED_PHASES)<=actual or qualification.report['outcome']!='passed':raise QualificationError('qualification_phases_incomplete')
    return qualification.report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kit',type=Path,required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--evidence-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        run_qualification(args)
    except BaseException as exc:
        diagnostic=safe_error(exc)
        print('Managed system qualification did not pass: '+diagnostic['error_type']+
              ':'+diagnostic['error_code'],file=sys.stderr)
        return 1
    print('Exact-head managed system qualification passed; synthetic private data was not exported.')
    return 0


if __name__=='__main__':raise SystemExit(main())
