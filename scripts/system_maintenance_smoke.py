"""Ephemeral systemd acceptance; never imported or installed by root components.

Actual socket/kernel-peer/task/service checks use installed root-owned code. The
candidate upgrade fixture replaces only network transport in memory; production
contains no URL/path/environment override. Stable selection is tested separately.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import stat
import shutil
import subprocess
import sys
import time
import uuid

CONTROL = Path('/usr/local/lib/v-ui')
CLIENT = r'''
import json,runpy,socket,sys
from pathlib import Path
reason = None
original_lstat = Path.lstat
def observed_lstat(path, *args, **kwargs):
    global reason
    try:
        return original_lstat(path, *args, **kwargs)
    except FileNotFoundError:
        if str(path) in ('/run/v-ui-maintenance', '/run/v-ui-maintenance/control.sock'):
            reason = 'LISTENER_MISSING'
        raise
class ObservedSocket(socket.socket):
    def connect(self, address):
        global reason
        self.observing_listener = address == '/run/v-ui-maintenance/control.sock'
        try:
            return super().connect(address)
        except ConnectionRefusedError:
            if self.observing_listener: reason = 'CONNECTION_REFUSED'
            raise
        except FileNotFoundError:
            if self.observing_listener: reason = 'LISTENER_MISSING'
            raise
    def recv(self, *args, **kwargs):
        global reason
        try:
            return super().recv(*args, **kwargs)
        except ConnectionResetError:
            if getattr(self, 'observing_listener', False): reason = 'CONNECTION_RESET'
            raise
Path.lstat = observed_lstat
socket.socket = ObservedSocket
api = runpy.run_path('/usr/local/lib/v-ui/maintenance_client.py')
value = json.loads(sys.stdin.read())
reason = None
try:
    result = api['request'](value)
except BaseException:
    if reason: print('VUI_CI_LISTENER=' + reason, file=sys.stderr)
    raise
print(json.dumps(result))
'''
MAINTENANCE_UNIT = 'v-ui-maintenance.service'
STATE_FIELDS = {'ActiveState', 'SubState', 'Result', 'ExecMainCode', 'ExecMainStatus'}
SERVICE_UNITS = ('v-ui.service', 'v-ui-http01.service', 'v-ui-http01.socket')
SERVICE_FIELDS = ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'ControlPID', 'ControlGroup',
                  'User', 'Group', 'KillMode', 'NeedDaemonReload', 'FragmentPath', 'DropInPaths')
STARTUP_CODES = ('ENTRY', 'LOAD_CONTROLLER', 'INSTANCE_LOCK', 'VERIFY_RECEIPT', 'STATE_DIRECTORY',
                 'TASK_DIRECTORY', 'SOCKET_DIRECTORY', 'READ_TASKS', 'SOCKET_BIND', 'SERVING')


def safe_maintenance_journal(raw):
    codes = []
    for line in raw.decode('utf-8', errors='replace').splitlines()[-20:]:
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(row, dict) or not isinstance(row.get('MESSAGE'), str):
            continue
        message = row['MESSAGE']
        if message.startswith('VUI_MAINTENANCE_REFUSED:'):
            refusal = re.fullmatch(r'VUI_MAINTENANCE_REFUSED:(RECEIVE|GET|LIST|STATUS|LOGS|CORE_STATUS|SUBMIT|TASK_LSTAT|TASK_TRUST_READ|TASK_JSON|TASK_ID|PUBLIC):(TRUSTED_INPUT|PROTOCOL_OR_STATE|IO|INTERNAL)', message)
            codes.append('REQUEST_' + refusal[1] + '_' + refusal[2] if refusal else 'UNCLASSIFIED')
            continue
        code = next(('STARTUP_' + stage for stage in STARTUP_CODES
                     if 'V-UI maintenance startup refused: STARTUP_' + stage in message), None)
        if code is None:
            code = next((code for needle, code in (
                ('V-UI system maintenance unavailable', 'STARTUP_UNCLASSIFIED'),
                ('PermissionError', 'PERMISSION_DENIED'),
                ('ModuleNotFoundError', 'MODULE_MISSING'),
                ('SyntaxError', 'CODE_SYNTAX'),
                ('Main process exited', 'PROCESS_EXITED'),
                ('Failed with result', 'SERVICE_FAILED'),
                ('Scheduled restart job', 'RESTART_SCHEDULED')) if needle in message), 'UNCLASSIFIED')
        codes.append(code)
    return codes


def bounded_diagnostic(command):
    # Only fixed OS commands below use this. timeout bounds even a privileged
    # journal process; reads are capped before decoding/retaining any message.
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'HOME': '/'})
    deadline = time.monotonic() + 4
    data = bytearray()
    try:
        while time.monotonic() < deadline:
            if not select.select([process.stdout], [], [], min(.1, max(0, deadline-time.monotonic())))[0]:
                continue
            chunk = os.read(process.stdout.fileno(), min(8192, 65537-len(data)))
            if not chunk:
                return bytes(data) if process.wait(timeout=1) == 0 else None
            data.extend(chunk)
            if len(data) > 65536:
                return None
        return None
    finally:
        process.stdout.close()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass  # The fixed OS timeout child still terminates within 3s.


def maintenance_diagnostics():
    result = {'unit': MAINTENANCE_UNIT}
    try:
        raw = bounded_diagnostic(['/usr/bin/timeout', '3s', '/usr/bin/systemctl', 'show',
            '--no-pager', '--property='+','.join(sorted(STATE_FIELDS)), MAINTENANCE_UNIT])
        if raw is not None:
            for line in raw.decode('ascii', errors='replace').splitlines():
                key, _, value = line.partition('=')
                if key in STATE_FIELDS and re.fullmatch(r'[A-Za-z0-9_.-]{0,40}', value):
                    result[key] = value
        raw = bounded_diagnostic(['sudo', '-n', '/usr/bin/timeout', '3s', '/usr/bin/journalctl',
            '--unit='+MAINTENANCE_UNIT, '--lines=20', '--no-pager', '--output=json', '--output-fields=MESSAGE'])
        result['journal_codes'] = safe_maintenance_journal(raw) if raw is not None else ['DIAGNOSTIC_UNAVAILABLE']
    except (OSError, ValueError, subprocess.SubprocessError):
        result['journal_codes'] = ['DIAGNOSTIC_UNAVAILABLE']
    return result


def maintenance_failure_class(stderr):
    # Inspect only the final fixed client exception. Never return a traceback,
    # arbitrary exception text, captured stdout, or caller/request values.
    last = stderr.rstrip().rsplit('\n', 1)[-1] if isinstance(stderr, str) else ''
    if last.endswith('MaintenanceUnavailable: 系统维护未接受请求，请检查任务与本机受管组件'):
        return 'DAEMON_REFUSED'
    if last.endswith('MaintenanceUnavailable: 系统维护组件不可用，请通过本机终端检查'):
        return 'SOCKET_OR_TRANSPORT_UNAVAILABLE'
    return 'UNKNOWN'


def safe_service_status(raw, unit):
    if unit not in SERVICE_UNITS:
        raise ValueError('Fixed service required')
    values = {}
    for line in raw.decode('ascii', errors='replace').splitlines():
        key, separator, value = line.partition('=')
        if not separator or key in values:
            return {'diagnostic': 'INVALID_STATUS'}
        if key in SERVICE_FIELDS:
            values[key] = value
    result = {}
    choices = {
        'LoadState': {'loaded', 'not-found', 'bad-setting', 'error', 'masked', 'merged', 'stub'},
        'ActiveState': {'active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading', 'maintenance'},
        'SubState': {'running', 'dead', 'listening', 'auto-restart', 'start-pre', 'start', 'start-post',
                     'stop', 'stop-sigterm', 'stop-sigkill', 'stop-post', 'final-sigterm', 'final-sigkill',
                     'failed', 'exited', 'waiting'},
        'User': {'', 'root', 'v-ui'}, 'Group': {'', 'root', 'v-ui'},
        'KillMode': {'', 'control-group', 'process', 'mixed', 'none'},
        'NeedDaemonReload': {'yes', 'no'},
    }
    for key, allowed in choices.items():
        if key in values:
            result[key] = values[key] if values[key] in allowed else 'OTHER'
    for key in ('MainPID', 'ControlPID'):
        if key in values:
            result[key] = values[key] if re.fullmatch(r'0|[1-9][0-9]{0,9}', values[key]) else 'INVALID'
    if 'FragmentPath' in values:
        result['fragment_path_matches'] = values['FragmentPath'] == '/etc/systemd/system/' + unit
    if 'ControlGroup' in values:
        result['control_group_matches'] = values['ControlGroup'] == '/system.slice/' + unit
        result['control_group_empty'] = values['ControlGroup'] == ''
    if 'DropInPaths' in values:
        result['has_drop_ins'] = values['DropInPaths'] != ''
    return result


def service_status_diagnostics():
    result = {}
    for unit in SERVICE_UNITS:
        try:
            raw = bounded_diagnostic(['/usr/bin/timeout', '3s', '/usr/bin/systemctl', 'show',
                '--all', '--no-pager', '--property='+','.join(SERVICE_FIELDS), unit])
            result[unit] = safe_service_status(raw, unit) if raw is not None else {'diagnostic': 'DIAGNOSTIC_UNAVAILABLE'}
        except (OSError, ValueError, subprocess.SubprocessError):
            result[unit] = {'diagnostic': 'DIAGNOSTIC_UNAVAILABLE'}
    return result


class BridgeTransportUnavailable(AssertionError):
    """A read may wait for listener recreation; mutations still fail once."""


def invoke(value, *, user='v-ui', expect_success=True, timeout=15):
    assert type(timeout) in (int, float) and 0 < timeout <= 15, 'Invalid maintenance request budget'
    started = time.monotonic()
    result = subprocess.run(['sudo', '-u', user, '/usr/bin/python3', '-B', '-I', '-S', '-c', CLIENT],
        input=json.dumps(value), capture_output=True, text=True, timeout=timeout)
    elapsed_ms = int(max(0, time.monotonic() - started) * 1000)
    if not expect_success:
        assert result.returncode != 0, 'Unexpected maintenance peer accepted'
        return
    if result.returncode != 0:
        failure = {'classification': maintenance_failure_class(result.stderr),
                   'returncode': result.returncode, 'elapsed_ms': elapsed_ms}
        try:
            failure['maintenance'] = maintenance_diagnostics()
            if failure['classification'] == 'DAEMON_REFUSED':
                failure['services'] = service_status_diagnostics()
        except Exception:
            # Diagnostics must neither replace the original failure nor expose
            # their own exception. The failed request is never retried.
            failure['diagnostic'] = 'DIAGNOSTIC_UNAVAILABLE'
        # The production client's generic unavailable message also covers path
        # trust and JSON errors. Only a fixed CI observation of a real listener
        # syscall failure permits read readiness waiting; those errors do not.
        reasons = re.findall(r'^VUI_CI_LISTENER=(LISTENER_MISSING|CONNECTION_REFUSED|CONNECTION_RESET)$', result.stderr, re.MULTILINE)
        retryable = failure['classification'] == 'SOCKET_OR_TRANSPORT_UNAVAILABLE' and len(reasons) == 1
        if retryable:
            failure['listener_reason'] = reasons[0]
        error = BridgeTransportUnavailable if retryable else AssertionError
        raise error('Installed maintenance bridge unavailable: ' + json.dumps(failure, sort_keys=True)) from None
    return json.loads(result.stdout)


def read_when_available(value, deadline):
    # Only idempotent observations may wait for the initial listener or the
    # documented daemon exec after upgrade/receipt refresh. Never resubmit work.
    assert value.get('operation') in ('status', 'get'), 'Only fixed maintenance reads may wait'
    probes = 0
    last_transport_error = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 and last_transport_error is not None:
            raise last_transport_error
        assert remaining > 0, 'Maintenance read readiness exceeded its original deadline'
        try:
            result = invoke(value, timeout=min(15, remaining))
            assert time.monotonic() <= deadline, 'Maintenance read completed after its original deadline'
            if probes:
                print('Maintenance read-only listener readiness confirmed; transport probes=' + str(probes), flush=True)
            return result
        except BridgeTransportUnavailable as error:
            last_transport_error = error
            probes += 1
            if probes == 1:
                print('Maintenance read-only listener pending; waiting within the original deadline', flush=True)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(.2, remaining))


def wait_idle(deadline):
    while True:
        status = read_when_available({'protocol': 1, 'operation': 'status'}, deadline)
        assert type(status.get('busy')) is bool, 'Missing or invalid maintenance busy state'
        if not status['busy']:
            return status
        remaining = deadline - time.monotonic()
        assert remaining > 0, 'Maintenance worker did not quiesce within its original deadline'
        time.sleep(min(.2, remaining))


def task(action):
    deadline = time.monotonic() + 150
    wait_idle(deadline)
    # Exactly one mutating submit. Refusals and unknown errors remain failures.
    remaining = deadline - time.monotonic()
    assert remaining > 0, 'Maintenance task budget expired before submission'
    row = invoke({'protocol': 1, 'operation': 'submit', 'action': action}, timeout=min(15, remaining))
    assert row['state'] == 'queued', 'Task not durably registered before response'
    identity = row['id']
    while row['state'] in ('queued', 'running') and time.monotonic() < deadline:
        time.sleep(.2)
        row = read_when_available({'protocol': 1, 'operation': 'get', 'task_id': identity}, deadline)
    assert row['state'] == 'succeeded', 'Installed task did not confirm success: ' + action
    # A durable succeeded row precedes child exit/reaping; busy remains true
    # until then. Respect that contract before chaining another task.
    wait_idle(deadline)
    return row


def daemon_pid():
    result = subprocess.check_output(['systemctl', 'show', '--property=MainPID', '--value', 'v-ui-maintenance.service'], text=True).strip()
    assert result.isdecimal() and int(result) > 1, 'Independent root service not running: ' + json.dumps(maintenance_diagnostics(), sort_keys=True)
    return result


NETLINK_SYSTEMD_MARKER = 'VUI_NETLINK_SYSTEMD_ACCEPTANCE: installed20 panel-child TLS TCP URI/YAML passed'


def installed_core_read_capability(source, *, control=CONTROL, owner=0):
    """Bind fixture capability to installed Root bytes and the actual suite."""
    for parent in (*reversed(control.parents), control):
        info = parent.lstat()
        assert stat.S_ISDIR(info.st_mode) and info.st_uid in (0, owner)
        assert not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX
    def read(path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            assert stat.S_ISREG(before.st_mode) and before.st_uid == owner
            assert before.st_nlink == 1 and not before.st_mode & 0o022 and 0 < before.st_size <= 65536
            chunks = []; total = 0
            while True:
                chunk = os.read(fd, min(8192, 65537-total))
                if not chunk: break
                chunks.append(chunk); total += len(chunk)
                assert total <= 65536
            raw = b''.join(chunks)
            after = os.fstat(fd)
            linked = os.stat(path, follow_symlinks=False)
            identity = lambda info: (info.st_dev,info.st_ino,info.st_size,info.st_mode,info.st_uid,info.st_gid,info.st_nlink,info.st_mtime_ns,info.st_ctime_ns)
            assert len(raw) == before.st_size and identity(before) == identity(after) == identity(linked)
            return raw
        finally:
            os.close(fd)
    worker = control/'maintenance_cli_worker.py'
    raw = read(worker)
    def pairs(items):
        value = {}
        for key,item in items:
            assert key not in value
            value[key] = item
        return value
    receipt = json.loads(read(control/'ops-control.json'), object_pairs_hook=pairs)
    assert receipt['files'][str(worker)] == hashlib.sha256(raw).hexdigest(), 'Installed grant worker receipt mismatch'
    expected = (Path(source)/'deploy/maintenance_cli_worker.py').read_bytes()
    assert raw == expected, 'Installed grant worker differs from the selected suite source'
    assignments = [node.value for node in ast.parse(raw).body if isinstance(node, ast.Assign)
                   and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'SCOPES']
    assert len(assignments) == 1
    scopes = ast.literal_eval(assignments[0])
    legacy = {'diagnose','nodes','share','relay','certificates','operate','upgrade','destructive'}
    assert type(scopes) is set and scopes in (legacy, legacy | {'core_read'}), 'Unknown installed grant scope contract'
    return 'core_read' in scopes


def maintenance_long_duration(release_id):
    return release_id.startswith(('0.4.19-', '0.4.20-', '0.4.21-', '0.4.22-', '0.4.23-', '0.4.24-', '0.4.25-', '0.4.26-'))


def netlink_systemd_enabled(release_id):
    return os.getenv('VUI_NETLINK_SYSTEMD_TEST') == '1' and release_id.startswith(('0.4.20-', '0.4.21-', '0.4.22-', '0.4.23-', '0.4.24-', '0.4.25-', '0.4.26-'))


def verified_upgrade_fixture_rows(actual, expected):
    """Only exact, previously snapshotted synthetic rows may be disposed here."""
    known_remarks = {'published-upgrade-synthetic-draft',
                     'published-upgrade-synthetic-managed-xray-draft'}
    assert len(actual) == len(expected), 'Unknown CI node; installed-core fixture refuses cleanup'
    by_id = {row['id']: row for row in expected}
    assert len(by_id) == len(expected), 'Duplicate expected CI node IDs'
    for row in actual:
        old = by_id.get(row['id'])
        assert old and old['remark'] in known_remarks, 'Unrecognized CI fixture node'
        for key in ('id', 'tag', 'remark', 'core', 'protocol', 'port', 'mutation_revision'):
            assert row[key] == old[key], 'CI fixture node changed before guarded cleanup'
        assert re.fullmatch('[a-f0-9]{64}', row['mutation_revision']), 'Missing exact CI node revision'
    return actual


def wait_installed_admin_identity(api, expected_username, *, timeout=20):
    """Read-only post-menu readiness, within the original first-GET budget.

    Type=simple active does not imply bind/TLS readiness. Only an actual refused
    connect is retried. TLS, HTTP, JSON, identity and request timeouts fail closed.
    """
    assert type(timeout) in (int,float) and 0 < timeout <= 20, 'Invalid installed HTTPS readiness budget'
    assert type(expected_username) is str and re.fullmatch(r'[A-Za-z0-9_.-]{3,64}',expected_username), 'Invalid expected fixture identity'
    deadline=time.monotonic()+timeout
    while True:
        remaining=deadline-time.monotonic()
        if remaining<=0:
            raise AssertionError('Installed HTTPS identity was not ready within the original deadline')
        try:
            status,raw=api('/api/auth/me',method='GET',timeout=min(5,remaining),readiness=True)
        except ConnectionRefusedError:
            remaining=deadline-time.monotonic()
            if remaining<=0:
                raise AssertionError('Installed HTTPS listener remained refused until the original deadline') from None
            time.sleep(min(.1,remaining))
            continue
        if status!=200:
            raise AssertionError('Installed HTTPS must retain the authenticated identity; status='+str(status))
        identity=json.loads(raw)
        assert type(identity) is dict and set(identity)=={'id','username','role'}, 'Invalid installed public identity fields'
        assert type(identity['id']) is int and identity['id']>0, 'Invalid installed public identity id'
        assert identity['role']=='admin' and identity['username']==expected_username, 'Installed identity differs from the logged-in fixture owner'
        if time.monotonic()>deadline:
            raise AssertionError('Installed HTTPS identity arrived after its original deadline')
        return identity


def installed_netlink_smoke(check, source, work, api, ca, cert, key, verified_old_nodes, private_values, *,
                            expected_username, login_again=None):
    """Run only after original data/upgrade assertions on an empty hosted runner.

    Existing invalid synthetic drafts are removed by exact guarded API revision,
    not repaired or hidden in snapshots. A new fake Xray and the first explicit
    direct Sing-box node then run as real descendants of installed v-ui.service.
    """
    assert os.geteuid() != 0 and os.getenv('GITHUB_ACTIONS') == 'true'
    assert os.getenv('RUNNER_ENVIRONMENT') == 'github-hosted'
    assert os.getenv('GITHUB_REPOSITORY') == 'ForceMind/V-UI'
    assert os.getenv('VUI_SYSTEM_INSTALL_TEST') == '1'
    config = json.loads(Path('/etc/v-ui/service.json').read_bytes())
    if not netlink_systemd_enabled(config['release_id']):
        raise AssertionError('Installed netlink gate requires an opted-in supported candidate from 0.4.20 through 0.4.26')
    release = Path('/var/lib/v-ui/releases') / config['release_id']
    check.assertIn((source/'VERSION').read_text().strip(), ('0.4.20','0.4.21','0.4.22','0.4.23','0.4.24','0.4.25','0.4.26'))
    check.assertTrue(config['release_id'].startswith((source/'VERSION').read_text().strip()+'-'))

    def request(path, body=None, *, method=None, extra_headers=(), expected=200):
        status, raw = api(path,body,method=method,extra_headers=extra_headers,timeout=20)
        check.assertEqual(status,expected,'Installed netlink fixture API failed: '+path)
        return json.loads(raw)

    admin_identity_before=wait_installed_admin_identity(api,expected_username)

    def service_python(program):
        prefix = 'import sys;sys.path.insert(0,'+repr(str(release/'payload'))+');'
        result = subprocess.run(['sudo','-u','v-ui','/usr/bin/env',
            'VUI_DATA_DIR=/var/lib/v-ui/data',str(release/'runtime/python/bin/python3'),
            '-B','-I','-c',prefix+program],capture_output=True,text=True,timeout=30)
        check.assertEqual(result.returncode,0,'Installed service-user netlink fixture failed; private output withheld')
        return json.loads(result.stdout)

    def delete_owned(row):
        current = next((value for value in request('/api/inbounds') if value['id']==row['id']),None)
        check.assertIsNotNone(current,'Owned CI node disappeared before cleanup')
        for field in ('id','tag','mutation_revision'):
            check.assertEqual(current[field],row[field],'Refusing to delete a changed CI node')
        status, raw = api('/api/inbounds/'+str(row['id'])+'/delete',
                         {'expected_instance_revision':row['mutation_revision']},timeout=20)
        if status != 200:
            check.assertEqual(status,409,'Guarded CI cleanup failed')
            detail=json.loads(raw)['detail']
            check.assertIs(detail.get('saved'),True)
            check.assertIs(detail.get('applied'),False)
        check.assertNotIn(row['id'],{value['id'] for value in request('/api/inbounds')})

    for row in verified_upgrade_fixture_rows(request('/api/inbounds'),verified_old_nodes):
        delete_owned(row)
    check.assertEqual(request('/api/inbounds'),[])
    prior_proxy=request('/api/proxy/settings')
    check.assertIsNone(prior_proxy['pending'])
    check.assertIsNone(prior_proxy['active'])
    off={name:value for name,value in prior_proxy['settings'].items() if name!='local_password_set'}
    off.update(local_proxy_enabled=False,relay_enabled=False,selected_upstream_id=None)
    off_proxy=request('/api/proxy/settings',off,method='PUT',
        extra_headers=['If-Match: '+prior_proxy['revision']])
    off_settings=off_proxy['settings']
    print('Installed netlink preflight: all prior data assertions complete; exact known CI drafts disposed via guarded API; draft dual-OFF selected through If-Match')

    properties=subprocess.check_output(['systemctl','show','v-ui.service','--all',
        '--property=MainPID,User,Group,NoNewPrivileges,CapabilityBoundingSet,AmbientCapabilities,RestrictAddressFamilies'],text=True)
    properties=dict(line.split('=',1) for line in properties.splitlines() if '=' in line)
    check.assertEqual(set(properties['RestrictAddressFamilies'].split()),{'AF_INET','AF_INET6','AF_UNIX','AF_NETLINK'})
    check.assertEqual({name:properties[name] for name in ('User','Group','NoNewPrivileges','CapabilityBoundingSet','AmbientCapabilities')},
                      {'User':'v-ui','Group':'v-ui','NoNewPrivileges':'yes','CapabilityBoundingSet':'','AmbientCapabilities':''})
    panel_pid=int(properties['MainPID'])
    user_program="""
