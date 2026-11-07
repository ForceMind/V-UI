"""Non-secret mutation condition for one node instance, not its configuration."""
from __future__ import annotations

import hashlib
import json
import re

INSTANCE_RE = re.compile(r"[0-9a-f]{32}\Z")
REVISION_RE = re.compile(r"[0-9a-f]{64}\Z")
INSTANCE_CHANGED = "Inbound instance changed; refresh nodes and reopen or confirm again"


def mutation_revision(item) -> str | None:
    identifier, instance = getattr(item, "id", None), getattr(item, "instance_id", None)
    if (type(identifier) is not int or identifier <= 0 or type(instance) is not str
            or not INSTANCE_RE.fullmatch(instance)):
        # Never turn a missing/corrupt identity into a shared usable condition.
        return None
    value = json.dumps([identifier, instance], separators=(",", ":")).encode("ascii")
    return hashlib.sha256(b"vui:inbound-instance:v1\0" + value).hexdigest()


def content_revision(item) -> str | None:
    """Optional full-edit CAS, salted by the undisclosed instance identity.

    The original mutation_revision remains an identity-only contract. This
    separate token detects same-instance edits, including private TLS material
    and credential changes, without returning those fields or counting traffic.
    """
    if mutation_revision(item) is None:
        return None
    fields = ('id', 'instance_id', 'user_id', 'core', 'protocol', 'remark', 'port',
              'enable', 'expiry_time', 'total', 'tag', 'routing_mode', 'settings', 'stream_settings')
    try:
        raw = json.dumps({key: getattr(item, key, None) for key in fields}, sort_keys=True,
                         separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError):
        return None
    return hashlib.sha256(b'vui:inbound-content:v1\0' + raw).hexdigest()
