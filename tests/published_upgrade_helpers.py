"""Opt-in empty-host upgrades from fixed public releases to the exact candidate.

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
import zipfile

from online_install_helpers import MenuSession, fixture_commands, published_commands

PUBLISHED_BASELINES = {
    '0.4.14': 'b0cc174d04d05e61277075412586f0ba0dd231db',
    '0.4.15': '65b1e75d90de733408101ec280a92be935731baa',
    '0.4.16': '18fe631bcf69b68425b454db1795a101e0378842',
    '0.4.17': '95c201c6e26ed54960f964c675e515890f2eea2a',
    '0.4.19': 'a2b9c188658f082b189e77d237787a96eb9cf921',
    '0.4.20': '05173798de1baed5d67a866c4125e72f74fe3f4c',
    '0.4.21': '2aa5e11e3f09c03d048018f033ae888e7c775604',
    '0.4.22': '558687a76cd29f4e4735284926eb05bf6664472e',
    '0.4.23': 'be5d8459cdd3eeedce73f2ff6e4663e5eb781e42',
    '0.4.24': '8b8d7cf82e55609a205455b351f659eb9de91974',
    '0.4.25': '32a03a5b6719400f21a07a75d04d278b76e0df09',
}
DNS_BASELINES = frozenset({'0.4.23', '0.4.24', '0.4.25'})
CORE_AUTHORITY_BASELINES = frozenset({'0.4.24', '0.4.25'})
CONTROL = Path('/usr/local/lib/v-ui')
FIREWALL = str(CONTROL / 'firewall_support.py')
MAINTENANCE_FILES = frozenset({
    str(CONTROL / 'maintenance_daemon.py'), str(CONTROL / 'maintenance_upgrade.py'),
    str(CONTROL / 'maintenance_client.py'), str(CONTROL / 'maintenance_cli_worker.py'),
    str(CONTROL / 'install_control_verifier.py'), str(CONTROL / 'v-ui-maintenance.service'),
    '/etc/systemd/system/v-ui-maintenance.service',
})
DNS_FILES = frozenset(str(CONTROL / name) for name in (
    'certificate_errors.py', 'dns_store.py', 'cloudflare.py', 'dns_journal.py', 'dns_backup.py'))
# The P3 development source uses the 23 application schema while its
# independent core feature is being qualified. Its test profile requires this
# exact additional group; the original frozen 23 profile remains DNS-only.
CORE_FILES = frozenset(str(CONTROL / name) for name in (
    'core_artifacts.py','core_selection.py','core_binary.py','core_store.py',
    'core_github.py','core_catalog.py','core_resolver.py','core_application.py',
    'core_selection_files.py','core_boot.py','core_backup.py','core_backup_archive.py',
    'core_maintenance.py','core_maintenance_recovery.py','core_boot_broker.py','core_maintenance_control.py'))
AUTHORITY_FILES = frozenset({str(CONTROL / 'core_authority.py')})
CERTIFICATE_COLUMNS = ('id', 'domain', 'email', 'environment', 'auto_renew', 'revision',
    'not_before', 'not_after', 'renew_at', 'retry_at', 'last_attempt', 'failures', 'error', 'created_at')
BINDING_COLUMNS = ('target', 'certificate_id', 'applied_certificate_id', 'applied_revision', 'error')
DNS_CERTIFICATE_COLUMNS = CERTIFICATE_COLUMNS[:4] + ('method', 'wildcard', 'credential_id') + CERTIFICATE_COLUMNS[4:]
DNS_BINDING_LAYOUTS = (BINDING_COLUMNS + ('server_name',), BINDING_COLUMNS[:4] + ('server_name', 'error'))


def _target_contract(candidate_version, old_version):
    if candidate_version not in ('0.4.22', '0.4.23', '0.4.24', '0.4.25', '0.4.26'):
        raise AssertionError('Explicit reviewed 0.4.22 through 0.4.26 upgrade target required')
    if old_version in DNS_BASELINES and candidate_version <= old_version:
        raise AssertionError('The published DNS baseline requires a newer candidate')
    return candidate_version


def assert_download_boundary(test, kit, log):
    """Require exactly this one candidate kit, once, with the accepted bytes."""
    expected = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in kit.iterdir() if path.is_file()}
    records = [json.loads(line) for line in log.read_text().splitlines()]
    test.assertEqual(len(records), len(expected), 'Unexpected repeated/missing candidate downloads')
    test.assertEqual({row['name']: row['sha256'] for row in records}, expected)


def candidate_origin(kit, release, target):
    """Expected Root origin from the candidate kit's accepted target archive."""
    if target not in ('x86_64-gnu', 'x86_64-musl', 'aarch64-gnu', 'aarch64-musl'):
        raise AssertionError('Unknown candidate target')
    bundle_name = 'vui-linux-' + target + '.zip'
    matches = [line.split('  ', 1)[0] for line in (kit / 'SHA256SUMS').read_text().splitlines()
               if line.endswith('  ' + bundle_name)]
    if len(matches) != 1 or not re.fullmatch('[a-f0-9]{64}', matches[0]):
        raise AssertionError('Candidate target requires one accepted archive digest')
    with zipfile.ZipFile(kit / bundle_name) as archive:
        raw = archive.read('MANIFEST.json')
    manifest = json.loads(raw)
    if any(manifest[name] != release[name] for name in ('release_id', 'source_commit', 'version')):
        raise AssertionError('Candidate archive manifest differs from release identity')
    return dict(release_id=release['release_id'], target=target, app_source=release['source_commit'],
                manifest_sha256=hashlib.sha256(raw).hexdigest(), archive_sha256=matches[0])


