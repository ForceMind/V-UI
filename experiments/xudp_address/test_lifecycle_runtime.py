"""Pure three-way controls: no core launch, sockets, real clock wait or /proc."""
from collections import Counter
from copy import deepcopy
import ast
import importlib.util
import io
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("lifecycle_runtime_tested", Path(__file__).with_name("lifecycle_runtime.py"))
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)
base = runtime.base


def provenance(phase="baseline"):
    commit = base.BASE_COMMIT
    version = f"1.14.2-vui-xudp-{phase}-experiment-{commit[:12]}"
    result = {"schema_version": 2, "kind": runtime.KIND, "phase": phase, "vui_source_commit": commit,
              "version": version, "binary_sha256": "a" * 64,
              **{key: {"version": value[0], "commit": value[1]} for key, value in base.SOURCE_COMMITS.items()},
              "build": {"go_version": "go1.25.14", "goos": "linux", "goarch": "amd64", "tags": [],
                        "cgo_enabled": "0", "gotoolchain": "local", "gowork": "off", "trimpath": True,
                        "buildvcs": False, "go_binary_sha256": "c" * 64,
                        "ldflags": "-X github.com/sagernet/sing-box/constant.Version=" + version},
              "source_hashes": {**runtime.SOURCE_FILE_HASHES, "route_lifecycle_test_sha256": "d" * 64},
              "phase_source_hashes": {"xudp": runtime.SOURCE_FILE_HASHES["sing_vmess_xudp"],
                                      "route_conn": runtime.SOURCE_FILE_HASHES["sing_box_route_conn"]},
              "patches": []}
    for name, field, enabled, file, old, new in (
        ("remoteaddr", "patch", phase != "baseline", "sing-vmess/xudp.go", "sing_vmess_xudp", "xudp"),
        ("lifecycle", "lifecycle_patch", phase == "lifecycle", "sing-box/route/conn.go", "sing_box_route_conn", "route_conn")):
        result[field] = {"applied": enabled, "sha256": runtime.PATCH_IDENTITIES[name]["sha256"] if enabled else None,
                         "path": "/" + name + ".patch" if enabled else None}
        if enabled:
            result["phase_source_hashes"][new] = runtime.PATCH_IDENTITIES[name]["after"]
            result["patches"].append({"name": name, "sha256": result[field]["sha256"], "path": result[field]["path"],
                                      "source_file": file, "before_sha256": result["source_hashes"][old],
                                      "after_sha256": result["phase_source_hashes"][new]})
    return result


def summary(methods, failures=()):
    failed = {item["method"] for item in failures}
    return {"tests_run": len(methods), "methods": [{"method": method, "status": "failed" if method in failed else "passed"} for method in methods],
            "failures": list(failures), "nonpass": [], "passed_methods": len(methods) - len(failed),
            "failed_methods": len(failed), "failure_records": len(failures)}


def groups(phase):
    failures = []
    if phase == "baseline":
        for name in base.BASELINE_FAILURES:
            for fault in base.FAULTS.get(name, (None,)):
                failures.append({"method": name, "fault": fault, "exception": "AssertionError",
                    "initial_port": {"case": name + ("-" + fault if fault else ""), "actual_port": 43000,
                                     "upstream_tcp_port": 43000, "expected_target_port": 33000}})
        dns = summary(base.DNS_METHODS, [{"method": base.DNS_METHODS[0], "exception": "AssertionError",
                                       "initial_port": {"case": base.DNS_METHODS[0], "actual_port": 43000,
                                                        "upstream_tcp_port": 43000, "expected_target_port": 33000}}])
    else:
        failures.append({"method": base.METHODS[12], "exception": "AssertionError",
                         "known_failure": {"kind": "frozen_domain_reply_excluded_by_original_allowlist"}})
        if phase == "patched":
            failures.append({"method": base.METHODS[13], "exception": "AssertionError",
                             "known_failure": {"kind": "original_six_second_idle_deadline"}})
        dns = summary(base.DNS_METHODS)
    return {"original": summary(base.METHODS, failures), "tcp": summary(base.TCP_METHODS), "dns_rebinding": dns}


def row(inode, protocol, local, remote, state="01", fd=9):
    return {"inode": inode, "fd": fd, "protocol": protocol, "local": {"host": local[0], "port": local[1]},
            "remote": {"host": remote[0], "port": remote[1]}, "proc_state": state}


