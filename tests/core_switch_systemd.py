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


def installed_core_switch(check, source, work, api, service_python, ca, cert, key, client, node):
    # This helper shares the actual fresh install and its authenticated session.
    assert os.geteuid() != 0 and os.getenv('GITHUB_ACTIONS') == 'true'
    assert os.getenv('RUNNER_ENVIRONMENT') == 'github-hosted'
    assert os.getenv('GITHUB_REPOSITORY') == 'ForceMind/V-UI'
    assert os.getenv('VUI_SYSTEM_INSTALL_TEST') == '1'
    assert os.getenv('VUI_PUBLISHED_INSTALL_VERSION') is None
    candidate = Path(os.environ['VUI_CORE_SWITCH_FIXTURE'])
    assert candidate == Path('/tmp/vui-oneclick/core-switch-fixture/candidate')
    source_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    assert re.fullmatch('[a-f0-9]{40}', source_sha)
    manifest = json.loads((candidate/'CORE-RELEASE.json').read_bytes())
    assert manifest['builder_source'] == source_sha
    assert manifest['compatibility']['app_sources'] == [source_sha] and manifest['core'] == 'sing-box'
    target = next(row for row in manifest['packs'] if row['target'] == 'x86_64-gnu')
    with zipfile.ZipFile(candidate/target['name']) as archive:
        pack = archive.read('CORE-PACK.json')
    assert hashlib.sha256(pack).hexdigest() == target['manifest_sha256']
    new_digest = json.loads(pack)['files']['bin/sing-box']['sha256']

    store = Path('/var/lib/v-ui-core')
    metadata = Path('/etc/v-ui')
    check.assertFalse(os.path.lexists(store))
    for name in ('core-selection.json', 'core-selection.fence.json', 'CORE_UPDATE_PENDING.json',
                 'core-selection.lock', '.core-update-active', '.core-update-history', 'core-catalog'):
        check.assertFalse(os.path.lexists(metadata/name))
    runner_root = Path('/run/vui-core-switch-fixture')
    check.assertFalse(os.path.lexists(runner_root))
    with ExitStack() as stack:
        subprocess.run(['sudo', 'install', '-d', '-o', 'root', '-g', 'root', '-m', '0700',
                        str(runner_root), str(runner_root/'candidate')], check=True)
        stack.callback(subprocess.run, ['sudo', 'rm', '-rf', str(runner_root)], check=True)
        runner = runner_root/'runner.py'
        subprocess.run(['sudo', 'install', '-o', 'root', '-g', 'root', '-m', '0600', str(source/'tests/core_switch_fixture_runner.py'),
                        str(runner)], check=True)
        expected = {'CORE-RELEASE.json', 'SHA256SUMS', *(row['name'] for row in manifest['packs'])}
        check.assertEqual({path.name for path in candidate.iterdir()}, expected)
        for name in sorted(expected):
            check.assertRegex(name, r'^[A-Za-z0-9._-]+$')
            subprocess.run(['sudo', 'install', '-m', '0600', str(candidate/name),
                            str(runner_root/'candidate'/name)], check=True)

        target_port, traffic = https_target(stack, '127.0.0.1', cert, key)
        home = Path(work)/'installed-core-switch-client'
        home.mkdir(mode=0o700)
        roots = home/'empty-roots'
        roots.mkdir(mode=0o700)
        env = {**os.environ, 'SSL_CERT_FILE':str(ca), 'SSL_CERT_DIR':str(roots)}
        markers = []

        def through(label):
            status, raw = api('/api/subscription/upstream/'+str(node['id']),
                {'format':'clash-yaml', 'server':'127.0.0.1', 'acknowledge_tcp_only':True})
            check.assertEqual(status, 200, 'Installed core-switch export failed')
            profile = yaml.safe_load(raw)
            check.assertEqual(profile['type'], 'vless')
            check.assertTrue(profile['tls'])
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
                check.assertEqual(https_through(local_port, '127.0.0.1', target_port, ca, label,
                                                protocol='socks5h'), (200, BODY))
            markers.append('/'+label)
            return hashlib.sha256(raw).hexdigest()

        def snapshot():
            status, raw = api('/api/cores/status')
            check.assertEqual(status, 200)
            core = json.loads(raw)['sing-box']
            check.assertTrue(core['running'])
            panel = int(subprocess.check_output(['systemctl', 'show', 'v-ui.service', '-p', 'MainPID',
                                                 '--value'], text=True).strip())
            witness = service_python('node='+repr(node['id'])+';port='+repr(node['port'])+
                ';watchdog='+repr(core['pid'])+';panel='+repr(panel)+';'+"""
import hashlib,json,os,sqlite3
from pathlib import Path
import psutil
root=Path('/var/lib/v-ui')
data=root/'data'
children=psutil.Process(watchdog).children()
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
with sqlite3.connect((data/'v-ui.db').as_uri()+'?mode=ro',uri=True) as database:
 row=database.execute('SELECT id,instance_id,core,protocol,port,enable,routing_mode,settings,stream_settings,tag FROM inbounds WHERE id=?',(node,)).fetchone()
 assert row is not None
intent=json.loads((data/'runtime/sing-box/state.json').read_bytes())
print(json.dumps({'panel':panel,'binary':digest.hexdigest(),
 'current':hashlib.sha256((root/'CURRENT.json').read_bytes()).hexdigest(),
 'node':hashlib.sha256(json.dumps(row).encode()).hexdigest(),
 'data':[data.stat().st_dev,data.stat().st_ino],
 'intent':[intent['revision'],intent['enabled']]}))
""")
            witness['version'] = core['version_required']
            return witness

        def task(action, revision, *, rejected=False):
            args = [action, source_sha, revision or 'none']
            if action == 'port-conflict':
                args.append(str(node['port']))
            output = subprocess.run(['sudo', '--preserve-env=GITHUB_ACTIONS,RUNNER_ENVIRONMENT,GITHUB_REPOSITORY',
                '/usr/bin/setsid', '--wait', '/usr/bin/python3', '-B', '-I', '-S', str(runner), *args],
                capture_output=True, text=True, timeout=650)
            try:
                result = json.loads(output.stdout)
            except ValueError:
                check.fail('Root core-switch fixture did not return its closed response')
            error = result.get('error')
            code = error if type(error) is str and re.fullmatch(r'[A-Z0-9_]{1,80}', error) else 'NO_CODE'
            check.assertEqual(output.returncode == 0, not rejected, 'Installed core-switch outcome mismatch '+code)
            check.assertIs(result['ok'], not rejected)
            if rejected:
                check.assertEqual(code, 'ASSET_DIGEST')
            return result

        initial = snapshot()
        check.assertNotEqual(initial['binary'], new_digest)
        export_digest = through('core-switch-before')
        task('bad-digest', None, rejected=True)
        check.assertEqual(snapshot(), initial, 'Bad candidate changed the installed instance')
        check.assertFalse((metadata/'CORE_UPDATE_PENDING.json').exists())
        conflict = task('port-conflict', None)
        check.assertEqual(conflict['outcome'], 'restored_previous')
        recovered = snapshot()
        check.assertEqual(recovered['binary'], initial['binary'])
        check.assertEqual(through('core-switch-recovered'), export_digest)
        updated = task('update', conflict['selection_revision'])
        check.assertEqual(updated['outcome'], 'switched')
        actual = snapshot()
        check.assertEqual(actual['binary'], new_digest)
        check.assertEqual(actual['version'], manifest['version'])
        check.assertEqual(through('core-switch-updated'), export_digest)
        reverted = task('rollback', updated['selection_revision'])
        check.assertEqual(reverted['outcome'], 'switched')
        final = snapshot()
        check.assertEqual((final['binary'], final['version']), (initial['binary'], initial['version']))
        check.assertEqual(through('core-switch-rolled-back'), export_digest)
        for state in (recovered, actual, final):
            for field in ('node', 'data', 'current', 'intent'):
                check.assertEqual(state[field], initial[field], 'Core switch changed protected fixture data')
        check.assertEqual(api('/api/auth/me')[0], 200, 'Original session was lost')
        check.assertEqual([path for _, _, path in traffic.snapshot()], markers)
        check.assertFalse((metadata/'CORE_UPDATE_PENDING.json').exists())
        print('VUI_INSTALLED_CORE_SWITCH_ACCEPTANCE: live systemd private CA, actual candidate and bundle, '
              'failed validation, failed startup recovery, stock TCP traffic and retained data')
