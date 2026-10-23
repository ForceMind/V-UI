"""Strict-export caller controls with fake sockets/cores, not loopback evidence.

Run the actual exported_client and unchanged allocator/preflight. Only kernel
reads, socket/process observations and core preparation/start are simulated;
these controls neither reserve ports nor identify an old CI collision's owner.
"""
from contextlib import contextmanager, ExitStack, nullcontext
import errno
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import yaml

from app.services import proxy_apply
from proxy_loopback_support import DOH, INGRESS, SNI
import tcp_upstream_export_helpers as helper
from test_proxy_exports import ExportFixture
import test_proxy_exports_real as real
from test_tcp_fixture_ports import kernel_values


def connection(port=20000, kind=socket.SOCK_STREAM, address=INGRESS, pid=4242):
    return SimpleNamespace(laddr=SimpleNamespace(ip=address, port=port), type=kind,
                           family=socket.AF_INET, status=real.psutil.CONN_LISTEN, pid=pid)


class StrictExportFixturePortTests(ExportFixture, unittest.TestCase):
    def setUp(self):
        self.setup_export()
        self.commit()

    @contextmanager
    def fixture(self, *, candidates=(20000,), occupied=(), previous=()):
        subject = real.StrictExportLoopbackTests()
        subject.rev, subject.doh_port, subject.env = self.rev, 24446, {"GODEBUG": "gctrace=0"}
        events, probes, sockets = [], [], []
        process = SimpleNamespace(process=SimpleNamespace(pid=4242))
        state = SimpleNamespace(subject=subject, events=events, probes=probes, process=process)

        class FakeSocket:
            def __init__(self, family, kind):
                self.family, self.kind, self.closed = family, kind, False
                sockets.append(self)
            def __enter__(self): return self
            def __exit__(self, *args): self.closed = True
            def setsockopt(self, *args): pass
            def bind(self, address):
                identity = self.family, self.kind, address[1]
                probes.append(identity)
                if identity in occupied:
                    raise OSError(errno.EADDRINUSE, "synthetic occupied DNS port")

        def exported(*args):
            events.append("export")
            return self.exporter.export(*args)

        def mixed_port():
            events.append("mixed")
            return 40000

        def allocate():
            events.append("allocate")
            return helper.local_proxy_port()

        def sample(population, count):
            self.assertEqual(count, min(128, len(population)))
            self.assertTrue(all(10000 <= port <= 65535 and not 32768 <= port <= 60999
                                for port in population))
            self.assertTrue(set(candidates).issubset(population))
            return list(candidates)

        def prepare(label, config, **kwargs):
            events.append("prepare")
            state.config, state.options = config, kwargs
            return process

        def dns_probe(endpoint, *, timeout):
            events.append("dns-probe")
            state.endpoint = endpoint
            self.assertEqual(timeout, 2)
            return nullcontext()

        def observe(*, kind):
            events.append("ownership")
            self.assertEqual(kind, "inet")
            return [connection(state.endpoint[1], kind) for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM)]

        subject.exporter = SimpleNamespace(export=Mock(side_effect=exported))
        subject.process = Mock(side_effect=prepare)
        subject.start_process = Mock(side_effect=lambda *args: events.append("start"))
        with ExitStack() as stack:
            stack.enter_context(kernel_values(floor="10000"))
            rng = stack.enter_context(patch.object(helper.random, "SystemRandom"))
            rng.return_value.sample.side_effect = sample
            state.sample = rng.return_value.sample
            stack.enter_context(patch.object(socket, "socket", FakeSocket))
            stack.enter_context(patch.object(proxy_apply.psutil, "net_connections", return_value=list(previous)))
            state.guard = stack.enter_context(patch.object(proxy_apply, "preflight_local_listener",
                                                           wraps=proxy_apply.preflight_local_listener))
            state.mixed = stack.enter_context(patch.object(real, "listen_port", side_effect=mixed_port))
            # create=True permits running these controls against the unmodified
            # caller for a behavioral red baseline, not merely a missing import.
            state.choose = stack.enter_context(patch.object(real, "allocate_dns_fixture_port",
                                                            side_effect=allocate, create=True))
            state.connect = stack.enter_context(patch.object(socket, "create_connection", side_effect=dns_probe))
            state.owner = stack.enter_context(patch.object(real.psutil, "Process"))
            state.connections = state.owner.return_value.net_connections
            state.connections.side_effect = observe
            launch = stack.enter_context(patch.object(real.subprocess, "Popen",
                side_effect=AssertionError("These controls must never launch subprocesses")))
            yield state
            launch.assert_not_called()
        self.assertTrue(all(probe.closed for probe in sockets))

    def test_both_formats_allocate_once_before_preparation_with_exact_dns_placement(self):
        for kind in ("sing-box", "mihomo"):
            before = yaml.safe_load(self.exported(kind).content)
            for wrong in (False, True):
                with self.subTest(kind=kind, wrong=wrong), self.fixture() as state:
                    result = state.subject.exported_client(kind, wrong_dns_identity=wrong)
                    self.assertEqual(result, (40000, state.process, 20000))
                    self.assertEqual(state.events, ["export", "mixed", "allocate", "prepare", "start",
                                                    "dns-probe", "ownership"])
                    state.choose.assert_called_once_with()
                    state.mixed.assert_called_once_with()
                    state.subject.exporter.export.assert_called_once_with(kind, self.rev, 1, "8.8.8.8")
                    state.subject.start_process.assert_called_once_with(state.process, 40000)
                    state.owner.assert_called_once_with(4242)
                    self.assertEqual(state.endpoint, (INGRESS, 20000))
                    self.assertEqual(set(state.probes), {(family, protocol, 20000)
                        for family in (socket.AF_INET, socket.AF_INET6)
                        for protocol in (socket.SOCK_STREAM, socket.SOCK_DGRAM)})
                    self.assertIsNone(state.guard.call_args.args[1].process)
                    state.sample.assert_called_once()
                    self.assertEqual(state.options["client"], "singbox" if kind == "sing-box" else "mihomo")
                    self.assertEqual(state.options["env"]["GODEBUG"],
                                     "gctrace=0,http2debug=1" if kind == "sing-box" and wrong else "gctrace=0")
                    identity = real.WRONG_DNS_IDENTITY if wrong else SNI
                    config = state.config
                    if kind == "sing-box":
                        self.assertEqual([item["listen_port"] for item in config["inbounds"]], [40000, 20000])
                        self.assertEqual(config["inbounds"][1]["listen"], INGRESS)
                        server = config["dns"]["servers"][0]
                        self.assertEqual((server["server"], server["server_port"]), (DOH, 24446))
                        self.assertEqual(server["tls"]["server_name"], identity)
                        self.assertEqual(config["route"], before["route"])
                    else:
                        self.assertEqual(config["mixed-port"], 40000)
                        self.assertEqual(config["dns"]["listen"], INGRESS + ":20000")
                        for name in ("nameserver", "default-nameserver", "proxy-server-nameserver"):
                            self.assertEqual(config["dns"][name], [value.replace("1.1.1.1", DOH + ":24446")
                                .replace("cloudflare-dns.com", identity) for value in before["dns"][name]])
                        self.assertEqual(config["rules"], before["rules"])

    def test_real_preflight_rejects_udp_occupancy_and_live_previous_client(self):
        cases = [("udp-v4", [(socket.AF_INET, socket.SOCK_DGRAM, 20000)], []),
                 ("udp-v6", [(socket.AF_INET6, socket.SOCK_DGRAM, 20000)], []),
                 ("previous-client", [], [connection()])]
        for kind in ("sing-box", "mihomo"):
            for label, occupied, previous in cases:
                with self.subTest(kind=kind, case=label), self.fixture(
                        candidates=(20000, 20001), occupied=occupied, previous=previous) as state:
                    self.assertEqual(state.subject.exported_client(kind)[2], 20001)
                    state.choose.assert_called_once_with()
                    state.sample.assert_called_once()
                    self.assertEqual([call.args[0]["inbounds"][0]["listen_port"]
                                      for call in state.guard.call_args_list], [20000, 20001])
                    self.assertTrue(all(call.args[1].process is None for call in state.guard.call_args_list))
                    self.assertTrue({(family, protocol, 20001)
                        for family in (socket.AF_INET, socket.AF_INET6)
                        for protocol in (socket.SOCK_STREAM, socket.SOCK_DGRAM)}.issubset(state.probes))
                    state.subject.process.assert_called_once()
                    self.assertEqual(state.endpoint, (INGRESS, 20001))

    def test_allocator_errors_propagate_without_preparation_or_reselection(self):
        for kind in ("sing-box", "mihomo"):
            for code in ("LOCAL_PORT_OCCUPIED", "LOCAL_PORT_UNAVAILABLE"):
                with self.subTest(kind=kind, code=code), self.fixture(candidates=range(20000, 20128)) as state:
                    failure = proxy_apply.ProxyApplyError(code)
                    state.guard.side_effect = failure
                    expected = AssertionError if code == "LOCAL_PORT_OCCUPIED" else type(failure)
                    with self.assertRaises(expected) as caught:
                        state.subject.exported_client(kind)
                    if code == "LOCAL_PORT_UNAVAILABLE":
                        self.assertIs(caught.exception, failure)
                    else:
                        self.assertIn("128 candidate limit", str(caught.exception))
                    state.choose.assert_called_once_with()
                    state.sample.assert_called_once()
                    self.assertEqual(state.guard.call_count, 128 if code == "LOCAL_PORT_OCCUPIED" else 1)
                    self.assertEqual(len({call.args[0]["inbounds"][0]["listen_port"]
                                          for call in state.guard.call_args_list}), state.guard.call_count)
                    state.mixed.assert_called_once_with()
                    state.subject.process.assert_not_called()
                    state.subject.start_process.assert_not_called()
                    state.connect.assert_not_called()

    def test_preparation_start_and_dns_readiness_failures_do_not_retry_or_reselect(self):
        for kind in ("sing-box", "mihomo"):
            for stage in ("prepare", "start", "dns-probe"):
                with self.subTest(kind=kind, stage=stage), self.fixture() as state:
                    failure = OSError(errno.EADDRINUSE, "synthetic late collision after preselection")
                    operation = {"prepare": state.subject.process, "start": state.subject.start_process,
                                 "dns-probe": state.connect}[stage]
                    operation.side_effect = failure
                    with self.assertRaises(OSError) as caught:
                        state.subject.exported_client(kind)
                    self.assertIs(caught.exception, failure)
                    operation.assert_called_once()
                    state.choose.assert_called_once_with()
                    state.sample.assert_called_once()
                    state.mixed.assert_called_once_with()
                    self.assertEqual(state.subject.start_process.call_count, int(stage != "prepare"))
                    state.owner.assert_not_called()

    def test_original_exact_loopback_tcp_and_udp_ownership_assertions_remain_fatal(self):
        invalid = [[], [connection()], [connection(kind=socket.SOCK_DGRAM)],
                   [connection(address="0.0.0.0"), connection(kind=socket.SOCK_DGRAM)],
                   [connection(), connection(kind=socket.SOCK_DGRAM, address="::1")],
                   [connection(port=20001), connection(port=20001, kind=socket.SOCK_DGRAM)]]
        for kind in ("sing-box", "mihomo"):
            for index, connections in enumerate(invalid):
                with self.subTest(kind=kind, case=index), self.fixture() as state:
                    state.connections.side_effect = None
                    state.connections.return_value = connections
                    with self.assertRaises(AssertionError):
                        state.subject.exported_client(kind)
                    state.choose.assert_called_once_with()
                    state.subject.start_process.assert_called_once_with(state.process, 40000)
                    state.connections.assert_called_once_with(kind="inet")
