"""Single-admin authentication. No default password, JWT, or external service."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException
from sqlalchemy import Column, Integer, String, text
from sqlalchemy.exc import IntegrityError

from app.models import database

# OWASP minimum Argon2id parameters; bounded memory suits a personal VPS.
HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID)
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(32))
SESSION_SECONDS = 3600
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class AdminSession(database.Base):
    __tablename__ = "admin_sessions"
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    credential_stamp = Column(String(64), nullable=False)
    created_at = Column(Integer, nullable=False)
    expires_at = Column(Integer, nullable=False, index=True)


class LoginBucket(database.Base):
    __tablename__ = "admin_login_buckets"
    key = Column(String(64), primary_key=True)
    attempts = Column(Integer, nullable=False)
    resets_at = Column(Integer, nullable=False, index=True)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def valid_username(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", value))


def validate_password(value: str) -> None:
    if not 15 <= len(value) <= 128:
        raise ValueError("Password must contain 15–128 characters")


def _verify(stored: str | None, password: str) -> bool:
    # Legacy/plain/mock credentials are not silently upgraded into admins.
    candidate = stored if stored and stored.startswith("$argon2id$") else DUMMY_HASH
    try:
        matched = HASHER.verify(candidate, password)
    except (VerificationError, InvalidHashError):
        return False
    return bool(stored and candidate == stored and matched)


def _public_user(user: database.User) -> dict:
    return {"id": user.id, "username": user.username, "role": "admin"}


def take_login_budget(address: str, username: str) -> None:
    """SQLite-serialized, restart-persistent limits. Ignore forwarded IP headers."""
    now = int(time.time())
    limits = [("global", 60, 60), ("ip:" + address, 8, 300),
              ("account:" + username.casefold(), 20, 900)]
    with database.SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        db.query(LoginBucket).filter(LoginBucket.resets_at <= now).delete()
        buckets = []
        for name, limit, window in limits:
            key = digest(name)
            bucket = db.get(LoginBucket, key)
            if bucket is not None and bucket.attempts >= limit:
                retry = max(1, bucket.resets_at - now)
                db.rollback()
                raise HTTPException(429, "Too many login attempts",
                                    headers={"Retry-After": str(retry)})
            if bucket is None:
                bucket = LoginBucket(key=key, attempts=0, resets_at=now + window)
                db.add(bucket)
            buckets.append(bucket)
        for bucket in buckets:
            bucket.attempts += 1
        db.commit()


def login(username: str, password: str, address: str, previous: str = "") -> tuple[dict, str]:
    take_login_budget(address, username)
    now = int(time.time())
    with database.SessionLocal() as db:
        # Serialize credential changes with login to avoid reset/login races.
        db.execute(text("BEGIN IMMEDIATE"))
        user = db.query(database.User).filter_by(username=username).first()
        verified = _verify(user.password_hash if user else None, password)
        if not user or not user.is_active or not verified:
            raise HTTPException(401, "Invalid credentials")
        if HASHER.check_needs_rehash(user.password_hash):
            user.password_hash = HASHER.hash(password)
            db.query(AdminSession).filter_by(user_id=user.id).delete()
        db.query(AdminSession).filter(AdminSession.expires_at <= now).delete()
        if previous:
            db.query(AdminSession).filter_by(token_hash=digest(previous)).delete()
        # Retain no more than five simultaneous sessions for this administrator.
        old = db.query(AdminSession).filter_by(user_id=user.id).order_by(
            AdminSession.created_at.desc(), AdminSession.token_hash.asc()).all()
        for session in old[4:]:
            db.delete(session)
        token = secrets.token_urlsafe(32)
        db.add(AdminSession(token_hash=digest(token), user_id=user.id,
                            credential_stamp=digest(user.password_hash),
                            created_at=now, expires_at=now + SESSION_SECONDS))
        result = _public_user(user)
        db.commit()
        return result, token


def authenticate(token: str) -> dict | None:
    if not TOKEN_PATTERN.fullmatch(token):
        return None
    with database.SessionLocal() as db:
        session = db.get(AdminSession, digest(token))
        if session is None or session.expires_at <= int(time.time()):
            return None
        user = db.get(database.User, session.user_id)
        if not user or not user.is_active or not user.password_hash:
            return None
        if not hmac.compare_digest(session.credential_stamp, digest(user.password_hash)):
            return None
        return _public_user(user)


def logout(token: str) -> None:
    with database.SessionLocal() as db:
        db.query(AdminSession).filter_by(token_hash=digest(token)).delete()
        db.commit()


def change_profile(user_id: int, username: str, current_password: str,
                   new_password: str | None) -> None:
    if not valid_username(username):
        raise HTTPException(422, "Username must be 3–64 ASCII letters, digits, _, . or -")
    if new_password is not None:
        try:
            validate_password(new_password)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    with database.SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        user = db.get(database.User, user_id)
        if not user or not user.is_active or not _verify(user.password_hash, current_password):
            raise HTTPException(401, "Current password is incorrect")
        user.username = username
        if new_password is not None:
            user.password_hash = HASHER.hash(new_password)
        db.query(AdminSession).filter_by(user_id=user_id).delete()
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409, "Username is unavailable") from exc


def provision_admin(username: str, password: str, *, reset: bool = False) -> None:
    """Local CLI only. Never called by a public registration endpoint."""
    if not valid_username(username):
        raise ValueError("Username must be 3–64 ASCII letters, digits, _, . or -")
    validate_password(password)
    password_hash = HASHER.hash(password)
    database.init_db()
    with database.SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        user = db.query(database.User).filter_by(username=username).first()
        if reset:
            if user is None:
                raise ValueError("Administrator does not exist")
            user.password_hash = password_hash
            user.is_active = True
            db.query(AdminSession).filter_by(user_id=user.id).delete()
        else:
            # This panel is single-admin; no implicit multi-user privilege model.
            if db.query(database.User).first() is not None:
                raise ValueError("An account already exists; use set-password for its username")
            db.add(database.User(username=username, password_hash=password_hash, is_active=True))
        db.commit()
