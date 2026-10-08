"""Bounded local VLESS/TCP/TLS resource smoke, not duration/throughput qualification.

The installed, verified bundle supplies both core processes. Only the server is
present during idle; the active phases additionally account the test client and
HTTP target in the worker's cgroup. No host trust or public network is used.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import http.client
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

from loopback_helpers import (CoreProcess, certificate_files, http_through,
                              start_http_target, unused_port)

UUID = "11111111-1111-1111-1111-111111111111"
WRONG_UUID = "22222222-2222-2222-2222-222222222222"
BODY = b"VUI-LOOPBACK-TARGET"
IDLE_SECONDS = 60


def server_config(port, cert, key):
    # Importing the adapter initializes its module's data directory. Keep that
    # incidental initialization inside this disposable fake fixture, and never
    # alter the caller's environment after import.
    original = os.environ.get("VUI_DATA_DIR")
    os.environ["VUI_DATA_DIR"] = str(Path(cert).parent / "adapter-data")
    try:
        from app.services.core_manager import SingBoxAdapter
    finally:
        if original is None:
            os.environ.pop("VUI_DATA_DIR", None)
        else:
            os.environ["VUI_DATA_DIR"] = original
    row = SimpleNamespace(enable=True, protocol="vless", tag="resource-vless", port=port,
                          settings={"users": [{"uuid": UUID, "flow": ""}]},
                          stream_settings={"tls": {"enabled": True,
                              "server_name": "vpn.example.test",
                              "certificate_path": str(cert), "key_path": str(key)}})
    config = SingBoxAdapter().build_config([row])
    config["inbounds"][0]["listen"] = "127.0.0.1"
    # Test-only logging: authentication evidence must be retained, not inferred
    # from an HTTP timeout. Only fake credentials occur in these logs.
    config["log"] = {"level": "debug", "timestamp": True}
    return config


def client_config(port, server_port, ca, credential=UUID):
    config = {"log": {"level": "debug", "timestamp": True},
              "inbounds": [{"type": "mixed", "listen": "127.0.0.1", "listen_port": port}],
              "outbounds": [{"type": "vless", "tag": "proxy", "server": "127.0.0.1",
                             "server_port": server_port, "uuid": credential, "network": "tcp",
                             "tls": {"enabled": True, "server_name": "vpn.example.test",
                                     "insecure": False, "certificate_path": str(ca)}}],
              "route": {"final": "proxy"}}
    assert_no_direct(config)
    return config


def assert_no_direct(config):
    if (config.get("route") != {"final": "proxy"}
            or len(config.get("outbounds", [])) != 1
            or config["outbounds"][0].get("type") != "vless"
            or config["outbounds"][0].get("tag") != "proxy"):
        raise RuntimeError("Resource client must have only the VLESS proxy route")


def assert_no_direct_log(path):
    log = path.read_text(errors="replace").lower()
    if "outbound/direct" in log or "outbound[direct]" in log:
        raise RuntimeError("Unexpected client DIRECT outbound evidence")


def require_rejection(request, deliveries, log_path, reasons, *, offset=0, timeout=12):
    """Timeout alone never passes; require fresh protocol/TLS evidence and zero delivery."""
    count = len(deliveries)
    deadline = time.monotonic() + timeout
    attempts = 0
    while True:
        attempts += 1
        try:
            status, body = request()
        except (OSError, http.client.HTTPException):
            pass
        else:
            if status == 200 or body == BODY:
                raise RuntimeError("Rejected proxy request unexpectedly succeeded")
        if len(deliveries) != count:
            raise RuntimeError("Rejected proxy request reached application target")
        log = log_path.read_text(errors="replace")[offset:].lower()
        if any(all(reason in line for reason in reasons) for line in log.splitlines()):
            return {"attempts": attempts, "reason_markers": list(reasons), "target_deliveries": 0}
        if time.monotonic() >= deadline:
            raise RuntimeError("Missing actual rejection reason: " + ", ".join(reasons))
        time.sleep(.05)


def run_proxy_smoke(binary: Path, root: Path, log_prefix: Path, stage, report, panel_check,
                    *, sustained=None, idle_monitor=None, overlap=None):
    root.mkdir(mode=0o700)
    report["proxy_workload"] = {
        "protocol": "VLESS/TCP/TLS", "core": "verified installed bundled sing-box",
        "client": "same bundled sing-box; synthetic explicit proxy-only config, not subscription export",
        "idle_seconds": 1800 if sustained else IDLE_SECONDS,
        "accounting": "panel plus one server during idle; active phases also include test client and local HTTP target in same cgroup",
        "scope": "short loopback regression only; not 30-minute idle, 10-minute load, 24-hour stability or VPS qualification",
        "positive": [], "negative": {}, "cleanup_complete": False,
    }
    evidence = report["proxy_workload"]
    if sustained:
        evidence["scope"] = "30-minute single-server idle and original short positive/negative controls; separate sustained_load records external ten-minute connection ladder"
    processes = []
    with ExitStack() as stack:
        ca, cert, key = certificate_files(root, "proxy")
        wrong_ca, _, _ = certificate_files(root, "wrong-proxy")
        port = unused_port()

        def launch(owner, name, config, listen):
            path = root / (name + ".json")
            path.write_text(json.dumps(config))
            path.chmod(0o600)
            core = CoreProcess(owner, [str(binary), "run", "-c", str(path)],
                               log_prefix.with_name(log_prefix.name + "-" + name + ".log"), None)
            processes.append(core)
            core.start(listen)
            return core

        server = stage("proxy_server_start", lambda: launch(stack, "proxy-server", server_config(port, cert, key), port))

        def healthy():
            if server.process.poll() is not None:
                raise RuntimeError("Proxy server exited unexpectedly")
            panel_check()

        def idle():
            healthy()
            if sustained:
                idle_monitor(1800, lambda: server_alive())
            else:
                time.sleep(IDLE_SECONDS)
            healthy()
        def server_alive():
            if server.process.poll() is not None:
                raise RuntimeError("Proxy server exited unexpectedly")
        stage("panel_single_proxy_idle_1800_seconds" if sustained else "panel_single_proxy_idle_60_seconds", idle)
        if sustained:
            sustained(binary, root, ca, port, server_alive)
        if overlap:
            overlap(binary, root, ca, port, server_alive, server.process.pid)
            healthy()
        target_port, deliveries = start_http_target(stack)
        client_port = unused_port()
        with ExitStack() as clients:
            client = stage("proxy_client_start", lambda: launch(clients, "proxy-client", client_config(client_port, port, ca), client_port))

            def load(concurrency, requests):
                before = len(deliveries)
                def one(_):
                    if http_through(client_port, "127.0.0.1", target_port) != (200, BODY):
                        raise RuntimeError("Positive proxy response failed")
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    list(pool.map(one, range(requests)))
                if len(deliveries) - before != requests or client.process.poll() is not None:
                    raise RuntimeError("Proxy delivery count or client liveness failed")
                healthy()
                evidence["positive"].append({"concurrency": concurrency, "requests": requests,
                                             "status_200": requests, "body_bytes": len(BODY),
                                             "target_deliveries": requests})
            stage("proxy_10_requests_concurrency_1", lambda: load(1, 10))
            stage("proxy_100_requests_concurrency_10", lambda: load(10, 100))
        assert_no_direct_log(client.log_path)
        for name, credential, trust, reasons in (
                ("wrong_uuid", WRONG_UUID, ca, ("unknown uuid",)),
                ("wrong_ca", UUID, wrong_ca, ("x509", "unknown authority"))):
            def negative():
                before = len(deliveries)
                with ExitStack() as clients:
                    client_port = unused_port()
                    offset = server.log_path.stat().st_size
                    bad = launch(clients, name, client_config(client_port, port, trust, credential), client_port)
                    log = server.log_path if name == "wrong_uuid" else bad.log_path
                    evidence["negative"][name] = require_rejection(
                        lambda: http_through(client_port, "127.0.0.1", target_port),
                        deliveries, log, reasons, offset=offset if name == "wrong_uuid" else 0)
                assert_no_direct_log(bad.log_path)
                evidence["negative"][name]["no_direct"] = True
                if len(deliveries) != before:
                    raise RuntimeError("Rejected proxy request was delivered late")
                healthy()
            stage("proxy_" + name + "_rejected", negative)
    if any(core.process is not None and core.process.poll() is None for core in processes):
        raise RuntimeError("Proxy child survived fixture cleanup")
    evidence["no_direct"] = True
    evidence["cleanup_complete"] = True
    panel_check()
