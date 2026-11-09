"""Exercise installed core switching inside the existing fresh systemd test."""

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import zipfile

import yaml

from inbound_routing_real_helpers import yaml_client
from proxy_loopback_support import BODY, ReadyMihomoProcess, https_target, https_through


VERSIONS = {'sing-box': ('1.14.2', '1.14.3'), 'xray': ('26.3.27', '26.10.21')}
TARGETS = {'x86_64-gnu', 'x86_64-musl', 'aarch64-gnu', 'aarch64-musl'}


def installed_core_switch(check, source, work, api, service_python, ca, cert, key, client, nodes,
                          before_restore, after_restore):
    # This helper shares the actual fresh install and its authenticated session.
    assert os.geteuid() != 0 and os.getenv('GITHUB_ACTIONS') == 'true'
    assert os.getenv('RUNNER_ENVIRONMENT') == 'github-hosted'
    assert os.getenv('GITHUB_REPOSITORY') == 'ForceMind/V-UI'
    assert os.getenv('VUI_SYSTEM_INSTALL_TEST') == '1'
    assert os.getenv('VUI_PUBLISHED_INSTALL_VERSION') is None
    fixture = Path(os.environ['VUI_CORE_SWITCH_FIXTURE'])
    assert fixture == Path('/tmp/vui-oneclick/core-switch-fixture')
    check.assertEqual(set(nodes), set(VERSIONS))
    check.assertNotEqual(nodes['sing-box']['port'], nodes['xray']['port'])
    source_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    assert re.fullmatch('[a-f0-9]{40}', source_sha)
    candidates, new_digests = {}, {}
    for core, (_, candidate_version) in VERSIONS.items():
        candidate = fixture/core/'candidate'
        manifest = json.loads((candidate/'CORE-RELEASE.json').read_bytes())
        check.assertEqual(manifest['builder_source'], source_sha)
        check.assertEqual(manifest['compatibility']['app_sources'], [source_sha])
        check.assertEqual((manifest['core'], manifest['version']), (core, candidate_version))
        check.assertEqual(len(manifest['packs']), 4)
        check.assertEqual({row['target'] for row in manifest['packs']}, TARGETS)
        for row in manifest['packs']:
            check.assertEqual(row['name'], f"vui-core-{core}-{candidate_version}-{row['target']}.zip")
        expected = {'CORE-RELEASE.json', 'SHA256SUMS', *(row['name'] for row in manifest['packs'])}
        check.assertEqual({path.name for path in candidate.iterdir()}, expected)
        target = next(row for row in manifest['packs'] if row['target'] == 'x86_64-gnu')
        with zipfile.ZipFile(candidate/target['name']) as archive:
            pack = archive.read('CORE-PACK.json')
        check.assertEqual(hashlib.sha256(pack).hexdigest(), target['manifest_sha256'])
        new_digests[core] = json.loads(pack)['files']['bin/'+core]['sha256']
        check.assertRegex(new_digests[core], r'^[a-f0-9]{64}$')
        candidates[core] = candidate, expected

    store = Path('/var/lib/v-ui-core')
    metadata = Path('/etc/v-ui')
    check.assertFalse(os.path.lexists(store))
    for name in ('core-selection.json', 'core-selection.fence.json', 'CORE_UPDATE_PENDING.json',
                 'core-selection.lock', '.core-update-active', '.core-update-history', 'core-catalog'):
        check.assertFalse(os.path.lexists(metadata/name))
    runner_root = Path('/run/vui-core-switch-fixture')
    check.assertFalse(os.path.lexists(runner_root))
    with ExitStack() as stack:
        candidate_dirs = [path for core in VERSIONS
                          for path in (runner_root/core, runner_root/core/'candidate')]
        subprocess.run(['sudo', 'install', '-d', '-o', 'root', '-g', 'root', '-m', '0700',
                        str(runner_root), *(str(path) for path in candidate_dirs)], check=True)
        stack.callback(subprocess.run, ['sudo', 'rm', '-rf', str(runner_root)], check=True)
        runner = runner_root/'runner.py'
        subprocess.run(['sudo', 'install', '-o', 'root', '-g', 'root', '-m', '0600',
                        str(source/'tests/core_switch_fixture_runner.py'), str(runner)], check=True)
        for core, (candidate, expected) in candidates.items():
            for name in sorted(expected):
                subprocess.run(['sudo', 'install', '-o', 'root', '-g', 'root', '-m', '0600',
                                str(candidate/name), str(runner_root/core/'candidate'/name)], check=True)

        target_port, traffic = https_target(stack, '127.0.0.1', cert, key)
        home = Path(work)/'installed-core-switch-client'
        home.mkdir(mode=0o700)
        roots = home/'empty-roots'
        roots.mkdir(mode=0o700)
        env = {**os.environ, 'SSL_CERT_FILE':str(ca), 'SSL_CERT_DIR':str(roots)}
        markers = []

        def through(core, label):
            if core == 'sing-box':
                status, raw = api('/api/subscription/upstream/'+str(nodes[core]['id']),
                    {'format':'clash-yaml', 'server':'127.0.0.1', 'acknowledge_tcp_only':True})
                check.assertEqual(status, 200, 'Installed sing-box TCP upstream export failed')
                profile = yaml.safe_load(raw)
            else:
                status, raw = api('/api/subscription/mihomo.yaml?host=127.0.0.1&core=xray')
                check.assertEqual(status, 200, 'Installed Xray ordinary Mihomo export failed')
                profiles = yaml.safe_load(raw)['proxies']
                check.assertEqual(len(profiles), 1, 'Ordinary export must contain only the Xray fixture')
                profile = profiles[0]
                check.assertEqual(profile.get('flow', ''), '')
                check.assertNotIn('packet-encoding', profile)
            check.assertEqual((profile['type'], profile['network']), ('vless', 'tcp'))
            check.assertEqual((profile['server'], profile['port']), ('127.0.0.1', nodes[core]['port']))
            check.assertEqual(profile['servername'], 'vpn.example.test')
            check.assertIs(profile['tls'], True)
            check.assertIs(profile['udp'], False)
            check.assertIs(profile['skip-cert-verify'], False)
            with socket.socket() as local, socket.socket() as controller:
                local.bind(('127.0.0.1', 0))
                controller.bind(('127.0.0.1', 0))
                local_port, controller_port = local.getsockname()[1], controller.getsockname()[1]
            configuration = yaml_client({'proxies':[profile]}, [profile['name']], local_port, controller_port)
            check.assertNotIn('DIRECT', json.dumps(configuration))
            path = home/(label+'.yaml')
            path.write_text(yaml.safe_dump(configuration))
            path.chmod(0o600)
            with ExitStack() as client_stack:
                process = ReadyMihomoProcess(client_stack, [str(client), '-d', str(home), '-f', str(path)],
                                             home/(label+'.log'), env)
                process.start(local_port)
                try:
                    response = https_through(local_port, '127.0.0.1', target_port, ca, label, protocol='socks5h')
                except TimeoutError:
                    check.fail('Installed TCP fixture timed out for ' + core + ' ' + label)
                check.assertEqual(response, (200, BODY), core + ' ' + label)
            markers.append('/'+label)
            return hashlib.sha256(raw).hexdigest()

        def snapshot():
            status, raw = api('/api/cores/status')
            check.assertEqual(status, 200)
            status_by_core = json.loads(raw)
            running = {}
            for core, node in nodes.items():
                observed = status_by_core[core]
                check.assertIs(observed['running'], True)
                check.assertIs(type(observed['pid']), int)
                running[core] = (node['id'], node['port'], observed['pid'])
            panel = int(subprocess.check_output(['systemctl', 'show', 'v-ui.service', '-p', 'MainPID',
                                                 '--value'], text=True).strip())
            witness = service_python('nodes='+repr(running)+';panel='+repr(panel)+';'+"""
import hashlib,json,os,sqlite3
from pathlib import Path
import psutil
root=Path('/var/lib/v-ui')
data=root/'data'
current=(root/'CURRENT.json').read_bytes()
payload=root/'releases'/json.loads(current)['release_id']/'payload'
manifest=json.loads((payload/'MANIFEST.json').read_bytes())
cores={}
with sqlite3.connect((data/'v-ui.db').as_uri()+'?mode=ro',uri=True) as database:
 users=hashlib.sha256(json.dumps(database.execute('SELECT * FROM users ORDER BY 1').fetchall()).encode()).hexdigest()
 for core,(node,port,watchdog) in nodes.items():
  parent=psutil.Process(watchdog)
  children=parent.children()
  assert len(children)==1
  child=children[0]
  assert panel in [item.pid for item in child.parents()]
  assert all(uid==os.geteuid()!=0 for uid in child.uids())
  proc=Path('/proc')/str(child.pid)
  assert (proc/'cgroup').read_text()=='0::/system.slice/v-ui.service\\n'
  assert any(item.status==psutil.CONN_LISTEN and item.laddr.port==port
             for item in child.net_connections(kind='inet'))
  digest=hashlib.sha256()
  with (proc/'exe').open('rb') as executable:
   for chunk in iter(lambda:executable.read(1048576),b''):
    digest.update(chunk)
  row=database.execute('SELECT id,instance_id,core,protocol,port,enable,routing_mode,settings,stream_settings,tag FROM inbounds WHERE id=?',(node,)).fetchone()
  assert row is not None and row[2]==core and row[4]==port
  intent=json.loads((data/'runtime'/core/'state.json').read_bytes())
  assert intent['enabled'] is True
  cores[core]={'watchdog':watchdog,'watchdog_created':parent.create_time(),
    'pid':child.pid,'created':child.create_time(),'binary':digest.hexdigest(),
    'bundle':manifest['files']['cores/x86_64/'+core]['sha256'],
    'node':hashlib.sha256(json.dumps(row).encode()).hexdigest(),
    'intent':[intent['revision'],intent['enabled']]}
print(json.dumps({'panel':panel,'current':hashlib.sha256(current).hexdigest(),
 'data':[data.stat().st_dev,data.stat().st_ino],'users':users,'cores':cores}))
""")
            for core in nodes:
                witness['cores'][core]['version'] = status_by_core[core]['version_required']
                check.assertEqual(status_by_core[core]['applied_revision'], witness['cores'][core]['intent'][0])
            return witness

        def task(core, action, revision, *, rejected=False, backup=()):
            case = core + ' ' + action
            args = [core, action, source_sha, revision or 'none']
            if action == 'port-conflict':
                args.append(str(nodes[core]['port']))
            if action == 'restore-backup':
                args.extend(backup)
            output = subprocess.run(['sudo', '--preserve-env=GITHUB_ACTIONS,RUNNER_ENVIRONMENT,GITHUB_REPOSITORY',
                '/usr/bin/setsid', '--wait', '/usr/bin/python3', '-B', '-I', '-S', str(runner), *args],
                capture_output=True, text=True, timeout=650)
            try:
                result = json.loads(output.stdout)
            except ValueError:
                check.fail(case + ' Root core-switch fixture did not return its closed response')
            check.assertIsInstance(result, dict)
            error = result.get('error')
            code = error if type(error) is str and re.fullmatch(r'[A-Z0-9_]{1,80}', error) else 'NO_CODE'
            check.assertEqual(output.returncode == 0, not rejected,
                              case + ' installed core-switch outcome mismatch ' + code)
            check.assertIs(result['ok'], not rejected)
            if rejected:
                check.assertEqual(set(result), {'ok', 'error'})
                check.assertEqual(code, 'ASSET_DIGEST')
            else:
                fields = {'ok', 'outcome', 'selection_revision', 'panel_running'}
                if action == 'update':
                    fields.update(('backup_name', 'backup_sha256'))
                    check.assertRegex(result['backup_name'], r'^core-maintenance-[a-f0-9]{32}\.zip$')
                    check.assertRegex(result['backup_sha256'], r'^[a-f0-9]{64}$')
                check.assertEqual(set(result), fields)
                check.assertIs(result['panel_running'], True)
                check.assertRegex(result['selection_revision'], r'^[a-f0-9]{64}$')
                check.assertNotEqual(result['selection_revision'], revision)
            return result

        def selected(revision):
            check.assertFalse(os.path.lexists(metadata/'CORE_UPDATE_PENDING.json'))
            if revision is None:
                check.assertFalse(os.path.lexists(metadata/'core-selection.json'))
                return None
            state = json.loads((metadata/'core-selection.json').read_bytes())
            check.assertEqual((state['revision'], state['app_source'], state['target']),
                              (revision, source_sha, 'x86_64-gnu'))
            return state

        status, raw_identity = api('/api/auth/me')
        check.assertEqual(status, 200)
        original_identity = json.loads(raw_identity)
        revision = None
        restore_backup = None
        for core, (bundle_version, candidate_version) in VERSIONS.items():
            peer = 'xray' if core == 'sing-box' else 'sing-box'
            prefix = 'core-switch' if core == 'sing-box' else 'xray-core-switch'
            initial = snapshot()
            initial_selection = selected(revision)
            installed = initial['cores'][core]
            check.assertEqual(installed['binary'], installed['bundle'])
            check.assertEqual(installed['version'], bundle_version)
            check.assertNotEqual(installed['binary'], new_digests[core])

            def traffic_at(stage):
                label = prefix+'-'+stage
                return {core:through(core, label), peer:through(peer, label+'-peer-'+peer)}

            export_digests = traffic_at('before')
            task(core, 'bad-digest', revision, rejected=True)
            check.assertEqual(snapshot(), initial, 'Bad candidate changed either installed core or the panel')
            check.assertEqual(selected(revision), initial_selection)

            previous = initial_selection
            for action, stage, outcome, binary, version in (
                    ('port-conflict', 'recovered', 'restored_previous', installed['binary'], bundle_version),
                    ('update', 'updated', 'switched', new_digests[core], candidate_version),
                    ('rollback', 'rolled-back', 'switched', installed['binary'], bundle_version)):
                receipt = task(core, action, revision)
                if core == 'sing-box' and action == 'update':
                    restore_backup = (receipt['backup_name'], receipt['backup_sha256'])
                    restore_selection = previous['cores']
                check.assertEqual(receipt['outcome'], outcome)
                revision = receipt['selection_revision']
                state = snapshot()
                committed = selected(revision)
                check.assertEqual((state['cores'][core]['binary'], state['cores'][core]['version']),
                                  (binary, version))
                for name in nodes:
                    ref = committed['cores'][name]['current']
                    check.assertEqual((ref['binary_sha256'], ref['version']),
                                      (state['cores'][name]['binary'], state['cores'][name]['version']))
                    for field in ('node', 'intent', 'bundle'):
                        check.assertEqual(state['cores'][name][field], initial['cores'][name][field],
                                          'Core switch changed protected fixture data')
                for field in ('binary', 'version'):
                    check.assertEqual(state['cores'][peer][field], initial['cores'][peer][field],
                                      'Core switch changed its peer')
                for field in ('current', 'data', 'users'):
                    check.assertEqual(state[field], initial[field], 'Core switch changed protected fixture data')
                if previous is not None:
                    check.assertGreater(committed['generation'], previous['generation'])
                    check.assertEqual(committed['cores'][peer], previous['cores'][peer])
                previous = committed
                check.assertEqual(traffic_at(stage), export_digests)
                auth_status, auth_raw = api('/api/auth/me')
                check.assertEqual(auth_status, 200, 'Original session was lost')
                check.assertEqual(json.loads(auth_raw), original_identity)
                check.assertEqual([path for _, _, path in traffic.snapshot()], markers)
            print(f'VUI_INSTALLED_CORE_SWITCH_ACCEPTANCE: {core} live systemd private CA, actual '
                  f'{candidate_version} and bundle {bundle_version}, bad digest, failed startup recovery, '
                  'both stock TCP paths and retained data')

        check.assertIsNotNone(restore_backup)
        before_restore()
        prior = snapshot()
        result = task('sing-box', 'restore-backup', revision, backup=restore_backup)
        check.assertEqual(result['outcome'], 'restored_backup')
        after_restore()
        status, raw = api('/api/auth/me')
        check.assertEqual(status, 200)
        check.assertEqual(json.loads(raw), original_identity)
        restored = snapshot()
        check.assertNotEqual(restored['data'], prior['data'])
        check.assertEqual(selected(result['selection_revision'])['cores'], restore_selection)
        for field in ('current', 'users'):
            check.assertEqual(restored[field], prior[field])
        for name in nodes:
            for field in ('node', 'intent', 'binary'):
                check.assertEqual(restored['cores'][name][field], prior['cores'][name][field])
            through(name, 'backup-restored-'+name)
        check.assertEqual([path for _, _, path in traffic.snapshot()], markers)
        print('VUI_INSTALLED_CORE_RESTORE_ACCEPTANCE: real systemd provided TLS, retained node data, revoked access and both stock TCP paths')
