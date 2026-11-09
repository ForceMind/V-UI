"""Canonical private YAML plan records readable without a YAML parser.

These records describe already checked plans. They do not grant application
authority or replace checks of current revisions, network state and artifacts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
import re

from app.services.upstream_profile import RESERVED_ROUTING_TARGETS, UpstreamProfile, UpstreamValidationError, dns_name, literal_string


MAX_RECORD_BYTES = 2_000_000
_HEX = re.compile(r'[a-f0-9]{64}\Z')
_BUILTINS = frozenset({'DIRECT', 'REJECT'})
_RULES = frozenset({'DOMAIN', 'DOMAIN-SUFFIX', 'IP-CIDR', 'IP-CIDR6', 'MATCH'})


class PlanRecordError(ValueError):
    def __init__(self):
        super().__init__('YAML_PLAN_RECORD_INVALID')


@dataclass(frozen=True)
class StoredMihomoPlan:
    record_id: str
    _raw: bytes = field(repr=False)

    @property
    def value(self):
        return json.loads(self._raw)


def _require(condition):
    if not condition:
        raise PlanRecordError()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value)
        value[key] = item
    return value


def _integer(raw):
    _require(len(raw) <= 20)
    value = int(raw)
    _require(-(1 << 63) <= value <= (1 << 63) - 1)
    return value


def _invalid_number(_):
    raise PlanRecordError()


def decode_plan_record(raw: bytes) -> StoredMihomoPlan:
    """Validate strict JSON shape and resolved references using only stdlib models."""
    try:
        _require(type(raw) is bytes and 0 < len(raw) <= MAX_RECORD_BYTES)
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_float=_invalid_number,
                           parse_constant=_invalid_number, parse_int=_integer)
        fields = {'schema', 'kind', 'compiler', 'sources', 'upstreams', 'rules', 'source_rules', 'group_members', 'dns_upstream'}
        _require(type(value) is dict and set(value) == fields)
        _require(type(value['schema']) is int and value['schema'] == 1
                 and value['kind'] == 'mihomo-static-plan-v1' and value['compiler'] == 1)
        _require(type(value['compiler']) is int)
        sources = value['sources']
        _require(type(sources) is dict and set(sources) == {'baseline', 'candidate', 'merged'})
        _require(all(type(digest) is str and _HEX.fullmatch(digest) for digest in sources.values()))
        upstreams = value['upstreams']
        _require(type(upstreams) is list and len(upstreams) <= 128)
        names = set()
        for profile in upstreams:
            _require(type(profile) is dict)
            model = UpstreamProfile.from_dict(profile)
            _require(model.to_dict() == profile and model.name not in RESERVED_ROUTING_TARGETS | {'MATCH'}
                     and model.name not in names)
            names.add(model.name)
        available = names | _BUILTINS
        groups = value['group_members']
        _require(type(groups) is dict and len(groups) <= 128)
        for name, target in groups.items():
            literal_string(name, allow_empty=False, max_bytes=512)
            _require(len(name) <= 128 and name not in names | RESERVED_ROUTING_TARGETS | {'MATCH'}
                     and type(target) is str and target in available)
        dns = value['dns_upstream']
        _require(dns is None or type(dns) is str and dns in names)
        rules, source_rules = value['rules'], value['source_rules']
        _require(type(rules) is list and 1 <= len(rules) <= 2048)
        _require(type(source_rules) is list and len(source_rules) == len(rules))
        referenced_groups = set()
        for index, row in enumerate(rules):
            _require(type(row) is list and len(row) == 3 and all(type(part) is str for part in row))
            original = source_rules[index]
            _require(type(original) is list and len(original) == 3
                     and all(type(part) is str for part in original))
            kind, operand, target = row
            _require(original[:2] == row[:2])
            source_target = original[2]
            if source_target in groups:
                referenced_groups.add(source_target)
                _require(target == groups[source_target])
            else:
                _require(source_target == target)
            _require(kind in _RULES and target in available)
            if kind == 'MATCH':
                _require(index == len(rules) - 1 and operand == '')
            elif kind in ('DOMAIN', 'DOMAIN-SUFFIX'):
                dns_name(operand)
                _require(operand == operand.lower())
            else:
                network = ipaddress.ip_network(operand, strict=True)
                _require(network.version == (4 if kind == 'IP-CIDR' else 6) and str(network) == operand)
        _require(referenced_groups == groups.keys())
        _require(rules[-1][0] == 'MATCH' and raw == _canonical(value))
        return StoredMihomoPlan(hashlib.sha256(raw).hexdigest(), raw)
    except PlanRecordError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, UpstreamValidationError):
        raise PlanRecordError() from None


def encode_plan_record(plan, *, sources, group_members, dns_upstream) -> bytes:
    """Build immutable bytes; source hashes refer to separately stored private inputs."""
    _require(dict(plan.group_members) == group_members)
    value = {
        'schema': 1, 'kind': 'mihomo-static-plan-v1', 'compiler': 1,
        'sources': dict(sources), 'upstreams': [profile.to_dict() for profile in plan.upstreams],
        'rules': [list(rule) for rule in plan.rules],
        'source_rules': [list(rule) for rule in plan.source_rules], 'group_members': dict(group_members),
        'dns_upstream': dns_upstream,
    }
    try:
        raw = _canonical(value)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise PlanRecordError() from None
    decode_plan_record(raw)
    return raw
