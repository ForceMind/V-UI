"""Read-only, node-scoped subscription grants, independent of admin sessions."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
import time

from fastapi import HTTPException
from sqlalchemy import Boolean, Column, Integer, JSON, String, text

from app.models import database

TOKEN_RE = re.compile(r"^vui_s_[A-Za-z0-9_-]{43}$")
FORMATS = {"mihomo.yaml", "raw", "sing-box.json"}


class SubscriptionGrant(database.Base):
    __tablename__ = "subscription_grants"
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    credential_stamp = Column(String(64), nullable=False)
    label = Column(String(128), nullable=False)
    server = Column(String(253), nullable=False)
    inbound_ids = Column(JSON, nullable=False)
    formats = Column(JSON, nullable=False)
    created_at = Column(Integer, nullable=False)
    expires_at = Column(Integer, nullable=False)
    revoked = Column(Boolean, nullable=False, default=False)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_server(value: str) -> str:
    """A connection hostname/IP, never a URL, listen address, or Host header."""
    value = value.strip()
    if not value or any(c.isspace() or ord(c) < 32 or c in "/\\?#@" for c in value):
        raise ValueError("Invalid public node address")
    try:
        address = ipaddress.ip_address(value[1:-1] if value.startswith("[") and value.endswith("]") else value)
        if address.is_unspecified or "%" in value:
            raise ValueError("Invalid public node address")
        return address.compressed
    except ValueError:
        if ":" in value or "[" in value or "]" in value or "%" in value:
            raise ValueError("Invalid public node address") from None
    try:
        host = value.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("Invalid public node address") from None
    if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p) for p in host.split(".")):
        raise ValueError("Invalid public node address")
    # An invalid numeric IP must not fall through and become a domain.
    if re.fullmatch(r"[0-9.]+", host):
        raise ValueError("Invalid public node address")
    return host


def public_record(grant: SubscriptionGrant) -> dict:
    return {"id": grant.id, "label": grant.label, "server": grant.server,
            "inbound_ids": list(grant.inbound_ids), "formats": list(grant.formats),
            "created_at": grant.created_at, "expires_at": grant.expires_at,
            "revoked": grant.revoked, "expired": grant.expires_at <= int(time.time())}


def _owner(db, owner_id: int):
    user = db.get(database.User, owner_id)
    if user is None or not user.is_active or not user.password_hash:
        raise HTTPException(401, "Authentication required")
    return user


def create_grant(owner_id: int, label: str, server: str, inbound_ids: list[int],
                 formats: list[str], days: int) -> tuple[dict, str]:
    try:
        server = normalize_server(server)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    ids = sorted(set(inbound_ids))
    formats = sorted(set(formats))
    if not ids or not formats or not set(formats) <= FORMATS:
        raise HTTPException(422, "Select nodes and supported output formats")
    now = int(time.time())
    with database.SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        user = _owner(db, owner_id)
        rows = db.query(database.Inbound).filter(database.Inbound.id.in_(ids)).all()
        if len(rows) != len(ids) or any(not row.enable for row in rows):
            raise HTTPException(422, "Selected nodes are missing or disabled")
        from app.services.validated_export import validated_nodes
        validated_nodes(rows, server)
        token = "vui_s_" + secrets.token_urlsafe(32)
        grant = SubscriptionGrant(owner_id=owner_id, token_hash=digest(token),
            credential_stamp=digest(user.password_hash), label=label, server=server,
            inbound_ids=ids, formats=formats, created_at=now,
            expires_at=now + days * 86400, revoked=False)
        db.add(grant)
        db.flush()
        result = public_record(grant)
        db.commit()
        return result, token


def list_grants(owner_id: int) -> list[dict]:
    with database.SessionLocal() as db:
        user = _owner(db, owner_id)
        return [{**public_record(g), "invalidated": not hmac.compare_digest(
            g.credential_stamp, digest(user.password_hash))} for g in db.query(SubscriptionGrant).filter_by(
            owner_id=owner_id).order_by(SubscriptionGrant.id.desc()).all()]


def change_grant(owner_id: int, grant_id: int, *, rotate_days: int | None = None) -> tuple[dict, str | None]:
    with database.SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        user = _owner(db, owner_id)
        grant = db.query(SubscriptionGrant).filter_by(id=grant_id, owner_id=owner_id).first()
        if grant is None:
            raise HTTPException(404, "Subscription not found")
        token = None
        if rotate_days is None:
            grant.revoked = True
        else:
            token = "vui_s_" + secrets.token_urlsafe(32)
            grant.token_hash = digest(token)
            grant.credential_stamp = digest(user.password_hash)
            grant.expires_at = int(time.time()) + rotate_days * 86400
            grant.revoked = False
        result = public_record(grant)
        db.commit()
        return result, token


def authorized_nodes(db, token: str, output_format: str):
    """No fallback to admin identity, all nodes, or an alternate output format."""
    denied = HTTPException(404, "Subscription not found")
    if not TOKEN_RE.fullmatch(token) or output_format not in FORMATS:
        raise denied
    grant = db.query(SubscriptionGrant).filter_by(token_hash=digest(token)).first()
    if grant is None or grant.revoked or grant.expires_at <= int(time.time()) or output_format not in grant.formats:
        raise denied
    user = db.get(database.User, grant.owner_id)
    if (not user or not user.is_active or not user.password_hash
            or not hmac.compare_digest(grant.credential_stamp, digest(user.password_hash))):
        raise denied
    rows = db.query(database.Inbound).filter(database.Inbound.id.in_(grant.inbound_ids)).order_by(database.Inbound.id).all()
    # Do not silently expand the scope or turn an empty selection into DIRECT.
    if len(rows) != len(grant.inbound_ids) or any(not row.enable for row in rows):
        raise HTTPException(409, "Subscription configuration is not ready")
    return rows, grant.server
