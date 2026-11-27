#!/usr/bin/env python3
"""Same-runner REALITY/Vision comparison with a separate client namespace."""

import argparse
import asyncio
import base64
import contextlib
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import pwd
import runpy
import shutil
import signal
import socket
import sqlite3
import ssl
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


basic = runpy.run_path(str(Path(__file__).with_name("compare-zh-traffic.py")))
digest, process_stat = basic["digest"], basic["process_stat"]
FLOW = "xtls-rprx-vision"
CLIENT_NS = "vui-perf-client"
SERVER, CLIENT = "198.18.0.1", "198.18.0.2"
INBOUND, PANEL, ORIGIN, SOCKS, BAD_SOCKS = 28504, 28503, 28505, 28506, 28507
UID = "11111111-1111-4111-8111-111111111111"
SHORT_ID = "0123456789abcdef"
LOGIN = basic["CREDENTIALS"]
BODY = b"v" * 65536
PROBE = b"verified HTTPS origin over REALITY Vision\n"
SECONDS, CONCURRENT = 20, 64


def ns_command(*args):
    return ["sudo", "-n", "ip", "netns", "exec", CLIENT_NS, *map(str, args)]


def child_environment():
    return {key: value for key, value in os.environ.items()
            if not key.startswith(("VUI_", "XUI_", "XRAY_", "PYTHON", "PIP_", "SSL_CERT_"))}


def verify_namespaces():
    for command in (["ip", "route", "show", "default"], ns_command("ip", "route", "show", "default")):
        if subprocess.check_output(command).strip():
            raise RuntimeError("benchmark namespace has a default route")
    check = ("import socket,sys; s=socket.socket(); s.settimeout(1); "
             "sys.exit(0 if s.connect_ex(('127.0.0.1', int(sys.argv[1]))) != 0 else 1)")
    subprocess.run(ns_command("python3", "-c", check, ORIGIN), check=True)


@contextlib.contextmanager
def process(command, cwd, env, log_name, *, client=False):
    with (cwd / log_name).open("wb") as log:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=log, start_new_session=True)
        try:
            yield proc
        finally:
            if client:
                members = subprocess.check_output(["sudo", "-n", "ip", "netns", "pids", CLIENT_NS]).split()
                if members:
                    subprocess.run(["sudo", "-n", "kill", "-TERM", "--", *[p.decode() for p in members]],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=6)
            elif proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if client:
                    members = subprocess.check_output(["sudo", "-n", "ip", "netns", "pids", CLIENT_NS]).split()
                    if members:
                        subprocess.run(["sudo", "-n", "kill", "-KILL", "--", *[p.decode() for p in members]],
                                       check=False, timeout=6)
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=6)


def running_core(parent, binary):
    expected = binary.resolve()
    for _ in range(60):
        if parent.poll() is not None:
            raise RuntimeError("panel exited before the core started")
        for item in Path("/proc").iterdir():
            if not item.name.isdecimal():
                continue
            try:
                if (item / "exe").resolve(strict=True) != expected:
                    continue
                pid = int(item.name)
                ancestor = process_stat(pid)[0]
                # V-UI uses a per-core watchdog between the panel and core.
                if ancestor == parent.pid or process_stat(ancestor)[0] == parent.pid:
                    return pid
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                pass
        time.sleep(0.25)
    raise RuntimeError("no running core descendant with the expected binary")


def active_config(pid):
    args = Path(f"/proc/{pid}/cmdline").read_bytes().decode().rstrip("\0").split("\0")
    for i, arg in enumerate(args):
        if arg in {"-c", "-config"}:
            return json.loads(Path(args[i + 1]).read_text())
    raise RuntimeError("core start command omitted the applied configuration")