import hashlib,json,sqlite3
with sqlite3.connect('file:/var/lib/v-ui/data/v-ui.db?mode=ro',uri=True) as db:
 rows=db.execute('SELECT * FROM users ORDER BY 1').fetchall()
print(json.dumps(hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()))
"""
    users_before=service_python(user_program)
    # Use the existing installer-provided fake material through the ordinary
    # administrator API. This proves core TLS startup, not public CA trust or
    # managed certificate_id deployment under the installed system trust store.
    own_nodes=[]
    maintenance_grant=None
    def grant_cli(*arguments):
        result=subprocess.run(['sudo','-u','v-ui','/usr/bin/python3','-B','-I','-S',
            str(CONTROL/'maintenance_cli_worker.py'),*arguments],capture_output=True,text=True,timeout=60)
        check.assertEqual(result.returncode,0,'Installed CLI grant operation failed; private output withheld')
        return json.loads(result.stdout)
    try:
        from contextlib import ExitStack
        from loopback_helpers import unused_port
        from proxy_loopback_support import BODY, ReadyMihomoProcess, https_target, https_through
        from inbound_routing_real_helpers import yaml_client, uri_client
        from scripts.fetch_mihomo import native_pin
        import yaml
        client=Path(os.environ['VUI_TEST_MIHOMO'])
        provenance=json.loads(client.with_name('mihomo-provenance.json').read_text())
        for field,value in native_pin().items():check.assertEqual(provenance[field],value)
        check.assertEqual(hashlib.sha256(client.read_bytes()).hexdigest(),provenance['binary_sha256'])
        version=subprocess.run([str(client),'-v'],capture_output=True,text=True,timeout=15)
        check.assertEqual(version.returncode,0)
        check.assertTrue(version.stdout.startswith('Mihomo Meta v1.19.32 '))

        def create(core,mode):
            uid='22222222-2222-4222-8222-222222222222' if core=='xray' else '11111111-1111-4111-8111-111111111111'
            private_values.append(uid)
            payload={'core':core,'protocol':'vless','routing_mode':mode,'port':unused_port(),
                'enable':True,'tag':'installed-netlink-'+core,'remark':'installed-netlink-'+core,
                'settings':{'clients':[{'id':uid}]} if core=='xray' else {'users':[{'uuid':uid}]},
                'profile':{'security':'tls','transport':'raw' if core=='xray' else 'direct',
                           'server_name':'vpn.example.test','flow':'',
                           'certificate_path':'/var/lib/v-ui/data/certs/provided-fullchain.pem',
                           'key_path':'/var/lib/v-ui/data/certs/provided-privkey.pem'}}
            before_ids={row['id'] for row in request('/api/inbounds')}
            status,raw=api('/api/inbounds',payload,timeout=20)
            if status==200:
                row=json.loads(raw)['inbound']
            else:
                # Preserve exact ownership for cleanup after a saved/not-applied
                # failure. The original failing status is still asserted below.
                rows=[row for row in request('/api/inbounds') if row['id'] not in before_ids
                      and all(row[field]==payload[field] for field in ('core','protocol','port','tag','remark'))]
                if rows:
                    check.assertEqual(len(rows),1,'Ambiguous failed-create ownership')
                    own_nodes.append(rows[0])
                check.assertEqual(status,200,'Installed node creation must save AND apply')
            if status==200:own_nodes.append(row)
            editor=request('/api/inbounds/'+str(row['id'])+'/editor')
            check.assertIsNone(editor['certificate_id'])
            check.assertEqual(editor['profile']['certificate_path'],payload['profile']['certificate_path'])
            check.assertEqual(editor['profile']['key_path'],payload['profile']['key_path'])
            return row

        xray=create('xray','inherit')
        xray_before=request('/api/cores/status')['xray']
        check.assertTrue(xray_before['running'])
        xray_row_before=next(row for row in request('/api/inbounds') if row['id']==xray['id'])
        xray_digest_program=user_program.replace(
            "SELECT * FROM users ORDER BY 1","SELECT * FROM inbounds WHERE id = "+str(xray['id']))
        xray_digest_before=service_python(xray_digest_program)

        # Explicit check/apply of unchanged OFF settings, with fresh source CAS.
        revision=off_proxy['revision']
        checked=request('/api/proxy/check',{'base_revision':revision})
        check.assertTrue(checked['valid']);check.assertFalse(checked['applied'])
        check.assertEqual(checked['snapshot']['settings'],off_settings)
        applied=request('/api/proxy/apply',{'base_revision':revision,'source_fingerprint':checked['source_fingerprint']},
            extra_headers=['Idempotency-Key: '+hashlib.sha256(uuid.uuid4().bytes).hexdigest()])
        check.assertTrue(applied['applied']);check.assertEqual(applied['phase'],'committed')
        check.assertIsNone(applied['pinned_address'])
        sing=create('sing-box','direct')
        grant_ttl = '3600' if os.getenv('VUI_CORE_SWITCH_FIXTURE') else '600'
        maintenance_grant=grant_cli('create','diagnose,nodes,share,relay,certificates,operate,upgrade',grant_ttl)
        private_values.append(maintenance_grant['link'])
        one_time=maintenance_grant['link'].split('#grant=',1)[1]
        private_values.append(one_time)
        exchanged=request('/api/maintenance/exchange',{'grant':one_time})
        private_values.append(exchanged['token'])
        bearer=['Authorization: Bearer '+exchanged['token']]
        check.assertEqual(request('/api/maintenance/session',extra_headers=bearer)['grant_id'],maintenance_grant['id'])
        summary=next(row for row in request('/api/inbounds',extra_headers=bearer) if row['id']==sing['id'])
        check.assertTrue(summary['share_eligible']);check.assertTrue(summary['upstream_export_eligible'])
        check.assertEqual(summary['routing_status']['state'],'applied')
        shared_status,shared=api('/api/subscription/link/'+str(sing['id'])+'?host=127.0.0.1',extra_headers=bearer)
        check.assertEqual(shared_status,200,'Ordinary share must remain usable')
        check.assertTrue(shared.startswith(b'vless://'))
        # Delegated edit sends no filesystem material. The guarded maintenance
        # route must preserve the manually configured server-side TLS paths.
        edit_status,edit_raw=api('/api/inbounds/'+str(sing['id']),{
            'profile':{'security':'tls','transport':'direct','server_name':'vpn.example.test'},
            'expected_instance_revision':summary['mutation_revision']},method='PUT',extra_headers=bearer,timeout=20)
        current=next(row for row in request('/api/inbounds') if row['id']==sing['id'])
        for field in ('id','tag','port','core','protocol'):
            check.assertEqual(current[field],sing[field],'Delegated edit changed fixture identity')
        own_nodes[-1]=current
        check.assertEqual(edit_status,200,'Guarded no-path maintenance edit must apply')
        editor=request('/api/inbounds/'+str(sing['id'])+'/editor',extra_headers=bearer)
        check.assertNotIn('certificate_path',editor['profile'])
        check.assertNotIn('key_path',editor['profile'])
        state=request('/api/cores/status')
        check.assertTrue(state['sing-box']['running']);check.assertFalse(state['sing-box']['dirty'])
        check.assertEqual(state['sing-box']['version_required'],'1.14.2')

        # Read-only witness from the installed service UID, using its pinned
        # binary, actual watchdog child, /proc identity/cgroup/capabilities/FDs.
        def witness(core,status,node):
            return service_python('watchdog='+str(status['pid'])+';panel='+str(panel_pid)+';port='+str(node['port'])+';core='+repr(core)+';release='+repr(str(release))+';'+"""
