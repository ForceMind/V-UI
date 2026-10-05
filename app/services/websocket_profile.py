"""The deliberately small, interoperable VLESS/WebSocket parameter surface.

Host is client routing metadata, never a sing-box server access control.
Keep path/Host validation shared by the visual compiler and strict exporter.
"""
import re


def websocket_path(value: object) -> str:
    if (not isinstance(value, str)
            or not re.fullmatch(r"/[A-Za-z0-9._~/-]{0,255}", value)
            or any(part in {".", ".."} for part in value.split("/"))):
        raise ValueError(
            "WebSocket path must be 1–256 ASCII characters beginning with /; "
            "use letters, digits, /, ., _, ~ or - without dot segments, "
            "query, fragment, percent escapes or early data"
        )
    return value


def websocket_host(value: object) -> str:
    if (not isinstance(value, str) or len(value) > 253
            or not value
            or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                   for label in value.split("."))):
        raise ValueError(
            "WebSocket Host must be an ASCII hostname without scheme, port, "
            "path or whitespace; it is not a server Host allowlist"
        )
    return value