def snapshot(moment, live=True, end=None, parent=True):
    domestic = [row(1, "tcp", ("127.0.0.1", 30000), ("0.0.0.0", 0), "0A")]
    overseas = [row(2, "tcp", ("127.0.0.2", 40000), ("0.0.0.0", 0), "0A")]
    if parent:
        domestic.append(row(3, "tcp", ("127.0.0.1", 30000), ("127.0.0.1", 50000)))
    if live:
        domestic.append(row(4, "tcp", ("127.0.0.1", 50001), ("127.0.0.2", 40000)))
        overseas += [row(5, "tcp", ("127.0.0.2", 40000), ("127.0.0.1", 50001)),
                     row(6, "udp", ("127.0.0.4", 60000), ("0.0.0.0", 0), "07")]
    return {"monotonic": moment, "sample_completed_monotonic": moment if end is None else end,
            "processes": [{"role": "domestic", "pid": 100, "races": [], "sockets": domestic},
                          {"role": "overseas", "pid": 200, "races": [], "sockets": overseas}]}


def leg():
    return runtime.paired_leg(snapshot(0, False, parent=False), snapshot(1), 40000)


def eof(moment=3):
    return {"kind": "core_eof_log", "role": "overseas", "phase": "natural_idle", "monotonic": moment,
            "line": "connection from 127.0.0.1:50001: EOF"}


def data_frame(session=0, payload=b"hello", first=False, address=b""):
    header = session.to_bytes(2, "big") + b"\x02\x01" + (b"\x02" + address if address else b"")
    return (b"\0\0" if first else b"") + len(header).to_bytes(2, "big") + header + len(payload).to_bytes(2, "big") + payload


def end_frame(session=0, flags=0):
    return b"\0\x04" + session.to_bytes(2, "big") + bytes((3, flags))


class ProvenanceTests(unittest.TestCase):
    def test_three_distinct_explicit_identities(self):
        versions = {runtime.validate_provenance(provenance(phase), phase) for phase in runtime.PHASES}
        self.assertEqual(len(versions), 3)
        self.assertTrue(all("experiment-" in value for value in versions))

    def test_rejects_old_schema_and_cross_phase_identity(self):
        for phase in runtime.PHASES:
            for field, value in (("schema_version", 1), ("kind", "vui-xudp-address-source-experiment"),
                                 ("version", "1.14.2"), ("phase", "unknown"), ("binary_sha256", base.STOCK_HASHES["sing-box"])):
                data = provenance(phase)
                data[field] = value
                with self.subTest(phase=phase, field=field), self.assertRaises(base.ExperimentError):
                    runtime.validate_provenance(data, phase)
        with self.assertRaises(base.ExperimentError):
            runtime.validate_provenance(provenance("lifecycle"), "patched")

    def test_rejects_patch_reordering_or_wrong_source_and_build(self):
        data = provenance("lifecycle")
        mutations = [lambda d: d["patches"].reverse(), lambda d: d["patches"].pop(),
                     lambda d: d["patches"][1].update(source_file="sing-vmess/xudp.go"),
                     lambda d: d["patches"][1].update(after_sha256="f" * 64),
                     lambda d: d["source_hashes"].update(sing_box_route_conn="f" * 64),
                     lambda d: d["phase_source_hashes"].update(route_conn="f" * 64),
                     lambda d: d["build"].update(gowork="auto"), lambda d: d["build"].update(tags=["with_quic"]),
                     lambda d: d["lifecycle_patch"].update(applied=False)]
        for mutate in mutations:
            changed = deepcopy(data)
            mutate(changed)
            with self.assertRaises(base.ExperimentError):
                runtime.validate_provenance(changed, "lifecycle")

    def test_baseline_requires_both_original_source_hashes(self):
        for name in ("xudp", "route_conn"):
            data = provenance()
            data["phase_source_hashes"][name] = "f" * 64
            with self.assertRaises(base.ExperimentError):
                runtime.validate_provenance(data, "baseline")

    def test_candidate_refuses_alias_and_checks_hash_before_execution(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "core")
            path.write_text("core")
            path.chmod(0o700)
            run = Mock()
            data = provenance()
            with self.assertRaisesRegex(base.ExperimentError, "aliases"):
                runtime.verify_candidate(path, data, "baseline", {"sing-box": {"path": str(path)}}, run)
            with self.assertRaisesRegex(base.ExperimentError, "SHA"):
                runtime.verify_candidate(path, data, "baseline", {}, run)
            run.assert_not_called()

    def test_cli_is_explicit_and_existing_output_refused(self):
        for phase in runtime.PHASES:
            parsed = runtime.parser().parse_args(["--phase", phase, "--candidate", "c", "--provenance", "p", "--output", "o"])
            self.assertEqual(parsed.phase, phase)
        with tempfile.TemporaryDirectory() as root, patch.object(runtime, "execute") as execute, patch("sys.stderr", io.StringIO()):
            self.assertEqual(runtime.main(["--phase", "lifecycle", "--candidate", "c", "--provenance", "p", "--output", root]), 2)
            execute.assert_not_called()


