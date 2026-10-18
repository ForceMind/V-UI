"""Pure, strict per-inbound routing semantics and private source manifests.

Manifests are privileged snapshots, not an API response. They contain no raw
settings or TLS material, but tags and source hashes are still private. Reading
an immutable manifest proves its internal semantics only: callers must compare
it with a fresh ``policy_plan`` before trusting current database rows.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import hashlib
import json
import re

MODES = frozenset(("inherit", "direct", "relay"))
_MANIFEST_KEYS = frozenset(("id", "mode", "effective", "enable", "core", "protocol", "port", "tag", "source_sha256"))
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class PolicyError(ValueError):
    """Stable codes and allowlisted coverage only; never include rejected data."""
    def __init__(self, code: str, coverage=()):
        self.code = code
        self.coverage = copy.deepcopy(list(coverage))
        super().__init__("Proxy policy rejected: " + code)


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise PolicyError("INVALID_INGRESS_SNAPSHOT") from None


def _digest(value):
    try:
        return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
    except UnicodeError:
        raise PolicyError("INVALID_INGRESS_SNAPSHOT") from None


def normalize_mode(value):
    """No trimming, coercion, nullable default, or compatibility fallback."""
    if type(value) is not str or value not in MODES:
        raise PolicyError("INVALID_ROUTING_MODE")
    return value


def row_mode(row):
    try:
        return normalize_mode(row.routing_mode)
    except AttributeError:
        raise PolicyError("MISSING_ROUTING_MODE") from None


def _flags(settings):
    if (type(settings) is not dict
            or any(type(settings.get(key)) is not bool for key in ("local_proxy_enabled", "relay_enabled"))):
        raise PolicyError("INVALID_PROXY_SETTINGS")
    return settings["local_proxy_enabled"], settings["relay_enabled"]


def _effective(mode, relay):
    return "relay" if mode == "relay" or mode == "inherit" and relay else "direct"


@dataclass(frozen=True)
class PolicyPlan:
    requires_upstream: bool
    relay_ids: tuple[int, ...]
    legacy: bool
    policy_version: int
    source_fingerprint: str
    _manifest_json: str = field(repr=False)

    @property
    def manifest(self):
        """A detached JSON-ready copy; mutating it cannot mutate this plan."""
        return json.loads(self._manifest_json)

    @property
    def fingerprint(self):
        return self.source_fingerprint


def plan_from_manifest(manifest, settings):
    """Strictly validate an already-committed private manifest, without I/O."""
    local, relay = _flags(settings)
    if type(manifest) is not list:
        raise PolicyError("INVALID_POLICY_MANIFEST")
    previous, relay_ids, legacy = 0, [], True
    for entry in manifest:
        if type(entry) is not dict or set(entry) != _MANIFEST_KEYS:
            raise PolicyError("INVALID_POLICY_MANIFEST")
        mode = normalize_mode(entry["mode"])
        if (type(entry["id"]) is not int or entry["id"] <= previous
                or type(entry["enable"]) is not bool
                or type(entry["port"]) is not int or not 1 <= entry["port"] <= 65535
                or any(type(entry[key]) is not str or not entry[key] for key in ("core", "protocol", "tag"))
                or type(entry["source_sha256"]) is not str or not _SHA256.fullmatch(entry["source_sha256"])
                or entry["effective"] != _effective(mode, relay)):
            raise PolicyError("INVALID_POLICY_MANIFEST")
        previous = entry["id"]
        legacy = legacy and mode == "inherit"
        if entry["enable"] and entry["effective"] == "relay":
            relay_ids.append(entry["id"])
    fingerprint = _digest({"policy_version": 2, "manifest": manifest, "settings": settings})
    return PolicyPlan(local or relay or bool(relay_ids), tuple(relay_ids), legacy,
                      1 if legacy else 2, fingerprint, _canonical(manifest))


def policy_plan(rows, settings):
    """Bind every row, including disabled drafts, to one effective policy.

    Counters (up/down) deliberately do not invalidate running policies. The
    configured quota, owner, remark, expiry and all protocol/TLS source values
    do: no source credential or policy change can reuse a checked fingerprint.
    """
    _local, relay = _flags(settings)
    manifest, ids = [], set()
    for row in rows:
        mode = row_mode(row)
        identifier, enable = getattr(row, "id", None), getattr(row, "enable", None)
        if type(identifier) is not int or identifier <= 0 or identifier in ids:
            raise PolicyError("INVALID_INGRESS_ID")
        if type(enable) is not bool:
            raise PolicyError("INVALID_INGRESS_SNAPSHOT")
        ids.add(identifier)
        core, protocol, port = (getattr(row, key, None) for key in ("core", "protocol", "port"))
        tag = getattr(row, "tag", None)
        if (type(core) is not str or not core or type(protocol) is not str or not protocol
                or type(port) is not int or not 1 <= port <= 65535
                or tag is not None and type(tag) is not str):
            raise PolicyError("INVALID_INGRESS_SNAPSHOT")
        tag = tag or f"{core}-{protocol}-{port}"
        source = {"id": identifier, "routing_mode": mode, "enable": enable,
                  "core": core, "protocol": protocol, "port": port, "tag": tag}
        # Preserve exact source values rather than normalizing corruption to an
        # empty settings object or dropping otherwise unknown protocol fields.
        for key in ("settings", "stream_settings", "expiry_time", "remark", "user_id", "total"):
            source[key] = getattr(row, key, None)
        manifest.append({"id": identifier, "mode": mode, "effective": _effective(mode, relay),
                         "enable": enable, "core": core, "protocol": protocol, "port": port,
                         "tag": tag, "source_sha256": _digest(source)})
    manifest.sort(key=lambda entry: entry["id"])
    return plan_from_manifest(manifest, settings)
