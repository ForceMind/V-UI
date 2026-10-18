"""Packaged managed-menu consumer, with simulated root trust and fake services.

No root operation, global wrapper installation, system service or host setting is
performed. The controller/worker/release-tools bytes and Python come from the
actual installed package. Only the test drivers supply private fixed paths and
simulate root-owned configuration; SQLite, leases, pipes, PTY and candidate
application startup/authentication health checks are real, running without root.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
import uuid

from scripts.ops_menu_smoke import (MenuSession, _lease_busy, _process_identity,
    content_snapshot, file_digest, fixture_bundle)

HARNESS_VERSION = 'managed-package-consumer-v1'
FIXTURE_VERSION = 'simulated-root-trust-fake-services-v1'
PROMPT = '请选择: '
CONFIRM = '操作可能中断面板和证书响应；失败后不自动恢复启动，停止未确认时需检查状态。确认 [yes/N]: '
SUCCESS = '操作完成；服务状态已核验。'
FAILURE = '操作未完成或状态无法确认。'
WRAPPER_BYTES = (b'#!/bin/sh\n'
    b'# Fixed root-owned entry point. No escalation, bootstrap runtime or user imports.\n'
    b'exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/ LANG=C LC_ALL=C '
    b'/usr/bin/python3 -B -I -S /usr/local/lib/v-ui/system_ops.py "$@"\n')
UNITS = ('v-ui.service', 'v-ui-http01.service', 'v-ui-http01.socket')
WRITER_LOCKS = ('runtime/xray/owner.lock', 'runtime/sing-box/owner.lock',
                'certificates/.worker.lock')
SUCCESS_ACTIONS = ['stage', 'backup', 'restore', 'activate', 'activate',
                   'stop', 'recover-restore', 'start']


def _copy_control(payload, control):
    """Check package provenance before copying bytes; never execute the wrapper."""
    manifest = json.loads((payload / 'MANIFEST.json').read_text())
    sources = {'system_ops.py': 'deploy/system_ops.py',
               'ops_worker.py': 'deploy/ops_worker.py',
               'release_tools.py': 'app/release_tools.py', 'v-ui': 'deploy/v-ui',
               'data_owner.py': 'app/data_owner.py',
               'proxy_store.py': 'app/services/proxy_store.py',
               'upstream_profile.py': 'app/services/upstream_profile.py',
                   'inbound_policy.py': 'app/services/inbound_policy.py'}
    digests = {}
    for destination, relative in sources.items():
        source = payload / relative
        raw = source.read_bytes()
        expected = manifest['files'][relative]
        assert not source.is_symlink() and len(raw) == expected['size']
        checksum = hashlib.sha256(raw).hexdigest()
        assert checksum == expected['sha256'], 'Controller differs from package manifest'
        if destination == 'v-ui':
            assert raw == WRAPPER_BYTES, 'Unexpected global wrapper bytes'
        (control / destination).write_bytes(raw)
        (control / destination).chmod(0o600)
        digests[relative] = checksum
    return digests


def _seed_fake_data(data):
    data.mkdir(mode=0o700)
    with sqlite3.connect(data / 'v-ui.db') as db:
        db.executescript('CREATE TABLE users(id INTEGER, username TEXT);'
            'CREATE TABLE inbounds(id INTEGER, remark TEXT);'
            'CREATE TABLE admin_sessions(token_hash TEXT);'
            'CREATE TABLE subscription_grants(revoked INTEGER);'
            "INSERT INTO users VALUES(1,'managed-fixture-only');"
            "INSERT INTO inbounds VALUES(1,'original-fake-node');"
            "INSERT INTO admin_sessions VALUES('synthetic-session-digest');"
            'INSERT INTO subscription_grants VALUES(0);')
    (data / 'v-ui.db').chmod(0o600)
    (data / 'fake-user-file').write_bytes(b'original synthetic private data\n')
    (data / 'fake-user-file').chmod(0o600)


def _database_values(data):
    with sqlite3.connect(f'file:{data / "v-ui.db"}?mode=ro', uri=True) as db:
        assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        return {'nodes': db.execute('SELECT remark FROM inbounds').fetchall(),
                'sessions': db.execute('SELECT token_hash FROM admin_sessions').fetchall(),
                'grants': db.execute('SELECT revoked FROM subscription_grants').fetchall()}


def _audit_released_writer_leases(root, retained, interrupted, pending_old):
    """Audit the exact fixture inventory, including consumed-path sibling locks.

    A stable lease stays at its original name after its data directory moves.
    Retained generations carry their component locks, but do not necessarily
    have their own sibling leases. Never classify arbitrary entries as data or
    skip a missing lock merely because its generation has moved.
    """
    root, retained, interrupted, pending_old = map(Path, (root, retained, interrupted, pending_old))
    recovery = root / 'recovery'
    assert all(path.parent == recovery for path in (retained, interrupted, pending_old))
    assert re.fullmatch(r'before-[a-f0-9]{32}', retained.name)
    assert re.fullmatch(r'interrupted-[a-f0-9]{32}', interrupted.name)
    assert re.fullmatch(r'before-[a-f0-9]{32}', pending_old.name)
    assert len({retained, interrupted, pending_old}) == 3
    assert not os.path.lexists(pending_old), 'Consumed predecessor path remains'
    assert not os.path.lexists(root / 'RESTORE_PENDING.json'), 'Restore journal remains'
    stable = (root / '.data.owner.lease',
              pending_old.with_name('.' + pending_old.name + '.owner.lease'))

    def directory(path):
        assert path.is_dir() and not path.is_symlink(), 'Expected real writer generation directory'

    def released(path):
        assert path.is_file() and not path.is_symlink(), 'Expected real instance lease'
        info = path.lstat()
        assert (stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                and info.st_nlink == 1 and info.st_size == 0
                and stat.S_IMODE(info.st_mode) == 0o600), 'Unsafe instance lease'
        assert not _lease_busy(path), 'Writer lease leaked after worker exit'
        after = path.lstat()
        signature = lambda value: (value.st_dev, value.st_ino, value.st_mode,
                                  value.st_uid, value.st_nlink, value.st_size)
        assert signature(info) == signature(after), 'Instance lease changed during audit'

    directory(root); directory(recovery)
    assert set(recovery.iterdir()) == {retained, interrupted, stable[1]}, 'Unexpected recovery inventory'
    generations = (root / 'data', retained, interrupted)
    for generation in generations:
        directory(generation)
        for name in WRITER_LOCKS:
            parent = generation
            for part in Path(name).parts[:-1]:
                parent /= part
                directory(parent)
            released(generation / name)
    for path in stable:
        released(path)
    return {'generations': 3, 'component_locks': 9, 'stable_siblings': 2}


def _write_drivers(control, root, python, config, *, controller=None):
    """The only injected paths/trust live in these disposable test scripts."""
    worker = control / 'fixture_worker_driver.py'
    worker.write_text('''import json,os,runpy,sys
from pathlib import Path
''' + 'WORKER = ' + repr(str(control / 'ops_worker.py')) + '\n'
        + 'CONFIG = ' + repr(str(config)) + '\nROOT = ' + repr(str(root)) + '\n'
        + '''
assert os.getuid() != 0 and os.geteuid() != 0, 'fixture worker must actually be non-root'
assert sys.flags.dont_write_bytecode, 'fixture must not write into its packaged interpreter'
with Path(WORKER).with_name('trace.jsonl').open('a') as trace:
    trace.write(json.dumps({'kind':'worker_started','uid':os.geteuid()})+'\\n')
worker = runpy.run_path(WORKER)
api = worker['_load_release_tools']()
api._MAINTENANCE_CONFIG = Path(CONFIG)
api._maintenance_read_config = lambda: json.loads(Path(CONFIG).read_text())
raise SystemExit(worker['_run'](Path(ROOT),api,sys.stdin.fileno(),sys.stdout))
''')
    # Use constants rather than production environment flags/CLI path overrides.
    # A module-local OS proxy simulates controller identity without changing the
    # real process or the worker's os module. Services inherits the real parser,
    # stop order, quiescence and recovery methods; only its OS backend is fake.
    driver = control / 'fixture_controller_driver.py'
    constants = ('ROOT = Path(' + repr(str(root)) + ')\n'
                 'CONTROL = Path(' + repr(str(control)) + ')\n'
                 'CONTROLLER = Path(' + repr(str(controller or control / 'system_ops.py')) + ')\n'
                 'PYTHON = Path(' + repr(str(python)) + ')\n'
                 'WORKER = Path(' + repr(str(worker)) + ')\n')
    driver.write_text('''import fcntl,importlib.util,json,os,stat,sys,time,types
from contextlib import contextmanager
from pathlib import Path
''' + constants + '''
assert os.getuid() != 0 and os.geteuid() != 0
assert sys.flags.dont_write_bytecode
spec = importlib.util.spec_from_file_location('packaged_system_ops', CONTROLLER)
ops = importlib.util.module_from_spec(spec); spec.loader.exec_module(ops)
trace = CONTROL/'trace.jsonl'
def record(kind, **values):
    with trace.open('a') as out:
        out.write(json.dumps({'kind':kind,'uid':os.geteuid(),**values},sort_keys=True)+'\\n')
def busy(path):
    if not path.exists(): return False
    fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW)
    try:
        try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return True
        fcntl.flock(fd,fcntl.LOCK_UN); return False
    finally: os.close(fd)
def stable_busy(path):
    assert path.is_file() and not path.is_symlink(), 'required stable writer lease missing'
    info=path.lstat()
    assert (stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid() and info.st_nlink==1
            and info.st_size==0 and stat.S_IMODE(info.st_mode)==0o600), 'unsafe stable writer lease'
    return busy(path)
def locks(operation, writers=None):
    assert busy(ROOT/'.operation.lock') is operation, 'operation lease lifecycle'
    if writers is not None:
        assert busy(ROOT/'.panel.lease') is writers, 'panel lease lifecycle'
        stable=ROOT/'.data.owner.lease'
        if writers or os.path.lexists(stable):
            assert stable_busy(stable) is writers, 'canonical stable writer lease lifecycle'
        paths=[ROOT/'data']
        if not writers:
            paths+=sorted((ROOT/'recovery').glob('before-*'))
            paths+=sorted((ROOT/'recovery').glob('interrupted-*'))
        for path in paths:
            for name in ('runtime/xray/owner.lock','runtime/sing-box/owner.lock','certificates/.worker.lock'):
                if (path/name).exists(): assert busy(path/name) is writers, 'writer generation lease lifecycle'
class FixtureOS:
    def __getattr__(self,name): return getattr(os,name)
    def getuid(self): return 0
    def geteuid(self): return 0
ops.os=FixtureOS()
@contextmanager
def installation_lock():
    with (CONTROL/'fixture-install.lock').open('a+b') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try: yield
        finally: fcntl.flock(handle,fcntl.LOCK_UN)
ops.installation_lock=installation_lock
ops.verify_installation=lambda: ('systemd',types.SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid()))
ops.PYTHON=PYTHON; ops.WORKER=WORKER
ops.cgroup_empty=lambda unit: unit in ops.SYSTEMD_UNITS
fake_states={unit:'active' if unit != 'v-ui-http01.service' else 'inactive' for unit in ops.SYSTEMD_UNITS}
class FakeServices(ops.Services):
    def __init__(self,manager,cancelled=None):
        assert manager=='systemd'
        self.manager=manager; self.cancelled=cancelled or []; self.deadline=None
        self.units=ops.SYSTEMD_UNITS
        self.states=fake_states
    def _run(self,arguments,ignore_cancel=False):
        record('fake_service',arguments=arguments)
        if arguments[0] in ('start','stop'):
            action,unit=arguments
            assert unit in self.units
            locks(True, False if action=='start' else None)
            self.states[unit]='active' if action=='start' else 'inactive'
            record('fake_states',states=self.states.copy())
            return 0,b''
        assert arguments[:3]==['show','--no-pager','--all']
        assert arguments[3]=='--property='+','.join(ops.PROPERTIES)
        unit=arguments[4]; assert unit in self.units
        active=self.states[unit]=='active'; socket=unit.endswith('.socket')
        socket_state='running' if self.states['v-ui-http01.service']=='active' else 'listening'
        fields={'LoadState':'loaded','NeedDaemonReload':'no','FragmentPath':'/etc/systemd/system/'+unit,
            'DropInPaths':'','ActiveState':'active' if active else 'inactive',
            'SubState':(socket_state if socket else 'running') if active else 'dead',
            'MainPID':'123' if active and not socket else '0','ControlPID':'0',
            'ControlGroup':'/system.slice/'+unit if active else '',
            'User':'' if socket else 'v-ui','Group':'' if socket else 'v-ui','KillMode':'control-group'}
        if socket: fields.pop('MainPID')
        return 0,('\\n'.join(key+'='+value for key,value in fields.items())+'\\n').encode('ascii')
ops.Services=FakeServices
ActualChild=ops.Child
class FixtureChild(ActualChild):
    def __init__(self,command,timeout,account=None,cancelled=None):
        assert command==[str(PYTHON),'-B','-I','-S',str(WORKER)], 'unexpected host command'
        assert account.pw_uid==os.getuid() and account.pw_gid==os.getgid()
        self.action=None
        self.recovery_lease=None
        journal=ROOT/'RESTORE_PENDING.json'
        if journal.is_file():
            previous=ROOT/json.loads(journal.read_text())['old']
            self.recovery_lease=previous.with_name('.'+previous.name+'.owner.lease')
        self.previous_recovery=set((ROOT/'recovery').glob('before-*'))
        self.previous_interrupted=set((ROOT/'recovery').glob('interrupted-*'))
        # Keep the production flags, including explicit -B: -I ignores the
        # PYTHONDONTWRITEBYTECODE environment variable. Only credential-switch
        # arguments are removed because this process is already non-root.
        super().__init__(command,timeout,account=None,cancelled=cancelled)
    def send(self,value):
        if 'action' in value: self.action=value['action']
        if value.get('command')=='finish': locks(True, None if self.action=='stage' else False)
        record('send',action=self.action,value=value)
        return super().send(value)
    def receive(self):
        value=super().receive()
        if value['event']=='prepared': locks(True, None if self.action=='stage' else False)
        elif value['event']=='done':
            locks(True, None if self.action=='stage' else True)
            if self.action=='restore':
                old=set((ROOT/'recovery').glob('before-*'))-self.previous_recovery
                assert len(old)==1
                for path in old:
                    for name in ('runtime/xray/owner.lock','runtime/sing-box/owner.lock','certificates/.worker.lock'):
                        assert busy(path/name), 'old writer generation released before restore completion'
            if self.action=='recover-restore':
                assert self.recovery_lease is not None
                assert stable_busy(self.recovery_lease), 'predecessor stable lease released before recovery completion'
                interrupted=set((ROOT/'recovery').glob('interrupted-*'))-self.previous_interrupted
                assert len(interrupted)==1
                for path in interrupted:
                    for name in ('runtime/xray/owner.lock','runtime/sing-box/owner.lock','certificates/.worker.lock'):
                        assert busy(path/name), 'interrupted generation released before recovery completion'
        elif value['event']=='writers_released':
            locks(True,False)
            assert not stable_busy(ROOT/'.data.owner.lease'), 'canonical stable lease not released'
            if self.action=='recover-restore':
                assert not stable_busy(self.recovery_lease), 'predecessor stable lease not released'
        record('receive',action=self.action,value=value)
        return value
    def exit_zero(self):
        result=super().exit_zero(); record('exit_zero',action=self.action); return result
    def close(self,*args,**kwargs):
        super().close(*args,**kwargs)
        locks(False)
        record('closed',action=self.action)
ops.Child=FixtureChild
sys.argv=[str(CONTROL/'system_ops.py')]
raise SystemExit(ops.menu())
''')
    for path in (driver, worker):
        path.chmod(0o600)
    return driver


def _audit_trace(events):
    """Fail closed if a successful worker skipped handshake or service ordering."""
    assert events and all(type(event['uid']) is int and event['uid'] > 0 for event in events)
    transactions, current = [], None
    for event in events:
        if event['kind'] == 'send' and 'action' in event['value']:
            assert current is None, 'Overlapping worker transaction'
            current = {'action': event['action'], 'events': [], 'mutations': [], 'worker_starts': 0}
        if event['kind'] == 'fake_service':
            arguments = event['arguments']
            assert arguments[-1] in UNITS, 'Unexpected service target'
            assert current is None or current['action'] != 'stage', 'Stage queried services'
            if arguments[0] in ('start', 'stop'):
                assert current is not None and current['action'] != 'stage'
                if arguments[0] == 'stop':
                    assert current['events'] == ['event:prepared'], 'Services stopped before preflight'
                else:
                    assert current['events'][-1:] == ['event:writers_released'], 'Services restarted before writers released'
                current['mutations'].append(arguments)
        if current is not None:
            if event['kind'] == 'send' and 'command' in event['value']:
                current['events'].append('command:' + event['value']['command'])
            elif event['kind'] == 'receive':
                current['events'].append('event:' + event['value']['event'])
            elif event['kind'] == 'exit_zero':
                current['events'].append('exit:0')
            elif event['kind'] == 'worker_started':
                current['worker_starts'] += 1
            elif event['kind'] == 'closed':
                transactions.append(current); current = None
    assert current is None, 'Worker not reaped'
    passed = []
    for item in transactions:
        assert item['worker_starts'] == 1, 'Expected a real non-root worker for each transaction'
        if 'exit:0' not in item['events']:
            assert item['action'] == 'stage' and item['events'] == ['event:error']
            assert not item['mutations'], 'Rejected stage touched services'
            continue
        expected = ['event:prepared', 'command:proceed', 'event:done']
        if item['action'] != 'stage':
            expected += ['command:release_writers', 'event:writers_released']
            mutations = []
            if item['action'] != 'start':
                mutations += [['stop', 'v-ui-http01.socket'], ['stop', 'v-ui.service'], ['stop', 'v-ui-http01.service']]
            if item['action'] not in ('stop', 'recover-restore'):
                mutations += [['start', 'v-ui-http01.socket'], ['start', 'v-ui.service']]
            assert item['mutations'] == mutations, item
        else:
            assert not item['mutations']
        assert item['events'] == expected + ['command:finish', 'exit:0'], item
        passed.append(item['action'])
    assert passed == SUCCESS_ACTIONS, passed
    return transactions


def installed_managed_ops_smoke(python, payload, root, bundle, checksum, *, panel, probe):
    """Consume the package while preserving the caller's original live HTTPS root."""
    assert os.getuid() != 0 and os.geteuid() != 0, 'Managed smoke must run without root'
    assert file_digest(bundle) == checksum
    source_ready = json.loads((payload.parent / 'READY.json').read_text())
    assert source_ready['archive_sha256'] == checksum
    manifest = json.loads((payload / 'MANIFEST.json').read_text())
    original = {'current': (root / 'CURRENT.json').read_bytes(),
                'data': content_snapshot(root / 'data'),
                'release': content_snapshot(payload.parent), 'panel': _process_identity(panel)}
    stop = threading.Event(); failures = []; observations = []
    def watch():
        while not stop.is_set():
            try:
                assert _process_identity(panel) == original['panel']; probe()
                observations.append(time.monotonic())
            except Exception as exc:
                failures.append(repr(exc)); return
            stop.wait(.2)
    watcher = threading.Thread(target=watch, daemon=True); watcher.start()
    try:
        with tempfile.TemporaryDirectory(prefix='vui-managed-package-') as directory:
            fixture = Path(directory); private = fixture / 'instance'; private.mkdir(mode=0o700)
            control = fixture / 'control'; control.mkdir(mode=0o700)
            digests = _copy_control(payload, control)
            identity = manifest['release_id']
            anchor = private / 'releases' / identity
            anchor.parent.mkdir(mode=0o700)
            shutil.copytree(payload.parent, anchor, symlinks=True)
            current = private / 'CURRENT.json'
            current.write_bytes(json.dumps({'release_id': identity, 'previous_id': None}).encode())
            current.chmod(0o600); before_current = current.read_bytes()
            _seed_fake_data(private / 'data')
            config = control / 'fixture-service.json'
            config.write_text(json.dumps({'bootstrap_python': str(anchor / 'runtime/python/bin/python3'),
                                         'target': source_ready['runtime_key']}))
            config.chmod(0o600)
            driver = _write_drivers(control, private, python, config,
                                    controller=payload / 'deploy/system_ops.py')
            candidate = fixture / 'manifest-only-candidate.zip'
            candidate_id = 'managed-fixture-' + hashlib.sha256(before_current).hexdigest()[:16]
            candidate_sha = fixture_bundle(bundle, candidate, candidate_id)
            command = [str(python), '-B', '-I', '-S', str(driver)]
            env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                   'PYTHONDONTWRITEBYTECODE': '1'}
            def begin_stage(session, sha):
                session.send('8\n'); session.expect('本地升级包绝对路径（留空取消）: ')
                session.send(str(candidate) + '\n'); session.expect('可信 SHA-256（留空取消）: ')
                session.send(sha + '\n'); session.expect('确认仅准备候选 [yes/N]: ')
            def completed(session, action):
                text = session.expect(PROMPT)
                assert SUCCESS in text and FAILURE not in text, action + ': ' + text
                return text
            with MenuSession(command, cwd=control, env=env, timeout=660) as session:
                session.expect(PROMPT)
                before_cancel = content_snapshot(private)
                session.send('8\n'); session.expect('本地升级包绝对路径（留空取消）: ')
                session.send('\n'); session.expect(PROMPT)
                begin_stage(session, candidate_sha); session.send('N\n')
                assert '已取消' in session.expect(PROMPT)
                assert content_snapshot(private) == before_cancel
                wrong = ('0' if candidate_sha[0] != '0' else '1') + candidate_sha[1:]
                begin_stage(session, wrong); session.send('yes\n')
                assert FAILURE in session.expect(PROMPT)
                assert current.read_bytes() == before_current
                assert not (private / 'releases' / candidate_id).exists()
                before_data = content_snapshot(private / 'data')
                begin_stage(session, candidate_sha); stage_started = time.monotonic(); session.send('yes\n')
                text = session.expect(PROMPT); stage_finished = time.monotonic()
                assert '候选准备完成，未激活: ' + candidate_id in text and FAILURE not in text, text
                assert current.read_bytes() == before_current
                assert content_snapshot(private / 'data') == before_data
                assert not (private / '.panel.lease').exists(), 'Stage created a writer lease'
                assert not (control / 'trace.jsonl').read_text().count('fake_service'), 'Stage queried services'
                prepared = private / 'releases' / candidate_id
                ready = json.loads((prepared / 'READY.json').read_text())
                assert ready['archive_sha256'] == candidate_sha
                assert ready['manifest_sha256'] == file_digest(prepared / 'payload/MANIFEST.json')
                session.send('5\n'); session.expect(CONFIRM); session.send('yes\n')
                completed(session, 'backup')
                backups = list((private / 'backups').glob('maintenance-*.zip'))
                assert len(backups) == 1
                backup = backups[0]; backup_sha = file_digest(backup)
                assert current.read_bytes() == before_current
                with sqlite3.connect(private / 'data/v-ui.db') as db:
                    db.execute("UPDATE inbounds SET remark='changed-after-backup'"); db.commit()
                (private / 'data/fake-user-file').write_bytes(b'changed after backup\n')
                session.send('6\n'); session.expect('备份绝对路径（留空取消）: ')
                session.send(str(backup) + '\n'); session.expect('可信 SHA-256（留空取消）: ')
                session.send(backup_sha + '\n'); session.expect(CONFIRM); session.send('yes\n')
                completed(session, 'restore')
                assert current.read_bytes() == before_current
                restored = _database_values(private / 'data')
                assert restored == {'nodes': [('original-fake-node',)], 'sessions': [], 'grants': [(1,)]}
                assert (private / 'data/fake-user-file').read_bytes() == b'original synthetic private data\n'
                recovery = list((private / 'recovery').glob('before-*')); assert len(recovery) == 1
                assert _database_values(recovery[0]) == {'nodes': [('changed-after-backup',)],
                    'sessions': [('synthetic-session-digest',)], 'grants': [(0,)]}
                assert (recovery[0] / 'fake-user-file').read_bytes() == b'changed after backup\n'
                old_recovery = content_snapshot(recovery[0])
                session.send('7\n'); session.expect('已准备版本 ID（留空取消）: ')
                session.send(candidate_id + '\n'); session.expect(CONFIRM)
                assert current.read_bytes() == before_current, 'Selection changed before explicit activation'
                session.send('yes\n'); text = session.expect(PROMPT)
                assert SUCCESS in text and '已选择版本: ' + candidate_id in text, text
                assert json.loads(current.read_text()) == {'release_id': candidate_id, 'previous_id': identity}
                assert len(list((private / 'backups').glob('maintenance-*.zip'))) == 2
                assert _database_values(private / 'data') == restored
                assert content_snapshot(recovery[0]) == old_recovery
                # An explicit second selection exercises code rollback through
                # the same real candidate health and backup path, not recovery
                # of data and never an automatic response to failure.
                session.send('7\n'); session.expect('已准备版本 ID（留空取消）: ')
                session.send(identity + '\n'); session.expect(CONFIRM)
                assert json.loads(current.read_text())['release_id'] == candidate_id
                session.send('yes\n'); text = session.expect(PROMPT)
                assert SUCCESS in text and '已选择版本: ' + identity in text, text
                assert json.loads(current.read_text()) == {'release_id': identity, 'previous_id': candidate_id}
                assert len(list((private / 'backups').glob('maintenance-*.zip'))) == 3
                assert _database_values(private / 'data') == restored
                assert content_snapshot(recovery[0]) == old_recovery
                session.send('3\n'); session.expect(CONFIRM); session.send('yes\n')
                completed(session, 'stop before interrupted-restore fixture')
                session.send('1\n'); status = session.expect(PROMPT)
                assert all(unit + ': inactive' in status for unit in UNITS), status
                # Only this isolated test fixture simulates the interrupted
                # rename. The menu then runs the actual journal recovery path.
                pending_old = private / 'recovery' / ('before-' + uuid.uuid4().hex)
                (private / 'data').rename(pending_old)
                interrupted = private / 'data'; interrupted.mkdir(mode=0o700)
                marker = interrupted / 'synthetic-interrupted-file'
                marker.write_bytes(b'preserve this interrupted replacement\n'); marker.chmod(0o600)
                journal = private / 'RESTORE_PENDING.json'
                journal.write_text(json.dumps({'old': str(pending_old.relative_to(private))}))
                journal.chmod(0o600)
                session.send('9\n'); session.expect(CONFIRM); session.send('yes\n')
                completed(session, 'recover-restore')
                assert not journal.exists() and not pending_old.exists()
                interrupted_generations = list((private / 'recovery').glob('interrupted-*'))
                assert len(interrupted_generations) == 1
                assert (interrupted_generations[0] / marker.name).read_bytes() == b'preserve this interrupted replacement\n'
                assert _database_values(private / 'data') == restored
                assert content_snapshot(recovery[0]) == old_recovery
                assert json.loads(current.read_text()) == {'release_id': identity, 'previous_id': candidate_id}
                session.send('1\n'); status = session.expect(PROMPT)
                assert all(unit + ': inactive' in status for unit in UNITS), status
                session.send('2\n'); session.expect(CONFIRM); session.send('yes\n')
                completed(session, 'explicit start after recover-restore')
                session.send('1\n'); status = session.expect(PROMPT)
                assert 'v-ui.service: active' in status and 'v-ui-http01.socket: active' in status
                assert 'v-ui-http01.service: inactive' in status
                assert _database_values(private / 'data') == restored
                session.send('0\n'); session.finish()
            events = [json.loads(line) for line in (control / 'trace.jsonl').read_text().splitlines()]
            transactions = _audit_trace(events)
            assert not _lease_busy(private / '.operation.lock')
            assert not _lease_busy(private / '.panel.lease')
            writer_audit = _audit_released_writer_leases(private, recovery[0],
                interrupted_generations[0], pending_old)
            assert sum(stage_started <= at <= stage_finished for at in observations) >= 2, 'Missing concurrent verified HTTPS observations'
            assert not failures, failures
            result = {'managed_ops': {
                'harness_version': HARNESS_VERSION, 'fixture_version': FIXTURE_VERSION,
                'writer_lease_audit': writer_audit,
                'target': source_ready['runtime_key'], 'product_version': manifest.get('version'),
                'source_commit': manifest.get('source_commit'),
                'package_release_id': identity, 'package_sha256': checksum, 'control_sha256': digests,
                'real': ['packaged Python/controller/worker/release-tools', 'non-root worker', 'PTY/pipes',
                    'SQLite backup/restore/revocation/recovery', 'writer+operation flocks',
                    'explicit interrupted-restore recovery preserving both generations',
                    'offline stage+application health', 'explicit activate/code rollback+application health',
                    'original verified HTTPS/PID/CURRENT/data preserved'],
                'simulated': ['controller root identity', 'root-owned install/config trust',
                    'systemd service commands/cgroup state'],
                'fixture_interpreter_flags': '-B -I -S (no package bytecode writes)',
                'not_exercised': ['actual root installation', 'global wrapper execution', 'real service manager'],
                'successful_actions': [item['action'] for item in transactions if 'exit:0' in item['events']],
                'explicit_selections': [candidate_id, identity],
                'actual_worker_uids': sorted({event['uid'] for event in events if event['kind'] == 'worker_started'}),
                'protocol_sequences': [{'action': item['action'], 'events': item['events']} for item in transactions],
                'fake_service_mutations': [event['arguments'] for event in events
                    if event['kind'] == 'fake_service' and event['arguments'][0] in ('start', 'stop')],
                'verified_https_observations_during_stage': sum(stage_started <= at <= stage_finished for at in observations),
                'negative_cases': ['cancel', 'wrong SHA-256 before service query'],
                'wrapper': 'exact fixed package bytes inspected; never installed or executed'}}
    finally:
        stop.set(); watcher.join(timeout=5)
        assert not watcher.is_alive(), 'HTTPS monitor did not stop'
    assert not failures, failures
    assert _process_identity(panel) == original['panel']; probe()
    assert (root / 'CURRENT.json').read_bytes() == original['current'], 'Managed consumer changed original CURRENT'
    assert content_snapshot(root / 'data') == original['data'], 'Managed consumer changed original data'
    assert content_snapshot(payload.parent) == original['release'], 'Managed consumer changed original installed release'
    assert _lease_busy(root / '.panel.lease'), 'Original real panel lease lost'
    return result
