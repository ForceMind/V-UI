"""Offline controller closure in real isolated children; fake state, no services."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile

from app.services.proxy_store import ProxyStore
from scripts import install_system as installer
from scripts import install_ops_control as control_installer
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
PROFILE = {'name': 'Controller fixture', 'server': 'upstream.example', 'server_port': 443,
           'uuid': '11111111-1111-4111-8111-111111111111',
           'tls': {'mode': 'tls', 'server_name': 'upstream.example'}}


def seed_runtime(store, data, revision, transaction):
    """A real immutable config/journal pair, without starting a core."""
    from app.services.core_runtime import encode, revision as core_revision
    settings = store.load_draft()['settings']
    needed = settings['local_proxy_enabled'] or settings['relay_enabled']
    config = {'inbounds': [{'tag': 'vui-relay-ingress-1' if settings['relay_enabled'] else 'direct-node',
                           'type': 'vless', 'listen_port': 24443}],
              'outbounds': [{'type': 'direct', 'tag': 'direct'}]}
    raw, digest = encode(config), core_revision(config)
    directory = data / 'runtime' / 'sing-box'
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory.parent.chmod(0o700)
    (directory / (digest + '.json')).write_bytes(raw)
    (directory / (digest + '.json')).chmod(0o600)
    state_path = directory / 'state.json'
    state_path.write_text(json.dumps({'revision': digest, 'enabled': True}))
    state_path.chmod(0o600)
    active = store.load_draft()
    previous = store.load_active()
    context = {'schema_version': 1, 'kind': 'apply', 'candidate_core_revision': digest,
        'previous_core_revision': previous['core_revision'] if previous else None, 'previous_enabled': False, 'policy_identity': 'b' * 64,
        'endpoint': {'profile_id': active['settings']['selected_upstream_id'], 'revision': revision,
                     'host': 'upstream.example', 'addresses': ['8.8.4.4'], 'resolved_at': 1} if needed else None,
        'self_addresses': ['127.0.0.1'] if needed else []}
    store.begin_apply(revision, transaction, runtime_context=context)
    store.commit_apply(transaction, digest)


def seed_proxy(data):
    store = ProxyStore(data)
    saved = store.save_upstream(PROFILE, store.read_snapshot()['revision'])
    settings = {key: value for key, value in saved['snapshot']['settings'].items()
                if key != 'local_password_set'}
    settings.update(local_proxy_enabled=True, relay_enabled=True, local_username='fixture',
                    selected_upstream_id=saved['upstream_id'])
    saved = store.save_draft(settings, saved['snapshot']['revision'], local_password='Fake-controller-password!')
    seed_runtime(store, data, saved['revision'], 'fixture-committed')
    return store


@unittest.skipIf(os.geteuid() == 0, 'Controllers must actually run as a non-root user')
class IsolatedProxyControllerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='vui-controller-closure-')
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.root = self.base / 'instance'
        self.root.mkdir(mode=0o700)
        self.data = self.root / 'data'
        self.data.mkdir(mode=0o700)
        with sqlite3.connect(self.data / 'v-ui.db') as db:
            db.executescript('CREATE TABLE users(id INTEGER); CREATE TABLE inbounds(id INTEGER);'
                             'CREATE TABLE admin_sessions(id INTEGER);'
                             'CREATE TABLE subscription_grants(revoked INTEGER);'
                             'INSERT INTO inbounds VALUES(7); INSERT INTO admin_sessions VALUES(3);'
                             'INSERT INTO subscription_grants VALUES(0);')
        self.store = seed_proxy(self.data)
        self.original = self.store.load_active()
        self.snapshot = self.store.read_snapshot()
        # Neither cwd nor PYTHONPATH may provide trusted controller dependencies.
        self.hostile = self.base / 'untrusted'
        (self.hostile / 'app/services').mkdir(parents=True)
        for name in ('app/__init__.py', 'app/data_owner.py', 'app/services/__init__.py', 'app/services/proxy_store.py'):
            (self.hostile / name).write_text('raise RuntimeError("MUTABLE_PAYLOAD_WAS_IMPORTED")')
        self.env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.hostile),
                    'PYTHONPATH': str(self.hostile), 'LANG': 'C.UTF-8'}
        self.control = self.base / 'controller'
        self.control.mkdir(mode=0o700)

    def bundle(self, *, omit=(), mutate=None):
        files = {name: (REPO / name).read_bytes()
                 for name in (*installer.CONTROLLER_MEMBERS, 'deploy/system_launcher.py') if name not in omit}
        manifest = {'schema': 1, 'kind': 'release', 'release_id': 'closure-fixture',
                    'platform': 'linux-multi-cpython312', 'targets': [installer.platform_support.target_key()],
                    'files': {name: {'size': len(raw), 'mode': 0o600,
                                     'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in files.items()}}
        if mutate:
            mutate(files)
        path = self.base / 'release.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('MANIFEST.json', json.dumps(manifest))
            for name, raw in files.items():
                archive.writestr(name, raw)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def extract(self):
        path, sha = self.bundle()
        snapshot, archive, _ = installer.verify_archive(path, sha, temp_dir=self.base)
        try:
            installer.extract_controller(archive, self.control, os.getuid(), os.getgid())
        finally:
            archive.close()
            snapshot.close()
        self.assertEqual({str(path.relative_to(self.control)) for path in self.control.rglob('*') if path.is_file()},
                         set(installer.CONTROLLER_MEMBERS))

    def run_controller(self, *arguments):
        return subprocess.run([sys.executable, '-B', '-I', '-S', str(self.control / 'scripts/deploy.py'),
                               '--root', str(self.root), *map(str, arguments)],
                              cwd=self.hostile, env=self.env, capture_output=True, text=True, timeout=15)

    def assert_preserved(self):
        self.assertEqual(self.store.read_snapshot(), self.snapshot)
        self.assertEqual(self.store.load_active(), self.original)
        with sqlite3.connect(self.data / 'v-ui.db') as db:
            self.assertEqual(db.execute('SELECT * FROM inbounds').fetchall(), [(7,)])
            self.assertEqual(db.execute('SELECT * FROM admin_sessions').fetchall(), [])
            self.assertEqual(db.execute('SELECT * FROM subscription_grants').fetchall(), [(1,)])

    def test_extracted_installer_controller_backs_up_and_restores_private_on_state(self):
        self.extract()
        backup = self.base / 'backup.zip'
        result = self.run_controller('backup', backup)
        self.assertEqual(result.returncode, 0, result.stderr)
        sha = hashlib.sha256(backup.read_bytes()).hexdigest()
        self.assertIn(sha, result.stdout)
        with zipfile.ZipFile(backup) as archive:
            self.assertIn('.proxy-state-initialized', archive.namelist())
            self.assertTrue(any(name.startswith('proxy-state/secrets/') for name in archive.namelist()))
            self.assertNotIn('.proxy-state.lock', archive.namelist())
        result = self.run_controller('restore', backup, '--sha256', sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_preserved()

    def test_pre_fix_two_file_controller_refuses_missing_stdlib_closure(self):
        for name in ('scripts/deploy.py', 'app/release_tools.py'):
            target = self.control / name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, target)
        backup = self.base / 'should-not-exist.zip'
        result = self.run_controller('backup', backup)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No module named 'app.data_owner'", result.stderr)
        self.assertFalse(backup.exists())
        self.assertEqual(self.store.read_snapshot(), self.snapshot)

    def test_each_missing_or_tampered_archive_dependency_refuses_before_extraction(self):
        for name in installer.CONTROLLER_MEMBERS[2:]:
            with self.subTest(name=name):
                path, sha = self.bundle(omit=(name,))
                with self.assertRaises(installer.InstallError):
                    installer.verify_archive(path, sha, temp_dir=self.base)
                path, sha = self.bundle(mutate=lambda files: files.__setitem__(name, b'changed'))
                with self.assertRaises(installer.InstallError):
                    installer.verify_archive(path, sha, temp_dir=self.base)
        self.assertEqual(list(self.control.iterdir()), [])
        self.assertEqual(self.store.read_snapshot(), self.snapshot)

    def test_managed_worker_missing_sibling_refuses_without_loading_mutable_fallback(self):
        sources = {'ops_worker.py': 'deploy/ops_worker.py', 'release_tools.py': 'app/release_tools.py',
                   'data_owner.py': 'app/data_owner.py',
                   'proxy_store.py': 'app/services/proxy_store.py',
                   'upstream_profile.py': 'app/services/upstream_profile.py',
                   'inbound_policy.py': 'app/services/inbound_policy.py',
                   **control_installer.DNS_SOURCE_DEPENDENCIES, **control_installer.YAML_SOURCE_DEPENDENCIES}
        for missing in ('data_owner.py', 'proxy_store.py', 'upstream_profile.py', 'inbound_policy.py',
                        *control_installer.DNS_SOURCE_DEPENDENCIES, *control_installer.YAML_SOURCE_DEPENDENCIES):
            with self.subTest(missing=missing):
                for name, source in sources.items():
                    target = self.control / name
                    if name == missing:
                        target.unlink(missing_ok=True)
                    else:
                        shutil.copyfile(REPO / source, target)
                result = subprocess.run([sys.executable, '-B', '-I', '-S', str(self.control / 'ops_worker.py')],
                                        cwd=self.hostile, env=self.env, input='',
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout), {'event': 'error', 'code': 'MAINTENANCE_REFUSED'})
                self.assertEqual(result.stderr, '')
                self.assertEqual(self.store.read_snapshot(), self.snapshot)
                self.assertEqual(self.store.load_active(), self.original)

    def test_managed_sibling_loader_backs_up_and_restores_without_payload_import(self):
        for name, source in {'ops_worker.py': 'deploy/ops_worker.py', 'release_tools.py': 'app/release_tools.py',
                             'data_owner.py': 'app/data_owner.py',
                             'proxy_store.py': 'app/services/proxy_store.py',
                             'upstream_profile.py': 'app/services/upstream_profile.py',
                   'inbound_policy.py': 'app/services/inbound_policy.py',
                   **control_installer.DNS_SOURCE_DEPENDENCIES, **control_installer.YAML_SOURCE_DEPENDENCIES}.items():
            shutil.copyfile(REPO / source, self.control / name)
        code = '''import runpy,sys
from pathlib import Path
worker=runpy.run_path(sys.argv[1]); api=worker['_load_release_tools']()
root=Path(sys.argv[2]); backup=Path(sys.argv[3])
checksum=api.backup(root,backup); api.restore(root,backup,checksum)
for name in ('app.data_owner','app.services.proxy_store','app.services.upstream_profile','app.services.inbound_policy','app.services.mihomo_plan_record',
             'app.certificates.errors','app.certificates.dns_store','app.certificates.cloudflare',
             'app.certificates.dns_journal','app.certificates.dns_backup'):
    assert Path(sys.modules[name].__file__).parent==Path(sys.argv[1]).parent
assert sys.modules['app'].__path__==[]
assert sys.modules['app.services'].__path__==[]
assert sys.modules['app.certificates'].__path__==[]
assert not any(name.startswith(('fastapi','yaml','app.api','app.services.core','cryptography','OpenSSL','sqlalchemy','app.certificates.material')) for name in sys.modules)
print('verified sibling backup/restore')
'''
        result = subprocess.run([sys.executable, '-B', '-I', '-S', '-c', code,
                                 str(self.control / 'ops_worker.py'), str(self.root), str(self.base / 'managed.zip')],
                                cwd=self.hostile, env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'verified sibling backup/restore')
        self.assert_preserved()


class DNSStdlibClosureTests(unittest.TestCase):
    """Only verified fake archives/imports, with no HTTP, core, PKI or systemd."""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='vui-dns-closure-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.control = self.base / 'controller'; self.control.mkdir(mode=0o700)
        self.hostile = self.base / 'hostile'; (self.hostile / 'app/certificates').mkdir(parents=True)
        for name in ('__init__.py','errors.py','dns_store.py','cloudflare.py','dns_journal.py','dns_backup.py','material.py'):
            (self.hostile / 'app/certificates' / name).write_text('raise RuntimeError("UNTRUSTED_PAYLOAD_IMPORTED")')
        self.data = self.base / 'data'; self.data.mkdir(mode=0o700)
        self.env = {'PATH':'/usr/bin:/bin','HOME':str(self.hostile),'PYTHONPATH':str(self.hostile),'LANG':'C.UTF-8'}

    def flat(self):
        sources = {'ops_worker.py':'deploy/ops_worker.py','release_tools.py':'app/release_tools.py',
                   'data_owner.py':'app/data_owner.py','proxy_store.py':'app/services/proxy_store.py',
                   'upstream_profile.py':'app/services/upstream_profile.py','inbound_policy.py':'app/services/inbound_policy.py',
                   **control_installer.DNS_SOURCE_DEPENDENCIES, **control_installer.YAML_SOURCE_DEPENDENCIES}
        for target, source in sources.items(): shutil.copyfile(REPO/source,self.control/target)

    def isolated(self, code):
        return subprocess.run([sys.executable,'-B','-I','-S','-c',code,str(self.control),str(self.data)],
                              cwd=self.hostile,env=self.env,capture_output=True,text=True,timeout=10)

    def test_all_five_authenticated_siblings_load_with_empty_paths_without_pki_or_orm(self):
        self.flat()
        code = """import runpy,sys
