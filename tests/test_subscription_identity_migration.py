"""Atomic subscription identity migration and offline restore, synthetic DB only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.models import database
from app.services.subscription_tokens import SubscriptionGrant, create_grant, list_grants, change_grant


def _initialize_in_separate_process(path):
    """Each child owns a fresh engine and process-local operation lock."""
    engine = create_engine(f'sqlite:///{path}', poolclass=NullPool)
    try:
        with patch.object(database, 'engine', engine), patch('subprocess.Popen', side_effect=AssertionError('No runtime process')):
            database.init_db()
    finally:
        engine.dispose()


def _crash_migration_process(path, boundary):
    """Exit without connection/lease cleanup, only against a synthetic DB."""
    engine = create_engine(f'sqlite:///{path}', poolclass=NullPool)
    def crash(conn, cursor, statement, parameters, context, executemany):
        if boundary in statement:
            os._exit(71)
    event.listen(engine, 'after_cursor_execute', crash)
    with patch.object(database, 'engine', engine), patch('subprocess.Popen', side_effect=AssertionError('No runtime process')):
        database.init_db()
    os._exit(72)  # The injected crash boundary must actually execute.


class SubscriptionIdentityMigrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-identity-migration-')))
        self.data = self.root / 'data'; self.data.mkdir(mode=0o700)
        self.path = self.data / 'v-ui.db'
        self.engine = create_engine(f'sqlite:///{self.path}', poolclass=NullPool)
        self.addCleanup(self.engine.dispose)
        self.stack.enter_context(patch.multiple(database, engine=self.engine,
            SessionLocal=sessionmaker(bind=self.engine, autoflush=False)))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('No subprocess allowed')))

    def legacy(self):
        # Actual pre-change schema, with all synthetic credentials/rows retained.
        with sqlite3.connect(self.path) as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR, password_hash VARCHAR, is_active BOOLEAN);
                CREATE TABLE inbounds (id INTEGER PRIMARY KEY, user_id INTEGER, core VARCHAR, routing_mode VARCHAR,
                    up INTEGER, down INTEGER, total INTEGER, remark VARCHAR, enable BOOLEAN, expiry_time INTEGER,
                    port INTEGER, protocol VARCHAR, settings JSON, stream_settings JSON, tag VARCHAR);
                CREATE TABLE subscription_grants (id INTEGER PRIMARY KEY, owner_id INTEGER, token_hash VARCHAR,
                    credential_stamp VARCHAR, label VARCHAR, server VARCHAR, inbound_ids JSON, formats JSON,
                    created_at INTEGER, expires_at INTEGER, revoked BOOLEAN);
                CREATE TABLE admin_sessions (token_hash VARCHAR);
                INSERT INTO users VALUES (1,'synthetic','synthetic-password-hash',1);
                INSERT INTO subscription_grants VALUES (1,1,'synthetic-old-digest','synthetic-stamp','old',
                    'vpn.example.test','[2]','["raw"]',1,9999999999,0);
                INSERT INTO admin_sessions VALUES ('synthetic-session');
            ''')
            for identifier in (1, 2):
                db.execute('INSERT INTO inbounds VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (identifier, 1, 'sing-box', 'inherit', 3, 4, 5, 'synthetic', 1, 0, 10000+identifier,
                     'vless', json.dumps({'users':[{'uuid':f'11111111-1111-1111-1111-11111111111{identifier}'}]}),
                     json.dumps({'tls':{'enabled':True,'server_name':'vpn.example.test'}}), f'node-{identifier}'))

    def rows(self, table):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY 1')]

    def test_legacy_migration_preserves_every_original_value_and_never_binds_old_grants(self):
        self.legacy()
        before = {name:self.rows(name) for name in ('inbounds','users','subscription_grants','admin_sessions')}
        database.init_db()
        first = self.rows('inbounds')
        self.assertEqual(len({row['instance_id'] for row in first}), 2)
        for old, new in zip(before['inbounds'], first):
            original = dict(new); self.assertRegex(original.pop('instance_id'), r'^[0-9a-f]{32}$')
            self.assertEqual(original, old)
        for name in ('users', 'admin_sessions'):
            self.assertEqual(self.rows(name), before[name])
        grant = self.rows('subscription_grants')[0]
        self.assertIsNone(grant.pop('inbound_bindings'))
        self.assertEqual(grant, before['subscription_grants'][0])
        self.assertEqual(self.rows('subscription_identity_schema'), [{'version':1}])
        for _ in range(3): database.init_db()
        self.assertEqual(self.rows('inbounds'), first)
        self.assertIsNone(self.rows('subscription_grants')[0]['inbound_bindings'])
        listed = list_grants(1)[0]
        self.assertTrue(listed['invalidated']); self.assertEqual(listed['invalidation_reason'], 'node_identity_missing')
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as raised: change_grant(1, 1, rotate_days=90)
        self.assertEqual(raised.exception.status_code, 409)
        created, _ = create_grant(1, 'explicit new authorization', 'vpn.example.test', [2], ['raw'], 90)
        self.assertNotEqual(created['id'], 1)
        self.assertIsNone(self.rows('subscription_grants')[0]['inbound_bindings'])

    def test_concurrent_first_initialization_serializes_without_partial_migration(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                if self.path.exists(): self.path.unlink()
                if legacy: self.legacy()
                # Bypass only the in-process operation lock to exercise the
                # SQLite cross-connection lock used by separate processes.
                from contextlib import nullcontext
                from app.services.core_manager import core_manager
                barrier = threading.Barrier(4)
                def start(_):
                    barrier.wait(timeout=5); database.init_db()
                with patch.object(core_manager, 'lock', nullcontext()), ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(start, range(4)))
                self.assertEqual(self.rows('subscription_identity_schema'), [{'version':1}])
                rows = self.rows('inbounds')
                database.init_db()
                self.assertEqual(self.rows('inbounds'), rows)
                self.assertEqual(len({row['instance_id'] for row in rows}), len(rows))

    def test_failure_rolls_back_columns_values_marker_and_create_all_tables(self):
        for boundary in ('identity', 'identity_column', 'identity_write', 'binding', 'binding_written', 'marker_table', 'marker', 'marker_written', 'commit'):
            with self.subTest(boundary=boundary):
                if self.path.exists(): self.path.unlink()
                self.legacy()
                before = self.path.read_bytes()
                tables = inspect(self.engine).get_table_names()
                if boundary == 'identity':
                    context = patch.object(database.secrets, 'token_hex', side_effect=['a'*32, OSError('synthetic entropy failure')])
                else:
                    def fail(conn, cursor, statement, parameters, context, executemany):
                        if ((boundary == 'binding' and 'ADD COLUMN inbound_bindings' in statement)
                                or (boundary == 'marker' and 'INSERT INTO subscription_identity_schema' in statement)
                                or (boundary == 'identity_column' and 'ADD COLUMN instance_id' in statement)
                                or (boundary == 'identity_write' and 'UPDATE inbounds SET instance_id' in statement)
                                or (boundary == 'binding_written' and 'ADD COLUMN inbound_bindings' in statement)
                                or (boundary == 'marker_table' and 'CREATE TABLE subscription_identity_schema' in statement)
                                or (boundary == 'marker_written' and 'INSERT INTO subscription_identity_schema' in statement)):
                            raise OSError('synthetic migration failure')
                    def fail_commit(conn): raise OSError('synthetic commit failure')
                    name, hook = (('commit', fail_commit) if boundary == 'commit' else
                                  ('after_cursor_execute' if boundary in {'identity_column', 'identity_write', 'binding_written', 'marker_table', 'marker_written'} else 'before_cursor_execute', fail))
                    event.listen(self.engine, name, hook)
                    self.addCleanup(lambda n=name, h=hook: event.remove(self.engine, n, h) if event.contains(self.engine, n, h) else None)
                    from contextlib import nullcontext
                    context = nullcontext()
                try:
                    with context, self.assertRaises(OSError): database.init_db()
                finally:
                    if boundary != 'identity': event.remove(self.engine, name, hook)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(inspect(self.engine).get_table_names(), tables)
                database.init_db()
                self.assertEqual(self.rows('subscription_identity_schema'), [{'version':1}])

    def test_separate_process_initialization_preserves_one_stable_migration(self):
        self.legacy()
        context = multiprocessing.get_context('spawn')
        processes = [context.Process(target=_initialize_in_separate_process, args=(str(self.path),)) for _ in range(4)]
        # Hold the SQLite writer lease until all children have started, so
        # they contend on the DB rather than sharing a Python operation lock.
        try:
            with sqlite3.connect(self.path) as conn:
                conn.execute('BEGIN IMMEDIATE')
                for process in processes: process.start()
                conn.commit()
            for process in processes:
                process.join(timeout=20)
                self.assertFalse(process.is_alive(), 'Database initializer failed to finish')
                self.assertEqual(process.exitcode, 0)
        finally:
            for process in processes:
                if process.is_alive(): process.terminate(); process.join(timeout=5)
                process.close()
        rows = self.rows('inbounds')
        self.assertEqual(len({row['instance_id'] for row in rows}), 2)
        self.assertEqual(self.rows('subscription_identity_schema'), [{'version':1}])
        database.init_db()
        self.assertEqual(self.rows('inbounds'), rows)
        self.assertIsNone(self.rows('subscription_grants')[0]['inbound_bindings'])

    def test_abrupt_process_exit_before_commit_recovers_original_database(self):
        for boundary in ('UPDATE inbounds SET instance_id', 'ADD COLUMN inbound_bindings',
                         'INSERT INTO subscription_identity_schema'):
            with self.subTest(boundary=boundary):
                if self.path.exists(): self.path.unlink()
                self.legacy()
                before = self.path.read_bytes()
                process = multiprocessing.get_context('spawn').Process(
                    target=_crash_migration_process, args=(str(self.path), boundary))
                try:
                    process.start(); process.join(timeout=20)
                    self.assertFalse(process.is_alive())
                    self.assertEqual(process.exitcode, 71)
                finally:
                    if process.is_alive(): process.terminate(); process.join(timeout=5)
                    process.close()
                with sqlite3.connect(self.path) as db:
                    self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                    self.assertNotIn('instance_id', {row[1] for row in db.execute('PRAGMA table_info(inbounds)')})
                self.assertEqual(self.path.read_bytes(), before)
                database.init_db()
                self.assertEqual(self.rows('subscription_identity_schema'), [{'version':1}])
                self.assertIsNone(self.rows('subscription_grants')[0]['inbound_bindings'])

    def test_marked_schema_column_or_table_loss_refuses_before_any_repair(self):
        for table, column in (('inbounds','instance_id'), ('subscription_grants','inbound_bindings'),
                              ('inbounds',None), ('subscription_grants',None)):
            with self.subTest(table=table, column=column):
                if self.path.exists(): self.path.unlink()
                self.legacy(); database.init_db()
                with sqlite3.connect(self.path) as db:
                    db.execute(f'ALTER TABLE {table} DROP COLUMN {column}' if column else f'DROP TABLE {table}')
                before = self.path.read_bytes()
                with self.assertRaises(database.SubscriptionIdentitySchemaError): database.init_db()
                self.assertEqual(self.path.read_bytes(), before)

    def test_unknown_empty_duplicate_or_malformed_marker_never_repaired(self):
        for change in ('UPDATE subscription_identity_schema SET version=2',
                       'DELETE FROM subscription_identity_schema',
                       'INSERT INTO subscription_identity_schema VALUES (1)',
                       'ALTER TABLE subscription_identity_schema ADD COLUMN extra TEXT'):
            with self.subTest(change=change):
                if self.path.exists(): self.path.unlink()
                self.legacy(); database.init_db()
                with sqlite3.connect(self.path) as db: db.execute(change)
                before = self.path.read_bytes()
                with self.assertRaises(database.SubscriptionIdentitySchemaError): database.init_db()
                self.assertEqual(self.path.read_bytes(), before)

    def test_marker_requires_integer_storage_not_numeric_lookalikes(self):
        for version in (1.0, '1', b'1', None, True):
            if version is True:
                # SQLite has no boolean storage class; a bound Python True is
                # the same integer 1, so use the textual malformed spelling.
                version = 'true'
            with self.subTest(version=version):
                if self.path.exists(): self.path.unlink()
                self.legacy(); database.init_db()
                with sqlite3.connect(self.path) as db:
                    db.execute('DROP TABLE subscription_identity_schema')
                    db.execute('CREATE TABLE subscription_identity_schema (version)')
                    db.execute('INSERT INTO subscription_identity_schema VALUES (?)', (version,))
                before = self.path.read_bytes()
                with self.assertRaises(database.SubscriptionIdentitySchemaError): database.init_db()
                self.assertEqual(self.path.read_bytes(), before)

    def test_existing_null_invalid_duplicate_identities_are_not_regenerated(self):
        self.legacy(); database.init_db()
        for identity in (None, '', 'corrupt', 'a'*32):
            with self.subTest(identity=identity):
                with sqlite3.connect(self.path) as db: db.execute('UPDATE inbounds SET instance_id=?', (identity,))
                before = self.rows('inbounds')
                with patch.object(database.secrets, 'token_hex', side_effect=AssertionError('Must not repair')):
                    database.init_db()
                self.assertEqual(self.rows('inbounds'), before)
                from fastapi import HTTPException
                with self.assertRaises(HTTPException) as raised:
                    create_grant(1, 'invalid', 'vpn.example.test', [1], ['raw'], 90)
                self.assertEqual(raised.exception.status_code, 409)

    def test_missing_unmarked_identity_column_with_bound_grant_is_not_legacy(self):
        self.legacy(); database.init_db()
        create_grant(1, 'bound', 'vpn.example.test', [2], ['raw'], 90)
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TABLE subscription_identity_schema')
            db.execute('ALTER TABLE inbounds DROP COLUMN instance_id')
        before = self.path.read_bytes()
        with self.assertRaises(database.SubscriptionIdentitySchemaError): database.init_db()
        self.assertEqual(self.path.read_bytes(), before)

    def test_offline_full_backup_restore_preserves_bindings_but_still_revokes_access(self):
        from app import release_tools
        from app.services import proxy_recovery
        self.legacy(); database.init_db()
        created, _ = create_grant(1, 'new binding', 'vpn.example.test', [2], ['raw'], 90)
        before_nodes = self.rows('inbounds'); before_grants = self.rows('subscription_grants')
        archive = self.root / 'synthetic-backup.zip'
        checksum = release_tools.backup(self.root, archive)
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE inbounds SET instance_id=? WHERE id=2', ('f'*32,))
        release_tools.restore(self.root, archive, checksum)
        self.assertEqual(self.rows('inbounds'), before_nodes)
        expected = [{**grant, 'revoked':1} for grant in before_grants]
        self.assertEqual(self.rows('subscription_grants'), expected)
        self.assertEqual(self.rows('admin_sessions'), [])
        self.assertEqual(self.rows('subscription_identity_schema'), [{'version':1}])
        database.init_db()
        self.assertEqual(self.rows('inbounds'), before_nodes)
        # Revocation preserved; only an explicit rotation can reissue a link
        # for the same proved instances. Legacy grant remains unrotatable.
        listed = {value['id']:value for value in list_grants(1)}
        self.assertTrue(listed[created['id']]['revoked']); self.assertFalse(listed[created['id']]['invalidated'])
        self.assertTrue(listed[1]['invalidated'])
        proxy_recovery._database(self.data, revoke=True)
        self.assertEqual(self.rows('inbounds'), before_nodes)
        self.assertEqual(self.rows('subscription_grants'), expected)

    def test_mixed_and_old_backup_rows_never_rebind_new_grants(self):
        self.legacy(); database.init_db()
        created, _ = create_grant(1, 'current binding', 'vpn.example.test', [2], ['raw'], 90)
        bound = self.rows('subscription_grants')[-1]['inbound_bindings']
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE inbounds SET instance_id=? WHERE id=2', ('e'*32,))
        database.init_db()
        self.assertEqual(self.rows('subscription_grants')[-1]['inbound_bindings'], bound)
        listed = {value['id']:value for value in list_grants(1)}
        self.assertEqual(listed[created['id']]['invalidation_reason'], 'node_identity_changed')
        self.assertEqual(listed[1]['invalidation_reason'], 'node_identity_missing')


if __name__ == '__main__': unittest.main()
