"""Offline recovery evidence: fake credentials and private temporary files only.

Real policy/compiler hashes are used; sockets, DNS and core execution are denied.
These tests do not establish runtime or network qualification.
"""
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import release_tools as release
from app.certificates.manager import CertificateManager
from app.data_owner import DataOwnerError, data_owner
from app.models import database
from app.services.core_manager import SingBoxAdapter
from app.services.core_runtime import CoreError, CoreRuntime, encode, revision
from app.services.proxy_policy import VerifiedEndpoint, compile_proxy_policy
from app.services import proxy_recovery as recovery, routing_store
from app.services.proxy_store import ProxyStore, ProxyStorageError


FAKE_UUID = '11111111-1111-4111-8111-111111111111'
FAKE_PASSWORD = 'fake-local-password-for-offline-tests'
FLAGS = ('local_proxy_enabled', 'relay_enabled')


class ProxyRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        old_umask = os.umask(0o077)
        self.addCleanup(os.umask, old_umask)
        self.top = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='vui-offline-recovery-')))
        # Enforce the offline promise, including accidental future regressions.
        for target in ('socket.socket', 'socket.create_connection', 'socket.getaddrinfo', 'subprocess.Popen'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('Offline recovery attempted network or execution')))
        for method in ('check', 'apply', 'restore', 'recover', '_launch'):
            self.stack.enter_context(patch.object(CoreRuntime, method,
                side_effect=AssertionError('Offline recovery attempted core operation')))
        self.fixture('initial')

    def fixture(self, name):
        self.root = self.top / name
        self.root.mkdir(mode=0o700)
        self.data = self.root / 'data'
        self.data.mkdir(mode=0o700)
        self.archive = self.root / 'backup.zip'
        self.row = SimpleNamespace(id=1, routing_mode='inherit', user_id=1, core='sing-box', up=0, down=0,
            total=0, remark='preserved fake inbound', enable=True, expiry_time=0,
            port=24443, protocol='vless', tag='fake-inbound',
            settings={'users': [{'uuid': FAKE_UUID}]},
            stream_settings={'tls': {'enabled': True, 'server_name': 'node.example',
                'certificate_path': '/test-only-fake-cert', 'key_path': '/test-only-fake-key'}})
        with sqlite3.connect(self.data / 'v-ui.db') as db:
            db.executescript('''
                CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT, is_active INTEGER);
                CREATE TABLE inbounds(id INTEGER PRIMARY KEY, user_id INTEGER, core TEXT, up INTEGER,
                    down INTEGER, total INTEGER, remark TEXT, enable INTEGER, expiry_time INTEGER,
                    port INTEGER, protocol TEXT, settings TEXT, stream_settings TEXT, tag TEXT);
                CREATE TABLE admin_sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER);
                CREATE TABLE subscription_grants(token_hash TEXT PRIMARY KEY, revoked INTEGER);
                INSERT INTO users VALUES(1, 'fake-admin', 'fake-hash-not-a-credential', 1);
                INSERT INTO admin_sessions VALUES('fake-session-hash', 1);
                INSERT INTO subscription_grants VALUES('fake-live-grant', 0);
                INSERT INTO subscription_grants VALUES('fake-revoked-grant', 1);
            ''')
            db.execute('INSERT INTO inbounds VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (self.row.id, self.row.user_id, self.row.core, self.row.up, self.row.down,
                 self.row.total, self.row.remark, int(self.row.enable), self.row.expiry_time,
                 self.row.port, self.row.protocol, json.dumps(self.row.settings),
                 json.dumps(self.row.stream_settings), self.row.tag))
        (self.data / 'v-ui.db').chmod(0o600)
        self.store = ProxyStore(self.data)
        first = self.store.read_snapshot()
        saved = self.store.save_upstream({'name': 'Fake upstream', 'server': 'upstream.example',
            'server_port': 443, 'uuid': FAKE_UUID,
            'tls': {'mode': 'tls', 'server_name': 'upstream.example'}}, first['revision'])
        self.upstream_id = saved['upstream_id']
        settings = self.settings(saved['snapshot'])
        settings.update(selected_upstream_id=self.upstream_id, local_username='fake-user')
        self.store.save_draft(settings, saved['snapshot']['revision'], local_password=FAKE_PASSWORD)

    @staticmethod
    def settings(snapshot):
        return {key: value for key, value in snapshot['settings'].items() if key != 'local_password_set'}

    def draft(self, local, relay):
        snapshot = self.store.read_snapshot()
        return self.store.save_draft({**self.settings(snapshot), 'local_proxy_enabled': local,
                                     'relay_enabled': relay}, snapshot['revision'])

    def compiled(self, snapshot, previous=None, previous_enabled=False, *, kind='apply'):
        endpoint = None
        addresses = []
        if any(snapshot['settings'][key] for key in FLAGS):
            endpoint = VerifiedEndpoint(self.upstream_id, snapshot['revision'],
                                        'upstream.example', ('8.8.4.4',), 1)
            addresses = ['127.0.0.1', '192.168.8.2']
        compiled = compile_proxy_policy(SingBoxAdapter().build_config([self.row]), [self.row],
            snapshot, endpoint=endpoint, self_addresses=addresses)
        context = {'schema_version': 1, 'kind': kind, 'candidate_core_revision': revision(compiled.config),
            'previous_core_revision': previous, 'previous_enabled': previous_enabled,
            'policy_identity': compiled.identity, 'self_addresses': addresses,
            'endpoint': None if endpoint is None else {**vars(endpoint), 'addresses': list(endpoint.addresses)}}
        return compiled.config, context

    def runtime(self, config, *, enabled=True, core='sing-box'):
        directory = self.data / 'runtime' / core
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw = encode(config)
        digest = release.digest(raw)
        (directory / (digest + '.json')).write_bytes(raw)
        (directory / (digest + '.json')).chmod(0o600)
        release.atomic_json(directory / 'state.json', {'revision': digest, 'enabled': enabled})
        return digest

    def commit(self, local=True, relay=True, transaction='committed'):
        snapshot = self.draft(local, relay)
        active = self.store.load_active()
        previous = None if active is None else active['core_revision']
        config, context = self.compiled(self.store.load_draft(), previous, previous is not None)
        self.store.begin_apply(snapshot['revision'], transaction, runtime_context=context)
        self.runtime(config)
        self.store.commit_apply(transaction, context['candidate_core_revision'])
        return self.store.load_active()

    def pending(self, local=False, relay=False, *, transaction='pending', kind='apply'):
        active = self.store.load_active()
        previous = None if active is None else active['core_revision']
        if kind == 'apply':
            self.draft(local, relay)
            snapshot = self.store.load_draft()
        else:
            snapshot = active
        config, context = self.compiled(snapshot, previous, previous is not None, kind=kind)
        if kind == 'apply':
            self.store.begin_apply(snapshot['revision'], transaction, runtime_context=context)
        else:
            self.store.begin_reconcile(snapshot['revision'], transaction,
                expected_core_revision=previous, runtime_context=context)
        self.runtime(config)
        return config, context

    def backup(self):
        return release.backup(self.root, self.archive)

    @staticmethod
    def files(data):
        return {path.relative_to(data).as_posix(): (path.read_bytes(), path.stat().st_mode & 0o777)
                for path in data.rglob('*') if path.is_file()
                and path.relative_to(data).as_posix() not in release._RECOVERY_LOCKS}

    def immutable(self):
        return {path.relative_to(self.data).as_posix(): path.read_bytes()
                for name in ('secrets', 'revisions') for path in (self.store.path / name).glob('*.json')}

    def preview_restore(self, sha):
        return recovery.preview(self.root, 'restore', archive=self.archive, archive_sha256=sha)

    def execute_restore(self, sha, plan):
        return recovery.execute(self.root, 'restore', plan['plan_sha256'],
                                archive=self.archive, archive_sha256=sha)

    def assert_no_change(self, action):
        before = self.files(self.data)
        inode = self.data.stat().st_ino
        with self.assertRaises(release.ReleaseError):
            action()
        self.assertEqual(self.files(self.data), before)
        self.assertEqual(self.data.stat().st_ino, inode)
        self.assertFalse((self.root / 'RESTORE_PENDING.json').exists())

    def assert_access_revoked(self):
        with sqlite3.connect(self.data / 'v-ui.db') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM admin_sessions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT revoked FROM subscription_grants ORDER BY token_hash').fetchall(), [(1,), (1,)])
            self.assertEqual(db.execute('SELECT remark FROM inbounds').fetchone()[0], self.row.remark)
            self.assertEqual(db.execute('SELECT username FROM users').fetchone()[0], 'fake-admin')

    def test_dangling_journal_blocks_new_operations_and_is_never_overwritten(self):
        self.commit(True, True)
        checksum = self.backup()
        plan = self.preview_restore(checksum)
        journal = self.root / 'RESTORE_PENDING.json'
        missing = self.root / 'missing-journal-target'
        before = self.files(self.data)
        inode = self.data.stat().st_ino
        with release.stopped(self.root) as guard:
            journal.symlink_to(missing)
            with self.assertRaises(release.ReleaseError):
                guard._check()
        for operation in (
                lambda: self.preview_restore(checksum),
                lambda: self.execute_restore(checksum, plan),
                lambda: release.restore(self.root, self.archive, checksum),
                lambda: release.backup(self.root, self.root / 'refused-backup.zip'),
                lambda: release.recover_restore(self.root)):
            with self.subTest(operation=operation), self.assertRaises((release.ReleaseError, OSError)):
                operation()
            self.assertEqual(self.files(self.data), before)
            self.assertEqual(self.data.stat().st_ino, inode)
            self.assertTrue(journal.is_symlink())
            self.assertEqual(journal.readlink(), missing)
            self.assertFalse(missing.exists())
        self.assertFalse((self.root / 'refused-backup.zip').exists())

    def test_four_committed_states_restore_exact_active_draft_and_secret_history(self):
        for index, flags in enumerate(((False, False), (True, False), (False, True), (True, True))):
            with self.subTest(flags=flags):
                self.fixture('four-' + str(index))
                active = self.commit(*flags)
                self.draft(not flags[0], not flags[1])
                draft = self.store.load_draft()
                snapshot = self.store.read_snapshot()
                objects = self.immutable()
                sha = self.backup()
                # Unknown current metadata can be replaced only by the explicitly selected archive.
                (self.store.path / 'state.json').write_bytes(b'{"unknown-policy":true}')
                before = self.files(self.data)
                plan = self.preview_restore(sha)
                self.assertEqual(self.files(self.data), before)
                self.assertEqual(plan, self.preview_restore(sha))
                self.assertEqual(plan['policy']['active_switches'], dict(zip(FLAGS, flags)))
                self.assertEqual(plan['policy']['draft_switches'], dict(zip(FLAGS, (not flags[0], not flags[1]))))
                self.assertFalse(plan['starts_core'])
                self.assertTrue(plan['revokes_restored_access'])
                for private in (FAKE_UUID, FAKE_PASSWORD, self.upstream_id, '8.8.4.4', '192.168.8.2', 'upstream.example'):
                    self.assertNotIn(private, json.dumps(plan))
                result = self.execute_restore(sha, plan)
                self.assertTrue(result['completed'])
                self.assertEqual(self.store.load_active(), active)
                self.assertEqual(self.store.load_draft(), draft)
                self.assertEqual(self.store.read_snapshot(), snapshot)
                self.assertEqual(self.immutable(), objects)
                self.assertEqual(self.files(self.root / result['retained_predecessor']), before)
                self.assert_access_revoked()
                self.assertFalse((self.root / 'RESTORE_PENDING.json').exists())

    def test_stopped_on_backup_retains_committed_switches_hash_and_disabled_runtime(self):
        active = self.commit(True, True)
        # The persisted result of an explicit stop retains the last committed config and policy.
        # Do not call CoreRuntime.stop or launch a real process in this offline suite.
        release.atomic_json(self.data / 'runtime/sing-box/state.json',
                            {'revision': active['core_revision'], 'enabled': False})
        sha = self.backup()
        plan = self.preview_restore(sha)
        self.assertFalse(plan['policy']['core_enabled'])
        self.assertEqual(plan['policy']['active_switches'], dict(zip(FLAGS, (True, True))))
        self.assertEqual(plan['policy']['core_revision'], active['core_revision'])
        self.assertEqual(self.preview_restore(sha), plan)
        result = self.execute_restore(sha, plan)
        self.assertTrue(result['completed'])
        self.assertEqual(self.store.load_active(), active)
        self.assertEqual(json.loads((self.data / 'runtime/sing-box/state.json').read_text()),
                         {'revision': active['core_revision'], 'enabled': False})
        self.assert_access_revoked()

    def test_plan_source_archive_and_argument_digests_must_match_exact_preview(self):
        self.commit()
        sha = self.backup()
        plan = self.preview_restore(sha)
        self.assert_no_change(lambda: recovery.execute(self.root, 'restore', '0' * 64,
            archive=self.archive, archive_sha256=sha))
        self.assert_no_change(lambda: recovery.execute(self.root, 'restore', plan['plan_sha256'],
            archive=self.archive, archive_sha256='0' * 64))
        self.assert_no_change(lambda: recovery.execute(self.root, 'reconcile', plan['plan_sha256']))
        note = self.data / 'preserved-note.txt'
        note.write_text('a new source byte makes prior approval stale')
        self.assert_no_change(lambda: self.execute_restore(sha, plan))
        note.unlink()
        original = self.archive.read_bytes()
        self.archive.write_bytes(original + b'changed archive bytes')
        self.assert_no_change(lambda: self.execute_restore(sha, plan))
        self.archive.write_bytes(original)
        # A newly accepted source requires its own new plan, never the old approval.
        note.write_text('different current evidence')
        self.assertNotEqual(self.preview_restore(sha)['plan_sha256'], plan['plan_sha256'])

    def test_reconcile_aborts_to_recorded_active_and_preserves_newer_draft_and_history(self):
        active = self.commit(True, True)
        self.pending(False, False)
        draft = self.store.load_draft()
        objects = self.immutable()
        before = self.files(self.data)
        plan = recovery.preview(self.root, 'reconcile')
        self.assertEqual(self.files(self.data), before)
        self.assertEqual(plan['transaction_id'], 'pending')
        self.assertEqual(plan['policy']['active_switches'], dict(zip(FLAGS, (True, True))))
        self.assertEqual(plan['policy']['draft_switches'], dict(zip(FLAGS, (False, False))))
        result = recovery.execute(self.root, 'reconcile', plan['plan_sha256'])
        self.assertEqual(self.store.load_active(), active)
        self.assertEqual(self.store.load_draft(), {**draft, 'pending': None})
        self.assertEqual(self.immutable(), objects)
        self.assertEqual(self.store.transaction('pending')['phase'], 'aborted')
        self.assertIsNone(self.store.recoverable_pending())
        self.assertEqual(json.loads((self.data / 'runtime/sing-box/state.json').read_text()),
                         {'revision': active['core_revision'], 'enabled': True})
        self.assertEqual(self.files(self.root / result['retained_predecessor']), before)
        self.assertFalse(result['revokes_restored_access'])
        with sqlite3.connect(self.data / 'v-ui.db') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM admin_sessions').fetchone()[0], 1)

    def test_first_apply_reconciliation_keeps_unapplied_draft_and_has_no_active_defaults(self):
        self.pending(True, True)
        draft = self.store.load_draft()
        objects = self.immutable()
        plan = recovery.preview(self.root, 'reconcile')
        self.assertIsNone(plan['policy']['active_revision'])
        self.assertIsNone(plan['policy']['active_switches'])
        self.assertIsNone(plan['policy']['core_revision'])
        self.assertFalse(plan['policy']['core_enabled'])
        recovery.execute(self.root, 'reconcile', plan['plan_sha256'])
        self.assertIsNone(self.store.load_active())
        self.assertEqual(self.store.load_draft(), {**draft, 'pending': None})
        self.assertEqual(self.immutable(), objects)
        self.assertFalse((self.data / 'runtime/sing-box/state.json').exists())
        self.assertEqual(self.store.transaction('pending')['phase'], 'aborted')

    def test_pending_active_only_reconcile_preserves_unpublished_draft(self):
        active = self.commit(True, True)
        self.draft(False, False)
        draft = self.store.load_draft()
        self.pending(kind='reconcile')
        plan = recovery.preview(self.root, 'reconcile')
        recovery.execute(self.root, 'reconcile', plan['plan_sha256'])
        self.assertEqual(self.store.load_active(), active)
        self.assertEqual(self.store.load_draft(), draft)
        self.assertEqual(self.store.transaction('pending')['phase'], 'aborted')

    def mutate_state(self, mutate):
        path = self.store.path / 'state.json'
        value = json.loads(path.read_text())
        mutate(value)
        path.write_bytes(encode(value))

    def test_reconcile_refuses_unknown_incomplete_or_mismatched_evidence_without_guessing(self):
        cases = ('write-intent', 'unknown-field', 'invalid-phase', 'missing-context',
                 'missing-secret', 'changed-baseline', 'missing-baseline', 'changed-inbound',
                 'unrelated-current-core', 'invalid-current-state')
        for case in cases:
            with self.subTest(case=case):
                self.fixture(case)
                active = self.commit()
                self.pending()
                baseline = self.data / 'runtime/sing-box' / (active['core_revision'] + '.json')
                if case == 'write-intent':
                    (self.store.path / 'write-intent.json').write_text('{"interrupted":true}')
                elif case == 'unknown-field':
                    self.mutate_state(lambda state: state.update(unrecognized=True))
                elif case == 'invalid-phase':
                    self.mutate_state(lambda state: state['pending'].update(phase='unknown'))
                elif case == 'missing-context':
                    self.mutate_state(lambda state: state['pending'].pop('runtime_context'))
                elif case == 'missing-secret':
                    next((self.store.path / 'secrets').glob('*.json')).unlink()
                elif case == 'changed-baseline':
                    baseline.write_bytes(b'{"changed":true}')
                elif case == 'missing-baseline':
                    baseline.unlink()
                elif case == 'changed-inbound':
                    with sqlite3.connect(self.data / 'v-ui.db') as db:
                        db.execute('UPDATE inbounds SET port=24444')
                elif case == 'unrelated-current-core':
                    self.runtime({'inbounds': [], 'outbounds': [{'type': 'direct'}]})
                else:
                    (self.data / 'runtime/sing-box/state.json').write_bytes(b'{}')
                self.assert_no_change(lambda: recovery.preview(self.root, 'reconcile'))

    def crafted_archive(self, sha, mutate):
        work = self.root / 'crafted'
        meta = release.unpack_verified(self.archive, sha, work)
        (work / release.MANIFEST).unlink()
        mutate(work)
        meta = {key: value for key, value in meta.items() if key not in ('schema', 'files')}
        target = self.root / 'crafted.zip'
        checksum = release.create_archive(work, target, meta)
        return target, checksum

    def test_restore_refuses_invalid_archive_policy_provenance_and_runtime(self):
        cases = ('missing-state', 'unknown-state', 'write-intent', 'missing-secret',
                 'changed-core', 'missing-provenance', 'unqualified-inbound', 'xray-enabled',
                 'pending-apply')
        for case in cases:
            with self.subTest(case=case):
                self.fixture('archive-' + case)
                active = self.commit()
                sha = self.backup()
                def mutate(work):
                    path = work / 'proxy-state/state.json'
                    if case == 'missing-state': path.unlink()
                    elif case == 'unknown-state': path.write_bytes(b'{}')
                    elif case == 'write-intent': (work / 'proxy-state/write-intent.json').write_bytes(b'{}')
                    elif case == 'missing-secret': next((work / 'proxy-state/secrets').glob('*.json')).unlink()
                    elif case == 'changed-core':
                        (work / 'runtime/sing-box' / (active['core_revision'] + '.json')).write_bytes(b'{}')
                    elif case == 'missing-provenance':
                        value = json.loads(path.read_text())
                        value['transactions']['committed'].pop('runtime_context')
                        path.write_bytes(encode(value))
                    elif case == 'unqualified-inbound':
                        with sqlite3.connect(work / 'v-ui.db') as db:
                            db.execute("UPDATE inbounds SET core='xray'")
                    elif case == 'xray-enabled':
                        directory = work / 'runtime/xray'
                        directory.mkdir(mode=0o700)
                        raw = encode({'inbounds': [], 'outbounds': []})
                        digest = release.digest(raw)
                        (directory / (digest + '.json')).write_bytes(raw)
                        release.atomic_json(directory / 'state.json', {'revision': digest, 'enabled': True})
                    else:
                        # Use a complete actual pending transaction rather than invalid JSON.
                        for directory in work.rglob('*'):
                            if directory.is_dir(): directory.chmod(0o700)
                        store = ProxyStore(work)
                        context = copy.deepcopy(active['runtime_context'])
                        context.update(previous_core_revision=active['core_revision'], previous_enabled=True)
                        store.begin_apply(active['revision'], 'archive-pending', runtime_context=context)
                        (work / '.proxy-state.lock').unlink()
                archive, checksum = self.crafted_archive(sha, mutate)
                self.assert_no_change(lambda: recovery.preview(self.root, 'restore',
                    archive=archive, archive_sha256=checksum))

    def test_restore_refuses_a_backup_for_another_data_path(self):
        self.commit()
        sha = self.backup()
        work = self.root / 'other-path-payload'
        meta = release.unpack_verified(self.archive, sha, work)
        (work / release.MANIFEST).unlink()
        meta = {key: value for key, value in meta.items() if key not in ('schema', 'files')}
        meta['data_path'] = str(self.root / 'different-data')
        archive = self.root / 'other-path.zip'
        checksum = release.create_archive(work, archive, meta)
        self.assert_no_change(lambda: recovery.preview(self.root, 'restore',
            archive=archive, archive_sha256=checksum))

    def test_source_symlink_hardlink_and_nonprivate_permissions_are_not_repaired(self):
        for case in ('symlink', 'hardlink', 'public-file', 'public-directory'):
            with self.subTest(case=case):
                self.fixture('source-' + case)
                self.commit()
                sha = self.backup()
                target = self.data / 'unsafe'
                external = self.root / 'external'
                external.write_bytes(b'never follow or modify this')
                if case == 'symlink': target.symlink_to(external)
                elif case == 'hardlink': os.link(external, target)
                elif case == 'public-file': target.write_bytes(b'private bytes'); target.chmod(0o644)
                else:
                    target.mkdir(mode=0o700)
                    target.chmod(0o755)
                info = target.lstat()
                with self.assertRaises(release.ReleaseError): self.preview_restore(sha)
                self.assertEqual(target.lstat().st_ino, info.st_ino)
                self.assertEqual(target.lstat().st_mode, info.st_mode)
                self.assertEqual(external.read_bytes(), b'never follow or modify this')
                self.assertFalse((self.root / 'RESTORE_PENDING.json').exists())

    def test_inbound_row_byte_bounds_and_blob_types_reject_before_json_decoding(self):
        cases = ('oversized-settings-null-stream', 'null-settings-oversized-stream',
                 'multibyte-json', 'blob-settings', 'blob-stream')
        for case in cases:
            with self.subTest(case=case):
                if case in ('oversized-settings-null-stream', 'null-settings-oversized-stream'):
                    oversized = '{"padding":"' + 'x' * (17 * 1024 * 1024) + '"}'
                    settings, stream = (oversized, None) if case.startswith('oversized') else (None, oversized)
                elif case == 'multibyte-json':
                    settings = '{"padding":"' + '中' * (6 * 1024 * 1024) + '"}'
                    self.assertLess(len(settings), 16 * 1024 * 1024)
                    self.assertGreater(len(settings.encode('utf-8')), 16 * 1024 * 1024)
                    stream = None
                elif case == 'blob-settings':
                    settings, stream = sqlite3.Binary(b'\xff{malformed-blob-json'), None
                else:
                    settings, stream = None, sqlite3.Binary(b'\xff{malformed-blob-json')
                with sqlite3.connect(self.data / 'v-ui.db') as db:
                    db.execute('UPDATE inbounds SET settings=?, stream_settings=?', (settings, stream))
                with patch.object(recovery.json, 'loads',
                                  side_effect=AssertionError('Invalid or oversized row reached JSON decoding')) as decode:
                    with self.assertRaises(recovery.ProxyRecoveryError): recovery._rows(self.data)
                decode.assert_not_called()

    def test_large_source_metadata_is_bounded_before_policy_materialization(self):
        with sqlite3.connect(self.data / 'v-ui.db') as db:
            db.execute('UPDATE inbounds SET remark=?', ('x' * (17 * 1024 * 1024),))
        with patch.object(recovery.json, 'loads', side_effect=AssertionError('Oversized source was materialized')):
            with self.assertRaises(recovery.ProxyRecoveryError):
                recovery._rows(self.data)

    @staticmethod
    def staged_entries(work):
        entries = {}
        for path in (work, *work.rglob('*')):
            info = path.stat()
            entries[(info.st_dev, info.st_ino)] = (path.relative_to(work).as_posix(), path.is_dir())
        return entries

    def test_execute_syncs_every_staged_file_and_directory_bottom_up_before_journal(self):
        self.commit()
        sha = self.backup()
        plan = self.preview_restore(sha)
        original_stage, original_fsync = recovery._sync_stage, os.fsync
        original_json = release.atomic_json
        state = {'entries': {}, 'syncing': False, 'complete': False}
        synced = []
        def stage(work):
            state.update(entries=self.staged_entries(work), syncing=True)
            try:
                result = original_stage(work)
            finally:
                state['syncing'] = False
            state['complete'] = True
            return result
        def fsync(fd):
            result = original_fsync(fd)
            if state['syncing']:
                info = os.fstat(fd)
                entry = state['entries'].get((info.st_dev, info.st_ino))
                if entry is not None: synced.append(entry)
            return result
        def atomic_json(path, value):
            if path == self.root / 'RESTORE_PENDING.json':
                self.assertTrue(state['complete'])
                self.assertEqual(set(synced), set(state['entries'].values()))
                positions = {name: index for index, (name, _) in enumerate(synced)}
                for name, is_directory in synced:
                    if name != '.':
                        parent = Path(name).parent.as_posix()
                        self.assertLess(positions[name], positions[parent],
                                        'directory must be durable after all its children')
                self.assertTrue(any(name.startswith('proxy-state/secrets/') for name, _ in synced))
                self.assertTrue(any(name.startswith('proxy-state/revisions/') for name, _ in synced))
                self.assertTrue(any(name.startswith('runtime/sing-box/') and name.endswith('.json')
                                    for name, _ in synced))
            return original_json(path, value)
        with patch.object(recovery, '_sync_stage', side_effect=stage), \
             patch.object(os, 'fsync', side_effect=fsync), \
             patch.object(release, 'atomic_json', side_effect=atomic_json):
            result = self.execute_restore(sha, plan)
        self.assertTrue(result['completed'])
        self.assertTrue(state['complete'])
        self.assertEqual(set(synced), set(state['entries'].values()))

    def test_each_staged_sync_failure_keeps_source_unchanged_without_publishing_journal(self):
        self.commit()
        sha = self.backup()
        plan = self.preview_restore(sha)
        original_stage, original_fsync = recovery._sync_stage, os.fsync
        categories = ('secret', 'revision', 'runtime-config', 'runtime-state', 'database',
                      'secret-directory', 'revision-directory', 'runtime-directory', 'root-directory')
        for category in categories:
            with self.subTest(category=category):
                state = {'entries': {}, 'syncing': False}
                fired = []
                def stage(work):
                    state.update(entries=self.staged_entries(work), syncing=True)
                    try: return original_stage(work)
                    finally: state['syncing'] = False
                def fsync(fd):
                    result = original_fsync(fd)
                    if not state['syncing']: return result
                    info = os.fstat(fd)
                    entry = state['entries'].get((info.st_dev, info.st_ino))
                    if entry is None: return result
                    name, is_directory = entry
                    selected = {
                        'secret': not is_directory and name.startswith('proxy-state/secrets/'),
                        'revision': not is_directory and name.startswith('proxy-state/revisions/'),
                        'runtime-config': not is_directory and name.startswith('runtime/sing-box/')
                                          and name.endswith('.json') and not name.endswith('/state.json'),
                        'runtime-state': name == 'runtime/sing-box/state.json',
                        'database': name == 'v-ui.db',
                        'secret-directory': is_directory and name == 'proxy-state/secrets',
                        'revision-directory': is_directory and name == 'proxy-state/revisions',
                        'runtime-directory': is_directory and name == 'runtime/sing-box',
                        'root-directory': is_directory and name == '.',
                    }[category]
                    if selected:
                        fired.append(name)
                        raise OSError('injected staged durability failure')
                    return result
                with patch.object(recovery, '_sync_stage', side_effect=stage), \
                     patch.object(os, 'fsync', side_effect=fsync):
                    self.assert_no_change(lambda: self.execute_restore(sha, plan))
                self.assertEqual(len(fired), 1, 'fault must reach the selected staged category')
                self.assertFalse(list(self.root.glob('.proxy-recovery-*')))
                self.assertFalse(list((self.root / 'recovery').glob('before-*')))

    def test_staged_bytes_changed_after_fsync_are_rejected_before_journal_or_swap(self):
        self.commit()
        sha = self.backup()
        plan = self.preview_restore(sha)
        original_stage = recovery._sync_stage
        changed = []
        def stage(work):
            original_stage(work)
            path = next((work / 'proxy-state/secrets').glob('*.json'))
            path.write_bytes(b'{"changed-after-flush":true}')
            changed.append(path.name)
        with patch.object(recovery, '_sync_stage', side_effect=stage):
            self.assert_no_change(lambda: self.execute_restore(sha, plan))
        self.assertEqual(len(changed), 1)

    def test_each_write_swap_fsync_validation_and_clear_failure_has_exact_recovery_boundary(self):
        stages = ('journal-file-fsync', 'journal-directory-fsync', 'journal', 'old-swap',
                  'old-recovery-fsync', 'old-root-fsync', 'new-swap', 'new-root-fsync',
                  'target-digest', 'target-policy', 'predecessor-digest', 'journal-unlink',
                  'journal-cleared', 'final-root-fsync')
        for stage in stages:
            with self.subTest(stage=stage):
                self.fixture('fault-' + stage)
                self.commit()
                sha = self.backup()
                (self.store.path / 'state.json').write_bytes(b'{"retained-corruption":true}')
                before = self.files(self.data)
                plan = self.preview_restore(sha)
                journal = self.root / 'RESTORE_PENDING.json'
                original_json, original_replace = release.atomic_json, os.replace
                original_digest, original_validate = release.recovery_tree_digest, recovery._validate
                original_unlink, original_fsync = Path.unlink, os.fsync
                original_sync_directory = release.sync_directory
                state = {'journal_started': False, 'journal_write': False, 'journal_fsyncs': 0,
                         'root_fsyncs': 0}
                fired = []
                def fail():
                    fired.append(stage)
                    raise OSError('injected offline recovery stage failure')
                def atomic_json(path, value):
                    if path != journal:
                        return original_json(path, value)
                    state.update(journal_started=True, journal_write=True)
                    try:
                        result = original_json(path, value)
                    finally:
                        state['journal_write'] = False
                    if stage == 'journal': fail()
                    return result
                def fsync(fd):
                    result = original_fsync(fd)
                    if state['journal_write']:
                        state['journal_fsyncs'] += 1
                        if stage == 'journal-file-fsync' and state['journal_fsyncs'] == 1: fail()
                        if stage == 'journal-directory-fsync' and state['journal_fsyncs'] == 2: fail()
                    return result
                def sync_directory(path):
                    result = original_sync_directory(path)
                    if state['journal_started']:
                        if stage == 'old-recovery-fsync' and path == self.root / 'recovery': fail()
                        if path == self.root:
                            state['root_fsyncs'] += 1
                            expected = {'old-root-fsync': 1, 'new-root-fsync': 2, 'final-root-fsync': 3}
                            if state['root_fsyncs'] == expected.get(stage): fail()
                    return result
                def replace(source, destination, *args, **kwargs):
                    result = original_replace(source, destination, *args, **kwargs)
                    if not args and not kwargs:
                        if stage == 'old-swap' and Path(source) == self.data: fail()
                        if stage == 'new-swap' and Path(destination) == self.data and journal.exists(): fail()
                    return result
                def digest(path, **kwargs):
                    result = original_digest(path, **kwargs)
                    if journal.exists():
                        if stage == 'target-digest' and path == self.data: fail()
                        if stage == 'predecessor-digest' and path.parent == self.root / 'recovery': fail()
                    return result
                def validate(path):
                    result = original_validate(path)
                    if stage == 'target-policy' and path == self.data and journal.exists(): fail()
                    return result
                def unlink(path, *args, **kwargs):
                    if stage == 'journal-unlink' and path == journal: fail()
                    result = original_unlink(path, *args, **kwargs)
                    if stage == 'journal-cleared' and path == journal: fail()
                    return result
                with patch.object(release, 'atomic_json', side_effect=atomic_json), \
                     patch.object(os, 'replace', side_effect=replace), \
                     patch.object(os, 'fsync', side_effect=fsync), \
                     patch.object(release, 'sync_directory', side_effect=sync_directory), \
                     patch.object(release, 'recovery_tree_digest', side_effect=digest), \
                     patch.object(recovery, '_validate', side_effect=validate), \
                     patch.object(Path, 'unlink', unlink):
                    with self.assertRaises(release.ReleaseError): self.execute_restore(sha, plan)
                self.assertEqual(fired, [stage])
                if stage == 'journal-file-fsync':
                    self.assertFalse(journal.exists())
                    self.assertEqual(self.files(self.data), before)
                    self.assertFalse(list((self.root / 'recovery').glob('before-*')))
                    continue
                if stage in ('journal-cleared', 'final-root-fsync'):
                    # A post-commit I/O error cannot recreate a deleted journal or undo a validated target.
                    self.assertFalse(journal.exists())
                    self.assertEqual(release.recovery_tree_digest(self.data), plan['target_sha256'])
                    self.assertEqual(recovery._validate(self.data), plan['policy'])
                    retained = list((self.root / 'recovery').glob('before-*'))
                    self.assertEqual(len(retained), 1)
                    self.assertEqual(self.files(retained[0]), before)
                    release.recover_restore(self.root)
                    self.assertEqual(release.recovery_tree_digest(self.data), plan['target_sha256'])
                    continue
                self.assertTrue(journal.is_file())
                record = json.loads(journal.read_text())
                self.assertEqual(record['source_sha256'], plan['source_sha256'])
                self.assertEqual(record['target_sha256'], plan['target_sha256'])
                self.assertEqual(record['plan_sha256'], plan['plan_sha256'])
                with self.assertRaises(release.ReleaseError): self.preview_restore(sha)
                release.recover_restore(self.root)
                self.assertEqual(self.files(self.data), before)
                self.assertFalse(journal.exists())
                # Recovery of a completed rollback is idempotent and must not initialize policy.
                release.recover_restore(self.root)
                self.assertEqual(self.files(self.data), before)

    def test_managed_recovery_preserves_exact_predecessor_without_creating_missing_writer_directories(self):
        for interrupted_swap in ('old', 'new'):
            with self.subTest(interrupted_swap=interrupted_swap):
                self.fixture('managed-recovery-' + interrupted_swap)
                self.commit()
                self.assertFalse((self.data / 'runtime/xray').exists())
                self.assertFalse((self.data / 'certificates').exists())
                sha = self.backup()
                before = self.files(self.data)
                before_digest = release.recovery_tree_digest(self.data)
                plan = self.preview_restore(sha)
                original_replace = os.replace
                def replace(source, destination, *args, **kwargs):
                    result = original_replace(source, destination, *args, **kwargs)
                    if not args and not kwargs:
                        if interrupted_swap == 'old' and Path(source) == self.data:
                            raise OSError('injected immediately after retaining predecessor')
                        if interrupted_swap == 'new' and Path(destination) == self.data:
                            raise OSError('injected immediately after installing target')
                    return result
                with patch.object(os, 'replace', side_effect=replace):
                    with self.assertRaises(release.ReleaseError): self.execute_restore(sha, plan)
                journal = self.root / 'RESTORE_PENDING.json'
                record = json.loads(journal.read_text())
                self.assertEqual(record['schema_version'], 2)
                self.assertEqual(record['kind'], 'proxy-recovery')
                retained = self.root / record['old']
                self.assertEqual(release.recovery_tree_digest(retained), before_digest)
                self.assertFalse((retained / 'runtime/xray').exists())
                self.assertFalse((retained / 'certificates').exists())
                session = release._MaintenanceSession(self.root)
                # Only environment/installation provenance is synthetic. The managed session,
                # filesystem ownership, journal, directory locks and swaps below are real.
                with patch.object(release, 'supported_environment', return_value='x86_64-gnu'), \
                     patch.object(release, 'active', return_value=(self.root / 'releases/fake',
                         {'release_id': 'fake', 'previous_id': None})) as active, \
                     patch.object(release, '_maintenance_anchor', return_value=('fake-anchor',)) as anchor:
                    try:
                        session.prepare('recover-restore')
                        self.assertEqual(release.recovery_tree_digest(retained), before_digest)
                        self.assertEqual(session.execute(), {})
                        self.assertFalse(journal.exists())
                        self.assertEqual(self.files(self.data), before)
                        self.assertEqual(release.recovery_tree_digest(self.data), before_digest)
                        self.assertFalse((self.data / 'runtime/xray').exists())
                        self.assertFalse((self.data / 'certificates').exists())
                        active.assert_called_once_with(self.root)
                        anchor.assert_called_once_with(self.root)
                        outcomes = []
                        def attempt_shared_writer():
                            try:
                                with data_owner(self.data): outcomes.append('opened')
                            except DataOwnerError:
                                outcomes.append('blocked')
                        def check_writer(expected):
                            thread = threading.Thread(target=attempt_shared_writer)
                            thread.start(); thread.join(timeout=5)
                            self.assertFalse(thread.is_alive(), 'shared writer must refuse promptly')
                            self.assertEqual(outcomes[-1:], [expected])
                        # A distinct thread cannot borrow this session's intentional EX-to-SH validator proof.
                        check_writer('blocked')
                        self.assertFalse(journal.exists())
                        session.release_writers()
                        check_writer('opened')
                        self.assertEqual(outcomes, ['blocked', 'opened'])
                    finally:
                        session.close()
                self.assertEqual(self.files(self.data), before)
                self.assertFalse(journal.exists())

    def test_recover_restore_refuses_changed_predecessor_and_keeps_journal(self):
        self.commit()
        sha = self.backup()
        plan = self.preview_restore(sha)
        original_replace = os.replace
        def fail_after_old(source, destination, *args, **kwargs):
            result = original_replace(source, destination, *args, **kwargs)
            if not args and not kwargs and Path(source) == self.data:
                raise OSError('injected after retaining old tree')
            return result
        with patch.object(os, 'replace', side_effect=fail_after_old):
            with self.assertRaises(release.ReleaseError): self.execute_restore(sha, plan)
        journal = self.root / 'RESTORE_PENDING.json'
        record = json.loads(journal.read_text())
        old = self.root / record['old']
        state = old / 'proxy-state/state.json'
        original = state.read_bytes()
        state.write_bytes(b'changed retained predecessor')
        with self.assertRaises(release.ReleaseError): release.recover_restore(self.root)
        self.assertTrue(journal.exists())
        self.assertFalse(self.data.exists())
        self.assertEqual(state.read_bytes(), b'changed retained predecessor')
        state.write_bytes(original)
        release.recover_restore(self.root)
        self.assertFalse(journal.exists())
        self.assertEqual(release.recovery_tree_digest(self.data), plan['source_sha256'])

    def test_active_panel_core_certificate_or_data_lease_blocks_preview_and_execution(self):
        self.commit()
        sha = self.backup()
        (self.data / 'certificates').mkdir(mode=0o700)
        (self.data / 'runtime/xray').mkdir(mode=0o700)
        plan = self.preview_restore(sha)
        for name in ('.operation.lock', '.panel.lease', 'data/runtime/sing-box/owner.lock',
                     'data/runtime/xray/owner.lock', 'data/certificates/.worker.lock'):
            with self.subTest(lease=name), release.lease(self.root / name):
                self.assert_no_change(lambda: self.preview_restore(sha))
                self.assert_no_change(lambda: self.execute_restore(sha, plan))
        with data_owner(self.data):
            self.assert_no_change(lambda: self.preview_restore(sha))
            self.assert_no_change(lambda: self.execute_restore(sha, plan))

    def test_stable_offline_lease_blocks_writers_before_any_data_or_writer_directory_exists(self):
        missing = self.root / 'never-created-data'
        runtime = CoreRuntime('sing-box', self.root / 'not-a-binary', missing / 'runtime/sing-box')
        certificate = CertificateManager(missing / 'certificates', provider=object())
        outcomes = []
        def attempt(label, callback):
            try:
                callback()
            except (DataOwnerError, ProxyStorageError, CoreError, routing_store.RoutingStorageError) as exc:
                outcomes.append((label, type(exc)))
            else:
                outcomes.append((label, None))
        with data_owner(missing, exclusive=True):
            with patch.object(database, 'DATA_DIR', missing), \
                 patch.object(database, 'DB_PATH', missing / 'v-ui.db'), \
                 patch.object(routing_store, 'ROUTING_FILE', missing / 'mihomo-routing.json'):
                tasks = [('proxy', lambda: ProxyStore(missing).read_snapshot()),
                         ('database', database._connect), ('core', runtime._claim),
                         ('certificate', certificate.process_once), ('routing', routing_store.read_snapshot)]
                for label, callback in tasks:
                    thread = threading.Thread(target=attempt, args=(label, callback))
                    thread.start(); thread.join(timeout=5)
                    self.assertFalse(thread.is_alive(), 'writer must refuse promptly, never wait behind recovery')
                    self.assertFalse(missing.exists(), 'writer created a directory before obtaining ownership')
        self.assertEqual(outcomes, [('proxy', ProxyStorageError), ('database', DataOwnerError),
                                    ('core', CoreError), ('certificate', DataOwnerError),
                                    ('routing', routing_store.RoutingStorageError)])
        self.assertIsNone(runtime.lease)
        self.assertIsNone(runtime._data_owner)


if __name__ == '__main__':
    unittest.main()
