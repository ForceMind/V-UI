"""Offline, value-free structural inspection; never an importer or core check.

Both documents are parsed in one resource-limited child. Only fixed vocabulary,
counts and zero-based positions leave it. No source values, names, fingerprints,
converted settings or executable configuration are returned.
"""
from __future__ import annotations

from collections import Counter
import ipaddress
import json
from pathlib import Path
import re
import struct
import sys
import threading

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services import upstream_import as bounded
from app.services.upstream_profile import UpstreamValidationError
from app.services.routing_validation import parse_routing_target

MAX_INPUT_BYTES = bounded.MAX_INPUT_BYTES
MAX_RULES = 2048
MAX_GROUPS = 128
MAX_ISSUES = 256
_NO_BASELINE = (1 << 32) - 1
_SLOTS = threading.BoundedSemaphore(2)
_INSPECTED = ("mode", "proxies", "proxy-groups", "rules")
# These labels are constants, never copied from caller-supplied keys.
_OTHER_SECTIONS = ("dns", "tun", "hosts", "profile", "sniffer", "proxy-providers",
                   "rule-providers", "port", "socks-port", "mixed-port", "redir-port",
                   "tproxy-port", "allow-lan", "bind-address", "ipv6", "log-level")
_BUILTINS = frozenset({"DIRECT", "REJECT"})
_ERROR_CODES = frozenset({
    "INVALID_INPUT", "INPUT_LIMIT", "INVALID_UTF8", "INVALID_SHAPE", "INVALID_STRING", "TOKEN_LIMIT",
    "DEPTH_LIMIT", "CANDIDATE_LIMIT", "STRING_LIMIT", "INTEGER_LIMIT", "NUMBER_LIMIT",
    "NONFINITE_NUMBER", "UNSUPPORTED_VALUE", "YAML_REFERENCE", "YAML_TAG", "YAML_MERGE",
    "DUPLICATE_KEY", "YAML_MULTIDOCUMENT", "YAML_AMBIGUOUS_SCALAR", "PARSE_SYNTAX",
    "PARSER_RESOURCE_LIMIT", "PARSER_LIMIT_UNAVAILABLE", "PARSE_TIMEOUT",
    "PARSER_UNAVAILABLE", "PARSER_FAILED", "PARSER_BUSY", "SECTION_LIMIT",
})
_CANONICAL_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
_DIAGNOSTIC_CODES = frozenset({
    "MODE_NOT_RECOGNIZED", "NAME_REQUIRED", "NAME_COLLISION", "NODE_PROFILE_NOT_EXPRESSIBLE",
    "GROUP_SHAPE_NOT_SUPPORTED", "GROUP_MEMBERS_INVALID", "DUPLICATE_GROUP_MEMBER",
    "REFERENCE_UNRESOLVED", "GROUP_REFERENCE_CYCLE", "RULE_AFTER_MATCH", "RULE_SHAPE_NOT_SUPPORTED",
    "DUPLICATE_RULE", "RULE_TARGET_CONFLICT", "SECTION_NOT_INTERPRETED", "OPAQUE_SECTIONS_WITHHELD",
})
_REPORT_KEYS = frozenset({
    "schema_version", "scope", "can_apply", "runtime_compatibility", "merge_equivalence",
    "comparison_scope", "sections", "uninterpreted_sections", "withheld_section_count", "nodes",
    "groups", "rules", "diagnostic_counts", "diagnostics", "diagnostics_omitted",
})
_PUBLIC_WORDS = _REPORT_KEYS | _DIAGNOSTIC_CODES | frozenset(_INSPECTED + _OTHER_SECTIONS) | frozenset({
    "offline_structural_preview", "not_tested", "not_proven", "listed_sections_only_opaque_values_not_compared",
    "section", "present", "change", "count", "existing_upstream_profile_expressible", "recognized_select_shapes",
    "recognized_syntax", "ordering_and_overlapping_predicates", "not_equivalence_checked", "code", "index",
    "opaque", "not_compared", "removed", "absent", "added", "same", "changed",
})


class MihomoPreviewError(ValueError):
    """Only allowlisted codes and source labels can cross the parser boundary."""

    def __init__(self, code: str, source: str = "pair"):
        self.code = code if type(code) is str and code in _ERROR_CODES else "PARSER_FAILED"
        self.source = source if type(source) is str and source in {"candidate", "baseline", "pair"} else "pair"
        super().__init__(self.code)