def fixed_public_baseline(version, commit):
    if version not in PUBLISHED_BASELINES or PUBLISHED_BASELINES[version] != commit:
        raise AssertionError('Fixed public upgrade baseline and its exact source required')
    return version, commit


YAML_FILES = frozenset({str(CONTROL / "mihomo_plan_record.py")})


def assert_receipt_migration(test, before, after, old_version, candidate_version='0.4.22', *, core_control=False, core_authority=False, yaml_control=False):
    if old_version not in PUBLISHED_BASELINES:
        raise AssertionError('Unknown public upgrade receipt baseline')
    _target_contract(candidate_version, old_version)
    if type(yaml_control) is not bool or yaml_control and not core_control:
        raise AssertionError("YAML closure qualification requires the explicit core profile")
    existing_yaml = old_version == '0.4.25'
    if existing_yaml:
        test.assertTrue(YAML_FILES <= set(before['files']), 'Published YAML receipt lacks its control reader')
    else:
        test.assertTrue(YAML_FILES.isdisjoint(before['files']))
    if type(core_control) is not bool or core_control and candidate_version == '0.4.22':
        raise AssertionError('Explicit P3 development control profile required')
    if type(core_authority) is not bool or core_authority and not core_control:
        raise AssertionError('Explicit private core authority control profile required')
    if candidate_version in ('0.4.24', '0.4.25', '0.4.26') and not (core_control and core_authority):
        raise AssertionError('Candidate requires the complete core and authority control profile')
    if candidate_version in ('0.4.25', '0.4.26') and not yaml_control:
        raise AssertionError('Candidate requires the YAML control reader')
    existing_core = old_version in CORE_AUTHORITY_BASELINES
    if existing_core:
        test.assertTrue(CORE_FILES | AUTHORITY_FILES <= set(before['files']))
    else:
        test.assertTrue(AUTHORITY_FILES.isdisjoint(before['files']), 'Historical receipt must not pre-contain authority helpers')
        test.assertTrue(CORE_FILES.isdisjoint(before['files']), 'Historical receipt must not pre-contain core helpers')
    if old_version in DNS_BASELINES:
        test.assertTrue(DNS_FILES <= set(before['files']), 'Published DNS receipt must contain every DNS helper')
    else:
        test.assertTrue(DNS_FILES.isdisjoint(before['files']), 'Historical receipt must not pre-contain DNS helpers')
    if old_version not in ('0.4.14', '0.4.15', '0.4.16'):
        test.assertTrue(MAINTENANCE_FILES <= set(before['files']))
        additions = set()
    else:
        test.assertTrue(MAINTENANCE_FILES.isdisjoint(before['files']))
        additions = MAINTENANCE_FILES | ({FIREWALL} if old_version == '0.4.14' else set())
    if candidate_version != '0.4.22' and old_version not in DNS_BASELINES:
        additions = additions | DNS_FILES
    if core_control and not existing_core:
        additions = additions | CORE_FILES
    if core_authority and not existing_core:
        additions = additions | AUTHORITY_FILES
    if yaml_control and not existing_yaml:
        additions = additions | YAML_FILES
    test.assertEqual(set(after['files']) - set(before['files']), additions)
    if old_version == '0.4.14':
        test.assertNotIn(FIREWALL, before['files'])
    else:
        test.assertIn(FIREWALL, before['files'])
    test.assertFalse(set(before['files']) - set(after['files']))
    for field in ('schema', 'service_manager', 'system_python', 'interpreter_resolved', 'interpreter_sha256'):
        test.assertEqual(after[field], before[field])


