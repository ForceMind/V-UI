"""D1: independent HTTP source export -> target import/save/apply -> two hops.

VUI_TCP_UPSTREAM_LOOPBACK=1 is a fail-closed CI gate: missing pins, startup
restrictions (including netlink EPERM), API/runtime failures and missing actual
negative reasons fail. With no opt-in, only the four real cases are skipped.
No system trust, firewall, network namespace, external DNS or real account is
changed. Fixture-only seams are enumerated in tcp_upstream_export_helpers.py.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
import socket
import unittest

import httpx
import yaml

from app.services.proxy_policy import UPSTREAM_TAG
from app.services.proxy_store import ProxyStore
from app.services.upstream_import import select_upstream
from loopback_helpers import certificate_files
from proxy_loopback_support import (
    INGRESS, SNI, TARGET, UPSTREAM, UPSTREAM_EGRESS, ReadyMihomoProcess,
    udp_target, udp_through,
)
from tcp_upstream_export_helpers import HTTPPanel, PanelCore
from test_proxy_double_hop import (
    AUTH, FLOW, INGRESS_UUID, PASSWORD, REALITY_SNI, SHORT_ID, UPSTREAM_UUID,
    USERNAME, WRONG_SNI, WRONG_UUID, DoubleHopFixture,
)


@unittest.skipUnless(os.environ.get("VUI_TCP_UPSTREAM_LOOPBACK") == "1",
                     "real two-panel TCP export requires VUI_TCP_UPSTREAM_LOOPBACK=1")
class TCPUpstreamExportRealTests(DoubleHopFixture):
    def setUp(self):
        super().setUp()
        self.panels = {}
        for role in ("source", "target"):
            self.panels[role] = HTTPPanel(self.stack, self.root / role, role, self.env,
                doh_port=self.doh_port, ordinary_dns_port=self.ordinary_dns_port,
                sanitize=self.sanitized)
        self.source, self.destination = self.panels["source"], self.panels["target"]
        self.assertNotEqual(self.source.server.pid, self.destination.server.pid)
        self.assertNotEqual(self.source.base, self.destination.base)
        self.assertNotEqual(self.source.data.resolve(), self.destination.data.resolve())
        # Separate cookies, single-admin databases and sockets, not two clients
        # aimed at one ASGI app or a mocked TestClient boundary.
        with httpx.Client(base_url=self.destination.base, trust_env=False,
                          cookies=self.source.client.cookies) as outsider:
            response = outsider.get("/api/inbounds")
        self.assertEqual(response.status_code, 401)
        for panel in self.panels.values():
            settings = panel.request("GET", "/api/proxy/settings").json()
            self.assertFalse(settings["settings"]["local_proxy_enabled"])
            self.assertFalse(settings["settings"]["relay_enabled"])
            self.assertEqual(settings["upstreams"], [])
            self.assertIsNone(settings["active"])

    def apply_revision(self, revision, label):
        key = hashlib.sha256((label + revision + str(self.sequence)).encode()).hexdigest()
        self.sequence += 1
        checked = self.destination.request("POST", "/api/proxy/check",
            json={"base_revision": revision}).json()
        self.assertTrue(checked["valid"])
        self.assertFalse(checked["applied"])
        applied = self.destination.request("POST", "/api/proxy/apply",
            json={"base_revision": revision}, headers={"Idempotency-Key": key}).json()
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["phase"], "committed")
        self.assertEqual(applied["snapshot"]["active"]["revision"], revision)
        return applied

    def establish_nodes(self, mode):
        self.mode = mode
        upstream = self.upstream_config(mode)["inbounds"][0]
        stream = {"tls": deepcopy(upstream["tls"])}
        stream["_vui"] = {"security": mode, "server_name": upstream["tls"]["server_name"],
                           "client_fingerprint": "chrome", "skip_cert_verify": False}
        if mode == "tls":
            # Preserve ordered optional ALPN and Chrome in the actual artifact,
            # target private profile and running outbound, not just basic SNI.
            stream["tls"]["alpn"] = ["h2", "http/1.1"]
        else:
            stream["_vui"].update(reality_public_key=self.public_key, reality_short_id=SHORT_ID)
        self.source_payload = {"core": "sing-box", "protocol": "vless", "enable": True,
            "port": self.upstream_port, "remark": "two-panel source / 测试 " + mode,
            "tag": "source-http-created", "expiry_time": 0,
            "settings": {"users": deepcopy(upstream["users"])}, "stream_settings": stream}
        response = self.source.request("POST", "/api/inbounds", json=self.source_payload)
        self.source_id = response.json()["inbound"]["id"]
        self.assertNotIn(UPSTREAM_UUID, response.text)
        self.assertNotIn(self.private_key, response.text)
        saved_settings, saved_stream = self.source.node(self.source_id)
        self.assertTrue(saved_settings == self.source_payload["settings"], "Source credential model changed")
        self.assertTrue(saved_stream == stream, "Source transport/TLS model changed")
        self.upstream = PanelCore(self.source, UPSTREAM, self.upstream_port)
        self.upstream.observe()
        self.upstream.assert_listener()
        self.core_processes.append(self.upstream)

        # Commit the unchanged default-off snapshot before adding an ingress.
        # This ensures every target config goes through the production compiler
        # before only its listener is narrowed, including the initial node.
        initial = self.destination.request("GET", "/api/proxy/settings").json()
        self.apply_revision(initial["revision"], "initial-default-off")
        response = self.destination.request("POST", "/api/inbounds", json={
            "core": "sing-box", "protocol": "vless", "enable": True,
            "port": self.ingress_port, "remark": "independent target ingress", "tag": "target-http-created",
            "settings": {"users": [{"uuid": INGRESS_UUID}]},
            "stream_settings": {"tls": {"enabled": True, "server_name": SNI,
                "certificate_path": str(self.cert), "key_path": str(self.key)}}})
        self.target_id = response.json()["inbound"]["id"]
        self.ingress = PanelCore(self.destination, INGRESS, self.ingress_port)
        self.ingress.observe()
        self.ingress.assert_listener()
        self.core_processes.append(self.ingress)
        self.expected_profile = {"schema_version": 1, "protocol": "vless", "name": self.source_payload["remark"],
            "server": UPSTREAM, "server_port": self.upstream_port, "uuid": saved_settings["users"][0]["uuid"],
            "transport": "tcp", "encryption": "none", "flow": saved_settings["users"][0].get("flow", ""),
            "tls": {"mode": mode, "server_name": saved_stream["tls"]["server_name"],
                "alpn": saved_stream["tls"].get("alpn", []), "fingerprint": "chrome",
                "public_key": saved_stream["_vui"].get("reality_public_key", ""),
                "short_id": saved_stream["_vui"].get("reality_short_id", "")}}

    def export_source(self, source_format):
        source_model = self.source.node(self.source_id)
        source_snapshot = self.source.request("GET", "/api/proxy/settings").json()
        source_core = self.upstream.observe()
        response = self.source.request("POST", f"/api/subscription/upstream/{self.source_id}", json={
            "format": source_format, "server": UPSTREAM, "acknowledge_tcp_only": True})
        self.assertEqual(response.headers["content-type"].split(";")[0],
                         "text/plain" if source_format == "uri" else "application/yaml")
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertEqual(response.headers["x-vui-export-profile"], "tcp-upstream-v1")
        filename = "upstream-tcp.txt" if source_format == "uri" else "upstream-tcp.yaml"
        self.assertEqual(response.headers["content-disposition"], f'attachment; filename="{filename}"')
        self.assertTrue(self.source.node(self.source_id) == source_model, "Export mutated its source model")
        self.assertEqual(self.source.request("GET", "/api/proxy/settings").json(), source_snapshot)
        self.assertEqual(self.upstream.observe(), source_core, "Export applied/restarted the source core")
        raw = response.content
        self.assertEqual(raw, response.text.encode("utf-8"))
        self.assertTrue(select_upstream(raw, 0, source_format).to_dict() == self.expected_profile,
                        "Full API artifact profile differs from the persisted source model")
        for forbidden in (self.private_key, str(self.cert), str(self.key), "certificate_path", "key_path",
                          "handshake", "packetEncoding", "packet-encoding", "_vui"):
            self.assertNotIn(forbidden, response.text)
        if source_format == "clash-yaml":
            node = yaml.safe_load(raw)
            self.assertIs(node["udp"], False)
            self.assertNotIn("rules", node)
            self.assertNotIn("proxies", node)
        self.export_sha = hashlib.sha256(raw).hexdigest()
        return response.text

    def import_artifact(self, content, source_format):
        before = self.destination.request("GET", "/api/proxy/settings")
        preview_response = self.destination.request("POST", "/api/proxy/import/preview",
            json={"format": source_format, "content": content})
        preview = preview_response.json()
        self.assertEqual(preview["ignored_sections"], [])
        self.assertEqual(len(preview["candidates"]), 1)
        self.assertTrue(preview["candidates"][0]["supported"])
        self.assertEqual(preview["candidates"][0]["index"], 0)
        self.assertEqual(self.destination.request("GET", "/api/proxy/settings").json(), before.json())
        for secret in (UPSTREAM_UUID, WRONG_UUID, self.public_key, SHORT_ID):
            self.assertNotIn(secret, preview_response.text)
        # The UI's explicit selection is required even for one candidate.
        self.destination.request("POST", "/api/proxy/profiles", expected=422,
            json={"format": source_format, "content": content}, headers={"If-Match": before.headers["etag"]})
        self.assertEqual(self.destination.request("GET", "/api/proxy/settings").json(), before.json())
        saved = self.destination.request("POST", "/api/proxy/profiles", expected=201,
            json={"format": source_format, "content": content, "candidate_index": 0},
            headers={"If-Match": before.headers["etag"]})
        for secret in (UPSTREAM_UUID, WRONG_UUID, self.public_key, SHORT_ID):
            self.assertNotIn(secret, saved.text)
        value = saved.json()
        selected = next(item for item in value["snapshot"]["upstreams"] if item["id"] == value["upstream_id"])
        self.assertEqual(selected["source_format"], source_format)
        self.assertEqual(selected["source_fingerprint"], hashlib.sha256(content.encode()).hexdigest())
        return value["upstream_id"]

    def select_apply(self, profile_id):
        before = self.destination.request("GET", "/api/proxy/settings")
        settings = {key: value for key, value in before.json()["settings"].items() if key != "local_password_set"}
        settings.update(local_proxy_enabled=True, relay_enabled=True, selected_upstream_id=profile_id,
                        local_port=self.local_port, local_username=USERNAME, local_password=PASSWORD)
        saved = self.destination.request("PUT", "/api/proxy/settings", json=settings,
                                        headers={"If-Match": before.headers["etag"]}).json()
        result = self.apply_revision(saved["revision"], "imported-upstream")
        self.assertEqual(result["pinned_address"], UPSTREAM)
        state = self.ingress.observe()
        self.assertTrue(state["running"])
        self.assertEqual(state["applied_revision"], result["snapshot"]["active"]["core_revision"])
        self.assertFalse(state["dirty"])
        self.ingress.assert_listener()
        config = self.destination.committed_config(state)
        upstream = next(item for item in config["outbounds"] if item["tag"] == UPSTREAM_TAG)
        self.assertEqual(upstream["server"], UPSTREAM)
        self.assertEqual(upstream["server_port"], self.upstream_port)
        self.assertEqual(upstream["network"], "tcp")
        self.assertNotIn("insecure", upstream["tls"])
        self.assertNotIn("packet_encoding", upstream)
        self.current_state = "11"
        return upstream

    def successful_clients(self):
        for kind, (port, process) in self.clients.items():
            with self.subTest(client=kind):
                if kind == "mihomo":
                    self.assertIsInstance(process, ReadyMihomoProcess)
                    self.assertEqual(process.ready_marker.read_text(), f"{process.process.pid}\n")
                    self.assertTrue(str(process.ready_marker).endswith(f".ready-{process.generation}"))
                self.success(port, auth=None, protocol="socks5h")
        self.success(self.local_port, auth=AUTH)
        self.assertTrue(all(peer == UPSTREAM_EGRESS for peer, _, _ in self.answers.snapshot()))
        self.assertEqual(self.ordinary_answers.queries, [])

    def reject_invalid_artifacts(self, content, source_format):
        before = self.destination.request("GET", "/api/proxy/settings")
        if source_format == "uri":
            rejected = content.replace("#", "&packetEncoding=xudp#", 1)
        else:
            node = yaml.safe_load(content)
            node["udp"] = True
            rejected = yaml.safe_dump(node)
        preview = self.destination.request("POST", "/api/proxy/import/preview",
            json={"format": source_format, "content": rejected}).json()
        self.assertFalse(preview["candidates"][0]["supported"])
        self.assertEqual(preview["candidates"][0]["reason_code"], "UDP_CAPABILITY_NOT_QUALIFIED")
        self.destination.request("POST", "/api/proxy/profiles", expected=422,
            json={"format": source_format, "content": rejected, "candidate_index": 0},
            headers={"If-Match": before.headers["etag"]})
        self.assertEqual(self.destination.request("GET", "/api/proxy/settings").json(), before.json())

    def failing_requests(self, observed, reasons, *, host=TARGET):
        before_dns = self.answers.snapshot()
        with self.negative_phase():
            for kind, (port, _) in self.clients.items():
                with self.subTest(client=kind):
                    self.rejection(self.negative_request(port, auth=None, host=host), observed, reasons)
        self.assertEqual(self.answers.snapshot(), before_dns, "Failed upstream leaked business DNS or used fallback")

    def reject_application_udp(self):
        port, packets = udp_target(self.stack)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
            control.settimeout(2)
            control.sendto(b"reachable-control", (TARGET, port))
            self.assertEqual(control.recv(65535), b"VUI-UDP-CONTROL:reachable-control")
        before = [(INGRESS, b"reachable-control")]
        self.assertEqual(packets, before)
        self.evidence.udp.append((packets, Counter(before)))
        routes = [("local", self.local_port, AUTH)]
        for kind in ("singbox", "mihomo"):
            client_port, _ = self.start_client(kind, udp=True)
            routes.append((kind, client_port, None))
        with self.negative_phase():
            for kind, client_port, auth in routes:
                with self.subTest(udp_client=kind):
                    self.rejection(lambda: udp_through(client_port, TARGET, port, b"must-not-arrive", auth=auth),
                                   self.ingress, ("network=udp", "reject"))
                    self.assertEqual(packets, before)

    def exercise(self, mode, source_format):
        self.establish_nodes(mode)
        content = self.export_source(source_format)
        original_id = self.import_artifact(content, source_format)
        upstream = self.select_apply(original_id)
        self.assertTrue(upstream["uuid"] == self.expected_profile["uuid"], "Applied UUID differs")
        self.assertEqual(upstream["tls"]["server_name"], self.expected_profile["tls"]["server_name"])
        self.assertEqual(upstream["tls"]["utls"], {"enabled": True, "fingerprint": "chrome"})
        if mode == "tls":
            self.assertEqual(upstream["tls"]["alpn"], ["h2", "http/1.1"])
        else:
            self.assertEqual(upstream["flow"], FLOW)
            self.assertTrue(upstream["tls"]["reality"] == {"enabled": True, "public_key": self.public_key,
                            "short_id": SHORT_ID}, "Applied REALITY identity differs")
        self.start_clients()
        self.successful_clients()
        positive = {"mode": mode, "format": source_format, "artifact_sha256": self.export_sha,
                    "pins": {"sing-box": "1.14.2", "mihomo": "1.19.32"},
                    "instances": [event for panel in self.panels.values() for event in panel.events
                                  if event["event"] in {"panel-ready", "core-listening"}],
                    "https_sources": sorted({peer for peer, _, path in self.target.snapshot()
                                             if path != "/reachable-control"}),
                    "business_dns_sources": sorted({peer for peer, _, _ in self.answers.snapshot()}),
                    "ordinary_dns_queries": len(self.ordinary_answers.queries)}
        print("two-panel HTTP TCP upstream positive: " + self.sanitized(json.dumps(positive, sort_keys=True)))
        self.reject_invalid_artifacts(content, source_format)

        # Mutations start from actual API bytes, and are re-imported through the
        # same preview/selection/save/apply path. No hand-built upstream profile
        # is substituted for either the positive or these authentication cases.
        for failure in ("uuid", "sni"):
            with self.subTest(failure=failure):
                old, new = ((UPSTREAM_UUID, WRONG_UUID) if failure == "uuid" else
                            (REALITY_SNI if mode == "reality" else SNI, WRONG_SNI))
                self.assertIn(old, content)
                damaged = content.replace(old, new)
                selected = self.import_artifact(damaged, source_format)
                self.select_apply(selected)
                if failure == "uuid":
                    observed, reasons = self.upstream, ("unknown uuid:", WRONG_UUID)
                else:
                    observed, reasons = self.ingress, (("reality verification failed",) if mode == "reality"
                                                       else ("x509", WRONG_SNI))
                self.failing_requests(observed, reasons)
                self.select_apply(original_id)
                self.successful_clients()

        if mode == "tls":
            other_ca, _, _ = certificate_files(self.root, "untrusted-two-panel")
            # Only this target process's inherited trust changes. Restoring the
            # same committed state exercises actual panel/runtime recovery.
            self.destination.restart_with_trust(other_ca)
            self.ingress.observe()
            self.ingress.assert_listener()
            self.failing_requests(self.ingress, ("x509", "unknown authority"))
            self.destination.restart_with_trust(self.trust)
            self.ingress.observe()
            self.ingress.assert_listener()
            self.successful_clients()

        self.reject_application_udp()
        self.upstream.stop()
        self.failing_requests(self.ingress, ("connect: connection refused",),
                              host="stopped-upstream-two-panel.example.test")
        self.assertEqual(self.ordinary_answers.queries, [])
        if mode == "reality":
            self.assertGreater(self.reference.snapshot()["accepts"], 0)

        # Final lifetime audit precedes shutdown, then the same inherited final
        # audit runs after all fixture sockets close. Never reset the ledger.
        self.stop_evidence_processes()
        self.drain_evidence()
        for panel in self.panels.values():
            panel.close()
        # Observe actual destination private storage only after its owner exits;
        # no data-owner lock is bypassed. Compare every canonical field.
        active = ProxyStore(self.destination.data).load_active()
        self.assertEqual(active["settings"]["selected_upstream_id"], original_id)
        self.assertTrue(active["upstreams"][original_id] == self.expected_profile,
                        "Complete persisted UpstreamProfile differs from source model")
        report = {"mode": mode, "format": source_format, "artifact_sha256": self.export_sha,
                  "events": [event for panel in self.panels.values() for event in panel.events],
                  "https_deliveries": len(self.target.snapshot()),
                  "https_sources": sorted({peer for peer, _, path in self.target.snapshot()
                                           if path != "/reachable-control"}),
                  "business_dns_queries": len(self.answers.snapshot()),
                  "business_dns_sources": sorted({peer for peer, _, _ in self.answers.snapshot()}),
                  "ordinary_dns_queries": len(self.ordinary_answers.queries), "final_drain": "complete"}
        print("two-panel HTTP TCP upstream evidence: " + self.sanitized(json.dumps(report, sort_keys=True)))

    def test_tls_uri_real_export_import_and_double_hop(self):
        self.exercise("tls", "uri")

    def test_tls_yaml_real_export_import_and_double_hop(self):
        self.exercise("tls", "clash-yaml")

    def test_reality_uri_real_export_import_and_double_hop(self):
        self.exercise("reality", "uri")

    def test_reality_yaml_real_export_import_and_double_hop(self):
        self.exercise("reality", "clash-yaml")


if __name__ == "__main__":
    unittest.main()
