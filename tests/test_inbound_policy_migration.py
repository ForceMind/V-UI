"""Temporary SQLite migration tests: never operate on an installed database."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models import database
from app.services.inbound_policy import PolicyError, row_mode


class InboundPolicyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "data"
        self.root.mkdir(mode=0o700)
        self.path = self.root / "v-ui.db"
        self.engine = create_engine(f"sqlite:///{self.path}", poolclass=NullPool)
        self.addCleanup(self.engine.dispose)
        self.override = patch.object(database, "engine", self.engine)
        self.override.start(); self.addCleanup(self.override.stop)

    def legacy(self, *, core=True, mode_column=False, modes=("inherit",)):
        core_column = ", core VARCHAR NOT NULL DEFAULT 'xray'" if core else ""
        mode = ", routing_mode VARCHAR" if mode_column else ""
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE inbounds (id INTEGER PRIMARY KEY, user_id INTEGER, up INTEGER, down INTEGER, total INTEGER, remark VARCHAR, enable BOOLEAN, expiry_time INTEGER, port INTEGER, protocol VARCHAR, settings JSON, stream_settings JSON, tag VARCHAR" + core_column + mode + ")")
            for i, value in enumerate(modes, 1):
                conn.execute("INSERT INTO inbounds (id,user_id,up,down,total,remark,enable,expiry_time,port,protocol,settings,stream_settings,tag" + (",routing_mode" if mode_column else "") + ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?" + (",?" if mode_column else "") + ")", (i, 7, 123, 456, 789, "unchanged", 1, 234, 11000+i, "vless", '{"users":[{"id":"fixture-uuid"}]}', '{"tls":{"key_path":"/fixture/key.pem"}}', "legacy-tag-"+str(i)) + ((value,) if mode_column else ()))

    def data(self):
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute("SELECT * FROM inbounds ORDER BY id")]

    def test_missing_column_migrates_exact_rows_idempotently(self):
        for with_core in (False, True):
            with self.subTest(core=with_core):
                if self.path.exists(): self.path.unlink()
                self.legacy(core=with_core)
                before = self.data()
                database.init_db(); database.init_db()
                after = self.data()
                for old, new in zip(before, after):
                    self.assertEqual(new.pop("routing_mode"), "inherit")
                    if not with_core: self.assertEqual(new.pop("core"), "xray")
                    self.assertEqual(old, new)
                columns = {column["name"]: column for column in inspect(self.engine).get_columns("inbounds")}
                self.assertFalse(columns["routing_mode"]["nullable"])
                self.assertEqual(columns["routing_mode"]["default"], "'inherit'")

    def test_existing_valid_values_survive_repeated_and_concurrent_initialization(self):
        self.legacy(mode_column=True, modes=("inherit", "direct", "relay"))
        before = self.data()
        with ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(lambda _: database.init_db(), range(6)))
        self.assertEqual(before, self.data())

    def test_existing_null_empty_case_unknown_and_non_string_fail_without_repair(self):
        for value in (None, "", "DIRECT", " relay", "relay ", "unknown", 1):
            with self.subTest(mode=value):
                if self.path.exists(): self.path.unlink()
                self.legacy(mode_column=True, modes=("direct", value))
                before = self.data()
                with self.assertRaisesRegex(PolicyError, "INVALID_ROUTING_MODE"):
                    database.init_db()
                self.assertEqual(before, self.data())

    def test_new_database_default_and_explicit_mode_values(self):
        database.init_db()
        with Session(self.engine) as session:
            for mode in (None, "direct", "relay"):
                kwargs = {} if mode is None else {"routing_mode": mode}
                session.add(database.Inbound(port=12000, protocol="vless", **kwargs))
            session.commit()
            self.assertEqual([row_mode(row) for row in session.query(database.Inbound).order_by(database.Inbound.id)], ["inherit", "direct", "relay"])
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO inbounds (port,protocol,core) VALUES (12001,'vless','xray')"))
            self.assertEqual(conn.execute(text("SELECT routing_mode FROM inbounds WHERE port=12001")).scalar(), "inherit")

    def test_explicit_invalid_orm_assignment_is_never_sqlalchemy_defaulted(self):
        for value in (None, "", "inherit ", True, 1, {}):
            with self.subTest(value=value), self.assertRaises(PolicyError):
                database.Inbound(routing_mode=value)
        row = database.Inbound(routing_mode="relay")
        with self.assertRaises(PolicyError): row.routing_mode = None
        self.assertEqual(row.routing_mode, "relay")

    def test_raw_corrupt_persisted_row_not_healed_by_model_or_plan(self):
        self.legacy(mode_column=True, modes=(None,))
        with Session(self.engine) as session:
            row = session.query(database.Inbound).one()
            self.assertIsNone(row.routing_mode)
            with self.assertRaises(PolicyError): row_mode(row)
        self.assertIsNone(self.data()[0]["routing_mode"])

    def test_initialization_uses_existing_operation_lock(self):
        from app.services.core_manager import core_manager
        original = core_manager.lock
        entered = []
        class Lock:
            def __enter__(self):
                entered.append(True); original.__enter__()
            def __exit__(self, *args):
                return original.__exit__(*args)
        with patch.object(core_manager, "lock", Lock()):
            database.init_db()
        self.assertEqual(entered, [True])


if __name__ == "__main__":
    unittest.main()
