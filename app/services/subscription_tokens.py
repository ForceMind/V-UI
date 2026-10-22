"""Read-only, node-scoped subscription grants, independent of admin sessions."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import time

from fastapi import HTTPException
from sqlalchemy import Boolean, Column, Integer, String, Text, text
from sqlalchemy.types import TypeDecorator

from app.models import database

TOKEN_RE = re.compile(r"^vui_s_[A-Za-z0-9_-]{43}$")
FORMATS = {"mihomo.yaml", "raw", "sing-box.json"}
INSTANCE_RE = re.compile(r"[0-9a-f]{32}\Z")


class _GrantJSON(TypeDecorator):
    """Malformed storage (including duplicate JSON keys) is never authority."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else json.dumps(value, allow_nan=False)

    def process_result_value(self, value, dialect):
        def pairs(items):
            result = {}
            for key, item in items:
                if key in result:
                    raise ValueError("Duplicate binding key")
                result[key] = item
            return result
        try:
            return None if value is None else json.loads(value, object_pairs_hook=pairs)
        except (ValueError, TypeError, RecursionError):
            return None


class SubscriptionGrant(database.Base):
    __tablename__ = "subscription_grants"
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    credential_stamp = Column(String(64), nullable=False)
    label = Column(String(128), nullable=False)
    server = Column(String(253), nullable=False)
    inbound_ids = Column(_GrantJSON, nullable=False)
    # Deliberately nullable: legacy grants cannot prove a historical binding.
    # Migration, lookup and rotation must never infer one from current IDs.
    inbound_bindings = Column(_GrantJSON, nullable=True)
    formats = Column(_GrantJSON, nullable=False)
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
            "inbound_ids": list(grant.inbound_ids) if _valid_ids(grant.inbound_ids) else [],
            "formats": list(grant.formats) if _valid_formats(grant.formats) else [],
            "created_at": grant.created_at, "expires_at": grant.expires_at,
            "revoked": grant.revoked, "expired": grant.expires_at <= int(time.time())}


def _valid_ids(ids) -> bool:
    return (type(ids) is list and 0 < len(ids) <= 256
            and all(type(value) is int and 0 < value <= 9223372036854775807 for value in ids)
            and len(set(ids)) == len(ids))


def _valid_formats(formats) -> bool:
    return (type(formats) is list and 0 < len(formats) <= len(FORMATS)
            and all(type(value) is str and value in FORMATS for value in formats)
            and len(set(formats)) == len(formats))


def _row_bindings(db, rows) -> dict | None:
    bindings = {str(row.id): row.instance_id for row in rows}
    if (len(bindings) != len(rows) or not rows
            or any(type(value) is not str or not INSTANCE_RE.fullmatch(value) for value in bindings.values())
            or len(set(bindings.values())) != len(rows)):
        return None
    # A duplicate imported/copied identity is ambiguous even if the other row
    # was not selected. Never authorize a subset of a corrupted identity set.
    count = db.query(database.Inbound.id).filter(
        database.Inbound.instance_id.in_(list(bindings.values()))).count()
    return bindings if count == len(rows) else None


def _bound_nodes(db, grant):
    bindings = grant.inbound_bindings
    if bindings is None:
        return [], "node_identity_missing"
    if (not _valid_ids(grant.inbound_ids) or type(bindings) is not dict
            or set(bindings) != {str(identifier) for identifier in grant.inbound_ids}
            or any(type(value) is not str or not INSTANCE_RE.fullmatch(value) for value in bindings.values())
            or len(set(bindings.values())) != len(bindings)):
        return [], "node_identity_changed"
    rows = db.query(database.Inbound).filter(database.Inbound.id.in_(grant.inbound_ids)).order_by(
        database.Inbound.id).populate_existing().all()
    if len(rows) != len(grant.inbound_ids) or _row_bindings(db, rows) != bindings:
        return [], "node_identity_changed"
    if any(not row.enable for row in rows):
        return [], "nodes_unavailable"
    return rows, None


def _management_binding_error(reason):
    if reason in {"node_identity_missing", "node_identity_changed"}:
        return HTTPException(409, "Subscription node identity is unverified or changed; select verified nodes and create a new subscription. Rotation cannot restore this authorization.")
    return HTTPException(409, "Selected subscription nodes are unavailable; enable the original nodes before rotating")


def _credential_matches(grant, user):
    stamp = grant.credential_stamp
    return (type(stamp) is str and bool(re.fullmatch(r"[0-9a-f]{64}", stamp))
            and hmac.compare_digest(stamp, digest(user.password_hash)))


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
    if not _valid_ids(inbound_ids):
        raise HTTPException(422, "Select distinct positive node IDs")
    ids = sorted(inbound_ids)
    if not _valid_formats(formats):
        raise HTTPException(422, "Select nodes and supported output formats")
    formats = sorted(formats)
    now = int(time.time())
    with database.SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        user = _owner(db, owner_id)
        rows = db.query(database.Inbound).filter(database.Inbound.id.in_(ids)).all()
        if len(rows) != len(ids) or any(not row.enable for row in rows):
            raise HTTPException(422, "Selected nodes are missing or disabled")
        bindings = _row_bindings(db, rows)
        if bindings is None:
            raise HTTPException(409, "Selected node identities are invalid; restore a verified full database or recreate the nodes")
        from app.services.validated_export import validated_nodes
        validated_nodes(rows, server)
        token = "vui_s_" + secrets.token_urlsafe(32)
        grant = SubscriptionGrant(owner_id=owner_id, token_hash=digest(token),
            credential_stamp=digest(user.password_hash), label=label, server=server,
            inbound_ids=ids, inbound_bindings=bindings, formats=formats, created_at=now,
            expires_at=now + days * 86400, revoked=False)
        db.add(grant)
        db.flush()
        result = public_record(grant)
        db.commit()
        return result, token


def list_grants(owner_id: int) -> list[dict]:
    with database.SessionLocal() as db:
        db.execute(text("BEGIN"))
        user = _owner(db, owner_id)
        result = []
        for grant in db.query(SubscriptionGrant).filter_by(owner_id=owner_id).order_by(SubscriptionGrant.id.desc()).all():
            _, reason = _bound_nodes(db, grant)
            if reason is None and not _valid_formats(grant.formats):
                reason = "invalid_configuration"
            if reason is None and not _credential_matches(grant, user):
                reason = "credentials_changed"
            result.append({**public_record(grant), "invalidated": reason is not None,
                           "invalidation_reason": reason})
        return result


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
            _, reason = _bound_nodes(db, grant)
            if reason:
                raise _management_binding_error(reason)
            if not _valid_formats(grant.formats):
                raise HTTPException(409, "Subscription formats are invalid; create a new subscription")
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
    if (grant is None or grant.revoked or grant.expires_at <= int(time.time())
            or not _valid_formats(grant.formats) or output_format not in grant.formats):
        raise denied
    user = db.get(database.User, grant.owner_id)
    if (not user or not user.is_active or not user.password_hash
            or not _credential_matches(grant, user)):
        raise denied
    rows, reason = _bound_nodes(db, grant)
    # Public errors reveal neither identities nor why the private grant failed.
    if reason:
        raise HTTPException(409, "Subscription configuration is not ready")
    return rows, grant.server
