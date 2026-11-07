"""Opt-in empty-host public 0.4.14/0.4.15/0.4.16/0.4.17/0.4.19/0.4.20 -> exact candidate upgrade observations.

The old installation uses anonymous published HTTPS. Only the unpublished new
kit's download transport is adapted; neither installer nor root receipt is faked.
Synthetic node/certificate records are persisted through the installed old app's
models, without applying a proxy or contacting a CA. A real HTTPS subscription
is issued and fetched before and after the real systemd upgrade.
"""
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess

from online_install_helpers import fixture_commands, published_commands

PUBLISHED_BASELINES = {
    '0.4.14': 'b0cc174d04d05e61277075412586f0ba0dd231db',
    '0.4.15': '65b1e75d90de733408101ec280a92be935731baa',
    '0.4.16': '18fe631bcf69b68425b454db1795a101e0378842',
    '0.4.17': '95c201c6e26ed54960f964c675e515890f2eea2a',
    '0.4.19': 'a2b9c188658f082b189e77d237787a96eb9cf921',
    '0.4.20': '05173798de1baed5d67a866c4125e72f74fe3f4c',
}
CONTROL = Path('/usr/local/lib/v-ui')
FIREWALL = str(CONTROL / 'firewall_support.py')
MAINTENANCE_FILES = frozenset({
    str(CONTROL / 'maintenance_daemon.py'), str(CONTROL / 'maintenance_upgrade.py'),
    str(CONTROL / 'maintenance_client.py'), str(CONTROL / 'maintenance_cli_worker.py'),
    str(CONTROL / 'install_control_verifier.py'), str(CONTROL / 'v-ui-maintenance.service'),
    '/etc/systemd/system/v-ui-maintenance.service',
})


def assert_download_boundary(test, kit, log):
    """Require exactly this one candidate kit, once, with the accepted bytes."""
    expected = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in kit.iterdir() if path.is_file()}
    records = [json.loads(line) for line in log.read_text().splitlines()]
    test.assertEqual(len(records), len(expected), 'Unexpected repeated/missing candidate downloads')
    test.assertEqual({row['name']: row['sha256'] for row in records}, expected)


def fixed_public_baseline(version, commit):
    if version not in PUBLISHED_BASELINES or PUBLISHED_BASELINES[version] != commit:
        raise AssertionError('Fixed public 0.4.14, 0.4.15, 0.4.16, 0.4.17, 0.4.19 or 0.4.20 upgrade baseline required')
    return version, commit


def assert_receipt_migration(test, before, after, old_version):
    if old_version not in PUBLISHED_BASELINES:
        raise AssertionError('Unknown public upgrade receipt baseline')
    if old_version in ('0.4.17', '0.4.19', '0.4.20'):
        test.assertTrue(MAINTENANCE_FILES <= set(before['files']))
        additions = set()
    else:
        test.assertTrue(MAINTENANCE_FILES.isdisjoint(before['files']))
        additions = MAINTENANCE_FILES | ({FIREWALL} if old_version == '0.4.14' else set())
    test.assertEqual(set(after['files']) - set(before['files']), additions)
    if old_version == '0.4.14':
        test.assertNotIn(FIREWALL, before['files'])
    else:
        test.assertIn(FIREWALL, before['files'])
    test.assertFalse(set(before['files']) - set(after['files']))
    for field in ('schema', 'service_manager', 'system_python', 'interpreter_resolved', 'interpreter_sha256'):
        test.assertEqual(after[field], before[field])


def assert_editor_content_addition(test, before, after):
    """Only the new derived CAS may be added; every prior value stays exact.

    The private DB and material snapshots are independently byte-exact below.
    No credential, identity or profile field is excused by this response check.
    """
    test.assertNotIn('content_revision', before)
    test.assertEqual(set(after), set(before) | {'content_revision'})
    token = after['content_revision']
    test.assertIs(type(token), str)
    test.assertIsNotNone(re.fullmatch('[a-f0-9]{64}', token))
    test.assertEqual({key:value for key,value in after.items() if key!='content_revision'}, before)


def assert_node_summary_upgrade(test, before, after, old_version=None):
    """Permit only the declared capability/CAS response additions for 21."""
    added = set() if old_version in ('0.4.19','0.4.20') else {'share_eligible','upstream_export_eligible'}
    test.assertEqual(len(after), len(before))
    for old, new in zip(before, after):
        test.assertTrue(added.isdisjoint(old))
        for key in added:
            test.assertIs(type(new.get(key)), bool)
        # Keep the original complete-field comparison after accounting for the
        # two versioned booleans; no unknown response key can slip through.
        comparable={key:value for key,value in new.items() if key not in added}
        assert_editor_content_addition(test, old, comparable)