class OriginalMatrixTests(unittest.TestCase):
    def test_original_twenty_failure_counts_remain_visible(self):
        for phase, passed, failed in (("baseline", 7, 13), ("patched", 18, 2), ("lifecycle", 19, 1)):
            result = runtime.assess_original(phase, groups(phase))
            self.assertEqual((result["executed"], result["passed"], result["failed"]), (20, passed, failed))
            self.assertFalse(result["original_suite_passed"])

    def test_arbitrary_failures_skip_and_domain_conversion_rejected(self):
        for phase in runtime.PHASES:
            current = groups(phase)
            current["original"]["nonpass"].append({"kind": "skip"})
            with self.assertRaises(base.ExperimentError):
                runtime.assess_original(phase, current)
        for value in (None, {"kind": "unrelated"}):
            current = groups("lifecycle")
            current["original"]["failures"][0]["known_failure"] = value
            with self.assertRaises(base.ExperimentError):
                runtime.assess_original("lifecycle", current)
        current = groups("lifecycle")
        current["original"] = summary(base.METHODS)
        with self.assertRaises(base.ExperimentError):
            runtime.assess_original("lifecycle", current)

    def test_lifecycle_test14_failure_or_missing_recovery_not_accepted(self):
        with self.assertRaises(base.ExperimentError):
            runtime.assess_original("lifecycle", groups("patched"))
        # The original source requires real f.echo after the six-second loop.
        tree = ast.parse((base.ROOT / "tests/test_restricted_udp_preflight.py").read_text())
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == base.METHODS[13])
        echoes = [n for n in ast.walk(method) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "echo"]
        self.assertEqual(len(echoes), 1)
        self.assertEqual(ast.literal_eval(echoes[0].args[0]), b"new-association-after-idle")

    def test_known_domain_failure_uses_actual_frozen_function_and_traceback(self):
        path = base.ROOT / "tests/test_restricted_udp_preflight.py"
        tree = ast.parse(path.read_text())
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "raw_echo")
        namespace = {"UPSTREAM_EGRESS": "127.0.0.4"}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
        target = SimpleNamespace(host="127.0.0.3", port=33000, expect=Mock(), audit=Mock())
        f = SimpleNamespace(label=base.METHODS[12], target=target)
        association = SimpleNamespace(initial=("rebind.example.test", 33000), send=Mock(),
                                      receive=lambda: ("rebind.example.test", 33000, b"same"))
        try:
            namespace["raw_echo"](self, f, association, "rebind.example.test", 44000, b"same")
        except AssertionError:
            proof = runtime.known_failure(sys.exc_info())
        else:
            self.fail("Original domain assertion unexpectedly passed")
        self.assertEqual(proof["kind"], "frozen_domain_reply_excluded_by_original_allowlist")
        association.receive = lambda: ("bad.example.test", 33000, b"same")
        try:
            namespace["raw_echo"](self, f, association, "rebind.example.test", 44000, b"same")
        except AssertionError:
            self.assertIsNone(runtime.known_failure(sys.exc_info()))

    def test_process_counts_come_from_frozen_case_plan_plus_six_real_cases(self):
        # Independent frozen-source/client check: test10 is singbox despite name.
        self.assertEqual(base.case_client(base.METHODS[9]), "singbox")
        for phase, count, domestic, reconfigured in (("baseline", 118, 40, 6), ("patched", 174, 68, 34), ("lifecycle", 174, 68, 34)):
            roles = runtime.expected_roles(phase)
            self.assertEqual(sum(roles.values()), count)
            self.assertEqual(sum(n for (case, role), n in roles.items() if role in base.DOMESTIC_ROLES), domestic)
            self.assertEqual(sum(n for (case, role), n in roles.items() if role == "domestic-reconfigured"), reconfigured)
            for case in runtime.RESTART_CASES:
                self.assertEqual(sum(n for (name, role), n in roles.items() if name == case), 9)
        source = ast.parse(Path(runtime.__file__).read_text())
        restart = next(n for n in ast.walk(source) if isinstance(n, ast.FunctionDef) and n.name == "restart_case")
        calls = [n for n in ast.walk(restart) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "restart_domestic_and_device"]
        self.assertEqual(len(calls), 1)
        loop = next(n for n in restart.body if isinstance(n, ast.For))
        self.assertEqual(ast.unparse(loop.iter), "range(RESTARTS)")
        self.assertEqual(runtime.RESTARTS, 3)


