"""P4 bounded real ordinary-node routing, official stock cores only.

VUI_INBOUND_ROUTING_LOOPBACK=1 enables three real methods. Missing pins, EPERM,
HTTP contract failures and missing specific negative reasons FAIL, never skip.
One stock Mihomo process imports actual ordinary API artifacts and switches its
real ENTRY selector. One target panel-owned sing-box serves every node together.
No production code or frozen gate is patched; HTTPPanel injects only its existing
listener, source binding, fixture DNS and exact loopback address-policy seams.
"""
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlsplit
from types import SimpleNamespace

import httpx
import yaml

from inbound_routing_real_helpers import MixedDeliveryEvidence, uri_client, yaml_client
from loopback_helpers import DnsAnswers, _socks_udp_target, certificate_files, dns_query
from proxy_loopback_support import (
    BODY, DIRECT_EGRESS, INGRESS, SNI, TARGET, UPSTREAM, UPSTREAM_EGRESS,
    BusinessAnswers, DeliveryEvidence, ReadyMihomoProcess, Traffic, fresh_lines,
    https_through, listen_port, udp_target, udp_through,
)
from tcp_upstream_export_helpers import HTTPPanel, PanelCore
from test_proxy_double_hop import (
    AUTH, INGRESS_UUID, PASSWORD, STATE_WALK, UPSTREAM_UUID, USERNAME,
    WRONG_SNI, WRONG_UUID, DoubleHopFixture,
)
from test_tcp_upstream_export_real import TCPUpstreamExportRealTests as _D1

# Import functions only: importing a TestCase under an alias would discover its
# four frozen methods again. No inherited D1 methods are added to this gate.
export_source = _D1.export_source
import_artifact = _D1.import_artifact
del _D1

DIRECT_UUID = "55555555-5555-4555-8555-555555555555"
INHERIT_UUID = "66666666-6666-4666-8666-666666666666"


