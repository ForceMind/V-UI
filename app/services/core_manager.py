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
            built.append(inbound)
        return {"log": {"level": "warn", "timestamp": True}, "inbounds": built,
            "outbounds": [{"type": "direct", "tag": "direct"}], "route": {"final": "direct"}}


class CoreManager:
    def __init__(self) -> None:
        self.adapters = {"xray": XrayAdapter(), "sing-box": SingBoxAdapter()}
        self.lock = threading.RLock()

    def get(self, core: str) -> BaseCoreAdapter:
        if core not in self.adapters:
            raise CoreError("Unsupported core")
        return self.adapters[core]

    def apply(self, core: str, inbounds: Iterable[Inbound], restart: bool = True) -> dict:
        with self.lock:
            adapter = self.get(core)
            return adapter.runtime.apply(adapter.build_config(inbounds), activate=restart)

    def apply_database(self, core: str, *, activate: bool = True) -> dict:
        from app.models import database
        with self.lock, database.SessionLocal() as db:
            rows = db.query(Inbound).filter(Inbound.core == core).order_by(Inbound.id).all()
            return self.apply(core, rows, restart=activate)

    def status(self) -> dict:
        from app.models import database
        with self.lock, database.SessionLocal() as db:
            result = {}
            for name, adapter in self.adapters.items():
                status = adapter.status()
                rows = db.query(Inbound).filter(Inbound.core == name).order_by(Inbound.id).all()
                desired = revision(adapter.build_config(rows))
                status.update(desired_revision=desired, dirty=desired != status["applied_revision"])
                result[name] = status
            return result

    def recover_all(self):
        for adapter in self.adapters.values():
            try:
                adapter.runtime.recover()
            except CoreError as exc:
                adapter.runtime.last_error = str(exc)

    def stop_all(self) -> None:
        for adapter in self.adapters.values():
            if adapter._runtime is not None:
                adapter.runtime.close()
                adapter._runtime = None


core_manager = CoreManager()
