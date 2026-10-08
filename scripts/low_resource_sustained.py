"""Opt-in sustained test support. Clients/target run outside the server cgroup.

Only disposable GitHub-hosted runners; fake loopback traffic, no host changes.
The coordinator broker accepts three fixed fixture requests, never commands.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import threading
import time

SOURCE = Path(__file__).resolve().parents[1]
CONCURRENCIES = (1, 10, 50)
DURATION = 600
INTERVAL = 1.0
BODY = bytes(range(256)) * 256  # Exactly 64 KiB, stable and non-secret.


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def membership():
    return Path("/proc/self/cgroup").read_text().strip()


def outside_server(unit, value):
    rows = [line.split(":", 2) for line in value.splitlines()]
    unified = [row[2] for row in rows if len(row) == 3 and row[:2] == ["0", ""]]
    if len(unified) != 1 or unit in Path(unified[0]).parts:
        raise RuntimeError("External load generator must be outside server cgroup")


def checked_request(request, work, unit, commit, concurrency):
    if (request.get("unit") != unit or request.get("source_commit") != commit
            or request.get("concurrency") != concurrency or concurrency not in CONCURRENCIES
            or request.get("duration_seconds") != DURATION):
        raise RuntimeError("Mismatched sustained fixture request")
    for name in ("binary", "fixture", "ca"):
        path = Path(request[name]).resolve(strict=True)
        if not path.is_relative_to(work.resolve()) or path == work.resolve():
            raise RuntimeError("External fixture path escaped disposable work directory")
    if Path(request["binary"]).name != "sing-box":
        raise RuntimeError("Only bundled sing-box may be started")
    if type(request.get("server_port")) is not int or not 1024 <= request["server_port"] <= 65535:
        raise RuntimeError("Invalid loopback server port")


def cleanup_process_group(process, *, grace_seconds=5, kill_seconds=3):
    """A leader exit does not prove its descendants exited; inspect the group."""
    def send(sig):
        try:
            os.killpg(process.pid, sig)
            return True
        except ProcessLookupError:
            return False
    def wait_gone(seconds):
        deadline = time.monotonic() + seconds
        while True:
            process.poll()  # Reap our leader even when descendants remain.
            if not send(0):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(.05, max(0, deadline - time.monotonic())))
    send(signal.SIGTERM)
    if not wait_gone(grace_seconds):
        send(signal.SIGKILL)
        if not wait_gone(kill_seconds):
            raise RuntimeError("External fixture process group survived cleanup")
    process.wait(timeout=3)


class LoadBroker:
    """Run known helper outside systemd unit; terminate its whole group on failure."""
    def __init__(self, work, output, unit, commit):
        self.work, self.output, self.unit, self.commit = work, output, unit, commit
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def __enter__(self):
        outside_server(self.unit, membership())
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=15)
        if self.thread.is_alive():
            raise RuntimeError("External load broker did not stop")

    def serve(self):
        for concurrency in CONCURRENCIES:
            request_path = self.work / f"external-{concurrency}.request.json"
            response = self.work / f"external-{concurrency}.response.json"
            while not request_path.exists():
                if self.stop.wait(.1):
                    return
            evidence = self.output.with_name(self.output.stem + f"-load-{concurrency}.json")
            process = None
            value = {"outcome": "failed"}
            try:
                request = json.loads(request_path.read_text())
                checked_request(request, self.work, self.unit, self.commit, concurrency)
                log_path = evidence.with_suffix(".log")
                with log_path.open("w") as log:
                    process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()),
                        "--request", str(request_path), "--output", str(evidence)],
                        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    deadline = time.monotonic() + DURATION + 90
                    while process.poll() is None:
                        if self.stop.wait(.1) or time.monotonic() >= deadline:
                            raise RuntimeError("External load stopped or exceeded deadline")
                    if process.returncode != 0:
                        raise RuntimeError("External load helper failed; evidence retained")
                value = json.loads(evidence.read_text())
                if value.get("outcome") != "passed":
                    raise RuntimeError("External load report incomplete")
            except Exception as exc:
                # Preserve the helper's partial counters even when it failed.
                if evidence.exists():
                    try:
                        value = json.loads(evidence.read_text())
                    except (ValueError, OSError):
                        pass
                value.update(outcome="failed", broker_error_type=type(exc).__name__)
            finally:
                value["helper_outcome"] = value.get("outcome")
                try:
                    if process is not None:
                        cleanup_process_group(process)
                    value["broker_cleanup_complete"] = True
                except Exception as exc:
                    value.update(outcome="failed", broker_cleanup_complete=False,
                                 broker_cleanup_error=type(exc).__name__)
                # Never publish a passing response before group cleanup has
                # been bounded, escalated if necessary, and independently verified.
                atomic_json(evidence, value)
                atomic_json(response, value)
            if value.get("outcome") != "passed":
                return


def request_load(work, unit, commit, concurrency, binary, fixture, ca, port, observe):
    request = {"unit": unit, "source_commit": commit, "concurrency": concurrency,
               "duration_seconds": DURATION, "binary": str(binary), "fixture": str(fixture),
               "ca": str(ca), "server_port": port}
    request_path = work / f"external-{concurrency}.request.json"
    response = work / f"external-{concurrency}.response.json"
    if request_path.exists() or response.exists():
        raise RuntimeError("Refusing to reuse external workload evidence")
    atomic_json(request_path, request)
    started = time.monotonic()
    while not response.exists():
        if time.monotonic() - started > DURATION + 110:
            raise RuntimeError("External workload response deadline exceeded")
        observe()
        time.sleep(1)
    value = json.loads(response.read_text())
    validate_result(value, unit, commit, concurrency)
    return value


def validate_result(value, unit, commit, concurrency):
    def finite_nonnegative(number):
        return type(number) in (int, float) and math.isfinite(number) and number >= 0
    if (not finite_nonnegative(value.get("wall_seconds"))
            or not finite_nonnegative(value.get("max_response_seconds"))):
        raise RuntimeError("Missing or invalid sustained timing accounting")
    for group in ("generator_and_target", "client_children"):
        usage = value.get(group, {})
        if (not isinstance(usage, dict)
                or not finite_nonnegative(usage.get("user_cpu_seconds"))
                or not finite_nonnegative(usage.get("system_cpu_seconds"))
                or type(usage.get("max_rss_kib")) is not int or usage["max_rss_kib"] <= 0):
            raise RuntimeError("Missing or invalid external CPU/RSS accounting")
    if (value.get("outcome") != "passed" or value.get("source_commit") != commit
            or value.get("unit") != unit or value.get("concurrency") != concurrency
            or value.get("requested_duration_seconds") != DURATION
            or value.get("wall_seconds", 0) < DURATION
            or value.get("requests") != concurrency * DURATION
            or value.get("target_requests") != concurrency * DURATION
            or value.get("target_connections") != concurrency
            or value.get("body_bytes") != len(BODY)
            or value.get("body_sha256") != hashlib.sha256(BODY).hexdigest()
            or value.get("requests_per_connection_per_second") != 1
            or value.get("client_core_cgroup") != value.get("client_cgroup")
            or value.get("broker_cleanup_complete") is not True
            or value.get("recovery_requests") != 1 or value.get("recovery_connections") != 1
            or value.get("no_direct") is not True
            or value.get("errors") != 0 or value.get("cleanup_complete") is not True):
        raise RuntimeError("Sustained workload failed or evidence is incomplete")
    outside_server(unit, value.get("client_cgroup", ""))


def fixed_load(proxy_port, target_port, concurrency, *, duration=DURATION, interval=INTERVAL, progress=None, record_times=False, stagger=False):
    """One persistent CONNECT/TCP tunnel per lane, one 64-KiB GET per second.

    Requests never reconnect/retry. A missed slot or body mismatch fails; time
    sleeping after the last response holds all tunnels to the common deadline.
    """
    start = {"time": None}
    cancelled = threading.Event()
    progress = {} if progress is None else progress
    progress.update(completed_requests=0, errors=0, first_response_lanes=0)
    if record_times: progress["response_monotonic"] = []
    progress_lock = threading.Lock()
    def ready():
        start['time'] = time.monotonic()
        progress['started_monotonic'] = start['time']
        progress['connected_lanes'] = concurrency
    barrier = threading.Barrier(concurrency, action=ready)
    def lane(index):
        client = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=5)
        client.set_tunnel("127.0.0.1", target_port)
        try:
            client.connect()
            socket = client.sock
            barrier.wait(timeout=20)
            count, maximum = 0, 0.0
            for tick in range(duration):
                if cancelled.is_set():
                    raise RuntimeError("Peer workload lane failed")
                scheduled = start["time"] + tick * interval + (index * interval / concurrency if stagger else 0)
                time.sleep(max(0, scheduled - time.monotonic()))
                before = time.monotonic()
                if before - scheduled >= interval:
                    raise RuntimeError("Sustained request missed its fixed-rate slot")
                client.request("GET", "/fixed-64k", headers={"Connection": "keep-alive"})
                response = client.getresponse()
                if response.status != 200 or response.read() != BODY or client.sock is not socket:
                    raise RuntimeError("Sustained response or persistent tunnel failed")
                maximum = max(maximum, time.monotonic() - before)
                count += 1
                with progress_lock:
                    progress["completed_requests"] += 1
                    if count == 1: progress["first_response_lanes"] += 1
                    if record_times: progress["response_monotonic"].append(time.monotonic())
            time.sleep(max(0, start["time"] + duration * interval - time.monotonic()))
            return count, maximum
        except Exception:
            cancelled.set()
            barrier.abort()
            with progress_lock:
                progress["errors"] += 1
            raise
        finally:
            client.close()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        rows = list(pool.map(lane, range(concurrency)))
    return {"requests": sum(row[0] for row in rows),
            "max_response_seconds": max(row[1] for row in rows),
            "wall_seconds": time.monotonic() - start["time"], "errors": 0}


def persistent_target(stack):
    counts = {"requests": 0, "connections": 0}
    lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *_): pass
        def setup(self):
            super().setup()
            with lock: counts["connections"] += 1
        def do_GET(self):
            with lock: counts["requests"] += 1
            self.send_response(200 if self.path == "/fixed-64k" else 404)
            self.send_header("Content-Length", str(len(BODY)))
            self.end_headers()
            self.wfile.write(BODY)
    class Target(ThreadingHTTPServer):
        request_queue_size = 128  # The external fixture must accept all 50 lanes.
    target = Target(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=target.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
    thread.start()
    def close_target():
        target.shutdown()
        target.server_close()
        thread.join(timeout=3)
        if thread.is_alive(): raise RuntimeError("Target survived cleanup")
    stack.callback(close_target)
    return target, counts


def run(request, output):
    sys.path.insert(0, str(SOURCE))
    sys.path.insert(0, str(SOURCE / "tests"))
    from scripts.low_resource_acceptance import require_hosted_runner
    from scripts.low_resource_proxy import client_config, assert_no_direct_log
    from loopback_helpers import CoreProcess, unused_port
    require_hosted_runner()
    outside_server(request["unit"], membership())
    value = {"schema": 1, "outcome": "running", "unit": request["unit"],
             "source_commit": request["source_commit"], "concurrency": request["concurrency"],
             "requested_duration_seconds": DURATION, "client_cgroup": membership(),
             "body_bytes": len(BODY), "body_sha256": hashlib.sha256(BODY).hexdigest(),
             "requests_per_connection_per_second": 1, "protocol": "VLESS/TCP/TLS",
             "connection_model": "persistent HTTP CONNECT tunnel per lane, no reconnect or retry",
             "accounting": "client core, target and traffic generator outside service cgroup; rusage excludes service",
             "cleanup_complete": False}
    atomic_json(output, value)
    try:
        with ExitStack() as stack:
            target, counts = persistent_target(stack)
            port = unused_port()
            config = client_config(port, request["server_port"], request["ca"])
            # Bounded logs during the long positive phase; negative smoke keeps debug.
            config["log"]["level"] = "warn"
            path = Path(request["fixture"]) / f"external-client-{request['concurrency']}.json"
            path.write_text(json.dumps(config)); path.chmod(0o600)
            core = CoreProcess(stack, [request["binary"], "run", "-c", str(path)],
                               output.with_name(output.stem + "-core.log"), None)
            core.start(port)
            value["client_core_cgroup"] = Path(f"/proc/{core.process.pid}/cgroup").read_text().strip()
            outside_server(request["unit"], value["client_core_cgroup"])
            if value["client_core_cgroup"] != value["client_cgroup"]:
                raise RuntimeError("Client and generator accounting membership differs")
            value["partial_load"] = {}
            value.update(fixed_load(port, target.server_address[1], request["concurrency"], progress=value["partial_load"]))
            value.update(target_requests=counts["requests"], target_connections=counts["connections"])
            if (counts["requests"] != DURATION * request["concurrency"]
                    or counts["connections"] != request["concurrency"] or core.process.poll() is not None):
                raise RuntimeError("Delivery, connection count or client liveness failed")
            # A new single connection after each ladder step proves recovery;
            # keep its delivery separate from the fixed 600*N measurement.
            before_requests, before_connections = counts["requests"], counts["connections"]
            recovered = fixed_load(port, target.server_address[1], 1, duration=1)
            value["recovery_requests"] = counts["requests"] - before_requests
            value["recovery_connections"] = counts["connections"] - before_connections
            if recovered["requests"] != 1 or value["recovery_requests"] != 1 or value["recovery_connections"] != 1:
                raise RuntimeError("Single-connection recovery after sustained load failed")
        assert_no_direct_log(core.log_path)
        if core.process.poll() is None: raise RuntimeError("Client survived cleanup")
        value.update(outcome="passed", cleanup_complete=True, no_direct=True)
    except Exception as exc:
        value.update(outcome="failed", error_type=type(exc).__name__, error=str(exc))
    finally:
        for name, who in (("generator_and_target", resource.RUSAGE_SELF), ("client_children", resource.RUSAGE_CHILDREN)):
            usage = resource.getrusage(who)
            value[name] = {"user_cpu_seconds": usage.ru_utime, "system_cpu_seconds": usage.ru_stime,
                           "max_rss_kib": usage.ru_maxrss,
                           "rss_scope": "Linux getrusage high water; not additive peak or cgroup memory"}
        atomic_json(output, value)
    return 0 if value["outcome"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    raise SystemExit(run(json.loads(args.request.read_text()), args.output))
