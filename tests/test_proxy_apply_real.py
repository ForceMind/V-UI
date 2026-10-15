"""M2 commit-last transitions using the actual store, coordinator and core.

Fixture substitutions are limited to loopback endpoints, trust, source-bound
ordinary egress and a narrowed ingress listener. CoreRuntime, journal commits,
preflight ownership, rollback and recovery execute their production methods.
No fake apply/recover implementation is used. Runtime failures require CI on a
host where the fixed core can start; an enabled run never skips host failures.
"""
from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import socket
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services.core_manager import SingBoxAdapter
from app.services.core_runtime import CoreRuntime, revision
from app.services.proxy_apply import ProxyApplyCoordinator, ProxyApplyError
from app.services.proxy_policy import VerifiedEndpoint, compile_proxy_policy
from app.services.proxy_store import ProxyStore
from test_proxy_double_hop import (
    DoubleHopFixture, INGRESS_UUID, PASSWORD, PROFILE_ID, STATE_WALK, USERNAME,
)
from proxy_loopback_support import DIRECT_EGRESS, INGRESS, SNI, UPSTREAM


class SimulatedInterruption(BaseException):
    """An interruption at commit, after the real candidate is listening."""


@unittest.skipUnless(os.getenv("VUI_PROXY_LOOPBACK") == "1",
                     "real apply transitions require explicit VUI_PROXY_LOOPBACK=1")
