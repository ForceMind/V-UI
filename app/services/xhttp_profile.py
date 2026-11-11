"""Literal parameters of the characterized Xray/Mihomo XHTTP profile."""
import re

from app.services.websocket_profile import websocket_host


STREAM_FIELDS = {"method", "network", "security", "tlsSettings", "_vui", "xhttpSettings"}
TRANSPORT_FIELDS = {"path", "host", "mode"}
TLS_FIELDS = {"serverName", "certificates", "alpn"}
CERTIFICATE_FIELDS = {"certificateFile", "keyFile"}
CLIENT_FIELDS = {"security", "server_name", "client_fingerprint", "skip_cert_verify"}


def transport_options(path, host, mode):
    if (type(path) is not str or not re.fullmatch(r"/[A-Za-z0-9._~/-]{0,255}", path)
            or not path.endswith("/") or "//" in path
            or any(part in {".", ".."} for part in path.split("/"))):
        raise ValueError("XHTTP requires a literal ASCII path beginning and ending with /")
    try:
        websocket_host(host)
    except ValueError:
        raise ValueError("XHTTP Host must be a literal ASCII hostname without a port") from None
    if mode != "stream-one":
        raise ValueError("The verified XHTTP profile requires stream-one")
    return {"path": path, "host": host, "mode": mode}


def server_name(value):
    try:
        websocket_host(value)
    except ValueError:
        raise ValueError("XHTTP requires a literal DNS TLS server name") from None
    if re.fullmatch(r"[0-9.]+", value):
        raise ValueError("XHTTP requires a literal DNS TLS server name")
    return value


def require_editable(stream):
    """Guard an explicit h2 edit against dropping unrepresented saved fields."""
    if stream.get("method", "xhttp") != "xhttp" or stream.get("network", "xhttp") != "xhttp":
        raise ValueError("Conflicting saved XHTTP transport fields")
    transport = stream.get("xhttpSettings", {})
    tls = stream.get("tlsSettings", {})
    meta = stream.get("_vui", {})
    values = ((stream, STREAM_FIELDS), (transport, TRANSPORT_FIELDS),
              (tls, TLS_FIELDS), (meta, CLIENT_FIELDS))
    if any(type(value) is not dict or set(value) - allowed for value, allowed in values):
        raise ValueError("Existing XHTTP options cannot be represented by the h2 editor")
    certificates = tls.get("certificates", [])
    if (type(certificates) is not list or len(certificates) > 1
            or any(type(cert) is not dict or set(cert) - CERTIFICATE_FIELDS for cert in certificates)):
        raise ValueError("Existing XHTTP certificate options cannot be represented by the h2 editor")
