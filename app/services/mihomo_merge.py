"""Offline, in-memory merge of two safely parsed Mihomo YAML documents.

This interface handles private data. It returns no public report and neither
persists inputs nor contacts providers. Callers keep the original bytes.
"""
from __future__ import annotations

import json

from app.services import upstream_import as bounded
from app.services.mihomo_preview import (
    MAX_GROUPS, MAX_RULES, MihomoPreviewError, _equal, _load_document, _rule_shape, _source_bytes,
)
from app.services.mihomo_routing import RESERVED_PROXY_NAMES
from app.services.upstream_profile import UpstreamValidationError


_NAMED_SECTIONS = ('proxies', 'proxy-groups')
# V-UI's exported groups may use these three names. They need definitions.
_EXPORT_GROUP_NAMES = {'PROXY', 'AUTO', 'FORCE_PROXY'}
_RESERVED_NAMES = frozenset(RESERVED_PROXY_NAMES - _EXPORT_GROUP_NAMES) | {'MATCH'}


class MihomoMergeError(ValueError):
    """Use only the fixed code for errors outside the private merge boundary."""

    def __init__(self, code, *, conflicts=()):
        super().__init__(code)
        self.code = code
        self.conflicts = conflicts


def _named_items(document):
    sections = {}
    seen = set(_RESERVED_NAMES)
    for section in _NAMED_SECTIONS:
        items = {}
        for item in document.get(section, ()):
            name = item.get('name') if type(item) is dict else None
            if type(name) is not str or not name or len(name) > 128:
                raise MihomoMergeError('MERGE_INVALID_NAME')
            if name in seen:
                raise MihomoMergeError('MERGE_NAME_COLLISION')
            seen.add(name)
            items[name] = item
        sections[section] = items
    return sections


def _check_references(document, named):
    groups = named['proxy-groups']
    available = named['proxies'].keys() | groups.keys() | {'DIRECT', 'REJECT'}
    graph = {}
    for name, group in groups.items():
        if 'proxies' not in group:
            continue
        members = group['proxies']
        if (type(members) is not list or any(type(member) is not str or member not in available for member in members)
                or len(set(members)) != len(members)):
            raise MihomoMergeError('MERGE_INVALID_REFERENCES')
        graph[name] = [member for member in members if member in groups]

    active, complete = set(), set()

    def visit(name):
        if name in active:
            raise MihomoMergeError('MERGE_INVALID_REFERENCES')
        if name in complete:
            return
        active.add(name)
        for member in graph.get(name, ()):
            visit(member)
        active.remove(name)
        complete.add(name)

    for name in graph:
        visit(name)

    rules = document.get('rules', ())
    for index, rule in enumerate(rules):
        parsed = _rule_shape(rule)
        if (parsed is None and type(rule) is str
                and rule.startswith(('IP-CIDR,', 'IP-CIDR6,')) and rule.endswith(',no-resolve')):
            parsed = _rule_shape(rule.removesuffix(',no-resolve'))
        if parsed is not None:
            kind, _, target = parsed
            if target not in available or (kind == 'MATCH' and index != len(rules) - 1):
                raise MihomoMergeError('MERGE_INVALID_REFERENCES')


def merge_mihomo(baseline, candidate, *, choices=None):
    """Keep baseline order, append candidate additions, and require conflict choices.

    Choice keys are private tuples of field names. A named item conflicts as a
    whole. Rules are a single ordered field, so callers must explicitly select
    a complete sequence when the documents differ. Missing keys mean additions,
    not deletions. Return a private merged document, or fixed-code errors.
    """
    left = _load_document(_source_bytes(baseline, 'baseline'), 'baseline')
    right = _load_document(_source_bytes(candidate, 'candidate'), 'candidate')
    left_named, right_named = _named_items(left), _named_items(right)
    if choices is None:
        choices = {}
    if (type(choices) is not dict or len(choices) > bounded.MAX_TOKENS
            or any(type(path) is not tuple or not path or len(path) > bounded.MAX_DEPTH
                   or any(type(part) is not str for part in path)
                   or type(side) is not str or side not in ('baseline', 'candidate')
                   for path, side in choices.items())):
        raise MihomoMergeError('MERGE_INVALID_CHOICES')
    conflicts, used = [], set()

    def choose(old, new, path):
        if _equal(old, new):
            return old
        if path not in choices:
            conflicts.append(path)
            return old
        used.add(path)
        return old if choices[path] == 'baseline' else new

    def fields(old, new, path=()):
        merged = dict(old)
        for key, value in new.items():
            location = (*path, key)
            if not path and key in _NAMED_SECTIONS:
                named = dict(left_named[key])
                for name, item in right_named[key].items():
                    named[name] = choose(named[name], item, (*location, name)) if name in named else item
                merged[key] = list(named.values())
            elif key not in merged:
                merged[key] = value
            elif type(merged[key]) is dict and type(value) is dict:
                merged[key] = fields(merged[key], value, location)
            else:
                merged[key] = choose(merged[key], value, location)
        return merged

    result = fields(left, right)
    if conflicts:
        raise MihomoMergeError('MERGE_CHOICES_REQUIRED', conflicts=tuple(conflicts))
    if used != choices.keys():
        raise MihomoMergeError('MERGE_UNUSED_CHOICES')
    named = _named_items(result)
    for section, limit in (('proxies', bounded.MAX_CANDIDATES), ('proxy-groups', MAX_GROUPS),
                           ('rules', MAX_RULES)):
        if len(result.get(section, ())) > limit:
            raise MihomoMergeError('MERGE_RESULT_LIMIT')
    try:
        bounded._validate_tree(result)
    except UpstreamValidationError:
        raise MihomoMergeError('MERGE_RESULT_LIMIT') from None
    if len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > bounded.MAX_INPUT_BYTES:
        raise MihomoMergeError('MERGE_RESULT_LIMIT')
    _check_references(result, named)
    return result


def serialize_mihomo_merge(baseline, candidate, *, choices=None) -> bytes:
    """Return private YAML bytes only if they can be safely re-imported unchanged."""
    import yaml

    merged = merge_mihomo(baseline, candidate, choices=choices)
    try:
        raw = yaml.safe_dump(merged, allow_unicode=True, sort_keys=False).encode('utf-8')
        reparsed = _load_document(_source_bytes(raw, 'pair'), 'pair')
    except (MihomoPreviewError, UnicodeError, yaml.YAMLError):
        raise MihomoMergeError('MERGE_SERIALIZATION_REJECTED') from None
    if not _equal(merged, reparsed):
        raise MihomoMergeError('MERGE_SERIALIZATION_CHANGED')
    return raw