def _source_bytes(value, source):
    try:
        return bounded._input_bytes(value)
    except UpstreamValidationError as error:
        raise MihomoPreviewError(error.code, source) from None


def preview_mihomo(candidate: str | bytes, baseline: str | bytes | None = None) -> dict:
    """Inspect YAML and optionally compare it with another YAML, without writes.

    A successful result is NOT compatibility, conversion or apply approval.
    Differences are structural, preserve list order, and contain no source values.
    """
    candidate = _source_bytes(candidate, "candidate")
    old = b"" if baseline is None else _source_bytes(baseline, "baseline")
    wire = struct.pack("!II", len(candidate), _NO_BASELINE if baseline is None else len(old)) + candidate + old
    if not _SLOTS.acquire(blocking=False):
        raise MihomoPreviewError("PARSER_BUSY")
    try:
        command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--worker"]
        try:
            response = json.loads(bounded._bounded_worker_io(command, wire))
        except UpstreamValidationError as error:
            raise MihomoPreviewError(error.code) from None
        except (ValueError, UnicodeError):
            raise MihomoPreviewError("PARSER_FAILED") from None
        if type(response) is not dict or type(response.get("ok")) is not bool:
            raise MihomoPreviewError("PARSER_FAILED")
        if not response["ok"]:
            raise MihomoPreviewError(response.get("code"), response.get("source"))
        report = response.get("report")
        if (type(report) is not dict or set(report) != _REPORT_KEYS
                or type(report.get("schema_version")) is not int or report["schema_version"] != 1
                or report.get("can_apply") is not False):
            raise MihomoPreviewError("PARSER_FAILED")
        _validate_public_value(report)
        return report
    finally:
        _SLOTS.release()


def _validate_public_value(value, depth=0):
    """Defense in depth: even a malformed child response cannot echo input."""
    if depth > 6:
        raise MihomoPreviewError("PARSER_FAILED")
    if type(value) is str and value in _PUBLIC_WORDS or type(value) is bool:
        return
    if type(value) is int and 0 <= value <= bounded.MAX_TOKENS:
        return
    if type(value) is dict and len(value) <= len(_PUBLIC_WORDS):
        for key, child in value.items():
            if type(key) is not str or key not in _PUBLIC_WORDS:
                raise MihomoPreviewError("PARSER_FAILED")
            _validate_public_value(child, depth + 1)
        return
    if type(value) is list and len(value) <= MAX_ISSUES:
        for child in value:
            _validate_public_value(child, depth + 1)
        return
    raise MihomoPreviewError("PARSER_FAILED")


def _load_document(raw: bytes, source: str):
    import yaml
    from yaml import tokens

    try:
        text = raw.decode("utf-8", errors="strict")
        # Reject YAML 1.1's surprising implicit booleans, timestamps and non-JSON
        # numeric spellings, rather than silently choosing a different meaning.
        for count, token in enumerate(yaml.scan(text, Loader=yaml.SafeLoader), 1):
            if count > bounded.MAX_TOKENS:
                raise MihomoPreviewError("TOKEN_LIMIT", source)
            if isinstance(token, tokens.ScalarToken) and token.plain:
                resolvers = yaml.SafeLoader.yaml_implicit_resolvers.get(token.value[:1], [])
                tag = next((tag for tag, regex in resolvers if regex.match(token.value)), "")
                suffix = tag.rsplit(":", 1)[-1]
                ambiguous = (suffix == "bool" and token.value not in {"true", "false"}
                             or suffix == "null" and token.value != "null"
                             or suffix == "timestamp"
                             or suffix in {"int", "float"} and not _CANONICAL_NUMBER.fullmatch(token.value)
                             or suffix not in {"int", "float"} and _CANONICAL_NUMBER.fullmatch(token.value))
                if ambiguous:
                    raise MihomoPreviewError("YAML_AMBIGUOUS_SCALAR", source)
        value = bounded._load_yaml(text)
        bounded._validate_tree(value)
        if type(value) is not dict:
            raise MihomoPreviewError("INVALID_SHAPE", source)
        for field, limit in (("proxies", 128), ("proxy-groups", MAX_GROUPS), ("rules", MAX_RULES)):
            if field in value and type(value[field]) is not list:
                raise MihomoPreviewError("INVALID_SHAPE", source)
            if len(value.get(field, [])) > limit:
                raise MihomoPreviewError("SECTION_LIMIT", source)
        return value
    except MihomoPreviewError:
        raise
    except UpstreamValidationError as error:
        raise MihomoPreviewError(error.code, source) from None
    except UnicodeError:
        raise MihomoPreviewError("INVALID_UTF8", source) from None
    except yaml.YAMLError:
        raise MihomoPreviewError("PARSE_SYNTAX", source) from None


