"""Offline synthetic data only; no sockets, cores, real credentials or network."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from app.services import mihomo_preview as preview
from app.services.upstream_profile import UpstreamValidationError

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "preview_mihomo.py"
SECRET = "synthetic-secret-never-render-this"


def node():
    return {"name": "synthetic-private-name", "type": "vless", "server": "private.example.test",
            "port": 443, "uuid": "11111111-1111-4111-8111-111111111111", "tls": True,
            "servername": "tls.example.test", "network": "tcp", "skip-cert-verify": False}


def document():
    return {"mode": "rule", "proxies": [node()],
            "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": [node()["name"]]}],
            "rules": ["DOMAIN-SUFFIX,example.test,DIRECT", "IP-CIDR,192.0.2.0/24,DIRECT", "MATCH,PROXY"]}


def encode(value):
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True)


class MihomoPreviewTests(unittest.TestCase):
    def report(self, value, baseline=None):
        return preview.preview_mihomo(encode(value), None if baseline is None else encode(baseline))

    def reject(self, text, code, baseline=False):
        with self.assertRaises(preview.MihomoPreviewError) as caught:
            preview.preview_mihomo("{}" if baseline else text, text if baseline else None)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.source, "baseline" if baseline else "candidate")
        self.assertNotIn(SECRET, str(caught.exception))

    def test_valid_inspection_never_grants_apply_or_runtime_compatibility(self):
        result = self.report(document())
        self.assertEqual(result["nodes"], {"count": 1, "existing_upstream_profile_expressible": 1})
        self.assertEqual(result["groups"]["recognized_select_shapes"], 1)
        self.assertEqual(result["rules"]["recognized_syntax"], 3)
        self.assertEqual(result["diagnostics"], [])
        self.assertIs(result["can_apply"], False)
        self.assertEqual(result["runtime_compatibility"], "not_tested")
        self.assertEqual(result["merge_equivalence"], "not_proven")
        serialized = json.dumps(result)
        for value in (node()["name"], node()["server"], node()["uuid"], "example.test"):
            self.assertNotIn(value, serialized)

    def test_stable_value_free_diff(self):
        old, new = document(), document()
        new["rules"] = list(reversed(new["rules"]))
        new["dns"] = {"nameserver": ["https://" + SECRET + ".test/dns-query"]}
        new["secret"] = SECRET
        old["ipv6"] = False
        result = self.report(new, old)
        changes = {x["section"]: x["change"] for x in result["sections"] + result["uninterpreted_sections"]}
        self.assertEqual(changes, {"mode": "same", "proxies": "same", "proxy-groups": "same", "rules": "changed", "dns": "added", "ipv6": "removed"})
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn('"secret"', json.dumps(result))
        self.assertEqual(result, self.report(new, old))

    def test_mapping_reordering_is_not_a_change_but_typed_values_are(self):
        new = document()
        old = deepcopy(new)
        old["proxies"][0] = dict(reversed(list(old["proxies"][0].items())))
        old["ipv6"], new["ipv6"] = False, 0
        result = self.report(new, old)
        self.assertEqual(result["sections"][1]["change"], "same")
        self.assertEqual(result["uninterpreted_sections"][0]["change"], "changed")

    def test_opaque_key_names_and_changes_are_withheld(self):
        result = self.report({SECRET: {"token": SECRET}, "authentication": [SECRET]}, {SECRET: "old"})
        self.assertEqual(result["withheld_section_count"], 2)
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn("authentication", json.dumps(result))
        self.assertEqual(result["comparison_scope"], "listed_sections_only_opaque_values_not_compared")

    def test_unsupported_node_is_not_declared_expressible(self):
        value = document()
        value["proxies"][0].update(type="trojan", password=SECRET)
        result = self.report(value)
        self.assertEqual(result["nodes"]["existing_upstream_profile_expressible"], 0)
        self.assertEqual(result["diagnostic_counts"], {"NODE_PROFILE_NOT_EXPRESSIBLE": 1})
        self.assertNotIn(SECRET, json.dumps(result))

    def test_group_cycles_and_dangling_references(self):
        value = {"proxy-groups": [{"name": "one", "type": "select", "proxies": ["two"]},
                                  {"name": "two", "type": "select", "proxies": ["one", SECRET]}]}
        result = self.report(value)
        self.assertEqual(result["diagnostic_counts"], {"GROUP_REFERENCE_CYCLE": 2, "REFERENCE_UNRESOLVED": 1})
        self.assertNotIn(SECRET, json.dumps(result))

    def test_group_self_cycle(self):
        result = self.report({"proxy-groups": [{"name": "self", "type": "select", "proxies": ["self"]}]})
        self.assertEqual(result["diagnostic_counts"], {"GROUP_REFERENCE_CYCLE": 1})

    def test_cycle_members_reached_through_a_previously_completed_branch(self):
        groups = [{"name": "a", "type": "select", "proxies": ["b", "c"]},
                  {"name": "b", "type": "select", "proxies": ["a"]},
                  {"name": "c", "type": "select", "proxies": ["b"]}]
        self.assertEqual(self.report({"proxy-groups": groups})["diagnostic_counts"], {"GROUP_REFERENCE_CYCLE": 3})

    def test_duplicate_names_cross_node_group_names_and_builtins(self):
        value = document()
        value["proxy-groups"][0]["name"] = node()["name"]
        result = self.report(value)
        self.assertEqual(result["diagnostic_counts"]["NAME_COLLISION"], 2)
        value = {"proxy-groups": [{"name": "DIRECT", "type": "select", "proxies": ["DIRECT"]}]}
        self.assertEqual(self.report(value)["diagnostic_counts"]["NAME_COLLISION"], 1)

    def test_invalid_group_shapes_are_explicit_not_fetched(self):
        for item in (None, {"name": "g", "type": "url-test", "url": "https://" + SECRET + ".test"},
                     {"name": "g", "type": "select", "use": [SECRET]},
                     {"name": "g", "type": "select", "proxies": []}):
            with self.subTest(item=item):
                result = self.report({"proxy-groups": [item]})
                self.assertEqual(result["groups"]["recognized_select_shapes"], 0)
                self.assertNotIn(SECRET, json.dumps(result))

    def test_duplicate_members(self):
        result = self.report({"proxy-groups": [{"name": "g", "type": "select", "proxies": ["DIRECT", "DIRECT"]}]})
        self.assertEqual(result["diagnostic_counts"], {"DUPLICATE_GROUP_MEMBER": 1})

    def test_rule_conflicts_duplicates_and_unreachable_rules(self):
        result = self.report({"rules": ["DOMAIN,x.test,DIRECT", "DOMAIN,x.test,REJECT",
                                       "DOMAIN,x.test,REJECT", "MATCH,DIRECT", "DOMAIN,y.test,DIRECT"]})
        self.assertEqual(result["diagnostic_counts"], {"RULE_TARGET_CONFLICT": 1, "DUPLICATE_RULE": 1, "RULE_AFTER_MATCH": 1})

    def test_rule_scope_is_strict_and_no_implicit_conversion(self):
        for rule in (None, "RULE-SET,remote,DIRECT", "DOMAIN,x.test,DIRECT,no-resolve", "DOMAIN,X.test,DIRECT",
                     "DOMAIN,https://x.test,DIRECT", "DOMAIN,*.x.test,DIRECT", "IP-CIDR,192.0.2.1/24,DIRECT",
                     "IP-CIDR6,192.0.2.0/24,DIRECT", "DOMAIN,x.test, DIRECT", "AND,((DOMAIN,x.test)),DIRECT"):
            with self.subTest(rule=rule):
                result = self.report({"rules": [rule]})
                self.assertEqual(result["rules"]["recognized_syntax"], 0)
                self.assertEqual(result["diagnostic_counts"], {"RULE_SHAPE_NOT_SUPPORTED": 1})

    def test_recognizes_canonical_ipv6_and_exact_domain(self):
        result = self.report({"rules": ["IP-CIDR6,2001:db8::/32,REJECT", "DOMAIN,x.test,DIRECT"]})
        self.assertEqual(result["rules"]["recognized_syntax"], 2)

    def test_unsupported_mode(self):
        for value in (None, False, 3, "UNKNOWN", []):
            self.assertEqual(self.report({"mode": value})["diagnostic_counts"], {"MODE_NOT_RECOGNIZED": 1})

    def test_missing_or_nonliteral_names(self):
        for value in ({}, {"name": False}, {"name": ""}, {"name": "a" * 129}):
            self.assertIn("NAME_REQUIRED", self.report({"proxies": [value]})["diagnostic_counts"])

    def test_diagnostics_are_bounded_but_complete_counts_remain(self):
        result = self.report({"rules": ["UNKNOWN," + SECRET] * 400})
        self.assertEqual(len(result["diagnostics"]), preview.MAX_ISSUES)
        self.assertEqual(result["diagnostics_omitted"], 400 - preview.MAX_ISSUES)
        self.assertEqual(result["diagnostic_counts"], {"RULE_SHAPE_NOT_SUPPORTED": 400})

    def test_duplicate_keys_at_any_depth(self):
        self.reject("mode: rule\nmode: direct", "DUPLICATE_KEY")
        self.reject("dns: {token: one, token: " + SECRET + "}", "DUPLICATE_KEY")

    def test_aliases_anchors_merge_and_objects_are_rejected(self):
        for text, code in [("a: &a [1]\nb: *a", "YAML_REFERENCE"), ("a: *a", "YAML_REFERENCE"),
                           ("a: {<<: {token: x}}", "YAML_MERGE"),
                           ("a: !!python/object/apply:os.system ['" + SECRET + "']", "YAML_TAG"),
                           ("%YAML 1.1\n---\na: 1", "YAML_TAG")]:
            with self.subTest(code=code):
                self.reject(text, code)

    def test_ambiguous_scalars_require_quotes_except_inherited_nonfinite_ban(self):
        for text in ("yes", "OFF", "True", "~", "Null", "01", "0x10", "1:30", ".nan", ".inf", "2026-10-25", "1e3"):
            with self.subTest(text=text):
                self.reject("unknown: " + text, "YAML_AMBIGUOUS_SCALAR")
                quoted = "unknown: " + json.dumps(text)
                if text in {".nan", ".inf"}:
                    # The unchanged shared loader rejects these spellings even
                    # when quoted. Do not weaken the existing import contract.
                    self.reject(quoted, "NONFINITE_NUMBER")
                else:
                    self.assertEqual(preview.preview_mihomo(quoted)["withheld_section_count"], 1)

    def test_non_string_keys_and_multiple_documents_are_rejected(self):
        self.reject("3: x", "INVALID_SHAPE")
        self.reject("{}\n---\n{}", "YAML_MULTIDOCUMENT")

    def test_empty_scalar_list_and_invalid_section_containers_are_rejected(self):
        for text in ("", "null", "[]", "secret", "rules: null", "proxies: {}", "proxy-groups: false"):
            self.reject(text, "INVALID_SHAPE")

    def test_invalid_utf8_input_types_and_size(self):
        self.reject(b"\xff", "INVALID_UTF8")
        self.reject("\ud800", "INVALID_UTF8")
        self.reject({}, "INVALID_INPUT")
        self.reject(b"x" * (preview.MAX_INPUT_BYTES + 1), "INPUT_LIMIT")
        self.reject("x" * (preview.MAX_INPUT_BYTES + 1), "INPUT_LIMIT")

    def test_depth_scalar_integer_and_section_limits(self):
        self.reject("a: " + "[" * 17 + "0" + "]" * 17, "DEPTH_LIMIT")
        self.reject("a: '" + "x" * 8193 + "'", "STRING_LIMIT")
        self.reject("a: 9223372036854775808", "INTEGER_LIMIT")
        self.reject(encode({"rules": ["MATCH,DIRECT"] * 2049}), "SECTION_LIMIT")
        self.reject(encode({"proxy-groups": [{} for _ in range(129)]}), "SECTION_LIMIT")
        self.reject(encode({"proxies": [{} for _ in range(129)]}), "CANDIDATE_LIMIT")

    def test_token_limit_and_baseline_failure_source(self):
        self.reject("a: [" + ",".join("0" for _ in range(17000)) + "]", "TOKEN_LIMIT")
        self.reject("secret: 1\nsecret: " + SECRET, "DUPLICATE_KEY", baseline=True)

    def test_invisible_characters_are_rejected_without_echo(self):
        self.reject('a: "' + SECRET + '\\u202e"', "INVALID_STRING")

    def test_controlled_worker_errors_do_not_leak_and_release_slot(self):
        for response in (b"not-json-" + SECRET.encode(), b"{}", b'{"ok":true,"report":{}}',
                         json.dumps({"ok": False, "code": [SECRET], "source": {SECRET: 1}}).encode()):
            with self.subTest(response=response), patch.object(preview.bounded, "_bounded_worker_io", return_value=response):
                with self.assertRaises(preview.MihomoPreviewError) as caught:
                    preview.preview_mihomo("{}")
                self.assertEqual(caught.exception.code, "PARSER_FAILED")
                self.assertNotIn(SECRET, str(caught.exception))
        self.assertEqual(self.report({})["diagnostics"], [])

    def test_worker_timeout_and_resource_failure_are_explicit(self):
        for code in ("PARSE_TIMEOUT", "PARSER_RESOURCE_LIMIT", "PARSER_UNAVAILABLE"):
            with patch.object(preview.bounded, "_bounded_worker_io", side_effect=UpstreamValidationError(code, SECRET)):
                with self.assertRaises(preview.MihomoPreviewError) as caught:
                    preview.preview_mihomo("{}")
                self.assertEqual(str(caught.exception), code)
        self.assertEqual(self.report({})["diagnostics"], [])

    def test_malformed_child_success_cannot_echo_values_or_unknown_keys(self):
        safe = self.report({})
        for mutate in (lambda value: value.update(secret=SECRET),
                       lambda value: value.update(scope=SECRET),
                       lambda value: value["diagnostic_counts"].update({SECRET: 1}),
                       lambda value: value.update(schema_version=True)):
            response = deepcopy(safe)
            mutate(response)
            with patch.object(preview.bounded, "_bounded_worker_io", return_value=json.dumps({"ok": True, "report": response}).encode()):
                with self.assertRaises(preview.MihomoPreviewError) as caught:
                    preview.preview_mihomo("{}")
                self.assertEqual(str(caught.exception), "PARSER_FAILED")

    def test_concurrent_limit(self):
        self.assertTrue(preview._SLOTS.acquire(blocking=False))
        self.assertTrue(preview._SLOTS.acquire(blocking=False))
        try:
            with self.assertRaises(preview.MihomoPreviewError) as caught:
                preview.preview_mihomo("{}")
            self.assertEqual(caught.exception.code, "PARSER_BUSY")
        finally:
            preview._SLOTS.release()
            preview._SLOTS.release()


class MihomoPreviewCLITests(unittest.TestCase):
    def run_cli(self, *arguments):
        result = subprocess.run([sys.executable, "-B", str(CLI), *map(str, arguments)],
                                capture_output=True, text=True, timeout=10)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        return result, json.loads(result.stdout)

    def test_cli_reports_without_changing_either_file(self):
        with tempfile.TemporaryDirectory() as directory:
            current, baseline = Path(directory) / "new.yml", Path(directory) / "old.yml"
            current.write_text(encode(document()))
            baseline.write_text("secret: " + SECRET + "\n")
            before = (current.read_bytes(), baseline.read_bytes())
            result, value = self.run_cli(current, "--baseline", baseline)
            self.assertEqual(result.returncode, 0)
            self.assertIs(value["ok"], True)
            self.assertIs(value["report"]["can_apply"], False)
            self.assertEqual(before, (current.read_bytes(), baseline.read_bytes()))
            self.assertEqual(set(Path(directory).iterdir()), {current, baseline})

    def test_cli_errors_never_echo_filename_or_unknown_arguments(self):
        for args in (("/missing/" + SECRET,), ("--" + SECRET,), ()):
            result, value = self.run_cli(*args)
            self.assertEqual(result.returncode, 2)
            self.assertIs(value["ok"], False)

    def test_cli_rejects_symlink_fifo_directory_and_oversize(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actual, link, fifo, huge = (root / name for name in ("actual", "link", "fifo", "huge"))
            actual.write_text("{}")
            link.symlink_to(actual)
            os.mkfifo(fifo)
            with huge.open("wb") as stream:
                stream.truncate(preview.MAX_INPUT_BYTES + 1)
            for path in (link, fifo, root, huge):
                with self.subTest(path=path):
                    result, value = self.run_cli(path)
                    self.assertEqual(result.returncode, 2)
                    self.assertIs(value["ok"], False)

    def test_cli_parse_error_identifies_baseline_not_source_text(self):
        with tempfile.TemporaryDirectory() as directory:
            current, old = Path(directory) / "current", Path(directory) / SECRET
            current.write_text("{}")
            old.write_text("unknown: &anchor [" + SECRET + "]")
            result, value = self.run_cli(current, "--baseline", old)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(value["error"], {"code": "YAML_REFERENCE", "source": "baseline"})

    def test_cli_allows_parent_directory_symlink_not_a_path_sandbox(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actual, link = root / "actual-directory", root / "linked-directory"
            actual.mkdir()
            source = actual / "config.yml"
            source.write_text("secret: " + SECRET + "\n")
            link.symlink_to(actual, target_is_directory=True)
            before = source.read_bytes()
            result, value = self.run_cli(link / source.name)
            self.assertEqual(result.returncode, 0)
            self.assertIs(value["ok"], True)
            self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
