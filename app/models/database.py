from __future__ import annotations

import os
import secrets
import sqlite3
from pathlib import Path

from sqlalchemy import Boolean, Column, Integer, JSON, String, create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker, validates
from sqlalchemy.pool import NullPool

from app.data_owner import data_owner

DATA_DIR = Path(os.getenv("VUI_DATA_DIR", "data"))
DB_PATH = DATA_DIR / "v-ui.db"

SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

class _OwnedConnection(sqlite3.Connection):
    """Keep the tree leased until SQLite has stopped using this inode."""
    _data_owner = None

    def close(self):
        try:
            super().close()
        finally:
            owner, self._data_owner = self._data_owner, None
            if owner is not None:
                owner.__exit__(None, None, None)


def _connect():
    owner = data_owner(DATA_DIR)
    owner.__enter__()
    try:
        DATA_DIR.mkdir(mode=0o700, exist_ok=True)
        connection = sqlite3.connect(str(DB_PATH), check_same_thread=False, factory=_OwnedConnection)
        connection._data_owner = owner
        return connection
    except BaseException:
        owner.__exit__(None, None, None)
        raise


engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    creator=_connect,
    # Never retain a connection to an old data inode across an offline restore.
    poolclass=NullPool,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    is_active = Column(Boolean, default=True)


class Inbound(Base):
    __tablename__ = "inbounds"

    id = Column(Integer, primary_key=True, index=True)
    # Private authorization identity, not a protocol credential or runtime
    # source field. NULL/corrupt imported values must fail closed, not heal.
    instance_id = Column(String(32), nullable=True, default=lambda: secrets.token_hex(16))
    user_id = Column(Integer, nullable=True)
    core = Column(String, nullable=False, default="xray", index=True)
    routing_mode = Column(String, nullable=False, default="inherit", server_default="inherit")
    up = Column(Integer, default=0)
    down = Column(Integer, default=0)
    total = Column(Integer, default=0)
    remark = Column(String)
    enable = Column(Boolean, default=True)
    expiry_time = Column(Integer, default=0)
    port = Column(Integer)
    protocol = Column(String)
    settings = Column(JSON, default=dict)
    stream_settings = Column(JSON, default=dict)
    tag = Column(String)

    @validates("routing_mode")
    def _validate_routing_mode(self, key, value):
        from app.services.inbound_policy import normalize_mode
        return normalize_mode(value)


def _saved_policy_schema() -> int:
    from app.services.proxy_store import ProxyStore
    data = Path(engine.url.database).absolute().parent
    names = ('proxy-state', '.proxy-state-initialized', '.proxy-state.lock')
    return (ProxyStore(data).policy_schema_version()
            if any(os.path.lexists(data / name) for name in names) else 1)


def _guard_policy_schema_before_create() -> None:
    """Do not let create_all/ALTER disguise loss of a fenced policy column."""
    from app.services.proxy_store import ProxyStorageError
    if _saved_policy_schema() != 2:
        return
    path = Path(engine.url.database).absolute()
    if not path.is_file():
        raise ProxyStorageError("Per-inbound database is missing; offline recovery is required")
    with engine.connect() as conn:
        inspector = inspect(conn)
        if ("inbounds" not in inspector.get_table_names()
                or "routing_mode" not in {column["name"] for column in inspector.get_columns("inbounds")}):
            raise ProxyStorageError("Per-inbound database column is missing; offline recovery is required")


class SubscriptionIdentitySchemaError(RuntimeError):
    pass


def _guard_subscription_identity_schema(conn) -> None:
    """A marked database cannot become legacy by losing an identity column."""
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())
    if "subscription_identity_schema" not in tables:
        return
    columns = {column["name"] for column in inspector.get_columns("subscription_identity_schema")}
    valid = columns == {"version"}
    if valid:
        valid = conn.execute(text("SELECT typeof(version), version FROM subscription_identity_schema")).all() == [("integer", 1)]
    for table, column in (("inbounds", "instance_id"), ("subscription_grants", "inbound_bindings")):
        valid = valid and table in tables and column in {
            item["name"] for item in inspector.get_columns(table)}
    if not valid:
        raise SubscriptionIdentitySchemaError(
            "Subscription identity schema is incomplete or unsupported; restore a verified full database")


