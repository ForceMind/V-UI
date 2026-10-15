from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from sqlalchemy import Boolean, Column, Integer, JSON, String, create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
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


def _migrate_legacy_schema() -> None:
    """Keep existing installations working without introducing Alembic yet."""
    inspector = inspect(engine)
    if "inbounds" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("inbounds")}
    if "core" not in columns:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE inbounds "
                    "ADD COLUMN core VARCHAR NOT NULL DEFAULT 'xray'"
                )
            )


def init_db() -> None:
    with data_owner(Path(engine.url.database).absolute().parent):
        Base.metadata.create_all(bind=engine)
        _migrate_legacy_schema()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
