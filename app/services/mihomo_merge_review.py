"""Private merge review using source positions and explicit indexed choices."""
from __future__ import annotations

import hashlib
import hmac
import json

from app.services.mihomo_merge import MihomoMergeError, _NAMED_SECTIONS, merge_mihomo, serialize_mihomo_merge
from app.services.mihomo_preview import _source_bytes


MAX_REVIEW_CONFLICTS = 256
_SECTIONS = frozenset({'proxies', 'proxy-groups', 'rules', 'mode'})


def _field(node, name):
    for key, value in node.value:
        if key.value == name:
            return value
    raise MihomoMergeError('MERGE_REVIEW_INVALID')


def _position(document, path):
    node = _field(document, path[0])
    if path[0] in _NAMED_SECTIONS:
        for item in node.value:
            if _field(item, 'name').value == path[1]:
                node = item
                break
        else:
            raise MihomoMergeError('MERGE_REVIEW_INVALID')
    else:
        for key in path[1:]:
            node = _field(node, key)
    # One-based text coordinates, with columns measured in Unicode code points.
    return node.start_mark.line + 1, node.start_mark.column + 1


def _review(baseline, candidate):
    old = _source_bytes(baseline, 'baseline')
    new = _source_bytes(candidate, 'candidate')
    fingerprint = hashlib.sha256(b'vui-mihomo-merge-review-v1\0')
    for raw in (old, new):
        fingerprint.update(len(raw).to_bytes(4, 'big'))
        fingerprint.update(raw)
    try:
        merge_mihomo(old, new)
    except MihomoMergeError as error:
        if error.code != 'MERGE_CHOICES_REQUIRED':
            raise
        paths = error.conflicts
    else:
        paths = ()
    if len(paths) > MAX_REVIEW_CONFLICTS:
        raise MihomoMergeError('MERGE_REVIEW_LIMIT')
    # Include order and path identities so a worker upgrade cannot silently
    # reinterpret selections for the same documents.
    fingerprint.update(json.dumps(paths, ensure_ascii=True, separators=(',', ':')).encode('ascii'))
    return old, new, fingerprint.hexdigest(), paths


def review_mihomo_merge(baseline, candidate):
    """Return only fixed categories, positions and a private input fingerprint."""
    import yaml

    old, new, fingerprint, paths = _review(baseline, candidate)
    rows = []
    if paths:
        # Both inputs have already passed the safe, bounded loader. Compose
        # solely to recover marks; no source names or values leave this step.
        old_nodes = yaml.compose(old.decode('utf-8'), Loader=yaml.SafeLoader)
        new_nodes = yaml.compose(new.decode('utf-8'), Loader=yaml.SafeLoader)
        for index, path in enumerate(paths):
            section = path[0] if path[0] in _SECTIONS else 'other'
            rows.append({'index': index, 'section': section,
                         'baseline': list(_position(old_nodes, path)), 'candidate': list(_position(new_nodes, path))})
    return {'fingerprint': fingerprint, 'conflicts': rows}


def serialize_reviewed_mihomo_merge(baseline, candidate, fingerprint, selections):
    """Recompute conflict identities before applying each indexed choice."""
    old, new, expected, paths = _review(baseline, candidate)
    if (type(fingerprint) is not str or len(fingerprint) != 64 or not fingerprint.isascii()
            or not hmac.compare_digest(fingerprint, expected)):
        raise MihomoMergeError('MERGE_REVIEW_STALE')
    if (type(selections) is not list or len(selections) != len(paths)
            or any(type(side) is not str or side not in ('baseline', 'candidate') for side in selections)):
        raise MihomoMergeError('MERGE_INVALID_CHOICES')
    return serialize_mihomo_merge(old, new, choices=dict(zip(paths, selections)))
