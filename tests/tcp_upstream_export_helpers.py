"""Two real HTTP panels for the D1 loopback gate, never imported by production.

Only network topology is substituted: loopback listeners/addresses, temporary
DoH, ordinary egress source binding and a private debug log. Authentication,
SQLAlchemy models, import workers, ProxyStore, coordinator, core ownership,
commit/recovery and HTTP handlers remain the production implementations.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import random
import re
import socket
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

import httpx
import psutil

from proxy_loopback_support import (
    DIRECT_EGRESS, DOH, INGRESS, SNI, TARGET, UPSTREAM, UPSTREAM_EGRESS,
)

ROOT = Path(__file__).resolve().parents[1]
ADMIN = "tcp-upstream-fixture"
PASSWORD = "fake-tcp-upstream-admin-only-2026"


def local_proxy_port():
    """D1-only choice outside Linux automatic ports, not an atomic reservation.

    A released bind(0) port can become a later panel/client source port before
    the real check. Read the kernel range, never change it or retry an API call.
    Explicit competing binds must still fail the unchanged production guard.
    """
    from app.services.proxy_apply import ProxyApplyError, preflight_local_listener
    from app.services.proxy_policy import LOCAL_TAG

    try:
        raw = Path('/proc/sys/net/ipv4/ip_local_port_range').read_text(encoding='ascii')
    except (OSError, UnicodeError):
        raise AssertionError('Cannot read Linux automatic port range') from None
    if re.fullmatch(r'[ \t]*[0-9]{1,5}[ \t]+[0-9]{1,5}[ \t]*\n?', raw) is None:
        raise AssertionError('Invalid Linux automatic port range')
    lower, upper = map(int, raw.split())
    if not 0 <= lower <= upper <= 65535:
        raise AssertionError('Invalid Linux automatic port range')
    candidates = [port for port in range(1024, 65536) if not lower <= port <= upper]
    if not candidates:
        raise AssertionError('No unprivileged port outside Linux automatic range')
    for port in random.SystemRandom().sample(candidates, min(128, len(candidates))):
        config = {'inbounds': [{'tag': LOCAL_TAG, 'type': 'mixed',
                               'listen': INGRESS, 'listen_port': port}]}
        try:
            preflight_local_listener(config, SimpleNamespace(process=None))
        except ProxyApplyError as exc:
            if exc.code == 'LOCAL_PORT_OCCUPIED':
                continue
            raise
        return port
    raise AssertionError('No available local proxy port within 128 candidate limit')


def install_loopback_topology(options):
    """Inject only existing Python fixture seams before main.app starts."""
    if os.environ.get("VUI_TCP_UPSTREAM_LOOPBACK") != "1":
        raise AssertionError("The real two-panel fixture requires explicit opt-in")
    if options["role"] not in {"source", "target"}:
        raise AssertionError("Unknown fixture panel role")
    from app.services.core_manager import SingBoxAdapter, core_manager
    from app.services.proxy_apply import ProxyApplyCoordinator
    from app.services.proxy_policy import AddressPolicy, VerifiedEndpoint, compile_proxy_policy
    from app.services.proxy_store import ProxyStore
    from app.services.upstream_profile import UpstreamProfile

    log_path = str(Path(options["core_log"]).resolve())
    adapter = core_manager.get("sing-box")
    ordinary_builder = SingBoxAdapter().build_config

    def build(rows):
        config = ordinary_builder(item for item in rows if item.core == "sing-box")
        config["log"] = {"level": "debug", "timestamp": False, "output": log_path}
        config["outbounds"][0]["inet4_bind_address"] = (
            UPSTREAM_EGRESS if options["role"] == "source" else DIRECT_EGRESS)
        if options["role"] == "source":
            for inbound in config["inbounds"]:
                if inbound["listen"] != "::":
                    raise AssertionError("Source binding changed before fixture narrowing")
                inbound["listen"] = UPSTREAM
        else:
            # A reachable ordinary resolver acts as a fallback canary. Protected
            # DNS must instead traverse the imported upstream's verified DoH.
            config["dns"] = {"servers": [{"type": "udp", "tag": "fixture-ordinary-dns",
                "server": INGRESS, "server_port": options["ordinary_dns_port"]}],
                "final": "fixture-ordinary-dns"}
            config["route"]["default_domain_resolver"] = "fixture-ordinary-dns"
        return config

    if options["role"] == "source":
        adapter.build_config = build
        return

    policy = AddressPolicy._for_tests(allowed_addresses=(TARGET, UPSTREAM, DOH),
                                      doh=(DOH, options["doh_port"], SNI))

    def resolve(profile_id, revision, value, *, self_addresses):
        # The dedicated export uses this explicit literal, so no name is
        # fabricated or resolved. The strict profile and exact-address guard
        # still run; there is no broad private-network/metadata bypass.
        profile = UpstreamProfile.from_dict(value)
        if str(ipaddress.ip_address(profile.server)) != UPSTREAM:
            raise AssertionError("Fixture only permits its exact source panel")
        addresses = policy.check_addresses((profile.server,), self_addresses=self_addresses)
        return VerifiedEndpoint(profile_id, revision, profile.server, addresses, int(time.time()))

    def compile_fixture(*args, **kwargs):
        compiled = compile_proxy_policy(*args, **kwargs, policy=policy)
        config = deepcopy(compiled.config)
        for inbound in config["inbounds"]:
            if inbound["type"] == "vless":
                if inbound["listen"] != "::":
                    raise AssertionError("Target binding changed before exact source validation")
                inbound["listen"] = INGRESS
        return replace(compiled, config=config)

    core_manager._proxy_coordinator = ProxyApplyCoordinator(
        ProxyStore(), adapter.runtime, build, core_manager.lock,
        rows_provider=core_manager._all_rows, address_provider=lambda _: (INGRESS,),
        resolver=resolve, compiler=compile_fixture)


def serve_panel():
    options = json.load(sys.stdin)
    install_loopback_topology(options)
    from app.services.auth_service import provision_admin
    from app.models.database import DB_PATH
    if not DB_PATH.exists():
        provision_admin(ADMIN, options.pop("password"))
    import main
    import uvicorn
    uvicorn.run(main.app, host=INGRESS, port=0, proxy_headers=False,
                use_colors=False, access_log=False)


class ObservedProcess:
    """Read-only identity of an actual panel-owned core watchdog process."""
    def __init__(self, pid):
        self.pid = pid
        self.created = psutil.Process(pid).create_time()

    def poll(self):
        try:
            process = psutil.Process(self.pid)
            if process.create_time() == self.created and process.status() != psutil.STATUS_ZOMBIE:
                return None
        except psutil.NoSuchProcess:
            pass
        return 0


class PanelCore:
    """Lifecycle adapter issuing real core HTTP calls for the shared ledger."""
    def __init__(self, panel, host, port):
        self.panel = panel
        self.log_path = panel.core_log
        self.fixture_endpoint = host, port
        self.process = None

    def observe(self):
        state = self.panel.request("GET", "/api/cores/status").json()["sing-box"]
        self.process = ObservedProcess(state["pid"]) if state["running"] else None
        return state

    def start(self, port, host=INGRESS):
        if (host, port) != self.fixture_endpoint:
            raise AssertionError("Remote core endpoint changed")
        self.panel.request("POST", "/api/cores/sing-box/restart")
        self.observe()
        self.assert_listener()

    def assert_listener(self):
        if self.process is None or self.process.poll() is not None:
            raise AssertionError("HTTP application did not leave a live core")
        host, port = self.fixture_endpoint
        watchdog = psutil.Process(self.process.pid)
        children = watchdog.children(recursive=True)
        cores = [child for child in children if Path(child.exe()).resolve() == self.panel.binary.resolve()]
        if len(cores) != 1:
            raise AssertionError("Expected exactly one unchanged pinned core child")
        sockets = cores[0].net_connections(kind="inet")
        listeners = [item for item in sockets if item.status == psutil.CONN_LISTEN and item.laddr.port == port]
        if {(item.laddr.ip, item.type) for item in listeners} != {(host, socket.SOCK_STREAM)}:
            raise AssertionError("Actual core does not exclusively own its loopback TCP listener")
        with socket.create_connection((host, port), timeout=2):
            pass
        self.panel.events.append({"event": "core-listening", "role": self.panel.role,
                                  "panel_pid": self.panel.server.pid, "watchdog_pid": watchdog.pid,
                                  "core_pid": cores[0].pid, "address": host, "port": port})

    def stop(self):
        if self.panel.server is not None and self.panel.server.poll() is None:
            self.panel.request("POST", "/api/cores/sing-box/stop")
            state = self.observe()
            if state["running"]:
                raise AssertionError("Core stop API left its process running")
        else:
            if self.process is not None and self.process.poll() is None:
                raise AssertionError("Panel exited but its core is still alive")
            self.process = None


class HTTPPanel:
    """One independent data tree, authenticated HTTP client and app process."""
    def __init__(self, stack, root, role, environment, *, doh_port, ordinary_dns_port, sanitize):
        self.root = Path(root)
        self.root.mkdir(mode=0o700)
        self.role, self.env, self.sanitize = role, environment.copy(), sanitize
        self.data = self.root / "data"
        self.core_log = self.root / "core.log"
        self.binary = Path(os.environ["VUI_TEST_CORES"]) / "sing-box"
        self.options = {"role": role, "core_log": str(self.core_log), "doh_port": doh_port,
                        "ordinary_dns_port": ordinary_dns_port, "password": PASSWORD}
        self.server = self.client = self.log = None
        self.events = []
        self.generation = 0
        stack.callback(self.close)
        self.start()

    def start(self):
        self.generation += 1
        self.server_log = self.root / f"panel-{self.generation}.log"
        self.log = self.server_log.open("w")
        self.server_log.chmod(0o600)
        env = {**self.env, "PYTHONPATH": str(ROOT) + os.pathsep + str(ROOT / "tests"),
               "PYTHONDONTWRITEBYTECODE": "1", "VUI_PUBLIC_ORIGIN": "",
               "VUI_DATA_DIR": str(self.data), "VUI_BIN_DIR": str(self.binary.parent),
               "VUI_CERTIFICATES_ENABLED": "0"}
        # New construction site: two parameterized real uvicorn instances.
        # Their core children use unmodified production CoreRuntime launches.
        self.server = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "--serve"],
            cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=self.log,
            stderr=subprocess.STDOUT, text=True)
        json.dump(self.options, self.server.stdin)
        self.server.stdin.close()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                raise AssertionError(self.sanitize(self.server_log.read_text(errors="replace")))
            match = re.search(r"Uvicorn running on http://127\.0\.0\.1:(\d+)",
                              self.server_log.read_text(errors="replace"))
            if match:
                self.base = "http://127.0.0.1:" + match.group(1)
                break
            time.sleep(.025)
        else:
            raise AssertionError("Real HTTP panel startup timed out")
        self.client = httpx.Client(base_url=self.base, trust_env=False, timeout=25,
                                  headers={"Origin": self.base, "X-VUI-Request": "1"})
        self.request("POST", "/api/auth/login", json={"username": ADMIN, "password": PASSWORD})
        self.request("GET", "/api/auth/me")
        self.events.append({"event": "panel-ready", "role": self.role, "pid": self.server.pid,
                            "generation": self.generation, "origin": self.base,
                            "data_identity": self.root.name})

    def request(self, method, path, *, expected=200, **kwargs):
        response = self.client.request(method, path, **kwargs)
        self.events.append({"event": "http", "role": self.role, "method": method,
                            "path": path, "status": response.status_code})
        if response.status_code != expected:
            logs = self.server_log.read_text(errors="replace")
            if self.core_log.exists():
                logs += "\n" + self.core_log.read_text(errors="replace")
            raise AssertionError(self.sanitize(f"{self.role} {method} {path}: "
                f"expected {expected}, got {response.status_code}: {response.text}\n{logs}"))
        return response

    def close(self):
        if self.server is not None and self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait(timeout=5)
                raise AssertionError("Panel failed to shut down cleanly")
        if self.client is not None:
            self.client.close()
            self.client = None
        if self.log is not None:
            self.log.close()
            self.log = None

    def restart_with_trust(self, trust):
        self.close()
        self.env["SSL_CERT_FILE"] = str(trust)
        self.start()

    def node(self, identity):
        # Read-only observation of this fake fixture's saved source model; no
        # service mutation or alternate path is used to create either profile.
        with sqlite3.connect(f"file:{self.data / 'v-ui.db'}?mode=ro", uri=True) as db:
            row = db.execute("SELECT settings, stream_settings FROM inbounds WHERE id = ?", (identity,)).fetchone()
        if row is None:
            raise AssertionError("HTTP-created source model was not saved")
        return tuple(json.loads(value) for value in row)

    def committed_config(self, state):
        revision = state["applied_revision"]
        path = self.data / "runtime" / "sing-box" / (revision + ".json")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != revision:
            raise AssertionError("Runtime evidence does not match its exact committed SHA")
        return json.loads(raw)


if __name__ == "__main__":
    if sys.argv[1:] != ["--serve"]:
        raise SystemExit("Only the isolated two-panel test launcher is supported")
    serve_panel()