class WireTests(unittest.TestCase):
    def test_split_wire_frames_keep_two_sessions_and_actual_end(self):
        reader = runtime.WireReader(lambda raw: ("127.0.0.3", 33000))
        packet = data_frame(0, b"first", first=True) + data_frame(1, b"second") + end_frame(0)
        frames = []
        for byte in packet:
            frames += reader.feed(bytes([byte]))
        self.assertEqual([(x["session"], x["status"], x["payload"]) for x in frames], [(0, 2, b"first"), (1, 2, b"second"), (0, 3, None)])
        self.assertFalse(reader.buffer)

    def test_wire_rejects_unknown_session_status_and_error_end(self):
        for data in (data_frame(3, first=True), b"\0\0\0\x04\0\0\x07\0", b"\0\0" + end_frame(flags=2), b"\0\x01"):
            with self.subTest(data=data), self.assertRaises(base.ExperimentError):
                runtime.WireReader(Mock()).feed(data)

    def test_host_port_and_payload_assertions_remain_strict(self):
        self.assertEqual(runtime.address_verdict("baseline", "127.0.0.3", 43000, b"same", "127.0.0.3", 33000, b"same", 43000), "known_baseline_address_failure")
        for phase, host, port, payload in (("patched", "127.0.0.3", 43000, b"same"),
                                          ("lifecycle", "127.0.0.7", 33000, b"same"),
                                          ("baseline", "127.0.0.3", 44000, b"same"),
                                          ("baseline", "127.0.0.3", 43000, b"wrong")):
            with self.assertRaises(base.ExperimentError):
                runtime.address_verdict(phase, host, port, payload, "127.0.0.3", 33000, b"same", 43000)

    def test_mux_sends_once_and_records_known_failure_without_promoting_it(self):
        with tempfile.TemporaryDirectory() as root:
            events = runtime.Events(Path(root, "events.json"), "test")
            stream = Mock()
            stream.recv.return_value = data_frame(0, b"same", first=True, address=b"address")
            raw = SimpleNamespace(stream=stream, __exit__=Mock())
            module = SimpleNamespace(TARGET="127.0.0.3", UPSTREAM_EGRESS="127.0.0.4",
                RawXudpAssociation=Mock(return_value=raw), decode_xudp_address=lambda _: ("127.0.0.3", 43000),
                xudp_frame=lambda h, p, data, first: b"\0\x04\0\0\1\1" + data)
            f = SimpleNamespace(target=SimpleNamespace(port=33000, expect=Mock(), audit=Mock()), upstream_port=43000)
            probe = runtime.MuxProbe(f, module, "baseline", events)
            probe.send(0, b"same")
            result = probe.reply(0)
            self.assertEqual(result["address_verdict"], "known_baseline_address_failure")
            stream.sendall.assert_called_once()
            self.assertNotIn("same", Path(root, "events.json").read_text())
            stream.recv.return_value = end_frame(0)
            probe.pump(runtime.time.monotonic() + 1)
            self.assertEqual(probe.ends[0]["wire_status"], 3)
            with self.assertRaises(base.ExperimentError):
                probe.send(0, b"again")

    def test_parent_eof_is_not_a_session_end_success(self):
        with tempfile.TemporaryDirectory() as root:
            events = runtime.Events(Path(root, "events.json"), "test")
            stream = Mock()
            stream.recv.return_value = b""
            module = SimpleNamespace(TARGET="127.0.0.3", RawXudpAssociation=Mock(return_value=SimpleNamespace(stream=stream)),
                                     decode_xudp_address=Mock())
            probe = runtime.MuxProbe(SimpleNamespace(target=SimpleNamespace(port=33000)), module, "lifecycle", events)
            with self.assertRaisesRegex(base.ExperimentError, "Parent TLS"):
                probe.pump(runtime.time.monotonic() + 1)


