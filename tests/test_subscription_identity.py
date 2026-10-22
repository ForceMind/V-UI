"""Node-instance subscription scope with synthetic SQLite and no runtime/network.

These tests deliberately allow SQLite to reuse the deleted highest INTEGER
PRIMARY KEY. They do not replace the real row allocator, CRUD, grant service,
public export handler, or exporters with mocks. Runtime application is outside
this unit contract and is blocked/stubbed explicitly.
"""
import base64
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.api import inbounds as inbound_api, singbox as singbox_api, xray as xray_api
from app.api.subscriptions import read_subscription
from app.models import database
from app.services.core_manager import SingBoxAdapter, XrayAdapter, core_manager
from app.services.inbound_policy import policy_plan
from app.services.inbound_service import (
    create_inbound, delete_inbound, editor_dict, routing_dicts, to_dict, update_inbound,
)
from app.services.routing_validation import normalize_routing
from app.services.subscription_tokens import (
    SubscriptionGrant, change_grant, create_grant, digest, list_grants,
)

ORIGIN = "https://panel.example.test"
FORMATS = ("raw", "mihomo.yaml", "sing-box.json")
NOT_READY = "Subscription configuration is not ready"
UUIDS = (
    "11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
)


def payload(label="synthetic node", port=10443, uuid=UUIDS[0], core="sing-box"):
    return {
        "core": core, "protocol": "vless", "remark": label, "port": port,
        "enable": True,
        "settings": {"users": [{"uuid" if core == "sing-box" else "id": uuid}]},
        "stream_settings": {"tls": {"enabled": True, "server_name": "vpn.example.test"}},
    }


class SubscriptionIdentityTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory(prefix="vui-node-identity-"))
        self.path = Path(self.temp) / "data.db"
        self.engine = create_engine(f"sqlite:///{self.path}", connect_args={"check_same_thread": False})
        self.stack.callback(self.engine.dispose)
        self.stack.enter_context(patch.multiple(database, engine=self.engine,
            SessionLocal=sessionmaker(bind=self.engine, autoflush=False),
            DATA_DIR=Path(self.temp), DB_PATH=self.path))
        self.stack.enter_context(patch.dict(os.environ, {
            "VUI_DATA_DIR": self.temp, "VUI_BIN_DIR": str(Path(self.temp) / "empty-bin"),
            "VUI_PUBLIC_ORIGIN": ORIGIN,
        }))
        self.stack.enter_context(patch("subprocess.Popen", side_effect=AssertionError(
            "Subscription identity tests must not launch any subprocess")))
        self.stack.enter_context(patch("socket.create_connection", side_effect=AssertionError(
            "Subscription identity tests must not connect to a network")))
        self.stack.enter_context(patch("socket.getaddrinfo", side_effect=AssertionError(
            "Subscription identity tests must not resolve network names")))
        # Keep the real prospective policy validation and CRUD transaction.
        # Only runtime objects/paths are isolated; none is started or applied.
        self.stack.enter_context(patch('app.services.core_manager.DATA_DIR', Path(self.temp)))
        self.stack.enter_context(patch('app.services.core_manager.BIN_DIR', Path(self.temp) / 'empty-bin'))
        self.stack.enter_context(patch.object(core_manager, 'adapters', {
            'xray': XrayAdapter(), 'sing-box': SingBoxAdapter()}))
        self.stack.enter_context(patch.object(core_manager, '_proxy_coordinator', None))
        self.stack.enter_context(patch.object(core_manager, "inbound_routing_status", return_value={}))
        self.stack.enter_context(patch("app.api.subscriptions.load_routing", return_value=normalize_routing({})))
        self.stack.enter_context(patch("app.services.subscription_tokens.time.time", return_value=2_000_000_000))
        for module in (inbound_api, singbox_api, xray_api):
            self.stack.enter_context(patch.object(module, "apply_checked", return_value={"applied": False}))
        database.init_db()
        with database.SessionLocal() as db:
            owner = database.User(username="synthetic-owner", password_hash="synthetic-not-a-login-hash", is_active=True)
            db.add(owner)
            db.commit()
            self.owner_id = owner.id
        # No production lifespan, sessions, login, browser, or listening socket.
        # These requests exercise real schema and route handlers only.
        app = FastAPI()
        app.include_router(inbound_api.router, prefix="/api/inbounds")
        app.include_router(singbox_api.router, prefix="/api/singbox")
        app.include_router(xray_api.router, prefix="/api/xray")
        self.client = self.stack.enter_context(TestClient(app, base_url=ORIGIN))

    def create(self, label="original node", port=10443, uuid=UUIDS[0], core="sing-box"):
        with database.SessionLocal() as db:
            row = create_inbound(db, payload(label, port, uuid, core))
            return row.id, row.instance_id

    def issue(self, *ids):
        return create_grant(self.owner_id, "synthetic grant", "vpn.example.test", list(ids), list(FORMATS), 90)

    def fetch(self, token, output_format="raw", method="GET"):
        request = Request({
            "type": "http", "scheme": "https", "method": method,
            "path": f"/sub/{token}/{output_format}", "query_string": b"",
            "headers": [(b"host", b"panel.example.test")],
            "server": ("panel.example.test", 443), "client": ("127.0.0.1", 55001),
        })
        try:
            response = read_subscription(token, output_format, request)
            return response.status_code, response.body.decode(), response.headers
        except HTTPException as exc:
            return exc.status_code, exc.detail, {}

    def assert_export(self, token, *, present=(), absent=()):
        for output_format in FORMATS:
            with self.subTest(output_format=output_format):
                status, body, headers = self.fetch(token, output_format)
                self.assertEqual(status, 200, body)
                if output_format == "raw":
                    body = base64.b64decode(body, validate=True).decode()
                for value in present:
                    self.assertIn(str(value), body)
                for value in absent:
                    self.assertNotIn(str(value), body)
                self.assertIn("no-store", headers["cache-control"])
                self.assertNotIn("set-cookie", headers)

    def assert_invalid(self, grant, token, reason="node_identity_changed"):
        for output_format in FORMATS:
            for method in ("GET", "HEAD"):
                with self.subTest(output_format=output_format, method=method):
                    status, detail, _ = self.fetch(token, output_format, method)
                    self.assertEqual(status, 409)
                    self.assertEqual(detail, NOT_READY)
        record = next(item for item in list_grants(self.owner_id) if item["id"] == grant["id"])
        self.assertIs(record["invalidated"], True)
        self.assertEqual(record["invalidation_reason"], reason)
        self.assertNotIn("inbound_bindings", record)
        self.assertNotIn("instance_id", json.dumps(record))

    def grant_state(self, grant_id):
        with database.SessionLocal() as db:
            row = db.get(SubscriptionGrant, grant_id)
            return {column.name: deepcopy(getattr(row, column.name)) for column in SubscriptionGrant.__table__.columns}

    def mutate_grant(self, grant_id, **values):
        with database.SessionLocal() as db:
            row = db.get(SubscriptionGrant, grant_id)
            for key, value in values.items():
                setattr(row, key, value)
            db.commit()

    def mutate_identity(self, identifier, value):
        # SQL avoids Python-side defaults concealing a corrupted persisted value.
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE inbounds SET instance_id=:value WHERE id=:id"),
                               {"value": value, "id": identifier})

    def raw_grant_rows(self):
        with self.engine.connect() as connection:
            return [tuple(row) for row in connection.execute(
                text("SELECT * FROM subscription_grants ORDER BY id"))]

    def mutate_raw_grant_json(self, grant_id, column, serialized):
        self.assertIn(column, ("inbound_bindings", "inbound_ids", "formats"))
        with self.engine.begin() as connection:
            connection.execute(text(f"UPDATE subscription_grants SET {column}=:value WHERE id=:id"),
                               {"value": serialized, "id": grant_id})

    def assert_create_rejected_unchanged(self, ids, status):
        before = self.raw_grant_rows()
        with patch("app.services.subscription_tokens.secrets.token_urlsafe") as generate:
            with self.assertRaises(HTTPException) as raised:
                create_grant(self.owner_id, "rejected grant", "vpn.example.test", ids, list(FORMATS), 7)
            self.assertEqual(raised.exception.status_code, status)
            generate.assert_not_called()
        self.assertEqual(self.raw_grant_rows(), before)
        return raised.exception.detail

    def assert_rotation_rejected_unchanged(self, grant_id, guidance="new subscription"):
        before = self.grant_state(grant_id)
        raw_before = self.raw_grant_rows()
        with patch("app.services.subscription_tokens.secrets.token_urlsafe") as generate:
            with self.assertRaises(HTTPException) as raised:
                change_grant(self.owner_id, grant_id, rotate_days=7)
            self.assertEqual(raised.exception.status_code, 409)
            self.assertIs(type(raised.exception.detail), str)
            self.assertIn(guidance, raised.exception.detail)
            for value in (before["token_hash"], before["credential_stamp"]):
                self.assertNotIn(value, raised.exception.detail)
            generate.assert_not_called()
        self.assertEqual(self.grant_state(grant_id), before)
        self.assertEqual(self.raw_grant_rows(), raw_before)

    def test_new_rows_receive_distinct_server_generated_instance_ids(self):
        first, first_instance = self.create()
        second, second_instance = self.create("second", 10444, UUIDS[1])
        self.assertNotEqual(first, second)
        for value in (first_instance, second_instance):
            self.assertIs(type(value), str)
            self.assertRegex(value, r"\A[0-9a-f]{32}\Z")
        self.assertNotEqual(first_instance, second_instance)
        with database.SessionLocal() as db:
            imported = database.Inbound(**payload("direct model insert", 10445, UUIDS[2]))
            db.add(imported)
            db.commit()
            self.assertRegex(imported.instance_id, r"\A[0-9a-f]{32}\Z")
            self.assertNotIn(imported.instance_id, (first_instance, second_instance))

    def test_grant_persists_exact_selected_instance_mapping_without_disclosure(self):
        first, first_instance = self.create()
        second, second_instance = self.create("second", 10444, UUIDS[1])
        unselected, unselected_instance = self.create("unselected", 10445, UUIDS[2])
        grant, token = self.issue(second, first)
        stored = self.grant_state(grant["id"])
        self.assertEqual(stored["inbound_ids"], sorted([first, second]))
        self.assertEqual(stored["inbound_bindings"], {str(first): first_instance, str(second): second_instance})
        self.assertNotIn(str(unselected), stored["inbound_bindings"])
        self.assertNotIn(token, json.dumps(stored))
        self.assertEqual(stored["token_hash"], digest(token))
        for result in (grant, list_grants(self.owner_id)):
            serialized = json.dumps(result)
            for private in ("inbound_bindings", "instance_id", first_instance, second_instance, unselected_instance):
                self.assertNotIn(private, serialized)
        listed = list_grants(self.owner_id)[0]
        self.assertIs(listed["invalidated"], False)
        self.assert_export(token, present=UUIDS[:2], absent=(UUIDS[2], first_instance, second_instance))

    def test_deleted_highest_id_reuse_cannot_retarget_any_public_format(self):
        sibling, _ = self.create("unchanged sibling", 10443, UUIDS[0])
        original, original_instance = self.create("authorized original", 10444, UUIDS[1])
        grant, token = self.issue(original)
        sibling_grant, sibling_token = self.issue(sibling)
        self.assert_export(token, present=(UUIDS[1], 10444), absent=(UUIDS[0],))
        with database.SessionLocal() as db:
            delete_inbound(db, original)
        self.assert_invalid(grant, token)
        replacement, replacement_instance = self.create("never authorized replacement", 10445, UUIDS[2])
        self.assertEqual(replacement, original, "Regression fixture must naturally reuse SQLite's numeric ID")
        self.assertNotEqual(replacement_instance, original_instance)
        self.assert_invalid(grant, token)
        self.assertFalse(self.grant_state(grant["id"])["revoked"])
        self.assert_export(sibling_token, present=(UUIDS[0],), absent=(UUIDS[1], UUIDS[2]))
        self.assertFalse(next(g for g in list_grants(self.owner_id) if g["id"] == sibling_grant["id"])["invalidated"])
        new_grant, new_token = self.issue(replacement)
        self.assertNotEqual(new_grant["id"], grant["id"])
        self.assert_export(new_token, present=(UUIDS[2], 10445), absent=(UUIDS[1],))
        self.assert_rotation_rejected_unchanged(grant["id"])

    def test_credential_port_and_label_edit_keeps_the_same_instance_authorized(self):
        identifier, instance = self.create()
        grant, token = self.issue(identifier)
        before = self.grant_state(grant["id"])
        with database.SessionLocal() as db:
            updated = update_inbound(db, identifier, {
                "remark": "edited but same node", "port": 11443,
                "settings": {"users": [{"uuid": UUIDS[1]}]},
            })
            self.assertEqual(updated.instance_id, instance)
        self.assertEqual(self.grant_state(grant["id"]), before)
        self.assert_export(token, present=(UUIDS[1], 11443), absent=(UUIDS[0], instance))
        self.assertFalse(list_grants(self.owner_id)[0]["invalidated"])
        rotated, new_token = change_grant(self.owner_id, grant["id"], rotate_days=10)
        self.assertNotEqual(new_token, token)
        self.assertEqual(self.grant_state(grant["id"])["inbound_bindings"], before["inbound_bindings"])
        self.assertEqual(rotated["id"], grant["id"])
        for output_format in FORMATS:
            self.assertEqual(self.fetch(token, output_format)[0], 404)
        self.assert_export(new_token, present=(UUIDS[1], 11443), absent=(UUIDS[0],))

    def test_multi_node_grant_fails_as_a_whole_when_one_instance_is_replaced(self):
        first, _ = self.create()
        second, _ = self.create("second", 10444, UUIDS[1])
        grant, token = self.issue(first, second)
        self.assert_export(token, present=UUIDS[:2])
        with database.SessionLocal() as db:
            delete_inbound(db, second)
        replacement, _ = self.create("replacement", 10445, UUIDS[2])
        self.assertEqual(replacement, second)
        self.assert_invalid(grant, token)
        self.assert_rotation_rejected_unchanged(grant["id"])

    def test_bindings_must_match_the_full_selection_exactly(self):
        first, first_instance = self.create()
        second, second_instance = self.create("second", 10444, UUIDS[1])
        outsider, outsider_instance = self.create("not selected", 10445, UUIDS[2])
        grant, token = self.issue(first, second)
        cases = {
            "empty": {},
            "missing selected node": {str(first): first_instance},
            "extra node": {str(first): first_instance, str(second): second_instance, str(outsider): outsider_instance},
            "swapped identities": {str(first): second_instance, str(second): first_instance},
            "duplicated identity": {str(first): first_instance, str(second): first_instance},
            "different identity": {str(first): "f" * 32, str(second): second_instance},
        }
        for label, bindings in cases.items():
            with self.subTest(case=label):
                self.mutate_grant(grant["id"], inbound_bindings=bindings)
                self.assert_invalid(grant, token)
                self.assert_rotation_rejected_unchanged(grant["id"])

    def test_legacy_null_bindings_never_rebind_on_read_list_or_rotation(self):
        identifier, instance = self.create()
        grant, token = self.issue(identifier)
        self.mutate_grant(grant["id"], inbound_bindings=None)
        before = self.grant_state(grant["id"])
        self.assert_invalid(grant, token, "node_identity_missing")
        database.init_db()
        self.assert_invalid(grant, token, "node_identity_missing")
        self.assert_rotation_rejected_unchanged(grant["id"])
        self.assertEqual(self.grant_state(grant["id"]), before)
        with database.SessionLocal() as db:
            self.assertEqual(db.get(database.Inbound, identifier).instance_id, instance)

    def test_malformed_binding_types_and_keys_fail_closed_without_private_details(self):
        identifier, instance = self.create()
        grant, token = self.issue(identifier)
        malformed = [
            [], [instance], [[str(identifier), instance]], "private-malformed-binding", 1, True,
            {str(identifier): None}, {str(identifier): True}, {str(identifier): 1},
            {str(identifier): []}, {str(identifier): {}}, {str(identifier): ""},
            {str(identifier): "A" * 32}, {str(identifier): instance + "0"},
            {str(identifier): instance[:-1]}, {str(identifier): "g" * 32},
            {str(identifier): instance + "\n"}, {"0" + str(identifier): instance},
            {"+" + str(identifier): instance}, {"true": instance}, {"": instance},
        ]
        for bindings in malformed:
            with self.subTest(bindings=bindings):
                self.mutate_grant(grant["id"], inbound_bindings=bindings)
                self.assert_invalid(grant, token)
                self.assert_rotation_rejected_unchanged(grant["id"])

    def test_corrupted_selected_id_types_and_duplicates_fail_closed(self):
        identifier, _ = self.create()
        grant, token = self.issue(identifier)
        for ids in ([identifier, identifier], [True], [str(identifier)], [float(identifier)],
                    [identifier, False], [0], [-1], [9223372036854775808], [], None, {}, "private-invalid-selection"):
            with self.subTest(ids=ids):
                self.mutate_raw_grant_json(grant["id"], "inbound_ids", json.dumps(ids))
                self.assert_invalid(grant, token)
                self.assert_rotation_rejected_unchanged(grant["id"])

    def test_exact_but_out_of_range_selected_id_is_rejected_without_sql_overflow(self):
        identifier, instance = self.create()
        grant, token = self.issue(identifier)
        self.mutate_raw_grant_json(grant['id'], 'inbound_ids', '[9223372036854775808]')
        self.mutate_raw_grant_json(grant['id'], 'inbound_bindings', json.dumps({'9223372036854775808':instance}))
        self.assert_invalid(grant, token)
        self.assert_rotation_rejected_unchanged(grant['id'])

    def test_live_missing_identity_columns_return_only_generic_public_errors(self):
        identifier, instance = self.create()
        _, token = self.issue(identifier)
        with self.engine.begin() as conn:
            conn.execute(text('ALTER TABLE inbounds DROP COLUMN instance_id'))
        for output_format in FORMATS:
            for method in ('GET', 'HEAD'):
                status, body, _ = self.fetch(token, output_format, method)
                self.assertEqual((status, body), (409, NOT_READY))
                for secret in (token, instance, *UUIDS): self.assertNotIn(secret, body)

    def test_browser_fixture_crud_reports_real_saved_not_applied_without_a_core(self):
        from app.api.cores import apply_checked
        self.create('unchanged sibling', 10443, UUIDS[0])
        expected = {'saved':True, 'applied':False,
                    'message':'Pinned core binary is missing or not executable'}
        try:
            with patch.object(inbound_api, 'apply_checked', apply_checked), patch(
                    'app.services.core_manager.BIN_DIR', Path(self.temp) / 'empty-bin'):
                response = self.client.post('/api/inbounds', json=payload('browser original', 10444, UUIDS[1]))
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json(), {'detail':expected})
                with database.SessionLocal() as db:
                    original = db.get(database.Inbound, 2)
                    self.assertIsNotNone(original); original_identity = original.instance_id
                response = self.client.delete('/api/inbounds/2')
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json(), {'detail':expected})
                response = self.client.post('/api/inbounds', json=payload('browser replacement', 10445, UUIDS[2]))
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json(), {'detail':expected})
                with database.SessionLocal() as db:
                    replacement = db.get(database.Inbound, 2)
                    self.assertIsNotNone(replacement)
                    self.assertNotEqual(replacement.instance_id, original_identity)
                self.assertIsNone(core_manager.get('sing-box').runtime.process)
        finally:
            for adapter in core_manager.adapters.values():
                if adapter._runtime is not None: adapter._runtime.close()

    def test_public_export_uses_one_sqlite_snapshot_across_concurrent_replacement(self):
        identifier, _ = self.create()
        grant, token = self.issue(identifier)
        with self.engine.connect() as conn: conn.execute(text('PRAGMA journal_mode=WAL'))
        replaced = []
        def replace():
            with database.SessionLocal() as db: delete_inbound(db, identifier)
            replacement, _ = self.create('concurrent replacement', 10445, UUIDS[2])
            self.assertEqual(replacement, identifier)
            replaced.append(replacement)
        with ThreadPoolExecutor(max_workers=1) as executor:
            def after_read(conn, cursor, statement, parameters, context, executemany):
                if 'FROM subscription_grants' in statement and not replaced:
                    executor.submit(replace).result(timeout=5)
            event.listen(self.engine, 'after_cursor_execute', after_read)
            try:
                status, body, _ = self.fetch(token)
            finally:
                event.remove(self.engine, 'after_cursor_execute', after_read)
        self.assertEqual(status, 200)
        content = base64.b64decode(body).decode()
        self.assertIn(UUIDS[0], content)
        self.assertNotIn(UUIDS[2], content)
        self.assert_invalid(grant, token)

    def test_invalid_serialized_bindings_and_duplicate_json_keys_never_become_authority(self):
        identifier, instance = self.create()
        grant, token = self.issue(identifier)
        key = json.dumps(str(identifier))
        valid = json.dumps(instance)
        alternate = json.dumps("f" * 32)
        malformed = (
            "not-json", "{", '["unterminated"', "null",
            "{" + key + ":" + valid + "," + key + ":" + valid + "}",
            "{" + key + ":" + alternate + "," + key + ":" + valid + "}",
        )
        for serialized in malformed:
            with self.subTest(serialized=serialized):
                self.mutate_raw_grant_json(grant["id"], "inbound_bindings", serialized)
                before = self.raw_grant_rows()
                self.assert_invalid(grant, token, "node_identity_missing")
                self.assert_rotation_rejected_unchanged(grant["id"])
                self.assertEqual(self.raw_grant_rows(), before)

    def test_invalid_serialized_selection_or_formats_fail_closed_without_rewriting_storage(self):
        identifier, _ = self.create()
        grant, token = self.issue(identifier)
        for column in ("inbound_ids", "formats"):
            for serialized in ("not-json", "[", "null", '{"duplicate":1,"duplicate":2}'):
                with self.subTest(column=column, serialized=serialized):
                    # Restore the other field so this tests each independently.
                    self.mutate_grant(grant["id"], inbound_ids=[identifier], formats=list(FORMATS))
                    self.mutate_raw_grant_json(grant["id"], column, serialized)
                    before = self.raw_grant_rows()
                    if column == "inbound_ids":
                        self.assert_invalid(grant, token)
                    else:
                        for output_format in FORMATS:
                            status, detail, _ = self.fetch(token, output_format)
                            self.assertEqual(status, 404)
                            self.assertEqual(detail, "Subscription not found")
                        record = list_grants(self.owner_id)[0]
                        self.assertEqual(record["formats"], [])
                        self.assertTrue(record["invalidated"])
                        self.assertEqual(record["invalidation_reason"], "invalid_configuration")
                    self.assert_rotation_rejected_unchanged(grant["id"])
                    self.assertEqual(self.raw_grant_rows(), before)

    def test_disabled_original_refuses_rotation_until_same_instance_is_enabled(self):
        identifier, instance = self.create()
        grant, token = self.issue(identifier)
        with database.SessionLocal() as db:
            updated = update_inbound(db, identifier, {"enable": False})
            self.assertEqual(updated.instance_id, instance)
        self.assert_invalid(grant, token, "nodes_unavailable")
        self.assert_rotation_rejected_unchanged(grant["id"], "enable the original nodes")
        self.assert_create_rejected_unchanged([identifier], 422)
        with database.SessionLocal() as db:
            updated = update_inbound(db, identifier, {"enable": True})
            self.assertEqual(updated.instance_id, instance)
        self.assert_export(token, present=(UUIDS[0],))
        rotated, new_token = change_grant(self.owner_id, grant["id"], rotate_days=7)
        self.assertEqual(rotated["id"], grant["id"])
        self.assert_export(new_token, present=(UUIDS[0],))
        self.assertEqual(self.fetch(token)[0], 404)

    def test_create_rejects_boolean_duplicate_and_malformed_ids_before_token_generation(self):
        identifier, _ = self.create()
        self.issue(identifier)
        for ids in ([True], [identifier, identifier], [str(identifier)], [float(identifier)],
                    [identifier, False], [0], [-1], [9223372036854775808], [], None, {}, "private-invalid-selection", [999]):
            with self.subTest(ids=ids):
                self.assert_create_rejected_unchanged(ids, 422)

    def test_missing_and_malformed_persisted_node_identity_cannot_authorize_or_rotate(self):
        identifier, instance = self.create()
        grant, token = self.issue(identifier)
        for invalid in (None, "", "not-an-instance-id", "A" * 32, "f" * 31, "f" * 33, instance + "\n"):
            with self.subTest(identity=invalid):
                self.mutate_identity(identifier, invalid)
                self.assert_invalid(grant, token)
                self.assert_rotation_rejected_unchanged(grant["id"])
                detail = self.assert_create_rejected_unchanged([identifier], 409)
                if invalid:
                    self.assertNotIn(str(invalid), str(detail))
                self.mutate_identity(identifier, instance)

    def test_duplicate_persisted_identity_is_ambiguous_even_outside_grant_selection(self):
        selected, instance = self.create()
        unselected, _ = self.create("other", 10444, UUIDS[1])
        grant, token = self.issue(selected)
        self.mutate_identity(unselected, instance)
        self.assert_invalid(grant, token)
        self.assert_rotation_rejected_unchanged(grant["id"])
        for ids in ([selected], [selected, unselected]):
            with self.subTest(ids=ids):
                self.assert_create_rejected_unchanged(ids, 409)

    def test_unrelated_malformed_or_duplicate_instances_do_not_expand_or_block_selection(self):
        selected, selected_instance = self.create()
        unrelated, unrelated_instance = self.create("unrelated", 10444, UUIDS[1])
        other, _ = self.create("other unrelated", 10445, UUIDS[2])
        grant, token = self.issue(selected)
        for invalid in (None, "invalid unrelated identity", unrelated_instance):
            with self.subTest(unrelated_identity=invalid):
                self.mutate_identity(other, invalid)
                self.assert_export(token, present=(UUIDS[0],), absent=UUIDS[1:])
                self.assertFalse(list_grants(self.owner_id)[0]["invalidated"])
                another, another_token = self.issue(selected)
                self.assert_export(another_token, present=(UUIDS[0],), absent=UUIDS[1:])
                self.assertEqual(self.grant_state(another["id"])["inbound_bindings"],
                                 {str(selected): selected_instance})
        self.assertEqual(self.grant_state(grant["id"])["inbound_bindings"],
                         {str(selected): selected_instance})

    def test_revoking_invalid_grant_remains_possible_without_rebinding(self):
        identifier, _ = self.create()
        grant, token = self.issue(identifier)
        self.mutate_grant(grant["id"], inbound_bindings=None)
        record, no_token = change_grant(self.owner_id, grant["id"])
        self.assertTrue(record["revoked"])
        self.assertIsNone(no_token)
        self.assertIsNone(self.grant_state(grant["id"])["inbound_bindings"])
        self.assert_rotation_rejected_unchanged(grant["id"])
        for output_format in FORMATS:
            self.assertEqual(self.fetch(token, output_format)[0], 404)

    def test_rotating_revoked_invalid_grant_does_not_restore_authority(self):
        identifier, _ = self.create()
        grant, token = self.issue(identifier)
        record, nothing = change_grant(self.owner_id, grant["id"])
        self.assertTrue(record["revoked"])
        self.assertIsNone(nothing)
        with database.SessionLocal() as db:
            delete_inbound(db, identifier)
        replacement, _ = self.create("replacement", 10444, UUIDS[1])
        self.assertEqual(replacement, identifier)
        self.assert_rotation_rejected_unchanged(grant["id"])
        self.assertTrue(self.grant_state(grant["id"])["revoked"])
        for output_format in FORMATS:
            self.assertEqual(self.fetch(token, output_format)[0], 404)
        record = list_grants(self.owner_id)[0]
        self.assertTrue(record["invalidated"])
        self.assertEqual(record["invalidation_reason"], "node_identity_changed")

    def test_rotation_can_reactivate_revoked_grant_only_for_unchanged_instances(self):
        identifier, _ = self.create()
        grant, old_token = self.issue(identifier)
        bindings = self.grant_state(grant["id"])["inbound_bindings"]
        change_grant(self.owner_id, grant["id"])
        rotated, token = change_grant(self.owner_id, grant["id"], rotate_days=7)
        self.assertFalse(rotated["revoked"])
        self.assertEqual(self.grant_state(grant["id"])["inbound_bindings"], bindings)
        self.assertEqual(self.fetch(old_token)[0], 404)
        self.assert_export(token, present=(UUIDS[0],))

    def test_service_rejects_supplied_identity_on_create_and_update_without_mutation(self):
        identifier, instance = self.create()
        for forged in (instance, "f" * 32, "", None, True, {}):
            with self.subTest(forged=forged):
                with database.SessionLocal() as db:
                    count = db.query(database.Inbound).count()
                    with self.assertRaises(HTTPException) as raised:
                        create_inbound(db, {**payload("forged", 10444), "instance_id": forged})
                    self.assertEqual(raised.exception.status_code, 422)
                    self.assertEqual(db.query(database.Inbound).count(), count)
                    with self.assertRaises(HTTPException) as raised:
                        update_inbound(db, identifier, {"remark": "must not persist", "instance_id": forged})
                    self.assertEqual(raised.exception.status_code, 422)
                    row = db.get(database.Inbound, identifier)
                    self.assertEqual(row.instance_id, instance)
                    self.assertEqual(row.remark, "original node")

    def test_modern_and_legacy_apis_reject_forged_identity_before_creation_or_update(self):
        for core, path in (("sing-box", "/api/inbounds"), ("sing-box", "/api/singbox/inbounds"),
                           ("xray", "/api/xray/inbounds")):
            with self.subTest(path=path):
                with database.SessionLocal() as db:
                    before = db.query(database.Inbound).count()
                candidate = payload("forged api", 12000 + before, core=core)
                if path != "/api/inbounds":
                    candidate.pop("core")
                response = self.client.post(path, json={**candidate, "instance_id": "f" * 32})
                self.assertEqual(response.status_code, 422, response.text)
                with database.SessionLocal() as db:
                    self.assertEqual(db.query(database.Inbound).count(), before)
                created = self.client.post(path, json=candidate)
                self.assertEqual(created.status_code, 200, created.text)
                identifier = created.json()["inbound"]["id"]
                with database.SessionLocal() as db:
                    instance = db.get(database.Inbound, identifier).instance_id
                update = {"remark": "must not persist", "instance_id": instance}
                if path != "/api/inbounds":
                    update = {**candidate, **update}
                rejected = self.client.put(f"{path}/{identifier}", json=update)
                self.assertEqual(rejected.status_code, 422, rejected.text)
                with database.SessionLocal() as db:
                    row = db.get(database.Inbound, identifier)
                    self.assertEqual(row.instance_id, instance)
                    self.assertEqual(row.remark, "forged api")

    def test_raw_configuration_copy_allocates_a_fresh_server_identity(self):
        for offset, core in enumerate(("sing-box", "xray")):
            with self.subTest(core=core):
                identifier, instance = self.create("source", 13000 + offset * 2, core=core)
                with database.SessionLocal() as db:
                    source = db.get(database.Inbound, identifier)
                    imported_payload = {key: deepcopy(getattr(source, key)) for key in (
                        "core", "protocol", "settings", "stream_settings", "remark", "enable", "tag")}
                    imported_payload["port"] = source.port + 1
                    imported = create_inbound(db, imported_payload)
                    self.assertNotEqual(imported.instance_id, instance)
                    self.assertRegex(imported.instance_id, r"\A[0-9a-f]{32}\Z")
                    self.assertEqual(imported.settings, source.settings)
                    self.assertEqual(imported.stream_settings, source.stream_settings)

    def test_identity_does_not_leak_into_summaries_editor_runtime_or_source_fingerprint(self):
        for offset, core in enumerate(("sing-box", "xray")):
            with self.subTest(core=core):
                identifier, instance = self.create("private identity", 14000 + offset, core=core)
                with database.SessionLocal() as db:
                    row = db.get(database.Inbound, identifier)
                    adapter = SingBoxAdapter() if core == "sing-box" else XrayAdapter()
                    runtime_before = adapter.build_config([row])
                    flags = {"local_proxy_enabled": False, "relay_enabled": False}
                    plan_before = policy_plan([row], flags)
                    outputs = (to_dict(row), editor_dict(row), routing_dicts(db), runtime_before, plan_before.manifest)
                    for output in outputs:
                        rendered = json.dumps(output)
                        self.assertNotIn("instance_id", rendered)
                        self.assertNotIn(instance, rendered)
                    row.instance_id = "e" * 32
                    self.assertEqual(adapter.build_config([row]), runtime_before)
                    self.assertEqual(policy_plan([row], flags).source_fingerprint, plan_before.source_fingerprint)
                    self.assertEqual(policy_plan([row], flags).manifest, plan_before.manifest)


if __name__ == "__main__":
    unittest.main()