import hashlib,json,os
from pathlib import Path
import psutil
root=Path(release)
ready=json.loads((root/'READY.json').read_text())
arch=ready['runtime_key'].split('-',1)[0]
binary=root/'payload/cores'/arch/core
manifest=json.loads((root/'payload/MANIFEST.json').read_text())
assert hashlib.sha256(binary.read_bytes()).hexdigest()==manifest['files'][str(binary.relative_to(root/'payload'))]['sha256']
children=[p for p in psutil.Process(watchdog).children(recursive=True) if Path(p.exe()).resolve()==binary.resolve()]
assert len(children)==1
child=children[0]
assert panel in [p.pid for p in child.parents()]
proc=Path('/proc')/str(child.pid)
assert '/system.slice/v-ui.service' in (proc/'cgroup').read_text()
props=dict(line.split(':',1) for line in (proc/'status').read_text().splitlines() if ':' in line)
assert all(int(value)==os.geteuid()!=0 for value in props['Uid'].split())
assert props['NoNewPrivs'].strip()=='1'
assert all(int(props[key].strip(),16)==0 for key in ('CapEff','CapPrm','CapBnd','CapAmb'))
listeners=[c for c in child.net_connections(kind='inet') if c.status==psutil.CONN_LISTEN]
assert len(listeners)==1 and listeners[0].laddr.port==port
print(json.dumps({'pid':child.pid,'created':child.create_time(),'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'port':port,'unit':'v-ui.service','nonroot':True,'capabilities':0}))
""")
        xray_witness=witness('xray',xray_before,xray)
        sing_witness=witness('sing-box',state['sing-box'],sing)
        with ExitStack() as stack:
            target_port,traffic=https_target(stack,'127.0.0.1',cert,key)
            home=Path(work)/'installed-netlink-client'
            home.mkdir()
            empty_roots=home/'empty-roots';empty_roots.mkdir()
            environment={**os.environ,'SSL_CERT_FILE':str(ca),'SSL_CERT_DIR':str(empty_roots)}
            artifacts=[]
            for source_format in ('uri','clash-yaml'):
                status,raw=api('/api/subscription/upstream/'+str(sing['id']),
                    {'format':source_format,'server':'127.0.0.1','acknowledge_tcp_only':True},extra_headers=bearer)
                check.assertEqual(status,200,'TCP-only upstream export failed')
                artifacts.append({'format':source_format,'sha256':hashlib.sha256(raw).hexdigest()})
                import socket
                with socket.socket() as local, socket.socket() as controller:
                    local.bind(('127.0.0.1',0));controller.bind(('127.0.0.1',0))
                    client_port,controller_port=local.getsockname()[1],controller.getsockname()[1]
                if source_format=='uri':
                    provider=home/'upstream.txt';provider.write_bytes(raw);provider.chmod(0o600)
                    client_config=uri_client(provider,client_port,controller_port)
                else:
                    # TCP-upstream YAML is one exported node, unlike an
                    # ordinary subscription. Add only the client shell wrapper.
                    node=yaml.safe_load(raw)
                    check.assertIsInstance(node,dict)
                    check.assertEqual(node['type'],'vless')
                    check.assertEqual(node['network'],'tcp')
                    check.assertIs(node['tls'],True)
                    check.assertIs(node['skip-cert-verify'],False)
                    check.assertIs(node['udp'],False)
                    client_config=yaml_client({'proxies':[node]},[node['name']],client_port,controller_port)
                check.assertNotIn('DIRECT',json.dumps(client_config))
                path=home/(source_format+'.yaml');path.write_text(yaml.safe_dump(client_config));path.chmod(0o600)
                process=ReadyMihomoProcess(stack,[str(client),'-d',str(home),'-f',str(path)],home/(source_format+'.log'),environment)
                process.start(client_port)
                marker='installed-netlink-'+source_format
                check.assertEqual(https_through(client_port,'127.0.0.1',target_port,ca,marker,protocol='socks5h'),(200,BODY))
                process.stop()
            check.assertEqual([path for _,_,path in traffic.snapshot()],['/installed-netlink-uri','/installed-netlink-clash-yaml'])
        check.assertEqual(request('/api/cores/status')['xray'],xray_before)
        check.assertEqual(witness('xray',xray_before,xray),xray_witness)
        xray_after=next(row for row in request('/api/inbounds') if row['id']==xray['id'])
        check.assertEqual({k:v for k,v in xray_after.items() if k!='routing_status'},
                          {k:v for k,v in xray_row_before.items() if k!='routing_status'})
        check.assertEqual(service_python(xray_digest_program),xray_digest_before)
        current_proxy=request('/api/proxy/settings')
        check.assertEqual(current_proxy['revision'],revision)
        check.assertEqual(current_proxy['settings'],off_settings)
        check.assertEqual(current_proxy['active']['settings'],off_settings)
        check.assertIsNone(current_proxy['pending'])
        check.assertEqual(service_python(user_program),users_before)
        check.assertEqual(request('/api/auth/me'),admin_identity_before)
        print('Installed netlink evidence: '+json.dumps({'release_id':config['release_id'],
            'panel_pid':panel_pid,'xray':xray_witness,'sing-box':sing_witness,'artifacts':artifacts,
            'global_flags':'dual-OFF','account_and_xray_preserved':True,
            'material_mode':'admin-provided-paths','maintenance_bearer':True,
            'source_case':os.getenv('VUI_PUBLISHED_INSTALL_VERSION','fresh-candidate')},sort_keys=True))
        if os.getenv('VUI_CORE_SWITCH_FIXTURE'):
            from core_switch_systemd import installed_core_switch
            login_started = int(time.time())
            login_again()
            def before_restore():
                identity = request('/api/maintenance/session', extra_headers=bearer)
                check.assertEqual(identity['grant_id'], maintenance_grant['id'])
                check.assertGreater(identity['expires_at'], time.time())

            def after_restore():
                nonlocal maintenance_grant
                old_login = api('/api/auth/me')[0]
                old_bearer = api('/api/maintenance/session', extra_headers=bearer)[0]
                checked_at = time.time()
                if old_bearer == 401:
                    maintenance_grant = None
                login_again()
                check.assertGreater(exchanged['expires_at'], checked_at)
                check.assertGreater(login_started + 3600, checked_at)
                check.assertEqual(old_login, 401, 'Restore must revoke the old login')
                check.assertEqual(old_bearer, 401, 'Restore must revoke the archived maintenance grant')
            installed_core_switch(check, source, work, api, service_python, ca, cert, key, client,
                                  {'sing-box': sing, 'xray': xray}, before_restore, after_restore)

    finally:
        try:
            if maintenance_grant is not None:grant_cli('revoke',str(maintenance_grant['id']))
        finally:
            for row in reversed(own_nodes):delete_owned(row)
            check.assertEqual(request('/api/inbounds'),[])
            check.assertEqual(service_python(user_program),users_before)
    if config['release_id'].startswith('0.4.20-'):
        print(NETLINK_SYSTEMD_MARKER)
    elif config['release_id'].startswith('0.4.21-'):
        print('VUI_SIMPLE_OPERATIONS_INSTALLED_ACCEPTANCE: installed21 panel-child TLS TCP URI/YAML passed')
    elif config['release_id'].startswith('0.4.24-'):
        print('VUI_MAINTENANCE_SHARE_INSTALLED_ACCEPTANCE: installed24 panel-child TLS TCP URI/YAML passed')
    elif config['release_id'].startswith('0.4.25-'):
        print('VUI_MAINTENANCE_SHARE_INSTALLED_ACCEPTANCE: installed25 panel-child TLS TCP URI/YAML passed')
    elif config['release_id'].startswith('0.4.26-'):
        print('VUI_MAINTENANCE_SHARE_INSTALLED_ACCEPTANCE: installed26 panel-child TLS TCP URI/YAML passed')
    else:
        print('VUI_MAINTENANCE_SHARE_INSTALLED_ACCEPTANCE: installed22 panel-child TLS TCP URI/YAML passed')


def nonroot_smoke(check, source, kit, api, expected_proxy):
    assert os.geteuid() != 0
    assert os.getenv('GITHUB_ACTIONS') == 'true' and os.getenv('RUNNER_ENVIRONMENT') == 'github-hosted'
    assert os.getenv('VUI_SYSTEM_INSTALL_TEST') == '1'
    suite = Path(os.environ['VUI_SETUP_RELEASE_SOURCE']) if os.getenv('VUI_SETUP_INSTALL') == '1' else Path(source)
    core_read = installed_core_read_capability(suite)
    if core_read:
        program = """import sqlite3,json,hashlib,os
from pathlib import Path
root=Path('/var/lib/v-ui/data')
with sqlite3.connect('file:'+str(root/'v-ui.db')+'?mode=ro',uri=True) as db:
 users=db.execute('SELECT id,username,password_hash,is_active FROM users ORDER BY id').fetchall()
 nodes=db.execute('SELECT id,user_id,core,enable,port,protocol,settings,stream_settings,tag,remark,total,expiry_time,routing_mode FROM inbounds ORDER BY id').fetchall()
 info=root.stat()
 states={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for core in ('sing-box','xray') for p in (root/'runtime'/core).glob('*.json')}
print(hashlib.sha256(json.dumps({'users':users,'nodes':nodes,'generation':[info.st_dev,info.st_ino],'states':states},sort_keys=True).encode()).hexdigest())
"""
        def witness():
            value = subprocess.check_output(['sudo','-u','v-ui','/usr/bin/python3','-B','-I','-S','-c',program],text=True,timeout=20).strip()
            check.assertRegex(value, r'^[a-f0-9]{64}$')
            return value
    status = wait_idle(time.monotonic() + 15)
    pid = daemon_pid()
    check.assertEqual(status['services']['v-ui.service'], 'active')
    check.assertTrue(status['ready'])
    check.assertRegex(status['release_id'], r'^[0-9]+\.[0-9]+\.[0-9]+-[a-f0-9]{12}$')
    check.assertIn(status['target'], ('x86_64-gnu', 'aarch64-gnu', 'x86_64-musl', 'aarch64-musl'))
    invoke({'protocol': 1, 'operation': 'status'}, user='nobody', expect_success=False)
    # Fixed JSON field contract is also enforced across the installed channel.
    invoke({'protocol': 1, 'operation': 'submit', 'action': 'status', 'unit': 'sshd.service'}, expect_success=False)
    task('status')
    backup = task('backup')
    check.assertEqual(backup['result']['backup_id'], backup['id'])
    task('stop')
    check.assertEqual(daemon_pid(), pid)
    check.assertEqual(invoke({'protocol': 1, 'operation': 'status'})['services']['v-ui.service'], 'inactive')
    # The prior persisted task is retrievable while the entire panel is stopped.
    check.assertEqual(invoke({'protocol': 1, 'operation': 'get', 'task_id': backup['id']})['state'], 'succeeded')
    if core_read:
        stopped_before = witness()
        stopped_selection = invoke({'protocol':1,'operation':'core_status'})
        check.assertEqual(witness(),stopped_before,'Stopped readonly core status changed business/runtime data')
        check.assertIn(stopped_selection.get('outcome'), ('bundle_default','core_pending','core_selected'))
        check.assertEqual(invoke({'protocol':1,'operation':'status'})['services']['v-ui.service'], 'inactive')
        check.assertEqual(daemon_pid(), pid)
    task('start')
    check.assertEqual(daemon_pid(), pid)
    task('restart')
    check.assertEqual(daemon_pid(), pid)
    logs = invoke({'protocol': 1, 'operation': 'logs'})
    check.assertTrue(logs['redacted'])
    check.assertLessEqual(len(logs['entries']), 60)
    for row in logs['entries']:
        check.assertEqual(set(row), {'timestamp', 'unit', 'code', 'summary'})
    # Real installed grant launcher validates the selected package and runtime
    # after dropping to the service user. Never echo its one-time link to CI.
    def grants(*arguments):
        result = subprocess.run(['sudo', '-u', 'v-ui', '/usr/bin/python3', '-B', '-I', '-S',
            str(CONTROL / 'maintenance_cli_worker.py'), *arguments], capture_output=True, text=True, timeout=60)
        check.assertEqual(result.returncode, 0, 'Installed nonroot grant CLI failed')
        return json.loads(result.stdout)
    if core_read:
        before = witness()
        tasks_before = invoke({'protocol':1,'operation':'list'})
        direct = invoke({'protocol':1,'operation':'core_status'})
        check.assertEqual(direct, stopped_selection)
        invoke({'protocol':1,'operation':'core_status'}, user='nobody', expect_success=False)
        for value in ({'protocol':1,'operation':'core_status','source':'a'*40},
                      {'protocol':1,'operation':'submit','action':'core_status'},
                      {'protocol':1,'operation':'submit','action':'update-core'}):
            invoke(value,expect_success=False)
        def exchange_scope(scopes):
            row = grants('create', scopes, '600')
            result, raw = api('/api/maintenance/exchange', {'grant':row['link'].split('#grant=',1)[1]})
            check.assertEqual(result,200,'Core readonly grant exchange failed; private output withheld')
            token = json.loads(raw)['token']
            return row,['Authorization: Bearer '+token]
        legacy, legacy_headers = exchange_scope('diagnose,nodes,share,relay,certificates,operate,upgrade,destructive')
        check.assertEqual(api('/api/maintenance/core-selection',extra_headers=legacy_headers)[0],403)
        grants('revoke',str(legacy['id']))
        record, headers = exchange_scope('core_read')
        result, raw = api('/api/maintenance/core-selection',extra_headers=headers)
        check.assertEqual(result,200,'Installed core readonly API failed; private output withheld')
        check.assertEqual(json.loads(raw),direct)
        check.assertNotRegex(raw.decode(),r'[a-f0-9]{64}')
        check.assertEqual(api('/api/maintenance/tasks',extra_headers=headers)[0],403)
        check.assertEqual(api('/api/maintenance/core-selection?source=untrusted',extra_headers=headers)[0],422)
        grants('revoke',str(record['id']))
        check.assertEqual(api('/api/maintenance/core-selection',extra_headers=headers)[0],401)
        check.assertEqual(invoke({'protocol':1,'operation':'list'}),tasks_before)
        check.assertEqual(witness(),before,'Readonly core status changed business/runtime data')
        check.assertEqual(invoke({'protocol':1,'operation':'status'})['services']['v-ui.service'],'active')
        check.assertEqual(daemon_pid(),pid)
        print('VUI_CORE_READ_ONLY_ACCEPTANCE: explicit grant, Root socket, stopped intent, fixed projection and unchanged data passed')
    grant = grants('create', 'diagnose', '600')
    check.assertIn('/maintenance#grant=', grant['link'])
    check.assertTrue(any(row['id'] == grant['id'] for row in grants('list')))
    grants('revoke', str(grant['id']))
    long_duration = maintenance_long_duration(status['release_id'])
    if long_duration:
        long_grant = grants('create', 'diagnose', '15552000')
        check.assertEqual(long_grant['expires_at'] - long_grant['created_at'], 15552000)
        check.assertEqual(long_grant['scopes'], ['diagnose'])
        grants('revoke', str(long_grant['id']))
    from online_install_helpers import PrivateMenuSession
    with PrivateMenuSession(['sudo', '/usr/local/sbin/v-ui'], cwd='/tmp', env=dict(os.environ), timeout=60) as terminal:
        terminal.expect('请选择: '); terminal.send('13\n')
        terminal.expect('请选择授权操作: '); terminal.send('1\n')
        terminal.expect('请选择权限 [1/2/3/4，默认 1]: ' if core_read else '请选择权限 [1/2/3，默认 1]: '); terminal.send('\n')
        terminal.expect('有效分钟数 1–259200，或输入180天/180d（默认30分钟）: ' if long_duration
                        else '有效分钟数 1–120（默认 30）: ')
        terminal.send('180天\n' if long_duration else '\n')
        issued = terminal.expect('请选择: ')
        records = [json.loads(line) for line in issued.splitlines() if line.startswith('{')]
        check.assertEqual(len(records), 1, 'Terminal grant result missing')
        check.assertEqual(records[0]['expires_at'] - records[0]['created_at'], 15552000 if long_duration else 1800)
        check.assertEqual(records[0]['scopes'], ['diagnose'])
        terminal.send('12\n'); terminal.expect('确认下载官方稳定版本并沿用原配置升级 [y/N]: ')
        terminal.send('n\n'); cancelled = terminal.expect('请选择: ')
        check.assertIn('已取消，未下载或更改安装', cancelled)
        terminal.send('0\n'); terminal.finish()
    grants('revoke', str(records[0]['id']))
    # Run the real upgrade tail from a task worker independent of the panel.
    # Same-package activation is intentional; old14/15 product upgrades retain
    # their separate real published-source acceptance in this workflow.
    pin = hashlib.sha256((Path(kit) / 'SHA256SUMS').read_bytes()).hexdigest()
    result = subprocess.run(['sudo', '/usr/bin/python3', '-B', '-I', '-S',
        str(Path(source) / 'scripts/system_maintenance_smoke.py'), '--candidate-kit', str(kit), '--sha256', pin],
        capture_output=True, text=True, timeout=900)
    check.assertEqual(result.returncode, 0, 'Exact candidate isolated upgrade task failed; output withheld')
    record = json.loads(result.stdout)
    check.assertEqual(record['state'], 'succeeded')
    check.assertEqual(record['failed_state'], 'failed')
    check.assertEqual(record['downloaded_bundles'], [record['target_bundle']])
    check.assertEqual(daemon_pid(), pid)
    check.assertEqual(invoke({'protocol': 1, 'operation': 'get', 'task_id': record['id']})['state'], 'succeeded')
    deadline = time.monotonic() + 30
    while True:
        try:
            if api('/api/auth/me')[0] == 200: break
        except (OSError, http.client.HTTPException):
            pass
        if time.monotonic() >= deadline: raise AssertionError('Preserved authenticated session unavailable after maintenance upgrade')
        time.sleep(.2)
    check.assertEqual(json.loads(api('/api/proxy/settings')[1]), expected_proxy)
    # A daemon restart retains final task state instead of inventing success.
    subprocess.run(['sudo', 'systemctl', 'restart', 'v-ui-maintenance.service'], check=True, timeout=50)
    deadline = time.monotonic() + 20
    while True:
        try:
            persisted = invoke({'protocol': 1, 'operation': 'get', 'task_id': record['id']})
            break
        except AssertionError:
            if time.monotonic() >= deadline: raise
            time.sleep(.2)
    check.assertEqual(persisted['state'], 'succeeded')
    print('Remote system acceptance: SO_PEERCRED/file permissions, closed JSON, persistent async status/backup/stop/start/restart, independent root PID, redacted logs, nonroot grants, exact-candidate upgrade with single-target transport and retained auth/proxy state passed; official newer-release network selection remains a separate unit boundary.')


def load(name):
    spec = importlib.util.spec_from_file_location('_vui_acceptance_' + name, CONTROL / (name + '.py'))
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


def root_candidate_fixture(kit, pin):
    # This acceptance-only entry is never packaged in a deployed application.
    # Its input comes from the workflow's exact-head candidate kit and digest.
    assert os.getuid() == 0 and sys.flags.isolated and sys.flags.no_site
    ops = load('system_ops'); ops.verify_installation()
    config = ops.decode_json(ops.trusted_bytes(ops.CONFIG, 16384))
    upgrade = load('maintenance_upgrade')
    daemon = load('maintenance_daemon')
    kit = Path(kit)
    raw = (kit / 'SHA256SUMS').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == pin
    upgrade.checksums(raw, pin)
    release = json.loads((kit / 'RELEASE.json').read_bytes())
    version = 'v' + release['version']
    base = upgrade.BASE + version + '/'
    downloaded = []
    def fixed_transport(url, path=None, *, limit):
        assert url.startswith(base) and url.count('/') == base.count('/')
        name = url[len(base):]
        assert name in upgrade.ASSETS | upgrade.BUNDLES | {'SHA256SUMS'}
        blob = (kit / name).read_bytes()
        assert len(blob) <= limit
        if path is None: return blob
        with path.open('xb') as output: output.write(blob)
        downloaded.append(name)
        return hashlib.sha256(blob).hexdigest()
    upgrade.download = fixed_transport
    daemon.load = lambda name: types_upgrade if name == 'maintenance_upgrade' else None
    # The real worker/store finalizes the persisted task. Only trusted candidate
    # transport and same-version selection are injected in this disposable test.
    import types
    types_upgrade = types.SimpleNamespace(upgrade=lambda controller: upgrade.install_release(controller, config, version, pin))
    store = daemon.Store(ops)
    # Corrupted target bytes never reach the installer and still produce a
    # durable failed task. Original service/configuration remain available.
    def corrupt_transport(url, path=None, *, limit):
        digest = fixed_transport(url, path, limit=limit)
        return '0' * 64 if path is not None and path.name in upgrade.BUNDLES else digest
    upgrade.download = corrupt_transport
    failed = {'id': uuid.uuid4().hex, 'action': 'upgrade', 'state': 'queued', 'created_at': int(time.time())}
    store.write(failed)
    bad_pid = os.fork()
    if bad_pid == 0:
        daemon.execute(ops, store, failed)
    os.waitpid(bad_pid, 0)
    failed = store.read(failed['id'])
    assert failed['state'] == 'failed'
    assert ops.decode_json(ops.trusted_bytes(ops.CONFIG, 16384)) == config
    ops.verify_installation()
    upgrade.download = fixed_transport
    row = {'id': uuid.uuid4().hex, 'action': 'upgrade', 'state': 'queued', 'created_at': int(time.time())}
    store.write(row)
    # Record selected file names across fork without disclosing URLs or bytes.
    trace = Path('/var/lib/v-ui-maintenance') / ('.acceptance-' + uuid.uuid4().hex)
    original = upgrade.download
    def traced(*args, **kwargs):
        result = original(*args, **kwargs)
        if len(args) > 1 and args[1] is not None:
            with trace.open('a') as output: output.write(args[1].name + '\n')
        return result
    upgrade.download = traced
    pid = os.fork()
    if pid == 0: daemon.execute(ops, store, row)
    _, status = os.waitpid(pid, 0)
    result = store.read(row['id'])
    names = trace.read_text().splitlines() if trace.exists() else []
    trace.unlink(missing_ok=True)
    assert os.waitstatus_to_exitcode(status) == 0 and result['state'] == 'succeeded'
    print(json.dumps({'id': row['id'], 'state': result['state'], 'failed_state': failed['state'], 'target_bundle': 'vui-linux-' + config['target'] + '.zip',
        'downloaded_bundles': sorted(set(names) & upgrade.BUNDLES)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-kit', required=True)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    root_candidate_fixture(args.candidate_kit, args.sha256)
