"""Exercise installed offline recovery on disposable private data, never live data.

This is a storage/compiler gate, not core configuration or network qualification.
The caller must supply the Python and payload from the actual staged package.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile

from app import release_tools


EXPECTED = {
    'proxy_recovery_schema': 1,
    'proxy_recovery': 'installed-runtime offline backup/restore',
    'proxy_recovery_states': ['00', '10', '01', '11'],
    'proxy_recovery_compiler_hashes': 4,
    'proxy_recovery_restores': 4,
    'proxy_recovery_plan_refusals': 8,
    'proxy_recovery_writer_refusals': 8,
    'proxy_recovery_payload_verified': True,
    'proxy_recovery_network_used': False,
    'proxy_recovery_core_started': False,
}


PROBE = r'''
import json, os, sqlite3, sys
from pathlib import Path
from types import SimpleNamespace

os.umask(0o077)
payload = Path(sys.argv[1]).resolve()
assert Path(sys.executable).absolute() == Path(sys.argv[2]).absolute()
attempts = []
def forbidden(*args, **kwargs):
    attempts.append('core operation')
    raise AssertionError('Offline recovery attempted a core operation')
def audit(event, args):
    if event.startswith('socket.') or event in (
            'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.fork', 'os.exec'):
        attempts.append(event)
        raise AssertionError('Offline recovery attempted network or process execution')
sys.addaudithook(audit)

from app import release_tools as release
from app.data_owner import data_owner
from app.services.core_manager import SingBoxAdapter
from app.services.core_runtime import CoreRuntime, encode, revision
from app.services.proxy_policy import VerifiedEndpoint, compile_proxy_policy
from app.services.proxy_store import ProxyStore
from app.services import proxy_recovery as recovery

for method in ('__init__', 'check', 'apply', 'restore', 'recover', '_launch'):
    setattr(CoreRuntime, method, forbidden)

def verify_payload_imports():
    for name, module in tuple(sys.modules.items()):
        if name == 'app' or name.startswith('app.'):
            paths = list(getattr(module, '__path__', ()))
            if getattr(module, '__file__', None): paths.append(module.__file__)
            assert paths and all(Path(path).resolve().is_relative_to(payload) for path in paths)
verify_payload_imports()

uid = '11111111-1111-4111-8111-111111111111'
password = 'synthetic-offline-local-password'
flags = ('local_proxy_enabled', 'relay_enabled')
top = Path(os.environ['VUI_DATA_DIR']).parent
states = []
hashes = set()
restores = plan_refusals = writer_refusals = 0

def settings(snapshot):
    return {key: value for key, value in snapshot['settings'].items() if key != 'local_password_set'}

def content(data):
    return {path.relative_to(data).as_posix(): (path.read_bytes(), path.stat().st_mode & 0o777)
        for path in data.rglob('*') if path.is_file()
        and path.relative_to(data).as_posix() not in release._RECOVERY_LOCKS}

def refused(root, action):
    before = content(root / 'data')
    inode = (root / 'data').stat().st_ino
    try:
        action()
    except release.ReleaseError:
        pass
    else:
        raise AssertionError('Offline recovery accepted a stale plan or active writer')
    assert content(root / 'data') == before
    assert (root / 'data').stat().st_ino == inode
    assert not (root / 'RESTORE_PENDING.json').exists()

for local, relay in ((False, False), (True, False), (False, True), (True, True)):
    state = str(int(local)) + str(int(relay))
    root = top / ('state-' + state)
    root.mkdir(mode=0o700)
    data = root / 'data'
    data.mkdir(mode=0o700)
    row = SimpleNamespace(id=1, user_id=1, core='sing-box', up=0, down=0, total=0,
        remark='synthetic preserved inbound', enable=True, expiry_time=0, port=24443,
        protocol='vless', tag='synthetic-inbound', settings={'users': [{'uuid': uid}]},
        stream_settings={'tls': {'enabled': True, 'server_name': 'node.example.test',
            'certificate_path': '/synthetic-only-certificate', 'key_path': '/synthetic-only-key'}})
    with sqlite3.connect(data / 'v-ui.db') as db:
        db.executescript("""CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT);
            CREATE TABLE inbounds(id INTEGER PRIMARY KEY, user_id INTEGER, core TEXT,
                up INTEGER, down INTEGER, total INTEGER, remark TEXT, enable INTEGER,
                expiry_time INTEGER, port INTEGER, protocol TEXT, settings TEXT,
                stream_settings TEXT, tag TEXT);
            CREATE TABLE admin_sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER);
            CREATE TABLE subscription_grants(token_hash TEXT PRIMARY KEY, revoked INTEGER);
            INSERT INTO users VALUES(1, 'synthetic-admin');
            INSERT INTO admin_sessions VALUES('synthetic-session', 1);
            INSERT INTO subscription_grants VALUES('synthetic-grant', 0);""")
        db.execute('INSERT INTO inbounds VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (row.id, row.user_id, row.core, row.up, row.down, row.total, row.remark,
             int(row.enable), row.expiry_time, row.port, row.protocol,
             json.dumps(row.settings), json.dumps(row.stream_settings), row.tag))
    store = ProxyStore(data)
    created = store.save_upstream({'name': 'Synthetic upstream', 'server': 'upstream.example.test',
        'server_port': 443, 'uuid': uid, 'tls': {'mode': 'tls', 'server_name': 'upstream.example.test'}},
        store.read_snapshot()['revision'])
    selected = created['upstream_id']
    store.save_draft({**settings(created['snapshot']), 'local_proxy_enabled': local,
        'relay_enabled': relay, 'selected_upstream_id': selected, 'local_username': 'synthetic-user'},
        created['snapshot']['revision'], local_password=password)
    snapshot = store.load_draft()
    endpoint = (VerifiedEndpoint(selected, snapshot['revision'], 'upstream.example.test',
                ('8.8.4.4',), 1) if local or relay else None)
    addresses = ['127.0.0.1', '192.168.8.2'] if endpoint else []
    compiled = compile_proxy_policy(SingBoxAdapter().build_config([row]), [row],
        snapshot, endpoint=endpoint, self_addresses=addresses)
    core_hash = revision(compiled.config)
    context = {'schema_version': 1, 'kind': 'apply', 'candidate_core_revision': core_hash,
        'previous_core_revision': None, 'previous_enabled': False,
        'policy_identity': compiled.identity, 'self_addresses': addresses,
        'endpoint': None if endpoint is None else {**vars(endpoint), 'addresses': list(endpoint.addresses)}}
    store.begin_apply(snapshot['revision'], 'synthetic-committed', runtime_context=context)
    runtime = data / 'runtime' / 'sing-box'
    runtime.mkdir(parents=True, mode=0o700)
    (runtime / (core_hash + '.json')).write_bytes(encode(compiled.config))
    release.atomic_json(runtime / 'state.json', {'revision': core_hash, 'enabled': True})
    store.commit_apply('synthetic-committed', core_hash)
    active = store.load_active()
    current = store.read_snapshot()
    store.save_draft({**settings(current), 'local_proxy_enabled': not local,
                     'relay_enabled': not relay}, current['revision'])
    draft = store.load_draft()
    saved = store.read_snapshot()
    archive = root / 'synthetic-backup.zip'
    checksum = release.backup(root, archive)
    objects = {path.relative_to(store.path).as_posix(): path.read_bytes()
        for name in ('revisions', 'secrets') for path in (store.path / name).glob('*.json')}
    # Recovery may replace unknown current state only from this explicit backup.
    (store.path / 'state.json').write_bytes(b'{"unknown-policy":true}')
    source = content(data)
    kwargs = {'archive': archive, 'archive_sha256': checksum}
    plan = recovery.preview(root, 'restore', **kwargs)
    assert recovery.preview(root, 'restore', **kwargs) == plan and content(data) == source
    assert plan['policy']['active_switches'] == dict(zip(flags, (local, relay)))
    assert plan['policy']['draft_switches'] == dict(zip(flags, (not local, not relay)))
    assert plan['policy']['core_revision'] == core_hash
    assert plan['starts_core'] is False and plan['revokes_restored_access'] is True
    for secret in (uid, password, selected, '8.8.4.4', '192.168.8.2', 'upstream.example.test'):
        assert secret not in json.dumps(plan)
    refused(root, lambda: recovery.execute(root, 'restore', '0' * 64, **kwargs))
    note = data / 'synthetic-new-source'
    note.write_bytes(b'new source bytes invalidate the exact preview')
    refused(root, lambda: recovery.execute(root, 'restore', plan['plan_sha256'], **kwargs))
    note.unlink()
    plan_refusals += 2
    with data_owner(data):
        refused(root, lambda: recovery.preview(root, 'restore', **kwargs))
        refused(root, lambda: recovery.execute(root, 'restore', plan['plan_sha256'], **kwargs))
        writer_refusals += 2
    result = recovery.execute(root, 'restore', plan['plan_sha256'], **kwargs)
    assert result['completed'] is True and result['plan_sha256'] == plan['plan_sha256']
    assert store.load_active() == active and store.load_draft() == draft
    assert store.read_snapshot() == saved
    assert content(root / result['retained_predecessor']) == source
    assert all((data / 'proxy-state' / name).read_bytes() == raw for name, raw in objects.items())
    assert release.digest((runtime / (core_hash + '.json')).read_bytes()) == core_hash
    assert json.loads((runtime / 'state.json').read_text()) == {'revision': core_hash, 'enabled': True}
    with sqlite3.connect(data / 'v-ui.db') as db:
        assert db.execute('SELECT count(*) FROM admin_sessions').fetchone()[0] == 0
        assert db.execute('SELECT revoked FROM subscription_grants').fetchall() == [(1,)]
        assert db.execute('SELECT remark FROM inbounds').fetchone()[0] == row.remark
        assert db.execute('SELECT username FROM users').fetchone()[0] == 'synthetic-admin'
    assert not (root / 'RESTORE_PENDING.json').exists()
    states.append(state)
    hashes.add(core_hash)
    restores += 1

verify_payload_imports()
assert not attempts
print(json.dumps({'proxy_recovery_schema': 1,
    'proxy_recovery': 'installed-runtime offline backup/restore',
    'proxy_recovery_states': states, 'proxy_recovery_compiler_hashes': len(hashes),
    'proxy_recovery_restores': restores, 'proxy_recovery_plan_refusals': plan_refusals,
    'proxy_recovery_writer_refusals': writer_refusals, 'proxy_recovery_payload_verified': True,
    'proxy_recovery_network_used': False, 'proxy_recovery_core_started': False}))
'''


def _evidence(stdout: str) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate evidence field')
            result[key] = value
        return result

    try:
        if len(stdout) > 8192:
            raise ValueError('Oversized evidence')
        evidence = json.loads(stdout, object_pairs_hook=pairs)
        if (type(evidence) is not dict or set(evidence) != set(EXPECTED)
                or any(type(evidence[key]) is not type(expected) or evidence[key] != expected
                       for key, expected in EXPECTED.items())):
            raise ValueError('Incomplete evidence')
        return evidence
    except (ValueError, TypeError):
        raise AssertionError('Installed proxy recovery evidence was invalid or incomplete') from None


def installed_proxy_recovery_smoke(python: Path, payload: Path, root: Path) -> dict:
    python, payload = Path(python).absolute(), Path(payload).resolve()
    with tempfile.TemporaryDirectory(prefix='.proxy-recovery-smoke-', dir=root) as temporary:
        env = release_tools.child_env(payload, Path(temporary) / 'data')
        result = subprocess.run([str(python), '-B', '-c', PROBE, str(payload), str(python)],
                                cwd=payload, env=env, capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise AssertionError('Installed isolated proxy recovery smoke failed: ' + result.stderr[-8192:])
        return _evidence(result.stdout)
