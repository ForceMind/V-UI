from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Boolean, Column, Integer, JSON, String, create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

DATA_DIR = Path(os.getenv("VUI_DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "v-ui.db"

SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
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
    Base.metadata.create_all(bind=engine)
    _migrate_legacy_schema()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
