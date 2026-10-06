"""Literal HY2 editor values; advanced legacy drafts are never normalized away."""
from copy import deepcopy
import unicodedata

from app.services.subscription_tokens import normalize_server


TLS_FIELDS = {"enabled", "server_name", "certificate_path", "key_path", "alpn"}
META_FIELDS = {"security", "server_name", "client_fingerprint", "skip_cert_verify"}
PROFILE_FIELDS = {
    "security", "transport", "flow", "server_name", "certificate_path", "key_path",
    "path", "host", "service_name", "xhttp_mode", "reality_target", "reality_server_name",
    "reality_short_id", "client_fingerprint", "skip_cert_verify", "up_mbps", "down_mbps",
    "obfs_type", "obfs_password", "obfs_password_set", "congestion_control", "udp_relay_mode",
    "zero_rtt_handshake", "shadowsocks_method", "shadowsocks_password_set",
    "hysteria2_password", "hysteria2_password_set",
    "tuic_uuid", "tuic_uuid_set", "tuic_password", "tuic_password_set",
}


def password_value(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 256
            or not value.strip() or any(unicodedata.category(c) == "Cc" for c in value)):
        raise ValueError("Hysteria2 password must be 1–256 non-control characters and not only whitespace")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ValueError("Hysteria2 password must be valid UTF-8 text") from None
    return value


def stored_fields(settings, stream):
    if not isinstance(settings, dict) or not isinstance(stream, dict):
        raise ValueError("Existing Hysteria2 fields must be objects")
    tls, meta, obfs = stream.get("tls", {}), stream.get("_vui", {}), settings.get("obfs", {})
    if not isinstance(tls, dict) or not isinstance(meta, dict) or not isinstance(obfs, dict):
        raise ValueError("Existing Hysteria2 TLS/metadata/obfs cannot be represented by this editor")
    if set(tls) - TLS_FIELDS or set(meta) - META_FIELDS or set(obfs) - {"type", "password"}:
        raise ValueError("Existing Hysteria2 options cannot be represented by this editor; no fields were dropped")
    for name in ("up_mbps", "down_mbps"):
        if name in settings and (type(settings[name]) is not int or settings[name] <= 0):
            raise ValueError("Existing Hysteria2 bandwidth cannot be normalized by this editor")
    if "obfs" in settings and (obfs.get("type") not in ("salamander", "gecko")
            or not isinstance(obfs.get("password"), str) or not obfs["password"]):
        raise ValueError("Existing Hysteria2 obfs cannot be normalized by this editor")
    if "enabled" in tls and type(tls["enabled"]) is not bool:
        raise ValueError("Existing Hysteria2 TLS enabled must be a boolean")
    for mapping, fields in ((tls, ("server_name", "certificate_path", "key_path")),
                            (meta, ("server_name", "security", "client_fingerprint")),
                            (obfs, ("type", "password"))):
        for field in fields:
            if field in mapping and not isinstance(mapping[field], str):
                raise ValueError("Existing Hysteria2 " + field + " must be a string")
    if "skip_cert_verify" in meta and type(meta["skip_cert_verify"]) is not bool:
        raise ValueError("Existing Hysteria2 skip_cert_verify must be a boolean")
    if "alpn" in tls and (not isinstance(tls["alpn"], list) or not tls["alpn"]
            or any(not isinstance(v, str) or not v for v in tls["alpn"])):
        raise ValueError("Existing Hysteria2 ALPN cannot be represented by this editor")
    if tls.get("enabled") is True:
        if meta.get("security", "tls") != "tls" or (meta.get("server_name") and meta["server_name"] != tls.get("server_name")):
            raise ValueError("Existing Hysteria2 client/server TLS fields conflict")
    return tls, meta, obfs


