"""Literal parameters for the bounded VLESS HTTPUpgrade profile."""
from app.services.websocket_profile import websocket_host, websocket_path


TRANSPORT_FIELDS = {"type", "host", "path"}
TLS_FIELDS = {"enabled", "server_name", "certificate_path", "key_path", "alpn"}
CLIENT_FIELDS = {"security", "server_name", "client_fingerprint", "skip_cert_verify"}


def is_httpupgrade(value):
    return isinstance(value, dict) and value.get("type") == "httpupgrade"


def literal_host(value):
    try:
        return websocket_host(value)
    except ValueError:
        raise ValueError("HTTPUpgrade requires an explicit ASCII hostname without a port") from None


def literal_path(value):
    try:
        return websocket_path(value)
    except ValueError:
        raise ValueError("HTTPUpgrade requires a literal path beginning with / without escapes or dot segments") from None


def require_editable(stream, settings, *, entering=False):
    transport = stream.get("transport")
    existing = is_httpupgrade(transport)
    if not existing and not entering:
        return
    tls = stream.get("tls", {})
    meta = stream.get("_vui", {})
    if (set(stream) - {"transport", "tls", "_vui"}
            or not isinstance(tls, dict) or set(tls) - TLS_FIELDS
            or not isinstance(meta, dict) or set(meta) - CLIENT_FIELDS
            or ("alpn" in tls and tls["alpn"] != ["http/1.1"])):
        raise ValueError("Existing HTTPUpgrade fields cannot be represented by this editor; no fields were dropped")
    if existing:
        transport_editable = (set(transport) == TRANSPORT_FIELDS
                              and type(transport["path"]) is str and bool(transport["path"])
                              and type(transport["host"]) is str)
    else:
        transport_editable = transport in (None, {}) or (
            isinstance(transport, dict) and any((
                transport.get("type") == "direct" and set(transport) == {"type"},
                transport.get("type") == "grpc" and not set(transport) - {"type", "service_name"},
                transport.get("type") == "ws" and not set(transport) - {"type", "path", "headers"}
                and isinstance(transport.get("headers", {}), dict)
                and not set(transport.get("headers", {})) - {"Host"},
            )))
    if not transport_editable:
        raise ValueError("Existing transport fields cannot be represented by this editor; no fields were dropped")
    users = settings.get("users") if isinstance(settings, dict) else None
    if (not isinstance(users, list) or len(users) != 1 or not isinstance(users[0], dict)
            or set(settings) - {"users"} or set(users[0]) - {"uuid", "name", "flow"}
            or type(users[0].get("flow", "")) is not str):
        raise ValueError("Existing HTTPUpgrade user options cannot be represented by this editor; no fields were dropped")
    if (("enabled" in tls and type(tls["enabled"]) is not bool)
            or any(type(tls[key]) is not str or tls[key] != tls[key].strip()
                   for key in (TLS_FIELDS & tls.keys()) - {"enabled", "alpn"})
            or any(type(meta[key]) is not str for key in (CLIENT_FIELDS & meta.keys()) - {"skip_cert_verify"})
            or ("skip_cert_verify" in meta and type(meta["skip_cert_verify"]) is not bool)
            or ("security" in meta and meta["security"] != ("tls" if tls.get("enabled") is True else "none"))
            or (tls.get("enabled") is True and meta.get("client_fingerprint") == "")
            or ("server_name" in meta and meta["server_name"] != tls.get("server_name"))):
        raise ValueError("Existing HTTPUpgrade values cannot be represented by this editor; no fields were dropped")
