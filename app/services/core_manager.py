from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Iterable

from app.models.database import Inbound
from app.services.core_runtime import CoreRuntime, CoreError, revision

DATA_DIR = Path(os.getenv("VUI_DATA_DIR", "data"))
BIN_DIR = Path(os.getenv("VUI_BIN_DIR", "bin"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _runtime_mapping(value: dict | None) -> dict:
    return {key: item for key, item in dict(value or {}).items() if not key.startswith("_")}


def _modern_xray_settings(protocol: str, value: dict | None) -> dict:
    settings = _runtime_mapping(value)
    # Pinned Xray v26.3.27 infra/conf/vless.go uses clients. Preserve stored
    # draft users rows without destructively migrating the user's database.
    if protocol in {"vless", "vmess", "trojan"} and "users" in settings:
        if "clients" in settings and settings["clients"] != settings["users"]:
            raise CoreError("Conflicting Xray credential representations")
        settings["clients"] = settings.pop("users")
    return settings


def _modern_xray_stream(value: dict | None) -> dict:
    stream = _runtime_mapping(value)
    method = stream.pop("method", stream.get("network", "tcp"))
    stream["network"] = {"raw": "raw", "websocket": "ws", "direct": "tcp"}.get(method, method)
    return stream


class BaseCoreAdapter:
    name = ""
    binary_name = ""
    config_filename = ""

    def __init__(self) -> None:
        self._runtime = None

    @property
    def runtime(self) -> CoreRuntime:
        if self._runtime is None:
            self._runtime = CoreRuntime(self.name, self.binary_path, DATA_DIR / "runtime" / self.name)
        return self._runtime

    @property
    def binary_path(self) -> Path:
        return BIN_DIR / self.binary_name

    @property
    def config_path(self) -> Path | None:
        return self.runtime.config_path()

    def stop(self) -> None:
        self.runtime.stop()

    def status(self) -> dict:
        return self.runtime.status()


class XrayAdapter(BaseCoreAdapter):
    name = "xray"
    binary_name = "xray"
    config_filename = "xray.json"

    def build_config(self, inbounds: Iterable[Inbound]) -> dict:
        built = []
        for item in inbounds:
            if not item.enable:
                continue
            inbound = {"tag": item.tag or f"xray-{item.protocol}-{item.port}",
                "listen": "0.0.0.0", "port": item.port, "protocol": item.protocol,
                "settings": _modern_xray_settings(item.protocol, item.settings)}
            stream = _modern_xray_stream(item.stream_settings)
            if stream:
                inbound["streamSettings"] = stream
            built.append(inbound)
        return {"log": {"loglevel": "warning"}, "inbounds": built,
            "outbounds": [{"protocol": "freedom", "tag": "direct", "settings": {}},
                          {"protocol": "blackhole", "tag": "blocked", "settings": {}}]}


class SingBoxAdapter(BaseCoreAdapter):
    name = "sing-box"
    binary_name = "sing-box"
    config_filename = "sing-box.json"

    def build_config(self, inbounds: Iterable[Inbound]) -> dict:
        built = []
        for item in inbounds:
            if not item.enable:
                continue
            inbound = {"type": item.protocol, "tag": item.tag or f"sing-box-{item.protocol}-{item.port}",
                "listen": "::", "listen_port": item.port}
            settings = _runtime_mapping(item.settings)
            stream = _runtime_mapping(item.stream_settings)
            if item.protocol in {"vless", "vmess", "trojan", "hysteria2", "tuic"}:
                users = settings.pop("users", None)
                if users:
                    inbound["users"] = users
            inbound.update(settings)
            for key in ("tls", "transport", "multiplex"):
                if key in stream:
                    inbound[key] = stream[key]
            transport = inbound.get("transport")
            if item.protocol == "vless" and isinstance(transport, dict) and transport.get("type") == "ws":
                # The existing stored Host field is client routing metadata.
                # sing-box does not validate it as a server Host allowlist;
                # do not send it back as a server response header either.
                transport = dict(inbound["transport"])
                if isinstance(transport.get("headers"), dict):
                    headers = dict(transport["headers"])
                    headers.pop("Host", None)
                    if headers:
                        transport["headers"] = headers
                    else:
                        transport.pop("headers", None)
                inbound["transport"] = transport
            built.append(inbound)
        return {"log": {"level": "warn", "timestamp": True}, "inbounds": built,
            "outbounds": [{"type": "direct", "tag": "direct"}], "route": {"final": "direct"}}


class CoreManager:
    def __init__(self) -> None:
        self.adapters = {"xray": XrayAdapter(), "sing-box": SingBoxAdapter()}
        # All panel-mediated ingress mutations, proxy transitions and core
        # operations share this process lock. SQLite and the runtime journal
        # remain separate commit domains; a saved row is not an applied row.
        self.lock = threading.RLock()
        self._proxy_coordinator = None

    def get(self, core: str) -> BaseCoreAdapter:
        if core not in self.adapters:
            raise CoreError("Unsupported core")
        return self.adapters[core]

    @property
    def proxy_coordinator(self):
        if self._proxy_coordinator is not None:
            return self._proxy_coordinator
        from app.services.proxy_apply import ProxyApplyCoordinator
        from app.services.proxy_store import ProxyStore
        # Resolve the configured data directory at operation time, just as the
        # draft API does. No cached private snapshot may survive a state change.
        return ProxyApplyCoordinator(
            ProxyStore(), self.get("sing-box").runtime,
            lambda rows: self.get("sing-box").build_config(
                item for item in rows if item.core == "sing-box"),
            self.lock, rows_provider=self._all_rows,
            panel_origin=os.getenv("VUI_PUBLIC_ORIGIN"),
        )

    @staticmethod
    def _all_rows() -> list[Inbound]:
        from app.models import database
        with database.SessionLocal() as db:
            return db.query(Inbound).order_by(Inbound.id).all()

    def _stop_untrusted(self, message: str) -> None:
        for adapter in self.adapters.values():
            try:
                adapter.runtime.stop(persist=False)
            except (CoreError, OSError):
                pass
            adapter.runtime.last_error = message

    def _compiled_active(self, rows):
        from app.services.proxy_store import ProxyConflict, ProxyStorageError, ProxyValidationError
        from app.services.proxy_policy import PolicyError
        from app.services.proxy_resolution import ResolutionError
        try:
            return self.proxy_coordinator.compile_active(rows)
        except CoreError as exc:
            if getattr(exc, "code", None) in {"RECOVERY_REQUIRED", "RUNTIME_MISMATCH", "MISSING_ACTIVE_CONTEXT"}:
                self._stop_untrusted("Proxy state is unavailable; core operations blocked")
            raise
        except (ProxyConflict, ProxyStorageError, ProxyValidationError, PolicyError, ResolutionError):
            raise CoreError("Active proxy policy is unavailable or rejects this ingress snapshot") from None

    def validate_inbound_change(self, rows: Iterable[Inbound]) -> None:
        """Guard the complete prospective DB snapshot before its commit.

        Compilation checks all cores, qualification and precise source binding.
        It cannot activate a newer draft, and makes no upstream DNS lookup.
        Runtime failure after the later DB commit is reported as saved/not
        applied; it never permits a fallback to an ordinary sing-box config.
        """
        with self.lock:
            self._compiled_active(list(rows))

    def _check_proxy_runtime_gate(self, coordinator, expected_revision: str) -> None:
        from app.services.proxy_store import ProxyConflict
        draft = coordinator.store.load_draft()
        if draft["revision"] != expected_revision:
            raise ProxyConflict("Proxy draft changed before apply")
        if draft["settings"]["relay_enabled"]:
            # A previous failed Xray delete/disable can leave an older direct
            # runtime even though its DB rows are now disabled. That runtime
            # must not survive an all-ingress relay claim.
            xray = self.get("xray").runtime
            if xray.state()["enabled"] or xray.status()["running"]:
                raise CoreError("Stop the remaining Xray runtime before enabling relay")

    def check_proxy(self, expected_revision: str) -> dict:
        from app.services.proxy_store import ProxyStorageError
        with self.lock:
            try:
                coordinator = self.proxy_coordinator
                self._check_proxy_runtime_gate(coordinator, expected_revision)
                return coordinator.check(expected_revision, self._all_rows())
            except ProxyStorageError:
                self._stop_untrusted("Proxy state is unavailable; core operations blocked")
                raise

    def apply_proxy(self, expected_revision: str, transaction_id: str) -> dict:
        from app.services.proxy_store import ProxyStorageError
        with self.lock:
            try:
                coordinator = self.proxy_coordinator
                if coordinator.store.transaction(transaction_id) is None:
                    self._check_proxy_runtime_gate(coordinator, expected_revision)
                # Existing IDs are resolved before draft inspection, preserving
                # idempotent replay after a newer draft has been saved.
                return coordinator.apply(expected_revision, transaction_id, self._all_rows())
            except ProxyStorageError:
                self._stop_untrusted("Proxy state is unavailable; core operations blocked")
                raise

    def _apply_rows(self, core: str, rows: list[Inbound], *, activate: bool) -> dict:
        adapter = self.get(core)
        compiled = self._compiled_active(rows)
        if core == "sing-box" and compiled is not None:
            return self.proxy_coordinator.reconcile(rows, activate=activate)
        # Xray mutations cannot reset the independent sing-box policy. They
        # still pass the complete all-core qualification gate when relay is ON.
        config = adapter.build_config(item for item in rows if item.core == core)
        return adapter.runtime.apply(config, activate=activate)

    def apply(self, core: str, inbounds: Iterable[Inbound], restart: bool = True) -> dict:
        with self.lock:
            self.get(core)
            supplied = list(inbounds)
            if any(item.core != core for item in supplied):
                raise CoreError("Inbound snapshot contains another core")
            # Callers supplying only one core must not hide enabled rows from
            # the other core from the global relay qualification gate.
            rows = [item for item in self._all_rows() if item.core != core] + supplied
            return self._apply_rows(core, rows, activate=restart)

    def apply_database(self, core: str, *, activate: bool = True) -> dict:
        with self.lock:
            return self._apply_rows(core, self._all_rows(), activate=activate)

    def status(self) -> dict:
        with self.lock:
            rows = self._all_rows()
            result = {}
            try:
                compiled = self._compiled_active(rows)
                policy_error = None
            except CoreError as exc:
                compiled, policy_error = None, str(exc)
            for name, adapter in self.adapters.items():
                status = adapter.status()
                if policy_error:
                    status.update(desired_revision=None, dirty=True, policy_error=policy_error)
                else:
                    config = (compiled.config if name == "sing-box" and compiled is not None
                              else adapter.build_config(item for item in rows if item.core == name))
                    desired = revision(config)
                    status.update(desired_revision=desired, dirty=desired != status["applied_revision"])
                result[name] = status
            return result

    def recover_all(self):
        with self.lock:
            try:
                # Resolve the proxy journal and committed strategy before ANY
                # core may auto-start. Corruption never implies default OFF.
                coordinator = self.proxy_coordinator
                coordinator.recover()
                active = coordinator.store.load_active()
                if active is not None and active["settings"]["relay_enabled"]:
                    # No old Xray commit is eligible for relay. Keep stopped
                    # even when desired DB deletion had failed to apply earlier.
                    self.get("xray").runtime.stop(persist=False)
                    return
            except (CoreError, ValueError, OSError):
                self._stop_untrusted("Proxy recovery failed; automatic core startup blocked")
                return
            try:
                self.get("xray").runtime.recover()
            except CoreError as exc:
                self.get("xray").runtime.last_error = str(exc)

    def stop(self, core: str) -> dict:
        with self.lock:
            adapter = self.get(core)
            adapter.stop()
            return adapter.status()

    def stop_all(self) -> None:
        with self.lock:
            for adapter in self.adapters.values():
                if adapter._runtime is not None:
                    adapter.runtime.close()
                    adapter._runtime = None


core_manager = CoreManager()