class SocketLifecycleTests(unittest.TestCase):
    def test_exact_reciprocal_trio_identification(self):
        self.assertEqual(leg()["keys"], [["domestic", 100, 4], ["overseas", 200, 5], ["overseas", 200, 6]])
        current = snapshot(1)
        current["processes"][1]["sockets"][1]["remote"]["port"] = 59999
        with self.assertRaises(base.ExperimentError):
            runtime.paired_leg(snapshot(0, False, parent=False), current, 40000)

    def test_complete_closure_proof_requires_all_three_inodes_end_and_peer_eof(self):
        current = [snapshot(1.1), snapshot(3.1, False), snapshot(7, False)]
        result = runtime.classify_leg(current, leg(), 1, {"monotonic": 3}, [eof()])
        self.assertTrue(result["released_by_6s"])
        self.assertTrue(runtime.active_keys(current[-1]))  # Parent TLS may remain.
        for ending, logs in ((None, [eof()]), ({"monotonic": 3}, []), ({"monotonic": 7.1}, [eof()]),
                             ({"monotonic": 3}, [{**eof(), "phase": "after_explicit_restart"}])):
            self.assertFalse(runtime.classify_leg(current, leg(), 1, ending, logs)["released_by_6s"])

    def test_cross_boundary_sample_is_never_promoted_to_within_six(self):
        result = runtime.classify_leg([snapshot(1.1), snapshot(6.99), snapshot(7, False, end=7.05)], leg(), 1,
                                     {"monotonic": 6}, [eof(6)])
        self.assertEqual(result["classification"], "boundary_inconclusive")
        self.assertFalse(result["released_by_6s"])
        with self.assertRaises(base.ExperimentError):
            runtime.require_phase_lifecycle("lifecycle", result)

    def test_control_must_preserve_exact_three_sockets_not_any_failure(self):
        result = runtime.classify_leg([snapshot(1.1), snapshot(7)], leg(), 1, None, [])
        runtime.require_phase_lifecycle("baseline", result)
        runtime.require_phase_lifecycle("patched", result)
        with self.assertRaises(base.ExperimentError):
            runtime.require_phase_lifecycle("lifecycle", result)
        result["session_end"] = {"monotonic": 3}
        with self.assertRaises(base.ExperimentError):
            runtime.require_phase_lifecycle("patched", result)

    def test_reappeared_inode_and_unresolved_race_rejected(self):
        cases = [[snapshot(1.1), snapshot(3, False), snapshot(7)], [snapshot(1.1), snapshot(7)]]
        cases[1][-1]["processes"][0]["races"] = [{"fd": 7}]
        for samples in cases:
            with self.assertRaises(base.ExperimentError):
                runtime.classify_leg(samples, leg(), 1, None, [])

    def test_disappearance_of_udp_alone_does_not_prove_propagation(self):
        current = snapshot(7)
        current["processes"][1]["sockets"] = [x for x in current["processes"][1]["sockets"] if x["protocol"] != "udp"]
        result = runtime.classify_leg([snapshot(1.1), current], leg(), 1, {"monotonic": 3}, [eof()])
        self.assertFalse(result["released_by_6s"])
        self.assertEqual(len(result["remaining_keys_at_6s"]), 2)
        with self.assertRaises(base.ExperimentError):
            runtime.require_phase_lifecycle("patched", result)

    def test_source_activity_is_seventeen_packets_over_eight_seconds_with_separate_idle(self):
        self.assertEqual(runtime.ACTIVITY_PACKETS, 17)
        self.assertEqual((runtime.ACTIVITY_PACKETS - 1) * runtime.ACTIVITY_INTERVAL, 8)
        self.assertEqual(runtime.IDLE_SECONDS, 6)
        source = Path(runtime.__file__).read_text()
        self.assertIn('require(1 not in probe.ends', source)
        self.assertIn('parent_keys <= socket_keys(current).keys()', source)
        self.assertIn('last + IDLE_SECONDS', source)


