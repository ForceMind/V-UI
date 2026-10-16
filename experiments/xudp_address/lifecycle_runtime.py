#!/usr/bin/env python3
"""Three-way, explicitly labelled lifecycle experiment; never a product gate."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import time
import traceback
import unittest
from unittest.mock import patch


def sibling(name):
    spec = importlib.util.spec_from_file_location("lifecycle_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = sibling("run_experiment")
observer = sibling("observe_runtime")
require = base.require
PHASES = ("baseline", "patched", "lifecycle")
KIND = "vui-xudp-lifecycle-source-experiment"
SOURCE_FILE_HASHES = {
    "sing_vmess_xudp": "11497d190defff25ed8fcc8a5d8123cd9fcb1cfbce07d068664164468d118cfa",
    "sing_box_route_conn": "af68e556e07dfa861f1dc46c2f7232595c7912acb491b6077e555c61dda8b78c",
}
PATCH_IDENTITIES = {
    "remoteaddr": {"sha256": "ee08c4049b05d0fd72b1609839af574fe9be59b5e6c1ed4a1da54f0ffcb3a8ae",
                   "after": "16f74a540504d2ecfad28633f6b766b7755ca773ea59e1f21c6bcc5f2ba0241f"},
    "lifecycle": {"sha256": "b1ddb2c13896d06cf86d401044cafc9cb8f15ebbe66664fbb443f8ad13847010",
                  "after": "e4690962eec3e0b1c039d1155e21e2c1fcfb34e20c3c530ac69a445f471fcfa1"},
}
IDLE_SECONDS = 6.0
ACTIVITY_INTERVAL = .5
ACTIVITY_PACKETS = 17
RESTARTS = 3
DOMAIN = "lifecycle_domain_equivalence"
IDLES = ("lifecycle_idle_mihomo", "lifecycle_idle_singbox")
ACTIVE = "lifecycle_active_sibling"
RESTART_CASES = ("lifecycle_restart_mihomo", "lifecycle_restart_singbox")
EXTRA_CASES = (DOMAIN, *IDLES, ACTIVE, *RESTART_CASES)
EXTRA_CLIENTS = dict(zip(EXTRA_CASES, ("mihomo", "mihomo", "singbox", "mihomo", "mihomo", "singbox")))
ALL_CASES = tuple(base.expected_cases("patched")) + EXTRA_CASES
ARTIFACT_ALLOWLIST = frozenset({
    "result.json", "process-binaries.json", "suite.log", "runner-error.log",
    *(f"cases/{case}{extension}" for case in ALL_CASES for extension in (".json", ".log")),
    *(f"delivery-ledgers/{case}.json" for case in ALL_CASES),
    *(f"observations/{case}.json" for case in EXTRA_CASES),
})


def clean(text):
    try:
        text = observer.clean(text)
    except ValueError as exc:
        raise base.ExperimentError(str(exc)) from exc
    return re.sub(r"\bb'(?:\\.|[^'\\])*'|\bb\"(?:\\.|[^\"\\])*\"", "<packet-bytes>", text)


def scrub(value):
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, dict):
        return {clean(key): scrub(item) for key, item in value.items()}
    return value


def save(path, value):
    Path(path).write_text(json.dumps(scrub(value), indent=2, sort_keys=True) + "\n")


class Events(observer.Events):
    def flush(self):
        save(self.path, self.data)


def digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_provenance(data, phase):
    require(phase in PHASES, "Unknown lifecycle phase")
    require(type(data.get("schema_version")) is int and data["schema_version"] == 2 and data.get("kind") == KIND,
            "Expected the explicit schema-2 lifecycle provenance")
    require(data.get("phase") == phase, "Lifecycle phase mismatch")
    commit = data.get("vui_source_commit", "")
    require(isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit), "Missing exact source commit")
    version = f"1.14.2-vui-xudp-{phase}-experiment-{commit[:12]}"
    require(data.get("version") == version, "Wrong experimental version identity")
    require(digest(data.get("binary_sha256")) and data["binary_sha256"] not in base.STOCK_HASHES.values(),
            "Invalid or official candidate hash")
    for name, (tag, sha) in base.SOURCE_COMMITS.items():
        require(data.get(name) == {"version": tag, "commit": sha}, "Wrong fixed source: " + name)
    build = data.get("build", {})
    for name, value in {"go_version": "go1.25.14", "goos": "linux", "goarch": "amd64", "tags": [],
                        "cgo_enabled": "0", "gotoolchain": "local", "gowork": "off",
                        "trimpath": True, "buildvcs": False}.items():
        require(build.get(name) == value, "Wrong build parameter: " + name)
    require(build.get("ldflags") == "-X github.com/sagernet/sing-box/constant.Version=" + version,
            "Experimental version build flags mismatch")
    require(digest(build.get("go_binary_sha256")), "Compiler hash is missing")
    source, final = data.get("source_hashes", {}), data.get("phase_source_hashes", {})
    for name in ("sing_vmess_xudp", "sing_box_route_conn", "route_lifecycle_test_sha256"):
        require(digest(source.get(name)), "Missing source hash: " + name)
    require(all(source[name] == value for name, value in SOURCE_FILE_HASHES.items()), "Official source file baseline changed")
    for name in ("xudp", "route_conn"):
        require(digest(final.get(name)), "Missing phase source hash: " + name)
    expected = []
    for name, field, enabled, file, original, final_name in (
        ("remoteaddr", "patch", phase != "baseline", "sing-vmess/xudp.go", "sing_vmess_xudp", "xudp"),
        ("lifecycle", "lifecycle_patch", phase == "lifecycle", "sing-box/route/conn.go", "sing_box_route_conn", "route_conn"),
    ):
        change = data.get(field, {})
        require(type(change.get("applied")) is bool and change["applied"] is enabled,
                "Wrong explicit patch state: " + name)
        if enabled:
            require(digest(change.get("sha256")) and isinstance(change.get("path"), str) and change["path"],
                    "Missing applied patch identity: " + name)
            require(change["sha256"] == PATCH_IDENTITIES[name]["sha256"]
                    and final[final_name] == PATCH_IDENTITIES[name]["after"], "Applied patch or resulting source is not the reviewed identity")
            expected.append({"name": name, "sha256": change["sha256"], "path": change["path"],
                             "source_file": file, "before_sha256": source[original], "after_sha256": final[final_name]})
        else:
            require(change.get("sha256") is None and change.get("path") is None,
                    "Unapplied patch has an identity")
            require(source[original] == final[final_name], "Unpatched source differs from official baseline")
    require(data.get("patches") == expected, "Unknown, reordered or inconsistent patch chain")
    return version


def verify_candidate(path, data, phase, stock, run=subprocess.run):
    version = validate_provenance(data, phase)
    path = Path(path).resolve(strict=True)
    require(path.is_file() and os.access(path, os.X_OK), "Candidate is not executable")
    require(not any(path.samefile(item["path"]) for item in stock.values()), "Candidate aliases an official core")
    require(base.sha256(path) == data["binary_sha256"], "Candidate SHA mismatch")
    require(base.sha256(Path(__file__).with_name("route_lifecycle_test.go")) == data["source_hashes"]["route_lifecycle_test_sha256"],
            "Source lifecycle control test differs from build provenance")
    for change in data["patches"]:
        require(base.sha256(Path(change["path"])) == change["sha256"], "Applied patch file SHA mismatch")
    result = run([str(path), "version"], capture_output=True, text=True, timeout=15)
    output = result.stdout + result.stderr
    require(result.returncode == 0 and output.splitlines() and output.splitlines()[0] == "sing-box version " + version,
            "Candidate version is not its exact lifecycle experiment identity")
    return {"path": str(path), "sha256": data["binary_sha256"], "version_output": output}


def known_failure(err):
    if not isinstance(err[1], AssertionError) or any("D2 lifetime cleanup failed:" in note
                                                   for note in getattr(err[1], "__notes__", ())):
        return None
    tb = err[2]
    while tb:
        frame, values = tb.tb_frame, tb.tb_frame.f_locals
        if Path(frame.f_code.co_filename).resolve() == base.ROOT / "tests/test_restricted_udp_preflight.py":
            line = Path(frame.f_code.co_filename).read_text().splitlines()[tb.tb_lineno - 1].strip()
            f = values.get("f")
            if f and f.label == base.METHODS[12] and frame.f_code.co_name == "raw_echo" and line == "self.assertIn(got_host, expected_hosts)":
                association, actual = values["association"], values["actual"]
                if (values["got_host"] == values["host"] == association.initial[0]
                        and values["expected_hosts"] == {actual.host}
                        and values["port"] != actual.port and values["got_port"] == actual.port
                        and values["got_payload"] == values["payload"] and actual.port == association.initial[1]):
                    return {"kind": "frozen_domain_reply_excluded_by_original_allowlist", "line": tb.tb_lineno,
                            "initial_domain": association.initial[0], "initial_port": association.initial[1],
                            "returned_host": values["got_host"], "returned_port": values["got_port"],
                            "requested_port": values["port"], "payload": observer.payload_identity(values["got_payload"])}
            if (f and f.label == base.METHODS[13] and frame.f_code.co_name == base.METHODS[13]
                    and line == 'self.assertLess(time.monotonic(), deadline, "Idle UDP association did not close")'):
                match = re.fullmatch(r"([0-9.]+) not less than ([0-9.]+) : Idle UDP association did not close", str(err[1]))
                if match and float(match[1]) >= float(match[2]) == values["deadline"]:
                    return {"kind": "original_six_second_idle_deadline", "line": tb.tb_lineno,
                            "observed_monotonic": float(match[1]), "deadline_monotonic": values["deadline"]}
        tb = tb.tb_next
    return None


class MatrixResult(base.ExperimentResult):
    def _failure(self, test, err, subtest=None):
        super()._failure(test, err, subtest)
        self.failure_records[-1]["known_failure"] = known_failure(err)


def run_suite(suite, log):
    return unittest.TextTestRunner(stream=log, verbosity=2, resultclass=MatrixResult).run(suite).summary()


def assess_original(phase, groups):
    require(phase in PHASES, "Unknown original-matrix phase")
    original, tcp, dns = (groups[name] for name in ("original", "tcp", "dns_rebinding"))
    base.assess_suite("patched", tcp, tcp=True)
    for result, methods in ((original, base.METHODS), (dns, base.DNS_METHODS)):
        require(result["tests_run"] == len(methods) and Counter(row["method"] for row in result["methods"]) == Counter(methods),
                "Original method coverage changed")
        require(not result["nonpass"], "Original matrix contains errors, skips or expected-failure conversions")
    if phase == "baseline":
        base.assess_suite("baseline", original)
        require(dns["failed_methods"] == dns["failure_records"] == 1 and dns["passed_methods"] == 0,
                "Baseline same-port DNS must preserve its initial port failure")
        failure = dns["failures"][0]
        require(failure["method"] == base.DNS_METHODS[0] and failure["exception"] == "AssertionError"
                and failure["initial_port"] is not None
                and failure["initial_port"]["case"] == base.DNS_METHODS[0], "Unrecognized baseline DNS failure")
        proof = failure["initial_port"]
        require(proof["actual_port"] == proof["upstream_tcp_port"] != proof["expected_target_port"],
                "Baseline extra DNS failure is not the exact upstream reply-port defect")
    else:
        base.assess_suite("patched", dns, dns=True)
        expected = {base.METHODS[12]: "frozen_domain_reply_excluded_by_original_allowlist"}
        if phase == "patched":
            expected[base.METHODS[13]] = "original_six_second_idle_deadline"
        require(Counter(row["method"] for row in original["failures"]) == Counter(expected.keys()),
                "Original matrix has unexpected or missing failure records")
        for row in original["failures"]:
            require(row["exception"] == "AssertionError" and (row.get("known_failure") or {}).get("kind") == expected[row["method"]],
                    "Original failure is not the precise preserved assertion")
        require(original["failed_methods"] == len(expected) and original["passed_methods"] == 17 - len(expected),
                "Original pass/fail counts mismatch")
        require(all(row["status"] == ("failed" if row["method"] in expected else "passed") for row in original["methods"]),
                "Original method statuses differ from actual classified failures")
    return {"executed": 20, "passed": sum(group["passed_methods"] for group in groups.values()),
            "failed": sum(group["failed_methods"] for group in groups.values()), "original_suite_passed": False,
            "domain_equivalence_is_separate": True}


def expected_roles(phase):
    result = base.expected_process_roles("baseline" if phase == "baseline" else "patched")
    if phase == "baseline":
        for case in (*base.TCP_METHODS, *base.DNS_METHODS):
            result.update((case, role) for role in ("overseas", "domestic", "device-" + base.case_client(case)))
    for case, client in EXTRA_CLIENTS.items():
        result.update((case, role) for role in ("overseas", "domestic", "device-" + client))
        if case in RESTART_CASES:
            result.update({(case, "domestic-reconfigured"): RESTARTS,
                           (case, "device-reconfigured-" + client): RESTARTS})
    return result


def verify_isolation(phase, records, candidate, stock):
    require(Counter((r["case"], r["role"]) for r in records) == expected_roles(phase), "Wrong per-case process-role coverage")
    for row in records:
        mihomo = row["role"] in ("device-mihomo", "device-reconfigured-mihomo")
        require(type(row["mihomo"]) is bool and row["mihomo"] is mihomo, "Role/core family mismatch")
        selected = candidate if row["role"] in base.DOMESTIC_ROLES else stock["mihomo" if mihomo else "sing-box"]
        require(row["binary"] == selected["path"] and row["sha256"] == selected["sha256"] and row["parser_passed"],
                "Process binary identity or parser proof failed")
        launches = 0 if row["case"] == base.METHODS[15] else 1
        if phase != "baseline" and row["role"] == "overseas" and row["case"].endswith("-stopped"):
            launches = 2
        require(len(row["launches"]) == launches, "Wrong number of actual process launches")
        for launch in row["launches"]:
            require(launch["ready"] and launch["sha256"] == selected["sha256"] and type(launch["pid"]) is int and launch["pid"] > 0,
                    "Process launch identity/readiness missing")
        require(digest(row.get("config_sha256")) and row.get("config_shape"), "Process configuration identity missing")


def address_verdict(phase, host, port, payload, expected_host, expected_port, expected_payload, upstream_port):
    require(phase in PHASES, "Unknown address observation phase")
    require(host in {expected_host, "127.0.0.3"} and payload == expected_payload, "Reply host or payload escaped its initial mapping")
    if port == expected_port:
        return "passed"
    require(phase == "baseline" and port == upstream_port != expected_port, "Unexpected reply-port failure")
    return "known_baseline_address_failure"  # Recorded failure, never a passing address gate.


class WireReader:
    """Bounded three-session decoder. Does not alter the original raw helper."""
    def __init__(self, decode_address):
        self.decode_address = decode_address
        self.buffer = bytearray()
        self.response_read = False

    def feed(self, data):
        self.buffer.extend(data)
        require(len(self.buffer) <= 131072, "Unbounded mux response buffer")
        result = []
        if not self.response_read:
            if len(self.buffer) < 2:
                return result
            require(self.buffer[:2] == b"\0\0", "Unexpected VLESS response version/addons")
            del self.buffer[:2]
            self.response_read = True
        while len(self.buffer) >= 2:
            length = int.from_bytes(self.buffer[:2], "big")
            require(4 <= length <= 1024, "Invalid mux header length")
            if len(self.buffer) < 2 + length:
                break
            header = bytes(self.buffer[2:2 + length])
            session, status, flags = int.from_bytes(header[:2], "big"), header[2], header[3]
            require(session in (0, 1, 2) and status in (2, 3) and flags in (0, 1), "Unknown mux session/status/flags")
            position = 2 + length
            address = None
            if status == 3:
                require(length == 4 and flags == 0, "Malformed or error-bearing session End")
                payload = None
            else:
                if length > 4:
                    require(header[4] == 2, "Mux response is not UDP")
                    address = self.decode_address(header[5:])
                if flags:
                    if len(self.buffer) < position + 2:
                        break
                    size = int.from_bytes(self.buffer[position:position + 2], "big")
                    position += 2
                    if len(self.buffer) < position + size:
                        break
                    payload = bytes(self.buffer[position:position + size])
                    position += size
                else:
                    payload = None
            del self.buffer[:position]
            result.append({"session": session, "status": status, "address": address, "payload": payload})
            require(len(result) <= 64, "Too many mux control frames")
        return result


class MuxProbe:
    def __init__(self, f, module, phase, events):
        self.f, self.module, self.phase, self.events = f, module, phase, events
        self.raw = module.RawXudpAssociation(f, module.TARGET, f.target.port)
        self.reader = WireReader(module.decode_xudp_address)
        self.initial, self.pending, self.replies, self.ends = {}, {}, {}, {}
        self.frames = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.events.event("explicit_client_close_begin")
        self.raw.__exit__(*args)
        self.closed = True
        self.events.event("explicit_client_close_complete")

    def send(self, session, payload, *, host=None, port=None):
        host, port = host or self.module.TARGET, port or self.f.target.port
        require(session in (0, 1, 2) and session not in self.ends and session not in self.pending, "Duplicate/closed pending mux session")
        first = session not in self.initial
        if first:
            self.initial[session] = (host, port)
        self.f.target.expect(self.module.UPSTREAM_EGRESS, payload)
        frame = self.module.xudp_frame(host, port, payload, first=first)
        frame = frame[:2] + struct.pack("!H", session) + frame[4:]
        event = self.events.event("business_packet_send", session=session, requested_host=host, requested_port=port,
                                  payload=observer.payload_identity(payload))
        self.pending[session] = (payload, event["monotonic"])
        self.raw.stream.sendall(frame)
        return event

    def pump(self, until):
        remaining = until - time.monotonic()
        if remaining <= 0:
            return
        self.raw.stream.settimeout(min(.03, remaining))
        try:
            data = self.raw.stream.recv(65536)
        except socket.timeout:
            return  # Passive bounded read only; no packet retry or renewal.
        require(data, "Parent TLS closed before explicit client cleanup")
        for frame in self.reader.feed(data):
            self.frames += 1
            require(self.frames <= 128, "Unexpected mux frame flood")
            session = frame["session"]
            require(session in self.initial, "Response for an unopened session")
            if frame["status"] == 3:
                require(session not in self.ends and session not in self.pending, "Duplicate or premature session End")
                self.ends[session] = self.events.event("session_end", session=session, wire_status=3, wire_flags=0)
            elif frame["payload"] is not None:
                require(session in self.pending and session not in self.ends, "Unsolicited or late business response")
                payload, sent = self.pending.pop(session)
                host, port = frame["address"] or self.initial[session]
                verdict = address_verdict(self.phase, host, port, frame["payload"], self.initial[session][0],
                                          self.initial[session][1], payload, self.f.upstream_port)
                self.replies[session] = self.events.event("business_packet_reply", session=session, host=host, port=port,
                    payload=observer.payload_identity(frame["payload"]), address_verdict=verdict, send_monotonic=sent)
                self.f.target.audit()
            else:
                self.events.event("mux_control", session=session, wire_status=2)

    def reply(self, session, timeout=4):
        deadline = time.monotonic() + timeout
        while session in self.pending:
            require(time.monotonic() < deadline, "Single business packet received no response within its bound")
            self.pump(deadline)
        return self.replies[session]


def socket_keys(snapshot):
    return {(p["role"], p["pid"], s["inode"]): s for p in snapshot["processes"] for s in p["sockets"]}


def active_keys(snapshot):
    return {key for key, row in socket_keys(snapshot).items() if row["protocol"] != "tcp" or row["proc_state"] != "0A"}


def paired_leg(before, after, upstream_port):
    added = {key: row for key, row in socket_keys(after).items() if key not in socket_keys(before)}
    domestic = [(key, row) for key, row in added.items() if key[0] == "domestic" and row["protocol"] == "tcp"
                and row["remote"] == {"host": "127.0.0.2", "port": upstream_port}]
    overseas = [(key, row) for key, row in added.items() if key[0] == "overseas" and row["protocol"] == "tcp"
                and row["local"] == {"host": "127.0.0.2", "port": upstream_port} and row["proc_state"] == "01"]
    udp = [(key, row) for key, row in added.items() if key[0] == "overseas" and row["protocol"] == "udp"
           and row["local"]["host"] == "127.0.0.4"]
    require(len(domestic) == len(overseas) == len(udp) == 1, "Cannot identify exactly one new association socket trio")
    require(domestic[0][1]["local"] == overseas[0][1]["remote"], "Two-hop TCP tuples are not reciprocal")
    return {"keys": [list(item[0]) for item in (domestic[0], overseas[0], udp[0])],
            "domestic_local": domestic[0][1]["local"], "upstream_port": upstream_port}


def verify_new_sockets(before, after, leg, *, ingress_port=None):
    added = set(socket_keys(after)) - set(socket_keys(before))
    expected = set(map(tuple, leg["keys"]))
    if ingress_port is not None:
        parents = added - expected
        require(len(parents) == 1, "New parent TLS socket coverage is not exact")
        key = next(iter(parents))
        row = socket_keys(after)[key]
        require(key[0] == "domestic" and row["protocol"] == "tcp" and row["proc_state"] == "01"
                and row["local"] == {"host": "127.0.0.1", "port": ingress_port},
                "Unexpected socket substituted for the new incoming parent TLS")
        expected |= parents
    require(added == expected, "Declared association admitted extra or missing socket identities")


def leg_present(snapshot, leg):
    return sorted(set(map(tuple, leg["keys"])) & socket_keys(snapshot).keys())


def classify_leg(samples, leg, last_reply, end, eof_events):
    deadline = last_reply + IDLE_SECONDS
    require(samples and samples[-1]["monotonic"] >= deadline, "Lifecycle sample window ended before six seconds")
    absent, previous_end = None, last_reply
    for row in samples:
        require(previous_end <= row["monotonic"] <= row["sample_completed_monotonic"], "Nonmonotonic lifecycle samples")
        previous_end = row["sample_completed_monotonic"]
        require(not any(p["races"] for p in row["processes"]), "Unresolved socket race")
        if not leg_present(row, leg):
            absent = absent or row["sample_completed_monotonic"]
        else:
            require(absent is None, "Closed association inode reappeared")
    eof = [e for e in eof_events if e["kind"] == "core_eof_log" and e["role"] == "overseas"
           and f"{leg['domestic_local']['host']}:{leg['domestic_local']['port']}" in e["line"]
           and last_reply <= e["monotonic"] <= deadline and e["phase"] == "natural_idle"]
    if absent is None:
        status = "still_open_at_6s"
    elif absent > deadline:
        status = "boundary_inconclusive"
    elif end is None or end["monotonic"] > deadline or not eof:
        status = "missing_session_end_or_official_peer_eof"
    else:
        status = "closed_with_session_end_and_official_peer_release"
    return {"classification": status, "released_by_6s": status == "closed_with_session_end_and_official_peer_release",
            "deadline_monotonic": deadline, "first_absence_completed_monotonic": absent,
            "remaining_keys_at_6s": leg_present(samples[-1], leg), "session_end": end,
            "official_peer_eof_count": len(eof)}


class SocketWatch:
    def __init__(self, f, psutil_module, events):
        self.f, self.psutil, self.events = f, psutil_module, events
        self.processes = {"domestic": f.domestic, "overseas": f.upstream}
        self.identities = {r: psutil_module.Process(c.process.pid).create_time() for r, c in self.processes.items()}
        self.logs = observer.HopLogs(f, events)
        self.samples = []
        self.allowed = None

    def snapshot(self, phase):
        row = observer.stable_snapshot(self.processes, self.psutil, self.identities, self.events)
        if self.allowed is not None:
            require(socket_keys(row).keys() <= self.allowed, "Unexpected replacement or unrequested socket appeared")
        self.events.event("socket_snapshot", phase=phase, snapshot=row)
        self.logs.collect("natural_idle" if phase == "natural_idle" else phase)
        self.f.audit()
        self.samples.append(row)
        return row

    def baseline(self):
        deadline = time.monotonic() + 1
        while True:
            row = self.snapshot("ready_before_business")
            if not active_keys(row):
                return row
            require(time.monotonic() < deadline, "Ready process socket baseline did not settle")
            time.sleep(.03)

    def until(self, probe, deadline, *, phase="natural_idle"):
        next_sample = time.monotonic()
        while time.monotonic() < deadline:
            if time.monotonic() >= next_sample:
                self.snapshot(phase)
                next_sample = time.monotonic() + .05
            probe.pump(min(deadline, next_sample))
        return self.snapshot(phase)  # A crossing sample is never promoted to <=6s.


def require_phase_lifecycle(phase, result):
    require(phase in PHASES, "Unknown lifecycle observation phase")
    if phase == "lifecycle":
        require(result["released_by_6s"], "Lifecycle candidate did not release the exact association within six seconds")
    else:
        require(result["classification"] == "still_open_at_6s" and len(result["remaining_keys_at_6s"]) == 3,
                "Control no longer reproduces all three idle association sockets at six seconds")
        require(result["session_end"] is None and result["official_peer_eof_count"] == 0,
                "Control unexpectedly ended a session or its official peer")


def device_recovery(f, phase, events, payload, *, on_live=None):
    helper = importlib.import_module("udp_dns_capability_helpers")
    f.target.expect("127.0.0.4", payload)
    result = None
    with helper.SocksDatagrams(f.client_port) as association:
        try:
            events.event("device_business_send", payload=observer.payload_identity(payload),
                         requested_host=f.target.host, requested_port=f.target.port)
            association.send(f.target.host, f.target.port, payload)  # One packet, no retries.
            association.stream.settimeout(4)
            response, relay = association.stream.recvfrom(65535)
            require(relay == association.relay, "Official device reply came from an unexpected SOCKS relay")
            host, port, received = helper.decode_socks_datagram(response)
            verdict = address_verdict(phase, host, port, received, f.target.host, f.target.port, payload, f.upstream_port)
            f.target.audit()
            events.event("device_recovery", address_verdict=verdict, payload=observer.payload_identity(received),
                         host=host, port=port)
            if on_live is not None:
                result = on_live()
        finally:
            events.event("explicit_device_client_close_begin")
    events.event("explicit_device_client_close_complete")
    return result


def domain_case(f, module, phase, events, psutil_module):
    trap = f.target_at(module.DENIED)
    require(trap.port != f.target.port, "Domain changed-port trap collided")
    trap.direct(b"lifecycle-domain-trap-control")
    control = Counter(trap.received)
    require(sum(control.values()) == 1, "Domain trap positive control count mismatch")
    config = f.guarded_config()
    name = "lifecycle-freeze.example.test"
    f.answers.records[name] = (module.TARGET,)
    f.start("mihomo", domestic=config)
    f.expect_dns(name)
    with MuxProbe(f, module, phase, events) as probe:
        probe.send(0, b"lifecycle-domain-first", host=name)
        probe.reply(0)
        initial_dns = list(f.answers.snapshot())
        f.answers.records[name] = (module.DENIED,)
        events.event("dns_answer_changed", domain=name, initial_address=module.TARGET, changed_address=module.DENIED,
                     initial_port=f.target.port, requested_changed_port=trap.port)
        probe.send(0, b"lifecycle-domain-second", host=name, port=trap.port)
        probe.reply(0)
        require(f.answers.snapshot() == initial_dns, "Existing domain association queried DNS again")
        require(Counter(trap.received) == control, "Changed-port protected packet reached the private trap")
    f.expect_dns(name)
    f.raw_reject(b"lifecycle-domain-new-denied", f.domestic, ("reject",), host=name, port=trap.port, target=trap)
    require(Counter(trap.received) == control and len(f.target.received) == 2, "Domain lifetime delivery counts mismatch")
    f.audit()
    events.event("domain_equivalence", passed=phase != "baseline", original_test13_unchanged=True,
                 business_deliveries=2, private_control_deliveries=1, protected_private_deliveries=0,
                 new_association_rejection=f.observations[-1], dns_queries=f.answers.snapshot())


def idle_case(f, module, phase, events, psutil_module, client):
    f.start(client, domestic=f.guarded_config(udp_timeout="2s"))
    watch = SocketWatch(f, psutil_module, events)
    before = watch.baseline()
    with MuxProbe(f, module, phase, events) as probe:
        probe.send(0, b"lifecycle-idle-once")
        reply = probe.reply(0)
        first = watch.snapshot("natural_idle")
        leg = paired_leg(before, first, f.upstream_port)
        verify_new_sockets(before, first, leg, ingress_port=f.ingress_port)
        watch.allowed = socket_keys(first).keys()
        events.event("association_socket_identity", session=0, leg=leg, route_timeout="2s", max_idle_seconds=6)
        watch.until(probe, reply["monotonic"] + IDLE_SECONDS)
        result = classify_leg([s for s in watch.samples if s["monotonic"] >= reply["monotonic"]], leg,
                              reply["monotonic"], probe.ends.get(0), events.data["events"])
        events.event("idle_classification", **result)
        require_phase_lifecycle(phase, result)
    device_recovery(f, phase, events, b"lifecycle-official-device-recovery")


def active_case(f, module, phase, events, psutil_module):
    f.start("mihomo", domestic=f.guarded_config(udp_timeout="2s"))
    watch = SocketWatch(f, psutil_module, events)
    before = watch.baseline()
    with MuxProbe(f, module, phase, events) as probe:
        probe.send(0, b"lifecycle-idle-sibling")
        idle_reply = probe.reply(0)
        first = watch.snapshot("natural_idle")
        idle_leg = paired_leg(before, first, f.upstream_port)
        verify_new_sockets(before, first, idle_leg, ingress_port=f.ingress_port)
        parent_keys = active_keys(first) - set(map(tuple, idle_leg["keys"]))
        require(len(parent_keys) == 1 and next(iter(parent_keys))[0] == "domestic", "Shared incoming TLS identity is ambiguous")
        started = time.monotonic()
        previous_send = None
        active_leg, active_reply, idle_result, reopened_leg, reopened_reply = None, None, None, None, None
        for index in range(ACTIVITY_PACKETS):
            planned = started + index * ACTIVITY_INTERVAL
            while time.monotonic() < planned:
                next_deadline = min(planned, idle_reply["monotonic"] + IDLE_SECONDS) if idle_result is None else planned
                watch.until(probe, next_deadline)
                if idle_result is None and time.monotonic() >= idle_reply["monotonic"] + IDLE_SECONDS:
                    idle_result = classify_leg([s for s in watch.samples if s["monotonic"] >= idle_reply["monotonic"]],
                        idle_leg, idle_reply["monotonic"], probe.ends.get(0), events.data["events"])
                    events.event("idle_sibling_classification", **idle_result)
                    require_phase_lifecycle(phase, idle_result)
                    # Open a NEW session only after the original idle verdict.
                    # For lifecycle this must follow the actual End of 0000;
                    # controls preserve their missing-End failure explicitly.
                    reopen_before = watch.snapshot("before_same_parent_new_session")
                    require(parent_keys <= socket_keys(reopen_before).keys(), "Parent TLS disappeared before session reuse")
                    if phase == "lifecycle":
                        require(0 in probe.ends, "No actual old session End before parent reuse")
                    watch.allowed = None  # Exactly one declared new business session is about to open.
                    sent = probe.send(2, b"lifecycle-new-session-same-parent")
                    reopened_reply = probe.reply(2, timeout=1.5)
                    reopened = watch.snapshot("after_same_parent_new_session")
                    reopened_leg = paired_leg(reopen_before, reopened, f.upstream_port)
                    verify_new_sockets(reopen_before, reopened, reopened_leg)
                    require(parent_keys <= socket_keys(reopened).keys() and len(leg_present(reopened, active_leg)) == 3,
                            "New session did not preserve the active sibling and parent TLS")
                    watch.allowed = socket_keys(reopened).keys()
                    events.event("same_parent_new_session", session=2, previous_session=0,
                                 previous_end=probe.ends.get(0), sent_monotonic=sent["monotonic"],
                                 parent_keys=sorted(parent_keys), leg=reopened_leg,
                                 after_end_gate_passed=phase == "lifecycle")
            sent = probe.send(1, ("lifecycle-active-%02d" % index).encode())
            if previous_send is not None:
                require(0 < sent["monotonic"] - previous_send < 2, "Activity schedule missed the two-second renewal window")
            previous_send = sent["monotonic"]
            active_reply = probe.reply(1, timeout=1.5)
            current = watch.snapshot("natural_idle")
            if active_leg is None:
                active_leg = paired_leg(first, current, f.upstream_port)
                verify_new_sockets(first, current, active_leg)
                watch.allowed = socket_keys(current).keys()
            require(parent_keys <= socket_keys(current).keys() and len(leg_present(current, active_leg)) == 3,
                    "Idle sibling canceled the active session or its shared parent TLS")
            require(1 not in probe.ends, "Active session ended during regular business traffic")
        require(previous_send - started >= 8, "Activity did not extend across the full eight seconds")
        require(idle_result is not None and reopened_leg is not None, "Idle/new-session path did not complete")
        # The new session has its OWN unchanged six-second deadline, not B's.
        reopened_last = reopened_reply["monotonic"]
        watch.until(probe, reopened_last + IDLE_SECONDS)
        reopened_result = classify_leg([s for s in watch.samples if s["monotonic"] >= reopened_last], reopened_leg,
                                      reopened_last, probe.ends.get(2), events.data["events"])
        events.event("reopened_session_idle_classification", **reopened_result)
        require_phase_lifecycle(phase, reopened_result)
        last = active_reply["monotonic"]
        watch.until(probe, last + IDLE_SECONDS)
        result = classify_leg([s for s in watch.samples if s["monotonic"] >= last], active_leg, last,
                              probe.ends.get(1), events.data["events"])
        events.event("active_then_idle_classification", packets=ACTIVITY_PACKETS, **result)
        require_phase_lifecycle(phase, result)
        require(len(f.target.received) == ACTIVITY_PACKETS + 2, "Active/reopened sibling delivery cardinality mismatch")


def wait_restart_release(f, psutil_module, events, old_leg, deadline):
    watch = SocketWatch(f, psutil_module, events)
    while True:
        row = watch.snapshot("after_explicit_restart")
        if not leg_present(row, old_leg) and not active_keys(row):
            require(row["sample_completed_monotonic"] <= deadline, "Restart closure crossed its separate six-second boundary")
            return row
        require(time.monotonic() < deadline, "Restart left old association resources alive")
        time.sleep(.03)


def restart_case(f, module, phase, events, psutil_module, client):
    config = f.guarded_config(udp_timeout="2s")
    f.start(client, domestic=config)
    overseas_pid = f.upstream.process.pid
    overseas_birth = psutil_module.Process(overseas_pid).create_time()
    previous_device_keys = set()
    for cycle in range(RESTARTS):
        watch = SocketWatch(f, psutil_module, events)
        baseline = watch.baseline() if cycle == 0 else watch.snapshot("prior_device_recovery_before_restart")
        require(active_keys(baseline) <= previous_device_keys, "Unrequested sockets appeared between restart rounds")
        with MuxProbe(f, module, phase, events) as probe:
            probe.send(0, ("lifecycle-before-restart-%d" % cycle).encode())
            probe.reply(0)
            live = watch.snapshot("before_explicit_restart")
            leg = paired_leg(baseline, live, f.upstream_port)
            verify_new_sockets(baseline, live, leg, ingress_port=f.ingress_port)
            added = active_keys(live) - previous_device_keys
            require(len(added) == 4 and len(active_keys(live)) <= 8,
                    "Raw plus previous official-device association socket budget exceeded")
            old = (f.domestic.process, f.client.process)
            begin = events.event("explicit_restart_begin", cycle=cycle, old_pids=[p.pid for p in old],
                                 application_socket_count=len(active_keys(live)), maximum_application_sockets=8)
            f.restart_domestic_and_device(client, config)
            require(all(p.poll() is not None and not psutil_module.pid_exists(p.pid) for p in old), "Old restarted child PID survived")
            require(f.upstream.process.pid == overseas_pid and psutil_module.Process(overseas_pid).create_time() == overseas_birth,
                    "Official overseas core changed during domestic/device restart")
            settled = wait_restart_release(f, psutil_module, events, leg, begin["monotonic"] + IDLE_SECONDS)
            events.event("restart_resources_released", cycle=cycle, old_pids_reaped=True,
                         remaining_application_socket_count=len(active_keys(settled)), live_core_processes=3)
        # Keep this real official-device association open until its live sockets
        # have been recorded. Closing its SOCKS control is EXPLICIT cleanup;
        # any remaining, already identified keys are drained by the next restart.
        def live_device():
            recovered_watch = SocketWatch(f, psutil_module, events)
            recovered = recovered_watch.snapshot("official_device_recovery_before_explicit_close")
            recovered_leg = paired_leg(settled, recovered, f.upstream_port)
            verify_new_sockets(settled, recovered, recovered_leg, ingress_port=f.ingress_port)
            require(len(active_keys(recovered)) == 4, "Official device recovery socket budget exceeded")
            events.event("restart_official_device_recovered", cycle=cycle, client=client, leg=recovered_leg,
                         application_socket_count=4, live_core_processes=3)
            return set(active_keys(recovered))
        previous_device_keys = device_recovery(f, phase, events, ("lifecycle-official-recovery-%d" % cycle).encode(),
                                               on_live=live_device)


def run_extra(case, fixture, module, phase, psutil_module, output):
    events = Events(output / "observations" / (case + ".json"), case)
    functions = {DOMAIN: domain_case, ACTIVE: active_case}
    if case in IDLES:
        exercise = lambda f: idle_case(f, module, phase, events, psutil_module, EXTRA_CLIENTS[case])
    elif case in RESTART_CASES:
        exercise = lambda f: restart_case(f, module, phase, events, psutil_module, EXTRA_CLIENTS[case])
    else:
        exercise = lambda f: functions[case](f, module, phase, events, psutil_module)
    selected_exercise = exercise
    def marked_exercise(f):
        try:
            selected_exercise(f)
        finally:
            events.event("fixture_cleanup_begin")
    observer.run_case(case, fixture, psutil_module, events, marked_exercise)
    events.data["lifecycle_contract_passed"] = phase == "lifecycle"
    events.data["original_suite_passed"] = False
    events.flush()
    return {key: value for key, value in events.data.items() if key != "events"}


def verify_case_ledgers(phase, output):
    require({p.stem for p in (output / "cases").glob("*.json")} == set(ALL_CASES), "Missing/unexpected case evidence")
    require({p.stem for p in (output / "delivery-ledgers").glob("*.json")} == set(ALL_CASES), "Missing/unexpected delivery ledgers")
    rows = {}
    for case in ALL_CASES:
        row = base.read_json(output / "cases" / (case + ".json"))
        require((output / "cases" / (case + ".log")).is_file(), "Missing case log")
        failed = ((phase == "baseline" and (case == base.DNS_METHODS[0] or any(case == m or case.startswith(m + "-")
                     for m in base.BASELINE_FAILURES))) or (phase != "baseline" and case == base.METHODS[12])
                  or (phase == "patched" and case == base.METHODS[13]))
        require(row["status"] == ("failed" if failed else "passed"), "Wrong preserved case status: " + case)
        rows[case] = row
        ledger = base.read_json(output / "delivery-ledgers" / (case + ".json"))
        require(not ledger["ordinary_dns"] and Counter(map(tuple, ledger["business_dns"])) ==
                Counter({tuple(item[:-1]): item[-1] for item in ledger["expected_dns"]}), "DNS lifetime audit failed")
        for target in ledger["udp_targets"]:
            require(target["closed"] and not target["errors"] and target["received"] == target["expected"], "UDP lifetime audit failed")
    if phase != "baseline":
        observations = Counter(item["kind"] for case in base.expected_cases("patched") for item in rows[case]["observations"])
        expected = {"echo": 38 if phase == "lifecycle" else 37, "rejection": 18, "raw-rejection": 9,
                    "guarded-retarget": 1, "naked-retarget": 1, "tcp-https-double-hop": 2,
                    "same-domain-same-port-rebinding": 1}
        if phase == "lifecycle":
            expected["idle-cleanup"] = 1
        require(observations == Counter(expected), "Original gate observation counts do not match executed paths")
        require(next(x for x in rows[base.METHODS[11]]["observations"] if x["kind"] == "guarded-retarget")["changes"] == 9,
                "Original retarget vector did not complete")


def verify_outputs(output):
    for path in output.rglob("*"):
        require(not path.is_symlink(), "Symlink artifact is forbidden")
        if path.is_file():
            require(str(path.relative_to(output)) in ARTIFACT_ALLOWLIST and path.suffix in (".json", ".log"), "Unexpected runtime artifact")
            require(clean(path.read_text()) == path.read_text(), "Unredacted fixture/packet material in artifact")
            if path.suffix == ".json":
                value = base.read_json(path)
                require(scrub(value) == value, "Unredacted nested JSON material in artifact")


def execute(args, report):
    env = {name: os.environ.get(name) for name in ("VUI_TEST_CORES", "VUI_TEST_MIHOMO")}
    require(all(env.values()), "Official peer/core environment is required")
    paths = {"sing-box": Path(env["VUI_TEST_CORES"]) / "sing-box", "mihomo": Path(env["VUI_TEST_MIHOMO"])}
    stock = {name: {"path": str(path.resolve(strict=True)), "sha256": base.sha256(path)} for name, path in paths.items()}
    require(all(row["sha256"] == base.STOCK_HASHES[name] for name, row in stock.items()), "Official binary hash mismatch")
    provenance = base.read_json(args.provenance)
    report.update(source_identity=base.source_identity(base.ROOT, provenance), provenance=provenance,
                  provenance_sha256=base.sha256(args.provenance), runner_sha256=base.sha256(Path(__file__)),
                  reused_observer_sha256=base.sha256(Path(observer.__file__)))
    candidate = verify_candidate(args.candidate, provenance, args.phase, stock)
    report.update(candidate=candidate, stock=stock)
    (args.output / "cases").mkdir()
    (args.output / "observations").mkdir()
    ledger = base.BinaryLedger(candidate, stock, args.output)
    problems = []
    with base.experiment_environment(args.output / "cases"), (args.output / "suite.log").open("w") as log:
        with redirect_stdout(log), redirect_stderr(log):
            module, support = base.runtime_modules()
            fixture = base.make_fixture(module.RestrictedUdpFixture, ledger)
            with patch.object(module, "RestrictedUdpFixture", fixture):
                report["groups"] = {
                    "original": run_suite(unittest.defaultTestLoader.loadTestsFromModule(module), log),
                    "tcp": run_suite(unittest.defaultTestLoader.loadTestsFromTestCase(base.make_tcp_tests(fixture, support)), log),
                    "dns_rebinding": run_suite(unittest.defaultTestLoader.loadTestsFromTestCase(base.make_dns_tests(fixture, module)), log),
                }
            module.RestrictedUdpPreflightTests.setUpClass()
            psutil_module = importlib.import_module("psutil")
            report["supplemental"] = {}
            for case in EXTRA_CASES:
                try:
                    report["supplemental"][case] = run_extra(case, fixture, module, args.phase, psutil_module, args.output)
                except Exception as exc:
                    problems.append({"case": case, "type": type(exc).__name__, "message": clean(str(exc))})
                    print(clean(traceback.format_exc()))
    # Every post-run identity/audit is attempted even if an original gate failed.
    def check(name, function):
        try:
            function()
        except Exception as exc:
            problems.append({"check": name, "type": type(exc).__name__, "message": clean(str(exc))})
    check("environment", lambda: require(all(os.environ.get(k) == v for k, v in env.items()), "Official environment was changed"))
    check("frozen_sources", lambda: base.source_identity(base.ROOT, provenance))
    for identity in [candidate, *stock.values()]:
        check("binary_identity", lambda item=identity: ledger.check(item["path"]))
    check("process_isolation", lambda: verify_isolation(args.phase, ledger.records, candidate, stock))
    check("case_lifetime", lambda: verify_case_ledgers(args.phase, args.output))
    try:
        report["original_matrix"] = assess_original(args.phase, report["groups"])
    except Exception as exc:
        problems.append({"check": "original_matrix", "type": type(exc).__name__, "message": clean(str(exc))})
    for path in args.output.rglob("*"):
        if path.is_file() and path.suffix == ".json":
            save(path, base.read_json(path))
        elif path.is_file() and path.suffix == ".log":
            path.write_text(clean(path.read_text()))
    check("output_allowlist", lambda: verify_outputs(args.output))
    report.update(errors=problems, process_records=len(ledger.records),
                  process_role_counts=dict(Counter(r["role"] for r in ledger.records)))
    require(not problems, "Three-way lifecycle evidence has failures or inconclusive observations")
    report.update(matrix_verified=True, lifecycle_contract_passed=args.phase == "lifecycle",
                  original_suite_passed=False, product_qualified=False,
                  outcome="lifecycle_diagnostic_contract_met" if args.phase == "lifecycle" else "negative_control_reproduced")


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--phase", choices=PHASES, required=True)
    result.add_argument("--candidate", type=Path, required=True)
    result.add_argument("--provenance", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        args.output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print("Refused existing lifecycle evidence output", file=sys.stderr)
        return 2
    report = {"schema_version": 2, "phase": args.phase, "matrix_verified": False,
              "lifecycle_contract_passed": False, "original_suite_passed": False,
              "product_qualified": False, "outcome": "not_qualified"}
    try:
        execute(args, report)
    except Exception as exc:
        report["blocker"] = clean(str(exc))
        (args.output / "runner-error.log").write_text(clean(traceback.format_exc()))
    finally:
        save(args.output / "result.json", report)
    print(json.dumps({key: report[key] for key in ("phase", "outcome", "matrix_verified", "original_suite_passed", "product_qualified")}))
    return 0 if report["matrix_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
