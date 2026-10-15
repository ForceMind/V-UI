"""Private, versioned proxy drafts and commit-last applied state.

This module never starts a core, edits the inbound database, or chooses a direct
fallback. ``load_*`` are internal, credential-bearing interfaces; only
``read_snapshot`` and mutation return values are suitable for ordinary APIs.
An interrupted apply must be reconciled by the runtime, not guessed from drafts.

History is retained without pruning. Each state/object/intent has a finite
2,000,000-byte bound; an intent holds both old and new state, so admission may
fill before the standalone state reaches that size. Capacity is checked before
writes and reserves both terminal transaction outcomes. Exhaustion rejects new
changes without stopping a valid committed runtime or altering retained history.
Archival/compaction is a separate maintenance operation, not implicit cleanup.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import threading
import uuid

from app.data_owner import DataOwnerError, data_owner


MAX_BYTES = 2_000_000
MAX_UPSTREAMS = 128
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"up_[0-9a-f]{32}\Z")
_TX = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_SECRET = re.compile(r"sec_[0-9a-f]{32}\Z")
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_FLAGS = ("local_proxy_enabled", "relay_enabled")
_PUBLIC_SETTINGS = {
    "schema_version", *_FLAGS, "selected_upstream_id", "local_port",
    "local_username", "dns_policy_version", "deny_policy_version",
}


class ProxyStorageError(ValueError):
    """State is unknown/unavailable; callers must fail closed."""


class ProxyCapacityError(ValueError):
    """A new write cannot fit; existing state is valid and remains usable."""


class ProxyConflict(ValueError):
    """A draft changed or an apply is unresolved."""


class ProxyValidationError(ValueError):
    """Invalid non-secret, user-facing input."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _decode(raw: bytes):
    return json.loads(raw, object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _require(condition: bool):
    if not condition:
        raise ProxyStorageError("Proxy state is invalid; dedicated offline recovery is required. No defaults were applied.")


def _defaults() -> dict:
    return {"schema_version": 1, "local_proxy_enabled": False,
            "relay_enabled": False, "selected_upstream_id": None,
            "local_port": 7891, "local_username": "",
            "local_password_ref": None, "dns_policy_version": 1,
            "deny_policy_version": 1}


def _model(profile: dict):
    # Lazy import avoids importing the parser/process runner during app startup.
    from app.services.upstream_profile import UpstreamProfile, UpstreamValidationError
    try:
        return UpstreamProfile.from_dict(profile)
    except (UpstreamValidationError, TypeError, KeyError):
        raise ProxyValidationError("Upstream profile is invalid") from None


def _settings(payload: dict, password_ref: str | None) -> dict:
    if not isinstance(payload, dict) or set(payload) != _PUBLIC_SETTINGS:
        raise ProxyValidationError("Proxy settings have missing or unsupported fields")
    result = copy.deepcopy(payload)
    for key in ("schema_version", "dns_policy_version", "deny_policy_version"):
        if type(result[key]) is not int or result[key] != 1:
            raise ProxyValidationError("Unsupported proxy settings version")
    if any(type(result[key]) is not bool for key in _FLAGS):
        raise ProxyValidationError("Proxy switches must be booleans")
    if type(result["local_port"]) is not int or not 1024 <= result["local_port"] <= 65535:
        raise ProxyValidationError("Local proxy port must be between 1024 and 65535")
    if not isinstance(result["local_username"], str) or not re.fullmatch(r"[A-Za-z0-9._-]{0,64}", result["local_username"]):
        raise ProxyValidationError("Local proxy username is invalid")
    selected = result["selected_upstream_id"]
    if selected is not None and (not isinstance(selected, str) or not _ID.fullmatch(selected)):
        raise ProxyValidationError("Selected upstream ID is invalid")
    if any(result[key] for key in _FLAGS) and selected is None:
        raise ProxyValidationError("An enabled proxy requires an explicitly selected upstream")
    if result["local_proxy_enabled"] and (not result["local_username"] or password_ref is None):
        raise ProxyValidationError("Local proxy requires a username and password")
    result["local_password_ref"] = password_ref
    return result


class ProxyStore:
    """A service-user-owned store under VUI_DATA_DIR/proxy-state.

    Existing unsafe permissions/links are rejected, never repaired with chmod.
    All path components are opened without following symlinks. Files are opened
    relative to verified directory descriptors, and lock files are never removed.
    The parent initialization marker prevents missing state from becoming OFF.
    """

    def __init__(self, data_dir: str | Path | None = None):
        self.data_dir = Path(os.path.abspath(data_dir if data_dir is not None else os.getenv("VUI_DATA_DIR", "data")))
        self.path = self.data_dir / "proxy-state"
        with _LOCKS_GUARD:
            self._thread_lock = _LOCKS.setdefault(str(self.data_dir), threading.RLock())

    @staticmethod
    def _check_file(fd: int):
        info = os.fstat(fd)
        _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                 and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)

    @staticmethod
    def _check_dir(fd: int, private: bool = True):
        info = os.fstat(fd)
        _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                 and (stat.S_IMODE(info.st_mode) == 0o700 if private else not info.st_mode & 0o022))

    def _data_fd(self, *, initialize: bool = True) -> int:
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            parts = self.data_dir.parts[1:]
            for index, part in enumerate(parts):
                ancestor = os.fstat(fd)
                # A root/service-owned sticky /tmp is safe for an owned child;
                # ordinary writable or foreign-owned ancestors are not.
                _require(ancestor.st_uid in (0, os.geteuid()) and (
                    not ancestor.st_mode & 0o022 or bool(ancestor.st_mode & stat.S_ISVTX)))
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    # Only the selected data directory may be bootstrapped.
                    if not initialize or index != len(parts) - 1:
                        raise
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                        os.fsync(fd)
                    except FileExistsError:
                        pass
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            self._check_dir(fd, private=False)
            return fd
        except BaseException:
            os.close(fd)
            raise

    @staticmethod
    def _read_bytes(directory: int, name: str, optional: bool = False) -> bytes | None:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            if optional:
                return None
            raise
        try:
            ProxyStore._check_file(fd)
            before = os.fstat(fd)
            _require(before.st_size <= MAX_BYTES)
            pieces, total = [], 0
            while True:
                part = os.read(fd, min(65536, MAX_BYTES + 1 - total))
                if not part:
                    break
                pieces.append(part)
                total += len(part)
                _require(total <= MAX_BYTES)
            after = os.fstat(fd)
            _require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                     == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns))
            return b"".join(pieces)
        finally:
            os.close(fd)

    @classmethod
    def _read_json(cls, directory: int, name: str, optional: bool = False):
        raw = cls._read_bytes(directory, name, optional)
        if raw is None:
            return None
        try:
            result = _decode(raw)
        except (ValueError, TypeError, RecursionError):
            raise ProxyStorageError("Saved proxy document is corrupt; no defaults were applied.") from None
        # A present JSON null must never masquerade as an absent marker/journal.
        _require(isinstance(result, dict))
        return result

    @staticmethod
    def _atomic_bytes(directory: int, name: str, raw: bytes):
        if len(raw) > MAX_BYTES:
            raise ProxyCapacityError("Proxy history capacity is full; no changes were applied")
        # Refuse unsafe targets before rename; no open/truncate follows a link.
        ProxyStore._read_bytes(directory, name, optional=True)
        temp = ".stage-" + uuid.uuid4().hex
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        try:
            ProxyStore._check_file(fd)
            remaining = memoryview(raw)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("short write")
                remaining = remaining[written:]
            os.fsync(fd)
            os.replace(temp, name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(fd)
            try:
                os.unlink(temp, dir_fd=directory)
            except FileNotFoundError:
                pass

    @classmethod
    def _atomic_json(cls, directory: int, name: str, value: dict):
        cls._atomic_bytes(directory, name, _canonical(value))

    @classmethod
    def _immutable(cls, directory: int, name: str, value: dict):
        existing = cls._read_bytes(directory, name, optional=True)
        raw = _canonical(value)
        if existing is not None:
            _require(existing == raw)
        else:
            cls._atomic_bytes(directory, name, raw)

    @staticmethod
    def _open_dir(parent: int, name: str) -> int:
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            ProxyStore._check_dir(fd)
            return fd
        except BaseException:
            os.close(fd)
            raise

    @contextmanager
    def _ownership(self):
        try:
            with data_owner(self.data_dir):
                yield
        except DataOwnerError:
            raise ProxyStorageError("Proxy storage is in use or unsafe; offline recovery cannot overlap a writer") from None

    @contextmanager
    def _locked(self, allow_uncertain: bool = False, initialize: bool = True):
        with self._thread_lock, self._ownership():
            data = lock = root = revisions = secrets = None
            try:
                data = self._data_fd(initialize=initialize)
                lock = os.open(".proxy-state.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                               0o600, dir_fd=data)
                self._check_file(lock)
                fcntl.flock(lock, fcntl.LOCK_EX)
                self._check_file(lock)
                # The lock itself must still be the name protected by flock.
                linked = os.stat(".proxy-state.lock", dir_fd=data, follow_symlinks=False)
                _require((linked.st_dev, linked.st_ino) == (os.fstat(lock).st_dev, os.fstat(lock).st_ino))
                marker = self._read_json(data, ".proxy-state-initialized", optional=True)
                try:
                    root = self._open_dir(data, "proxy-state")
                except FileNotFoundError:
                    _require(marker is None and initialize)
                    os.mkdir("proxy-state", mode=0o700, dir_fd=data)
                    os.fsync(data)
                    root = self._open_dir(data, "proxy-state")
                if marker is None:
                    _require(initialize and not os.listdir(root))
                    marker = {"schema_version": 1, "store_id": uuid.uuid4().hex}
                    # Written before other state: interrupted initialization is
                    # unknown, and cannot be mistaken for a fresh OFF install.
                    self._atomic_json(data, ".proxy-state-initialized", marker)
                    for name in ("revisions", "secrets"):
                        os.mkdir(name, mode=0o700, dir_fd=root)
                    os.fsync(root)
                    revisions = self._open_dir(root, "revisions")
                    secrets = self._open_dir(root, "secrets")
                    initial = {"schema_version": 1, "settings": _defaults(), "upstreams": {},
                               "local_password_revision": None}
                    revision = _digest(initial)
                    self._immutable(revisions, revision + ".json", initial)
                    state = {"schema_version": 1, "store_id": marker["store_id"],
                             "draft_revision": revision, "active_revision": None,
                             "core_revision": None, "pending": None, "transactions": {}}
                    self._atomic_json(root, "state.json", state)
                else:
                    _require(isinstance(marker, dict) and set(marker) == {"schema_version", "store_id"}
                             and type(marker["schema_version"]) is int and marker["schema_version"] == 1
                             and isinstance(marker["store_id"], str) and re.fullmatch(r"[0-9a-f]{32}", marker["store_id"]) is not None)
                    revisions = self._open_dir(root, "revisions")
                    secrets = self._open_dir(root, "secrets")
                uncertain = self._read_json(root, "write-intent.json", optional=True)
                if uncertain is not None and not allow_uncertain:
                    raise ProxyStorageError("Proxy storage has an unconfirmed write; recovery is required. No defaults were applied.")
                state = self._read_json(root, "state.json")
                self._validate_state(state, marker["store_id"])
                yield root, revisions, secrets, state, uncertain
            except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
                raise ProxyStorageError("Proxy storage is unavailable or corrupt; recovery is required. No defaults were applied.") from None
            finally:
                for fd in (secrets, revisions, root, lock, data):
                    if fd is not None:
                        os.close(fd)

    @staticmethod
    def _validate_state(state: dict, store_id: str):
        _require(isinstance(state, dict) and set(state) == {
            "schema_version", "store_id", "draft_revision", "active_revision",
            "core_revision", "pending", "transactions"} or isinstance(state, dict) and set(state) == {
            "schema_version", "store_id", "draft_revision", "active_revision", "core_revision",
            "pending", "transactions", "active_transaction_id"})
        _require(type(state["schema_version"]) is int and state["schema_version"] == 1 and state["store_id"] == store_id)
        _require(isinstance(state["draft_revision"], str) and _HEX.fullmatch(state["draft_revision"]) is not None)
        active = state["active_revision"]
        _require(active is None or isinstance(active, str) and _HEX.fullmatch(active) is not None)
        core = state["core_revision"]
        _require((active is None and core is None) or (active is not None and isinstance(core, str) and _TX.fullmatch(core) is not None))
        _require(isinstance(state["transactions"], dict))
        for transaction_id, result in state["transactions"].items():
            _require(isinstance(transaction_id, str) and _TX.fullmatch(transaction_id) is not None)
            _require(isinstance(result, dict) and set(result) in ({"transaction_id", "revision", "phase", "core_revision"},
                {"transaction_id", "revision", "phase", "core_revision", "runtime_context"}))
            _require(result["transaction_id"] == transaction_id and result["phase"] in ("committed", "aborted"))
            _require(isinstance(result["revision"], str) and _HEX.fullmatch(result["revision"]) is not None)
            _require(result["core_revision"] is None if result["phase"] == "aborted" else isinstance(result["core_revision"], str) and _TX.fullmatch(result["core_revision"]) is not None)
            if "runtime_context" in result:
                ProxyStore._validate_runtime_context(result["runtime_context"], result["revision"])
                if result["phase"] == "committed":
                    _require(result["runtime_context"]["candidate_core_revision"] == result["core_revision"])
        committed = [result for result in state["transactions"].values() if result["phase"] == "committed"]
        # Absent active is valid only before the first successful apply. An
        # erased active pointer must not turn a previously applied store OFF.
        _require(not committed if active is None else any(
            result["revision"] == active and result["core_revision"] == core for result in committed))
        if "active_transaction_id" in state:
            _require(isinstance(state["active_transaction_id"], str) and _TX.fullmatch(state["active_transaction_id"]) is not None)
            transaction = state["transactions"].get(state["active_transaction_id"])
            _require(transaction is not None and transaction["phase"] == "committed"
                     and transaction["revision"] == active and transaction["core_revision"] == core)
        pending = state["pending"]
        if pending is not None:
            _require(isinstance(pending, dict) and set(pending) in ({"transaction_id", "revision", "base_active_revision", "base_core_revision", "phase"},
                {"transaction_id", "revision", "base_active_revision", "base_core_revision", "phase", "runtime_context"}))
            _require(isinstance(pending["transaction_id"], str) and _TX.fullmatch(pending["transaction_id"]) is not None)
            _require(pending["transaction_id"] not in state["transactions"] and pending["phase"] == "prepared")
            context = pending.get("runtime_context")
            if "runtime_context" in pending:
                ProxyStore._validate_runtime_context(context, pending["revision"])
                if active is not None:
                    _require(context["previous_core_revision"] == core)
            target = active if context and context["kind"] == "reconcile" else state["draft_revision"]
            _require(pending["revision"] == target and pending["base_active_revision"] == active and pending["base_core_revision"] == core)

    @staticmethod
    def _validate_runtime_context(context, revision):
        """Private, bounded recovery metadata; never raw configs or paths."""
        _require(isinstance(context, dict) and set(context) == {
            "schema_version", "kind", "candidate_core_revision", "previous_core_revision",
            "previous_enabled", "policy_identity", "endpoint", "self_addresses"})
        _require(type(context["schema_version"]) is int and context["schema_version"] == 1)
        _require(context["kind"] in ("apply", "reconcile"))
        for key in ("candidate_core_revision", "policy_identity"):
            _require(isinstance(context[key], str) and _HEX.fullmatch(context[key]) is not None)
        previous = context["previous_core_revision"]
        _require(previous is None or isinstance(previous, str) and _HEX.fullmatch(previous) is not None)
        _require(type(context["previous_enabled"]) is bool and (previous is not None or not context["previous_enabled"]))
        endpoint = context["endpoint"]
        if endpoint is not None:
            _require(isinstance(endpoint, dict) and set(endpoint) == {"profile_id", "revision", "host", "addresses", "resolved_at"})
            _require(isinstance(endpoint["profile_id"], str) and _ID.fullmatch(endpoint["profile_id"]) is not None)
            _require(endpoint["revision"] == revision and type(endpoint["resolved_at"]) is int and endpoint["resolved_at"] >= 0)
            from app.services.upstream_profile import server_host, UpstreamValidationError
            try:
                server_host(endpoint["host"])
            except (UpstreamValidationError, TypeError):
                _require(False)
            _require(type(endpoint["addresses"]) is list and 1 <= len(endpoint["addresses"]) <= 32)
        if endpoint is not None:
            try:
                ordered = sorted(set(endpoint["addresses"]), key=lambda value: (ipaddress.ip_address(value).version, int(ipaddress.ip_address(value))))
                _require(ordered == endpoint["addresses"])
            except (ValueError, TypeError):
                _require(False)
        addresses = context["self_addresses"]
        _require(type(addresses) is list and len(addresses) <= 4096)
        for address in addresses + ([] if endpoint is None else endpoint["addresses"]):
            _require(type(address) is str and "%" not in address)
            try:
                _require(str(ipaddress.ip_address(address)) == address)
            except ValueError:
                _require(False)

    @staticmethod
    def _public_pending(pending):
        return None if pending is None else {key: copy.deepcopy(pending[key]) for key in (
            "transaction_id", "revision", "base_active_revision", "base_core_revision", "phase")}

    @staticmethod
    def _active_context(state):
        # The core revision is part of the active identity, including node/cert
        # reconciliation that intentionally leaves the proxy revision alone.
        if "active_transaction_id" in state:
            return copy.deepcopy(state["transactions"][state["active_transaction_id"]].get("runtime_context"))
        for result in reversed(list(state["transactions"].values())):
            if (result["phase"] == "committed" and result["revision"] == state["active_revision"]
                    and result["core_revision"] == state["core_revision"]):
                return copy.deepcopy(result.get("runtime_context"))
        return None

    def _revision(self, revisions: int, secrets: int, revision: str) -> tuple[dict, dict, str | None]:
        doc = self._read_json(revisions, revision + ".json")
        _require(isinstance(doc, dict) and set(doc) == {"schema_version", "settings", "upstreams", "local_password_revision"})
        _require(type(doc["schema_version"]) is int and doc["schema_version"] == 1 and _digest(doc) == revision)
        settings = doc["settings"]
        _require(isinstance(settings, dict) and set(settings) == _PUBLIC_SETTINGS | {"local_password_ref"})
        password_ref = settings["local_password_ref"]
        _require(password_ref is None or isinstance(password_ref, str) and _SECRET.fullmatch(password_ref) is not None)
        try:
            _settings({key: value for key, value in settings.items() if key != "local_password_ref"}, password_ref)
        except ProxyValidationError:
            raise ProxyStorageError("Saved proxy settings are invalid; no defaults were applied.") from None
        entries = doc["upstreams"]
        _require(isinstance(entries, dict) and len(entries) <= MAX_UPSTREAMS)
        profiles = {}
        for profile_id, entry in entries.items():
            _require(isinstance(profile_id, str) and _ID.fullmatch(profile_id) is not None)
            _require(isinstance(entry, dict) and set(entry) == {"profile", "secret_ref", "secret_revision", "source_format", "source_fingerprint"})
            _require(isinstance(entry["secret_ref"], str) and _SECRET.fullmatch(entry["secret_ref"]) is not None)
            secret = self._read_json(secrets, entry["secret_ref"] + ".json")
            _require(isinstance(secret, dict) and set(secret) == {"schema_version", "kind", "uuid", "public_key", "short_id"}
                     and type(secret["schema_version"]) is int and secret["schema_version"] == 1 and secret["kind"] == "upstream"
                     and _digest(secret) == entry["secret_revision"])
            _require(isinstance(entry["profile"], dict) and "uuid" not in entry["profile"]
                     and isinstance(entry["profile"].get("tls"), dict)
                     and not {"public_key", "short_id"} & entry["profile"]["tls"].keys())
            try:
                profile = _model({**entry["profile"], "uuid": secret["uuid"],
                                  "tls": {**entry["profile"]["tls"], "public_key": secret["public_key"],
                                          "short_id": secret["short_id"]}}).to_dict()
                self._source(entry["source_format"], entry["source_fingerprint"])
            except ProxyValidationError:
                raise ProxyStorageError("Saved upstream profile is invalid; no defaults were applied.") from None
            description = {key: copy.deepcopy(value) for key, value in profile.items() if key != "uuid"}
            del description["tls"]["public_key"]
            del description["tls"]["short_id"]
            _require(description == entry["profile"])
            profiles[profile_id] = profile
        selected = settings["selected_upstream_id"]
        _require(selected is None or selected in profiles)
        password = None
        if password_ref is not None:
            secret = self._read_json(secrets, password_ref + ".json")
            _require(isinstance(secret, dict) and set(secret) == {"schema_version", "kind", "password"}
                     and type(secret["schema_version"]) is int and secret["schema_version"] == 1
                     and secret["kind"] == "local_password" and _digest(secret) == doc["local_password_revision"])
            password = secret["password"]
            try:
                self._password(password)
            except ProxyValidationError:
                raise ProxyStorageError("Saved local authentication is invalid; no defaults were applied.") from None
            _require(bool(password))
        else:
            _require(doc["local_password_revision"] is None)
        return doc, profiles, password

    @staticmethod
    def _source(source_format, source_fingerprint):
        if source_format not in ("manual", "uri", "v2ray-json", "clash-yaml"):
            raise ProxyValidationError("Upstream source format is invalid")
        if source_fingerprint is not None and (not isinstance(source_fingerprint, str) or not _HEX.fullmatch(source_fingerprint)):
            raise ProxyValidationError("Upstream source fingerprint is invalid")

    @staticmethod
    def _password(password):
        if password is not None and (not isinstance(password, str) or password and not 12 <= len(password) <= 255
                                     or any(ord(char) < 33 or ord(char) > 126 for char in password)):
            raise ProxyValidationError("Local proxy password must contain 12 to 255 printable non-space ASCII characters")

    @staticmethod
    def _public_settings(settings):
        return {**{key: copy.deepcopy(settings[key]) for key in _PUBLIC_SETTINGS},
                "local_password_set": settings["local_password_ref"] is not None}

    def _snapshot(self, revisions, secrets, state):
        doc, profiles, _ = self._revision(revisions, secrets, state["draft_revision"])
        upstreams = []
        for profile_id in sorted(profiles):
            summary = _model(profiles[profile_id]).redacted()
            upstreams.append({**summary, "id": profile_id,
                              "source_format": doc["upstreams"][profile_id]["source_format"],
                              "source_fingerprint": doc["upstreams"][profile_id]["source_fingerprint"]})
        active = None
        if state["active_revision"] is not None:
            active_doc, _, _ = self._revision(revisions, secrets, state["active_revision"])
            context = self._active_context(state)
            endpoint = context.get("endpoint") if context and any(active_doc["settings"][key] for key in _FLAGS) else None
            active = {"revision": state["active_revision"], "settings": self._public_settings(active_doc["settings"]),
                      "core_revision": state["core_revision"],
                      "pinned_address": endpoint["addresses"][0] if endpoint else None,
                      "resolved_at": endpoint["resolved_at"] if endpoint else None}
        return {"revision": state["draft_revision"], "settings": self._public_settings(doc["settings"]),
                "upstreams": upstreams, "active": active, "pending": self._public_pending(state["pending"])}

    @staticmethod
    def _check_state_capacity(old, new):
        """Exact encoded limits, BEFORE touching an intent or commit record."""
        if old == new:
            return
        intent = {"schema_version": 1, "previous": old, "candidate": new}
        if len(_canonical(new)) > MAX_BYTES or len(_canonical(intent)) > MAX_BYTES:
            raise ProxyCapacityError("Proxy history capacity is full; no changes were applied")

    @staticmethod
    def _terminal_state(state, pending, phase, core_revision=None):
        """One constructor shared by admission and the actual terminal write."""
        transaction_id = pending["transaction_id"]
        result = {"transaction_id": transaction_id, "revision": pending["revision"],
                  "phase": phase, "core_revision": core_revision}
        if "runtime_context" in pending:
            result["runtime_context"] = copy.deepcopy(pending["runtime_context"])
        changed = {**state, "pending": None, "transactions": {**state["transactions"], transaction_id: result}}
        if phase == "committed":
            changed.update(active_revision=pending["revision"], core_revision=core_revision,
                           active_transaction_id=transaction_id)
        return changed

    @classmethod
    def _check_doc_capacity(cls, state, doc):
        raw = _canonical(doc)
        if len(raw) > MAX_BYTES:
            raise ProxyCapacityError("Proxy draft capacity is full; no changes were applied")
        changed = {**state, "draft_revision": hashlib.sha256(raw).hexdigest()}
        cls._check_state_capacity(state, changed)

    def _commit_state(self, root, old, new):
        if old == new:
            return
        self._check_state_capacity(old, new)
        # A durable intent guards against an unconfirmed rename/fsync. If even
        # rollback fails, readers stop rather than infer an effective OFF mode.
        self._atomic_json(root, "write-intent.json", {"schema_version": 1, "previous": old, "candidate": new})
        try:
            self._atomic_json(root, "state.json", new)
        except (OSError, ProxyStorageError):
            try:
                self._atomic_json(root, "state.json", old)
                os.unlink("write-intent.json", dir_fd=root)
                os.fsync(root)
            except (OSError, ProxyStorageError):
                pass
            raise ProxyStorageError("Proxy write failed; reload to verify state or recover an unconfirmed write") from None
        os.unlink("write-intent.json", dir_fd=root)
        os.fsync(root)

    @staticmethod
    def _editable(state, expected_revision):
        if expected_revision != state["draft_revision"]:
            raise ProxyConflict("Proxy draft changed in another session; reload before saving")
        if state["pending"] is not None:
            raise ProxyConflict("An apply is pending recovery; drafts cannot change")

    def _save_doc(self, root, revisions, secrets, state, doc):
        self._check_doc_capacity(state, doc)
        revision = _digest(doc)
        self._immutable(revisions, revision + ".json", doc)
        self._revision(revisions, secrets, revision)
        changed = {**state, "draft_revision": revision}
        self._commit_state(root, state, changed)
        return self._snapshot(revisions, secrets, changed)

    def read_snapshot(self, *, initialize: bool = True) -> dict:
        with self._locked(initialize=initialize) as (_, revisions, secrets, state, _):
            return self._snapshot(revisions, secrets, state)

    def _load(self, active: bool, *, reject_pending: bool = False) -> dict | None:
        with self._locked() as (_, revisions, secrets, state, _):
            # Validate both roots even when the requested one is absent.
            self._snapshot(revisions, secrets, state)
            if reject_pending and state["pending"] is not None:
                raise ProxyConflict("An apply is pending recovery")
            revision = state["active_revision"] if active else state["draft_revision"]
            if revision is None:
                return None
            doc, profiles, password = self._revision(revisions, secrets, revision)
            return {"revision": revision, "settings": copy.deepcopy(doc["settings"]),
                    "upstreams": profiles, "local_password": password,
                    "core_revision": state["core_revision"] if active else None,
                    "pending": self._public_pending(state["pending"]),
                    "runtime_context": self._active_context(state) if active else None}

    def load_draft(self) -> dict:
        return self._load(False)

    def load_active(self, *, reject_pending: bool = False) -> dict | None:
        return self._load(True, reject_pending=reject_pending)

    def save_upstream(self, profile: dict, expected_revision: str, *, profile_id: str | None = None,
                      source_format: str = "manual", source_fingerprint: str | None = None) -> dict:
        self._source(source_format, source_fingerprint)
        if profile_id is not None and (not isinstance(profile_id, str) or not _ID.fullmatch(profile_id)):
            raise ProxyValidationError("Upstream ID is invalid")
        with self._locked() as (root, revisions, secrets, state, _):
            self._editable(state, expected_revision)
            doc, profiles, _ = self._revision(revisions, secrets, state["draft_revision"])
            self._snapshot(revisions, secrets, state)
            if profile_id is not None and profile_id not in profiles:
                raise ProxyValidationError("Upstream does not exist")
            if not isinstance(profile, dict):
                raise ProxyValidationError("Upstream profile is invalid")
            profile = copy.deepcopy(profile)
            if profile_id is not None and profile.get("uuid") == "":
                profile["uuid"] = profiles[profile_id]["uuid"]
            if (profile_id is not None and isinstance(profile.get("tls"), dict)
                    and profile["tls"].get("mode") == "reality"
                    and profiles[profile_id]["tls"]["mode"] == "reality"):
                for field in ("public_key", "short_id"):
                    if profile["tls"].get(field) == "":
                        profile["tls"][field] = profiles[profile_id]["tls"][field]
            normalized = _model(profile).to_dict()
            if profile_id is None:
                if len(profiles) >= MAX_UPSTREAMS:
                    raise ProxyValidationError("Too many upstream profiles")
                profile_id = "up_" + uuid.uuid4().hex
            old_entry = doc["upstreams"].get(profile_id)
            secret = {"schema_version": 1, "kind": "upstream", "uuid": normalized.pop("uuid"),
                      "public_key": normalized["tls"].pop("public_key"), "short_id": normalized["tls"].pop("short_id")}
            secret_revision = _digest(secret)
            secret_ref = old_entry["secret_ref"] if old_entry and old_entry["secret_revision"] == secret_revision else "sec_" + uuid.uuid4().hex
            doc["upstreams"][profile_id] = {"profile": normalized, "secret_ref": secret_ref,
                                            "secret_revision": secret_revision, "source_format": source_format,
                                            "source_fingerprint": source_fingerprint}
            self._check_doc_capacity(state, doc)
            self._immutable(secrets, secret_ref + ".json", secret)
            return {"snapshot": self._save_doc(root, revisions, secrets, state, doc), "upstream_id": profile_id}

    def save_draft(self, settings: dict, expected_revision: str, *, local_password: str | None = None) -> dict:
        self._password(local_password)
        with self._locked() as (root, revisions, secrets, state, _):
            self._editable(state, expected_revision)
            doc, profiles, old_password = self._revision(revisions, secrets, state["draft_revision"])
            self._snapshot(revisions, secrets, state)
            ref = doc["settings"]["local_password_ref"]
            secret = None
            if local_password and local_password != old_password:
                secret = {"schema_version": 1, "kind": "local_password", "password": local_password}
                ref = "sec_" + uuid.uuid4().hex
                doc["local_password_revision"] = _digest(secret)
            doc["settings"] = _settings(settings, ref)
            if doc["settings"]["selected_upstream_id"] is not None and doc["settings"]["selected_upstream_id"] not in profiles:
                raise ProxyValidationError("Selected upstream does not exist")
            self._check_doc_capacity(state, doc)
            if secret is not None:
                self._immutable(secrets, ref + ".json", secret)
            return self._save_doc(root, revisions, secrets, state, doc)

    def delete_upstream(self, upstream_id: str, expected_revision: str) -> dict:
        if not isinstance(upstream_id, str) or not _ID.fullmatch(upstream_id):
            raise ProxyValidationError("Upstream ID is invalid")
        with self._locked() as (root, revisions, secrets, state, _):
            self._editable(state, expected_revision)
            doc, profiles, _ = self._revision(revisions, secrets, state["draft_revision"])
            self._snapshot(revisions, secrets, state)
            if upstream_id not in profiles:
                raise ProxyValidationError("Upstream does not exist")
            active = self._revision(revisions, secrets, state["active_revision"])[0] if state["active_revision"] else None
            for snapshot in (doc, active):
                if snapshot and snapshot["settings"]["selected_upstream_id"] == upstream_id and any(snapshot["settings"][key] for key in _FLAGS):
                    raise ProxyConflict("Disable or replace the upstream before deleting a profile in use")
            del doc["upstreams"][upstream_id]
            if doc["settings"]["selected_upstream_id"] == upstream_id:
                doc["settings"]["selected_upstream_id"] = None
            # Old immutable secrets/revisions are retained for recovery; this
            # explicit deletion removes only the draft profile reference.
            return self._save_doc(root, revisions, secrets, state, doc)

    def clear_local_password(self, expected_revision: str) -> dict:
        with self._locked() as (root, revisions, secrets, state, _):
            self._editable(state, expected_revision)
            doc, _, _ = self._revision(revisions, secrets, state["draft_revision"])
            self._snapshot(revisions, secrets, state)
            active = self._revision(revisions, secrets, state["active_revision"])[0] if state["active_revision"] else None
            if doc["settings"]["local_proxy_enabled"] or active and active["settings"]["local_proxy_enabled"]:
                raise ProxyConflict("Disable the local proxy before removing authentication")
            doc["settings"]["local_password_ref"] = None
            doc["local_password_revision"] = None
            return self._save_doc(root, revisions, secrets, state, doc)

    def transaction(self, transaction_id: str) -> dict | None:
        """Internal lookup for idempotent requests, including a newer draft."""
        if not isinstance(transaction_id, str) or not _TX.fullmatch(transaction_id):
            raise ProxyValidationError("Apply transaction ID is invalid")
        with self._locked() as (_, revisions, secrets, state, _):
            self._snapshot(revisions, secrets, state)
            result = state["transactions"].get(transaction_id)
            if result is None and state["pending"] and state["pending"]["transaction_id"] == transaction_id:
                result = state["pending"]
            return copy.deepcopy(result)

    def begin_apply(self, expected_revision: str, transaction_id: str, *, runtime_context: dict | None = None) -> dict:
        return self._begin(expected_revision, transaction_id, runtime_context=runtime_context)

    def begin_reconcile(self, expected_revision: str, transaction_id: str, *, expected_core_revision: str,
                        runtime_context: dict) -> dict:
        """Journal an active-only node/certificate change; never publish draft."""
        return self._begin(expected_revision, transaction_id, runtime_context=runtime_context,
                           reconcile=True, expected_core_revision=expected_core_revision)

    def _begin(self, expected_revision, transaction_id, *, runtime_context=None,
               reconcile=False, expected_core_revision=None):
        if not isinstance(transaction_id, str) or not _TX.fullmatch(transaction_id):
            raise ProxyValidationError("Apply transaction ID is invalid")
        with self._locked() as (root, revisions, secrets, state, _):
            self._snapshot(revisions, secrets, state)
            target = state["active_revision"] if reconcile else state["draft_revision"]
            if expected_revision != target or reconcile and expected_core_revision != state["core_revision"]:
                raise ProxyConflict("Proxy snapshot changed before apply")
            if runtime_context is not None:
                self._validate_runtime_context(runtime_context, expected_revision)
                _require(runtime_context["kind"] == ("reconcile" if reconcile else "apply"))
                if state["active_revision"] is not None:
                    _require(runtime_context["previous_core_revision"] == state["core_revision"])
            elif reconcile:
                raise ProxyValidationError("Runtime context is required for reconciliation")
            previous = state["transactions"].get(transaction_id)
            if previous:
                if (previous["revision"] != expected_revision or runtime_context is not None
                        and previous.get("runtime_context") != runtime_context):
                    raise ProxyConflict("Apply transaction ID was already used for another revision")
                return copy.deepcopy(previous)
            if state["pending"]:
                if (state["pending"]["transaction_id"] != transaction_id or runtime_context is not None
                        and state["pending"].get("runtime_context") != runtime_context):
                    raise ProxyConflict("Another apply is pending recovery")
                return copy.deepcopy(state["pending"])
            pending = {"transaction_id": transaction_id, "revision": expected_revision,
                       "base_active_revision": state["active_revision"], "base_core_revision": state["core_revision"],
                       "phase": "prepared"}
            if runtime_context is not None:
                pending["runtime_context"] = copy.deepcopy(runtime_context)
            prepared = {**state, "pending": pending}
            # Admission guarantees room for BOTH outcomes. Draft mutations are
            # blocked while prepared, so no writer can consume this reserve.
            # Old M1 callers do not name the core until commit; reserve its
            # maximum accepted identifier length rather than assume SHA-256.
            candidate = runtime_context["candidate_core_revision"] if runtime_context is not None else "x" * 128
            self._check_state_capacity(state, prepared)
            self._check_state_capacity(prepared, self._terminal_state(prepared, pending, "committed", candidate))
            self._check_state_capacity(prepared, self._terminal_state(prepared, pending, "aborted"))
            self._commit_state(root, state, prepared)
            return copy.deepcopy(pending)

    def commit_apply(self, transaction_id: str, core_revision: str) -> dict:
        if not isinstance(transaction_id, str) or not _TX.fullmatch(transaction_id):
            raise ProxyValidationError("Apply transaction ID is invalid")
        if not isinstance(core_revision, str) or not _TX.fullmatch(core_revision):
            raise ProxyValidationError("Core revision is invalid")
        with self._locked() as (root, revisions, secrets, state, _):
            self._snapshot(revisions, secrets, state)
            previous = state["transactions"].get(transaction_id)
            if previous:
                if previous["phase"] != "committed" or previous["core_revision"] != core_revision:
                    raise ProxyConflict("Apply transaction already has a different outcome")
                return self._snapshot(revisions, secrets, state)
            pending = state["pending"]
            if pending is None or pending["transaction_id"] != transaction_id:
                raise ProxyConflict("No matching pending apply exists")
            context = pending.get("runtime_context")
            if context is not None and context["candidate_core_revision"] != core_revision:
                raise ProxyConflict("Core revision does not match the prepared candidate")
            changed = self._terminal_state(state, pending, "committed", core_revision)
            self._commit_state(root, state, changed)
            return self._snapshot(revisions, secrets, changed)

    def abort_apply(self, transaction_id: str) -> dict:
        """Record runtime-confirmed rollback; never roll back runtime implicitly."""
        if not isinstance(transaction_id, str) or not _TX.fullmatch(transaction_id):
            raise ProxyValidationError("Apply transaction ID is invalid")
        with self._locked() as (root, revisions, secrets, state, _):
            self._snapshot(revisions, secrets, state)
            previous = state["transactions"].get(transaction_id)
            if previous:
                if previous["phase"] != "aborted":
                    raise ProxyConflict("A committed apply cannot be aborted")
                return self._snapshot(revisions, secrets, state)
            pending = state["pending"]
            if pending is None or pending["transaction_id"] != transaction_id:
                raise ProxyConflict("No matching pending apply exists")
            changed = self._terminal_state(state, pending, "aborted")
            self._commit_state(root, state, changed)
            return self._snapshot(revisions, secrets, changed)

    def recoverable_pending(self) -> dict | None:
        with self._locked(allow_uncertain=True) as (_, revisions, secrets, state, uncertain):
            if uncertain is not None:
                return {"phase": "storage_uncertain", "requires_recovery": True}
            self._snapshot(revisions, secrets, state)
            return copy.deepcopy(state["pending"])

    @contextmanager
    def backup_snapshot(self):
        """Yield validated private backup bytes while retaining the writer lock.

        This never bootstraps missing state. The consumer must exhaust the
        iterator inside the context, copy it only into private staging, and
        discard that staging on any error. No lock file or arbitrary extra
        files are included. A pending runtime apply remains explicit in backup.
        """
        with self._locked(initialize=False) as (root, revisions, secrets, state, _):
            self._snapshot(revisions, secrets, state)
            _require(set(os.listdir(root)) == {"state.json", "revisions", "secrets"})

            def entries():
                yield ".proxy-state-initialized", _canonical({"schema_version": 1, "store_id": state["store_id"]})
                yield "proxy-state/state.json", _canonical(state)
                for name in sorted(os.listdir(revisions)):
                    _require(name.endswith(".json") and _HEX.fullmatch(name[:-5]) is not None)
                    self._revision(revisions, secrets, name[:-5])
                    yield "proxy-state/revisions/" + name, self._read_bytes(revisions, name)
                for name in sorted(os.listdir(secrets)):
                    _require(name.endswith(".json") and _SECRET.fullmatch(name[:-5]) is not None)
                    secret = self._read_json(secrets, name)
                    _require(type(secret.get("schema_version")) is int and secret["schema_version"] == 1)
                    if secret.get("kind") == "local_password":
                        _require(set(secret) == {"schema_version", "kind", "password"} and bool(secret["password"]))
                        try:
                            self._password(secret["password"])
                        except ProxyValidationError:
                            raise ProxyStorageError("Saved authentication is invalid; backup was not created") from None
                    else:
                        _require(set(secret) == {"schema_version", "kind", "uuid", "public_key", "short_id"}
                                 and secret["kind"] == "upstream")
                        # Referenced objects were fully validated above. Orphan
                        # objects left by an interrupted draft must also have
                        # bounded, literal credential fields before archiving.
                        from app.services.upstream_profile import canonical_uuid, literal_string, UpstreamValidationError
                        try:
                            canonical_uuid(secret["uuid"])
                            literal_string(secret["public_key"])
                            literal_string(secret["short_id"])
                        except UpstreamValidationError:
                            raise ProxyStorageError("Saved upstream secret is invalid; backup was not created") from None
                    yield "proxy-state/secrets/" + name, self._read_bytes(secrets, name)

            yield entries()
