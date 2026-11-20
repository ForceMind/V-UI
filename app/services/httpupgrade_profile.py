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


def require_editable(stream):
    transport = stream.get("transport")
    if not is_httpupgrade(transport):
        return
    tls = stream.get("tls", {})
    meta = stream.get("_vui", {})
    if (set(transport) != TRANSPORT_FIELDS
            or not isinstance(tls, dict) or set(tls) - TLS_FIELDS
            or not isinstance(meta, dict) or set(meta) - CLIENT_FIELDS
            or ("alpn" in tls and tls["alpn"] != ["http/1.1"])):
        raise ValueError("Existing HTTPUpgrade fields cannot be represented by this editor; no fields were dropped")
    if (type(transport["path"]) is not str or not transport["path"]
            or type(transport["host"]) is not str
            or ("enabled" in tls and type(tls["enabled"]) is not bool)
            or any(type(tls[key]) is not str for key in (TLS_FIELDS & tls.keys()) - {"enabled", "alpn"})
            or any(type(meta[key]) is not str for key in (CLIENT_FIELDS & meta.keys()) - {"skip_cert_verify"})
            or ("skip_cert_verify" in meta and type(meta["skip_cert_verify"]) is not bool)
            or ("server_name" in meta and meta["server_name"] != tls.get("server_name"))):
        raise ValueError("Existing HTTPUpgrade values cannot be represented by this editor; no fields were dropped")
