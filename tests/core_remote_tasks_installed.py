"""Real installed daemon tasks on an empty GitHub-hosted systemd host.

The Root-only CI bootstrap issues an explicitly fake ordinary-grant binding to
exercise Root authorization/daemon execution. HTTP ordinary identity is checked
separately by core_switch_systemd. No official component release is fabricated.
Bearer values only cross private stdin pipes to the actual service-UID client.
"""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import types
import uuid

FIXTURE = Path('/run/vui-core-switch-fixture')
CONTROL = Path('/usr/local/lib/v-ui')
UNIT = 'v-ui-maintenance.service'
PANEL = 'v-ui.service'
PHASE = 'ENTRY'


def stage(name):
    global PHASE
    PHASE = name


def require(value):
    if not value:
        raise ValueError('INSTALLED_REMOTE_CORE_ASSERTION')


def installed(stack):
    """Compile the independent Root fixture before its receipt-bound loader."""
    path = FIXTURE/'runner.py'
    info = FIXTURE.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    stack.callback(os.close, fd)
    before = os.fstat(fd)
    def identity(row):
        return (row.st_dev, row.st_ino, row.st_mode, row.st_uid, row.st_gid,
                row.st_nlink, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
    require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
            and stat.S_IMODE(before.st_mode) == 0o600 and 0 < before.st_size <= 4_000_000)
    raw = os.pread(fd, before.st_size+1, 0)
    require(len(raw) == before.st_size and identity(os.fstat(fd)) == identity(before)
            and identity(path.lstat()) == identity(before))
    module = types.ModuleType('_vui_remote_ci_bootstrap')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    module.fixed_directory(stack, FIXTURE, private=True)
    return module.installed_modules(stack)


# This process is genuinely demoted, with no supplementary groups. Installed
# maintenance_client is inert non-Root code; all mutations reach the real socket.
CLIENT = r'''
import json,os,runpy,socket,sys
from pathlib import Path
assert os.geteuid()!=0
transport=False
original_lstat=Path.lstat
def observed_lstat(path,*args,**kwargs):
 global transport
 try:return original_lstat(path,*args,**kwargs)
 except FileNotFoundError:
  if str(path) in ('/run/v-ui-maintenance','/run/v-ui-maintenance/control.sock'):transport=True
  raise
Path.lstat=observed_lstat
class ObservedSocket(socket.socket):
 def connect(self,address):
  global transport
  try:return super().connect(address)
  except (FileNotFoundError,ConnectionRefusedError):
   if address=='/run/v-ui-maintenance/control.sock':transport=True
   raise
socket.socket=ObservedSocket
client=runpy.run_path('/usr/local/lib/v-ui/maintenance_client.py')
for line in sys.stdin:
 transport=False
 try:
  value=client['request'](json.loads(line))
  result={'accepted':True,'value':value}
 except BaseException:
  result={'accepted':False}
  if transport:result['transport']=True
 print(json.dumps(result,separators=(',',':')),flush=True)
'''


class ReadTransportUnavailable(ValueError):
    pass


