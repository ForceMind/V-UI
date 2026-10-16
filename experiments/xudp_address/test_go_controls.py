import json
from pathlib import Path
import tempfile
import unittest

from verify_go_controls import CHILDREN, FAILURE_MARKERS, GREEN, RED, verify


class GoControlEvidenceTests(unittest.TestCase):
    def fixture(self, root):
        for phase in ("baseline", "patched"):
            rows = [{"Test": name, "Action": "fail" if phase == "baseline" and name in RED else "pass"}
                    for name in sorted(RED | GREEN)]
            rows += [{"Test": name, "Action": "fail" if phase == "baseline" and name in FAILURE_MARKERS else "pass"}
                     for name in sorted(CHILDREN)]
            if phase == "baseline":
                rows += [{"Test": name, "Action": "output", "Output": "    fixture.go:99: " + marker + "\n"}
                         for name, marker in FAILURE_MARKERS.items()]
            rows.append({"Action": "fail" if phase == "baseline" else "pass", "Elapsed": .1})
            (root / (phase + "-go-tests.jsonl")).write_text("\n".join(json.dumps(x) for x in rows))
            (root / (phase + "-go-test-exit.json")).write_text(json.dumps({"returncode": 1 if phase == "baseline" else 0}))

    def test_exact_red_green_names_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            self.assertEqual(len(verify(root)["baseline"]["top_level"]), 5)

    def test_arbitrary_nonzero_missing_cases_or_skips_rejected(self):
        for mode in ("empty", "compile_error", "skip", "all_pass", "extra_case", "duplicate"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.fixture(root)
                path = root / "baseline-go-tests.jsonl"
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                if mode == "empty":
                    rows = []
                elif mode == "compile_error":
                    rows = [{"Action": "fail"}]
                elif mode == "skip":
                    rows[0]["Action"] = "skip"
                elif mode == "all_pass":
                    for row in rows:
                        row["Action"] = "pass"
                elif mode == "extra_case":
                    rows.insert(0, {"Test": "TestExperimentXUDPUnexpected", "Action": "fail"})
                else:
                    rows.insert(0, rows[0])
                path.write_text("\n".join(json.dumps(x) for x in rows))
                with self.assertRaises(ValueError):
                    verify(root)

    def test_patched_failure_or_wrong_exit_rejected(self):
        for mode in ("failure", "exit"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.fixture(root)
                if mode == "failure":
                    path = root / "patched-go-tests.jsonl"
                    rows = [json.loads(line) for line in path.read_text().splitlines()]
                    rows[0]["Action"] = "fail"
                    path.write_text("\n".join(json.dumps(x) for x in rows))
                else:
                    (root / "patched-go-test-exit.json").write_text('{"returncode": 2}')
                with self.assertRaises(ValueError):
                    verify(root)

    def test_same_top_names_cannot_hide_wrong_assertion_or_missing_subtest(self):
        for mode in ("arbitrary_message", "wrong_actual", "wrong_want", "missing_child", "extra_marker"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.fixture(root)
                path = root / "baseline-go-tests.jsonl"
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                output = next(row for row in rows if row.get("Action") == "output")
                if mode == "arbitrary_message":
                    output["Output"] = "fixture.go:99: unexpected EOF\n"
                elif mode == "wrong_actual":
                    output["Output"] = output["Output"].replace("127.0.0.2:24443", "127.0.0.2:12345")
                elif mode == "wrong_want":
                    output["Output"] = output["Output"].replace("want=127.0.0.3:12345", "want=127.0.0.3:9")
                elif mode == "missing_child":
                    rows.remove(next(row for row in rows if "/" in row.get("Test", "") and row["Action"] != "output"))
                else:
                    rows.append(output)
                path.write_text("\n".join(json.dumps(x) for x in rows))
                with self.assertRaises(ValueError):
                    verify(root)


if __name__ == "__main__":
    unittest.main()
