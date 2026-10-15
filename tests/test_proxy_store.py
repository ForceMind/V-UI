"""Fake credentials, temporary files, no cores, listeners, or external network."""
import copy
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.services.proxy_store import ProxyStore, ProxyStorageError, ProxyConflict, ProxyValidationError


FAKE_UUID = "11111111-1111-4111-8111-111111111111"
FAKE_UUID_2 = "22222222-2222-4222-8222-222222222222"
FAKE_PASSWORD = "fake-local-password-for-tests"


def profile():
    return {"schema_version": 1, "protocol": "vless", "name": "Example overseas",
            "server": "upstream.example", "server_port": 443, "uuid": FAKE_UUID,
            "transport": "tcp", "encryption": "none", "flow": "",
            "tls": {"mode": "tls", "server_name": "upstream.example", "alpn": [],
                    "fingerprint": "", "public_key": "", "short_id": ""}}


def writable_settings(snapshot):
    return {key: value for key, value in snapshot["settings"].items() if key != "local_password_set"}


class ProxyStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="vui-proxy-state-")
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        self.store = ProxyStore(self.data)
        self.initial = self.store.read_snapshot()

    def add(self, value=None):
        result = self.store.save_upstream(value or profile(), self.store.read_snapshot()["revision"])
        return result["upstream_id"], result["snapshot"]

    def enabled(self, *, local=False, relay=True):
        upstream_id, snapshot = self.add()
        settings = writable_settings(snapshot)
        settings.update(selected_upstream_id=upstream_id, relay_enabled=relay,
                        local_proxy_enabled=local, local_username="test-proxy" if local else "")
        return self.store.save_draft(settings, snapshot["revision"], local_password=FAKE_PASSWORD if local else None)

    def apply(self, snapshot, transaction="apply-test", core="core-test"):
        self.store.begin_apply(snapshot["revision"], transaction)
        return self.store.commit_apply(transaction, core)

    def test_fresh_defaults_are_explicitly_unapplied(self):
        self.assertFalse(self.initial["settings"]["local_proxy_enabled"])
        self.assertFalse(self.initial["settings"]["relay_enabled"])
        self.assertEqual(self.initial["settings"]["local_port"], 7891)
        self.assertEqual(self.initial["settings"]["local_username"], "")
        self.assertIsNone(self.initial["active"])
        self.assertIsNone(self.store.load_active())
        self.assertIsNone(self.store.recoverable_pending())

    def test_empty_existing_directory_is_fresh_but_existing_files_are_not(self):
        fresh = self.data / "fresh"
        fresh.mkdir(mode=0o700)
        (fresh / "proxy-state").mkdir(mode=0o700)
        self.assertIsNone(ProxyStore(fresh).read_snapshot()["active"])
        other = self.data / "unknown"
        (other / "proxy-state").mkdir(parents=True, mode=0o700)
        (other / "proxy-state" / "unrecognized").write_text("old state")
        with self.assertRaises(ProxyStorageError):
            ProxyStore(other).read_snapshot()

    def test_environment_directory_and_existing_database_are_preserved(self):
        target = self.data / "legacy"
        target.mkdir(mode=0o700)
        db = target / "v-ui.db"
        db.write_bytes(b"test-only existing nodes and credentials")
        before = db.read_bytes()
        with patch.dict(os.environ, {"VUI_DATA_DIR": str(target)}):
            store = ProxyStore()
            self.assertFalse(store.read_snapshot()["settings"]["relay_enabled"])
        self.assertEqual(db.read_bytes(), before)

    def test_bootstrap_is_idempotent_across_instances(self):
        self.assertEqual(self.initial, ProxyStore(self.data).read_snapshot())
        self.assertEqual(self.initial["revision"], self.store.save_draft(writable_settings(self.initial), self.initial["revision"])["revision"])

    def test_store_files_are_private(self):
        self.enabled(local=True)
        for path in self.store.path.rglob("*"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)
        for name in (".proxy-state.lock", ".proxy-state-initialized"):
            self.assertEqual(stat.S_IMODE((self.data / name).stat().st_mode), 0o600)

    def test_redacted_summary_and_separate_uuid_secret(self):
        upstream_id, snapshot = self.add()
        public = json.dumps(snapshot)
        self.assertNotIn(FAKE_UUID, public)
        self.assertNotIn("secret_ref", public)
        self.assertNotIn("local_password_ref", public)
        self.assertNotIn("sec_", public)
        self.assertTrue(snapshot["upstreams"][0]["uuid_set"])
        self.assertEqual(self.store.load_draft()["upstreams"][upstream_id]["uuid"], FAKE_UUID)
        for path in (self.store.path / "revisions").glob("*.json"):
            self.assertNotIn(FAKE_UUID, path.read_text())
        self.assertTrue(any(FAKE_UUID in path.read_text() for path in (self.store.path / "secrets").glob("*.json")))

    def test_reality_key_and_short_id_are_separate_and_redacted(self):
        candidate = profile()
        candidate["flow"] = "xtls-rprx-vision"
        candidate["tls"].update(mode="reality", fingerprint="chrome", public_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", short_id="0123456789abcdef")
        upstream_id, snapshot = self.add(candidate)
        for path in (self.store.path / "revisions").glob("*.json"):
            self.assertNotIn(candidate["tls"]["short_id"], path.read_text())
            self.assertNotIn(candidate["tls"]["public_key"], path.read_text())
        self.assertNotIn(candidate["tls"]["short_id"], json.dumps(snapshot))
        edited = copy.deepcopy(candidate)
        edited["uuid"] = edited["tls"]["short_id"] = edited["tls"]["public_key"] = ""
        result = self.store.save_upstream(edited, snapshot["revision"], profile_id=upstream_id)
        self.assertEqual(snapshot["revision"], result["snapshot"]["revision"])
        self.assertEqual(self.store.load_draft()["upstreams"][upstream_id], candidate)

    def test_source_metadata_is_strict_and_raw_input_not_accepted(self):
        for value in ("manual", "uri", "v2ray-json", "clash-yaml"):
            self.store.save_upstream(profile(), self.store.read_snapshot()["revision"], source_format=value, source_fingerprint="a" * 64)
        before = self.store.read_snapshot()
        for extra in ({"raw_text": "DO_NOT_STORE"}, {"source_format": "script"}):
            with self.assertRaises(ProxyValidationError):
                self.store.save_upstream({**profile(), **extra}, before["revision"])
        with self.assertRaises(ProxyValidationError):
            self.store.save_upstream(profile(), before["revision"], source_fingerprint="../../bad")
        for path in self.store.path.rglob("*.json"):
            self.assertNotIn("DO_NOT_STORE", path.read_text())

    def test_uuid_empty_preserves_and_secret_rotation_changes_revision(self):
        upstream_id, snapshot = self.add()
        result = self.store.save_upstream({**profile(), "uuid": ""}, snapshot["revision"], profile_id=upstream_id)
        self.assertEqual(snapshot, result["snapshot"])
        changed = self.store.save_upstream({**profile(), "uuid": FAKE_UUID_2}, snapshot["revision"], profile_id=upstream_id)
        self.assertNotEqual(changed["snapshot"]["revision"], snapshot["revision"])
        self.assertEqual(self.store.load_draft()["upstreams"][upstream_id]["uuid"], FAKE_UUID_2)

    def test_settings_whitelist_and_no_type_coercion(self):
        valid = writable_settings(self.initial)
        changes = ({"schema_version": True}, {"dns_policy_version": 2}, {"deny_policy_version": 0},
                   {"local_proxy_enabled": 1}, {"relay_enabled": "true"}, {"local_port": True},
                   {"local_port": 1023}, {"local_port": 65536}, {"local_port": "7891"},
                   {"local_username": "bad\nname"}, {"local_username": "x" * 65},
                   {"selected_upstream_id": "../bad"}, {"local_password_ref": "sec_" + "a" * 32},
                   {"local_password_set": False}, {"listen": "0.0.0.0"})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ProxyValidationError):
                self.store.save_draft({**valid, **change}, self.initial["revision"])
        self.assertEqual(self.store.read_snapshot(), self.initial)

    def test_flags_require_selected_profile_and_local_authentication(self):
        for local, relay in ((False, True), (True, False), (True, True)):
            settings = {**writable_settings(self.initial), "local_proxy_enabled": local, "relay_enabled": relay}
            with self.subTest(local=local, relay=relay), self.assertRaises(ProxyValidationError):
                self.store.save_draft(settings, self.initial["revision"])
        upstream_id, snapshot = self.add()
        settings = {**writable_settings(snapshot), "local_proxy_enabled": True, "selected_upstream_id": upstream_id}
        with self.assertRaises(ProxyValidationError):
            self.store.save_draft(settings, snapshot["revision"], local_password=FAKE_PASSWORD)
        settings["local_username"] = "proxy-user"
        with self.assertRaises(ProxyValidationError):
            self.store.save_draft(settings, snapshot["revision"])
        settings["selected_upstream_id"] = "up_" + "a" * 32
        with self.assertRaises(ProxyValidationError):
            self.store.save_draft(settings, snapshot["revision"], local_password=FAKE_PASSWORD)
        self.assertEqual(self.store.read_snapshot(), snapshot)

    def test_four_switch_combinations_save_independently(self):
        upstream_id, snapshot = self.add()
        for local, relay in ((False, False), (True, False), (False, True), (True, True), (False, False)):
            settings = {**writable_settings(snapshot), "local_proxy_enabled": local, "relay_enabled": relay,
                        "selected_upstream_id": upstream_id, "local_username": "fake-user"}
            snapshot = self.store.save_draft(settings, snapshot["revision"], local_password=FAKE_PASSWORD)
            self.assertEqual((snapshot["settings"]["local_proxy_enabled"], snapshot["settings"]["relay_enabled"]), (local, relay))
            self.assertIsNone(snapshot["active"])

    def test_local_password_boundaries_and_explicit_removal(self):
        settings = writable_settings(self.initial)
        for password in ("x" * 11, "x" * 256, "fake password", "fake-password\n", "非ascii假密码字符串", 123):
            with self.subTest(password=password), self.assertRaises(ProxyValidationError):
                self.store.save_draft(settings, self.initial["revision"], local_password=password)
        snapshot = self.store.save_draft(settings, self.initial["revision"], local_password="x" * 255)
        self.assertTrue(snapshot["settings"]["local_password_set"])
        self.assertNotIn("x" * 255, json.dumps(snapshot))
        for blank in (None, ""):
            same = self.store.save_draft(settings, snapshot["revision"], local_password=blank)
            self.assertEqual(same["revision"], snapshot["revision"])
        changed = self.store.save_draft(settings, snapshot["revision"], local_password=FAKE_PASSWORD)
        self.assertNotEqual(snapshot["revision"], changed["revision"])
        cleared = self.store.clear_local_password(changed["revision"])
        self.assertFalse(cleared["settings"]["local_password_set"])
        self.assertIsNone(self.store.load_draft()["local_password"])

    def test_active_and_draft_are_independent_and_restart_recovers_same(self):
        enabled = self.enabled(local=True)
        committed = self.apply(enabled)
        off = writable_settings(enabled)
        off.update(local_proxy_enabled=False, relay_enabled=False)
        draft = self.store.save_draft(off, enabled["revision"])
        reloaded = ProxyStore(self.data)
        self.assertEqual(reloaded.read_snapshot(), draft)
        self.assertTrue(reloaded.load_active()["settings"]["relay_enabled"])
        self.assertFalse(reloaded.load_draft()["settings"]["relay_enabled"])
        self.assertEqual(reloaded.load_active()["revision"], committed["active"]["revision"])
        with self.assertRaises(ProxyConflict):
            self.store.clear_local_password(draft["revision"])

    def test_pending_apply_survives_restart_and_blocks_writes(self):
        snapshot = self.enabled()
        pending = self.store.begin_apply(snapshot["revision"], "pending")
        self.assertEqual(pending, ProxyStore(self.data).recoverable_pending())
        self.assertEqual(pending, self.store.begin_apply(snapshot["revision"], "pending"))
        self.assertIsNone(self.store.load_active())
        for action in (lambda: self.store.save_draft(writable_settings(snapshot), snapshot["revision"]),
                       lambda: self.store.save_upstream(profile(), snapshot["revision"]),
                       lambda: self.store.begin_apply(snapshot["revision"], "another")):
            with self.assertRaises(ProxyConflict):
                action()

    def test_first_failed_enable_and_failed_disable_keep_last_committed_policy(self):
        enabled = self.enabled()
        self.store.begin_apply(enabled["revision"], "first-fails")
        self.store.abort_apply("first-fails")
        self.assertIsNone(self.store.load_active())
        self.apply(enabled, "enable-ok")
        disabled_settings = {**writable_settings(enabled), "relay_enabled": False}
        disabled = self.store.save_draft(disabled_settings, enabled["revision"])
        pending = self.store.begin_apply(disabled["revision"], "disable-fails")
        self.assertEqual(pending["base_active_revision"], enabled["revision"])
        self.store.abort_apply("disable-fails")
        self.assertTrue(self.store.load_active()["settings"]["relay_enabled"])

    def test_completed_transactions_are_idempotent_and_ids_cannot_change_meaning(self):
        snapshot = self.enabled()
        first = self.apply(snapshot)
        self.assertEqual(first, self.store.commit_apply("apply-test", "core-test"))
        self.assertEqual(self.store.begin_apply(snapshot["revision"], "apply-test")["phase"], "committed")
        with self.assertRaises(ProxyConflict):
            self.store.abort_apply("apply-test")
        with self.assertRaises(ProxyConflict):
            self.store.commit_apply("apply-test", "different-core")
        self.store.begin_apply(snapshot["revision"], "abort-once")
        first_abort = self.store.abort_apply("abort-once")
        self.assertEqual(first_abort, self.store.abort_apply("abort-once"))
        with self.assertRaises(ProxyConflict):
            self.store.commit_apply("abort-once", "core-test")
        changed = self.store.save_draft({**writable_settings(snapshot), "local_port": 7892}, snapshot["revision"])
        with self.assertRaises(ProxyConflict):
            self.store.begin_apply(changed["revision"], "apply-test")

    def test_in_use_profile_cannot_be_deleted_in_draft_or_active(self):
        snapshot = self.enabled()
        upstream_id = snapshot["settings"]["selected_upstream_id"]
        with self.assertRaises(ProxyConflict):
            self.store.delete_upstream(upstream_id, snapshot["revision"])
        self.apply(snapshot)
        disabled = self.store.save_draft({**writable_settings(snapshot), "relay_enabled": False}, snapshot["revision"])
        with self.assertRaises(ProxyConflict):
            self.store.delete_upstream(upstream_id, disabled["revision"])
        self.apply(disabled, "disable-ok", "disabled-core")
        deleted = self.store.delete_upstream(upstream_id, disabled["revision"])
        self.assertEqual(deleted["upstreams"], [])
        self.assertIsNone(deleted["settings"]["selected_upstream_id"])
        self.assertTrue(list((self.store.path / "secrets").iterdir()))

    def test_missing_state_and_missing_entire_private_directory_fail_closed(self):
        self.apply(self.enabled())
        state_file = self.store.path / "state.json"
        state_file.unlink()
        with self.assertRaises(ProxyStorageError):
            self.store.read_snapshot()
        shutil.rmtree(self.store.path)
        with self.assertRaises(ProxyStorageError):
            ProxyStore(self.data).read_snapshot()
        self.assertFalse(self.store.path.exists())

    def test_missing_active_revision_never_uses_disabled_draft(self):
        snapshot = self.enabled()
        self.apply(snapshot)
        self.store.save_draft({**writable_settings(snapshot), "relay_enabled": False}, snapshot["revision"])
        (self.store.path / "revisions" / (snapshot["revision"] + ".json")).unlink()
        for call in (self.store.read_snapshot, self.store.load_active, self.store.load_draft):
            with self.assertRaises(ProxyStorageError):
                call()

    def test_erased_or_inconsistent_active_pointer_is_corruption_not_unapplied(self):
        self.apply(self.enabled())
        path = self.store.path / "state.json"
        state = json.loads(path.read_text())
        for active, core in ((None, None), (self.initial["revision"], "core-test"),
                             (state["active_revision"], "other-core")):
            path.write_text(json.dumps({**state, "active_revision": active, "core_revision": core}))
            with self.subTest(active=active, core=core), self.assertRaises(ProxyStorageError):
                self.store.load_active()

    def test_missing_and_corrupt_secrets_fail_closed(self):
        self.enabled(local=True)
        secret = next((self.store.path / "secrets").glob("*.json"))
        raw = secret.read_bytes()
        secret.write_text("{}")
        with self.assertRaises(ProxyStorageError):
            self.store.load_draft()
        secret.write_bytes(raw)
        secret.unlink()
        with self.assertRaises(ProxyStorageError):
            self.store.read_snapshot()

    def test_corrupt_documents_and_json_null_do_not_look_absent(self):
        state_file = self.store.path / "state.json"
        original = state_file.read_bytes()
        for corrupt in (b"null", b"[]", b"{}", b"{", b'{"schema_version":1,"schema_version":1}', b'{"schema_version":NaN}', b"x" * 2_000_001):
            state_file.write_bytes(corrupt)
            with self.subTest(corrupt=corrupt[:64]), self.assertRaises(ProxyStorageError):
                self.store.read_snapshot()
        state_file.write_bytes(original)
        marker = self.data / ".proxy-state-initialized"
        marker.write_text("null")
        with self.assertRaises(ProxyStorageError):
            self.store.read_snapshot()

    def test_revision_digest_rejects_well_formed_tampering(self):
        path = self.store.path / "revisions" / (self.initial["revision"] + ".json")
        document = json.loads(path.read_text())
        document["settings"]["local_port"] = 7892
        path.write_text(json.dumps(document))
        with self.assertRaises(ProxyStorageError):
            self.store.read_snapshot()

    def test_symlinked_data_path_component_is_rejected(self):
        link = self.data / "alias"
        link.symlink_to(self.store.path, target_is_directory=True)
        with self.assertRaises(ProxyStorageError):
            ProxyStore(link / "child").read_snapshot()

    def test_unsafe_writable_ancestor_is_rejected(self):
        parent = self.data / "writable-ancestor"
        parent.mkdir(mode=0o700)
        parent.chmod(0o777)
        child = parent / "private-data"
        child.mkdir(mode=0o700)
        with self.assertRaises(ProxyStorageError):
            ProxyStore(child).read_snapshot()
        self.assertFalse((child / ".proxy-state.lock").exists())

    def test_owned_child_below_trusted_sticky_directory_is_allowed(self):
        parent = self.data / "sticky-tmp"
        parent.mkdir(mode=0o700)
        parent.chmod(0o1777)
        child = parent / "private-data"
        child.mkdir(mode=0o700)
        self.assertIsNone(ProxyStore(child).read_snapshot()["active"])

    def test_symlinked_state_and_secret_are_never_followed(self):
        self.add()
        for path in (self.store.path / "state.json", next((self.store.path / "secrets").glob("*.json"))):
            raw = path.read_bytes()
            outside = self.data / "unchanged"
            outside.write_bytes(raw)
            outside.chmod(0o600)
            path.unlink()
            path.symlink_to(outside)
            with self.subTest(path=path.name), self.assertRaises(ProxyStorageError):
                self.store.read_snapshot()
            self.assertEqual(outside.read_bytes(), raw)
            path.unlink()
            path.write_bytes(raw)
            path.chmod(0o600)

    def test_hardlinked_lock_marker_state_revision_and_secret_are_rejected(self):
        _, snapshot = self.add()
        paths = [self.data / ".proxy-state.lock", self.data / ".proxy-state-initialized",
                 self.store.path / "state.json", self.store.path / "revisions" / (snapshot["revision"] + ".json"),
                 *list((self.store.path / "secrets").glob("*.json"))]
        for path in paths:
            alias = self.data / "hardlink"
            os.link(path, alias)
            try:
                with self.subTest(path=path.name), self.assertRaises(ProxyStorageError):
                    self.store.read_snapshot()
            finally:
                alias.unlink()

    def test_group_readable_secret_and_writable_private_directory_are_rejected_not_chmodded(self):
        self.add()
        secret = next((self.store.path / "secrets").glob("*.json"))
        secret.chmod(0o640)
        with self.assertRaises(ProxyStorageError):
            self.store.read_snapshot()
        self.assertEqual(stat.S_IMODE(secret.stat().st_mode), 0o640)
        secret.chmod(0o600)
        self.store.path.chmod(0o750)
        with self.assertRaises(ProxyStorageError):
            self.store.read_snapshot()
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o750)

    def test_nonregular_state_file_does_not_block(self):
        path = self.store.path / "state.json"
        path.unlink()
        os.mkfifo(path, 0o600)
        with self.assertRaises(ProxyStorageError):
            self.store.read_snapshot()

    def test_compare_and_swap_conflicts_do_not_leave_secret_files(self):
        _, saved = self.add()
        files_before = list((self.store.path / "secrets").iterdir())
        with self.assertRaises(ProxyConflict):
            self.store.save_upstream(profile(), self.initial["revision"])
        with self.assertRaises(ProxyConflict):
            self.store.save_draft(writable_settings(saved), self.initial["revision"], local_password=FAKE_PASSWORD)
        self.assertEqual(files_before, list((self.store.path / "secrets").iterdir()))

    def test_concurrent_threads_have_exactly_one_cas_winner(self):
        barrier = threading.Barrier(6)
        results = []
        def writer(index):
            other = ProxyStore(self.data)
            barrier.wait(timeout=5)
            try:
                other.save_draft({**writable_settings(self.initial), "local_port": 8000 + index}, self.initial["revision"])
                results.append("saved")
            except ProxyConflict:
                results.append("conflict")
        threads = [threading.Thread(target=writer, args=(index,)) for index in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(results.count("saved"), 1)
        self.assertEqual(results.count("conflict"), 5)

    def test_concurrent_processes_have_exactly_one_cas_winner(self):
        code = '''import json,sys
from app.services.proxy_store import ProxyStore,ProxyConflict
s=ProxyStore(sys.argv[1]); settings=json.loads(sys.argv[3])
try: s.save_draft(settings,sys.argv[2]); print("saved")
except ProxyConflict: print("conflict")
'''
        processes = [subprocess.Popen([sys.executable, "-c", code, str(self.data), self.initial["revision"],
                       json.dumps({**writable_settings(self.initial), "local_port": 9000 + index})],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for index in range(4)]
        outputs = []
        try:
            for process in processes:
                out, error = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, error)
                outputs.append(out.strip())
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        self.assertEqual(outputs.count("saved"), 1)
        self.assertEqual(outputs.count("conflict"), 3)

    def test_file_fsync_failure_preserves_existing_manifest_and_cleans_stage(self):
        state_file = self.store.path / "state.json"
        before = state_file.read_bytes()
        with patch("app.services.proxy_store.os.fsync", side_effect=OSError("test fsync")):
            with self.assertRaises(ProxyStorageError):
                self.store.save_draft({**writable_settings(self.initial), "local_port": 7892}, self.initial["revision"])
        self.assertEqual(state_file.read_bytes(), before)
        self.assertEqual(self.store.read_snapshot(), self.initial)
        self.assertFalse(list(self.store.path.rglob(".stage-*")))

    def test_directory_fsync_failure_after_commit_replace_rolls_back(self):
        self.apply(self.enabled())
        old = self.store.read_snapshot()
        draft = self.store.save_draft({**writable_settings(old), "relay_enabled": False}, old["revision"])
        self.store.begin_apply(draft["revision"], "disable-fails-fsync")
        state_file = self.store.path / "state.json"
        before = state_file.read_bytes()
        real_replace, real_fsync = os.replace, os.fsync
        fail = [False]
        failed = [False]
        def replace(source, destination, **kwargs):
            real_replace(source, destination, **kwargs)
            if destination == "state.json" and not failed[0]:
                fail[0] = True
        def fsync(fd):
            if fail[0] and stat.S_ISDIR(os.fstat(fd).st_mode):
                fail[0], failed[0] = False, True
                raise OSError("test directory fsync")
            return real_fsync(fd)
        with patch("app.services.proxy_store.os.replace", side_effect=replace), patch("app.services.proxy_store.os.fsync", side_effect=fsync):
            with self.assertRaises(ProxyStorageError):
                self.store.commit_apply("disable-fails-fsync", "off-core")
        self.assertTrue(failed[0])
        self.assertEqual(state_file.read_bytes(), before)
        self.assertTrue(self.store.load_active()["settings"]["relay_enabled"])
        self.assertEqual(self.store.recoverable_pending()["transaction_id"], "disable-fails-fsync")

    def test_unconfirmed_rollback_preserves_intent_and_blocks_all_normal_reads(self):
        snapshot = self.enabled()
        self.store.begin_apply(snapshot["revision"], "uncertain")
        real_write = self.store._atomic_json
        def uncertain_write(directory, name, value):
            real_write(directory, name, value)
            if name == "state.json":
                raise OSError("test commit and rollback fsync uncertainty")
        with patch.object(self.store, "_atomic_json", side_effect=uncertain_write):
            with self.assertRaises(ProxyStorageError):
                self.store.commit_apply("uncertain", "some-core")
        self.assertTrue((self.store.path / "write-intent.json").exists())
        for call in (self.store.read_snapshot, self.store.load_active, self.store.load_draft):
            with self.assertRaises(ProxyStorageError):
                call()
        self.assertEqual(self.store.recoverable_pending(), {"phase": "storage_uncertain", "requires_recovery": True})

    def test_partial_bootstrap_never_retries_as_fresh(self):
        target = self.data / "interrupted"
        target.mkdir(mode=0o700)
        store = ProxyStore(target)
        real_write = store._atomic_json
        def interrupt(directory, name, value):
            if name == "state.json":
                raise OSError("test initialization interruption")
            return real_write(directory, name, value)
        with patch.object(store, "_atomic_json", side_effect=interrupt):
            with self.assertRaises(ProxyStorageError):
                store.read_snapshot()
        self.assertTrue((target / ".proxy-state-initialized").exists())
        with self.assertRaises(ProxyStorageError):
            ProxyStore(target).read_snapshot()

    def test_invalid_transaction_inputs_have_sanitized_errors(self):
        for bad in ([], "../escape", "", "line\nbreak"):
            for action in (lambda: self.store.begin_apply(self.initial["revision"], bad),
                           lambda: self.store.commit_apply(bad, "core"),
                           lambda: self.store.abort_apply(bad)):
                with self.subTest(bad=bad), self.assertRaises(ProxyValidationError):
                    action()


if __name__ == "__main__":
    unittest.main()
