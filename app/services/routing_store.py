"""Validated routing persistence. Corruption never silently selects default routing."""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from app.services.routing_validation import normalize_routing

ROUTING_FILE = Path(os.getenv('VUI_DATA_DIR', 'data')) / 'mihomo-routing.json'
MAX_BYTES = 2_000_000

class RoutingStorageError(ValueError):
    pass

class RoutingConflict(ValueError):
    pass

@contextmanager
def locked(exclusive: bool):
    ROUTING_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (ROUTING_FILE.parent / '.routing.lock').open('a+b') as handle:
        os.chmod(handle.name, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

def _read() -> dict:
    try:
        if not ROUTING_FILE.exists():
            settings, raw, source = normalize_routing({}), b'not-yet-saved', 'defaults'
        else:
            with ROUTING_FILE.open('rb') as handle:
                raw = handle.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError()
            settings, source = normalize_routing(json.loads(raw)), 'saved'
        return {'settings': settings, 'revision': hashlib.sha256(raw).hexdigest(), 'source': source}
    except (OSError, ValueError, TypeError, UnicodeError):
        raise RoutingStorageError('Saved routing is unreadable; restore a verified backup. No defaults were applied.') from None

def read_snapshot() -> dict:
    try:
        with locked(False):
            return _read()
    except OSError:
        raise RoutingStorageError('Routing storage is unavailable') from None

def load_routing() -> dict:
    return read_snapshot()['settings']

def save_routing(payload: dict, expected_revision: str) -> dict:
    settings = normalize_routing(payload)
    raw = json.dumps(settings, ensure_ascii=False, separators=(',', ':'), indent=2).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError('Routing settings exceed the size limit')
    try:
        with locked(True):
            old = _read()
            if expected_revision != old['revision']:
                raise RoutingConflict('Saved routing changed in another session; reload before saving')
            fd, name = tempfile.mkstemp(prefix='.routing-stage-', dir=ROUTING_FILE.parent)
            try:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(name, ROUTING_FILE)
                directory = os.open(ROUTING_FILE.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
            return _read()
    except OSError:
        # After an I/O error the caller must reload, not assume a commit succeeded.
        raise RoutingStorageError('Routing write could not be confirmed; reload to verify the saved revision') from None
