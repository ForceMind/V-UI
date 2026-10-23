"""Opaque optimistic precondition for selecting current inbound instances.

This is a full inventory snapshot, not a secret, protocol credential, runtime
revision, or replacement for the selected nodes' authorization checks.
"""
from __future__ import annotations

import hashlib
import json
import re

NODE_REVISION_HEADER = "X-VUI-Node-Revision"
NODE_SELECTION_CHANGED = "Subscription node selection changed; refresh nodes and select them again"
INSTANCE_RE = re.compile(r"[0-9a-f]{32}\Z")
REVISION_RE = re.compile(r"[0-9a-f]{64}\Z")


def node_selection_revision(rows) -> str:
    # Unrelated malformed identities do not prevent selecting valid nodes.
    # Their fixed marker carries no corrupt/raw contents into the response.
    pairs = sorted((row.id, row.instance_id if type(row.instance_id) is str
        and INSTANCE_RE.fullmatch(row.instance_id) else "<invalid>") for row in rows)
    snapshot = json.dumps(pairs, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(b"vui:subscription-node-selection:v1\0" + snapshot).hexdigest()
