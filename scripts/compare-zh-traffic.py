#!/usr/bin/env python3
"""Compare two 3.11 panel binaries with one Xray and one cloned fixture."""

import argparse
import asyncio
import contextlib
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import statistics
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import uuid


WINDOWS = os.name == "nt"
if WINDOWS:
    import psutil  # Windows process counters and child enumeration

PANEL_PORT, VLESS_PORT, TARGET_PORT = 28503, 28504, 28505
INGRESS = "127.0.0.2" if WINDOWS else "198.18.0.10"
BASE_PATH = "/cpu-fixture/"
CREDENTIALS = {"username": "bench-user", "password": "bench-only-password-523af8"}
QUOTA = 16 * 1024**2
BLOCK = b"z" * 32768
CLOCK_TICKS = None if WINDOWS else os.sysconf("SC_CLK_TCK")


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def account(index):
    return f"bench-{index:04d}@example.invalid"


def identity(index):
    return uuid.uuid5(uuid.NAMESPACE_DNS, account(index))


def process_stat(pid):
    if WINDOWS:
        proc = psutil.Process(pid)
        with proc.oneshot():
            cpu = proc.cpu_times()
            return proc.ppid(), cpu.user + cpu.system, proc.create_time()
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return int(fields[1]), (int(fields[11]) + int(fields[12])) / CLOCK_TICKS, int(fields[19])


def find_core(panel, executable):
    if WINDOWS:
        for child in psutil.Process(panel.pid).children():
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                if Path(child.exe()).resolve() == executable:
                    return child.pid
        return None
    for path in Path("/proc").iterdir():
        if not path.name.isdecimal():
            continue
        try:
            pid = int(path.name)
            if process_stat(pid)[0] == panel.pid and (path / "exe").resolve() == executable:
                return pid
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
    return None


def panel_api():
    address = f"http://127.0.0.1:{PANEL_PORT}{BASE_PATH}"
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar))
    token = None

    def call(path, data=None):
        headers = {"Accept": "application/json"}
        if token:
            headers["X-CSRF-Token"] = token
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            data = urllib.parse.urlencode(data).encode()
        with opener.open(urllib.request.Request(address + path, data=data, headers=headers), timeout=6) as response:
            result = json.load(response)
        if result.get("success") is not True:
            raise RuntimeError(f"panel API rejected {path}")
        return result.get("obj")

    for _ in range(60):
        try:
            token = call("csrf-token")
            break
        except (OSError, ValueError):
            time.sleep(0.25)
    if not token:
        raise RuntimeError("panel did not produce a CSRF token")
    call("login", CREDENTIALS)
    return call


def setup_fixture(api, users):
    route = "panel/api/xray/"
    envelope = api(route, {})
    if isinstance(envelope, str):
        envelope = json.loads(envelope)
    template = envelope["xraySetting"]
    if isinstance(template, str):
        template = json.loads(template)
    template["log"] = {"access": "none", "loglevel": "warning"}
    template["outbounds"].append({"tag": "bench-target", "protocol": "freedom", "settings": {
        "finalRules": [{"action": "allow", "ip": ["127.0.0.1/32"], "port": str(TARGET_PORT)}, {"action": "block"}],
    }})
    template["routing"]["rules"].insert(1, {"type": "field", "ip": ["127.0.0.1/32"],
                                             "port": str(TARGET_PORT), "outboundTag": "bench-target"})
    api(route + "update", {"xraySetting": json.dumps(template), "outboundTestUrl": ""})
    clients = [{"id": str(identity(i)), "email": account(i), "enable": True, "flow": "",
                "expiryTime": 0, "limitIp": 0, "totalGB": QUOTA if i == 0 else 0, "reset": 0}
               for i in range(users + 1)]
    api("panel/api/inbounds/add", {"remark": "cpu-fixture", "enable": "true", "listen": INGRESS,
        "port": VLESS_PORT, "protocol": "vless", "up": 0, "down": 0, "total": 0, "expiryTime": 0,
        "settings": json.dumps({"clients": clients, "decryption": "none"}),
        "streamSettings": json.dumps({"network": "tcp", "security": "none", "tcpSettings": {"header": {"type": "none"}}}),
        "sniffing": '{"enabled":false}'})


@contextlib.contextmanager
def panel(binary, directory, environment, label):
    with (directory / f"{label}.log").open("wb") as log:
        proc = subprocess.Popen([binary], cwd=directory, env=environment, stdout=log, stderr=log,
                                start_new_session=not WINDOWS)
        try:
            yield proc
        finally:
            if proc.poll() is None:
                if WINDOWS:
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=True)
                else:
                    os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                if WINDOWS:
                    proc.kill()
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)


