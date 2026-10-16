"""Pure observation controls; no real sockets, cores, psutil or /proc access."""
from collections import Counter
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("runtime_observer", Path(__file__).with_name("observe_runtime.py"))
observer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observer)


def frame(payload=b"packet", session=b"\0\0", first=False):
    header = session + b"\x02\x01"
    return (b"\0\0" if first else b"") + len(header).to_bytes(2, "big") + header + len(payload).to_bytes(2, "big") + payload


def snapshot(moment, inodes=(101,), race=False):
    return {"monotonic": moment, "sample_completed_monotonic": moment, "processes": [
        {"role": "domestic", "pid": 100, "sockets": [], "races": []},
        {"role": "overseas", "pid": 200,
         "sockets": [{"protocol": "udp", "local": {"host": "127.0.0.4", "port": 50000}, "inode": inode}
                     for inode in inodes],
         "races": [{"fd": 7, "kind": "fd_changed_during_snapshot"}] if race else []}]}


class PacketObservationTests(unittest.TestCase):
    def test_actual_session_is_extracted_without_payload_retention(self):
        for first in (False, True):
            result = observer.received_session(frame(b"secret-payload", first=first), first)
            self.assertEqual(result, {"wire_session_id_hex": "0000", "status": 2, "flags": 1,
                                      "preceding_control_frames": 0})
            self.assertNotIn("secret-payload", json.dumps(result))

    def test_session_parser_rejects_wrong_id_and_truncation(self):
        for packet, first in ((frame(session=b"\0\x01"), False), (b"", False), (b"\0", True),
                              (frame()[:-1], False), (frame() + b"extra", False), (b"\0\x02\0\0", False)):
            with self.subTest(packet=packet), self.assertRaises(observer.base.ExperimentError):
                observer.received_session(packet, first)

    def test_capturing_stream_forwards_each_read_exactly(self):
        stream = Mock()
        stream.recv.side_effect = [b"a", b"bc"]
        capture = observer.CapturingStream(stream)
        self.assertEqual(capture.recv(1), b"a")
        self.assertEqual(capture.recv(2), b"bc")
        capture.settimeout(3)
        stream.settimeout.assert_called_once_with(3)
        self.assertEqual(capture.parts, [b"a", b"bc"])

    def test_changed_port_reply_keeps_initial_mapping_and_single_send(self):
        payload = b"original-content"
        raw = frame(payload, first=True)
        stream = Mock()
        stream.recv.return_value = raw
        association = SimpleNamespace(stream=stream, response_read=False, send=Mock())
        def receive():
            self.assertEqual(association.stream.recv(len(raw)), raw)
            return "initial.example.test", 33000, payload
        association.receive = receive
        fixture = SimpleNamespace(target=SimpleNamespace(host="127.0.0.3", expect=Mock(), audit=Mock()))
        with tempfile.TemporaryDirectory() as directory:
            events = observer.Events(Path(directory) / "result.json", "test")
            observer.send_and_record(fixture, association, "initial.example.test", 44000, payload,
                                     initial_host="initial.example.test", initial_port=33000,
                                     association_id="same-stream", events=events, label="changed_port")
            association.send.assert_called_once_with("initial.example.test", 44000, payload)
            self.assertIs(association.stream, stream)
            self.assertEqual([row["kind"] for row in events.data["events"]],
                             ["business_packet_send", "business_packet_reply"])
            reply = events.data["events"][-1]
            self.assertEqual(reply["port"], 33000)
            self.assertEqual(reply["wire_session_id_hex"], "0000")
            self.assertEqual(reply["payload"], observer.payload_identity(payload))
            self.assertNotIn("original-content", Path(directory, "result.json").read_text())

    def test_host_port_payload_mismatches_never_become_observation_success(self):
        for host, port, payload in (("127.0.0.7", 33000, b"sent"),
                                    ("127.0.0.3", 44000, b"sent"), ("127.0.0.3", 33000, b"wrong")):
            stream = Mock()
            stream.recv.return_value = frame(payload, first=True)
            association = SimpleNamespace(stream=stream, response_read=False, send=Mock())
            def receive():
                association.stream.recv(100)
                return host, port, payload
            association.receive = receive
            fixture = SimpleNamespace(target=SimpleNamespace(host="127.0.0.3", expect=Mock(), audit=Mock()))
            with tempfile.TemporaryDirectory() as directory:
                events = observer.Events(Path(directory) / "result.json", "test")
                with self.subTest(host=host, port=port), self.assertRaises(observer.base.ExperimentError):
                    observer.send_and_record(fixture, association, "initial.example.test", 44000, b"sent",
                                             initial_host="initial.example.test", initial_port=33000,
                                             association_id="same", events=events, label="changed")
                self.assertEqual(association.send.call_count, 1)
                self.assertIs(association.stream, stream)


