"""Only temporary SQLite files and fake certificate contact details are used."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from app.data_owner import data_owner
from app.release_tools import ReleaseError
from scripts.installer_certificate_state import MAX_JSON, SELF_CONTAINED_CODE, run, safe_file


class InstallerCertificateStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='vui-installer-cert-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.root.chmod(0o700)
        self.data = self.root / 'data'
        self.db_path = self.data / 'v-ui.db'

    def initialize(self):
        self.data.mkdir(mode=0o700)
        with sqlite3.connect(self.db_path) as db:
            db.executescript('''
                CREATE TABLE managed_certificates (
                    id VARCHAR(32) PRIMARY KEY, domain VARCHAR(253), email VARCHAR(254),
                    environment VARCHAR(16), auto_renew BOOLEAN, revision VARCHAR(64),
                    not_before INTEGER, not_after INTEGER, renew_at INTEGER,
                    retry_at INTEGER, last_attempt INTEGER, failures INTEGER,
                    error VARCHAR(64), created_at INTEGER);
                CREATE TABLE certificate_bindings (
                    target VARCHAR(64) PRIMARY KEY, certificate_id VARCHAR(32),
                    applied_certificate_id VARCHAR(32), applied_revision VARCHAR(64), error VARCHAR(64));
                CREATE TABLE users (id INTEGER PRIMARY KEY, password_hash TEXT);
                CREATE TABLE sessions (id TEXT PRIMARY KEY, user_id INTEGER);
                CREATE TABLE grants (id TEXT PRIMARY KEY, token TEXT);
                CREATE TABLE subscription_grants (id TEXT PRIMARY KEY, token_hash TEXT);
                CREATE TABLE inbounds (id INTEGER PRIMARY KEY, config TEXT);
                CREATE TABLE certificate_jobs (id TEXT PRIMARY KEY, state TEXT);
                INSERT INTO users VALUES (1,'fake-hash');
                INSERT INTO sessions VALUES ('fake-session',1);
                INSERT INTO grants VALUES ('fake-grant','fake-token');
                INSERT INTO subscription_grants VALUES ('fake-subscription','fake-token-hash');
                INSERT INTO inbounds VALUES (1,'fake-node');
                INSERT INTO certificate_jobs VALUES ('old-job','succeeded');
            ''')
            self.certificate(db, 'old', 'old.example.test', 'old@example.test')
            self.certificate(db, 'stage', 'old.example.test', 'stage@example.test', 'staging')
            db.execute('INSERT INTO certificate_bindings VALUES (?,?,?,?,?)',
                       ('panel', 'old', 'old', 'a' * 64, None))
            db.execute('INSERT INTO certificate_bindings VALUES (?,?,?,?,?)',
                       ('inbound:1', 'old', 'old', 'a' * 64, 'OLD_ERROR'))

    def certificate(self, db, identifier, domain, email, environment='production'):
        db.execute('INSERT INTO managed_certificates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (identifier, domain, email, environment, 1, 'a' * 64, 1, 2, 3, 0, 0, 0, None, 1))

    def query(self, sql):
        with sqlite3.connect(self.db_path) as db:
            return db.execute(sql).fetchall()

    def snapshot(self):
        return run('snapshot', self.root)

    def restore(self, state):
        return run('restore', self.root, json.dumps(state))

    def test_absent_database_is_not_created(self):
        state = self.snapshot()
        self.assertFalse(state['db_exists'])
        self.assertFalse(state['ready'])
        self.assertFalse(self.data.exists())
        self.assertEqual(run('contact', self.root, 'new.example.test', 'new@example.test'), {'updated': 0})
        self.restore(state)
        self.assertFalse(self.db_path.exists())

    def test_uninitialized_database_is_not_migrated(self):
        self.data.mkdir(mode=0o700)
        with sqlite3.connect(self.db_path):
            pass
        state = self.snapshot()
        self.assertTrue(state['db_exists'])
        self.assertFalse(state['ready'])
        self.assertEqual(self.query('SELECT name FROM sqlite_schema'), [])

    def test_first_install_rollback_removes_only_new_panel_binding(self):
        state = self.snapshot()
        self.initialize()
        pointer = self.root / 'CURRENT.json'
        current = {'release_id': 'prepared-new', 'previous_id': None}
        pointer.write_text(json.dumps(current))
        tables = ('managed_certificates', 'certificate_jobs', 'users', 'sessions',
                  'grants', 'subscription_grants', 'inbounds')
        retained = {table: self.query('SELECT * FROM ' + table) for table in tables}
        self.restore(state)
        self.restore(state)
        self.assertEqual(self.query('SELECT target FROM certificate_bindings'), [('inbound:1',)])
        self.assertEqual(json.loads(pointer.read_text()), current)
        for table, expected in retained.items():
            self.assertEqual(self.query('SELECT * FROM ' + table), expected)

    @unittest.skipUnless(importlib.util.find_spec('sqlalchemy'), 'SQLAlchemy is required for actual model schema')
    def test_actual_model_schema_and_uninitialized_business_state_survive_rollback(self):
        from sqlalchemy import create_engine
        from app.certificates.models import Certificate, CertificateBinding

        self.data.mkdir(mode=0o700)
        with sqlite3.connect(self.db_path) as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, password_hash TEXT);
                CREATE TABLE sessions (id TEXT PRIMARY KEY, user_id INTEGER);
                CREATE TABLE subscription_grants (id TEXT PRIMARY KEY, token_hash TEXT);
                INSERT INTO users VALUES (1,'fake-retained-hash');
                INSERT INTO sessions VALUES ('fake-retained-session',1);
                INSERT INTO subscription_grants VALUES ('fake-retained-grant','fake-retained-token-hash');
            ''')
        # Existing business data without initialized certificate tables occurs
        # during an unfinished install. Do not initialize the application's
        # default engine or call init_db; create only these real model tables.
        unfinished = self.snapshot()
        self.assertTrue(unfinished['db_exists'])
        self.assertFalse(unfinished['ready'])
        retained = {table: self.query('SELECT * FROM ' + table)
                    for table in ('users', 'sessions', 'subscription_grants')}
        engine = create_engine('sqlite:///' + str(self.db_path))
        try:
            Certificate.__table__.create(engine)
            CertificateBinding.__table__.create(engine)
        finally:
            engine.dispose()
        with sqlite3.connect(self.db_path) as db:
            self.certificate(db, 'old', 'old.example.test', 'old@example.test')
            db.execute('INSERT INTO certificate_bindings VALUES (?,?,?,?,?)',
                       ('panel', 'old', 'old', 'a' * 64, None))
        original = self.snapshot()
        self.assertTrue(original['ready'])
        self.assertEqual(run('contact', self.root, 'old.example.test', 'new@example.test'), {'updated': 1})
        with sqlite3.connect(self.db_path) as db:
            self.certificate(db, 'new', 'new.example.test', 'new@example.test')
            db.execute("UPDATE certificate_bindings SET certificate_id='new',applied_certificate_id='new' WHERE target='panel'")
            db.execute("INSERT INTO certificate_bindings VALUES ('inbound:1','new',NULL,NULL,NULL)")
        self.restore(original)
        self.assertEqual(self.snapshot()['panel_binding'], original['panel_binding'])
        self.assertEqual(self.query("SELECT email FROM managed_certificates WHERE id='old'"), [('old@example.test',)])
        certificates = self.query('SELECT * FROM managed_certificates')
        self.restore(unfinished)
        self.assertEqual(self.query('SELECT target FROM certificate_bindings'), [('inbound:1',)])
        self.assertEqual(self.query('SELECT * FROM managed_certificates'), certificates)
        for table, expected in retained.items():
            self.assertEqual(self.query('SELECT * FROM ' + table), expected)

    def test_file_identity_uses_device_and_inode(self):
        self.initialize()
        identity = safe_file(self.db_path)
        info = self.db_path.stat()
        self.assertIs(type(identity), tuple)
        self.assertEqual(identity, (info.st_dev, info.st_ino))
        replacement = self.data / 'replacement.db'
        replacement.write_bytes(b'fake replacement')
        replacement.replace(self.db_path)
        self.assertNotEqual(safe_file(self.db_path), identity)

    def test_contact_changes_only_selected_production_certificate(self):
        self.initialize()
        result = run('contact', self.root, 'OLD.example.test.', 'new@EXAMPLE.test')
        self.assertEqual(result, {'updated': 1})
        self.assertEqual(self.query('SELECT id,email FROM managed_certificates ORDER BY id'),
                         [('old', 'new@example.test'), ('stage', 'stage@example.test')])
        self.assertEqual(run('contact', self.root, 'absent.example.test', 'n@example.test'), {'updated': 0})

    def test_invalid_contact_is_rejected_before_writing(self):
        self.initialize()
        before = self.snapshot()
        for domain, email in [('bad domain', 'n@example.test'), ('old.example.test', 'bad\n@example.test'),
                               ('127.0.0.1', 'n@example.test'), ('old.example.test', 'n@localhost')]:
            with self.subTest(domain=domain, email=email), self.assertRaises(ValueError):
                run('contact', self.root, domain, email)
        self.assertEqual(self.snapshot(), before)

    def test_restore_old_binding_and_contacts_preserves_live_business_rows(self):
        self.initialize()
        state = self.snapshot()
        retained = {table: self.query('SELECT * FROM ' + table)
                    for table in ('users', 'sessions', 'grants', 'subscription_grants', 'inbounds')}
        with sqlite3.connect(self.db_path) as db:
            self.certificate(db, 'new', 'new.example.test', 'new@example.test')
            db.execute("UPDATE managed_certificates SET email='changed@example.test' WHERE id='old'")
            db.execute("UPDATE certificate_bindings SET certificate_id='new',applied_certificate_id='new',"
                       "applied_revision=?,error='NEW_ERROR' WHERE target='panel'", ('b' * 64,))
            db.execute("INSERT INTO certificate_jobs VALUES ('new-job','queued')")
            # New sessions created after the snapshot must also survive rollback.
            db.execute("INSERT INTO sessions VALUES ('later-session',1)")
        retained['sessions'].append(('later-session', 1))
        self.restore(state)
        self.restore(state)  # Recovery after another interruption is idempotent.
        self.assertEqual(self.snapshot()['panel_binding'], state['panel_binding'])
        self.assertEqual(self.query("SELECT email FROM managed_certificates WHERE id='old'"), [('old@example.test',)])
        self.assertEqual(self.query("SELECT email FROM managed_certificates WHERE id='new'"), [('new@example.test',)])
        self.assertEqual(self.query('SELECT * FROM certificate_jobs'),
                         [('old-job', 'succeeded'), ('new-job', 'queued')])
        self.assertEqual(self.query("SELECT error FROM certificate_bindings WHERE target='inbound:1'"), [('OLD_ERROR',)])
        for table, expected in retained.items():
            self.assertEqual(self.query('SELECT * FROM ' + table), expected)

    def test_restore_absent_original_panel_removes_only_new_panel_binding(self):
        self.initialize()
        with sqlite3.connect(self.db_path) as db:
            db.execute("DELETE FROM certificate_bindings WHERE target='panel'")
        state = self.snapshot()
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO certificate_bindings VALUES ('panel','old',NULL,NULL,NULL)")
        self.restore(state)
        self.assertEqual(self.query('SELECT target FROM certificate_bindings'), [('inbound:1',)])

    def test_restore_failure_rolls_back_all_email_updates(self):
        self.initialize()
        state = self.snapshot()
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE managed_certificates SET email='changed@example.test' WHERE id='old'")
            db.execute("DELETE FROM managed_certificates WHERE id='stage'")
        with self.assertRaises(ValueError):
            self.restore(state)
        self.assertEqual(self.query("SELECT email FROM managed_certificates WHERE id='old'"), [('changed@example.test',)])

    def test_restore_complete_pointer_and_keep_new_pointer_when_original_absent(self):
        state = self.snapshot()
        pointer = self.root / 'CURRENT.json'
        pointer.write_text(json.dumps({'release_id': 'new', 'previous_id': 'old'}))
        self.restore(state)
        self.assertEqual(json.loads(pointer.read_text())['release_id'], 'new')
        old = {'release_id': 'old', 'previous_id': None, 'extra': {'retained': True}}
        pointer.write_text(json.dumps(old))
        state = self.snapshot()
        pointer.write_text(json.dumps({'release_id': 'new', 'previous_id': 'old'}))
        self.restore(state)
        self.assertEqual(json.loads(pointer.read_text()), old)

    def test_symlink_database_pointer_and_ancestor_are_rejected(self):
        self.initialize()
        real = self.data / 'real.db'
        self.db_path.rename(real)
        self.db_path.symlink_to(real)
        with self.assertRaises(ValueError):
            self.snapshot()
        self.db_path.unlink()
        real.rename(self.db_path)
        pointer = self.root / 'CURRENT.json'
        pointer.symlink_to(self.root / 'missing')
        with self.assertRaises(ValueError):
            self.snapshot()
        pointer.unlink()
        link = self.root / 'link'
        link.symlink_to(self.data, target_is_directory=True)
        with self.assertRaises(OSError):
            run('snapshot', link)

    def test_sidecar_symlink_is_rejected(self):
        self.initialize()
        Path(str(self.db_path) + '-wal').symlink_to(self.root / 'missing')
        with self.assertRaises(ValueError):
            self.snapshot()

    def test_malformed_schema_and_trigger_are_rejected(self):
        self.initialize()
        with sqlite3.connect(self.db_path) as db:
            db.execute('CREATE TRIGGER unsafe AFTER UPDATE ON managed_certificates BEGIN DELETE FROM sessions; END')
        with self.assertRaises(ValueError):
            run('contact', self.root, 'old.example.test', 'n@example.test')
        self.assertEqual(self.query('SELECT count(*) FROM sessions'), [(1,)])
        with sqlite3.connect(self.db_path) as db:
            db.execute('DROP TRIGGER unsafe')
            db.execute('ALTER TABLE certificate_bindings ADD COLUMN surprise TEXT')
        with self.assertRaises(ValueError):
            self.snapshot()

    def test_malformed_oversize_and_duplicate_state_rejected(self):
        state = self.snapshot()
        for raw in ('x' * (MAX_JSON + 1), '[]', '{"version":1,"version":1}'):
            with self.subTest(raw=raw[:40]), self.assertRaises((ValueError, json.JSONDecodeError)):
                run('restore', self.root, raw)
        state['panel_binding'] = ['panel', 'unknown', None, None, None]
        with self.assertRaises(ValueError):
            self.restore(state)

    def test_existing_writer_and_worker_locks_are_respected(self):
        self.initialize()
        with data_owner(self.data):
            with self.assertRaises(ReleaseError):
                self.snapshot()
        worker_dir = self.data / 'certificates'
        worker_dir.mkdir(mode=0o700)
        path = worker_dir / '.worker.lock'
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(ReleaseError):
                self.snapshot()
        finally:
            os.close(fd)

    def test_isolated_no_site_source_runs_with_only_verified_controller_path(self):
        # The installer supplies this prefix after archive verification; -I -S
        # excludes environment PYTHONPATH, site packages, and the installed app.
        source = 'import sys;sys.path.insert(0,' + repr(str(Path(__file__).resolve().parents[1])) + ')\n' + SELF_CONTAINED_CODE
        result = subprocess.run([sys.executable, '-B', '-I', '-S', '-c', source,
            'snapshot', str(self.root)], text=True, capture_output=True, timeout=10)
        if os.geteuid() == 0:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('service user', result.stderr)
        else:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)['db_exists'])


if __name__ == '__main__':
    unittest.main()
