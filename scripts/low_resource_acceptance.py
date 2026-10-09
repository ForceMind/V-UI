"""Disposable GitHub-hosted cgroup-v2 deployment smoke, never a 512 MiB VPS claim.

Coordinator requires explicit GitHub-hosted runner markers. The non-root worker
verifies its own limits before installing the actual offline release bundle.
No system services, firewall, trust store, credentials, or production data change.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, nullcontext
from http.cookiejar import CookieJar
import json
import math
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


def peak_assessment(peak: int, budget: int, page_size: int) -> dict:
    # Linux documents temporary memory.max overshoot. Keep one fixed base-page
    # allowance, disclose every byte, and never grow it to make a run green.
    if type(page_size) is not int or page_size not in (4096, 16384, 65536):
        raise RuntimeError("Unsupported base page size for fixed peak allowance")
    if type(peak) is not int or peak <= 0:
        raise RuntimeError("Missing peak accounting")
    return {"nominal_budget_bytes": budget, "observed_peak_bytes": peak,
            "nominal_overage_bytes": max(0, peak - budget),
            "base_page_size_bytes": page_size, "fixed_allowance_bytes": page_size,
            "within_nominal_budget": peak <= budget,
            "within_fixed_page_allowance": peak <= budget + page_size}


def assert_no_oom(value: dict):
    if any(value["memory.events"].get(name, 0) for name in ("oom", "oom_kill", "oom_group_kill")):
        raise RuntimeError("Cgroup reported OOM; acceptance failed")


def write_json(path: Path, value: dict):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def process_snapshot(directory: Path):
    rows = []
    for pid in (directory / "cgroup.procs").read_text().split():
        try:
            status = Path(f"/proc/{int(pid)}/status").read_text().splitlines()
            fields = dict(line.split(":", 1) for line in status if ":" in line)
            rows.append({"pid": int(pid), "ppid": int(fields["PPid"]),
                         "name": fields["Name"].strip(), "threads": int(fields["Threads"]),
                         "starttime_ticks": int(Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()[19])})
        except FileNotFoundError:
            continue  # The process exited between the two kernel reads.
    return rows


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


def duration_profile(args):
    profile = getattr(args, "duration_profile", "smoke")
    if profile not in {"smoke", "sustained", "interactions", "certificates", "data-backup", "accounting"}:
        raise RuntimeError("Unknown duration profile")
    if profile in {"sustained", "interactions", "certificates", "data-backup", "accounting"} and args.memory_mib != 512:
        raise RuntimeError("Extended profiles currently require the original 512 MiB limit")
    return profile


def runtime_seconds(args):
    return {"smoke": 600, "sustained": 6600, "interactions": 1800, "certificates": 1800, "data-backup": 1800, "accounting": 900}[duration_profile(args)]


def worker(args) -> int:
    require_hosted_runner()
    backend = getattr(args, 'cgroup_backend', 'systemd')
    if backend == 'docker-private':
        # Only the dedicated container entry point supplies this programmatic
        # mode. Native CLI/path checks remain unchanged; host validates Docker
        # identity and the real host cgroup before allowing any measured work.
        from scripts.low_resource_container import private_directory
        directory = private_directory(args)
    elif backend == 'systemd':
        directory = cgroup_directory(args.unit)
    else:
        raise RuntimeError('Unknown resource accounting backend')
    limits = verify_limits(directory, args.memory_mib)
    profile = duration_profile(args)
    report = {"schema": 1, "scope": "actual offline stage/activate, HTTPS panel with idle certificate manager and bounded single sing-box proxy smoke; not full VPS qualification",
              "source_commit": args.source_commit, "unit": args.unit, "limits": limits,
              "cgroup_backend": backend,
              "worker_pid": os.getpid(), "service_cgroup": Path("/proc/self/cgroup").read_text().strip(),
              "duration_profile": profile, "requested_memory_mib": args.memory_mib, "base_page_size_bytes": os.sysconf("SC_PAGE_SIZE"),
              "accounting": "worker, offline pip, panel, local clients, and descendants in one cgroup; build/download, host OS and pre-existing cache ownership excluded",
              "memory_peak_scope": "lifetime cgroup maximum, not reset per stage",
              "memory_stat_scope": "stage-end/current snapshots, not composition at lifetime peak; separate kernel reads may differ",
              "stages": [], "complete": False, "outcome": "running"}
    if profile == "sustained":
        report["scope"] = "30-minute panel and single-core idles plus three 10-minute fixed-rate VLESS/TCP/TLS connection steps; not full VPS or 24-hour qualification"
        report["sustained_load"] = []
        report["accounting"] += "; sustained client/target/generator outside service cgroup, separately measured; short controls remain inside"
    if profile == "interactions":
        report["scope"] = "real UI visibility/multi-tab/logout and fixed 100-node export resource diagnostic; not complete VPS qualification"
        report["accounting"] += "; headed browser/export generator outside service cgroup; same host remains shared"
    if profile == "certificates":
        report["scope"] = "real local-test-CA issuance and due renewal during 10-connection 600-second loopback traffic; not live certificate rotation or full VPS qualification"
        report["accounting"] += "; certificate manager/responder/Certbot inside; Pebble/DNS/client/target outside service cgroup"
    if profile == 'data-backup':
        report['scope']='1000 synthetic nodes, saved large routing, fixed administrator exports and stopped backup/restore of 256MiB synthetic log; not runtime log rotation or full VPS qualification'
        report['accounting']+='; external export clients separately accounted; generation/hash/backup/restore remain inside service cgroup'
    if profile == "accounting":
        report["scope"] = "warm panel and single-core 60-second inclusive memory attribution diagnostic; not 160 MiB or 30-minute idle qualification"
        report["process_accounting_scope"] = "sequential smaps_rollup RSS/PSS/private snapshots of every cgroup descendant; PSS is not memcg charge; observer remains included; no subtraction or cache reset"
    output = args.output

    def checkpoint():
        report["metrics"] = metrics(directory)
        write_json(output, report)
        assert_no_oom(report["metrics"])

    def stage(name, action):
        before = metrics(directory)
        started = time.monotonic()
        row = {"name": name, "outcome": "running", "started_monotonic": started}
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
            row["cpu_mean_percent_one_core"] = row["cpu_usage_usec"] / max(row["wall_seconds"], 1e-9) / 10000
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
            def alive():
                if running[0].poll() is not None:
                    raise RuntimeError("Panel exited unexpectedly")
            def monitor_wait(seconds, health, core_pid=None, watchdog_pid=None):
                accounting_wait(seconds, health, core_pid, watchdog_pid, interval=30)
            def accounting_wait(seconds, health, core_pid=None, watchdog_pid=None, interval=5):
                from scripts.low_resource_accounting import snapshot
                roles = {"worker": os.getpid(), "panel": running[0].pid}
                if core_pid is not None:
                    roles["core"] = core_pid
                    roles["watchdog"] = watchdog_pid
                started = time.monotonic()
                samples = []
                report["stages"][-1]["accounting_samples"] = samples
                report["stages"][-1]["accounting_roles"] = roles
                report["stages"][-1]["accounting_cgroup_root"] = str(directory)
                report["stages"][-1]["accounting_started_monotonic"] = started
                for offset in range(0, seconds + 1, interval):
                    planned = started + offset
                    time.sleep(max(0, planned - time.monotonic()))
                    alive()
                    health()
                    sample = snapshot(directory, roles, metrics)
                    sample["planned_monotonic"] = planned
                    samples.append(sample)
                    if profile == "sustained":
                        observed = metrics(directory)
                        observed.update(observed_monotonic=time.monotonic(), processes=process_snapshot(directory))
                        assert_no_oom(observed)
                        report["stages"][-1].setdefault("samples", []).append(observed)
                    checkpoint()
                    if sample["finished_monotonic"] >= planned + interval:
                        raise RuntimeError("Accounting snapshot missed its planned slot")
                health()
            def idle():
                if profile == "accounting":
                    accounting_wait(60, alive)
                elif profile == "sustained":
                    monitor_wait(1800, alive)
                else:
                    time.sleep(60)
                    alive()
            if profile != "sustained":
                stage("panel_only_idle_60_seconds", idle)
            else:
                stage("panel_only_idle_1800_seconds", idle)
                stage("refresh_session_after_panel_idle", login)
            if profile == "interactions":
                def seed_export_fixture():
                    # Seed synthetic rows without pretending this is the normal
                    # node-edit API or starting 100 real listeners. API/editor
                    # correctness remains covered by the existing deployment gate.
                    code = r'''import sys, json
from app.models.database import SessionLocal, Inbound
with SessionLocal() as db:
    if db.query(Inbound).count(): raise RuntimeError("Fixture requires empty node database")
    for n in range(100):
        db.add(Inbound(core="sing-box", remark=f"resource-node-{n:03d}", tag=f"resource-{n:03d}",
            enable=True, port=20000+n, protocol="vless",
            settings={"users":[{"uuid":f"11111111-1111-1111-1111-{n+1:012d}","flow":""}]},
            stream_settings={"tls":{"enabled":True,"server_name":"vpn.example.test",
                "certificate_path":sys.argv[1],"key_path":sys.argv[2]}}))
    db.commit()
'''
                    subprocess.run([str(python), "-B", "-c", code, str(cert), str(key)],
                        cwd=payload, env=tools.child_env(payload, data), check=True, timeout=30)
                    rows = json.loads(api("/api/inbounds")[1])
                    if len(rows) != 100: raise RuntimeError("Synthetic export fixture not visible")
                stage("seed_100_synthetic_export_rows", seed_export_fixture)
                def fixture_rows_not_running():
                    states=json.loads(api("/api/cores/status")[1])
                    if any(value.get("running") for value in states.values()):
                        raise RuntimeError("Synthetic export rows unexpectedly started a managed core")
                stage("synthetic_export_rows_not_running", fixture_rows_not_running)
                def interactions():
                    from scripts.low_resource_interactions import request_interactions
                    samples = []
                    last = [0.0, None]
                    def observe(phase_name):
                        alive()
                        if phase_name != last[1] or time.monotonic()-last[0] >= 10:
                            sample = metrics(directory)
                            assert_no_oom(sample)
                            sample.update(observed_monotonic=time.monotonic(), driver_phase=phase_name,
                                          processes=process_snapshot(directory))
                            samples.append(sample)
                            report["interaction_service_samples"] = samples
                            checkpoint()
                            last[:] = [time.monotonic(), phase_name]
                    cookie = "; ".join(item.name+"="+item.value for item in jar)
                    report["interactions"] = request_interactions(args.work_dir, output, args.unit,
                        args.source_commit, origin, ca, cert, cookie, observe)
                stage("browser_visibility_tabs_exports_logout", interactions)
                stage("interaction_revoked_cookie_rejected", lambda: api("/api/auth/me", expected=401))
                stage("login_after_interaction_logout", login)
            if profile == 'data-backup':
                from scripts import low_resource_data as large
                report['large_data']={}
                stage('seed_1000_synthetic_nodes',lambda:large.seed_nodes(python,payload,data,cert,key))
                def large_nodes_not_running():
                    if len(json.loads(api('/api/inbounds')[1]))!=1000:raise RuntimeError('Large node fixture missing')
                    if any(row.get('running') for row in json.loads(api('/api/cores/status')[1]).values()):raise RuntimeError('Synthetic nodes started listeners')
                stage('large_synthetic_nodes_not_running',large_nodes_not_running)
                def routing_request(body,revision,expected):
                    headers={'Origin':origin,'X-VUI-Request':'1','Content-Type':'application/json'}
                    if revision is not None:headers['If-Match']='"'+revision+'"'
                    request=Request(origin+'/api/routing/mihomo',data=json.dumps(body).encode(),headers=headers,method='PUT')
                    try:response=opener.open(request,timeout=30)
                    except HTTPError as exc:response=exc
                    with response:
                        raw=response.read()
                        if response.status!=expected:raise RuntimeError('Unexpected routing write status')
                        return raw
                def configure_large_rules():
                    original_revision=json.loads(api('/api/routing/mihomo/snapshot')[1])['revision']
                    routing_request(large.routing_fixture(),original_revision,200)
                    saved=json.loads(api('/api/routing/mihomo/snapshot')[1]);revision=saved['revision']
                    expected_hash=large.digest(data/'mihomo-routing.json')
                    cases=[(large.routing_fixture(),None,428),(large.routing_fixture(),original_revision,409),
                        ({**large.routing_fixture(),'direct_domains':[f'extra-{n}.example.test' for n in range(2049)]},revision,422)]
                    for body,prior,status in cases:
                        routing_request(body,prior,status)
                        if large.digest(data/'mihomo-routing.json')!=expected_hash or json.loads(api('/api/routing/mihomo/snapshot')[1])['revision']!=revision:
                            raise RuntimeError('Rejected routing write changed saved data')
                    report['large_data']['routing_rejections']=[428,409,422]
                    report['large_data']['original']={'business':large.business_digest(data),'routing':expected_hash,'revision':revision}
                stage('large_rules_and_real_conditional_write_rejections',configure_large_rules)
                def anonymous_export_denied():
                    anonymous=build_opener(ProxyHandler({}),HTTPSHandler(context=context))
                    try:anonymous.open(origin+'/api/subscription/raw',timeout=10)
                    except HTTPError as exc:
                        if exc.code==401:return
                        raise
                    raise RuntimeError('Anonymous administrator export accepted')
                stage('large_anonymous_export_denied',anonymous_export_denied)
                grant=json.loads(stage('create_256_node_scoped_grant',lambda:api('/api/subscriptions',
                    {'label':'synthetic-large-data','server':'vpn.example.test','inbound_ids':list(range(1,257)),'formats':['raw']},expected=201))[1])
                grant_path=grant['paths']['raw']
                stage('synthetic_public_grant_before_backup',lambda:api(grant_path))
                def exports():
                    cookie='; '.join(item.name+'='+item.value for item in jar)
                    report['large_data']['exports']=large.request_exports(args.work_dir,output,args.unit,args.source_commit,origin,ca,cookie,alive)
                stage('large_four_format_exports_serial_and_concurrent',exports)
                def live_backup_rejected():
                    target=root/'must-not-exist-live-backup.zip'
                    result=large.installed_operation(python,payload,data,root,'reject_live_backup',target)
                    if result.get('rejected') is not True or target.exists():raise RuntimeError('Live backup was not cleanly rejected')
                    api('/api/auth/me')
                    report['large_data']['live_backup_rejected']=True
                stage('large_live_backup_rejected_without_stopping_panel',live_backup_rejected)
            from scripts.low_resource_proxy import run_proxy_smoke
            def sustained_load(binary, fixture, ca, port, health):
                from scripts.low_resource_sustained import CONCURRENCIES, request_load
                last_sample = [0.0]
                def observe():
                    alive()
                    health()
                    if time.monotonic() - last_sample[0] >= 30:
                        sample = metrics(directory)
                        sample["processes"] = process_snapshot(directory)
                        sample["observed_monotonic"] = time.monotonic()
                        assert_no_oom(sample)
                        report["stages"][-1].setdefault("samples", []).append(sample)
                        checkpoint()
                        last_sample[0] = time.monotonic()
                for concurrency in CONCURRENCIES:
                    last_sample[0]=0.0
                    stage(f"refresh_session_before_{concurrency}_connection_load", login)
                    result = stage(f"proxy_sustained_{concurrency}_connections_600_seconds",
                        lambda: request_load(args.work_dir, args.unit, args.source_commit,
                            concurrency, binary, fixture, ca, port, observe))
                    report["sustained_load"].append(result)
                    stage(f"panel_recovery_after_{concurrency}_connections", lambda: api("/api/auth/me"))
            def certificate_overlap(binary, fixture, ca, port, health, proxy_pid, watchdog_pid):
                from scripts.low_resource_certificates import request_overlap
                samples=[];last=[0.0]
                def identity(pid):
                    return dict(pid=pid,starttime_ticks=int(Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]))
                report['certificate_service_roles']={name:identity(pid) for name,pid in
                    (('worker',os.getpid()),('panel',running[0].pid),('proxy',proxy_pid),('watchdog',watchdog_pid))}
                def observe():
                    alive();health()
                    if time.monotonic()-last[0]>=5:
                        sample=metrics(directory);assert_no_oom(sample)
                        processes=process_snapshot(directory)
                        for process in processes:
                            try:process['starttime_ticks']=identity(process['pid'])['starttime_ticks']
                            except FileNotFoundError:process['starttime_ticks']=0
                        sample.update(observed_monotonic=time.monotonic(),processes=processes)
                        if 'manager' in report['certificate_service_roles']:
                            manager_pid=report['certificate_service_roles']['manager']['pid']
                            thread=report['certificate_manager_thread']
                            try:
                                from scripts.low_resource_accounting import start_ticks
                                sample['manager_thread']=dict(thread,observed_starttime_ticks=start_ticks(Path(f"/proc/{manager_pid}/task/{thread['tid']}/stat").read_text()))
                            except FileNotFoundError:sample['manager_thread']=None
                        samples.append(sample);report['certificate_service_samples']=samples
                        checkpoint();last[0]=time.monotonic()
                def bind_roles(manager_ready, responder_ready):
                    for name, row in (('manager',manager_ready['manager']),('responder',responder_ready['identity'])):
                        report['certificate_service_roles'][name]={key:row[key] for key in ('pid','starttime_ticks')}
                    report['certificate_roles_bound_monotonic']=time.monotonic()
                    report['certificate_manager_thread']=manager_ready['thread']
                    last[0]=0
                    observe()
                def overlap():
                    report['certificate_overlap']=request_overlap(args.work_dir,output,args.unit,args.source_commit,
                        payload,python,binary,fixture,ca,port,observe,bind_roles)
                stage('real_issue_and_due_renewal_with_10_connections_600_seconds',overlap)
                def panel_db_untouched():
                    with sqlite3.connect(data/'v-ui.db') as db:
                        if db.execute('SELECT COUNT(*) FROM managed_certificates').fetchone()[0] or db.execute('SELECT COUNT(*) FROM certificate_jobs').fetchone()[0]:
                            raise RuntimeError('Test certificate manager touched panel database')
                stage('original_panel_certificate_database_untouched',panel_db_untouched)
                stage('panel_recovery_after_certificate_overlap',lambda:api('/api/auth/me'))
            run_proxy_smoke(payload / "cores" / tools.target_arch() / "sing-box", root / "proxy-fixture",
                            output.with_suffix(""), stage, report,
                            lambda: api("/api/auth/me"),
                            sustained=sustained_load if profile == "sustained" else None,
                            idle_monitor=monitor_wait if profile == "sustained" else None,
                            overlap=certificate_overlap if profile == "certificates" else None,
                            accounting_monitor=accounting_wait if profile == "accounting" else None,
                            server_runtime=dict(python=python,payload=payload,data=data,root=root,origin=origin,
                                runtime_key=json.loads((release/'READY.json').read_text())['runtime_key']))
            old_cookies = list(jar)
            stage("logout", lambda: api("/api/auth/logout", {}))
            for cookie in old_cookies:
                jar.set_cookie(cookie)
            stage("logged_out_replayed_cookie_denied", lambda: api("/api/auth/me", expected=401))
            stage("login_before_backup", login)
            stage("stopped_panel", stop)
            backup = root / "stopped-backup.zip"
            if profile == 'data-backup':
                report['large_data']['logs']=stage('generate_256MiB_and_64_small_logs_inside_service',lambda:large.generate_log_fixture(data))
                def large_backup():
                    result=large.installed_operation(python,payload,data,root,'backup',backup)
                    report['large_data']['archive']=large.verify_archive(root,data,backup,report['large_data']['logs'])
                    return result['digest']
                digest=stage('stopped_backup',large_backup)
            else:
                digest = stage("stopped_backup", lambda: tools.backup(root, backup))
            with sqlite3.connect(data / "v-ui.db") as db:
                db.execute("UPDATE users SET username='modified-after-backup'")
            if profile == 'data-backup':
                stage('mutate_large_data_after_backup',lambda:large.mutate_after_backup(data))
                changed=large.current_snapshot(root,data)
                def bad_restore():
                    result=large.installed_operation(python,payload,data,root,'reject_bad_digest',backup,'0'*64)
                    if result.get('rejected') is not True or large.current_snapshot(root,data)!=changed:
                        raise RuntimeError('Bad digest damaged existing data or code')
                    report['large_data']['bad_digest_preserved_data']=True
                stage('large_bad_digest_restore_preserves_current_data',bad_restore)
                stage('stopped_restore',lambda:large.installed_operation(python,payload,data,root,'restore',backup,digest))
                report['large_data']['recovery']=stage('large_business_logs_revocation_and_original_data_retained',
                    lambda:large.verify_recovery(root,data,report['large_data']['original'],changed,report['large_data']['logs']))
            else:
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
            if profile == 'data-backup':
                stage('old_subscription_after_large_restore_denied',lambda:api(grant_path,expected=404))
            stage("restored_login", login)
            if profile == 'data-backup':
                stage('restored_large_nodes_not_running',large_nodes_not_running)
                def restored_exports():
                    for path in large.PATHS:large.check_body(path,api(path)[1])
                    report['large_data']['restored_exports_verified']=True
                stage('restored_large_exports_and_rules',restored_exports)
            if profile == "interactions":
                stage("restored_export_rows_still_not_running", fixture_rows_not_running)
            stage("restored_logout", lambda: api("/api/auth/logout", {}))
            stage("final_panel_stop", stop)
            if profile == 'data-backup':
                def no_token_in_logs():
                    for index in range(log_number):
                        if grant['token'] in (output.parent/f'{output.stem}-panel-{index}.log').read_text(errors='replace'):
                            raise RuntimeError('Synthetic subscription token leaked to panel log')
                    report['large_data']['synthetic_token_not_logged']=True
                stage('large_subscription_token_absent_from_logs',no_token_in_logs)
        report["outcome"] = "passed"
    except Exception as exc:
        report["outcome"] = "failed"
        # Avoid placing fake credentials, request bodies or response bodies in evidence.
        report["error_type"] = type(exc).__name__
        print(f"Low-resource worker failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        try:
            report["metrics"] = metrics(directory)
            report["peak_budget_assessment"] = peak_assessment(report["metrics"]["memory.peak"],
                memory_bytes(args.memory_mib), report["base_page_size_bytes"])
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
            "--property", "CPUQuota=100%", "--property", f"RuntimeMaxSec={runtime_seconds(args)}",
            "--property", "KillMode=control-group", "--property", "OOMPolicy=stop",
            "--setenv=GITHUB_ACTIONS=true", "--setenv=RUNNER_ENVIRONMENT=github-hosted",
            "--setenv=TMPDIR=" + str(work),
            sys.executable, "-B", str(Path(__file__).resolve()), "--worker", "--unit", unit,
            "--bundle", str(args.bundle.resolve()), "--source-commit", args.source_commit,
            "--output", str(worker_output), "--work-dir", str(work), "--memory-mib", str(args.memory_mib),
            "--duration-profile", duration_profile(args)]


def validate_sustained_stages(report, unit, commit):
    from scripts.low_resource_sustained import validate_result
    stages = {row["name"]: row for row in report.get("stages", [])}
    for name, seconds in (("panel_only_idle_1800_seconds", 1800),
                          ("panel_single_proxy_idle_1800_seconds", 1800),
                          *((f"proxy_sustained_{n}_connections_600_seconds", 600) for n in (1, 10, 50))):
        row = stages.get(name, {})
        elapsed = row.get("wall_seconds")
        if (row.get("outcome") != "passed" or type(elapsed) not in (int, float)
                or not math.isfinite(elapsed) or elapsed < seconds):
            raise RuntimeError("Sustained stage missing or shorter than contracted duration")
    from scripts.low_resource_accounting import validate_complete
    validate_complete(report, seconds=1800, interval=30)
    if [row.get("concurrency") for row in report.get("sustained_load", [])] != [1, 10, 50]:
        raise RuntimeError("Sustained connection ladder incomplete")
    for row in report["sustained_load"]:
        validate_result(row, unit, commit, row["concurrency"])


def coordinator(args) -> int:
    sys.path.insert(0, str(SOURCE))
    require_hosted_runner()
    budget = memory_bytes(args.memory_mib)
    profile = duration_profile(args)
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
               "duration_profile": profile, "requested_memory_mib": args.memory_mib, "worker_report": worker_output.name}
    with tempfile.TemporaryDirectory(prefix="low-resource-work-", dir=output.parent) as directory:
        command = systemd_command(args, unit, Path(directory), worker_output)
        try:
            broker = nullcontext()
            if profile == "sustained":
                sys.path.insert(0, str(SOURCE))
                from scripts.low_resource_sustained import LoadBroker
                broker = LoadBroker(Path(directory), output, unit, args.source_commit)
            elif profile == "interactions":
                sys.path.insert(0, str(SOURCE))
                from scripts.low_resource_interactions import InteractionBroker
                broker = InteractionBroker(Path(directory), output, unit, args.source_commit)
            elif profile == "certificates":
                sys.path.insert(0, str(SOURCE))
                from scripts.low_resource_certificates import CertificateBroker
                broker = CertificateBroker(Path(directory), output, unit, args.source_commit)
            elif profile == "data-backup":
                sys.path.insert(0,str(SOURCE))
                from scripts.low_resource_data import DataBroker
                broker=DataBroker(Path(directory),output,unit,args.source_commit)
            elif profile == "accounting":
                sys.path.insert(0, str(SOURCE))
            with broker, output.with_suffix(".log").open("w") as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                        timeout=runtime_seconds(args) + 60)
            summary["systemd_run_returncode"] = result.returncode
            if not worker_output.exists():
                raise RuntimeError("Worker accounting report missing (possible OOM or timeout)")
            report = json.loads(worker_output.read_text())
            if (result.returncode != 0 or report.get("outcome") != "passed" or report.get("complete") is not True
                    or report.get("unit") != unit or report.get("source_commit") != args.source_commit
                    or report.get("duration_profile") != profile
                    or report.get("requested_memory_mib") != args.memory_mib
                    or report.get("limits", {}).get("memory.max") != budget
                    or report.get("base_page_size_bytes") != os.sysconf("SC_PAGE_SIZE")):
                raise RuntimeError("Worker failed or its final accounting is incomplete")
            if profile == "sustained":
                validate_sustained_stages(report, unit, args.source_commit)
            elif profile == "interactions":
                from scripts.low_resource_interactions import validate_result, validate_service_samples
                validate_result(report.get("interactions", {}), unit, args.source_commit)
                validate_service_samples(report.get("interaction_service_samples"))
            elif profile == "certificates":
                from scripts.low_resource_certificates import validate_result
                value=report.get('certificate_overlap',{})
                from app.release_tools import target_key
                validate_result(value.get('external',{}),value.get('service',{}),unit,args.source_commit,target_key())
                from scripts.low_resource_certificates import validate_service_samples
                validate_service_samples(report.get('certificate_service_samples'),report.get('certificate_service_roles'),
                    value['external']['load_started_monotonic'],value['external']['load_started_monotonic']+value['external']['wall_seconds'],
                    report.get('certificate_roles_bound_monotonic'),report.get('certificate_manager_thread'))
            elif profile == 'data-backup':
                from scripts.low_resource_data import validate_complete
                validate_complete(report,unit,args.source_commit)
            if profile == 'accounting':
                from scripts.low_resource_accounting import validate_complete
                validate_complete(report)
            from scripts.low_resource_service_tree import validate_tree, canonical_cgroup, validate_sample_bindings
            from app.release_tools import target_key
            canonical_cgroup(report.get('service_cgroup'), unit)
            validate_tree(report.get('proxy_workload', {}).get('server_tree', {}), target_key(),
                          report.get('worker_pid'), report.get('service_cgroup'))
            validate_sample_bindings(report)
            assert_no_oom(report["metrics"])
            summary["peak_budget_assessment"] = peak_assessment(report["metrics"]["memory.peak"],
                budget, report["base_page_size_bytes"])
            if not summary["peak_budget_assessment"]["within_fixed_page_allowance"]:
                raise RuntimeError("Peak exceeded configured cgroup budget plus one disclosed base page")
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
    parser.add_argument("--duration-profile", choices=("smoke", "sustained", "interactions", "certificates", "data-backup", "accounting"), default="smoke")
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