from pathlib import Path
root=Path(sys.argv[1]); api=runpy.run_path(str(root/'ops_worker.py'))['_load_release_tools']()
from app.certificates.dns_backup import validate_dns_snapshot
assert validate_dns_snapshot(Path(sys.argv[2])) is False
for name in ('errors','dns_store','cloudflare','dns_journal','dns_backup'):
 assert Path(sys.modules['app.certificates.'+name].__file__).parent==root
assert sys.modules['app'].__path__==[] and sys.modules['app.certificates'].__path__==[]
assert not any(name.startswith(('cryptography','OpenSSL','sqlalchemy','fastapi','app.certificates.material','app.models')) for name in sys.modules)
print('stdlib trusted siblings only')
"""
        result=self.isolated(code)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'stdlib trusted siblings only')

    def test_each_missing_flat_dns_sibling_refuses_instead_of_payload_fallback(self):
        code="import runpy,sys;from pathlib import Path;runpy.run_path(str(Path(sys.argv[1])/'ops_worker.py'))['_load_release_tools']()"
        for name in control_installer.DNS_SOURCE_DEPENDENCIES:
            self.flat(); (self.control/name).unlink()
            result=self.isolated(code)
            with self.subTest(name=name):
                self.assertNotEqual(result.returncode,0)
                self.assertIn(name,result.stderr)
                self.assertNotIn('UNTRUSTED_PAYLOAD_IMPORTED',result.stderr)

    def test_each_dns_archive_dependency_missing_or_changed_refuses_before_extraction(self):
        names=(*installer.CONTROLLER_MEMBERS,'deploy/system_launcher.py')
        original={name:(REPO/name).read_bytes() for name in names}
        for selected in control_installer.DNS_SOURCE_DEPENDENCIES.values():
            for fault in ('missing','changed'):
                files=dict(original)
                if fault=='missing': files.pop(selected)
                manifest={'schema':1,'kind':'release','release_id':'dns-closure-fixture',
                          'platform':'linux-multi-cpython312','targets':['x86_64-gnu'],
                          'files':{name:{'size':len(raw),'mode':0o600,'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in files.items()}}
                if fault=='changed': files[selected]+=b'\n# untrusted replacement'
                path=self.base/'bad.zip'
                with zipfile.ZipFile(path,'w') as archive:
                    archive.writestr('MANIFEST.json',json.dumps(manifest))
                    for name,raw in files.items(): archive.writestr(name,raw)
                with self.subTest(selected=selected,fault=fault), \
                        patch.object(installer.platform_support,'target_key',return_value='x86_64-gnu'), \
                        self.assertRaises(installer.InstallError):
                    installer.verify_archive(path,hashlib.sha256(path.read_bytes()).hexdigest(),temp_dir=self.base)
                self.assertEqual(list(self.control.iterdir()),[])

    def test_installer_extracts_dns_closure_from_verified_manifest_with_same_bytes(self):
        names=(*installer.CONTROLLER_MEMBERS,'deploy/system_launcher.py')
        expected={'app/certificates/'+name for name in ('errors.py','dns_store.py','cloudflare.py','dns_journal.py','dns_backup.py')}
        self.assertTrue(expected<=set(installer.CONTROLLER_MEMBERS))
        files={name:(REPO/name).read_bytes() for name in names}
        manifest={'schema':1,'kind':'release','release_id':'dns-closure-fixture',
                  'platform':'linux-multi-cpython312','targets':['x86_64-gnu'],
                  'files':{name:{'size':len(raw),'mode':0o600,'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in files.items()}}
        path=self.base/'fake.zip'
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr('MANIFEST.json',json.dumps(manifest))
            for name,raw in files.items(): archive.writestr(name,raw)
        with patch.object(installer.platform_support,'target_key',return_value='x86_64-gnu'):
            snapshot,archive,_=installer.verify_archive(path,hashlib.sha256(path.read_bytes()).hexdigest(),temp_dir=self.base)
            try: installer.extract_controller(archive,self.control,os.getuid(),os.getgid())
            finally: archive.close(); snapshot.close()
        for name in expected:
            self.assertEqual((self.control/name).read_bytes(),files[name])
            self.assertEqual((self.control/name).stat().st_mode & 0o777,0o600)
        code="""import runpy,sys
from pathlib import Path
root=Path(sys.argv[1]); runpy.run_path(str(root/'scripts/deploy.py'))
from app.certificates.dns_backup import validate_dns_snapshot
assert validate_dns_snapshot(Path(sys.argv[2])) is False
assert not any(name.startswith(('cryptography','OpenSSL','sqlalchemy','fastapi','app.certificates.material')) for name in sys.modules)
print('verified extracted stdlib closure')
"""
        result=self.isolated(code)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'verified extracted stdlib closure')


if __name__ == '__main__':
    unittest.main()
