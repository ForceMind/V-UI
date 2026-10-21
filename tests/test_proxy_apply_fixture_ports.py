"""Execute the real transition callers with fake cores/network, not loopback QA.

The actual setup, drafts, compiler, private store, coordinator and test methods
run. Only the inherited network topology, core runtime and network assertions
are simulated. The original real competing-bind/rollback/revision guards stay
in test_proxy_apply_real.py and still require their separately enabled CI gate.
"""
from contextlib import contextmanager, ExitStack, nullcontext
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.services import proxy_apply
from app.services.proxy_policy import AddressPolicy
from app.services.proxy_store import ProxyStore
from proxy_loopback_support import DOH, INGRESS, TARGET, UPSTREAM
from test_proxy_apply import FakeRuntime
import test_proxy_apply_real as real


class ProxyApplyFixturePortTests(unittest.TestCase):
    @contextmanager
    def fixture(self, *, allocation_error=None):
        subject = real.ProxyApplyRealTests()
        events, drafts, routes, runtimes = [], [], [], {}
        with tempfile.TemporaryDirectory(prefix="vui-transition-ports-") as directory, ExitStack() as stack:
            def topology(case):
                events.append("topology")
                case.root = Path(directory)
                case.singbox = "/test-only-no-core"
                case.cert, case.key = case.root / "fake.pem", case.root / "fake.key"
                case.ingress_port, case.upstream_port, case.local_port = 24443, 24444, 40000
                case.ordinary_dns_port, case.doh_port = 24445, 24446
                case.policy = AddressPolicy._for_tests(allowed_addresses=(TARGET, UPSTREAM, DOH),
                                                       doh=(DOH, case.doh_port, real.SNI))
                # Valid-shaped fake public key; no private key or TLS server.
                case.public_key = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                case.bootstrap_queries, case.clients, case.env = [], {}, {}
                case.upstream, case.mode = None, "tls"

            def allocate():
                self.assertEqual(events, ["topology"])
                self.assertFalse(hasattr(subject, "store"))
                self.assertFalse(hasattr(subject, "runtime"))
                self.assertEqual(subject.local_port, 40000)
                events.append("allocate")
                if allocation_error is not None:
                    raise allocation_error
                return 20000

            def store(path):
                self.assertEqual(subject.local_port, 20000)
                events.append("store")
                return ProxyStore(path)

            def runtime(name, binary, path):
                self.assertEqual(subject.local_port, 20000)
                events.append("runtime")
                value = FakeRuntime()
                if path in runtimes:
                    # Reconstruct a new fake runtime with the persisted record
                    # and immutable candidates, not an already-running object.
                    previous = runtimes[path]
                    value.record, value.saved = previous.state(), previous.saved.copy()
                value.close = lambda: value.stop(persist=False)
                runtimes[path] = value
                return value

            original_save_draft = ProxyStore.save_draft

            def save_draft(store, settings, *args, **kwargs):
                self.assertEqual(subject.local_port, 20000)
                self.assertEqual(settings["local_port"], 20000)
                events.append("draft")
                drafts.append((subject.mode, settings.copy()))
                return original_save_draft(store, settings, *args, **kwargs)

            def start_upstream(mode):
                subject.mode = mode
                subject.upstream = SimpleNamespace(stop=Mock())

            def assert_routes(state):
                active = subject.store.load_active()
                self.assertEqual(active["settings"]["local_port"], 20000)
                self.assertEqual(subject.local_port, 20000)
                routes.append((subject.mode, state))

            def connection(endpoint, *, timeout):
                self.assertEqual(endpoint, (INGRESS, 20000))
                if not subject.store.load_active()["settings"]["local_proxy_enabled"]:
                    raise ConnectionRefusedError("synthetic local-off")
                return nullcontext()

            stack.enter_context(patch.object(real.DoubleHopFixture, "setUp", topology))
            choose = stack.enter_context(patch.object(real, "allocate_proxy_fixture_port", side_effect=allocate))
            new_store = stack.enter_context(patch.object(real, "ProxyStore", side_effect=store))
            new_runtime = stack.enter_context(patch.object(real, "CoreRuntime", side_effect=runtime))
            stack.enter_context(patch.object(ProxyStore, "save_draft", save_draft))
            preflight = stack.enter_context(patch.object(proxy_apply, "preflight_local_listener"))
            stack.enter_context(patch.object(real.socket, "create_connection", side_effect=connection))
            stack.enter_context(patch("builtins.print"))
            subject.start_upstream = start_upstream
            subject.start_clients = lambda: subject.clients.update({"fake": True})
            subject.assert_state = assert_routes
            state = SimpleNamespace(subject=subject, choose=choose, events=events, drafts=drafts,
                                    routes=routes, preflight=preflight, new_store=new_store,
                                    new_runtime=new_runtime)
            try:
                yield state
            finally:
                subject.doCleanups()

    def test_single_setup_allocation_precedes_draft_and_survives_tls_reality_walks(self):
        with self.fixture() as state:
            state.subject.setUp()
            self.assertEqual(state.events, ["topology", "allocate", "store", "runtime"])
            state.subject.test_all_twelve_real_applies_commit_exact_runtime_and_remain_usable()
            state.choose.assert_called_once_with()
            self.assertEqual(state.new_store.call_count, 2)
            self.assertEqual(state.new_runtime.call_count, 2)
            expected = [(mode, value) for mode in ("tls", "reality") for value in real.STATE_WALK]
            self.assertEqual(state.routes, expected)
            self.assertEqual([(mode, ("1" if settings["local_proxy_enabled"] else "0") +
                              ("1" if settings["relay_enabled"] else "0"))
                             for mode, settings in state.drafts], expected)

    def test_allocation_failure_is_fatal_before_store_runtime_or_first_draft(self):
        failure = AssertionError("synthetic allocator exhaustion")
        with self.fixture(allocation_error=failure) as state:
            with self.assertRaises(AssertionError) as caught:
                state.subject.setUp()
            self.assertIs(caught.exception, failure)
            state.choose.assert_called_once_with()
            state.new_store.assert_not_called()
            state.new_runtime.assert_not_called()
            self.assertEqual(state.drafts, [])
            self.assertEqual(state.subject.local_port, 40000)

    def test_all_twelve_failed_switches_keep_the_selected_port_during_rollback(self):
        with self.fixture() as state:
            state.subject.setUp()
            state.subject.test_every_failed_switch_restores_complete_real_baseline()
            state.choose.assert_called_once_with()
            self.assertEqual(len(state.drafts), 25)  # Initial + baseline/candidate per edge.
            self.assertEqual(state.routes, [("tls", old) for old in real.STATE_WALK[:-1]])
            self.assertEqual(state.subject.local_port, 20000)

    def test_interrupted_and_clean_restarts_reuse_the_same_port(self):
        with self.fixture() as state:
            state.subject.setUp()
            state.subject.test_pending_restart_restores_committed_relay_then_exact_active_core()
            state.choose.assert_called_once_with()
            self.assertEqual(state.new_runtime.call_count, 3)
            self.assertEqual(state.new_store.call_count, 2)
            self.assertEqual(len(state.drafts), 2)
            self.assertEqual(state.routes, [("tls", "11")] * 3)
            self.assertEqual(state.subject.local_port, 20000)

    def test_preflight_failure_propagates_without_reallocation_or_apply_retry(self):
        for code in ("LOCAL_PORT_OCCUPIED", "LOCAL_PORT_UNAVAILABLE"):
            with self.subTest(code=code), self.fixture() as state:
                state.subject.setUp()
                state.subject.activate("01")
                baseline = state.subject.store.load_active()
                state.subject.runtime.calls.clear()
                failure = proxy_apply.ProxyApplyError(code)
                state.preflight.reset_mock(side_effect=True)
                state.preflight.side_effect = failure
                with self.assertRaises(proxy_apply.ProxyApplyError) as caught:
                    state.subject.activate("11")
                self.assertIs(caught.exception, failure)
                state.choose.assert_called_once_with()
                state.preflight.assert_called_once()
                self.assertEqual([operation for operation, _ in state.subject.runtime.calls], ["check"])
                self.assertEqual(state.subject.store.load_active(), baseline)
                self.assertIsNone(state.subject.store.recoverable_pending())
                self.assertEqual(len(state.drafts), 2)
                self.assertEqual(state.subject.local_port, 20000)

    def test_core_apply_failure_is_not_retried_or_given_another_port(self):
        with self.fixture() as state:
            state.subject.setUp()
            state.subject.activate("01")
            baseline = state.subject.store.load_active()
            state.subject.runtime.calls.clear()
            state.subject.runtime.fail_apply = True
            with self.assertRaises(proxy_apply.ProxyApplyError) as caught:
                state.subject.activate("11")
            self.assertEqual(caught.exception.code, "APPLY_FAILED")
            state.choose.assert_called_once_with()
            self.assertEqual([operation for operation, _ in state.subject.runtime.calls],
                             ["check", "apply", "restore"])
            self.assertEqual(state.subject.store.load_active(), baseline)
            self.assertEqual(state.subject.store.transaction("real-2")["phase"], "aborted")
            self.assertIsNone(state.subject.store.recoverable_pending())
            self.assertEqual(len(state.drafts), 2)
            self.assertEqual(state.subject.local_port, 20000)


if __name__ == "__main__":
    unittest.main()
