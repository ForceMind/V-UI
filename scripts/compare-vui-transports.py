#!/usr/bin/env python3
"""Compare the V-UI release's TLS and REALITY profiles on one Linux runner."""

import argparse
import asyncio
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import runpy
import socket
import ssl
import statistics
import subprocess
import sys
import tempfile
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


b = SimpleNamespace(**runpy.run_path(str(Path(__file__).with_name("compare-zh-reality.py"))))
MODES = ("ws_tls", "tcp_tls", "reality_vision")
WS_PATH = "/vui-transport-benchmark"


def seed_tls(api, mode, sni, cert, key):
    profile = {"security": "tls", "transport": "ws" if mode == "ws_tls" else "direct",
               "flow": "", "server_name": sni, "certificate_path": str(cert), "key_path": str(key)}
    if mode == "ws_tls":
        profile.update(path=WS_PATH, host=sni)
    api("/api/inbounds", {"core": "sing-box", "protocol": "vless", "remark": mode,
                         "port": b.INBOUND, "settings": {"users": [{"uuid": b.UID}]}, "profile": profile})


def verify_tls(config, mode, sni, cert, key):
    inbound, = config["inbounds"]
    user, = inbound["users"]
    tls = inbound["tls"]
    if ((inbound["type"], inbound["listen_port"], user["uuid"], user.get("flow", ""),
         tls["enabled"], tls["server_name"], tls["certificate_path"], tls["key_path"])
            != ("vless", b.INBOUND, b.UID, "", True, sni, str(cert), str(key))
            or tls.get("reality")):
        raise RuntimeError(f"{mode} has unexpected users or TLS settings")
    expected_transport = {"type": "ws", "path": WS_PATH} if mode == "ws_tls" else None
    if inbound.get("transport") != expected_transport:
        raise RuntimeError(f"{mode} has unexpected transport settings")


def tls_client(mode, sni, ca, unrelated_ca):
    config = b.client_config("", sni)
    for outbound, root in zip(config["outbounds"], (ca, unrelated_ca), strict=True):
        outbound["settings"]["vnext"][0]["users"][0]["flow"] = ""
        stream = {"network": "raw", "security": "tls", "tlsSettings": {
            "serverName": sni, "minVersion": "1.3", "maxVersion": "1.3", "alpn": ["http/1.1"],
            "disableSystemRoot": True, "certificates": [{"usage": "verify", "certificateFile": str(root)}],
        }}
        if mode == "ws_tls":
            stream.update(network="ws", wsSettings={"path": WS_PATH, "host": sni})
        outbound["streamSettings"] = stream
    return config


async def wait_listener(address, port):
    for _ in range(40):
        try:
            with socket.create_connection((address, port), timeout=0.25):
                return
        except OSError:
            await asyncio.sleep(0.25)
    raise RuntimeError(f"listener on port {port} did not become ready")