class ArtifactTests(unittest.TestCase):
    def test_artifacts_have_exact_case_and_observation_allowlist(self):
        self.assertEqual(len(runtime.ALL_CASES), 35)
        self.assertEqual(len(runtime.EXTRA_CASES), 6)
        self.assertEqual(len(runtime.ARTIFACT_ALLOWLIST), 115)
        self.assertFalse(any(name.endswith((".pem", ".yaml")) or name.endswith("sing-box") for name in runtime.ARTIFACT_ALLOWLIST))

    def test_unknown_artifact_and_sensitive_material_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "result.json").write_text('{}')
            runtime.verify_outputs(output)
            (output / "config.json").write_text('{}')
            with self.assertRaises(base.ExperimentError):
                runtime.verify_outputs(output)
        for value in ("-----BEGIN PRIVATE KEY-----", "-----BEGIN CERTIFICATE-----"):
            with self.assertRaises(base.ExperimentError):
                runtime.clean(value)
        self.assertNotIn("payload-secret", runtime.clean("failure: b'payload-secret'"))
        self.assertNotIn("11111111-1111-4111-8111-111111111111", runtime.clean("11111111-1111-4111-8111-111111111111"))

    def test_run_extra_preserves_original_suite_false(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "observations").mkdir()
            with patch.object(runtime.observer, "run_case") as run:
                result = runtime.run_extra(runtime.DOMAIN, Mock(), Mock(), "lifecycle", Mock(), root)
            self.assertTrue(result["lifecycle_contract_passed"])
            self.assertFalse(result["original_suite_passed"])
            self.assertFalse(result["product_qualified"])
            self.assertEqual(run.call_count, 1)


class SimulatedActivityTests(unittest.TestCase):
    """Drive the actual orchestration with a deterministic clock and fake sockets."""
    def exercise(self, phase, *, kill_parent=False, ignore_activity=False):
        clock = SimpleNamespace(now=100.0)
        events = SimpleNamespace(data={"events": []})
        def event(kind, **values):
            result = {"kind": kind, "monotonic": clock.now, **values}
            events.data["events"].append(result)
            return result
        events.event = event
        f = SimpleNamespace(start=Mock(), guarded_config=Mock(return_value={}), upstream_port=40000, ingress_port=30000,
                            target=SimpleNamespace(received=[]))
        probes = []
        class Probe:
            def __init__(self, *args):
                self.ends, self.last, self.first, self.replies, self.payloads = {}, {}, {}, {}, {}
                self.sends = []
                probes.append(self)
            def __enter__(self):
                return self
            def __exit__(self, *_):
                event("explicit_client_close_complete")
            def refresh(self):
                if phase != "lifecycle":
                    return
                for session, last in self.last.items():
                    since = self.first[session] if ignore_activity else last
                    if clock.now >= since + 2 and session not in self.ends:
                        self.ends[session] = event("session_end", session=session, wire_status=3)
                        event("core_eof_log", role="overseas", phase="natural_idle",
                              line="connection from 127.0.0.1:%d: EOF" % (50001 + session))
            def send(self, session, payload):
                self.refresh()
                runtime.require(session not in self.ends, "Closed active session")
                self.payloads[session] = payload
                self.sends.append((session, clock.now))
                return event("business_packet_send", session=session)
            def reply(self, session, timeout=4):
                clock.now += .01
                self.first.setdefault(session, clock.now)
                self.last[session] = clock.now
                f.target.received.append(self.payloads[session])
                self.replies[session] = event("business_packet_reply", session=session)
                return self.replies[session]
            def pump(self, deadline):
                clock.now = max(clock.now, deadline)
                self.refresh()
        class Watch:
            until = runtime.SocketWatch.until
            def __init__(self, *args):
                self.samples, self.allowed = [], None
            def baseline(self):
                return snapshot(clock.now, False, parent=False)
            def snapshot(self, phase):
                clock.now += .01
                probe = probes[-1]
                probe.refresh()
                current = snapshot(clock.now, 0 in probe.last and 0 not in probe.ends,
                                   parent=not (kill_parent and 0 in probe.ends))
                if 1 in probe.last and 1 not in probe.ends:
                    current["processes"][0]["sockets"].append(row(7, "tcp", ("127.0.0.1", 50002), ("127.0.0.2", 40000)))
                    current["processes"][1]["sockets"] += [row(8, "tcp", ("127.0.0.2", 40000), ("127.0.0.1", 50002)),
                                                         row(9, "udp", ("127.0.0.4", 60001), ("0.0.0.0", 0), "07")]
                if 2 in probe.last and 2 not in probe.ends:
                    current["processes"][0]["sockets"].append(row(10, "tcp", ("127.0.0.1", 50003), ("127.0.0.2", 40000)))
                    current["processes"][1]["sockets"] += [row(11, "tcp", ("127.0.0.2", 40000), ("127.0.0.1", 50003)),
                                                         row(12, "udp", ("127.0.0.4", 60002), ("0.0.0.0", 0), "07")]
                self.samples.append(current)
                return current
        with patch.object(runtime, "MuxProbe", Probe), patch.object(runtime, "SocketWatch", Watch), \
                patch.object(runtime, "time", SimpleNamespace(monotonic=lambda: clock.now)):
            runtime.active_case(f, Mock(), phase, events, Mock())
        return probes[0], events.data["events"]

    def test_actual_orchestration_runs_idle_sibling_and_seventeen_active_packets(self):
        for phase in runtime.PHASES:
            probe, events = self.exercise(phase)
            self.assertEqual(Counter(session for session, _ in probe.sends), {0: 1, 1: 17, 2: 1})
            active = [moment for session, moment in probe.sends if session == 1]
            self.assertGreaterEqual(active[-1] - active[0], 8)
            self.assertTrue(all(0 < right - left < 2 for left, right in zip(active, active[1:])))
            results = [e for e in events if e["kind"].endswith("classification")]
            self.assertEqual(len(results), 3)
            self.assertTrue(all(r["released_by_6s"] is (phase == "lifecycle") for r in results))

    def test_parent_mux_kill_is_rejected_while_sibling_is_active(self):
        with self.assertRaisesRegex(base.ExperimentError, "shared parent"):
            self.exercise("lifecycle", kill_parent=True)

    def test_absolute_timer_without_activity_renewal_is_rejected(self):
        with self.assertRaises(base.ExperimentError):
            self.exercise("lifecycle", ignore_activity=True)