async def serve_target(reader, writer):
    try:
        mode = await reader.readexactly(1)
        if mode == b"p":
            writer.write(b"ok")
            await writer.drain()
            return
        delay = {b"s": 0.0, b"m": 0.08, b"q": 0.0125}[mode]
        while True:
            writer.write(BLOCK)
            await writer.drain()
            if delay:
                await asyncio.sleep(delay)
    except (OSError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()


@contextlib.asynccontextmanager
async def proxy(index, mode):
    reader, writer = await asyncio.open_connection(INGRESS, VLESS_PORT, local_addr=(INGRESS, 0))
    try:
        request = b"\x00" + identity(index).bytes + b"\x00\x01" + TARGET_PORT.to_bytes(2, "big") + b"\x01\x7f\x00\x00\x01"
        writer.write(request + mode)
        await writer.drain()
        version, options = await asyncio.wait_for(reader.readexactly(2), 2)
        if version != 0:
            raise RuntimeError("unexpected VLESS response version")
        await asyncio.wait_for(reader.readexactly(options), 2)
        yield reader
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()


async def probe(index):
    try:
        async with proxy(index, b"p") as reader:
            return await asyncio.wait_for(reader.readexactly(2), 2) == b"ok"
    except (OSError, TimeoutError, asyncio.IncompleteReadError):
        return False


async def ready(proc, executable):
    for _ in range(90):
        if proc.poll() is not None:
            raise RuntimeError("panel exited during startup")
        core = find_core(proc, executable)
        if core and await probe(1):
            return core
        await asyncio.sleep(0.25)
    raise RuntimeError("core did not deliver traffic through the fixture inbound")


async def measure(proc, core, users, duration):
    sample = [0] * users
    started = [asyncio.Event() for _ in sample]
    mode = b"s" if users == 1 else b"m"

    async def download(offset):
        async with proxy(offset + 1, mode) as reader:
            started[offset].set()
            while data := await reader.read(len(BLOCK)):
                sample[offset] += len(data)
            raise RuntimeError("unlimited client stream ended during the measurement")

    def counters():
        p_cpu, p_generation = process_stat(proc.pid)[1:]
        x_cpu, x_generation = process_stat(core)[1:]
        return p_cpu, x_cpu, p_generation, x_generation

    tasks = [asyncio.create_task(download(i)) for i in range(users)]
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), 16)
        await asyncio.sleep(6)
        before = counters()
        begin, initial_byte_count = time.monotonic(), sum(sample)
        await asyncio.sleep(duration)
        seconds, transferred = time.monotonic() - begin, sum(sample) - initial_byte_count
        after = counters()
        if before[2:] != after[2:] or any(task.done() for task in tasks):
            raise RuntimeError("panel/core restarted or a client disconnected during the measurement")
        if not transferred or (users > 1 and min(sample) == 0):
            raise RuntimeError("a workload client received no payload")
        panel_cpu, core_cpu = after[0] - before[0], after[1] - before[1]
        return {"clients": users, "seconds": seconds, "payload_bytes": transferred,
                "mbit_s": transferred * 8 / seconds / 1e6,
                "panel_cpu_pct": panel_cpu * 100 / seconds, "xray_cpu_pct": core_cpu * 100 / seconds,
                "panel_cpu_seconds_per_gib": panel_cpu * 1024**3 / transferred,
                "xray_cpu_seconds_per_gib": core_cpu * 1024**3 / transferred}
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def quota_test(database):
    if not await probe(0):
        raise RuntimeError("quota account inaccessible before crossing the limit")
    crossed, received = None, 0

    async def consume():
        nonlocal crossed, received
        async with proxy(0, b"q") as reader:
            while data := await reader.read(len(BLOCK)):
                received += len(data)
                if crossed is None and received >= QUOTA:
                    crossed = time.monotonic()

    task = asyncio.create_task(consume())
    try:
        started = time.monotonic()
        while time.monotonic() - started < 35:
            with contextlib.closing(sqlite3.connect(database, timeout=2)) as connection:
                enabled, down = connection.execute("SELECT enable, down FROM client_traffics WHERE email = ?", (account(0),)).fetchone()
            if not enabled and crossed is not None and not await probe(0):
                if down < QUOTA or not await probe(1):
                    raise RuntimeError("quota account was not rejected independently of the control account")
                delay = time.monotonic() - crossed
                if delay > 10:
                    raise RuntimeError("quota enforcement took longer than two traffic ticks")
                return {"quota_bytes": QUOTA, "panel_down_bytes": down, "received_before_block_check": received,
                        "overshoot_bytes": max(0, received - QUOTA), "blocked_after_seconds": delay,
                        "new_quota_connection_rejected": True, "control_connection_passed": True,
                        "original_stream_ended_at_check": task.done()}
            await asyncio.sleep(0.2)
        raise RuntimeError("quota was not enforced before timeout")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def compare(args, directory):
    if not WINDOWS:
        route = json.loads(subprocess.check_output(["ip", "-j", "route", "get", INGRESS]))[0]
        if route.get("type") != "local" or route.get("dev") != "lo":
            raise RuntimeError("benchmark ingress must be a local address on lo")
        if subprocess.check_output(["ip", "route", "show", "default"]).strip():
            raise RuntimeError("benchmark requires an isolated namespace without a default route")
    for address, port in (("127.0.0.1", PANEL_PORT), (INGRESS, VLESS_PORT), ("127.0.0.1", TARGET_PORT), ("127.0.0.1", 62789)):
        with socket.socket() as listener:
            if WINDOWS:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind((address, port))
    binaries = {"official": args.official.resolve(), "candidate": args.candidate.resolve()}
    bin_dir = directory / "bin"
    shutil.copytree(args.core_dir, bin_dir)
    core_binary = bin_dir / ("xray-windows-amd64.exe" if WINDOWS else "xray-linux-amd64")
    core_hash = digest(core_binary)
    db_dir, logs = directory / "db", directory / "logs"
    db_dir.mkdir()
    logs.mkdir()
    db_path = db_dir / "x-ui.db"
    seed = directory / "seed.db"
    environment = {k: v for k, v in os.environ.items() if not k.startswith(("XUI_", "XRAY_"))}
    environment.update(TZ="UTC", XUI_DB_FOLDER=str(db_dir), XUI_BIN_FOLDER=str(bin_dir), XUI_LOG_FOLDER=str(logs))
    with (directory / "initialize.log").open("wb") as output:
        subprocess.run([binaries["official"], "setting", "-username", CREDENTIALS["username"], "-password",
                        CREDENTIALS["password"], "-webBasePath", BASE_PATH, "-listenIP", "127.0.0.1", "-port",
                        str(PANEL_PORT)], env=environment, cwd=directory, stdout=output, stderr=output, check=True)
    with contextlib.closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("DELETE FROM settings WHERE key = 'subEnable'")
        connection.execute("INSERT INTO settings(key, value) VALUES ('subEnable', 'false')")
    with panel(binaries["official"], directory, environment, "seed"):
        setup_fixture(panel_api(), args.users)
    with contextlib.closing(sqlite3.connect(db_path)) as source, contextlib.closing(sqlite3.connect(seed)) as dest:
        source.backup(dest)

    results = {"platform": "windows" if WINDOWS else "linux", "core_sha256": core_hash,
               "sqlite_fixture_sha256": digest(seed), "config_sha256": None,
               "duration_seconds": args.seconds, "repetitions": args.repetitions,
               "panel_sha256": {label: digest(path) for label, path in binaries.items()}, "workloads": {}, "quota": {}}
    target = await asyncio.start_server(serve_target, "127.0.0.1", TARGET_PORT)
    async with target:
        async def run(label, suffix, action):
            print(f"running {label} {suffix}", flush=True)
            for name in ("x-ui.db-wal", "x-ui.db-shm"):
                (db_dir / name).unlink(missing_ok=True)
            shutil.copyfile(seed, db_path)
            with panel(binaries[label], directory, environment, f"{label}-{suffix}") as proc:
                core = await ready(proc, core_binary)
                data = json.loads((bin_dir / "config.json").read_text())
                config_hash = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                if results["config_sha256"] is None:
                    results["config_sha256"] = config_hash
                if config_hash != results["config_sha256"] or digest(core_binary) != core_hash:
                    raise RuntimeError("panel generated a different config or modified the common Xray binary")
                # The initial poll establishes the baseline from the core before measurement.
                await asyncio.sleep(13)
                return await action(proc, core)

        for workload, users in (("single", 1), ("concurrent", args.users)):
            samples = {label: [] for label in binaries}
            for rep in range(args.repetitions):
                order = list(binaries if rep % 2 == 0 else reversed(binaries))
                for label in order:
                    samples[label].append(await run(label, f"{workload}-{rep}", lambda proc, core: measure(proc, core, users, args.seconds)))
            results["workloads"][workload] = {label: {"samples": runs, "median": {
                key: statistics.median(run[key] for run in runs) for key in runs[0]}} for label, runs in samples.items()}
        for label in binaries:
            results["quota"][label] = await run(label, "quota", lambda _proc, _core: quota_test(db_path))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--core-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--users", type=int, default=320)
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--repetitions", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.users <= 350 or not 10 <= args.seconds <= 120 or not 1 <= args.repetitions <= 5:
        parser.error("workload exceeds benchmark bounds")
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix="3x-traffic-compare-") as dirname:
        report = asyncio.run(compare(args, Path(dirname)))
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