def snapshot_program(old_version, candidate_version, *, after=False, data_root='/var/lib/v-ui/data'):
    """Hash every old value; validate the exact declared DNS migration separately.

    Only hashes/counts/column names leave the read-only service process. A 23
    after snapshot projects all old certificate columns by name after proving
    the new columns and every migrated default. The published 23 baseline
    already has DNS identity fields and compares every field in both phases.
    """
    if old_version not in PUBLISHED_BASELINES or type(after) is not bool:
        raise AssertionError('Known historical snapshot and explicit phase required')
    _target_contract(candidate_version, old_version)
    existing_dns = old_version in DNS_BASELINES
    modern = existing_dns or (after and candidate_version != '0.4.22')
    return ('data_root=' + repr(str(data_root)) + '\nmodern=' + repr(modern)
        + '\nexisting_dns=' + repr(existing_dns)
        + '\ncertificate_columns=' + repr(CERTIFICATE_COLUMNS)
        + '\nbinding_columns=' + repr(BINDING_COLUMNS)
        + '\ndns_certificate_columns=' + repr(DNS_CERTIFICATE_COLUMNS)
        + '\ndns_binding_layouts=' + repr(DNS_BINDING_LAYOUTS) + '\n' + '''
import hashlib,json,os,sqlite3,stat
from pathlib import Path
result={}
def need(condition):
 if not condition: raise AssertionError('Unexpected published-upgrade certificate schema or migrated defaults')
with sqlite3.connect((Path(data_root)/'v-ui.db').as_uri()+'?mode=ro',uri=True) as db:
 for table in ('users','inbounds','subscription_grants','managed_certificates','certificate_bindings'):
  info=list(db.execute('PRAGMA table_xinfo('+table+')'))
  need(info and all(row[6]==0 for row in info))
  columns=tuple(row[1] for row in info)
  selected=columns
  if table=='managed_certificates':
   need(columns==(dns_certificate_columns if modern else certificate_columns))
   selected=dns_certificate_columns if existing_dns else certificate_columns
   if modern:
    fields={row[1]:row for row in info}
    need(fields['method'][2].upper()=='VARCHAR(16)' and fields['method'][3]==1
         and fields['method'][4] in ("'http-01'","('http-01')")
         and fields['wildcard'][2].upper()=='BOOLEAN' and fields['wildcard'][3]==1
         and fields['wildcard'][4] in ('0','(0)')
         and fields['credential_id'][2].upper()=='VARCHAR(32)'
         and fields['credential_id'][3]==0 and fields['credential_id'][4] is None)
    if not existing_dns:
     for method,wildcard,credential in db.execute('SELECT method,wildcard,credential_id FROM managed_certificates'):
      need(method=='http-01' and type(wildcard) is int and wildcard==0 and credential is None)
  elif table=='certificate_bindings':
   need(columns in dns_binding_layouts if modern else columns==binding_columns)
   selected=binding_columns+('server_name',) if existing_dns else binding_columns
   if modern:
    field=next(row for row in info if row[1]=='server_name')
    need(field[2].upper()=='VARCHAR(253)' and field[3]==0 and field[4] is None)
    if not existing_dns:
     need(all(row[0] is None for row in db.execute('SELECT server_name FROM certificate_bindings')))
  quoted=','.join('"'+column.replace('"','""')+'"' for column in selected)
  rows=db.execute('SELECT '+quoted+' FROM '+table+' ORDER BY 1').fetchall()
  result[table]={'columns':list(selected),'count':len(rows),'sha256':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()}
 marker=list(db.execute("SELECT type FROM sqlite_master WHERE name='certificate_identity_schema'"))
 if modern:
  need(marker==[('table',)])
  info=list(db.execute('PRAGMA table_xinfo(certificate_identity_schema)'))
  need(len(info)==1 and info[0][1]=='version' and info[0][2].upper()=='INTEGER'
       and info[0][3]==1 and info[0][4] is None and info[0][5]==0 and info[0][6]==0)
  need(list(db.execute('SELECT typeof(version),version FROM certificate_identity_schema'))==[('integer',1)])
 else: need(not marker)
for field,directory in (('certificate_material','certs'),('managed_certificate_material','certificates/revisions')):
 root=Path(data_root)/directory
 result[field]={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
  for p in sorted(root.rglob('*')) if p.is_file()}
if existing_dns:
 root=Path(data_root)/'certificates/dns-credentials'
 need(not root.is_symlink() and root.is_dir())
 info=root.lstat()
 need(info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o700)
 result['dns_credentials']={}
 for path in sorted(root.rglob('*')):
  info=path.lstat()
  need(info.st_uid==os.geteuid())
  if stat.S_ISDIR(info.st_mode):
   need(stat.S_IMODE(info.st_mode)==0o700)
  else:
   need(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode)==0o600 and info.st_nlink==1)
   result['dns_credentials'][str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
print(json.dumps(result,sort_keys=True))
''')


