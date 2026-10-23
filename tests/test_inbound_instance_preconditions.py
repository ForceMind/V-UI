"""Guarded node intent through real route functions and temporary disk SQLite.

The adapter invokes actual Pydantic models and API functions directly. It is
not HTTP/auth/browser evidence. CRUD, allocation, policy validation, database
transactions and certificate storage are real; core apply is a named test
seam. Product subprocesses, sockets and DNS are blocked throughout each test.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from typing import get_type_hints
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.api import inbounds
from app.certificates import manager as certificates
from app.certificates.models import CertificateBinding
from app.models import database
from app.services import inbound_service
from app.services.core_manager import CoreError, SingBoxAdapter, XrayAdapter, core_manager

CHANGED = "Inbound instance changed; refresh nodes and reopen or confirm again"
UUIDS = ("11111111-1111-4111-8111-111111111111",
         "22222222-2222-4222-8222-222222222222")


def revision_oracle(identifier, instance):
    value = json.dumps([identifier, instance], separators=(",", ":")).encode("ascii")
    return hashlib.sha256(b"vui:inbound-instance:v1\0" + value).hexdigest()


def payload(label="original", port=10443, uuid=UUIDS[0]):
    return {"core": "sing-box", "protocol": "vless", "remark": label,
            "port": port, "enable": True,
            "settings": {"users": [{"uuid": uuid}]},
            "profile": {"security": "none", "transport": "direct"}}


class InboundInstancePreconditionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="vui-inbound-intent-")))
        self.path = self.temp / "data.db"
        self.engine = create_engine(f"sqlite:///{self.path}", connect_args={"check_same_thread": False})
        self.stack.callback(self.engine.dispose)
        self.stack.enter_context(patch.multiple(database, engine=self.engine,
            SessionLocal=sessionmaker(bind=self.engine, autoflush=False),
            DATA_DIR=self.temp, DB_PATH=self.path))
        self.stack.enter_context(patch.dict(os.environ, {
            "VUI_DATA_DIR": str(self.temp), "VUI_BIN_DIR": str(self.temp / "empty-bin")}))
        for target in ("subprocess.Popen", "socket.socket", "socket.create_connection", "socket.getaddrinfo"):
            self.stack.enter_context(patch(target, side_effect=AssertionError(
                "Inbound instance unit tests must not launch cores or use sockets/network")))
        self.stack.enter_context(patch("app.services.core_manager.DATA_DIR", self.temp))
        self.stack.enter_context(patch("app.services.core_manager.BIN_DIR", self.temp / "empty-bin"))
        self.stack.enter_context(patch.object(core_manager, "adapters", {
            "sing-box": SingBoxAdapter(), "xray": XrayAdapter()}))
        self.stack.enter_context(patch.object(core_manager, "_proxy_coordinator", None))
        self.stack.enter_context(patch.object(core_manager, "inbound_routing_status", return_value={}))
        self.apply = self.stack.enter_context(patch.object(inbounds, "apply_checked",
            return_value={"applied": False, "test_runtime_blocked": True}))
        self.manager = certificates.CertificateManager(self.temp / "certificates")
        self.stack.enter_context(patch.object(certificates, "_instance", self.manager))
        database.init_db()

    def request(self, method, identifier=None, body=None):
        """Real API/model adapter with no ASGI server, listener or session mock."""
        try:
            with database.SessionLocal() as db:
                if method == "PUT":
                    value = inbounds.edit_inbound(identifier,
                        inbounds.InboundUpdatePayload.model_validate(body), db=db)
                elif method == "GUARDED_DELETE":
                    route = next((route for route in inbounds.router.routes
                        if route.path == "/{inbound_id}/delete" and "POST" in route.methods), None)
                    if route is None:
                        raise HTTPException(404, "Not Found")
                    model = get_type_hints(route.endpoint)["payload"]
                    value = route.endpoint(identifier, model.model_validate(body), db=db)
                elif method == "DELETE":
                    value = inbounds.remove_inbound(identifier, db=db)
                elif method == "POST":
                    value = inbounds.add_inbound(inbounds.InboundPayload.model_validate(body), db=db)
                else:
                    raise AssertionError(method)
                return 200, value
        except HTTPException as exc:
            return exc.status_code, {"detail": exc.detail}
        except ValidationError:
            return 422, {"detail": "Request schema rejected"}

    def create(self, label="original", port=10443, uuid=UUIDS[0], **overrides):
        status, value = self.request("POST", body={**payload(label, port, uuid), **overrides})
        self.assertEqual(status, 200, value)
        identifier = value["inbound"]["id"]
        return identifier, self.row(identifier)["instance_id"]

    def row(self, identifier):
        with database.SessionLocal() as db:
            row = db.get(database.Inbound, identifier)
            return None if row is None else {column.name: deepcopy(getattr(row, column.name))
                for column in database.Inbound.__table__.columns}

    def revision(self, identifier):
        return revision_oracle(identifier, self.row(identifier)["instance_id"])

    def editor(self, identifier):
        with database.SessionLocal() as db:
            return inbounds.get_inbound_editor(identifier, db=db)

    def list_rows(self):
        with database.SessionLocal() as db:
            return inbounds.get_all_inbounds(Response(), db=db)

    def replace(self, identifier):
        self.assertEqual(self.request("DELETE", identifier)[0], 200)
        replacement, instance = self.create("replacement", 11443, UUIDS[1])
        self.assertEqual(replacement, identifier, "must exercise SQLite's natural allocator")
        return instance

    def assert_conflict_without_effects(self, method, identifier, revision, **extra):
        before = self.row(identifier)
        self.apply.reset_mock()
        with patch.object(certificates, "get_manager") as manager, \
             patch.object(inbound_service, "_commit_inbound_change") as commit, \
             patch.object(core_manager, "validate_inbound_change") as validate:
            status, value = self.request(method, identifier,
                {"expected_instance_revision": revision, **extra})
        self.assertEqual((status, value), (409, {"detail": CHANGED}))
        manager.assert_not_called()
        commit.assert_not_called()
        validate.assert_not_called()
        self.apply.assert_not_called()
        self.assertEqual(self.row(identifier), before)

    def test_list_editor_and_mutation_response_share_opaque_instance_revision(self):
        identifier, instance = self.create()
        expected = revision_oracle(identifier, instance)
        values = [self.list_rows()[0], self.editor(identifier)]
        status, saved = self.request("PUT", identifier,
            {"remark": "same instance", "expected_instance_revision": expected})
        self.assertEqual(status, 200, saved)
        values.append(saved["inbound"])
        for value in values:
            self.assertEqual(value.get("mutation_revision"), expected)
            rendered = json.dumps(value)
            for secret in (instance, UUIDS[0], "instance_id", "inbound_bindings"):
                self.assertNotIn(secret, rendered)

    def test_natural_reuse_rejects_stale_edit_and_guarded_delete_before_any_effect(self):
        for method in ("PUT", "GUARDED_DELETE"):
            with self.subTest(method=method):
                identifier, original = self.create()
                revision = self.revision(identifier)
                replacement = self.replace(identifier)
                self.assertNotEqual(replacement, original)
                try:
                    extra = {"remark": "old intent", "certificate_id": "a" * 32} if method == "PUT" else {}
                    self.assert_conflict_without_effects(method, identifier, revision, **extra)
                finally:
                    self.assertEqual(self.request("DELETE", identifier)[0], 200)

    def test_same_id_restored_other_instance_rejects_even_with_identical_public_fields(self):
        identifier, old_instance = self.create()
        revision = self.revision(identifier)
        old = self.row(identifier)
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE inbounds SET instance_id=:instance WHERE id=:id"),
                {"id": identifier, "instance": "e" * 32})
        self.assertNotEqual(old_instance, self.row(identifier)["instance_id"])
        self.assertEqual({k: v for k, v in self.row(identifier).items() if k != "instance_id"},
                         {k: v for k, v in old.items() if k != "instance_id"})
        for method in ("PUT", "GUARDED_DELETE"):
            self.assert_conflict_without_effects(method, identifier, revision)

    def test_missing_guarded_target_is_explicit_unsaved_conflict(self):
        identifier, _ = self.create()
        revision = self.revision(identifier)
        self.assertEqual(self.request("DELETE", identifier)[0], 200)
        for method in ("PUT", "GUARDED_DELETE"):
            self.assert_conflict_without_effects(method, identifier, revision)
        self.assertEqual(self.request("PUT", identifier, {"remark": "legacy"})[0], 404)
        self.assertEqual(self.request("DELETE", identifier)[0], 404)

    def test_spoofed_or_other_nodes_revision_cannot_select_a_target(self):
        identifier, _ = self.create()
        other, _ = self.create("other", 10444)
        for revision in ("0" * 64, self.revision(other)):
            for method in ("PUT", "GUARDED_DELETE"):
                self.assert_conflict_without_effects(method, identifier, revision)

    def test_revision_is_strict_and_null_never_downgrades_to_legacy(self):
        identifier, _ = self.create()
        before = self.row(identifier)
        bad = (None, True, False, 1, 1.0, [], {}, "", "f" * 63, "f" * 65,
               "F" * 64, "g" * 64, " " + "f" * 64, "f" * 64 + "\n")
        for method in ("PUT", "GUARDED_DELETE"):
            for revision in bad:
                with self.subTest(method=method, revision=repr(revision)):
                    self.assertEqual(self.request(method, identifier,
                        {"expected_instance_revision": revision})[0], 422)
        self.assertEqual(self.row(identifier), before)

    def test_guarded_delete_requires_condition_and_rejects_extra_fields(self):
        identifier, _ = self.create()
        before = self.row(identifier)
        for body in ({}, {"expected_instance_revision": self.revision(identifier), "remark": "unexpected"},
                     {"expected_instance_revision": self.revision(identifier), "instance_id": before["instance_id"]}):
            self.assertEqual(self.request("GUARDED_DELETE", identifier, body)[0], 422)
        self.assertEqual(self.row(identifier), before)

    def test_same_instance_fields_uuid_and_unrelated_inventory_do_not_invalidate_guard(self):
        identifier, instance = self.create()
        revision = self.revision(identifier)
        with database.SessionLocal() as db:
            inbound_service.update_inbound(db, identifier, {
                "remark": "new label", "port": 11443, "tag": "changed", "expiry_time": 123,
                "settings": {"users": [{"uuid": UUIDS[1]}]}})
        other, _ = self.create("unrelated", 12443)
        self.assertEqual(self.request("DELETE", other)[0], 200)
        self.assertEqual(self.editor(identifier).get("mutation_revision"), revision)
        status, result = self.request("PUT", identifier, {
            "remark": "explicit edit", "expected_instance_revision": revision})
        self.assertEqual(status, 200, result)
        self.assertEqual(self.row(identifier)["settings"]["users"][0]["uuid"], UUIDS[1])
        self.assertEqual(self.row(identifier)["instance_id"], instance)
        self.assertEqual(self.request("GUARDED_DELETE", identifier,
            {"expected_instance_revision": revision})[0], 200)

    def test_same_instance_tuic_uuid_and_password_blank_preserve_and_explicit_replace(self):
        identifier, instance = self.create(protocol="tuic", settings={"users": [{
            "uuid": UUIDS[0], "password": "synthetic-original-password"}]}, profile={
            "security": "tls", "transport": "quic", "server_name": "vpn.example.test",
            "certificate_path": "/synthetic/cert.pem", "key_path": "/synthetic/key.pem"})
        revision = self.revision(identifier)
        for profile, expected_uuid, expected_password in (
            ({"tuic_uuid": "", "tuic_password": ""}, UUIDS[0], "synthetic-original-password"),
            ({"tuic_uuid": UUIDS[1], "tuic_password": ""}, UUIDS[1], "synthetic-original-password"),
            ({"tuic_uuid": "", "tuic_password": "synthetic-replacement-password"}, UUIDS[1], "synthetic-replacement-password")):
            status, result = self.request("PUT", identifier, {
                "expected_instance_revision": revision, "profile": profile})
            self.assertEqual(status, 200, result)
            current = self.row(identifier)
            self.assertEqual(current["instance_id"], instance)
            self.assertEqual(current["settings"]["users"][0], {
                "uuid": expected_uuid, "password": expected_password})
            self.assertEqual(self.editor(identifier).get("mutation_revision"), revision)

    def test_corrupt_identity_has_no_guard_and_cannot_be_mutated_guardedly(self):
        identifier, _ = self.create()
        revision = self.revision(identifier)
        for identity in (None, "", "x" * 32, "a" * 31, "A" * 32, "private corrupt value", "f" * 32 + "\n"):
            with self.subTest(identity=identity):
                with self.engine.begin() as connection:
                    connection.execute(text("UPDATE inbounds SET instance_id=:value WHERE id=:id"),
                        {"value": identity, "id": identifier})
                self.assertIsNone(self.list_rows()[0].get("mutation_revision"))
                self.assertIsNone(self.editor(identifier).get("mutation_revision"))
                for method in ("PUT", "GUARDED_DELETE"):
                    self.assert_conflict_without_effects(method, identifier, revision)

    def test_legacy_omission_and_delete_preserve_current_numeric_id_semantics(self):
        identifier, original = self.create()
        replacement = self.replace(identifier)
        self.assertNotEqual(original, replacement)
        status, result = self.request("PUT", identifier, {"remark": "legacy intent"})
        self.assertEqual(status, 200, result)
        self.assertEqual(self.row(identifier)["remark"], "legacy intent")
        self.assertEqual(self.row(identifier)["instance_id"], replacement)
        self.assertEqual(self.request("DELETE", identifier)[0], 200)
        self.assertIsNone(self.row(identifier))

    def test_guarded_reads_and_writes_do_not_return_private_instance_or_protocol_secrets(self):
        identifier, instance = self.create()
        revision = self.revision(identifier)
        results = [self.list_rows(), self.editor(identifier)]
        for method in ("PUT", "GUARDED_DELETE"):
            status, result = self.request(method, identifier, {"expected_instance_revision": revision})
            self.assertEqual(status, 200, result)
            results.append(result)
        for result in results:
            for secret in (instance, UUIDS[0], "instance_id", "inbound_bindings"):
                self.assertNotIn(secret, json.dumps(result))

    def test_guarded_update_checks_before_certificate_preparation(self):
        identifier, _ = self.create()
        revision = self.revision(identifier)
        replacement = self.replace(identifier)
        manager = Mock()
        for body in ({"certificate_id": "a" * 32}, {"certificate_id": None},
                     {"profile": {"security": "none"}}, {"profile": {"security": "reality"}}):
            with patch.object(certificates, "get_manager", return_value=manager) as get_manager:
                status, result = self.request("PUT", identifier, {
                    "expected_instance_revision": revision, **body})
            self.assertEqual((status, result), (409, {"detail": CHANGED}))
            get_manager.assert_not_called()
            self.assertEqual(self.row(identifier)["instance_id"], replacement)

    def test_guarded_delete_unbind_runs_after_commit_without_core_lock(self):
        identifier, _ = self.create()
        revision = self.revision(identifier)
        with database.SessionLocal() as db:
            db.add(CertificateBinding(target=f"inbound:{identifier}", certificate_id="a" * 32))
            db.commit()
        actual_unbind = self.manager.unbind
        def unbind(target):
            self.assertFalse(core_manager.lock._is_owned(), "core-to-binding lock inversion")
            self.assertIsNone(self.row(identifier), "unbind must follow committed deletion")
            return actual_unbind(target)
        with patch.object(self.manager, "unbind", side_effect=unbind) as called:
            status, result = self.request("GUARDED_DELETE", identifier,
                {"expected_instance_revision": revision})
        self.assertEqual(status, 200, result)
        called.assert_called_once_with(f"inbound:{identifier}")
        self.assertIsNone(self.manager.binding(f"inbound:{identifier}"))

    def test_guarded_saved_but_unapplied_remains_a_committed_success_boundary(self):
        for method in ("PUT", "GUARDED_DELETE"):
            identifier, _ = self.create()
            revision = self.revision(identifier)
            with patch.object(inbounds, "apply_checked", side_effect=HTTPException(
                    409, {"saved": True, "applied": False, "message": "synthetic runtime unavailable"})):
                status, result = self.request(method, identifier, {
                    "expected_instance_revision": revision, **({"remark": "committed"} if method == "PUT" else {})})
            self.assertEqual(status, 409, result)
            self.assertTrue(result["detail"]["saved"])
            if method == "PUT":
                self.assertEqual(self.row(identifier)["remark"], "committed")
                self.assertEqual(self.request("DELETE", identifier)[0], 200)
            else:
                self.assertIsNone(self.row(identifier))

    def test_guarded_request_waits_for_external_writer_and_rejects_committed_replacement(self):
        for method in ("PUT", "GUARDED_DELETE"):
            with self.subTest(method=method):
                identifier, instance = self.create()
                revision = self.revision(identifier)
                started = threading.Event()
                statements = []
                def observe(connection, cursor, statement, parameters, context, executemany):
                    if threading.current_thread().name.startswith("guarded-request"):
                        statements.append(statement)
                        if statement == "BEGIN IMMEDIATE":
                            started.set()
                event.listen(self.engine, "before_cursor_execute", observe)
                try:
                    with self.engine.connect() as writer:
                        writer.execute(text("BEGIN IMMEDIATE"))
                        writer.execute(text("DELETE FROM inbounds WHERE id=:id"), {"id": identifier})
                        with database.SessionLocal(bind=writer) as db:
                            replacement = database.Inbound(core="sing-box", protocol="vless", port=11443,
                                remark="replacement", enable=True, settings={"users": [{"uuid": UUIDS[1]}]},
                                stream_settings={})
                            db.add(replacement); db.flush()
                            self.assertEqual(replacement.id, identifier)
                            self.assertNotEqual(replacement.instance_id, instance)
                        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="guarded-request") as pool:
                            pending = pool.submit(self.request, method, identifier,
                                {"expected_instance_revision": revision})
                            try:
                                self.assertTrue(started.wait(5), "guard never attempted BEGIN IMMEDIATE")
                                self.assertFalse(pending.done(), "guard escaped SQLite writer lock")
                            finally:
                                writer.commit()
                            self.assertEqual(pending.result(timeout=5), (409, {"detail": CHANGED}))
                    self.assertEqual(statements[0], "BEGIN IMMEDIATE")
                    self.assertEqual(self.row(identifier)["remark"], "replacement")
                finally:
                    event.remove(self.engine, "before_cursor_execute", observe)
                    self.request("DELETE", identifier)

    def test_competing_writer_cannot_replace_target_after_check_before_commit(self):
        for method in ("PUT", "GUARDED_DELETE"):
            with self.subTest(method=method):
                identifier, _ = self.create()
                revision = self.revision(identifier)
                checked, release = threading.Event(), threading.Event()
                actual_commit = inbound_service._commit_inbound_change
                def hold_commit(db):
                    checked.set()
                    if not release.wait(5):
                        raise AssertionError("test did not release guarded mutation")
                    return actual_commit(db)
                try:
                    with ThreadPoolExecutor(max_workers=1) as pool, \
                         patch.object(inbound_service, "_commit_inbound_change", side_effect=hold_commit):
                        pending = pool.submit(self.request, method, identifier,
                            {"expected_instance_revision": revision})
                        try:
                            self.assertTrue(checked.wait(5), "guard never reached mutation commit")
                            with self.engine.connect() as writer:
                                writer.execute(text("PRAGMA busy_timeout=0"))
                                with self.assertRaises(OperationalError) as denied:
                                    writer.execute(text("BEGIN IMMEDIATE"))
                                self.assertIn("locked", str(denied.exception).lower())
                        finally:
                            release.set()
                        self.assertEqual(pending.result(timeout=5)[0], 200)
                finally:
                    if self.row(identifier) is not None:
                        self.assertEqual(self.request("DELETE", identifier)[0], 200)

    def test_guarded_service_refuses_existing_session_state_without_rolling_it_back(self):
        identifier, _ = self.create()
        revision = self.revision(identifier)
        for mode in ("read", "new", "dirty", "deleted", "external transaction"):
            with self.subTest(mode=mode), ExitStack() as stack:
                if mode == "external transaction":
                    connection = stack.enter_context(self.engine.connect())
                    transaction = connection.begin()
                    stack.callback(transaction.rollback)
                    db = stack.enter_context(database.SessionLocal(bind=connection))
                    self.assertFalse(db.in_transaction())
                else:
                    db = stack.enter_context(database.SessionLocal())
                    if mode == "read":
                        db.execute(text("SELECT 1"))
                    elif mode == "new":
                        db.add(database.User(username="pending", password_hash="synthetic", is_active=True))
                    else:
                        item = db.get(database.Inbound, identifier)
                        if mode == "dirty": item.remark = "not yet saved"
                        else: db.delete(item)
                before = (set(db.new), set(db.dirty), set(db.deleted), db.in_transaction())
                with patch.object(db, "rollback", wraps=db.rollback) as rollback:
                    with self.assertRaisesRegex(RuntimeError, "pristine"):
                        inbounds.edit_inbound(identifier, inbounds.InboundUpdatePayload.model_validate({
                            "expected_instance_revision": revision, "remark": "must not write"}), db=db)
                    rollback.assert_not_called()
                self.assertEqual((set(db.new), set(db.dirty), set(db.deleted), db.in_transaction()), before)
                if mode == "external transaction": self.assertTrue(transaction.is_active)
        self.assertEqual(self.row(identifier)["remark"], "original")

    def test_response_is_frozen_before_commit_and_not_refreshed_to_later_replacement(self):
        identifier, instance = self.create()
        revision = self.revision(identifier)
        actual_commit = inbound_service._commit_inbound_change
        def commit_then_replace(db):
            actual_commit(db)
            # Different connection deliberately bypasses the in-process lock,
            # representing a later committed writer, not a mocked ORM refresh.
            with sqlite3.connect(self.path) as writer:
                writer.execute("DELETE FROM inbounds WHERE id=?", (identifier,))
                writer.execute("INSERT INTO inbounds (instance_id, core, protocol, remark, port, enable, "
                    "settings, stream_settings, routing_mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("f" * 32, "xray", "vless", "postcommit replacement", 12443, 1,
                     json.dumps({"users": [{"id": UUIDS[1]}]}), "{}", "inherit"))
                self.assertEqual(writer.execute("SELECT id FROM inbounds").fetchone()[0], identifier)
        self.apply.reset_mock()
        with patch.object(inbound_service, "_commit_inbound_change", side_effect=commit_then_replace):
            status, result = self.request("PUT", identifier, {
                "expected_instance_revision": revision, "remark": "checked committed intent"})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["inbound"]["remark"], "checked committed intent")
        self.assertEqual(result["inbound"]["core"], "sing-box")
        self.assertEqual(result["inbound"]["mutation_revision"], revision)
        self.apply.assert_called_once_with("sing-box")
        self.assertEqual(self.row(identifier)["remark"], "postcommit replacement")
        self.assertNotEqual(self.row(identifier)["instance_id"], instance)

    def test_certificate_read_preparation_is_checked_but_unbind_is_postcommit(self):
        identifier, _ = self.create(profile={"security": "tls", "transport": "direct",
            "server_name": "vpn.example.test", "certificate_path": "/synthetic/cert.pem",
            "key_path": "/synthetic/key.pem"})
        revision = self.revision(identifier)
        with database.SessionLocal() as db:
            db.add(CertificateBinding(target=f"inbound:{identifier}", certificate_id="a" * 32))
            db.commit()
        actual_binding, actual_unbind = self.manager.binding, self.manager.unbind
        def binding(target):
            self.assertTrue(core_manager.lock._is_owned())
            with self.engine.connect() as writer:
                writer.execute(text("PRAGMA busy_timeout=0"))
                with self.assertRaises(OperationalError): writer.execute(text("BEGIN IMMEDIATE"))
            return actual_binding(target)
        def unbind(target):
            self.assertFalse(core_manager.lock._is_owned(), "core-to-binding lock inversion")
            self.assertFalse(self.row(identifier)["stream_settings"].get("tls", {}).get("enabled", False))
            return actual_unbind(target)
        with patch.object(self.manager, "binding", side_effect=binding) as read, \
             patch.object(self.manager, "unbind", side_effect=unbind) as write:
            status, result = self.request("PUT", identifier, {"expected_instance_revision": revision,
                "profile": {"security": "none"}, "certificate_id": None})
        self.assertEqual(status, 200, result)
        read.assert_called_once_with(f"inbound:{identifier}")
        write.assert_called_once_with(f"inbound:{identifier}")

    def test_guarded_policy_rejection_rolls_back_and_has_no_postcommit_effect(self):
        identifier, _ = self.create()
        revision = self.revision(identifier)
        before = self.row(identifier)
        self.apply.reset_mock()
        with patch.object(core_manager, "validate_inbound_change", side_effect=CoreError("synthetic policy rejection")), \
             patch.object(self.manager, "bind") as bind, patch.object(self.manager, "unbind") as unbind:
            for method in ("PUT", "GUARDED_DELETE"):
                status, result = self.request(method, identifier, {"expected_instance_revision": revision,
                    **({"remark": "must roll back"} if method == "PUT" else {})})
                self.assertEqual(status, 409, result)
                self.assertFalse(result["detail"]["saved"])
                self.assertEqual(self.row(identifier), before)
            bind.assert_not_called(); unbind.assert_not_called()
        self.apply.assert_not_called()


class InboundGuardedDeleteBoundaryTests(unittest.TestCase):
    """Existing in-process TestClient auth fixture; never an HTTP listener.

    Separate from the direct-route fixture so its blanket socket prohibition
    stays intact. TestClient's event-loop wakeup pair is local IPC; network
    bind/listen/connect/DNS and product subprocesses are explicitly forbidden.
    """
    def setUp(self):
        import test_auth as auth_tests
        self.auth_tests = auth_tests
        stack = ExitStack()
        self.addCleanup(stack.close)
        for target in ("subprocess.Popen", "socket.create_connection", "socket.getaddrinfo",
                       "socket.socket.bind", "socket.socket.listen", "socket.socket.connect",
                       "socket.socket.connect_ex"):
            stack.enter_context(patch(target, side_effect=AssertionError(
                "Guarded delete auth test must not launch cores or use a network")))
        stack.enter_context(patch.dict(os.environ, {"VUI_CERTIFICATES_ENABLED": "0"}))
        stack.enter_context(patch.object(core_manager, "recover_all"))
        stack.enter_context(patch.object(core_manager, "stop_all"))
        auth_tests.AuthenticationTests.setUp(self)

    def test_new_delete_action_requires_authentication_and_exact_write_headers(self):
        with database.SessionLocal() as db:
            row = database.Inbound(core="sing-box", protocol="vless", port=10443,
                remark="boundary target", enable=True, settings={"users": [{"uuid": UUIDS[0]}]},
                stream_settings={})
            db.add(row); db.commit(); identifier = row.id
        path = f"/api/inbounds/{identifier}/delete"
        body = {"expected_instance_revision": "0" * 64}
        actual = inbound_service.delete_inbound_if_instance
        with patch.object(inbounds, "delete_inbound_if_instance", wraps=actual) as handler:
            response = self.client.post(path, headers=self.auth_tests.HEADERS, json=body)
            self.assertEqual(response.status_code, 401, response.text)
            self.assertIn("no-store", response.headers["cache-control"])
            handler.assert_not_called()
            self.assertEqual(self.auth_tests.AuthenticationTests.login(self).status_code, 200)
            for headers in ({}, {"Origin": self.auth_tests.ORIGIN}, {"X-VUI-Request": "1"},
                            {**self.auth_tests.HEADERS, "Origin": "https://other.example.test"},
                            {**self.auth_tests.HEADERS, "X-VUI-Request": "wrong"}):
                with self.subTest(headers=headers):
                    response = self.client.post(path, headers=headers, json=body)
                    self.assertEqual(response.status_code, 403, response.text)
                    self.assertIn("no-store", response.headers["cache-control"])
                    handler.assert_not_called()
            # A real authenticated request reaches the guarded service. Its
            # intentionally stale condition fails before policy/runtime work.
            response = self.client.post(path, headers=self.auth_tests.HEADERS, json=body)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json(), {"detail": CHANGED})
            handler.assert_called_once()
        with database.SessionLocal() as db:
            self.assertEqual(db.get(database.Inbound, identifier).remark, "boundary target")


if __name__ == "__main__":
    unittest.main()