def _equal(left, right):
    """Typed comparison: false and 0 are different; mapping order is not a diff."""
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_equal(left[k], right[k]) for k in left)
    if type(left) is list:
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _change(field, document, baseline):
    if baseline is None:
        return "not_compared"
    if field not in document:
        return "removed" if field in baseline else "absent"
    if field not in baseline:
        return "added"
    return "same" if _equal(document[field], baseline[field]) else "changed"


def _inspect(document, baseline):
    issues, totals = [], Counter()

    def issue(code, section, index=None):
        totals[code] += 1
        if len(issues) < MAX_ISSUES:
            row = {"code": code, "section": section}
            if index is not None:
                row["index"] = index
            issues.append(row)

    sections = [{"section": key, "present": key in document,
                 "change": _change(key, document, baseline)} for key in _INSPECTED]
    modes = document.get("mode")
    if "mode" in document and (type(modes) is not str or modes not in {"rule", "global", "direct"}):
        issue("MODE_NOT_RECOGNIZED", "mode")
    nodes, groups, rules = (document.get(key, []) for key in _INSPECTED[1:])
    names, node_names, group_names = Counter(), [], []
    for section, values, collected in (("proxies", nodes, node_names), ("proxy-groups", groups, group_names)):
        for index, item in enumerate(values):
            name = item.get("name") if type(item) is dict else None
            if type(name) is not str or not name or len(name) > 128:
                issue("NAME_REQUIRED", section, index)
                name = None
            else:
                names[name] += 1
            collected.append(name)
    for section, values in (("proxies", node_names), ("proxy-groups", group_names)):
        for index, name in enumerate(values):
            if name is not None and (names[name] != 1 or name in _BUILTINS):
                issue("NAME_COLLISION", section, index)
    usable_names = {name for name, count in names.items() if count == 1 and name not in _BUILTINS}
    compatible_nodes = 0
    for index, node in enumerate(nodes):
        try:
            bounded._clash_profile(node)
        except UpstreamValidationError:
            issue("NODE_PROFILE_NOT_EXPRESSIBLE", "proxies", index)
        else:
            compatible_nodes += 1

    graph = {}
    recognized_groups = 0
    for index, group in enumerate(groups):
        if type(group) is not dict or set(group) - {"name", "type", "proxies"} or group.get("type") != "select":
            issue("GROUP_SHAPE_NOT_SUPPORTED", "proxy-groups", index)
            continue
        members = group.get("proxies")
        if type(members) is not list or not members or len(members) > 128 or any(type(x) is not str or not x for x in members):
            issue("GROUP_MEMBERS_INVALID", "proxy-groups", index)
            continue
        recognized_groups += 1
        if len(set(members)) != len(members):
            issue("DUPLICATE_GROUP_MEMBER", "proxy-groups", index)
        for target in members:
            if target not in usable_names and target not in _BUILTINS:
                issue("REFERENCE_UNRESOLVED", "proxy-groups", index)
        name = group_names[index]
        if name in usable_names:
            graph[name] = members

    # At most 128 group vertices; no provider/network resolution is performed.
    indices, low, stack, active, cyclic = {}, {}, [], set(), set()
    def visit(name):
        indices[name] = low[name] = len(indices)
        stack.append(name)
        active.add(name)
        for target in graph[name]:
            if target not in graph:
                continue
            if target not in indices:
                visit(target)
                low[name] = min(low[name], low[target])
            elif target in active:
                low[name] = min(low[name], indices[target])
        if low[name] == indices[name]:
            component = []
            while True:
                member = stack.pop()
                active.remove(member)
                component.append(member)
                if member == name:
                    break
            if len(component) > 1 or name in graph[name]:
                cyclic.update(component)
    for name in graph:
        if name not in indices:
            visit(name)
    for index, name in enumerate(group_names):
        if name in cyclic:
            issue("GROUP_REFERENCE_CYCLE", "proxy-groups", index)

    recognized_rules, seen, match_seen = 0, {}, False
    for index, rule in enumerate(rules):
        if match_seen:
            issue("RULE_AFTER_MATCH", "rules", index)
        parsed = _rule_shape(rule)
        if parsed is None:
            issue("RULE_SHAPE_NOT_SUPPORTED", "rules", index)
            continue
        kind, operand, destination = parsed
        recognized_rules += 1
        match_seen |= kind == "MATCH"
        if destination not in usable_names and destination not in _BUILTINS:
            issue("REFERENCE_UNRESOLVED", "rules", index)
        key = (kind, operand)
        if key in seen:
            issue("DUPLICATE_RULE" if seen[key] == destination else "RULE_TARGET_CONFLICT", "rules", index)
        seen[key] = destination

    other = []
    for key in _OTHER_SECTIONS:
        if key in document or baseline is not None and key in baseline:
            other.append({"section": key, "present": key in document, "change": _change(key, document, baseline)})
        if key in document:
            issue("SECTION_NOT_INTERPRETED", key)
    # Opaque/unknown keys (including credential and controller fields) are not
    # echoed, fingerprinted, compared or used as diagnostic paths.
    withheld = sum(key not in _INSPECTED + _OTHER_SECTIONS for key in document)
    if withheld:
        issue("OPAQUE_SECTIONS_WITHHELD", "opaque")
    return {
        "schema_version": 1, "scope": "offline_structural_preview", "can_apply": False,
        "runtime_compatibility": "not_tested", "merge_equivalence": "not_proven",
        "comparison_scope": "listed_sections_only_opaque_values_not_compared",
        "sections": sections, "uninterpreted_sections": other, "withheld_section_count": withheld,
        "nodes": {"count": len(nodes), "existing_upstream_profile_expressible": compatible_nodes},
        "groups": {"count": len(groups), "recognized_select_shapes": recognized_groups},
        "rules": {"count": len(rules), "recognized_syntax": recognized_rules,
                  "ordering_and_overlapping_predicates": "not_equivalence_checked"},
        "diagnostic_counts": dict(sorted(totals.items())), "diagnostics": issues,
        "diagnostics_omitted": sum(totals.values()) - len(issues),
    }


