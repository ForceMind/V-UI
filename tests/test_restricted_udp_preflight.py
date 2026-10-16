"""Bounded D2 feasibility; production remains TCP-only until separate gates.

VUI_RESTRICTED_UDP_PREFLIGHT=1 opts into fixed-core runtime. Runtime startup,
permissions, absent actual delivery or absent fresh rejection reasons fail.
"""
from copy import deepcopy
import os
import socket
import struct
import time
from pathlib import Path
import subprocess
import unittest

from restricted_udp_preflight_helpers import (
    RestrictedUdpFixture, RawXudpAssociation, INGRESS, UPSTREAM, TARGET, DENIED,
    UPSTREAM_EGRESS, WRONG_UUID, xudp_address, xudp_frame, decode_xudp_address,
    assert_xudp_rejection_response,
)
from udp_dns_capability_helpers import SocksDatagrams


@unittest.skipUnless(os.getenv("VUI_RESTRICTED_UDP_PREFLIGHT") == "1", "D2 runtime opt-in required")
class RestrictedUdpPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cores, mihomo = os.getenv("VUI_TEST_CORES"), os.getenv("VUI_TEST_MIHOMO")
        if not cores or not mihomo:
            raise AssertionError("D2 preflight requires both fixed binary paths")
        for command, prefix in (([str(Path(cores) / "sing-box"), "version"], "sing-box version 1.14.2\n"),
                                ([mihomo, "-v"], "Mihomo Meta v1.19.32 ")):
            result = subprocess.run(command, text=True, capture_output=True, timeout=15)
            output = result.stdout + result.stderr
            if result.returncode or not output.startswith(prefix):
                raise AssertionError("Fixed-core pin mismatch: " + output)
            print("D2 preflight pin: " + output.splitlines()[0], flush=True)

    def test_01_naked_mihomo_tls_xudp_ipv4_double_hop(self):
        with RestrictedUdpFixture(self, self._testMethodName) as fixture:
            fixture.target.direct(b"D2-direct-reachable-control")
            fixture.start("mihomo")
            fixture.echo(b"D2-verified-TLS-XUDP-device-domestic-overseas")

    def test_02_naked_singbox_tls_xudp_ipv4_double_hop(self):
        with RestrictedUdpFixture(self, self._testMethodName) as fixture:
            fixture.target.direct(b"D2-direct-reachable-control")
            fixture.start("singbox")
            fixture.echo(b"D2-verified-TLS-XUDP-device-domestic-overseas")

    def guarded_echo(self, kind):
        with RestrictedUdpFixture(self, self._testMethodName) as f:
            f.target.direct(b"guarded-control")
            f.start(kind, domestic=f.guarded_config())
            for payload in (b"x", bytes(range(256)) * 4 + bytes(176)):
                f.echo(payload)
            f.audit()

    def test_03_guarded_mihomo_exact_payload_address_and_port(self):
        self.guarded_echo("mihomo")

    def test_04_guarded_singbox_exact_payload_address_and_port(self):
        self.guarded_echo("singbox")

    def upstream_faults(self, kind):
        for fault in ("uuid", "ca", "sni", "stopped"):
            with self.subTest(fault=fault), RestrictedUdpFixture(self, self._testMethodName + "-" + fault) as f:
                config = f.guarded_config()
                f.target.direct(b"negative-target-reachable")
                f.start(kind, domestic=config)
                f.echo(b"before-fault-positive")
                bad, env = deepcopy(config), None
                observed, reasons = None, None
                if fault == "uuid":
                    bad["outbounds"][0]["uuid"] = WRONG_UUID
                    observed, reasons = f.upstream, ("unknown uuid",)
                elif fault == "ca":
                    env = {**f.env, "SSL_CERT_FILE": str(f.wrong_ca)}
                    reasons = ("x509", "unknown authority")
                elif fault == "sni":
                    bad["outbounds"][0]["tls"]["server_name"] = "wrong.example.test"
                    reasons = ("x509", "not wrong.example.test")
                else:
                    f.upstream.stop()
                    self.assertIsNotNone(f.upstream.process.poll())
                    with self.assertRaises(ConnectionRefusedError):
                        socket.create_connection((UPSTREAM, f.upstream_port), timeout=.2)
                    reasons = ("connect: connection refused",)
                f.restart_domestic_and_device(kind, bad, domestic_env=env)
                f.reject(b"must-not-deliver-" + fault.encode(), observed or f.domestic, reasons)
                if fault == "stopped":
                    f.upstream.start(f.upstream_port, UPSTREAM)
                f.restart_domestic_and_device(kind, config)
                f.echo(b"authorized-recovery")
                f.audit()  # Lifetime ledger still forbids the earlier negative.

    def test_05_mihomo_upstream_uuid_ca_sni_stop_recovery(self):
        self.upstream_faults("mihomo")

    def test_06_singbox_upstream_uuid_ca_sni_stop_recovery(self):
        self.upstream_faults("singbox")

    def device_faults(self, kind):
        for fault in ("uuid", "ca", "sni"):
            with self.subTest(fault=fault), RestrictedUdpFixture(self, self._testMethodName + "-" + fault) as f:
                config = f.guarded_config()
                f.target.direct(b"device-negative-target-reachable")
                f.start(kind, domestic=config)
                f.echo(b"before-device-fault")
                bad, env = f.client_config(kind), None
                node = bad["proxies"][0] if kind == "mihomo" else bad["outbounds"][0]
                if fault == "uuid":
                    node["uuid"] = WRONG_UUID
                    reasons = ("unknown uuid",)
                elif fault == "ca":
                    env = {**f.env, "SSL_CERT_FILE": str(f.wrong_ca)}
                    reasons = ("x509", "unknown authority")
                else:
                    if kind == "mihomo":
                        node["servername"] = "wrong.example.test"
                    else:
                        node["tls"]["server_name"] = "wrong.example.test"
                    reasons = ("x509", "not wrong.example.test")
                f.restart_domestic_and_device(kind, config, client=bad, client_env=env)
                f.reject(b"bad-device-" + fault.encode(), f.domestic if fault == "uuid" else f.client, reasons)
                f.restart_domestic_and_device(kind, config)
                f.echo(b"device-authorized-recovery")

    def test_07_mihomo_device_uuid_ca_sni(self):
        self.device_faults("mihomo")

    def test_08_singbox_device_uuid_ca_sni(self):
        self.device_faults("singbox")

    def raw_domain_echo(self, f, host, payload):
        with RawXudpAssociation(f, host, f.target.port) as association:
            self.raw_echo(f, association, host, f.target.port, payload)

    def test_09_raw_xudp_doh_all_answers_private_mixed_and_fail_closed(self):
        with RestrictedUdpFixture(self, self._testMethodName) as f:
            config = f.guarded_config()
            denied = f.target_at(DENIED)
            f.target.direct(b"dns-allowed-reachable")
            denied.direct(b"dns-denied-reachable")
            f.start("mihomo", domestic=config)
            f.echo(b"literal-before-domain")
            name = "good.example.test"
            f.answers.records[name] = (TARGET,)
            f.expect_dns(name)
            self.raw_domain_echo(f, name, b"domain-detoured-doh")
            for label, records in (("private", (DENIED,)), ("mixed", (TARGET, DENIED)),
                                   ("reverse", (DENIED, TARGET)), ("ipv6-sibling", (TARGET, "::1")),
                                   ("public-ipv6-sibling", (TARGET, "2001:4860:4860::8888")),
                                   ("self", (INGRESS,)), ("upstream", (UPSTREAM,))):
                host = label + ".example.test"
                f.answers.records[host] = records
                f.expect_dns(host)
                f.raw_reject(("deny-" + label).encode(), f.domestic, ("reject",), host=host, target=denied)
            f.reject(b"literal-private", f.domestic, ("reject",), target=denied)
            f.audit()

    def test_10_raw_xudp_partial_dns_family_and_both_failed_no_fallback(self):
        with RestrictedUdpFixture(self, self._testMethodName) as f:
            config = f.guarded_config()
            f.target.direct(b"partial-dns-reachable")
            f.start("singbox", domestic=config)
            f.echo(b"literal-before-partial-dns")
            # Every successful answer is checked even when the other family fails.
            host = "failed-aaaa.example.test"
            f.answers.records[host] = (TARGET,)
            f.answers.failed.add((host, 28))
            f.expect_dns(host)
            self.raw_domain_echo(f, host, b"valid-a-failed-aaaa")
            host = "both-failed.example.test"
            f.answers.failed.add(host)
            f.expect_dns(host)
            f.raw_reject(b"dns-must-not-fallback", f.domestic,
                         ("dns: lookup failed for " + host, "servfail"), host=host)
            f.audit()

    def raw_echo(self, f, association, host, port, payload, *, actual=None):
        actual = actual or f.target
        actual.expect(UPSTREAM_EGRESS, payload)
        association.send(host, port, payload)
        got_host, got_port, got_payload = association.receive()
        expected_hosts = {actual.host}
        if host == association.initial[0] and port == actual.port:
            expected_hosts.add(host)
        self.assertIn(got_host, expected_hosts)
        self.assertEqual(got_port, actual.port, "XUDP reply port differs from actual target")
        self.assertEqual(got_payload, payload)
        actual.audit()

    def test_11_naked_raw_xudp_retarget_positive_control(self):
        with RestrictedUdpFixture(self, self._testMethodName) as f:
            denied = f.target_at(DENIED)
            denied.direct(b"retarget-trap-reachable")
            f.start("mihomo")
            with RawXudpAssociation(f, TARGET, f.target.port) as association:
                self.raw_echo(f, association, TARGET, f.target.port, b"first-raw-legitimate")
                self.raw_echo(f, association, DENIED, denied.port, b"naked-retarget-control", actual=denied)
            f.observations.append({"kind": "naked-retarget", "delivered_to_changed_target": True})

    def test_12_guarded_raw_xudp_cannot_retarget_private_self_upstream_or_port(self):
        with RestrictedUdpFixture(self, self._testMethodName) as f:
            config = f.guarded_config()
            traps = [f.target_at(host) for host in (DENIED, INGRESS, UPSTREAM, TARGET)]
            for index, trap in enumerate(traps):
                trap.direct(b"retarget-reachable-" + bytes([index]))
            f.start("mihomo", domestic=config)
            with RawXudpAssociation(f, TARGET, f.target.port) as association:
                self.raw_echo(f, association, TARGET, f.target.port, b"first-guarded-legitimate")
                changes = [(trap.host, trap.port) for trap in traps]
                changes += [("::1", traps[0].port), ("::ffff:" + DENIED, traps[0].port),
                            ("metadata-alias.example.test", traps[0].port),
                            (TARGET, 53), (TARGET, 443)]
                for index, (host, port) in enumerate(changes):
                    # Bound-target behavior may deliver to the original target;
                    # changing address/port must NEVER reach any changed target.
                    self.raw_echo(f, association, host, port, b"bound-retarget-" + bytes([index]))
                    for trap in traps:
                        trap.audit()
            f.observations.append({"kind": "guarded-retarget", "changes": len(changes),
                                   "outcome": "all kept initial checked target"})

    def test_13_guarded_raw_domain_rebinding_cannot_escape_checked_address(self):
        with RestrictedUdpFixture(self, self._testMethodName) as f:
            denied = f.target_at(DENIED)
            config = f.guarded_config()
            name = "rebind.example.test"
            f.answers.records[name] = (TARGET,)
            f.start("mihomo", domestic=config)
            f.expect_dns(name)
            with RawXudpAssociation(f, name, f.target.port) as association:
                self.raw_echo(f, association, name, f.target.port, b"checked-first-answer")
                f.answers.records[name] = (DENIED,)
                self.raw_echo(f, association, name, denied.port, b"keep-initial-binding")
                f.audit()  # No second DNS lookup inside the existing association.
            f.expect_dns(name)
            f.raw_reject(b"new-association-sees-rebind", f.domestic, ("reject",), host=name, target=denied)

    def test_14_guarded_udp_idle_drains_and_new_association_recovers(self):
        import psutil
        with RestrictedUdpFixture(self, self._testMethodName) as f:
            f.target.direct(b"idle-target-reachable")
            f.start("mihomo", domestic=f.guarded_config(udp_timeout="2s"))
            with RawXudpAssociation(f, TARGET, f.target.port) as association:
                self.raw_echo(f, association, TARGET, f.target.port, b"before-idle")
                process = psutil.Process(f.upstream.process.pid)
                deadline = time.monotonic() + 6
                def target_sockets():
                    return [entry for entry in process.net_connections(kind="udp4")
                            if entry.laddr and entry.laddr.ip == UPSTREAM_EGRESS]
                self.assertTrue(target_sockets(), "No live overseas UDP association observed")
                while target_sockets():
                    self.assertLess(time.monotonic(), deadline, "Idle UDP association did not close")
                    time.sleep(.05)
            f.echo(b"new-association-after-idle")
            f.observations.append({"kind": "idle-cleanup", "route_timeout": "2s", "max_wait_seconds": 6})

    def test_15_guarded_new_associations_reject_udp_53_443_and_low_ports(self):
        with RestrictedUdpFixture(self, self._testMethodName) as f:
            f.target.direct(b"low-port-control")
            f.start("mihomo", domestic=f.guarded_config())
            f.echo(b"high-port-positive")
            for port in (53, 443, 1023):
                # No DNS/QUIC service is started. Fresh route rejection plus
                # no DIRECT proves these ports never get an outbound attempt.
                f.reject(b"reject-low-port-" + str(port).encode(), f.domestic, ("reject",), port=port)


