"""Fixed official cores driven by the actual scoped HTTPUpgrade YAML export."""
from contextlib import ExitStack
from copy import deepcopy
import json
import time
import unittest
from unittest.mock import patch

import yaml

from app.models import database
from app.services import routing_store
from app.services.core_manager import SingBoxAdapter, core_manager
import test_subscription_tokens as grants
from test_xhttp_preflight_loopback import XHTTPFixture, fixed_cores_available
from loopback_helpers import CoreProcess, require_rejection_evidence, unused_port
from proxy_loopback_support import ReadyMihomoProcess
from xhttp_helpers import HOST, PATH, SNI, UUID, WRONG_SNI, WRONG_UUID, mihomo_config


@unittest.skipUnless(fixed_cores_available(), "fixed official cores and Mihomo required")
class HTTPUpgradePublicLoopbackTests(XHTTPFixture, unittest.TestCase):
    def setUp(self):
        grants.SubscriptionTokenTests.setUp(self)
        super().setUp()
        self.stack.enter_context(patch.object(routing_store, "ROUTING_FILE", self.root / "routing.json"))
        self.server_port = unused_port()
        with patch.object(core_manager, "validate_inbound_change"), patch("app.api.inbounds.apply_checked", return_value={}):
            created = self.client.post("/api/inbounds", headers=grants.HEADERS, json={
                "core": "sing-box", "protocol": "vless", "port": self.server_port,
                "remark": "ONLY_PROXY", "settings": {"users": [{"uuid": UUID}]},
                "profile": {"transport": "httpupgrade", "security": "tls", "flow": "",
                            "host": HOST, "path": PATH, "server_name": SNI,
                            "certificate_path": str(self.cert), "key_path": str(self.key)},
            })
        self.assertEqual(created.status_code, 200, created.text)
        row = created.json()["inbound"]
        self.assertTrue(row["mihomo_yaml_only"])
        self.assertFalse(row["share_eligible"])
        self.assertFalse(row["upstream_export_eligible"])
        self.assertNotIn(UUID, created.text)
        self.assertNotIn(str(self.root), created.text)
        self.node_id = row["id"]
        issued = grants.SubscriptionTokenTests.issue(self, server="127.0.0.1",
            inbound_ids=[self.node_id], formats=["mihomo.yaml"])
        native = grants.SubscriptionTokenTests.public_get(self, issued["paths"]["mihomo.yaml"])
        self.assertEqual(native.status_code, 200)
        self.assertNotIn(str(self.root), native.text)
        self.proxy, = yaml.safe_load(native.text)["proxies"]
        self.assertEqual(self.proxy, {
            "name": "ONLY_PROXY", "type": "vless", "server": "127.0.0.1", "port": self.server_port,
            "uuid": UUID, "udp": False, "tls": True, "servername": SNI, "network": "ws",
            "skip-cert-verify": False, "client-fingerprint": "chrome", "alpn": ["http/1.1"],
            "ws-opts": {"path": PATH, "headers": {"Host": HOST}, "v2ray-http-upgrade": True},
        })

    def start_server(self):
        with database.SessionLocal() as db:
            config = SingBoxAdapter().build_config([db.get(database.Inbound, self.node_id)])
        inbound, = config["inbounds"]
        self.assertEqual(inbound["transport"], {"type": "httpupgrade", "host": HOST, "path": PATH})
        self.assertEqual(inbound["tls"]["alpn"], ["http/1.1"])
        inbound["listen"] = "127.0.0.1"
        config["log"] = {"level": "debug", "timestamp": False}
        path = self.write_config(config, "product-httpupgrade")
        self.checked([self.singbox, "check", "-c", str(path)])
        server = CoreProcess(self.stack, [self.singbox, "run", "-c", str(path)],
                             self.unique_path("server", "log"), self.env)
        server.start(self.server_port)
        self.verify_server_alpn(self.server_port, "http/1.1")
        return server

    def start_client(self, stack, failure):
        proxy = deepcopy(self.proxy)
        env = self.env.copy()
        if failure == "uuid":
            proxy["uuid"] = WRONG_UUID
        elif failure == "ca":
            env["SSL_CERT_FILE"] = str(self.wrong_ca)
        elif failure == "sni":
            proxy["servername"] = WRONG_SNI
        elif failure == "host":
            proxy["ws-opts"]["headers"]["Host"] = "other.example.test"
        elif failure == "path":
            proxy["ws-opts"]["path"] = "/wrong-httpupgrade/"
        elif failure == "upgrade":
            proxy["ws-opts"].pop("v2ray-http-upgrade")
        port = unused_port()
        config = mihomo_config(port, proxy=proxy)
        config["ipv6"] = ":" in self.target_host
        self.assertEqual(config["rules"], ["MATCH,ONLY_PROXY"])
        self.assertNotIn("DIRECT", json.dumps(config))
        home = self.unique_path("mihomo-home", "data")
        home.mkdir()
        path = self.unique_path("mihomo", "yaml")
        path.write_text(yaml.safe_dump(config, sort_keys=False))
        path.chmod(0o600)
        command = [self.mihomo, "-d", str(home), "-f", str(path)]
        self.checked([*command, "-t"], env=env)
        process = ReadyMihomoProcess(stack, command, self.unique_path("mihomo", "log"), env)
        process.start(port)
        return port, process

    def verify_yaml(self, failure=None):
        self.start_target("sing-box")
        server = self.start_server()
        before = len(self.requests)
        with ExitStack() as positive:
            port, client = self.start_client(positive, None)
            self.assert_success(port)
            self.assertIsNone(client.process.poll())
        self.assertEqual(len(self.requests), before + 1)
        print("HTTPUpgrade scoped Mihomo YAML: complete HTTP body delivered; no DIRECT")
        if failure:
            reasons = {
                "uuid": ("unknown uuid:", WRONG_UUID),
                "ca": ("x509", "unknown authority"),
                "sni": ("x509", WRONG_SNI),
                "host": ("bad host:", "other.example.test"),
                "path": ("bad path:", "/wrong-httpupgrade/"),
                "upgrade": ("real websocket request received",),
            }[failure]
            baseline = len(self.requests)
            with ExitStack() as negative:
                port, client = self.start_client(negative, failure)
                log_path = client.log_path if failure in {"ca", "sni"} else server.log_path
                offset = len(log_path.read_text(errors="replace"))
                self.assert_failure(port, baseline)
                log = require_rejection_evidence(
                    self, log_path, lambda: self.assert_failure(port, baseline), self.requests,
                    reasons, expected_count=baseline, log_offset=offset, label="HTTPUpgrade " + failure,
                )
                line = next(line for line in log.splitlines() if all(reason in line for reason in reasons))
                print(f"HTTPUpgrade {failure}: {line}; application delta=0; no DIRECT")
            time.sleep(.15)
            self.assertEqual(len(self.requests), baseline, "Late rejected application delivery")
        self.assertIsNone(server.process.poll())

    def test_verified_http_forwarding(self):
        self.verify_yaml()

    def test_wrong_uuid_rejected(self):
        self.verify_yaml("uuid")

    def test_wrong_ca_rejected(self):
        self.verify_yaml("ca")

    def test_wrong_sni_rejected(self):
        self.verify_yaml("sni")

    def test_wrong_host_rejected(self):
        self.verify_yaml("host")

    def test_wrong_path_rejected(self):
        self.verify_yaml("path")

    def test_missing_upgrade_flag_rejected_as_websocket(self):
        self.verify_yaml("upgrade")
