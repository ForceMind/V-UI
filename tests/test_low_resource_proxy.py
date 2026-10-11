"""Safety contracts and opt-in actual pinned-core bounded proxy smoke."""
import http.client
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import low_resource_proxy as proxy


class LowResourceProxyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vui-resource-proxy-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.log = self.root / "rejection.log"
        self.log.write_text("")

    def test_server_adapter_retains_fake_auth_and_verified_tls_loopback(self):
        config = proxy.server_config(12345, self.root / "cert", self.root / "key")
        inbound = config["inbounds"][0]
        self.assertEqual(inbound["type"], "vless")
        self.assertEqual(inbound["listen"], "127.0.0.1")
        self.assertEqual(inbound["users"], [{"uuid": proxy.UUID, "flow": ""}])
        self.assertTrue(inbound["tls"]["enabled"])
        self.assertNotIn("transport", inbound)

    def test_client_has_no_direct_escape_and_validates_test_ca(self):
        config = proxy.client_config(12345, 23456, self.root / "ca")
        outbound = config["outbounds"][0]
        self.assertEqual(outbound["network"], "tcp")
        self.assertFalse(outbound["tls"]["insecure"])
        self.assertEqual(outbound["tls"]["server_name"], "vpn.example.test")
        config["outbounds"].append({"type": "direct", "tag": "direct"})
        with self.assertRaises(RuntimeError): proxy.assert_no_direct(config)

    def test_direct_log_is_rejected_and_adapter_import_restores_environment(self):
        self.log.write_text("outbound/direct[direct]: outbound connection")
        with self.assertRaises(RuntimeError): proxy.assert_no_direct_log(self.log)
        with patch.dict(os.environ, {"VUI_DATA_DIR": str(self.root / "original")}):
            proxy.server_config(12345, self.root / "cert", self.root / "key")
            self.assertEqual(os.environ["VUI_DATA_DIR"], str(self.root / "original"))

    def test_timeout_and_unrelated_or_stale_reason_never_pass(self):
        for text, offset in (("", 0), ("x509 unrelated\nunknown authority\n", 0),
                             ("x509 unknown authority\n", len("x509 unknown authority\n"))):
            self.log.write_text(text)
            with self.subTest(text=text, offset=offset), self.assertRaisesRegex(RuntimeError, "Missing actual"):
                proxy.require_rejection(lambda: (_ for _ in ()).throw(TimeoutError()), [], self.log,
                                        ("x509", "unknown authority"), offset=offset, timeout=0)

    def test_actual_reason_and_no_delivery_pass(self):
        def request():
            self.log.write_text("TLS: x509 certificate signed by unknown authority\n")
            raise http.client.RemoteDisconnected()
        result = proxy.require_rejection(request, [], self.log, ("x509", "unknown authority"), timeout=0)
        self.assertEqual(result["target_deliveries"], 0)
        self.assertEqual(result["attempts"], 1)

    def test_delivery_or_success_fails_despite_rejection_log(self):
        self.log.write_text("unknown uuid\n")
        deliveries = []
        def delivered():
            deliveries.append("unexpected")
            raise TimeoutError()
        for request in (delivered, lambda: (200, b"other"), lambda: (502, proxy.BODY)):
            with self.subTest(request=request), self.assertRaises(RuntimeError):
                proxy.require_rejection(request, deliveries, self.log, ("unknown uuid",), timeout=0)

    def test_acceptance_keeps_panel_only_phase_and_proxy_cleanup_before_backup(self):
        source = (Path(__file__).parents[1] / "scripts/low_resource_acceptance.py").read_text()
        self.assertLess(source.index('stage("panel_only_idle_60_seconds"'), source.index('            run_proxy_smoke('))
        self.assertLess(source.index('            run_proxy_smoke('), source.index('stage("stopped_backup"'))
        self.assertIn('payload / "cores" / tools.target_arch() / "sing-box"', source)

    def test_proxy_children_and_target_close_after_load_failure(self):
        processes, closed = [], []
        class FakeCore:
            def __init__(self, owner, command, log_path, env):
                self.process = self
                self.pid = 100 + len(processes)
                self.log_path = log_path
                self.stopped = False
                log_path.write_text("")
                processes.append(self)
                owner.callback(self.stop)
            def start(self, port): pass
            def stop(self): self.stopped = True
            def poll(self): return 0 if self.stopped else None
        def target(owner):
            owner.callback(lambda: closed.append(True))
            return 12345, []
        report = {}
        with patch.object(proxy, "CoreProcess", FakeCore), patch.object(proxy, "IDLE_SECONDS", 0), \
             patch.object(proxy, "start_http_target", target), \
             patch.object(proxy, "http_through", side_effect=RuntimeError("synthetic load failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic load failure"):
                proxy.run_proxy_smoke(self.root / "binary", self.root / "fixture",
                                      self.root / "logs", lambda name, action: action(),
                                      report, lambda: None)
        self.assertEqual(len(processes), 2)
        self.assertTrue(all(process.stopped for process in processes))
        self.assertEqual(closed, [True])
        self.assertFalse(report["proxy_workload"]["cleanup_complete"])

    @unittest.skipUnless(os.getenv("VUI_TEST_CORES"), "actual pinned core required")
    def test_actual_bundled_core_proxy_positive_negative_and_cleanup(self):
        stages, report, panel_checks = [], {}, []
        def stage(name, action):
            stages.append(name)
            return action()
        # Unit integration skips the 60-second resource idle; real cgroup gate
        # does not patch this constant and retains the complete idle interval.
        with patch.object(proxy, "IDLE_SECONDS", 0):
            proxy.run_proxy_smoke(Path(os.environ["VUI_TEST_CORES"]) / "sing-box",
                                  self.root / "fixture", self.root / "evidence", stage,
                                  report, lambda: panel_checks.append(True))
        evidence = report["proxy_workload"]
        self.assertEqual([row["target_deliveries"] for row in evidence["positive"]], [10, 100])
        self.assertEqual(set(evidence["negative"]), {"wrong_uuid", "wrong_ca"})
        self.assertTrue(evidence["cleanup_complete"])
        self.assertGreater(len(panel_checks), 3)
        self.assertEqual(len(stages), 7)


if __name__ == "__main__":
    unittest.main()
