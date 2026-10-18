"""Commit-last coordination of one proxy snapshot and the sing-box runtime.

The manager's operation lock covers rows, compilation, checks, journaling and
activation. Runtime configuration is private; every public result is allowlisted.
Recovery must run before ordinary core auto-recovery. A pending transaction is
always rolled back to its recorded baseline, never inferred from a newer draft.
"""
from __future__ import annotations

import copy
import errno
import ipaddress
from dataclasses import asdict
import socket
import uuid

import psutil

from app.services.core_runtime import CoreError, revision
from app.services.inbound_policy import policy_plan
from app.services.proxy_policy import prepare_policy
from app.services.proxy_policy import LOCAL_TAG, CompiledPolicy, VerifiedEndpoint, compile_proxy_policy
from app.services.proxy_resolution import resolve_upstream, self_address_snapshot
from app.services.proxy_store import ProxyCapacityError, ProxyConflict, ProxyStorageError, ProxyStore


_MESSAGES = {
    "LOCAL_PORT_OCCUPIED": "Local proxy port is occupied by another socket or owner",
    "LOCAL_PORT_UNAVAILABLE": "Local proxy port ownership could not be verified",
    "PREPARE_FAILED": "Proxy candidate could not be prepared; committed runtime was not changed",
    "RUNTIME_MISMATCH": "Proxy and core state disagree; affected runtime was stopped for recovery",
    "APPLY_FAILED": "Proxy apply failed; the complete previous committed runtime was restored",
    "RECOVERY_REQUIRED": "Proxy recovery could not be confirmed; affected runtime was stopped and evidence retained",
    "MISSING_ACTIVE_CONTEXT": "Applied proxy recovery context is unavailable; offline recovery is required",
    "VERIFY_FAILED": "Candidate runtime did not match its checked configuration",
}
_STATUS_KEYS = {"name", "version_required", "binary_exists", "running", "pid", "applied_revision",
                "enabled", "config_exists"}


class ProxyApplyError(CoreError):
    """Only fixed messages; core output, profiles and material paths stay private."""

    def __init__(self, code):
        self.code = code
        super().__init__(_MESSAGES[code])


def preflight_local_listener(config, runtime):
    """Probe TCP/UDP and IPv4/IPv6 without replacing or opening a listener.

    An existing local listener is allowed only when its saved configuration and
    actual process owner agree. Inaccessible ownership and competing sockets are
    blockers. This cannot reserve a port across restart; runtime readiness must
    independently prove the resulting exact loopback listener.
    """
    local = [item for item in config.get("inbounds", []) if item.get("tag") == LOCAL_TAG]
    if not local:
        return
    if (len(local) != 1 or local[0].get("type") != "mixed" or local[0].get("listen") != "127.0.0.1"
            or type(local[0].get("listen_port")) is not int):
        raise ProxyApplyError("LOCAL_PORT_UNAVAILABLE")
    port = local[0]["listen_port"]
    owners = set()
    try:
        process = getattr(runtime, "process", None)
        if process is not None and process.poll() is None:
            previous = runtime.active_config()
            if not isinstance(previous, dict):
                raise ProxyApplyError("LOCAL_PORT_UNAVAILABLE")
            old_local = [item for item in previous.get("inbounds", []) if item.get("tag") == LOCAL_TAG]
            if (len(old_local) == 1 and old_local[0].get("type") == "mixed"
                    and old_local[0].get("listen") == "127.0.0.1" and old_local[0].get("listen_port") == port):
                owner = psutil.Process(process.pid)
                owners = {owner.pid, *(child.pid for child in owner.children(recursive=True))}

        def occupied():
            own_listener = False
            for connection in psutil.net_connections(kind="inet"):
                if not connection.laddr or connection.laddr.port != port:
                    continue
                # Established/TIME_WAIT sockets are not listeners; bind probes
                # below still catch exclusive bindings that actually conflict.
                if connection.type == socket.SOCK_STREAM and connection.status != psutil.CONN_LISTEN:
                    continue
                if (connection.type == socket.SOCK_STREAM and connection.family == socket.AF_INET
                        and connection.status == psutil.CONN_LISTEN and connection.laddr.ip == "127.0.0.1"
                        and connection.pid in owners):
                    own_listener = True
                else:
                    raise ProxyApplyError("LOCAL_PORT_OCCUPIED")
            return own_listener

        own_listener = occupied()
        for family, address in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
            for socktype in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
                try:
                    with socket.socket(family, socktype) as probe:
                        if socktype == socket.SOCK_STREAM:
                            # Match the core's normal TCP restart semantics;
                            # TIME_WAIT is not another listening owner.
                            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        if family == socket.AF_INET6:
                            probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                        probe.bind((address, port))
                except OSError as exc:
                    # A host without IPv6 has no IPv6 socket to conflict; any
                    # other unverified error fails closed instead of guessing.
                    if family == socket.AF_INET6 and exc.errno in (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT):
                        continue
                    if (exc.errno == errno.EADDRINUSE and family == socket.AF_INET
                            and socktype == socket.SOCK_STREAM and own_listener):
                        continue
                    raise ProxyApplyError("LOCAL_PORT_OCCUPIED" if exc.errno == errno.EADDRINUSE
                                          else "LOCAL_PORT_UNAVAILABLE") from None
        if occupied() != own_listener:
            raise ProxyApplyError("LOCAL_PORT_UNAVAILABLE")
    except ProxyApplyError:
        raise
    except Exception:
        raise ProxyApplyError("LOCAL_PORT_UNAVAILABLE") from None