def prepare_profile(profile, settings, stream):
    tls, meta, obfs = stored_fields(settings, stream)
    if set(profile) - PROFILE_FIELDS:
        raise ValueError("Unsupported Hysteria2 visual profile fields; no fields were dropped")
    # The shared form carries harmless defaults for other protocols. A caller
    # supplying meaningful foreign options must not see them silently ignored.
    foreign_defaults = {"flow": "", "path": "/", "host": "", "service_name": "",
        "xhttp_mode": "auto", "reality_target": "", "reality_server_name": "",
        "reality_short_id": "", "congestion_control": "bbr", "udp_relay_mode": "native",
        "zero_rtt_handshake": False, "tuic_uuid": "", "tuic_uuid_set": False,
        "tuic_password": "", "tuic_password_set": False, "shadowsocks_method": "aes-128-gcm", "shadowsocks_password_set": False}
    for name, default in foreign_defaults.items():
        if name in profile and (type(profile[name]) is not type(default) or profile[name] != default):
            raise ValueError("Hysteria2 does not accept the foreign option " + name)
    if profile.get("obfs_password", "") and "obfs_type" not in profile:
        profile["obfs_type"] = obfs.get("type", "")
        if not profile["obfs_type"]:
            raise ValueError("Choose the Hysteria2 obfs type before supplying an obfs password")
    for name in ("server_name", "certificate_path", "key_path"):
        profile.setdefault(name, deepcopy(meta.get(name, tls.get(name, ""))))
    profile.setdefault("client_fingerprint", deepcopy(meta.get("client_fingerprint", "")))
    profile.setdefault("skip_cert_verify", deepcopy(meta.get("skip_cert_verify", False)))
    if profile.get("transport", "quic") != "quic":
        raise ValueError("Hysteria2 uses its native QUIC transport")
    if "security" in profile:
        if not isinstance(profile["security"], str) or profile["security"].lower() != "tls":
            raise ValueError("Hysteria2 requires an explicit TLS security string")
    elif "tls" in stream and tls.get("enabled") is not True:
        raise ValueError("Explicit TLS selection is required to change an existing Hysteria2 security state")
    for name in ("server_name", "certificate_path", "key_path", "client_fingerprint"):
        if not isinstance(profile[name], str) or profile[name] != profile[name].strip():
            raise ValueError("Hysteria2 " + name + " must be a literal string without surrounding whitespace")
    try:
        if normalize_server(profile["server_name"]) != profile["server_name"]:
            raise ValueError()
    except ValueError:
        raise ValueError("Hysteria2 requires an explicit valid TLS server name") from None
    if type(profile["skip_cert_verify"]) is not bool:
        raise ValueError("Hysteria2 skip_cert_verify must be a boolean")
    if "hysteria2_password" in profile and profile["hysteria2_password"] != "":
        password_value(profile["hysteria2_password"])
    for name in ("up_mbps", "down_mbps"):
        value = profile.get(name, settings.get(name))
        if value not in (None, "") and (type(value) is not int or value <= 0):
            raise ValueError("Hysteria2 bandwidth must be a positive integer or empty")
    if "obfs_type" in profile and (not isinstance(profile["obfs_type"], str)
            or profile["obfs_type"] not in ("", "salamander", "gecko")):
        raise ValueError("Hysteria2 obfs type cannot be represented by this editor")
    if "obfs_password" in profile and not isinstance(profile["obfs_password"], str):
        raise ValueError("Hysteria2 obfs password must be a string")
    return deepcopy(tls.get("alpn"))


def editor_profile(settings, stream):
    tls, meta, obfs = stored_fields(settings, stream)
    users = settings.get("users")
    user = users[0] if isinstance(users, list) and users and isinstance(users[0], dict) else {}
    # Preserve a malformed/unverified transport as-is until an explicit raw
    # repair; the compiler never removes it and strict public export rejects it.
    return {
        "security": "tls" if tls.get("enabled") is True else "none", "transport": "quic",
        "server_name": deepcopy(meta.get("server_name", tls.get("server_name", ""))),
        "certificate_path": deepcopy(tls.get("certificate_path", "")),
        "key_path": deepcopy(tls.get("key_path", "")),
        "client_fingerprint": deepcopy(meta.get("client_fingerprint", "")),
        "skip_cert_verify": deepcopy(meta.get("skip_cert_verify", False)),
        "up_mbps": deepcopy(settings.get("up_mbps")), "down_mbps": deepcopy(settings.get("down_mbps")),
        "obfs_type": deepcopy(obfs.get("type", "")), "obfs_password": "",
        "obfs_password_set": bool(obfs.get("password")),
        "hysteria2_password": "", "hysteria2_password_set": bool(user.get("password")),
    }