def assert_editor_content_addition(test, before, after):
    """Only the new derived CAS may be added; every prior value stays exact.

    The private DB and material snapshots are independently byte-exact below.
    No credential, identity or profile field is excused by this response check.
    """
    if 'content_revision' in before:
        # Published21–23 already carry the CAS; do not excuse any old field.
        test.assertEqual(after,before)
        test.assertIs(type(after['content_revision']),str)
        test.assertIsNotNone(re.fullmatch('[a-f0-9]{64}',after['content_revision']))
        return
    test.assertNotIn('content_revision', before)
    test.assertEqual(set(after), set(before) | {'content_revision'})
    token = after['content_revision']
    test.assertIs(type(token), str)
    test.assertIsNotNone(re.fullmatch('[a-f0-9]{64}', token))
    test.assertEqual({key:value for key,value in after.items() if key!='content_revision'}, before)


def assert_node_summary_upgrade(test, before, after, old_version=None):
    """Permit only the declared capability/CAS response additions for 21."""
    if old_version in ('0.4.21', '0.4.22', '0.4.23', '0.4.24', '0.4.25'):
        test.assertEqual(after,before)
        for row in after:
            test.assertIs(type(row.get('content_revision')),str)
            test.assertIsNotNone(re.fullmatch('[a-f0-9]{64}',row['content_revision']))
        return
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


