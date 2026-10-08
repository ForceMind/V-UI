"""Disposable GitHub-hosted cgroup-v2 deployment smoke, never a 512 MiB VPS claim.

Coordinator requires explicit GitHub-hosted runner markers. The non-root worker
verifies its own limits before installing the actual offline release bundle.
No system services, firewall, trust store, credentials, or production data change.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import platform
import re
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, HTTPSHandler, ProxyHandler, Request, build_opener
import uuid

SOURCE = Path(__file__).resolve().parents[1]
MEMORY_PROFILES = (320, 384, 512)
MEMORY_BYTES = 512 * 1024 * 1024  # Default profile, retained for callers.
UNIT_PATTERN = re.compile(r"vui-low-resource-[0-9a-f]{32}\.service")


def require_hosted_runner():
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or platform.system() != "Linux" or os.geteuid() == 0):
        raise RuntimeError("Only an explicit non-root GitHub-hosted Linux runner may run this gate")


def cgroup_directory(unit: str, membership: str | None = None,
                     root: Path = Path("/sys/fs/cgroup")) -> Path:
    if not UNIT_PATTERN.fullmatch(unit):
        raise RuntimeError("Invalid scoped transient unit")
    text = Path("/proc/self/cgroup").read_text() if membership is None else membership
    rows = [row.split(":", 2) for row in text.splitlines()]
    unified = [row[2] for row in rows if len(row) == 3 and row[:2] == ["0", ""]]
    if len(unified) != 1:
        raise RuntimeError("Unified cgroup v2 membership is required")
    path = Path(unified[0])
    if not path.is_absolute() or ".." in path.parts or path.name != unit:
        raise RuntimeError("Worker is not in its expected transient unit")
    directory = root / str(path).lstrip("/")
    if not directory.is_dir():
        raise RuntimeError("Worker cgroup directory is unavailable")
    return directory


def read_pairs(path: Path) -> dict[str, int]:
    return {key: int(value) for key, value in (row.split() for row in path.read_text().splitlines())}


def memory_bytes(memory_mib: int) -> int:
    if memory_mib not in MEMORY_PROFILES:
        raise RuntimeError("Unsupported memory profile")
    return memory_mib * 1024 * 1024


def verify_limits(directory: Path, memory_mib: int = 512) -> dict:
    memory = (directory / "memory.max").read_text().strip()
    swap = (directory / "memory.swap.max").read_text().strip()
    cpu = (directory / "cpu.max").read_text().strip().split()
    if memory != str(memory_bytes(memory_mib)) or swap != "0":
        raise RuntimeError(f"Expected exact {memory_mib} MiB memory.max and zero memory.swap.max")
    if len(cpu) != 2 or cpu[0] == "max" or int(cpu[0]) <= 0 or int(cpu[0]) != int(cpu[1]):
        raise RuntimeError("Expected exactly one CPU quota")
    return {"memory.max": int(memory), "memory.swap.max": int(swap), "cpu.max": " ".join(cpu)}


def metrics(directory: Path) -> dict:
    result = {name: int((directory / name).read_text()) for name in ("memory.current", "memory.peak")}
    result["memory.events"] = read_pairs(directory / "memory.events")
    result["memory.stat"] = read_pairs(directory / "memory.stat")
    if not {"anon", "file"} <= result["memory.stat"].keys() or any(value < 0 for value in result["memory.stat"].values()):
        raise RuntimeError("Missing or invalid anonymous/file memory accounting")
    result["cpu.stat"] = read_pairs(directory / "cpu.stat")
    if not {"oom", "oom_kill"} <= result["memory.events"].keys():
        raise RuntimeError("Missing OOM accounting")
    if "usage_usec" not in result["cpu.stat"] or result["memory.peak"] <= 0:
        raise RuntimeError("Missing CPU/peak accounting")
    return result


def assert_no_oom(value: dict):
    if any(value["memory.events"].get(name, 0) for name in ("oom", "oom_kill", "oom_group_kill")):
        raise RuntimeError("Cgroup reported OOM; acceptance failed")


def write_json(path: Path, value: dict):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def filesystem_type(path: Path) -> str:
    # Capture actual mount type, not an assumption that /tmp is disk-backed.
    matches = []
    for row in Path("/proc/mounts").read_text().splitlines():
        fields = row.split()
        mount = Path(fields[1].replace("\\040", " "))
        if path == mount or mount in path.parents:
            matches.append((len(mount.parts), fields[2]))
    if not matches:
        raise RuntimeError("Unable to identify work filesystem")
    return max(matches)[1]


def worker(args) -> int:
    require_hosted_runner()
    directory = cgroup_directory(args.unit)
    limits = verify_limits(directory, args.memory_mib)
    report = {"schema": 1, "scope": "actual offline stage/activate, HTTPS panel with idle certificate manager, no proxy cores; not full VPS qualification",
              "source_commit": args.source_commit, "unit": args.unit, "limits": limits,
              "requested_memory_mib": args.memory_mib,
              "accounting": "worker, offline pip, panel, local clients, and descendants in one cgroup; build/download, host OS and pre-existing cache ownership excluded",
              "memory_peak_scope": "lifetime cgroup maximum, not reset per stage",
              "memory_stat_scope": "stage-end/current snapshots, not composition at lifetime peak; separate kernel reads may differ",
              "stages": [], "complete": False, "outcome": "running"}
    output = args.output

    def checkpoint():
        report["metrics"] = metrics(directory)
        write_json(output, report)
        assert_no_oom(report["metrics"])

    def stage(name, action):
        before = metrics(directory)
        started = time.monotonic()
        row = {"name": name, "outcome": "running"}
        report["stages"].append(row)
        checkpoint()
        try:
            value = action()
            row["outcome"] = "passed"
            return value
        except Exception as exc:
            row["outcome"] = "failed"
            row["error_type"] = type(exc).__name__
            raise
        finally:
            row["wall_seconds"] = time.monotonic() - started
            after = metrics(directory)
            row["cpu_usage_usec"] = after["cpu.stat"]["usage_usec"] - before["cpu.stat"]["usage_usec"]
            row["memory_current_bytes"] = after["memory.current"]
            row["memory_peak_bytes"] = after["memory.peak"]
            row["memory_stat"] = after["memory.stat"]
            row["memory_events"] = after["memory.events"]
            checkpoint()

    try:
        checkpoint()
        work_type = filesystem_type(args.work_dir.resolve())
        report["work_filesystem"] = work_type
        if work_type in {"tmpfs", "ramfs"}:
            raise RuntimeError("Acceptance work directory must be disk-backed")
        sys.path.insert(0, str(SOURCE))
        sys.path.insert(0, str(SOURCE / "tests"))
        from app import release_tools as tools
        from loopback_helpers import certificate_files, unused_port
        tools.supported_environment()
        with ExitStack() as stack:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="installed-", dir=args.work_dir)))
            checksum = args.bundle.with_suffix(args.bundle.suffix + ".sha256").read_text().split()[0]
            identity = stage("offline_stage_including_wheels", lambda: tools.stage(args.bundle, checksum, root))
            stage("activate", lambda: tools.activate(root, identity))
            release, _ = tools.active(root)
            payload, python = release / "payload", release / "runtime/python/bin/python3"
            manifest = tools.verify_payload(payload)
            if manifest["source_commit"] != args.source_commit:
                raise RuntimeError("Installed bundle is not the requested exact source commit")
            report["bundle_sha256"] = checksum
            report["platform"] = manifest["platform"]
            data = root / "data"
            password = "Synthetic-low-resource-test-password!"
            def provision():
                subprocess.run([str(python), "-B", "-c",
                    'import sys;from app.services.auth_service import provision_admin;provision_admin("resource-admin",sys.stdin.read())'],
                    input=password, text=True, cwd=payload, env=tools.child_env(payload, data), check=True, timeout=30)
            stage("fake_admin_provision", provision)
            certdir = root / "fake-certs"
            certdir.mkdir(mode=0o700)
            ca, cert, key = certificate_files(certdir, "resource-panel")
            port = unused_port()
            origin = f"https://127.0.0.1:{port}"
            context = ssl.create_default_context(cafile=ca)
            jar = CookieJar()
            opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), HTTPCookieProcessor(jar))
            running = []
            log_number = 0

            def stop():
                if running:
                    process = running.pop()
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=12)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
            stack.callback(stop)

            def start():
                nonlocal log_number
                log_path = output.parent / f"{output.stem}-panel-{log_number}.log"
                log_number += 1
                log = stack.enter_context(log_path.open("w"))
                command = [sys.executable, "-B", str(SOURCE / "scripts/deploy.py"), "--root", str(root), "run",
                           "--origin", origin, "--cert", str(cert), "--key", str(key), "--port", str(port)]
                process = subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT)
                running.append(process)
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Panel exited before HTTPS startup; see scoped panel log")
                    try:
                        with opener.open(origin + "/login", timeout=.5) as response:
                            if response.status == 200:
                                return
                    except (OSError, URLError):
                        time.sleep(.1)
                raise RuntimeError("HTTPS startup deadline exceeded")

            def api(path, body=None, expected=200):
                headers = {"Origin": origin, "X-VUI-Request": "1", "Content-Type": "application/json"}
                request = Request(origin + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
                try:
                    response = opener.open(request, timeout=15)
                except HTTPError as exc:
                    response = exc
                with response:
                    raw = response.read()
                    if response.status != expected:
                        raise RuntimeError(f"Unexpected HTTP status {response.status}; expected {expected}")
                    return response.headers, raw

            def login():
                headers, _ = api("/api/auth/login", {"username": "resource-admin", "password": password})
                cookie = headers.get("Set-Cookie", "")
                if "__Host-vui_session=" not in cookie or "Secure" not in cookie or "HttpOnly" not in cookie:
                    raise RuntimeError("HTTPS session flags missing")

            stage("https_startup", start)
            stage("anonymous_denied", lambda: api("/api/inbounds", expected=401))
            def untrusted_rejected():
                untrusted = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()))
                try:
                    with untrusted.open(origin + "/login", timeout=3):
                        pass
                except URLError as exc:
                    if isinstance(exc.reason, ssl.SSLCertVerificationError):
                        return
                    raise
                raise RuntimeError("Untrusted fake CA was accepted")
            stage("untrusted_ca_rejected", untrusted_rejected)
            stage("login", login)
            stage("authenticated_read", lambda: api("/api/auth/me"))
            def concurrent_reads():
                cookie = "; ".join(item.name + "=" + item.value for item in jar)
                def one(_):
                    client = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
                    request = Request(origin + "/api/auth/me", headers={"Cookie": cookie})
                    with client.open(request, timeout=15) as response:
                        if response.status != 200 or json.loads(response.read())["username"] != "resource-admin":
                            raise RuntimeError("Concurrent authenticated read failed")
                with ThreadPoolExecutor(max_workers=10) as pool:
                    list(pool.map(one, range(100)))
                report["concurrent_workload"] = {"requests": 100, "concurrency": 10, "path": "/api/auth/me", "status_200": 100}
            stage("100_authenticated_reads_concurrency_10", concurrent_reads)
            def idle():
                time.sleep(60)
                if running[0].poll() is not None:
                    raise RuntimeError("Panel exited during idle")
            stage("panel_only_idle_60_seconds", idle)
            old_cookies = list(jar)
            stage("logout", lambda: api("/api/auth/logout", {}))
            for cookie in old_cookies:
                jar.set_cookie(cookie)
            stage("logged_out_replayed_cookie_denied", lambda: api("/api/auth/me", expected=401))
            stage("login_before_backup", login)
            stage("stopped_panel", stop)
            backup = root / "stopped-backup.zip"
            digest = stage("stopped_backup", lambda: tools.backup(root, backup))
            with sqlite3.connect(data / "v-ui.db") as db:
                db.execute("UPDATE users SET username='modified-after-backup'")
            stage("stopped_restore", lambda: tools.restore(root, backup, digest))
            def verify_restore():
                with sqlite3.connect(data / "v-ui.db") as db:
                    if db.execute("SELECT username FROM users").fetchone() != ("resource-admin",):
                        raise RuntimeError("Fake administrator data was not restored")
                    if db.execute("SELECT count(*) FROM admin_sessions").fetchone()[0] != 0:
                        raise RuntimeError("Restore did not revoke existing sessions")
            stage("restore_data_and_session_revocation", verify_restore)
            stage("restored_https_startup", start)
            stage("old_session_after_restore_denied", lambda: api("/api/auth/me", expected=401))
            stage("restored_login", login)
            stage("restored_logout", lambda: api("/api/auth/logout", {}))
            stage("final_panel_stop", stop)
        report["outcome"] = "passed"
    except Exception as exc:
        report["outcome"] = "failed"
        # Avoid placing fake credentials, request bodies or response bodies in evidence.
        report["error_type"] = type(exc).__name__
        print(f"Low-resource worker failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        try:
            report["metrics"] = metrics(directory)
            assert_no_oom(report["metrics"])
        except Exception as exc:
            report["outcome"] = "failed"
            report["accounting_error"] = type(exc).__name__
        report["complete"] = True
        write_json(output, report)
    return 0 if report["outcome"] == "passed" else 1


def systemd_command(args, unit: str, work: Path, worker_output: Path) -> list[str]:
    memory_bytes(args.memory_mib)  # Validate programmatic callers as well as argparse.
    return ["sudo", "-n", "systemd-run", "--wait", "--pipe", "--unit", unit,
            "--uid", str(os.getuid()), "--gid", str(os.getgid()),
            "--property", f"MemoryMax={args.memory_mib}M", "--property", "MemorySwapMax=0",
            "--property", "CPUQuota=100%", "--property", "RuntimeMaxSec=600",
            "--property", "KillMode=control-group", "--property", "OOMPolicy=stop",
            "--setenv=GITHUB_ACTIONS=true", "--setenv=RUNNER_ENVIRONMENT=github-hosted",
            "--setenv=TMPDIR=" + str(work),
            sys.executable, "-B", str(Path(__file__).resolve()), "--worker", "--unit", unit,
            "--bundle", str(args.bundle.resolve()), "--source-commit", args.source_commit,
            "--output", str(worker_output), "--work-dir", str(work), "--memory-mib", str(args.memory_mib)]


def coordinator(args) -> int:
    require_hosted_runner()
    budget = memory_bytes(args.memory_mib)
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        raise RuntimeError("An exact source commit is required")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output = args.output.resolve()
    worker_output = output.with_name(output.stem + "-worker.json")
    if output.exists() or worker_output.exists():
        raise RuntimeError("Refusing to overwrite prior acceptance evidence")
    unit = "vui-low-resource-" + uuid.uuid4().hex + ".service"
    summary = {"schema": 1, "unit": unit, "source_commit": args.source_commit, "outcome": "failed",
               "scope": f"{args.memory_mib} MiB cgroup / one CPU quota regression, not a full 512 MiB host",
               "requested_memory_mib": args.memory_mib, "worker_report": worker_output.name}
    with tempfile.TemporaryDirectory(prefix="low-resource-work-", dir=output.parent) as directory:
        command = systemd_command(args, unit, Path(directory), worker_output)
        try:
            with output.with_suffix(".log").open("w") as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=660)
            summary["systemd_run_returncode"] = result.returncode
            if not worker_output.exists():
                raise RuntimeError("Worker accounting report missing (possible OOM or timeout)")
            report = json.loads(worker_output.read_text())
            if (result.returncode != 0 or report.get("outcome") != "passed" or report.get("complete") is not True
                    or report.get("unit") != unit or report.get("source_commit") != args.source_commit
                    or report.get("requested_memory_mib") != args.memory_mib
                    or report.get("limits", {}).get("memory.max") != budget):
                raise RuntimeError("Worker failed or its final accounting is incomplete")
            assert_no_oom(report["metrics"])
            if report["metrics"]["memory.peak"] > budget:
                raise RuntimeError("Peak exceeded configured cgroup budget")
            summary["outcome"] = "passed"
            summary["metrics"] = report["metrics"]
        except Exception as exc:
            summary["error"] = str(exc)
        finally:
            # Only this randomly named unit is inspected/stopped/reset; never a host service.
            unit_state = ""
            for operation in ("show", "stop", "reset-failed"):
                try:
                    result = subprocess.run(["sudo", "-n", "systemctl", operation, unit],
                                            capture_output=True, text=True, timeout=20)
                    if operation == "show":
                        unit_state = result.stdout + result.stderr
                        output.with_name(output.stem + "-unit.log").write_text(unit_state)
                    if operation == "stop" and result.returncode != 0:
                        inactive = any(marker in unit_state for marker in
                                       ("ActiveState=inactive", "ActiveState=failed", "LoadState=not-found"))
                        if not inactive:
                            summary["cleanup_error"] = "Unable to confirm scoped unit stopped"
                            summary["outcome"] = "failed"
                except subprocess.SubprocessError as exc:
                    summary["cleanup_error"] = type(exc).__name__
                    summary["outcome"] = "failed"
            write_json(output, summary)
    print(json.dumps(summary))
    return 0 if summary["outcome"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--memory-mib", type=int, choices=MEMORY_PROFILES, default=512)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--unit")
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    if args.worker and (not args.unit or not args.work_dir):
        parser.error("Worker requires --unit and --work-dir")
    os.umask(0o077)
    return worker(args) if args.worker else coordinator(args)


if __name__ == "__main__":
    raise SystemExit(main())
