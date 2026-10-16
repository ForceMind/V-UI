"""Pure guardrail tests. No real cores, sockets, network, or product overrides."""
from collections import Counter
from copy import deepcopy
import ast
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("xudp_experiment_runner", Path(__file__).with_name("run_experiment.py"))
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def provenance(phase="baseline", digest="a" * 64):
    version = f"1.14.2-vui-xudp-{phase}-experiment-{runner.BASE_COMMIT[:12]}"
    return {"schema_version": 1, "kind": "vui-xudp-address-source-experiment", "phase": phase,
            "vui_source_commit": runner.BASE_COMMIT, "version": version, "binary_sha256": digest,
            **{key: {"version": value[0], "commit": value[1]} for key, value in runner.SOURCE_COMMITS.items()},
            "build": {"go_version": "go1.25.14", "goos": "linux", "goarch": "amd64", "tags": [],
                      "cgo_enabled": "0", "ldflags": "-X github.com/sagernet/sing-box/constant.Version=" + version},
            "patch": {"applied": phase == "patched", "path": "/patch.diff" if phase == "patched" else None,
                      "sha256": "b" * 64 if phase == "patched" else None}}


def suite_summary(phase="baseline", tcp=False, dns=False):
    methods = runner.DNS_METHODS if dns else runner.TCP_METHODS if tcp else runner.METHODS
    failures = []
    if phase == "baseline" and not tcp and not dns:
        for method in runner.BASELINE_FAILURES:
            for fault in runner.FAULTS.get(method, (None,)):
                failures.append({"method": method, "fault": fault, "exception": "AssertionError",
                                 "initial_port": {"case": method + ("-" + fault if fault else ""),
                                                  "actual_port": 43000, "upstream_tcp_port": 43000,
                                                  "expected_target_port": 33000}})
    failed = {failure["method"] for failure in failures}
    return {"tests_run": len(methods), "methods": [{"method": method,
             "status": "failed" if method in failed else "passed"} for method in methods],
            "failures": failures, "nonpass": [], "passed_methods": len(methods) - len(failed),
            "failed_methods": len(failed), "failure_records": len(failures)}


def source_fixture_plan():
    """Read calls in frozen source, independently of runner expectation tables."""
    original = ast.parse((runner.ROOT / "tests/test_restricted_udp_preflight.py").read_text())
    extra = ast.parse(Path(runner.__file__).read_text())
    functions = {node.name: node for tree in (original, extra) for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)}
    wrappers = {"guarded_echo", "upstream_faults", "device_faults", "exercise"}
    tracked = {"start", "echo", "reject", "raw_reject", "restart_domestic_and_device"}

    def metrics(node):
        if isinstance(node, ast.For):
            body = sum((metrics(child) for child in node.body), Counter())
            if not body:
                return Counter()
            if not isinstance(node.iter, (ast.Tuple, ast.List)):
                raise AssertionError("A counted fixture loop needs explicit source review")
            return Counter({key: value * len(node.iter.elts) for key, value in body.items()})
        if isinstance(node, (ast.While, ast.If)):
            body = sum((metrics(child) for child in [*node.body, *node.orelse]), Counter())
            if body:
                raise AssertionError("A conditional fixture count needs explicit source review")
            return Counter()
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            receiver = node.func.value
            if isinstance(receiver, ast.Name):
                if receiver.id in ("f", "fixture") and node.func.attr in tracked:
                    return Counter({node.func.attr: 1})
                if receiver.id == "self" and node.func.attr in wrappers:
                    return metrics(functions[node.func.attr])
        return sum((metrics(child) for child in ast.iter_child_nodes(node)), Counter())

    plan = {}
    for name, function in functions.items():
        if not (name.startswith("test_") and len(name) > 7 and name[5:7].isdigit()
                or name in (*runner.TCP_METHODS, *runner.DNS_METHODS)):
            continue
        calls = [node for node in ast.walk(function)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and isinstance(node.func.value, ast.Name)
                 and ((node.func.value.id in ("f", "fixture") and node.func.attr == "start")
                      or (node.func.value.id == "self" and node.func.attr in wrappers))]
        if len(calls) != 1 or not calls[0].args or not isinstance(calls[0].args[0], ast.Constant):
            raise AssertionError("Fixture client call needs explicit source review: " + name)
        call = calls[0]
        faults = ()
        if call.func.attr in ("upstream_faults", "device_faults"):
            fault_loop = next(node for node in ast.walk(functions[call.func.attr])
                              if isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                              and node.target.id == "fault")
            faults = tuple(ast.literal_eval(fault_loop.iter))
        plan[name] = {"client": call.args[0].value, "faults": faults, "metrics": metrics(function)}
    parser = functions["test_candidate_shapes_with_exact_core_parser_only"]
    shapes = next(node for node in ast.walk(parser) if isinstance(node, ast.For))
    parser_roles = tuple(ast.literal_eval(item.elts[0]) for item in shapes.iter.elts)
    return plan, parser_roles


