"""Local, synthetic renderer/CLI checks; no cores, network or configuration writes."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.services import mihomo_preview as preview
from app.services import mihomo_preview_report as presentation
from test_mihomo_preview import CLI, SECRET, document, encode, node


class MihomoPreviewReportTests(unittest.TestCase):
    def report(self, value=None, baseline=None):
        return preview.preview_mihomo(encode(document() if value is None else value),
                                      None if baseline is None else encode(baseline))

    def test_no_diagnostics_never_becomes_compatibility_approval(self):
        report = self.report()
        before = deepcopy(report)
        text = presentation.render_preview_text(report)
        self.assertIn("不可应用", text)
        self.assertIn("未验证真实核心兼容性", text)
        self.assertIn("未证明合并等价", text)
        self.assertIn("共 0 条；展示 0 条；省略 0 条", text)
        self.assertIn("诊断为零也不代表兼容", text)
        self.assertIn("未提供基线，未比较", text)
        self.assertEqual(report, before)
        for value in (node()["name"], node()["server"], node()["uuid"], "example.test"):
            self.assertNotIn(value, text)

    def test_diagnostics_explain_actions_and_preserve_correct_positions(self):
        report = self.report({"rules": ["DOMAIN,x.test,DIRECT", "DOMAIN,x.test,REJECT", "MATCH,DIRECT", "MATCH,DIRECT"]})
        text = presentation.render_preview_text(report)
        self.assertIn("同一精确谓词指向不同目标", text)
        self.assertIn("检查预期优先顺序", text)
        self.assertIn("rules / 第 2 项（索引 1）：RULE_TARGET_CONFLICT", text)
        self.assertIn("不是 YAML 行号", text)
        self.assertIn("不是问题节点数", text)
        self.assertNotIn("x.test", text)

    def test_comparison_distinguishes_absent_removed_and_uninterpreted(self):
        report = self.report({"mode": "direct", "dns": {}, "secret": SECRET}, {"mode": "rule", "ipv6": True})
        text = presentation.render_preview_text(report)
        self.assertIn("mode：候选存在；结构有变化", text)
        self.assertIn("proxies：候选不存在；两份均无此区块", text)
        self.assertIn("dns：候选存在；新增；语义未解释", text)
        self.assertIn("ipv6：候选不存在；移除；语义未解释", text)
        self.assertIn("可见区块相同不代表整份配置相同", text)
        self.assertIn("隐藏的其他区块：1 个", text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("secret", text)

    def test_all_diagnostic_codes_have_fixed_explanations(self):
        self.assertEqual(set(presentation._DIAGNOSTICS), preview._DIAGNOSTIC_CODES)
        self.assertTrue(all(type(value) is str and value for value in presentation._DIAGNOSTICS.values()))

    def test_diagnostic_truncation_keeps_total_and_exact_display_limit(self):
        report = self.report({"rules": ["UNKNOWN," + SECRET] * 400})
        text = presentation.render_preview_text(report)
        self.assertIn("共 400 条；展示 256 条；省略 144 条", text)
        self.assertIn("第 256 项（索引 255）", text)
        self.assertNotIn("第 257 项", text)
        self.assertNotIn(SECRET, text)

    def assert_rejected(self, value):
        # Check both direct renderer input and the actual parent/worker boundary.
        with self.assertRaises(preview.MihomoPreviewError) as raised:
            presentation.render_preview_text(value)
        self.assertEqual(str(raised.exception), "PARSER_FAILED")
        wire = json.dumps({"ok": True, "report": value}).encode()
        with patch.object(preview.bounded, "_bounded_worker_io", return_value=wire):
            with self.assertRaises(preview.MihomoPreviewError) as raised:
                preview.preview_mihomo("{}")
        self.assertEqual(str(raised.exception), "PARSER_FAILED")

    def test_rejects_allowlisted_words_in_wrong_schema_positions(self):
        base = self.report()
        for key, replacement in (
            ("schema_version", True), ("scope", "same"), ("can_apply", 0),
            ("runtime_compatibility", "changed"), ("merge_equivalence", "same"),
            ("comparison_scope", "not_proven"), ("sections", {}),
            ("uninterpreted_sections", "rules"), ("nodes", []),
            ("groups", {}), ("rules", None), ("diagnostic_counts", []),
            ("diagnostics", {}), ("diagnostics_omitted", False),
            ("withheld_section_count", -1),
        ):
            with self.subTest(key=key):
                value = deepcopy(base)
                value[key] = replacement
                self.assert_rejected(value)

    def test_rejects_missing_extra_and_duplicate_section_rows(self):
        base = self.report()
        for rows in (base["sections"][:3], base["sections"] + base["sections"][:1],
                     list(reversed(base["sections"])), [{"section": "dns", "present": False, "change": "not_compared"}]):
            with self.subTest(rows=rows):
                value = deepcopy(base)
                value["sections"] = rows
                self.assert_rejected(value)
        for replacement in ({"section": "mode", "present": 1, "change": "same"},
                            {"section": "mode", "present": True, "change": "removed"},
                            {"section": "mode", "present": True, "change": "same"},
                            {"section": "mode", "present": True, "change": "not_compared", "count": 0}):
            value = deepcopy(base)
            value["sections"][0] = replacement
            self.assert_rejected(value)

    def test_rejects_invalid_uninterpreted_rows(self):
        base = self.report({"dns": {}, "tun": {}})
        for mutate in (
            lambda value: value["uninterpreted_sections"].reverse(),
            lambda value: value["uninterpreted_sections"].append(value["uninterpreted_sections"][0]),
            lambda value: value["uninterpreted_sections"][0].update(present=False),
            lambda value: value["uninterpreted_sections"][0].update(section="mode"),
        ):
            value = deepcopy(base)
            mutate(value)
            self.assert_rejected(value)

    def test_rejects_non_integer_out_of_range_and_inconsistent_counts(self):
        base = self.report()
        mutations = [
            lambda value: value["nodes"].update(count=True),
            lambda value: value["nodes"].update(count=129),
            lambda value: value["groups"].update(count=129),
            lambda value: value["rules"].update(count=2049),
            lambda value: value["nodes"].update(existing_upstream_profile_expressible=2),
            lambda value: value["nodes"].update(count=0),
            lambda value: value["groups"].update(recognized_select_shapes=-1),
            lambda value: value["rules"].update(ordering_and_overlapping_predicates="same"),
            lambda value: value["sections"][1].update(present=False),
            lambda value: value.update(diagnostics_omitted=1),
            lambda value: value["diagnostic_counts"].update(DUPLICATE_RULE=1),
            lambda value: value["diagnostic_counts"].update(DUPLICATE_RULE=0),
            lambda value: value["diagnostic_counts"].update(DUPLICATE_RULE=True),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                value = deepcopy(base)
                mutate(value)
                self.assert_rejected(value)

    def test_rejects_invalid_diagnostic_locations_and_histograms(self):
        base = self.report({"rules": ["UNKNOWN,x"]})
        mutations = [
            lambda value: value["diagnostics"][0].update(index=True),
            lambda value: value["diagnostics"][0].update(index=-1),
            lambda value: value["diagnostics"][0].update(index=1),
            lambda value: value["diagnostics"][0].update(section="proxies"),
            lambda value: value["diagnostics"][0].pop("index"),
            lambda value: value["diagnostics"][0].update(code="SECTION_NOT_INTERPRETED"),
            lambda value: value["diagnostic_counts"].update(RULE_SHAPE_NOT_SUPPORTED=2),
            lambda value: value["diagnostic_counts"].update(RULE_SHAPE_NOT_SUPPORTED=0, DUPLICATE_RULE=1),
            lambda value: value.update(diagnostics=[]),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                value = deepcopy(base)
                mutate(value)
                self.assert_rejected(value)
        for report in (self.report({"mode": 1}), self.report({"dns": {}}), self.report({SECRET: SECRET})):
            value = deepcopy(report)
            value["diagnostics"][0]["section"] = "rules"
            self.assert_rejected(value)
            value = deepcopy(report)
            value["diagnostics"][0]["index"] = 0
            self.assert_rejected(value)

    def test_missing_extra_or_arbitrary_strings_never_render(self):
        base = self.report()
        for mutate in (lambda value: value.pop("scope"), lambda value: value.update(secret=SECRET),
                       lambda value: value["sections"][0].update(section=SECRET)):
            value = deepcopy(base)
            mutate(value)
            self.assert_rejected(value)

    def test_many_supported_shapes_and_baseline_combinations_remain_valid(self):
        values = [{}, document(), {"dns": {}}, {"mode": False}, {"rules": [None]},
                  {"proxies": [{}]}, {"proxy-groups": [{}]}, {SECRET: SECRET}]
        for value in values:
            for baseline in [None, *values]:
                with self.subTest(value=value, baseline=baseline):
                    result = self.report(value, baseline)
                    self.assertTrue(presentation.render_preview_text(result).endswith("\n"))


class MihomoPreviewTextCLITests(unittest.TestCase):
    def run_cli(self, *arguments):
        result = subprocess.run([sys.executable, "-B", str(CLI), *map(str, arguments)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.stderr, "")
        self.assertNotIn(SECRET, result.stdout)
        return result

    def test_explicit_json_matches_default_output_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "input.yml"
            candidate.write_text(encode(document()))
            default = self.run_cli(candidate)
            explicit = self.run_cli(candidate, "--format", "json")
            self.assertEqual(default.returncode, 0)
            self.assertEqual(explicit.returncode, 0)
            self.assertEqual(default.stdout, explicit.stdout)
            self.assertFalse(json.loads(default.stdout)["report"]["can_apply"])

    def test_text_comparison_is_read_only_and_never_prints_source_or_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate, baseline = Path(directory) / SECRET, Path(directory) / "baseline.yml"
            candidate.write_text(encode({**document(), "secret": SECRET}))
            baseline.write_text("{}")
            before = (candidate.read_bytes(), baseline.read_bytes())
            result = self.run_cli(candidate, "--baseline", baseline, "--format", "text")
            self.assertEqual(result.returncode, 0)
            self.assertIn("Mihomo YAML 离线结构预览", result.stdout)
            self.assertIn("新增", result.stdout)
            self.assertNotIn(directory, result.stdout)
            self.assertEqual(before, (candidate.read_bytes(), baseline.read_bytes()))
            self.assertEqual(set(Path(directory).iterdir()), {candidate, baseline})

    def test_text_success_with_conflicts_still_exits_zero_not_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "input.yml"
            candidate.write_text(encode({"rules": ["MATCH,DIRECT", "MATCH,REJECT"]}))
            result = self.run_cli("--format", "text", candidate)
            self.assertEqual(result.returncode, 0)
            self.assertIn("RULE_TARGET_CONFLICT", result.stdout)
            self.assertIn("不可应用", result.stdout)

    def test_text_output_is_utf8_even_with_ascii_locale_and_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "input.yml"
            candidate.write_text("{}")
            environment = {**os.environ, "LC_ALL": "C", "PYTHONUTF8": "0",
                           "PYTHONCOERCECLOCALE": "0", "PYTHONIOENCODING": "ascii"}
            for format in ("text", "json"):
                with self.subTest(format=format):
                    result = subprocess.run([sys.executable, "-B", str(CLI), str(candidate), "--format", format],
                                            env=environment, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, b"")
                    if format == "text":
                        self.assertIn("不可应用", result.stdout.decode("utf-8"))
                    else:
                        self.assertFalse(json.loads(result.stdout.decode("ascii"))["report"]["can_apply"])

    def test_format_parse_and_file_failures_remain_fixed_json(self):
        for args in (("--format", SECRET, "/missing"), ("--format", "text", "/missing/" + SECRET),
                     ("--format", "text"), ("--format", "text", "--" + SECRET)):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(json.loads(result.stdout)["ok"])
        with tempfile.TemporaryDirectory() as directory:
            candidate, baseline = Path(directory) / "input", Path(directory) / "baseline"
            candidate.write_text("{}")
            baseline.write_text("mode: &anchor " + SECRET)
            result = self.run_cli(candidate, "--baseline", baseline, "--format", "text")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["error"], {"code": "YAML_REFERENCE", "source": "baseline"})


if __name__ == "__main__":
    unittest.main()
