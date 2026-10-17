#!/usr/bin/env python3
"""Two-arm real runtime matrix. A verified result still preserves original 19/20 RED."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
import importlib
import json
import os
from pathlib import Path
import sys
import traceback
import unittest
from unittest.mock import patch

import common as c
import compatibility

ALL_CASES = (*c.life.ALL_CASES, *compatibility.CASES)
ARTIFACTS = frozenset({
    "result.json", "process-binaries.json", "suite.log", "runner-error.log",
    *(f"cases/{case}{extension}" for case in ALL_CASES for extension in (".json", ".log")),
    *(f"delivery-ledgers/{case}.json" for case in ALL_CASES),
    *(f"observations/{case}.json" for case in (*c.life.EXTRA_CASES, *compatibility.CASES)),
})


def assess_original(groups):
    result = c.life.assess_original("lifecycle", groups)
    c.need(result["executed"] == 20 and result["passed"] == 19 and result["failed"] == 1 and
           result["original_suite_passed"] is False, "Original 19/20 RED identity changed")
    return result


def verify_processes(records, candidate, stock):
    existing = [row for row in records if row["case"] not in compatibility.CASES]
    c.life.verify_isolation("lifecycle", existing, candidate, stock)
    extra = [row for row in records if row["case"] in compatibility.CASES]
    c.need(Counter((row["case"], row["role"]) for row in extra) ==
           Counter((case, role) for case in compatibility.CASES
                   for role in ("domestic", "overseas", "device-singbox")),
           "Missing/duplicated compatibility process role")
    for row in extra:
        expected = candidate if row["role"] == "domestic" else stock["sing-box"]
        c.need(row["mihomo"] is False and row["binary"] == expected["path"] and
               row["sha256"] == expected["sha256"] and row["parser_passed"] is True,
               "Compatibility candidate leaked into official peer/device or parser failed")
        c.need(len(row["launches"]) == 1, "Compatibility process did not launch exactly once")
        launch = row["launches"][0]
        c.need(launch["ready"] is True and launch["sha256"] == expected["sha256"] and
               type(launch["pid"]) is int and launch["pid"] > 0, "Missing compatibility launch identity")
        c.need(c.digest(row.get("config_sha256")), "Missing compatibility config digest")
        expected_inbound = "vmess" if row["case"] in compatibility.VMESS else "vless"
        if row["role"] == "domestic":
            c.need(row["config_shape"]["inbound_types"] == [expected_inbound], "Wrong actual candidate ingress")


def verify_ledgers(output):
    c.need({path.stem for path in (output / "cases").glob("*.json")} == set(ALL_CASES),
           "Missing/unexpected case evidence")
    c.need({path.stem for path in (output / "delivery-ledgers").glob("*.json")} == set(ALL_CASES),
           "Missing/unexpected delivery ledger")
    rows = {}
    for case in ALL_CASES:
        row = c.read_json(output / "cases" / (case + ".json"))
        c.need((output / "cases" / (case + ".log")).is_file(), "Missing case log")
        c.need(row["status"] == ("failed" if case == c.base.METHODS[12] else "passed"),
               "Wrong preserved case result: " + case)
        rows[case] = row
        ledger = c.read_json(output / "delivery-ledgers" / (case + ".json"))
        c.need(ledger["case"] == case and not ledger["ordinary_dns"] and
               Counter(map(tuple, ledger["business_dns"])) ==
               Counter({tuple(item[:-1]): item[-1] for item in ledger["expected_dns"]}), "DNS lifetime audit failed")
        for target in ledger["udp_targets"]:
            c.need(target["closed"] is True and not target["errors"] and target["received"] == target["expected"],
                   "UDP lifetime target/port/payload/peer ledger differs")
    original = Counter(item["kind"] for case in c.base.expected_cases("patched") for item in rows[case]["observations"])
    c.need(original == Counter({"echo": 38, "rejection": 18, "raw-rejection": 9, "guarded-retarget": 1,
                                "naked-retarget": 1, "tcp-https-double-hop": 2,
                                "same-domain-same-port-rebinding": 1, "idle-cleanup": 1}),
           "Frozen original paths did not execute completely")
    c.need(next(item for item in rows[c.base.METHODS[11]]["observations"]
                if item["kind"] == "guarded-retarget")["changes"] == 9, "Incomplete retarget vectors")
    for case in (*c.life.EXTRA_CASES, *compatibility.CASES):
        row = c.read_json(output / "observations" / (case + ".json"))
        field = "compatibility_passed" if case in compatibility.CASES else "lifecycle_contract_passed"
        c.need(row["status"] == "observed" and row.get(field) is True, "Supplemental case missing/failed")
        c.need(sum(event["kind"] == "cleanup_verified" for event in row["events"]) == 1,
               "Missing final joined fixture cleanup")
        if case == compatibility.TCP:
            c.need(sum(event["kind"] == "tcp_lifetime_audit" for event in row["events"]) == 1,
                   "Missing final TCP payload/peer ledger")


def verify_outputs(output):
    for path in output.rglob("*"):
        c.need(not path.is_symlink(), "Linked runtime evidence is forbidden")
        if path.is_file():
            c.need(str(path.relative_to(output)) in ARTIFACTS, "Unexpected artifact (binary/config/key upload is forbidden)")
            text = path.read_text()
            c.need(c.life.clean(text) == text, "Unscrubbed runtime artifact")
            if path.suffix == ".json":
                c.need(c.life.scrub(c.read_json(path)) == c.read_json(path), "Unscrubbed nested evidence")


def run_arm(arm, candidate, stock, provenance, identity, output):
    output.mkdir()
    (output / "cases").mkdir()
    (output / "observations").mkdir()
    ledger = c.base.BinaryLedger(candidate, stock, output)
    report = {"schema_version": c.SCHEMA, "kind": c.KIND, "arm": arm, "source_identity": identity,
              "provenance": provenance, "candidate": candidate, "stock": stock, "matrix_verified": False,
              "original_suite_passed": False, "product_qualified": False, "outcome": "runtime_error",
              "original": {"executed": False}, "supplemental": {}, "compatibility": {}}
    problems = []
    env = {key: os.environ.get(key) for key in ("VUI_TEST_CORES", "VUI_TEST_MIHOMO")}
    try:
        with c.base.experiment_environment(output / "cases"), (output / "suite.log").open("w") as log:
            with redirect_stdout(log), redirect_stderr(log):
                module, support = c.base.runtime_modules()
                fixture = c.base.make_fixture(module.RestrictedUdpFixture, ledger)
                # Only fixture binary-role selection is substituted. Test methods,
                # raw_echo, assertions and timeout constants are all unchanged.
                with patch.object(module, "RestrictedUdpFixture", fixture):
                    report["groups"] = {
                        "original": c.life.run_suite(unittest.defaultTestLoader.loadTestsFromModule(module), log),
                        "tcp": c.life.run_suite(unittest.defaultTestLoader.loadTestsFromTestCase(c.base.make_tcp_tests(fixture, support)), log),
                        "dns_rebinding": c.life.run_suite(unittest.defaultTestLoader.loadTestsFromTestCase(c.base.make_dns_tests(fixture, module)), log),
                    }
                report["original"]["executed"] = True
                module.RestrictedUdpPreflightTests.setUpClass()
                psutil_module = importlib.import_module("psutil")
                for case in (*c.life.EXTRA_CASES, *compatibility.CASES):
                    try:
                        if case in compatibility.CASES:
                            report["compatibility"][case] = compatibility.run_case(case, fixture, module, psutil_module, output)
                        else:
                            report["supplemental"][case] = c.life.run_extra(case, fixture, module, "lifecycle", psutil_module, output)
                    except Exception as exc:
                        problems.append({"case": case, "type": type(exc).__name__, "message": c.life.clean(str(exc))})
                        print(c.life.clean(traceback.format_exc()))
    except Exception as exc:
        problems.append({"check": "execution", "type": type(exc).__name__, "message": c.life.clean(str(exc))})
        (output / "runner-error.log").write_text(c.life.clean(traceback.format_exc()))
    def check(name, action):
        try:
            return action()
        except Exception as exc:
            problems.append({"check": name, "type": type(exc).__name__, "message": c.life.clean(str(exc))})
    original = check("original_20", lambda: assess_original(report.get("groups", {})))
    if original is not None:
        report["original"] = original
    check("source_identity", lambda: c.need(c.source_identity() == identity, "Checkout changed during runtime"))
    check("environment", lambda: c.need(all(os.environ.get(key) == value for key, value in env.items()),
                                         "Official peer/device environment changed"))
    for binary in (candidate, *stock.values()):
        check("binary_identity", lambda item=binary: ledger.check(item["path"]))
    check("process_isolation", lambda: verify_processes(ledger.records, candidate, stock))
    check("delivery_lifetimes", lambda: verify_ledgers(output))
    for path in output.rglob("*"):
        if path.is_file() and path.suffix == ".json":
            check("sanitize_json", lambda item=path: c.life.save(item, c.read_json(item)))
        elif path.is_file() and path.suffix == ".log":
            check("sanitize_log", lambda item=path: item.write_text(c.life.clean(item.read_text())))
    check("artifact_allowlist", lambda: verify_outputs(output))
    report.update(errors=problems, process_records=len(ledger.records), matrix_verified=not problems)
    if not problems:
        report["outcome"] = "runtime_contract_met_with_original_19_of_20_red"
    c.life.save(output / "result.json", report)
    verify_outputs(output)
    return report


def validate_pair(provenances, identity):
    for arm in c.ARMS:
        c.validate_provenance(provenances[arm], arm, identity)
    c.need(len({row["binary_sha256"] for row in provenances.values()}) == 2, "Candidate arms are not distinct")
    c.need(len({row["module_graph_sha256"] for row in provenances.values()}) == 1, "Arm module graphs differ")
    c.need(len({row["build"]["go_binary_sha256"] for row in provenances.values()}) == 1, "Arm compilers differ")
    c.need(all(provenances["route-only"]["source_files"][name]["before"] ==
               provenances["mux"]["source_files"][name]["before"] for name in c.SOURCE_FILES),
           "Arms use different original upstream source files")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True, type=Path, help="Output of build_candidates.py")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        args.output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print("Runtime refused: output already exists", file=sys.stderr)
        return 2
    report = {"schema_version": c.SCHEMA, "kind": c.KIND, "matrix_verified": False,
              "original_suite_passed": False, "product_qualified": False, "arms": {}, "outcome": "runtime_error"}
    try:
        identity = c.source_identity()
        env = {key: os.environ.get(key) for key in ("VUI_TEST_CORES", "VUI_TEST_MIHOMO")}
        c.need(all(env.values()), "Both fixed official binary environment paths are required")
        paths = {"sing-box": Path(env["VUI_TEST_CORES"]) / "sing-box", "mihomo": Path(env["VUI_TEST_MIHOMO"])}
        stock = {name: {"path": str(path.resolve(strict=True)), "sha256": c.sha256(path)} for name, path in paths.items()}
        c.need(all(row["sha256"] == c.base.STOCK_HASHES[name] for name, row in stock.items()),
               "Official peer/device hash mismatch")
        provenances = {arm: c.read_json(args.candidates / arm / "provenance.json") for arm in c.ARMS}
        validate_pair(provenances, identity)
        candidates = {arm: c.verify_candidate(args.candidates / arm / "candidate-sing-box", provenances[arm],
                                              arm, identity, stock) for arm in c.ARMS}
        report["source_identity"] = identity
        for arm in c.ARMS:
            report["arms"][arm] = run_arm(arm, candidates[arm], stock, provenances[arm], identity, args.output / arm)
        c.need(all(row["matrix_verified"] for row in report["arms"].values()), "One or both runtime arms failed")
        report.update(matrix_verified=True, outcome="two_arm_runtime_contract_met_original_gates_remain_red")
    except Exception as exc:
        report["blocker"] = c.life.clean(str(exc))
        (args.output / "runner-error.log").write_text(c.life.clean(traceback.format_exc()))
    c.life.save(args.output / "result.json", report)
    print(json.dumps({key: report[key] for key in ("outcome", "matrix_verified", "original_suite_passed", "product_qualified")}))
    return 0 if report["matrix_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