class InboundRoutingFixtureTests(unittest.TestCase):
    """Fixture guard tests are not real-core qualification."""
    def fixture(self):
        ordinary, protected, traffic = DnsAnswers("ordinary"), BusinessAnswers(), Traffic()
        evidence = MixedDeliveryEvidence(DeliveryEvidence(protected, ordinary))
        evidence.expect(traffic, DIRECT_EGRESS, "good.example.test", "/good")
        traffic.append(DIRECT_EGRESS, "good.example.test", "/good")
        return evidence, traffic

    def test_mixed_ledger_only_allows_explicit_ordinary_dns(self):
        evidence, _ = self.fixture()
        evidence.expect_ordinary_dns("direct.example.test")
        for family in (1, 28):
            evidence.ordinary.answer(dns_query("direct.example.test", family))
        evidence.assert_exact(self)
        evidence.ordinary.answer(dns_query("relay-leak.example.test", 1))
        with self.assertRaisesRegex(AssertionError, "Ordinary DNS"):
            evidence.assert_exact(self)

    def test_mixed_ledger_rejects_late_target_and_wrong_dns_source(self):
        evidence, traffic = self.fixture()
        traffic.append(DIRECT_EGRESS, "negative.example.test", "/late")
        with self.assertRaisesRegex(AssertionError, "late"):
            evidence.assert_exact(self)
        evidence, _ = self.fixture()
        evidence.expect_dns("relay.example.test")
        for family in (1, 28):
            evidence.answers.answer(dns_query("relay.example.test", family), DIRECT_EGRESS)
        with self.assertRaisesRegex(AssertionError, "Protected DNS"):
            evidence.assert_exact(self)

    def test_yaml_shell_preserves_node_fields_and_has_no_fallback(self):
        nodes = [{"name": "ordinary-direct", "uuid": DIRECT_UUID, "udp": True,
                  "packet-encoding": "xudp", "tls": True, "skip-cert-verify": False},
                 {"name": "ordinary-relay", "uuid": INGRESS_UUID, "udp": False}]
        original = deepcopy(nodes)
        config = yaml_client({"proxies": nodes}, [node["name"] for node in nodes], 1234, 1235)
        self.assertEqual(config["proxies"], original)
        config["proxies"][0]["uuid"] = "changed-copy"
        self.assertEqual(nodes, original)
        self.assertEqual(config["rules"], ["MATCH,ENTRY"])
        self.assertNotIn("DIRECT", json.dumps(config))
        self.assertNotIn("dns", config)
        with self.assertRaisesRegex(AssertionError, "exact selected entries"):
            yaml_client({"proxies": nodes}, ["ordinary-direct"], 1234, 1235)

    def test_uri_shell_references_unchanged_bytes_and_has_no_client_dns(self):
        raw = b"vless://fixture@127.0.0.1:1234?security=tls&type=tcp#ordinary"
        with tempfile.TemporaryDirectory() as directory:
            provider = Path(directory) / "ordinary.txt"
            provider.write_bytes(raw)
            config = uri_client(provider, 1234, 1235)
            self.assertEqual(Path(config["proxy-providers"]["ordinary"]["path"]).read_bytes(), raw)
            self.assertNotIn("proxies", config)
            self.assertNotIn("dns", config)
            self.assertNotIn("DIRECT", json.dumps(config))

    def test_transition_walk_has_exactly_twelve_distinct_directed_edges(self):
        states = {"00", "01", "10", "11"}
        edges = list(zip(STATE_WALK, STATE_WALK[1:]))
        self.assertEqual(len(edges), 12)
        self.assertEqual(set(edges), {(a, b) for a in states for b in states if a != b})

    def negative_fixture(self, path, *, upstream_running):
        case = InboundRoutingRealTests()
        case.evidence, traffic = self.fixture()
        case._negative_phase = None
        events = []
        def server(name, running):
            value = SimpleNamespace(log_path=path, fixture_endpoint=(INGRESS, 1234),
                process=SimpleNamespace(poll=lambda: None) if running else None)
            def stop():
                events.append("stop-" + name)
                value.process = None
            value.stop = stop
            value.name = name
            return value
        case.ingress = server("ingress", True)
        case.upstream = server("upstream", upstream_running)
        case.client = server("device-must-stay", True)
        case.drain_evidence = lambda baseline: (
            events.append("drain"), case.evidence.assert_targets(self, baseline))
        case.start_process = lambda process, port, host: events.append("start-" + process.name)
        return case, traffic, events

    def test_negative_phase_drains_owned_servers_keeps_device_and_stopped_upstream(self):
        for running in (False, True):
            with self.subTest(upstream_running=running), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "core.log"
                path.write_text("")
                case, _, events = self.negative_fixture(path, upstream_running=running)
                device = case.client.process
                with case.mixed_negative_phase():
                    pass
                self.assertEqual(events, ["stop-ingress", "stop-upstream", "drain", "start-ingress"]
                                 + (["start-upstream"] if running else []))
                self.assertIs(case.client.process, device)
                self.assertIsNone(case._negative_phase)

    def test_negative_cleanup_preserves_primary_failure_and_rejects_late_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "core.log"
            path.write_text("")
            case, traffic, events = self.negative_fixture(path, upstream_running=True)
            with self.assertRaisesRegex(ValueError, "missing real reason") as caught:
                with case.mixed_negative_phase():
                    traffic.append(DIRECT_EGRESS, "rejected.example.test", "/late")
                    raise ValueError("missing real reason")
            self.assertEqual(events, ["stop-ingress", "stop-upstream", "drain"])
            self.assertTrue(any("cleanup also failed" in note for note in caught.exception.__notes__))
            self.assertIsNone(case._negative_phase)


@unittest.skipUnless(os.getenv("VUI_INBOUND_ROUTING_LOOPBACK") == "1",
                     "real ordinary-node routing requires VUI_INBOUND_ROUTING_LOOPBACK=1")
