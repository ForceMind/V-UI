from __future__ import annotations

import os
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


def _migrate_legacy_schema() -> None:
    """Keep existing installations working without introducing Alembic yet."""
    # Serialize schema inspection and DDL across processes as well as the
    # panel's operation lock. Only an actually missing column gets defaults.
    from app.services.inbound_policy import normalize_mode
    with engine.connect() as conn:
        conn.execute(text("BEGIN IMMEDIATE"))
        try:
            inspector = inspect(conn)
            if "inbounds" not in inspector.get_table_names():
                conn.commit()
                return
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
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def init_db() -> None:
    # A lazy import avoids making model import create runtime state; migration
    # uses the same operation lock as every panel-mediated inbound mutation.
    from app.services.core_manager import core_manager
    with core_manager.lock, data_owner(Path(engine.url.database).absolute().parent):
        _guard_policy_schema_before_create()
        Base.metadata.create_all(bind=engine)
        _migrate_legacy_schema()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