async def compare(args, directory, reference):
    payload = args.vui / "payload"
    singbox, = (payload / "cores").glob("*/sing-box")
    env = b.child_environment()
    panel_command = [sys.executable, "-B", payload / "main.py"]
    admin = ("import json,sys; from app.models.database import init_db; "
             "from app.services.auth_service import provision_admin; init_db(); provision_admin(**json.load(sys.stdin))")
    for name in ("reference", "origin"):
        (directory / name).mkdir()
    ca, cert, key = reference["reference_certificates"](directory / "reference")
    origin_ca, origin_cert, origin_key = reference["reference_certificates"](directory / "origin")
    sni, hostname = reference["SNI"], reference["WRONG_SNI"]
    origin_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    origin_tls.load_cert_chain(origin_cert, origin_key)
    origin_tls.minimum_version = ssl.TLSVersion.TLSv1_3
    origin_client = ssl.create_default_context(cafile=str(origin_ca))
    origin_client.minimum_version = ssl.TLSVersion.TLSv1_3

    release_versions = {"xray": subprocess.check_output([args.xray, "version"], text=True).splitlines()[0],
                        "sing_box": subprocess.check_output([singbox, "version"], text=True).splitlines()[0]}
    if not release_versions["xray"].startswith("Xray 26.10.21 ") or release_versions["sing_box"] != "sing-box version 1.14.2":
        raise RuntimeError("the released client's or server's core version changed")
    metadata = {"vui": "0.4.8", "versions": release_versions,
                "runner": {"platform": platform.platform(), "logical_cpus": os.cpu_count(),
                           "cpu_model": next(line.split(":", 1)[1].strip()
                                             for line in Path("/proc/cpuinfo").read_text().splitlines()
                                             if line.startswith("model name"))},
                "sha256": {name: b.digest(path) for name, path in (
                    ("sing_box", singbox), ("vui_main", payload / "main.py"), ("xray", args.xray),
                    ("load_driver", Path(b.__file__)))},
                "protocol": "vless", "application": "HTTPS/TLS1.3", "per_connection_block_bytes": len(b.BODY),
                "concurrent_block_interval_seconds": 0.05, "seconds_per_sample": b.SECONDS,
                "without_default_route": True, "origin_unreachable_directly_from_client": True,
                "workloads": {}, "profiles": {}}
    origin = b.Origin()
    account = pwd.getpwuid(os.getuid()).pw_name
    configs, environments = {}, {}
    async with await asyncio.start_server(origin.serve, "127.0.0.1", b.ORIGIN, ssl=origin_tls):
        b.verify_namespaces()
        with contextlib.ExitStack() as stack:
            reference_port, _ = reference["start_reference_target"](stack, cert, key)
            for mode in MODES:
                data = directory / mode
                data.mkdir()
                panel_env = dict(env, TZ="UTC", VUI_DATA_DIR=str(data), VUI_BIN_DIR=str(singbox.parent),
                                 VUI_HOST="127.0.0.1", VUI_PORT=str(b.PANEL),
                                 VUI_PUBLIC_ORIGIN=f"http://127.0.0.1:{b.PANEL}", PYTHONPATH=str(payload))
                environments[mode] = panel_env
                subprocess.run([sys.executable, "-B", "-c", admin], input=json.dumps(b.LOGIN).encode(),
                               env=panel_env, cwd=payload, stdout=subprocess.DEVNULL, check=True)
                print(f"preparing {mode} through the released panel", flush=True)
                with b.process(panel_command, directory, panel_env, f"{mode}-seed.log") as panel:
                    api = b.vui_api()
                    if mode == "reality_vision":
                        b.seed_vui(api, sni, reference_port)
                        core = b.running_core(panel, singbox)
                        private = b.verify_server(b.active_config(core), "vui", sni, reference_port)
                        pair = X25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(private + "="))
                        public = base64.urlsafe_b64encode(pair.public_key().public_bytes_raw()).decode().rstrip("=")
                        client = b.client_config(public, sni)
                    else:
                        seed_tls(api, mode, sni, cert, key)
                        client = tls_client(mode, sni, ca, origin_ca)
                configs[mode] = directory / f"{mode}-client.json"
                configs[mode].write_text(json.dumps(client, sort_keys=True))
                metadata["profiles"][mode] = {"transport": "websocket" if mode == "ws_tls" else "tcp/raw",
                                              "security": "reality" if mode == "reality_vision" else "tls1.3",
                                              "flow": b.FLOW if mode == "reality_vision" else "",
                                              "client_config_sha256": b.digest(configs[mode])}

            async def run(mode, trial, workload=None):
                print(f"running {mode} {trial}", flush=True)
                panel_log = f"{mode}-{trial}.log"
                client_log = f"{mode}-{trial}-client.log"
                client_command = b.ns_command("runuser", "-u", account, "--", args.xray, "run", "-c", configs[mode])
                with b.process(panel_command, directory, environments[mode], panel_log) as panel:
                    b.vui_api()("/api/cores/sing-box/restart", {})
                    core = b.running_core(panel, singbox)
                    active = b.active_config(core)
                    if mode == "reality_vision":
                        if b.verify_server(active, "vui", sni, reference_port) != private:
                            raise RuntimeError("the REALITY key changed after seeding")
                    else:
                        verify_tls(active, mode, sni, cert, key)
                    active_hash = hashlib.sha256(json.dumps(active, sort_keys=True).encode()).hexdigest()
                    profile = metadata["profiles"][mode]
                    if profile.setdefault("active_config_sha256", active_hash) != active_hash:
                        raise RuntimeError(f"{mode} configuration changed across trials")
                    await wait_listener(b.SERVER, b.INBOUND)
                    with b.process(client_command, directory, env, client_log, client=True):
                        try:
                            await wait_listener(b.CLIENT, b.SOCKS)
                            if workload:
                                return await b.measure(origin_client, hostname, workload, panel.pid, core)
                            negative = "wrong REALITY public key" if mode == "reality_vision" else "untrusted TLS certificate"
                            await b.smoke(origin, origin_client, hostname, negative)
                            profile["negative_probe"] = negative
                            profile["verified_https_and_negative_probe"] = True
                        except Exception:
                            b.diagnose([directory / client_log, directory / panel_log], 0)
                            raise

            for mode in MODES:
                await run(mode, "smoke")
            print("all profiles delivered verified HTTPS and rejected the negative control", flush=True)
            for workload in ("single", "concurrent"):
                trials = {mode: [] for mode in MODES}
                for index, order in enumerate((MODES, reversed(MODES))):
                    for mode in order:
                        trials[mode].append(await run(mode, f"{workload}-{index}", workload))
                metadata["workloads"][workload] = {mode: {"samples": rows, "median": {
                    field: statistics.median(row[field] for row in rows) for field in rows[0]}}
                    for mode, rows in trials.items()}
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("vui", "xray", "reference-helper", "output"):
        parser.add_argument("--" + name, type=lambda value: Path(value).resolve(), required=True)
    args = parser.parse_args()
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix="vui-transport-comparison-") as directory:
        report = asyncio.run(compare(args, Path(directory), runpy.run_path(str(args.reference_helper))))
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
