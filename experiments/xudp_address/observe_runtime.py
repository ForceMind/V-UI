#!/usr/bin/env python3
"""Bounded diagnostics only; never changes the original D2 gate or its outcome."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import struct
import sys
import threading
import time
import traceback
import unittest

_SPEC = importlib.util.spec_from_file_location("xudp_observation_base", Path(__file__).with_name("run_experiment.py"))
base = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(base)

DOMAIN_CASE = "observe_domain_freeze"
IDLE_CASE = "observe_idle_sockets"
CASES = (DOMAIN_CASE, IDLE_CASE)
IDLE_SECONDS = 6.0
POST_CLOSE_SECONDS = 1.0
POLL_SECONDS = .2
ARTIFACT_ALLOWLIST = frozenset({
    "result.json", "process-binaries.json", "suite.log", "runner-error.log",
    "observations/domain-freeze.json", "observations/idle-sockets.json",
    *(f"cases/{case}{extension}" for case in CASES for extension in (".json", ".log")),
    *(f"delivery-ledgers/{case}.json" for case in CASES),
})


def require(condition, message):
    base.require(condition, message)


def payload_identity(payload):
    return {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def clean(text):
    for value in ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
                  "33333333-3333-4333-8333-333333333333"):
        text = text.replace(value, "<fixture>")
    text = re.sub(r"/tmp/vui-d2-[^/\s'\"]+", "<fixture>", text)
    require("PRIVATE KEY-----" not in text and "BEGIN CERTIFICATE" not in text,
            "Key or certificate material is forbidden in observation output")
    return text


def save(path, value):
    # JSON serialization is checked before writing; no configs or packet bodies.
    Path(path).write_text(clean(json.dumps(value, indent=2, sort_keys=True)) + "\n")


class Events:
    def __init__(self, path, kind):
        self.path = Path(path)
        self.data = {"kind": kind, "status": "running", "events": [], "product_qualified": False,
                     "original_gate_unchanged": True}
        self.flush()

    def event(self, kind, **details):
        value = {"kind": kind, "monotonic": time.monotonic(), **details}
        self.data["events"].append(value)
        self.flush()
        return value

    def flush(self):
        save(self.path, self.data)


class CapturingStream:
    """Capture only one original parser receive; never changes bytes or timing."""
    def __init__(self, stream):
        self.stream = stream
        self.parts = []

    def recv(self, *args, **kwargs):
        value = self.stream.recv(*args, **kwargs)
        self.parts.append(value)
        return value

    def __getattr__(self, name):
        return getattr(self.stream, name)


def received_session(packet, first_response):
    """Extract the actual mux session after the unchanged parser has accepted it."""
    position = 0
    if first_response:
        require(len(packet) >= 2, "Missing captured VLESS response")
        position = 2 + packet[1]
    frames = []
    while position < len(packet):
        require(position + 2 <= len(packet), "Truncated captured XUDP frame length")
        length = int.from_bytes(packet[position:position + 2], "big")
        position += 2
        require(length >= 4 and position + length <= len(packet), "Truncated captured XUDP frame")
        header = packet[position:position + length]
        position += length
        row = {"wire_session_id_hex": header[:2].hex(), "status": header[2], "flags": header[3]}
        require(row["wire_session_id_hex"] == "0000" and row["status"] == 2 and not row["flags"] & 2,
                "Unexpected captured XUDP session frame")
        frames.append(row)
        if header[3] & 1:
            require(position + 2 <= len(packet), "Missing captured XUDP payload length")
            size = int.from_bytes(packet[position:position + 2], "big")
            position += 2 + size
            require(position == len(packet), "Captured XUDP payload boundary mismatch")
            require(row["wire_session_id_hex"] == "0000", "Unexpected actual XUDP session ID")
            return {**row, "preceding_control_frames": len(frames) - 1}
    raise base.ExperimentError("No actual XUDP payload session captured")


def send_and_record(fixture, association, host, port, payload, *, initial_host, initial_port,
                    association_id, events, label):
    fixture.target.expect("127.0.0.4", payload)
    sent = events.event("business_packet_send", label=label, association_id=association_id,
                        requested_host=host, requested_port=port, payload=payload_identity(payload))
    association.send(host, port, payload)  # Exactly once: no application retries.
    first = not association.response_read
    original = association.stream
    capture = CapturingStream(original)
    association.stream = capture
    try:
        received_host, received_port, received_payload = association.receive()
    finally:
        association.stream = original
    session = received_session(b"".join(capture.parts), first)
    received = events.event("business_packet_reply", label=label, association_id=association_id,
                            host=received_host, port=received_port, payload=payload_identity(received_payload),
                            **session)
    require(received_host in {initial_host, fixture.target.host}, "Reply escaped the frozen initial host mapping")
    require(received_port == initial_port, "Reply port differs from the frozen initial target port")
    require(received_payload == payload, "Reply payload differs from the single sent packet")
    fixture.target.audit()
    return sent, received


def fd_inodes(pid, proc_root=Path("/proc")):
    result = {}
    for entry in (proc_root / str(pid) / "fd").iterdir():
        try:
            target = os.readlink(entry)
        except FileNotFoundError:
            # A descriptor removed between readdir and readlink is a known race.
            continue
        match = re.fullmatch(r"socket:\[([0-9]+)\]", target)
        if match:
            result[int(entry.name)] = int(match[1])
    return result


def proc_address(value, family):
    address, port = value.split(":")
    if family == socket.AF_INET:
        packed = struct.pack("=I", int(address, 16))
    else:
        require(len(address) == 32, "Unexpected /proc IPv6 socket address")
        packed = struct.pack("=4I", *(int(address[index:index + 8], 16) for index in range(0, 32, 8)))
    return {"host": str(ipaddress.ip_address(socket.inet_ntop(family, packed))), "port": int(port, 16)}


def read_proc_sockets(pid, inodes, proc_root=Path("/proc")):
    result = {}
    for name, family, kind in (("tcp", socket.AF_INET, "tcp"), ("tcp6", socket.AF_INET6, "tcp"),
                               ("udp", socket.AF_INET, "udp"), ("udp6", socket.AF_INET6, "udp")):
        for line in (proc_root / str(pid) / "net" / name).read_text().splitlines()[1:]:
            fields = line.split()
            require(len(fields) >= 10, "Malformed /proc socket table row")
            inode = int(fields[9])
            if inode not in inodes:
                continue  # Never retain other processes' namespace-wide rows.
            require(inode not in result, "Duplicate controlled socket inode in /proc")
            result[inode] = {"inode": inode, "protocol": kind,
                             "family": "ipv4" if family == socket.AF_INET else "ipv6",
                             "local": proc_address(fields[1], family),
                             "remote": proc_address(fields[2], family), "proc_state": fields[3]}
    return result


def psutil_address(address):
    return {"host": str(ipaddress.ip_address(address.ip)), "port": address.port} if address else None


def socket_snapshot(processes, psutil_module, identities, *, proc_root=Path("/proc")):
    """Cross-check only the two owned hop PIDs, never silently ignore access errors."""
    snapshot = {"monotonic": time.monotonic(), "processes": []}
    for role, core in processes.items():
        require(role in ("domestic", "overseas"), "Unexpected observed process role")
        require(core.process is not None and core.process.poll() is None, "Observed core exited unexpectedly")
        pid = core.process.pid
        process = psutil_module.Process(pid)
        require(process.create_time() == identities[role], "Observed process PID identity changed")
        before = fd_inodes(pid, proc_root)
        connections = process.net_connections(kind="inet")
        table = read_proc_sockets(pid, set(before.values()), proc_root)
        after = fd_inodes(pid, proc_root)
        rows, races = [], []
        for connection in connections:
            fd = connection.fd
            inode = before.get(fd)
            if fd < 0 or inode is None or after.get(fd) != inode:
                # This race is explicit, excluded from closure proof, and must
                # resolve in a later bounded snapshot; it is not a read error.
                races.append({"fd": fd, "before_inode": inode, "after_inode": after.get(fd),
                              "kind": "fd_changed_during_snapshot"})
                continue
            item = table.get(inode)
            if item is None:
                later = process.net_connections(kind="inet")
                require(not any(entry.fd == fd for entry in later), "Stable socket absent from /proc evidence")
                races.append({"fd": fd, "before_inode": inode, "after_inode": after.get(fd),
                              "kind": "socket_closed_during_snapshot"})
                continue
            local, remote = psutil_address(connection.laddr), psutil_address(connection.raddr)
            require(connection.type in (socket.SOCK_DGRAM, socket.SOCK_STREAM), "Unexpected controlled socket type")
            protocol = "udp" if connection.type == socket.SOCK_DGRAM else "tcp"
            require(item["family"] == ("ipv4" if connection.family == socket.AF_INET else "ipv6"),
                    "psutil and /proc disagree on controlled socket family")
            require(item["protocol"] == protocol and item["local"] == local,
                    "psutil and /proc disagree on controlled socket identity")
            if remote is not None:
                require(item["remote"] == remote, "psutil and /proc disagree on controlled remote tuple")
            else:
                require(item["remote"]["port"] == 0 and ipaddress.ip_address(item["remote"]["host"]).is_unspecified,
                        "Unexpected connected /proc tuple for an unconnected psutil socket")
            rows.append({**item, "fd": fd, "psutil_state": connection.status})
        stable_table_inodes = {inode for fd, inode in before.items()
                               if after.get(fd) == inode and inode in table}
        require({row["inode"] for row in rows} == stable_table_inodes,
                "A stable controlled /proc socket was omitted by psutil")
        snapshot["processes"].append({"role": role, "pid": pid, "create_time": identities[role],
                                      "sockets": rows, "races": races, "sample_completed_monotonic": time.monotonic()})
    snapshot["sample_completed_monotonic"] = time.monotonic()
    return snapshot


def stable_snapshot(processes, psutil_module, identities, events):
    # Read-only resampling handles an explicitly evidenced FD teardown race;
    # permission errors, PID changes and mismatched stable identities propagate.
    for attempt in range(3):
        snapshot = socket_snapshot(processes, psutil_module, identities)
        if not any(row["races"] for row in snapshot["processes"]):
            return snapshot
        events.event("socket_sampling_race", attempt=attempt + 1, snapshot=snapshot)
        if attempt < 2:
            time.sleep(.01)
    raise base.ExperimentError("Socket observation remained inconclusive after three bounded read-only samples")


def application_sockets(snapshot):
    upstream = next(item for item in snapshot["processes"] if item["role"] == "overseas")
    # Matches the original idle gate's source-bound overseas UDP criterion.
    return {item["inode"] for item in upstream["sockets"]
            if item["protocol"] == "udp" and item["local"]["host"] == "127.0.0.4"}


def classify_idle(snapshots, initial_inodes, last_reply):
    require(initial_inodes and snapshots, "No actual initial overseas UDP association observed")
    deadline = last_reply + IDLE_SECONDS
    require(snapshots[-1]["monotonic"] >= deadline, "Idle observation ended before six seconds")
    first_absent = None
    previous_end = last_reply
    for snapshot in snapshots:
        started, completed = snapshot["monotonic"], snapshot["sample_completed_monotonic"]
        require(previous_end <= started <= completed, "Socket sample time bounds are inconsistent")
        previous_end = completed
        require(not any(row["races"] for row in snapshot["processes"]),
                "Unresolved socket snapshot race cannot classify idle closure")
        current = application_sockets(snapshot)
        require(not current - initial_inodes, "A new application UDP socket appeared without a business packet")
        if not current:
            first_absent = first_absent or {"sample_started_monotonic": started, "sample_completed_monotonic": completed}
        else:
            require(first_absent is None, "An application UDP socket reappeared without a business packet")
    if first_absent is None:
        classification = "still_open_at_6s"
    elif first_absent["sample_completed_monotonic"] <= deadline:
        classification = "closed_naturally_within_6s"
    else:
        classification = "closure_boundary_inconclusive"
    return {"classification": classification, "conclusive": classification != "closure_boundary_inconclusive",
            "first_natural_absence_sample": first_absent,
            "at_six_seconds_monotonic": snapshots[-1]["monotonic"],
            "original_idle_gate_passed": False,
            "note": "Observation classification only; original test_14 result is not replaced."}


class HopLogs:
    def __init__(self, fixture, events):
        self.fixture, self.events = fixture, events
        self.offsets = {role: len(core.log_path.read_text(errors="replace"))
                        for role, core in self.processes().items()}

    def processes(self):
        return {"domestic": self.fixture.domestic, "overseas": self.fixture.upstream}

    def collect(self, phase):
        for role, core in self.processes().items():
            text = core.log_path.read_text(errors="replace")
            require(len(text) >= self.offsets[role], "Controlled core log unexpectedly shrank")
            fresh = text[self.offsets[role]:]
            # Only complete lines advance the cursor; partial EOF is not lost.
            complete, _, _ = fresh.rpartition("\n")
            if not complete:
                continue
            self.offsets[role] += len(complete) + 1
            for line in complete.splitlines():
                if "eof" in line.lower():
                    self.events.event("core_eof_log", phase=phase, role=role, pid=core.process.pid,
                                      line=self.fixture.safe(line))


def cleanup_proof(fixture, before_threads, events, psutil_module):
    processes = []
    for core in fixture.processes:
        require(core.process is not None, "A fixture core never acquired a process")
        status = core.process.poll()
        require(status is not None, "A fixture core survived cleanup")
        # Popen.wait in the original stop method reaps each owned PID.
        require(not psutil_module.pid_exists(core.process.pid), "An owned fixture PID still exists after cleanup")
        processes.append({"pid": core.process.pid, "returncode": status})
    targets = []
    for target in fixture.targets:
        target.audit()
        require(target.closed and not target.thread.is_alive(), "UDP target thread survived cleanup")
        require(Counter(target.received) == target.expected and not target.errors,
                "Final target ledger differs from authorized packets")
        targets.append({"host": target.host, "port": target.port, "closed": True,
                        "thread_alive": False, "received_count": len(target.received)})
    require(not fixture.root.exists(), "Temporary fixture material survived cleanup")
    require(not [thread for thread in threading.enumerate() if thread not in before_threads],
            "A newly created fixture thread survived cleanup")
    if fixture.answers is not None:
        require(fixture.answers.pending() == 0 and fixture.ordinary_answers.queries == [],
                "DNS fixture remained active or used ordinary fallback after cleanup")
    events.event("cleanup_verified", processes=processes, targets=targets, remaining_fixture_threads=0)


def run_case(label, fixture_type, psutil_module, events, exercise):
    before_threads = set(threading.enumerate())
    fixture = None
    problem = None
    try:
        with fixture_type(unittest.TestCase(), label) as fixture:
            exercise(fixture)
    except Exception as exc:
        problem = exc
        events.event("observation_error", exception=type(exc).__name__, message=clean(str(exc)))
    finally:
        if fixture is not None:
            try:
                cleanup_proof(fixture, before_threads, events, psutil_module)
            except Exception as exc:
                events.event("cleanup_error", exception=type(exc).__name__, message=clean(str(exc)))
                problem = problem or exc
        else:
            problem = problem or base.ExperimentError("Fixture did not initialize")
        events.data["status"] = "observed" if problem is None else "error"
        events.flush()
    if problem is not None:
        raise problem


def domain_observation(fixture_type, module, psutil_module, output):
    events = Events(output / "observations/domain-freeze.json", "frozen_domain_mapping")
    def exercise(f):
        trap = f.target_at(module.DENIED)
        require(trap.port != f.target.port, "Changed-port trap accidentally equals the initial application port")
        control = b"observation-private-trap-reachable"
        trap.direct(control)
        control_deliveries = Counter(trap.received)
        require(sum(control_deliveries.values()) == 1, "Private trap control was not exactly one packet")
        events.event("private_trap_positive_control", host=trap.host, port=trap.port,
                     packet=payload_identity(control), deliveries=1)
        config = f.guarded_config()
        domain = "frozen-mapping.example.test"
        f.answers.records[domain] = (module.TARGET,)
        f.start("mihomo", domestic=config)
        f.expect_dns(domain)
        initial_payload, changed_payload = b"observation-original-port", b"observation-changed-port"
        with module.RawXudpAssociation(f, domain, f.target.port) as association:
            send_and_record(f, association, domain, f.target.port, initial_payload,
                            initial_host=domain, initial_port=f.target.port,
                            association_id="domain-initial", events=events, label="original_port")
            initial_dns = list(f.answers.snapshot())
            f.answers.records[domain] = (module.DENIED,)
            events.event("dns_answer_changed", domain=domain, before=module.TARGET, after=module.DENIED,
                         initial_port=f.target.port, changed_port=trap.port)
            send_and_record(f, association, domain, trap.port, changed_payload,
                            initial_host=domain, initial_port=f.target.port,
                            association_id="domain-initial", events=events, label="changed_port")
            require(f.answers.snapshot() == initial_dns, "Existing association performed a second DNS lookup")
            f.audit()
            require(Counter(trap.received) == control_deliveries, "Protected packet reached the private trap")
        events.event("explicit_association_close", association_id="domain-initial")
        f.expect_dns(domain)
        f.raw_reject(b"observation-new-private-answer-denied", f.domestic, ("reject",),
                     host=domain, port=trap.port, target=trap)
        require(Counter(trap.received) == control_deliveries, "New private association delivered to the trap")
        require(Counter(f.target.received) == Counter(((module.UPSTREAM_EGRESS, initial_payload),
                                                       (module.UPSTREAM_EGRESS, changed_payload))),
                "Original target did not receive exactly the two authorized payloads")
        f.audit()
        events.event("new_association_rejected", association_id="domain-new", domain=domain, port=trap.port,
                     actual_rejection=f.observations[-1], dns_queries=f.answers.snapshot(),
                     private_control_deliveries=sum(control_deliveries.values()), protected_private_deliveries=0)
        events.data["classification"] = "same_association_kept_initial_domain_mapping_and_port"
        f.observations.append({"kind": "domain-freeze-observation", "original_gate_unchanged": True,
                               "original_target_business_deliveries": 2, "private_control_deliveries": 1,
                               "protected_private_deliveries": 0, "new_association_rejected": True})
    run_case(DOMAIN_CASE, fixture_type, psutil_module, events, exercise)
    return events.data


def idle_observation(fixture_type, module, psutil_module, output):
    events = Events(output / "observations/idle-sockets.json", "bounded_idle_socket_lifecycle")
    def exercise(f):
        f.start("mihomo", domestic=f.guarded_config(udp_timeout="2s"))
        processes = {"domestic": f.domestic, "overseas": f.upstream}
        identities = {role: psutil_module.Process(core.process.pid).create_time() for role, core in processes.items()}
        logs = HopLogs(f, events)
        # Original __exit__ stops/joins all cores before stack.close. This callback
        # runs while sanitized logs still exist, after that explicit cleanup.
        f.stack.callback(lambda: logs.collect("fixture_cleanup_after_process_stop"))
        snapshots = []
        with module.RawXudpAssociation(f, module.TARGET, f.target.port) as association:
            _, reply = send_and_record(f, association, module.TARGET, f.target.port, b"observation-idle-single-packet",
                                       initial_host=module.TARGET, initial_port=f.target.port,
                                       association_id="idle-single", events=events, label="last_business_packet")
            last_reply = reply["monotonic"]
            logs.collect("positive_setup_before_idle")
            events.event("idle_observation_started", route_timeout="2s", original_deadline_seconds=IDLE_SECONDS,
                         last_reply_monotonic=last_reply, further_business_packets=0)
            first = stable_snapshot(processes, psutil_module, identities, events)
            require(not any(row["races"] for row in first["processes"]), "Initial socket snapshot raced")
            initial = application_sockets(first)
            require(initial, "No live overseas application UDP socket observed after the real reply")
            snapshots.append(first)
            events.event("socket_snapshot", phase="idle_before_explicit_close", snapshot=first)
            while snapshots[-1]["monotonic"] < last_reply + IDLE_SECONDS:
                time.sleep(min(POLL_SECONDS, max(0, last_reply + IDLE_SECONDS - time.monotonic())))
                snapshot = stable_snapshot(processes, psutil_module, identities, events)
                snapshots.append(snapshot)
                events.event("socket_snapshot", phase="idle_before_explicit_close", snapshot=snapshot)
                logs.collect("idle_before_explicit_close")
                f.audit()  # Read-only accounting, never sends or renews traffic.
            events.data.update(classify_idle(snapshots, initial, last_reply))
            events.event("six_second_observation", classification=events.data["classification"],
                         application_socket_inodes=sorted(application_sockets(snapshots[-1])))
            require(events.data["conclusive"], "Natural closure timing crosses the original six-second boundary")
            events.event("explicit_client_close_begin", association_id="idle-single")
        events.event("explicit_client_close_complete", association_id="idle-single")
        close_deadline = time.monotonic() + POST_CLOSE_SECONDS
        while True:
            snapshot = stable_snapshot(processes, psutil_module, identities, events)
            require(not any(row["races"] for row in snapshot["processes"]), "Post-close socket snapshot raced")
            events.event("socket_snapshot", phase="after_explicit_client_close", snapshot=snapshot)
            logs.collect("after_explicit_client_close")
            if time.monotonic() >= close_deadline:
                break
            time.sleep(min(POLL_SECONDS, max(0, close_deadline - time.monotonic())))
        f.audit()
        f.observations.append({"kind": "idle-socket-observation", "classification": events.data["classification"],
                               "route_timeout": "2s", "observation_seconds": IDLE_SECONDS,
                               "business_packets": 1, "original_idle_gate_unchanged": True})
        events.event("fixture_cleanup_begin")
    run_case(IDLE_CASE, fixture_type, psutil_module, events, exercise)
    return events.data


def verify_observation_isolation(records, candidate, stock):
    expected = Counter((case, role) for case in CASES for role in ("overseas", "domestic", "device-mihomo"))
    require(Counter((row["case"], row["role"]) for row in records) == expected,
            "Observation must run exactly six known fixture process roles")
    for row in records:
        mihomo = row["role"] == "device-mihomo"
        require(type(row["mihomo"]) is bool and row["mihomo"] is mihomo, "Observation role/core family mismatch")
        identity = candidate if row["role"] == "domestic" else stock["mihomo" if mihomo else "sing-box"]
        require(row["binary"] == identity["path"] and row["sha256"] == identity["sha256"]
                and row["parser_passed"], "Observation binary isolation or parser proof failed")
        require(len(row["launches"]) == 1, "Observation process did not launch exactly once")
        launch = row["launches"][0]
        require(launch["ready"] and launch["sha256"] == identity["sha256"]
                and type(launch["pid"]) is int and launch["pid"] > 0,
                "Observation process launch identity is incomplete")


def verify_outputs(output):
    actual = {str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()}
    require(not actual - ARTIFACT_ALLOWLIST, "Unexpected observation artifact path")
    for path in output.rglob("*"):
        require(not path.is_symlink(), "Symlinks are forbidden in observation artifacts")
        if path.is_file():
            require(path.suffix in (".json", ".log"), "Non-text observation artifact")
            text = path.read_text()
            require(clean(text) == text, "Observation output contains unredacted fixture identity or material")


def execute(args, report):
    require(args.phase == "patched", "Runtime observations require the explicit patched candidate")
    original_env = {key: os.environ.get(key) for key in ("VUI_TEST_CORES", "VUI_TEST_MIHOMO")}
    require(all(original_env.values()), "Official fixed core paths are required")
    paths = {"sing-box": Path(original_env["VUI_TEST_CORES"]) / "sing-box",
             "mihomo": Path(original_env["VUI_TEST_MIHOMO"])}
    stock = {}
    for name, path in paths.items():
        path = path.resolve(strict=True)
        require(base.sha256(path) == base.STOCK_HASHES[name], "Official fixed core SHA mismatch: " + name)
        stock[name] = {"path": str(path), "sha256": base.STOCK_HASHES[name]}
    provenance = base.read_json(args.provenance)
    report.update(provenance=provenance, provenance_sha256=base.sha256(args.provenance),
                  source_identity=base.source_identity(base.ROOT, provenance),
                  observer_sha256=base.sha256(Path(__file__)))
    candidate = base.verify_candidate(args.candidate, provenance, args.phase, paths)
    report.update(candidate=candidate, stock=stock)
    cases = args.output / "cases"
    cases.mkdir()
    (args.output / "observations").mkdir()
    ledger = base.BinaryLedger(candidate, stock, args.output)
    problems = []
    with base.experiment_environment(cases), (args.output / "suite.log").open("w") as log:
        with redirect_stdout(log), redirect_stderr(log):
            module, _ = base.runtime_modules()
            module.RestrictedUdpPreflightTests.setUpClass()  # Unchanged official version checks.
            fixture = base.make_fixture(module.RestrictedUdpFixture, ledger)
            psutil_module = importlib.import_module("psutil")
            for name, observe in (("domain", domain_observation), ("idle", idle_observation)):
                try:
                    report[name] = observe(fixture, module, psutil_module, args.output)
                except Exception as exc:
                    problems.append({"observation": name, "exception": type(exc).__name__, "message": clean(str(exc))})
                    print(clean(traceback.format_exc()))
    require(all(os.environ.get(key) == value for key, value in original_env.items()),
            "Official binary environment changed during observation")
    base.source_identity(base.ROOT, provenance)
    for identity in [candidate, *stock.values()]:
        ledger.check(identity["path"])
    verify_observation_isolation(ledger.records, candidate, stock)
    report["errors"] = problems
    require(not problems, "One or more runtime observations failed; original gate results remain unchanged")
    for case in CASES:
        case_result = base.read_json(cases / (case + ".json"))
        require(case_result["status"] == "passed", "Original fixture cleanup/audit failed during observation")
        lifetime = base.read_json(args.output / "delivery-ledgers" / (case + ".json"))
        require(not lifetime["ordinary_dns"] and Counter(tuple(item) for item in lifetime["business_dns"])
                == Counter({tuple(item[:-1]): item[-1] for item in lifetime["expected_dns"]}),
                "Observation DNS lifetime ledger mismatch")
        for target in lifetime["udp_targets"]:
            require(target["closed"] and not target["errors"] and target["received"] == target["expected"],
                    "Observation UDP lifetime ledger mismatch")
    verify_outputs(args.output)
    report["observations_complete"] = True
    report["outcome"] = "bounded_observations_recorded"


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--phase", required=True, choices=("patched",))
    value.add_argument("--candidate", required=True, type=Path)
    value.add_argument("--provenance", required=True, type=Path)
    value.add_argument("--output", required=True, type=Path)
    return value


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        args.output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print("Observation refused: output already exists", file=sys.stderr)
        return 2
    report = {"schema_version": 1, "phase": args.phase, "observations_complete": False,
              "outcome": "observation_error", "product_qualified": False, "original_gate_unchanged": True}
    try:
        execute(args, report)
    except Exception as exc:
        report["blocker"] = clean(str(exc))
        (args.output / "runner-error.log").write_text(clean(traceback.format_exc()))
    finally:
        save(args.output / "result.json", report)
    print(json.dumps({key: report[key] for key in ("outcome", "observations_complete", "product_qualified")}))
    return 0 if report["observations_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