def source_process_roles(phase):
    plan, parser_roles = source_fixture_plan()
    result = Counter()
    for method, row in plan.items():
        if phase == "baseline" and method in (*runner.TCP_METHODS, *runner.DNS_METHODS):
            continue
        cases = [method + "-" + fault for fault in row["faults"]] if row["faults"] else [method]
        for case in cases:
            result.update((case, role) for role in ("overseas", "domestic", "device-" + row["client"]))
            if phase == "patched":
                restarts = row["metrics"]["restart_domestic_and_device"] // len(cases)
                result.update({(case, "domestic-reconfigured"): restarts,
                               (case, "device-reconfigured-" + row["client"]): restarts})
    result.update(("test_candidate_shapes_with_exact_core_parser_only", role) for role in parser_roles)
    return +result


class ProvenanceTests(unittest.TestCase):
    def test_exact_phase_versions_are_required(self):
        for phase in ("baseline", "patched"):
            runner.validate_provenance(provenance(phase), phase)
        for bad in ("1.14.2", "1.14.2-vui-xudp-patched-experiment-" + runner.BASE_COMMIT[:12],
                    "1.14.2-vui-xudp-baseline-experiment-short"):
            data = provenance()
            data["version"] = bad
            with self.subTest(version=bad), self.assertRaises(runner.ExperimentError):
                runner.validate_provenance(data, "baseline")

    def test_bad_contract_fields_fail_closed(self):
        changes = [(("schema_version",), True), (("kind",), "other"), (("phase",), "patched"),
                   (("vui_source_commit",), "short"), (("binary_sha256",), "bad"),
                   (("binary_sha256",), runner.STOCK_HASHES["sing-box"]),
                   (("sing_box", "commit"), "0" * 40), (("sing_vmess", "version"), "0.2.9"),
                   (("build", "go_version"), "go1.26.0"), (("build", "goos"), "darwin"),
                   (("build", "goarch"), "arm64"), (("build", "tags"), ["unexpected"]),
                   (("build", "cgo_enabled"), "1"), (("build", "ldflags"), ""),
                   (("patch", "applied"), True), (("patch", "sha256"), "0" * 64)]
        for keys, value in changes:
            data = provenance()
            target = data
            for key in keys[:-1]:
                target = target[key]
            target[keys[-1]] = value
            with self.subTest(keys=keys), self.assertRaises(runner.ExperimentError):
                runner.validate_provenance(data, "baseline")
        with self.assertRaises(runner.ExperimentError):
            runner.validate_provenance(provenance(), "nonsense")

    def test_patched_requires_actual_patch_and_correct_state(self):
        for key, value in (("applied", False), ("applied", 1), ("sha256", None), ("path", None)):
            data = provenance("patched")
            data["patch"][key] = value
            with self.subTest(key=key), self.assertRaises(runner.ExperimentError):
                runner.validate_provenance(data, "patched")

    def test_duplicate_provenance_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provenance.json"
            path.write_text('{"phase":"baseline","phase":"patched"}')
            with self.assertRaises(runner.ExperimentError):
                runner.read_json(path)

    def test_candidate_hash_checked_before_any_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, official = root / "candidate", root / "official"
            candidate.write_bytes(b"not an executable core")
            candidate.chmod(0o700)
            official.write_bytes(b"official")
            run = Mock()
            with self.assertRaisesRegex(runner.ExperimentError, "SHA-256"):
                runner.verify_candidate(candidate, provenance(), "baseline", {"sing-box": official}, run=run)
            run.assert_not_called()

    def test_official_samepath_symlink_and_hardlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            official = root / "official"
            official.write_bytes(b"fake")
            official.chmod(0o700)
            symbolic, hard = root / "symbolic", root / "hard"
            symbolic.symlink_to(official)
            os.link(official, hard)
            data = provenance(digest=runner.sha256(official))
            for candidate in (official, symbolic, hard):
                with self.subTest(path=candidate), self.assertRaisesRegex(runner.ExperimentError, "aliases"):
                    runner.verify_candidate(candidate, data, "baseline", {"sing-box": official}, run=Mock())

    def test_version_output_must_be_exact_not_prefix_spoof(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, official = root / "candidate", root / "official"
            candidate.write_bytes(b"fake")
            candidate.chmod(0o700)
            official.write_bytes(b"stock")
            data = provenance(digest=runner.sha256(candidate))
            correct = "sing-box version " + data["version"]
            for text in ("sing-box version 1.14.2\n", correct + "-other\n", "noise\n" + correct):
                run = Mock(return_value=SimpleNamespace(returncode=0, stdout=text, stderr=""))
                with self.subTest(text=text), self.assertRaises(runner.ExperimentError):
                    runner.verify_candidate(candidate, data, "baseline", {"sing-box": official}, run=run)
            run = Mock(return_value=SimpleNamespace(returncode=0, stdout=correct + "\nEnvironment: test\n", stderr=""))
            result = runner.verify_candidate(candidate, data, "baseline", {"sing-box": official}, run=run)
            self.assertEqual(result["sha256"], data["binary_sha256"])

    def test_candidate_patch_hash_is_verified_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, official, change = root / "candidate", root / "official", root / "patch"
            candidate.write_bytes(b"fake")
            candidate.chmod(0o700)
            official.write_bytes(b"stock")
            change.write_text("actual patch")
            data = provenance("patched", runner.sha256(candidate))
            data["patch"]["path"] = str(change)
            run = Mock()
            with self.assertRaisesRegex(runner.ExperimentError, "patch SHA-256"):
                runner.verify_candidate(candidate, data, "patched", {"sing-box": official}, run=run)
            run.assert_not_called()

    def test_frozen_original_files_still_exact(self):
        self.assertEqual({key: runner.sha256(runner.ROOT / key) for key in runner.FROZEN_FILES}, runner.FROZEN_FILES)

    def test_cli_requires_all_explicit_options(self):
        with patch("sys.stderr", new=io.StringIO()):
            for args in ([], ["--phase", "baseline"], ["--phase", "other"]):
                with self.subTest(args=args), self.assertRaises(SystemExit):
                    runner.parser().parse_args(args)


class ProcessSelectionTests(unittest.TestCase):
    def test_only_exact_domestic_roles_select_candidate(self):
        for label in ("domestic", "domestic-reconfigured"):
            self.assertEqual(runner.choose_binary(label, False, "candidate", "stock", "mihomo"), "candidate")
            with self.assertRaises(runner.ExperimentError):
                runner.choose_binary(label, True, "candidate", "stock", "mihomo")
        for label in ("overseas", "device-singbox", "device-reconfigured-singbox", "guarded",
                      "domestic-extra", "not-domestic", "device-mihomo", "device-reconfigured-mihomo"):
            for mihomo in (False, True):
                with self.subTest(label=label, mihomo=mihomo):
                    self.assertEqual(runner.choose_binary(label, mihomo, "candidate", "stock", "mihomo"),
                                     "mihomo" if mihomo else "stock")

    def test_subclass_restores_stock_and_records_restarts(self):
        seen = []
        class Base:
            def __init__(self):
                self.singbox, self.mihomo, self.label = "/stock", "/mihomo", "case"
            def safe(self, value):
                return value
            def process(self, label, config, *, mihomo=False, env=None):
                seen.append((label, self.singbox, self.mihomo, os.environ["VUI_TEST_CORES"]))
                process = SimpleNamespace(command=[self.mihomo if mihomo else self.singbox], process=None)
                def start(*args, **kwargs):
                    process.process = SimpleNamespace(pid=123)
                process.start = start
                return process
        entries = []
        ledger = Mock(candidate={"path": "/candidate"})
        def record(*args):
            entry = {"launches": []}
            entries.append(entry)
            return entry
        ledger.record.side_effect = record
        ledger.check.return_value = "a" * 64
        fixture = runner.make_fixture(Base, ledger)()
        with patch.dict(os.environ, {"VUI_TEST_CORES": "/official"}):
            for label, mihomo in (("domestic", False), ("overseas", False),
                                  ("domestic-reconfigured", False), ("device-singbox", False),
                                  ("device-reconfigured-mihomo", True)):
                process = fixture.process(label, {}, mihomo=mihomo)
                process.start(42)
                self.assertEqual(fixture.singbox, "/stock")
            self.assertEqual(os.environ["VUI_TEST_CORES"], "/official")
        self.assertEqual([value[1] for value in seen], ["/candidate", "/stock", "/candidate", "/stock", "/stock"])
        self.assertTrue(all(entry["parser_passed"] and entry["launches"][0]["ready"] for entry in entries))

    def test_subclass_restores_stock_after_failed_parser(self):
        class Base:
            singbox, mihomo, label = "/stock", "/mihomo", "case"
            def process(self, *args, **kwargs):
                raise AssertionError("actual parser failure")
        ledger = Mock(candidate={"path": "/candidate"})
        ledger.record.return_value = {"launches": []}
        fixture = runner.make_fixture(Base, ledger)()
        with self.assertRaises(AssertionError):
            fixture.process("domestic", {})
        self.assertEqual(fixture.singbox, "/stock")

    def test_optin_context_never_repoints_official_env(self):
        before = {"VUI_TEST_CORES": "/official", "VUI_TEST_MIHOMO": "/official-mihomo",
                  "VUI_RESTRICTED_UDP_PREFLIGHT": "old"}
        with patch.dict(os.environ, before):
            with runner.experiment_environment("/evidence"):
                self.assertEqual(os.environ["VUI_TEST_CORES"], "/official")
                self.assertEqual(os.environ["VUI_TEST_MIHOMO"], "/official-mihomo")
                self.assertEqual(os.environ["VUI_RESTRICTED_UDP_PREFLIGHT"], "1")
            self.assertEqual(os.environ["VUI_RESTRICTED_UDP_PREFLIGHT"], "old")

    def test_binary_ledger_rehashes_before_use(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate"
            candidate.write_bytes(b"first")
            ledger = runner.BinaryLedger({"path": str(candidate), "sha256": runner.sha256(candidate)}, {}, directory)
            ledger.check(candidate)
            candidate.write_bytes(b"changed")
            with self.assertRaisesRegex(runner.ExperimentError, "changed"):
                ledger.check(candidate)


class ResultAssessmentTests(unittest.TestCase):
    def test_exact_baseline_22_in_12_with_5_passes(self):
        result = suite_summary()
        self.assertEqual((result["tests_run"], result["passed_methods"], result["failed_methods"],
                          result["failure_records"]), (17, 5, 12, 22))
        runner.assess_suite("baseline", result)

    def test_arbitrary_baseline_failures_or_wrong_port_not_accepted(self):
        mutations = [lambda r: r["failures"][0].update(initial_port=None),
                     lambda r: r["failures"][0].update(exception="TimeoutError"),
                     lambda r: r["failures"][0]["initial_port"].update(actual_port=44000),
                     lambda r: r["failures"][0]["initial_port"].update(expected_target_port=43000),
                     lambda r: r["failures"][0]["initial_port"].update(case="other"),
                     lambda r: r["failures"].pop(),
                     lambda r: r["nonpass"].append({"kind": "error"}),
                     lambda r: r["failures"][2].update(fault="duplicate"),
                     lambda r: r.update(passed_methods=4)]
        for mutation in mutations:
            result = suite_summary()
            mutation(result)
            with self.subTest(mutation=mutation), self.assertRaises(runner.ExperimentError):
                runner.assess_suite("baseline", result)

    def test_unexpectedly_green_baseline_is_not_expected_reproduction(self):
        with self.assertRaises(runner.ExperimentError):
            runner.assess_suite("baseline", suite_summary("patched"))

    def test_patched_all_17_and_two_tcp_must_pass(self):
        runner.assess_suite("patched", suite_summary("patched"))
        runner.assess_suite("patched", suite_summary("patched", tcp=True), tcp=True)
        with self.assertRaises(runner.ExperimentError):
            runner.assess_suite("patched", suite_summary())

    def test_skip_error_expected_failure_and_missing_coverage_rejected(self):
        for kind in ("skip", "error", "expected_failure", "unexpected_success"):
            for tcp in (False, True):
                result = suite_summary("patched", tcp)
                result["nonpass"].append({"kind": kind})
                with self.subTest(kind=kind, tcp=tcp), self.assertRaises(runner.ExperimentError):
                    runner.assess_suite("patched", result, tcp=tcp)
        for mutate in (lambda r: r.update(tests_run=16), lambda r: r["methods"].pop(),
                       lambda r: r["methods"][0].update(method=r["methods"][1]["method"]),
                       lambda r: r["methods"][0].update(status="running")):
            result = suite_summary("patched")
            mutate(result)
            with self.assertRaises(runner.ExperimentError):
                runner.assess_suite("patched", result)

    def test_result_tracks_subtest_failures_as_failed_method(self):
        class Cases(unittest.TestCase):
            def test_failure(self):
                for value in (1, 2):
                    with self.subTest(fault=value):
                        self.assertEqual(value, 0)
            def test_pass(self):
                pass
        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=runner.ExperimentResult).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(Cases)).summary()
        self.assertEqual((result["tests_run"], result["failed_methods"], result["passed_methods"],
                          result["failure_records"]), (2, 1, 1, 2))
        self.assertTrue(all(item["initial_port"] is None for item in result["failures"]))

    def test_initial_port_classifier_uses_actual_frozen_assertion_frame(self):
        source = runner.ROOT / "tests/restricted_udp_preflight_helpers.py"
        statement = "self.case.assertEqual(actual_port, target.port)"
        line = next(index + 1 for index, text in enumerate(source.read_text().splitlines()) if text.strip() == statement)
        namespace = {}
        code = "\n" * (line - 2) + "def echo(self, target, actual_port):\n    " + statement + "\n"
        exec(compile(code, str(source), "exec"), namespace)
        fixture = SimpleNamespace(case=self, upstream_port=43000, observations=[], label="case")
        target = SimpleNamespace(port=33000)
        def collect(actual):
            try:
                namespace["echo"](fixture, target, actual)
            except AssertionError:
                return sys.exc_info()
        failure = collect(43000)
        self.assertEqual(runner.initial_port_failure(failure)["actual_port"], 43000)
        self.assertIsNone(runner.initial_port_failure(collect(44000)))
        fixture.observations.append({"kind": "prior-success"})
        self.assertIsNone(runner.initial_port_failure(failure))
        fixture.observations.clear()
        failure[1].add_note("D2 lifetime cleanup failed: unrelated late delivery")
        self.assertIsNone(runner.initial_port_failure(failure))

    def test_expected_case_counts_include_faults_parser_and_tcp(self):
        self.assertEqual(len(runner.expected_cases("baseline")), 26)
        self.assertEqual(len(runner.expected_cases("patched")), 29)
        self.assertEqual(len(set(runner.expected_cases("patched"))), 29)
        self.assertTrue(set(runner.TCP_METHODS).issubset(runner.expected_cases("patched")))

    def test_no_domestic_use_cannot_qualify(self):
        with self.assertRaises(runner.ExperimentError):
            runner.verify_isolation("baseline", [], {}, {})
        with self.assertRaisesRegex(runner.ExperimentError, "never used"):
            runner.verify_isolation("baseline", [{"role": "overseas"}], {}, {})

    def test_patched_evidence_requires_full_observation_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for case in runner.expected_cases("patched"):
                runner.write_json(root / (case + ".json"), {"status": "passed", "observations": []})
                (root / (case + ".log")).write_text("test evidence")
            with self.assertRaisesRegex(runner.ExperimentError, "Incomplete echo"):
                runner.verify_case_evidence("patched", root)
            observations = []
            for kind, count in (("echo", 38), ("rejection", 18), ("raw-rejection", 10),
                                ("naked-retarget", 1), ("idle-cleanup", 1), ("tcp-https-double-hop", 2)):
                observations.extend({"kind": kind} for _ in range(count))
            observations.append({"kind": "same-domain-same-port-rebinding", "initial_port": 33000,
                                 "changed_port": 33000, "initial_address": "127.0.0.3", "changed_answer": "127.0.0.7",
                                 "same_association_kept_initial_target": True, "new_association_rejected": True,
                                 "trap_deliveries": 1, "trap_positive_controls": 1})
            observations.append({"kind": "guarded-retarget", "changes": 9,
                                 "outcome": "all kept initial checked target"})
            runner.write_json(root / (runner.METHODS[0] + ".json"), {"status": "passed", "observations": observations})
            self.assertEqual(len(runner.verify_case_evidence("patched", root)), 29)
            (root / (runner.METHODS[1] + ".log")).unlink()
            with self.assertRaisesRegex(runner.ExperimentError, "Missing per-case"):
                runner.verify_case_evidence("patched", root)

    def test_process_role_coverage_matches_79_baseline_and_144_patched(self):
        candidate = {"path": "/candidate", "sha256": "a" * 64}
        stock = {"sing-box": {"path": "/stock", "sha256": "b" * 64},
                 "mihomo": {"path": "/mihomo", "sha256": "c" * 64}}
        for phase, count in (("baseline", 79), ("patched", 144)):
            records = []
            for (case, role), number in source_process_roles(phase).items():
                mihomo = "mihomo" in role
                identity = candidate if role in runner.DOMESTIC_ROLES else stock["mihomo" if mihomo else "sing-box"]
                launches = 0 if case == runner.METHODS[15] else 1
                if phase == "patched" and role == "overseas" and case.endswith("-stopped"):
                    launches = 2
                for _ in range(number):
                    records.append({"case": case, "role": role, "mihomo": mihomo,
                                    "binary": identity["path"], "sha256": identity["sha256"],
                                    "parser_passed": True, "launches": [
                                        {"ready": True, "pid": 42, "sha256": identity["sha256"]}
                                        for _ in range(launches)]})
            self.assertEqual(len(records), count)
            runner.verify_isolation(phase, records, candidate, stock)
            overseas = next(item for item in records if item["role"] == "overseas")
            overseas["binary"] = candidate["path"]
            with self.assertRaisesRegex(runner.ExperimentError, "leaked"):
                runner.verify_isolation(phase, records, candidate, stock)
            overseas["binary"] = stock["sing-box"]["path"]
            records.remove(overseas)
            with self.assertRaisesRegex(runner.ExperimentError, "per-case process roles"):
                runner.verify_isolation(phase, records, candidate, stock)

    def test_explicit_client_map_matches_every_frozen_fixture_call(self):
        plan, parser_roles = source_fixture_plan()
        self.assertEqual(len(plan), 18)
        self.assertEqual(runner.CASE_CLIENTS, {name: row["client"] for name, row in plan.items()})
        self.assertEqual(parser_roles, ("overseas", "guarded", "device-singbox", "device-mihomo"))
        self.assertEqual(runner.FAULTS, {name: row["faults"] for name, row in plan.items() if row["faults"]})
        for phase in ("baseline", "patched"):
            self.assertEqual(runner.expected_process_roles(phase), source_process_roles(phase))
        for invalid in ("test_unknown_singbox", runner.METHODS[4] + "-unknown"):
            with self.subTest(case=invalid), self.assertRaises(runner.ExperimentError):
                runner.case_client(invalid)

    def test_observation_and_restart_counts_come_from_actual_source_calls(self):
        plan, _ = source_fixture_plan()
        # Per-method decomposition comes from the original call sites and literal
        # loop lengths. It does not manufacture records from runner constants.
        per_method = [
            (1, 1, 0, 0, 0), (1, 1, 0, 0, 0), (1, 2, 0, 0, 0), (1, 2, 0, 0, 0),
            (4, 8, 4, 0, 8), (4, 8, 4, 0, 8), (3, 6, 3, 0, 6), (3, 6, 3, 0, 6),
            (1, 1, 1, 7, 0), (1, 1, 0, 1, 0), (1, 0, 0, 0, 0), (1, 0, 0, 0, 0),
            (1, 0, 0, 1, 0), (1, 1, 0, 0, 0), (1, 1, 3, 0, 0),
        ]
        keys = ("start", "echo", "reject", "raw_reject", "restart_domestic_and_device")
        original_names = sorted(name for name in plan if name[5:7].isdigit())
        self.assertEqual(len(original_names), 15)
        for name, counts in zip(original_names, per_method):
            self.assertEqual(tuple(plan[name]["metrics"][key] for key in keys), counts, name)
        original = sum((plan[name]["metrics"] for name in original_names), Counter())
        self.assertEqual(tuple(original[key] for key in keys), (25, 38, 18, 9, 28))
        self.assertEqual(plan[runner.TCP_METHODS[0]]["metrics"], Counter(start=1))
        self.assertEqual(plan[runner.TCP_METHODS[1]]["metrics"], Counter(start=1))
        self.assertEqual(plan[runner.DNS_METHODS[0]]["metrics"], Counter(start=1, raw_reject=1))
        total = sum((row["metrics"] for row in plan.values()), Counter())
        self.assertEqual(tuple(total[key] for key in keys), (28, 38, 18, 10, 28))
        # Three processes per start, two per restart, plus four official parsers.
        self.assertEqual(original["start"] * 3 + 4, 79)
        self.assertEqual(total["start"] * 3 + total["restart_domestic_and_device"] * 2 + 4, 144)
        self.assertEqual(total["start"] + total["restart_domestic_and_device"], 56)

    def test_real_first_attempt_test_10_uses_official_singbox(self):
        # Projection of CI 38943133607, candidate 93856d1, preserved unchanged in
        # xudp-93856d1-first-attempt.zip / logs/baseline/process-binaries.json.
        observed = {"case": "test_10_raw_xudp_partial_dns_family_and_both_failed_no_fallback",
                    "role": "device-singbox", "mihomo": False, "parser_passed": True,
                    "binary": "/tmp/vui-xudp-official/cores/sing-box",
                    "sha256": "07d2866a6c908281a56053f0e7d454918501592f2f258526f2ebe826dab58793",
                    "launches": [{"pid": 10842, "ready": True,
                                  "sha256": "07d2866a6c908281a56053f0e7d454918501592f2f258526f2ebe826dab58793"}]}
        self.assertNotIn("singbox", observed["case"])
        self.assertEqual(runner.case_client(observed["case"]), "singbox")
        for phase in ("baseline", "patched"):
            roles = runner.expected_process_roles(phase)
            self.assertEqual(roles[(observed["case"], observed["role"])], 1)
            self.assertEqual(roles[(observed["case"], "device-mihomo")], 0)
        self.assertEqual(observed["sha256"], runner.STOCK_HASHES["sing-box"])
        # The genuine record passes all unchanged identity/readiness/hash checks.
        candidate = {"path": "/candidate", "sha256": "a" * 64}
        stock = {"sing-box": {"path": observed["binary"], "sha256": observed["sha256"]},
                 "mihomo": {"path": "/mihomo", "sha256": runner.STOCK_HASHES["mihomo"]}}
        records = []
        for (case, role), number in source_process_roles("baseline").items():
            mihomo = role == "device-mihomo"
            identity = candidate if role == "domestic" else stock["mihomo" if mihomo else "sing-box"]
            for _ in range(number):
                records.append({"case": case, "role": role, "mihomo": mihomo,
                                "binary": identity["path"], "sha256": identity["sha256"], "parser_passed": True,
                                "launches": [] if role == "guarded" or case == runner.METHODS[15] else
                                [{"pid": 42, "ready": True, "sha256": identity["sha256"]}]})
        index = next(index for index, row in enumerate(records)
                     if row["case"] == observed["case"] and row["role"] == observed["role"])
        records[index] = deepcopy(observed)
        runner.verify_isolation("baseline", records, candidate, stock)
        for field, value in (("sha256", "0" * 64), ("binary", "/candidate"), ("parser_passed", False)):
            bad = deepcopy(records)
            bad[index][field] = value
            with self.subTest(field=field), self.assertRaises(runner.ExperimentError):
                runner.verify_isolation("baseline", bad, candidate, stock)
        for field, value in (("ready", False), ("sha256", "0" * 64), ("pid", None)):
            bad = deepcopy(records)
            bad[index]["launches"][0][field] = value
            with self.subTest(launch_field=field), self.assertRaises(runner.ExperimentError):
                runner.verify_isolation("baseline", bad, candidate, stock)
        bad = deepcopy(records)
        bad[index].update(mihomo=True, binary=stock["mihomo"]["path"], sha256=stock["mihomo"]["sha256"])
        bad[index]["launches"][0]["sha256"] = stock["mihomo"]["sha256"]
        with self.assertRaisesRegex(runner.ExperimentError, "core family"):
            runner.verify_isolation("baseline", bad, candidate, stock)

    def test_raw_port_classifier_also_rejects_later_observations(self):
        source = runner.ROOT / "tests/test_restricted_udp_preflight.py"
        statement = 'self.assertEqual(got_port, actual.port, "XUDP reply port differs from actual target")'
        line = next(index + 1 for index, text in enumerate(source.read_text().splitlines()) if text.strip() == statement)
        namespace = {}
        code = "\n" * (line - 2) + "def raw_echo(self, f, actual, got_port):\n    " + statement + "\n"
        exec(compile(code, str(source), "exec"), namespace)
        fixture = SimpleNamespace(upstream_port=43000, observations=[], label="raw-case")
        try:
            namespace["raw_echo"](self, fixture, SimpleNamespace(port=33000), 43000)
        except AssertionError:
            error = sys.exc_info()
        self.assertEqual(runner.initial_port_failure(error)["kind"], "raw-xudp-reply-port")
        fixture.observations.append({"kind": "rejection"})
        self.assertIsNone(runner.initial_port_failure(error))

    def test_supplemental_same_port_gate_cannot_skip(self):
        runner.assess_suite("patched", suite_summary("patched", dns=True), dns=True)
        result = suite_summary("patched", dns=True)
        result["nonpass"].append({"kind": "skip"})
        with self.assertRaises(runner.ExperimentError):
            runner.assess_suite("patched", result, dns=True)
        self.assertIn(runner.DNS_METHODS[0], runner.expected_cases("patched"))
        self.assertNotIn(runner.DNS_METHODS[0], runner.METHODS)

    def test_same_port_target_preserves_inherited_audit_and_only_binds_fixture(self):
        class Base:
            def audit(self):
                pass
            def close(self):
                pass
        target_class = runner.make_same_port_target(Base)
        self.assertIs(target_class.audit, Base.audit)
        self.assertIs(target_class.close, Base.close)
        server = SimpleNamespace(server_address=("127.0.0.7", 33000), socket=Mock(), serve_forever=Mock())
        with patch.object(runner.socketserver, "UDPServer", return_value=server) as bind:
            with patch.object(runner.threading, "Thread") as thread:
                target = target_class("127.0.0.7", 33000)
                self.assertEqual((target.host, target.port), ("127.0.0.7", 33000))
                self.assertEqual(target.expected, Counter())
                thread.return_value.start.assert_called_once_with()
                self.assertEqual(bind.call_args.args[0], ("127.0.0.7", 33000))
                for host, port in (("8.8.8.8", 33000), ("127.0.0.7", 53), ("127.0.0.7", True)):
                    with self.subTest(host=host, port=port), self.assertRaises(runner.ExperimentError):
                        target_class(host, port)
                self.assertEqual(bind.call_count, 1)

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(runner.main(["--phase", "baseline", "--candidate", "/unused",
                                              "--provenance", "/unused", "--output", directory]), 2)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