class Peer:
    def __init__(self, ops, account, stack):
        self.child = subprocess.Popen([str(ops.PYTHON), '-B', '-I', '-S', '-c', CLIENT],
            user=account.pw_uid, group=account.pw_gid, extra_groups=[], cwd='/', env=ops.ENV,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        stack.callback(self.close)

    def request(self, value, *, accepted=True):
        # No retries of an unknown submission. Even a rejected nonce is sent once.
        import select
        self.child.stdin.write(json.dumps(value, separators=(',', ':'))+'\n')
        self.child.stdin.flush()
        require(select.select([self.child.stdout], [], [], 8)[0])
        row = json.loads(self.child.stdout.readline())
        if row == {'accepted':False,'transport':True}:
            raise ReadTransportUnavailable('SOCKET_READINESS')
        require(type(row) is dict and row.get('accepted') is accepted)
        require(set(row) == ({'accepted', 'value'} if accepted else {'accepted'}))
        return row.get('value')

    def close(self):
        if self.child.poll() is None:
            self.child.stdin.close()
            try:
                self.child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.child.kill(); self.child.wait(timeout=8)
        self.child.stdout.close()


def property_value(unit, field):
    return subprocess.check_output(['systemctl', 'show', unit, '-p', field, '--value'],
                                   text=True, timeout=10).strip()


def wait_idle(peer, deadline):
    while True:
        row = peer.request(dict(protocol=1, operation='status'))
        require(type(row.get('busy')) is bool)
        if not row['busy']:
            return
        require(time.monotonic() < deadline)
        time.sleep(.1)


def wait_task(peer, row, *, state='succeeded'):
    identity = row['id']
    require(re.fullmatch('[a-f0-9]{32}', identity) and row['state'] == 'queued')
    deadline = time.monotonic()+650
    while row['state'] in ('queued', 'running'):
        require(time.monotonic() < deadline)
        time.sleep(.1)
        row = peer.request(dict(protocol=1, operation='core_get', task_id=identity))
    require(row['id'] == identity and row['state'] == state and row['phase'] == 'finished')
    wait_idle(peer, deadline)
    return row


def restart(peer, rows):
    """Restart only the independent daemon after every task is durably final."""
    old = property_value(UNIT, 'MainPID')
    subprocess.run(['systemctl', 'restart', UNIT], check=True, timeout=50,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    require(property_value(UNIT, 'MainPID') not in ('0', old))
    # systemctl restart returned after Type=simple; wait only for socket readiness.
    deadline = time.monotonic()+20
    while True:
        try:
            current = peer.request(dict(protocol=1, operation='core_get', task_id=rows[0]['id']))
            break
        except ReadTransportUnavailable:
            require(time.monotonic() < deadline)
            time.sleep(.1)
    require(current == rows[0])
    for row in rows[1:]:
        require(peer.request(dict(protocol=1, operation='core_get', task_id=row['id'])) == row)
    wait_idle(peer, deadline)


def writer_peer(ops, account, stack):
    # The same kernel lease used by real data_owner; this independent test
    # process is outside v-ui.service and survives its intentional stop.
    script = r"""import fcntl,os,stat,sys
assert os.geteuid()!=0
fd=os.open('/var/lib/v-ui/.data.owner.lease',os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK)
info=os.fstat(fd)
assert stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid() and info.st_nlink==1
assert stat.S_IMODE(info.st_mode)==0o600
fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
print('WRITER_HELD',flush=True)
sys.stdin.read(1)
os.close(fd)
"""
    child = subprocess.Popen([str(ops.PYTHON), '-B', '-I', '-S', '-c', script],
        user=account.pw_uid, group=account.pw_gid, extra_groups=[], cwd='/', env=ops.ENV,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    def release():
        if child.poll() is None:
            child.stdin.close()
            try: child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait(timeout=8)
        child.stdout.close()
    stack.callback(release)
    import select
    require(select.select([child.stdout], [], [], 8)[0] and child.stdout.readline() == 'WRITER_HELD\n')
    return release


def interrupted_backup(ops, account, authority, peer, request, stack, *, leave_pending=False):
    stage('WRITER_CONFLICT')
    release = writer_peer(ops, account, stack)
    failed = wait_task(peer, peer.request(request), state='failed')
    require(failed.get('error') == 'CORE_TASK_FAILED' and property_value(PANEL, 'ActiveState') == 'inactive')
    stage('PENDING_DURABILITY')
    journal = authority.backup_recovery()
    require(journal is not None and journal['transaction'] == failed['id'] and journal['sequence'] == 1
            and journal['before']['revision'] == request['parameters']['selection_revision'])
    require(authority.admission(task_id=failed['id'], live=False)['phase'] == 'mutating')
    saved = ops.decode_json(ops.trusted_bytes(Path('/var/lib/v-ui-maintenance/core-tasks')/(failed['id']+'.json'), 60000))
    require('_backup' not in saved and 'result' not in failed)
    restart(peer, [failed])
    require(authority.backup_recovery() == journal and property_value(PANEL, 'ActiveState') == 'inactive')
    require(peer.request(request) == failed)  # Same nonce never replays the failed backup.
    observed = wait_task(peer, peer.request(dict(protocol=1, operation='core_observe')))
    pending = dict(recovery_kind='backup', transaction=failed['id'], sequence=1,
                   selection_revision=request['parameters']['selection_revision'])
    require(observed['result']['pending'] == pending)
    release()
    if leave_pending:
        # The caller will use the actual interactive Root menu while the panel
        # is stopped. This helper does not issue a recovery socket request.
        return failed
    parameters = dict(recovery_kind='backup', pending_transaction=failed['id'], pending_sequence=1,
                      selection_revision=pending['selection_revision'])
    recover = dict(request, request_id=uuid.uuid4().hex, action='recover', parameters=parameters)
    mismatch = dict(recover, request_id=uuid.uuid4().hex, parameters={**parameters, 'pending_sequence':2})
    peer.request(mismatch, accepted=False)
    require(authority.backup_recovery() == journal and property_value(PANEL, 'ActiveState') == 'inactive')
    stage('EXPLICIT_RECOVERY')
    result = wait_task(peer, peer.request(recover))
    require(result['result'] == dict(outcome='backup_recovered', selection_revision=pending['selection_revision'], panel_running=True))
    require(authority.backup_recovery() is None and property_value(PANEL, 'ActiveState') == 'active')
    require(peer.request(recover) == result and peer.request(request) == failed)
    restart(peer, [result, failed])
    require(property_value(PANEL, 'ActiveState') == 'active' and authority.backup_recovery() is None)
    return result


def probe(ops, control, mode):
    stage('VERIFY_INSTALLATION')
    manager, account = ops.verify_installation()
    require(manager == 'systemd' and account.pw_uid != 0)
    expected = hashlib.sha256(ops.trusted_bytes(ops.RECEIPT, ops.MAX_JSON)).hexdigest()
    authority = ops._load_core_authority(expected).CoreAuthority()
    with control._modules(ops) as modules:
        files = modules.selection_files.SelectionFiles()
        status = files.read_status(_context=True)
        require(status['outcome'] == 'core_selected')
        parameters = dict(selection_revision=status['selection_revision'])
    fake = dict(main_id=2_000_000_000 + int.from_bytes(os.urandom(3), 'big'),
                main_digest=hashlib.sha256(os.urandom(32)).hexdigest())
    stage('ISSUE_TEST_BINDING')
    _, token = authority.issue(**fake, scopes=['core_backup'], seconds=1800)
    with ExitStack() as stack:
        peer = Peer(ops, account, stack)
        wait_idle(peer, time.monotonic()+20)
        action = 'backup'
        nonce = uuid.uuid4().hex
        request = dict(protocol=1, operation='core_submit', token=token, **fake,
                       request_id=nonce, action=action, parameters=parameters)
        try:
            pid = property_value(UNIT, 'MainPID')
            if mode in ('backup-recovery','backup-pending'):
                result = interrupted_backup(ops, account, authority, peer, request, stack,
                                            leave_pending=mode=='backup-pending')
                authority.revoke_main(**fake)
                peer.request(dict(request, request_id=uuid.uuid4().hex), accepted=False)
                return dict(id=result['id'], action='backup' if mode=='backup-pending' else 'recover', state=result['state'],
                            selection_revision=parameters['selection_revision'])
            stage('BACKUP_TASK')
            result = wait_task(peer, peer.request(request))
            require(property_value(UNIT, 'MainPID') == pid)
            require(result['result']['outcome'] == 'backup_created')
            require(result['result']['panel_running'] is True)
            stage('NONCE_REPLAY')
            # Same nonce returns the durable task; changing intent is rejected.
            require(peer.request(request) == result)
            conflict = dict(request, parameters={**parameters, 'selection_revision':'0'*64})
            peer.request(conflict, accepted=False)
            require(peer.request(dict(protocol=1, operation='core_lookup', token=token,
                                     **fake, request_id=nonce)) == result)
            if mode == 'backup':
                require(result['result']['backup_id'] == result['id'])
                require(not any(key in json.dumps(result) for key in ('backup_name', 'backup_sha256', '_backup', token)))
                # Exact private map exists only after a real successful archive.
                saved = ops.decode_json(ops.trusted_bytes(Path('/var/lib/v-ui-maintenance/core-tasks')/(result['id']+'.json'), 60000))
                mapping = saved['_backup']
                require(mapping['task_id'] == result['id'] and mapping['installation_id'])
                require(re.fullmatch(r'core-maintenance-[a-f0-9]{32}\.zip', mapping['name'])
                        and re.fullmatch('[a-f0-9]{64}', mapping['sha256']))
                before = (property_value(PANEL, 'MainPID'), ops.trusted_bytes(Path('/etc/v-ui/core-selection.json')))
                stage('STALE_REVISION')
                stale_request = dict(request, request_id=uuid.uuid4().hex,
                                     parameters=dict(selection_revision='0'*64))
                rejected = wait_task(peer, peer.request(stale_request), state='failed')
                require(authority.admission(task_id=rejected['id'])['phase'] == 'preflight')
                require((property_value(PANEL, 'MainPID'), ops.trusted_bytes(Path('/etc/v-ui/core-selection.json'))) == before)
                stage('DAEMON_RESTART')
                restart(peer, [result, rejected])
                require(peer.request(request) == result)
                require((property_value(PANEL, 'MainPID'), ops.trusted_bytes(Path('/etc/v-ui/core-selection.json'))) == before)
            stage('ROOT_REVOCATION')
            authority.revoke_main(**fake)
            peer.request(dict(request, request_id=uuid.uuid4().hex), accepted=False)
            require(peer.request(dict(protocol=1, operation='core_get', task_id=result['id'])) == result)
            return dict(id=result['id'], action=action, state=result['state'],
                        selection_revision=result['result'].get('selection_revision', parameters['selection_revision']))
        finally:
            authority.revoke_main(**fake)


def main():
    require(sys.platform == 'linux' and os.geteuid() == 0 and sys.executable == '/usr/bin/python3'
            and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
            and os.getenv('GITHUB_ACTIONS') == 'true' and os.getenv('RUNNER_ENVIRONMENT') == 'github-hosted'
            and os.getenv('GITHUB_REPOSITORY') == 'ForceMind/V-UI'
            and Path(__file__) == FIXTURE/'remote-tasks.py'
            and len(sys.argv) == 2 and sys.argv[1] in ('backup', 'backup-recovery','backup-pending'))
    with ExitStack() as stack:
        stage('ROOT_BOOTSTRAP')
        ops, control = installed(stack)
        result = probe(ops, control, sys.argv[1])
    print(json.dumps(result, separators=(',', ':')))


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        # No raw request, exception, traceback, token or private map in CI logs.
        print('VUI_INSTALLED_CORE_REMOTE_TASK_FAILED:'+PHASE, file=sys.stderr)
        raise SystemExit(1)