def assert_upgrade_configuration(test, before, after, old_release, new_release):
    """Match the complete saved configuration and its exact runtime transition."""
    test.assertEqual(before['release_id'], old_release.name)
    test.assertEqual(before['bootstrap_python'], str(old_release / 'runtime/python/bin/python3'))
    test.assertNotEqual(old_release, new_release)
    expected = {**before, 'release_id':new_release.name,
                'bootstrap_python':str(new_release / 'runtime/python/bin/python3')}
    # Fresh provided-certificate baselines may omit this field. The upgrade writes
    # the canonical empty authorization list; existing authorizations must stay.
    expected.setdefault('ca_authorizations', [])
    test.assertEqual(after, expected)


def managed_xray_fixture_program(data_root='/var/lib/v-ui/data'):
    """Persist synthetic v0.4.16 bound state; never apply a core or issue a cert."""
    return 'data_root=' + repr(str(data_root)) + ';' + '''
import hashlib,json,time
from pathlib import Path
from app.models import database
from app.certificates.models import Certificate,CertificateBinding
root=Path(data_root)
chain=(root/'certs/provided-fullchain.pem').read_bytes()
key=(root/'certs/provided-privkey.pem').read_bytes()
identity='b'*32
revision=hashlib.sha256(chain).hexdigest()
material=root/'certificates/revisions'/identity/revision
material.mkdir(parents=True,mode=0o700)
for name,content in (('fullchain.pem',chain),('privkey.pem',key)):
 path=material/name
 path.write_bytes(content)
 path.chmod(0o600)
with database.SessionLocal() as db:
 db.add(Certificate(id=identity,domain='vpn.example.test',email='fixture@example.test',
  environment='production',auto_renew=False,revision=revision,created_at=int(time.time())))
 node=database.Inbound(core='xray',protocol='vless',remark='published-upgrade-synthetic-managed-xray-draft',
  port=19372,enable=False,settings={'clients':[{'id':'22222222-2222-4222-8222-222222222222'}]},
  stream_settings={'network':'raw','security':'tls','tlsSettings':{'serverName':'vpn.example.test',
   'certificates':[{'certificateFile':str(material/'fullchain.pem'),'keyFile':str(material/'privkey.pem')}]},
   '_vui':{'security':'tls','server_name':'vpn.example.test'}})
 db.add(node)
 db.flush()
 node_id=node.id
 db.add(CertificateBinding(target='inbound:'+str(node.id),certificate_id=identity,
  error='CORE_STOPPED_PENDING_APPLY'))
 db.commit()
print(json.dumps({'inbound_id':node_id,'certificate_id':identity}))
'''


