import unittest
from copy import deepcopy
from unittest.mock import patch

import yaml

from app.models import database
from app.services.protocol_profiles import compile_profile
from app.services.core_manager import core_manager
import test_subscription_tokens as grants
from test_subscription_tokens import HEADERS
from xhttp_helpers import HOST, PATH, SNI, UUID


class HTTPUpgradeExportTests(unittest.TestCase):
    profile = {"transport": "httpupgrade", "security": "tls", "server_name": SNI,
               "path": PATH, "host": HOST, "certificate_path": "/private/cert.pem",
               "key_path": "/private/key.pem"}
    setUp = grants.SubscriptionTokenTests.setUp
    issue = grants.SubscriptionTokenTests.issue
    public_get = grants.SubscriptionTokenTests.public_get

    def make_httpupgrade(self):
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, 1)
            row.settings, row.stream_settings = compile_profile("sing-box", "vless", self.profile,
                {"users": [{"uuid": UUID}]}, {})
            db.commit()

    def test_yaml_only_download_and_old_format_grants(self):
        old = self.issue(inbound_ids=[1, 2], formats=["mihomo.yaml", "raw", "sing-box.json"])
        self.make_httpupgrade()
        current = self.issue(formats=["mihomo.yaml"])
        response = self.public_get(current["paths"]["mihomo.yaml"])
        self.assertEqual(response.status_code, 200)
        proxy, = yaml.safe_load(response.content)["proxies"]
        self.assertEqual(proxy["ws-opts"], {"path": PATH, "headers": {"Host": HOST}, "v2ray-http-upgrade": True})
        self.assertIs(proxy["udp"], False)
        self.assertNotIn("packet-encoding", proxy)
        self.assertNotIn("/private/", response.text)
        mixed = self.public_get(old["paths"]["mihomo.yaml"])
        self.assertEqual(mixed.status_code, 200)
        self.assertEqual({p["name"] for p in yaml.safe_load(mixed.content)["proxies"]}, {"node-1", "node-2"})
        for fmt in ("raw", "sing-box.json"):
            self.assertEqual(self.public_get(old["paths"][fmt]).status_code, 409)
            self.assertEqual(self.public_get(current["paths"]["mihomo.yaml"].replace("mihomo.yaml", fmt)).status_code, 404)
        for path in ("/api/subscription/link/1", "/api/subscription/raw", "/api/subscription/sing-box.json"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 409)
            self.assertNotIn(UUID, response.text)
        response = self.client.post("/api/subscription/upstream/1", headers=HEADERS, json={
            "server": SNI, "format": "clash-yaml", "acknowledge_tcp_only": True})
        self.assertEqual(response.status_code, 409)

    def test_new_grant_rejects_each_unsupported_format_atomically(self):
        self.make_httpupgrade()
        for formats in (["raw"], ["sing-box.json"], ["mihomo.yaml", "raw"], ["mihomo.yaml", "sing-box.json"]):
            with self.subTest(formats=formats):
                response = self.client.post("/api/subscriptions", headers=HEADERS, json={
                    "label": "mixed selection", "server": SNI, "inbound_ids": [1, 2], "formats": formats})
                self.assertEqual(response.status_code, 409)
                self.assertIn("Mihomo YAML only", response.json()["detail"])
        self.assertEqual(self.client.get("/api/subscriptions").json(), [])

    def test_unrepresentable_saved_fields_survive_rejected_visual_edit(self):
        self.make_httpupgrade()
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, 1)
            original = deepcopy(row.stream_settings)
            original["transport"]["headers"] = {"X-Advanced": "present"}
            row.stream_settings = original
            db.commit()
        with patch.object(core_manager, "validate_inbound_change"), patch("app.api.inbounds.apply_checked"):
            response = self.client.put("/api/inbounds/1", headers=HEADERS, json={
                "profile": self.profile, "stream_settings": {}, "remark": "unrelated edit"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("no fields were dropped", response.json()["detail"])
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, 1)
            self.assertEqual(row.stream_settings, original)
            self.assertEqual(row.remark, "node-1")

    def test_switching_to_httpupgrade_does_not_drop_advanced_tls(self):
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, 1)
            original = deepcopy(row.stream_settings)
            original["tls"]["alpn"] = ["h2"]
            row.stream_settings = original
            db.commit()
        with patch.object(core_manager, "validate_inbound_change"), patch("app.api.inbounds.apply_checked"):
            response = self.client.put("/api/inbounds/1", headers=HEADERS, json={
                "profile": self.profile, "stream_settings": {}})
        self.assertEqual(response.status_code, 422)
        with database.SessionLocal() as db:
            self.assertEqual(db.get(database.Inbound, 1).stream_settings, original)

    def test_legacy_non_string_flow_cannot_become_eligible_by_editing(self):
        self.make_httpupgrade()
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, 1)
            original = deepcopy(row.settings)
            original["users"][0]["flow"] = False
            row.settings = original
            db.commit()
        with patch.object(core_manager, "validate_inbound_change"), patch("app.api.inbounds.apply_checked"):
            response = self.client.put("/api/inbounds/1", headers=HEADERS, json={"profile": self.profile})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("mihomo_yaml_only", self.client.get("/api/inbounds").json()[0])
        with database.SessionLocal() as db:
            self.assertEqual(db.get(database.Inbound, 1).settings, original)