def vui_api():
    address = f"http://127.0.0.1:{PANEL}"
    cookie = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(cookie))

    def call(path, data=None):
        headers = {"Origin": address, "X-VUI-Request": "1", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(data).encode()
        with opener.open(urllib.request.Request(address + path, data=data, headers=headers), timeout=12) as response:
            return json.load(response)

    for _ in range(60):
        try:
            call("/api/auth/login", LOGIN)
            return call
        except (ConnectionError, urllib.error.URLError) as failure:
            if isinstance(failure, urllib.error.HTTPError):
                raise RuntimeError(f"V-UI login returned status {failure.code}") from None
            time.sleep(0.25)
    raise RuntimeError("V-UI login did not become ready")


def seed_vui(api, reference_host, reference_port):
    created = api("/api/inbounds", {"core": "sing-box", "protocol": "vless", "remark": "reality-performance",
                    "port": INBOUND, "settings": {"users": [{"uuid": UID}]},
                    "profile": {"security": "reality", "transport": "direct", "flow": FLOW,
                                "reality_target": f"127.0.0.1:{reference_port}", "reality_server_name": reference_host,
                                "client_fingerprint": "chrome", "reality_uuid": UID, "reality_short_id": SHORT_ID}})
    editor = api(f"/api/inbounds/{created['inbound']['id']}/editor")
    if editor.get("core") != "sing-box":
        raise RuntimeError("V-UI editor did not report the sing-box inbound")


def seed_3x(api, private, reference_host, reference_port, error_log):
    route = "panel/api/xray/"
    envelope = api(route, {})
    envelope = json.loads(envelope) if isinstance(envelope, str) else envelope
    template = envelope["xraySetting"]
    template = json.loads(template) if isinstance(template, str) else template
    template["log"] = {"access": "none", "error": str(error_log), "loglevel": "warning"}
    template["outbounds"].append({"tag": "bench-origin", "protocol": "freedom", "settings": {
        "finalRules": [{"action": "allow", "ip": ["127.0.0.1/32"], "port": str(ORIGIN)}, {"action": "block"}]}})
    template["routing"]["rules"].insert(1, {"type": "field", "ip": ["127.0.0.1/32"],
                                             "port": str(ORIGIN), "outboundTag": "bench-origin"})
    api(route + "update", {"xraySetting": json.dumps(template), "outboundTestUrl": ""})
    settings = {"clients": [{"id": UID, "email": basic["account"](1), "enable": True, "flow": FLOW, "totalGB": 0,
                             "expiryTime": 0, "limitIp": 0, "reset": 0}], "decryption": "none"}
    reality = {"target": f"127.0.0.1:{reference_port}", "serverNames": [reference_host],
               "privateKey": private, "shortIds": [SHORT_ID], "show": False}
    api("panel/api/inbounds/add", {"remark": "reality-performance", "enable": "true", "listen": SERVER,
        "port": INBOUND, "protocol": "vless", "up": 0, "down": 0, "total": 0, "expiryTime": 0,
        "settings": json.dumps(settings), "streamSettings": json.dumps({"network": "tcp", "security": "reality",
        "realitySettings": reality, "tcpSettings": {"header": {"type": "none"}}}), "sniffing": '{"enabled":false}'})


def verify_server(config, name, sni, reference_port, private=None):
    builtin = name == "vui"
    inbound, = [item for item in config["inbounds"] if item.get("listen_port", item.get("port")) == INBOUND]
    if builtin:
        user, = inbound["users"]
        tls, reality = inbound["tls"], inbound["tls"]["reality"]
        if (inbound["type"], user["uuid"], user["flow"], tls["server_name"], reality["short_id"], reality["handshake"]) != (
                "vless", UID, FLOW, sni, [SHORT_ID], {"server": "127.0.0.1", "server_port": reference_port}):
            raise RuntimeError("V-UI's active configuration differs from the agreed REALITY settings")
        if tls.get("enabled") is not True or reality.get("enabled") is not True or inbound.get("transport"):
            raise RuntimeError("V-UI did not activate raw TCP REALITY")
        return reality["private_key"]
    user, = inbound["settings"]["clients"]
    stream = inbound["streamSettings"]
    reality = stream["realitySettings"]
    if (inbound["protocol"], user["id"], user["flow"], stream["security"], reality["privateKey"],
            reality["serverNames"], reality["shortIds"], reality.get("target", reality.get("dest"))) != (
            "vless", UID, FLOW, "reality", private, [sni], [SHORT_ID], f"127.0.0.1:{reference_port}"):
        raise RuntimeError("3X-UI's active configuration differs from the agreed REALITY settings")
    if stream["network"] not in {"tcp", "raw"}:
        raise RuntimeError("3X-UI did not activate raw TCP")
    return private


def client_config(public, sni):
    alternate = X25519PrivateKey.generate().public_key().public_bytes_raw()
    wrong = base64.urlsafe_b64encode(alternate).decode().rstrip("=")

    def outbound(tag, key):
        return {"tag": tag, "protocol": "vless", "settings": {"vnext": [{"address": SERVER, "port": INBOUND,
                "users": [{"id": UID, "flow": FLOW, "encryption": "none"}]}]}, "streamSettings": {
                "network": "raw", "security": "reality", "realitySettings": {"fingerprint": "chrome",
                "serverName": sni, "password": key, "shortId": SHORT_ID, "spiderX": "/", "show": False}}}

    return {"log": {"loglevel": "warning", "access": "none"}, "inbounds": [
            {"protocol": "socks", "tag": tag, "listen": CLIENT, "port": port, "settings": {"auth": "noauth", "udp": False}}
            for tag, port in (("good-socks", SOCKS), ("bad-socks", BAD_SOCKS))],
            "outbounds": [outbound("good", public), outbound("bad", wrong)],
            "routing": {"rules": [{"type": "field", "inboundTag": ["bad-socks"], "outboundTag": "bad"}]}}


class Origin:
    def __init__(self):
        self.requests = 0

    async def serve(self, reader, writer):
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 8)
            method, target, _ = request.split(b"\r\n", 1)[0].split()
            if method != b"GET" or target not in (b"/probe", b"/single", b"/concurrent"):
                return
            self.requests += 1
            data = PROBE if target == b"/probe" else BODY
            size = len(data) if target == b"/probe" else len(data) * 2**20
            writer.write(f"HTTP/1.1 200 OK\r\nContent-Length: {size}\r\nConnection: close\r\n\r\n".encode())
            writer.write(data)
            await writer.drain()
            if target == b"/probe":
                return
            while True:
                if target == b"/concurrent":
                    await asyncio.sleep(0.05)
                writer.write(data)
                await writer.drain()
        except (OSError, TimeoutError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()


@contextlib.asynccontextmanager
async def get_https(context, hostname, path, port):
    reader, writer = await asyncio.open_connection(CLIENT, port)
    try:
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        if await asyncio.wait_for(reader.readexactly(2), 5) != b"\x05\x00":
            raise ConnectionError("SOCKS handshake rejected")
        writer.write(b"\x05\x01\x00\x01\x7f\x00\x00\x01" + ORIGIN.to_bytes(2, "big"))
        await writer.drain()
        reply = await asyncio.wait_for(reader.readexactly(4), 8)
        if reply[1] != 0 or reply[3] not in (1, 4):
            raise ConnectionError(f"SOCKS connection rejected (status {reply[1]})")
        await asyncio.wait_for(reader.readexactly(6 if reply[3] == 1 else 18), 8)
        await writer.start_tls(context, server_hostname=hostname, ssl_handshake_timeout=8)
        if writer.get_extra_info("ssl_object").version() != "TLSv1.3":
            raise RuntimeError("origin did not negotiate TLS 1.3")
        writer.write(f"GET {path} HTTP/1.1\r\nHost: {hostname}\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 8)
        if not header.startswith(b"HTTP/1.1 200 "):
            raise RuntimeError("unexpected HTTPS origin status")
        yield reader
    finally:
        writer.close()
        with contextlib.suppress(OSError, TimeoutError):
            await asyncio.wait_for(writer.wait_closed(), 2)


async def smoke(origin, context, hostname, rejected_client="wrong REALITY public key"):
    async with get_https(context, hostname, "/probe", SOCKS) as reader:
        if await asyncio.wait_for(reader.readexactly(len(PROBE)), 5) != PROBE:
            raise RuntimeError("positive probe did not deliver the HTTPS fixture")
    before = origin.requests
    try:
        async with get_https(context, hostname, "/probe", BAD_SOCKS) as reader:
            await asyncio.wait_for(reader.readexactly(len(PROBE)), 5)
    except (OSError, TimeoutError, asyncio.IncompleteReadError):
        pass
    else:
        raise RuntimeError(f"{rejected_client} reached the HTTPS origin")
    if origin.requests != before:
        raise RuntimeError("negative probe reached the HTTPS origin")


def diagnose(paths, client_offset):
    categories = ("reality verification failed", "invalid connection", "connection refused", "connection reset",
                  "no route to host", "timeout", "unknown user", "flow mismatch", "unsupported", "invalid request",
                  "failed to read", "failed to dial", "certificate", "tls handshake", "short id", "reality",
                  "closed pipe", "eof", "unknown authority", "hello retry", "unexpected message", "x25519")
    for index, path in enumerate(paths):
        if path.is_file():
            offset = client_offset if index == 0 else 0
            contents = path.read_bytes()[offset:].decode(errors="replace").casefold()
            print(f"{path.name} categories: " + ", ".join(item for item in categories if item in contents), flush=True)


async def measure(context, hostname, mode, panel_pid, core_pid):
    count = 1 if mode == "single" else CONCURRENT
    received = [0] * count

    async def download(index):
        async with get_https(context, hostname, "/" + mode, SOCKS) as reader:
            while block := await reader.read(len(BODY)):
                if block != BODY[:len(block)]:
                    raise RuntimeError("HTTPS payload failed integrity check")
                received[index] += len(block)
            raise RuntimeError("HTTPS connection ended before the test window")

    tasks = [asyncio.create_task(download(i)) for i in range(count)]
    try:
        for _ in range(90):
            if any(task.done() for task in tasks):
                await next(task for task in tasks if task.done())
            if min(received) > 0:
                break
            await asyncio.sleep(0.25)
        else:
            raise RuntimeError("not all HTTPS connections delivered data")
        await asyncio.sleep(5)
        initial = sum(received)
        panel_before, core_before = process_stat(panel_pid)[1:], process_stat(core_pid)[1:]
        start = time.monotonic()
        await asyncio.sleep(SECONDS)
        elapsed, size = time.monotonic() - start, sum(received) - initial
        panel_after, core_after = process_stat(panel_pid)[1:], process_stat(core_pid)[1:]
        if not size or any(task.done() for task in tasks) or (panel_before[1], core_before[1]) != (panel_after[1], core_after[1]):
            raise RuntimeError("stream or process did not survive the full measurement window")
        panel_cpu, core_cpu = panel_after[0] - panel_before[0], core_after[0] - core_before[0]
        return {"connections": count, "seconds": elapsed, "payload_bytes": size, "mbit_s": size * 8 / elapsed / 1e6,
                "panel_cpu_pct": 100 * panel_cpu / elapsed, "core_cpu_pct": 100 * core_cpu / elapsed,
                "panel_cpu_seconds": panel_cpu, "core_cpu_seconds": core_cpu,
                "panel_cpu_seconds_per_gib": panel_cpu * 1024**3 / size,
                "core_cpu_seconds_per_gib": core_cpu * 1024**3 / size}
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def compare(args, directory, reference):
    payload = args.vui / "payload"
    singbox, = (payload / "cores").glob("*/sing-box")
    xray_dir = directory / "xray-bin"
    shutil.copytree(args.official.parent / "bin", xray_dir)
    xray = xray_dir / "xray-linux-amd64"
    data_3x, data_vui, logs = [directory / name for name in ("db-3x", "db-vui", "logs")]
    for path in (data_3x, data_vui, logs):
        path.mkdir()
    env = child_environment()
    env_3x = dict(env, TZ="UTC", XUI_DB_FOLDER=str(data_3x), XUI_BIN_FOLDER=str(xray_dir), XUI_LOG_FOLDER=str(logs))
    env_vui = dict(env, TZ="UTC", VUI_DATA_DIR=str(data_vui), VUI_BIN_DIR=str(singbox.parent), VUI_HOST="127.0.0.1",
                   VUI_PORT=str(PANEL), VUI_PUBLIC_ORIGIN=f"http://127.0.0.1:{PANEL}", PYTHONPATH=str(payload))
    vui_command = [sys.executable, "-B", payload / "main.py"]
    xin = env_3x
    db = data_3x / "x-ui.db"
    seed = directory / "seed-3x.db"
    handshake_dir, origin_dir = directory / "reference", directory / "origin"
    handshake_dir.mkdir()
    origin_dir.mkdir()
    _, cert, key = reference["reference_certificates"](handshake_dir)
    ca, application_cert, application_key = reference["reference_certificates"](origin_dir)
    sni, hostname = reference["SNI"], reference["WRONG_SNI"]
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.load_cert_chain(application_cert, application_key)
    server_tls.minimum_version = ssl.TLSVersion.TLSv1_3
    context = ssl.create_default_context(cafile=str(ca))
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    application = Origin()
    http = await asyncio.start_server(application.serve, "127.0.0.1", ORIGIN, ssl=server_tls)
    verify_namespaces()
    versions = {"xray": subprocess.check_output([xray, "version"], text=True).splitlines()[0],
                "sing_box": subprocess.check_output([singbox, "version"], text=True).splitlines()[0]}
    if not versions["xray"].startswith("Xray 26.10.21 ") or versions["sing_box"] != "sing-box version 1.14.2":
        raise RuntimeError("release cores differ from the expected versions")
    with contextlib.ExitStack() as stack:
        reference_port, _ = reference["start_reference_target"](stack, cert, key)
        async with http:
            print("Preparing the V-UI inbound through the release API", flush=True)
            admin = "import json,sys; from app.models.database import init_db; from app.services.auth_service import provision_admin; init_db(); provision_admin(**json.load(sys.stdin))"
            subprocess.run([sys.executable, "-B", "-c", admin], input=json.dumps(LOGIN).encode(), env=env_vui,
                           cwd=payload, stdout=subprocess.DEVNULL, check=True)
            with process(vui_command, directory, env_vui, "seed-vui.log") as panel:
                seed_vui(vui_api(), sni, reference_port)
                core = running_core(panel, singbox)
                private = verify_server(active_config(core), "vui", sni, reference_port)
            pair = X25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(private + "="))
            public = base64.urlsafe_b64encode(pair.public_key().public_bytes_raw()).decode().rstrip("=")
            print("Preparing the matching official 3X-UI inbound", flush=True)
            subprocess.run([args.official, "setting", "-username", LOGIN["username"], "-password", LOGIN["password"],
                            "-webBasePath", basic["BASE_PATH"], "-listenIP", "127.0.0.1", "-port", str(PANEL)],
                           cwd=directory, env=xin, stdout=subprocess.DEVNULL, check=True)
            with contextlib.closing(sqlite3.connect(db)) as sql, sql:
                sql.execute("DELETE FROM settings WHERE key = 'subEnable'")
                sql.execute("INSERT INTO settings(key, value) VALUES ('subEnable', 'false')")
            with process([args.official], directory, xin, "seed-3x.log"):
                seed_3x(basic["panel_api"](), private, sni, reference_port, logs / "xray-error.log")
            with contextlib.closing(sqlite3.connect(db)) as old, contextlib.closing(sqlite3.connect(seed)) as new:
                old.backup(new)

            config = client_config(public, sni)
            proxy_config = directory / "client.json"
            encoded = json.dumps(config, sort_keys=True).encode()
            proxy_config.write_bytes(encoded)
            proxy = ns_command("runuser", "-u", pwd.getpwuid(os.getuid()).pw_name, "--", xray, "run", "-c", proxy_config)
            builds = {"vui": (vui_command, env_vui, singbox), "official": ([args.official], xin, xray),
                      "candidate": ([args.candidate], xin, xray)}
            proof = {"vui": None, "official": None, "candidate": None}
            report = {"versions": versions, "common_client": "xray", "sha256": {"sing_box": digest(singbox), "xray": digest(xray),
                      "official_panel": digest(args.official), "candidate_panel": digest(args.candidate),
                      "vui_main": digest(payload / "main.py"), "client_config": hashlib.sha256(encoded).hexdigest(),
                      "3x_sqlite_fixture": digest(seed)}, "verified_settings": {"protocol": "vless", "network": "tcp/raw",
                      "security": "reality", "flow": FLOW, "application": "HTTPS/TLS1.3"},
                      "concurrent_block_bytes": len(BODY), "concurrent_block_interval_seconds": 0.05,
                      "without_default_route": True, "origin_unreachable_directly_from_client": True,
                      "positive_and_bad_key_smoke": proof, "workloads": {}}
            config_hashes = {}
            with process(proxy, directory, env, "client.log", client=True):
                for _ in range(40):
                    try:
                        with socket.create_connection((CLIENT, SOCKS), timeout=0.25):
                            break
                    except OSError:
                        await asyncio.sleep(0.25)
                else:
                    raise RuntimeError("common SOCKS client did not start")

                async def run(name, trial, mode=None):
                    print(f"running {name} {trial}", flush=True)
                    command, environment, binary = builds[name]
                    if name != "vui":
                        for suffix in ("-wal", "-shm"):
                            Path(str(db) + suffix).unlink(missing_ok=True)
                        shutil.copyfile(seed, db)
                    with process(command, directory, environment, f"{name}-{trial}.log") as panel:
                        if name == "vui":
                            vui_api()("/api/cores/sing-box/restart", {})
                        core = running_core(panel, binary)
                        active = active_config(core)
                        verify_server(active, name, sni, reference_port, private)
                        current = hashlib.sha256(json.dumps(active, sort_keys=True).encode()).hexdigest()
                        saved = config_hashes.setdefault("vui" if name == "vui" else "3x", current)
                        if saved != current:
                            raise RuntimeError("applied configuration changed between trials")
                        for _ in range(40):
                            try:
                                with socket.create_connection((SERVER, INBOUND), timeout=0.25):
                                    break
                            except OSError:
                                await asyncio.sleep(0.25)
                        else:
                            raise RuntimeError("the server core did not bind its REALITY listener")
                        if not mode:
                            stages = [directory / "client.log", directory / f"{name}-{trial}.log", logs / "xray-error.log"]
                            client_offset = stages[0].stat().st_size
                            try:
                                await smoke(application, context, hostname)
                            except Exception:
                                diagnose(stages, client_offset)
                                raise
                            proof[name] = True
                            return None
                        return await measure(context, hostname, mode, panel.pid, core)

                for name in builds:
                    await run(name, "smoke")
                print("all three builds verified HTTPS through REALITY Vision, with wrong-key rejection", flush=True)
                for mode in ("single", "concurrent"):
                    samples = {name: [] for name in builds}
                    for repetition in (0, 1):
                        for name in (builds if repetition == 0 else reversed(builds)):
                            samples[name].append(await run(name, f"{mode}-{repetition}", mode))
                    report["workloads"][mode] = {name: {"samples": runs, "median": {
                        key: statistics.median(run[key] for run in runs) for key in runs[0]}} for name, runs in samples.items()}
            report["active_config_sha256"] = config_hashes
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("vui", "official", "candidate", "reference-helper", "output"):
        parser.add_argument("--" + name, type=lambda raw: Path(raw).resolve(), required=True)
    args = parser.parse_args()
    helper = runpy.run_path(str(args.reference_helper))
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix="vui-reality-comparison-") as directory:
        report = asyncio.run(compare(args, Path(directory), helper))
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