class SocketObservationTests(unittest.TestCase):
    def test_proc_address_decoding_ipv4_and_ipv6(self):
        self.assertEqual(observer.proc_address("0100007F:1F90", socket.AF_INET),
                         {"host": "127.0.0.1", "port": 8080})
        self.assertEqual(observer.proc_address("00000000000000000000000001000000:01BB", socket.AF_INET6),
                         {"host": "::1", "port": 443})

    def test_proc_table_keeps_only_owned_inodes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            net = root / "42/net"
            net.mkdir(parents=True)
            for name in ("tcp", "tcp6", "udp", "udp6"):
                (net / name).write_text("header\n")
            (net / "udp").write_text("header\n"
                "0: 0400007F:C350 00000000:0000 07 00000000:00000000 00:00000000 00000000 1000 0 123 2\n"
                "1: 0100007F:C351 00000000:0000 07 00000000:00000000 00:00000000 00000000 1000 0 999 2\n")
            rows = observer.read_proc_sockets(42, {123}, root)
            self.assertEqual(set(rows), {123})
            self.assertEqual(rows[123]["local"], {"host": "127.0.0.4", "port": 50000})
            self.assertEqual(rows[123]["remote"], {"host": "0.0.0.0", "port": 0})

    def test_fd_inode_reader_retains_socket_descriptors_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fds = root / "42/fd"
            fds.mkdir(parents=True)
            (fds / "7").symlink_to("socket:[123]")
            (fds / "8").symlink_to("/regular/file")
            self.assertEqual(observer.fd_inodes(42, root), {7: 123})

    def test_socket_snapshot_cross_checks_pid_fd_inode_and_tuples(self):
        core = SimpleNamespace(process=SimpleNamespace(pid=42, poll=lambda: None))
        connection = SimpleNamespace(fd=7, family=socket.AF_INET, type=socket.SOCK_DGRAM,
                                     laddr=SimpleNamespace(ip="127.0.0.4", port=50000), raddr=(), status="NONE")
        process = Mock()
        process.create_time.return_value = 123.0
        process.net_connections.return_value = [connection]
        psutil_module = Mock()
        psutil_module.Process.return_value = process
        proc_row = {"inode": 321, "family": "ipv4", "protocol": "udp", "proc_state": "07",
                    "local": {"host": "127.0.0.4", "port": 50000},
                    "remote": {"host": "0.0.0.0", "port": 0}}
        with patch.object(observer, "fd_inodes", return_value={7: 321}):
            with patch.object(observer, "read_proc_sockets", return_value={321: proc_row}):
                result = observer.socket_snapshot({"overseas": core}, psutil_module, {"overseas": 123.0})
                row = result["processes"][0]["sockets"][0]
                self.assertEqual((row["fd"], row["inode"]), (7, 321))
                self.assertEqual(result["processes"][0]["races"], [])
                proc_row["local"]["port"] = 50001
                with self.assertRaisesRegex(observer.base.ExperimentError, "disagree"):
                    observer.socket_snapshot({"overseas": core}, psutil_module, {"overseas": 123.0})
        process.create_time.return_value = 124.0
        with self.assertRaisesRegex(observer.base.ExperimentError, "identity changed"):
            observer.socket_snapshot({"overseas": core}, psutil_module, {"overseas": 123.0})

    def test_stable_proc_socket_missing_from_psutil_is_not_closure(self):
        core = SimpleNamespace(process=SimpleNamespace(pid=42, poll=lambda: None))
        process = Mock()
        process.create_time.return_value = 123.0
        process.net_connections.return_value = []
        psutil_module = Mock()
        psutil_module.Process.return_value = process
        proc_row = {"inode": 321, "protocol": "udp", "local": {"host": "127.0.0.4", "port": 50000}}
        with patch.object(observer, "fd_inodes", return_value={7: 321}):
            with patch.object(observer, "read_proc_sockets", return_value={321: proc_row}):
                with self.assertRaisesRegex(observer.base.ExperimentError, "omitted by psutil"):
                    observer.socket_snapshot({"overseas": core}, psutil_module, {"overseas": 123.0})

    def test_snapshot_permission_errors_are_not_swallowed(self):
        core = SimpleNamespace(process=SimpleNamespace(pid=42, poll=lambda: None))
        process = Mock()
        process.create_time.return_value = 123.0
        process.net_connections.side_effect = PermissionError("denied")
        psutil_module = Mock()
        psutil_module.Process.return_value = process
        with patch.object(observer, "fd_inodes", return_value={}):
            with self.assertRaises(PermissionError):
                observer.socket_snapshot({"overseas": core}, psutil_module, {"overseas": 123.0})

    def test_known_sampling_race_resolves_with_bounded_read_only_samples(self):
        events = Mock()
        with patch.object(observer, "socket_snapshot", side_effect=[snapshot(1, race=True), snapshot(2)]) as read:
            with patch.object(observer.time, "sleep"):
                result = observer.stable_snapshot({}, None, {}, events)
        self.assertEqual(result["monotonic"], 2)
        self.assertEqual(read.call_count, 2)
        events.event.assert_called_once()
        with patch.object(observer, "socket_snapshot", return_value=snapshot(1, race=True)) as read:
            with patch.object(observer.time, "sleep"):
                with self.assertRaisesRegex(observer.base.ExperimentError, "inconclusive"):
                    observer.stable_snapshot({}, None, {}, events)
        self.assertEqual(read.call_count, 3)

    def test_six_second_still_open_is_observation_not_green_gate(self):
        result = observer.classify_idle([snapshot(100), snapshot(102), snapshot(106)], {101}, 100)
        self.assertEqual(result["classification"], "still_open_at_6s")
        self.assertFalse(result["original_idle_gate_passed"])

    def test_natural_close_still_does_not_rewrite_original_gate(self):
        result = observer.classify_idle([snapshot(100), snapshot(102, ()), snapshot(106, ())], {101}, 100)
        self.assertEqual(result["classification"], "closed_naturally_within_6s")
        self.assertEqual(result["first_natural_absence_sample"]["sample_completed_monotonic"], 102)
        self.assertFalse(result["original_idle_gate_passed"])

    def test_first_absence_after_or_across_deadline_is_inconclusive(self):
        for first_absent in (snapshot(106.05, ()), snapshot(105.99, ())):
            first_absent["sample_completed_monotonic"] = 106.1
            rows = [snapshot(100), snapshot(105.9), first_absent]
            if first_absent["monotonic"] < 106:
                rows.append(snapshot(106.2, ()))
            result = observer.classify_idle(rows, {101}, 100)
            self.assertEqual(result["classification"], "closure_boundary_inconclusive")
            self.assertFalse(result["conclusive"])
            self.assertFalse(result["original_idle_gate_passed"])

    def test_unknown_early_reappeared_or_new_socket_outcomes_fail(self):
        cases = [([], {101}), ([snapshot(105.99)], {101}), ([snapshot(106)], set()),
                 ([snapshot(100), snapshot(106, (102,))], {101}),
                 ([snapshot(100), snapshot(102, ()), snapshot(106)], {101}),
                 ([snapshot(100), snapshot(106, race=True)], {101})]
        for rows, initial in cases:
            with self.subTest(rows=rows), self.assertRaises(observer.base.ExperimentError):
                observer.classify_idle(rows, initial, 100)


