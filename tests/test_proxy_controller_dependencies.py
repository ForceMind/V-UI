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

REPO = Path(__file__).resolve().parents[1]
PROFILE = {'name': 'Controller fixture', 'server': 'upstream.example', 'server_port': 443,
           'uuid': '11111111-1111-4111-8111-111111111111',
           'tls': {'mode': 'tls', 'server_name': 'upstream.example'}}


def seed_proxy(data):
    store = ProxyStore(data)
    saved = store.save_upstream(PROFILE, store.read_snapshot()['revision'])
    settings = {key: value for key, value in saved['snapshot']['settings'].items()
                if key != 'local_password_set'}
    settings.update(local_proxy_enabled=True, relay_enabled=True, local_username='fixture',
                    selected_upstream_id=saved['upstream_id'])
    saved = store.save_draft(settings, saved['snapshot']['revision'], local_password='Fake-controller-password!')
    store.begin_apply(saved['revision'], 'fixture-committed')
    store.commit_apply('fixture-committed', 'fixture-core')
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
        for name in ('app/__init__.py', 'app/services/__init__.py', 'app/services/proxy_store.py'):
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

    def test_pre_fix_two_file_controller_reproduces_missing_app_services(self):
        for name in ('scripts/deploy.py', 'app/release_tools.py'):
            target = self.control / name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, target)
        backup = self.base / 'should-not-exist.zip'
        result = self.run_controller('backup', backup)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No module named 'app.services'", result.stderr)
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
                   'proxy_store.py': 'app/services/proxy_store.py',
                   'upstream_profile.py': 'app/services/upstream_profile.py'}
        for missing in ('proxy_store.py', 'upstream_profile.py'):
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
                             'proxy_store.py': 'app/services/proxy_store.py',
                             'upstream_profile.py': 'app/services/upstream_profile.py'}.items():
            shutil.copyfile(REPO / source, self.control / name)
        code = '''import runpy,sys
from pathlib import Path
worker=runpy.run_path(sys.argv[1]); api=worker['_load_release_tools']()
root=Path(sys.argv[2]); backup=Path(sys.argv[3])
checksum=api.backup(root,backup); api.restore(root,backup,checksum)
for name in ('app.services.proxy_store','app.services.upstream_profile'):
    assert Path(sys.modules[name].__file__).parent==Path(sys.argv[1]).parent
assert sys.modules['app'].__path__==[]
assert sys.modules['app.services'].__path__==[]
assert not any(name.startswith(('fastapi','yaml','app.api','app.services.core')) for name in sys.modules)
print('verified sibling backup/restore')
'''
        result = subprocess.run([sys.executable, '-B', '-I', '-S', '-c', code,
                                 str(self.control / 'ops_worker.py'), str(self.root), str(self.base / 'managed.zip')],
                                cwd=self.hostile, env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'verified sibling backup/restore')
        self.assert_preserved()


if __name__ == '__main__':
    unittest.main()
