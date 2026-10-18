"""Durable coordination tests: fake credentials/runtime, temporary private files.

These are transaction/policy unit tests, not real core or network evidence.
"""
import copy
import errno
import itertools
import json
from pathlib import Path
import tempfile
import socket
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.services.core_runtime import CoreError, CoreRuntime, atomic_write, encode, revision
from app.services.proxy_apply import ProxyApplyCoordinator, ProxyApplyError, preflight_local_listener
from app.services.proxy_policy import PolicyError, VerifiedEndpoint
from app.services.proxy_store import ProxyCapacityError, ProxyConflict, ProxyStore
from tests.test_proxy_store import FAKE_PASSWORD, FAKE_UUID, near_full_history, profile, writable_settings


class SimulatedCrash(BaseException):
    pass


class FakeRuntime:
    def __init__(self):
        self.saved = {}
        self.record = {"revision": None, "enabled": False}
        self.running = False
        self.calls = []
        self.fail_check = self.fail_apply = self.fail_restore = False
        self.fail_after_switch = self.fail_recover = False
        self.on_check = None

    def state(self):
        return dict(self.record)

    def status(self):
        return {"name": "sing-box", "version_required": "1.14.2", "binary_exists": True,
                "running": self.running, "pid": 111 if self.running else None,
                "applied_revision": self.record["revision"], "enabled": self.record["enabled"],
                "config_exists": self.record["revision"] is not None,
                "last_error": "DO_NOT_EXPOSE_INTERNAL_ERROR", "config": {"uuid": FAKE_UUID}}

    def apply(self, config, *, activate=True, rollback_on_failure=True):
        rev = revision(config)
        self.calls.append(("apply" if activate else "check", rev))
        self.saved[rev] = copy.deepcopy(config)
        if self.fail_check:
            raise CoreError("Rejected fake candidate")
        if self.on_check and not activate:
            callback, self.on_check = self.on_check, None
            callback()
        if not activate:
            return self.status()
        if self.fail_apply:
            raise CoreError("Failed fake launch")
        self.record = {"revision": rev, "enabled": bool(config.get("inbounds"))}
        self.running = self.record["enabled"]
        if self.fail_after_switch:
            raise CoreError("Failed after switching")
        return self.status()

    def restore(self, rev, *, enabled):
        self.calls.append(("restore", rev))
        self.running = False
        if self.fail_restore:
            raise CoreError("Failed fake restoration")
        if rev is not None and rev not in self.saved:
            raise CoreError("Missing saved config")
        self.record = {"revision": rev, "enabled": enabled}
        self.running = enabled

    def stop(self, *, persist=True):
        self.calls.append(("stop", persist))
        self.running = False
        if persist:
            self.record["enabled"] = False

    def recover(self):
        self.calls.append(("recover", self.record["revision"]))
        if self.fail_recover:
            raise CoreError("Failed fake recovery")
        self.running = self.record["enabled"]


def row(identifier=1, port=24443):
    return SimpleNamespace(id=identifier, routing_mode="inherit", enable=True, core="sing-box", protocol="vless", tag=f"node-{identifier}",
                           port=port, expiry_time=0, settings={"users": [{"uuid": FAKE_UUID}]},
                           stream_settings={"tls": {"enabled": True, "server_name": "node.example",
                               "certificate_path": "/test-only-fake-cert", "key_path": "/test-only-fake-key"}})


def build_config(rows):
    return {"inbounds": [{"type": item.protocol, "tag": item.tag, "listen": "::", "listen_port": item.port,
                           **copy.deepcopy(item.settings), **copy.deepcopy(item.stream_settings)}
                          for item in rows if item.enable and item.core == "sing-box"],
            "outbounds": [{"type": "direct", "tag": "direct"}], "route": {"final": "direct"}}


class ProxyApplyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vui-apply-")
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        self.store = ProxyStore(self.data)
        self.runtime = FakeRuntime()
        self.rows = [row()]
        self.lock = threading.RLock()
        self.resolutions = []
        self.resolved_at = 1
        initial = self.store.read_snapshot()
        added = self.store.save_upstream(profile(), initial["revision"])
        settings = writable_settings(added["snapshot"])
        settings.update(selected_upstream_id=added["upstream_id"], local_username="fake-user")
        self.store.save_draft(settings, added["snapshot"]["revision"], local_password=FAKE_PASSWORD)
        self.coordinator = self.make_coordinator()

    def resolver(self, profile_id, rev, value, *, self_addresses):
        self.resolutions.append((profile_id, rev, self_addresses))
        return VerifiedEndpoint(profile_id, rev, value["server"], ("8.8.4.4",), self.resolved_at)

    def make_coordinator(self, **kwargs):
        kwargs.setdefault("preflight", lambda *_: None)
        return ProxyApplyCoordinator(self.store, self.runtime, build_config, self.lock,
                   rows_provider=lambda: self.rows, resolver=self.resolver,
                   address_provider=lambda _: ("127.0.0.1", "192.168.8.2"), **kwargs)

    def draft(self, local, relay):
        snapshot = self.store.read_snapshot()
        settings = writable_settings(snapshot)
        settings.update(local_proxy_enabled=local, relay_enabled=relay)
        return self.store.save_draft(settings, snapshot["revision"])

    def apply(self, local=False, relay=True, transaction="apply"):
        snapshot = self.draft(local, relay)
        return self.coordinator.apply(snapshot["revision"], transaction)

    def assert_active(self, local, relay):
        active = self.store.load_active()
        self.assertEqual((active["settings"]["local_proxy_enabled"], active["settings"]["relay_enabled"]), (local, relay))
        self.assertEqual(active["core_revision"], self.runtime.state()["revision"])

    def test_capacity_admission_keeps_prior_on_runtime_and_every_history_byte(self):
        result = self.apply(True, True, transaction="old")
        before = near_full_history(self.store)
        self.runtime.calls.clear()
        with self.assertRaises(ProxyCapacityError):
            self.coordinator.apply(result["revision"], "does-not-fit")
        self.assert_active(True, True)
        self.assertTrue(self.runtime.running)
        self.assertEqual(self.runtime.calls, [("check", result["candidate_revision"])])
        self.assertEqual((self.store.path / "state.json").read_bytes(), before)
        self.assertIsNone(self.store.recoverable_pending())
        self.assertFalse((self.store.path / "write-intent.json").exists())
        self.assertIsNone(self.store.transaction("does-not-fit"))
        self.assertTrue(self.coordinator.apply(result["revision"], "old")["already_completed"])

    def test_all_twelve_directed_transitions_are_atomic_in_fake_runtime(self):
        states = ((False, False), (True, False), (False, True), (True, True))
        for index, (before, after) in enumerate(itertools.permutations(states, 2)):
            with self.subTest(before=before, after=after):
                self.apply(*before, transaction=f"baseline-{index}")
                result = self.apply(*after, transaction=f"switch-{index}")
                self.assertTrue(result["applied"])
                self.assert_active(*after)
                self.assertIsNone(result["snapshot"]["pending"])

    def test_all_twelve_failed_switches_preserve_whole_old_combination(self):
        states = ((False, False), (True, False), (False, True), (True, True))
        for index, (before, after) in enumerate(itertools.permutations(states, 2)):
            with self.subTest(before=before, after=after):
                self.apply(*before, transaction=f"baseline-{index}")
                old = self.runtime.state()
                self.runtime.fail_after_switch = True
                with self.assertRaisesRegex(ProxyApplyError, "complete previous"):
                    self.apply(*after, transaction=f"switch-{index}")
                self.runtime.fail_after_switch = False
                self.assertEqual(self.runtime.state(), old)
                self.assert_active(*before)
                self.assertTrue(self.runtime.running)
                self.assertEqual(self.store.transaction(f"switch-{index}")["phase"], "aborted")

    def test_first_failed_enable_retains_unapplied_baseline(self):
        self.runtime.fail_apply = True
        with self.assertRaises(ProxyApplyError):
            self.apply(True, True)
        self.assertIsNone(self.store.load_active())
        self.assertIsNone(self.runtime.state()["revision"])
        self.assertFalse(self.runtime.running)
        self.assertIsNone(self.store.recoverable_pending())

    def test_candidate_check_precedes_journal_and_disruption(self):
        self.apply(transaction="old")
        before = self.runtime.state()
        snapshot = self.draft(True, True)
        self.runtime.calls.clear()
        self.runtime.fail_check = True
        with self.assertRaises(CoreError):
            self.coordinator.apply(snapshot["revision"], "reject")
        self.assertEqual([call[0] for call in self.runtime.calls], ["check"])
        self.assertEqual(self.runtime.state(), before)
        self.assertTrue(self.runtime.running)
        self.assertIsNone(self.store.transaction("reject"))

    def test_preflight_failure_cannot_disrupt_or_publish_candidate(self):
        self.apply(transaction="old")
        self.runtime.calls.clear()
        blocked = self.make_coordinator(preflight=lambda *_: (_ for _ in ()).throw(CoreError("Port owned")))
        with self.assertRaises(CoreError):
            blocked.apply(self.draft(True, True)["revision"], "blocked")
        self.assertEqual([call[0] for call in self.runtime.calls], ["check"])
        self.assert_active(False, True)
        self.assertTrue(self.runtime.running)

    def test_verification_failure_rolls_back_before_abort(self):
        self.apply(transaction="old")
        verifier = self.make_coordinator(verify_runtime=lambda *_: (_ for _ in ()).throw(CoreError("Bad listener")))
        with self.assertRaises(ProxyApplyError):
            verifier.apply(self.draft(True, True)["revision"], "new")
        self.assert_active(False, True)
        self.assertEqual(self.store.transaction("new")["phase"], "aborted")

    def test_draft_change_during_check_is_strict_cas_conflict(self):
        snapshot = self.draft(False, True)
        self.runtime.on_check = lambda: self.draft(True, True)
        with self.assertRaises(ProxyConflict):
            self.coordinator.apply(snapshot["revision"], "raced")
        self.assertFalse(any(call[0] == "apply" for call in self.runtime.calls))
        self.assertIsNone(self.store.recoverable_pending())
        self.assertIsNone(self.store.load_active())

    def test_idempotent_retry_survives_newer_draft_without_reapply_or_resolution(self):
        result = self.apply(transaction="same")
        before_calls, before_resolutions = list(self.runtime.calls), list(self.resolutions)
        self.draft(True, True)
        retry = self.coordinator.apply(result["revision"], "same")
        self.assertTrue(retry["already_completed"])
        self.assertTrue(retry["applied"])
        self.assertEqual(self.runtime.calls, before_calls)
        self.assertEqual(self.resolutions, before_resolutions)
        with self.assertRaises(ProxyConflict):
            self.coordinator.apply(self.store.read_snapshot()["revision"], "same")

    def test_aborted_retry_does_not_start_again(self):
        snapshot = self.draft(False, True)
        self.runtime.fail_apply = True
        with self.assertRaises(ProxyApplyError):
            self.coordinator.apply(snapshot["revision"], "failed-once")
        before = list(self.runtime.calls)
        self.runtime.fail_apply = False
        result = self.coordinator.apply(snapshot["revision"], "failed-once")
        self.assertFalse(result["applied"])
        self.assertEqual(result["phase"], "aborted")
        self.assertEqual(self.runtime.calls, before)

    def test_same_transaction_concurrent_requests_launch_only_once(self):
        snapshot = self.draft(True, True)
        barrier, results, errors = threading.Barrier(6), [], []
        def run():
            barrier.wait(timeout=10)
            try:
                results.append(self.make_coordinator().apply(snapshot["revision"], "same"))
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=run) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 6)
        self.assertEqual(sum(call[0] == "apply" for call in self.runtime.calls), 1)
        self.assertEqual(len(self.resolutions), 1)

    def test_reconcile_uses_active_snapshot_pin_and_updates_only_core_revision(self):
        result = self.apply(transaction="old")
        old_revision, old_core = result["revision"], result["candidate_revision"]
        draft = self.draft(True, False)
        self.rows[0].stream_settings["tls"]["certificate_path"] = "/different-fake-cert"
        before_resolution = list(self.resolutions)
        result = self.coordinator.reconcile(transaction_id="certificate")
        self.assertEqual(self.resolutions, before_resolution)
        self.assertEqual(result["snapshot"]["revision"], draft["revision"])
        self.assertEqual(result["snapshot"]["active"]["revision"], old_revision)
        self.assertNotEqual(result["snapshot"]["active"]["core_revision"], old_core)
        self.assert_active(False, True)
        self.assertEqual(self.store.load_active()["runtime_context"]["endpoint"]["resolved_at"], 1)
        self.assertNotIn("vui-relay-local", json.dumps(self.runtime.saved[result["candidate_revision"]]))

    def test_active_validation_blocks_new_unqualified_ingress_without_publishing(self):
        self.apply(transaction="old")
        bad = row(2, 24444)
        bad.core = "xray"
        with self.assertRaises(PolicyError):
            self.coordinator.compile_active([*self.rows, bad])
        self.assert_active(False, True)
        self.assertIsNone(self.store.recoverable_pending())

    def test_validate_only_preserves_intentionally_stopped_core(self):
        self.apply(transaction="old")
        self.runtime.stop()
        state = self.runtime.state()
        result = self.coordinator.reconcile(activate=False)
        self.assertFalse(result["applied"])
        self.assertFalse(self.runtime.running)
        self.assertEqual(self.runtime.state(), state)
        self.assertIsNone(self.store.recoverable_pending())

    def test_check_is_revision_bound_and_never_creates_active_state(self):
        snapshot = self.draft(True, True)
        result = self.coordinator.check(snapshot["revision"])
        self.assertEqual(result["revision"], snapshot["revision"])
        self.assertFalse(result["applied"])
        self.assertIsNone(self.store.load_active())
        self.assertFalse(self.runtime.running)
        self.assertIsNone(self.store.recoverable_pending())

    def test_crash_after_core_commit_restores_old_committed_relay_before_recovery(self):
        self.apply(transaction="old")
        old = self.runtime.state()
        new = self.draft(False, False)
        with patch.object(self.store, "commit_apply", side_effect=SimulatedCrash()):
            with self.assertRaises(SimulatedCrash):
                self.coordinator.apply(new["revision"], "crashed")
        self.assertNotEqual(self.runtime.state()["revision"], old["revision"])
        self.assertFalse(self.runtime.running)
        self.assertIsNotNone(self.store.recoverable_pending())
        self.runtime.calls.clear()
        result = self.make_coordinator().recover()
        self.assertEqual(self.runtime.state(), old)
        self.assert_active(False, True)
        self.assertEqual(result["phase"], "aborted")
        self.assertEqual(self.runtime.calls, [("restore", old["revision"])])

    def test_crash_with_no_previous_state_stops_candidate_and_leaves_unapplied(self):
        snapshot = self.draft(True, True)
        with patch.object(self.store, "commit_apply", side_effect=SimulatedCrash()):
            with self.assertRaises(SimulatedCrash):
                self.coordinator.apply(snapshot["revision"], "crash-first")
        self.make_coordinator().recover()
        self.assertEqual(self.runtime.state(), {"revision": None, "enabled": False})
        self.assertIsNone(self.store.load_active())
        self.assertFalse(self.runtime.running)

    def test_failed_restore_stops_and_retains_pending_evidence(self):
        self.apply(transaction="old")
        self.runtime.fail_after_switch = self.runtime.fail_restore = True
        with self.assertRaisesRegex(ProxyApplyError, "evidence retained"):
            self.apply(True, False, transaction="failed")
        self.assertFalse(self.runtime.running)
        self.assertEqual(self.store.recoverable_pending()["transaction_id"], "failed")
        self.assertTrue(self.store.load_active()["settings"]["relay_enabled"])
        with self.assertRaises(ProxyApplyError):
            self.make_coordinator().recover()
        self.assertFalse(any(call[0] == "recover" for call in self.runtime.calls))
        self.assertIsNotNone(self.store.recoverable_pending())

    def test_commit_exception_after_success_never_rolls_core_back_behind_active(self):
        self.apply(transaction="old")
        original = self.store.commit_apply
        def committed_then_error(*args):
            original(*args)
            raise OSError("Simulated uncertain completion")
        with patch.object(self.store, "commit_apply", side_effect=committed_then_error):
            with self.assertRaisesRegex(ProxyApplyError, "evidence retained"):
                self.apply(True, True, transaction="new")
        self.assert_active(True, True)
        self.assertFalse(self.runtime.running)
        self.assertIsNone(self.store.recoverable_pending())
        self.make_coordinator().recover()
        self.assertTrue(self.runtime.running)
        self.assert_active(True, True)

    def test_commit_failure_before_write_restores_complete_old_policy(self):
        self.apply(transaction="old")
        with patch.object(self.store, "commit_apply", side_effect=OSError("fake fault")):
            with self.assertRaises(ProxyApplyError):
                self.apply(True, True, transaction="new")
        self.assert_active(False, True)
        self.assertTrue(self.runtime.running)
        self.assertIsNone(self.store.recoverable_pending())

    def test_abort_failure_stops_and_retains_journal(self):
        self.apply(transaction="old")
        self.runtime.fail_apply = True
        with patch.object(self.store, "abort_apply", side_effect=OSError("fake fault")):
            with self.assertRaises(ProxyApplyError):
                self.apply(True, True, transaction="new")
        self.assertFalse(self.runtime.running)
        self.assertIsNotNone(self.store.recoverable_pending())
        self.assert_active(False, True)

    def test_unknown_m1_pending_cannot_recover_or_fall_back_when_active_absent(self):
        snapshot = self.draft(False, True)
        self.store.begin_apply(snapshot["revision"], "m1-unresolved")
        for callback in (self.coordinator.compile_active, self.coordinator.reconcile):
            with self.assertRaises(ProxyConflict):
                callback()
        with self.assertRaises(ProxyApplyError):
            self.coordinator.recover()
        self.assertIsNotNone(self.store.recoverable_pending())
        self.assertFalse(any(call[0] in ("apply", "recover") for call in self.runtime.calls))

    def test_corrupt_and_uncertain_store_stop_without_guessing_direct(self):
        self.apply(transaction="old")
        state = self.store.path / "state.json"
        state.write_text("{}")
        with self.assertRaises(ProxyApplyError):
            self.coordinator.recover()
        self.assertFalse(self.runtime.running)
        self.assertEqual(state.read_text(), "{}")
        self.assertFalse(any(call[0] == "recover" for call in self.runtime.calls))

    def test_runtime_drift_from_active_revision_stops_before_auto_recovery(self):
        self.apply(transaction="old")
        direct = build_config(self.rows)
        self.runtime.apply(direct)
        with self.assertRaises(ProxyApplyError):
            self.coordinator.recover()
        self.assertFalse(self.runtime.running)
        self.assertTrue(self.store.load_active()["settings"]["relay_enabled"])
        self.assertFalse(any(call[0] == "recover" for call in self.runtime.calls))

    def test_ordinary_no_active_recovery_does_not_publish_proxy_draft(self):
        self.draft(True, True)
        self.runtime.apply(build_config(self.rows))
        self.runtime.running = False
        self.coordinator.recover()
        self.assertTrue(self.runtime.running)
        self.assertIsNone(self.store.load_active())
        self.assertEqual(self.resolutions, [])

    def test_public_results_never_expose_private_runtime_context_or_config(self):
        result = self.apply(True, True)
        raw = json.dumps(result)
        for secret in (FAKE_UUID, FAKE_PASSWORD, "runtime_context", "previous_core_revision", "policy_identity",
                       "self_addresses", "certificate_path", "key_path", "/test-only-fake", "DO_NOT_EXPOSE_INTERNAL_ERROR"):
            self.assertNotIn(secret, raw)

    def test_startup_changed_self_addresses_stops_before_old_core_launch(self):
        self.apply(transaction="old")
        self.runtime.running = False
        self.coordinator.address_provider = lambda _: ("127.0.0.1", "192.168.8.2", "8.8.8.8")
        before = len(self.resolutions)
        with self.assertRaises(ProxyApplyError):
            self.coordinator.recover()
        self.assertEqual(len(self.resolutions), before)
        self.assertFalse(self.runtime.running)
        self.assertFalse(any(call[0] == "recover" for call in self.runtime.calls))
        self.assertTrue(self.store.load_active()["settings"]["relay_enabled"])

    def test_startup_changed_ingress_snapshot_stops_before_old_core_launch(self):
        self.apply(transaction="old")
        self.runtime.running = False
        self.rows.append(row(2, 24444))
        with self.assertRaises(ProxyApplyError):
            self.coordinator.recover()
        self.assertFalse(any(call[0] == "recover" for call in self.runtime.calls))
        self.assertFalse(self.runtime.running)

    def test_pending_recovery_checks_current_self_snapshot_before_restoring(self):
        self.apply(transaction="old")
        snapshot = self.draft(False, False)
        with patch.object(self.store, "commit_apply", side_effect=SimulatedCrash()):
            with self.assertRaises(SimulatedCrash):
                self.coordinator.apply(snapshot["revision"], "new")
        self.coordinator.address_provider = lambda _: ("127.0.0.1", "8.8.4.4")
        self.runtime.calls.clear()
        with self.assertRaises(ProxyApplyError):
            self.coordinator.recover()
        self.assertFalse(self.runtime.running)
        self.assertEqual(self.runtime.calls, [("stop", False)])
        self.assertEqual(self.store.recoverable_pending()["transaction_id"], "new")

    def test_reapply_same_core_preserves_latest_resolution_timestamp(self):
        first = self.apply(transaction="zzzz")
        self.resolved_at = 99
        second = self.coordinator.apply(first["revision"], "aaaa")
        self.assertEqual(second["candidate_revision"], first["candidate_revision"])
        self.assertEqual(self.store.load_active()["runtime_context"]["endpoint"]["resolved_at"], 99)
        self.coordinator.reconcile(transaction_id="node-change")
        self.assertEqual(self.store.load_active()["runtime_context"]["endpoint"]["resolved_at"], 99)


    def _actual_runtime_algorithm(self):
        # Exercise the real CoreRuntime.apply/restore ordering, mocking only
        # external process validation/launch because this is a unit test.
        self.runtime = CoreRuntime("sing-box", self.data / "fake-binary", self.data / "runtime")
        launches = []
        reject = set()
        def launch(path):
            launches.append(path.stem)
            if path.stem in reject:
                raise CoreError("Synthetic launch failure")
            self.runtime.process = SimpleNamespace(poll=lambda: None, pid=12345)
        def terminate():
            self.runtime.process = None
        for name, effect in (("check", lambda *_: None), ("_launch", launch), ("_terminate", terminate)):
            patcher = patch.object(self.runtime, name, side_effect=effect)
            patcher.start(); self.addCleanup(patcher.stop)
        self.addCleanup(self.runtime.close)
        self.coordinator = self.make_coordinator()
        return launches, reject

    def test_actual_runtime_cannot_relaunch_old_policy_before_changed_self_guard(self):
        launches, reject = self._actual_runtime_algorithm()
        old = self.apply(transaction="old")
        old_revision = old["candidate_revision"]
        self.coordinator.address_provider = lambda _: ("127.0.0.1", "192.168.8.2", "9.9.9.9")
        candidate = self.draft(True, True)
        compiled = self.coordinator._compile(self.store.load_draft(), self.rows)[0]
        reject.add(revision(compiled.config))
        launches.clear()
        with self.assertRaises(ProxyApplyError) as error:
            self.coordinator.apply(candidate["revision"], "new")
        self.assertEqual(error.exception.code, "RECOVERY_REQUIRED")
        self.assertEqual(launches, [revision(compiled.config)])
        self.assertNotIn(old_revision, launches)
        self.assertIsNone(self.runtime.process)
        self.assertEqual(self.store.load_active()["core_revision"], old_revision)
        self.assertEqual(self.store.recoverable_pending()["transaction_id"], "new")

    def test_actual_runtime_safe_rollback_occurs_only_through_coordinator_guard(self):
        launches, reject = self._actual_runtime_algorithm()
        old = self.apply(transaction="old")
        candidate = self.draft(True, True)
        compiled = self.coordinator._compile(self.store.load_draft(), self.rows)[0]
        reject.add(revision(compiled.config))
        launches.clear()
        with self.assertRaises(ProxyApplyError) as error:
            self.coordinator.apply(candidate["revision"], "new")
        self.assertEqual(error.exception.code, "APPLY_FAILED")
        self.assertEqual(launches, [revision(compiled.config), old["candidate_revision"]])
        self.assertTrue(self.runtime.status()["running"])
        self.assertIsNone(self.store.recoverable_pending())
        self.assertEqual(self.store.load_active()["core_revision"], old["candidate_revision"])