class IsolationMutationTests(unittest.TestCase):
    def records(self, phase):
        candidate = {"path": "/experiment/core", "sha256": "a" * 64}
        stock = {"sing-box": {"path": "/official/core", "sha256": "b" * 64},
                 "mihomo": {"path": "/official/mihomo", "sha256": "c" * 64}}
        records = []
        for (case, role), count in runtime.expected_roles(phase).items():
            for _ in range(count):
                mihomo = role in ("device-mihomo", "device-reconfigured-mihomo")
                identity = candidate if role in base.DOMESTIC_ROLES else stock["mihomo" if mihomo else "sing-box"]
                launches = 0 if case == base.METHODS[15] else 1
                if phase != "baseline" and role == "overseas" and case.endswith("-stopped"):
                    launches = 2
                records.append({"case": case, "role": role, "mihomo": mihomo, "binary": identity["path"],
                    "sha256": identity["sha256"], "parser_passed": True, "config_sha256": "d" * 64,
                    "config_shape": {"sections": ["route"]},
                    "launches": [{"pid": 1, "ready": True, "sha256": identity["sha256"]} for _ in range(launches)]})
        return records, candidate, stock

    def test_original_and_restart_roles_only_select_domestic_candidate(self):
        for phase in runtime.PHASES:
            records, candidate, stock = self.records(phase)
            runtime.verify_isolation(phase, records, candidate, stock)
            for index, field, value in ((0, "binary", candidate["path"]), (1, "mihomo", True),
                                        (1, "config_sha256", "bad"), (1, "parser_passed", False)):
                changed = deepcopy(records)
                changed[index][field] = value
                with self.assertRaises(base.ExperimentError):
                    runtime.verify_isolation(phase, changed, candidate, stock)
            changed = deepcopy(records)
            next(r for r in changed if r["launches"])["launches"][0]["ready"] = False
            with self.assertRaises(base.ExperimentError):
                runtime.verify_isolation(phase, changed, candidate, stock)
            with self.assertRaises(base.ExperimentError):
                runtime.verify_isolation(phase, records[:-1], candidate, stock)

    def test_nested_json_byte_repr_is_redacted_before_encoding(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "result.json")
            runtime.save(path, {"error": 'Counter({b"secret-body": 1})'})
            self.assertNotIn("secret-body", path.read_text())
            runtime.verify_outputs(Path(root))