def yaml_draft_program(*, create=False, data_root='/var/lib/v-ui/data'):
    """Run through the installed store and return only identities and source hashes."""
    return ('create=' + repr(create) + '\ndata_root=' + repr(str(data_root)) + '\n' + r"""
import hashlib,json
from app.services.mihomo_merge import serialize_mihomo_merge
from app.services.mihomo_merge_plan import plan_mihomo
from app.services.mihomo_plan_record import encode_plan_record
from app.services.proxy_store import ProxyStore

def digest(value):
 return hashlib.sha256(value).hexdigest()

store=ProxyStore(data_root)
if create:
 baseline='mode: rule\n'
 candidate='rules:\n  - DOMAIN,fixture.example.test,REJECT\n  - MATCH,REJECT\n'
 sources={'baseline':baseline.encode(),'candidate':candidate.encode(),
          'merged':serialize_mihomo_merge(baseline,candidate)}
 record=encode_plan_record(plan_mihomo(baseline,candidate),
  sources={role:digest(raw) for role,raw in sources.items()},group_members={},dns_upstream=None)
 store.save_yaml_plan(record,sources,store.read_snapshot()['revision'])
draft=store.load_draft()
assert draft['execution_mode']=='yaml'
assert draft['pending'] is None and store.load_active() is None
record,sources=draft['yaml_plan']
print(json.dumps({'revision':draft['revision'],'plan':record.record_id,
                  'sources':{role:digest(raw) for role,raw in sources.items()}}))
""")


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
        _target_contract(self.candidate['version'], self.old_version)
        self.old_source = Path(os.environ['VUI_PUBLISHED_UPGRADE_SOURCE'])
        self.old = published_commands(self.old_source, self.old_version, self.old_commit,
                                      certificate_args, configured_args)
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

    def _snapshot(self, release, *, after=False):
        # Only counts and hashes leave the service process. No DB credentials,
        # password hashes, token hashes or certificate private bytes are printed.
        return self._service_python(release, snapshot_program(self.old_version,self.candidate['version'],after=after))

    def _manifest(self, release):
        return self._service_python(release, 'import json;from pathlib import Path;print((Path('+repr(str(release / 'payload' / 'MANIFEST.json'))+')).read_text())')

    def before(self, release, api, private_values):
        self.old_receipt = self._receipt()
        if self.old_version == '0.4.23':
            from scripts.system_maintenance_smoke import installed_core_read_capability
            self.test.assertFalse(installed_core_read_capability(self.old_source))
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
        if self.old_version in CORE_AUTHORITY_BASELINES:
            self.test.assertEqual(self._manifest(release)['version'], self.old_version)
            self.old_origin = self._root_origin(release.name, self.config['target'])
            self.test.assertEqual(self.old_origin['app_source'], self.old_commit)
            self.test.assertEqual(self.old_origin['manifest_sha256'], self._manifest_digest(release))
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
        if self.old_version in DNS_BASELINES:
            self._service_python(release, '''
from pathlib import Path
import time
from app.certificates.dns_store import DNSCredentialStore
from app.certificates.models import Certificate
from app.models import database
store=DNSCredentialStore(Path('/var/lib/v-ui/data/certificates'))
profile=store.create(label='offline-upgrade-fixture',zone_name='example.test',
 zone_id='f'*32,api_token='synthetic-offline-upgrade-token')
with database.SessionLocal() as db:
 db.add(Certificate(id='b'*32,domain='dns-upgrade.example.test',email='fixture@example.test',
  environment='staging',method='dns-01',wildcard=True,credential_id=profile['id'],
  auto_renew=False,created_at=int(time.time())))
 db.commit()
print('{}')
''')
        self.managed_xray = None
        if self.old_version in ('0.4.16', '0.4.17', '0.4.19', '0.4.20', '0.4.21'):
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
        self.yaml_state = None
        if self.old_version == '0.4.25':
            self.yaml_state = self._service_python(release, yaml_draft_program(create=True))
        for table in ('users','inbounds','subscription_grants','managed_certificates'):
            self.test.assertGreater(self.data[table]['count'], 0)
        self.test.assertGreaterEqual(len(self.data['certificate_material']), 2)
        if self.old_version in DNS_BASELINES:
            self.test.assertGreaterEqual(self.data['managed_certificates']['count'], 2)
            self.test.assertTrue(any(name.endswith('/state.json') for name in self.data['dns_credentials']))
            self.test.assertTrue(any(re.fullmatch(r'[a-f0-9]{32}/[a-f0-9]{32}\.json', name)
                                     for name in self.data['dns_credentials']))
        if self.managed_xray:
            self.test.assertGreater(self.data['certificate_bindings']['count'], 0)
            self.test.assertEqual(len(self.data['managed_certificate_material']), 2)
        self.test.assertEqual(self.new['download_log'].read_text(), '', 'Candidate must not download before old-data snapshot')

    def _manifest_digest(self, release):
        manifest_path = release / 'payload/MANIFEST.json'
        return self._service_python(release, 'import hashlib,json;from pathlib import Path;'
            'print(json.dumps(hashlib.sha256(Path(' + repr(str(manifest_path)) + ').read_bytes()).hexdigest()))')

    def _root_origin(self, release_id, target):
        program = """
import json,os,re,stat,sys
from pathlib import Path
assert os.geteuid()==0 and os.getenv('GITHUB_ACTIONS')=='true'
assert os.getenv('RUNNER_ENVIRONMENT')=='github-hosted' and os.getenv('GITHUB_REPOSITORY')=='ForceMind/V-UI'
assert os.getenv('VUI_SYSTEM_INSTALL_TEST')=='1'
release_id,target=sys.argv[1:]
root=Path('/var/lib/v-ui-maintenance/core-authority')
for path in (*reversed(root.parents),root):
 info=path.lstat()
 assert stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022
assert stat.S_IMODE(root.stat().st_mode)==0o700
fd=os.open(root/'authority.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
with os.fdopen(fd,'rb') as stream:
 info=os.fstat(fd)
 assert stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1
 assert stat.S_IMODE(info.st_mode)==0o600 and 0<info.st_size<=2000000
 raw=stream.read(2000001)
 assert len(raw)==info.st_size
state=json.loads(raw)
assert state['schema']==2 and state['kind']=='core-authority-v2'
row,=[item for item in state['applications'].values()
      if (item['release_id'],item['target'])==(release_id,target)]
assert set(row)=={'release_id','target','app_source','manifest_sha256','archive_sha256'}
assert re.fullmatch('[a-f0-9]{40}',row['app_source'])
assert all(re.fullmatch('[a-f0-9]{64}',row[key]) for key in ('manifest_sha256','archive_sha256'))
print(json.dumps(row))
"""
        result = subprocess.run(['sudo',
            '--preserve-env=GITHUB_ACTIONS,RUNNER_ENVIRONMENT,GITHUB_REPOSITORY,VUI_SYSTEM_INSTALL_TEST',
            '/usr/bin/python3', '-B', '-I', '-S', '-c', program, release_id, target],
            capture_output=True, text=True, timeout=15)
        self.test.assertEqual(result.returncode, 0, 'Installed Root origin verification failed')
        return json.loads(result.stdout)

    def _assert_candidate_origin(self, release, config):
        from scripts.system_maintenance_smoke import installed_core_read_capability
        self.test.assertTrue(installed_core_read_capability(self.source))
        expected = candidate_origin(self.kit, self.candidate, config['target'])
        self.test.assertEqual(self._manifest_digest(release), expected['manifest_sha256'])
        self.test.assertEqual(self._root_origin(release.name, config['target']), expected)
        if self.old_version in CORE_AUTHORITY_BASELINES:
            self.test.assertEqual(self._root_origin(self.old_release.name, config['target']), self.old_origin)
        with MenuSession(['sudo', '/usr/local/sbin/v-ui'], cwd='/tmp', env=dict(os.environ), timeout=30) as terminal:
            terminal.expect('请选择: ')
            terminal.send('20\n')
            self.test.assertIn('未发现独立核心选择记录。', terminal.expect('请选择: '))
            terminal.send('0\n')
            terminal.finish()

    def after(self, api):
        config = json.loads(Path('/etc/v-ui/service.json').read_bytes())
        release = Path('/var/lib/v-ui/releases') / self.candidate['release_id']
        assert_upgrade_configuration(self.test,self.config,config,self.old_release,release)
        manifest = self._manifest(release)
        self.test.assertEqual(manifest['source_commit'], self.candidate['source_commit'])
        self.test.assertEqual(manifest['version'], self.candidate['version'])
        assert_receipt_migration(self.test, self.old_receipt, self._receipt(), self.old_version, self.candidate['version'],
                                 core_control=True, core_authority=True, yaml_control=True)
        self.test.assertEqual(self._snapshot(release,after=True), self.data, 'Private DB rows and certificate bytes must survive')
        if self.yaml_state is not None:
            self.test.assertEqual(self._service_python(release, yaml_draft_program()), self.yaml_state,
                                  'Saved YAML plan and all private source bytes must survive')
        if self.old_version in DNS_BASELINES:
            self._assert_candidate_origin(release, config)
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
        if self.old_version == '0.4.23' and self.candidate['version'] == '0.4.24':
            print('VUI_23_TO_24_UPGRADE_ACCEPTANCE: existing DNS identity and private credentials retained, accepted Root origin and default core selection verified')
        if self.old_version == '0.4.24' and self.candidate['version'] == '0.4.25':
            print('VUI_24_TO_25_UPGRADE_ACCEPTANCE: public application, saved data and DNS materials retained, previous and candidate Root origins verified, YAML control reader installed')
        if self.old_version == '0.4.25':
            print('VUI_25_TO_26_UPGRADE_ACCEPTANCE: prior and candidate Root origins, private DB and DNS material, saved YAML plan and three source digests verified')
        print('Published upgrade: immutable public '+self.old_version+' source/assets -> exact candidate; exact historical root receipt closure; config, users/password hashes, node draft/manual TLS paths, live subscription, provided/managed fixture certificate bytes and managed certificate/binding metadata retained (synthetic un-applied Xray binding on 0.4.16/17/19/20/21; no CA/core qualification); candidate-only bounded downloads OK')

    def clear_yaml_fixture(self, api):
        if self.yaml_state is None:
            return
        status, raw = api('/api/proxy/yaml/draft', method='DELETE',
                          extra_headers=['If-Match: ' + self.yaml_state['revision']])
        self.test.assertEqual(status, 200, 'Guarded cleanup of the checked synthetic YAML draft failed')
        snapshot = json.loads(raw)
        self.test.assertEqual(snapshot['execution_mode'], 'single')
        self.test.assertIsNone(snapshot['yaml_plan'])
        self.test.assertIsNone(snapshot['active'])

    def public_subscription(self, api):
        status, raw = api(self.subscription)
        self.test.assertEqual(status, 200)
        self.test.assertTrue(raw == self.subscription_bytes, 'Existing public subscription bytes changed')
