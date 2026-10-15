"""Strict, immutable M1 overseas-outbound model. Never contains source documents.

``to_dict`` is privileged: it contains credentials. Ordinary responses must use
``redacted``. Validation does not resolve names, probe hosts, or grant relay
eligibility; network/address policy belongs to the later application stage.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import ipaddress
import re
import unicodedata
import uuid as uuid_module

SCHEMA_VERSION = 1
MAX_STRING_BYTES = 8192
MAX_NAME_LENGTH = 128
FLOW = "xtls-rprx-vision"
PROFILE_FIELDS = frozenset({"schema_version", "protocol", "name", "server", "server_port",
                            "uuid", "transport", "tls", "flow", "encryption"})
TLS_FIELDS = frozenset({"mode", "server_name", "alpn", "fingerprint", "public_key", "short_id"})


class UpstreamValidationError(ValueError):
    """A stable, non-secret error suitable for API translation."""
    def __init__(self, code: str, message: str = "Upstream profile is not supported"):
        self.code = code
        super().__init__(message)


def literal_string(value, *, allow_empty=True, max_bytes=MAX_STRING_BYTES):
    """Reject coercion, surrogates, controls, bidi/zero-width and line injection."""
    if type(value) is not str or (not value and not allow_empty):
        raise UpstreamValidationError("INVALID_STRING", "A literal string is required")
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in value):
        raise UpstreamValidationError("INVALID_STRING", "Control or invisible characters are not allowed")
    if len(value.encode("utf-8")) > max_bytes:
        raise UpstreamValidationError("STRING_LIMIT", "A string exceeds the import size limit")
    return value


def strict_mapping(value, allowed, required=()):
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise UpstreamValidationError("INVALID_SHAPE", "A string-keyed object is required")
    if value.keys() - allowed:
        raise UpstreamValidationError("UNKNOWN_FIELD", "Unsupported fields cannot be discarded")
    if set(required) - value.keys():
        raise UpstreamValidationError("MISSING_FIELD", "A required upstream field is missing")
    return value


def dns_name(value):
    value = literal_string(value, allow_empty=False)
    if (len(value) > 253 or re.fullmatch(r"[0-9.]+", value)
            or not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                       for label in value.split("."))):
        raise UpstreamValidationError("INVALID_SERVER_NAME", "An explicit literal DNS server name is required")
    return value


def server_host(value):
    value = literal_string(value, allow_empty=False)
    if "%" in value or value != value.strip():
        raise UpstreamValidationError("INVALID_SERVER", "A literal host without a zone or expansion is required")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        try:
            dns_name(value)
        except UpstreamValidationError:
            raise UpstreamValidationError("INVALID_SERVER", "A literal hostname or IP address is required") from None
    return value


def server_port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise UpstreamValidationError("INVALID_PORT", "The upstream port must be an integer from 1 to 65535")
    return value


def canonical_uuid(value):
    value = literal_string(value, allow_empty=False)
    try:
        if str(uuid_module.UUID(value)) != value:
            raise ValueError()
    except ValueError:
        raise UpstreamValidationError("INVALID_UUID", "One canonical UUID is required") from None
    return value


def public_key(value):
    value = literal_string(value, allow_empty=False)
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise UpstreamValidationError("INVALID_PUBLIC_KEY", "REALITY requires a canonical 32-byte base64url public key")
    raw = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != value:
        raise UpstreamValidationError("INVALID_PUBLIC_KEY", "REALITY requires a canonical 32-byte base64url public key")
    return value


@dataclass(frozen=True)
class TLSProfile:
    mode: str
    server_name: str
    alpn: tuple[str, ...] = ()
    fingerprint: str = ""
    public_key: str = field(default="", repr=False)
    short_id: str = field(default="", repr=False)

    def __post_init__(self):
        if type(self.mode) is not str or self.mode not in {"tls", "reality"}:
            raise UpstreamValidationError("UNSUPPORTED_SECURITY", "Only verified TLS or REALITY is supported")
        dns_name(self.server_name)
        if (type(self.alpn) is not tuple or len(self.alpn) > 2
                or any(type(item) is not str or item not in {"h2", "http/1.1"} for item in self.alpn)
                or len(set(self.alpn)) != len(self.alpn)):
            raise UpstreamValidationError("UNSUPPORTED_ALPN", "ALPN must contain only unique h2 or http/1.1 values")
        if type(self.fingerprint) is not str or self.fingerprint not in {"", "chrome"}:
            raise UpstreamValidationError("UNSUPPORTED_FINGERPRINT", "Only the optional Chrome fingerprint is supported")
        literal_string(self.public_key)
        literal_string(self.short_id)
        if self.mode == "reality":
            if self.alpn:
                raise UpstreamValidationError("UNSUPPORTED_ALPN", "REALITY does not allow an ALPN override")
            if self.fingerprint != "chrome":
                raise UpstreamValidationError("UNSUPPORTED_FINGERPRINT", "REALITY requires the Chrome fingerprint")
            public_key(self.public_key)
            if not re.fullmatch(r"[0-9a-f]{16}", self.short_id):
                raise UpstreamValidationError("INVALID_SHORT_ID", "REALITY requires exactly 16 lowercase hexadecimal characters")
        elif self.public_key != "" or self.short_id != "":
            raise UpstreamValidationError("CONFLICTING_SECURITY", "REALITY parameters are not valid for TLS")

    @classmethod
    def from_dict(cls, value):
        strict_mapping(value, TLS_FIELDS, {"mode", "server_name"})
        alpn = value.get("alpn", [])
        if type(alpn) is not list:
            raise UpstreamValidationError("UNSUPPORTED_ALPN", "ALPN must be a list")
        return cls(mode=value["mode"], server_name=value["server_name"], alpn=tuple(alpn),
                   fingerprint=value.get("fingerprint", ""), public_key=value.get("public_key", ""),
                   short_id=value.get("short_id", ""))

    def to_dict(self):
        return {"mode": self.mode, "server_name": self.server_name, "alpn": list(self.alpn),
                "fingerprint": self.fingerprint, "public_key": self.public_key, "short_id": self.short_id}


@dataclass(frozen=True)
class UpstreamProfile:
    name: str
    server: str
    server_port: int
    uuid: str = field(repr=False)
    tls: TLSProfile
    schema_version: int = SCHEMA_VERSION
    protocol: str = "vless"
    transport: str = "tcp"
    flow: str = ""
    encryption: str = "none"

    def __post_init__(self):
        literal_string(self.name, allow_empty=False)
        if len(self.name) > MAX_NAME_LENGTH:
            raise UpstreamValidationError("NAME_LIMIT", "Upstream names must be at most 128 characters")
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise UpstreamValidationError("UNSUPPORTED_SCHEMA", "Unsupported upstream schema version")
        if self.protocol != "vless" or type(self.protocol) is not str:
            raise UpstreamValidationError("UNSUPPORTED_PROTOCOL", "Only VLESS upstreams are supported")
        if self.transport != "tcp" or type(self.transport) is not str:
            raise UpstreamValidationError("UNSUPPORTED_TRANSPORT", "Only direct TCP transport is supported")
        server_host(self.server)
        server_port(self.server_port)
        canonical_uuid(self.uuid)
        if type(self.tls) is not TLSProfile:
            raise UpstreamValidationError("INVALID_SHAPE", "A validated TLS profile is required")
        if self.encryption != "none" or type(self.encryption) is not str:
            raise UpstreamValidationError("UNSUPPORTED_ENCRYPTION", "Only encryption=none is supported")
        expected = FLOW if self.tls.mode == "reality" else ""
        if type(self.flow) is not str or self.flow != expected:
            raise UpstreamValidationError("UNSUPPORTED_FLOW", "Flow must match the selected TLS or REALITY contract")

    @classmethod
    def from_dict(cls, value):
        strict_mapping(value, PROFILE_FIELDS, {"name", "server", "server_port", "uuid", "tls"})
        return cls(name=value["name"], server=value["server"], server_port=value["server_port"],
                   uuid=value["uuid"], tls=TLSProfile.from_dict(value["tls"]),
                   schema_version=value.get("schema_version", SCHEMA_VERSION),
                   protocol=value.get("protocol", "vless"), transport=value.get("transport", "tcp"),
                   flow=value.get("flow", ""), encryption=value.get("encryption", "none"))

    def to_dict(self):
        """Privileged internal serialization, including the credential."""
        return {"schema_version": self.schema_version, "protocol": self.protocol, "name": self.name,
                "server": self.server, "server_port": self.server_port, "uuid": self.uuid,
                "transport": self.transport, "tls": self.tls.to_dict(), "flow": self.flow,
                "encryption": self.encryption}

    def redacted(self):
        """Allowlisted ordinary response; no UUID, key, short ID, or raw input."""
        return {"schema_version": self.schema_version, "protocol": self.protocol, "name": self.name,
                "server": self.server, "server_port": self.server_port, "transport": self.transport,
                "tls": {"mode": self.tls.mode, "server_name": self.tls.server_name,
                        "alpn": list(self.tls.alpn), "fingerprint": self.tls.fingerprint,
                        "public_key_set": bool(self.tls.public_key), "short_id_set": bool(self.tls.short_id)},
                "flow": self.flow, "encryption": self.encryption, "uuid_set": True}