class ProxyApplyCoordinator:
    """Dependency-injected coordinator, sharing ``operation_lock`` with manager.

    ``build_config(all_rows)`` builds the ordinary sing-box-only base. Resolution
    accepts the production resolver signature. ``preflight(config, runtime)``
    and ``verify_runtime(config, runtime)`` return None or raise a safe exception.
    The runtime implements CoreRuntime's apply/state/status/recover/stop/restore.
    Callers must never expose ``compile_active`` or its config in an ordinary API.
    """

    def __init__(self, store: ProxyStore, runtime, build_config, operation_lock, *,
                 rows_provider=None, panel_origin=None, resolver=None, address_provider=None,
                 compiler=None, preflight=None, verify_runtime=None):
        self.store, self.runtime = store, runtime
        self.build_config, self.lock = build_config, operation_lock
        self.rows_provider = rows_provider or (lambda: [])
        self.panel_origin = panel_origin
        self.resolver = resolver or resolve_upstream
        self.address_provider = address_provider or self_address_snapshot
        self.compiler = compiler or compile_proxy_policy
        self.preflight = preflight_local_listener if preflight is None else preflight
        self.verify_runtime = verify_runtime

    def _rows(self, rows):
        # Copy while the shared operation lock is held; do not keep mutable ORM
        # instances in transaction state or across a new database operation.
        return list(self.rows_provider() if rows is None else rows)

    def _stop(self):
        try:
            self.runtime.stop(persist=False)
        except Exception:
            # Preserve the committed runtime record even if stopping fails.
            raise ProxyApplyError("RECOVERY_REQUIRED") from None

    def _load_active(self):
        try:
            # Check pending and active under ONE store lock, including the
            # never-applied case where the active snapshot itself is None.
            return self.store.load_active(reject_pending=True)
        except ProxyStorageError:
            self._stop()
            raise ProxyApplyError("RECOVERY_REQUIRED") from None

    def _load_draft(self, expected_revision):
        try:
            snapshot = self.store.load_draft()
        except ProxyStorageError:
            self._stop()
            raise ProxyApplyError("RECOVERY_REQUIRED") from None
        if snapshot["revision"] != expected_revision:
            raise ProxyConflict("Proxy draft changed before apply")
        if snapshot["pending"] is not None:
            raise ProxyConflict("An apply is pending recovery")
        return snapshot

    def _compile(self, snapshot, rows, *, active=False, fresh_pin=False, fence_required=True):
        plan = policy_plan(rows, snapshot["settings"])
        if fence_required and not plan.legacy and self.store.policy_schema_version() != 2:
            self._stop()
            raise ProxyApplyError("RECOVERY_REQUIRED")
        addresses, endpoint = (), None
        if plan.requires_upstream:
            addresses = tuple(self.address_provider(self.panel_origin))
            context = snapshot.get("runtime_context") if active else None
            data = context.get("endpoint") if context else None
            if active and data is not None:
                endpoint = VerifiedEndpoint(**{**data, "addresses": tuple(data["addresses"])})
            elif active and not (fresh_pin and context is not None
                    and not self.store.requires_upstream(snapshot["settings"], context)
                    and context["endpoint"] is None):
                # Only explicit reconcile may prepare the FIRST needed pin.
                # Old policy need is read from committed metadata, never rows.
                raise ProxyApplyError("MISSING_ACTIVE_CONTEXT")
            else:
                selected = snapshot["settings"]["selected_upstream_id"]
                if selected is None or selected not in snapshot["upstreams"]:
                    raise ProxyApplyError("PREPARE_FAILED")
                endpoint = self.resolver(selected, snapshot["revision"], snapshot["upstreams"][selected],
                                         self_addresses=addresses)
        compiled = self.compiler(self.build_config(rows), rows, snapshot,
                                 endpoint=endpoint, self_addresses=addresses)
        return compiled, endpoint, addresses

    def _validate_saved_source(self, rows, active):
        """Reject provably corrupt persisted rows before any early return.

        Prospective CRUD must not call this: its first noninherit row has not
        committed yet and is allowed to establish the durable fence.
        """
        from app.services.proxy_store import _defaults
        plan = policy_plan(rows, active["settings"] if active else _defaults())
        if ((not plan.legacy and self.store.policy_schema_version() != 2)
                or active is None and plan.relay_ids):
            self._stop()
            raise ProxyApplyError("RECOVERY_REQUIRED")
        return plan

    def validate_saved_source(self, rows=None):
        """Manager gates must not mask corrupt persisted policy provenance."""
        with self.lock:
            try:
                active = self.store.load_active()
            except ProxyStorageError:
                try:
                    self._stop()
                except ProxyApplyError:
                    pass
                # Preserve the storage failure even if the affected core
                # cannot stop, so the manager still attempts the other core.
                raise
            return self._validate_saved_source(self._rows(rows), active)

    def validate_inbound_change(self, rows, *, persist_fence=True):
        """Pure prospective qualification; never resolve or activate a draft."""
        with self.lock:
            rows = self._rows(rows)
            snapshot = self._load_active()
            if not persist_fence:
                self._validate_saved_source(rows, snapshot)
            if snapshot is None:
                # Read schema defaults, not unapplied switches or credentials.
                from app.services.proxy_store import _defaults
                settings = _defaults()
                snapshot = {"settings": settings, "upstreams": {}, "local_password": None}
                plan = policy_plan(rows, settings)
                if plan.relay_ids:
                    raise ProxyApplyError("MISSING_ACTIVE_CONTEXT")
                prepare_policy(self.build_config(rows), rows, snapshot)
            else:
                plan = prepare_policy(self.build_config(rows), rows, snapshot)
                context = snapshot.get("runtime_context")
                if plan.requires_upstream and (context is None or context["endpoint"] is None):
                    if context is None or self.store.requires_upstream(snapshot["settings"], context):
                        raise ProxyApplyError("MISSING_ACTIVE_CONTEXT")
                    # Valid old OFF context: exact selected model/ingress was
                    # checked above. Explicit reconcile alone may do DNS later.
                else:
                    self._compile(snapshot, rows, active=True, fence_required=False)
            if not plan.legacy:
                if persist_fence:
                    self.store.ensure_policy_v2()
            return plan

    @staticmethod
    def _bind_source(plan, expected_source_fingerprint, *, required=False):
        if ((required and not plan.legacy and expected_source_fingerprint is None)
                or expected_source_fingerprint is not None
                and expected_source_fingerprint != plan.source_fingerprint):
            raise ProxyConflict("Inbound policy changed; check the complete candidate again before applying")

    def compile_active(self, rows=None) -> CompiledPolicy | None:
        """Internal prospective validation from active settings, never draft."""
        with self.lock:
            snapshot = self._load_active()
            if snapshot is None:
                from app.services.proxy_store import _defaults
                plan = policy_plan(self._rows(rows), _defaults())
                if not plan.legacy:
                    raise ProxyApplyError("MISSING_ACTIVE_CONTEXT")
                return None
            if snapshot["pending"] is not None:
                raise ProxyConflict("An apply is pending recovery")
            return self._compile(snapshot, self._rows(rows), active=True)[0]

    def _checked(self, compiled):
        # CoreRuntime's validation writes an immutable private candidate but
        # neither terminates the old process nor changes its commit record.
        self.runtime.apply(compiled.config, activate=False)
        if self.preflight is not None and compiled.pinned_address is not None:
            self.preflight(compiled.config, self.runtime)

    def validate_active(self, rows=None) -> CompiledPolicy | None:
        with self.lock:
            compiled = self.compile_active(rows)
            if compiled is not None:
                self._checked(compiled)
            return compiled

    def _status(self):
        status = self.runtime.status()
        return {key: copy.deepcopy(value) for key, value in status.items() if key in _STATUS_KEYS}

    def _public(self, *, transaction=None, compiled=None, phase="checked", applied=False, recovered=False):
        try:
            snapshot = self.store.read_snapshot()
        except ProxyStorageError:
            self._stop()
            raise ProxyApplyError("RECOVERY_REQUIRED") from None
        result = {**self._status(), "valid": True, "applied": applied,
                  "phase": phase, "snapshot": snapshot, "recovered": recovered}
        if transaction is not None:
            result.update(transaction_id=transaction["transaction_id"],
                          revision=transaction["revision"],
                          candidate_revision=transaction.get("core_revision") or
                          transaction.get("runtime_context", {}).get("candidate_core_revision"))
        if compiled is not None:
            coverage = copy.deepcopy(compiled.coverage)
            manifest = {item["id"]: item for item in compiled.plan_manifest or []}
            for item in coverage:
                source = manifest.get(item.get("inbound_id"))
                if source is not None:
                    item.update(routing_mode=source["mode"], effective_mode=source["effective"])
            result.update(candidate_revision=revision(compiled.config), coverage=coverage,
                          source_fingerprint=compiled.source_fingerprint,
                          pinned_address=compiled.pinned_address, resolved_at=compiled.resolved_at)
        return result

    def _existing(self, transaction_id, expected_revision, *, kind, rows=None, expected_source_fingerprint=None):
        try:
            transaction = self.store.transaction(transaction_id)
        except ProxyStorageError:
            self._stop()
            raise ProxyApplyError("RECOVERY_REQUIRED") from None
        if transaction is None:
            return None
        context = transaction.get("runtime_context")
        if transaction["revision"] != expected_revision or context and context["kind"] != kind:
            raise ProxyConflict("Apply transaction ID was already used for another request")
        if transaction["phase"] == "prepared":
            raise ProxyConflict("This apply is pending recovery")
        source = self.store.load_revision_snapshot(expected_revision)
        plan = policy_plan(self._rows(rows), source["settings"])
        self._bind_source(plan, expected_source_fingerprint, required=kind == "apply")
        if context and context["schema_version"] == 2:
            if (context["source_fingerprint"] != plan.source_fingerprint
                    or context["plan_manifest"] != plan.manifest):
                raise ProxyConflict("Apply transaction belongs to a different inbound policy")
        elif not plan.legacy:
            raise ProxyConflict("Apply transaction belongs to a legacy inbound policy")
        active = self._load_active()
        applied = (transaction["phase"] == "committed" and active is not None
                   and active["revision"] == transaction["revision"]
                   and active["core_revision"] == transaction["core_revision"]
                   and (context is None or active.get("runtime_context") is not None
                        and active["runtime_context"]["policy_identity"] == context["policy_identity"]))
        result = self._public(transaction=transaction, phase=transaction["phase"], applied=applied)
        result["already_completed"] = True
        return result

    def _baseline(self, active):
        try:
            previous = self.runtime.state()
            if (active is not None and previous["revision"] != active["core_revision"]
                    or type(previous.get("enabled")) is not bool):
                raise CoreError("State mismatch")
        except Exception:
            self._stop()
            raise ProxyApplyError("RUNTIME_MISMATCH") from None
        return previous

    def _verify(self, compiled):
        state = self.runtime.state()
        enabled = bool(compiled.config.get("inbounds"))
        if (state["revision"] != revision(compiled.config) or state["enabled"] != enabled
                or self.runtime.status()["running"] != enabled):
            raise ProxyApplyError("VERIFY_FAILED")
        if self.verify_runtime is not None:
            self.verify_runtime(compiled.config, self.runtime)

    def _restore(self, pending):
        context = pending.get("runtime_context")
        if context is None:
            raise ProxyApplyError("RECOVERY_REQUIRED")
        previous, enabled = context["previous_core_revision"], context["previous_enabled"]
        # The store validates this binding too. Never permit an old direct
        # revision to be supplied as rollback for a committed relay revision.
        if pending["base_active_revision"] is not None and previous != pending["base_core_revision"]:
            raise ProxyApplyError("RECOVERY_REQUIRED")
        self.runtime.restore(previous, enabled=enabled)
        state = self.runtime.state()
        if state["revision"] != previous or state["enabled"] != enabled:
            raise ProxyApplyError("RECOVERY_REQUIRED")
        if bool(self.runtime.status()["running"]) != enabled:
            raise ProxyApplyError("RECOVERY_REQUIRED")

    def _failed(self, transaction_id):
        # Re-read before rollback: a commit failure can be an uncertain write,
        # or an already committed record. Neither permits reverting core alone.
        try:
            pending = self.store.transaction(transaction_id)
            if pending is None or pending["phase"] != "prepared":
                raise ProxyApplyError("RECOVERY_REQUIRED")
            self._rollback_guard(self.store.load_active())
            self._restore(pending)
            self.store.abort_apply(transaction_id)
        except Exception:
            self._stop()
            raise ProxyApplyError("RECOVERY_REQUIRED") from None
        raise ProxyApplyError("APPLY_FAILED") from None

    def _activate(self, snapshot, active, compiled, endpoint, addresses, transaction_id, *, kind):
        previous = self._baseline(active)
        self._checked(compiled)
        context = {"schema_version": 1, "kind": kind, "candidate_core_revision": revision(compiled.config),
                   "previous_core_revision": previous["revision"], "previous_enabled": previous["enabled"],
                   "policy_identity": compiled.identity,
                   "endpoint": None if endpoint is None else {**asdict(endpoint), "addresses": sorted(set(endpoint.addresses),
                       key=lambda value: (ipaddress.ip_address(value).version, int(ipaddress.ip_address(value))))},
                   "self_addresses": list(addresses)}
        if compiled.policy_version == 2:
            # A SQL mode without its precommit capability fence is corrupt,
            # not a migration opportunity during runtime application.
            if self.store.policy_schema_version() != 2:
                self._stop()
                raise ProxyApplyError("RECOVERY_REQUIRED")
            context.update(schema_version=2, policy_version=2,
                           plan_manifest=copy.deepcopy(compiled.plan_manifest),
                           source_fingerprint=compiled.source_fingerprint,
                           requires_upstream=endpoint is not None)
        # begin is after checks and before disruption. Its strict CAS catches a
        # draft writer outside the manager lock without publishing stale data.
        try:
            if kind == "reconcile":
                self.store.begin_reconcile(snapshot["revision"], transaction_id,
                                           expected_core_revision=active["core_revision"], runtime_context=context)
            else:
                self.store.begin_apply(snapshot["revision"], transaction_id, runtime_context=context)
        except ProxyCapacityError:
            # Admission failed before any journal write or runtime disruption.
            # Retained history is healthy; do not classify a full store as
            # corruption and stop the still-valid previous runtime.
            raise
        except ProxyStorageError:
            # No activation occurred, but an uncertain journal must not survive
            # alongside an auto-recoverable process whose ownership is unclear.
            self._stop()
            raise ProxyApplyError("RECOVERY_REQUIRED") from None
        try:
            self.runtime.apply(compiled.config, activate=True, rollback_on_failure=False)
            self._verify(compiled)
            self.store.commit_apply(transaction_id, context["candidate_core_revision"])
        except Exception:
            self._failed(transaction_id)
        except BaseException:
            # Cooperative cancellation is not a successful transaction. Leave
            # the durable pending journal, stop, and let startup recover it.
            self._stop()
            raise
        transaction = self.store.transaction(transaction_id)
        return self._public(transaction=transaction, compiled=compiled, phase="committed", applied=True)

    def check(self, expected_revision, rows=None, *, expected_source_fingerprint=None):
        with self.lock:
            rows = self._rows(rows)
            self._validate_saved_source(rows, self._load_active())
            snapshot = self._load_draft(expected_revision)
            plan = policy_plan(rows, snapshot["settings"])
            self._bind_source(plan, expected_source_fingerprint)
            compiled, _, _ = self._compile(snapshot, rows)
            self._checked(compiled)
            result = self._public(compiled=compiled)
            result["revision"] = expected_revision
            return result

    def apply(self, expected_revision, transaction_id, rows=None, *, expected_source_fingerprint=None):
        with self.lock:
            rows = self._rows(rows)
            active = self._load_active()
            self._validate_saved_source(rows, active)
            existing = self._existing(transaction_id, expected_revision, kind="apply", rows=rows,
                                      expected_source_fingerprint=expected_source_fingerprint)
            if existing is not None:
                return existing
            snapshot = self._load_draft(expected_revision)
            plan = policy_plan(rows, snapshot["settings"])
            self._bind_source(plan, expected_source_fingerprint, required=True)
            compiled, endpoint, addresses = self._compile(snapshot, rows)
            return self._activate(snapshot, active, compiled, endpoint, addresses, transaction_id, kind="apply")

    def reconcile(self, rows=None, transaction_id=None, *, activate=True):
        with self.lock:
            snapshot = self._load_active()
            rows = self._rows(rows)
            plan = self._validate_saved_source(rows, snapshot)
            if snapshot is None:
                if not plan.legacy:
                    raise ProxyApplyError("MISSING_ACTIVE_CONTEXT")
                return self.runtime.apply(self.build_config(rows), activate=activate)
            transaction_id = transaction_id or "reconcile-" + uuid.uuid4().hex
            existing = self._existing(transaction_id, snapshot["revision"], kind="reconcile", rows=rows)
            if existing is not None:
                return existing
            if snapshot["pending"] is not None:
                raise ProxyConflict("An apply is pending recovery")
            compiled, endpoint, addresses = self._compile(snapshot, rows, active=True, fresh_pin=True)
            if not activate:
                self._checked(compiled)
                return self._public(compiled=compiled)
            return self._activate(snapshot, snapshot, compiled, endpoint, addresses, transaction_id, kind="reconcile")

    def _rollback_guard(self, active):
        if active is None:
            return
        context = active.get("runtime_context")
        if not self.store.requires_upstream(active["settings"], context):
            return
        if context is None or set(self.address_provider(self.panel_origin)) != set(context["self_addresses"]):
            raise ProxyApplyError("RECOVERY_REQUIRED")
        # During a failed node edit, the exact old config still protects its
        # exact old ingress set. A saved newer database remains unapplied; do
        # not publish it or restore a config with a stale self-address deny set.

    def _recovery_guard(self, active):
        rows = self._rows(None)
        if active is None:
            from app.services.proxy_store import _defaults
            if not policy_plan(rows, _defaults()).legacy:
                raise ProxyApplyError("MISSING_ACTIVE_CONTEXT")
            return
        context = active.get("runtime_context")
        plan = policy_plan(rows, active["settings"])
        if (context is None and not plan.requires_upstream and plan.legacy):
            return
        # Recompile from current rows/self addresses but the saved pin BEFORE
        # launching any old config. Newly local addresses or an unbound/new
        # ingress cannot be hidden by restoring a formerly safe configuration.
        if not active.get("runtime_context"):
            raise ProxyApplyError("MISSING_ACTIVE_CONTEXT")
        compiled, _, _ = self._compile(active, rows, active=True)
        if (revision(compiled.config) != active["core_revision"]
                or compiled.identity != context["policy_identity"]
                or context["schema_version"] == 2 and (
                    compiled.plan_manifest != context["plan_manifest"]
                    or compiled.source_fingerprint != context["source_fingerprint"])
                or context["schema_version"] == 1 and not plan.legacy):
            raise ProxyApplyError("RECOVERY_REQUIRED")

    def recover(self):
        """Resolve journal before core auto-recovery; unknown state stops core."""
        with self.lock:
            try:
                pending = self.store.recoverable_pending()
                if pending is not None:
                    if pending.get("phase") != "prepared":
                        raise ProxyApplyError("RECOVERY_REQUIRED")
                    self._recovery_guard(self.store.load_active())
                    self._restore(pending)
                    self.store.abort_apply(pending["transaction_id"])
                    transaction = self.store.transaction(pending["transaction_id"])
                    return self._public(transaction=transaction, phase="aborted", recovered=True)
                active = self._load_active()
                self._baseline(active)
                self._recovery_guard(active)
                self.runtime.recover()
                state = self.runtime.state()
                if state["enabled"] and not self.runtime.status()["running"]:
                    raise ProxyApplyError("RECOVERY_REQUIRED")
                return self._public(phase="recovered", recovered=True)
            except Exception:
                self._stop()
                raise ProxyApplyError("RECOVERY_REQUIRED") from None