class PublishedUpgrade:
    def __init__(self, test, source, work, certificate_args, configured_args):
        self.test = test
        self.old_version, self.old_commit = fixed_public_baseline(
            os.environ.get('VUI_PUBLISHED_INSTALL_VERSION'), os.environ.get('VUI_RELEASE_COMMIT'))
        if (os.environ.get('VUI_SETUP_INSTALL')
                or os.environ.get('VUI_EXISTING_INSTALL_TEST')):
            raise AssertionError('Fixed public upgrade requires standalone empty-host mode')
        self.source = source
        self.kit = Path(os.environ['VUI_RELEASE_KIT'])
        self.candidate = json.loads((self.kit / 'RELEASE.json').read_bytes())
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
        test.assertEqual(self.candidate['source_commit'], commit)
        test.assertEqual(self.candidate['version'], (source / 'VERSION').read_text().strip())
        test.assertNotEqual(self.candidate['version'], self.old_version)
        self.old = published_commands(Path(os.environ['VUI_PUBLISHED_UPGRADE_SOURCE']), self.old_version,
                                      self.old_commit, certificate_args, configured_args)
        self.new = fixture_commands(source, work, self.kit, certificate_args, configured_args)

    def _service_python(self, release, program):
        # Explicit isolated interpreter, installed old/new payload, service UID.
        result = subprocess.run(['sudo', '-u', 'v-ui', '/usr/bin/env',
            'VUI_DATA_DIR=/var/lib/v-ui/data', str(release / 'runtime/python/bin/python3'),
            '-B', '-I', '-c', 'import sys;sys.path.insert(0,' + repr(str(release / 'payload')) + ');' + program],
            capture_output=True, text=True, timeout=30)
        if result.returncode:
            # Do not expose a model/SQL exception containing credential values.
            raise AssertionError('Installed service-user data fixture failed (exit '+str(result.returncode)+')')
        return json.loads(result.stdout)

    def _receipt(self):
        receipt = json.loads((CONTROL / 'ops-control.json').read_bytes())
        self.test.assertEqual(receipt['schema'], 1)
        self.test.assertEqual(receipt['service_manager'], 'systemd')
        for name, expected in receipt['files'].items():
            path = Path(name)
            self.test.assertTrue(path.is_absolute())
            self.test.assertEqual(path.stat().st_uid, 0)
            self.test.assertEqual(path.stat().st_mode & 0o022, 0)
            self.test.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected,
                                  'Installed root receipt must match every live file: '+name)
        return receipt

    def _snapshot(self, release):
        # Only counts and hashes leave the service process. No DB credentials,
        # password hashes, token hashes or certificate private bytes are printed.
        return self._service_python(release, '''
import hashlib,json,sqlite3
from pathlib import Path
result={}
with sqlite3.connect('file:/var/lib/v-ui/data/v-ui.db?mode=ro',uri=True) as db:
 for table in ('users','inbounds','subscription_grants','managed_certificates','certificate_bindings'):
  rows=db.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall()
  result[table]={'count':len(rows),'sha256':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()}
for field,directory in (('certificate_material','certs'),('managed_certificate_material','certificates/revisions')):
 root=Path('/var/lib/v-ui/data')/directory
 result[field]={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
  for p in sorted(root.rglob('*')) if p.is_file()}
print(json.dumps(result,sort_keys=True))
''')

    def _manifest(self, release):
        return self._service_python(release, 'import json;from pathlib import Path;print((Path('+repr(str(release / 'payload' / 'MANIFEST.json'))+')).read_text())')

    def before(self, release, api, private_values):
        self.old_receipt = self._receipt()
        if self.old_version == '0.4.14':
            self.test.assertNotIn(FIREWALL, self.old_receipt['files'])
            self.test.assertFalse(os.path.lexists(FIREWALL), 'Do not pre-create new root closure helper')
        else:
            self.test.assertIn(FIREWALL, self.old_receipt['files'])
            self.test.assertTrue(Path(FIREWALL).is_file())
        self.config = json.loads(Path('/etc/v-ui/service.json').read_bytes())
        self.old_release = release
        self.test.assertEqual(self.config['bootstrap_python'], str(release / 'runtime/python/bin/python3'))
        self.test.assertEqual(self._manifest(release)['source_commit'], self.old_commit)
        self._service_python(release, '''
import json,time
from app.models import database
from app.certificates.models import Certificate
with database.SessionLocal() as db:
 db.add(database.Inbound(core='sing-box',protocol='vless',remark='published-upgrade-synthetic-draft',
  port=19371,enable=True,settings={'users':[{'uuid':'11111111-1111-4111-8111-111111111111'}]},
  stream_settings={'tls':{'enabled':True,'server_name':'vpn.example.test'},
                   '_vui':{'security':'tls','server_name':'vpn.example.test'}}))
 db.add(Certificate(id='a'*32,domain='upgrade.example.test',email='fixture@example.test',
  environment='staging',auto_renew=False,created_at=int(time.time())))
 db.commit()
print('{}')
''')
        if self.old_version == '0.4.15':
            # An existing manual TLS consumer stays manual across the new
            # certificate-binding release; this draft is never applied.
            self._service_python(release, '''
import json
from app.models import database
with database.SessionLocal() as db:
 node=db.query(database.Inbound).one()
 stream=dict(node.stream_settings)
 stream['tls']={**stream['tls'],
  'certificate_path':'/var/lib/v-ui/data/certs/provided-fullchain.pem',
  'key_path':'/var/lib/v-ui/data/certs/provided-privkey.pem'}
 node.stream_settings=stream
 db.commit()
print('{}')
''')
        self.managed_xray = None
        if self.old_version in ('0.4.16', '0.4.17', '0.4.19', '0.4.20'):
            # The stored binding and its local fixture material are observed as
            # data only. No CA, certificate validation or business core is run.
            self.managed_xray = self._service_python(release, managed_xray_fixture_program())
        status, raw = api('/api/inbounds')
        self.test.assertEqual(status, 200)
        self.nodes = json.loads(raw)
        self.test.assertEqual(len(self.nodes), 2 if self.managed_xray else 1)
        self.manual_editor = None
        if self.old_version == '0.4.15':
            status, raw = api('/api/inbounds/'+str(self.nodes[0]['id'])+'/editor')
            self.test.assertEqual(status, 200)
            self.manual_editor = json.loads(raw)
            self.test.assertIsNone(self.manual_editor['certificate_id'])
            self.test.assertEqual(self.manual_editor['profile']['certificate_path'],
                                  '/var/lib/v-ui/data/certs/provided-fullchain.pem')
            self.test.assertEqual(self.manual_editor['profile']['key_path'],
                                  '/var/lib/v-ui/data/certs/provided-privkey.pem')

        self.managed_editor = None
        if self.managed_xray:
            status, raw = api('/api/inbounds/'+str(self.managed_xray['inbound_id'])+'/editor')
            self.test.assertEqual(status, 200)
            self.managed_editor = json.loads(raw)
            self.test.assertEqual(self.managed_editor['core'], 'xray')
            self.test.assertEqual(self.managed_editor['certificate_id'], self.managed_xray['certificate_id'])
            self.test.assertFalse(self.managed_editor['enable'])

        status, raw = api('/api/subscriptions', {'label':'published-upgrade-fixture',
            'server':'vpn.example.test', 'inbound_ids':[self.nodes[0]['id']], 'formats':['raw']})
        self.test.assertEqual(status, 201, 'Real old HTTPS subscription creation required')
        grant = json.loads(raw)
        private_values.append(grant['token'])
        self.subscription = grant['paths']['raw']
        self.test.assertEqual(api(self.subscription)[0], 200)
        self.subscription_bytes = api(self.subscription)[1]
        self.grants = json.loads(api('/api/subscriptions')[1])
        self.data = self._snapshot(release)
        for table in ('users','inbounds','subscription_grants','managed_certificates'):
            self.test.assertGreater(self.data[table]['count'], 0)
        self.test.assertGreaterEqual(len(self.data['certificate_material']), 2)
        if self.managed_xray:
            self.test.assertGreater(self.data['certificate_bindings']['count'], 0)
            self.test.assertEqual(len(self.data['managed_certificate_material']), 2)
        self.test.assertEqual(self.new['download_log'].read_text(), '', 'Candidate must not download before old-data snapshot')

    def after(self, api):
        config = json.loads(Path('/etc/v-ui/service.json').read_bytes())
        release = Path('/var/lib/v-ui/releases') / self.candidate['release_id']
        assert_upgrade_configuration(self.test,self.config,config,self.old_release,release)
        manifest = self._manifest(release)
        self.test.assertEqual(manifest['source_commit'], self.candidate['source_commit'])
        self.test.assertEqual(manifest['version'], self.candidate['version'])
        assert_receipt_migration(self.test, self.old_receipt, self._receipt(), self.old_version)
        self.test.assertEqual(self._snapshot(release), self.data, 'Private DB rows and certificate bytes must survive')
        assert_node_summary_upgrade(self.test, self.nodes, json.loads(api('/api/inbounds')[1]), self.old_version)
        if self.manual_editor is not None:
            status, raw = api('/api/inbounds/'+str(self.nodes[0]['id'])+'/editor')
            self.test.assertEqual(status, 200)
            assert_editor_content_addition(self.test,self.manual_editor,json.loads(raw))

        if self.managed_editor is not None:
            status, raw = api('/api/inbounds/'+str(self.managed_xray['inbound_id'])+'/editor')
            self.test.assertEqual(status, 200)
            assert_editor_content_addition(self.test,self.managed_editor,json.loads(raw))

        self.test.assertEqual(json.loads(api('/api/subscriptions')[1]), self.grants)
        self.public_subscription(api)
        assert_download_boundary(self.test, self.kit, self.new['download_log'])
        print('Published upgrade: immutable public '+self.old_version+' source/assets -> exact candidate; exact historical root receipt closure; config, users/password hashes, node draft/manual TLS paths, live subscription, provided/managed fixture certificate bytes and managed certificate/binding metadata retained (synthetic un-applied Xray binding on 0.4.16/17/19/20; no CA/core qualification); candidate-only bounded downloads OK')

    def public_subscription(self, api):
        status, raw = api(self.subscription)
        self.test.assertEqual(status, 200)
        self.test.assertTrue(raw == self.subscription_bytes, 'Existing public subscription bytes changed')