def _rule_shape(value):
    if type(value) is not str:
        return None
    parts = value.split(",")
    if any(not part or part.strip() != part for part in parts):
        return None
    if len(parts) == 2 and parts[0] == "MATCH":
        return "MATCH", "", parts[1]
    if len(parts) != 3:
        return None
    kind, operand, target = parts
    if kind in {"DOMAIN", "DOMAIN-SUFFIX"}:
        parsed = parse_routing_target(operand)
        # URLs, wildcards, ports and case-normalization are not silently merged.
        if not parsed or parsed["kind"] != "domain" or parsed["value"] != operand:
            return None
    elif kind in {"IP-CIDR", "IP-CIDR6"}:
        try:
            network = ipaddress.ip_network(operand, strict=True)
        except ValueError:
            return None
        if network.version != (4 if kind == "IP-CIDR" else 6) or str(network) != operand:
            return None
    else:
        return None
    return kind, operand, target


def _worker_main():
    try:
        bounded._set_worker_limits()
        header = sys.stdin.buffer.read(8)
        if len(header) != 8:
            raise MihomoPreviewError("INVALID_INPUT")
        new_size, old_size = struct.unpack("!II", header)
        if new_size > MAX_INPUT_BYTES or old_size != _NO_BASELINE and old_size > MAX_INPUT_BYTES:
            raise MihomoPreviewError("INPUT_LIMIT")
        expected = new_size + (0 if old_size == _NO_BASELINE else old_size)
        raw = sys.stdin.buffer.read(expected + 1)
        if len(raw) != expected:
            raise MihomoPreviewError("INVALID_INPUT")
        candidate = _load_document(raw[:new_size], "candidate")
        baseline = None if old_size == _NO_BASELINE else _load_document(raw[new_size:], "baseline")
        response = {"ok": True, "report": _inspect(candidate, baseline)}
    except MihomoPreviewError as error:
        response = {"ok": False, "code": error.code, "source": error.source}
    except UpstreamValidationError as error:
        response = {"ok": False, "code": MihomoPreviewError(error.code).code, "source": "pair"}
    except (MemoryError, RecursionError):
        response = {"ok": False, "code": "PARSER_RESOURCE_LIMIT", "source": "pair"}
    except Exception:
        # Never stringify a parser exception, source document or traceback.
        response = {"ok": False, "code": "PARSER_FAILED", "source": "pair"}
    sys.stdout.write(json.dumps(response, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit(64)
    raise SystemExit(_worker_main())
