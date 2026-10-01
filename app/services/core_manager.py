from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Iterable

from app.models.database import Inbound

DATA_DIR = Path(os.getenv("VUI_DATA_DIR", "data"))
BIN_DIR = Path(os.getenv("VUI_BIN_DIR", "bin"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


class CoreError(RuntimeError):
    pass


class BaseCoreAdapter:
    name = ""
    binary_name = ""
    config_filename = ""

    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self._lock = threading.RLock()

    @property
    def binary_path(self) -> Path:
        suffix = ".exe" if os.name == "nt" else ""
        return BIN_DIR / f"{self.binary_name}{suffix}"

    @property
    def config_path(self) -> Path:
        return DATA_DIR / self.config_filename

    def build_config(self, inbounds: Iterable[Inbound]) -> dict:
        raise NotImplementedError

    def command(self) -> list[str]:
        raise NotImplementedError

    def check_command(self) -> list[str]:
        raise NotImplementedError

    def write_config(self, inbounds: Iterable[Inbound]) -> Path:
        config = self.build_config(inbounds)
        tmp_path = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(self.config_path)
        return self.config_path

    def validate_config(self) -> tuple[bool, str]:
        if not self.binary_path.exists():
            return False, f"{self.name} binary not found: {self.binary_path}"
        if not self.config_path.exists():
            return False, f"{self.name} config not found: {self.config_path}"
        result = subprocess.run(
            self.check_command(),
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode == 0, output.strip()

    def start(self) -> None:
        with self._lock:
            if not self.binary_path.exists():
                raise CoreError(f"{self.name} binary not found: {self.binary_path}")
            if not self.config_path.exists():
                raise CoreError(f"{self.name} config not found: {self.config_path}")

            ok, output = self.validate_config()
            if not ok:
                raise CoreError(f"{self.name} config validation failed: {output}")

            self.stop()
            self.process = subprocess.Popen(
                self.command(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def stop(self) -> None:
        with self._lock:
            if not self.process:
                return
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            self.process = None

    def restart(self) -> None:
        self.start()

    def status(self) -> dict:
        running = bool(self.process and self.process.poll() is None)
        return {
            "name": self.name,
            "binary_exists": self.binary_path.exists(),
            "binary_path": str(self.binary_path),
            "config_exists": self.config_path.exists(),
            "config_path": str(self.config_path),
            "running": running,
            "pid": self.process.pid if running and self.process else None,
        }


class XrayAdapter(BaseCoreAdapter):
    name = "xray"
    binary_name = "xray"
    config_filename = "xray.json"

    def build_config(self, inbounds: Iterable[Inbound]) -> dict:
        built = []
        for item in inbounds:
            if not item.enable:
                continue
            inbound = {
                "tag": item.tag or f"xray-{item.protocol}-{item.port}",
                "listen": "0.0.0.0",
                "port": item.port,
                "protocol": item.protocol,
                "settings": item.settings or {},
            }
            if item.stream_settings:
                inbound["streamSettings"] = item.stream_settings
            built.append(inbound)

        return {
            "log": {"loglevel": "warning"},
            "inbounds": built,
            "outbounds": [
                {"protocol": "freedom", "tag": "direct", "settings": {}},
                {"protocol": "blackhole", "tag": "blocked", "settings": {}},
            ],
        }

    def command(self) -> list[str]:
        return [str(self.binary_path), "run", "-c", str(self.config_path)]

    def check_command(self) -> list[str]:
        return [
            str(self.binary_path),
            "run",
            "-test",
            "-c",
            str(self.config_path),
        ]


class SingBoxAdapter(BaseCoreAdapter):
    name = "sing-box"
    binary_name = "sing-box"
    config_filename = "sing-box.json"

    def build_config(self, inbounds: Iterable[Inbound]) -> dict:
        built = []
        for item in inbounds:
            if not item.enable:
                continue

            inbound = {
                "type": item.protocol,
                "tag": item.tag or f"sing-box-{item.protocol}-{item.port}",
                "listen": "::",
                "listen_port": item.port,
            }
            settings = dict(item.settings or {})
            stream = dict(item.stream_settings or {})

            if item.protocol in {"vless", "vmess", "trojan", "hysteria2", "tuic"}:
                users = settings.pop("users", None)
                if users:
                    inbound["users"] = users

            inbound.update(settings)
            for key in ("tls", "transport", "multiplex"):
                if key in stream:
                    inbound[key] = stream[key]

            built.append(inbound)

        return {
            "log": {"level": "warn", "timestamp": True},
            "inbounds": built,
            "outbounds": [{"type": "direct", "tag": "direct"}],
            "route": {"final": "direct"},
        }

    def command(self) -> list[str]:
        return [str(self.binary_path), "run", "-c", str(self.config_path)]

    def check_command(self) -> list[str]:
        return [str(self.binary_path), "check", "-c", str(self.config_path)]


class CoreManager:
    def __init__(self) -> None:
        self.adapters = {
            "xray": XrayAdapter(),
            "sing-box": SingBoxAdapter(),
        }

    def get(self, core: str) -> BaseCoreAdapter:
        try:
            return self.adapters[core]
        except KeyError as exc:
            raise CoreError(f"Unsupported core: {core}") from exc

    def apply(self, core: str, inbounds: Iterable[Inbound], restart: bool = True) -> dict:
        adapter = self.get(core)
        adapter.write_config(inbounds)
        ok, validation_output = adapter.validate_config()
        if restart and ok:
            adapter.restart()
        result = adapter.status()
        result["valid"] = ok
        result["validation_output"] = validation_output
        return result

    def status(self) -> dict:
        return {name: adapter.status() for name, adapter in self.adapters.items()}

    def stop_all(self) -> None:
        for adapter in self.adapters.values():
            adapter.stop()


core_manager = CoreManager()