def _migrate_subscription_identity(conn) -> None:
    """Called inside the same serialized transaction as legacy SQL migration.

    No existing identity is repaired and no grant is inferred from numeric IDs.
    The marker guards subsequent partial restores/column loss independently of
    the unrelated per-inbound routing capability fence.
    """
    _guard_subscription_identity_schema(conn)
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())
    if "subscription_grants" not in tables:
        # init_db imports its model before create_all; this supports the narrow
        # legacy helper without pretending an incomplete schema was migrated.
        raise SubscriptionIdentitySchemaError("Subscription grant schema is missing")
    inbound_columns = {column["name"] for column in inspector.get_columns("inbounds")}
    grant_columns = {column["name"] for column in inspector.get_columns("subscription_grants")}
    if "instance_id" not in inbound_columns:
        if ("inbound_bindings" in grant_columns and conn.execute(text(
                "SELECT 1 FROM subscription_grants WHERE inbound_bindings IS NOT NULL LIMIT 1")).first()):
            raise SubscriptionIdentitySchemaError("Bound subscription identities are missing; restore a verified full database")
        conn.execute(text("ALTER TABLE inbounds ADD COLUMN instance_id VARCHAR(32)"))
        for identifier in conn.execute(text("SELECT id FROM inbounds ORDER BY id")).scalars().all():
            conn.execute(text("UPDATE inbounds SET instance_id=:identity WHERE id=:id"),
                         {"identity": secrets.token_hex(16), "id": identifier})
    if "inbound_bindings" not in grant_columns:
        conn.execute(text("ALTER TABLE subscription_grants ADD COLUMN inbound_bindings TEXT"))
    if "subscription_identity_schema" not in tables:
        conn.execute(text("CREATE TABLE subscription_identity_schema (version INTEGER NOT NULL)"))
        conn.execute(text("INSERT INTO subscription_identity_schema (version) VALUES (1)"))


def _migrate_legacy_schema(conn) -> None:
    """Migrate under init_db's serialized, atomic create/migrate transaction."""
    from app.services.inbound_policy import normalize_mode
    inspector = inspect(conn)
    columns = {column["name"] for column in inspector.get_columns("inbounds")}
    if "core" not in columns:
        conn.execute(text("ALTER TABLE inbounds ADD COLUMN core VARCHAR NOT NULL DEFAULT 'xray'"))
    if "routing_mode" not in columns:
        conn.execute(text("ALTER TABLE inbounds ADD COLUMN routing_mode VARCHAR NOT NULL DEFAULT 'inherit'"))
    modes = {normalize_mode(value) for value in conn.execute(
        text("SELECT DISTINCT routing_mode FROM inbounds")).scalars()}
    if modes - {"inherit"} and _saved_policy_schema() != 2:
        from app.services.proxy_store import ProxyStorageError
        raise ProxyStorageError("Inbound routing capability fence is missing; offline recovery is required")
    _migrate_subscription_identity(conn)


def init_db() -> None:
    # A lazy import avoids making model import create runtime state; migration
    # uses the same operation lock as every panel-mediated inbound mutation.
    from app.services.core_manager import core_manager
    with core_manager.lock, data_owner(Path(engine.url.database).absolute().parent):
        _guard_policy_schema_before_create()
        # Register grants even when init_db is called from the standalone CLI.
        from app.services import subscription_tokens  # noqa: F401
        with engine.connect() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            try:
                # Recheck under the cross-process write lock before any DDL.
                _guard_policy_schema_before_create()
                _guard_subscription_identity_schema(conn)
                Base.metadata.create_all(bind=conn)
                _migrate_legacy_schema(conn)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