class ProxyApplyRealTests(DoubleHopFixture):
    def setUp(self):
        super().setUp()
        self.lock = threading.RLock()
        self.store = ProxyStore(self.root/"store")
        self.store.read_snapshot()
        self.runtime_root = self.root/"runtime"
        self.runtime = CoreRuntime("sing-box", Path(self.singbox), self.runtime_root)
        self.addCleanup(lambda: self.runtime.close())
        self.rows = [SimpleNamespace(id=1, core="sing-box", protocol="vless", port=self.ingress_port,
            remark="isolated-ingress", tag="fixture-ingress", enable=True, expiry_time=0,
            settings={"users": [{"uuid": INGRESS_UUID}]}, stream_settings={"tls": {
                "enabled": True, "server_name": SNI,
                "certificate_path": str(self.cert), "key_path": str(self.key)}})]
        self.coordinator = self.make_coordinator()
        self.profile_saved = False
        self.transaction_sequence = 0

    def make_coordinator(self):
        return ProxyApplyCoordinator(self.store, self.runtime, self.build_base, self.lock,
            rows_provider=lambda: self.rows, resolver=self.resolve_fixture,
            address_provider=lambda _: (INGRESS,), compiler=self.compile_fixture)

    def build_base(self, rows):
        config = SingBoxAdapter().build_config(rows)
        config["log"] = {"level": "debug", "timestamp": False}
        config["outbounds"][0]["inet4_bind_address"] = DIRECT_EGRESS
        config["dns"] = {"servers": [{"type": "udp", "tag": "fixture-ordinary-dns",
            "server": INGRESS, "server_port": self.ordinary_dns_port}], "final": "fixture-ordinary-dns"}
        config["route"]["default_domain_resolver"] = "fixture-ordinary-dns"
        return config

    def resolve_fixture(self, profile_id, rev, profile, *, self_addresses):
        self.assertEqual(tuple(self_addresses), (INGRESS,))
        self.bootstrap_queries.append(profile["server"])
        return VerifiedEndpoint(profile_id, rev, profile["server"], (UPSTREAM,), 1792080000)

    def compile_fixture(self, base, rows, snapshot, **kwargs):
        compiled = compile_proxy_policy(base, rows, snapshot, policy=self.policy, **kwargs)
        config = deepcopy(compiled.config)
        for inbound in config["inbounds"]:
            if inbound["type"] == "vless":
                self.assertEqual(inbound["listen"], "::")
                inbound["listen"] = INGRESS
        self.last_compiled = replace(compiled, config=config)
        return self.last_compiled

    def draft(self, state):
        if not self.profile_saved:
            # Reuse the same validated fake upstream model as the compiler
            # topology, but save it through the real private store.
            _, _, fixture = self.compile_state("00")
            current = self.store.read_snapshot()
            added = self.store.save_upstream(fixture["upstreams"][PROFILE_ID], current["revision"])
            self.profile_id = added["upstream_id"]
            self.profile_saved = True
        current = self.store.read_snapshot()
        settings = {key: value for key, value in current["settings"].items() if key != "local_password_set"}
        settings.update(local_proxy_enabled=state[0] == "1", relay_enabled=state[1] == "1",
                        selected_upstream_id=self.profile_id, local_username=USERNAME, local_port=self.local_port)
        return self.store.save_draft(settings, current["revision"], local_password=PASSWORD)

    def activate(self, state, **options):
        self.assertEqual(options, {})
        draft = self.draft(state)
        self.transaction_sequence += 1
        transaction = f"real-{self.transaction_sequence}"
        with patch.dict(os.environ, self.env):
            result = self.coordinator.apply(draft["revision"], transaction)
        self.assertTrue(result["applied"])
        self.assertIsNone(self.store.recoverable_pending())
        self.assertEqual(self.store.transaction(transaction)["phase"], "committed")
        self.compiled = self.last_compiled
        self.snapshot = self.store.load_active()
        self.current_state = state
        self.assert_active(state)
        return self.compiled

    def assert_active(self, state):
        active = self.store.load_active()
        core = self.runtime.state()
        self.assertEqual((active["settings"]["local_proxy_enabled"], active["settings"]["relay_enabled"]),
                         (state[0] == "1", state[1] == "1"))
        self.assertEqual(active["core_revision"], core["revision"])
        self.assertEqual(active["core_revision"], revision(self.compiled.config))
        self.assertEqual(self.runtime.status()["applied_revision"], core["revision"])
        self.assertTrue(core["enabled"])
        self.assertTrue(self.runtime.status()["running"])
        self.assertEqual(self.store.read_snapshot()["active"]["revision"], active["revision"])
        if state[0] == "1":
            with socket.create_connection((INGRESS, self.local_port), timeout=1):
                pass
        else:
            with self.assertRaises(ConnectionRefusedError):
                socket.create_connection((INGRESS, self.local_port), timeout=.5)

    def test_all_twelve_real_applies_commit_exact_runtime_and_remain_usable(self):
        for mode in ("tls", "reality"):
            with self.subTest(upstream=mode):
                if self.upstream:
                    self.upstream.stop()
                    self.runtime.close()
                    self.store = ProxyStore(self.root/("store-" + mode))
                    self.store.read_snapshot()
                    self.runtime = CoreRuntime("sing-box", Path(self.singbox), self.root/("runtime-" + mode))
                    self.coordinator = self.make_coordinator()
                    self.profile_saved = False
                self.start_upstream(mode)
                self.activate(STATE_WALK[0])
                if not self.clients:
                    self.start_clients()
                self.assert_state(STATE_WALK[0])
                seen = set()
                for old, new in zip(STATE_WALK, STATE_WALK[1:]):
                    with self.subTest(transition=old + "->" + new):
                        previous = self.store.load_active()
                        self.activate(new)
                        self.assertNotEqual(previous["revision"], self.store.load_active()["revision"])
                        self.assert_state(new)
                        seen.add((old, new))
                        print(f"real coordinator {mode} {old}->{new}: committed active/core SHA agree; actual routes verified")
                states = ("00", "10", "01", "11")
                self.assertEqual(seen, {(a, b) for a in states for b in states if a != b})

    def test_every_failed_switch_restores_complete_real_baseline(self):
        self.start_upstream("tls")
        self.activate(STATE_WALK[0])
        self.start_clients()
        for old, new in zip(STATE_WALK, STATE_WALK[1:]):
            with self.subTest(transition=old + "->" + new):
                self.activate(old)
                baseline, compiled = self.store.load_active(), self.compiled
                draft = self.draft(new)
                transaction = "fail-" + old + "-" + new
                # The candidate core actually starts. Inject only the final
                # storage call, forcing production restore of the old SHA.
                with patch.object(self.store, "commit_apply", side_effect=OSError("fixture commit failure")):
                    with patch.dict(os.environ, self.env), self.assertRaises(ProxyApplyError) as caught:
                        self.coordinator.apply(draft["revision"], transaction)
                self.assertEqual(caught.exception.code, "APPLY_FAILED")
                self.compiled = compiled
                self.snapshot = self.store.load_active()
                self.current_state = old
                self.assertEqual(self.snapshot["revision"], baseline["revision"])
                self.assertEqual(self.store.transaction(transaction)["phase"], "aborted")
                self.assertIsNone(self.store.recoverable_pending())
                self.assert_active(old)
                self.assert_state(old)
                print(f"real coordinator failed {old}->{new}: old committed routes restored; zero mixed state")

    def test_occupied_tcp_udp_v4_v6_ports_block_before_disrupting_relay(self):
        self.start_upstream("tls")
        self.activate("01")
        self.start_clients()
        baseline, compiled = self.store.load_active(), self.compiled
        for family, address in ((socket.AF_INET, INGRESS), (socket.AF_INET6, "::1")):
            for transport in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
                with self.subTest(family=family, transport=transport):
                    draft = self.draft("11")
                    with socket.socket(family, transport) as blocker:
                        if family == socket.AF_INET6:
                            blocker.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                        blocker.bind((address, self.local_port))
                        if transport == socket.SOCK_STREAM:
                            blocker.listen(1)
                        before_pid = self.runtime.status()["pid"]
                        with patch.dict(os.environ, self.env), self.assertRaises(ProxyApplyError) as caught:
                            self.coordinator.apply(draft["revision"], f"blocked-{family}-{transport}")
                        self.assertEqual(caught.exception.code, "LOCAL_PORT_OCCUPIED")
                        self.assertEqual(self.runtime.status()["pid"], before_pid)
                        self.assertEqual(self.store.load_active()["revision"], baseline["revision"])
                        self.assertIsNone(self.store.recoverable_pending())
                        self.compiled, self.snapshot, self.current_state = compiled, self.store.load_active(), "01"
                        # Do not probe local-off while this deliberate foreign
                        # listener is present; prove the existing node still
                        # uses the committed upstream instead.
                        self.success(self.clients["singbox"][0], auth=None)
                        self.success(self.clients["mihomo"][0], auth=None)
                    self.assert_active("01")
        self.activate("11")
        self.assert_state("11")

    def test_pending_restart_restores_committed_relay_then_exact_active_core(self):
        self.start_upstream("tls")
        self.activate("11")
        self.start_clients()
        self.assert_state("11")
        baseline, compiled = self.store.load_active(), self.compiled
        draft = self.draft("00")
        with patch.object(self.store, "commit_apply", side_effect=SimulatedInterruption()):
            with patch.dict(os.environ, self.env), self.assertRaises(SimulatedInterruption):
                self.coordinator.apply(draft["revision"], "interrupted-disable")
        pending = self.store.recoverable_pending()
        self.assertEqual(pending["phase"], "prepared")
        self.assertEqual(pending["base_active_revision"], baseline["revision"])
        self.assertFalse(self.runtime.status()["running"])
        # Release ownership and reconstruct both objects, as after a restart.
        self.runtime.close()
        self.store = ProxyStore(self.root/"store")
        self.runtime = CoreRuntime("sing-box", Path(self.singbox), self.runtime_root)
        self.coordinator = self.make_coordinator()
        with patch.dict(os.environ, self.env):
            recovered = self.coordinator.recover()
        self.assertTrue(recovered["recovered"])
        self.assertIsNone(self.store.recoverable_pending())
        self.assertEqual(self.store.transaction("interrupted-disable")["phase"], "aborted")
        self.compiled, self.snapshot, self.current_state = compiled, self.store.load_active(), "11"
        self.assertEqual(self.snapshot["revision"], baseline["revision"])
        self.assert_active("11")
        self.assert_state("11")
        # A second clean restart must use the same committed active/core pair,
        # never the newer still-disabled draft.
        self.runtime.close()
        self.runtime = CoreRuntime("sing-box", Path(self.singbox), self.runtime_root)
        self.coordinator = self.make_coordinator()
        bootstrap = list(self.bootstrap_queries)
        with patch.dict(os.environ, self.env):
            self.coordinator.recover()
        self.assertEqual(self.bootstrap_queries, bootstrap)
        self.assert_active("11")
        self.assert_state("11")


if __name__ == "__main__":
    unittest.main()
