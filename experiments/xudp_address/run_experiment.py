#!/usr/bin/env python3
"""Explicit, isolated source experiment. Never a product core override.

The unchanged 17-method D2 suite retains its official pin checks. Only fixture
process roles named domestic/domestic-reconfigured use the experimental binary.
A baseline is accepted only for the exact previously observed 22 initial-port
assertions. A patched run must complete all original gates and both TCP checks plus the same-domain/same-port DNS rebinding vector.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import socketserver
import threading
import sys
import time
import traceback
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BASE_COMMIT = "49ec3f8269e0b250a50fe9f9a1d0d56f0e560441"
SOURCE_COMMITS = {
    "sing_box": ("1.14.2", "af6e64c3b69e6132ebaee0e1a3d24e93903f6709"),
    "sing_vmess": ("0.2.8", "31ec11e8790c48a4bf11711806a61e929ee6b89d"),
}
STOCK_HASHES = {
    "sing-box": "07d2866a6c908281a56053f0e7d454918501592f2f258526f2ebe826dab58793",
    "mihomo": "3122d100e8177501776109f1a6253a694611627cf4d7c7ec82705855cf8626a8",
}
FROZEN_FILES = {
    "tests/test_restricted_udp_preflight.py": "65533f115b67fe3f88d489b2289a3243ffeaf3776d3094aad85a0526eb84c034",
    "tests/restricted_udp_preflight_helpers.py": "6cf081f447bb4b6c8e6553d4b8bf25051fbef1bb7a2a0b0fc8be730c9545cbe9",
    "tests/proxy_loopback_support.py": "6bd9164bcff46c43a7d52b5ffcd11ce8c77741a203a3ae979a88d998ff1e836c",
    "tests/udp_dns_capability_helpers.py": "7706e422336b60b79e31a959c583b28725c5ed6a3cf632d71652b2f80bee77ea",
    "tests/loopback_helpers.py": "9916e72962f4dd090346b5cda2079d88da8a6208e818e9cc3964e71567c065c8",
    "app/services/proxy_policy.py": "4ed96d6e131f9f4860469569f6964b281c957154294c82a677fc2a1b520c1e98",
    "app/services/upstream_profile.py": "98d4fc4c31ce3536be164cc9fa03f96816d5aeeee3160136aa7d00216ac77a02",
    "scripts/fetch_test_cores.py": "efa2991b83618172164978c85e82ef347e9f387e8b6546e4f6565cd2c2ae3daf",
    "scripts/fetch_mihomo.py": "59f178594b56d475392f601affd4ee2e71f103a6a0830eb78259f5603c535de2",
}
METHODS = (
    "test_01_naked_mihomo_tls_xudp_ipv4_double_hop",
    "test_02_naked_singbox_tls_xudp_ipv4_double_hop",
    "test_03_guarded_mihomo_exact_payload_address_and_port",
    "test_04_guarded_singbox_exact_payload_address_and_port",
    "test_05_mihomo_upstream_uuid_ca_sni_stop_recovery",
    "test_06_singbox_upstream_uuid_ca_sni_stop_recovery",
    "test_07_mihomo_device_uuid_ca_sni",
    "test_08_singbox_device_uuid_ca_sni",
    "test_09_raw_xudp_doh_all_answers_private_mixed_and_fail_closed",
    "test_10_raw_xudp_partial_dns_family_and_both_failed_no_fallback",
    "test_11_naked_raw_xudp_retarget_positive_control",
    "test_12_guarded_raw_xudp_cannot_retarget_private_self_upstream_or_port",
    "test_13_guarded_raw_domain_rebinding_cannot_escape_checked_address",
    "test_14_guarded_udp_idle_drains_and_new_association_recovers",
    "test_15_guarded_new_associations_reject_udp_53_443_and_low_ports",
    "test_candidate_shapes_with_exact_core_parser_only",
    "test_xudp_address_and_frame_exact_fixture_bytes",
)
TCP_METHODS = ("test_tcp_https_mihomo_double_hop", "test_tcp_https_singbox_double_hop")
DNS_METHODS = ("test_same_domain_same_port_dns_rebinding",)
# Freeze the client selected by each actual fixture call. Raw-XUDP method
# names do not identify their device core: in particular test_10 starts singbox.
# Fault suffixes are expanded only from FAULTS below, never inferred from text.
CASE_CLIENTS = {
    "test_01_naked_mihomo_tls_xudp_ipv4_double_hop": "mihomo",
    "test_02_naked_singbox_tls_xudp_ipv4_double_hop": "singbox",
    "test_03_guarded_mihomo_exact_payload_address_and_port": "mihomo",
    "test_04_guarded_singbox_exact_payload_address_and_port": "singbox",
    "test_05_mihomo_upstream_uuid_ca_sni_stop_recovery": "mihomo",
    "test_06_singbox_upstream_uuid_ca_sni_stop_recovery": "singbox",
    "test_07_mihomo_device_uuid_ca_sni": "mihomo",
    "test_08_singbox_device_uuid_ca_sni": "singbox",
    "test_09_raw_xudp_doh_all_answers_private_mixed_and_fail_closed": "mihomo",
    "test_10_raw_xudp_partial_dns_family_and_both_failed_no_fallback": "singbox",
    "test_11_naked_raw_xudp_retarget_positive_control": "mihomo",
    "test_12_guarded_raw_xudp_cannot_retarget_private_self_upstream_or_port": "mihomo",
    "test_13_guarded_raw_domain_rebinding_cannot_escape_checked_address": "mihomo",
    "test_14_guarded_udp_idle_drains_and_new_association_recovers": "mihomo",
    "test_15_guarded_new_associations_reject_udp_53_443_and_low_ports": "mihomo",
    "test_tcp_https_mihomo_double_hop": "mihomo",
    "test_tcp_https_singbox_double_hop": "singbox",
    "test_same_domain_same_port_dns_rebinding": "mihomo",
}
FAULTS = {METHODS[4]: ("uuid", "ca", "sni", "stopped"),
          METHODS[5]: ("uuid", "ca", "sni", "stopped"),
          METHODS[6]: ("uuid", "ca", "sni"), METHODS[7]: ("uuid", "ca", "sni")}
BASELINE_FAILURES = Counter({name: len(FAULTS.get(name, (None,)))
                             for name in METHODS[2:15] if name != METHODS[10]})
DOMESTIC_ROLES = frozenset(("domestic", "domestic-reconfigured"))


class ExperimentError(ValueError):
    """An experiment prerequisite or evidence check failed closed."""


def require(condition, message):
    if not condition:
        raise ExperimentError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def read_json(path):
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate provenance key: " + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_text(), object_pairs_hook=no_duplicates)


def validate_provenance(data, phase):
    require(phase in ("baseline", "patched"), "Unknown experiment phase")
    require(isinstance(data, dict), "Provenance must be an object")
    require(type(data.get("schema_version")) is int and data["schema_version"] == 1,
            "Unsupported provenance schema")
    require(data.get("kind") == "vui-xudp-address-source-experiment", "Wrong provenance kind")
    require(data.get("phase") == phase, "Candidate provenance phase mismatch")
    commit = data.get("vui_source_commit", "")
    require(isinstance(commit, str) and re.fullmatch(r"[a-f0-9]{40}", commit),
            "Missing full V-UI source commit")
    version = f"1.14.2-vui-xudp-{phase}-experiment-{commit[:12]}"
    require(data.get("version") == version, "Not the exact experimental version label")
    digest = data.get("binary_sha256", "")
    require(isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest), "Invalid binary hash")
    require(digest not in STOCK_HASHES.values(), "Experimental candidate cannot be an official binary")
    for key, (expected_version, expected_commit) in SOURCE_COMMITS.items():
        source = data.get(key)
        require(isinstance(source, dict) and source.get("version") == expected_version
                and source.get("commit") == expected_commit, "Unexpected fixed source: " + key)
    build = data.get("build", {})
    require(isinstance(build, dict), "Missing build parameters")
    for key, expected in (("go_version", "go1.25.14"), ("goos", "linux"),
                          ("goarch", "amd64"), ("tags", []), ("cgo_enabled", "0")):
        require(build.get(key) == expected, "Unexpected build parameter: " + key)
    flags = build.get("ldflags")
    require(isinstance(flags, str) and
            f"-X github.com/sagernet/sing-box/constant.Version={version}" in flags,
            "Build flags do not identify the experimental version")
    change = data.get("patch", {})
    require(isinstance(change, dict) and type(change.get("applied")) is bool,
            "Missing explicit patch state")
    require(change["applied"] is (phase == "patched"), "Patch state differs from phase")
    if phase == "baseline":
        require(change.get("sha256") is None and change.get("path") is None,
                "Baseline must not claim an applied patch")
    else:
        value = change.get("sha256", "")
        require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value), "Invalid patch hash")
        require(isinstance(change.get("path"), str) and change["path"], "Missing patch path")
    return version


def verify_candidate(candidate, provenance, phase, stock_paths, *, run=subprocess.run):
    version = validate_provenance(provenance, phase)
    candidate = Path(candidate).resolve(strict=True)
    require(candidate.is_file() and os.access(candidate, os.X_OK), "Candidate must be executable")
    for path in stock_paths.values():
        require(not candidate.samefile(path), "Candidate aliases an official binary path")
    require(sha256(candidate) == provenance["binary_sha256"], "Candidate SHA-256 mismatch")
    if phase == "patched":
        require(sha256(Path(provenance["patch"]["path"])) == provenance["patch"]["sha256"],
                "Applied patch SHA-256 mismatch")
    result = run([str(candidate), "version"], text=True, capture_output=True, timeout=15)
    output = result.stdout + result.stderr
    require(result.returncode == 0 and output.splitlines()
            and output.splitlines()[0] == "sing-box version " + version,
            "Candidate does not report its exact experimental version")
    return {"path": str(candidate), "sha256": provenance["binary_sha256"], "version_output": output}


def source_identity(root, provenance):
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True,
                            capture_output=True, check=True, timeout=15)
    require(result.stdout.strip() == provenance["vui_source_commit"],
            "Build provenance V-UI commit differs from this checkout HEAD")
    hashes = {name: sha256(root / name) for name in FROZEN_FILES}
    require(hashes == FROZEN_FILES, "An original gate/helper/policy/pin file changed")
    return {"vui_commit": result.stdout.strip(), "frozen_d2_commit": BASE_COMMIT,
            "frozen_files": hashes, "runner_sha256": sha256(Path(__file__))}


def choose_binary(label, mihomo, candidate, stock_singbox, stock_mihomo):
    if label in DOMESTIC_ROLES:
        require(not mihomo, "Domestic role must be sing-box")
        return candidate
    return stock_mihomo if mihomo else stock_singbox


class BinaryLedger:
    def __init__(self, candidate, stock, destination):
        self.candidate, self.stock = candidate, stock
        self.destination = Path(destination)
        self.records = []
        (self.destination / "delivery-ledgers").mkdir(exist_ok=True)

    def check(self, path):
        path = Path(path).resolve(strict=True)
        known = [self.candidate, *self.stock.values()]
        matching = [item for item in known if path == Path(item["path"]).resolve()]
        require(len(matching) == 1, "Unrecognized or aliased process binary")
        require(sha256(path) == matching[0]["sha256"], "Process binary changed during experiment")
        return matching[0]["sha256"]

    def save(self):
        write_json(self.destination / "process-binaries.json", self.records)

    def record(self, fixture, label, path, mihomo):
        record = {"case": fixture.label, "role": label, "binary": str(Path(path).resolve()),
                  "sha256": self.check(path), "mihomo": mihomo, "parser_passed": False, "launches": []}
        self.records.append(record)
        self.save()
        return record


def make_fixture(base, ledger):
    """Only this subclass substitutes binaries; original helper bytes stay frozen."""
    class ExperimentalFixture(base):
        def __exit__(self, kind, error, traceback):
            try:
                return super().__exit__(kind, error, traceback)
            finally:
                # Keep actual packet and DNS accounting after original teardown,
                # independently of the original compact status/count artifact.
                def packets(items):
                    return [{"peer": peer, "bytes": len(payload),
                             "payload_sha256": hashlib.sha256(payload).hexdigest(), "count": count}
                            for (peer, payload), count in sorted(Counter(items).items())]
                rows = [{"host": target.host, "port": target.port,
                         "received": packets(target.received), "expected": packets(target.expected),
                         "errors": list(target.errors), "closed": target.closed}
                        for target in self.targets]
                write_json(ledger.destination / "delivery-ledgers" / (self.label + ".json"),
                           {"case": self.label, "udp_targets": rows,
                            "business_dns": self.answers.snapshot() if self.answers is not None else [],
                            "expected_dns": [[*key, count] for key, count in sorted(self.expected_dns.items())],
                            "ordinary_dns": self.ordinary_answers.queries if self.ordinary_answers is not None else []})

        def process(self, label, config, *, mihomo=False, env=None):
            stock = self.singbox
            selected = choose_binary(label, mihomo, ledger.candidate["path"], stock, self.mihomo)
            entry = ledger.record(self, label, selected, mihomo)
            canonical = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
            entry["config_sha256"] = hashlib.sha256(canonical).hexdigest()
            entry["config_shape"] = {
                "sections": sorted(config),
                "inbound_types": [node.get("type") for node in config.get("inbounds", [])],
                "outbound_types": [node.get("type") for node in config.get("outbounds", [])],
                "proxy_types": [node.get("type") for node in config.get("proxies", [])],
                "route_rule_count": len(config.get("route", {}).get("rules", [])),
            }
            ledger.save()
            if label in DOMESTIC_ROLES:
                self.singbox = selected
            try:
                process = super().process(label, config, mihomo=mihomo, env=env)
            finally:
                self.singbox = stock
            require(Path(process.command[0]).resolve() == Path(selected).resolve(),
                    "Fixture process command differs from selected role binary")
            entry["parser_passed"] = True
            original_start = process.start
            def start(*args, **kwargs):
                require(Path(process.command[0]).resolve() == Path(selected).resolve(),
                        "Process role binary changed before launch")
                attempt = {"sha256": ledger.check(process.command[0]), "ready": False}
                entry["launches"].append(attempt)
                ledger.save()
                try:
                    value = original_start(*args, **kwargs)
                    attempt["ready"] = True
                    return value
                finally:
                    attempt["pid"] = process.process.pid if process.process is not None else None
                    attempt["command"] = [self.safe(str(value)) for value in process.command]
                    ledger.save()
            process.start = start
            ledger.save()
            return process
    return ExperimentalFixture


def initial_port_failure(err, root=ROOT):
    """Classify actual traceback locals, never arbitrary text matching or timeout."""
    exception = err[1]
    if not isinstance(exception, AssertionError):
        return None
    if any("D2 lifetime cleanup failed:" in note for note in getattr(exception, "__notes__", ())):
        return None
    current = err[2]
    while current is not None:
        frame, line = current.tb_frame, current.tb_lineno
        filename = Path(frame.f_code.co_filename).resolve()
        values = frame.f_locals
        found = None
        if (filename == root / "tests/restricted_udp_preflight_helpers.py"
                and frame.f_code.co_name == "echo"):
            expected_line = "self.case.assertEqual(actual_port, target.port)"
            fixture, target = values.get("self"), values.get("target")
            actual = values.get("actual_port")
            found = (fixture, target, actual, "socks-reply-port")
        elif (filename == root / "tests/test_restricted_udp_preflight.py"
              and frame.f_code.co_name == "raw_echo"):
            expected_line = 'self.assertEqual(got_port, actual.port, "XUDP reply port differs from actual target")'
            found = (values.get("f"), values.get("actual"), values.get("got_port"), "raw-xudp-reply-port")
        if found:
            source_line = filename.read_text().splitlines()[line - 1].strip()
            fixture, target, actual, kind = found
            if (source_line == expected_line and fixture is not None and target is not None
                    and isinstance(actual, int) and actual == fixture.upstream_port
                    and actual != target.port and not fixture.observations):
                return {"kind": kind, "case": fixture.label, "actual_port": actual,
                        "expected_target_port": target.port, "upstream_tcp_port": fixture.upstream_port,
                        "file": str(filename.relative_to(root)), "line": line}
        current = current.tb_next
    return None


class ExperimentResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.methods = []
        self.failure_records = []
        self.nonpass = []
        self._current = None

    def startTest(self, test):
        super().startTest(test)
        self._current = {"method": test._testMethodName, "id": test.id(), "status": "running",
                         "subtests": []}
        self.methods.append(self._current)

    def addSuccess(self, test):
        super().addSuccess(test)
        self._current["status"] = "passed"

    def _failure(self, test, err, subtest=None):
        method = getattr(test, "_testMethodName", test.id())
        record = {"method": method, "subtest": str(subtest) if subtest else None,
                  "fault": dict(subtest.params).get("fault") if subtest is not None else None,
                  "initial_port": initial_port_failure(err),
                  "exception": err[0].__name__, "message": str(err[1])}
        self.failure_records.append(record)
        if self._current:
            self._current["status"] = "failed"

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._failure(test, err)

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        self._current["subtests"].append({"label": str(subtest), "status": "passed" if err is None else "failed"})
        if err is not None:
            self._failure(test, err, subtest)
            if not issubclass(err[0], test.failureException):
                self.nonpass.append({"kind": "error", "id": str(subtest)})

    def addError(self, test, err):
        super().addError(test, err)
        self.nonpass.append({"kind": "error", "id": test.id()})
        if self._current:
            self._current["status"] = "error"

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.nonpass.append({"kind": "skip", "id": test.id(), "reason": reason})
        if self._current:
            self._current["status"] = "skipped"

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.nonpass.append({"kind": "expected_failure", "id": test.id()})
        self._current["status"] = "expected_failure"

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.nonpass.append({"kind": "unexpected_success", "id": test.id()})
        self._current["status"] = "unexpected_success"

    def summary(self):
        return {"tests_run": self.testsRun, "methods": self.methods, "failures": self.failure_records,
                "nonpass": self.nonpass, "passed_methods": sum(item["status"] == "passed" for item in self.methods),
                "failed_methods": sum(item["status"] == "failed" for item in self.methods),
                "failure_records": len(self.failure_records)}


def assess_suite(phase, result, tcp=False, dns=False):
    require(not (tcp and dns), "Ambiguous extra suite")
    require(phase in ("baseline", "patched"), "Unknown result phase")
    expected = DNS_METHODS if dns else TCP_METHODS if tcp else METHODS
    require(result["tests_run"] == len(expected), "Wrong executed method count")
    require(Counter(item["method"] for item in result["methods"]) == Counter(expected),
            "Missing, duplicated, or unexpected original methods")
    require(not result["nonpass"], "Errors, skips, expected failures, or unexpected successes are not qualification")
    if phase == "patched" or tcp or dns:
        require(not result["failures"] and all(item["status"] == "passed" for item in result["methods"]),
                "Every method must pass without failures")
        return
    require(phase == "baseline", "Unknown result phase")
    failures = result["failures"]
    require(Counter(item["method"] for item in failures) == BASELINE_FAILURES,
            "Baseline must reproduce exactly 22 failure records in the original 12 methods")
    require(result["passed_methods"] == 5 and result["failed_methods"] == 12,
            "Baseline must have exactly 5 passed and 12 failed methods")
    for item in result["methods"]:
        expected_status = "failed" if item["method"] in BASELINE_FAILURES else "passed"
        require(item["status"] == expected_status, "Unexpected baseline method outcome")
    for name, faults in FAULTS.items():
        require(Counter(item["fault"] for item in failures if item["method"] == name) == Counter(faults),
                "Baseline fault-subtest coverage changed")
    for failure in failures:
        observation = failure["initial_port"]
        require(observation is not None and failure["exception"] == "AssertionError",
                "Baseline failure is not the actual initial reply-port assertion")
        require(observation["actual_port"] == observation["upstream_tcp_port"]
                and observation["actual_port"] != observation["expected_target_port"],
                "Baseline mismatch does not equal the actual upstream TCP port")
        case = failure["method"] + ("-" + failure["fault"] if failure["fault"] else "")
        require(observation["case"] == case, "Baseline assertion belongs to an unexpected fixture")


def expected_cases(phase):
    result = []
    for method in METHODS[:15]:
        result.extend(method + "-" + fault for fault in FAULTS[method]) if method in FAULTS else result.append(method)
    result.append(METHODS[15])
    if phase == "patched":
        result.extend(TCP_METHODS)
        result.extend(DNS_METHODS)
    return result


def verify_case_evidence(phase, directory):
    names = expected_cases(phase)
    files = sorted(Path(directory).glob("*.json"))
    require({file.stem for file in files} == set(names), "Per-case evidence count or identity mismatch")
    rows = {}
    for name in names:
        row = read_json(Path(directory) / (name + ".json"))
        require((Path(directory) / (name + ".log")).is_file(), "Missing per-case core logs")
        failed = phase == "baseline" and any(name == method or name.startswith(method + "-")
                                             for method in BASELINE_FAILURES)
        require(row.get("status") == ("failed" if failed else "passed"), "Wrong per-case status: " + name)
        rows[name] = row
    if phase == "patched":
        observations = [item for row in rows.values() for item in row["observations"]]
        kinds = Counter(item["kind"] for item in observations)
        # Real runtime gates, not just method-discovery or parser counts.
        for kind, count in (("echo", 38), ("rejection", 18), ("raw-rejection", 10),
                            ("guarded-retarget", 1), ("naked-retarget", 1),
                            ("idle-cleanup", 1), ("tcp-https-double-hop", 2),
                            ("same-domain-same-port-rebinding", 1)):
            require(kinds[kind] == count, f"Incomplete {kind} evidence: {kinds[kind]} != {count}")
        retarget = next(item for item in observations if item["kind"] == "guarded-retarget")
        require(retarget.get("changes") == 9 and retarget.get("outcome") == "all kept initial checked target",
                "Guarded changed-target probes did not retain the original checked target")
        same_port = next(item for item in observations if item["kind"] == "same-domain-same-port-rebinding")
        require(same_port.get("initial_port") == same_port.get("changed_port")
                and type(same_port.get("initial_port")) is int and 1024 <= same_port["initial_port"] <= 65535
                and same_port.get("initial_address") == "127.0.0.3"
                and same_port.get("changed_answer") == "127.0.0.7"
                and same_port.get("same_association_kept_initial_target") is True
                and same_port.get("new_association_rejected") is True
                and same_port.get("trap_deliveries") == same_port.get("trap_positive_controls") == 1,
                "Same-domain/same-port rebind evidence is incomplete or changed its port")
    return rows


def case_client(case):
    if case in CASE_CLIENTS:
        return CASE_CLIENTS[case]
    for method, faults in FAULTS.items():
        if case in {method + "-" + fault for fault in faults}:
            return CASE_CLIENTS[method]
    raise ExperimentError("Unknown fixture case: " + case)


def expected_process_roles(phase):
    require(phase in ("baseline", "patched"), "Unknown process-ledger phase")
    result = Counter()
    for case in expected_cases(phase):
        if case == METHODS[15]:
            result.update((case, role) for role in ("overseas", "guarded", "device-singbox", "device-mihomo"))
            continue
        kind = case_client(case)
        result.update((case, role) for role in ("overseas", "domestic", "device-" + kind))
        if phase == "patched" and any(case.startswith(method + "-") for method in FAULTS):
            result.update({(case, "domestic-reconfigured"): 2, (case, "device-reconfigured-" + kind): 2})
    return result


def verify_isolation(phase, records, candidate, stock):
    require(records, "No fixture processes recorded")
    domestic = [item for item in records if item["role"] in DOMESTIC_ROLES]
    require(any(item["role"] == "domestic" for item in domestic), "Candidate never used by domestic role")
    require(Counter((item["case"], item["role"]) for item in records) == expected_process_roles(phase),
            "Missing, duplicated, or unexpected per-case process roles")
    expected_domestic = 25 if phase == "baseline" else 56
    require(len(domestic) == expected_domestic, "Unexpected domestic process coverage")
    require(sum(item["role"] == "domestic-reconfigured" for item in domestic)
            == (0 if phase == "baseline" else 28), "Domestic restart coverage mismatch")
    for item in records:
        expected_mihomo = item["role"] in ("device-mihomo", "device-reconfigured-mihomo")
        require(type(item["mihomo"]) is bool and item["mihomo"] is expected_mihomo,
                "Process role and declared core family disagree")
        expected = candidate if item["role"] in DOMESTIC_ROLES else stock["mihomo" if item["mihomo"] else "sing-box"]
        require(item["binary"] == expected["path"] and item["sha256"] == expected["sha256"],
                "Candidate leaked into a non-domestic role or wrong binary ran")
        require(item["parser_passed"], "A selected process failed its parser check")
        for launch in item["launches"]:
            require(launch["ready"] and launch["sha256"] == expected["sha256"]
                    and isinstance(launch["pid"], int), "A process did not actually launch the selected binary")
        launches = 0 if item["case"] == METHODS[15] else 1
        if phase == "patched" and item["role"] == "overseas" and item["case"].endswith("-stopped"):
            launches = 2
        require(len(item["launches"]) == launches, "Missing or unexpected actual process launches")


def make_tcp_tests(fixture, support):
    class TcpCompatibility(unittest.TestCase):
        def exercise(self, kind):
            with fixture(self, self._testMethodName) as f:
                expected = []
                def final_https_audit():
                    self.assertEqual(traffic.snapshot(), expected, "Late or direct HTTPS delivery")
                    self.assertEqual(traffic.pending(), 0, "HTTPS fixture did not close")
                # Registered before target cleanup, so LIFO executes this only
                # after the server, accepted sockets and cores have all closed.
                f.stack.callback(final_https_audit)
                port, traffic = support.https_target(f.stack, support.TARGET, f.cert, f.key)
                f.start(kind, domestic=f.guarded_config())
                marker = "experimental-tcp-" + kind
                response = support.https_through(f.client_port, support.TARGET, port, f.ca,
                                                marker, protocol="socks5h")
                self.assertEqual(response, (200, support.BODY))
                deadline = time.monotonic() + 3
                while traffic.pending():
                    self.assertLess(time.monotonic(), deadline, "HTTPS traffic did not drain")
                    time.sleep(.01)
                expected = [(support.UPSTREAM_EGRESS, support.TARGET, "/" + marker)]
                self.assertEqual(traffic.snapshot(), expected, "HTTPS bypassed the overseas hop")
                f.audit()
                f.observations.append({"kind": "tcp-https-double-hop", "client": kind,
                                       "verified_tls": True, "status": response[0],
                                       "body_sha256": hashlib.sha256(response[1]).hexdigest(),
                                       "deliveries": traffic.snapshot()})
            # The original fixture has now stopped every core and closed/joined
            # the HTTPS target. Late or direct deliveries cannot hide in teardown.
            self.assertEqual(traffic.snapshot(), expected)
            self.assertEqual(traffic.pending(), 0)

        def test_tcp_https_mihomo_double_hop(self):
            self.exercise("mihomo")

        def test_tcp_https_singbox_double_hop(self):
            self.exercise("singbox")
    return TcpCompatibility


def make_same_port_target(base):
    """Reuse every original lifetime/allowlist assertion with an explicit trap port."""
    class SamePortDatagramTarget(base):
        def __init__(self, host, port):
            require(host == "127.0.0.7" and type(port) is int and 1024 <= port <= 65535,
                    "Same-port trap is restricted to the controlled DENIED IPv4 fixture")
            self.responder = lambda packet: packet
            self.lock = threading.Lock()
            self.received, self.errors = [], []
            self.expected = Counter()
            self.closed = False
            owner = self

            class Handler(socketserver.BaseRequestHandler):
                def handle(self):
                    packet, stream = self.request
                    with owner.lock:
                        owner.received.append((self.client_address[0], packet))
                    try:
                        stream.sendto(owner.responder(packet), self.client_address)
                    except Exception as exc:
                        with owner.lock:
                            owner.errors.append(repr(exc))

            self.server = socketserver.UDPServer((host, port), Handler)
            self.server.socket.settimeout(.25)
            self.host, self.port = self.server.server_address
            self.thread = threading.Thread(target=self.server.serve_forever,
                                           kwargs={"poll_interval": .01}, daemon=True)
            self.thread.start()
    return SamePortDatagramTarget


def make_dns_tests(fixture, module):
    helper = importlib.import_module("udp_dns_capability_helpers")
    same_port_target = make_same_port_target(helper.DatagramTarget)

    class SamePortDnsRebinding(unittest.TestCase):
        def test_same_domain_same_port_dns_rebinding(self):
            # Supplemental experiment vector, not a modified original test 13:
            # the wire domain AND port stay constant; only its DNS answer moves.
            with fixture(self, self._testMethodName) as f:
                trap = same_port_target(module.DENIED, f.target.port)
                f.targets.append(trap)
                trap.direct(b"same-port-denied-trap-reachable")
                f.target.direct(b"same-port-original-target-reachable")
                name = "same-port-rebind.example.test"
                config = f.guarded_config()
                f.answers.records[name] = (module.TARGET,)
                f.start("mihomo", domestic=config)
                f.expect_dns(name)
                with module.RawXudpAssociation(f, name, f.target.port) as association:
                    module.RestrictedUdpPreflightTests.raw_echo(
                        self, f, association, name, f.target.port, b"same-port-first-checked-answer")
                    f.answers.records[name] = (module.DENIED,)
                    module.RestrictedUdpPreflightTests.raw_echo(
                        self, f, association, name, f.target.port, b"same-port-keep-original-binding")
                    f.audit()  # Requires no new DNS queries in this association.
                f.expect_dns(name)
                f.raw_reject(b"same-port-new-association-denied", f.domestic, ("reject",),
                             host=name, port=f.target.port, target=trap)
                self.assertEqual(trap.port, f.target.port)
                self.assertEqual(len(trap.received), 1, "Protected flow reached the same-port private trap")
                f.audit()
                f.observations.append({"kind": "same-domain-same-port-rebinding", "domain": name,
                                       "initial_address": module.TARGET, "changed_answer": module.DENIED,
                                       "initial_port": f.target.port, "changed_port": trap.port,
                                       "same_association_kept_initial_target": True,
                                       "new_association_rejected": True,
                                       "trap_deliveries": len(trap.received), "trap_positive_controls": 1})
    return SamePortDnsRebinding


@contextmanager
def experiment_environment(evidence):
    # The two binary-location variables are deliberately absent from this map.
    values = {"VUI_RESTRICTED_UDP_PREFLIGHT": "1", "VUI_RESTRICTED_UDP_PARSER": "1",
              "VUI_RESTRICTED_UDP_EVIDENCE": str(evidence)}
    with patch.dict(os.environ, values):
        yield


def runtime_modules():
    sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
    module = importlib.import_module("test_restricted_udp_preflight")
    support = importlib.import_module("proxy_loopback_support")
    require(Path(module.__file__).resolve() == ROOT / "tests/test_restricted_udp_preflight.py",
            "Unexpected original suite module path")
    return module, support


def run_suite(suite, log):
    result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=ExperimentResult).run(suite)
    return result.summary()


def execute(args, report):
    original_env = {key: os.environ.get(key) for key in ("VUI_TEST_CORES", "VUI_TEST_MIHOMO")}
    require(all(original_env.values()), "VUI_TEST_CORES and VUI_TEST_MIHOMO must point to official fixed cores")
    paths = {"sing-box": Path(original_env["VUI_TEST_CORES"]) / "sing-box",
             "mihomo": Path(original_env["VUI_TEST_MIHOMO"])}
    stock = {}
    for name, path in paths.items():
        path = path.resolve(strict=True)
        require(sha256(path) == STOCK_HASHES[name], "Official fixed-core binary hash mismatch: " + name)
        stock[name] = {"path": str(path), "sha256": STOCK_HASHES[name]}
    provenance = read_json(args.provenance)
    report["provenance"] = provenance
    report["provenance_sha256"] = sha256(args.provenance)
    report["source_identity"] = source_identity(ROOT, provenance)
    candidate = verify_candidate(args.candidate, provenance, args.phase, paths)
    report["candidate"], report["stock"] = candidate, stock
    cases = args.output / "cases"
    cases.mkdir()
    ledger = BinaryLedger(candidate, stock, args.output)
    with experiment_environment(cases), (args.output / "suite.log").open("w") as log:
        with redirect_stdout(log), redirect_stderr(log):
            module, support = runtime_modules()
            fixture = make_fixture(module.RestrictedUdpFixture, ledger)
            with patch.object(module, "RestrictedUdpFixture", fixture):
                original = unittest.defaultTestLoader.loadTestsFromModule(module)
                report["original"] = run_suite(original, log)
                # Run TCP independently even if the original suite fails: preserve
                # useful results, but do not promote any partial outcome.
                if args.phase == "patched":
                    tcp = unittest.defaultTestLoader.loadTestsFromTestCase(make_tcp_tests(fixture, support))
                    report["tcp"] = run_suite(tcp, log)
                    dns = unittest.defaultTestLoader.loadTestsFromTestCase(make_dns_tests(fixture, module))
                    report["dns_rebinding"] = run_suite(dns, log)
    require(all(os.environ.get(key) == value for key, value in original_env.items()),
            "Official core environment changed during experiment")
    source_identity(ROOT, provenance)
    for item in [candidate, *stock.values()]:
        ledger.check(item["path"])
    report["process_records"] = len(ledger.records)
    report["process_role_counts"] = dict(Counter(item["role"] for item in ledger.records))
    assess_suite(args.phase, report["original"])
    if args.phase == "patched":
        assess_suite(args.phase, report["tcp"], tcp=True)
        assess_suite(args.phase, report["dns_rebinding"], dns=True)
    verify_isolation(args.phase, ledger.records, candidate, stock)
    rows = verify_case_evidence(args.phase, cases)
    ledgers = args.output / "delivery-ledgers"
    require({path.stem for path in ledgers.glob("*.json")} == set(rows),
            "Missing or unexpected per-case delivery ledger")
    for path in ledgers.glob("*.json"):
        evidence = read_json(path)
        require(not evidence["ordinary_dns"], "Ordinary DNS fallback appeared in lifetime ledger")
        require(Counter(tuple(item) for item in evidence["business_dns"])
                == Counter({tuple(item[:-1]): item[-1] for item in evidence["expected_dns"]}),
                "DNS lifetime ledger differs from authorized lookups")
        for target in evidence["udp_targets"]:
            require(target["closed"] and not target["errors"] and target["received"] == target["expected"],
                    "UDP lifetime ledger differs from pre-authorized deliveries")
    report["case_count"] = len(rows)
    report["outcome"] = "baseline_reproduced" if args.phase == "baseline" else "patched_experiment_passed"
    report["qualified"] = True
    report["scope"] = ("Experimental evidence only. No product pin, runtime bundle, release or deployment change. "
                       "Guarded later destinations retain the initial checked target; this is not per-frame rerouting.")


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--phase", required=True, choices=("baseline", "patched"))
    value.add_argument("--candidate", type=Path, required=True)
    value.add_argument("--provenance", type=Path, required=True)
    value.add_argument("--output", type=Path, required=True)
    return value


def main(argv=None):
    args = parser().parse_args(argv)
    # Never overwrite a previous experiment or the repository itself.
    try:
        args.output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print("Experiment refused: output directory already exists", file=sys.stderr)
        return 2
    report = {"schema_version": 1, "phase": args.phase, "qualified": False, "outcome": "not_qualified"}
    try:
        execute(args, report)
    except Exception as exc:
        report["blocker"] = str(exc)
        (args.output / "runner-error.log").write_text(traceback.format_exc())
    finally:
        write_json(args.output / "result.json", report)
    print(json.dumps({key: report[key] for key in ("phase", "qualified", "outcome")}))
    if "blocker" in report:
        print(report["blocker"], file=sys.stderr)
    return 0 if report["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
