"""Stopped backup/restore integration, using only fake data and local files."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
import zipfile

from app import release_tools as tools
from app.services.proxy_store import ProxyStore, ProxyStorageError


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


class ProxyBackupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="vui-proxy-backup-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.data = self.root / "data"
        self.data.mkdir(mode=0o700)
        with sqlite3.connect(self.data / "v-ui.db") as db:
            db.executescript("CREATE TABLE users(id INTEGER); CREATE TABLE inbounds(id INTEGER, remark TEXT);"
                             "CREATE TABLE admin_sessions(id INTEGER); CREATE TABLE subscription_grants(id INTEGER, revoked INTEGER);"
                             "INSERT INTO inbounds VALUES(1,'existing-node'); INSERT INTO admin_sessions VALUES(1);"
                             "INSERT INTO subscription_grants VALUES(1,0);")
        self.archive = self.root / "backup.zip"
        self.store = ProxyStore(self.data)

    def enabled(self):
        initial = self.store.read_snapshot()
        profile = {"name": "Fake upstream", "server": "upstream.example", "server_port": 443,
                   "uuid": "11111111-1111-4111-8111-111111111111",
                   "tls": {"mode": "tls", "server_name": "upstream.example"}}
        saved = self.store.save_upstream(profile, initial["revision"])
        settings = {key: value for key, value in saved["snapshot"]["settings"].items() if key != "local_password_set"}
        settings.update(relay_enabled=True, local_proxy_enabled=True, local_username="fake-user",
                        selected_upstream_id=saved["upstream_id"])
        draft = self.store.save_draft(settings, saved["snapshot"]["revision"], local_password="fake-password-for-tests")
        seed_runtime(self.store, self.data, draft["revision"], "test-apply")
        return self.store.read_snapshot()

    def assert_nodes_preserved_and_access_revoked(self):
        with sqlite3.connect(self.data / "v-ui.db") as db:
            self.assertEqual(db.execute("SELECT remark FROM inbounds").fetchone()[0], "existing-node")
            self.assertEqual(db.execute("SELECT count(*) FROM admin_sessions").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT revoked FROM subscription_grants").fetchone()[0], 1)

    def crafted_backup(self, mutate):
        sha = tools.backup(self.root, self.archive)
        payload = self.root / "crafted-payload"
        metadata = tools.unpack_verified(self.archive, sha, payload)
        (payload / tools.MANIFEST).unlink()
        mutate(payload)
        path = self.root / "crafted.zip"
        metadata = {key: value for key, value in metadata.items() if key not in ("schema", "files")}
        checksum = tools.create_archive(payload, path, metadata)
        return path, checksum

    def assert_rejected_restore_preserves_current(self, archive, checksum):
        inode = self.data.stat().st_ino
        before = (self.data / "v-ui.db").read_bytes()
        snapshot = self.store.read_snapshot()
        with self.assertRaises(tools.ReleaseError):
            tools.restore(self.root, archive, checksum)
        self.assertEqual(self.data.stat().st_ino, inode)
        self.assertEqual((self.data / "v-ui.db").read_bytes(), before)
        self.assertEqual(self.store.read_snapshot(), snapshot)
        self.assertFalse((self.root / "RESTORE_PENDING.json").exists())

    def test_fresh_store_backup_restore_roundtrip_and_empty_secret_directory(self):
        before = self.store.read_snapshot()
        (self.data / ".unrelated-secret").write_text("must-not-be-in-backup")
        sha = tools.backup(self.root, self.archive)
        with zipfile.ZipFile(self.archive) as archive:
            self.assertIn(".proxy-state-initialized", archive.namelist())
            self.assertIn("proxy-state/state.json", archive.namelist())
            self.assertNotIn(".proxy-state.lock", archive.namelist())
            self.assertNotIn(".unrelated-secret", archive.namelist())
        previous_umask = os.umask(0o022)
        try:
            tools.restore(self.root, self.archive, sha)
        finally:
            os.umask(previous_umask)
        self.assertEqual(ProxyStore(self.data).read_snapshot(), before)
        self.assertTrue((self.data / "proxy-state" / "secrets").is_dir())
        self.assert_nodes_preserved_and_access_revoked()

    def test_enabled_store_roundtrip_preserves_active_draft_and_private_credentials(self):
        before = self.enabled()
        active = self.store.load_active()
        sha = tools.backup(self.root, self.archive)
        settings = {key: value for key, value in before["settings"].items() if key != "local_password_set"}
        settings.update(local_proxy_enabled=False, relay_enabled=False)
        self.store.save_draft(settings, before["revision"])
        tools.restore(self.root, self.archive, sha)
        self.assertEqual(self.store.read_snapshot(), before)
        self.assertEqual(self.store.load_active(), active)
        self.assertEqual(self.archive.stat().st_mode & 0o777, 0o600)
        self.assert_nodes_preserved_and_access_revoked()

    def test_pending_apply_backup_remains_recovery_required_and_cannot_restore(self):
        current = self.enabled()
        self.store.begin_apply(current["revision"], "interrupted-apply")
        sha = tools.backup(self.root, self.archive)
        self.assert_rejected_restore_preserves_current(self.archive, sha)
        self.store.abort_apply("interrupted-apply")
        self.assert_rejected_restore_preserves_current(self.archive, sha)
        self.assertTrue(self.store.load_active()["settings"]["relay_enabled"])

    def test_legacy_backup_without_proxy_state_remains_supported(self):
        sha = tools.backup(self.root, self.archive)
        self.assertFalse((self.data / ".proxy-state-initialized").exists())
        tools.restore(self.root, self.archive, sha)
        self.assertFalse((self.data / ".proxy-state-initialized").exists())
        self.assert_nodes_preserved_and_access_revoked()

    def test_legacy_backup_cannot_erase_committed_on_policy(self):
        sha = tools.backup(self.root, self.archive)
        self.enabled()
        self.assert_rejected_restore_preserves_current(self.archive, sha)

    def test_proxy_bearing_off_backup_cannot_turn_off_active_proxy(self):
        initial = self.store.read_snapshot()
        self.store.begin_apply(initial["revision"], "explicit-off")
        self.store.commit_apply("explicit-off", "a" * 64)
        sha = tools.backup(self.root, self.archive)
        self.enabled()
        self.assert_rejected_restore_preserves_current(self.archive, sha)

    def test_current_corrupt_proxy_blocks_ordinary_restore_even_with_valid_backup(self):
        self.enabled()
        sha = tools.backup(self.root, self.archive)
        state = self.data / "proxy-state" / "state.json"
        state.write_text("{}")
        inode = self.data.stat().st_ino
        before = (self.data / "v-ui.db").read_bytes()
        with self.assertRaises(tools.ReleaseError):
            tools.restore(self.root, self.archive, sha)
        self.assertEqual(state.read_text(), "{}")
        self.assertEqual(self.data.stat().st_ino, inode)
        self.assertEqual((self.data / "v-ui.db").read_bytes(), before)

    def test_legacy_backup_can_replace_never_applied_off_draft(self):
        sha = tools.backup(self.root, self.archive)
        self.store.read_snapshot()
        tools.restore(self.root, self.archive, sha)
        self.assertFalse(self.store.read_snapshot()["settings"]["relay_enabled"])

    def test_backup_rejects_corrupt_state_before_publication(self):
        self.enabled()
        (self.data / "proxy-state" / "state.json").write_text("{}")
        before = (self.data / "v-ui.db").read_bytes()
        with self.assertRaises(tools.ReleaseError):
            tools.backup(self.root, self.archive)
        self.assertFalse(self.archive.exists())
        self.assertEqual((self.data / "v-ui.db").read_bytes(), before)

    def test_backup_rejects_missing_marker_and_missing_state_without_defaults(self):
        self.store.read_snapshot()
        marker = self.data / ".proxy-state-initialized"
        raw = marker.read_bytes()
        marker.unlink()
        with self.assertRaises(tools.ReleaseError):
            tools.backup(self.root, self.archive)
        self.assertFalse(marker.exists())
        marker.write_bytes(raw)
        marker.chmod(0o600)
        (self.data / "proxy-state" / "state.json").unlink()
        with self.assertRaises(tools.ReleaseError):
            tools.backup(self.root, self.archive)
        self.assertFalse(self.archive.exists())

    def test_backup_rejects_marker_state_secret_and_historical_object_hardlinks(self):
        self.enabled()
        paths = [self.data / ".proxy-state-initialized", self.data / "proxy-state" / "state.json",
                 *list((self.data / "proxy-state" / "secrets").glob("*.json")),
                 *list((self.data / "proxy-state" / "revisions").glob("*.json"))]
        for path in paths:
            alias = self.root / "external-alias"
            os.link(path, alias)
            try:
                with self.subTest(path=path.name), self.assertRaises(tools.ReleaseError):
                    tools.backup(self.root, self.archive)
                self.assertFalse(self.archive.exists())
            finally:
                alias.unlink()

    def test_backup_rejects_marker_and_private_directory_symlinks(self):
        self.enabled()
        for path in (self.data / ".proxy-state-initialized", self.data / "proxy-state" / "secrets"):
            saved = self.root / "saved-original"
            path.rename(saved)
            path.symlink_to(saved, target_is_directory=saved.is_dir())
            try:
                with self.subTest(path=path.name), self.assertRaises(tools.ReleaseError):
                    tools.backup(self.root, self.archive)
                self.assertFalse(self.archive.exists())
            finally:
                path.unlink()
                saved.rename(path)

    def test_backup_rejects_unknown_proxy_file_instead_of_archiving_it(self):
        self.store.read_snapshot()
        path = self.data / "proxy-state" / ".unknown-secret"
        path.write_text("must-not-be-archived")
        path.chmod(0o600)
        with self.assertRaises(tools.ReleaseError):
            tools.backup(self.root, self.archive)
        self.assertFalse(self.archive.exists())

    def test_restore_rejects_missing_marker_before_data_replacement(self):
        self.enabled()
        archive, sha = self.crafted_backup(lambda path: (path / ".proxy-state-initialized").unlink())
        self.assert_rejected_restore_preserves_current(archive, sha)

    def test_restore_rejects_corrupt_active_object_before_data_replacement(self):
        before = self.enabled()
        archive, sha = self.crafted_backup(lambda path: (
            path / "proxy-state" / "revisions" / (before["revision"] + ".json")).write_text("{}"))
        self.assert_rejected_restore_preserves_current(archive, sha)

    def test_restore_rejects_missing_referenced_secret_before_data_replacement(self):
        self.enabled()
        archive, sha = self.crafted_backup(lambda path: shutil.rmtree(path / "proxy-state" / "secrets"))
        self.assert_rejected_restore_preserves_current(archive, sha)

    def test_restore_rejects_transient_lock_in_archive(self):
        self.store.read_snapshot()
        def insert_lock(path):
            target = path / ".proxy-state.lock"
            target.write_bytes(b"")
            target.chmod(0o600)
        archive, sha = self.crafted_backup(insert_lock)
        self.assert_rejected_restore_preserves_current(archive, sha)

    def test_unconfirmed_write_cannot_be_backed_up_as_effective_state(self):
        self.enabled()
        intent = self.data / "proxy-state" / "write-intent.json"
        intent.write_text(json.dumps({"schema_version": 1, "interrupted": True}))
        intent.chmod(0o600)
        with self.assertRaises(tools.ReleaseError):
            tools.backup(self.root, self.archive)
        self.assertFalse(self.archive.exists())
        with self.assertRaises(ProxyStorageError):
            self.store.load_active()


if __name__ == "__main__":
    unittest.main()