class LifecycleAndOutputTests(unittest.TestCase):
    def test_eof_lines_are_classified_by_phase_without_old_startup_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = {role: Path(directory) / (role + ".log") for role in ("domestic", "overseas")}
            for path in paths.values():
                path.write_text("old EOF\n")
            fixture = SimpleNamespace(domestic=SimpleNamespace(log_path=paths["domestic"], process=SimpleNamespace(pid=1)),
                                      upstream=SimpleNamespace(log_path=paths["overseas"], process=SimpleNamespace(pid=2)),
                                      safe=lambda value: value)
            events = Mock()
            logs = observer.HopLogs(fixture, events)
            paths["domestic"].write_text("old EOF\nnew EOF\n")
            logs.collect("after_explicit_client_close")
            self.assertEqual(events.event.call_count, 1)
            self.assertEqual(events.event.call_args.kwargs["phase"], "after_explicit_client_close")
            self.assertEqual(events.event.call_args.kwargs["line"], "new EOF")
            paths["overseas"].write_text("old EOF\ncleanup EOF\n")
            logs.collect("fixture_cleanup_after_process_stop")
            self.assertEqual(events.event.call_args.kwargs["phase"], "fixture_cleanup_after_process_stop")

    def test_isolation_requires_exact_six_roles_and_official_devices(self):
        candidate = {"path": "/candidate", "sha256": "a" * 64}
        stock = {"sing-box": {"path": "/official", "sha256": "b" * 64},
                 "mihomo": {"path": "/mihomo", "sha256": "c" * 64}}
        rows = []
        for case in observer.CASES:
            for role in ("overseas", "domestic", "device-mihomo"):
                mihomo = role == "device-mihomo"
                item = candidate if role == "domestic" else stock["mihomo" if mihomo else "sing-box"]
                rows.append({"case": case, "role": role, "mihomo": mihomo, "binary": item["path"],
                             "sha256": item["sha256"], "parser_passed": True,
                             "launches": [{"pid": 42, "ready": True, "sha256": item["sha256"]}]})
        observer.verify_observation_isolation(rows, candidate, stock)
        for change in (lambda values: values.pop(), lambda values: values[0].update(binary="/candidate"),
                       lambda values: values[0]["launches"][0].update(ready=False),
                       lambda values: values[0].update(mihomo=True)):
            values = deepcopy(rows)
            change(values)
            with self.assertRaises(observer.base.ExperimentError):
                observer.verify_observation_isolation(values, candidate, stock)

    def test_cleanup_rejects_live_process_or_target_threads(self):
        process = SimpleNamespace(pid=42, poll=lambda: 0)
        target = SimpleNamespace(host="127.0.0.3", port=33000, audit=Mock(), closed=True,
                                 thread=SimpleNamespace(is_alive=lambda: False), received=[], expected=Counter(), errors=[])
        with tempfile.TemporaryDirectory() as directory:
            fixture = SimpleNamespace(processes=[SimpleNamespace(process=process)], targets=[target],
                                      root=Path(directory) / "already-removed", answers=None)
            psutil_module = Mock()
            psutil_module.pid_exists.return_value = False
            with patch.object(observer.threading, "enumerate", return_value=[]):
                observer.cleanup_proof(fixture, set(), Mock(), psutil_module)
                process.poll = lambda: None
                with self.assertRaisesRegex(observer.base.ExperimentError, "survived cleanup"):
                    observer.cleanup_proof(fixture, set(), Mock(), psutil_module)
                process.poll = lambda: 0
                target.thread.is_alive = lambda: True
                with self.assertRaisesRegex(observer.base.ExperimentError, "thread survived"):
                    observer.cleanup_proof(fixture, set(), Mock(), psutil_module)

    def test_output_allows_only_declared_text_evidence_and_redacts_fixture_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observer.save(root / "result.json", {"message": "11111111-1111-4111-8111-111111111111"})
            self.assertNotIn("11111111-1111", (root / "result.json").read_text())
            observer.verify_outputs(root)
            (root / "secret.key").write_text("not permitted")
            with self.assertRaisesRegex(observer.base.ExperimentError, "Unexpected"):
                observer.verify_outputs(root)
        with self.assertRaises(observer.base.ExperimentError):
            observer.clean("-----BEGIN PRIVATE KEY-----")

    def test_cli_requires_patched_and_never_overwrites_evidence(self):
        with patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                observer.parser().parse_args(["--phase", "baseline", "--candidate", "/x",
                                               "--provenance", "/x", "--output", "/x"])
            with tempfile.TemporaryDirectory() as directory:
                self.assertEqual(observer.main(["--phase", "patched", "--candidate", "/x",
                                                "--provenance", "/x", "--output", directory]), 2)
                self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