class InboundRoutingRealTests(DoubleHopFixture):
    export_source = export_source
    import_artifact = import_artifact

    def setUp(self):
        super().setUp()
        self.evidence = MixedDeliveryEvidence(self.evidence)
        # Existing HTTPPanel explicitly requires its original opt-in inside its
        # child. Enable only that child environment; no global environment edit.
        environment = {**self.env, "VUI_TCP_UPSTREAM_LOOPBACK": "1"}
        self.panels = {role: HTTPPanel(self.stack, self.root / role, role, environment,
            doh_port=self.doh_port, ordinary_dns_port=self.ordinary_dns_port,
            sanitize=self.sanitized) for role in ("source", "target")}
        self.source, self.destination = self.panels["source"], self.panels["target"]
        self.assertNotEqual(self.source.server.pid, self.destination.server.pid)
        self.assertNotEqual(self.source.data.resolve(), self.destination.data.resolve())
        self.entries, self.artifacts, self.transitions_seen = {}, [], []
        self.current_state = "00"
        self.client = None
        for panel in self.panels.values():
            initial = panel.request("GET", "/api/proxy/settings").json()
            self.assertIsNone(initial["active"])
            self.assertEqual(initial["upstreams"], [])
            self.assertFalse(initial["settings"]["relay_enabled"])
            self.assertFalse(initial["settings"]["local_proxy_enabled"])

    def apply_revision(self, revision, label):
        checked = self.destination.request("POST", "/api/proxy/check",
            json={"base_revision": revision}).json()
        self.assertTrue(checked["valid"])
        self.assertFalse(checked["applied"])
        fingerprint = checked["source_fingerprint"]
        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")
        key = hashlib.sha256(f"{label}:{revision}:{self.sequence}".encode()).hexdigest()
        self.sequence += 1
        applied = self.destination.request("POST", "/api/proxy/apply", json={
            "base_revision": revision, "source_fingerprint": fingerprint},
            headers={"Idempotency-Key": key}).json()
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["phase"], "committed")
        self.assertEqual(applied["source_fingerprint"], fingerprint)
        self.assertEqual(applied["snapshot"]["active"]["revision"], revision)
        self.last_fingerprint = fingerprint
        self.last_apply = applied
        return applied

    def create_entry(self, mode, *, enabled=True, first_saved_only=False):
        port = self.ingress_port if mode == "relay" else listen_port()
        uid = {"direct": DIRECT_UUID, "relay": INGRESS_UUID, "inherit": INHERIT_UUID}[mode]
        payload = {"core": "sing-box", "protocol": "vless", "enable": enabled,
            "routing_mode": mode, "port": port, "remark": "ordinary-" + mode,
            "tag": "ordinary-" + mode, "settings": {"users": [{"uuid": uid}]},
            "stream_settings": {"tls": {"enabled": True, "server_name": SNI,
                "certificate_path": str(self.cert), "key_path": str(self.key)}}}
        response = self.destination.request("POST", "/api/inbounds", json=payload,
            expected=409 if first_saved_only else 200)
        if first_saved_only:
            detail = response.json()["detail"]
            self.assertIs(detail["saved"], True)
            self.assertIs(detail["applied"], False)
            snapshot = self.destination.request("GET", "/api/proxy/settings").json()
            self.assertIsNone(snapshot["active"])
            rows = self.destination.request("GET", "/api/inbounds").json()
            matches = [row for row in rows if row["tag"] == payload["tag"]]
            self.assertEqual(len(matches), 1)
            node = matches[0]
            self.assertIsNone(node["routing_status"]["applied_effective_mode"])
            self.assertNotEqual(node["routing_status"]["state"], "applied")
        else:
            node = response.json()["inbound"]
        self.assertEqual(node["routing_mode"], mode)
        self.assertNotIn(uid, response.text)
        self.entries[mode] = {"id": node["id"], "port": port, "uuid": uid,
                              "name": payload["remark"]}
        return node

    def observe_ingress(self):
        first = self.entries.get("relay") or self.entries["direct"]
        if self.ingress is None:
            self.ingress = PanelCore(self.destination, INGRESS, first["port"])
            self.core_processes.append(self.ingress)
        state = self.ingress.observe()
        self.assertTrue(state["running"])
        self.assertFalse(state["dirty"])
        self.ingress.assert_listener()
        # Same exact watchdog/core owns every enabled destination inlet.
        for entry in self.entries.values():
            other = PanelCore(self.destination, INGRESS, entry["port"])
            other_state = other.observe()
            self.assertEqual(other_state["pid"], state["pid"])
            self.assertEqual(other_state["applied_revision"], state["applied_revision"])
            other.assert_listener()
        return state

    def establish_upstream(self):
        stream = {"tls": {"enabled": True, "server_name": SNI,
            "certificate_path": str(self.cert), "key_path": str(self.key),
            "alpn": ["h2", "http/1.1"]},
            "_vui": {"security": "tls", "server_name": SNI,
                     "client_fingerprint": "chrome", "skip_cert_verify": False}}
        self.source_payload = {"core": "sing-box", "protocol": "vless", "enable": True,
            "port": self.upstream_port, "remark": "source overseas TLS",
            "tag": "source-http-created", "settings": {"users": [{"uuid": UPSTREAM_UUID}]},
            "stream_settings": stream}
        self.source_id = self.source.request("POST", "/api/inbounds",
            json=self.source_payload).json()["inbound"]["id"]
        self.upstream = PanelCore(self.source, UPSTREAM, self.upstream_port)
        self.core_processes.append(self.upstream)
        self.upstream.observe()
        self.upstream.assert_listener()
        self.expected_profile = {"schema_version": 1, "protocol": "vless",
            "name": self.source_payload["remark"], "server": UPSTREAM,
            "server_port": self.upstream_port, "uuid": UPSTREAM_UUID,
            "transport": "tcp", "encryption": "none", "flow": "",
            "tls": {"mode": "tls", "server_name": SNI, "alpn": ["h2", "http/1.1"],
                    "fingerprint": "chrome", "public_key": "", "short_id": ""}}
        content = self.export_source("uri")
        self.original_profile = self.import_artifact(content, "uri")
        self.upstream_artifact = content
        self.save_apply_state("00", self.original_profile)
        self.assertIsNone(self.last_apply["snapshot"]["active"]["pinned_address"])

    def save_apply_state(self, state, profile_id=None):
        before = self.destination.request("GET", "/api/proxy/settings")
        settings = {key: value for key, value in before.json()["settings"].items()
                    if key != "local_password_set"}
        settings.update(local_proxy_enabled=state[0] == "1", relay_enabled=state[1] == "1",
                        local_port=self.local_port, local_username=USERNAME, local_password=PASSWORD)
        if profile_id is not None:
            settings["selected_upstream_id"] = profile_id
        saved = self.destination.request("PUT", "/api/proxy/settings", json=settings,
            headers={"If-Match": before.headers["etag"]}).json()
        applied = self.apply_revision(saved["revision"], "state-" + state)
        self.current_state = state
        return applied

    def establish_mixed(self, *, inherit=False):
        self.establish_upstream()
        self.create_entry("direct")
        # This is the FIRST explicit relay activation: selected upstream is
        # already committed at00, with no endpoint. CRUD must pin/reconcile it.
        self.create_entry("relay")
        self.assertEqual(self.current_state, "00")
        if inherit:
            self.create_entry("inherit")
        self.observe_ingress()
        current = self.destination.request("GET", "/api/proxy/settings").json()
        self.assertEqual(current["active"]["pinned_address"], UPSTREAM)
        self.assertEqual(current["active"]["settings"]["selected_upstream_id"], self.original_profile)
        self.assertFalse(current["active"]["settings"]["relay_enabled"])
        self.assertFalse(current["active"]["settings"]["local_proxy_enabled"])
        self.assert_applied_modes()

    def assert_applied_modes(self):
        state = self.observe_ingress()
        snapshot = self.destination.request("GET", "/api/proxy/settings").json()
        self.assertEqual(snapshot["active"]["core_revision"], state["applied_revision"])
        self.assertIsNone(snapshot["pending"])
        self.assertEqual((snapshot["active"]["settings"]["local_proxy_enabled"],
                          snapshot["active"]["settings"]["relay_enabled"]),
                         (self.current_state[0] == "1", self.current_state[1] == "1"))
        rows = {item["id"]: item for item in self.destination.request("GET", "/api/inbounds").json()}
        identities = set()
        for mode, entry in self.entries.items():
            row = rows[entry["id"]]
            status = row["routing_status"]
            effective = ("relay" if self.current_state[1] == "1" else "direct") if mode == "inherit" else mode
            self.assertEqual(row["routing_mode"], mode)
            self.assertEqual(status["desired_effective_mode"], effective)
            self.assertEqual(status["applied_effective_mode"], effective)
            self.assertEqual(status["state"], "applied")
            self.assertRegex(status["policy_identity"], r"^[0-9a-f]{64}$")
            identities.add(status["policy_identity"])
        self.assertEqual(len(identities), 1)
        checked = self.destination.request("POST", "/api/proxy/check",
            json={"base_revision": snapshot["active"]["revision"]}).json()
        self.assertEqual(checked["candidate_revision"], state["applied_revision"])
        self.assertRegex(checked["source_fingerprint"], r"^[0-9a-f]{64}$")
        self.last_fingerprint = checked["source_fingerprint"]
        return identities.pop(), self.last_fingerprint

    def start_ordinary_client(self, source_format):
        names = [entry["name"] for entry in self.entries.values()]
        port, controller_port = listen_port(), listen_port()
        home = self.path("ordinary-mihomo", "home")
        home.mkdir()
        if source_format == "clash-yaml":
            response = self.destination.request("GET", "/api/subscription/mihomo.yaml",
                params={"host": INGRESS, "core": "sing-box"})
            raw = response.content
            exported = yaml.safe_load(raw)
            for node in exported["proxies"]:
                entry = next(value for value in self.entries.values() if value["name"] == node["name"])
                self.assertEqual((node["server"], node["port"], node["uuid"]),
                                 (INGRESS, entry["port"], entry["uuid"]))
                self.assertEqual((node["type"], node["network"], node["tls"],
                                  node["skip-cert-verify"], node["servername"]),
                                 ("vless", "tcp", True, False, SNI))
                self.assertEqual(node.get("flow", ""), "")
                if node["name"] == self.entries["direct"]["name"]:
                    self.assertIs(node["udp"], True)
                    self.assertEqual(node["packet-encoding"], "xudp")
            config = yaml_client(exported, names, port, controller_port)
            self.assertEqual(config["proxies"], exported["proxies"])
        else:
            raws = []
            for entry in self.entries.values():
                response = self.destination.request("GET", f"/api/subscription/link/{entry['id']}",
                    params={"host": INGRESS})
                raw = response.content
                uri = urlsplit(raw.decode())
                query = parse_qs(uri.query)
                self.assertEqual((uri.scheme, uri.hostname, uri.port, uri.username),
                                 ("vless", INGRESS, entry["port"], entry["uuid"]))
                self.assertEqual(query["security"], ["tls"])
                self.assertEqual(query["type"], ["tcp"])
                self.assertEqual(query["sni"], [SNI])
                self.assertNotIn("routing_mode", query)
                self.assertNotIn("flow", query)
                raws.append(raw)
                self.artifacts.append({"format": "ordinary-uri-entry",
                    "sha256": hashlib.sha256(raw).hexdigest(), "entry": entry["name"]})
            # Joining untouched API byte strings with a provider record separator
            # does not parse/rebuild/replace any exported URI or credential.
            raw = b"\n".join(raws)
            provider = home / "ordinary.txt"
            provider.write_bytes(raw)
            provider.chmod(0o600)
            self.assertEqual(provider.read_bytes().split(b"\n"), raws)
            config = uri_client(provider, port, controller_port)
        for forbidden in (UPSTREAM_UUID, UPSTREAM, USERNAME, PASSWORD, str(self.cert), str(self.key),
                          "certificate_path", "key_path", "routing_mode", "selected_upstream_id"):
            self.assertNotIn(forbidden.encode(), raw)
        self.artifacts.append({"format": source_format, "sha256": hashlib.sha256(raw).hexdigest()})
        self.assertNotIn("DIRECT", json.dumps(config))
        self.assertNotIn("dns", config, "Device DNS would hide the entry's domain routing")
        path = home / "config.yaml"
        path.write_text(yaml.safe_dump(config, sort_keys=False))
        path.chmod(0o600)
        checked = subprocess.run([self.mihomo, "-t", "-d", str(home), "-f", str(path)],
            text=True, capture_output=True, timeout=15, env=self.env)
        self.assertEqual(checked.returncode, 0, self.sanitized(checked.stdout + checked.stderr))
        process = ReadyMihomoProcess(self.stack,
            [self.mihomo, "-d", str(home), "-f", str(path)], self.path("ordinary-mihomo", "log"), self.env)
        self.start_process(process, port)
        self.client = process
        self.client_port = port
        self.controller = self.stack.enter_context(httpx.Client(
            base_url=f"http://{INGRESS}:{controller_port}", trust_env=False, timeout=5,
            headers={"Authorization": "Bearer fake-inbound-controller-only"}))
        self.client_pid = process.process.pid
        self.client_incarnations = [self.client_pid]
        self.select_entry(next(iter(self.entries)))

    def select_entry(self, mode):
        self.assertEqual(self.client.process.pid, self.client_pid)
        self.assertIsNone(self.client.process.poll())
        self.assertEqual(self.client.ready_marker.read_text(), f"{self.client_pid}\n")
        response = self.controller.put("/proxies/ENTRY", json={"name": self.entries[mode]["name"]})
        self.assertEqual(response.status_code, 204, response.text)
        selected = self.controller.get("/proxies/ENTRY")
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json()["now"], self.entries[mode]["name"])
        self.assertEqual(set(selected.json()["all"]), {entry["name"] for entry in self.entries.values()})

    def positive(self, mode=None, *, literal=False):
        self.assertIsNone(self._negative_phase)
        self.evidence.assert_exact(self)
        if mode is not None:
            self.select_entry(mode)
        effective = ("relay" if self.current_state[1] == "1" else "direct") if mode == "inherit" else mode
        proxied = mode is None or effective == "relay"
        marker = "ordinary-" + str(time.monotonic_ns())
        host = TARGET if literal else marker + ".example.test"
        path = "/" + marker
        if not literal:
            if proxied:
                self.evidence.expect_dns(host)
            else:
                # Register only a fresh DIRECT lookup. SOCKS5h passes this exact
                # name to the entry; the literal entry endpoint needs no DNS.
                self.ordinary_answers.hosts[host] = TARGET
                self.evidence.expect_ordinary_dns(host)
        peer = UPSTREAM_EGRESS if proxied else DIRECT_EGRESS
        self.evidence.expect(self.target, peer, host, path)
        result = https_through(self.local_port if mode is None else self.client_port,
            host, self.target_port, self.ca, marker, protocol="socks5h",
            auth=AUTH if mode is None else None)
        self.assertEqual(result, (200, BODY), self.logs())
        self.evidence.assert_exact(self)
        self.assertIn((peer, host, path), self.target.snapshot())
        return host

    def assert_host_switch(self):
        if self.current_state[0] == "1":
            self.positive()
        else:
            with self.assertRaises(ConnectionRefusedError):
                socket.create_connection((INGRESS, self.local_port), timeout=.5)

    @contextmanager
    def mixed_negative_phase(self):
        """Stop owned server cores, fully drain, then restore original servers.

        Keep the same Mihomo device process and imported configuration. Stopping
        both panel cores closes their target/DoH sockets before the inherited
        bounded connection drain and continuous 0.2 s quiet window. A previously
        stopped upstream is never restarted here. No observed baseline is reset.
        """
        self.assertIsNone(self._negative_phase)
        self.evidence.assert_exact(self)
        baseline = self.evidence.target_baseline()
        offset = len(self.ingress.log_path.read_text(errors="replace"))
        self._negative_phase = {"baseline": baseline, "dns": False}
        primary = None
        try:
            yield
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                running = []
                for process in (self.ingress, self.upstream):
                    if process is not None:
                        if process.process is not None and process.process.poll() is None:
                            running.append(process)
                        process.stop()
                self.drain_evidence(baseline)
                self.assertNotIn("outbound/direct[direct]",
                    self.ingress.log_path.read_text(errors="replace")[offset:].lower(),
                    "Negative-phase drain revealed a late DIRECT attempt")
                if primary is None:
                    for process in running:
                        host, port = process.fixture_endpoint
                        self.start_process(process, port, host)
            except BaseException as cleanup:
                if primary is None:
                    raise
                primary.add_note("Mixed negative cleanup also failed: " + str(cleanup))
            finally:
                self._negative_phase = None

    def reject_once(self, request, observed, reasons):
        baseline = self._negative_phase["baseline"]
        offset = len(observed.log_path.read_text(errors="replace"))
        error = None
        try:
            result = request()
        except (OSError, http.client.HTTPException) as exc:
            error = exc
        else:
            self.fail("Rejected operation unexpectedly succeeded: " + repr(result))
        deadline = time.monotonic() + 12
        while True:
            self.evidence.assert_targets(self, baseline)
            self.evidence.assert_exact(self)
            lines = [line for line in fresh_lines(observed.log_path, offset)
                     if all(reason in line for reason in reasons)]
            if lines:
                print("ordinary relay refusal: " + self.sanitized(lines[-1]))
                return
            if time.monotonic() >= deadline:
                failure = AssertionError("No fresh actual rejection reason " + repr(reasons))
                failure.add_note(self.rejection_failure_context(observed, error, request))
                raise failure from error
            time.sleep(.05)

    def reject_relay(self, observed, reasons, *, domain=False):
        self.select_entry("relay")
        host = "rejected-" + str(time.monotonic_ns()) + ".example.test" if domain else TARGET
        with self.mixed_negative_phase():
            self.reject_once(self.negative_request(self.client_port, auth=None, host=host), observed, reasons)

    def udp_check(self, *, rejected):
        port, packets = udp_target(self.stack)
        payload = b"ordinary-udp-" + str(time.monotonic_ns()).encode()
        control_payload = b"reachable-control-" + str(time.monotonic_ns()).encode()
        expected = Counter({(INGRESS, control_payload): 1})
        self.evidence.udp.append((packets, expected))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
            control.settimeout(2)
            control.sendto(control_payload, (TARGET, port))
            self.assertEqual(control.recv(65535), b"VUI-UDP-CONTROL:" + control_payload)
        self.evidence.assert_exact(self)
        self.select_entry("relay" if rejected else "direct")
        if rejected:
            with self.mixed_negative_phase():
                self.reject_once(lambda: udp_through(self.client_port, TARGET, port, payload),
                                 self.ingress, ("network=udp", "reject"))
        else:
            expected[(DIRECT_EGRESS, payload)] += 1
            response = udp_through(self.client_port, TARGET, port, payload)
            self.assertEqual(response, b"\x00\x00\x00" + _socks_udp_target(TARGET, port)
                             + b"VUI-UDP-CONTROL:" + payload)
            self.evidence.assert_exact(self)

    def finish_report(self, label, *, positives, ordinary_dns, protected_dns):
        self.stop_evidence_processes()
        self.drain_evidence()
        for panel in self.panels.values():
            panel.close()
        self.evidence.assert_exact(self)
        self.assertEqual(len(self.target.snapshot()) - 1, positives)
        self.assertEqual(len(self.evidence.ordinary_snapshot()), ordinary_dns)
        self.assertEqual(len(self.answers.snapshot()), protected_dns)
        self.assertEqual(self.client_incarnations, [self.client_pid], "Device process changed during the case")
        print("ordinary routing evidence: " + self.sanitized(json.dumps({
            "case": label, "pins": {"sing-box": "1.14.2", "mihomo": "1.19.32"},
            "artifacts": self.artifacts, "client_incarnations": self.client_incarnations,
            "transitions": self.transitions_seen,
            "https_deliveries": len(self.target.snapshot()),
            "https_sources": sorted({peer for peer, _, path in self.target.snapshot()
                                      if path != "/reachable-control"}),
            "ordinary_dns_queries": len(self.evidence.ordinary_snapshot()),
            "protected_dns_queries": len(self.answers.snapshot()),
            "owned_core_processes": len(self.core_processes), "final_drain": "complete",
            "events": [event for panel in self.panels.values() for event in panel.events]
        }, sort_keys=True)))

    def test_first_direct_at00_without_upstream_yaml_and_legacy_udp(self):
        self.create_entry("direct", first_saved_only=True)
        snapshot = self.destination.request("GET", "/api/proxy/settings").json()
        self.assertEqual(snapshot["upstreams"], [])
        self.assertIsNone(snapshot["settings"]["selected_upstream_id"])
        self.apply_revision(snapshot["revision"], "first-direct-no-upstream")
        self.assertIsNone(self.last_apply["snapshot"]["active"]["pinned_address"])
        self.assert_applied_modes()
        self.start_ordinary_client("clash-yaml")
        self.positive("direct", literal=True)
        self.positive("direct")
        self.assert_host_switch()
        self.udp_check(rejected=False)
        self.finish_report("first-direct-no-upstream", positives=2, ordinary_dns=2, protected_dns=0)

    def test_ordinary_yaml_same_instance_four_states_twelve_transitions(self):
        self.establish_mixed(inherit=True)
        self.start_ordinary_client("clash-yaml")
        old_identity, old_fingerprint = self.assert_applied_modes()
        for index, state in enumerate(STATE_WALK):
            with self.subTest(state=state, step=index):
                if index:
                    previous = STATE_WALK[index - 1]
                    applied = self.save_apply_state(state)
                    identity, fingerprint = self.assert_applied_modes()
                    self.assertNotEqual(identity, old_identity)
                    self.assertNotEqual(fingerprint, old_fingerprint)
                    self.assertEqual(fingerprint, applied["source_fingerprint"])
                    old_identity, old_fingerprint = identity, fingerprint
                    self.transitions_seen.append(previous + "->" + state)
                for mode in ("direct", "relay", "inherit"):
                    self.positive(mode)
                self.assert_host_switch()
        self.assertEqual(len(set(self.transitions_seen)), 12)
        self.finish_report("four-states-twelve-transitions", positives=45, ordinary_dns=40, protected_dns=50)

    def test_ordinary_uri_two_nodes_upstream_failures_and_udp_fail_closed(self):
        self.establish_mixed()
        self.start_ordinary_client("uri")
        self.positive("direct")
        self.positive("relay")
        for failure in ("uuid", "sni", "ca", "stopped"):
            with self.subTest(upstream_failure=failure):
                if failure in ("uuid", "sni"):
                    old, new = (UPSTREAM_UUID, WRONG_UUID) if failure == "uuid" else (SNI, WRONG_SNI)
                    damaged = self.upstream_artifact.replace(old, new)
                    self.assertNotEqual(damaged, self.upstream_artifact)
                    profile_id = self.import_artifact(damaged, "uri")
                    self.save_apply_state("00", profile_id)
                    observed, reasons = ((self.upstream, ("unknown uuid:", WRONG_UUID)) if failure == "uuid"
                                         else (self.ingress, ("x509", WRONG_SNI)))
                elif failure == "ca":
                    other_ca, _, _ = certificate_files(self.root, "untrusted-mixed")
                    self.destination.restart_with_trust(other_ca)
                    observed, reasons = self.ingress, ("x509", "unknown authority")
                else:
                    self.upstream.stop()
                    observed, reasons = self.ingress, ("connect: connection refused",)
                self.assert_applied_modes()
                self.positive("direct")
                self.reject_relay(observed, reasons, domain=failure == "stopped")
                if failure in ("uuid", "sni"):
                    self.save_apply_state("00", self.original_profile)
                elif failure == "ca":
                    self.destination.restart_with_trust(self.trust)
                else:
                    self.upstream.start(self.upstream_port, UPSTREAM)
                self.assert_applied_modes()
                self.positive("direct")
                self.positive("relay")
        self.udp_check(rejected=True)
        self.finish_report("uri-upstream-negatives-and-udp", positives=14, ordinary_dns=18, protected_dns=10)


if __name__ == "__main__":
    unittest.main()