class ListenerPreflightTests(unittest.TestCase):
    def setUp(self):
        self.local = {"inbounds": [{"tag": "vui-relay-local", "type": "mixed", "listen": "127.0.0.1", "listen_port": 17891}]}
        self.runtime = SimpleNamespace(process=None)
        self.probes = []
        self.bind_error = None

    def fake_socket(self, family, socktype):
        parent = self
        class Probe:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def setsockopt(self, level, option, value):
                self.option = level, option, value
            def bind(self, address):
                parent.probes.append((family, socktype, address))
                if parent.bind_error is not None:
                    error = parent.bind_error(family, socktype)
                    if error:
                        raise OSError(error, "fake socket error")
        return Probe()

    def connection(self, *, family=socket.AF_INET, socktype=socket.SOCK_STREAM, ip="127.0.0.1", pid=333):
        return SimpleNamespace(laddr=SimpleNamespace(ip=ip, port=17891), family=family, type=socktype,
                               status="LISTEN" if socktype == socket.SOCK_STREAM else "NONE", pid=pid)

    def test_free_port_probes_all_four_family_protocol_pairs(self):
        with patch("app.services.proxy_apply.psutil.net_connections", return_value=[]), \
                patch("app.services.proxy_apply.socket.socket", side_effect=self.fake_socket):
            preflight_local_listener(self.local, self.runtime)
        self.assertEqual({(family, kind) for family, kind, _ in self.probes}, {
            (socket.AF_INET, socket.SOCK_STREAM), (socket.AF_INET, socket.SOCK_DGRAM),
            (socket.AF_INET6, socket.SOCK_STREAM), (socket.AF_INET6, socket.SOCK_DGRAM)})

    def test_any_foreign_tcp_udp_ipv4_ipv6_listener_blocks_before_bind(self):
        for family, kind in itertools.product((socket.AF_INET, socket.AF_INET6), (socket.SOCK_STREAM, socket.SOCK_DGRAM)):
            connection = self.connection(family=family, socktype=kind, ip="::" if family == socket.AF_INET6 else "0.0.0.0")
            with self.subTest(family=family, kind=kind), \
                    patch("app.services.proxy_apply.psutil.net_connections", return_value=[connection]), \
                    patch("app.services.proxy_apply.socket.socket", side_effect=self.fake_socket):
                with self.assertRaisesRegex(ProxyApplyError, "occupied"):
                    preflight_local_listener(self.local, self.runtime)
        self.assertEqual(self.probes, [])

    def test_exact_saved_core_owned_local_tcp_listener_may_remain_during_check(self):
        with tempfile.TemporaryDirectory() as directory:
            self.runtime.process = SimpleNamespace(pid=111, poll=lambda: None)
            self.runtime.active_config = lambda: copy.deepcopy(self.local)
            owner = SimpleNamespace(pid=111, children=lambda **_: [SimpleNamespace(pid=222)])
            self.bind_error = lambda family, kind: errno.EADDRINUSE if (family, kind) == (socket.AF_INET, socket.SOCK_STREAM) else None
            with patch("app.services.proxy_apply.psutil.Process", return_value=owner), \
                    patch("app.services.proxy_apply.psutil.net_connections", return_value=[self.connection(pid=222)]), \
                    patch("app.services.proxy_apply.socket.socket", side_effect=self.fake_socket):
                preflight_local_listener(self.local, self.runtime)
                with patch("app.services.proxy_apply.psutil.net_connections", return_value=[self.connection(ip="0.0.0.0", pid=222)]):
                    with self.assertRaisesRegex(ProxyApplyError, "occupied"):
                        preflight_local_listener(self.local, self.runtime)

    def test_running_runtime_without_private_config_accessor_has_no_path_fallback(self):
        self.runtime.process = SimpleNamespace(pid=111, poll=lambda: None)
        self.runtime.config_path = Mock(side_effect=AssertionError('No pathname fallback'))
        with patch('app.services.proxy_apply.psutil.net_connections') as connections:
            with self.assertRaises(ProxyApplyError) as caught:
                preflight_local_listener(self.local, self.runtime)
            self.assertEqual(caught.exception.code, 'LOCAL_PORT_UNAVAILABLE')
            connections.assert_not_called()
        self.runtime.active_config = lambda: None
        with self.assertRaises(ProxyApplyError): preflight_local_listener(self.local, self.runtime)
        self.runtime.config_path.assert_not_called()

    def test_replaced_saved_config_path_fails_before_external_read_or_socket_probe(self):
        from app.services import core_runtime as runtime_module
        with tempfile.TemporaryDirectory(prefix='vui-preflight-path-') as directory:
            root = Path(directory)
            runtime = CoreRuntime('sing-box', root / 'unused', root / 'runtime')
            self.addCleanup(runtime.close)
            runtime._claim()
            rev = revision(self.local)
            path = runtime.root / (rev + '.json')
            atomic_write(path, encode(self.local))
            atomic_write(runtime.state_file, encode({'revision': rev, 'enabled': True}))
            external = root / 'external.json'
            atomic_write(external, b'{"inbounds":[],"password":"outside-fake-secret"}')
            identity = (external.stat().st_dev, external.stat().st_ino)
            state, read = runtime.state, runtime_module.os.read
            external_reads = []
            def swap():
                saved = state()
                path.unlink()
                path.symlink_to(external)
                return saved
            def guarded_read(fd, count):
                info = runtime_module.os.fstat(fd)
                if (info.st_dev, info.st_ino) == identity:
                    external_reads.append(fd)
                return read(fd, count)
            runtime.process = SimpleNamespace(pid=111, poll=lambda: None)
            try:
                with patch.object(runtime, 'state', side_effect=swap), \
                     patch.object(runtime_module.os, 'read', side_effect=guarded_read), \
                     patch.object(Path, 'read_text', side_effect=AssertionError('No pathname read')) as pathname_read, \
                     patch('app.services.proxy_apply.psutil.net_connections') as connections, \
                     patch('app.services.proxy_apply.socket.socket') as probe:
                    with self.assertRaises(ProxyApplyError) as caught:
                        preflight_local_listener(self.local, runtime)
                    self.assertEqual(caught.exception.code, 'LOCAL_PORT_UNAVAILABLE')
                    connections.assert_not_called()
                    probe.assert_not_called()
                    pathname_read.assert_not_called()
                    self.assertEqual(external_reads, [])
            finally:
                runtime.process = None

    def test_unknown_owner_and_permission_failures_are_not_treated_as_free(self):
        for connections in ([self.connection(pid=None)], []):
            self.bind_error = lambda *_: errno.EACCES
            with patch("app.services.proxy_apply.psutil.net_connections", return_value=connections), \
                    patch("app.services.proxy_apply.socket.socket", side_effect=self.fake_socket):
                with self.assertRaises(ProxyApplyError):
                    preflight_local_listener(self.local, self.runtime)
        with patch("app.services.proxy_apply.psutil.net_connections", side_effect=PermissionError("fake permission")):
            with self.assertRaisesRegex(ProxyApplyError, "could not be verified"):
                preflight_local_listener(self.local, self.runtime)

    def test_tcp_time_wait_does_not_block_normal_core_restart_semantics(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            listener.listen(1)
            with socket.create_connection(("127.0.0.1", port)) as client:
                connection, _ = listener.accept()
                connection.shutdown(socket.SHUT_RDWR)
                connection.close()
                self.assertEqual(client.recv(1), b"")
        config = copy.deepcopy(self.local)
        config["inbounds"][0]["listen_port"] = port
        preflight_local_listener(config, self.runtime)

    def test_no_local_listener_does_not_probe_system_ports(self):
        with patch("app.services.proxy_apply.psutil.net_connections") as connections:
            preflight_local_listener({"inbounds": []}, self.runtime)
        connections.assert_not_called()

    def test_real_loopback_occupied_tcp_and_udp_are_detected_without_starting_core(self):
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            with self.subTest(kind=kind), socket.socket(socket.AF_INET, kind) as occupied:
                occupied.bind(("127.0.0.1", 0))
                if kind == socket.SOCK_STREAM:
                    occupied.listen(1)
                config = copy.deepcopy(self.local)
                config["inbounds"][0]["listen_port"] = occupied.getsockname()[1]
                with self.assertRaisesRegex(ProxyApplyError, "occupied"):
                    preflight_local_listener(config, self.runtime)


if __name__ == "__main__":
    unittest.main()
