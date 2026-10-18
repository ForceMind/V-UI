#!/usr/bin/env python3
"""Linux-only isolated packet boundaries; independent of every old gate/assertion.

No core binaries are built, uploaded or published. Real execution requires this
script and its fixture dependencies committed at the supplied repository HEAD.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
import errno
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
SCHEMA = 1
KIND = "vui-packet-boundary-runtime"
PROTOCOLS = ("aes-128-gcm", "chacha20-poly1305", "vless")
SIZES = (0, 1, 8192, 32768, 60000, 65497)
STOCK_SHA = "07d2866a6c908281a56053f0e7d454918501592f2f258526f2ebe826dab58793"
FIXED = {"sing_box": "af6e64c3b69e6132ebaee0e1a3d24e93903f6709",
         "sing_vmess": "31ec11e8790c48a4bf11711806a61e929ee6b89d",
         "sing": "87c33f17688fe3dd80eeabef7c3f7a69152ccd42"}
ROLES = {"A": {"domestic": "candidate", "overseas": "candidate", "device-singbox": "official"},
         "B": {"domestic": "candidate", "overseas": "official", "device-singbox": "candidate"},
         "C": {"domestic": "candidate", "overseas": "candidate", "device-singbox": "candidate"},
         "D": {"domestic": "candidate", "overseas": "candidate", "device-singbox": "candidate"},
         "U": {"domestic": "candidate", "overseas": "candidate", "device-singbox": "official"},
         "V": {"domestic": "candidate", "overseas": "candidate", "device-singbox": "official"}}


def need(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ident(body):
    return {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def payload(size, seed):
    """Distinct counter blocks; do not mask offset corruption with a repeated block."""
    need(type(size) is int and 0 <= size <= 65535, "Bad payload size")
    prefix = seed.encode("ascii") + b"\0"
    return b"".join(hashlib.sha256(prefix + i.to_bytes(4, "big")).digest()
                    for i in range((size + 31) // 32))[:size]


def packet_bodies(leaf, spec):
    """One authoritative byte generator, including the independent short ACK."""
    request = payload(32 if spec["direction"] == "downlink" else spec["size"], leaf + "/request")
    if spec["direction"] == "uplink_ack":
        return request, payload(32, leaf + "/response")
    return request, (payload(spec["size"], leaf + "/response")
                     if spec["direction"] == "downlink" else request)


def socks_overhead(kind, domain_bytes=None):
    if kind == "ipv4":
        return 10
    if kind == "ipv6":
        return 22
    need(kind == "domain" and type(domain_bytes) is int and 1 <= domain_bytes <= 255,
         "Invalid SOCKS address budget")
    return 7 + domain_bytes


def packet(pid, size, *, direction="uplink", diagnostic=False, expected_prefix=None):
    return {"id": pid, "size": size, "direction": direction,
            "diagnostic": diagnostic, "expected_prefix": expected_prefix}


def plan(stage):
    need(stage in ROLES, "Unknown stage")
    cases = []
    for protocol in PROTOCOLS:
        tag = {"aes-128-gcm": "aes", "chacha20-poly1305": "chacha", "vless": "vless"}[protocol]
        def add(suffix, packets, *, mode="packets"):
            cases.append({"id": f"{stage}_{tag}_{suffix}", "stage": stage, "protocol": protocol,
                          "roles": dict(ROLES[stage]), "mode": mode, "packets": packets})
        if stage == "A":
            boundary = 16106 if protocol == "vless" else 15992
            add("warmed", [packet("warm", 1),
                *[packet(f"boundary_{n}", n, diagnostic=True,
                         expected_prefix=15992 if protocol != "vless" and n == 15993 else None)
                  for n in range(boundary - 1, boundary + 2)], packet("recover", 7)])
            for n in (boundary - 1, boundary, boundary + 1, 16373, 16374, 16375):
                add(f"first_{n}", [packet("first", n, diagnostic=True), packet("recover", 7)])
        elif stage == "U":
            for n in (16373, 16374, 16375):
                add(f"first_{n}", [packet("first", n, direction="uplink_ack", diagnostic=True),
                                  packet("recover", 7)])
        elif stage == "V":
            # 16122 is a source-budget prediction, not an accepted runtime limit.
            for mode in ("first", "warmed"):
                for n in (16121, 16122, 16123):
                    probes = [packet("warm", 1)] if mode == "warmed" else []
                    add(f"{mode}_{n}", probes + [packet("probe", n, direction="downlink", diagnostic=True),
                                                 packet("recover", 7)])
        elif stage == "B":
            add("response", [*[packet(f"response_{n}", n, direction="downlink", diagnostic=n > 8192)
                               for n in (8192, 32768, 60000)], packet("recover", 7)])
        elif stage == "C":
            for mode in ("first", "warmed"):
                for direction in ("uplink", "downlink"):
                    for n in SIZES:
                        probes = ([packet("warm", 1)] if mode == "warmed" else [])
                        probes += [packet("probe", n, direction=direction), packet("recover", 7)]
                        add(f"{mode}_{direction}_{n}", probes)
            add("full_short_zero_recovery", [packet("full", 65497), packet("short", 7),
                                              packet("empty", 0), packet("recover", 7)])
            add("local_emsgsize", [packet("oversize", 65498, direction="local_emsgsize"),
                                   packet("recover", 7)])
        elif stage == "D":
            # SOCKS idle/close needs no raw-parent inference; these are endpoint
            # smoke additions, not a replacement for the six frozen life probes.
            add("large_close", [packet("large", 60000), packet("recover", 32768)], mode="close")
            add("large_idle", [packet("large", 60000), packet("recover", 32768)], mode="idle")
    need(cases and len({x["id"] for x in cases}) == len(cases), "Empty or duplicate case plan")
    return cases


def leaf_ids(cases):
    return [f"{case['id']}/{p['id']}" for case in cases for p in case["packets"]]


def verify_leaf_set(expected, actual, label):
    need(expected and len(expected) == len(set(expected)), "Invalid fixed " + label + " plan")
    need(Counter(actual) == Counter(expected), "Missing, extra or duplicate " + label + " leaves")


def json_read(path):
    def pairs(items):
        result = {}
        for k, v in items:
            need(k not in result, "Duplicate JSON key: " + k)
            result[k] = v
        return result
    return json.loads(Path(path).read_text(), object_pairs_hook=pairs)


def save(path, value):
    text = json.dumps(value, sort_keys=True, indent=2)
    need("PRIVATE KEY-----" not in text and "BEGIN CERTIFICATE" not in text, "Sensitive material in evidence")
    Path(path).write_text(text + "\n")


def digest(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def load_reuse(repository):
    path = repository / "experiments/xudp_mux/runtime"
    sys.path[:0] = [str(path), str(repository / "tests"), str(repository)]
    spec = importlib.util.spec_from_file_location("boundary_common", path / "common.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_identity(repo, c):
    """Byte-for-byte identity contract shared with build_and_test.identity()."""
    need(c.ROOT.resolve() == repo.resolve() and HERE == repo / "experiments/xudp_packet",
         "Packet runtime is not in the reviewed repository directory")
    result = c.source_identity()
    for path in sorted(set(HERE.rglob("*")) | set((repo/"experiments/xudp_mux").glob("*"))):
        if path.is_file() and path.suffix in (".py", ".go", ".patch", ".md", ".json"):
            name = str(path.relative_to(repo))
            need(not path.is_symlink() and c.git("show", "HEAD:" + name) == path.read_bytes(),
                 "Uncommitted packet source: " + name)
            result["files"][name] = sha(path)
    return result



def verify_build_record(provenance):
    names = {package + "-" + mode for package in ("common-network", "common-bufio", "protocol-socks")
             for mode in ("race", "debug", "darwin-compile", "windows-compile")} | {"build", "module-verify"} | {kind+"-"+mode for kind in ("mux-unit","mux-encrypted","mux-encrypted-cancel") for mode in ("race","debug")} | {"mux-route-race"}
    checks = provenance.get("checks", [])
    need(Counter(row.get("label") for row in checks) == Counter(names) and
         all(type(row.get("returncode")) is int and row["returncode"] == 0 for row in checks),
         "Runtime refused incomplete, duplicate or failed packet build checks")

def validate_manifest(data, identity, repo, *, execute=True):
    need(set(data) == {"schema_version", "kind", "source_identity", "official", "candidate"}, "Wrong input manifest fields")
    need(data["schema_version"] == 1 and type(data["schema_version"]) is int and
         data["kind"] == KIND + "-input" and data["source_identity"] == identity, "Input identity mismatch")
    stock, candidate = data["official"], data["candidate"]
    need(set(stock) == {"path", "sha256", "version"} and stock["sha256"] == STOCK_SHA and
         stock["version"] == "1.14.2", "Official binary manifest changed")
    need(set(candidate) == {"path", "sha256", "version", "provenance_path", "provenance_sha256"}, "Candidate manifest fields")
    need(digest(candidate["sha256"]) and candidate["sha256"] != STOCK_SHA, "Candidate is official or missing digest")
    need("experiment" in candidate["version"] and identity["vui_commit"][:12] in candidate["version"],
         "Candidate needs an exact-commit experimental version")
    provenance_path = Path(candidate["provenance_path"])
    need(not provenance_path.is_symlink() and sha(provenance_path) == candidate["provenance_sha256"], "Build provenance digest mismatch")
    provenance = json_read(provenance_path)
    need(provenance.get("schema_version") == 1 and type(provenance.get("schema_version")) is int and
         provenance.get("kind") == "vui-complete-packet-source-experiment", "Wrong packet build provenance kind/schema")
    verify_build_record(provenance)
    need(provenance.get("source_identity") == identity and provenance.get("product_qualified") is False,
         "Build source identity or qualification mismatch")
    need(provenance.get("fixed_sources") == FIXED and provenance.get("binary_sha256") == candidate["sha256"] and
         provenance.get("version") == candidate["version"], "Build binary/fixed-source identity mismatch")
    patches = provenance.get("patches")
    need(isinstance(patches, list) and len(patches) == 5, "Missing full ordered mux plus packet patch chain")
    expected_prefix = ["experiments/xudp_address/remoteaddr.patch", "experiments/xudp_address/lifecycle.patch",
                       "experiments/xudp_mux/mux.patch", "experiments/xudp_mux/route_cancel.patch"]
    need([x.get("path") for x in patches] == expected_prefix + ["experiments/xudp_packet/receive.patch"], "Frozen/packet patch order changed")
    need(len({x.get("path") for x in patches}) == len(patches), "Duplicate patch identity")
    for row in patches:
        name = row.get("path")
        need(isinstance(name, str) and not Path(name).is_absolute() and ".." not in Path(name).parts,
             "Patch escaped repository")
        need(set(row) == {"path", "sha256"} and digest(row["sha256"]), "Malformed patch identity")
        path = repo / name
        need(not path.is_symlink() and sha(path) == row["sha256"], "Patch bytes differ from build")
        need(subprocess.check_output(["git", "show", "HEAD:" + name], cwd=repo, timeout=30) == path.read_bytes(),
             "Patch is not committed at HEAD")
    build = provenance.get("build", {})
    expected = {"go_version": "go1.25.14", "goos": "linux", "goarch": "amd64", "tags": [],
                "cgo_enabled": "0", "gotoolchain": "local", "gowork": "off", "trimpath": True, "buildvcs": False}
    need(all(build.get(k) == v for k, v in expected.items()) and digest(build.get("go_binary_sha256")),
         "Build flags/toolchain identity mismatch")
    need(digest(provenance.get("module_graph_sha256")) and isinstance(provenance.get("source_files"), dict)
         and provenance["source_files"], "Missing graph/source byte identities")
    for row in provenance["source_files"].values():
        need(set(row) == {"before", "after"} and (row["before"] is None or digest(row["before"])) and digest(row["after"]),
             "Malformed patched source hash pair")
    for row in (stock, candidate):
        path = Path(row["path"])
        need(path.is_file() and not path.is_symlink() and os.access(path, os.X_OK), "Invalid executable")
        need(sha(path) == row["sha256"], "Actual binary hash mismatch")
        if execute:
            result = subprocess.run([str(path), "version"], capture_output=True, text=True, timeout=15)
            need(result.returncode == 0 and (result.stdout + result.stderr).splitlines()[0] == "sing-box version " + row["version"],
                 "Actual binary version mismatch")
    need(not Path(stock["path"]).samefile(candidate["path"]), "Official and candidate alias")
    return provenance


class Target:
    """Independent complete recvmsg recorder; each announced packet is single-shot.

    For diagnostic stages audit enforces source, order, count and prefix integrity;
    full delivery is an independently retained RED field, never rewritten PASS.
    """
    def __init__(self, host, peer, diagnostic, *, correlate=False):
        self.host, self.peer, self.diagnostic = host, peer, diagnostic
        self.correlate, self.closed_windows = correlate, set()
        self.closed, self.received, self.expected, self.errors = False, [], Counter(), []
        self.plans, self.rows, self.receive_events = [], [], []
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.stream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.stream.bind((host, 0))
        self.port = self.stream.getsockname()[1]
        self.stream.settimeout(.05)
        self.thread = threading.Thread(target=self.run, name="packet-boundary-target", daemon=False)
        self.thread.start()

    def announce(self, leaf, request, reply):
        with self.lock:
            if not self.correlate:
                need(len(self.plans) == len(self.rows), "More than one target packet outstanding")
            need(leaf not in {p["leaf"] for p in self.plans}, "Duplicate target announcement")
            self.plans.append({"leaf": leaf, "request": request, "reply": reply})
            self.expected[(self.peer, request)] += 1

    def run(self):
        while not self.stop.is_set():
            try:
                body, ancillary, flags, peer = self.stream.recvmsg(65535)
            except socket.timeout:
                continue
            except OSError as exc:
                if not self.stop.is_set():
                    self.errors.append(type(exc).__name__ + ": " + str(exc))
                return
            try:
                with self.lock:
                    self.receive_events.append({"target": [self.host, self.port], "source": list(peer),
                        "payload": ident(body), "flags": flags, "ancillary_count": len(ancillary)})
                need(not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) and not ancillary,
                     "Independent target recorder truncated data/control")
                with self.lock:
                    self.received.append((peer[0], body))
                    if getattr(self, "correlate", False):
                        matches = [p for p in self.plans if body and len(body) <= len(p["request"])
                                   and body == p["request"][:len(body)]]
                        need(len(matches) == 1, "Unmatched/ambiguous target datagram")
                        planned = matches[0]
                        need(planned["leaf"] not in self.closed_windows,
                             "Late/duplicate datagram after the leaf observation window")
                        need(not any(r["leaf"] == planned["leaf"] for r in self.rows),
                             "Duplicate target datagram for leaf")
                    else:
                        index = len(self.rows)
                        need(index < len(self.plans), "Unexpected/duplicate target datagram")
                        planned = self.plans[index]
                need(peer[0] == self.peer and peer[1] > 0, "Wrong target source tuple/direct bypass")
                need(body == planned["request"][:len(body)] and len(body) <= len(planned["request"]),
                     "Target payload is neither complete nor exact original prefix")
                reply = body if planned["reply"] is None else planned["reply"]
                sent = self.stream.sendto(reply, peer)
                need(sent == len(reply), "Incomplete independent target UDP send")
                with self.lock:
                    self.rows.append({"leaf": planned["leaf"], "target": [self.host, self.port],
                        "source": list(peer), "request_expected": ident(planned["request"]),
                        "request_actual": ident(body), "request_full": body == planned["request"],
                        "request_exact_prefix": True, "reply_sent": ident(reply), "sendto_return": sent,
                        "reply_body": reply})
            except BaseException as exc:
                with self.lock:
                    self.errors.append(type(exc).__name__ + ": " + str(exc))

    def finish_window(self, leaf):
        with self.lock:
            self.closed_windows.add(leaf)
            return [r for r in self.rows if r["leaf"] == leaf]

    def observed_rows(self, leaf):
        with self.lock:
            return [r for r in self.rows if r["leaf"] == leaf]

    def receive_count(self):
        with self.lock:
            return len(self.receive_events)

    def wait_row(self, leaf, timeout=4):
        deadline = time.monotonic() + timeout
        while True:
            self.audit()
            with self.lock:
                rows = [r for r in self.rows if r["leaf"] == leaf]
            need(len(rows) <= 1, "Duplicate target leaf")
            if rows:
                return rows[0]
            if time.monotonic() >= deadline:
                raise TimeoutError("No target delivery within bounded single-shot window")
            time.sleep(.005)

    def audit(self):
        with self.lock:
            need(not self.errors, "Target recorder errors: " + "; ".join(self.errors))
            need(len(self.rows) <= len(self.plans) and len(self.received) <= len(self.plans), "Target cardinality exceeded plan")
            if not self.diagnostic:
                need(all(row["request_full"] for row in self.rows), "Full target uplink delivery failed")

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop.set()
        # Let the bounded recv finish before close: no send racing EBADF.
        self.thread.join(timeout=1)
        self.stream.close()
        self.thread.join(timeout=1)
        need(not self.thread.is_alive() and self.stream.fileno() == -1, "Target worker/socket survived close")

    def evidence(self):
        self.audit()
        with self.lock:
            return {"host": self.host, "port": self.port, "closed": self.closed,
                    "thread_joined": not self.thread.is_alive(), "socket_closed": self.stream.fileno() == -1,
                    "planned_count": len(self.plans), "received_count": len(self.received),
                    "errors": list(self.errors), "actual_datagrams": list(self.receive_events),
                    "packets": [{k: v for k, v in row.items() if k != "reply_body"} for row in self.rows]}


def role_fixture(c, module, output, binaries, case, process_records):
    """Use the frozen fixture unchanged; override binary selection only."""
    class Fixture(module.RestrictedUdpFixture):
        def process(self, label, config, *, mihomo=False, env=None):
            need(not mihomo and label in case["roles"], "Unexpected process role")
            origin = case["roles"][label]
            selected = binaries[origin]
            previous = self.singbox
            row = {"case": self.label, "role": label, "origin": origin, "binary": selected["path"],
                   "sha256": selected["sha256"], "version": selected["version"],
                   "classification": "official" if origin == "official" else "experimental",
                   "config_sha256": hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                   "inbound_types": [x.get("type") for x in config.get("inbounds", [])],
                   "outbound_types": [x.get("type") for x in config.get("outbounds", [])],
                   "parser_passed": False, "launches": []}
            process_records.append(row)
            save(output / "process-binaries.json", process_records)
            self.singbox = selected["path"]
            try:
                core = super().process(label, config, mihomo=False, env=env)
            finally:
                self.singbox = previous
            need(Path(core.command[0]).resolve() == Path(selected["path"]).resolve(), "Wrong role command")
            row["parser_passed"] = True
            original = core.start
            def start(*args, **kwargs):
                need(sha(core.command[0]) == selected["sha256"], "Binary changed before launch")
                launch = {"ready": False, "sha256": selected["sha256"]}
                row["launches"].append(launch)
                try:
                    result = original(*args, **kwargs)
                    launch["ready"] = True
                    launch["birth_time"] = importlib.import_module("psutil").Process(core.process.pid).create_time()
                    return result
                finally:
                    launch["pid"] = core.process.pid if core.process else None
                    save(output / "process-binaries.json", process_records)
            core.start = start
            return core
    return Fixture


def configs(f, protocol):
    domestic, client = f.guarded_config(udp_timeout="2s"), f.client_config("singbox")
    if protocol != "vless":
        domestic["inbounds"][0]["type"] = "vmess"
        domestic["inbounds"][0]["users"][0].pop("flow")
        node = client["outbounds"][0]
        node["type"] = "vmess"
        node.pop("flow")
        node.update(security=protocol, alter_id=0, global_padding=True, authenticated_length=True)
    need(client["outbounds"][0]["packet_encoding"] == "xudp" and
         "multiplex" not in client["outbounds"][0] and
         "insecure" not in client["outbounds"][0]["tls"], "Wrong protocol/configuration path")
    return domestic, client


def _exchange(association, target, case, p, helper, rows=None, persist=None):
    leaf = case["id"] + "/" + p["id"]
    request, expected = packet_bodies(leaf, p)
    row = {"leaf": leaf, "direction": p["direction"], "first_udp": not association.sent,
           "application_socket": list(association.stream.getsockname()), "socks_relay": list(association.relay),
           "requested_target": [target.host, target.port], "request": ident(request), "expected_reply": ident(expected),
           "status": "running", "full_delivery_passed": False,
           "send_state": "not_attempted", "target_window": {"status": "not_started"},
           "reply_window": {"status": "not_started"}}
    if rows is not None:
        rows.append(row)
    if persist is not None:
        save(persist, rows)
    before = len(target.received)
    if p["direction"] == "local_emsgsize":
        need(len(request) + socks_overhead("ipv4") == 65508, "Wrong local oversize vector")
        try:
            row["send_state"] = "attempting"
            association.send(target.host, target.port, request)
            row["send_state"] = "sent"
        except OSError as exc:
            row["send_state"] = "send_error"
            need(exc.errno == errno.EMSGSIZE, "Local send failed for a reason other than EMSGSIZE")
            row.update(errno=exc.errno, rejection_location="local_ipv4_udp_socket", socks_wire_bytes=65508)
        else:
            raise ValueError("Oversized IPv4 UDP send unexpectedly succeeded")
        until = time.monotonic() + .3
        while time.monotonic() < until:
            target.audit()
            need(len(target.received) == before, "Locally rejected packet reached target")
            association.stream.settimeout(.02)
            try:
                association.stream.recvfrom(65535)
            except socket.timeout:
                pass
            else:
                raise ValueError("Locally rejected packet returned unexpected data")
        row.update(status="local_emsgsize_verified", local_zero_delivery=True)
        return row
    target.announce(leaf, request, expected if p["direction"] in ("downlink", "uplink_ack") else None)
    row["send_state"] = "attempting"
    association.send(target.host, target.port, request)  # exactly once
    row["send_state"] = "sent"
    started = time.monotonic()
    row["target_window"] = {"status": "observing", "started": started, "deadline": started + 4}
    actual_target = target.wait_row(leaf)
    row["target_window"].update(status="received", completed=time.monotonic())
    row["target"] = {k: v for k, v in actual_target.items() if k != "reply_body"}
    association.stream.settimeout(4)
    started = time.monotonic()
    row["reply_window"] = {"status": "observing", "started": started, "deadline": started + 4}
    response, ancillary, flags, relay = association.stream.recvmsg(65535)
    row["reply_window"].update(status="received", completed=time.monotonic())
    row["reply_monotonic"] = time.monotonic()
    need(not ancillary and not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC), "Application recorder truncated reply")
    need(relay == association.relay, "Reply came from a different SOCKS relay")
    row["socks_reply_wire"] = ident(response)
    host, port, received = helper.decode_socks_datagram(response)
    need((host, port) == (target.host, target.port), "Reply target host/port changed")
    need(received == expected[:len(received)] and len(received) <= len(expected), "Reply changed payload or is not exact expected prefix")
    row.update(actual_reply=ident(received), reply_source=list(relay), reply_target=[host, port],
               reply_exact_prefix=True, full_delivery_passed=actual_target["request_full"] and received == expected)
    if case["stage"] == "A":
        need(received == actual_target["reply_body"], "Patched return path changed the actual stock-device prefix")
    if case["stage"] == "B":
        need(actual_target["request_full"], "Stock overseas downlink probe did not receive its complete small request")
    if p["expected_prefix"] is not None:
        need(actual_target["request_actual"]["bytes"] == p["expected_prefix"] and not row["full_delivery_passed"],
             "Known warmed official-device 15992-byte RED identity changed")
    if p["diagnostic"]:
        row["status"] = "full_delivery_passed" if row["full_delivery_passed"] else "capacity_failed_exact_prefix"
    else:
        need(row["full_delivery_passed"], "Strict complete target/reply payload gate failed")
        row["status"] = "full_delivery_passed"
    return row


def terminal_error(row, exc):
    """End a started leaf without manufacturing a target or reply observation."""
    now = time.monotonic()
    row.update(status="error", full_delivery_passed=False,
               error={"type": type(exc).__name__, "message": str(exc)}, terminal_monotonic=now)
    if row.get("send_state") == "attempting":
        row["send_state"] = "send_error"
    for key in ("target_window", "reply_window"):
        window = row.get(key, {})
        if window.get("status") == "observing":
            bounded_timeout = isinstance(exc, TimeoutError) and now >= window["deadline"]
            window.update(status=("zero_observed" if key == "target_window" else "timeout")
                          if bounded_timeout else "observation_error", completed=now)
    return row


def exchange(association, target, case, p, helper, rows=None, persist=None):
    # B/C/D keep fail-fast packet semantics. Only recording a failed terminal is
    # shared; this wrapper does not retry, proceed to recovery, or waive a gate.
    current = rows if rows is not None else []
    before = len(current)
    try:
        return _exchange(association, target, case, p, helper, current, persist)
    except Exception as exc:
        if len(current) > before:
            terminal_error(current[-1], exc)
            if persist is not None:
                save(persist, current)
        raise


def device_log_start(f, c, psutil):
    core = f.client
    text = c.life.clean(f.safe(core.log_path.read_text(errors="replace")))
    return {"role": "device-singbox", "file": core.log_path.name,
            "pid": core.process.pid, "birth_time": psutil.Process(core.process.pid).create_time(),
            "start_offset": len(text), "prefix_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "starts_at_line_boundary": not text or text.endswith("\n")}


def device_log_finish(row, f, c, psutil):
    record = row["device_log"]
    text = c.life.clean(f.safe(f.client.log_path.read_text(errors="replace")))
    start = record["start_offset"]
    need(len(text) >= start and hashlib.sha256(text[:start].encode()).hexdigest() == record["prefix_sha256"],
         "Device log prefix changed during packet observation")
    interval = text[start:]
    complete_lines = interval if record["starts_at_line_boundary"] else interval.partition("\n")[2]
    # Ignore an incomplete trailing line; a bare timeout or an old line is not
    # an explicit device rejection. The complete original log is also retained.
    complete_lines = complete_lines.rsplit("\n", 1)[0] if "\n" in complete_lines else ""
    birth = psutil.Process(f.client.process.pid).create_time()
    need(f.client.process.pid == record["pid"] and birth == record["birth_time"],
         "Device PID/birth changed during packet observation")
    record.update(end_offset=len(text), text=interval,
                  interval_sha256=hashlib.sha256(interval.encode()).hexdigest(), birth_time_after=birth,
                  fresh_short_buffer_lines=[line for line in complete_lines.splitlines()
                                            if "packet download closed: short buffer" in line])


def observed_exchange(association, target, case, p, helper, rows, persist, f, c, psutil):
    """A/U/V only: single send, real bounded observations, then same-association continuation.

    Missing target or reply data remain errors. A short ACK is confined to U;
    A still sends the original echo and V still sends its full requested reply.
    """
    need(case["stage"] in ("A", "U", "V"), "Observed continuation escaped its explicit stage scope")
    leaf = case["id"] + "/" + p["id"]
    request, expected = packet_bodies(leaf, p)
    row = {"leaf": leaf, "direction": p["direction"], "first_udp": not association.sent,
           "application_socket": list(association.stream.getsockname()), "socks_relay": list(association.relay),
           "requested_target": [target.host, target.port], "request": ident(request), "expected_reply": ident(expected),
           "status": "running", "full_delivery_passed": False, "send_state": "not_attempted",
           "target_window": {"status": "not_started"}, "reply_window": {"status": "not_started"}}
    rows.append(row)
    announced = False
    try:
        row["device_log"] = device_log_start(f, c, psutil)
        target_count_before = target.receive_count()
        target.announce(leaf, request, expected if p["direction"] in ("uplink_ack", "downlink") else None)
        announced = True
        row.update(send_state="attempting", send_started=time.monotonic())
        save(persist, rows)
        association.send(target.host, target.port, request)  # Exactly once, including recovery.
        started = time.monotonic()
        row.update(send_state="sent", send_completed=started)
        deadline = started + 4
        row["target_window"] = {"status": "observing", "started": started, "deadline": deadline}
        # Preserve A's original target window followed by its separate reply
        # window. A missing target now also gets a real application observation;
        # this never shortens the former four seconds available for a reply.
        while not target.observed_rows(leaf) and time.monotonic() < deadline:
            time.sleep(min(.005, max(0, deadline - time.monotonic())))
        actual_rows = target.finish_window(leaf)
        target_finished = time.monotonic()
        actual_target = actual_rows[0] if len(actual_rows) == 1 else None
        need(len(actual_rows) <= 1, "Duplicate target leaf")
        actual_count = target.receive_count() - target_count_before
        row["target_window"].update(completed=target_finished, observed_count=len(actual_rows),
                                    actual_datagram_count=actual_count,
                                    status="received" if actual_target else
                                    ("zero_observed" if actual_count == 0 else "unaccounted_datagrams"))
        if actual_target is not None:
            row["target"] = {k: v for k, v in actual_target.items() if k != "reply_body"}
        else:
            need(target_finished >= deadline, "Target zero claim before complete observation window")
        reply_started = time.monotonic()
        reply_deadline = reply_started + 4
        row["reply_window"] = {"status": "observing", "started": reply_started, "deadline": reply_deadline}
        response = receive_error = None
        while time.monotonic() < reply_deadline:
            remaining = reply_deadline - time.monotonic()
            if remaining <= 0:
                break
            association.stream.settimeout(min(.025, remaining))
            try:
                response = association.stream.recvmsg(65535)
                row["reply_monotonic"] = time.monotonic()
                break
            except socket.timeout:
                pass
            except OSError as exc:
                receive_error = exc
                break
        finished = time.monotonic()
        row["reply_window"].update(completed=finished, observed_count=0 if response is None else 1,
                                   status="received" if response is not None else
                                   ("receive_error" if receive_error is not None else "timeout"))
        if receive_error is not None:
            row["reply_window"]["error"] = {"type": type(receive_error).__name__, "message": str(receive_error)}
        if response is not None:
            wire, ancillary, flags, relay = response
            row["reply_receive"] = {"wire": ident(wire), "source": list(relay),
                                    "flags": flags, "ancillary_count": len(ancillary)}
        if response is None and receive_error is None:
            need(finished >= reply_deadline, "Reply timeout before complete observation window")
        device_log_finish(row, f, c, psutil)
        if receive_error is not None:
            row["failure_kind"] = "application_receive_error"
            raise receive_error
        if response is None:
            explicit = actual_target is not None and row["device_log"]["fresh_short_buffer_lines"]
            row["failure_kind"] = ("explicit_device_short_buffer" if explicit else
                                   "target_received_reply_timeout" if actual_target else
                                   "target_zero_reply_timeout" if actual_count == 0 else "target_recorder_error_reply_timeout")
            raise TimeoutError("No application reply in complete four-second single-send window")
        if actual_target is None:
            row["failure_kind"] = "target_zero_unaccounted_reply"
            raise ValueError("Application reply has no matching target delivery/send")
        wire, ancillary, flags, relay = response
        need(not ancillary and not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC), "Application recorder truncated reply")
        need(relay == association.relay, "Reply came from a different SOCKS relay")
        host, port, received = helper.decode_socks_datagram(wire)
        need((host, port) == (target.host, target.port), "Reply target host/port changed")
        need(received == expected[:len(received)] and len(received) <= len(expected),
             "Reply changed payload or is not exact expected prefix")
        row.update(socks_reply_wire=ident(wire), actual_reply=ident(received), reply_source=list(relay),
                   reply_target=[host, port], reply_exact_prefix=True,
                   full_delivery_passed=actual_target["request_full"] and received == expected)
        row["status"] = "full_delivery_passed" if row["full_delivery_passed"] else "capacity_failed_exact_prefix"
        verify_packet(case, p, row, row["target"], row["first_udp"])
        target.audit()
        row["terminal_monotonic"] = time.monotonic()
    except Exception as exc:
        terminal_error(row, exc)
    finally:
        if announced:
            target.finish_window(leaf)
        if "device_log" in row and "end_offset" not in row["device_log"]:
            try:
                device_log_finish(row, f, c, psutil)
            except Exception as exc:
                row["device_log_error"] = {"type": type(exc).__name__, "message": str(exc)}
                terminal_error(row, exc)
        save(persist, rows)
    return row


def lifecycle_exercise(f, target, case, helper, c, psutil, rows, persist):
    events = c.life.Events(persist.parent / "lifecycle-observation.json", case["id"])
    association = None
    try:
        watch = c.life.SocketWatch(f, psutil, events)
        before = watch.baseline()
        baseline_index = watch.samples.index(before)
        association = helper.SocksDatagrams(f.client_port)
        exchange(association, target, case, case["packets"][0], helper, rows, persist)
        save(persist, rows)
        live = watch.snapshot("after_large_business")
        leg = c.life.paired_leg(before, live, f.upstream_port)
        c.life.verify_new_sockets(before, live, leg, ingress_port=f.ingress_port)
        last = rows[0]["reply_monotonic"]
        if case["mode"] == "close":
            association.close()
        deadline, gone = last + 6, None
        while time.monotonic() < deadline:
            sample = watch.snapshot("large_then_" + case["mode"])
            if not c.life.leg_present(sample, leg):
                if gone is None:
                    need(sample["sample_completed_monotonic"] <= deadline, "Closure crossing sample is inconclusive")
                    gone = sample
            elif gone is not None:
                raise ValueError("Released association inode reappeared before idle/close window ended")
            time.sleep(.03)
        final = watch.snapshot("large_then_" + case["mode"] + "_window_complete")
        need(gone is not None and not c.life.leg_present(final, leg), "Exact large-packet association sockets survived/reappeared within six seconds")
        # No raw session End is visible on a SOCKS interface. Report only the
        # endpoint socket/packet smoke, keeping the old raw-End suite separate.
        watch.events.event("large_endpoint_resources_released", leg=leg, deadline=deadline,
                           raw_session_end_observed=False, scope="additional_endpoint_smoke")
        if case["mode"] == "close":
            association = helper.SocksDatagrams(f.client_port)
        exchange(association, target, case, case["packets"][1], helper, rows, persist)
        save(persist, rows)
        proof = {"mode": case["mode"], "baseline": before, "live": live, "leg": leg,
                 "last_business_reply": last, "deadline": deadline, "first_absence": gone, "ingress_port": f.ingress_port,
                 "final": final, "samples": watch.samples[baseline_index:],
                 "prebaseline_samples": watch.samples[:baseline_index],
                 "recovery_leaf": rows[-1]["leaf"], "recovery_reply_monotonic": rows[-1]["reply_monotonic"]}
        watch.events.event("large_endpoint_proof", proof=proof)
        watch.events.data["status"] = "observed"
        watch.events.flush()
    except Exception as exc:
        events.data["status"] = "error"
        events.data["error"] = {"type": type(exc).__name__, "message": c.life.clean(str(exc))}
        events.flush()
        raise
    finally:
        if association is not None:
            try:
                association.close()
                need(association.stream.fileno() == -1, "SOCKS application socket survived cleanup")
            except Exception as exc:
                events.data["status"] = "error"
                events.data["cleanup_error"] = {"type": type(exc).__name__, "message": c.life.clean(str(exc))}
                events.data.setdefault("error", events.data["cleanup_error"])
                events.flush()
                raise
    return {"proof": proof, "observation_sha256": sha(persist.parent / "lifecycle-observation.json")}


def cleanup(f, before_threads, psutil):
    processes = []
    for core in f.processes:
        need(core.process is not None and core.process.poll() is not None, "Core never started or survived cleanup")
        need(not psutil.pid_exists(core.process.pid), "Owned PID survived stop/wait")
        processes.append({"pid": core.process.pid, "returncode": core.process.returncode, "reaped": True})
    for target in f.targets:
        target.audit()
        need(target.closed and not target.thread.is_alive(), "Target worker survived cleanup")
        if isinstance(target, Target):
            need(target.stream.fileno() == -1 and len(target.rows) == len(target.received), "Target socket or row survived/missing")
        else:
            need(Counter(target.received) == target.expected, "Frozen target lifetime ledger changed")
    need(not f.root.exists(), "Temporary fixture material survived cleanup")
    need(not [t for t in threading.enumerate() if t not in before_threads], "Unjoined new fixture worker")
    need(f.answers is not None and f.answers.pending() == 0 and not f.ordinary_answers.queries, "DNS missing/active or fallback used")
    return {"processes": processes, "threads_joined": True, "fixture_removed": True,
            "all_target_workers_joined": True, "application_sockets_closed": True}


def run_case(case, c, module, output, binaries, process_records, psutil):
    directory = output / case["id"]
    directory.mkdir()
    fixture = role_fixture(c, module, output, binaries, case, process_records)
    helper = importlib.import_module("udp_dns_capability_helpers")
    f, target = None, None
    before_threads = set(threading.enumerate())
    row = {"case": case["id"], "stage": case["stage"], "protocol": case["protocol"],
           "roles": case["roles"], "status": "running", "packets": [], "errors": [],
           "original_suite_passed": False, "product_qualified": False}
    try:
        with fixture(unittest.TestCase(), case["id"]) as f:
            target = Target(module.TARGET, module.UPSTREAM_EGRESS, case["stage"] in ("A", "B", "U", "V"),
                            correlate=case["stage"] in ("A", "U", "V"))
            f.targets.append(target)
            f.target = target
            need(target.port != f.upstream_port, "Target/upstream port collision")
            domestic, client = configs(f, case["protocol"])
            f.start("singbox", domestic=domestic, client=client)
            row["process_identity"] = [{"pid": core.process.pid,
                "birth_time": psutil.Process(core.process.pid).create_time()} for core in f.processes]
            if case["mode"] in ("close", "idle"):
                row["lifecycle"] = lifecycle_exercise(f, target, case, helper, c, psutil, row["packets"], directory / "packets.json")
            else:
                association = helper.SocksDatagrams(f.client_port)
                try:
                    for p in case["packets"]:
                        if case["stage"] in ("A", "U", "V"):
                            observed_exchange(association, target, case, p, helper, row["packets"],
                                              directory / "packets.json", f, c, psutil)
                            if row["packets"][-1]["status"] == "error":
                                row["errors"].append({"leaf": row["packets"][-1]["leaf"],
                                                      **row["packets"][-1]["error"]})
                        else:
                            exchange(association, target, case, p, helper, row["packets"], directory / "packets.json")
                        save(directory / "packets.json", row["packets"])
                finally:
                    association.close()
                    need(association.stream.fileno() == -1, "SOCKS application socket survived cleanup")
            f.audit()
            f.observations.append({"kind": "separate-packet-boundary", "stage": case["stage"],
                "full_delivery_passed": all(p["full_delivery_passed"] for p in row["packets"] if p["direction"] != "local_emsgsize"),
                "original_gate_invoked": False, "product_qualified": False})
            need(not row["errors"], "One or more actually attempted packet leaves failed; see packet terminals")
    except Exception as exc:
        row["errors"].append({"type": type(exc).__name__, "message": c.life.clean(str(exc))})
        (directory / "error.log").write_text(c.life.clean(traceback.format_exc()))
    finally:
        if f is not None:
            try:
                row["cleanup"] = cleanup(f, before_threads, psutil)
            except Exception as exc:
                row["errors"].append({"check": "cleanup", "type": type(exc).__name__, "message": c.life.clean(str(exc))})
            if target is not None:
                # Preserve failing raw ledger even when Target.audit fails.
                row["target_ledger"] = {"host": target.host, "port": target.port, "closed": target.closed,
                    "thread_joined": not target.thread.is_alive(), "socket_closed": target.stream.fileno() == -1,
                    "planned_count": len(target.plans), "received_count": len(target.received), "errors": target.errors,
                    "actual_datagrams": target.receive_events,
                    "packets": [{k: v for k, v in item.items() if k != "reply_body"} for item in target.rows]}
            row["frozen_target_ledgers"] = [{"host": t.host, "port": t.port, "closed": t.closed,
                "thread_joined": not t.thread.is_alive(), "socket_closed": t.server.socket.fileno() == -1,
                "received": [{"peer": peer, **ident(body)} for peer, body in t.received],
                "expected": [{"peer": peer, **ident(body), "count": count} for (peer, body), count in t.expected.items()],
                "errors": t.errors} for t in f.targets if not isinstance(t, Target)]
            row["dns"] = {"business": f.answers.snapshot() if f.answers else [],
                          "expected": [[*key, n] for key, n in f.expected_dns.items()],
                          "ordinary": f.ordinary_answers.queries if f.ordinary_answers else []}
        save(directory / "packets.json", row["packets"])
        started_leaves = [p["leaf"] for p in row["packets"]]
        expected_leaves = [case["id"] + "/" + p["id"] for p in case["packets"]]
        row["unexecuted_leaves"] = [leaf for leaf in expected_leaves if leaf not in started_leaves]
        row["status"] = "error" if row["errors"] else "observed"
        save(directory / "result.json", row)
    return row



def verify_packet(case, spec, row, ledger_row, first):
    leaf = case["id"] + "/" + spec["id"]
    request, reply = packet_bodies(leaf, spec)
    need(row["leaf"] == leaf and row["direction"] == spec["direction"] and row["first_udp"] is first,
         "Packet plan identity/direction/first-packet state differs")
    need(row["request"] == ident(request) and row["expected_reply"] == ident(reply), "Expected payload identity changed")
    def tuple_ok(value, host):
        return isinstance(value, list) and len(value) == 2 and value[0] == host and type(value[1]) is int and 0 < value[1] <= 65535
    need(tuple_ok(row["application_socket"], "127.0.0.1") and tuple_ok(row["socks_relay"], "127.0.0.1") and
         tuple_ok(row["requested_target"], "127.0.0.3"), "Missing complete application/relay/target tuple")
    if spec["direction"] == "local_emsgsize":
        need(ledger_row is None and row["full_delivery_passed"] is False, "Local OS rejection was relabelled delivery")
        return
    need(row["target"] == ledger_row, "Packet/target ledger disagree")
    target = ledger_row
    need(target["leaf"] == leaf and target["target"] == row["requested_target"] and
         tuple_ok(target["source"], "127.0.0.4"), "Wrong target/source tuple")
    need(target["request_expected"] == ident(request), "Target expected request changed")
    n, m = target["request_actual"]["bytes"], row["actual_reply"]["bytes"]
    need(type(n) is int and type(m) is int and 0 <= n <= len(request) and 0 <= m <= len(reply), "Invalid observed lengths")
    need(target["request_actual"] == ident(request[:n]) and row["actual_reply"] == ident(reply[:m]), "Actual hashes are not expected prefixes")
    full = n == len(request) and m == len(reply)
    need(target["request_full"] is (n == len(request)) and row["full_delivery_passed"] is full,
         "Actual full-delivery classification contradicts bytes")
    sent = reply if spec["direction"] in ("downlink", "uplink_ack") else request[:n]
    need(target["reply_sent"] == ident(sent) and target["sendto_return"] == len(sent), "Target did not send complete declared reply")
    wire = b"\0\0\0\x01" + socket.inet_aton(row["requested_target"][0]) + row["requested_target"][1].to_bytes(2, "big") + reply[:m]
    need(row["socks_reply_wire"] == ident(wire), "SOCKS wire hash/length differs from decoded tuple/payload")
    need(row["reply_source"] == row["socks_relay"] and row["reply_target"] == row["requested_target"], "Reply tuple changed")
    need(target["request_exact_prefix"] is True and row["reply_exact_prefix"] is True, "Prefix integrity missing")
    if case["stage"] == "A":
        need(row["actual_reply"] == ident(sent), "A return path corrupted stock ingress observation")
    if case["stage"] in ("B", "V"):
        need(target["request_full"] is True, "Downlink probe did not isolate its complete small request")
    if spec["expected_prefix"] is not None:
        need(n == spec["expected_prefix"] and not full, "Known warmed RED boundary changed")
    if not spec["diagnostic"]:
        need(full and row["status"] == "full_delivery_passed", "Strict packet is not fully delivered")
    else:
        need(row["status"] == ("full_delivery_passed" if full else "capacity_failed_exact_prefix"), "Diagnostic status changed")

def verify_cleanup(result, process_rows):
    value = result.get("cleanup", {})
    for key in ("threads_joined", "fixture_removed", "all_target_workers_joined", "application_sockets_closed"):
        need(value.get(key) is True, "Incomplete cleanup boolean: " + key)
    launches = [row["launches"][0] for row in process_rows]
    need(all(type(row.get("birth_time")) in (int, float) and row["birth_time"] > 0 for row in launches),
         "Missing numeric process launch birth identity")
    pids = [row["pid"] for row in launches]
    need(len(pids) == 3 and len(set(pids)) == 3, "Three roles did not own distinct PIDs")
    lives = result.get("process_identity", [])
    exited = value.get("processes", [])
    need(Counter(row.get("pid") for row in lives) == Counter(pids) == Counter(row.get("pid") for row in exited),
         "Launch/live/cleanup PID sets differ")
    expected = {row["pid"]: row["birth_time"] for row in launches}
    for row in lives:
        need(type(row.get("birth_time")) in (int, float) and row["birth_time"] > 0 and
             row["birth_time"] == expected[row["pid"]], "PID birth identity changed")
    for row in exited:
        need(row.get("reaped") is True and type(row.get("returncode")) is int, "Missing reaped PID terminal status")
    frozen = result.get("frozen_target_ledgers", [])
    need(len(frozen) == 1, "Frozen original empty target disappeared or was replaced")
    for row in frozen:
        need(row.get("closed") is True and row.get("thread_joined") is True and row.get("socket_closed") is True and
             row.get("received") == [] and row.get("expected") == [] and row.get("errors") == [],
             "Frozen target received unexpected traffic or failed cleanup")


def verify_lifecycle(case, result, process_rows):
    lifecycle = result.get("lifecycle", {})
    need(digest(lifecycle.get("observation_sha256")), "Missing lifecycle observation digest")
    proof = lifecycle.get("proof", {})
    need(proof.get("mode") == case["mode"], "Wrong large-packet lifecycle mode")
    by_role = {r["role"]: r["launches"][0] for r in process_rows}
    def sockets(snapshot):
        need(set(p["role"] for p in snapshot["processes"]) == {"domestic", "overseas"} and
             len(snapshot["processes"]) == 2, "Lifecycle hop snapshots incomplete")
        need(snapshot["monotonic"] <= snapshot["sample_completed_monotonic"], "Inverted socket sample")
        values = {}
        for p in snapshot["processes"]:
            launch = by_role[p["role"]]
            need(p["pid"] == launch["pid"] and p["create_time"] == launch["birth_time"] and not p["races"],
                 "Lifecycle PID birth changed or sampling race unresolved")
            for sock in p["sockets"]:
                key = (p["role"], p["pid"], sock["inode"])
                need(type(sock["inode"]) is int and sock["inode"] > 0 and key not in values,
                     "Invalid/duplicate actual socket inode")
                values[key] = sock
        return values
    prior = None
    for sample in proof.get("prebaseline_samples", []):
        sockets(sample)
        need((prior is None or sample["monotonic"] >= prior) and
             sample["sample_completed_monotonic"] <= proof["baseline"]["monotonic"],
             "Prebaseline readiness samples overlap the actual lifecycle window")
        prior = sample["sample_completed_monotonic"]
    baseline, live = sockets(proof["baseline"]), sockets(proof["live"])
    need(all(r["protocol"] == "tcp" and r["proc_state"] == "0A" for r in baseline.values()),
         "Lifecycle did not start from an idle process baseline")
    added = {k:v for k,v in live.items() if k not in baseline}
    port, ingress = proof["leg"]["upstream_port"], proof["ingress_port"]
    domestic = [(k,r) for k,r in added.items() if k[0] == "domestic" and r["protocol"] == "tcp" and
                r["remote"] == {"host":"127.0.0.2", "port":port}]
    overseas = [(k,r) for k,r in added.items() if k[0] == "overseas" and r["protocol"] == "tcp" and
                r["local"] == {"host":"127.0.0.2", "port":port} and r["proc_state"] == "01"]
    udp = [(k,r) for k,r in added.items() if k[0] == "overseas" and r["protocol"] == "udp" and
           r["local"]["host"] == "127.0.0.4"]
    parent = [(k,r) for k,r in added.items() if k[0] == "domestic" and r["protocol"] == "tcp" and
              r["local"] == {"host":"127.0.0.1", "port":ingress} and r["proc_state"] == "01"]
    need(len(domestic) == len(overseas) == len(udp) == len(parent) == 1 and len(added) == 4,
         "Actual association socket trio and parent are not exact")
    keys = {domestic[0][0], overseas[0][0], udp[0][0]}
    need(Counter(map(tuple, proof["leg"]["keys"])) == Counter(keys) and
         domestic[0][1]["local"] == overseas[0][1]["remote"] == proof["leg"]["domestic_local"],
         "Socket leg keys or reciprocal TCP tuple changed")
    first, recovery = result["packets"]
    need(udp[0][1]["local"] == {"host": first["target"]["source"][0], "port": first["target"]["source"][1]},
         "Observed UDP socket differs from actual target source tuple")
    last, deadline = proof["last_business_reply"], proof["deadline"]
    need(last == first["reply_monotonic"] and deadline == last + 6 and
         proof["baseline"]["sample_completed_monotonic"] <= last <= proof["live"]["monotonic"],
         "Lifecycle deadline not measured from actual last business reply")
    samples = proof["samples"]
    need(samples and samples[0] == proof["baseline"] and proof["live"] in samples and samples[-1] == proof["final"],
         "Lifecycle sample window omitted baseline/live/final")
    prior, absent = None, None
    for sample in samples:
        if prior is not None:
            need(sample["monotonic"] >= prior, "Nonmonotonic lifecycle samples")
        prior = sample["sample_completed_monotonic"]
        current = sockets(sample)
        need(current.keys() <= live.keys(), "Unrequested replacement socket appeared")
        if sample["monotonic"] >= proof["live"]["sample_completed_monotonic"]:
            if not keys & current.keys():
                absent = absent or sample
            else:
                need(absent is None, "Released inode reappeared")
    need(absent == proof["first_absence"] and absent is not None and
         absent["sample_completed_monotonic"] <= deadline and proof["final"]["monotonic"] >= deadline and
         not keys & sockets(proof["final"]).keys(), "Missing/late exact socket release or incomplete window")
    need(proof["recovery_leaf"] == recovery["leaf"] and recovery["full_delivery_passed"] is True and
         proof["recovery_reply_monotonic"] == recovery["reply_monotonic"] >= proof["final"]["sample_completed_monotonic"],
         "No real large-packet recovery after release window")


def verify_lifecycle_files(output, cases, rows):
    by_case = {row["case"]: row for row in rows}
    for case in cases:
        if case["mode"] not in ("close", "idle"):
            continue
        result = by_case[case["id"]]
        path = output / case["id"] / "lifecycle-observation.json"
        need(path.is_file() and not path.is_symlink() and sha(path) == result["lifecycle"]["observation_sha256"],
             "Lifecycle observation absent/changed")
        data = json_read(path)
        proofs = [e["proof"] for e in data["events"] if e["kind"] == "large_endpoint_proof"]
        need(data["status"] == "observed" and proofs == [result["lifecycle"]["proof"]],
             "Lifecycle file does not carry the exact verified proof")

def assess(cases, rows, processes, binaries):
    verify_leaf_set([case["id"] for case in cases], [row["case"] for row in rows], "case")
    verify_leaf_set(leaf_ids(cases), [p["leaf"] for row in rows for p in row["packets"]], "packet")
    need(all(row["status"] == "observed" and not row["errors"] and row.get("cleanup")
             and row.get("unexecuted_leaves") == [] for row in rows),
         "One or more cases incomplete/failed cleanup")
    expected_roles = Counter((case["id"], role) for case in cases for role in case["roles"])
    need(Counter((p["case"], p["role"]) for p in processes) == expected_roles, "Process role leaf coverage mismatch")
    by_case = {case["id"]: case for case in cases}
    for row in processes:
        expected = by_case[row["case"]]["roles"][row["role"]]
        binary = binaries[expected]
        need(row["origin"] == expected and row["binary"] == binary["path"] and row["sha256"] == binary["sha256"] and
             row["version"] == binary["version"] and row["classification"] == ("official" if expected == "official" else "experimental") and row["parser_passed"] is True and digest(row["config_sha256"]),
             "Role origin/binary/parser/config evidence mismatch")
        need(len(row["launches"]) == 1 and row["launches"][0]["ready"] is True and
             type(row["launches"][0].get("pid")) is int and row["launches"][0]["pid"] > 0,
             "Core not launched exactly once")
        need(row["launches"][0]["sha256"] == binary["sha256"], "Launch digest changed")
    red = []
    for result in rows:
        case = by_case[result["case"]]
        owned = [r for r in processes if r["case"] == result["case"]]
        verify_cleanup(result, owned)
        if case["mode"] in ("idle", "close"):
            verify_lifecycle(case, result, owned)
        ledger = result["target_ledger"]
        target_leaves = [case["id"] + "/" + p["id"] for p in case["packets"] if p["direction"] != "local_emsgsize"]
        verify_leaf_set(target_leaves, [p["leaf"] for p in ledger["packets"]], "target")
        need(ledger["closed"] is True and ledger["thread_joined"] is True and ledger["socket_closed"] is True and
             ledger["planned_count"] == ledger["received_count"] == len(target_leaves) and not ledger["errors"],
             "Target packet or cleanup counts differ")
        need(len(ledger["actual_datagrams"]) == len(target_leaves), "Actual target datagram events missing or extra")
        for actual, accounted in zip(ledger["actual_datagrams"], ledger["packets"]):
            need(actual["target"] == accounted["target"] and actual["source"] == accounted["source"] and
                 actual["payload"] == accounted["request_actual"] and actual["flags"] == 0 and
                 actual["ancillary_count"] == 0, "Actual target receive event/ledger mismatch")
        need(not result["dns"]["ordinary"] and not result["dns"]["business"] and not result["dns"]["expected"],
             "Literal IPv4 probe unexpectedly used DNS")
        need({tuple(p["target"]) for p in ledger["packets"]} == {(ledger["host"], ledger["port"])},
             "Per-packet target tuple differs from actual bound target")
        if case["mode"] == "packets":
            need(len({tuple(p["source"]) for p in ledger["packets"]}) == 1,
                 "One live association unexpectedly changed its overseas source tuple")
        if case["mode"] != "close":
            need(len({tuple(p["application_socket"]) for p in result["packets"]}) == 1 and
                 len({tuple(p["socks_relay"]) for p in result["packets"]}) == 1,
                 "Live SOCKS association/socket identity changed")
        ledger_by_leaf = {p["leaf"]: p for p in ledger["packets"]}
        sent_count = 0
        for spec, p in zip(case["packets"], result["packets"]):
            first = sent_count == 0 or (case["mode"] == "close" and spec["id"] == "recover")
            verify_packet(case, spec, p, ledger_by_leaf.get(p["leaf"]), first)
            if p["direction"] != "local_emsgsize":
                sent_count += 1
            if p["direction"] == "local_emsgsize":
                need(p["status"] == "local_emsgsize_verified" and p.get("errno") == errno.EMSGSIZE and
                     p.get("socks_wire_bytes") == 65508 and p.get("local_zero_delivery") is True,
                     "Missing local EMSGSIZE proof")
            elif not p["full_delivery_passed"]:
                need(case["stage"] in ("A", "B", "U", "V") and p["status"] == "capacity_failed_exact_prefix", "Strict capacity failure")
                red.append(p["leaf"])
    stage = cases[0]["stage"]
    if stage == "A":
        for protocol in ("aes", "chacha"):
            need(f"A_{protocol}_warmed/boundary_15993" in red, "Stock VMess warmed RED absent")
    if stage == "B":
        need({f"B_{p}_response/response_{n}" for p in ("aes", "chacha", "vless") for n in (32768, 60000)} <= set(red),
             "Stock overseas large-response RED absent")
    if stage in ("C", "D"):
        need(not red, "Strict repaired capacity or lifecycle remains RED")
    return {"diagnostic_complete": True, "stage_capacity_passed": stage in ("C", "D") or (stage in ("U", "V") and not red),
            "capacity_failed_leaves": red, "original_suite_passed": False, "product_qualified": False,
            "lifecycle_scope": "large_endpoint_socket_smoke_only" if stage == "D" else "not_run"}


def expected_artifacts(cases):
    result = {"result.json", "plan.json", "input-identity.json", "process-binaries.json", "suite.log", "runner-error.log"}
    for case in cases:
        name = case["id"]
        result.update({f"{name}/result.json", f"{name}/packets.json", f"{name}/error.log",
                       f"frozen-fixture/{name}.json", f"frozen-fixture/{name}.log"})
        if case["mode"] in ("idle", "close"):
            result.add(f"{name}/lifecycle-observation.json")
    return result


def write_evidence_manifest(output, cases):
    allowed = expected_artifacts(cases)
    files = {}
    for path in output.rglob("*"):
        need(not path.is_symlink(), "Linked artifact forbidden")
        if path.is_file():
            name = str(path.relative_to(output))
            need(name in allowed, "Unexpected artifact; no binaries/configs/material upload: " + name)
            content = path.read_text()
            need("PRIVATE KEY-----" not in content and "BEGIN CERTIFICATE" not in content,
                 "Sensitive material in runtime evidence")
            files[name] = sha(path)
    need({"result.json", "plan.json", "input-identity.json", "process-binaries.json", "suite.log"} <= files.keys(),
         "Missing root evidence files")
    for case in cases:
        need({case["id"] + "/result.json", case["id"] + "/packets.json",
              "frozen-fixture/" + case["id"] + ".json", "frozen-fixture/" + case["id"] + ".log"} <= files.keys(),
             "Missing fixed case evidence files")
        if case["mode"] in ("idle", "close"):
            need(case["id"] + "/lifecycle-observation.json" in files, "Missing fixed lifecycle observation file")
    save(output / "manifest.json", {"schema_version": 1, "kind": KIND + "-evidence", "files": files})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=list(ROLES), required=True)
    parser.add_argument("--describe", action="store_true", help="Print only fixed plan; no core/socket access")
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--binary-manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    cases = plan(args.stage)
    if args.describe:
        print(json.dumps({"cases": cases, "case_count": len(cases), "packet_count": len(leaf_ids(cases)),
                          "linux_only": True, "ipv4_only": True, "product_qualified": False}, indent=2))
        return 0
    need(args.repository and args.binary_manifest and args.output, "Real run requires repository, binary manifest and fresh output")
    need(sys.platform.startswith("linux"), "Real packet runtime is Linux-only")
    args.output.mkdir(parents=True, exist_ok=False)
    save(args.output / "plan.json", cases)
    save(args.output / "process-binaries.json", [])
    report = {"schema_version": 1, "kind": KIND, "stage": args.stage, "diagnostic_complete": False,
              "stage_capacity_passed": False, "original_suite_passed": False, "product_qualified": False,
              "cases": [], "errors": []}
    try:
        repo = args.repository.resolve(strict=True)
        c = load_reuse(repo)
        identity = source_identity(repo, c)
        data = json_read(args.binary_manifest)
        provenance = validate_manifest(data, identity, repo)
        save(args.output / "input-identity.json", {"source_identity": identity,
             "manifest_sha256": sha(args.binary_manifest), "build_provenance": provenance,
             "official": data["official"], "candidate": data["candidate"]})
        binaries = {k: data[k] for k in ("official", "candidate")}
        # Frozen fixture expects both vars, but this independent matrix launches
        # only sing-box; no fake Mihomo core or official version proof is invented.
        stock = Path(binaries["official"]["path"])
        need(stock.name == "sing-box", "Official fixture core path must end in sing-box")
        env_before = {k: os.environ.get(k) for k in ("VUI_TEST_CORES", "VUI_TEST_MIHOMO", "VUI_DATA_DIR")}
        data_directory = tempfile.TemporaryDirectory(prefix="vui-packet-data-")
        os.environ["VUI_DATA_DIR"] = data_directory.name
        os.environ["VUI_TEST_CORES"] = str(stock.parent)
        os.environ.setdefault("VUI_TEST_MIHOMO", "not-launched-by-packet-boundary")
        records = []
        try:
            with c.base.experiment_environment(args.output / "frozen-fixture"), (args.output / "suite.log").open("w") as log:
                with redirect_stdout(log), redirect_stderr(log):
                    module, _ = c.base.runtime_modules()
                    psutil = importlib.import_module("psutil")
                    stage_started = time.monotonic()
                    for case in cases:
                        need(time.monotonic() - stage_started < 1200, "Stage exceeded its twenty-minute budget; remaining leaves unexecuted")
                        report["cases"].append(run_case(case, c, module, args.output, binaries, records, psutil))
            verify_lifecycle_files(args.output, cases, report["cases"])
            report.update(assess(cases, report["cases"], records, binaries))
            need(source_identity(repo, c) == identity, "Repository changed during runtime")
            for binary in binaries.values():
                need(sha(binary["path"]) == binary["sha256"], "Binary changed during runtime")
        finally:
            for key, value in env_before.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            data_directory.cleanup()
    except Exception as exc:
        report["errors"].append({"type": type(exc).__name__, "message": str(exc)})
        (args.output / "runner-error.log").write_text(traceback.format_exc())
        report.update(diagnostic_complete=False, stage_capacity_passed=False)
    if "c" in locals():
        report = c.life.scrub(report)
        for path in args.output.rglob("*"):
            if path.is_file() and path.suffix == ".log":
                path.write_text(c.life.clean(path.read_text()))
            elif path.is_file() and path.suffix == ".json":
                save(path, c.life.scrub(json_read(path)))
    save(args.output / "result.json", report)
    try:
        write_evidence_manifest(args.output, cases)
    except Exception as exc:
        report["errors"].append({"check": "evidence_manifest", "message": str(exc)})
        report.update(diagnostic_complete=False, stage_capacity_passed=False)
        save(args.output / "result.json", report)
    print(json.dumps({k: report[k] for k in ("stage", "diagnostic_complete", "stage_capacity_passed", "original_suite_passed", "product_qualified")}))
    return 0 if report["diagnostic_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