class OfficialRecoveryTests(unittest.TestCase):
    def invoke(self, phase, *, returned_port=33000, relay=("127.0.0.1", 45000)):
        events = SimpleNamespace(data={"events": []})
        def event(kind, **values):
            result = {"kind": kind, **values}
            events.data["events"].append(result)
            return result
        events.event = event
        association = Mock()
        association.relay = ("127.0.0.1", 45000)
        association.stream.recvfrom.return_value = (b"wire", relay)
        manager = Mock()
        manager.__enter__ = Mock(return_value=association)
        manager.__exit__ = Mock(return_value=False)
        helper = SimpleNamespace(SocksDatagrams=Mock(return_value=manager),
                                 decode_socks_datagram=Mock(return_value=("127.0.0.3", returned_port, b"packet")))
        f = SimpleNamespace(client_port=45000, upstream_port=43000,
            target=SimpleNamespace(host="127.0.0.3", port=33000, expect=Mock(), audit=Mock()))
        on_live = Mock(return_value={"live-keys"})
        def sample_while_open():
            manager.__exit__.assert_not_called()
            return on_live()
        with patch.object(runtime.importlib, "import_module", return_value=helper):
            result = runtime.device_recovery(f, phase, events, b"packet", on_live=sample_while_open)
        self.assertEqual(result, {"live-keys"})
        association.send.assert_called_once_with("127.0.0.3", 33000, b"packet")
        association.stream.recvfrom.assert_called_once()
        on_live.assert_called_once()
        manager.__exit__.assert_called_once()
        self.assertEqual([e["kind"] for e in events.data["events"]],
                         ["device_business_send", "device_recovery", "explicit_device_client_close_begin", "explicit_device_client_close_complete"])
        return events.data["events"][1]

    def test_official_recovery_samples_live_trio_before_explicit_control_close(self):
        self.assertEqual(self.invoke("lifecycle")["address_verdict"], "passed")
        self.assertEqual(self.invoke("baseline", returned_port=43000)["address_verdict"], "known_baseline_address_failure")

    def test_wrong_relay_or_unknown_port_is_not_retried_or_accepted(self):
        with self.assertRaises(base.ExperimentError):
            self.invoke("lifecycle", relay=("127.0.0.7", 45000))
        with self.assertRaises(base.ExperimentError):
            self.invoke("baseline", returned_port=44000)
        with self.assertRaises(base.ExperimentError):
            self.invoke("lifecycle", returned_port=43000)

    def test_every_restart_round_contains_real_device_recovery_and_single_close_path(self):
        tree = ast.parse(Path(runtime.__file__).read_text())
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "restart_case")
        loop = next(n for n in method.body if isinstance(n, ast.For))
        recoveries = [n for n in ast.walk(loop) if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Name) and n.func.id == "device_recovery"]
        self.assertEqual(len(recoveries), 1)
        self.assertEqual(runtime.RESTARTS, 3)
        self.assertIn("on_live", [kw.arg for kw in recoveries[0].keywords])

    def test_same_parent_new_session_follows_end_and_has_own_six_second_window(self):
        probe, events = SimulatedActivityTests().exercise("lifecycle")
        reopened = next(e for e in events if e["kind"] == "same_parent_new_session")
        self.assertEqual(reopened["session"], 2)
        self.assertTrue(reopened["after_end_gate_passed"])
        self.assertLess(reopened["previous_end"]["monotonic"], reopened["sent_monotonic"])
        parent = reopened["parent_keys"]
        self.assertEqual(parent, [("domestic", 100, 3)])
        result = next(e for e in events if e["kind"] == "reopened_session_idle_classification")
        self.assertEqual(result["deadline_monotonic"], probe.replies[2]["monotonic"] + 6)
        self.assertTrue(result["released_by_6s"])


class NewAssociationCoverageTests(unittest.TestCase):
    def test_exact_new_parent_plus_trio_and_existing_parent_trio_only(self):
        before, after = snapshot(0, False, parent=False), snapshot(1)
        runtime.verify_new_sockets(before, after, leg(), ingress_port=30000)
        runtime.verify_new_sockets(snapshot(0, False), after, leg())

    def test_extra_unrequested_socket_is_never_absorbed_as_allowed(self):
        before, after = snapshot(0, False), snapshot(1)
        after["processes"][0]["sockets"].append(row(99, "udp", ("127.0.0.1", 55555), ("0.0.0.0", 0), "07"))
        with self.assertRaisesRegex(base.ExperimentError, "extra or missing"):
            runtime.verify_new_sockets(before, after, leg())
        after = snapshot(1)
        after["processes"][0]["sockets"][1]["protocol"] = "udp"
        with self.assertRaisesRegex(base.ExperimentError, "incoming parent"):
            runtime.verify_new_sockets(snapshot(0, False, parent=False), after, leg(), ingress_port=30000)


if __name__ == "__main__":
    unittest.main()
