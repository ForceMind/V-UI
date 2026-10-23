"""Stale new-subscription selections, using real API routes and temporary SQLite.

The shared fixture blocks subprocesses, DNS and outbound connections. Synthetic
admin request state intentionally isolates schema/handler behavior from auth;
this suite starts no production lifespan, HTTP listener, browser or proxy core.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from app.api import inbounds, subscriptions
from app.models import database
from app.services.core_manager import core_manager
from app.services.inbound_service import delete_inbound, routing_dicts, update_inbound
from app.services.subscription_tokens import create_grant
from app.services import subscription_tokens
from app.services.node_selection_revision import node_selection_revision
from app.services.validated_export import ExportError
import test_subscription_identity as identity
import main

HEADER = "X-VUI-Node-Revision"
CHANGED = "Subscription node selection changed; refresh nodes and select them again"


def revision_oracle(pairs):
    normalized = sorted((identifier, instance if type(instance) is str
        and len(instance) == 32 and all(c in "0123456789abcdef" for c in instance)
        else "<invalid>") for identifier, instance in pairs)
    encoded = json.dumps(normalized, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(b"vui:subscription-node-selection:v1\0" + encoded).hexdigest()


class SubscriptionSelectionTests(unittest.TestCase):
    create = identity.SubscriptionIdentityTests.create
    issue = identity.SubscriptionIdentityTests.issue
    fetch = identity.SubscriptionIdentityTests.fetch
    assert_export = identity.SubscriptionIdentityTests.assert_export
    assert_invalid = identity.SubscriptionIdentityTests.assert_invalid
    grant_state = identity.SubscriptionIdentityTests.grant_state
    raw_grant_rows = identity.SubscriptionIdentityTests.raw_grant_rows
    mutate_identity = identity.SubscriptionIdentityTests.mutate_identity

    def setUp(self):
        identity.SubscriptionIdentityTests.setUp(self)
        app = FastAPI()
        app.add_exception_handler(ExportError, main.invalid_export)
        app.add_exception_handler(RequestValidationError, main.validation_error)

        @app.middleware("http")
        async def synthetic_admin(request: Request, call_next):
            request.state.admin = {"id": self.owner_id}
            return await call_next(request)

        app.include_router(inbounds.router, prefix="/api/inbounds")
        app.include_router(subscriptions.router, prefix="/api/subscriptions")
        app.include_router(subscriptions.public_router)
        self.client = self.stack.enter_context(TestClient(app, base_url=identity.ORIGIN))

    def snapshot(self, path="/api/inbounds"):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(type(response.json()), list)
        revision = response.headers.get(HEADER)
        self.assertIs(type(revision), str)
        self.assertRegex(revision, r"\A[0-9a-f]{64}\Z")
        return response, revision

    def post(self, ids, **extra):
        return self.client.post("/api/subscriptions", json={
            "label": "synthetic selection", "server": "vpn.example.test",
            "inbound_ids": ids, "formats": list(identity.FORMATS), **extra})

    def assert_rejected(self, ids, revision, status=409, detail=CHANGED):
        before = self.raw_grant_rows()
        with patch("app.services.subscription_tokens.secrets.token_urlsafe") as generate:
            response = self.post(ids, expected_node_revision=revision)
            self.assertEqual(response.status_code, status, response.text)
            if detail is not None:
                self.assertEqual(response.json()["detail"], detail)
            generate.assert_not_called()
        self.assertEqual(self.raw_grant_rows(), before)
        return response

    def test_array_response_revision_covers_full_inventory_and_never_exposes_identity(self):
        selected, first = self.create()
        other, second = self.create("xray node", 10444, identity.UUIDS[1], "xray")
        response, revision = self.snapshot()
        expected = revision_oracle([(selected, first), (other, second)])
        self.assertEqual(revision, expected)
        self.assertEqual([row["id"] for row in response.json()], [selected, other])
        filtered, filtered_revision = self.snapshot("/api/inbounds?core=sing-box")
        self.assertEqual([row["id"] for row in filtered.json()], [selected])
        self.assertEqual(filtered_revision, expected)
        trailing, trailing_revision = self.snapshot("/api/inbounds/")
        self.assertEqual(trailing.json(), response.json())
        self.assertEqual(trailing_revision, expected)
        with database.SessionLocal() as db:
            self.assertEqual(routing_dicts(db), response.json())
        for secret in (first, second, *identity.UUIDS, "instance_id", "inbound_bindings"):
            self.assertNotIn(secret, response.text)
            self.assertNotIn(secret, json.dumps(dict(response.headers)))

    def test_empty_inventory_has_deterministic_revision(self):
        response, revision = self.snapshot()
        self.assertEqual(response.json(), [])
        self.assertEqual(revision, revision_oracle([]))

    def test_revision_is_sorted_and_invalid_identity_contents_share_a_fixed_marker(self):
        selected, first = self.create()
        other, second = self.create("other", 10444, identity.UUIDS[1])
        with database.SessionLocal() as db:
            rows = db.query(database.Inbound).order_by(database.Inbound.id.desc()).all()
            self.assertEqual(node_selection_revision(rows), revision_oracle([(selected, first), (other, second)]))
            for invalid in (None, "", "private malformed contents", "A" * 32, "f" * 31, "f" * 32 + "\n"):
                rows[0].instance_id = invalid
                self.assertEqual(node_selection_revision(rows), revision_oracle([(selected, first), (other, None)]))

    def test_optional_schema_is_strict_string_and_owner_check_precedes_revision_conflict(self):
        schema = subscriptions.GrantPayload.model_json_schema()
        self.assertNotIn("expected_node_revision", schema["required"])
        self.assertEqual(schema["properties"]["expected_node_revision"]["type"], "string")
        selected, _ = self.create()
        _, revision = self.snapshot()
        with database.SessionLocal() as db:
            db.get(database.User, self.owner_id).is_active = False
            db.commit()
        self.assert_rejected([selected], "0" * 64 if revision != "0" * 64 else "1" * 64,
                             401, "Authentication required")

    def test_stale_selection_rejects_natural_id_reuse_before_token_or_grant_write(self):
        selected, first = self.create()
        old_grant, old_token = self.issue(selected)
        response, revision = self.snapshot()
        with database.SessionLocal() as db:
            delete_inbound(db, selected)
        replacement, second = self.create("replacement", 10444, identity.UUIDS[1])
        self.assertEqual(replacement, selected)
        self.assertNotEqual(first, second)
        self.assert_rejected([response.json()[0]["id"]], revision)
        self.assert_invalid(old_grant, old_token)
        _, refreshed = self.snapshot()
        self.assertNotEqual(refreshed, revision)
        created = self.post([replacement], expected_node_revision=refreshed)
        self.assertEqual(created.status_code, 201, created.text)
        self.assert_export(created.json()["token"], present=(identity.UUIDS[1],), absent=(identity.UUIDS[0],))
        self.assert_invalid(old_grant, old_token)
        for output_format in identity.FORMATS:
            for method in ("GET", "HEAD"):
                denied = self.client.request(method, f"/sub/{old_token}/{output_format}")
                self.assertEqual(denied.status_code, 409, denied.text)
                self.assertNotIn(identity.UUIDS[1], denied.text)
                allowed = self.client.request(method, created.json()["paths"][output_format])
                self.assertEqual(allowed.status_code, 200, allowed.text)
                self.assertIn("no-store", allowed.headers["cache-control"])
                self.assertNotIn("set-cookie", allowed.headers)

    def test_same_instance_edits_traffic_and_runtime_observation_preserve_revision(self):
        selected, instance = self.create()
        _, revision = self.snapshot()
        with database.SessionLocal() as db:
            updated = update_inbound(db, selected, {"remark": "edited", "port": 10445,
                "tag": "edited-tag", "expiry_time": 99,
                "settings": {"users": [{"uuid": identity.UUIDS[2]}]}})
            self.assertEqual(updated.instance_id, instance)
            updated.up = 123
            updated.down = 456
            db.commit()
        with patch.object(core_manager, "inbound_routing_status", return_value={selected: {
                "state": "pending", "desired_effective_mode": "direct", "policy_identity": "a" * 64}}):
            response, current = self.snapshot()
        self.assertEqual(current, revision)
        self.assertEqual(response.json()[0]["remark"], "edited")
        self.assertEqual(response.json()[0]["routing_status"]["state"], "pending")
        # The existing exporter forbids any nonzero expiry; restoring it does
        # not change the instance snapshot either.
        with database.SessionLocal() as db:
            update_inbound(db, selected, {"expiry_time": 0})
        self.assertEqual(self.snapshot()[1], revision)
        created = self.post([selected], expected_node_revision=revision)
        self.assertEqual(created.status_code, 201, created.text)
        self.assert_export(created.json()["token"], present=(identity.UUIDS[2],), absent=identity.UUIDS[:2])

    def test_explicit_null_or_malformed_revision_is_422_without_token_or_write(self):
        selected, _ = self.create()
        for invalid in (None, "", "a" * 63, "a" * 65, "A" * 64, "g" * 64,
                        "a" * 64 + "\n", " " + "a" * 63, 1, 1.0, True, [], {}, ["a" * 64]):
            with self.subTest(revision=invalid):
                self.assert_rejected([selected], invalid, 422, None)

    def test_omission_preserves_admin_api_and_six_argument_service_compatibility(self):
        selected, first = self.create()
        self.snapshot()
        with database.SessionLocal() as db:
            delete_inbound(db, selected)
        replacement, second = self.create("replacement", 10444, identity.UUIDS[1])
        self.assertEqual(replacement, selected)
        self.assertNotEqual(first, second)
        created = self.post([selected])
        self.assertEqual(created.status_code, 201, created.text)
        self.assert_export(created.json()["token"], present=(identity.UUIDS[1],), absent=(identity.UUIDS[0],))
        record, token = create_grant(self.owner_id, "direct caller", "vpn.example.test",
                                    [selected], list(identity.FORMATS), 7)
        self.assertEqual(record["inbound_ids"], [selected])
        self.assert_export(token, present=(identity.UUIDS[1],), absent=(identity.UUIDS[0],))

    def test_unrelated_create_and_delete_conservatively_conflict(self):
        selected, _ = self.create()
        _, before_create = self.snapshot()
        other, _ = self.create("other", 10444, identity.UUIDS[1])
        self.assert_rejected([selected], before_create)
        _, before_delete = self.snapshot()
        with database.SessionLocal() as db:
            delete_inbound(db, other)
        self.assert_rejected([selected], before_delete)
        _, restored = self.snapshot()
        self.assertEqual(restored, before_create)

    def test_unrelated_malformed_or_duplicate_identity_does_not_block_valid_selection(self):
        selected, selected_instance = self.create()
        unrelated, unrelated_instance = self.create("unrelated", 10444, identity.UUIDS[1])
        other, _ = self.create("other", 10445, identity.UUIDS[2])
        for invalid in (None, "invalid unrelated identity", "A" * 32, unrelated_instance):
            with self.subTest(identity=invalid):
                self.mutate_identity(other, invalid)
                _, revision = self.snapshot()
                self.assertEqual(revision, revision_oracle([(selected, selected_instance),
                    (unrelated, unrelated_instance), (other, invalid)]))
                created = self.post([selected], expected_node_revision=revision)
                self.assertEqual(created.status_code, 201, created.text)
                self.assert_export(created.json()["token"], present=(identity.UUIDS[0],), absent=identity.UUIDS[1:])

    def test_current_revision_does_not_bypass_selected_identity_validation(self):
        selected, instance = self.create()
        other, _ = self.create("other", 10444, identity.UUIDS[1])
        for invalid in (None, "invalid selected identity", instance):
            with self.subTest(identity=invalid):
                self.mutate_identity(other, invalid)
                _, revision = self.snapshot()
                response = self.assert_rejected([other], revision, 409, None)
                self.assertIn("Selected node identities are invalid", response.json()["detail"])
                self.assertNotEqual(response.json()["detail"], CHANGED)
                self.assertNotIn(instance, response.text)

    def test_current_revision_keeps_missing_disabled_and_export_validation(self):
        selected, _ = self.create()
        _, revision = self.snapshot()
        with database.SessionLocal() as db:
            update_inbound(db, selected, {"enable": False})
        self.assertEqual(self.snapshot()[1], revision)
        self.assert_rejected([selected], revision, 422, "Selected nodes are missing or disabled")
        self.assert_rejected([selected + 1], revision, 422, "Selected nodes are missing or disabled")
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, selected)
            row.enable = True
            row.settings = {"users": []}
            db.commit()
        self.assertEqual(self.snapshot()[1], revision)
        response = self.assert_rejected([selected], revision, 409,
            "Node configuration is outside the validated export profile")
        self.assertNotEqual(response.json()["detail"], CHANGED)

    def test_rows_and_revision_are_from_one_query_even_if_inventory_changes_during_runtime_read(self):
        selected, instance = self.create()
        selects = []

        def capture(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT") and "FROM inbounds" in statement:
                selects.append(statement)

        def after_snapshot(rows):
            self.assertEqual([row.id for row in rows], [selected])
            with database.SessionLocal() as writer:
                writer.add(database.Inbound(**identity.payload("concurrent other", 10444, identity.UUIDS[1])))
                writer.commit()
            return {}

        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            with patch.object(core_manager, "inbound_routing_status", side_effect=after_snapshot):
                response, revision = self.snapshot()
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)
        self.assertEqual(len(selects), 1, selects)
        self.assertEqual([row["id"] for row in response.json()], [selected])
        self.assertEqual(revision, revision_oracle([(selected, instance)]))
        refreshed, current = self.snapshot()
        self.assertEqual(len(refreshed.json()), 2)
        self.assertNotEqual(current, revision)
        self.assert_rejected([selected], revision)

    def test_revision_check_waits_for_existing_writer_and_observes_committed_replacement(self):
        selected, original = self.create()
        _, revision = self.snapshot()
        started = threading.Event()
        statements = []

        def observe(connection, cursor, statement, parameters, context, executemany):
            if threading.current_thread().name.startswith("selection-request"):
                statements.append(statement)
                if statement == "BEGIN IMMEDIATE":
                    started.set()

        event.listen(self.engine, "before_cursor_execute", observe)
        try:
            with self.engine.connect() as writer:
                writer.execute(text("BEGIN IMMEDIATE"))
                writer.execute(text("DELETE FROM inbounds WHERE id=:id"), {"id": selected})
                # The SQLite allocator, not a forced ID, reuses the highest row.
                with database.SessionLocal(bind=writer) as session:
                    row = database.Inbound(**identity.payload("replacement", 10444, identity.UUIDS[1]))
                    session.add(row)
                    session.flush()
                    self.assertEqual(row.id, selected)
                    self.assertNotEqual(row.instance_id, original)
                with ThreadPoolExecutor(max_workers=1, thread_name_prefix="selection-request") as pool:
                    with patch("app.services.subscription_tokens.secrets.token_urlsafe") as generate:
                        pending = pool.submit(create_grant, self.owner_id, "racing selection", "vpn.example.test",
                            [selected], list(identity.FORMATS), 7, expected_node_revision=revision)
                        try:
                            self.assertTrue(started.wait(5), "grant creation never attempted BEGIN IMMEDIATE")
                            self.assertFalse(pending.done(), "grant creation escaped the writer's lock")
                        finally:
                            writer.commit()
                        with self.assertRaises(HTTPException) as raised:
                            pending.result(timeout=5)
                        self.assertEqual(getattr(raised.exception, "status_code", None), 409)
                        self.assertEqual(getattr(raised.exception, "detail", None), CHANGED)
                        generate.assert_not_called()
            self.assertEqual(self.raw_grant_rows(), [])
            self.assertEqual(statements[0], "BEGIN IMMEDIATE")
        finally:
            event.remove(self.engine, "before_cursor_execute", observe)

    def test_revision_and_grant_binding_remain_in_same_write_transaction(self):
        selected, original = self.create()
        _, revision = self.snapshot()
        checked = threading.Event()
        release = threading.Event()
        real_bindings = subscription_tokens._row_bindings

        def after_revision(db, rows):
            checked.set()
            if not release.wait(5):
                raise AssertionError("test did not release grant binding")
            return real_bindings(db, rows)

        try:
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="selection-request") as requests:
                with patch.object(subscription_tokens, "_row_bindings", side_effect=after_revision):
                    pending = requests.submit(create_grant, self.owner_id, "racing selection", "vpn.example.test",
                        [selected], list(identity.FORMATS), 7, expected_node_revision=revision)
                    try:
                        self.assertTrue(checked.wait(5), "grant did not reach its binding check")
                        # A separate connection must actually fail to acquire a
                        # write lock, rather than merely be unfinished at one
                        # scheduler instant. No timing sleep is the evidence.
                        with self.engine.connect() as writer:
                            writer.execute(text("PRAGMA busy_timeout=0"))
                            with self.assertRaises(OperationalError) as denied:
                                writer.execute(text("BEGIN IMMEDIATE"))
                            self.assertIn("locked", str(denied.exception).lower())
                    finally:
                        release.set()
                    grant, token = pending.result(timeout=5)
            with database.SessionLocal() as db:
                delete_inbound(db, selected)
            reused, current = self.create("replacement", 10444, identity.UUIDS[1])
            self.assertEqual(reused, selected)
            self.assertNotEqual(current, original)
            self.assertEqual(self.grant_state(grant["id"])["inbound_bindings"], {str(selected): original})
            self.assert_invalid(grant, token)
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main()