class RestrictedUdpWireFixtureTests(unittest.TestCase):
    def test_xudp_address_and_frame_exact_fixture_bytes(self):
        address = b"\x30\x39\x01\x7f\x00\x00\x03"
        self.assertEqual(xudp_address(TARGET, 12345), address)
        self.assertEqual(decode_xudp_address(address), (TARGET, 12345))
        self.assertEqual(xudp_frame(TARGET, 12345, b"abc", first=True),
                         b"\x00\x0c\x00\x00\x01\x01\x02" + address + b"\x00\x03abc")
        self.assertEqual(xudp_frame(TARGET, 12345, b"abc", first=False)[4], 2)
        for host in ("domain.example.test", "::ffff:" + DENIED):
            self.assertEqual(xudp_address(*decode_xudp_address(xudp_address(host, 18443))), xudp_address(host, 18443))
        for malformed in (b"", b"\0\1\1", b"\0\1\2\0", b"\0\1\7abc"):
            with self.assertRaises(AssertionError):
                decode_xudp_address(malformed)
        for response in (b"", b"\0\0", b"\0\0\0\4\0\0\3\2"):
            assert_xudp_rejection_response(response)
        for response in (b"\0", b"\0\0\0", b"\0\0\0\4\0\0\2\1",
                         b"\0\0\0\4\0\0\3\1"):
            with self.assertRaises(AssertionError):
                assert_xudp_rejection_response(response)


@unittest.skipUnless(os.getenv("VUI_RESTRICTED_UDP_PARSER") == "1", "D2 parser opt-in required")
class RestrictedUdpParserTests(unittest.TestCase):
    def test_candidate_shapes_with_exact_core_parser_only(self):
        RestrictedUdpPreflightTests.setUpClass()
        with RestrictedUdpFixture(self, self._testMethodName) as f:
            guarded = f.guarded_config()
            udp = next(rule for rule in guarded["route"]["rules"] if rule.get("network") == "udp")
            self.assertTrue(udp["udp_connect"])
            self.assertEqual(guarded["outbounds"][0]["network"], ["tcp", "udp"])
            self.assertEqual(udp["udp_timeout"], "30s")
            self.assertEqual(udp["port_range"], ["1024:65535"])
            for label, config, mihomo in (("overseas", f.upstream_config(), False),
                                          ("guarded", guarded, False),
                                          ("device-singbox", f.client_config("singbox"), False),
                                          ("device-mihomo", f.client_config("mihomo"), True)):
                with self.subTest(shape=label):
                    f.process(label, config, mihomo=mihomo)


if __name__ == "__main__":
    unittest.main()
